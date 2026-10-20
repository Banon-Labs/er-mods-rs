#!/usr/bin/env python3
"""Upload an ash-of-war loadout as a new build planner build.

    python3 scripts/planner-ash-loadout-upload.py --base <build id> --plan <plan.json> \
        [--combo "Guardian's Swordspear+Butchering Knife"] [--name ...]

`--plan` is `er-mechanics-ash-loadout.py --plan-json` output: one entry per ash, naming the weapon
copy that carries it. Everything except the weapons (class, stats, armor, talismans, great rune,
face) is copied from `--base`, except what `--gear` replaces: armor and talisman sets
(`apply_gear`), and the great rune, crystal tears, tools, flasks, spells and tags (`apply_kit`).

Weapon inventory: one +25 copy per plan entry with its ash and affinity, ordered so each weapon's
copies sit together (weapons with the most copies first; within a weapon bullet, then non-bullet,
then melee bullet, best damage/s first). Sorting is set to acquisition order so the planner shows
that grouping.

Weapon equip sets (`sets.weapons`; a slot's `equipSet[i]` is its equip position in set i, 0-2 the
right hand, 3-5 the left -- read from the planner's own `setSlotEquipIndex` and equip-slot
rendering):

- one kit per weapon: right hand 1-3 hold that weapon's best copy of each category;
- one per `--combo RIGHT+LEFT`: right hand 1 the right weapon's best copy, left hand 1 the left
  weapon's.

The first kit is the active set.

A `{"slots", "sets"}` plan may also carry `ammo` ({arrow1, arrow2, bolt1, bolt2}: ammunition
names, `er-mechanics-combo-loadout.py --ranged`); those replace the base build's equipped
ammunition, which the planner stores under `items.ammo` rather than in the weapon inventory.
Shields and bows need nothing else: they are weapon copies like any other.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import sys
import unicodedata
import urllib.error
import urllib.request

API = "https://er-inventory-api.nyasu.business/inventories"
PLANNER = "https://er-build-planner.nyasu.business/?b="
USER_AGENT = "er-mods-rs build-import (+github.com/Banon-Labs)"
SESSION = (pathlib.Path(os.environ.get("ER_GAME_DIR", pathlib.Path.home()
    / ".local/share/Steam/steamapps/common/ELDEN RING/Game")) / "er-build-planner-session.json")
SERVER_FIELDS = ("id", "author", "views", "revision", "computed")
CATEGORIES = ("bullet", "non-bullet", "melee bullet")
LEFT_HAND_1 = 3


def request(url, body=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT,
        "Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        sys.exit(f"{req.get_method()} {url}: HTTP {e.code}: {e.read()[:500]!r}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True, help="build id whose class, stats and gear are kept")
    ap.add_argument("--plan", type=pathlib.Path, required=True)
    ap.add_argument("--combo", action="append", default=[], help="RIGHT+LEFT weapon names, repeatable")
    ap.add_argument("--upgrade", type=int, default=25)
    ap.add_argument("--stats", help="override the base build's attributes, e.g. str=80,dex=20,vit=41")
    ap.add_argument("--class", dest="character_class",
                    help="override the base build's starting class (the planner's `characterClass`, e.g. Hero)")
    ap.add_argument("--name")
    ap.add_argument("--description", default="")
    ap.add_argument("--update", default="", metavar="ID",
                    help="overwrite this account's build ID instead of storing a new one (the account "
                         "has a cap on stored builds: HTTP 403 \"Exceeding max inventory count\")")
    ap.add_argument("--gear", type=pathlib.Path,
                    help="armor and talisman sets to equip instead of the base build's, and the great rune, "
                         "tears, tools, flasks, spells and tags (see `apply_gear`, `apply_kit`)")
    ap.add_argument("--dry-run", action="store_true", help="print the inventory and sets, upload nothing")
    a = ap.parse_args()

    plan = json.loads(a.plan.read_text())
    if isinstance(plan, dict):
        return upload_with_sets(a, plan)
    count = {}
    for p in plan:
        count[p["weapon"]] = count.get(p["weapon"], 0) + 1
    weapons = sorted(count, key=lambda w: -count[w])
    plan.sort(key=lambda p: (weapons.index(p["weapon"]), CATEGORIES.index(p["cat"]), -p["dps"]))

    set_names = [f"{w} kit" for w in weapons]
    combos = []
    for c in a.combo:
        right, _, left = c.partition("+")
        if right not in count or left not in count:
            ap.error(f"--combo {c!r}: both weapons must be in the plan ({', '.join(weapons)})")
        combos.append((right, left))
        set_names.append(f"{right} + {left}")
    slots = [{"name": p["weapon"], "order": i, "upgrade": a.upgrade, "infusion": p["affinity"],
              "weaponArt": p["ash"]} for i, p in enumerate(plan)]
    equip = [[None] * len(set_names) for _ in slots]

    def first(weapon, cat=None):
        return next((i for i, p in enumerate(plan) if p["weapon"] == weapon and (cat is None or p["cat"] == cat)),
                    None)

    for s, w in enumerate(weapons):
        pos = 0
        for cat in CATEGORIES:
            i = first(w, cat)
            if i is not None:
                equip[i][s] = pos
                pos += 1
    for k, (right, left) in enumerate(combos):
        s = len(weapons) + k
        equip[first(right)][s] = 0
        equip[first(left)][s] = LEFT_HAND_1
    for slot, e in zip(slots, equip):
        if any(v is not None for v in e):
            slot["equipSet"] = e
            if e[0] is not None:
                slot["equipIndex"] = e[0]

    for p, slot in zip(plan, slots):
        print(f"{slot['order']:2} {p['weapon']:24} {p['affinity']:8} {p['cat']:12} {p['ash']:28} "
              f"sets={slot.get('equipSet')}")
    print("sets:", set_names)
    return publish(a, slots, set_names)


def upload_with_sets(a, plan):
    """`plan` = {"slots": [{weapon, ash, affinity, ...}], "sets": [{name, right, left}]} with slot
    indices (`er-mechanics-combo-loadout.py`): the inventory is the slots in that order."""
    slots = [{"name": p["weapon"], "order": i, "upgrade": p.get("upgrade", a.upgrade), "infusion": p["affinity"],
              "weaponArt": p["ash"]} for i, p in enumerate(plan["slots"])]
    names = [s["name"] for s in plan["sets"]] or ["Default"]
    equip = [[None] * len(names) for _ in slots]
    for k, s in enumerate(plan["sets"]):
        for pos, i in enumerate(s["right"]):
            equip[i][k] = pos
        for pos, i in enumerate(s["left"]):
            equip[i][k] = LEFT_HAND_1 + pos
    for slot, e in zip(slots, equip):
        if any(v is not None for v in e):
            slot["equipSet"] = e
            if e[0] is not None:
                slot["equipIndex"] = e[0]
    print(f"{len(slots)} weapon copies, {len(names)} equip sets")
    if a.dry_run:
        for s in plan["sets"]:
            print(f"  set {s['name']}: right {s['right']}, left {s['left']}")
        for i, p in enumerate(plan["slots"]):
            if p.get("theme", "").startswith(("Shields", "Ranged")):
                print(f"  {i:4} {p['affinity']} {p['weapon']} +{p.get('upgrade', a.upgrade)} {p.get('skill') or ''}")
    return publish(a, slots, names, plan.get("ammo"))


HERE = pathlib.Path(__file__).resolve().parent
PLANNER_NAMES = HERE.parent / "data" / "planner-item-names.json"


def fold(name):
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return " ".join(plain.replace("’", "'").lower().split())


def planner_spelling(names):
    """Each item name as the planner itself writes it, or exit naming the ones it does not have.

    The planner builds a slot's icon URL from the stored name, and only its own spelling has an
    icon: `Misericorde` (the regulation row name, and what this script uploaded after the 2026-10-09
    accent stripping) is a 404 under `weapons-hi/`, `Miséricorde` is not. A name the planner has
    no entry for would render the same broken slot, so nothing is uploaded until every name
    resolves. The list is `scripts/planner-item-names.py`'s snapshot of the planner's bundle."""
    known = json.loads(PLANNER_NAMES.read_text(encoding="utf-8"))
    out, missing = [], []
    for n in names:
        spelled = known.get(fold(n))
        if spelled is None:
            missing.append(n)
        out.append(spelled or n)
    if missing:
        sys.exit(f"not on the planner, nothing uploaded: {sorted(set(missing))} "
                 f"(refresh {PLANNER_NAMES.name} with scripts/planner-item-names.py if the planner added them)")
    return out


PROTECTOR_SLOTS = ("head", "body", "arms", "legs")
#: The planner equips a protector at index 1 (`setSlotEquipIndex('protectors', slot, 1)` in its
#: router bundle) and a talisman at its slot position 0-3.
PROTECTOR_EQUIP_INDEX = 1


def _equip_sets(inv_slots, sets, pick):
    """Clear every equip mark in `inv_slots`, then mark each set's items. `pick(set)` yields
    (item name, equip index); an item the inventory lacks is appended to it. Set 0 is active."""
    for s in inv_slots:
        s.pop("equipSet", None)
        s.pop("equipIndex", None)
    for k, st in enumerate(sets):
        used = set()
        for name, index in pick(st):
            slot = next((s for i, s in enumerate(inv_slots) if fold(s["name"]) == fold(name) and i not in used
                         and (s.get("equipSet") or [None] * len(sets))[k] is None), None)
            if slot is None:
                slot = {"name": name, "order": max((s.get("order", 0) for s in inv_slots), default=-1) + 1}
                inv_slots.append(slot)
            used.add(inv_slots.index(slot))
            slot.setdefault("equipSet", [None] * len(sets))[k] = index
            if k == 0:
                slot["equipIndex"] = index


def apply_gear(doc, gear):
    """`gear` = {"protectors": [{"name", "head", "body", "arms", "legs"}],
    "talismans": [{"name", "slots": [four names or null]}]}: each list becomes the build's sets of
    that kind (the first active), replacing the base build's. A piece named "-" or null is an empty
    slot."""
    if gear.get("protectors"):
        sets = gear["protectors"]
        for part in PROTECTOR_SLOTS:
            names = [st.get(part) for st in sets if st.get(part) not in (None, "-")]
            spelled = dict(zip(names, planner_spelling(names)))
            _equip_sets(doc["protectors"][part]["slots"], sets,
                        lambda st, part=part: [(spelled[st[part]], PROTECTOR_EQUIP_INDEX)]
                        if st.get(part) not in (None, "-") else [])
        doc["sets"]["protectors"] = [{"name": st["name"], **({"active": True} if i == 0 else {})}
                                     for i, st in enumerate(sets)]
    if gear.get("talismans"):
        sets = gear["talismans"]
        names = [n for st in sets for n in st["slots"] if n]
        spelled = dict(zip(names, planner_spelling(names)))
        _equip_sets(doc["talismans"]["slots"], sets,
                    lambda st: [(spelled[n], pos) for pos, n in enumerate(st["slots"]) if n])
        doc["sets"]["talismans"] = [{"name": st["name"], **({"active": True} if i == 0 else {})}
                                    for i, st in enumerate(sets)]
    for kind in ("protectors", "talismans"):
        for st in doc["sets"][kind]:
            print(f"{kind} set: {st['name']}")
    apply_kit(doc, gear)


def apply_kit(doc, gear):
    """The rest of `gear`, each key replacing the base build's own when present:
    "great_rune" (a name, or null for none), "crystal_tears" (two names), "tools" (a list of
    {"name", "equipIndex"}: the quick items and the pouch, flasks included), "flasks"
    ({"crimson", "cerulean"} of the base's flask total), "spells" (names in slot order) and "tags"."""
    if "great_rune" in gear:
        rune = gear["great_rune"]
        doc["greatRune"] = planner_spelling([rune])[0] if rune else None
        doc.pop("computed", None)
        print("great rune:", doc["greatRune"])
    items = doc.setdefault("items", {})
    if gear.get("crystal_tears") is not None:
        items["crystalTears"] = planner_spelling(list(gear["crystal_tears"]))
        print("crystal tears:", items["crystalTears"])
    if gear.get("tools") is not None:
        names = planner_spelling([t["name"] for t in gear["tools"]])
        items["tools"] = {**(items.get("tools") or {}), "slots": [
            {"name": n, "order": i, **({"equipIndex": t["equipIndex"]} if t.get("equipIndex") is not None else {})}
            for i, (n, t) in enumerate(zip(names, gear["tools"]))]}
        print("tools:", [s["name"] for s in items["tools"]["slots"]])
    if gear.get("flasks"):
        items["flasks"] = {**(items.get("flasks") or {}), **gear["flasks"]}
        print("flasks:", items["flasks"])
    if gear.get("spells") is not None:
        doc["spells"] = {**(doc.get("spells") or {}),
                         "slots": [{"name": n, "order": i} for i, n in enumerate(planner_spelling(list(gear["spells"])))]}
        print("spells:", [s["name"] for s in doc["spells"]["slots"]])
    # "derive" is read from the finished document instead (`publish`), once the stats are set.
    if gear.get("tags") not in (None, "derive"):
        doc["tags"] = list(gear["tags"])
        print("tags:", doc["tags"])


def publish(a, slots, set_names, ammo=None):
    for s, spelled in zip(slots, planner_spelling([s["name"] for s in slots])):
        s["name"] = spelled
    if ammo:
        ammo = dict(zip(ammo, planner_spelling(list(ammo.values()))))
    doc = request(f"{API}/{a.base}")
    for key in SERVER_FIELDS:
        doc.pop(key, None)
    doc["inventory"] = {"slots": slots, "sorting": {"method": "acquisition", "direction": "asc"}}
    if ammo:
        # The planner keeps ammunition outside the weapon inventory: four equipped names,
        # `items.ammo` {arrow1, arrow2, bolt1, bolt2} (read off a stored build).
        items = doc.setdefault("items", {})
        items["ammo"] = {**(items.get("ammo") or {}), **ammo}
        print("ammo:", items["ammo"])
    doc["sets"]["weapons"] = [{"name": n, **({"active": True} if i == 0 else {})} for i, n in enumerate(set_names)]
    if a.gear:
        apply_gear(doc, json.loads(a.gear.read_text()))
    if a.stats:
        doc["stats"].update({k: int(v) for k, v in (p.split("=") for p in a.stats.split(","))})
        doc.pop("computed", None)
    if a.character_class:
        doc["characterClass"] = a.character_class
        doc.pop("computed", None)
    if a.name:
        doc["name"] = a.name
    doc["description"] = a.description
    if a.gear and json.loads(a.gear.read_text()).get("tags") == "derive":
        spec = importlib.util.spec_from_file_location("er_mechanics_build_tags", HERE / "er-mechanics-build-tags.py")
        tags = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tags)
        doc["tags"] = tags.tags(doc)
        print("tags:", doc["tags"])
    if a.dry_run:
        return 0
    session = json.loads(SESSION.read_text(encoding="utf-8"))
    stored = request(API, {"id": a.update, "data": doc, "gameId": 1, "version": doc.get("version")},
        {"Authorization": f"Basic {session['session']}", "User": session["user"]})
    new_id = stored.get("id")
    if not new_id:
        print(f"upload refused: {stored}", file=sys.stderr)
        return 1
    back = request(f"{API}/{new_id}")
    got = [(s["name"], s.get("weaponArt"), s.get("infusion")) for s in back["inventory"]["slots"]]
    sent = [(s["name"], s["weaponArt"], s["infusion"]) for s in slots]
    print(f"{back['name']}  {PLANNER}{new_id}")
    print("read back matches" if got == sent else f"read back differs: {got}")
    print("sets read back:", [s["name"] for s in back["sets"]["weapons"]])
    if a.gear:
        for k, st in enumerate(back["sets"]["protectors"]):
            worn = [s["name"] for part in PROTECTOR_SLOTS for s in back["protectors"][part]["slots"]
                    if (s.get("equipSet") or [])[k:k + 1] == [PROTECTOR_EQUIP_INDEX]]
            print(f"  armor {st['name']}: {worn}")
        for k, st in enumerate(back["sets"]["talismans"]):
            worn = sorted((s["equipSet"][k], s["name"]) for s in back["talismans"]["slots"]
                          if len(s.get("equipSet") or []) > k and s["equipSet"][k] is not None)
            print(f"  talismans {st['name']}: {worn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
