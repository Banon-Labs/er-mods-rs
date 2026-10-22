#!/usr/bin/env python3
"""Carry an ELDEN RING game address from 1.17.1 (2.7.1.0) to the 2026-10-20 build (2.7.2.0).

Why this is a segment table and not one constant
------------------------------------------------
1.17.0 to 1.17.1 grew one function and slid everything above it by `+0x70`, so
`scripts/map-rvas-1170-to-1171.py` is a boundary and a constant. 2.7.2.0 is not
that. Measured from the `.pdata` of both de-Arxan'd images (bd er-effects-rs-gpkk):
the code moved in dozens of runs with different deltas, the section table moved
(`.rdata` +0x2000, `.data` +0x3000), and the data inside `.rdata`/`.data` drifts
piecewise as well. So this script derives two segment lists and writes them to
`docs/recon/carry-1171-to-1172.tsv`, which `crates/er-game-base/build.rs` compiles
into the runtime carry:

  * text: `.pdata` entries of both images, paired in order by length and by a
    delta that holds across neighbours. Consecutive pairs sharing a delta form a
    segment, and the gap between them (leaf code `.pdata` does not describe) moves
    with them because the two entries around it did. An address in no segment is
    in or beside a function that changed, and is refused.
  * data: every instruction of carried code is decoded -- each `.pdata` function,
    and the leaf code `.pdata` does not describe, found from call and jump targets
    and from code pointers in data. A rip-relative operand whose instruction carries
    to one of the same mnemonic and operand shape is a reader, and the difference of
    its two targets is the data delta at that target. A reader votes when the code
    after it matches too (see `collect_readers`). A target takes the delta a strict
    majority of its voting readers agree on, one reader included. Consecutive
    targets sharing a delta form a segment; a segment ends at the next target that
    disagrees and never spans one.

Why decoding, and why one reader is enough (2026-10-21): this used to scan every
byte of `.text` for a `modrm` of mod 00 rm 101. Most of those are not instructions,
so a target needed two agreeing votes and a segment two agreeing targets, and every
target with a single reader was dropped. Its neighbours then carried it with their
delta. `0x3b3885c` (the 10.0 the join tick reads at `0x6ff691`) went to `+0x3010`
for `+0x300c`, and `0x3d6deb4` (read at `0x6ff688`) to `+0x3024` for `+0x301c`: the
data inside `.data` is reordered as well as shifted, so a neighbour's delta says
nothing about a target of its own. 66,406 of the 94,659 read targets have exactly
one reader.

The 2.7.2.0 side of a reader is decoded no further than the end of its function,
as `scripts/function_extent.py` resolves it; a reader whose 2.7.2.0 code has no
extent it can find does not vote.

Refusing is the point. A carried address that lands in changed code is a
mid-function detour or a wrong global; a refused one is a dead feature with a log
line naming it.

Usage
-----
    python3 scripts/map-rvas-1171-to-1172.py --write        # regenerate the tsv
    python3 scripts/map-rvas-1171-to-1172.py 0x140b00070    # carry addresses (reads the tsv)
    python3 scripts/map-rvas-1171-to-1172.py --selftest
    python3 scripts/map-rvas-1171-to-1172.py --measure [TSV ...]  # every reader against a table

`--write`, `--selftest` and `--measure` decode with capstone and re-run themselves
under `uv run --with capstone --with numpy` when it is not importable.

Both images are read-only, gitignored inputs; see `AGENTS.md`.
"""

from __future__ import annotations

import argparse
import bisect
import os
import re
import struct
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import function_extent  # noqa: E402 - repo-local; the sys.path line above is what makes it work

IMAGE_BASE = 0x140000000
REPO_ROOT = Path(__file__).resolve().parent.parent
TSV = REPO_ROOT / "docs" / "recon" / "carry-1171-to-1172.tsv"

# A resync after a changed function looks this far ahead in each list, and needs
# this many consecutive entries agreeing on lengths and delta to accept.
RESYNC_WINDOW = 256
RESYNC_RUN = 8

# The provisional data table `mispaired` checks pairs against: a target needs this many readers
# agreeing, at this share, and a segment this many such targets. It is coarse on purpose -- it
# only has to outvote a pair that is two different functions -- and it is never written out.
MIN_DATA_VOTES = 2
MIN_DATA_SHARE = 0.75

# A segment's last target covers this many bytes, or its own access size when larger, unless the
# next target comes first. It is what a `lea` of a pointer-sized global needs.
MIN_DATA_WIDTH = 8

# The two addresses the byte-scan vote carried wrong, each read by one instruction of the join
# tick: (1.17.1 rva, 2.7.2.0 rva its reader reads). The selftest pins both.
PINNED_DATA = ((0x3B3885C, 0x3B3B868), (0x3D6DEB4, 0x3D70ED0))

# A rip-relative memory operand as capstone prints it: `[rip + 0x1234]`, `[rip - 0x10]`, `[rip]`.
RIP_OPERAND = re.compile(r"\[rip(?: ([+-]) (0x[0-9a-f]+|\d+))?\]")
# Every number in an operand string. Masking them leaves the operand shape: registers and sizes.
NUMBER = re.compile(r"\b(0x[0-9a-f]+|\d+)\b")
ACCESS = re.compile(r"\b(byte|word|dword|qword|tbyte|xmmword|ymmword|zmmword) ptr \[rip")
ACCESS_SIZE = {"byte": 1, "word": 2, "dword": 4, "qword": 8, "tbyte": 10,
               "xmmword": 16, "ymmword": 32, "zmmword": 64}
# A leaf's straight-line decode stops after one of these. capstone spells `f3 c3`, the return the
# hand-written runtime routines low in `.text` end with, `repz ret`.
TERMINATORS = {"ret", "repz ret", "rep ret", "retf", "jmp", "int3", "ud2", "hlt"}
# A reader votes only when this many instructions from it on, or up to a terminator, match.
WINDOW = 8


def _ensure_decoder() -> None:
    """Re-run this script under uv when capstone or numpy is missing."""
    try:
        import capstone  # noqa: F401
        import numpy  # noqa: F401
    except ImportError:
        if os.environ.get("_MAP1172_UNDER_UV"):
            raise SystemExit("capstone or numpy is still missing under uv")
        os.environ["_MAP1172_UNDER_UV"] = "1"
        os.execvp("uv", ["uv", "run", "--with", "capstone", "--with", "numpy", "python3", *sys.argv])


def _resolve_image(env_var: str, filename: str) -> Path:
    """Env override, then this checkout, then the primary worktree (images are gitignored)."""
    override = os.environ.get(env_var)
    if override:
        return Path(override)
    local = REPO_ROOT / filename
    if local.exists():
        return local
    common = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "--git-common-dir"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    if common.returncode == 0:
        candidate = (REPO_ROOT / common.stdout.strip()).resolve().parent / filename
        if candidate.exists():
            return candidate
    return local


OLD_IMAGE = _resolve_image("ER_DEOBF_1171", "eldenring-deobf-1.17.1.bin")
NEW_IMAGE = _resolve_image("ER_DEOBF_1172", "eldenring-deobf-1.17.2.bin")


class Image:
    """A flat de-Arxan'd image: file offset == rva."""

    def __init__(self, path: Path):
        if not path.is_file():
            raise SystemExit(f"{path} is absent; regenerate it with scripts/dearxan-deobfuscate.rs")
        self.data = path.read_bytes()
        pe = struct.unpack_from("<I", self.data, 0x3C)[0]
        nsec = struct.unpack_from("<H", self.data, pe + 6)[0]
        opt = pe + 0x18
        opt_size = struct.unpack_from("<H", self.data, pe + 0x14)[0]
        self.pdata_rva, self.pdata_size = struct.unpack_from("<II", self.data, opt + 0x70 + 3 * 8)
        self.sections = []
        for i in range(nsec):
            at = opt + opt_size + i * 40
            name = self.data[at:at + 8].rstrip(b"\0").decode("ascii", "replace")
            vsize, vaddr = struct.unpack_from("<II", self.data, at + 8)
            self.sections.append((name, vaddr, vsize))
        text = self.sections[0]
        if text[0] != ".text":
            raise SystemExit(f"first section is {text[0]!r}, expected .text")
        self.text_lo, self.text_hi = text[1], text[1] + text[2]

    def section(self, name: str) -> tuple[int, int]:
        for n, lo, size in self.sections:
            if n == name:
                return lo, lo + size
        raise KeyError(name)

    def pdata(self) -> list[tuple[int, int]]:
        out = set()
        for off in range(0, self.pdata_size - 11, 12):
            begin, end, _ = struct.unpack_from("<III", self.data, self.pdata_rva + off)
            if self.text_lo <= begin < end <= self.text_hi:
                out.add((begin, end))
        return sorted(out)


def pair_text(old: list[tuple[int, int]], new: list[tuple[int, int]]) -> list[tuple[int, int, int, int]]:
    """[(old_begin, old_end, delta, old_index)] for every `.pdata` entry carried unchanged."""
    ol = [e - b for b, e in old]
    nl = [e - b for b, e in new]

    def agree(i: int, j: int, run: int) -> bool:
        if i + run > len(old) or j + run > len(new):
            run = min(len(old) - i, len(new) - j)
            if run <= 0:
                return False
        delta = new[j][0] - old[i][0]
        return all(ol[i + t] == nl[j + t] and new[j + t][0] - old[i + t][0] == delta for t in range(run))

    pairs, i, j, synced = [], 0, 0, None
    while i < len(old) and j < len(new):
        delta = new[j][0] - old[i][0]
        if ol[i] == nl[j] and (delta == synced or agree(i, j, RESYNC_RUN)):
            pairs.append((old[i][0], old[i][1], delta, i))
            synced = delta
            i += 1
            j += 1
            continue
        synced = None
        found = None
        for step in range(1, RESYNC_WINDOW):
            for a in range(step + 1):
                if agree(i + a, j + step - a, RESYNC_RUN):
                    found = (a, step - a)
                    break
            if found:
                break
        if found is None:
            i += 1
            continue
        i += found[0]
        j += found[1]
    return pairs


def text_segments(pairs: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int]]:
    """Pairs adjacent in the old `.pdata` and sharing a delta merge, the gap between them included.

    Adjacency matters: an unpaired entry between two pairs is a function that changed, and
    merging across it would carry an address inside it.
    """
    segs: list[list[int]] = []
    for b, e, d, i in pairs:
        if segs and segs[-1][2] == d and segs[-1][3] == i - 1:
            segs[-1][1] = e
            segs[-1][3] = i
        else:
            segs.append([b, e, d, i])
    return [(lo, hi, d) for lo, hi, d, _ in segs]


def widen_over_identical_gaps(segs, old: Image, new: Image, entries) -> list[tuple[int, int, int]]:
    """Extend each segment over the leaf code beside it when that code is byte-identical.

    `.pdata` describes no leaf function, so a leaf in the gap next to a refused entry is refused
    with it -- `0x252130`, 0x2c bytes and identical in both builds, was. Bytes equal at the
    segment's own delta are the strongest evidence there is, so such a gap joins its segment.
    A gap that differs at all stays refused.
    """
    ends = sorted(e for _, e in entries)
    starts = sorted(b for b, _ in entries)
    out = [list(s) for s in segs]

    def same(lo: int, hi: int, d: int) -> bool:
        # Alignment filler before a 16-byte function start is not code and differs between
        # builds (`0x25212a`..`0x25212f` does), so up to 15 bytes at either edge may differ.
        a, b = old.data[lo:hi], new.data[lo + d:hi + d]
        i, j = 0, len(a)
        while i < min(15, j) and a[i] != b[i]:
            i += 1
        while j > i and len(a) - j < 15 and a[j - 1] != b[j - 1]:
            j -= 1
        # An all-different gap passes only when it is too short to be anything but filler.
        return a[i:j] == b[i:j] and (j > i or len(a) < 16)
    for k, seg in enumerate(out):
        lo, hi, d = seg
        at = bisect.bisect_right(ends, lo) - 1
        prev_end = ends[at] if at >= 0 else lo
        floor = out[k - 1][1] if k else 0
        if floor <= prev_end < lo and same(prev_end, lo, d):
            seg[0] = prev_end
        at = bisect.bisect_left(starts, hi)
        next_start = starts[at] if at < len(starts) else hi
        ceiling = out[k + 1][0] if k + 1 < len(out) else next_start
        if hi < next_start <= ceiling and same(hi, next_start, d):
            seg[1] = next_start
    return [tuple(s) for s in out]


class Reader(NamedTuple):
    """One rip-relative operand of 1.17.1 code that reads `.rdata` or `.data`."""

    insn: int  # rva of the instruction
    fn: int | None  # begin of the `.pdata` entry it sits in; None in leaf code
    delta: int  # the text carry of the instruction
    target: int  # rva its operand reads
    new_target: int | None  # rva the carried instruction reads; None when the shapes differ
    width: int  # bytes the operand accesses; 0 for a `lea`
    doubt: str  # empty when the code from it to `WINDOW` instructions on matches; else what differs


def segment_carry(rva: int, segs: list[tuple[int, int, int]], starts: list[int]) -> int | None:
    at = bisect.bisect_right(starts, rva) - 1
    if at >= 0 and segs[at][0] <= rva < segs[at][1]:
        return rva + segs[at][2]
    return None


def rip_target(address: int, size: int, op: str) -> int | None:
    m = RIP_OPERAND.search(op)
    if m is None:
        return None
    disp = int(m.group(2), 0) if m.group(2) else 0
    return address + size + (-disp if m.group(1) == "-" else disp)


def operand_shape(mnemonic: str, op: str) -> str:
    return mnemonic + " " + NUMBER.sub("N", RIP_OPERAND.sub("[rip]", op))


def branch_target(mnemonic: str, op: str) -> int | None:
    if (mnemonic == "call" or mnemonic.startswith("j")) and op.startswith("0x"):
        return int(op, 16)
    return None


def collect_readers(old: Image, new: Image, entries: list[tuple[int, int]],
                    segs: list[tuple[int, int, int]]) -> list[Reader]:
    """Every rip-relative data operand of 1.17.1 code that `segs` carries, decoded in both images.

    Code is every `.pdata` function, plus leaf code: `.pdata` describes no leaf, so a leaf is
    entered from a direct call or jump of decoded code, or from a 16-byte-aligned code pointer in
    `.rdata`/`.data`, and decoded in straight lines up to a terminator, following its own branches.
    Decoding starts only at those entries, never at an arbitrary byte, so nothing between functions
    -- padding, a jump table -- is read as an instruction.

    A reader's own instruction having the same shape in 2.7.2.0 is not enough to trust where it
    points: `0x5f7da4` is `lea rcx, [obj]; jmp dtor` in both builds, but the 2.7.2.0 one is another
    object's destructor thunk, and its `jmp` goes to a function the text carry puts elsewhere. So
    `doubt` records the first thing that differs in the straight line from the reader on, up to
    `WINDOW` instructions or a terminator: an instruction of another shape, a direct branch the
    text carry takes somewhere other than where the 2.7.2.0 branch goes, a `jmp` into code the
    carry refuses (nothing after it can be checked), or the Arxan return `jmp qword ptr [rsp - 8]`
    -- `0xfdf4d0` is `lea rax, [slot]` before one, and its slot moved by `+0x10040` among `.data`
    neighbours at `+0x3020`. A reader whose target changes section is doubted too: a `.data`
    global does not become `.rdata`, and the three that do here are all other objects.

    The `.pdata` functions are decoded in forked workers, in fixed slices and joined in order, so
    the result does not depend on how many there are.
    """
    import numpy as np

    global _DECODER
    dec = _Decoder(old, new, entries, segs)
    step = 4096
    spans = [(i, min(i + step, len(entries))) for i in range(0, len(entries), step)]
    # `read` bounds every 2.7.2.0 decode with that image's function extents. Parse its section
    # table and `.pdata` once, here, so the forked workers inherit them instead of each parsing.
    function_extent.executable_at(new.data, IMAGE_BASE + new.text_lo)
    function_extent.declared_functions(new.data)
    _DECODER = dec
    try:
        import multiprocessing

        with multiprocessing.get_context("fork").Pool(min(16, os.cpu_count() or 1)) as pool:
            parts = pool.map(_decode_functions, spans)
    except (OSError, ValueError):
        parts = [dec.functions(lo, hi) for lo, hi in spans]
    finally:
        _DECODER = None
    readers = [Reader(*r) for part, _ in parts for r in part]
    leaves = set().union(*(found for _, found in parts))
    for name in (".rdata", ".data"):
        lo, hi = old.section(name)
        q = np.frombuffer(old.data, dtype="<u8", count=(hi - lo) // 8, offset=lo)
        q = q[(q >= IMAGE_BASE + old.text_lo) & (q < IMAGE_BASE + old.text_hi) & (q % 16 == 0)]
        leaves.update(t for t in (q - IMAGE_BASE).tolist() if not dec.in_pdata(t))
    return readers + dec.leaves(leaves)


_DECODER: "_Decoder | None" = None


def _decode_functions(span: tuple[int, int]):
    return _DECODER.functions(*span)


class _Decoder:
    """The state `collect_readers` decodes with; see there for what it does."""

    def __init__(self, old: Image, new: Image, entries: list[tuple[int, int]],
                 segs: list[tuple[int, int, int]]):
        self.old, self.new, self.entries, self.segs = old, new, entries, segs
        self.data_lo = min(lo for n, lo, _ in old.sections if n in (".rdata", ".data"))
        self.data_hi = max(lo + s for n, lo, s in old.sections if n in (".rdata", ".data"))
        self.seg_starts = [s[0] for s in segs]
        self.starts = [b for b, _ in entries]
        self.ends = [e for _, e in entries]
        self._md = None
        self._md_pid = None

    @property
    def md(self):
        # One capstone handle per process: a handle is not shared across a fork.
        if self._md_pid != os.getpid():
            import capstone

            self._md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
            self._md_pid = os.getpid()
        return self._md

    def in_pdata(self, rva: int) -> bool:
        at = bisect.bisect_right(self.starts, rva) - 1
        return at >= 0 and rva < self.ends[at]

    def is_code_target(self, rva: int) -> bool:
        return self.old.text_lo <= rva < self.old.text_hi and not self.in_pdata(rva)

    @staticmethod
    def same_shape(mn: str, op: str, nmn: str, nop: str) -> bool:
        return mn == nmn and (op == nop or operand_shape(mn, op) == operand_shape(nmn, nop))

    def doubt(self, line: list, news: list) -> str:
        for k, (a, _, mn, op) in enumerate(line):
            if k >= len(news) or not self.same_shape(mn, op, news[k][2], news[k][3]):
                return f"{a:#x} `{mn} {op}` has another shape in 2.7.2.0"
            if mn == "jmp" and "[rsp" in op:
                return f"{a:#x} `{mn} {op}` ends an Arxan stub, whose slots each build re-randomises"
            t = branch_target(mn, op)
            if t is None:
                continue
            want = segment_carry(t, self.segs, self.seg_starts)
            if want is None and mn == "jmp":
                return f"{a:#x} `jmp` continues at {t:#x}, which the text carry refuses"
            if want is not None and want != int(news[k][3], 16):
                return f"{a:#x} `{mn}` goes to {int(news[k][3], 16):#x} in 2.7.2.0, the carry says {want:#x}"
        return ""

    @staticmethod
    def section_name(img: Image, rva: int) -> str | None:
        return next((n for n, lo, size in img.sections if lo <= rva < lo + size), None)

    def read(self, code: list, k: int, fn: int | None, out: list[Reader]) -> None:
        address, size, mnemonic, op = code[k]
        target = rip_target(address, size, op)
        if target is None or not self.data_lo <= target < self.data_hi:
            return
        moved = segment_carry(address, self.segs, self.seg_starts)
        if moved is None:
            return
        line = []
        for ins in code[k:k + WINDOW]:
            line.append(ins)
            if ins[2] in TERMINATORS:
                break
        access = ACCESS.search(op)
        width = ACCESS_SIZE[access.group(1)] if access else 0
        # The 1.17.1 line was cut from its function's extent, so the 2.7.2.0 one is too: past the
        # end of the function `moved` sits in, the image holds padding and the deobfuscator's
        # leftover bytes, which decode into instructions nobody assembled and could pass for the
        # rest of the line. The byte count only caps the slice. No extent is a refusal, and the
        # reader does not vote.
        stop = function_extent.body_slice_end(self.new.data, IMAGE_BASE + moved, cap=15 * len(line))
        if stop is None:
            out.append(Reader(address, fn, moved - address, target, None, width,
                              f"{moved:#x} has no function extent in 2.7.2.0"))
            return
        news = list(self.md.disasm_lite(self.new.data[moved:stop], moved, len(line)))
        new_target = None
        if news and self.same_shape(mnemonic, op, news[0][2], news[0][3]):
            new_target = rip_target(news[0][0], news[0][1], news[0][3])
        why = self.doubt(line, news)
        if not why and new_target is not None:
            was, now = self.section_name(self.old, target), self.section_name(self.new, new_target)
            if was != now:
                why = f"reads {now} in 2.7.2.0 and {was} in 1.17.1"
        out.append(Reader(address, fn, moved - address, target, new_target, width, why))

    def functions(self, lo: int, hi: int) -> tuple[list[Reader], set[int]]:
        """Readers in `.pdata` entries `lo..hi`, and the leaf entries their branches name."""
        out: list[Reader] = []
        leaves: set[int] = set()
        for b, e in self.entries[lo:hi]:
            code = list(self.md.disasm_lite(self.old.data[b:e], b))
            for k, (a, s, mn, op) in enumerate(code):
                if "rip" in op:
                    self.read(code, k, b, out)
                elif (mn[0] == "j" or mn == "call") and op[:2] == "0x":
                    t = int(op, 16)
                    if self.is_code_target(t):
                        leaves.add(t)
        # Plain tuples cross the process boundary: a module loaded through importlib under another
        # name cannot pickle its own `Reader`.
        return [tuple(r) for r in out], leaves

    def leaves(self, entries: set[int]) -> list[Reader]:
        """Readers in leaf code, decoded in straight lines from each entry and its branches."""
        out: list[Reader] = []
        seen: set[int] = set()
        work = sorted(entries, reverse=True)
        while work:
            entry = work.pop()
            if entry in seen:
                continue
            at = bisect.bisect_right(self.starts, entry)
            stop = self.starts[at] if at < len(self.starts) else self.old.text_hi
            code = []
            for ins in self.md.disasm_lite(self.old.data[entry:stop], entry):
                if ins[0] in seen:
                    break
                seen.add(ins[0])
                code.append(ins)
                t = branch_target(ins[2], ins[3])
                if t is not None and self.is_code_target(t) and t not in seen:
                    work.append(t)
                if ins[2] in TERMINATORS:
                    break
            for k, ins in enumerate(code):
                if "rip" in ins[3]:
                    self.read(code, k, None, out)
        return out


def byte_scan_anchors(old: Image, new: Image, segs: list[tuple[int, int, int]]) -> dict[int, int]:
    """{old data rva: delta}, voted over every byte of carried code that could be a rip operand.

    This was the data table until 2026-10-21 and now only feeds `mispaired`, so the text rows stay
    what they were. Most of the positions it reads are not instructions, which is why a target
    needs two agreeing votes; it is also what lets `mispaired` refuse a pair whose code changed
    without the change touching a decoded operand.
    """
    import numpy as np

    od = np.frombuffer(old.data, dtype=np.uint8)
    nd = np.frombuffer(new.data, dtype=np.uint8)
    data_lo = min(lo for n, lo, _ in old.sections if n in (".rdata", ".data"))
    data_hi = max(lo + s for n, lo, s in old.sections if n in (".rdata", ".data"))
    votes: dict[int, Counter] = defaultdict(Counter)
    for lo, hi, d in segs:
        if hi - lo < 8:
            continue
        p = np.arange(lo + 1, hi - 4, dtype=np.int64)
        modrm = od[p - 1]
        p = p[(modrm & 0xC7) == 0x05]
        if p.size == 0:
            continue
        do = _i32(od, p)
        dn = _i32(nd, p + d)
        target = p + 4 + do
        keep = (target >= data_lo) & (target < data_hi)
        for t, dd in zip(target[keep].tolist(), (d + dn[keep] - do[keep]).tolist()):
            votes[t][dd] += 1
    anchors = {}
    for t, c in votes.items():
        delta, n = c.most_common(1)[0]
        if n >= MIN_DATA_VOTES and n / sum(c.values()) >= MIN_DATA_SHARE:
            anchors[t] = delta
    return anchors


def _i32(buf, at):
    import numpy as np

    b = buf[at[:, None] + np.arange(4)].astype(np.uint32)
    v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16) | (b[:, 3] << 24)
    return v.astype(np.int64) - ((v >= 0x80000000).astype(np.int64) << 32)


def coarse_data_segments(anchors: dict[int, int], old: Image) -> list[tuple[int, int, int]]:
    """The byte-scan anchors as segments, for `mispaired` only; never written out."""
    segs: list[list[int]] = []
    bounds = [(lo, lo + s) for n, lo, s in old.sections if n in (".rdata", ".data")]

    def section_of(t: int):
        return next((b for b in bounds if b[0] <= t < b[1]), None)

    for t in sorted(anchors):
        d = anchors[t]
        if segs and segs[-1][2] == d and section_of(segs[-1][0]) == section_of(t):
            segs[-1][1] = t + 8
            segs[-1][3] += 1
        else:
            segs.append([t, t + 8, d, 1])
    # A lone anchor between two disagreeing neighbours is noise more often than a real
    # one-object move; keep only segments backed by at least two anchors.
    kept = [(lo, hi, d) for lo, hi, d, n in segs if n >= 2]
    # Dropping one can leave its two neighbours sharing a delta, or ending past the next start
    # (a segment's last anchor is given 8 bytes). Merge the first, clip the second.
    out: list[list[int]] = []
    for lo, hi, d in kept:
        if out and out[-1][2] == d and section_of(out[-1][0]) == section_of(lo):
            out[-1][1] = max(out[-1][1], hi)
            continue
        if out and out[-1][1] > lo:
            out[-1][1] = lo
        out.append([lo, hi, d])
    return [tuple(s) for s in out]


class Verdict(NamedTuple):
    """What the trusted readers of one target say."""

    readers: int  # readers with no `doubt` whose instruction has the same shape in 2.7.2.0
    delta: int | None  # the delta a strict majority of them read at; None when they split evenly


def data_segments(readers: list[Reader], old: Image) -> tuple[list[tuple[int, int, int]], dict[int, Verdict]]:
    """The written data table: each target where a strict majority of its trusted readers say.

    A target with one reader is as good as one with a thousand -- the reader is decoded, in code
    the text carry pairs, its instruction has the same shape on both sides and so does the code
    after it. Only readers with no `doubt` vote: one whose following code changed is evidence of
    nothing about the object, and as the sole reader of a target it would carry the target wherever
    the changed code now points (`0x439e14` would take the vtable at `0x2be2fa0` to `-0x1894a8`,
    among neighbours at `+0x22b0`, and refuse `csdebug_cam_pad_vmt` beside it).

    Consecutive targets sharing a delta merge, the bytes between them included; the last one
    covers `MIN_DATA_WIDTH` bytes or its access size, cut at the next target. Nothing is carried
    across a target of another delta, and a target whose readers split evenly is refused.
    """
    votes: dict[int, Counter] = defaultdict(Counter)
    width: dict[int, int] = defaultdict(int)
    for r in readers:
        if r.new_target is not None and not r.doubt:
            votes[r.target][r.new_target - r.target] += 1
            width[r.target] = max(width[r.target], r.width)
    bounds = [(lo, lo + s) for n, lo, s in old.sections if n in (".rdata", ".data")]

    def section_of(t: int):
        return next((b for b in bounds if b[0] <= t < b[1]), None)

    targets = sorted(votes)
    verdicts: dict[int, Verdict] = {}
    for t in targets:
        delta, n = votes[t].most_common(1)[0]
        total = sum(votes[t].values())
        verdicts[t] = Verdict(total, delta if 2 * n > total else None)

    segs: list[list[int]] = []
    for k, t in enumerate(targets):
        delta = verdicts[t].delta
        if delta is None:
            continue
        section = section_of(t)
        nxt = targets[k + 1] if k + 1 < len(targets) else section[1]
        hi = min(t + max(MIN_DATA_WIDTH, width[t]), nxt, section[1])
        if segs and segs[-1][2] == delta and segs[-1][3] == k - 1 and section_of(segs[-1][0]) == section:
            segs[-1][1] = hi
            segs[-1][3] = k
        else:
            segs.append([t, hi, delta, k])
    return [(lo, hi, d) for lo, hi, d, _ in segs], verdicts


def mispaired(old: Image, new: Image, pairs: list, data: list) -> set[int]:
    """Old entry starts whose pair is two different functions of the same length.

    Length and neighbour delta cannot tell two same-length stubs apart: `0x262890` is a "Failed to
    allocate TLS Object" stub in 1.17.1 and an "access to an out of range" stub in 2.7.2.0, at the
    same address. What does tell them apart is where they point. Every rip-relative operand of a
    real pair, carried through the data segments, lands where the new image's operand points; a
    mispair's operands point somewhere else. A pair is refused when at least two of its operands
    disagree and they outnumber the ones that agree.

    It reads every byte that could be an operand, against the byte-scan table, as it did before
    the data table was decoded. Feeding it decoded readers instead carried 657 more pairs, 553 of
    them functions whose instructions differ between the builds.
    """
    import numpy as np

    od = np.frombuffer(old.data, dtype=np.uint8)
    nd = np.frombuffer(new.data, dtype=np.uint8)
    starts = np.array([b for b, *_ in pairs], dtype=np.int64)
    ends = np.array([e for _, e, *_ in pairs], dtype=np.int64)
    dlo = np.array([s[0] for s in data], dtype=np.int64)
    dhi = np.array([s[1] for s in data], dtype=np.int64)
    dd = np.array([s[2] for s in data], dtype=np.int64)

    bad = np.zeros(len(pairs), dtype=np.int64)
    good = np.zeros(len(pairs), dtype=np.int64)
    # One contiguous span per run of adjacent pairs is enough; per-function bookkeeping is by index.
    for lo, hi, d in text_segments(pairs):
        p = np.arange(lo + 1, hi - 4, dtype=np.int64)
        p = p[(od[p - 1] & 0xC7) == 0x05]
        if p.size == 0:
            continue
        old_t = p + 4 + _i32(od, p)
        new_t = p + d + 4 + _i32(nd, p + d)
        at = np.searchsorted(dlo, old_t, side="right") - 1
        inside = (at >= 0) & (old_t < dhi[np.clip(at, 0, None)])
        carried = old_t + dd[np.clip(at, 0, None)]
        fn = np.searchsorted(starts, p, side="right") - 1
        inside &= (fn >= 0) & (p < ends[np.clip(fn, 0, None)])
        fn, agree = fn[inside], (carried == new_t)[inside]
        np.add.at(good, fn[agree], 1)
        np.add.at(bad, fn[~agree], 1)
    return {int(starts[i]) for i in np.nonzero((bad >= 2) & (bad > good))[0]}


def write_tsv(text: list, data: list, path: Path = TSV) -> None:
    lines = [
        "# Generated by scripts/map-rvas-1171-to-1172.py --write; do not edit by hand.",
        "# kind  old_lo  old_hi  delta  -- an rva in [old_lo, old_hi) of 1.17.1 is at rva+delta in 2.7.2.0;",
        "# an rva in no row is refused.",
    ]
    # The PE headers sit at rva 0 in every build; the map carries a row for the image base itself.
    lines.append("header\t0x0\t0x1000\t+0x0")
    for kind, segs in (("text", text), ("data", data)):
        for lo, hi, d in segs:
            lines.append(f"{kind}\t{lo:#x}\t{hi:#x}\t{d:+#x}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_tsv(path: Path = TSV) -> list[tuple[str, int, int, int]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        kind, lo, hi, d = line.split("\t")
        rows.append((kind, int(lo, 16), int(hi, 16), int(d, 16)))
    return rows


def carry(rva: int, rows) -> int | None:
    starts = [r[1] for r in rows]
    at = bisect.bisect_right(starts, rva) - 1
    if at >= 0 and rows[at][1] <= rva < rows[at][2]:
        return rva + rows[at][3]
    return None


def build() -> tuple[list, list, Image, Image, list, list[Reader], dict[int, Verdict]]:
    old, new = Image(OLD_IMAGE), Image(NEW_IMAGE)
    po, pn = old.pdata(), new.pdata()
    pairs = pair_text(po, pn)
    coarse = coarse_data_segments(byte_scan_anchors(old, new, text_segments(pairs)), old)
    # Drop the pairs whose operands say they are different functions; the adjacency rule in
    # `text_segments` then refuses them and the gap on either side.
    wrong = mispaired(old, new, pairs, coarse)
    pairs = [p for p in pairs if p[0] not in wrong]
    text = widen_over_identical_gaps(text_segments(pairs), old, new, po)
    # The data table is voted by decoded readers in the code the written text table carries.
    readers = collect_readers(old, new, po, text)
    data, verdicts = data_segments(readers, old)
    return text, data, old, new, pairs, readers, verdicts


def measure(rows, old: Image, new: Image) -> tuple[list[Reader], list[Reader]]:
    """(agreeing, disagreeing) readers of a table: every rip-relative data operand of every decoded
    1.17.1 instruction the table's text rows carry to one of the same mnemonic and operand shape,
    judged by whether the table carries its target to where the 2.7.2.0 instruction reads."""
    segs = sorted((lo, hi, d) for _, lo, hi, d in rows)
    starts = [s[0] for s in segs]
    text = [(lo, hi, d) for kind, lo, hi, d in sorted(rows, key=lambda r: r[1]) if kind != "data"]
    readers = [r for r in collect_readers(old, new, old.pdata(), text) if r.new_target is not None]
    agree, disagree = [], []
    for r in readers:
        (agree if segment_carry(r.target, segs, starts) == r.new_target else disagree).append(r)
    return agree, disagree


def selftest() -> int:
    text, data, old, new, pairs, readers, verdicts = build()
    rows = sorted([("text", *s) for s in text] + [("data", *s) for s in data], key=lambda r: r[1])
    failures = []

    def check(label, ok, detail=""):
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}{(' -- ' + detail) if detail else ''}")
        if not ok:
            failures.append(label)

    # The two the byte-scan vote got wrong: one reader each, inside runs of another delta.
    for rva, expected in PINNED_DATA:
        got = carry(rva, rows)
        check(f"{rva:#x} carries to {expected:#x}, where its reader reads",
              got == expected, "refused" if got is None else f"got {got:#x}")
    # The table's contract: every read target carries to where a strict majority of its trusted
    # readers read, and one they split on is refused.
    missed = sorted(t for t, v in verdicts.items()
                    if carry(t, rows) != (None if v.delta is None else t + v.delta))
    split = sum(1 for v in verdicts.values() if v.delta is None)
    lone = sum(1 for v in verdicts.values() if v.readers == 1)
    check(f"{len(verdicts) - len(missed)} of {len(verdicts)} read targets carry to their trusted "
          f"readers' majority ({lone} have one reader, {split} split and are refused)",
          len(verdicts) > 50000 and not missed, ", ".join(f"{t:#x}" for t in missed[:10]))
    doubted = sum(1 for r in readers if r.new_target is not None and r.doubt)
    print(f"        {doubted} same-shape readers do not vote: the code after them changed")
    # A `lea` whose 2.7.2.0 instruction names another vtable; its vtable stays with the neighbours.
    check("0x2be2fa0 (its only reader's code changed) carries with its neighbours, +0x22b0",
          carry(0x2BE2FA0, rows) == 0x2BE2FA0 + 0x22B0)
    # Arxan stubs re-randomise their slots per build: `0xfdf4d0` would carry this by +0x10040.
    check("0x3d6a2c4 (read only by an Arxan stub) carries with its neighbours, +0x3020",
          carry(0x3D6A2C4, rows) == 0x3D6A2C4 + 0x3020)

    po = old.pdata()
    check(f"{len(pairs)} of {len(po)} .pdata entries paired", len(pairs) > 0.99 * len(po))
    pn = set(new.pdata())
    check("every pair lands on a .pdata entry of the same length",
          all((b + d, e + d) in pn for b, e, d, _ in pairs))
    check(f"{len(text)} text segments, {len(data)} data segments", len(text) > 10 and len(data) > 10)
    # A same-length stub that changed content, measured by hand in the patch-notes diff. The other
    # example that diff named, 0x1f866f0, is an Arxan `jmp` stub on both sides: its bytes are
    # re-randomised per build and it has no operand to compare, so no image read can settle it.
    check("0x262890 (same length, different function) is refused", carry(0x262890, rows) is None)
    check("0x252130 (identical leaf beside a refused entry) carries", carry(0x252130, rows) == 0x252130)
    check("segments are sorted and disjoint",
          all(a[2] <= b[1] for a, b in zip(rows, rows[1:])) and all(r[1] < r[2] for r in rows))

    # The sibling fromsoftware-rs bundle, generated by binary-mapper from AOB patterns and RTTI on
    # each exe, knows nothing of `.pdata` or operands: every field it has for both builds must carry.
    sibling = REPO_ROOT.parent / "fromsoftware-rs" / "crates" / "eldenring" / "src" / "rva"
    if not sibling.is_dir():
        sibling = Path.home() / "projects" / "fromsoftware-rs" / "crates" / "eldenring" / "src" / "rva"
    old_b, new_b = sibling / "rva_ww_2710.rs", sibling / "rva_ww.rs"
    if old_b.is_file() and new_b.is_file():
        import re
        fields = lambda p: dict(re.findall(r"([a-z0-9_]+)\s*:\s*0x([0-9a-fA-F]+)", p.read_text()))
        fo, fn = fields(old_b), fields(new_b)
        both = [k for k in fo if k in fn]
        good = [k for k in both if carry(int(fo[k], 16), rows) == int(fn[k], 16)]
        check(f"{len(good)} of {len(both)} RVA-bundle fields carry to the 2.7.2.0 bundle's value",
              len(both) > 100 and len(good) == len(both),
              ", ".join(sorted(set(both) - set(good))))
    else:
        print(f"  skip  RVA-bundle cross-check: {old_b} or {new_b} absent")
    covered = sum(hi - lo for lo, hi, _ in text)
    check(f"text segments cover {covered / (old.text_hi - old.text_lo):.1%} of .text",
          covered > 0.95 * (old.text_hi - old.text_lo))

    # Independent of the anchors: every code pointer in a vtable must land where the text
    # carry says, read at the vtable's carried address.
    print("vtables (text carry checked through the data carry):")
    for name, rva in (("chr_cam_vmt", 0x2A2AA08), ("cscam_vmt", 0x2AA0A18), ("csbullet_state_vmt", 0x2A28418)):
        moved = carry(rva, rows)
        if moved is None:
            check(f"{name} {rva:#x} carried", False, "refused")
            continue
        good = bad = 0
        for i in range(16):
            po_ = struct.unpack_from("<Q", old.data, rva + i * 8)[0] - IMAGE_BASE
            pn_ = struct.unpack_from("<Q", new.data, moved + i * 8)[0] - IMAGE_BASE
            if not (old.text_lo <= po_ < old.text_hi):
                continue
            c = carry(po_, rows)
            if c is None:
                continue
            good += c == pn_
            bad += c != pn_
        check(f"{name} {rva:#x} -> {moved:#x}: {good} slots agree", good >= 3 and bad == 0, f"{bad} disagree")

    print("\nRTTI type names land on the same string:")
    rtti = 0
    agree = 0
    lo, hi = old.section(".data")
    blob = old.data[lo:hi]
    at = blob.find(b".?AV")
    while at != -1 and rtti < 400:
        end = blob.find(b"\0", at)
        name = blob[at:end]
        c = carry(lo + at, rows)
        if c is not None:
            rtti += 1
            agree += new.data[c:c + len(name)] == name
        at = blob.find(b".?AV", end)
    check(f"{agree} of {rtti} carried RTTI names match", rtti > 50 and agree == rtti)

    print()
    if failures:
        print(f"selftest FAILED: {failures}")
        return 1
    print("selftest passed")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("addresses", nargs="*")
    ap.add_argument("--write", action="store_true", help=f"regenerate {TSV.relative_to(REPO_ROOT)}")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--measure", action="store_true",
                    help="check every decoded reader against the tsv, or against each TSV named")
    a = ap.parse_args()
    if a.selftest or a.write or a.measure:
        _ensure_decoder()
    if a.selftest:
        return selftest()
    if a.write:
        text, data, *_ = build()
        write_tsv(text, data)
        print(f"wrote {TSV}: {len(text)} text and {len(data)} data segments")
        return 0
    if a.measure:
        old, new = Image(OLD_IMAGE), Image(NEW_IMAGE)
        rc = 0
        for path in [Path(p) for p in a.addresses] or [TSV]:
            rows = read_tsv(path)
            agree, disagree = measure(rows, old, new)
            trusted = [r for r in agree + disagree if not r.doubt]
            print(f"{path}: {len(agree)} readers agree, {len(disagree)} disagree "
                  f"({len({r.target for r in disagree})} targets); of the {len(trusted)} whose "
                  f"following code matches, {len(trusted) - sum(1 for r in disagree if not r.doubt)} "
                  f"agree")
            votes: dict[int, Counter] = defaultdict(Counter)
            for r in trusted:
                votes[r.target][r.new_target - r.target] += 1
            for r in sorted(disagree, key=lambda r: (r.target, r.insn)):
                got = carry(r.target, rows)
                c = votes.get(r.target, Counter())
                n = sum(c.values())
                if r.doubt:
                    why = f"code changed: {r.doubt}"
                elif c.most_common(1)[0][1] * 2 > n:
                    why = f"outvoted: {c[r.new_target - r.target]} of {n} matching readers read here"
                else:
                    why = f"its {n} matching readers split evenly"
                print(f"  {r.target:#x} read at {r.insn:#x} ({'leaf' if r.fn is None else f'fn {r.fn:#x}'}): "
                      f"table {'refuses' if got is None else f'{got:#x}'}, reader reads "
                      f"{r.new_target:#x} -- {why}")
            rc |= bool(disagree)
        return rc
    if not a.addresses:
        ap.print_help()
        return 2
    rows = read_tsv()
    rc = 0
    for raw in a.addresses:
        v = int(raw, 16)
        va = v >= IMAGE_BASE
        c = carry(v - IMAGE_BASE if va else v, rows)
        if c is None:
            print(f"{v:#x}  REFUSED  in no carried segment")
            rc = 1
        else:
            print(f"{v:#x} -> {(c + IMAGE_BASE if va else c):#x}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
