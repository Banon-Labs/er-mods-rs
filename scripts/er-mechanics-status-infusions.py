#!/usr/bin/env python3
"""The stat spread that gets the most out of a Blood, Cold or Poison infusion: every melee weapon
that takes one, every starting class, one- and two-handed, at one rune level, scored on the R1
string's damage plus its status per second (`er-mechanics-string-score`).

    python3 scripts/er-mechanics-status-infusions.py --out data/status-infusions-rl150.csv
    python3 scripts/er-mechanics-status-infusions.py --rl 125 --vig 50 --end 35 --out <csv>

Per (weapon, infusion, grip, class) the build is made as `er-mechanics-class-by-weapon.py` makes
it, with the infusion fixed. Arcane raises bleed and poison build-up through the arcane
multiplier and is searched outside the greedy walk; frost never scales
(`docs/er-mechanics/status.md`), so a Cold build is spent on damage alone. Status is valued as the
weapon embedding values it (`ash-table.Table.status_hp`: build-up spread linearly over the
target's gauge, a DoT cut to one engagement); frost's damage-taken increase after a proc is not in
it.

One CSV row per (weapon, infusion, grip, class): the run's parameters, `score` (per second),
`damage` and `status_hp` (the whole string; `status_hp` is the infusion's own status), `frames`,
the class's `rank` on that weapon, infusion and grip (equal scores share a rank), `best` (rank 1),
the final stats and `unspent`. The summary printed at the end is per infusion over the rank-1 rows.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EPS = 1e-6


def _load(name: str, fname: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HERE / fname)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


CW = _load("er_mechanics_class_by_weapon", "er-mechanics-class-by-weapon.py")
SS, OPT = CW.SS, CW.OPT
CLASSES, STATS = CW.CLASSES, CW.STATS
#: Infusion -> the status its rows carry.
INFUSIONS = {"Blood": "bleed", "Cold": "frost", "Poison": "poison"}
COLUMNS = ("rl", "vig_floor", "end_floor", "weapon_id", "weapon", "infusion", "status", "grip", "class",
           "score", "damage", "status_hp", "frames", "rank", "best") + STATS + ("unspent",)
_W: dict = {}


def _job(arg):
    base_id, affinity, grip = arg
    e = SS.embed()
    try:
        sc = SS.StringScorer(e, base_id, affinity, grip)
    except (KeyError, ValueError, SystemExit):
        return base_id, affinity, grip, {}
    if not sc.links:
        return base_id, affinity, grip, {}
    out = {}
    for cls in CLASSES:
        r = SS.build(sc, cls, _W["rl"], _W["vig"], _W["end"])
        if r:
            out[cls] = r
    return base_id, affinity, grip, out


def summary(rows: list[dict]) -> None:
    for aff, status in INFUSIONS.items():
        seen, uniq = set(), []
        for r in rows:                      # one rank-1 row per weapon and grip
            if r["infusion"] == aff and r["best"] and (r["weapon_id"], r["grip"]) not in seen:
                seen.add((r["weapon_id"], r["grip"]))
                uniq.append(r)
        if not uniq:
            continue
        med = {k: statistics.median(r[k] for r in uniq) for k in ("str", "dex", "int", "fth", "arc")}
        arc_q = statistics.quantiles([r["arc"] for r in uniq], n=4)
        share = statistics.median(r["status_hp"] / (r["damage"] + r["status_hp"]) for r in uniq
                                  if r["damage"] + r["status_hp"])
        print(f"{aff} ({status}): {len(uniq)} weapon-grips; median best spread "
              + " ".join(f"{k.upper()} {v:g}" for k, v in med.items())
              + f"; ARC quartiles {arc_q}; {status} is {share:.0%} of the median string's value")
        for r in sorted(uniq, key=lambda r: -r["score"])[:10]:
            print(f"  {r['score']:6.1f}/s  {r['weapon']} {r['grip']} ({r['class']}) STR {r['str']} DEX {r['dex']} "
                  f"INT {r['int']} FTH {r['fth']} ARC {r['arc']}; string {r['damage']:.0f} + {r['status_hp']} {status}"
                  f" in {r['frames']:g} f")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--rl", type=int, default=150)
    ap.add_argument("--vig", type=int, default=56)
    ap.add_argument("--end", type=int, default=41)
    ap.add_argument("--jobs", type=int, default=6, help="throttled worker processes")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()

    _W.update(rl=a.rl, vig=a.vig, end=a.end)
    e = SS.embed()
    params = {"rl": a.rl, "vig_floor": a.vig, "end_floor": a.end}
    todo = [(i, aff, g) for i in CW.weapon_ids(e) for aff in OPT.affinities(e.art, i) if aff in INFUSIONS
            for g in ("1H", "2H")]
    rows = []
    with CW.throttled_pool(a.jobs) as pool:
        for n, (base_id, aff, grip, by_class) in enumerate(pool.imap_unordered(_job, todo), 1):
            if n % 25 == 0 or n == len(todo):
                print(f"{n}/{len(todo)}", flush=True)
            if not by_class:
                continue
            scores = sorted({v["score"] for v in by_class.values()}, reverse=True)
            for cls in CLASSES:
                v = by_class.get(cls)
                if v is None:
                    continue
                rank = 1 + sum(1 for s in scores if s > v["score"] + EPS)
                row = dict(params, weapon_id=base_id, weapon=e.art.names.get(base_id), infusion=aff,
                           status=INFUSIONS[aff], grip=grip, **{"class": cls}, score=v["score"],
                           damage=v["damage"], status_hp=v["status"].get(INFUSIONS[aff], 0),
                           frames=v["frames"], rank=rank, best=int(rank == 1), unspent=v["unspent"])
                row.update({k: v["stats"][k] for k in STATS})
                rows.append(row)
    order = {c: n for n, c in enumerate(CLASSES)}
    rows.sort(key=lambda r: (r["weapon_id"], r["infusion"], r["grip"], order[r["class"]]))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with a.out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} rows -> {a.out}")
    summary(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
