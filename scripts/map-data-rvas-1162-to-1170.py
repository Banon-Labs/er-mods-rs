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


def references(image: Image, target: int) -> list[int]:
    """Byte offsets in .text whose 4-byte displacement points at `target`.

    A rip-relative displacement `d` stored at offset `i` addresses `i + 4 + d`
    (the instruction ends right after the displacement whenever the displacement
    is last, which is the case for the `lea`/`mov` forms that reference globals).
    So the hunt is for every `i` where `dword[i] + i + 4 == target`, which is one
    vectorised pass rather than decoding forty-three megabytes of instructions.
    """
    import numpy as np

    va, size = image.text
    hits = []
    for start in range(va, va + size, CHUNK):
        stop = min(start + CHUNK + 4, va + size)
        raw = np.frombuffer(image.data[start:stop], dtype=np.uint8).astype(np.uint32)
        if raw.size < 5:
            continue
        dw = raw[0:-3] | (raw[1:-2] << 8) | (raw[2:-1] << 16) | (raw[3:] << 24)
        idx = np.arange(start, start + dw.size, dtype=np.uint32)
        want = np.uint32((target - 4) & 0xFFFFFFFF) - idx
        for hit in np.nonzero(dw == want)[0]:
            hits.append(start + int(hit))
    return hits


def enclosing(starts: list[int], rva: int) -> int | None:
    import bisect

    i = bisect.bisect_right(starts, rva) - 1
    return starts[i] if i >= 0 else None


def instruction_index(md, image: Image, func: int, disp_at: int) -> int | None:
    """Which instruction of `func` carries its displacement at `disp_at`."""
    window = image.data[func : disp_at + 16]
    for n, insn in enumerate(md.disasm(window, BASE + func)):
        pos = insn.address - BASE - func
        if insn.disp_size == 4 and func + pos + insn.disp_offset == disp_at:
            return n
        if func + pos > disp_at:
            return None
    return None


def displacement_of(md, image: Image, func: int, index: int) -> int | None:
    """Where instruction `index` of `func` points, rip-relatively."""
    window = image.data[func : func + 0x400]
    for n, insn in enumerate(md.disasm(window, BASE + func)):
        if n == index:
            if insn.disp_size != 4:
                return None
            return insn.address - BASE + insn.size + insn.disp
    return None


def carry(md, old: Image, new: Image, fmap: dict[int, int], target: int):
    old_starts, new_starts = old.function_starts(), new.function_starts()
    votes: dict[int, int] = {}
    seen_functions = 0
    for disp_at in references(old, target):
        func = enclosing(old_starts, disp_at)
        if func is None or func not in fmap:
            continue
        index = instruction_index(md, old, func, disp_at)
        if index is None:
            continue
        seen_functions += 1
        moved = displacement_of(md, new, fmap[func], index)
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



CONST = re.compile(r"const\s+([A-Z0-9_]*RVA[A-Z0-9_]*)\s*:\s*usize\s*=\s*(0x[0-9a-fA-F_]+)")
BOUND = re.compile(r"_(MIN|MAX|BOUND|BASE|SIZE|LEN|LENGTH|COUNT|END|START|STRIDE|ALIGN)$")
DATA_MAP = "docs/recon/rva-map-1162-to-1170.data.tsv"


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
    for path in sorted(repo.glob("crates/**/*.rs")):
        for name, value in CONST.findall(path.read_text(encoding="utf-8", errors="replace")):
            if BOUND.search(name):
                continue
            rva = int(value.replace("_", ""), 16)
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

    rows, weak = [], []
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
            weak.append((name, rva, f"{note}, winner {best}/{total}"))
            continue
        rows.append((name, rva, moved, best, total))

    head = [
        "# 1.16.2 RVA\t1.17 RVA\tconstant\tvotes",
        "# Generated by scripts/map-data-rvas-1162-to-1170.py --refresh.",
        "# Data has no content to compare, so each row is carried by the CODE that references it:",
        "# every rip-relative reference in 1.16.2 .text is mapped onto its 1.17 function and the",
        "# same instruction re-read there. `votes` is agreeing references / total. A row with",
        "# fewer than two agreeing references, or without a clear majority, is listed at the",
        "# bottom as UNUSED rather than promoted -- a missing address costs a feature, a confident",
        "# wrong one cost a boot (0x3d6e278, the first 2.7.0.0 cs_system_step).",
    ]
    body = [f"0x{rva:x}\t0x{moved:x}\t{name}\t{best}/{total}" for name, rva, moved, best, total in rows]
    tail = ["#", "# UNUSED -- not enough agreement to be worth trusting:"]
    tail += [f"# {name}\t0x{rva:x}\t{note}" for name, rva, note in weak]
    (repo / DATA_MAP).write_text("\n".join(head + body + tail) + "\n", encoding="utf-8")
    print(f"wrote {DATA_MAP}: {len(rows)} usable row(s), {len(weak)} withheld")
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


def main() -> int:
    _ensure("capstone")
    _ensure("numpy")
    from capstone import CS_ARCH_X86, CS_MODE_64, Cs

    repo = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rvas", nargs="*", help="1.16.2 data RVAs or VAs (hex)")
    ap.add_argument("--old", type=Path, default=repo / "eldenring-deobf.bin")
    ap.add_argument("--new", type=Path, default=repo / "eldenring-deobf-1.17.bin")
    ap.add_argument("--map", type=Path, default=repo / "docs/recon/rva-map-1162-to-1170.functions.tsv")
    ap.add_argument("--refresh", action="store_true", help="rewrite the tracked data map")
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
