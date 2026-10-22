#!/usr/bin/env python3
"""The tags er-build-planner gives a build, worked out from the build document itself.

    python3 scripts/er-mechanics-build-tags.py <planner build .json>
    python3 scripts/er-mechanics-build-tags.py <planner build .json> --stats vig=58,mnd=8,...,arc=68
    python3 scripts/er-mechanics-build-tags.py --corpus     # agreement with the scraped builds
    python3 scripts/er-mechanics-build-tags.py --selftest

`tags(doc, tables=None)` returns the tag list for a planner build document, the body the
inventory API serves for `/inventories/<id>`: `stats`, `is2h`, `isPvE`, `characterClass`,
`greatRune`, `inventory`, `talismans`, `protectors`, `spells`, `items`, `conditions`. Its
existing `tags` are ignored.

The rule is the planner's own, read out of its client (`notifications-*.js`, mirrored under
`~/.cache/er-build-planner/js`): the tag table `B`, `inferBuildArchetype`, `getIncludedDLCs` and
`characterClassDLC`, which `storeBuild` runs through `autoComputeTags` on every save. Only the
vocabulary of `B` is ever emitted (`VOCABULARY`).

- Archetype, one of `ARCHETYPES` (the `archetype` group, one per build). At RL 80..179, from the
  five damage stats after equipped talismans, armor, the great rune and, with
  `conditions.crystalTears`, the crystal tears (`final_stats`), STR at 1.5x when `is2h`. With
  them sorted high to low, `top` the highest capped at 80 and `total` their sum: when
  `top / total >= 0.4` the highest stat names the build, or Quality when the second is at least
  half of `top` and the two are STR and DEX. Otherwise, with `total < 250`, Quality when
  min(STR, DEX) beats the best of INT/FTH/ARC, max(STR, DEX) > 30 and STR + DEX >= INT + FTH + ARC,
  or Hybrid when that best is over 30 and INT + FTH + ARC >= 1.333 x (STR + DEX).
- `Caster` when the first right- or left-hand slot (`equipIndex` 0 or 3) holds a staff or seal
  (`wepType` 57 / 61) or Carian Sorcery Sword.
- With neither, the archetype comes from the inventory's weapons instead (`scaling_archetype`):
  each scaling's sum over every inventory slot (`correct*` / 100 of the slot's affinity row),
  squared, as shares of the five.
- `PvE` when `isPvE`. The gamemode tags (Invasions, Duels, ...) and the playstyle tags
  (Hardswaps, kbm, Fishing) say how the player plays it, which the document does not hold, so
  they are never emitted.
- `DLC: SotE` / `DLC: Tarnished` when any item the planner counts is from that DLC: every
  inventory weapon and its ash of war, every talisman, armor piece, spell and tool, the crystal
  tears, and the class (not ammunition, not the great rune). `No DLC` only when nothing at all is
  counted, as the planner does: its base items carry `dlc: [0]`, which still counts.

What is a DLC item is read from the game's message files (`er-item-name.py`): Shadow of the
Erdtree names its items in the `_dlc01` FMGs. The Tarnished Pack's (the planner's `dlc: [2]`,
added with 1.17) are named in the base FMGs but numbered as DLC rows, an `EquipParamWeapon` id
whose ten-thousands pair is 50 or more, or an `EquipParamProtector` id from 3000000
(`TARNISHED_ROW`); measured 2026-10-14, those are 8 weapons and 18 armor pieces, exactly the 26
items the planner's data marks `dlc: [2]`. Its two classes, Heavy Knight and Idus Knight, are
`TARNISHED_CLASSES`. Of the 2077 items the planner's data gives a `dlc`, the message files agree on
2073; the other four are the planner's own entries against the game's files (Bonny Butchering
Knife and Prattling Pate "Lamentation" are dlc01 rows it lists as base, Radahn's Spear a base row
it lists as SotE, Golden Vow a name it carries twice).

Measured with `--corpus` on the 5699 scraped builds (2026-10-14), the share whose stored tags in
each group equal these: DLC 94.2% (99.1% against the planner's own item data applied to the same
documents, so the rest are tags not refreshed since the build changed), PvE 94.9%, Caster 94.9%,
archetype 72.4%. Archetype disagreement is mostly a tag the user chose: the planner never replaces
an archetype tag a build already has, 595 builds carry one this rule gives none (most outside RL
80..179), and of the 4221 where both have one, 84.8% agree, the misses spread over every pair.
Counting the stat bonuses of worn gear lifts it from 4083 to 4126 agreeing builds.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import unicodedata
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import er_corpus  # noqa: E402  the corpus floor every mirror read goes through
CORPUS = Path.home() / ".cache/er-build-planner/builds.jsonl"

#: The planner's tag table `B`, by group.
GAMEMODE = ("Invasions", "Duels", "2v2", "PvE", "Co-op/Gank", "Ladder")
PLAYSTYLE = ("Hardswaps", "kbm", "Fishing", "Caster")
ARCHETYPES = ("Strength", "Dexterity", "Intelligence", "Faith", "Arcane", "Quality", "Hybrid")
DLC_TAGS = ("No DLC", "DLC: SotE", "DLC: Tarnished")
VOCABULARY = GAMEMODE + PLAYSTYLE + ARCHETYPES + DLC_TAGS
SOTE, TARNISHED = "DLC: SotE", "DLC: Tarnished"

DAMAGE_STATS = ("str", "dex", "int", "fth", "arc")
#: The planner document spells Endurance `vit`. This module names it `end` and translates where it
#: reads or writes a document's `stats`.
PLANNER_KEY = {"end": "vit"}
STAT_TAG = {"str": "Strength", "dex": "Dexterity", "int": "Intelligence", "fth": "Faith", "arc": "Arcane"}
#: `inferBuildArchetype` reads the stats only from RL 80 up to, not including, 180.
ARCHETYPE_RL = (80, 180)
#: The highest stat counts at most this much against the others.
TOP_CAP = 80
#: Shares of `inferBuildArchetype`'s stat branch: the highest stat over the five's sum, and the
#: second over the highest.
TOP_SHARE, SECOND_SHARE = 0.4, 0.5
#: The spread branch only runs under this five-stat total.
SPREAD_TOTAL = 250
SPREAD_FLOOR = 30
HYBRID_RATIO = 1.333
TWO_HAND_STR = 1.5
#: `wepType` of a glintstone staff and a sacred seal (`er-mechanics-class-best.CASTING_WEP_TYPES`),
#: the planner's `Glintstone Staff` / `Sacred Seal` categories, plus its one named exception.
CASTER_WEP_TYPES = (57, 61)
CASTER_NAMES = ("Carian Sorcery Sword",)
#: `equipIndex` of the first right-hand and the first left-hand slot.
CASTER_SLOTS = (0, 3)
#: `scaling_archetype`'s share thresholds, in the order the planner tests them; the first that
#: holds wins, since the archetype group takes one tag.
SCALING_RULES = (
    ("Dexterity", lambda s: s["dex"] > 0.5 and s["str"] < 0.2),
    ("Quality", lambda s: s["dex"] > 0.33 and s["str"] > 0.33),
    ("Strength", lambda s: s["str"] > 0.5 and s["dex"] < 0.2),
    ("Hybrid", lambda s: s["int"] > 0.33 and s["fth"] > 0.33),
    ("Intelligence", lambda s: s["int"] > 0.5),
    ("Faith", lambda s: s["fth"] > 0.5),
    ("Arcane", lambda s: s["arc"] > 0.45),
)
#: `EquipParamWeapon` scaling field per stat.
CORRECT = {"str": "correctStrength", "dex": "correctAgility", "int": "correctMagic", "fth": "correctFaith",
           "arc": "correctLuck"}
AFFINITY_STEP = 100

#: Message file per kind of item the planner counts.
STEMS = ("WeaponName", "GemName", "AccessoryName", "ProtectorName", "GoodsName")
ASH_PREFIX = "Ash of War: "
#: Tarnished Pack rows: named in the base message files, numbered as DLC rows.
TARNISHED_ROW = {
    "WeaponName": lambda rid: rid // 10000 % 100 >= 50,
    "ProtectorName": lambda rid: rid >= 3000000,
}
#: The planner's class list `Zn` gives these two `dlc: [2]` (CharaInitParam 3011 and 3010).
TARNISHED_CLASSES = ("Heavy Knight", "Idus Knight")
PROTECTOR_PARTS = ("head", "body", "arms", "legs")

_MODS = {}


def _sibling(name: str, fname: str):
    if name not in _MODS:
        mod = sys.modules.get(name)
        if mod is None:
            spec = importlib.util.spec_from_file_location(name, HERE / fname)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
        _MODS[name] = mod
    return _MODS[name]


def fold(name: str) -> str:
    """`er-builds-catalog.fold`: ASCII, case-insensitive, whitespace-normalised, typographic quotes
    and dashes flattened."""
    s = name.replace("‘", "'").replace("’", "'").replace("–", "-").replace("—", "-")
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = s.replace("æ", "ae").replace("Æ", "ae").replace("œ", "oe").replace("ß", "ss")
    return " ".join(s.lower().split())


class Tables:
    """What `tags` reads: the message-file name index, `EquipParamWeapon`, the goods' SpEffects
    and the resources model. Each part is loaded on first use; `ar` reuses an
    `er-mechanics-ar.Tables` already loaded."""

    def __init__(self, ar=None, model=None, corpus=None):
        self._ar, self._model, self._corpus = ar, model, corpus
        self._names = None
        self._goods = None

    @property
    def ar(self):
        if self._ar is None:
            self._ar = _sibling("er_mechanics_ar", "er-mechanics-ar.py").Tables(None)
        return self._ar

    @property
    def model(self):
        if self._model is None:
            self._model = _sibling("er_mechanics_resources", "er-mechanics-resources.py").Model()
        return self._model

    @property
    def names(self):
        """{stem: {folded name: [("base" | "dlc01", row id)]}} from the base and dlc01 bundles."""
        if self._names is None:
            item = _sibling("er_item_name", "er-item-name.py")
            corpus = self._corpus or item.DEFAULT_CORPUS
            if not os.path.isdir(os.path.join(corpus, "item-msgbnd-dcx")):
                raise SystemExit(f"no message files under {corpus}: python3 {HERE / 'er-item-name.py'} --refresh")
            out = {}
            for stem in STEMS:
                idx = {}
                for directory, suffix, src in (("item-msgbnd-dcx", "", "base"),
                                               ("item_dlc01-msgbnd-dcx", "_dlc01", "dlc01")):
                    path = os.path.join(corpus, directory, f"{stem}{suffix}.fmg")
                    for rid, text in item.read_fmg(path).items():
                        if item.is_placeholder(text):
                            continue
                        if stem == "GemName":
                            text = text.removeprefix(ASH_PREFIX)
                        idx.setdefault(fold(text), []).append((src, rid))
                out[stem] = idx
            self._names = out
        return self._names

    @property
    def goods(self):
        """{goods row id: refId_default}, the SpEffect a crystal tear applies."""
        if self._goods is None:
            pr = sys.modules[type(self.model).__module__].PR
            rows, _, _ = pr.rows(pr.param_bytes(pr.load(None), "EquipParamGoods"), ["refId_default"])
            self._goods = {r["id"]: r["refId_default"] for r in rows}
        return self._goods

    def dlc(self, stem: str, name: str):
        """SOTE, TARNISHED, "base", or None for a name no message file of `stem` holds. The
        name's row is its lowest id (`er-builds-catalog`'s rule). A row the base file names is base
        or Tarnished, even when the dlc01 file names it again (Perfumer's Talisman 2220 is in
        both); only a row the dlc01 file alone names is SotE."""
        hits = self.names[stem].get(fold(name))
        if not hits:
            return None
        rid = min(r for _, r in hits)
        if not any(src == "base" and r == rid for src, r in hits):
            return SOTE
        row = TARNISHED_ROW.get(stem)
        return TARNISHED if row and row(rid) else "base"

    def goods_id(self, name: str):
        hits = self.names["GoodsName"].get(fold(name)) or []
        return min((rid for _, rid in hits), default=None)

    def weapon_row(self, name: str, affinity: str | None):
        """`EquipParamWeapon` row of the slot's weapon at its affinity, or None."""
        try:
            return self.ar.weapons[self.ar.find_weapon(name, affinity or "Standard")]
        except SystemExit:
            return None


_DEFAULT = None


def default_tables() -> Tables:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = Tables()
    return _DEFAULT


def _slots(section) -> list:
    return [s for s in (section or {}).get("slots") or [] if s and s.get("name")]


def _equipped(section) -> list[str]:
    return [s["name"] for s in _slots(section) if s.get("equipIndex") is not None]


def final_stats(doc: dict, tables: Tables) -> dict:
    """The planner's `finalStatsWith2H`: each stat plus what equipped talismans and armor, the
    great rune and (with `conditions.crystalTears`) the crystal tears add, capped at 99 and
    left at 0 when the stat is 0; STR x 1.5, truncated, when `is2h`."""
    st = doc.get("stats") or {}
    base = {k: int(st.get(PLANNER_KEY.get(k, k)) or 0) for k in ("vig", "mnd", "end") + DAMAGE_STATS}
    prot = doc.get("protectors") or {}
    armor = [n for part in PROTECTOR_PARTS for n in _equipped(prot.get(part))]
    extra = []
    items = doc.get("items") or {}
    if (doc.get("conditions") or {}).get("crystalTears"):
        for t in items.get("crystalTears") or []:
            gid = tables.goods_id(t) if t else None
            if gid is not None and tables.goods.get(gid, -1) > 0:
                extra.append(tables.goods[gid])
    effects, _ = tables.model.speffects_for(_equipped(doc.get("talismans")), armor, doc.get("greatRune"), extra)
    added = tables.model.effective_stats(base, effects)
    out = {k: (added[k] if base[k] > 0 else 0) for k in base}
    if doc.get("is2h"):
        out["str"] = int(out["str"] * TWO_HAND_STR)
    return out


def stat_archetype(stats: dict, rl: int):
    """`inferBuildArchetype`'s stat branch: the tag the five damage stats `stats` give at `rl`, or
    None."""
    if not ARCHETYPE_RL[0] <= rl < ARCHETYPE_RL[1]:
        return None
    order = sorted(DAMAGE_STATS, key=lambda k: -stats[k])
    total = sum(stats[k] for k in DAMAGE_STATS)
    top = min(TOP_CAP, stats[order[0]])
    if total > 0 and top > 0 and top / total >= TOP_SHARE:
        if stats[order[1]] / top >= SECOND_SHARE and {order[0], order[1]} == {"str", "dex"}:
            return "Quality"
        return STAT_TAG[order[0]]
    if total < SPREAD_TOTAL:
        phys, low, high = stats["str"] + stats["dex"], min(stats["str"], stats["dex"]), max(stats["str"], stats["dex"])
        magic, best = stats["int"] + stats["fth"] + stats["arc"], max(stats["int"], stats["fth"], stats["arc"])
        if low > best and high > SPREAD_FLOOR and phys >= magic:
            return "Quality"
        if best > SPREAD_FLOOR and magic >= phys * HYBRID_RATIO:
            return "Hybrid"
    return None


def caster(doc: dict, tables: Tables) -> bool:
    for s in _slots(doc.get("inventory")):
        if s.get("equipIndex") in CASTER_SLOTS:
            if s["name"] in CASTER_NAMES:
                return True
            row = tables.weapon_row(s["name"], "Standard")
            if row and row["wepType"] in CASTER_WEP_TYPES:
                return True
    return False


def scaling_archetype(doc: dict, tables: Tables):
    """The archetype the inventory's weapons scale with, or None: per stat, the sum over every
    inventory slot of its affinity row's scaling, squared and divided by the slot count, as a
    share of the five (`SCALING_RULES`)."""
    slots = _slots(doc.get("inventory"))
    if not slots:
        return None
    total = dict.fromkeys(DAMAGE_STATS, 0.0)
    for s in slots:
        row = tables.weapon_row(s["name"], s.get("infusion"))
        if row:
            for k in DAMAGE_STATS:
                total[k] += row[CORRECT[k]] / 100.0
    sq = {k: v ** 2 / len(slots) for k, v in total.items()}
    whole = sum(sq.values())
    if not whole:
        return None
    share = {k: v / whole for k, v in sq.items()}
    return next((tag for tag, rule in SCALING_RULES if rule(share)), None)


def counted_items(doc: dict):
    """(stem, name) of every item `getIncludedDLCs` counts."""
    for s in _slots(doc.get("inventory")):
        yield "WeaponName", s["name"]
        if s.get("weaponArt"):
            yield "GemName", s["weaponArt"]
    for s in _slots(doc.get("talismans")):
        yield "AccessoryName", s["name"]
    for s in _slots(doc.get("spells")):
        yield "GoodsName", s["name"]
    prot = doc.get("protectors") or {}
    for part in PROTECTOR_PARTS:
        for s in _slots(prot.get(part)):
            yield "ProtectorName", s["name"]
    items = doc.get("items") or {}
    for s in _slots(items.get("tools")):
        yield "GoodsName", s["name"]
    for t in items.get("crystalTears") or []:
        if t:
            yield "GoodsName", t


def dlc_tags(doc: dict, tables: Tables, unknown: list | None = None) -> list[str]:
    """`DLC: SotE` / `DLC: Tarnished` for the DLCs the build's items and class come from, or
    `No DLC` when it counts no item at all. Names no message file holds go to `unknown`."""
    found, counted = set(), False
    for stem, name in counted_items(doc):
        d = tables.dlc(stem, name)
        if d is None:
            if unknown is not None:
                unknown.append((stem, name))
            continue
        counted = True
        if d != "base":
            found.add(d)
    if doc.get("characterClass") in TARNISHED_CLASSES:
        found.add(TARNISHED)
        counted = True
    if not counted:
        return ["No DLC"]
    return [t for t in (SOTE, TARNISHED) if t in found]


def tags(doc: dict, tables: Tables | None = None) -> list[str]:
    """The planner tags `doc` earns, in the order the planner adds them: archetype, `Caster`,
    `PvE`, then the DLC tags."""
    tables = tables or default_tables()
    out = []
    st = doc.get("stats") or {}
    arch = stat_archetype(final_stats(doc, tables), int(st.get("rl") or 0))
    if arch:
        out.append(arch)
    if caster(doc, tables):
        out.append("Caster")
    if not out:
        arch = scaling_archetype(doc, tables)
        if arch:
            out.append(arch)
    if doc.get("isPvE") is True:
        out.append("PvE")
    out += dlc_tags(doc, tables)
    assert all(t in VOCABULARY for t in out), out
    return out


def parse_stats(text: str) -> dict:
    """`vig=..,end=..,...` as a planner document's `stats`: `end` (or `vit`) is written as the
    planner's `vit`."""
    st = {k.strip(): int(v) for k, v in (p.split("=") for p in text.split(",") if p.strip())}
    st = {PLANNER_KEY.get(k, k): v for k, v in st.items()}
    if "rl" not in st:
        # The game's level: the eight attributes' sum less 79.
        st["rl"] = sum(st.get(PLANNER_KEY.get(k, k), 0) for k in ("vig", "mnd", "end") + DAMAGE_STATS) - 79
    return st


# ---- corpus agreement -----------------------------------------------------------------------

def corpus(path: Path = CORPUS, show: int = 0, tables: Tables | None = None) -> dict:
    """How often `tags` matches the tags the scraped builds carry, per group. The planner adds an
    inferred archetype only when the build has none (`addBuildTag` on an exclusive group), so a
    hand-picked archetype, or stats changed after the last save, disagree; the DLC tags it
    recomputes on every save."""
    tables = tables or default_tables()
    groups = {"archetype": ARCHETYPES, "caster": ("Caster",), "pve": ("PvE",), "dlc": DLC_TAGS}
    res = {g: Counter() for g in groups}
    examples = {g: [] for g in groups}
    unknown = Counter()
    n = 0
    for row in er_corpus.iter_rows(path):
        b = row["build"]
        n += 1
        have = set(b.get("tags") or [])
        miss = []
        got = set(tags(b, tables)) | set(dlc_tags(b, tables, miss))
        unknown.update(f"{s}: {x}" for s, x in miss)
        for g, vocab in groups.items():
            mine, theirs = got & set(vocab), have & set(vocab)
            same = mine == theirs
            res[g]["agree" if same else "disagree"] += 1
            if g == "dlc" and miss and not same:
                res[g]["disagree with an unknown item"] += 1
            if g == "archetype":
                kind = "one archetype tag" if len(theirs) == 1 else "none" if not theirs else "several"
                res[g][f"builds with {kind}"] += 1
                res[g][f"builds with {kind}, agree"] += same
            if not same and len(examples[g]) < show:
                examples[g].append({"id": b.get("id"), "theirs": sorted(theirs), "ours": sorted(mine),
                                    "stats": b.get("stats"), "is2h": b.get("is2h")})
    rates = {g: round(c["agree"] / n, 4) if n else None for g, c in res.items()}
    return {"builds": n, "agreement": rates, "groups": {g: dict(c) for g, c in res.items()},
            "examples": examples, "unknown_items": unknown.most_common(20)}


# ---- selftest ---------------------------------------------------------------------------------

def _doc(stats, **kw):
    d = {"stats": parse_stats(",".join(f"{k}={v}" for k, v in stats.items())),
         "is2h": False, "isPvE": False, "characterClass": "Vagabond", "greatRune": None,
         "inventory": {"slots": []}, "talismans": {"slots": []}, "spells": {"slots": []},
         "protectors": {p: {"slots": []} for p in PROTECTOR_PARTS},
         "items": {"tools": {"slots": []}, "crystalTears": []}, "conditions": {"crystalTears": True}}
    d.update(kw)
    return d


def selftest() -> int:
    fails = []

    def check(label, got, want):
        ok = got == want
        print(f"{'ok' if ok else 'FAIL'}  {label}: {got!r}" + ("" if ok else f" (want {want!r})"))
        if not ok:
            fails.append(label)

    tab = default_tables()
    base = {"vig": 56, "mnd": 8, "end": 43, "str": 18, "dex": 80, "int": 7, "fth": 8, "arc": 9}
    knife = [{"name": "Dagger", "infusion": "Keen", "equipIndex": 0}]
    check("RL from the attributes", parse_stats("vig=58,mnd=8,end=38,str=20,dex=22,int=7,fth=8,arc=68")["rl"], 150)
    check("DEX 80 at RL 150 is Dexterity", tags(_doc(base, inventory={"slots": knife}), tab), ["Dexterity"])
    arc = {"vig": 58, "mnd": 8, "end": 38, "str": 20, "dex": 22, "int": 7, "fth": 8, "arc": 68}
    check("ARC 68 over DEX 22 is Arcane", tags(_doc(arc, inventory={"slots": knife}), tab), ["Arcane"])
    q = dict(base, str=45, dex=60, vig=40)
    check("STR 45 / DEX 60 is Quality (second at least half the first)", stat_archetype(q, 150), "Quality")
    check("DEX 60 / ARC 45 is Dexterity: the pair is not STR and DEX",
          stat_archetype(dict(q, str=10, arc=45), 150), "Dexterity")
    check("STR 25 one-handed is under half of DEX 60: Dexterity",
          stat_archetype(final_stats(_doc(dict(base, str=25, dex=60)), tab), 150), "Dexterity")
    check("two-handed it counts as 37, over half: Quality",
          stat_archetype(final_stats(_doc(dict(base, str=25, dex=60), is2h=True), tab), 150), "Quality")
    flat = {"str": 35, "dex": 40, "int": 20, "fth": 20, "arc": 20}
    check("a spread under 250 with min(STR, DEX) over the rest is Quality", stat_archetype(flat, 150), "Quality")
    check("a spread with INT + FTH + ARC >= 1.333 x STR + DEX is Hybrid",
          stat_archetype({"str": 12, "dex": 12, "int": 40, "fth": 40, "arc": 9}, 150), "Hybrid")
    check("RL 60 is outside the archetype window", stat_archetype({"str": 10, "dex": 60, "int": 9, "fth": 9,
                                                                   "arc": 9}, 60), None)
    check("RL 180 is outside it too", stat_archetype({"str": 10, "dex": 60, "int": 9, "fth": 9, "arc": 9}, 180), None)
    check("Radagon's Soreseal worn adds 5 STR and DEX",
          {k: final_stats(_doc(base, talismans={"slots": [{"name": "Radagon's Soreseal", "equipIndex": 0}]}), tab)[k]
           for k in ("str", "dex")}, {"str": 23, "dex": 85})
    check("a talisman carried, not worn, adds nothing",
          final_stats(_doc(base, talismans={"slots": [{"name": "Radagon's Soreseal"}]}), tab)["str"], 18)
    check("Strength-knot Crystal Tear adds 10 STR with crystal tears on",
          final_stats(_doc(base, items={"crystalTears": ["Strength-knot Crystal Tear"]}), tab)["str"], 28)
    check("and nothing with them off",
          final_stats(_doc(base, items={"crystalTears": ["Strength-knot Crystal Tear"]},
                           conditions={"crystalTears": False}), tab)["str"], 18)
    staff = [{"name": "Lusat's Glintstone Staff", "infusion": "Standard", "equipIndex": 3}]
    int_doc = _doc(dict(base, dex=10, int=80), inventory={"slots": staff})
    check("a staff in the first left-hand slot adds Caster", tags(int_doc, tab), ["Intelligence", "Caster"])
    check("a staff in the third slot does not",
          caster(_doc(base, inventory={"slots": [dict(staff[0], equipIndex=5)]}), tab), False)
    low = _doc({"vig": 10, "mnd": 10, "end": 10, "str": 10, "dex": 10, "int": 10, "fth": 10, "arc": 10},
               inventory={"slots": [{"name": "Uchigatana", "infusion": "Keen"}]})
    check("at RL 1 the inventory's scaling decides: a Keen Uchigatana is Dexterity", tags(low, tab), ["Dexterity"])
    check("a Heavy Claymore is Strength", scaling_archetype(
        dict(low, inventory={"slots": [{"name": "Claymore", "infusion": "Heavy"}]}), tab), "Strength")
    check("isPvE adds PvE", tags(_doc(base, isPvE=True, inventory={"slots": knife}), tab), ["Dexterity", "PvE"])
    check("isPvE None adds nothing", "PvE" in tags(_doc(base, isPvE=None, inventory={"slots": knife}), tab), False)
    check("Main-gauche (dlc01 WeaponName) is SotE", tab.dlc("WeaponName", "Main-gauche"), SOTE)
    check("Verdigris Armor (dlc01 ProtectorName) is SotE", tab.dlc("ProtectorName", "Verdigris Armor"), SOTE)
    check("Leontiel's Greatsword (base file, row 3560000) is Tarnished", tab.dlc("WeaponName", "Leontiel's Greatsword"),
          TARNISHED)
    check("Steel Helm (base file, row 5370000) is Tarnished", tab.dlc("ProtectorName", "Steel Helm"), TARNISHED)
    check("Uchigatana is base", tab.dlc("WeaponName", "Uchigatana"), "base")
    check("an ash of war is looked up without its prefix", tab.dlc("GemName", "Dryleaf Whirlwind"), SOTE)
    check("a base build with items is untagged", dlc_tags(_doc(base, inventory={"slots": knife}), tab), [])
    check("a build with no item at all is No DLC", dlc_tags(_doc(base), tab), ["No DLC"])
    check("Heavy Knight is a Tarnished Pack class", dlc_tags(_doc(base, characterClass="Heavy Knight"), tab),
          [TARNISHED])
    check("Perfumer's Talisman is base: row 2220 is in the base file, though dlc01 names it again",
          tab.dlc("AccessoryName", "Perfumer's Talisman"), "base")
    check("a SotE talisman in storage still counts",
          dlc_tags(_doc(base, inventory={"slots": knife}, talismans={"slots": [{"name": "Crimson Amber Medallion +3"}]}),
                   tab), [SOTE])
    check("a crystal tear counts: Viridian Hidden Tear is SotE",
          dlc_tags(_doc(base, items={"crystalTears": ["Crimson Bubbletear", "Viridian Hidden Tear"]}), tab), [SOTE])
    check("the doc's own tags are ignored",
          tags(_doc(base, tags=["Strength", "Duels"], inventory={"slots": knife}), tab), ["Dexterity"])
    check("every tag is in the planner's vocabulary", set(VOCABULARY) >= {"Dexterity", SOTE, TARNISHED, "No DLC"},
          True)
    print("selftest", "passed" if not fails else f"FAILED: {len(fails)}")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("build", nargs="?", type=Path, help="a planner build document (.json)")
    ap.add_argument("--stats", help="replace the document's stats: vig=..,mnd=..,end=..,str=..,dex=..,int=..,"
                                    "fth=..,arc=.. (rl from their sum unless given)")
    ap.add_argument("--corpus", nargs="?", const=CORPUS, type=Path,
                    help=f"report agreement with the tags of the scraped builds (default {CORPUS})")
    ap.add_argument("--show", type=int, default=0, help="with --corpus: disagreements to list per group")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.corpus:
        print(json.dumps(corpus(a.corpus, a.show), indent=1))
        return 0
    if not a.build:
        ap.error("name a planner build .json, or pass --corpus / --selftest")
    doc = json.loads(a.build.read_text(encoding="utf-8"))
    if a.stats:
        doc["stats"] = parse_stats(a.stats)
    print(json.dumps(tags(doc)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
