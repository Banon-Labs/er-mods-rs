#!/usr/bin/env python3
"""Every instruction that reads one struct byte through a wider operand.

`find-deobf-field-access.py N` finds instructions whose displacement is exactly N. A bitfield
byte is often read through a word, dword, qword or vector load that starts below it -- `mov eax,
[rcx+0x15c]` reads byte 0x15f too -- and those never show up there. This decodes (capstone) every
instruction whose memory operand starts at a displacement in [N-15, N] and whose width reaches N,
skips plain stores, and prints it with the next few instructions so the consumer of the loaded
bits is visible.

    uv run --with capstone python3 scripts/find-deobf-covering-loads.py 0x15f
    uv run --with capstone python3 scripts/find-deobf-covering-loads.py 0x15f --image eldenring-deobf-1.17.1.bin

A displacement is not a struct: every hit is some register plus that offset. Narrow the
survivors by reading the function in Ghidra.
"""

from __future__ import annotations

import argparse
import os
import sys

import capstone
from capstone import x86

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import function_extent  # noqa: E402

DEFAULT_IMG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eldenring-deobf.bin"
)
BASE = 0x140000000


def covering(data: bytes, target: int, follow: int) -> list[tuple[int, str]]:
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True
    found: dict[int, str] = {}
    for disp in range(max(0, target - 15), target + 1):
        # One-byte displacements encode as mod=01; everything above 0x7f is four bytes.
        enc = disp.to_bytes(1 if disp < 0x80 else 4, "little")
        start = 0
        while True:
            at = data.find(enc, start)
            if at < 0:
                break
            start = at + 1
            for back in range(2, 9):
                s = at - back
                if s < 0 or s in found:
                    continue
                # Stop at the function's last byte; an unknown extent is a refusal, not a guess.
                stop = function_extent.body_end(data, BASE + s)
                if stop is None or stop <= s:
                    continue
                insns = list(md.disasm(data[s:stop], BASE + s, follow + 1))
                if not insns:
                    continue
                first = insns[0]
                # The displacement must sit inside this instruction, after the opcode.
                if not (first.address < BASE + at < first.address + first.size):
                    continue
                hit = False
                for op in first.operands:
                    if (
                        op.type == x86.X86_OP_MEM
                        and op.mem.base not in (0, x86.X86_REG_RIP)
                        and op.mem.disp == disp
                        and disp + op.size > target
                    ):
                        hit = True
                if not hit:
                    continue
                if first.mnemonic.startswith("mov") and first.operands[0].type == x86.X86_OP_MEM:
                    continue
                found[s] = " | ".join(f"{i.mnemonic} {i.op_str}" for i in insns)
                break
    return sorted(found.items())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("byte", help="struct byte offset, e.g. 0x15f")
    parser.add_argument("--image", default=os.environ.get("ER_DEOBF_BIN", DEFAULT_IMG))
    parser.add_argument("--follow", type=int, default=5, help="instructions to show after each hit")
    parser.add_argument("--range", dest="va_range", help="restrict to LO-HI virtual addresses")
    args = parser.parse_args()
    target = int(args.byte, 0)
    low, high = BASE, BASE + (1 << 32)
    if args.va_range:
        lo_text, _, hi_text = args.va_range.partition("-")
        low, high = int(lo_text, 0), int(hi_text, 0)
    with open(args.image, "rb") as image:
        data = image.read()
    hits = [(s, t) for s, t in covering(data, target, args.follow) if low <= s + BASE < high]
    print(f"byte {target:#x}: {len(hits)} reading instructions in {args.image}")
    for s, text in hits:
        print(f"  {s + BASE:#x}  {text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
