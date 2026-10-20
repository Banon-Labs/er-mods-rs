#!/usr/bin/env python3
"""Report the main-hand feint and movement-R1 terms of `er-mechanics-class-roles.py` on a cached build.

    python3 scripts/er-mechanics-move-feint-report.py <prefix> [--weapon `NAME`]... [--jobs N]

`<prefix>` names a weapon-build run's files (`<prefix>embed.json`, `roles.json`,
`planner-build.json`), and `--exclude` defaults to the `ladder-exclude.json` beside them. Prints,
writing nothing but the string-simulation cache `ChainTrades` keeps:

- the feint survey (`FeintModel`): weapon grips with a held R2, the ones whose delay beats some
  reaction, the longest delay windows;
- the backstep R1 against the rolling R1 over every wieldable weapon grip (best affinity by hit
  damage), each traded after its entry clip (`er-mechanics-ash-interrupt.entry_step`): how often
  the backstep R1 delivers more;
- per class of each `--weapon` (and the top feint weapon): the main-hand score before (the cached
  roles' features and weights) and after (this tree's features and `pick_weights`), the parry,
  perfect-block, ash and true-combo features carried over from the cache.
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
#: The main-hand features before the feint and movement-R1 terms (2026-10-19).
OLD_FEATURES = ("r1_recovery", "r1_reach", "r1_startup", "ar", "r1_chain_dps", "ash_value", "stamina", "crit",
                "parry_cost", "pb_cost")
#: Features no part of this report recomputes: taken from the cached roles.
CARRIED = ("parry_cost", "pb_cost", "ash_value", "true_combo")


def _load(name):
    key = name.replace("-", "_")
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, HERE / f"{name}.py")
        m = importlib.util.module_from_spec(spec)
        sys.modules[key] = m
        spec.loader.exec_module(m)
    return sys.modules[key]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("prefix")
    ap.add_argument("--weapon", action="append", default=[])
    ap.add_argument("--exclude", type=Path)
    ap.add_argument("--jobs", type=int, default=8)
    a = ap.parse_args()
    CR = _load("er-mechanics-class-roles")
    p = a.prefix
    emb = json.loads(Path(p + "embed.json").read_text())
    roles = json.loads(Path(p + "roles.json").read_text())
    build = json.loads(Path(p + "planner-build.json").read_text())
    ex_path = a.exclude or Path(p).parent / "ladder-exclude.json"
    excl = json.loads(ex_path.read_text()) if ex_path.exists() else {}
    weights, old_w = CR.pick_weights(excl.get("ashes", ())), roles["weights"]
    bans = [tuple(r) for r in excl.get("rows", ())]

    def grip_ok(r):
        return not any(w == r["weapon"] and aff in (None, r["affinity"]) and g == r["grip"] for w, aff, g in bans)

    feint = CR.FeintModel.from_game()
    print(f"feint model: roll i-frames {feint.I:g}, reaction + network {[round(d, 1) for d, _ in feint.delays]} "
          f"frames, R2 share {feint.r2_share:.2f}, reacting share {feint.react_share:.2f}")
    best = {}
    for r in emb["weapons"]:
        if r["grip"] in CR.RIGHT_GRIPS and r["wieldable"] and grip_ok(r):
            k = (r["weapon"], r["grip"])
            if k not in best or CR.hit_damage(r) > CR.hit_damage(best[k]):
                best[k] = r
    fs = {k: feint(r["slots"]) for k, r in best.items()}
    held = [k for k, f in fs.items() if f]
    nz = sorted((k for k in held if fs[k]["r2_feint"] > 0), key=lambda k: -fs[k]["feint_hp"])
    print(f"\nweapon grips with a held R2: {len(held)} of {len(best)}; the delay beats some reaction on {len(nz)}")
    for k in nz[:12]:
        s = best[k]["slots"]["r2_1"]
        print(f"  {k[0]} {k[1]} ({best[k]['affinity']}): r2_feint {fs[k]['r2_feint']:.3f}, window {fs[k]['r2_delay']} f, "
              f"release to hit {s['startup']} f, feint_hp {fs[k]['feint_hp']}")
    for k in sorted(held, key=lambda k: -fs[k]["r2_delay"])[:5]:
        print(f"  long window: {k[0]} {k[1]} {fs[k]['r2_delay']} f, r2_feint {fs[k]['r2_feint']:.3f}, "
              f"release to hit {best[k]['slots']['r2_1']['startup']} f")

    trades = CR.chain_trades(build)
    print(f"\nstring trades: my poise {trades.poise:g}, pool {trades.opp.n_builds} builds", flush=True)
    targets = list(a.weapon) + ([nz[0][0]] if nz else [])
    classes = {roles["weapons"][w]["class"] for w in targets if w in roles["weapons"]}
    members = [w for w, v in roles["weapons"].items() if v["class"] in classes and v["wieldable"]]
    items = [(chain, r["weapon"], r["grip"], s.get("reach"))
             for r in best.values() for s, chain in CR.right_moves(r["slots"]).values()]
    items += CR.trade_items({"weapons": [r for r in emb["weapons"] if r["weapon"] in members]}, grip_ok)
    trades.prepare(items, a.jobs, log=lambda m: print(m, flush=True))

    rows = {}
    for k, r in best.items():
        if "roll_r1" not in r["slots"] or "bstep_r1" not in r["slots"]:
            continue
        mv = CR.right_moves(r["slots"])
        got = {}
        for fam in ("roll", "bstep"):
            s, chain = mv[fam]
            t = trades.trade(chain, r["weapon"], r["grip"], s.get("reach"))
            got[fam] = (CR._slot_hp(s), t["mult"] if t else None)
        if None not in (got["roll"][1], got["bstep"][1]):
            rows[k] = got
    n = len(rows)
    if n:
        hp = sum(1 for o in rows.values() if o["bstep"][0] * o["bstep"][1] > o["roll"][0] * o["roll"][1])
        mu = sum(1 for o in rows.values() if o["bstep"][1] > o["roll"][1])
        print(f"\nbackstep R1 against rolling R1, {n} weapon grips, each after its entry clip:")
        print(f"  delivered HP higher for the backstep R1 on {hp} ({hp / n:.1%}); trade multiplier higher on {mu} "
              f"({mu / n:.1%}); mean multiplier roll {sum(o['roll'][1] for o in rows.values()) / n:.3f}, "
              f"backstep {sum(o['bstep'][1] for o in rows.values()) / n:.3f}")
        for k in sorted(rows):
            if k[0] in targets:
                o = rows[k]
                print(f"  {k[0]} {k[1]}: roll R1 {o['roll'][0]:.0f} HP x {o['roll'][1]:.3f}, "
                      f"backstep R1 {o['bstep'][0]:.0f} HP x {o['bstep'][1]:.3f}")

    recs = collections.defaultdict(list)
    for r in emb["weapons"]:
        if r["weapon"] in members:
            recs[r["weapon"]].append(r)
    cover = CR.PunishCoverage(emb)
    for c in sorted(classes):
        new, old = {}, {}
        for w in (w for w in members if roles["weapons"][w]["class"] == c):
            for aff, e in roles["weapons"][w]["main_hand"]["by_affinity"].items():
                mine = [r for r in recs[w] if r["affinity"] == aff and r["grip"] in CR.RIGHT_GRIPS and r["wieldable"]
                        and grip_ok(r)]
                if not mine:
                    continue
                f = CR.main_hand_entry(mine, None, None, None, cover, trades, feint)["features"]
                f.update({k: e["features"][k] for k in CARRIED if k in e["features"]})
                new[(w, aff)], old[(w, aff)] = f, e["features"]
        bonus = {k: CR.TRUE_COMBO_BONUS if roles["weapons"][k[0]]["true_combos"] else 0.0 for k in new}
        sn = {k: CR.norm_score(f, list(new.values()), CR.FEATURES, weights) + bonus[k] for k, f in new.items()}
        so = {k: CR.norm_score(f, list(old.values()), OLD_FEATURES, old_w) + bonus[k] for k, f in old.items()}
        rn, ro = sorted(sn, key=lambda k: -sn[k]), sorted(so, key=lambda k: -so[k])
        print(f"\n== {c}: {len(new)} (weapon, affinity) candidates; main-hand pick before {ro[0]}, after {rn[0]}")
        for k in rn[:5] + [k for k in new if k[0] in targets and k not in rn[:5]]:
            f = new[k]
            stored = roles["weapons"][k[0]]["main_hand"]["by_affinity"][k[1]].get("score")
            print(f"  {k[1]} {k[0]}: {so[k]:.3f} (stored {stored}) -> {sn[k]:.3f}, rank {ro.index(k) + 1} -> "
                  f"{rn.index(k) + 1}; feint_hp {f.get('feint_hp')} (r2_feint {f.get('r2_feint')}, window "
                  f"{f.get('r2_delay')} f); R1 HP dash {f.get('r1_dash_damage')} roll {f.get('r1_roll_damage')} "
                  f"backstep {f.get('r1_bstep_damage')} crouch {f.get('r1_crouch_damage')}")
    trades.save()
    return 0


if __name__ == "__main__":
    sys.exit(main())
