#!/usr/bin/env python3
"""Assemble the thing a player downloads: one executable, carrying everything it installs.

Layout, and there is deliberately nothing else in it:

    er-mods-<commit>/
      er-installer.exe      run this on Windows
      er-installer          run this on Linux
      README.txt

Why the mods are not files beside it
------------------------------------
A player finds a release page and downloads the installer. They do not clone the repo, and
plenty of them will not unpack a folder either -- they run the executable. An installer that
reads a `dlls` directory beside itself works on the machine that built it and fails on every
machine that matters, so the DLLs are compiled into both binaries by `tools/er-installer/
build.rs` and written out from there. 33 MB of payload against a 50 GB game.

Both hosts, one download
------------------------
me3 runs natively on Linux, where the game is a Proton process -- so the mods stay PE for
everyone and only the installer differs. The Linux build is an ELF from the host toolchain, the
Windows one a PE from cargo-xwin, and the zip records the executable bit on the ELF because
Python does not do that by default and a downloaded installer nobody can run is a download that
failed quietly.

The binary is asked, not assumed
--------------------------------
`er-installer --selfcheck` exits non-zero unless the executable carries every mod its catalog
offers. This runs it against the actual ELF being packaged rather than trusting that the build
command was given the right environment -- the failure it exists to catch is a release built
without `ER_INSTALLER_EMBED_DIR`, which is a perfectly good 440 KB binary that can install
nothing. The Windows build cannot be run here, so it is checked by size against the ELF.

What may never go in, checked per file rather than trusted
----------------------------------------------------------
`SeamlessCoop/ersc.dll` is another author's work and this repo does not copy, stage, archive or
release it -- profiles reference it where the game installed it. Save files are the user's.
Both are refused by name and by suffix on every file added.

Usage:
    python3 scripts/build-installer-release.py
    python3 scripts/build-installer-release.py --out-dir target/deliverables
    python3 scripts/build-installer-release.py --selftest

Build the payload first; this packages, it does not compile:
    scripts/er-build-dlls.sh --all
    ER_INSTALLER_EMBED_DIR=$PWD/target/x86_64-pc-windows-msvc/release \\
        cargo xwin build --release --target x86_64-pc-windows-msvc -p er-installer
    ER_INSTALLER_EMBED_DIR=$PWD/target/x86_64-pc-windows-msvc/release \\
        cargo build --release -p er-installer

That variable must be an absolute path. `tools/er-installer/build.rs` runs with cargo's own working
directory, which is the crate directory and not the workspace root, so a relative path resolves
against `tools/er-installer/` and finds nothing there. The build then fails with a list of DLL
names under "Build them first" -- which reads as a missing payload and is really a mislaid one,
because the payload it is looking past is sitting in the workspace `target/`. Measured 2026-09-23:
the relative spelling this block used to print failed both host builds while every DLL it named
was on disk, and the packaging step then shipped the previous run's binaries without complaint.
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

# Refused by name. `ersc.dll` is Seamless Co-op; the rest are save containers.
FORBIDDEN_NAMES = {"ersc.dll", "ER0000.sl2", "ER0000.co2"}
# Refused by suffix, so a renamed save cannot get through the name check.
FORBIDDEN_SUFFIXES = {".sl2", ".co2", ".bak"}

# `rwxr-xr-x` in the high half of a zip entry's external attributes, which is where Info-ZIP and
# every Linux unzip look for a Unix mode. Without it the ELF unpacks unreadable as a program.
EXECUTABLE_ZIP_ATTR = (0o100755 & 0xFFFF) << 16

# The Windows build cannot be run on this host, so its payload is checked by size against the
# ELF's. The two differ in runtime and in linker, not in the 33 MB they both carry, so anything
# above half is carrying it and a payload-free build is under two percent.
MIN_EXE_RATIO = 0.5

SELFCHECK_TIMEOUT_SECONDS = 25


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
  2. Windows: double-click er-installer.exe
     Linux:   run ./er-installer      (same program; me3 runs natively on Linux)
  3. Move with the arrow keys, space to tick, enter to install.

Double-clicking it opens a window that stays open until you press a key, so you can read what
it did. Run from a terminal instead and it does not wait.

This one file is everything. The mods are inside it -- there is nothing else to download and
no folder to keep it next to. The mods themselves are Windows DLLs on both systems, because on
Linux the game runs under Proton; only the installer differs.

It finds the game through Steam's own records: where the registry says Steam is, every library
in libraryfolders.vdf, and Elden Ring's install record in the library that has it. If none of
that finds the game, it asks you to type or paste the folder -- pasting one copied with
Explorer's "Copy as path" works, quotation marks and all. You can also name it up front:

    er-installer.exe --game-dir "C:\\...\\steamapps\\common\\ELDEN RING\\Game"
    ./er-installer --game-dir "$HOME/.local/share/Steam/steamapps/common/ELDEN RING/Game"

Some pairs of mods destroy each other when loaded together -- usually by one of them silently
doing nothing rather than by crashing. The installer knows which pairs those are and refuses
them as you tick, telling you which one to drop. `er-installer --list` prints the whole list up
front, mods and conflicting pairs both.

Picking nothing is a supported answer: it writes a profile that loads no mods at all.

SETTINGS
Several of these mods read a settings file of their own from the game folder. After the mods
are installed you are walked through those settings, one file at a time: the keys, what each
one does in the words its author wrote, and what it is set to now. Enter changes the
highlighted one, `n` moves to the next file, `q` stops asking.

A file that is already in your game folder is never replaced -- only the keys you actually
change are touched, and every comment in it stays. A mod with no file yet gets the one that mod
itself would write, comments and all, so it is there to read and edit before you first launch.

  --keep-configs     touch no settings file at all. This is the one for reinstalling the mods
                     after an update: every setting stays exactly as it is, including the ones
                     you changed from inside the game.
  --default-configs  ask nothing. Write the file for a mod that has none, leave the rest alone.
  --configure        ask, on a run that would not have -- one that chose its mods with
                     --select or --defaults.

OTHER OPTIONS
  --dry-run       show the profile it would write, and touch nothing
  --defaults      install the recommended set without the picker
  --plain         a numbered list instead of the full-screen picker
  --no-seamless   leave Seamless Co-op out of the profile
  --selfcheck     confirm this copy carries every mod it offers

SEAMLESS CO-OP
Not included here, and never will be -- it is someone else's mod. If you have it installed, the
profile references it where it already is.

WHERE THINGS GO
  <game>/er-mods/            the mods you chose
  <game>/er-mods/er-mods.me3 the profile
  <game>/er-*.toml           the settings files, named when the installer finishes

Built from commit {commit}.
"""


def verify_self_contained(elf: Path, expected: int) -> None:
    """Ask the binary itself whether it carries every mod. Refuse the release if not."""
    try:
        finished = subprocess.run(
            [str(elf), "--selfcheck"],
            capture_output=True,
            text=True,
            timeout=SELFCHECK_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        raise SystemExit(f"could not run {elf} --selfcheck: {err}") from err
    if finished.returncode != 0:
        raise SystemExit(
            f"{elf} is not self-contained, so a player downloading it alone would get an "
            f"installer that can install nothing:\n\n{finished.stdout}{finished.stderr}"
        )
    if str(expected) not in finished.stdout:
        raise SystemExit(
            f"{elf} --selfcheck passed but does not mention {expected} mods -- the catalog and "
            f"the payload disagree:\n{finished.stdout}"
        )


def verify_exe_payload(exe: Path, elf: Path) -> None:
    """The Windows build cannot be run here, so check it is carrying the same payload by size."""
    exe_size = exe.stat().st_size
    floor = int(elf.stat().st_size * MIN_EXE_RATIO)
    if exe_size < floor:
        raise SystemExit(
            f"{exe} is {exe_size:,} bytes against the Linux build's "
            f"{elf.stat().st_size:,}. It was almost certainly built without "
            "ER_INSTALLER_EMBED_DIR, which produces a working installer that carries no mods."
        )


def stage(out_dir: Path, source: Path, host_source: Path, commit: str) -> tuple[Path, list[str]]:
    """Copy the payload into `out_dir/<name>`, returning that directory and its file list."""
    exe = source / INSTALLER_EXE
    elf = host_source / INSTALLER_ELF
    missing = [str(path) for path in (exe, elf) if not path.is_file()]
    if missing:
        raise SystemExit(
            "missing from the build tree:\n"
            + "".join(f"  {item}\n" for item in missing)
            + "\nBuild the payload first (the embed dir must be absolute -- build.rs runs in the\n"
            "crate directory, so a relative path resolves against tools/er-installer/):\n"
            "  scripts/er-build-dlls.sh --all\n"
            "  ER_INSTALLER_EMBED_DIR=$PWD/target/x86_64-pc-windows-msvc/release \\\n"
            "      cargo xwin build --release --target x86_64-pc-windows-msvc -p er-installer\n"
            "  ER_INSTALLER_EMBED_DIR=$PWD/target/x86_64-pc-windows-msvc/release \\\n"
            "      cargo build --release -p er-installer"
        )

    verify_self_contained(elf, len(shipped_artifacts()))
    verify_exe_payload(exe, elf)

    root = out_dir / f"er-mods-{commit}"
    root.mkdir(parents=True, exist_ok=True)
    staged: list[str] = []
    for name, built in ((INSTALLER_EXE, exe), (INSTALLER_ELF, elf)):
        reason = forbidden_reason(name)
        if reason:
            raise SystemExit(f"refusing to package: {reason}")
        destination = root / name
        destination.write_bytes(built.read_bytes())
        destination.chmod(0o755)
        staged.append(name)
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
    import tempfile

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

    with tempfile.TemporaryDirectory(prefix="er-installer-release-") as tmp:
        tmp = Path(tmp)
        source, host_source, out_dir = tmp / "win", tmp / "host", tmp / "out"
        source.mkdir()
        host_source.mkdir()

        # Nothing built at all must refuse and name both binaries.
        try:
            stage(out_dir, source, host_source, "selftest")
        except SystemExit as refusal:
            if INSTALLER_ELF not in str(refusal) or INSTALLER_EXE not in str(refusal):
                print(f"SELFTEST FAIL: refusal did not name both builds: {refusal}")
                failures += 1
        else:
            print("SELFTEST FAIL: an empty build tree was packaged anyway")
            failures += 1

        # A binary that fails its own self-check must not be shippable.
        failing = host_source / INSTALLER_ELF
        failing.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        failing.chmod(0o755)
        (source / INSTALLER_EXE).write_bytes(b"stub")
        try:
            stage(out_dir, source, host_source, "selftest")
        except SystemExit as refusal:
            if "self-contained" not in str(refusal):
                print(f"SELFTEST FAIL: a failed selfcheck gave the wrong refusal: {refusal}")
                failures += 1
        else:
            print("SELFTEST FAIL: a binary carrying no mods was packaged anyway")
            failures += 1

        # A passing self-check, but a Windows build far too small to hold the payload.
        passing = host_source / INSTALLER_ELF
        passing.write_text(
            f"#!/bin/sh\necho 'self-contained -- all {len(wanted)} mods are built in'\n",
            encoding="utf-8",
        )
        passing.chmod(0o755)
        # Pad the ELF so the ratio check has something to measure against.
        with passing.open("ab") as handle:
            handle.write(b"#" * 200_000)
        try:
            stage(out_dir, source, host_source, "selftest")
        except SystemExit as refusal:
            if "ER_INSTALLER_EMBED_DIR" not in str(refusal):
                print(f"SELFTEST FAIL: a tiny exe gave the wrong refusal: {refusal}")
                failures += 1
        else:
            print("SELFTEST FAIL: a payload-free Windows build was packaged anyway")
            failures += 1

        # Both sound: it stages two binaries and a readme, and nothing else.
        (source / INSTALLER_EXE).write_bytes(b"#" * 200_000)
        root, staged = stage(out_dir, source, host_source, "selftest")
        if sorted(staged) != sorted([INSTALLER_EXE, INSTALLER_ELF, "README.txt"]):
            print(f"SELFTEST FAIL: unexpected payload {staged}")
            failures += 1
        if any(path.is_dir() for path in root.iterdir()):
            print("SELFTEST FAIL: the download has a folder in it; it should be flat")
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

    if failures:
        print(f"selftest: {failures} case(s) failed")
        return 1
    print(f"selftest: {len(cases) + 7} cases passed, {len(wanted)} mods expected in a release")
    return 0


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

    print(f"staged {len(staged)} file(s) in {root}")
    for name in staged:
        size = (root / name).stat().st_size
        print(f"  {name:<20} {size:>12,} bytes")
    print(f"zip    {archive} ({archive.stat().st_size:,} bytes)")
    print(f"sha256 {hashlib.sha256(archive.read_bytes()).hexdigest()}")
    print(
        "\nEither binary can be uploaded on its own -- each carries every mod it offers, "
        "confirmed by running --selfcheck against the Linux build."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
