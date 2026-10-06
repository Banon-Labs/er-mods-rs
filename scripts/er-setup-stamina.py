#!/usr/bin/env python3
"""Stamina of the paired loop: running R1 -> off-hand L1 -> (roll-catch) L1 -> running R1.

    python3 scripts/er-setup-stamina.py --right Halberd --left "Hand Axe"

Costs are `er-mechanics-exchange.stamina_total` (the AtkParam `stamina_cost` per hitbox event,
`VERIFIED` charge site) of the right weapon's one-handed `run_r1` and the left weapon's off-hand
L1 rows (`er-mechanics-combo.Model.offhand`), plus the run entry (`ENTRY_STAMINA`). The bar is the
exchange pool's (`Pool.bar`, the RL window's median), regeneration `STAMINA_REGEN_PER_S` while it
runs; this script charges every cost at once and credits no regeneration inside the loop, so
the loop count is a floor on what a full bar pays for (`INFERRED` bound). Written for
docs/er-mechanics/combo.md section 11a.
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = Path.home() / ".cache/er-build-planner"


def _sibling(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--right", default="Halberd")
    ap.add_argument("--left", default="Hand Axe")
    ap.add_argument("--rl", type=int, default=150)
    ap.add_argument("--window", type=int, default=10)
    a = ap.parse_args()
    exch, combo, atk = _sibling("er-mechanics-exchange"), _sibling("er-mechanics-combo"), _sibling("er-mechanics-attacks")
    reg = atk.Regulation(None)
    pool = exch.Pool(exch.opponent_pool(reg, CACHE / "builds.jsonl", a.rl - a.window, a.rl + a.window))
    model = combo.Model(mirror=None)
    names = {}
    for wid in combo.base_weapons(model.reg):
        names.setdefault(model.reg.weapon_names[wid], wid)
    rid, lid = names[a.right], names[a.left]
    run = next(x for x in atk.weapon_attacks(reg, rid, "one") if x["slot"] == "run_r1")
    run_cost = exch.stamina_total(reg, rid, run) + exch.ENTRY_STAMINA.get("run_r1", 0)
    rows = model.offhand(lid)
    l1 = rows["left_1"]
    l1_cost = exch.stamina_total(reg, lid, l1)
    l2 = rows.get("left_2")
    l2_cost = exch.stamina_total(reg, lid, l2) if l2 else l1_cost
    loop = run_cost + l1_cost + l2_cost
    print(f"bar (pool median) {pool.bar:.0f}, regen {exch.STAMINA_REGEN_PER_S:.0f}/s")
    print(f"{a.right} running R1 {run_cost} (entry {exch.ENTRY_STAMINA.get('run_r1', 0)} included), "
          f"{a.left} L1 #1 {l1_cost}, L1 #2 {l2_cost}")
    print(f"one loop (run R1 + L1 + catch L1) {loop}; without the catch {run_cost + l1_cost}")
    print(f"full loops from a full bar, no regeneration: {pool.bar / loop:.2f} "
          f"(without the catch {pool.bar / (run_cost + l1_cost):.2f}); "
          f"a roll after it needs {exch.ENTRY_STAMINA.get('roll_r1', 12)} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
