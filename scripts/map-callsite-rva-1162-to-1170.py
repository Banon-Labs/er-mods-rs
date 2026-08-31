#!/usr/bin/env python3
"""Carry a MID-FUNCTION 1.16.2 address (a call site or return site) onto ELDEN RING 1.17.

`map-rvas-1162-to-1170.py` maps FUNCTION ENTRIES. Several addresses in this workspace are not
entries at all: they are the address of an instruction INSIDE a function, used to recognise a
caller from a captured return address (`trace_first_game_caller_rva`,
`callstack_contains_game_rva`). Handing one of those to the entry mapper is meaningless -- there
is no `.pdata` entry to match and no prologue to sign -- and hooking one would be worse.

WHAT THIS DOES
--------------
Given a mid-function 1.16.2 VA and the entry of the function that contains it, it decodes BOTH
functions from their entries in lockstep, comparing each instruction normalised the way the rest
of this toolchain normalises (mnemonic + register operand shape; displacements, immediates and
branch targets dropped, because that is exactly what a patch moves). When the two bodies agree
instruction for instruction up to the target, the 1.17 address is the byte offset of the SAME
instruction index in the 1.17 function -- which is NOT necessarily `target + function delta`,
because a single changed instruction length shifts everything after it.

It refuses rather than guesses:
  * the target must land exactly on an instruction boundary in 1.16.2;
  * the two decodes must agree on every instruction up to that point;
  * both functions must be declared by their image's own `.pdata`.

USAGE
    python3 scripts/map-callsite-rva-1162-to-1170.py 0x140744e02
    python3 scripts/map-callsite-rva-1162-to-1170.py --entry 0x140744dd0 0x140744e02

With no `--entry` the containing function is looked up in the 1.16.2 `.pdata`, and its 1.17
counterpart in `docs/recon/rva-map-1162-to-1170.functions.tsv`.
"""

import argparse
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLD_IMAGE = os.path.join(ROOT, "eldenring-deobf.bin")
NEW_IMAGE = os.path.join(ROOT, "eldenring-deobf-1.17.bin")
FUNCTION_MAP = os.path.join(ROOT, "docs", "recon", "rva-map-1162-to-1170.functions.tsv")
BASE = 0x140000000


def pdata(image):
    """`{begin RVA: end RVA}` from the image's own exception directory."""
    e_lfanew = struct.unpack_from("<I", image, 0x3C)[0]
    magic = struct.unpack_from("<H", image, e_lfanew + 24)[0]
    directories = e_lfanew + 24 + (112 if magic == 0x20B else 96)
    table_rva, table_size = struct.unpack_from("<II", image, directories + 3 * 8)
    out = {}
    for offset in range(table_rva, table_rva + table_size, 12):
        begin, end, _unwind = struct.unpack_from("<III", image, offset)
        if begin or end:
            out[begin] = end
    return out


def containing(extents, rva):
    """The `.pdata` function whose extent covers `rva`, or None.

    MSVC splits some functions into several RUNTIME_FUNCTION chunks, so the containing chunk may
    start after the function's real entry -- that is fine here: the comparison only needs a common
    starting point in both images, and a chunk boundary is one.
    """
    best = None
    for begin, end in extents.items():
        if begin <= rva < end and (best is None or begin > best):
            best = begin
    return best


def load_function_map():
    mapping = {}
    with open(FUNCTION_MAP, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) == 2 and parts[1] != "-":
                mapping[int(parts[0], 16)] = int(parts[1], 16)
    return mapping


def normalise(insn):
    from capstone import CS_OP_MEM, CS_OP_REG

    parts = [insn.mnemonic]
    for operand in insn.operands:
        if operand.type == CS_OP_REG:
            parts.append(insn.reg_name(operand.reg) or "reg")
        elif operand.type == CS_OP_MEM:
            base = insn.reg_name(operand.mem.base) if operand.mem.base else "-"
            index = insn.reg_name(operand.mem.index) if operand.mem.index else "-"
            parts.append(f"[{base}+{index}*{operand.mem.scale}]")
        else:
            parts.append("imm")
    return " ".join(parts)


def decode(image, begin, end):
    """`[(rva, normalised, length)]` across the whole `.pdata` extent."""
    from capstone import CS_ARCH_X86, CS_MODE_64, Cs

    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True
    out = []
    for insn in md.disasm(bytes(image[begin:end]), begin):
        out.append((insn.address, normalise(insn), insn.size))
    return out


def carry(old_image, new_image, old_extents, new_extents, function_map, target_rva, entry_rva):
    old_entry = entry_rva if entry_rva is not None else containing(old_extents, target_rva)
    if old_entry is None:
        return None, f"no 1.16.2 .pdata function contains {target_rva + BASE:#x}"
    new_entry = function_map.get(old_entry)
    if new_entry is None:
        return None, f"the containing function {old_entry + BASE:#x} has no 1.17 counterpart in the function map"
    old_body = decode(old_image, old_entry, old_extents[old_entry])
    new_body = decode(new_image, new_entry, new_extents.get(new_entry, new_entry + (old_extents[old_entry] - old_entry) + 0x40))
    index = next((i for i, (rva, _, _) in enumerate(old_body) if rva == target_rva), None)
    if index is None:
        return None, f"{target_rva + BASE:#x} is not an instruction boundary in {old_entry + BASE:#x}"
    if index >= len(new_body):
        return None, f"the 1.17 function is shorter than instruction index {index}"
    for i in range(index + 1):
        if old_body[i][1] != new_body[i][1]:
            return None, (
                f"bodies diverge at instruction {i} of {index}: "
                f"1.16.2 {old_body[i][0] + BASE:#x} {old_body[i][1]!r} vs "
                f"1.17 {new_body[i][0] + BASE:#x} {new_body[i][1]!r}"
            )
    return new_body[index][0], (
        f"instruction {index} of {old_entry + BASE:#x} -> {new_entry + BASE:#x}; "
        f"{index + 1} leading instructions identical; offset {target_rva - old_entry:#x} -> "
        f"{new_body[index][0] - new_entry:#x}"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("vas", nargs="+", help="1.16.2 mid-function VAs (hex 0x...)")
    parser.add_argument("--entry", help="containing function entry VA, when .pdata does not declare one")
    arguments = parser.parse_args()

    try:
        import capstone  # noqa: F401
    except ImportError:
        os.execvp("uv", ["uv", "run", "--with", "capstone", "python3", *sys.argv])

    old_image = open(OLD_IMAGE, "rb").read()
    new_image = open(NEW_IMAGE, "rb").read()
    old_extents = pdata(old_image)
    new_extents = pdata(new_image)
    function_map = load_function_map()
    entry = int(arguments.entry, 0) - BASE if arguments.entry else None

    print("# 1.16.2 VA\t1.17 VA\tdelta\tmethod-or-error")
    for text in arguments.vas:
        target = int(text, 0) - BASE
        result, note = carry(old_image, new_image, old_extents, new_extents, function_map, target, entry)
        if result is None:
            print(f"{target + BASE:#x}\t-\t-\tUNRESOLVED: {note}")
        else:
            print(f"{target + BASE:#x}\t{result + BASE:#x}\t{result - target:+#x}\tcall-site carry: {note}")


if __name__ == "__main__":
    main()
