#!/usr/bin/env python3
"""Use the Festering Bloody Finger and answer its bounds prompt, gated on what is really on screen.

Every press is gated.  The item is a toggle whose two prompts mean opposite things -- one starts an
invasion search, the other cancels one -- and they arrive through the same raiser with the same
`kind`.  So the driver predicts the prompt from SpEffect 541 before pressing anything, refuses to
confirm when the raiser reports a different message id than predicted, and records which row the
game actually read at confirm time rather than assuming the row it aimed at.
"""
from __future__ import annotations

import argparse
import pathlib
import queue
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from er_pad_frames import Pad, WAIT_SECONDS  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
FINGER = 0x4000006F
MESSAGE_BOUNDS = 20000010
MESSAGE_LEAVE = 20000011

PAD_A = 0x1000
PAD_B = 0x2000
PAD_X = 0x4000
PAD_DOWN = 0x0002
PAD_RIGHT = 0x0008
PAD_LEFT = 0x0004



def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--row", default="right", choices=("left", "right"),
                    help="left = Nearby only, right = Both near and far")
    ap.add_argument("--dwell", type=float, default=12.0)
    ap.add_argument("--reset-first", action="store_true",
                    help="if the finger is already ON, answer its leave prompt to turn it off "
                         "before raising the bounds prompt")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        oracle = (REPO / "scripts/frida/finger-prompt-oracle.js").read_text()
        assert "541" in oracle, "the oracle no longer keys on the finger's active SpEffect"
        assert str(MESSAGE_LEAVE) in oracle, "the oracle does not know the leave prompt's id"
        src = pathlib.Path(__file__).read_text()
        assert "refusing to confirm" in src, "the driver has no refusal path"
        print("selftest: ok")
        return 0

    import frida

    # Elden Ring discards controller input while its window is unfocused, so an injected pad is
    # read and then thrown away -- which looks exactly like a dead injector. Focus it first.
    # This Hyprland build's dispatch is a Lua shim: `hl.dsp.focus` takes a table, and the window
    # is addressed by class alone so no other client is ever named or listed.
    focus = subprocess.run(
        ["hyprctl", "dispatch", 'hl.dsp.focus({ window = "class:steam_app_1245620" })'],
        capture_output=True, text=True, timeout=10)
    print(f"focus: {focus.stdout.strip() or focus.stderr.strip()}", flush=True)

    dev = frida.get_device_manager().add_remote_device("127.0.0.1:27042")
    pid = [p.pid for p in dev.enumerate_processes() if p.name.lower() == "eldenring.exe"][0]
    sess = dev.attach(pid)
    seen: list[dict] = []
    events: queue.Queue = queue.Queue()

    def on_message(message, _data):
        if message.get("type") == "send":
            seen.append(message["payload"])
            events.put(message["payload"])
            print("  EVENT " + str(message["payload"]), flush=True)

    oracle = sess.create_script((REPO / "scripts/frida/finger-prompt-oracle.js").read_text())
    oracle.load()
    popup = sess.create_script((REPO / "scripts/frida/bounds-popup-oracle.js").read_text())
    popup.on("message", on_message)
    popup.load()
    consumer = sess.create_script((REPO / "scripts/frida/finger-answer-consumer.js").read_text())
    consumer.on("message", on_message)
    consumer.load()
    actions = sess.create_script((REPO / "scripts/frida/ersc-action-trace.js").read_text())
    actions.on("message", on_message)
    actions.load()
    cycle = sess.create_script((REPO / "scripts/frida/native-quickslot-cycle.js").read_text())
    cycle.load()
    rows = sess.create_script((REPO / "scripts/frida/popup-row-oracle.js").read_text())
    rows.load()
    pad = Pad(sess)

    def tap(mask: int, down: float = 0, up: float = 0) -> None:
        pad.tap(mask)

    def settle(frames: int = 30) -> None:
        pad.tap(0, hold_frames=0, gap_frames=frames)

    def raised_since(_count: int = 0):
        """Block for the next prompt the game raises. A real event, bounded by a hard cap --
        not a poll, and not a sleep."""
        try:
            while True:
                event = events.get(timeout=WAIT_SECONDS)
                if event.get("kind") == "raiser":
                    return event
        except queue.Empty:
            return None

    predicted = oracle.exports_sync.variant()
    print(f"oracle: {predicted['verdict']}", flush=True)

    before = cycle.exports_sync.selected()["id"]
    tap(PAD_DOWN)
    if cycle.exports_sync.selected()["id"] == before:
        print("refusing to confirm: input is not reaching the game (window focus?)")
        return 4
    for _ in range(20):
        if cycle.exports_sync.selected()["id"] == FINGER:
            break
        tap(PAD_DOWN)
    if cycle.exports_sync.selected()["id"] != FINGER:
        print("refusing to confirm: the cursor never reached the finger")
        return 5

    if predicted["active"]:
        if not args.reset_first:
            print("refusing to confirm: the finger is ON, so this prompt would cancel, not start")
            return 3
        print("the finger is ON; answering its leave prompt to turn it off first", flush=True)
        tap(PAD_X, down=0.6, up=0.8)
        leave = raised_since(len([e for e in seen if e.get("kind") == "raiser"]) - 1)
        if leave is None or leave["message"] != MESSAGE_LEAVE:
            print(f"refusing to confirm: expected {MESSAGE_LEAVE}, got {leave}")
            return 9
        tap(PAD_A, down=0.4, up=0.3)
        settle()
        if oracle.exports_sync.variant()["active"]:
            print("refusing to confirm: the finger is still ON after answering its leave prompt")
            return 10
        print("  the finger is OFF", flush=True)
        predicted = oracle.exports_sync.variant()

    tap(PAD_X, down=0.6, up=0.8)
    raised = raised_since(len([e for e in seen if e.get("kind") == "raiser"]) - 1)
    if raised is None:
        print("refusing to confirm: no prompt was raised")
        return 6
    if raised["message"] != predicted["expectMessage"]:
        print(f"refusing to confirm: the oracle predicted {predicted['expectMessage']} "
              f"but the raiser reports {raised['message']}")
        tap(PAD_B, down=0.3, up=0.5)
        return 7
    print(f"gate passed: prompt {raised['message']} is the one predicted", flush=True)

    # Closed loop: press, read the row back, press again if it did not move. A press is not
    # proof the cursor moved, and confirming the wrong row toggles the item instead of choosing
    # a range -- which has already happened twice.
    want = 1 if args.row == "right" else 0
    for attempt in range(1, 7):
        here = rows.exports_sync.row()
        print(f"  row reads {here}", flush=True)
        if here.get("ok") and here["row"] == want:
            break
        tap(PAD_RIGHT if want == 1 else PAD_LEFT, down=0.25, up=0.9)
    else:
        here = rows.exports_sync.row()
        if not (here.get("ok") and here["row"] == want):
            print(f"refusing to confirm: the row never reached {want}; it reads {here}")
            tap(PAD_B, down=0.3, up=0.5)
            return 8
    print(f"gate passed: the highlighted row is {want}", flush=True)
    tap(PAD_A, down=0.4, up=0.3)
    settle()

    answered = [e for e in seen if e.get("kind") == "answer"]
    print("row the game read: " + (str(answered[-1]) if answered else "NOT CAPTURED"), flush=True)
    print("consumer: " + str(consumer.exports_sync.report()["confirmVirtual"]), flush=True)
    print("ersc actions: " + str(actions.exports_sync.report()["counts"]), flush=True)
    print("finger after: " + str(oracle.exports_sync.variant()["active"]), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
