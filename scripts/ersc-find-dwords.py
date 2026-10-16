#!/usr/bin/env python3
"""Find little-endian dword constants in ersc.dll images and name their section.

Usage: python3 scripts/ersc-find-dwords.py <image> <value> [<value> ...]

`<image>` is either an on-disk PE (file offsets are mapped to rvas through the
section table) or a flat runtime dump such as
`vendor-archive/seamless/ersc-2.0.1.runtime.bin`, where file offset == rva.
A file ending in `.bin` is treated as flat.
"""

import struct
import sys


def sections(data):
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    count = struct.unpack_from("<H", data, pe + 6)[0]
    opt = struct.unpack_from("<H", data, pe + 20)[0]
    out = []
    for i in range(count):
        o = pe + 24 + opt + 40 * i
        name = data[o : o + 8].rstrip(b"\0").decode(errors="replace")
        vsize, va, rsize, rptr = struct.unpack_from("<IIII", data, o + 8)
        out.append((name, va, vsize, rptr, rsize))
    return out


def main():
    path = sys.argv[1]
    values = [int(v, 0) for v in sys.argv[2:]]
    data = open(path, "rb").read()
    flat = path.endswith(".bin")
    secs = sections(data)
    for name, va, vsize, rptr, rsize in secs:
        print(f"section {name:8} rva {va:#x} vsize {vsize:#x} raw {rptr:#x} rsize {rsize:#x}")

    def rva_of(off):
        if flat:
            return off
        for _, va, _, rptr, rsize in secs:
            if rptr <= off < rptr + rsize:
                return va + off - rptr
        return None

    def sec_of(rva):
        for name, va, vsize, _, rsize in secs:
            if rva is not None and va <= rva < va + max(vsize, rsize):
                return name
        return "?"

    for value in values:
        needle = struct.pack("<I", value)
        hits = []
        i = data.find(needle)
        while i != -1:
            hits.append(i)
            i = data.find(needle, i + 1)
        rendered = []
        for off in hits:
            rva = rva_of(off)
            rendered.append(f"{rva:#x}:{sec_of(rva)}" if rva is not None else f"off{off:#x}")
        print(f"{value} ({value:#x}) hits={len(hits)} {' '.join(rendered)}")


if __name__ == "__main__":
    main()
