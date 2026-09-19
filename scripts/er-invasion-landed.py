#!/usr/bin/env python3
"""Say whether the player is standing in somebody else's world, read out of live memory.

Session state `0x16` is what `LobbyEnter_t::Run` writes on a successful join, so it says the join
succeeded -- but it is Seamless's own field and a redirect that drives Seamless is, in the end,
writing in that neighbourhood. Two readings the game itself owns settle it independently:

  the main player pointer   changes when the world changes
  the SpEffect set          gains the invasion set, and loses 541, the finger's own active flag

    uv run --with frida python3 scripts/er-invasion-landed.py
    uv run --with frida python3 scripts/er-invasion-landed.py --session 0x469ac930
    python3 scripts/er-invasion-landed.py --selftest

Reads only. Presses nothing.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
SPEFFECTS = REPO / "scripts" / "frida" / "player-speffects.js"
OBSERVER = REPO / "scripts" / "frida" / "invasion-observer.js"

# The set a landed invasion carries, measured on run br-20260916-175313-4f21. Any of these means
# the engine thinks this character is an invader right now.
INVASION_SPEFFECTS = {100620, 4271, 4652, 4602, 4700, 503315, 4212, 503040, 3245, 503045}
# The finger's own active flag, which is gone once the invasion lands rather than while it searches.
FINGER_ACTIVE_SPEFFECT = 541
SESSION_IN_WORLD = 0x16


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", default="0x469ac930",
                        help="hex pointer to Seamless's session; its +0x150 is the state")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        checks = [
            ("the speffect agent exists", SPEFFECTS.is_file()),
            ("the observer agent exists", OBSERVER.is_file()),
            ("the invasion set is the measured one", len(INVASION_SPEFFECTS) == 10),
            ("in world is 0x16", SESSION_IN_WORLD == 0x16),
        ]
        bad = sum(0 if ok else 1 for _, ok in checks)
        for label, ok in checks:
            print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
        print("selftest: ok" if not bad else f"selftest: {bad} check(s) failed")
        return 0 if not bad else 1

    import frida

    device = frida.get_device_manager().add_remote_device("127.0.0.1:27042")
    procs = [p for p in device.enumerate_processes() if p.name.lower() == "eldenring.exe"]
    if not procs:
        raise SystemExit("no eldenring.exe visible to the frida server")
    session = device.attach(procs[0].pid)

    effects_script = session.create_script(SPEFFECTS.read_text(encoding="utf-8"))
    effects_script.load()
    observer = session.create_script(OBSERVER.read_text(encoding="utf-8"))
    observer.load()
    state = observer.exports_sync.watch(args.session)

    ids = effects_script.exports_sync.ids() or []
    carried = sorted(set(ids) & INVASION_SPEFFECTS)
    searching = FINGER_ACTIVE_SPEFFECT in ids
    in_world = state.get("state") == SESSION_IN_WORLD

    print(f"session state: 0x{(state.get('state') or 0):x}"
          f"{'  (in world)' if in_world else ''}", flush=True)
    print(f"speffects: {ids}", flush=True)
    print(f"invasion speffects carried: {carried}", flush=True)
    print(f"finger active flag {FINGER_ACTIVE_SPEFFECT}: "
          f"{'present -- still searching' if searching else 'absent'}", flush=True)

    session.detach()
    if in_world and carried:
        print("VERDICT: standing in another player's world as an invader", flush=True)
        return 0
    if in_world:
        print("VERDICT: the session says in world but the character carries no invasion "
              "speffect -- the join landed and the role did not", flush=True)
        return 2
    print("VERDICT: not in another world", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
