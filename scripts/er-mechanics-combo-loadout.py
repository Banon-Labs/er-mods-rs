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
6. Ranged: one copy per viable ranged weapon the build wields, viable by the measured ranged
   score (`er-mechanics-shields-ranged.ranged_plan`; `data/ranged-picks.toml` keeps only the
   user's loadout rules), with its own skill or its assigned ash, and the four equipped
   ammunition names (`ammo`).

Last, the greatbow page (`FALL_CANCEL_NOTE`): a whole page of the ranged plan's greatbow, the one
the build wields best, inserted at the page boundary nearest the middle of the finished plan.

Equip sets: the `COMBO_SETS` measured pairs (`derive_combos`: the best main hands across classes,
each in the play mode it scores best in -- two-handed alone, one-handed with an off hand plus any
measured cross-hand link, powerstance, or a paired weapon two-handed), then one covering set per
melee weapon the plan holds that no such pair already is the best set of (`cover_sets`), its best
partner added to the plan when the plan lacks it. Every set's copies sit in one block, each weapon
beside its partners (`arrange_sets`); the Endure swap is every set's second right hand. With
`--shields`, the first combo's right hand with the block shield in left hand 1.

`--affinity` (the sweeps' pinned affinity) keeps an off-affinity copy only when its primary damage
stat is the pinned affinity's (`AffinityRule`); the crit row's weapon is the swept weapon with the
highest one-handed riposte plus backstab (`rank_crit`) unless `--crit-weapon` names one.

Writes {"slots": [...], "sets": [...], "ammo": {...}} for `planner-ash-loadout-upload.py --plan`.
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import functools
import hashlib
import html
import importlib.util
import inspect
import io
import json
import math
import os
import re
import statistics
import sys
import time
import tomllib
import unicodedata
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent


def _progress():
    """The `er-mechanics-progress.py` module (`Count`, `counted`: a stage's `  i/N <unit>` lines), loaded once."""
    key = "er_mechanics_progress"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, HERE / "er-mechanics-progress.py")
        sys.modules[key] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[key])
    return sys.modules[key]


Count, counted = _progress().Count, _progress().counted
BLACKLIST = HERE.parent / "data" / "ash-blacklist.toml"
PREFS = HERE.parent / "data" / "ash-preferences.toml"
#: `--exclude`: what the build may not hold at all, from a JSON file of {"weapons": [...],
#: "ashes": [...], "ash_affinity": [[ash, affinity], ...]}. `er-weapon-build.py --fight duel`
#: writes it from the ladder bans (`er-builds-ladder.py`), so a banned weapon or skill is gone
#: before anything is ranked rather than found in the finished build.
EXCLUDE = {"weapons": set(), "ashes": set(), "ash_affinity": set(), "rows": []}
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
#: `EquipParamWeapon` requirement field per stat key, for `one_handed`.
REQUIREMENT_FIELDS = {"str": "properStrength", "dex": "properAgility", "int": "properMagic",
                      "fth": "properFaith", "arc": "properLuck"}
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
#: Equip sets drawn by `derive_combos`, the count the hand-listed class pairs had. The pairs
#: themselves are measured, not listed (user, 2026-10-18: no hand-listed picks where an objective,
#: measured score can decide).
COMBO_SETS = 6
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
#: The crit row: one row of a weapon on the affinity whose riposte plus backstab, damage and the
#: status the crit's hits build, is highest at the build's stats (user, 2026-10-17: "we must have a
#: row of misericordes with the best infusion for its crit"). The weapon is no longer Misericorde
#: by name but the swept weapon with the highest such crit (`rank_crit`; user, 2026-10-18: no
#: hand-listed picks where an objective, measured score can decide). `--crit-weapon` overrides it.
#: Only these grips are ranked: the crit row is swapped to after a parry and used at once, and a
#: copy that must be two-handed first costs an input the swap does not expect (the rule
#: `one_handed` applies to grab blocks, user, 2026-10-18).
CRIT_GRIPS = ("1H",)
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
    for i, s in counted(list(enumerate(slots)), "slots"):
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
        for x in counted(todo, "ashes"):
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


def _class_roles():
    """The `er-mechanics-class-roles.py` module, loaded once."""
    cr = sys.modules.get("er_mechanics_class_roles")
    if cr is None:
        spec = importlib.util.spec_from_file_location("er_mechanics_class_roles", HERE / "er-mechanics-class-roles.py")
        cr = importlib.util.module_from_spec(spec)
        sys.modules["er_mechanics_class_roles"] = cr
        spec.loader.exec_module(cr)
    return cr


_CRIT = {}


def _crit_ctx():
    """(crit-affinity module, embedding, crit tables, reference defender, {weapon name: base id}),
    loaded once."""
    if not _CRIT:
        spec = importlib.util.spec_from_file_location("er_mechanics_crit_affinity",
                                                      HERE / "er-mechanics-crit-affinity.py")
        CA = sys.modules.get("er_mechanics_crit_affinity")
        if CA is None:
            CA = importlib.util.module_from_spec(spec)
            sys.modules["er_mechanics_crit_affinity"] = CA
            spec.loader.exec_module(CA)
        e = CA.SS.embed()
        t = CA.CR.load_tables()
        ids = {}
        for i in CA.CW.weapon_ids(e):
            ids.setdefault(e.art.names[i], i)
        _CRIT.update(CA=CA, e=e, t=t, d=CA.CR.reference_defender(t), ids=ids)
    return _CRIT["CA"], _CRIT["e"], _CRIT["t"], _CRIT["d"], _CRIT["ids"]


def crit_affinities(weapon, stats, grips=("1H", "2H"), allowed=lambda weapon, affinity, grip: True):
    """[(affinity, riposte, backstab)] for `weapon` at `stats`, best first: each crit's total
    (`er-mechanics-crit-affinity.rows_for`: damage through `er-mechanics-crits.reference_defender`
    plus the status HP of the crit's hits), the better of `grips` per affinity. An affinity whose
    requirements the stats miss is not listed, nor one `allowed(weapon, affinity, grip)` refuses
    (`AffinityRule`). The crit multiplier skips status build-up
    (crits.md section 2, `VERIFIED`); that a crit hit still builds the weapon's status at one R1
    hit's worth per hit is that module's model (`INFERRED`: the crit AtkParam is taken through the
    same hit slots 5..7 as any swing)."""
    CA, e, t, d, ids = _crit_ctx()
    base = ids.get(weapon)
    if base is None:
        return []
    best = {}
    for r in CA.rows_for(t, e, d, base, stats):
        if r["grip"] not in grips or not allowed(weapon, r["affinity"], r["grip"]):
            continue
        v = (r["riposte"]["total"], r["backstab"]["total"])
        if sum(v) > sum(best.get(r["affinity"], (0, 0))):
            best[r["affinity"]] = v
    return sorted(((a, *v) for a, v in best.items()), key=lambda r: -(r[1] + r[2]))


def rank_crit(by_weapon):
    """[(riposte + backstab, weapon, [(affinity, riposte, backstab)])] best first, from
    {weapon: `crit_affinities` result}; a weapon with no wieldable affinity is left out. Ties go
    to the weapon name, so the order does not depend on the dict's."""
    out = [(r[0][1] + r[0][2], w, r) for w, r in by_weapon.items() if r]
    return sorted(out, key=lambda x: (-x[0], x[1]))


def stat_scaling(AR, tables, weapon, affinity, level, stats, two_handed):
    """{stat: attack rating that stat adds} for one weapon, affinity, level and grip: each element's
    base x (influence x stat multiplier - 1), summed over the elements. `er-mechanics-ar
    .element_multiplier` combines one element's stats as 1 + the sum of those terms when every
    requirement is met, so these add up to `attack_rating`'s scaling total; the base part of the
    attack rating belongs to no stat. Status build-up is not attack rating and is left out, so a
    Blood copy's arcane bleed does not count toward its damage stat."""
    wep = tables.weapons[tables.find_weapon(weapon, affinity)]
    reinf = tables.reinforce[wep["reinforceTypeId"] + level]
    aecp = tables.aecp.get(wep["attackElementCorrectId"], {})
    two = two_handed or wep["wepType"] in AR.ALWAYS_TWO_HANDED_WEP_TYPES
    if wep.get("isDualBlade"):
        two = False
    out = dict.fromkeys(AR.STATS, 0.0)
    for _, suf, bfield, rfield, gfield in AR.ELEMENTS:
        base = wep[bfield] * reinf[rfield]
        if not base:
            continue
        for s in AR.STATS:
            aname, cfield, rf, pf = AR.STAT_FIELDS[s]
            if not aecp.get(f"is{aname}Correct_by{suf}"):
                continue
            over = aecp.get(f"overwrite{aname}CorrectRate_by{suf}", -1)
            infl = aecp.get(f"Influence{aname}CorrectRate_by{suf}", 100) * 0.01
            rate = (over if over >= 0 else wep[cfield]) * reinf[rf]
            m = AR.stat_multiplier(tables, wep[pf], stats.get(s, 0), rate, wep[gfield],
                                   AR.TWO_HAND_STR_MULT if two and s == "str" else 1.0)
            out[s] += base * (infl * m - 1.0)
    return out


def primary_stat(scaling):
    """The stat that adds the most attack rating (`stat_scaling`), or None when none adds any."""
    s, v = max(scaling.items(), key=lambda kv: kv[1], default=(None, 0.0))
    return s if v > 0 else None


class AffinityRule:
    """Which copies a build pinned to one affinity (`--affinity`) may hold. User, 2026-10-18: "A
    heavy build having blood copies is fine, as long as the bleed weapon has STR as its primary
    damage stat since that's what heavy is based off of, same for poison, or madness, or sleep."

    The primary damage stat belongs to the infusion, not to the weapon (user, 2026-10-18: "With
    each status weapon type, they have a primary damage dealing stat, that's all I meant with the
    heavy vs other infusion thing"). It is measured, not listed (`affinity_primaries`). A copy of an
    infusable weapon on another affinity is kept only when that affinity's primary stat is the
    pinned affinity's. A weapon with no row on the pinned affinity (a somber weapon) is not
    infusable to it (`infusable`), and every copy of it is kept, as is a copy on an affinity with no
    measured primary stat. With no pinned affinity, everything is kept."""

    def __init__(self, pinned, primaries=None, infusable=lambda weapon: True):
        self.pinned, self.primaries, self.infusable = pinned, primaries or {}, infusable
        self.dropped = collections.Counter()

    def ok(self, weapon, affinity, grip=None):
        if not self.pinned or affinity == self.pinned or not self.infusable(weapon):
            return True
        want, got = self.primaries.get(self.pinned), self.primaries.get(affinity)
        return want is None or got is None or got == want

    def ok_any(self, weapon, affinity):
        """`ok` for picks that name no grip; the rule does not depend on the grip."""
        return self.ok(weapon, affinity)

    def keep(self, x):
        """`ok` for a sweep row, counting what it drops per (weapon, affinity)."""
        good = self.ok(x["weapon"], x.get("affinity", "Standard"))
        if not good:
            self.dropped[x["weapon"], x.get("affinity", "Standard")] += 1
        return good


def affinity_primaries(AR, tables, weapons, stats, upgrade=25, scaling=stat_scaling):
    """({affinity: primary damage stat}, {affinity: {stat: attack rating summed over weapons}}).

    For each affinity, the attack rating each stat adds (`stat_scaling`, one-handed, top upgrade
    capped at `upgrade`) is summed over every weapon in `weapons` that has a row on it, and the stat
    with the most is the affinity's. Every damage stat is set to the build's highest one, so the
    affinity's own scaling (its reinforce rates and attack-element flags) decides, not where the
    build put its points: at the build's own STR 43 / DEX 16 a Keen copy would read as STR. A weapon
    whose requirements that spread misses on an affinity is left out of that affinity's sum."""
    flat = dict.fromkeys(AR.STATS, max(stats[k] for k in AR.STATS))
    tot = {}
    for aff in AR.AFFINITIES:
        acc = dict.fromkeys(AR.STATS, 0.0)
        n = 0
        for w in weapons:
            try:
                wep = tables.weapons[tables.find_weapon(w, aff)]
            except (SystemExit, KeyError):
                continue
            if any(wep[AR.STAT_FIELDS[s][3]] > flat[s] for s in AR.STATS):
                continue
            level = min(upgrade, tables.max_level(wep["reinforceTypeId"]))
            for s, v in scaling(AR, tables, w, aff, level, flat, False).items():
                acc[s] += v
            n += 1
        if n:
            tot[aff] = acc
    return {aff: primary_stat(v) for aff, v in tot.items() if primary_stat(v)}, tot


class WeightCost:
    """What carrying a weight of weapons costs the build, from `er-weapon-build.weight_cost`'s
    table (`--weight-cost`): `joint_pick`'s best damage x EHP with that weight on the medium-roll
    budget, over the table's best. Linear between the table's points; 0 past its last point, the
    first weight at which no armor set reaches the poise floor at medium roll."""

    def __init__(self, table: dict):
        self.ws, self.vs = list(table["weights"]), list(table["value"])

    def __call__(self, w: float) -> float:
        ws, vs = self.ws, self.vs
        if not ws or w > ws[-1] + 1e-9:
            return 0.0
        if w <= ws[0]:
            return vs[0]
        i = next(i for i in range(1, len(ws)) if ws[i] >= w - 1e-9)
        t = (w - ws[i - 1]) / (ws[i] - ws[i - 1])
        return vs[i - 1] + t * (vs[i] - vs[i - 1])


def derive_combos(roles, cr, weights, off_weights, usable, n=COMBO_SETS, aff_ok=lambda weapon, affinity: True,
                  weight=None, cost=None, carry=0.0, floor=0.0, speed=None, k=0.0, ps=None, cross=None,
                  progress=False):
    """The equip sets' (main class, off class, main weapon, off weapon) pairs, with a log line each.

    A set is played in one mode and is worth the best mode it can be played in (user, 2026-10-19:
    two-handing puts the left weapon on your back, so L1 no longer throws it). Each main hand is
    scored per grip (`MainHand`, from the roles' `by_grip`, `er-mechanics-class-roles
    .main_hand_entry`), and each off-hand candidate names the grip its mode holds the main hand in
    (`OffHand.grip`):

    - two-handed alone (`OffHand.alone`): the main hand's 2H score, no off-hand term, the left
      hand empty and its weight not carried;
    - one-handed with an off hand: the main hand's 1H score + the off hand's L1 score + the
      cross-hand term, `TRUE_COMBO_BONUS` x the landing chance of the best measured link between
      the main hand's R1/R2 and the off hand's L1, either way (`cross`, a `CrossHand`; nothing
      where the frame data measures no link);
    - powerstance: the main hand's 1H score + the dual moveset's score;
    - a paired weapon two-handed (`OffHand.two_hand`, not alone): its 2H score + its paired
      moveset's.

    Without per-grip features (roles written before 2026-10-19) a main hand has one score for
    every mode and no two-handed-alone candidate, as before.

    User, 2026-10-18: no hand-listed picks where an objective, measured score can decide. Each
    class's `main_hand` and `offhand` pick (`er-mechanics-class-roles.py`) is scored again over
    every class's candidates at once, the way `er-weapon-build.class_leads` does, so classes
    compare on one scale: the main hand with `cr.FEATURES` and `[class_pick_weights]` (`weights`),
    the off hand with `cr.OFFHAND_FEATURES` and `[offhand_pick_weights]` (`off_weights`). A pick
    keeps the affinity it won its class on when `aff_ok` allows it, else takes its best allowed
    one. The `n` best main hands each take the off hand that maximises main score + off score,
    any class, its own included (a powerstance), and without `ps` never the main hand's own weapon.
    The sum separates, so every set takes the same off hand unless
    that off hand is a set's own main weapon (user, 2026-10-18: "we don't need a variety of
    weapons if a good one exists"). `usable(weapon)` limits both hands to weapons the plan has
    rows for.

    With `cost` (a `WeightCost`) and `weight(weapon)`, the sets pay for what they weigh. The stats
    are bought for the heaviest set the plan holds (`er-weapon-build.heaviest_set`), so a set's
    weight costs nothing below the heaviest one and everything above it: the choice is a weight
    cap W, and at each cap the `n` best pairs whose weight fits under it, one per main hand. A set
    weighs its two hands plus `carry` (the Endure swap every set also holds), and `floor` is the
    heaviest set outside these pairs (a `--focus` set), which the stats already pay for. The cap
    chosen maximises mean pair score x `cost(heaviest)`. Both factors are ratios to a best: the
    pair score is a weighted share of the pool's best features, and `cost` is damage x EHP over its
    best, which is hits-to-kill traded against hits-to-die (`er-weapon-build.joint_pick`). So the
    product treats one per cent of pair score as worth one per cent of the duel exchange, the
    same exchange rate `joint_pick` itself uses between damage and HP, and no weight is set by
    hand. Without `cost` the sets are drawn as above, weight unseen.

    With `speed` (a `SetSpeed`), a set is worth its pair score x (1 + `k` x C_set), C_set the
    punish coverage of the fastest attack the set throws from neutral (user, 2026-10-18:
    "implement the speed weighting"; `_set_value`). Every main hand then takes the off hand that
    maximises that value, and the `n` sets are the best `n` by it, one per main hand: the plan's
    value is a sum over sets, so taking the largest gain one at a time is taking the largest `n`.
    A slow main hand can buy back its coverage with a fast off hand, so the sets stop sharing
    one off hand where a faster pairing covers more.

    With `ps` (a `PowerstanceOffs`), a pair that can powerstance (`can_powerstance`) is scored on
    what its L1 then throws, the dual moveset, not on the off hand's own L1 chain, which the
    behaviour script no longer plays for that pair (user, 2026-10-19: generated builds never
    powerstanced because the dual chain was never scored). Every main hand also takes any weapon
    that powerstances with it as an off-hand candidate, not only its class's off-hand pick
    (`hand_scores`), a second copy of its own weapon included (two Broadswords): the plan then
    holds both copies, and the weight cap counts the weapon's weight twice. A paired main hand
    (`PowerstanceOffs.paired`) never takes a second copy; it is offered itself two-handed, one copy,
    scored on its same-weapon dual record (`PowerstanceOffs.two_hand`), and such a set's off
    weapon is None (`combo_pair`).

    With `progress` the slow walks print their counters (`Count`): the candidate hands scored
    (`hands`), then each main hand's off hands (`mains`), then the weight caps tried (`caps`)."""
    sv = _set_value(speed, k)
    count = Count(sum(hand_picks(roles)), "hands") if progress else None
    hands = hand_scores(roles, cr, weights, off_weights, usable, aff_ok, ps, cross, count=count)
    if cost is not None and weight is not None:
        return _weighed_combos(hands, n, weight, cost, carry, floor, sv, progress)
    mains, offs_for = hands
    walk = mains if speed is not None else mains[:n]
    tick = Count(len(walk), "mains") if progress else (lambda: None)
    picks = []
    for m in walk:
        tick()
        got = best_pair([(m, o) for o in offs_for(m)], lambda m, o: sv(m, o)[0])
        if got:
            o = got[2]
            picks.append((sv(m, o), m, o))
    if speed is not None:
        picks = sorted(picks, key=lambda p: (-p[0][0], p[1][1]))[:n]
    pairs = [combo_pair(m, o) for _, m, o in picks]
    log = [combo_line(m, o, speed_note(v)) for v, m, o in picks]
    return pairs, log


def _set_value(speed, k):
    """`sv(main, off)` -> (value, s_set, C_set): the pair score, times 1 + `k` x C_set when `speed`
    (a `SetSpeed`) is given (`derive_combos`). The pair score is `set_score`, the set's own mode."""
    def sv(m, o):
        if speed is None:
            return set_score(m, o), None, None
        s, c = speed(m, o)
        return set_score(m, o) * (1.0 + k * c), s, c
    return sv


def main_score(m, o):
    """The main hand's score in the grip `o`'s mode holds it in (`OffHand.grip`): its own grip's
    score when the roles give one per grip (`MainHand`), else its one score."""
    if isinstance(m, MainHand) and m.grips is not None:
        return m.grips[getattr(o, "grip", "1H")]
    return m[0]


def set_score(m, o):
    """One set's pair score in its mode (`derive_combos`): the better hand per feature over the
    options that mode plays (`SetScorer`: the main hand in that mode's grip, and the off hand's L1,
    dual or paired moveset where the mode has one), plus the cross-hand term (`OffHand.cross`,
    one-handed with a plain off hand only, a measured link and so real extra value). No set is
    credited a two-handed main hand and an off-hand L1: two-handed alone has no off-hand option,
    and a one-handed set takes the main hand's 1H features. Hands whose features share nothing
    (no `SetScorer`) sum: main score in the mode's grip + off score (0 alone) + cross term."""
    sc = getattr(m, "scorer", None)
    if sc is not None:
        return round(sc.pair(m, o, m.bonus) + getattr(o, "cross", 0.0), 4)
    return main_score(m, o) + o[0] + getattr(o, "cross", 0.0)


def speed_note(v):
    """The `combo_line` suffix of one set's speed term, empty without one."""
    _, s, c = v
    return "" if c is None else f", s_set {s if s is not None else 'none'}, C_set {c:.3f}, value {v[0]:.4f}"


class SetSpeed:
    """`speed(main, off)` -> (s_set, C_set) for `derive_combos`, hands as `hand_scores` gives them:
    s_set the first hit frame of the soonest attack the set throws from neutral -- the main hand's
    R1 #1 (`r1_startup`), and the off hand's L1 #1 (`l1_startup`, present only when its L1 is an
    attack) or, for a powerstance (a pair the embedding has a `powerstance` record for), the dual
    chain's first attack `dual_1` -- and C_set its `PunishCoverage`. The main hand's R1 #1 is the
    grip the set's mode holds it in (`OffHand.grip`, `by_grip`), and a two-handed-alone set
    (`OffHand.alone`) throws nothing from the left hand."""

    def __init__(self, roles, cover, powerstance=()):
        self.roles, self.cover = roles, cover
        self.dual = {}
        for p in powerstance:
            s = (p.get("slots") or {}).get("dual_1") or {}
            if s.get("startup") is not None:
                key = (p["right"], p["left"])
                self.dual[key] = min(self.dual.get(key, s["startup"]), s["startup"])

    def _feat(self, weapon, hand, affinity, f, grip=None):
        h = (self.roles.get("weapons") or {}).get(weapon, {}).get(hand) or {}
        a = (h.get("by_affinity") or {}).get(affinity) or {}
        return (((a.get("by_grip") or {}).get(grip) if grip else None) or a.get("features") or {}).get(f)

    def __call__(self, m, o):
        _, mc, mw, ma = m
        _, oc, ow, oa = o
        # A pair with a powerstance record throws the dual chain on L1; `derive_combos` only pairs
        # such a main hand with that off hand as a powerstance. Two-handed alone, L1 throws nothing.
        if getattr(o, "alone", False):
            off = None
        else:
            off = self.dual[mw, ow] if (mw, ow) in self.dual else self._feat(ow, "offhand", oa, "l1_startup")
        main = self._feat(mw, "main_hand", ma, "r1_startup", getattr(o, "grip", None))
        starts = [x for x in (main, off) if x is not None]
        s = min(starts) if starts else None
        return s, (self.cover(s) if s is not None else 0.0)


def combo_pair(m, o):
    """One equip set as `derive_combos` returns it: (main class, off class, main weapon, off weapon),
    the off weapon None when the main hand is two-handed (`OffHand.two_hand`, a paired moveset or
    alone): the left hand holds nothing, so the plan carries no second copy and no left weapon."""
    if getattr(o, "two_hand", False):
        return (m[1], o[1], m[2], None)
    return (m[1], o[1], m[2], o[2])


def combo_line(m, o, extra=""):
    (_, mc, mw, ma), (os_, oc, ow, oa) = m, o
    ms, total = main_score(m, o), set_score(m, o)
    g = getattr(o, "grip", "1H")
    if getattr(o, "alone", False):
        return (f"{ma} {mw} ({mc}, main 2H {ms:.4f}) two-handed alone, one copy, no left weapon = {total:.4f}{extra}"
                f" (two-handed: L1 no longer throws a left weapon, so no off-hand term; its weight counted once)")
    if getattr(o, "two_hand", False):
        return (f"{ma} {mw} ({mc}, main {g} {ms:.4f}) two-handed, one copy (off {os_:.4f}) = {total:.4f}{extra}"
                f" (two-handed: the off-hand score is its paired moveset, the same-weapon dual record at "
                f"{oa}/{oa}; its weight counted once, no second copy)")
    cross = getattr(o, "cross", 0.0)
    if cross:
        extra = f", cross-hand {o.cross_link} +{cross:.4f}{extra}"
    if getattr(o, "ps", False):
        note = (f" (powerstance: the off-hand score is the dual moveset at its recorded affinities "
                f"{o.right_aff}/{oa}, L1 string and movement L1s; no block cost on either side)")
    elif mc == oc:
        note = " (same class, no powerstance record: the off-hand score is its L1 chain)"
    else:
        note = ""
    if mw == ow:
        note += " (two copies of one weapon, its weight counted twice)"
    grip = f"{g} " if isinstance(m, MainHand) and m.grips is not None else ""
    if getattr(m, "scorer", None) is not None:
        note += " (per feature the better hand of the two, not their sum)"
    return f"{ma} {mw} ({mc}, main {grip}{ms:.4f}) + {oa} {ow} ({oc}, off {os_:.4f}) = {total:.4f}{extra}{note}"


def second_copy(slots, rs, start=None, length=0):
    """The slot for a set's left hand when it is a second copy of the right hand's weapon (slot
    `rs`): the best-valued other slot of that weapon in its row (`start`, `length`), else anywhere
    in the plan, preferring the right hand's affinity; None when the plan holds one copy only. One
    slot cannot be worn in both hands: the upload writes one hand per slot and set
    (`planner-ash-loadout-upload.upload_with_sets`)."""
    w, aff = slots[rs]["weapon"], slots[rs]["affinity"]
    row = range(start, start + length) if start is not None else range(0)
    for scope in (row, range(len(slots))):
        got = [i for i in scope if i != rs and i < len(slots) and slots[i]["weapon"] == w]
        if got:
            return max(got, key=lambda i: (slots[i]["affinity"] == aff, slots[i].get("value", 0)))
    return None


def _weighed_combos(hands, n, weight, cost, carry, floor, sv=None, progress=False):
    """`derive_combos` with the weight cap: see its doc. `hands` is `hand_scores`'s (main hands,
    off hands per main hand); `sv` is its `_set_value` (the pair score when absent); a pick's
    score is the set's value, speed term included. With `progress`, counters (`Count`) over the
    main hands whose off hands are scored, then over the caps tried."""
    mains, offs_for = hands
    wt = {}
    sv = sv or _set_value(None, 0.0)
    notes = {}

    def pw(m, o):
        for w in (m[2], o[2]):
            if w not in wt:
                wt[w] = weight(w)
        # Two-handed, the set wears one copy (`OffHand.two_hand`).
        return wt[m[2]] + (0.0 if getattr(o, "two_hand", False) else wt[o[2]]) + carry

    def at_cap(cap):
        picks = []
        for m in mains:
            got = best_pair([(m, o) for o in offs_for(m) if pw(m, o) <= cap + 1e-9], lambda m, o: sv(m, o)[0])
            if got:
                o = got[2]
                v = sv(m, o)
                notes[m, o] = speed_note(v)
                picks.append((v[0], m, o))
        return sorted(picks, key=lambda p: (-p[0], p[1][1]))[:n]

    tick = Count(len(mains), "mains") if progress else (lambda: None)
    weights_seen = set()
    for m in mains:
        # The first `offs_for(m)` scores every off hand against the pool; later calls are cached.
        weights_seen |= {round(pw(m, o), 3) for o in offs_for(m)}
        tick()
    caps = sorted(weights_seen)
    tick = Count(len(caps), "caps") if progress else (lambda: None)
    best, widest = None, None
    for cap in caps:
        tick()
        picks = at_cap(cap)
        if not picks:
            continue
        heavy = max(floor, max(pw(m, o) for _, m, o in picks))
        mean = sum(s for s, _, _ in picks) / n
        c = {"cap": cap, "picks": picks, "heavy": heavy, "mean": mean, "mult": cost(heavy),
             "value": mean * cost(heavy)}
        widest = c
        if best is None or c["value"] > best["value"] + 1e-12:
            best = c
    if best is None:
        return [], []
    pairs = [combo_pair(m, o) for _, m, o in best["picks"]]
    log = [combo_line(m, o, f", weight {pw(m, o):g}{notes.get((m, o), '')}") for _, m, o in best["picks"]]

    def summary(c):
        return (f"heaviest set {c['heavy']:g} (cost x{c['mult']:.3f}), mean set value {c['mean']:.4f}, "
                f"value {c['value']:.4f}")
    log.append(f"weight cap {best['cap']:g}: {summary(best)}; every set weighs its two hands + {carry:g} "
               f"(Endure swap), the stats already pay for {floor:g}")
    if widest is not best:
        log.append(f"without the weight cost (cap {widest['cap']:g}): {summary(widest)}, "
                   f"{100 * (widest['value'] / best['value'] - 1):+.1f}%")
    return pairs, log


class OffHand(tuple):
    """One off-hand candidate, (score, class, weapon, affinity) as `hand_scores` gives it, with its
    features and, for a powerstance, `ps` set and the right hand's recorded affinity."""

    def __new__(cls, t, feats, ps=False, right_aff=None, two_hand=False, alone=False, cross=0.0, cross_link=None,
                measure=None, cross_max=0.0):
        o = super().__new__(cls, t)
        o.feats, o.ps, o.right_aff = feats, ps, right_aff
        #: The main hand two-handed: a paired weapon's one copy playing its paired moveset
        #: (`PowerstanceOffs.two_hand`), or (`alone`) any weapon two-handed with no off-hand term.
        #: Its weapon is the main hand's, worn once, left hand empty.
        o.two_hand = two_hand or alone
        o.alone = alone
        #: The grip the set holds the main hand in: two-handed for both kinds above, else one-handed
        #: (a plain off hand or a powerstance).
        o.grip = "2H" if o.two_hand else "1H"
        #: The cross-hand term of a one-handed set (`hand_scores`, `CrossHand`) and its link,
        #: measured on first use by `measure()` -> (term, link) or None, and at most `cross_max`.
        o._cross, o.cross_link = (None if measure else cross), cross_link
        o.measure, o.cross_max = measure, (cross_max if measure else cross)
        return o

    @property
    def cross(self):
        if self._cross is None:
            got = self.measure()
            self._cross, self.cross_link = got if got else (0.0, None)
        return self._cross

    @property
    def measured(self):
        return self._cross is not None

    def at(self, cross):
        """This candidate with its cross-hand term fixed at `cross` (`best_pair`'s bounds)."""
        return OffHand(tuple(self), self.feats, self.ps, self.right_aff, self.two_hand and not self.alone,
                       self.alone, cross, self.cross_link)


def best_pair(pairs, value):
    """(value, main, off) of the (main, off) in `pairs` with the highest (`value`, off class), or
    None. An off hand whose cross-hand term is not measured yet (`OffHand.measured`) is bounded by
    its value at 0 and at `cross_max` first, and measured only while that bound can still win, so a
    sweep over every pair (`cover_sets`) measures the few links that can change the answer."""
    rows = []
    for m, o in pairs:
        if getattr(o, "measured", True):
            v = value(m, o)
            rows.append((v, m, o))
        else:
            rows.append((value(m, o.at(o.cross_max)), m, o))
    rows.sort(key=lambda r: -r[0])
    best = None
    for hi, m, o in rows:
        if best is not None and hi < best[0] - 1e-12:
            break
        v = value(m, o)
        if best is None or (v, o[1]) > (best[0], best[2][1]):
            best = (v, m, o)
    return best


class MainHand(tuple):
    """One main-hand candidate, (score, class, weapon, affinity) as `hand_scores` gives it, the
    score the better of its grips', with `grips` {grip: score} over the grips it is wieldable in
    when the roles carry per-grip features (`by_grip`), else None: one score for every mode."""

    def __new__(cls, t, grips=None, feats=None):
        m = super().__new__(cls, t)
        m.grips = grips
        #: {grip: features}, or {None: features} without per-grip roles; the `SetScorer` and
        #: `TRUE_COMBO_BONUS` `hand_scores` values its sets with, None where the hands share no
        #: feature.
        m.feats, m.scorer, m.bonus = feats or {}, None, 0.0
        return m


class PowerstanceOffs:
    """The embedding's powerstance records as off-hand candidates (`derive_combos`).

    `records` is the embedding's `powerstance` list: one record per (right, left) weapon pair, each
    weapon at one recorded affinity, both hands' hits counted. A record is a candidate when it is
    wieldable and `can(right, left)` (`er-mechanics-powerstance-guard.can_powerstance`, the
    behaviour script's `IsEnableDualWielding`) holds. A weapon with itself is a candidate (Broadsword
    with Broadsword, most of the game's powerstances): the plan carries a second copy for the left
    hand (`second_copy`). Its features are `er-mechanics-class-roles.powerstance_features`
    and `pool` holds every candidate's, for the off-hand normalisation. `class_of(weapon, default)`
    names the left weapon's class (a katana's Wakizashi is not a katana).

    A paired weapon (`paired`) is never its own powerstance: `can` refuses the pair, so no second
    copy is carried. Its same-weapon record at one affinity on both hands is what the one copy
    plays two-handed (bd `paired-weapons-2h-is-powerstance-and-atkattribute-252-253-2026-10-19`:
    the 2H clips hit on the powerstance clips' frames), and is kept in `two_hand`
    {(weapon, affinity): features} as the main hand's two-handed off-hand term (`hand_scores`)."""

    def __init__(self, records, can, cr, class_of=lambda weapon, default: default, paired=lambda weapon: False,
                 trades=None):
        self._can_fn, self._can = can, {}
        #: `paired(weapon)`: two-handed, it plays its paired moveset (`two_hand_is_pair`).
        self.paired = paired
        self.by_right = collections.defaultdict(list)
        self.two_hand = {}
        self.pool = []
        self.unmeasured = tuple(getattr(cr, "POWERSTANCE_UNMEASURED", ()))
        # The dual strings' interrupt multipliers `er-mechanics-class-roles.py` wrote
        # (`powerstance_trades`, keyed by `powerstance_key`), so a powerstance off hand is valued
        # against the pool's answers as a plain L1 one is.
        trades = trades or {}
        for p in records:
            if not p.get("wieldable"):
                continue
            self_2h = (p["right"] == p["left"] and p["right_affinity"] == p["left_affinity"]
                       and paired(p["right"]))
            if not self_2h and not self.can(p["right"], p["left"]):
                continue
            f = (cr.powerstance_features(p, trades.get(cr.powerstance_key(p))) if trades
                 else cr.powerstance_features(p))
            if f and self_2h:
                self.pool.append(f)
                self.two_hand[p["right"], p["right_affinity"]] = f
            elif f:
                self.pool.append(f)
                self.by_right[p["right"]].append((p["left"], p["left_affinity"], p["right_affinity"], f,
                                                  class_of(p["left"], p["class"])))

    def can(self, right, left):
        if (right, left) not in self._can:
            self._can[right, left] = bool(self._can_fn(right, left))
        return self._can[right, left]


def powerstance_offs(records, tables, roles, cr):
    """`PowerstanceOffs` over the embedding's records, `can_powerstance` read from the regulation
    rows (`tables`), the left weapon's class from `roles`."""
    spec = importlib.util.spec_from_file_location("er_mechanics_powerstance_guard",
                                                  HERE / "er-mechanics-powerstance-guard.py")
    PG = sys.modules.get(spec.name)
    if PG is None:
        PG = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = PG
        spec.loader.exec_module(PG)
    reg = type("Reg", (), {"weapon": tables.weapons})()

    def paired(weapon):
        """Two-handed, `weapon` plays its paired moveset with both hands (`isDualBlade` and
        `bothHandEquipable`, `er-mechanics-weapon-embed.two_hand_is_pair`)."""
        try:
            row = tables.weapons[tables.find_weapon(weapon, "Standard")]
        except (KeyError, SystemExit):
            return False
        return bool(row.get("isDualBlade") and row.get("bothHandEquipable"))

    def can(right, left):
        # A second copy of a paired weapon adds a slot and nothing else: two-handed, the one copy
        # already swings both hands.
        if right == left and paired(right):
            return False
        try:
            r, lw = tables.find_weapon(right, "Standard"), tables.find_weapon(left, "Standard")
        except (KeyError, SystemExit):                  # a name the regulation does not have
            return False
        # The rule compares the hands' `wepmotionCategory` (and `spAtkcategory`), not `wepType`:
        # env 225 `GetEquipWeaponCategory` returns the PlayerIns+0x630 per-hand array, filled from
        # EquipParamWeapon+0xe7 (`FUN_1403beae0`, 1.16.2; the same read at 0x1403beaf0 in 1.17.1).
        # A row without the field is a loader that dropped it, never "cannot powerstance": raise.
        for w in (r, lw):
            if tables.weapons[w].get("wepmotionCategory") is None:
                raise KeyError(f"weapon row {w} has no wepmotionCategory, so IsEnableDualWielding cannot be read")
        return PG.can_powerstance(reg, r, lw)

    def class_of(w, default):
        return ((roles.get("weapons") or {}).get(w) or {}).get("class") or default
    return PowerstanceOffs(records, can, cr, class_of, paired, roles.get("powerstance_trades"))


#: The off-hand L1 string feature that measures what each main-hand feature measures, so a set is
#: valued by the better hand per feature (`SetScorer`). `ar`, `ash_value` and `crit` are the main
#: hand's alone: L1 throws no skill, no crit and has no AR of its own in `cr.OFFHAND_FEATURES`.
SHARED_FEATURES = {"r1_startup": "l1_startup", "r1_recovery": "l1_recovery", "r1_reach": "l1_reach",
                   "r1_chain_dps": "l1_chain_dps", "stamina": "l1_stamina", "parry_cost": "l1_parry_cost",
                   "pb_cost": "l1_pb_cost"}
#: Shared features measured in one unit on both hands, so both are normalised over one pool:
#: frames to the first hit (as punish coverage where the main hand carries `r1_cover`), frames from
#: the hit to the roll cancel, metres, stamina per 100 HP (`er-mechanics-class-roles.stamina_per_hp`
#: on both). `r1_chain_dps` is motion value per second (`er-mechanics-weapon-embed.summarise`
#: `mv_per_sec_full_chain`) and `l1_chain_dps` HP per second (`_left_features`), so each of those is
#: a share of its own hand's pool.
SAME_UNIT = {"r1_startup", "r1_recovery", "r1_reach", "stamina"}


class SetScorer:
    """One set's value as the better hand per feature (user, 2026-10-19), not main score + off score.

    The options a set can play are its mode's: two-handed alone, the main hand's 2H features only;
    one-handed with an off hand, its 1H features and the off hand's L1 (or, powerstanced, the dual
    moveset's); a paired weapon two-handed, its 2H features and its paired moveset's. So no set
    takes 2H AR and an off-hand L1 at once. For each feature the set takes the better option's
    share of the pool's best, and the shares are weighted and summed with `[class_pick_weights]`
    over the same total `norm_score` divides by, so a set with no off hand scores as its main hand.

    - A shared feature (`SHARED_FEATURES`) is the better of the two hands. `SAME_UNIT` ones share one
      pool; frames to the first hit are compared as punish coverage, an L1's frames read off the
      main hands' own (frames, `r1_cover`) pairs (`cover_of`).
    - The L1 out of movement (`cr.MOVE_FEATURES`) shares its string analogue's weight in the split
      `cr.offhand_weights` gives the off hand (string and movement, equal halves). The main hand's
      own movement R1s are not in the roles, so each stands in at its R1 string's share: a set is
      never credited for an off hand's movement attack beyond what its main hand's string already
      scores, and a set with no off hand scores exactly its main hand.
    - A block cost is the smaller of the two hands' penalty. The main hand's cost reads as
      `norm_score` reads it (none is 0); an off hand that does not measure it (a powerstance,
      `cr.POWERSTANCE_UNMEASURED`) is no option there.
    - An off-hand feature with no main-hand counterpart (`l1_status`) counts only in a mode with
      an off hand, at its `[offhand_pick_weights]` share of that table's total, rescaled to this one.
    - `ar`, `ash_value` and `crit` are the main hand's in the mode's grip."""

    def __init__(self, cr, weights, off_weights, main_pool, off_pool, main_dicts):
        self.lower = set(getattr(cr, "LOWER_BETTER", ()))
        self.pen = dict(getattr(cr, "PENALTIES", {}))
        self.main_pool, self.off_pool = main_pool, off_pool
        cov = {}
        for d in main_dicts:
            if d.get("r1_startup") is not None and d.get("r1_cover") is not None:
                cov.setdefault(d["r1_startup"], d["r1_cover"])
        self.cover_table = dict(sorted(cov.items()))
        offs = tuple(cr.OFFHAND_FEATURES)
        analogue = getattr(cr, "MOVE_ANALOGUE", {})
        fams = getattr(cr, "MOVE_FAMILIES", ())
        unit = cr.offhand_weights({g: 1.0 for g in analogue.values()}) if analogue else {}
        self.total = sum(weights.get(f, 0.0) for f in cr.FEATURES if f not in self.pen)
        #: (weight, main feature, off feature or None)
        self.roles, self.penalties, covered = [], [], set()
        for f in cr.FEATURES:
            g = SHARED_FEATURES.get(f)
            g = g if g in offs else None
            covered.add(g)
            w = weights.get(f, 0.0)
            if f in self.pen:
                self.penalties.append((w, f, g))
                continue
            x = next((k for k, l1 in analogue.items() if l1 == g), None) if g else None
            if x is None:
                self.roles.append((w, f, g))
                continue
            self.roles.append((w * unit.get(g, 0.0), f, g))
            for fam in fams:
                mf = f"l1_{fam}_{x}"
                if mf in offs:
                    covered.add(mf)
                    self.roles.append((w * unit.get(mf, 0.0), f, mf))
        ow = cr.offhand_weights(off_weights)
        off_total = sum(ow.get(g, 0.0) for g in offs if g not in self.pen)
        self.off_only = [(ow.get(g, 0.0) * self.total / off_total, g) for g in offs
                         if off_total and g not in covered and g not in self.pen]
        self._best, self._cache = {}, {}
        #: Whether a main hand with no crit bonus has the crit weight score its reach
        #: (`cr.norm_score`): only while reach is itself a scored feature. Over the duel spread
        #: the roles take the `r1_reach` weight off (`cr.spread_weights`), the strings crediting
        #: reach already, so the crit weight then leaves that set's total instead.
        self.reach_fill = weights.get("r1_reach", 0.0) > 0
        self.crit_w = sum(w for w, f, _ in self.roles if f == "crit")

    def cover_of(self, frames):
        """Punish coverage of an attack whose first hit lands on `frames`: the main hands' own
        coverage at that frame count, else at the nearest pooled count above it (a lower bound,
        coverage falls with frames), else at the slowest pooled one."""
        if frames is None or not self.cover_table:
            return None
        if frames in self.cover_table:
            return self.cover_table[frames]
        above = [k for k in self.cover_table if k >= frames]
        return self.cover_table[min(above) if above else max(self.cover_table)]

    def _value(self, d, f, main):
        """(value, lower is better) of feature `f` of one hand's features `d` in the space it is
        compared in: punish coverage for frames to a first hit when the main hands carry it."""
        startup = (f == "r1_startup") if main else (f == "l1_startup" or f.endswith("_startup"))
        if startup and self.cover_table:
            if main and d.get("r1_cover") is not None:
                return d["r1_cover"], False
            return self.cover_of(d.get(f)), False
        return d.get(f), f in self.lower

    def _pool_best(self, f, g, same):
        """The best value of main feature `f` (with `g` None) or off feature `g`, over the main
        pool, the off pool, or both (`same`)."""
        key = (f, g, same)
        if key not in self._best:
            vals, lower = [], False
            if g is None or same:
                for p in self.main_pool:
                    v, lower = self._value(p, f, True)
                    vals.append(v)
            if g is not None:
                for p in self.off_pool:
                    v, lower = self._value(p, g, False)
                    vals.append(v)
            vals = [v for v in vals if v]
            self._best[key] = (min(vals) if lower else max(vals)) if vals else None
        return self._best[key]

    def _pen_max(self, off, k):
        """The greatest truthy value of a penalty's paired feature `k` over the off pool (`off`)
        or the main pool, None where there is none: read once, where `value` read the 26,000-record
        off pool again for every set it scored."""
        key = ("pen", off, k)
        if key not in self._best:
            vals = [p.get(k) for p in (self.off_pool if off else self.main_pool) if p.get(k)]
            self._best[key] = max(vals) if vals else None
        return self._best[key]

    def _share(self, d, f, g, main):
        same = f in SAME_UNIT
        x, lower = self._value(d, f if main else g, main)
        best = self._pool_best(f, None if main and not same else g, same)
        if not x or not best:
            return 0.0
        return best / x if lower else x / best

    def value(self, fm, fo=None, ash=None):
        """The set's value from the main hand's features `fm` in its grip and the off hand's `fo`
        (None two-handed alone). `ash`, when given, is the set's `ash_value` share in place of the
        main hand's (`ash_share`)."""
        s = 0.0
        for w, f, g in self.roles:
            if not w:
                continue
            if f == "ash_value" and ash is not None:
                s += w * ash
                continue
            if f == "crit" and fm.get("crit") is None:
                # `norm_score`: a weapon with no crit bonus has the crit weight score its reach,
                # while reach is scored (`reach_fill`); otherwise the weight leaves the total.
                if not self.reach_fill:
                    continue
                best = self._pool_best("r1_reach", None, False)
                x = fm.get("r1_reach")
                s += w * (x / best if x and best else 0.0)
                continue
            got = self._share(fm, f, g, True)
            if fo is not None and g is not None:
                got = max(got, self._share(fo, f, g, False))
            s += w * got
        if fo is not None:
            for w, g in self.off_only:
                if w:
                    x, lower = self._value(fo, g, False)
                    best = self._pool_best(None, g, False)
                    s += w * ((best / x if lower else x / best) if x and best else 0.0)
        for w, f, g in self.penalties:
            scale = self._pen_max(False, self.pen[f])
            x = fm.get(f)
            cost = w * x / scale if x and scale is not None else 0.0
            if fo is not None and g is not None and fo.get(g) is not None:
                oscale = self._pen_max(True, self.pen[g])
                if oscale is not None:
                    cost = min(cost, w * fo[g] / oscale)
            s -= cost
        total = self.total - (self.crit_w if not self.reach_fill and fm.get("crit") is None else 0.0)
        return s / total if total else 0.0

    def pair(self, m, o, bonus=0.0):
        """`value` of main hand `m` (a `MainHand`) in `o`'s mode, plus the main hand's own
        `TRUE_COMBO_BONUS` in that grip, rounded as `hand_scores` rounds."""
        grip = getattr(o, "grip", "1H")
        key = (m[2], m[3], grip, o[2], o[3], getattr(o, "ps", False), getattr(o, "right_aff", None),
               getattr(o, "two_hand", False), getattr(o, "alone", False))
        if key not in self._cache:
            fm = m.feats.get(grip) if grip in m.feats else m.feats.get(None) or {}
            fo = None if getattr(o, "alone", False) else (getattr(o, "feats", None) or None)
            self._cache[key] = round(self.value(fm, fo, self.ash_share(m, o, grip)) + bonus * fm.get("true_combo", 0), 4)
        return self._cache[key]

    def ash_share(self, m, o, grip):
        """The set's ash term over a duel's distance bands (`ASH_BANDS`, `band_set_value`), as a
        share of the best single carrier's, or None (the roles' `ash_value`, main hand alone) when
        the main hand has no bands. User, 2026-10-19: "and far away, storm blade beats unsheathe":
        a set holding Unsheathe and Storm Blade presses each where it is better.

        Which hand L2 casts is c0000.hks `GetSwordArtInfo` (`HKS_SHIELD_ARTS`). The main hand's
        ash is its row in the set's grip and the off hand's its two-handed row: two-handing the
        left weapon casts its skill, through the same `SWORDARTS_REQUEST_RIGHT_NORMAL` request a
        right-hand two-handed cast sends (`GetSwordArtsRequestNew` lines 722-730), so the 2H row
        stands for it with the grip change on its clock (`add_grip_change`). One-handed, an off
        hand whose skill is a shield art takes L2 itself (its 1H row, else its 2H row), and one
        with a bow skill blocks it; either way the main hand's ash is then only its two-handed
        row. Not for a set whose left hand is the main weapon itself (`two_hand`) or empty
        (`alone`)."""
        if not ASH_BANDS:
            return None
        main = ASH_BANDS.get((m[2], m[3], grip))
        if main is None:
            return None
        held = [main]
        if not getattr(o, "two_hand", False) and o[2] is not None:
            off = ASH_BANDS.get((o[2], o[3], "2H"))
            left = ASH_BAND_LEFT_L2.get((o[2], o[3], "1H")) or ASH_BAND_LEFT_L2.get((o[2], o[3], "2H"))
            if grip == "1H" and left:
                held = [b for b in (ASH_BANDS.get((m[2], m[3], "2H")),) if b is not None]
                if left == "shield":
                    off = ASH_BANDS.get((o[2], o[3], "1H")) or off
            if off is not None:
                held.append(off)
        if "_band_best" not in self.__dict__:
            AI = _ai()
            self._band_best = max((AI.band_set_value([b]) for b in ASH_BANDS.values()), default=0.0)
        return _ai().band_set_value(held) / self._band_best if self._band_best > 0 else None


def hand_picks(roles, all_mains=False) -> tuple[int, int]:
    """How many candidates `hand_scores` walks, (main hands, off hands): one main hand per class,
    or with `all_mains` every weapon with main-hand features, and every weapon with off-hand
    features. Their sum is its `count`'s total."""
    def having(h):
        return sum(1 for rw in roles["weapons"].values() if (rw.get(h) or {}).get("by_affinity"))
    return (having("main_hand") if all_mains else len(roles["classes"])), having("offhand")


def pool_scorer(cr, pool):
    """`(feats, use, weights) -> cr.norm_score(feats, pool, use, weights)`, the pool's extremes read
    once (`er-mechanics-class-roles.PoolScales`) where `cr` has them; a selftest stand-in may not."""
    if hasattr(cr, "PoolScales"):
        scales = cr.PoolScales(pool)
        return lambda f, use, w: cr.norm_score(f, pool, use, w, scales=scales)
    return lambda f, use, w: cr.norm_score(f, pool, use, w)


def hand_scores(roles, cr, weights, off_weights, usable, aff_ok, ps=None, cross=None, all_mains=False,
                grip_ok=None, count=None):
    """(main hands, `offs_for`) for `derive_combos`: main hands [(score, class, weapon, affinity)]
    best first, one per class, each a `MainHand` scored per grip when the roles carry `by_grip`,
    and `offs_for(main)` the off hands that main hand can take, each an `OffHand` naming the grip
    its mode holds the main hand in, best first. With per-grip roles a main hand wieldable
    two-handed is also offered alone (`OffHand.alone`, no off-hand term), and with `cross` (a
    `CrossHand`) a plain off hand carries the cross-hand term of its pair (`OffHand.cross`,
    `TRUE_COMBO_BONUS` x the link's landing chance). With `all_mains` every weapon with main-hand
    features is a main hand, not one pick per class (`cover_sets`: every weapon its own best set).
    `grip_ok(weapon, affinity, grip)` refuses a main hand's grip, by default a grip `--exclude`
    bans (`grip_excluded`, the ladder's `ROW_BANS`: Stone Club two-handed): the grip is dropped
    from its `grips`, and a main hand left with none is no candidate.

    The off hands are every class's off-hand pick (the main hand's own weapon only with `ps`, as
    a second copy), scored with
    `cr.OFFHAND_FEATURES` and `[offhand_pick_weights]` spread over the movement families
    (`cr.offhand_weights`). With `ps` (a `PowerstanceOffs`), an off hand that can powerstance with
    the main hand is dropped from those, because its L1 chain is not what L1 throws beside that main
    hand, and every powerstance record for the main hand's weapon is a candidate instead, scored
    on the dual moveset (`cr.powerstance_features`). The off-hand pool the scores normalise over
    holds every L1 candidate and every powerstance candidate. A powerstance record has no block
    cost (`cr.POWERSTANCE_UNMEASURED`), so for a main hand with powerstance candidates every off
    hand it is offered, L1 ones included, is scored with those features left out: the two kinds
    compare on what both measure rather than on an invented cost.

    `count` (a `Count`, or any callable) is called once per candidate walked, the sum of
    `hand_picks`: each one is scored against the whole pool, so the walk is the slow part."""
    if grip_ok is None:
        def grip_ok(weapon, affinity, grip):
            return not grip_excluded(weapon, affinity, grip)

    def hand(h, use, wts, extra_pool=()):
        pool = [v["features"] for rw in roles["weapons"].values()
                for v in ((rw.get(h) or {}).get("by_affinity") or {}).values()] + list(extra_pool)
        norm = pool_scorer(cr, pool)
        out = []
        picks = sorted(roles["classes"].items())
        if h == "offhand" or all_mains:
            # Every wieldable weapon's L1, not one pick per class: each class's off-hand pick was
            # made in its own class's pool, where dividing by that class's best flips rankings
            # (measured 2026-10-19: Bloodhound Claws 0.9765 over Raptor Talons 0.9702 inside the
            # claws, Raptor 0.6666 over Bloodhound 0.6641 across every class, the pool the sets
            # use).
            # No affinity named: every affinity `aff_ok` admits is scored and the best kept. The
            # class is the weapon's own, else the class whose pick names it.
            cls_of = {p["weapon"]: c for c, r in roles["classes"].items()
                      for p in (r.get("main_hand"), r.get("offhand")) if p}
            picks = [(rw.get("class") or cls_of.get(w) or "Other", {h: {"weapon": w, "affinity": None}})
                     for w, rw in sorted(roles["weapons"].items()) if (rw.get(h) or {}).get("by_affinity")]
        for c, r in picks:
            if count is not None:
                count()
            p = r.get(h)
            if not p or not usable(p["weapon"]):
                continue
            by = ((roles["weapons"].get(p["weapon"]) or {}).get(h) or {}).get("by_affinity") or {}
            if p["affinity"] in by and aff_ok(p["weapon"], p["affinity"]):
                affs = [p["affinity"]]
            else:
                affs = [a for a in by if aff_ok(p["weapon"], a)]
            # A main hand with a measured true combo gets the class pick's `TRUE_COMBO_BONUS`
            # here too (`true_combo` on its features, per grip on `by_grip`).
            bonus = getattr(cr, "TRUE_COMBO_BONUS", 0.0) if h == "main_hand" else 0.0

            def one(f):
                return round(norm(f, use, wts) + bonus * f.get("true_combo", 0), 4)
            if h == "main_hand":
                # Per grip when the roles give it (`MainHand`): the main hand's score is the better
                # grip's, each mode then takes its own grip's (`main_score`). The pool stays the
                # best-of features, whose per-feature best is the per-grip vectors' best.
                scored = []
                for a in sorted(affs):
                    bg = by[a].get("by_grip")
                    grips = {g: one(f) for g, f in bg.items() if grip_ok(p["weapon"], a, g)} if bg else None
                    if bg and not grips:
                        continue
                    scored.append((max(grips.values()) if grips else one(by[a]["features"]), a, grips))
                if scored:
                    s, a, grips = max(scored, key=lambda t: (t[0], t[1]))
                    bg = by[a].get("by_grip")
                    out.append(MainHand((s, c, p["weapon"], a), grips,
                                        feats=dict(bg) if bg else {None: by[a]["features"]}))
                continue
            scored = [(one(by[a]["features"]), a) for a in sorted(affs)]
            if scored:
                s, a = max(scored)
                out.append(OffHand((s, c, p["weapon"], a), by[a]["features"]))
        return sorted(out, key=lambda x: (-x[0], x[1])), pool

    mains, main_pool = hand("main_hand", cr.FEATURES, weights)
    ow = cr.offhand_weights(off_weights)
    offs, off_pool = hand("offhand", cr.OFFHAND_FEATURES, ow, ps.pool if ps else ())
    # A set is valued by the better hand per feature (`SetScorer`, `set_score`) wherever the two
    # hands' features measure something in common; hands that share none (the selftest's one-feature
    # stand-ins) sum, which is the same rule over disjoint features.
    if any(SHARED_FEATURES.get(f) in cr.OFFHAND_FEATURES for f in cr.FEATURES):
        main_dicts = [d for rw in roles["weapons"].values()
                      for v in ((rw.get("main_hand") or {}).get("by_affinity") or {}).values()
                      for d in (v["features"], *(v.get("by_grip") or {}).values())]
        scorer = SetScorer(cr, weights, off_weights, main_pool, off_pool, main_dicts)
        for m in mains:
            m.scorer, m.bonus = scorer, getattr(cr, "TRUE_COMBO_BONUS", 0.0)
    shared = tuple(f for f in cr.OFFHAND_FEATURES if f not in (ps.unmeasured if ps else ()))
    off_norm = pool_scorer(cr, off_pool)
    cache = {}

    def offs_for(m):
        if m not in cache:
            mw = m[2]
            # With `ps`, a second copy of the main weapon is a candidate too: the plan then holds
            # two copies, the set wears one in each hand (`second_copy`) and pays for both in the
            # weight cap. Without `ps` the pair cannot be told apart from a powerstance, so the
            # main weapon stays out of its own off hand as before.
            # A second copy of a paired main hand is never offered: two-handed, the one copy
            # already swings both hands (`powerstance_offs.paired`).
            plain = [o for o in offs if ((ps and not ps.paired(mw)) or o[2] != mw)
                     and not (ps and ps.can(mw, o[2]))]
            # The records with the main hand on its own affinity, where the embedding has one for that
            # left weapon (`er-mechanics-weapon-embed.powerstance_affinities`), else that weapon's
            # others, so the right hand's hits are counted at the affinity the set carries.
            recs = [x for x in (ps.by_right.get(mw, ()) if ps else ())
                    if usable(x[0]) and aff_ok(x[0], x[1]) and aff_ok(mw, x[2])]
            own = {x[0] for x in recs if x[2] == m[3]}
            dual = [x for x in recs if x[2] == m[3] or x[0] not in own]
            # A paired main hand two-handed: its one copy plays the paired moveset, measured by its
            # same-weapon record on the main hand's own affinity (`PowerstanceOffs.two_hand`). It
            # competes with the off hands on the same features; worn once, so `_weighed_combos`
            # counts its weight once. The true combo stays in the main hand's score only: the
            # two-handed features carry none (`cr.powerstance_features`).
            th = ps.two_hand.get((mw, m[3])) if ps and ps.paired(mw) else None
            # The grips the main hand is wieldable in (`MainHand.grips`); without per-grip roles
            # every mode is open, as before. A one-handed mode needs the 1H grip, a two-handed one
            # the 2H grip (a semi-wieldable weapon, its STR met two-handed only, has no 1H mode).
            grips = getattr(m, "grips", None)
            one_h = grips is None or "1H" in grips
            two_h = grips is None or "2H" in grips
            if not one_h:
                plain, dual = [], []
            th = th if two_h else None
            if dual or th:
                plain = [OffHand((round(off_norm(o.feats, shared, ow), 4), *o[1:]), o.feats)
                         for o in plain]
                plain += [OffHand((round(off_norm(f, shared, ow), 4), c, left, laff), f,
                                  ps=True, right_aff=raff) for left, laff, raff, f, c in dual]
            if cross is not None:
                # The cross-hand term, one-handed with a plain off hand only: L1 throws the left
                # weapon's own attack there, and nowhere else (`CrossHand`).
                plain = [with_cross(o, mw) if not o.ps else o for o in plain]
            if th:
                plain.append(OffHand((round(off_norm(th, shared, ow), 4), m[1], mw, m[3]), th,
                                     right_aff=m[3], two_hand=True))
            elif grips is not None and "2H" in grips:
                # Two-handed alone: the main hand's 2H grip and moveset, the left hand empty, no
                # off-hand term (user, 2026-10-19). A paired weapon's two-handed mode is its paired
                # moveset (`th`) instead, when the embedding records one.
                plain.append(OffHand((0.0, m[1], mw, m[3]), {}, alone=True))
            cache[m] = sorted(plain, key=lambda x: (-x[0], x[1], x[2]))
        return cache[m]

    bonus = getattr(cr, "TRUE_COMBO_BONUS", 0.0)

    def with_cross(o, mw):
        # Measured on first use (`OffHand.cross`): `best_pair` asks only where it can matter.
        def measure(right=mw, left=o[2]):
            got = cross(right, left)
            if not got or not got[0]:
                return None
            return round(bonus * got[0], 4), f"{got[1]} p {got[0]:.2f}"
        return OffHand(tuple(o), o.feats, ps=o.ps, right_aff=o.right_aff, measure=measure, cross_max=bonus)

    return mains, offs_for


#: The main hand's attacks a cross-hand link may open from: its standing R1s and R2s (user,
#: 2026-10-19: "true combos / catches between the main's R1/R2 and the off-hand's L1 (and back)").
CROSS_OPENERS = ("r1_", "r2_")
CROSS_HAND_CACHE = Path.home() / ".cache" / "er-build-planner" / "cross-hand.json"
#: Part of every `CROSS_HAND_CACHE` key: change it when `cross_land` or the link model changes.
CROSS_HAND_MODEL = "cross-land-1"


def _combo():
    key = "er_mechanics_combo"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, HERE / "er-mechanics-combo.py")
        m = importlib.util.module_from_spec(spec)
        sys.modules[key] = m
        spec.loader.exec_module(m)
    return sys.modules[key]


def cross_land(links, land):
    """(p, "first->next") of the best cross-hand link in `links` (`er-mechanics-combo.Model
    .cross_links`): a main-hand R1/R2 (`CROSS_OPENERS`) into the off hand's L1 #1, or an off-hand L1
    into R1 #1. `p` is its landing chance `land(link, stagger)` (`er-mechanics-combo.link_land`: a
    true link 1, a tie 0.5, on the poise-broken side weighted by the corpus share the first hit
    breaks, else by whether it breaks the median victim). None when none lands."""
    best = None
    for lk in links:
        f, nx = lk["first"], lk["next"]
        if not ((f.startswith(CROSS_OPENERS) and nx == "left_1") or (f.startswith("left_") and nx == "r1_1")):
            continue
        st = lk.get("stagger")
        p = land(lk, float(bool(lk.get("breaks_victim"))) if st is None else st)
        if p > 0 and (best is None or p > best[0]):
            best = (round(p, 4), f"{f}->{nx}")
    return best


class CrossHand:
    """`cross(right, left)` -> (landing chance, link) or None, the cross-hand term's measurement for
    `hand_scores`: `er-mechanics-combo.Model.cross_links` with `right` one-handed and `left`'s
    off-hand L1, read by `cross_land`. Only where L1 plays the left weapon's own attack
    (`left_mode` 'offhand'): a powerstance pair throws the dual chain and is scored on it, a shield
    guards. The stagger shares are the mirror's builds `POOL_RL_WINDOW` either side of `rl`. Cached
    per (`CROSS_HAND_MODEL`, rl, right, left) in `path`."""

    def __init__(self, rl, mirror, path=CROSS_HAND_CACHE, model=None, module=None):
        self.rl, self.mirror, self.path = rl, mirror, path
        self.model, self.C = model, module
        self.cache = json.loads(path.read_text()) if path and path.exists() else {}
        self.new = 0

    def __call__(self, right, left):
        k = f"{CROSS_HAND_MODEL}|{self.rl}|{right}|{left}"
        if k not in self.cache:
            self.cache[k] = self._measure(right, left)
            self.new += 1
        got = self.cache[k]
        return tuple(got) if got else None

    def _measure(self, right, left):
        C = self.C = self.C or _combo()
        if self.model is None:
            self.model = C.Model(rl=self.rl, window=_class_roles().POOL_RL_WINDOW, mirror=self.mirror)
        reg = self.model.reg
        try:
            r, lw = reg.find_weapon(right), reg.find_weapon(left)
        except SystemExit:                              # a name the regulation does not have
            return None
        if C.left_mode(reg, r, lw) != "offhand":
            return None
        try:
            links = self.model.cross_links(r, lw)
        except Exception:                               # weapon rows the attack module cannot read
            return None
        got = cross_land(links, C.link_land)
        return list(got) if got else None

    def save(self):
        if self.path and self.new:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.cache), encoding="utf-8")


def set_weapons(m, o):
    """The weapons one set wears, right hand first: one for a two-handed set (`OffHand.two_hand`),
    else the main and the off weapon (the same weapon twice for two copies)."""
    return (m[2],) if getattr(o, "two_hand", False) else (m[2], o[2])


def cover_sets(hands, weapons, value, fallback=None, count=None):
    """Every weapon of `weapons` its best equip set (user, 2026-10-19: every melee weapon in the plan
    in at least one set): the best by `value(main, off)` over every set that holds it, as the main
    hand in any mode `offs_for` offers it (two-handed alone or paired, one-handed with an off hand,
    powerstanced) or as another main hand's off hand or powerstance partner. `hands` is
    `hand_scores(..., all_mains=True)`, so the partner may be any usable weapon, held by the plan or
    not. A weapon whose every set is worth 0 (past the weight table) takes the best by `fallback`.

    Returns ({weapon: (value, main, off)}, sets) with sets [(value, main, off, covers)], best first:
    one entry per distinct set, `covers` the weapons it is the best set of, so two weapons that are
    each other's best partner share one set.

    `count` is called once per main hand (scoring its every off hand, `offs_for`, the slow part)
    and once per weapon covered."""
    mains, offs_for = hands
    tick = count or (lambda: None)
    by_w = {m[2]: m for m in mains}
    as_left = collections.defaultdict(list)
    for m in mains:
        for o in offs_for(m):
            if not o.two_hand and o[2] != m[2]:
                as_left[o[2]].append((m, o))
        tick()
    best = {}
    for w in weapons:
        tick()
        pairs = ([(by_w[w], o) for o in offs_for(by_w[w])] if w in by_w else []) + as_left.get(w, [])
        got = best_pair(pairs, value)
        if got and got[0] <= 0 and fallback is not None:
            got = best_pair(pairs, fallback)
        if got:
            best[w] = got
    sets = {}
    for w, (v, m, o) in best.items():
        k = (m[2], m[3], o.two_hand and o.alone, set_weapons(m, o), None if o.two_hand else o[3], o.ps)
        sets.setdefault(k, [v, m, o, []])[3].append(w)
    return best, sorted((tuple(s) for s in sets.values()), key=lambda s: (-s[0], s[1][2]))


#: The farthest apart, in slots, `arrange_sets` lets the two copies of one set sit before it gives
#: the set a copy of its own beside the other weapon: one row's width, so both are on screen at
#: once wherever the row breaks.
SET_NEAR = ROW - 1


def arrange_sets(sets, near=SET_NEAR):
    """Acquisition order for the equip sets' copies (user, 2026-10-19: a weapon in k sets sits next to
    its k partners). `sets` is [(right,) or (right, left)] weapon names; returns (order, at): `order`
    the weapons in slot order, one copy each where it can serve every set it is in, and `at` per
    set the positions in `order` of its copies, right hand first.

    A placement over the set graph (weapons nodes, two-weapon sets edges) that keeps partners close:
    per connected part, its most-connected weapon first, then repeatedly the unplaced weapon with
    the most links into the line so far, put on whichever end leaves its links shorter. A hub with
    four partners ends in the middle with two on each side (1 and 2 slots away). A set left more
    than `near` apart gets a second copy of its more-connected weapon (the hub that could not sit
    beside every partner) on the far partner's outer side, the only case that adds a copy, so every
    set ends within `near`."""
    adj = collections.defaultdict(collections.Counter)
    nodes = []
    for s in sets:
        for w in s:
            if w not in nodes:
                nodes.append(w)
        if len(s) == 2 and s[0] != s[1]:
            adj[s[0]][s[1]] += 1
            adj[s[1]][s[0]] += 1
    rank = {w: i for i, w in enumerate(nodes)}
    line_all, left = [], list(nodes)
    while left:
        hub = max(left, key=lambda w: (len(adj[w]), -rank[w]))
        line = [hub]
        left.remove(hub)
        while True:
            on = set(line)
            cands = [w for w in left if any(p in on for p in adj[w])]
            if not cands:
                break
            w = max(cands, key=lambda w: (sum(n for p, n in adj[w].items() if p in on), -len(adj[w]), -rank[w]))
            pos = {x: i for i, x in enumerate(line)}
            nb = [(pos[p], n) for p, n in adj[w].items() if p in pos]
            front = sum(n * (i + 1) for i, n in nb)
            back = sum(n * (len(line) - i) for i, n in nb)
            if front < back:
                line.insert(0, w)
            else:
                line.append(w)
            left.remove(w)
        line_all += line
    # Tokens, so a second copy of a weapon is its own position.
    order = [[w] for w in line_all]
    first = {w: t for t in reversed(order) for w in t}
    refs = []
    for s in sets:
        if len(s) == 2 and s[0] == s[1]:
            # Two copies of one weapon (a powerstance on itself): the second copy beside the first.
            dup = [s[0]]
            order.insert(next(i for i, t in enumerate(order) if t is first[s[0]]) + 1, dup)
            refs.append([first[s[0]], dup])
        else:
            refs.append([first[w] for w in s])
    for _ in range(2 * len(sets) + 1):
        pos = {id(t): i for i, t in enumerate(order)}
        far = [(abs(pos[id(r[0])] - pos[id(r[1])]), k) for k, r in enumerate(refs) if len(r) == 2]
        far = [f for f in far if f[0] > near]
        if not far:
            break
        _, k = max(far)
        a_, b_ = refs[k]
        # The more-connected weapon (the hub that could not sit beside every partner) gets a copy
        # beside the far one, on its outer side, so no other set is split by it.
        move, keep = (a_, b_) if len(adj[a_[0]]) >= len(adj[b_[0]]) else (b_, a_)
        dup = [move[0]]
        order.insert(pos[id(keep)] + (1 if pos[id(keep)] > pos[id(move)] else 0), dup)
        refs[k] = [dup if t is move else t for t in refs[k]]
    pos = {id(t): i for i, t in enumerate(order)}
    return [t[0] for t in order], [tuple(pos[id(t)] for t in r) for r in refs]


def crit_copies(weapon, affinity, pool, mountable, note):
    """(row, every candidate): `weapon`'s distinct ashes that mount on `affinity` (`mountable(ash)`),
    each its best copy by `norm` made on `affinity` whatever affinity the sweep valued it on. The
    row is the best `ROW` of them with one step or buff at most, as a class pair's row is drawn
    (`PAIR_UTILITY`), damaging ones first."""
    # A damaging copy valued at nothing is not a pick (`pair_pool`).
    ok = [x for x in pool if x["weapon"] == weapon and mountable(x["ash"])
          and (x["cat"] == "utility" or x["norm"] > 0)]
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
    for x in counted(rows, "rows"):
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
        return (f"{x['weapon']}|{x['affinity']}|{x['ash']}|{x['grip']}|{r1.get((x['weapon'], x['grip']))}|{stats_key}|"
                f"{MIST_DWELL_S}{A.variant_key(x)}")

    todo = [x for x in rows if x["cat"] != "utility" and key(x) not in cache]
    tab = None
    if todo:
        tab = T.Table(todo[0]["weapon"], todo[0]["affinity"], stats, DEFAULT_ARMOR, True)
        ST, st, _ = tab.status_tables()
        t = tab.t
        sids = skill_ids(t, {x["ash"] for x in todo})
        events, ws_cache, fires = {}, {}, {}
        print(f"ranged status: {len(todo)} rows to read", file=sys.stderr, flush=True)
    for x in counted(todo, "rows"):
        sid = sids.get(x["ash"])
        if sid is None or fires.get(sid) is False:
            cache[key(x)] = {}
            continue
        wid = t.find_weapon(x["weapon"])
        level = tab.art.max_level(t.reg.weapon[wid]["reinforceTypeId"])
        grip = "both" if x["grip"] == "2H" else "one"
        # The bullets a cast fires depend on the weapon only through its behavior variation and
        # the opening its class plays (`er-mechanics-ashes.resolve_judge`, `_opening`).
        ek = (sid, t.reg.weapon[wid]["behaviorVariationId"], A.weapon_diff_category(t, sid, wid, grip), level,
              x.get("stance"))
        if ek not in events:
            prof = A.skill_profile(t, sid, wid, level, grip, x.get("stance"))
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


def drop_no_pvp_utility(rows):
    """Rows of skills with no hits that do nothing to a player (`er-mechanics-ash-table.pvp_utility`),
    left out here too so a sweep made before that rule cannot bring one back."""
    T = _table_mod()
    names = {x["ash"] for x in rows if x["cat"] == "utility" and not x.get("buff")}
    if not names:
        return rows
    t = T.A.AshTables()
    sids = skill_ids(t, names)
    bad = {n for n in names if n in sids and not T.pvp_utility(n, t.arts[sids[n]]["swordArtsTypeNew"], None)}
    if bad:
        print(f"no PvP effect, dropped: {', '.join(sorted(bad))}", file=sys.stderr)
    return [x for x in rows if not (x["ash"] in bad and x["cat"] == "utility" and not x.get("buff"))]


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


def chain_net(opp, poise, distance=None):
    """`er-mechanics-ash-table.Table.chain_net` from the interrupt model: a chain's hits
    [(frame, poise dealt, HP)] and hyperarmor windows go through
    `er-mechanics-ash-interrupt.simulate` against the pool `opp` at my `poise`, with the clock
    ending at the chain's roll cancel, and the chain nets the HP of the hits it lands less what his
    replies take, the measure `er-mechanics-ash-interrupt.multiplier` uses. The two sides start
    `distance` apart (default the strike distance, `INFERRED`: the buff's window is fought up
    close, the string's opener already in range)."""
    AI = _ai()
    D = AI.EXCHANGE_DISTANCE_M if distance is None else distance
    memo = {}

    def net(hits, windows, end):
        k = json.dumps([[[round(f, 1), round(q, 2)] for f, q, _ in hits], [list(w) for w in windows], round(end, 1)])
        if k not in memo:
            memo[k] = AI.simulate(opp, [(f, q) for f, q, _ in hits], windows, D, end, poise)
        r = memo[k]
        return sum(p * h for p, (_, _, h) in zip(r["p"], hits)) - r["taken"]
    return net


def set_buff_values(rows, stats, cache_path, armor=None):
    """Value the buff of every row of a sweep made before `er-mechanics-ash-table.BUFF_MODEL`
    existed, or under an older one (`unapply_buff` takes that one out first), so no re-sweep is
    needed: the hits the row's clock holds are re-read (`Table.r1_slots`, through `buff_hits`) and
    `Table.buff_gain` adds the buff's damage on that carrier, folded in by `apply_buff`. A row
    that already carries the current model's `buff` is left as it is, and so is a step. Run before
    `add_grip_change`: the clock it divides by is the cast's.

    A heavy-special buff (War Cry) weighs its window's R1 chain against the R2 it swaps in through
    `chain_net`, at the poise of `armor` (the build's protectors; `DEFAULT_ARMOR` without them).

    Which ashes buff is read once per ash on its first carrier (`er-mechanics-ashes.skill_buffs`
    on that weapon's opening); only those ashes' rows are re-read. A utility row's follow-up is
    recomputed too, and a row more than 1 HP off the sweep's is counted and reported: it means the
    sweep was made at other stats than `stats`. Cached per row, stats and armor in `cache_path`."""
    T = _table_mod()
    armor = armor or DEFAULT_ARMOR
    stats_key = ",".join(f"{k}{stats[k]}" for k in STATS) + "|" + ",".join(armor)

    def key(x):
        return (f"{T.BUFF_MODEL}|{x['weapon']}|{x['affinity']}|{x['ash']}|{x['grip']}|{x.get('follow_up')}|"
                f"{(x.get('string') or {}).get('r1s')}|{T.string_scored(x)}|{stats_key}{T.A.variant_key(x)}")

    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    for x in rows:
        if (x.get("buff") or {}).get("model") not in (None, T.BUFF_MODEL):
            T.unapply_buff(x)
    todo = [x for x in rows if "buff" not in x]
    miss = [x for x in todo if key(x) not in cache]
    if miss:
        tab = T.Table(miss[0]["weapon"], miss[0]["affinity"], stats, armor, True)
        tab.chain_net = chain_net(_ai().Opponent(), round(tab.armor * 1000))
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
        for (w, aff), xs in counted(sorted(groups.items()), "weapons"):
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
                prof = A.skill_profile(t, sids[x["ash"]], tab.wid, tab.level, two, x.get("stance"))
                cache[key(x)] = tab.buff_gain(sids[x["ash"]], prof, two, ctx, *bh)
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
    for x in counted(rows, "rows"):
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
    for x in counted(rows, "rows"):
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
    for ash, x0 in counted(list(first.items()), "ashes"):
        try:
            sid = t.find_arts(ash)
        except (SystemExit, KeyError):
            continue
        if not T.A.lead_in(t, sid, T.A.skill_profile(t, sid, t.find_weapon(x0["weapon"]), 0, x0["grip"])):
            continue
        for x in old:
            if x["ash"] != ash:
                continue
            prof = T.A.skill_profile(t, sid, t.find_weapon(x["weapon"]), 0, x["grip"], x.get("stance"))
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
        return f"ash|{x['weapon']}|{x['affinity']}|{x['ash']}|{x['grip']}{lead}{T.A.variant_key(x)}"

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
                hits = T.S.AshScorer(tab.lev, tab.weapon, tab.affinity, sid, two, tab.defender, None,
                                     x.get("stance")).hits(stats)
                prof = T.A.skill_profile(tab.t, sid, tab.wid, tab.level, two, x.get("stance"))
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
#: Weapons that want an off-hand partner: {left weapon: right weapons} of the measured equip sets
#: (`derive_combos`, `offhand_partners`), filled in by `main` once the sets are known. Keyed by
#: weapon, not class: measured 2026-10-19, the one set Star Fist + Bloodhound Claws keyed by class
#: made every claw in the plan, Raptor Talons included, want a Star Fist beside it, a pair nothing
#: had measured.
OFFHAND_PARTNERS = {}


def offhand_partners(combos):
    """{left weapon: right weapons} of `combos` (`derive_combos`'s sets): the left weapon of a set
    wants that set's right weapon near it. A set two-handed (no left weapon) or on two copies of one
    weapon names no partner."""
    out = {}
    for _, _, right, left in combos:
        if left is not None and left != right:
            out[left] = tuple(dict.fromkeys(out.get(left, ()) + (right,)))
    return out


class Partners:
    """Which weapon makes another perform best beside it (`PARTNER_ROWS`).

    - A weapon whose powerstanced light chain (`er-mechanics-grip-modes.py`, the weapon with a
      copy of itself, both hands on one affinity) beats its one- and two-handed chains on the same
      affinity wants a powerstance partner: any weapon `IsEnableDualWielding` pairs it with
      (`er-mechanics-powerstance-guard.can_powerstance`), scored by the embedding's chain damage per
      second for the pair times the pierce counter bonus (`PIERCE_COUNTER`, `COUNTER_SHARE`) on the
      share of the pair's hits thrown by a pierce weapon (`EquipParamWeapon.atkAttribute` 2).
    - A weapon `OFFHAND_PARTNERS` names (the left weapon of a measured set) wants that set's right
      weapon, unless it is paired (`isDualBlade` and `bothHandEquipable`): two-handed, a paired
      weapon already plays its own dual moveset and needs no off-hand partner."""

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

    def paired(self, w):
        """Two-handed, `w` plays its paired moveset (`powerstance_offs.paired`)."""
        row = self.row(w)
        return bool(row.get("isDualBlade") and row.get("bothHandEquipable"))

    def pierce_factor(self, p):
        hands = [h for k, s in p["slots"].items() if k.startswith("dual_") and k[5:].isdigit()
                 for h in s.get("hands") or ()]
        weapon = {"right": p["right"], "left": p["left"]}
        share = (sum(self.pierce(weapon[h]) for h in hands if h in weapon) / len(hands)) if hands else 0.0
        return 1.0 + share * (PIERCE_COUNTER - 1.0) * COUNTER_SHARE

    def need(self, w, aff):
        """"powerstance", "offhand" or None."""
        if (w, aff) not in self._need:
            kind = "offhand" if w in OFFHAND_PARTNERS and not self.paired(w) else None
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
            return p in OFFHAND_PARTNERS[w]
        sc = self.scores(w, aff)
        return p in sc and sc[p] >= (1 - PARTNER_SLACK) * max(sc.values())


def pierce_key(x):
    return f"{x['weapon']}|{x['affinity']}|{x['ash']}|{x['grip']}{_table_mod().A.variant_key(x)}"


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
                                 tab.defender, None, x.get("stance")).hits(stats)
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
        # Largest chunk first, so the rows done, not the chunks, say how much is left.
        rows_total, rows_done = len({pierce_key(x) for x in todo}), 0
        with ctx.Pool(jobs, initializer=AI._worker_init, initargs=(workers, ctx.Value("i", 0))) as pool:
            for n, res in enumerate(pool.imap_unordered(_pierce_work, chunks), 1):
                cache.update(res)
                rows_done += len(res)
                print(f"  {n}/{len(chunks)} weapon affinities, {rows_done}/{rows_total} rows",
                      file=sys.stderr, flush=True)
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
    got = None
    if a.interrupt_weight or a.whiff_weight or pb:
        # The perfect-block term reads the same cast profiles, so it computes them when the
        # interrupt and whiff weights are both off.
        stage("interrupt")
        got = interrupt_weights(rows, a)
        if a.whiff_weight:
            stage("whiff")
            whiff_weights(rows, got, getattr(a, "interrupt_cache", None), getattr(a, "interrupt_jobs", 6))
            if report:
                whiff_report(rows)
    # Self-damage and the full-HP gear it switches off (`set_self_costs`), in the whiff stage's
    # time: they read the same profiles for the share of casts that reach the effect.
    set_self_costs(rows, getattr(a, "build", None), got if a.interrupt_weight else None, report)
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
    set_by_distance(rows)
    return got


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
    for x in counted(rows, "rows"):
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
    for x in counted(rows, "rows"):
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
                       "status_hp": {}, "note": f"{x['job']}: {note}",
                       # For `icon_saved_hp`: the share of a corpus opening the raised shield
                       # stops, without the repel-punish credit, and the parry's HP over that block.
                       "guard_base": float(p["_v"]["base"].mean()) if "_v" in p else p["guard"],
                       "net_hp": o["net_hp"] if o else 0.0})
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


#: The fight model icon-shield rows are valued over (user, 2026-10-18: "weighted by value based
#: on the potential length of invasions being an hour"): `er-mechanics-buffs.FIGHT_KINDS`.
ICON_FIGHT = "invasion"


def _buffs():
    key = "er_mechanics_buffs"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, HERE / "er-mechanics-buffs.py")
        m = importlib.util.module_from_spec(spec)
        sys.modules[key] = m
        spec.loader.exec_module(m)
    return sys.modules[key]


def icon_saved_hp(c):
    """HP one Shields-row copy saves per corpus opening against no shield: the share of the
    opening its raised guard stops (`guard_base`, `ShieldModel.guard` without the repel-punish
    credit) times the opening's mean HP on me (`opening_hp`), plus a parry copy's `net_hp`, which
    is measured over a block of the same opening (`ShieldModel.ash_options`)."""
    return c["guard_base"] * c["opening_hp"] + max(0.0, c.get("net_hp", 0.0))


def fight_hold_hp(kind=ICON_FIGHT):
    """`defense(hp_saved)` and `offense(dps)`: HP per fight of holding one copy, the mean over
    `er-mechanics-buffs.fight_kind_points(kind)` (invasion: 10 s to 1 h, log-spaced).

        defense  attackers x f / ENGAGEMENT_SECONDS x (1 - FIGHT_WIN_SHARE) x hp_saved
        offense  min(dps x f x FIGHT_WIN_SHARE, targets x (hp + CORPUS_CRIMSON x FLASK_HEAL_HP x FLASK_ETA))

    Every constant is `er-mechanics-buffs`'s, with its own label there: each attacker gets an
    engagement every `ENGAGEMENT_SECONDS` and lands its opening in the share of them I lose, and
    my damage is only worth what the hosts' HP bars and their flasks hold (`hp`: `fight_hits`'
    corpus median max HP). So offense stops growing once the hosts are dead and defense does not,
    which is what an hour-long invasion does to a shield.

    `INFERRED`: a copy is valued as if it were the one held for the whole fight. Nothing measures
    what share of a fight a given inventory copy spends in hand; the share is the same unknown on
    both sides of a comparison, so it cancels and is not a constant here."""
    B = _buffs()
    k = B.FIGHT_KINDS[kind]
    fs = tuple(B.fight_kind_points(kind))
    hp = inspect.signature(B.fight_hits).parameters["hp"].default
    kill = k["targets"] * (hp + B.CORPUS_CRIMSON * B.FLASK_HEAL_HP * B.FLASK_ETA)
    win = B.FIGHT_WIN_SHARE

    def defense(saved):
        return statistics.fmean(k["attackers"] * f / B.ENGAGEMENT_SECONDS * (1.0 - win) * saved for f in fs)

    def offense(dps):
        return statistics.fmean(min(dps * f * win, kill) for f in fs)
    return SimpleNamespace(points=fs, kill=kill, attackers=k["attackers"], defense=defense, offense=offense)


def icon_shield_plan(slots, pool, cap, protected, hold):
    """[{k, copy, icon_hp, rows, row, slot, row_hp, placed}]: up to `cap` icon-shield rows, one per
    even share of the plan's whole rows. Share k offers `pool[k % len(pool)]` (`icon_hp`, its
    `hold.defense`) against the share's cheapest displaceable row: no slot `protected`, and no
    weapon whose last copies it holds. A row is worth its best copy's `hold.offense`, since one copy
    is held at a time; `slot` is that copy. `placed` when the shield is worth more, and then the
    row's weapons no longer count toward later shares' last-copy rule. `row` None: the share has
    no displaceable row."""
    nrows = len(slots) // ROW
    cap = min(cap, nrows)
    count = collections.Counter(s["weapon"] for s in slots)
    out = []
    for k in range(cap):
        c = pool[k % len(pool)]
        d = {"k": k, "copy": c, "icon_hp": hold.defense(icon_saved_hp(c)),
             "rows": (k * nrows // cap, (k + 1) * nrows // cap), "row": None, "placed": False}
        cands = []
        for r in range(*d["rows"]):
            idx = range(r * ROW, (r + 1) * ROW)
            here = collections.Counter(slots[i]["weapon"] for i in idx)
            if any(protected(i) for i in idx) or any(count[w] <= n for w, n in here.items()):
                continue
            best = max(idx, key=lambda i: slots[i]["value"])
            cands.append((hold.offense(slots[best]["value"]), r, best, here))
        if cands:
            row_hp, r, best, here = min(cands, key=lambda e: e[:2])
            d.update(row=r, slot=best, row_hp=row_hp, placed=d["icon_hp"] > row_hp)
            if d["placed"]:
                count -= here
        out.append(d)
    return out


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
    `pierce_w` (`set_pierce_weights`): the pierce counter-hit bonus on the cast's pierce share.

    `wmult` is the reply's `net`, below 0 when his punish outweighs the cast, and `combined`
    floors only the mix. When the reply comes from the escape geometry (`whiff['geometry']`), it
    already measured which of this carrier's hits land on a defender who rolls or strafes from
    the ash's own engagement distance, so `area_w` and `reach_w` (`reactive_proxy`), the landing
    proxies `raw_value` applies, are taken back off that half: they scale the interrupt half
    only, and a hit the geometry says misses is not credited again by a wide sweep or a long reach.

    Less `self_cost['share']` of `raw_value` (`set_self_costs`): the HP a self-damaging cast takes
    from its user and the full-HP gear it switches off, over the HP the row delivers. It is paid
    whatever the opponent does, so no reply weight scales it; the result is floored at 0.

    Times `stance_w` (`merge_stance_buttons`) on a stance skill whose buttons were joined: what
    pressing the better button per duel distance band adds over this row's button alone."""
    m = x.get("imult", 1.0)
    if "wmult" in x:
        m = _whiff().combined(m, x["wmult"] / reactive_proxy(x))
    v = value_base(x) * m
    share = (x.get("self_cost") or {}).get("share", 0.0)
    v = max(0.0, v - raw_value(x) / spread_proxy(x) * share) if share else v
    return v * x.get("stance_w", 1.0)


def value_base(x):
    """`value` before the reply multiplier: `raw_value` (less `spread_proxy`) times the parry,
    perfect-block, multihit, buff and pierce weights."""
    return (raw_value(x) / spread_proxy(x) * x.get("pmult", 1.0) * x.get("bmult", 1.0) * x.get("mh_w", 1.0)
            * x.get("buff_w", 1.0) * x.get("pierce_w", 1.0))


def set_by_distance(rows):
    """`by_distance` on every row valued over the duel's distance spread (`imult_by`, set by
    `interrupt_weights`): [[node, credited time, m, value], ...] per band of the wave the cast
    reaches, `m` the reply multiplier of the cast thrown at that band alone (`combined` of that
    band's interrupt multiplier and whiff `net`, as `value` mixes the whole-duel ones, floored at 0
    per band since the player presses it only where it is worth it) and `value` = `value_base` x m.
    A set holding two ashes is then valued per band on the better of them (`band_set_value`,
    `SetScorer.pair`). A row whose perfect-block term was measured per band (`bmult_by`,
    `er-mechanics-perfect-block.AshSide.row`) takes that band's `bmult` in place of the whole
    duel's."""
    W = _whiff()
    for x in rows:
        if not x.get("imult_by"):
            continue
        net_by = {p[3]: p[2] for p in ((x.get("whiff") or {}).get("spread") or ()) if len(p) > 3}
        bm_by = dict(map(tuple, x.get("bmult_by") or ()))
        base, proxy = value_base(x), reactive_proxy(x)
        bm = x.get("bmult", 1.0)
        out = []
        for node, c, im in x["imult_by"]:
            m = W.combined(im, net_by.get(node, 1.0) / proxy) if "wmult" in x else im
            b = base / bm * bm_by[node] if node in bm_by and bm > 0 else base
            out.append([node, c, round(m, 4), round(b * m, 2)])
        x["by_distance"] = out


def merge_stance_buttons(rows):
    """`rows` with each stance skill's button rows joined into one (user, 2026-10-19: "Is it
    unsheathe R1, R2 or both?"). The sweep keeps one row per button the stance hands over on
    (`er-mechanics-ash-table`, `variant`, `stance`: Unsheathe's R1 slash 040060 and R2 slash
    040070), and the player picks the button after the L2 press, so the ash is worth the better
    button wherever the cast is thrown, not one button's whole-duel value.

    The distance band is the cast-time situation the model tells apart (`set_by_distance`); the
    interrupt and whiff replies are the opponent's answer after the press and are already mixed
    per band. So with bands, the kept row's `by_distance` holds every button's bands and
    `band_set_value` takes the better per band, a nested credited time included (a longer-reaching
    button takes only the time beyond the shorter one's reach where it is worse). The kept row is
    the button whose bands are worth most alone, `stance_w` is the joined bands' worth over its
    own, which `value` multiplies in, and `stance_by_band` records [node, button pressed there].
    Without bands (no `--arena-m`, one fixed distance), there is one situation, so the kept row is
    the better button by `value` and nothing is added. `stance_buttons` lists the joined buttons."""
    AI = _ai()
    groups = collections.defaultdict(list)
    for x in rows:
        if x.get("stance"):
            groups[(x["weapon"], x["affinity"], x["ash"], x["grip"])].append(x)
    drop = set()
    for g in groups.values():
        if len(g) < 2:
            continue
        banded = all(x.get("by_distance") for x in g)
        best = (max(g, key=lambda x: (AI.band_set_value([x["by_distance"]]), value(x))) if banded
                else max(g, key=value))
        drop.update(id(x) for x in g if x is not best)
        best["stance_buttons"] = sorted(x["stance"] for x in g)
        if not banded:
            continue
        own = AI.band_set_value([best["by_distance"]])
        both = AI.band_set_value([x["by_distance"] for x in g])
        pick = {}
        for x in g:
            for b in x["by_distance"]:
                if b[-1] > pick.get(b[0], (-1.0, None))[0]:
                    pick[b[0]] = (b[-1], x["stance"])
        best["stance_by_band"] = [[n, pick[n][1]] for n in sorted(pick)]
        best["by_distance"] = sorted((b for x in g for b in x["by_distance"]), key=lambda b: (b[0], -b[-1]))
        best["stance_w"] = round(both / own, 4) if own > 0 else 1.0
    return [x for x in rows if id(x) not in drop]


#: {(weapon, affinity, grip): `by_distance` of that carrier's best-valued damaging row}, filled by
#: `main` after the rows are weighed (`ash_bands`), read by `SetScorer.pair`. Empty outside a duel.
ASH_BANDS: dict = {}

#: Which hand L2 casts, from c0000.hks (1.17.1) `GetSwordArtInfo` (debug lines 569-598).
#: Two-handed (`c_Style` >= `HAND_LEFT_BOTH`), the held weapon's skill: `env(326, hand)` with hand
#: `HAND_RIGHT` for `HAND_RIGHT_BOTH`, `HAND_LEFT` for `HAND_LEFT_BOTH`. One-handed, the left
#: weapon's skill when `IsShieldArts` or `IsArrowStanceArts` holds for it, else the right
#: weapon's -- powerstance included, the function has no pair case. `IsEnableSwordArts` (601-624)
#: then refuses a left-hand one-handed skill unless `IsShieldArts` holds, so a bow skill on the
#: left blocks L2 one-handed. The ids are `SwordArtsParam.swordArtsTypeNew` (`IsShieldArts` lines
#: 664-668, `IsArrowStanceArts` 682-685); the 29 rows the shield list names are exactly the ones
#: with `isGrayoutLeftHand` 0, the menu's mirror of the same rule.
HKS_SHIELD_ARTS = frozenset(range(90, 100)) | frozenset(
    {17, 71, 151, 152, 195, 196, 197, 201, 202, 207, 211, 322, 324, 334, 348, 352, 354, 355, 359, 399})
HKS_ARROW_STANCE_ARTS = frozenset({100, 101, 102, 103, 104, 105, 106, 107, 108, 169, 337, 357, 371})
#: {(weapon, affinity, grip): "shield" | "arrow"} for an `ASH_BANDS` carrier whose best row's skill
#: is one L2 takes from the left hand one-handed (`HKS_SHIELD_ARTS`) or that blocks L2 there
#: (`HKS_ARROW_STANCE_ARTS`), filled with `ASH_BANDS`.
ASH_BAND_LEFT_L2: dict = {}


def _ash_band_rows(rows) -> dict:
    best = {}
    for x in rows:
        if x["cat"] != "utility" and x.get("by_distance"):
            k = (x["weapon"], x["affinity"], x["grip"])
            if k not in best or value(x) > value(best[k]):
                best[k] = x
    return best


def ash_bands(rows) -> dict:
    """{(weapon, affinity, grip): by_distance} of each carrier's best row by `value`."""
    return {k: x["by_distance"] for k, x in _ash_band_rows(rows).items()}


def left_l2_kind(type_new):
    """"shield" when L2 one-handed casts this skill from the left hand, "arrow" when it there
    blocks L2, else None (`HKS_SHIELD_ARTS`, `HKS_ARROW_STANCE_ARTS`)."""
    if type_new in HKS_SHIELD_ARTS:
        return "shield"
    if type_new in HKS_ARROW_STANCE_ARTS:
        return "arrow"
    return None


def ash_band_left_l2(rows, type_new_of) -> dict:
    """`ASH_BAND_LEFT_L2` for `rows`, `type_new_of(ash name)` giving its `swordArtsTypeNew`."""
    out = {}
    for k, x in _ash_band_rows(rows).items():
        kind = left_l2_kind(type_new_of(x["ash"]))
        if kind:
            out[k] = kind
    return out


def reactive_proxy(x):
    """`area_w` x `reach_w` when the whiff reply measured landing from geometry, else 1 (`value`).
    A row valued over the duel's distances has `reach_w` off both halves already (`spread_proxy`),
    so only `area_w` is taken off here."""
    if not (x.get("whiff") or {}).get("geometry"):
        return 1.0
    p = x.get("area_w", 1.0) * (1.0 if x.get("spread") else x.get("reach_w", 1.0))
    return p if p > 0 else 1.0


def spread_proxy(x):
    """`reach_w` x `land_w` on a row valued over the duel's distance spread (`x['spread']`, set by
    `interrupt_weights`), else 1. Both are proxies for what range is worth: `reach_w` credits reach
    beyond the carrier's R1 and the ash's longest carrier, `land_w` the share landed at
    1.5-3.0 m. The spread measures it instead, from the hits that touch him at each distance the
    duel holds him at (`er-mechanics-ash-interrupt.Rows.spread_profile`) on both the interrupt and
    the whiff half, so `value` and `interrupt_weights` take them back off rather than count range
    twice. `area_w` (sweep width) stays on the interrupt half: the standing touch test there is
    straight ahead and does not measure width."""
    if not x.get("spread"):
        return 1.0
    p = x.get("reach_w", 1.0) * x.get("land_w", 1.0)
    return p if p > 0 else 1.0


#: Interrupt profiles, keyed by sweep row (`er-mechanics-ash-interrupt.profiles`).
INTERRUPT_CACHE = Path.home() / ".cache/er-build-planner/ash-interrupt.json"
#: The run's stages in order. `Stages` prints them for `er-mechanics-pipeline-watch.py`, which
#: draws its bar and its estimate from them, weighing each by `STAGE_TIMES` from the last run.
STAGES = ("rows", "interrupt", "whiff", "area", "landing", "reach", "ranged status", "multihit", "pierce",
          "parry", "perfect block", "own status", "rank", "layout", "crit", "shields", "ranged", "fun and sets",
          "partners", "set coverage", "redundant status", "write")
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


#: (whiff module, opponents, interrupt profiles) for `_whiff_one`, set before the workers fork.
_WHIFF_JOB = None


def _whiff_one(job):
    """One row's whiff simulation, `job` = (its interrupt profile key, its grip change)."""
    W, opp, got = _WHIFF_JOB
    rk, grip_change = job
    return W.from_profile(opp, got.get(rk), grip_change)


def whiff_weights(rows, got, cache_path=None, jobs=1):
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
    # The uncached rows are independent simulations, so they run on `jobs` forked workers.
    need = {}
    for x in todo:
        key = _whiff_key(AI, x)
        if key not in memo and key not in need:
            need[key] = (AI.row_key(x), x.get("grip_change", 0.0))
    if need:
        global _WHIFF_JOB
        _WHIFF_JOB = (W, opp, got)
        # Rows that share their plans (`er-mechanics-ash-whiff._plans`: one weapon's affinities)
        # are one job, which takes their duel distances one at a time across the rows
        # (`from_spreads`), so each plan is computed once (`whiff_groups`). The results go into the
        # memo in the order before, so the cache file is the same too.
        groups = whiff_groups(list(need), need, got)
        # Each worker pins itself to a core of its own (`_worker_init`). A plain pool inherits the
        # caller's affinity, and a throttled caller is pinned to one CPU: on 2026-10-20 ten whiff
        # workers shared CPU 1 at 10% each for hours.
        cpus = AI.allowed_cpus()
        workers = cpus[1:] or cpus
        n = max(1, min(jobs, len(groups), len(workers)))
        done = {}
        if n > 1:
            import multiprocessing as mp
            ctx = mp.get_context("fork")
            with ctx.Pool(n, initializer=AI._worker_init, initargs=(workers, ctx.Value("i", 0))) as pool:
                out = pool.imap(_whiff_group, [[need[k] for k in g] for g in groups], chunksize=8)
                tick = Count(len(need), f"rows simulated on {n} workers")
                for g, ws in zip(groups, out):
                    done.update(zip(g, ws))
                    tick(len(g))
        else:
            tick = Count(len(need), "rows simulated")
            for g in groups:
                done.update(zip(g, _whiff_group([need[k] for k in g])))
                tick(len(g))
        for k in need:
            memo[k] = done[k]
        _WHIFF_JOB = None
    for x in counted(todo, "rows"):
        key = _whiff_key(AI, x)
        w = memo[key]
        # A step or buff, or a row with no profile, has no whiff to punish and keeps `imult` alone.
        if w:
            x["wmult"] = W.net(w)
            x["whiff"] = {k: round(v, 3) if isinstance(v, float) else v for k, v in w.items()}
    # A cast with no hit has nothing to roll: the waiting reply punishes it outright
    # (`er-mechanics-self-damage.cast_punish`). Not memoised: the punish is charged against
    # `delivered_hp`, which the buff stage sets after the sweep, and a few hundred rows take seconds.
    casts, geo = cast_ashes(rows), {}
    cast_rows = [x for x in rows if x["cat"] == "utility" and x["ash"] in casts]
    print(f"whiff: {len(cast_rows)} no-hit casts punished outright", file=sys.stderr, flush=True)
    for x in cast_rows:
        prof = got.get(AI.row_key(x))
        if not prof or "error" in prof or prof.get("hits") or delivered_hp(x) <= 0:
            continue
        w = cast_whiff(x, prof, opp, geo)
        x["wmult"] = W.net(w)
        x["whiff"] = {k: round(v, 3) if isinstance(v, float) else v for k, v in w.items()}
    if path:
        path.write_text(json.dumps({"settings": settings, "rows": memo}))


def whiff_plan_key(prof, grip_change):
    """What `er-mechanics-ash-whiff._plans` keys a row's plans on, as far as the profile says: its
    escape tables (one per distance of a duel spread), windows, gate and poise, and the grip
    change it is led by. Only `whiff_groups` reads it, to put rows that share their plans together;
    a key that groups too little or too much changes the speed, never a result."""
    if not prof or "error" in prof:
        return None
    esc = tuple(q.get("escape") for q in prof["spread"]) if prof.get("spread") else (prof.get("escape"),)
    return esc, json.dumps(prof.get("windows")), prof.get("gate"), prof.get("poise"), grip_change


def whiff_groups(keys, need, got):
    """`keys` as lists of the rows of one `whiff_plan_key`, in the order of each list's first
    row. The rows of one weapon's affinities share their plans and sat a whole ash list apart, so
    a worker's plan memo held none of them by the next: measured on the 2026-10-19 heavy-kit
    cache (fixed distance), 6747 of 6752 plans computed row by row, 4873 grouped."""
    groups, alone = {}, 0
    for k in keys:
        rk, grip_change = need[k]
        pk = whiff_plan_key(got.get(rk), grip_change)
        if pk is None:
            pk, alone = ("alone", alone), alone + 1
        groups.setdefault(pk, []).append(k)
    return list(groups.values())


def _whiff_group(jobs):
    """The whiff simulations of rows that share one grip change, `jobs` [(profile key, grip
    change)], each what `_whiff_one` returns for it (`er-mechanics-ash-whiff.from_spreads`)."""
    W, opp, got = _WHIFF_JOB
    return W.from_spreads(opp, [got.get(rk) for rk, _ in jobs], jobs[0][1])


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
              f"p_dodge {w['p_dodge']:.2f}, strafe {w.get('strafe', 0.0):.2f}, skill {w.get('skill', 0.0):.2f} "
              f"({w.get('skill_top') or '-'}), punish {w['punish_hp']:.0f} HP, "
              f"backstab {w.get('backstab', 0.0):.2f}, trade {w['trade']:.2f}, "
              f"wmult {x['wmult']:.2f}", file=sys.stderr)


_SELF = {}


def _self_mod():
    key = "er_mechanics_self_damage"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, HERE / "er-mechanics-self-damage.py")
        m = importlib.util.module_from_spec(spec)
        sys.modules[key] = m
        spec.loader.exec_module(m)
    return sys.modules[key]


def _self_tables():
    """(ash-table module, `AshTables`), read once for the self-cost and cast terms."""
    if "t" not in _SELF:
        T = _table_mod()
        _SELF["t"] = (T, T.A.AshTables())
    return _SELF["t"]


def cast_ashes(rows) -> set:
    """Names of the no-hit skills among `rows` that are casts the opponent can punish
    (`er-mechanics-self-damage.cast_punish`): every utility row's skill but a step
    (`ROLLING_ARTS`, invincible and moving away) and a `NO_HIT_KEEP` skill (valued by its own
    model)."""
    names = {x["ash"] for x in rows if x["cat"] == "utility" and x.get("ash")}
    if not names:
        return set()
    T, t = _self_tables()
    sids = skill_ids(t, names)
    return {n for n, s in sids.items()
            if t.arts[s]["swordArtsTypeNew"] not in T.ROLLING_ARTS and n not in T.NO_HIT_KEEP}


def delivered_hp(x) -> float:
    """HP the row's own clock delivers: the cast's (a utility row's: the follow-up's) damage, or
    the string's when the row is scored on it, plus what its buff adds (`apply_buff`)."""
    T = _table_mod()
    st = x.get("string") or {}
    base = st.get("dmg") if T.string_scored(x) and st.get("dmg") else x.get("dmg") or 0.0
    return float(base) + float((x.get("buff") or {}).get("added") or 0.0)


def build_armor(build_path):
    """The protectors the `--build` planner JSON wears (the interrupt model's armor), or None."""
    if not build_path:
        return None
    b = json.loads(Path(build_path).read_text())
    return [s["name"] for p in ("arms", "body", "legs", "head")
            for s in (b.get("protectors") or {}).get(p, {}).get("slots", []) if s.get("equipSet")] or None


def build_wear(build_path):
    """(max HP, worn talismans) of the `--build` planner JSON; the interrupt model's fallback HP
    and no talismans without one."""
    max_hp, worn = 2292.5, []
    if build_path:
        b = json.loads(Path(build_path).read_text())
        max_hp = (b.get("computed") or {}).get("maxHealth") or max_hp
        worn = [s["name"] for s in (b.get("talismans") or {}).get("slots", []) if s.get("equipSet")]
    return max_hp, worn


def set_self_costs(rows, build_path=None, got=None, report=False, wear=None):
    """`self_cost` on every row of a skill that hurts its user (`er-mechanics-self-damage`): `hp`
    the HP its own SpEffects take (`self_damage`, at the build's max HP), `gear_hp` what the worn
    full- or high-HP talisman rows stop saving and adding once it has (`gated_loss`), `spent` the
    talismans it leaves worth nothing, and `share` = (`hp` + `gear_hp`) x `land` / the HP the row
    delivers (`delivered_hp`), which `value` takes off. `land` is the share of casts that reach the
    effect: the interrupt profile's `fx` in the attack-into reply, every cast in the waiting one
    (`er-mechanics-ash-whiff.DODGE_SHARE`); 1 without profiles. A skill whose SpEffects take
    nothing gets no `self_cost`, so its value is unchanged. `wear` (max HP, worn talismans)
    stands in for `build_path`."""
    SD = _self_mod()
    T, t = _self_tables()
    names = {x["ash"] for x in rows if x.get("ash")}
    sids = skill_ids(t, names)
    hurt = {n for n, s in sids.items() if SD.skill_hurts_user(t, s)}
    if not hurt:
        return
    max_hp, worn = wear or build_wear(build_path)
    gated = SD.hp_gated_rows(_sib_talismans().Talismans(), worn) if worn else []
    hit, win = _ai().B_HIT_HP, _buffs().FIGHT_WIN_SHARE
    share_w = _whiff().DODGE_SHARE if got else None
    AI = _ai() if got else None
    A, memo, best = T.A, {}, {}
    for x in rows:
        if x.get("ash") not in hurt:
            continue
        k = (x["ash"], x["weapon"], x["grip"], x.get("stance"))
        if k not in memo:
            prof = A.skill_profile(t, sids[x["ash"]], t.find_weapon(x["weapon"]), 0, x["grip"], x.get("stance"))
            memo[k] = SD.self_damage(t.speffect, prof["anims"].get(A.main_anim(prof), []), max_hp)
        sd = memo[k]
        if not sd["hp"]:
            continue
        loss = SD.gated_loss(sd["hp"], max_hp, gated, hit, win)
        land = 1.0
        if got:
            r = got.get(AI.row_key(x)) or {}
            fx = (r.get("fx") or [1.0])[0] if not r.get("error") else 1.0
            land = (1.0 - share_w) * fx + share_w
        full = delivered_hp(x)
        cost = (sd["hp"] + loss["hp"]) * land
        x["self_cost"] = {"hp": sd["hp"], "gear_hp": loss["hp"], "rows": [r["id"] for r in sd["rows"]],
                          "gear": [r for r in loss["rows"] if r["lost"] > 0], "spent": SD.spent_talismans(loss),
                          "max_hp": max_hp, "land": round(land, 3), "full": round(full, 1),
                          "share": round(cost / full, 4) if full > 0 else 1.0}
        if x["ash"] not in best or full > best[x["ash"]]["self_cost"]["full"]:
            best[x["ash"]] = x
    if report:
        for n, x in sorted(best.items()):
            c = x["self_cost"]
            print(f"self cost: {n} ({x['affinity']} {x['weapon']} {x['grip']}): takes {c['hp']:.0f} of {max_hp:g} HP, "
                  f"switches off {c['gear_hp']:.0f} HP of {', '.join(g['name'] for g in c['gear']) or 'no gear'}, "
                  f"x land {c['land']:.2f} against {c['full']:.0f} HP delivered: share {c['share']:.2f}", file=sys.stderr)


def _sib_talismans():
    key = "er_mechanics_talismans"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, HERE / "er-mechanics-talismans.py")
        m = importlib.util.module_from_spec(spec)
        sys.modules[key] = m
        spec.loader.exec_module(m)
    return sys.modules[key]


def cast_whiff(x, prof, opp, cache):
    """`er-mechanics-self-damage.cast_punish` of one cast row from its interrupt profile `prof`:
    the chase to my back is `circle` on the skill's own clip (`chase_frames`), my roll gate the
    opening's roll cancel (`er-mechanics-disengage.clip`) on the cast clock. `cache` keeps the
    geometry per skill, opening (`weapon_diff_category`: the clip a weapon class plays), grip and
    distance, and whether the skill blocks a backstab per skill, so the carriers of one opening
    share one `circle`.

    A profile valued over the duel's distances (`prof['spread']`) is punished at each of them,
    his run in from there, and the results are weighted by the distance's share of the duel."""
    SD, W, AI = _self_mod(), _whiff(), _ai()
    T, t = _self_tables()
    A = T.A
    sid, wid = t.find_arts(x["ash"]), t.find_weapon(x["weapon"])
    k = (sid, A.weapon_diff_category(t, sid, wid, "both" if x["grip"] == "2H" else "one"), x["grip"], x.get("stance"))
    bs = AI._backstab()
    if k not in cache:
        p = A.skill_profile(t, sid, wid, 0, x["grip"], x.get("stance"))
        main = A.main_anim(p)
        cat = A.SKILL_TAE_BASE + t.arts[sid]["swordArtsTypeNew"]
        clip = T._disengage().clip(cat, int(main)) if main is not None else None
        roll = ((clip or {}).get("gates") or {}).get("roll")
        roll_gate = roll + A.lead_in(t, sid, p) if roll is not None else prof["gate"]
        if "limits" not in _SELF:
            _SELF["limits"] = bs.throw_limits()
        circle = bs.circle(t, wid, sid, max(roll_gate, prof["gate"]) + 30.0, _SELF["limits"])
        if ("blocks", sid) not in cache:
            cache["blocks", sid] = bool(bs.skill_blocks_backstab(t, sid))
        cache[k] = (circle, roll_gate, cache["blocks", sid])
    circle, roll_gate, blocks = cache[k]
    fx = (prof.get("fx_frames") or [None])[0]
    spread = [(q["D"], q["w"]) for q in prof.get("spread") or ()] or [(prof["D"], 1.0)]
    out = None
    for D, w in spread:
        chase = SD.chase_frames(bs, t, wid, sid, D, opp.k, max(roll_gate, prof["gate"]) + 30.0, _SELF["limits"],
                                circle=circle)
        # The buff is lost only to a reply that stops the cast before `fx`, the frame its TAE
        # applies the buff SpEffect (`cast_punish`); what it adds is `apply_buff`'s `added`.
        r = SD.cast_punish(opp, prof["windows"], prof["gate"], D, delivered_hp(x),
                           x.get("grip_change", 0.0), chase, roll_gate, blocks, W.backstab_hp(), fx,
                           buff_hp=float((x.get("buff") or {}).get("added") or 0.0), poise=prof.get("poise"))
        if len(spread) == 1:
            return r
        fixed = ("T", "lead", "gate", "full")
        if out is None:
            out = {key: (0.0 if isinstance(v, float) and key not in fixed else v) for key, v in r.items()}
        for key, v in r.items():
            if isinstance(v, float) and key not in fixed:
                out[key] += w * v
    return out


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
    # A duel (`--arena-m`, its starting separation) values each cast over the duel's distance
    # spread rather than at the ash's own ideal spacing (`er-mechanics-ash-interrupt.duel_spread`).
    got = AI.profiles([{**x, **x["cast"]} if "cast" in x else x for x in rows], a.interrupt_cache, stats, armor, max_hp, a.interrupt_poise, None, a.interrupt_jobs,
                      log=lambda m: print(m, file=sys.stderr, flush=True), throttled=a.throttle,
                      arena_m=getattr(a, "arena_m", None))
    errors = 0
    casts = cast_ashes(rows) if a.interrupt_weight else set()
    for x in rows:
        r = got.get(AI.row_key(x))
        if not r or "error" in r:
            errors += 1
            r = None
        if r and r.get("spread") and r["hits"]:
            # Valued over the duel's distances: which hits reach at each is measured (`spread_proxy`).
            # Per band the interrupt multiplier of the cast thrown there alone, for `set_by_distance`:
            # [node, credited time, multiplier at that distance].
            x["spread"] = True
            base = raw_value(x) / spread_proxy(x)
            x["imult_by"] = [[q["node"], q["w"], round(AI.multiplier({"hits": r["hits"], "sim": q["sim"]}, base), 4)]
                             for q in r["spread"] if q["reach"]]
        if a.interrupt_weight:
            x["imult"] = AI.multiplier(r, raw_value(x) / spread_proxy(x))
            # A cast with no hit (`cast_ashes`) is worth the share of casts that reach the effect,
            # less his hits through it (`taken`) over what the row delivers, as a damaging cast is.
            full = delivered_hp(x) if r and not r["hits"] and x["ash"] in casts else 0.0
            if full > 0:
                x["imult"] = max(0.0, x["imult"] - r["sim"]["taken"] / full)
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


def category_refs(rows):
    """{category: the value `norm` divides by} over `rows` (each with "cat", "ash" and "value").

    Each ash counts once, at its best copy, and only above 0: the interrupt weight floors many
    ashes at 0, and on the STR 80 build only 4 bullet ashes were left above it, so the 10th best
    was 0, the scale fell back to 1 and Flaming Strike's `norm` was 382 -- no `CLASS_ASH_PENALTY`
    could hold it to `CLASS_ASH_CAP`, and it went on 17 class rows.

    The scale is the category's `NORM_RANK`th best ash, not its best: a single status outlier
    (Blood Spinning Slash on a Great Katana) would otherwise squash every other copy of its
    category. But no higher above it than the pooled ashes' best sits above the pooled
    `NORM_RANK`th best, `spread`: every value is the same unit (damage plus status per second,
    `raw_value`), so a category may not give its best ash more `norm` than the whole pool would.
    Without that bound the cap held unequally (heavy-kit RL 125, 2026-10-19): the utility
    category's 10th best ash was worth 28 and War Cry's best copy 226, so its `norm` reached 8.2
    against 2.1 for the best damaging ash, and `CLASS_ASH_PENALTY` (1 per carrier past the cap) let it onto
    8 class rows."""
    per = {c: {} for c in CATEGORIES}
    for x in rows:
        d = per.setdefault(x["cat"], {})
        d[x["ash"]] = max(d.get(x["ash"], 0.0), x["value"])

    def kth(vals):
        return vals[min(NORM_RANK, len(vals)) - 1]
    best = {c: sorted((v for v in d.values() if v > 0), reverse=True) for c, d in per.items()}
    pooled = sorted((v for vals in best.values() for v in vals), reverse=True)
    spread = pooled[0] / kth(pooled) if pooled else 1.0
    return {c: max(kth(vals), vals[0] / spread) if vals else 1.0 for c, vals in best.items()}


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


def out_of_arena(x, arena_m) -> bool:
    """Whether a camouflage copy (`er-mechanics-ash-table.CAMOUFLAGE_SKILLS`) hides its wearer only
    farther away than the fight's players ever get (`arena_m`): Assassin's Gambit hides past 17 m,
    and a duel arena is 16.9 m (user, 2026-10-18), so it does nothing in a duel."""
    if arena_m is None:
        return False
    T = _table_mod()
    h = x.get("hidden_beyond_m")
    if h is None and x["ash"] in T.CAMOUFLAGE_SKILLS:      # a sweep cached before the field existed
        h = T.camouflage_m(T.CAMOUFLAGE_SKILLS[x["ash"]])
    return h is not None and h > arena_m


def excluded(x) -> bool:
    """Whether `--exclude` (`EXCLUDE`) rules out this copy: its weapon, its skill, the skill on
    this affinity, or the weapon in this grip (and affinity, when the rule names one)."""
    aff = x.get("affinity", "Standard")
    return (x["weapon"] in EXCLUDE["weapons"] or x.get("ash") in EXCLUDE["ashes"]
            or (x.get("ash"), aff) in EXCLUDE["ash_affinity"]
            or grip_excluded(x["weapon"], aff, x.get("grip")))


def grip_excluded(weapon, affinity, grip) -> bool:
    """Whether `--exclude`'s "rows" ban `weapon` held in `grip` ("1H"/"2H") on `affinity`."""
    return any(weapon == w and grip == g and a in (None, affinity) for w, a, g in EXCLUDE["rows"])


def ladder_exclude():
    """The duel ladder's `--exclude` (`er-weapon-build.loadout_exclude` over `er-builds-ladder.py`,
    no talismans), the one `er-weapon-build.py --fight duel` writes."""
    spec = importlib.util.spec_from_file_location("er_weapon_build", HERE / "er-weapon-build.py")
    wb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wb)
    return wb.loadout_exclude(wb.ladder_bans(), [])


def set_exclude(got):
    """Fill `EXCLUDE` from an `--exclude` document."""
    EXCLUDE.update(weapons=set(got.get("weapons", ())), ashes=set(got.get("ashes", ())),
                   ash_affinity={tuple(p) for p in got.get("ash_affinity", ())},
                   rows=[tuple(r) for r in got.get("rows", ())])


def off_stat(x):
    """True when the copy's bullet takes under `OFF_STAT_SHARE` of its damage from the build's
    stats: it scales with a stat the build does not have (Glintstone Pebble at INT 10 is 8 of 266),
    so on this build it is a weak fixed-damage cast. Ashes in `OFF_STAT_KEEP` are kept regardless.

    A bullet with no stat term at all (every stat 0: the weapon's affinity corrects that element
    by no stat, `er-mechanics-ash-table.Table.sources` with `terms` None) scales with nothing a
    build could add, so it is not off-stat and is kept: Lightning Slash, Beast's Roar and
    Thunderbolt on a Heavy weapon were dropped while scoring 413-452 on Heavy Wakizashi
    (heavy-kit-f4bae4c4, 2026-10-19). `fits_build` keeps a non-scaling copy for the same reason."""
    b = (x.get("contrib") or {}).get("bullet")
    if not b or x["ash"] in OFF_STAT_KEEP:
        return False
    total = sum(b.values())
    stat = sum(b[k] for k in ("str", "dex", "int", "fth", "arc"))
    return bool(total) and stat > 0 and stat / total < OFF_STAT_SHARE


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
    for b, bk in counted(list(by.items()), "ashes"):
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
    for b, bk in counted(list(by.items()), "ashes"):
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


def grab_copies(best, by_weapon, mobility, bloodhound=(), usable=lambda x: True):
    """The copies that get whole pages: Waves of Darkness on its hyperarmor carrier, Bloodhound's
    Step on `bloodhound`'s first weapon (the best whole-arsenal moveset), each other
    `MOBILITY_ASHES` ash on the weapon public builds most often put it on, then every ash whose
    hyperarmor starts by `GRAB_HA_START` and that lands by `GRAB_FIRST_HIT`, earliest hyperarmor
    first, each on its best-scoring carrier. Only copies `usable` passes (`one_handed`) are
    considered."""
    best = {k: x for k, x in best.items() if usable(x)}
    by_weapon = collections.defaultdict(list, {w: [x for x in xs if usable(x)] for w, xs in by_weapon.items()})
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

    un = {"weapon": "Uchigatana", "affinity": "Keen", "ash": "Unsheathe", "grip": "1H"}
    check("pierce_key: a stance row's button is part of the key (Unsheathe R1 vs R2), a plain row's unchanged",
          [pierce_key({**un, "stance": "r1"}), pierce_key({**un, "stance": "r2"}), pierce_key(un)],
          ["Uchigatana|Keen|Unsheathe|1H|stance r1", "Uchigatana|Keen|Unsheathe|1H|stance r2",
           "Uchigatana|Keen|Unsheathe|1H"])

    # Every `stage("...")` this file calls is in `STAGES`, which `Stages.__call__` indexes: a missing
    # one crashed the heavy-kit run at its 20th stage (2026-10-19, "set coverage").
    import re as _re
    called = set(_re.findall(r'\bstage\("([a-z][a-z ]*)"\)', Path(__file__).read_text()))
    check("STAGES: every stage the loadout calls is listed", sorted(called - set(STAGES)), [])

    # `_whiff_one` on forked workers returns what the serial loop does, in order.
    global _WHIFF_JOB
    import types
    fake_w = types.SimpleNamespace(from_profile=lambda opp, prof, gc: {"p": prof, "gc": gc, "opp": opp})
    _WHIFF_JOB = (fake_w, "opp", {"a": 1, "b": 2})
    jobs_in = [("a", 0.0), ("b", 7.5), ("c", 0.0)]
    import multiprocessing as mp
    with mp.get_context("fork").Pool(2) as pool:
        par = list(pool.imap(_whiff_one, jobs_in, chunksize=1))
    check("whiff: forked workers match the serial simulation", par, [_whiff_one(j) for j in jobs_in])
    _WHIFF_JOB = None
    # `whiff_groups`: the rows of one plan key together, in the order of each group's first row; a
    # row with no profile is a group of its own.
    sp = lambda esc: {"spread": [{"escape": e} for e in esc], "windows": [], "gate": 50.0, "poise": 89}
    got_ = {"a|Heavy": sp(["e1", "e2"]), "b": sp(["e9"]), "a|Blood": sp(["e1", "e2"])}
    need_ = {"k1": ("a|Heavy", 0.0), "k2": ("b", 0.0), "k3": ("a|Blood", 0.0), "k4": ("a|Blood", 7.0),
             "k5": ("gone", 0.0), "k6": ("gone", 0.0)}
    check("whiff_groups: one plan key a group, grip change part of the key, no profile alone",
          whiff_groups(list(need_), need_, got_), [["k1", "k3"], ["k2"], ["k4"], ["k5"], ["k6"]])

    # `off_stat`: a bullet with a stat term the build lacks is dropped; one with no stat term is not.
    check("off_stat: a bullet scaling 8 of 266 with INT on an INT 10 build is off-stat",
          off_stat({"ash": "Glintstone Pebble", "contrib": {"bullet": {
              "fixed": 60, "weapon level": 198, "str": 0, "dex": 0, "int": 8, "fth": 0, "arc": 0}}}), True)
    check("off_stat: Lightning Slash's Heavy Wakizashi bullet (fixed 80, weapon level 240, no stat) is kept",
          off_stat({"ash": "Lightning Slash", "contrib": {
              "blade": {"fixed": 58, "weapon level": 95, "str": 156, "dex": 0, "int": 0, "fth": 0, "arc": 0},
              "bullet": {"fixed": 80, "weapon level": 240, "str": 0, "dex": 0, "int": 0, "fth": 0, "arc": 0}}}),
          False)
    # The whiff reply measured from geometry is not scaled again by the landing proxies: score 100
    # x area_w 2 x reach_w 1.5 is 300 on the interrupt half (x 0.8) and 100 on the reply (x -0.4),
    # 0.5 x 240 - 0.5 x 40 = 100. A reply without geometry keeps both proxies on both halves.
    wx = {"score": 100, "dmg": 100, "cat": "melee", "area_w": 2.0, "reach_w": 1.5, "imult": 0.8,
          "wmult": -0.4, "whiff": {"geometry": 1.0}}
    check("value: area_w and reach_w scale the interrupt half only when the whiff reply is measured",
          round(value(wx), 6), 100.0)
    check("value: without geometry the proxies stay on both halves",
          round(value({**wx, "whiff": {}}), 6), 60.0)
    check("value: a punish larger than the interrupt half floors the row at 0",
          value({**wx, "wmult": -3.0}), 0.0)
    # Valued over the duel's distances, range is measured on both halves, so reach_w (and land_w)
    # come off both: the interrupt half is 100 x area_w 2 = 200 (x 0.8), the reply 100 (x -0.4),
    # 0.5 x 160 - 0.5 x 40 = 60, and land_w 0.5 changes nothing.
    check("value: over the duel spread reach_w and land_w come off both halves, area_w off the reply only",
          [round(value({**wx, "spread": True}), 6), round(value({**wx, "spread": True, "land_w": 0.5}), 6)],
          [60.0, 60.0])

    # `category_refs` (heavy-kit RL 125, 2026-10-19): the damaging ashes' values as that run had
    # them (best 476, 10th 225) and a utility category whose 10th best is 28 under War Cry's 226.
    # The utility scale is raised until War Cry's `norm` is no further above it than the pool's best
    # is above the pool's 10th; the damaging scale keeps its own 10th best.
    dmg_vals = [476, 406, 347, 344, 340, 270, 236, 230, 201, 225, 164, 158]
    util_vals = [226, 49, 45, 40, 38, 35, 33, 31, 30, 28, 14]
    crows = ([{"cat": "non-bullet", "ash": f"D{i}", "value": v} for i, v in enumerate(dmg_vals)]
             + [{"cat": "utility", "ash": "War Cry" if i == 0 else f"U{i}", "value": v} for i, v in enumerate(util_vals)])
    cref = category_refs(crows)
    pooled = sorted(dmg_vals + util_vals, reverse=True)
    check("category_refs: the damaging scale is its 10th best; the utility scale holds War Cry to the "
          "pool's best-over-10th spread",
          (round(cref["non-bullet"], 6), round(226 / cref["utility"], 4), cref["bullet"]),
          (225, round(pooled[0] / pooled[NORM_RANK - 1], 4), 1.0))
    # The cap then binds War Cry as it binds a damaging ash. Ten weapons, two slots each, each with
    # two damaging ashes of its own (norm 1.0 and 0.9 on the damaging scale) and War Cry (a copy
    # worth 200): on the old scale (utility 10th best, 28) War Cry's 7.1 pays the rising penalty
    # past `CLASS_ASH_CAP` on 9 weapons; on the bounded scale (1.87) a fourth carrier would gain 0.97
    # over the weapon's own 0.9 ash and pay 1, so it stays at the cap.
    def carriers(util_ref):
        ws = [f"W{i}" for i in range(10)]
        cands = {}
        for w in ws:
            cands[w, f"A{w}"] = {"weapon": w, "ash": f"A{w}", "cat": "non-bullet", "norm": 1.0}
            cands[w, f"B{w}"] = {"weapon": w, "ash": f"B{w}", "cat": "non-bullet", "norm": 0.9}
            cands[w, "War Cry"] = {"weapon": w, "ash": "War Cry", "cat": "utility", "norm": 200 / util_ref}
        got = spread_ashes(ws, cands, 2, CLASS_ASH_CAP, CLASS_ASH_PENALTY)
        return sum(any(x["ash"] == "War Cry" for x in xs) for xs in got.values())
    check("category_refs: War Cry carriers on the old utility scale vs the bounded one",
          (carriers(28.0), carriers(cref["utility"])), (9, CLASS_ASH_CAP))

    # Self-damage (`set_self_costs`), read from the regulation: Seppuku on Estoc 2H as the
    # heavy-kit sweep has it, at the heavy kit's 2594 max HP.
    def util(ash, dmg, score, last, added):
        return {"ash": ash, "weapon": "Estoc", "affinity": "Heavy", "grip": "2H", "cat": "utility", "dmg": dmg,
                "dps": score, "score": score, "last": last, "first": last - 10, "string": None, "quick": True,
                "follow_up": "roll_r1", "gate": 121.0, "area": 0.0, "status_hp": {}, "imult": 1.0,
                "buff": {"added": added, "share": round(added / last * FPS / score, 4), "status_hp": {}}}
    sep, cry = util("Seppuku", 257, 62, 139.0, 33.1), util("War Cry", 257, 70, 80.0, 60.0)
    plain = {k: value(x) for k, x in (("sep", sep), ("cry", cry))}
    set_self_costs([sep, cry], wear=(2594, []))
    check("self cost: Seppuku takes 15% of max HP plus 100 from its user",
          sep["self_cost"]["hp"], round(0.15 * 2594 + 100, 1))
    check("self cost: Seppuku is worth less than with no self-damage term", value(sep) < plain["sep"], True)
    check("self cost: a buff that does not hurt its user (War Cry) is unaffected",
          ("self_cost" in cry, value(cry)), (False, plain["cry"]))
    # `chain_net` on a one-row pool: his 200-poise hit lands at f5 (no approach at 2.5 m) on a
    # 100-poise me. My hits at f3 and f20 for 100 HP each: the first lands, the second is lost to
    # the break, and his hit takes `B_HIT_HP`. Hyperarmor +200 over f0-30 holds it: both land.
    import numpy as np
    from types import SimpleNamespace
    toy = SimpleNamespace(P=np.array([1000.0]), offs=np.array([0.0]), w=np.array([1.0]), reach=np.array([3.0]),
                          k=7.5, t=np.array([[5.0]]), q=np.array([[200.0]]), m=np.array([[True]]),
                          win=np.array([[[-1.0]], [[-1.0]], [[0.0]], [[1.0]], [[0.0]]]),
                          inv=np.array([[[-1.0]], [[-1.0]]]))
    net = chain_net(toy, 100.0)
    bh = _ai().B_HIT_HP
    check("chain_net: a hit that breaks me cancels my later hits and takes B_HIT_HP",
          round(net([(3.0, 10.0, 100.0), (20.0, 10.0, 100.0)], [], 40.0), 3), round(100.0 - bh, 3))
    check("chain_net: hyperarmor that holds his hit keeps both of mine",
          round(net([(3.0, 10.0, 100.0), (20.0, 10.0, 100.0)], [(0.0, 30.0, 200.0, 1.0, 0.0, 1.0)], 40.0), 3),
          round(200.0 - bh, 3))
    worn = ["Ritual Shield Talisman", "Crimson Amber Medallion +3"]
    # A follow-up big enough that the cast alone keeps some value, so the gear's part shows.
    sep2, cry2 = util("Seppuku", 2000, 450, 139.0, 33.1), util("War Cry", 257, 70, 80.0, 60.0)
    set_self_costs([sep2, cry2], wear=(2594, worn))
    alone = util("Seppuku", 2000, 450, 139.0, 33.1)
    set_self_costs([alone], wear=(2594, []))
    check("self cost: beside Seppuku, Ritual Shield's full-HP row is switched off and named spent",
          (sep2["self_cost"]["gear_hp"] > 0, sep2["self_cost"]["spent"]), (True, ["Ritual Shield Talisman"]))
    check("self cost: the switched-off talisman makes the cast cost more than it does alone",
          value(sep2) < value(alone), True)
    check("self cost: a non-damaging buff leaves a full-HP talisman alone", "self_cost" in cry2, False)

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
    gam = {"ash": "Assassin's Gambit", "hidden_beyond_m": 17.0}
    check("arena: Gambit (hidden past 17 m) out of a 16.9 m duel, kept with no bound or at 20 m, "
          "an ungated camouflage and a plain ash kept",
          [out_of_arena(gam, 16.9), out_of_arena(gam, None), out_of_arena(gam, 20.0),
           out_of_arena({"ash": "x", "hidden_beyond_m": 0.0}, 16.9), out_of_arena({"ash": "Quickstep"}, 16.9)],
          [True, False, False, False, False])
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

    # `derive_combos`: one feature per hand, scored as its share of the pool's best.
    cr = type("CR", (), {"FEATURES": ("m",), "OFFHAND_FEATURES": ("o",),
                         "offhand_weights": staticmethod(dict),
                         "norm_score": staticmethod(lambda f, pool, use, w: f[use[0]] / max(p[use[0]] for p in pool))})

    def hands(m, o):
        return {"main_hand": {"by_affinity": {a: {"features": {"m": v}} for a, v in m.items()}},
                "offhand": {"by_affinity": {a: {"features": {"o": v}} for a, v in o.items()}}}
    roles = {"weapons": {"Axe A": hands({"Heavy": 9, "Blood": 10}, {"Heavy": 1}),
                         "Dagger D": hands({"Heavy": 8}, {"Blood": 10, "Heavy": 7}),
                         "Halberd H": hands({"Heavy": 5}, {"Heavy": 6}),
                         "Club C": hands({"Heavy": 7}, {"Heavy": 2})},
             "classes": {"Axe": {"main_hand": {"weapon": "Axe A", "affinity": "Blood"},
                                 "offhand": {"weapon": "Axe A", "affinity": "Heavy"}},
                         "Dagger": {"main_hand": {"weapon": "Dagger D", "affinity": "Heavy"},
                                    "offhand": {"weapon": "Dagger D", "affinity": "Blood"}},
                         "Halberd": {"main_hand": {"weapon": "Halberd H", "affinity": "Heavy"},
                                     "offhand": {"weapon": "Halberd H", "affinity": "Heavy"}},
                         "Hammer": {"main_hand": {"weapon": "Club C", "affinity": "Heavy"},
                                    "offhand": {"weapon": "Club C", "affinity": "Heavy"}}}}
    pairs, plog = derive_combos(roles, cr, {}, {}, usable=lambda w: True, n=3)
    check("combos: the best main hands across classes, each with the best off hand but its own weapon",
          pairs, [("Axe", "Dagger", "Axe A", "Dagger D"), ("Dagger", "Halberd", "Dagger D", "Halberd H"),
                  ("Hammer", "Dagger", "Club C", "Dagger D")])
    pairs, plog = derive_combos(roles, cr, {}, {}, usable=lambda w: w != "Halberd H", n=2,
                                aff_ok=lambda w, aff: aff != "Blood")
    check("combos: a refused pick affinity falls back to the weapon's best allowed one, unusable weapons drop out",
          (pairs, plog[0].split(" (")[0]),
          ([("Axe", "Dagger", "Axe A", "Dagger D"), ("Dagger", "Hammer", "Dagger D", "Club C")], "Heavy Axe A"))
    roles["classes"]["Dagger"]["offhand"] = {"weapon": "Dagger E", "affinity": "Blood"}
    roles["weapons"]["Dagger E"] = hands({"Heavy": 1}, {"Blood": 10})
    pairs, plog = derive_combos(roles, cr, {}, {}, usable=lambda w: True, n=2)
    check("combos: with no powerstance records a class's own off hand is scored on its L1 chain, and says so",
          (pairs[1], "no powerstance record" in plog[1]), (("Dagger", "Dagger", "Dagger D", "Dagger E"), True))

    # Powerstance (`PowerstanceOffs`): the real class-roles scoring over two daggers and a halberd.
    # Dagger E's own L1 chain is weak, but Dagger D + Dagger E powerstance, and their dual moveset
    # (both hands' hits) beats the halberd's L1 on every family; the halberd's longer L1 reach is
    # not counted against a powerstance that has none. Halberd H cannot powerstance with a dagger.
    rcr = _class_roles()

    def l1rec(su, dmg, reach):
        s = {"startup": su, "roll_after_hit": 6, "damage": dmg, "next_cancel": 12, "hits": 1, "stamina": 10}
        return {"affinity": "Heavy", "summary": {"recovery": 6.0, "stamina": 10.0, "first_reach": reach},
                "slots": {"left_1": s, "left_2": dict(s)}}

    def psrec(right, left, su, dmg, wieldable=True, raff="Heavy"):
        # The dagger's own reach: the embedding measures each dual attack's reach since 2026-10-19,
        # and `l1_reach` is compared on both kinds of off hand.
        s = {"startup": su, "roll_after_hit": 5, "damage": dmg, "next_cancel": 12, "hits": 2, "stamina": 16,
             "reach": 2.0}
        return {"right": right, "right_affinity": raff, "left": left, "left_affinity": "Heavy",
                "class": "Dagger", "wieldable": wieldable, "summary": {"recovery": 5.0, "stamina": 16.0},
                "slots": {"dual_1": s, "dual_2": dict(s), "dual_dash": dict(s), "dual_roll": dict(s),
                          "dual_bstep": dict(s), "dual_crouch": dict(s)}}

    def rhands(m, l1):
        return {"main_hand": {"by_affinity": {"Heavy": {"features": {"ar": m}}}},
                "offhand": {"by_affinity": {"Heavy": {"features": rcr.offhand_features(l1)}}}}
    proles = {"weapons": {"Dagger D": {**rhands(10, l1rec(10, 50.0, 2.0)), "class": "Dagger"},
                          "Dagger E": {**rhands(9, l1rec(10, 40.0, 2.0)), "class": "Dagger"},
                          "Halberd H": {**rhands(8, l1rec(14, 120.0, 4.0)), "class": "Halberd"}},
              "classes": {"Dagger": {"main_hand": {"weapon": "Dagger D", "affinity": "Heavy"},
                                     "offhand": {"weapon": "Dagger E", "affinity": "Heavy"}},
                          "Halberd": {"main_hand": {"weapon": "Halberd H", "affinity": "Heavy"},
                                      "offhand": {"weapon": "Halberd H", "affinity": "Heavy"}}}}
    owts = {"l1_startup": 0.25, "l1_recovery": 0.2, "l1_reach": 0.2, "l1_chain_dps": 0.25, "l1_stamina": 0.1}
    # A set takes the better hand per feature (`SetScorer`) at the main table's weights, so the
    # shared features carry weight there too; these main hands measure only `ar`, and every other
    # feature is the off hand's.
    mwts = {"ar": 1.0, **{m_: owts[o_] for m_, o_ in SHARED_FEATURES.items() if o_ in owts}}

    def can(r, l):
        return r.startswith("Dagger") and l.startswith("Dagger")
    pso = PowerstanceOffs([psrec("Dagger D", "Dagger E", 9, 150.0), psrec("Dagger D", "Dagger D", 9, 120.0),
                           psrec("Dagger E", "Dagger D", 9, 150.0, wieldable=False),
                           psrec("Halberd H", "Dagger D", 9, 999.0),
                           psrec("Dagger D", "Dagger E", 9, 999.0, raff="Blood")],
                          can, rcr, lambda w, d: proles["weapons"][w]["class"])
    check("powerstance: candidates are wieldable IsEnableDualWielding records, a weapon with itself included",
          ({r: [x[0] for x in v] for r, v in pso.by_right.items()}, len(pso.pool)),
          ({"Dagger D": ["Dagger E", "Dagger D", "Dagger E"]}, 3))
    mains, offs_for = hand_scores(proles, rcr, {"ar": 1.0}, owts, lambda w: True, lambda w, a: True, pso)
    dm = next(m for m in mains if m[2] == "Dagger D")
    hm = next(m for m in mains if m[2] == "Halberd H")
    dofs = offs_for(dm)
    check("powerstance: a pair that powerstances is offered as the dual moveset, never as its L1 chain, "
          "on the record with the main hand's own affinity (Heavy, not the stronger Blood one), "
          "and a second copy of the main weapon is offered as its own powerstance",
          sorted((o[2], o.ps, o.right_aff) for o in dofs),
          [("Dagger D", True, "Heavy"), ("Dagger E", True, "Heavy"), ("Halberd H", False, None)])
    halb = next(o for o in dofs if o[2] == "Halberd H")
    check("powerstance: block costs left out on both kinds beside a powerstance main hand",
          halb[0], round(rcr.norm_score(halb.feats, [p for p in [proles["weapons"][w]["offhand"]["by_affinity"]
                                                                  ["Heavy"]["features"] for w in proles["weapons"]]]
                                        + pso.pool, [f for f in rcr.OFFHAND_FEATURES
                                                     if f not in rcr.POWERSTANCE_UNMEASURED],
                                        rcr.offhand_weights(owts)), 4))
    check("powerstance: a main hand with no powerstance candidate keeps reach, and a second copy that "
          "cannot powerstance is offered on its own L1 chain; every weapon's L1 is a candidate",
          sorted((o[2], o.ps, "l1_reach" in o.feats) for o in offs_for(hm)),
          [("Dagger D", False, True), ("Dagger E", False, True), ("Halberd H", False, True)])
    pairs, plog = derive_combos(proles, rcr, mwts, owts, usable=lambda w: True, n=2, ps=pso)
    check("powerstance: the dagger set powerstances, logged as the dual moveset",
          (pairs[0], "powerstance: the off-hand score is the dual moveset" in plog[0]),
          (("Dagger", "Dagger", "Dagger D", "Dagger E"), True))
    pairs, _ = derive_combos(proles, rcr, mwts, owts, usable=lambda w: True, n=2)
    check("powerstance: without the records the dagger takes the halberd's stronger L1 chain",
          pairs[0], ("Dagger", "Halberd", "Dagger D", "Halberd H"))
    # Two copies of one weapon: Dagger D with itself is the strongest powerstance; the set wears two
    # copies and pays for 2 x 2 weight.
    self_ps = PowerstanceOffs([psrec("Dagger D", "Dagger D", 9, 300.0)], can, rcr,
                              lambda w, d: proles["weapons"][w]["class"])
    pairs, plog = derive_combos(proles, rcr, mwts, owts, usable=lambda w: True, n=1, ps=self_ps,
                                weight={"Dagger D": 2.0, "Dagger E": 1.0, "Halberd H": 6.0}.get,
                                cost=WeightCost({"weights": [0, 40], "value": [1.0, 1.0]}))
    check("powerstance: a second copy of the main weapon makes the set, its weight counted twice",
          (pairs[0], ", weight 4 (" in plog[0], "two copies" in plog[0]),
          (("Dagger", "Dagger", "Dagger D", "Dagger D"), True, True))
    # The dual strings' interrupt multipliers from the roles (`powerstance_trades`): the record's
    # string and movement attacks are scaled by them, a record with none is left as it was.
    rec = psrec("Dagger D", "Dagger E", 9, 150.0)
    half = {"mult": 0.5, "poise_hit": 40.0}
    tr_ps = PowerstanceOffs([rec], can, rcr, lambda w, d: proles["weapons"][w]["class"],
                            trades={rcr.powerstance_key(rec): {"chain": half, "dash": half}})
    plain_ps = PowerstanceOffs([rec], can, rcr, lambda w, d: proles["weapons"][w]["class"],
                               trades={"other|key": {"chain": half}})
    f, g = tr_ps.pool[0], plain_ps.pool[0]
    check("powerstance trades: the dual string and the dash attack scaled, the roll attack and an "
          "unmatched record not",
          (f["l1_chain_dps"], f["l1_dash_damage"], f["l1_roll_damage"], f["l1_trade"], "l1_trade" in g),
          (round(g["l1_chain_dps"] * 0.5, 1), round(g["l1_dash_damage"] * 0.5, 1), g["l1_roll_damage"], 0.5, False))
    cs = [{"weapon": w, "affinity": a, "value": v} for w, a, v in
          (("Dagger D", "Heavy", 5), ("Dagger D", "Blood", 9), ("Dagger D", "Heavy", 3), ("Club C", "Heavy", 1),
           ("Dagger D", "Heavy", 8))]
    check("second copy: the right hand's affinity first, then value, within the row before the plan; "
          "none when the plan holds one copy",
          [second_copy(cs, 0, 0, 4), second_copy(cs, 0, 0, 1), second_copy(cs, 3, 3, 1)], [2, 4, None])

    # A paired main hand (Raptor Talons: `isDualBlade` and `bothHandEquipable`) two-handed: its one
    # copy plays the paired moveset, scored on its same-weapon record at the main hand's affinity,
    # against the real powerstance off hand (Claw B) on the same features. Measured 2026-10-19:
    # without it, Raptor + Bloodhound Claws won because Raptor two-handed scored 0 off hand.
    paired_ws = {"Claw R", "Claw B"}

    def claw_roles(tc=0):
        r = {"weapons": {"Claw R": {**rhands(10, l1rec(10, 50.0, 2.0)), "class": "Claw"},
                         "Claw B": {**rhands(8, l1rec(10, 40.0, 2.0)), "class": "Claw"}},
             "classes": {"Claw": {"main_hand": {"weapon": "Claw R", "affinity": "Heavy"},
                                  "offhand": {"weapon": "Claw B", "affinity": "Heavy"}}}}
        r["weapons"]["Claw R"]["main_hand"]["by_affinity"]["Heavy"]["features"]["true_combo"] = tc
        return r

    def claw_ps(self_dmg):
        return PowerstanceOffs([psrec("Claw R", "Claw R", 9, self_dmg), psrec("Claw R", "Claw B", 9, 150.0),
                                psrec("Claw R", "Claw R", 9, 999.0, raff="Blood")],
                               lambda r, l: not (r == l and r in paired_ws), rcr, lambda w, d: "Claw",
                               paired=lambda w: w in paired_ws)
    cps = claw_ps(300.0)
    check("two-handed: a paired weapon's same-weapon record on one affinity is its two-handed term, never "
          "a powerstance; a record across two affinities is neither",
          (sorted(cps.two_hand), {r: [x[0] for x in v] for r, v in cps.by_right.items()}),
          ([("Claw R", "Heavy")], {"Claw R": ["Claw B"]}))
    cmains, coffs_for = hand_scores(claw_roles(), rcr, {"ar": 1.0}, owts, lambda w: True, lambda w, a: True, cps)
    cm = next(m for m in cmains if m[2] == "Claw R")
    coffs = coffs_for(cm)
    check("two-handed: the paired main hand is offered itself two-handed beside the real powerstance, "
          "and no second copy",
          sorted((o[2], o.ps, o.two_hand) for o in coffs), [("Claw B", True, False), ("Claw R", False, True)])
    cw = {"Claw R": 2.0, "Claw B": 1.5}.get
    pairs, plog = derive_combos(claw_roles(), rcr, mwts, owts, usable=lambda w: True, n=1, ps=cps,
                                weight=cw, cost=WeightCost({"weights": [0, 40], "value": [1.0, 1.0]}))
    check("two-handed: a stronger paired moveset wins the set, one copy, weight counted once, no off weapon",
          (pairs[0], ", weight 2 (" in plog[0], "two-handed, one copy" in plog[0], "two copies" in plog[0]),
          (("Claw", "Claw", "Claw R", None), True, True, False))
    pairs, _ = derive_combos(claw_roles(), rcr, mwts, owts, usable=lambda w: True, n=1, ps=claw_ps(40.0),
                             weight=cw, cost=WeightCost({"weights": [0, 40], "value": [1.0, 1.0]}))
    check("two-handed: a weaker paired moveset loses to the powerstance on the same features",
          pairs[0], ("Claw", "Claw", "Claw R", "Claw B"))
    bonus = getattr(rcr, "TRUE_COMBO_BONUS", 0.0)
    tm, toffs_for = hand_scores(claw_roles(1), rcr, {"ar": 1.0}, owts, lambda w: True, lambda w, a: True, cps)
    tcm = next(m for m in tm if m[2] == "Claw R")
    t2h = next(o for o in toffs_for(tcm) if o.two_hand)
    c2h = next(o for o in coffs if o.two_hand)
    check("two-handed: the true combo counts once, on the main hand, not again on the two-handed term",
          (round(tcm[0] - cm[0], 4), t2h[0] == c2h[0], "true_combo" in t2h.feats), (round(bonus, 4), True, False))

    # Play modes (user, 2026-10-19: two-handing puts the left weapon on your back). Main hands
    # scored per grip (`by_grip`), shares of the pool's best m = 10 (Ref R, not usable); off hands
    # shares of the best o = 10. The bonus is the roles' own `TRUE_COMBO_BONUS`.
    gcr = type("CR", (), {"FEATURES": ("m",), "OFFHAND_FEATURES": ("o",), "offhand_weights": staticmethod(dict),
                          "TRUE_COMBO_BONUS": rcr.TRUE_COMBO_BONUS,
                          "norm_score": staticmethod(lambda f, pool, use, w: f[use[0]] / max(p[use[0]] for p in pool))})

    def gh(cls, grips=None, o=None, su=None, osu=None):
        su = su or {}
        mh = {"Heavy": {"features": {"m": max(grips.values()), "r1_startup": min(su.values(), default=None)},
                        "by_grip": {g: {"m": v, "r1_startup": su.get(g)} for g, v in grips.items()}}} if grips else {}
        return {"class": cls, "main_hand": {"by_affinity": mh},
                "offhand": {"by_affinity": {"Heavy": {"features": {"o": o, "l1_startup": osu}}} if o else {}}}

    def groles(*mains):
        ws = {"Ref R": gh("Ref", {"1H": 10, "2H": 10}, o=10), "Knife K": gh("Dagger", o=3, osu=9),
              "Club C": gh("Hammer", o=3.4)}
        ws.update(mains)
        return {"weapons": ws, "classes": {v["class"]: {"main_hand": {"weapon": w, "affinity": "Heavy"}}
                                           for w, v in ws.items() if v["main_hand"]["by_affinity"]}}
    usable = lambda w: w != "Ref R"   # noqa: E731
    # A STR main hand: 2H 0.9 alone beats 1H 0.4 + the best off hand 0.34; the old sum would have
    # credited 0.9 + 0.34.
    str_roles = groles(("Great G", gh("Greataxe", {"1H": 4, "2H": 9}, su={"1H": 12, "2H": 14})))
    pairs, glog = derive_combos(str_roles, gcr, {}, {}, usable=usable, n=1)
    check("modes: a STR main hand whose 2H beats 1H + off hand is played two-handed alone, no left weapon",
          (pairs, "two-handed alone" in glog[0], glog[0].split(" = ")[1][:6]),
          ([("Greataxe", "Greataxe", "Great G", None)], True, "0.9000"))
    pairs, glog = derive_combos(str_roles, gcr, {}, {}, usable=usable, n=1, weight={"Great G": 10.0, "Knife K": 2.0,
                                "Club C": 4.0}.get, cost=WeightCost({"weights": [0, 40], "value": [1.0, 1.0]}))
    check("modes: two-handed alone weighs the main hand only",
          (pairs[0][3], ", weight 10 (" in glog[0]), (None, True))
    # A main hand whose 1H R1 true-combos into Knife K's L1 (frame data, `CrossHand`): 2H 0.75 alone
    # beats 1H 0.4 + Club 0.34 = 0.74, but 1H + Knife 0.3 + the cross term 0.25 x 1.0 wins, over the
    # stronger Club L1.
    bk_roles = groles(("Halberd B", gh("Halberd", {"1H": 4, "2H": 7.5})))
    pairs, _ = derive_combos(bk_roles, gcr, {}, {}, usable=usable, n=1)
    check("modes: with no cross-hand link the halberd is two-handed alone", pairs,
          [("Halberd", "Halberd", "Halberd B", None)])
    xh = {("Halberd B", "Knife K"): (1.0, "r1_1->left_1")}
    pairs, glog = derive_combos(bk_roles, gcr, {}, {}, usable=usable, n=1, cross=lambda r, l: xh.get((r, l)))
    check("modes: a 1H R1 that true-combos into the off hand's L1 is played one-handed with it, the term logged",
          (pairs, "cross-hand r1_1->left_1 p 1.00 +0.2500" in glog[0], "main 1H 0.4000" in glog[0]),
          ([("Halberd", "Dagger", "Halberd B", "Knife K")], True, True))
    # No set is credited a two-handed main hand and an off-hand term: every candidate's set score is
    # its own grip's main score plus, one-handed only, the off hand and the cross term.
    audit = []
    for rl_ in (str_roles, bk_roles):
        ms, of = hand_scores(rl_, gcr, {}, {}, usable, lambda w, a: True, cross=lambda r, l: xh.get((r, l)))
        for m in ms:
            for o in of(m):
                if o.grip == "2H":
                    audit.append(o.alone and o[0] == 0.0 and o.cross == 0.0 and set_score(m, o) == m.grips["2H"])
                else:
                    audit.append(not o.two_hand and set_score(m, o) == m.grips["1H"] + o[0] + o.cross)
    check("modes: no set credited both a 2H main hand and an off-hand or cross term", (len(audit), all(audit)),
          (6, True))
    # Semi-wieldable (STR met two-handed only): no one-handed mode at all.
    semi = groles(("Colossal X", gh("Colossal", {"2H": 8})))
    ms, of = hand_scores(semi, gcr, {}, {}, usable, lambda w, a: True)
    check("modes: a main hand wieldable two-handed only is offered alone, never with an off hand",
          [(o[2], o.alone) for o in of(next(m for m in ms if m[2] == "Colossal X"))], [("Colossal X", True)])
    # The ladder's bans (`--exclude`, `ladder_exclude`) reach the sets: measured 2026-10-19, the
    # mode report passed every weapon as usable and listed Dueling Shield, Idus Sword and Sword
    # Lance. A banned weapon is in neither hand; a grip ban (Stone Club 2H) drops that grip only.
    saved = {k: (set(v) if isinstance(v, set) else list(v)) for k, v in EXCLUDE.items()}
    set_exclude({"weapons": ["Knife K"], "rows": [["Great G", None, "2H"]]})
    try:
        ms, of = hand_scores(str_roles, gcr, {}, {}, lambda w: usable(w) and w not in EXCLUDE["weapons"],
                             lambda w, a: True)
        gm = next(m for m in ms if m[2] == "Great G")
        got = (sorted(gm.grips), sorted({o[2] for m in ms for o in of(m)}), any(m[2] == "Knife K" for m in ms),
               any(o.alone for o in of(gm)))
    finally:
        EXCLUDE.update(saved)
    check("ladder bans: a banned weapon is in neither hand, a banned grip is no mode",
          got, (["1H"], ["Club C"], False, False))
    check("ladder bans: the duel exclusion holds the weapons the mode report used to list",
          {"Dueling Shield", "Idus Sword", "Sword Lance"} <= set(ladder_exclude()["weapons"]), True)
    # `powerstance_offs.can` on the regulation the sets load (`er-mechanics-ar.Tables`): the pair
    # rule reads `wepmotionCategory`, so Pickaxe (great hammer by `wepType`, motion 32) pairs with
    # Rusted Anchor (greataxe, motion 32) and not with Great Stars (great hammer, motion 35); only
    # these two records are offered. A table whose rows lack the field raises instead of quietly
    # refusing every powerstance.
    spec = importlib.util.spec_from_file_location("er_mechanics_ar", HERE / "er-mechanics-ar.py")
    ARm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ARm)
    at = ARm.Tables(None)
    precs = [{"right": r, "left": l, "right_affinity": "Heavy", "left_affinity": "Heavy", "wieldable": True,
              "class": "Great Hammer"} for r, l in (("Pickaxe", "Rusted Anchor"), ("Pickaxe", "Great Stars"),
                                                     ("Rusted Anchor", "Pickaxe"))]
    pcr = SimpleNamespace(powerstance_features=lambda p, t=None: {"ar": 1.0}, POWERSTANCE_UNMEASURED=())
    pso_real = powerstance_offs(precs, at, {}, pcr)
    check("powerstance: Pickaxe pairs with Rusted Anchor both ways and never with Great Stars",
          ({r: [x[0] for x in v] for r, v in pso_real.by_right.items()}, pso_real.can("Pickaxe", "Great Stars")),
          ({"Pickaxe": ["Rusted Anchor"], "Rusted Anchor": ["Pickaxe"]}, False))
    stripped = SimpleNamespace(weapons={k: {f: v for f, v in row.items() if f != "wepmotionCategory"}
                                        for k, row in at.weapons.items()}, find_weapon=at.find_weapon)
    try:
        powerstance_offs(precs[:1], stripped, {}, pcr)
        raised = False
    except KeyError:
        raised = True
    check("powerstance: a loader without wepmotionCategory raises rather than refusing every pair", raised, True)
    # The speed term reads the mode's grip: alone, R1 #1 two-handed (f14) and nothing from L1;
    # one-handed with Knife K, its L1 #1 at f9.
    gs = SetSpeed(str_roles, lambda s: s)
    gm = next(m for m in hand_scores(str_roles, gcr, {}, {}, usable, lambda w, a: True)[0] if m[2] == "Great G")
    gof = hand_scores(str_roles, gcr, {}, {}, usable, lambda w, a: True)[1](gm)
    check("modes: the speed term takes the 2H R1 alone, the soonest of 1H R1 and L1 with an off hand",
          (gs(gm, next(o for o in gof if o.alone))[0], gs(gm, next(o for o in gof if o[2] == "Knife K"))[0]), (14, 9))
    # `cross_land`: the best link between R1/R2 and L1 #1 either way, by landing chance; a running
    # R1 and the off hand's own L1 string are not cross-hand links.
    C = _combo()

    def lk(first, nxt, verdict, stagger=1.0):
        return {"first": first, "next": nxt, "stagger": stagger, "breaks_victim": True,
                "on_break": {"verdict": verdict}, "on_intact": {"verdict": "blocked by poise"}}
    check("cross_land: a true R1 -> L1 over a tie, movement and L1 -> L1 links ignored, none when nothing lands",
          [cross_land([lk("r1_2", "left_1", "tie"), lk("r1_1", "left_1", "true", 0.86),
                       lk("run_r1", "left_1", "true"), lk("left_1", "left_2", "true")], C.link_land),
           cross_land([lk("left_1", "r1_1", "true", 0.13)], C.link_land),
           cross_land([lk("r1_1", "left_1", "roll-out-able"), lk("run_r1", "left_1", "true")], C.link_land)],
          [(0.86, "r1_1->left_1"), (0.13, "left_1->r1_1"), None])

    # Set coverage (`cover_sets`): every weapon its best set, as main hand or as off hand.
    def cover(rl_, ws, xf=None):
        hs = hand_scores(rl_, gcr, {}, {}, usable, lambda w, a: True, cross=xf, all_mains=True)
        return cover_sets(hs, ws, lambda m, o: set_score(m, o))
    best_g, cov_g = cover(str_roles, ["Great G", "Knife K", "Club C"])
    check("cover: every plan weapon is in some set",
          sorted(w for *_, covers in cov_g for w in covers), ["Club C", "Great G", "Knife K"])
    check("cover: a weapon whose best set is two-handed gets a set to itself",
          [(set_weapons(m, o), o.alone, covers) for _, m, o, covers in cov_g if "Great G" in covers],
          [(("Great G",), True, ["Great G"])])
    check("cover: an off-hand-only weapon takes its best main hand one-handed",
          [set_weapons(m, o) for _, m, o, covers in cov_g if "Knife K" in covers], [("Great G", "Knife K")])
    _, cov_b = cover(bk_roles, ["Halberd B", "Knife K", "Club C"], lambda r, l: xh.get((r, l)))
    check("cover: two weapons that are each other's best partner share one set",
          [(set_weapons(m, o), sorted(covers)) for _, m, o, covers in cov_b],
          [(("Halberd B", "Knife K"), ["Halberd B", "Knife K"]), (("Halberd B", "Club C"), ["Club C"])])
    # The set coverage stage's counter: every candidate hand, every main hand's off hands, every
    # weapon covered, with the candidates that are no main hand skipped past, so it ends at N/N.
    ws_g = ["Great G", "Knife K", "Club C"]
    n_main, n_off = hand_picks(str_roles, all_mains=True)
    err = io.StringIO()
    cnt = Count(n_main + n_off + n_main + len(ws_g), "weapons", out=err)
    hs = hand_scores(str_roles, gcr, {}, {}, usable, lambda w, a: True, all_mains=True, count=cnt)
    cnt.skip(n_main - len(hs[0]))
    best_c, cov_c = cover_sets(hs, ws_g, lambda m, o: set_score(m, o), count=cnt)
    check("cover: counted, same sets, ending at N/N",
          (cov_c == cov_g, cnt.i == cnt.total > 0, err.getvalue().splitlines()[-1] == f"  {cnt.total}/{cnt.total} weapons"),
          (True, True, True))
    # `Count` itself: the first item and the last always print, the rest at most once per
    # `COUNT_SECONDS` of its clock.
    clk = [0.0]
    err = io.StringIO()
    cnt = Count(10, "weapons", clock=lambda: clk[0], out=err)
    for i in range(9):
        clk[0] = i * 0.5
        cnt()
    cnt.skip(1)
    check("Count: first, one per COUNT_SECONDS, last; lines in the watcher's counter format",
          err.getvalue().splitlines(), ["  1/10 weapons", "  5/10 weapons", "  9/10 weapons", "  10/10 weapons"])

    # Better hand per feature (`SetScorer`, user 2026-10-19), on the real class-roles features. A STR
    # main hand: 2H AR 150, reach 3.2, R1 string 100 MV/s; 1H AR 100, reach 3.0, string 80. Knife
    # K's L1 string is the off-hand pool's best, its reach 2.0. Weights ar 0.5, reach 0.25, string
    # 0.25 (half on the string, half on the movement R1s, `offhand_weights`'s split). Alone: 1.0.
    # One-handed with K: AR 100/150 x 0.5 (1H AR, never 2H's), reach 3.0/3.2 x 0.25 (the 1H R1 over
    # K's L1), string 1.0 x 0.125 (K's over the 1H's 0.8), movement 1.0 x 0.125 (a plain off hand's
    # movement L1 is its L1 #1, the pool's best, over the 1H string standing in for the main hand's
    # unmeasured movement R1s at 0.8) = 0.8177, so 2H wins, where the old sum credited 1H + all of K.
    def sh(cls, grips=None, l1=None):
        mh = ({"Heavy": {"features": {f: max(g[f] for g in grips.values()) for f in ("ar", "r1_reach", "r1_chain_dps")},
                         "by_grip": grips}} if grips else {})
        return {"class": cls, "main_hand": {"by_affinity": mh},
                "offhand": {"by_affinity": {"Heavy": {"features": rcr.offhand_features(l1)}} if l1 else {}}}
    s_roles = {"weapons": {"Great G": sh("Greataxe", {"1H": {"ar": 100, "r1_reach": 3.0, "r1_chain_dps": 80},
                                                      "2H": {"ar": 150, "r1_reach": 3.2, "r1_chain_dps": 100}}),
                           "Knife K": sh("Dagger", l1=l1rec(10, 50.0, 2.0)),
                           "Club C": sh("Hammer", l1=l1rec(14, 30.0, 2.5)),
                           "Wand W": sh("Staff", l1=l1rec(30, 1.0, 1.0))},
               "classes": {"Greataxe": {"main_hand": {"weapon": "Great G", "affinity": "Heavy"}}}}
    swts = {"ar": 0.5, "r1_reach": 0.25, "r1_chain_dps": 0.25}
    sms, sof = hand_scores(s_roles, rcr, swts, owts, lambda w: True, lambda w, a: True)
    sg = next(m for m in sms if m[2] == "Great G")
    so = {(o[2], o.alone): o for o in sof(sg)}
    check("per feature: alone is the 2H main hand's own score; one-handed takes the 1H AR, the better "
          "reach, K's string and K's movement L1; the old sum would have been over 1.0",
          (set_score(sg, so["Great G", True]), set_score(sg, so["Knife K", False]),
           round(sg.grips["1H"] + so["Knife K", False][0], 4) > 1.0), (1.0, 0.8177, True))
    check("per feature: an off hand no better on any feature adds nothing to the 1H main hand",
          (set_score(sg, so["Wand W", False]), sg.grips["1H"]), (sg.grips["1H"], 0.7677))
    pairs, slog = derive_combos(s_roles, rcr, swts, owts, usable=lambda w: True, n=1)
    check("per feature: a STR main hand whose off hands only improve a few features two-hands",
          (pairs, "two-handed alone" in slog[0]), ([("Greataxe", "Greataxe", "Great G", None)], True))
    sx = {("Great G", "Knife K"): (1.0, "r1_1->left_1")}
    pairs, slog = derive_combos(s_roles, rcr, swts, owts, usable=lambda w: True, n=1,
                                cross=lambda r, l: sx.get((r, l)))
    check("per feature: a measured R1 -> L1 true combo stays an additive bonus and wins one-handed "
          "(0.8177 + 0.25 over 1.0)",
          (pairs, "= 1.0677, cross-hand r1_1->left_1" in slog[0], "not their sum" in slog[0]),
          ([("Greataxe", "Dagger", "Great G", "Knife K")], True, True))
    audit = []
    for o in hand_scores(s_roles, rcr, swts, owts, lambda w: True, lambda w, a: True,
                         cross=lambda r, l: sx.get((r, l)))[1](sg):
        if o.alone:
            audit.append(set_score(sg, o) == round(sg.scorer.value(sg.feats["2H"]), 4))
        else:
            v = set_score(sg, o) - o.cross
            audit.append(round(sg.scorer.value(sg.feats["1H"]), 4) - 1e-9 <= v
                         <= sg.grips["1H"] + o[0] + 1e-9)
    check("per feature: alone scores the 2H features with no off option; one-handed is at least the 1H "
          "main hand alone and at most the old sum", (len(audit), all(audit)), (4, True))
    # Block costs: the smaller penalty of the two hands; an off hand that does not measure it (a
    # powerstance) is no option, so it neither zeroes nor adds the main hand's cost.
    pc = SetScorer(rcr, {"ar": 1.0, "parry_cost": 1.0}, owts, [{"ar": 100, "parry_cost": 50}],
                   [{"l1_hit": 100, "l1_parry_cost": 10}], [])
    fm = {"ar": 100, "parry_cost": 50}
    check("per feature: block cost the smaller measured penalty, an unmeasured off hand no option",
          [round(pc.value(fm), 4), round(pc.value(fm, {"l1_hit": 100, "l1_parry_cost": 10}), 4),
           round(pc.value(fm, {"l1_hit": 100, "l1_parry_cost": None}), 4)], [0.5, 0.9, 0.5])
    # Reach over the duel spread (`cr.spread_weights`): the roles write `r1_reach` at 0 and the
    # crit gap stops scoring reach in its place; at a fixed distance it still does.
    rw = {"ar": 1.0, "r1_reach": 1.0, "crit": 1.0}
    rpool = [{"ar": 100, "r1_reach": 4.0, "crit": 150}, {"ar": 100, "r1_reach": 2.0}]
    fixed_s = SetScorer(rcr, rw, owts, rpool, [{"l1_hit": 1.0}], [])
    spread_s = SetScorer(rcr, rcr.spread_weights(rw, {"nodes": [(2.0, 1.0)]}), owts, rpool, [{"l1_hit": 1.0}], [])
    check("reach over the spread: r1_reach and the crit gap's reach both off; at a fixed distance both on",
          [round(fixed_s.value(rpool[1]), 4), round(spread_s.value(rpool[1]), 4),
           round(spread_s.value({"ar": 100, "r1_reach": 4.0}), 4)], [0.6667, 1.0, 1.0])
    # The ash term over a duel's distance bands (`ASH_BANDS`, `SetScorer.ash_share`): a close ash
    # (Unsheathe-like, 100 a band where it reaches, its reach ending inside the 4.38 m band) and a
    # far ash (Storm Blade-like, 40 at every band). The set holding both presses the close one up
    # close and the far one beyond: 18.7 + 40 x 0.813 = 51.2 against the far ash's 40 alone, its
    # 40 again with two copies, and the close ash's 18.7.
    duel_w = [0.018, 0.119, 0.186, 0.144, 0.136, 0.133, 0.132, 0.132]
    nodes = [0.95, 2.16, 4.38, 7.28, 10.42, 13.32, 15.54, 16.75]
    close = [[0.95, 0.018, 1.0, 100.0], [2.16, 0.119, 1.0, 100.0], [4.38, 0.05, 1.0, 100.0]]
    far = [[d, w, 1.0, 40.0] for d, w in zip(nodes, duel_w)]
    saved = dict(ASH_BANDS)
    ASH_BANDS.clear()
    ASH_BANDS.update({("Uchi U", "Heavy", "1H"): close, ("Waki W", "Heavy", "1H"): far,
                      ("Waki W", "Heavy", "2H"): far})
    try:
        bs = SetScorer(rcr, {"ash_value": 1.0}, owts, [{"ash_value": 1.0}], [{"l1_hit": 1.0}], [])
        uchi = MainHand((0.0, "Katana", "Uchi U", "Heavy"), grips={"1H": 0.0}, feats={"1H": {"ash_value": 1.0}})
        waki = MainHand((0.0, "Katana", "Waki W", "Heavy"), grips={"1H": 0.0}, feats={"1H": {"ash_value": 1.0}})
        off_waki = OffHand((0.0, "Katana", "Waki W", "Heavy"), {"l1_hit": 1.0})
        off_none = OffHand((0.0, "Staff", "Wand W", "Heavy"), {"l1_hit": 1.0})
        got = [bs.pair(uchi, off_waki), bs.pair(uchi, off_none), bs.pair(waki, off_none), bs.pair(waki, off_waki)]
        # Which hand L2 casts (c0000.hks `GetSwordArtInfo`): a left-hand shield art (Parry 92)
        # takes L2 one-handed, a left-hand bow skill (Barrage 101) blocks it, and then the main
        # hand's ash is its 2H row only (30 a band here, against 40 one-handed).
        far2 = [[d, w, 1.0, 30.0] for d, w in zip(nodes, duel_w)]
        ASH_BANDS.update({("Waki W", "Heavy", "2H"): far2, ("Para P", "Heavy", "1H"): close,
                          ("Bow B", "Heavy", "2H"): close})
        rows_l2 = [{"cat": "melee", "weapon": "Para P", "affinity": "Heavy", "grip": "1H", "ash": "Parry-like",
                    "by_distance": close, "score": 1, "dmg": 1},
                   {"cat": "melee", "weapon": "Bow B", "affinity": "Heavy", "grip": "2H", "ash": "Barrage-like",
                    "by_distance": close, "score": 1, "dmg": 1},
                   {"cat": "melee", "weapon": "Waki W", "affinity": "Heavy", "grip": "2H", "ash": "Unsheathe-like",
                    "by_distance": far2, "score": 1, "dmg": 1}]
        kinds = {"Parry-like": 92, "Barrage-like": 101, "Unsheathe-like": 14}
        saved_l2 = dict(ASH_BAND_LEFT_L2)
        ASH_BAND_LEFT_L2.clear()
        ASH_BAND_LEFT_L2.update(ash_band_left_l2(rows_l2, kinds.get))
        l2_kinds = dict(ASH_BAND_LEFT_L2)
        bs2 = SetScorer(rcr, {"ash_value": 1.0}, owts, [{"ash_value": 1.0}], [{"l1_hit": 1.0}], [])
        off_para = OffHand((0.0, "Dagger", "Para P", "Heavy"), {"l1_hit": 1.0})
        off_bow = OffHand((0.0, "Bow", "Bow B", "Heavy"), {"l1_hit": 1.0})
        got_l2 = [bs2.pair(waki, off_para), bs2.pair(waki, off_bow), bs2.pair(uchi, off_bow),
                  bs2.pair(waki, off_none)]
        ASH_BAND_LEFT_L2.clear()
        ASH_BAND_LEFT_L2.update(saved_l2)
    finally:
        ASH_BANDS.clear()
        ASH_BANDS.update(saved)
    check("L2 hand: SwordArtsParam.swordArtsTypeNew of Parry, Barrage and Unsheathe",
          [left_l2_kind(92), left_l2_kind(101), left_l2_kind(14)], ["shield", "arrow", None])
    check("L2 hand: a shield art or bow skill on the left takes L2 one-handed, so the main hand's ash "
          "is its 2H row; the shield art casts from its 1H row; an off hand with neither leaves the main's 1H ash",
          (l2_kinds, [round(g, 4) for g in got_l2]),
          ({("Para P", "Heavy", "1H"): "shield", ("Bow B", "Heavy", "2H"): "arrow"},
           # (18.7 + 30 x 0.813) / 40 = 1.07725, which the float sum rounds up
           [1.0773, 1.0773, round(18.7 / 40, 4), 1.0]))
    # `set_by_distance`: per band the two replies mixed as `value` mixes them, times `value_base`.
    bx = {"score": 100, "dmg": 100, "cat": "melee", "spread": True, "wmult": 0.0, "whiff": {"geometry": 1.0,
          "spread": [[1.0, 0.2, -0.2, 0.95], [2.0, 0.3, 0.4, 2.16]]}, "imult_by": [[0.95, 0.2, 0.8], [2.16, 0.3, 0.6]]}
    set_by_distance([bx])
    check("duel bands: each band's multiplier mixes that band's interrupt and whiff replies",
          bx["by_distance"], [[0.95, 0.2, 0.3, 30.0], [2.16, 0.3, 0.5, 50.0]])
    # A perfect block measured per band (`bmult_by`): each band takes its own `bmult`, not the
    # whole duel's 0.9.
    bb = {**bx, "bmult": 0.9, "bmult_by": [[0.95, 0.8], [2.16, 1.0]]}
    set_by_distance([bb])
    check("duel bands: a per-band perfect-block multiplier replaces the duel's in its band",
          bb["by_distance"], [[0.95, 0.2, 0.3, 24.0], [2.16, 0.3, 0.5, 50.0]])
    # `merge_stance_buttons`: a stance skill's R1 and R2 rows on one carrier are joined. R1 is worth
    # 100 at 0.95 m and 20 at 2.16 m (its reach ends there), R2 50 at both and reaches on into the
    # 4.38 m band. Per band the better button: 0.95 m R1 (100 x 0.018), 2.16 m R2 (50 x 0.119),
    # 4.38 m R2 (50 x 0.05) = 10.25, against R2's 9.35 alone (kept, the better alone) and R1's 4.18.
    def stance_row(button, bands, score):
        return {"weapon": "Uchi U", "affinity": "Heavy", "ash": "Stance-like", "grip": "1H", "cat": "non-bullet",
                "stance": button, "variant": f"stance {button.upper()}", "score": score, "dmg": score,
                "by_distance": bands}
    sr1 = stance_row("r1", [[0.95, 0.018, 1.0, 100.0], [2.16, 0.119, 1.0, 20.0]], 60)
    sr2 = stance_row("r2", [[0.95, 0.018, 1.0, 50.0], [2.16, 0.119, 1.0, 50.0], [4.38, 0.05, 1.0, 50.0]], 50)
    lone = {**stance_row("r1", [[0.95, 0.018, 1.0, 10.0]], 10), "weapon": "Other O"}
    joined = merge_stance_buttons([sr1, sr2, lone])
    j = next(x for x in joined if x["weapon"] == "Uchi U")
    AIm = _ai()
    check("stance buttons: R1 and R2 rows join into one row that presses the better button per band",
          (len(joined), j["stance"], j["stance_buttons"], j["stance_by_band"],
           round(AIm.band_set_value([j["by_distance"]]), 3), j["stance_w"], "stance_w" in lone),
          (2, "r2", ["r1", "r2"], [[0.95, "r1"], [2.16, "r2"], [4.38, "r2"]], 10.25,
           round(10.25 / 9.35, 4), False))
    check("stance buttons: the joined row's value is its own button's times stance_w",
          round(value(j), 4), round(value({**j, "stance_w": 1.0}) * round(10.25 / 9.35, 4), 4))
    # No bands (one fixed distance, no situation to choose by): the better button by `value`.
    nb1 = {**stance_row("r1", None, 60)}
    nb2 = {**stance_row("r2", None, 50)}
    nj = merge_stance_buttons([nb1, nb2])
    check("stance buttons: without bands the better button by value is kept and nothing is added",
          (len(nj), nj[0]["stance"], "stance_w" in nj[0]), (1, "r1", False))
    check("duel bands: a close ash and a far ash in one set beat either alone and two copies of the far one",
          (got[0] > max(got[1:]), got[2] == got[3], [round(g, 4) for g in got]),
          (True, True, [round((18.7 + 40 * (1 - 0.018 - 0.119 - 0.05)) / 40, 4), round(18.7 / 40, 4), 1.0, 1.0]))

    # `arrange_sets`: a hub in four sets sits in the middle, one copy, each partner 1 or 2 slots
    # away; with ten partners the line cannot hold them all within `SET_NEAR`, so the farthest get a
    # second hub copy beside them, and every set ends within `SET_NEAR`.
    order, at = arrange_sets([("H", p) for p in "ABCD"])
    check("arrange: a hub with 4 partners is next to all 4, one copy of it",
          (order.count("H"), sorted(abs(i - j) for i, j in at)), (1, [1, 1, 2, 2]))
    order, at = arrange_sets([("H", p) for p in "abcdefghij"] + [("a", "b"), ("X",), ("Y", "Y")])
    check("arrange: every set within SET_NEAR, a second hub copy only where the line runs out, two copies "
          "of one weapon side by side, a one-weapon set placed",
          (max(abs(r[0] - r[1]) for r in at if len(r) == 2) <= SET_NEAR, order.count("H"),
           abs(at[-1][0] - at[-1][1]), order[at[-2][0]], all(order[i] == w for r, s in zip(at, [("H", p) for p in "abcdefghij"])
                                                               for i, w in zip(r, s))),
          (True, 3, 1, "X", True))

    # Off-hand partners (`offhand_partners`, `Partners.need`): keyed by the measured sets' weapons.
    # A claw that is not a set's left weapon wants nothing, nor does a paired left weapon.
    check("partners: the left weapon of a measured set wants its right weapon; two-handed and two-copy "
          "sets name none",
          offhand_partners([("Fist", "Claw", "Star Fist", "Bloodhound Claws"), ("Axe", "Dagger", "Hand Axe", "Dagger D"),
                            ("Claw", "Claw", "Raptor Talons", None), ("Dagger", "Dagger", "Dagger E", "Dagger E")]),
          {"Bloodhound Claws": ("Star Fist",), "Dagger D": ("Hand Axe",)})
    prow = {"Raptor Talons": {"isDualBlade": 1, "bothHandEquipable": 1}, "Bloodhound Claws":
            {"isDualBlade": 1, "bothHandEquipable": 1}, "Star Fist": {"isDualBlade": 1, "bothHandEquipable": 1},
            "Hand Axe": {}, "Dagger D": {}, "Club C": {}}
    P = Partners.__new__(Partners)
    P.tables = type("T", (), {"weapons": prow, "find_weapon": staticmethod(lambda w, a: w)})()
    P.GM = type("GM", (), {"ps_affinities": staticmethod(lambda e, w: []), "modes": staticmethod(lambda e, w, a: {})})
    P.e, P._need, P._scores = {}, {}, {}
    saved = dict(OFFHAND_PARTNERS)
    OFFHAND_PARTNERS.clear()
    OFFHAND_PARTNERS.update(offhand_partners([("Fist", "Claw", "Star Fist", "Bloodhound Claws"),
                                              ("Axe", "Dagger", "Hand Axe", "Dagger D")]))
    check("partners: a claw outside the set and a paired left weapon want no off-hand partner; a plain "
          "left weapon wants only its set's right weapon",
          [P.need("Raptor Talons", "Heavy"), P.need("Bloodhound Claws", "Heavy"), P.need("Dagger D", "Heavy"),
           P.ok("Dagger D", "Heavy", "Hand Axe"), P.ok("Dagger D", "Heavy", "Club C")],
          [None, None, "offhand", True, False])
    OFFHAND_PARTNERS.clear()
    OFFHAND_PARTNERS.update(saved)

    # `WeightCost`: linear between the table's points, 0 past its last.
    wc = WeightCost({"weights": [0, 10, 20], "value": [1.0, 0.9, 0.5]})
    check("weight cost: interpolated, 0 past the table", [wc(0), wc(5), wc(15), wc(20), wc(20.5)],
          [1.0, 0.95, 0.7, 0.5, 0.0])
    # The weight cap: Axe A (weight 20) leads the main hands, and Dagger D (weight 2) is the best
    # off hand. A steep cost past 10 drops the heavy axe for the light pairs under 8; a flat one
    # keeps it.
    roles["classes"]["Dagger"]["offhand"] = {"weapon": "Dagger D", "affinity": "Blood"}
    # Every weapon's L1 is an off-hand candidate, not only the class picks, so the Dagger E added
    # above leaves the roles with its class pick.
    roles["weapons"].pop("Dagger E", None)
    wts = {"Axe A": 20.0, "Dagger D": 2.0, "Halberd H": 6.0, "Club C": 3.0, "Dagger E": 1.0}
    steep = WeightCost({"weights": [0, 10, 25, 40], "value": [1.0, 1.0, 0.5, 0.4]})
    flat = WeightCost({"weights": [0, 40], "value": [1.0, 1.0]})
    pairs, plog = derive_combos(roles, cr, {}, {}, usable=lambda w: True, n=2, weight=wts.get, cost=steep)
    check("combos: a heavy main hand is left out when its weight costs more than its score adds",
          [p[2] for p in pairs], ["Club C", "Halberd H"])
    check("combos: the weight cap is logged with what dropping the cost would change",
          (plog[-2].startswith("weight cap 8:"), plog[-1].startswith("without the weight cost")), (True, True))
    pairs, _ = derive_combos(roles, cr, {}, {}, usable=lambda w: True, n=2, weight=wts.get, cost=flat)
    check("combos: with no cost to weight the best pairs lead whatever they weigh",
          [p[2] for p in pairs], ["Axe A", "Club C"])
    pairs, _ = derive_combos(roles, cr, {}, {}, usable=lambda w: True, n=2, weight=wts.get, cost=steep, floor=25.0)
    check("combos: weight the stats already pay for (a focus set) costs a pair nothing more",
          [p[2] for p in pairs], ["Axe A", "Club C"])
    # The layout stage's counters (`derive_combos(progress=True)`): the hands scored, the main
    # hands' off hands, the caps tried, each ending at N/N, in the watcher's counter format.
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        pairs_p, _ = derive_combos(roles, cr, {}, {}, usable=lambda w: True, n=2, weight=wts.get, cost=steep,
                                   progress=True)
    got = [x.strip() for x in err.getvalue().splitlines()]
    units = [x.split()[1] for x in got]
    ends = {u: x for x, u in zip(got, units)}
    check("combos progress: same picks, counters over hands, mains and caps, each finished",
          ([p[2] for p in pairs_p], list(dict.fromkeys(units)),
           all(re.fullmatch(r"(\d+)/\1 \w+", ends[u]) for u in ends)),
          (["Club C", "Halberd H"], ["hands", "mains", "caps"], True))

    # The speed term (`SetSpeed`, `_set_value`): a slow main hand (R1 #1 at f20) and a quick one
    # (f10); a strong slow off hand (L1 #1 at f22) and a weaker fast one (f9). Coverage 1 at f10 or
    # sooner, 0 at f20, k 0.5. Without the term both sets share the strong off hand; with it the
    # slow main hand takes the fast off hand (1.8 x 1.5 = 2.7 over 2.0) and the quick one keeps
    # the strong one (1.6 x 1.5 = 2.4 over 1.4 x 1.5).
    def sp(m=None, o=None, m_su=None, o_su=None):
        return {"main_hand": {"by_affinity": {"Heavy": {"features": {"m": m, "r1_startup": m_su}}} if m else {}},
                "offhand": {"by_affinity": {"Heavy": {"features": {"o": o, "l1_startup": o_su}}} if o else {}}}
    sroles = {"weapons": {"Slow S": sp(m=10, m_su=20), "Quick Q": sp(m=6, m_su=10),
                          "Big B": sp(o=10, o_su=22), "Knife K": sp(o=8, o_su=9)},
              "classes": {"Greatsword": {"main_hand": {"weapon": "Slow S", "affinity": "Heavy"}},
                          "Dagger": {"main_hand": {"weapon": "Quick Q", "affinity": "Heavy"}},
                          "Hammer": {"offhand": {"weapon": "Big B", "affinity": "Heavy"}},
                          "Knife": {"offhand": {"weapon": "Knife K", "affinity": "Heavy"}}}}
    speed = SetSpeed(sroles, lambda s: min(1.0, max(0.0, (20 - s) / 10)),
                     [{"right": "Quick Q", "left": "Quick R", "slots": {"dual_1": {"startup": 8}}}])
    pairs, _ = derive_combos(sroles, cr, {}, {}, usable=lambda w: True, n=2)
    check("speed: without the term every set takes the strongest off hand",
          [(p[2], p[3]) for p in pairs], [("Slow S", "Big B"), ("Quick Q", "Big B")])
    pairs, slog = derive_combos(sroles, cr, {}, {}, usable=lambda w: True, n=2, speed=speed, k=0.5)
    check("speed: a slow main hand buys its coverage back with a fast off hand",
          [(p[2], p[3]) for p in pairs], [("Slow S", "Knife K"), ("Quick Q", "Big B")])
    check("speed: each set's s_set and C_set logged", slog[0].endswith("s_set 9, C_set 1.000, value 2.7000"), True)
    pairs, wlog = derive_combos(sroles, cr, {}, {}, usable=lambda w: True, n=2, speed=speed, k=0.5,
                                weight=lambda w: 1.0, cost=flat)
    check("speed: the weight-capped path scores sets by the same value",
          ([(p[2], p[3]) for p in pairs], "s_set 9" in wlog[0]), ([("Slow S", "Knife K"), ("Quick Q", "Big B")], True))
    check("speed: a powerstance's off hand throws dual_1, a slow set covers nothing",
          [speed((1, "Dagger", "Quick Q", "Heavy"), (1, "Dagger", "Quick R", "Heavy")),
           speed((1, "Greatsword", "Slow S", "Heavy"), (1, "Hammer", "Big B", "Heavy"))],
          [(8, 1.0), (20, 0.0)])

    # `affinity_primaries`: summed over weapons with every stat at the build's highest; a weapon the
    # flat spread does not wield, or with no row on an affinity, is left out of that sum.
    fake = {("Axe A", "Heavy"): {"str": 200, "dex": 20}, ("Axe A", "Keen"): {"str": 60, "dex": 70},
            ("Axe A", "Blood"): {"str": 90, "dex": 30}, ("Axe A", "Sleep"): {"str": 10, "dex": 0},
            ("Katana K", "Heavy"): {"str": 150, "dex": 40}, ("Katana K", "Keen"): {"str": 40, "dex": 120},
            ("Katana K", "Blood"): {"str": 60, "dex": 80}, ("Katana K", "Sleep"): {"str": 10, "dex": 90},
            ("Big B", "Keen"): {"str": 900, "dex": 0}}
    req = {"Big B": 99}
    stat_fields = {s: (None, None, None, f"need_{s}") for s in ("str", "dex")}
    FAR = type("AR", (), {"STATS": ("str", "dex"), "AFFINITIES": ["Heavy", "Keen", "Blood", "Sleep", "Fire"],
                          "STAT_FIELDS": stat_fields})

    class Tab:
        def __init__(self):
            self.weapons = {k: {"reinforceTypeId": 0, "need_str": req.get(k[0], 0), "need_dex": 0} for k in fake}

        def find_weapon(self, w, aff):
            if (w, aff) not in fake:
                raise SystemExit("no row")
            return w, aff

        def max_level(self, _):
            return 25
    seen = []

    def fake_scaling(AR, t, w, aff, lv, st, two):
        seen.append(st["dex"])
        return fake[w, aff]
    prim, tot = affinity_primaries(FAR, Tab(), ["Axe A", "Katana K", "Big B"], {"str": 43, "dex": 16}, 25, fake_scaling)
    check("affinity primaries: measured per infusion over the weapons, at a flat spread, unwieldable rows left out",
          (prim, tot["Blood"], set(seen)),
          ({"Heavy": "str", "Keen": "dex", "Blood": "str", "Sleep": "dex"}, {"str": 150.0, "dex": 110.0}, {43}))
    prim = {"Heavy": "str", "Blood": "str", "Poison": "str", "Cold": "dex", "Keen": "dex"}
    rule = AffinityRule("Heavy", prim, infusable=lambda w: w != "Somber S")
    check("affinity rule: an off-affinity copy kept only when its infusion's primary stat is the pinned one's",
          [rule.ok(w, aff) for w, aff in (("Axe A", "Heavy"), ("Axe A", "Blood"), ("Katana K", "Poison"),
                                         ("Katana K", "Cold"), ("Katana K", "Keen"), ("Somber S", "Standard"),
                                         ("Axe A", "Occult"))],
          [True, True, True, False, False, True, True])
    check("affinity rule: no pinned affinity keeps everything",
          AffinityRule(None).ok("Katana K", "Cold"), True)
    check("affinity rule: drops counted per weapon and affinity",
          ([x["weapon"] for x in [{"weapon": "Katana K", "affinity": "Cold", "grip": "2H"},
                                  {"weapon": "Axe A", "affinity": "Blood", "grip": "1H"}] if rule.keep(x)],
           dict(rule.dropped)), (["Axe A"], {("Katana K", "Cold"): 1}))
    check("primary stat: none when nothing scales", primary_stat({"str": 0.0, "dex": 0.0}), None)

    # `stat_scaling` against `er-mechanics-ar.attack_rating` on the real regulation, when present.
    try:
        spec = importlib.util.spec_from_file_location("er_mechanics_ar", HERE / "er-mechanics-ar.py")
        AR = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(AR)
        tab = AR.Tables(None)
    except (SystemExit, OSError, KeyError) as e:
        print(f"skip stat_scaling vs attack_rating: no regulation ({e})")
    else:
        st = {"str": 43, "dex": 16, "int": 7, "fth": 8, "arc": 9}
        got = []
        for w, aff, two in (("Uchigatana", "Heavy", False), ("Uchigatana", "Cold", True),
                            ("Uchigatana", "Blood", False), ("Misericorde", "Keen", False)):
            sc = stat_scaling(AR, tab, w, aff, 25, st, two)
            r = AR.attack_rating(tab, w, aff, 25, st, two)
            got.append((round(sum(sc.values()), 3) == round(sum(d["scaling"] for d in r["damage"].values()), 3),
                        primary_stat(sc)))
        check("stat_scaling: per-stat parts add up to attack_rating's scaling; Heavy is STR, Keen DEX at STR 43 DEX 16",
              got, [(True, "str"), (True, "str"), (True, "str"), (True, "dex")])

    check("crit rank: best riposte + backstab first, a weapon with no wieldable affinity left out",
          [w for _, w, _ in rank_crit({"Misericorde": [("Keen", 300, 420)], "Club": [("Heavy", 500, 300)],
                                       "Rapier": [], "Dagger": [("Heavy", 350, 350)]})],
          ["Club", "Misericorde", "Dagger"])

    # Icon shields: three rows, one per share. Row 0 is a page (protected), row 1 can go (Axe and
    # Club both have copies in row 0), row 2 holds every Solo copy (the last-copy rule).
    hold = fight_hold_hp("invasion")

    def vs(w, v, theme="Classes"):
        return dict(mk(w, "Standard", "x", theme=theme), value=v)
    islots = ([vs("Axe", 900, "Page: Axe"), vs("Axe", 900, "Page: Axe"), vs("Club", 900, "Page: Axe"),
               vs("Club", 900, "Page: Axe"), vs("Axe", 900, "Page: Axe")]
              + [vs("Axe", 120), vs("Axe", 80), vs("Club", 100), vs("Club", 60), vs("Club", 40)]
              + [vs("Solo", 10)] * ROW)

    def prot(i):
        return islots[i]["theme"].startswith("Page")
    big = {"weapon": "Shield", "affinity": "Heavy", "ash": "No Skill", "guard_base": 0.7, "opening_hp": 400.0,
           "net_hp": 0.0}
    got = icon_shield_plan(islots, [big], 3, prot, hold)
    check("icon shields: a page and a weapon's last copies are never displaced; the free row goes to a "
          "0.7-guard shield, valued against its best copy (Axe 120/s)",
          [(d["row"], d["placed"], d.get("slot")) for d in got], [(None, False, None), (1, True, 5), (None, False, None)])
    check("icon shields: the shield's value is 0.7 x 400 HP saved per opening over the invasion model",
          round(got[1]["icon_hp"]), round(hold.defense(280.0)))
    tiny = dict(big, guard_base=0.001, opening_hp=10.0)
    check("icon shields: a shield worth less than the row it would displace is not placed",
          [(d["row"], d["placed"]) for d in icon_shield_plan(islots, [tiny], 3, prot, hold)],
          [(None, False), (1, False), (None, False)])
    check("icon shields: the hour-long tail decides it -- offense stops at the hosts' HP and flasks, defense "
          "keeps growing (the invasion's engaged attackers)",
          (hold.points[0], hold.points[-1], hold.offense(1e9) == hold.kill, hold.attackers,
           hold.defense(1.0) > hold.defense(0.5)),
          (10.0, 3600.0, True, _buffs().FIGHT_KINDS["invasion"]["attackers"], True))
    print("selftest:", "FAIL" if bad else "ok")
    return 1 if bad else 0


def mode_report(roles_path, all_mains=False, use_cross=True, ladder=True):
    """Each main hand's best set and its mode on one roles file (`er-mechanics-class-roles.py
    --json`), at its own weights and `[offhand_pick_weights]`, the cross-hand term read from
    `CROSS_HAND_CACHE` only (the rl with the most entries; an unmeasured link is none) and named
    with its link. No powerstance records. With `ladder` (the default) the duel ladder's bans
    apply as they do to a `--fight duel` build (`ladder_exclude`): a banned weapon is in neither
    hand and a banned grip is no mode; without it every weapon is usable. Prints one line per main
    hand and the count played two-handed alone."""
    cr = _class_roles()
    if ladder:
        set_exclude(ladder_exclude())
        print(f"ladder bans: {len(EXCLUDE['weapons'])} weapons, {len(EXCLUDE['rows'])} grip bans")
    roles = json.loads(Path(roles_path).read_text())
    prefs = tomllib.loads(PREFS.read_text())
    cache = json.loads(CROSS_HAND_CACHE.read_text()) if CROSS_HAND_CACHE.exists() else {}
    by = collections.defaultdict(dict)
    for k, v in cache.items():
        model, rl, r, l = k.split("|", 3)
        if model == CROSS_HAND_MODEL:
            by[rl][r, l] = v
    xs = by[max(by, key=lambda x: len(by[x]))] if by and use_cross else {}
    cross = (lambda r, l: tuple(xs[r, l]) if xs.get((r, l)) else None) if use_cross else None
    mains, offs_for = hand_scores(roles, cr, roles.get("weights") or cr.pick_weights(EXCLUDE["ashes"]),
                                  roles.get("off_weights") or prefs["offhand_pick_weights"],
                                  lambda w: w not in EXCLUDE["weapons"],
                                  lambda w, a: True, cross=cross, all_mains=all_mains)
    alone_n = 0
    for m in mains:
        v, _, o = best_pair([(m, o) for o in offs_for(m)], set_score)
        solo = next((x for x in offs_for(m) if x.alone), None)
        alone_n += o.alone
        mode = ("2H alone" if o.alone else "2H paired" if o.two_hand
                else f"1H + {o[2]}" + (" (powerstance)" if o.ps else ""))
        print(f"{m[3]} {m[2]}: {mode} {v:.4f} (2H alone {'-' if solo is None else f'{set_score(m, solo):.4f}'}, "
              f"cross {getattr(o, 'cross', 0.0):.4f}" + (f" {o.cross_link}" if getattr(o, "cross_link", None) else "")
              + ")")
    print(f"{alone_n} of {len(mains)} main hands two-handed alone")
    return 0


def main() -> int:
    if sys.argv[1:] == ["--selftest"]:
        return selftest()
    if sys.argv[1:2] == ["--mode-report"]:
        # `--mode-report <roles.json> [all] [nocross] [noladder]`: `mode_report`.
        return mode_report(sys.argv[2], "all" in sys.argv[3:], "nocross" not in sys.argv[3:],
                           "noladder" not in sys.argv[3:])
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
    ap.add_argument("--cross-hand-cache", type=Path, default=CROSS_HAND_CACHE,
                    help="each (right, left) pair's best cross-hand link and landing chance (`CrossHand`)")
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
                    help="with --build: a Ranged row (the ranged weapons the build wields that the "
                         "measured ranged score keeps, er-mechanics-shields-ranged.ranged_plan) and the four equipped ammunition names")
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
    ap.add_argument("--crit-weapon", default=None,
                    help="weapon of the crit row, on its best riposte and backstab affinity ('' for none; "
                         "default: the swept weapon with the highest one-handed crit, `rank_crit`)")
    ap.add_argument("--affinity",
                    help="the affinity the build is pinned to (the sweeps' --affinity): a copy on another "
                         "affinity is kept only when its primary damage stat is the pinned one's (`AffinityRule`)")
    ap.add_argument("--rank-terms", type=Path,
                    help="write each ash's best-value copy and the terms its `value` multiplies (JSON)")
    ap.add_argument("--throttle", action=argparse.BooleanOptionalAction, default=True,
                    help="stay off CPU 0 and under 90%% of any other core (er-mechanics-ash-interrupt.throttle)")
    ap.add_argument("--icon-shields", type=int, default=0,
                    help="at most this many rows of one Shields-row shield repeated, spread through the pages "
                         "as markers, each placed only where it beats the row it displaces over the invasion "
                         "fight model (`icon_shield_plan`)")
    ap.add_argument("--weight-cost", type=Path,
                    help="er-weapon-build.py's carried weight -> damage x EHP table (`WeightCost`); the "
                         "equip-set pairs then pay for their weight (`derive_combos`)")
    ap.add_argument("--arena-m", type=float, default=None,
                    help="the farthest two players get apart in this fight, m: a camouflage ash that "
                         "only hides past it gets no row (`out_of_arena`); default no bound")
    ap.add_argument("--weigh-only", action="store_true",
                    help="stop after the rows are weighed: print each `--terms-weapon` row's value per "
                         "stance button and joined (`merge_stance_buttons`) and its duel bands; --out is "
                         "not written")
    ap.add_argument("--exclude", type=Path,
                    help="JSON of weapons, ashes and [ash, affinity] pairs the build may not hold (`EXCLUDE`)")
    a = ap.parse_args()
    if a.exclude:
        set_exclude(json.loads(a.exclude.read_text()))
        print(f"exclude: {len(EXCLUDE['weapons'])} weapons, {len(EXCLUDE['ashes'])} ashes, "
              f"{len(EXCLUDE['ash_affinity'])} ash-on-affinity pairs ({a.exclude})", file=sys.stderr)
    if a.throttle:
        _ai().throttle(_ai().allowed_cpus()[:1])
    stage = Stages(a.stage_times)
    stage("rows")
    # The stage's counter, one step per whole-pool pass: each takes seconds with no item worth
    # counting (the sweep files, the embedding read twice), and a pass that computes counts its own.
    rows_step = Count(len(a.rows) + 7, "steps")
    # The sweep already drops blacklisted ashes; reading the list again keeps a sweep made before an
    # entry was added from bringing it back.
    blacklist = tomllib.loads(BLACKLIST.read_text())
    # Several sweeps (the build's Keen sweep, a Lightning sweep of the role winners, ...) are one
    # pool; a (weapon, affinity, ash, grip) row two sweeps both have is kept once, a stance skill's
    # once per button (`variant`) until `merge_stance_buttons` joins them.
    seen, rows = set(), []
    for path in a.rows:
        for x in json.loads(path.read_text()):
            k = (x["weapon"], x.get("affinity", "Standard"), x["ash"], x["grip"], x.get("variant"))
            if k not in seen and x["ash"] not in blacklist and not off_stat(x) and not excluded(x) \
                    and not out_of_arena(x, a.arena_m):
                seen.add(k)
                x.setdefault("affinity", "Standard")
                rows.append(x)
        rows_step()
    # A buff is worth what it adds on its carrier (`er-mechanics-ash-table.BUFF_MODEL`); a sweep
    # made before that is valued here, on the cast's clock, so before the grip change.
    build_stats = ({k: json.loads(a.build.read_text())["stats"][k] for k in STATS} if a.build
                   else {"str": 18, "dex": 80, "int": 7, "fth": 8, "arc": 9})
    spec = importlib.util.spec_from_file_location("er_mechanics_ar", HERE / "er-mechanics-ar.py")
    AR = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(AR)
    tables = AR.Tables(None)
    # `--affinity`: off-affinity copies whose primary damage stat is not the pinned affinity's go
    # before anything is weighed (`AffinityRule`, user 2026-10-18).
    aff_rule = AffinityRule(None)
    if a.affinity:
        prim, prim_tot = affinity_primaries(AR, tables, sorted({x["weapon"] for x in rows}), build_stats, a.upgrade)
        print("affinity primary stats (attack rating each stat adds, summed over the swept weapons, every "
              "stat at the build's highest): " + "; ".join(
                  f"{aff} {s.upper()} ({', '.join(f'{k} {v:.0f}' for k, v in sorted(prim_tot[aff].items(), key=lambda kv: -kv[1]) if v > 0)})"
                  for aff, s in prim.items()), file=sys.stderr)

        def infusable(w):
            try:
                tables.find_weapon(w, a.affinity)
                return True
            except (SystemExit, KeyError):
                return False
        aff_rule = AffinityRule(a.affinity, prim, functools.lru_cache(None)(infusable))
        rows = [x for x in rows if aff_rule.keep(x)]
        gone = collections.defaultdict(list)
        for (w, aff), k in sorted(aff_rule.dropped.items()):
            gone[aff].append(w)
        print(f"affinity {a.affinity}: primary stat rule dropped {sum(aff_rule.dropped.values())} rows: "
              + ("; ".join(f"{aff} on {len(ws)} weapons ({', '.join(ws[:6])}{', ...' if len(ws) > 6 else ''})"
                           for aff, ws in sorted(gone.items())) or "none"), file=sys.stderr)
    rows_step()
    set_lead_in(rows, build_stats)
    rows_step()
    set_buff_values(rows, build_stats, a.buff_cache, build_armor(a.build))
    rows_step()
    set_buff_weights(rows)
    buff_report(rows)
    rows_step()
    rows = drop_no_pvp_utility(rows)
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
    rows_step()
    r1 = r1_reaches(json.loads(a.roles.read_text()) if a.roles else None, a.embed)
    rows_step()
    traits = weapon_traits(a.embed)
    rows_step()
    got = weigh(rows, a, r1, build_stats, report=True, stages=stage)
    AIb = _ai()
    for w in a.terms_weapon if a.weigh_only else ():
        for x in (x for x in rows if x["weapon"] == w):
            print(f"button {x['affinity']} {w} {x['grip']} {x['ash']} {x.get('variant') or '-'}: value "
                  f"{value(x):.1f}, duel bands {AIb.band_set_value([x['by_distance']]) if x.get('by_distance') else 0:.2f}"
                  f" {x.get('by_distance')}")
            # The perfect-block term over the duel's bands against the fixed `REACH_SHARE` distance.
            prof = (got or {}).get(AIb.row_key(x))
            if "bmult" in x and prof and "side" in _PB:
                y = dict(x)
                _PB["side"].row(y, {k: v for k, v in prof.items() if k != "spread"}, x["score"], status_rate(x))
                print(f"perfect block {x['affinity']} {w} {x['grip']} {x['ash']} {x.get('variant') or '-'}: bmult "
                      f"{x['bmult']:.4f} over the bands {x.get('bmult_by')}, {y['bmult']:.4f} at the fixed "
                      f"{prof.get('D')} m")
    rows = merge_stance_buttons(rows)
    for w in a.terms_weapon if a.weigh_only else ():
        for x in (x for x in rows if x["weapon"] == w):
            print(f"joined {x['affinity']} {w} {x['grip']} {x['ash']}: value {value(x):.1f} (kept button "
                  f"{(x.get('stance') or '-').upper()} alone {value({**x, 'stance_w': 1.0}):.1f}), duel bands "
                  f"{AIb.band_set_value([x['by_distance']]) if x.get('by_distance') else 0:.2f}, "
                  f"stance_w {x.get('stance_w', 1.0)}, per band {x.get('stance_by_band')}")
    if a.weigh_only:
        return 0
    for x in rows:
        if x.get("stance_buttons"):
            print(f"stance buttons: {x['affinity']} {x['weapon']} {x['grip']} {x['ash']}: "
                  f"{'/'.join(b.upper() for b in x['stance_buttons'])}, kept {x['stance'].upper()}, x{x.get('stance_w', 1.0)}"
                  + (f", per band {' '.join(f'{n:g} m {b.upper()}' for n, b in x['stance_by_band'])}"
                     if x.get("stance_by_band") else ""), file=sys.stderr)
    # A duel's sets value their ashes per distance band (`SetScorer.ash_share`).
    ASH_BANDS.clear()
    ASH_BANDS.update(ash_bands(rows))
    ASH_BAND_LEFT_L2.clear()
    if ASH_BANDS:
        arts_t = _table_mod().A.AshTables()
        sids = skill_ids(arts_t, {x["ash"] for x in rows})
        ASH_BAND_LEFT_L2.update(ash_band_left_l2(
            rows, lambda n: arts_t.arts[sids[n]]["swordArtsTypeNew"] if n in sids else None))
        if ASH_BAND_LEFT_L2:
            print(f"Combos: {len(ASH_BAND_LEFT_L2)} carriers' skills take L2 from the left hand "
                  f"one-handed (c0000.hks GetSwordArtInfo): {sorted(ASH_BAND_LEFT_L2.items())}",
                  file=sys.stderr)
        print(f"Combos: {len(ASH_BANDS)} carriers valued per duel distance band for the sets' ash term",
              file=sys.stderr)
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
                  f"{x.get('buff_w', 1.0):.3f} reach_w {x.get('reach_w', 1.0):.3f} stance_w "
                  f"{x.get('stance_w', 1.0):.3f}", file=sys.stderr)
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
            "reactive_proxy": reactive_proxy(x), "whiff": x.get("whiff"), "self_cost": x.get("self_cost"),
            "string": x.get("string")} for n, x in top.items()}, indent=1))
    cut_rows = [x for x in rows if x["ash"] in cut]
    rows = [x for x in rows if x["ash"] not in cut]
    # The crit weapon's copies come from the rows before the role filter below, which drops a
    # weapon no class role picked; the weapon itself is chosen in the crit stage (`rank_crit`).
    crit_src = rows
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
    ref = category_refs(rows)
    print("norm scale per category (`category_refs`): " + ", ".join(f"{c} {v:.1f}" for c, v in ref.items()),
          file=sys.stderr)
    for x in rows + near_rows:
        x["norm"] = x["value"] / ref[x["cat"]]

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
        # A pick's affinity that `--affinity` rules out (`AffinityRule`) leaves the weapon's row
        # to the affinities the rule kept.
        for w in [w for w, aff in role_aff.items() if not aff_rule.ok_any(w, aff)]:
            print(f"role affinity: {role_aff.pop(w)} {w} dropped by the {a.affinity} primary stat rule",
                  file=sys.stderr)

    def class_copies(w):
        mine = [x for x in by_weapon[w] if x["affinity"] == role_aff.get(w, x["affinity"])]
        if w in role_aff and not mine:
            print(f"warning: no {role_aff[w]} {w} rows in the sweep; using every affinity", file=sys.stderr)
        return mine or by_weapon[w]

    def pair_pool(w):
        own = class_copies(w)
        util = top_distinct([x for x in own if x["cat"] == "utility"], PAIR_UTILITY)
        # A damaging copy valued at nothing (its multipliers took it to 0, Impaling Thrust on a
        # claw by `mh_w`) is not a pick: before 2026-10-19 such zeros filled slots on ties, in
        # sweep order.
        return [x for x in own if x["cat"] != "utility" and x["norm"] > 0] + util

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
    # The equip-set pairs are measured (`derive_combos`, user 2026-10-18), not the six class pairs
    # this list used to name; without `--roles` there is nothing to measure them on.
    combos, combo_ws = [], []
    if roles:
        cr = _class_roles()
        prefs = tomllib.loads(cr.PREFS.read_text())
        weighed = {}
        if a.weight_cost:
            def std_weight(w):
                return tables.weapons[tables.find_weapon(w, "Standard")]["weight"]
            # The Endure swap every set holds (`endure_swap`, chosen later): the copy it takes when
            # every set stays at medium roll, the best-valued one up to `ENDURE_MAX_WEIGHT`.
            swaps = sorted(((value(x), std_weight(x["weapon"])) for x in endure_rows
                            if std_weight(x["weapon"]) <= ENDURE_MAX_WEIGHT), reverse=True)
            # A `--focus` set (the focus weapon and its class's off-hand pick) is in the plan
            # whatever the pairs are, so its weight is paid for already.
            focus_w = 0.0
            for spec in a.focus:
                fw = spec.partition(":")[0]
                fc = CLASS.get(tables.weapons[tables.find_weapon(fw, "Standard")]["wepType"], "Other")
                off = ((((roles.get("classes") or {}).get(fc) or {}).get("offhand")) or {}).get("weapon")
                focus_w = max(focus_w, std_weight(fw) + (std_weight(off) if off and off in by_weapon else 0.0))
            weighed = {"weight": std_weight, "cost": WeightCost(json.loads(a.weight_cost.read_text())),
                       "carry": swaps[0][1] if swaps else 0.0, "floor": focus_w + (swaps[0][1] if swaps else 0.0)}
        # The speed term (`SetSpeed`): each set's fastest neutral attack against the PvP corpus's
        # openings (`er-mechanics-class-roles.PunishCoverage`, the same openings `r1_cover` is read
        # on); without `--embed` there are no frames and no term.
        if a.embed:
            emb_doc = json.loads(a.embed.read_text())
            cover = cr.PunishCoverage(emb_doc)
            weighed.update(speed=SetSpeed(roles, cover, emb_doc.get("powerstance") or ()), k=cr.punish_k())
            print(f"Combos: speed term k {weighed['k']} (REACT_SHARE, INFERRED) x punish coverage over "
                  f"{cover.n_builds} corpus builds, {len(cover.w)} openings", file=sys.stderr)
            # A pair that can powerstance is scored on its dual moveset (`PowerstanceOffs`).
            weighed["ps"] = powerstance_offs(emb_doc.get("powerstance") or (), tables, roles, cr)
            print(f"Combos: {len(weighed['ps'].pool)} powerstance candidates (wieldable, IsEnableDualWielding) "
                  f"in the off-hand pool", file=sys.stderr)
            del emb_doc
        # Each set is scored in the mode it is played in (`derive_combos`): roles without per-grip
        # main-hand features (written before 2026-10-19) cannot tell a two-handed set from a
        # one-handed one, and every set is then scored as before.
        if not any(v.get("by_grip") for rw in roles["weapons"].values()
                   for v in (((rw.get("main_hand") or {}).get("by_affinity")) or {}).values()):
            print("Combos: WARNING the roles carry no per-grip main-hand features (`by_grip`); every set is "
                  "scored on its best grip with an off hand, two-handed alone is never offered -- rerun "
                  "er-mechanics-class-roles.py", file=sys.stderr)
        # The cross-hand term (`CrossHand`): the build's RL sets the stagger corpus.
        if a.build:
            weighed["cross"] = CrossHand(cr.build_rl(json.loads(a.build.read_text())), a.mirror, a.cross_hand_cache)
        combos, combo_log = derive_combos(roles, cr, roles.get("weights") or cr.pick_weights(EXCLUDE["ashes"]),
                                          roles.get("off_weights") or prefs["offhand_pick_weights"],
                                          usable=lambda w: w in by_weapon, aff_ok=aff_rule.ok_any, progress=True,
                                          **weighed)
        if weighed.get("cross") is not None:
            weighed["cross"].save()
            print(f"Combos: cross-hand links measured for {weighed['cross'].new} new pairs (TRUE_COMBO_BONUS "
                  f"x landing chance, R1/R2 into L1 #1 and back; {a.cross_hand_cache})", file=sys.stderr)
        for line in combo_log:
            print(f"Combos: {line}", file=sys.stderr)
    else:
        print("Combos: no --roles, so no measured pairs and no equip sets from them", file=sys.stderr)
    for rc, lc, rw, lw in combos:
        combo_ws += [w for w in (rw, lw) if w is not None and w not in combo_ws]
    OFFHAND_PARTNERS.clear()
    OFFHAND_PARTNERS.update(offhand_partners(combos))
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
    def one_handed(x):
        """Whether a copy can be swapped to and used at once: its skill row is one-handed and the
        build meets every requirement one-handed. A block of repeats exists to be grabbed and used
        straight away, and a copy that must be two-handed first costs an extra input the swap does
        not expect, so it is a failed swap (user, 2026-10-18, of Waves of Darkness and Stormcaller
        pages)."""
        if x["grip"] != "1H":
            return False
        r = tables.weapons[tables.find_weapon(x["weapon"], x.get("affinity", "Standard"))]
        return all(r[f] <= build_stats[k] for k, f in REQUIREMENT_FIELDS.items())

    grabs = [x for x in grab_copies(best, by_weapon, mob, usable=one_handed) if x["ash"] not in MOBILITY_ASHES]
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
                "status_hp": x.get("status_hp") or {}, "grab": theme.startswith("Landmark"),
                # Read by `er-weapon-build.py`: a full-HP talisman this copy leaves worth nothing.
                **({"self_cost": x["self_cost"]} if x.get("self_cost") else {})}

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
    extra_sets, ammo, greatbow, icon_pool = [], {}, None, []

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
    # The crit row is swapped to after a parry, so a ruleset that leaves no parry skill legal
    # (`er-mechanics-class-roles.parry_share` of `--exclude`'s ashes) has no use for it.
    share = _class_roles().parry_share(EXCLUDE["ashes"])
    if a.crit_weapon is None and not share:
        print("Crit: no row, the ruleset leaves no parry skill legal", file=sys.stderr)
        crit_order, crit_grips = [], CRIT_GRIPS
    elif a.crit_weapon is None:
        # The swept weapon with the highest one-handed riposte plus backstab (`rank_crit`,
        # `CRIT_GRIPS`) on an affinity `--affinity` allows, rather than Misericorde by name (user,
        # 2026-10-18). A weapon whose copies cannot mount any swept ash has no row to give, so the
        # next one is taken.
        crit_ws = sorted({x["weapon"] for x in crit_src if not excluded(x)})
        crit_rank = rank_crit({w: crit_affinities(w, build_stats, CRIT_GRIPS, aff_rule.ok)
                               for w in counted(crit_ws, "weapons")})
        print("Crit: one-handed riposte + backstab, best per weapon: "
              + ", ".join(f"{w} {r[0][0]} {s:.0f}" for s, w, r in crit_rank[:8]), file=sys.stderr)
        crit_order = [w for _, w, _ in crit_rank]
        crit_grips = CRIT_GRIPS
    else:
        crit_order = [a.crit_weapon] if a.crit_weapon else []
        crit_grips = ("1H", "2H")
    crit_t = None
    for crit_weapon in crit_order:
        a.crit_weapon = crit_weapon
        crit_pool = [x for x in crit_src if x["weapon"] == crit_weapon]
        for x in crit_pool:
            x.setdefault("affinity", "Standard")
            x["value"] = value(x)
            x["norm"] = x["value"] / ref[x["cat"]]
        ranked = crit_affinities(a.crit_weapon, build_stats, crit_grips, aff_rule.ok)
        print(f"Crit: {a.crit_weapon} riposte + backstab per affinity: "
              + (", ".join(f"{aff} {rp:.0f}+{bs:.0f}" for aff, rp, bs in ranked) or "none wieldable"), file=sys.stderr)
        if ranked:
            aff, rp, bs = ranked[0]
            t = crit_t = crit_t or _table_mod().A.AshTables()
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
                break
            print(f"Crit: no {a.crit_weapon} copy in the sweeps mounts on {aff}", file=sys.stderr)

    stage("shields")
    if a.shields and build and main_w:
        rs = row_start["Combos", main_w]
        main_aff = slots[rs]["affinity"]
        copies, picks, summary = shield_copies(a, build, main_w, main_aff, r1, build_stats)
        copies = [c for c in copies if not excluded(c)]
        picks = {j: p for j, p in picks.items() if any(c["weapon"] == p["weapon"] for c in copies)}
        if copies and picks:
            # Shields only (a non-shield parry tool has no `guard_base`), each with the corpus's
            # mean opening HP on me (`ShieldModel.summary`) for `icon_saved_hp`.
            icon_pool = [dict(c, opening_hp=summary["opening_hp_mean"]) for c in copies if "guard_base" in c]
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
        copies = [c for c in copies if not excluded(c)]
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
        for p in counted(fun_picks, "picks"):
            w, aff = p["weapon"], p["affinity"]
            # `--fun-skip-held`: a weapon already somewhere above gets no extra copy for its fun
            # mechanic.
            if a.fun_skip_held and w in held_ws:
                continue
            # The roles' picks are not sweep rows, so `--exclude` is applied to them here too.
            if excluded(p):
                print(f"Fun: skipped {aff} {w} ({p['label']}): excluded", file=sys.stderr)
                continue
            # And `--affinity` (`AffinityRule`), for the same reason.
            if not aff_rule.ok(w, aff, p["grip"] if p.get("grip") in ("1H", "2H") else "1H"):
                print(f"Fun: skipped {aff} {w} ({p['label']}): the {aff} primary damage stat is not "
                      f"{a.affinity}'s", file=sys.stderr)
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

    # Page copies are grabbed like landmarks, so they must be usable one-handed too (`one_handed`).
    storm = max((x for x in rows if x["ash"] == "Stormcaller" and one_handed(x)), key=value, default=None)
    endure_w = next((w for w in mob if any(x["ash"] == "Endure" and one_handed(x) for x in by_weapon[w])), None)
    endure = max((x for x in by_weapon.get(endure_w, []) if x["ash"] == "Endure" and one_handed(x)),
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
    combo_sets = []          # the `derive_combos` sets, for `arrange_sets` at the set coverage stage
    for rc, lc, right, left_w in combos:
        rs = row_start["Combos", right]
        if left_w is None:
            # Two-handed, one copy (`combo_pair`): the left hand holds nothing, whether the weapon
            # plays a paired moveset or is two-handed alone.
            sets.append({"name": f"{rc}: {right} two-handed", "right": [rs], "left": [], "grip": "2H"})
            combo_sets.append(sets[-1])
            continue
        ls = row_start["Combos", left_w]
        if left_w == right:
            # Two copies of one weapon (`derive_combos`): the left hand wears another slot of it.
            ls = second_copy(slots, rs, rs, ROW)
            if ls is None:
                print(f"Set {rc} + {lc}: {right} x2 dropped, the plan holds one copy of {right}", file=sys.stderr)
                continue
        # One-handed with an off hand or powerstanced: the right hand is held one-handed.
        sets.append({"name": f"{rc} + {lc}: {right} + {left_w}", "right": [rs], "left": [ls], "grip": "1H"})
        combo_sets.append(sets[-1])
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

    def displace_protected(i, in_set):
        """A slot nothing may overwrite: an equip set's copy, a landmark or page, a shield, icon
        shield or ranged pick, a Quick row (kept for mobility) or a partner copy. Shared by the icon
        shields and `Partners`; the last-copy rule is each caller's."""
        t = slots[i]["theme"]
        return bool(i in in_set or slots[i].get("grab") or slots[i].get("partner")
                    or t.startswith(("Page", "Landmark", "Shields", "Icon shields", "Ranged", "Quick")))

    # Icon shields (user, 2026-10-18: "for the invader build... we want 1-3 rows of icon shields
    # interspersed in the pages"; then "including icon shields as rows shouldn't be just up to me,
    # it should also be weighted by value based on the potential length of invasions being an
    # hour"). Up to `--icon-shields` rows, one per even share of the plan, each a Shields-row shield
    # repeated over the cheapest displaceable row of its share, and only while the shield is worth
    # more over the invasion model than that row (`icon_shield_plan`, `fight_hold_hp`).
    if a.icon_shields and icon_pool:
        hold = fight_hold_hp(ICON_FIGHT)
        in_set = {i for s in sets for hand in ("right", "left") for i in s[hand]}
        plan_ = icon_shield_plan(slots, icon_pool, a.icon_shields, lambda i: displace_protected(i, in_set), hold)
        print(f"Icon shields: {ICON_FIGHT} model, {len(hold.points)} lengths {hold.points[0]:.0f}..{hold.points[-1]:.0f} s, "
              f"{hold.attackers} attackers, offense capped at {hold.kill:.0f} HP (the hosts' HP and flasks)",
              file=sys.stderr)
        placed_at = []
        for d in plan_:
            c = d["copy"]
            what = (f"{c['affinity']} {c['weapon']} {c['ash']}: {icon_saved_hp(c):.0f} HP saved per opening "
                    f"-> {d['icon_hp']:.0f} HP per fight")
            if d["row"] is None:
                print(f"Icon shields: share {d['k'] + 1}: none, {what}; no displaceable row in rows "
                      f"{d['rows'][0] + 1}..{d['rows'][1]}", file=sys.stderr)
                continue
            g = slots[d["slot"]]
            vs = (f"{g['affinity']} {g['weapon']} {g['skill']} {g['value']}/s -> {d['row_hp']:.0f} HP per fight "
                  f"(best copy of page {d['row'] // (PAGE // ROW) + 1} row {d['row'] % (PAGE // ROW) + 1}, {g['theme']})")
            if not d["placed"]:
                print(f"Icon shields: share {d['k'] + 1}: none, {what} <= {vs}", file=sys.stderr)
                continue
            at, theme = d["row"] * ROW, f"Icon shields: {c['weapon']}"
            for j in range(at, at + ROW):
                s = slot(c, c["weapon"], theme, d["row"])
                s["note"] = c.get("note", "")
                slots[j] = s
            # The section the row was taken from carries on after it as "(continued)".
            cur = max((e for e in layout if e[1] <= at), key=lambda e: e[1], default=None)
            layout = [e for e in layout if e[1] != at]
            if cur and at + ROW < len(slots) and not any(st == at + ROW for _, st in layout):
                base = cur[0].split(" (continued)")[0]
                layout.append((base if cur[1] == at else base + " (continued)", at + ROW))
            layout.append((theme, at))
            layout.sort(key=lambda e: e[1])
            placed_at.append(at)
            print(f"Icon shields: share {d['k'] + 1}: placed, {what} > {vs}", file=sys.stderr)
        print(f"Icon shields: {len(placed_at)} of at most {a.icon_shields} rows, at slots {sorted(placed_at)}",
              file=sys.stderr)
    elif a.icon_shields:
        print("Icon shields: none, the plan has no Shields row to take them from", file=sys.stderr)

    # Partners (`Partners`): a weapon that performs best with another beside it gets that other
    # within `PARTNER_ROWS` rows. When none is there, the partner's best copy takes the lowest-value
    # slot of the weapon's row that is not an equip set's, a landmark or page, a shield or ranged
    # pick, or the last copy of its weapon.
    stage("partners")
    if a.embed:
        P = Partners(json.loads(a.embed.read_text()), tables)
        in_set = {i for s in sets for hand in ("right", "left") for i in s[hand]}

        def fixed_slot(i):
            # A partner copy placed by this pass stays, and neither it nor the Quick rows (steps and
            # Endure, kept for mobility) are given partners of their own.
            return displace_protected(i, in_set)

        def partner_copy(w, aff, kind):
            if kind == "offhand":
                ws = list(OFFHAND_PARTNERS.get(w, ()))
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
        for i in counted(range(len(slots)), "slots"):
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

    # Set coverage (user, 2026-10-19): every melee weapon in the plan is in at least one equip set,
    # its best one (`cover_sets`), and the sets' copies are laid out together so each weapon sits
    # beside its partners (`arrange_sets`). The planner caps neither the number of sets nor of
    # copies (its set editor pushes a new set unconditionally; only a set name must be under 48
    # characters), so a weapon's best partner the plan does not hold is added, one copy.
    stage("set coverage")
    if roles and (combo_sets or any(s["weapon"] in roles["weapons"] for s in slots)):
        def melee(w):
            try:
                c = CLASS.get(tables.weapons[tables.find_weapon(w, "Standard")]["wepType"])
            except (KeyError, SystemExit):
                return False
            rw = roles["weapons"].get(w) or {}
            return c is not None and any((rw.get(h) or {}).get("by_affinity") for h in ("main_hand", "offhand"))
        held_before = {s["weapon"] for s in slots}
        plan_ws = list(dict.fromkeys(s["weapon"] for s in slots if melee(s["weapon"])))
        sv_ = _set_value(weighed.get("speed"), weighed.get("k", 0.0))
        cost_, wfn, carry_ = weighed.get("cost"), weighed.get("weight"), weighed.get("carry", 0.0)

        def set_weight(m, o):
            return sum(wfn(w) for w in set_weapons(m, o)) + carry_
        # The stats are bought for the heaviest `derive_combos` set (or the focus set); a covering
        # set heavier than that pays `WeightCost` for the difference, as `_weighed_combos` does.
        heavy = (max([weighed.get("floor", 0.0)] + [sum(wfn(w) for w in (rw, lw) if w) + carry_
                                                     for _, _, rw, lw in combos]) if cost_ else 0.0)
        base = cost_(heavy) if cost_ else 1.0

        def set_value(m, o):
            v = sv_(m, o)[0]
            return v * cost_(max(heavy, set_weight(m, o))) / base if cost_ and base > 0 else v
        # The stage's counter: every candidate hand scored, then every main hand's off hands, then
        # every plan weapon covered; a candidate that is no main hand (unusable, no grip) is
        # skipped past so the counter ends at N/N.
        n_main, n_off = hand_picks(roles, all_mains=True)
        count = Count(n_main + n_off + n_main + len(plan_ws), "weapons")
        hands_all = hand_scores(roles, cr, roles.get("weights") or cr.pick_weights(EXCLUDE["ashes"]),
                                roles.get("off_weights") or prefs["offhand_pick_weights"],
                                lambda w: w in by_weapon, aff_rule.ok_any,
                                weighed.get("ps"), weighed.get("cross"), all_mains=True, count=count)
        count.skip(n_main - len(hands_all[0]))
        best_of, cov = cover_sets(hands_all, plan_ws, set_value, fallback=lambda m, o: sv_(m, o)[0], count=count)
        if weighed.get("cross") is not None:
            weighed["cross"].save()
        for w in plan_ws:
            if w not in best_of:
                print(f"Set coverage: {w} has no set (no usable partner or grip)", file=sys.stderr)
        # Nodes are (weapon, affinity): a set was scored at its hands' affinities.
        combo_nodes = [tuple((slots[i]["weapon"], slots[i]["affinity"]) for i in s["right"][:1] + s["left"][:1])
                       for s in combo_sets]
        new_sets, covers_of = [], {}
        for v, m, o, covers in cov:
            nodes = ((m[2], m[3]),) if o.two_hand else ((m[2], m[3]), (o[2], o[3]))
            same = next((k for k, n in enumerate(combo_nodes) if tuple(w for w, _ in n) == set_weapons(m, o)), None)
            if same is not None:
                covers_of.setdefault(same, []).extend(covers)
                continue
            new_sets.append((nodes, v, m, o, covers))
        order, at = arrange_sets(combo_nodes + [n for n, *_ in new_sets])

        def as_slot(x, w):
            # A sweep row outside the ranked pool carries no `norm` or `area` (as the Fill rows).
            x = dict(x, affinity=x.get("affinity", "Standard"), value=value(x))
            x.setdefault("norm", 0.0)
            x.setdefault("area", 0.0)
            return slot(x, w, "", 0)

        def copy_of(w, aff):
            have = [s for s in slots if s["weapon"] == w and s["affinity"] == aff and s.get("cat") != "utility"]
            if have:
                return dict(max(have, key=lambda s: s.get("value") or 0))
            rows_ = [x for x in by_weapon.get(w, []) if x.get("affinity", "Standard") == aff and not excluded(x)]
            rows_ = [x for x in rows_ if x["cat"] != "utility"] or rows_
            if rows_:
                return as_slot(max(rows_, key=value), w)
            any_ = [x for x in by_weapon.get(w, []) if not excluded(x)] or by_weapon[w]
            print(f"Set coverage: no {aff} {w} copy, taking its best other affinity", file=sys.stderr)
            return as_slot(max(any_, key=value), w)
        theme = "Sets (each weapon beside its equip-set partners)"
        layout.append((theme, len(slots)))
        start = len(slots)
        for w, aff in order:
            s = copy_of(w, aff)
            s.update(theme=theme, row=len(slots) // ROW, note="equip-set copy")
            s.pop("partner", None)
            slots.append(s)
        pad = -len(slots) % ROW
        for _ in range(pad):
            s = dict(slots[start])
            s.update(row=len(slots) // ROW, note="padding to a whole row")
            slots.append(s)
        swap_refs = next((s["right"][1:] for s in combo_sets if len(s["right"]) > 1), [])
        for k, s in enumerate(combo_sets):
            s["right"][0] = start + at[k][0]
            if len(at[k]) > 1:
                s["left"] = [start + at[k][1]]
            s["covers"] = covers_of.get(k, [])
        for k, (nodes, v, m, o, covers) in enumerate(new_sets):
            pos = at[len(combo_sets) + k]
            mode = ("two-handed alone" if o.alone else "two-handed, paired moveset" if o.two_hand
                    else "powerstance" if o.ps else "one-handed with an off hand")
            sets.append({"name": f"{m[2]} two-handed" if o.two_hand else f"{m[2]} + {o[2]}",
                         "right": [start + pos[0]] + swap_refs, "left": [start + p for p in pos[1:]],
                         "grip": o.grip, "mode": mode, "cover": True, "covers": covers, "value": round(v, 4),
                         **({"weight": round(set_weight(m, o), 1)} if cost_ else {})})
        added = sorted({w for w, _ in order} - held_before)
        print(f"Set coverage: {len(plan_ws)} melee weapons, {len(best_of)} covered by {len(cov)} best sets "
              f"({len(cov) - len(new_sets)} of them `derive_combos` sets); {len(new_sets)} sets added, "
              f"{len(order)} set copies + {pad} padding in rows {start // ROW + 1}..{len(slots) // ROW}, "
              f"{len(order) - len(set(order))} second copies, {len(added)} weapons new to the plan"
              + (f": {', '.join(added)}" if added else ""), file=sys.stderr)
        for s in combo_sets + sets[len(sets) - len(new_sets):]:
            idx = s["right"][:1] + s["left"]
            d = abs(idx[0] - idx[1]) if len(idx) > 1 else 0
            print(f"Set {s['name']} ({s.get('mode') or s.get('grip')}): slots {', '.join(map(str, idx))}, "
                  f"{d} apart; best set of {', '.join(s.get('covers') or []) or 'none'}", file=sys.stderr)

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
    for p in counted(range(0, len(slots), PAGE), "pages"):
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
