#!/usr/bin/env python3
"""Compare a function's instruction shape across two de-Arxan'd ELDEN RING images.

    uv run --with capstone python3 scripts/compare-fn-shape.py 0x140604630:0x140605930 ...
    uv run --with capstone python3 scripts/compare-fn-shape.py --a eldenring-deobf.bin \
        --b eldenring-deobf-1.17.2.bin --show 0x1406c7000:0x1406c8300
    uv run --with capstone python3 scripts/compare-fn-shape.py --selftest

Each argument is `<va in image A>:<va in image B>`. Defaults: A is the 1.16.2 image
(`eldenring-deobf.bin`, the one the named Ghidra dump on :8765 matches at shift 0), B is the
installed 2.7.2.0 image (`eldenring-deobf-1.17.2.bin`). Both are flat images (file offset == rva)
in the main checkout; override the directory with `ER_DEOBF_DIR`.

Decodes each function over its own `.pdata` extent (the exception directory of each image) when
the address begins a `.pdata` entry, so Arxan residue past the end is never compared; a leaf with
no entry is decoded up to the first `ret`/`jmp` followed by `int3` padding, capped at --max. Normalised shape per instruction: mnemonic plus
operands with every immediate, displacement and branch target replaced by `#`, the way
`map-rvas-1162-to-1170.py` masks them. `SAME n/n` means every decoded instruction has the same
shape; anything else names the first differing index. Displacements on non-RIP memory operands
(struct offsets) are kept in a second, stricter comparison, because a struct offset that moved is
exactly the kind of change that makes a call with a hand-built argument wrong; `offsets SAME`
means those agree too.

A shape match is evidence that the two are the same function, not proof that every callee is the
same callee. `--show` prints both listings side by side for reading.
"""
import argparse
import bisect
import os
import re
import struct
import sys
from pathlib import Path

IMAGE_BASE = 0x140000000
REPO = Path(__file__).resolve().parent.parent


def image_dir():
    env = os.environ.get("ER_DEOBF_DIR")
    if env:
        return Path(env)
    for candidate in (REPO, *REPO.parents):
        if (candidate / "eldenring-deobf-1.17.2.bin").exists():
            return candidate
    sys.exit("no eldenring-deobf-1.17.2.bin above this checkout; set ER_DEOBF_DIR")


def pdata(blob):
    """Sorted (begin rva, end rva) of the image's RUNTIME_FUNCTION table (exception directory)."""
    pe = struct.unpack_from("<I", blob, 0x3C)[0]
    rva, size = struct.unpack_from("<II", blob, pe + 24 + 112 + 3 * 8)
    entries = []
    for i in range(size // 12):
        begin, end, _ = struct.unpack_from("<III", blob, rva + i * 12)
        entries.append((begin, end))
    entries.sort()
    return entries


def extent(table, va):
    """End va of the .pdata entry that begins exactly at `va`, or None (a leaf, or mid-function)."""
    rva = va - IMAGE_BASE
    i = bisect.bisect_left(table, (rva, 0))
    if i < len(table) and table[i][0] == rva:
        return IMAGE_BASE + table[i][1]
    return None


def decode(cs, blob, va, limit, end=None):
    """Instructions from `va` up to `end` (the .pdata extent) when known, else up to the first
    ret/jmp followed by int3 padding."""
    off = va - IMAGE_BASE
    window = blob[off: (end - IMAGE_BASE) if end else off + limit * 16]
    out = []
    for insn in cs.disasm(window, va):
        out.append(insn)
        if len(out) >= limit:
            break
        if end is None and insn.mnemonic in ("ret", "jmp", "int3"):
            nxt = off + (insn.address - va) + insn.size
            if blob[nxt: nxt + 1] in (b"\xcc", b""):
                break
    return out


HEX = re.compile(r"0x[0-9a-f]+")


def shape(insn):
    return insn.mnemonic + " " + HEX.sub("#", insn.op_str)


def strict(insn):
    """Shape, but keeping non-RIP memory displacements (struct offsets) and small immediates."""
    text = insn.op_str
    if insn.mnemonic.startswith("j") or insn.mnemonic == "call" or "rip" in text:
        return shape(insn)
    return insn.mnemonic + " " + text


def compare(cs, a_blob, b_blob, a_va, b_va, limit, a_end=None, b_end=None):
    a = decode(cs, a_blob, a_va, limit, a_end)
    b = decode(cs, b_blob, b_va, limit, b_end)
    n = min(len(a), len(b))
    first_shape = next((i for i in range(n) if shape(a[i]) != shape(b[i])), None)
    first_strict = next((i for i in range(n) if strict(a[i]) != strict(b[i])), None)
    return a, b, n, first_shape, first_strict


def selftest():
    from capstone import CS_ARCH_X86, CS_MODE_64, Cs
    cs = Cs(CS_ARCH_X86, CS_MODE_64)
    # mov eax,[rsi+0xa4]; mov [rbp-0x44],eax; ret ; int3
    body = bytes.fromhex("8b86a4000000" "8945bc" "c3" "cc")
    moved = bytes.fromhex("8b86a8000000" "8945bc" "c3" "cc")
    blob_a = bytes(0x10) + body
    blob_b = bytes(0x10) + moved
    a, b, n, fs, fx = compare(cs, blob_a, blob_b, IMAGE_BASE + 0x10, IMAGE_BASE + 0x10, 50)
    assert n == 3 and fs is None, (n, fs)
    assert fx == 0, "a moved struct offset must show in the strict comparison"
    a, b, n, fs, fx = compare(cs, blob_a, blob_a, IMAGE_BASE + 0x10, IMAGE_BASE + 0x10, 50)
    assert fs is None and fx is None
    print("selftest: ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pairs", nargs="*")
    ap.add_argument("--a", default="eldenring-deobf.bin")
    ap.add_argument("--b", default="eldenring-deobf-1.17.2.bin")
    ap.add_argument("--max", type=int, default=400)
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return 0
    from capstone import CS_ARCH_X86, CS_MODE_64, Cs
    cs = Cs(CS_ARCH_X86, CS_MODE_64)
    d = image_dir()
    a_blob = (d / args.a).read_bytes()
    b_blob = (d / args.b).read_bytes()
    a_table, b_table = pdata(a_blob), pdata(b_blob)
    bad = 0
    for pair in args.pairs:
        a_s, b_s = pair.split(":")
        a_va, b_va = int(a_s, 16), int(b_s, 16)
        a_end, b_end = extent(a_table, a_va), extent(b_table, b_va)
        a, b, n, fs, fx = compare(cs, a_blob, b_blob, a_va, b_va, args.max, a_end, b_end)
        raw = a_blob[a_va - IMAGE_BASE: a_va - IMAGE_BASE + 16] == \
            b_blob[b_va - IMAGE_BASE: b_va - IMAGE_BASE + 16]
        verdict = "SAME %d/%d" % (n, n) if fs is None and len(a) == len(b) else (
            "SAME-PREFIX %d (lengths %d vs %d)" % (n, len(a), len(b)) if fs is None
            else "DIFFERS at %d of %d/%d" % (fs, len(a), len(b)))
        offs = "offsets SAME" if fx is None else "offsets differ at %d" % fx
        body = "whole .pdata body" if a_end and b_end else "no .pdata entry, decoded to ret"
        print("%s -> %s  %s, %s, first16 %s, %s" % (
            hex(a_va), hex(b_va), verdict, offs, "equal" if raw else "differ", body))
        if fs is not None or (fx is not None and fx < n):
            bad += 1
        if args.show:
            for i in range(max(len(a), len(b))):
                la = "%x %s %s" % (a[i].address, a[i].mnemonic, a[i].op_str) if i < len(a) else ""
                lb = "%x %s %s" % (b[i].address, b[i].mnemonic, b[i].op_str) if i < len(b) else ""
                mark = " " if i < n and strict(a[i]) == strict(b[i]) else "*"
                print("  %s %-60s | %s" % (mark, la, lb))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
