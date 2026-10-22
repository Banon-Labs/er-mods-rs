#!/usr/bin/env python3
"""Find the struct offsets and immediates ELDEN RING 2.7.2.0 changed inside functions the carry keeps.

    nice -n 19 uv run --with capstone python3 scripts/diff-struct-offsets-1171-1172.py [--jobs N] [--top N]

# Why this exists

`docs/recon/carry-1171-to-1172.tsv` pairs a 1.17.1 function with a 2.7.2.0 one by `.pdata` length
and by its RIP-relative operands landing where the new image's do. Neither test reads a
register-based displacement or an immediate, so a function whose body is the same length but reads
`[rax+0x1ecf0]` where it read `[rax+0x1ece0]` is carried as if nothing changed. That is exactly
what 2.7.2.0 did to `WorldChrManImp`: two qwords inserted at `+0x1e5f0` moved every later member
`+0x10`, and the carry, correctly for addresses, says nothing about it. Any DLL that reads such a
member at a constant offset then reads the neighbouring field.

So this walks every carried pair of equal length, disassembles both bodies where their bytes
differ, normalises away what a move is expected to change (RIP-relative displacements and absolute
branch targets), and reports what is left: per `(old -> new)` displacement or immediate, how many
instructions changed that way and a few sites. A layout change shows up as one delta repeated
across many functions; a one-off is a code change.

Read-only. The images are the flat de-Arxan'd ones in the main checkout (`ER_DEOBF_DIR` to
override); the carry is `docs/recon/carry-1171-to-1172.tsv`.
"""

import bisect
import collections
import multiprocessing
import os
import re
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TSV = REPO / "docs/recon/carry-1171-to-1172.tsv"
BASE = 0x1_4000_0000
TEXT_END = 0x29A5800


def image_dir() -> Path:
    env = os.environ.get("ER_DEOBF_DIR")
    if env:
        return Path(env)
    for candidate in (REPO, *REPO.parents):
        if (candidate / "eldenring-deobf-1.17.2.bin").exists():
            return candidate
    sys.exit("no eldenring-deobf-1.17.2.bin above this checkout; set ER_DEOBF_DIR")


def load_carry():
    rows = []
    for line in TSV.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        kind, lo, hi, delta = line.split()
        rows.append((int(lo, 16), int(hi, 16), int(delta, 16)))
    rows.sort()
    return rows


def pdata(img: bytes) -> list[tuple[int, int]]:
    pe = struct.unpack_from("<I", img, 0x3C)[0]
    rva, size = struct.unpack_from("<II", img, pe + 0x18 + 0x70 + 3 * 8)
    out = set()
    for off in range(0, size - 11, 12):
        begin, end, _ = struct.unpack_from("<III", img, rva + off)
        if 0 < begin < end:
            out.add((begin, end))
    return sorted(out)


RIP = re.compile(r"\[rip [+-] 0x[0-9a-f]+\]")
ABS = re.compile(r"\b0x14[0-9a-f]{7}\b")
HEX = re.compile(r"0x[0-9a-f]+|\b\d+\b")

_A = _B = None


def _init(dir_: str):
    global _A, _B
    _A = (Path(dir_) / "eldenring-deobf-1.17.1.bin").read_bytes()
    _B = (Path(dir_) / "eldenring-deobf-1.17.2.bin").read_bytes()


def normalise(op: str) -> str:
    return ABS.sub("ADDR", RIP.sub("[rip+X]", op))


def diff_pairs(pairs):
    import capstone

    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    changes = []
    for b1, b2, n in pairs:
        x, y = _A[b1 : b1 + n], _B[b2 : b2 + n]
        if x == y:
            continue
        ia = list(md.disasm_lite(x, BASE + b1))
        ib = list(md.disasm_lite(y, BASE + b2))
        if len(ia) != len(ib):
            changes.append(("shape", f"{len(ia)} vs {len(ib)} insns", b1, b2))
            continue
        for (a_at, a_len, a_mn, a_op), (b_at, b_len, b_mn, b_op) in zip(ia, ib):
            if a_len != b_len or a_mn != b_mn:
                changes.append(("insn", f"{a_mn} {a_op} -> {b_mn} {b_op}", a_at - BASE, b_at - BASE))
                continue
            na, nb = normalise(a_op), normalise(b_op)
            if na == nb:
                continue
            ta, tb = HEX.findall(na), HEX.findall(nb)
            if HEX.sub("N", na) == HEX.sub("N", nb) and len(ta) == len(tb):
                for va, vb in zip(ta, tb):
                    if va != vb:
                        kind = "disp" if "[" in na else "imm"
                        changes.append((kind, f"{va} -> {vb}", a_at - BASE, b_at - BASE, a_mn, a_op))
            else:
                changes.append(("operand", f"{a_mn} {a_op} -> {b_op}", a_at - BASE, b_at - BASE))
    return changes


def main() -> None:
    args = sys.argv[1:]
    jobs = int(args[args.index("--jobs") + 1]) if "--jobs" in args else 2
    top = int(args[args.index("--top") + 1]) if "--top" in args else 60
    d = image_dir()
    a = (d / "eldenring-deobf-1.17.1.bin").read_bytes()
    b = (d / "eldenring-deobf-1.17.2.bin").read_bytes()
    carry = load_carry()
    lows = [r[0] for r in carry]
    pb = set(pdata(b))
    pairs = []
    for begin, end in pdata(a):
        if begin >= TEXT_END:
            continue
        at = bisect.bisect_right(lows, begin) - 1
        if at < 0:
            continue
        lo, hi, delta = carry[at]
        if not (lo <= begin < hi):
            continue
        if (begin + delta, end + delta) in pb:
            pairs.append((begin, begin + delta, end - begin))
    del a, b
    chunks = [pairs[i::jobs * 8] for i in range(jobs * 8)]
    with multiprocessing.Pool(jobs, initializer=_init, initargs=(str(d),)) as pool:
        results = pool.map(diff_pairs, chunks)
    by = collections.defaultdict(list)
    for chunk in results:
        for change in chunk:
            by[(change[0], change[1])].append(change[2:])
    print(f"{len(pairs)} carried equal-length function pairs compared")

    def carried(rva: int) -> int | None:
        at = bisect.bisect_right(lows, rva) - 1
        if at >= 0 and carry[at][0] <= rva < carry[at][1]:
            return rva + carry[at][2]
        return None

    # An image-relative operand (`[base + idx*4 + rva]`, a jump table, an rva immediate) moving
    # with the carry is the move itself, not a layout change; alignment filler flipping between
    # `int3` and `nop` is not code at all. Both are counted and set aside.
    moved_with_image = filler = 0
    kept = {}
    for (kind, what), sites in by.items():
        if kind in ("disp", "imm"):
            old, new = (int(v, 0) for v in what.split(" -> "))
            if carried(old) == new:
                moved_with_image += len(sites)
                continue
        if kind == "insn" and what.replace(" ", "") in ("int3->nop", "nop->int3"):
            filler += len(sites)
            continue
        kept[(kind, what)] = sites
    print(f"set aside: {moved_with_image} image-relative operands that moved with the carry, "
          f"{filler} int3/nop filler swaps")
    if "--sites" in args:
        # Every kept change, one per line, for intersecting with the functions a DLL touches.
        path = Path(args[args.index("--sites") + 1])
        with path.open("w") as out:
            for (kind, what), sites in sorted(kept.items()):
                for site in sites:
                    out.write(f"{site[0]:#x}\t{site[1]:#x}\t{kind}\t{what}\n")
        print(f"sites written to {path}")
    ranked = sorted(kept.items(), key=lambda kv: -len(kv[1]))
    for (kind, what), sites in ranked[:top]:
        sample = ", ".join(f"{s[0] + BASE:#x}" for s in sites[:4])
        extra = f"  e.g. `{sites[0][2]} {sites[0][3]}`" if kind in ("disp", "imm") else ""
        print(f"{len(sites):6}  {kind:7} {what:40} {sample}{extra}")


if __name__ == "__main__":
    main()
