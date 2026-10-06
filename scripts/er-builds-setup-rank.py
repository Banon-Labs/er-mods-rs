#!/usr/bin/env python3
"""Setup ranking (right weapon x off-hand, each hand with its own weapon buff) from an
`er-builds-pvp.py --setup --json` run, set against the corpus's own pairings.

    python3 scripts/er-builds-pvp.py --rl 150 --one-handed --setup --jobs 4 --json > setup.json
    python3 scripts/er-builds-pvp.py --rl 150 --one-handed --paired-loop --jobs 4 --json > loop.json
    python3 scripts/er-builds-setup-rank.py --setup setup.json --loop loop.json

Written for docs/er-mechanics/combo.md section 11a. Per one-handed row the setup run carries the
kept left (`moveset.paired_loop`: left, affinity, left weapon buff, right-hand buff, every
candidate's score) and `unpaired`, the row's moveset with nothing in the left hand. A setup's
gain is its moveset score before the skill term minus `unpaired`; a pair's gain is that
candidate's score minus `unpaired`.

Corpus: the PvP builds of the RL window (`er-mechanics-offhand.corpus`), each build's first
right-hand weapon paired with every left-hand weapon whose L1 is an off-hand attack with it
(`er-mechanics-combo.left_mode`). Spearman of gain against pair counts:
  - per right weapon, against builds pairing it with any off-hand, and with a hatchet (the count
    the previous paired loop was checked against, combo.md section 11: -0.087);
  - per pair, over every scored (right, left);
  - within each right weapon with enough corpus pairs, its candidates' scores against their
    counts (mean rho).
`--loop` adds the same per-right numbers for a `--paired-loop` run (gain against the setup
run's `unpaired`, the same right-hand slots).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = Path.home() / ".cache/er-build-planner"
HATCHETS = ("Hand Axe", "Forked Hatchet", "Icerind Hatchet")


def _sibling(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def corpus_pairs(off, sc) -> Counter:
    """{(right name, left name): builds} over the scorer's corpus, off-hand pairs only."""
    pairs = Counter()
    for b in sc.builds:
        rname = next((n for n in b["right"] if n in sc.names), None)
        if rname is None:
            continue
        rid = sc.names[rname]
        for n in set(b["left"]):
            lid = sc.names.get(n)
            if lid is not None and off.COMBO.left_mode(sc.reg, rid, lid) == "offhand":
                pairs[(rname, n)] += 1
    return pairs


def rows_of(path: Path) -> list[dict]:
    return [r for r in json.loads(path.read_text())["results"] if not r["two"]]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--setup", type=Path, required=True, help="er-builds-pvp.py --setup --json output")
    ap.add_argument("--loop", type=Path, help="er-builds-pvp.py --paired-loop --json output (comparison)")
    ap.add_argument("--rl", type=int, default=150)
    ap.add_argument("--window", type=int, default=10)
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--min-pairs", type=int, default=3,
                    help="within-right rho only for right weapons with at least this many corpus pairs")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    off = _sibling("er-mechanics-offhand")
    off.PVP.lower_priority()
    sc = off.Scorer(a.rl, a.window)
    pairs = corpus_pairs(off, sc)
    per_right = Counter()
    per_right_hatchet = Counter()
    for (r, n), c in pairs.items():
        per_right[r] += c
        if n in HATCHETS:
            per_right_hatchet[r] += c

    rows = [r for r in rows_of(a.setup) if (r["moveset"].get("paired_loop") or {}).get("unpaired") is not None]
    unpaired = {r["weapon"]: r["moveset"]["paired_loop"]["unpaired"] for r in rows}
    setups = []
    pair_rows = []
    for r in rows:
        pl = r["moveset"]["paired_loop"]
        k = pl["candidates"][pl["left"]]
        setups.append({"right": r["weapon"], "right_aff": r["aff"], "right_grease": r["grease"],
                       "right_buff": k.get("right_buff"), "left": pl["left"], "left_aff": k["aff"],
                       "left_buff": k.get("buff"), "score": r["moveset"]["score"],
                       "base": r["moveset"]["base_score"], "unpaired": pl["unpaired"],
                       "gain": r["moveset"]["base_score"] - pl["unpaired"],
                       "corpus_offhand": per_right[r["weapon"]], "corpus_hatchet": per_right_hatchet[r["weapon"]]})
        for n, c in pl["candidates"].items():
            pair_rows.append({"right": r["weapon"], "left": n, "score": c["score"], "gain": c["score"] - pl["unpaired"],
                              "left_aff": c["aff"], "left_buff": c.get("buff"), "right_buff": c.get("right_buff"),
                              "corpus": pairs[(r["weapon"], n)]})
    setups.sort(key=lambda s: -s["score"])
    pair_rows.sort(key=lambda p: -p["score"])

    rho = {
        "per_right_offhand": off.spearman([s["gain"] for s in setups], [s["corpus_offhand"] for s in setups]),
        "per_right_hatchet": off.spearman([s["gain"] for s in setups], [s["corpus_hatchet"] for s in setups]),
        "per_pair": off.spearman([p["gain"] for p in pair_rows], [p["corpus"] for p in pair_rows]),
    }
    within = []
    by_right = {}
    for p in pair_rows:
        by_right.setdefault(p["right"], []).append(p)
    for right, ps in by_right.items():
        if sum(p["corpus"] for p in ps) >= a.min_pairs:
            x = off.spearman([p["score"] for p in ps], [p["corpus"] for p in ps])
            if x is not None:
                within.append((right, x, sum(p["corpus"] for p in ps)))
    rho["within_right_mean"] = statistics.fmean(x for _, x, _ in within) if within else None
    rho["within_right_n"] = len(within)
    loop = None
    if a.loop:
        lrows = [r for r in rows_of(a.loop) if r["weapon"] in unpaired and r["moveset"].get("paired_loop")]
        g = [r["moveset"]["base_score"] - unpaired[r["weapon"]] for r in lrows]
        loop = {"rows": len(lrows),
                "per_right_offhand": off.spearman(g, [per_right[r["weapon"]] for r in lrows]),
                "per_right_hatchet": off.spearman(g, [per_right_hatchet[r["weapon"]] for r in lrows])}

    if a.json:
        print(json.dumps({"setups": setups, "pairs": pair_rows[:500], "rho": rho, "loop": loop,
                          "within": within}, indent=1))
        return 0
    print(f"RL {a.rl}: {len(setups)} one-handed rights, {len(pair_rows)} scored pairs, corpus {len(sc.builds)} "
          f"PvP builds, {sum(pairs.values())} off-hand pairings over {len(pairs)} distinct pairs")
    print(f"\nTop setups (moveset score with the skill term; gain = before the skill term over no left hand)")
    print(f"  {'right':<28}{'build':>18}{'right buff':>22}  {'left':<26}{'left aff/buff':<34}{'score':>8}"
          f"{'gain':>8}{'corpus':>7}")
    for s in setups[:a.top]:
        rb = s["right_buff"] if s["right_buff"] != "grease" else (s["right_grease"] or "-")
        print(f"  {s['right']:<28}{s['right_aff']:>18}{rb:>22}  {s['left']:<26}"
              f"{s['left_aff'] + ' / ' + str(s['left_buff']):<34}{s['score']:>8.1f}{s['gain']:>8.1f}"
              f"{s['corpus_offhand']:>7}")
    print(f"\nTop pairs (candidate moveset score before the skill term)")
    for p in pair_rows[:a.top]:
        print(f"  {p['right']:<28}{p['left']:<26}{p['left_aff'] + ' / ' + str(p['left_buff']):<34}"
              f"right {str(p['right_buff']):<22}{p['score']:>8.1f}{p['gain']:>8.1f}{p['corpus']:>5}")
    kept_left = Counter(s["left"] for s in setups)
    kept_buff = Counter(str(s["left_buff"]) for s in setups)
    kept_right = Counter(str(s["right_buff"]) for s in setups)
    print(f"\nKept left: {kept_left.most_common(12)}")
    print(f"Kept left buff: {kept_buff.most_common(12)}")
    print(f"Right hand: {kept_right.most_common(8)}")
    print(f"\nCorpus pairs, the most used: {pairs.most_common(15)}")
    def f(x):
        return "-" if x is None else f"{x:.3f}"
    print(f"\nSpearman, setup gain vs corpus: per right (any off-hand) {f(rho['per_right_offhand'])}, per right "
          f"(hatchet) {f(rho['per_right_hatchet'])}, per pair {f(rho['per_pair'])}, within right (mean of "
          f"{rho['within_right_n']}) {f(rho['within_right_mean'])}")
    if loop:
        print(f"Spearman, paired-loop gain vs corpus ({loop['rows']} rows): per right (any off-hand) "
              f"{f(loop['per_right_offhand'])}, per right (hatchet) {f(loop['per_right_hatchet'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
