#!/usr/bin/env python3
"""Make Seamless hand over its option-menu object, by using the item that opens its menu.

The near+far redirect needs one pointer: the object `ersc+0x25850` takes as its first argument,
whose `+0x58` is Seamless's session. Every attempt to find it by scanning `ersc.dll`'s writable data
returned a different address, and calling the invade action on one of those parked a thread inside a
lock helper that acquires without a timeout.

So this does not look for it. The Challenger's Lynchpin is already in quick slot 1 on the live
character, and using it makes Seamless open its own option menu through the game's
`CS::CSMenuMan::OpenConversationChoicesMenu`, where the object is in `r14 - 0x120`. The drive is
closed-loop: the cursor is moved one quick-item at a time and the game's own getter says where it
landed, so nothing is assumed about how many presses are needed.

    python3 scripts/er-frida-up.py
    uv run --with frida python3 scripts/er-capture-seamless-owner.py
    python3 scripts/er-capture-seamless-owner.py --selftest

The menu is backed out of with pad B rather than answered: the point is the pointer, and answering
Seamless's own Invade row would start the search this is meant to hand to the vanilla finger.
"""
from __future__ import annotations

import argparse
import pathlib
import queue
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from er_pad_frames import PAD_B, PAD_DOWN, PAD_X, Pad, wait_for  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
AGENT = REPO / "scripts" / "frida" / "capture-seamless-owner.js"
CHALLENGERS_LYNCHPIN = 0x407FDE63

# Ten quick-item slots, so a full cycle cannot need more than ten presses. One spare press covers a
# cursor that starts on an empty slot the cycle skips.
MAX_CYCLE_PRESSES = 11
# The menu takes a moment to open after the use press. Frames, not seconds, for the same reason
# every other wait in this repo is: a hold in seconds is a different number of frames every run.
MENU_SETTLE_FRAMES = 120


def attach(frida):
    device = frida.get_device_manager().add_remote_device("127.0.0.1:27042")
    procs = [p for p in device.enumerate_processes() if "eldenring" in p.name.lower()]
    if not procs:
        raise SystemExit(
            "no eldenring.exe visible to the frida server -- run scripts/er-frida-up.py first; "
            "an open port is not proof of a working server"
        )
    return device.attach(procs[0].pid), procs[0].pid


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        source = AGENT.read_text(encoding="utf-8")
        checks = [
            ("the agent exists", AGENT.is_file()),
            ("it reads the object out of r14", "sub(OSM_FROM_R14)" in source),
            ("it follows an Arxan stub", "function follow" in source),
            ("it carries the Steam oracle", "RequestLobbyList" in source),
            ("the cycle is bounded", MAX_CYCLE_PRESSES <= 16),
        ]
        bad = sum(0 if ok else 1 for _, ok in checks)
        for label, ok in checks:
            print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
        print("selftest: ok" if not bad else f"selftest: {bad} check(s) failed")
        return 0 if not bad else 1

    import frida

    session, pid = attach(frida)
    events: queue.Queue = queue.Queue()
    script = session.create_script(AGENT.read_text(encoding="utf-8"))
    script.on(
        "message",
        lambda message, _data: events.put(message.get("payload", message))
        if message.get("type") == "send"
        else None,
    )
    script.load()
    pad = Pad(session)
    print(f"attached to pid {pid}", flush=True)

    selected = script.exports_sync.selected()
    print(f"quick item under the cursor: {selected}", flush=True)
    if selected is None:
        print("no player -- the character is not in a world")
        session.detach()
        return 2

    presses = 0
    while selected["id"] != CHALLENGERS_LYNCHPIN and presses < MAX_CYCLE_PRESSES:
        pad.tap(PAD_DOWN)
        presses += 1
        selected = script.exports_sync.selected()
        print(f"  cycle {presses}: {selected['hex']}", flush=True)
    if selected["id"] != CHALLENGERS_LYNCHPIN:
        print(
            f"the Challenger's Lynchpin is not in the quick-item cycle -- "
            f"{presses} presses ended on {selected['hex']}"
        )
        session.detach()
        return 3

    print("using the Lynchpin to make Seamless open its own menu", flush=True)
    pad.tap(PAD_X)
    opened = wait_for(events, lambda e: "menu open" in str(e.get("line", "")))
    if opened is not None:
        print("  " + opened["line"], flush=True)
    pad.tap(PAD_B, hold_frames=MENU_SETTLE_FRAMES // 6)
    pad.tap(PAD_B)

    owner = script.exports_sync.owner()
    report = script.exports_sync.report()
    print("owner: " + str(owner), flush=True)
    print(f"menu opens seen: {report['opens']}  steam queries: {report['queries']}", flush=True)
    for line in report["log"][-12:]:
        print("  note: " + line, flush=True)

    if owner.get("ok"):
        print(
            "VERDICT: captured. Hand it to the redirect:\n"
            f"  uv run --with frida python3 scripts/er-drive-finger-bounds.py "
            f"--row right --redirect {owner['owner']}",
            flush=True,
        )
    else:
        print("VERDICT: no object captured -- " + str(owner.get("why")), flush=True)

    pad.release()
    session.detach()
    return 0 if owner.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
