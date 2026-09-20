#!/usr/bin/env python3
"""Two DLLs that arm the Quit tab and offer the same flow must be declared a conflict.

The rule is read off the table rather than invented. Every `[[conflict]]` pair among the row
shells shares at least one `QuitRowActions` flow, and until 2026-09-19 four pairs that shared
one were missing -- which is how an installer came to offer `Quickload` and `Load Character
rows only` as a tickable combination.

Why a shared flow is the condition
----------------------------------
`row_registry::elect` fixed the row table in 2026-09-13: the second host to arm delegates
through the owner's `er_quit_rows_register` export and one merged table results. What it did
not fix, and cannot, is that each cdylib links its own copy of the core's statics.

Run br-20260917-205031-edd3 measured the consequence. The owner answered the product's
registration `open_profile_load_dialog=offered-but-already-held`, and the product's activate
hook then declined because `flow_active` is a latch in the product's copy, set only when the
product opens the picker -- the shell had opened it in its own. The row looked armed, the press
forwarded to the game, and the player got an in-world load instead of the save-safe switch. No
crash, no logged error, the wrong load.

So the hazard needs two hosts offering the same flow. Shells whose rows and flows are disjoint
(`er-quit-menu` X `er-save-game-row`, `er-quit-load-character` X `er-save-game-row`) are
deliberately not conflicts, and this gate must not demand that they be.

What this catches that the conflict gate cannot
-----------------------------------------------
`check-me3-dll-conflicts.py` proves every shipped shell is classified. It cannot know that a
new pair became hazardous because someone added a `save_game_start_flow: Some(..)` to a shell
that did not have one. That is a source fact, so it is read from source here on every run.

Usage:
    python3 scripts/check-quit-row-flow-overlap.py
    python3 scripts/check-quit-row-flow-overlap.py --selftest

Exit status is 1 on any failure, so this can gate.
"""

from __future__ import annotations

import argparse
import itertools
import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CRATES_DIR = REPO_ROOT / "crates"
CONFLICTS_TOML = REPO_ROOT / "scripts" / "me3-dll-conflicts.toml"

# What makes a crate a row-arming host. A crate that only links `er-quit-menu-core` without
# arming -- er-input-harness does -- is not one, which is why this looks for the call.
#
# There are two entry points, and the first cut of this gate knew only one. It reported three
# arming shells when there are five: `er-quit-menu` and `er-quit-load-character` reach the row
# cloner through `arm::arm_standalone`, so both were invisible and so were the pairs they form.
# A gate that under-detects passes while the thing it guards is broken, which is worse than not
# having it -- hence `MIN_ARMING_SHELLS` below.
ARM_CALLS = (
    re.compile(r"\brow_cloner::arm\s*\("),
    re.compile(r"\barm::arm_standalone\s*\("),
)

# The five shells known to arm as of 2026-09-19. A scanner that finds fewer has stopped
# matching something rather than found a simpler workspace, and says so instead of passing.
# Raise this when a new row shell lands; lowering it is only correct alongside a deleted crate.
MIN_ARMING_SHELLS = 5

# Every field of `QuitRowActions` a host can fill. A flow supplied by two hosts is the hazard;
# the names are matched as `<flow>: Some(` so a `None` placeholder does not count as offering it.
FLOW_FIELDS = (
    "open_profile_load_dialog",
    "open_save_picker_menu",
    "save_game_start_flow",
    "save_game_request_slot",
    "open_build_url_import",
    "generate_build_link",
)


def crate_sources(package: str) -> list[Path]:
    return sorted((CRATES_DIR / package / "src").rglob("*.rs"))


def row_arming_shells(shipped: set[str]) -> dict[str, set[str]]:
    """package -> the flows it supplies, for every shipped shell that arms the Quit rows."""
    hosts: dict[str, set[str]] = {}
    for package in sorted(shipped):
        source_dir = CRATES_DIR / package / "src"
        if not source_dir.is_dir():
            continue
        text = "\n".join(
            path.read_text(encoding="utf-8", errors="replace") for path in crate_sources(package)
        )
        if not any(pattern.search(text) for pattern in ARM_CALLS):
            continue
        flows = {
            field for field in FLOW_FIELDS if re.search(rf"\b{field}\s*:\s*Some\s*\(", text)
        }
        hosts[package] = flows
    return hosts


def declared_pairs(table: dict) -> set[frozenset[str]]:
    return {frozenset((row["a"], row["b"])) for row in table.get("conflict", [])}


def check(hosts: dict[str, set[str]], declared: set[frozenset[str]]) -> list[str]:
    problems: list[str] = []
    for a, b in itertools.combinations(sorted(hosts), 2):
        shared = hosts[a] & hosts[b]
        pair = frozenset((a, b))
        if shared and pair not in declared:
            problems.append(
                f"{a} and {b} both arm the Quit rows and both supply "
                f"{', '.join(sorted(shared))}. Two hosts offering one flow is the shape measured "
                "on br-20260917-205031-edd3: the loser's registration comes back "
                "`offered-but-already-held` and its activate hook reads a latch the winner never "
                "set, so the row looks armed and silently does the wrong thing. Declare the pair "
                "in scripts/me3-dll-conflicts.toml, or make the flows disjoint."
            )
    return problems


def selftest() -> int:
    failures = 0
    cases: list[tuple[str, dict[str, set[str]], set[frozenset[str]], bool]] = [
        (
            "a shared flow with no declaration is caught",
            {"a": {"open_profile_load_dialog"}, "b": {"open_profile_load_dialog"}},
            set(),
            True,
        ),
        (
            "a shared flow that is declared is accepted",
            {"a": {"open_profile_load_dialog"}, "b": {"open_profile_load_dialog"}},
            {frozenset(("a", "b"))},
            False,
        ),
        (
            "disjoint flows need no declaration",
            {"a": {"open_profile_load_dialog"}, "b": {"save_game_start_flow"}},
            set(),
            False,
        ),
        (
            "a host supplying nothing collides with nobody",
            {"a": set(), "b": set()},
            set(),
            False,
        ),
        (
            "one undeclared pair among three hosts is caught",
            {
                "a": {"save_game_start_flow"},
                "b": {"save_game_start_flow"},
                "c": {"open_profile_load_dialog"},
            },
            set(),
            True,
        ),
    ]
    for name, hosts, declared, should_fail in cases:
        problems = check(hosts, declared)
        if bool(problems) != should_fail:
            print(f"SELFTEST FAIL {name}: problems={problems}")
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

    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "me3_dll_list", REPO_ROOT / "scripts" / "me3-dll-list.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    shipped = {package for package, _artifact in module.dll_pairs()}

    hosts = row_arming_shells(shipped)
    if len(hosts) < MIN_ARMING_SHELLS:
        print(
            f"found {len(hosts)} row-arming shell(s) ({', '.join(sorted(hosts)) or 'none'}) but "
            f"expected at least {MIN_ARMING_SHELLS}. Either a crate was deleted, or this scanner "
            "stopped matching how one of them arms -- and an under-detecting scanner passes while "
            "the pairs it cannot see go unclassified. Fix the patterns in ARM_CALLS, or lower "
            "MIN_ARMING_SHELLS alongside the deletion.",
            file=sys.stderr,
        )
        return 1

    table = tomllib.loads(CONFLICTS_TOML.read_text(encoding="utf-8"))
    problems = check(hosts, declared_pairs(table))
    if problems:
        print(f"{len(problems)} undeclared row-flow overlap(s):\n", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}\n", file=sys.stderr)
        return 1

    pairs = sum(1 for a, b in itertools.combinations(sorted(hosts), 2) if hosts[a] & hosts[b])
    print(
        f"quit-row flows: {len(hosts)} arming shell(s), {pairs} pair(s) share a flow and all "
        "are declared conflicts."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
