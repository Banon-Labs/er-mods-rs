#!/usr/bin/env python3
"""Ask the game what the quick-item cursor is on, and what a D-pad press does to it.

The drivers here refuse with "input is not reaching the game (window focus?)" when one press fails
to move the cursor, which names a symptom and guesses at the cause. There are at least four causes
and they need telling apart: the window is unfocused, the pad injection is dead, the player is in a
state where the cycle is ignored, or there is no player at all.

    uv run --with frida python3 scripts/er-quickitem-probe.py
    python3 scripts/er-quickitem-probe.py --selftest

Reads only, apart from the D-pad presses it is measuring.
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from er_pad_frames import PAD_DOWN, Pad  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
CYCLE = REPO / "scripts" / "frida" / "native-quickslot-cycle.js"
DIALOG = REPO / "scripts" / "frida" / "dialog-open-oracle.js"

PRESSES = 6
SAMPLE_FRAMES = 40
OPEN_THRESHOLD = 5


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--presses", type=int, default=PRESSES)
    parser.add_argument("--clear-queued-use", action="store_true",
                        help="put ChrIns+0x160 back to rest first. A drive killed mid-use leaves "
                             "an item id latched there and the engine refuses the next use")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        checks = [
            ("the cycle agent exists", CYCLE.is_file()),
            ("the dialog oracle exists", DIALOG.is_file()),
            ("the press count is bounded", args.presses <= 16),
        ]
        bad = sum(0 if ok else 1 for _, ok in checks)
        for label, ok in checks:
            print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
        print("selftest: ok" if not bad else f"selftest: {bad} check(s) failed")
        return 0 if not bad else 1

    import frida

    focus = subprocess.run(
        ["hyprctl", "dispatch", 'hl.dsp.focus({ window = "class:steam_app_1245620" })'],
        capture_output=True, text=True, timeout=10)
    print(f"focus: {focus.stdout.strip() or focus.stderr.strip()}", flush=True)

    device = frida.get_device_manager().add_remote_device("127.0.0.1:27042")
    procs = [p for p in device.enumerate_processes() if p.name.lower() == "eldenring.exe"]
    if not procs:
        raise SystemExit("no eldenring.exe visible to the frida server")
    session = device.attach(procs[0].pid)
    cycle = session.create_script(CYCLE.read_text(encoding="utf-8"))
    cycle.load()
    dialog = session.create_script(DIALOG.read_text(encoding="utf-8"))
    dialog.load()
    pad = Pad(session)

    dialog.exports_sync.sample()
    pad.tap(0, hold_frames=0, gap_frames=SAMPLE_FRAMES)
    reading = dialog.exports_sync.sample()
    dialog_open = reading["calls"] >= OPEN_THRESHOLD
    print(f"dialog {'OPEN' if dialog_open else 'closed'} (reader calls={reading['calls']})",
          flush=True)

    before_frames = pad.frames()
    selected = cycle.exports_sync.selected()
    print(f"player: {'present' if selected is not None else 'ABSENT'}", flush=True)
    if selected is None:
        pad.release()
        session.detach()
        return 2

    print(f"use slot: {cycle.exports_sync.useState()}", flush=True)
    if args.clear_queued_use:
        print(f"clear queued use: {cycle.exports_sync.clearQueued()}", flush=True)

    print(f"cursor starts on {selected['hex']}", flush=True)
    seen = [selected["hex"]]
    for index in range(args.presses):
        pad.tap(PAD_DOWN)
        selected = cycle.exports_sync.selected()
        seen.append(selected["hex"])
        print(f"  press {index + 1}: {selected['hex']}", flush=True)

    frames = pad.frames() - before_frames
    distinct = len(set(seen))
    pad.release()
    session.detach()

    print(f"game frames during the probe: {frames}", flush=True)
    if frames < args.presses:
        print("VERDICT: the game is barely ticking -- loading, paused, or not running", flush=True)
        return 3
    if distinct == 1:
        print("VERDICT: the cursor never moved. The pad reaches the game but the cycle is "
              "ignored, so the player is in a state that owns the D-pad", flush=True)
        return 4
    print(f"VERDICT: the cursor cycles -- {distinct} distinct items across {args.presses} presses",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
