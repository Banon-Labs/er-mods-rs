#!/usr/bin/env python3
"""Wait for a run to reach a world, then bring up frida-server and attach an agent.

    python3 scripts/er-frida-when-world.py --agent scripts/frida/<agent>.js

Why this exists. `scripts/er-frida-up.py` refuses to start while the game is still booting --
correctly, because attaching during boot has killed the process -- so every probe of a fresh launch
needs someone to notice the world has appeared and then run three commands. On 2026-09-19 that
someone was the player: the agent asked them to say when they had loaded in, which is a round trip
carrying no information and is exactly what the standing order against instructing the user in their
own game exists to prevent. The world's arrival is observable; asking about it is not necessary.

What it waits on is the same pair of witnesses `er-frida-up.py` gates on, in the same order.
`oracle_player_present` in the run's `er-quickload-telemetry.json` answers first when that file is
there; its absence during boot is not an error and not a failure -- it is the ordinary case for the
first minute of a launch.

A profile that does not carry `er-quickload` never writes that file at all,
though, and reading only it made this wait unpassable for every standalone shell however long the
game had been in a world: the watch ran out its `--wait-seconds` and reported "no world yet", which
sent the agent back to asking the player -- the round trip the paragraph above says this exists to
prevent. So the second witness is a direct read of `WorldChrMan -> mainPlayerIns` through
`/proc/<pid>/mem` (`er_run_lib.player_in_a_world`), which needs no server, no hook and no file, and
which any profile can produce.

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
    """Whether a player is in a world yet, from the run's telemetry or from the process itself.

    Two witnesses, in the order `er-frida-up.py` uses. `oracle_player_present` is the product's own
    read of `WorldChrMan`, written by `er_quickload` into this run's artifact directory: cheaper
    than a memory read and more specific, because it carries the loading substep alongside the
    verdict. A profile that writes no telemetry leaves it with nothing to say, and the live read is
    then the witness -- the same predicate, taken from outside through `/proc/<pid>/mem`.

    Returns the reason as well as the verdict, and the reason names which witness answered: "the
    file is not written yet", "the file says False" and "the read could not be made" look identical
    to a caller that only gets a bool, and they mean different things about how long to keep
    waiting.
    """
    # No game is the clearest not-ready there is, and it comes before the file: a previous run's
    # telemetry goes on saying a player was present long after that process died, so reading it
    # first would open the gate with nothing running at all.
    pid = er_run_lib.game_pid()
    if pid is None:
        return False, "no eldenring.exe is running"
    telemetry = run / "er-quickload-telemetry.json"
    if telemetry.is_file():
        try:
            data = json.loads(telemetry.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # Written from the game thread, so a read can land mid-write. Not an error; ask again.
            return False, (
                f"{run.name} telemetry unreadable this instant (mid-write); will ask again"
            )
        present = data.get("oracle_player_present")
        if present is True:
            return True, f"{run.name} telemetry: a player is in a world"
        step = data.get("oracle_system_step_label", "unknown")
        return False, (
            f"{run.name} telemetry: oracle_player_present is {present!r}, step={step!r}"
        )
    present, detail = er_run_lib.player_in_a_world(pid)
    if present:
        return True, f"no run telemetry; a live read of pid {pid} says {detail}"
    if present is False:
        return False, f"no run telemetry; a live read of pid {pid} says {detail}"
    # `None`, not `False`: the walk could not be made at all -- an unmapped address, a refused read,
    # or a qword that is not a pointer. Folding that into "no world" is how a broken address
    # constant would read as an empty world forever instead of as a wrong address.
    return False, f"no run telemetry, and a live read of pid {pid} could not tell: {detail}"


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

    # inotify on the run directory, not a timer. For the telemetry witness the thing that could
    # change the answer is the DLL writing that file, and a write is an event -- waiting on it means
    # this wakes when the world actually appears rather than up to a poll interval later, and sits
    # idle otherwise. The live-read witness has no such event, which is what `WATCH_SLICE_SECONDS`
    # is for: it bounds how long one wait may sit, so a profile that writes nothing at all is still
    # re-read on that slice rather than never. `DirectoryWatch` degrades honestly too -- with
    # inotify unavailable `wait()` returns at once, so the loop below still makes progress on its
    # own deadline instead of blocking forever.
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


def world_gate_selftest() -> list[tuple[str, bool]]:
    """Prove the two witnesses compose in the order the wait documents.

    The walk itself is exercised against a planted chain by `er_run_lib.world_read_selftest`; what
    is left is which witness `world_is_up` believes, and that every verdict the walk can return
    survives the trip. Both witnesses are stubbed -- a planted run directory for the first, a
    replaced `er_run_lib.player_in_a_world` for the second -- so every combination is reachable
    without a game and without writing into the user's own run history.
    """
    import tempfile

    saved_pid = er_run_lib.game_pid
    saved_walk = er_run_lib.player_in_a_world
    results: list[tuple[str, bool]] = []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            run = pathlib.Path(tmp) / "br-selftest"
            run.mkdir()
            er_run_lib.game_pid = lambda: 1234

            er_run_lib.player_in_a_world = lambda pid: (True, "stub: a player")
            ready, detail = world_is_up(run)
            results.append(
                (
                    f"a profile with no telemetry is answered by the live read -- {detail}",
                    ready and "live read" in detail,
                )
            )

            er_run_lib.player_in_a_world = lambda pid: (False, "stub: no player")
            ready, detail = world_is_up(run)
            results.append((f"the live read can refuse as well as pass -- {detail}", not ready))

            er_run_lib.player_in_a_world = lambda pid: (None, "stub: unreadable")
            ready, detail = world_is_up(run)
            results.append(
                (
                    f"a walk that could not be made says so, not `no world` -- {detail}",
                    not ready and "could not tell" in detail,
                )
            )

            telemetry = run / "er-quickload-telemetry.json"
            telemetry.write_text('{"oracle_player_present": true}', encoding="utf-8")
            ready, detail = world_is_up(run)
            results.append(
                (
                    f"telemetry answers before the live read -- {detail}",
                    ready and "telemetry" in detail,
                )
            )

            telemetry.write_text(
                '{"oracle_player_present": false, "oracle_system_step_label": "BOOT"}',
                encoding="utf-8",
            )
            ready, detail = world_is_up(run)
            results.append(
                (
                    f"telemetry that says no keeps the gate shut and names the step -- {detail}",
                    not ready and "step=" in detail,
                )
            )

            telemetry.write_text("{ torn", encoding="utf-8")
            ready, detail = world_is_up(run)
            results.append(
                (
                    f"a mid-write telemetry read is retried, not fatal -- {detail}",
                    not ready and "mid-write" in detail,
                )
            )

            er_run_lib.game_pid = lambda: None
            telemetry.write_text('{"oracle_player_present": true}', encoding="utf-8")
            ready, detail = world_is_up(run)
            results.append(
                (
                    f"no game outranks a previous run's telemetry -- {detail}",
                    not ready and "no eldenring.exe" in detail,
                )
            )
    finally:
        er_run_lib.game_pid = saved_pid
        er_run_lib.player_in_a_world = saved_walk
    return results


def selftest() -> int:
    source = pathlib.Path(__file__).read_text(encoding="utf-8")
    # The walk this wait's second witness is lives in `er_run_lib`, so the structural checks on it
    # read that file. One that only ever read this one would go green while the thing it guards sat
    # unguarded in the other.
    shared = pathlib.Path(er_run_lib.__file__).read_text(encoding="utf-8")
    checks = [
        ("er-frida-up.py exists beside this", (REPO / "scripts" / "er-frida-up.py").is_file()),
        ("er-frida-watch.py exists beside this", (REPO / "scripts" / "er-frida-watch.py").is_file()),
        (
            "the world check reads the same oracle er-frida-up gates on",
            "oracle_player_present" in source,
        ),
        (
            "a mid-write read is retried rather than failing",
            "mid-write" in source,
        ),
        (
            "a profile that writes no telemetry still has a witness",
            "er_run_lib.player_in_a_world(pid)" in source,
        ),
        (
            "that witness is the same walk er-frida-up gates on, from one owner",
            # An assignment, assembled from pieces so the needle is not in its own haystack: this
            # file reads the constant through `er_run_lib`, and what must never appear is a second
            # definition of it here.
            ("WORLD_CHR_MAN" + "_GLOBAL_RVA = ") not in source
            and ("WORLD_CHR_MAN" + "_GLOBAL_RVA = 0x3D69FF8") in shared,
        ),
        (
            "the live read opens the game's memory read-only, and injects nothing",
            'open(f"/proc/{pid}/mem", "rb", 0)' in shared,
        ),
        (
            "the wait never passes on how long the process has been alive",
            # Both files, because the walk lives in one and the wait in the other. Assembled from
            # pieces so the assertion does not match its own source text. An age fallback passes a
            # process that has been up five minutes, which a crashed modal satisfies as readily as
            # a world does, and bd er-effects-rs-y53v rejected that shape outright.
            ("/proc/" + "uptime") not in source + shared
            and ("BOOT_" + "SETTLE_SECONDS") not in source + shared,
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

    print("\n  the WorldChrMan witness, address and read path:")
    hop, hop_detail = er_run_lib.recorded_1162_to_1170_hop()
    if hop is None:
        print(f"  ....  the 1.16.2 to 1.17.0 hop went unchecked -- {hop_detail}")
    else:
        print(f"  {'ok  ' if hop else 'FAIL'}  the map still carries the first hop -- {hop_detail}")
        failed += 0 if hop else 1
    for name, ok in er_run_lib.world_read_selftest():
        print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
        failed += 0 if ok else 1
    for name, ok in world_gate_selftest():
        print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
        failed += 0 if ok else 1

    pid = er_run_lib.game_pid()
    if pid is None:
        print(
            "  ....  no eldenring.exe is running, so the live path went unexercised: the walk "
            "above ran against a planted chain in a child process, which proves the arithmetic "
            "and the read but not that these offsets still name a live WorldChrMan"
        )
    else:
        verdict, detail = er_run_lib.player_in_a_world(pid)
        reading = {True: "a player is in a world", False: "no player", None: "cannot tell"}[verdict]
        print(f"  ....  live read of pid {pid}: {reading} -- {detail}")

    print("selftest: " + ("PASS" if failed == 0 else f"FAIL ({failed})"))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
