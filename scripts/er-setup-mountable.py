#!/usr/bin/env python3
"""Which off-hand candidates can carry a given ash, by the game's own mount check.

    python3 scripts/er-setup-mountable.py --ash Cragblade

Candidates are the left weapons `er-builds-pvp.py --setup --setup-lefts all` scores: every base
weapon whose L1 is an off-hand attack (`er-mechanics-offhand.Scorer.candidates`'s rule). The
mount check is `er-mechanics-ashes.mountable_skills` (`can_mount`, `VERIFIED` 0x140d549d0, with
the affinities the ash itself allows), per affinity at max upgrade. Written for
docs/er-mechanics/combo.md section 11a.
"""

from __future__ import annotations

import argparse
import collections
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _sibling(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ash", default="Cragblade")
    ap.add_argument("--list", action="store_true", help="print every candidate with its mountable affinities")
    a = ap.parse_args()
    ash, combo, ar = _sibling("er-mechanics-ashes"), _sibling("er-mechanics-combo"), _sibling("er-mechanics-ar")
    t, tab, model = ash.AshTables(), ar.Tables(None), combo.Model(mirror=None)
    sid = t.find_arts(a.ash)
    cands, seen = [], set()
    for wid in combo.base_weapons(model.reg):
        name = model.reg.weapon_names[wid]
        if name in seen:
            continue
        seen.add(name)
        cat = model.reg.weapon[wid]["wepmotionCategory"]
        if cat in combo.LEFT_NO_ATTACK or cat in combo.psg().GUARD_LEFT_ONE_HAND:
            continue
        if model.offhand(wid).get("left_1"):
            cands.append((name, wid))
    yes, no = [], []
    by_type = collections.Counter()
    for name, wid in cands:
        affs = [ar.AFFINITIES[i] for i in range(len(ar.AFFINITIES)) if wid + i * 100 in tab.weapons and
                sid in ash.mountable_skills(t, wid, i, tab.max_level(tab.weapons[wid + i * 100]["reinforceTypeId"]))]
        (yes if affs else no).append((name, affs))
        if affs:
            by_type[model.reg.weapon[wid]["wepType"]] += 1
    print(f"{len(cands)} off-hand candidates; {a.ash} mountable on {len(yes)}, not on {len(no)}")
    print(f"mountable, by wepType: {sorted(by_type.items())}")
    print(f"not mountable (first 30): {[n for n, _ in no[:30]]}")
    if a.list:
        for name, affs in yes:
            print(f"  {name}: {', '.join(affs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
