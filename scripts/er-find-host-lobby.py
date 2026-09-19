#!/usr/bin/env python3
"""Find every Seamless host currently advertising, and print what each one publishes.

    python3 scripts/er-frida-up.py
    uv run --with frida python3 scripts/er-find-host-lobby.py
    uv run --with frida python3 scripts/er-find-host-lobby.py --block m32_02_00_00

# Why this exists

A host's lobby id changes every time they close and reopen their world, so the id the DLL's sweep
logged minutes ago is stale the moment they rehost -- and reading a stale id back reports
`available = false` forever, which reads exactly like "they are still closed" when the truth is
"you are looking at a lobby nobody is in any more". That cost several rounds of asking the player
whether their friend had reopened yet on 2026-09-18.

This asks Steam fresh instead. It runs one lobby query of its own through
`scripts/frida/lobby-search-proof.js`, then reads every returned lobby's published keys, so the
answer is the current population rather than a remembered id.

Read-only: one `RequestLobbyList` plus `GetLobbyData` getters. Nothing is joined, nothing is
published, and no game state is touched.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
HERE = REPO / "scripts"
SEARCH_AGENT = HERE / "frida" / "lobby-search-proof.js"
DATA_AGENT = HERE / "frida" / "seamless-search-filters.js"
ENDPOINT = "127.0.0.1:27042"

# Seamless's own key names, hashed per build but stable for the one build this repo supports.
AVAILABLE_KEY = "91489e05e1c2c5e7701b2d92ec209a8acd594349f1e21e73422430b114a7c467"
BAND_KEY = "21c40388cba69692c865c11604f6e340fb8f0df83bebea279e802ccc0d46de8e"
POOL_KEY = "lobby_key"
MAP_KEY = "er_invasion_warp_map"
EFFECTS_KEY = "er_invasion_warp_effects"

# Bounded, and spent in an external sleep so this process owns no timer of its own.
POLL_SECONDS = 0.5
POLL_LIMIT = 40


def settle(seconds: float) -> None:
    subprocess.run(["sleep", str(seconds)], check=False, timeout=10)


def selftest() -> int:
    search = SEARCH_AGENT.read_text(encoding="utf-8")
    for needed in ("rpc.exports", "queryBlock", "idle", "results"):
        assert needed in search, f"the search agent must expose {needed}"
    data = DATA_AGENT.read_text(encoding="utf-8")
    assert "lobby_data" in data or "lobbyData" in data, "the data agent must read lobby keys"
    print("selftest ok -- both agents expose what this driver calls")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--block",
        help="only report hosts publishing this block, e.g. m32_02_00_00. Omitted, every "
        "advertising host is reported.",
    )
    parser.add_argument("--json", help="write the findings here")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    import frida

    device = frida.get_device_manager().add_remote_device(ENDPOINT)
    pid = None
    for process in device.enumerate_processes():
        if process.name.lower() == "eldenring.exe":
            pid = process.pid
            break
    if pid is None:
        print("eldenring.exe not found through the wine-side frida server", file=sys.stderr)
        return 2

    session = device.attach(pid)
    search = session.create_script(SEARCH_AGENT.read_text(encoding="utf-8"))
    search.on("message", lambda message, data: None)
    search.load()
    reader = session.create_script(DATA_AGENT.read_text(encoding="utf-8"))
    reader.on("message", lambda message, data: None)
    reader.load()

    # Every host running this mod publishes the map key, so a non-empty test on it is the widest
    # net that still only returns hosts -- and it is the query the DLL's own sweep uses.
    if args.block:
        search.exports_sync.query_block("find", {"resultCount": 50}, args.block)
    else:
        search.exports_sync.query("find", {"resultCount": 50})

    for _ in range(POLL_LIMIT):
        if search.exports_sync.idle():
            break
        settle(POLL_SECONDS)

    # A result record is one query, and the lobbies it returned are nested under `lobbies` -- each
    # already carrying whatever the agent read off it. Reading the record itself as a lobby is what
    # made the first version of this report "no advertising host answered" against a live population.
    records = search.exports_sync.results() or []
    ids: list[str] = []
    for record in records:
        for entry in record.get("lobbies") or []:
            found = entry.get("id") or entry.get("lobby")
            if found:
                ids.append(str(found))
    print(f"{len(ids)} lobby/lobbies returned by {len(records)} query/queries")
    findings = []
    for lobby in ids:
        try:
            answer = reader.exports_sync.lobby_data(str(lobby))
        except Exception:  # noqa: BLE001 -- a lobby that vanished mid-read is data, not a crash
            continue
        keys = answer.get("keys") or {}
        if args.block and keys.get(MAP_KEY) != args.block:
            continue
        findings.append(
            {
                "lobby": str(lobby),
                "available": keys.get(AVAILABLE_KEY),
                "band": keys.get(BAND_KEY),
                "block": keys.get(MAP_KEY),
                "effects": keys.get(EFFECTS_KEY),
                "pool": (keys.get(POOL_KEY) or "")[:16],
            }
        )

    if not findings:
        print("no advertising host answered this query")
    for found in findings:
        print(
            f"{found['lobby']}  available={found['available']}  band={found['band']}  "
            f"block={found['block']}  effects={found['effects']}  pool={found['pool']}"
        )

    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(findings, indent=2), encoding="utf-8")
        print(f"wrote {args.json}")

    search.unload()
    reader.unload()
    session.detach()
    return 0 if findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
