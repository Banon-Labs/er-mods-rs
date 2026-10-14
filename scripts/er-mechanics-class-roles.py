#!/usr/bin/env python3
"""Pick weapons per weapon class: best scaling, best reach/moveset, and the fun ones.

    python3 scripts/er-mechanics-class-roles.py <build.json> --embed <embed.json> --list-wieldable
    python3 scripts/er-mechanics-class-roles.py <build.json> --embed <embed.json> --out <roles.json>

`<build.json>` is the planner build document (`GET /inventories/<id>`); it only says which
weapons are the build's own (`in_build`, `order`). `<embed.json>` is
`er-mechanics-weapon-embed.py` output at the build's stat block, and every feature below is read
from its records rather than measured here (docs/er-mechanics/weapon-embedding.md). A weapon is
`wieldable` in a grip when some affinity's vector for that grip is (no damage type with negative
scaling, two-handing's STR x1.5 included); only wieldable weapons take the scaling and reach roles.
`--list-wieldable` prints them comma-separated, for `er-mechanics-ash-table.py --extra-weapons ...
--only-extra`.

Features per weapon, each the better of its wieldable right-hand grips (1H, 2H):

- `ar` (named for its `[class_pick_weights]` key): the vector's `damage`, one motion-value-100 hit
  against the fixed 60 VIG RL150 defender, best affinity and grip; a greasable row (Standard,
  Heavy, Keen, Quality) counts with its best DLC drawstring grease (`damage_greased`), which an
  elemental infusion such as Lightning cannot take;
- `r1_reach`, `r1_recovery`, `r1_chain_dps`, `stamina`: the vector summaries `r1_reach`,
  `r1_recovery`, `r1_chain_dps` and `stamina_per_r1`;
- `r1_startup`: R1 #1's first hit frame (its slot's `startup`), the soonest wieldable grip;
- `ash_value`: the best non-utility ash vector's `value` on that weapon at its best-damage
  affinity (a fixed-skill weapon's is its own skill);
- `crit`: `ar` times the critical multiplier (100 + `throwAtkRate`) / 100, so Misericorde
  competes in its class on ripostes the way Shamshir does on R1 startup (user, 2026-10-08);
- `parry_cost` (with `--parry`): the riposte HP per attack thrown that the corpus's parriers are
  expected to land (`er-mechanics-parry.py`), the cheaper wieldable grip; a cost, subtracted as
  weight x cost / the class's best `ar` rather than normalised (`PENALTIES`), so with the `ar`
  weight an HP of riposte taken counts as an HP of hit damage. The off hand's `l1_parry_cost` is
  the same over the L1 chain, against the class's best L1 #1 hit.
- `pb_cost` (with `--perfect-block`): the HP per attack thrown the corpus's perfect blockers are
  expected to take off (`er-mechanics-perfect-block.py`: chip only on the blocked hit, its
  damage-over-time status dropped, their punish unless a later hit or the chain's next attack
  lands first), at the carried affinity, the cheaper wieldable grip; a cost scored as
  `parry_cost` is. `l1_pb_cost` is the same over the L1 chain.

Each feature is normalised to the class's best wieldable weapon (lower-is-better ones as
best / value) and weighted by `[class_pick_weights]` in `data/ash-preferences.toml`. Roles:

1. scaling: the highest `ar` (damage).
2. reach: the highest weighted score with `ar` left out (weights renormalised).
3. fun: per mechanic whose game-wide share is at most the embedding's `fun_max_share` (a rare
   status, a rare scaling route, a skill with a damaging hit few options can carry), the class's
   wieldable vector carrying it that does the most (weapon mechanics by `damage`, skills by ash
   `value`), rarest first. A class may have none or several. A fun pick only has to be wieldable.

4. main_hand and offhand: every wieldable weapon is scored twice, once per hand, and each hand
   over every affinity that carries a status (Blood bleed, Cold frost, Poison poison, and an
   innate status such as scarlet rot, sleep or madness on whichever affinity keeps it) plus the
   weapon's best statusless affinity by hit damage (user, 2026-10-08: "score each weapon twice,
   once as a main hand weapon with every status as well as 2H...and so on. Not just axes, and not
   just bleed"). Main hand takes the features above from that affinity's 1H and 2H records, the
   better grip each, status HP per hit inside `ar` as `hit_damage` counts it. Off hand takes
   the left-hand L1 chain (`OFFHAND_FEATURES`, weighted by `[offhand_pick_weights]`): L1 #1's
   first hit frame, recovery, reach, stamina, and damage plus status HP per second over the
   chain; `l1_status` (mean status HP per L1 hit) is reported, and its weight is 0 by default
   because the chain's HP per second already counts it. The
   records are the embedding's own: it carries every affinity a weapon can take, and the R1
   and L1 frames do not change with the affinity, only the attack rating and the build-up do.
   Normalisation is over the class's (weapon, affinity) candidates for that hand. Each class
   gets `main_hand` and `offhand` as {weapon, affinity, score}; each weapon gets the same two
   keys with `by_affinity` scores.

Near-twins are not merged: Shamshir and Scimitar each keep whatever role they win. When a class's
scaling and reach picks are near-twins (`moveset_distance`: right-hand slot sets
`TWIN_SLOT_SHARE` alike, slot features within `TWIN_MAX_DIFF`), `twins.near` says so, and
`er-mechanics-combo-loadout.py --collapse-twins` gives the class one row. A weapon can
hold two roles. Writes {"weapons": {name: {...}}, "classes": {class: {...}}} and prints each
class's top 3 by weighted score with every feature, for auditing the weights.
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import sys
import tomllib
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
PREFS = HERE.parent / "data" / "ash-preferences.toml"
FEATURES = ("r1_recovery", "r1_reach", "r1_startup", "ar", "r1_chain_dps", "ash_value", "stamina", "crit",
            "parry_cost", "pb_cost")
#: Off-hand role features, read off the L1 record's left-hand chain.
OFFHAND_FEATURES = ("l1_startup", "l1_recovery", "l1_reach", "l1_chain_dps", "l1_stamina", "l1_status",
                    "l1_parry_cost", "l1_pb_cost")
LOWER_BETTER = {"r1_recovery", "r1_startup", "stamina", "l1_startup", "l1_recovery", "l1_stamina"}
#: Costs in HP per attack, subtracted rather than normalised: weight x cost / the pool's best value
#: of the paired HP feature, so with the paired feature's weight an HP of cost counts as an HP of
#: damage lost (`er-mechanics-parry.py` `weapons`: the riposte the corpus's parriers are expected
#: to land per attack I throw; user, 2026-10-10: "Model parrying"; `er-mechanics-perfect-block.py`
#: `weapons`: what the corpus's perfect blockers take off per attack, user 2026-10-10). The weight
#: sits outside the normalising total, so a pool with no costs scores as before.
PENALTIES = {"parry_cost": "ar", "l1_parry_cost": "l1_hit", "pb_cost": "ar", "l1_pb_cost": "l1_hit"}
LEFT_CHAIN = ("left_1", "left_2", "left_3", "left_4", "left_5", "left_6")
#: Scaling pick: a weapon at most `SCALING_TIE` below the class's best hit damage that reaches
#: `SCALING_REACH_GAIN` farther on R1 #1 is picked instead (user, 2026-10-08: 0.8% less damage
#: for 18% more reach, "maybe 5% further is enough"; then 2.5%, after Wakizashi's 2.6% more R1
#: reach than Bloodstained Dagger fell short: "We can change our rule from 5% to 2.5% minimum
#: then"). The main-hand and off-hand picks apply the same rule to their score and their own
#: reach (`hand_pick`).
SCALING_TIE = 0.02
SCALING_REACH_GAIN = 0.025
#: Weapons with a true combo (measured R1/R2 links from `--combos`, or listed in `TRUE_COMBOS`)
#: get this added to every pick score -- more than the spread of most classes, so a combo weapon
#: wins its class's moveset pick (user, 2026-10-08: "any known true combos should add a massive
#: weight to a weapons choseability").
TRUE_COMBO_BONUS = 0.25
TRUE_COMBOS = HERE.parent / "data" / "true-combos.toml"
RIGHT_GRIPS = ("1H", "2H")


def ascii_name(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def best(xs, lower=False):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    return min(xs) if lower else max(xs)


#: Slot features compared for near-twins: timing, cost, motion value and reach (damage and status
#: are left out; they follow the affinity, not the moveset).
TWIN_FEATURES = ("anim", "startup", "active", "last_hit", "roll_cancel", "next_cancel", "stamina",
                 "mv", "hits", "reach")
#: Near-twins: the two weapons' right-hand slot sets agree on at least this share, and the shared
#: slots' features differ by at most this mean relative difference (Shamshir vs Scimitar: same
#: slots, 0.4%, reach alone differing by 3.6 vs 3.45 m on R1 #1).
TWIN_SLOT_SHARE = 0.9
TWIN_MAX_DIFF = 0.05


def hit_damage(r):
    """A vector's one motion-value-100 hit, greased when its row takes a grease (the build carries
    the DLC drawstring greases, and an elemental or status infusion cannot take one)."""
    # Plus the HP its own status is worth per hit (`status_hp`, the linear proc model), averaged
    # over every attack slot -- R1s, R2s, running, rolling, jumping: Spiked Caestus did 4 less
    # than Caestus and lost the class with bleed counted nowhere (user, 2026-10-08: "barely less
    # damage/scaling but apply bleed and are otherwise identical").
    per_hit = [sum(s["status_hp"].values()) / s["hits"]
               for s in (r.get("slots") or {}).values() if s.get("status_hp") and s.get("hits")]
    status = sum(per_hit) / len(per_hit) if per_hit else 0.0
    return max(r.get("damage") or 0.0, r.get("damage_greased") or 0.0) + status


def pb_grip_cost(pb, affinity, grip):
    """The `er-mechanics-perfect-block.py` cost of one weapon at `affinity` and `grip`, falling back
    to its best-damage affinity's; None without one."""
    c = ((pb or {}).get("by_affinity", {}).get(affinity) or {}).get(grip) or (pb or {}).get(grip)
    return c.get("cost") if c else None


def right_features(ok, av, parry=None, pb=None):
    """The main-hand features of one weapon's wieldable right-hand records `ok` (any affinities;
    the frame features do not depend on it), `av` its ash value, `parry` its
    `er-mechanics-parry.py` costs per grip ({'1H': {'cost': ...}, ...}) or None, `pb` its
    `er-mechanics-perfect-block.py` entry or None."""
    top = max(ok, key=hit_damage, default=None)
    per_grip = {}
    for r in ok:
        per_grip.setdefault(r["grip"], r["summary"])
    costs = [((parry or {}).get(g) or {}).get("cost") for g in per_grip]
    pbs = [pb_grip_cost(pb, top["affinity"], g) for g in per_grip] if top else []
    return top, {
        # What the corpus's perfect blockers take off per attack at the carried affinity, the
        # cheapest wieldable grip, as `parry_cost`.
        "pb_cost": best((c for c in pbs if c is not None), lower=True),
        # The riposte HP per attack the corpus's parriers land, the cheapest wieldable grip
        # (every other feature also takes its better grip).
        "parry_cost": best((c for c in costs if c is not None), lower=True),
        "ar": hit_damage(top) if top else None,
        "r1_reach": best(s.get("r1_reach") for s in per_grip.values()),
        "r1_recovery": best((s.get("r1_recovery") for s in per_grip.values()), lower=True),
        # Frame R1 #1 first hits (its slot's `startup`), the grip that lands soonest.
        "r1_startup": best(((r["slots"].get("r1_1") or {}).get("startup") for r in ok), lower=True),
        "r1_chain_dps": best(s.get("r1_chain_dps") for s in per_grip.values()),
        "stamina": best((s.get("stamina_per_r1") for s in per_grip.values()), lower=True),
        "ash_value": round(av) if av else None,
        # Riposte and backstab damage: hit damage times the weapon's critical multiplier
        # (`throwAtkRate`, Misericorde +40, Executioner's Greataxe +15).
        # Only for a weapon whose critical multiplier is above the normal one; for the rest it
        # would only repeat `ar`, so `score` gives its weight to `r1_reach` instead (user,
        # 2026-10-08: "Crit should only be used if the crit value is above the normal value,
        # otherwise range should fill the gap").
        "crit": (round(hit_damage(top) * (100 + top["crit"]) / 100, 1)
                 if top and top.get("crit", 0) > 0 else None),
    }


def offhand_features(rec, parry=None, pb=None):
    """The off-hand features of one wieldable L1 record, or None when it has no L1 chain. The
    chain is timed as `er-mechanics-weapon-embed.chain_summary` times it (each next press at the
    previous step's next-attack cancel), with each step's status HP added to its damage.
    `l1_hit` (L1 #1's damage plus status HP) is the scale of `l1_parry_cost`, the L1 chain's
    parry cost from `parry` (`er-mechanics-parry.py`, per grip), and of `l1_pb_cost`, its
    perfect-block cost from `pb` (`er-mechanics-perfect-block.py`)."""
    chain = [rec["slots"][k] for k in LEFT_CHAIN if k in rec["slots"]]
    if not chain or chain[0].get("startup") is None:
        return None
    t, end, hp = 0.0, 0.0, 0.0
    for i, s in enumerate(chain):
        end = t + s["startup"]
        hp += (s.get("damage") or 0.0) + sum((s.get("status_hp") or {}).values())
        if i + 1 < len(chain):
            t += s.get("next_cancel") or 0
    hits = sum(s.get("hits") or 1 for s in chain)
    sm = rec["summary"]
    return {"l1_startup": chain[0]["startup"], "l1_recovery": sm.get("recovery"),
            "l1_reach": sm.get("first_reach"),
            "l1_chain_dps": round(hp / (end / 30.0), 1) if len(chain) > 1 and end > 0 else None,
            "l1_stamina": sm.get("stamina"),
            "l1_status": round(sum(sum((s.get("status_hp") or {}).values()) for s in chain) / hits, 1),
            "l1_hit": round((chain[0].get("damage") or 0.0) + sum((chain[0].get("status_hp") or {}).values()), 1),
            "l1_parry_cost": ((parry or {}).get("L1") or {}).get("cost"),
            "l1_pb_cost": pb_grip_cost(pb, rec["affinity"], "L1")}


def norm_score(feats, pool, use, weights):
    """Weighted score of `feats` with each feature normalised to the best of `pool` (lower-is-better
    ones as best / value), less each `PENALTIES` cost. A weapon with no crit bonus has the crit
    weight score reach instead."""
    tot = sum(weights.get(f, 0.0) for f in use if f not in PENALTIES)
    s = 0.0
    for f in use:
        if f in PENALTIES:
            x = feats.get(f)
            scale = [y for y in (p.get(PENALTIES[f]) for p in pool) if y]
            if x and scale:
                s -= weights.get(f, 0.0) * x / max(scale)
            continue
        g = "r1_reach" if f == "crit" and feats.get("crit") is None else f
        x = feats.get(g)
        vals = [y for y in (p.get(g) for p in pool) if y]
        if x and vals:
            s += weights.get(f, 0.0) * ((min(vals) / x) if g in LOWER_BETTER else (x / max(vals)))
    return s / tot if tot else 0.0


def role_affinities(rs):
    """The affinities a weapon is scored on per hand: each one with a status, and the best
    statusless one by right-hand hit damage (the build's physical or elemental default)."""
    by = collections.defaultdict(list)
    for r in rs:
        by[r["affinity"]].append(r)
    status = [aff for aff, xs in by.items() if any(r["status"] for r in xs)]
    plain = [(max((hit_damage(r) for r in xs if r["grip"] in RIGHT_GRIPS and r["wieldable"]), default=-1), aff)
             for aff, xs in by.items() if aff not in status]
    plain = [p for p in plain if p[0] >= 0]
    return status + ([max(plain)[1]] if plain else []), by


def moveset_distance(ra, rb):
    """(share of slots both have, mean relative feature difference over them) between two weapons'
    right-hand vectors, {grip: record} each; the slots are the embedding's per-slot features."""
    both = union = 0
    diffs = []
    for g in RIGHT_GRIPS:
        sa, sb = (ra.get(g) or {}).get("slots", {}), (rb.get(g) or {}).get("slots", {})
        both += len(sa.keys() & sb.keys())
        union += len(sa.keys() | sb.keys())
        for k in sa.keys() & sb.keys():
            for f in TWIN_FEATURES:
                x, y = sa[k].get(f), sb[k].get(f)
                if x is None and y is None:
                    continue
                if isinstance(x, str) or isinstance(y, str) or x is None or y is None:
                    diffs.append(0.0 if x == y else 1.0)
                else:
                    diffs.append(abs(x - y) / max(abs(x), abs(y), 1.0))
    return (both / union if union else 0.0), (sum(diffs) / len(diffs) if diffs else 1.0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("build", type=Path)
    ap.add_argument("--embed", type=Path, required=True)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--list-wieldable", action="store_true")
    ap.add_argument("--combos", type=Path,
                    help="JSONL of measured true combos ({weapon, grip, from, to, gap, escape} per line)")
    ap.add_argument("--parry", type=Path,
                    help="er-mechanics-parry.py --weapons-from output: each weapon's parry cost per grip, "
                         "scored as `parry_cost` / `l1_parry_cost`")
    ap.add_argument("--perfect-block", type=Path,
                    help="er-mechanics-perfect-block.py --weapons-from output: each weapon's perfect-block "
                         "cost per affinity and grip, scored as `pb_cost` / `l1_pb_cost`")
    a = ap.parse_args()
    parry = json.loads(a.parry.read_text()).get("weapons", {}) if a.parry else {}
    pbw = json.loads(a.perfect_block.read_text()).get("weapons", {}) if a.perfect_block else {}
    weights = tomllib.loads(PREFS.read_text())["class_pick_weights"]
    emb = json.loads(a.embed.read_text())
    # A reach override only goes to a weapon whose strongest scaling is one of the build's main
    # stats (at least half its highest): a same-moveset sibling built for other stats is not a
    # longer copy of the winner (user, 2026-10-09, Wakizashi over Bloodstained Dagger on STR 80:
    # "Its not really a fun weapon since its just the sibling to bloodstained for other stats").
    spec = importlib.util.spec_from_file_location("er_mechanics_ar", HERE / "er-mechanics-ar.py")
    AR = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(AR)
    ar_tables = AR.Tables(None)
    top_stat = max(emb["stats"].values())
    primary = {k for k, v in emb["stats"].items() if v >= top_stat / 2}
    correct = {"str": "correctStrength", "dex": "correctAgility", "int": "correctMagic",
               "fth": "correctFaith", "arc": "correctLuck"}

    def fits_stats(w, aff="Standard"):
        try:
            row = ar_tables.weapons[ar_tables.find_weapon(w, aff)]
        except (KeyError, SystemExit):
            return True
        best = max(row.get(c, 0) for c in correct.values())
        return best > 0 and any(row.get(correct[k], 0) == best for k in primary)
    max_share = emb["fun_max_share"]
    mech = emb["mechanics"]

    order = []
    for s in json.loads(a.build.read_text())["inventory"]["slots"]:
        w = ascii_name(s["name"])
        if w not in order:
            order.append(w)

    recs = collections.defaultdict(list)
    for r in emb["weapons"]:
        recs[r["weapon"]].append(r)
    ashes = collections.defaultdict(list)
    for x in emb["ashes"]:
        ashes[x["weapon"]].append(x)

    weapons = {}
    for w, rs in recs.items():
        right = [r for r in rs if r["grip"] in RIGHT_GRIPS]
        ok = [r for r in right if r["wieldable"]]
        grips = sorted({r["grip"] for r in ok})
        top = max(ok, key=lambda r: hit_damage(r), default=None)
        # The ash value at the affinity the weapon is carried on (its best-damage one), so a weapon
        # some sweep also covered on another affinity is not scored on the better of the two.
        own = [x for x in ashes.get(w, []) if x["cat"] != "utility"]
        av = best(x["value"] for x in ([x for x in own if top and x["affinity"] == top["affinity"]] or own))
        top, feats = right_features(ok, av, parry.get(w), pbw.get(w))
        # Per hand, per role affinity (module docstring, role 4). An affinity the ash sweep did
        # not cover is scored with the carried affinity's ash value.
        affs, by_aff = role_affinities(rs)
        main_hand, offhand = {}, {}
        for aff in affs:
            mine = [r for r in by_aff[aff] if r["grip"] in RIGHT_GRIPS and r["wieldable"]]
            if mine:
                aav = best(x["value"] for x in own if x["affinity"] == aff) or av
                main_hand[aff] = {"features": right_features(mine, aav, parry.get(w), pbw.get(w))[1]}
            left = next((r for r in by_aff[aff] if r["grip"] == "L1" and r["wieldable"]), None)
            of = offhand_features(left, parry.get(w), pbw.get(w)) if left else None
            if of:
                offhand[aff] = {"features": of}
        weapons[w] = {"order": order.index(w) if w in order else None, "in_build": w in order,
                      "class": rs[0]["class"], "fixed_skill": rs[0]["fixed_skill"],
                      "wieldable": bool(ok), "grips": grips,
                      "affinity": top["affinity"] if top else None, "grip": top["grip"] if top else None,
                      "grease": top.get("grease") if top and top.get("damage_greased") else None,
                      "moveset": rs[0]["moveset"], "features": feats,
                      "parry": {g: {k: v[k] for k in ("cost", "catch", "exposure")}
                                for g, v in (parry.get(w) or {}).items() if v},
                      "perfect_block": {g: {k: v[k] for k in ("cost", "p", "stopped", "affinity")}
                                        for g, v in (pbw.get(w) or {}).items() if g != "by_affinity" and v},
                      "main_hand": {"by_affinity": main_hand}, "offhand": {"by_affinity": offhand},
                      # Fists and claws have no crouch attack (Bloodhound's Step placement).
                      "crouch": any(r["slots"].get("crouch_r1") for r in ok),
                      "fun": max([r["fun"] for r in ok] + [x["fun"] for x in ashes.get(w, [])], default=0.0)}

    combos = collections.defaultdict(list)
    for c in tomllib.loads(TRUE_COMBOS.read_text()).get("combo", []):
        combos[c["weapon"]].append(c["string"])
    if a.combos:
        for line in a.combos.read_text().splitlines():
            if line.strip():
                c = json.loads(line)
                combos[c["weapon"]].append(f"{c['from']} -> {c['to']} ({c['gap']}/{c['escape']})")
    for w, v in weapons.items():
        v["true_combos"] = combos.get(w, [])

    if a.list_wieldable:
        print(",".join(w for w, v in weapons.items() if v["wieldable"]))
        return 0
    if not a.out:
        ap.error("--out is required unless --list-wieldable")

    def score(c_ws, w, use):
        return (norm_score(weapons[w]["features"], [weapons[o]["features"] for o in c_ws], use, weights)
                + (TRUE_COMBO_BONUS if weapons[w]["true_combos"] else 0.0))

    off_weights = tomllib.loads(PREFS.read_text())["offhand_pick_weights"]

    def hand_pick(ws, hand):
        """Score every (weapon, role affinity) of `ws` for one hand against the others and return
        the best as {weapon, affinity, score}. A true combo is an R1/R2 link, so it counts on the
        main hand only."""
        cands = [(w, aff, v["features"]) for w in ws for aff, v in weapons[w][hand]["by_affinity"].items()]
        pool = [f for _, _, f in cands]
        use, wts = (FEATURES, weights) if hand == "main_hand" else (OFFHAND_FEATURES, off_weights)
        out = None
        for w, aff, f in cands:
            s = norm_score(f, pool, use, wts)
            if hand == "main_hand" and weapons[w]["true_combos"]:
                s += TRUE_COMBO_BONUS
            s = round(s, 3)
            weapons[w][hand]["by_affinity"][aff]["score"] = s
            if s > weapons[w][hand].get("score", -1):
                weapons[w][hand].update(affinity=aff, score=s)
            if out is None or s > out["score"]:
                out = {"weapon": w, "affinity": aff, "score": s}
        # Within `SCALING_TIE` of the best score, `SCALING_REACH_GAIN` more reach wins (the
        # scaling pick's rule, on this hand's own reach): the longest such candidate.
        if out is None:                 # no candidate in this hand (a class the stats cannot wield)
            return None
        reach = "r1_reach" if hand == "main_hand" else "l1_reach"
        top = weapons[out["weapon"]][hand]["by_affinity"][out["affinity"]]["features"].get(reach)
        if top:
            longer = [(w, aff, f) for w, aff, f in cands
                      if weapons[w][hand]["by_affinity"][aff]["score"] >= out["score"] * (1 - SCALING_TIE)
                      and (f.get(reach) or 0) >= top * (1 + SCALING_REACH_GAIN)
                      and fits_stats(w)]  # native scaling: Heavy turns every weapon to STR
            if longer:
                w, aff, f = max(longer, key=lambda c: (c[2][reach], weapons[c[0]][hand]["by_affinity"][c[1]]["score"]))
                out = {"weapon": w, "affinity": aff, "score": weapons[w][hand]["by_affinity"][aff]["score"],
                       "reach_over": out["weapon"]}
        return out

    # Fun candidates: (class, mechanic) -> best carrier.
    fun = {}

    def offer(cls, m, cand, strength):
        if m is None or mech.get(m, {}).get("share", 1.0) > max_share:
            return
        k = (cls, m)
        if k not in fun or strength > fun[k][1]:
            fun[k] = (cand, strength)

    for w, rs in recs.items():
        for r in rs:
            if r["grip"] not in RIGHT_GRIPS or not r["wieldable"]:
                continue
            for m in r["mechanics"]:
                offer(r["class"], m, {"weapon": w, "affinity": r["affinity"], "grip": r["grip"], "ash": None},
                      hit_damage(r))
    for w, xs in ashes.items():
        if w not in weapons:
            continue
        for x in xs:
            # Ash values are ungreased, so a greasable affinity's copy is undervalued against an
            # elemental one; a skill's carrier is taken on the weapon's best-damage affinity (with
            # its grease), and other affinities only stand in when that one was not swept.
            if any(y["affinity"] == weapons[w]["affinity"] for y in xs) and x["affinity"] != weapons[w]["affinity"]:
                continue
            offer(weapons[w]["class"], x.get("mechanic"),
                  {"weapon": w, "affinity": x["affinity"], "grip": x["grip"], "ash": x["ash"]}, x["value"])

    classes, by_class = {}, collections.defaultdict(list)
    for w, v in weapons.items():
        by_class[v["class"]].append(w)
    for c, ws in sorted(by_class.items()):
        ok = [w for w in ws if weapons[w]["wieldable"]]
        for w in ok:
            weapons[w]["score"] = round(score(ok, w, FEATURES), 3)
            weapons[w]["score_no_ar"] = round(score(ok, w, [f for f in FEATURES if f != "ar"]), 3)
        # The scaling pick is the highest hit damage, unless a weapon within `SCALING_TIE` of that
        # damage reaches `SCALING_REACH_GAIN` farther on R1 #1; then the longest such weapon. User,
        # 2026-10-08: Nightrider Glaive does 0.8% less than Gargoyle's Halberd at STR 80 but
        # reaches 18% farther, "that's the weapon that should be chosen".
        top = max(ok, key=lambda w: weapons[w]["features"]["ar"] or 0, default=None)
        scaling = top
        if top:
            f = weapons[top]["features"]
            longer = [w for w in ok if (weapons[w]["features"]["ar"] or 0) >= (f["ar"] or 0) * (1 - SCALING_TIE)
                      and f["r1_reach"]  # whips have no measured reach; no gain to compare
                      and fits_stats(w)
                      and (weapons[w]["features"]["r1_reach"] or 0) >= f["r1_reach"] * (1 + SCALING_REACH_GAIN)]
            scaling = max(longer, key=lambda w: weapons[w]["features"]["r1_reach"], default=top)
        reach = max(ok, key=lambda w: weapons[w]["score_no_ar"], default=None)
        picks = sorted(((m, cand, s) for (cc, m), (cand, s) in fun.items() if cc == c),
                       key=lambda p: (mech[p[0]]["share"], -p[2]))
        twins = None
        if scaling and reach and scaling != reach:
            share, diff = moveset_distance(*({g: next(r for r in recs[w] if r["grip"] == g) for g in RIGHT_GRIPS}
                                             for w in (scaling, reach)))
            twins = {"slot_share": round(share, 3), "feature_diff": round(diff, 4),
                     "near": share >= TWIN_SLOT_SHARE and diff <= TWIN_MAX_DIFF}
        classes[c] = {"scaling": scaling, "scaling_affinity": weapons[scaling]["affinity"] if scaling else None,
                      "reach": reach, "reach_affinity": weapons[reach]["affinity"] if reach else None,
                      "twins": twins,
                      "main_hand": hand_pick(ok, "main_hand"), "offhand": hand_pick(ok, "offhand"),
                      # The top-damage weapon the reach rule passed over: one copy, not a row
                      # (user, 2026-10-08: "We can have 1 of the was, but a row of the now").
                      "damage_top": top if top != scaling else None,
                      "fun": [{**cand, "mechanic": m, "label": mech[m]["label"],
                               "share": mech[m]["share"], "rarity": round(1 - mech[m]["share"], 4)}
                              for m, cand, _ in picks],
                      "top": sorted(ok, key=lambda w: -weapons[w]["score"])[:3]}
    # The same weighted score normalised over every wieldable weapon instead of one class, ash left
    # out: how good the moveset is against the whole arsenal. Bloodhound's Step goes on the best of
    # these that has a crouch attack (user, 2026-10-08: "If a weapon has a crouch attack, and its
    # the best moveset by far, then I think bloodhound step is maximally ideal on it").
    every = [w for w, v in weapons.items() if v["wieldable"]]
    for w in every:
        weapons[w]["moveset_score"] = round(score(every, w, [f for f in FEATURES if f != "ash_value"]), 3)
    a.out.write_text(json.dumps({"weights": weights, "stats": emb["stats"], "fun_max_share": max_share,
                                 "weapons": weapons, "classes": classes}, indent=1))
    for c, v in classes.items():
        tw = v["twins"]
        print(f"== {c}: scaling {v['scaling_affinity']} {v['scaling']}, reach/moveset {v['reach_affinity']} "
              f"{v['reach']}" + (f" (slots shared {tw['slot_share']}, feature diff {tw['feature_diff']}"
                                 f"{', near-twins' if tw['near'] else ''})" if tw else ""))
        for hand in ("main_hand", "offhand"):
            p = v[hand]
            if p:
                f = weapons[p["weapon"]][hand]["by_affinity"][p["affinity"]]["features"]
                print(f"   {hand}: {p['affinity']} {p['weapon']} score {p['score']} "
                      + " ".join(f"{k}={x}" for k, x in f.items()))
        for w in v["top"]:
            f = weapons[w]["features"]
            print(f"   {w}{' *' if weapons[w]['in_build'] else ''}: score {weapons[w]['score']} "
                  f"(no ar {weapons[w]['score_no_ar']}) " + " ".join(f"{k}={f.get(k)}" for k in FEATURES))
        for p in v["fun"]:
            print(f"   fun: {p['affinity']} {p['weapon']}{' with ' + p['ash'] if p['ash'] else ''} "
                  f"({p['label']}, share {p['share']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
