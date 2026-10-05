#!/usr/bin/env python3
"""Find virtual-call sites `call qword ptr [reg + slot]` in a flat ELDEN RING image, optionally
only those whose preceding bytes load the object from a given field offset.

The images are flat (file offset == RVA, VA = 0x140000000 + offset), so this is a byte scan, not a
disassembly. A hit is a candidate: the window filter only proves the field-offset bytes occur
shortly before the call, so read each hit's disassembly before believing it.

Usage
    python3 scripts/find-vcall-slot-sites.py --image eldenring-deobf-1.17.1.bin \
        --slot 0xe8 --slot 0xf0 --field 0x6a0 [--window 48]
"""

from __future__ import annotations

import argparse
import os
import re
import sys

BASE = 0x140000000
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="eldenring-deobf-1.17.1.bin")
    ap.add_argument("--slot", action="append", required=True, help="vtable byte offset, hex ok")
    ap.add_argument("--field", default=None, help="object field offset that must precede the call")
    ap.add_argument("--window", type=int, default=48)
    a = ap.parse_args()

    path = a.image if os.path.isabs(a.image) else os.path.join(ROOT, a.image)
    with open(path, "rb") as f:
        data = f.read()

    field = None
    if a.field is not None:
        field = int(a.field, 0).to_bytes(4, "little")

    for slot_text in a.slot:
        slot = int(slot_text, 0)
        if slot < 0x80:
            # call [reg+disp8]: FF /2 with mod=01 -> modrm 0x50..0x57 (0x54 needs a SIB, skipped)
            pat = re.compile(rb"\xff[\x50-\x53\x55-\x57]" + re.escape(bytes([slot])))
        else:
            pat = re.compile(rb"\xff[\x90-\x93\x95-\x97]" + re.escape(slot.to_bytes(4, "little")))
        hits = []
        for m in pat.finditer(data):
            off = m.start()
            if field is not None:
                lo = max(0, off - a.window)
                if field not in data[lo:off]:
                    continue
            hits.append(off)
        print(f"slot {slot:#x}: {len(hits)} hit(s)")
        for off in hits:
            print(f"  {BASE + off:#x}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
