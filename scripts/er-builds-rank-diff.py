#!/usr/bin/env python3
"""Rank and score movement between two `er-builds-pvp.py --sort score --json` runs.

    python3 scripts/er-builds-rank-diff.py base.json other.json [--show weapon ...] [--top n]

Rows are keyed by (weapon, grip) and ranked by `moveset.score` (the skill term included), as
`--sort score` ranks them. Prints how many rows moved, the family engagements of the second run that take a
follow-up (the off-hand `left_1` of `--paired-offhand` counted apart), the largest rank and score
movers, and for each `--show` weapon its rank, score and per-family best engagement in both runs.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter


def load(path: str) -> dict:
    out = {}
    for r in json.load(open(path))["results"]:
        ms = r["moveset"]
        out[(r["weapon"], "2H" if r["two"] else "1H")] = {
            "score": ms.get("score") or 0.0, "base": ms.get("base_score") or 0.0, "best": ms.get("best_opener"),
            "fam": {n: {"opener": f["opener"], "depth": f["depth"], "links": f.get("links") or [],
                        "p": f.get("p"), "score": f["score"], "rate": f.get("rate_score"),
                        "share": f.get("share", 0.0)}
                    for n, f in (ms.get("families") or {}).items()}}
    for i, k in enumerate(sorted(out, key=lambda k: -out[k]["score"]), 1):
        out[k]["rank"] = i
    return out


def _fam(f: dict | None) -> str:
    if not f:
        return "-"
    chain = " -> ".join([f["opener"], *f["links"]])
    p = f" p={[round(x, 3) for x in f['p']]}" if f["p"] else ""
    rate = f" (rate {f['rate']:.1f})" if f.get("rate") is not None else ""
    return f"{chain}{p} {f['score']:.1f}{rate} share {f['share']:.3f}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("base")
    ap.add_argument("other")
    ap.add_argument("--show", nargs="*", default=[], help="weapons to print in full")
    ap.add_argument("--top", type=int, default=20)
    a = ap.parse_args()
    x, y = load(a.base), load(a.other)
    keys = [k for k in x if k in y]
    moves = [(x[k]["rank"] - y[k]["rank"], y[k]["score"] - x[k]["score"], k) for k in keys]
    print(f"rows {len(x)} / {len(y)}; score moved by more than 0.05: "
          f"{sum(abs(m[1]) > 0.05 for m in moves)}; rank moved: {sum(1 for m in moves if m[0])}")
    fams = [(k, n, f) for k, v in y.items() for n, f in v["fam"].items() if f["depth"]]
    left = [t for t in fams if "left_1" in t[2]["links"]]
    print(f"family engagements taking a follow-up: {len(fams)} ({len(left)} the off-hand L1); "
          f"L1 by opener {Counter(f['opener'] for _, _, f in left).most_common()}")
    for k, n, f in [t for t in fams if "left_1" not in t[2]["links"]][:a.top]:
        print(f"  same-weapon: {k[0]} {k[1]} {n}: {_fam(f)}")

    def line(k, d):
        return (f"  {k[0]:<34}{k[1]}  rank {x[k]['rank']:>4} -> {y[k]['rank']:>4} ({d:+d})  "
                f"score {x[k]['score']:7.1f} -> {y[k]['score']:7.1f}  best {x[k]['best']} -> {y[k]['best']}")
    print("\nrank risers:")
    for d, _, k in sorted(moves, key=lambda m: (-m[0], -m[1]))[:a.top]:
        print(line(k, d))
    print("\nscore gains:")
    for d, _, k in sorted(moves, key=lambda m: -m[1])[:a.top]:
        print(line(k, d))
    print("\nrank fallers:")
    for d, _, k in sorted(moves, key=lambda m: (m[0], m[1]))[:a.top // 2]:
        print(line(k, d))
    for name in a.show:
        for g in ("1H", "2H"):
            k = (name, g)
            if k not in x or k not in y:
                continue
            print(f"\n{name} {g}: rank {x[k]['rank']} -> {y[k]['rank']}, score {x[k]['score']:.1f} -> "
                  f"{y[k]['score']:.1f} (moveset before the skill term {x[k]['base']:.1f} -> {y[k]['base']:.1f})")
            for n in sorted(set(x[k]["fam"]) | set(y[k]["fam"])):
                print(f"   {n:<5} {_fam(x[k]['fam'].get(n))}\n         -> {_fam(y[k]['fam'].get(n))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
