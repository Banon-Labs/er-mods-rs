#!/usr/bin/env python3
"""Say whether this client and a named host are in the same Seamless matchmaking pool.

    python3 scripts/er-frida-up.py
    uv run --with frida python3 scripts/er-pool-check.py --lobby 109775241915869141

# Why this exists

`lobby_key` is compared with `k_ELobbyComparisonEqual`, so two clients whose keys differ are
invisible to each other in both directions -- no band, no location and no amount of climbing can
bridge it. Nothing in this repo compared the two values, and on 2026-09-18 that cost an entire
evening: the sweep logged the host's key on every hit (`sweep: lobby ... lobby_key=34154670c4db`)
and the search sent `f89c2a50...`, and because the two numbers were never put side by side the
session chased the band ladder, the place ring and the advertised-availability flag instead. All
three were already agreeing. See bd `seamless-band-is-the-single-field-excluding-a-friend-proven-live-2026-09-18`.

The comparison needs both halves at once and each comes from a different place, which is the whole
reason it kept not happening:

  * the host's half is published on their lobby and readable without joining, via
    `GetLobbyDataCount` / `GetLobbyDataByIndex`;
  * this client's half only exists while a search is in flight, because Seamless computes it when
    the search starts and puts it in the outgoing filter set.

So this waits for a search rather than asking for one. Drive the item with
`scripts/er-drive-item.py` (or let the player use it) and the answer lands on the next query.

Read-only: entry observers and getters, no join, no lobby write, nothing driven.

Exit status is the answer, so this can gate a harness:
    0  the keys match -- the pool is not what is excluding this host
    1  the keys differ -- nothing else can matter until they agree
    2  no search was observed in time, or the host's lobby could not be read
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
AGENT = REPO / "scripts" / "frida" / "seamless-search-filters.js"
EVIDENCE_SCRIPT = REPO / "scripts" / "er-frida-evidence.py"

# Seamless publishes the pool fingerprint under its own unhashed name, unlike the band and
# availability fields. Matched by name because that is what the agent reports it as.
POOL_KEY = "lobby_key"


def evidence_module():
    spec = importlib.util.spec_from_file_location("er_frida_evidence", EVIDENCE_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {EVIDENCE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def short(value: str) -> str:
    """Enough of a 64-character hash to compare by eye, without wrapping the terminal."""
    return value if len(value) <= 16 else f"{value[:16]}..."


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lobby", required=True, help="the host's lobby id, from a sweep hit")
    parser.add_argument("--seconds", type=float, default=60.0, help="how long to wait for a search")
    parser.add_argument("--port", type=int, default=27042)
    parser.add_argument("--json", help="write the verdict here")
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

    started = time.monotonic()
    handle = device.attach(target)
    script = handle.create_script(AGENT.read_text(encoding="utf-8"))

    # Filled by the first search that carries the field. A search may send several filters and the
    # pool key is only one of them, so this is not simply "the first filter seen".
    ours: dict = {"key": None}
    messages = {"n": 0}

    def on_message(message, _data):
        if message["type"] != "send":
            return
        payload = message["payload"]
        messages["n"] += 1
        if payload.get("tag") != "search" or ours["key"] is not None:
            return
        for entry in payload.get("filters") or []:
            if entry.get("key") == POOL_KEY and entry.get("value"):
                ours["key"] = entry["value"]
                return

    script.on("message", on_message)
    script.load()
    print(f"attached to pid {target}")

    try:
        answer = script.exports_sync.lobby_data(args.lobby)
    except Exception as exc:  # noqa: BLE001 -- an unreadable lobby is an answer, not a crash
        print(f"could not read lobby {args.lobby}: {exc}", file=sys.stderr)
        script.unload()
        handle.detach()
        return 2
    theirs = (answer.get("keys") or {}).get(POOL_KEY)
    if not theirs:
        print(f"lobby {args.lobby} publishes no {POOL_KEY} -- it is gone, or it is not a Seamless lobby")
        script.unload()
        handle.detach()
        return 2
    print(f"host  {args.lobby}  {POOL_KEY} = {theirs}")

    print(f"\nwaiting up to {args.seconds:.0f}s for a search of ours to carry its own {POOL_KEY}")
    print("(nothing here starts one -- drive the item, or use it, and the next query answers)")
    # The wait is bounded and spent in an external `sleep`, so this process owns no timer of its
    # own -- the same shape `scripts/er-drive-item.py` uses. The readiness condition is the agent
    # having reported a filter carrying the pool key, not the clock; `args.seconds` is only the cap.
    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline and ours["key"] is None:
        subprocess.run(["sleep", "0.25"], check=False, timeout=10)

    verdict = {"host_lobby": args.lobby, "theirs": theirs, "ours": ours["key"]}
    if ours["key"] is None:
        print(f"\nNO SEARCH OBSERVED in {args.seconds:.0f}s, so this client's {POOL_KEY} is unknown.")
        print("Seamless computes it when a search starts; without one there is nothing to compare.")
        code = 2
    elif ours["key"] == theirs:
        print(f"\nours  {ours['key']}")
        print(f"\nSAME POOL -- {short(theirs)} on both sides. Whatever is excluding this host, it")
        print("is not the pool: look at the band pair and the advertised-availability flag.")
        code = 0
    else:
        print(f"\nours  {ours['key']}")
        print(f"\nDIFFERENT POOLS -- ours {short(ours['key'])} against theirs {short(theirs)}.")
        print(f"Steam compares {POOL_KEY} for equality, so this host cannot be returned by any")
        print("query this game sends, at any band, in any location. Nothing else matters until")
        print("the two installs agree.")
        print()
        print("Check what THIS HARNESS wrote before blaming a shell. Any tool that writes a param")
        print("byte into the live game moves this key, and on 2026-09-18 the divergence that cost")
        print("an evening was the agent's own item driver: lynchpin-item-drive.js cleared")
        print("`disableOffline` in the EquipParamGoods rows for goods 102/111/112 and printed the")
        print("write every time as `fingers: {\"102\": 99->67, ...}`. Same build, same save, same")
        print("config:")
        print("    agent-driven item  -> f89c2a507f99a522...  matched nobody")
        print("    hand-driven item   -> 34154670c4dbf536...  invasion landed")
        print("34154670... is the ordinary vanilla key, published by hosts running none of these")
        print("DLLs. If no harness wrote a param byte, the remaining causes are a regulation or")
        print("param mod on one of the two machines.")
        code = 1
    verdict["same_pool"] = code == 0

    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(verdict, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")

    try:
        evidence_module().record(str(AGENT), int(target), messages["n"], time.monotonic() - started)
    except Exception as exc:  # noqa: BLE001 -- recording must never fail the measurement
        print(f"could not record frida evidence ({exc})", file=sys.stderr)

    script.unload()
    handle.detach()
    return code


if __name__ == "__main__":
    sys.exit(main())
