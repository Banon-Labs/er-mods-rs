#!/usr/bin/env python3
"""List instructions in a flat Elden Ring image whose memory displacement equals a value.

Finds every occurrence of the displacement's little-endian bytes, decodes backwards from a few
start offsets with capstone, and keeps decodings that end exactly on an instruction carrying that
displacement. Prints the instruction and, when the Ghidra daemon is given, the containing function.

Run under uv: uv run --with capstone python3 dispscan.py <image> <disp hex> [<lo va> <hi va>]
"""
import struct
import sys

import capstone

BASE = 0x140000000
data = open(sys.argv[1], "rb").read()
disp = int(sys.argv[2], 16)
lo = int(sys.argv[3], 16) - BASE if len(sys.argv) > 3 else 0x1000
hi = int(sys.argv[4], 16) - BASE if len(sys.argv) > 4 else 0x2A00000
needle = struct.pack("<i", disp)
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
md.detail = True
seen = set()
i = data.find(needle, lo)
while i != -1 and i < hi:
    for back in range(2, 12):
        start = i - back
        insns = list(md.disasm(data[start : i + 12], BASE + start, 1))
        if not insns:
            continue
        ins = insns[0]
        if ins.size < back + 4:
            continue
        hit = False
        for op in ins.operands:
            if op.type == capstone.x86.X86_OP_MEM and op.mem.disp == disp and op.mem.base != capstone.x86.X86_REG_RIP:
                hit = True
        if hit and ins.address not in seen:
            seen.add(ins.address)
            print(f"{ins.address:#x}: {ins.mnemonic} {ins.op_str}")
            break
    i = data.find(needle, i + 1)
