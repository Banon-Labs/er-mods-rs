#!/usr/bin/env python3
"""Dress the AI lab's duelists in whatever their build planner link holds right now.

    python3 scripts/er-duelist-gear.py              # rewrite scripts/frida/ai-lua/mods/duelists.lua
    python3 scripts/er-duelist-gear.py --check      # is that file the link's current revision?
    python3 scripts/er-duelist-gear.py --json       # the same rewrite, one JSON status line out
    python3 scripts/er-duelist-gear.py --selftest   # the catalog rules and the JSON-to-Lua step, offline

The link, the think row the duelists run, how many there are and how the lab respawns them live in
scripts/frida/ai-lua/duelists.json, which is the file to edit. duelists.lua is output:
scripts/er-ai-lab.py runs this at start and before every respawn, so a build edited on the planner
reaches the next duelists that spawn without anyone copying gear into Lua by hand.

Nothing is resolved here. The build goes to `er-worn-gear` (crates/er-build-import-core), the equip
plan and armament arithmetic the in-game importer uses: which rows of the active set are worn
(a talisman with no equip position is carried, not worn), affinity and level folded into the
armament id on the right scale for somber armaments, the ash as its EquipParamGem row, and the
engine's own empty value in every slot the build leaves bare. What this script supplies is the
catalog that crate looks names up in -- the rows er-build-import-runtime reads from the live game,
read here from the same tables on disk: the item-name message files (scripts/er-item-name.py's
cache) and the installed regulation.bin (scripts/er-param-read.py) -- and the Lua around the answer.

A fetch or resolve that fails keeps the last generated file and says so; it never blanks it.
Needs Python 3.14+ (regulation.bin is zstd-compressed; see scripts/regulation-params.py).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
LUA_DIR = HERE / "frida" / "ai-lua"
CONFIG = LUA_DIR / "duelists.json"
OUTPUT = LUA_DIR / "mods" / "duelists.lua"
CRATE = ROOT / "crates" / "er-build-import-core"
BIN_NAME = "er-worn-gear"
CACHE = pathlib.Path(os.environ.get("ER_DUELIST_GEAR_CACHE",
                                    pathlib.Path.home() / ".cache" / "er-mods-rs" / "duelist-gear"))

API = "https://er-inventory-api.nyasu.business/inventories"
PLANNER = "https://er-build-planner.nyasu.business/?b="
USER_AGENT = "er-mods-rs duelist-gear (+github.com/Banon-Labs)"
# The three bounds add up to under the 30 s scripts/er-ai-lab.py gives this whole script.
FETCH_TIMEOUT_S = 8
# A rebuild of the one crate after a source edit; a cold build of its dependencies can take longer,
# and then the last generated file is kept and the error says to build it once by hand.
BUILD_TIMEOUT_S = 15
RUN_TIMEOUT_S = 5

# EquipParamWeapon.weaponCategory for arrows and bolts: those rows are ammunition, which the
# runtime catalog files under its own kind and never offers a hand (er-build-import-runtime
# catalog.rs, `Quivers`).
AMMO_CATEGORIES = (13, 14)
# The highest level the game has reinforce rows for, regular and somber.
MAX_REGULAR_LEVEL = 25
MAX_SOMBER_LEVEL = 10
# EquipParamGem.iconId values that draw no icon (the field is unsigned, so "none" is either end).
NO_ICON = (0, 0xFFFF)


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GearError(RuntimeError):
    """A step that failed; the message says which and why."""


# ---------------------------------------------------------------------------------------------
# Config and link


def load_config(path: pathlib.Path = CONFIG) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    for key in ("build", "think", "summons"):
        if key not in config:
            raise GearError(f"{path}: no {key!r}")
    if not isinstance(config["think"], int) or not isinstance(config["summons"], int) or config["summons"] < 1:
        raise GearError(f"{path}: think and summons are whole numbers, summons at least 1")
    return config


def share_id(link: str) -> str:
    """The `?b=` id out of a planner link, or the id itself when that is what was given."""
    link = link.strip()
    match = re.search(r"[?&]b=([A-Za-z0-9]+)", link) or re.fullmatch(r"([A-Za-z0-9]+)", link)
    if match is None:
        raise GearError(f"{link!r} is not a planner ?b= link or build id")
    return match.group(1)


def fetch(build_id: str) -> dict:
    request = urllib.request.Request(f"{API}/{build_id}", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_S) as response:
            return json.load(response)
    except Exception as exc:  # urllib raises several unrelated types; all mean "no build"
        raise GearError(f"fetching build {build_id}: {type(exc).__name__}: {exc}") from exc


# ---------------------------------------------------------------------------------------------
# Catalog


def highest_level(reinforce_type: int, reinforce_ids: set[int]) -> int | None:
    """The highest level an armament has a ReinforceParamWeapon row for, as the runtime's
    `ReinforceLevels::clamp` measures it."""
    levels = [level for level in range(MAX_REGULAR_LEVEL + 1) if reinforce_type + level in reinforce_ids]
    return max(levels) if levels else None


def rows_from_tables(weapons, reinforce_ids, protectors, accessories, gems, names) -> list[dict]:
    """Catalog rows for er-worn-gear, by the rules er-build-import-runtime's catalog.rs applies to
    the live tables.

    weapons: [{id, reinforceTypeId, weaponCategory}]; protectors, accessories: [id];
    gems: [{id, swordArtsParamId, iconId}]; names: {"WeaponName": {id: text}, "ProtectorName",
    "AccessoryName", "ArtsName", "GemName"}. Each kind is listed in ascending row id, the order the
    runtime enumerates a param table in, because the catalog keeps the first row under a name.
    """
    rows = []
    for row in sorted(weapons, key=lambda r: r["id"]):
        if row.get("weaponCategory") in AMMO_CATEGORIES:
            continue
        name = names["WeaponName"].get(row["id"])
        if name:
            somber = highest_level(row.get("reinforceTypeId", 0), reinforce_ids) == MAX_SOMBER_LEVEL
            rows.append({"kind": "weapon", "row": row["id"], "name": name, "somber": somber})
    for kind, ids, stem in (("protector", protectors, "ProtectorName"),
                            ("talisman", accessories, "AccessoryName")):
        for row_id in sorted(ids):
            name = names[stem].get(row_id)
            if name:
                rows.append({"kind": kind, "row": row_id, "name": name})
    # An ash is keyed by the skill's name and valued by a gem row: per skill the canonical
    # `gem == arts * 100` row wins outright, then a row with an icon, then a row the game names,
    # then the lowest row id -- `insert_ashes_of_war`'s order, so placeholder rows never win.
    best: dict[int, tuple] = {}
    for gem in gems:
        arts = gem["swordArtsParamId"]
        if arts < 0:
            continue
        key = (gem["id"] == arts * 100, gem.get("iconId", 0) not in NO_ICON,
               gem["id"] in names["GemName"], -gem["id"])
        if arts not in best or key > best[arts]:
            best[arts] = key
    for arts in sorted(best):
        # SwordArtsParam row 0 is `No Skill`, which the message files do not name; the runtime
        # catalogues it under the planner's own spelling.
        name = names["ArtsName"].get(arts) or ("No Skill" if arts == 0 else None)
        if name:
            rows.append({"kind": "ash_of_war", "row": -best[arts][3], "name": name})
    return rows


def catalog_rows() -> tuple[list[dict], list[str]]:
    """The catalog from the installed game's own tables, and anything worth a warning."""
    names_mod = _load("er_item_name", "er-item-name.py")
    params = _load("er_param_read", "er-param-read.py")
    notes = []
    state = names_mod.cache_state()
    if state == "missing":
        raise GearError("no item-name cache: run python3 scripts/er-item-name.py --refresh once")
    if state == "stale":
        notes.append("the item-name cache predates the installed archives (python3 "
                     "scripts/er-item-name.py --refresh); items added since are unnamed")
    names = {stem: names_mod.game_names(stem)
             for stem in ("WeaponName", "ProtectorName", "AccessoryName", "ArtsName", "GemName")}
    # The readers report a bad key, a missing paramdef or an unverified field offset with
    # SystemExit; here that is one failed step, not the end of the lab that called this.
    try:
        files = params.load()

        def ids(stem):
            return params.RP.param_row_ids(params.param_bytes(files, stem))

        def fields(stem, wanted):
            return params.rows(params.param_bytes(files, stem), wanted)[0]

        weapons = fields("EquipParamWeapon", ["reinforceTypeId", "weaponCategory"])
        reinforce = set(ids("ReinforceParamWeapon"))
        protectors = ids("EquipParamProtector")
        accessories = ids("EquipParamAccessory")
        gems = fields("EquipParamGem", ["swordArtsParamId", "iconId"])
    except (Exception, SystemExit) as exc:
        raise GearError(f"reading regulation.bin: {type(exc).__name__}: {exc}") from exc
    return rows_from_tables(weapons, reinforce, protectors, accessories, gems, names), notes


# ---------------------------------------------------------------------------------------------
# Resolution (er-build-import-core)


def target_dir() -> pathlib.Path:
    return pathlib.Path(os.environ.get("CARGO_TARGET_DIR", ROOT / "target"))


def worn_gear_binary() -> pathlib.Path:
    """er-worn-gear, rebuilt first when any of the crate's sources is newer than it."""
    binary = target_dir() / "debug" / BIN_NAME
    sources = [CRATE / "Cargo.toml", CRATE / "build.rs"]
    sources += [p for d in ("src", "data") for p in (CRATE / d).rglob("*") if p.is_file()]
    newest = max(p.stat().st_mtime for p in sources)
    if binary.exists() and binary.stat().st_mtime >= newest:
        return binary
    command = ["nice", "-n", "19", "cargo", "build", "--quiet", "-p", "er-build-import-core", "--bin", BIN_NAME]
    try:
        run = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=BUILD_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise GearError(f"building {BIN_NAME} took over {BUILD_TIMEOUT_S} s; build it once with: "
                        f"cd {ROOT} && {' '.join(command)}") from exc
    if run.returncode != 0 or not binary.exists():
        raise GearError(f"building {BIN_NAME} failed ({run.returncode}):\n{run.stderr[-2000:]}")
    return binary


def resolve(doc: dict, rows: list[dict]) -> dict:
    """er-worn-gear's answer for `doc` against `rows`. The inputs are kept in the cache directory,
    so the last run can be replayed by hand."""
    CACHE.mkdir(parents=True, exist_ok=True)
    build_path = CACHE / "build.json"
    rows_path = CACHE / "catalog-rows.json"
    build_path.write_text(json.dumps(doc), encoding="utf-8")
    rows_path.write_text(json.dumps(rows), encoding="utf-8")
    binary = worn_gear_binary()
    try:
        run = subprocess.run([str(binary), "--catalog", str(rows_path), str(build_path)],
                             capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise GearError(f"{BIN_NAME} ran over {RUN_TIMEOUT_S} s") from exc
    if run.returncode != 0:
        raise GearError(f"{BIN_NAME} failed ({run.returncode}): {run.stderr.strip()}")
    gear = json.loads(run.stdout)
    (CACHE / "gear.json").write_text(run.stdout, encoding="utf-8")
    return gear


# ---------------------------------------------------------------------------------------------
# Face


def face_template(think: int, mods: pathlib.Path = LUA_DIR / "mods", skip: pathlib.Path = OUTPUT) -> str | None:
    """The face buffer the think row already wears: the hex of another mod's `lab_face(think, ...)`.
    A build's sliders are written over it, the way scripts/planner-face-hex.py does."""
    pattern = re.compile(rf'lab_face\(\s*{think}\s*,\s*"([0-9A-Fa-f]+)"\s*\)')
    for path in sorted(mods.glob("*.lua")):
        if path.resolve() == skip.resolve():
            continue
        match = pattern.search(path.read_text(encoding="utf-8"))
        if match:
            return match.group(1)
    return None


def face_hex(doc: dict, template: str | None) -> str | None:
    """The build's appearance as a face buffer, or None when it has none (most builds)."""
    sliders = (doc.get("sliders") or {}).get("sliders")
    if not isinstance(sliders, dict) or not sliders or template is None:
        return None
    face = _load("planner_face_hex", "planner-face-hex.py")
    buffer = bytearray(bytes.fromhex(template))
    face.encode_into(sliders, buffer, face.load_layout())
    return buffer.hex().upper()


# ---------------------------------------------------------------------------------------------
# Lua


def summon_keys(config: dict) -> list[str]:
    """spawn-npc.js's per-summon gear keys, "think.N" for the Nth summon."""
    return [f"{config['think']}.{index}" for index in range(config["summons"])]


def lab_pieces(config: dict, gear: dict, face: str | None) -> list[str]:
    """The LAB_EQUIP entries the rendered file makes _lab.lua log, spelled as _lab.lua spells them
    (`think:slot=id` and `/gem`, `think:face=hex`). The lab waits for these to reach spawn-npc.js
    before it respawns."""
    out = []
    for key in summon_keys(config):
        for piece in gear["slots"]:
            entry = f"{key}:{piece['slot']}={piece['id']}"
            if piece.get("gem") is not None:
                entry += f"/{piece['gem']}"
            out.append(entry)
        if face:
            out.append(f"{key}:face={face}")
    return out


def one_line(text) -> str:
    """Text from the build, made safe for the end of a Lua comment line."""
    return " ".join(str(text).split())


def describe(piece: dict) -> str:
    if piece.get("name") is None:
        return "empty"
    return one_line(piece["name"] + (f", {piece['ash']}" if piece.get("ash") else ""))


def render(config: dict, link: str, doc: dict, gear: dict, face: str | None, notes: list[str]) -> str:
    """duelists.lua for `gear` (er-worn-gear's output). Pure: the same inputs give the same text."""
    think = config["think"]
    unresolved = gear.get("unresolved") or []
    respawn = config.get("respawn") or {}
    teams = [spot.get("team") for spot in respawn.get("at") or [] if spot.get("team") is not None]
    sides = (f"teams {', '.join(map(str, teams))}, which fight each other and never the player"
             if teams else "the teams its spots name")
    held = ("held (fight false) until fight(true) or the arena lever releases them"
            if respawn.get("fight") is False else "free to fight at once")
    lines = [
        "-- Generated by scripts/er-duelist-gear.py; do not edit. scripts/er-ai-lab.py rewrites this",
        "-- file at start and before every respawn from the planner link in",
        "-- scripts/frida/ai-lua/duelists.json, so change the build on the planner, or the link there.",
        f"-- link     {link}",
        f"-- name     {one_line(doc.get('name', ''))}",
        f"-- revision {doc.get('revision', '')}",
        "--",
        f"-- {config['summons']} duelists on Moonrithyll's body and think row ({think}), running her",
        "-- simulated-player brain (brain_moonrithyll.lua) with her face from moonrithyll.lua unless the",
        "-- build has one of its own. spawn-npc.js applies a \"think.N\" gear key to its Nth summon only,",
        "-- slot by slot over moonrithyll.lua's think-wide gear, so every slot is named below. The lab's",
        "-- \"respawn duelists\" button (POST /api/respawn {\"preset\": \"duelists\"}) spawns them with",
        "-- duelists.json's `respawn` object: one per arena spot, on Colosseum",
        f"-- {sides},",
        f"-- {held}.",
        "--",
        "-- Gear is the build's worn set as er-build-import-core resolves it (er-worn-gear): armament",
        "-- ids are the affinity row plus the level, gems are EquipParamGem rows, and a slot the build",
        "-- leaves bare gets the engine's empty value (Unarmed, the bare armour piece, -1 for a talisman).",
        "-- Not carried over: attributes, the great rune, spells and the pouch.",
    ]
    if unresolved:
        lines.append("-- Not resolved, so not worn:")
        lines += [f"--   {one_line(problem['name'])}: {one_line(problem['reason'])}" for problem in unresolved]
    for note in notes:
        lines.append(f"-- Note: {one_line(note)}")
    lines.append("")
    for key in summon_keys(config):
        lines.append(f"-- Summon {key.rsplit('.', 1)[1]}")
        for piece in gear["slots"]:
            args = [json.dumps(key), json.dumps(piece["slot"]), str(piece["id"])]
            if piece.get("gem") is not None:
                args.append(str(piece["gem"]))
            lines.append(f"lab_equip({', '.join(args)}) -- {describe(piece)}")
        if face:
            lines.append(f'lab_face({json.dumps(key)}, "{face}")')
        lines.append("")
    return "\n".join(lines)


def header_meta(text: str) -> tuple[str | None, int | None]:
    """The link and revision a generated file names in its header."""
    link = re.search(r"^-- link +(\S+)$", text, re.M)
    revision = re.search(r"^-- revision +(\d+)$", text, re.M)
    return (link.group(1) if link else None, int(revision.group(1)) if revision else None)


# ---------------------------------------------------------------------------------------------
# Commands


def generate(config_path: pathlib.Path = CONFIG, output: pathlib.Path = OUTPUT) -> dict:
    """Fetch, resolve and write. Returns a status the lab publishes; on failure the file is kept."""
    status = {"ok": False, "changed": False, "output": str(output)}
    try:
        config = load_config(config_path)
        build_id = share_id(config["build"])
        link = PLANNER + build_id
        status.update(link=link)
        doc = fetch(build_id)
        status.update(name=doc.get("name"), revision=doc.get("revision"))
        rows, notes = catalog_rows()
        gear = resolve(doc, rows)
        face = face_hex(doc, face_template(config["think"], output.parent, output))
        text = render(config, link, doc, gear, face, notes)
        old = output.read_text(encoding="utf-8") if output.exists() else None
        if text != old:
            output.write_text(text, encoding="utf-8")
        status.update(ok=True, changed=text != old, pieces=lab_pieces(config, gear, face),
                      unresolved=gear.get("unresolved") or [], notes=notes, face=face is not None,
                      slots={piece["slot"]: [piece["id"], piece.get("gem")] for piece in gear["slots"]})
    # Everything, because the caller is the lab's respawn path: a failure here is reported and the
    # spawn goes ahead on the file already on disk.
    except (Exception, SystemExit) as exc:
        status["error"] = str(exc) if isinstance(exc, GearError) else f"{type(exc).__name__}: {exc}"
        status["kept"] = output.exists()
    return status


def check(config_path: pathlib.Path = CONFIG, output: pathlib.Path = OUTPUT) -> int:
    config = load_config(config_path)
    build_id = share_id(config["build"])
    try:
        doc = fetch(build_id)
    except GearError as exc:
        print(f"cannot tell: {exc}")
        return 2
    if not output.exists():
        print(f"{output} does not exist; the link is at revision {doc.get('revision')}")
        return 1
    link, revision = header_meta(output.read_text(encoding="utf-8"))
    current = (PLANNER + build_id, doc.get("revision"))
    if (link, revision) == current:
        print(f"current: {output} is {link} revision {revision}")
        return 0
    print(f"stale: {output} is {link} revision {revision}; the config's link is {current[0]} "
          f"at revision {current[1]}")
    return 1


# ---------------------------------------------------------------------------------------------
# Selftest


def selftest() -> int:
    checks: dict[str, bool] = {}

    checks["share id from a link"] = share_id("https://er-build-planner.nyasu.business/?b=bbbf0b83524d9f") == "bbbf0b83524d9f"
    checks["share id given bare"] = share_id(" bbbf0b83524d9f ") == "bbbf0b83524d9f"
    try:
        share_id("https://er-build-planner.nyasu.business/?i=eyJ2Ijox")
        checks["a ?i= link is refused"] = False
    except GearError:
        checks["a ?i= link is refused"] = True

    names = {
        "WeaponName": {21070000: "Iron Ball", 9010000: "Moonveil", 50000000: "Bone Arrow"},
        "ProtectorName": {140100: "Bull-Goat Armor", 10000: "Head"},
        "AccessoryName": {7000: "Crimson Amber Medallion +3"},
        "ArtsName": {652: "Beast's Roar", 309: "Thops's Barrier"},
        "GemName": {65200: "Ash of War: Beast's Roar", 181: "Beast's Roar placeholder"},
    }
    reinforce = set(range(0, 26)) | set(range(2200, 2211))
    weapons = [
        {"id": 9010000, "reinforceTypeId": 2200, "weaponCategory": 3},
        {"id": 21070000, "reinforceTypeId": 0, "weaponCategory": 9},
        {"id": 50000000, "reinforceTypeId": 0, "weaponCategory": 13},
    ]
    gems = [
        # A named, iconless placeholder row below the canonical one: the canonical row wins.
        {"id": 181, "swordArtsParamId": 652, "iconId": 0},
        {"id": 65200, "swordArtsParamId": 652, "iconId": 8442},
        # Arts 309's canonical row 30900 is "No Skill" in the game; arts 0 is No Skill itself.
        {"id": 30900, "swordArtsParamId": 0, "iconId": 0},
        {"id": 10, "swordArtsParamId": 0, "iconId": 8400},
        {"id": 401, "swordArtsParamId": 309, "iconId": 0},
        {"id": 400, "swordArtsParamId": 309, "iconId": 0},
    ]
    rows = rows_from_tables(weapons, reinforce, [140100, 10000], [7000], gems, names)
    by = {(r["kind"], r["name"]): r for r in rows}
    checks["ammunition is not an armament"] = ("weapon", "Bone Arrow") not in by
    checks["somber is a +10 ceiling"] = by[("weapon", "Moonveil")]["somber"] is True
    checks["regular is not somber"] = by[("weapon", "Iron Ball")]["somber"] is False
    checks["canonical gem beats a placeholder"] = by[("ash_of_war", "Beast's Roar")]["row"] == 65200
    checks["arts 0 is No Skill, iconned row"] = by[("ash_of_war", "No Skill")]["row"] == 10
    checks["lowest row when nothing else decides"] = by[("ash_of_war", "Thops's Barrier")]["row"] == 400
    checks["rows ascend within a kind"] = [r["row"] for r in rows if r["kind"] == "protector"] == [10000, 140100]

    config = {"think": 524320000, "summons": 2}
    link = PLANNER + "bbbf0b83524d9f"
    doc = {"name": "Balls", "revision": 3}
    slots = [("right1", 1, 21070125, 65200, "Iron Ball", "Beast's Roar"), ("right2", 3, 110000, None, None, None),
             ("right3", 5, 110000, None, None, None), ("left1", 0, 110000, None, None, None),
             ("left2", 2, 110000, None, None, None), ("left3", 4, 110000, None, None, None),
             ("head", 12, 5330000, None, "Imp Head (Lion)", None), ("chest", 13, 140100, None, "Bull-Goat Armor", None),
             ("hands", 14, 140200, None, "Bull-Goat Gauntlets", None), ("legs", 15, 10300, None, None, None),
             ("talisman1", 17, 4090, None, "Ritual Shield Talisman", None), ("talisman2", 18, -1, None, None, None),
             ("talisman3", 19, 7000, None, "Crimson Amber Medallion +3", None), ("talisman4", 20, -1, None, None, None)]
    gear = {"slots": [dict(zip(("slot", "chr_asm_slot", "id", "gem", "name", "ash"), s)) for s in slots],
            "unresolved": [{"name": "Unknown Charm", "reason": "talisman not in catalog"}]}
    text = render(config, link, doc, gear, None, ["a note"])
    checks["render is pure"] = text == render(config, link, doc, gear, None, ["a note"])
    checks["header names link and revision"] = header_meta(text) == (link, 3)
    checks["one block per summon key"] = all(f'-- Summon {i}' in text for i in (0, 1)) and \
        text.count('lab_equip("524320000.0"') == 14 and text.count('lab_equip("524320000.1"') == 14
    checks["armament carries its gem"] = 'lab_equip("524320000.1", "right1", 21070125, 65200) -- Iron Ball, Beast\'s Roar' in text
    checks["empty talisman is -1"] = 'lab_equip("524320000.0", "talisman2", -1) -- empty' in text
    checks["unresolved is listed"] = "--   Unknown Charm: talisman not in catalog" in text
    checks["no face without sliders"] = "lab_face" not in text

    dialect = _load("check_ai_lua_dialect", "check-ai-lua-dialect.py")
    checks["no `#` or `%` the game's Lua rejects"] = dialect.dialect_violations(text) == []
    with_face = render(config, link, doc, gear, "46414345", [])
    lua = shutil.which("lua5.1")
    if lua:
        # The rendered file run after the real _lab.lua, as the hot reload runs it: LAB_EQUIP is
        # what the lab hands spawn-npc.js, so it has to be exactly what lab_pieces predicts.
        with tempfile.TemporaryDirectory() as work:
            mod = pathlib.Path(work) / "duelists.lua"
            mod.write_text(with_face, encoding="utf-8")
            driver = (f'dofile({json.dumps(str(LUA_DIR / "_lab.lua"))}) '
                      f'dofile({json.dumps(str(mod))}) io.write(table.concat(LAB_EQUIP, ";"))')
            run = subprocess.run([lua, "-e", driver], capture_output=True, text=True, timeout=20)
        checks["lab pieces are what _lab.lua logs"] = \
            run.returncode == 0 and run.stdout.split(";") == lab_pieces(config, gear, "46414345")
    else:
        print("skip lua5.1 not installed: lab pieces not checked against _lab.lua")

    with tempfile.TemporaryDirectory() as mods:
        mods_dir = pathlib.Path(mods)
        (mods_dir / "a.lua").write_text('lab_face(524320000, "46414345AB")\n', encoding="utf-8")
        (mods_dir / "duelists.lua").write_text('lab_face(524320000, "FFFF")\n', encoding="utf-8")
        found = face_template(524320000, mods_dir, mods_dir / "duelists.lua")
        checks["face template is the think row's own, not ours"] = found == "46414345AB"
        checks["no template for another think row"] = face_template(1, mods_dir, mods_dir / "duelists.lua") is None
    checks["a face is applied per summon"] = with_face.count('lab_face("524320000.') == 2
    checks["face pieces are logged"] = "524320000.1:face=46414345" in lab_pieces(config, gear, "46414345")
    checks["no sliders, no face"] = face_hex({"sliders": None}, "46414345") is None

    for name, ok in checks.items():
        print(f"{'ok  ' if ok else 'FAIL'} {name}")
    return 0 if all(checks.values()) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=pathlib.Path, default=CONFIG)
    parser.add_argument("--output", type=pathlib.Path, default=OUTPUT)
    parser.add_argument("--check", action="store_true", help="report whether the file is the link's current revision")
    parser.add_argument("--json", action="store_true", help="print the status as one JSON line")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if args.check:
        return check(args.config, args.output)
    status = generate(args.config, args.output)
    if args.json:
        print(json.dumps(status))
    elif status["ok"]:
        state = "rewrote" if status["changed"] else "unchanged:"
        print(f"{state} {status['output']} from {status['link']} revision {status['revision']}")
        for slot, (item, gem) in status["slots"].items():
            print(f"  {slot:<10} {item}" + (f" gem {gem}" if gem is not None else ""))
        for problem in status["unresolved"]:
            print(f"  not resolved: {problem['name']}: {problem['reason']}")
        for note in status["notes"]:
            print(f"  note: {note}")
    else:
        kept = "kept the last generated file" if status.get("kept") else "no file was written"
        print(f"failed, {kept}: {status['error']}", file=sys.stderr)
    return 0 if status["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
