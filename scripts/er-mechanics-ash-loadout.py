#!/usr/bin/env python3
"""Pick the fewest weapons that carry the best ashes of war, from an `er-mechanics-ash-table.py
--build ... --json` sweep.

    python3 scripts/er-mechanics-ash-loadout.py <rows.json> --top 10 --max-weapons 6 --out <file.html>

The pool is the `--top` ashes of each category (bullet, non-bullet, melee bullet), ranked by their
best damage per second anywhere in the sweep. Every ash then belongs on exactly one weapon: an
inventory may hold several copies of one weapon, each with its own ash, so a weapon is a base that
carries as many pool ashes as suit it, and an ash is never repeated on a second base.

How well a weapon carries an ash, between 0 and 1:

    value(ash, weapon) = dps(ash, weapon) / best dps(ash)  x  (reach(ash, weapon) / best reach(ash)) ** REACH_WEIGHT

`reach` is the blade's reach from the start position for non-bullet and melee-bullet ashes and the
bullet's maximum reach for bullet ashes; best is over every weapon in the sweep that mounts the ash.
For every weapon count from 1 to `--max-weapons`, the set of weapons is the one that maximises the
sum of each pool ash's value on its best weapon in the set (exhaustive over the candidate weapons:
those within `CANDIDATE_FLOOR` of the best value for at least one pool ash) while that is at most
`EXHAUSTIVE_LIMIT` sets, greedy plus single swaps beyond it. An ash with no weapon
in the set that mounts it scores 0 and is listed as missing.
"""
from __future__ import annotations

import argparse
import html
import itertools
import json
import math
import sys
from pathlib import Path

REACH_WEIGHT = 0.5
CANDIDATE_FLOOR = 0.85
EXHAUSTIVE_LIMIT = 20000
CATEGORIES = ("bullet", "non-bullet", "melee bullet")


def reach_of(x):
    if x["cat"] == "bullet":
        return (x.get("reach") or [0, 0])[1]
    return (x.get("melee_reach") or [0, 0])[0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("rows", type=Path)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--max-weapons", type=int, default=6)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--plan-json", type=Path,
                    help="also write the largest weapon count's plan: [{weapon, ash, cat, grip, affinity, value, dps}]")
    a = ap.parse_args()
    rows = json.loads(a.rows.read_text())

    best = {}
    for x in rows:
        k = (x["ash"], x["weapon"])
        if k not in best or x["dps"] > best[k]["dps"]:
            best[k] = x
    pool = []
    for c in CATEGORIES:
        top = {}
        for x in rows:
            if x["cat"] == c and x["dps"] > top.get(x["ash"], 0):
                top[x["ash"]] = x["dps"]
        pool += sorted(top, key=lambda n: -top[n])[:a.top]
    cat_of = {x["ash"]: x["cat"] for x in rows}
    value = {}
    for ash in pool:
        carriers = [x for (n, _), x in best.items() if n == ash]
        top_dps = max(x["dps"] for x in carriers)
        top_reach = max(reach_of(x) for x in carriers) or 1.0
        for x in carriers:
            value[ash, x["weapon"]] = (x["dps"] / top_dps) * ((reach_of(x) or 0.0) / top_reach) ** REACH_WEIGHT
    candidates = sorted({w for (ash, w), v in value.items()
                         if v >= CANDIDATE_FLOOR * max(v2 for (a2, _), v2 in value.items() if a2 == ash)})

    def score(bases):
        total, plan = 0.0, {}
        for ash in pool:
            opts = [(value[ash, w], w) for w in bases if (ash, w) in value]
            if opts:
                v, w = max(opts)
                total += v
                plan[ash] = (w, v)
        return total, plan

    def search(k):
        """Exhaustive while the combinations stay small, otherwise greedy additions refined by
        single swaps until no swap improves the total."""
        if math.comb(len(candidates), k) <= EXHAUSTIVE_LIMIT:
            return max(itertools.combinations(candidates, k), key=lambda b: score(b)[0])
        chosen = []
        for _ in range(k):
            chosen.append(max((w for w in candidates if w not in chosen), key=lambda w: score(chosen + [w])[0]))
        improved = True
        while improved:
            improved = False
            for i, w in itertools.product(range(k), candidates):
                if w in chosen:
                    continue
                trial = chosen[:i] + [w] + chosen[i + 1:]
                if score(trial)[0] > score(chosen)[0] + 1e-9:
                    chosen, improved = trial, True
        return tuple(chosen)

    print(f"{len(pool)} pool ashes, {len(candidates)} candidate weapons", file=sys.stderr)
    results = []
    for k in range(1, a.max_weapons + 1):
        top = search(k)
        total, plan = score(top)
        results.append((k, top, total, plan))
        print(f"{k} weapons, {total:.2f} of {len(pool)}: {', '.join(top)}")

    out = ["<html><head><meta charset=utf-8><title>Ash loadouts</title><style>body{font-family:sans-serif;"
           "margin:24px}table{border-collapse:collapse;margin-bottom:24px}td,th{border:1px solid #888;"
           "padding:4px 8px}th{background:#ddd}td.n{text-align:right}.miss{color:#a00}</style></head><body>",
           f"<h2>Fewest weapons for the top {a.top} ashes of each category</h2>",
           "<p>value = dps / best dps for that ash &times; (reach / best reach)<sup>0.5</sup>; 1.00 means the ash is "
           "on its best carrier. Each weapon listed is one base: hold one copy of it per ash assigned to it, each "
           "copy with its own ash. Total is the sum of values over the "
           f"{len(pool)} pool ashes.</p>"]
    for k, bases, total, plan in results:
        out.append(f"<h3>{k} weapon{'s' if k > 1 else ''}: total {total:.2f} of {len(pool)}</h3><table>"
                   "<tr><th>weapon</th><th>category</th><th>ash</th><th>grip</th><th>value</th><th>damage/s</th>"
                   "<th>damage</th><th>reach (m)</th></tr>")
        for w in bases:
            mine = sorted((ash for ash, (pw, _) in plan.items() if pw == w),
                          key=lambda n: (CATEGORIES.index(cat_of[n]), -plan[n][1]))
            for i, ash in enumerate(mine):
                x = best[ash, w]
                out.append(f"<tr><td>{html.escape(w) if i == 0 else ''}</td><td>{cat_of[ash]}</td>"
                           f"<td>{html.escape(ash)}</td><td>{x['grip']}</td><td class=n>{plan[ash][1]:.2f}</td>"
                           f"<td class=n>{x['dps']}</td><td class=n>{x['dmg']}</td><td class=n>{reach_of(x)}</td></tr>")
        missing = [n for n in pool if n not in plan]
        out.append("</table>" + (f"<p class=miss>No weapon here mounts: {html.escape(', '.join(missing))}</p>"
                                 if missing else ""))
    out.append("</body></html>")
    a.out.write_text("\n".join(out))
    if a.plan_json:
        _, bases, _, plan = results[-1]
        a.plan_json.write_text(json.dumps([
            {"weapon": w, "ash": ash, "cat": cat_of[ash], "grip": best[ash, w]["grip"],
             "affinity": best[ash, w].get("affinity", "Keen"), "value": round(v, 3), "dps": best[ash, w]["dps"]}
            for ash, (w, v) in plan.items()], indent=1))
    print(a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
