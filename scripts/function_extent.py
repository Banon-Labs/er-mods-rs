"""Where a function ends, for every offline tool in this repo that decodes forward.

The one rule this module exists to enforce
------------------------------------------
A linear x86-64 decode starting at an address in the de-Arxan'd images is trustworthy only
inside one function. Past the `ret` the decoder is reading inter-function padding and the
deobfuscator's leftover bytes -- not a uniform `cc`/`90` run -- so it RESYNCHRONISES into
plausible-looking instructions that were never assembled, including branches. Anything that
decides a verdict on those bytes is deciding it on noise.

The class has been found five times as of 2026-08-31, each time as a forward decode bounded by
a byte count instead of by a function EXTENT:

  1. `audit-1170-hook-targets.py::patch_safe` read a flat 0x400 from the hook target. On a
     14-byte leaf that is 0x3f2 bytes of the neighbours, and it manufactured a
     `jno 0x14067ac91` -- a branch into the patch operand -- out of one padding byte. The 1.17
     counterpart is the same fourteen bytes and stayed green only because its junk pad byte was
     `28` instead of `83`.
  2. 12 false `DIVERGE` verdicts (2026-08-30), a verifier decoding past a tail call.
  3. 31 false `SHAPE-DIFF`s, same date, same cause.
  4. A trampoline walk that counted bytes past its own `ret`.
  5. `check-singleton-field-offsets.py::_follow` walked five instructions from a
     `mov r64,[rip+SessionManager]` at 1.17 `0x140257d0f`, whose function `.pdata` ends at
     `0x140257d22`, and collected `lea edx,[rax+0x18]` at `0x140257d28` -- Six bytes into the
     next function, where `rax` holds something else. That phantom was the sole evidence for the
     gate's headline claim that "SessionManager gains one field in 1.17". The same walk invented
     `CS::PlayerGameData +0xe5` in both images, 0x17 bytes past the declared end each time --
     symmetric, so it never showed up as a lost field either.

Three sources, most authoritative first
---------------------------------------
  1. `.pdata` declares a function starting at the address -- the linker's own answer, with chunk
     runs merged by `function_regions` so a split function's extent is the whole run rather than
     its first couple of dozen bytes.
  2. `.pdata` declares one containing it. The address is then mid-function, which is a separate
     question, but the enclosing extent is still the right bound for reading its bytes.
  3. Neither: an unwindless leaf. The x64 ABI omits unwind data for a function that allocates no
     stack and calls nothing, so ELDEN RING's small getters have no `.pdata` entry at all --
     `.pdata` is blind across 146,715 holes and a missing entry is not a missing function. Its
     end is DECODED by `leaf_extent` below, the watermark rule that refuses to stop at a `ret`
     some earlier branch reaches past.

Measured over the 425 rows both detour ledgers admit, in both images: 359 declared, 0 enclosed,
66 decoded leaves, 0 unknown. `body_end` returning None is a fallback nothing in the current
tables takes, and a caller that gets None must refuse rather than substitute a byte count.

Why it lives here and not in the tool that first needed it
----------------------------------------------------------
It was written inside `scripts/audit-1170-hook-targets.py` (now `scripts/audit-hook-targets.py`).
Four more tools then needed the same
answer, and a second implementation of extent resolution is the next divergence bug -- the rule's
own history is two earlier wrong versions (first-`ret` truncation, and a padding-only tail-`jmp`
test that walked straight through the de-Arxan'd images' leftover gap bytes). So the primitive
moved to a module with a plain importable name and no import side effects, and every consumer --
`audit-hook-targets.py` included -- imports it from here.

`scripts/check-decode-extent-bounds.py` is the gate that keeps instance six from being written.

Where the primitives came from
------------------------------
The `.pdata` reader, the chunk-run merge, the leaf sweep and the PE section-table reader used to
be imported from `verify-rva-map-1170.py` and `check-ledger-section-kind.py`. Both were tools of
the 1.16.2 -> 1.17 carry and were deleted with it on 2026-10-23, so the four primitives moved
here, unchanged, rather than leaving every extent consumer importing a deleted file.
`verify_rules()` and `section_rules()` still exist and now answer with this module, so a caller
that reached the rules through them keeps working.
"""

from __future__ import annotations

import struct
import sys

BASE = 0x140000000
# A body longer than this is read only this far. It is a cap on top of an extent, never the
# window: `body_end` supplies the window, and this only stops a pathological decode of a
# 0x20000-byte function when a caller has no interest past the first kilobyte.
DEFAULT_SCAN_CAP = 0x400


def verify_rules():
    """The module holding `function_regions` and `leaf_extent`: this one."""
    return sys.modules[__name__]


def section_rules():
    """The module holding `sections`, `classify` and `Refuse`: this one."""
    return sys.modules[__name__]


# --------------------------------------------------------------------------------------------
# `.pdata`: what the image itself declares about where functions are
# --------------------------------------------------------------------------------------------


def runtime_functions(image):
    """Every `RUNTIME_FUNCTION` in the image's exception directory: `(begin, end, unwind)` RVAs.

    x86-64 PE stores one of these per function region for stack unwinding. Regions, not
    functions -- a split function has one per chunk, and `function_regions` merges them.
    """
    e_lfanew = struct.unpack_from("<I", image, 0x3C)[0]
    magic = struct.unpack_from("<H", image, e_lfanew + 24)[0]
    # PE32+ optional header is 112 bytes before the data directories; PE32 is 96.
    directories = e_lfanew + 24 + (112 if magic == 0x20B else 96)
    # Data directory 3 is IMAGE_DIRECTORY_ENTRY_EXCEPTION.
    table_rva, table_size = struct.unpack_from("<II", image, directories + 3 * 8)
    out = []
    for offset in range(table_rva, table_rva + table_size, 12):
        begin, end, unwind = struct.unpack_from("<III", image, offset)
        if begin or end:
            out.append((begin, end, unwind))
    return out


# `UNW_FLAG_CHAININFO` in `UNWIND_INFO`. Byte 0 packs `Version` in bits 0-2 and `Flags` in bits
# 3-7; the flag says "this region is a continuation of another function, and my unwind data is the
# other one's". It is the image's own statement that a `.pdata` entry is not a function.
UNWIND_CHAINED = 0x4


def unwind_is_chained(image, unwind_rva):
    if unwind_rva <= 0 or unwind_rva >= len(image):
        return False
    return bool(((image[unwind_rva] >> 3) & 0x1F) & UNWIND_CHAINED)


def function_regions(image):
    """`({begin: end of the whole chunk run}, {every primary begin})`, chunk runs merged.

    MSVC splits a function into chunks and gives each chunk its own `.pdata` entry. Only the
    first is a real function start; the rest carry `UNW_FLAG_CHAININFO`. Reading the table without
    that flag makes two errors at once: the extent of a chunked function becomes its first chunk
    (measured: 0x16 bytes of a 0x25c-byte run), and a continuation's begin -- the middle of a
    function -- reads as a function start.

    The run is extended only while the next entry is both contiguous with the current end and
    flagged as a continuation. Contiguity alone is not enough: two unrelated functions packed
    flush against each other satisfy it, and merging them would over-extend the body.
    """
    entries = sorted(runtime_functions(image))
    chained = [unwind_is_chained(image, unwind) for _, _, unwind in entries]
    extents, starts = {}, set()
    for index, (begin, end, _unwind) in enumerate(entries):
        if chained[index]:
            continue
        run_end = end
        follower = index + 1
        while follower < len(entries) and chained[follower] and entries[follower][0] == run_end:
            run_end = entries[follower][1]
            follower += 1
        extents[begin] = run_end
        starts.add(begin)
    return extents, starts


def chained_begins(image):
    """Every `.pdata` begin that is a chained continuation rather than a function start."""
    return {
        begin
        for begin, _end, unwind in runtime_functions(image)
        if unwind_is_chained(image, unwind)
    }


def branch_target(insn):
    """The rel8/rel32 destination of a capstone branch, or `None` for anything that is not one.

    Only a branch whose destination is an immediate has a destination this module can see. An
    indirect `jmp` -- a switch dispatch through a table in `.rdata` -- does not, and treating it as
    visible would let the leaf sweep conclude nothing follows it and truncate the function at its
    own switch.
    """
    from capstone.x86 import X86_OP_IMM

    if not insn.mnemonic.startswith("j"):
        return None
    operands = insn.operands
    if operands and operands[0].type == X86_OP_IMM:
        return operands[0].imm
    return None


# Inter-function padding the linker writes. The de-Arxan'd images often hold the deobfuscator's
# leftover bytes between functions instead, which is why a tail `jmp` out of the sweep window ends
# a leaf on its own (see `leaf_extent`).
FUNCTION_PAD_BYTES = frozenset((0xCC, 0x90))


def leaf_extent(image, va, starts, limit=0x100):
    """Where a `.pdata`-less leaf function ends, decoded rather than declared.

    The first `ret` is not the end: a getter with a range check is `cmp / ja / <compute> / ret /
    xor eax,eax / ret`, so the early `ret` is the fast path and the branch target sits behind it.
    The sweep carries a watermark of the furthest forward branch target seen, and only a
    `ret`/tail-`jmp` beyond every such target ends the function. The extent never includes the
    padding that follows. Returns `None` when no terminator is reached inside `limit`.

    A direct `jmp` whose target lies outside the sweep window ends the body on its own: it leaves
    the function by construction, and the bytes after it are unreachable by fall-through whether
    they are padding, deobfuscator leftovers or the next function. An intra-function jump targets
    an address inside the window and still has to satisfy the padding/start test.
    """
    from capstone import CS_ARCH_X86, CS_MODE_64, Cs

    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True
    rva = va - BASE
    if rva < 0 or rva >= len(image):
        return None
    watermark = rva
    for insn in md.disasm(bytes(image[rva : rva + limit]), rva):
        after = insn.address + insn.size
        target = branch_target(insn)
        if target is not None and rva <= target < rva + limit:
            watermark = max(watermark, target)
        if insn.mnemonic == "ret" and after > watermark:
            return after
        if insn.mnemonic == "jmp" and after > watermark:
            if target is not None and not (rva <= target < rva + limit):
                return after
            if after in starts or (after < len(image) and image[after] in FUNCTION_PAD_BYTES):
                return after
    return None


# --------------------------------------------------------------------------------------------
# The PE section table
# --------------------------------------------------------------------------------------------


class Refuse(Exception):
    """A condition under which no verdict is available, as distinct from a clean answer."""


def sections(blob):
    """`[(name, rva, size, executable)]` from the PE headers of a flat image.

    Flat means file offset == RVA for every section, so the headers sit where the loader would
    have put them. Size is `max(virtual, raw)`: a `.data` whose virtual size exceeds its raw size
    has a zero-filled tail that is still inside `.data`, and taking the raw size alone would place
    an address there outside every section.
    """
    if len(blob) < 0x40:
        raise Refuse("image is too short to carry a PE header")
    e_lfanew = struct.unpack_from("<I", blob, 0x3C)[0]
    if e_lfanew + 24 > len(blob) or blob[e_lfanew : e_lfanew + 4] != b"PE\0\0":
        raise Refuse("image does not start with a PE signature at e_lfanew")
    count = struct.unpack_from("<H", blob, e_lfanew + 6)[0]
    opt_size = struct.unpack_from("<H", blob, e_lfanew + 20)[0]
    table = e_lfanew + 24 + opt_size
    out = []
    for i in range(count):
        off = table + i * 40
        if off + 40 > len(blob):
            raise Refuse(f"section header {i} runs past the end of the image")
        name = blob[off : off + 8].rstrip(b"\0").decode("ascii", "replace")
        virtual_size, rva, raw_size, _raw_ptr = struct.unpack_from("<IIII", blob, off + 8)
        characteristics = struct.unpack_from("<I", blob, off + 36)[0]
        # IMAGE_SCN_MEM_EXECUTE: the page protection the loader applies is what decides whether
        # a detour can run there.
        out.append((name, rva, max(virtual_size, raw_size), bool(characteristics & 0x20000000)))
    if not out:
        raise Refuse("image declares no sections")
    return out


def classify(secs, va):
    """`(section name, executable)` for a VA, or `('<outside>', False)`."""
    rva = va - BASE
    for name, start, size, executable in secs:
        if start <= rva < start + size:
            return name, executable
    return "<outside>", False


_SECTIONS = {}


def executable_at(blob, va):
    """Is `va` inside a section this image marks IMAGE_SCN_MEM_EXECUTE?

    The other half of "DO NOT DECODE THIS". A decode that does not establish where the function
    ends and a decode that does not establish that the destination is code at all are the same
    disease, and the second one produced the most extreme instance found so far: the deleted
    `docs/recon/rva-1170-detour-audited.tsv` promoted 85 rows on an "unwindless leaf" clause, and
    all 85 named non-executable memory. The clause cannot fire on a real leaf by accident and it
    could not miss a global: `.pdata` declares no enclosing function for a `.data` address for
    exactly the same reason it declares none for an unwindless leaf, so feeding that ledger the
    GLOBALS table made every global look like a leaf. Four of them were 24 bytes of `00` in the
    `.data` virtual tail; `00 00` decodes as `add byte ptr [rax], al`, and three of those is the
    "6B relocatable" the ledger recorded.

    Returns None when the image cannot be parsed, which callers must treat as "do not know",
    never as "yes".
    """
    key = id(blob)
    cached = _SECTIONS.get(key)
    if cached is None or cached[0] is not blob:
        rules = section_rules()
        try:
            cached = (blob, rules.sections(blob))
        except Exception:  # noqa: BLE001 - Refuse is defined in the sibling module
            cached = (blob, None)
        _SECTIONS[key] = cached
    secs = cached[1]
    if secs is None:
        return None
    _name, executable = section_rules().classify(secs, va)
    return executable


_REGIONS = {}


def declared_functions(blob):
    """`({begin: end}, {begin}, [(begin, end)])` for `blob`: chunk runs merged, once per image.

    Cached because callers ask per address while the parse walks the whole `.pdata` table and the
    sorted span list is 175k entries. Re-parsing `.pdata` per candidate row is what turned a
    409-row run into minutes. The blob itself is kept in the cache value so its `id` cannot be
    recycled under the key while the entry is alive.
    """
    cached = _REGIONS.get(id(blob))
    if cached is None or cached[0] is not blob:
        extents, starts = verify_rules().function_regions(blob)
        cached = (blob, extents, starts, sorted(extents.items()))
        _REGIONS[id(blob)] = cached
    return cached[1], cached[2], cached[3]


def inside_declared_function(rva, spans):
    """The `(begin, end)` of a declared function that strictly contains `rva`, or None."""
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


def body_end(blob, va, limit=DEFAULT_SCAN_CAP):
    """The RVA one past the last byte of the function at or containing `va`.

    `None` when it cannot be told -- which is an answer, and callers must treat it as a refusal
    rather than falling back to a byte count. See the module docstring for the three sources and
    for the five times this was got wrong by not asking.

    `limit` bounds only the decode arm (source 3), where there is no declaration to stop at and
    it is the one thing standing between the sweep and the rest of the image.
    """
    # Not code is not a function. Checked first, because the leaf arm below is exactly where a
    # data address gets mistaken for a function: `.pdata` declares nothing for either, so the
    # sweep happily decodes `.data` and hands back an "extent". See `executable_at` for the 85
    # rows that reached a detour verdict that way.
    if executable_at(blob, va) is False:
        return None
    extents, starts, spans = declared_functions(blob)
    rva = va - BASE
    if rva in extents:
        return extents[rva]
    enclosing = inside_declared_function(rva, spans)
    if enclosing is not None:
        return enclosing[1]
    return verify_rules().leaf_extent(blob, va, starts, limit=limit)


def body_slice_end(blob, va, cap=None):
    """`body_end` expressed as an absolute file offset for slicing, capped.

    Returns the offset one past the function's last byte, or `None` when the extent is unknown.
    `cap`, when given, is an upper bound in bytes from `va` applied on top of the extent -- a cap
    is legitimate, a cap standing in for the extent is the bug.
    """
    end = body_end(blob, va)
    if end is None:
        return None
    if cap is not None:
        return min(end, (va - BASE) + cap)
    return end
