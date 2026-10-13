#!/usr/bin/env python3
"""Find a named NPC's character init -- gear, stats, spells and face sliders -- offline.

    python3 scripts/er-npc-chara-init.py sellen
    python3 scripts/er-npc-chara-init.py "witch hunter jerren" --face
    python3 scripts/er-npc-chara-init.py jerren --pick 2
    python3 scripts/er-npc-chara-init.py varre --json
    python3 scripts/er-npc-chara-init.py --selftest

A human NPC is a `CharaInitParam` row: armaments, armour, talismans, ammunition, spells,
items, level and the eight attributes. Its sliders are not in that row; `npcPlayerFaceGenId`
names the `FaceParam` row that holds them. Only rows with a body can be read -- a face id that
resolves to a `FaceParam` row. A name without one still matches, and is then rejected with the
reason and exit code 3: the starting classes have no face, and NPCs such as Melina or Ranni are
not built from a `CharaInitParam` row at all.

Names come from two places, merged by spelling with accents and punctuation folded:

  * Smithbox's `CharaInitParam` row names, which name the row itself and cover the DLC NPCs
    (`2024150 Moore (NPC)`). The parenthesised qualifier is dropped from the name a query
    matches and kept as the row's label, so every Moore row lands under one NPC.
  * The game's own `NpcName` text, for base-game NPCs Smithbox leaves unnamed (White Mask
    Varré). These are linked by NPC number, a convention measured on 1.17.1 rather than a
    reference the game stores: digits 2-4 of an `NpcName` id (1|316|00, Sorceress Sellen) are
    digits 2-4 of the `CharaInitParam` rows `2NNNv` (23160..23163). Rows reached only this way
    are labelled `by NPC number`, and a row Smithbox names keeps its Smithbox name.

Matching, loosest last: every query word prefixes a word of the name (`sellen`, `w m varre`),
then the query as a substring with spaces ignored, then a fuzzy ratio for typos. One NPC
matched is shown. Several, one of them exactly the query, shows that one and names the
others. Several with no exact one asks which on a terminal; off a terminal it lists them,
numbered, and exits 2 so the caller can repeat with `--pick N`.

The regulation is decrypted once per run through `er-param-read.py`, which this reuses.
"""

from __future__ import annotations

import argparse
import difflib
import importlib.util
import json
import os
import re
import shutil
import sys
import unicodedata
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))

#: The weapon slots, as (label, id field, table-selector field). A selector of 1 means the id
#: is an `EquipParamCustomWeapon` row (base weapon, reinforcement and gem) rather than a weapon.
WEAPON_SLOTS = (
    ("Right 1", "equip_Wep_Right", "wepParamType_Right1"),
    ("Right 2", "equip_Subwep_Right", "wepParamType_Right2"),
    ("Right 3", "equip_Subwep_Right3", "wepParamType_Right3"),
    ("Left 1", "equip_Wep_Left", "wepParamType_Left1"),
    ("Left 2", "equip_Subwep_Left", "wepParamType_Left2"),
    ("Left 3", "equip_Subwep_Left3", "wepParamType_Left3"),
)
ARMOUR_SLOTS = (("Head", "equip_Helm"), ("Body", "equip_Armer"), ("Arms", "equip_Gaunt"), ("Legs", "equip_Leg"))
AMMO_SLOTS = (
    ("Arrows 1", "equip_Arrow", "arrowNum"),
    ("Arrows 2", "equip_SubArrow", "subArrowNum"),
    ("Bolts 1", "equip_Bolt", "boltNum"),
    ("Bolts 2", "equip_SubBolt", "subBoltNum"),
)
TALISMAN_FIELDS = tuple(f"equip_Accessory0{i}" for i in range(1, 5))
SPELL_FIELDS = tuple(f"equip_Spell_0{i}" for i in range(1, 8))
ITEM_SLOTS = tuple((f"item_{i:02}", f"itemNum_{i:02}") for i in range(1, 11)) + tuple(
    (f"secondaryItem_0{i}", f"secondaryItemNum_0{i}") for i in range(1, 7)
)
ATTRIBUTES = (
    ("Vig", "baseVit"), ("Min", "baseWil"), ("End", "baseEnd"), ("Str", "baseStr"),
    ("Dex", "baseDex"), ("Int", "baseMag"), ("Fai", "baseFai"), ("Arc", "baseLuc"),
)
CUSTOM_WEAPON_TYPE = 1
NO_ID = -1
#: Fuzzy matches below this `difflib` ratio are not offered.
FUZZY_FLOOR = 0.72
#: How many fuzzy candidates to list at most.
FUZZY_LIMIT = 12
#: A table cell is as wide as its longest value, up to this; longer values are cut.
CELL_WIDTH_MAX = 60
#: The width assumed when output is not a terminal.
DEFAULT_TERMINAL_WIDTH = 200


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses in the loaded file look their module up here
    spec.loader.exec_module(module)
    return module


# --- names and matching -------------------------------------------------------------------

def fold(text: str) -> str:
    """Lowercase, accents and punctuation removed, runs of space collapsed."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def base_name(label: str) -> str:
    """`Moore (Boss) (Enir-Ilim)` -> `Moore`; the qualifiers stay on the row label."""
    return re.sub(r"(\s*\([^)]*\))+\s*$", "", label).strip()


@dataclass
class Npc:
    name: str
    #: row id -> label: the Smithbox row name, or `by NPC number`.
    rows: dict[int, str] = field(default_factory=dict)
    aliases: set[str] = field(default_factory=set)
    #: Why a name that matched cannot be used: it has no body behind it.
    rejected: str = ""


def build_index(row_names: dict[int, str], npc_names: dict[int, str], body_rows: set[int]) -> dict[str, Npc]:
    """{folded name: Npc}. A name with no body behind it stays in with `rejected` saying why,
    so asking for Ranni is answered ("no body, and this is why") rather than read as a typo."""
    index: dict[str, Npc] = {}
    reasons: dict[str, tuple[str, str]] = {}

    def entry(name: str) -> Npc:
        key = fold(name)
        npc = index.get(key)
        if npc is None:
            npc = index[key] = Npc(name)
        npc.aliases.add(key)
        return npc

    faceless: dict[str, list[int]] = {}
    for row, label in row_names.items():
        if not label or not base_name(label):
            continue
        if row in body_rows:
            entry(base_name(label)).rows[row] = label
        else:
            faceless.setdefault(base_name(label), []).append(row)

    by_number: dict[int, dict[str, str]] = {}
    for text_id, name in npc_names.items():
        if 100000 <= text_id < 200000:
            by_number.setdefault((text_id // 100) % 1000, {})[fold(name)] = name
        elif fold(name):
            reasons.setdefault(fold(name), (name, "no CharaInitParam row carries its name"))
    for number, names in by_number.items():
        span = f"{20000 + number * 10}-{20009 + number * 10}"
        linked = [r for r in range(20000 + number * 10, 20010 + number * 10) if r in body_rows]
        named = {fold(base_name(row_names[r])): base_name(row_names[r]) for r in linked if row_names.get(r)}
        if not linked:
            for key, name in names.items():
                reasons[key] = (name, f"no CharaInitParam row carries its name, and none with a body "
                                      f"exists under its NPC number {number} ({span})")
            continue
        # A number two NPCs share (Irina and Hyetta are both 310) cannot say whose a row is.
        if len(names) != 1:
            rows_named = (f"; the rows there are named {', '.join(sorted(set(named.values())))}, which can "
                          "be asked for by that name") if named else ""
            for key, name in names.items():
                others = ", ".join(n for k, n in names.items() if k != key)
                reasons[key] = (name, f"its NPC number {number} is shared with {others}, so no "
                                      f"CharaInitParam row ({span}) can be attributed to it{rows_named}")
            continue
        (key, name), = names.items()
        # Every row Smithbox names under this number must be this NPC, or the convention is
        # not holding here: measured on 1.17.1, Hornsent Grandam's number lands on Mimic Tear.
        if not all(key in b or b in key for b in named):
            reasons[key] = (name, f"the CharaInitParam rows under its NPC number {number} ({span}) "
                                  f"belong to {', '.join(sorted(set(named.values())))}")
            continue
        unnamed = [r for r in linked if not row_names.get(r)]
        if unnamed or key in index:
            npc = entry(name)
            npc.name = name  # the game's own spelling, accents and all
            for row in unnamed:
                npc.rows[row] = "by NPC number"
        for b in named:
            index[b].aliases.add(key)  # the game's `Roderika` finds Smithbox's `Roderika, Spirit Tuner`

    for name, rows in faceless.items():
        shown = ", ".join(str(r) for r in sorted(rows)[:6]) + (", ..." if len(rows) > 6 else "")
        reasons[fold(name)] = (name, f"CharaInitParam {shown} has no face, so it is not a body")

    valid = {k: v for k, v in index.items() if v.rows}
    for key, (name, why) in reasons.items():
        if key and key not in valid:
            valid[key] = Npc(name, aliases={key}, rejected=why)
    return valid


def candidates(query: str, index: dict[str, Npc]) -> tuple[list[Npc], Npc | None]:
    """(matches in display order, the exact match if there is one)."""
    q = fold(query)
    words = q.split()
    if not words:
        return [], None
    exact = index.get(q)

    def prefixed(alias: str) -> bool:
        tokens = alias.split()
        return all(any(t.startswith(w) for t in tokens) for w in words)

    tiers = (
        [n for n in index.values() if any(prefixed(a) for a in n.aliases)],
        [n for n in index.values() if any(q.replace(" ", "") in a.replace(" ", "") for a in n.aliases)],
    )
    for tier in tiers:
        if tier:
            tier.sort(key=lambda n: (n is not exact, n.name.lower()))
            return tier, exact
    scored = []
    for npc in index.values():
        best = max(
            max(difflib.SequenceMatcher(None, q, a).ratio() for a in npc.aliases),
            max((difflib.SequenceMatcher(None, w, t).ratio()
                 for a in npc.aliases for t in a.split() for w in words), default=0.0)
            if len(words) == 1 else 0.0,
        )
        if best >= FUZZY_FLOOR:
            scored.append((best, npc))
    scored.sort(key=lambda s: (-s[0], s[1].name.lower()))
    return [npc for _, npc in scored[:FUZZY_LIMIT]], None


#: Exit codes beyond 0 (found) and 1 (nothing matched).
EXIT_AMBIGUOUS = 2
EXIT_REJECTED = 3


def listing(found: list[Npc]) -> str:
    """The NPCs with a body, numbered for `--pick`; the rest after them, unnumbered, with why."""
    valid = [n for n in found if not n.rejected]
    lines = [f"  {i}. {n.name}  ({len(n.rows)} row{'s' * (len(n.rows) != 1)})" for i, n in enumerate(valid, 1)]
    lines += [f"  -  {n.name}  (no body: {n.rejected})" for n in found if n.rejected]
    return "\n".join(lines)


def choose(query: str, found: list[Npc], exact: Npc | None, pick: int | None) -> Npc:
    """The one NPC the query means. A name with no body can be returned; the caller rejects it."""
    valid = [n for n in found if not n.rejected]
    if not found:
        print(f"no NPC matches {query!r}", file=sys.stderr)
        raise SystemExit(1)
    if pick is not None:
        if not 1 <= pick <= len(valid):
            print(f"--pick {pick} is out of range; {query!r} matched:\n{listing(found)}", file=sys.stderr)
            raise SystemExit(EXIT_AMBIGUOUS)
        return valid[pick - 1]
    # The query names exactly one thing, or spells one name out: answer about that one.
    single = found[0] if len(found) == 1 else exact if exact is not None else valid[0] if len(valid) == 1 else None
    if single is not None:
        others = [n.name for n in found if n is not single]
        if others:
            print(f"also matched: {', '.join(others)} (use --pick N or a fuller name)", file=sys.stderr)
        return single
    if not valid:
        print(f"{query!r} matches only NPCs with no body:\n{listing(found)}", file=sys.stderr)
        raise SystemExit(EXIT_REJECTED)
    if sys.stdin.isatty() and sys.stdout.isatty():
        print(f"{query!r} matches more than one NPC:\n{listing(found)}")
        while True:
            answer = input(f"Pick 1-{len(valid)}: ").strip()
            if answer.isdigit() and 1 <= int(answer) <= len(valid):
                return valid[int(answer) - 1]
    print(f"{query!r} matches more than one NPC; repeat with --pick N:\n{listing(found)}", file=sys.stderr)
    raise SystemExit(EXIT_AMBIGUOUS)


# --- decoding ------------------------------------------------------------------------------

def weapon_name(names: dict[int, str], weapon_id: int) -> str:
    """A weapon id carries affinity and level: 33000025 is 33000000 at +25."""
    level = weapon_id % 100
    for candidate in (weapon_id, weapon_id - level, weapon_id - weapon_id % 10000):
        if names.get(candidate):
            return names[candidate] + (f" +{level}" if level else "")
    return "no name"


class Decoder:
    def __init__(self, regulation: str | None):
        self.pr = load_module("er_param_read", "er-param-read.py")
        files = self.pr.load(regulation)
        self.tables = {}
        for stem in ("CharaInitParam", "FaceParam", "EquipParamCustomWeapon"):
            rows, _, _ = self.pr.rows(self.pr.param_bytes(files, stem), strict=False)
            self.tables[stem] = {r["id"]: r for r in rows}
        self._names = {}

    def names(self, stem: str) -> dict[int, str]:
        if stem not in self._names:
            loaded = self.pr.row_names(stem)
            self._names[stem] = {i: (n or "").strip() for i, n in loaded.items()}
        return self._names[stem]

    def body_rows(self) -> set[int]:
        faces = self.tables["FaceParam"]
        return {i for i, r in self.tables["CharaInitParam"].items() if r.get("npcPlayerFaceGenId", NO_ID) in faces}

    def spell(self, spell_id: int) -> str:
        return self.names("Magic").get(spell_id) or self.names("EquipParamGoods").get(spell_id) or "no name"

    def weapon(self, weapon_id: int, table: int) -> str:
        if table != CUSTOM_WEAPON_TYPE:
            return f"{weapon_id} {weapon_name(self.names('EquipParamWeapon'), weapon_id)}"
        custom = self.tables["EquipParamCustomWeapon"].get(weapon_id)
        if custom is None:
            return f"custom {weapon_id} (no such row)"
        base = custom["baseWepId"]
        gem = custom.get("gemId", NO_ID)
        gem_text = f", gem {gem} {self.names('EquipParamGem').get(gem) or 'no name'}" if gem > 0 else ""
        return (f"custom {weapon_id}: {base} {weapon_name(self.names('EquipParamWeapon'), base)} "
                f"+{custom.get('reinforceLv', 0)}{gem_text}")

    def describe(self, row_id: int) -> dict:
        r = self.tables["CharaInitParam"][row_id]
        out: dict = {"id": row_id}
        for label, fid, tfield in WEAPON_SLOTS:
            if r.get(fid, NO_ID) > 0:
                out[label] = self.weapon(r[fid], r.get(tfield, 0))
        for label, fid in ARMOUR_SLOTS:
            if r.get(fid, NO_ID) > 0:
                out[label] = f"{r[fid]} {self.names('EquipParamProtector').get(r[fid]) or 'no name'}"
        for i, fid in enumerate(TALISMAN_FIELDS, 1):
            if r.get(fid, NO_ID) > 0:
                out[f"Talisman {i}"] = f"{r[fid]} {self.names('EquipParamAccessory').get(r[fid]) or 'no name'}"
        for label, fid, nfield in AMMO_SLOTS:
            if r.get(fid, NO_ID) > 0:
                out[label] = f"{r[fid]} {weapon_name(self.names('EquipParamWeapon'), r[fid])} x{r.get(nfield, 0)}"
        for i, fid in enumerate(SPELL_FIELDS, 1):
            if r.get(fid, NO_ID) > 0:
                out[f"Spell {i}"] = f"{r[fid]} {self.spell(r[fid])}"
        for fid, nfield in ITEM_SLOTS:
            if r.get(fid, NO_ID) > 0:
                slot = fid.replace("secondaryItem_", "Item 2.").replace("item_", "Item ")
                out[slot] = f"{r[fid]} {self.names('EquipParamGoods').get(r[fid]) or 'no name'} x{r.get(nfield, 0)}"
        out["Level"] = r.get("soulLv", 0)
        out["/".join(short for short, _ in ATTRIBUTES)] = "/".join(str(r.get(fid, 0)) for _, fid in ATTRIBUTES)
        face_id = r.get("npcPlayerFaceGenId", NO_ID)
        face = self.tables["FaceParam"].get(face_id, {})
        sliders = {k: v for k, v in face.items() if k not in ("id", "_off")}
        out["Face"] = f"FaceParam {face_id} ({sum(1 for v in sliders.values() if v)} of {len(sliders)} set)"
        out["Sex"] = r.get("npcPlayerSex", 0)
        out["Voice"] = r.get("voiceType", 0)
        out["_face_fields"] = sliders
        return out


# --- output --------------------------------------------------------------------------------

def print_table(npc: Npc, described: list[dict]) -> None:
    """One column per row, as many side by side as the terminal fits, the rest below."""
    order: list[str] = []
    for d in described:
        order += [k for k in d if k not in order and not k.startswith("_") and k != "id"]
    cells = {d["id"]: [f"{d['id']} {npc.rows[d['id']]}"] + [str(d.get(k, "-")) for k in order] for d in described}
    label_w = max(len(k) for k in order)
    cell_w = min(CELL_WIDTH_MAX, max(len(c) for column in cells.values() for c in column))
    width = shutil.get_terminal_size((DEFAULT_TERMINAL_WIDTH, 24)).columns
    per_chunk = max(1, (width - label_w - 2) // (cell_w + 3))
    for start in range(0, len(described), per_chunk):
        chunk = [cells[d["id"]] for d in described[start:start + per_chunk]]
        for i, label in enumerate([""] + order):
            print(f"{label:<{label_w}}  " + " | ".join(f"{c[i][:cell_w]:<{cell_w}}" for c in chunk).rstrip())
        print()


def print_face(described: list[dict]) -> None:
    seen = set()
    for d in described:
        face = d["Face"].split()[1]
        if face in seen:
            continue
        seen.add(face)
        print(f"FaceParam {face}:")
        items = [f"{k}={v}" for k, v in d["_face_fields"].items()]
        width = max(len(s) for s in items) + 2
        per_line = max(1, 120 // width)
        for i in range(0, len(items), per_line):
            print("  " + "".join(s.ljust(width) for s in items[i:i + per_line]).rstrip())
        print()


# --- selftest ------------------------------------------------------------------------------

def selftest() -> int:
    failures = 0

    def check(what: str, ok: bool) -> None:
        nonlocal failures
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {what}")

    row_names = {
        23160: "Sorceress Sellen", 23161: "Sorceress Sellen", 23121: "Castellan Jerren",
        23122: "Witch-Hunter Jerren", 2024150: "Moore (NPC)", 2024151: "Moore (Invader)",
        3000: "Class - Vagabond", 23232: "Dung Eater Puppet", 24020: "Mimic Tear",
        23101: "Finger Maiden Hyetta", 23102: "Irina of Morne",
    }
    # Real 1.17.1 NpcName ids, so the NPC-number arithmetic is checked against the game's own.
    npc_names = {131600: "Sorceress Sellen", 130100: "White Mask Varré", 132300: "Dung Eater",
                 110600: "Ranni the Witch", 140200: "Hornsent Grandam",
                 131000: "Irina of Morne", 131010: "Lightseeker Hyetta", 90210000: "Messmer the Impaler"}
    body = {23160, 23161, 23162, 23121, 23122, 23120, 2024150, 2024151, 23010, 23011, 23230, 23232,
            24020, 24021, 23100, 23101, 23102}
    index = build_index(row_names, npc_names, body)

    def resolve(q):
        found, exact = candidates(q, index)
        return [n.name for n in found], exact.name if exact else None

    check("sellen -> one NPC", resolve("sellen") == (["Sorceress Sellen"], None))
    check("Sellen keeps the unnamed 23162 by NPC number",
          index["sorceress sellen"].rows == {23160: "Sorceress Sellen", 23161: "Sorceress Sellen",
                                             23162: "by NPC number"})
    check("varre (no accent) finds Varré by NPC number", resolve("varre")[0] == ["White Mask Varré"]
          and set(index["white mask varre"].rows) == {23010, 23011})
    check("jerren is ambiguous with no exact match",
          resolve("jerren") == (["Castellan Jerren", "Witch-Hunter Jerren"], None))
    check("both Moore rows sit under one NPC", set(index["moore"].rows) == {2024150, 2024151})
    check("dung eater: exact match first, puppet also listed",
          resolve("dung eater") == (["Dung Eater", "Dung Eater Puppet"], "Dung Eater"))
    check("Dung Eater takes 23230 by number, the puppet keeps its own row",
          index["dung eater"].rows == {23230: "by NPC number"}
          and set(index["dung eater puppet"].rows) == {23232})
    def rejected(key: str, *words: str) -> bool:
        npc = index.get(key)
        return npc is not None and not npc.rows and all(w in npc.rejected for w in words)

    check("Grandam matches, rejected: her number's row is Mimic Tear's",
          rejected("hornsent grandam", "402", "Mimic Tear"))
    check("Hyetta's game name matches, rejected: number 310 is shared with Irina",
          rejected("lightseeker hyetta", "310", "Irina of Morne") and 23100 not in index["irina of morne"].rows)
    check("a class row matches, rejected: no face", rejected("class vagabond", "3000", "no face"))
    check("Ranni matches, rejected: no row with a body under number 106",
          rejected("ranni the witch", "106", "21060-21069"))
    check("ranni resolves to Ranni, who choose() returns for the caller to reject",
          resolve("ranni") == (["Ranni the Witch"], None))
    check("a DLC name with no number convention matches, rejected",
          rejected("messmer the impaler", "no CharaInitParam row"))
    check("typo selen -> fuzzy Sellen", "Sorceress Sellen" in resolve("selen")[0])
    check("w m varre -> word prefixes", resolve("w m varre")[0] == ["White Mask Varré"])
    weapons = {33000000: "Heavy Thing", 33900000: "Staff"}
    check("levelled weapon id", weapon_name(weapons, 33000025) == "Heavy Thing +25")
    check("unnamed weapon id", weapon_name(weapons, 12345600) == "no name")
    print("selftest:", "PASS" if failures == 0 else f"{failures} FAILED")
    return 0 if failures == 0 else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Find a named NPC's CharaInitParam rows and face, offline.")
    parser.add_argument("name", nargs="*", help="the NPC's name, loosely: 'sellen', 'w m varre'")
    parser.add_argument("--pick", type=int, help="take the Nth candidate when the name is ambiguous")
    parser.add_argument("--face", action="store_true", help="also print every FaceParam field (the sliders)")
    parser.add_argument("--json", action="store_true", help="print the decoded rows as JSON")
    parser.add_argument("--list", action="store_true", help="list the matching NPCs and stop")
    parser.add_argument("--regulation", help="regulation.bin to read (default: the installed game's)")
    parser.add_argument("--selftest", action="store_true", help="check matching and decoding offline")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    query = " ".join(args.name).strip()
    if not query:
        parser.error("give an NPC name")

    decoder = Decoder(args.regulation)
    item_names = load_module("er_item_name", "er-item-name.py")
    npc_names = item_names.game_names("NpcName") if item_names.cache_state() != "missing" else {}
    index = build_index(decoder.names("CharaInitParam"), npc_names, decoder.body_rows())
    found, exact = candidates(query, index)
    if args.list:
        if not found:
            print(f"no NPC matches {query!r}", file=sys.stderr)
        for i, npc in enumerate((n for n in found if not n.rejected), 1):
            print(f"{i}. {npc.name}: " + ", ".join(f"{r} {lbl}" for r, lbl in sorted(npc.rows.items())))
        for npc in (n for n in found if n.rejected):
            print(f"-  {npc.name}: no body: {npc.rejected}")
        return 0 if found else 1
    npc = choose(query, found, exact, args.pick)
    if npc.rejected:
        print(f"{npc.name}: matched, but it has no body to read: {npc.rejected}", file=sys.stderr)
        return EXIT_REJECTED
    described = [decoder.describe(row) for row in sorted(npc.rows)]
    if args.json:
        print(json.dumps({"npc": npc.name, "rows": [
            {**{k: v for k, v in d.items() if k != "_face_fields"}, "label": npc.rows[d["id"]],
             **({"face_fields": d["_face_fields"]} if args.face else {})} for d in described]},
            ensure_ascii=False, indent=2))
        return 0
    print(f"{npc.name} -- CharaInitParam {', '.join(str(d['id']) for d in described)}\n")
    print_table(npc, described)
    if args.face:
        print_face(described)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
