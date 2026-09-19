#!/usr/bin/env python3
"""Assemble the thing a player downloads: the installer, and the DLLs it installs.

Layout, which is also what `install::find_dll_source` looks for:

    er-mods-<commit>/
      er-installer.exe      run this on Windows
      er-installer          run this on Linux
      README.txt
      dlls/
        er_quickload.dll
        ... one per shipped shell

Both hosts, one download
------------------------
me3 runs natively on Linux, where the game is a Proton process -- so the natives stay PE for
everyone and only the installer differs. The Linux build is an ELF from the host toolchain,
the Windows one a PE from cargo-xwin, and the zip records the executable bit on the ELF
because Python does not do that by default and a downloaded installer nobody can run is a
download that failed quietly.

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
    cargo build --release -p er-installer
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
HOST_TARGET_DIR = REPO_ROOT / "target" / "release"
DEFAULT_OUT_DIR = REPO_ROOT / "target" / "deliverables"
INSTALLER_EXE = "er-installer.exe"
INSTALLER_ELF = "er-installer"

# `rwxr-xr-x` in the high half of a zip entry's external attributes, which is where Info-ZIP and
# every Linux unzip look for a Unix mode. Without it the ELF unpacks unreadable as a program.
EXECUTABLE_ZIP_ATTR = (0o100755 & 0xFFFF) << 16

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
  2. Windows: run er-installer.exe
     Linux:   run ./er-installer      (same program; me3 runs natively on Linux)
  3. Tick what you want and press a.

The mods themselves are Windows DLLs on both systems -- on Linux the game is a Proton
process, so only the installer differs.

It finds the game on its own when Steam is somewhere usual. If it cannot, pass the folder
holding eldenring.exe:

    er-installer.exe --game-dir "C:\\...\\steamapps\\common\\ELDEN RING\\Game"
    ./er-installer --game-dir "$HOME/.local/share/Steam/steamapps/common/ELDEN RING/Game"

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


def stage(out_dir: Path, source: Path, host_source: Path, commit: str) -> tuple[Path, list[str]]:
    """Copy the payload into `out_dir/<name>`, returning that directory and its file list."""
    name = f"er-mods-{commit}"
    root = out_dir / name
    dll_dir = root / "dlls"
    dll_dir.mkdir(parents=True, exist_ok=True)

    wanted = shipped_artifacts()
    missing = [f"{source}/{artifact}" for artifact in wanted if not (source / artifact).is_file()]
    exe = source / INSTALLER_EXE
    elf = host_source / INSTALLER_ELF
    if not exe.is_file():
        missing.append(str(exe))
    # The Linux installer is not optional. me3 runs natively on Linux, and shipping only the
    # Windows build would leave every Linux player with a zip they cannot start.
    if not elf.is_file():
        missing.append(str(elf))
    if missing:
        raise SystemExit(
            "missing from the build tree:\n"
            + "".join(f"  {item}\n" for item in missing)
            + "\nBuild the payload first:\n"
            "  scripts/er-build-dlls.sh --all\n"
            "  cargo xwin build --release --target x86_64-pc-windows-msvc -p er-installer\n"
            "  cargo build --release -p er-installer"
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
    linux_installer = root / INSTALLER_ELF
    linux_installer.write_bytes(elf.read_bytes())
    linux_installer.chmod(0o755)
    staged.append(INSTALLER_ELF)
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
            arcname = str(Path(root.name) / relative)
            info = zipfile.ZipInfo.from_file(path, arcname)
            info.compress_type = zipfile.ZIP_DEFLATED
            if path.name == INSTALLER_ELF:
                info.external_attr = EXECUTABLE_ZIP_ATTR
            zf.writestr(info, path.read_bytes())
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

    failures += _selftest_package(wanted)

    if failures:
        print(f"selftest: {failures} case(s) failed")
        return 1
    print(f"selftest: {len(cases) + 4} cases passed, {len(wanted)} artifacts in the payload")
    return 0


def _selftest_package(wanted: list[str]) -> int:
    """Stage and zip a payload of stubs, and check what a player would actually unpack."""
    import tempfile

    failures = 0
    with tempfile.TemporaryDirectory(prefix="er-installer-release-") as tmp:
        tmp = Path(tmp)
        source, host_source, out_dir = tmp / "win", tmp / "host", tmp / "out"
        source.mkdir()
        host_source.mkdir()
        for artifact in wanted:
            (source / artifact).write_bytes(b"stub")
        (source / INSTALLER_EXE).write_bytes(b"stub")

        # The Linux build absent must refuse, not quietly ship a Windows-only zip.
        try:
            stage(out_dir, source, host_source, "selftest")
        except SystemExit as refusal:
            if INSTALLER_ELF not in str(refusal):
                print(f"SELFTEST FAIL: refusal did not name the missing Linux build: {refusal}")
                failures += 1
        else:
            print("SELFTEST FAIL: a missing Linux installer was packaged anyway")
            failures += 1

        (host_source / INSTALLER_ELF).write_bytes(b"stub")
        root, staged = stage(out_dir, source, host_source, "selftest")
        if INSTALLER_ELF not in staged or INSTALLER_EXE not in staged:
            print(f"SELFTEST FAIL: both installers should be staged, got {staged[-3:]}")
            failures += 1

        archive = write_zip(root, out_dir)
        with zipfile.ZipFile(archive) as zf:
            entry = next(
                (i for i in zf.infolist() if i.filename.endswith(f"/{INSTALLER_ELF}")), None
            )
            if entry is None:
                print("SELFTEST FAIL: the Linux installer is not in the zip")
                failures += 1
            elif not (entry.external_attr >> 16) & 0o111:
                print(
                    "SELFTEST FAIL: the Linux installer unpacks without its executable bit "
                    f"(mode {(entry.external_attr >> 16):o})"
                )
                failures += 1
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--source", type=Path, default=TARGET_DIR)
    parser.add_argument("--host-source", type=Path, default=HOST_TARGET_DIR)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    commit = git_commit()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    root, staged = stage(args.out_dir, args.source, args.host_source, commit)
    archive = write_zip(root, args.out_dir)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()

    print(f"staged {len(staged)} file(s) in {root}")
    print(f"zip    {archive}")
    print(f"sha256 {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
