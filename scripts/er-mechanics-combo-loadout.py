#!/usr/bin/env python3
"""A paged weapon inventory from an `er-mechanics-ash-table.py --build ... --variants --json` sweep.

    python3 scripts/er-mechanics-combo-loadout.py <rows.json> --out <plan.json> --html <page.html>

The in-game weapon grid is viewed in acquisition order, which is the planner's inventory order
(user, 2026-10-08): slot i sits in row i // 5 and page i // 30. So the plan is written in grid order:

- a row is 5 copies of one weapon, each with a different ash or infusion, starting on a multiple of 5;
- a theme starts on a page, a multiple of 30;
- a row the weapon cannot fill with distinct ashes repeats its best copy;
- before each theme, a grab weapon (`grab_copies`) fills the rest of the page and one whole page
  more: one important copy repeated so it is under the cursor anywhere on that page, and a landmark
  for where the next theme starts. Grabs left over after the last theme get a page each.

How a copy is valued (`value`): the sweep's `score` (damage per second; for a quick weapon the ash
plus its R1 string, `er-mechanics-ash-table.QUICK_R1_CANCEL`; for a step or buff the fastest R1 out
of it; a buff, Cragblade's too, adds the damage it adds on its carrier over the same clock,
`er-mechanics-ash-table.BUFF_MODEL`, filled in by `set_buff_values` for an older sweep), times (damage + status HP) / damage, where status HP is each hit's build-up spread over the
60 VIG RL150 target's gauge. `norm` is value over the best value of its category, so a step, a
bullet and a blade ash compete on one scale. The opponent's replies multiply in: interrupt and
whiff (default on), parry (`--parry`) and perfect block (`--perfect-block`,
`er-mechanics-perfect-block.py`), each with an opt-out flag.

Themes, in grid order:

1. class pairs: per weapon class the two weapons with the best five-copy rows. The pair's ten
   copies are ten different ashes, assigned between the two weapons by an exact maximum-weight
   assignment, so which weapon gets which ash is decided by where it is worth more.
2. mobility: the weapons public builds put Endure, Quickstep and Bloodhound's Step on (the planner
   mirror, `MOBILITY_ASHES`), each row led by its step and buff ashes, best first.
3. AOE: the `--aoe-top` ashes with the largest measured hit area, grouped onto the fewest weapons
   (Waves of Darkness on its hyperarmor carrier).
4. status: weapons with an innate status, each row its five best (infusion, ash) copies counting
   status HP.

After Status, with no landmark before them (`--shields`, `--ranged`, both on with `--build`;
`er-mechanics-shields-ranged.py` has the scores):

5. Shields (`er-mechanics-shields-ranged.shield_plan`): the medium shield that blocks most of
   the PvP corpus's opening hit per unit of weight and keeps the first Combos weapon at medium
   roll, with No Skill so L2 stays the main hand's; then one parry copy per job (melee, spells),
   its shield infused against the job's element. With `--parry`, a dagger, fist or other
   non-shield parry tool replaces the melee copy when it answers the corpus's openings better
   (`parry_tool_copy`).
6. Ranged: one copy per viable pick of `data/ranged-picks.toml` the build wields, with its own
   skill or its assigned ash (`er-mechanics-shields-ranged.ranged_plan`), and the four equipped
   ammunition names (`ammo`).

Last, the greatbow page (`FALL_CANCEL_NOTE`): a whole page of the ranged plan's greatbow, the one
the build wields best, inserted at the page boundary nearest the middle of the finished plan.

Equip sets: the cross-class `COMBOS`, right hand 1-3 = the first three copies of the right weapon's
row, left hand 1-3 = the left weapon's; then, with `--shields`, the first combo's right hand with
the block shield in left hand 1.

Writes {"slots": [...], "sets": [...], "ammo": {...}} for `planner-ash-loadout-upload.py --plan`.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import html
import importlib.util
import json
import math
import os
import statistics
import sys
import time
import tomllib
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
BLACKLIST = HERE.parent / "data" / "ash-blacklist.toml"
PREFS = HERE.parent / "data" / "ash-preferences.toml"
ROW, PAGE = 5, 30
#: Frames per second of every frame count in a sweep row (TimeAct frames).
FPS = 30.0
CATEGORIES = ("bullet", "non-bullet", "melee bullet", "utility")
MIRROR = Path.home() / ".cache/er-build-planner/builds.jsonl"
MOBILITY_ASHES = ("Endure", "Quickstep", "Bloodhound's Step")
#: `norm` divides by the category's 10th best ash (the top-10 pool the earlier loadouts used).
NORM_RANK = 10
#: Step and buff copies per class-pair row; the mobility theme carries the rest.
PAIR_UTILITY = 1
STATUS_AFFINITIES = ("Blood", "Cold", "Poison")
STATS = ("str", "dex", "int", "fth", "arc")
CORRECT = {"str": "correctStrength", "dex": "correctAgility", "int": "correctMagic", "fth": "correctFaith",
           "arc": "correctLuck"}
#: A grab page's damaging ash: hyperarmor from this frame or sooner, first hit by this frame.
GRAB_HA_START = 12
GRAB_FIRST_HIT = 30
#: `off_stat`: on the STR 18 / DEX 80 sweep the bullets split cleanly, 4-7% (Pebble, Sacred Blade,
#: Flame Spear, Blood Blade, Sovereignty, Flaming Strike) against 28%+ (Hoarfrost Stomp, Stormcaller,
#: Beast's Roar). Waves of Darkness is kept for the ledge waves and its hyperarmor (user, 2026-10-08).
OFF_STAT_SHARE = 0.10
OFF_STAT_KEEP = {"Waves of Darkness"}
# How far below the winner a dominated ash may sit on one measure and still count as beaten: Cold
# Carian Greatsword builds 4 more frost HP than Carian Grandeur (1353 vs 1349) on a Great Katana
# while losing on every other measure.
DOMINANCE_TOLERANCE = 0.02
# Share of the damaging ashes kept, best first, ranked by each ash's best value anywhere in the
# pool. The user picked rank 27 of the DEX build's 56-ash pool (2026-10-08: 31, then 15, then 25,
# then "lets get 27 then", to bring back Spinning Slash and Blood Tax), and meant it as a share of
# the pool, not a count (2026-10-08: "I meant in relation to the denominator, generalized as a
# percentage"): the STR build's pool is 43, and a count of 27 kept Loretta's Slash at 23rd.
ASH_RANK_KEEP_SHARE = 0.50  # user, 2026-10-09: "Lets keep the top 50%" (was 27/56, 48.2%)
#: Per-ash carrier limits: (latest first-hit frame, reach that must be exceeded in m). Spinning
#: Slash opens at f26/f27 on the heavy and polearm classes and reaches 3.3-4.0 m on most f18
#: classes; only the middle of the curve is wanted (user, 2026-10-08: "f26 is too late and I
#: think 4.0 meters is too short"). Stamp (Sweep) lands at f19 on every carrier, so the same limits
#: put it on the long heavy weapons Spinning Slash is too slow on (user, 2026-10-08).
ASH_CARRIER_LIMITS = {"Spinning Slash": (25, 4.0), "Stamp (Sweep)": (25, 4.0)}
#: Frames a "2H" row loses before its skill can start: the copy is swapped to one-handed and must
#: be two-handed first (user, 2026-10-08). c0000.hks `ExecHandChange` sends a right-hand
#: one-hand -> two-hand request through `HandChangeStart` (`LEFT_TO_{WAIST,BACK,SHOULDER,SPEAR}`,
#: clips a000_029060-029063) and then `HandChangeEnd` (`BOTH_FROM_ALL`, a0xx_029070), the start
#: leaving on `env(IsAnimEnd, 1)` (`HandChangeStart_Upper_onUpdate`). The start clips have
#: TimeAct entries but no hkx in any c0000 binder, and no cancel window either, so the start ends
#: at once (inferred: no clip, nothing to play). In a0xx_029070 the skill cancel (jump table 104)
#: and every other cancel open at real frame 7, identical in all seven idle categories that own the
#: clip (a000, a010, a012-a016, read with `er-mechanics-swap-windows.py`), so there is one number,
#: not one per class. The swap itself costs 12 frames before any skill, one- or two-handed
#: (a0xx_029000-029003 is 5 frames with no cancel, then a0xx_029020-029023 opens the skill and
#: two-hand cancels at 7), and is not added here because it is the same for every row.
GRIP_CHANGE_FRAMES = 7
#: Classes section: an ash goes on at most this many weapons before each further carrier costs
#: `CLASS_ASH_PENALTY` of `norm` more than the last (coordinator, 2026-10-08: Beast's Roar,
#: Hoarfrost Stomp, Thunderbolt and Quickstep were on ~30 rows).
CLASS_ASH_CAP = 3
CLASS_ASH_PENALTY = 1.0
CLASS_UTILITY = set(MOBILITY_ASHES) | {"Parry"}
#: A class row is shared by the class's main-hand pick and the other weapons whose class-roles
#: `main_hand` score is within `MAIN_HAND_NEAR` of the pick's, `MAIN_HAND_ROW` weapons at most, each
#: on its own best main-hand affinity and one copy each (user, 2026-10-12: "the top three mainhand
#: scoring weapons can be represented in a row like this as long as their scores are this close to
#: eachother", of Heavy Flamberge 0.989, Blood Iron Greatsword 0.976 and Blood Gargoyle's
#: Greatsword 0.974, with Blood Flamberge's 0.953 an outlier).
MAIN_HAND_NEAR = 0.02
MAIN_HAND_ROW = 3
#: The Endure page and the equip sets' second right-hand weapon: the best Endure copy (`value`)
#: on a weapon no heavier than this, so that every equip set still rolls medium with it (user,
#: 2026-10-12: "a page of the best endure weapon despite number of endure's presence already. My
#: suggestions are star fist, spiked caestus, raptors talons, misericorde. Something light. We
#: should also make sure the equipped weapons has one of these equipped in the second mainhand
#: slot"). 3.0 is Star Fist, the heaviest of the four.
ENDURE_MAX_WEIGHT = 3.0
#: The greatbow page: a whole page of the build's greatbow (the ranged plan's pick), placed at the
#: page boundary nearest the middle of the plan, so a mid-fall menu swap lands on one anywhere on it
#: (user, 2026-10-12: "a whole page of greatbows near the middle of the whole set of pages"). The
#: reason is a landing cancel: c0000.hks `ExecArrowBothJumpLandAttack` runs only for a two-handed
#: LARGE_ARROW or BALLISTA, and in game (run q, 2026-10-12, a 13 m drop, `IsMoveCancelPossible`
#: read every frame with no movement input) an in-air R1 with the Greatbow could move 49 frames
#: after the fall began, against 101 and 107 for the same fall with nothing done, about 52-58
#: frames saved. The Hand Ballista and Jar Cannon land into a firing clip and could move at 132-133,
#: later than doing nothing, so the page is greatbows only.
FALL_CANCEL_NOTE = ("fall cancel: in-air R1 with a greatbow, can move 2 frames after touchdown "
                    "instead of 53-56 (measured on a 13 m drop)")


def near_main_hand(roles, v):
    """Whether class-roles weapon entry `v` is a wieldable main hand within `MAIN_HAND_NEAR` of its
    class's main-hand pick."""
    mh = (roles["classes"].get(v["class"]) or {}).get("main_hand")
    s = (v.get("main_hand") or {}).get("score")
    return bool(mh and v["wieldable"] and s is not None and mh["score"] - s <= MAIN_HAND_NEAR)
#: Copies in a landmark block: five whole rows.
LANDMARK = 25
#: `EquipParamWeapon.wepType` -> class, read off the build's own weapons (Wakizashi 1 dagger,
#: Butchering Knife 19 greataxe, Guardian's Swordspear 29 halberd, Grave Scythe 31 reaper, ...).
CLASS = {1: "Dagger", 3: "Straight Sword", 5: "Greatsword", 7: "Colossal Sword", 9: "Curved Sword",
         11: "Curved Greatsword", 13: "Katana", 14: "Twinblade", 15: "Thrusting Sword",
         16: "Heavy Thrusting Sword", 17: "Axe", 19: "Greataxe", 21: "Hammer", 23: "Great Hammer",
         24: "Flail", 25: "Spear", 28: "Great Spear", 29: "Halberd", 31: "Reaper", 35: "Fist",
         37: "Claw", 39: "Whip", 41: "Colossal Weapon", 90: "Thrusting Shield", 92: "Backhand Blade",
         93: "Light Greatsword", 94: "Great Katana", 95: "Beast Claw"}
COMBOS = [("Greataxe", "Greataxe"), ("Axe", "Halberd"), ("Dagger", "Halberd"),
          ("Thrusting Sword", "Halberd"), ("Heavy Thrusting Sword", "Halberd"), ("Reaper", "Dagger")]
#: Reach an ash adds beyond the carrier's own R1 #1 (user, 2026-10-09: "Model that next", after
#: Storm Blade's 18.7 m counted the same on a 2.9 m hatchet as on a 4 m halberd). `reach_w` =
#: 1 + `REACH_GAIN_K` x max(0, ln(ash reach / weapon R1 #1 world reach)). The logarithm makes each
#: doubling of reach over the weapon's worth the same step wherever it starts, and an ash shorter
#: than the R1 gains nothing, because the R1 already covers that range. `REACH_GAIN_K` sizes the
#: term to `area_w`: on the STR pool (15,744 rows) the log gain runs 0 to 1.80 from the 10th to the
#: 90th percentile and `area_w` 0.89 to 1.10, so 0.10 gives the reach term the same 0.2 spread
#: (1.00 to 1.18). Raised to 0.30 (1.00 to 1.54), the larger factor now, for Sword Dance's 6.1 m on
#: a 3.6 m Shamshir (user, 2026-10-12). It lifts the bullets most (Thunderbolt x1.74, Storm Blade
#: x1.50, Sword Dance x1.16), which the user accepted: "storm blade and thunder bolt can be more
#: powerful".
REACH_GAIN_K = 0.30
#: Hyperarmor that holds through every hit of the cast (`er-mechanics-ash-table` `covers`). User,
#: 2026-10-12, on Sword Dance over Double Slash on a Heavy Shamshir:
#: "if we have the chance to give ourselves hyperarmor with such a light weapon AND reach that far"
#: `ha_w` = 1 + `HA_GAIN_K` x the longsword R1s the window absorbs (`ha_r1s`), at
#: most `HA_MAX_R1S`. The interrupt multiplier already counts the window, but only against an
#: opponent who reacts to the cast after it starts, by when a 20-frame ash has mostly landed, so it
#: gave Sword Dance's 236 poise 1.5% over Double Slash's none on the Shamshir.
HA_GAIN_K = 0.10
HA_MAX_R1S = 2
#: Ranged status proc (user, 2026-10-09: "I usually see users with projectile ashes on short weapons
#: or mists so they can proc status quickly before switching back"). The status a cast's bullets
#: build on a target standing just outside the carrier's R1 reach is valued per second up to its
#: last status hit (`set_ranged_status`). A cloud that stays put and re-hits (the mists' 2411 and
#: 2416: penetrating, 4 s life, 0.7 s hit record) hits once on contact and again every hit record
#: while the target is inside it; the target is taken to leave after `MIST_DWELL_S`, one roll out
#: of a 2.5 m cloud (inferred, not measured).
MIST_DWELL_S = 1.0
#: A bullet whose path spans less than this is a cloud that stays where it was made, in m.
STATIONARY_M = 0.5
RANGED_CACHE = Path.home() / ".cache/er-build-planner/ash-ranged-status.json"
#: The status each status affinity adds to a weapon's hits.
AFFINITY_STATUS = {"Blood": "bleed", "Cold": "frost", "Poison": "poison"}
#: An ash whose own bullets build a status is dropped once more than this share of the plan's weapon
#: copies already build that status from the weapon itself, by affinity or innately (user,
#: 2026-10-17: "I don't think we need hoarfrost stomp on a build that has more than 50% weapons
#: with frost"). `drop_redundant_status` replaces each such copy in place.
OWN_STATUS_SHARE = 0.5
#: The crit row (`--crit-weapon`): one row of this weapon on the affinity whose riposte plus
#: backstab, damage and the status the crit's hits build, is highest at the build's stats (user,
#: 2026-10-17: "we must have a row of misericordes with the best infusion for its crit").
CRIT_WEAPON = "Misericorde"
BUFF_CACHE = Path.home() / ".cache/er-build-planner/ash-buff-value.json"
#: Multihit weapons (user, 2026-10-12): "we don't want to include ashes on it that slow the user
#: down significantly unless they are also multihit", where "Multihit is defined by how quickly it
#: can trigger a talisman or effect related to multiple connected attacks in a duration".
#: The effect is the successive-hit counter `MULTIHIT_TALISMAN` reads
#: (`er-mechanics-talisman-affinity.py`, `VERIFIED`). A weapon's rank is the share of the pool's
#: weapons slower than it to that talisman's first threshold from a cold start, in its fastest
#: grip (one-, two-handed or powerstanced). An ash's rate is the counter it adds per cast over the
#: frames until the weapon can act again (`gate`, or the recast `skill_q` gate when it has an R2
#: follow-up). `mh_w` = 1 - `MULTIHIT_K` x rank x the ash's shortfall against the weapon's best
#: string rate, floored at 0, so Bloody Slash (no counter) on Milady (17 counter/s 1H, 22 powerstanced) loses
#: `MULTIHIT_K` x its rank of its value, at most all of it, and an ash on a slow single-hit weapon
#: loses next to nothing.
#: Raised from 0.5 (user, 2026-10-12: "raise multihit_k until double slash or spinning slash outpace
#: phantim slash on twinblades"): on the STR pool (rank 0.737 for both twinblades) Double Slash passes
#: Phantom Slash above 1.646 on Twinned Knight Swords and 1.726 on the Twinblade, Spinning Slash above
#: 1.96 and 1.978. At 1.75 an ash with a shortfall above 1 / (1.75 x rank) is worth nothing on that
#: weapon (`mh_w` floors at 0): every no-counter ash on a twinblade.
MULTIHIT_K = 1.75
MULTIHIT_TALISMAN = "Winged Sword Insignia"
COUNTER_CACHE = Path.home() / ".cache/er-build-planner/ash-counter-rate.json"


def raw_value(x):
    """The copy's damage per second plus its status HP per second, times `area_w`, `reach_w` and
    `ha_w`.

    Status HP (`er-mechanics-ash-table.status_hp`, plus `status_only`, the build-up of the cast's
    no-damage bullets that the sweep's hits leave out, the mists' clouds) is read on two clocks
    and the faster one is kept:

    - the string's: status x `score` / the string's damage, which is status per second of the
      string. The status HP always counts the cast plus the string's R1s, so it is weighed against
      the string's damage whenever there is a string: weighing it against the cast alone inflated
      status copies (Keen-less Spinning Slash on a Great Katana, 2026-10-08: 660 HP over a 283
      cast for x3.3, against x1.35 over the 1873 string);
    - the ranged proc's: `ranged` HP over `ranged_t` frames, the status the cast's bullets build
      on a target just outside the carrier's R1 reach, per second to the last of those hits
      (`set_ranged_status`), the cast thrown and the weapon switched back (user, 2026-10-09).

    Taking the faster rather than the sum counts each point of build-up once: a bullet's status
    is in both readings."""
    return ((x["score"] + status_rate(x)) * x.get("area_w", 1.0) * x.get("reach_w", 1.0) * ha_w(x)
            * x.get("land_w", 1.0))


def ha_w(x):
    """`HA_GAIN_K` per longsword R1 a hyperarmor window covering the whole cast absorbs."""
    return 1 + HA_GAIN_K * min(x.get("ha_r1s", 0), HA_MAX_R1S) if x.get("covers") else 1.0


def status_rate(x):
    """The status half of `raw_value` before `area_w` and `reach_w`: the faster of the string's
    and the ranged proc's status HP per second."""
    base = (x.get("string") or {}).get("dmg") or x["dmg"]
    status = sum((x.get("status_hp") or {}).values()) + sum((x.get("status_only") or {}).values())
    # The clock rate without the buff's added damage (`er-mechanics-ash-table.apply_buff`): `base`
    # does not hold it.
    plain = x["score"] * (1.0 - (x.get("buff") or {}).get("share", 0.0))
    string_rate = plain * status / base if base else 0.0
    ranged = sum((x.get("ranged") or {}).values())
    ranged_rate = ranged / (x["ranged_t"] / FPS) if ranged and x.get("ranged_t") else 0.0
    return max(string_rate, ranged_rate)


def ash_reach(x):
    """How far the ash itself lands, in m: a bullet's farthest reach, a melee bullet's farther of
    its bullet and its blade, any other ash its blade's world reach."""
    bullet = (x.get("reach") or [0, 0])[1] or 0.0
    blade = (x.get("melee_reach") or [0])[0] or 0.0
    return bullet if x["cat"] == "bullet" else max(bullet, blade) if x["cat"] == "melee bullet" else blade


def r1_reaches(roles, embed):
    """{(weapon, grip): R1 #1 world reach in m}. From `--embed` (`er-mechanics-weapon-embed.py`
    output) per grip when given; otherwise the roles' `features.r1_reach`, the longer grip's,
    for both grips (1H and 2H differ by 0.05-0.15 m: Hand Axe 3.58 / 3.43, Halberd 4.49 / 4.58).
    Whips and flails have no measured R1 reach and are left out."""
    out = {}
    for w, v in ((roles or {}).get("weapons") or {}).items():
        r = (v.get("features") or {}).get("r1_reach")
        if r:
            out[w, "1H"] = out[w, "2H"] = r
    if embed:
        for x in json.loads(embed.read_text())["weapons"]:
            r = (x.get("summary") or {}).get("r1_reach")
            if r and x["grip"] in ("1H", "2H"):
                out[x["weapon"], x["grip"]] = r
    return out


def weapon_traits(embed):
    """{weapon: {"anims", "innate", "infusable"}} from `--embed`: the right-hand attack animations
    over both grips (a weapon's moveset; they do not change with the affinity), the statuses it
    builds up on an affinity that adds none (its innate status), and whether it takes more than
    one affinity (the embedding carries every affinity a weapon can take)."""
    out = {}
    if not embed:
        return out
    for x in json.loads(embed.read_text())["weapons"]:
        t = out.setdefault(x["weapon"], {"anims": set(), "innate": set(), "affs": set()})
        t["affs"].add(x["affinity"])
        for k, s in x["slots"].items():
            if x["grip"] in ("1H", "2H"):
                t["anims"].add((x["grip"], k, s.get("anim")))
            if x["affinity"] not in STATUS_AFFINITIES:
                t["innate"] |= {n for n, v in (s.get("status_hp") or {}).items() if v}
    return {w: {"anims": frozenset(t["anims"]), "innate": t["innate"], "infusable": len(t["affs"]) > 1}
            for w, t in out.items()}


def carried_status(s, traits):
    """The statuses a copy builds from the weapon itself: its affinity's and its innate ones."""
    own = {AFFINITY_STATUS[s["affinity"]]} if s["affinity"] in AFFINITY_STATUS else set()
    return own | set((traits.get(s["weapon"]) or {}).get("innate") or ())


def status_shares(slots, traits):
    """({status: share of the plan's weapon copies that carry it}, copies). Every slot is a weapon
    the inventory holds, a landmark's repeats included; fun and padding slots are left out. On the
    Warped Axe Cold plan (2026-10-17) frost is 52.3% of slots against 48.5% of distinct (weapon,
    affinity, ash) copies, so the measure decides the verdict there; slots is what the inventory
    shows."""
    copies = [s for s in slots if s["cat"] not in ("junk", "fun")]
    count = collections.Counter(st for s in copies for st in carried_status(s, traits))
    return {k: v / len(copies) for k, v in count.items()} if copies else {}, len(copies)


def drop_redundant_status(slots, own, traits, pool_of, make_slot, share=OWN_STATUS_SHARE):
    """Replace in place every copy whose ash builds a status (`own`, `ash_own_status`) that more
    than `share` of the plan's copies already carry (`status_shares`). The replacement is the same
    weapon's best other copy by `norm`, of the same kind (a step or buff for a step or buff), on
    the copy's own affinity when the pool has one, with an ash its five-slot row does not hold yet;
    failing that, any kind, then an ash the row already holds.
    A landmark or page block takes one replacement for all its slots, so it stays one copy repeated.
    `pool_of(slot)` lists the copies that may take the slot. Slot indices do not move, so the equip sets keep pointing at the same slots.

    Returns (shares, {status: [ashes dropped]}, [log lines])."""
    shares, _ = status_shares(slots, traits)
    full = {k for k, v in shares.items() if v > share}
    redundant = {ash: sorted(sts & full) for ash, sts in own.items() if sts & full}
    dropped, log, block = collections.defaultdict(set), [], {}
    for i, s in enumerate(slots):
        ash = s.get("skill")
        if ash not in redundant:
            continue
        grab = s.get("grab") or s["theme"].startswith(("Landmark", "Page"))
        bk = (s["theme"], s["weapon"], s["affinity"], ash)
        if grab and bk in block:
            x = block[bk]
        else:
            r0 = i // ROW * ROW
            taken = set() if grab else {t.get("skill") for t in slots[r0:r0 + ROW]}
            util = s["cat"] == "utility"
            ok = [x for x in pool_of(s) if x["ash"] not in redundant]
            # Loosened in turn when a weapon's pool runs short: another kind (a step for a bullet),
            # then an ash the row already holds, a short row repeating a copy as `place_classes` does.
            tiers = ([x for x in ok if x["ash"] not in taken and (x["cat"] == "utility") == util],
                     [x for x in ok if x["ash"] not in taken], ok)
            cands = next((c for c in tiers if c), [])
            x = max(cands, key=lambda x: (x["affinity"] == s["affinity"], x["norm"]), default=None)
            if grab:
                block[bk] = x
        for st in redundant[ash]:
            dropped[st].add(ash)
        if x is None:
            log.append(f"kept {s['affinity']} {s['weapon']} {ash} at slot {i}: no other copy of the weapon")
            continue
        new = make_slot(x, s["weapon"], s["theme"], s["row"])
        for k in ("note", "partner", "grab"):
            if k in s:
                new[k] = s[k]
        slots[i] = new
        log.append(f"slot {i}: {s['affinity']} {s['weapon']} {ash} -> {x['affinity']} {x['ash']} "
                   f"(frees {', '.join(redundant[ash])})")
    return shares, {k: sorted(v) for k, v in dropped.items()}, log


def ash_own_status(rows, stats, cache_path):
    """{ash: statuses its own bullets build}, whatever the carrier: every bullet node of every
    animation of the cast (`er-mechanics-ashes.fly_tree`, as `set_ranged_status` walks them), read
    with `Table.hit_buildup` against the carrier's status rows emptied, so only the Bullet rows' own
    `spEffectId0..4` count (hit slots 0..4; Hoarfrost Stomp's 1800/1801 carry no
    `isUseStatusAilmentAtkPowerCorrect`, `VERIFIED` in that docstring). A Cold carrier's frost riding
    a bullet is the weapon's, not the ash's, and is not counted. An ash with no bullet builds no
    status of its own here: a blade ash's own AtkParam status rows are not read. One carrier per ash
    (the first damaging row): a bullet's own rows do not depend on the weapon (`INFERRED`: the
    carrier only picks the opening animation). Cached in `cache_path` under `own|` keys."""
    first = {}
    for x in rows:
        if x["cat"] != "utility":
            first.setdefault(x["ash"], x)
    key = lambda x: f"own|{x['ash']}|{x['weapon']}|{x['affinity']}|{x['grip']}"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    todo = [x for x in first.values() if key(x) not in cache]
    if todo:
        T = _table_mod()
        A = T.A
        tab = T.Table(todo[0]["weapon"], todo[0]["affinity"], stats, DEFAULT_ARMOR, True)
        ST, st, _ = tab.status_tables()
        t = tab.t
        sids = skill_ids(t, {x["ash"] for x in todo})
        print(f"own status: {len(todo)} ashes to read", file=sys.stderr, flush=True)
        for x in todo:
            got, sid = set(), sids.get(x["ash"])
            if sid is not None:
                wid = t.find_weapon(x["weapon"])
                level = tab.art.max_level(t.reg.weapon[wid]["reinforceTypeId"])
                two = x["grip"] == "2H"
                prof = A.skill_profile(t, sid, wid, level, "both" if two else "one")
                ws = dict(ST.weapon_status(st, x["weapon"], x["affinity"], level, stats, two), sources=[])
                for evs in prof["anims"].values():
                    for e in evs:
                        if e["kind"] == "bullet" and e.get("bullet"):
                            for node, *_ in A.fly_tree(t, e["bullet"]):
                                b, _ = tab.hit_buildup(st, ws, node["atk_row"], node["bullet"])
                                got |= {k for k, v in b.items() if v}
            cache[key(x)] = sorted(got)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache))
    return {ash: set(cache[key(x)]) for ash, x in first.items()}


def crit_affinities(weapon, stats):
    """[(affinity, riposte, backstab)] for `weapon` at `stats`, best first: each crit's total
    (`er-mechanics-crit-affinity.rows_for`: damage through `er-mechanics-crits.reference_defender`
    plus the status HP of the crit's hits), the better grip per affinity. An affinity whose
    requirements the stats miss is not listed. The crit multiplier skips status build-up
    (crits.md section 2, `VERIFIED`); that a crit hit still builds the weapon's status at one R1
    hit's worth per hit is that module's model (`INFERRED`: the crit AtkParam is taken through the
    same hit slots 5..7 as any swing)."""
    spec = importlib.util.spec_from_file_location("er_mechanics_crit_affinity", HERE / "er-mechanics-crit-affinity.py")
    CA = sys.modules.get("er_mechanics_crit_affinity")
    if CA is None:
        CA = importlib.util.module_from_spec(spec)
        sys.modules["er_mechanics_crit_affinity"] = CA
        spec.loader.exec_module(CA)
    e = CA.SS.embed()
    t = CA.CR.load_tables()
    base = next((i for i in CA.CW.weapon_ids(e) if e.art.names[i] == weapon), None)
    if base is None:
        return []
    best = {}
    for r in CA.rows_for(t, e, CA.CR.reference_defender(t), base, stats):
        v = (r["riposte"]["total"], r["backstab"]["total"])
        if sum(v) > sum(best.get(r["affinity"], (0, 0))):
            best[r["affinity"]] = v
    return sorted(((a, *v) for a, v in best.items()), key=lambda r: -(r[1] + r[2]))


def crit_copies(weapon, affinity, pool, mountable, note):
    """(row, every candidate): `weapon`'s distinct ashes that mount on `affinity` (`mountable(ash)`),
    each its best copy by `norm` made on `affinity` whatever affinity the sweep valued it on. The
    row is the best `ROW` of them with one step or buff at most, as a class pair's row is drawn
    (`PAIR_UTILITY`), damaging ones first."""
    ok = [x for x in pool if x["weapon"] == weapon and mountable(x["ash"])]
    cands, seen = [], set()
    for x in sorted(ok, key=lambda x: -x["norm"]):
        if x["ash"] not in seen:
            seen.add(x["ash"])
            cands.append(dict(x, affinity=affinity, note=f"{note}; ash valued on the {x['affinity']} sweep"
                              if x["affinity"] != affinity else note))
    row, util = [], 0
    for x in cands:
        if x["cat"] == "utility" and util >= PAIR_UTILITY:
            continue
        util += x["cat"] == "utility"
        row.append(x)
        if len(row) == ROW:
            break
    return sorted(row, key=lambda x: (CATEGORIES.index(x["cat"]), -x["norm"])), cands


def set_reach_weights(rows, r1):
    """`reach_w` on every damaging row with a measured carrier R1 reach (`REACH_GAIN_K`), plus the
    same step for the ash's own reach against its longest carrier in the pool.

    The first term alone ranks a blade ash on a short weapon as high as on a long one: Repeating
    Thrust reaches 5.9 m on a 2.8 m Bloodstained Dagger and 7.0 m on a 3.3 m Sword Lance, both a
    ratio of 2.1, so both got x1.22, though the dagger falls 1.1 m short of the lance (user,
    2026-10-12: "it definitely gets better with range"). The second term,
    `REACH_GAIN_K` x ln(ash reach / the ash's longest reach on any carrier), is 0 on that
    carrier and negative below it."""
    best = collections.defaultdict(float)
    for x in rows:
        if x["cat"] != "utility":
            best[x["ash"]] = max(best[x["ash"]], ash_reach(x))
    for x in rows:
        w, a = r1.get((x["weapon"], x["grip"])), ash_reach(x)
        if x["cat"] != "utility" and w and a:
            x["reach_w"] = 1 + REACH_GAIN_K * (max(0.0, math.log(a / w)) + math.log(a / best[x["ash"]]))


def set_ranged_status(rows, r1, stats, cache_path):
    """`ranged`, `ranged_t` and `status_only` on every damaging row whose ash fires a bullet.

    The cast's opening animation's bullet events are walked through their whole tree
    (`er-mechanics-ashes.fly_tree`), each node's build-up read with
    `er-mechanics-ash-table.Table.hit_buildup` (the Bullet row's own status, plus the weapon's
    scaled by the bullet's AtkParam: Storm Blade's 2060 has scale 0, so a Blood carrier's bleed
    never rides it) and turned into HP with `Table.buildup_hp`.

    - `ranged`: nodes that touch a target standing at the carrier's R1 reach (its path within
      the node's radius of that distance), each bullet id once, a stationary cloud
      `1 + MIST_DWELL_S // hit record` times; `ranged_t` the frame of the last of those hits,
      plus the grip change of a 2H row. Whips have no R1 reach and get no ranged reading.
    - `status_only`: the no-damage nodes (the mists' carrier and cloud, Hoarfrost Stomp's 2268),
      at any distance, the same counts.

    Cached per row, R1 reach, stats and dwell in `cache_path`."""
    T = sys.modules.get("er_mechanics_ash_table")
    if T is None:
        spec = importlib.util.spec_from_file_location("er_mechanics_ash_table", HERE / "er-mechanics-ash-table.py")
        T = importlib.util.module_from_spec(spec)
        sys.modules["er_mechanics_ash_table"] = T
        spec.loader.exec_module(T)
    A = T.A
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    stats_key = ",".join(f"{k}{stats[k]}" for k in STATS)

    def key(x):
        return f"{x['weapon']}|{x['affinity']}|{x['ash']}|{x['grip']}|{r1.get((x['weapon'], x['grip']))}|{stats_key}|{MIST_DWELL_S}"

    todo = [x for x in rows if x["cat"] != "utility" and key(x) not in cache]
    tab = None
    if todo:
        tab = T.Table(todo[0]["weapon"], todo[0]["affinity"], stats, DEFAULT_ARMOR, True)
        ST, st, _ = tab.status_tables()
        t = tab.t
        sids = skill_ids(t, {x["ash"] for x in todo})
        events, ws_cache, fires = {}, {}, {}
        print(f"ranged status: {len(todo)} rows to read", file=sys.stderr, flush=True)
    for n, x in enumerate(todo):
        sid = sids.get(x["ash"])
        if sid is None or fires.get(sid) is False:
            cache[key(x)] = {}
            continue
        wid = t.find_weapon(x["weapon"])
        level = tab.art.max_level(t.reg.weapon[wid]["reinforceTypeId"])
        grip = "both" if x["grip"] == "2H" else "one"
        # The bullets a cast fires depend on the weapon only through its behavior variation and
        # the opening its class plays (`er-mechanics-ashes.resolve_judge`, `_opening`).
        ek = (sid, t.reg.weapon[wid]["behaviorVariationId"], A.weapon_diff_category(t, sid, wid, grip), level)
        if ek not in events:
            prof = A.skill_profile(t, sid, wid, level, grip)
            anim = A.main_anim(prof)
            evs = prof["anims"].get(anim, prof["anims"].get(str(anim), []))
            events[ek] = [(e["frames"][0], e["bullet"]) for e in evs if e["kind"] == "bullet" and e.get("bullet")]
            # An ash whose first carrier fires no bullet in any animation fires none anywhere.
            if sid not in fires:
                fires[sid] = any(e["kind"] == "bullet" for a in prof["anims"].values() for e in a)
        wk = (x["weapon"], x["affinity"], grip)
        if wk not in ws_cache:
            ws_cache[wk] = ST.weapon_status(st, x["weapon"], x["affinity"], level, stats, grip == "both")
        ws, reach = ws_cache[wk], r1.get((x["weapon"], x["grip"]))
        ranged, ranged_first, only, only_first, last = {}, {}, {}, {}, 0.0
        seen_ranged, seen_only = set(), set()
        for frame, bt in events[ek]:
            for node, path, t0, _ in A.fly_tree(t, bt):
                b, first = tab.hit_buildup(st, ws, node["atk_row"], node["bullet"])
                b = {k: v for k, v in b.items() if v}
                if not b:
                    continue
                r = t.bullet[node["bullet"]]
                ds = [math.hypot(p[0], p[2]) for p in path]
                rec = r["dmgHitRecordLifeTime"]
                still = max(ds) - min(ds) < STATIONARY_M and r["isPenetrateChr"] and rec > 0

                def hits(i):
                    left = max(0.0, r["life"] - i / A.BULLET_FPS)
                    return 1 + int(min(MIST_DWELL_S, left) / rec) if still else 1
                if not A._bullet_damages_self(node) and node["bullet"] not in seen_only:
                    seen_only.add(node["bullet"])
                    for k, v in b.items():
                        only[k] = only.get(k, 0) + v * hits(0)
                        only_first.setdefault(k, first[k])
                i = next((i for i, d in enumerate(ds) if abs(d - reach) <= node["radius_m"]), None) if reach else None
                if i is not None and node["bullet"] not in seen_ranged:
                    seen_ranged.add(node["bullet"])
                    k_hits = hits(i)
                    for k, v in b.items():
                        ranged[k] = ranged.get(k, 0) + v * k_hits
                        ranged_first.setdefault(k, first[k])
                    last = max(last, frame + FPS * (t0 + i / A.BULLET_FPS + (k_hits - 1) * rec))
        out = {}
        hp = tab.buildup_hp(ws, ranged, ranged_first)
        if hp:
            out["ranged"], out["ranged_t"] = hp, round(last, 1)
        hp = tab.buildup_hp(ws, only, only_first)
        if hp:
            out["status_only"] = hp
        cache[key(x)] = out
        if n and n % 500 == 0:
            print(f"  {n}/{len(todo)} rows", file=sys.stderr, flush=True)
    if todo:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache))
    for x in rows:
        got = cache.get(key(x)) if x["cat"] != "utility" else None
        for k, v in (got or {}).items():
            x[k] = v
        if "ranged_t" in x:
            x["ranged_t"] = x["ranged_t"] + x.get("grip_change", 0.0)


def _table_mod():
    T = sys.modules.get("er_mechanics_ash_table")
    if T is None:
        spec = importlib.util.spec_from_file_location("er_mechanics_ash_table", HERE / "er-mechanics-ash-table.py")
        T = importlib.util.module_from_spec(spec)
        sys.modules["er_mechanics_ash_table"] = T
        spec.loader.exec_module(T)
    return T


def skill_ids(t, names):
    """{ash name: SwordArtsParam id} the way the sweep resolves a name (`find_arts`). A name to id
    map built over every row is wrong for the 12 names two rows share: Spinning Slash is 103 and
    also 1166, whose opening applies Ice Lightning Sword's 1890, so the last row won."""
    out = {}
    for n in names:
        try:
            out[n] = t.find_arts(n)
        except (SystemExit, KeyError):
            pass
    return out


def set_buff_values(rows, stats, cache_path):
    """Value the buff of every row of a sweep made before `er-mechanics-ash-table.BUFF_MODEL`
    existed, so no re-sweep is needed: the hits the row's clock holds are re-read
    (`Table.r1_slots`, through `buff_hits`) and `Table.buff_gain` adds the buff's damage on that
    carrier, folded in by `apply_buff`. A row that already carries `buff` (a newer sweep) is left
    as it is, and so is a step. Run before `add_grip_change`: the clock it divides by is the
    cast's.

    Which ashes buff is read once per ash on its first carrier (`er-mechanics-ashes.skill_buffs`
    on that weapon's opening); only those ashes' rows are re-read. A utility row's follow-up is
    recomputed too, and a row more than 1 HP off the sweep's is counted and reported: it means the
    sweep was made at other stats than `stats`. Cached per row and stats in `cache_path`."""
    T = _table_mod()
    stats_key = ",".join(f"{k}{stats[k]}" for k in STATS)

    def key(x):
        return (f"{T.BUFF_MODEL}|{x['weapon']}|{x['affinity']}|{x['ash']}|{x['grip']}|{x.get('follow_up')}|"
                f"{(x.get('string') or {}).get('r1s')}|{T.string_scored(x)}|{stats_key}")

    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    todo = [x for x in rows if "buff" not in x]
    miss = [x for x in todo if key(x) not in cache]
    if miss:
        tab = T.Table(miss[0]["weapon"], miss[0]["affinity"], stats, DEFAULT_ARMOR, True)
        A, t = T.A, tab.t
        sids = skill_ids(t, {x["ash"] for x in miss})
        buffs = {}
        for x in miss:
            sid = sids.get(x["ash"])
            if x["ash"] in buffs or sid is None:
                continue
            if t.arts[sid]["swordArtsTypeNew"] in T.ROLLING_ARTS:
                buffs[x["ash"]] = False
                continue
            try:
                prof = A.skill_profile(t, sid, t.find_weapon(x["weapon"]), 0, x["grip"] == "2H")
            except (SystemExit, KeyError, StopIteration, TypeError):
                continue
            buffs[x["ash"]] = any(s.get("effect") == "buff" for s in A.skill_buffs(t, prof))
        for x in miss:
            if not buffs.get(x["ash"]):
                cache[key(x)] = None
        miss = [x for x in miss if buffs.get(x["ash"])]
        print(f"buff values: {len(miss)} rows of {sum(map(bool, buffs.values()))} buff ashes to read",
              file=sys.stderr, flush=True)
        groups = collections.defaultdict(list)
        for x in miss:
            groups[(x["weapon"], x["affinity"])].append(x)
        off = 0
        for n, ((w, aff), xs) in enumerate(sorted(groups.items())):
            tab.set_weapon(w, aff)
            reads = {}
            for x in xs:
                two = x["grip"] == "2H"
                if two not in reads:
                    ctx, _ = tab.contexts(two)
                    reads[two] = (ctx, tab.r1_slots(two, ctx))
                ctx, slots = reads[two]
                bh = T.buff_hits(x, slots)
                if bh is None or (x["cat"] == "utility" and x.get("gate") is None):
                    cache[key(x)] = None
                    continue
                if x["cat"] == "utility" and abs(bh[1]["dmg"] - x["dmg"]) > 1:
                    off += 1
                prof = A.skill_profile(t, sids[x["ash"]], tab.wid, tab.level, two)
                cache[key(x)] = tab.buff_gain(sids[x["ash"]], prof, two, ctx, *bh)
            if n and n % 50 == 0:
                print(f"  {n}/{len(groups)} weapons", file=sys.stderr, flush=True)
        if off:
            print(f"buff values: {off} utility rows' follow-up differs from the sweep by more than 1 HP "
                  f"(sweep stats are not {stats_key}?)", file=sys.stderr)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache))
    for x in todo:
        got = cache.get(key(x))
        if got is not None:
            T.apply_buff(x, dict(got))


def set_buff_weights(rows):
    """`buff_w` on every buff row: the damage the buff adds on this carrier over the most it adds
    on any carrier in the pool. A buff's `score` is the follow-up it is timed on, buff included,
    and most of that follow-up is the carrier's own R1, which it hits without the buff too; so on
    a weak carrier the buff still scored like a step. Royal Knight's Resolve adds 280 HP on a Heavy
    Giant-Crusher and 29 on a Cold Great Knife, and Beast Claw's class row was five buffs and
    Endure (user, 2026-10-12: "I think that scales with AR, so on beast claw, its unacceptably
    bad"). The best carrier keeps its value, so the buffs' standing against the steps is
    unchanged there."""
    best = collections.defaultdict(float)
    for x in rows:
        add = (x.get("buff") or {}).get("added") or 0.0
        best[x["ash"]] = max(best[x["ash"]], add)
    for x in rows:
        add = (x.get("buff") or {}).get("added")
        if add is not None and best[x["ash"]] > 0:
            x["buff_w"] = add / best[x["ash"]]


def buff_report(rows):
    """Per buff ash, its kind and multiplier and the carriers it adds the most and least on."""
    xs = [x for x in rows if (x.get("buff") or {}).get("added") or (x.get("buff") or {}).get("status_hp")]
    by_ash = collections.defaultdict(list)
    for x in xs:
        by_ash[x["ash"]].append(x)
    for ash, ys in sorted(by_ash.items(), key=lambda kv: -max(y["buff"]["added"] for y in kv[1])):
        hi = max(ys, key=lambda y: y["buff"]["added"])
        lo = min(ys, key=lambda y: y["buff"]["added"])
        print(f"Buff: {ash}: {hi['buff']['kind']} x{hi['buff']['mult']}, {len(ys)} rows, adds "
              f"{hi['buff']['added']:.0f} HP on {hi['affinity']} {hi['weapon']} {hi['grip']} .. "
              f"{lo['buff']['added']:.0f} on {lo['affinity']} {lo['weapon']} {lo['grip']}", file=sys.stderr)


def set_area_weights(rows):
    """`area_w` on every damaging row: its swept area (`er-mechanics-ash-table.area`, the
    blade's swept volume weighted by swing angle plus the bullet's ground) over the median of the
    same ash's carriers, so a wider sweep is worth more on the weapon that makes it (user,
    2026-10-09, Stormcaller: "Why are we still not accounting for weapon hitbox volume and angle
    then?" -- the area only chose the AOE section, never the carrier). The same holds for every
    ash that depends on the weapon landing (user, 2026-10-09: "Same rule basically applies for
    any ash of war that's heavily dependent on landing the weapon in the cast"), so the ratio is
    blended by the blade's share of the row's damage (`contrib["blade"]`): a pure weapon swing
    takes it whole, a projectile ash with no blade hit keeps 1."""
    def blade_share(x):
        parts = {k: sum(v.values()) for k, v in (x.get("contrib") or {}).items() if isinstance(v, dict)}
        total = sum(parts.values())
        return parts.get("blade", 0) / total if total else 0.0

    by_ash = collections.defaultdict(list)
    for x in rows:
        if x["cat"] != "utility" and x.get("area"):
            by_ash[x["ash"]].append(x["area"])
    med = {a: statistics.median(v) for a, v in by_ash.items()}
    for x in rows:
        share = blade_share(x) if x["cat"] != "utility" else 0.0
        if share and x.get("area") and med.get(x["ash"]):
            x["area_w"] = 1 + share * (x["area"] / med[x["ash"]] - 1)


def set_landing_weights(rows, stats, cache_path):
    """`land_w` on every row `er-mechanics-ash-landing.py` has landed: the share of the cast's
    damage that lands on this carrier (`er-mechanics-ashes.skill_landing` over the corpus poises at
    1.5-3.0 m) over the median share of the same ash's carriers. The ash table credits every hit on
    every carrier; Stormcaller's three swings are the weapon's own capsules (dummies 100-130), so a
    short carrier misses swings a long one lands (user, 2026-10-13: "it should be prioritized on
    longer weapons, and deprioritized/ranked on smaller weapons"; ashes-of-war.md section 15c).
    Dividing by the ash's median moves its carriers against each other and leaves the ash's
    standing against other ashes, whose hits are all credited too, where it was."""
    if not cache_path or not Path(cache_path).exists():
        return
    name = "er_mechanics_ash_landing"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, HERE / "er-mechanics-ash-landing.py")
        sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[name])
    LAND = sys.modules[name]
    cache = json.loads(Path(cache_path).read_text())
    got = {}
    for x in rows:
        v = cache.get(LAND.key(x["weapon"], x["affinity"], x["grip"], x["ash"], stats))
        if v and v.get("share") is not None:
            got[id(x)] = v["share"]
    by_ash = collections.defaultdict(list)
    for x in rows:
        if id(x) in got:
            by_ash[x["ash"]].append(got[id(x)])
    med = {a: statistics.median(v) for a, v in by_ash.items()}
    for x in rows:
        if id(x) in got and med.get(x["ash"]):
            x["land_w"] = got[id(x)] / med[x["ash"]]
    for ash, v in sorted(by_ash.items()):
        ws = sorted((x["land_w"], x["weapon"], x["grip"]) for x in rows if x["ash"] == ash and "land_w" in x)
        print(f"Landing: {ash} on {len(v)} carriers, median landed share {med[ash]:.2f}; lowest "
              + ", ".join(f"{w} {g} x{lw:.2f}" for lw, w, g in ws[:3]) + "; highest "
              + ", ".join(f"{w} {g} x{lw:.2f}" for lw, w, g in ws[-3:]), file=sys.stderr)


def add_grip_change(x, delay=GRIP_CHANGE_FRAMES):
    """Move a "2H" row onto the swap clock: the skill starts `delay` frames late, so every frame
    measured from cast start (`first`, `last`, `gate`, the hyperarmor window, the string's end)
    moves by `delay`, and each per-second figure is the same damage over the longer span:
    `dps` x last / (last + delay), the string's x end / (end + delay), and `score` by whichever of
    the two it was (the string for a quick weapon, else `dps`; a step or buff's `dps` is its
    follow-up over `last`). Rescaling keeps `score` damage over time to the last damaging frame
    instead of charging the grip change as a flat cost, so a long cast loses less than a fast one,
    as it does in a fight. The cast-relative numbers are kept in `cast` for the interrupt model,
    whose opponent reacts to the cast itself."""
    if x["grip"] != "2H" or "cast" in x:
        return
    x["cast"] = {"first": x["first"], "last": x["last"], "gate": x.get("gate"), "ha": x.get("ha"),
                 "string": x.get("string"), "dps": x["dps"], "score": x["score"]}
    shift_cast(x, delay)
    x["grip_change"] = delay


def shift_cast(x, delay):
    """Move every frame of `x` measured from cast start `delay` later, with each per-second figure
    the same damage over the longer span (`add_grip_change`)."""
    st = x.get("string")
    string_scored = bool(x.get("quick") and st and x["score"] == st["dps"])
    if st:
        st = dict(st, end=round(st["end"] + delay, 1), dps=round(st["dps"] * st["end"] / (st["end"] + delay)))
        x["string"] = st
    span = x["last"]
    x["dps"] = round(x["dps"] * span / (span + delay)) if span else x["dps"]
    x["score"] = st["dps"] if string_scored else round(x["score"] * span / (span + delay)) if span else x["score"]
    x["first"] = round(x["first"] + delay, 1)
    x["last"] = round(x["last"] + delay, 1)
    if x.get("gate") is not None:
        x["gate"] = round(x["gate"] + delay, 1)
    if x.get("ha"):
        end = x["ha"]["end"]
        x["ha"] = dict(x["ha"], start=x["ha"]["start"] + delay, end=end + delay if end is not None else None)


def set_lead_in(rows, stats):
    """Put the lead-in (`er-mechanics-ashes.lead_in`, Wild Strikes' a610_040050) in front of the
    rows of a sweep made before the sweep counted it; such a row has no `lead` key. Run on the
    cast's clock, before `add_grip_change`. An ash is read on its first carrier and its rows are
    read one by one only when that carrier has a lead-in."""
    T = _table_mod()
    old = [x for x in rows if "lead" not in x]
    if not old:
        return
    t = T.Table(old[0]["weapon"], old[0]["affinity"], stats, DEFAULT_ARMOR, True).t
    first, n = {}, 0
    for x in old:
        first.setdefault(x["ash"], x)
    for ash, x0 in first.items():
        try:
            sid = t.find_arts(ash)
        except (SystemExit, KeyError):
            continue
        if not T.A.lead_in(t, sid, T.A.skill_profile(t, sid, t.find_weapon(x0["weapon"]), 0, x0["grip"])):
            continue
        for x in old:
            if x["ash"] != ash:
                continue
            prof = T.A.skill_profile(t, sid, t.find_weapon(x["weapon"]), 0, x["grip"])
            lead = T.A.lead_in(t, sid, prof)
            if lead:
                shift_cast(x, lead)
                x["lead"] = lead
                if x.get("ha"):
                    x["ha"]["start"] = T.A.lead_in_ha_start(prof, x["ha"]["start"])
                    x["covers"] = x["ha"]["start"] <= x["first"] and (x["ha"]["end"] or 0) >= x["last"]
                n += 1
    print(f"lead-in: {n} rows moved later by the clip their cast plays before its opening", file=sys.stderr)


def set_multihit_weights(rows, stats, cache_path):
    """`mh_w` on every damaging row (`MULTIHIT_K`). Weapon counter rates and each (weapon,
    affinity, ash, grip)'s counter gain and period are cached in `cache_path`."""
    T = sys.modules.get("er_mechanics_ash_table")
    if T is None:
        spec = importlib.util.spec_from_file_location("er_mechanics_ash_table", HERE / "er-mechanics-ash-table.py")
        T = importlib.util.module_from_spec(spec)
        sys.modules["er_mechanics_ash_table"] = T
        spec.loader.exec_module(T)
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    dmg = [x for x in rows if x["cat"] != "utility"]

    def akey(x):
        lead = f"|lead{x['lead']:g}" if x.get("lead") else ""
        return f"ash|{x['weapon']}|{x['affinity']}|{x['ash']}|{x['grip']}{lead}"

    weapons = sorted({x["weapon"] for x in dmg})
    todo_w = [w for w in weapons if f"weapon|{w}" not in cache]
    todo_a = [x for x in dmg if akey(x) not in cache]
    d = None
    if todo_w or todo_a:
        spec = importlib.util.spec_from_file_location("er_mechanics_talisman_affinity",
                                                      HERE / "er-mechanics-talisman-affinity.py")
        TA = importlib.util.module_from_spec(spec)
        sys.modules["er_mechanics_talisman_affinity"] = TA
        spec.loader.exec_module(TA)
        d = TA.Data()
        succ = d.triggers[MULTIHIT_TALISMAN]["kinds"]["successive"]
        print(f"multihit: {len(todo_w)} weapons, {len(todo_a)} rows to read", file=sys.stderr, flush=True)
    by_wa = collections.defaultdict(list)
    for x in todo_a:
        by_wa[x["weapon"], x["affinity"]].append(x)
    units = len(todo_w) + len(by_wa)
    for i, w in enumerate(todo_w, 1):
        if i % 10 == 0 or i == len(todo_w):
            print(f"  {i}/{units} weapons, then weapon affinities", file=sys.stderr, flush=True)
        f = TA.successive_features(TA.weapon_profile(d, d.find_weapon(w)), succ)
        grips = [f[g] for g in TA.GRIPS if g in f]
        times = [g["cold_start_first_threshold"]["seconds"][0] for g in grips
                 if (g.get("cold_start_first_threshold") or {}).get("seconds")]
        cache[f"weapon|{w}"] = {"rate": max((g["gain_per_s"] or 0.0 for g in grips), default=0.0),
                                "trigger_s": min(times, default=None)}
    tab = None
    for n, ((w, aff), xs) in enumerate(sorted(by_wa.items())):
        if n % 10 == 9 or n == len(by_wa) - 1:
            print(f"  {len(todo_w) + n + 1}/{units} weapons, then weapon affinities", file=sys.stderr, flush=True)
        # One table, switched per weapon: a new one decodes the regulation again, 7 s each and 349
        # of them in a build's pass (measured 2026-10-14).
        if tab is None:
            tab = T.Table(w, aff, stats, DEFAULT_ARMOR, True)
        else:
            tab.set_weapon(w, aff)
        sids = skill_ids(tab.t, {x["ash"] for x in xs})
        for x in xs:
            sid, two = sids.get(x["ash"]), x["grip"] == "2H"
            got = {"gain": 0, "period": None}
            if sid is not None:
                hits = T.S.AshScorer(tab.lev, tab.weapon, tab.affinity, sid, two, tab.defender, None).hits(stats)
                prof = T.A.skill_profile(tab.t, sid, tab.wid, tab.level, two)
                clip = tab.gates(sid, prof)
                gates = clip["gates"] if clip else {}
                period = gates.get("r1")
                if prof["fp_cost"].get("R2", -1) != -1 and gates.get("skill_q"):
                    period = min(p for p in (period, gates["skill_q"]) if p)
                # A loop opens R1 at its f0 (Wild Strikes), so the cast lasts to its last hit, after
                # the clip it plays first.
                lead = T.A.lead_in(tab.t, sid, prof)
                if lead:
                    period = lead + max(period or 0.0, max((h["frame"] for h in hits), default=0.0))
                got = {"gain": sum(d.counter_gain(h["atk_row"]) * h.get("count", 1)
                                   for h in hits if h.get("atk_row")), "period": period}
            cache[akey(x)] = got
        if n % 20 == 19:
            cache_path.write_text(json.dumps(cache))
    if todo_w or todo_a:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache))
    # Rank: the share of the pool's weapons slower to the first threshold (never counts as slowest).
    trig = {w: cache[f"weapon|{w}"]["trigger_s"] for w in weapons}
    slow = lambda t: math.inf if t is None else t
    rank = {w: sum(slow(trig[v]) > slow(trig[w]) for v in weapons) / max(1, len(weapons) - 1)
            for w in weapons}
    for x in dmg:
        c, wr = cache[akey(x)], cache[f"weapon|{x['weapon']}"]["rate"]
        rate = c["gain"] / (c["period"] / FPS) if c["period"] else 0.0
        x["counter_rate"] = round(rate, 2)
        x["multihit_rank"] = round(rank[x["weapon"]], 3)
        short = max(0.0, 1.0 - rate / wr) if wr else 0.0
        x["mh_w"] = max(0.0, 1.0 - MULTIHIT_K * rank[x["weapon"]] * short)


#: Pierce counter hits (`docs/er-mechanics/defense.md`, `VERIFIED`): SpEffect 45, the counter state
#: every player attack puts on its user from its first hitbox frame, cuts thrust damage taken by
#: 1.15, and Spear Talisman (320600, stateInfo 197) multiplies that counter rate by 1.15 again.
#: Players of pierce weapons wear Spear Talisman (user, 2026-10-12: "Users of pierce damaging
#: weapons use spear talisman and even partial hits with ashes that do pierce are good"), so the
#: talisman is counted as worn.
PIERCE_COUNTER = 1.15 * 1.15
#: The share of a cast's landed damage that lands as a counter hit, on a target mid-attack
#: (`INFERRED`: no trade model yet; the interrupt model's hits land during the opponent's windup,
#: before his counter state starts).
COUNTER_SHARE = 0.5
PIERCE_CACHE = Path.home() / ".cache/er-build-planner/ash-pierce-share.json"


#: Placement (user, 2026-10-12): "when we build out rows, or even place individual weapons they are
#: within a similar area to another weapon that enables it to perform optimally, like putting
#: Pickaxe near another greataxe/greathammer to get the powerstanced moveset, preferrably one with
#: pierce since it does pierce, and putting every halberd individual item or row of items near
#: another row or individual item that helps in the offhand". A partner counts as near within
#: `PARTNER_ROWS` rows of the weapon's slot.
PARTNER_ROWS = 1
#: A powerstance partner is good enough when its score is within this share of the best one's.
PARTNER_SLACK = 0.03
#: Classes whose weapons want an off-hand partner: the `COMBOS` pairs of two classes, Halberd's.
OFFHAND_PARTNERS = {"Halberd": tuple(rc for rc, lc in COMBOS if lc == "Halberd" and rc != lc)}


class Partners:
    """Which weapon makes another perform best beside it (`PARTNER_ROWS`).

    - A weapon whose powerstanced light chain (`er-mechanics-grip-modes.py`, the weapon with a
      copy of itself, both hands on one affinity) beats its one- and two-handed chains on the same
      affinity wants a powerstance partner: any weapon `IsEnableDualWielding` pairs it with
      (`er-mechanics-powerstance-guard.can_powerstance`), scored by the embedding's chain damage per
      second for the pair times the pierce counter bonus (`PIERCE_COUNTER`, `COUNTER_SHARE`) on the
      share of the pair's hits thrown by a pierce weapon (`EquipParamWeapon.atkAttribute` 2).
    - A weapon of an `OFFHAND_PARTNERS` class wants a weapon of one of its partner classes."""

    def __init__(self, embed_doc, tables):
        spec = importlib.util.spec_from_file_location("er_mechanics_grip_modes", HERE / "er-mechanics-grip-modes.py")
        self.GM = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.GM)
        spec = importlib.util.spec_from_file_location("er_mechanics_powerstance_guard",
                                                      HERE / "er-mechanics-powerstance-guard.py")
        self.PG = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.PG)
        self.e, self.tables = embed_doc, tables
        self.reg = type("Reg", (), {"weapon": tables.weapons})()
        self.pairs = collections.defaultdict(dict)
        for p in embed_doc["powerstance"]:
            c = self.GM.chain(p["slots"], "dual_")
            if c:
                self.pairs[p["right"], p["right_affinity"]][p["left"]] = max(
                    self.pairs[p["right"], p["right_affinity"]].get(p["left"], 0.0),
                    c[0] * self.pierce_factor(p))
        self._need, self._scores = {}, {}

    def row(self, w):
        return self.tables.weapons[self.tables.find_weapon(w, "Standard")]

    def cls(self, w):
        return CLASS.get(self.row(w)["wepType"], "Other")

    def pierce(self, w):
        return self.row(w).get("atkAttribute") == 2

    def pierce_factor(self, p):
        hands = [h for k, s in p["slots"].items() if k.startswith("dual_") and k[5:].isdigit()
                 for h in s.get("hands") or ()]
        weapon = {"right": p["right"], "left": p["left"]}
        share = (sum(self.pierce(weapon[h]) for h in hands if h in weapon) / len(hands)) if hands else 0.0
        return 1.0 + share * (PIERCE_COUNTER - 1.0) * COUNTER_SHARE

    def need(self, w, aff):
        """"powerstance", "offhand" or None."""
        if (w, aff) not in self._need:
            kind = "offhand" if self.cls(w) in OFFHAND_PARTNERS else None
            if kind is None:
                for b in [aff] + [p for p in self.GM.ps_affinities(self.e, w) if p != aff]:
                    m = self.GM.modes(self.e, w, b)
                    if "PS" in m:
                        own = max((m[g]["chain"][0] for g in ("1H", "2H") if (m.get(g) or {}).get("chain")), default=0)
                        kind = "powerstance" if m["PS"]["chain"] and m["PS"]["chain"][0] > own else None
                        break
            self._need[w, aff] = kind
        return self._need[w, aff]

    def scores(self, w, aff):
        """{partner weapon: score} for a powerstance, on `aff` or the affinity it was measured on."""
        if (w, aff) not in self._scores:
            got = self.pairs.get((w, aff)) or next((v for (r, _), v in self.pairs.items() if r == w), {})
            rid = self.tables.find_weapon(w, "Standard")
            self._scores[w, aff] = {p: s for p, s in got.items()
                                    if self.PG.can_powerstance(self.reg, rid, self.tables.find_weapon(p, "Standard"))}
        return self._scores[w, aff]

    def ok(self, w, aff, p):
        kind = self.need(w, aff)
        if kind == "offhand":
            return self.cls(p) in OFFHAND_PARTNERS[self.cls(w)]
        sc = self.scores(w, aff)
        return p in sc and sc[p] >= (1 - PARTNER_SLACK) * max(sc.values())


def pierce_key(x):
    return f"{x['weapon']}|{x['affinity']}|{x['ash']}|{x['grip']}"


_PIERCE_W = {}


def _pierce_work(item):
    """{pierce_key: share} for one (weapon, affinity)'s rows, in a forked worker."""
    (w, aff), xs = item
    T, stats = _table_mod(), _PIERCE_W["stats"]
    # One table per worker, switched per weapon (see `set_multihit_weights`).
    tab = _PIERCE_W.get("tab")
    if tab is None:
        tab = _PIERCE_W["tab"] = T.Table(w, aff, stats, DEFAULT_ARMOR, True)
    else:
        tab.set_weapon(w, aff)
    sids = skill_ids(tab.t, {x["ash"] for x in xs})
    out = {}
    for x in xs:
        sid, share = sids.get(x["ash"]), None
        if sid is not None:
            hits = T.S.AshScorer(tab.lev, tab.weapon, tab.affinity, sid, x["grip"] == "2H",
                                 tab.defender, None).hits(stats)
            total = T.A.pvp_damage(tab.t, tab.wid, hits, tab.defender, T.S.OPT.DEF)
            pierce = sum(h["pvp_damage_total"] for h in hits if h["phys_type"] == "pierce")
            share = round(pierce / total, 4) if total else 0.0
        out[pierce_key(x)] = share
    return out


def set_pierce_weights(rows, stats, cache_path, jobs=6):
    """`pierce_w` = 1 + the cast's pierce share of PvP damage x (`PIERCE_COUNTER` - 1) x
    `COUNTER_SHARE` on every damaging row; the share is each hit's `phys_type`
    (`er-mechanics-ashes.phys_type`, AtkParam `atkAttribute` with 252/253 deferring to the
    weapon), weighted by its PvP damage, cached per (weapon, affinity, ash, grip)."""
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    dmg = [x for x in rows if x["cat"] != "utility"]
    todo = [x for x in dmg if pierce_key(x) not in cache]
    if todo:
        import multiprocessing as mp
        AI = _ai()
        by_wa = collections.defaultdict(list)
        for x in todo:
            by_wa[x["weapon"], x["affinity"]].append({k: x[k] for k in ("weapon", "affinity", "ash", "grip")})
        chunks = sorted(by_wa.items(), key=lambda kv: -len(kv[1]))
        ctx = mp.get_context("fork")
        cpus = AI.allowed_cpus()
        workers = cpus[1:] or cpus
        jobs = max(1, min(jobs, len(workers)))
        print(f"pierce: {len(todo)} rows to read, {len(chunks)} weapon affinities on {jobs} workers",
              file=sys.stderr, flush=True)
        _PIERCE_W["stats"] = stats
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with ctx.Pool(jobs, initializer=AI._worker_init, initargs=(workers, ctx.Value("i", 0))) as pool:
            for n, res in enumerate(pool.imap_unordered(_pierce_work, chunks), 1):
                cache.update(res)
                print(f"  {n}/{len(chunks)} weapon affinities", file=sys.stderr, flush=True)
                if n % 20 == 0 or n == len(chunks):
                    cache_path.write_text(json.dumps(cache))
    for x in dmg:
        share = cache.get(pierce_key(x)) or 0.0
        x["pierce_share"] = share
        x["pierce_w"] = 1.0 + share * (PIERCE_COUNTER - 1.0) * COUNTER_SHARE


def weigh(rows, a, r1, build_stats, report=False, stages=None):
    """Every per-row weight `value` reads, in the pool's order: the interrupt and whiff
    multipliers (`--interrupt-weight`, `--whiff-weight`), `area_w`, `reach_w` and the ranged
    status proc. Each pass starts a stage of `stages` (`Stages`)."""
    pb = bool(getattr(a, "perfect_block", None) and a.perfect_block_weight)
    stage = stages or (lambda name: None)
    if a.interrupt_weight or a.whiff_weight or pb:
        # The perfect-block term reads the same cast profiles, so it computes them when the
        # interrupt and whiff weights are both off.
        stage("interrupt")
        got = interrupt_weights(rows, a)
        if a.whiff_weight:
            stage("whiff")
            whiff_weights(rows, got, getattr(a, "interrupt_cache", None))
            if report:
                whiff_report(rows)
    stage("area")
    set_area_weights(rows)
    stage("landing")
    set_landing_weights(rows, build_stats, getattr(a, "landing_cache", None))
    stage("reach")
    set_reach_weights(rows, r1)
    stage("ranged status")
    set_ranged_status(rows, r1, build_stats, a.ranged_cache)
    stage("multihit")
    set_multihit_weights(rows, build_stats, a.counter_cache)
    stage("pierce")
    set_pierce_weights(rows, build_stats, getattr(a, "pierce_cache", PIERCE_CACHE), getattr(a, "interrupt_jobs", 6))
    if a.parry and a.parry_weight:
        stage("parry")
        parry_weights(rows, a.parry)
        if report:
            parry_report(rows)
    if pb:
        stage("perfect block")
        pb_weights(rows, got, a.perfect_block, build_stats)
        if report:
            pb_report(rows)
    if report:
        ranged_report(rows)


def _sr():
    key = "er_mechanics_shields_ranged"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, HERE / "er-mechanics-shields-ranged.py")
        m = importlib.util.module_from_spec(spec)
        sys.modules[key] = m
        spec.loader.exec_module(m)
    return sys.modules[key]


def _pm():
    key = "er_mechanics_parry"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, HERE / "er-mechanics-parry.py")
        m = importlib.util.module_from_spec(spec)
        sys.modules[key] = m
        spec.loader.exec_module(m)
    return sys.modules[key]


_PARRY = {}


def parry_weights(rows, path):
    """Set `pmult` and `parry` on every damaging row (user, 2026-10-10: "Model parrying"): the
    riposte HP per cast the corpus's parriers land on its first hit (`er-mechanics-parry.ash_cost`:
    carry x attempt x catch x their riposte on me, from the `--parry` file), and pmult = max(0,
    1 - that / the cast's damage). A cast whose first hit is a bullet, or that no JumpTable 5
    window guards, keeps 1."""
    PM = _pm()
    if "summary" not in _PARRY:
        _PARRY["summary"] = json.loads(Path(path).read_text())["summary"]
        A = (sys.modules.get("er_mechanics_ash_table") or _sr().table_mod()).A
        _PARRY["A"], _PARRY["t"], _PARRY["catcher"] = A, A.AshTables(), PM.Catcher()
        _PARRY["ids"] = {}
        for sid in _PARRY["t"].arts:
            _PARRY["ids"].setdefault(_PARRY["t"].arts_name(sid), sid)
        _PARRY["atk"] = {}
    s, t, ids, cache = _PARRY["summary"], _PARRY["t"], _PARRY["ids"], _PARRY["atk"]
    misses = 0
    for x in rows:
        if x["cat"] == "utility" or not x.get("dmg"):
            continue
        sid = ids.get(x["ash"])
        key = (sid, x["weapon"], x["grip"])
        if key not in cache:
            try:
                cache[key] = PM.ash_attack(t, sid, t.find_weapon(x["weapon"]), 0, "both" if x["grip"] == "2H" else "one")
            except (KeyError, SystemExit, TypeError, ValueError, IndexError):
                cache[key] = None
                misses += 1
        cost, c = PM.ash_cost(s, cache[key], _PARRY["catcher"])
        x["parry"] = {"cost": round(cost, 1), "catch": round(c, 3)}
        x["pmult"] = max(0.0, 1.0 - cost / x["dmg"])
    if misses:
        print(f"parry: {misses} (ash, weapon, grip) without a resolved first hit kept at 1.0", file=sys.stderr)


def parry_report(rows, n=10):
    """Stderr: the `n` ashes whose best-value carrier pays the most parry HP per cast."""
    best = {}
    for x in rows:
        if x.get("parry") and (x["ash"] not in best or value(x) > value(best[x["ash"]])):
            best[x["ash"]] = x
    for x in sorted(best.values(), key=lambda x: -x["parry"]["cost"])[:n]:
        print(f"parry: {x['ash']} ({x['affinity']} {x['weapon']} {x['grip']}): catch {x['parry']['catch']:.2f}, "
              f"{x['parry']['cost']:.1f} HP per cast of {x['dmg']}, pmult {x['pmult']:.3f}", file=sys.stderr)
    free = sorted(a for a, x in best.items() if x["parry"]["cost"] == 0)
    print(f"parry: {len(free)} of {len(best)} ashes unparryable on their best carrier", file=sys.stderr)


_PB = {}


def _pbm():
    key = "er_mechanics_perfect_block"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, HERE / "er-mechanics-perfect-block.py")
        m = importlib.util.module_from_spec(spec)
        sys.modules[key] = m
        spec.loader.exec_module(m)
    return sys.modules[key]


def pb_weights(rows, got, path, build_stats):
    """Set `bmult` and `pblock` on every damaging row from its interrupt profile in `got` (module
    docstring of `er-mechanics-perfect-block.py`; user, 2026-10-10: model the PvP perfect block
    from its effects). P = carry x attempt x success on the first hit, and in that case the
    blocked hit passes only the corpus guard's chip, its damage-over-time status is dropped, and
    the blocker punishes unless a later hit of the cast lands first. A step or buff, or a row with
    no profile, keeps 1."""
    PB = _pbm()
    if "side" not in _PB:
        s = json.loads(Path(path).read_text())["summary"]
        model = PB.Model(PB.Guards.from_summary(s), opp=_ai().Opponent(), use=s["attempt"])
        _PB["side"] = PB.AshSide(model, build_stats)
    side = _PB["side"]
    for x in rows:
        if x["cat"] == "utility" or not x.get("dmg"):
            continue
        srate = status_rate(x)
        side.row(x, got.get(_ai().row_key(x)), x["score"], srate)


def pb_report(rows, n=10):
    """Stderr: the `n` ashes whose best-value carrier pays the most and the least HP per cast to
    perfect blocks, with P, the protection share and the stun the block skips."""
    best = {}
    for x in rows:
        if (x.get("pblock") or {}).get("cost_hp") is not None and (x["ash"] not in best or value(x) > value(best[x["ash"]])):
            best[x["ash"]] = x
    order = sorted(best.values(), key=lambda x: -x["pblock"]["cost_hp"])

    def line(tag, x):
        b = x["pblock"]
        print(f"perfect block {tag}: {x['ash']} ({x['affinity']} {x['weapon']} {x['grip']}): T f{b['T']:g}, P {b['P']:.4f}, "
              f"chip {b['chip']:.2f}, stopped {b['stopped']:.2f}, punish {b['punish_hp']:.0f} HP, stun skipped "
              f"f{b.get('stun_skipped', '-')}, {b['cost_hp']:.1f} HP per cast, bmult {x['bmult']:.4f}", file=sys.stderr)
    for x in order[:n]:
        line("most", x)
    for x in order[-n:][::-1]:
        line("least", x)


def parry_tool_copy(path, picks):
    """The parry tool copy for the Shields row, or None (user, 2026-10-10). A left hand answers
    every corpus opening: the parry where a JumpTable 5 window guards it, its own block otherwise
    (`er-mechanics-parry` `ev_abs_open`, HP per opening). A tool that is not on a shield goes in
    only when it fits the load and its `ev_abs_open` beats every shield pick's best: the shield
    picks carry their own parry ash already, so a dagger or fist parry only earns a slot by
    answering the corpus better than they do. Prints the comparison either way."""
    tools = json.loads(Path(path).read_text())["tools"]
    shield = [x for x in tools if x["class"] in (65, 67, 69) and x["fits"]]
    other = [x for x in tools if x["class"] not in (65, 67, 69) and x["fits"]]
    held = {p["weapon"] for p in picks.values()}
    best_shield = max((x for x in shield if x["carrier"] in held), key=lambda x: x["ev_abs_open"], default=None) \
        or max(shield, key=lambda x: x["ev_abs_open"], default=None)
    best_other = max(other, key=lambda x: x["ev_abs_open"], default=None)
    if best_other:
        print(f"Shields: best non-shield parry {best_other['skill']} on {best_other['carrier']} "
              f"{best_other['ev_abs_open']:.0f} HP per opening against "
              + (f"{best_shield['skill']} on {best_shield['carrier']} {best_shield['ev_abs_open']:.0f}"
                 if best_shield else "no shield"), file=sys.stderr)
    if best_other and (best_shield is None or best_other["ev_abs_open"] > best_shield["ev_abs_open"]):
        return best_other
    return None


def shield_copies(a, build, main_w, main_aff, r1, build_stats):
    """(copies, picks): the Shields section as slot-ready dicts, and {job: shield dict}, from
    `er-mechanics-shields-ranged.shield_plan` for the main-hand pick `main_w`: the medium No Skill
    block shield and one parry copy per job (melee, spells), each infused for its job. With
    `--parry`, a non-shield parry tool replaces the melee copy when it answers the corpus's
    openings better (`parry_tool_copy`)."""
    SR = _sr()
    T = sys.modules.get("er_mechanics_ash_table") or SR.table_mod()
    m = SR.ShieldModel(build, main_w, main_aff, mirror=a.mirror)
    armor = SR.worn(build)[0] or DEFAULT_ARMOR
    tab = T.Table(main_w, main_aff, build_stats, armor, True)
    plan, _ = SR.shield_plan(m, tab.t, T.A)
    copies, picks = [], {}
    for x in plan:
        p, o = x["shield"], x.get("option")
        picks[x["job"]] = p
        cuts = "/".join(f"{p['cuts'][el]:.0f}" for el in SR.ELEMENTS)
        note = (f"{p['size']} shield, guard {p['guard']:.2f} ({p['guard'] / p['weight']:.3f} per weight unit), "
                f"cuts {cuts}" + (f"; {o['net_hp']:.0f} HP per opening, catch {o['catch']:.2f}" if o else ""))
        copies.append({"weapon": p["weapon"], "ash": x["ash"], "affinity": p["affinity"], "cat": "utility",
                       "grip": "1H", "value": o["net_hp"] if o else 0.0, "norm": 0.0, "area": 0.0,
                       "status_hp": {}, "note": f"{x['job']}: {note}"})
        print(f"Shields: {x['job']}: {p['affinity']} {p['weapon']} {x['ash']}, {note}", file=sys.stderr)
    tool = parry_tool_copy(a.parry, picks) if a.parry else None
    if tool:
        k = next((i for i, x in enumerate(plan) if x["job"] == "melee"), len(copies))
        copies[k:k + 1] = [{"weapon": tool["carrier"], "ash": tool["skill"], "affinity": "Standard", "cat": "utility",
                            "grip": "1H", "value": tool["ev_abs_open"], "norm": 0.0, "area": 0.0, "status_hp": {},
                            "note": f"melee: parry tool; {tool['ev_abs_open']:.0f} HP per opening, catch "
                                    f"{tool['catch_open']:.2f}"}]
    return copies, picks, m.summary()


def ranged_copies(build, build_stats, main_reach):
    """(copies, ammo): one copy per viable ranged pick the build wields, with its own skill or
    its assigned ash (`er-mechanics-shields-ranged.ranged_plan`, `data/ranged-picks.toml`), and
    the planner's four equipped ammunition names."""
    SR = _sr()
    m = SR.RangedModel(build)
    plan, ammo = SR.ranged_plan(m, main_reach, REACH_GAIN_K)
    copies = []
    for p in plan:
        cls, s, sk = p["class"], p["shot"], p["skill"]
        copies += [{"weapon": p["weapon"], "ash": p["ash"], "affinity": "Standard", "cat": "bullet",
                    "grip": "2H", "value": p["value"], "norm": 0.0, "area": 0.0, "status_hp": s["status_hp"],
                    "note": f"{cls}: {s['ammo']} {s['dmg']} dmg, fires f{s['startup']:g}, roll f{s['roll']:g}, "
                            f"reach {s['reach'] or 0:.0f} m, {s['shot_dps']}/s"
                            + (f"; {sk['ash']} {sk['dmg']} dmg, {sk['dps']}/s" if sk else "")}] * p["copies"]
        print(f"Ranged: {cls} {p['weapon']} x{p['copies']} value {p['value']} (shot {s['ammo']} {s['shot_dps']}/s, "
              f"skill {p['ash']} {sk['dps'] if sk else '-'}/s, reach_w {p['reach_w']})", file=sys.stderr)
    print(f"Ranged: ammunition {ammo}", file=sys.stderr)
    seal = SR.seal_pick(build)
    if seal:
        need = ", ".join(f"{k.upper()} {v}" for k, v in seal["need"].items() if v)
        copies.append({"weapon": seal["weapon"], "ash": None, "affinity": "Standard", "cat": "utility", "grip": "1H",
                       "value": 0.0, "norm": 0.0, "area": 0.0, "status_hp": {},
                       "note": f"seal: casts {seal['spell']} ({need})"
                               + ("" if seal["castable"] else "; the build's stats are below that")})
        print(f"Ranged: seal {seal['weapon']} for {seal['spell']} ({need})"
              + ("" if seal["castable"] else ": NOT castable at the build's stats"), file=sys.stderr)
    return copies, ammo


def value(x):
    """`raw_value` times the interrupt multiplier (`er-mechanics-ash-interrupt.multiplier`, set as
    `imult` by `--interrupt-weight`): the share of the cast still delivered, net of the HP taken
    and plus the recovery saved, when the opponent answers it. With `--whiff-weight` the opponent
    instead rolls the first hit and punishes the recovery (`wmult`) in `DODGE_SHARE` of fights:
    `er-mechanics-ash-whiff.combined`. With `--parry`, times `pmult` (`parry_weights`): the cast's
    damage left after the riposte the corpus's parriers are expected to land on it. With
    `--perfect-block`, times `bmult` (`pb_weights`): the value left after the corpus's perfect
    blocks. Parry and perfect block are a few percent each, so multiplying them in is their
    mixture with the other replies to first order. Times `mh_w` (`set_multihit_weights`): what a
    cast that feeds the successive-hit counter slower than its weapon's own strings gives up. Times
    `buff_w` (`set_buff_weights`) on a buff: what it adds here against its best carrier. Times
    `pierce_w` (`set_pierce_weights`): the pierce counter-hit bonus on the cast's pierce share."""
    m = x.get("imult", 1.0)
    if "wmult" in x:
        m = _whiff().combined(m, x["wmult"])
    return (raw_value(x) * m * x.get("pmult", 1.0) * x.get("bmult", 1.0) * x.get("mh_w", 1.0)
            * x.get("buff_w", 1.0) * x.get("pierce_w", 1.0))


#: Interrupt profiles, keyed by sweep row (`er-mechanics-ash-interrupt.profiles`).
INTERRUPT_CACHE = Path.home() / ".cache/er-build-planner/ash-interrupt.json"
#: The run's stages in order. `Stages` prints them for `er-mechanics-pipeline-watch.py`, which
#: draws its bar and its estimate from them, weighing each by `STAGE_TIMES` from the last run.
STAGES = ("rows", "interrupt", "whiff", "area", "landing", "reach", "ranged status", "multihit", "pierce",
          "parry", "perfect block", "own status", "rank", "layout", "crit", "shields", "ranged", "fun and sets",
          "partners", "redundant status", "write")
STAGE_TIMES = Path.home() / ".cache/er-build-planner/loadout-stage-times.json"


class Stages:
    """Prints `stages: [[name, seconds last time], ...]` once, then `stage k/N: name (Ns in)` as
    each stage starts, and on `finish` records how long each one took. A stage a run skips (parry
    with no parry file) keeps the time it took the last time it ran."""

    def __init__(self, path: Path | None = STAGE_TIMES):
        self.path, self.t0, self.cur, self.took = path, time.monotonic(), None, {}
        try:
            self.last = json.loads(path.read_text()) if path else {}
        except (OSError, ValueError):
            self.last = {}
        print("stages: " + json.dumps([[n, self.last.get(n)] for n in STAGES]), file=sys.stderr, flush=True)

    def __call__(self, name: str) -> None:
        now = time.monotonic()
        if self.cur:
            self.took[self.cur[0]] = round(now - self.cur[1], 1)
        self.cur = (name, now)
        print(f"stage {STAGES.index(name) + 1}/{len(STAGES)}: {name} ({now - self.t0:.0f}s in)",
              file=sys.stderr, flush=True)

    def finish(self) -> None:
        if self.cur:
            self.took[self.cur[0]] = round(time.monotonic() - self.cur[1], 1)
            self.cur = None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps({**self.last, **self.took}))
            os.replace(tmp, self.path)
DEFAULT_ARMOR = ["Verdigris Gauntlets", "Verdigris Armor", "Verdigris Greaves"]


def _ai():
    spec = importlib.util.spec_from_file_location("er_mechanics_ash_interrupt", HERE / "er-mechanics-ash-interrupt.py")
    AI = sys.modules.get("er_mechanics_ash_interrupt") or importlib.util.module_from_spec(spec)
    if "er_mechanics_ash_interrupt" not in sys.modules:
        sys.modules["er_mechanics_ash_interrupt"] = AI
        spec.loader.exec_module(AI)
    return AI


def _whiff():
    key = "er_mechanics_ash_whiff"
    if key not in sys.modules:
        _ai()
        spec = importlib.util.spec_from_file_location(key, HERE / "er-mechanics-ash-whiff.py")
        m = importlib.util.module_from_spec(spec)
        sys.modules[key] = m
        spec.loader.exec_module(m)
    return sys.modules[key]


def whiff_weights(rows, got, cache_path=None):
    """Set `wmult` and `whiff` on every damaging row from its interrupt profile in `got`
    (module docstring of `er-mechanics-ash-whiff.py`). A two-handed row's grip change counts as
    telegraph: the opponent sees the swap start `grip_change` frames before the cast."""
    W, AI = _whiff(), _ai()
    opp = W.distinct(AI.Opponent())
    # Each row is a numpy simulation of the opponents' roll and punish (about 0.02 s over the
    # distinct moves, 13,000 rows a build), so the results are kept beside the interrupt cache, valid while its settings and the
    # whiff module are the ones they came from.
    memo, path, settings = {}, None, None
    if cache_path and Path(cache_path).exists():
        path = Path(f"{cache_path}.whiff.json")
        settings = {**json.loads(Path(cache_path).read_text()).get("settings", {}),
                    "whiff": hashlib.sha256((HERE / "er-mechanics-ash-whiff.py").read_bytes()).hexdigest()[:16]}
        doc = json.loads(path.read_text()) if path.exists() else {}
        memo = doc.get("rows", {}) if doc.get("settings") == settings else {}
    todo = [x for x in rows if x["cat"] != "utility"]
    print(f"whiff: {len(todo)} rows, {sum(1 for x in todo if _whiff_key(AI, x) in memo)} cached",
          file=sys.stderr, flush=True)
    for i, x in enumerate(todo, 1):
        key = _whiff_key(AI, x)
        if key not in memo:
            memo[key] = W.from_profile(opp, got.get(AI.row_key(x)), x.get("grip_change", 0.0))
        w = memo[key]
        # A step or buff, or a row with no profile, has no whiff to punish and keeps `imult` alone.
        if w:
            x["wmult"] = W.multiplier(w)
            x["whiff"] = {k: round(v, 3) for k, v in w.items()}
        if i % 500 == 0 or i == len(todo):
            print(f"  {i}/{len(todo)} rows", file=sys.stderr, flush=True)
    if path:
        path.write_text(json.dumps({"settings": settings, "rows": memo}))


def _whiff_key(AI, x):
    return f"{AI.row_key(x)}|grip{x.get('grip_change', 0.0):g}"


def whiff_report(rows, n=10):
    """Stderr: the `n` ashes whose best-value carrier carries the largest expected punish HP."""
    best = {}
    for x in rows:
        if x.get("whiff") and (x["ash"] not in best or value(x) > value(best[x["ash"]])):
            best[x["ash"]] = x
    for x in sorted(best.values(), key=lambda x: -x["whiff"]["punish_hp"])[:n]:
        w, lead = x["whiff"], x.get("grip_change", 0.0)
        ha = x.get("ha") or {}
        print(f"whiff: {x['ash']} ({x['affinity']} {x['weapon']} {x['grip']}): T f{w['T'] + lead:g}, "
              f"gate f{w['gate'] + lead:g}, HA start {('f%g' % ha['start']) if ha else '-'}, "
              f"p_dodge {w['p_dodge']:.2f}, punish {w['punish_hp']:.0f} HP, trade {w['trade']:.2f}, "
              f"wmult {x['wmult']:.2f}", file=sys.stderr)


def ranged_report(rows, n=12):
    """Stderr: per ash with a ranged proc, its best ranged reading (HP per second) and carrier,
    then the reach weight of Storm Blade's shortest and longest carrier."""
    best = {}
    for x in rows:
        hp = sum((x.get("ranged") or {}).values())
        if hp and x.get("ranged_t"):
            rate = hp / (x["ranged_t"] / FPS)
            if x["ash"] not in best or rate > best[x["ash"]][0]:
                best[x["ash"]] = (rate, x)
    for rate, x in sorted(best.values(), key=lambda p: -p[0])[:n]:
        print(f"ranged: {x['ash']} ({x['affinity']} {x['weapon']} {x['grip']}): {x['ranged']} by "
              f"f{x['ranged_t']:g} = {rate:.0f} HP/s; status only {x.get('status_only') or {}}", file=sys.stderr)
    storm = sorted((x for x in rows if x["ash"] == "Storm Blade" and "reach_w" in x), key=lambda x: x["reach_w"])
    for x in storm[:1] + storm[-1:]:
        print(f"reach_w: Storm Blade on {x['weapon']} {x['grip']}: {x['reach_w']:.3f}", file=sys.stderr)


def interrupt_weights(rows, a):
    """Set `imult` on every row (module docstring of `er-mechanics-ash-interrupt.py`), when
    `--interrupt-weight`, and return the profiles. The stats, armor worn (the build's equipped
    protectors) and max HP (Blood Tax's heal) come from `--build` when given, else the sweep's
    defaults."""
    AI = _ai()
    stats, armor, max_hp = {"str": 18, "dex": 80, "int": 7, "fth": 8, "arc": 9}, DEFAULT_ARMOR, 2292.5
    if a.build:
        b = json.loads(a.build.read_text())
        stats = {k: b["stats"][k] for k in ("str", "dex", "int", "fth", "arc")}
        worn = [s["name"] for p in ("arms", "body", "legs", "head")
                for s in (b.get("protectors") or {}).get(p, {}).get("slots", []) if s.get("equipSet")]
        armor = worn or armor
        max_hp = (b.get("computed") or {}).get("maxHealth") or max_hp
    # The simulated opponent answers the cast, whose hits `profiles` re-derives cast-relative, so it
    # gets the cast-relative `gate` and `last` of a two-handed row (`add_grip_change`), not the
    # swap-inclusive ones.
    got = AI.profiles([{**x, **x["cast"]} if "cast" in x else x for x in rows], a.interrupt_cache, stats, armor, max_hp, a.interrupt_poise, None, a.interrupt_jobs,
                      log=lambda m: print(m, file=sys.stderr, flush=True), throttled=a.throttle)
    errors = 0
    for x in rows:
        r = got.get(AI.row_key(x))
        if not r or "error" in r:
            errors += 1
            r = None
        if a.interrupt_weight:
            x["imult"] = AI.multiplier(r, raw_value(x))
    if errors:
        print(f"interrupt: {errors} rows without a profile kept at 1.0", file=sys.stderr)
    return got


def hungarian(cost):
    """Minimum-cost assignment of every row of `cost` (n <= m rows x m columns) to a distinct
    column. Returns [column per row]."""
    n, m = len(cost), len(cost[0])
    INF = float("inf")
    u, v, p, way = [0.0] * (n + 1), [0.0] * (m + 1), [0] * (m + 1), [0] * (m + 1)
    for i in range(1, n + 1):
        p[0], j0 = i, 0
        minv, used = [INF] * (m + 1), [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], INF, 0
            for j in range(1, m + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    out = [0] * n
    for j in range(1, m + 1):
        if p[j]:
            out[p[j] - 1] = j - 1
    return out


def mobility_weapons(path):
    """Weapons public builds put a `MOBILITY_ASHES` ash on, most builds first."""
    count = collections.Counter()
    if not path.exists():
        return []
    for line in path.open():
        seen = set()
        for s in ((json.loads(line).get("build") or {}).get("inventory") or {}).get("slots", []):
            # The planner spells it "Miséricorde"; the sweep's names are ASCII.
            name = unicodedata.normalize("NFKD", s["name"]).encode("ascii", "ignore").decode()
            if s.get("weaponArt") in MOBILITY_ASHES and name not in seen:
                seen.add(name)
                count[name] += 1
    return [w for w, _ in count.most_common()]


def spread_ashes(weapons, cands, per_weapon, cap, penalty, uncapped=frozenset()):
    """Give each weapon `per_weapon` distinct ashes (an int, or {weapon: count}), maximising the
    summed `norm`, with each ash on at most `cap` weapons before every further carrier costs
    `penalty` x (copies over the cap).

    A min-cost flow: source -> weapon (capacity `per_weapon`), weapon -> ash (capacity 1, cost
    -norm of that weapon's best copy of the ash), ash -> sink (one unit edge per carrier, cost 0
    up to `cap`, then rising). Successive shortest paths with Bellman-Ford, exact for this size
    (tens of weapons, a hundred ashes). `cands` is {(weapon, ash): row}. A weapon in `uncapped`
    goes from its damaging ashes straight to the sink: it takes its own best ones and uses none of
    the cap. Returns {weapon: [rows]}."""
    ashes = sorted({ash for _, ash in cands})
    W, A = len(weapons), len(ashes)
    src, sink = 0, 1 + W + A
    graph = [[] for _ in range(sink + 1)]

    def edge(u, v, cap_, cost):
        graph[u].append([v, cap_, cost, len(graph[v])])
        graph[v].append([u, 0, -cost, len(graph[u]) - 1])
        return u, len(graph[u]) - 1

    used = {}
    for i, w in enumerate(weapons):
        edge(src, 1 + i, per_weapon[w] if isinstance(per_weapon, dict) else per_weapon, 0.0)
        for j, ash in enumerate(ashes):
            if (w, ash) in cands:
                # A step or buff stays capped: its `norm` is on its own category's scale (Shriek of
                # Sorrow 1.38, Endure 0.68 on the Shamshir), so an uncapped weapon would fill its
                # rows with them ahead of Sword Dance's 0.6.
                free = w in uncapped and cands[w, ash]["cat"] != "utility"
                to = sink if free else 1 + W + j
                used[w, ash] = edge(1 + i, to, 1, -cands[w, ash]["norm"])
    for j in range(A):
        for k in range(len(weapons)):
            edge(1 + W + j, sink, 1, 0.0 if k < cap else penalty * (k - cap + 1))
    while True:
        # Shortest path by SPFA (Bellman-Ford with a queue): the residual graph has negative
        # costs but no negative cycles, since each augmentation is along a shortest path.
        dist, prev = [float("inf")] * (sink + 1), [None] * (sink + 1)
        dist[src], queue, queued = 0.0, collections.deque([src]), {src}
        while queue:
            u = queue.popleft()
            queued.discard(u)
            for ei, (v, c, cost, _) in enumerate(graph[u]):
                if c > 0 and dist[u] + cost < dist[v] - 1e-12:
                    dist[v], prev[v] = dist[u] + cost, (u, ei)
                    if v not in queued:
                        queued.add(v)
                        queue.append(v)
        if dist[sink] == float("inf"):
            break
        v = sink
        while v != src:
            u, ei = prev[v]
            graph[u][ei][1] -= 1
            graph[v][graph[u][ei][3]][1] += 1
            v = u
    out = {w: [] for w in weapons}
    for (w, ash), (u, ei) in used.items():
        if graph[u][ei][1] == 0:
            out[w].append(cands[w, ash])
    return out


def off_stat(x):
    """True when the copy's bullet takes under `OFF_STAT_SHARE` of its damage from the build's
    stats: it scales with a stat the build does not have (Glintstone Pebble at INT 10 is 8 of 266),
    so on this build it is a weak fixed-damage cast. Ashes in `OFF_STAT_KEEP` are kept regardless."""
    b = (x.get("contrib") or {}).get("bullet")
    if not b or x["ash"] in OFF_STAT_KEEP:
        return False
    total = sum(b.values())
    return bool(total) and sum(b[k] for k in ("str", "dex", "int", "fth", "arc")) / total < OFF_STAT_SHARE


def primary_stats(build):
    """The build's invested damage stats: at least half its highest (STR 80 / DEX 20 -> str)."""
    s = {k: build["stats"][k] for k in STATS}
    return {k for k, v in s.items() if v >= max(s.values()) / 2}


def fits_build(x, primary, weapon_row=None):
    """AOE and Fun only take a copy that suits the build's stats (user, 2026-10-08): its ash does
    not scale with stats (under `OFF_STAT_SHARE` of its damage from any stat, e.g. Waves of
    Darkness), or a primary stat carries at least `OFF_STAT_SHARE` of it. Sword of Night on STR 80
    / DEX 20 fails both (STR 0 of 493). A copy without a damage breakdown is judged on its
    weapon's scaling (`weapon_row`, EquipParamWeapon): a primary stat's `correct*` must be the
    weapon's highest. Any nonzero one was not enough: Dragonscale Blade (STR 30, DEX 60) passed on
    STR 80 (user, 2026-10-08: "1/2 of the scaling of dex? That's terrible")."""
    parts = [p for p in (x.get("contrib") or {}).values() if isinstance(p, dict)] if x else []
    total = sum(sum(p.values()) for p in parts)
    if total:
        stat = sum(p.get(k, 0) for p in parts for k in STATS)
        return stat / total < OFF_STAT_SHARE or sum(p.get(k, 0) for p in parts for k in primary) / total >= OFF_STAT_SHARE
    if weapon_row is None:
        return True
    top = max(weapon_row.get(CORRECT[k], 0) for k in STATS)
    return top > 0 and any(weapon_row.get(CORRECT[k], 0) == top for k in primary)


def _reach(x):
    return (x.get("melee_reach") or [0])[0] if x["cat"] != "bullet" else (x.get("reach") or [0, 0])[1]


def dominated(rows):
    """Ashes another ash beats everywhere, as {loser: winner}. Ash B is dominated by ash A when every
    (weapon, affinity, grip) in the pool that carries B also carries A, and on each of them A is at
    least as good on damage plus status, damage per second, ash-plus-R1s damage per second, reach,
    first hit frame and hyperarmor (poise, start frame), and strictly better on one somewhere.
    Hyperarmor is in because Savage Lion's Claw (none) dropped Waves of Darkness (233 from f15) on
    the STR build, and Waves is carried for its hyperarmor. Carian Greatsword under Carian
    Grandeur is the case that prompted it (131 of 131 carriers, user 2026-10-08). FP cost is not in
    the sweep, so a cheaper dominated ash is still dropped."""
    by = {}
    for x in rows:
        if x["cat"] == "utility":  # steps and buffs are scored on follow-ups, not damage
            continue
        by.setdefault(x["ash"], {})[x["weapon"], x.get("affinity", "Standard"), x["grip"]] = x

    def stats(x):
        ha = x.get("ha") or {}
        return (x["dmg"] + sum((x.get("status_hp") or {}).values()) + sum((x.get("status_only") or {}).values()),
                x["dps"], (x.get("string") or {}).get("dps", 0),
                _reach(x) or 0, -x["first"], ha.get("poise", 0), -ha.get("start", 999))

    out = {}
    for b, bk in by.items():
        for a, ak in by.items():
            if a == b or b in out or not bk.keys() <= ak.keys():
                continue
            pairs = [(stats(ak[k]), stats(bk[k])) for k in bk]
            if (all(sa >= sb - DOMINANCE_TOLERANCE * abs(sb) for pa, pb in pairs for sa, sb in zip(pa, pb))
                    and any(sa > sb for pa, pb in pairs for sa, sb in zip(pa, pb))):
                out[b] = a
    return out


def dominated_buffs(rows):
    """Buff ashes another buff beats everywhere, as {loser: winner}: every carrier of B carries A,
    and on each A's `score` (the follow-up with the damage the buff adds, `set_buff_values`) is at
    least B's while its first frame and gate are no later, strictly better somewhere. `dominated`
    skips utility rows, so this is the same rule on the buffs' own measure. Determination
    (`atkPlayerDmgCorrectRate` 1.3) under Royal Knight's Resolve (1.4), same timing on all 574 STR
    carriers, is the case that prompted it (user, 2026-10-12: "determination is just a downgrade
    to RKR and should never be used")."""
    by = {}
    for x in rows:
        if x["cat"] == "utility" and (x.get("buff") or {}).get("added"):
            by.setdefault(x["ash"], {})[x["weapon"], x.get("affinity", "Standard"), x["grip"]] = x

    def stats(x):
        return x["score"], -x["first"], -x.get("gate", 0.0)

    out = {}
    for b, bk in by.items():
        for a, ak in by.items():
            if a == b or b in out or not bk.keys() <= ak.keys():
                continue
            pairs = [(stats(ak[k]), stats(bk[k])) for k in bk]
            if (all(sa >= sb for pa, pb in pairs for sa, sb in zip(pa, pb))
                    and any(sa > sb for pa, pb in pairs for sa, sb in zip(pa, pb))):
                out[b] = a
    return out


def below_rank(rows):
    """Damaging ashes ranked below the best `ASH_RANK_KEEP_SHARE` of them, by each ash's best `value` in the
    pool, worst last. Kept regardless: steps and buffs (`utility`), `OFF_STAT_KEEP`, and a skill
    only one weapon carries -- a unique weapon's own skill, since unique ashes of war are already
    out of the sweep -- so dropping it would drop the weapon."""
    best, carriers = {}, collections.defaultdict(set)
    for x in rows:
        carriers[x["ash"]].add(x["weapon"])
        if x["cat"] != "utility":
            best[x["ash"]] = max(best.get(x["ash"], 0), value(x))
    # Ranked among the ashes of war only (more than one carrier): a unique's own skill is never
    # dropped, so letting it take a rank pushed the user's "rank 27" down to 28th and below.
    order = sorted((a for a in best if len(carriers[a]) > 1), key=lambda a: -best[a])
    print("ash rank: " + ", ".join(f"{i + 1} {a}" for i, a in enumerate(order)), file=sys.stderr)
    # Floor: "the top 50%" of 43 is 21, rank 22 being 51% of the way down.
    keep = int(ASH_RANK_KEEP_SHARE * len(order))
    print(f"ash rank: keeping {keep} of {len(order)} ({ASH_RANK_KEEP_SHARE:.1%})", file=sys.stderr)
    return [a for a in order[keep:] if len(carriers[a]) > 1 and a not in OFF_STAT_KEEP]


def bloodhound_carriers(roles, by_weapon):
    """Weapons for Bloodhound's Step, best first, ranked by their whole-arsenal moveset score
    (`er-mechanics-class-roles.py` `moveset_score`). R1 out of the step plays the rolling R1
    (`SwordArtsRolling_onUpdate` -> `W_AttackRightLightStep`, c0000.hks 1.17.1), not the crouch
    R1, which the step cannot reach; the sweep already scores the step's follow-up as that slot."""
    ws = (roles or {}).get("weapons", {})
    return sorted((w for w, v in ws.items() if v.get("moveset_score") is not None
                   and any(x["ash"] == "Bloodhound's Step" for x in by_weapon.get(w, []))),
                  key=lambda w: -ws[w]["moveset_score"])


def grab_copies(best, by_weapon, mobility, bloodhound=()):
    """The copies that get whole pages: Waves of Darkness on its hyperarmor carrier, Bloodhound's
    Step on `bloodhound`'s first weapon (the best whole-arsenal moveset), each other
    `MOBILITY_ASHES` ash on the weapon public builds most often put it on, then every ash whose
    hyperarmor starts by `GRAB_HA_START` and that lands by `GRAB_FIRST_HIT`, earliest hyperarmor
    first, each on its best-scoring carrier."""
    out = []

    def top(xs):
        # The undisturbed value breaks ties: the interrupt weight floors many rows at 0, and every
        # Waves of Darkness carrier sits there, so without it the first row in dict order won.
        return max(xs, key=lambda x: (x["norm"], raw_value(x))) if xs else None

    # Waves is held for its hyperarmor when ganked and for the downward wave off a ledge (user,
    # 2026-10-08), not its damage: the carrier is the one with the most poise inside that window.
    # The wave bullets (AtkParam 30000812, flat atkMag 120, isAddBaseAtk, correct row 51080) take
    # nothing from the weapon's AR, only its upgrade level and INT (224 on every Keen carrier), and
    # carry no status on any infusion, so ties go to the hardest-hitting swings, then the earliest
    # hit (user, 2026-10-08: highest-AR Keen carrier with the longest reach).
    all_waves = [x for x in best.values() if x["ash"] == "Waves of Darkness"]
    # A plain physical infusion (Keen on a DEX build, Heavy on a STR one) over a status or element
    # one, which the waves do not use.
    waves = max(all_waves, key=lambda x: ((x.get("ha") or {}).get("poise", 0),
                                          x["affinity"] in ("Keen", "Heavy", "Quality", "Standard"),
                                          raw_value(x), -x["first"])) if all_waves else None
    if waves:
        out.append(waves)
    for ash in MOBILITY_ASHES:
        order = list(bloodhound) if ash == "Bloodhound's Step" and bloodhound else mobility
        carrier = next((w for w in order if any(x["ash"] == ash for x in by_weapon[w])), None)
        if carrier:
            out.append(top([x for x in by_weapon[carrier] if x["ash"] == ash]))
    fast = {}
    for x in best.values():
        ha = x.get("ha")
        if (x["cat"] != "utility" and ha and ha["start"] <= GRAB_HA_START and x["first"] <= GRAB_FIRST_HIT
                and x["ash"] not in {y["ash"] for y in out}):
            if x["ash"] not in fast or x["norm"] > fast[x["ash"]]["norm"]:
                fast[x["ash"]] = x
    out += sorted(fast.values(), key=lambda x: (x["ha"]["start"], x["first"]))
    return out


def selftest() -> int:
    """The redundant-status and crit-row rules on hand-made slots."""
    bad = 0

    def check(name, got, want):
        nonlocal bad
        ok = got == want
        bad += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {name}: {got!r}" + ("" if ok else f" (want {want!r})"))

    def mk(w, aff, ash, cat="bullet", norm=1.0, theme="Classes", grab=False):
        return {"weapon": w, "affinity": aff, "ash": ash, "skill": ash, "cat": cat, "norm": norm,
                "theme": theme, "row": 0, "grab": grab}

    def make_slot(x, w, theme, r):
        return mk(w, x["affinity"], x["ash"], x["cat"], x["norm"], theme)
    traits = {"Icerind Hatchet": {"innate": {"frost"}}}
    # 3 of 5 copies carry frost (two Cold, one innate): 60% > 50%, so Hoarfrost Stomp goes.
    slots = [mk("Axe", "Cold", "Hoarfrost Stomp"), mk("Axe", "Cold", "Storm Blade"),
             mk("Icerind Hatchet", "Standard", "Hoarfrost Stomp"), mk("Club", "Heavy", "Endure", "utility"),
             mk("Club", "Heavy", "Bloody Slash")]
    check("frost share over slots", round(status_shares(slots, traits)[0]["frost"], 2), 0.6)
    check("frost share: fun and padding slots left out",
          round(status_shares(slots + [mk("Axe", "Cold", "x", "fun")], traits)[0]["frost"], 2), 0.6)
    pool = {"Axe": [mk("Axe", "Cold", "Hoarfrost Stomp", norm=2.0), mk("Axe", "Cold", "Storm Blade", norm=1.5),
                    mk("Axe", "Blood", "Lion's Claw", "non-bullet", 1.4), mk("Axe", "Cold", "Lion's Claw", "non-bullet", 1.2),
                    mk("Axe", "Cold", "Quickstep", "utility", 3.0)],
            "Icerind Hatchet": [mk("Icerind Hatchet", "Standard", "Hoarfrost Stomp")]}
    own = {"Hoarfrost Stomp": {"frost"}, "Bloody Slash": {"bleed"}, "Storm Blade": set()}
    s1 = [dict(s) for s in slots]
    shares, dropped, log = drop_redundant_status(s1, own, traits, lambda s: pool.get(s["weapon"], []), make_slot)
    check("dropped: Hoarfrost Stomp for frost, Bloody Slash kept (bleed 0%)", dropped, {"frost": ["Hoarfrost Stomp"]})
    check("replacement: same affinity over a higher-norm other affinity, no step for a bullet, not a row repeat",
          (s1[0]["affinity"], s1[0]["ash"]), ("Cold", "Lion's Claw"))
    check("no other copy: kept and reported", (s1[2]["ash"], log[-1].startswith("kept")), ("Hoarfrost Stomp", True))
    short = {"Axe": [mk("Axe", "Cold", "Hoarfrost Stomp"), mk("Axe", "Cold", "Storm Blade"),
                     mk("Axe", "Cold", "Quickstep", "utility")]}
    s3 = [mk("Axe", "Cold", "Hoarfrost Stomp"), mk("Axe", "Cold", "Storm Blade")] + [mk("Icerind Hatchet", "Standard", "x")] * 3
    drop_redundant_status(s3, own, traits, lambda s: short.get(s["weapon"], []), make_slot)
    check("short pool: another kind before a repeat", s3[0]["ash"], "Quickstep")
    s4 = [mk("Axe", "Cold", "Hoarfrost Stomp"), mk("Axe", "Cold", "Storm Blade"), mk("Axe", "Cold", "Quickstep", "utility"),
          mk("Icerind Hatchet", "Standard", "x"), mk("Icerind Hatchet", "Standard", "y")]
    drop_redundant_status(s4, own, traits, lambda s: short.get(s["weapon"], []), make_slot)
    check("short pool: a repeat of the row's best last", s4[0]["ash"], "Storm Blade")
    s2 = [dict(s) for s in slots[:2]] + [mk("Club", "Heavy", n) for n in ("Wild Strikes", "Endure", "Stamp (Sweep)")]
    check("at 40% frost nothing is dropped", drop_redundant_status(s2, own, traits, lambda s: pool[s["weapon"]],
                                                                   make_slot)[1], {})
    lm = [mk("Axe", "Cold", "Hoarfrost Stomp", theme="Landmark: x", grab=True) for _ in range(ROW)]
    drop_redundant_status(lm, own, traits, lambda s: pool[s["weapon"]], make_slot)
    check("a landmark block stays one copy repeated", {s["ash"] for s in lm}, {"Storm Blade"})
    cpool = [mk("Misericorde", "Cold", n, c, v) for n, c, v in
             (("Quickstep", "utility", 5.0), ("Endure", "utility", 4.0), ("Storm Blade", "bullet", 3.0),
              ("Storm Blade", "bullet", 2.9), ("Bloody Slash", "non-bullet", 2.0), ("Lion's Claw", "non-bullet", 1.0),
              ("Wild Strikes", "non-bullet", 0.9), ("Glintblade Phalanx", "bullet", 0.8), ("Repeating Thrust", "non-bullet", 0.7))]
    row, cands = crit_copies("Misericorde", "Fire", cpool, lambda n: n != "Bloody Slash", "crit")
    check("crit row: five distinct mountable ashes on the crit affinity, one step at most",
          sorted((x["ash"], x["affinity"]) for x in row),
          sorted((n, "Fire") for n in ("Quickstep", "Storm Blade", "Lion's Claw", "Wild Strikes", "Glintblade Phalanx")))
    check("crit row: damaging copies first", [x["cat"] for x in row][-1], "utility")
    check("crit candidates: every distinct mountable ash", len(cands), 7)
    print("selftest:", "FAIL" if bad else "ok")
    return 1 if bad else 0


def main() -> int:
    if sys.argv[1:] == ["--selftest"]:
        return selftest()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("rows", type=Path, nargs="+", help="one or more er-mechanics-ash-table.py --json sweeps")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--html", type=Path)
    ap.add_argument("--per-class", type=int, default=2)
    ap.add_argument("--mobility-rows", type=int, default=3)
    ap.add_argument("--aoe-top", type=int, default=15)
    ap.add_argument("--aoe-rows", type=int, default=2)
    ap.add_argument("--status-rows", type=int, default=2)
    ap.add_argument("--upgrade", type=int, default=25)
    ap.add_argument("--mirror", type=Path, default=MIRROR)
    ap.add_argument("--roles", type=Path,
                    help="er-mechanics-class-roles.py output: per class the scaling, reach and fun picks")
    ap.add_argument("--build", type=Path,
                    help="planner build JSON: with --roles, keep only the rows of the build's weapons "
                         "and the roles' picks")
    ap.add_argument("--landmark", type=int, default=LANDMARK, help="copies in a landmark block")
    ap.add_argument("--collapse-twins", action="store_true",
                    help="with --roles: one Classes row for a class whose scaling and reach picks are near-twins")
    ap.add_argument("--fun-skip-held", action="store_true",
                    help="with --roles: no Fun copy for a weapon already placed in another section")
    ap.add_argument("--interrupt-weight", action=argparse.BooleanOptionalAction, default=True,
                    help="weight each ash by the share of it delivered when the opponent answers it "
                         "(er-mechanics-ash-interrupt.py)")
    ap.add_argument("--whiff-weight", action=argparse.BooleanOptionalAction, default=True,
                    help="weight each ash by the share of it delivered when the opponent rolls the first "
                         "hit and punishes the recovery (er-mechanics-ash-whiff.py)")
    ap.add_argument("--interrupt-cache", type=Path, default=INTERRUPT_CACHE)
    ap.add_argument("--embed", type=Path,
                    help="er-mechanics-weapon-embed.py output: each weapon's R1 #1 reach per grip for `reach_w` "
                         "(default: the roles' reach, the longer grip's)")
    ap.add_argument("--ranged-cache", type=Path, default=RANGED_CACHE)
    ap.add_argument("--stage-times", type=Path, default=STAGE_TIMES,
                    help="each stage's duration, read for the watcher's estimate and written at the end")
    ap.add_argument("--counter-cache", type=Path, default=COUNTER_CACHE,
                    help="successive-counter rates of weapons and ash casts (`set_multihit_weights`)")
    ap.add_argument("--pierce-cache", type=Path, default=PIERCE_CACHE,
                    help="pierce share of each ash cast's PvP damage (`set_pierce_weights`)")
    ap.add_argument("--focus", action="append", default=[], metavar="WEAPON[:AFFINITY]",
                    help="the weapon the build is made around: a row of its best ashes after Quick, "
                         "and the first equip set (repeatable)")
    ap.add_argument("--terms-weapon", action="append", default=[],
                    help="print every row of this weapon with the terms `value` multiplies (repeatable)")
    ap.add_argument("--landing-cache", type=Path,
                    help="er-mechanics-ash-landing.py cache: weighs each landed ash's carriers by the share "
                         "of its damage they land (`set_landing_weights`)")
    ap.add_argument("--buff-cache", type=Path, default=BUFF_CACHE,
                    help="per-row buff values (`set_buff_values`) for sweeps made before the buff model")
    ap.add_argument("--shields", action=argparse.BooleanOptionalAction, default=True,
                    help="with --build: a Shields row (per size class the best-guarding shield the build "
                         "carries at medium roll, er-mechanics-shields-ranged.py) and an equip set with the "
                         "best of them in the first Combos weapon's left hand")
    ap.add_argument("--ranged", action=argparse.BooleanOptionalAction, default=True,
                    help="with --build: a Ranged row (the viable picks of data/ranged-picks.toml the build "
                         "wields) and the four equipped ammunition names")
    ap.add_argument("--parry", type=Path,
                    help="er-mechanics-parry.py --json output: weighs each damaging copy by the riposte the "
                         "corpus's parriers land on it (`pmult`), and adds a non-shield parry tool to the Shields "
                         "row when it answers the corpus's openings better than the shield picks")
    ap.add_argument("--parry-weight", action=argparse.BooleanOptionalAction, default=True,
                    help="with --parry: apply `pmult` (default on)")
    ap.add_argument("--perfect-block", type=Path,
                    help="er-mechanics-perfect-block.py --json output: weighs each damaging copy by what the "
                         "corpus's perfect blockers take off it (`bmult`: chip only on the blocked hit, its "
                         "damage-over-time status dropped, their punish unless a later hit stops it)")
    ap.add_argument("--perfect-block-weight", action=argparse.BooleanOptionalAction, default=True,
                    help="with --perfect-block: apply `bmult` (default on)")
    ap.add_argument("--interrupt-poise", type=float, help="my poise (default: the worn armor's)")
    ap.add_argument("--interrupt-jobs", type=int, default=6)
    ap.add_argument("--crit-weapon", default=CRIT_WEAPON,
                    help="weapon of the crit row, on its best riposte and backstab affinity ('' for none)")
    ap.add_argument("--rank-terms", type=Path,
                    help="write each ash's best-value copy and the terms its `value` multiplies (JSON)")
    ap.add_argument("--throttle", action=argparse.BooleanOptionalAction, default=True,
                    help="stay off CPU 0 and under 90%% of any other core (er-mechanics-ash-interrupt.throttle)")
    a = ap.parse_args()
    if a.throttle:
        _ai().throttle(_ai().allowed_cpus()[:1])
    stage = Stages(a.stage_times)
    stage("rows")
    # The sweep already drops blacklisted ashes; reading the list again keeps a sweep made before an
    # entry was added from bringing it back.
    blacklist = tomllib.loads(BLACKLIST.read_text())
    # Several sweeps (the build's Keen sweep, a Lightning sweep of the role winners, ...) are one
    # pool; a (weapon, affinity, ash, grip) row two sweeps both have is kept once.
    seen, rows = set(), []
    for path in a.rows:
        for x in json.loads(path.read_text()):
            k = (x["weapon"], x.get("affinity", "Standard"), x["ash"], x["grip"])
            if k not in seen and x["ash"] not in blacklist and not off_stat(x):
                seen.add(k)
                x.setdefault("affinity", "Standard")
                rows.append(x)
    # A buff is worth what it adds on its carrier (`er-mechanics-ash-table.BUFF_MODEL`); a sweep
    # made before that is valued here, on the cast's clock, so before the grip change.
    build_stats = ({k: json.loads(a.build.read_text())["stats"][k] for k in STATS} if a.build
                   else {"str": 18, "dex": 80, "int": 7, "fth": 8, "arc": 9})
    set_lead_in(rows, build_stats)
    set_buff_values(rows, build_stats, a.buff_cache)
    set_buff_weights(rows)
    buff_report(rows)
    # A "2H" copy is two-handed after the swap before its skill can start (`GRIP_CHANGE_FRAMES`);
    # every rule below (carrier limits, interrupt weights, dominance, rank cut, grabs) sees that.
    for x in rows:
        add_grip_change(x)
    print(f"2H rows: +{GRIP_CHANGE_FRAMES} frames to two-hand before the skill "
          f"({sum('grip_change' in x for x in rows)} of {len(rows)} rows)", file=sys.stderr)
    for ash, (last_first, min_reach) in ASH_CARRIER_LIMITS.items():
        before = sum(x["ash"] == ash for x in rows)
        rows = [x for x in rows if x["ash"] != ash
                or x["first"] <= last_first and (x.get("melee_reach") or [0])[0] > min_reach]
        print(f"{ash}: kept {sum(x['ash'] == ash for x in rows)} of {before} carriers "
              f"(first hit <= f{last_first}, reach > {min_reach} m)", file=sys.stderr)
    r1 = r1_reaches(json.loads(a.roles.read_text()) if a.roles else None, a.embed)
    traits = weapon_traits(a.embed)
    weigh(rows, a, r1, build_stats, report=True, stages=stage)
    stage("own status")
    own_status = ash_own_status(rows, build_stats, a.ranged_cache)
    print("own status: " + (", ".join(f"{n} {'/'.join(sorted(v))}" for n, v in sorted(own_status.items()) if v)
                            or "no ash builds a status of its own"), file=sys.stderr)
    stage("rank")
    for w in a.terms_weapon:
        for x in sorted((x for x in rows if x["weapon"] == w), key=lambda x: -value(x)):
            print(f"terms {w}: {x['affinity']} {x['grip']} {x['ash']}: value {value(x):.0f} = raw "
                  f"{raw_value(x):.0f} x imult {x.get('imult', 1.0):.3f} wmult {x.get('wmult')} "
                  f"mh_w {x.get('mh_w', 1.0):.3f} (counter {x.get('counter_rate')}/s, rank "
                  f"{x.get('multihit_rank')}) pierce_w {x.get('pierce_w', 1.0):.3f} buff_w "
                  f"{x.get('buff_w', 1.0):.3f} reach_w {x.get('reach_w', 1.0):.3f}", file=sys.stderr)
    # Every Endure copy the sweeps hold, before any weapon is dropped for not being a role pick.
    endure_rows = [x for x in rows if x["ash"] == "Endure"]
    beaten = dominated(rows) | dominated_buffs(rows)
    for loser, winner in sorted(beaten.items()):
        print(f"dropped {loser}: {winner} is at least as good on every carrier", file=sys.stderr)
    rows = [x for x in rows if x["ash"] not in beaten]
    cut = below_rank(rows)
    print(f"dropped below the rank cut: {', '.join(cut)}", file=sys.stderr)
    if a.rank_terms:
        # Each ash's best-`value` copy with every term `value` multiplies together.
        top = {}
        for x in rows:
            if x["cat"] != "utility" and (x["ash"] not in top or value(x) > value(top[x["ash"]])):
                top[x["ash"]] = x
        ha = lambda x: x.get("ha") or {}
        a.rank_terms.write_text(json.dumps({n: {
            "weapon": x["weapon"], "affinity": x["affinity"], "grip": x["grip"], "value": value(x),
            "score": x["score"], "status_rate": status_rate(x), "area_w": x.get("area_w", 1.0),
            "reach_w": x.get("reach_w", 1.0), "ha_w": ha_w(x), "imult": x.get("imult", 1.0), "wmult": x.get("wmult"),
            "pmult": x.get("pmult", 1.0), "bmult": x.get("bmult", 1.0), "mh_w": x.get("mh_w", 1.0),
            "counter_rate": x.get("counter_rate"), "multihit_rank": x.get("multihit_rank"),
            "pierce_share": x.get("pierce_share"), "pierce_w": x.get("pierce_w", 1.0), "dmg": x["dmg"],
            "first": x["first"], "reach": ash_reach(x), "area": x.get("area"),
            "ha_start": ha(x).get("start"), "ha_poise": ha(x).get("poise"),
            "string": x.get("string")} for n, x in top.items()}, indent=1))
    cut_rows = [x for x in rows if x["ash"] in cut]
    rows = [x for x in rows if x["ash"] not in cut]
    # The crit weapon's copies before the role filter below, which drops a weapon no class role
    # picked.
    crit_pool = [x for x in rows if x["weapon"] == a.crit_weapon] if a.crit_weapon else []
    stage("layout")
    roles = json.loads(a.roles.read_text()) if a.roles else None
    if roles and a.build:
        keep = {unicodedata.normalize("NFKD", s["name"]).encode("ascii", "ignore").decode()
                for s in json.loads(a.build.read_text())["inventory"]["slots"]}
        for r in roles["classes"].values():
            keep |= {r["scaling"], r["reach"], r.get("damage_top")} | {p["weapon"] for p in r["fun"]}
            keep |= {(r.get(h) or {}).get("weapon") for h in ("main_hand", "offhand")}
        # A near-tied main hand that is no role pick is kept for its class-row slot only, outside
        # the pool every other section picks from.
        # The class top lists too: a top weapon may join a class row on its moveset alone.
        tops = {w for r in roles["classes"].values() for w in (r.get("top") or ())}
        near_ws = {w for w, v in roles["weapons"].items()
                   if near_main_hand(roles, v) or w in tops and v["wieldable"]} - keep
        near_rows = [x for x in rows if x["weapon"] in near_ws]
        rows = [x for x in rows if x["weapon"] in keep]
    else:
        near_rows = []
    fun_picks = [p for c in sorted(roles["classes"]) for p in roles["classes"][c]["fun"]] if roles else []
    for x in rows + near_rows:
        x.setdefault("affinity", "Standard")
        x["value"] = value(x)
    # The scale per category is its `NORM_RANK`th best ash, not its best: a single status outlier
    # (Blood Spinning Slash on a Great Katana) would otherwise squash every other copy of its
    # category under the steps and buffs.
    ref = {}
    for c in CATEGORIES:
        per_ash = {}
        for x in rows:
            if x["cat"] == c:
                per_ash[x["ash"]] = max(per_ash.get(x["ash"], 0.0), x["value"])
        # Positive values only: the interrupt weight floors many ashes at 0, and on the STR 80 build
        # only 4 bullet ashes were left above it, so the 10th best was 0, the scale fell back to 1
        # and Flaming Strike's `norm` was 382 -- no `CLASS_ASH_PENALTY` could hold it to
        # `CLASS_ASH_CAP`, and it went on 17 class rows.
        vals = sorted((v for v in per_ash.values() if v > 0), reverse=True)
        ref[c] = vals[min(NORM_RANK, len(vals)) - 1] if vals else 1.0
    for x in rows + near_rows:
        x["norm"] = x["value"] / ref[x["cat"]]
    for x in crit_pool:
        x.setdefault("affinity", "Standard")
        x["value"] = value(x)
        x["norm"] = x["value"] / ref[x["cat"]]

    spec = importlib.util.spec_from_file_location("er_mechanics_ar", HERE / "er-mechanics-ar.py")
    AR = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(AR)
    tables = AR.Tables(None)
    primary = primary_stats(json.loads(a.build.read_text()))
    # A weapon with a true combo (class-roles `true_combos`) is kept in AOE and Fun whatever its
    # stats (user, 2026-10-08: Sword of Night is pure DEX "but it still might one shot").
    true_combo_ws = {w for w, v in ((roles or {}).get("weapons") or {}).items() if v.get("true_combos")}

    def fun_fits(p):
        """A fun pick's best sweep copy (or None) when the pick may be placed: a true-combo weapon,
        or one where something scales with the build's primary stats."""
        w, aff = p["weapon"], p["affinity"]
        cands = [x for x in by_weapon.get(w, []) if x["affinity"] == aff and p["ash"] in (None, x["ash"])]
        own = max(cands, key=lambda x: x["norm"], default=None)
        ok = w in true_combo_ws or fits_build(own, primary, tables.weapons.get(tables.find_weapon(w, aff)))
        return ok, own

    cls_of = {w: CLASS.get(tables.weapons[tables.find_weapon(w, "Standard")]["wepType"], "Other")
              for w in {x["weapon"] for x in rows}}

    # (weapon, affinity, ash) -> its best row (grip)
    best = {}
    for x in rows:
        k = (x["weapon"], x["affinity"], x["ash"])
        if k not in best or x["value"] > best[k]["value"]:
            best[k] = x
    by_weapon = collections.defaultdict(list)
    for x in best.values():
        by_weapon[x["weapon"]].append(x)
    near_best = {}
    for x in near_rows:
        k = (x["weapon"], x["affinity"], x["ash"])
        if k not in near_best or x["value"] > near_best[k]["value"]:
            near_best[k] = x
    near_by_weapon = collections.defaultdict(list)
    for x in near_best.values():
        near_by_weapon[x["weapon"]].append(x)

    def top_distinct(xs, n, key=lambda x: x["norm"], distinct=lambda x: x["ash"], exclude=()):
        out, seen = [], set(exclude)
        for x in sorted(xs, key=key, reverse=True):
            if distinct(x) not in seen:
                seen.add(distinct(x))
                out.append(x)
            if len(out) == n:
                break
        return out

    themes = []  # (name, [(weapon, [copies])])

    # 1. class pairs
    # A role pick carries the affinity it won its role on (`er-mechanics-class-roles.py`: the best
    # damage at the build's stats, Lightning for most DEX 80 picks), so its class row is that
    # affinity's copies alone, with that affinity's own ash values.
    role_aff = {}
    if roles:
        for r in roles["classes"].values():
            for w in (r["scaling"], r["reach"], r.get("damage_top")):
                if w and roles["weapons"][w]["affinity"]:
                    role_aff[w] = roles["weapons"][w]["affinity"]
        # The per-hand picks (class-roles `main_hand`, `offhand`) carry the affinity they won that
        # hand on, Blood or Cold included (user, 2026-10-08: "score each weapon twice"). Both go
        # over the scaling pick's, and the main hand's over the off hand's, since one weapon has
        # one class row: Bloodstained Dagger, the dagger off hand on Blood and no longer the main
        # hand, was left on the scaling pick's Heavy (user, 2026-10-09: "Why do we have a row of
        # bloodstained daggers still though?").
        for h in ("offhand", "main_hand"):
            for r in roles["classes"].values():
                p = r.get(h)
                if p:
                    role_aff[p["weapon"]] = p["affinity"]

    def class_copies(w):
        mine = [x for x in by_weapon[w] if x["affinity"] == role_aff.get(w, x["affinity"])]
        if w in role_aff and not mine:
            print(f"warning: no {role_aff[w]} {w} rows in the sweep; using every affinity", file=sys.stderr)
        return mine or by_weapon[w]

    def pair_pool(w):
        own = class_copies(w)
        util = top_distinct([x for x in own if x["cat"] == "utility"], PAIR_UTILITY)
        return [x for x in own if x["cat"] != "utility"] + util

    keen = collections.defaultdict(list)
    for w in by_weapon:
        keen[cls_of[w]].append((sum(x["norm"] for x in top_distinct(pair_pool(w), ROW)), w))
    # With `--roles` (`er-mechanics-class-roles.py`) a class keeps its best-scaling and its
    # best-reach weapon (one weapon may be both); without, its `--per-class` best ash rows.
    pair_rows, kept = [], {}
    for c in sorted(keen):
        if roles and c in roles["classes"]:
            r = roles["classes"][c]
            # The main-hand pick takes the scaling pick's place when class-roles has one: it is
            # scored on damage and moveset together, over the status affinities too.
            main = (r.get("main_hand") or {}).get("weapon") or r["scaling"]
            ws = [w for w in dict.fromkeys((main, r["reach"])) if w in by_weapon]
            # Near-twin picks (same slots, features within class-roles' `TWIN_MAX_DIFF`) share
            # one row: the scaling pick's.
            if a.collapse_twins and len(ws) == 2 and (r.get("twins") or {}).get("near"):
                ws = ws[:1]
            if not ws:
                continue
        else:
            ws = [w for _, w in sorted(keen[c], reverse=True)[:a.per_class]]
        kept[c] = ws
        if len(ws) == 1:
            pair_rows.append((ws[0], sorted(top_distinct(pair_pool(ws[0]), ROW),
                                            key=lambda x: (CATEGORIES.index(x["cat"]), -x["norm"]))))
            continue
        # The pair's copies: `ROW` slots per weapon, each slot one distinct ash.
        cand = {}
        for w in ws:
            for x in pair_pool(w):
                if (w, x["ash"]) not in cand or x["norm"] > cand[w, x["ash"]]["norm"]:
                    cand[w, x["ash"]] = x
        ashes = sorted({ash for _, ash in cand})
        slots = [w for w in ws for _ in range(ROW)]
        big = 1e6
        cost = [[-cand[w, ash]["norm"] if (w, ash) in cand else big for ash in ashes] for w in slots]
        if len(ashes) < len(slots):
            cost = [r + [big] * (len(slots) - len(ashes)) for r in cost]
        pick = hungarian(cost)
        for w in ws:
            mine = [cand[w, ashes[j]] for i, j in enumerate(pick)
                    if slots[i] == w and j < len(ashes) and (w, ashes[j]) in cand]
            pair_rows.append((w, sorted(mine, key=lambda x: (CATEGORIES.index(x["cat"]), -x["norm"]))))
    themes.append(("Class pairs (two weapons per class, ten different ashes)", pair_rows))

    # 2. mobility
    mob = [w for w in mobility_weapons(a.mirror) if w in by_weapon]
    # Bloodhound's Step's weapon (best whole-arsenal moveset) leads the section and is the only
    # one that carries it here.
    bloodhound = bloodhound_carriers(roles, by_weapon)
    if bloodhound:
        mob = [bloodhound[0]] + [w for w in mob if w != bloodhound[0]]
        print(f"Bloodhound's Step: {bloodhound[0]} (moveset "
              f"{roles['weapons'][bloodhound[0]]['moveset_score']}; next {', '.join(bloodhound[1:4])})", file=sys.stderr)
    mob_rows = []
    for w in mob[:a.mobility_rows]:
        # Steps and Endure only: a buff or Parry is utility too, and by `norm` alone Determination
        # and Royal Knight's Resolve pushed Bloodhound's Step and Quickstep off Raptor Talons, the
        # Bloodhound's Step weapon (user, 2026-10-10: "Why would we use Determination? On claws").
        lead = top_distinct([x for x in by_weapon[w] if x["cat"] == "utility" and x["ash"] in MOBILITY_ASHES
                             and (x["ash"] != "Bloodhound's Step" or not bloodhound or w == bloodhound[0])], ROW)
        lead.sort(key=lambda x: (x["ash"] not in MOBILITY_ASHES, -x["norm"]))
        lead = lead[:3] if len(lead) > 3 else lead
        rest = top_distinct(by_weapon[w], ROW - len(lead), exclude={x["ash"] for x in lead})
        mob_rows.append((w, lead + rest))
    themes.append(("Mobility (steps and Endure, on the weapons public builds use them on)", mob_rows))

    # 3. AOE
    area = {}
    for x in best.values():
        if x["cat"] == "utility" or not (x["weapon"] in true_combo_ws or fits_build(x, primary)):
            continue
        if x["area"] > area.get(x["ash"], (0, None))[0]:
            area[x["ash"]] = (x["area"], x)
    aoe = set(sorted(area, key=lambda n: -area[n][0])[:a.aoe_top])
    aoe_rows, left = [], set(aoe)
    while left:
        def carry(w):
            xs = [x for x in by_weapon[w] if x["ash"] in left and (w in true_combo_ws or fits_build(x, primary))]
            return top_distinct(xs, ROW, key=lambda x: (x["area"], x["norm"]))
        w = max(by_weapon, key=lambda w: (len(carry(w)), sum(x["area"] for x in carry(w))))
        got = carry(w)
        if not got:
            break
        aoe_rows.append((w, got))
        left -= {x["ash"] for x in got}
    themes.append((f"AOE (the {a.aoe_top} ashes with the largest hit area)", aoe_rows))

    # 4. status
    st = collections.defaultdict(list)
    for x in best.values():
        if x.get("status_hp") and x["cat"] != "utility":
            st[x["weapon"]].append(x)
    status_w = sorted(st, key=lambda w: -sum(x["value"] for x in top_distinct(
        st[w], ROW, key=lambda x: x["value"], distinct=lambda x: (x["affinity"], x["ash"]))))
    status_rows = [(w, top_distinct(st[w], ROW, key=lambda x: x["value"], distinct=lambda x: (x["affinity"], x["ash"])))
                   for w in status_w]
    themes.append(("Status (innate-status weapons, infusion x ash, status HP counted)", status_rows))

    # Sections, each a handful of one-row weapons picked by role (modelled on the user's
    # ?b=819726f64648b6: ~15 real weapons, the quick one in slot 0, landmark blocks between groups).
    combos, combo_ws = [], []
    for rc, lc in COMBOS:
        r, l = kept.get(rc, []), kept.get(lc, [])
        if rc == lc and len(r) >= 2:
            pair = (r[0], r[1])
        elif rc != lc and r and l:
            # The left-hand weapon is the off-hand class's `offhand` pick, judged on its own L1
            # chain, when the sweep has rows for it; otherwise that class's first kept weapon.
            off = ((roles or {}).get("classes", {}).get(lc, {}).get("offhand") or {}).get("weapon")
            pair = (r[0], off if off in by_weapon else l[0])
        else:
            continue
        combos.append((rc, lc, *pair))
        combo_ws += [w for w in pair if w not in combo_ws]
    pair_row_of = dict(pair_rows)
    # (theme, candidate rows best first, how many). A weapon holds one row, so a section skips a
    # weapon an earlier one already placed and takes its next candidate; a combo weapon is the
    # exception, because its set needs a row of damaging ashes.
    # `--focus`: the weapon a build is made around (user, 2026-10-12: "make a build around this
    # weapon"), its best distinct ashes on the infusion asked for, ranked or cut, as the row after
    # Quick; it is also the first equip set's main hand below.
    focus_rows = []
    for spec in a.focus:
        fw, _, faff = spec.partition(":")
        pool = [x for x in by_weapon.get(fw, []) + near_by_weapon.get(fw, []) if not faff or x["affinity"] == faff]
        if not pool:
            print(f"Focus: no sweep rows for {spec}", file=sys.stderr)
            continue
        focus_rows.append((fw, top_distinct(pool, ROW, key=lambda x: x["value"])))
        print(f"Focus: {fw} " + ", ".join(f"{x['affinity']} {x['ash']} {x['value']:.0f}" for x in focus_rows[-1][1]))
    sections = [("Quick (steps, Endure)", mob_rows, a.mobility_rows),
                *([("Focus (the weapon this build is made around)", focus_rows, len(focus_rows))] if focus_rows else []),
                # An off-hand pick outside its class's kept weapons has no class row yet; it gets
                # its own best distinct copies.
                ("Combos", [(w, pair_row_of.get(w) or sorted(top_distinct(pair_pool(w), ROW),
                                                             key=lambda x: (CATEGORIES.index(x["cat"]), -x["norm"])))
                            for w in combo_ws], len(combo_ws)),
                ("Classes (best scaling and best reach per class)", pair_rows, len(pair_rows)),
                ("AOE", aoe_rows, a.aoe_rows),
                ("Status", status_rows, a.status_rows)]

    # Landmarks: one copy repeated `LANDMARK` times between sections (user, 2026-10-08: "quickly
    # pick up a very important weapon like one with quick activation, or activation and hyperarmor
    # kick in"). The steps are already in slot 0's row, so they are not landmarks.
    grabs = [x for x in grab_copies(best, by_weapon, mob) if x["ash"] not in MOBILITY_ASHES]
    print("landmarks:", ", ".join(f"{x['affinity']} {x['weapon']} {x['ash']}" for x in grabs[:len(sections) - 1]))

    def slot(x, w, theme, r):
        # A somber weapon (a unique with its own skill) tops out at +10, not `--upgrade`.
        row = tables.weapons[tables.find_weapon(w, "Standard")]
        top = tables.max_level(row["reinforceTypeId"])
        # The planner stores a unique's built-in skill as no ash (`weaponArt` null, as the build
        # itself does), so `ash` is what gets uploaded and `skill` is what the page shows.
        own = row.get("gemMountType") == 0
        return {"weapon": w, "ash": None if own else x["ash"], "skill": x["ash"],
                "affinity": x["affinity"], "upgrade": min(a.upgrade, top),
                "theme": theme, "row": r, "cat": x["cat"], "grip": x["grip"],
                "value": round(x["value"]), "norm": round(x["norm"], 2), "area": x["area"],
                "status_hp": x.get("status_hp") or {}, "grab": theme.startswith("Landmark")}

    slots, row_start, layout = [], {}, []

    def fixed(w):
        return tables.weapons[tables.find_weapon(w, "Standard")].get("gemMountType") == 0

    def place_classes(theme, ws):
        """The Classes section. A weapon with its own fixed skill takes one copy, and those copies
        are packed five to a row after the ash rows; a short last row is topped up with the Fun
        section's fixed-skill weapons (build order) so the next section starts on a row. Every
        other weapon takes a row of `ROW` distinct ashes, assigned over the whole section by
        `spread_ashes`, so a row shows what that weapon is comparatively best at rather than the
        same four ashes everywhere. A class's first row gives one slot each to the class's other
        main hands scored within `MAIN_HAND_NEAR` of its pick, on their own affinities.

        A backup takes one copy too: a class's scaling pick when its reach/moveset pick is another
        weapon with the same right-hand slots, since that one wins on the weighted moveset score
        and the backup only hits harder (user, 2026-10-08: "I don't think we need to have full
        rows for backup weapons", Bandit's Curved Sword behind Shamshir)."""
        backups = {r["scaling"] for r in (roles or {}).get("classes", {}).values()
                   if r["scaling"] != r["reach"] and (r.get("twins") or {}).get("slot_share", 0) >= 0.9}
        # One full row per class, the moveset pick's: a main-hand pick that is another weapon takes
        # one copy, as Shamshir is the curved swords' only row (user, 2026-10-09: "does it also
        # dictate not having a full row of any curved sword except for shamshir? I want to be
        # consistent", after Hammer and Stone Club both had rows).
        backups |= {(r.get("main_hand") or {}).get("weapon") for r in (roles or {}).get("classes", {}).values()
                    if (r.get("main_hand") or {}).get("weapon") not in (None, r["reach"])}
        # No backup for a class that already brings a second weapon in through its Fun picks: that
        # one already covers the class's moveset (user, 2026-10-08, Bloodhound Claws: "in the claw
        # category, we already had venomous fang in fun category").
        no_backup = {r["scaling"] for r in (roles or {}).get("classes", {}).values()
                     if r["scaling"] in backups
                     and any(p["weapon"] not in (r["scaling"], r["reach"]) and fun_fits(p)[0] for p in r["fun"])}
        ws = [w for w in ws if w not in no_backup]
        backups -= no_backup
        if no_backup:
            print(f"Classes: no damage backup where a Fun pick covers the class: {', '.join(sorted(no_backup))}",
                  file=sys.stderr)
        # The top-damage weapon a longer one replaced as scaling pick (class-roles `damage_top`)
        # is a backup as well, after its class's rows, and so is a scaling pick the main-hand pick
        # displaced: Blood Rotten Battle Hammer, the Great Hammers' scaling pick once rot counted
        # over a battle (654.9 against 564.4), had no copy at all behind the Pickaxe main hand
        # because it was no longer a separate `damage_top` (user, 2026-10-13).
        tops = list(dict.fromkeys(
            w for r in (roles or {}).get("classes", {}).values() for w in (r.get("damage_top"), r["scaling"])
            if w and w not in ws and w not in no_backup and by_weapon.get(w)))
        backups |= set(tops)
        ws = ws + tops
        ash_ws = [w for w in ws if not fixed(w) and w not in backups]
        singles = [w for w in ws if (fixed(w) or w in backups) and by_weapon.get(w)]
        cands = {}
        for w in ash_ws:
            for x in class_copies(w):
                # Utility here is the steps, Endure and Parry, plus a buff whose damage on this
                # carrier is measured (`er-mechanics-ash-table.BUFF_MODEL`). One that adds nothing
                # to a hit on a player (Sacred Order, Shared Order) is scored only on how soon an
                # attack follows it, and would fill a weak weapon's row once the cap pushes the big
                # ashes off it.
                if (x["cat"] == "utility" and x["ash"] not in CLASS_UTILITY
                        and not (x.get("buff") or {}).get("added")):
                    continue
                if (w, x["ash"]) not in cands or x["norm"] > cands[w, x["ash"]]["norm"]:
                    cands[w, x["ash"]] = x
        # A weapon with `[class_extra_rows]` in the preferences gets that many more rows, its ashes
        # outside `CLASS_ASH_CAP` (user, 2026-10-12, on Sword Dance for the Shamshir: "The weapon is
        # good enough that we can make space for a second row with lesser ashes").
        extra_rows = {w: n for w, n in tomllib.loads(PREFS.read_text()).get("class_extra_rows", {}).items()
                      if w in ash_ws}
        # Its lesser ashes include the rank cut's: without them the Shamshir's second row ran out of
        # damaging ashes and took Seppuku at 0.20 (user, 2026-10-12: "our ranking isn't correctly
        # tuned if seppuku sqeaks in on this build").
        for x in cut_rows:
            w = x["weapon"]
            if w in extra_rows and x["affinity"] == role_aff.get(w, x["affinity"]):
                x["value"] = value(x)
                x["norm"] = x["value"] / ref[x["cat"]]
                if (w, x["ash"]) not in cands or x["norm"] > cands[w, x["ash"]]["norm"]:
                    cands[w, x["ash"]] = x
        got = spread_ashes(ash_ws, cands, {w: ROW * (1 + extra_rows.get(w, 0)) for w in ash_ws},
                           CLASS_ASH_CAP, CLASS_ASH_PENALTY, set(extra_rows))
        # Near-tied main hands (`MAIN_HAND_NEAR`) join the first row of their class.
        held = {w for _, w in row_start} | set(ws)
        shared, near_log = {}, []
        for w in ash_ws:
            c = cls_of[w]
            mh = ((roles or {}).get("classes", {}).get(c) or {}).get("main_hand")
            if not mh or any(cls_of[v] == c for v in shared):
                continue
            # The class's top list (`er-mechanics-class-roles.py` `top`) may join on a moveset no
            # member has even outside `MAIN_HAND_NEAR` (user, 2026-10-12: "It might be worth adding
            # one Urumi ... but has a different moveset": its R2s are a226_0305xx against the
            # whips' a043, at 0.626 against Hoslow's Petal Whip's 0.67).
            top = set((roles["classes"].get(c) or {}).get("top") or ())
            near = sorted(((v["main_hand"]["score"], n, v["main_hand"]["affinity"])
                           for n, v in roles["weapons"].items()
                           if v["class"] == c and n not in held and (by_weapon.get(n) or near_by_weapon.get(n))
                           and (near_main_hand(roles, v) or n in top and v["wieldable"]
                                and (v.get("main_hand") or {}).get("score") is not None)),
                          reverse=True)
            # A near-tied weapon joins only for something the row lacks (user, 2026-10-12: "only use
            # the other ranked weapons if they have a different movest, or better damage+less range,
            # or are infusable but apply a status already"): a moveset no member has, more `ar` on
            # a shorter R1 #1 than the row weapon, or an innate status on an infusable weapon.
            own = (roles["weapons"].get(w) or {}).get("main_hand") or {}
            row_aff = got[w][0]["affinity"] if got.get(w) else own.get("affinity")
            base = ((own.get("by_affinity") or {}).get(row_aff) or {}).get("features") or {}
            members, mates = [w], []
            for s, n, aff in near:
                if len(mates) == MAIN_HAND_ROW - 1:
                    break
                f = roles["weapons"][n]["main_hand"]["by_affinity"][aff]["features"]
                t = traits.get(n) or {}
                why = []
                if t.get("anims") and all(t["anims"] != (traits.get(m) or {}).get("anims") for m in members):
                    why.append("moveset")
                if (f.get("ar") and base.get("ar") and f["ar"] > base["ar"] and f.get("r1_reach")
                        and base.get("r1_reach") and f["r1_reach"] < base["r1_reach"]):
                    why.append(f"ar {f['ar']} > {base['ar']} on {f['r1_reach']} < {base['r1_reach']} m")
                # Only a status no infusable member already carries innately: Thorned Whip (50
                # bleed) beside Hoslow's Petal Whip (55 bleed, same moveset, same hit dummies,
                # more AR on this build) brought nothing but 1.0 less weight (user, 2026-10-12:
                # "I don't think we should include Thorned if its literally just a downgrade").
                held_innate = set().union(*((traits.get(m) or {}).get("innate", set()) for m in members
                                            if (traits.get(m) or {}).get("infusable")))
                new_innate = (t.get("innate") or set()) - held_innate
                if t.get("infusable") and new_innate:
                    why.append("innate " + "/".join(sorted(new_innate)))
                if not near_main_hand(roles, roles["weapons"][n]):
                    why = [y for y in why if y == "moveset"]
                if why:
                    members.append(n)
                    mates.append((n, aff))
                    near_log.append(f"{aff} {n} {s} with {own.get('affinity')} {w} ({', '.join(why)})")
            shared[w] = mates
            held |= {n for n, _ in mates}
        if near_log:
            print("Classes: near-tied main hands share the row: " + "; ".join(near_log), file=sys.stderr)
        for w in ash_ws:
            r = len(slots) // ROW
            row_start[theme, w] = len(slots)
            mates = shared.get(w, [])
            ranked = sorted(got[w], key=lambda x: -x["norm"])
            row = ranked[:ROW - len(mates)]
            row.sort(key=lambda x: (CATEGORIES.index(x["cat"]), -x["norm"]))
            row += [max(row, key=lambda x: x["norm"])] * (ROW - len(mates) - len(row)) if row else []
            slots.extend(slot(x, w, theme, r) for x in row)
            # Its extra rows hold the next-best ashes, a row each, after the mates' slots.
            later = []
            for k in range(extra_rows.get(w, 0)):
                more = ranked[ROW - len(mates) + k * ROW:ROW - len(mates) + (k + 1) * ROW]
                if more:
                    more.sort(key=lambda x: (CATEGORIES.index(x["cat"]), -x["norm"]))
                    later.append(more + [more[0]] * (ROW - len(more)))
            for n, aff in mates:
                pool = by_weapon.get(n) or near_by_weapon[n]
                ok = [x for x in pool if x["cat"] != "utility" or x["ash"] in CLASS_UTILITY]
                mine = [x for x in ok if x["affinity"] == aff]
                if not mine:
                    # The ash sweep covered other affinities only: the ash is chosen on those, and
                    # the copy is still made on the main-hand affinity it won its score on.
                    print(f"Classes: no {aff} {n} rows in the sweep; its ash is picked on "
                          f"{', '.join(sorted({x['affinity'] for x in ok}))}", file=sys.stderr)
                    mine = ok or pool
                taken = {s["skill"] for s in slots[row_start[theme, w]:]}
                x = dict(max(mine, key=lambda x: (x["ash"] not in taken, x["norm"])), affinity=aff)
                # Not a `row_start` owner: a one-copy member does not keep a later section from
                # giving the weapon its own row (Lance in AOE).
                slots.append(slot(x, n, theme, r))
            for more in later:
                r = len(slots) // ROW
                slots.extend(slot(x, w, theme, r) for x in more)
        if len(singles) % ROW and roles:
            held = {w for _, w in row_start} | set(singles)
            extra = list(dict.fromkeys(p["weapon"] for p in fun_picks
                                       if p["weapon"] not in held and fixed(p["weapon"]) and by_weapon.get(p["weapon"])
                                       and fun_fits(p)[0]))
            singles += extra[:ROW - len(singles) % ROW]
        for w in singles:
            row_start[theme, w] = len(slots)
            slots.append(slot(max(class_copies(w), key=lambda x: x["norm"]), w, theme, len(slots) // ROW))
        counts = collections.Counter(x["ash"] for w in ash_ws for x in got[w])
        print(f"Classes: {len(ash_ws)} ash rows, {len(singles)} one-copy uniques and backups; carriers per ash: "
              + ", ".join(f"{n} {k}" for n, k in sorted(((n, k) for k, n in counts.items()), reverse=True)))

    next_grab = iter(grabs)
    for k, (theme, trs, want) in enumerate(sections):
        # No landmark before Focus: it is the row the build is about, right after Quick.
        x = next(next_grab, None) if k and not theme.startswith("Focus") else None
        if x is not None:
            lm = f"Landmark: {x['affinity']} {x['weapon']} with {x['ash']}"
            layout.append((lm, len(slots)))
            slots.extend(slot(x, x["weapon"], lm, (len(slots) + i) // ROW) for i in range(a.landmark))
        layout.append((theme, len(slots)))
        if theme.startswith("Classes"):
            place_classes(theme, [w for w, _ in trs if not any(w == held for _, held in row_start)])
            continue
        placed = 0
        for w, copies in trs:
            if placed == want:
                break
            if theme != "Combos" and any(w == held for _, held in row_start):
                continue
            placed += 1
            r = len(slots) // ROW
            row_start[theme, w] = len(slots)
            row = copies[:ROW]
            # A weapon short of five distinct ashes repeats its best copy.
            row += [max(row, key=lambda x: x["norm"])] * (ROW - len(row)) if row else []
            slots.extend(slot(x, w, theme, r) for x in row)

    # Shields and Ranged (user, 2026-10-09: "Now we need to figure out how shields come in and
    # bows"): one row each, after Status and with no landmark before them, since a landmark is
    # `--landmark` copies, more than either section holds.
    build = json.loads(a.build.read_text()) if a.build else None
    main_w = combos[0][2] if combos else None
    extra_sets, ammo, greatbow = [], {}, None

    def place_row(theme, copies, pad=True):
        """`copies` from the current slot, topped up to whole rows with copies of the first unless
        `pad` is off (the next section continues the row)."""
        layout.append((theme, len(slots)))
        start, r = len(slots), len(slots) // ROW
        if pad:
            copies = copies + [copies[0]] * (-(len(slots) + len(copies)) % ROW)
        for c in copies:
            s = slot(c, c["weapon"], theme, len(slots) // ROW)
            s["note"] = c["note"]
            slots.append(s)
        return start

    stage("crit")
    crit_cands = []
    if a.crit_weapon:
        ranked = crit_affinities(a.crit_weapon, build_stats)
        print(f"Crit: {a.crit_weapon} riposte + backstab per affinity: "
              + (", ".join(f"{aff} {rp:.0f}+{bs:.0f}" for aff, rp, bs in ranked) or "none wieldable"), file=sys.stderr)
        if ranked:
            aff, rp, bs = ranked[0]
            t = _table_mod().A.AshTables()
            gems, wid = t.ash_gems(), t.find_weapon(a.crit_weapon)
            top = tables.max_level(t.reg.weapon[wid]["reinforceTypeId"])

            def mountable(ash):
                try:
                    gem = gems.get(t.find_arts(ash))
                except (SystemExit, KeyError):
                    return False
                return gem is not None and t.can_mount(wid, gem, AR.AFFINITIES.index(aff), top)[0]
            row, crit_cands = crit_copies(a.crit_weapon, aff, crit_pool, mountable,
                                          f"crit: {aff} riposte {rp:.0f}, backstab {bs:.0f}")
            if row:
                place_row(f"Crit ({aff} {a.crit_weapon}, the affinity with the best riposte and backstab)", row)
                print(f"Crit: {aff} {a.crit_weapon}: " + ", ".join(f"{x['ash']} {x['value']:.0f}" for x in row),
                      file=sys.stderr)
            else:
                print(f"Crit: no {a.crit_weapon} copy in the sweeps mounts on {aff}", file=sys.stderr)

    stage("shields")
    if a.shields and build and main_w:
        rs = row_start["Combos", main_w]
        main_aff = slots[rs]["affinity"]
        copies, picks, summary = shield_copies(a, build, main_w, main_aff, r1, build_stats)
        if copies:
            start = place_row("Shields (a medium No Skill shield, most block per weight; a parry shield per "
                              "job, infused for it)", copies, pad=not a.ranged)
            best = picks.get("block") or max(picks.values(), key=lambda p: p["guard"])
            k = next(i for i, c in enumerate(copies) if c["weapon"] == best["weapon"])
            extra_sets.append({"name": f"Shield: {main_w} + {best['weapon']}", "right": [rs],
                               "left": [start + k]})
            print(f"Shields: corpus {summary}", file=sys.stderr)
    stage("ranged")
    if a.ranged and build:
        main_reach = r1.get((main_w, "1H")) if main_w else None
        copies, ammo = ranged_copies(build, build_stats, main_reach)
        if copies:
            place_row("Ranged (the viable picks, each with its own skill or assigned ash; the seal)", copies)
        greatbow = next((c for c in copies if c["note"].startswith("greatbow:")), None)

    stage("fun and sets")
    # Fun: each class's fun picks (`er-mechanics-class-roles.py`: a mechanic few weapon, affinity
    # and ash options in the whole game have, carried by the class's strongest wieldable vector),
    # one copy each, class by class, rarest first, skipping a copy already placed above (user,
    # 2026-10-08: fun picks only have to be wieldable, weak is fine). Every section above is whole
    # rows, so this one starts on a row boundary.
    fun_slots = []
    if roles:
        placed = {(s["weapon"], s["affinity"], s.get("skill")) for s in slots}
        held_ws = {s["weapon"] for s in slots}
        for p in fun_picks:
            w, aff = p["weapon"], p["affinity"]
            # `--fun-skip-held`: a weapon already somewhere above gets no extra copy for its fun
            # mechanic.
            if a.fun_skip_held and w in held_ws:
                continue
            ok, own = fun_fits(p)
            if not ok:
                print(f"Fun: skipped {aff} {w} ({p['label']}): its damage is not mainly from "
                      f"{'/'.join(sorted(primary))}", file=sys.stderr)
                continue
            key = (w, aff, own["ash"] if own else p["ash"])
            if key in placed:
                continue
            placed.add(key)
            if own:
                s = slot(own, w, "Fun", len(slots) // ROW)
            else:
                # An affinity the ash sweep did not cover (Sacred, Occult, ...): the weapon with
                # its own skill, no numbers.
                top = tables.max_level(tables.weapons[tables.find_weapon(w, "Standard")]["reinforceTypeId"])
                s = {"weapon": w, "ash": None if fixed(w) else p["ash"], "skill": p["ash"], "affinity": aff,
                     "upgrade": min(a.upgrade, top), "theme": "Fun", "row": len(slots) // ROW, "cat": "fun",
                     "grip": p["grip"], "value": 0, "norm": 0, "area": 0, "status_hp": {}, "grab": False}
            s["fun"] = p["label"]
            fun_slots.append(s)

    # A whole page of Stormcaller and Endure, half each, so one is under the cursor anywhere on it
    # (user, 2026-10-08: "a page of stormcallers and endure are prudent"). It starts on a page; the
    # Fun copies fill the gap before it and follow it.
    def emit_fun(n):
        if not n:
            return
        more = any(t.startswith("Fun") for t, _ in layout)
        layout.append(("Fun (continued)" if more else "Fun (rare status, scaling route or skill, one each)",
                       len(slots)))
        for s in fun_slots[:n]:
            s["row"] = len(slots) // ROW
            slots.append(s)
        del fun_slots[:n]

    storm = max((x for x in rows if x["ash"] == "Stormcaller"), key=value, default=None)
    endure_w = next((w for w in mob if any(x["ash"] == "Endure" for x in by_weapon[w])), None)
    endure = max((x for x in by_weapon.get(endure_w, []) if x["ash"] == "Endure"),
                 key=lambda x: x["norm"], default=None)
    if storm and endure:
        emit_fun(min((-len(slots)) % PAGE, len(fun_slots)))
        # Fun short of the gap: Endure copies close it, so the page still starts on a page.
        gap = (-len(slots)) % PAGE
        slots.extend(slot(endure, endure["weapon"], "Quick (steps, Endure)", (len(slots) + i) // ROW)
                     for i in range(gap))
        for x, half in ((storm, PAGE // 2), (endure, PAGE - PAGE // 2)):
            lm = f"Page: {x['affinity']} {x['weapon']} with {x['ash']}"
            layout.append((lm, len(slots)))
            slots.extend(slot(x, x["weapon"], lm, (len(slots) + i) // ROW) for i in range(half))

    def weight_of(w):
        return tables.weapons[tables.find_weapon(w, "Standard")]["weight"]

    def endure_swap(sets):
        """The Endure copy for the sets' second right-hand slot: the best `value` among weapons up
        to `ENDURE_MAX_WEIGHT` that keeps every set at medium roll with the build's armor and
        talismans, else the one that keeps the most sets there."""
        best = {}
        for x in endure_rows:
            if weight_of(x["weapon"]) <= ENDURE_MAX_WEIGHT and (
                    x["weapon"] not in best or value(x) > value(best[x["weapon"]])):
                best[x["weapon"]] = x
        cands = sorted(best.values(), key=lambda x: -value(x))
        if not cands:
            print("Endure: no Endure copy on a weapon up to "
                  f"{ENDURE_MAX_WEIGHT} weight in the sweeps", file=sys.stderr)
            return None
        if not build:
            return cands[0]
        R = _sr().resources_mod()
        m, (armor, tal) = R.Model(), _sr().worn(build)
        cap = float(build["computed"]["maxEquipLoad"])

        def loads(x):
            out = []
            for s in sets:
                ws = [weight_of(slots[i]["weapon"]) for i in s["right"] + s["left"]]
                out.append(m.burden(ws + [weight_of(x["weapon"])], armor, tal) / cap)
            return out

        scored = [(sum(R.Model.weight_type(r * cap, cap) <= 2 for r in loads(x)), x) for x in cands]
        fits, pick = max(scored, key=lambda t: (t[0], value(t[1])))
        print("Endure: " + ", ".join(f"{x['affinity']} {x['weapon']} {value(x):.0f} ({weight_of(x['weapon'])} wt, "
                                     f"medium in {n}/{len(sets)} sets)" for n, x in scored[:6])
              + f"; picked {pick['affinity']} {pick['weapon']}; set loads "
              + ", ".join(f"{r:.1%}" for r in loads(pick)), file=sys.stderr)
        return pick

    # The equip sets: a main hand, the Endure swap as the second right hand (`ENDURE_MAX_WEIGHT`)
    # and one off hand, the loadout players carry (user, 2026-10-12: "they have a mainhand weapon,
    # and have an offhand, even if they 2H their weapon, and they also have a mainhand swap ... But
    # weight is a consideration"). Three copies a hand put every set past medium roll.
    sets = []
    # The focus weapon's set comes first, so it is the active one: its best copy in the right hand
    # and the off-hand pick of its class (`er-mechanics-class-roles` `offhand`) in the left when the
    # plan holds a row of it.
    for fw, copies in focus_rows:
        rs = row_start.get(("Focus (the weapon this build is made around)", fw))
        if rs is None:
            continue
        off = (((roles or {}).get("classes", {}).get(cls_of.get(fw), {}) or {}).get("offhand") or {}).get("weapon")
        ls = next((i for (t, w), i in row_start.items() if w == off), None) if off else None
        sets.append({"name": f"Focus: {copies[0]['affinity']} {fw}" + (f" + {off}" if ls is not None else ""),
                     "right": [rs], "left": [ls] if ls is not None else []})
    for rc, lc, right, left_w in combos:
        rs, ls = row_start["Combos", right], row_start["Combos", left_w]
        sets.append({"name": f"{rc} + {lc}: {right} + {left_w}", "right": [rs], "left": [ls]})
    sets += extra_sets
    swap = endure_swap(sets)
    if swap:
        emit_fun(min((-len(slots)) % PAGE, len(fun_slots)))
        gap = (-len(slots)) % PAGE
        slots.extend(slot(swap, swap["weapon"], "Quick (steps, Endure)", (len(slots) + i) // ROW)
                     for i in range(gap))
        lm = f"Page: {swap['affinity']} {swap['weapon']} with Endure (the equip sets' second right hand)"
        layout.append((lm, len(slots)))
        at = len(slots)
        slots.extend(slot(swap, swap["weapon"], lm, (len(slots) + i) // ROW) for i in range(PAGE))
        for s in sets:
            s["right"].append(at)
    # The off hand carries a different ash from the right hand (user, 2026-10-13: "its quite rare
    # that you would want the same ash of war on the mainhand as the offhand"): one-handed, L2 is
    # the off hand's skill and two-handed it is the main hand's, so a repeat is one skill twice.
    # Each left-hand slot moves to the best-valued copy of the same weapon and affinity whose ash
    # no right-hand slot of the set has; with no such copy it stays.
    for s in sets:
        taken = {slots[i]["ash"] for i in s["right"]}
        for n, li in enumerate(s["left"]):
            if slots[li]["ash"] not in taken:
                continue
            w, aff = slots[li]["weapon"], slots[li]["affinity"]
            alt = max((i for i, x in enumerate(slots) if x["weapon"] == w and x["affinity"] == aff
                       and x["ash"] not in taken), key=lambda i: slots[i].get("value", 0), default=None)
            if alt is not None:
                print(f"Set {s['name']}: off hand {aff} {w} {slots[li]['ash']} -> {slots[alt]['ash']} "
                      f"(the right hand has {slots[li]['ash']})", file=sys.stderr)
                s["left"][n] = alt
    emit_fun(len(fun_slots))

    # The last row is whole (user, 2026-10-11: "we should ensure that the final row is complete,
    # even if that means adding additional weapons with ashes that were otherwise excluded because
    # they don't meet ranking"): the rank cut's ashes, best `value` first, one copy per ash, on the
    # weapons the plan already holds.
    gap = -len(slots) % ROW
    if gap:
        held_ws = {s["weapon"] for s in slots}
        fill, seen = [], set()
        for x in sorted(cut_rows, key=lambda x: -value(x)):
            if x["weapon"] in held_ws and x["ash"] not in seen:
                x.setdefault("affinity", "Standard")
                x["value"] = value(x)
                # Outside the pool, so never normalised against a category (`norm`).
                x.setdefault("norm", 0.0)
                x.setdefault("area", 0.0)
                fill.append(x)
                seen.add(x["ash"])
            if len(fill) == gap:
                break
        if fill:
            layout.append(("Fill (best ashes below the rank cut, to complete the last row)", len(slots)))
            for x in fill:
                slots.append(slot(x, x["weapon"], "Fill", len(slots) // ROW))
            print("Fill: " + ", ".join(f"{x['affinity']} {x['weapon']} {x['ash']} {x['value']:.0f}" for x in fill),
                  file=sys.stderr)
        if len(fill) < gap:
            print(f"Fill: only {len(fill)} of {gap} slots found below the rank cut", file=sys.stderr)

    # The greatbow page (`FALL_CANCEL_NOTE`), inserted whole at the page boundary nearest the
    # middle of the finished plan; everything after it moves down a page, and a section it splits
    # carries on after it as "(continued)".
    if greatbow:
        at = (-(-len(slots) // PAGE) + 1) // 2 * PAGE
        lm = f"Page: {greatbow['weapon']} with {greatbow['ash'] or 'its own skill'} (greatbow fall cancel)"
        page = []
        for i in range(PAGE):
            s = slot(greatbow, greatbow["weapon"], lm, 0)
            s["note"] = FALL_CANCEL_NOTE
            page.append(s)
        split = max(((t, st) for t, st in layout if st < at), key=lambda e: e[1], default=None)
        layout = [(t, st + PAGE if st >= at else st) for t, st in layout]
        layout.append((lm, at))
        if split and not any(st == at + PAGE for _, st in layout) and at < len(slots):
            layout.append((split[0].split(" (continued)")[0] + " (continued)", at + PAGE))
        layout.sort(key=lambda e: e[1])
        for s in sets:
            for hand in ("right", "left"):
                s[hand] = [i + PAGE if i >= at else i for i in s[hand]]
        slots[at:at] = page
        for i, s in enumerate(slots):
            s["row"] = i // ROW
        print(f"Greatbow page: {greatbow['weapon']} on page {at // PAGE + 1}", file=sys.stderr)
    elif a.ranged and build:
        print("Greatbow page: none, the ranged plan holds no greatbow the build wields", file=sys.stderr)

    # Partners (`Partners`): a weapon that performs best with another beside it gets that other
    # within `PARTNER_ROWS` rows. When none is there, the partner's best copy takes the lowest-value
    # slot of the weapon's row that is not an equip set's, a landmark or page, a shield or ranged
    # pick, or the last copy of its weapon.
    stage("partners")
    if a.embed:
        P = Partners(json.loads(a.embed.read_text()), tables)
        in_set = {i for s in sets for hand in ("right", "left") for i in s[hand]}

        def fixed_slot(i):
            t = slots[i]["theme"]
            # A partner copy placed by this pass stays, and neither it nor the Quick rows (steps and
            # Endure, kept for mobility) are given partners of their own.
            return (i in in_set or slots[i].get("grab") or slots[i].get("partner")
                    or t.startswith(("Page", "Landmark", "Shields", "Ranged", "Quick")))

        def partner_copy(w, aff, kind):
            if kind == "offhand":
                ws = [right for rc, lc, right, _ in combos if lc == P.cls(w) and rc != lc]
                cands = [x for p in ws for x in by_weapon.get(p, ())]
            else:
                sc = P.scores(w, aff)
                top = max(sc.values(), default=0.0)
                ok = [p for p, s in sc.items() if s >= (1 - PARTNER_SLACK) * top and by_weapon.get(p)]
                p = max(ok, key=sc.get, default=None)
                cands = list(by_weapon.get(p, ())) if p else []
            if not cands:
                return None
            pw = cands[0]["weapon"]
            # The partner is there for its attacks, so it carries its best damaging ash; a utility
            # copy (Shriek of Sorrow, a step) only when it has no other.
            cands = [x for x in cands if x["cat"] != "utility"] or cands
            mine = [x for x in cands if x["affinity"] == role_aff.get(pw)] or cands
            return max(mine, key=lambda x: x["norm"])

        moved, missing = [], []
        for i in range(len(slots)):
            s = slots[i]
            if fixed_slot(i):
                continue
            w, aff = s["weapon"], s["affinity"]
            kind = P.need(w, aff)
            if not kind:
                continue
            r = i // ROW
            lo, hi = max(0, (r - PARTNER_ROWS) * ROW), min(len(slots), (r + PARTNER_ROWS + 1) * ROW)
            if any(j != i and P.ok(w, aff, slots[j]["weapon"]) for j in range(lo, hi)):
                continue
            x = partner_copy(w, aff, kind)
            if x is None:
                missing.append(f"{aff} {w} ({kind}): no partner copy in the sweeps")
                continue
            count = collections.Counter(t["weapon"] for t in slots)
            free = [j for j in range(r * ROW, min(len(slots), (r + 1) * ROW))
                    if j != i and not fixed_slot(j) and count[slots[j]["weapon"]] > 1
                    and not (slots[j]["weapon"] == w and sum(t["weapon"] == w for t in slots[r * ROW:(r + 1) * ROW]) <= 1)]
            if not free:
                missing.append(f"{aff} {w} ({kind}) on page {r // (PAGE // ROW) + 1} row {r % (PAGE // ROW) + 1}: "
                               f"no free slot in its row for {x['affinity']} {x['weapon']}")
                continue
            j = min(free, key=lambda j: slots[j]["norm"])
            gone = slots[j]
            new = slot(x, x["weapon"], gone["theme"], r)
            new["note"] = f"{kind} partner for {w}"
            new["partner"] = True
            slots[j] = new
            moved.append(f"{x['affinity']} {x['weapon']} {x['ash']} for {aff} {w} ({kind}) on page "
                         f"{r // (PAGE // ROW) + 1} row {r % (PAGE // ROW) + 1}, over {gone['affinity']} "
                         f"{gone['weapon']} {gone['skill']}")
        for m in moved:
            print(f"Partner: {m}", file=sys.stderr)
        for m in missing:
            print(f"Partner: none for {m}", file=sys.stderr)

    # Last before writing, so a partner copy placed above is held to the same rule.
    stage("redundant status")
    crit_theme = next((t for t, _ in layout if t.startswith("Crit")), None)

    def pool_of(s):
        if s["theme"] == crit_theme:
            return crit_cands
        return by_weapon.get(s["weapon"], []) + near_by_weapon.get(s["weapon"], [])
    shares, dropped, log = drop_redundant_status(slots, own_status, traits, pool_of, slot)
    print("status shares of the weapon copies: " + ", ".join(f"{k} {v:.1%}" for k, v in sorted(shares.items())),
          file=sys.stderr)
    for st_name, ashes in sorted(dropped.items()):
        print(f"dropped for {st_name} (over {OWN_STATUS_SHARE:.0%} of copies carry it): {', '.join(ashes)}",
              file=sys.stderr)
    for line in log:
        print(f"  {line}", file=sys.stderr)

    stage("write")
    a.out.write_text(json.dumps({"slots": slots, "sets": sets, **({"ammo": ammo} if ammo else {})}, indent=1))
    print(f"{len(slots)} slots, {len(slots) // ROW} rows, {-(-len(slots) // PAGE)} pages, {len(sets)} equip sets -> {a.out}")
    if ammo:
        print("  ammunition: " + ", ".join(f"{k} {v}" for k, v in sorted(ammo.items())))
    ends = [s for _, s in layout[1:]] + [len(slots)]
    for (theme, start), end in zip(layout, ends):
        print(f"  page {start // PAGE + 1} row {start % PAGE // ROW + 1}: {theme} "
              f"({-(-(end - start) // ROW)} rows, {end - start} slots)")
    if a.html:
        a.html.write_text(render(slots, layout))
        print(a.html)
    stage.finish()
    return 0


def render(slots, layout):
    starts = {start: theme for theme, start in layout}
    out = ["<html><head><meta charset=utf-8><title>Weapon pages</title><style>body{font-family:sans-serif;"
           "margin:24px}table{border-collapse:collapse;margin-bottom:20px}td{border:1px solid #888;padding:4px 6px;"
           "width:170px;vertical-align:top;font-size:13px}td.junk{color:#999;background:#f4f4f4}"
           ".c-bullet{background:#eef4ff}.c-non-bullet{background:#fff4ec}.c-melee-bullet{background:#f2ecff}"
           ".c-utility{background:#ecfff0}small{color:#555}</style></head><body>",
           "<h2>Weapon pages (acquisition order: 5 per row, 6 rows per page)</h2>",
           "<p>Each cell: weapon, infusion, ash; value = damage/s (quick weapons: ash + R1 string; steps and buffs: "
           "fastest R1 out of them; a buff adds what it adds to the hits it covers on that weapon) with status "
           "HP added; norm = value / best of its category. Colours: blue bullet, "
           "orange non-bullet, purple melee bullet, green step/buff, grey padding.</p>"]
    for p in range(0, len(slots), PAGE):
        out.append(f"<h3>Page {p // PAGE + 1}</h3><table>")
        for r in range(p, min(p + PAGE, len(slots)), ROW):
            if r in starts:
                out.append(f"<tr><th colspan={ROW} align=left>{html.escape(starts[r])}</th></tr>")
            out.append("<tr>")
            for s in slots[r:r + ROW]:
                fun = f"<br><small>fun: {html.escape(s['fun'])}</small>" if s.get("fun") else ""
                if s.get("note"):
                    fun += f"<br><small>{html.escape(s['note'])}</small>"
                if s["cat"] in ("junk", "fun"):
                    out.append(f"<td class=junk>{html.escape(s['weapon'])} +{s['upgrade']}<br>"
                               f"{html.escape(s['affinity'])} {html.escape(s.get('skill') or '')}{fun}</td>")
                    continue
                st = ", ".join(f"{k} {v}" for k, v in s["status_hp"].items())
                out.append(f"<td class='c-{s['cat'].replace(' ', '-')}'><b>{html.escape(s['weapon'])}</b><br>"
                           f"{html.escape(s['affinity'])} {html.escape(s.get('skill') or s['ash'] or '')}<br><small>{s['grip']} value "
                           f"{s['value']} norm {s['norm']}{' area ' + str(s['area']) + ' m2' if s['area'] else ''}"
                           f"{' status ' + html.escape(st) if st else ''}</small>{fun}</td>")
            out.append("</tr>")
        out.append("</table>")
    out.append("</body></html>")
    return "\n".join(out)


if __name__ == "__main__":
    sys.exit(main())
