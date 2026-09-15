#!/usr/bin/env python3
"""Find which field of a map param carries a `PlaceName` text id.

The offline regulation reader needs no paramdef, which is what makes it usable at
all -- and what means it reports row ids rather than named fields. This closes
that gap for one param without acquiring a paramdef: it dumps every row at its
real stride and scores each candidate offset on how much it behaves like a text
id rather than a flag, a count or padding.

A field is interesting when its values are large, varied, and shared between
rows -- several tiles of one region carry the same name, so a place-name field
has fewer distinct values than rows but far more than a boolean. Padding is
constant, flags are tiny, and per-row keys are unique; all three score low.

Run it against the installed regulation:

    python3 scripts/map-region-place-names.py
    python3 scripts/map-region-place-names.py --param MapGdRegionInfoParam

What it found, 2026-09-15, and what that cost to learn:

* `WorldMapPieceParam` -- 34 rows, stride 64 -- carries the text id at `+0x04`
  (`62010`, `62011`, `62012`, `62020`, ... a structured `62AAB` numbering), four
  floats at `+0x08..+0x17` describing the piece in map space, and a second text
  id at `+0x18` (`63010`, `63011`, ...). This is the table that names a region.
* `MapGdRegionInfoParam` -- 293 rows keyed by the block id, stride 32 -- is
  almost empty: a flag at `+0x00` and a small value at `+0x04` whose 115
  distinct values are area-number shaped. Tile-keyed, but it holds no name.
* `WorldMapPlaceNameParam` has ten rows and names nothing.

So the tile-keyed table and the name-carrying table are different params, which
is the thing a name-string search cannot tell you and a row dump settles in one
run.
"""

from __future__ import annotations

import argparse
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "regulation_params", Path(__file__).resolve().parent / "regulation-params.py"
)
_reg = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_reg)

ROW_ENTRY_SIZE = 24
ROW_TABLE_OFFSET = 0x40


def rows(param: bytes) -> list[tuple[int, int]]:
    """`(row id, data offset)` in file order."""
    count = struct.unpack_from("<H", param, 0x0A)[0]
    out = []
    for index in range(count):
        at = ROW_TABLE_OFFSET + index * ROW_ENTRY_SIZE
        row_id = struct.unpack_from("<i", param, at)[0]
        data_offset = struct.unpack_from("<q", param, at + 8)[0]
        out.append((row_id, data_offset))
    return out


def stride(entries: list[tuple[int, int]]) -> int:
    """Derived from consecutive data offsets, never from a paramdef."""
    gaps = Counter(b - a for (_, a), (_, b) in zip(entries, entries[1:]) if b > a)
    if not gaps:
        raise SystemExit("cannot derive a row stride from one row")
    return gaps.most_common(1)[0][0]


def score(values: list[int], row_count: int) -> float:
    """How much this offset behaves like a text id.

    Rewards large values and repetition across rows, punishes constants and
    per-row uniqueness. Deliberately crude: it ranks candidates for a human to
    read, it does not decide anything.
    """
    distinct = len(set(values))
    if distinct <= 1:
        return 0.0
    # One name per row is not disqualifying. An earlier version zeroed any field whose
    # values were all distinct, on the theory that such a field is a key -- and that rule
    # buried WorldMapPieceParam's real text id at +0x04, because a table of 34 map pieces
    # has 34 different names. Repetition is now a mild preference, not a gate.
    plausible = sum(1 for v in values if 1_000 <= v <= 10_000_000)
    repetition = 1.0 if distinct < row_count else 0.75
    return (plausible / row_count) * repetition


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--param", default="WorldMapPieceParam")
    parser.add_argument("--regulation")
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--row-id", type=int, action="append", default=[])
    args = parser.parse_args()

    path = args.regulation or _reg.DEFAULT_REGULATION
    files = _reg.bnd4_entries(_reg.dcx_unpack(_reg.decrypt(path)))
    key = next((k for k in files if Path(k.replace("\\", "/")).stem == args.param), None)
    if key is None:
        print(f"{args.param}: not found among {len(files)} params", file=sys.stderr)
        return 1

    param = files[key]
    entries = rows(param)
    width = stride(entries)
    print(f"{args.param}: {len(entries)} rows, stride {width} (0x{width:x})")

    body = {row_id: param[at : at + width] for row_id, at in entries}
    # Aligned offsets only. An unaligned window over a float array scores well and means
    # nothing: the first run of this tool ranked +0x0d, +0x11 and +0x09 above the real field,
    # all three of them the middle of a coordinate.
    ranked = []
    for offset in range(0, width - 3, 4):
        i32 = [struct.unpack_from("<i", b, offset)[0] for b in body.values()]
        ranked.append((score(i32, len(entries)), offset, i32))
    ranked.sort(reverse=True, key=lambda r: r[0])

    print("\ncandidate text-id fields, best first (float column shown so a coordinate outs itself):")
    for value, offset, values in ranked[: args.top]:
        distinct = len(set(values))
        sample = sorted({v for v in values if v > 0})[:6]
        floats = [struct.unpack_from("<f", b, offset)[0] for b in body.values()]
        span = f"{min(floats):.1f}..{max(floats):.1f}"
        print(
            f"  +0x{offset:02x}  score {value:.3f}  {distinct} distinct  "
            f"sample {sample}  as f32 {span}"
        )

    # The four floats of a map piece are two ranges, not two points -- and the difference is
    # not cosmetic: paired as (x0, z0, x1, z1) a third of the rows look inside-out, which reads
    # as "these are not a rectangle" and sends you looking for a centre-and-extent that is not
    # there. Paired as (min, max) per axis, every row holds. Re-derived on every run rather
    # than written down, so a paramdef change cannot leave a stale claim in a comment.
    if width >= 0x18:
        pairs = [
            ("+0x08 < +0x0c  (x)", 0x08, 0x0C),
            ("+0x10 < +0x14  (z)", 0x10, 0x14),
        ]
        print("\nfloat pairs read as ranges:")
        for label, low, high in pairs:
            held = sum(
                1
                for b in body.values()
                if struct.unpack_from("<f", b, low)[0] < struct.unpack_from("<f", b, high)[0]
            )
            verdict = "holds for every row" if held == len(body) else f"holds for {held}/{len(body)}"
            print(f"  {label}: {verdict}")

    for row_id in args.row_id:
        if row_id not in body:
            print(f"\nrow {row_id}: absent")
            continue
        print(f"\nrow {row_id} ({row_id:08d}):")
        for _, offset, _ in ranked[: args.top]:
            print(f"  +0x{offset:02x} = {struct.unpack_from('<i', body[row_id], offset)[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
