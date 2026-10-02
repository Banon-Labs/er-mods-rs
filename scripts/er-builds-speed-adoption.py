#!/usr/bin/env python3
"""Does the fastest weapon of a class get adopted most? Startup rank against adoption rank.

    python3 scripts/er-builds-speed-adoption.py --pvp rank.json --offhand offhand.json
    python3 scripts/er-builds-speed-adoption.py --selftest

Evidence for (or against) a relative-speed term in `er-builds-pvp.py`. Class = `wepType`, the key
`er-mechanics-ashes.ash_adoption_check` uses. Two hands, each within its class:

* main hand: the one-handed R1 #1's first active frame (`slots.r1_1.startup` of the 1H row of an
  `er-builds-pvp.py --json` ranking) against primary adoption in the RL window's PvP corpus
  (`er-mechanics-moveset.grip_shares` `_adoption`, right hand position 0);
* off-hand: the off-hand L1 #1's first active frame (`er-mechanics-offhand.py --json` rows)
  against their `corpus_offhand` use.

Per class with at least `--min-weapons` weapons and some adoption: Spearman(-startup, adoption),
whether the most used weapon is among the fastest, and the fastest weapons' share of the class's
adoption against their share of its weapons. Across classes: Spearman(gap to the second-fastest
startup, the fastest weapons' adoption share), and the log-linear slope of within-class adoption
share on frames behind the class's fastest (`fit_slope`), which is what the speed term's one
parameter is read from (so that parameter is chosen from this corpus, `MEASURED` on it).
"""

from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import math
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = Path.home() / ".cache/er-build-planner"


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def spearman(a, b):
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r, i = [0.0] * len(v), 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2.0 + 1.0
            i = j + 1
        return r
    if len(a) < 3:
        return None
    ra, rb = ranks(a), ranks(b)
    ma, mb = statistics.fmean(ra), statistics.fmean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den if den else None


def analyse(items: list[dict], min_weapons: int = 3) -> dict:
    """`items`: [{'name', 'cls', 'startup', 'use'}]. Per-class and pooled statistics (module
    docstring)."""
    by = collections.defaultdict(list)
    for it in items:
        if it["startup"] is not None:
            by[it["cls"]].append(it)
    classes, gaps, xs, ys = [], [], [], []
    for cls, members in sorted(by.items(), key=lambda kv: str(kv[0])):
        total = sum(m["use"] for m in members)
        if len(members) < min_weapons or total <= 0:
            continue
        s = sorted({m["startup"] for m in members})
        fastest = [m for m in members if m["startup"] == s[0]]
        gap = (s[1] - s[0]) if len(s) > 1 else 0.0
        top_use = max(m["use"] for m in members)
        most = [m for m in members if m["use"] == top_use]
        share = sum(m["use"] for m in fastest) / total
        classes.append({"cls": cls, "n": len(members), "adoption": total,
                        "rho": spearman([-m["startup"] for m in members], [m["use"] for m in members]),
                        "fastest": [m["name"] for m in fastest], "fastest_startup": s[0], "gap": gap,
                        "most_used": [m["name"] for m in most],
                        "fastest_is_most_used": any(m in fastest for m in most),
                        "fastest_share": share, "fastest_weapon_share": len(fastest) / len(members)})
        gaps.append((gap, share - len(fastest) / len(members)))
        for m in members:
            # Adoption share per weapon against frames behind the class's fastest, +0.5 build so
            # an unused weapon has a finite log (`INFERRED` smoothing).
            xs.append(m["startup"] - s[0])
            ys.append(math.log((m["use"] + 0.5) / (total + 0.5 * len(members))) + math.log(len(members)))
        c = classes[-1]
        c["points"] = [(m["startup"] - s[0], ys[k]) for k, m in zip(range(len(ys) - len(members), len(ys)), members)]
    rhos = [c["rho"] for c in classes if c["rho"] is not None]
    wsum = sum(c["adoption"] for c in classes if c["rho"] is not None)

    def fit(pts):
        if len(pts) < 3:
            return None
        mx, my = statistics.fmean(x for x, _ in pts), statistics.fmean(y for _, y in pts)
        vx = sum((x - mx) ** 2 for x, _ in pts)
        return sum((x - mx) * (y - my) for x, y in pts) / vx if vx else None
    slope = fit(list(zip(xs, ys)))
    # Percentile bootstrap over classes (a class's weapons move together).
    import random
    rng = random.Random(20260930)
    draws = []
    for _ in range(1000):
        pick = [classes[rng.randrange(len(classes))] for _ in classes] if classes else []
        v = fit([p for c in pick for p in c["points"]])
        if v is not None:
            draws.append(v)
    draws.sort()
    ci = (draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws)) - 1]) if draws else None
    unique = [c for c in classes if len(c["fastest"]) == 1]
    for c in classes:
        c.pop("points", None)
    return {"classes": classes,
            "rho_mean": statistics.fmean(rhos) if rhos else None,
            "rho_weighted": (sum(c["rho"] * c["adoption"] for c in classes if c["rho"] is not None) / wsum
                             if wsum else None),
            "fastest_is_most_used": sum(c["fastest_is_most_used"] for c in classes), "n_classes": len(classes),
            "fastest_share_mean": statistics.fmean(c["fastest_share"] for c in classes) if classes else None,
            "fastest_weapon_share_mean": (statistics.fmean(c["fastest_weapon_share"] for c in classes)
                                          if classes else None),
            "gap_rho": spearman([g for g, _ in gaps], [e for _, e in gaps]),
            "fit_slope": slope, "fit_slope_ci": ci,
            "unique_fastest": len(unique),
            "unique_fastest_most_used": sum(c["fastest_is_most_used"] for c in unique),
            "unique_fastest_share_mean": statistics.fmean(c["fastest_share"] for c in unique) if unique else None,
            "unique_fastest_uniform_mean": statistics.fmean(1 / c["n"] for c in unique) if unique else None}


def main_items(pvp_path: Path, mirror: Path, lo: int, hi: int) -> list[dict]:
    moveset = _load("er-mechanics-moveset")
    gap = _load("er-builds-adoption-gap")
    adoption = {gap.plain_name(k): v for k, v in moveset.grip_shares(mirror, lo, hi)["_adoption"].items()}
    weapon_rows, _ = gap.weight_tables()
    wep_type = {gap.plain_name(n): r["wepType"] for n, r in weapon_rows.items()}
    out = []
    for r in json.load(pvp_path.open())["results"]:
        if r["two"]:
            continue
        name = gap.plain_name(r["weapon"])
        if name not in wep_type:
            continue
        m = main_measures(r["slots"])
        r1 = r["slots"].get("r1_1") or {}
        out.append({"name": name, "cls": wep_type[name], "startup": m["first_hit"], "use": adoption.get(name, 0),
                    "measures": m, "family": (r1.get("anim") or "?")[:4]})
    return out


def _sub(a, b):
    return None if a is None or b is None else a - b


def main_measures(slots: dict) -> dict:
    """`er-builds-pvp.speed_measures`, the definitions the relative-speed term keys on."""
    return _load("er-builds-pvp").speed_measures(slots) if "er_builds_pvp" not in sys.modules \
        else sys.modules["er_builds_pvp"].speed_measures(slots)


def offhand_items(path: Path) -> list[dict]:
    out = []
    for r in json.load(path.open())["rows"]:
        s, nxt = r["startup"], r.get("next")
        chain = r.get("chain") or {}
        m = {"first_hit": s, "rec_roll": _sub(r.get("roll"), s), "rec_next": _sub(nxt, s),
             "second_hit": (s + chain["gap"]) if s is not None and chain.get("gap") is not None else None,
             "string_dps": -(r["dmg"] / nxt * 30.0) if nxt and r.get("dmg") else None}
        p = r.get("pressure") or {}
        # The thrown L1 chain (`er-mechanics-offhand.py --chain`): mean hit-to-hit gap, and minus
        # its expected damage per second.
        m["chain_gap"] = p.get("mean_gap")
        m["chain_dps"] = -p["dps"] if p.get("dps") else None
        out.append({"name": r["weapon"], "cls": r["wep_type"], "startup": s, "use": r["corpus_offhand"],
                    "measures": m, "family": (r.get("anim") or "?")[:4]})
    return out


def by_measure(items: list[dict], measure: str, family: bool = False) -> list[dict]:
    """`items` re-keyed on one measure. With `family`, each (class, animation set) becomes one
    unit, its adoption summed and its measure the median of its weapons: the families a player
    compares inside a class (halberd against glaive movesets), `INFERRED` grouping."""
    rows = [{**it, "startup": it["measures"].get(measure)} for it in items if it["measures"].get(measure) is not None]
    if not family:
        return rows
    groups = collections.defaultdict(list)
    for it in rows:
        groups[(it["cls"], it["family"])].append(it)
    return [{"name": f"{fam} ({len(g)}: {g[0]['name']})", "cls": cls, "use": sum(x["use"] for x in g),
             "startup": statistics.median(x["startup"] for x in g)} for (cls, fam), g in groups.items()]


MEASURES_MAIN = ("first_hit", "rec_roll", "rec_next", "second_hit", "string_dps", "run_r1", "roll_r1")
MEASURES_OFF = ("first_hit", "rec_roll", "rec_next", "second_hit", "string_dps", "chain_gap", "chain_dps")


def report(label: str, res: dict, show: int) -> None:
    print(f"\n{label}: {res['n_classes']} classes with >=3 weapons and some adoption")
    print(f"  Spearman(-startup, adoption) within class: mean {res['rho_mean'] and round(res['rho_mean'], 3)}, "
          f"adoption-weighted {res['rho_weighted'] and round(res['rho_weighted'], 3)}")
    print(f"  fastest is (among) the most used: {res['fastest_is_most_used']} of {res['n_classes']}")
    print(f"  fastest weapons' adoption share {res['fastest_share_mean'] and round(res['fastest_share_mean'], 3)} "
          f"vs their share of the class's weapons {res['fastest_weapon_share_mean'] and round(res['fastest_weapon_share_mean'], 3)}")
    print(f"  Spearman(gap to second-fastest, fastest excess share) across classes: "
          f"{res['gap_rho'] and round(res['gap_rho'], 3)}")
    ci = res["fit_slope_ci"]
    print(f"  log adoption share per frame behind the fastest (slope): {res['fit_slope'] and round(res['fit_slope'], 4)}"
          f" CI {ci and [round(v, 4) for v in ci]}")
    print(f"  classes with one fastest weapon: {res['unique_fastest']}; it is the most used in "
          f"{res['unique_fastest_most_used']}; its adoption share {res['unique_fastest_share_mean'] and round(res['unique_fastest_share_mean'], 3)} "
          f"vs 1/n {res['unique_fastest_uniform_mean'] and round(res['unique_fastest_uniform_mean'], 3)}")
    for c in sorted(res["classes"], key=lambda c: -c["adoption"])[:show]:
        print(f"    type {c['cls']!s:>3} n {c['n']:>3} adopt {c['adoption']:>4} rho {c['rho'] if c['rho'] is None else round(c['rho'], 2)!s:>5} "
              f"fastest {c['fastest_startup']} {c['fastest'][:2]} gap {c['gap']:.1f} share {c['fastest_share']:.2f} "
              f"most used {c['most_used'][:2]}")


def selftest() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok   " if cond else "FAIL ") + msg)
        ok = ok and bool(cond)
    items = [{"name": n, "cls": 1, "startup": s, "use": u} for n, s, u in
             (("a", 10, 30), ("b", 14, 5), ("c", 16, 1), ("d", 20, 0))]
    items += [{"name": n, "cls": 2, "startup": s, "use": u} for n, s, u in
              (("e", 12, 5), ("f", 12, 5), ("g", 13, 5))]
    r = analyse(items)
    c1 = next(c for c in r["classes"] if c["cls"] == 1)
    c2 = next(c for c in r["classes"] if c["cls"] == 2)
    check(abs(c1["rho"] - 1.0) < 1e-12 and c1["fastest_is_most_used"] and c1["gap"] == 4,
          "a class ordered by speed: rho 1, fastest most used, gap 4")
    check(c2["fastest"] == ["e", "f"] and c2["fastest_is_most_used"] and c2["gap"] == 1,
          "tied fastest weapons are all fastest; gap to the next distinct startup")
    check(r["fit_slope"] is not None and r["fit_slope"] < 0, "adoption falling with frames behind gives a negative slope")
    print("selftest", "passed" if ok else "FAILED")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pvp", type=Path, help="er-builds-pvp.py --json ranking")
    ap.add_argument("--offhand", type=Path, help="er-mechanics-offhand.py --json output")
    ap.add_argument("--mirror", type=Path, default=CACHE / "builds.jsonl")
    ap.add_argument("--rl", type=int, default=150)
    ap.add_argument("--window", type=int, default=10)
    ap.add_argument("--min-weapons", type=int, default=3)
    ap.add_argument("--show", type=int, default=12)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--all-measures", action="store_true",
                    help="every speed measure (`main_measures`), per weapon and per animation family")
    ap.add_argument("--apply", type=float, metavar="TAU",
                    help="write --pvp with `er-builds-pvp.apply_relative_speed` applied to --out: the same "
                         "rows a `--relative-speed TAU` run gives, since that factor is applied after scoring")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--measure", default="string_dps", help="speed measure for --apply (er-builds-pvp.SPEED_MEASURES)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.apply:
        pvp = _load("er-builds-pvp")
        data = json.load(a.pvp.open())
        pvp.apply_relative_speed(data["results"], pvp.AR.Tables(None), a.apply, a.measure)
        a.out.write_text(json.dumps(data))
        return 0
    out = {}
    hands = []
    if a.pvp:
        hands.append(("main", main_items(a.pvp, a.mirror, a.rl - a.window, a.rl + a.window), MEASURES_MAIN))
    if a.offhand:
        hands.append(("offhand", offhand_items(a.offhand), MEASURES_OFF))
    for hand, items, measures in hands:
        for m in (measures if a.all_measures else ("first_hit",)):
            for fam in ((False, True) if a.all_measures else (False,)):
                out[f"{hand} {m}{' family' if fam else ''}"] = analyse(by_measure(items, m, fam), 2 if fam else a.min_weapons)
    if a.json:
        print(json.dumps(out, indent=1, default=str))
        return 0
    if a.all_measures:
        print(f"{'hand measure unit':<34}{'cls':>4}{'rho':>7}{'rhoW':>7}{'fast=top':>10}{'shr/wshr':>12}"
              f"{'1-fast':>8}{'gapRho':>8}{'slope [CI]':>26}")
        for k, v in out.items():
            ci = v["fit_slope_ci"]
            f = lambda x, n=3: "-" if x is None else f"{x:.{n}f}"  # noqa: E731
            print(f"{k:<34}{v['n_classes']:>4}{f(v['rho_mean']):>7}{f(v['rho_weighted']):>7}"
                  f"{v['fastest_is_most_used']:>5}/{v['n_classes']:<4}"
                  f"{f(v['fastest_share_mean'], 2):>6}/{f(v['fastest_weapon_share_mean'], 2):<5}"
                  f"{v['unique_fastest_most_used']:>3}/{v['unique_fastest']:<4}"
                  f"{f(v['unique_fastest_share_mean'], 2):>6}/{f(v['unique_fastest_uniform_mean'], 2):<5}{f(v['gap_rho']):>8}"
                  f"{f(v['fit_slope'], 4):>10} [{f(ci and ci[0], 3)}, {f(ci and ci[1], 3)}]")
        return 0
    for k, v in out.items():
        report(k, v, a.show)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
