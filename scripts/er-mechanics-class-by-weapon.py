#!/usr/bin/env python3
"""Every starting class's best stat spread and R1 string damage per second on every melee weapon,
one- and two-handed, at one rune level, written as one CSV row per (weapon, grip, class).

    python3 scripts/er-mechanics-class-by-weapon.py --out data/class-by-weapon-rl150.csv
    python3 scripts/er-mechanics-class-by-weapon.py --rl 125 --vig 50 --end 35 --out <csv>

Per row: the class's base stats raised to the Vigor and Endurance floors and every requirement of
the weapon (two-handing's STR x1.5 included), the rest spent on the damage stats by
`er-builds-optimize.spend` (arcane searched outside it when the weapon's status scales with it),
every affinity tried. The score is `er-mechanics-string-score`: the R1 string's PvP damage plus
its status HP, per second of the string. DLC grease is not counted.

Columns: the run's parameters, the weapon and grip, the class, its best affinity, `score`
(per second), `damage` and `status_hp` (the whole string), `frames` (string length), `rank`
among the classes on that weapon and grip (1 is best; equal scores share a rank), `behind_best`,
`sole_best` (rank 1 with no other class at rank 1), the final stats and `unspent` (points nothing
wanted, which go to Mind). Rows are sorted by weapon id, grip, then class order.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import multiprocessing as mp
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


SS = _load("er_mechanics_string_score", "er-mechanics-string-score.py")
SD = _load("er_mechanics_strdex_rl150", "er-mechanics-strdex-rl150.py")
OPT, RES = SS.OPT, SS.RES
CLASSES = list(RES.CLASS_ROWS)
STATS = ("vig", "mnd", "end", "str", "dex", "int", "fth", "arc")
COLUMNS = ("rl", "vig_floor", "end_floor", "weapon_id", "weapon", "grip", "class", "affinity", "score",
           "damage", "status_hp", "frames", "rank", "behind_best", "sole_best") + STATS + ("unspent",)
_W: dict = {}


def weapon_ids(e) -> list[int]:
    real = OPT._real_weapon_ids()
    return [i for i in sorted(e.art.weapons) if i % 10000 == 0
            and OPT.sweep_kind(e.art, i, real) is not None and e.art.weapons[i]["wepType"] not in SD.SHIELD_WEP_TYPES]


def _job(arg):
    base_id, grip = arg
    e = SS.embed()
    out = {}
    for aff in OPT.affinities(e.art, base_id):
        try:
            sc = SS.StringScorer(e, base_id, aff, grip)
        except (KeyError, ValueError, SystemExit):
            continue
        if not sc.links:
            continue
        for cls in CLASSES:
            r = SS.build(sc, cls, _W["rl"], _W["vig"], _W["end"])
            if r and (cls not in out or r["score"] > out[cls]["score"]):
                out[cls] = dict(r, affinity=aff)
    return base_id, grip, out


def rows_for(base_id: int, grip: str, by_class: dict, params: dict, names: dict) -> list[dict]:
    scores = sorted({r["score"] for r in by_class.values()}, reverse=True)
    best = scores[0]
    top = [c for c, r in by_class.items() if r["score"] >= best - EPS]
    out = []
    for cls in CLASSES:
        r = by_class.get(cls)
        if r is None:
            continue
        row = dict(params, weapon_id=base_id, weapon=names.get(base_id), grip=grip, **{"class": cls},
                   affinity=r["affinity"], score=r["score"], damage=r["damage"],
                   status_hp=sum(r["status"].values()), frames=r["frames"],
                   rank=1 + sum(1 for s in scores if s > r["score"] + EPS),
                   behind_best=round(best - r["score"], 1), sole_best=int(top == [cls]), unspent=r["unspent"])
        row.update({k: r["stats"][k] for k in STATS})
        out.append(row)
    return out


def throttled_pool(jobs: int):
    """A fork pool under the interrupt sweep's throttle (`er-mechanics-ash-interrupt.throttle`):
    this process on one core, each worker pinned to its own, `SCHED_IDLE` and capped per core."""
    AI = _load("er_mechanics_ash_interrupt", "er-mechanics-ash-interrupt.py")
    ctx = mp.get_context("fork")
    cpus = AI.allowed_cpus()
    AI.throttle(cpus[:1])
    workers = cpus[1:] or cpus
    n = max(1, min(jobs, len(workers)))
    print(f"throttled: {n} workers on CPUs {workers[:n]}, {AI.THROTTLE_SHARE:.0%} each", flush=True)
    return ctx.Pool(n, initializer=AI._worker_init, initargs=(workers, ctx.Value("i", 0)))


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
    todo = [(i, g) for i in weapon_ids(e) for g in ("1H", "2H")]
    rows = []
    with throttled_pool(a.jobs) as pool:
        for n, (base_id, grip, by_class) in enumerate(pool.imap_unordered(_job, todo), 1):
            if n % 25 == 0 or n == len(todo):
                print(f"{n}/{len(todo)}", flush=True)
            if by_class:
                rows.extend(rows_for(base_id, grip, by_class, params, e.art.names))
    order = {c: n for n, c in enumerate(CLASSES)}
    rows.sort(key=lambda r: (r["weapon_id"], r["grip"], order[r["class"]]))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with a.out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    sole = {c: len({r["weapon_id"] for r in rows if r["class"] == c and r["sole_best"]}) for c in CLASSES}
    print(f"{len(rows)} rows -> {a.out}")
    print("weapons where the class is sole best (either grip): "
          + ", ".join(f"{c} {n}" for c, n in sorted(sole.items(), key=lambda kv: -kv[1])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
