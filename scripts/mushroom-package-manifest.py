#!/usr/bin/env python3
"""Compare two Mushroom Man package manifests written by `mushroom-man-builder hash-package`.

    python3 scripts/mushroom-package-manifest.py diff <base.manifest> <new.manifest> [--except <path>]...
    python3 scripts/mushroom-package-manifest.py summary <manifest>
    python3 scripts/mushroom-package-manifest.py --selftest

`hash-package` runs under wine against a built package folder, because only the game's own Oodle
can open its binders. Its manifest has one `file` line per file in the folder (length and sha256
of the bytes on disk) and, under each binder, one `entry` line per file the binder holds (length
and sha256 after decompression).

Parity is decided on file bytes. That was settled once, by measurement, on 2026-10-15: the same
c2280 build run twice into two folders gave 242 byte-identical files, so Kraken through the game's
Oodle is deterministic and a file whose bytes moved is a file whose content moved. The entry lines
are reported only to say which part of a binder changed; a binder whose entries all match but
whose bytes differ is still a difference, and is named as a repack.

`diff` exits 0 when the two packages are identical outside the `--except` paths, 1 when anything
else differs, 2 on a malformed manifest. An `--except` value is a package-relative path or an
fnmatch pattern (`mushroom-man.me3`, `parts/fc_*`); a difference it covers is printed as excepted
and does not fail the comparison, and an exception that matched nothing is printed too, so a
renamed file cannot hide behind a stale one.

`summary` prints how many files the package holds per part family (`fc_m`, `fg_a`, ...), the
counts the approved c2280 layout is described by: fg_a 120, hr_a 74, fc_m 19, fc_f 12, and the
16 naked am/bd/hd/lg binders.

The baseline every c2280 build is compared against is
`docs/recon/dsr-character-builder/c2280-baseline.manifest`.
"""

from __future__ import annotations

import fnmatch
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class Digest:
    length: int
    sha256: str


@dataclass
class PackageFile:
    digest: Digest
    entries: dict[str, Digest] = field(default_factory=dict)


def parse(text: str, source: str = "<manifest>") -> dict[str, PackageFile]:
    files: dict[str, PackageFile] = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip("\r")
        if not line or line.startswith("#"):
            continue
        cols = line.split("\t")

        def digest(length: str, sha: str) -> Digest:
            if not length.isdigit() or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
                raise ManifestError(f"{source}:{number}: bad length or sha256: {line}")
            return Digest(int(length), sha)

        if cols[0] == "file" and len(cols) == 4:
            if cols[1] in files:
                raise ManifestError(f"{source}:{number}: {cols[1]} listed twice")
            files[cols[1]] = PackageFile(digest(cols[2], cols[3]))
        elif cols[0] == "entry" and len(cols) == 5:
            binder = files.get(cols[1])
            if binder is None:
                raise ManifestError(f"{source}:{number}: entry before its file line: {line}")
            if cols[2] in binder.entries:
                raise ManifestError(f"{source}:{number}: entry {cols[2]} listed twice in {cols[1]}")
            binder.entries[cols[2]] = digest(cols[3], cols[4])
        else:
            raise ManifestError(f"{source}:{number}: not a file or entry line: {line}")
    if not files:
        raise ManifestError(f"{source}: no file lines")
    return files


@dataclass
class Comparison:
    """Every difference between two packages, split by whether an exception covers it."""

    failing: list[str] = field(default_factory=list)
    excepted: list[str] = field(default_factory=list)
    unused_exceptions: list[str] = field(default_factory=list)
    identical: int = 0


def entry_detail(base: PackageFile, new: PackageFile) -> list[str]:
    lines = []
    for name in sorted(base.entries.keys() | new.entries.keys()):
        old, now = base.entries.get(name), new.entries.get(name)
        if old == now:
            continue
        if old is None:
            lines.append(f"    entry only in new: {name} ({now.length} bytes)")
        elif now is None:
            lines.append(f"    entry only in base: {name} ({old.length} bytes)")
        else:
            lines.append(f"    entry changed: {name} ({old.length} -> {now.length} bytes)")
    if not lines and base.entries:
        lines.append("    every entry identical: a repack, still a difference on file bytes")
    return lines


def compare(base: dict[str, PackageFile], new: dict[str, PackageFile], exceptions: list[str]) -> Comparison:
    result = Comparison()
    used: set[str] = set()

    def covered(path: str) -> bool:
        hits = [pattern for pattern in exceptions if fnmatch.fnmatchcase(path, pattern)]
        used.update(hits)
        return bool(hits)

    for path in sorted(base.keys() | new.keys()):
        old, now = base.get(path), new.get(path)
        if old is not None and now is not None and old.digest == now.digest:
            result.identical += 1
            continue
        if old is None:
            lines = [f"only in new: {path} ({now.digest.length} bytes)"]
        elif now is None:
            lines = [f"only in base: {path} ({old.digest.length} bytes)"]
        else:
            lines = [f"changed: {path} ({old.digest.length} -> {now.digest.length} bytes)"]
            lines += entry_detail(old, now)
        (result.excepted if covered(path) else result.failing).extend(lines)
    result.unused_exceptions = [pattern for pattern in exceptions if pattern not in used]
    return result


def family(path: str) -> str:
    """`parts/fg_a_0100.partsbnd.dcx` -> `fg_a`; anything outside `parts/` is its own folder."""
    folder, _, name = path.rpartition("/")
    if folder == "parts":
        return "_".join(name.split("_")[:2])
    return folder or "(top level)"


def summary(files: dict[str, PackageFile]) -> list[str]:
    counts = Counter(family(path) for path in files)
    total = sum(item.digest.length for item in files.values())
    entries = sum(len(item.entries) for item in files.values())
    lines = [f"{len(files)} files, {entries} binder entries, {total} bytes"]
    lines += [f"  {name:<12} {count}" for name, count in sorted(counts.items())]
    return lines


def read(path: str) -> dict[str, PackageFile]:
    return parse(Path(path).read_text(encoding="utf-8"), path)


def main_diff(args: list[str]) -> int:
    paths, exceptions = [], []
    it = iter(args)
    for arg in it:
        if arg == "--except":
            value = next(it, None)
            if value is None:
                print("--except needs a path or pattern", file=sys.stderr)
                return 2
            exceptions.append(value)
        else:
            paths.append(arg)
    if len(paths) != 2:
        print("diff <base.manifest> <new.manifest> [--except <path>]...", file=sys.stderr)
        return 2
    result = compare(read(paths[0]), read(paths[1]), exceptions)
    for line in result.failing:
        print(line)
    for line in result.excepted:
        print(line if line.startswith("    ") else f"excepted {line}")
    for pattern in result.unused_exceptions:
        print(f"exception matched nothing: {pattern}")
    verdict = "IDENTICAL" if not result.failing else "DIFFERENT"
    print(
        f"{verdict}: {result.identical} files identical, "
        f"{sum(1 for line in result.failing if not line.startswith('    '))} differ, "
        f"{sum(1 for line in result.excepted if not line.startswith('    '))} excepted"
    )
    return 0 if not result.failing else 1


def manifest(*lines: str) -> str:
    return "# synthetic\n" + "\n".join(lines) + "\n"


def sha(char: str) -> str:
    return char * 64


def selftest() -> int:
    base_text = manifest(
        f"file\tfacegen/facegen.fgbnd.dcx\t10\t{sha('1')}",
        f"entry\tfacegen/facegen.fgbnd.dcx\tN:\\face.flver\t40\t{sha('2')}",
        f"file\tparts/fc_m_0000.partsbnd.dcx\t20\t{sha('3')}",
        f"entry\tparts/fc_m_0000.partsbnd.dcx\tN:\\FC_M_0000.tpf\t50\t{sha('4')}",
        f"entry\tparts/fc_m_0000.partsbnd.dcx\tN:\\FC_M_0000.flver\t60\t{sha('5')}",
        f"file\tparts/fg_a_0100.partsbnd.dcx\t30\t{sha('6')}",
        f"file\tmushroom-man.me3\t5\t{sha('7')}",
    )
    base = parse(base_text, "base")
    assert len(base) == 4 and len(base["parts/fc_m_0000.partsbnd.dcx"].entries) == 2

    same = compare(base, parse(base_text, "same"), [])
    assert not same.failing and same.identical == 4, same

    # One entry of one binder moved: the binder fails, and the detail names the entry.
    moved = parse(base_text.replace(f"60\t{sha('5')}", f"64\t{sha('8')}").replace(sha("3"), sha("9")), "moved")
    result = compare(base, moved, [])
    assert result.failing[0] == "changed: parts/fc_m_0000.partsbnd.dcx (20 -> 20 bytes)", result.failing
    assert result.failing[1] == "    entry changed: N:\\FC_M_0000.flver (60 -> 64 bytes)", result.failing
    assert len(result.failing) == 2

    # Bytes moved while every entry matched: still a difference, named as a repack.
    repacked = compare(base, parse(base_text.replace(sha("3"), sha("a")), "repacked"), [])
    assert any("repack" in line for line in repacked.failing), repacked.failing

    # A new file and a missing one, the profile excepted by name and a family by pattern.
    grown = parse(
        base_text.replace(f"file\tmushroom-man.me3\t5\t{sha('7')}\n", "")
        + f"file\tcharacter.txt\t6\t{sha('b')}\n",
        "grown",
    )
    result = compare(base, grown, ["mushroom-man.me3"])
    assert result.failing == ["only in new: character.txt (6 bytes)"], result.failing
    assert result.excepted == ["only in base: mushroom-man.me3 (5 bytes)"], result.excepted
    result = compare(base, grown, ["mushroom-man.me3", "character.txt", "parts/hr_a_*"])
    assert not result.failing and result.unused_exceptions == ["parts/hr_a_*"], result

    # Malformed manifests are refused with the line that broke them.
    for bad in (
        manifest(f"file\tparts/x\t10\t{sha('Z')}"),
        manifest(f"entry\tparts/x\tN:\\a\t1\t{sha('1')}"),
        manifest(f"file\tparts/x\t1\t{sha('1')}", f"file\tparts/x\t1\t{sha('1')}"),
        manifest("flie\tparts/x\t1\t1"),
        "# only comments\n",
    ):
        try:
            parse(bad, "bad")
        except ManifestError:
            continue
        raise AssertionError(f"accepted a malformed manifest: {bad!r}")

    # The summary counts files per part family.
    lines = summary(base)
    assert lines[0] == "4 files, 3 binder entries, 65 bytes", lines
    assert "  fc_m         1" in lines and "  fg_a         1" in lines and "  facegen      1" in lines, lines
    assert "  (top level)  1" in lines, lines
    print("selftest ok: identical, changed entry, repack, added/missing, exceptions, malformed, summary")
    return 0


def main() -> int:
    args = sys.argv[1:]
    try:
        if args == ["--selftest"]:
            return selftest()
        if args[:1] == ["diff"]:
            return main_diff(args[1:])
        if args[:1] == ["summary"] and len(args) == 2:
            print("\n".join(summary(read(args[1]))))
            return 0
    except ManifestError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(__doc__.split("\n\n")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
