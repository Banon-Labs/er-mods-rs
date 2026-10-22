#!/usr/bin/env python3
"""Carriers per ash in a loadout's Classes section, on the unbounded and the bounded norm scale.

    python3 scripts/er-mechanics-class-ash-carriers.py <er-mechanics-combo-loadout.py arguments>
    python3 scripts/er-mechanics-class-ash-carriers.py --selftest

Runs `er-mechanics-combo-loadout.main` with those arguments up to the Classes section's
`spread_ashes` call, then spreads its candidates twice: with each copy's `norm` on the old scale
(each category's `NORM_RANK`th best ash, `unbounded_refs`) and on `category_refs`, the scale that
holds a category's best no further above it than the pooled ashes' best sits above their
`NORM_RANK`th. Prints both scales and the carriers per ash, then exits without writing a plan.
"""
import collections
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("er_mechanics_combo_loadout", HERE / "er-mechanics-combo-loadout.py")
CL = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = CL
spec.loader.exec_module(CL)


def unbounded_refs(rows):
    """`category_refs` before the bound: each category's `NORM_RANK`th best ash (best copy, above 0)."""
    per = {c: {} for c in CL.CATEGORIES}
    for x in rows:
        d = per.setdefault(x["cat"], {})
        d[x["ash"]] = max(d.get(x["ash"], 0.0), x["value"])
    out = {}
    for c, d in per.items():
        vals = sorted((v for v in d.values() if v > 0), reverse=True)
        out[c] = vals[min(CL.NORM_RANK, len(vals)) - 1] if vals else 1.0
    return out


def carriers(weapons, cands, per_weapon, cap, penalty, uncapped, scale):
    """Counter of carriers per ash with every candidate's `norm` = value / scale[its category]."""
    cs = {k: {**x, "norm": x["value"] / scale[x["cat"]]} for k, x in cands.items()}
    got = CL.spread_ashes(weapons, cs, per_weapon, cap, penalty, uncapped)
    return collections.Counter(x["ash"] for xs in got.values() for x in xs)


def run(argv):
    seen = {}
    bounded, spread = CL.category_refs, CL.spread_ashes

    def recording(rows):
        got = bounded(rows)
        seen.setdefault("scales", (unbounded_refs(rows), got))
        return got

    class Done(Exception):
        pass

    def counting(weapons, cands, per_weapon, cap, penalty, uncapped=frozenset()):
        CL.spread_ashes = spread
        old, new = seen["scales"]
        print("norm scale, unbounded -> bounded: " + ", ".join(f"{c} {old[c]:.1f} -> {new[c]:.1f}" for c in new))
        for label, scale in (("unbounded", old), ("bounded", new)):
            n = carriers(weapons, cands, per_weapon, cap, penalty, uncapped, scale)
            print(f"{label}: War Cry on {n['War Cry']} of {len(weapons)} class weapons; "
                  + ", ".join(f"{c} {a}" for a, c in n.most_common(8)))
        raise Done

    CL.category_refs, CL.spread_ashes = recording, counting
    sys.argv = ["er-mechanics-combo-loadout.py", *argv]
    try:
        CL.main()
    except Done:
        return 0
    print("the loadout finished without reaching the Classes section", file=sys.stderr)
    return 1


def selftest():
    rows = ([{"cat": "non-bullet", "ash": f"D{i}", "value": v} for i, v in enumerate(range(400, 100, -25))]
            + [{"cat": "utility", "ash": "War Cry" if i == 0 else f"U{i}", "value": v}
               for i, v in enumerate([226, 49, 45, 40, 38, 35, 33, 31, 30, 28, 14])])
    old, new = unbounded_refs(rows), CL.category_refs(rows)
    ws = [f"W{i}" for i in range(10)]
    cands = {}
    for w in ws:
        cands[w, f"A{w}"] = {"weapon": w, "ash": f"A{w}", "cat": "non-bullet", "value": old["non-bullet"]}
        cands[w, f"B{w}"] = {"weapon": w, "ash": f"B{w}", "cat": "non-bullet", "value": 0.9 * old["non-bullet"]}
        cands[w, "War Cry"] = {"weapon": w, "ash": "War Cry", "cat": "utility", "value": 200.0}
    got = [carriers(ws, cands, 2, CL.CLASS_ASH_CAP, CL.CLASS_ASH_PENALTY, frozenset(), s)["War Cry"]
           for s in (old, new)]
    ok = old["utility"] == 28 and got[0] > CL.CLASS_ASH_CAP and got[1] == CL.CLASS_ASH_CAP
    print(f"{'ok  ' if ok else 'FAIL'} War Cry carriers, unbounded vs bounded scale: {got} "
          f"(utility scale {old['utility']:.1f} -> {new['utility']:.1f}, cap {CL.CLASS_ASH_CAP})")
    print("selftest: " + ("ok" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(selftest() if sys.argv[1:] == ["--selftest"] else run(sys.argv[1:]))
