#!/usr/bin/env python3
"""Carry a 1.16.2 DATA address (a global, a vtable, a table) onto 1.17.

WHY THE FUNCTION MAP CANNOT DO THIS
-----------------------------------
`build-1162-1170-function-map.py` pairs functions, and it works because a
function has content to compare. A global has no content: at rest it is eight
zero bytes like every other global, so nothing about the datum itself says
which one it is.

And they DID move. Every `.data` global in the sibling's RVA bundle shifted
between 2.6.2.0 and 2.7.0.0 -- most by +0x4070, `runtime_heap_allocator` by
+0x4080, `multiplay_properties` by +0x4000, and `cs_system_step` BACKWARDS by
-0x17408. So a constant delta is not merely unproven, it is wrong, and the one
that breaks it is not an outlier anybody would have guessed.

Reading a stale global is quiet and then fatal. `GLOBAL_TEX_REPOSITORY_RVA`
went unread-and-unnoticed into `CreateTpfResCap`, which divided by zero at
`eldenring.exe+0x26537d0` and took the game down 894ms after load on
2026-08-29 -- with a perfectly correct, freshly translated function address
sitting one frame up, which is what made it look like the translation's fault.

HOW THIS WORKS INSTEAD
----------------------
A global has no content, but the CODE THAT USES IT does. So: find every
instruction in 1.16.2 `.text` that references the address rip-relatively, map
each of those functions onto 1.17 with the function map, decode the instruction
at the same position in the 1.17 function, and read where ITS displacement
points. Every reference casts a vote.

Agreement across independent call sites is the evidence. A single unopposed
vote is reported as WEAK rather than silently promoted, because one reference
inside a function that happens to have been edited is exactly how a confident
wrong address gets produced.

Calibrated against the eleven `.data` fields whose 1.16.2 and 1.17 values are
both known from the sibling's own generator -- including `cs_system_step`,
whose backwards move any delta-based method gets wrong.
"""

from __future__ import annotations

import argparse
import re
import struct
import sys
from pathlib import Path

BASE = 0x140000000
CS_OP_IMM_TYPE: tuple = ()
CHUNK = 1 << 22


def _ensure(module: str):
    try:
        __import__(module)
    except ImportError:
        import os

        if os.environ.get("_MAPDATA_UNDER_UV"):
            raise SystemExit(f"{module} is still missing under uv")
        os.environ["_MAPDATA_UNDER_UV"] = "1"
        os.execvp(
            "uv",
            ["uv", "run", "--with", "capstone", "--with", "numpy", "python3", *sys.argv],
        )


class Image:
    def __init__(self, path: Path):
        self.data = path.read_bytes()
        pe = struct.unpack_from("<I", self.data, 0x3C)[0]
        nsec = struct.unpack_from("<H", self.data, pe + 6)[0]
        optsz = struct.unpack_from("<H", self.data, pe + 20)[0]
        off = pe + 24 + optsz
        self.sections = {}
        for i in range(nsec):
            e = self.data[off + i * 40 : off + (i + 1) * 40]
            name = e[:8].rstrip(b"\0").decode("latin1")
            vsz, va, rsz, _ = struct.unpack_from("<IIII", e, 8)
            self.sections.setdefault(name, (va, max(vsz, rsz)))
        self.text = self.sections[".text"]
        self.pdata = self.sections[".pdata"]

    def function_starts(self) -> list[int]:
        va, size = self.pdata
        out = []
        for off in range(va, va + size, 12):
            begin, end, _ = struct.unpack_from("<III", self.data, off)
            if begin and end > begin and end - begin <= 0x20000:
                out.append(begin)
        out.sort()
        return out


# Bytes that can follow a rip-relative displacement before the instruction ends. The displacement
# is rip-relative to the END of the instruction, so a trailing immediate shifts the arithmetic by
# its own width -- and an immediate is not exotic here, it is the normal encoding for setting or
# testing a flag:
#
#     C6 05 dd dd dd dd 01     mov byte ptr [rip+dd], 1     <- trailing imm8
#     80 3D dd dd dd dd 00     cmp byte ptr [rip+dd], 0     <- trailing imm8
#     C7 05 dd dd dd dd ....   mov dword ptr [rip+dd], imm32
#
# Scanning only tail 4 therefore finds every READ of a global and misses every write-an-immediate
# and compare-against-an-immediate. That is not a uniform loss: it lands hardest on exactly the
# globals that are single BYTE FLAGS, whose entire use is `mov [x],1` / `cmp [x],0`. Measured
# 2026-08-29 on `TITLE_GLOBAL_ACCEPT_BYTE_RVA 0x4589bdc`, the zero-input title-advance flag: tail 4
# found NOTHING and the address was reported "no usable reference", while tail 5 finds its real
# references. The mod wrote to the stale 1.16.2 address for the whole of 1.17 and the title menu
# never opened.
IMMEDIATE_TAILS = (4, 5, 6, 8)


def references(image: Image, target: int) -> list[int]:
    """Byte offsets in .text whose 4-byte displacement could point at `target`.

    A rip-relative displacement `d` stored at offset `i` addresses `i + tail + d`, where `tail` is
    the number of bytes between the displacement and the end of the instruction. Every plausible
    tail is scanned (see `IMMEDIATE_TAILS`), which is one vectorised pass each rather than decoding
    forty-three megabytes of instructions.

    A wider scan admits candidates whose bytes happen to look right, so these are CANDIDATES only:
    `instruction_index` decodes each one and discards any whose instruction does not genuinely
    address `target`.
    """
    import numpy as np

    va, size = image.text
    hits: set[int] = set()
    for start in range(va, va + size, CHUNK):
        stop = min(start + CHUNK + 4, va + size)
        raw = np.frombuffer(image.data[start:stop], dtype=np.uint8).astype(np.uint32)
        if raw.size < 5:
            continue
        dw = raw[0:-3] | (raw[1:-2] << 8) | (raw[2:-1] << 16) | (raw[3:] << 24)
        idx = np.arange(start, start + dw.size, dtype=np.uint32)
        for tail in IMMEDIATE_TAILS:
            want = np.uint32((target - tail) & 0xFFFFFFFF) - idx
            for hit in np.nonzero(dw == want)[0]:
                hits.add(start + int(hit))
    return sorted(hits)


def call_references(image: Image, target: int) -> list[int]:
    """Offsets of the rel32 in every `call`/`jmp` in .text whose target is `target`.

    A rel32 branch is relative to the END of the instruction, and both `E8 rel32` and `E9 rel32`
    put the rel32 last, so the arithmetic is the same as a trailing displacement: the four bytes
    at `i` name `target` when `i + 4 + rel32 == target`. Candidates only -- `call_index` decodes
    them and discards anything that is not really a branch to `target`.
    """
    import numpy as np

    va, size = image.text
    hits: set[int] = set()
    for start in range(va, va + size, CHUNK):
        stop = min(start + CHUNK + 4, va + size)
        raw = np.frombuffer(image.data[start:stop], dtype=np.uint8).astype(np.uint32)
        if raw.size < 5:
            continue
        dw = raw[0:-3] | (raw[1:-2] << 8) | (raw[2:-1] << 16) | (raw[3:] << 24)
        idx = np.arange(start, start + dw.size, dtype=np.uint32)
        want = np.uint32((target - 4) & 0xFFFFFFFF) - idx
        for hit in np.nonzero(dw == want)[0]:
            hits.add(start + int(hit))
    return sorted(hits)


def call_index(md, image: Image, func: int, rel_at: int, target: int) -> int | None:
    """Which instruction of `func` is the branch whose rel32 sits at `rel_at` and reaches `target`."""
    window = image.data[func : rel_at + 16]
    for n, insn in enumerate(md.disasm(window, BASE + func)):
        pos = insn.address - BASE - func
        if insn.mnemonic in ("call", "jmp") and func + pos + insn.size - 4 == rel_at:
            operands = insn.operands
            if len(operands) != 1 or operands[0].type != CS_OP_IMM_TYPE[0]:
                return None
            return n if operands[0].imm - BASE == target else None
        if func + pos > rel_at:
            return None
    return None


def call_target_of(md, image: Image, func: int, index: int) -> int | None:
    """Where instruction `index` of `func` branches to, as an RVA."""
    window = image.data[func : func + 0x800]
    for n, insn in enumerate(md.disasm(window, BASE + func)):
        if n == index:
            if insn.mnemonic not in ("call", "jmp"):
                return None
            operands = insn.operands
            if len(operands) != 1 or operands[0].type != CS_OP_IMM_TYPE[0]:
                return None
            return operands[0].imm - BASE
    return None


def carry_code(md, old: Image, new: Image, fmap: dict[int, int], target: int):
    """Carry a `.text` address by its CALLERS rather than by its own bytes.

    The body-signature mappers -- this repo's function map and
    `map-rvas-1162-to-1170.py` -- both identify a function by what it looks like, so both go
    silent on the one case that matters most: a function whose body genuinely CHANGED in 1.17.
    Measured 2026-08-29, six addresses the running game refused were absent from the 128,603-row
    function map and unresolvable by masked signature, including the now-loading helper `Update`
    that the loading bar reads (1,513 refusals in a single boot).

    A caller is different evidence. If the CALLER maps, the call instruction at the same index in
    the 1.17 caller points at wherever the callee moved to -- and that holds however much the
    callee itself was rewritten. Every caller votes, exactly as the data path votes.
    """
    old_starts = old.function_starts()
    votes: dict[int, int] = {}
    seen = 0
    for rel_at in call_references(old, target):
        func = enclosing(old_starts, rel_at)
        if func is None:
            continue
        index = call_index(md, old, func, rel_at, target)
        if index is None:
            continue
        if func not in fmap:
            continue
        seen += 1
        moved = call_target_of(md, new, fmap[func], index)
        if moved is not None:
            votes[moved] = votes.get(moved, 0) + 1
    if not votes:
        # Say WHY there is no caller: "the callers are unmapped" and "nothing branches here at
        # all" are different problems with different next steps -- improve the function map, or
        # go looking for a vtable slot / runtime-built function pointer.
        cands = len(call_references(old, target))
        decoded = 0
        for rel_at in call_references(old, target):
            func = enclosing(old_starts, rel_at)
            if func is None or call_index(md, old, func, rel_at, target) is None:
                continue
            decoded += 1
            # Name the site. "The caller is unmapped" is only actionable if you are told WHICH
            # caller, so the next step -- map that one function -- is one command away.
            print(f"    branch at 0x{rel_at:x} in fn 0x{func:x}  {'IN MAP' if func in fmap else 'NOT IN FUNCTION MAP'}")
        return None, (
            f"no usable caller ({cands} candidate branch site(s), {decoded} real, {seen} in the map)"
        ), votes
    best = max(votes, key=lambda k: votes[k])
    if len(votes) > 1:
        return best, f"CONTESTED {len(votes)} answers from {seen} callers", votes
    if votes[best] < 2:
        return best, f"WEAK (one caller of {seen})", votes
    return best, f"agreed by {votes[best]} callers", votes


def enclosing(starts: list[int], rva: int) -> int | None:
    import bisect

    i = bisect.bisect_right(starts, rva) - 1
    return starts[i] if i >= 0 else None


def instruction_index(md, image: Image, func: int, disp_at: int, target: int) -> tuple[int, int] | None:
    """`(instruction index, byte offset from function start)` for the reference at `disp_at`.

    BOTH anchors are returned because neither survives on its own, and trusting only the index
    silently loses references. `displacement_of` re-reads the paired 1.17 function by walking to
    the SAME instruction index -- so if any earlier instruction changed length, the walk arrives at
    a different instruction and the reference is dropped or, worse, votes for whatever that
    instruction points at. Measured 2026-08-30 on `RETURN_TITLE_FINAL_FUNCTOR_GLOBAL_FLAG_RVA`
    (0x3d6c5e8) and `SAVE_SERIALIZE_BYTES_RVA` (0x3d69920): both enclosing functions ARE in the
    map, and both were reported "no usable reference" purely because index #271 and #84 land
    elsewhere in 1.17 while the BYTE OFFSET of the reference is unchanged.

    The target check is what makes a multi-tail candidate scan safe. A candidate offset only means
    "these four bytes would be the right displacement IF the instruction ended a certain number of
    bytes later"; decoding says where the instruction really points, and a candidate that lands on
    a real displacement pointing somewhere else is a coincidence, not a reference.
    """
    window = image.data[func : disp_at + 16]
    for n, insn in enumerate(md.disasm(window, BASE + func)):
        pos = insn.address - BASE - func
        if insn.disp_size == 4 and func + pos + insn.disp_offset == disp_at:
            reaches = insn.address - BASE + insn.size + insn.disp
            return (n, pos) if reaches == target else None
        if func + pos > disp_at:
            return None
    return None


def displacement_of(md, image: Image, func: int, index: int, at_offset: int | None = None) -> int | None:
    """Where the paired instruction in `func` points, rip-relatively.

    Anchored on the BYTE OFFSET from the function start when one is supplied, falling back to the
    instruction INDEX. Byte offset is the better anchor: an instruction that changes length shifts
    every later index by one but leaves the offsets of everything before it alone, and a patch
    usually edits one instruction rather than inserting one. Neither anchor is sound alone, so a
    disagreement between them is not resolved here -- the offset simply wins, and the vote across
    independent references is what catches a bad answer.
    """
    window = image.data[func : func + 0x400]
    by_index = None
    for n, insn in enumerate(md.disasm(window, BASE + func)):
        pos = insn.address - BASE - func
        if at_offset is not None and pos == at_offset and insn.disp_size == 4:
            return insn.address - BASE + insn.size + insn.disp
        if n == index and insn.disp_size == 4:
            by_index = insn.address - BASE + insn.size + insn.disp
    return by_index


def carry(md, old: Image, new: Image, fmap: dict[int, int], target: int):
    old_starts, new_starts = old.function_starts(), new.function_starts()
    votes: dict[int, int] = {}
    seen_functions = 0
    for disp_at in references(old, target):
        func = enclosing(old_starts, disp_at)
        if func is None or func not in fmap:
            continue
        found = instruction_index(md, old, func, disp_at, target)
        if found is None:
            continue
        index, at_offset = found
        seen_functions += 1
        moved = displacement_of(md, new, fmap[func], index, at_offset)
        if moved is not None:
            votes[moved] = votes.get(moved, 0) + 1
    if not votes:
        return None, "no usable reference", votes
    best = max(votes, key=lambda k: votes[k])
    if len(votes) > 1:
        return best, f"CONTESTED {len(votes)} answers from {seen_functions} references", votes
    if votes[best] < 2:
        return best, f"WEAK (one reference of {seen_functions})", votes
    return best, f"agreed by {votes[best]} references", votes



# --- RTTI RESCUE for vtables ----------------------------------------------------------------
# Reference voting needs two agreeing references, and a vtable referenced from only one place is
# withheld even when its identity is not in doubt. A vtable carries its own name: MSVC puts a
# CompleteObjectLocator at `vtable[-1]` whose type descriptor holds the mangled class. If the SAME
# mangled name sits at the candidate in the new image and at the source in the old one -- and at
# neither of the crossed positions -- that is a unique-name match, which is STRONGER evidence than
# any number of agreeing displacements.
#
# Measured 2026-08-29: `FUNCTOR_VTABLE_RVA 0x2ac3ea8 -> 0x2ac6f28` was dropped as WEAK (one
# reference), and RTTI confirms it outright -- both ends carry
# `.?AV?$_Func_impl@V<lambda_e1e7...>@@...PEAVMenuWindow@CS@@AEAVSceneProxy@5@@std@@`, a name that
# occurs once per image.
RTTI_TYPE_DESCRIPTOR_OFFSET = 0x0C
RTTI_NAME_OFFSET = 0x10


def rtti_class_name(image: bytes, vtable_rva: int, image_base: int = 0x140000000) -> str | None:
    """The mangled RTTI class name for a vtable at `vtable_rva`, or None if that is not a vtable."""
    if not 8 <= vtable_rva < len(image):
        return None
    locator = int.from_bytes(image[vtable_rva - 8 : vtable_rva], "little")
    if not image_base <= locator < image_base + len(image):
        return None
    locator -= image_base
    if locator + RTTI_TYPE_DESCRIPTOR_OFFSET + 4 > len(image):
        return None
    descriptor = int.from_bytes(
        image[locator + RTTI_TYPE_DESCRIPTOR_OFFSET : locator + RTTI_TYPE_DESCRIPTOR_OFFSET + 4],
        "little",
    )
    start = descriptor + RTTI_NAME_OFFSET
    if not 0 < start < len(image):
        return None
    end = image.find(b"\0", start, start + 256)
    if end < 0:
        return None
    name = image[start:end]
    return name.decode("ascii", "replace") if name.startswith(b".?A") else None


def rtti_confirms(old_image: bytes, new_image: bytes, src_rva: int, dst_rva: int) -> str | None:
    """The shared mangled name when `src` in the old image and `dst` in the new are the same class.

    Requires the crossed positions NOT to carry that name, so a region that happens not to have
    moved cannot pass by accident.
    """
    src_name = rtti_class_name(old_image, src_rva)
    if src_name is None or src_name != rtti_class_name(new_image, dst_rva):
        return None
    if rtti_class_name(new_image, src_rva) == src_name or rtti_class_name(old_image, dst_rva) == src_name:
        return None
    return src_name


CONST = re.compile(r"const\s+([A-Z0-9_]*RVA[A-Z0-9_]*)\s*:\s*usize\s*=\s*(0x[0-9a-fA-F_]+)")
# `pub const FOO_RVA: usize = SomeEnum::Variant as usize;` -- the value lives on the enum, so a
# literal scan never sees it. SESSION_SINGLETON_144588E98_RVA is written this way, wrapped across
# two lines, and its absence from this map stalled the 1.17 autoload at `session=0x0`: the title
# owner was found, state 10/10, and core readiness then waited forever on a singleton whose
# address had no 1.17 mapping. It maps cleanly once seen -- 0x4588e98 -> 0x458cf18, 28 agreeing
# references -- so the only thing missing was the scanner looking for this spelling.
ALIAS = re.compile(r"const\s+([A-Z0-9_]*RVA[A-Z0-9_]*)\s*:\s*usize\s*=\s*(\w+)::(\w+)\s+as\s+usize")
VARIANT = re.compile(r"^\s*(\w+)\s*=\s*(0x[0-9a-fA-F_]+)\s*,", re.M)
BOUND = re.compile(r"_(MIN|MAX|BOUND|BASE|SIZE|LEN|LENGTH|COUNT|END|START|STRIDE|ALIGN)$")
DATA_MAP = "docs/recon/rva-map-1162-to-1170.data.tsv"

# BRACKET-AND-SHAPE RESCUE: globals whose only reference lives in the dearxan'd image's trampoline
# rubble, where the enclosing `.pdata` entry maps to nine places or none and reference voting has
# nothing to vote with. Each entry here is carried by THREE independent facts, all reproducible
# from this script, and is listed with the command that reproduces them:
#
#   1. BRACKET -- every mapped `.data` anchor on both sides of it moved by the same delta.
#   2. SOURCE  -- in 1.16.2 exactly N sites of one masked instruction shape reach the address.
#   3. TARGET  -- in 1.17 exactly N sites of that identical shape reach the candidate.
#
# Fact 3 alone is weak (the `movzx eax, byte ptr [rip]; ret` getter shape reaches 405 addresses
# image-wide); the bracket is what selects the address and the shape is what confirms it. Kept as
# data rather than a hand-edited row so `--refresh` cannot silently drop it.
SHAPE_RESCUED = {
    # The title's zero-input menu-accept byte. The mod wrote it at the stale 1.16.2 address for
    # the whole of 1.17: `title-accept-byte: set [0x144589bdc]=1` logged success every boot while
    # the store landed on a moved global, so `TitleTopDialog::update` never ran the open-menu
    # registrar and the title menu never opened. Bracketed by five anchors that all move +0x4080
    # (0x4588e98, 0x4589390, 0x45896a8, 0x4589ad8, 0x458b890); one getter stub each side
    # (1.16.2 0xe85f50, 1.17 0xe87d50).
    #   python3 scripts/map-data-rvas-1162-to-1170.py 0x4589bdc --confirm 0x458dc5c
    0x4589BDC: (0x458DC5C, "TITLE_GLOBAL_ACCEPT_BYTE_RVA", "bracket+shape"),
    # The return-title rebuild flag. The single highest-volume refusal in the entire suite:
    # 339,684 in one session, because `write_telemetry.rs` reads it every tick. Its one reference
    # is `mov byte ptr [rip+d], 1` @0x78a990 -- a trailing-immediate encoding, and the setter is a
    # tail-jump target reached by exactly one control transfer in each image.
    #   python3 scripts/map-data-rvas-1162-to-1170.py 0x3d6c5e8 --confirm 0x3d70658
    0x3D6C5E8: (0x3D70658, "RETURN_TITLE_FINAL_FUNCTOR_GLOBAL_FLAG_RVA", "shape"),
    # SAVE_SERIALIZE_BYTES_RVA, refused by er-save-disable. It is GAME_MAN_SINGLETON + 8 in both
    # images -- eight bytes from a 115-vote anchor -- and its one reference is the same
    # instruction at the same byte offset inside a .pdata-paired function.
    #   python3 scripts/map-data-rvas-1162-to-1170.py 0x3d69920 --confirm 0x3d6d990
    0x3D69920: (0x3D6D990, "SAVE_SERIALIZE_BYTES_RVA", "anchor+8+shape"),
}


def already_mapped(repo: Path) -> set[int]:
    """RVAs the function map and the byte verifier already answer for."""
    out: set[int] = set()
    for name in ("docs/recon/rva-map-1162-to-1170.needed.tsv", "docs/recon/rva-map-1162-to-1170.verified.tsv"):
        path = repo / name
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or not line.strip():
                continue
            try:
                value = int(line.split("\t")[0], 16)
            except ValueError:
                continue
            out.add(value - BASE if value >= BASE else value)
    return out


def refresh(md, old: Image, new: Image, fmap: dict[int, int], repo: Path) -> int:
    """Carry every `*_RVA` constant that lives outside .text and is not already answered."""
    text_va, text_size = old.text
    done = already_mapped(repo)
    targets: dict[str, int] = {}
    # Enum variant values first: an alias and its enum are routinely in different files.
    enum_variants: dict[str, int] = {}
    for path in sorted((repo / "crates").glob("**/*.rs")):
        for variant, value in VARIANT.findall(path.read_text(encoding="utf-8", errors="replace")):
            enum_variants.setdefault(variant, int(value.replace("_", ""), 16))

    for path in sorted(repo.glob("crates/**/*.rs")):
        source = path.read_text(encoding="utf-8", errors="replace")
        literals = [(n, int(v.replace("_", ""), 16)) for n, v in CONST.findall(source)]
        for n, _enum, variant in ALIAS.findall(source):
            value = enum_variants.get(variant)
            if value is not None:
                literals.append((n, value))
        for name, rva in literals:
            if BOUND.search(name):
                continue
            if rva in done:
                continue
            # `.text` IS EXCLUDED, and the exclusion was tested rather than assumed.
            #
            # It looked like it should not be. 75 of the 83 addresses the running game asked for
            # and could not be placed are in `.text` but absent from `.pdata` -- leaf functions
            # with no unwind data, structurally invisible to a map built from the function table.
            # And a `call rel32` encodes its target exactly as a rip-relative displacement does,
            # `dword[i] + i + 4`, so the reference scan finds their call sites for free.
            #
            # MEASURED 2026-08-29: allowing them took the table from 304 rows to 329 and killed
            # the game at +145ms, during DLL init, where 304 rows had survived past twenty
            # seconds. The contract this tool advertises -- never wrong, sometimes silent -- is
            # calibrated on eleven `.data` globals. Leaf functions are a class it has never been
            # calibrated on, and the runtime says the vote does not carry them. Re-enabling this
            # needs its own calibration set first.
            if text_va <= rva < text_va + text_size:
                continue
            targets.setdefault(name, rva)

    # The declared constants are not the whole population; the running game is the authority on
    # what is actually reached. See scripts/record-1170-refusals.py.
    observed = repo / "docs/recon/rva-1170-observed-refusals.txt"
    if observed.is_file():
        for line in observed.read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or not line.strip():
                continue
            try:
                rva = int(line, 16)
            except ValueError:
                continue
            if rva == 0 or rva in done or rva in targets.values():
                continue
            if text_va <= rva < text_va + text_size:
                continue
            targets.setdefault(f"(refused at runtime 0x{rva:x})", rva)

    rows, weak, rtti_rescued = [], [], []
    for name, rva in sorted(targets.items(), key=lambda kv: kv[1]):
        moved, note, votes = carry(md, old, new, fmap, rva)
        if moved is None:
            weak.append((name, rva, note))
            continue
        total = sum(votes.values())
        best = votes[moved]
        # A single unopposed reference, or a contested vote without a clear
        # majority, is reported and NOT used. The failure this guards against is
        # not a missing address -- that only costs a feature -- but a confident
        # wrong one, which is what put 0x3d6e278 in the first 2.7.0.0 bundle.
        if best < 2 or best * 5 < total * 3:
            # A vtable can rescue itself: it carries its own mangled class name, and a name that
            # occurs once per image is stronger evidence than any number of agreeing
            # displacements. Only for vtables, and only when the crossed positions do NOT carry
            # the name, so a region that happens not to have moved cannot pass by accident.
            confirmed = rtti_confirms(old.data, new.data, rva, moved)
            if confirmed:
                rtti_rescued.append((name, rva, moved, confirmed))
                rows.append((name, rva, moved, best, total))
                continue
            weak.append((name, rva, f"{note}, winner {best}/{total}"))
            continue
        rows.append((name, rva, moved, best, total))

    # Bracket-and-shape rescues, re-verified here rather than trusted from the table: the shape
    # counts must still match in the two images, so a future patch that changes the referencing
    # code drops the row instead of carrying a stale answer forward.
    shape_rescued = []
    for rva, (moved, name, kind) in sorted(SHAPE_RESCUED.items()):
        if any(row[1] == rva for row in rows):
            continue
        shapes = reference_shapes(md, old, rva)
        src_sites = len(shape_sites(old, shapes).get(rva, [])) if shapes else 0
        dst_sites = len(shape_sites(new, shapes).get(moved, [])) if shapes else 0
        if src_sites and src_sites == dst_sites:
            rows.append((name, rva, moved, src_sites, dst_sites))
            shape_rescued.append((name, rva, moved, kind, src_sites))
            # It failed the vote on the way in -- that is why it is here. Drop the withheld entry
            # so the file does not list the same address as both carried and unusable.
            weak[:] = [entry for entry in weak if entry[1] != rva]
        else:
            weak.append((name, rva, f"{kind} rescue FAILED: {src_sites} source vs {dst_sites} target site(s)"))
    rows.sort(key=lambda row: row[1])

    head = [
        "# 1.16.2 RVA\t1.17 RVA\tconstant\tvotes",
        "# Generated by scripts/map-data-rvas-1162-to-1170.py --refresh.",
        "# Data has no content to compare, so each row is carried by the CODE that references it:",
        "# every rip-relative reference in 1.16.2 .text is mapped onto its 1.17 function and the",
        "# same instruction re-read there. `votes` is agreeing references / total. A row with",
        "# fewer than two agreeing references, or without a clear majority, is listed at the",
        "# bottom as UNUSED rather than promoted -- a missing address costs a feature, a confident",
        "# wrong one cost a boot (0x3d6e278, the first 2.7.0.0 cs_system_step).",
        "# A `rtti` suffix means a VTABLE carried itself: the same mangled class name sits at the",
        "# source in 1.16.2 and the destination in 1.17 and at neither crossed position. A name",
        "# that occurs once per image beats any number of agreeing displacements.",
        "# A `shape` suffix means the address is bracketed by anchors that all moved by the same",
        "# delta AND its one referencing instruction shape reaches the candidate exactly as often",
        "# in 1.17 as it reached the source in 1.16.2 -- the fallback for a global referenced only",
        "# from trampoline rubble, where there is no mappable function to vote with.",
    ]
    rescued_names = {name for name, _rva, _moved, _cls in rtti_rescued}
    shape_names = {name for name, _rva, _moved, _kind, _n in shape_rescued}
    body = [
        f"0x{rva:x}\t0x{moved:x}\t{name}\t"
        + (
            f"{best}/{total} rtti"
            if name in rescued_names
            else f"{best}/{total} shape"
            if name in shape_names
            else f"{best}/{total}"
        )
        for name, rva, moved, best, total in rows
    ]
    tail = ["#", "# UNUSED -- not enough agreement to be worth trusting:"]
    tail += [f"# {name}\t0x{rva:x}\t{note}" for name, rva, note in weak]
    (repo / DATA_MAP).write_text("\n".join(head + body + tail) + "\n", encoding="utf-8")
    for name, rva, moved, cls in rtti_rescued:
        print(f"  rtti rescue: {name} 0x{rva:x} -> 0x{moved:x}  {cls[:90]}")
    for name, rva, moved, kind, sites in shape_rescued:
        print(f"  {kind} rescue: {name} 0x{rva:x} -> 0x{moved:x}  ({sites} matching site(s) each side)")
    print(
        f"wrote {DATA_MAP}: {len(rows)} usable row(s) "
        f"({len(rtti_rescued)} carried by RTTI), {len(weak)} withheld"
    )
    return 0


CALIBRATION = {
    "game_man": (0x3D69918, 0x3D6D988),
    "game_data_man": (0x3D5DF38, 0x3D61F98),
    "field_area_ptr": (0x3D691D8, 0x3D6D248),
    "cs_system_step": (0x3D85680, 0x3D89700),  # corrected: the bundle pattern was ambiguous
    "world_chr_man_dbg_flags": (0x3D661A0, 0x3D6A210),
    "multiplay_properties": (0x3B11230, 0x3B15230),
    "character_type_properties": (0x3B17C00, 0x3B1BC00),
    "runtime_heap_allocator": (0x4842D40, 0x4846DC0),
    "crypto_spi_registry": (0x4843038, 0x48470B8),
    "title_step_state_table": (0x3D71580, 0x3D755F0),
    "global_hinstance": (0x3D85688, 0x3D89708),
}


def explain(md, old: Image, new: Image, fmap: dict[int, int], target: int) -> None:
    """Print the fate of every candidate reference to `target`.

    "no usable reference" is a single word for four different situations, and they call for
    opposite responses: no candidate bytes at all means the global is not addressed
    rip-relatively from `.text` (look for a struct-relative access instead); candidates whose
    enclosing function is absent from the map means the FUNCTION map is the thing to improve;
    candidates that decode to a different address mean the bytes were a coincidence and the
    scan is working as intended. Without this, all four read as "the tool cannot do it".
    """
    old_starts = old.function_starts()
    cands = references(old, target)
    print(f"0x{target:x}: {len(cands)} candidate displacement site(s) in .text")
    tally = {"no enclosing function": 0, "function not in map": 0, "decodes elsewhere": 0, "usable": 0}
    for disp_at in cands:
        func = enclosing(old_starts, disp_at)
        if func is None:
            tally["no enclosing function"] += 1
            continue
        # DECODE BEFORE asking the function map. A candidate that decodes elsewhere is a
        # coincidence and says nothing about map coverage; reporting it as "function not in map"
        # would send the next reader off to improve the function map over bytes that were never a
        # reference at all.
        found = instruction_index(md, old, func, disp_at, target)
        if found is None:
            tally["decodes elsewhere"] += 1
            continue
        index, at_offset = found
        if func not in fmap:
            tally["function not in map"] += 1
            print(f"  0x{disp_at:x}  in fn 0x{func:x}  REAL reference, fn NOT IN FUNCTION MAP")
            continue
        moved = displacement_of(md, new, fmap[func], index, at_offset)
        tally["usable"] += 1
        print(
            f"  0x{disp_at:x}  in fn 0x{func:x} -> 0x{fmap[func]:x}  insn #{index}  "
            f"votes 0x{moved:x}" if moved is not None else
            f"  0x{disp_at:x}  in fn 0x{func:x} -> 0x{fmap[func]:x}  insn #{index}  no displacement there"
        )
    for reason, count in tally.items():
        print(f"  {count:5d}  {reason}")


def reference_shapes(md, image: Image, target: int) -> list[tuple[str, str]]:
    """`(mnemonic+operand shape, masked bytes)` for every instruction that really addresses `target`.

    The displacement is blanked, so what is left is what a patch does not change: the opcode and
    the registers. Two addresses in two builds that are referenced by the SAME multiset of shapes,
    from the same number of sites, are the same datum -- or a coincidence that has to repeat itself
    once per reference.
    """
    starts = image.function_starts()
    out = []
    for disp_at in references(image, target):
        func = enclosing(starts, disp_at)
        if func is None:
            continue
        window = image.data[func : disp_at + 16]
        for insn in md.disasm(window, BASE + func):
            pos = insn.address - BASE - func
            if insn.disp_size == 4 and func + pos + insn.disp_offset == disp_at:
                if insn.address - BASE + insn.size + insn.disp != target:
                    break
                raw = bytearray(insn.bytes)
                for i in range(insn.disp_offset, min(insn.disp_offset + 4, len(raw))):
                    raw[i] = 0
                out.append((f"{insn.mnemonic} {insn.op_str.split(',')[0]}", raw.hex()))
                break
            if func + pos > disp_at:
                break
    return sorted(out)


def shape_search(md, old: Image, new: Image, target: int, lo: int, hi: int) -> int:
    """Find `target`'s 1.17 address by looking for the SAME instruction shape in the new image.

    The last resort, for a global whose only reference lives in a trampoline stub. Reference
    VOTING needs the enclosing function to be mappable; the dearxan'd image's stub regions are
    `jmp`/`int3` rubble that maps to nine places or none, so a getter that lives there can never
    be voted on. But the getter's OWN bytes are still a signature: `movzx eax, byte ptr [rip+d];
    ret` with the displacement blanked. Search the new image for that exact masked shape, read
    where each hit points, and keep the ones landing in the plausible window.

    This is weaker than voting and is reported as such -- one hit in the window is a CANDIDATE,
    several is a genuine ambiguity, none says the shape itself changed. It is offered because the
    alternative for such a global is a delta guess, and a guess that lands on a live neighbouring
    byte is far worse than an honest "unknown".
    """
    shapes = reference_shapes(md, old, target)
    if not shapes:
        print(f"0x{target:x}: no reference to take a shape from")
        return 1
    print(f"0x{target:x}: searching 1.17 for {len(shapes)} reference shape(s), window 0x{lo:x}..0x{hi:x}")
    va, size = new.text
    found: dict[int, list[str]] = {}
    for shape, hexbytes in shapes:
        raw = bytes.fromhex(hexbytes)
        # The blanked displacement bytes are the zero run; everything else must match exactly.
        disp_at = raw.find(b"\x00\x00\x00\x00")
        if disp_at < 0:
            continue
        head, tail = raw[:disp_at], raw[disp_at + 4 :]
        at = new.data.find(head, va, va + size)
        while at >= 0:
            end = at + len(raw)
            if new.data[at + disp_at + 4 : end] == tail:
                disp = int.from_bytes(new.data[at + disp_at : at + disp_at + 4], "little", signed=True)
                points_at = at + len(raw) + disp
                if lo <= points_at <= hi:
                    found.setdefault(points_at, []).append(f"{shape} @0x{at:x}")
            at = new.data.find(head, at + 1, va + size)
    if not found:
        print("  no shape match points into the window -- the referencing code itself changed")
        return 1
    for address, where in sorted(found.items()):
        print(f"  0x{address:x}  from {len(where)} site(s): {', '.join(where[:4])}")
    if len(found) == 1:
        only = next(iter(found))
        print(f"CANDIDATE 0x{only:x} (single shape match in the window; weaker than a reference vote)")
        return 0
    print(f"AMBIGUOUS: {len(found)} addresses match the shape in this window")
    return 1


def shape_sites(image: Image, shapes: list[tuple[str, str]]) -> dict[int, list[str]]:
    """`{address: [site, ...]}` for every masked shape occurrence anywhere in `.text`.

    Deliberately BOUNDARY-FREE: it scans bytes and never decodes from a `.pdata` function start.
    That matters because the addresses this fallback exists for are referenced from the dearxan'd
    image's trampoline rubble, where decoding forward from the enclosing `.pdata` entry
    desynchronises long before it reaches the instruction -- so a boundary-based count reports
    ZERO references to an address that plainly has one.
    """
    va, size = image.text
    found: dict[int, list[str]] = {}
    for shape, hexbytes in shapes:
        raw = bytes.fromhex(hexbytes)
        disp_at = raw.find(b"\x00\x00\x00\x00")
        if disp_at < 0:
            continue
        head, tail = raw[:disp_at], raw[disp_at + 4 :]
        at = image.data.find(head, va, va + size)
        while at >= 0:
            if image.data[at + disp_at + 4 : at + len(raw)] == tail:
                disp = int.from_bytes(
                    image.data[at + disp_at : at + disp_at + 4], "little", signed=True
                )
                found.setdefault(at + len(raw) + disp, []).append(f"{shape} @0x{at:x}")
            at = image.data.find(head, at + 1, va + size)
    return found


def confirm(md, old: Image, new: Image, target: int, candidate: int) -> int:
    """Compare how `target` is referenced in 1.16.2 with how `candidate` is referenced in 1.17.

    The fallback for a global whose only reference sits in a function the function map could not
    pair -- the reference vote has nothing to vote with, but the reference itself is still
    evidence, and so is how rare its shape is.
    """
    shapes = reference_shapes(md, old, target)
    if not shapes:
        print(f"0x{target:x}: no reference to take a shape from")
        return 1
    before = shape_sites(old, shapes).get(target, [])
    everywhere = shape_sites(new, shapes)
    after = everywhere.get(candidate, [])
    # The shape's operand text comes from the 1.16.2 instruction in BOTH lines -- that is inherent
    # to matching on a masked shape, since the displacement is exactly what was blanked. Labelling
    # it plainly, because printed unqualified it reads as though the two images decoded to the
    # same displacement, which would be a much stronger claim than this test actually makes. The
    # site ADDRESSES are per-image and are the real content of these lines.
    print(f"1.16.2 0x{target:x}: {len(before)} site(s) at  {', '.join(before[:4])}")
    print(f"1.17   0x{candidate:x}: {len(after)} site(s) at  {', '.join(after[:4])}")
    print("       (operand text above is the 1.16.2 shape; its displacement is masked by design)")
    print(f"1.17   the same shape reaches {len(everywhere)} distinct address(es) image-wide")
    if before and len(before) == len(after):
        print("CONFIRMED: the shape references the candidate exactly as often as it referenced the source")
        return 0
    print("NOT CONFIRMED: the reference counts differ")
    return 1


def main() -> int:
    _ensure("capstone")
    _ensure("numpy")
    from capstone import CS_ARCH_X86, CS_MODE_64, Cs
    from capstone.x86 import X86_OP_IMM

    global CS_OP_IMM_TYPE
    CS_OP_IMM_TYPE = (X86_OP_IMM,)

    repo = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rvas", nargs="*", help="1.16.2 data RVAs or VAs (hex)")
    ap.add_argument("--old", type=Path, default=repo / "eldenring-deobf.bin")
    ap.add_argument("--new", type=Path, default=repo / "eldenring-deobf-1.17.bin")
    ap.add_argument("--map", type=Path, default=repo / "docs/recon/rva-map-1162-to-1170.functions.tsv")
    ap.add_argument("--refresh", action="store_true", help="rewrite the tracked data map")
    ap.add_argument(
        "--explain",
        action="store_true",
        help="show every candidate reference and why it was kept or dropped",
    )
    ap.add_argument(
        "--confirm",
        metavar="NEW_RVA",
        help="check a PREDICTED 1.17 address by comparing reference shapes in both images",
    )
    ap.add_argument(
        "--shape-search",
        metavar="LO:HI",
        help="last resort: find the 1.17 address by matching the reference instruction's shape, "
        "restricted to this 1.17 RVA window (hex, e.g. 0x458c000:0x458e000)",
    )
    ap.add_argument(
        "--callers",
        action="store_true",
        help="carry a .text address by its CALLERS -- for a function whose body changed, which no "
        "body-signature mapper can identify",
    )
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    for path in (args.old, args.new, args.map):
        if not path.is_file():
            print(f"SKIP: missing {path}")
            return 0

    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True
    old, new = Image(args.old), Image(args.new)
    fmap = {}
    for line in args.map.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        a, b = line.split()[:2]
        fmap[int(a, 16)] = int(b, 16)

    if args.selftest:
        # THE CONTRACT IS "NEVER WRONG", NOT "ALWAYS ANSWERS".
        #
        # A missing address costs a feature: the caller refuses and says so. A WRONG
        # address costs the session -- that is what 0x3d6e278 did, and it looked
        # authoritative the whole way down. So a miss is reported and tolerated; a
        # disagreement fails the run.
        wrong, missed = [], []
        for name, (src, want) in CALIBRATION.items():
            got, note, _votes = carry(md, old, new, fmap, src)
            if got == want:
                status = "ok"
            elif got is None:
                status = "miss"
                missed.append(f"{name} (0x{src:x}): {note}")
            else:
                status = "WRONG"
                wrong.append(f"{name}: got 0x{got:x}, want 0x{want:x} ({note})")
            print(f"  {status:5s} {name:28s} 0x{src:x} -> {got and hex(got)}  [{note}]")
        for line in wrong:
            print(f"SELFTEST FAIL: {line}")
        for line in missed:
            print(f"  unresolved (tolerated): {line}")
        print(
            f"selftest: {len(CALIBRATION) - len(wrong) - len(missed)}/{len(CALIBRATION)} carried, "
            f"{len(missed)} unresolved, {len(wrong)} WRONG"
        )
        return 1 if wrong else 0

    if args.refresh:
        return refresh(md, old, new, fmap, repo)

    if not args.rvas:
        ap.error("give at least one RVA, --refresh, or --selftest")
    if args.callers:
        worst = 0
        for text in args.rvas:
            value = int(text, 16)
            rva = value - BASE if value >= BASE else value
            moved, note, votes = carry_code(md, old, new, fmap, rva)
            if moved is None:
                print(f"0x{rva:x}  ->  UNMAPPED   {note}")
                worst = 1
            else:
                print(f"0x{rva:x}  ->  0x{moved:x}   {note}  delta +0x{moved - rva:x}")
        return worst

    if args.shape_search:
        if len(args.rvas) != 1:
            print("--shape-search takes exactly one 1.16.2 RVA")
            return 2
        value = int(args.rvas[0], 16)
        lo_s, hi_s = args.shape_search.split(":")
        return shape_search(
            md, old, new,
            value - BASE if value >= BASE else value,
            int(lo_s, 16), int(hi_s, 16),
        )

    if args.confirm:
        if len(args.rvas) != 1:
            print("--confirm takes exactly one 1.16.2 RVA")
            return 2
        value = int(args.rvas[0], 16)
        cand = int(args.confirm, 16)
        return confirm(
            md,
            old,
            new,
            value - BASE if value >= BASE else value,
            cand - BASE if cand >= BASE else cand,
        )

    if args.explain:
        for text in args.rvas:
            value = int(text, 16)
            explain(md, old, new, fmap, value - BASE if value >= BASE else value)
        return 0

    for text in args.rvas:
        value = int(text, 16) if text.startswith("0x") else int(text)
        rva = value - BASE if value >= BASE else value
        got, note, votes = carry(md, old, new, fmap, rva)
        if got is None:
            print(f"0x{rva:x}  ->  UNMAPPED   {note}")
        else:
            print(f"0x{rva:x}  ->  0x{got:x}   {note}  votes={ {hex(k): v for k, v in votes.items()} }")
    return 0


if __name__ == "__main__":
    sys.exit(main())
