#!/usr/bin/env python3
"""List every `mov edx/r8d, <imm>` in a flat de-Arxan'd image that a call follows within five
instructions, with that call's target.

Built to find the readers of a `stateInfo` value: the `CS::SpecialEffect` queries
(`HasSpEffectWithStateInfo`, `RemoveByStateInfo`, ...) take it in `edx`. A call passing the value
in another register, or computing it, is not found, so an empty result is not proof of absence.

    uv run --with capstone python3 scripts/find-imm-arg-calls.py 0x180 0x181
    ER_DEOBF_BIN=eldenring-deobf-1.17.1.bin uv run --with capstone python3 scripts/find-imm-arg-calls.py 384
    uv run --with capstone python3 scripts/find-imm-arg-calls.py --any 0x180   # cmp/lea/sub too

The default image is `eldenring-deobf.bin` (1.16.2, the named Ghidra dump's build; shift 0).
"""
import os
import re
import sys

from capstone import CS_ARCH_X86, CS_MODE_64, Cs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import function_extent  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = 0x140000000
TEXT_END = 0x2a00000


def any_operand(img, md, v):
    """Every instruction whose 32- or 16-bit immediate or displacement is `v` or `-v`.

    Each occurrence of the encoded value is tried as the tail of an instruction starting 1..10
    bytes earlier; a start that decodes to one instruction ending exactly after the value, with
    the value in an operand, is kept. Catches `cmp eax, 0x180`, `lea eax, [rcx - 0x180]`,
    `cmp word ptr [rax + 0x156], 0x180`. Overlapping decodings of one site can print twice.
    """
    pats = {v.to_bytes(4, 'little', signed=True), (-v).to_bytes(4, 'little', signed=True)}
    if -0x8000 <= v < 0x8000:
        pats |= {v.to_bytes(2, 'little', signed=True)}
    seen = set()
    for pat in pats:
        for m in re.finditer(re.escape(pat), img):
            end = m.end()
            if end > TEXT_END or end < 0x1000:
                continue
            for back in range(len(pat) + 1, len(pat) + 9):
                start = end - back
                insns = list(md.disasm(img[start:end], BASE + start, 1))
                if not insns or insns[0].size != back:
                    continue
                i = insns[0]
                if not re.search(rf'(?<![0-9a-fx]){-v:#x}|(?<![0-9a-fx]){v:#x}(?![0-9a-f])', i.op_str):
                    continue
                if i.mnemonic in ('call', 'jmp') or i.mnemonic.startswith('j'):
                    continue
                if start in seen:
                    continue
                seen.add(start)
                print(f'{v:#x} {BASE + start:#x} {i.mnemonic} {i.op_str}')


def main():
    image = os.environ.get('ER_DEOBF_BIN', 'eldenring-deobf.bin')
    img = open(os.path.join(ROOT, image), 'rb').read()
    args = sys.argv[1:]
    anyop = '--any' in args
    vals = [int(v, 0) for v in args if v != '--any']
    if not vals:
        sys.exit('usage: find-imm-arg-calls.py [--any] <imm> [<imm>...]')
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    if anyop:
        for v in vals:
            any_operand(img, md, v)
        return
    for v in vals:
        imm = v.to_bytes(4, 'little')
        for enc in (b'\xba' + imm, b'\x41\xb8' + imm):
            for m in re.finditer(re.escape(enc), img):
                off = m.start()
                if off < 0x1000 or off > TEXT_END:
                    continue
                # The mov and the five after it, never past the function's last byte; an
                # unknown extent is a refusal, not a guess.
                stop = function_extent.body_end(img, BASE + off)
                if stop is None or stop <= off:
                    continue
                insns = list(md.disasm(img[off:stop], BASE + off, 6))
                if not insns or insns[0].size != len(enc):
                    continue
                calls = [i.op_str for i in insns[1:6] if i.mnemonic == 'call']
                if calls:
                    print(f'{v:#x} {BASE + off:#x} {insns[0].op_str} -> call {calls[0]}')


if __name__ == '__main__':
    main()
