#!/usr/bin/env python3
"""List asset models that exist in the unpacked game but no map places.

An asset nobody places is a safe carrier for new geometry: overriding its geombnd changes nothing
the player already sees, and the game still has its param row, so a runtime spawn of that name
behaves like any other asset. Placements come from every unpacked MSB `Part/Asset/*.xml`
`<ModelName>`; models from every `aegNNN_NNN-geombnd-dcx` directory.

Usage: er-asset-placement-census.py [--prefix AEG004] [--root <sharded>]
"""

import argparse
import os
import re

DEFAULT_ROOT = os.path.join(
    os.environ.get("ER_EXTRACT_ROOT", os.path.expanduser("~/er-extract")),
    "LOOK_HERE_WITCHY_RECURSIVE_20260713/sharded",
)
MODEL = re.compile(r"<ModelName>([^<]+)</ModelName>")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=DEFAULT_ROOT)
    parser.add_argument("--prefix", default="AEG")
    args = parser.parse_args()

    placed = {}
    for directory, _, files in os.walk(os.path.join(args.root, "map")):
        if not directory.endswith(os.path.join("Part", "Asset")):
            continue
        msb = directory.split(os.sep)[-3]
        for name in files:
            with open(os.path.join(directory, name), encoding="utf-8", errors="replace") as handle:
                match = MODEL.search(handle.read())
            if match:
                placed.setdefault(match.group(1).upper(), set()).add(msb)

    models = set()
    for directory, dirs, _ in os.walk(os.path.join(args.root, "asset")):
        for name in dirs:
            match = re.fullmatch(r"(aeg\d{3}_\d{3})-geombnd-dcx", name)
            if match:
                models.add(match.group(1).upper())

    wanted = sorted(m for m in models if m.startswith(args.prefix.upper()))
    unplaced = [m for m in wanted if m not in placed]
    print(f"models {len(wanted)}, placed {len(wanted) - len(unplaced)}, unplaced {len(unplaced)}")
    for model in unplaced:
        print(model)


if __name__ == "__main__":
    main()
