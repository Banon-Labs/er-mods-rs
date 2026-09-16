#!/usr/bin/env python3
"""Use a goods item in a live Elden Ring, with none of this repo's game DLLs required.

Standing directive, 2026-09-16: invasion experiments run from Frida, not from
`er_invasion_warp.dll`, because that module is the thing under suspicion and reaching for it to
set up an experiment re-contaminates the experiment it was withheld from. It is also the only
condition under which `ersc.dll` may be hooked at all -- the module byte-checks those prologues
before every call, and an `Interceptor` trampoline disarms it.

The one thing this still borrows is the pad press, from `er_quickload.dll`, which is not under
suspicion and is present in a `--without er-invasion-warp` run. Nothing here calls into
`er_invasion_warp.dll`.

    python3 scripts/er-frida-up.py
    uv run --with frida python3 scripts/er-drive-item.py --goods 111
    uv run --with frida python3 scripts/er-drive-item.py --selftest
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

_spec = importlib.util.spec_from_file_location("er_frida_watch", HERE / "er-frida-watch.py")
_watch = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_watch)

AGENT = HERE / "frida" / "lynchpin-item-drive.js"
FESTERING_BLOODY_FINGER = 111
CHALLENGERS_LYNCHPIN = 0x7FDE63
VANILLA_FINGERS = [102, 111, 112]
PAD_A = 0x1000

# Bounded waits, well inside the repo's 30s cap, spent in an external `sleep` so this process owns
# no timer of its own.
SETTLE_SECONDS = 2.5
PRESS_HOLD_SECONDS = 0.5
POLL_SECONDS = 1.0
SETTLE_TIMEOUT_SECONDS = 10
# Enough polls to cover a cold boot to the world, at one second each.
WORLD_POLLS = 240


def settle(seconds: float) -> None:
    """Wait, bounded, without this process owning a timer."""
    subprocess.run(["sleep", str(seconds)], check=False, timeout=SETTLE_TIMEOUT_SECONDS)


def selftest() -> int:
    """The agent has to carry the addresses and exports this driver calls."""
    text = AGENT.read_text(encoding="utf-8")
    for needed in (
        "0x143d6f820",  # CSMenuMan on 1.17.1
        "0x140657410",  # GetSelectedQuickSlotItemId on 1.17.1
        "0x14024c560",  # GetItemInventoryIdx, which takes a pointer to the tagged id, never the id itself
        "0x143d61f98",  # GameDataMan on 1.17.1, the root of the equip inventory chain
        "0x140d3b5b0",  # EquipParamGoods::GetEntry
    ):
        assert needed in text, f"the agent must carry {needed}"
    for export in ("ready", "enableFingers", "pin", "state", "unpin"):
        assert f"{export} (" in text or f"{export} (" in text, f"the agent must export {export}"
    assert "setTimeout" not in text and "setInterval" not in text, "no timers in the agent"
    # A mention in prose is fine; resolving the module is the dependency that would matter.
    assert "findModuleByName('er_invasion_warp" not in text, (
        "the agent must not resolve the module under test"
    )
    print("selftest ok -- agent carries the 1.17.1 addresses, exports the four calls, no timers")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--goods", type=lambda v: int(v, 0), default=FESTERING_BLOODY_FINGER)
    parser.add_argument("--attempts", type=int, default=3)
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

    session = dev.attach(pid)
    drive = session.create_script(AGENT.read_text(encoding="utf-8"))
    drive.on("message", lambda message, _data: print("AGENT", message.get("payload", message)))
    drive.load()

    # The world has to exist before any of this means anything. Bounded, and it reports the
    # reason it is still waiting rather than looping mutely.
    for _ in range(WORLD_POLLS):
        state = drive.exports_sync.ready()
        if state.get("ready"):
            break
        settle(POLL_SECONDS)
    else:
        print(f"world never came up: {json.dumps(state)}", file=sys.stderr)
        return 2
    print("world ready", flush=True)

    print("fingers:", json.dumps(drive.exports_sync.enable_fingers(VANILLA_FINGERS)), flush=True)

    pad = session.create_script(
        "const m = Process.findModuleByName('er_quickload.dll');\n"
        "const h = new NativeFunction(m.getExportByName('er_quickload_hold_xinput_pad'),"
        " 'void', ['uint16','int16','int16']);\n"
        "rpc.exports = { press (mask) { h(mask, 0, 0); }, release () { h(0, 0, 0); } };\n"
    )
    pad.load()

    latched = False
    for attempt in range(1, args.attempts + 1):
        pinned = drive.exports_sync.pin(args.goods)
        print(f"--- attempt {attempt}: pin {json.dumps(pinned)}", flush=True)
        if not pinned.get("ok"):
            continue
        # Read `ChrIns+0x160` ahead of the press. Without this baseline an already-latched field
        # reads as a success the drive did not cause -- and it stays latched after any earlier
        # use, so that is the normal state of a process that has been driven once.
        before = drive.exports_sync.state().get("queuedUseItem")
        print(f"    baseline queuedUseItem {before}", flush=True)
        if before == pinned["itemId"]:
            print("    already latched on this item -- relaunch; this attempt cannot prove anything",
                  flush=True)
            drive.exports_sync.unpin()
            continue
        settle(SETTLE_SECONDS)
        pad.exports_sync.press(PAD_A)
        settle(PRESS_HOLD_SECONDS)
        pad.exports_sync.release()
        # The proof is `ChrIns+0x160` taking the pinned id -- that is the character accepting the
        # item. The use-state byte is not usable as the criterion here: it steps 0 -> 2 -> 0 inside
        # a frame or two, so a one-second poll misses it almost every time, and an earlier version
        # of this loop called a working drive a failure for exactly that reason.
        want = pinned["itemId"]
        seen = set()
        for _ in range(10):
            settle(POLL_SECONDS)
            state = drive.exports_sync.state()
            seen.add(state.get("state"))
            print("   ", json.dumps(state), flush=True)
            if state.get("queuedUseItem") == want:
                latched = True
                break
        drive.exports_sync.unpin()
        print(f"    use-state bytes seen: {sorted(s for s in seen if s is not None)}", flush=True)
        if latched:
            print(f"ACCEPTED -- ChrIns+0x160 went {before} -> {want}, so the character took "
                  f"the item, driven with no er-invasion-warp in the process", flush=True)
            return 0
        # `ChrIns+0x160` does not clear after a use, so the game refuses the next one and every
        # later attempt in this process is contaminated. Relaunch between drives.
        print("    (a second attempt in one run is contaminated: +0x160 never clears)", flush=True)

    print("NOT ACCEPTED in any attempt -- this run is void, not negative", flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
