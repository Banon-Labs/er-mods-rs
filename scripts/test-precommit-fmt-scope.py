#!/usr/bin/env python3
"""Does scripts/hooks/pre-commit's format check judge the staged files, and only those?

The hook used to run `cargo fmt --all -- --check` over the working tree, so another session's
untracked crate or unstaged edit blocked every unrelated commit, and the guard forbids
`--no-verify` (2026-10-02). It now pipes each staged blob through rustfmt. This drives the real
hook in a throwaway repository and asserts both directions:

  * an untracked misformatted crate, and an unstaged misformatted edit, do not block a commit
    of well-formatted staged files;
  * a staged misformatted file still blocks, judged by its staged bytes rather than the
    working tree (a fixed working copy does not rescue a bad staged blob, and a bad working
    copy does not condemn a good one);
  * an `include!` fragment, which `cargo fmt` never formats, is not checked either.

The other gates the hook runs are replaced by stubs that pass, so a verdict here depends on
the format check alone.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "scripts" / "hooks" / "pre-commit"

GOOD_LIB = 'include!("frag.rs");\n\nmod other;\n\npub fn one() -> u32 {\n    1\n}\n'
BAD_LIB = "include!(\"frag.rs\");\n\nmod other;\n\npub fn one()->u32{1}\n"
GOOD_OTHER = "pub fn two() -> u32 {\n    2\n}\n"
BAD_OTHER = "pub fn two()->u32{2}\n"
GOOD_FRAG = "pub const FRAG: u32 = 3;\n"
BAD_FRAG = "pub const   FRAG:u32=3;\n"

MEMBER_TOML = '[package]\nname = "{name}"\nversion = "0.1.0"\nedition = "2024"\n\n[lib]\npath = "src/lib.rs"\n'


def clean_env(root: Path) -> dict[str, str]:
    """The caller's environment minus every `GIT_*` variable, pointed at the throwaway repo.

    Run from inside a hook, git exports `GIT_DIR` and `GIT_INDEX_FILE` for the real checkout;
    inherited, they would aim every command below at it.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_DIR"] = str(root / ".git")
    env["GIT_WORK_TREE"] = str(root)
    return env


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, timeout=25, env=clean_env(root))


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_repo(base: Path) -> Path:
    root = base / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "test")
    write(root, "Cargo.toml", '[workspace]\nmembers = ["a"]\nresolver = "3"\n')
    write(root, "a/Cargo.toml", MEMBER_TOML.replace("{name}", "a"))
    write(root, "a/src/lib.rs", GOOD_LIB)
    write(root, "a/src/other.rs", GOOD_OTHER)
    write(root, "a/src/frag.rs", GOOD_FRAG)
    for gate in ("check-marker-file-gates.py", "check-env-gate-comments.py"):
        write(root, f"scripts/{gate}", "raise SystemExit(0)\n")
    (root / "scripts" / "hooks").mkdir(parents=True)
    shutil.copyfile(HOOK, root / "scripts" / "hooks" / "pre-commit")
    git(root, "add", "-A")
    git(root, "commit", "-q", "--no-verify", "-m", "base")
    return root


def run_hook(root: Path) -> tuple[int, str]:
    proc = subprocess.run(
        ["bash", str(root / "scripts" / "hooks" / "pre-commit")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=25,
        env=clean_env(root),
    )
    return proc.returncode, proc.stdout + proc.stderr


def reset(root: Path) -> None:
    git(root, "reset", "-q", "--hard", "HEAD")
    git(root, "clean", "-qfdx")


def case(root: Path, name: str, want_block: bool, setup) -> bool:
    reset(root)
    setup(root)
    code, out = run_hook(root)
    blocked = code != 0
    ok = blocked == want_block
    verdict = "blocked" if blocked else "passed"
    print(f"{'ok  ' if ok else 'FAIL'} {name}: hook {verdict} (exit {code})")
    if not ok:
        print("    " + out.replace("\n", "\n    "))
    return ok


def stage_good_change(root: Path) -> None:
    write(root, "a/src/lib.rs", GOOD_LIB + "\npub fn three() -> u32 {\n    3\n}\n")
    git(root, "add", "a/src/lib.rs")


def untracked_bad_crate(root: Path) -> None:
    # The working tree lists the in-progress crate as a member, as an uncommitted Cargo.toml
    # edit would, so `cargo fmt --all` reaches it.
    write(root, "Cargo.toml", '[workspace]\nmembers = ["a", "b"]\nresolver = "3"\n')
    write(root, "b/Cargo.toml", MEMBER_TOML.replace("{name}", "b"))
    write(root, "b/src/lib.rs", "pub fn b()->u32{0}\n")
    stage_good_change(root)


def unstaged_bad_edit(root: Path) -> None:
    write(root, "a/src/other.rs", BAD_OTHER)
    stage_good_change(root)


def staged_bad_file(root: Path) -> None:
    write(root, "a/src/lib.rs", BAD_LIB)
    git(root, "add", "a/src/lib.rs")


def staged_bad_working_copy_fixed(root: Path) -> None:
    staged_bad_file(root)
    write(root, "a/src/lib.rs", GOOD_LIB)


def staged_good_working_copy_bad(root: Path) -> None:
    stage_good_change(root)
    write(root, "a/src/lib.rs", BAD_LIB)


def staged_bad_include_fragment(root: Path) -> None:
    write(root, "a/src/frag.rs", BAD_FRAG)
    git(root, "add", "a/src/frag.rs")


def staged_bad_new_module(root: Path) -> None:
    write(root, "a/src/lib.rs", GOOD_LIB + "\nmod fresh;\n")
    write(root, "a/src/fresh.rs", "pub fn f()->u32{4}\n")
    git(root, "add", "a/src/lib.rs", "a/src/fresh.rs")


def main() -> int:
    # `--hook <path>` runs the cases against another copy of the hook, which is how this test
    # is shown to fail: against the pre-2026-10-02 whole-workspace hook, the first case blocks.
    global HOOK
    if len(sys.argv) == 3 and sys.argv[1] == "--hook":
        HOOK = Path(sys.argv[2]).resolve()
    for tool in ("cargo", "rustfmt", "git"):
        if shutil.which(tool) is None:
            print(f"test-precommit-fmt-scope: {tool} not on PATH", file=sys.stderr)
            return 1
    with tempfile.TemporaryDirectory(prefix="precommit-fmt-scope-") as tmp:
        root = make_repo(Path(tmp))
        results = [
            case(root, "untracked misformatted crate does not block", False, untracked_bad_crate),
            case(root, "unstaged misformatted edit does not block", False, unstaged_bad_edit),
            case(root, "staged misformatted file blocks", True, staged_bad_file),
            case(root, "staged bad blob blocks though working copy is fixed", True, staged_bad_working_copy_fixed),
            case(root, "staged good blob passes though working copy is bad", False, staged_good_working_copy_bad),
            case(root, "include! fragment is not checked, as cargo fmt does not", False, staged_bad_include_fragment),
            case(root, "staged misformatted new module blocks", True, staged_bad_new_module),
        ]
    passed = sum(results)
    print(f"test-precommit-fmt-scope: {passed}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
