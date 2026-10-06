"""Read-only impact estimates for the unpriced punish windows and the armor-poise assumption.

Not part of the ranking. Usage:
    python3 scripts/er-builds-punish-dig.py <ranking.json> <setup-ranking.json> <out.json>

* stamina: the greedy burst's `lock_frames` against the pool's strike frames;
* links: how many moveset engagements go past depth 0 (where `MISS_ADVANTAGE` can act);
* recasts: each `--setup` left candidate's buff recasts, priced as a chaser's hit per recast;
* poise: the corpus armor poise by the wepType of the build's first right-hand weapon.
"""
import importlib.util
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

S = Path(__file__).resolve().parent


def mod(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


EXCH = mod("er-mechanics-exchange")
ATK = EXCH.ATK
#: The disengage module's chaser fallback (`CHASER_FALLBACK`): run R1 reach 5.98 m, hit 16.9, 410 HP.
CHASER = {"reach": 5.98, "hit": 16.9, "hp": 410.0}
SPRINT_MPS = 6.04
FIGHTS = [180.0 + 10.0 * i for i in range(13)]


def main():
    rank, setup, outp = sys.argv[1:4]
    reg = ATK.Regulation(None)
    raw = EXCH.opponent_pool(reg)
    pool = EXCH.Pool(raw)
    strikes = np.sort(pool.startup[pool.build_prof])
    out = {}

    d = json.load(open(rank))["results"]
    # stamina lock
    locks, rows = [], []
    for r in d:
        s = r["slots"].get("r1_1") or {}
        ex = s.get("exchange") or {}
        if ex.get("lock_frames") is None:
            continue
        L = ex["lock_frames"]
        p = float(np.searchsorted(strikes, L, side="right") / len(strikes))
        land = (s.get("score") or {}).get("land", 1.0)
        extra = (ex["burst"] - ex["burst_safe"]) * s["dmg"] * land
        locks.append(L)
        rows.append({"weapon": r["weapon"], "two": r["two"], "lock": L, "p_lock_punish": p,
                     "burst": ex["burst"], "burst_safe": ex["burst_safe"], "greedy_extra_hp": extra,
                     "punish_hp": p * 388.0})
    out["stamina"] = {"lock_pct": {q: float(np.percentile(locks, q)) for q in (10, 50, 90)},
                      "p_punish_pct": {q: float(np.percentile([x["p_lock_punish"] for x in rows], q))
                                       for q in (10, 50, 90)},
                      "greedy_beats_safe_after_punish": sum(x["greedy_extra_hp"] > x["punish_hp"] for x in rows),
                      "n": len(rows), "examples": [x for x in rows if x["weapon"] in
                                                   ("Giant-Crusher", "Greatsword", "Hand Axe", "Claymore",
                                                    "Lance") and x["two"]]}
    # links
    depth = defaultdict(int)
    for r in d:
        for f in (r["moveset"].get("families") or {}).values():
            depth[f.get("depth")] += 1
    out["links_depth"] = dict(depth)
    del d

    # recasts on the --setup ranking
    sd = json.load(open(setup))["results"]
    inv_f = float(np.mean([1.0 / f for f in FIGHTS]))
    rec_rows = []
    for r in sd:
        loop = (r["moveset"] or {}).get("paired_loop") or {}
        cands = loop.get("candidates") or {}
        if not cands:
            continue
        best_old = max(cands, key=lambda k: cands[k]["score"] or 0)
        new = {}
        for name, c in cands.items():
            R, tf, sc = c.get("recasts") or 0, c.get("time_factor") or 1.0, c.get("score") or 0.0
            if not R or not sc:
                new[name] = (sc, sc, None)
                continue
            cast_frames = (1.0 - tf) / R / inv_f * 30.0
            # A chaser at sprint lands before the cast ends unless the gap exceeds this.
            safe_m = CHASER["reach"] + max(0.0, cast_frames - CHASER["hit"]) * SPRINT_MPS / 30.0
            rate = sc / tf     # score before the recast time cost, read as damage per second
            # each punished recast costs the chaser's hit, in seconds of this build's output
            cost = R * CHASER["hp"] / rate * inv_f
            new[name] = (sc, sc * (tf - cost) / tf, {"recasts": R, "cast_frames": cast_frames,
                                                     "safe_m": safe_m, "tf": tf, "tf_punished": tf - cost})
        best_new = max(new, key=lambda k: new[k][1])
        rec_rows.append({"weapon": r["weapon"], "two": r["two"], "old_left": best_old,
                         "old_buff": cands[best_old].get("buff"), "new_left": best_new,
                         "new_buff": cands[best_new].get("buff"), "score_old": r["moveset"]["score"],
                         "loop_old": new[best_old][0], "loop_new": new[best_new][1],
                         "detail_old": new[best_old][2]})
    out["recast"] = {"rows": rec_rows, "n": len(rec_rows),
                     "left_changed": sum(x["old_left"] != x["new_left"] for x in rec_rows),
                     "old_buffs": dict(sorted(defaultdict(int, {}).items())),
                     }
    cnt_o, cnt_n = defaultdict(int), defaultdict(int)
    for x in rec_rows:
        cnt_o[x["old_buff"]] += 1
        cnt_n[x["new_buff"]] += 1
    out["recast"]["old_buffs"] = dict(cnt_o)
    out["recast"]["new_buffs"] = dict(cnt_n)

    # poise by class
    builds, _ = EXCH._corpus_builds(EXCH.CACHE / "builds.jsonl", 140, 160)
    ids = EXCH.weapon_ids()
    by = defaultdict(list)
    for b in builds:
        if b["poise"] is None:
            continue
        w = next((n for n in b["right"] if n in ids), None)
        t = reg.weapon[ids[w]]["wepType"] if w else None
        by[t].append(float(b["poise"]))
        by["all"].append(float(b["poise"]))
    out["poise_by_wepType"] = {str(k): {"n": len(v), "p25": float(np.percentile(v, 25)),
                                        "p50": float(np.median(v)), "p75": float(np.percentile(v, 75))}
                               for k, v in by.items() if len(v) >= 8}
    json.dump(out, open(outp, "w"), indent=1)
    print("done")


if __name__ == "__main__":
    main()
