#!/usr/bin/env python3
"""Say whether a dialog is on screen, and optionally press until it is not.

Every driver in this repo assumes the screen is clear when it starts, and none of them looks. A
dialog left open swallows the next press, so the run reports that the item raised nothing -- a false
negative produced repeatedly, including by an agent that printed "dialog cleared" after injecting
pad B and never checked.

    uv run --with frida python3 scripts/er-dialog-state.py
    uv run --with frida python3 scripts/er-dialog-state.py --clear
    python3 scripts/er-dialog-state.py --selftest

`--clear` presses and re-reads after each press, and stops on the reading rather than on a count of
presses. If the presses do not close it, that is reported as a refusal, not papered over.
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from er_pad_frames import PAD_A, PAD_B, Pad  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
AGENT = REPO / "scripts" / "frida" / "dialog-open-oracle.js"

# Frames to let the reader run before sampling. The game polls it once a frame while a dialog is
# open, so anything above a handful of calls in this window is a dialog and zero is a clear screen.
SAMPLE_FRAMES = 40
OPEN_THRESHOLD = 5
# Presses to try before giving up and saying so. Back out first, then confirm: a prompt with no
# cancel row ignores pad B entirely.
PRESS_LADDER = [("b", PAD_B), ("b", PAD_B), ("a", PAD_A), ("b", PAD_B)]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clear", action="store_true", help="press until the reading says closed")
    parser.add_argument("--answer", type=int, metavar="VALUE",
                        help="write this answer into the reader's out-parameter: 0 closes without "
                             "acting, 1 is Nearby only, 2 is Both near and far. The way out of a "
                             "prompt with no cancel row, which no button can close")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        source = AGENT.read_text(encoding="utf-8")
        checks = [
            ("the agent exists", AGENT.is_file()),
            ("it hooks the answer reader", "0x1407ee550" in source),
            ("it follows an Arxan stub", "function follow" in source),
            ("it reports a windowed call count", "sample:" in source),
            ("the press ladder is bounded", len(PRESS_LADDER) <= 6),
        ]
        bad = sum(0 if ok else 1 for _, ok in checks)
        for label, ok in checks:
            print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
        print("selftest: ok" if not bad else f"selftest: {bad} check(s) failed")
        return 0 if not bad else 1

    import frida

    subprocess.run(
        ["hyprctl", "dispatch", 'hl.dsp.focus({ window = "class:steam_app_1245620" })'],
        capture_output=True, text=True, timeout=10)

    device = frida.get_device_manager().add_remote_device("127.0.0.1:27042")
    procs = [p for p in device.enumerate_processes() if p.name.lower() == "eldenring.exe"]
    if not procs:
        raise SystemExit("no eldenring.exe visible to the frida server")
    session = device.attach(procs[0].pid)
    script = session.create_script(AGENT.read_text(encoding="utf-8"))
    script.load()
    pad = Pad(session)

    def read() -> dict:
        script.exports_sync.sample()
        pad.tap(0, hold_frames=0, gap_frames=SAMPLE_FRAMES)
        return script.exports_sync.sample()

    reading = read()
    open_now = reading["calls"] >= OPEN_THRESHOLD
    print(f"dialog {'OPEN' if open_now else 'closed'}: "
          f"reader calls={reading['calls']} lastResult={reading['lastResult']}", flush=True)

    if args.answer is not None and open_now:
        print("answer: " + str(script.exports_sync.answer(args.answer)), flush=True)
        reading = read()
        open_now = reading["calls"] >= OPEN_THRESHOLD
        print(f"  after answer {args.answer}: calls={reading['calls']} "
              f"lastResult={reading['lastResult']} "
              f"-> {'still open' if open_now else 'closed'}", flush=True)

    if args.clear and open_now:
        for name, mask in PRESS_LADDER:
            pad.tap(mask)
            reading = read()
            open_now = reading["calls"] >= OPEN_THRESHOLD
            print(f"  after {name}: calls={reading['calls']} lastResult={reading['lastResult']} "
                  f"-> {'still open' if open_now else 'closed'}", flush=True)
            if not open_now:
                break

    pad.release()
    session.detach()
    if open_now:
        print("VERDICT: a dialog is on screen and the presses did not close it", flush=True)
        return 1
    print("VERDICT: the screen is clear", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
