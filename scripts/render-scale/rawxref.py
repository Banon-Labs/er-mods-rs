#!/usr/bin/env python3
"""Find raw references to VAs in a flat Elden Ring image (file offset == RVA, base 0x140000000).

Reports every RIP-relative disp32 whose target is one of the VAs (the byte before it must be a
ModRM with mod=00 rm=101; the instruction may carry no immediate, an imm8 or an imm32 after the
disp, so each hit is a candidate the caller confirms by disassembly), and every little-endian
qword equal to one of the VAs.

Run under uv: uv run --with numpy python3 rawxref.py <image> <va> [<va> ...]
"""
import struct
import sys

import numpy as np

BASE = 0x140000000
data = open(sys.argv[1], "rb").read()
targets = [int(a, 16) for a in sys.argv[2:]]
raw = np.frombuffer(data, dtype=np.uint8)

for t in targets:
    needle = struct.pack("<Q", t)
    i = data.find(needle)
    while i != -1:
        print(f"ptr  {BASE + i:#x} -> {t:#x}")
        i = data.find(needle, i + 1)

for o in range(4):
    count = (len(data) - o) // 4
    d = np.frombuffer(data, dtype="<i4", count=count, offset=o).astype(np.int64)
    p = o + 4 * np.arange(count, dtype=np.int64)
    prev = np.zeros(count, dtype=np.uint8)
    if o == 0:
        prev[1:] = raw[3 : 3 + 4 * (count - 1) : 4]
    else:
        prev[:] = raw[o - 1 : o - 1 + 4 * count : 4][:count]
    modrm_ok = (prev & 0xC7) == 0x05
    end = p + 4 + d
    for t in targets:
        rva = t - BASE
        for k in (0, 1, 4):
            for h in np.nonzero(modrm_ok & (end + k == rva))[0]:
                print(f"rip  disp@{BASE + int(p[h]):#x} imm{k} -> {t:#x}")
