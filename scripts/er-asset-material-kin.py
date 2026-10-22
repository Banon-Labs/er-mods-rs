#!/usr/bin/env python3
"""Find every asset FLVER that shares materials with a given set of assets.

A ruined building and its intact pieces are modelled from the same material
set, so assets sharing those materials are the candidate kit for rebuilding it.
For each kin asset the output also says whether it ships its own Havok
collision (`<name>_l-geomhkxbnd-dcx` / `_h-geomhkxbnd-dcx`) in the unpack.

Material names come from the UTF-16 `.matxml`/`.mtd` path strings a FLVER
stores, so no FLVER parser is needed.
"""

import argparse
import os
import re
import sys

DEFAULT_ROOT = os.path.join(
    os.environ.get("ER_EXTRACT_ROOT", os.path.expanduser("~/er-extract")),
    "LOOK_HERE_WITCHY_RECURSIVE_20260713/sharded/asset/aeg",
)
DEFAULT_MATERIAL_ROOT = os.path.join(
    os.environ.get("ER_EXTRACT_ROOT", os.path.expanduser("~/er-extract")),
    "LOOK_HERE_WITCHY_RECURSIVE_20260713/sharded/material",
)
STRING = re.compile(rb"(?:[\x20-\x7e]\x00){4,}")


def matbin_albedo(root):
    """Material name -> albedo texture names (`*_a.tif`), from every unpacked matbin."""
    albedo = {}
    for directory, _, files in os.walk(root):
        for name in files:
            if not name.endswith(".matbin.xml"):
                continue
            with open(os.path.join(directory, name), encoding="utf-8", errors="replace") as handle:
                text = handle.read()
            paths = re.findall(r"<Path>([^<]+)</Path>", text)
            albedo[name[: -len(".matbin.xml")]] = {
                p.rsplit("\\", 1)[-1] for p in paths if p.lower().endswith("_a.tif")
            }
    return albedo


SUFFIXES = (".matxml", ".mtd")


def materials(path):
    with open(path, "rb") as handle:
        data = handle.read()
    found = set()
    for match in STRING.finditer(data):
        text = match.group().decode("utf-16-le")
        if text.endswith(SUFFIXES):
            found.add(text.rsplit("\\", 1)[-1])
    return found


def index(root):
    models = {}
    collision = set()
    for directory, dirs, files in os.walk(root):
        for name in dirs:
            match = re.match(r"(aeg\d{3}_\d{3})_[lh]-geomhkxbnd-dcx$", name)
            if match:
                collision.add(match.group(1).upper())
        for name in files:
            stem = name[:-6] if name.endswith(".flver") else None
            if stem and re.fullmatch(r"AEG\d{3}_\d{3}", stem):
                models[stem] = materials(os.path.join(directory, name))
    return models, collision


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("assets", nargs="+", help="seed asset names, e.g. AEG004_501")
    parser.add_argument("--root", default=DEFAULT_ROOT)
    parser.add_argument(
        "--ignore",
        default=r"^M\[|^AEG006_000_|_Edge|Vesitation",
        help="regex of shared/generic materials that do not identify a building",
    )
    parser.add_argument(
        "--textures",
        action="store_true",
        help="match on albedo texture names (`*_a.tif`) instead of material names: finds pieces "
        "built from the same stone even when they were given their own materials",
    )
    parser.add_argument("--material-root", default=DEFAULT_MATERIAL_ROOT)
    args = parser.parse_args()
    ignore = re.compile(args.ignore)
    models, collision = index(args.root)
    if args.textures:
        # Asset FLVERs name a `.matxml`; the textures live in the matbin of the same name.
        albedo = matbin_albedo(args.material_root)
        models = {
            name: {t for m in mats for t in albedo.get(m.rsplit(".", 1)[0], ())}
            for name, mats in models.items()
        }
        ignore = re.compile(r"^$" if args.ignore == parser.get_default("ignore") else args.ignore)
    seeds = [name.upper() for name in args.assets]
    missing = [name for name in seeds if name not in models]
    if missing:
        sys.exit(f"no FLVER for {' '.join(missing)}")
    signature = {m for name in seeds for m in models[name] if not ignore.search(m)}
    print(f"seed materials ({len(signature)}): {' '.join(sorted(signature))}")
    kin = []
    for name, mats in models.items():
        shared = signature & mats
        if shared:
            kin.append((name, len(shared), len(mats), name in collision))
    kin.sort(key=lambda row: (-row[1], row[0]))
    for name, shared, total, has_hkx in kin:
        tag = "seed" if name in seeds else ""
        print(f"{name} shared {shared:3}/{total:3} collision {'yes' if has_hkx else 'no '} {tag}")
    print(f"kin assets: {len(kin)}, with collision: {sum(r[3] for r in kin)}", file=sys.stderr)


if __name__ == "__main__":
    main()
