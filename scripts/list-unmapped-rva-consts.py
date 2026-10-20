#!/usr/bin/env python3
"""List game-address constants in `crates/` that are not a 1.16.2 source row of any address map.

    python3 scripts/list-unmapped-rva-consts.py [--all]

A constant named like an address (`*_RVA`, `*_VA`, `*GLOBAL*`, `*VTABLE*`, ...) whose value is a
1.16.2 source row of `docs/recon/rva-map-1162-to-1170*.tsv` is translated by the resolvers in
`er_game_base::game_build` and is not listed unless `--all` is given. Every other one is printed
with a tag:

  DST  the value is a 1.17.0 destination of the map -- measured on 1.17, so it must reach the
       running build through `carry_1171_rva` (after the 1.17.0 -> 1.17.1 step), not a resolver;
  ---  in no map at all: unmapped 1.16.2 (refused at resolve time), measured on 1.17.x, an offset
       into a foreign module, or not an address. Read the code to tell which.

Read-only triage for the 2.7.2.0 migration (bd er-effects-rs-gpkk); it decides nothing.
"""

import glob
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
IMAGE_BASE = 0x140000000
NAME = re.compile(r"RVA|VA\b|_VA_|ADDR|GLOBAL|FN|FUNC|SITE|ENTRY|VTABLE|SINGLETON")
CONST = re.compile(
    r"const\s+([A-Z0-9_]+)\s*:\s*(?:usize|u32|u64|isize)\s*=\s*(0x[0-9a-fA-F_]+)"
)
# A table row spelled as a struct literal, `rva: 0x...` / `function_rva: 0x...`; the field name
# stands in for the constant's.
FIELD = re.compile(r"\b([a-z_]*rva|[a-z_]*va)\s*:\s*(0x[0-9a-fA-F_]+)")


def rva(value: int) -> int:
    return value - IMAGE_BASE if value >= IMAGE_BASE else value


def map_rows():
    sources, dests = set(), set()
    files = glob.glob(str(REPO / "docs/recon/rva-map-1162-to-1170*.tsv"))
    files += glob.glob(str(REPO / "docs/recon/npc-possess-rva-map-1170.verified.tsv"))
    for path in files:
        for line in open(path, encoding="utf-8"):
            if line.startswith("#") or not line.strip():
                continue
            cols = line.split("\t")
            try:
                a, b = int(cols[0], 16), int(cols[1], 16)
            except (ValueError, IndexError):
                continue
            sources.add(rva(a))
            dests.add(rva(b))
    return sources, dests


def main() -> None:
    show_all = "--all" in sys.argv[1:]
    # `--any-name` drops the name filter: crates such as er-npc-summons name their 1.17.1
    # addresses after the function (`CAN_USE_GOODS`), with no `_RVA` suffix.
    any_name = "--any-name" in sys.argv[1:]
    sources, dests = map_rows()
    for path in sorted(glob.glob(str(REPO / "crates/**/*.rs"), recursive=True)):
        rel = Path(path).relative_to(REPO)
        for number, line in enumerate(open(path, encoding="utf-8", errors="replace"), 1):
            match = CONST.search(line) or FIELD.search(line)
            if not match or not (any_name or NAME.search(match.group(1))):
                continue
            value = rva(int(match.group(2).replace("_", ""), 16))
            if not 0x1000 <= value < 0x6000000:
                continue
            tag = "SRC" if value in sources else ("DST" if value in dests else "---")
            if tag != "SRC" or show_all:
                print(f"{tag} {rel}:{number} {match.group(1)} {value:#x}")


if __name__ == "__main__":
    main()
