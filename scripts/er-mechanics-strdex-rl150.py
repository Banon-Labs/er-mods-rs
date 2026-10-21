#!/usr/bin/env python3
"""The attack-rating-maximising Strength and Dexterity of every melee weapon at a fixed rune level,
with Vigor and Endurance held at set values, and a plot of where those spreads land.

    python3 scripts/er-mechanics-strdex-rl150.py --out-dir <dir>
    python3 scripts/er-mechanics-strdex-rl150.py --rl 150 --vig 56 --end 43 --two-handed
    python3 scripts/er-mechanics-strdex-rl150.py --class Prisoner --spend str,dex,int,fth,arc --objective damage

For each weapon `er-builds-optimize.py` counts as melee (`sweep_kind`), shields excepted
(`SHIELD_WEP_TYPES`), at its max upgrade, every
affinity a player can give it and every starting class are tried. The class's base stats are raised
to Vigor `--vig`, Endurance `--end` and every requirement of the weapon (two-handing's STR x1.5
included with `--two-handed`); every remaining point is spent on Strength and Dexterity only, by
the optimizer's greedy look-ahead and swap pass (`spend`), to maximise the AR total. Neither stat
goes past the weapon's top soft cap (`stat_caps`); points nothing wants are reported as `unspent`
and would go to Mind. The weapon's answer is the class and affinity with the highest AR.

`--class` limits the classes tried and `--spend` names the stats the points may go to.
`--objective damage` scores one motion-value-100 hit on the median defender of the corpus builds
within 10 levels (`er-builds-optimize.bracket_defender`) in place of the AR total, which
overvalues a split across elements.

Writes `strdex-rl<rl>.json` (one row per weapon) and `strdex-rl<rl>.png` (a 2D histogram of the
optimal STR/DEX with both marginals) into `--out-dir`.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import multiprocessing as mp
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name: str, fname: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HERE / fname)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


OPT = _load("er_builds_optimize", "er-builds-optimize.py")
AR, RES = OPT.AR, OPT.RES
SPEND_STATS = ("str", "dex")
#: Small, medium and greatshields (`er-mechanics-powerstance-guard.py` `SHIELD_TYPES`) are left
#: out; thrusting shields (90) are a weapon class and stay.
SHIELD_WEP_TYPES = {65, 67, 69}
_W: dict = {}


def _init(rl, vig, end, two, classes, spend, objective, defender):
    _W.update(tables=AR.Tables(None), model=RES.Model(), rl=rl, vig=vig, end=end, two=two,
              real=OPT._real_weapon_ids(), classes=classes, spend=spend, objective=objective,
              defender=defender)


def best_for(base_id: int) -> dict | None:
    t, model, rl, two = _W["tables"], _W["model"], _W["rl"], _W["two"]
    if OPT.sweep_kind(t, base_id, _W["real"]) is None or t.weapons[base_id]["wepType"] in SHIELD_WEP_TYPES:
        return None
    best = None
    for aff in OPT.affinities(t, base_id):
        try:
            level = AR.attack_rating(t, base_id, aff, 0, None, two)["max_level"]
        except (KeyError, ValueError, SystemExit):
            continue
        need = OPT.requirements(t, base_id, aff, level, two)
        scorer = OPT.Scorer(t, base_id, aff, level, two, _W["objective"], _W["defender"])
        for cls in _W["classes"]:
            cls_level, base = model.class_base(cls)
            st = {k: max(base[k], need.get(k, 0)) for k in OPT.STATS}
            st["vig"] = max(st["vig"], _W["vig"])
            st["end"] = max(st["end"], _W["end"])
            points = rl - (sum(st.values()) - OPT.LEVEL_OFFSET)
            if points < 0 or rl < cls_level:
                continue
            scorer.floor = dict(st)
            st, left = OPT.spend(st, points, scorer, _W["spend"])
            score = scorer.score(st)
            if best is None or score > best["ar"]:
                best = {"id": base_id, "weapon": t.names.get(base_id), "wep_type": t.weapons[base_id]["wepType"],
                        "affinity": aff, "class": cls, "level": level, "ar": round(score, 1),
                        "str": st["str"], "dex": st["dex"], "unspent": left, "stats": st,
                        "requirements": {k: need[k] for k in ("str", "dex", "int", "fth", "arc")}}
    return best


def plot(rows: list[dict], path: Path, title: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    s = [r["str"] for r in rows]
    d = [r["dex"] for r in rows]
    fig = plt.figure(figsize=(9, 9))
    grid = fig.add_gridspec(4, 4, hspace=0.05, wspace=0.05)
    main = fig.add_subplot(grid[1:, :3])
    top = fig.add_subplot(grid[0, :3], sharex=main)
    side = fig.add_subplot(grid[1:, 3], sharey=main)
    edges = range(0, 101, 5)
    h = main.hist2d(s, d, bins=[edges, edges], cmap="viridis", cmin=1)
    fig.colorbar(h[3], ax=side, label="weapons")
    main.set_xlabel("optimal STR")
    main.set_ylabel("optimal DEX")
    top.hist(s, bins=edges, color="tab:red")
    side.hist(d, bins=edges, orientation="horizontal", color="tab:blue")
    top.tick_params(labelbottom=False)
    side.tick_params(labelleft=False)
    top.set_title(title)
    fig.savefig(path, dpi=130, bbox_inches="tight")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--rl", type=int, default=150)
    ap.add_argument("--vig", type=int, default=56)
    ap.add_argument("--end", type=int, default=43)
    ap.add_argument("--two-handed", action="store_true")
    ap.add_argument("--class", dest="classes", action="append", choices=list(RES.CLASS_ROWS),
                    help="limit to these starting classes (repeatable; default every class)")
    ap.add_argument("--spend", default=",".join(SPEND_STATS), help="stats the free points may go to")
    ap.add_argument("--objective", choices=["ar", "damage"], default="ar")
    ap.add_argument("--out-dir", type=Path, required=True)
    a = ap.parse_args()
    classes = a.classes or list(RES.CLASS_ROWS)
    spend = tuple(a.spend.split(","))
    if set(spend) - set(OPT.DAMAGE_STATS):
        ap.error(f"--spend takes {','.join(OPT.DAMAGE_STATS)}")
    defender = None
    if a.objective == "damage":
        defender = OPT.bracket_defender(OPT.corpus_rows(OPT.CACHE / "builds.jsonl", a.rl - 10, a.rl + 10))

    tables = AR.Tables(None)
    ids = sorted(i for i in tables.weapons if i % 10000 == 0)
    with mp.get_context("fork").Pool(initializer=_init, initargs=(a.rl, a.vig, a.end, a.two_handed, classes, spend, a.objective, defender)) as pool:
        rows = [r for r in pool.map(best_for, ids, chunksize=4) if r]
    a.out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"strdex-rl{a.rl}{'-2h' if a.two_handed else ''}"
    if a.classes:
        stem += "-" + "-".join(c.lower().replace(" ", "") for c in a.classes)
    if spend != SPEND_STATS:
        stem += "-" + "".join(spend)
    if a.objective != "ar":
        stem += "-" + a.objective
    (a.out_dir / f"{stem}.json").write_text(json.dumps(rows, indent=1))
    hands = "two-handed" if a.two_handed else "one-handed"
    plot(rows, a.out_dir / f"{stem}.png",
         f"{a.objective}-optimal STR/DEX,{len(rows)} melee weapons, RL{a.rl}, VIG {a.vig}, END {a.end}, {hands}")
    print(f"{len(rows)} weapons -> {a.out_dir / stem}.json, .png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
