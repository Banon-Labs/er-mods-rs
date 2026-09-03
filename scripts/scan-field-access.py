#!/usr/bin/env python3
"""Scan a flat de-obfuscated Elden Ring image for instructions touching [reg+DISP].

Ghidra xrefs cannot answer "who reads/writes struct field +0xNN"; this does, by
decoding .text with capstone and matching the memory-operand displacement (and,
optionally, the immediate).  Complements scripts/find-deobf-bytes.py, which only
takes literal byte patterns with whole-byte `??` wildcards.

    uv run --with capstone python3 scripts/scan-field-access.py 0x1c8 0x40,0xbf
    ER_DEOBF_BIN=eldenring-deobf-1.17.bin uv run --with capstone \
        python3 scripts/scan-field-access.py 0x538 --size 1

Env: ER_DEOBF_BIN (default eldenring-deobf.bin = 1.16.2), SCAN_START, SCAN_END.
"""
import argparse
import os
import sys

from capstone import CS_ARCH_X86, CS_MODE_64, Cs
from capstone.x86 import X86_OP_IMM, X86_OP_MEM

IMAGE_BASE = 0x140000000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("disp", help="struct field displacement, e.g. 0x1c8")
    ap.add_argument("imms", nargs="?", default=None,
                    help="comma-separated immediates to require, e.g. 0x40,0xbf")
    ap.add_argument("--size", type=int, default=0,
                    help="require this memory-operand size in bytes (0 = any)")
    a = ap.parse_args()

    image = os.environ.get("ER_DEOBF_BIN", "eldenring-deobf.bin")
    data = open(image, "rb").read()
    disp = int(a.disp, 0)
    imms = [int(x, 0) & 0xFF for x in a.imms.split(",")] if a.imms else None
    start = int(os.environ.get("SCAN_START", "0x1000"), 0)
    end = int(os.environ.get("SCAN_END", hex(len(data))), 0)

    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True
    # A flat image carries no instruction map, so sweep linearly and RESYNC: capstone's
    # generator stops at the first undecodable byte, which a mid-instruction start hits
    # almost immediately.  Restart one byte past the stall rather than skipping the rest
    # of the window (that silently reported ~50 hits instead of thousands).
    seen, hits = set(), []
    cursor = start
    while cursor < end:
        last = None
        for ins in md.disasm(data[cursor:end], IMAGE_BASE + cursor):
            last = ins
            if ins.address in seen:
                break
            seen.add(ins.address)
            mem = imm = None
            for o in ins.operands:
                if o.type == X86_OP_MEM and o.mem.disp == disp and o.mem.base != 0:
                    mem = o
                elif o.type == X86_OP_IMM:
                    imm = o.imm
            if mem is None:
                continue
            if a.size and mem.size != a.size:
                continue
            if imms is not None and (imm is None or (imm & 0xFF) not in imms):
                continue
            hits.append((ins.address, f"{ins.mnemonic} {ins.op_str}"))
        cursor = (last.address - IMAGE_BASE + last.size + 1) if last is not None else cursor + 1

    for addr, text in sorted(hits):
        print(f"{addr:#x}  {text}")
    print(f"# {len(hits)} hits for disp {disp:#x} in {image}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
