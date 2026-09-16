#!/usr/bin/env python3
"""Tap controller buttons at the live game, counted in game frames.

For the one thing every other driver needs and none of them owns: clearing whatever is on screen
before a run starts. A dialog left open swallows the next use press, and the run then reads as "the
item raised nothing" -- a false negative this repo has produced more than once.

    uv run --with frida python3 scripts/er-pad-tap.py b b
    uv run --with frida python3 scripts/er-pad-tap.py right a
    python3 scripts/er-pad-tap.py --selftest

Names, not masks, so a caller never has to remember that use is 0x4000.
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from er_pad_frames import (  # noqa: E402
    PAD_A,
    PAD_B,
    PAD_DOWN,
    PAD_LEFT,
    PAD_RIGHT,
    PAD_UP,
    PAD_X,
    Pad,
)

BUTTONS = {
    "a": PAD_A, "b": PAD_B, "x": PAD_X,
    "up": PAD_UP, "down": PAD_DOWN, "left": PAD_LEFT, "right": PAD_RIGHT,
}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("buttons", nargs="*", help=" ".join(sorted(BUTTONS)))
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        checks = [
            ("use is pad X", BUTTONS["x"] == 0x4000),
            ("confirm is pad A", BUTTONS["a"] == 0x1000),
            ("back out is pad B", BUTTONS["b"] == 0x2000),
            ("the bounds cursor moves right", BUTTONS["right"] == 0x0008),
        ]
        bad = sum(0 if ok else 1 for _, ok in checks)
        for label, ok in checks:
            print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
        print("selftest: ok" if not bad else f"selftest: {bad} check(s) failed")
        return 0 if not bad else 1

    unknown = [b for b in args.buttons if b.lower() not in BUTTONS]
    if unknown or not args.buttons:
        print(f"unknown or missing button(s): {unknown or 'none given'}; "
              f"choose from {' '.join(sorted(BUTTONS))}")
        return 2

    import frida

    # The game throws away injected pad state while its window is unfocused, which looks exactly
    # like a dead injector. The window is addressed by class alone, so no other client is named.
    subprocess.run(
        ["hyprctl", "dispatch", 'hl.dsp.focus({ window = "class:steam_app_1245620" })'],
        capture_output=True, text=True, timeout=10)

    device = frida.get_device_manager().add_remote_device("127.0.0.1:27042")
    procs = [p for p in device.enumerate_processes() if p.name.lower() == "eldenring.exe"]
    if not procs:
        raise SystemExit("no eldenring.exe visible to the frida server")
    session = device.attach(procs[0].pid)
    pad = Pad(session)
    for name in args.buttons:
        pad.tap(BUTTONS[name.lower()])
        print(f"tapped {name}", flush=True)
    pad.release()
    session.detach()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
