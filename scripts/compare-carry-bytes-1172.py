#!/usr/bin/env python3
"""Carry 1.17.1 rvas to 2.7.2.0 and compare the bytes at both ends.

    python3 scripts/compare-carry-bytes-1172.py [-n N] [--from1170] <rva>...

Each rva is hex, measured on 1.17.1 (or on 1.17.0 with `--from1170`, which first applies the
1.17.0 -> 1.17.1 rule of `scripts/map-rvas-1170-to-1171.py`: +0x70 for rvas in
[0xafefe9, 0x29a5800), else unchanged, and reports whether the 1.17.0 and 1.17.1 bytes agree).
The carry is read from `docs/recon/carry-1171-to-1172.tsv`, the same segments the runtime
`carry_1171_rva` uses; the bytes come from the flat de-Arxan'd images in the main checkout
(file offset == rva). Override the image directory with `ER_DEOBF_DIR`.

Read-only. A `MATCH` says the first N bytes agree; it does not say rel32 operands still reach the
same function, so an `E8`/`E9`/RIP-relative operand that differs needs its target carried too.
"""

import bisect
import os
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TSV = REPO / "docs/recon/carry-1171-to-1172.tsv"


def image_dir() -> Path:
    env = os.environ.get("ER_DEOBF_DIR")
    if env:
        return Path(env)
    # A worktree lives at <main>/.worktrees/<name>; the images sit in the main checkout.
    for candidate in (REPO, *REPO.parents):
        if (candidate / "eldenring-deobf-1.17.2.bin").exists():
            return candidate
    sys.exit("no eldenring-deobf-1.17.2.bin above this checkout; set ER_DEOBF_DIR")


def load_rows():
    rows = []
    for line in TSV.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        kind, lo, hi, delta = line.split()
        rows.append((int(lo, 16), int(hi, 16), int(delta, 16), kind))
    return rows


def carry(rows, rva):
    for lo, hi, delta, kind in rows:
        if lo <= rva < hi:
            return rva + delta, kind
    return None, None


def pdata(image: bytes) -> list[tuple[int, int]]:
    """Sorted (begin, end) of every `.pdata` record."""
    pe = struct.unpack_from("<I", image, 0x3C)[0]
    rva, size = struct.unpack_from("<II", image, pe + 0x18 + 0x70 + 3 * 8)
    out = set()
    for off in range(0, size - 11, 12):
        begin, end, _ = struct.unpack_from("<III", image, rva + off)
        if 0 < begin < end:
            out.add((begin, end))
    return sorted(out)


def containing(records, rva):
    """`(begin, length, offset)` of the `.pdata` record holding rva, or None."""
    at = bisect.bisect_right(records, (rva, 1 << 40)) - 1
    if at >= 0 and records[at][0] <= rva < records[at][1]:
        begin, end = records[at]
        return f"fn {begin:#x} len {end - begin:#x} +{rva - begin:#x}"
    return "no .pdata record"


def main() -> None:
    args = sys.argv[1:]
    n = 16
    from1170 = False
    while args and args[0].startswith("-"):
        if args[0] == "-n":
            n = int(args[1], 0)
            args = args[2:]
        elif args[0] == "--from1170":
            from1170 = True
            args = args[1:]
        else:
            sys.exit(__doc__)
    if not args:
        sys.exit(__doc__)
    d = image_dir()
    a0 = (d / "eldenring-deobf-1.17.bin").read_bytes() if from1170 else b""
    a = (d / "eldenring-deobf-1.17.1.bin").read_bytes()
    b = (d / "eldenring-deobf-1.17.2.bin").read_bytes()
    rows = load_rows()
    pa, pb = pdata(a), pdata(b)
    for text in args:
        rva = int(text, 16)
        if from1170:
            src = rva
            rva = rva + 0x70 if 0xAFEFE9 <= rva < 0x29A5800 else rva
            same = a0[src : src + n] == a[rva : rva + n]
            print(f"1.17.0 {src:#x} -> 1.17.1 {rva:#x} bytes-equal={same}")
        dest, kind = carry(rows, rva)
        if dest is None:
            print(f"{rva:#x}: REFUSED  1171 {a[rva : rva + n].hex(' ')}")
            continue
        x, y = a[rva : rva + n], b[dest : dest + n]
        verdict = "MATCH" if x == y else "DIFF"
        print(f"{rva:#x} -> {dest:#x} [{kind}] {verdict}")
        print(f"   1171 {x.hex(' ')}  [{containing(pa, rva)}]")
        print(f"   1172 {y.hex(' ')}  [{containing(pb, dest)}]")


if __name__ == "__main__":
    main()
