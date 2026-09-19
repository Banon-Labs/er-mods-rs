#!/usr/bin/env python3
"""Prove `scripts/me3-dll-catalog.toml` can actually drive a picker.

The catalog is the only place a shipped DLL gets a name a player can read. A shell with no
entry is not merely undocumented -- it is shipped and unreachable, because the installer
builds its rows from this file and nothing else. So coverage is checked in both directions.

The rule worth having is the last one. A picker's first run offers a set of ticked boxes,
and if that set cannot be loaded together the very first profile a user produces is corrupt
before the game starts. That set is computable here, from the same `[[conflict]]` table the
installer consults, so it is checked here rather than discovered by a player.

Six assertions:

1. **Coverage, both ways.** Every package in `me3-dll-list.py` has exactly one catalog
   entry, and every catalog entry names a shipped package. A rename breaks the build rather
   than dropping a row.
2. **Fields.** `label`, `blurb`, `category`, `audience` and `default` on every entry;
   `category` and `audience` from fixed sets; no unknown keys, because a misspelled field
   is a setting that silently does nothing.
3. **Copy that fits a row.** A non-empty label under 48 characters, a non-empty blurb under
   201, and no two entries sharing a label -- two identical rows is a picker that cannot be
   used, which is how these rows got renamed in the first place (AGENTS.md, 2026-07-31).
4. **Diagnostics are never ticked.** `audience = "diagnostic"` implies `default = false`.
   These drive input or install trace detours; a player gets one by asking for it.
5. **Consent agrees with the conflict table.** A package in that table's `[opt_in_only]`
   has already been found to change params or drive input by mere presence. It may not be
   ticked here. Two tables disagreeing about consent is the bug.
6. **The default set loads.** No two `default = true` packages appear as a `[[conflict]]`
   pair. `[[shared]]` pairs are fine and deliberately not checked: they are the declared,
   mechanism-backed sharing that licenses two DLLs to detour one prologue.

Usage:
    python3 scripts/check-me3-dll-catalog.py
    python3 scripts/check-me3-dll-catalog.py --selftest

Exit status is 1 on any failure, so this can gate.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG_TOML = REPO_ROOT / "scripts" / "me3-dll-catalog.toml"
CONFLICTS_TOML = REPO_ROOT / "scripts" / "me3-dll-conflicts.toml"
DLL_LIST = REPO_ROOT / "scripts" / "me3-dll-list.py"

VALID_CATEGORIES = {
    "menus-and-saves",
    "quality-of-life",
    "multiplayer",
    "cosmetic",
    "diagnostics",
}

# `player` is a mod someone installs to play with. `diagnostic` drives input, installs trace
# detours, or writes telemetry nobody reads back -- shipped so a bug report can carry evidence,
# never ticked by default.
VALID_AUDIENCES = {"player", "diagnostic"}

REQUIRED_FIELDS = {"label", "blurb", "category", "audience", "default"}
OPTIONAL_FIELDS = {"needs_seamless", "config"}

MAX_LABEL = 48
MAX_BLURB = 200


def shipped_packages() -> list[str]:
    spec = importlib.util.spec_from_file_location("me3_dll_list", DLL_LIST)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return [package for package, _artifact in module.dll_pairs()]


def conflict_pairs(table: dict) -> list[tuple[str, str]]:
    return [(row["a"], row["b"]) for row in table.get("conflict", [])]


def check(
    catalog: dict,
    conflicts: dict,
    shipped: list[str],
) -> list[str]:
    """Return a list of failures; empty means the catalog is sound."""
    problems: list[str] = []
    shipped_set = set(shipped)

    missing = shipped_set - set(catalog)
    for package in sorted(missing):
        problems.append(
            f"{package}: shipped as an ME3 DLL but has no entry in me3-dll-catalog.toml. "
            "A shell with no catalog entry cannot appear in the installer at all."
        )
    for package in sorted(set(catalog) - shipped_set):
        problems.append(
            f"{package}: has a catalog entry but is not a shipped shell. Either the crate was "
            "renamed or removed, or it is missing from the me3_shells array."
        )

    labels: dict[str, str] = {}
    for package in sorted(set(catalog) & shipped_set):
        entry = catalog[package]
        if not isinstance(entry, dict):
            problems.append(f"{package}: catalog entry is not a table.")
            continue

        for field in sorted(REQUIRED_FIELDS - set(entry)):
            problems.append(f"{package}: missing required field `{field}`.")
        for field in sorted(set(entry) - REQUIRED_FIELDS - OPTIONAL_FIELDS):
            problems.append(
                f"{package}: unknown field `{field}`. A misspelled field is a setting that "
                "silently does nothing."
            )

        label = entry.get("label", "")
        if not isinstance(label, str) or not label.strip():
            problems.append(f"{package}: `label` must be a non-empty string.")
        elif len(label) > MAX_LABEL:
            problems.append(
                f"{package}: label is {len(label)} characters, over the {MAX_LABEL} a picker "
                "row can show."
            )
        elif label in labels:
            problems.append(
                f"{package}: label {label!r} is already used by {labels[label]}. Two rows "
                "reading the same thing cannot be told apart."
            )
        else:
            labels[label] = package

        blurb = entry.get("blurb", "")
        if not isinstance(blurb, str) or not blurb.strip():
            problems.append(f"{package}: `blurb` must be a non-empty string.")
        elif len(blurb) > MAX_BLURB:
            problems.append(
                f"{package}: blurb is {len(blurb)} characters, over the {MAX_BLURB} limit."
            )

        category = entry.get("category")
        if category not in VALID_CATEGORIES:
            problems.append(
                f"{package}: category {category!r} is not one of "
                f"{sorted(VALID_CATEGORIES)}."
            )

        audience = entry.get("audience")
        if audience not in VALID_AUDIENCES:
            problems.append(
                f"{package}: audience {audience!r} is not one of {sorted(VALID_AUDIENCES)}."
            )

        default = entry.get("default")
        if not isinstance(default, bool):
            problems.append(f"{package}: `default` must be a boolean.")
        elif default and audience == "diagnostic":
            problems.append(
                f"{package}: audience is `diagnostic` but default is true. A harness that "
                "drives input or installs trace detours is ticked by the person who wants it."
            )

        for flag in ("needs_seamless",):
            if flag in entry and not isinstance(entry[flag], bool):
                problems.append(f"{package}: `{flag}` must be a boolean.")
        if "config" in entry and not (
            isinstance(entry["config"], str) and entry["config"].strip()
        ):
            problems.append(f"{package}: `config` must be a non-empty string when present.")

    opt_in_only = set(conflicts.get("opt_in_only", {}))
    for package in sorted(opt_in_only & set(catalog)):
        if catalog[package].get("default") is True:
            problems.append(
                f"{package}: listed in [opt_in_only] in me3-dll-conflicts.toml but ticked by "
                "default here. Those are the two opposite answers to the same consent question."
            )

    ticked = {p for p, e in catalog.items() if isinstance(e, dict) and e.get("default") is True}
    for a, b in conflict_pairs(conflicts):
        if a in ticked and b in ticked:
            problems.append(
                f"{a} and {b} are both ticked by default but are a [[conflict]] pair. The "
                "first profile a user produces would be corrupt before the game starts."
            )

    return problems


def selftest() -> int:
    """Each assertion is proven to fire, not merely to pass against the real data."""
    shipped = ["er-alpha", "er-beta", "er-gamma"]
    base_conflicts: dict = {"conflict": [], "opt_in_only": {}}

    def entry(**overrides):
        base = {
            "label": "Alpha",
            "blurb": "Does a thing.",
            "category": "quality-of-life",
            "audience": "player",
            "default": False,
        }
        base.update(overrides)
        return base

    sound = {
        "er-alpha": entry(),
        "er-beta": entry(label="Beta", default=True),
        "er-gamma": entry(label="Gamma", audience="diagnostic"),
    }
    cases: list[tuple[str, dict, dict, str]] = [
        ("sound catalog passes", sound, base_conflicts, ""),
        (
            "missing entry is caught",
            {k: v for k, v in sound.items() if k != "er-gamma"},
            base_conflicts,
            "has no entry",
        ),
        (
            "stale entry is caught",
            {**sound, "er-removed": entry(label="Removed")},
            base_conflicts,
            "not a shipped shell",
        ),
        (
            "missing field is caught",
            {**sound, "er-alpha": {"label": "Alpha", "blurb": "x", "category": "cosmetic"}},
            base_conflicts,
            "missing required field",
        ),
        (
            "unknown field is caught",
            {**sound, "er-alpha": entry(colour="red")},
            base_conflicts,
            "unknown field",
        ),
        (
            "duplicate label is caught",
            {**sound, "er-beta": entry(label="Alpha", default=True)},
            base_conflicts,
            "already used by",
        ),
        (
            "bad category is caught",
            {**sound, "er-alpha": entry(category="misc")},
            base_conflicts,
            "is not one of",
        ),
        (
            "over-long blurb is caught",
            {**sound, "er-alpha": entry(blurb="x" * (MAX_BLURB + 1))},
            base_conflicts,
            "over the",
        ),
        (
            "ticked diagnostic is caught",
            {**sound, "er-gamma": entry(label="Gamma", audience="diagnostic", default=True)},
            base_conflicts,
            "ticked by the person who wants it",
        ),
        (
            "ticked opt-in-only is caught",
            sound,
            {"conflict": [], "opt_in_only": {"er-beta": "reason"}},
            "opposite answers",
        ),
        (
            "conflicting default set is caught",
            {**sound, "er-alpha": entry(default=True)},
            {"conflict": [{"a": "er-alpha", "b": "er-beta"}], "opt_in_only": {}},
            "corrupt before the game starts",
        ),
    ]

    failures = 0
    for name, catalog, conflicts, expect in cases:
        problems = check(catalog, conflicts, shipped)
        if expect:
            if not any(expect in p for p in problems):
                print(f"SELFTEST FAIL {name}: expected {expect!r}, got {problems}")
                failures += 1
        elif problems:
            print(f"SELFTEST FAIL {name}: expected no problems, got {problems}")
            failures += 1

    if failures:
        print(f"selftest: {failures} case(s) failed")
        return 1
    print(f"selftest: {len(cases)} cases passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    catalog = tomllib.loads(CATALOG_TOML.read_text(encoding="utf-8"))
    conflicts = tomllib.loads(CONFLICTS_TOML.read_text(encoding="utf-8"))
    shipped = shipped_packages()

    problems = check(catalog, conflicts, shipped)
    if problems:
        print(f"{CATALOG_TOML.relative_to(REPO_ROOT)}: {len(problems)} problem(s)\n")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    ticked = sum(1 for e in catalog.values() if e.get("default") is True)
    print(
        f"me3-dll-catalog.toml: {len(catalog)} shells catalogued, "
        f"{ticked} ticked by default, default set is conflict-free."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
