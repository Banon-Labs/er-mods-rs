"""A skill of mine vs a reacting RL140-160 pool attack B: how often B breaks my poise before my
second hit lands.

    python3 scripts/er-mechanics-reacted-interrupt.py --skill "Blood Tax" --weapon "Dueling Shield" --poise 88

Reuses `er-mechanics-interrupt` (`PoolRows`/`resolve`: the pool, its weights, B's hits with or
without saRate, B's windows and entry i-frames, the skill delay proxy), `er-mechanics-ashes`
(`skill_profile`, the reaction constants) and the poise rule of `er-mechanics-interrupt.simulate`.
No new damage or poise model. Every assumption beyond those modules is listed in `--help`.

Model (`INFERRED` where not inherited): I start the skill at frame 0 on my machine. B starts at
`30 x (reaction + 2 x NETWORK_ONE_WAY_S)` frames, reaction lognormal (median `--median`, log sd
`REACTION_LOG_SD`), sampled at `--points` quantiles. My hit 1 lands at its 2.5 m contact frame;
if it breaks them (their armor poise, raised by any window of B open at that moment) B's later
hits are cancelled; a hit of B on the same frame still lands (a trade). B breaks me when its
cumulative poise reaches mine on a frame before my hit 2's contact. Fresh poise on both sides.
If my hit lands before B is pressed and does not break them, they still press B on schedule.
"""
import argparse
import importlib.util
import math
import statistics
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent


def _sib(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


INTR = _sib("er-mechanics-interrupt")
AS = INTR._mod("er-mechanics-ashes")
EXCH, ATK = INTR.EXCH, INTR.ATK
CLASS = _sib("er-mechanics-combo-loadout").CLASS

_orig_resolve = INTR.resolve


def _resolve(*a, **k):
    """`INTR.resolve`, with each sequence tagged by its opener name."""
    out = _orig_resolve(*a, **k)
    for oname, s in out.items():
        s["oname"] = oname
    return out


INTR.resolve = _resolve


def my_skill(skill: str, weapon: str, grip: str, weapons: dict):
    ash = AS.AshTables()
    sa = EXCH.SaRates()
    aid = ash.find_arts(skill)
    wid = ash.find_weapon(weapon)
    prof = AS.skill_profile(ash, aid, wid, 0, grip)     # the opening moves with class and grip
    anim = AS.main_anim(prof)
    acts = prof["anims"][anim]
    mel = sorted((x["frames"][0], (x.get("poise") or 0.0) * INTR.POISE_MENU, sa(x["atk_row"]), x["atk_row"])
                 for x in acts if x["kind"] == "melee")
    ha = [x for x in acts if x["kind"] == "hyperarmor"]
    r1 = weapons[f"{EXCH.plain_name(prof['weapon'])}|{grip}"]["members"]["r1_1"]
    delay = r1["fc"] - r1["first"]
    print(f"{prof['name']} (aid {aid}) on {prof['weapon']} (wid {wid}), anim {anim}; "
          f"hyperarmor events {len(ha)}; contact delay proxy (1H R1 #1) {delay}")
    for f, p, r, row in mel:
        print(f"  hit f{f} -> contact f{f + delay}: poise {p:.0f} x saRate {r:.3f} = {p * r:.1f} (AtkParam {row})")
    return [(f + delay, p, r) for f, p, r, _ in mel]


def reaction_frames(median: float, points: int) -> np.ndarray:
    nd = statistics.NormalDist()
    return np.array([(median * math.exp(AS.REACTION_LOG_SD * nd.inv_cdf((k + 0.5) / points))
                      + 2 * AS.NETWORK_ONE_WAY_S) * AS.TAE_FPS for k in range(points)])


_ROWS = {}


def pool_rows(weapons: dict, sa_rate: bool):
    if sa_rate not in _ROWS:
        reg = ATK.Regulation(None)
        pool_raw = EXCH.opponent_pool(reg, INTR.CACHE / "builds.jsonl", 140, 160)
        _ROWS[sa_rate] = INTR.PoolRows(pool_raw, weapons, INTR.Delays(weapons), INTR.entry_table(), sa_rate)
    return _ROWS[sa_rate]


def run(weapons, my_hits, my_poise, sa_rate, median, points, use_hit1=True):
    rows = pool_rows(weapons, sa_rate)
    t1, t2 = my_hits[0][0], my_hits[1][0]
    p1 = my_hits[0][1] * (my_hits[0][2] if sa_rate else 1.0)
    offs = reaction_frames(median, points)
    tot, by_class, by_open = defaultdict(float), defaultdict(float), defaultdict(float)
    for r in range(len(rows.d_idx)):
        s = rows.seqs[rows.d_idx[r]]
        their_p, w = rows.d_poise[r], rows.d_w[r] / len(offs)
        win = [tuple(x) for x in s["windows"]][: INTR.MAX_WINDOWS]
        inv = s["invuln"][0] if s["invuln"] else None
        bh = s["hits"][: INTR.MAX_HITS]
        members = weapons[f"{s['weapon']}|{s['grip']}"]["members"]
        cls = CLASS.get(next(iter(members.values())).get("wep_type"), "?")
        if cls == "?":
            cls = s["weapon"]
        for o in offs:
            f_them = t1 - o                      # my hit 1 on B's clock
            cur, mult, blocked = their_p, 1.0, False
            if f_them >= 0:
                k = next((i for i, x in enumerate(win) if x[0] <= f_them < x[1]), None)
                if k is not None:
                    cur, mult = their_p + win[k][2], win[k][3]
                blocked = inv is not None and inv[0] <= f_them < inv[1]
            stop_at = t1 if use_hit1 and not blocked and cur - p1 * mult <= 1e-6 else None
            mine, landed, broken = my_poise, 0, False
            for ft, pd in bh:
                t = o + ft
                if t >= t2 or (stop_at is not None and t > stop_at):
                    break
                landed += 1
                mine -= pd
                if mine <= 1e-6:
                    broken = True
                    break
            if broken:
                tot["interrupted"] += w
                # On my clock: before my hit 1's contact I dealt nothing; at it is a trade; after it
                # hit 1 already landed.
                tot["broken_before_hit1" if t < t1 else "broken_at_hit1" if t == t1 else "broken_after_hit1"] += w
                tot.setdefault("break_frames", []).append((t, w))
                by_class[(cls, s["family"])] += w
                by_open[s["oname"]] += w
                if stop_at is not None:
                    tot["trade_at_hit1"] += w
            elif stop_at is not None:
                tot["hit1_stops_them"] += w
                if f_them < 0:
                    tot["hit1_before_B_pressed"] += w
            elif landed == 0:
                tot["nothing_before_hit2"] += w
            else:
                tot["landed_but_held"] += w
    return tot, by_class, by_open, offs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skill", default="Blood Tax")
    ap.add_argument("--weapon", default="Dueling Shield")
    ap.add_argument("--grip", default="one", choices=("one", "both"))
    ap.add_argument("--poise", type=float, nargs="+", default=[88.0])
    ap.add_argument("--medians", type=float, nargs="+", default=[0.20, 0.25, 0.35])
    ap.add_argument("--points", type=int, default=41)
    a = ap.parse_args()
    weapons = INTR.load_cache()
    my_hits = my_skill(a.skill, a.weapon, a.grip, weapons)
    for poise in a.poise:
        for sa_rate in (True, False):
            for med in a.medians:
                tot, bc, bo, offs = run(weapons, my_hits, poise, sa_rate, med, a.points)
                cf, _, _, _ = run(weapons, my_hits, poise, sa_rate, med, a.points, use_hit1=False)
                print(f"\npoise {poise:g} saRate={sa_rate} median {med}s: B starts f{np.median(offs):.1f} "
                      f"(p5 f{offs[int(0.05 * len(offs))]:.1f}, p95 f{offs[int(0.95 * len(offs))]:.1f})")
                for k in ("interrupted", "trade_at_hit1", "hit1_stops_them", "hit1_before_B_pressed",
                          "nothing_before_hit2", "landed_but_held"):
                    print(f"  {k:22s} {100 * tot[k]:5.1f}%")
                for k in ("broken_before_hit1", "broken_at_hit1", "broken_after_hit1"):
                    print(f"  {k:22s} {100 * tot[k]:5.1f}%")
                bf = sorted(tot.get("break_frames", []))
                if bf:
                    total, acc, qs = sum(w for _, w in bf), 0.0, {}
                    for t, w in bf:
                        acc += w
                        for q in (0.05, 0.25, 0.5, 0.75, 0.95):
                            if q not in qs and acc >= q * total:
                                qs[q] = t
                    print(f"  break frame on my clock (hit 1 at f{my_hits[0][0]:g}): "
                          + ", ".join(f"p{int(q * 100)} f{t:.1f}" for q, t in qs.items()))
                print(f"  interrupted if hit 1 never broke anyone: {100 * cf['interrupted']:.1f}%")
                print("  top (class, family):", ", ".join(
                    f"{c}/{fam} {100 * v:.1f}" for (c, fam), v in sorted(bc.items(), key=lambda kv: -kv[1])[:6]))
                print("  by opener:", ", ".join(
                    f"{k} {100 * v:.1f}" for k, v in sorted(bo.items(), key=lambda kv: -kv[1])))


if __name__ == "__main__":
    main()
