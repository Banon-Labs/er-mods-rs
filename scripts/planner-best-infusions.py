#!/usr/bin/env python3
"""Give every armament in a build planner build the affinity that hits hardest, and store it.

    python3 scripts/planner-best-infusions.py e46f4946f84c6d                # print what would change
    python3 scripts/planner-best-infusions.py '<planner ?b= link>' --apply  # write it onto the build
    python3 scripts/planner-best-infusions.py --selftest

Damage is `er-builds-optimize`'s `damage` objective: one motion-value-100 hit on the median defender
of the corpus builds at RL `--rl` +- `--window` (`bracket_defender`, the planner's computed defense
and absorption), here narrowed to the builds whose Endurance is within `--end-window` of
`--defender-end` (default 44; 0 keeps the whole window). The attacker is the build itself: its
attributes with the planner's computed changes laid over them, its `is2h` grip unless
`--two-handed` / `--one-handed` says otherwise, and each copy at its own level or the build's
`weaponUpgrade` (a somber weapon read on the regular-stone scale, `SOMBER_LEVEL_FOR_REGULAR`).
Grease and status build-up are not counted.

The affinity has to be one the game lets that copy carry with its ash of war:
`CanMountGemWithAffinityOnWeapon` (`er-mechanics-ashes.AshTables.can_mount`). A copy with no ash
written carries its own skill, and an infusion needs an ash, so it changes affinity only through the
gem that grants that same skill, which is then written as its ash; when no gem grants it, it stays
Standard. A better affinity the ash forbids is reported, not applied.

`--apply` stores the build in place on the account of `~/.config/er-mods-rs/planner-login-code`
(`planner-sync-items.py`'s), and reads it back.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys
import urllib.parse

HERE = pathlib.Path(__file__).resolve().parent


def _sibling(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


OPT = _sibling("er-builds-optimize")
AR, DEF = OPT.AR, OPT.DEF
ASH = _sibling("er-mechanics-ashes")
CATALOG = _sibling("er-builds-catalog")
SAVE_DOC = _sibling("er-save-planner-doc")
LINK = _sibling("er-npc-planner-link")
UPLOAD = LINK.upload

LOGIN_CODE = pathlib.Path("~/.config/er-mods-rs/planner-login-code")
SERVER_FIELDS = ("id", "author", "views", "revision", "computed")
DAMAGE_STATS = OPT.DAMAGE_STATS
#: The planner spells Endurance `vit`.
PLANNER_STAT = {"end": "vit"}


def build_id(arg: str) -> str:
    """A build id from an id or a planner `?b=` link."""
    if "://" not in arg:
        return arg
    found = urllib.parse.parse_qs(urllib.parse.urlparse(arg).query).get("b")
    if not found:
        sys.exit(f"{arg!r} has no ?b= build id")
    return found[0]


def defender(rl: int, window: int, end: int, end_window: int) -> tuple[dict, str]:
    rows = OPT.corpus_rows(OPT.CACHE / "builds.jsonl", rl - window, rl + window)
    scope = f"RL {rl - window}-{rl + window}"
    if end:
        rows = [r for r in rows if abs(r["stats"]["end"] - end) <= end_window]
        scope += f", END {end - end_window}-{end + end_window}"
    return OPT.bracket_defender(rows), scope


def attacker(doc: dict) -> dict:
    """The build's damage stats, with the planner's computed changes (talismans, great rune) on top."""
    stats = {k: doc["stats"][PLANNER_STAT.get(k, k)] for k in DAMAGE_STATS}
    stats.update({k: v for k, v in ((doc.get("computed") or {}).get("stats") or {}).items() if k in stats})
    return stats


class Game:
    """The weapon, ash and name tables one run reads."""

    def __init__(self):
        self.t = AR.Tables(None)
        self.ash = ASH.AshTables()
        self.inventory_gem = self.ash.ash_gems()
        cat = json.loads((OPT.CACHE / "catalog.json").read_text())
        self.armament, self.gems = cat["armament"], cat["ash of war"]

    def weapon(self, name: str) -> int | None:
        """The base row of an armament name: the catalog's, else the regulation's own row name (an
        item newer than the catalog, e.g. Leontiel's Greatsword under 2.7.2.0)."""
        found = self.armament.get(CATALOG.fold(name))
        if found is None:
            try:
                found = self.t.find_weapon(name)
            except SystemExit:
                return None
        return found

    def gem(self, ash: str | None, base: int) -> int | None:
        """The ash-of-war item a copy mounts: its written ash, or the gem granting its own skill.
        Several gem rows grant one skill; the inventory item is the one `ash_gems` picks."""
        if ash is None:
            return self.inventory_gem.get(self.t.weapons[base]["swordArtsParamId"])
        gid = self.gems.get(CATALOG.fold(ash))
        return None if gid is None else self.inventory_gem.get(self.ash.gem[gid]["swordArtsParamId"], gid)

    def ash_name(self, gid: int) -> str:
        return self.ash.gem_names[gid].removeprefix("Ash of War: ")

    def level(self, row: int, upgrade: int) -> int:
        top = self.t.max_level(self.t.weapons[row]["reinforceTypeId"])
        if top < SAVE_DOC.MAX_REGULAR_LEVEL:
            upgrade = SAVE_DOC.SOMBER_LEVEL_FOR_REGULAR[upgrade]
        return min(upgrade, top)

    def damage(self, base: int, affinity: str, upgrade: int, stats: dict, two: bool, dfn: dict) -> float:
        row = base + AR.AFFINITIES.index(affinity) * 100
        r = AR.attack_rating(self.t, base, affinity, self.level(row, upgrade), stats, two)
        by = {el: r["damage"].get(el, {}).get("total", 0.0) for el in OPT.ELEMENTS}
        return DEF.damage(by, 100.0, dfn)["total"]


def choose(g: Game, slot: dict, upgrade: int, stats: dict, two: bool, dfn: dict) -> dict:
    """{weapon, old, new, ash, dmg_old, dmg_new, blocked, note} for one inventory slot. `ash` is the
    ash to write, `blocked` the best affinity the ash forbids and its damage, when one beats `new`."""
    old = slot.get("infusion") or "Standard"
    out = {"weapon": slot["name"], "old": old, "new": old, "ash": slot.get("weaponArt"), "blocked": None}
    base = g.weapon(slot["name"])
    if base is None:
        return {**out, "note": "not in the catalog"}
    if old not in AR.AFFINITIES:
        return {**out, "note": f"unknown infusion {old!r}"}
    gid = g.gem(slot.get("weaponArt"), base)
    level = slot["upgrade"] if slot.get("upgrade") is not None else upgrade
    allowed, forbidden = {}, {}
    for aff in OPT.affinities(g.t, base):
        dmg = g.damage(base, aff, level, stats, two, dfn)
        ok = aff == "Standard" or (gid is not None and g.ash.can_mount(
            base, gid, AR.AFFINITIES.index(aff), g.level(base, level))[0])
        (allowed if ok else forbidden)[aff] = dmg
    # The slot's own affinity is always kept as a candidate: the planner holds it even where the
    # game would not let it be made, and it is never replaced by something that hits softer.
    if old not in allowed:
        try:
            allowed[old] = g.damage(base, old, level, stats, two, dfn)
        except SystemExit as e:
            return {**out, "note": str(e)}
    new = max(allowed, key=lambda a: (allowed[a], a == old))
    out.update(new=new, dmg_old=allowed[old], dmg_new=allowed[new])
    if forbidden:
        best = max(forbidden, key=forbidden.get)
        if forbidden[best] > allowed[new]:
            out["blocked"] = (best, forbidden[best])
    if new != old and slot.get("weaponArt") is None and new != "Standard":
        out["ash"] = UPLOAD.planner_spelling([g.ash_name(gid)])[0]
    if len(allowed) == 1 and not forbidden:
        out["note"] = "one affinity"
    return out


def apply(doc: dict, choices: list[dict]) -> list[tuple]:
    """Write each choice into its slot; returns the (name, infusion, ash) list that was written."""
    for s, c in zip(doc["inventory"]["slots"], choices):
        if c["new"] != c["old"]:
            s["infusion"] = c["new"]
            s["weaponArt"] = c["ash"]
    return [(s["name"], s.get("infusion"), s.get("weaponArt")) for s in doc["inventory"]["slots"]]


def report(choices: list[dict]) -> None:
    print(f"{'#':>3} {'weapon':<28} {'ash':<24} {'from':<9} {'to':<9} {'dmg':>13}  note")
    for i, c in enumerate(choices):
        if "dmg_old" not in c:
            print(f"{i:3} {c['weapon'][:28]:<28} {(c['ash'] or '-')[:24]:<24} {c['old']:<9} {'':<9} {'':>13}  {c['note']}")
            continue
        gain = (c["dmg_new"] / c["dmg_old"] - 1) * 100 if c["dmg_old"] else 0.0
        dmg = f"{c['dmg_old']:5.0f}>{c['dmg_new']:<5.0f}" if c["new"] != c["old"] else f"{c['dmg_new']:5.0f}"
        note = [f"+{gain:.1f}%"] if c["new"] != c["old"] else []
        if c["blocked"]:
            aff, d = c["blocked"]
            note.append(f"ash forbids {aff} ({d:.0f}, +{(d / c['dmg_new'] - 1) * 100:.1f}%)")
        if c.get("note"):
            note.append(c["note"])
        print(f"{i:3} {c['weapon'][:28]:<28} {(c['ash'] or '-')[:24]:<24} {c['old']:<9} "
              f"{c['new'] if c['new'] != c['old'] else '=':<9} {dmg:>13}  {', '.join(note)}")


def selftest() -> int:
    """Offline: no planner read, and a fixed defender so the corpus does not move the answers: the
    RL 140-160, Endurance 42-46 median measured 2026-11-07 (153 builds)."""
    g = Game()
    absorption = {"physical": 34.5, "strike": 31.9, "slash": 34.8, "pierce": 33.4, "magic": 25.8,
                  "fire": 27.0, "lightning": 25.6, "holy": 26.2}
    dfn = {"defense": {"physical": 142, "magic": 152, "fire": 192, "lightning": 133, "holy": 148},
           "armor_mult": {k: 1 - v / 100 for k, v in absorption.items()}, "effect_mult": {k: 1.0 for k in DEF.TYPES}}
    # DEX 14 clears the Claymore's 13; below it every physical row takes the 40% penalty.
    strength = {"str": 80, "dex": 14, "int": 9, "fth": 9, "arc": 9}
    fails = []

    def check(cond, msg):
        print(("ok   " if cond else "FAIL ") + msg)
        if not cond:
            fails.append(msg)

    unique = choose(g, {"name": "Bloodhound's Fang", "infusion": "Standard", "weaponArt": None}, 25, strength, False, dfn)
    check(unique["new"] == "Standard" and unique.get("note") == "one affinity", "a unique weapon stays Standard")
    bow = choose(g, {"name": "Greatbow", "infusion": "Standard", "weaponArt": "Rain of Arrows"}, 25, strength, False, dfn)
    check(bow["new"] == "Standard", "a greatbow (disableGemAttr) stays Standard")
    clay = choose(g, {"name": "Claymore", "infusion": "Keen", "weaponArt": "Lion's Claw"}, 25, strength, False, dfn)
    check(clay["new"] == "Heavy" and clay["dmg_new"] > clay["dmg_old"], f"STR 80 Claymore goes Heavy (got {clay['new']})")
    own = choose(g, {"name": "Claymore", "infusion": "Standard", "weaponArt": None}, 25, strength, False, dfn)
    check(own["new"] == "Heavy" and own["ash"] == "Lion's Claw",
          f"a Claymore on its own skill mounts that skill's ash to go Heavy (got {own['new']}, {own['ash']})")
    # Every gem's forbidden affinity must stay forbidden: walk one ash that does not allow them all.
    claymore = g.weapon("Claymore")
    narrow = next(gid for sid, gid in sorted(g.inventory_gem.items())
                  if g.ash.can_mount(claymore, gid, 0, 25)[0]
                  and not all(g.ash.can_mount(claymore, gid, i, 25)[0] for i in range(len(AR.AFFINITIES))))
    name = UPLOAD.planner_spelling([g.ash_name(narrow)])[0]
    for stats in (strength, {"str": 16, "dex": 14, "int": 80, "fth": 9, "arc": 9},
                  {"str": 16, "dex": 14, "int": 9, "fth": 80, "arc": 9}):
        c = choose(g, {"name": "Claymore", "infusion": "Standard", "weaponArt": name}, 25, stats, False, dfn)
        check(g.ash.can_mount(claymore, narrow, AR.AFFINITIES.index(c["new"]), 25)[0],
              f"{name} on Claymore at {stats}: {c['new']} is one the ash allows")
    somber = g.weapon("Moonveil")
    check(g.level(somber, 25) == 10 and g.level(claymore, 25) == 25, "+25 reads as +10 on a somber weapon")
    check(g.level(g.weapon("Meteorite Staff"), 25) == 0, "+25 reads as +0 on a weapon that cannot be reinforced")
    print("selftest", "FAILED" if fails else "passed")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("build", nargs="?", help="build id or planner ?b= link")
    ap.add_argument("--rl", type=int, default=150, help="defender rune level (default 150)")
    ap.add_argument("--window", type=int, default=10, help="corpus RL window around --rl (default 10)")
    ap.add_argument("--defender-end", type=int, default=44, help="defender Endurance, 0 for any (default 44)")
    ap.add_argument("--end-window", type=int, default=2, help="Endurance window around --defender-end (default 2)")
    grip = ap.add_mutually_exclusive_group()
    grip.add_argument("--two-handed", dest="two", action="store_true", default=None)
    grip.add_argument("--one-handed", dest="two", action="store_false")
    ap.add_argument("--apply", action="store_true", help="store the result on the build (default: print only)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.build:
        ap.error("a build id or link is required")
    bid = build_id(a.build)
    doc = UPLOAD.request(f"{UPLOAD.API}/{bid}")
    stats = attacker(doc)
    two = bool(doc.get("is2h")) if a.two is None else a.two
    dfn, scope = defender(a.rl, a.window, a.defender_end, a.end_window)
    print(f"{doc.get('name')!r} {bid}: {' '.join(f'{k} {v}' for k, v in stats.items())}, "
          f"{'two' if two else 'one'}-handed, weapons +{doc.get('weaponUpgrade')}")
    print(f"defender: median of {dfn['n']} corpus builds, {scope}: defense "
          + " ".join(f"{k[:4]} {v:.0f}" for k, v in dfn["defense"].items()) + "; absorption "
          + " ".join(f"{k[:4]} {(1 - v) * 100:.1f}%" for k, v in dfn["armor_mult"].items()))
    g = Game()
    choices = [choose(g, s, doc.get("weaponUpgrade") or SAVE_DOC.MAX_REGULAR_LEVEL, stats, two, dfn)
               for s in doc["inventory"]["slots"]]
    report(choices)
    changed = sum(c["new"] != c["old"] for c in choices)
    print(f"{changed} of {len(choices)} copies change affinity")
    if not a.apply or not changed:
        return 0
    want = apply(doc, choices)
    for k in SERVER_FIELDS:
        doc.pop(k, None)
    session = LINK.load_session(LOGIN_CODE)
    stored = UPLOAD.request(UPLOAD.API, {"id": bid, "data": doc, "gameId": 1, "version": doc.get("version")},
                            LINK.headers(session))
    back = UPLOAD.request(f"{UPLOAD.API}/{bid}")
    got = [(s["name"], s.get("infusion"), s.get("weaponArt")) for s in back["inventory"]["slots"]]
    ok = stored.get("id") == bid and got == want
    print(f"{'stored' if ok else 'STORE FAILED'}: {UPLOAD.PLANNER}{bid}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
