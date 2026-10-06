#!/usr/bin/env python3
"""A weapon's PvP score from the attacks a player actually opens with, not its single best slot.

    python3 scripts/er-builds-pvp.py --rl 150 --json > pvp150.json
    python3 scripts/er-mechanics-moveset.py --pvp pvp150.json                  # rows, weapons
    python3 scripts/er-mechanics-moveset.py --pvp pvp150.json --powerstance    # + same-weapon pairs
    python3 scripts/er-mechanics-moveset.py --pvp pvp150.json --calibrate      # exponent vs adoption
    python3 scripts/er-mechanics-moveset.py --selftest

Write-up: `docs/er-mechanics/moveset.md`. Labels as the sibling docs use them: `VERIFIED` =
regulation value or EXE code, `TAE` = decoded TimeAct, `COMMUNITY` = Smithbox's decompiled
`c0000.hks`, `MEASURED` = computed here over the regulation and the planner corpus, `INFERRED` = a
modelling choice or a reading whose consumer was not traced.

`er-builds-pvp.py --sort score` ranks a weapon by `best_slot`, the maximum of `slot_score` over
every slot except jumps and the guard counter. That picks slots a player cannot start from neutral
(R1 #3 needs R1 #1 and #2 first) and never charges a slow R2 on a weapon whose R1 is good. This
module keeps `slot_score` as the unit and changes what is aggregated:

1. Openers. Only slots that start from neutral are scored on their own (`FAMILIES`). A follow-up
   (R1 #2.., R2 #2) enters only as a link of an opener's engagement (item 2).
2. Engagement. An opener plus the follow-ups that are true combos (`combos` from
   `er-mechanics-frame-advantage.slot_profile`, landing chance from
   `er-mechanics-status.combo_land`), taken to the depth that maximises `slot_score`: the damage is
   the opener's plus each link's times the chance it and every link before it land, the
   commitment runs to the last link's recovery, and the status HP scales with the expected landed
   uses. Depth 0 is the opener's own `slot_score`, unchanged. With `opening` (`er-builds-pvp.py
   --paired-offhand`) a string is scored per opening won, not per committed frame
   (`opening_credit`): each engagement also costs that many frames of neutral, which a landed
   follow-up does not pay again (`INFERRED`, docs/er-mechanics/combo.md section 6).
   The chained R2 #2 goes through `W_AttackRightHeavy2Start` and its release gate
   (`AttackRightHeavy1End_onUpdate`, `COMMUNITY` c0000.hks line 7778); the frame-advantage
   `combo` already counts that lead-in in its gap (`lead_in` on each side), and the verdict is
   read as it comes. A `--pvp` file whose R2 links carry no `lead_in` predates that and is
   refused (`check_lead_ins`).
3. Families. Within a family the player takes the best engagement (the choice is which variant of
   one input to use); across families the use share follows the matching law, share proportional
   to score ** `MATCHING_EXP`, so the weapon score is sum(s ** (1 + a)) / sum(s ** a): `a` = 0 is
   the plain mean, a large `a` the old max (`INFERRED`: Herrnstein's matching law is a claim about
   choice behaviour in general, not about this game; `--calibrate` measures `a` against adoption).
4. Grips. A weapon's two sweep rows (1H, 2H) are blended by the share of the window's PvP builds
   that carry it as primary weapon with `is2h` set (`grip_shares`, `MEASURED`), shrunk toward its
   `wepType` share by `GRIP_PRIOR_BUILDS`. Reading a build flag as a time share is `INFERRED`, and
   each grip keeps its own sweep build, so the blend describes the weapon's players, not one stat
   line.
5. Powerstance (`--powerstance`). A 1H row whose weapon passes `can_powerstance` with itself gets
   the dual L1 openers (`dual_slots`): each `AttackDualWield_SM` clip resolved the way
   `er-mechanics-attacks.tae_details` resolves an attack (spAtkcategory, imports, TAE 608 play
   speed, state-gated hitboxes skipped, the hit-record rule), each hit scored through
   `er-builds-pvp.slot_hit` against its own hand. The 1H R1/R2 openers stay available.
6. Jumps (`with_jumps`, `er-mechanics-jump.py`, moveset.md section 6). The sweep's `jump_r1`,
   `jump_r2` and `dual_jump` slots are measured on the landed clip, whose clock the air swing
   shares from the press. Each becomes three openers, one per jump the behavior script can start
   (N standing, F running, D sprinting): entry = the press frame after the jump input (6, `TAE`),
   plus the sprint's entry for D; first hit = the air clip's when it comes before the landing;
   reach = the swing's reach from the body plus the jump's travel to the hit. Damage, poise,
   stagger, frame advantage, status and parry are the slot's own (same AtkParam row). The
   exchange and reaction-dodge factors the sweep computed stay as they are: they count from the
   landed clip's start, so they miss the takeoff (see the proposed `SCORE_ENTRY_FRAMES` change in
   moveset.md section 6).
"""

from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = Path.home() / ".cache/er-build-planner"


def _sibling(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_MODS: dict = {}


def _mod(name: str):
    """A sibling module, loaded on first use. `er-builds-pvp` is never imported at load time, so
    that module can import this one without a cycle."""
    if name not in _MODS:
        _MODS[name] = _sibling(name)
    return _MODS[name]


#: Opener families: slot keys without the `2h_` prefix. The guard counter stays out (it needs a
#: blocked hit). Grouping the movement attacks into one family is `INFERRED`: each needs its own
#: movement first and the entry cost is already charged by `SCORE_ENTRY_FRAMES`.
#: The jump family is scored from the jump input, not from the landing clip the sweep's
#: `jump_r1`/`jump_r2` slots were measured on: `er-mechanics-jump.jump_slots` turns each into one
#: opener per jump (N standing, F running, D sprinting; `with_jumps`), with the takeoff charged
#: as entry and the jump's travel added to the reach. One family for both buttons: the player
#: jumps, then picks R1 or R2 (`INFERRED`, as for `move`).
JUMP_KINDS = ("n", "f", "d")
FAMILIES = {
    "r1": ("r1_1",),
    "r2": ("r2_1", "r2_1c"),
    "move": ("run_r1", "run_r2", "roll_r1", "bstep_r1", "crouch_r1"),
    "jump": tuple(f"{b}_{k}" for b in ("jump_r1", "jump_r2") for k in JUMP_KINDS),
}
#: Powerstance families (`dual_slots`); the powerstance jump L1 is scored like the right-hand jumps.
DUAL_FAMILIES = {
    "l1": ("dual_1",),
    "move": ("dual_dash", "dual_roll", "dual_crouch", "dual_bstep"),
    "jump": tuple(f"dual_jump_{k}" for k in JUMP_KINDS),
}
#: A dual movement attack costs the same entry as its right-hand twin (`SCORE_ENTRY_FRAMES`).
DUAL_ENTRY_ALIAS = {"dual_dash": "run_r1", "dual_roll": "roll_r1", "dual_crouch": "crouch_r1",
                    "dual_bstep": "bstep_r1"}
#: Dual slots whose reach, coverage, reaction dodge, exchange and neutral contest are measured on
#: their own clip by `measure_dual_slot` instead of borrowed from a right-hand twin. The crouch L1
#: of most pairs is the rolling L1 clip 034300 (`dual_attacks`), which the twin crouch R1 is not.
#: The standing, dash and backstep L1 are measured too: a borrowed twin hides their own startup
#: from the reaction dodge (Bloodhound's Fang `dual_1` hits on 17.9 against its R1 #1's 15.9).
MEASURED_DUAL_SLOTS = ("dual_1", "dual_dash", "dual_bstep", "dual_crouch", "dual_roll")
#: The right-hand slot key a measured dual slot is entered and costed as (`SCORE_ENTRY_FRAMES`,
#: `ENTRY_STAMINA`, the held-crouch neutral of `--timing-mixup crouch`).
MEASURED_DUAL_AS = {"dual_1": "r1_1", "dual_dash": "run_r1", "dual_bstep": "bstep_r1",
                    "dual_crouch": "crouch_r1", "dual_roll": "roll_r1"}
#: Matching-law sensitivity `a` (section 3 of the docstring). `INFERRED`; 1.0 is strict matching.
MATCHING_EXP = 1.0
#: Frame advantage charged when a follow-up the attacker committed to does not land: the defender
#: escaped and acts first. The floor of `SCORE_ADV_SPAN`'s clamp, `INFERRED`.
MISS_ADVANTAGE = -30.0
#: Pseudo-builds of the `wepType` 2H share added to a weapon's own count (`INFERRED`).
GRIP_PRIOR_BUILDS = 5.0
#: Candidate exponents for `--calibrate`; `inf` is the old per-family maximum.
CALIBRATE_EXPS = (0.0, 0.5, 1.0, 2.0, 4.0, 8.0, float("inf"))
#: How `moveset_score` combines its engagements when no `agg` is passed (moveset.md section 8):
#: None is the family matching mean above; ("standout", w) is `(1 - w)` x that mean + `w` x the
#: best single opener's engagement; ("openers", a) is the matching mean over every opener's own
#: engagement instead of over families. Set by `er-builds-pvp.py --aggregate`.
AGGREGATE: tuple | None = None


# --------------------------------------------------------------------------------------------
# engagements


def _combo_land(combo: dict, stagger: float) -> float:
    """`er-mechanics-status.combo_land`, except that a side carrying its own landing chance `p`
    (a cross-hand link, `er-mechanics-combo.paired_slots`: a roll-out-able one lands with the
    reaction model's chance) is read by it."""
    sides = [(combo or {}).get(k) for k in ("on_intact", "on_break")]
    if not any(v and v.get("p") is not None for v in sides):
        return _mod("er-mechanics-status").combo_land(combo, stagger)
    land = _mod("er-mechanics-status").COMBO_LAND

    def p(v):
        return 0.0 if not v else v["p"] if v.get("p") is not None else land.get(v.get("verdict"), 0.0)
    return (1.0 - stagger) * p(sides[0]) + stagger * p(sides[1])


def check_lead_ins(results: list[dict]) -> None:
    """Refuse an `er-builds-pvp.py --json` result whose R2 -> R2 links were measured before
    `er-mechanics-frame-advantage.combo` counted the charge-start lead-in: their verdicts are the
    optimistic ones, and `chain_links` reads the verdict as given."""
    stale = sorted({f"{r['weapon']} {'2H' if r['two'] else '1H'}" for r in results
                    for s in r["slots"].values() for c in s.get("combos") or []
                    if c.get("via") == "r2" for side in (c.get("on_break"), c.get("on_intact"))
                    if side and "lead_in" not in side})
    if stale:
        raise SystemExit(f"{len(stale)} rows have R2 links without `lead_in` (e.g. {stale[0]}): the "
                         "--pvp file predates the R2 release lead-in in "
                         "er-mechanics-frame-advantage.combo; regenerate it with er-builds-pvp.py")


def chain_links(slots: dict, opener: str) -> list[dict]:
    """The true-combo follow-ups after `opener`, in order: [{'key', 'p', 'combo'}].

    Same walk as `er-builds-pvp.Mechanics.slots.chain`: the first combo entry of each slot, its
    landing chance from the stagger share of the hit before it, stopping at a repeat or a zero."""
    links, seen, key = [], {opener}, opener
    while True:
        combos = (slots.get(key) or {}).get("combos") or []
        c = combos[0] if combos else None
        nxt = c["next"].removeprefix("2h_") if c else None
        if not c or nxt not in slots or nxt in seen:
            return links
        p = _combo_land(c, slots[key].get("stagger") or 0.0)
        if p <= 0.0:
            return links
        links.append({"key": nxt, "p": p, "combo": c})
        seen.add(nxt)
        key = nxt


def _expected_adv(s: dict) -> float | None:
    adv, adv_s = s.get("adv"), s.get("adv_stagger")
    if adv is None and adv_s is None:
        return None
    a = adv if adv is not None else adv_s
    b = adv_s if adv_s is not None else adv
    stag = s.get("stagger") or 0.0
    return (1.0 - stag) * a + stag * b


def opening_credit(string_score: dict, opener_score: dict, opening: float) -> float:
    """A string's score with the neutral it saves paid for (`engagement`, `opening` frames).

    `slot_score` is damage per committed frame of an opener assumed to land, so a follow-up that
    adds its damage at the opener's own rate adds nothing, and the neutral that has to be won again
    before the next hit is charged nowhere (moveset.md 1e). Here every engagement costs `opening`
    frames of neutral plus its commitment: the string's score is scaled by
    `C_s / (opening + C_s) x (opening + C_o) / C_o` (C = `commit`, entry included). The opener's
    own score is unchanged (the factor is 1 at depth 0), and `opening` = 0 is the plain rate."""
    cs, co = string_score["commit"], opener_score["commit"]
    return string_score["score"] * cs / (opening + cs) * (opening + co) / co


def engagement(slots: dict, opener: str, score_fn, entry: float = 0.0,
               opening: float | None = None) -> dict | None:
    """The best-depth engagement from `opener` (docstring item 2), or None when the opener has no
    `score_fn` score. `score_fn` is `er-builds-pvp.slot_score`. With `opening` (frames of neutral
    one engagement costs, `er-builds-pvp.py --paired-offhand`), each string is scored by
    `opening_credit`: a landed opener's follow-ups are credited as damage of the same opening, and
    the string competes with the other openers as a whole."""
    o = slots.get(opener)
    if not o:
        return None
    base = score_fn(o, entry)
    if not base:
        return None
    best = {"opener": opener, "depth": 0, "links": [], "dmg": o["dmg"], "score": base["score"],
            "detail": base}
    links = chain_links(slots, opener)
    status_hp = sum(v["hp_per_hit"] for v in (o.get("status") or {}).values())
    dmg, uses, offset, reach, prev = o["dmg"], 1.0, 0.0, 1.0, o
    for d, link in enumerate(links, 1):
        # A cross-hand link (`er-mechanics-combo.paired_slots`) starts on its own button's frame,
        # carried as `start`; every other link starts where the same button is free again.
        step = link["combo"].get("start", prev.get("next"))
        if step is None:
            break
        offset += step
        s = slots[link["key"]]
        reach *= link["p"]
        dmg += reach * s["dmg"]
        uses += reach
        last_adv = _expected_adv(s)
        adv = None if last_adv is None else reach * last_adv + (1.0 - reach) * MISS_ADVANTAGE
        syn = {**o, "dmg": dmg, "adv": adv, "adv_stagger": adv,
               "roll": None if s.get("roll") is None else offset + s["roll"],
               "next": None if s.get("next") is None else offset + s["next"],
               "status": {"engagement": {"hp_per_hit": status_hp * uses}} if status_hp else {}}
        sc = score_fn(syn, entry)
        score = sc and (sc["score"] if opening is None else opening_credit(sc, base, opening))
        if sc and score > best["score"]:
            best = {"opener": opener, "depth": d, "links": [x["key"] for x in links[:d]],
                    "p": [x["p"] for x in links[:d]], "dmg": dmg, "score": score, "detail": sc}
            if opening is not None:
                best["rate_score"] = sc["score"]
        prev = s
    return best


def matching_mean(values: list[float], exp: float = MATCHING_EXP) -> tuple[float, list[float]]:
    """(sum v ** (1 + a) / sum v ** a, use shares v ** a / sum v ** a) over positive values; the
    maximum and a one-hot share when `a` is infinite."""
    vals = [v for v in values if v and v > 0]
    if not vals:
        return 0.0, []
    if exp == float("inf"):
        top = max(vals)
        return top, [1.0 if v == top else 0.0 for v in vals]
    w = [v ** exp for v in vals]
    total = sum(w)
    return sum(v * x for v, x in zip(vals, w)) / total, [x / total for x in w]


def with_jumps(slots: dict, entry_fn) -> dict:
    """`slots` plus the per-jump openers of its `jump_r1`, `jump_r2` and `dual_jump` slots
    (`er-mechanics-jump.jump_slots`); unchanged when it has none or already carries them."""
    if not any(k in slots for k in ("jump_r1", "jump_r2", "dual_jump")) or any("jump_entry" in (s or {})
                                                                               for s in slots.values()):
        return slots
    return {**slots, **_mod("er-mechanics-jump").jump_slots(slots, entry_fn)}


def aggregate(family_scores: list[float], opener_scores: list[float], exp: float = MATCHING_EXP,
              agg: tuple | None = None) -> float:
    """The row score from the families' best engagements and every opener's own engagement
    (`AGGREGATE`): the family matching mean, blended toward the best opener (`standout`), or the
    matching mean over openers (`openers`)."""
    kind, x = agg if agg else (None, None)
    if kind == "openers":
        return matching_mean(opener_scores, x)[0]
    mean = matching_mean(family_scores, exp)[0]
    if kind == "standout":
        top = max([v for v in opener_scores if v and v > 0] or [0.0])
        return (1.0 - x) * mean + x * top
    if kind is not None:
        raise ValueError(f"unknown aggregation {agg!r}")
    return mean


def moveset_score(slots: dict, score_fn, entry_fn, families: dict | None = None,
                  exp: float = MATCHING_EXP, opening: float | None = None, agg: tuple | None = None) -> dict:
    """The weapon-row score: per family the best engagement, across families the matching mean.

    `slots` is {slot key without `2h_`: slot dict} as `er-builds-pvp.py` builds it (fields read:
    `dmg`, `next`, `roll`, `stagger`, `adv`, `adv_stagger`, `reach`, `parryable`, `status`,
    and `combos`). `score_fn` is `slot_score`, `entry_fn`
    `entry_frames`. `opening` is passed to `engagement`. Returns `score`, per family its best
    engagement and use share, and `best` (the family and opener with the highest engagement
    score)."""
    families = families or FAMILIES
    slots = with_jumps(slots, entry_fn)

    def entry(k):
        s = slots.get(k) or {}
        return s["jump_entry"] if "jump_entry" in s else entry_fn(k)
    fam, openers = {}, {}
    for name, keys in families.items():
        engs = [e for e in (engagement(slots, k, score_fn, entry(k), opening) for k in keys) if e]
        openers.update({e["opener"]: e["score"] for e in engs})
        if engs:
            fam[name] = max(engs, key=lambda e: e["score"])
    names = list(fam)
    score, shares = matching_mean([fam[n]["score"] for n in names], exp)
    agg = agg if agg is not None else AGGREGATE
    if agg:
        score = aggregate([fam[n]["score"] for n in names], list(openers.values()), exp, agg)
    for n, sh in zip([n for n in names if fam[n]["score"] > 0], shares):
        fam[n]["share"] = sh
    best = max(names, key=lambda n: fam[n]["score"]) if names else None
    out = {"score": score, "families": fam, "best": best,
           "best_opener": fam[best]["opener"] if best else None, "exp": exp, "opening": opening}
    if agg:
        out["agg"] = list(agg)
    return out


def grip_blend(row_scores: dict, share_2h: float | None) -> float | None:
    """{False: 1H score, True: 2H score} blended by the 2H share; one grip alone when the other is
    absent or the share unknown (then the higher)."""
    have = {g: s for g, s in row_scores.items() if s}
    if not have:
        return None
    if len(have) == 1 or share_2h is None:
        return max(have.values())
    return (1.0 - share_2h) * have[False] + share_2h * have[True]


# --------------------------------------------------------------------------------------------
# grip shares from the corpus


def grip_shares(mirror: Path, rl_lo: int, rl_hi: int) -> dict:
    """{weapon name: share of the window's PvP builds with it as primary that set `is2h`}.

    Corpus as `er-builds-adoption-gap.corpus` reads it (not PvE, deduplicated, primary = right
    hand position 0), every tag. A weapon's share is shrunk toward its `wepType` share by
    `GRIP_PRIOR_BUILDS` pseudo-builds. Also returns the primary counts under `_adoption`."""
    gap = _mod("er-builds-adoption-gap")
    weapon, prot = gap.weight_tables()
    rs, _ = gap.corpus(mirror, "all", rl_lo, rl_hi, weapon, prot)
    per = collections.defaultdict(lambda: [0, 0])
    cls = collections.defaultdict(lambda: [0, 0])
    adoption = collections.Counter()
    for r in rs:
        w = r["primary"]
        if not w:
            continue
        adoption[w] += 1
        if r["is2h"] is None:
            continue
        t = weapon.get(w, {}).get("wepType")
        per[w][0] += bool(r["is2h"])
        per[w][1] += 1
        cls[t][0] += bool(r["is2h"])
        cls[t][1] += 1
    total = sum(v[0] for v in cls.values()) / max(1, sum(v[1] for v in cls.values()))
    out = {"_adoption": dict(adoption), "_builds": len(rs), "_overall": total}
    for w, row in weapon.items():
        c = cls.get(row.get("wepType"))
        prior = c[0] / c[1] if c and c[1] else total
        n2, n = per.get(w, (0, 0))
        out[w] = (n2 + GRIP_PRIOR_BUILDS * prior) / (n + GRIP_PRIOR_BUILDS)
    return out


# --------------------------------------------------------------------------------------------
# powerstance


def _real(to_real, seconds):
    return _mod("er-mechanics-attacks").real_frame(to_real(seconds))


def dual_attacks(reg, weapon_id: int, level: int = 0) -> list[dict]:
    """The powerstance L1 slots of `weapon_id` in both hands, in real frames.

    Per `AttackDualWield_SM` clip (`er-mechanics-powerstance-guard.DUAL_SLOTS`; the jump L1 is its
    landed clip 034570, timed from the jump input later by `with_jumps`): the TAE entry resolved with `motion_category` and `resolve_events`, TAE 608 play speed through
    `clip_to_real`, state-gated hitboxes skipped, and `hit_records` over every resolvable judge;
    a dual judge (800-899) counts when it opens a fresh record and is not a takeover, as
    `tae_details` counts a sweep hit. Both hands are `weapon_id` (same-weapon pairs), so every hit
    resolves against its behavior variation; `hand` is the Source byte (`VERIFIED`, powerstance-
    guard.md section 1). Recovery: `recovery_details` for roll/R1/R2/guard/move and the L1 input
    and cancel pair of `er-mechanics-powerstance-guard._l1_recovery` for the next L1, carried to
    real time."""
    atk = _mod("er-mechanics-attacks")
    psg = _mod("er-mechanics-powerstance-guard")
    w = reg.weapon[weapon_id]
    variation = w["behaviorVariationId"]
    out = []
    for key, label, anim, state in psg.DUAL_SLOTS:
        if key == "dual_crouch" and not atk.uses_dual_stealth_attack(w):
            # `ExecAttack` lines 1644-1648 (`HKS` bytecode): the powerstance crouch request becomes
            # `W_AttackDualRolling` when `IsUseStealthAttack(TRUE)` refuses the right hand, and
            # `c0000.behbnd` plays clip 034300 for that event. This is the game's crouch L1 for
            # every pair outside `DUAL_STEALTH_ATTACK_CATEGORIES`, the colossal weapons and
            # colossal swords included, not a stand-in.
            anim, state = next(a for k, _, a, _ in psg.DUAL_SLOTS if k == "dual_roll"), "AttackDualRolling"
        category = atk.motion_category(w, anim, right_hand_fallback=False)
        src_cat, src_anim, events = atk.resolve_events(category, anim)
        if events is None:
            continue
        to_real = atk.clip_to_real(events)
        claimed = []
        for e in events:
            if e.type != atk.TAE_ATTACK_BEHAVIOR:
                continue
            if struct.unpack_from("<H", e.params, atk.ATTACK_STATE_GATE_OFFSET)[0]:
                continue
            judge = struct.unpack_from("<i", e.params, psg.TAE_ARG_JUDGE)[0]
            if reg.resolve_behavior_id(judge, variation) < 0:
                continue
            claimed.append((e, judge, struct.unpack_from("<i", e.params, psg.TAE_ARG_ATTACK_INDEX)[0],
                            e.params[psg.TAE_ARG_SOURCE]))
        records = atk.hit_records([(e.start, e.end, j, i) for e, j, i, _ in claimed])
        hits = []
        for (e, judge, index, source), (opened, follows) in zip(claimed, records):
            if judge not in psg.DUAL_JUDGES or not opened or follows is not None:
                continue
            nums = atk.attack_numbers(reg, weapon_id, judge, level)
            if not nums or not nums["can_hit_enemy"] or (nums["mv_phys"] + nums["mv_mag"] + nums["mv_fire"]
                                                         + nums["mv_light"] + nums["mv_holy"]) <= 0:
                continue
            hits.append({**nums, "hand": "left" if source == psg.SOURCE_LEFT else "right",
                         "attack_index": index,
                         "frames": (_real(to_real, e.start), _real(to_real, e.end)),
                         "frames_clip": (atk.clip_frame(e.start), atk.clip_frame(e.end))})
        if not hits:
            continue
        hits.sort(key=lambda h: h["frames"][0])
        rec = atk.recovery_details(w, src_anim, events, [h["frames"] for h in hits], src_cat, to_real)
        l1 = psg._l1_recovery(events)
        cancel = dict(rec["cancel_frame"])
        cancel["l1"] = None if l1 is None else _real(to_real, l1 / atk.TAE_FPS)
        crits = _mod("er-mechanics-crits")
        out.append({"slot": key, "label": label, "anim": f"a{src_cat:03d}_{src_anim:06d}", "state": state,
                    "hits": hits, "cancel_frame": cancel, "anim_frames": rec["anim_frames"],
                    "parryable": bool(crits._jump_table_windows(events, crits.JT_GET_PARRIED))})
    return out


def measure_dual_slot(pvp, mech, reg, wid: int, base_id: int, row: dict, slot: dict, level: int = 0,
                      pool=None, npool=None) -> dict:
    """Measure one `dual_attacks` row's reach, coverage, reaction dodge, exchange and neutral
    contest on its own clip and write them into its `DualScorer` slot dict.

    Each is read the way `er-builds-pvp` reads a right-hand slot: `er-mechanics-reach.attack_reach`
    on the row's TAE entry (every damaging dual judge's hitbox posed through the clip),
    `er-mechanics-ashes.slot_reaction` on those contacts timed from the clip's start, and
    `er-mechanics-exchange.slot_exchange` / `er-mechanics-neutral.slot_neutral` on an attack row
    built by `tae_details` (right-hand judge as the main window, left-hand judge as an extra hitbox,
    the clip's hyperarmor). Entry and stamina are the right-hand twin's (`MEASURED_DUAL_AS`).
    Both hands' hitboxes are posed on `R_Weapon`: the Source byte that names the left hand is not
    read by the reach module (`INFERRED` that a symmetric two-handed slam reaches as far on both
    sides). Returns the slot, whose `measured` lists the fields that came from the clip."""
    atk = _mod("er-mechanics-attacks")
    rm, ash = mech.re, mech.ash
    as_key = MEASURED_DUAL_AS.get(row["slot"], row["slot"])
    cat, anim = (int(x) for x in row["anim"][1:].split("_"))
    first = row["hits"][0]
    judge = first["judge"]
    measured = []
    if rm._RC is None:
        rm._RC = rm.Reach()
    reach = rm.attack_reach(rm._RC, wid, row["slot"], row["label"], judge, anim, "one", clip=(cat, anim)) or {}
    if reach.get("world_reach_m") is not None:
        slot["reach"], slot["reach_source"] = reach["world_reach_m"], "world (own clip)"
        measured += ["reach", "reach_source"]
    if reach.get("coverage_factor") is not None:
        slot["coverage"] = reach["coverage_factor"]
        measured.append("coverage")
    fc = reach.get("front_contact_frame_real") or {}
    slot["front_contact"] = fc
    if mech.react:
        dists = [d for d in rm.FRONT_CONTACT_DISTANCES_M if fc.get(d) is not None]
        slot["react"] = ash.slot_reaction(reach.get("window_contacts"), dists or [ash.ENGAGE_DISTANCE_M], 0.0,
                                          slot.get("roll"), mech.strikes,
                                          fallback=(slot.get("startup"), slot.get("active")))
        measured.append("react")
    if pool is not None:
        tae = atk.tae_details(reg, wid, anim, judge, cat)
        nums = atk.attack_numbers(reg, wid, judge, level)
        if tae is not None and nums is not None:
            arow = {"slot": as_key, "tae_entry": row["anim"], **nums, **tae}
            entry = pvp.entry_frames(as_key)
            slot["exchange"] = pvp.EXCH.slot_exchange(pool, reg, base_id, arow, slot, entry)
            measured.append("exchange")
            if npool is not None and (slot["exchange"] or {}).get("strike_frame") is not None:
                held = {}
                if "crouch" in pvp.TIMING_MIXUP and as_key == "crouch_r1":
                    held = {"tools": (), "k": _mod("er-mechanics-timing-mixup").crouch_k()}
                slot["neutral"] = pvp.NEUT.slot_neutral(npool, slot, arow, entry, **held)
                measured.append("neutral")
    slot["measured"] = tuple(measured)
    return slot


class DualScorer:
    """Scores `dual_attacks` rows into `er-builds-pvp` slot dicts, with the PvP tool's tables."""

    def __init__(self, pvp, reg, pvp_tables, defenders, mech, poises):
        self.pvp, self.reg, self.pt, self.defenders, self.mech = pvp, reg, pvp_tables, defenders, mech
        self.poises = poises
        self.fa, self.fa_t = mech.fa, mech.fa_t
        self._react = {}

    def react(self, level):
        if level not in self._react:
            self._react[level] = self.fa.reaction(level)
        return self._react[level]

    def slots(self, weapon, aff, level, stats, wid, base_id, ar_by, grease, spear=False, talismans=None):
        """{dual key: slot dict} with the fields `moveset_score` reads (`dmg`, `next` = next L1,
        `roll`, `stagger`, `adv`, `adv_stagger`, `status`, `combos`). A grease (`(element,
        flat)`) is added to right-hand hits only: the goods applies the right-hand SpEffect row
        (grease.md section 1, `VERIFIED`), and whether a dual judge's hit reads it through the
        BehaviorParam category mask is not traced (`INFERRED`)."""
        rows = dual_attacks(self.reg, wid, level)
        st = self.mech.st
        ws = st.weapon_status(self.mech.st_t, weapon, aff, level, stats, two_handed=False, pvp=True)
        out, meta = {}, {}
        for r in rows:
            key = r["slot"]
            total = None
            poise = 0.0
            for h in r["hits"]:
                a = {**h, "slot": key, "hit_windows": [h["frames"]], "own_sweep_hits": 1, "cancel_frame": {}}
                one = self.pvp.slot_hit(self.pt, self.reg, base_id, a, ar_by, self.defenders,
                                        grease if h["hand"] == "right" else None, spear, talismans)
                poise += one["poise"]
                if total is None:
                    total = one
                else:
                    for k in ("dmg", "med", "ctr"):
                        total[k] += one[k]
                    for el in total["by_type"]:
                        total["by_type"][el] += one["by_type"][el]
                    total["hits"] += 1
            last = r["hits"][-1]
            stagger = sum(p < poise for p in self.poises) / len(self.poises) if self.poises else 0.0
            lvl = (self.fa.reaction_level(self.fa_t, last["atk_row"], False),
                   self.fa.reaction_level(self.fa_t, last["atk_row"], True))
            syn = {"hit_windows": [last["frames"]], "cancel_frame": r["cancel_frame"]}
            adv_i = self.fa.advantage(syn, self.react(lvl[0])) if self.react(lvl[0]) else None
            adv_b = self.fa.advantage(syn, self.react(lvl[1])) if self.react(lvl[1]) else None
            status = {}
            if ws["sources"]:
                first = {"atk_row": r["hits"][0]["atk_row"], "hit_windows": [r["hits"][0]["frames"]],
                         "other_hitboxes": [{"atk_row": h["atk_row"]} for h in r["hits"][1:]]}
                status = st.status_expected(self.mech.st_t, ws, first, self.mech.st_dfs,
                                            gap=r["cancel_frame"].get("l1"), react=lvl, stagger=stagger)
            out[key] = {**total, "label": r["label"], "anim": r["anim"], "poise": poise, "stagger": stagger,
                        "startup": r["hits"][0]["frames"][0], "next": r["cancel_frame"].get("l1"),
                        "roll": r["cancel_frame"].get("dodge"), "reaction": lvl,
                        "adv": (adv_i or {}).get("advantage"), "adv_stagger": (adv_b or {}).get("advantage"),
                        "status": status, "hands": [h["hand"] for h in r["hits"]],
                        "parryable": r["parryable"]}
            meta[key] = (r, syn, lvl)
        order = [k for k, *_ in _mod("er-mechanics-powerstance-guard").DUAL_SLOTS]
        for a, b in zip(order, order[1:]):
            if a not in out or b not in out or not a.startswith("dual_") or not a[5:].isdigit() \
                    or not b[5:].isdigit():
                continue
            ra, syn, lvl = meta[a]
            second = {"hit_windows": [meta[b][0]["hits"][0]["frames"]]}
            first = {**syn, "cancel_frame": {"l1": ra["cancel_frame"].get("l1")}}
            out[a]["combos"] = [{"next": b, "via": "l1",
                                 "on_intact": self.fa.combo(first, second, "l1", self.react(lvl[0])),
                                 "on_break": self.fa.combo(first, second, "l1", self.react(lvl[1]))}]
        return out


# --------------------------------------------------------------------------------------------
# measurement over an `er-builds-pvp.py --json` result


def _entry_fn(pvp):
    return lambda k: pvp.entry_frames(DUAL_ENTRY_ALIAS.get(k, k))


def score_results(pvp, results: list[dict], exp: float = MATCHING_EXP) -> None:
    entry = _entry_fn(pvp)
    for r in results:
        r["moveset"] = moveset_score(r["slots"], pvp.slot_score, entry, exp=exp)
        b = r.get("best_slot")
        r["old_score"] = r["slots"][b]["score"]["score"] if b else 0.0
        if r.get("dual_slots"):
            fam = {**FAMILIES, "l1": DUAL_FAMILIES["l1"],
                   "move": FAMILIES["move"] + DUAL_FAMILIES["move"],
                   "jump": FAMILIES["jump"] + DUAL_FAMILIES["jump"]}
            r["moveset_ps"] = moveset_score({**r["slots"], **r["dual_slots"]}, pvp.slot_score, entry,
                                            families=fam, exp=exp)


def add_powerstance(pvp, results: list[dict], rl: int, window: int, mirror: Path, grease_name: str,
                    pool=None, npool=None) -> None:
    """Compute `dual_slots` for every 1H row whose weapon powerstances with itself.

    The `MEASURED_DUAL_SLOTS` are measured on their own clip (`measure_dual_slot`) against `pool`
    (the ranking's `er-mechanics-exchange.Pool`, built here when not passed) and `npool` (its
    neutral pool; without it those slots carry no neutral contest)."""
    opt, atk, ar = pvp.OPT, pvp.ATK, pvp.AR
    psg = _mod("er-mechanics-powerstance-guard")
    tables, reg, pt = ar.Tables(None), atk.Regulation(None), pvp.PvpTables()
    defenders = pvp.Defenders(pvp.pvp_corpus(mirror, rl - window, rl + window))
    mech = pvp.Mechanics(reg, tables, mirror, rl, window)
    scorer = DualScorer(pvp, reg, pt, defenders, mech, defenders.poise)
    flat = opt.GREASES[grease_name]
    for r in results:
        if r["two"]:
            continue
        wid = tables.find_weapon(r["weapon"], r["aff"])
        if not psg.can_powerstance(reg, wid, wid):
            continue
        stats = {k: r["stats"][k] for k in opt.DAMAGE_STATS}
        rating = ar.attack_rating(tables, r["weapon"], r["aff"], r["level"], stats, False)
        ar_by = {el: rating["damage"].get(el, {}).get("total", 0.0) for el in pvp.ELEMENTS}
        grease = (r["grease"], flat) if r["grease"] else None
        base_id = tables.find_weapon(r["weapon"], "Standard")
        r["dual_slots"] = scorer.slots(r["weapon"], r["aff"], r["level"], r["stats"], wid, base_id, ar_by, grease)
        # Reach proxy: the reach module poses right-hand hitboxes only, so a dual slot takes the
        # row's 1H R1 #1 reach (`INFERRED`: both are a one-handed swing of the same weapon).
        r1 = r["slots"].get("r1_1") or {}
        for s in r["dual_slots"].values():
            s["reach"], s["reach_source"] = r1.get("reach"), "r1_1 proxy"
        want = [k for k in MEASURED_DUAL_SLOTS if k in r["dual_slots"]]
        if want:
            if pool is None:
                pool = pvp.EXCH.Pool(pvp.EXCH.opponent_pool(reg, mirror, rl - window, rl + window))
            mech.strikes = pool.startup[pool.build_prof]
            rows = {d["slot"]: d for d in dual_attacks(reg, wid, r["level"])}
            for k in want:
                measure_dual_slot(pvp, mech, reg, wid, base_id, rows[k], r["dual_slots"][k], r["level"],
                                  pool, npool)


def weapon_table(results: list[dict], shares: dict, key: str = "moveset") -> dict:
    """{weapon: {'score', 'rows', 'share_2h'}} with `grip_blend` over its rows."""
    by = collections.defaultdict(dict)
    for r in results:
        m = r.get(key) or r.get("moveset")
        by[r["weapon"]][r["two"]] = m["score"] if m else None
    return {w: {"score": grip_blend(g, shares.get(w)), "rows": g, "share_2h": shares.get(w)}
            for w, g in by.items()}


# --------------------------------------------------------------------------------------------
# self test


def selftest() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok   " if cond else "FAIL ") + msg)
        ok = ok and bool(cond)

    pvp = _mod("er-builds-pvp")
    entry = _entry_fn(pvp)
    base = {"dmg": 600.0, "roll": 30.0, "next": 40.0, "reach": pvp.SCORE_REACH_REF_M, "stagger": 0.0,
            "adv": 0.0, "adv_stagger": 0.0, "parryable": False, "status": {}}
    e0 = engagement({"r1_1": base}, "r1_1", pvp.slot_score, 0.0)
    check(e0 and e0["depth"] == 0 and abs(e0["score"] - pvp.slot_score(base)["score"]) < 1e-9,
          "an opener without combos scores exactly its slot_score")

    # A follow-up that always lands (true on both sides) and comes quickly raises the score.
    true = {"gap": 10, "escape": 20, "escape_by": "roll", "verdict": "true"}
    slots = {"r2_1": {**base, "stagger": 1.0, "combos": [{"next": "r2_2", "via": "r2", "on_break": true,
                                                          "on_intact": true}]},
             "r2_2": {**base, "dmg": 900.0, "roll": 20.0, "next": 25.0}}
    e = engagement(slots, "r2_1", pvp.slot_score, 0.0)
    check(e["depth"] == 1 and e["dmg"] == 1500.0,
          f"a landing follow-up is taken and its damage added ({e['depth']}, {e['dmg']})")
    check(abs(e["detail"]["commit"] - (40.0 + 20.0)) < 1e-9,
          "the engagement commits to the follow-up's roll, offset by the opener's next frame")
    # The verdict is read as the frame-advantage module gives it: no second lead-in on top.
    slots["r2_2"]["release_lead_in"] = 10.0
    e = engagement(slots, "r2_1", pvp.slot_score, 0.0)
    check(e["depth"] == 1, "a release lead-in on the follow-up slot is not added to the gap again")
    try:
        check_lead_ins([{"weapon": "X", "two": True, "slots": slots}])
        refused = False
    except SystemExit:
        refused = True
    check(refused, "an R2 link without `lead_in` (a --pvp file from before the fix) is refused")
    check_lead_ins([{"weapon": "X", "two": True, "slots": {"r2_1": {"combos": [
        {"next": "r2_2", "via": "r2", "on_break": {**true, "lead_in": 14.2}, "on_intact": None}]}}}])
    # A combo that lands only on a stagger is weighted by the stagger share.
    half = {**slots["r2_1"], "stagger": 0.5,
            "combos": [{"next": "r2_2", "via": "r2", "on_break": true,
                        "on_intact": {"gap": 10, "escape": 0, "escape_by": "not locked", "verdict": "no"}}]}
    links = chain_links({"r2_1": half, "r2_2": slots["r2_2"]}, "r2_1")
    check(len(links) == 1 and abs(links[0]["p"] - 0.5) < 1e-9, "landing chance = stagger share x on_break")

    # Opening credit: a sure follow-up at the opener's own rate adds nothing to the plain rate, and
    # with 150 frames of neutral per engagement it is worth taking.
    same = {**base, "stagger": 1.0, "combos": [{"next": "left_1", "via": "l1", "start": 40.0,
                                                "on_break": {**true, "p": 1.0}, "on_intact": None}]}
    fu = {**base, "roll": 30.0, "next": 40.0}
    pair = {"r1_1": same, "left_1": fu}
    plain = engagement(pair, "r1_1", pvp.slot_score, 0.0)
    cred = engagement(pair, "r1_1", pvp.slot_score, 0.0, 150.0)
    check(plain["depth"] == 0 and cred["depth"] == 1 and cred["score"] > plain["score"],
          f"at the opener's rate the plain rate keeps depth 0 and the opening credit takes the string "
          f"({plain['score']:.1f} -> {cred['score']:.1f})")
    c0, c1 = pvp.slot_score(same)["commit"], cred["detail"]["commit"]
    check(abs(opening_credit(cred["detail"], pvp.slot_score(same), 150.0)
              - cred["detail"]["score"] * c1 / (150.0 + c1) * (150.0 + c0) / c0) < 1e-9
          and abs(opening_credit(pvp.slot_score(same), pvp.slot_score(same), 150.0)
                  - pvp.slot_score(same)["score"]) < 1e-9,
          "opening_credit: C_s / (T + C_s) x (T + C_o) / C_o, and 1 on the opener itself")
    rolly = {**same, "combos": [{**same["combos"][0], "on_break": {**true, "verdict": "roll-out-able", "p": 0.25}}]}
    lk = chain_links({"r1_1": rolly, "left_1": fu}, "r1_1")
    check(len(lk) == 1 and abs(lk[0]["p"] - 0.25) < 1e-9, "a side's own `p` (roll-out-able) is its landing chance")

    vals = [1.0, 2.0, 4.0]
    check(abs(matching_mean(vals, 0.0)[0] - 7.0 / 3.0) < 1e-9, "matching exponent 0 is the mean")
    check(matching_mean(vals, float("inf"))[0] == 4.0, "an infinite exponent is the maximum")
    m1 = matching_mean(vals, 1.0)[0]
    check(abs(m1 - 21.0 / 7.0) < 1e-9 and 7.0 / 3.0 < m1 < 4.0, "exponent 1: sum v^2 / sum v, between them")
    ms = moveset_score({"r1_1": base, "r2_1": {**base, "dmg": 300.0}}, pvp.slot_score, entry)
    check(ms["best_opener"] == "r1_1" and ms["score"] < pvp.slot_score(base)["score"],
          "a weaker R2 family pulls the weapon below its best opener's score")
    ms2 = moveset_score({"r1_1": base, "r1_3": {**base, "dmg": 5000.0}}, pvp.slot_score, entry)
    check(ms2["best_opener"] == "r1_1", "R1 #3 is not an opener")
    check(grip_blend({False: 100.0, True: 200.0}, 0.25) == 125.0 and grip_blend({False: 100.0, True: None}, 0.9)
          == 100.0, "grip_blend weights by the 2H share and falls back to the grip that has a score")

    # Game data: the frame-advantage R2 link carries the lead-in, and powerstance extraction.
    atk = _mod("er-mechanics-attacks")
    psg = _mod("er-mechanics-powerstance-guard")
    fa = _mod("er-mechanics-frame-advantage")
    reg = atk.Regulation(None)
    gs = reg.find_weapon("Greatsword")
    prof = {e["slot"]: e for e in fa.slot_profile(reg, fa.Tables(reg), gs, "both", ("2h_r2_1", "2h_r2_2"))}
    link = ((prof.get("2h_r2_1") or {}).get("combos") or [{}])[0].get("on_break") or {}
    check(link.get("lead_in"), f"Greatsword 2H R2 #1 -> #2 carries its release lead-in ({link.get('lead_in')})")
    check(psg.can_powerstance(reg, gs, gs), "Greatsword pairs with itself")
    dual = {r["slot"]: r for r in dual_attacks(reg, gs)}
    d1 = dual.get("dual_1")
    check(d1 and sorted(h["hand"] for h in d1["hits"]) == ["left", "right"],
          f"Greatsword L1 #1 is one right and one left hit ({d1 and [h['hand'] for h in d1['hits']]})")
    check(d1 and d1["hits"][0]["frames"][0] < d1["hits"][0]["frames_clip"][0],
          "its 1.2x play-speed window makes the real first hit earlier than the clip frame")
    check(d1 and d1["cancel_frame"].get("l1") and d1["cancel_frame"].get("dodge"),
          f"L1 #1 has a next-L1 and a roll frame ({d1 and d1['cancel_frame']})")
    dj = dual.get("dual_jump")
    check(dj and dj["anim"].endswith("_034570") and dj["cancel_frame"].get("dodge"),
          f"the powerstance jump L1 is extracted from its landed clip ({dj and dj['anim']})")

    # Jumps: the family scores the jump from the input, not the landed clip.
    jump = {"dmg": 700.0, "roll": 42.0, "next": 35.0, "reach": 3.0, "stagger": 1.0, "adv": 0.0,
            "adv_stagger": 0.0, "parryable": False, "status": {}, "startup": 16, "anim": "a026_031070"}
    js = with_jumps({"r1_1": base, "jump_r1": jump}, entry)
    check({f"jump_r1_{k}" for k in JUMP_KINDS} <= set(js) and set(FAMILIES["jump"]) >= {"jump_r1_f", "jump_r2_d"},
          "each jump slot becomes one opener per jump kind, all in the jump family")
    run = entry("run_r1")
    check(js["jump_r1_f"]["jump_entry"] == 6.0 and js["jump_r1_d"]["jump_entry"] == 6.0 + run,
          f"entry: the press frame, plus the sprint's {run} for the D jump")
    check(js["jump_r1_f"]["reach"] > js["jump_r1_n"]["reach"] and js["jump_r1_f"]["startup"] == 22,
          f"the running jump reaches farther ({js['jump_r1_n']['reach']} -> {js['jump_r1_f']['reach']}) and "
          f"hits 6 frames after its clip's hit")
    ms3 = moveset_score({"r1_1": base, "jump_r1": jump}, pvp.slot_score, entry)
    fj = ms3["families"].get("jump")
    check(fj and abs(fj["detail"]["commit"] - (6.0 + 35.0)) < 1e-9,
          f"a jump engagement commits from the jump input: press 6 + next 35 ({fj and fj['detail']['commit']})")
    flat = {**jump, "reach": None}
    fj0 = moveset_score({"jump_r1": flat}, pvp.slot_score, entry)["families"]["jump"]
    check(fj0["score"] < pvp.slot_score(flat)["score"],
          "reach aside, the takeoff lowers the jump below the landed clip's own score")
    check(with_jumps(js, entry) is js, "with_jumps does not add openers twice")
    # A cross-hand link on the jump slot survives onto the jump openers, held to the landing.
    xl = {"next": "left_1", "via": "l1", "start": 20.0, "first_hit": 16, "lead_in": 0.0, "next_first_hit": 13,
          "on_break": {"gap": 17.0, "escape": 35, "verdict": "true", "p": 1.0, "p_roll": 0.1},
          "on_intact": {"gap": 17.0, "escape": 0, "verdict": "no", "p": 0.0, "p_roll": 0.1}}
    jl = with_jumps({"r1_1": base, "jump_r1": {**jump, "combos": [xl, {"next": "r1_2", "via": "r1"}]}}, entry)
    jc = jl["jump_r1_n"]["combos"]
    seq = jl["jump_r1_n"]["jump"]
    floor = seq["landing"] - seq["press"]
    want = 17.0 + max(0.0, floor - 20.0) + (16 - (seq["first_hit"] - seq["press"]))
    check(len(jc) == 1 and jc[0]["start"] == max(20.0, floor) and abs(jc[0]["on_break"]["gap"] - round(want, 1)) < 1e-9
          and jc[0]["on_break"]["verdict"] == ("true" if want < 35 else "roll-out-able"),
          f"a jump keeps its cross-hand link, re-timed to the landing ({jc and jc[0]['start']}, "
          f"gap {jc and jc[0]['on_break']['gap']}), and drops the same-weapon one")
    print("selftest", "passed" if ok else "FAILED")
    return 0 if ok else 1


# --------------------------------------------------------------------------------------------
# output


def _spearman(a, b):
    return _mod("er-builds-adoption-gap").spearman(a, b)


def calibrate(pvp, results, shares) -> list[tuple]:
    """Spearman rho of the weapon score against primary-weapon adoption, per matching exponent,
    over the sweep weapons (zeros included) and over the adopted ones; the old best-slot
    ranking (per grip, blended the same way) as the reference."""
    adoption = shares["_adoption"]
    out = []
    by = collections.defaultdict(dict)
    for r in results:
        by[r["weapon"]][r["two"]] = r["old_score"]
    old = {w: grip_blend(g, shares.get(w)) or 0.0 for w, g in by.items()}
    names = sorted(old)
    adopt = [adoption.get(w, 0) for w in names]
    adopted = [i for i, a in enumerate(adopt) if a]

    def rho(scores):
        v = [scores.get(w) or 0.0 for w in names]
        return (_spearman(v, adopt), _spearman([v[i] for i in adopted], [adopt[i] for i in adopted]))

    out.append(("best slot (old)",) + rho(old))
    for x in CALIBRATE_EXPS:
        score_results(pvp, results, x)
        wt = weapon_table(results, shares)
        out.append((f"matching a={x:g}",) + rho({w: v["score"] for w, v in wt.items()}))
    score_results(pvp, results, MATCHING_EXP)
    return out


def jump_effect(pvp, results, shares, exp: float = MATCHING_EXP) -> list[dict]:
    """Per weapon, the grip-blended moveset score and rank with the jump family and without it,
    over the same slots; sorted by rank gained. Each entry also names the row's best jump opener
    and its use share (the grip with the larger share of the weapon's builds)."""
    entry = _entry_fn(pvp)
    no_jump = {k: v for k, v in FAMILIES.items() if k != "jump"}
    by = collections.defaultdict(dict)
    for r in results:
        a = moveset_score(r["slots"], pvp.slot_score, entry, exp=exp)
        b = moveset_score(r["slots"], pvp.slot_score, entry, families=no_jump, exp=exp)
        by[r["weapon"]][r["two"]] = (a, b)
    table = {}
    for w, g in by.items():
        sh = shares.get(w)
        new = grip_blend({k: v[0]["score"] for k, v in g.items()}, sh)
        old = grip_blend({k: v[1]["score"] for k, v in g.items()}, sh)
        grip = True if True in g and (False not in g or (sh or 0) >= 0.5) else False
        fam = g[grip][0]["families"].get("jump") or {}
        table[w] = {"weapon": w, "with": new or 0.0, "without": old or 0.0, "grip": "2H" if grip else "1H",
                    "jump_opener": fam.get("opener"), "jump_score": fam.get("score"), "jump_share": fam.get("share")}
    for key in ("with", "without"):
        for i, w in enumerate(sorted(table, key=lambda x: -table[x][key]), 1):
            table[w][f"rank_{key}"] = i
    return sorted(table.values(), key=lambda v: v["rank_with"] - v["rank_without"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pvp", type=Path, help="er-builds-pvp.py --json output")
    ap.add_argument("--mirror", type=Path, default=CACHE / "builds.jsonl")
    ap.add_argument("--window", type=int, default=10)
    ap.add_argument("--grease", default="dlc-drawstring", help="grease flat value key (er-builds-optimize GREASES)")
    ap.add_argument("--exp", type=float, default=MATCHING_EXP)
    ap.add_argument("--powerstance", action="store_true")
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--weapon", action="append", default=[], help="print this weapon's families in full")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--jump-effect", action="store_true",
                    help="weapon ranks with and without the jump family, largest movers first")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.pvp:
        ap.error("--pvp is required")
    pvp = _mod("er-builds-pvp")
    data = json.load(a.pvp.open())
    results, rl = data["results"], data["rl"]
    check_lead_ins(results)
    if a.powerstance:
        add_powerstance(pvp, results, rl, a.window, a.mirror, a.grease)
    score_results(pvp, results, a.exp)
    shares = grip_shares(a.mirror, rl - a.window, rl + a.window)
    adoption = shares["_adoption"]
    if a.jump_effect:
        eff = jump_effect(pvp, results, shares, a.exp)
        print(f"jump family effect on {len(eff)} weapons (moveset score, grips blended; rank with / "
              f"without the jump family)")
        print(f"  {'weapon':<30}{'with':>7}{'w/o':>7}{'rank':>6}{'w/o':>6}{'moved':>7}  grip best jump opener")
        ends = eff[:a.top] + eff[-a.top:] if len(eff) > 2 * a.top else eff
        shown = ends + [e for e in eff if e["weapon"] in a.weapon and e not in ends]
        for e in shown:
            op = f"{e['jump_opener']} {e['jump_score']:.0f} ({100 * (e['jump_share'] or 0):.0f}%)" \
                if e["jump_opener"] else "-"
            print(f"  {e['weapon'][:29]:<30}{e['with']:>7.0f}{e['without']:>7.0f}{e['rank_with']:>6}"
                  f"{e['rank_without']:>6}{e['rank_without'] - e['rank_with']:>+7}  {e['grip']}  {op}")
        return 0

    old_rank = {id(r): i + 1 for i, r in enumerate(sorted(results, key=lambda r: -r["old_score"]))}
    new = sorted(results, key=lambda r: -r["moveset"]["score"])
    if a.json:
        print(json.dumps({"rl": rl, "exp": a.exp, "rows": [
            {"weapon": r["weapon"], "two": r["two"], "old_score": r["old_score"], "old_best": r["best_slot"],
             "moveset": r["moveset"], "moveset_ps": r.get("moveset_ps")} for r in new],
            "weapons": weapon_table(results, shares)}, indent=1, default=str))
        return 0
    print(f"RL {rl}: {len(results)} sweep rows; matching exponent {a.exp:g}; grip shares from "
          f"{shares['_builds']} PvP builds of RL {rl - a.window}-{rl + a.window} (overall 2H "
          f"{100 * shares['_overall']:.0f}%)")
    print(f"\nrows by moveset score (old = best single slot, its rank in brackets); per family the best "
          f"engagement's opener, depth (links taken) and use share")
    print(f"  {'#':>3} {'weapon':<30}{'grip':>5}{'moveset':>9}{'old':>7}{'(rank)':>8}  families")
    for i, r in enumerate(new[:a.top], 1):
        m = r["moveset"]
        fams = "  ".join(f"{n}:{f['opener']}{'+' + str(f['depth']) if f['depth'] else ''} {f['score']:.0f} "
                         f"({100 * f.get('share', 0):.0f}%)" for n, f in m["families"].items())
        print(f"  {i:>3} {r['weapon'][:29]:<30}{'2H' if r['two'] else '1H':>5}{m['score']:>9.0f}"
              f"{r['old_score']:>7.0f}{'(' + str(old_rank[id(r)]) + ')':>8}  {fams}")
    depth = collections.Counter((r["weapon"], r["two"], n) for r in results
                                for n, f in r["moveset"]["families"].items() if f["depth"])
    print(f"\nengagements that take a true-combo follow-up: {len(depth)} of "
          f"{sum(len(r['moveset']['families']) for r in results)} row families")
    wt = weapon_table(results, shares)
    wold = weapon_table([{**r, "moveset": {"score": r["old_score"]}} for r in results], shares)
    worder = sorted(wt, key=lambda w: -(wt[w]["score"] or 0))
    oorder = {w: i + 1 for i, w in enumerate(sorted(wold, key=lambda w: -(wold[w]["score"] or 0)))}
    print(f"\nweapons (grip blend by 2H share; adoption = primary weapon in the window's PvP builds)")
    print(f"  {'#':>3} {'weapon':<30}{'score':>8}{'1H':>7}{'2H':>7}{'2H%':>6}{'old#':>6}{'adopt':>7}")
    for i, w in enumerate(worder[:a.top], 1):
        v = wt[w]
        print(f"  {i:>3} {w[:29]:<30}{v['score']:>8.0f}{v['rows'].get(False) or 0:>7.0f}{v['rows'].get(True) or 0:>7.0f}"
              f"{100 * (v['share_2h'] or 0):>6.0f}{oorder[w]:>6}{adoption.get(w, 0):>7}")
    for w in ("Giant-Crusher", "Greatsword"):
        if w in wt:
            print(f"  {w}: rank {worder.index(w) + 1} (old {oorder[w]}), score {wt[w]['score']:.0f}, adoption "
                  f"{adoption.get(w, 0)}")
    if a.powerstance:
        ps = sorted([r for r in results if r.get("moveset_ps")], key=lambda r: -r["moveset_ps"]["score"])
        verdicts = collections.Counter(
            ((c.get("on_intact") or {}).get("verdict"), (c.get("on_break") or {}).get("verdict"))
            for r in ps for s in r["dual_slots"].values() for c in s.get("combos") or [])
        taken = sum(1 for r in ps for f in r["moveset_ps"]["families"].values()
                    if f["opener"].startswith("dual_") and f["depth"])
        parry = collections.Counter(s.get("parryable") for r in ps for s in r["dual_slots"].values())
        print(f"\npowerstance: {len(ps)} 1H rows pair with themselves; dual L1 chain verdicts (intact, break) "
              f"{dict(verdicts)}; dual engagements taking a follow-up {taken}; dual slots parryable {dict(parry)}")
        print(f"powerstance (same weapon both hands, 1H build) against the same row without it")
        print(f"  {'weapon':<30}{'PS':>8}{'1H':>8}{'best dual engagement':>28}")
        for r in ps[:a.top]:
            m = r["moveset_ps"]
            dual = [f for n, f in m["families"].items() if f["opener"].startswith("dual_")]
            d = max(dual, key=lambda f: f["score"]) if dual else None
            print(f"  {r['weapon'][:29]:<30}{m['score']:>8.0f}{r['moveset']['score']:>8.0f}"
                  f"{(d['opener'] + ' ' + format(d['score'], '.0f')) if d else '-':>28}")
    for w in a.weapon:
        for r in results:
            if r["weapon"].lower() != w.lower():
                continue
            print(f"\n{r['weapon']} {'2H' if r['two'] else '1H'}: {r['moveset']['score']:.0f}")
            for key in ("moveset", "moveset_ps"):
                for n, f in (r.get(key) or {}).get("families", {}).items():
                    print(f"  {key:<10} {n:<5} {f['opener']:<11} depth {f['depth']} score {f['score']:.0f} "
                          f"dmg {f['dmg']:.0f} commit {f['detail']['commit']} share {100 * f.get('share', 0):.0f}%")
    if a.calibrate:
        print("\nSpearman rho of the weapon score against primary adoption (all sweep weapons / adopted only)")
        for name, rho_all, rho_ad in calibrate(pvp, results, shares):
            print(f"  {name:<22}{rho_all:+.3f}  {rho_ad:+.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
