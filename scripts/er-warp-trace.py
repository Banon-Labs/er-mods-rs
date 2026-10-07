#!/usr/bin/env python3
"""Warp to one point and print the raw height trace, to tell a bad point from a bad harness.

`scripts/er-warp-sweep.py` labelled 14 of its first 64 points `fell`, and every one of them
dropped from its arrival height to roughly `y = 0` -- 752 from 755, 1288 from 1295, 1696 from
1699. A hole in the floor drops the player to the next surface, not to the world origin, so that
signature reads as the map unloading underneath them rather than as a defect in the spawn point.

This tool settles the question by printing the whole trace instead of a verdict: warp once, then
sample position, ground and hp for a long dwell and show every sample. A point that is genuinely
unsupported falls immediately and keeps falling. A streaming artifact stands first, then drops.

Usage:
    uv run --with frida python3 scripts/er-warp-trace.py m60_39_50_00 -42.0 755.0 11.0 --dwell 40
    python3 scripts/er-warp-trace.py --selftest
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import queue
import sys
import time

ENDPOINT = os.environ.get("ER_FRIDA_ENDPOINT", "127.0.0.1:27042")
AGENT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frida", "warp-to-point.js")
SWEEP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "er-warp-sweep.py")


def load_sweep():
    """`er-warp-sweep.py` as a module, for its `VitalsStream`; the hyphen rules out `import`."""
    spec = importlib.util.spec_from_file_location("er_warp_sweep", SWEEP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def block_id(name):
    import re

    if name.startswith("0x"):
        return int(name, 16)
    m = re.match(r"^m(\d{2})_(\d{2})_(\d{2})_(\d{2})$", name)
    if not m:
        raise ValueError(f"not a block name: {name}")
    a, b, r, i = (int(g) for g in m.groups())
    return (a << 24) | (b << 16) | (r << 8) | i


def shape(samples):
    """Describe the trace: when it first grounded, and when it first started falling."""
    first_ground = None
    fall_after_ground = None
    peak = None
    for t, v in samples:
        if v.get("player") is None:
            continue
        y = v["player"]["y"]
        peak = y if peak is None else max(peak, y)
        if v.get("onSolidGround"):
            if first_ground is None:
                first_ground = t
        elif first_ground is not None and fall_after_ground is None and peak - y > 5.0:
            fall_after_ground = t
    return first_ground, fall_after_ground


def selftest():
    failures = []

    def v(y, ground):
        return {"player": {"x": 0.0, "y": y, "z": 0.0}, "onSolidGround": ground, "hp": 500}

    # Stood the whole time: grounded early, never fell.
    g, f = shape([(0.0, v(10.0, True)), (1.0, v(10.0, True))])
    if g != 0.0 or f is not None:
        failures.append(f"stood: {g} {f}")
    # Never grounded: a point that is genuinely unsupported.
    g, f = shape([(0.0, v(10.0, False)), (1.0, v(-50.0, False))])
    if g is not None or f is not None:
        failures.append(f"never grounded: {g} {f}")
    # The artifact signature: grounded, then the floor goes away.
    g, f = shape([(0.0, v(10.0, True)), (1.0, v(10.0, True)), (2.0, v(-40.0, False))])
    if g != 0.0 or f != 2.0:
        failures.append(f"ground-then-fall: {g} {f}")
    if not os.path.exists(AGENT):
        failures.append("agent missing")
    if not hasattr(load_sweep(), "VitalsStream"):
        failures.append("er-warp-sweep.py has no VitalsStream")
    for x in failures:
        print(f"FAIL {x}")
    print("selftest: " + ("FAILED" if failures else "ok"))
    return 1 if failures else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("block", nargs="?")
    ap.add_argument("x", nargs="?", type=float)
    ap.add_argument("y", nargs="?", type=float)
    ap.add_argument("z", nargs="?", type=float)
    ap.add_argument("--dwell", type=float, default=40.0)
    ap.add_argument("--rate", type=float, default=4.0)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.block is None or args.x is None:
        ap.error("need block x y z")
    if args.rate <= 0:
        ap.error("--rate must be positive")

    import frida

    sweep = load_sweep()
    dev = frida.get_device_manager().add_remote_device(ENDPOINT)
    pid = [p.pid for p in dev.enumerate_processes() if p.name.lower() == "eldenring.exe"][0]
    session = dev.attach(pid)
    script = session.create_script(open(AGENT, encoding="utf-8").read())
    messages: queue.Queue = queue.Queue()
    script.on("message", lambda m, d: messages.put(m))
    script.load()
    # Samples come from the game's per-frame pass at `--rate`; while that pass is not running
    # (the load itself), each one is a direct read instead, and `src` says which.
    stream = sweep.VitalsStream(script, messages, 1.0 / args.rate)
    stream.start()

    script.exports_sync.warp(block_id(args.block), args.x, args.y, args.z, 0.0)
    print(f"warped to {args.block} ({args.x}, {args.y}, {args.z}); tracing {args.dwell}s")
    start = time.monotonic()
    samples = []
    while time.monotonic() - start < args.dwell:
        v = stream.next()
        t = round(time.monotonic() - start, 2)
        samples.append((t, v))
        p = v.get("player")
        print(
            f"  t={t:6.2f} block={v.get('block')} "
            f"y={('%9.2f' % p['y']) if p else '   -     '} "
            f"ground={v.get('onSolidGround')} hp={v.get('hp')} state={v.get('protocolState')} "
            f"src={v.get('source')}",
            flush=True,
        )
    for report in stream.warps:
        print(f"warp report: frame={report.get('frame')} kicked={report.get('kicked')}")
    g, f = shape(samples)
    print(f"\nfirst grounded at t={g}, started falling after grounding at t={f}")
    stream.stop()
    session.detach()
    return 0


if __name__ == "__main__":
    sys.exit(main())
