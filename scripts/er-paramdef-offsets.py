#!/usr/bin/env python3
"""Print the byte offset (and bit, for bitfields) of every field of a Smithbox paramdef.

    er-paramdef-offsets.py <Defs/Xxx.xml> [name-regex]

So a live param row read out of game memory (a Frida dump, /proc/<pid>/mem) can be decoded
field by field without the .NET bridge. Bitfields pack low bit first into a run of their base
type; a non-bitfield or a different base type ends the run.
"""

import re
import sys

SIZES = {"u8": 1, "s8": 1, "u16": 2, "s16": 2, "u32": 4, "s32": 4, "f32": 4, "f64": 8,
         "dummy8": 1, "fixstr": 1, "fixstrW": 2, "angle32": 4, "b32": 4}


def fields(text):
    off = 0
    run = None  # (type, start, bits used)
    # Smithbox writes `<Field Def="u8 name:1 = 0">`; older dumps `<Def>u8 name:1</Def>`.
    defs = re.findall(r'<Field Def="([^"]*)"', text) or re.findall(r"<Def>(.*?)</Def>", text, re.S)
    for d in defs:
        m = re.match(r"\s*(\w+)\s+(\w+)(?:\s*:\s*(\d+))?(?:\[(\d+)\])?", d)
        ty, name, bits, count = m.groups()
        size = SIZES[ty]
        if bits:
            if run is None or run[0] != ty or run[2] + int(bits) > size * 8:
                run = [ty, off, 0]
                off += size
            yield run[1], run[2], int(bits), ty, name
            run[2] += int(bits)
            continue
        run = None
        yield off, None, None, ty, name + (f"[{count}]" if count else "")
        off += size * (int(count) if count else 1)
    yield off, None, None, "end", "(row size)"


def main():
    path = sys.argv[1]
    pattern = re.compile(sys.argv[2], re.I) if len(sys.argv) > 2 else None
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    for off, bit, width, ty, name in fields(text):
        if pattern and not pattern.search(name) and ty != "end":
            continue
        where = f"0x{off:03x}" + (f".{bit}" if bit is not None else "  ")
        print(f"{where}  {ty:7} {name}" + (f" :{width}" if width else ""))


if __name__ == "__main__":
    main()
