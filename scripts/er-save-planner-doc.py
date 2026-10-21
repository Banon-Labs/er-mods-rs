#!/usr/bin/env python3
"""A save slot's character as an er-build-planner document, read offline, and the upload of every
save character the planner account does not already hold.

    python3 scripts/er-save-planner-doc.py <ER0000.sl2> --slot 0              # print the document
    python3 scripts/er-save-planner-doc.py <ER0000.sl2> --slot 0 --compare <build.json>
    python3 scripts/er-save-planner-doc.py <ER0000.sl2> --slot 0 --dump       # the raw sections
    python3 scripts/er-save-planner-doc.py --upload --dry-run                 # what would be uploaded
    python3 scripts/er-save-planner-doc.py --upload
    python3 scripts/er-save-planner-doc.py --selftest

This is the in-game exporter (`er_quickload_request_build_document`, crates/er-build-import-runtime
`read_character.rs` + `export_doc.rs`) done from the save file instead of from game memory, so a
character can be put on the planner without booting it. The rules are that exporter's, one for one:

- armaments, armour and talismans are everything carried, ordered by the inventory's acquisition
  key, each stacked entry expanded into one row per copy, the worn ones marked with their equip
  position (armaments matched to the worn copy by gaitem handle, the rest by name);
- ammunition, `Unarmed`, goods and loose ashes of war are not inventory rows; the four equipped
  ammunition positions, the quickbar and pouch, the physick, the great rune and the memorised
  spells are written where the planner keeps them;
- an ash of war is written only when a gem the game would let this armament mount grants a skill
  that is not the armament's own;
- `weaponUpgrade` is the highest armament level on the regular-stone scale;
- the appearance is the slot's `FaceDataBuffer` decoded into the planner's slider object, on body
  B when `PlayerGameData::gender` is 1.

Names come from the game's own message files (`scripts/er-item-name.py`), the same FMGs the
exporter's name getters read, and param facts from the installed `regulation.bin`.

# The save layout

A `USER_DATA` slot is `[MD5 0x10][version][map id][0x18]`, then the `CSGaitemImp` map: 0x1400
entries of `{handle, item id}`, a weapon entry carrying 13 more bytes (the mounted gem's handle at
+8 of them) and an armour entry 8 more. `PlayerGameData` (0x1b0) follows the map directly, then the
special effects (0xd0) and the equipment sections `er-profile-summary-core`'s `serialized_slot.rs`
walks: equip indices, arm style, the 22 `ChrAsm` param ids, their gaitem handles, the inventory
(0xa80 common entries of `{handle, quantity, sort id}`), the 14 spell slots, the quickbar and pouch
with the equipped great rune after them, gestures, projectiles, the 39 equipped item ids, the
physick and the face.

# Upload

`--upload` takes every character in the save folders (`ER_PORTRAIT_SAVES`) the way
`er-portrait-batch.py --saves` does -- a name at a level, from its newest copy, starting-class
fillers skipped -- and stores each one the account holds no build for at that name and rune level
as a new private build named `<name> (RL<level>)`, through the account in
`~/.config/er-mods-rs/planner-login-code`. Every stored build is read back.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import re
import struct
import sys
import unicodedata

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
LAYOUT = REPO / "crates/er-build-import-core/data/planner-sliders-layout.json"
PLANNER_SCRIPTS = pathlib.Path(os.environ.get("ER_NPC_PLANNER_SCRIPTS", REPO / ".worktrees/pr-npc-planner/scripts"))
PLANNER_NAMES = PLANNER_SCRIPTS.parent / "data/planner-item-names.json"
LOGIN_CODE = pathlib.Path.home() / ".config/er-mods-rs/planner-login-code"
PLANNER_SITE = "https://er-build-planner.nyasu.business/?b="


def module(name: str, path: pathlib.Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


oracle = module("save_slot_oracle", HERE / "save-slot-oracle.py")
item_names = module("er_item_name", HERE / "er-item-name.py")

# --- save layout ---------------------------------------------------------------------------

GAITEM_MAP_OFFSET = 0x30
GAITEM_COUNT = 0x1400
GAITEM_WEAPON_EXTRA = 13
GAITEM_WEAPON_GEM_HANDLE = 8
GAITEM_PROTECTOR_EXTRA = 8
PGD_SIZE = 0x1B0
SPEFFECT_SIZE = 0xD0
# In the order the game serialises them after the special effects.
SECTIONS_BEFORE_PROJECTILES = (("indices", 0x58), ("arm_style", 0x1C), ("param_ids", 0x58), ("handles", 0x58),
                               ("inventory", 0x9010), ("magic", 0x74), ("items", 0x8C), ("gestures", 0x18))
PROJECTILE_ENTRY = 8
SECTIONS_AFTER_PROJECTILES = (("equipped", 0x9C), ("physick", 0x0C), ("face", 0x12F))
CHR_ASM_SLOTS = 22
COMMON_ENTRIES = 0xA80
INVENTORY_ENTRY = 12
SPELL_SLOTS = 14
SPELL_ENTRY = 8
# `items` is ten quickbar `{handle, index}` pairs, the selected quickbar position, six pouch pairs,
# then the equipped great rune's pair.
GREAT_RUNE_ITEM_WORD = 33
# `equipped` holds one category-tagged item id per `ChrAsmSlot`: the 22 gear slots, then the
# quickbar and the pouch.
QUICKBAR_FIRST = 22
QUICKBAR_SLOTS = 10
POUCH_SLOTS = 6

EMPTY = (0, 0xFFFFFFFF)
CATEGORY_MASK = 0xF0000000
ROW_MASK = 0x0FFFFFFF
HANDLE_WEAPON = 0x80000000
HANDLE_PROTECTOR = 0x90000000
HANDLE_ACCESSORY = 0xA0000000
ITEM_GOODS = 0x40000000

# --- the exporter's tables (er-build-import-core, er-build-import-runtime) -------------------

# Planner armament index -> `ChrAsmSlot`: right hand 1..3, then left hand 1..3.
ARMAMENT_CHR_ASM_SLOTS = (1, 3, 5, 0, 2, 4)
AMMO_CHR_ASM_FIRST = 6
# `ChrAsmSlot` order, which interleaves arrows and bolts.
AMMO_POSITION_KEYS = ("arrow1", "bolt1", "arrow2", "bolt2")
PROTECTOR_CHR_ASM_FIRST = 12
PROTECTOR_PARTS = ("head", "body", "arms", "legs")
PROTECTOR_PART_FIELDS = (("headEquip", "head"), ("bodyEquip", "body"), ("armEquip", "arms"), ("legEquip", "legs"))
# The planner's writer marks a worn armour piece with 1, whichever part it is.
PROTECTOR_EQUIP_INDEX = 1
TALISMAN_CHR_ASM_FIRST = 17
TALISMAN_SLOTS = 4
STARTING_CLASSES = ("Vagabond", "Warrior", "Hero", "Bandit", "Astrologer", "Prophet", "Confessor", "Samurai",
                    "Prisoner", "Wretch", "Idus Knight", "Heavy Knight")
INFUSIONS = ("Standard", "Heavy", "Keen", "Quality", "Fire", "Flame Art", "Lightning", "Sacred", "Magic", "Cold",
             "Poison", "Blood", "Occult")
ARMAMENT_LEVEL_STEP = 100
ARMAMENT_ID_BLOCK = 10_000
UNARMED_ARMAMENT_ROW = 110_000
AMMUNITION_WEAPON_TYPES = (81, 83, 85, 86)
MAX_COPIES_PER_ENTRY = 999
MAX_REGULAR_LEVEL = 25
MAX_SOMBER_LEVEL = 10
SOMBER_LEVEL_FOR_REGULAR = (0, 0, 1, 1, 1, 2, 2, 3, 3, 3, 4, 4, 5, 5, 5, 6, 6, 7, 7, 7, 8, 8, 9, 9, 9, 10)
MATCHING_WEAPON_LEVEL_MAX = 25
ARM_STYLE_TWO_HANDED = (2, 3)
GENDER_BODY_B = 1
PLANNER_VERSION = "3.12.2"
# `CheckIfWepTypeCanEquipGem`: the `canMountWep_*` flag each `wepType` selects (gem_mount.rs).
GEM_MOUNT_FIELD = {
    0x01: "Dagger", 0x03: "SwordNormal", 0x05: "SwordLarge", 0x07: "SwordGigantic", 0x09: "SaberNormal",
    0x0B: "SaberLarge", 0x0D: "katana", 0x0E: "SwordDoubleEdge", 0x0F: "SwordPierce", 0x10: "RapierHeavy",
    0x11: "AxeNormal", 0x13: "AxeLarge", 0x15: "HammerNormal", 0x17: "HammerLarge", 0x18: "Flail",
    0x19: "SpearNormal", 0x1B: "SpearLarge", 0x1C: "SpearHeavy", 0x1D: "SpearAxe", 0x1F: "Sickle",
    0x23: "Knuckle", 0x25: "Claw", 0x27: "Whip", 0x29: "AxhammerLarge", 0x32: "BowSmall", 0x33: "BowNormal",
    0x35: "BowLarge", 0x37: "ClossBow", 0x38: "Ballista", 0x39: "Staff", 0x3B: "Sorcery", 0x3D: "Talisman",
    0x41: "ShieldSmall", 0x43: "ShieldNormal", 0x45: "ShieldLarge", 0x57: "Torch", 0x58: "HandToHand",
    0x59: "PerfumeBottle", 0x5A: "ThrustingShield", 0x5B: "ThrowingWeapon", 0x5C: "ReverseHandSword",
    0x5D: "LightGreatsword", 0x5E: "GreatKatana", 0x5F: "BeastClaw",
}
# The face buffer and the planner's slice of it (er_build_import_core::sliders).
FACE_MAGIC = b"FACE"
FACE_BUFFER_LEN = 0x120
FACE_BUFFER_VERSION = 4
FACE_PAYLOAD_OFFSET = 12
SLIDER_BYTES = 264
FACE_MODEL_ID = "faceModelId"
AGE_MODULUS = 10
MUSCULATURE_STEP = 100


class Slot:
    """One `USER_DATA` slot, cut into the sections the exporter's reads correspond to."""

    def __init__(self, path: pathlib.Path, slot: int):
        data = path.read_bytes()
        self.path, self.slot = path, slot
        self.body, _ = oracle.extract_slot(data, slot)
        decoded = oracle.decode_save_slot(data, path, slot)
        self.fields = decoded["decoded_fields"]
        self.pgd = int(decoded["layout_offsets"]["player_game_data"], 16)
        self.gaitems, gaitem_end = self._gaitems()
        if gaitem_end != self.pgd:
            raise ValueError(f"{path} slot {slot}: the gaitem map ends at {gaitem_end:#x}, "
                             f"not at PlayerGameData {self.pgd:#x}")
        o = self.pgd + PGD_SIZE + SPEFFECT_SIZE
        self.sections = {}
        for name, size in SECTIONS_BEFORE_PROJECTILES:
            self.sections[name] = o
            o += size
        self.sections["projectiles"] = o
        o += 4 + self.u32(o) * PROJECTILE_ENTRY
        for name, size in SECTIONS_AFTER_PROJECTILES:
            self.sections[name] = o
            o += size

    def u32(self, offset: int) -> int:
        return struct.unpack_from("<I", self.body, offset)[0]

    def words(self, section: str, count: int, stride: int = 4, start: int = 0) -> list[int]:
        base = self.sections[section] + start
        return [self.u32(base + stride * i) for i in range(count)]

    def _gaitems(self) -> tuple[dict[int, tuple[int, int | None]], int]:
        """`{handle: (item id, mounted gem handle)}` and where the map ends."""
        out, o = {}, GAITEM_MAP_OFFSET
        for _ in range(GAITEM_COUNT):
            handle, item_id = struct.unpack_from("<II", self.body, o)
            o += 8
            if handle == 0:
                continue
            kind = handle & CATEGORY_MASK
            gem = None
            if kind == HANDLE_WEAPON:
                gem = self.u32(o + GAITEM_WEAPON_GEM_HANDLE)
                o += GAITEM_WEAPON_EXTRA
            elif kind == HANDLE_PROTECTOR:
                o += GAITEM_PROTECTOR_EXTRA
            out[handle] = (item_id, gem)
        return out, o

    def inventory(self) -> list[tuple[int, int, int]]:
        """Every occupied common inventory entry as `(handle, quantity, sort id)`, in array order."""
        base = self.sections["inventory"] + 4
        out = []
        for i in range(COMMON_ENTRIES):
            handle, quantity, sort_id = struct.unpack_from("<III", self.body, base + INVENTORY_ENTRY * i)
            if handle not in EMPTY:
                out.append((handle, quantity, sort_id))
        return out

    def face_buffer(self) -> bytes | None:
        start = self.sections["face"]
        section = self.body[start:start + SAVE_FACE_SECTION]
        at = section.find(FACE_MAGIC)
        if at < 0:
            return None
        buffer = self.body[start + at:start + at + FACE_BUFFER_LEN]
        return buffer if len(buffer) == FACE_BUFFER_LEN else None


SAVE_FACE_SECTION = dict(SECTIONS_AFTER_PROJECTILES)["face"]


# --- game data -----------------------------------------------------------------------------

class GameData:
    """The installed game's names and the param facts the exporter asks for."""

    def __init__(self):
        self.names = {stem: item_names.game_names(stem) for stem in
                      ("WeaponName", "ProtectorName", "AccessoryName", "GoodsName", "ArtsName")}
        pr = module("er_param_read", HERE / "er-param-read.py")
        files = pr.load()

        def table(stem):
            rows, _, _ = pr.rows(pr.param_bytes(files, stem), strict=False)
            return {r["id"]: r for r in rows}

        self.weapons = table("EquipParamWeapon")
        self.gems = table("EquipParamGem")
        self.reinforce = set(table("ReinforceParamWeapon"))
        self.parts = {}
        for row_id, row in table("EquipParamProtector").items():
            part = next((p for field, p in PROTECTOR_PART_FIELDS if row.get(field)), None)
            if part:
                self.parts[row_id] = part
        # Every skill some gem grants, row 0 ("No Skill") included.
        self.ashes = {int(r["swordArtsParamId"]) for r in self.gems.values() if int(r["swordArtsParamId"]) >= 0}

    def name(self, stem: str, row: int) -> str | None:
        return self.names[stem].get(row)

    def weapon_facts(self, row_with_affinity: int) -> tuple[int, int] | None:
        """`(wepType, swordArtsParamId)` of an armament row."""
        row = self.weapons.get(row_with_affinity)
        return (int(row["wepType"]), int(row["swordArtsParamId"])) if row else None

    def max_upgrade(self, row_with_affinity: int) -> int:
        """The highest level this armament has a `ReinforceParamWeapon` row for (`ReinforceLevels`)."""
        row = self.weapons.get(row_with_affinity)
        if row is None:
            return 0
        base = int(row["reinforceTypeId"])
        return max((lv for lv in range(MAX_REGULAR_LEVEL + 1) if base + lv in self.reinforce), default=0)

    def gem_can_mount(self, gem_row: int, wep_type: int) -> bool:
        field = GEM_MOUNT_FIELD.get(wep_type)
        row = self.gems.get(gem_row)
        return bool(field and row and row.get(f"canMountWep_{field}"))


def split_armament_id(param_id: int) -> tuple[int, int, str | None, int]:
    """`(row, row_with_affinity, infusion, level)`, as `er_build_import_core::plan` splits it."""
    level = param_id % ARMAMENT_LEVEL_STEP
    with_affinity = param_id - level
    index = with_affinity % ARMAMENT_ID_BLOCK // ARMAMENT_LEVEL_STEP
    if index == 0 or index >= len(INFUSIONS):
        return with_affinity, with_affinity, None, level
    return with_affinity - index * ARMAMENT_LEVEL_STEP, with_affinity, INFUSIONS[index], level


def regular_level_for_somber(somber: int) -> int:
    return max((regular for regular, s in enumerate(SOMBER_LEVEL_FOR_REGULAR) if s == somber), default=0)


# --- the read ------------------------------------------------------------------------------

def mounted_ash(game: GameData, slot: Slot, gem_handle: int | None, facts: tuple[int, int] | None) -> str | None:
    """The ash of war on an armament, or None when it carries only its own skill (`mounted_ash`)."""
    default_arts = facts[1] if facts else None
    if gem_handle in EMPTY or gem_handle is None or gem_handle not in slot.gaitems:
        return None
    gem_row = slot.gaitems[gem_handle][0] & ROW_MASK
    gem = game.gems.get(gem_row)
    if gem is None:
        return None
    arts = int(gem["swordArtsParamId"])
    if arts not in game.ashes or arts == default_arts:
        return None
    if facts is not None and not game.gem_can_mount(gem_row, facts[0]):
        return None
    return game.name("ArtsName", arts)


def armament(game: GameData, slot: Slot, param_id: int, handle: int | None, sort_id: int = 0) -> dict | None:
    """One armament as the exporter's `ReadSlot`, or None for an empty hand, ammunition or no name."""
    row, with_affinity, infusion, level = split_armament_id(param_id)
    if row == UNARMED_ARMAMENT_ROW:
        return None
    facts = game.weapon_facts(with_affinity)
    if facts and facts[0] in AMMUNITION_WEAPON_TYPES:
        return None
    name = game.name("WeaponName", row)
    if name is None:
        return None
    gem = slot.gaitems.get(handle, (None, None))[1] if handle is not None else None
    return {"name": name, "infusion": infusion, "weapon_art": mounted_ash(game, slot, gem, facts), "upgrade": level,
            "sort_id": sort_id, "max_upgrade": game.max_upgrade(with_affinity), "equip_index": None, "handle": handle}


def plain(name: str, sort_id: int = 0, equip_index: int | None = None) -> dict:
    return {"name": name, "infusion": None, "weapon_art": None, "upgrade": None, "sort_id": sort_id,
            "max_upgrade": None, "equip_index": equip_index, "handle": None}


def merge_worn(worn: list[dict], held: list[dict], matches) -> list[dict]:
    claimed = [False] * len(worn)
    out = []
    for item in held:
        item = dict(item)
        for i, w in enumerate(worn):
            if not claimed[i] and matches(w, item):
                claimed[i] = True
                item["equip_index"] = w["equip_index"]
                if item["weapon_art"] is None:
                    item["weapon_art"] = w["weapon_art"]
                break
        out.append(item)
    return out + [w for i, w in enumerate(worn) if not claimed[i]]


def read_character(game: GameData, slot: Slot) -> dict:
    f = slot.fields
    ids, handles = slot.words("param_ids", CHR_ASM_SLOTS), slot.words("handles", CHR_ASM_SLOTS)
    unnamed = []

    worn_arms = []
    for index, chr_slot in enumerate(ARMAMENT_CHR_ASM_SLOTS):
        if ids[chr_slot] in EMPTY:
            continue
        item = armament(game, slot, ids[chr_slot], handles[chr_slot])
        if item is not None:
            item["equip_index"] = index
            worn_arms.append(item)
    worn_protectors = []
    for offset, part in enumerate(PROTECTOR_PARTS):
        row = ids[PROTECTOR_CHR_ASM_FIRST + offset]
        if row in EMPTY:
            continue
        name = game.name("ProtectorName", row)
        if name is None:
            unnamed.append(f"worn {part} {row}")
            continue
        worn_protectors.append((part, plain(name, equip_index=0)))
    worn_talismans = []
    for index in range(TALISMAN_SLOTS):
        row = ids[TALISMAN_CHR_ASM_FIRST + index]
        if row in EMPTY:
            continue
        name = game.name("AccessoryName", row)
        if name is None:
            unnamed.append(f"worn talisman {row}")
            continue
        worn_talismans.append(plain(name, equip_index=index))

    held_arms, held_protectors, held_talismans = [], [], []
    for handle, quantity, sort_id in slot.inventory():
        kind = handle & CATEGORY_MASK
        copies = min(max(quantity, 1), MAX_COPIES_PER_ENTRY)
        if kind == HANDLE_WEAPON:
            if handle not in slot.gaitems:
                unnamed.append(f"inventory weapon handle {handle:#x} not in the gaitem map")
                continue
            item = armament(game, slot, slot.gaitems[handle][0] & ROW_MASK, handle, sort_id)
            if item is not None:
                held_arms += [item] * copies
        elif kind == HANDLE_PROTECTOR:
            if handle not in slot.gaitems:
                unnamed.append(f"inventory protector handle {handle:#x} not in the gaitem map")
                continue
            row = slot.gaitems[handle][0] & ROW_MASK
            name, part = game.name("ProtectorName", row), game.parts.get(row)
            if name and part:
                held_protectors += [(part, plain(name, sort_id))] * copies
        elif kind == HANDLE_ACCESSORY:
            name = game.name("AccessoryName", handle & ROW_MASK)
            if name:
                held_talismans += [plain(name, sort_id)] * copies
    held_arms.sort(key=lambda i: i["sort_id"])
    held_talismans.sort(key=lambda i: i["sort_id"])
    held_protectors.sort(key=lambda p: p[1]["sort_id"])

    protectors = []
    for part in PROTECTOR_PARTS:
        worn = [s for p, s in worn_protectors if p == part]
        held = [s for p, s in held_protectors if p == part]
        protectors += [(part, s) for s in merge_worn(worn, held, lambda w, h: w["name"] == h["name"])]

    ammo = []
    for i in range(len(AMMO_POSITION_KEYS)):
        row = ids[AMMO_CHR_ASM_FIRST + i]
        ammo.append(None if row in EMPTY else game.name("WeaponName", row))

    equipped = slot.words("equipped", QUICKBAR_FIRST + QUICKBAR_SLOTS + POUCH_SLOTS)

    def goods(item_id: int) -> str | None:
        if item_id in EMPTY:
            return None
        if item_id & CATEGORY_MASK != ITEM_GOODS:
            unnamed.append(f"tool {item_id:#x} is not goods")
            return None
        return game.name("GoodsName", item_id & ROW_MASK)

    quickbar = [goods(v) for v in equipped[QUICKBAR_FIRST:QUICKBAR_FIRST + QUICKBAR_SLOTS]]
    pouch = [goods(v) for v in equipped[QUICKBAR_FIRST + QUICKBAR_SLOTS:]]

    spells = []
    for spell_id in slot.words("magic", SPELL_SLOTS, SPELL_ENTRY):
        if spell_id in EMPTY:
            continue
        name = game.name("GoodsName", spell_id)
        if name is None:
            unnamed.append(f"spell {spell_id}")
        else:
            spells.append(name)

    tears = [None if v in EMPTY else game.name("GoodsName", v & ROW_MASK) for v in slot.words("physick", 2)]
    rune = slot.words("items", 1, start=4 * GREAT_RUNE_ITEM_WORD)[0]
    great_rune = None if rune in EMPTY else game.name("GoodsName", rune & ROW_MASK)

    matching = f.get("matchmaking_weapon_level")
    archetype = f.get("archetype")
    return {
        "name": f["name"],
        "character_class": STARTING_CLASSES[archetype] if isinstance(archetype, int) and archetype < len(
            STARTING_CLASSES) else None,
        "stats": {"rl": f["level"], **dict(zip(("vig", "mnd", "vit", "str", "dex", "int", "fth", "arc"), f["stats"]))},
        "weapon_upgrade": matching if isinstance(matching, int) and matching <= MATCHING_WEAPON_LEVEL_MAX else None,
        "armaments": merge_worn(worn_arms, held_arms, lambda w, h: w["handle"] is not None and w["handle"] == h["handle"]),
        "protectors": protectors,
        "talismans": merge_worn(worn_talismans, held_talismans, lambda w, h: w["name"] == h["name"]),
        "spells": spells,
        "ammo": ammo,
        "quickbar": quickbar,
        "pouch": pouch,
        "crystal_tears": tears,
        "great_rune": great_rune,
        "two_handing": slot.words("arm_style", 1)[0] in ARM_STYLE_TWO_HANDED,
        "flask_crimson": f.get("max_crimson_flask_count", 0),
        "flask_cerulean": f.get("max_cerulean_flask_count", 0),
        "face": slot.face_buffer(),
        "gender": f.get("gender", 0),
        "unnamed": unnamed,
    }


# --- the document (export_doc.rs) ----------------------------------------------------------

def sorting() -> dict:
    return {"direction": "asc", "method": "acquisition"}


def planner_slot(item: dict, order: int) -> dict:
    out = {"name": item["name"], "order": order}
    if item["upgrade"] is not None:
        out["upgrade"] = item["upgrade"]
    if item["infusion"]:
        out["infusion"] = item["infusion"]
    if item["weapon_art"]:
        out["weaponArt"] = item["weapon_art"]
    if item["equip_index"] is not None:
        out["equipSet"] = [item["equip_index"]]
        out["equipIndex"] = item["equip_index"]
    return out


def decode_sliders(buffer: bytes) -> dict | None:
    """`er_build_import_core::sliders::decode_face_buffer`."""
    if len(buffer) != FACE_BUFFER_LEN or buffer[:4] != FACE_MAGIC:
        return None
    if struct.unpack_from("<I", buffer, 4)[0] != FACE_BUFFER_VERSION:
        return None
    payload = buffer[FACE_PAYLOAD_OFFSET:FACE_PAYLOAD_OFFSET + SLIDER_BYTES]
    out = {}
    for field in json.loads(LAYOUT.read_text(encoding="utf-8")):
        at, kind, key = field["offset"], field["type"], field["key"]
        if kind == "colour":
            out[key] = list(payload[at:at + 3])
        elif kind == "list":
            value = struct.unpack_from("<I", payload, at)[0]
            if key == FACE_MODEL_ID:
                musculature = 1 if value >= MUSCULATURE_STEP else 0
                rest = value - MUSCULATURE_STEP * musculature
                out["age"] = rest % AGE_MODULUS
                out["boneStructure"] = rest // AGE_MODULUS * AGE_MODULUS
                out["musculature"] = musculature
            else:
                out[key] = value
        else:
            out[key] = payload[at]
    return out


def document(read: dict) -> dict:
    measured = [regular_level_for_somber(a["upgrade"]) if a["max_upgrade"] == MAX_SOMBER_LEVEL else a["upgrade"]
                for a in read["armaments"] if a["upgrade"] is not None]
    weapon_upgrade = max(measured) if measured else read["weapon_upgrade"]
    protectors = {}
    for part in PROTECTOR_PARTS:
        slots = []
        for order, item in enumerate(s for p, s in read["protectors"] if p == part):
            slot = planner_slot(item, order)
            if item["equip_index"] is not None:
                slot["equipSet"], slot["equipIndex"] = [PROTECTOR_EQUIP_INDEX], PROTECTOR_EQUIP_INDEX
            slots.append(slot)
        protectors[part] = {"slots": slots, "sorting": sorting()}
    tools = []
    positions = list(enumerate(read["quickbar"])) + [(QUICKBAR_SLOTS + i, n) for i, n in enumerate(read["pouch"])]
    for position, name in positions:
        if name:
            tools.append({"name": name, "order": len(tools), "equipIndex": position})
    sliders = None
    if read["face"]:
        decoded = decode_sliders(read["face"])
        if decoded is not None:
            sliders = {"id": "", "bodyType": "B" if read["gender"] == GENDER_BODY_B else "A", "name": "",
                       "images": [{"url": "", "geometry": {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0,
                                                           "zoom": 1.0}}],
                       "sliders": decoded}
    doc = {
        "id": "", "name": read["name"], "description": "", "buildUrl": "",
        "characterClass": read["character_class"],
        "stats": read["stats"],
        "inventory": {"slots": [planner_slot(a, i) for i, a in enumerate(read["armaments"])], "sorting": sorting()},
        "talismans": {"slots": [planner_slot(t, i) for i, t in enumerate(read["talismans"])], "sorting": sorting()},
        "protectors": protectors,
        "spells": {"slots": [{"name": n, "order": i} for i, n in enumerate(read["spells"])]},
        "items": {
            "ammo": {k: n for k, n in zip(AMMO_POSITION_KEYS, read["ammo"]) if n},
            "tools": {"slots": tools, "sorting": sorting()},
            "crystalTears": read["crystal_tears"],
            "flasks": {"crimson": read["flask_crimson"], "cerulean": read["flask_cerulean"],
                       "total": read["flask_crimson"] + read["flask_cerulean"], "level": 12},
        },
        "greatRune": read["great_rune"],
        "weaponUpgrade": weapon_upgrade if weapon_upgrade is not None else MAX_REGULAR_LEVEL,
        "is2h": read["two_handing"],
        "isPvE": False,
        "version": PLANNER_VERSION,
        "author": None,
        "views": {"overview": "build-planner"},
        "conditions": {"forEffects": [], "crystalTears": False},
        "sets": {k: [{"name": "Default", "active": True}] for k in ("weapons", "talismans", "protectors")},
        "tags": [],
        "customEffects": [],
    }
    if sliders is not None:
        doc["sliders"] = sliders
    return doc


def save_document(path: pathlib.Path, slot: int, game: GameData | None = None) -> tuple[dict, list[str]]:
    read = read_character(game or GameData(), Slot(path, slot))
    return document(read), read["unnamed"]


# --- checks --------------------------------------------------------------------------------

def fold(name: str) -> str:
    plain_text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return " ".join(plain_text.replace("’", "'").lower().split())


def planner_unknowns(doc: dict) -> list[str]:
    """Names in `doc` the planner's own item list does not hold (its snapshot, when present)."""
    if not PLANNER_NAMES.exists():
        return [f"(unchecked: no {PLANNER_NAMES})"]
    known = json.loads(PLANNER_NAMES.read_text(encoding="utf-8"))
    names = [s.get("weaponArt") for s in doc["inventory"]["slots"]] + [s["name"] for s in doc["inventory"]["slots"]]
    names += [doc["items"]["ammo"][k] for k in doc["items"]["ammo"]]
    return sorted({n for n in names if n and fold(n) not in known})


def differences(ours, theirs, path="") -> list[str]:
    if isinstance(ours, dict) and isinstance(theirs, dict):
        out = []
        for key in sorted(set(ours) | set(theirs)):
            if key not in ours:
                out.append(f"{path}.{key}: missing here (theirs {json.dumps(theirs[key])[:120]})")
            elif key not in theirs:
                out.append(f"{path}.{key}: extra here ({json.dumps(ours[key])[:120]})")
            else:
                out += differences(ours[key], theirs[key], f"{path}.{key}")
        return out
    if isinstance(ours, list) and isinstance(theirs, list):
        out = [f"{path}: {len(ours)} entries here, {len(theirs)} theirs"] if len(ours) != len(theirs) else []
        for i, (a, b) in enumerate(zip(ours, theirs)):
            out += differences(a, b, f"{path}[{i}]")
        return out
    if ours != theirs and not (isinstance(ours, (int, float)) and isinstance(theirs, (int, float)) and ours == theirs):
        return [f"{path}: {json.dumps(ours)[:120]} here, {json.dumps(theirs)[:120]} theirs"]
    return []


def selftest() -> int:
    checks = {
        "split keeps the level and names the affinity": split_armament_id(16110217) == (16110000, 16110200, "Keen", 17),
        "split leaves a standard armament whole": split_armament_id(2550010) == (2550000, 2550000, None, 10),
        "somber +10 is regular +25": regular_level_for_somber(10) == 25,
        "somber +7 is regular +19": regular_level_for_somber(7) == 19,
    }
    buffer = bytearray(FACE_BUFFER_LEN)
    buffer[:4] = FACE_MAGIC
    buffer[4:8] = FACE_BUFFER_VERSION.to_bytes(4, "little")
    buffer[8:12] = FACE_BUFFER_LEN.to_bytes(4, "little")
    buffer[12:16] = (141).to_bytes(4, "little")
    decoded = decode_sliders(bytes(buffer)) or {}
    checks["faceModelId 141 is age 1, bone 40, musculature 1"] = (
        (decoded.get("age"), decoded.get("boneStructure"), decoded.get("musculature")) == (1, 40, 1)
        and FACE_MODEL_ID not in decoded)
    checks["a wrong face version decodes to nothing"] = decode_sliders(bytes(buffer[:4]) + b"\x03" + bytes(buffer[5:])) is None
    merged = merge_worn([plain("A", equip_index=2)], [plain("B", 1), plain("A", 2), plain("A", 3)],
                        lambda w, h: w["name"] == h["name"])
    checks["a worn item marks the first held copy only"] = [m["equip_index"] for m in merged] == [None, 2, None]
    checks["an unmatched worn item is kept"] = len(merge_worn([plain("Z", equip_index=0)], [], lambda w, h: False)) == 1
    for name, ok in checks.items():
        print(f"{'ok  ' if ok else 'FAIL'} {name}")
    return 0 if all(checks.values()) else 1


def dump(s: Slot, game: GameData) -> None:
    print(f"pgd {s.pgd:#x}, {len(s.gaitems)} gaitems; sections " + json.dumps({k: hex(v) for k, v in s.sections.items()}))
    ids, handles = s.words("param_ids", CHR_ASM_SLOTS), s.words("handles", CHR_ASM_SLOTS)
    for i in range(CHR_ASM_SLOTS):
        gem = s.gaitems.get(handles[i], (None, None))[1]
        print(f"  chrasm {i:2} id={ids[i]:#010x} handle={handles[i]:#010x} gem={gem}")
    print("  equipped " + " ".join(f"{v:#x}" for v in s.words("equipped", 39)))
    print("  items " + " ".join(f"{v:#x}" for v in s.words("items", 0x8C // 4)))
    print("  magic " + " ".join(f"{v:#x}" for v in s.words("magic", SPELL_SLOTS, SPELL_ENTRY)))
    for handle, quantity, sort_id in s.inventory():
        if handle & CATEGORY_MASK in (HANDLE_WEAPON, HANDLE_PROTECTOR, HANDLE_ACCESSORY):
            item_id, gem = s.gaitems.get(handle, (handle, None))
            print(f"  inv {handle:#010x} x{quantity} sort={sort_id} item={item_id:#x} gem={gem}")


# --- upload --------------------------------------------------------------------------------

LEVEL_SUFFIX = re.compile(r"\s*\(RL\d+\)\s*$")


def planner():
    link = module("er_npc_planner_link", PLANNER_SCRIPTS / "er-npc-planner-link.py")
    with contextlib.redirect_stdout(io.StringIO()):
        session = link.load_session(LOGIN_CODE)
    return link, session


def held_key(name: str, level) -> tuple[str, int | None]:
    return fold(LEVEL_SUFFIX.sub("", name or "")), level if isinstance(level, int) else None


def upload(dry_run: bool, only: list[str] | None) -> int:
    batch = module("er_portrait_batch", HERE / "er-portrait-batch.py")
    characters = batch.save_characters()
    link, session = planner()
    held = {held_key(b["data"].get("name", ""), (b["data"].get("stats") or {}).get("rl")): b["id"]
            for b in link.account_builds(session)}
    todo = [c for c in characters if held_key(c["character"], c["level"]) not in held
            and (not only or fold(c["character"]) in {fold(o) for o in only})]
    present = [c for c in characters if held_key(c["character"], c["level"]) in held]
    print(f"{len(characters)} save characters; {len(present)} already on the account, {len(todo)} to upload")
    for c in present:
        print(f"  on the account  {c['character']} RL{c['level']}  {PLANNER_SITE}{held[held_key(c['character'], c['level'])]}")
    game = GameData()
    failed = 0
    for c in todo:
        title = f"{c['character']} (RL{c['level']})"
        try:
            doc, unnamed = save_document(pathlib.Path(c["save"]), c["slot"], game)
        except Exception as err:  # noqa: BLE001 -- one unreadable slot must not stop the rest
            print(f"  FAILED to read {title} from {c['save']} slot {c['slot']}: {type(err).__name__}: {err}")
            failed += 1
            continue
        if (doc["name"], doc["stats"]["rl"]) != (c["character"], c["level"]):
            print(f"  FAILED {title}: the slot read back as {doc['name']} RL{doc['stats']['rl']}")
            failed += 1
            continue
        doc["name"] = title
        unknown = planner_unknowns(doc)
        note = (f"; unnamed {unnamed}" if unnamed else "") + (f"; not in the planner's list {unknown}" if unknown else "")
        counts = (f"{len(doc['inventory']['slots'])} armaments, {len(doc['talismans']['slots'])} talismans, "
                  f"{sum(len(p['slots']) for p in doc['protectors'].values())} armour, {len(doc['spells']['slots'])} spells, "
                  f"sliders {'yes' if 'sliders' in doc else 'NO'}")
        if dry_run:
            print(f"  would upload {title} from {c['save']} slot {c['slot']}: {counts}{note}")
            continue
        new_id = link.store(doc, "", session)
        back = batch.Planner.public(new_id)
        ok = (back.get("name") == title and (back.get("stats") or {}).get("rl") == c["level"]
              and (back.get("sliders") or {}).get("sliders") == (doc.get("sliders") or {}).get("sliders")
              and len((back.get("inventory") or {}).get("slots") or []) == len(doc["inventory"]["slots"]))
        if not ok:
            failed += 1
        print(f"  {'uploaded' if ok else 'UPLOADED BUT DID NOT READ BACK'} {title}: {PLANNER_SITE}{new_id} "
              f"({counts}{note})")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("save", nargs="?", type=pathlib.Path)
    ap.add_argument("--slot", type=int, default=0)
    ap.add_argument("--compare", type=pathlib.Path, help="a planner document to diff against (the in-game export)")
    ap.add_argument("--dump", action="store_true", help="print the raw equipment sections")
    ap.add_argument("--upload", action="store_true", help="upload every save character the account lacks")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", action="append", help="with --upload, only this character name (repeatable)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.upload:
        return upload(args.dry_run, args.only)
    if args.save is None:
        ap.error("a save file, --upload or --selftest")
    if args.dump:
        dump(Slot(args.save, args.slot), GameData())
        return 0
    doc, unnamed = save_document(args.save, args.slot)
    if args.compare:
        theirs = json.loads(args.compare.read_text(encoding="utf-8"))
        diff = differences(doc, theirs)
        for line in diff:
            print(line)
        print(f"{len(diff)} difference(s); unnamed {unnamed}")
        return 1 if diff else 0
    print(json.dumps(doc, indent=1, ensure_ascii=False))
    if unnamed:
        print(f"unnamed: {unnamed}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
