#!/usr/bin/env python3
"""One weapon's greased and ungreased R1 chain against named ashes, as JSON on stdout.

    python3 scripts/er-mechanics-r1-vs-ash.py <build.json> Shamshir Heavy "Lion's Claw" "Savage Lion's Claw"

No damage model of its own: the R1 rows are `er-mechanics-ash-table.Table.r1_slots`, the ash and
its R1 string `Table.string` with `Table.gates` and `Table.hyperarmor`, the ash with grease
`er-mechanics-ash-stats.AshScorer` (grease on from-weapon melee hits times
`spEffectAtkPowerCorrectRate_byPoint`), and the grease is the DLC drawstring tier on the element
that maximises one R1 #1, as `er-mechanics-weapon-embed.py` picks it. Two-handed only.
"""
import copy
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("er_mechanics_ash_table", HERE / "er-mechanics-ash-table.py")
AT = importlib.util.module_from_spec(spec)
sys.modules["er_mechanics_ash_table"] = AT
spec.loader.exec_module(AT)
S, A, OPT = AT.S, AT.A, AT.OPT
GREASE_TIER = "dlc-drawstring"


def r1_window(slots, frames):
    """(damage, R1 count) of R1s pressed back to back from frame 0, each at the previous R1's
    cancel and the chain restarting at its last slot's roll cancel, counting hits landed by
    `frames`."""
    ch = [s for s in slots if s["slot"] in AT.R1_SLOTS]
    t, dmg, n = 0.0, 0.0, 0
    while True:
        s = ch[n % len(ch)]
        if t + s["hit"] > frames:
            return round(dmg, 1), n
        dmg += s["dmg"]
        n += 1
        t += s["r1"] if s["r1"] is not None else s["roll"]


def main() -> int:
    build, weapon, affinity, *ashes = sys.argv[1:]
    b = json.loads(Path(build).read_text())
    stats = {k: b["stats"][k] for k in ("str", "dex", "int", "fth", "arc")}
    armor = [s["name"] for part in b["protectors"].values() for s in part["slots"] if s.get("equipSet")]
    tals = [s["name"] for s in b["talismans"]["slots"] if s.get("equipSet")]
    tab = AT.Table(weapon, affinity, stats, armor, "Bull-Goat's Talisman" in tals)
    two = True
    ctx, _ = tab.contexts(two)
    flat = OPT.GREASES[GREASE_TIER]

    def r1s(ar_by):
        c = copy.copy(ctx)
        c.ar_by = ar_by
        return tab.r1_slots(two, c)

    base = r1s(dict(ctx.ar_by))
    mv_key = {"physical": "mv_phys", "magic": "mv_mag", "fire": "mv_fire", "lightning": "mv_light", "holy": "mv_holy"}

    def greased_slots(el):
        """R1 rows with the grease added after the motion value, times the row's
        `spEffectAtkPowerCorrectRate_byPoint` (docs/er-mechanics/grease.md section 0): each slot is
        scored with an AR raised so that AR x MV / 100 gains exactly flat x byPoint / 100."""
        out = []
        for s in base:
            r = s["attack"]
            bp = tab.t.atk_extra.get(r["atk_row"], {}).get("spEffectAtkPowerCorrectRate_byPoint", 100)
            mv = r[mv_key[el]] or 100
            sl = r1s(dict(ctx.ar_by, **{el: ctx.ar_by.get(el, 0.0) + flat * bp / mv}))
            out.append(next(x for x in sl if x["slot"] == s["slot"]))
        return out

    best = None
    for el in OPT.GREASE_ELEMENTS:
        sl = greased_slots(el)
        d = next(s["dmg"] for s in sl if s["slot"] == "r1_1")
        if best is None or d > best[0]:
            best = (d, el, sl)
    _, gel, greased = best

    def sa_rate(atk_row):
        fid = tab.t.atk_extra.get(atk_row, {}).get("finalDamageRateId", -1)
        fr = tab.t.final_rate.get(fid) if fid >= 0 else None
        return fr["saRate"] if fr else 1.0

    def chain(slots):
        return [{"slot": s["slot"], "dmg": round(s["dmg"], 1), "first_hit": s["hit"], "r1_cancel": s["r1"],
                 "roll_cancel": s["roll"],
                 "pvp_poise": round(s["attack"]["poise_damage"] * A.POISE_MENU * sa_rate(s["attack"]["atk_row"]), 1),
                 "ha": [h["frames"] for h in (s["attack"].get("hyperarmor") or [])],
                 "stamina": s["attack"]["stamina_cost"]} for s in slots]

    out = {"stats": stats, "armor": armor, "talismans": tals, "grease": OPT.GREASE_NAMES[GREASE_TIER][gel],
           "ar": {k: round(v) for k, v in ctx.ar_by.items() if v}, "defender": tab.defender,
           "r1": {"ungreased": chain(base), "greased": chain(greased)}, "ashes": {}}
    for name in ashes:
        sid = tab.t.find_arts(name)
        prof = A.skill_profile(tab.t, sid, tab.wid, tab.level)
        clip = tab.gates(sid, prof)
        e = {"sid": sid, "gates": clip["gates"] if clip else None, "length": clip["length"] if clip else None,
             "ha": tab.hyperarmor(sid), "fp": prof["fp_cost"]}
        for label, grease, slots in (("ungreased", None, base), ("greased", (gel, flat), greased)):
            hits = S.AshScorer(tab.lev, weapon, affinity, sid, two, tab.defender, grease).hits(stats)
            tot = A.pvp_damage(tab.t, tab.wid, hits, tab.defender, OPT.DEF)
            last = max(h["frame"] for h in hits)
            gate = clip["gates"]["r1"] if clip else None
            st = tab.string(max(gate, last), slots, tot, last) if gate is not None else None
            roll = clip["gates"]["roll"] if clip else None
            e[label] = {
                "dmg": round(tot, 1),
                "hits": [{k: (round(v, 1) if isinstance(v, float) else v) for k, v in h.items()
                          if k in ("kind", "from_weapon", "frame", "pvp_damage_total", "atk_row", "count")}
                         | {"attack": {k: round(v) for k, v in h["attack"].items() if v},
                            "pvp_poise": round((h.get("poise") or 0.0) * sa_rate(h["atk_row"]), 1),
                            "by_point": tab.t.atk_extra.get(h["atk_row"], {}).get(
                                "spEffectAtkPowerCorrectRate_byPoint")} for h in hits],
                "string": {"dps": round(st[0]), "r1s": st[1], "dmg": round(st[2]), "end": st[3]} if st else None,
                "dps_to_roll": round(tot / (roll / AT.FPS)) if roll else None,
                "r1s_in_roll_window": r1_window(slots, roll) if roll else None,
                "r1s_in_string_window": r1_window(slots, st[3]) if st else None,
            }
        e["hit_keys"] = sorted(hits[0])
        out["ashes"][name] = e
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
