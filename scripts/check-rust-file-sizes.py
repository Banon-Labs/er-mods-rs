#!/usr/bin/env python3
"""Guard against recreating giant Rust source files.

This is intentionally lighter than clippy: it keeps the refactor branch from
backsliding while semantic module extraction continues.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

DEFAULT_WARN_LINES = 900
DEFAULT_FAIL_LINES = 3200
SKIP_DIRS = {
    ".git",
    ".worktrees",
    # `.claude/worktrees` holds transient agent worktree COPIES of the repo (gitignored); scanning them
    # double-counts the real files. `.worktrees` above only matches the dot-prefixed top-level dir, so the
    # part here is the bare `.claude` segment (also covers `.claude/skills` etc. -- none are product source).
    ".claude",
    "target",
    "save-files",
    "docs",
    "third_party",
}


def rust_files(root: Path) -> list[Path]:
    """Every `.rs` under `root` except `SKIP_DIRS`.

    The walk PRUNES `SKIP_DIRS` as it descends instead of enumerating their contents and
    discarding them afterwards, which is what `rglob` forced. Identical by construction: a path
    under a skipped directory carries that directory in `rel_parts`, so the filter below already
    rejected it. Measured 2026-08-31: `rglob` traversed all 1,118,634 entries under the repo root
    -- `.worktrees`, `.claude` and `target` are 99.4% of them -- to produce 571 files.
    """
    files: list[Path] = []
    for directory, subdirectories, filenames in os.walk(root):
        subdirectories[:] = [name for name in subdirectories if name not in SKIP_DIRS]
        base = Path(directory)
        for name in filenames:
            if not name.endswith(".rs"):
                continue
            path = base / name
            if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
                continue
            files.append(path)
    return sorted(files)


def line_count(path: Path) -> int:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return sum(1 for _ in handle)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root (default: inferred from this script)",
    )
    parser.add_argument("--warn-lines", type=int, default=DEFAULT_WARN_LINES)
    parser.add_argument("--fail-lines", type=int, default=DEFAULT_FAIL_LINES)
    args = parser.parse_args()

    if args.warn_lines <= 0 or args.fail_lines <= 0:
        raise SystemExit("line thresholds must be positive")
    if args.warn_lines > args.fail_lines:
        raise SystemExit("--warn-lines must be <= --fail-lines")

    root = args.root.resolve()
    rows = sorted(
        ((line_count(path), path.relative_to(root)) for path in rust_files(root)),
        reverse=True,
    )
    failures = [(lines, path) for lines, path in rows if lines > args.fail_lines]
    warnings = [(lines, path) for lines, path in rows if lines > args.warn_lines]

    print(
        f"checked {len(rows)} Rust files; warn>{args.warn_lines} lines, "
        f"fail>{args.fail_lines} lines"
    )
    if warnings:
        print("largest Rust files:")
        for lines, path in warnings[:25]:
            marker = "FAIL" if lines > args.fail_lines else "warn"
            print(f"  {marker:4s} {lines:5d} {path}")
    if failures:
        print("\nRefactor required: Rust files above the hard size limit remain.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
