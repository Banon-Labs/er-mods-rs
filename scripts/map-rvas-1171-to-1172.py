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
  * data: every rip-relative operand (`modrm` mod 00, rm 101) inside a text
    segment is read at its place in both images. The difference of the two targets
    is the data delta at that target. Targets are voted on, consecutive targets
    sharing a delta form a segment, and anything between segments that disagree is
    refused.

Refusing is the point. A carried address that lands in changed code is a
mid-function detour or a wrong global; a refused one is a dead feature with a log
line naming it.

Usage
-----
    python3 scripts/map-rvas-1171-to-1172.py --write        # regenerate the tsv
    python3 scripts/map-rvas-1171-to-1172.py 0x140b00070    # carry addresses (reads the tsv)
    python3 scripts/map-rvas-1171-to-1172.py --selftest

Both images are read-only, gitignored inputs; see `AGENTS.md`.
"""

from __future__ import annotations

import argparse
import bisect
import os
import struct
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

IMAGE_BASE = 0x140000000
REPO_ROOT = Path(__file__).resolve().parent.parent
TSV = REPO_ROOT / "docs" / "recon" / "carry-1171-to-1172.tsv"

# A resync after a changed function looks this far ahead in each list, and needs
# this many consecutive entries agreeing on lengths and delta to accept.
RESYNC_WINDOW = 256
RESYNC_RUN = 8

# A data target is accepted when at least this many references agree and they
# are at least this share of all references to it.
MIN_DATA_VOTES = 2
MIN_DATA_SHARE = 0.75


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


def data_anchors(old: Image, new: Image, segs: list[tuple[int, int, int]]) -> dict[int, int]:
    """{old data rva: delta}, voted over every rip-relative operand in carried code."""
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
        def i32(buf, at):
            b = buf[at[:, None] + np.arange(4)].astype(np.uint32)
            v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16) | (b[:, 3] << 24)
            return v.astype(np.int64) - ((v >= 0x80000000).astype(np.int64) << 32)
        do = i32(od, p)
        dn = i32(nd, p + d)
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


def data_segments(anchors: dict[int, int], old: Image) -> list[tuple[int, int, int]]:
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


def mispaired(old: Image, new: Image, pairs: list, data: list) -> set[int]:
    """Old entry starts whose pair is two different functions of the same length.

    Length and neighbour delta cannot tell two same-length stubs apart: `0x262890` is a "Failed to
    allocate TLS Object" stub in 1.17.1 and an "access to an out of range" stub in 2.7.2.0, at the
    same address. What does tell them apart is where they point. Every rip-relative operand of a
    real pair, carried through the data segments, lands where the new image's operand points; a
    mispair's operands point somewhere else. A pair is refused when at least two of its operands
    disagree and they outnumber the ones that agree.
    """
    import numpy as np

    od = np.frombuffer(old.data, dtype=np.uint8)
    nd = np.frombuffer(new.data, dtype=np.uint8)
    starts = np.array([b for b, *_ in pairs], dtype=np.int64)
    ends = np.array([e for _, e, *_ in pairs], dtype=np.int64)
    dlo = np.array([s[0] for s in data], dtype=np.int64)
    dhi = np.array([s[1] for s in data], dtype=np.int64)
    dd = np.array([s[2] for s in data], dtype=np.int64)

    def i32(buf, at):
        b = buf[at[:, None] + np.arange(4)].astype(np.uint32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16) | (b[:, 3] << 24)
        return v.astype(np.int64) - ((v >= 0x80000000).astype(np.int64) << 32)

    bad = np.zeros(len(pairs), dtype=np.int64)
    good = np.zeros(len(pairs), dtype=np.int64)
    # One contiguous span per run of adjacent pairs is enough; per-function bookkeeping is by index.
    for lo, hi, d in text_segments(pairs):
        p = np.arange(lo + 1, hi - 4, dtype=np.int64)
        p = p[(od[p - 1] & 0xC7) == 0x05]
        if p.size == 0:
            continue
        old_t = p + 4 + i32(od, p)
        new_t = p + d + 4 + i32(nd, p + d)
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


def build() -> tuple[list, list, Image, Image, list]:
    old, new = Image(OLD_IMAGE), Image(NEW_IMAGE)
    po, pn = old.pdata(), new.pdata()
    pairs = pair_text(po, pn)
    data = data_segments(data_anchors(old, new, text_segments(pairs)), old)
    # Drop the pairs whose operands say they are different functions; the adjacency rule in
    # `text_segments` then refuses them and the gap on either side.
    wrong = mispaired(old, new, pairs, data)
    pairs = [p for p in pairs if p[0] not in wrong]
    text = widen_over_identical_gaps(text_segments(pairs), old, new, po)
    return text, data, old, new, pairs


def selftest() -> int:
    text, data, old, new, pairs = build()
    rows = sorted([("text", *s) for s in text] + [("data", *s) for s in data], key=lambda r: r[1])
    failures = []

    def check(label, ok, detail=""):
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}{(' -- ' + detail) if detail else ''}")
        if not ok:
            failures.append(label)

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
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.write:
        text, data, *_ = build()
        write_tsv(text, data)
        print(f"wrote {TSV}: {len(text)} text and {len(data)} data segments")
        return 0
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
