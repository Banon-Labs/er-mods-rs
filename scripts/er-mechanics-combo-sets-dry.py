#!/usr/bin/env python3
"""Draw a finished pipeline run's equip sets again, without the rest of the loadout.

    python3 scripts/er-mechanics-combo-sets-dry.py <prefix> [--roles <roles.json>] [--embed <embed.json>]
        [--exclude <bans.json>] [--affinity Heavy] [--mode before|after|after-nops ...] [--explain N]

`<prefix>` is a `er-mechanics-build-pipeline.sh` output prefix (`<dir>/<name>-<id>`), whose
`-roles.json`, `-embed.json`, `-planner-build.json`, `-weight-cost.json` and sweep files
(`-main.json`, `-inbuild.json`, `-fixed.json`, `-status.json`) it reads. It calls
`er-mechanics-combo-loadout.derive_combos` the way that script's `main` does -- the same weight
cost, speed term and affinity rule, both hands limited to role picks and build weapons with sweep
rows left after `--exclude` and the affinity rule -- and prints each mode's set lines. The ash-level
cuts `main` makes later (dominance, rank cut) are not applied; they drop ashes, not weapons.

Modes: `after` draws with the powerstance candidates (`PowerstanceOffs`) and the movement-family
off-hand weights (`er-mechanics-class-roles.offhand_weights`); `after-nops` without the
candidates; `before` with neither, the off-hand weights as written, for comparing against a roles
file the earlier scoring made. Writes nothing.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tomllib
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("prefix")
    ap.add_argument("--roles", type=Path, help="roles file to draw from (default <prefix>-roles.json)")
    ap.add_argument("--embed", type=Path, help="embedding to draw from (default <prefix>-embed.json)")
    ap.add_argument("--exclude", type=Path)
    ap.add_argument("--affinity", default="Heavy")
    ap.add_argument("--mode", action="append", choices=("before", "after", "after-nops"))
    ap.add_argument("--explain", type=int, default=0, metavar="N",
                    help="with `after`: per main hand, its powerstance records and its N best off hands")
    a = ap.parse_args()
    P = a.prefix
    CL = _load("er_mechanics_combo_loadout", "er-mechanics-combo-loadout.py")
    AR = _load("er_mechanics_ar", "er-mechanics-ar.py")
    cr = CL._class_roles()
    if a.exclude:
        ex = json.loads(a.exclude.read_text())
        CL.EXCLUDE.update(weapons=set(ex.get("weapons", ())), ashes=set(ex.get("ashes", ())),
                          ash_affinity={tuple(x) for x in ex.get("ash_affinity", ())},
                          rows=[tuple(r) for r in ex.get("rows", ())])
    rows = []
    for f in ("main", "inbuild", "fixed", "status"):
        p = Path(f"{P}-{f}.json")
        if p.exists():
            rows += json.loads(p.read_text())
    rows = [x for x in rows if not CL.excluded(x)]
    tables = AR.Tables(None)
    build = json.loads(Path(f"{P}-planner-build.json").read_text())
    stats = {k: build["stats"][k] for k in CL.STATS}
    prim, _ = CL.affinity_primaries(AR, tables, sorted({x["weapon"] for x in rows}), stats, 25)

    def infusable(w):
        try:
            tables.find_weapon(w, a.affinity)
            return True
        except (SystemExit, KeyError):
            return False
    rule = CL.AffinityRule(a.affinity, prim, infusable)
    row_ws = {x["weapon"] for x in rows if rule.keep(x)}
    emb = json.loads((a.embed or Path(f"{P}-embed.json")).read_text())
    cover = cr.PunishCoverage(emb)
    wc_path = Path(f"{P}-weight-cost.json")
    wc = CL.WeightCost(json.loads(wc_path.read_text())) if wc_path.exists() else None
    roles = json.loads((a.roles or Path(f"{P}-roles.json")).read_text())
    keep = {unicodedata.normalize("NFKD", s["name"]).encode("ascii", "ignore").decode()
            for s in build["inventory"]["slots"]}
    for r in roles["classes"].values():
        keep |= {r["scaling"], r["reach"], r.get("damage_top")} | {p["weapon"] for p in r["fun"]}
        keep |= {(r.get(h) or {}).get("weapon") for h in ("main_hand", "offhand")}
    off_w = tomllib.loads(cr.PREFS.read_text())["offhand_pick_weights"]

    def std_weight(w):
        return tables.weapons[tables.find_weapon(w, "Standard")]["weight"]
    for mode in a.mode or ["after"]:
        ps = CL.powerstance_offs(emb["powerstance"], tables, roles, cr) if mode == "after" else None
        spread = cr.offhand_weights
        if mode == "before":
            cr.offhand_weights = dict
        try:
            _, log = CL.derive_combos(roles, cr, roles.get("weights") or cr.pick_weights(CL.EXCLUDE["ashes"]), off_w,
                                      usable=lambda w: w in keep and w in row_ws, aff_ok=rule.ok_any,
                                      **({"weight": std_weight, "cost": wc} if wc else {}),
                                      speed=CL.SetSpeed(roles, cover, emb["powerstance"] if mode == "after" else ()),
                                      k=cr.punish_k(), ps=ps)
        finally:
            cr.offhand_weights = spread
        print(f"== {mode}" + (f" ({len(ps.pool)} powerstance candidates in the off-hand pool)" if ps else ""))
        for line in log:
            print("   " + line)
        if a.explain and ps:
            usable = lambda w: w in keep and w in row_ws
            mains, offs_for = CL.hand_scores(roles, cr, roles.get("weights"), off_w, usable, rule.ok_any, ps)
            for m in mains:
                recs = ps.by_right.get(m[2], ())
                print(f"   main {m[3]} {m[2]} ({m[1]}, {m[0]:.4f}): {len(recs)} powerstance records, "
                      f"{sum(usable(r[0]) for r in recs)} with a usable left hand; best off hands: "
                      + "; ".join(f"{'PS ' if o.ps else ''}{o[3]} {o[2]} {o[0]:.4f}" for o in offs_for(m)[:a.explain]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
