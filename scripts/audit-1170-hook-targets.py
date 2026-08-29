#!/usr/bin/env python3
"""Audit the translated 1.17 detour targets OFFLINE, before any of them is installed.

`scripts/verify-rva-map-1170.py` answers "is the code at the mapped address the same function?".
This answers three questions it never asks, each of which corrupts the process rather than
faulting cleanly, and none of which needs the game to run:

  ENTRY     is the 1.17 target a FUNCTION ENTRY, or did the signature re-occur mid-function?
            A mapper finds where bytes recur; an inlined copy of a prologue is a perfectly good
            match and a perfectly fatal detour. Evidence is FORWARD-derived: `call`/`jmp rel32`
            instructions elsewhere in the image whose computed destination is exactly this
            address, plus absolute 8-byte pointers to it (vtable slots, jump tables).
            An earlier version of this check decoded BACKWARDS from the address looking for
            padding or a terminator, and was deleted: run against the 1.16.2 image at the 27
            addresses this project has hooked successfully for months, it called 20 of them
            mid-function. Backward decoding desynchronises, and a de-Arxan'd image does not
            carry the int3 padding the check assumed. A test that fails on known-good input is
            not a strict test, it is a broken one.
  PATCH     do the whole instructions MinHook must relocate fit, and does anything jump INTO the
            five bytes it overwrites? A short jump landing inside the patch returns into a JMP's
            operand bytes -- an execute-fault into an address in no module, with no unwind.
  OVERLAP   do two targets land within 16 bytes of each other? The second MH_CreateHook then
            reads a prologue the first one has already replaced with a JMP, and trampolines it.

    python3 scripts/audit-1170-hook-targets.py              # audit the translated pairs
    python3 scripts/audit-1170-hook-targets.py --calibrate  # same checks on 1.16.2 known-good
    python3 scripts/audit-1170-hook-targets.py --selftest
"""

import argparse
import os
import sys

try:
    import capstone
except ImportError:  # provisioned ephemerally; there is no system pip here
    os.execvp("uv", ["uv", "run", "--with", "capstone", "python3", *sys.argv])

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGE_1170 = os.path.join(ROOT, "eldenring-deobf-1.17.bin")
IMAGE_1162 = os.path.join(ROOT, "eldenring-deobf.bin")
VERIFIED = os.path.join(ROOT, "docs", "recon", "rva-map-1162-to-1170.verified.tsv")
BASE = 0x140000000
# er-hook/build.rs admits exactly these rows; the audit must see the same set it will install.
MIN_VERIFIED_INSNS = 12
# MinHook writes a 5-byte relative JMP over the entry and relocates whole instructions out of it.
PATCH_BYTES = 5
# Two entries closer than this share MinHook's patch/relocation window.
OVERLAP_BYTES = 16
# Enough of the function to see the short-range branches that could target its own prologue.
BRANCH_SCAN_BYTES = 0x400


def rows():
    """(1.16.2 VA, 1.17 VA) exactly as er-hook/build.rs filters them."""
    out = []
    for line in open(VERIFIED, encoding="utf-8"):
        if line.startswith("#") or not line.strip():
            continue
        f = line.rstrip("\n").split("\t")
        if len(f) < 5 or f[2] != "IDENTICAL" or int(f[4]) < MIN_VERIFIED_INSNS:
            continue
        out.append((int(f[0], 16), int(f[1], 16)))
    return sorted(out, key=lambda pair: pair[1])


def xref_targets(blob, wanted):
    """Addresses in `wanted` that something in the image CALLs, JMPs to, or stores a pointer to.

    One linear pass for the relative forms (every 0xE8/0xE9 byte is treated as a candidate
    opcode and its rel32 resolved -- false candidates resolve outside the image or miss the
    set), plus a direct search for each address's little-endian 8-byte encoding, which is how
    a vtable slot or a jump table names a function.
    """
    hits = {va: {"call": 0, "jmp": 0, "ptr": 0} for va in wanted}
    limit = len(blob)
    for opcode, kind in ((0xE8, "call"), (0xE9, "jmp")):
        pos = blob.find(bytes([opcode]))
        while pos != -1 and pos + 5 <= limit:
            rel = int.from_bytes(blob[pos + 1 : pos + 5], "little", signed=True)
            dest = BASE + pos + 5 + rel
            if dest in hits:
                hits[dest][kind] += 1
            pos = blob.find(bytes([opcode]), pos + 1)
    for va in wanted:
        needle = va.to_bytes(8, "little")
        pos = blob.find(needle)
        while pos != -1:
            hits[va]["ptr"] += 1
            pos = blob.find(needle, pos + 1)
    return hits


def entry_verdict(hit):
    """Positive evidence only: something names this address as a callable destination."""
    total = hit["call"] + hit["jmp"] + hit["ptr"]
    if total == 0:
        return False, "nothing references it"
    return True, f"{hit['call']} call, {hit['jmp']} jmp, {hit['ptr']} ptr"


def patch_safe(blob, va):
    """Whole instructions covering five bytes, and nothing branching into them."""
    off = va - BASE
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True
    covered = 0
    for insn in md.disasm(blob[off : off + 32], va):
        covered += insn.size
        if covered >= PATCH_BYTES:
            break
    if covered < PATCH_BYTES:
        return False, f"only {covered}B of whole instructions"
    hot = range(va + 1, va + covered)
    body = blob[off : off + BRANCH_SCAN_BYTES]
    for insn in md.disasm(body, va):
        if capstone.CS_GRP_JUMP not in insn.groups:
            continue
        for op in insn.operands:
            if op.type == capstone.x86.X86_OP_IMM and op.imm in hot:
                return False, f"{insn.mnemonic} at 0x{insn.address:x} targets 0x{op.imm:x}"
    return True, f"{covered}B relocatable"


def audit(image_path, pairs, column, label):
    """Run the three checks over `pairs`, judging the address in `column` of each."""
    blob = open(image_path, "rb").read()
    targets = sorted({pair[column] for pair in pairs})
    hits = xref_targets(blob, set(targets))
    bad = 0
    previous = None
    print(f"{len(pairs)} pairs, judging the {label} address against "
          f"{os.path.basename(image_path)}\n")
    for pair in sorted(pairs, key=lambda p: p[column]):
        va = pair[column]
        flags = []
        ok, why = entry_verdict(hits[va])
        detail = why
        if not ok:
            flags.append(f"MID-FUNCTION ({why})")
        ok, why = patch_safe(blob, va)
        if not ok:
            flags.append(f"PATCH-UNSAFE ({why})")
        else:
            detail += f", {why}"
        if previous is not None and va - previous < OVERLAP_BYTES:
            flags.append(f"OVERLAP (0x{previous:x} is {va - previous}B away)")
        previous = va
        arrow = f"0x{pair[0]:x} -> 0x{pair[1]:x}"
        print(f"{arrow}  {'; '.join(flags) if flags else 'ENTRY-OK'}  [{detail}]")
        if flags:
            bad += 1
    print(f"\n{bad} of {len(pairs)} need a look.")
    return bad


def selftest():
    """Calibrate on input whose answer is already known, then assert the deliberate negative.

    The 27 SOURCE addresses are hooked successfully on 1.16.2 today, so a check that calls any
    of them mid-function is broken -- which is exactly how the previous implementation was
    caught. The negative control is an address two bytes into a known entry.
    """
    pairs = rows()
    bad = audit(IMAGE_1162, pairs, 0, "1.16.2")
    assert bad == 0, f"{bad} known-good 1.16.2 entries were flagged; the check is broken"
    blob = open(IMAGE_1170, "rb").read()
    inside = 0x1407AE8C0 + 2
    hits = xref_targets(blob, {inside})
    ok, why = entry_verdict(hits[inside])
    assert not ok, f"an address inside a function must not read as an entry, got {why}"
    print("\nselftest OK")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument(
        "--calibrate",
        action="store_true",
        help="run the same checks on the 1.16.2 source addresses, which are known-good",
    )
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if args.calibrate:
        return 1 if audit(IMAGE_1162, rows(), 0, "1.16.2") else 0
    return 1 if audit(IMAGE_1170, rows(), 1, "1.17") else 0


if __name__ == "__main__":
    sys.exit(main())
