#!/usr/bin/env python3
"""Audit every constant detour target offline, before any of them is installed.

A constant detour may target only an rva on `crates/er-game-base/src/detour_allowlist.rs`
(`DETOUR_ALLOWLIST`). Being on the list is a licence for MinHook to write five bytes there, so
this answers three questions about each row against the 2.7.2.0 image
(`eldenring-deobf-1.17.2.bin`), each of which corrupts the process rather than faulting cleanly,
and none of which needs the game to run:

  Entry     is the target a function entry, or the middle of one? Evidence is forward-derived:
            `call`/`jmp rel32` instructions elsewhere in the image whose computed destination is
            exactly this address, plus absolute 8-byte pointers to it (vtable slots, jump tables),
            plus the image's own `.pdata` -- which both declares entries and, by declaring none
            that contains the address, identifies an unwindless leaf. Those last two are different
            answers and reading them as one flagged good leaf targets as mid-function.
            An earlier version of this check decoded backwards from the address looking for
            padding or a terminator, and was deleted: run against 27 addresses this project had
            hooked successfully for months, it called 20 of them mid-function. Backward decoding
            desynchronises, and a de-Arxan'd image does not carry the int3 padding the check
            assumed. A test that fails on known-good input is not a strict test, it is a broken one.
  Patch     do the whole instructions MinHook must relocate fit, and does anything jump into the
            five bytes it overwrites? A short jump landing inside the patch returns into a JMP's
            operand bytes -- an execute-fault into an address in no module, with no unwind.
  Overlap   do two targets land within 16 bytes of each other? The second MH_CreateHook then
            reads a prologue the first one has already replaced with a JMP, and trampolines it.

    python3 scripts/audit-hook-targets.py              # audit the allowlist
    python3 scripts/audit-hook-targets.py --selftest   # fixtures, controls, then the audit
"""

import argparse
import os
import re
import struct
import sys

try:
    import capstone
except ImportError:  # provisioned ephemerally; there is no system pip here
    os.execvp("uv", ["uv", "run", "--with", "capstone", "python3", *sys.argv])

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import er_game_image  # noqa: E402 - repo-local; see the sys.path line above
import function_extent  # noqa: E402 - repo-local; see the sys.path line above

ROOT = str(er_game_image.REPO)
BASE = er_game_image.BASE
# The licence table, read rather than transcribed: the audit's whole claim is that it judges the
# same address set `resolve_detour_address` admits.
ALLOWLIST = os.path.join(ROOT, "crates", "er-game-base", "src", "detour_allowlist.rs")
_ROW = re.compile(r"^\s*(0x[0-9a-fA-F_]+)\s*,", re.MULTILINE)
# MinHook writes a 5-byte relative JMP over the entry and relocates whole instructions out of it.
PATCH_BYTES = 5
# Two entries closer than this share MinHook's patch/relocation window.
OVERLAP_BYTES = 16
# Enough of the function to see the short-range branches that could target its own prologue.
BRANCH_SCAN_BYTES = 0x400

# --- MinHook's own trampoline rules, ported ---------------------------------------------------
# Every constant and branch below is read out of `vendor/minhook/src/trampoline.c`
# (`CreateTrampolineFunction`, `IsCodePadding`), which is the code that will actually be asked to
# install these detours. Where this port cannot match it exactly, it is noted at the site.
#
# The rule that was missing once, and what it cost: an accumulator with no stop condition but its
# own byte count scored a function that ended inside the five bytes on whatever followed it --
# padding, then the next function. Two real addresses came back "relocatable" on bytes belonging
# to their neighbours (a 3-byte `8b c1 / c3` leaf reported 8B, a 4-byte `41 89 00 / c3` leaf
# reported 11B). `PATCH_SAFE_CASES` keeps both as fixtures.
JMP_REL_SIZE = 5  # sizeof(JMP_REL): the relative JMP MinHook writes over the entry
JMP_REL_SHORT_SIZE = 2  # sizeof(JMP_REL_SHORT): the 2-byte hop it writes instead when patching above
JMP_ABS_SIZE = 14  # sizeof(JMP_ABS), the form a relocated JMP grows into
CALL_ABS_SIZE = 16  # sizeof(CALL_ABS)
JCC_ABS_SIZE = 16  # sizeof(JCC_ABS)
MAX_TRAMPOLINE_IPS = 8  # ARRAYSIZE(TRAMPOLINE.oldIPs) -- more instructions than this and it gives up


class Vacuous(Exception):
    """The licence table yielded no rows, so an audit of it would examine nothing."""


def is_code_padding(blob, off, size):
    """`IsCodePadding`: `size` bytes of one filler, from {0x00, 0x90, 0xCC}.

    It does not look for a particular filler byte. MSVC pads with 0xCC in some places and 0x90 in
    others, so neither is the signal -- the signal is a uniform run of any one of the three.
    MinHook needs the run uniform because it overwrites the whole of it.
    """
    if size <= 0:
        return True
    if off < 0 or off + size > len(blob):
        return False
    filler = blob[off]
    if filler not in (0x00, 0x90, 0xCC):
        return False
    return all(blob[off + i] == filler for i in range(1, size))


def _branch_target(insn):
    """Absolute destination of a relative branch, or None if the operand is not an immediate."""
    for op in insn.operands:
        if op.type == capstone.x86.X86_OP_IMM:
            return op.imm
    return None


def rows(path=ALLOWLIST):
    """Every target VA the licence table admits, as `(va, va)` pairs sorted by address.

    The pair shape is kept so `audit` can judge either column; for the allowlist both columns are
    the same 2.7.2.0 VA. An empty table raises `Vacuous`: `assert not bad` over an empty set is a
    gate that examined nothing and reported clean, which this file once did for a whole day.
    """
    with open(path, encoding="utf-8") as handle:
        found = sorted({BASE + int(value.replace("_", ""), 16) for value in _ROW.findall(handle.read())})
    if not found:
        raise Vacuous(f"{os.path.relpath(path, ROOT)} yielded no rows")
    return [(va, va) for va in found]


def xref_targets(blob, wanted):
    """Addresses in `wanted` that something in the image CALLs, JMPs to, or stores a pointer to.

    One linear pass for the relative forms (every 0xE8/0xE9 byte is treated as a candidate opcode
    and its rel32 resolved -- false candidates resolve outside the image or miss the set), plus a
    search for the little-endian 8-byte encoding, which is how a vtable slot or a jump table names
    a function.

    The pointer search is shared, not per-address, which is what makes the cost independent of how
    many rows the allowlist holds: every needle for a VA in this image ends in the same five bytes
    (`<0x40|0x41|0x42> 01 00 00 00`), so scanning for those few suffixes and reconstructing the low
    three bytes at each hit finds exactly the occurrences a per-address search would, unaligned
    ones included. `_per_address_pointer_scan` is kept and the selftest asserts the two agree.
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
    for suffix in {va.to_bytes(8, "little")[3:] for va in wanted}:
        pos = blob.find(suffix)
        while pos != -1:
            if pos >= 3:
                va = int.from_bytes(blob[pos - 3 : pos + 5], "little")
                if va in hits:
                    hits[va]["ptr"] += 1
            pos = blob.find(suffix, pos + 1)
    return hits


def _per_address_pointer_scan(blob, wanted):
    """The straightforward one-scan-per-address pointer search, kept as an equivalence fixture."""
    hits = {va: 0 for va in wanted}
    for va in wanted:
        needle = va.to_bytes(8, "little")
        pos = blob.find(needle)
        while pos != -1:
            hits[va] += 1
            pos = blob.find(needle, pos + 1)
    return hits


def pdata_functions(blob):
    """`(starts, spans)` from the image's own `.pdata`: declared entry rvas, and `(begin, end)`."""
    image = er_game_image.FlatImage(blob)
    spans = image.functions()
    return frozenset(begin for begin, _end in spans), spans


# The extent rules live in `scripts/function_extent.py` -- one implementation for the whole repo,
# because a second one is the next divergence bug.
_inside_declared_function = function_extent.inside_declared_function


def entry_verdict(hit, pdata_starts=None, va=None, pdata_spans=None):
    """Is this address a function entry, by any positive evidence?

    Three kinds of evidence:

      1. Something names it -- a `call`, a `jmp`, or a stored pointer.
      2. The image's own `.pdata` declares a function to start there. This is the authority, not a
         hint: it is the binary telling you where its functions begin, and the unwinder depends on
         it being right.
      3. `.pdata` declares no function containing it either. That is not the same as (2) failing.

    Judging on (1) alone reads "reached only indirectly" as "mid-function", which flags plenty of
    correct targets: many functions are reached only through a vtable or a runtime-resolved
    pointer and have no literal reference anywhere.

    Case (3) is the leaf. The x64 ABI omits unwind data for a function that allocates no stack
    and calls nothing, so ELDEN RING's small getters have no `.pdata` entry at all. Answering
    them "mid-function" conflates `inside somebody else's function`, which is fatal, with `inside
    nobody's`, which is ordinary.
    """
    total = hit["call"] + hit["jmp"] + hit["ptr"]
    if total:
        return True, f"{hit['call']} call, {hit['jmp']} jmp, {hit['ptr']} ptr"
    if pdata_starts is not None and va is not None and (va - BASE) in pdata_starts:
        return True, "no literal reference, but .pdata declares a function entry here"
    if pdata_spans is not None and va is not None:
        enclosing = _inside_declared_function(va - BASE, pdata_spans)
        if enclosing is None:
            return True, (
                "no literal reference and no .pdata entry, but no declared function contains it "
                "either -- an unwindless leaf, not a mid-function landing"
            )
        return False, (
            f"it is 0x{va - BASE - enclosing[0]:x} bytes inside the function .pdata declares at "
            f"0x{BASE + enclosing[0]:x}"
        )
    return False, "nothing references it and .pdata declares no entry"


def instruction_boundary(blob, va, enclosing):
    """Is `va` even an instruction boundary of the function `.pdata` declares at `enclosing`?

    The discriminator a mid-function flag needs, because "inside a declared function" has two
    causes with opposite fixes: the check is wrong (`.pdata` merged a tail, or declared an extent
    covering a second real entry), or the data is wrong (somebody put an address on the allowlist
    that is not a function). Decoding forward from the declared start separates them: an address
    that is not on an instruction boundary cannot be a function entry under any boundary source.

    Measured on the address that prompted this (2026-08-31), and the same on 2.7.2.0:
    `0x140001050` sits 4 bytes into the 7-byte `lea rdx, [rip + disp]` at `0x14000104c`, inside
    `0x140001040` (`.pdata` 0x1040..0x111c).

    Returns `(on_boundary, detail)`. `on_boundary` is None when the decode desynchronised or ran
    off the end before reaching `va`, which is an answer about the decode and must not be read as
    either verdict.
    """
    start = BASE + enclosing[0]
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = False
    previous = None
    for insn in md.disasm(blob[enclosing[0] : enclosing[1]], start):
        if insn.address == va:
            return True, f"on an instruction boundary of 0x{start:x} ({insn.mnemonic} {insn.op_str})"
        if insn.address > va:
            return False, (
                f"NOT an instruction boundary: it is {va - previous.address} bytes into the "
                f"{previous.size}-byte `{previous.mnemonic} {previous.op_str}` at "
                f"0x{previous.address:x}"
            )
        previous = insn
    return None, f"the decode of 0x{start:x} did not reach it -- boundary unknown"


def trampoline_walk(blob, va):
    """MinHook's own trampoline decision, and how much of the function it consumes.

    `(ok, relocated_bytes, patched_above, reason)`. A port of `CreateTrampolineFunction` from
    `vendor/minhook/src/trampoline.c` rather than a summary of it. The branch order, the `jmpDest`
    internal-branch tracking, the outright loop refusal and the padding fallbacks are all its own.
    A hand-reasoned version written first got two rules wrong in opposite directions (it stopped
    the byte count at a relocated `jmp` where MinHook counts through it, and missed that a short
    function can still be hooked when uniform padding follows it).

    `relocated_bytes` is `oldPos` at the moment the walk finishes: the bytes of the original
    function copied into the trampoline, and the offset the trampoline's trailing jump returns
    into. It is meaningless when `ok` is False and is returned as 0 there.
    """
    off = va - BASE
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True
    old_pos = 0  # MinHook's oldPos: bytes of the original function consumed so far
    jmp_dest = 0  # furthest destination of a branch that stays inside the five patched bytes
    relocated = 0  # bytes actually copied out (oldPos at the moment the walk finished)
    finished = False
    n_ip = 0
    while not finished:
        addr = va + old_pos
        decoded = list(md.disasm(blob[off + old_pos : off + old_pos + 16], addr, count=1))
        if not decoded:
            # HDE's `F_ERROR`. MinHook returns false without touching the target.
            return False, 0, False, f"undecodable byte at 0x{addr:x}, {old_pos}B in"
        insn = decoded[0]
        length = insn.size
        copy_size = length
        opcode, opcode2, modrm = insn.opcode[0], insn.opcode[1], insn.modrm
        if old_pos >= JMP_REL_SIZE:
            # Past the patch window: MinHook stops copying and jumps back into the original.
            finished = True
            relocated = old_pos
            copy_size = JMP_ABS_SIZE
        elif (modrm & 0xC7) == 0x05:
            # RIP-relative operand. Rewritten in place at the same length; an indirect JMP
            # (FF /4) also ends the function.
            if opcode == 0xFF and (modrm >> 3) & 7 == 4:
                finished = True
                relocated = old_pos + length
        elif opcode == 0xE8:
            copy_size = CALL_ABS_SIZE  # direct relative call, widened to an absolute one
        elif (opcode & 0xFD) == 0xE9:
            # Direct relative JMP (EB or E9). One that lands inside the patched bytes is copied
            # as-is and merely raises jmpDest; one that leaves ends the function.
            dest = _branch_target(insn)
            if dest is not None and va <= dest < va + JMP_REL_SIZE:
                jmp_dest = max(jmp_dest, dest)
            else:
                copy_size = JMP_ABS_SIZE
                finished = addr >= jmp_dest
                if finished:
                    relocated = old_pos + length
        elif (opcode & 0xF0) == 0x70 or (opcode & 0xFC) == 0xE0 or (opcode2 & 0xF0) == 0x80:
            # Direct relative Jcc, and the loop family that shares its encoding shape.
            dest = _branch_target(insn)
            if dest is not None and va <= dest < va + JMP_REL_SIZE:
                jmp_dest = max(jmp_dest, dest)
            elif (opcode & 0xFC) == 0xE0:
                # LOOPNZ/LOOPZ/LOOP/JCXZ/JECXZ leaving the window: MinHook returns false outright,
                # because an 8-bit displacement cannot be widened to reach out of the trampoline.
                return False, 0, False, (
                    f"{insn.mnemonic} at 0x{addr:x} branches out of the patch window, and "
                    "MinHook refuses LOOPNZ/LOOPZ/LOOP/JCXZ/JECXZ rather than relocating them"
                )
            else:
                copy_size = JCC_ABS_SIZE
        elif (opcode & 0xFE) == 0xC2:
            # RET (C2 or C3). Ends the function unless an earlier branch reaches past it.
            finished = addr >= jmp_dest
            if finished:
                relocated = old_pos + length
        if addr < jmp_dest and copy_size != length:
            return False, 0, False, (
                f"the instruction at 0x{addr:x} has to change length to relocate, and a branch "
                "inside the patch window jumps past it"
            )
        n_ip += 1
        if n_ip > MAX_TRAMPOLINE_IPS:
            return False, 0, False, f"more than {MAX_TRAMPOLINE_IPS} instructions in the patch window"
        old_pos += length
    patched_above = False
    if old_pos < JMP_REL_SIZE and not is_code_padding(blob, off + old_pos, JMP_REL_SIZE - old_pos):
        # The function is shorter than the JMP and is not followed by padding to spill into.
        if old_pos < JMP_REL_SHORT_SIZE and not is_code_padding(
            blob, off + old_pos, JMP_REL_SHORT_SIZE - old_pos
        ):
            return False, 0, False, f"only {old_pos}B long, with no padding after it for even a short jump"
        # Last resort: put the long jump in the padding above the entry and leave a 2-byte hop.
        # `IsExecutableAddress` is not modelled -- everything in a flat de-Arxan'd .text image
        # would answer yes -- so this arm is as permissive as the port gets.
        if not is_code_padding(blob, off - JMP_REL_SIZE, JMP_REL_SIZE):
            return False, 0, False, (
                f"only {old_pos}B long, the bytes after it are not padding, and the {JMP_REL_SIZE}B "
                f"above 0x{va:x} are not padding either, so there is nowhere to put the jump"
            )
        patched_above = True
    return True, relocated, patched_above, ""


def body_end(blob, va):
    """The rva one past the last byte of the function at `va`, or None if it cannot be told.

    `scripts/function_extent.body_end`, with this file's own scan cap. Without it the branch scan
    reads the next function, and not even in phase: the gaps between functions in a de-Arxan'd
    image hold the deobfuscator's leftover bytes rather than a uniform `cc`/`90` run, so the decode
    desynchronises on the way out and manufactures instructions that were never assembled --
    including branches. `BRANCH_SCAN_CASES` keeps the measured instance as a fixture.
    """
    return function_extent.body_end(blob, va, limit=BRANCH_SCAN_BYTES)


def patch_safe(blob, va):
    """Will MinHook build a trampoline here, and does anything jump into the patched bytes?

    The first half is `trampoline_walk`, the port of MinHook's own `CreateTrampolineFunction`.

    The second half is not MinHook. MinHook never looks at the rest of the body, so it cannot see
    a branch from later in the function landing on bytes 1..4 of the prologue -- which after the
    patch are the operand of a JMP. That control transfer faults into an address in no module,
    with no unwind. It is checked here because nothing else checks it.

    The scan stops at the end of the function. Only this function's own branches can be read as
    evidence about this function's prologue; bytes past its end belong to someone else.
    """
    off = va - BASE
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True
    ok, relocated, patched_above, why = trampoline_walk(blob, va)
    if not ok:
        return False, why
    # Exactly the bytes MinHook overwrites, and no more. Byte 0 is the JMP's own opcode, and a
    # branch there is the ordinary case -- it goes through the hook. Bytes 1..4 are the operand,
    # and a branch into them executes an address. Bytes at 5 and beyond still hold their original
    # instructions, and a branch to one of them merely bypasses the hook.
    hot = range(va + 1, va + JMP_REL_SIZE)
    end = body_end(blob, va)
    # BRANCH_SCAN_BYTES survives as a cap, not as the window: a body longer than it is read only
    # that far, and an extent that cannot be determined at all falls back to it rather than to
    # nothing.
    limit = off + BRANCH_SCAN_BYTES if end is None else min(end, off + BRANCH_SCAN_BYTES)
    body = blob[off : max(limit, off)]
    for insn in md.disasm(body, va):
        if capstone.CS_GRP_JUMP not in insn.groups:
            continue
        for op in insn.operands:
            if op.type == capstone.x86.X86_OP_IMM and op.imm in hot:
                return False, f"{insn.mnemonic} at 0x{insn.address:x} targets 0x{op.imm:x}"
    if patched_above:
        return True, f"{relocated}B relocatable, via a jump patched into the padding above the entry"
    return True, f"{relocated}B relocatable"


def audit(blob, pairs, column, label):
    """Run the three checks over `pairs`, judging the address in `column` of each."""
    targets = sorted({pair[column] for pair in pairs})
    hits = xref_targets(blob, set(targets))
    starts, spans = pdata_functions(blob)
    bad = []
    previous = None
    print(f"{len(pairs)} targets, judged against {label}\n")
    for pair in sorted(pairs, key=lambda p: p[column]):
        va = pair[column]
        flags = []
        ok, why = entry_verdict(hits[va], starts, va, spans)
        if not ok:
            # Say which of the flag's two causes it is, rather than leaving the reader to guess.
            enclosing = _inside_declared_function(va - BASE, spans)
            if enclosing is not None:
                _on_boundary, boundary_why = instruction_boundary(blob, va, enclosing)
                why = f"{why}; {boundary_why}"
            flags.append(f"MID-FUNCTION ({why})")
        detail = why
        ok, why = patch_safe(blob, va)
        if not ok:
            flags.append(f"PATCH-UNSAFE ({why})")
        else:
            detail += f", {why}"
        if previous is not None and va - previous < OVERLAP_BYTES:
            flags.append(f"OVERLAP (0x{previous:x} is {va - previous}B away)")
        previous = va
        arrow = f"0x{va:x}"
        print(f"{arrow}  {'; '.join(flags) if flags else 'ENTRY-OK'}  [{detail}]")
        if flags:
            bad.append((arrow, "; ".join(flags)))
    print(f"\n{len(bad)} of {len(pairs)} need a look.")
    return bad


# --- regression fixtures for the bounds above ------------------------------------------------
def _unbounded_walk(blob, va):
    """The buggy `patch_safe` walk this file shipped until 2026-08-30, kept as a fixture.

    It adds instruction sizes with no stop condition but the byte count, so it reads straight
    through a `ret` into the padding and the function after it. An address only earns a place in
    `PATCH_SAFE_CASES` as a refusal if this thing accepts it. That is the mutation test, made
    permanent instead of run once by hand.
    """
    off = va - BASE
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True
    covered = 0
    for insn in md.disasm(blob[off : off + 32], va):
        covered += insn.size
        if covered >= PATCH_BYTES:
            break
    return covered >= PATCH_BYTES, f"{covered}B"


# Addresses in the 2.7.2.0 image whose MinHook answer is settled by the bytes themselves. The three
# refusals are the 2.7.2.0 copies of addresses this audit once approved -- the first two were
# `er-reload-trace`'s own hook targets, and the byte counts in their old verdicts are longer than
# the functions. The acceptances are the control: a bound that refuses everything passes the
# refusals for free, so genuine prologues have to keep getting through.
#
# `discriminating` marks the cases the old unbounded walk gets wrong. Every refusal must be one,
# or it is not testing the bound.
PATCH_SAFE_CASES = (
    (0x14067A9D0, False, True, "3-byte leaf `mov eax, ecx / ret`, once read as 8B"),
    (0x14067A8D0, False, True, "4-byte leaf `mov [r8], eax / ret`, once read as 11B"),
    (0x1407ACFB0, False, True, "`push rbp` then a LOOPNE out of the window, which MinHook refuses"
                               " outright"),
    (0x1407AED70, True, False, "a real prologue: the bound must still let this through"),
    (0x140765E80, True, False, "`add rcx, 8` then a tail `jmp`: MinHook counts through the"
                               " relocated jump, so this is 9B and hookable"),
)


def _unbounded_branch_scan(blob, va):
    """The buggy branch scan `patch_safe` shipped until 2026-08-31, kept as a fixture.

    A flat `BRANCH_SCAN_BYTES` from the entry with no notion of where the function ends. A case
    only earns a place in `BRANCH_SCAN_CASES` as a discriminator if this thing gets it wrong.
    """
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True
    off = va - BASE
    hot = range(va + 1, va + JMP_REL_SIZE)
    for insn in md.disasm(blob[off : off + BRANCH_SCAN_BYTES], va):
        if capstone.CS_GRP_JUMP not in insn.groups:
            continue
        for op in insn.operands:
            if op.type == capstone.x86.X86_OP_IMM and op.imm in hot:
                return False, f"{insn.mnemonic} at 0x{insn.address:x} targets 0x{op.imm:x}"
    return True, "no branch into the patched bytes"


# The branch-scan half of `patch_safe`, on addresses whose answer the bytes already settle.
# `GameMan::SetSaveState` is a 14-byte leaf (`mov rax,[rip+..]; mov [rax+0xb80],ecx; ret`). On
# 1.16.2 the junk byte `83` after its `ret` desynchronised an unbounded decode into a phantom
# `jno` back to entry+1 -- a branch into the patch operand that was never assembled -- while the
# 1.17 and 2.7.2.0 copies carry other junk bytes and decode harmlessly. Which build a correct row
# failed on was decided by one leftover byte. The 2.7.2.0 image no longer carries the trap, so the
# discriminator is planted: the 1.16.2 body and its tail written over the 2.7.2.0 copy.
SET_SAVE_STATE_2720 = 0x14067BF90
SET_SAVE_STATE_1162_TAIL = bytes.fromhex(
    "488b0581ec6e038988800b0000c39083488b0571ec6e030f28010f1180e00a0000c3"
)
BRANCH_SCAN_CASES = (
    (SET_SAVE_STATE_2720, False, True, False,
     "GameMan::SetSaveState as 2.7.2.0 carries it: harmless junk, both scans accept"),
    (SET_SAVE_STATE_2720, True, True, True,
     "the same function with the 1.16.2 junk byte `83` planted after its `ret`: the unbounded scan"
     " invents a `jno` back into the patch operand"),
    (0x1407AED70, False, True, False,
     "a real prologue: the bound must not turn the scan into a rubber stamp"),
)


def _planted_set_save_state(blob):
    planted = bytearray(blob)
    at = SET_SAVE_STATE_2720 - BASE
    planted[at : at + len(SET_SAVE_STATE_1162_TAIL)] = SET_SAVE_STATE_1162_TAIL
    return bytes(planted)


def selftest():
    """Assert the byte-dictated fixtures and the deliberate negatives, then audit the allowlist.

    The negative controls are an address two bytes into a known entry, a mid-function address
    driven through the whole audit path, a planted branch into a patch window, and an empty
    licence table.
    """
    # The vacuity control: an empty licence table must be refused, not audited as clean.
    scratch = os.path.join(ROOT, "target", "audit-hook-targets-selftest.rs")
    os.makedirs(os.path.dirname(scratch), exist_ok=True)
    with open(scratch, "w", encoding="utf-8") as handle:
        handle.write("pub(crate) const DETOUR_ALLOWLIST: [u32; 0] = [];\n")
    try:
        rows(scratch)
        raise AssertionError(
            "negative control failed: an empty licence table was accepted, so the gate would "
            "report `0 of 0 need a look` and exit green having examined nothing"
        )
    except Vacuous:
        pass
    finally:
        os.remove(scratch)
    pairs = rows()
    print(f"negative control: an empty licence table is refused; the real one has {len(pairs)} rows")

    path = er_game_image.image_path()
    if path is None:
        raise SystemExit(f"selftest needs the image: {er_game_image.missing_reason()}")
    blob = path.read_bytes()
    for va, want, discriminating, why in PATCH_SAFE_CASES:
        got, detail = patch_safe(blob, va)
        assert got == want, f"0x{va:x} ({why}): patch_safe returned {got} [{detail}], expected {want}"
        # The mutation test, run every time rather than once by hand.
        old, old_detail = _unbounded_walk(blob, va)
        assert (old != want) == discriminating, (
            f"0x{va:x} ({why}): the pre-2026-08-30 walk said {old} [{old_detail}], so this case is "
            f"{'not ' if discriminating else ''}discriminating -- fix the flag or the case"
        )
    discriminators = sum(1 for case in PATCH_SAFE_CASES if case[2])
    assert discriminators >= 3, "too few cases actually exercise the new bound"
    print(f"patch_safe: {len(PATCH_SAFE_CASES)} byte-dictated cases agree, {discriminators} of "
          "them wrong under the walk this replaced")

    planted_tail = _planted_set_save_state(blob)
    for va, planted, want, discriminating, why in BRANCH_SCAN_CASES:
        image = planted_tail if planted else blob
        got, detail = patch_safe(image, va)
        assert got == want, f"0x{va:x} ({why}): patch_safe returned {got} [{detail}], expected {want}"
        old, old_detail = _unbounded_branch_scan(image, va)
        assert (old != want) == discriminating, (
            f"0x{va:x} ({why}): the pre-2026-08-31 scan said {old} [{old_detail}], so this case is "
            f"{'not ' if discriminating else ''}discriminating -- fix the flag or the case"
        )
    assert any(case[3] for case in BRANCH_SCAN_CASES), (
        "no BRANCH_SCAN_CASES row is wrong under the unbounded scan, so none exercises the bound"
    )

    # ...and the bound must not have made the arm toothless. Narrowing a scan can only ever make
    # it accept more, so accepting the leaves above proves nothing on its own. No allowlist row has
    # a genuine branch into its own patch window, so one is made: take an address the audit
    # accepts and write a short jump into the middle of its body, aimed at entry+2. In phase, same
    # length, inside the declared extent.
    donor = 0x1407AED70
    site = donor + 0x2A  # `33 f6` (xor esi, esi), two bytes, and within rel8 range of the entry
    assert blob[site - BASE : site - BASE + 2] == b"\x33\xf6", "the planting site moved"
    planted_body = bytearray(blob)
    displacement = (donor + 2) - (site + 2)
    planted_body[site - BASE : site - BASE + 2] = bytes([0xEB, displacement & 0xFF])
    ok, why = patch_safe(bytes(planted_body), donor)
    assert not ok and f"0x{site:x}" in why and f"0x{donor + 2:x}" in why, (
        f"positive control failed: a `jmp 0x{donor + 2:x}` planted at 0x{site:x} lands on the "
        f"second byte of the JMP MinHook writes over 0x{donor:x}, and patch_safe returned "
        f"{ok} [{why}]. The bound has turned the branch scan into a rubber stamp."
    )
    assert patch_safe(blob, donor)[0], (
        "the control is inert: the unplanted donor must be accepted, or the refusal above is "
        "about something other than the planted branch"
    )
    print(f"branch scan: {len(BRANCH_SCAN_CASES)} byte-dictated cases agree, and a planted "
          f"`jmp` into 0x{donor:x}'s patch window is still refused")

    # Negative control: two bytes into a known entry. Neither the exact-start test nor the
    # unwindless-leaf test may rescue it -- `.pdata` declares a function containing it.
    inside = donor + 2
    hits = xref_targets(blob, {inside})
    starts, spans = pdata_functions(blob)
    ok, why = entry_verdict(hits[inside], starts, inside, spans)
    assert not ok, f"an address inside a function must not read as an entry, got {why}"

    # The positive control, end to end: a mid-function row driven through `audit()`, which must go
    # red, name the containing function, and say the row is not even an instruction boundary.
    # `0x140001050` is 0x10 bytes into the function `.pdata` declares at 0x140001040 and 4 bytes
    # into the 7-byte `lea rdx, [rip + disp]` at 0x14000104c, in 2.7.2.0 as in 1.16.2.
    planted = 0x140001050
    caught = audit(blob, [(planted, planted)], 0, "a planted mid-function row (positive control)")
    assert caught, (
        f"positive control failed: 0x{planted:x} is 4 bytes into a `lea` inside the function "
        "`.pdata` declares at 0x140001040, and the audit passed it"
    )
    _arrow, reason = caught[0]
    assert "MID-FUNCTION" in reason and "0x140001040" in reason, (
        f"the positive control was flagged, but not as a mid-function landing in 0x140001040: {reason}"
    )
    assert "NOT an instruction boundary" in reason, (
        f"the flag must say which of its two causes this is: {reason}"
    )
    print("positive control: a mid-function row is caught and named as bad data")

    # The pointer scan's equivalence, measured on a sample rather than asserted in prose.
    sample = [va for va, _same in pairs[:6]] + [0x140001000, planted]
    reference = _per_address_pointer_scan(blob, set(sample))
    measured = xref_targets(blob, set(sample))
    for va in sample:
        assert measured[va]["ptr"] == reference[va], (
            f"the shared-suffix pointer scan disagrees with the per-address one at 0x{va:x}: "
            f"{measured[va]['ptr']} vs {reference[va]}"
        )
    print(f"pointer scan: shared-suffix and per-address agree on {len(sample)} addresses\n")

    bad = audit(blob, pairs, 0, f"{er_game_image.IMAGE_NAME} (the allowlist)")
    assert not bad, (
        f"{len(bad)} allowlist row(s) cannot carry a detour:\n"
        + "\n".join(f"    {arrow}  {why}" for arrow, why in bad)
        + "\n\nA MID-FUNCTION flag carries its own discriminator -- read to the end of the "
        "parenthesis. `NOT an instruction boundary` means the row is wrong: re-measure it on "
        "eldenring-deobf-1.17.2.bin or drop it. `on an instruction boundary` (or `boundary "
        "unknown`) is the case where `.pdata` may be at fault. A PATCH-UNSAFE row on a short "
        "function is wrong data too: a 3-byte leaf with no uniform padding after it and none "
        "above it can never be detoured. Do not loosen patch_safe to get past this."
    )
    print("\nselftest OK")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    path = er_game_image.image_path()
    if path is None:
        print(f"hook-target audit skipped: {er_game_image.missing_reason()}")
        return 0
    return 1 if audit(path.read_bytes(), rows(), 0, er_game_image.IMAGE_NAME) else 0


if __name__ == "__main__":
    sys.exit(main())
