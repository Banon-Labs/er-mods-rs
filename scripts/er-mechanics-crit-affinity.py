#!/usr/bin/env python3
"""Riposte and backstab damage per affinity at one stat spread, with the status the crit's hits
build up, to choose the infusion of a crit weapon.

Crit damage is `er-mechanics-crits.crit_damage` (crits.md section 2): every element's attack x
the hit's motion value x (100 + throwAtkRate) / 100, against `crits.reference_defender`. The
multiplier covers the five damage types and not status build-up, so an affinity that moves
attack into status (Blood, Poison) loses crit damage in proportion and gets back only one hit's
worth of build-up per crit hit. That build-up is valued as `er-mechanics-string-score` values it:
the R1 string's status HP divided by its hit count, times the crit's hits.

    python3 scripts/er-mechanics-crit-affinity.py --stats str=51,dex=17,int=7,fth=8,arc=41
    python3 scripts/er-mechanics-crit-affinity.py --stats ... --weapon Misericorde --weapon Dagger
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name: str, fname: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HERE / fname)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


CW = _load("er_mechanics_class_by_weapon", "er-mechanics-class-by-weapon.py")
CR = _load("er_mechanics_crits", "er-mechanics-crits.py")
SS, OPT = CW.SS, CW.OPT
KINDS = ("riposte", "backstab")


def status_per_hit(e, base_id: int, affinity: str, grip: str, stats: dict) -> dict:
    """Status HP one hit of this weapon builds, from its R1 string."""
    sc = SS.StringScorer(e, base_id, affinity, grip)
    hits = sum(s["hits"] for s in sc.links)
    if not hits:
        return {}
    return {k: v / hits for k, v in sc.parts(stats)["status"].items()}


def rows_for(t, e, defender, base_id: int, stats: dict) -> list[dict]:
    out = []
    for aff in OPT.affinities(e.art, base_id):
        for grip in ("1H", "2H"):
            two = grip == "2H"
            try:
                wid = e.art.find_weapon(e.art.names[base_id], aff)
                level = e.art.max_level(e.art.weapons[wid]["reinforceTypeId"])
                need = OPT.requirements(e.art, base_id, aff, level, two)
                if any(stats[k] < need.get(k, 0) for k in OPT.DAMAGE_STATS):
                    continue
                per_hit = status_per_hit(e, base_id, aff, grip, stats)
            except (KeyError, ValueError, SystemExit):
                continue
            row = {"weapon": e.art.names[base_id], "affinity": aff, "grip": grip}
            for kind in KINDS:
                c = CR.crit_damage(t, base_id, kind, aff, stats=stats, two_handed=two, defender=defender)
                status = {k: v * len(c["hits"]) for k, v in per_hit.items()}
                row[kind] = {"damage": c["damage"], "status": status,
                             "total": c["damage"] + sum(status.values()), "mult": c["crit_multiplier"]}
            out.append(row)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--stats", required=True, help="str=..,dex=..,int=..,fth=..,arc=..")
    ap.add_argument("--weapon", action="append", default=[], help="only these (default: every weapon)")
    ap.add_argument("--top", type=int, default=20)
    a = ap.parse_args()
    stats = {k: int(v) for k, v in (p.split("=") for p in a.stats.split(","))}
    stats = {k: stats.get(k, 10) for k in OPT.DAMAGE_STATS}
    e = SS.embed()
    t = CR.load_tables()
    defender = CR.reference_defender(t)
    ids = CW.weapon_ids(e)
    if a.weapon:
        want = set(a.weapon)
        ids = [i for i in ids if e.art.names[i] in want]
    rows = [r for i in ids for r in rows_for(t, e, defender, i, stats)]
    for kind in KINDS:
        print(f"\n{kind}s at {stats}, damage + status HP of the crit's hits (top {a.top}):")
        for r in sorted(rows, key=lambda r: -r[kind]["total"])[:a.top]:
            k = r[kind]
            st = ", ".join(f"{s} {v:.0f}" for s, v in k["status"].items())
            print(f"  {k['total']:6.0f}  {r['weapon']} {r['affinity']} {r['grip']}  "
                  f"(damage {k['damage']:.0f} at x{k['mult']:.2f}{', ' + st if st else ''})")
    if a.weapon:
        print("\nevery affinity:")
        for r in sorted(rows, key=lambda r: (r["weapon"], r["grip"], -r["riposte"]["total"])):
            print(f"  {r['weapon']:22s} {r['grip']} {r['affinity']:9s} "
                  + "  ".join(f"{kind} {r[kind]['damage']:5.0f} + {sum(r[kind]['status'].values()):4.0f}"
                              f" = {r[kind]['total']:5.0f}" for kind in KINDS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
