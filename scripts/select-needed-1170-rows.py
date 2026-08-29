#!/usr/bin/env python3
"""Pick the rows of the whole-image function map that this repo actually needs.

`docs/recon/rva-map-1162-to-1170.functions.tsv` pairs 94,111 functions. Baking
all of them into `er-game-base` would put three quarters of a megabyte of
address table into each of eighteen DLLs and make every resolve a linear scan
of 94,000 entries -- for a set of addresses this workspace names in about a
hundred constants.

So this selects the intersection: every `const *_RVA: usize = 0x...` declared
anywhere in `crates/`, looked up in the function map. The output is tracked, so
the addresses that reach the binary arrive as a reviewable diff rather than
appearing from a 94,000-row file nobody reads.

PROVENANCE MATTERS AND IS RECORDED PER ROW. These pairs are established by
masked-signature identity across `.pdata`, which is a weaker claim than the
byte-for-byte instruction comparison behind
`rva-map-1162-to-1170.verified.tsv`. The verified map therefore wins wherever
both cover an address, and a row selected here is good enough to CALL but
should still pass `audit-1170-hook-targets.py` before anything DETOURS it --
the audit checks that the destination is a real function entry with room for
MinHook's five-byte patch, which signature identity does not.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

BASE = 0x140000000
CONST = re.compile(r"const\s+([A-Z0-9_]*RVA[A-Z0-9_]*)\s*:\s*usize\s*=\s*(0x[0-9a-fA-F_]+)")
# `pub const FOO_RVA: usize = SomeEnum::Variant as usize;` -- the value lives on the enum, not at
# the declaration, so a `= 0x...` scan cannot see it. 37 constants are written this way and the
# selector was blind to every one. It cost a black screen: TITLE_TOP_DIALOG_IS_IN_STATE_RVA
# (`TitleDialogRva::IsInState = 0x749b20`) never reached the map, so the running game REFUSED it,
# `title_dialog_state` could not tell whether the title had reached Loop, and the boot cover --
# which releases on that observation -- never released. 37 `boot-view DECISION` lines, every one
# `own_menu=false render_ready=false`, in front of a title screen that was rendering fine underneath.
ALIAS = re.compile(r"const\s+([A-Z0-9_]*RVA[A-Z0-9_]*)\s*:\s*usize\s*=\s*(\w+)::(\w+)\s+as\s+usize")
VARIANT = re.compile(r"^\s*(\w+)\s*=\s*(0x[0-9a-fA-F_]+)\s*,", re.M)
# Names that describe a RANGE rather than an address. `AV_GAME_TEXT_RVA_MIN` is
# 0x1000, which is where .text begins and therefore also where a function
# begins -- so it pairs cleanly and means nothing. Translating a bound would be
# a category error, and it is the only kind the intersection cannot catch by
# itself, because every other non-function constant lives in .data or .rdata
# and simply is not in a table built from .pdata.
BOUND = re.compile(r"_(MIN|MAX|BOUND|BASE|SIZE|LEN|LENGTH|COUNT|END|START|STRIDE|ALIGN)$")
OUTPUT = "docs/recon/rva-map-1162-to-1170.needed.tsv"
FUNCTIONS = "docs/recon/rva-map-1162-to-1170.functions.tsv"
VERIFIED = "docs/recon/rva-map-1162-to-1170.verified.tsv"
OBSERVED = "docs/recon/rva-1170-observed-refusals.txt"


def declared_rvas(repo: Path) -> dict[str, int]:
    """Every `*_RVA` constant declared under crates/, by name.

    Two declaration forms, and missing the second one is what let a refused address black-screen
    the game: a literal `= 0x...`, and an alias onto an enum variant whose value lives elsewhere.
    """
    out: dict[str, int] = {}
    aliases: dict[str, tuple[str, str]] = {}
    variants: dict[tuple[str, str], int] = {}
    enum_of_variant: dict[str, int] = {}
    for path in sorted(repo.glob("crates/**/*.rs")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, value in CONST.findall(text):
            if BOUND.search(name):
                continue
            out.setdefault(name, int(value.replace("_", ""), 16))
        for name, enum_name, variant in ALIAS.findall(text):
            if not BOUND.search(name):
                aliases.setdefault(name, (enum_name, variant))
        for variant, value in VARIANT.findall(text):
            # Variant names are matched without their enum -- the declaration and the enum body are
            # routinely in different files, and a variant name is unique enough in practice. A
            # collision would only ever re-point a constant the verifier then rejects.
            enum_of_variant.setdefault(variant, int(value.replace("_", ""), 16))
    del variants
    for name, (_enum_name, variant) in aliases.items():
        value = enum_of_variant.get(variant)
        if value is not None:
            out.setdefault(name, value)
    return out


def read_map(path: Path) -> dict[int, int]:
    out: dict[int, int] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        cols = line.split("\t")
        if len(cols) < 2:
            continue
        try:
            old, new = int(cols[0], 16), int(cols[1], 16)
        except ValueError:
            continue
        out[old - BASE if old >= BASE else old] = new - BASE if new >= BASE else new
    return out


def verified_rvas(path: Path) -> set[int]:
    """RVAs the byte-comparison verifier already covers; those win."""
    out: set[int] = set()
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        cols = line.split("\t")
        if len(cols) < 3 or cols[2] != "IDENTICAL":
            continue
        try:
            out.add(int(cols[0], 16) - BASE)
        except ValueError:
            continue
    return out


def observed_rvas(path: Path) -> list[int]:
    """Addresses the running game asked for and was refused; see record-1170-refusals.py."""
    out: list[int] = []
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        try:
            out.append(int(line, 16))
        except ValueError:
            continue
    return out


def select(repo: Path) -> tuple[list[tuple[str, int, int]], list[str]]:
    functions = read_map(repo / FUNCTIONS)
    already = verified_rvas(repo / VERIFIED)
    wanted = dict(declared_rvas(repo))
    # The declaration scan is not the whole population. An address the GAME reached and the
    # gate refused is wanted whether or not a constant name gave it away -- 42 of the 54
    # refused on 2026-08-29 were answerable by the function map and simply never selected.
    for rva in observed_rvas(repo / OBSERVED):
        if rva not in wanted.values():
            wanted.setdefault(f"(refused at runtime 0x{rva:x})", rva)
    rows, missing = [], []
    for name, rva in sorted(wanted.items()):
        if rva in already:
            continue
        if rva in functions:
            rows.append((name, rva, functions[rva]))
        else:
            missing.append(name)
    rows.sort(key=lambda r: r[1])
    return rows, missing


def render(rows) -> str:
    head = [
        "# 1.16.2 RVA\t1.17 RVA\tconstant",
        "# Selected by scripts/select-needed-1170-rows.py from",
        "# rva-map-1162-to-1170.functions.tsv -- the subset this workspace names in a",
        "# `const *_RVA` declaration. Pairs come from masked-signature identity across",
        "# .pdata, which is weaker evidence than the byte comparison behind",
        "# rva-map-1162-to-1170.verified.tsv; rows that map already covers are omitted",
        "# here so the stronger evidence wins. Good enough to CALL. Before DETOURING one,",
        "# run scripts/audit-1170-hook-targets.py: signature identity does not check that",
        "# the destination has room for MinHook's five-byte patch.",
    ]
    return "\n".join(head + [f"0x{old:x}\t0x{new:x}\t{name}" for name, old, new in rows]) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    repo = Path(__file__).resolve().parent.parent
    ap.add_argument("--repo", type=Path, default=repo)
    ap.add_argument("--refresh", action="store_true", help="rewrite the tracked file")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        failures = []
        names = declared_rvas(args.repo)
        if any(BOUND.search(n) for n in names):
            failures.append("a range-bound constant survived the filter")
        if len(names) < 50:
            failures.append(f"only {len(names)} *_RVA constants found; the scan is not seeing the sources")
        # The crash of 2026-08-29 is the case this must never silently drop.
        # The alias form, asserted by the constant whose absence black-screened the game on
        # 2026-08-29: `TITLE_TOP_DIALOG_IS_IN_STATE_RVA = TitleDialogRva::IsInState as usize`.
        if names.get("TITLE_TOP_DIALOG_IS_IN_STATE_RVA") != 0x749B20:
            failures.append(
                "enum-alias constants are invisible again -- "
                "TITLE_TOP_DIALOG_IS_IN_STATE_RVA did not resolve to 0x749b20"
            )
        if names.get("GET_CURRENT_MAP_ID_RVA") != 0x5EEFB0:
            failures.append("GET_CURRENT_MAP_ID_RVA did not parse to 0x5eefb0")
        rows, _missing = select(args.repo)
        observed = observed_rvas(args.repo / OBSERVED)
        if observed and not any(old in observed for _n, old, _new in rows):
            failures.append("no runtime-observed refusal made it into the selection")
        if not any(old == 0x5EEFB0 for _n, old, _new in rows):
            failures.append("0x5eefb0 is not in the selection; the live crasher would stay unmapped")
        for line in failures:
            print(f"SELFTEST FAIL: {line}")
        print(f"selftest: {len(names)} constants, {len(rows)} selected, {len(failures)} failure(s)")
        return 1 if failures else 0

    rows, missing = select(args.repo)
    text = render(rows)
    target = args.repo / OUTPUT
    if args.refresh:
        target.write_text(text, encoding="utf-8")
        print(f"wrote {target} ({len(rows)} rows); {len(missing)} constant(s) still unmapped")
        return 0
    current = target.read_text(encoding="utf-8") if target.is_file() else ""
    if current == text:
        print(f"OK: {target.name} is current ({len(rows)} rows, {len(missing)} still unmapped)")
        return 0
    print(f"FAIL: {target.name} is out of date. Re-run with --refresh.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
