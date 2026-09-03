#!/usr/bin/env python3
"""Print the same short instruction window from 1.16.2 and 1.17 side by side."""
import sys, os
from capstone import Cs, CS_ARCH_X86, CS_MODE_64
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = 0x140000000
IMG = {"1162": open(os.path.join(ROOT, "eldenring-deobf.bin"), "rb").read(),
       "1170": open(os.path.join(ROOT, "eldenring-deobf-1.17.bin"), "rb").read()}
md = Cs(CS_ARCH_X86, CS_MODE_64); md.detail = False
def dis(which, va, n):
    out = []
    for i in md.disasm(IMG[which][va - BASE: va - BASE + n], va):
        out.append("%09x %-20s %s %s" % (i.address, i.bytes.hex(), i.mnemonic, i.op_str))
    return out
def main():
    # argv: label a1162 a1170 nbytes
    for chunk in sys.argv[1:]:
        label, a, b, n = chunk.split(",")
        a, b, n = int(a, 16), int(b, 16), int(n, 16)
        print("### " + label)
        L, R = dis("1162", a, n), dis("1170", b, n)
        for i in range(max(len(L), len(R))):
            l = L[i] if i < len(L) else ""
            r = R[i] if i < len(R) else ""
            same = "  " if (l.split(None, 2)[1:] == r.split(None, 2)[1:]) else "!!"
            print(f"{same} {l:<62s} | {r}")
        print()
main()
