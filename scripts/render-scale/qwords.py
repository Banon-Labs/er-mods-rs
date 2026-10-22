#!/usr/bin/env python3
"""Dump qwords of a flat Elden Ring image around a VA, resolving pointers to strings.

Usage: qwords.py <image> <va> <count> [--before N]
"""
import struct
import sys

BASE = 0x140000000
data = open(sys.argv[1], "rb").read()
va = int(sys.argv[2], 16)
count = int(sys.argv[3])
before = int(sys.argv[5]) if len(sys.argv) > 5 and sys.argv[4] == "--before" else 0


def string_at(off):
    if not 0 <= off < len(data):
        return None
    chunk = data[off : off + 160]
    if len(chunk) > 8 and chunk[1] == 0 and 0x20 <= chunk[0] < 0x7F:
        out = []
        for i in range(0, len(chunk) - 1, 2):
            c = chunk[i] | chunk[i + 1] << 8
            if c == 0:
                break
            if not 0x20 <= c < 0x7F:
                return None
            out.append(chr(c))
        return "W'" + "".join(out) + "'" if len(out) >= 3 else None
    out = []
    for b in chunk:
        if b == 0:
            break
        if not 0x20 <= b < 0x7F:
            return None
        out.append(chr(b))
    return "A'" + "".join(out) + "'" if len(out) >= 3 else None


start = va - before * 8
for i in range(count + before):
    a = start + i * 8
    q = struct.unpack_from("<Q", data, a - BASE)[0]
    note = ""
    if BASE <= q < BASE + len(data):
        s = string_at(q - BASE)
        note = s or ""
    lo, hi = struct.unpack_from("<ii", data, a - BASE)
    f0, f1 = struct.unpack_from("<ff", data, a - BASE)
    print(f"{a:#x}: {q:#018x}  i32({lo},{hi}) f32({f0:.6g},{f1:.6g}) {note}")
