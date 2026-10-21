#!/usr/bin/env python3
"""List asset models whose FLVER bounding box is a standing slab: wall-shaped.

A slab here is thin on x or z (under `--thin` metres), long on the other horizontal axis and tall
on y. Such a model can be scaled non-uniformly into a visible wall of any size, which is what an
arena built from invisible AEG099_002 colliders needs drawn over it. Bounds are read from the
FLVER2 header (min at 0x28, max at 0x34), so nothing is parsed past the first 64 bytes; LOD
variants (`AEG004_506_1`) are skipped.

Usage: er-asset-slab-census.py [--prefix AEG004] [--thin 1.2] [--root <sharded/asset/aeg>]
"""

import argparse
import glob
import os
import struct

DEFAULT_ROOT = os.path.join(
    os.environ.get("ER_EXTRACT_ROOT", os.path.expanduser("~/er-extract")),
    "LOOK_HERE_WITCHY_RECURSIVE_20260713/sharded/asset/aeg",
)


def bounds(path):
    with open(path, "rb") as f:
        head = f.read(0x40)
    if head[:6] != b"FLVER\0" or head[6:8] != b"L\0":
        return None
    return struct.unpack_from("<3f", head, 0x28), struct.unpack_from("<3f", head, 0x34)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=DEFAULT_ROOT)
    parser.add_argument("--prefix", default="")
    parser.add_argument("--thin", type=float, default=1.2)
    # A zero-thickness box is a plane (a decal or billboard), which reads as nothing edge-on.
    parser.add_argument("--min-thin", type=float, default=0.2)
    parser.add_argument("--long", type=float, default=4.0)
    parser.add_argument("--tall", type=float, default=2.5)
    args = parser.parse_args()
    rows = []
    for path in glob.glob(os.path.join(args.root, "*", "*", "*-geombnd-dcx", "*.flver")):
        name = os.path.basename(path)[:-6]
        if name.count("_") != 1 or not name.upper().startswith(args.prefix.upper()):
            continue
        b = bounds(path)
        if b is None:
            continue
        lo, hi = b
        d = [hi[i] - lo[i] for i in range(3)]
        if not all(0 <= x < 500 for x in d):
            continue
        thin, long_ = min(d[0], d[2]), max(d[0], d[2])
        if args.min_thin <= thin < args.thin and long_ > args.long and d[1] > args.tall:
            rows.append((long_ / max(thin, 0.01), name, d, lo))
    rows.sort(reverse=True)
    for _, name, d, lo in rows:
        print(f"{name:14} size={[round(x, 2) for x in d]} min={[round(x, 2) for x in lo]}")


if __name__ == "__main__":
    main()
