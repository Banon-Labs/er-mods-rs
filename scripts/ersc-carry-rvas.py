#!/usr/bin/env python3
"""Carry ersc.dll rvas (function entries or mid-function sites) from one Seamless build to another.

For each rva it prints two independent answers, to be compared:

  - the enclosing `.pdata` function in the old build and where that function sits in the new
    build by the sizes of its `.pdata` neighbours (`ersc-disas.py pdatamatch`, widening down to
    radius 2), plus the same offset into it;
  - a byte window starting at the rva itself, with rel32 branch targets and memory
    displacements masked, searched for in the new build (24, 40, then 64 bytes, first unique hit).

Agreement is the carry. A miss or disagreement is left for a person to read with `disas`.

Usage
    uv run --with capstone python3 scripts/ersc-carry-rvas.py 0x258d0 0x38ab5 --build v201 --to v202
    uv run --with capstone python3 scripts/ersc-carry-rvas.py --selftest
"""

import argparse
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = 0x180000000


def _ersc_disas():
    spec = importlib.util.spec_from_file_location("_ersc_disas", os.path.join(ROOT, "scripts", "ersc-disas.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def masked_window(data, va, n):
    """`(pattern, mask)` over whole instructions covering at least `n` bytes of `data` at `va`.

    Memory displacements and the immediate of a branch or call are masked (mask byte 0), so a
    window survives code moving around it.
    """
    import capstone

    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True
    pattern, mask = bytearray(), bytearray()
    for insn in md.disasm(data, va):
        if len(pattern) >= n:
            break
        m = bytearray([1] * insn.size)
        if insn.disp_size and insn.disp_offset:
            m[insn.disp_offset : insn.disp_offset + insn.disp_size] = bytes(insn.disp_size)
        if insn.imm_size and insn.imm_offset and (
            insn.group(capstone.CS_GRP_JUMP) or insn.group(capstone.CS_GRP_CALL)
        ):
            m[insn.imm_offset : insn.imm_offset + insn.imm_size] = bytes(insn.imm_size)
        pattern += insn.bytes
        mask += m
    return bytes(pattern), bytes(mask)


def selftest():
    # call rel32 ; mov eax,[rip+0x10] ; ret
    data = bytes.fromhex("e811223344" "8b0510000000" "c3")
    pattern, mask = masked_window(data, BASE + 0x1000, 8)
    expect = bytes([1, 0, 0, 0, 0, 1, 1, 0, 0, 0, 0])
    if pattern != data[:11] or mask != expect:
        print(f"SELFTEST FAIL: mask {mask.hex()} != {expect.hex()}", file=sys.stderr)
        return 1
    print("selftest passed")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("rva", nargs="*")
    parser.add_argument("--build", default="v201")
    parser.add_argument("--to", default="v202")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if not args.rva:
        parser.error("at least one rva is required")

    ed = _ersc_disas()
    old, old_path = ed.load(args.build)
    new, new_path = ed.load(args.to)
    print(f"# from {args.build}: {old_path}\n# to   {args.to}: {new_path}")
    old_entries, new_entries = ed.pdata_entries(old), ed.pdata_entries(new)

    for argument in args.rva:
        value = int(argument, 0)
        rva = value - BASE if value >= BASE else value
        line = f"0x{rva:x}:"
        enclosing = next(((b, e) for b, e in old_entries if b <= rva < e), None)
        if enclosing:
            begin = enclosing[0]
            carried, how = None, "pdata miss"
            for radius in (4, 3, 2):
                _, hits = ed.size_signature_matches(old_entries, new_entries, begin, radius=radius)
                if len(hits) == 1:
                    carried, how = hits[0][1], f"pdata r{radius}"
                    break
            off = rva - begin
            line += f" fn 0x{begin:x}+0x{off:x} -> "
            line += f"0x{carried + off:x} [{how}]" if carried is not None else how
        else:
            line += " no enclosing .pdata entry"
        data = old.bytes_at_va(BASE + rva, 0x60)
        verdict = " | bytes: unreadable"
        if data is not None:
            for n in (24, 40, 64):
                pattern, mask = masked_window(data, BASE + rva, n)
                hits = new.find(pattern, mask)
                if len(hits) == 1:
                    verdict = f" | bytes{n}: 0x{hits[0] - BASE:x}"
                    break
                verdict = f" | bytes{n}: {len(hits)} hits " + ",".join(f"0x{h - BASE:x}" for h in hits[:4])
        print(line + verdict)
    return 0


if __name__ == "__main__":
    sys.exit(main())
