#!/usr/bin/env python3
"""Warp the running game to one block-local coordinate, once, and report what the engine did.

Pairs with `scripts/frida/warp-to-point.js`. The server has to be up already
(`python3 scripts/er-frida-up.py`); this attaches, arms a one-shot hook on the per-frame
heads-up-display pass, queues the destination, waits for the hook to report, and detaches.

A block name is accepted instead of a raw id, because the id is the name's four fields in
reverse byte order and hand-assembling it is how a warp lands in the wrong area:
`m30_10_00_00` is `0x1e0a0000`.

Usage:
    uv run --with frida python3 scripts/er-warp-to.py m30_10_00_00 -105.59 751.49 167.70 --yaw-deg 152.55
    uv run --with frida python3 scripts/er-warp-to.py --where
    python3 scripts/er-warp-to.py --selftest
"""

from __future__ import annotations

import argparse
import json
import math
import os
import queue
import re
import sys
import time

ENDPOINT = os.environ.get("ER_FRIDA_ENDPOINT", "127.0.0.1:27042")
AGENT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frida", "warp-to-point.js")
BLOCK_NAME = re.compile(r"^m(\d{2})_(\d{2})_(\d{2})_(\d{2})$")
# Ceiling on any single blocking wait for an agent message.
MAX_WAIT_SECONDS = 30.0
# How long to wait for the frame after the kick before detaching anyway. Bounded by the same
# ceiling; the pass may not run while the load it started is in progress.
KICK_CONSUMED_WAIT_SECONDS = 30.0


def wait_for_message(messages: queue.Queue, kind: str, seconds: float):
    """Block on the agent's message queue until a `send()` of type `kind` arrives.

    Returns the payload, or `None` once `seconds` (capped at `MAX_WAIT_SECONDS`) have passed.
    Agent errors are printed as they arrive rather than swallowed, since a script exception
    is the usual reason the wanted message never comes.
    """
    deadline = time.monotonic() + min(seconds, MAX_WAIT_SECONDS)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            message = messages.get(timeout=remaining)
        except queue.Empty:
            return None
        if message.get("type") == "error":
            print(f"agent error: {message.get('description')}", file=sys.stderr)
            continue
        if message.get("type") != "send":
            continue
        payload = message.get("payload") or {}
        if payload.get("type") == kind:
            return payload


def block_id(name: str) -> int:
    """`m30_10_00_00` -> `0x1e0a0000`.

    The in-memory `BlockId` holds area, block, region and index in the order the name writes
    them, which is the reverse of the order the `.aip` file stores them in. Checked against a
    measured live value: `m61_53_41_00` reads `0x3d352900`.
    """
    if name.startswith("0x"):
        return int(name, 16)
    m = BLOCK_NAME.match(name)
    if not m:
        raise ValueError(f"not a block name or 0x id: {name}")
    area, block, region, index = (int(g) for g in m.groups())
    for field, value in (("area", area), ("block", block), ("region", region), ("index", index)):
        if not 0 <= value <= 0xFF:
            raise ValueError(f"{field} out of range in {name}")
    return (area << 24) | (block << 16) | (region << 8) | index


def selftest() -> int:
    failures = []
    cases = {
        "m61_53_41_00": 0x3D352900,
        "m30_10_00_00": 0x1E0A0000,
        "m12_02_00_00": 0x0C020000,
        "m60_37_52_00": 0x3C253400,
        "0xdeadbeef": 0xDEADBEEF,
    }
    for name, want in cases.items():
        got = block_id(name)
        if got != want:
            failures.append(f"block_id({name}) = {got:#x}, want {want:#x}")
    for bad in ("m30_10_00", "stormveil", "m30_10_00_00_00"):
        try:
            block_id(bad)
        except ValueError:
            continue
        failures.append(f"block_id({bad}) should have refused")
    if not os.path.exists(AGENT):
        failures.append(f"agent missing: {AGENT}")

    # The message wait: skips other traffic, surfaces the wanted payload, and gives up when the
    # queue stays empty instead of blocking forever.
    q: queue.Queue = queue.Queue()
    q.put({"type": "log", "payload": "noise"})
    q.put({"type": "send", "payload": {"type": "vitals"}})
    q.put({"type": "send", "payload": {"type": "warp", "report": {"kicked": True}}})
    got = wait_for_message(q, "warp", 1.0)
    if not got or not got["report"].get("kicked"):
        failures.append(f"wait_for_message did not return the warp payload: {got}")
    if wait_for_message(q, "warp", 0.05) is not None:
        failures.append("wait_for_message returned a payload from an empty queue")
    for f in failures:
        print(f"FAIL {f}")
    print("selftest: " + ("FAILED" if failures else "ok"))
    return 1 if failures else 0


def connect():
    import frida

    dev = frida.get_device_manager().add_remote_device(ENDPOINT)
    pid = None
    for proc in dev.enumerate_processes():
        if proc.name.lower() == "eldenring.exe":
            pid = proc.pid
            break
    if pid is None:
        sys.exit(f"eldenring.exe not visible through {ENDPOINT}; is the game running?")
    return dev, dev.attach(pid), pid


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("block", nargs="?", help="block name such as m30_10_00_00, or 0x1e0a0000")
    ap.add_argument("x", nargs="?", type=float)
    ap.add_argument("y", nargs="?", type=float)
    ap.add_argument("z", nargs="?", type=float)
    ap.add_argument("--yaw-deg", type=float, default=0.0, help="MSB rotations are degrees")
    ap.add_argument("--yaw-rad", type=float, help="aip yaw is already radians")
    ap.add_argument("--where", action="store_true", help="report the current block and exit")
    ap.add_argument(
        "--probe",
        action="store_true",
        help="read the player's settled physics position, and where the given point lands in "
        "that same space, without warping",
    )
    ap.add_argument(
        "--wait",
        type=float,
        default=12.0,
        help=f"seconds to wait for the hook (capped at {MAX_WAIT_SECONDS:g})",
    )
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    messages: queue.Queue = queue.Queue()
    dev, session, pid = connect()
    script = session.create_script(open(AGENT, encoding="utf-8").read())
    script.on("message", lambda m, d: messages.put(m))
    script.load()

    if args.where:
        print(json.dumps(script.exports_sync.where(), indent=1))
        session.detach()
        return 0

    if args.block is None or args.x is None or args.y is None or args.z is None:
        session.detach()
        ap.error("need block x y z (or --where)")

    ready = script.exports_sync.ready()
    print(f"attached pid={pid} base={ready['base']} block={ready['block']} spawnFlag={ready['spawnFlag']}")

    yaw = args.yaw_rad if args.yaw_rad is not None else math.radians(args.yaw_deg)
    bid = block_id(args.block)
    if args.probe:
        print(f"probing {args.block} ({bid:#010x}) ({args.x}, {args.y}, {args.z})")
        script.exports_sync.probe(bid, args.x, args.y, args.z)
    else:
        print(
            f"warping to {args.block} ({bid:#010x}) "
            f"({args.x}, {args.y}, {args.z}) yaw={yaw:.4f} rad"
        )
        script.exports_sync.warp(bid, args.x, args.y, args.z, yaw)

    warp = wait_for_message(messages, "warp", args.wait)
    report = warp["report"] if warp is not None else None

    if report is None:
        print("the per-frame hook never fired -- the game is probably not in a world yet")
        session.detach()
        return 2

    print(json.dumps(report, indent=1))
    if args.probe:
        player = report.get("player")
        target = report.get("targetInPhysics")
        if player and target:
            dy = player["y"] - target["y"]
            flat = math.dist(
                (player["x"], player["z"]), (target["x"], target["z"])
            )
            print(f"settled {dy:+.2f} m in y and {flat:.2f} m horizontally from the request")
        session.detach()
        return 0
    # Let the stage kick be consumed before the trampoline is removed from under the thread: the
    # agent reports the first frame after the one that kicked, which means the main thread has
    # left that hook invocation and run the stage machine once more.
    if report.get("kicked"):
        after = wait_for_message(messages, "frame-after-kick", KICK_CONSUMED_WAIT_SECONDS)
        if after is None:
            print(
                f"no frame after the kick within {KICK_CONSUMED_WAIT_SECONDS:g}s; detaching anyway"
            )
        else:
            print(f"kick on frame {after['kickedOnFrame']}, next frame {after['frame']} ran")
    session.detach()
    return 0 if report.get("kicked") else 3


if __name__ == "__main__":
    sys.exit(main())
