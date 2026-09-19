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
# How long to wait for a world before calling the boot failed, counted in game frames rather than
# seconds: a slow load is then patience and a dead process is still a failure. Forty settles of
# thirty frames is about two minutes at 30fps, which covers a cold load.
WORLD_SETTLES = 40
WORLD_SETTLE_FRAMES = 30
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
    ap.add_argument(
        "--finger",
        type=lambda v: int(v, 0),
        default=FINGER,
        help="which finger to use, as a tagged item id. Default is the Festering Bloody "
        "Finger (0x4000006f); 0x40000070 is the Bloody Finger. The bounds prompt is the "
        "same for both -- this only has to match what the character is carrying.",
    )
    ap.add_argument("--row", default="right", choices=("left", "right"),
                    help="left = Nearby only, right = Both near and far")
    ap.add_argument("--dwell", type=float, default=12.0)
    ap.add_argument("--reset-first", action="store_true",
                    help="if the finger is already ON, answer its leave prompt to turn it off "
                         "before raising the bounds prompt")
    ap.add_argument("--redirect", metavar="OWNER",
                    help="hex pointer whose +0x58 is the Seamless session; a Both-near-and-far "
                         "answer then calls ersc+0x25850 with it instead of the vanilla request")
    ap.add_argument("--ersc-trace", action="store_true",
                    help="count calls to Seamless's option actions. This hooks ersc+0x25850, which "
                         "er_invasion_warp byte-checks before calling, so the DLL's own invade "
                         "drive is refused for as long as it is armed. Diagnostic only.")
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

    # Enable the item before anything presses it. Seamless leaves all four of CanUseGoods' terms
    # failing for the vanilla fingers, so without this the use press is refused and the run reads
    # as "the item raised nothing" -- a false negative this driver produced repeatedly.
    gate = sess.create_script((REPO / "scripts/frida/force-canusegoods.js").read_text())
    gate.on("message", on_message)
    gate.load()
    gate_report = gate.exports_sync.report()
    print(f"gate: body={gate_report['body']} followed={gate_report['followed']} "
          f"enabled={gate_report['enabled']}", flush=True)
    if not gate_report["enabled"]:
        print("refusing to press: the CanUseGoods override is not enabled")
        return 11

    oracle = sess.create_script((REPO / "scripts/frida/finger-prompt-oracle.js").read_text())
    oracle.load()
    popup = sess.create_script((REPO / "scripts/frida/bounds-popup-oracle.js").read_text())
    popup.on("message", on_message)
    popup.load()
    consumer = sess.create_script((REPO / "scripts/frida/finger-answer-consumer.js").read_text())
    consumer.on("message", on_message)
    consumer.load()
    # Off by default, because arming it disables the thing this driver exists to test.
    #
    # `ersc-action-trace.js` puts an `Interceptor` on `ersc+0x25850`, and `er_invasion_warp`
    # byte-checks that function's prologue before it will call it. With the trace loaded the DLL
    # reads Frida's trampoline instead of Seamless's bytes and logs "an armed search was dropped
    # because ersc+0x25850 would not hand out its invade action" -- measured on
    # br-20260917-025609-d24a, where the far half reached the drive with a correct owner and was
    # refused by our own instrument. The codebase already learned this about its own detours; a
    # Frida hook on the same address is the same mistake wearing a different hat.
    actions = None
    if args.ersc_trace:
        actions = sess.create_script((REPO / "scripts/frida/ersc-action-trace.js").read_text())
        actions.on("message", on_message)
        actions.load()
    cycle = sess.create_script((REPO / "scripts/frida/native-quickslot-cycle.js").read_text())
    cycle.load()
    rows = sess.create_script((REPO / "scripts/frida/popup-row-oracle.js").read_text())
    rows.load()
    redirect = sess.create_script(
        (REPO / "scripts/frida/finger-redirect-to-seamless.js").read_text())
    redirect.on("message", on_message)
    redirect.load()
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

    # The oracle answers `{ok: false, why: ...}` while the player does not exist yet, and the
    # launcher returns as soon as the DLL says it loaded -- which is a minute before a world. This
    # used to index `verdict` straight away and die with `KeyError: 'verdict'`, reporting a boot
    # that had not finished as a broken driver.
    #
    # Waited out in game frames rather than seconds, so a slow load is patience and a dead process
    # is a failure.
    for _ in range(WORLD_SETTLES):
        predicted = oracle.exports_sync.variant()
        if predicted.get("ok"):
            break
        settle(WORLD_SETTLE_FRAMES)
    else:
        print(
            f"the world never came up: {predicted.get('why', predicted)}",
            file=sys.stderr,
        )
        return 2
    print(f"oracle: {predicted['verdict']}", flush=True)

    # Is input arriving? Ask the thing that can answer it.
    #
    # This used to tap Down and require the selected id to change, calling a no-change
    # "input is not reaching the game (window focus?)". That oracle is invalid whenever the
    # quick-slot ring holds a single item: the id cannot change, so a perfectly live run was
    # refused with a diagnosis naming the wrong cause. Measured on br-20260917-025609-d24a with
    # `scripts/er-pad-reaches.py`: the game polled `XInputGetState` 31 times, was handed the
    # injected D-pad Down mask on 20 of them (`masks={'0x2': 20}`), and the slot stayed on
    # 0x40000070 -- which was already the finger this run wanted.
    #
    # So liveness is measured where it is observable: the mask the game is handed. Cycling is then
    # only about reaching the wanted item, and a ring of one needs no cycling at all.
    reaches = sess.create_script((REPO / "scripts/frida/pad-reaches-the-game.js").read_text())
    reaches.load()
    reaches.exports_sync.reset()
    tap(PAD_DOWN)
    arrival = reaches.exports_sync.report()
    print(f"pad: polls={arrival['polls']} masks={arrival['masks']}", flush=True)
    if arrival["polls"] == 0:
        print("refusing to confirm: the game is not polling XInputGetState at all")
        return 4
    if arrival["nonZeroMasks"] == 0:
        print("refusing to confirm: the game polls but never receives the injected mask -- "
              "the injector is the broken half, not window focus")
        return 4
    # The hook being installed is not the hook running. CanUseGoods is reached about 200 times a
    # second, so a zero here after a pad tap means the override is answering nothing.
    live = gate.exports_sync.report()
    print(f"gate live: calls={live['calls']} forced={live['forced']} "
          f"lastGoods={live['lastGoods']}", flush=True)
    if live["calls"] == 0:
        print("refusing to confirm: the CanUseGoods override has not been reached")
        return 12
    # Which finger this run is aiming at. The bounds prompt is the same for every one of them, so
    # the choice is only about what the character is carrying -- and a character carrying the
    # Bloody Finger and not the Festering one used to fail here with a blank refusal that named
    # neither the item it wanted nor the ones it walked past.
    wanted = args.finger
    # Named apart from the `seen` event list above on purpose: rebinding that name here would
    # point `on_message`'s closure at this list and silently throw every recorded event away.
    slots_walked: list[int] = []
    for _ in range(20):
        now = cycle.exports_sync.selected()["id"]
        if now not in slots_walked:
            slots_walked.append(now)
        if now == wanted:
            break
        tap(PAD_DOWN)
    if cycle.exports_sync.selected()["id"] != wanted:
        print(
            f"refusing to confirm: the cursor never reached {wanted:#x} -- the quick slots hold "
            f"{[hex(x) for x in slots_walked]}. Pass --finger with one of those, or put the finger "
            "want in a quick slot.",
        )
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
    # The highlight cannot be read: popupState+0x1a4 reports 0 for both rows and nothing in 2 KB
    # of that struct moves on a D-pad or stick press. What can be read is the answer the game took,
    # after the fact -- 1 for Nearby only, 2 for Both near and far. So the move is best effort and
    # the answer is the proof; a run that aimed right and was answered 1 says so instead of
    # claiming the row it wanted.
    if args.row == "right":
        tap(PAD_RIGHT)
    if args.redirect:
        armed = redirect.exports_sync.arm(args.redirect, 2 if args.row == "right" else 1)
        print(f"redirect armed: {armed}", flush=True)
    tap(PAD_A, down=0.4, up=0.3)
    settle()

    answered = [e for e in seen if e.get("kind") == "answer"]
    print("row the game read: " + (str(answered[-1]) if answered else "NOT CAPTURED"), flush=True)
    print("consumer: " + str(consumer.exports_sync.report()["confirmVirtual"]), flush=True)
    print("ersc actions: " + (
        str(actions.exports_sync.report()["counts"]) if actions is not None
        else "not traced -- pass --ersc-trace, and expect the DLL's own invade drive to be "
             "refused while it is on"), flush=True)
    print("finger after: " + str(oracle.exports_sync.variant()["active"]), flush=True)
    report = redirect.exports_sync.report()
    print("redirect: fired=" + str(report["fired"]) + " " + str(report["log"]), flush=True)
    wanted = 2 if args.row == "right" else 1
    got = answered[-1]["result"] if answered else None
    verdict = ("the answer matched the row aimed at"
               if got == wanted else
               f"THE ROW MOVE DID NOT TAKE -- aimed at {wanted}, the game was answered {got}")
    print("VERDICT: " + verdict, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
