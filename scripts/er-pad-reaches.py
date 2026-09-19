#!/usr/bin/env python3
"""Does injected pad state reach Elden Ring? Measure it instead of guessing at window focus.

`er-drive-finger-bounds.py` reports "input is not reaching the game (window focus?)" whenever a
D-pad press leaves the quick slot unchanged. That message names one cause out of four and has been
wrong about it. This asks the game directly: how many times did it poll `XInputGetState`, and what
button mask did it receive, with and without an injected hold.

Three arms, and the control is the point:

  idle     nothing injected      -- proves the poll hook fires at all
  held     a mask injected       -- proves the injector reaches the same buffer the game reads
  slot     the quick slot        -- proves whether the game acted on what it received
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

REPO = pathlib.Path(__file__).resolve().parent.parent
PAD_DOWN = 0x0002
# Long enough that a poll at ~82/second cannot miss it, short enough to be one gesture.
HOLD_FRAMES = 20
GAP_FRAMES = 10


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mask", type=lambda v: int(v, 0), default=PAD_DOWN,
                    help="button mask to inject; default is D-pad Down (0x0002)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        agent = (REPO / "scripts/frida/pad-reaches-the-game.js").read_text()
        assert "onLeave" in agent, "the mask must be read after the call fills the out-parameter"
        assert "BUTTONS_OFFSET = 4" in agent, "wButtons sits one dword into XINPUT_STATE"
        print("selftest: ok")
        return 0

    import frida

    from er_pad_frames import Pad  # noqa: E402

    dev = frida.get_device_manager().add_remote_device("127.0.0.1:27042")
    pid = [p.pid for p in dev.enumerate_processes() if p.name.lower() == "eldenring.exe"][0]
    sess = dev.attach(pid)

    probe = sess.create_script((REPO / "scripts/frida/pad-reaches-the-game.js").read_text())
    probe.on("message", lambda m, _d: print("  " + str(m.get("payload", m)), flush=True))
    probe.load()
    cycle = sess.create_script((REPO / "scripts/frida/native-quickslot-cycle.js").read_text())
    cycle.load()
    pad = Pad(sess)

    # Arm one: is the game polling at all, with nothing injected?
    probe.exports_sync.reset()
    pad.tap(0, hold_frames=0, gap_frames=30)
    idle = probe.exports_sync.report()
    print(f"idle:   polls={idle['polls']} nonZeroMasks={idle['nonZeroMasks']} "
          f"lastMask={idle['lastMask']} injector={idle['injector']}", flush=True)
    if idle["polls"] == 0:
        print("the game is not polling XInputGetState -- nothing injected there can ever be read")
        return 2

    # Arm two: inject, and see whether the game is handed the mask.
    before = cycle.exports_sync.selected()["id"]
    probe.exports_sync.reset()
    pad.tap(args.mask, hold_frames=HOLD_FRAMES, gap_frames=GAP_FRAMES)
    held = probe.exports_sync.report()
    after = cycle.exports_sync.selected()["id"]
    print(f"held:   polls={held['polls']} nonZeroMasks={held['nonZeroMasks']} "
          f"masks={held['masks']}", flush=True)
    print(f"slot:   before={before:#x} after={after:#x} moved={before != after}", flush=True)

    if held["nonZeroMasks"] == 0:
        print("VERDICT: the game polls, but the injected mask never reaches the buffer it reads -- "
              "the injector is the broken half, not window focus")
        return 3
    if before == after:
        print("VERDICT: the game RECEIVED the mask and did not act on it -- so this is the "
              "character being unable to act (or a binding that is not quick-slot cycling), "
              "not input failing to arrive")
        return 4
    print("VERDICT: injected input reaches the game and the game acts on it")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
