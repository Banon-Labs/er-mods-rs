#!/usr/bin/env python3
"""Audit the translated 1.17 detour targets OFFLINE, before any of them is installed.

`scripts/verify-rva-map-1170.py` answers "is the code at the mapped address the same function?".
This answers three questions it never asks, each of which corrupts the process rather than
faulting cleanly, and none of which needs the game to run:

  ENTRY     is the 1.17 target a FUNCTION ENTRY, or did the signature re-occur mid-function?
            A mapper finds where bytes recur; an inlined copy of a prologue is a perfectly good
            match and a perfectly fatal detour. Evidence is FORWARD-derived: `call`/`jmp rel32`
            instructions elsewhere in the image whose computed destination is exactly this
            address, plus absolute 8-byte pointers to it (vtable slots, jump tables), plus the
            image's own `.pdata` -- which both DECLARES entries and, by declaring none that
            CONTAINS the address, identifies an unwindless leaf. Those last two are different
            answers and reading them as one flagged good leaf targets as mid-function.
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
import re
import struct
import sys
import tempfile

try:
    import capstone
except ImportError:  # provisioned ephemerally; there is no system pip here
    os.execvp("uv", ["uv", "run", "--with", "capstone", "python3", *sys.argv])

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rva_admission  # noqa: E402 - repo-local, and the sys.path line above is what makes it work

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGE_1170 = os.path.join(ROOT, "eldenring-deobf-1.17.bin")
IMAGE_1162 = os.path.join(ROOT, "eldenring-deobf.bin")
VERIFIED = os.path.join(ROOT, "docs", "recon", "rva-map-1162-to-1170.verified.tsv")
BASE = 0x140000000
# er-game-base/build.rs admits exactly these rows; the audit must see the same set it will
# install, so the admission rule is READ OUT OF build.rs rather than transcribed here.
BUILD_RS = os.path.join(ROOT, "crates", "er-game-base", "build.rs")
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
# The rule that was missing, and what it cost: the previous accumulator had NO stop condition but
# its own byte count, so a function that ENDED inside the five bytes was scored on whatever
# followed it -- padding, then the next function. Two real 1.17 addresses, `er-reload-trace`'s own
# former hook targets, came back "relocatable" on bytes belonging to their neighbours:
# 0x14067a520 (`8b c1 / c3`, a 3-byte leaf, reported 8B) and 0x14067a420 (`41 89 00 / c3`, 4
# bytes, reported 11B). Same decode-past-the-end bug class as the 12 false DIVERGES verdicts and
# 31 false SHAPE-DIFFs of 2026-08-30.
JMP_REL_SIZE = 5  # sizeof(JMP_REL): the relative JMP MinHook writes over the entry
JMP_REL_SHORT_SIZE = 2  # sizeof(JMP_REL_SHORT): the 2-byte hop it writes instead when patching above
JMP_ABS_SIZE = 14  # sizeof(JMP_ABS), the form a relocated JMP grows into
CALL_ABS_SIZE = 16  # sizeof(CALL_ABS)
JCC_ABS_SIZE = 16  # sizeof(JCC_ABS)
MAX_TRAMPOLINE_IPS = 8  # ARRAYSIZE(TRAMPOLINE.oldIPs) -- more instructions than this and it gives up


def is_code_padding(blob, off, size):
    """`IsCodePadding`: `size` bytes of ONE filler, from {0x00, 0x90, 0xCC}.

    Note what this does NOT do, because getting it wrong would build a rule on sand: it does not
    look for a particular filler byte. MSVC pads with 0xCC in some places and 0x90 in others and
    does not make the same choice in 1.16.2 and 1.17, so `0xCC` is not the signal and neither is
    `0x90` -- the signal is a uniform run of any one of the three. MinHook needs the run uniform
    because it overwrites the whole of it.
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


def build_rs_admission():
    """Every admission rule READ OUT of er-game-base/build.rs. Parsed, never transcribed.

    This audit's entire claim is that it judges the same address set `build.rs` is about to
    install, and a copy of build.rs's constants makes that claim only until someone edits one of
    them. That is not hypothetical: on 2026-08-30 the verifier split `IDENTICAL` into
    `BYTE-IDENTICAL` / `IDENTICAL-WHOLE` / `IDENTICAL-LEAF`, build.rs was updated, and this filter
    -- still asking for the single old word -- matched NOTHING. Nothing went red. The calibration
    printed `0 of 0 need a look` and the selftest's `assert bad == 0` passed over an empty set, so
    the gate stayed green while checking no address at all. A sibling agent's simulator hit the
    same drift from the other side and reported DETOUR as 42 instead of 374.

    The parse now lives in ONE place for the whole repo (`scripts/rva_admission.py`, which defers
    in turn to `check-1170-translation-collisions.py`), because four audits each carrying their own
    copy of the parse is the same duplication one level up: this file went on comparing against a
    hard-coded `"IDENTICAL"` for the PREFIX arm even after it had learned to read the exhaustive
    list, and that literal is spelled in a match arm rather than a constant, so only a parse of the
    arm itself can track a rename of it.
    """
    return rva_admission.rules()


def rows(path=None, rule_set=None):
    """(1.16.2 VA, 1.17 VA) exactly as `er-game-base/build.rs::detourable_pairs` filters them.

    `path`/`rule_set` exist so the selftest can point the SAME filter at a synthetic table and
    watch it go red; a gate whose real input is its only input cannot be shown to have teeth.
    """
    rule_set = rule_set or build_rs_admission()
    table = path or VERIFIED
    # `admit_rows` refuses outright when a non-empty table yields no admitted rows -- the vacuity
    # this gate spent part of 2026-08-30 wearing as a green tick.
    admitted, _unknown = rva_admission.admit_rows(
        table,
        rule_set,
        label=f"detourable rows of {os.path.relpath(table, ROOT)}",
    )
    # A SET, because the table records provenance as well as addresses and the same pair can be
    # written twice by two agents who found it two ways -- 0x1409b72b0 and 0x1408c47c0 each appear
    # with two different "how it was mapped" notes. Judged as a list they became two targets zero
    # bytes apart, and the OVERLAP check dutifully reported each as colliding with itself.
    out = {(int(f[0], 16), int(f[1], 16)) for f in admitted}
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


def pdata_functions(blob):
    """`(starts, spans)` from the image's own `.pdata`: declared entry RVAs, and `(begin, end)`."""
    pe = struct.unpack_from("<I", blob, 0x3C)[0]
    nsec = struct.unpack_from("<H", blob, pe + 6)[0]
    optsz = struct.unpack_from("<H", blob, pe + 20)[0]
    off = pe + 24 + optsz
    # Section header layout: name[8], VirtualSize, VirtualAddress, SizeOfRawData, PointerToRawData.
    entry = next(
        (
            blob[off + i * 40 : off + (i + 1) * 40]
            for i in range(nsec)
            if blob[off + i * 40 : off + i * 40 + 8].rstrip(b"\0") == b".pdata"
        ),
        None,
    )
    if entry is None:
        return frozenset(), []
    vsz, vaddr, rsz, _ = struct.unpack_from("<IIII", entry, 8)
    size = max(vsz, rsz)
    starts, spans = set(), []
    for at in range(vaddr, vaddr + size, 12):
        begin, end, _unwind = struct.unpack_from("<III", blob, at)
        if begin and end > begin:
            starts.add(begin)
            spans.append((begin, end))
    spans.sort()
    return frozenset(starts), spans


def pdata_entry_starts(blob):
    """Every RVA the image's own `.pdata` declares as a function start."""
    return pdata_functions(blob)[0]


def _inside_declared_function(rva, spans):
    """The `(begin, end)` of a declared function that STRICTLY contains `rva`, or None."""
    lo, hi = 0, len(spans)
    while lo < hi:
        mid = (lo + hi) // 2
        if spans[mid][0] <= rva:
            lo = mid + 1
        else:
            hi = mid
    if lo and spans[lo - 1][0] < rva < spans[lo - 1][1]:
        return spans[lo - 1]
    return None


def entry_verdict(hit, pdata_starts=None, va=None, pdata_spans=None):
    """Is this address a function ENTRY, by any positive evidence?

    Three kinds of evidence, and each was missing at some point on 2026-08-30:

      1. Something NAMES it -- a `call`, a `jmp`, or a stored pointer. Direct and obvious.
      2. The image's own `.pdata` DECLARES a function to start there. This is the authority, not a
         hint: it is the binary telling you where its functions begin, and the unwinder depends on
         it being right.
      3. `.pdata` declares no function CONTAINING it either. That is not the same as (2) failing,
         and reading the two as one answer is what this check got wrong.

    Judging on (1) alone reads "reached only indirectly" as "mid-function", which is a false
    positive with real cost -- a correct hook target gets flagged and the gate goes red.
    `MMS_CHILD_CLEANUP_RVA` (1.16.2 0xaf5750) was flagged exactly this way while `.pdata` declares
    an EXACT FUNCTION ENTRY of size 0x68 there in both builds. Plenty of functions are reached
    only through a vtable or a runtime-resolved pointer and have no literal reference anywhere.

    Case (3) is the LEAF, and it is the whole point of the distinction. The x64 ABI omits unwind
    data for a function that allocates no stack and calls nothing, so ELDEN RING's small getters
    have no `.pdata` entry at all -- and `er-game-base/build.rs` admits exactly those through its
    `NEITHER-ENTRY` clause, under the `IDENTICAL-LEAF` verdict. Answering them "mid-function"
    conflates `inside somebody else's function`, which is fatal, with `inside nobody's`, which is
    ordinary. `0x1407add70` and `0x140c90080` were both flagged on that conflation.
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
            f"it is 0x{va - BASE - enclosing[0]:x} bytes INSIDE the function .pdata declares at "
            f"0x{BASE + enclosing[0]:x}"
        )
    return False, "nothing references it and .pdata declares no entry"


def trampoline_walk(blob, va):
    """MinHook's own trampoline decision, and HOW MUCH of the function it consumes.

    `(ok, relocated_bytes, patched_above, reason)`. A port of `CreateTrampolineFunction` from
    `vendor/minhook/src/trampoline.c` -- the copy that will be asked to install these detours --
    rather than a summary of it. The branch order, the `jmpDest` internal-branch tracking, the
    outright LOOP refusal and the padding fallbacks are all its own. Reading the C was not
    optional: a hand-reasoned version written first got two rules WRONG in opposite directions,
    refusing five 1.16.2 addresses this project hooks successfully today (it stopped the byte
    count AT a relocated `jmp` where MinHook counts THROUGH it) while missing that a short
    function can still be hooked when uniform padding follows it.

    `relocated_bytes` is `oldPos` at the moment the walk finishes: the bytes of the ORIGINAL
    function copied into the trampoline, and therefore also the offset the trampoline's trailing
    jump returns INTO. It is split out from `patch_safe` so that
    `verify-rva-map-1170.py::PATCH_SITE_IDENTICAL` can ask where the relocated region ENDS
    without re-deriving MinHook's rules a second time, badly. It is meaningless when `ok` is
    False and is returned as 0 there.
    """
    off = va - BASE
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True
    old_pos = 0  # MinHook's oldPos: bytes of the ORIGINAL function consumed so far
    jmp_dest = 0  # furthest destination of a branch that stays inside the five patched bytes
    relocated = 0  # bytes actually copied out (oldPos at the moment the walk finished)
    finished = False
    n_ip = 0
    while not finished:
        addr = va + old_pos
        decoded = list(md.disasm(blob[off + old_pos : off + old_pos + 16], addr, count=1))
        if not decoded:
            # HDE's `F_ERROR`. MinHook returns FALSE without touching the target.
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
            # RIP-relative operand. Rewritten in place at the SAME length; an indirect JMP
            # (FF /4) also ends the function.
            if opcode == 0xFF and (modrm >> 3) & 7 == 4:
                finished = True
                relocated = old_pos + length
        elif opcode == 0xE8:
            copy_size = CALL_ABS_SIZE  # direct relative CALL, widened to an absolute one
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
            # Direct relative Jcc, and the LOOP family that shares its encoding shape.
            dest = _branch_target(insn)
            if dest is not None and va <= dest < va + JMP_REL_SIZE:
                jmp_dest = max(jmp_dest, dest)
            elif (opcode & 0xFC) == 0xE0:
                # LOOPNZ/LOOPZ/LOOP/JCXZ/JECXZ leaving the window: MinHook returns FALSE outright,
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
        # Last resort: put the long jump in the padding ABOVE the entry and leave a 2-byte hop.
        # `IsExecutableAddress` is not modelled -- everything in a flat de-Arxan'd .text image
        # would answer yes -- so this arm is as permissive as the port gets. It matters only for
        # a function under five bytes preceded by five uniform padding bytes.
        if not is_code_padding(blob, off - JMP_REL_SIZE, JMP_REL_SIZE):
            return False, 0, False, (
                f"only {old_pos}B long, the bytes after it are not padding, and the {JMP_REL_SIZE}B "
                f"above 0x{va:x} are not padding either, so there is nowhere to put the jump"
            )
        patched_above = True
    return True, relocated, patched_above, ""


def patch_safe(blob, va):
    """Will MinHook build a trampoline here, and does anything jump INTO the patched bytes?

    The first half is `trampoline_walk`, the port of MinHook's own `CreateTrampolineFunction`.

    The second half is NOT MinHook. MinHook never looks at the rest of the body, so it cannot see
    a branch from later in the function landing on bytes 1..4 of the prologue -- which after the
    patch are the operand of a JMP. That control transfer faults into an address in no module,
    with no unwind. It is checked here because nothing else checks it.
    """
    off = va - BASE
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True
    ok, relocated, patched_above, why = trampoline_walk(blob, va)
    if not ok:
        return False, why
    # Exactly the bytes MinHook overwrites, and no more. Byte 0 is the JMP's own opcode, and a
    # branch there is the ordinary case -- it goes through the hook. Bytes 1..4 are the operand,
    # and a branch into them executes an address. Bytes at 5 and beyond are NOT written even when
    # more than five were relocated, so they still hold their original instructions and a branch
    # to one of them merely bypasses the hook. The previous window ran to the relocated extent
    # and flagged those; on the current tables that difference is one address, 1.17 0x14067b010,
    # where a `jrcxz` at +0x4e targets +6.
    hot = range(va + 1, va + JMP_REL_SIZE)
    body = blob[off : off + BRANCH_SCAN_BYTES]
    for insn in md.disasm(body, va):
        if capstone.CS_GRP_JUMP not in insn.groups:
            continue
        for op in insn.operands:
            if op.type == capstone.x86.X86_OP_IMM and op.imm in hot:
                return False, f"{insn.mnemonic} at 0x{insn.address:x} targets 0x{op.imm:x}"
    if patched_above:
        return True, f"{relocated}B relocatable, via a jump patched into the padding ABOVE the entry"
    return True, f"{relocated}B relocatable"


def wider_rows():
    """Every pair the build knows, from all three sources, as (va_1162, va_1170).

    The 27 byte-verified rows are already detour-safe. These are the rest: the whole-image
    signature pairs and the code-reference carries, which are known to be the right ADDRESS
    and not known to be a safe place to write five bytes. Auditing them is how a row earns
    the second claim.
    """
    out, seen = [], set()
    for name in ("rva-map-1162-to-1170.needed.tsv", "rva-map-1162-to-1170.data.tsv"):
        path = os.path.join(ROOT, "docs", "recon", name)
        if not os.path.exists(path):
            continue
        for line in open(path, encoding="utf-8"):
            if line.startswith("#") or not line.strip():
                continue
            f = line.split("\t")
            if len(f) < 2:
                continue
            try:
                a, b = int(f[0], 16), int(f[1], 16)
            except ValueError:
                continue
            a = a if a >= BASE else a + BASE
            b = b if b >= BASE else b + BASE
            if a in seen:
                continue
            seen.add(a)
            out.append((a, b))
    return out


def promote(pairs):
    """Audit `pairs` and write the ones that pass to the tracked detour-safe list."""
    blob = open(IMAGE_1170, "rb").read()
    hits = xref_targets(blob, {b for _a, b in pairs})
    # BOTH halves of the .pdata answer, and hoisted out of the loop.
    #
    # Passing only `starts` is what made this file call 85 UNWINDLESS LEAVES "mid-function
    # (nothing references it and .pdata declares no entry)" -- the exact conflation
    # `entry_verdict`'s own docstring warns about, and the reason its third argument exists.
    # A leaf has no entry AND no enclosing function; a mid-function landing has no entry and IS
    # inside one, and only the second is fatal. `audit()` has always passed both; `promote()`
    # did not, so the artefact it writes disagreed with the gate that reads the same function.
    # (It also re-parsed the whole `.pdata` table once per candidate row, which is why a 409-row
    # run took minutes.)
    starts, spans = pdata_functions(blob)
    passed, failed = [], []
    previous = None
    for a, b in sorted(pairs, key=lambda p: p[1]):
        entry_ok, entry_why = entry_verdict(hits[b], starts, b, spans)
        patch_ok, patch_why = patch_safe(blob, b)
        overlap = previous is not None and b - previous < OVERLAP_BYTES
        previous = b
        if entry_ok and patch_ok and not overlap:
            passed.append((a, b, entry_why, patch_why))
        else:
            reason = []
            if not entry_ok:
                reason.append(f"mid-function ({entry_why})")
            if not patch_ok:
                reason.append(f"patch-unsafe ({patch_why})")
            if overlap:
                reason.append("overlaps the previous target")
            failed.append((a, b, "; ".join(reason)))
    head = [
        "# 1.16.2 VA\t1.17 VA\tentry evidence\tprologue",
        "# Generated by scripts/audit-1170-hook-targets.py --promote.",
        "#",
        "# Rows from the signature and code-reference maps that ALSO pass the detour checks:",
        "# the 1.17 destination is a real function ENTRY -- by the image's own forward references,",
        "# by .pdata declaring a function start there, or by .pdata declaring no function that",
        "# CONTAINS it (an unwindless leaf) -- and its first five bytes relocate. Those two facts",
        "# are what a MinHook detour needs and what a signature match does not supply.",
        "#",
        "# THIS FILE IS NOT WIRED INTO THE BUILD, and must not be. er-game-base/build.rs reads it",
        "# as AUDITED_DETOURS and then writes `let _ = AUDITED_DETOURS;`, because entry-and-prologue",
        "# is not SEMANTIC identity: HUD_WEAPON_SLOT_UPDATE (0x8d2110) passes every check here and",
        "# is paired with a 1.17 function sharing 18% of its instruction shape. Feeding these rows",
        "# to the detour table put the 2026-08-29 crash straight back. The detour licence comes from",
        "# the byte comparison in rva-map-1162-to-1170.needed-verified.tsv; this is a reading aid",
        "# for the rows that remain refused.",
        "#",
        "# It also audits addresses carried from rva-map-1162-to-1170.data.tsv, which are GLOBALS.",
        "# A detour verdict on a global is meaningless -- nothing hooks a datum -- so read a row",
        "# here as `the entry/prologue checks say this much`, never as `this may carry a hook`.",
        "#",
        "# This exists because allowing un-audited rows to carry detours cost a boot: on",
        "# 2026-08-29 er-armament-icons installed five of them and the game died at the first",
        "# overlay draw, having lived when the same hooks were refused as unmapped.",
    ]
    body = [f"0x{a:x}\t0x{b:x}\t{ew}\t{pw}" for a, b, ew, pw in passed]
    tail = ["#", "# NOT promoted:"] + [f"# 0x{a:x} -> 0x{b:x}\t{why}" for a, b, why in failed]
    out = os.path.join(ROOT, "docs", "recon", "rva-1170-detour-audited.tsv")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(head + body + tail) + "\n")
    print(f"audited {len(pairs)} candidate(s): {len(passed)} promoted, {len(failed)} withheld")
    print(f"wrote {os.path.relpath(out, ROOT)}")
    return 0


def audit(image_path, pairs, column, label):
    """Run the three checks over `pairs`, judging the address in `column` of each."""
    blob = open(image_path, "rb").read()
    targets = sorted({pair[column] for pair in pairs})
    hits = xref_targets(blob, set(targets))
    starts, spans = pdata_functions(blob)
    bad = []
    previous = None
    print(f"{len(pairs)} pairs, judging the {label} address against "
          f"{os.path.basename(image_path)}\n")
    for pair in sorted(pairs, key=lambda p: p[column]):
        va = pair[column]
        flags = []
        ok, why = entry_verdict(hits[va], starts, va, spans)
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
            bad.append((arrow, "; ".join(flags)))
    print(f"\n{len(bad)} of {len(pairs)} need a look.")
    return bad


# --- regression fixtures for the bound above -------------------------------------------------
def _unbounded_walk(blob, va):
    """The BUGGY `patch_safe` walk this file shipped until 2026-08-30, kept as a fixture.

    It adds instruction sizes with no stop condition but the byte count, so it reads straight
    through a `ret` into the padding and the function after it. It is preserved -- and asserted
    against -- so the cases below cannot quietly stop discriminating: an address only earns a
    place in `PATCH_SAFE_CASES` as a REFUSAL if this thing accepts it. That is the mutation test,
    made permanent instead of run once by hand. Reverting `patch_safe` to this shape makes the
    selftest fail, which is the whole point of keeping it.
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


# Addresses whose MinHook answer is settled by the bytes themselves. The three refusals are REAL
# 1.17 addresses this audit APPROVED until 2026-08-30 -- the first two are `er-reload-trace`'s own
# former hook targets, and the byte counts in their old verdicts are longer than the functions.
# The acceptances are the control: a bound that refuses everything passes the refusals for free,
# so genuine prologues have to keep getting through, in BOTH builds.
#
# `discriminating` marks the cases the old unbounded walk got WRONG. Every refusal must be one,
# or it is not testing the new bound.
PATCH_SAFE_CASES = (
    ("1.17", 0x14067A520, False, True, "3-byte leaf `mov eax, ecx / ret`, once read as 8B"),
    ("1.17", 0x14067A420, False, True, "4-byte leaf `mov [r8], eax / ret`, once read as 11B"),
    ("1.17", 0x1407ACB00, False, True, "`push rbp` then a LOOPNE out of the window, which"
                                       " MinHook refuses outright"),
    ("1.17", 0x1407AE8C0, True, False, "a real 1.17 prologue: the bound must still let this"
                                       " through"),
    ("1.16.2", 0x1407AE8C0, True, False, "and the same address in the calibration build"),
    ("1.16.2", 0x140764B80, True, False, "`add rcx, 8` then a tail `jmp`: MinHook counts THROUGH"
                                         " the relocated jump, so this is 9B and hookable"),
)


def selftest():
    """Calibrate on input whose answer is already known, then assert the deliberate negatives.

    The 1.16.2 SOURCE addresses are hooked successfully today, so a check that calls any of them
    mid-function or unpatchable is broken -- which is exactly how the previous ENTRY check was
    caught, and how the first draft of the current PATCH check was caught. The negative controls
    are an address two bytes into a known entry, and the byte-dictated `PATCH_SAFE_CASES`.
    """
    pairs = rows()
    # An empty calibration passes `bad == 0` while checking nothing. That is how this gate spent
    # part of 2026-08-30 green: the verifier renamed its verdicts and `rows()` matched none of
    # them. There is no plausible state of the verified table in which zero rows are detourable.
    assert pairs, (
        f"{os.path.relpath(VERIFIED, ROOT)} yielded NO detourable rows -- the admission rule read "
        f"out of {os.path.relpath(BUILD_RS, ROOT)} matched nothing. Realign it; do not let the "
        "calibration pass on an empty set."
    )

    # THE NEGATIVE CONTROL FOR THE VACUITY CLASS. The control this class needs is NOT "plant a bad
    # row and see it caught" -- a filter matching zero rows catches nothing at all, planted or
    # otherwise, and reports it as clean. So: hand the REAL filter a table whose verdict column
    # carries a word the vocabulary cannot contain, and require the gate to go RED. Run against
    # the pre-2026-08-30 code this assertion fails, because `rows()` returned [] and every
    # downstream `assert not bad` then passed over the empty set.
    with tempfile.TemporaryDirectory() as scratch:
        blind = rva_admission._synthetic(scratch, "IDENTICAL-SHORT")
        try:
            rows(blind)
            raise AssertionError(
                "NEGATIVE CONTROL FAILED: a verdict table whose every row the filter refuses was "
                "accepted as a clean audit. That is the vacuous-quantification bug itself -- the "
                "gate would report `0 of 0 need a look` and exit green having examined nothing."
            )
        except rva_admission.Vacuous as refusal:
            assert "IDENTICAL-SHORT" in str(refusal), (
                "the refusal must name the verdict word the table really carries, or the reader "
                f"cannot tell drift from an empty scope: {refusal}"
            )
        # ...and the control is only meaningful if the same filter ACCEPTS a table written in the
        # live vocabulary. Otherwise it would pass with the filter permanently broken.
        good = rva_admission._synthetic(os.path.join(scratch, "good"), pairs and "BYTE-IDENTICAL")
        assert len(rows(good)) == 8, "the filter rejects its own vocabulary; the control is inert"
    print("negative control: a zero-matching verdict filter is refused, not reported clean")
    images = {"1.17": open(IMAGE_1170, "rb").read(), "1.16.2": open(IMAGE_1162, "rb").read()}
    for build, va, want, discriminating, why in PATCH_SAFE_CASES:
        got, detail = patch_safe(images[build], va)
        assert got == want, (
            f"{build} 0x{va:x} ({why}): patch_safe returned {got} [{detail}], expected {want}"
        )
        # The mutation test, run every time rather than once by hand: a bound nobody has watched
        # fail is a claim, not a check.
        old, old_detail = _unbounded_walk(images[build], va)
        assert (old != want) == discriminating, (
            f"{build} 0x{va:x} ({why}): the pre-2026-08-30 walk said {old} [{old_detail}], so this "
            f"case is {'not ' if discriminating else ''}discriminating -- fix the flag or the case"
        )
    discriminators = sum(1 for case in PATCH_SAFE_CASES if case[3])
    assert discriminators >= 3, "too few cases actually exercise the new bound"
    print(f"patch_safe: {len(PATCH_SAFE_CASES)} byte-dictated cases agree, {discriminators} of "
          "them wrong under the walk this replaced")
    bad = audit(IMAGE_1162, pairs, 0, "1.16.2")
    assert not bad, (
        f"{len(bad)} row(s) the verified table admits as DETOURABLE cannot carry a detour:\n"
        + "\n".join(f"    {arrow}  {why}" for arrow, why in bad)
        + "\n\nRead this failure carefully before touching anything, because it has two very "
        "different causes and only one of them is here.\n"
        "  * If a row is flagged MID-FUNCTION or the byte counts look impossible, the CHECK is "
        "broken -- that is how the previous ENTRY check (20 of 27 known-good addresses called "
        "mid-function) and the first draft of this PATCH check were both caught. Fix it here.\n"
        "  * If a row is flagged PATCH-UNSAFE on a short function, the DATA is wrong and this "
        "file is right. `IDENTICAL-LEAF` grants a detour licence after checking that no branch "
        "targets the patched bytes, but NOT that those five bytes exist: a 3-byte leaf with no "
        "uniform padding after it and none above it can never be detoured, in any build. That "
        "belongs in scripts/verify-rva-map-1170.py, which issues the verdict.\n"
        "Do NOT loosen patch_safe and do NOT drop rows from the calibration to get past this."
    )
    blob = images["1.17"]
    inside = 0x1407AE8C0 + 2
    hits = xref_targets(blob, {inside})
    # Negative control: two bytes into a known entry. Neither the exact-start test nor the
    # unwindless-leaf test may rescue it -- `.pdata` declares a function CONTAINING it.
    starts, spans = pdata_functions(blob)
    ok, why = entry_verdict(hits[inside], starts, inside, spans)
    assert not ok, f"an address inside a function must not read as an entry, got {why}"
    print("\nselftest OK")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument(
        "--promote",
        action="store_true",
        help="audit the signature/reference-mapped rows and record those safe to detour",
    )
    parser.add_argument(
        "--calibrate",
        action="store_true",
        help="run the same checks on the 1.16.2 source addresses, which are known-good",
    )
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if args.promote:
        return promote(wider_rows())
    if args.calibrate:
        return 1 if audit(IMAGE_1162, rows(), 0, "1.16.2") else 0
    return 1 if audit(IMAGE_1170, rows(), 1, "1.17") else 0


if __name__ == "__main__":
    sys.exit(main())
