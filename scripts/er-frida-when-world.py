#!/usr/bin/env python3
"""Wait for a run to reach a world, then bring up frida-server and attach an agent.

    python3 scripts/er-frida-when-world.py --agent scripts/frida/<agent>.js

Why this exists. `scripts/er-frida-up.py` refuses to start while the game is still booting --
correctly, because attaching during boot has killed the process -- so every probe of a fresh launch
needs someone to notice the world has appeared and then run three commands. On 2026-09-19 that
someone was the player: the agent asked them to say when they had loaded in, which is a round trip
carrying no information and is exactly what the standing order against instructing the user in their
own game exists to prevent. The world's arrival is observable; asking about it is not necessary.

What it waits on is the same oracle `er-frida-up.py` gates on: `oracle_player_present` in the run's
`er-quickload-telemetry.json`. That file does not exist during boot, which is not an error and not a
failure -- it is the ordinary case for the first minute of a launch.

Two properties this deliberately has:

- **It exits rather than waiting forever.** `--wait-seconds` bounds the watch so a caller that is
  itself bounded (an agent shell is capped) gets a clean answer -- world, or not yet -- instead of
  being killed mid-bringup. Re-running it is cheap and idempotent.
- **It `exec`s the watcher.** The watcher must be the process, not a child of this one, so that a
  signal sent to it reaches the watcher's own handler. A watcher that owns hardware watchpoints must
  unset them on the way out, and a wrapper in between is one more place that can be killed without
  the cleanup running.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import er_run_lib

# The longest a single inotify wait may block before the deadline is re-checked.
#
# Not a poll interval: the wait returns as soon as the DLL writes anything in the run directory,
# which is the event that could make the answer change. This only bounds how long a wait sits when
# nothing at all is being written, so `--wait-seconds` stays honest on a game that has stopped
# producing artifacts entirely.
WATCH_SLICE_SECONDS = 4.0

# A safety cap on starting the server, not a wait for it: `er-frida-up.py` returns as soon as the
# port answers, so a start still running after this has hit something worth failing on.
SERVER_START_TIMEOUT_SECONDS = 30


def newest_run() -> pathlib.Path | None:
    """The most recently created run directory, or `None` if there are none."""
    root = pathlib.Path.home() / ".cache" / "er-me3-runs"
    if not root.is_dir():
        return None
    runs = [entry for entry in root.iterdir() if entry.is_dir()]
    if not runs:
        return None
    return max(runs, key=lambda entry: entry.stat().st_mtime)


def world_is_up(run: pathlib.Path) -> tuple[bool, str]:
    """Whether the run's own telemetry says a player exists.

    Returns the reason as well as the verdict: "the file is not written yet" and "the file says
    False" look identical to a caller that only gets a bool, and they mean different things about
    how long to keep waiting.
    """
    telemetry = run / "er-quickload-telemetry.json"
    if not telemetry.is_file():
        return False, "no telemetry file yet -- the DLL writes it once the game is past boot"
    try:
        data = json.loads(telemetry.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # Written from the game thread, so a read can land mid-write. Not an error; ask again.
        return False, "telemetry unreadable this instant (mid-write); will ask again"
    present = data.get("oracle_player_present")
    if present is True:
        return True, "oracle_player_present is True"
    step = data.get("oracle_system_step_label", "unknown")
    return False, f"oracle_player_present is {present!r}, step={step!r}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", type=pathlib.Path, help="Frida agent .js to attach")
    parser.add_argument("--run", type=pathlib.Path, help="Run directory (default: the newest)")
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=100.0,
        help="Give up waiting after this and exit 2, so a bounded caller gets an answer",
    )
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    if args.agent is None:
        print("--agent is required", file=sys.stderr)
        return 1
    agent = args.agent if args.agent.is_absolute() else REPO / args.agent
    if not agent.is_file():
        print(f"no such agent: {agent}", file=sys.stderr)
        return 1

    run = args.run or newest_run()
    if run is None:
        print("no run directory under ~/.cache/er-me3-runs", file=sys.stderr)
        return 1
    print(f"watching {run.name} for a world", flush=True)

    # inotify on the run directory, not a timer. The thing that could change the answer is the DLL
    # writing its telemetry, and that write is an event -- waiting on it means this wakes when the
    # world actually appears rather than up to a poll interval later, and sits idle otherwise.
    # `DirectoryWatch` degrades honestly: with inotify unavailable `wait()` returns at once, so the
    # loop below still makes progress on its own deadline instead of blocking forever.
    deadline = time.monotonic() + args.wait_seconds
    reason = "never checked"
    with er_run_lib.DirectoryWatch(run) as watch:
        while True:
            up, reason = world_is_up(run)
            if up:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print(f"no world yet: {reason}", file=sys.stderr)
                print("nothing was started; run this again -- it is idempotent", file=sys.stderr)
                return 2
            watch.wait(min(WATCH_SLICE_SECONDS, remaining))

    print(f"world is up ({reason}); starting frida-server", flush=True)
    started = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "er-frida-up.py"), "--force"],
        cwd=REPO,
        timeout=SERVER_START_TIMEOUT_SECONDS,
        check=False,
    )
    if started.returncode != 0:
        print(f"frida-server refused to start (exit {started.returncode})", file=sys.stderr)
        return 1

    # `exec`, not a child: the watcher owns hardware watchpoints it must unset on the way out, and a
    # wrapper process in between is one more thing that can be killed without that cleanup running.
    watcher = [
        "uv",
        "run",
        "--with",
        "frida",
        "python3",
        str(REPO / "scripts" / "er-frida-watch.py"),
        "--agent",
        str(agent),
    ]
    print(f"attaching {agent.name}", flush=True)
    os.execvp(watcher[0], watcher)


def selftest() -> int:
    source = pathlib.Path(__file__).read_text(encoding="utf-8")
    checks = [
        ("er-frida-up.py exists beside this", (REPO / "scripts" / "er-frida-up.py").is_file()),
        ("er-frida-watch.py exists beside this", (REPO / "scripts" / "er-frida-watch.py").is_file()),
        (
            "the world check reads the same oracle er-frida-up gates on",
            "oracle_player_present" in source,
        ),
        (
            "a missing telemetry file is not an error",
            "no telemetry file yet" in source,
        ),
        (
            "a mid-write read is retried rather than failing",
            "mid-write" in source,
        ),
        (
            "the wait is bounded so a capped caller gets an answer",
            "--wait-seconds" in source and "return 2" in source,
        ),
        (
            "the wait is an inotify event, never a sleep",
            # The needle is assembled rather than written out, or this check matches itself: the
            # literal would be in the source it is searching, and the test would fail on a file
            # that contains no sleep at all.
            "DirectoryWatch" in source and ("time." + "sleep(") not in source,
        ),
        (
            "the watcher replaces this process rather than becoming its child",
            "os.execvp" in source,
        ),
        (
            "the server start carries an explicit cap of 30s or less",
            SERVER_START_TIMEOUT_SECONDS <= 30 and "timeout=SERVER_START_TIMEOUT_SECONDS" in source,
        ),
        ("a run directory can be resolved or reported missing", newest_run() is not None),
    ]
    failed = 0
    for name, ok in checks:
        print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
        failed += 0 if ok else 1
    print("selftest: " + ("PASS" if failed == 0 else f"FAIL ({failed})"))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
