#!/usr/bin/env python3
"""Judge whether a mapped 1.17 address is really the same function as its 1.16.2 original.

`map-rvas-1162-to-1170.py` finds where a signature RE-OCCURS. That is where the evidence stops:
the signature is short and its operands are wildcarded, so a match proves the opening bytes have
the same shape, not that the function still does the same job. This script asks the follow-up
question by decoding much further into both functions and comparing them instruction by
instruction, normalised the same way the matcher masks: mnemonic plus register operands, with
displacements, immediates and branch targets dropped.

That normalisation is the point. ELDEN RING 1.17's dominant change is layout drift -- a struct
grew, so `[rcx+0xab5]` became `[rcx+0xabd]` -- and a function whose every instruction is identical
except for such displacements is the same function. A function that has gained a branch, lost a
call, or reordered its body is not, whatever its prologue says.

A verdict is evidence for a human, not permission. `IDENTICAL` over a long body is strong;
`DIVERGES` names the first instruction index where the two disagree so the reader knows where to
look; a short body is reported as short, because 6 matching instructions is not a lot of evidence
no matter how clean the ratio looks.

USAGE
    uv run --with capstone python3 scripts/verify-rva-map-1170.py            # whole table
    uv run --with capstone python3 scripts/verify-rva-map-1170.py 0x1407ada40
    uv run --with capstone python3 scripts/verify-rva-map-1170.py --tsv <out> --min-ratio 0.98
"""

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLD_IMAGE = os.path.join(ROOT, "eldenring-deobf.bin")
NEW_IMAGE = os.path.join(ROOT, "eldenring-deobf-1.17.bin")
MAP_TSV = os.path.join(ROOT, "docs", "recon", "rva-map-1162-to-1170.tsv")
BASE = 0x140000000
# Instructions decoded per side. Long enough to run past a prologue into the body that actually
# distinguishes two similar functions, short enough that one wrong decode does not cascade.
DECODE_LIMIT = 120
# Bytes handed to the decoder to reach that many instructions.
DECODE_BYTES = 0x400
# Below this many compared instructions the verdict is reported as thin evidence regardless of
# how well it matched -- several ELDEN RING getters are 6 instructions long and identical.
THIN_EVIDENCE = 12
# Entry-evidence verdicts, written into the table's last column and required by
# er-game-base/build.rs before a row may carry a DETOUR.
ENTRY_BOTH = "BOTH-ENTRIES"
ENTRY_DEST_NOT = "DEST-NOT-ENTRY"
ENTRY_SRC_NOT = "SRC-NOT-ENTRY"
ENTRY_NEITHER = "NEITHER-ENTRY"


def function_extents(image):
    """`{begin RVA: end RVA}` for every function the image's .pdata declares.

    The end is what makes a SHORT function verifiable. `compare` stops at the first `ret` and
    reports how many instructions it managed, which reads as thin evidence -- but a 21-byte
    function compared over all 21 bytes has been compared COMPLETELY, and calling that thin
    confuses "few instructions" with "not much of the function". The extent is how the two are
    told apart.
    """
    import struct

    e_lfanew = struct.unpack_from("<I", image, 0x3C)[0]
    magic = struct.unpack_from("<H", image, e_lfanew + 24)[0]
    directories = e_lfanew + 24 + (112 if magic == 0x20B else 96)
    table_rva, table_size = struct.unpack_from("<II", image, directories + 3 * 8)
    extents = {}
    for offset in range(table_rva, table_rva + table_size, 12):
        begin, end, _unwind = struct.unpack_from("<III", image, offset)
        if begin or end:
            extents[begin] = end
    return extents


def function_starts(image):
    """Every address the image itself declares as a function start, from its .pdata.

    x86-64 PE stores one RUNTIME_FUNCTION per function in the exception directory: begin RVA,
    end RVA, unwind info. That table is the image's OWN answer to "does a function start here",
    written by the linker for stack unwinding, and it is not a heuristic the way counting
    forward references is. A detour needs its target to be a function entry -- MinHook relocates
    the first five bytes and they had better be a prologue -- so this is the check that
    `scripts/audit-1170-hook-targets.py` was approximating.

    What it does NOT say: that the function is the SAME function. That is what `compare` is for,
    and the two are required together.
    """
    import struct

    e_lfanew = struct.unpack_from("<I", image, 0x3C)[0]
    magic = struct.unpack_from("<H", image, e_lfanew + 24)[0]
    # PE32+ optional header is 112 bytes before the data directories; PE32 is 96.
    directories = e_lfanew + 24 + (112 if magic == 0x20B else 96)
    # Data directory 3 is IMAGE_DIRECTORY_ENTRY_EXCEPTION.
    table_rva, table_size = struct.unpack_from("<II", image, directories + 3 * 8)
    starts = set()
    for offset in range(table_rva, table_rva + table_size, 12):
        begin, end, _unwind = struct.unpack_from("<III", image, offset)
        if begin or end:
            starts.add(begin)
    return starts


def entry_evidence(old_starts, new_starts, old_va, new_va):
    """Which side of the pair the image declares to be a function start."""
    src = (old_va - BASE) in old_starts
    dst = (new_va - BASE) in new_starts
    if src and dst:
        return ENTRY_BOTH
    if src:
        return ENTRY_DEST_NOT
    if dst:
        return ENTRY_SRC_NOT
    return ENTRY_NEITHER


def normalise(insn):
    """Mnemonic + register-only operand shape: what survives a patch that moves data around."""
    from capstone import CS_OP_MEM, CS_OP_REG

    parts = [insn.mnemonic]
    for operand in insn.operands:
        if operand.type == CS_OP_REG:
            parts.append(insn.reg_name(operand.reg) or "reg")
        elif operand.type == CS_OP_MEM:
            base = insn.reg_name(operand.mem.base) if operand.mem.base else "-"
            index = insn.reg_name(operand.mem.index) if operand.mem.index else "-"
            # The displacement itself is dropped: that is exactly what 1.17 changed.
            parts.append(f"[{base}+{index}*{operand.mem.scale}]")
        else:
            parts.append("imm")
    return " ".join(parts)


def decode(image, va, limit=DECODE_LIMIT, end_rva=None):
    """Normalised instructions of the function at `va`, stopped at the function's real end.

    KNOWING WHERE TO STOP IS THE WHOLE ACCURACY OF THIS TOOL. Stopping only at `ret` -- which is
    what this did until 2026-08-30 -- silently walks off the end of any function that ends in a
    TAIL CALL, and a great many do. Past the end sits inter-function padding whose length differs
    between builds (measured: 3 bytes in 1.16.2 against 4 in 1.17 after
    `CS::MenuWindowJob::~MenuWindowJob`), so the two decodes fall out of phase and every
    instruction after that point compares unequal. The result is a confident `DIVERGES` on a
    function that is byte-identical in its own body.

    That false negative is not merely noise: `build.rs::refuted_sources()` treats `DIVERGES` as
    positive evidence that an address is WRONG and subtracts the row from `VERIFIED_1162_TO_1170`
    -- the CALL map, not just the detour map. So a decoding artifact removes a working address and
    the feature dies with a `failed to resolve` line. Three independent reviews on 2026-08-30
    found the same artifact behind 12 of 12 non-clean rows, with zero changed immediates and zero
    changed struct offsets among them.

    Stops, in order of authority:
      1. `end_rva` -- the `.pdata` extent. The image's own declaration of where the function ends;
         nothing beats it, so it is used whenever both images declare one.
      2. `ret`.
      3. An unconditional `jmp` immediately followed by an `int3` pad byte. MSVC pads between
         functions with 0xCC, so `jmp` + `0xCC` is a tail call at a function boundary -- while a
         `jmp` in the middle of a body (a loop, a branch to a shared epilogue) is followed by real
         code and must NOT stop the decode.
    """
    from capstone import CS_ARCH_X86, CS_MODE_64, Cs

    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True
    offset = va - BASE
    if offset < 0 or offset >= len(image):
        return []
    out = []
    for insn in md.disasm(bytes(image[offset : offset + DECODE_BYTES]), va):
        if end_rva is not None and insn.address - BASE >= end_rva:
            break
        out.append(normalise(insn))
        if len(out) >= limit:
            break
        if insn.mnemonic == "ret":
            break
        if end_rva is None and insn.mnemonic == "jmp":
            after = insn.address - BASE + insn.size
            if after < len(image) and image[after] == INT3_PAD:
                break
    return out


# MSVC pads between functions with `int3`. A `jmp` followed by one is a tail call at a function
# boundary; a `jmp` followed by anything else is inside a body.
INT3_PAD = 0xCC


def whole_function_bytes(image, extents, va):
    """The function's entire body, or `None` when the image does not declare one here."""
    begin = va - BASE
    end = extents.get(begin)
    if end is None or end <= begin:
        return None
    return bytes(image[begin:end])


def compare(old_image, new_image, old_va, new_va, old_extents=None, new_extents=None):
    # A whole-function byte comparison, where both images declare an extent, settles the question
    # outright: same length, same bytes, nothing left to interpret. It is worth trying first
    # because the normalised comparison deliberately throws away displacements and immediates, so
    # it can only ever say "the same up to what 1.17 was expected to change" -- a weaker claim
    # than the one available for free when a function did not change at all.
    if old_extents is not None and new_extents is not None:
        left_body = whole_function_bytes(old_image, old_extents, old_va)
        right_body = whole_function_bytes(new_image, new_extents, new_va)
        if left_body and right_body and left_body == right_body:
            return {
                "verdict": "BYTE-IDENTICAL",
                "ratio": 1.0,
                "compared": len(left_body),
                "first_diff": None,
                "left_len": len(left_body),
                "right_len": len(right_body),
                "whole_body": True,
            }
    # Bound each decode by that image's own `.pdata` extent when it declares one. This is what
    # keeps the two instruction streams in phase: without it a tail-call function runs into
    # padding of a different length in each build and everything after diverges.
    old_end = old_extents.get(old_va - BASE) if old_extents is not None else None
    new_end = new_extents.get(new_va - BASE) if new_extents is not None else None
    left = decode(old_image, old_va, end_rva=old_end)
    right = decode(new_image, new_va, end_rva=new_end)
    if not left or not right:
        return {"verdict": "UNDECODABLE", "ratio": 0.0, "compared": 0, "first_diff": None}
    compared = min(len(left), len(right))
    first_diff = next((i for i in range(compared) if left[i] != right[i]), None)
    same = sum(1 for i in range(compared) if left[i] == right[i])
    ratio = same / compared
    # Did the decode reach the end of BOTH declared functions? If it did, a low instruction count
    # is the function being short, not the evidence being partial.
    whole_body = False
    if old_extents is not None and new_extents is not None:
        left_body = whole_function_bytes(old_image, old_extents, old_va)
        right_body = whole_function_bytes(new_image, new_extents, new_va)
        whole_body = bool(
            left_body
            and right_body
            and len(left_body) <= DECODE_BYTES
            and len(right_body) <= DECODE_BYTES
        )
    if first_diff is None and len(left) == len(right):
        verdict = (
            "IDENTICAL" if compared >= THIN_EVIDENCE or whole_body else "IDENTICAL-SHORT"
        )
    elif ratio >= 0.95:
        verdict = "NEAR"
    else:
        verdict = "DIVERGES"
    return {
        "verdict": verdict,
        "ratio": ratio,
        "compared": compared,
        "first_diff": first_diff,
        "left_len": len(left),
        "right_len": len(right),
        "whole_body": whole_body,
    }


def load_map(path=None):
    """Pairs to verify, from `path` or the original byte-search table.

    Two shapes are accepted, because the maps that produce candidates now outnumber the one
    this started with. The original table carries a How-it-was-mapped note in column 4; the
    function, data and needed maps carry a constant name or a vote count there, or nothing at
    all. Either way the first two columns are the pair, which is all the verification needs,
    and anything after the second column is passed through as the note.
    """
    pairs = []
    for line in open(path or MAP_TSV, encoding="utf-8"):
        if line.startswith("#") or not line.strip():
            continue
        fields = line.rstrip("\n").split("\t")
        if len(fields) < 2 or fields[1] == "-":
            continue
        try:
            old_va, new_va = int(fields[0], 16), int(fields[1], 16)
        except ValueError:
            continue
        # The newer maps are keyed by RVA; this one by VA. Both are unambiguous because the
        # image base is 0x140000000 and no RVA reaches it.
        if old_va < BASE:
            old_va += BASE
        if new_va < BASE:
            new_va += BASE
        note = fields[3] if len(fields) >= 4 else (fields[2] if len(fields) >= 3 else "")
        pairs.append((old_va, new_va, note))
    return pairs


def main():
    parser = argparse.ArgumentParser(
        description="Verify mapped 1.17 addresses are the same function as their 1.16.2 original."
    )
    parser.add_argument("vas", nargs="*", help="1.16.2 VAs to check (default: the whole table)")
    parser.add_argument("--tsv", metavar="PATH", help="write the verdicts here")
    parser.add_argument(
        "--map",
        metavar="PATH",
        help="read candidate pairs from this table instead of the byte-search one",
    )
    parser.add_argument(
        "--min-ratio",
        type=float,
        default=1.0,
        help="ratio at or above which a pair is listed as accepted (default 1.0)",
    )
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="assert the verdicts that the 2026-08-29 crash bisect established",
    )
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    for image in (OLD_IMAGE, NEW_IMAGE):
        if not os.path.exists(image):
            sys.exit(f"missing image: {image}")
    old_image = open(OLD_IMAGE, "rb").read()
    new_image = open(NEW_IMAGE, "rb").read()
    old_starts = function_starts(old_image)
    new_starts = function_starts(new_image)
    old_extents = function_extents(old_image)
    new_extents = function_extents(new_image)

    pairs = load_map(args.map)
    if args.vas:
        wanted = {int(v, 0) for v in args.vas}
        pairs = [p for p in pairs if p[0] in wanted]
    if not pairs:
        sys.exit("nothing to verify")

    rows = []
    for old_va, new_va, how in pairs:
        result = compare(
            old_image, new_image, old_va, new_va, old_extents, new_extents
        )
        result["entry"] = entry_evidence(old_starts, new_starts, old_va, new_va)
        rows.append((old_va, new_va, how, result))
        diff = "" if result["first_diff"] is None else f", first diff at insn {result['first_diff']}"
        print(
            f"{old_va:#x} -> {new_va:#x}  {result['verdict']:<16} "
            f"{result['ratio']:.2f} over {result['compared']} insns{diff}  "
            f"{result['entry']}   [{how}]"
        )

    accepted = [r for r in rows if r[3]["ratio"] >= args.min_ratio and r[3]["compared"] >= THIN_EVIDENCE]
    thin = [r for r in rows if r[3]["ratio"] >= args.min_ratio and r[3]["compared"] < THIN_EVIDENCE]
    rejected = [r for r in rows if r[3]["ratio"] < args.min_ratio]
    print(
        f"\n{len(accepted)} accepted, {len(thin)} accepted-but-thin (<{THIN_EVIDENCE} insns), "
        f"{len(rejected)} rejected, of {len(rows)}"
    )

    if args.tsv:
        with open(args.tsv, "w", encoding="utf-8") as handle:
            handle.write(
                "# 1.16.2 VA\t1.17 VA\tverdict\tratio\tinsns compared\thow it was mapped\tentry\n"
            )
            handle.write(
                "# Generated by scripts/verify-rva-map-1170.py. A verdict is evidence, not\n"
                "# permission: IDENTICAL means the normalised instruction sequences agree, which\n"
                "# cannot see a change in what a called function does.\n"
                "#\n"
                "# The last column is the OTHER half of a detour's licence: whether each image's own\n"
                "# .pdata declares a function to start at that address. er-game-base/build.rs\n"
                "# requires BOTH-ENTRIES before a row may carry a detour, because IDENTICAL over a\n"
                "# body says the code is the same and says nothing about whether MinHook may\n"
                "# relocate the five bytes it is about to overwrite.\n"
            )
            for old_va, new_va, how, result in rows:
                handle.write(
                    f"{old_va:#x}\t{new_va:#x}\t{result['verdict']}\t{result['ratio']:.3f}\t"
                    f"{result['compared']}\t{how}\t{result['entry']}\n"
                )
        print(f"wrote {args.tsv}")
    return 0


def selftest():
    """Pin the decode boundary, and record that one 2026-08-29 verdict was retracted.

    THE RETRACTION, because a test that asserts a wrong answer is worse than no test. This
    selftest used to require `HUD_WEAPON_SLOT_UPDATE` (0x1408d2110 -> 0x1408d32b0) to come back
    `DIVERGES` at "18% of its instruction shape", and treated that as the lesson of the
    2026-08-29 crash bisect. The 18% was an artifact of THIS FILE: the function is 86 bytes and
    ends in a tail-call `jmp`, `decode()` stopped only at `ret`, and roughly 98 of the 120
    instructions it compared belonged to the NEXT function. Bounded by the `.pdata` extent the two
    bodies differ in four bytes, both of them halves of `call rel32` displacements, and the
    verdict is IDENTICAL. Three independent reviews found the same artifact behind 12 of 12
    non-clean rows, with zero changed immediates and zero changed struct offsets among them.

    WHAT IS NOT RETRACTED: the crash was real. Its cause is now UNKNOWN and must be re-derived --
    an independent look at the same run found the game dying in FromSoftware's own `DL_PANIC`
    ("未初期化のシングルトンにアクセスしました", FD4Singleton.h) for an uninitialised singleton,
    which points at a stale `.data` global rather than at a detour. Do not read the IDENTICAL
    verdict below as "that address was fine all along"; read it as "the reason we gave was wrong".

    So what is asserted here is the BOUNDARY, which is the thing that was actually broken: a
    function ending in a tail call must compare over its own body and no further.
    """
    old_image = open(OLD_IMAGE, "rb").read()
    new_image = open(NEW_IMAGE, "rb").read()
    old_starts = function_starts(old_image)
    new_starts = function_starts(new_image)
    old_extents = function_extents(old_image)
    new_extents = function_extents(new_image)

    failures = []

    def check(name, got, want):
        if got != want:
            failures.append(f"{name}: got {got!r}, want {want!r}")

    check("1.16.2 .pdata is populated", len(old_starts) > 200_000, True)
    check("1.17 .pdata is populated", len(new_starts) > 200_000, True)

    # The four that are genuinely the same function, and the one that is not.
    same = {
        0x1408D0900: 0x1408D1AA0,  # HUD_SCENE_UPDATE
        0x1408D1D00: 0x1408D2EA0,  # HUD_WEAPON_SLOT_CTOR
        0x1408D1E30: 0x1408D2FD0,  # HUD_CHILD_BINDER
        0x1408FF470: 0x140900610,  # TILE_POPULATE
    }
    for old_va, new_va in same.items():
        result = compare(
            old_image, new_image, old_va, new_va, old_extents, new_extents
        )
        check(f"{old_va:#x} verdict", result["verdict"], "IDENTICAL")
        check(
            f"{old_va:#x} entry",
            entry_evidence(old_starts, new_starts, old_va, new_va),
            ENTRY_BOTH,
        )

    killer = compare(
        old_image, new_image, 0x1408D2110, 0x1408D32B0, old_extents, new_extents
    )
    # Retracted 2026-08-29 verdict -- see this function's docstring. Bounded by .pdata, the two
    # bodies agree; the old DIVERGES was this file decoding the following function.
    check("HUD_WEAPON_SLOT_UPDATE verdict", killer["verdict"], "IDENTICAL")
    check("HUD_WEAPON_SLOT_UPDATE compares its own body only", killer["compared"] <= 40, True)
    # THE REGRESSION GUARD THAT MATTERS. A tail-call function must not decode past its own end.
    # Without the .pdata bound this returned ~120 instructions for an 86-byte body.
    tail_call_body = decode(
        old_image,
        0x1408D2110,
        end_rva=old_extents.get(0x1408D2110 - BASE),
    )
    check("tail-call body stays inside its .pdata extent", len(tail_call_body) <= 40, True)
    check("tail-call body is not empty", len(tail_call_body) > 0, True)

    # Short functions the extent rule rescues: 21 and 38 bytes, byte-for-byte unchanged in 1.17.
    # Before extents were read these were IDENTICAL-SHORT over 7 instructions and excluded, which
    # is what left er-armament-icons' PROXY_IS_BOUND detour refused on a function that did not
    # change at all.
    for old_va, new_va in ((0x140733150, 0x140733FA0), (0x140733EF0, 0x140734D40)):
        whole = compare(
            old_image, new_image, old_va, new_va, old_extents, new_extents
        )
        check(f"{old_va:#x} whole-function verdict", whole["verdict"], "BYTE-IDENTICAL")

    # A mid-function address is not an entry, whatever the bytes around it say. Five bytes into a
    # known function start is by construction not a function start.
    midway = entry_evidence(old_starts, new_starts, 0x1408D0905, 0x1408D1AA5)
    check("mid-function pair", midway, ENTRY_NEITHER)

    for line in failures:
        print(f"selftest FAIL {line}")
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
