#!/usr/bin/env python3
"""An example build around one status (bleed, frost or poison): the weapon, class and stat spread
that get the most out of it, and the four talismans that add the most to that weapon's R1 string.

    python3 scripts/er-mechanics-status-build.py bleed --sweep data/status-infusions-rl150.csv
    python3 scripts/er-mechanics-status-build.py bleed --sweep <csv> --json out.json

Weapons, two sources, one score (`er-mechanics-string-score`: the R1 string's PvP damage plus
its status HP, per second):

- the infused ones: the rank-1 rows of `er-mechanics-status-infusions.py`'s CSV for the infusion
  that carries the status (Blood for bleed, Cold for frost, Poison for poison);
- the fixed-affinity ones: every melee weapon with one affinity whose own rows carry the status
  (Rivers of Blood, Reduvia, ...), built here per class the same way.

Talismans: every talisman that touches attack is scored on the winning weapon at its spread as
the string's damage with that talisman's multipliers per link (`er-mechanics-talismans.multipliers`,
slot `r1_<n>`, the weapon's own AtkParam subcategories), and four are taken greedily, one per
accessory group. Two conditional ones are given a share instead of all or nothing:

- an exultation (Lord of Blood's for bleed, Kindred of Rot's for poison) is on for
  min(1, procs per second x its 20 s), procs per second being the string's build-up per second
  over the median PvP resistance (`er-mechanics-status.corpus_defender`);
- the successive-hit insignias reach a stage from the hits before each link: +8 per hit, -1 per
  0.5 s, thresholds `STAGE_THRESHOLDS` (`er-mechanics-talismans`; the +8 is the most common
  increment, `INFERRED` for any one weapon).

Damage-side only: Vigor/Endurance floors are the caller's, armor and the great rune are not chosen.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
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


CW = _load("er_mechanics_class_by_weapon", "er-mechanics-class-by-weapon.py")
SS, OPT = CW.SS, CW.OPT
TAL = _load("er_mechanics_talismans", "er-mechanics-talismans.py")
ST = SS.WE.ST
INFUSION_OF = {"bleed": "Blood", "frost": "Cold", "poison": "Poison"}
EXULTATION = {"bleed": "Lord of Blood's Exultation", "poison": "Kindred of Rot's Exultation"}
#: Successive-hit gauge (`er-mechanics-talismans.STAGE_THRESHOLDS`): points per hit and decay.
STAGE_GAIN, STAGE_DECAY_PER_S = 8, 2.0
FPS = SS.FPS
SCREEN_CLASS = "Heavy Knight"
#: A screened weapon within this share of the list's last score gets every class.
SCREEN_MARGIN = 0.97


def fixed_candidates(e, status: str) -> list[tuple[int, str]]:
    """(base id, affinity) of one-affinity melee weapons whose own rows carry `status`."""
    out = []
    for base_id in CW.weapon_ids(e):
        affs = OPT.affinities(e.art, base_id)
        if affs != ["Standard"]:
            continue
        try:
            ws = ST.weapon_status(e.st, e.art.names[base_id], "Standard", 0, None, False)
        except (KeyError, SystemExit):
            continue
        if any(s["status"] == status for s in ws["sources"]):
            out.append((base_id, "Standard"))
    return out


def best_class(e, base_id, aff, grip, rl, vig, end, classes=None):
    sc = SS.StringScorer(e, base_id, aff, grip)
    if not sc.links:
        return None
    best = None
    for cls in classes or CW.CLASSES:
        r = SS.build(sc, cls, rl, vig, end)
        if r and (best is None or r["score"] > best["score"]):
            best = dict(r, **{"class": cls, "weapon": e.art.names[base_id], "weapon_id": base_id,
                              "affinity": aff, "grip": grip})
    return best


def string_hits(sc, st):
    """Per link: (start frame, damage, physical type key) at stats `st`."""
    e = sc.e
    e.stats = {k: st[k] for k in OPT.DAMAGE_STATS}
    r, _, _ = e.ar(sc.name, sc.affinity, sc.grip)
    ar_by = {el: r["damage"].get(el, {}).get("total", 0.0) for el in SS.WE.ELEMENTS}
    out, t = [], 0.0
    for i, s in enumerate(sc.links):
        out.append((t + s["startup"], e.hit_damage(sc.base_id, ar_by, s["mv_by"], s["_row"]["atk_row"],
                                                   s["hits"])["pvp_damage_total"]))
        if i + 1 < len(sc.links):
            t += s.get("next_cancel") or s.get("roll_cancel") or 0
    return out


def stage_before(hits, i):
    """Successive-hit stage the i-th hit lands at: the gauge from the hits before it."""
    gauge, last = 0.0, None
    for t, _ in hits[:i]:
        if last is not None:
            gauge = max(0.0, gauge - STAGE_DECAY_PER_S * (t - last) / FPS)
        gauge += STAGE_GAIN
        last = t
    if last is not None:
        gauge = max(0.0, gauge - STAGE_DECAY_PER_S * (hits[i][0] - last) / FPS)
    return sum(1 for th in TAL.STAGE_THRESHOLDS if gauge >= th)


def exultation_share(status: str, build: dict, sc) -> float:
    """Share of the string inside the exultation's buff: procs per second x 20 s, capped at 1."""
    if status not in EXULTATION or not build["status"].get(status):
        return 0.0
    ws = ST.weapon_status(sc.e.st, sc.name, sc.affinity, sc.level, {k: build["stats"][k] for k in OPT.DAMAGE_STATS},
                          sc.two)
    per_string = sum(ST.use_buildup(sc.e.st, ws, s["_row"]).get(status, 0) for s in sc.links)
    d = ST.corpus_defender(ST.corpus_rows(rl=150))
    resist = d["resist"][next(x[7] for x in ST.STATUSES if x[0] == status)]
    return min(1.0, per_string / resist * FPS / sc.frames * 20.0) if resist else 0.0


#: Attacker HP for the talisman gates: just under full, so Ritual Sword Talisman (exactly full HP)
#: and Red-Feathered Branchsword (20% or less) count as off for the string, which is the state
#: of most of a fight.
HP_RATIO = 0.9999


def median_equip_weight(e, wid: int) -> float:
    """Equipped weight with this weapon: the corpus PvP builds' median burden of everything but
    their heaviest right-hand weapon (`er-builds-optimize.other_load`), plus this weapon. Blue
    Dancer Charm reads it."""
    if "rest" not in _CACHE:
        rows = OPT.corpus_rows(OPT.CACHE / "builds.jsonl", 140, 160)
        loads = sorted(r["load"]["rest"] for r in rows if r["pvp"] and r["load"])
        _CACHE["rest"] = loads[len(loads) // 2] if loads else 0.0
    return _CACHE["rest"] + e.art.weapons[wid]["weight"]


_CACHE: dict = {}


def talisman_value(t, names, sc, hits, active_share, ex_key):
    """String damage with `names` worn: per link, the physical product of the attack multipliers,
    the exultation's share blended in."""
    total = 0.0
    wid = sc.e.art.find_weapon(sc.name, sc.affinity)
    weight = median_equip_weight(sc.e, wid)
    for i, (_, dmg) in enumerate(hits):
        ctx = {"slot": f"r1_{i + 1}", "grip": "2h" if sc.two else "1h", "weapon_id": wid,
               "successive_stage": stage_before(hits, i), "equip_weight": weight, "hp_ratio": HP_RATIO}
        def mult(active):
            m = TAL.multipliers(t, names, dict(ctx, active=active))
            return m["power_rate"]["physical"] * m["damage_rate"]["physical"] * m["damage_correct"]["physical"]
        on, off = mult({ex_key} if ex_key else set()), mult(set())
        total += dmg * (active_share * on + (1 - active_share) * off)
    return total


def pick_talismans(status, build, sc, n=4):
    t = TAL.Talismans()
    hits = string_hits(sc, build["stats"])
    ex = EXULTATION.get(status)
    ex_key = ST.EXULTATIONS[ex][0] if ex else None
    share = exultation_share(status, build, sc)
    # Every talisman: `affects` names systems by field family, and Lord of Blood's Exultation,
    # Claw Talisman or the insignias are not under "attack" there.
    pool = [x.name for x in t]
    worn, groups, out = [], set(), []
    base = talisman_value(t, [], sc, hits, share, ex_key)
    for _ in range(n):
        cur = talisman_value(t, worn, sc, hits, share, ex_key)
        best = None
        for name in pool:
            tal = t.get(name)
            if name in worn or tal is None or tal.group in groups:
                continue
            v = talisman_value(t, worn + [name], sc, hits, share, ex_key)
            if best is None or v > best[1]:
                best = (name, v)
        if best is None or best[1] <= cur + 1e-9:
            break
        worn.append(best[0])
        groups.add(t.get(best[0]).group)
        out.append({"talisman": best[0], "gain": round(best[1] / cur - 1, 4)})
    total = talisman_value(t, worn, sc, hits, share, ex_key)
    return out, round(total / base - 1, 4) if base else 0.0, round(share, 3)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("status", choices=sorted(INFUSION_OF))
    ap.add_argument("--sweep", type=Path, help="er-mechanics-status-infusions.py CSV")
    ap.add_argument("--weapon", metavar="WEAPON:AFFINITY:GRIP",
                    help="talismans for this weapon at --stats instead of searching the sweep")
    ap.add_argument("--stats", metavar="vig=..,str=..", help="the full spread, with --weapon")
    ap.add_argument("--rl", type=int, default=150)
    ap.add_argument("--vig", type=int, default=56)
    ap.add_argument("--end", type=int, default=41)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()

    e = SS.embed()
    if a.weapon:
        name, aff, grip = a.weapon.split(":")
        wid = e.art.find_weapon(name, aff)
        sc = SS.StringScorer(e, wid - wid % 10000, aff, grip)
        stats = {k: int(v) for k, v in (p.split("=") for p in a.stats.split(","))}
        p = sc.parts(stats)
        build = {"stats": stats, "score": p["score"], "damage": p["damage"], "status": p["status"],
                 "frames": sc.frames}
        tals, gain, share = pick_talismans(a.status, build, sc)
        print(f"{a.weapon} at {a.stats}: {p['score']:.1f}/s, R1 string {p['damage']:.0f} damage + "
              f"{p['status']} in {sc.frames:g} f")
        print(f"  talismans (+{gain:.1%} string damage; exultation on {share:.0%} of the time): "
              + ", ".join(f"{t['talisman']} (+{t['gain']:.1%})" for t in tals))
        if a.json:
            a.json.write_text(json.dumps(dict(build, weapon=name, affinity=aff, grip=grip, talismans=tals,
                                              talisman_gain=gain, exultation_share=share), indent=1))
        return 0
    if a.sweep is None:
        ap.error("--sweep or --weapon is required")
    cands = []
    aff = INFUSION_OF[a.status]
    with a.sweep.open() as f:
        for r in csv.DictReader(f):
            if r["infusion"] == aff and r["best"] == "1" and int(r["rl"]) == a.rl:
                cands.append({"weapon": r["weapon"], "weapon_id": int(r["weapon_id"]), "affinity": aff,
                              "grip": r["grip"], "class": r["class"], "score": float(r["score"]),
                              "stats": {k: int(r[k]) for k in CW.STATS}})
    seen = set()
    cands = [c for c in cands if (c["weapon_id"], c["grip"]) not in seen and not seen.add((c["weapon_id"], c["grip"]))]
    # Fixed-affinity weapons are screened with one class (`SCREEN_CLASS`, the sole best on 285 of
    # 339 weapons in the class sweep) and only those that would make the list get every class.
    fixed = []
    for base_id, faff in fixed_candidates(e, a.status):
        for grip in ("1H", "2H"):
            b = best_class(e, base_id, faff, grip, a.rl, a.vig, a.end, [SCREEN_CLASS])
            if b:
                fixed.append(b)
    cut = sorted([c["score"] for c in cands], reverse=True)[:a.top][-1] if cands else 0.0
    for b in fixed:
        if b["score"] >= cut * SCREEN_MARGIN:
            b = best_class(e, b["weapon_id"], b["affinity"], b["grip"], a.rl, a.vig, a.end) or b
        cands.append(b)
    cands.sort(key=lambda c: -c["score"])
    print(f"top {a.top} {a.status} weapons by R1 string (damage + status) per second, RL {a.rl}, "
          f"VIG {a.vig}, END {a.end}:")
    for c in cands[:a.top]:
        s = c["stats"]
        print(f"  {c['score']:6.1f}/s  {c['weapon']} {c['affinity']} {c['grip']} ({c['class']}) "
              f"STR {s['str']} DEX {s['dex']} INT {s['int']} FTH {s['fth']} ARC {s['arc']} MND {s['mnd']}")
    top = cands[0]
    sc = SS.StringScorer(e, top["weapon_id"], top["affinity"], top["grip"])
    build = SS.build(sc, top["class"], a.rl, a.vig, a.end)
    tals, gain, share = pick_talismans(a.status, build, sc)
    out = dict(top, damage=build["damage"], status=build["status"], frames=build["frames"],
               talismans=tals, talisman_gain=gain, exultation_share=share)
    print(f"\nbuild: {top['weapon']} {top['affinity']} {top['grip']}, {top['class']}, "
          + ", ".join(f"{k.upper()} {build['stats'][k]}" for k in CW.STATS)
          + f"\n  R1 string: {build['damage']:.0f} damage + {build['status']} in {build['frames']:g} f")
    print(f"  talismans (+{gain:.1%} string damage; exultation on {share:.0%} of the time): "
          + ", ".join(f"{t['talisman']} (+{t['gain']:.1%})" for t in tals))
    if a.json:
        a.json.write_text(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
