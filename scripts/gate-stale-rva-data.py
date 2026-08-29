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

ONLY CONSTANTS ONE OF THE THREE MAPS ALREADY CARRIES are rewritten. A constant with no row would gain
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
# The resolver's table is fed by ALL THREE maps (see crates/er-game-base/build.rs), so "already
# mapped" has to mean the union. Scoring against the data map alone said 167 sites needed a new
# row; against the union it is 110, and 57 of the difference were free wins sitting in plain sight.
# NAME column per map, or None where the map has no constant column at all. `verified.tsv` has
# none -- its column 5 is a signature description -- and reading it as a name pulled junk into the
# "already mapped" set. Match by RVA there, which is the only key every map actually shares.
MAPS = (
    (os.path.join(REPO, "docs", "recon", "rva-map-1162-to-1170.data.tsv"), 2),
    (os.path.join(REPO, "docs", "recon", "rva-map-1162-to-1170.needed-verified.tsv"), 5),
    (os.path.join(REPO, "docs", "recon", "rva-map-1162-to-1170.verified.tsv"), None),
)
IMAGE_BASE = 0x140000000
# `const FOO_RVA: usize = 0x1234;` -- only the literal form; a constant defined from an enum
# discriminant has no value here and falls back to matching by NAME.
DECLARATION = re.compile(r"const\s+([A-Z0-9_]*RVA[A-Z0-9_]*)\s*:\s*usize\s*=\s*(0x[0-9a-fA-F_]+)")
RESOLVER = "er_game_base::mem::game_data_addr"

# `$base` included on purpose: several sites live inside macro bodies, and the dollar has to stay
# attached to the identifier rather than being swallowed into the replacement.
SITE = re.compile(
    r"(?P<base>\$?\b(?:base|module_base|image_base|game_base)\b)\s*\+\s*"
    r"(?P<prefix>(?:\w+::)*)(?P<const>[A-Z0-9_]*RVA[A-Z0-9_]*)\b"
)
ALREADY_GATED = re.compile(r"game_data_addr|game_rva|resolve_game_address|game_ptr")
# A HOOK TARGET must stay a raw `base + rva`: `MhHook::new` resolves it itself, through the DETOUR
# resolver, which is a stricter test than the call one. Pre-resolving it does one of two bad
# things -- translates the address TWICE (the bug in bd resolve-twice-refuses-double-translation),
# or hands MinHook the `0` that `game_data_addr` returns on a refusal, which is an install at
# address zero. Measured 2026-08-29: an earlier version of this tool rewrote 18 such sites,
# including a whole `let targets = [...]` list whose `MhHook::new` sat fourteen lines below the
# addresses it collected -- hence the deliberately wide window.
HOOK_TARGET = re.compile(r"MhHook::new|MH_CreateHook|register_union_hook|detour|trampoline|hook as \*mut", re.I)
HOOK_WINDOW_LINES = 14


def mapped_constants() -> set[str]:
    """Every constant the resolver can answer for, by NAME -- resolved through both keys.

    A constant counts as mapped when its NAME appears in a map that has a name column, OR when its
    declared RVA appears in any map. The second half matters: `verified.tsv` carries no names, so
    ~three constants per sweep looked unmapped while the resolver knew them perfectly well.
    """
    names: set[str] = set()
    rvas: set[int] = set()
    for path, column in MAPS:
        try:
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    if not line.startswith("0x"):
                        continue
                    parts = line.rstrip("\n").split("\t")
                    try:
                        value = int(parts[0], 16)
                    except ValueError:
                        continue
                    rvas.add(value - IMAGE_BASE if value >= IMAGE_BASE else value)
                    if column is not None and len(parts) > column:
                        names.add(parts[column].strip())
        except OSError:
            continue
    for path in glob.glob(os.path.join(REPO, "crates", "**", "*.rs"), recursive=True):
        try:
            source = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for match in DECLARATION.finditer(source):
            if int(match.group(2).replace("_", ""), 16) in rvas:
                names.add(match.group(1))
    return names


def rewrite(path: str, mapped: set[str], dry_run: bool) -> int:
    lines = open(path, encoding="utf-8").read().splitlines(keepends=True)
    out, changed = [], 0
    for index, line in enumerate(lines):
        if line.lstrip().startswith("//"):
            out.append(line)
            continue
        window = "".join(lines[max(0, index - 2) : index + 3])
        hook_window = "".join(
            lines[max(0, index - HOOK_WINDOW_LINES) : index + HOOK_WINDOW_LINES + 1]
        )
        if ALREADY_GATED.search(window) or HOOK_TARGET.search(hook_window):
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
    if len(mapped) < 200:
        failures.append(f"only {len(mapped)} constants read from the data map")
    match = SITE.search("if vt != base + FOO_VTABLE_RVA {")
    if not match or match.group("base") != "base":
        failures.append("SITE did not capture a plain `base +`")
    match = SITE.search("if vt == $base + FOO_VTABLE_RVA {")
    if not match or match.group("base") != "$base":
        failures.append("SITE lost the `$` on a macro base -- the rewrite would not compile")
    if not ALREADY_GATED.search("game_data_addr(base, FOO_RVA, \"FOO_RVA\")"):
        failures.append("ALREADY_GATED would rewrite an already-gated site twice")
    if not HOOK_TARGET.search("let hook = unsafe { MhHook::new(target as *mut c_void, detour) };"):
        failures.append("HOOK_TARGET missed an MhHook::new install -- it would be pre-resolved")
    if HOOK_TARGET.search("if vt != base + FOO_VTABLE_RVA {"):
        failures.append("HOOK_TARGET wrongly claimed a plain vtable compare is a hook install")
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
