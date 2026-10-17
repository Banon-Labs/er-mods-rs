#!/usr/bin/env python3
"""Send one command to a command-file Frida agent (geom-lab.js, grass-lab.js) and print its reply.

    er-frida-cmd.py <cmd.json> <agent log .jsonl> '<json command, no seq>' [timeout seconds]

The agents act once per new `seq`, so this keeps the last seq it used in `<cmd.json>.seq` (starting
at 30000, clear of the hand-written ones), writes the command with the next one, and waits for the
first message in the log after that write whose `kind` is the op's own or `error`. Exits 1 when no
reply arrives within the timeout (default 10 s, capped at 30 s), which means the agent is not
polling. The wait is an inotify watch on the log's directory, never a sleep.
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import er_run_lib  # noqa: E402

MAX_TIMEOUT_SECONDS = 30


def main():
    cmdfile, log, body = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
    timeout = min(float(sys.argv[4]) if len(sys.argv) > 4 else 10, MAX_TIMEOUT_SECONDS)
    seqfile = cmdfile + ".seq"
    try:
        seq = int(open(seqfile).read()) + 1
    except (OSError, ValueError):
        seq = 30000
    try:
        seq = max(seq, int(json.load(open(cmdfile)).get("seq", 0)) + 1)
    except (OSError, ValueError, AttributeError):
        pass
    with open(seqfile, "w") as f:
        f.write(str(seq))
    start = os.path.getsize(log)
    # Armed before the command is written, so a reply that lands at once still raises an event.
    watch = er_run_lib.DirectoryWatch(Path(log).resolve().parent)
    body["seq"] = seq
    with open(cmdfile, "w") as f:
        f.write(json.dumps(body))
    # A `place` inside geom-lab answers `place`, a batch answers per sub-command.
    kinds = {body["op"], "error"} | set(body.get("expect", []))
    end = time.monotonic() + timeout
    try:
        while True:
            with open(log) as f:
                f.seek(start)
                for line in f:
                    try:
                        p = json.loads(line)["message"].get("payload")
                    except (ValueError, KeyError, AttributeError):
                        continue
                    if isinstance(p, dict) and p.get("kind") in kinds:
                        print(json.dumps(p)[: int(os.environ.get("MAXC", "8000"))])
                        return 0
            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            # Woken by the watcher appending to the log. Without inotify, a pidfd wait on this
            # process blocks out the remainder on a real descriptor, as er-frida-watch.py does.
            if watch.available:
                watch.wait(remaining)
            else:
                er_run_lib.wait_for_exit(os.getpid(), remaining)
    finally:
        watch.close()
    print(f"no reply in {timeout}s (seq {seq})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
