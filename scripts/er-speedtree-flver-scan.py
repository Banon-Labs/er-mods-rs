#!/usr/bin/env python3
"""List the asset FLVERs that use SpeedTree materials, and which ones.

A FLVER stores each material's mtd/matbin path as UTF-16, so a byte scan for
`SpeedTree_` names every tree model without parsing the format. Output is one
line per FLVER: `<path relative to root> | <material names>`.
"""

import argparse
import os
import re
import sys

DEFAULT_ROOT = os.path.join(
    os.environ.get("ER_EXTRACT_ROOT", os.path.expanduser("~/er-extract")),
    "LOOK_HERE_WITCHY_RECURSIVE_20260713/sharded/asset/aeg",
)
PATTERN = re.compile("SpeedTree_".encode("utf-16-le") + rb"((?:[\x20-\x7e]\x00){1,48})")


def scan(root):
    for directory, _, files in os.walk(root):
        for name in sorted(files):
            if not name.endswith(".flver"):
                continue
            path = os.path.join(directory, name)
            with open(path, "rb") as handle:
                data = handle.read()
            found = {m.group(1).decode("utf-16-le") for m in PATTERN.finditer(data)}
            if found:
                yield os.path.relpath(path, root), sorted(found)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=DEFAULT_ROOT)
    args = parser.parse_args()
    count = 0
    for rel, materials in scan(args.root):
        print(f"{rel} | {' '.join(materials)}")
        count += 1
    print(f"flvers with SpeedTree materials: {count}", file=sys.stderr)


if __name__ == "__main__":
    main()
