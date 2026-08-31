#!/usr/bin/env python3
"""Reject unapproved String::from_utf8_lossy in Rust source files."""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
IGNORED_DIRECTORIES = {".git", ".worktrees", "target", "third_party"}
LOSSY_CALL = "String::from_utf8_lossy"
JUSTIFICATION_MARKER = "UTF-8 Lossy:"


def rust_source_files() -> list[Path]:
    """Every `.rs` under the repo except the ignored directories.

    The walk PRUNES `IGNORED_DIRECTORIES` as it descends rather than enumerating their contents
    and discarding them afterwards, which is what `rglob` forced. The result is identical by
    construction: a path under an ignored directory has that directory in its relative `.parts`,
    so the old post-filter already threw it away. It is purely the difference between reading
    `.worktrees/` and `target/` and then dropping them, and never reading them at all.

    Measured 2026-08-31 on a warm cache: `REPO_ROOT.rglob("*.rs")` traverses all 1,118,634
    filesystem entries in the tree (`.worktrees` 564,630 + `.claude` 387,965 + `target` 159,386
    are 99.4% of it) to arrive at this list. Under load the same walk was measured at 28-69s.
    """
    paths: list[Path] = []
    for directory, subdirectories, filenames in os.walk(REPO_ROOT):
        subdirectories[:] = [
            name for name in subdirectories if name not in IGNORED_DIRECTORIES
        ]
        base = Path(directory)
        for name in filenames:
            if not name.endswith(".rs"):
                continue
            path = base / name
            if any(part in IGNORED_DIRECTORIES for part in path.relative_to(REPO_ROOT).parts):
                continue
            paths.append(path)
    return sorted(paths)


def has_explicit_justification(lines: list[str], index: int) -> bool:
    current_line = lines[index]
    previous_line = lines[index - 1] if index > 0 else ""
    return JUSTIFICATION_MARKER in current_line or JUSTIFICATION_MARKER in previous_line


def lossy_utf8_findings(path: Path) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    lines = path.read_text(encoding="utf-8").splitlines()

    for index, line in enumerate(lines):
        if LOSSY_CALL not in line:
            continue
        if has_explicit_justification(lines, index):
            continue
        findings.append((index + 1, line.strip()))

    return findings


def main() -> int:
    failures: list[str] = []

    for path in rust_source_files():
        for line_number, line in lossy_utf8_findings(path):
            relative_path = path.relative_to(REPO_ROOT)
            failures.append(f"{relative_path}:{line_number}: {LOSSY_CALL} without explicit justification: {line}")

    if failures:
        print(f"{LOSSY_CALL} is banned without an explicit justification.", file=sys.stderr)
        print(
            f"Prefer strict String::from_utf8(...). If lossy display is explicitly approved, add a nearby comment starting with '// {JUSTIFICATION_MARKER}'.\n",
            file=sys.stderr,
        )
        print("\n".join(failures), file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
