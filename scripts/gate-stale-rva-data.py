#!/usr/bin/env python3
"""Route hand-built `base + SOME_RVA` DATA addresses through the 1.17 data map.

WHY THIS IS A SEPARATE TOOL FROM THE CALL ONE
---------------------------------------------
`gate-stale-rva-calls.py` handles addresses that get EXECUTED, where a refusal has to become a
control-flow decision (return what, exactly?) and the risk is deleting behaviour. Data addresses
have no such problem: `er_game_base::mem::game_data_addr` returns `0` when the running build has
no verified mapping, and every one of these sites is already a read or an identity compare with an
existing "not the object I wanted" branch. Handing it `0` puts a refusal down the path the caller
already had.

The hazard being fixed is the QUIET one. A stale data address does not crash -- the reads are
fault-safe -- so the comparison simply never matches and the feature behind it stops working with
nothing said. Measured 2026-08-29: `TITLE_OWNER_VTABLE_RVA` is `CS::TitleStep` in 1.16.2 and not a
vtable at all in 1.17, and its three scans had been finding no title owner, forever, silently.

ONLY CONSTANTS THE DATA MAP ALREADY CARRIES are rewritten. A constant with no row would gain
nothing but noise: `game_data_addr` would return 0 where the raw value at least had a chance of
being right on some build. Getting the row is `map-data-rvas-1162-to-1170.py`'s job first.

USAGE
    python3 scripts/gate-stale-rva-data.py --dry-run
    python3 scripts/gate-stale-rva-data.py
    python3 scripts/gate-stale-rva-data.py --selftest
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_MAP = os.path.join(REPO, "docs", "recon", "rva-map-1162-to-1170.data.tsv")
RESOLVER = "er_game_base::mem::game_data_addr"

# `$base` included on purpose: several sites live inside macro bodies, and the dollar has to stay
# attached to the identifier rather than being swallowed into the replacement.
SITE = re.compile(
    r"(?P<base>\$?\b(?:base|module_base|image_base|game_base)\b)\s*\+\s*"
    r"(?P<prefix>(?:\w+::)*)(?P<const>[A-Z0-9_]*RVA[A-Z0-9_]*)\b"
)
ALREADY_GATED = re.compile(r"game_data_addr|game_rva|resolve_game_address|game_ptr")


def mapped_constants() -> set[str]:
    names = set()
    with open(DATA_MAP, encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("0x"):
                parts = line.split("\t")
                if len(parts) > 2:
                    names.add(parts[2].strip())
    return names


def rewrite(path: str, mapped: set[str], dry_run: bool) -> int:
    lines = open(path, encoding="utf-8").read().splitlines(keepends=True)
    out, changed = [], 0
    for index, line in enumerate(lines):
        if line.lstrip().startswith("//"):
            out.append(line)
            continue
        window = "".join(lines[max(0, index - 2) : index + 3])
        if ALREADY_GATED.search(window):
            out.append(line)
            continue

        def replace(match: re.Match) -> str:
            nonlocal changed
            if match.group("const") not in mapped:
                return match.group(0)
            changed += 1
            constant = match.group("prefix") + match.group("const")
            return f'{RESOLVER}({match.group("base")}, {constant}, "{match.group("const")}")'

        out.append(SITE.sub(replace, line))
    if changed and not dry_run:
        open(path, "w", encoding="utf-8").write("".join(out))
    return changed


def selftest() -> int:
    failures = []
    mapped = mapped_constants()
    if len(mapped) < 40:
        failures.append(f"only {len(mapped)} constants read from the data map")
    match = SITE.search("if vt != base + FOO_VTABLE_RVA {")
    if not match or match.group("base") != "base":
        failures.append("SITE did not capture a plain `base +`")
    match = SITE.search("if vt == $base + FOO_VTABLE_RVA {")
    if not match or match.group("base") != "$base":
        failures.append("SITE lost the `$` on a macro base -- the rewrite would not compile")
    if not ALREADY_GATED.search("game_data_addr(base, FOO_RVA, \"FOO_RVA\")"):
        failures.append("ALREADY_GATED would rewrite an already-gated site twice")
    for line in failures:
        print(f"selftest FAIL {line}")
    print(f"selftest: {len(failures)} failure(s); {len(mapped)} mapped constants")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    mapped = mapped_constants()
    paths = args.paths or glob.glob(os.path.join(REPO, "crates", "**", "*.rs"), recursive=True)
    total = 0
    for path in paths:
        count = rewrite(path, mapped, args.dry_run)
        if count:
            print(f"  {os.path.relpath(path, REPO)}: {count}")
            total += count
    print(f"{'would route' if args.dry_run else 'routed'} {total} data site(s) through {RESOLVER}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
