#!/usr/bin/env python3
"""Measure ELDEN RING 1.16.2 -> 1.17 STRUCT FIELD OFFSET drift from the two images.

THE BLIND SPOT THIS CLOSES
--------------------------
`er-game-base` now refuses any game ADDRESS that has no verified 1.17 mapping, so a moved
function announces itself in the log. There is no equivalent protection for a struct FIELD
OFFSET. `PlayerGameData` grew 8 bytes in 1.17 -- `GetScadutreeBlessing` is byte-identical
between the builds EXCEPT `[rcx+0xab5]` -> `[rcx+0xabd]` -- and a constant like
`GAME_MAN_SAVED_MAP_C30_OFFSET` carrying a 1.16.2 value into a 1.17 object produces no
refusal, no crash and no log line. It silently reads, or writes, the wrong member.

HOW IT IS MEASURED (not guessed)
--------------------------------
A field offset is used by INSTRUCTIONS. So take a function whose 1.16.2 -> 1.17 identity is
already established (`docs/recon/rva-map-1162-to-1170.functions.tsv`, ~128k pairs; the
hand-verified subset is in `...verified.tsv`), decode both bodies, and keep only the pairs
that are instruction-for-instruction identical EXCEPT for memory displacements on a register
base. In such a pair the code did not change, so every displacement that DID change is a
field that moved, and by exactly how much. That set is the struct drift, measured.

`scripts/map-rvas-1162-to-1170.py::build_masked_pattern` masks precisely these bytes so its
signatures survive the drift. This tool is the inverse: it keeps what that one discards.

WHAT IS DELIBERATELY NOT COUNTED AS STRUCT DRIFT
-----------------------------------------------
* `[rip + disp]`   -- code and data moved; the displacement changing says nothing about a struct.
* branch/call targets -- same reason.
* `[rsp + disp]` and `[rbp - disp]` frame slots -- a stack frame is not a game structure. These
  are reported under their own `stack` class so a reader can see them and not mistake them for
  fields.
* absolute `[0x1400...]` operands -- data addresses, not fields.
* immediates -- `add rcx, 0xab5` really can carry a field offset, but an immediate is far more
  often a size, an id or a flag mask, so immediate changes go in a SEPARATE, explicitly
  lower-confidence table and any function containing one is downgraded to MIXED.

WHAT A RESULT MEANS
-------------------
A row here says: in N functions that are otherwise identical between the builds, displacement
`old` became `new`. That is strong evidence the field at `old` in SOME structure moved. It is
NOT evidence about a particular repo constant, because the same number is a field offset in
many unrelated structures. So the cross-reference (`--report`) always prints BOTH sides: how
often that value was seen drifting and how often it was seen unchanged, plus named example
functions, and it refuses to convert either into a verdict on its own. A missing offset costs a
lookup; a wrong one writes.

USAGE
    --selftest                     assert the parser, the classifier and a known 1.17 field move
    --inventory                    part 1: count and classify every *_OFFSET constant
    --scan                         part 2: measure drift across every mapped pair   (~1 min)
    --attribute                    ask Ghidra which TYPE each drifting function operates on
    --regions [--min-fields N]     cluster the raw rows into candidate structures
    --report [--autoload-only]     cross-reference the inventory against the measurement
    --explain 0xc30                everything measured about one displacement
    --find-displacement 0xc30 --names 8
                                   the decisive per-constant check: every mapped function that
                                   reads at that offset, and what happened to each   (~1 min)
    --pairs docs/recon/rva-map-1162-to-1170.verified.tsv --names 1
                                   compare only the functions this mod actually hooks

ORDER: --scan, then --attribute (needs `bash scripts/ghidra/mcp-up-1162.sh`), then --report.
`--scan` and `--find-displacement` decode ~29 MB of function bodies on each side; background them.
Everything else reads cached output under `--out-dir`.

THE ONE RULE FOR READING THE OUTPUT: a displacement is not a field. `0xb0c` moved in 1.17 -- in
`MoWwiseManImp`. `DIALOG_SLOT_CURSOR_B0C_OFFSET` is also `0xb0c` and indexes something else
entirely, and is unaffected. Never act on a number without the structure beside it.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = 0x140000000
IMAGE_1162 = ROOT / "eldenring-deobf.bin"
IMAGE_1170 = ROOT / "eldenring-deobf-1.17.bin"
FUNCTION_MAP = ROOT / "docs/recon/rva-map-1162-to-1170.functions.tsv"
VERIFIED_MAP = ROOT / "docs/recon/rva-map-1162-to-1170.verified.tsv"
DEFAULT_OUT = Path(
    os.environ.get(
        "ER_STRUCT_DRIFT_OUT",
        "/tmp/claude-1000/-home-banon-projects-er-mods-rs/"
        "f1b1f237-c4a5-4649-9833-a40666da21bb/scratchpad/struct-drift",
    )
)

# A function bigger than this is skipped: the odds that a 128 KB function is
# instruction-for-instruction identical across a patch are nil, and decoding it twice costs more
# than every small function put together.
MAX_FUNCTION_BYTES = 0x4000
# Ghidra's image-address window. An immediate inside it is a moved code/data pointer, not a field.
IMAGE_LO, IMAGE_HI = 0x140000000, 0x150000000

# Registers whose displacements are frame slots, not structure fields.
STACK_BASES = {"rsp", "esp", "rbp", "ebp"}

# A displacement at or above this is an RVA, not a field. The de-Arxan'd image reaches its
# globals through an image-base register -- `lea r14, [rip - 0x1b2e983]` puts 0x140000000 in r14,
# and the next instruction is `mov ecx, [r14 + rax*4 + 0x3030aa0]`. That looks exactly like a
# structure field on a register base, and it is not one: the number is a `.data` RVA, so it moves
# when a section moves and says nothing about any object's layout.
#
# The threshold is measured, not chosen: across the whole scan the drifting displacements are
# sharply bimodal -- the largest field-shaped one is 0xd18 and the smallest RVA-shaped one is
# 0x289ce0, a gap of ten bit-lengths with nothing in it. 0x100000 sits in the middle of that gap
# and above every plausible field (the largest this repo names is WorldInfoOwner's 0xb3030).
GLOBAL_DISPLACEMENT_MIN = 0x100000


# --------------------------------------------------------------------------------------------
# images
# --------------------------------------------------------------------------------------------
class Image:
    """A flat (virtual-layout) PE image: file offset == RVA, VA == BASE + offset."""

    def __init__(self, path: Path):
        self.path = path
        self.data = path.read_bytes()
        pe = struct.unpack_from("<I", self.data, 0x3C)[0]
        if self.data[pe : pe + 4] != b"PE\0\0":
            raise SystemExit(f"{path}: not a PE image")
        nsec = struct.unpack_from("<H", self.data, pe + 6)[0]
        optsz = struct.unpack_from("<H", self.data, pe + 20)[0]
        off = pe + 24 + optsz
        self.sections: dict[str, tuple[int, int]] = {}
        for i in range(nsec):
            entry = self.data[off + i * 40 : off + (i + 1) * 40]
            name = entry[:8].rstrip(b"\0").decode("latin1")
            vsz, va, rsz, _raw = struct.unpack_from("<IIII", entry, 8)
            # Two sections can share the name ".text"; the first is the real one.
            self.sections.setdefault(name, (va, max(vsz, rsz)))

    def function_ends(self) -> dict[int, int]:
        """start_rva -> end_rva for every RUNTIME_FUNCTION in `.pdata`.

        `.pdata` is the game's own function table, so the extents here are FromSoftware's, not
        a heuristic of ours. Chunked functions appear as several records; taking the first is
        fine because a body is only compared against the matching chunk on the other side.
        """
        va, size = self.sections[".pdata"]
        out: dict[int, int] = {}
        for off in range(va, va + size, 12):
            begin, end, _unwind = struct.unpack_from("<III", self.data, off)
            if begin == 0 or end <= begin or end - begin > 0x20000:
                continue
            if begin not in out:
                out[begin] = end
        return out


# --------------------------------------------------------------------------------------------
# operand-text parsing
#
# Capstone's Intel `op_str` is compared as TEXT rather than through `insn.operands`. Two reasons,
# both practical: the text already normalises a disp8/disp32 re-encoding (`[rcx + 0x7f]` and
# `[rcx + 0x80]` print the same shape though their byte lengths differ), and detail-mode operand
# access in Python costs several times a lite decode over 29 MB of code. The parse below is
# exercised by --selftest against hand-written encodings including every awkward form.
# --------------------------------------------------------------------------------------------
# Capstone prints an immediate below 10 in decimal and everything else as `0x..`, so a
# number-matcher that only knows `0x` silently misses `cmp ..., 0` -- and a `0` -> `1` change then
# reads as a different SHAPE and the whole function is discarded. Both spellings are matched.
_NUM = r"(?:0x[0-9a-f]+|\d+)"
_SIGNED_TAIL = re.compile(r"\s*([+-])\s*(" + _NUM + r")\s*$")
_ABSOLUTE = re.compile(r"^\s*(" + _NUM + r")\s*$")
# Outside a `[...]` there is no `*scale`, so a bare decimal is safe to match -- but it must not
# eat the digits inside a register name (`r8`, `xmm12`), hence the look-behind.
_OUTSIDE_NUM = re.compile(r"(?<![\w.])(-?" + _NUM + r")\b")


def split_memory(op_str: str) -> tuple[str, list[tuple[str, int]]]:
    """Return `(shape, operands)`.

    `shape` is `op_str` with every displacement inside `[...]` and every immediate outside them
    replaced by a placeholder, so two instructions with the same shape differ only in numbers.
    `operands` is one `(base_register, displacement)` per `[...]`, in order; `base` is `""` for
    an absolute operand and `"rip"` for rip-relative.
    """
    shape: list[str] = []
    operands: list[tuple[str, int]] = []
    i = 0
    n = len(op_str)
    while i < n:
        start = op_str.find("[", i)
        if start < 0:
            shape.append(_OUTSIDE_NUM.sub("#I", op_str[i:]))
            break
        # Text before the bracket: numbers there are true immediates.
        shape.append(_OUTSIDE_NUM.sub("#I", op_str[i:start]))
        end = op_str.find("]", start)
        if end < 0:  # malformed; treat the rest as opaque
            shape.append(_OUTSIDE_NUM.sub("#I", op_str[start:]))
            break
        body = op_str[start + 1 : end]
        base, disp, stem = parse_mem_body(body)
        operands.append((base, disp))
        shape.append("[" + stem + "#D]")
        i = end + 1
    return "".join(shape), operands


def parse_mem_body(body: str) -> tuple[str, int, str]:
    """`rcx + 0xab5` -> `("rcx", 0xab5, "rcx")`; `rax + rdx*8 - 0x10` -> `("rax", -0x10, ...)`.

    The third value is the operand with its displacement stripped: that is what goes into the
    shape, so `[rcx]` and `[rcx + 0x8]` normalise to the same shape and their displacements are
    then compared as 0 against 8 -- a field moving off the head of a structure is a real move and
    must not be thrown away as a shape difference.

    An operand with no register at all (`[0x143d71580]`) yields base `""`, which the caller treats
    as an absolute data address rather than a field.
    """
    absolute = _ABSOLUTE.match(body)
    if absolute:
        return "", int(absolute.group(1), 0), ""
    tail = _SIGNED_TAIL.search(body)
    disp = 0
    if tail:
        disp = int(tail.group(2), 0)
        if tail.group(1) == "-":
            disp = -disp
        body = body[: tail.start()]
    stem = body.strip()
    if not stem:
        return "", disp, ""
    base = stem.split("+")[0].split("*")[0].strip()
    # `fs:` / `gs:` segment prefixes stay in the base text; they are not fields.
    return base, disp, stem


def immediates(shape_text: str, op_str: str) -> list[int]:
    """Every immediate OUTSIDE a `[...]`, in order -- the ones `split_memory` turned into `#I`."""
    out: list[int] = []
    i = 0
    n = len(op_str)
    while i < n:
        start = op_str.find("[", i)
        segment = op_str[i:] if start < 0 else op_str[i:start]
        out += [int(text, 0) for text in _OUTSIDE_NUM.findall(segment)]
        if start < 0:
            break
        end = op_str.find("]", start)
        if end < 0:
            break
        i = end + 1
    return out


# --------------------------------------------------------------------------------------------
# comparison
# --------------------------------------------------------------------------------------------
SHAPE_DIFF = "SHAPE-DIFF"
STABLE = "STABLE"
DRIFT = "DRIFT"
MIXED = "MIXED"


class Comparison:
    __slots__ = (
        "verdict",
        "field_drift",
        "stack_drift",
        "global_drift",
        "imm_drift",
        "stable",
        "insns",
        "why",
    )

    def __init__(self):
        self.verdict = SHAPE_DIFF
        self.field_drift: list[tuple[str, int, int]] = []  # (base, old, new)
        self.stack_drift: list[tuple[str, int, int]] = []
        self.global_drift: list[tuple[int, int]] = []  # image-base-relative globals (old, new)
        self.imm_drift: list[tuple[str, int, int]] = []  # (mnemonic, old, new)
        self.stable: list[int] = []  # non-stack, non-rip displacements that did NOT move
        self.insns = 0
        self.why = ""


def is_branchy(mnemonic: str) -> bool:
    """A relative branch's immediate is a moved address, never a field offset."""
    return mnemonic[0] == "j" or mnemonic in ("call", "loop", "loope", "loopne", "jmp")


def compare_bodies(md, a: bytes, a_va: int, b: bytes, b_va: int) -> Comparison:
    """Compare two function bodies instruction-for-instruction.

    Returns SHAPE-DIFF unless the two decode to the same number of instructions with the same
    mnemonics and the same operand SHAPES -- i.e. the code is the same and only numbers differ.
    Only then are the number differences meaningful, and they are split into field displacements,
    stack displacements and immediates.
    """
    result = Comparison()
    da = list(md.disasm_lite(a, a_va))
    db = list(md.disasm_lite(b, b_va))
    if not da or len(da) != len(db):
        result.why = f"instruction count {len(da)} vs {len(db)}"
        return result
    result.insns = len(da)
    saw_field, saw_imm = False, False
    for (_aa, _asz, amn, aop), (_ba, _bsz, bmn, bop) in zip(da, db):
        if amn != bmn:
            result.why = f"mnemonic {amn} vs {bmn}"
            return result
        if aop == bop:
            # Identical text: nothing moved here. Still harvest its displacements, because the
            # denominator ("0xc30 was seen unchanged N times") is half of an honest report.
            if "[" in aop:
                for base, disp in split_memory(aop)[1]:
                    if (
                        base
                        and base != "rip"
                        and base not in STACK_BASES
                        and 0 < disp < GLOBAL_DISPLACEMENT_MIN
                    ):
                        result.stable.append(disp)
            continue
        a_shape, a_mem = split_memory(aop)
        b_shape, b_mem = split_memory(bop)
        if a_shape != b_shape:
            result.why = f"operand shape {aop!r} vs {bop!r}"
            return result
        for (a_base, a_disp), (b_base, b_disp) in zip(a_mem, b_mem):
            if a_disp == b_disp:
                if (
                    a_base
                    and a_base != "rip"
                    and a_base not in STACK_BASES
                    and 0 < a_disp < GLOBAL_DISPLACEMENT_MIN
                ):
                    result.stable.append(a_disp)
                continue
            if not a_base or a_base == "rip":
                continue  # absolute / rip-relative: the image moved, not a struct
            if a_base in STACK_BASES:
                result.stack_drift.append((a_base, a_disp, b_disp))
                continue
            if a_disp >= GLOBAL_DISPLACEMENT_MIN:
                result.global_drift.append((a_disp, b_disp))
                continue
            result.field_drift.append((a_base, a_disp, b_disp))
            saw_field = True
        if not is_branchy(amn):
            for a_imm, b_imm in zip(immediates(a_shape, aop), immediates(b_shape, bop)):
                if a_imm == b_imm:
                    continue
                if IMAGE_LO <= a_imm < IMAGE_HI and IMAGE_LO <= b_imm < IMAGE_HI:
                    continue  # a moved absolute address
                result.imm_drift.append((amn, a_imm, b_imm))
                saw_imm = True
    if saw_field and saw_imm:
        result.verdict = MIXED
    elif saw_field:
        result.verdict = DRIFT
    elif saw_imm:
        result.verdict = MIXED
    else:
        result.verdict = STABLE
    return result


# --------------------------------------------------------------------------------------------
# scan
# --------------------------------------------------------------------------------------------
def load_pairs(path: Path) -> list[tuple[int, int]]:
    pairs = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        cols = line.split()
        if len(cols) < 2:
            continue
        try:
            a, b = int(cols[0], 16), int(cols[1], 16)
        except ValueError:
            continue
        pairs.append((a - BASE if a >= BASE else a, b - BASE if b >= BASE else b))
    return pairs


def scan(args) -> int:
    _ensure_capstone()
    from capstone import CS_ARCH_X86, CS_MODE_64, Cs

    md = Cs(CS_ARCH_X86, CS_MODE_64)
    old, new = Image(IMAGE_1162), Image(IMAGE_1170)
    ends_old, ends_new = old.function_ends(), new.function_ends()
    pairs = load_pairs(FUNCTION_MAP)
    if args.limit:
        pairs = pairs[: args.limit]

    drift = collections.defaultdict(
        lambda: {"insns": 0, "funcs": set(), "bases": collections.Counter()}
    )
    stack = collections.Counter()
    globals_moved: dict[tuple[int, int], int] = collections.Counter()
    imm = collections.defaultdict(lambda: {"insns": 0, "funcs": set()})
    stable = collections.Counter()
    verdicts = collections.Counter()
    per_function = []

    for a_rva, b_rva in pairs:
        a_end, b_end = ends_old.get(a_rva), ends_new.get(b_rva)
        if a_end is None or b_end is None:
            verdicts["NO-PDATA"] += 1
            continue
        if a_end - a_rva > MAX_FUNCTION_BYTES or b_end - b_rva > MAX_FUNCTION_BYTES:
            verdicts["TOO-BIG"] += 1
            continue
        cmp = compare_bodies(
            md,
            old.data[a_rva:a_end],
            BASE + a_rva,
            new.data[b_rva:b_end],
            BASE + b_rva,
        )
        verdicts[cmp.verdict] += 1
        if cmp.verdict == SHAPE_DIFF:
            continue
        for disp in cmp.stable:
            stable[disp] += 1
        for base, a_disp, b_disp in cmp.field_drift:
            row = drift[(a_disp, b_disp)]
            row["insns"] += 1
            row["funcs"].add(BASE + a_rva)
            row["bases"][base] += 1
        for base, a_disp, b_disp in cmp.stack_drift:
            stack[(a_disp, b_disp)] += 1
        for a_disp, b_disp in cmp.global_drift:
            globals_moved[(a_disp, b_disp)] += 1
        for mnemonic, a_imm, b_imm in cmp.imm_drift:
            row = imm[(a_imm, b_imm)]
            row["insns"] += 1
            row["funcs"].add(BASE + a_rva)
        if cmp.field_drift or cmp.imm_drift:
            per_function.append(
                {
                    "va_1162": f"{BASE + a_rva:#x}",
                    "va_1170": f"{BASE + b_rva:#x}",
                    "verdict": cmp.verdict,
                    "insns": cmp.insns,
                    "field_drift": [[b, o, n] for b, o, n in cmp.field_drift],
                    "imm_drift": [[m, o, n] for m, o, n in cmp.imm_drift],
                    # The offsets this same function read at the SAME place in both builds. For a
                    # struct that grew, these are the fields BELOW the insertion point, and they
                    # are what lets the report say "0x68 did not move" instead of "not observed".
                    "stable": sorted(set(cmp.stable)),
                }
            )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "pairs_considered": len(pairs),
        "verdicts": dict(verdicts),
        "field_drift": [
            {
                "old": old_d,
                "new": new_d,
                "delta": new_d - old_d,
                "insns": row["insns"],
                "functions": len(row["funcs"]),
                "bases": dict(row["bases"]),
                # Every witness is kept, not a sample: attribution asks Ghidra what type each
                # one operates on, and a truncated list can hide the only named function in the
                # set -- which is the difference between "PlayerGameData grew" and "unknown".
                "examples": [f"{v:#x}" for v in sorted(row["funcs"])],
            }
            for (old_d, new_d), row in sorted(
                drift.items(), key=lambda kv: -len(kv[1]["funcs"])
            )
        ],
        "stack_drift": [
            {"old": o, "new": n, "insns": c}
            for (o, n), c in sorted(stack.items(), key=lambda kv: -kv[1])[:200]
        ],
        "imm_drift": [
            {
                "old": o,
                "new": n,
                "insns": row["insns"],
                "functions": len(row["funcs"]),
                "examples": [f"{v:#x}" for v in sorted(row["funcs"])[:6]],
            }
            for (o, n), row in sorted(imm.items(), key=lambda kv: -len(kv[1]["funcs"]))[:400]
        ],
        "stable_displacements": {str(k): v for k, v in stable.items()},
        # Not struct drift, kept because it is the same measurement: every `.data` RVA reached
        # through the image-base register that moved, which is a free 1.16.2 -> 1.17 global map.
        "global_drift": [
            {"old": o, "new": n, "delta": n - o, "insns": c}
            for (o, n), c in sorted(globals_moved.items())
        ],
    }
    (args.out_dir / "field-drift.json").write_text(json.dumps(payload), encoding="utf-8")
    (args.out_dir / "drift-functions.json").write_text(
        json.dumps(per_function), encoding="utf-8"
    )
    with (args.out_dir / "field-drift.tsv").open("w", encoding="utf-8") as handle:
        handle.write(
            "# 1.16.2 disp\t1.17 disp\tdelta\tfunctions\tinsns\tbases\texample 1.16.2 VAs\n"
            "# Generated by scripts/detect-struct-field-drift.py --scan. Each row: in N otherwise\n"
            "# instruction-identical function pairs, this register-base displacement changed.\n"
            "# A row is evidence that SOME structure's field moved, not a verdict on any one\n"
            "# repo constant -- the same number is a field offset in many structures.\n"
        )
        for row in payload["field_drift"]:
            handle.write(
                f"{row['old']:#x}\t{row['new']:#x}\t{row['delta']:+#x}\t{row['functions']}\t"
                f"{row['insns']}\t{','.join(sorted(row['bases']))}\t"
                f"{' '.join(row['examples'][:6])}\n"
            )
    print(f"pairs considered      {len(pairs)}")
    for key, count in sorted(verdicts.items(), key=lambda kv: -kv[1]):
        print(f"  {key:<12} {count}")
    print(f"distinct field-offset moves {len(payload['field_drift'])}")
    print(f"distinct global (.data RVA) moves {len(payload['global_drift'])}  [not struct drift]")
    print(f"wrote {args.out_dir}/field-drift.tsv (+ .json, drift-functions.json)")
    return 0


def pairs_mode(args) -> int:
    """Compare an explicit list of function pairs and print every displacement, moved or not.

    `--scan` answers "what moved in the game". This answers the narrower and more useful question
    "did anything move in the functions THIS MOD hooks", using
    `docs/recon/rva-map-1162-to-1170.verified.tsv` -- pairs established by hand, which are exactly
    the code the product detours and therefore exactly the objects its offsets index.

    Both the moved and the UNCHANGED displacements are printed. An unchanged one is the only
    positive clearance available: it says this specific function still reads this specific field
    at this specific offset in 1.17.
    """
    _ensure_capstone()
    from capstone import CS_ARCH_X86, CS_MODE_64, Cs

    md = Cs(CS_ARCH_X86, CS_MODE_64)
    old, new_img = Image(IMAGE_1162), Image(IMAGE_1170)
    ends_old, ends_new = old.function_ends(), new_img.function_ends()
    starts_old = sorted(ends_old)
    starts_new = sorted(ends_new)

    def extent(rva, ends, starts):
        """`.pdata` end, or the next function start -- leaf functions carry no unwind record."""
        if rva in ends:
            return ends[rva]
        import bisect

        index = bisect.bisect_right(starts, rva)
        following = starts[index] if index < len(starts) else rva + 0x200
        return min(following, rva + 0x400)

    path = Path(args.pairs)
    cache_path = args.out_dir / "ghidra-function-types.json"
    cache = json.loads(cache_path.read_text()) if cache_path.is_file() else {}
    verdicts = collections.Counter()
    for a_rva, b_rva in load_pairs(path):
        a_end = extent(a_rva, ends_old, starts_old)
        b_end = extent(b_rva, ends_new, starts_new)
        cmp = compare_bodies(
            md, old.data[a_rva:a_end], BASE + a_rva, new_img.data[b_rva:b_end], BASE + b_rva
        )
        verdicts[cmp.verdict] += 1
        va = f"{BASE + a_rva:#x}"
        if args.names:
            function_types(cache, [va])
        label = cache.get(va, {}).get("name", "")
        print(f"{va} -> {BASE + b_rva:#x}  {cmp.verdict:<10} {cmp.insns:>4} insns  {label}")
        if cmp.verdict == SHAPE_DIFF:
            print(f"    body differs: {cmp.why}")
            continue
        for base, o, n in cmp.field_drift:
            print(f"    MOVED  [{base}+{o:#x}] -> [{base}+{n:#x}]  ({n - o:+#x})")
        held = sorted(set(cmp.stable))
        if held:
            print(f"    held   {' '.join(f'{d:#x}' for d in held[:24])}"
                  f"{' ...' if len(held) > 24 else ''}")
    if args.names:
        cache_path.write_text(json.dumps(cache), encoding="utf-8")
    print(f"\n{dict(verdicts)}")
    print(
        "SHAPE-DIFF here is not a struct verdict: the function itself changed, so its "
        "displacements cannot be compared and its offsets stay UNKNOWN."
    )
    return 0


def find_displacement(args) -> int:
    """Every mapped function that uses a given displacement, and whether it moved in 1.17.

    `--report` can only clear a constant whose STRUCTURE it can name, and most cannot be named.
    This is the way to settle one of those by hand: give it the number, and it lists the functions
    that read at that offset in 1.16.2 together with what happened to each in 1.17. Ghidra names
    them, so the answer arrives as "GameMan's save-slot writer still reads +0xc30" rather than as
    a count.

    A function that came out SHAPE-DIFF is listed too and explicitly NOT counted either way: its
    body changed, so the displacement cannot be compared, and silence there would read as a clean
    bill of health.
    """
    _ensure_capstone()
    from capstone import CS_ARCH_X86, CS_MODE_64, Cs

    wanted = {int(v, 0) for v in args.find_displacement.split(",")}
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    old, new_img = Image(IMAGE_1162), Image(IMAGE_1170)
    ends_old, ends_new = old.function_ends(), new_img.function_ends()
    held: dict[int, list[str]] = collections.defaultdict(list)
    moved: dict[int, list[tuple[str, int]]] = collections.defaultdict(list)
    unknown: dict[int, list[str]] = collections.defaultdict(list)

    for a_rva, b_rva in load_pairs(FUNCTION_MAP):
        a_end, b_end = ends_old.get(a_rva), ends_new.get(b_rva)
        if a_end is None or b_end is None or a_end - a_rva > MAX_FUNCTION_BYTES:
            continue
        body = old.data[a_rva:a_end]
        uses = set()
        for _ad, _sz, _mn, op in md.disasm_lite(body, BASE + a_rva):
            if "[" not in op:
                continue
            for base, disp in split_memory(op)[1]:
                if disp in wanted and base and base != "rip" and base not in STACK_BASES:
                    uses.add(disp)
        if not uses:
            continue
        cmp = compare_bodies(md, body, BASE + a_rva, new_img.data[b_rva:b_end], BASE + b_rva)
        va = f"{BASE + a_rva:#x}"
        for disp in uses:
            if cmp.verdict == SHAPE_DIFF:
                unknown[disp].append(va)
            elif any(o == disp for _b, o, _n in cmp.field_drift):
                new_value = next(n for _b, o, n in cmp.field_drift if o == disp)
                moved[disp].append((va, new_value))
            else:
                held[disp].append(va)

    cache_path = args.out_dir / "ghidra-function-types.json"
    cache = json.loads(cache_path.read_text()) if cache_path.is_file() else {}
    for disp in sorted(wanted):
        print(f"\ndisplacement {disp:#x}")
        print(f"  UNCHANGED in {len(held[disp])} comparable function(s)")
        print(f"  MOVED     in {len(moved[disp])} comparable function(s)")
        print(f"  UNKNOWN   in {len(unknown[disp])} function(s) whose body itself changed")
        if args.names:
            sample = (
                [va for va, _n in moved[disp][: args.names]]
                + held[disp][: args.names]
                + unknown[disp][: args.names]
            )
            function_types(cache, sample)
            for va, new_value in moved[disp][: args.names]:
                print(f"    MOVED     {va} -> {new_value:#x}  {cache.get(va, {}).get('name', '')}"
                      f"  {cache.get(va, {}).get('types', [])}")
            for va in held[disp][: args.names]:
                print(f"    unchanged {va}  {cache.get(va, {}).get('name', '')}"
                      f"  {cache.get(va, {}).get('types', [])}")
            for va in unknown[disp][: args.names]:
                print(f"    UNKNOWN   {va}  {cache.get(va, {}).get('name', '')}"
                      f"  {cache.get(va, {}).get('types', [])}")
    if args.names:
        cache_path.write_text(json.dumps(cache), encoding="utf-8")
    return 0


# --------------------------------------------------------------------------------------------
# part 1: inventory of *_OFFSET constants
# --------------------------------------------------------------------------------------------
CONST_RE = re.compile(
    r"(?:pub(?:\([^)]*\))?\s+)?(?:const|static)\s+([A-Z0-9_]*OFFSET[A-Z0-9_]*)\s*:"
    r"\s*([A-Za-z0-9_:<>, ]+?)\s*=\s*([^;]+);",
    re.S,
)
LITERAL_RE = re.compile(r"^(0x[0-9A-Fa-f_]+|\d+)$")
OFFSET_OF_RE = re.compile(r"core::mem::offset_of!\s*\(\s*([A-Za-z0-9_]+)\s*,")

# INCLUSION RULE, stated so a reader can disagree with it precisely.
#
# A constant counts as a GAME STRUCT FIELD OFFSET when all of:
#   1. its name contains OFFSET and it is a `const`/`static`;
#   2. it resolves to a byte offset -- a literal, an `offset_of!` on a type that describes game
#      memory, or arithmetic over those;
#   3. it is not matched by an exclusion below.
# Everything excluded is excluded because the bytes it indexes are NOT a game object in the
# game's address space, so a 1.17 struct change cannot reach it.
EXCLUSIONS: list[tuple[str, re.Pattern, str]] = [
    (
        "os-struct",
        re.compile(
            r"^(CONTEXT_|PEB_|LDR_ENTRY_|UNICODE_STRING_|TEB_|PE_|DOS_|NT_HEADER|SECTION_HEADER"
            r"|IMAGE_|EXCEPTION_RECORD)"
        ),
        "Windows/PE/loader structure, owned by the OS and unaffected by a game patch",
    ),
    (
        "msvc-rtti",
        re.compile(r"^(THROW_INFO_|CATCHABLE_|TYPE_DESCRIPTOR_)"),
        "MSVC C++ EH/RTTI record laid out by the compiler, not by FromSoftware",
    ),
    (
        "save-file",
        re.compile(r"^(SAVE_PGD_|SLOT_BODY_|USER_DATA10_|ENT_[A-Z]*_OFFSET_OFF|BND4_|SL2_)"),
        "byte offset into an on-disk BND4/SL2 save, versioned by the save format not the image",
    ),
    (
        "param-row",
        re.compile(r"^PARAM_[A-Z0-9_]*_OFFSET$"),
        "field of a regulation.bin param row; moves with the paramdef, not with a struct",
    ),
    (
        "code-relative",
        re.compile(
            r"(RVA|PATCH|CALL_SITE|CALLSITE|INSN|PROLOGUE|STUB|TRAMPOLINE|_SITE_OFFSET$"
            r"|BYTES_OFFSET$|_CODE_)"
        ),
        "offset from a function entry to an instruction, gated already by the RVA map",
    ),
    (
        "file-format",
        re.compile(r"(GFX|SWF|TPF|DCX|FMG|SPIRV|TAG_)"),
        "offset inside an asset/file format parsed by us, not a live game object",
    ),
    (
        "not-a-field",
        re.compile(
            r"(ALIGNMENT|LIMIT|INTERVAL|COUNTDOWN|SCAN_|DIMMED_FRAME|_STEP$|MAX_ADDRESS"
            r"|^MODULE_MIN_OFFSET$|^MODULE_MAX_OFFSET$|^NEXT_INDEX_OFFSET$|^CAPS_SUBTYPE_OFFSET$)"
        ),
        "a tuning value, an index step or a scan parameter that happens to have OFFSET in its name",
    ),
]
# Files that parse BYTES OFF DISK rather than a live object. Their offsets are just as real and
# just as version-fragile, but a 1.17 image change cannot reach them -- the save format is
# versioned separately -- so they are outside this tool's question. Named by file because the
# constants inside them (`REC_NAME_OFFSET`, `SUMMARY_TABLE_OFFSET`) carry no name-level tell.
ON_DISK_FILES = {
    "crates/er-save-loader/src/bnd4.rs",
    "crates/er-save-loader/src/profile_summary.rs",
    "crates/er-profile-summary-core/src/serialized_slot.rs",
    "crates/er-save-loader/src/face_data.rs",
    "crates/er-profile-summary-core/src/face_data.rs",
}
# Crates whose code never touches a live game object.
HOST_ONLY_CRATES = {"er-gfx", "er-tpf", "soulsformats", "er-objectkit", "er-param-inspect"}
# The product DLL is `er-quickload`; everything it links is on the autoload path, because the
# autoload flow runs inside it. Computed from the Cargo.toml `path = "../..."` graph rather than
# listed by hand, so a crate added to the product cannot quietly fall out of the count.
PRODUCT_CRATE = "er-quickload"


def autoload_crates() -> set[str]:
    """Transitive path-dependency closure of the product DLL crate."""
    dep = re.compile(r'path\s*=\s*"([^"]+)"')
    seen: set[str] = set()
    queue = [PRODUCT_CRATE]
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        manifest = ROOT / "crates" / name / "Cargo.toml"
        if not manifest.is_file():
            continue
        text = manifest.read_text(encoding="utf-8", errors="replace")
        for rel in dep.findall(text):
            # Sibling `fromsoftware-rs` paths leave the workspace; only local crates are counted.
            resolved = (manifest.parent / rel).resolve()
            try:
                inside = resolved.relative_to(ROOT / "crates")
            except ValueError:
                continue
            queue.append(inside.parts[0])
    return seen


AUTOLOAD_CRATES = autoload_crates()


# --------------------------------------------------------------------------------------------
# `offset_of!` resolution
#
# 119 of the offsets are `core::mem::offset_of!(GameMan, some_field)`. That LOOKS type-safe and is
# not: the type is a 1.16.2 mirror -- either a `*Layout` struct in this repo or a struct in the
# sibling `fromsoftware-rs` -- so the number it yields is a 1.16.2 number with a type annotation
# on it. Resolving them matters twice over: it puts them in the cross-reference at all, and the
# type name is the exact struct identity that a literal offset can only guess at from its name.
# --------------------------------------------------------------------------------------------
SIBLING = ROOT.parent / "fromsoftware-rs"
PRIMITIVE_SIZES = {
    "u8": (1, 1), "i8": (1, 1), "bool": (1, 1),
    "u16": (2, 2), "i16": (2, 2),
    "u32": (4, 4), "i32": (4, 4), "f32": (4, 4),
    "u64": (8, 8), "i64": (8, 8), "f64": (8, 8), "usize": (8, 8), "isize": (8, 8),
}
_STRUCT_RE = re.compile(
    r"#\[repr\(C(?:[^)]*)\)\][^\n]*\n(?:\s*#\[[^\]]*\]\s*\n)*"
    r"\s*(?:pub(?:\([^)]*\))?\s+)?struct\s+([A-Za-z0-9_]+)\s*\{(.*?)\n\}",
    re.S,
)
_FIELD_RE = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?([A-Za-z0-9_]+)\s*:\s*([^,]+),\s*$", re.M)
_ARRAY_RE = re.compile(r"^\[\s*([A-Za-z0-9_:<>]+)\s*;\s*([0-9_xXa-fA-F]+)\s*\]$")


def collect_structs() -> dict[str, list[tuple[str, str]]]:
    """Every `#[repr(C)]` struct in this repo and in the sibling game bindings, by name."""
    out: dict[str, list[tuple[str, str]]] = {}
    roots = [ROOT / "crates"]
    if SIBLING.is_dir():
        # ONLY the Elden Ring bindings and the shared types. The sibling also carries Dark Souls 3,
        # Sekiro and Nightreign, which define their own `PlayerGameData` -- and DS3's is a
        # completely different object. Reading the wrong game's struct produces a number that is
        # confidently wrong, which is the exact failure this whole tool exists to catch.
        roots.append(SIBLING / "crates/shared")
        roots.append(SIBLING / "crates/eldenring")
    for base in roots:
        if not base.is_dir():
            continue
        for path in base.rglob("*.rs"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if "repr(C" not in text:
                continue
            for match in _STRUCT_RE.finditer(text):
                name, body = match.group(1), match.group(2)
                fields = [
                    (fname, ftype.strip())
                    for fname, ftype in _FIELD_RE.findall(body)
                    # A comment line or a doc attribute can look like a field; a type with a
                    # space in it that is not an array is not something this resolver models.
                    if not ftype.strip().startswith("//")
                ]
                # Later roots win: the Elden Ring bindings are appended last on purpose.
                out[name] = fields
    return out


def size_align(type_text: str, structs, depth=0):
    """`(size, alignment)` for a field type, or `None` when the type is not modelled.

    Returning None rather than a guess is the whole point: an invented size shifts every field
    after it, which is exactly the silent wrong offset this tool exists to find.
    """
    if depth > 8:
        return None
    type_text = type_text.strip()
    if type_text in PRIMITIVE_SIZES:
        return PRIMITIVE_SIZES[type_text]
    if (
        type_text.startswith("*")
        or type_text.startswith("&")
        or type_text.split("<")[0].strip().split("::")[-1]
        in ("Option", "OwnedPtr", "NonNull", "SharedPtr", "Box")
    ):
        return (8, 8)
    array = _ARRAY_RE.match(type_text)
    if array:
        inner = size_align(array.group(1), structs, depth + 1)
        if inner is None:
            return None
        count = int(array.group(2).replace("_", ""), 0)
        return (inner[0] * count, inner[1])
    short = type_text.split("::")[-1].split("<")[0].strip()
    if short in structs:
        return struct_size_align(short, structs, depth + 1)
    return None


def struct_layout(name: str, structs, depth=0):
    """`{field: offset}` under repr(C) rules, or None if any field type is unmodelled."""
    fields = structs.get(name)
    if fields is None or depth > 8:
        return None
    offsets: dict[str, int] = {}
    cursor = 0
    for fname, ftype in fields:
        got = size_align(ftype, structs, depth + 1)
        if got is None:
            return None
        size, align = got
        cursor = (cursor + align - 1) // align * align
        offsets[fname] = cursor
        cursor += size
    return offsets


def struct_size_align(name: str, structs, depth=0):
    fields = structs.get(name)
    if fields is None or depth > 8:
        return None
    cursor, max_align = 0, 1
    for _fname, ftype in fields:
        got = size_align(ftype, structs, depth + 1)
        if got is None:
            return None
        size, align = got
        max_align = max(max_align, align)
        cursor = (cursor + align - 1) // align * align
        cursor += size
    return ((cursor + max_align - 1) // max_align * max_align, max_align)


# When a constant is a bare literal its struct identity lives only in its NAME. This maps the
# repo's naming prefixes onto the Ghidra type names the drift is attributed to, so a value can be
# checked against the drift of the RIGHT object instead of against every object that happens to
# use the same number. Only prefixes whose meaning is unambiguous are listed; anything absent is
# reported as "struct unknown", which is the honest answer and keeps a numeric coincidence from
# being printed as a hit.
NAME_PREFIX_TYPES = [
    ("PGD_", "PlayerGameData"),
    ("PLAYER_GAME_DATA_", "PlayerGameData"),
    ("GAME_DATA_MAN_", "GameDataMan"),
    ("GAME_MAN_", "GameMan"),
    ("WORLD_CHR_MAN_", "WorldChrManImp"),
    ("CS_MENU_MAN_", "CSMenuManImp"),
    ("CSMENUMAN_", "CSMenuManImp"),
    ("CHR_ASM_", "ChrAsm"),
    ("FD4_FILECAP_", "FD4FileCap"),
    ("MENU_DATA_", "CSMenuManImp"),
]


# Two fallbacks for an `offset_of!` whose type this resolver cannot lay out (the sibling bindings
# use enums, generics and nested game types it does not model).
#
#  1. A `const _: () = assert!(NAME == 0x..)` pin somewhere in the crate. That is the compiler's
#     own answer for the current build, so it is ground truth, not a guess.
#  2. The hex embedded in the constant's name, which is a deliberate convention here
#     (`PGD_MATCHING_WEAPON_LEVEL_E2_OFFSET` is 0xe2, and the line under it asserts exactly that).
#     A name is a comment, so this is labelled `name-hint` wherever it is used, and --selftest
#     checks it against every constant whose value is known independently.
_PIN_RE = re.compile(r"assert!\(\s*([A-Z0-9_]+)\s*==\s*(0x[0-9a-fA-F_]+|\d+)\s*\)")
# The token must look like hex AND carry a digit, unless it is one or two characters. Without the
# digit rule a field named `..._FACE_OFFSET` resolves to 0xface.
_NAME_HEX_RE = re.compile(r"_([0-9A-F]{1,6})_OFFSET(?:$|_)")


def name_hint(name: str) -> int | None:
    match = _NAME_HEX_RE.search(name)
    if not match:
        return None
    token = match.group(1)
    # A single letter is a disambiguating suffix (`GX_RES_CHAIN_HOLDER_A_OFFSET`), not an offset,
    # and a multi-letter all-hex word is a word (`..._FACE_OFFSET` is not 0xface).
    if len(token) < 2:
        return None
    if len(token) > 2 and not any(c.isdigit() for c in token):
        return None
    return int(token, 16)


def collect_pins() -> dict[str, int]:
    """`const _: () = assert!(NAME == 0x..)` pins, which the compiler proves for this build."""
    pins: dict[str, int] = {}
    for path in (ROOT / "crates").rglob("*.rs"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "assert!(" not in text:
            continue
        for name, value in _PIN_RE.findall(text):
            pins.setdefault(name, int(value.replace("_", ""), 0))
    return pins


def struct_for(name: str, offset_of_type: str | None) -> str:
    if offset_of_type:
        return offset_of_type
    for prefix, type_name in NAME_PREFIX_TYPES:
        if name.startswith(prefix):
            return type_name
    return ""


def classify(name: str, crate: str, rel_path: str = "") -> tuple[bool, str]:
    if crate in HOST_ONLY_CRATES:
        return False, "host-only-crate"
    if rel_path in ON_DISK_FILES:
        return False, "save-file"
    for label, pattern, _why in EXCLUSIONS:
        if pattern.search(name):
            return False, label
    return True, "game-struct-field"


def inventory() -> list[dict]:
    structs = collect_structs()
    pins = collect_pins()
    layouts: dict[str, dict[str, int] | None] = {}
    rows: list[dict] = []
    for path in sorted((ROOT / "crates").rglob("*.rs")):
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(ROOT).as_posix()
        crate = rel.split("/")[1]
        for match in CONST_RE.finditer(text):
            name, ty, value = match.group(1), match.group(2), " ".join(match.group(3).split())
            included, why = classify(name, crate, rel)
            offset_of_type = None
            if LITERAL_RE.match(value):
                kind, resolved = "literal", int(value.replace("_", ""), 0)
            elif OFFSET_OF_RE.search(value):
                kind, resolved = "offset_of", None
                # `offset_of!(T, f)` is resolvable exactly when T's repr(C) layout is modelled;
                # a single `+ size_of::<X>()` tail is handled too, because several constants are
                # written that way to derive the field after a named one.
                inner = re.findall(
                    r"core::mem::offset_of!\s*\(\s*([A-Za-z0-9_:]+)\s*,\s*([A-Za-z0-9_]+)\s*\)",
                    value,
                )
                if inner:
                    type_name = inner[0][0].split("::")[-1]
                    offset_of_type = type_name
                    if type_name not in layouts:
                        layouts[type_name] = struct_layout(type_name, structs)
                    layout = layouts[type_name]
                    if layout is not None and inner[0][1] in layout:
                        resolved = layout[inner[0][1]]
                        for size_type in re.findall(
                            r"core::mem::size_of::<\s*([A-Za-z0-9_:]+)\s*>\(\)", value
                        ):
                            got = size_align(size_type.split("::")[-1], structs)
                            if got is None:
                                resolved = None
                                break
                            resolved += got[0]
                        if resolved is not None:
                            kind = "offset_of(resolved)"
            else:
                kind, resolved = "expr", None
            if resolved is None and name in pins:
                kind, resolved = f"{kind}(pinned)", pins[name]
            if resolved is None:
                hinted = name_hint(name)
                if hinted is not None:
                    kind, resolved = f"{kind}(name-hint)", hinted
            rows.append(
                {
                    "file": rel,
                    "line": text.count("\n", 0, match.start()) + 1,
                    "crate": crate,
                    "name": name,
                    "type": ty,
                    "value": value[:120],
                    "kind": kind,
                    "resolved": resolved,
                    "name_hint": name_hint(name),
                    "included": included,
                    "class": why,
                    "struct": struct_for(name, offset_of_type),
                    "autoload": crate in AUTOLOAD_CRATES,
                }
            )
    return rows


def print_inventory(args) -> int:
    rows = inventory()
    included = [r for r in rows if r["included"]]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "offset-inventory.json").write_text(json.dumps(rows, indent=1), "utf-8")
    print(f"*_OFFSET constants in crates/**/*.rs : {len(rows)}")
    print(f"  game struct field offsets          : {len(included)}")
    excluded = collections.Counter(r["class"] for r in rows if not r["included"])
    for label, count in excluded.most_common():
        why = next((w for lbl, _p, w in EXCLUSIONS if lbl == label), "crate never sees game memory")
        print(f"  excluded {label:<15} {count:>4}   {why}")
    print()
    print("included, by crate (A = on the product autoload path):")
    by_crate = collections.Counter(r["crate"] for r in included)
    for crate, count in by_crate.most_common():
        flag = "A" if crate in AUTOLOAD_CRATES else " "
        print(f"  {flag} {count:>4}  {crate}")
    on_path = sum(1 for r in included if r["autoload"])
    print(f"\n  on the autoload path: {on_path} of {len(included)}")
    kinds = collections.Counter(r["kind"] for r in included)
    print(f"  by shape: {dict(kinds)}")
    print(f"\nwrote {args.out_dir}/offset-inventory.json")
    return 0


# --------------------------------------------------------------------------------------------
# cross-reference
# --------------------------------------------------------------------------------------------
def load_scan(out_dir: Path) -> dict:
    path = out_dir / "field-drift.json"
    if not path.is_file():
        raise SystemExit(f"no scan output at {path}; run --scan first (it takes minutes)")
    return json.loads(path.read_text(encoding="utf-8"))


def ghidra_names(vas: list[str]) -> dict[str, str]:
    """Best-effort 1.16.2 function names, so a drift row reads as code and not as a number."""
    sys.path.insert(0, str(ROOT / "scripts" / "ghidra"))
    try:
        from mcp_query import query  # type: ignore
    except Exception:
        return {}
    out: dict[str, str] = {}
    for va in vas:
        try:
            res = query("getFunctionByAddress", {"address": va}, timeout=15).get("result") or {}
        except Exception:
            continue
        name = res.get("name")
        if name:
            out[va] = f"{name}{'  ' + res['signature'] if res.get('signature') else ''}"
    return out


def report(args) -> int:
    """Name the repo constants the measurement says are wrong.

    The join is on (STRUCTURE, offset), never on offset alone. `0xb0c` moved to `0xb60` in 1.17 --
    in `MoWwiseManImp`, the Wwise audio manager. `DIALOG_SLOT_CURSOR_B0C_OFFSET` is also `0xb0c`
    and indexes a title-screen dialog. Joining on the number alone calls that a hit, and it is
    not one; that single false positive is the difference between this tool being useful and it
    being a list of coincidences.

    So a constant is only judged when its structure is known -- exactly, from the `offset_of!`
    type it is written against, or from a naming prefix in `NAME_PREFIX_TYPES`. A constant whose
    structure cannot be named is reported as UNKNOWN rather than cleared, because "no drift was
    measured for a number" is not evidence that the field did not move.
    """
    attributed_path = args.out_dir / "field-drift-attributed.json"
    if not attributed_path.is_file():
        raise SystemExit(
            f"no attribution at {attributed_path}; run --attribute first (it needs the Ghidra "
            "daemon: bash scripts/ghidra/mcp-up-1162.sh)"
        )
    by_type = json.loads(attributed_path.read_text(encoding="utf-8"))
    per_function = json.loads((args.out_dir / "drift-functions.json").read_text(encoding="utf-8"))
    stable_by_function = {row["va_1162"]: set(row.get("stable", [])) for row in per_function}

    # For each structure: the offsets that moved, and the offsets that were seen holding still in
    # the very same functions. Those two together bracket where the insertion happened.
    struct_moved: dict[str, list[dict]] = {}
    struct_held: dict[str, set[int]] = {}
    for name, rows in by_type.items():
        struct_moved[name] = sorted(rows, key=lambda r: r["old"])
        held: set[int] = set()
        for row in rows:
            for va in row["examples"]:
                held |= stable_by_function.get(va, set())
        struct_held[name] = held

    verdicts = collections.Counter()
    findings = []
    for row in inventory():
        if not row["included"] or row["resolved"] is None:
            continue
        if args.autoload_only and not row["autoload"]:
            continue
        offset, struct = row["resolved"], row["struct"]
        if not struct:
            verdicts["UNKNOWN-STRUCT"] += 1
            continue
        moved = struct_moved.get(struct)
        if not moved:
            verdicts["NO-DRIFT-MEASURED"] += 1
            continue
        at_or_below = [m for m in moved if m["old"] <= offset]
        if not at_or_below:
            # Every measured move in this structure is ABOVE this field, and the same functions
            # were seen reading fields at or above it unchanged -- so it sits under the insertion.
            verdicts["BELOW-INSERTION"] += 1
            continue
        nearest = at_or_below[-1]
        exact = next((m for m in moved if m["old"] == offset), None)
        findings.append(
            {
                "row": row,
                "delta": nearest["delta"],
                "predicted": offset + nearest["delta"],
                "exact": exact,
                "nearest": nearest,
                "held_above": sorted(d for d in struct_held[struct] if d >= offset)[:6],
            }
        )
        verdicts["WRONG" if exact else "LIKELY-WRONG"] += 1

    print("cross-reference on (structure, offset) -- never on the offset alone\n")
    for label, count in verdicts.most_common():
        print(f"  {label:<20} {count}")
    print()
    findings.sort(key=lambda f: (f["exact"] is None, f["row"]["struct"], f["row"]["resolved"]))
    for finding in findings:
        row = finding["row"]
        grade = "WRONG" if finding["exact"] else "LIKELY WRONG"
        print(f"{grade}: {row['name']} = {row['resolved']:#x}   [{row['struct']}]")
        if finding["exact"]:
            e = finding["exact"]
            print(
                f"    measured: {e['old']:#x} -> {e['new']:#x} ({e['delta']:+#x}) in "
                f"{e['functions']} otherwise-identical function(s)"
                + ("  -- witness signature names several types" if e["ambiguous"] else "")
            )
        else:
            n = finding["nearest"]
            print(
                f"    measured: the nearest field below it, {n['old']:#x}, moved {n['delta']:+#x}"
                f" -- so {row['resolved']:#x} is above the insertion and should be "
                f"{finding['predicted']:#x}, but THIS field was never witnessed directly"
            )
        print(f"    site:     {row['file']}:{row['line']}  ({row['kind']})")
        print()
    print(
        "BELOW-INSERTION means the structure moved but only ABOVE this field, and the same\n"
        "functions read this region unchanged. NO-DRIFT-MEASURED and UNKNOWN-STRUCT are both\n"
        "'not measured', not 'fine': the function map covers 128602 of 235823 functions, so a\n"
        "structure only this repo touches can move without a single witness in that half."
    )
    return 0


# Type names that appear in almost every signature and identify nothing.
GENERIC_TYPES = {
    "void", "undefined", "undefined1", "undefined2", "undefined4", "undefined8", "bool",
    "char", "int", "uint", "long", "ulong", "longlong", "ulonglong", "float", "double", "byte",
    "short", "ushort", "code", "size_t", "wchar_t", "uint64_t", "int64_t", "uint32_t", "int32_t",
    "function",
}
_TYPE_TOKEN = re.compile(r"\b([A-Za-z_][A-Za-z_0-9:]*)\s*\*")


def function_types(cache: dict[str, dict], vas: list[str]) -> dict[str, dict]:
    """Ghidra name + pointer-parameter types for each function, memoised across calls."""
    sys.path.insert(0, str(ROOT / "scripts" / "ghidra"))
    try:
        from mcp_query import query  # type: ignore
    except Exception:
        return cache
    for va in vas:
        if va in cache:
            continue
        try:
            res = query("getFunctionByAddress", {"address": va}, timeout=20).get("result") or {}
        except Exception:
            cache[va] = {"name": "", "types": []}
            continue
        signature = res.get("signature", "") or ""
        types = [
            t.split("::")[-1]
            for t in _TYPE_TOKEN.findall(signature)
            if t.split("::")[-1] not in GENERIC_TYPES
        ]
        cache[va] = {"name": res.get("name", ""), "types": sorted(set(types))}
    return cache


def attribute(args) -> int:
    """Ask the 1.16.2 dump WHICH TYPE each drifting function operates on.

    A displacement is only half an answer. `0xb0c` moved to `0xb60` -- in `MoWwiseManImp`, whose
    layout has nothing to do with the dialog-slot object a constant of the same value indexes.
    Without the type, a numeric match reads as a hit and is a false alarm; the whole value of
    this step is turning "this number moved" into "this OBJECT moved".

    Attribution is by the pointer types in the witness function's Ghidra signature, which is
    evidence and not proof: a function that takes two pointers touches two objects, so a row
    naming several types is ambiguous and is printed as ambiguous.
    """
    data = load_scan(args.out_dir)
    cache_path = args.out_dir / "ghidra-function-types.json"
    cache = json.loads(cache_path.read_text()) if cache_path.is_file() else {}
    wanted = sorted({va for row in data["field_drift"] for va in row["examples"]})
    before = len(cache)
    function_types(cache, wanted)
    cache_path.write_text(json.dumps(cache), encoding="utf-8")
    print(f"named {len(cache) - before} new function(s); {len(cache)} cached\n")

    by_type: dict[str, list[dict]] = collections.defaultdict(list)
    unattributed = []
    for row in data["field_drift"]:
        types = collections.Counter()
        for va in row["examples"]:
            for name in cache.get(va, {}).get("types", []):
                types[name] += 1
        if not types:
            unattributed.append(row)
            continue
        # A type seen in every witness is the one the field belongs to; ties stay ambiguous.
        top = types.most_common()
        best = [name for name, count in top if count == top[0][1]]
        for name in best:
            by_type[name].append(dict(row, witnesses=types[name], candidates=len(best)))

    rows_out = []
    for name, rows in sorted(by_type.items(), key=lambda kv: -len(kv[1])):
        deltas = collections.Counter(r["delta"] for r in rows)
        offsets = sorted(r["old"] for r in rows)
        print(
            f"{name}: {len(rows)} field(s) moved, offsets {offsets[0]:#x}..{offsets[-1]:#x}, "
            f"deltas { {hex(d): c for d, c in deltas.most_common()} }"
        )
        for row in sorted(rows, key=lambda r: r["old"])[: args.limit or 200]:
            ambiguous = "  (ambiguous: several types in the signature)" if row["candidates"] > 1 else ""
            print(
                f"    {row['old']:#x} -> {row['new']:#x} ({row['delta']:+#x})  "
                f"{row['functions']} fn{ambiguous}"
            )
            rows_out.append((name, row))
        print()
    print(f"{len(unattributed)} drift row(s) have no named witness in the 1.16.2 dump")

    with (args.out_dir / "field-drift-attributed.tsv").open("w", encoding="utf-8") as handle:
        handle.write("# type\t1.16.2 disp\t1.17 disp\tdelta\tfunctions\tambiguous\n")
        for name, row in rows_out:
            handle.write(
                f"{name}\t{row['old']:#x}\t{row['new']:#x}\t{row['delta']:+#x}\t"
                f"{row['functions']}\t{'yes' if row['candidates'] > 1 else 'no'}\n"
            )
    (args.out_dir / "field-drift-attributed.json").write_text(
        json.dumps(
            {
                name: [
                    {
                        "old": r["old"],
                        "new": r["new"],
                        "delta": r["delta"],
                        "functions": r["functions"],
                        "ambiguous": r["candidates"] > 1,
                        "examples": r["examples"],
                    }
                    for r in rows
                ]
                for name, rows in by_type.items()
            }
        ),
        encoding="utf-8",
    )
    print(f"wrote {args.out_dir}/field-drift-attributed.tsv (+ .json)")
    return 0


def regions(args) -> int:
    """Cluster the raw drift rows into candidate STRUCTURES.

    A single row ("0xc30 became 0xc88") is a number. What a reader needs is the object: a run of
    offsets that all moved by the same amount, witnessed by an overlapping set of functions. Two
    rows are joined when they share a delta AND at least one witnessing function -- the function
    is the evidence that the two offsets are fields of the SAME object, which mere numeric
    adjacency is not. Each component then reports the offset range that moved, how far, and the
    functions that prove it, which Ghidra can name.
    """
    data = load_scan(args.out_dir)
    rows = [r for r in data["field_drift"] if not args.min_functions or r["functions"] >= args.min_functions]
    by_delta: dict[int, list[dict]] = collections.defaultdict(list)
    for row in rows:
        by_delta[row["delta"]].append(row)

    clusters = []
    for delta, group in by_delta.items():
        parent = list(range(len(group)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        witnesses = [set(r["examples"]) for r in group]
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                if witnesses[i] & witnesses[j]:
                    a, b = find(i), find(j)
                    if a != b:
                        parent[a] = b
        buckets: dict[int, list[dict]] = collections.defaultdict(list)
        for i, row in enumerate(group):
            buckets[find(i)].append(row)
        for members in buckets.values():
            offsets = sorted(r["old"] for r in members)
            funcs = sorted(set().union(*[set(r["examples"]) for r in members]))
            clusters.append(
                {
                    "delta": delta,
                    "low": offsets[0],
                    "high": offsets[-1],
                    "fields": len(members),
                    "functions": funcs,
                    "rows": sorted(members, key=lambda r: r["old"]),
                }
            )
    clusters.sort(key=lambda c: (-c["fields"], -len(c["functions"])))

    print(f"{len(rows)} drift rows -> {len(clusters)} candidate structures\n")
    for cluster in clusters:
        if cluster["fields"] < args.min_fields:
            continue
        print(
            f"delta {cluster['delta']:+#x}   offsets {cluster['low']:#x}..{cluster['high']:#x}"
            f"   {cluster['fields']} field(s)   {len(cluster['functions'])} witness function(s)"
        )
        moves = ", ".join(f"{r['old']:#x}->{r['new']:#x}" for r in cluster["rows"][:14])
        print(f"    {moves}{' ...' if len(cluster['rows']) > 14 else ''}")
        if args.names:
            named = ghidra_names(cluster["functions"][: args.names])
            for va in cluster["functions"][: args.names]:
                print(f"    {va}  {named.get(va, '(unnamed in the 1.16.2 dump)')}")
        else:
            print(f"    witnesses: {' '.join(cluster['functions'][:6])}")
        print()
    return 0


def explain(args) -> int:
    data = load_scan(args.out_dir)
    want = int(args.explain, 0)
    stable = {int(k): v for k, v in data["stable_displacements"].items()}
    moves = [r for r in data["field_drift"] if r["old"] == want]
    print(f"displacement {want:#x}")
    held = stable.get(want, 0)
    if not held and not moves:
        # Zero of both is NOT a clean bill of health, and printing "never observed moving" alone
        # reads exactly like one. `0xab5` lands here: the function that proves it moved,
        # `GetScadutreeBlessing`, is a leaf with no unwind record, so it is absent from `.pdata`
        # and therefore absent from the `.pdata`-derived function map this scan walks.
        print("  NO EVIDENCE EITHER WAY -- not seen moving and not seen holding still.")
        print("  Use --find-displacement to search the images directly, or read the function.")
        return 0
    print(f"  held still in {held} instructions of comparable function pairs")
    if not moves:
        print("  never observed moving in a comparable pair")
    for m in moves:
        print(
            f"  -> {m['new']:#x} ({m['delta']:+#x}) in {m['functions']} functions, "
            f"{m['insns']} instructions, bases {m['bases']}"
        )
        names = ghidra_names(m["examples"][: args.names or 4]) if args.names else {}
        for va in m["examples"][:8]:
            print(f"       {va}  {names.get(va, '')}")
    consumers = [
        r
        for r in inventory()
        if r["included"] and r["kind"] == "literal" and r["resolved"] == want
    ]
    if consumers:
        print(f"\n  repo constants holding {want:#x}:")
        for r in consumers:
            print(f"    {r['name']:<52} {r['file']}:{r['line']}")
    return 0


# --------------------------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------------------------
def _ensure_capstone():
    """Re-exec under uv when capstone is absent; there is no system pip here.

    The re-exec must name `python3`, not `sys.executable`: an absolute path to the system
    interpreter ignores the ephemeral environment uv just built, so the import fails again and
    the process re-execs until uv's recursion guard stops it with an unrelated message.
    """
    try:
        import capstone  # noqa: F401
    except ImportError:
        if os.environ.get("_DRIFT_UNDER_UV"):
            raise SystemExit("capstone is still missing under `uv run --with capstone`")
        os.environ["_DRIFT_UNDER_UV"] = "1"
        os.execvp("uv", ["uv", "run", "--with", "capstone", "python3", *sys.argv])


# Hand-written encodings. Each pair is (bytes_1162, bytes_1170, what it must be classified as).
# Keeping them literal means the classifier is tested against x86 rather than against itself.
SYNTHETIC = [
    (
        "field displacement moves",
        bytes.fromhex("488b4110c3"),  # mov rax, [rcx+0x10]; ret
        bytes.fromhex("488b4118c3"),  # mov rax, [rcx+0x18]; ret
        DRIFT,
        [("rcx", 0x10, 0x18)],
    ),
    (
        "disp8 widening to disp32 is still one move",
        bytes.fromhex("488b417cc3"),  # mov rax, [rcx+0x7c]
        bytes.fromhex("488b8184000000c3"),  # mov rax, [rcx+0x84]
        DRIFT,
        [("rcx", 0x7C, 0x84)],
    ),
    (
        "rip-relative displacement is not a field",
        bytes.fromhex("488b0510000000c3"),
        bytes.fromhex("488b0520000000c3"),
        STABLE,
        [],
    ),
    (
        "stack slot is not a field",
        bytes.fromhex("488b442420c3"),  # mov rax, [rsp+0x20]
        bytes.fromhex("488b442428c3"),  # mov rax, [rsp+0x28]
        STABLE,
        [],
    ),
    (
        "different code is refused, not explained",
        bytes.fromhex("488b4110c3"),  # mov rax, [rcx+0x10]
        bytes.fromhex("488b5110c3"),  # mov rdx, [rcx+0x10]
        SHAPE_DIFF,
        [],
    ),
    (
        "identical bodies drift nowhere",
        bytes.fromhex("488b4110c3"),
        bytes.fromhex("488b4110c3"),
        STABLE,
        [],
    ),
    (
        "indexed operand keeps its base and its displacement",
        bytes.fromhex("488b84d1b00a0000c3"),  # mov rax, [rcx + rdx*8 + 0xab0]
        bytes.fromhex("488b84d1b80a0000c3"),  # mov rax, [rcx + rdx*8 + 0xab8]
        DRIFT,
        [("rcx", 0xAB0, 0xAB8)],
    ),
    (
        "an image-base-relative global is not a field",
        # mov ecx, dword ptr [r14 + rax*4 + 0x3030aa0]  -- r14 holds 0x140000000
        bytes.fromhex("418b8c86a00a0303c3"),
        bytes.fromhex("418b8c86280c3303c3"),
        STABLE,
        [],
    ),
    (
        "an immediate change is not a field move",
        bytes.fromhex("81f9d2040000c3"),  # cmp ecx, 0x4d2
        bytes.fromhex("81f9d3040000c3"),  # cmp ecx, 0x4d3
        MIXED,
        [],
    ),
]

# The one field move this repo already knows by hand, from the live 1.17 process: PlayerGameData
# grew 8 bytes, so GetScadutreeBlessing's two reads moved and its third did not.
# `.pdata` deliberately omits leaf functions that need no unwind data, and this 24-byte getter is
# one of them -- so its extent is stated here rather than looked up. That omission does not affect
# --scan, whose work list is itself derived from `.pdata`.
KNOWN = {
    "va_1162": 0x14025F5F0,
    "va_1170": 0x14025F5D0,
    "length": 0x18,
    "expect_drift": [("rcx", 0xAB5, 0xABD), ("rcx", 0xAB4, 0xABC)],
    "expect_stable": 0xFC,
}


def selftest(args) -> int:
    _ensure_capstone()
    from capstone import CS_ARCH_X86, CS_MODE_64, Cs

    md = Cs(CS_ARCH_X86, CS_MODE_64)
    failures = []

    print("operand parsing")
    parse_cases = [
        ("qword ptr [rcx + 0xab5], 0", [("rcx", 0xAB5)], [0]),
        ("eax, byte ptr [rbp - 0x18]", [("rbp", -0x18)], []),
        ("rax, qword ptr [rip + 0x2a8f9e8]", [("rip", 0x2A8F9E8)], []),
        ("rax, qword ptr [rcx + rdx*8 + 0x10]", [("rcx", 0x10)], []),
        ("qword ptr [0x143d71580], rax", [("", 0x143D71580)], []),
        ("rax, qword ptr [rcx]", [("rcx", 0)], []),
        ("dword ptr [rax + 0x10], 0x20", [("rax", 0x10)], [0x20]),
    ]
    for text, want_mem, want_imm in parse_cases:
        shape, mem = split_memory(text)
        imm = immediates(shape, text)
        ok = mem == want_mem and imm == want_imm
        print(f"  {'ok  ' if ok else 'FAIL'} {text!r} -> {mem} imm={imm}")
        if not ok:
            failures.append(text)

    print("\nsynthetic instruction pairs")
    for label, a, b, want_verdict, want_drift in SYNTHETIC:
        cmp = compare_bodies(md, a, BASE, b, BASE)
        ok = cmp.verdict == want_verdict and cmp.field_drift == want_drift
        print(
            f"  {'ok  ' if ok else 'FAIL'} {label}: {cmp.verdict}"
            f"{' ' + str(cmp.field_drift) if cmp.field_drift else ''}"
            f"{'' if ok else f'  (wanted {want_verdict} {want_drift})'}"
        )
        if not ok:
            failures.append(label)

    print("\nknown 1.17 field move, decoded from the two images")
    if not IMAGE_1162.is_file() or not IMAGE_1170.is_file():
        print("  SKIP images absent")
    else:
        old, new = Image(IMAGE_1162), Image(IMAGE_1170)
        a_rva = KNOWN["va_1162"] - BASE
        b_rva = KNOWN["va_1170"] - BASE
        length = KNOWN["length"]
        cmp = compare_bodies(
            md,
            old.data[a_rva : a_rva + length],
            KNOWN["va_1162"],
            new.data[b_rva : b_rva + length],
            KNOWN["va_1170"],
        )
        ok = cmp.verdict == DRIFT and cmp.field_drift == KNOWN["expect_drift"]
        print(
            f"  {'ok  ' if ok else 'FAIL'} GetScadutreeBlessing {cmp.verdict} "
            f"{[(b, hex(o), hex(n)) for b, o, n in cmp.field_drift]}"
        )
        if not ok:
            failures.append("GetScadutreeBlessing drift")
        held = KNOWN["expect_stable"] in cmp.stable
        print(f"  {'ok  ' if held else 'FAIL'} the same function's {KNOWN['expect_stable']:#x} "
              "read is reported as unchanged")
        if not held:
            failures.append("GetScadutreeBlessing stable field")

        print("\nfunction table")
        pairs = load_pairs(FUNCTION_MAP) if FUNCTION_MAP.is_file() else []
        ok = len(pairs) > 100000
        print(f"  {'ok  ' if ok else 'FAIL'} {len(pairs)} mapped function pairs available")
        if not ok:
            failures.append("function map")

    print("\ninventory rule")
    rows = inventory()
    ok = len(rows) > 500
    print(f"  {'ok  ' if ok else 'FAIL'} parsed {len(rows)} *_OFFSET constants")
    if not ok:
        failures.append("inventory parse")
    spot = {
        "GAME_MAN_CURRENT_MAP_C30_OFFSET": True,
        "CONTEXT_RIP_OFFSET": False,
        "SAVE_PGD_LEVEL_OFFSET": False,
        "PEB_LDR_OFFSET": False,
        "FD4_FILECAP_STATUS_88_OFFSET": True,
    }
    by_name = {r["name"]: r for r in rows}
    for name, want in spot.items():
        row = by_name.get(name)
        got = row["included"] if row else None
        ok = got == want
        print(f"  {'ok  ' if ok else 'FAIL'} {name} included={got} (wanted {want})")
        if not ok:
            failures.append(name)

    print("\nnaming convention vs independently resolved values")
    # Constants whose embedded hex deliberately names something OTHER than their own offset.
    # Listed by hand so that a NEW disagreement is a selftest failure rather than noise: the
    # name is what resolves an `offset_of!` this tool cannot lay out, so it has to stay honest.
    KNOWN_NAME_DIVERGENCE = {
        # names the SAVE-FILE field C4, while the value is the in-memory record's 0x293
        "PROFILE_SUMMARY_FIELD_C4_OFFSET",
        # renamed field, stale number kept in the symbol
        "PADMAPS_88_OFFSET",
    }
    disagree = [
        r
        for r in rows
        if r["included"]
        and r["resolved"] is not None
        and "name-hint" not in r["kind"]
        and r["name_hint"] is not None
        and r["name_hint"] != r["resolved"]
        and r["name"] not in KNOWN_NAME_DIVERGENCE
    ]
    checked = [
        r for r in rows if r["included"] and r["resolved"] is not None and r["name_hint"] is not None
    ]
    ok = len(disagree) == 0
    print(
        f"  {'ok  ' if ok else 'FAIL'} {len(checked)} constants embed a hex offset in their name; "
        f"{len(disagree)} disagree with the value the code actually computes"
    )
    for r in disagree[:10]:
        print(f"       {r['name']} = {r['resolved']:#x} but the name says {r['name_hint']:#x}")
    if not ok:
        failures.append("naming convention")

    if failures:
        print(f"\nselftest FAILED: {len(failures)} case(s): {failures}")
        return 1
    print("\nselftest passed")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--selftest", action="store_true", help="assert the classifier and the parser")
    ap.add_argument("--scan", action="store_true", help="decode every mapped pair (minutes)")
    ap.add_argument("--inventory", action="store_true", help="part 1: count *_OFFSET constants")
    ap.add_argument("--report", action="store_true", help="cross-reference inventory x drift")
    ap.add_argument("--explain", metavar="DISP", help="everything measured about one displacement")
    ap.add_argument("--regions", action="store_true", help="cluster drift into candidate structures")
    ap.add_argument("--attribute", action="store_true", help="name the drifting TYPE via Ghidra")
    ap.add_argument(
        "--find-displacement",
        metavar="N[,N...]",
        help="list every mapped function using this displacement and what happened to it",
    )
    ap.add_argument(
        "--pairs",
        metavar="TSV",
        help="compare only these 1.16.2/1.17 pairs (e.g. docs/recon/rva-map-1162-to-1170.verified.tsv)",
    )
    ap.add_argument("--min-fields", type=int, default=1, help="--regions: hide clusters below this")
    ap.add_argument("--min-functions", type=int, default=0, help="--regions: drop thin rows")
    ap.add_argument("--autoload-only", action="store_true", help="--report: autoload crates only")
    ap.add_argument("--names", type=int, default=0, metavar="N", help="annotate N examples via Ghidra")
    ap.add_argument("--limit", type=int, default=0, help="--scan: stop after N pairs (smoke)")
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    if args.selftest:
        return selftest(args)
    if args.inventory:
        return print_inventory(args)
    if args.explain:
        return explain(args)
    if args.find_displacement:
        return find_displacement(args)
    if args.pairs:
        return pairs_mode(args)
    if args.attribute:
        return attribute(args)
    if args.regions:
        return regions(args)
    if args.report:
        return report(args)
    if args.scan:
        return scan(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
