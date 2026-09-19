#!/usr/bin/env python3
"""Prove the fetch-read-hand chain against the live game before any of it is written in Rust.

    python3 scripts/er-frida-up.py
    uv run --with frida python3 scripts/er-prove-lobby-handoff.py --block m61_48_45_00

Runs `scripts/frida/hand-lobby-to-seamless.js`, which asks Steam for lobbies itself, fetches every
one it matched with `GetLobbyByIndex`, reads their keys with `GetLobbyData`, and resolves each
owner's `CSteamID` with `GetLobbyOwner`. With `--session` it then reads the Seamless session's
qwords and reports whether any of them already holds one of those ids -- which is where a target
would live, and the thing no measurement in this repo has yet found.

Why this exists rather than the Rust it would justify: user directive 2026-09-17, "you can prove
what you're about to commit/change in rust through Frida by making a direct connection". The fetch
half was already proven by `scripts/er-lobby-search-proof.py`; the hand-off half is not proven at
all, and Rust written against an unproven field is a guess wearing an integration's clothes.

Read-only: requests, getters and memory reads. Nothing is written and no lobby is joined.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
AGENT = REPO / "scripts" / "frida" / "hand-lobby-to-seamless.js"
EVIDENCE_SCRIPT = REPO / "scripts" / "er-frida-evidence.py"

# The value that marks a Seamless advertisement lobby, as `er-lobby-search-proof.py` records it.
SEAMLESS_MASTER = "yknx3_seamless_master_lobby"


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
    parser.add_argument(
        "--block",
        help="ask only for hosts publishing this block, e.g. m61_48_45_00; omit for every lobby",
    )
    parser.add_argument(
        "--session",
        help="Seamless session address, to scan for a field already holding a returned CSteamID",
    )
    parser.add_argument("--port", type=int, default=27042)
    parser.add_argument("--json", help="write the full answer here")
    parser.add_argument(
        "--join",
        action="store_true",
        help="join every matched lobby, which is the only way Steam will name its owner: "
        "GetLobbyOwner answers 0 to a non-member. Leaves again unless --stay is given.",
    )
    parser.add_argument(
        "--stay",
        action="store_true",
        help="with --join, remain in the lobby instead of leaving it straight away. A bare join "
        "enters as a CO-OP guest, not an invader, so staying puts you in the host's world.",
    )
    parser.add_argument(
        "--leave",
        metavar="LOBBY",
        help="leave this lobby and do nothing else, for a --stay that should not have stayed",
    )
    parser.add_argument(
        "--filter",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="add one string-equality filter, repeatable. Seamless hashes its key names, so pass "
        "them as the hashes `scripts/er-watch-seamless-search.py` captured. Sending Seamless's own "
        "set with exactly one value changed is how a filter is proven to be the one excluding a "
        "host: the count goes 0 -> 1 on the value that matches what she publishes.",
    )
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
    count = 0

    handle = device.attach(target)
    script = handle.create_script(AGENT.read_text(encoding="utf-8"))

    def on_message(message, _data):
        nonlocal count
        count += 1
        payload = message.get("payload") if message["type"] == "send" else message
        print(f"  {json.dumps(payload)[:400]}")

    script.on("message", on_message)
    script.load()
    print(f"attached to pid {target}")

    if args.leave:
        print(f"leave {args.leave}: {json.dumps(script.exports_sync.leave(args.leave))}")
        script.unload()
        handle.detach()
        return 0

    filters = []
    if args.block:
        filters.append(["er_invasion_warp_map", args.block])
    for pair in args.filter:
        key, sep, value = pair.partition("=")
        if not sep:
            raise SystemExit(f"--filter wants KEY=VALUE, got {pair!r}")
        filters.append([key, value])
    if filters:
        print("filters:")
        for key, value in filters:
            print(f"    {key} = {value}")

    answer = script.exports_sync.look(args.session, filters)

    fetch = answer.get("fetch", answer) if isinstance(answer, dict) else {}
    lobbies = fetch.get("lobbies") or []
    print(f"\nmatching={fetch.get('matching')} fetched={len(lobbies)}")
    for entry in lobbies:
        keys = entry.get("keys", {})
        mark = " <- seamless advert" if SEAMLESS_MASTER in json.dumps(keys) else ""
        print(f"  lobby {entry['id']} owner {entry['owner']}{mark}")
        for key, value in keys.items():
            print(f"      {key} = {value}")

    if args.join:
        for entry in lobbies:
            joined = script.exports_sync.join(entry["id"], args.stay)
            print(f"\njoin {entry['id']}: {json.dumps(joined)}")
            if joined.get("entered"):
                print(f"  owner  {joined.get('owner')}")
                for member in joined.get("members") or []:
                    print(f"  member {member}")
            else:
                print(
                    "  refused -- EChatRoomEnterResponse "
                    f"{joined.get('enter_response')} (1 is success)"
                )

    session = answer.get("session") if isinstance(answer, dict) else None
    if session:
        hits = session.get("holds_a_returned_steamid") or []
        print(f"\nsession state={session.get('state')} nonzero qwords={session.get('nonzero_qwords')}")
        if hits:
            for hit in hits:
                print(f"  {hit['offset']} already holds {hit['value']}")
        else:
            print("  no qword in +0x100..+0x300 holds a lobby or owner id this query returned")

    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(answer, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")

    try:
        evidence_module().record(str(AGENT), int(target), count, time.monotonic() - started)
    except Exception as exc:  # noqa: BLE001 -- recording must never fail the measurement
        print(f"could not record frida evidence ({exc})", file=sys.stderr)

    script.unload()
    handle.detach()
    return 0


if __name__ == "__main__":
    sys.exit(main())
