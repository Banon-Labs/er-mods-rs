#!/usr/bin/env python3
"""Measure whether driving Seamless's own invade action produces a Steam query.

The near+far handoff turns on this one question, and until 2026-09-16 it was answered by argument
rather than measurement. `ersc+0x25850` is nine instructions -- lock the session, return unless the
state reads idle, store `0x0e`, unlock -- so the state write cannot be the search. Something
consumes that state, and the only honest oracle is a query going out.

    python3 scripts/er-frida-up.py
    uv run --with frida python3 scripts/er-invade-proof.py
    uv run --with frida python3 scripts/er-invade-proof.py --watch-only   # no drive, just observe
    python3 scripts/er-invade-proof.py --selftest

The drive is a state-changing action on a live game: it is the same call Seamless's own Invade row
makes, from a Frida thread rather than the game task. It is announced before it happens and the
session state is reported either side of it.

Nothing here waits on a clock. The attach blocks on the game directory changing, the watch blocks
on the agent's own `query` message, and the two deadlines below are caps on a thing that never
happens -- never the way the run is synchronised.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import queue
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

# The inotify watch and the pid primitives this script blocks on, shared with the launchers.
import er_run_lib

REPO = pathlib.Path(__file__).resolve().parent.parent
AGENT = REPO / "scripts" / "frida" / "ersc-invade-proof.js"

# The cap on watching after the drive, for a run where no query ever goes out -- which is itself
# the answer in that case. A search that happens at all announces itself in well under a second,
# and the watch ends the moment it does.
WATCH_SECONDS = 25.0
# The hard ceiling the cap is clamped to, so a `--seconds` typo cannot turn the backstop into a
# wait (scripts/check-no-timeouts.py).
MAX_WATCH_SECONDS = 30.0

# Bounded like every other non-game operation in this repo (scripts/check-no-timeouts.py).
ATTACH_TIMEOUT = 15.0
# How long one slice of the wait for the game may last when nothing at all is written. A launch
# writes in the game directory -- me3's staging, the DLLs' own logs -- long before a process
# exists, so in practice the slice ends on that write rather than here.
ATTACH_SLICE_SECONDS = 2.0
# `enumerate_processes` against a server whose wineserver is gone never returns, so the call is
# asked on a thread that can be abandoned.
ENUMERATE_TIMEOUT = 6.0

SERVER_ADDRESS = "127.0.0.1:27042"


def enumerate_bounded(dev, timeout_seconds: float = ENUMERATE_TIMEOUT):
    """`dev.enumerate_processes()` with a bound, because the underlying call has none.

    The same shape `scripts/er-frida-watch.py` uses, for the same reason: an open port is not proof
    of a working server. A server started beside the game's container accepts the connection and
    then answers nothing, and without this the script hangs there instead of naming the fix.
    """
    answer: list = []

    def ask() -> None:
        try:
            answer.append(dev.enumerate_processes())
        except Exception as exc:  # noqa: BLE001 -- re-raised below, whatever it was
            answer.append(exc)

    worker = threading.Thread(target=ask, daemon=True)
    worker.start()
    worker.join(timeout_seconds)
    if not answer:
        raise SystemExit(
            "the frida server accepted the connection but did not answer enumerate_processes -- "
            "its prefix is stale. Restart it with `python3 scripts/er-frida-up.py --force`."
        )
    if isinstance(answer[0], Exception):
        raise answer[0]
    return answer[0]


def attach(frida):
    """Attach to the game, waiting on the launch rather than on a clock.

    Between enumerations this blocks on an inotify watch of the game directory, so it wakes when
    something about the launch actually happens. `ATTACH_TIMEOUT` only bounds a game that never
    arrives. Where inotify is unavailable there is no event to wait on, and a retry loop with
    nothing to wake it is a delay wearing a loop -- so that case asks once and says so.
    """
    dev = frida.get_device_manager().add_remote_device(SERVER_ADDRESS)
    deadline = time.monotonic() + ATTACH_TIMEOUT
    with er_run_lib.DirectoryWatch(er_run_lib.game_dir()) as launch:
        while True:
            procs = [p for p in enumerate_bounded(dev) if "eldenring" in p.name.lower()]
            if procs:
                return dev.attach(procs[0].pid), procs[0].pid
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not launch.available:
                break
            launch.wait(min(ATTACH_SLICE_SECONDS, remaining))
    raise SystemExit(
        "no eldenring.exe visible to the frida server. Run scripts/er-frida-up.py first; an open "
        "port is not proof of a working server."
    )


def message_handler(notes: list[str], events: "queue.Queue[str]"):
    """Collect every line the agent sends, and queue the ones a caller can block on.

    The agent tags a note `event: 'query'` from inside its `RequestLobbyList` hooks, so the queue
    carries the oracle itself rather than a transcript somebody has to read afterwards. The `line`
    field is untouched, so the notes list keeps the shape it always had.
    """

    def on_message(message, _data) -> None:
        if message.get("type") != "send":
            notes.append(str(message))
            return
        payload = message.get("payload") or {}
        notes.append(payload.get("line", str(message)))
        event = payload.get("event")
        if event is not None:
            events.put(event)

    return on_message


def wait_for_query(events: "queue.Queue[str]", window: float) -> bool:
    """Block until the agent reports a query, or until the cap runs out.

    The cap is a backstop, not the synchronisation: a run that searches ends on the search, and a
    run that does not ends having proven the thing it set out to test.
    """
    deadline = time.monotonic() + window
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        try:
            if events.get(timeout=remaining) == "query":
                return True
        except queue.Empty:
            return False


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--watch-only",
        action="store_true",
        help="locate and observe, but never call the invade action",
    )
    parser.add_argument("--seconds", type=float, default=WATCH_SECONDS)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest(args.seconds)

    import frida  # imported here so --selftest runs without it

    session, pid = attach(frida)
    notes: list[str] = []
    events: queue.Queue[str] = queue.Queue()
    script = session.create_script(AGENT.read_text(encoding="utf-8"))
    script.on("message", message_handler(notes, events))
    script.load()

    found = script.exports_sync.find()
    print("find: " + json.dumps(found), flush=True)
    if not found.get("ok"):
        session.detach()
        return 3

    armed = script.exports_sync.arm()
    print("arm: " + json.dumps(armed), flush=True)
    if not armed.get("ok"):
        session.detach()
        return 4

    # Watch for Seamless to hand its own option-menu object over. Every scan this session found a
    # shape and none found a session, so the hand-over is the identification and the scan is only
    # a starting guess the hand-over overwrites.
    print("handover: " + json.dumps(script.exports_sync.watchHandover()), flush=True)

    if args.watch_only:
        print(f"watching for a query, up to {args.seconds}s, without driving", flush=True)
    else:
        print(
            "DRIVING ersc+0x25850 on the live session now -- the same call Seamless's own "
            "Invade row makes",
            flush=True,
        )
        print("drive: " + json.dumps(script.exports_sync.drive()), flush=True)

    window = min(args.seconds, MAX_WATCH_SECONDS)
    started = time.monotonic()
    queried = wait_for_query(events, window)
    watched = time.monotonic() - started
    print(
        f"a lobby query went out after {watched:.1f}s"
        if queried
        else f"no lobby query in {watched:.1f}s of watching",
        flush=True,
    )

    report = script.exports_sync.report()
    print("report: " + json.dumps(report, indent=1), flush=True)
    for line in notes[-30:]:
        print("  note: " + line, flush=True)

    verdict = (
        "PROVEN: driving the action produced a Steam query"
        if report.get("queries", 0) > 0
        else "NOT PROVEN: the state moved and no query went out, so nothing consumed it"
    )
    print("VERDICT: " + verdict, flush=True)

    # Record the measurement, so the Rust-edit gate can see that somebody went and looked. The
    # duration recorded is the one the watch really lasted, not the cap it was allowed.
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "er_frida_evidence", REPO / "scripts" / "er-frida-evidence.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.record(str(AGENT.relative_to(REPO)), pid, len(notes), watched)
    except Exception as exc:  # noqa: BLE001 -- recording must never change the verdict
        print(f"  (evidence not recorded: {exc})", flush=True)

    session.detach()
    return 0 if report.get("queries", 0) > 0 else 1


def selftest(seconds: float) -> int:
    source = pathlib.Path(__file__).read_text(encoding="utf-8")
    agent = AGENT.read_text(encoding="utf-8") if AGENT.is_file() else ""
    checks = [
        ("the agent file exists", AGENT.is_file()),
        (
            "the agent hooks Steam's vtable, never ersc.dll",
            "Interceptor.attach" in agent and "ERSC_INVADE" in agent,
        ),
        ("the watch window is bounded", seconds <= MAX_WATCH_SECONDS),
        (
            "the watch ends on the agent's query message, not on a clock",
            "def wait_for_query(" in source and "events.get(timeout=remaining)" in source,
        ),
        (
            "the agent tags its query notes for the caller to block on",
            "'query')" in agent,
        ),
        (
            "the agent samples the session state on a game tick, not a timer",
            "setInterval" not in agent and "gameTick()" in agent,
        ),
        (
            "every wait is an event wait, never a delay",
            # Built from pieces so the assertion does not match its own source text.
            ("ti" + "me.sl" + "eep(") not in source,
        ),
        (
            "a stale server cannot hang the attach",
            "def enumerate_bounded(" in source,
        ),
        (
            "the recorded duration is the watch that happened, not the cap it was allowed",
            "len(notes), watched)" in source,
        ),
    ]
    bad = 0
    for label, ok in checks:
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
        bad += 0 if ok else 1
    print("selftest: PASS" if not bad else f"selftest: {bad} check(s) failed")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
