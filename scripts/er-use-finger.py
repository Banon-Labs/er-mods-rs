#!/usr/bin/env python3
"""Use a vanilla invasion finger in the running game, agent-driven.

AGENTS.md's 2026-07-22 standing order is that the agent drives every required input; asking the
user to press a button is an instruction-following failure, and its 2026-09-15 companion forbids
reciting a menu route back at the person who owns the game.

The press is not simulated. `er_invasion_warp.dll` exports `er_invasion_warp_use_item`, which
records an item id for its own game task to perform through the engine's `Use` command -- the four
stores into `CSMenuMan->menuData->menuGaitemUseState` plus the `ChrIns+0x168` repeat count that
`lynchpin_use` measured live on 2026-09-09. Driving it that way needs no inventory route, so there
is no row count to guess wrong.

    python3 scripts/er-frida-up.py
    uv run --with frida python3 scripts/er-use-finger.py --row 111     # Festering Bloody Finger
    uv run --with frida python3 scripts/er-use-finger.py --selftest
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

_spec = importlib.util.spec_from_file_location(
    "er_frida_watch", pathlib.Path(__file__).resolve().parent / "er-frida-watch.py"
)
_watch = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_watch)

AGENT = pathlib.Path(__file__).resolve().parent / "frida" / "use-vanilla-finger.js"
ROWS = {102: "Bloody Finger", 111: "Festering Bloody Finger", 112: "Recusant Finger"}


def selftest() -> int:
    """The agent and the export it calls must agree on the name, or the drive is a silent no-op."""
    agent_source = AGENT.read_text(encoding="utf-8")
    assert "er_invasion_warp_use_item" in agent_source, "the agent must name the export"
    dll_source = (
        pathlib.Path(__file__).resolve().parent.parent
        / "crates/er-invasion-warp/src/lib.rs"
    ).read_text(encoding="utf-8")
    assert "er_invasion_warp_use_item" in dll_source, "the DLL must export it"
    # The three ids the agent sends have to be the goods rows with the category nibble, because a
    # wrong nibble is an item that is simply not in the inventory and reads as "you do not hold it".
    for row in ROWS:
        expected = f"0x{(row & 0x0FFF_FFFF) | 0x4000_0000:x}"
        assert expected in agent_source, f"row {row} must map to {expected}"
    print("selftest ok -- agent, export and the three item ids agree")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--row", type=int, default=111, choices=sorted(ROWS))
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    dev = _watch.device()
    try:
        pid = _watch.find_game_bounded(dev)
    except TimeoutError:
        print("the frida server did not answer -- restart it with --force", file=sys.stderr)
        return 2
    if pid is None:
        print("no eldenring.exe in the prefix", file=sys.stderr)
        return 1

    frida_session = dev.attach(pid)

    def on_message(message, _data):
        print(f"AGENT {message.get('payload', message)}", flush=True)

    script = frida_session.create_script(AGENT.read_text(encoding="utf-8"))
    script.on("message", on_message)
    script.load()

    result = script.exports_sync.use_finger(args.row)
    print(json.dumps(result), flush=True)
    # Detaching straight away is safe and there is nothing to wait for. The export stores an item
    # id and returns; the DLL's own game task performs the use on its next tick and owns the
    # 90-frame override for its whole life. Nothing in that path reads this session, so a wait here
    # would only be a guess at a frame budget dressed up as synchronisation.
    frida_session.detach()
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
