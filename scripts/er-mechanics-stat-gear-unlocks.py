#!/usr/bin/env python3
"""Weapons a fixed stat block cannot wield on its own but could with stat-raising gear.

    python3 scripts/er-mechanics-stat-gear-unlocks.py --stats str=18,dex=80,int=7,fth=8,arc=9 \
        --exclude-build b495a31a5249be

Gear is every named talisman and armor piece whose unconditional SpEffects (`refId` and
`residentSpEffectId*`) raise STR, DEX, INT, FTH or ARC. A kit is at most one armor piece per slot,
two armor pieces in all, and up to two talismans of different `accessoryGroup`s; boosted stats count
toward requirements and scaling, as in game. For each melee weapon the planner names (shields
excepted), one- and two-handed and every affinity, the smallest kit that meets every requirement is
found and the weapon's AR with it computed; the best result per weapon is printed, highest AR first,
with the build's own best AR (no kit) for reference. Weapons in `--exclude-build` (a planner build id)
are only used for that reference.
"""
from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("er_builds_optimize", HERE / "er-builds-optimize.py")
OPT = sys.modules.get("er_builds_optimize") or importlib.util.module_from_spec(spec)
if "er_builds_optimize" not in sys.modules:
    sys.modules["er_builds_optimize"] = OPT
    spec.loader.exec_module(OPT)
AR = OPT.AR
EPR = AR.EPR
API = "https://er-inventory-api.nyasu.business/inventories"
UA = {"User-Agent": "er-mods-rs build-import (+github.com/Banon-Labs)"}
STAT_F = {"str": "addStrengthStatus", "dex": "addDexterityStatus", "int": "addMagicStatus",
          "fth": "addFaithStatus", "arc": "addLuckStatus"}
SHIELD_WEP_TYPES = {65, 67, 69}
#: `EquipParamProtector.protectorCategory` of a helmet (0 head, 1 body, 2 arms, 3 legs).
HEAD_CATEGORY = 0


def gear(files, talisman_raises, armor_raises, head_only):
    sp = {r["id"]: r for r in EPR.rows(EPR.param_bytes(files, "SpEffectParam"), list(STAT_F.values()))[0]}

    def boost(ids):
        out = dict.fromkeys(STAT_F, 0)
        for i in ids:
            r = sp.get(i)
            for k, f in STAT_F.items():
                out[k] += int((r or {}).get(f, 0) or 0)
        return out

    tal = {}
    names = EPR.row_names("EquipParamAccessory")
    for a in EPR.rows(EPR.param_bytes(files, "EquipParamAccessory"))[0]:
        nm = names.get(a["id"])
        b = boost([i for i in [a["refId"]] + [a[f"residentSpEffectId{i}"] for i in range(1, 5)] if i > 0])
        if nm and any(b[k] for k in talisman_raises):
            key = a["accessoryGroup"] if a["accessoryGroup"] >= 0 else -a["id"]
            if key not in tal or sum(b.values()) > sum(tal[key][1].values()):
                tal[key] = (nm, b)
    armor = {}
    names = EPR.row_names("EquipParamProtector")
    for r in EPR.rows(EPR.param_bytes(files, "EquipParamProtector"), None, strict=False)[0]:
        nm = names.get(r["id"])
        ids = [r[f] for f in ("residentSpEffectId", "residentSpEffectId2", "residentSpEffectId3") if r[f] > 0]
        b = boost(ids)
        if head_only and r["protectorCategory"] != HEAD_CATEGORY:
            continue
        if nm and any(b[k] for k in armor_raises):
            armor.setdefault((r["protectorCategory"], tuple(sorted(b.items()))), (nm, b, r["protectorCategory"]))
    return list(tal.values()), list(armor.values())


def kits(talismans, armor, max_talismans=2):
    by_cat = {}
    for a in armor:
        by_cat.setdefault(a[2], []).append(a)
    out = []
    for arm in itertools.product(*([None] + v for v in by_cat.values())):
        arm = [a for a in arm if a]
        if len(arm) > 2:
            continue
        for n in range(max_talismans + 1):
            for tl in itertools.combinations(talismans, n):
                b = dict.fromkeys(STAT_F, 0)
                for part in arm + list(tl):
                    for k in b:
                        b[k] += part[1][k]
                out.append((len(arm) + n, [p[0] for p in arm + list(tl)], b))
    out.sort(key=lambda k: (k[0], -sum(k[2].values())))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--stats", required=True, help="str=18,dex=80,int=7,fth=8,arc=9")
    ap.add_argument("--exclude-build", help="planner build id whose weapons are left out")
    ap.add_argument("--talisman-raises", default="str,dex,int,fth,arc",
                    help="only talismans raising at least one of these stats, e.g. int,fth,arc")
    ap.add_argument("--armor-raises", default="str,dex,int,fth,arc",
                    help="only armor raising at least one of these stats, e.g. str")
    ap.add_argument("--head-only", action="store_true", help="helmets only, no other armor slot")
    ap.add_argument("--max-talismans", type=int, default=2)
    ap.add_argument("--exclude-gear", default="", help="comma-separated item names never used")
    a = ap.parse_args()
    base = {k: int(v) for k, v in (p.split("=") for p in a.stats.split(","))}
    t = AR.Tables(None)
    talismans, armor = gear(EPR.load(None), a.talisman_raises.split(","), a.armor_raises.split(","),
                            a.head_only)
    banned = {n.strip() for n in a.exclude_gear.split(",") if n.strip()}
    talismans = [g for g in talismans if g[0] not in banned]
    armor = [g for g in armor if g[0] not in banned]
    all_kits = kits(talismans, armor, a.max_talismans)
    catalog = json.loads((Path.home() / ".cache/er-build-planner/catalog.json").read_text())["armament"]
    have = set()
    if a.exclude_build:
        req = urllib.request.Request(f"{API}/{a.exclude_build}", headers=UA)
        have = {s["name"] for s in json.load(urllib.request.urlopen(req, timeout=20))["inventory"]["slots"]}
    real = OPT._real_weapon_ids()
    reference, rows = 0.0, []
    for base_id in sorted(i for i in t.weapons if i % 10000 == 0):
        name = t.names.get(base_id)
        if (OPT.sweep_kind(t, base_id, real) is None or t.weapons[base_id]["wepType"] in SHIELD_WEP_TYPES
                or not name or name.lower() not in catalog):
            continue
        best = None
        for two in (False, True):
            for aff in OPT.affinities(t, base_id):
                try:
                    lvl = AR.attack_rating(t, base_id, aff, 0, None, two)["max_level"]
                except (KeyError, ValueError, SystemExit):
                    continue
                need = OPT.requirements(t, base_id, aff, lvl, two)
                for n, items, b in all_kits:
                    st = {k: base[k] + b[k] for k in base}
                    if all(st[k] >= need[k] for k in base):
                        ar = AR.attack_rating(t, base_id, aff, lvl, st, two)["total"]
                        if best is None or ar > best[0] + 1e-6:
                            best = (ar, aff, two, n, items, need)
                        break
        if not best:
            continue
        if name in have:
            if best[3] == 0:
                reference = max(reference, best[0])
            continue
        if best[3] > 0:
            short = {k: best[5][k] for k in ("str", "dex", "int", "fth", "arc") if best[5][k] > base[k]}
            rows.append((round(best[0]), name, best[1], "2H" if best[2] else "1H", best[4], short))
    print("best AR already in the build, no gear:", round(reference))
    print("talismans:", [(n, {k: v for k, v in b.items() if v}) for n, b in talismans])
    print("armor:", [(n, {k: v for k, v in b.items() if v}) for n, b, _ in armor])
    for r in sorted(rows, reverse=True):
        print(json.dumps({"ar": r[0], "weapon": r[1], "affinity": r[2], "grip": r[3], "kit": r[4],
                          "requirements_over_base": r[5]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
