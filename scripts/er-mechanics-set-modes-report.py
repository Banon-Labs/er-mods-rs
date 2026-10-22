#!/usr/bin/env python3
"""Every mode one weapon class's main hands can be played in, side by side.

`er-mechanics-combo-loadout.py --mode-report` prints only each main hand's best set and carries no
powerstance records. This prints, for each main hand of one class, the best set of each mode: the
overall best, the best powerstance (from the embed's `powerstance` records), the best shield off
hand, the best off hand with no cross-hand link, and two-handed alone. Scores are
`combo-loadout.set_score`, so the same per-feature set value the build uses.

    er-mechanics-set-modes-report.py <roles.json> <embed.json> [class]   (default "Straight Sword")
"""
import collections
import importlib.util
import json
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    roles_path, embed_path = Path(sys.argv[1]), Path(sys.argv[2])
    want = sys.argv[3] if len(sys.argv) > 3 else "Straight Sword"
    M = load("er_mechanics_combo_loadout", "er-mechanics-combo-loadout.py")
    AR = load("er_mechanics_ar", "er-mechanics-ar.py")
    cr = M._class_roles()
    roles = json.loads(roles_path.read_text())
    prefs = tomllib.loads(M.PREFS.read_text())
    emb = json.loads(embed_path.read_text())
    tables = AR.Tables(None)
    ps = M.powerstance_offs(emb.get("powerstance") or (), tables, roles, cr)
    cache = json.loads(M.CROSS_HAND_CACHE.read_text()) if M.CROSS_HAND_CACHE.exists() else {}
    by = collections.defaultdict(dict)
    for k, v in cache.items():
        model, rl, r, l = k.split("|", 3)
        if model == M.CROSS_HAND_MODEL:
            by[rl][r, l] = v
    xs = by[max(by, key=lambda x: len(by[x]))] if by else {}

    def cross(r, l):
        return tuple(xs[r, l]) if xs.get((r, l)) else None

    mains, offs_for = M.hand_scores(roles, cr, roles.get("weights") or cr.pick_weights(M.EXCLUDE["ashes"]),
                                    prefs["offhand_pick_weights"], lambda w: True, lambda w, a: True,
                                    ps, cross, all_mains=True)

    def label(o):
        if o.alone:
            return "2H alone"
        if o.two_hand:
            return "2H paired"
        return f"+ {o[2]}" + (" (powerstance)" if o.ps else "")

    def show(t):
        return "-" if t is None else f"{t[0]:.3f} {label(t[1])}"

    if not any(m[1] == want for m in mains):
        print(f"no {want!r} main hand; {len(mains)} main hands, e.g. {mains[:3]}", file=sys.stderr)
    for m in mains:
        if m[1] != want:
            continue
        rows = sorted(((M.set_score(m, o), o) for o in offs_for(m)), key=lambda t: -t[0])
        pick = {
            "best": rows[0] if rows else None,
            "powerstance": next((t for t in rows if t[1].ps), None),
            "shield": next((t for t in rows if not t[1].ps and not t[1].alone and "Shield" in str(t[1][1])), None),
            "no link": next((t for t in rows if not t[1].ps and not t[1].alone and not t[1].two_hand
                             and not getattr(t[1], "cross", 0)), None),
            "2H": next((t for t in rows if t[1].alone), None),
        }
        print(f"{m[3]} {m[2]}: " + " | ".join(f"{k} {show(v)}" for k, v in pick.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
