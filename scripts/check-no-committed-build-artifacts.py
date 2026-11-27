#!/usr/bin/env python3
"""Fail if any build output is tracked in git, or untracked and not ignored (the next `git add -A`).

Why this exists. `.gitignore` opened with a root-anchored `/target`, which ignores
only the workspace's own build directory. A cargo project nested anywhere else --
`scripts/oodle-dcx-probe/` was the first -- has its `target/` completely unignored,
so `git add -A` sweeps the whole build in. That is exactly how 19 files and 6.5 MB
of `.exe`/`.pdb`/fingerprint state reached main, and nothing in `check.sh` looked.

The ignore rule is fixed (`target/`, unanchored, matches at any depth), but an
ignore rule only stops files that are not already tracked: git keeps updating a
tracked path even when a later rule would ignore it. So this check is the part that
actually holds -- it reads the index, not the working tree.

Two independent rules, because either alone has a blind spot:
  1. no tracked path may sit under a directory named `target`
  2. no tracked path may carry a binary build-output extension, wherever it lives

Rule 2 catches a stray `foo.pdb` committed outside any `target/`; rule 1 catches
fingerprint/lock files that have no telling extension.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Compiler/linker output. Deliberately not `.bin` or `.dcx` -- this repo has
# legitimate reasons to discuss those, and game assets are covered by their own rule
# in AGENTS.md.
BINARY_SUFFIXES = {".exe", ".pdb", ".dll", ".so", ".dylib", ".a", ".lib", ".obj", ".o", ".rlib"}

# A directory named `target` is not enough: a crate may legitimately be called
# `target` (`crates/target/src/lib.rs` is source, not build output). What identifies
# a cargo build directory is what sits directly inside it, so require that.
BUILD_DIR_CHILDREN = {"debug", "release", "CACHEDIR.TAG", ".rustc_info.json", "package", "doc"}
TARGET_TRIPLE_MARKERS = ("-pc-windows-", "-unknown-linux-", "-apple-darwin", "-unknown-none")

# Paths allowed to keep a build-output extension. Keep this empty unless there is a
# real reason; every entry is a hole in the rule.
ALLOWLIST: set[str] = set()


def _is_cargo_build_path(parts: list[str]) -> bool:
    """True when `parts` descends through a cargo build directory.

    Matches `…/target/release/…`, `…/target/CACHEDIR.TAG` and
    `…/target/<triple>/release/…`; does not match `crates/target/src/lib.rs`.
    """
    for index, part in enumerate(parts[:-1]):
        if part != "target" or index + 1 >= len(parts):
            continue
        child = parts[index + 1]
        if child in BUILD_DIR_CHILDREN:
            return True
        if any(marker in child for marker in TARGET_TRIPLE_MARKERS):
            return True
    return False


def tracked_files(repo_root: Path) -> list[str]:
    """Tracked paths plus untracked ones git does not ignore -- what `git add -A` would sweep in.

    The index alone let a new, unstaged file pass locally and fail CI once committed (measured
    2026-11-25 on the comment-caps gate, which selected its files the same way)."""
    out = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=True,
        # Required by scripts/check-no-timeouts.py: every subprocess gets an explicit
        # bound <= 30s so a mistake fails fast instead of hanging the gate.
        timeout=30,
    ).stdout
    # An unmerged path is listed once per stage; the dict keeps the first and the order.
    return [p for p in dict.fromkeys(out.split("\0")) if p]


def violations(paths: list[str]) -> tuple[list[str], list[str]]:
    in_build_dir = []
    binary = []
    for path in paths:
        if path in ALLOWLIST:
            continue
        parts = path.split("/")
        if _is_cargo_build_path(parts):
            in_build_dir.append(path)
            continue
        if Path(path).suffix.lower() in BINARY_SUFFIXES:
            binary.append(path)
    return in_build_dir, binary


def selftest() -> None:
    build, binary = violations(
        [
            "scripts/oodle-dcx-probe/target/release/foo.exe",
            "scripts/oodle-dcx-probe/target/CACHEDIR.TAG",
            "scripts/oodle-dcx-probe/target/x86_64-pc-windows-msvc/release/a.pdb",
            "crates/er-hook/src/lib.rs",
            "docs/notes.md",
            "tools/prebuilt/helper.pdb",
            "src/target_resolver.rs",
            "crates/target/src/lib.rs",
        ]
    )
    assert build == [
        "scripts/oodle-dcx-probe/target/release/foo.exe",
        "scripts/oodle-dcx-probe/target/CACHEDIR.TAG",
        "scripts/oodle-dcx-probe/target/x86_64-pc-windows-msvc/release/a.pdb",
    ], build
    # Must not match: `target_resolver.rs` is a substring, not a path component, and
    # `crates/target/` is a crate named target whose child is `src` -- source, not a
    # build directory. Both were live false positives in this checker's first draft.
    assert binary == ["tools/prebuilt/helper.pdb"], binary

    # An untracked build output git does not ignore is what `git add -A` sweeps in next, so it is
    # reported; an ignored one is not.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        git = ["git", "-C", tmp, "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(git + ["init", "-q"], check=True, timeout=30)
        (root / ".gitignore").write_text("ignored.dll\n", encoding="utf-8")
        (root / "src.rs").write_text("fn main() {}\n", encoding="utf-8")
        subprocess.run(git + ["add", ".gitignore", "src.rs"], check=True, timeout=30)
        (root / "stray.exe").write_bytes(b"MZ")
        (root / "ignored.dll").write_bytes(b"MZ")
        build, binary = violations(tracked_files(root))
        assert (build, binary) == ([], ["stray.exe"]), (build, binary)

    print(
        "[check-no-committed-build-artifacts] selftest ok "
        "(9 cases: profile dir, CACHEDIR, triple dir, source, doc, stray binary, substring, crate-named-target, "
        "untracked binary)"
    )


def main() -> int:
    if "--selftest" in sys.argv:
        selftest()
        return 0

    repo_root = Path(__file__).resolve().parent.parent
    build, binary = violations(tracked_files(repo_root))
    if not build and not binary:
        return 0

    print(
        "[check-no-committed-build-artifacts] FAIL: build output is tracked in git, "
        "or untracked and not ignored."
    )
    if build:
        print(f"\n  {len(build)} file(s) under a `target/` directory:")
        for path in build[:20]:
            print(f"    {path}")
        if len(build) > 20:
            print(f"    ... and {len(build) - 20} more")
    if binary:
        print(f"\n  {len(binary)} file(s) with a build-output extension:")
        for path in binary[:20]:
            print(f"    {path}")
    print(
        "\nUntrack a tracked one (this keeps your local copy):\n"
        "    git rm -r --cached <path>\n"
        "and delete or ignore an untracked one.\n"
        "and make sure .gitignore has an UNANCHORED rule -- `target/`, not `/target` --\n"
        "or a nested cargo project's build dir stays unignored."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
