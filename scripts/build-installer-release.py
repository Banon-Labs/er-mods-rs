#!/usr/bin/env python3
"""Assemble the thing a player downloads: the installer, and the DLLs it installs.

Layout, which is also what `install::find_dll_source` looks for:

    er-mods-<commit>/
      er-installer.exe      run this
      README.txt
      dlls/
        er_quickload.dll
        ... one per shipped shell

The zip is refused rather than shipped incomplete. A download missing one DLL produces an
installer that offers a mod and then cannot install it, which is a worse failure than not
having built the zip: the user has already chosen it by then.

What may never go in, checked per file rather than trusted
----------------------------------------------------------
`SeamlessCoop/ersc.dll` is another author's work and this repo does not copy, stage, archive
or release it -- profiles reference it where the game installed it. Save files are the user's.
Both are refused by name and by suffix on every file added, so a future edit that widens the
glob cannot quietly include one.

Usage:
    python3 scripts/build-installer-release.py
    python3 scripts/build-installer-release.py --out-dir target/deliverables
    python3 scripts/build-installer-release.py --selftest

Build the payload first; this packages, it does not compile:
    scripts/er-build-dlls.sh --all
    cargo xwin build --release --target x86_64-pc-windows-msvc -p er-installer
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DLL_LIST = REPO_ROOT / "scripts" / "me3-dll-list.py"
TARGET_DIR = REPO_ROOT / "target" / "x86_64-pc-windows-msvc" / "release"
DEFAULT_OUT_DIR = REPO_ROOT / "target" / "deliverables"
INSTALLER_EXE = "er-installer.exe"

# Refused by name. `ersc.dll` is Seamless Co-op; the rest are save containers.
FORBIDDEN_NAMES = {"ersc.dll", "ER0000.sl2", "ER0000.co2"}
# Refused by suffix, so a renamed save cannot get through the name check.
FORBIDDEN_SUFFIXES = {".sl2", ".co2", ".bak"}


def shipped_artifacts() -> list[str]:
    spec = importlib.util.spec_from_file_location("me3_dll_list", DLL_LIST)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return [f"{artifact}.dll" for _package, artifact in module.dll_pairs()]


def forbidden_reason(name: str) -> str | None:
    """Why this file may not be shipped, or None if it may."""
    if name in FORBIDDEN_NAMES:
        return (
            f"{name} is never bundled by this repo -- it belongs to its own author or to the "
            "user, and a profile references it where it already lives."
        )
    if Path(name).suffix.lower() in FORBIDDEN_SUFFIXES:
        return f"{name} looks like a save file or a backup, which is the user's, not ours."
    return None


def git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=25,
        ).strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return "unknown"


README = """\
Elden Ring mods -- pick what you want, and this writes the me3 profile for them.

WHAT TO DO
  1. Install me3 if you have not: https://github.com/garyttierney/me3
  2. Run er-installer.exe.
  3. Tick what you want and press a.

It finds the game on its own when Steam is somewhere usual. If it cannot, pass the folder
holding eldenring.exe:

    er-installer.exe --game-dir "C:\\...\\steamapps\\common\\ELDEN RING\\Game"

Some pairs of mods destroy each other when loaded together -- usually by one of them silently
doing nothing rather than by crashing. The installer knows which pairs those are and refuses
them as you tick, telling you which one to drop. `er-installer.exe --list` prints the whole
list up front, mods and conflicting pairs both.

Picking nothing is a supported answer: it writes a profile that loads no mods at all.

SEAMLESS CO-OP
Not included here, and never will be -- it is someone else's mod. If you have it installed,
the profile references it where it already is. `--no-seamless` leaves it out.

WHERE THINGS GO
  <game>/er-mods/            the DLLs you chose
  <game>/er-mods/er-mods.me3 the profile
Some mods read a settings file from the game folder; the installer names them when it finishes.

Built from commit {commit}.
"""


def stage(out_dir: Path, source: Path, commit: str) -> tuple[Path, list[str]]:
    """Copy the payload into `out_dir/<name>`, returning that directory and its file list."""
    name = f"er-mods-{commit}"
    root = out_dir / name
    dll_dir = root / "dlls"
    dll_dir.mkdir(parents=True, exist_ok=True)

    wanted = shipped_artifacts()
    missing = [artifact for artifact in wanted if not (source / artifact).is_file()]
    exe = source / INSTALLER_EXE
    if not exe.is_file():
        missing.append(INSTALLER_EXE)
    if missing:
        raise SystemExit(
            f"not in {source}:\n"
            + "".join(f"  {item}\n" for item in missing)
            + "\nBuild the payload first:\n"
            "  scripts/er-build-dlls.sh --all\n"
            "  cargo xwin build --release --target x86_64-pc-windows-msvc -p er-installer"
        )

    staged: list[str] = []
    for artifact in wanted:
        reason = forbidden_reason(artifact)
        if reason:
            raise SystemExit(f"refusing to package: {reason}")
        (dll_dir / artifact).write_bytes((source / artifact).read_bytes())
        staged.append(f"dlls/{artifact}")

    (root / INSTALLER_EXE).write_bytes(exe.read_bytes())
    staged.append(INSTALLER_EXE)
    (root / "README.txt").write_text(README.format(commit=commit), encoding="utf-8")
    staged.append("README.txt")
    return root, staged


def write_zip(root: Path, out_dir: Path) -> Path:
    archive = out_dir / f"{root.name}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            reason = forbidden_reason(path.name)
            if reason:
                raise SystemExit(f"refusing to package: {reason}")
            zf.write(path, str(Path(root.name) / relative))
    return archive


def selftest() -> int:
    failures = 0
    cases = [
        ("ersc.dll", True, "Seamless Co-op is refused by name"),
        ("ER0000.sl2", True, "a save container is refused by name"),
        ("MySave.co2", True, "a renamed save is refused by suffix"),
        ("anything.BAK", True, "a backup is refused case-insensitively"),
        ("er_quickload.dll", False, "one of our own DLLs is allowed"),
        ("er-installer.exe", False, "the installer is allowed"),
    ]
    for name, should_refuse, description in cases:
        refused = forbidden_reason(name) is not None
        if refused != should_refuse:
            print(f"SELFTEST FAIL {description}: {name} refused={refused}")
            failures += 1

    wanted = shipped_artifacts()
    if not wanted:
        print("SELFTEST FAIL: the shipped artifact list came back empty")
        failures += 1
    for artifact in wanted:
        if forbidden_reason(artifact):
            print(f"SELFTEST FAIL: a shipped artifact is on the refusal list: {artifact}")
            failures += 1

    if failures:
        print(f"selftest: {failures} case(s) failed")
        return 1
    print(f"selftest: {len(cases) + 1} cases passed, {len(wanted)} artifacts in the payload")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--source", type=Path, default=TARGET_DIR)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    commit = git_commit()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    root, staged = stage(args.out_dir, args.source, commit)
    archive = write_zip(root, args.out_dir)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()

    print(f"staged {len(staged)} file(s) in {root}")
    print(f"zip    {archive}")
    print(f"sha256 {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
