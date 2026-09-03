#!/usr/bin/env python3
"""Scratch: disassemble a VA range out of a flat ER image with capstone.

Usage: re117_read.py <image:1162|1170> <va> <nbytes> [--filter SUBSTR]
"""
import sys, os
from capstone import Cs, CS_ARCH_X86, CS_MODE_64

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGES = {"1162": os.path.join(ROOT, "eldenring-deobf.bin"),
          "1170": os.path.join(ROOT, "eldenring-deobf-1.17.bin")}
BASE = 0x140000000

def main():
    which = sys.argv[1]
    va = int(sys.argv[2], 16)
    n = int(sys.argv[3], 16) if sys.argv[3].startswith("0x") else int(sys.argv[3])
    filt = None
    if "--filter" in sys.argv:
        filt = sys.argv[sys.argv.index("--filter") + 1]
    data = open(IMAGES[which], "rb").read()
    off = va - BASE
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = False
    for insn in md.disasm(data[off:off + n], va):
        line = "%016x  %-24s %s %s" % (insn.address, insn.bytes.hex(), insn.mnemonic, insn.op_str)
        if filt is None or filt.lower() in line.lower():
            print(line)

main()
