#!/usr/bin/env python3
"""Before / after of the interrupt weight on the ash ranking `er-mechanics-combo-loadout.py` cuts at.

    python3 scripts/er-mechanics-ash-interrupt-report.py <rows.json>... [--cache <ash-interrupt.json>]

Reads the sweeps and the interrupt cache (`er-mechanics-ash-interrupt.profiles`), applies the same
blacklist, off-stat and dominance filters as the plan, and prints each damaging ash's best value
anywhere in the pool with and without the multiplier: the top 15 each way, the biggest movers, the
named ashes, and the ashes on either side of the `ASH_RANK_KEEP` cut. Read-only.
"""
import argparse
import importlib.util
import json
import math
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("rows", type=Path, nargs="+")
    ap.add_argument("--cache", type=Path, default=Path.home() / ".cache/er-build-planner/ash-interrupt.json")
    ap.add_argument("--named", default="Impaling Thrust,Blood Tax")
    a = ap.parse_args()
    spec = importlib.util.spec_from_file_location("er_mechanics_combo_loadout", HERE / "er-mechanics-combo-loadout.py")
    CL = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(CL)
    AI = CL._ai()
    bl = tomllib.loads(CL.BLACKLIST.read_text())
    seen, rows = set(), []
    for p in a.rows:
        for x in json.loads(p.read_text()):
            k = (x["weapon"], x.get("affinity", "Keen"), x["ash"], x["grip"])
            if k not in seen and x["ash"] not in bl and not CL.off_stat(x):
                seen.add(k)
                x.setdefault("affinity", "Keen")
                rows.append(x)
    cache = json.loads(a.cache.read_text())["rows"]
    for x in rows:
        r = cache.get(AI.row_key(x))
        x["prof"] = r if r and "error" not in r else None
        x["imult"] = AI.multiplier(x["prof"], CL.raw_value(x))
    beaten = CL.dominated(rows)
    rows = [x for x in rows if x["ash"] not in beaten]
    before, after, bx, ax = {}, {}, {}, {}
    for x in rows:
        if x["cat"] == "utility":
            continue
        n, rv, v = x["ash"], CL.raw_value(x), CL.value(x)
        if rv > before.get(n, -1):
            before[n], bx[n] = rv, x
        if v > after.get(n, -1):
            after[n], ax[n] = v, x
    ob = sorted(before, key=lambda n: -before[n])
    oa = sorted(after, key=lambda n: -after[n])
    rb = {n: i + 1 for i, n in enumerate(ob)}
    ra = {n: i + 1 for i, n in enumerate(oa)}
    keep = math.ceil(len(ob) * CL.ASH_RANK_KEEP)
    print(f"{len(ob)} damaging ashes, rank {keep} is the last kept")

    def desc(x):
        p = x["prof"]
        if not p:
            return "no profile"
        s = p["sim"]
        return (f"{x['affinity']} {x['weapon']} {x['grip']}, D {p['D']} m, delivered "
                + "/".join(f"{q:.2f}" for q in s["p"][:4]) + f", taken {s['taken']:.0f}, saved {s['saved']:.1f} f, "
                f"x{x['imult']:.2f}")

    def x_of(n):
        return after[n] / before[n] if before[n] else 1.0

    print("\ntop 15 before")
    for n in ob[:15]:
        print(f"  {rb[n]:3d} {n:28s} {before[n]:6.0f} -> #{ra[n]:<3d} {after[n]:6.0f} x{x_of(n):.2f}")
    print("\ntop 15 after")
    for n in oa[:15]:
        print(f"  {ra[n]:3d} {n:28s} {after[n]:6.0f} (was #{rb[n]}, x{x_of(n):.2f}) {desc(ax[n])}")
    mv = sorted(ob, key=lambda n: ra[n] - rb[n])
    print("\nup")
    for n in mv[:8]:
        print(f"  {n:28s} #{rb[n]} -> #{ra[n]} x{x_of(n):.2f} {desc(ax[n])}")
    print("\ndown")
    for n in mv[::-1][:8]:
        print(f"  {n:28s} #{rb[n]} -> #{ra[n]} x{x_of(n):.2f} {desc(ax[n])}")
    for n in a.named.split(","):
        if n in rb:
            print(f"\n{n}: #{rb[n]} -> #{ra[n]}, x{x_of(n):.2f}\n  before best {desc(bx[n])}\n  after best  {desc(ax[n])}")
    cut_b, cut_a = set(ob[keep:]), set(oa[keep:])
    print(f"\nnewly cut: {sorted(cut_a - cut_b)}\nnewly kept: {sorted(cut_b - cut_a)}")
    ms = sorted(x["imult"] for x in rows if x["cat"] != "utility" and x["prof"])
    print(f"\nrow multipliers: median {ms[len(ms) // 2]:.2f}, zero {sum(m == 0 for m in ms) / len(ms):.1%}, "
          f"above 1 {sum(m > 1 for m in ms) / len(ms):.1%}; rows without a profile {sum(not x['prof'] for x in rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
