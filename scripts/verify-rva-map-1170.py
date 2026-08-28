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


def decode(image, va, limit=DECODE_LIMIT):
    from capstone import CS_ARCH_X86, CS_MODE_64, Cs

    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True
    offset = va - BASE
    if offset < 0 or offset >= len(image):
        return []
    out = []
    for insn in md.disasm(bytes(image[offset : offset + DECODE_BYTES]), va):
        out.append(normalise(insn))
        if len(out) >= limit:
            break
        # Stop at a plain return: past it is the next function, whose drift is not this
        # function's business.
        if insn.mnemonic == "ret":
            break
    return out


def compare(old_image, new_image, old_va, new_va):
    left = decode(old_image, old_va)
    right = decode(new_image, new_va)
    if not left or not right:
        return {"verdict": "UNDECODABLE", "ratio": 0.0, "compared": 0, "first_diff": None}
    compared = min(len(left), len(right))
    first_diff = next((i for i in range(compared) if left[i] != right[i]), None)
    same = sum(1 for i in range(compared) if left[i] == right[i])
    ratio = same / compared
    if first_diff is None and len(left) == len(right):
        verdict = "IDENTICAL" if compared >= THIN_EVIDENCE else "IDENTICAL-SHORT"
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
    }


def load_map():
    pairs = []
    for line in open(MAP_TSV, encoding="utf-8"):
        if line.startswith("#") or not line.strip():
            continue
        fields = line.rstrip("\n").split("\t")
        if len(fields) < 4 or fields[1] == "-":
            continue
        pairs.append((int(fields[0], 16), int(fields[1], 16), fields[3]))
    return pairs


def main():
    parser = argparse.ArgumentParser(
        description="Verify mapped 1.17 addresses are the same function as their 1.16.2 original."
    )
    parser.add_argument("vas", nargs="*", help="1.16.2 VAs to check (default: the whole table)")
    parser.add_argument("--tsv", metavar="PATH", help="write the verdicts here")
    parser.add_argument(
        "--min-ratio",
        type=float,
        default=1.0,
        help="ratio at or above which a pair is listed as accepted (default 1.0)",
    )
    args = parser.parse_args()

    for image in (OLD_IMAGE, NEW_IMAGE):
        if not os.path.exists(image):
            sys.exit(f"missing image: {image}")
    old_image = open(OLD_IMAGE, "rb").read()
    new_image = open(NEW_IMAGE, "rb").read()

    pairs = load_map()
    if args.vas:
        wanted = {int(v, 0) for v in args.vas}
        pairs = [p for p in pairs if p[0] in wanted]
    if not pairs:
        sys.exit("nothing to verify")

    rows = []
    for old_va, new_va, how in pairs:
        result = compare(old_image, new_image, old_va, new_va)
        rows.append((old_va, new_va, how, result))
        diff = "" if result["first_diff"] is None else f", first diff at insn {result['first_diff']}"
        print(
            f"{old_va:#x} -> {new_va:#x}  {result['verdict']:<16} "
            f"{result['ratio']:.2f} over {result['compared']} insns{diff}   [{how}]"
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
            handle.write("# 1.16.2 VA\t1.17 VA\tverdict\tratio\tinsns compared\thow it was mapped\n")
            handle.write(
                "# Generated by scripts/verify-rva-map-1170.py. A verdict is evidence, not\n"
                "# permission: IDENTICAL means the normalised instruction sequences agree, which\n"
                "# cannot see a change in what a called function does.\n"
            )
            for old_va, new_va, how, result in rows:
                handle.write(
                    f"{old_va:#x}\t{new_va:#x}\t{result['verdict']}\t{result['ratio']:.3f}\t"
                    f"{result['compared']}\t{how}\n"
                )
        print(f"wrote {args.tsv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
