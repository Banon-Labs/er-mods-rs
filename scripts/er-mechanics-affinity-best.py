#!/usr/bin/env python3
"""Every melee weapon's best build at one rune level with the affinity pinned, ranked.

    python3 scripts/er-mechanics-affinity-best.py --affinity Cold --rl 150
    python3 scripts/er-mechanics-affinity-best.py --affinity Cold --two-handed --json out.json

Each weapon `er-builds-optimize.sweep_kind` counts as melee and that has a row for `--affinity`
goes through `er-builds-optimize.weapon_best` exactly as the single-weapon CLI runs it with
`--affinity <aff> --grease none`: Vigor, Mind and Endurance floored from the corpus, every
requirement met, every starting class tried, the remaining points spent for the most damage on
one motion-value-100 hit against the median defender of the RL window. Rows are printed best
first, with the stat the build leans on most (`lean`) so STR, DEX and INT spreads separate.
"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location("er_builds_optimize", HERE / "er-builds-optimize.py")
OPT = importlib.util.module_from_spec(_s)
sys.modules["er_builds_optimize"] = OPT
_s.loader.exec_module(OPT)
AR = OPT.AR
DAMAGE_STATS = ("str", "dex", "int", "fth", "arc")
_G: dict = {}


def _init(rl, window, two, aff):
    _G.update(t=AR.Tables(None), rows=OPT.corpus_rows(OPT.CACHE / "builds.jsonl", rl - window, rl + window),
              rl=rl, two=two, aff=aff)


def _one(wid):
    t = _G["t"]
    row = {"id": wid, "name": t.names.get(wid)}
    try:
        wb = OPT.weapon_best(t, _G["rows"], wid, _G["rl"], _G["two"], "damage", None, [_G["aff"]],
                             15, "pvp", "medium", None)
    except SystemExit as e:
        return {**row, "error": str(e)}
    if not wb["results"]:
        return {**row, "error": "no class reaches this RL with its requirements"}
    b = wb["results"][0]
    st = b["stats"]
    return {**row, "class": b["class"], "stats": st, "score": round(b["score"], 1),
            "lean": max(DAMAGE_STATS, key=lambda k: st[k]),
            "ar": {k: round(v) for k, v in b["by_element"].items() if v},
            "status": {k: round(v["total"]) for k, v in b["ar"].get("status", {}).items()}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--affinity", default="Cold", choices=AR.AFFINITIES)
    ap.add_argument("--rl", type=int, default=150)
    ap.add_argument("--window", type=int, default=10)
    ap.add_argument("--two-handed", action="store_true")
    ap.add_argument("--json", type=Path, help="also write the rows here")
    a = ap.parse_args()
    t = AR.Tables(None)
    real = OPT._real_weapon_ids()
    ids = [i for i in sorted(t.weapons) if i % 10000 == 0 and OPT.sweep_kind(t, i, real) is not None
           and a.affinity in OPT.affinities(t, i)]
    # `weapon_best` already fans out over classes in its own pool, so weapons go one at a time.
    _init(a.rl, a.window, a.two_handed, a.affinity)
    rows = [_one(i) for i in ids]
    ok = sorted((r for r in rows if "error" not in r), key=lambda r: -r["score"])
    print(f"{a.affinity}, RL {a.rl}, {'two' if a.two_handed else 'one'}-handed, {len(ok)} weapons")
    print(f"{'dmg':>6}  {'weapon':<34} {'lean':<4} {'class':<12} VIG MND END STR DEX INT FTH ARC  AR / status")
    for r in ok:
        st = r["stats"]
        ar = " ".join(f"{k[:4]} {v}" for k, v in r["ar"].items())
        stt = " ".join(f"{k} {v}" for k, v in r["status"].items())
        print(f"{r['score']:6.1f}  {r['name'][:34]:<34} {r['lean'].upper():<4} {r['class']:<12} "
              + " ".join(f"{st[k]:>3}" for k in ("vig", "mnd", "vit", *DAMAGE_STATS)) + f"  {ar} | {stt}")
    for r in rows:
        if "error" in r:
            print(f"skipped {r['name']}: {r['error']}", file=sys.stderr)
    if a.json:
        a.json.write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
