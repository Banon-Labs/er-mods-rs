#!/usr/bin/env python3
"""Fetch dxcompiler.dll for the shader editor's compiler, or check what is there.

The shader editor compiles HLSL in game through `er-shader-compiler.exe`, which loads the
dxcompiler.dll er-cel-shade names on its command line, and er-cel-shade loads the same file itself
only when the helper cannot be run from the game (design `docs/plans/er-shader-editor.md`, 7.5).
er-cel-shade looks for the helper and for the compiler each on its own, beside the DLL and then
in `<Game>/er-shader-editor/compiler/`, so the compiler may be fetched to either while cargo leaves
the helper beside the DLL. Neither imports it, so both run without it; with it, layers compile.

The file is Microsoft's and is never committed or bundled: `LICENSE-MS.txt` in the release zip
says "solely for use on Windows", and what that covers is unresolved (design section 15). So it
is fetched onto this machine from the official release and nothing else:

- the release zip is downloaded from `RELEASE_URL` and refused unless its SHA-256 is
  `RELEASE_SHA256`, the digest GitHub publishes for that asset;
- `bin/x64/dxcompiler.dll` and `LICENSE-LLVM.txt` are taken out of it, each refused unless it
  hashes to its pin, and written to `--dest` through a rename, so a reader never sees half a file;
- nothing else in the zip is written. dxil.dll in particular is never fetched: 1.8.2505 and later
  sign with their internal validator, and the pinned release is 1.9.2609.

`--check` is the gate. It asserts that no copy of dxcompiler.dll, dxil.dll or a DXC release zip
is tracked by git and that `.gitignore` keeps one out wherever it lands, and, when a copy has been
fetched to `--dest`, that it is the pinned file with its licence beside it. Without a fetched copy
it says so and checks the rest.

Usage:
    python3 scripts/fetch-dxcompiler.py                 # fetch to the default --dest
    python3 scripts/fetch-dxcompiler.py --zip <file>    # from a copy of the release zip on disk
    python3 scripts/fetch-dxcompiler.py --dest <dir>    # e.g. <Game>/er-shader-editor/compiler/
    python3 scripts/fetch-dxcompiler.py --check
    python3 scripts/fetch-dxcompiler.py --selftest

The default `--dest` is `target/x86_64-pc-windows-msvc/release/` in this checkout, beside the
`er_cel_shade.dll` and `er-shader-compiler.exe` that `scripts/er-build-dlls.sh er-cel-shade`
builds there. Fetching is skipped when
`--dest` already holds the pinned files. The download is 32 MB with a per-read timeout of
`READ_TIMEOUT_SECONDS`; run it in the background where a shell caps a call at 30 s.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DEST = REPO / "target" / "x86_64-pc-windows-msvc" / "release"

# The release, its asset and the asset's digest, all three read off
# https://api.github.com/repos/microsoft/DirectXShaderCompiler/releases on 2026-10-14. The digest is
# GitHub's own `sha256:` for the asset and matches the zip the shader editor's research measured
# (its dxcompiler.dll reports file version 1.9.2609.5).
RELEASE_TAG = "v1.9.2609"
RELEASE_URL = ("https://github.com/microsoft/DirectXShaderCompiler/releases/download/"
               "v1.9.2609/dxc_2026_09_29.zip")
RELEASE_SHA256 = "ad31b1fc8443175d204f77a611fdb3ef2ec42759bdc2f1167368de24a4a7e7f1"
RELEASE_BYTES = 32_287_592

# What is taken out of the zip, by member name with `/` separators (the zip itself uses `\`), and
# the name and digest it is written under.
DLL_MEMBER = "bin/x64/dxcompiler.dll"
DLL_NAME = "dxcompiler.dll"
DLL_SHA256 = "0689e2fee0a1c9df8662a59680469f24e2a3ad7ce6bbbcc9284f7a83172bfc59"
LICENSE_MEMBER = "LICENSE-LLVM.txt"
LICENSE_NAME = "LICENSE-LLVM.txt"
LICENSE_SHA256 = "729615317e28dd03907e46f0fc3b5e88f7853cee61d1a1471d2749335516b46f"

# Per read from the socket, not for the whole download.
READ_TIMEOUT_SECONDS = 20
GIT_TIMEOUT_SECONDS = 20
CHUNK_BYTES = 1 << 20

# File names that must never be tracked: the compiler, the external validator this design never
# uses, and a DXC release zip. Matched on the base name, ignoring case.
NEVER_TRACKED_NAMES = ("dxcompiler.dll", "dxil.dll")
NEVER_TRACKED_ZIP_PREFIX = "dxc_"

# Paths `.gitignore` must ignore, as `git check-ignore` reads them: the default dest, the
# repository root, and somewhere no other rule covers.
IGNORE_PROBES = (
    "target/x86_64-pc-windows-msvc/release/dxcompiler.dll",
    "dxcompiler.dll",
    "crates/er-cel-shade/dxcompiler.dll",
    "dxil.dll",
    "dxc_2026_09_29.zip",
)


class Refused(Exception):
    """A download, a member or a fetched file that is not the pinned one, and why."""


@dataclass(frozen=True)
class Pins:
    """What the release and the two files taken from it must hash to."""

    release_sha256: str
    members: tuple[tuple[str, str, str], ...]  # (zip member, written name, sha256)


PINS = Pins(
    release_sha256=RELEASE_SHA256,
    members=((DLL_MEMBER, DLL_NAME, DLL_SHA256), (LICENSE_MEMBER, LICENSE_NAME, LICENSE_SHA256)),
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def download(url: str, limit: int) -> bytes:
    """The body at `url`, refused past `limit` bytes so a wrong URL cannot fill the disk."""
    request = urllib.request.Request(url, headers={"User-Agent": "er-mods-rs fetch-dxcompiler"})
    chunks, received = [], 0
    with urllib.request.urlopen(request, timeout=READ_TIMEOUT_SECONDS) as response:
        while chunk := response.read(CHUNK_BYTES):
            received += len(chunk)
            if received > limit:
                raise Refused(f"{url} is more than {limit} bytes; that is not the pinned release")
            chunks.append(chunk)
    return b"".join(chunks)


def extract(archive: bytes, pins: Pins) -> dict[str, bytes]:
    """The pinned members of the release zip by written name, each checked against its pin. The
    zip's own digest is checked before it is opened."""
    found = sha256(archive)
    if found != pins.release_sha256:
        raise Refused(f"the release zip hashes to {found}, not the pinned {pins.release_sha256}")
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        by_name = {str(PurePosixPath(info.filename.replace("\\", "/"))): info
                   for info in zipped.infolist()}
        files = {}
        for member, name, digest in pins.members:
            info = by_name.get(member)
            if info is None:
                raise Refused(f"the release zip has no {member}")
            data = zipped.read(info)
            if sha256(data) != digest:
                raise Refused(f"{member} hashes to {sha256(data)}, not the pinned {digest}")
            files[name] = data
    return files


def install(files: dict[str, bytes], dest: Path) -> None:
    """Write each file into `dest` through a temporary name and a rename."""
    dest.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        partial = dest / f".{name}.partial"
        partial.write_bytes(data)
        os.replace(partial, dest / name)


def problems_at(dest: Path, pins: Pins) -> list[str] | None:
    """What is wrong with the fetched files in `dest`, or None when nothing was fetched there."""
    present = [(name, digest) for _, name, digest in pins.members if (dest / name).is_file()]
    if not present:
        return None
    problems = []
    for _, name, digest in pins.members:
        path = dest / name
        if not path.is_file():
            problems.append(f"{path} is missing beside the others")
        elif sha256(path.read_bytes()) != digest:
            problems.append(f"{path} hashes to {sha256(path.read_bytes())}, not the pinned {digest}")
    return problems


def never_tracked(paths: list[str]) -> list[str]:
    """The paths among `paths` that name a compiler file or a DXC release zip."""
    hits = []
    for path in paths:
        base = PurePosixPath(path).name.lower()
        if base in NEVER_TRACKED_NAMES or (
                base.startswith(NEVER_TRACKED_ZIP_PREFIX) and base.endswith(".zip")):
            hits.append(path)
    return hits


def git(*argv: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", "-C", str(REPO), *argv], capture_output=True,
                          timeout=GIT_TIMEOUT_SECONDS, check=False)


def tracked_paths() -> list[str]:
    proc = git("ls-files", "-z")
    if proc.returncode != 0:
        raise SystemExit("fetch-dxcompiler: git ls-files failed: "
                         f"{proc.stderr.decode(errors='backslashreplace')}")
    return [path.decode("utf-8", errors="surrogateescape")
            for path in proc.stdout.split(b"\0") if path]


def not_ignored(probes: tuple[str, ...]) -> list[str]:
    """The probe paths `.gitignore` does not ignore. `--no-index` asks about the rules alone, so
    a path that does not exist is answered too."""
    misses = []
    for probe in probes:
        proc = git("check-ignore", "-q", "--no-index", probe)
        if proc.returncode == 1:
            misses.append(probe)
        elif proc.returncode != 0:
            raise SystemExit(f"fetch-dxcompiler: git check-ignore failed on {probe}: "
                             f"{proc.stderr.decode(errors='backslashreplace')}")
    return misses


def check(dest: Path) -> int:
    failures = []
    tracked = never_tracked(tracked_paths())
    if tracked:
        failures.append(f"tracked by git, and must never be: {', '.join(tracked)}")
    else:
        print("fetch-dxcompiler: no compiler file or DXC release zip is tracked")
    misses = not_ignored(IGNORE_PROBES)
    if misses:
        failures.append(f".gitignore does not ignore: {', '.join(misses)}")
    else:
        print(f"fetch-dxcompiler: .gitignore keeps a fetched copy out ({len(IGNORE_PROBES)} paths)")
    problems = problems_at(dest, PINS)
    if problems is None:
        print(f"fetch-dxcompiler: nothing fetched at {dest}; no fetched copy to compare")
    elif problems:
        failures.extend(problems)
    else:
        print(f"fetch-dxcompiler: {dest / DLL_NAME} is the pinned {RELEASE_TAG} build")
    for failure in failures:
        print(f"fetch-dxcompiler: FAIL: {failure}", file=sys.stderr)
    return 1 if failures else 0


def fetch(dest: Path, zip_path: Path | None) -> int:
    if problems_at(dest, PINS) == []:
        print(f"fetch-dxcompiler: {dest / DLL_NAME} is already the pinned {RELEASE_TAG} build")
        return 0
    try:
        if zip_path is not None:
            archive = zip_path.read_bytes()
        else:
            print(f"fetch-dxcompiler: downloading {RELEASE_URL}")
            archive = download(RELEASE_URL, RELEASE_BYTES)
        files = extract(archive, PINS)
    except Refused as refusal:
        print(f"fetch-dxcompiler: refused: {refusal}", file=sys.stderr)
        return 1
    install(files, dest)
    for name in files:
        print(f"fetch-dxcompiler: wrote {dest / name}")
    return 0


# ---------------------------------------------------------------------------------------------
# The selftest: synthetic zips with their own pins, written only under a temporary directory.
# ---------------------------------------------------------------------------------------------

def synthetic_zip(members: dict[str, bytes]) -> bytes:
    """A zip holding `members`, named with `\\` the way the DXC release names them."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zipped:
        for name, data in members.items():
            zipped.writestr(name.replace("/", "\\"), data)
    return out.getvalue()


def selftest() -> int:
    failures = []

    def expect(name: str, got, want) -> None:
        if got != want:
            failures.append(f"{name}: got {got!r}, want {want!r}")

    def refused(name: str, call, says: str) -> None:
        try:
            call()
        except Refused as refusal:
            if says not in str(refusal):
                failures.append(f"{name}: refused for {refusal}, want a refusal naming {says!r}")
            return
        failures.append(f"{name}: passed, want a refusal naming {says!r}")

    dll, licence = b"MZ fake compiler", b"NCSA licence text"
    good = synthetic_zip({DLL_MEMBER: dll, LICENSE_MEMBER: licence,
                          "bin/x64/dxil.dll": b"never taken", "LICENSE-MS.txt": b"not taken"})
    pins = Pins(sha256(good), ((DLL_MEMBER, DLL_NAME, sha256(dll)),
                               (LICENSE_MEMBER, LICENSE_NAME, sha256(licence))))

    expect("the pinned members, and nothing else", extract(good, pins),
           {DLL_NAME: dll, LICENSE_NAME: licence})
    refused("a zip other than the pinned one", lambda: extract(good + b"\0", pins), "release zip")
    bad_dll = synthetic_zip({DLL_MEMBER: b"MZ another compiler", LICENSE_MEMBER: licence})
    refused("a member other than the pinned one",
            lambda: extract(bad_dll, Pins(sha256(bad_dll), pins.members)), DLL_MEMBER)
    no_dll = synthetic_zip({LICENSE_MEMBER: licence})
    refused("a zip without the compiler", lambda: extract(no_dll, Pins(sha256(no_dll), pins.members)),
            f"no {DLL_MEMBER}")

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "release"
        expect("nothing fetched yet", problems_at(dest, pins), None)
        install(extract(good, pins), dest)
        expect("what was written", sorted(path.name for path in dest.iterdir()),
               [LICENSE_NAME, DLL_NAME])
        expect("a fresh fetch is current", problems_at(dest, pins), [])
        (dest / DLL_NAME).write_bytes(b"MZ replaced")
        changed = problems_at(dest, pins) or []
        expect("a replaced compiler is reported", len(changed), 1)
        if changed and "not the pinned" not in changed[0]:
            failures.append(f"a replaced compiler: reported as {changed[0]!r}")
        install(extract(good, pins), dest)
        expect("a second fetch replaces it", problems_at(dest, pins), [])
        (dest / LICENSE_NAME).unlink()
        missing = problems_at(dest, pins) or []
        expect("a compiler without its licence is reported", len(missing), 1)
        if missing and "missing" not in missing[0]:
            failures.append(f"a missing licence: reported as {missing[0]!r}")

    expect("never-tracked names", never_tracked([
        "target/x/dxcompiler.dll", "a/Dxcompiler.DLL", "third_party/dxil.dll", "dxc_2026_09_29.zip",
        "docs/dxcompiler.md", "dxcompiler.dll.txt", "crates/x/src/dxc.rs", "scripts/fetch-dxcompiler.py",
    ]), ["target/x/dxcompiler.dll", "a/Dxcompiler.DLL", "third_party/dxil.dll",
         "dxc_2026_09_29.zip"])

    # The real pins are well formed, and the real repository holds to the rules `--check` asserts.
    for name, digest in (("release", RELEASE_SHA256), ("dll", DLL_SHA256), ("licence", LICENSE_SHA256)):
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            failures.append(f"the {name} pin is not a lowercase SHA-256: {digest!r}")
    if not RELEASE_URL.startswith("https://github.com/microsoft/DirectXShaderCompiler/releases/"
                                  f"download/{RELEASE_TAG}/"):
        failures.append(f"the release URL is not the official {RELEASE_TAG} asset: {RELEASE_URL}")
    expect("compiler files tracked in this repository", never_tracked(tracked_paths()), [])
    expect("paths .gitignore lets through", not_ignored(IGNORE_PROBES), [])

    for failure in failures:
        print(f"selftest FAIL: {failure}", file=sys.stderr)
    print("selftest:", "FAIL" if failures else "ok")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST,
                        help=f"where the files go (default {DEFAULT_DEST})")
    parser.add_argument("--zip", type=Path, help="a copy of the release zip already on disk")
    parser.add_argument("--check", action="store_true",
                        help="nothing is tracked or trackable, and a fetched copy is the pinned one")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if args.check:
        return check(args.dest)
    return fetch(args.dest, args.zip)


if __name__ == "__main__":
    sys.exit(main())
