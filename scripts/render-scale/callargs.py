#!/usr/bin/env python3
"""Print the immediate loaded into an argument register just before each call site.

Reads call-site VAs (one per line, first token) from a file, decodes the 0x40 bytes before each
call in a flat Elden Ring image with capstone (trying every start offset and keeping a decoding
that ends exactly on the call), and prints the last `mov <reg>, imm` for the named register.

Run under uv: uv run --with capstone python3 callargs.py <image> <sites file> <reg, e.g. edx>
"""
import sys

import capstone

BASE = 0x140000000
data = open(sys.argv[1], "rb").read()
sites = [int(line.split()[0], 16) for line in open(sys.argv[2]) if line.strip()]
want = sys.argv[3]
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
for site in sites:
    off = site - BASE
    best = None
    for back in range(0x40, 0, -1):
        insns = list(md.disasm(data[off - back : off + 5], site - back))
        if insns and insns[-1].address == site and insns[-1].mnemonic == "call":
            best = insns
            break
    if best is None:
        print(f"{site:#x}: undecoded")
        continue
    val = None
    for ins in best[:-1]:
        if ins.mnemonic == "mov" and ins.op_str.startswith(want + ","):
            val = ins.op_str.split(",", 1)[1].strip()
        elif ins.op_str.startswith(want + ",") or ins.op_str.startswith(want + " "):
            val = f"({ins.mnemonic} {ins.op_str})"
    print(f"{site:#x}: {want}={val}")
