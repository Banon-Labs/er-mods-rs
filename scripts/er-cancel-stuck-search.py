#!/usr/bin/env python3
"""Cancel a Seamless search that is stuck cycling, using the session the DLL already logs.

    python3 scripts/er-frida-up.py
    uv run --with frida python3 scripts/er-cancel-stuck-search.py

The session address is read out of the newest run's `er-invasion-warp.log` heartbeat line
(`ersc_session=0x...@0x...`) rather than typed, so it cannot be a stale literal from another
process. Pass `--session 0x...` to override.

Why this exists: `scripts/frida/drive-cancel-now.js` cancels from the invade action's `onLeave`,
and a search that is sitting at `0x12` never re-enters that action -- measured on run
`br-20260917-234208-1e25`, where the agent armed, read `state_now: 18`, and never fired across
repeated cycles. This drives `ersc+0x258d0` directly at a state ERSC itself offers Cancel for.
"""
from __future__ import annotations

import argparse
import pathlib
import queue
import re
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
AGENT = REPO / "scripts" / "frida" / "cancel-from-connecting.js"
EVIDENCE_SCRIPT = REPO / "scripts" / "er-frida-evidence.py"
RUNS = pathlib.Path.home() / ".cache" / "er-me3-runs"

# Every blocked wait in this repo is bounded; this one matches the rest.
WAIT_SECONDS = 30.0

SESSION_RE = re.compile(r"ersc_session=(0x[0-9a-f]+)@0x[0-9a-f]+")


def newest_session() -> tuple[str, pathlib.Path]:
    """The last session address the DLL logged, and the log it came from."""
    runs = sorted(RUNS.glob("br-*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for run in runs:
        log = run / "er-invasion-warp.log"
        if not log.exists():
            continue
        found = SESSION_RE.findall(log.read_text(encoding="utf-8", errors="replace"))
        if found:
            return found[-1], log
    raise SystemExit(
        "no `ersc_session=0x...` line in any run log under "
        f"{RUNS} -- the DLL writes one on every heartbeat, so an empty search means no run has "
        "resolved a Seamless session yet"
    )


def evidence_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("er_frida_evidence", EVIDENCE_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {EVIDENCE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", help="session address; default is the newest logged one")
    parser.add_argument("--port", type=int, default=27042)
    parser.add_argument(
        "--look",
        action="store_true",
        help="report the session state and exit without cancelling",
    )
    args = parser.parse_args()

    if args.session:
        session, source = args.session, "--session"
    else:
        session, log = newest_session()
        source = str(log)
    print(f"session {session} (from {source})")

    import frida

    device = frida.get_device_manager().add_remote_device(f"127.0.0.1:{args.port}")
    target = None
    for process in device.enumerate_processes():
        if process.name.lower() == "eldenring.exe":
            target = process.pid
            break
    if target is None:
        raise SystemExit("eldenring.exe not found through the wine-side frida server")

    started = time.monotonic()
    messages: queue.Queue = queue.Queue()
    count = 0

    session_handle = device.attach(target)
    script = session_handle.create_script(AGENT.read_text(encoding="utf-8"))

    def on_message(message, _data):
        nonlocal count
        count += 1
        messages.put(message)
        payload = message.get("payload") if message["type"] == "send" else message
        print(f"  {payload}")

    script.on("message", on_message)
    script.load()
    print(f"attached to pid {target}")

    if args.look:
        print(f"state = {script.exports_sync.look(session)}")
    else:
        result = script.exports_sync.cancel(session)
        print(f"result {result}")

    try:
        evidence_module().record(str(AGENT), int(target), count, time.monotonic() - started)
    except Exception as exc:  # noqa: BLE001 -- recording must never fail the cancel
        print(f"could not record frida evidence ({exc})", file=sys.stderr)

    script.unload()
    session_handle.detach()
    return 0


if __name__ == "__main__":
    sys.exit(main())
