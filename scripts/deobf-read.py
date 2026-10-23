#!/usr/bin/env python3
"""Readable disassembly of Arxan-obfuscated functions in eldenring-deobf-1.17.2.bin.

Arxan turns most function entries into a `jmp` into its own .text section and
disperses the real instruction stream across scattered basic blocks linked by
unconditional jumps, with call/ret replaced by `lea rsp`/`mov [rsp]`/`jmp [rsp]`
stack gadgets. Raw `objdump` of such a function shows a `jmp` into the blob
followed by ~40 bytes of never-executed garbage.

This tool makes them readable by:
  * resolving the entry thunk (scripts/arxan-thunks.tsv) to where real code resumes; that table
    was measured on the 1.16.2 image, so it is used only when the image read is that build,
  * taking the game and Arxan code ranges from the image's own section table,
  * following unconditional dispersion `jmp`s transparently (so the stream is linear),
  * skipping the dead bytes after each jump,
  * recognizing the Arxan ret-gadget (`jmp qword [rsp-8]`) as the function's `ret`,
  * flagging real `call` targets (the actual game logic) and `[arxan]` scaffolding.

It is a READABILITY aid for static RE, not a proven re-lift: conditional branches
are shown in place (only the fall-through is followed), so treat multi-branch
output as a trace of one path plus annotated branch targets.

Usage
  scripts/deobf-read.py 0x140416830            # linearize a function
  scripts/deobf-read.py --raw 0x140416830      # raw disasm of the thunk region (for contrast)
  scripts/deobf-read.py --budget 200 0x...     # follow more instructions
  ER_DEOBF=eldenring-deobf.bin scripts/deobf-read.py 0x140110820   # a 1.16.2 VA, thunk table on

The image is `eldenring-deobf-1.17.2.bin`, the installed build (2.7.2.0), so a VA is a 2.7.2.0
VA unless `ER_DEOBF` names another build's image.

capstone is auto-provisioned via `uv run --with capstone` if not importable.
"""
import argparse, os, struct, sys

try:
    import capstone  # noqa
except ImportError:
    if os.environ.get("_DR_BOOT") != "1":
        os.environ["_DR_BOOT"] = "1"
        os.execvp("uv", ["uv", "run", "--with", "capstone", "python3",
                         os.path.abspath(__file__)] + sys.argv[1:])
    sys.exit("capstone unavailable and uv bootstrap failed")

from capstone import Cs, CS_ARCH_X86, CS_MODE_64
BASE = 0x140000000
IMAGE_NAME = "eldenring-deobf-1.17.2.bin"
#: PE TimeDateStamp of the 1.16.2 image `arxan-thunks.tsv` was measured on.
THUNKS_BUILD_STAMP = 0x69E21187
#: Game code and Arxan code ranges, taken from the image by `set_ranges`.
ARX = []
GAME = (0, 0)


def find_deobf():
    here = os.path.dirname(os.path.abspath(__file__))
    # `ER_DEOBF` first, then the installed build's image at the repo root and in each directory
    # above it: the image is gitignored, so a linked worktree under `.worktrees/` has no copy and
    # finds the main checkout's.
    candidates = [os.environ.get("ER_DEOBF")]
    d = os.path.dirname(here)
    while True:
        candidates.append(os.path.join(d, IMAGE_NAME))
        if os.path.dirname(d) == d:
            break
        d = os.path.dirname(d)
    for p in candidates:
        if p and os.path.exists(p):
            return p
    sys.exit(f"{IMAGE_NAME} not found (set ER_DEOBF=/path)")


def pe_stamp(img):
    return struct.unpack_from("<I", img, struct.unpack_from("<I", img, 0x3C)[0] + 8)[0]


def set_ranges(img):
    """Game code is the first `.text` up to `.interpr`; Arxan's is `.interpr` and the second `.text`."""
    global ARX, GAME
    pe = struct.unpack_from("<I", img, 0x3C)[0]
    count = struct.unpack_from("<H", img, pe + 6)[0]
    opt = struct.unpack_from("<H", img, pe + 20)[0]
    sections = []
    for i in range(count):
        o = pe + 24 + opt + 40 * i
        vsize, va = struct.unpack_from("<II", img, o + 8)
        sections.append((img[o:o + 8].rstrip(b"\0"), BASE + va, BASE + va + vsize))
    texts = [s for s in sections if s[0] == b".text"]
    interp = next(s for s in sections if s[0] == b".interpr")
    GAME = (texts[0][1], interp[1])
    ARX = [(interp[1], interp[2]), (texts[1][1], texts[1][2])]


def in_arx(v):
    return any(lo <= v < hi for lo, hi in ARX)


def load_thunks(img):
    here = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(here, "arxan-thunks.tsv")
    m = {}
    if pe_stamp(img) != THUNKS_BUILD_STAMP:
        return m
    if os.path.exists(p):
        for line in open(p).read().splitlines()[1:]:
            f = line.split("\t")
            if len(f) >= 5:
                m[int(f[0], 16)] = (int(f[2], 16), f[3], int(f[4], 16))
    return m


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("va", help="function VA (hex)")
    ap.add_argument("--raw", action="store_true", help="raw disasm of the thunk region instead of linearizing")
    ap.add_argument("--budget", type=int, default=120, help="max instructions to follow (default 120)")
    args = ap.parse_args()
    va = int(args.va, 16)
    img = open(find_deobf(), "rb").read()
    set_ranges(img)
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    thunks = load_thunks(img)

    def one(a):
        for ins in md.disasm(img[a - BASE:a - BASE + 16], a):
            return ins
        return None

    if args.raw:
        print(f"; raw disassembly at {hex(va)} (Arxan thunk region -- note dead bytes after the jmp):")
        a = va
        for ins in md.disasm(img[va - BASE:va - BASE + 64], va):
            print(f"  {ins.address:x}: {ins.mnemonic} {ins.op_str}")
        return

    start = va
    if va in thunks:
        tgt, kind, res = thunks[va]
        print(f"; {hex(va)} is an ARXAN THUNK  [{kind}]  entry-> {hex(tgt)}  real code @ {hex(res)}")
        start = res if kind == "stub-return" else tgt

    print(f"; linearized from {hex(start)} (following Arxan dispersion jumps):")
    a = start
    seen = set()
    n = 0
    calls = []
    while n < args.budget:
        if a in seen:
            print(f"  ; <- loops back to {hex(a)}")
            break
        seen.add(a)
        ins = one(a)
        if ins is None:
            print(f"  {a:x}: <undecodable / dead bytes>")
            break
        m, o = ins.mnemonic, ins.op_str
        if m == "jmp" and "[" in o and "rsp" in o:
            print(f"  {a:x}: ret            ; Arxan ret-gadget ({m} {o})")
            break
        if m in ("ret", "retf"):
            print(f"  {a:x}: ret")
            break
        if m == "jmp" and o.startswith("0x"):
            t = int(o, 16)
            if in_arx(t) or GAME[0] <= t < GAME[1]:
                a = t          # dispersion link: follow silently
                n += 1
                continue
        tag = "  ; [arxan]" if in_arx(a) else ""
        if m == "call":
            tag = "   <== CALL (real game logic)" + ("" if not in_arx(a) else "  [from arxan blk]")
            if o.startswith("0x"):
                calls.append(int(o, 16))
        print(f"  {a:x}: {m} {o}{tag}")
        a += ins.size
        n += 1
    if n >= args.budget:
        print(f"  ; ...budget ({args.budget}) exhausted at {hex(a)}")
    if calls:
        print(f"; real call targets: {', '.join(hex(c) for c in dict.fromkeys(calls))}")


if __name__ == "__main__":
    main()
