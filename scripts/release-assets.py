#!/usr/bin/env python3
"""Stage the DLLs a release publishes, and write the release-page table that groups them.

A GitHub release cannot hold folders: its asset list is flat and GitHub sorts it by file name.
Every DLL stays a separate asset anyway, because each one is attested on its own and a reader
can see the whole payload without downloading an archive to find out what is in it. So the
grouping a folder would have given lives in two places that keep every file visible:

* `provenance.json` -- one asset holding every `<artifact>.provenance.json` record, keyed by
  DLL name. The sidecars used to be published one per DLL, which made the asset list twice as
  long, each DLL followed by a JSON file nobody downloads one at a time.
* a release-notes table -- the mods under the installer's own category headings (the tabs a
  player sees in the picker), each row linking that DLL's download. The categories and their
  order come from `scripts/me3-dll-catalog.toml` and `CATEGORY_ORDER` in
  `scripts/gen-installer-catalog.py`, the same sources the installer's `catalog.rs` is
  generated from, so the release page and the picker cannot disagree about where a mod lives.

Usage:
    python3 scripts/release-assets.py stage --source target/x86_64-pc-windows-msvc/release --dist dist
    python3 scripts/release-assets.py notes --repo Banon-Labs/er-mods-rs --tag main-<sha>
    python3 scripts/release-assets.py --selftest
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
import tempfile
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG_TOML = REPO_ROOT / "scripts" / "me3-dll-catalog.toml"
DLL_LIST = REPO_ROOT / "scripts" / "me3-dll-list.py"
GEN_CATALOG = REPO_ROOT / "scripts" / "gen-installer-catalog.py"
PROVENANCE_ASSET = "provenance.json"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def shipped_pairs() -> list[tuple[str, str]]:
    """[(package, "<artifact>.dll"), ...] for every DLL a release carries."""
    return [(package, f"{stem}.dll") for package, stem in _load(DLL_LIST, "me3_dll_list").dll_pairs()]


def category_order() -> list[tuple[str, str]]:
    return list(_load(GEN_CATALOG, "gen_installer_catalog").CATEGORY_ORDER)


def stage(source: Path, dist: Path, pairs: list[tuple[str, str]]) -> list[str]:
    """Copy every DLL into `dist` and fold the provenance sidecars into one file.

    Refuses before copying anything if a DLL or its sidecar is missing: a release with a hole
    in it is worse than no release, and the sidecar is what ties a DLL to the tree it came from.
    """
    missing = []
    for _package, artifact in pairs:
        for name in (artifact, f"{artifact}.provenance.json"):
            if not (source / name).is_file():
                missing.append(str(source / name))
    if missing:
        raise SystemExit(
            "missing from the build tree:\n"
            + "".join(f"  {item}\n" for item in missing)
            + "Build them with scripts/er-build-dlls.sh --all, which also writes the sidecars."
        )

    dist.mkdir(parents=True, exist_ok=True)
    records = {}
    for _package, artifact in pairs:
        shutil.copyfile(source / artifact, dist / artifact)
        sidecar = source / f"{artifact}.provenance.json"
        records[artifact] = json.loads(sidecar.read_text(encoding="utf-8"))
    (dist / PROVENANCE_ASSET).write_text(
        json.dumps(dict(sorted(records.items())), indent=2) + "\n", encoding="utf-8"
    )
    return [artifact for _package, artifact in pairs] + [PROVENANCE_ASSET]


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def notes(
    repo: str,
    tag: str,
    pairs: list[tuple[str, str]],
    catalog: dict,
    categories: list[tuple[str, str]],
) -> str:
    """Markdown for the release page: every DLL, linked, under its picker category."""
    unlisted = [package for package, _artifact in pairs if package not in catalog]
    if unlisted:
        raise SystemExit(
            f"shipped but not in {CATALOG_TOML.name}, so it has no category: {', '.join(unlisted)}"
        )
    known = {key for key, _title in categories}
    stray = sorted(
        {catalog[package]["category"] for package, _a in pairs} - known
    )
    if stray:
        raise SystemExit(f"categories with no heading in CATEGORY_ORDER: {', '.join(stray)}")

    base = f"https://github.com/{repo}/releases/download/{tag}"
    lines = [
        "## Mods by use case",
        "",
        "Each file name downloads that DLL. The headings are the installer's own tabs. "
        f"`{PROVENANCE_ASSET}` records the source tree every DLL was built from.",
    ]
    for key, title in categories:
        rows = sorted(
            (catalog[package]["label"], artifact, catalog[package])
            for package, artifact in pairs
            if catalog[package]["category"] == key
        )
        if not rows:
            continue
        lines += ["", f"### {title}", "", "| File | Mod | What it does |", "|---|---|---|"]
        for label, artifact, entry in rows:
            blurb = entry["blurb"]
            if entry.get("needs_seamless"):
                blurb += " Needs Seamless Co-op."
            lines.append(f"| [`{artifact}`]({base}/{artifact}) | {_cell(label)} | {_cell(blurb)} |")
    return "\n".join(lines) + "\n"


def selftest() -> int:
    failures = 0

    def fail(message: str) -> None:
        nonlocal failures
        print(f"SELFTEST FAIL: {message}")
        failures += 1

    pairs = [("pkg-a", "a.dll"), ("pkg-b", "b.dll")]
    catalog = {
        "pkg-a": {"label": "Alpha", "blurb": "Does a | thing.", "category": "multiplayer"},
        "pkg-b": {
            "label": "Bravo",
            "blurb": "Does b.",
            "category": "menus-and-saves",
            "needs_seamless": True,
        },
    }
    categories = category_order()

    text = notes("o/r", "t1", pairs, catalog, categories)
    if "[`a.dll`](https://github.com/o/r/releases/download/t1/a.dll)" not in text:
        fail("a row does not link its DLL's download")
    if text.index("### Menus and saves") > text.index("### Multiplayer"):
        fail("sections are not in the picker's order")
    if "Does a \\| thing." not in text:
        fail("a pipe in a blurb is not escaped, which splits the table row")
    if "Does b. Needs Seamless Co-op." not in text:
        fail("a Seamless-only mod does not say so")
    if "### Cosmetic" in text:
        fail("an empty category printed a heading")

    for broken, why in (
        ({"pkg-a": catalog["pkg-a"]}, "a shipped DLL missing from the catalog"),
        (
            {**catalog, "pkg-a": {**catalog["pkg-a"], "category": "nowhere"}},
            "a category with no heading",
        ),
    ):
        try:
            notes("o/r", "t1", pairs, broken, categories)
        except SystemExit:
            continue
        fail(f"{why} was accepted, so that DLL would vanish from the table")

    with tempfile.TemporaryDirectory(prefix="release-assets-") as tmp:
        source, dist = Path(tmp) / "src", Path(tmp) / "dist"
        source.mkdir()
        (source / "a.dll").write_bytes(b"A")
        (source / "a.dll.provenance.json").write_text('{"package": "pkg-a"}', encoding="utf-8")
        (source / "b.dll").write_bytes(b"B")
        try:
            stage(source, dist, pairs)
        except SystemExit as refusal:
            if "b.dll.provenance.json" not in str(refusal):
                fail(f"the refusal did not name the missing sidecar: {refusal}")
            if dist.exists():
                fail("a refused stage still copied files")
        else:
            fail("a DLL with no sidecar was staged")

        (source / "b.dll.provenance.json").write_text('{"package": "pkg-b"}', encoding="utf-8")
        staged = stage(source, dist, pairs)
        if sorted(p.name for p in dist.iterdir()) != sorted(staged):
            fail(f"dist holds {sorted(p.name for p in dist.iterdir())}, expected {sorted(staged)}")
        if any(p.name.endswith(".provenance.json") for p in dist.iterdir()):
            fail("a per-DLL sidecar reached the release")
        merged = json.loads((dist / PROVENANCE_ASSET).read_text(encoding="utf-8"))
        if merged != {"a.dll": {"package": "pkg-a"}, "b.dll": {"package": "pkg-b"}}:
            fail(f"merged provenance is wrong: {merged}")

    real_pairs = shipped_pairs()
    real_catalog = tomllib.loads(CATALOG_TOML.read_text(encoding="utf-8"))
    real = notes("o/r", "t", real_pairs, real_catalog, categories)
    for _package, artifact in real_pairs:
        if f"[`{artifact}`]" not in real:
            fail(f"{artifact} is shipped but has no row in the real table")

    if failures:
        print(f"selftest: {failures} case(s) failed")
        return 1
    print(f"selftest: passed; the real table lists all {len(real_pairs)} shipped DLLs")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--selftest", action="store_true")
    sub = parser.add_subparsers(dest="command")
    stage_cmd = sub.add_parser("stage", help="copy the DLLs and write provenance.json")
    stage_cmd.add_argument("--source", type=Path, required=True)
    stage_cmd.add_argument("--dist", type=Path, required=True)
    notes_cmd = sub.add_parser("notes", help="print the grouped release-notes table")
    notes_cmd.add_argument("--repo", required=True, help="owner/name")
    notes_cmd.add_argument("--tag", required=True)
    args = parser.parse_args()

    if args.selftest:
        return selftest()
    if args.command == "stage":
        staged = stage(args.source, args.dist, shipped_pairs())
        print(f"staged {len(staged)} file(s) in {args.dist}")
        return 0
    if args.command == "notes":
        catalog = tomllib.loads(CATALOG_TOML.read_text(encoding="utf-8"))
        sys.stdout.write(notes(args.repo, args.tag, shipped_pairs(), catalog, category_order()))
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
