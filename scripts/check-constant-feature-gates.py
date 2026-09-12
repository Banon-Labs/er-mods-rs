#!/usr/bin/env python3
"""A feature flag with one possible value is not a flag. Delete the feature.

`title_05_000_strip_default_enabled` was a `pub(crate) fn ... -> bool { false }` carrying
twenty-two lines of doc explaining why it was off. Nothing could turn it on: the body was a
literal. So the strip, its runtime cache, its swap function, its eight telemetry counters and
its eight oracle fields were all reachable only from a branch that could never be taken, and
the comment was the only part of the feature still doing anything. The user's instruction on
2026-09-12, verbatim: "If title_05_000_strip_default_enabled is only used to disable
features...delete the feature!!!!! ... I never want to see it again".

# What this refuses

A function whose name ends in `_enabled` or `_armed` and whose whole body is the literal `true`
or `false`. That shape says "this is a decision" while making no decision, and it is how a
deleted feature keeps its call sites, its constants and its telemetry alive in the tree.

Two things it deliberately leaves alone, because they are different patterns wearing similar
clothes:

* a host-seam default (`default_gate_off`, `default_seamless`, ...). Those are the neutral value
  of a function pointer a host overrides at install time -- the constant is the seam's documented
  "no host supplied one" answer, not a flag;
* a `#[cfg]`-selected stub (`keyboard_edge` on a non-windows build). The constant is one arm of a
  real choice the compiler makes.

Neither ends in `_enabled`/`_armed`, so the name rule separates them without an allowlist.

# The ratchet

`scripts/constant-feature-gates.baseline.json` records what exists today, per file, by name.
The count may only go DOWN: a file may not gain one, and a file that loses one must record that
too, so no file reads as owing more than it does. Zero is the target and bd er-effects-rs-rxgr
tracks getting there; until then this stops the population growing.

Usage:
  python3 scripts/check-constant-feature-gates.py
  python3 scripts/check-constant-feature-gates.py --selftest
  python3 scripts/check-constant-feature-gates.py --update
"""

from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = os.path.join(ROOT, "scripts", "constant-feature-gates.baseline.json")

# `fn NAME(...) -> bool {` followed by nothing but `true` or `false`. `[^)]*` is enough for the
# argument list because a gate that takes a closure or a nested generic is not this shape.
CONSTANT_BOOL_FN = re.compile(
    r"\bfn\s+(?P<name>\w+)\s*\([^)]*\)\s*->\s*bool\s*\{\s*(?P<value>true|false)\s*\}",
    re.S,
)

# The names that claim to be a decision. A seam default is spelled `default_*` and is not one.
GATE_SUFFIXES = ("_enabled", "_armed")

# Non-vacuity floors: a scan that suddenly reads no files has broken, not passed.
MIN_SOURCE_FILES = 400


def is_gate_name(name: str) -> bool:
    return name.endswith(GATE_SUFFIXES) and not name.startswith("default_")


def rust_sources(root: str = ROOT) -> list[str]:
    found = []
    for base, dirs, files in os.walk(os.path.join(root, "crates")):
        dirs[:] = [d for d in dirs if d not in ("target", ".git")]
        for name in files:
            if name.endswith(".rs"):
                found.append(os.path.relpath(os.path.join(base, name), root))
    return sorted(found)


def scan(root: str = ROOT, sources: list[str] | None = None) -> dict[str, list[str]]:
    """Map each file to the sorted names of its constant-valued gates."""
    found: dict[str, list[str]] = {}
    for rel in sources if sources is not None else rust_sources(root):
        try:
            with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            continue
        names = sorted(
            m.group("name") for m in CONSTANT_BOOL_FN.finditer(text) if is_gate_name(m.group("name"))
        )
        if names:
            found[rel] = names
    return found


def load_baseline() -> dict[str, list[str]]:
    try:
        with open(BASELINE, encoding="utf-8") as handle:
            return json.load(handle)["gates"]
    except (OSError, KeyError, json.JSONDecodeError):
        return {}


def write_baseline(found: dict[str, list[str]]) -> None:
    total = sum(len(v) for v in found.values())
    payload = {
        "note": (
            "Constant-valued feature gates still in the tree. The ratchet is DOWN-ONLY: a file "
            "may not gain one, and a file that loses one must be recorded here in the same "
            "commit. Zero is the target -- each entry is a feature to delete, not to document."
        ),
        "total": total,
        "gates": {k: v for k, v in sorted(found.items())},
    }
    with open(BASELINE, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False)
        handle.write("\n")
    print(f"wrote {os.path.relpath(BASELINE, ROOT)} ({total} gate(s) in {len(found)} file(s))")


def compare(found: dict[str, list[str]], baseline: dict[str, list[str]]) -> list[str]:
    problems = []
    for path in sorted(set(found) | set(baseline)):
        now = set(found.get(path, []))
        was = set(baseline.get(path, []))
        gained = sorted(now - was)
        lost = sorted(was - now)
        if gained:
            problems.append(
                f"{path}: new constant-valued gate(s) {', '.join(gained)} -- a `-> bool` body "
                f"that is a literal is a feature with one value. Delete the feature and its call "
                f"sites, or make the body an actual decision."
            )
        if lost:
            problems.append(
                f"{path}: {', '.join(lost)} is gone -- lower the baseline in this commit so the "
                f"file does not read as owing more than it does (--update writes it)."
            )
    return problems


def selftest() -> int:
    failures = []

    def check(label, got, want):
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    fixture = {
        "a.rs": "pub(crate) fn thing_enabled() -> bool {\n    false\n}\n",
        "b.rs": "fn other_armed() -> bool { true }\n",
        "c.rs": "fn default_gate_off() -> bool {\n    false\n}\n",
        "d.rs": "fn keyboard_edge() -> bool {\n    false\n}\n",
        "e.rs": "fn real_enabled() -> bool {\n    !disabled() && ready()\n}\n",
        "f.rs": "fn spaced_enabled() -> bool {\n\n    true\n\n}\n",
    }

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        for name, body in fixture.items():
            with open(os.path.join(tmp, name), "w", encoding="utf-8") as handle:
                handle.write(body)
        found = scan(tmp, sources=sorted(fixture))

    check("a bare `false` gate is caught", found.get("a.rs"), ["thing_enabled"])
    check("an `_armed` gate on one line is caught", found.get("b.rs"), ["other_armed"])
    check("a host-seam default is not a gate", "c.rs" in found, False)
    check("a cfg stub is not a gate", "d.rs" in found, False)
    check("a gate with a real body is not caught", "e.rs" in found, False)
    check("blank lines inside the body do not hide it", found.get("f.rs"), ["spaced_enabled"])

    # The comparison, both directions.
    check(
        "a gained gate fails",
        bool(compare({"x.rs": ["new_enabled"]}, {})),
        True,
    )
    check(
        "a removed gate fails until the baseline follows",
        bool(compare({}, {"x.rs": ["old_enabled"]})),
        True,
    )
    check("an unchanged tree passes", compare({"x.rs": ["a_enabled"]}, {"x.rs": ["a_enabled"]}), [])

    # Non-vacuity against the real tree: the scanner must actually read this repository.
    sources = rust_sources()
    check(
        f"the real scan reads at least {MIN_SOURCE_FILES} rust files",
        len(sources) >= MIN_SOURCE_FILES,
        True,
    )
    # And the rule must still separate the two populations in the live tree, or the name rule has
    # stopped doing any work.
    live = scan()
    check("the live tree has gates the baseline tracks", bool(live), True)

    for failure in failures:
        print(f"check-constant-feature-gates selftest FAILED -- {failure}", file=sys.stderr)
    if failures:
        return 1
    print(
        f"check-constant-feature-gates selftest: OK (6 shape cases, 3 ratchet cases, "
        f"{len(sources)} live sources, {sum(len(v) for v in live.values())} tracked gate(s))"
    )
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    found = scan()
    if "--update" in sys.argv:
        write_baseline(found)
        return 0
    problems = compare(found, load_baseline())
    if problems:
        print("[check-constant-feature-gates] FAIL:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    total = sum(len(v) for v in found.values())
    print(
        f"[check-constant-feature-gates] ok -- {total} constant-valued gate(s) in "
        f"{len(found)} file(s), all recorded; none gained"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
