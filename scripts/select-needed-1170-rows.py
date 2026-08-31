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

WHICH LEDGER IS WRITABLE BY HAND, AND WHICH IS NOT
--------------------------------------------------
`rva-map-1162-to-1170.needed.tsv` is GENERATED. `--refresh` rewrites it
WHOLESALE from `functions.tsv`, so every line of it is disposable by
construction -- including a line a human typed. Its sibling
`rva-map-1162-to-1170.verified.tsv` is the hand-curated one: rows are added to
it with their own derivation recorded in the `how` column, and nothing
regenerates it from a machine source.

That asymmetry set a trap, and the trap is the reason for `unreproduced()`
below. Before 2026-08-30 a hand-added row in `needed.tsv` whose pair was absent
from `functions.tsv` was DELETED by the next `--refresh`, with exit 0 and no
line of output naming it. Measured, on a scratch copy, with the two rows a
merge agent had been about to add: `0x764290 -> 0x7650e0` and
`0x8c47c0 -> 0x8c5960` both vanished and the file's sha256 returned to its
pre-edit value. The loss does not read as a loss afterwards -- the address
reads as one that was never mapped, and the feature it unblocked simply stops
working again. Short `.pdata` records and body-changed functions are exactly
the addresses that need hand derivation, and exactly the ones `functions.tsv`
cannot carry, so the rows most worth typing were the rows most certain to
evaporate.

So a row this script cannot reproduce is now PRESERVED rather than dropped,
written back under the `HAND-CARRIED` banner at the foot of the file, listed on
stderr at every run, and never silently removed. Preserving carries its own
hazard -- a wrong row that survives forever reads as a live value, and
`er-game-base/build.rs::refuted_sources()` subtracts a `DIVERGES` row from BOTH
maps precisely because a wrong pair is worse than a missing one -- so a
preserved row is not immortal. It leaves in one of four ways:

  * `functions.tsv` gains a pair that AGREES: the row is reproduced normally
    and the banner drops it, no hand edit;
  * `functions.tsv` gains a pair that DISAGREES: that is a contradiction, not
    weak evidence, and this script REFUSES to write until a human settles it;
  * the last `crates/` declaration of the address goes away: the row is still
    preserved, but it is reported as `undeclared`, which is the standing
    invitation to delete it;
  * a human deletes the line. `--refresh` does not put it back -- preservation
    only ever carries forward what it finds.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

BASE = 0x140000000
# The TYPE is not part of what makes something a game address. Requiring `usize` here made every
# `: u32` constant invisible to this scanner -- and invisibility is total: the row is never
# selected, never mapped, never verified, and the game refuses it at runtime with no clue why.
# Measured 2026-08-30: SYSTEM_QUIT_RETURN_TITLE_ACTION_DO_CALL_RVA is declared `: u32`, so the
# whole System>Quit tab routing was refused on 1.17 -- including the guard in front of the native
# Return-to-Desktop confirmation. Accept any integer width; the NAME is the signal.
# A game address does not need a NAME to be a game address. Three crates keep theirs as bare
# `rva: 0x...` fields inside `HookSpec`/`MapSeam` table literals -- 39 in er-reload-trace, 13 in
# er-invasion-warp, 1 in er-seamless-bugfixes -- and because every tool here keyed on the constant
# NAME, all 53 were invisible: never selected, never mapped, never verified, and refused at
# runtime under no name anyone could search for. Underscore separators are used in those tables
# (`0x088_55b0`), so they have to be accepted and stripped.
BARE_RVA_FIELD = re.compile(r"\brva\s*:\s*(0x[0-9a-fA-F_]+)")

RVA_TYPE = r"(?:usize|u32|u64)"
CONST = re.compile(
    r"const\s+([A-Z0-9_]*RVA[A-Z0-9_]*)\s*:\s*" + RVA_TYPE + r"\s*=\s*(0x[0-9a-fA-F_]+)"
)
# `pub const FOO_RVA: usize = SomeEnum::Variant as usize;` -- the value lives on the enum, not at
# the declaration, so a `= 0x...` scan cannot see it. 37 constants are written this way and the
# selector was blind to every one. It cost a black screen: TITLE_TOP_DIALOG_IS_IN_STATE_RVA
# (`TitleDialogRva::IsInState = 0x749b20`) never reached the map, so the running game REFUSED it,
# `title_dialog_state` could not tell whether the title had reached Loop, and the boot cover --
# which releases on that observation -- never released. 37 `boot-view DECISION` lines, every one
# `own_menu=false render_ready=false`, in front of a title screen that was rendering fine underneath.
ALIAS = re.compile(
    r"const\s+([A-Z0-9_]*RVA[A-Z0-9_]*)\s*:\s*" + RVA_TYPE + r"\s*=\s*(\w+)::(\w+)\s+as\s+" + RVA_TYPE
)
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
BUILD_RS = "crates/er-game-base/build.rs"
# The line the preserved rows sit under. Matched as a prefix when the file is re-read, so the rest
# of the banner can be reworded without orphaning the rows it introduces.
HAND_BANNER = "# HAND-CARRIED"


def declared_rvas(repo: Path) -> dict[str, int]:
    """Every game address declared under crates/, by name.

    Three declaration forms, and each one that was missing cost a feature:
      * a literal `const FOO_RVA: usize = 0x...`;
      * an alias onto an enum variant whose value lives in another file -- missing this one let a
        refused address black-screen the game;
      * a bare `rva: 0x...` field in a `HookSpec`/`MapSeam` table, which has NO constant name at
        all. Those get a synthetic `<file>:<line>` key, because the map still needs the address
        and a refusal still needs something a human can search for.
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
        for match in BARE_RVA_FIELD.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            rel = path.relative_to(repo).as_posix()
            out.setdefault(f"{rel}:{line}", int(match.group(1).replace("_", ""), 16))
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


def exhaustive_verdicts(repo: Path) -> list[str]:
    """The verdict strings `er-game-base/build.rs` treats as a WHOLE-body comparison.

    Parsed out of `build.rs`, not copied. The vocabulary has already moved once -- the plain
    `IDENTICAL` of 2026-08-28 became `BYTE-IDENTICAL` / `IDENTICAL-WHOLE` / `IDENTICAL-LEAF` -- and
    the two tools that had transcribed the old word went on reporting confident numbers about a
    string that no longer occurs in the file (one counted DETOUR at 42 rather than 374). A
    transcription cannot notice that; a parse fails loudly and empty.
    """
    text = (repo / BUILD_RS).read_text(encoding="utf-8", errors="replace")
    # BOTH floor-exempt lists, because build.rs has had two since 2026-08-30 and reading only the
    # first is the same transcription error one word later: PATCH_SITE_VERDICTS carries rows whose
    # bodies DIFFER while their patch sites do not, and they are admitted without a floor exactly
    # as the exhaustive ones are.
    out: list[str] = []
    for name in ("EXHAUSTIVE_VERDICTS", "PATCH_SITE_VERDICTS"):
        match = re.search(rf"const\s+{name}\s*:[^=]*=\s*\[([^\]]*)\]", text)
        if match:
            out += re.findall(r'"([^"]+)"', match.group(1))
    return out


def verified_covered(path: Path, verdicts: list[str]) -> dict[int, int]:
    """Every RVA `verified.tsv` covers with one of `verdicts`, mapped to its 1.17 pair.

    Reported, NOT subtracted. `verified_rvas` above -- which decides what this selection skips --
    still matches the literal string `IDENTICAL`, and as of 2026-08-30 that matches none of the 99
    rows in the file, so the docstring's "the verified map wins wherever both cover an address" is
    not what this script actually does. `build.rs` re-establishes that precedence itself by
    address, which is why the drift is currently duplication rather than a wrong address, and why
    narrowing the selection here is a deliberate change to the ledger's contents rather than a
    drive-by. Surfacing the count is the part that costs nothing.
    """
    out: dict[int, int] = {}
    if not path.is_file() or not verdicts:
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        cols = line.split("\t")
        if len(cols) < 3 or cols[2] not in verdicts:
            continue
        try:
            out[int(cols[0], 16) - BASE] = int(cols[1], 16) - BASE
        except ValueError:
            continue
    return out


def body_rows(path: Path) -> list[tuple[int, int, str]]:
    """The (old, new, label) rows the tracked file currently holds, comments dropped."""
    if not path.is_file():
        return []
    return body_rows_from_text(path.read_text(encoding="utf-8", errors="replace"))


def body_rows_from_text(text: str) -> list[tuple[int, int, str]]:
    """Same, from text. Split out so the guard's selftest never needs a file on disk."""
    out: list[tuple[int, int, str]] = []
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        cols = line.split("\t")
        if len(cols) < 2:
            continue
        try:
            old, new = int(cols[0], 16), int(cols[1], 16)
        except ValueError:
            continue
        old = old - BASE if old >= BASE else old
        new = new - BASE if new >= BASE else new
        out.append((old, new, cols[2] if len(cols) > 2 else ""))
    return out


def unreproduced(
    current: list[tuple[int, int, str]],
    rows: list[tuple[str, int, int]],
    declared: set[int],
) -> tuple[list[tuple[int, int, str, str]], list[tuple[int, int, int, str]]]:
    """Split the rows on disk that this run would not produce into KEEP and CONFLICT.

    Returns `(preserved, conflicts)`.

    `preserved` is one entry per (old, new) pair the regeneration does not contain and does not
    contradict: hand work, carried forward with the reason it is not reproducible attached, so the
    operator reads WHY on every run rather than discovering the row's absence months later.

    `conflicts` is the dangerous half: the file says `old -> a`, this run says `old -> b`. One of
    the two is a wrong address at a live-looking value, and this script has no standing to pick.
    The caller refuses to write.
    """
    produced: dict[int, set[int]] = {}
    for _name, old, new in rows:
        produced.setdefault(old, set()).add(new)
    preserved: list[tuple[int, int, str, str]] = []
    conflicts: list[tuple[int, int, int, str]] = []
    for old, new, label in current:
        if new in produced.get(old, ()):
            continue
        if old in produced:
            conflicts.append((old, new, sorted(produced[old])[0], label))
            continue
        if any(old == kept_old and new == kept_new for kept_old, kept_new, _l, _r in preserved):
            continue
        reason = "unmapped" if old in declared else "undeclared"
        preserved.append((old, new, label, reason))
    return preserved, conflicts


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


def render(rows, preserved=()) -> str:
    head = [
        "# 1.16.2 RVA\t1.17 RVA\tconstant",
        "# GENERATED -- DO NOT HAND-EDIT THE BODY OF THIS FILE.",
        "# scripts/select-needed-1170-rows.py --refresh rewrites everything below WHOLESALE",
        "# from rva-map-1162-to-1170.functions.tsv. A row typed in by hand is reproduced only",
        "# if functions.tsv already carries the same pair; anything else is moved to the",
        "# HAND-CARRIED block at the foot of the file and reported on every run. Nothing here",
        "# is deleted silently, but a hand-derived pair belongs in the curated ledger next door:",
        "# rva-map-1162-to-1170.verified.tsv, which only verify-rva-map-1170.py --tsv writes and",
        "# which carries forward every row that run did not itself produce.",
        "#",
        "# Selected by scripts/select-needed-1170-rows.py from",
        "# rva-map-1162-to-1170.functions.tsv -- the subset this workspace names in a",
        "# `const *_RVA` declaration. Pairs come from masked-signature identity across",
        "# .pdata, which is weaker evidence than the byte comparison behind",
        "# rva-map-1162-to-1170.verified.tsv; rows that map already covers are omitted",
        "# here so the stronger evidence wins. Good enough to CALL. Before DETOURING one,",
        "# run scripts/audit-1170-hook-targets.py: signature identity does not check that",
        "# the destination has room for MinHook's five-byte patch.",
    ]
    body = [f"0x{old:x}\t0x{new:x}\t{name}" for name, old, new in rows]
    tail: list[str] = []
    if preserved:
        tail = [
            "#",
            f"{HAND_BANNER} -- {len(preserved)} row(s) this selection cannot reproduce.",
            "# Kept because a wholesale regeneration that drops a hand-derived pair does it with",
            "# exit 0 and no diagnostic, and the loss reads afterwards as an address that was",
            "# never mapped. These are read by er-game-base/build.rs exactly like the rows above.",
            "# `unmapped`   -- a crates/ constant names this address, functions.tsv has no pair",
            "#                 for it. Short .pdata records and body-changed functions live here.",
            "# `undeclared` -- no crates/ declaration names it any more. Nothing consumes it;",
            "#                 delete the line unless you know which unscanned spelling wants it.",
            "# To remove one: delete its line. --refresh will not put it back. If functions.tsv",
            "# later disagrees with a pair here, this script refuses to write until you settle it.",
        ]
        tail += [
            f"0x{old:x}\t0x{new:x}\t{label}\t{reason}"
            for old, new, label, reason in sorted(preserved)
        ]
    return "\n".join(head + body + tail) + "\n"


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

        # THE WHOLESALE-REGENERATION GUARD. Asserted on a fabricated table rather than on the
        # tracked file, so it keeps failing after the real file's hand rows are all absorbed. The
        # two `unmapped` addresses are the pair a merge agent nearly typed into needed.tsv on
        # 2026-08-30 -- both declared under crates/, neither present in functions.tsv, and both
        # measured vanishing from a scratch copy at exit 0 before this guard existed.
        fake_rows = [("REPRODUCED_RVA", 0x111111, 0x222222)]
        fake_current = [
            (0x111111, 0x222222, "REPRODUCED_RVA"),  # regeneration produces it: not preserved
            (0x764290, 0x7650E0, "MENU_CONTINUE_IDLE_INSERT_CALLER_FN_RVA"),  # declared, unmapped
            (0x8C47C0, 0x8C5960, "UPDATE_RVA"),  # declared, unmapped
            (0x333333, 0x444444, "GONE_RVA"),  # nothing declares it any more
        ]
        kept, clash = unreproduced(fake_current, fake_rows, {0x764290, 0x8C47C0, 0x111111})
        if {old for old, _n, _l, _r in kept} != {0x764290, 0x8C47C0, 0x333333}:
            failures.append(f"a hand-added row would be dropped silently again: kept {kept}")
        if dict((old, reason) for old, _n, _l, reason in kept).get(0x333333) != "undeclared":
            failures.append("an undeclared preserved row is not reported as such")
        if clash:
            failures.append(f"agreeing rows were reported as conflicts: {clash}")
        # A pair the file and the function map disagree about must stop the write, not be merged.
        _kept2, clash2 = unreproduced([(0x111111, 0x999999, "REPRODUCED_RVA")], fake_rows, set())
        if [c[:3] for c in clash2] != [(0x111111, 0x999999, 0x222222)]:
            failures.append("a contradicting pair did not raise a conflict")
        # ... and the preserved rows must survive a write/read round trip, or the guard preserves
        # them into a file the next run cannot see them in.
        round_trip = body_rows_from_text(render(fake_rows, kept))
        if not {(0x764290, 0x7650E0), (0x8C47C0, 0x8C5960)} <= {(o, n) for o, n, _l in round_trip}:
            failures.append("preserved rows do not survive render() -> body_rows()")
        verdicts = exhaustive_verdicts(args.repo)
        if "IDENTICAL-WHOLE" not in verdicts:
            failures.append(f"EXHAUSTIVE_VERDICTS no longer parses out of build.rs: {verdicts}")

        for line in failures:
            print(f"SELFTEST FAIL: {line}")
        print(f"selftest: {len(names)} constants, {len(rows)} selected, {len(failures)} failure(s)")
        return 1 if failures else 0

    rows, missing = select(args.repo)
    target = args.repo / OUTPUT
    declared = set(declared_rvas(args.repo).values())
    preserved, conflicts = unreproduced(body_rows(target), rows, declared)

    # Printed on EVERY run, refresh or check, before anything is written. The whole defect this
    # guards against is a deletion nobody saw, so the set is never summarised away to a count.
    for old, new, label, reason in sorted(preserved):
        print(f"  hand-carried: 0x{old:x} -> 0x{new:x}  {label}  [{reason}]")
    if preserved:
        print(
            f"{len(preserved)} row(s) this selection cannot reproduce were kept under "
            f"'{HAND_BANNER}'. Delete a line there to drop it; --refresh will not restore it."
        )
    for old, new, other, label in sorted(conflicts):
        print(
            f"CONFLICT: 0x{old:x} -> 0x{new:x} in {target.name} ({label}), "
            f"but functions.tsv now pairs it with 0x{other:x}"
        )
    if conflicts:
        print(
            f"REFUSING to write {target.name}: {len(conflicts)} row(s) disagree with the "
            "function map. One of the two addresses is wrong and reads as live either way -- "
            "er-game-base/build.rs subtracts a refuted pair from BOTH maps for the same reason. "
            "Settle it by hand: delete the row to accept the function map, or correct "
            "functions.tsv."
        )
        return 1

    overlap = verified_covered(args.repo / VERIFIED, exhaustive_verdicts(args.repo))
    both = [old for _n, old, _new in rows if old in overlap]
    if both:
        print(
            f"note: {len(both)} selected row(s) are ALSO covered by {Path(VERIFIED).name} with a "
            "whole-body verdict. verified_rvas() still filters on the literal string 'IDENTICAL', "
            "which that file no longer writes, so the 'verified map wins' rule is not being "
            "applied here; build.rs re-applies it by address. Narrowing this selection changes "
            "the ledger's contents and wants its own commit."
        )

    text = render(rows, preserved)
    if args.refresh:
        target.write_text(text, encoding="utf-8")
        print(
            f"wrote {target} ({len(rows)} rows, {len(preserved)} hand-carried); "
            f"{len(missing)} constant(s) still unmapped"
        )
        return 0
    current = target.read_text(encoding="utf-8") if target.is_file() else ""
    if current == text:
        print(f"OK: {target.name} is current ({len(rows)} rows, {len(missing)} still unmapped)")
        return 0
    print(f"FAIL: {target.name} is out of date. Re-run with --refresh.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
