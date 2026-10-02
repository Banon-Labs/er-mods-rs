#!/usr/bin/env python3
"""Compare whole PvP rankings made at different values of one assumed constant.

    python3 scripts/er-builds-sweep-compare.py REF=rank-e5.json e3=rank-e3.json e8=rank-e8.json [--top 30]

Each ranking is an `er-builds-pvp.py --sort score --json` output (see
`er-builds-constant-sweep.sh`). Per point: the Spearman rho of the grip-blended stored score
against primary adoption (`er-builds-score-adoption.analyse`, all swept weapons and adopted only),
the paired bootstrap CI of rho(point) - rho(REF), and how the REF top-N rows move: rows still in
the top N, the largest rank move among them, and Spearman over the REF top N's ranks.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = Path.home() / ".cache/er-build-planner"


def _mod(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


def row_ranks(results: list[dict]) -> dict:
    order = sorted(results, key=lambda r: -(r["moveset"]["score"] or 0.0))
    return {(r["weapon"], r["two"]): i + 1 for i, r in enumerate(order)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("points", nargs="+", help="NAME=ranking.json; the first is the reference")
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--mirror", type=Path, default=CACHE / "builds.jsonl")
    ap.add_argument("--window", type=int, default=10)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    sa, mv = _mod("er-builds-score-adoption"), _mod("er-mechanics-moveset")
    pts = [p.split("=", 1) for p in a.points]
    data = {n: json.load(open(f)) for n, f in pts}
    ref = pts[0][0]
    rl = data[ref]["rl"]
    shares = mv.grip_shares(a.mirror, rl - a.window, rl + a.window)
    scores = {}
    for n, _ in pts:
        rows = {(r["weapon"], r["two"]): r["moveset"]["score"] for r in data[n]["results"]}
        scores["full" if n == ref else n] = sa.weapon_scores(mv, rows, shares)
    res = sa.analyse(scores, shares["_adoption"])
    ranks = {n: row_ranks(data[n]["results"]) for n, _ in pts}
    top = [k for k, v in sorted(ranks[ref].items(), key=lambda kv: kv[1])[:a.top]]
    out = {"ref": ref, "top": a.top, "points": {}}
    for n, _ in pts:
        pt = {}
        for pop in ("all", "adopted"):
            r = res[pop]
            if n == ref:
                pt[pop] = {"rho": r["rho"], "ci": r["ci"]}
            else:
                v = r["variants"][n]
                # analyse's delta is ref - point; report point - ref.
                pt[pop] = {"rho": v["rho_without"], "ci": v["ci_without"], "delta": -v["delta"],
                           "delta_ci": (-v["ci"][1], -v["ci"][0])}
        rk = ranks[n]
        moved = [abs(rk[k] - ranks[ref][k]) for k in top if k in rk]
        pt["top_kept"] = sum(1 for k in top if rk.get(k, 10 ** 9) <= a.top)
        pt["top_max_move"] = max(moved, default=0)
        pt["top_rho"] = float(sa.spearman([ranks[ref][k] for k in top], [rk.get(k, 10 ** 9) for k in top]))
        out["points"][n] = pt
    for n, pt in out["points"].items():
        line = f"{n:>6}"
        for pop in ("all", "adopted"):
            p = pt[pop]
            line += f"  {pop} rho {p['rho']:+.4f}"
            if "delta" in p:
                line += f" d {p['delta']:+.4f} [{p['delta_ci'][0]:+.4f},{p['delta_ci'][1]:+.4f}]"
        line += f"  top{a.top} kept {pt['top_kept']} max move {pt['top_max_move']} rho {pt['top_rho']:+.3f}"
        print(line)
    if a.json:
        a.json.write_text(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
