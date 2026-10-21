#!/usr/bin/env python3
"""Landed share of a skill's damage per carrier: `er-mechanics-ashes.skill_landing` (section 15a
of `docs/er-mechanics/ashes-of-war.md`) over the corpus poises of the RL window, for every weapon
and grip an ash-table sweep lists the skill on. The ash table credits every hit of a cast; this is
the share that lands, which depends on the carrier wherever the skill swings the weapon itself.

Stormcaller is the case it was written for (section 15c): its three swings are capsules between
the weapon's own dummies 100-130, so their reach is the weapon model's length (0.71 m on a Dagger,
2.15 m on a Halberd), while its three bullets launch from body dummy 280 the same on every weapon.

    python3 scripts/er-mechanics-ash-landing.py SWEEP.json... --stats str=51,dex=17,int=7,fth=8,arc=41 \
        --skills Stormcaller --cache ~/.cache/er-build-planner/ash-landing.json

The cache is keyed `weapon|affinity|grip|skill|stats` and only missing keys are computed (throttled,
`er-mechanics-ash-interrupt.throttle`).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import multiprocessing as mp
import os
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = Path.home() / ".cache/er-build-planner/ash-landing.json"
DEFAULT_SKILLS = ("Stormcaller",)
_W: dict = {}


def _load(name: str, fname: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HERE / fname)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def key(weapon, affinity, grip, skill, stats):
    return f"{weapon}|{affinity}|{grip}|{skill}|" + ",".join(f"{k}={stats[k]}" for k in sorted(stats))


def _setup(rl, window, stats):
    A = _load("er_mechanics_ashes", "er-mechanics-ashes.py")
    D = _load("er_mechanics_defense", "er-mechanics-defense.py")
    OPT = _load("er_builds_optimize", "er-builds-optimize.py")
    rows = A.corpus_slots(os.path.join(A.CACHE, "builds.jsonl"), rl - window, rl + window, "pvp")
    # One set of attack-rating tables for every carrier: a `WeaponContext` given none reads the
    # params again, 3.2 s of a carrier's 3.8 s (measured 2026-10-20). Nothing writes to them.
    art = A._load("er_mechanics_ar", "er-mechanics-ar.py").Tables(None)
    _W.update(A=A, D=D, t=A.AshTables(), stats=stats, art=art,
              defender=OPT.bracket_defender([{"computed": r["computed"]} for r in rows]),
              poises=[p for p in ((r["computed"].get("poise") or {}).get("original") for r in rows) if p is not None])


def _job(job):
    weapon, affinity, grip, skill = job
    A, t = _W["A"], _W["t"]
    try:
        wid = t.find_weapon(weapon)
        ctx = A.WeaponContext(weapon, affinity, None, _W["stats"], grip == "2H", ar_tables=_W["art"])
        sid = t.find_arts(skill)
        hits = A.skill_hits(t, wid, sid, ctx, ctx.level)
        A.pvp_damage(t, wid, hits, _W["defender"], _W["D"])
        land = A.skill_landing(t, wid, sid, hits, _W["poises"])
    except Exception as exc:  # one weapon's failure is recorded, not fatal to the sweep
        return job, {"error": f"{type(exc).__name__}: {exc}"}
    share = land["dmg"] / land["dmg_all"] if land["dmg_all"] else None
    return job, {"dmg": round(land["dmg"], 1), "dmg_all": round(land["dmg_all"], 1),
                 "share": None if share is None else round(share, 4), "geometry": land["geometry"],
                 "by_distance": {str(d): v["hits"] for d, v in land["by_distance"].items()}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("sweeps", nargs="+", type=Path, help="er-mechanics-ash-table.py --json outputs")
    ap.add_argument("--stats", required=True, help="str=..,dex=..,int=..,fth=..,arc=..")
    ap.add_argument("--skills", default=",".join(DEFAULT_SKILLS))
    ap.add_argument("--cache", type=Path, default=CACHE)
    ap.add_argument("--rl", type=int, default=150)
    ap.add_argument("--window", type=int, default=10)
    ap.add_argument("--jobs", type=int, default=6)
    a = ap.parse_args()
    stats = {k: int(v) for k, v in (p.split("=") for p in a.stats.split(","))}
    skills = {s.strip() for s in a.skills.split(",")}
    cache = json.loads(a.cache.read_text()) if a.cache.exists() else {}
    # A row with no affinity is Standard, the game's own fallback, as `er-mechanics-combo-loadout.py`
    # reads it.
    todo = sorted({(x["weapon"], x.get("affinity") or "Standard", x["grip"], x["ash"])
                   for f in a.sweeps for x in json.loads(f.read_text()) if x.get("ash") in skills})
    todo = [j for j in todo if key(*j, stats) not in cache]
    print(f"{len(todo)} carriers to land ({len(cache)} cached)", flush=True)
    if todo:
        _setup(a.rl, a.window, stats)
        CW = _load("er_mechanics_class_by_weapon", "er-mechanics-class-by-weapon.py")
        with CW.throttled_pool(a.jobs) as pool:
            for n, (job, out) in enumerate(pool.imap_unordered(_job, todo), 1):
                cache[key(*job, stats)] = out
                if n % 20 == 0 or n == len(todo):
                    print(f"{n}/{len(todo)}", flush=True)
                    a.cache.parent.mkdir(parents=True, exist_ok=True)
                    a.cache.write_text(json.dumps(cache, indent=0, sort_keys=True))
    for skill in sorted(skills):
        mine = {k: v for k, v in cache.items() if k.split("|")[3] == skill and v.get("share") is not None
                and k.endswith("|" + key("", "", "", "", stats).split("|", 4)[4])}
        if not mine:
            continue
        med = statistics.median(v["share"] for v in mine.values())
        print(f"\n{skill}: landed share of the cast's damage, median {med:.2f} over {len(mine)} carriers")
        ranked = sorted(mine.items(), key=lambda kv: kv[1]["share"])
        for k, v in ranked[:8] + [("...", None)] + ranked[-8:]:
            if v is None:
                print("  ...")
                continue
            w, aff, grip = k.split("|")[:3]
            print(f"  {v['share']:.2f}  {aff} {w} {grip}  hits by distance {v['by_distance']}")
    return 0


if __name__ == "__main__":
    mp.set_start_method("fork", force=True)
    sys.exit(main())
