#!/usr/bin/env python3
"""Choose a stat spread for a whole build rather than for one weapon: every weapon, affinity and
grip scored by its R1 string (`er-mechanics-string-score.py`) at every STR/DEX/ARC spread each
starting class can reach at the given level, then two numbers per spread:

    bleed    mean of the best 5 bleed options (one per weapon) -- how good the bleed core is
    breadth  per weapon class, the best option's score as a share of that class's best at any
             spread, averaged over classes -- how much of a real arsenal the spread can carry

The Pareto front of the two is printed, with the spread `--pick` selects: the most bleed whose
breadth stays within `--breadth-loss` of the widest spread's.

    python3 scripts/er-mechanics-build-spread.py --scores ~/.cache/er-build-planner/spread-rl150.npz
    python3 scripts/er-mechanics-build-spread.py --scores <npz> --analyse-only --breadth-loss 0.03

Spreads keep Vigor and Endurance at the floors, Mind, Intelligence and Faith at the class's base,
and spend the rest on STR, DEX and ARC in steps of `--step`. Scoring runs throttled.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import numpy as np

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
SS, OPT, RES = CW.SS, CW.OPT, CW.RES
WE = SS.WE
DMG = OPT.DAMAGE_STATS
SPEND = ("str", "dex", "arc")
TOP_BLEED = 5
_W: dict = {}


def spreads(rl: int, vig: int, end: int, step: int) -> tuple[list[tuple], dict]:
    """Every damage-stat tuple a class reaches at `rl` with the floors, and per tuple the classes
    that reach it (a class's full stats)."""
    model = RES.Model()
    by_tuple: dict = {}
    for cls in RES.CLASS_ROWS:
        _, base = model.class_base(cls)
        st = dict(base, vig=max(base["vig"], vig), vit=max(base["vit"], end))
        free = rl - (sum(st.values()) - OPT.LEVEL_OFFSET)
        if free < 0:
            continue
        for s in range(base["str"], min(OPT.STAT_CAP, base["str"] + free) + 1, step):
            for d in range(base["dex"], min(OPT.STAT_CAP, base["dex"] + free - (s - base["str"])) + 1, step):
                a = base["arc"] + free - (s - base["str"]) - (d - base["dex"])
                if a > OPT.STAT_CAP:
                    continue
                full = dict(st, str=s, dex=d, arc=a)
                by_tuple.setdefault(tuple(full[k] for k in DMG), []).append((cls, full))
    tuples = sorted(by_tuple)
    return tuples, by_tuple


def _job(arg):
    base_id, grip = arg
    e = SS.embed()
    tuples = _W["tuples"]
    T = np.array(tuples, dtype=np.int16)
    out = []
    for aff in OPT.affinities(e.art, base_id):
        try:
            sc = SS.StringScorer(e, base_id, aff, grip)
        except (KeyError, ValueError, SystemExit):
            continue
        if not sc.links:
            continue
        need = OPT.requirements(e.art, base_id, aff, sc.level, sc.two)
        ok = np.all(T >= np.array([need.get(k, 0) for k in DMG], dtype=np.int16), axis=1)
        score = np.full(len(tuples), np.nan, dtype=np.float32)
        bleed = np.zeros(len(tuples), dtype=np.float32)
        for i in np.flatnonzero(ok):
            p = sc.parts(dict(zip(DMG, tuples[i])))
            score[i] = p["score"]
            bleed[i] = p["status"].get("bleed", 0.0)
        if ok.any():
            out.append((aff, score, bleed))
    return base_id, grip, out


def score_all(rl, vig, end, step, jobs, path: Path) -> None:
    tuples, _ = spreads(rl, vig, end, step)
    _W["tuples"] = tuples
    e = SS.embed()
    todo = [(i, g) for i in CW.weapon_ids(e) for g in ("1H", "2H")]
    print(f"{len(tuples)} spreads x {len(todo)} weapon-grips", flush=True)
    meta, scores, bleeds = [], [], []
    with CW.throttled_pool(jobs) as pool:
        for n, (base_id, grip, rows) in enumerate(pool.imap_unordered(_job, todo), 1):
            if n % 25 == 0 or n == len(todo):
                print(f"{n}/{len(todo)}", flush=True)
            for aff, s, b in rows:
                meta.append((base_id, grip, aff))
                scores.append(s)
                bleeds.append(b)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, tuples=np.array(tuples, dtype=np.int16), score=np.array(scores),
                        bleed=np.array(bleeds), base_id=np.array([m[0] for m in meta]),
                        grip=np.array([m[1] for m in meta]), affinity=np.array([m[2] for m in meta]),
                        params=np.array([rl, vig, end, step]))


def analyse(path: Path, breadth_loss: float, show: int) -> int:
    z = np.load(path)
    tuples, score, bleed = z["tuples"], z["score"], z["bleed"]
    base_id, grip, aff = z["base_id"], z["grip"], z["affinity"]
    rl, vig, end, step = (int(x) for x in z["params"])
    e = SS.embed()
    names = e.art.names
    cat = np.array([WE.COMBO.CLASS.get(e.art.weapons[int(b)]["wepType"], "Other") for b in base_id])
    S = np.nan_to_num(score, nan=0.0)

    # breadth: each weapon class's best option per spread, over that class's best anywhere
    cats = sorted(set(cat))
    shares = []
    for c in cats:
        best = S[cat == c].max(axis=0)
        if best.max() > 0:
            shares.append(best / best.max())
    breadth = np.mean(shares, axis=0)

    # bleed: the best option per weapon among those that build bleed, then the top few
    is_bleed = (bleed > 0)
    B = np.where(is_bleed, S, 0.0)
    weapons = sorted(set(base_id.tolist()))
    per_weapon = np.stack([B[base_id == w].max(axis=0) for w in weapons])
    top = -np.sort(-per_weapon, axis=0)[:TOP_BLEED]
    bleed_core = top.mean(axis=0)

    # Pareto front over (bleed_core, breadth)
    order = np.argsort(-bleed_core)
    front, widest = [], -1.0
    for i in order:
        if breadth[i] > widest + 1e-9:
            front.append(i)
            widest = breadth[i]
    _, by_tuple = spreads(rl, vig, end, step)

    def describe(i):
        t = tuple(int(x) for x in tuples[i])
        classes = by_tuple[t]
        cls, full = max(classes, key=lambda cf: cf[1]["mnd"])
        col = per_weapon[:, i]
        best = [weapons[j] for j in np.argsort(-col)[:3]]
        return (f"bleed {bleed_core[i]:6.1f}  breadth {breadth[i]:.3f}  {cls:12s} "
                + " ".join(f"{k.upper()} {full[k]}" for k in ("vig", "mnd", "vit", "str", "dex", "int", "fth", "arc"))
                + "  | " + ", ".join(names[w] for w in best))

    print(f"RL {rl}, VIG {vig}, END {end}: {len(tuples)} spreads, {len(score)} options, "
          f"{len(cats)} weapon classes; Pareto front (most bleed first):")
    for i in front[:show]:
        print("  " + describe(i))
    floor = breadth.max() * (1 - breadth_loss)
    pick = max((i for i in range(len(tuples)) if breadth[i] >= floor), key=lambda i: bleed_core[i])
    print(f"\npick (most bleed with breadth >= {floor:.3f}, {breadth_loss:.0%} under the widest):")
    print("  " + describe(pick))
    t = tuples[pick]
    print("\nper weapon class at the pick (best option, share of the class's best anywhere):")
    for c, sh in sorted(zip(cats, [s[pick] for s in shares]), key=lambda x: -x[1]):
        m = np.flatnonzero(cat == c)
        j = m[np.argmax(S[m, pick])]
        print(f"  {c:22s} {sh:5.2f}  {names[int(base_id[j])]} {aff[j]} {grip[j]} {S[j, pick]:.1f}/s")
    print("\nbleed options at the pick:")
    for k in np.argsort(-B[:, pick])[:10]:
        print(f"  {S[k, pick]:6.1f}/s  {names[int(base_id[k])]} {aff[k]} {grip[k]}  (bleed {bleed[k, pick]:.0f} HP)")
    print("\npick stats:", dict(zip(DMG, (int(x) for x in t))))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--scores", type=Path, required=True, help="npz of every option's score per spread")
    ap.add_argument("--analyse-only", action="store_true")
    ap.add_argument("--rl", type=int, default=150)
    ap.add_argument("--vig", type=int, default=56)
    ap.add_argument("--end", type=int, default=41)
    ap.add_argument("--step", type=int, default=2)
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--breadth-loss", type=float, default=0.03)
    ap.add_argument("--show", type=int, default=25)
    a = ap.parse_args()
    if not a.analyse_only:
        score_all(a.rl, a.vig, a.end, a.step, a.jobs, a.scores)
    return analyse(a.scores, a.breadth_loss, a.show)


if __name__ == "__main__":
    sys.exit(main())
