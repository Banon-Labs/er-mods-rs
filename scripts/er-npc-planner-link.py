#!/usr/bin/env python3
"""Upload a named NPC's CharaInitParam row as a build planner build and print its link.

    python3 scripts/er-npc-planner-link.py sellen --row 23162
    python3 scripts/er-npc-planner-link.py sellen            # asks which row on a terminal
    python3 scripts/er-npc-planner-link.py sellen --row 23162 --dry-run
    python3 scripts/er-npc-planner-link.py --all --login-code-file ~/.config/er-mods-rs/planner-login-code

`--all` uploads one build per named NPC with a body (its lowest row), skipping any NPC the account
already holds a build for and stopping at the account's build limit. `--login-code-file` stores the
builds on your own account: it holds the code the planner's Settings page generates ("login code",
the cross-device alternative to Discord login). Without it the builds go to the mod's anonymous
session.

The NPC is found the way `er-npc-chara-init.py` finds it (loose name, the same refusals). When it
has more than one row the row is asked for on a terminal; elsewhere the rows are listed and the
script exits 2.

What goes into the build:

- gear: each slot's item under the planner's own spelling. A row the game names nothing (NPC-only
  weapons and armour) becomes the player item drawing the same model -- same `equipModelId` and
  `equipModelCategory` -- and a custom weapon row becomes its base weapon at its level with its
  ash. Anything with no planner entry is left out and listed;
- spells: `[NPC: Sorcery] X`, `X 2` and `X (Variant)` are the player's X;
- attributes: the row's own, unchanged, under the starting class needing the fewest points raised
  to reach them. NPC rows go under every class floor (Sellen has Str 6), and what fell short is
  listed rather than silently raised;
- appearance: the row's FaceParam, laid into the 264-byte payload the planner's sliders encode.
  The order is measured, not assumed: a character the game itself saved (build e36f37c0e54a8d)
  holds exactly FaceParam's sequence in the planner's 64 `unused` bytes, and the three geometry
  bytes FaceParam always leaves 0 land on the planner's `unused0..2`.

The upload is the one `planner-ash-loadout-upload.py` makes; a single build is read back and its
link printed.
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import pathlib
import re
import struct
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = pathlib.Path(HERE).parent
LAYOUT = REPO / "crates/er-build-import-core/data/planner-sliders-layout.json"


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


npc = load_module("er_npc_chara_init", "er-npc-chara-init.py")
upload = load_module("planner_ash_loadout_upload", "planner-ash-loadout-upload.py")

#: Planner schema version (`er-build-export`'s `PLANNER_VERSION`); stamping the current one makes
#: every migration on load a no-op.
PLANNER_VERSION = "3.12.2"
#: CharaInitParam rows holding the ten starting classes, named "Class - <name>".
CLASS_ROWS = range(3000, 3010)
#: Rune level of a character whose eight attributes sum to `ATTRIBUTE_BASE + level`.
ATTRIBUTE_BASE = 79
#: The planner's attribute keys, in `er-npc-chara-init.ATTRIBUTES` order. Its wire format spells
#: Endurance `vit`, so the planner document this script writes keeps that key.
STAT_KEYS = ("vig", "mnd", "vit", "str", "dex", "int", "fth", "arc")
#: EquipParamWeapon ids carry the affinity in the hundreds: 33930800 is 33930000 Magic.
AFFINITIES = ("Standard", "Heavy", "Keen", "Quality", "Fire", "Flame Art", "Lightning", "Sacred",
              "Magic", "Cold", "Poison", "Blood", "Occult")
PROTECTOR_KEYS = {"Head": "head", "Body": "body", "Arms": "arms", "Legs": "legs"}
AMMO_KEYS = {"Arrows 1": "arrow1", "Arrows 2": "arrow2", "Bolts 1": "bolt1", "Bolts 2": "bolt2"}
#: Equip positions in the planner's weapon sets: right hand 0-2, left hand 3-5.
LEFT_HAND_1 = 3
#: `[NPC: Sorcery] `, `[Incantation] `: the kind of spell, in front of the player's name.
SPELL_KIND_PREFIX = re.compile(r"^\[[^\]]*\]\s*")
#: An NPC's copies of one spell: `Loretta's Greatbow 1`, `Howl of Shabriri (Variant)`.
SPELL_VARIANT_SUFFIX = re.compile(r"\s+(\d+|\(Variant\))$")
ASH_PREFIX = "Ash of War: "
EXIT_AMBIGUOUS_ROW = 2
#: Smithbox labels the starting keepsake rows "Gift - <item>"; they are not characters.
NOT_A_CHARACTER = "Gift - "
#: Field positions in a decoded login code; see `session_from_login_code`.
CODE_EXPIRY, CODE_USER, CODE_SESSION = 1, 2, 4

# --- appearance ----------------------------------------------------------------------------

#: The payload's eight leading u32 model ids, from FaceParam's `*_partsId`.
MODEL_FIELDS = ("face", "hair", "eye", "eyebrow", "beard", "accessories", "decal", "eyelash")
GEO_COUNT, TEX_COUNT, ASYM_COUNT = 61, 36, 26
#: Payload offset 159, the byte after the last asymmetry value; 0 in a game-saved character.
SHAPE_END = 160
#: FaceParam fields from offset 160 on, one byte each; a colour is three consecutive `_R/_G/_B`.
TAIL_FIELDS = (
    "chrBodyScaleHead", "chrBodyScaleBreast", "chrBodyScaleAbdomen", "chrBodyScaleRArm",
    "chrBodyScaleRLeg", "chrBodyScaleLArm", "chrBodyScaleLLeg",
    "skin_color*", "skin_gloss", "skin_pores", "face_beard",
    "face_aroundEye", "face_aroundEyeColor*", "face_cheek", "face_cheekColor*",
    "face_eyeLine", "face_eyeLineColor*", "face_eyeShadowDown", "face_eyeShadowDownColor*",
    "face_eyeShadowUp", "face_eyeShadowUpColor*", "face_lip", "face_lipColor*",
    "decal_posX", "decal_posY", "decal_angle", "decal_scale", "decal_color*", "decal_gloss",
    "decal_mirror", "body_hair", "body_hairColor*",
    "eyeR_irisColor*", "eyeR_irisScale", "eyeR_cataract", "eyeR_cataractColor*",
    "eyeR_scleraColor*", "eyeR_irisDistance",
    "eyeL_irisColor*", "eyeL_irisScale", "eyeL_cataract", "eyeL_cataractColor*",
    "eyeL_scleraColor*", "eyeL_irisDistance",
    "hair_color*", "hair_shininess", "hair_rootBlack", "hair_whiteDensity",
    "beard_color*", "beard_shininess", "beard_rootBlack", "beard_whiteDensity",
    "eyebrow_color*", "eyebrow_shininess", "eyebrow_rootBlack", "eyebrow_whiteDensity",
    "eyelash_color*", "accessories_color*", "burn_scar",
)
SLIDER_BYTES = 264


def tail_names(field: str) -> list[str]:
    """`skin_color*` is `skin_color_R`, `skin_color_G`, `skin_color_B`."""
    return [f"{field[:-1]}_{c}" for c in "RGB"] if field.endswith("*") else [field]


def face_payload(face: dict) -> bytes:
    """FaceParam as the planner's 264-byte slider payload (`FaceDataBuffer` bytes 12..276)."""
    out = bytearray(struct.pack("<8I", *(face[f"{m}_partsId"] for m in MODEL_FIELDS)))
    out += bytes(face[k] for k in ("age", "gender", "caricatureGeometry", "caricatureTexture"))
    out += bytes(face[f"faceGeoData{i:02}"] for i in range(GEO_COUNT))
    out += bytes(face[f"faceTexData{i:02}"] for i in range(TEX_COUNT))
    out += bytes(face[f"faceGeoAsymData{i:02}"] for i in range(ASYM_COUNT))
    out += bytes(SHAPE_END - len(out))
    for field in TAIL_FIELDS:
        out += bytes(face[n] for n in tail_names(field))
    out += bytes(SLIDER_BYTES - len(out))
    if len(out) != SLIDER_BYTES:
        raise ValueError(f"face payload is {len(out)} bytes, not {SLIDER_BYTES}")
    return bytes(out)


def decode_sliders(payload: bytes, layout: list[dict]) -> dict:
    """The planner's `sliders.sliders` object, as `er_build_import_core::sliders::decode` writes it."""
    out = {}
    for field in layout:
        at, key = field["offset"], field["key"]
        if field["size"] == 4:
            value = struct.unpack_from("<I", payload, at)[0]
            if key == "faceModelId":
                musculature = int(value >= 100)
                rest = value - 100 * musculature
                out.update(age=rest % 10, boneStructure=rest // 10 * 10, musculature=musculature)
            else:
                out[key] = value
        elif field["size"] == 3:
            out[key] = list(payload[at:at + 3])
        else:
            out[key] = payload[at]
    return out


# --- gear ----------------------------------------------------------------------------------

class Catalogue:
    """Game rows and their planner spellings, with the model fallback for unnamed rows."""

    def __init__(self, decoder, regulation: str | None):
        self.d = decoder
        files = decoder.pr.load(regulation)
        self.rows = {}
        for stem in ("EquipParamWeapon", "EquipParamProtector"):
            rows, _, _ = decoder.pr.rows(decoder.pr.param_bytes(files, stem), strict=False)
            self.rows[stem] = {r["id"]: r for r in rows}
        self.known = json.loads(upload.PLANNER_NAMES.read_text(encoding="utf-8"))
        self.reset()

    def reset(self) -> None:
        """Start a new build's lists of what was substituted and what was left out."""
        self.missing: list[str] = []
        self.substituted: list[str] = []

    def spelled(self, name: str | None) -> str | None:
        return self.known.get(upload.fold(name)) if name else None

    def by_model(self, stem: str, row_id: int) -> str | None:
        """The planner item sharing `row_id`'s model, or None. Weapons and armour only."""
        row = self.rows.get(stem, {}).get(row_id)
        if row is None or row["equipModelId"] <= 0:
            return None
        key = (row["equipModelId"], row["equipModelCategory"])
        names = self.d.names(stem)
        for other_id in sorted(self.rows[stem]):
            other = self.rows[stem][other_id]
            if other_id == row_id or (other["equipModelId"], other["equipModelCategory"]) != key:
                continue
            if stem == "EquipParamWeapon" and other_id % 10000:
                continue
            spelled = self.spelled(names.get(other_id))
            if spelled:
                return spelled
        return None

    def item(self, stem: str, row_id: int, slot: str) -> str | None:
        name = self.d.names(stem).get(row_id)
        spelled = self.spelled(name)
        if spelled:
            return spelled
        spelled = self.by_model(stem, row_id)
        if spelled:
            self.substituted.append(f"{slot}: {row_id} {name or 'no name'} -> {spelled} (same model)")
            return spelled
        self.missing.append(f"{slot}: {row_id} {name or 'no name'}")
        return None

    def weapon(self, weapon_id: int, table: int, slot: str) -> dict | None:
        level, ash = None, None
        if table == npc.CUSTOM_WEAPON_TYPE:
            custom = self.d.tables["EquipParamCustomWeapon"].get(weapon_id)
            if custom is None:
                self.missing.append(f"{slot}: custom weapon {weapon_id} has no row")
                return None
            weapon_id, level = custom["baseWepId"], custom.get("reinforceLv", 0)
            gem = custom.get("gemId", npc.NO_ID)
            gem_name = self.d.names("EquipParamGem").get(gem, "") if gem > 0 else ""
            ash = gem_name[len(ASH_PREFIX):] if gem_name.startswith(ASH_PREFIX) else None
        else:
            level = weapon_id % 100
        affinity = AFFINITIES[(weapon_id % 10000) // 100] if (weapon_id % 10000) // 100 < len(AFFINITIES) else None
        base = weapon_id - weapon_id % 10000
        name = self.item("EquipParamWeapon", base, slot)
        if name is None:
            return None
        return {"name": name, "upgrade": level, "infusion": affinity or "Standard", "weaponArt": ash}

    def spell(self, spell_id: int, slot: str) -> str | None:
        raw = self.d.spell(spell_id)
        plain = SPELL_KIND_PREFIX.sub("", raw)
        spelled = self.spelled(plain) or self.spelled(SPELL_VARIANT_SUFFIX.sub("", plain))
        if spelled is None:
            self.missing.append(f"{slot}: {spell_id} {raw}")
        return spelled


# --- the document --------------------------------------------------------------------------

def sorting() -> dict:
    return {"method": "acquisition", "direction": "asc"}


def closest_class(decoder, stats: list[int]) -> tuple[str, list[str]]:
    """The starting class needing the fewest points raised to reach `stats`, and the shortfalls."""
    names = decoder.names("CharaInitParam")
    best = None
    for row_id in CLASS_ROWS:
        row = decoder.tables["CharaInitParam"][row_id]
        floor = [row[fid] for _, fid in npc.ATTRIBUTES]
        short = sum(max(0, f - s) for f, s in zip(floor, stats))
        if best is None or short < best[0]:
            best = (short, names[row_id].removeprefix("Class - "), floor)
    _, name, floor = best
    shortfalls = [f"{short} {s} < {f}" for (short, _), s, f in zip(npc.ATTRIBUTES, stats, floor) if s < f]
    return name, shortfalls


def build_document(decoder, cat: Catalogue, who: str, row_id: int, layout: list[dict]) -> tuple[dict, list[str]]:
    row = decoder.tables["CharaInitParam"][row_id]
    notes: list[str] = []

    weapons = []
    for label, fid, tfield in npc.WEAPON_SLOTS:
        if row.get(fid, npc.NO_ID) > 0:
            slot = cat.weapon(row[fid], row.get(tfield, 0), label)
            if slot:
                hand, n = label.split()
                pos = (LEFT_HAND_1 if hand == "Left" else 0) + int(n) - 1
                weapons.append({**slot, "order": len(weapons), "equipSet": [pos], "equipIndex": pos})

    protectors = {key: {"slots": [], "sorting": sorting()} for key in PROTECTOR_KEYS.values()}
    for label, fid in npc.ARMOUR_SLOTS:
        if row.get(fid, npc.NO_ID) > 0:
            name = cat.item("EquipParamProtector", row[fid], label)
            if name:
                protectors[PROTECTOR_KEYS[label]]["slots"].append({
                    "name": name, "order": 0, "equipSet": [upload.PROTECTOR_EQUIP_INDEX],
                    "equipIndex": upload.PROTECTOR_EQUIP_INDEX})

    talismans = []
    for pos, fid in enumerate(npc.TALISMAN_FIELDS):
        if row.get(fid, npc.NO_ID) > 0:
            name = cat.item("EquipParamAccessory", row[fid], f"Talisman {pos + 1}")
            if name:
                talismans.append({"name": name, "order": len(talismans), "equipSet": [pos], "equipIndex": pos})

    ammo = {}
    for label, fid, _ in npc.AMMO_SLOTS:
        if row.get(fid, npc.NO_ID) > 0:
            name = cat.item("EquipParamWeapon", row[fid] - row[fid] % 10000, label)
            if name:
                ammo[AMMO_KEYS[label]] = name

    spells = []
    for i, fid in enumerate(npc.SPELL_FIELDS, 1):
        if row.get(fid, npc.NO_ID) > 0:
            name = cat.spell(row[fid], f"Spell {i}")
            if name and all(s["name"] != name for s in spells):  # `Loretta's Greatbow 1` and `2`
                spells.append({"name": name, "order": len(spells)})

    stats = [row[fid] for _, fid in npc.ATTRIBUTES]
    class_name, shortfalls = closest_class(decoder, stats)
    if shortfalls:
        notes.append(f"under the {class_name} floor, kept as the NPC has them: {', '.join(shortfalls)}")

    face_id = row.get("npcPlayerFaceGenId", npc.NO_ID)
    face = decoder.tables["FaceParam"][face_id]
    sliders = decode_sliders(face_payload(face), layout)
    # npcPlayerSex is 0 on every female NPC checked (Sellen, Nepheli, Millicent, Roderika) and 1 on
    # every male one (Varre, Patches, Blackguard); the planner's B is the feminine body.
    body = "B" if row.get("npcPlayerSex", 1) == 0 else "A"

    doc = {
        "buildUrl": "", "description": "", "id": "",
        "inventory": {"slots": weapons, "sorting": sorting()},
        "talismans": {"slots": talismans, "sorting": sorting()},
        "name": f"{who} ({row_id})",
        "stats": {**dict(zip(STAT_KEYS, stats)), "rl": sum(stats) - ATTRIBUTE_BASE},
        "spells": {"slots": spells},
        "protectors": protectors,
        "items": {"ammo": ammo, "tools": {"slots": [], "sorting": sorting()}, "crystalTears": [None, None],
                  "flasks": {"crimson": 10, "cerulean": 4, "total": 14, "level": 12}},
        "characterClass": class_name,
        "sliders": {"id": "", "bodyType": body, "name": f"{who} (FaceParam {face_id})",
                    "images": [{"url": "", "geometry": {"x": 0, "y": 0, "width": 0, "height": 0, "zoom": 1}}],
                    "sliders": sliders},
        "version": PLANNER_VERSION, "author": None,
        "weaponUpgrade": max([w["upgrade"] or 0 for w in weapons], default=0),
        "is2h": False, "isPvE": False,
        "views": {"overview": "build-planner"},
        "conditions": {"forEffects": [], "crystalTears": False},
        "sets": {k: [{"name": "Default", "active": True}] for k in ("weapons", "talismans", "protectors")},
        "tags": [], "customEffects": [],
        # The planner's own state keys, empty: without them its merge drops `sliders` (a990b562).
        "computed": {}, "activeEffects": [], "pve": {}, "current": {},
    }
    notes += [f"substituted {s}" for s in cat.substituted] + [f"left out, no planner entry: {m}" for m in cat.missing]
    doc["description"] = (f"Read out of regulation.bin: CharaInitParam {row_id}, FaceParam {face_id}, "
                          f"by er-npc-planner-link.py." + "".join(f"\n- {n}" for n in notes))
    return doc, notes


# --- choosing and uploading ----------------------------------------------------------------

def choose_row(found, rows: list[int], wanted: int | None) -> int:
    if wanted is not None:
        if wanted not in rows:
            sys.exit(f"{found.name} has no row {wanted}; its rows are {', '.join(map(str, rows))}")
        return wanted
    if len(rows) == 1:
        return rows[0]
    listing = "\n".join(f"  {i}. {r}" for i, r in enumerate(rows, 1))
    if not sys.stdin.isatty():
        print(f"{found.name} has {len(rows)} rows; pass --row:\n{listing}", file=sys.stderr)
        sys.exit(EXIT_AMBIGUOUS_ROW)
    print(f"{found.name} has {len(rows)} rows:\n{listing}")
    while True:
        answer = input(f"Pick 1-{len(rows)}: ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(rows):
            return rows[int(answer) - 1]


def session_from_login_code(text: str) -> dict:
    """The planner's login code as `{"session", "user", "expires"}`.

    The code is base64 of `random;expiry;b64(userId);b7d44a1b;b64(sessionId);QQ`, read from the
    `LoginCodeTool` component of its Settings chunk (PreferencesView-BcQxlwje.js), whose importer
    takes fields 1, 2 and 4. It carries the account's own session id; the 24-hour expiry is the
    planner's own check on importing the code, not something the API enforces.
    """
    fields = base64.b64decode(text.strip()).decode().split(";")
    if len(fields) <= CODE_SESSION:
        raise ValueError(f"a login code has six fields, this one {len(fields)}")
    return {"user": base64.b64decode(fields[CODE_USER]).decode(),
            "session": base64.b64decode(fields[CODE_SESSION]).decode(),
            "expires": int(fields[CODE_EXPIRY])}


def headers(session: dict) -> dict:
    return {"Authorization": f"Basic {session['session']}", "User": session["user"]}


def load_session(login_code_file: pathlib.Path | None) -> dict:
    if login_code_file is None:
        return json.loads(upload.SESSION.read_text(encoding="utf-8"))
    text = login_code_file.expanduser().read_text(encoding="utf-8").strip()
    if not text:
        sys.exit(f"{login_code_file} is empty; paste the planner's login code into it")
    try:
        session = session_from_login_code(text)
    except ValueError as e:
        sys.exit(f"{login_code_file} does not hold a planner login code: {e}")
    query = urllib.parse.urlencode({"q": session["user"], "s": session["session"]})
    check = upload.request(f"{upload.API.rsplit('/', 1)[0]}/is-valid-uid?{query}")
    if not check.get("valid"):
        sys.exit("the planner says that login code's session is not valid")
    name = base64.b64decode(check["result"]).decode() if check.get("result") else "?"
    print(f"account: {name}")
    return session


def store(doc: dict, update: str, session: dict) -> str:
    stored = upload.request(upload.API, {"id": update, "data": doc, "gameId": 1, "version": doc["version"]},
                            headers(session))
    new_id = stored.get("id")
    if not new_id:
        sys.exit(f"upload refused: {stored}")
    return new_id


def make_public(build_id: str, session: dict) -> bool:
    """The planner's `setInventoryVisibility`: `PATCH /inventories/<id>/visibility {isPublic}`."""
    req = urllib.request.Request(f"{upload.API}/{build_id}/visibility", method="PATCH",
                                 data=json.dumps({"isPublic": True}).encode(),
                                 headers={"User-Agent": upload.USER_AGENT, "Content-Type": "application/json",
                                          **headers(session)})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            json.load(resp)
        return True
    except urllib.error.HTTPError as e:
        print(f"  {build_id}: HTTP {e.code} {e.read()[:200]!r}", file=sys.stderr)
        return False


def account_builds(session: dict) -> list[dict]:
    """Every build the session's account stores: `{id, data, createdAt, isPublic}`."""
    out, page, pages = [], 1, 1
    while page <= pages:
        listed = upload.request(f"{upload.API}?page={page}", headers=headers(session))
        out += listed.get("data") or []
        pages = listed.get("pagination", {}).get("pages", 0)
        page += 1
    return out


def named_npcs(index: dict, npc_names: dict[int, str]) -> list:
    """One entry per character with a body: the game's own names first, then Smithbox's."""
    game = {npc.fold(n) for n in npc_names.values()}
    unique = {id(v): v for v in index.values() if v.rows and not v.name.startswith(NOT_A_CHARACTER)}
    return sorted(unique.values(), key=lambda v: (not (v.aliases & game), v.name))


def upload_all(decoder, cat: Catalogue, index: dict, npc_names: dict[int, str], layout: list[dict],
               session: dict | None, publish: bool) -> int:
    everyone = named_npcs(index, npc_names)
    held = set()
    room = len(everyone)
    if session is not None:
        held = {b["data"].get("name", "") for b in account_builds(session)}
        limits = upload.request(f"{upload.API.rsplit('/', 1)[0]}/limits", headers=headers(session))
        room = limits["limits"]["build"] - limits["current"]["inventories"]
        print(f"account holds {limits['current']['inventories']} of {limits['limits']['build']} builds")
    todo = [v for v in everyone if not any(h.startswith(f"{v.name} (") for h in held)]
    print(f"{len(everyone)} characters, {len(everyone) - len(todo)} already on the account, "
          f"room for {room} more")
    if session is not None and todo and room > 0 and sys.stdin.isatty():
        if input(f"Store {min(len(todo), room)} builds on this account? [y/N] ").strip().lower() != "y":
            print("nothing uploaded")
            return 0
    stored = []
    for who in todo[:max(room, 0)]:
        row_id = min(who.rows)
        cat.reset()
        doc, notes = build_document(decoder, cat, who.name, row_id, layout)
        if session is None:
            print(f"  {who.name} ({row_id}): {len(notes)} notes")
            continue
        new_id = store(doc, "", session)
        stored.append(new_id)
        print(f"  {who.name} ({row_id})  {upload.PLANNER}{new_id}" + (f"  [{len(notes)} notes]" if notes else ""))
    left = todo[max(room, 0):]
    if left:
        print(f"not uploaded, the account is full: {', '.join(v.name for v in left)}")
    if session is None:
        return 0
    builds = account_builds(session)
    missing = [i for i in stored if i not in {b["id"] for b in builds}]
    print(f"stored {len(stored)}; the account lists all of them" if not missing
          else f"stored {len(stored)}; the account does not list {missing}")
    if not publish:
        return 1 if missing else 0
    # Only this script's own builds: the exact names it writes, `<NPC> (<row>)`.
    ours = {f"{v.name} ({r})" for v in everyone for r in v.rows}
    private = [b for b in builds if b["data"].get("name") in ours and not b.get("isPublic")]
    print(f"making {len(private)} NPC builds public")
    failed = [b["id"] for b in private if not make_public(b["id"], session)]
    still = [b["id"] for b in account_builds(session) if b["data"].get("name") in ours and not b.get("isPublic")]
    print("every NPC build on the account is public" if not still else f"still private: {still}")
    return 1 if missing or failed or still else 0


def selftest() -> int:
    layout = json.loads(LAYOUT.read_text(encoding="utf-8"))
    failed = 0

    def check(what: str, ok: bool) -> None:
        nonlocal failed
        failed += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {what}")

    face = {f"{m}_partsId": 0 for m in MODEL_FIELDS}
    face.update(face_partsId=141, hair_partsId=109, age=1, gender=2, caricatureGeometry=3, caricatureTexture=4)
    face.update({f"faceGeoData{i:02}": 128 for i in range(GEO_COUNT)})
    face.update(faceGeoData00=10, faceGeoData21=0, faceGeoData26=0, faceGeoData29=0, faceGeoData60=127)
    face.update({f"faceTexData{i:02}": 5 for i in range(TEX_COUNT)})
    face.update({f"faceGeoAsymData{i:02}": 6 for i in range(ASYM_COUNT)})
    for field in TAIL_FIELDS:
        for n in tail_names(field):
            face[n] = 7
    face.update(skin_color_R=1, skin_color_G=2, skin_color_B=3, decal_gloss=9, decal_mirror=1, burn_scar=8)
    payload = face_payload(face)
    s = decode_sliders(payload, layout)
    check("payload is 264 bytes", len(payload) == SLIDER_BYTES)
    check("faceModelId 141 splits as the Rust codec does", (s["age"], s["boneStructure"], s["musculature"]) == (1, 40, 1))
    check("hair model id lands on hairModelId", s["hairModelId"] == 109)
    check("FaceParam age/gender/caricature land on the first four shape bytes",
          [s[k] for k in ("apparentAge", "facialAesthetic", "formEmphasis", "unkNumen128")] == [1, 2, 3, 4])
    check("geo 00 is the brow ridge, the first named shape slider", s["browRidgeHeight"] == 10)
    check("geo 21/26/29, always 0 in FaceParam, land on unused0..2",
          [s[f"unused{i}"] for i in range(3)] == [0, 0, 0])
    check("geo 60 is the first of the 64 unused bytes", s["unused3"] == 127)
    check("tex then asym fill unused4..65, unused66 stays 0",
          [s[f"unused{i}"] for i in range(4, 67)] == [5] * TEX_COUNT + [6] * ASYM_COUNT + [0])
    check("skin colour is RGB in order", s["skinColour"] == [1, 2, 3])
    check("decal gloss is the unnamed byte before decalFlip", s["unused67"] == 9 and s["decalFlip"] == 1)
    check("burn scar is frenziedFlame", s["frenziedFlame"] == 8)
    check("every layout key is written once",
          set(s) == {f["key"] for f in layout} - {"faceModelId"} | {"age", "boneStructure", "musculature"})

    def b64(text: str) -> str:
        return base64.b64encode(text.encode()).decode()

    # The planner's own generator: btoa([rand8, expiry, btoa(user), "b7d44a1b", btoa(session), "QQ"].join(";")).
    code = b64(";".join(["12345678", "1760400000", b64("user-1"), "b7d44a1b", b64("sess-2"), "QQ"]))
    check("a login code decodes to its user, session and expiry",
          session_from_login_code(f"  {code}\n") == {"user": "user-1", "session": "sess-2", "expires": 1760400000})
    try:
        session_from_login_code(b64("only;three;fields"))
        check("a short code is refused", False)
    except ValueError:
        check("a short code is refused", True)
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", nargs="*", help="the NPC's name, loosely: 'sellen'")
    ap.add_argument("--pick", type=int, help="take the Nth NPC when the name is ambiguous")
    ap.add_argument("--row", type=int, help="the CharaInitParam row to upload, when the NPC has several")
    ap.add_argument("--regulation", help="regulation.bin to read (default: the installed game's)")
    ap.add_argument("--update", default="", metavar="ID",
                    help="overwrite this session's build ID instead of storing a new one")
    ap.add_argument("--all", action="store_true", help="one build per named NPC with a body")
    ap.add_argument("--publish", action="store_true",
                    help="with --all: make every NPC build on the account public, new and already stored")
    ap.add_argument("--login-code-file", type=pathlib.Path,
                    help="a file holding the planner's Settings login code; the builds go to that account")
    ap.add_argument("--dry-run", action="store_true", help="print the document, upload nothing")
    ap.add_argument("--selftest", action="store_true", help="check the face mapping offline")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.name and not a.all:
        ap.error("name the NPC, or pass --all")

    decoder = npc.Decoder(a.regulation)
    item_names = load_module("er_item_name", "er-item-name.py")
    npc_names = item_names.game_names("NpcName")
    index = npc.build_index(decoder.names("CharaInitParam"), npc_names, decoder.body_rows())
    layout = json.loads(LAYOUT.read_text(encoding="utf-8"))
    if a.all:
        session = None if a.dry_run else load_session(a.login_code_file)
        return upload_all(decoder, Catalogue(decoder, a.regulation), index, npc_names, layout, session, a.publish)

    query = " ".join(a.name)
    found, exact = npc.candidates(query, index)
    who = npc.choose(query, found, exact, a.pick)
    if who.rejected:
        print(f"{who.name}: matched, but it has no body to read: {who.rejected}", file=sys.stderr)
        return npc.EXIT_REJECTED
    row_id = choose_row(who, sorted(who.rows), a.row)

    doc, notes = build_document(decoder, Catalogue(decoder, a.regulation), who.name, row_id, layout)
    for n in notes:
        print(f"  {n}")
    if a.dry_run:
        print(json.dumps(doc, indent=1, ensure_ascii=False))
        return 0
    new_id = store(doc, a.update, load_session(a.login_code_file))
    back = upload.request(f"{upload.API}/{new_id}")
    sent = back.get("sliders", {}).get("sliders", {})
    same = sent == doc["sliders"]["sliders"]
    print(f"{back['name']}  {upload.PLANNER}{new_id}")
    print(f"  read back: {len(sent)} slider values ({'as sent' if same else 'differ from what was sent'}), "
          f"{len(back['inventory']['slots'])} weapons, {len(back['spells']['slots'])} spells, "
          f"class {back['characterClass']}, keys {len(back)}")
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
