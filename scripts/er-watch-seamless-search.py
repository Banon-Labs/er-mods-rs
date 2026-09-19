#!/usr/bin/env python3
"""Record the filter set Seamless sends when it searches, and the host's published lobby data.

    python3 scripts/er-frida-up.py
    uv run --with frida python3 scripts/er-watch-seamless-search.py --seconds 60 \
        --lobby 109775241801277563

Runs `scripts/frida/seamless-search-filters.js`. Every `RequestLobbyList` is printed with the
filters that were accumulated for it and the module that asked, so this mod's sweep and ersc's own
invasion search are told apart instead of averaged. `--lobby` additionally enumerates that lobby's
published keys, which needs no membership.

The comparison this exists to make: Seamless filters on `lobby_key` for equality, and `lobby_key`
fingerprints the loaded param tables. If the value ersc puts in its search differs from the value
the host published, her lobby is excluded from his search by construction -- which is exactly what
a search that fetches nothing across 46 cycles looks like.

Read-only: entry/exit observers and getters. Nothing is written and no lobby is joined.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import signal
import sys
import threading
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
AGENT = REPO / "scripts" / "frida" / "seamless-search-filters.js"
EVIDENCE_SCRIPT = REPO / "scripts" / "er-frida-evidence.py"


def evidence_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("er_frida_evidence", EVIDENCE_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {EVIDENCE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def describe(entry: dict) -> str:
    kind = entry.get("kind")
    if kind == "string":
        return f"{entry['key']} {entry.get('comparison')} {entry['value']}"
    if kind == "numerical":
        return f"{entry['key']} {entry.get('comparison')} {entry['value']} (num)"
    if kind == "near":
        return f"{entry['key']} near {entry['value']}"
    return f"{kind}={entry.get('value')}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=60.0, help="how long to watch")
    parser.add_argument("--lobby", help="also enumerate this lobby's published keys")
    parser.add_argument("--port", type=int, default=27042)
    parser.add_argument("--json", help="write every record here")
    args = parser.parse_args()

    import frida

    device = frida.get_device_manager().add_remote_device(f"127.0.0.1:{args.port}")
    target = None
    for process in device.enumerate_processes():
        if process.name.lower() == "eldenring.exe":
            target = process.pid
            break
    if target is None:
        raise SystemExit("eldenring.exe not found through the wine-side frida server")

    records: list[dict] = []
    started = time.monotonic()

    handle = device.attach(target)
    script = handle.create_script(AGENT.read_text(encoding="utf-8"))

    # The watch ends on an event, and `--seconds` is the cap on how long it may wait for one. Two
    # things end it early and both are real: the session detaching, which is the game going away
    # and every subsequent second being spent watching nothing, and `SIGTERM`, which is how a
    # backgrounded run is stopped -- its default action would kill this process where it stands,
    # past the totals and the evidence record, so the measurement would be taken and then thrown
    # away.
    done = threading.Event()
    ended = {"why": "the watch window elapsed"}

    def on_detached(reason, *_):
        ended["why"] = f"the session detached ({reason})"
        done.set()

    handle.on("detached", on_detached)

    def on_terminate(_signum, _frame) -> None:
        ended["why"] = "terminated"
        done.set()

    try:
        signal.signal(signal.SIGTERM, on_terminate)
    except (ValueError, OSError):
        # Only installable from the main thread. Driven from anywhere else this keeps the default
        # action and simply records nothing when it is terminated.
        pass

    def on_message(message, _data):
        if message["type"] != "send":
            print(f"  {json.dumps(message)[:400]}")
            return
        payload = message["payload"]
        records.append(payload)
        tag = payload.get("tag")
        if tag == "search":
            print(f"\nsearch #{payload['n']} by {payload['caller']} -> call {payload['call']}")
            for entry in payload.get("filters") or []:
                print(f"    {describe(entry)}")
            if not payload.get("filters"):
                print("    (no filters recorded for this request)")
        elif tag == "by-index":
            print(f"  by-index #{payload['n']} {payload['caller']} [{payload['index']}] -> {payload['lobby']}")
        elif tag == "lobby-data":
            print(f"  lobby-data {payload['caller']} {payload['key']} = {payload['value']}")
        elif tag == "join":
            print(f"  JOIN by {payload['caller']} -> lobby {payload['lobby']}")
        else:
            print(f"  {json.dumps(payload)[:300]}")

    script.on("message", on_message)
    script.load()
    print(f"attached to pid {target}")

    if args.lobby:
        answer = script.exports_sync.lobby_data(args.lobby)
        records.append({"tag": "host-lobby", **answer})
        print(f"\nhost lobby {answer['lobby']} publishes {answer['count']} keys")
        for key, value in sorted(answer["keys"].items()):
            print(f"    {key} = {value}")

    print(f"\nwatching for up to {args.seconds:.0f}s -- searches print as they happen")
    done.wait(args.seconds)
    print(f"watch over: {ended['why']}")

    # A detached session has no exports left to call, and everything below this is the write-up of
    # what was already seen. Losing all of it to the game exiting one second early would throw away
    # the measurement rather than report it short.
    try:
        totals = script.exports_sync.totals()
    except Exception as exc:  # noqa: BLE001 -- the records above stand without the agent's tally
        print(f"\ncould not read the agent's totals ({exc})", file=sys.stderr)
    else:
        print(f"\ntotals {json.dumps(totals)}")
        records.append({"tag": "totals", **totals})

    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(records, indent=2), encoding="utf-8")
        print(f"wrote {args.json}")

    try:
        evidence_module().record(str(AGENT), int(target), len(records), time.monotonic() - started)
    except Exception as exc:  # noqa: BLE001 -- recording must never fail the measurement
        print(f"could not record frida evidence ({exc})", file=sys.stderr)

    try:
        script.unload()
        handle.detach()
    except Exception:  # noqa: BLE001 -- already gone is the state this asks for
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
