#!/usr/bin/env python3
"""Histogram of field displacements reached through ChrIns->container->module.

Finds `mov rA,[rX + 0x190]` ; `mov rB,[rA + MODOFF]` ; <any insn touching [rB + disp]>
and histograms that final disp. Comparing the histogram between builds shows whether the
module's field layout moved.

Usage: re117_module_field_scan.py <1162|1170> <module_off_hex>
"""
import sys, os, collections
from capstone import Cs, CS_ARCH_X86, CS_MODE_64, x86_const

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGES = {"1162": os.path.join(ROOT, "eldenring-deobf.bin"),
          "1170": os.path.join(ROOT, "eldenring-deobf-1.17.bin")}
BASE = 0x140000000
CONTAINER_OFF = 0x190

def main():
    which = sys.argv[1]
    modoff = int(sys.argv[2], 16)
    data = open(IMAGES[which], "rb").read()
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True
    disp = CONTAINER_OFF.to_bytes(4, "little")
    hist = collections.Counter()
    sites = collections.defaultdict(list)
    i = 0
    chains = 0
    while True:
        i = data.find(disp, i + 1)
        if i < 0:
            break
        for back in (3, 4):
            start = i - back
            if start < 0:
                continue
            insns = list(md.disasm(data[start:start + 48], BASE + start))
            if len(insns) < 3:
                continue
            a = insns[0]
            if a.mnemonic != "mov" or len(a.operands) != 2:
                continue
            dst, src = a.operands
            if (dst.type != x86_const.X86_OP_REG or src.type != x86_const.X86_OP_MEM
                    or src.mem.disp != CONTAINER_OFF or src.mem.index != 0 or a.size != back + 4):
                continue
            b = insns[1]
            if b.mnemonic != "mov" or len(b.operands) != 2:
                break
            d2, s2 = b.operands
            if (s2.type != x86_const.X86_OP_MEM or s2.mem.base != dst.reg
                    or s2.mem.index != 0 or s2.mem.disp != modoff
                    or d2.type != x86_const.X86_OP_REG):
                break
            chains += 1
            # walk forward a few instructions for the first [d2.reg + disp] memory operand
            for c in insns[2:6]:
                hit = None
                for op in c.operands:
                    if (op.type == x86_const.X86_OP_MEM and op.mem.base == d2.reg
                            and op.mem.index == 0):
                        hit = op.mem.disp
                        break
                if hit is not None:
                    hist[hit] += 1
                    if len(sites[hit]) < 3:
                        sites[hit].append(hex(c.address))
                    break
                # stop if the register is clobbered
                if c.operands and c.operands[0].type == x86_const.X86_OP_REG and c.operands[0].reg == d2.reg:
                    break
            break
    print(f"# image={which} module=+0x{modoff:x} chains={chains}")
    for off in sorted(hist):
        print(f"  +0x{off:<4x}  n={hist[off]:<4d}  e.g. {', '.join(sites[off])}")

main()
