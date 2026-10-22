#!/usr/bin/env python3
"""Write a machine-readable XML catalog of Elden Ring map asset models (AEG).

One `<asset id="AEG004_506">` per model found in the unpacked corpus or placed by any MSB:

- `<geometry>`: LOD0 FLVER2 bounding box (header min at 0x28, max at 0x34, metres), the LOD FLVERs
  the geombnd carries, header mesh/material/dummy counts and TrueFaceCount. An asset whose header
  box is inverted (min > max) has no visible mesh and is written `empty="true"`: AEG099_002 is
  one, an invisible collider.
- `<materials>`: matxml basenames from the FLVER material table (the matbin names the engine
  loads).
- `<collision>`: which `<name>_h/_l-geomhkxbnd-dcx` binders exist, and the `hknp*Shape` type names
  the tagfile's `TYPE` chunk declares. A type section lists base classes too, so a box collider
  reads `hknpBoxShape hknpConvexPolytopeShape hknpConvexShape`.
- `<placements>`: how many MSB `Part/Asset/*.xml` entries name the model, and `<msb>:<count>`
  per map.
- `<name>` and `<note>` with a `source` attribute: Smithbox's ER asset alias file, Smithbox's
  AssetEnvironmentGeometryParam row names (row id = family * 1000 + index), and facts recorded in
  this repo's bd memories (`bd:<key>`). A model no source names keeps no `<name>`; nothing is
  invented.

The output carries no timestamp and is sorted, so a rerun on the same inputs is byte-identical.

Usage: er-asset-catalog.py [--root <sharded>] [--smithbox <app dir>] [--out <xml>] [--selftest]
"""

import argparse
import collections
import hashlib
import io
import os
import re
import struct
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

EXTRACT_ROOT = os.environ.get("ER_EXTRACT_ROOT", os.path.expanduser("~/er-extract"))
CORPUS = "LOOK_HERE_WITCHY_RECURSIVE_20260713/sharded"
DEFAULT_ROOT = os.path.join(EXTRACT_ROOT, CORPUS)
DEFAULT_SMITHBOX = os.environ.get(
    "SMITHBOX_DIR", os.path.expanduser("~/.local/share/smithbox/app")
)
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(REPO, "docs", "recon", "er-asset-catalog.xml")

ALIAS_FILE = os.path.join("Assets", "Aliases", "ER", "Assets.json")
ROW_NAME_FILE = os.path.join(
    "Assets", "PARAM", "ER", "Param Row Names", "English", "AssetEnvironmentGeometryParam.json"
)

MODEL_ID = re.compile(r"AEG\d{3}_\d{3}")
GEOMBND = re.compile(r"(aeg\d{3}_\d{3})-geombnd-dcx", re.I)
HKXBND = re.compile(r"(aeg\d{3}_\d{3})_([lh])-geomhkxbnd-dcx", re.I)
MODEL_NAME = re.compile(rb"<ModelName>([^<]+)</ModelName>")
HKNP_SHAPE = re.compile(rb"hknp[A-Za-z]*Shape(?![A-Za-z])")

# Facts worked out in this repo, each tied to the bd memory that records it. A pattern is matched
# against the whole model id; a family pattern attaches its note to every member. `name` stays
# None where the memory describes the model without naming what it is. An optional fifth element
# is a matbin the model's FLVER must use for the entry to apply.
BD_CHURCH = "bd:church-kit-damage-ladder-rungs-share-exact-vertices-2026-10-17"
BD_GEOM = "bd:geom-asset-hide-spawn-swap-scaled-collision-frida-proven-1171-2026-10-17"
BD_GRASS = "bd:er-grass-system-m60-42-36-2026-10-17"
BD_FLOWERS = "bd:er-grass-type-73-purple-flowers-2026-10-17"
BD_BUSH = "bd:er-chest-height-bushes-are-aeg001-290-plant-atlas-not-speedtree-2026-10-17"
BD_LOD = "bd:er-distant-lod-trees-mechanism-1171-2026-10-17"
BD_BLOCKS = "bd:er-terrain-embedded-carved-stone-blocks-aeg007-076-080-2026-10-17"
BD_BLOCKS_JOIN = "bd:er-carved-stone-blocks-are-independent-props-not-joined-2026-10-17"
BD_CROSS = "bd:er-marika-crosses-aeg099-350-360-aeg007-553-555-2026-10-17"
BD_TREES = "bd:er-trees-trunk-asset-plus-speedtree-leaf-clusters-m60-42-36-2026-10-17"
BD_ROCK = "bd:er-rock-eruption-scan-method-2026-10-17"
HKX_READ = "measured:aeg099_002_l-geomhkxbnd-dcx/AEG099_002_L.hkx+0x390"

KNOWN = [
    ("AEG099_002", "Invisible collision wall (box collider, no visible mesh)",
     "Havok hknpBoxShape, base 14.9 x 8.34 x 0.13 m. Spawn scale applies to the collider "
     "non-uniformly: spawned at scale [1,0.1,30] it became a ~0.8 m platform the player stood on.",
     BD_GEOM),
    ("AEG099_002", None,
     "Box polytope vertices: x -7.466..7.460, y 0.010..8.343, z -0.065..0.065, so the wall is "
     "14.925 x 8.333 x 0.13 m standing on its base, centred on x and z.",
     HKX_READ),
    (r"AEG004_50\d", "First Step church kit piece",
     "Ruined church at First Step (m60_42_36_00). Damage-ladder twins share its vertices exactly "
     "in the same local frame: AEG004_37x more ruined, AEG004_42x scorched, 57x/87x same "
     "footprint.",
     BD_CHURCH),
    ("AEG004_506", "Church wall (First Step church kit)",
     "Swapping AEG004_506_1201 under the player for AEG004_372 dropped them 1.77 m onto the "
     "twin's collision.",
     BD_GEOM),
    ("AEG004_501", "Church tower (First Step church kit)",
     "Placed as AEG004_501_1200 on m60_42_36_00; 28 meshes on AEG004_501_ID* and m60_fort_230.",
     BD_GRASS),
    (r"AEG004_37\d", None, "More-ruined twin of a First Step church kit piece.", BD_CHURCH),
    (r"AEG004_42[3-9]", None,
     "Scorched variant of a First Step church kit piece: the same geometry plus scorch overlay "
     "meshes on materials AEG004_423_ID01/06/07/08.",
     BD_CHURCH),
    (r"AEG004_(57\d|87\d)", None, "Same-footprint twin of a First Step church kit piece.",
     BD_CHURCH),
    ("AEG004_869", None, "More-ruined rung of AEG004_506 (church wall).", BD_CHURCH),
    ("AEG004_875", None, "More-ruined rung of AEG004_501 (church tower).", BD_CHURCH),
    (r"AEG004_87[6-9]", None, "More-ruined breakdown step of the church kit.", BD_CHURCH),
    ("AEG007_680|AEG004_954", None,
     "A different structure, not an intact church (no game church is intact).", BD_GEOM),
    ("AEG004_145", "Rock (minor eruption)",
     "Typical minor rock eruption: a low wide slab, 65-85% buried in terrain.", BD_ROCK),
    ("AEG001_290", "Chest-height bush (plant atlas, no collision)",
     "148 placed around First Step (m60_42_36_00); materials AtlasPlant / AtrasPlant2 "
     "(Field_Vesitation_forest*), not SpeedTree.",
     BD_BUSH),
    ("AEG099_720", "Chest-height bush (plant atlas)",
     "33 placed around First Step; AtlasPlant material.", BD_BUSH),
    # The memory identifies the impostors by their matbin; other AEG091 models (AEG091_053/054,
    # AET005_201 only) were left out of the tree sweep, so the name follows the material.
    (r"AEG091_\d{3}", "Distant forest LOD impostor (billboard)",
     "One per m60 _00 tile, placed in the _02 LOD tile MSB as m60_XX_YY_00-AEG091_<n>_7000; shader "
     "M[AMSN][XZ_BillBoard]_Edge, matbins AET997_600 (tree billboard atlas AET802_000) and "
     "AET005_201.",
     BD_LOD, "AET997_600"),
    (r"AEG007_07[678]", "Carved stone block (terrain-embedded)",
     "A 5-model family AEG007_076..080, the only FLVERs on materials AEG006_000_ID921/ID925 over "
     "albedo AET003_271. Independent props, never vertex-joined.",
     BD_BLOCKS),
    ("AEG007_07[67]", None,
     "27.9 x 19.7 x 27.7 m; AEG007_077 is a variant of AEG007_076 (35% shared vertices).",
     BD_BLOCKS_JOIN),
    ("AEG007_078", None,
     "43 x 33 x 16 m, mostly buried. Each placement is a site ringed by rubble "
     "AEG007_079/080/081; AEG007_094 sits at the same transform in m60.",
     BD_BLOCKS_JOIN),
    ("AEG007_079|AEG007_080", "Carved stone rubble",
     "Small pieces of the carved stone block family (079 25 x 5 x 16 m, 080 17 x 4 x 7 m), "
     "scattered around AEG007_078 sites.",
     BD_BLOCKS),
    ("AEG007_081", None,
     "Rubble kin of the carved stone blocks (m60_fort material only), scattered around AEG007_078 "
     "sites.",
     BD_BLOCKS_JOIN),
    ("AEG007_094", None,
     "Placed at exactly the transform of every m60 AEG007_078 (51/51): the same volume, a "
     "different mesh, one object built from two assets.",
     BD_BLOCKS_JOIN),
    ("AEG099_350|AEG099_360", "Crucifixion cross (body tied to a cross)",
     "2.8 x 8.4 x 0.8 m; materials AEG099_304/305 (albedo AET099_300/301).", BD_CROSS),
    ("AEG007_553", "Crucifixion cross, upright (10 m)",
     "Same textures as AEG099_350/360.", BD_CROSS),
    ("AEG007_554", "Crucifixion cross, upright (16 m)",
     "Same textures as AEG099_350/360.", BD_CROSS),
    ("AEG007_555", "Crucifixion cross, lying (16 m)",
     "Same textures as AEG099_350/360.", BD_CROSS),
    ("AEG007_55[67]|AEG007_470", None,
     "9 x 9 m flat piece using the crucifixion-cross textures; identity unconfirmed.", BD_CROSS),
    (r"AEG001_273|AEG49[02]_\d{3}|AEG80[15]_\d{3}|AEG81[0-6]_\d{3}", None,
     "SpeedTree family: hiding this set removed whole trees around First Step.", BD_TREES),
    ("AEG801_(250|253|016|011)", "SpeedTree tree (bark and leaf cards in one FLVER)",
     "Whole tree; AEG801_250 is 8.3 x 16.9 x 9.4 m.", BD_TREES),
    ("AEG801_006", "SpeedTree bush (leaf cards only)", "5.5 m bush.", BD_TREES),
    (r"AEG007_27[0-3]", None,
     "52 m, AET801 tree textures; may be giant trees whose foliage is separate (unconfirmed).",
     BD_LOD),
    (r"AEG094_\d{3}|AEG096_41\d", None, "Erdtree geometry (WorldTree shaders).", BD_LOD),
    ("AEG007_217", None, "WorldTree_Leaves material (Erdtree area).", BD_LOD),
    ("AEG001_06[12]", None, "Grass model for GrassTypeParam row 7.", BD_GRASS),
    ("AEG001_06[3-5]", None, "Grass model for GrassTypeParam row 8.", BD_GRASS),
    ("AEG098_202", "Purple flowers (grass type 73)",
     "Confirmed in game by the user; LOD models AEG001_255/254.", BD_FLOWERS),
    ("AEG098_204", "White flowers (grass type 75)", "Confirmed in game by the user.", BD_FLOWERS),
    ("AEG001_25[45]", None, "LOD model of grass type 73 (AEG098_202, purple flowers).", BD_GRASS),
    ("AEG001_302", None, "Forest plant, grass type 202.", BD_GRASS),
    ("AEG001_303", None, "Forest plant, grass type 203.", BD_GRASS),
    (r"AEG098_2(0\d|10)", None,
     "AEG098_200..210 are one plant mesh with different atlas cells (AET005_070_a).", BD_GRASS),
]


# FLVER2 ---------------------------------------------------------------------------------------


def _string(f, offset, unicode):
    """Read a zero-terminated FLVER string at `offset`."""
    f.seek(offset)
    out = b""
    step = 2 if unicode else 1
    while True:
        chunk = f.read(128)
        if not chunk:
            break
        out += chunk
        for i in range(0, len(out) - step + 1, step):
            if out[i:i + step] == b"\0" * step:
                raw = out[:i]
                return raw.decode("utf-16-le" if unicode else "shift_jis", errors="replace")
        if len(out) > 4096:
            break
    return ""


def read_flver(f):
    """Header summary of a FLVER2 file object, or None when it is not a little-endian FLVER2.

    Header fields (SoulsFormats FLVER2): version 0x08, dummy/material/bone/mesh/vertex-buffer
    counts 0x14..0x24, bbox min 0x28 and max 0x34, TrueFaceCount 0x40, unicode flag 0x49. The
    material table follows the 0x80 header and 0x40-byte dummies, 0x20 bytes per entry with the
    name offset at +0 and the matxml path offset at +4.
    """
    head = f.read(0x80)
    if len(head) < 0x80 or head[:6] != b"FLVER\0" or head[6:8] != b"L\0":
        return None
    version = struct.unpack_from("<I", head, 0x08)[0]
    dummies, materials, bones, meshes, buffers = struct.unpack_from("<5i", head, 0x14)
    lo = struct.unpack_from("<3f", head, 0x28)
    hi = struct.unpack_from("<3f", head, 0x34)
    faces = struct.unpack_from("<i", head, 0x40)[0]
    unicode = head[0x49] != 0
    empty = any(lo[i] > hi[i] or abs(lo[i]) > 1e30 or abs(hi[i]) > 1e30 for i in range(3))
    mats = []
    if 0 <= materials <= 4096 and 0 <= dummies <= 65536:
        f.seek(0x80 + dummies * 0x40)
        table = f.read(materials * 0x20)
        offsets = [struct.unpack_from("<2i", table, i * 0x20) for i in range(len(table) // 0x20)]
        for name_off, mtd_off in offsets:
            path = _string(f, mtd_off, unicode) if mtd_off > 0 else ""
            name = _string(f, name_off, unicode) if name_off > 0 else ""
            mats.append((name, path))
    return {
        "version": version,
        "dummies": dummies,
        "materials": mats,
        "bones": bones,
        "meshes": meshes,
        "faces": faces,
        "min": None if empty else lo,
        "max": None if empty else hi,
    }


def matbin_names(materials):
    """Unique matxml basenames (no extension), in first-seen order."""
    seen = []
    for name, path in materials:
        base = path.replace("/", "\\").rsplit("\\", 1)[-1]
        base = re.sub(r"\.(matxml|mtd)$", "", base, flags=re.I) if base else name
        if base and base not in seen:
            seen.append(base)
    return seen


# Havok ----------------------------------------------------------------------------------------


def _chunk(header):
    return int.from_bytes(header[:4], "big") & 0x3FFFFFFF, header[4:8]


def hkx_shape_types(f):
    """`hknp*Shape` names a Havok tagfile declares, `hknpShape` itself dropped.

    A tagfile is a `TAG0` chunk holding `SDKV`, `DATA`, `TYPE` (and `INDX`) chunks, each with a
    big-endian u32 size (top two bits are flags) and a 4-byte tag. Only `TYPE` is read; a file
    that does not walk as a tagfile is scanned whole.
    """
    head = f.read(8)
    body = None
    if len(head) == 8 and head[4:8] == b"TAG0":
        total, _ = _chunk(head)
        pos = 8
        while pos + 8 <= total:
            f.seek(pos)
            size, tag = _chunk(f.read(8))
            if size < 8:
                break
            if tag == b"TYPE":
                body = f.read(size - 8)
                break
            pos += size
    if body is None:
        f.seek(0)
        body = f.read()
    names = {m.decode() for m in HKNP_SHAPE.findall(body)}
    names.discard("hknpShape")
    return sorted(names)


# Corpus scans ---------------------------------------------------------------------------------


def scan_assets(aeg_root):
    """Model id -> {flvers: [paths], hkx: {lod: [paths]}, animated, cloth, grass}."""
    models = collections.defaultdict(
        lambda: {"flvers": [], "hkx": {}, "animated": False, "cloth": False, "grass": False}
    )
    for directory, dirs, _ in os.walk(aeg_root):
        for name in dirs:
            geom = GEOMBND.fullmatch(name)
            hkx = HKXBND.fullmatch(name)
            if geom:
                model = models[geom.group(1).upper()]
                for sub, subdirs, files in os.walk(os.path.join(directory, name)):
                    for file in files:
                        lower = file.lower()
                        if lower.endswith(".flver"):
                            model["flvers"].append(os.path.join(sub, file))
                        elif lower.endswith(".anibnd"):
                            model["animated"] = True
                        elif lower.endswith(".clm2"):
                            model["cloth"] = True
                        elif lower.endswith(".grass"):
                            model["grass"] = True
            elif hkx:
                model = models[hkx.group(1).upper()]
                path = os.path.join(directory, name)
                files = [
                    os.path.join(path, file)
                    for file in os.listdir(path)
                    if file.lower().endswith(".hkx")
                ]
                model["hkx"].setdefault(hkx.group(2).lower(), []).extend(files)
    return models


def _read_part_models(job):
    directory, msb = job
    found = []
    for name in os.listdir(directory):
        if not name.endswith(".xml"):
            continue
        path = os.path.join(directory, name)
        with open(path, "rb") as handle:
            head = handle.read(1024)
            match = MODEL_NAME.search(head)
            if not match:
                match = MODEL_NAME.search(head + handle.read())
        if match:
            found.append(match.group(1).decode("utf-8", "replace").strip().upper())
    return msb, found


def scan_placements(map_root, workers):
    """Model id -> Counter(msb name -> placements), from every MSB `Part/Asset/*.xml`."""
    jobs = []
    tail = os.path.join("Part", "Asset")
    for directory, _, _ in os.walk(map_root):
        if not directory.endswith(tail):
            continue
        msb = next(
            (p[: -len("-msb-dcx")] for p in directory.split(os.sep) if p.endswith("-msb-dcx")),
            None,
        )
        if msb:
            jobs.append((directory, msb))
    placed = collections.defaultdict(collections.Counter)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for msb, found in pool.map(_read_part_models, jobs):
            for model in found:
                placed[model][msb] += 1
    return placed, len(jobs)


# Name sources ---------------------------------------------------------------------------------


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _display(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path


def load_smithbox(app_dir):
    """(sources, names): sources describe each file read; names maps model id -> [(text, src)]."""
    import json

    sources = []
    names = collections.defaultdict(list)
    alias = os.path.join(app_dir, ALIAS_FILE)
    if os.path.isfile(alias):
        with open(alias, encoding="utf-8-sig") as handle:
            entries = json.load(handle)
        named = 0
        for entry in entries:
            model = str(entry.get("ID", "")).upper()
            text = (entry.get("Name") or "").strip()
            if MODEL_ID.fullmatch(model) and text:
                names[model].append((text, "smithbox-asset-aliases"))
                named += 1
        sources.append({
            "id": "smithbox-asset-aliases", "path": _display(alias), "sha256": _sha256(alias),
            "entries": str(len(entries)), "named": str(named),
        })
    rows = os.path.join(app_dir, ROW_NAME_FILE)
    if os.path.isfile(rows):
        with open(rows, encoding="utf-8-sig") as handle:
            entries = json.load(handle).get("Entries", [])
        named = 0
        for entry in entries:
            row = entry.get("ID")
            text = next((n.strip() for n in entry.get("Entries", []) if n and n.strip()), "")
            if isinstance(row, int) and 0 <= row < 1_000_000 and text:
                names[f"AEG{row // 1000:03d}_{row % 1000:03d}"].append(
                    (text, "smithbox-param-row-names")
                )
                named += 1
        sources.append({
            "id": "smithbox-param-row-names", "path": _display(rows), "sha256": _sha256(rows),
            "entries": str(len(entries)), "named": str(named),
        })
    return sources, names


def known_facts(model, materials=()):
    """[(kind, text, source)] from `KNOWN` for one model id and its matbin names."""
    out = []
    for pattern, name, note, source, *requires in KNOWN:
        if re.fullmatch(pattern, model) and all(m in materials for m in requires):
            if name:
                out.append(("name", name, source))
            if note:
                out.append(("note", note, source))
    return out


# Catalog --------------------------------------------------------------------------------------


def _fmt(values):
    """Millimetre precision, trailing zeros dropped, `-0` written as `0`."""
    out = []
    for value in values:
        text = f"{value:.3f}".rstrip("0").rstrip(".")
        out.append("0" if text in ("-0", "") else text)
    return " ".join(out)


def build_entry(model, info, placements, aliases):
    """Plain dict for one model; `info` is a scan_assets value or None."""
    entry = {"id": model, "family": model[:6], "names": [], "notes": []}
    for text, source in aliases.get(model, []):
        entry["names"].append((text, source))
    if info is not None and info["flvers"]:
        flvers = sorted(info["flvers"], key=lambda p: os.path.basename(p).upper())
        lod0 = next((p for p in flvers if os.path.basename(p)[:-6].upper() == model), flvers[0])
        with open(lod0, "rb") as handle:
            header = read_flver(handle)
        geometry = {"lods": " ".join(os.path.basename(p)[:-6] for p in flvers)}
        if header is None:
            geometry["unreadable"] = "true"
        else:
            geometry.update({
                "meshes": str(header["meshes"]), "faces": str(header["faces"]),
                "dummies": str(header["dummies"]), "bones": str(header["bones"]),
            })
            if header["min"] is None:
                geometry["empty"] = "true"
            else:
                size = [header["max"][i] - header["min"][i] for i in range(3)]
                geometry["size"] = _fmt(size)
                geometry["min"] = _fmt(header["min"])
            entry["materials"] = matbin_names(header["materials"])
        entry["geometry"] = geometry
    elif info is not None:
        entry["geometry"] = {"lods": ""}
    for kind, text, source in known_facts(model, entry.get("materials", ())):
        entry["names" if kind == "name" else "notes"].append((text, source))
    if info is not None:
        flags = [f for f in ("animated", "cloth", "grass") if info[f]]
        if flags:
            entry["flags"] = flags
        if info["hkx"]:
            types = set()
            for paths in info["hkx"].values():
                for path in paths:
                    with open(path, "rb") as handle:
                        types.update(hkx_shape_types(handle))
            entry["collision"] = {"lods": " ".join(sorted(info["hkx"])), "havok": " ".join(sorted(types))}
    if info is None:
        entry["in_corpus"] = False
    counts = placements.get(model)
    if counts:
        entry["placements"] = sorted(counts.items())
    return entry


def to_element(entry):
    asset = ET.Element("asset", {"id": entry["id"], "family": entry["family"]})
    if entry.get("in_corpus") is False:
        asset.set("in-corpus", "false")
    for text, source in entry["names"]:
        ET.SubElement(asset, "name", {"source": source}).text = text
    for text, source in entry["notes"]:
        ET.SubElement(asset, "note", {"source": source}).text = text
    if "geometry" in entry:
        ET.SubElement(asset, "geometry", dict(sorted(entry["geometry"].items())))
    if entry.get("materials"):
        ET.SubElement(asset, "materials").text = " ".join(entry["materials"])
    if "collision" in entry:
        ET.SubElement(asset, "collision", dict(sorted(entry["collision"].items())))
    if entry.get("flags"):
        ET.SubElement(asset, "flags").text = " ".join(entry["flags"])
    placements = entry.get("placements") or []
    total = sum(count for _, count in placements)
    node = ET.SubElement(asset, "placements", {"count": str(total), "maps": str(len(placements))})
    if placements:
        node.text = " ".join(f"{msb}:{count}" for msb, count in placements)
    return asset


def from_element(asset):
    """Inverse of to_element, for consumers and the round-trip selftest."""
    entry = {"id": asset.get("id"), "family": asset.get("family"), "names": [], "notes": []}
    if asset.get("in-corpus") == "false":
        entry["in_corpus"] = False
    for child in asset:
        if child.tag == "name":
            entry["names"].append((child.text or "", child.get("source")))
        elif child.tag == "note":
            entry["notes"].append((child.text or "", child.get("source")))
        elif child.tag == "geometry":
            entry["geometry"] = dict(child.attrib)
        elif child.tag == "materials":
            entry["materials"] = (child.text or "").split()
        elif child.tag == "collision":
            entry["collision"] = dict(child.attrib)
        elif child.tag == "flags":
            entry["flags"] = (child.text or "").split()
        elif child.tag == "placements" and child.text:
            entry["placements"] = [
                (item.rsplit(":", 1)[0], int(item.rsplit(":", 1)[1])) for item in child.text.split()
            ]
    return entry


def serialize(root_attrs, sources, entries):
    root = ET.Element("asset-catalog", root_attrs)
    for source in sources:
        ET.SubElement(root, "source", dict(sorted(source.items())))
    for entry in sorted(entries, key=lambda e: e["id"]):
        root.append(to_element(entry))
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"


def parse(data):
    root = ET.fromstring(data)
    sources = [dict(node.attrib) for node in root.findall("source")]
    entries = [from_element(node) for node in root.findall("asset")]
    return dict(root.attrib), sources, entries


# Main -----------------------------------------------------------------------------------------


def build(args):
    aeg_root = os.path.join(args.root, "asset", "aeg")
    map_root = os.path.join(args.root, "map")
    if not os.path.isdir(aeg_root):
        sys.exit(f"no asset corpus at {aeg_root} (set ER_EXTRACT_ROOT or --root)")
    print(f"scanning {aeg_root}", file=sys.stderr)
    models = scan_assets(aeg_root)
    print(f"  {len(models)} models; scanning placements under {map_root}", file=sys.stderr)
    placed, msbs = scan_placements(map_root, args.workers)
    print(f"  {sum(sum(c.values()) for c in placed.values())} placements in {msbs} MSBs",
          file=sys.stderr)
    sources, aliases = load_smithbox(args.smithbox)
    for _, _, _, source, *_ in KNOWN:
        if source not in {s["id"] for s in sources}:
            kind = "bd-memory" if source.startswith("bd:") else "measurement"
            sources.append({"id": source, "kind": kind})

    ids = sorted(set(models) | {m for m in placed if MODEL_ID.fullmatch(m)})
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        entries = list(pool.map(lambda m: build_entry(m, models.get(m), placed, aliases), ids))

    in_corpus = [e for e in entries if e.get("in_corpus") is not False]
    named = [e for e in entries if e["names"]]
    root_attrs = {
        "generator": "scripts/er-asset-catalog.py",
        "corpus": "$ER_EXTRACT_ROOT/" + CORPUS if args.root == DEFAULT_ROOT else _display(args.root),
        "assets": str(len(entries)),
        "in-corpus": str(len(in_corpus)),
        "placed-only": str(len(entries) - len(in_corpus)),
        "unplaced": str(sum(1 for e in in_corpus if not e.get("placements"))),
        "named": str(len(named)),
        "msbs": str(msbs),
    }
    data = serialize(root_attrs, sources, entries)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    previous = None
    if os.path.isfile(args.out):
        with open(args.out, "rb") as handle:
            previous = handle.read()
    if previous != data:
        with open(args.out, "wb") as handle:
            handle.write(data)
    by_source = collections.Counter(
        source for e in entries for source in {s for _, s in e["names"]}
    )
    print(f"wrote {args.out} ({len(data)} bytes, {'changed' if previous != data else 'unchanged'})")
    for key, value in root_attrs.items():
        print(f"  {key}: {value}")
    for source, count in sorted(by_source.items()):
        print(f"  named by {source}: {count}")
    alias_ids = set(aliases)
    print(f"  name-source ids with no model in corpus or MSB: {len(alias_ids - set(ids))}")


def selftest():
    # Synthetic FLVER2: 1 dummy, 2 materials, unicode strings after the table.
    def flver(lo, hi, unicode=True):
        head = bytearray(0x80)
        head[0:8] = b"FLVER\0L\0"
        struct.pack_into("<I", head, 0x08, 0x2001A)
        struct.pack_into("<5i", head, 0x14, 1, 2, 3, 4, 4)
        struct.pack_into("<3f", head, 0x28, *lo)
        struct.pack_into("<3f", head, 0x34, *hi)
        struct.pack_into("<i", head, 0x40, 1234)
        head[0x49] = 1 if unicode else 0
        dummies = bytes(0x40)
        strings = [
            "AEG000_001_ID01", "N:\\GR\\data\\Material\\mtd\\Map_m60_00\\matxml\\AEG000_001_ID01.matxml",
            "Second", "N:\\GR\\data\\Material\\mtd\\x\\m60_fort_230.matxml",
        ]
        table_at = 0x80 + 0x40
        blob_at = table_at + 2 * 0x20
        blob = b""
        offsets = []
        for text in strings:
            offsets.append(blob_at + len(blob))
            blob += text.encode("utf-16-le" if unicode else "ascii") + (b"\0\0" if unicode else b"\0")
        table = bytearray(2 * 0x20)
        struct.pack_into("<2i", table, 0, offsets[0], offsets[1])
        struct.pack_into("<2i", table, 0x20, offsets[2], offsets[3])
        return bytes(head) + dummies + bytes(table) + blob

    got = read_flver(io.BytesIO(flver((-1.5, 0.0, -0.25), (2.5, 8.0, 0.25))))
    assert got and got["meshes"] == 4 and got["faces"] == 1234 and got["dummies"] == 1, got
    assert got["min"] == (-1.5, 0.0, -0.25) and got["max"] == (2.5, 8.0, 0.25), got
    assert matbin_names(got["materials"]) == ["AEG000_001_ID01", "m60_fort_230"], got["materials"]
    ascii_got = read_flver(io.BytesIO(flver((0, 0, 0), (1, 1, 1), unicode=False)))
    assert matbin_names(ascii_got["materials"]) == ["AEG000_001_ID01", "m60_fort_230"]
    big = 3.4028234663852886e38
    empty = read_flver(io.BytesIO(flver((big, big, big), (-big, -big, -big))))
    assert empty["min"] is None and empty["max"] is None, empty
    assert read_flver(io.BytesIO(b"FLVER\0B\0" + bytes(0x78))) is None
    assert read_flver(io.BytesIO(b"short")) is None

    # Synthetic tagfile: a decoy shape name in `DATA` must be ignored, `TYPE` must be read.
    def chunk(tag, body, flags=0):
        return ((len(body) + 8) | flags).to_bytes(4, "big") + tag + body

    inner = (
        chunk(b"SDKV", b"20180100", 0x40000000)
        + chunk(b"DATA", b"hknpCylinderShape\0", 0x40000000)
        + chunk(b"TYPE", b"hknpBoxShape\0hknpConvexPolytopeShape\0hknpShape\0", 0)
    )
    hkx = chunk(b"TAG0", inner)
    assert hkx_shape_types(io.BytesIO(hkx)) == ["hknpBoxShape", "hknpConvexPolytopeShape"]
    assert hkx_shape_types(io.BytesIO(b"junk hknpCompressedMeshShape junk")) == [
        "hknpCompressedMeshShape"
    ]

    # Known-fact patterns compile and name real-looking ids.
    for pattern, name, note, source, *requires in KNOWN:
        re.compile(pattern)
        assert source and (name or note) and len(requires) <= 1, pattern
    assert any(k == "name" for k, _, _ in known_facts("AEG099_002"))
    assert known_facts("AEG999_999") == []
    # A material-gated entry applies only when the FLVER uses that matbin.
    assert not any(k == "name" for k, _, _ in known_facts("AEG091_053", ["AET005_201"]))
    assert any(k == "name" for k, _, _ in known_facts("AEG091_024", ["AET997_600", "AET005_201"]))

    # XML round trip, and byte-identical reserialization.
    entries = [
        {
            "id": "AEG004_506", "family": "AEG004",
            "names": [("Church wall", BD_GEOM)],
            "notes": [("a <note> & \"quotes\"", BD_CHURCH)],
            "geometry": {"lods": "AEG004_506 AEG004_506_1", "meshes": "19", "faces": "10218",
                         "dummies": "0", "bones": "1", "size": "14.5 12 3", "min": "-7 0 -1.5"},
            "materials": ["AEG004_501_ID01", "m60_fort_230"],
            "collision": {"havok": "hknpCompressedMeshShape", "lods": "h l"},
            "flags": ["grass"],
            "placements": [("m60_42_36_00", 2), ("m60_42_36_10", 1)],
        },
        {"id": "AEG099_002", "family": "AEG099", "names": [], "notes": [],
         "geometry": {"lods": "AEG099_002", "meshes": "0", "faces": "0", "dummies": "9",
                      "bones": "1", "empty": "true"}},
        {"id": "AEG123_456", "family": "AEG123", "names": [], "notes": [], "in_corpus": False,
         "placements": [("m10_00_00_00", 3)]},
    ]
    attrs = {"generator": "selftest", "assets": "3"}
    sources = [{"id": "x", "path": "~/a b.json", "sha256": "00"}]
    data = serialize(attrs, sources, entries)
    got_attrs, got_sources, got_entries = parse(data)
    assert got_attrs == attrs and got_sources == sources, (got_attrs, got_sources)
    assert got_entries == sorted(entries, key=lambda e: e["id"]), got_entries
    assert serialize(got_attrs, got_sources, got_entries) == data
    assert _fmt([0.0004, -0.0004, 1.23456, -2.0, -0.5, 10.0]) == "0 0 1.235 -2 -0.5 10"
    print("selftest ok")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=DEFAULT_ROOT, help="the unpacked `sharded` directory")
    parser.add_argument("--smithbox", default=DEFAULT_SMITHBOX, help="Smithbox app directory")
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        selftest()
        return
    build(args)


if __name__ == "__main__":
    main()
