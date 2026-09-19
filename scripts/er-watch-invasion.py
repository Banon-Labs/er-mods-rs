#!/usr/bin/env python3
"""Stream what an invasion actually does: the Steam query, the session states, the lobby join.

Stays attached and prints every change as it happens, so the outcome of a redirect is observed
rather than inferred from the field the redirect itself wrote.

    python3 scripts/er-frida-up.py
    uv run --with frida python3 scripts/er-watch-invasion.py --session 0x469ac930 --seconds 240

Detaching is what preceded the one crash this repo has seen on this target, so the watcher holds the
session for the whole window and detaches once, at the end.
"""
from __future__ import annotations

import argparse
import pathlib
import queue
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
AGENT = REPO / "scripts" / "frida" / "invasion-observer.js"

# Hard cap on any single blocked wait, matching every other wait in this repo.
WAIT_SECONDS = 30.0

# The game runtime cap, read from the one file that holds it rather than repeated here.
CAP_FILE = REPO / ".auto" / "runtime_timeout_cap_seconds"


def cap_seconds() -> float:
    try:
        return float(CAP_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 300.0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", help="hex pointer to Seamless's session, if already known")
    parser.add_argument("--seconds", type=float, default=cap_seconds())
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        source = AGENT.read_text(encoding="utf-8")
        checks = [
            ("the agent exists", AGENT.is_file()),
            ("it samples on the game's own tick", "XInputGetState" in source),
            ("it carries the query oracle", "_RequestLobbyList" in source),
            ("it carries the join oracle", "_JoinLobby" in source),
            ("the window is capped by the canonical file", args.seconds <= cap_seconds()),
        ]
        bad = sum(0 if ok else 1 for _, ok in checks)
        for label, ok in checks:
            print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
        print("selftest: ok" if not bad else f"selftest: {bad} check(s) failed")
        return 0 if not bad else 1

    import frida

    device = frida.get_device_manager().add_remote_device("127.0.0.1:27042")
    procs = [p for p in device.enumerate_processes() if "eldenring" in p.name.lower()]
    if not procs:
        raise SystemExit("no eldenring.exe visible to the frida server")
    session = device.attach(procs[0].pid)
    script = session.create_script(AGENT.read_text(encoding="utf-8"))
    started = time.monotonic()

    events: queue.Queue = queue.Queue()
    script.on(
        "message",
        lambda message, _data: events.put(message["payload"])
        if message.get("type") == "send"
        else None,
    )
    script.load()
    if args.session:
        print("watch: " + str(script.exports_sync.watch(args.session)), flush=True)
    print(f"attached to pid {procs[0].pid}; watching {args.seconds:.0f}s", flush=True)

    # Block on the agent's own messages rather than polling a clock. Nothing here samples: the
    # agent sends only when a reading changes, so a quiet session costs one blocked thread and no
    # wakeups, and the window below is a safety cap rather than the synchronisation.
    window = min(args.seconds, cap_seconds())
    deadline = started + window
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            payload = events.get(timeout=min(remaining, WAIT_SECONDS))
        except queue.Empty:
            continue
        print(f"[{time.monotonic() - started:7.1f}s] {payload['kind']:6s} {payload['line']}",
              flush=True)

    report = script.exports_sync.report()
    print(f"queries={report['queries']} joins={report['joins']}", flush=True)
    print("states: " + " -> ".join(report["states"][-12:]), flush=True)
    print("lobby:  " + " -> ".join(report["lobby"][-12:]), flush=True)
    session.detach()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
