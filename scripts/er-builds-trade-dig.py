"""Read-only impact estimate on an existing RL 150 ranking JSON: damage-weighted trades and a
fixed attacker armor poise, re-running the neutral contest per pool build.

Not part of the ranking. Usage: python3 scripts/er-builds-trade-dig.py <ranking.json> <out.json>

The trade price: per pool build k, `hp = (win + trade) x D_me - (loss + trade) x D_k`, divided by
the pair's mean hit `(D_me + D_k) / 2`, so it equals `win - loss` when the two hits are equal.
`D_k` is the ranking's own R1 #1 `dmg` for that pool weapon and grip (the source
`er-mechanics-ashes.opponents_from_results` reads), else 388 (`OPPONENT_FALLBACK`).
"""
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np

S = Path(__file__).resolve().parent


def mod(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


EXCH = mod("er-mechanics-exchange")
NEUT = mod("er-mechanics-neutral")
ATK = EXCH.ATK
W = EXCH.EXCHANGE_WEIGHT
FALLBACK = 388.0
POISE_GRID = (0, 40, 60, 80, 100, 120, 140, 160)


def wa(windows, frame):
    for s_, e_, b_, m_ in windows:
        if s_ <= frame < e_:
            return b_, m_
    return 0.0, 1.0


def main():
    d = json.load(open(sys.argv[1]))
    res = d["results"]
    reg = ATK.Regulation(None)
    raw = EXCH.opponent_pool(reg)
    pool = EXCH.Pool(raw)
    npool = NEUT.NeutralPool(pool, NEUT.pool_reaches(raw))
    ids = EXCH.weapon_ids()

    def pkey(k):
        n, g = k.rsplit("|", 1)
        return f"{EXCH.plain_name(n)}|{g}"

    by = {}
    for r in res:
        s = (r.get("slots") or {}).get("r1_1") or {}
        if s.get("dmg"):
            by[f"{EXCH.plain_name(r['weapon'])}|{'2h' if r['two'] else '1h'}"] = s["dmg"]
    D = np.array([by.get(pkey(k), FALLBACK) for k in pool.keys])
    Db = D[pool.build_prof]
    matched = float(np.mean([pkey(pool.keys[i]) in by for i in pool.build_prof]))

    def neutral_arrays(strike, reach, poise, hyper, active, tools, my_poise=None):
        p = pool
        mp = p.my_poise if my_poise is None else np.sort(np.asarray(my_poise, float))
        reach = float(reach)
        dist = np.maximum(npool.reach, reach)
        my_start, my_ifr = NEUT.arrival(dist - reach, tools, npool.k)
        their_start, their_ifr = NEUT.arrival(dist - npool.reach, npool.tools, npool.k)
        mine = my_start + strike
        theirs = their_start + p.startup
        they_miss = (my_ifr > 0) & (theirs >= 0) & (theirs + npool.active <= my_ifr)
        i_miss = (their_ifr > 0) & (mine >= 0) & (mine + active <= their_ifr)
        first = ((mine < theirs) & ~i_miss) | (they_miss & ~i_miss)
        second = ((mine > theirs) & ~they_miss) | (i_miss & ~they_miss)
        their = [wa(h, t - s_) for h, t, s_ in zip(p.hyper, mine, their_start)]
        tb = np.array([b for b, _ in their])[p.build_prof]
        tm = np.array([m for _, m in their])[p.build_prof]
        breaks_them = poise * tm >= p.build_poise + tb
        my_w = [wa(hyper, t - s_) for t, s_ in zip(theirs, my_start)]
        dealt = p.poise_dealt * np.array([m for _, m in my_w])
        room = dealt - np.array([b for b, _ in my_w])
        p_break_me = np.searchsorted(mp, room, side="right") / len(mp)
        f_b, s_b = first[p.build_prof], second[p.build_prof]
        whiff_b, iwhiff_b = (they_miss & ~i_miss)[p.build_prof], (i_miss & ~they_miss)[p.build_prof]
        win = (f_b & (breaks_them | whiff_b)).astype(float)
        loss = np.where(s_b, np.where(iwhiff_b, 1.0, p_break_me[p.build_prof]), 0.0)
        return win, loss

    def exchange_arrays(strike, poise, hyper):
        p = pool
        first, second = p.startup > strike, p.startup < strike
        their = [wa(h, strike) for h in p.hyper]
        tb = np.array([b for b, _ in their])[p.build_prof]
        tm = np.array([m for _, m in their])[p.build_prof]
        breaks = poise * tm >= p.build_poise + tb
        mine = [wa(hyper, t) for t in p.startup]
        dealt = p.poise_dealt * np.array([m for _, m in mine])
        room = dealt - np.array([b for b, _ in mine])
        pbm = np.searchsorted(p.my_poise, room, side="right") / len(p.my_poise)
        f_b, s_b = first[p.build_prof], second[p.build_prof]
        return (f_b & breaks).astype(float), np.where(s_b, pbm[p.build_prof], 0.0)

    def priced(win, loss, dme):
        trade = 1.0 - win - loss
        hp = (win + trade) * dme - (loss + trade) * Db
        return float(np.mean(hp / ((dme + Db) / 2.0)))

    def slot_ratio(sc, s, f_new):
        react = s.get("react") or {}
        dmg = s["dmg"] + (sc.get("status_hp") or 0.0)
        fs = sc.get("f_sustain") or 1.0
        if react:
            share = react.get("react_share", 0.0)
            hw_new = (1.0 - share) * f_new + (sc["land"] - (1.0 - share))
            old = fs * (sc["hit_worth"] * dmg + sc["crit_hp"]) - sc["parry_hp"] - sc["whiff_hp"]
            new = fs * (hw_new * dmg + sc["crit_hp"]) - sc["parry_hp"] - sc["whiff_hp"]
            return new / old if old > 0 else 1.0
        return f_new / sc["f_contest"]

    def agg(scores):
        s = np.array([x for x in scores if x and x > 0])
        return float((s ** 2).sum() / s.sum()) if len(s) else 0.0

    out = {"matched_pool_share": matched, "D_pool_mean": float(Db.mean()),
           "D_pct": {q: float(np.percentile(Db, q)) for q in (10, 50, 90)},
           "corpus_poise_pct": {q: float(np.percentile(pool.my_poise, q)) for q in (10, 25, 50, 75, 90)},
           "rows": []}
    errs = []
    for r in res:
        slots, ms = r["slots"], r["moveset"]
        fam_old, fam_new, detail = [], [], {}
        for fname, f in (ms.get("families") or {}).items():
            fs = f.get("score")
            if not fs:
                continue
            fam_old.append(fs)
            op = f.get("opener")
            s = slots.get(op) or {}
            sc = s.get("score") or {}
            nt, ni = s.get("neutral"), s.get("neutral_in")
            if not (nt and ni and s.get("dmg") and sc):
                fam_new.append(fs)
                continue
            tools = tuple(t for t in nt.get("tools") or [] if t)
            win, loss = neutral_arrays(ni["strike"], ni["reach"], ni["poise"], ni["hyper"], ni["active"], tools)
            f_old = 1.0 + W * float(np.mean(win - loss))
            errs.append(abs(f_old - nt["f_neutral"]))
            net_p = priced(win, loss, s["dmg"])
            f_new = 1.0 + W * max(-1.0, min(1.0, net_p))
            used = abs((sc.get("f_contest") or 0) - nt["f_neutral"]) < 1e-9
            ratio = slot_ratio(sc, s, f_new) if used else 1.0
            fam_new.append(fs * ratio)
            det = {"opener": op, "trade": float(np.mean(1 - win - loss)), "net": float(np.mean(win - loss)),
                   "net_priced": net_p, "f_old": f_old, "f_new": f_new, "ratio": ratio, "dmg": s["dmg"],
                   "contest_is_neutral": used}
            ex = s.get("exchange") or {}
            if ex.get("strike_frame") is not None:
                ew, el = exchange_arrays(ex["strike_frame"], ni["poise"], ni["hyper"])
                det.update(ex_trade=float(np.mean(1 - ew - el)), ex_net=float(np.mean(ew - el)),
                           ex_net_priced=priced(ew, el, s["dmg"]))
            if fname == "r1" or op == ms.get("best_opener"):
                curve = {}
                for P in POISE_GRID:
                    w_, l_ = neutral_arrays(ni["strike"], ni["reach"], ni["poise"], ni["hyper"], ni["active"],
                                            tools, my_poise=[P])
                    curve[P] = 1.0 + W * float(np.mean(w_ - l_))
                det["poise_curve"] = curve
            detail[fname] = det
        extra = ms["score"] - (ms.get("base_score") or ms["score"])
        name = EXCH.plain_name(r["weapon"])
        out["rows"].append({"weapon": r["weapon"], "two": r["two"], "score_old": ms["score"],
                            "base_recomputed": agg(fam_old), "base_stored": ms.get("base_score"),
                            "score_new": agg(fam_new) + extra, "families": detail,
                            "wepType": reg.weapon[ids[name]]["wepType"] if name in ids else None})
    out["check_f_neutral_maxerr"] = max(errs) if errs else None
    json.dump(out, open(sys.argv[2], "w"))
    print("done", len(out["rows"]), "maxerr", out["check_f_neutral_maxerr"], "matched", matched)


if __name__ == "__main__":
    main()
