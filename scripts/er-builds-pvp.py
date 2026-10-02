#!/usr/bin/env python3
"""Per-attack PvP numbers for the grease sweep's best build of every weapon at one rune level.

    python3 scripts/er-builds-pvp.py --rl 150
    python3 scripts/er-builds-pvp.py --rl 150 --sort startup --slot r1_1
    python3 scripts/er-builds-pvp.py --rl 150 --weapon Giant-Crusher --all-slots
    python3 scripts/er-builds-pvp.py --rl 150 --summary            # per-weapon type mix + averages
    python3 scripts/er-builds-pvp.py --rl 150 --slot 2h_crouch_r1 --sort counter --spear-talisman
    python3 scripts/er-builds-pvp.py --selftest

`er-builds-optimize.py` scores a weapon by one motion-value-100 hit, which ranks every weapon as
if its attacks were the same attack. This joins each weapon's actual attacks
(`scripts/er-mechanics-attacks.py`) into the same damage model, and adds what one hit hides:

* Motion value, per element (`AtkParam_Pc.atk*Correction`), applied to AR before defense.
* The physical damage type, which picks the physical absorption the hit meets:
  `AtkParam_Pc.atkAttribute` 0 slash, 1 strike, 2 pierce, 3 standard; 253 takes the weapon's
  `atkAttribute` and 252 its `atkAttribute2` (`VERIFIED`, EXE resolver 0x140685a90 / 1.17.1
  0x1406868e0, see `er-mechanics-attacks.resolve_phys_type`). Affinity, grease and buffs do not
  change it (docs/er-mechanics/defense.md section 2a).
* The defender is the RL window's PvP corpus, not one build. Every attack is scored against each
  PvP build of the window (its planner-computed defense and per-type absorption) and `dmg` is the
  mean over them; `med` is the same hit on one synthetic defender holding the median of every
  column, which is what this tool reported before.
* Counter hits: a hit on a player inside their attack's counter frames is multiplied by the
  defender's stateInfo-110 SpEffect cut rate for the hit's damage type (`VERIFIED`, defense.md
  section 2b). Player weapon attacks apply SpEffect 45 there, which is 1.15 for pierce and 1.0
  for every other type, so `ctr` differs from `dmg` for pierce hits only. `--spear-talisman`
  multiplies that factor by Spear Talisman's `physicsAttackRate` 1.15 (EXE 0x1404f5310).
  `ctrW` is the attack's own exposure: how many frames it carries SpEffect 45 itself.
* The player-vs-player factors: the weapon's `vsPlayerDmgCorrectRate_*` and the attack's
  `FinalDamageRateParam[finalDamageRateId]` (defense.md section 3, EXE). That row's `saRate`
  multiplies poise damage between players (0x140486bf0), so PvP poise damage is far above the
  PvE 51-poise breakpoint (see `slot_hit`).
* Startup (first active frame), active frames, hyperarmor window and stamina cost, from the TAE
  at 30 fps (`INFERRED` rate; play-speed changes are not modelled).
* Recovery: the frame the same button can start the next attack, and the frame a roll can start,
  from the TAE cancel windows `er-mechanics-attacks.py` resolves (attacks.md section 4). Whether
  the behavior script adds conditions of its own is not traced.

Per slot, from the sibling mechanics modules (their functions, not a copy of their math; see
`Mechanics`):

* `status`: the weapon's own status (`er-mechanics-status.status_expected`) as an expected value
  over every PvP build of the window (`Defenders`: each build's planner resistance, talismans and
  armor included, and whether it carries the matching bolus), by engagement: the slot's hit plus
  the follow-ups that are true combos (frame-advantage `combos`, weighted by the stagger share);
  then the defender disengages. A bolus carrier refills the gauge and ends an active poison, rot
  or frost before the next engagement; anyone else keeps the gauge, which refills over
  `ENGAGEMENT_SECONDS`. While a proc is live the next row of that status is refused, so nothing
  builds and nothing refreshes. A proc is credited up to its expiry, the carrier's cure or the end
  of the fight, whose engagements are the landed-hit schedule (`Mechanics.set_fight`: one count
  per fight point, the HP and flasks to empty at most what the fight length leaves room for, the
  fight kinds mixed by planner tag). Its `hp_per_hit` (credited proc HP over the fight / landed hits)
  enters the score. The greases this sweep uses carry no status.
* `--talismans` with an exultation: the slot's damage is scaled by the share of its hits inside
  the buff its own procs start (`exultation_factor`); nothing an attacker wears raises build-up.
* `reach`: metres from the start position to the farthest hit point at the hit frame
  (`er-mechanics-reach.reach_summary` `world_reach_m`). A slot with no pose measurement takes
  its weapon class's median (`er-mechanics-reach.class_fallback`, `INFERRED`, shown with an
  `i`), and so does a missing coverage factor; the exchange's strike frame does the same.
* `adv` / `advS`: frame advantage on hit from the first hit frame
  (`er-mechanics-frame-advantage.slot_profile`), with the defender's poise holding / broken.
* `parry`: the slot's animation opens JumpTable 5 (`er-mechanics-crits.parry_exposure`).
* Per weapon, `skill`: the weapon's own skill, its best single hit on the median defender and its
  FP cost (`er-mechanics-ashes.skill_hits` + `pvp_damage`). The scored skill term is below.

`--sort score` ranks each weapon by its moveset score (`er-mechanics-moveset.moveset_score`): each
opener family (R1, R2, movement attacks) is scored by its best engagement, an opener plus its
true-combo follow-ups, and the families are combined with use shares proportional to score
(docs/er-mechanics/moveset.md). Each slot inside it is scored as:

    score = (dmg + SCORE_STATUS_WEIGHT * expected status HP per use
             + SCORE_CRIT_WEIGHT * crit HP - (parry HP if parryable else 0)) / commit * SCORE_FPS
            * clamp((reach / SCORE_REACH_REF_M) ** SCORE_REACH_EXP, SCORE_REACH_CLAMP)
            * coverage (`er-mechanics-reach.coverage_factor`: swing arc plus late turn, `INFERRED` weights)
            * (1 + SCORE_ADV_WEIGHT * clamp(expected advantage, +-SCORE_ADV_SPAN) / SCORE_ADV_SPAN)
            * (1 + SCORE_STAGGER_WEIGHT * stagger share)
            * guard factor (`er-mechanics-powerstance-guard.guard_score_factor`: chip, stamina
              drain net of guard regen over the slot's cycle, guard break and repel punish against
              the window's blockers, with the attacker's staminaAttackRate from talismans and
              buffs; and the configuration's own guard: a 2H weapon's, a 1H row's best corpus
              shield, 0 with a weapon in the left hand)
            * f_exchange * f_stamina (`er-mechanics-exchange.slot_exchange`, below)

`crit HP` and `parry HP` are per exchange (`er-mechanics-crits.weapon_crit`): the weapon's riposte
and backstab by how often the corpus hands out those openings, and the corpus's riposte by how
often a parryable attack meets a parry tool. They replace a flat parryable-slot factor of 0.85,
which scaled with the attack's own damage and overstated the cost about threefold
(docs/er-mechanics/crits.md).

`commit` is the first real frame a roll or the same button is free; expected advantage is `adv`
and `advS` weighted by the stagger share. Jump attacks and the guard counter are scored but never
picked as the best slot (`SCORE_BEST_SLOT_EXCLUDED`): their clip does not start where the
commitment does. Running, rolling, backstep and crouch attacks add the frames it takes to get into
them (`SCORE_ENTRY_FRAMES`) to their commitment. Every weight is `INFERRED`: a modelling choice, not game
data. The factors come from the modules; the weights only say how much each is worth.

Section 16 of ashes-of-war.md changes the damage term: `dmg` and status HP are multiplied by
the hit's worth, `(1 - REACT_SHARE) x f_contest + REACT_SHARE x` the share that lands on a defender
who waits and dodges on reaction (`er-mechanics-ashes.slot_reaction`), and the punish an evaded
attack eats (`whiff_hp`) is subtracted like parry HP. `f_contest` is the exchange's `f_exchange`,
or with `--interrupt` the interrupt module's `f_interrupt` for the openers it covers
(docs/er-mechanics/interrupt.md section 6); it then leaves the product, where only `f_stamina`
stays. `--no-react` restores the old form (the contest multiplies the whole score).

and, at the weapon level:

    weapon score = moveset score + SKILL_WEIGHT * max over mountable skills of share x max(0, skill - moveset)

* `f_exchange` x `f_stamina` (`er-mechanics-exchange.slot_exchange`, weight `SCORE_EXCHANGE_WEIGHT`,
  window `SCORE_STAMINA_WINDOW_S`): the slot started on the same frame as each R1 of the window's
  PvP builds, won / lost / traded through poise and hyperarmor; and `f_stamina` = N(W) x commit /
  W, the share of a W-second window the slot can keep swinging, N(W) the swings the median bar
  plus regeneration (45/s, paused by TAE 225 windows) pays for in W (docs/er-mechanics/exchange.md
  section 3c). A rolling or backstep attack gets a
  neutral exchange (its invincibility is not modelled); a crouch or running attack is exchanged
  from standing, its `SCORE_ENTRY_FRAMES` added to its first hit, and still pays that entry in its
  commitment. `--no-exchange` leaves them out.
* Buffs (`er-mechanics-buffs.py`, docs/er-mechanics/buffs.md section 10): every hit carries the
  expected factors of the buff kits `SCORE_BUFF_ARCHETYPE` PvP builds attack with (great rune by
  role, tears, consumables, with uptime over the fight) and of the kits every PvP build defends
  with, plus the buff rows of the skills the corpus mounts on the weapon, weighted by their
  probability. Greases stay the sweep's; on a greased build a skill's weapon buff (the same slot)
  is dropped. A buff's `change*Point` scaling-rate add (Roar / War Cry: +5 STR rate) is run
  through the weapon's own AR (`ar_stat_ratio`, attack-rating.md section 7). `--no-buffs` leaves
  them out.
* Skill (`er-mechanics-ashes.skill_term`, docs/er-mechanics/ashes-of-war.md sections 13-15): every
  skill the weapon can carry as built (`mountable_skills`: its own, plus each ash `can_mount`
  accepts at the build's affinity and level) is scored as a slot, with its commitment, stagger,
  parry exposure, frame advantage from its last hit, the weapon's crit terms and own-guard term,
  and its own reach and coverage (`skill_reach_factors`: the skill TimeAct's pose and footprint,
  or the bullet model for projectiles); blocker pressure stays neutral. Its damage is the hits that
  land (`skill_landing`: each hit's own shapes or bullets against a defender pushed back by the
  earlier ones, and only while the damage animation they started keeps him from rolling or
  guarding), with the every-hit number kept as `dmg_all` / `score_all`. `share` is the casts one
  median FP bar pays for over the fight's landed hits. The ash slot is worth its best option; a
  skill weaker than the moveset adds nothing. The section 13 term (what the corpus mounts,
  probability p) is kept as `value_corpus` and the `skC` column, not scored. A skill with no
  scored hit (a dodge, a defensive buff, a parry) is valued on the best opener's engagement
  (`skill_engagement`) as HP per engagement against the pool's own R1 strings (section 14 of that
  doc), so the term is computed after every row exists. `--no-skill` leaves it out.

The build per weapon is the grease sweep's winner at this RL
(`~/.cache/er-build-planner/grease-sweep-<tier>-<lo>-<hi>.jsonl`). The sweep covers every weapon
it can build: greasable infusables, ungreasable ones and fixed-affinity uniques (the row's
`kind`). A fixed-affinity row has no greased or quality configuration, so `--greased` skips it.
Each row also carries the sweep's equip-weight model (`weight`): `weight.factor` multiplies every
slot score as `f_weight`, and `weight.fit` is shown as a column only. The PvP corpus is the scraped planner builds with `isPvE` false, plus
builds with no `isPvE` value that carry a PvP tag (`PVP_TAGS`); `isPvE` true is left out, because
the planner computes those builds' absorption against monsters (`defEnemyDmgCorrectRate`).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
CACHE = Path.home() / ".cache/er-build-planner"


def _sibling(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_CACHED: dict = {}


def _sibling_cached(name: str):
    """A sibling module loaded on first use and kept (the flag-gated terms' modules)."""
    if name not in _CACHED:
        _CACHED[name] = _sibling(name)
    return _CACHED[name]


OPT = _sibling("er-builds-optimize")
ATK = _sibling("er-mechanics-attacks")
MOVESET = _sibling("er-mechanics-moveset")
GUARD = _sibling("er-mechanics-powerstance-guard")
EXCH = _sibling("er-mechanics-exchange")
INTR = _sibling("er-mechanics-interrupt")
NEUT = _sibling("er-mechanics-neutral")
AR, DEF, PR, EMBED = OPT.AR, OPT.DEF, ATK.PR, OPT.EMBED

ELEMENTS = OPT.ELEMENTS
PHYS_TYPES = ("slash", "strike", "pierce", "standard")
#: Planner `computed.absorption` key per damage type; its `physical` row is standard (neutral).
ABSORB_KEY = {"slash": "slash", "strike": "strike", "pierce": "pierce", "standard": "physical",
              "magic": "magic", "fire": "fire", "lightning": "lightning", "holy": "holy"}
MV_KEY = {"physical": "mv_phys", "magic": "mv_mag", "fire": "mv_fire", "lightning": "mv_light", "holy": "mv_holy"}
VS_PLAYER = {"physical": "Physics", "magic": "Magic", "fire": "Fire", "lightning": "Thunder", "holy": "Dark"}
FINAL_RATE = {"physical": "physRate", "magic": "magRate", "fire": "fireRate", "lightning": "thunRate",
              "holy": "darkRate"}
#: Planner tags that mark a build as made for player fights.
PVP_TAGS = {"Invasions", "Duels", "Co-op/Gank", "2v2", "Ladder", "Fishing"}
#: The counter-frame SpEffect player weapon attacks apply (TAE event 66 Args[0] in 5940 of the
#: 5976 counter events across the player TAEs; the other 30 are SpEffect 31 in `a938`).
COUNTER_SPEFFECT = 45
SPEAR_TALISMAN_SPEFFECT = 320600
#: Internal poise units are menu units / 10 (docs/er-mechanics/attacks.md section 2).
POISE_MENU = 10.0
PERCENTILES = (10, 25, 50, 75, 90)
SORTS = ("damage", "median", "counter", "startup", "poise", "stagger", "per-frame", "roll", "score")

#: Combined score weights (`--sort score`, see the module docstring). Every one of them is a
#: modelling choice, `INFERRED`: nothing in the game weighs reach against frame advantage.
SCORE_FPS = 30.0              # frames per second, so the base term is damage per second
SCORE_REACH_REF_M = 2.5       # reach that leaves the damage rate unchanged
SCORE_REACH_EXP = 0.5         # reach factor = (reach / SCORE_REACH_REF_M) ** SCORE_REACH_EXP
SCORE_REACH_CLAMP = (0.6, 1.5)
SCORE_ADV_SPAN = 30.0         # expected frame advantage is clamped to +-SCORE_ADV_SPAN
SCORE_ADV_WEIGHT = 0.25       # advantage factor = 1 + weight * clamped advantage / span
SCORE_STAGGER_WEIGHT = 0.5    # stagger factor = 1 + weight * share of the corpus one hit staggers
SCORE_STATUS_WEIGHT = 1.0     # status proc HP per hit is added to damage at this weight
SCORE_CRIT_WEIGHT = 1.0       # the weapon's expected crit HP per exchange is added at this weight
# The exchange and stamina factors (`er-mechanics-exchange.slot_exchange`, docs/er-mechanics/
# exchange.md). The weights live in that module, which computes the factor; these names re-export
# them so every score weight can be read here. `INFERRED`, like the rest.
SCORE_EXCHANGE_WEIGHT = EXCH.EXCHANGE_WEIGHT   # f_exchange = 1 + weight * (P(win) - P(loss))
SCORE_STAMINA_WINDOW_S = EXCH.FIGHT_WINDOW_S  # f_stamina = N(W) * commit / W (exchange.md section 3c)
# The skill term (docs/er-mechanics/ashes-of-war.md section 13): the weapon score gains
# `er-mechanics-ashes.SKILL_WEIGHT` x the skill's option value over the moveset score. The buff
# term (docs/er-mechanics/buffs.md section 10) averages the buff kits of this planner tag's PvP
# builds as the attacker's, and every PvP build's as the defender's. Both `INFERRED` choices.
SCORE_BUFF_ARCHETYPE = "Strength"
#: Slots scored but never picked as a weapon's best: their animation does not start where the
#: commitment starts. A jump attack's clip is the landing (`Jump_LandAttack_*`), after the
#: airtime, and a guard counter needs a blocked hit first.
SCORE_BEST_SLOT_EXCLUDED = ("jump_r1", "jump_r2", "counter")
#: Frames spent before a slot's own clip can start, added to its commitment. Roll, backstep and
#: crouch are the first frame the R1/R2 JumpTable input and cancel overlap in the `a000` clip that
#: leads into the attack (medium load, the sweep's `--roll medium`): Medium Roll 027110 frame 20,
#: Medium Backstep 027000 frame 14, Standing Crouch 390000 frame 8, all `VERIFIED` TAE and matching
#: the Nyasu frame data. The sprint is `INFERRED`: HKS picks the running attacks when
#: `MoveSpeedIndex` is 2 (Smithbox `c0000.hks` line 6273), and the dodge-hold time that makes the
#: engine set it is not traced, so the medium roll's 20 stands in for it.
#: A jump attack's swing starts at the press, 6 frames after the jump input at the earliest (er-mechanics-jump.takeoff, TAE).
SCORE_ENTRY_FRAMES = {"roll_r1": 20.0, "bstep_r1": 14.0, "crouch_r1": 8.0, "run_r1": 20.0,
                      "run_r2": 20.0, "jump_r1": 6.0, "jump_r2": 6.0}


#: `--timing-mixup` parts (`er-mechanics-timing-mixup`, moveset.md section 9): `crouch`, a crouch
#: R1 thrown from a held crouch (no entry at the engagement, crouch-speed approach); `entry`, a
#: rolling or backstep attack the waiting defender may also anticipate from its entry. Empty
#: (off) by default.
TIMING_MIXUP: frozenset = frozenset()
#: `--sustain` (buffs.md section 11): None (off), or fn(landed HP per engagement) -> the share of
#: it that counts toward the kill once the defenders' healing over time is in their HP.
SUSTAIN = None
#: `--multi-hit-escape`: a later hitbox of a multi-hit attack lands on a defender the first hit
#: did not stagger only if he cannot roll before it (`extra_hit_land`). Off by default.
MULTI_HIT_ESCAPE = False
#: Hook for a per-hit proc-opening term (a status proc that stuns the victim and opens a free
#: hit, e.g. frostbite): None (off), or fn(slot dict) -> HP-equivalent per landed hit, added to
#: the slot's status HP in `slot_score`. The slot carries its `status` (`er-mechanics-status.
#: status_expected` per status). `--proc-opening` loads `slot_proc_opening` from
#: `er-mechanics-proc-opening.py`, which is owned by that model, not by this script.
PROC_OPENING = None
#: `--setup` (combo.md section 11a): the left hand's weapon-buff category. Buffs.md section 3:
#: 162 is the right hand's slot and 163 the left's, and the add path clashes only within one
#: category, so one buff per hand coexists (`VERIFIED`, 0x1404fc0c0 / 0x1405005a0).
LEFT_WEAPON_BUFF_CAT = 163
RIGHT_WEAPON_BUFF_CAT = 162


def extra_hit_land(gap_frames: float, p_stagger: float, mech) -> float:
    """Chance a later hitbox of one attack lands, `gap_frames` (TAE frames) after the first hit.

    A staggered defender is locked through it (the gaps are shorter than every reaction's roll
    gate). One whose poise held is free at once: he rolls out when his reaction plus the round trip
    (`er-mechanics-ashes` `REACTION_MEDIAN_S`, `REACTION_LOG_SD`, `NETWORK_ONE_WAY_S`, all
    `INFERRED`) beats the gap; the roll's i-frames start on its first frame. `INFERRED`: he has
    no other commitment and reacts to the first hit rather than to the attack's start."""
    import math
    ash = mech.ash
    t = gap_frames / ATK.TAE_FPS - 2.0 * ash.NETWORK_ONE_WAY_S
    if t <= 0:
        return 1.0
    z = (math.log(t) - math.log(ash.REACTION_MEDIAN_S)) / ash.REACTION_LOG_SD
    p_out = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    return p_stagger + (1.0 - p_stagger) * (1.0 - p_out)


def entry_frames(slot_key: str) -> float:
    """`SCORE_ENTRY_FRAMES` for a slot key with or without its `2h_` prefix, else 0."""
    if "crouch" in TIMING_MIXUP and slot_key.removeprefix("2h_") == "crouch_r1":
        return 0.0
    return SCORE_ENTRY_FRAMES.get(slot_key.removeprefix("2h_"), 0.0)


class PvpTables:
    """The regulation columns the attack module does not load."""

    def __init__(self):
        files = PR.load(None)

        def table(stem, fields=None):
            rows, _, _ = PR.rows(PR.param_bytes(files, stem), fields)
            return {r["id"]: r for r in rows}

        self.weapon = table("EquipParamWeapon", [f"vsPlayerDmgCorrectRate_{v}" for v in VS_PLAYER.values()])
        self.atk = table("AtkParam_Pc", ["finalDamageRateId", "spEffectAtkPowerCorrectRate_byPoint"])
        self.final_rate = table("FinalDamageRateParam")


# --------------------------------------------------------------------------------------------
# the PvP corpus as defenders

def is_pvp(build: dict) -> bool:
    if build.get("isPvE") is True:
        return False
    return build.get("isPvE") is False or bool(set(build.get("tags") or []) & PVP_TAGS)


def pvp_corpus(mirror: Path, rl_lo: int, rl_hi: int) -> list[dict]:
    """PvP builds of the RL window that carry the planner's computed defense and absorption."""
    out = []
    for line in mirror.read_text().splitlines():
        b = json.loads(line)["build"]
        st = EMBED.stats_of(b)
        if st is None or not rl_lo <= st["rl"] <= rl_hi or not is_pvp(b):
            continue
        if sum(st[k] for k in EMBED.ATTRS) - OPT.LEVEL_OFFSET != st["rl"]:
            continue
        c = b.get("computed") or {}
        if not c.get("defenses") or not c.get("absorption"):
            continue
        out.append(c)
    if not out:
        raise SystemExit(f"no PvP build in RL {rl_lo}-{rl_hi} carries computed defenses")
    return out


class Defenders:
    """The corpus as arrays: flat defense per element and armor multiplier per damage type."""

    def __init__(self, computed: list[dict]):
        self.n = len(computed)
        self.defense = {el: np.array([c["defenses"][el] for c in computed], float) for el in ELEMENTS}
        self.absorption = {t: np.array([c["absorption"][k] for c in computed], float)
                           for t, k in ABSORB_KEY.items()}
        self.mult = {t: 1.0 - a / 100.0 for t, a in self.absorption.items()}
        self.poise = [p for p in (((c.get("poise") or {}).get("altered", (c.get("poise") or {}).get("original")))
                                  for c in computed) if p is not None]
        # The median defender, in the shape `er-mechanics-defense.damage` reads (physical
        # absorption keyed as that function expects: `physical` is the standard column).
        med_abs = {("physical" if t == "standard" else t): float(np.median(a)) for t, a in self.absorption.items()}
        self.median = {"defense": {el: float(np.median(v)) for el, v in self.defense.items()},
                       "armor_mult": {k: 1.0 - v / 100.0 for k, v in med_abs.items()},
                       "effect_mult": {k: 1.0 for k in med_abs}}

    def distribution(self) -> dict:
        out = {}
        for t, a in self.absorption.items():
            out[t] = {"mean": float(a.mean()), **{f"p{q}": float(np.percentile(a, q)) for q in PERCENTILES}}
        for el, d in self.defense.items():
            out[f"def_{el}"] = {"mean": float(d.mean()), **{f"p{q}": float(np.percentile(d, q)) for q in PERCENTILES}}
        return out


def defense_curve(attack: float, defense: np.ndarray) -> np.ndarray:
    """`er-mechanics-defense.defense_curve` over an array of defenses (same constants)."""
    if -DEF._EPS <= attack <= DEF._EPS:
        return np.zeros_like(defense)
    safe = np.where(defense > 0, defense, 1.0)
    r = np.where(defense > 0, attack / safe, DEF._R_HI)
    lo = np.where(r <= DEF._R_LO, DEF._P_LO,
                  DEF._P_LO + (DEF._P_ONE - DEF._P_LO) / (DEF._R_ONE - DEF._R_LO) ** 2 * (r - DEF._R_LO) ** 2)
    mid = DEF._P_MID + (DEF._P_ONE - DEF._P_MID) / (DEF._R_ONE - DEF._R_MID) ** 2 * (r - DEF._R_MID) ** 2
    hi = DEF._P_HI + (DEF._P_MID - DEF._P_HI) / (DEF._R_MID - DEF._R_HI) ** 2 * (r - DEF._R_HI) ** 2
    pct = np.where(r < DEF._R_ONE, lo, np.where(r <= DEF._R_MID, mid, np.where(r < DEF._R_HI, hi, DEF._P_HI)))
    return (1.0 - pct / 100.0) * attack


def corpus_hit(scaled: dict, phys: str, defenders: Defenders, fr: dict | None, counter: dict,
               post: dict | None = None) -> dict:
    """One hit (post-MV attack per element) on every defender: per-element damage arrays.

    Returns the mean total, the mean per element, and the mean total when the hit lands in the
    defender's counter frames (`counter`: factor per element, physical already by type). `post` is
    an after-defense factor per element (talisman `*AttackRate` and `atkPlayerDmgCorrectRate`)."""
    per = {}
    for el in ELEMENTS:
        key = el if el != "physical" else phys
        d = defense_curve(scaled.get(el, 0.0), defenders.defense[el])
        if key in defenders.mult:
            d = d * defenders.mult[key]
        if fr:
            d = d * fr[FINAL_RATE[el]]
        if post:
            d = d * post.get(el, 1.0)
        per[el] = np.maximum(d, 0.0)

    def total(parts):
        t = sum(parts.values())
        return np.where((t > 0) & (t < 1), np.ceil(t), t)

    return {"mean": float(total(per).mean()), "by_type": {el: float(v.mean()) for el, v in per.items()},
            "counter": float(total({el: v * counter[el] for el, v in per.items()}).mean())}


_TAL: dict = {}


def talisman_multipliers(names, attack: dict, weapon_id: int, active=None) -> dict | None:
    """`er-mechanics-talismans.multipliers` for this attack, or None when no talisman is worn.

    `active` is the set of timed buffs that are on (`TIMED` keys, e.g. 'lord_of_blood')."""
    if not names:
        return None
    if not _TAL:
        _TAL["m"] = _sibling("er-mechanics-talismans")
        _TAL["t"] = _TAL["m"].Talismans()
    slot = attack["slot"]
    ctx = {"slot": slot, "grip": "2h" if slot.startswith("2h_") else "1h", "weapon_id": weapon_id,
           "phys_type": attack["phys_type"], "pvp": True, "active": set(active or ())}
    if attack.get("atk_row") is not None:
        ctx["subcategories"] = _TAL["m"].attack_subcategories(_TAL["t"], attack["atk_row"])
    return _TAL["m"].multipliers(_TAL["t"], list(names), ctx)


def counter_factors(reg, phys: str, spear: bool) -> dict:
    """The defender's counter multiplier per element for a hit of physical type `phys`."""
    sp = reg.counter_speffects[COUNTER_SPEFFECT]
    f = {"physical": sp.get(phys, 1.0), **{el: sp[el] for el in ELEMENTS if el != "physical"}}
    if spear:
        boost = reg.counter_boosts[SPEAR_TALISMAN_SPEFFECT]
        f = {el: v * boost[el] if v > 1.0 else v for el, v in f.items()}
    return f


# --------------------------------------------------------------------------------------------
# one attack

def _with_def(post: dict | None, buff: dict | None, phys: str) -> dict | None:
    """`post` (after-defense factor per element, or None) times the defenders' expected buff
    factor (`buff['def']`, keyed by damage type: the physical column by `phys`)."""
    if not buff:
        return post
    d = buff["def"]
    return {el: (post or {}).get(el, 1.0) * d[phys if el == "physical" else el] for el in ELEMENTS}


def slot_hit(pvp, reg, weapon_id, attack, ar_by, defenders, grease=None, spear=False,
             talismans=None, active=None, buff=None) -> dict:
    """One attack on the corpus, player vs player.

    `grease` is (element, flat attack). It is added after the motion value and scaled by the
    attack's `spEffectAtkPowerCorrectRate_byPoint` instead (docs/er-mechanics/grease.md section 1);
    whether `vsPlayerDmgCorrectRate` then applies to it is not traced, and every weapon checked
    has that rate at 1.0.

    `talismans` is a list of talisman names, applied through `er-mechanics-talismans.multipliers`
    with this attack's slot, weapon and physical type: its AR rates scale the weapon part before
    defense (not the grease, docs/er-mechanics/talismans.md), its damage rates multiply each
    element after defense, and its counter rate replaces the `spear` flag.

    `buff` is `Mechanics.buffs` (docs/er-mechanics/buffs.md section 10): `pre` scales the weapon
    part of the AR like a talisman AR rate, `flat` is added like the grease (times `byPoint`),
    `post` multiplies each element after defense, and `def` is the defenders' own buffs per
    damage type, after defense too."""
    wep = pvp.weapon[weapon_id]
    tm = talisman_multipliers(talismans, attack, weapon_id, active)
    by_point = pvp.atk[attack["atk_row"]]["spEffectAtkPowerCorrectRate_byPoint"] / 100.0
    scaled = {el: ar_by[el] * attack[MV_KEY[el]] / 100.0 * (tm["power_rate"].get(el, 1.0) if tm else 1.0)
              * (buff["pre"][el] if buff else 1.0) for el in ELEMENTS}
    if grease:
        scaled[grease[0]] += grease[1] * by_point
    if buff:
        for el in ELEMENTS:
            scaled[el] += buff["flat"][el] * by_point
    scaled = {el: v * wep[f"vsPlayerDmgCorrectRate_{VS_PLAYER[el]}"] for el, v in scaled.items()}
    fr_id = pvp.atk[attack["atk_row"]]["finalDamageRateId"]
    fr = pvp.final_rate.get(fr_id) if fr_id >= 0 else None
    phys = attack["phys_type"]
    counter = counter_factors(reg, phys, spear and not tm)
    post = None
    if tm:
        post = {el: tm["damage_rate"].get(el, 1.0) * tm["damage_correct"].get(el, 1.0) for el in ELEMENTS}
        counter = {el: v * tm["counter_rate"].get(el, 1.0) if v > 1.0 else v for el, v in counter.items()}
    if buff:
        post = {el: (post or {}).get(el, 1.0) * buff["post"][el] for el in ELEMENTS}
        post = _with_def(post, buff, phys)
    hit = corpus_hit(scaled, phys, defenders, fr, counter, post)
    med_parts = DEF.damage(scaled, 100.0, defenders.median, phys, fr)["by_type"]
    median = sum(v * (post.get(el, 1.0) if post else 1.0) for el, v in med_parts.items())
    # FinalDamageRateParam.saRate multiplies poise damage between players (0x140486bf0, under the
    # same player-vs-player gate as ToughnessParam unk1). The 51-poise breakpoint is PvE only.
    poise = attack["poise_damage"] * POISE_MENU * (fr["saRate"] if fr else 1.0)
    windows = attack.get("hit_windows") or []
    hyper = attack.get("hyperarmor") or []
    # Hits on one target: `own_sweep_hits` counts this judge's events that open a fresh hit list
    # and are not a takeover of another judge's index (er-mechanics-attacks.py, the hit-list rule
    # is `VERIFIED`, treating a takeover as the same sweep is `INFERRED`). It can be 0 when an
    # earlier judge's segment carries the sweep hit (Milady running R1: judge 123, not 120).
    n = attack["own_sweep_hits"] if "own_sweep_hits" in attack else max(1, len(windows))
    endurance = round(reg.counter_speffects[COUNTER_SPEFFECT]["endurance"] * ATK.TAE_FPS)
    ctr_windows = [c["frames"] for c in attack.get("counter_windows") or [] if c["speffect"] == COUNTER_SPEFFECT]
    out = {"dmg": hit["mean"] * n, "med": median * n, "ctr": hit["counter"] * n, "hits": n,
           "by_type": {el: v * n for el, v in hit["by_type"].items()},
           "poise": poise, "stamina": attack["stamina_cost"], "phys_type": phys,
           "final_rate": fr["physRate"] if fr else 1.0,
           "startup": windows[0][0] if windows else None,
           "active": round(windows[0][1] - windows[0][0], 1) if windows else None,
           "hyperarmor": max((h["poise_bonus"] for h in hyper), default=0.0) * POISE_MENU,
           "ctr_window": round(sum(e - s + endurance for s, e in ctr_windows), 1) if ctr_windows else 0}
    # Recovery (`er-mechanics-attacks.py` cancel_frame): the first frame, from the start of this
    # animation, that the same button can start the next attack and that a roll can start.
    cancel = attack.get("cancel_frame") or {}
    button = "r2" if attack["slot"].removeprefix("2h_").startswith("r2") else "r1"
    out["next"] = cancel.get(button)
    out["roll"] = cancel.get("dodge")
    # An uncharged R2's frames are counted from its release clip; the charge-start clip before
    # the earliest release (`release_lead_in`, er-mechanics-attacks.py) comes first.
    lead = attack.get("release_lead_in") or 0.0
    if lead:
        for k in ("startup", "next", "roll"):
            if out[k] is not None:
                out[k] = round(out[k] + lead, 1)
    # The attacker's staminaAttackRate on a guarded hit (info+0x28, `FUN_14068aa80`): the worn
    # talismans' (Hammer Talisman) times the buff kits' uptime-weighted product.
    stamina_rate = (tm["stamina_damage"] if tm else 1.0) * (buff.get("stamina", 1.0) if buff else 1.0)
    out["guard_part"] = {"hit": {**attack, "weapon": weapon_id}, "scaled": scaled, "fr": fr, "post": post,
                         "n": n, "stamina_rate": stamina_rate}
    return out


# --------------------------------------------------------------------------------------------
# the mechanics modules, per slot

class Mechanics:
    """The sibling mechanics modules and their tables, loaded once.

    Each column below comes from the named module's own function; nothing here redoes its math:
    status `weapon_status`/`status_expected` over `Defenders` of the same RL window,
    crits `parry_exposure`, frame-advantage `slot_profile`, reach `reach_summary`, ashes
    `skill_hits`/`pvp_damage`."""

    def __init__(self, reg, ar_tables, mirror: Path, rl: int, window: int):
        self.reg = reg
        self.st = _sibling("er-mechanics-status")
        self.st_t = self.st.Tables(ar_tables=ar_tables)
        rows = self.st.corpus_rows(mirror, rl, window)
        self.st_def = self.st.corpus_defender(rows)
        self.st_dfs = self.st.Defenders(self.st_t, rows)
        self.cr = _sibling("er-mechanics-crits")
        self.cr_t = self.cr.load_tables()
        self.fa = _sibling("er-mechanics-frame-advantage")
        self.fa_t = self.fa.Tables(reg)
        self.re = _sibling("er-mechanics-reach")
        self.ash = _sibling("er-mechanics-ashes")
        self.ash_t = self.ash.AshTables()
        self.ar_tables = ar_tables
        # The skill and buff terms (docs/er-mechanics/ashes-of-war.md section 13, buffs.md
        # section 10): what the window's PvP builds mount on each weapon, their median FP bar,
        # and the buff kits the archetype attacks with and every PvP build defends with.
        lo, hi = rl - window, rl + window
        rows_p = self.ash.corpus_slots(str(mirror), lo, hi, "pvp")
        self.pairings = self.ash.skill_pairings(self.ash_t, rows_p)
        fps = sorted(r["computed"]["maxFP"] for r in rows_p if (r["computed"] or {}).get("maxFP"))
        self.fp_bar = fps[len(fps) // 2] if fps else self.ash.FP_BAR_DEFAULT
        self.buf = _sibling("er-mechanics-buffs")
        self.buf_m = self.buf.Buffs()
        # What limits and costs a kit buff's recast (`Buffs.source_recast`): the window's FP bar
        # for a spell, the item clip's goods frame for a consumable.
        self.buf_m.fp_bar = self.fp_bar
        self.buf_m.item_cast_frames = float(self.st.cure_frame())
        self.att_kits = self.buf.corpus_kits(self.buf_m, str(mirror), (lo, hi), SCORE_BUFF_ARCHETYPE, "offense-last")
        self.def_kits = self.buf.corpus_kits(self.buf_m, str(mirror), (lo, hi), None, "defense-last")
        # The fight (`set_fight`): its sample points and the landed hits at each, from the
        # window's rows (tags, flask split, median HP).
        self.fight_rows = rows
        self.set_fight()
        # Section 16 of ashes-of-war.md: the reaction dodge on every attack (`react`, with the
        # pool's R1 strike frames `strikes` for the whiff punish once `main` has the pool), and the
        # buff ashes scored as buffs in the option pool (`buff_options`; the corpus-weighted skill
        # buffs then leave the base, which would count them twice).
        self.react = True
        self.strikes = None
        self.buff_options = True
        self.follow_ups = True
        self.measure_all = False
        # The exchange pool (`er-mechanics-exchange.Pool`), set by `main`: skill options are
        # contested against it like slots (`skill_exchange`). `npool` is the same pool with each
        # build's reach for the neutral contest (`er-mechanics-neutral.NeutralPool`), or None.
        self.pool = None
        self.npool = None
        self.neut = NEUT

    def set_fight(self, fight_seconds=None, flasks="corpus", eta=None, pinned=None, cerulean="corpus") -> None:
        """The fight every buff, recast and status reader takes (buffs.md section 10).

        `fight_seconds` is the sample points of the 3 to 5 minute range
        (`er-mechanics-buffs.fight_points`, `--fight-seconds`); `hits` the landed hits at each,
        `er-mechanics-buffs.fight_hits` from the window's median HP, the reference landed hit
        (`er-mechanics-status.FIGHT_REF_DAMAGE`) and the defender's crimson flasks:

            duel      0 flasks (duel etiquette)
            invasion  every crimson flask the window carries (median `items.flasks.crimson`, 10)
            corpus    both, mixed by the window's planner tags (`tag_shares`, `fight_mix`)
            N         N flasks

        `pinned` sets every point to that many hits (the old fixed count is 5). `cerulean` is the
        attacker's cerulean flasks for skill recasts (`SetupBuffs.skill`): `corpus` takes the
        window's median. The status sims take the same schedule (`Defenders.fight_hits`)."""
        b = self.buf
        rows = self.fight_rows
        fs = tuple(fight_seconds) if fight_seconds is not None else tuple(b.FIGHT_SECONDS)

        def median(key, fallback):
            v = sorted(int(r["flasks"][key]) for r in rows if (r.get("flasks") or {}).get(key) is not None)
            return v[len(v) // 2] if v else fallback

        crimson = median("crimson", b.CORPUS_CRIMSON)
        eta = b.FLASK_ETA if eta is None else eta
        eng = self.st.ENGAGEMENT_SECONDS

        def sched(k):
            return b.fight_hits(fs, self.st_dfs.median_hp, self.st.FIGHT_REF_DAMAGE, k, b.FLASK_HEAL_HP, eta, eng)

        shares = b.tag_shares(rows)
        if pinned:
            points, hits, kind = fs, (int(pinned),) * len(fs), f"pinned {int(pinned)}"
        elif flasks == "corpus":
            points, hits = b.fight_mix([(shares["duel"], fs, sched(0)), (shares["flasks"], fs, sched(crimson))])
            kind = (f"corpus mix: duel {shares['duel']:.3f} (0 flasks), invasion/gank {shares['flasks']:.3f} "
                    f"({crimson} flasks), tag mentions {shares['mentions'][0]} / {shares['mentions'][1]}")
        elif flasks == "duel":
            points, hits, kind = fs, sched(0), "duel (0 flasks)"
        elif flasks == "invasion":
            points, hits, kind = fs, sched(crimson), f"invasion ({crimson} flasks)"
        else:
            points, hits, kind = fs, sched(int(flasks)), f"{int(flasks)} flasks"
        self.fight = {"fight_seconds": points, "hits": hits}
        self.fight_kind = {"kind": kind, "eta": eta, "crimson": crimson, "shares": shares,
                           "hits_mean": b.hits_mean(hits), "hits_min": min(hits), "hits_max": max(hits)}
        self.cerulean = median("cerulean", b.CORPUS_CERULEAN) if cerulean == "corpus" else int(cerulean)
        self.fight_kind["cerulean"] = self.cerulean
        self.st_dfs.fight_hits = hits
        self.def_buffs = self.buf_m.expected_defense(self.def_kits, **self.fight)
        self.ash.FIGHT_SECONDS = points
        self.__dict__.pop("_grease_plans", None)

    def grease_plan(self, tier: str, element: str) -> dict:
        """The right hand's grease of `element` (`OPT.GREASE_NAMES[tier]`) over the fight: its
        category-162 row's duration, recast as every buff is (`er-mechanics-buffs.recast_plan`) at
        most `maxNum` times (`Buffs.source_recast`), each use costing the item clip's goods frame.
        {'uptime', 'recasts', 'time_factor'}, cached."""
        cache = self.__dict__.setdefault("_grease_plans", {})
        key = (tier, element, self.fight["fight_seconds"], self.fight["hits"])
        if key not in cache:
            name = OPT.GREASE_NAMES[tier][element]
            ents, _ = self.buf_m.resolve([name])
            durs = [self.buf_m.sp[i]["effectEndurance"] for i, _ in ents if self.buf_m.sp[i]["spCategory"] == 162]
            uses, cast = self.buf_m.source_recast(name)
            cache[key] = self.buf.recast_plan(max(durs, default=-1.0), self.fight["fight_seconds"], uses=uses,
                                              cast_frames=cast, hits=self.fight["hits"])
        return cache[key]

    def skill_exchange(self, option: dict, base_id: int) -> dict | None:
        """The exchange and stamina factors of a skill option, as `EXCH.slot_exchange` gives a slot
        (`er-mechanics-ashes.skill_contest_inputs` for the strike frame, poise and hyperarmor).
        None when the option has no hit or the inputs fail."""
        try:
            ci = self.ash.skill_contest_inputs(self.ash_t, base_id, option["sword_arts_id"], option)
        except (SystemExit, KeyError, StopIteration, TypeError, ValueError, FileNotFoundError, OSError):
            ci = None
        if not ci:
            return None
        ex = EXCH.exchange(self.pool, ci["strike"], ci["poise"], ci["hyper"], option.get("dmg"))
        ex["strike_frame"], ex["strike_source"] = ci["strike"], "skill contact at 2.5 m"
        atk = {"slot": "skill", "stamina_cost": ci["stamina"], "hit_windows": [(ci["strike"], ci["strike"])],
               "tae_entry": ci["tae_entry"], "cancel_frame": option.get("cancel_frame") or {}}
        hit = {"next": option.get("next"), "roll": option.get("roll"), "dmg": option.get("dmg")}
        try:
            st = EXCH.slot_stamina(self.pool, self.reg, base_id, atk, hit)
        except (KeyError, TypeError, ValueError, FileNotFoundError, OSError):
            st = {"f_stamina": 1.0}
        return {**ex, **st, "factor": ex["f_exchange"] * st["f_stamina"], "poise": ci["poise"], "hyper": ci["hyper"]}

    def skill_choice(self, base_id: int, aff: str, level: int) -> list:
        """[(SwordArtsParam id, p)]: what the corpus mounts on this weapon as built."""
        return self.ash.skill_choice(self.ash_t, self.pairings, base_id, self.ash.AFFINITIES.index(aff), level)

    def buffs(self, weapon: str, aff: str, level: int, stats: dict, two: bool, base_id: int, choice: list,
              greased: bool) -> dict:
        """The expected buff factors on this build's hits (`slot_hit` `buff`): the archetype's kits
        with the weapon's skill buffs as alternatives, and the defenders' kits."""
        alts = [] if self.buff_options else self.ash.skill_buff_alternatives(
            self.ash_t, base_id, choice, level, self.fp_bar, self.fight["hits"])
        ratio = self.buf.ar_stat_ratio(weapon, aff, level, stats, two, self.ar_tables)
        e = self.buf_m.expected_attack(self.att_kits, two, ratio, alternatives=alts,
                                       drop_skill_weapon_buffs=greased, **self.fight)
        return {"pre": e["pre"], "post": e["post"], "flat": e["flat"], "def": self.def_buffs["factor"],
                "stamina": e["stamina"], "alternatives": alts}

    def skill_buff(self, weapon: str, aff: str, level: int, stats: dict, two: bool, roots: tuple,
                   casts: float) -> dict:
        """The build's buff factors with one skill's buff `roots` held (`casts` of them in a fight),
        and whether they take the weapon-buff slot, so the sweep's grease comes off
        (buffs.md section 10: 162/163 rows replace each other)."""
        ratio = self.buf.ar_stat_ratio(weapon, aff, level, stats, two, self.ar_tables)
        e = self.buf_m.expected_attack(self.att_kits, two, ratio,
                                       alternatives=[(1.0, tuple((r, 1.0, casts) for r in roots))],
                                       drop_skill_weapon_buffs=False, **self.fight)
        slot = any(self.buf_m.sp[i]["spCategory"] in self.buf.WEAPON_BUFF_CATS
                   for r in roots for i in self.buf_m.skill_rows(r))
        return {"pre": e["pre"], "post": e["post"], "flat": e["flat"], "def": self.def_buffs["factor"],
                "stamina": e["stamina"], "alternatives": [], "weapon_slot": slot}

    def skill_term(self, weapon: str, aff: str, level: int, stats: dict, two: bool, base_id: int,
                   choice: list, base_score: float, defenders, reg, buff: dict | None, slot_extra,
                   engagement: dict | None = None, opponents=None, buff_fn=None) -> dict:
        """`er-mechanics-ashes.skill_term` against `base_score` (the moveset score), each skill hit
        scored on the same corpus and buffs as a slot, and each skill with no scored hit valued
        on `engagement` (`skill_engagement`) against `opponents` (ashes-of-war.md section 14)."""
        ctx = self.ash.WeaponContext(weapon, aff, level, stats, two, self.ar_tables)

        def damage_fn(attack, phys, fr):
            scaled, post = dict(attack), None
            if buff:
                # `pre` scales the whole skill attack, bullet flat parts included, and a skill
                # hit's AtkParam byPoint is taken as 100 for the flat adds (both `INFERRED`).
                scaled = {el: v * buff["pre"][el] + buff["flat"][el] for el, v in attack.items()}
                post = {el: buff["post"][el] for el in ELEMENTS}
            fac = counter_factors(reg, phys, False)
            hit = corpus_hit(scaled, phys, defenders, fr, fac, _with_def(post, buff, phys))
            return hit["mean"]

        # Every skill the weapon can carry as built is an option (ashes-of-war.md section 15), each
        # with its own reach and coverage; the corpus-weighted term rides along as `value_corpus`.
        available = self.ash.mountable_skills(self.ash_t, base_id, self.ash.AFFINITIES.index(aff), level)
        # The neutral contest can raise the committed share above the 2.5 m one; the bound on an
        # unmeasured option allows for the largest it can give (`1 + EXCHANGE_WEIGHT`).
        bound = SCORE_REACH_CLAMP[1] * self.re.COVER_CLAMP[1] * (1.0 + EXCH.EXCHANGE_WEIGHT if self.npool else 1.0)
        return self.ash.skill_term(self.ash_t, base_id, choice, ctx, level, base_score, self.fp_bar,
                                   self.fight["hits"], score_fn=slot_score, damage_fn=damage_fn,
                                   poises=defenders.poise, slot_extra_fn=slot_extra,
                                   engagement=engagement, opponents=opponents, available=available,
                                   reach=True, factor_bound=bound,
                                   grip="both" if two else "one", react=self.react, strikes=self.strikes,
                                   buff_fn=buff_fn if self.buff_options else None,
                                   follow_ups=self.follow_ups, measure_all=self.measure_all,
                                   neutral_fn=self.skill_neutral if self.npool else None)

    def skill_neutral(self, option: dict, slot: dict) -> dict | None:
        """`er-mechanics-neutral.neutral_exchange` of a measured skill option: its 2.5 m contact
        and poise (`skill_exchange`) with its own measured reach."""
        ex = slot.get("exchange") or {}
        if ex.get("strike_frame") is None or not slot.get("reach"):
            return None
        return self.neut.neutral_exchange(self.npool, ex["strike_frame"], slot["reach"], ex.get("poise") or 0.0,
                                          ex.get("hyper") or [], dmg=option.get("dmg") or slot.get("dmg"))

    def skill_slot_extra(self, option: dict, crit: dict | None, own, own_ref, base_id: int | None = None,
                         f_weight: float = 1.0) -> dict:
        """The slot factors a skill option gets beside its damage: frame advantage from its last
        hit (`er-mechanics-frame-advantage.advantage` on the skill TimeAct's cancel frames), the
        weapon's crit terms, the 2H own-guard term, the sweep row's equip-weight factor, and with
        an exchange pool the same contest and stamina factors a slot gets
        (`er-mechanics-exchange.exchange` / `slot_stamina` on `skill_contest_inputs`: the first
        contact at 2.5 m, its poise, the skill's hyperarmor windows, the melee events' stamina).
        Reach and coverage are added by `er-mechanics-ashes.skill_term` itself
        (`skill_reach_factors`); the blockers' guard pressure is not measured for skills and stays
        neutral."""
        out = {"crit_hp": crit["crit_hp"] if crit else 0.0, "parry_hp": crit["parry_hp"] if crit else 0.0,
               "guard_own": own, "guard_own_ref": own_ref, "f_weight": f_weight}
        if self.pool is not None and base_id is not None and option.get("hit_rows"):
            out["exchange"] = self.skill_exchange(option, base_id)
        row = option.get("last_atk_row")
        if row is None or row not in self.fa_t.atk or not option.get("hit_windows"):
            return out
        slot = {"hit_windows": option["hit_windows"], "cancel_frame": option["cancel_frame"]}
        for key, broken in (("adv", False), ("adv_stagger", True)):
            react = self.fa.reaction(self.fa.reaction_level(self.fa_t, row, broken))
            a = self.fa.advantage(slot, react) if react else None
            out[key] = a["advantage"] if a else None
        return out

    def slots(self, weapon: str, aff: str, level: int, stats: dict, two: bool, wid: int,
              attacks: list[dict], hits: dict | None = None) -> dict:
        """{slot key without `2h_`: status, parry, frame advantage and reach} for one build.

        `hits` is {slot key: `slot_hit` result} when the caller has it: its `next` (lead-in
        included) and `stagger` feed the status model; without it `next` comes from the attack's
        own cancel frame and the stagger share is 0."""
        grip = "both" if two else "one"
        out = {a["slot"].removeprefix("2h_"): {} for a in attacks}
        for d in self.cr.parry_exposure(self.cr_t, wid, grips=(grip,))["detail"]:
            if d["slot"].removeprefix("2h_") in out:
                out[d["slot"].removeprefix("2h_")]["parryable"] = d["parryable"]
        for e in self.fa.slot_profile(self.reg, self.fa_t, wid, grip):
            s = out.get(e["slot"].removeprefix("2h_"))
            if s is not None:
                s["adv"] = (e["on_intact"] or {}).get("advantage")
                s["adv_stagger"] = (e["on_break"] or {}).get("advantage")
                s["reaction"] = (e["reaction_poise_intact"], e["reaction_on_break"])
                gaps = [c["on_intact"]["gap"] for c in e.get("combos") or [] if c.get("on_intact")]
                s["follow_up_gap"] = min(gaps) if gaps else None
                s["combos"] = e.get("combos") or []
        summary = self.re.reach_summary(wid, grip)
        # Read now: the class medians below measure peer weapons, and only the latest is kept.
        contacts = self.re.slot_contacts(wid, grip) if self.react else {}
        for key, r in summary.items():
            s = out.get(key.removeprefix("2h_"))
            if s is not None:
                s["reach"] = r.get("world_reach_m")
                s["reach_source"] = "world"
                s["weapon_reach"] = r["weapon_reach_m"]
                s["first_hit_real"] = r["first_hit_frame_real"]
                s["coverage"] = r.get("coverage_factor")
                if s["reach"] is None or s["coverage"] is None:
                    # No pose measurement: the class median stands in (`class_fallback`), since
                    # a neutral 1.0 sits below the measured reach median and above the coverage
                    # one, and grip-to-tip reach is a different quantity from world reach.
                    fb = self.re.class_fallback(wid, grip, key)
                    s["fallback"] = fb["basis"]
                    if s["reach"] is None and fb.get("world_reach_m") is not None:
                        s["reach"], s["reach_source"] = fb["world_reach_m"], "inferred"
                    if s["coverage"] is None:
                        s["coverage"] = fb.get("coverage_factor")
                s["front_contact"] = r.get("front_contact_frame_real")
                s["swing"] = r.get("swing_shape")
                s["arc_eff"] = r.get("coverage_arc_eff_deg")
        for short, s in out.items():
            if "reach_source" in s:
                continue
            # A slot the reach module returned no row for (no hit window it could read):
            # the same class-median stand-in as an unposed slot.
            key = ("2h_" if two else "") + short
            fb = self.re.class_fallback(wid, grip, key)
            s["fallback"] = fb["basis"]
            s["reach"], s["reach_source"] = fb.get("world_reach_m"), "inferred"
            s["coverage"] = fb.get("coverage_factor")
        if self.react:
            # The reaction dodge (ashes-of-war.md section 16) at every distance the slot reaches,
            # timed from its own clip's start: an R2's charge is held as long as the attacker
            # likes, so its timing is read from the release clip, the one `startup` counts from
            # after its lead-in. A jump attack's landed clip starts at the press, and the jump
            # is visible from its input, `SCORE_ENTRY_FRAMES` earlier (6, moveset.md 6c). The
            # sprint in front of a sprinting jump is as long as the attacker likes, so like the
            # R2 charge it gives no timing: every jump is read from its input.
            for a in attacks:
                key = a["slot"].removeprefix("2h_")
                s, hit = out[key], (hits or {}).get(key) or {}
                lead = a.get("release_lead_in") or 0.0
                cue = -entry_frames(key) if key.startswith("jump_") else 0.0
                fc = s.get("front_contact") or {}
                dists = [d for d in self.re.FRONT_CONTACT_DISTANCES_M if fc.get(d) is not None]
                roll = hit.get("roll")
                first = hit.get("startup")
                tm = _sibling_cached("er-mechanics-timing-mixup") if "entry" in TIMING_MIXUP else None
                if tm is not None and key in tm.FIXED_ENTRY_CUE:
                    # A rolling or backstep attack may also be anticipated from its entry. The
                    # term reads the reaction model from this run's module, not a second copy.
                    tm._MODS.setdefault("er-mechanics-ashes", self.ash)
                    s["react"] = tm.slot_reaction_mixup(
                        contacts.get(a["slot"]), dists or [self.ash.ENGAGE_DISTANCE_M], key, entry_frames(key),
                        roll - lead if roll is not None else None, self.strikes,
                        fallback=(first - lead if first is not None else None, hit.get("active")))
                    continue
                s["react"] = self.ash.slot_reaction(
                    contacts.get(a["slot"]), dists or [self.ash.ENGAGE_DISTANCE_M], cue,
                    roll - lead if roll is not None else None, self.strikes,
                    fallback=(first - lead if first is not None else None, hit.get("active")))
        return self.statuses(out, weapon, aff, level, stats, two, attacks, hits)

    def statuses(self, out: dict, weapon: str, aff: str, level: int, stats: dict, two: bool,
                 attacks: list[dict], hits: dict | None = None, grease=None) -> dict:
        """`slots`' status step on `out` ({slot key: slot dict with `combos`, `reaction`,
        `follow_up_gap`}), each slot's `status` set in place: `er-mechanics-status.status_expected`
        per attack with its true-combo chain. `grease` is an attacker buff's status row
        (`--setup`'s right-hand spill), applied like a grease. Returns `out`."""
        ws = self.st.weapon_status(self.st_t, weapon, aff, level, stats, two_handed=two, pvp=True)
        by_key = {a["slot"].removeprefix("2h_"): a for a in attacks}

        def chain(key):
            """The true-combo follow-ups after `key`: [{'attack', 'p', 'gap'}] (status engagement)."""
            links, seen = [], {key}
            while True:
                combos = out.get(key, {}).get("combos") or []
                c = combos[0] if combos else None
                nxt = c["next"].removeprefix("2h_") if c else None
                if not c or nxt not in by_key or nxt in seen:
                    return links
                stag = ((hits or {}).get(key) or {}).get("stagger") or 0.0
                p = self.st.combo_land(c, stag)
                if p <= 0.0:
                    return links
                sides = [(w, v["gap"]) for w, v in ((1.0 - stag, c.get("on_intact")),
                                                     (stag, c.get("on_break"))) if v]
                wsum = sum(w for w, _ in sides)
                gap = sum(w * g for w, g in sides) / wsum if wsum else (sides[0][1] if sides else None)
                links.append({"attack": by_key[nxt], "p": p, "gap": gap})
                seen.add(nxt)
                key = nxt

        for a in attacks:
            key = a["slot"].removeprefix("2h_")
            s = out[key]
            if not ws["sources"] and grease is None:
                s["status"] = {}
                continue
            hit = (hits or {}).get(key) or {}
            nxt = hit.get("next")
            if nxt is None and not hits:
                button = "r2" if key.startswith("r2") else "r1"
                nxt = (a.get("cancel_frame") or {}).get(button)
            gap = min([g for g in (nxt, s.get("follow_up_gap")) if g], default=None)
            react = s.get("reaction") or (0, 0)
            s["status"] = self.st.status_expected(self.st_t, ws, a, self.st_dfs, grease=grease, gap=gap,
                                                  react=react, stagger=hit.get("stagger") or 0.0,
                                                  chain=chain(key), eng_s=self.st.ENGAGEMENT_SECONDS)
        return out

    def skill(self, weapon: str, aff: str, level: int, stats: dict, two: bool, base_id: int,
              defender: dict) -> dict | None:
        """The weapon's own skill: its best single PvP hit on `defender` and its FP cost."""
        t = self.ash_t
        sid = t.weapon_skill(base_id)
        if sid is None or sid < 0 or sid not in t.arts:
            return None
        try:
            ctx = self.ash.WeaponContext(weapon, aff, level, stats, two, self.ar_tables)
            hits = self.ash.skill_hits(t, base_id, sid, ctx, level)
        except (SystemExit, KeyError, StopIteration) as exc:
            return {"name": t.arts_name(sid), "error": str(exc)}
        total = self.ash.pvp_damage(t, base_id, hits, defender, DEF) if hits else 0.0
        # A stance skill (Unsheathe) charges nothing on L2 and its FP on the R1/R2 follow-up, so
        # the cost shown is the highest of the four SwordArtsParam `useMagicPoint_*` columns.
        fp = max(t.arts[sid][f"useMagicPoint_{h}"] for h in ("L1", "L2", "R1", "R2"))
        return {"name": t.arts_name(sid), "fp": fp,
                "best_hit": max((h["pvp_damage"] for h in hits), default=0.0), "total": total,
                "hits": len(hits)}


def exultation_factor(mech, pvp, reg, weapon_id, attack, ar_by, defenders, grease, spear, talismans,
                      hit: dict, ms: dict) -> dict | None:
    """The damage factor an exultation the attacker wears earns on this slot from its own procs.

    `er-mechanics-status.exultation_uptime` gives the share of the slot's hits inside the buff
    (procs per landed hit x hits landing within the buff, from the engagement rate); the main hitbox is scored
    again with the buff on (`er-mechanics-talismans` `active`), and the slot's damage is scaled by
    1 + uptime x (buffed / plain - 1). Build-up and proc HP are untouched: attacker SpEffects
    never scale status (docs/er-mechanics/status.md section 1)."""
    worn = [n for n in talismans or () if n in mech.st.EXULTATIONS]
    status = ms.get("status") or {}
    if not worn or not status:
        return None
    gap = next((v["gap"] for v in status.values() if v.get("gap")), None)
    keys, up = set(), 0.0
    for n in worn:
        key, u = mech.st.exultation_uptime(mech.st_t, n, status, gap)
        if u > 0:
            keys.add(key)
            up = max(up, u)
    if not keys:
        return None
    plain = slot_hit(pvp, reg, weapon_id, attack, ar_by, defenders, grease, spear, talismans)
    on = slot_hit(pvp, reg, weapon_id, attack, ar_by, defenders, grease, spear, talismans, keys)
    if not plain["dmg"]:
        return None
    buffed = on["dmg"] / plain["dmg"]
    return {"talismans": sorted(keys), "uptime": up, "buffed": buffed, "factor": 1.0 + up * (buffed - 1.0)}


def slot_score(s: dict, entry: float = 0.0) -> dict | None:
    """The combined score of one slot (module docstring), with each factor, or None when the
    slot has no hit or no recovery frame to measure its commitment by. `entry` is the
    `SCORE_ENTRY_FRAMES` cost of getting into the slot."""
    ends = [f for f in (s.get("roll"), s.get("next")) if f]
    if not s.get("dmg") or not ends:
        return None
    commit = entry + min(ends)
    status_hp = sum(v["hp_per_hit"] for v in (s.get("status") or {}).values())
    if PROC_OPENING is not None:
        status_hp += PROC_OPENING(s) or 0.0
    crit_hp = s.get("crit_hp") or 0.0
    parry_hp = (s.get("parry_hp") or 0.0) if s.get("parryable") else 0.0
    # The reaction dodge (ashes-of-war.md section 16): the share of the slot's worth that lands when
    # the waiting share of defenders may dodge it, and the punish an evaded one eats. A skill
    # option carries its own landed damage and `whiff_hp` instead.
    react = s.get("react") or {}
    land = react.get("land", 1.0)
    whiff_hp = s.get("whiff_hp", react.get("whiff_hp", 0.0)) or 0.0
    # The contest factor: the exchange's startup and hyperarmor part, or the interrupt factor that
    # replaces it for the openers it covers (docs/er-mechanics/interrupt.md section 6). The stamina
    # part multiplies the whole score either way. Both the contest and the reaction dodge reward a
    # fast startup, so they split the defenders instead of multiplying: the committed share
    # (1 - react_share) meets the contest, the waiting share the dodge. Without a reaction term
    # the contest multiplies the whole score, as before.
    ex = s.get("exchange") or {}
    it = s.get("interrupt")
    f_contest = it["f_interrupt"] if it is not None else ex.get("f_exchange", 1.0)
    # The neutral game (`er-mechanics-neutral`, docs/er-mechanics/neutral.md): the same contest
    # started from outside both reaches. Under `--interrupt` its ratio to the 2.5 m exchange (the
    # part distance changes, on the same poise rule) scales the interrupt factor.
    nt = s.get("neutral")
    if nt is not None:
        f_contest = nt["f_neutral"] if it is None else f_contest * nt["f_neutral"] / (ex.get("f_exchange") or 1.0)
    f_stamina = ex.get("f_stamina", ex.get("factor", 1.0) / (ex.get("f_exchange") or 1.0))
    hit_worth = land
    if react:
        share = react.get("react_share", 0.0)
        hit_worth = (1.0 - share) * f_contest + (land - (1.0 - share))
    f_sustain = SUSTAIN(s["dmg"] + SCORE_STATUS_WEIGHT * status_hp) if SUSTAIN else 1.0
    rate = (f_sustain * (hit_worth * (s["dmg"] + SCORE_STATUS_WEIGHT * status_hp) + SCORE_CRIT_WEIGHT * crit_hp)
            - parry_hp - whiff_hp) / commit * SCORE_FPS
    reach = s.get("reach")
    f_reach = 1.0 if not reach else min(max((reach / SCORE_REACH_REF_M) ** SCORE_REACH_EXP,
                                            SCORE_REACH_CLAMP[0]), SCORE_REACH_CLAMP[1])
    stag = s.get("stagger") or 0.0
    adv, adv_s = s.get("adv"), s.get("adv_stagger")
    exp_adv = None
    if adv is not None or adv_s is not None:
        a = adv if adv is not None else adv_s
        b = adv_s if adv_s is not None else adv
        exp_adv = (1.0 - stag) * a + stag * b
    f_adv = 1.0 if exp_adv is None else \
        1.0 + SCORE_ADV_WEIGHT * max(-SCORE_ADV_SPAN, min(SCORE_ADV_SPAN, exp_adv)) / SCORE_ADV_SPAN
    f_stag = 1.0 + SCORE_STAGGER_WEIGHT * stag
    f_guard = GUARD.guard_score_factor(s.get("guard"), s.get("guard_own"), s.get("guard_own_ref"))
    f_cover = s.get("coverage") or 1.0
    # `f_startup` x `f_hyper` is the same f_exchange split in two, so it is not multiplied again
    # (docs/er-mechanics/exchange.md). With a reaction term the contest is already in the rate.
    f_ex = f_stamina * (1.0 if react else f_contest)
    # The sweep row's equip-weight factor (`weight.factor`, the sweep's own model), carried on the
    # slot like `crit_hp`.
    f_weight = s.get("f_weight") or 1.0
    return {"score": rate * f_reach * f_cover * f_adv * f_stag * f_guard * f_ex * f_weight, "rate": rate,
            "commit": commit, "status_hp": status_hp, "crit_hp": crit_hp, "parry_hp": parry_hp, "exp_adv": exp_adv,
            "land": land, "whiff_hp": whiff_hp, "hit_worth": hit_worth,
            "f_reach": f_reach, "f_cover": f_cover, "f_adv": f_adv, "f_stagger": f_stag, "f_guard": f_guard,
            "f_exchange": ex.get("f_exchange", 1.0), "f_stamina": f_stamina, "f_weight": f_weight,
            "f_interrupt": (it or {}).get("f_interrupt", 1.0), "f_contest": f_contest,
            "f_neutral": (nt or {}).get("f_neutral"), "f_sustain": f_sustain}


def _buff_moveset_fn(mech, pvp, reg, base_id, row, b, level, stats, attacks, slots, main_dmg, ar_by,
                     defenders, grease, spear, talismans, pair_fn=None, opening=None, grease_tf=1.0,
                     regard=None):
    """`er-mechanics-ashes.skill_term`'s `buff_fn` for one row (ashes-of-war.md section 16b): the
    moveset score with a skill's buff rows held. Every slot's main hitbox is hit again with the
    build's buff factors plus that skill's (`Mechanics.skill_buff`), and without the sweep's grease
    when the skill's rows take the weapon-buff slot; the slot's damage is scaled by the ratio (its
    other hitboxes are taken to scale the same, `INFERRED`) and the moveset scored again.
    `pair_fn` and `opening` are the row's `--paired-offhand` step, so the buffed moveset is scored
    the way the row's own was (the off-hand L1 itself is not buffed). A grease that stays on pays
    its uses' time (`grease_tf`, `Mechanics.grease_plan`), as the row's own score does.
    `regard(slot key, staminaAttackRate)` re-measures a slot's guard pressure under the skill's
    stamina multiplier (Royal Knight's Resolve x4 on its next hit); None keeps the row's."""
    cache = {}

    def fn(_option, roots, casts):
        key = (tuple(roots), casts)
        if key in cache:
            return cache[key]
        bf = mech.skill_buff(row["weapon"], b["aff"], level, stats, row["two"], roots, casts)
        g = None if bf["weapon_slot"] else grease
        buffed = {}
        for atk in attacks:
            k = atk["slot"].removeprefix("2h_")
            s = slots.get(k)
            if s is None:
                continue
            if not main_dmg.get(k):
                buffed[k] = s
                continue
            h = slot_hit(pvp, reg, base_id, atk, ar_by, defenders, g, spear, talismans, buff=bf)
            r = h["dmg"] / main_dmg[k]
            new = {**s, "dmg": s["dmg"] * r, "med": s["med"] * r, "ctr": s["ctr"] * r}
            if regard is not None:
                new["guard"] = regard(k, h["guard_part"]["stamina_rate"])
            new["score"] = slot_score(new, entry_frames(k))
            buffed[k] = new
        if pair_fn is not None:
            buffed = pair_fn(buffed)
        cache[key] = MOVESET.moveset_score({**buffed, **jump_openers(buffed, mech.npool)}, slot_score,
                                           entry_frames, opening=opening)["score"] * (grease_tf if g else 1.0)
        return cache[key]

    return fn


def jump_openers(slots: dict, npool=None) -> dict:
    """The per-jump openers `er-mechanics-moveset.with_jumps` synthesizes from the landed-clip jump
    slots, each with its own neutral contest when `npool` is given: the jump's reach (its travel
    included) and its first hit counted from the jump input, the landed clip's delay from first
    hit to the 2.5 m contact and its hyperarmor windows moved to the same clock (`INFERRED`: the
    landed clip's timing relative to its first hit holds in the air). Without `npool` the openers
    carry the same `neutral_in` (what an opponent pool throws, `NEUT.NeutralPool.from_results`)
    but no contest, so the moveset scores them as it did before; when the landed slot has no
    `neutral_in` they are what the moveset module makes of them."""
    synth = {k: v for k, v in MOVESET.with_jumps(slots, entry_frames).items() if k not in slots}
    out = {}
    for k, s in synth.items():
        base_key = k.rsplit("_", 1)[0]
        base = slots.get(base_key) or {}
        ni = base.get("neutral_in")
        s = dict(s)
        if ni and s.get("reach") and s.get("startup") is not None and base.get("startup") is not None:
            entry = entry_frames(base_key)
            delay = max(0.0, ni["strike"] - entry - base["startup"])
            shift = s["startup"] - base["startup"] - entry
            hyper = [(a + shift, b + shift, bonus, m) for a, b, bonus, m in ni["hyper"]]
            if npool is not None:
                s["neutral"] = NEUT.neutral_exchange(npool, s["startup"] + delay, s["reach"], ni["poise"], hyper,
                                                     ni["active"], dmg=s.get("dmg"))
            s["neutral_in"] = {**ni, "strike": s["startup"] + delay, "reach": s["reach"], "hyper": hyper}
        out[k] = s
    return out


def skill_engagement(slots: dict, moveset: dict, crit: dict | None, crit_ev: dict | None) -> dict | None:
    """The engagement a utility skill is valued on (ashes-of-war.md section 14): the moveset's best
    opener as `slot_score` scored it. `numerator` = its HP per engagement (damage, status, crit,
    less parry), `commit` its commitment, `strike` the exchange's strike frame (else its first
    hit), `exchange` its outcome shares, and the weapon's crit terms for a parry."""
    key = moveset.get("best_opener")
    if key and key not in slots and hasattr(MOVESET, "with_jumps"):
        # A jump opener is synthesized by the moveset module from the landed-clip slot.
        slots = MOVESET.with_jumps(slots, entry_frames)
    s = slots.get(key) if key else None
    sc = (s or {}).get("score") or (slot_score(s, s.get("jump_entry", entry_frames(key))) if s else None)
    if not sc:
        return None
    ex = s.get("exchange") or {}
    strike = ex.get("strike_frame") or s["startup"]
    reach = s.get("reach") or EXCH.STRIKE_DISTANCE_M
    # `dmg` is what the opener lands: section 16's reaction share applied, as in the numerator.
    # `neutral_in` lets a utility skill rerun the opener's neutral contest with its own dodge.
    return {"numerator": sc["rate"] * sc["commit"] / SCORE_FPS, "commit": sc["commit"],
            "dmg": s["dmg"] * sc.get("land", 1.0), "dmg_raw": s["dmg"],
            "strike": strike, "reach": reach, "neutral_in": s.get("neutral_in"), "neutral": s.get("neutral"),
            "exchange": ex if "win" in ex else None, "crit": crit, "crit_ev": crit_ev, "slot": key}


#: Speed measures `--relative-speed` can key on (`speed_measures`; docs/er-mechanics/moveset.md
#: section 7). `string_dps` is the only one the RL 140-160 corpus supports (7d).
SPEED_MEASURES = ("string_dps", "first_hit", "rec_roll", "rec_next", "second_hit", "run_r1", "roll_r1")


def speed_measures(slots: dict) -> dict:
    """Speed measures of one row, each lower = faster (frames at 30 fps; the DPS negated):
    `first_hit` R1 #1's first active frame; `rec_roll` / `rec_next` its hit to the earliest roll /
    the earliest R1 #2 start; `second_hit` the frame R1 #2 lands from R1 #1's start (R1 #2 started
    at R1 #1's `next`); `string_dps` minus the R1 chain's damage per second, each R1 started at the
    previous one's `next` and the last timed to its recovery; `run_r1` and `roll_r1` those slots'
    first active frame (their own clip, entry not included)."""
    r1 = slots.get("r1_1") or {}
    r2 = slots.get("r1_2") or {}
    s = r1.get("startup")
    t, dmg, k = 0.0, 0.0, 1
    while slots.get(f"r1_{k}") and slots[f"r1_{k}"].get("dmg"):
        cur = slots[f"r1_{k}"]
        dmg += cur["dmg"]
        nxt = slots.get(f"r1_{k + 1}")
        if nxt and cur.get("next") is not None and nxt.get("dmg"):
            t += cur["next"]
        else:
            end = [f for f in (cur.get("roll"), cur.get("next")) if f]
            t += min(end) if end else (cur.get("startup") or 0.0)
            break
        k += 1

    def sub(a, b):
        return None if a is None or b is None else a - b
    return {"first_hit": s, "rec_roll": sub(r1.get("roll"), s), "rec_next": sub(r1.get("next"), s),
            "second_hit": (r1["next"] + r2["startup"]) if r1.get("next") is not None
            and r2.get("startup") is not None else None,
            "string_dps": -(dmg / t * SCORE_FPS) if t else None,
            "run_r1": (slots.get("run_r1") or {}).get("startup"),
            "roll_r1": (slots.get("roll_r1") or {}).get("startup")}


def relative_speed(value: float | None, fastest: float | None, tau: float) -> float:
    """The relative-speed factor: 1 for the class's fastest, `exp(-(value - fastest) / tau)` for
    the rest (`value` lower = faster, in the measure's units), so a class leader's lead over the
    next weapon grows with the gap. One parameter (docs/er-mechanics/moveset.md section 7)."""
    if value is None or fastest is None or not tau:
        return 1.0
    return float(np.exp(-max(0.0, value - fastest) / tau))


#: The off-hands of the user's paired loop (running R1 -> off-hand L1, the next L1 catching a roll,
#: back to running R1): the hatchets, whose L1 moveset the user names as the reason
#: (docs/er-mechanics/combo.md section 11).
PAIRED_LOOP_LEFTS = ("Hand Axe", "Forked Hatchet", "Icerind Hatchet")


def paired_loop_lefts(rl: int, window: int, names=PAIRED_LOOP_LEFTS, jobs: int = 1,
                      keep_no_catch: bool = False) -> dict:
    """{left weapon: {'p': roll-catch chance, 'next': L1 #1's next-L1 frame, 'wid'}} from
    `er-mechanics-offhand.Scorer.roll_catch` (combo.md section 10b). `names` 'all' is every left
    weapon with an off-hand L1 (`Scorer.candidates`). With `keep_no_catch` a left whose roll-catch
    cannot be measured stays in with `p` 0 (`--setup`); otherwise it is left out, as before."""
    off = _sibling("er-mechanics-offhand")
    sc = off.Scorer(rl, window)
    if names == "all":
        names = sorted({sc.reg.weapon_names[w] for w in sc.candidates()})

    def one(n):
        wid = sc.names.get(n)
        if wid is None:
            return None
        rows = sc.model.offhand(wid)
        seq = [rows[k] for k, *_ in off.COMBO.OFFHAND if k in rows]
        if not seq or seq[0].get("l1_start") is None:
            return None
        rc = sc.roll_catch(wid, seq, len(seq))
        if rc:
            return n, {"p": rc["p_catch_any"], "next": seq[0]["l1_start"], "roll_catch": rc, "wid": wid}
        return (n, {"p": 0.0, "next": seq[0]["l1_start"], "roll_catch": None, "wid": wid}) if keep_no_catch else None
    names = list(names)
    got = _fork_map(lambda i: one(names[i]), range(len(names)), jobs) if jobs > 1 else map(one, names)
    return dict(g for g in got if g)


class SetupBuffs:
    """`--setup` (combo.md section 11a): the weapon buffs a left weapon can carry, and what each
    does to its own hits.

    Options per left weapon row (affinity included): none; each grease element of the run's grease
    tier when the row takes a grease (`Buffs.can_take_weapon_buff`, `isEnhance`); each skill
    mountable at that affinity and level (`er-mechanics-ashes.mountable_skills`) whose buff closure
    holds a category-163 row (Chilling Mist 827 -> 828, Sacred Blade 822 -> 823, ...). Only the
    163 rows are kept for the left hand. A skill's TimeAct applies both chains at once (Chilling
    Mist 825 -> 826, category 162, `wepParamChange` 1, and 827 -> 828, 163, 2;
    `er-mechanics-ashes.skill_buffs`), so the cast also buffs the right hand until a right-hand
    grease replaces 826 (same category 162, buffs.md section 3 R3). That right-hand row is not
    scored: the right keeps the sweep's grease. Both the AR accumulator and the status selector
    `FUN_1404f71e0` pass every entry through `IsApplicableForCategory` (the hand gate on
    `wepParamChange`, `VERIFIED` 1.16.2 decompile), so 828 reaches only the left hand's hits.
    Its frost is its `atkOccurrenceSpEffectId` 880 (frost 60, both rates; status.md section 1),
    not its own `freezeAttackPower` 30, which no hit reads. A skill's rows are read on the first
    weapon asked about it (`INFERRED`: the rows a skill applies do not depend on the weapon that
    mounts it).

    Uptime: the first application is before the fight and free (buffs.md section 10). A buff
    that does not cover the fight is either left to lapse or recast by the rule every buff shares
    (`er-mechanics-buffs.recast_plan` over the fight lengths `fight_s`, the 180..300 s sample
    points: ceil(fight / duration) - 1 recasts, or once per landed hit of that point's schedule
    (`Mechanics.set_fight`) for a next-hit row, at most what one FP bar and the attacker's
    cerulean flasks pay for, or a grease's `maxNum`), each recast costing its cast's frames of
    the fight and each cerulean drink its drink frames (time factor 1 - (recasts x cast + drinks x
    drink) / fight). A skill's cast is its opening animation's
    first roll frame (`er-mechanics-ashes.anim_recovery`); a grease's is the item-use frame to its
    SpEffect (`er-mechanics-status.cure_frame`, the bolus animation standing in, `INFERRED`). The
    grip change a left skill needs is not counted, nor is the cast's punish exposure."""

    def __init__(self, mech, tier: str, fight_s, left_buffs: str = "all"):
        self.mech, self.tier, self.fight_s, self.left_buffs = mech, tier, fight_s, left_buffs
        self.buf = mech.buf_m
        self._skill, self._eff, self._status = {}, {}, {}
        self._greases = None
        self._body = None

    def _rows(self, roots, cat: int = LEFT_WEAPON_BUFF_CAT) -> tuple:
        out = []
        for r in roots:
            if 0 < self.buf.sp[r]["conditionHp"] < 100:
                # A tier that fires only below that HP share (Shriek of Sorrow's 85/55/30,
                # ashes-of-war.md section 5, `INFERRED`): the attacker is taken at full HP.
                continue
            for i, _ in self.buf._closure([r]):
                if self.buf.sp[i]["spCategory"] == cat and i not in out:
                    out.append(i)
        return tuple(out)

    def greases(self) -> list:
        if self._greases is None:
            cast = self.mech.st.cure_frame()
            self._greases = []
            for el in OPT.GREASE_ELEMENTS:
                name = OPT.GREASE_NAMES[self.tier][el]
                ents, _ = self.buf.resolve([name])
                rows = self._rows([i for i, _ in ents])
                if rows:
                    self._greases.append({"kind": "grease", "name": name, "rows": rows, "cast": cast,
                                          "uses": self.buf.source_recast(name)[0]})
        return self._greases

    def skill(self, sid: int, wid: int) -> dict | None:
        if sid not in self._skill:
            ash, t = self.mech.ash, self.mech.ash_t
            opt = None
            try:
                prof = ash.skill_profile(t, sid, wid, 0)
                roots = [s["id"] for s in ash.skill_buffs(t, prof) if s.get("effect") == "buff"]
                rows = self._rows(roots)
                if rows:
                    op = ash.main_anim(prof)
                    cast = ash.anim_recovery(t, wid, sid, op).get("dodge") if op is not None else None
                    # The FP budget: one bar, then each cerulean flask the attacker carries
                    # (`Mechanics.cerulean`, `CERULEAN_FP` each). `paid[d]` is the casts d drinks
                    # pay for; each drink a recast needs costs `FLASK_DRINK_FRAMES` of the fight.
                    cost, buf = ash.skill_fp(t, sid), self.mech.buf
                    paid = tuple(ash.fp_uses(self.mech.fp_bar + d * buf.CERULEAN_FP, cost)
                                 for d in range(self.mech.cerulean + 1))
                    refill = (paid, buf.FLASK_DRINK_FRAMES) if cost > 0 and self.mech.cerulean else None
                    opt = {"kind": "skill", "name": t.arts_name(sid), "sid": sid, "rows": rows, "cast": cast,
                           "uses": paid[-1], "refill": refill,
                           "rows_right": self._rows(roots, RIGHT_WEAPON_BUFF_CAT)}
            except (SystemExit, KeyError, StopIteration, TypeError, ValueError):
                opt = None
            self._skill[sid] = opt
        return self._skill[sid]

    def options(self, lid: int, aff_idx: int, level: int) -> list:
        """[None (no buff), option, ...] for the left weapon `lid` at affinity index `aff_idx`."""
        out = [None]
        if self.left_buffs == "none":
            return out
        if self.buf.can_take_weapon_buff(lid + aff_idx * 100):
            out += self.greases()
        for sid in self.mech.ash.mountable_skills(self.mech.ash_t, lid, aff_idx, level):
            o = self.skill(sid, lid)
            if o:
                out.append(o)
        if self.left_buffs != "all":
            # One named option only (`--setup-left-buffs NAME`): that one where it fits, else none.
            out = [o for o in out if o and o["name"].lower() == self.left_buffs.lower()] or [None]
        return out

    def effect(self, opt: dict | None) -> dict | None:
        """The option's rows through the left-hand gate (`Buffs.attack_context` hand 'left'):
        `pre`, `flat`, `post` per element as `Mechanics.buffs` gives them, the status row its
        hits apply (a stateInfo 152/153 row's `atkOccurrenceSpEffectId`), its duration and the uptime choices [(uptime, recasts, time factor)]."""
        if opt is None:
            return None
        key = opt["rows"]
        if key not in self._eff:
            hand = self.hand_effect(key, "left")
            durs = [self.buf.sp[i]["effectEndurance"] for i in key]
            dur = -1.0 if -1.0 in durs else max(durs)
            # Uptime by `Buffs.uptime`, the rule the buff kits use: a timed row covers duration /
            # fight per cast, a next-hit row (stateInfo 384/385: Royal Knight's Resolve 1703,
            # Determination 1693) one of that fight point's landed hits per cast. Recasts: as many
            # as reach full uptime, at most what one FP bar and the cerulean flasks pay for after
            # the free first cast, each drink charged its frames (`refill`). The recasts are
            # `er-mechanics-buffs.recast_plan`'s, averaged over the fight points.
            hits = self.mech.fight["hits"]
            one_hit = any(self.buf.one_hit(i) for i in key)
            lapse = self.mech.buf.recast_plan(dur, self.fight_s, uses=1, hits=hits, one_hit=one_hit)
            ups = [(lapse["uptime"], 0, 1.0)]
            if ups[0][0] < 1.0:
                plan = self.mech.buf.recast_plan(dur, self.fight_s, uses=opt.get("uses"),
                                                 cast_frames=opt.get("cast") or 0.0, hits=hits, one_hit=one_hit,
                                                 refill=opt.get("refill"))
                if plan["recasts"] > 0:
                    ups.append((plan["uptime"], round(plan["recasts"], 4), plan["time_factor"]))
            self._eff[key] = {**hand, "duration": dur, "uptimes": ups}
        return self._eff[key]

    def hand_effect(self, rows: tuple, hand: str) -> dict:
        """`rows` through one hand's gate (`Buffs.attack_context`): `pre`, `flat`, `post` per
        element, `gated` (any row passed), and `status_row`: a hit's status from an attacker buff
        is the first hand-applicable row with stateInfo 152/153's `atkOccurrenceSpEffectId`
        (`FUN_1404f71e0`, status.md section 1)."""
        ctx = self.buf.attack_context(list(rows), pvp=True, hand=hand, apply_stack=False)
        st, sp = self.mech.st, self.mech.st_t.sp
        passed = {i for i, _ in ctx["entries"]}
        status_row = None
        for i in rows:
            c = sp[i]["atkOccurrenceSpEffectId"] if i in passed and i in sp and \
                sp[i]["stateInfo"] in st.GREASE_BUFF_STATES else -1
            if c > 0 and c in sp and st.row_status(sp[c]) and status_row is None:
                status_row = c
        return {"pre": ctx["ar_rate"], "flat": ctx["flat_add"],
                "post": {e: ctx["pvp_rate"][e] * ctx["atk_rate"][e] for e in ELEMENTS},
                "stamina": ctx["stamina_rate"],
                "rate_points": {k: v for k, v in ctx["rate_points"].items() if v},
                "status_row": status_row, "gated": bool(passed)}

    def right_uptime(self, element: str) -> float:
        """Uptime of the right hand's grease of `element`, recast over the fight
        (`Mechanics.grease_plan`; its time factor is charged on the row's score)."""
        return self.mech.grease_plan(self.tier, element)["uptime"]

    def body(self) -> dict | None:
        """The archetype's buff kits on an off-hand hit and the defenders' kits (`Mechanics.buffs`
        without the weapon's skill), shared by every left weapon: the attribute adds of a kit are
        not turned into AR for the off-hand (`INFERRED`, small)."""
        if self._body is None:
            m = self.mech
            e = m.buf_m.expected_attack(m.att_kits, False, None, alternatives=[], drop_skill_weapon_buffs=False,
                                        **m.fight)
            self._body = {"pre": e["pre"], "post": e["post"], "flat": e["flat"], "def": m.def_buffs["factor"],
                          "stamina": e["stamina"], "alternatives": []}
        return self._body

    @staticmethod
    def buff_dict(base: dict | None, eff: dict | None, up: float, defense: dict) -> dict | None:
        """`slot_hit` `buff` for one off-hand hit: `base` (the body kits, or None) with the option
        `eff` held `up` of the fight. `stamina` is the `staminaAttackRate` product a guarded hit's
        stamina damage takes (Determination 3, Royal Knight's Resolve 4, `slot_hit`)."""
        if eff is None:
            return base
        b = base or {"pre": dict.fromkeys(ELEMENTS, 1.0), "post": dict.fromkeys(ELEMENTS, 1.0),
                     "flat": dict.fromkeys(ELEMENTS, 0.0), "def": defense, "stamina": 1.0, "alternatives": []}
        return {**b, "pre": {e: b["pre"][e] * (1.0 + up * (eff["pre"][e] - 1.0)) for e in ELEMENTS},
                "post": {e: b["post"][e] * (1.0 + up * (eff["post"][e] - 1.0)) for e in ELEMENTS},
                "flat": {e: b["flat"][e] + up * eff["flat"][e] for e in ELEMENTS},
                "stamina": b.get("stamina", 1.0) * (1.0 + up * (eff.get("stamina", 1.0) - 1.0))}

    def status(self, name: str, aff: str, level: int, stats: dict, l1: dict, status_row, up: float,
               gap, react, stagger: float) -> dict:
        """`er-mechanics-status.status_expected` of the off-hand L1 #1: the weapon's own status
        and the option's status row; with `up` < 1 each status's `hp_per_hit` is the uptime blend
        of the two (with and without the row). Cached on what it reads."""
        st = self.mech.st
        ws = st.weapon_status(self.mech.st_t, name, aff, level, stats, two_handed=False, pvp=True)
        if not ws["sources"] and status_row is None:
            return {}
        key = (ws["id"], aff, status_row, ws["req"], round(ws["finals"]["bleed"]["arcane"], 4),
               round(ws["finals"]["poison"]["arcane"], 4), round(up, 4), gap, react, round(stagger, 4))
        if key not in self._status:
            def run(row):
                if not ws["sources"] and row is None:
                    return {}
                return st.status_expected(self.mech.st_t, ws, l1, self.mech.st_dfs, grease=row, gap=gap,
                                          react=react, stagger=stagger, eng_s=st.ENGAGEMENT_SECONDS)
            on = run(status_row)
            if status_row is not None and up < 1.0:
                off = run(None)
                on = {k: {**v, "hp_per_hit": up * v["hp_per_hit"] + (1.0 - up) * off.get(k, {}).get("hp_per_hit", 0.0)}
                      for k, v in on.items()}
            self._status[key] = on
        return self._status[key]


def _setup_left_choice(setup, model, name: str, lid: int, l1: dict, stats: dict, tables, pvp, reg, defenders,
                       spear, talismans, poises) -> tuple | None:
    """`--setup`: the off-hand's (affinity, weapon buff, uptime) whose L1 #1 is worth the most at
    the row's stats: damage with the archetype and defender kits (`SetupBuffs.body`) and the
    option held for its uptime, plus `SCORE_STATUS_WEIGHT` x its status HP per hit (the weapon's
    own status included), times the recast time factor. Returns (value, affinity, AR, option,
    effect, (uptime, recasts, time factor), buff dict, status, level, {option name: the same tuple
    for that option at its best affinity})."""
    st = setup.mech.st
    react = tuple(st._fa().reaction_level(model.fa, l1["atk_row"], b) for b in (False, True))
    body = setup.body() if not setup.mech.no_buffs else None
    defense = setup.mech.def_buffs["factor"]
    best, by_opt = None, {}
    for i, aff in enumerate(AR.AFFINITIES):
        aid = lid + i * 100
        if aid not in tables.weapons:
            continue
        level = tables.max_level(tables.weapons[aid]["reinforceTypeId"])
        r = AR.attack_rating(tables, name, aff, level, stats, False)
        ar = {el: r["damage"].get(el, {}).get("total", 0.0) for el in ELEMENTS}
        for opt in setup.options(lid, i, level):
            eff = setup.effect(opt)
            if eff is not None and not eff["gated"]:
                continue
            if eff is not None and eff.get("rate_points"):
                # A left roar's `changeStrengthPoint` 5 (843, 1683, 1813) on this weapon's own AR
                # (attack-rating.md section 7), folded into `pre` before the uptime blend.
                rp = AR.attack_rating(tables, name, aff, level, stats, False, eff["rate_points"])
                eff = {**eff, "pre": {el: eff["pre"][el] * (rp["damage"].get(el, {}).get("total", 0.0) / ar[el]
                                                            if ar[el] else 1.0) for el in ELEMENTS}}
            for up in (eff["uptimes"] if eff else [(1.0, 0, 1.0)]):
                buff = setup.buff_dict(body, eff, up[0], defense)
                h = slot_hit(pvp, reg, lid, l1, ar, defenders, None, spear, talismans, buff=buff)
                stag = sum(p < h["poise"] for p in poises) / len(poises) if poises else 0.0
                status = setup.status(name, aff, level, stats, l1, eff and eff["status_row"], up[0],
                                      h.get("next"), react, stag)
                v = (h["dmg"] + SCORE_STATUS_WEIGHT * sum(s["hp_per_hit"] for s in status.values())) * up[2]
                k = opt["name"] if opt else "none"
                cand = (v, aff, ar, opt, eff, up, buff, status, level)
                if k not in by_opt or v > by_opt[k][0]:
                    by_opt[k] = cand
                if best is None or v > best[0]:
                    best = cand
    return best and best + (by_opt,)


def paired_loop(combo, model, lefts: dict, base_id: int, slots: dict, stats: dict, tables, pvp, reg, defenders,
                spear, talismans, poises, opening: float, npool=None, setup=None, spill=None,
                grease_tf: float = 1.0) -> dict | None:
    """`--paired-loop` for one one-handed row: each of `lefts` in the left hand at the affinity
    whose L1 #1 hits hardest at the row's stats, its cross-hand links with roll-catch
    (`er-mechanics-combo.paired_slots` `catch`), scored by `moveset_score` per opening; the best
    is kept. Returns the kept slots and pairing function, every candidate's score, and the
    running R1 -> L1 engagement (`run_r1`: link chances, where the defender escapes).

    With `setup` (`SetupBuffs`, `--setup`) each left also carries its own weapon buff, chosen with
    its affinity by `_setup_left_choice`, its hits take the body and defender kits and its status
    (own and buff) is scored; the candidate score is times the buff's recast time factor, and the
    unpaired moveset score is returned as `unpaired`. With `spill` (`score_row`'s right-hand
    spill) a left skill whose cast also puts a category-162 row on the right hand is also tried
    with that row on the right instead of the grease: the skill whose row raises the unpaired
    moveset most, and the L1-best option when it has one."""
    best, per = None, {}
    unpaired_memo = {}

    def unpaired(s):
        if id(s) not in unpaired_memo:
            unpaired_memo[id(s)] = (s, MOVESET.moveset_score({**s, **jump_openers(s, npool)}, slot_score,
                                                             entry_frames)["score"])
        return unpaired_memo[id(s)][1]

    for name, info in lefts.items():
        lid = info.get("wid") or tables.find_weapon(name, "Standard")
        l1 = model.offhand(lid).get("left_1")
        if l1 is None or combo.left_mode(model.reg, base_id, lid) != "offhand":
            continue
        # Alternatives: (choice (value, affinity, AR), left buff dict, left status, right slots,
        # time factor, extra fields).
        alts = []
        if setup is not None:
            c = _setup_left_choice(setup, model, name, lid, l1, stats, tables, pvp, reg, defenders, spear,
                                   talismans, poises)
            if c is None:
                continue
            by_opt = c[-1]

            def alt(t, right, right_buff):
                # A right hand that keeps the sweep's grease also pays its uses' time
                # (`grease_tf`); one that holds the left skill's 162 row has no grease to recast.
                v, aff, ar, opt, eff, up, lbuff, lstatus, _ = t
                return ((v, aff, ar), lbuff, lstatus, right, up[2] * (grease_tf if right_buff == "grease" else 1.0),
                        {"buff": opt and opt["name"], "buff_kind": opt and opt["kind"], "uptime": round(up[0], 4),
                         "recasts": up[1], "time_factor": round(up[2], 4), "right_buff": right_buff,
                         "status": {k: round(s["hp_per_hit"], 2) for k, s in lstatus.items()},
                         # L1 #1 value per weapon-buff option (its best affinity), the top few.
                         "options": {k: [round(x[0], 1), x[1]] for k, x in
                                     sorted(by_opt.items(), key=lambda kv: -kv[1][0])[:6]}})
            alts.append(alt(c[:-1], slots, "grease"))
            if spill is not None:
                base_u = unpaired(slots) * grease_tf
                gains = []
                for t in by_opt.values():
                    sp_slots = spill(t[3], t[5][0])
                    if sp_slots is not None:
                        gains.append((unpaired(sp_slots) - base_u, t, sp_slots))
                gains.sort(key=lambda g: -g[0])
                tried = set()
                for g, t, sp_slots in gains[:1] + [x for x in gains if x[1][3] is c[3]]:
                    if g > 0 and t[3]["name"] not in tried:
                        tried.add(t[3]["name"])
                        alts.append(alt(t, sp_slots, t[3]["name"]))
        else:
            choice = None
            for i, aff in enumerate(AR.AFFINITIES):
                aid = lid + i * 100
                if aid not in tables.weapons:
                    continue
                r = AR.attack_rating(tables, name, aff, tables.max_level(tables.weapons[aid]["reinforceTypeId"]),
                                     stats, False)
                ar = {el: r["damage"].get(el, {}).get("total", 0.0) for el in ELEMENTS}
                d = slot_hit(pvp, reg, lid, l1, ar, defenders, None, spear, talismans)["dmg"]
                if choice is None or d > choice[0]:
                    choice = (d, aff, ar)
            alts.append((choice, None, None, slots, 1.0, {}))
        kept = None
        for choice, lbuff, lstatus, right, tf, extra in alts:
            def hit_fn(atk, lid=lid, ar=choice[2], buff=lbuff, status=lstatus):
                h = slot_hit(pvp, reg, lid, atk, ar, defenders, None, spear, talismans, buff=buff)
                h.pop("guard_part")
                h["stagger"] = sum(p < h["poise"] for p in poises) / len(poises) if poises else None
                if status is not None:
                    h["status"] = status
                return h

            def pf(s, lid=lid, hit_fn=hit_fn, c=info):
                return left_weapon_guard(combo.paired_slots(model, base_id, lid, s, hit_fn, catch=c), s)
            ms = pf(right)
            if ms is right:
                continue
            full = {**ms, **jump_openers(ms, npool)}
            score = MOVESET.moveset_score(full, slot_score, entry_frames, opening=opening)["score"] * tf
            if kept is None or score > kept[0]:
                kept = (score, choice, tf, extra, ms, pf, full)
        if kept is None:
            continue
        score, choice, tf, extra, ms, pf, full = kept
        run = None
        link = ((ms.get("run_r1") or {}).get("combos") or [{}])[0]
        if link.get("next") == "left_1":
            stag = ms["run_r1"].get("stagger") or 0.0
            b = link.get("on_break") or {}
            # The chance before roll-catch: L1 #1 itself lands.
            p0 = (b.get("p_roll") or 0.0) if b.get("verdict") == "roll-out-able" else \
                {"true": 1.0, "tie": 0.5}.get(b.get("verdict"), 0.0)
            eng = MOVESET.engagement(full, "run_r1", slot_score, entry_frames("run_r1"), opening)
            run = {"verdict": b.get("verdict"), "gap": b.get("gap"), "escape": b.get("escape"), "stagger": stag,
                   "p_l1_lands": stag * p0, "p_caught_on_l1_2": stag * ((b.get("p") or 0.0) - p0),
                   "p_escapes": 1.0 - stag * (b.get("p") or 0.0),
                   "engagement": eng and {"depth": eng["depth"], "score": eng["score"], "dmg": eng["dmg"],
                                          "commit": eng["detail"]["commit"]}}
        per[name] = {"score": score, "aff": choice[1], "l1_dmg": round(choice[0], 1), "run_r1": run, **extra}
        if best is None or score > best[0]:
            best = (score, name, ms, pf, tf)
    if best is None:
        return None
    out = {"left": best[1], "candidates": per, "ms_slots": best[2], "pair_fn": best[3],
           "roll_catch": {n: v["p"] for n, v in lefts.items()}}
    if setup is not None:
        out["time_factor"] = best[4]
        # The row's moveset as a run without a left hand scores it (no per-opening credit).
        out["unpaired"] = unpaired(slots) * grease_tf
    return out


def apply_relative_speed(results: list[dict], tables, tau: float, measure: str = "string_dps") -> None:
    """`--relative-speed`: each row's `moveset.score` (skill term included) times
    `relative_speed` of its `speed_measures` `measure` against the fastest row of its `wepType`
    and grip in `results`. The absolute speed inside `slot_score` is left as it is. The class
    minimum is taken over the rows of this run, so a `--weapon` subset has its own."""
    fastest = {}
    keyed = []
    for r in results:
        wid = tables.find_weapon(r["weapon"], "Standard")
        key = (tables.weapons[wid]["wepType"], r["two"])
        s = speed_measures(r["slots"]).get(measure)
        keyed.append((r, key, s))
        if s is not None:
            fastest[key] = min(fastest.get(key, s), s)
    for r, key, s in keyed:
        f = relative_speed(s, fastest.get(key), tau)
        r["moveset"]["f_speed"] = f
        r["moveset"]["speed_measure"] = measure
        r["moveset"]["score"] = r["moveset"]["score"] * f


def left_weapon_guard(paired: dict, slots: dict) -> dict:
    """`paired` (`er-mechanics-combo.paired_slots` of `slots`) with every slot's own guard at 0:
    a left hand holding an off-hand weapon (the only kind `paired_slots` pairs) cannot raise a
    guard one-handed (`er-mechanics-powerstance-guard.GUARD_LEFT_ONE_HAND`), so the one-handed
    row's left shield (`best_left_shield`) is gone. `slots` itself when nothing was paired."""
    if paired is slots:
        return slots
    return {k: ({**v, "guard_own": 0.0} if v.get("guard_own") is not None else v) for k, v in paired.items()}


def best_slot(slots: dict) -> str | None:
    scored = [(s["score"]["score"], k) for k, s in slots.items()
              if s.get("score") and k not in SCORE_BEST_SLOT_EXCLUDED]
    return max(scored)[1] if scored else None


def _top_status(status: dict | None) -> str | None:
    """The status worth the most HP per landed hit, then the fewest engagements to proc on a
    non-carrier; None when none ever procs."""
    vals = [(v["hp_per_hit"], -v["engagements_to_proc"]["non_carrier"], k) for k, v in (status or {}).items()
            if v.get("engagements_to_proc", {}).get("non_carrier")]
    return max(vals)[2] if vals else None


def _status_cell(status: dict | None) -> str:
    """Engagements to the first proc on a non-carrier for `_top_status`, e.g. `scarl 3.2`; `-`."""
    k = _top_status(status)
    n = k and status[k]["engagements_to_proc"]["non_carrier"]
    return f"{k[:5]} {n:.1f}" if n else "-"


def _carrier_cell(status: dict | None) -> str:
    """Share of the bolus carriers `_top_status` procs within one engagement, in percent."""
    k = _top_status(status)
    p = k and status[k]["proc_share"]["carrier"]
    return "-" if p is None or not k else f"{100 * p:.0f}"


def build_for(row: dict, greased_only: bool = False) -> dict | None:
    """The sweep row's highest-damage configuration, or its best greased one; None when the row
    has none of the asked kinds (a fixed-affinity weapon under `greased_only`)."""
    opts = []
    for kind in ("greased", "quality") if greased_only else ("elemental", "greased", "quality"):
        c = row.get(kind)
        if c:
            opts.append({"dmg": c["dmg"], "aff": c.get("aff", "Quality"), "grease": c.get("grease"),
                         "stats": c["stats"]})
    return max(opts, key=lambda c: c["dmg"]) if opts else None


def weapon_mix(slots: dict) -> dict:
    """A moveset's damage-type make-up: each slot weighs one, whatever its hit count.

    `types` is the share of slots whose physical type is slash/strike/pierce/standard (a
    multi-hitbox slot is counted by its main hitbox); `elements` is each element's share of the
    moveset's summed expected damage; `avg`/`avg_ctr` are the mean over slots of expected and
    counter-hit damage, `avg_med` of damage against the median defender."""
    n = len(slots)
    if not n:
        return {}
    types = Counter(s["phys_type"] for s in slots.values())
    el_sum = {el: sum(s["by_type"][el] for s in slots.values()) for el in ELEMENTS}
    tot = sum(el_sum.values()) or 1.0
    return {"slots": n, "types": {t: types.get(t, 0) / n for t in (*PHYS_TYPES, "none")},
            "elements": {el: v / tot for el, v in el_sum.items()},
            "avg": sum(s["dmg"] for s in slots.values()) / n,
            "avg_med": sum(s["med"] for s in slots.values()) / n,
            "avg_ctr": sum(s["ctr"] for s in slots.values()) / n}


# --------------------------------------------------------------------------------------------
# self test

def selftest() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok   " if cond else "FAIL ") + msg)
        ok = ok and bool(cond)

    grid = np.array([0.0, 1.0, 37.0, 100.0, 180.0, 400.0])
    for atk in (0.0, 5.0, 12.0, 90.0, 100.0, 170.0, 250.0, 555.0, 900.0):
        got = defense_curve(atk, grid)
        want = [DEF.defense_curve(atk, d) for d in grid]
        check(np.allclose(got, want, rtol=1e-6, atol=1e-6), f"array curve equals defense_curve at attack {atk}")
    reg = ATK.Regulation(None)
    sp = reg.counter_speffects.get(COUNTER_SPEFFECT, {})
    check(sp.get("pierce") and abs(sp["pierce"] - 1.15) < 1e-6 and all(
        abs(sp[t] - 1.0) < 1e-6 for t in ("slash", "strike", "standard", *ELEMENTS[1:])),
        "SpEffect 45 (stateInfo 110): pierce 1.15, every other type 1.0 (regulation)")
    f = counter_factors(reg, "pierce", True)
    check(abs(f["physical"] - 1.15 * 1.15) < 1e-6 and f["fire"] == 1.0,
          "Spear Talisman scales only a counter factor above 1 (EXE 0x1404f5310 guard)")
    one = {"defenses": {el: 100 for el in ELEMENTS}, "absorption": {k: 20.0 for k in ABSORB_KEY.values()},
           "poise": {"original": 50}}
    d = Defenders([one, one])
    hit = corpus_hit({"physical": 300.0}, "slash", d, None, counter_factors(reg, "slash", False))
    want = DEF.defense_curve(300.0, 100.0) * 0.8
    check(abs(hit["mean"] - want) < 1e-6 and abs(hit["counter"] - want) < 1e-6,
          "a slash hit on identical defenders equals the scalar model and gets no counter bonus")

    # The mechanics columns, through the sibling modules.
    tables = AR.Tables(None)
    mech = Mechanics(reg, tables, CACHE / "builds.jsonl", 150, 10)

    def columns(name, aff, stats, two):
        wid = tables.find_weapon(name, aff)
        level = tables.max_level(tables.weapons[wid]["reinforceTypeId"])
        attacks = ATK.weapon_attacks(reg, wid, "both" if two else "one", level)
        return mech.slots(name, aff, level, stats, two, wid, attacks), attacks, level

    gs, gs_atk, gs_level = columns("Greatsword", "Heavy", {"str": 60, "dex": 12, "int": 9, "fth": 9, "arc": 9}, True)
    r1 = gs.get("r1_1", {})
    check(r1.get("parryable") is False, "Greatsword 2H R1 is not parryable (crits parry_exposure, JumpTable 5)")
    check((r1.get("reach") or 0) > 0 and (r1.get("weapon_reach") or 0) > 0,
          f"Greatsword 2H R1 reach is positive ({r1.get('reach')} m, weapon {r1.get('weapon_reach')} m)")
    check(r1.get("adv") is not None and r1.get("adv_stagger") is not None and r1["adv_stagger"] > r1["adv"],
          f"Greatsword 2H R1 frame advantage is better on a stagger ({r1.get('adv_stagger')}) than on a "
          f"held poise ({r1.get('adv')})")
    first = next(x for x in gs_atk if x["slot"] == "2h_r1_1")["hit_windows"][0][0]
    check(r1.get("first_hit_real") is not None and abs(r1["first_hit_real"] - first) <= 0.15,
          f"reach and attacks modules agree on the first real hit frame ({r1.get('first_hit_real')} vs {first})")
    check(not any(s.get("status") for s in gs.values()), "Heavy Greatsword has no status build-up")
    uchi, _, _ = columns("Uchigatana", "Standard", {"str": 16, "dex": 40, "int": 9, "fth": 9, "arc": 9}, False)
    bleed = (uchi.get("r1_1", {}).get("status") or {}).get("bleed") or {}
    eng = (bleed.get("engagements_to_proc") or {}).get("non_carrier")
    check(eng and 1 <= eng < mech.st.ENGAGEMENT_LIMIT,
          f"Uchigatana R1 bleed reaches a non-carrier in a finite number of engagements ({eng})")
    check(bleed.get("hits_per_engagement") == 1.0 and (bleed.get("proc_share") or {}).get("carrier") == 0.0,
          "Uchigatana 1H R1 #2 is not a true combo on a held poise, so no bolus carrier is ever bled "
          "by one 45 hit")
    uchi_brk, _, _ = columns("Uchigatana", "Standard", {"str": 16, "dex": 40, "int": 9, "fth": 9, "arc": 9}, True)
    combos = uchi_brk.get("r1_1", {}).get("combos") or []
    brk = mech.st.combo_land(combos[0], 1.0) if combos else None
    check(brk == 0.0, "Uchigatana 2H R1 #1 -> #2 lands on nobody even on a broken poise (gap 18 vs roll "
                      "at 10, frame-advantage selftest): a one-hit engagement")
    late = [(k, c) for cols in (gs, uchi, uchi_brk) for k, s in cols.items() for c in s.get("combos") or []
            for side, lvl in (("on_intact", 0), ("on_break", 1))
            if (c.get(side) or {}).get("verdict") == "true"
            and c[side]["gap"] >= mech.st.cure_ready((s.get("reaction") or (0, 0))[lvl])]
    check(not late, f"every true combo lands before a bolus could ({late[:2]})")
    st = dict(bleed, hp_per_hit=100.0)
    check(abs(slot_score({"dmg": 300.0, "roll": 30.0, "next": 40.0, "status": {"bleed": st}})["status_hp"] - 100.0)
          < 1e-9, "slot_score adds the status model's expected hp_per_hit")
    sk = mech.skill("Greatsword", "Heavy", gs_level, {"str": 60, "dex": 12}, True,
                    tables.find_weapon("Greatsword", "Standard"), Defenders([one]).median)
    check(sk and sk.get("best_hit", 0) > 0 and sk.get("fp", 0) > 0,
          f"Greatsword skill has a damaging hit and an FP cost ({sk})")

    # Buff and skill terms (buffs.md section 10, ashes-of-war.md section 13).
    pvp = PvpTables()
    gs_stats = {"str": 60, "dex": 12, "int": 9, "fth": 9, "arc": 9}
    gs_ar = AR.attack_rating(tables, "Greatsword", "Heavy", gs_level, gs_stats, True)
    ar_by = {el: gs_ar["damage"].get(el, {}).get("total", 0.0) for el in ELEMENTS}
    gs_base = tables.find_weapon("Greatsword", "Standard")
    r1 = next(x for x in gs_atk if x["slot"] == "2h_r1_1")
    d2 = Defenders([one, one])
    plain = slot_hit(pvp, reg, gs_base, r1, ar_by, d2)["dmg"]
    unit = {el: 1.0 for el in ELEMENTS}
    zero = {el: 0.0 for el in ELEMENTS}
    cut = slot_hit(pvp, reg, gs_base, r1, ar_by, d2, buff={"pre": unit, "post": unit, "flat": zero,
                                                           "def": {k: 0.9 for k in ABSORB_KEY}})["dmg"]
    check(abs(cut / plain - 0.9) < 1e-6, f"a defender buff of 0.9 scales the hit by 0.9 after defense ({cut / plain:.4f})")
    up = slot_hit(pvp, reg, gs_base, r1, ar_by, d2, buff={"pre": {el: 1.1 for el in ELEMENTS}, "post": unit,
                                                          "flat": zero, "def": {k: 1.0 for k in ABSORB_KEY}})["dmg"]
    check(up > plain, "an attacker AR buff raises the hit")
    choice = mech.skill_choice(gs_base, "Heavy", gs_level)
    bf = mech.buffs("Greatsword", "Heavy", gs_level, gs_stats, True, gs_base, choice, True)
    check(bf["post"]["physical"] > 1.0 and bf["def"]["standard"] < 1.0,
          f"corpus buffs: attacker physical x{bf['post']['physical']:.3f}, defenders x{bf['def']['standard']:.3f}")
    term = mech.skill_term("Greatsword", "Heavy", gs_level, gs_stats, True, gs_base, choice, 0.0, d2, reg, bf,
                           lambda o: mech.skill_slot_extra(o, None, None, None))
    scored = [o for o in term["options"] if o.get("score")]
    check(scored and term["value"] > 0 and term["score"] > 0,
          f"skill term: against a zero base every damaging skill is a gain ({[o['name'] for o in scored]})")
    best = term["best"] or {}
    check(term["value"] >= term["value_corpus"] > 0 and best.get("reach_measured") and best.get("reach"),
          f"the best mountable skill ({best.get('name')}, reach {best.get('reach')} m {best.get('reach_source')}) "
          f"is worth at least the corpus mix ({term['value']:.0f} >= {term['value_corpus']:.0f}) and carries its own reach")
    check(any(mech.skill_slot_extra(o, None, None, None).get("adv") is not None
              for o in term["options"] if o.get("hits")),
          "a skill's last hit gets a frame advantage from its own cancel frames")
    high = mech.skill_term("Greatsword", "Heavy", gs_level, gs_stats, True, gs_base, choice, 1e6, d2, reg, bf,
                           lambda o: mech.skill_slot_extra(o, None, None, None))
    check(high["value"] == 0.0 and high["score"] == 1e6, "a skill weaker than the moveset adds nothing")
    eslots = {"r1_1": {"dmg": 500.0, "startup": 16.0, "reach": 4.0,
                       "exchange": {"win": 0.2, "loss": 0.1, "p_second": 0.5, "strike_frame": 18.0},
                       "score": {"score": 900.0, "rate": 600.0, "commit": 30.0}}}
    eng = skill_engagement(eslots, {"best_opener": "r1_1"}, None, None)
    check(eng and abs(eng["numerator"] - 600.0 * 30.0 / SCORE_FPS) < 1e-9 and eng["strike"] == 18.0
          and eng["exchange"]["loss"] == 0.1,
          f"skill_engagement: the best opener's HP per engagement is rate x commit / fps ({eng})")

    base = {"dmg": 600.0, "roll": 30.0, "next": 40.0, "reach": SCORE_REACH_REF_M, "stagger": 0.0,
            "adv": 0.0, "adv_stagger": 0.0, "parryable": False, "status": {}}
    s0 = slot_score(base)["score"]
    check(abs(s0 - 600.0 / 30.0 * SCORE_FPS) < 1e-9, "neutral factors leave damage per second of commitment")
    check(slot_score({**base, "parryable": True, "crit_hp": 0.0, "parry_hp": 30.0})["score"] < s0,
          "a parryable slot pays the corpus riposte")
    check(slot_score({**base, "parryable": False, "parry_hp": 30.0})["score"] == s0,
          "an unparryable slot pays no riposte")
    check(slot_score({**base, "crit_hp": 30.0})["score"] > s0, "crit HP scores higher")
    check(slot_score({**base, "coverage": 1.2})["score"] > s0 and slot_score(base)["f_cover"] == 1.0,
          "wider coverage scores higher, and none leaves the score unchanged")
    check(slot_score({**base, "reach": 2 * SCORE_REACH_REF_M})["score"] > s0, "more reach scores higher")
    check(slot_score({**base, "adv": 10.0, "adv_stagger": 10.0})["score"] > s0, "frame advantage scores higher")
    check(slot_score({**base, "stagger": 1.0})["score"] > s0, "a staggering hit scores higher")
    check(slot_score({**base, "guard": {"factor": 0.9}})["score"] < s0, "a hit guards absorb scores lower")
    ex = slot_score({**base, "exchange": {"factor": 0.95 * 0.9, "f_exchange": 0.95, "f_stamina": 0.9,
                                          "f_startup": 0.8, "f_hyper": 0.95 / 0.8}})
    check(abs(ex["score"] - s0 * 0.95 * 0.9) < 1e-6 and ex["f_exchange"] == 0.95 and ex["f_stamina"] == 0.9,
          "the exchange factor multiplies once (f_startup x f_hyper is not applied again)")
    ne = slot_score({**base, "exchange": {"f_exchange": 0.95, "f_stamina": 1.0}, "neutral": {"f_neutral": 1.1}})
    check(abs(ne["score"] - s0 * 1.1) < 1e-6 and ne["f_contest"] == 1.1,
          "the neutral contest replaces the 2.5 m exchange (docs/er-mechanics/neutral.md)")
    ni = slot_score({**base, "exchange": {"f_exchange": 0.95, "f_stamina": 1.0}, "interrupt": {"f_interrupt": 0.9},
                     "neutral": {"f_neutral": 1.1}})
    check(abs(ni["f_contest"] - 0.9 * 1.1 / 0.95) < 1e-9,
          "under --interrupt the neutral contest's ratio to the 2.5 m exchange scales the interrupt factor")
    sk = slot_score({**base, "react": {"land": 0.4, "react_share": 0.5},
                     "exchange": {"f_exchange": 0.8, "f_stamina": 1.0}})
    check(abs(sk["hit_worth"] - (0.5 * 0.8 + 0.4 - 0.5)) < 1e-9,
          "a skill option carries its landed damage and reaction share like a slot: the committed half meets "
          "the contest")
    w = slot_score({**base, "f_weight": 0.9})
    check(abs(w["score"] - 0.9 * s0) < 1e-6 and w["f_weight"] == 0.9 and slot_score(base)["f_weight"] == 1.0,
          "the sweep's equip-weight factor multiplies the slot score, and none leaves it unchanged")
    check(build_for({"elemental": {"dmg": 1.0, "aff": "Standard", "stats": {}}, "greased": None, "quality": None},
                    greased_only=True) is None, "a fixed-affinity row has no greased build (skipped, no crash)")
    check(slot_score({**base, "roll": None, "next": None}) is None, "no recovery frame, no score")
    check(slot_score(base, entry_frames("2h_roll_r1"))["score"] < s0, "a rolling attack pays for its roll")
    check(entry_frames("r1_1") == 0.0 and entry_frames("2h_crouch_r1") == 8.0, "entry frames by slot key")
    check(best_slot({"a": {"score": {"score": 1.0}}, "b": {"score": {"score": 2.0}}, "c": {"score": None},
                     "counter": {"score": {"score": 9.0}}}) == "b",
          "best_slot picks the highest score and skips the guard counter")
    ones = dict.fromkeys(ELEMENTS, 1.0)
    eff = {"pre": {**ones, "physical": 1.15}, "post": {**ones, "physical": 1.4},
           "flat": {**dict.fromkeys(ELEMENTS, 0.0), "lightning": 85.0}}
    bd = SetupBuffs.buff_dict(None, eff, 0.5, {"standard": 1.0})
    check(abs(bd["pre"]["physical"] - 1.075) < 1e-9 and abs(bd["post"]["physical"] - 1.2) < 1e-9
          and abs(bd["flat"]["lightning"] - 42.5) < 1e-9 and bd["def"] == {"standard": 1.0},
          "a left weapon buff held half the fight adds half of each factor and half its flat attack")
    check(SetupBuffs.buff_dict({"pre": ones}, None, 1.0, {}) == {"pre": ones},
          "no left weapon buff leaves the body-kit buff as it is")
    # A guarded hit's stamina damage takes the attacker's staminaAttackRate (info+0x28,
    # docs/er-mechanics/powerstance-guard.md section 3): Determination 3.0 held half the fight is
    # 2.0, and Hammer Talisman's 1.4 reaches the guard part through the talisman list.
    bd = SetupBuffs.buff_dict(None, {**eff, "stamina": 3.0}, 0.5, {"standard": 1.0})
    check(abs(bd["stamina"] - 2.0) < 1e-9, "a left Determination held half the fight doubles the guard stamina rate")
    gp = slot_hit(pvp, reg, gs_base, r1, ar_by, d2, talismans=["Hammer Talisman"])["guard_part"]
    check(abs(gp["stamina_rate"] - 1.4) < 1e-6 and gp["hit"]["weapon"] == gs_base,
          f"Hammer Talisman carries x1.4 into the guard part ({gp['stamina_rate']:.4f}) with the weapon id")
    check(abs(slot_hit(pvp, reg, gs_base, r1, ar_by, d2, buff={**bf, "stamina": 4.0})["guard_part"]
              ["stamina_rate"] - 4.0) < 1e-9, "a buff's staminaAttackRate carries into the guard part")
    paired = {"r1_1": {"guard_own": 0.7, "dmg": 1.0}, "left_1": {"dmg": 1.0}}
    same = {"r1_1": {"guard_own": 0.7}}
    check(left_weapon_guard(paired, same)["r1_1"]["guard_own"] == 0.0 and left_weapon_guard(same, same) is same
          and "guard_own" not in left_weapon_guard(paired, same)["left_1"],
          "a left weapon in the pairing drops the one-handed row's own guard to 0; no pairing keeps it")
    # The fight the buffs cover (user, 2026-10-01): 3 to 5 minutes, sampled; the engagement
    # spacing and the landed hits do not move with it.
    buf, st = _sibling("er-mechanics-buffs"), _sibling("er-mechanics-status")
    fights = buf.FIGHT_SECONDS
    check(fights[0] == 180.0 and fights[-1] == 300.0, "the buffs' fight is 180..300 s by default")
    rec = [n for _, n in buf.recast_points(60.0, fights)]
    plan = buf.recast_plan(60.0, fights, uses=8, cast_frames=60.0)
    check(min(rec) >= 2 and max(rec) <= 4 and 2.0 <= plan["recasts"] <= 4.0 and plan["uptime"] == 1.0
          and plan["time_factor"] < 1.0,
          f"a 60 s buff (Cragblade) is recast 2..4 times over the fight (mean {plan['recasts']:.2f}), stays on, "
          f"and costs time (factor {plan['time_factor']:.4f})")
    rkr = buf.recast_plan(10.0, fights, uses=8, cast_frames=60.0, hits=5, one_hit=True)
    check(rkr["recasts"] == 4.0 and rkr["uptime"] == 1.0,
          "a next-hit buff (Royal Knight's Resolve) keeps its per-hit rule: one cast per landed hit")
    check(st.ENGAGEMENT_SECONDS == 5.0 and buf.ENGAGEMENT_SECONDS == 5.0
          and buf.fight_points(100.0, 200.0) != fights and st.ENGAGEMENT_SECONDS == 5.0,
          "the engagement spacing (5 s) is a separate constant from the fight length")
    # Landed hits per fight point (`Mechanics.set_fight`): a duel drinks no crimson flask, so 5
    # at every point; an invasion every flask, so the kill needs 22 and the clock holds the
    # short points below it; the corpus default mixes both by the window's tags; the status sims
    # read the same schedule.
    mech.set_fight(flasks="duel")
    duel = mech.fight["hits"]
    mech.set_fight(flasks="invasion")
    inv = mech.fight["hits"]
    mech.set_fight()
    mix = mech.fight
    sh = mech.fight_kind["shares"]
    check(set(duel) == {5} and max(inv) == 22 and min(inv) < 22 and mech.fight_kind["crimson"] == 10,
          f"duel 5 hits everywhere, invasion {min(inv)}..{max(inv)} with {mech.fight_kind['crimson']} flasks")
    check(0.0 < sh["duel"] < 0.5 and len(mix["fight_seconds"]) == len(mix["hits"]) > len(fights)
          and mech.st_dfs.fight_hits == mix["hits"] and mech.cerulean == 4,
          f"corpus mix: duel share {sh['duel']:.3f} over {len(mix['hits'])} points, the status sims on the same "
          f"schedule, 4 cerulean flasks")
    mech.set_fight(pinned=5)
    check(set(mech.fight["hits"]) == {5} and mech.st_dfs.fight_hits == mech.fight["hits"],
          "--fight-hits 5 pins every point, status sims included")
    mech.set_fight()
    print("selftest", "passed" if ok else "FAILED")
    return 0 if ok else 1


# --------------------------------------------------------------------------------------------
# output

def _fmt_dist(dist: dict) -> list[str]:
    head = f"  {'':<14}{'mean':>7}" + "".join(f"{'p' + str(q):>7}" for q in PERCENTILES)
    lines = [head]
    for k in (*PHYS_TYPES, "magic", "fire", "lightning", "holy"):
        v = dist[k]
        lines.append(f"  {k + ' abs%':<14}{v['mean']:>7.1f}" + "".join(f"{v[f'p{q}']:>7.1f}" for q in PERCENTILES))
    for el in ELEMENTS:
        v = dist[f"def_{el}"]
        lines.append(f"  {el + ' def':<14}{v['mean']:>7.1f}" + "".join(f"{v[f'p{q}']:>7.0f}" for q in PERCENTILES))
    return lines


_FORK: dict = {}


def _fork_call(i):
    return _FORK["fn"](i)


def _fork_map(fn, items, jobs: int) -> list:
    """`[fn(i) for i in items]` over `jobs` forked workers, in order. `fn` need not pickle (a
    closure is fine): the workers inherit it and every table built before the call, and only the
    items and the return values cross the pipe. `gc.freeze` keeps the collector from touching, and
    so copying, the inherited pages."""
    import gc
    import multiprocessing
    import types
    _FORK["fn"] = fn
    # The pool pickles `_fork_call` by module name. Loaded through `_sibling`, this module is not
    # in `sys.modules`, so a stand-in carrying this copy's `_fork_call` is put there (the workers
    # inherit it with the fork).
    # Another loaded copy registered under the same name cannot be pointed here: run serially.
    shim = sys.modules.get(__name__)
    if shim is not None and hasattr(shim, "_FORK") and shim._fork_call is not _fork_call:
        _FORK.clear()
        return [fn(i) for i in items]
    if shim is None:
        shim = sys.modules[__name__] = types.ModuleType(__name__)
    shim._fork_call = _fork_call
    gc.collect()
    gc.freeze()
    try:
        with multiprocessing.get_context("fork").Pool(jobs) as p:
            return p.map(_fork_call, list(items), chunksize=1)
    finally:
        gc.unfreeze()
        _FORK.clear()


def default_jobs() -> int:
    return max(1, (os.cpu_count() or 2) - 1)


def lower_priority() -> None:
    """Run at nice 19 and idle I/O class so a ranking never competes with the desktop.

    Forked workers inherit both settings.
    """
    try:
        os.setpriority(os.PRIO_PROCESS, 0, 19)
    except OSError:
        pass
    try:
        subprocess.run(["ionice", "-c", "3", "-p", str(os.getpid())], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
    except OSError:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rl", type=int, default=150)
    ap.add_argument("--sweep", type=Path, default=CACHE / "grease-sweep-dlc-drawstring-150-200.jsonl")
    ap.add_argument("--grease", choices=list(OPT.GREASES), default="dlc-drawstring")
    ap.add_argument("--slot", help="attack slot key to rank by (r1_1, r2_1c, run_r1, ...); default r1_1, "
                    "and for --sort score each weapon's best-scoring slot")
    ap.add_argument("--sort", choices=SORTS, default="damage",
                    help="damage = mean over the PvP corpus; median = vs the median defender")
    ap.add_argument("--weapon", help="show one weapon")
    ap.add_argument("--all-slots", action="store_true")
    ap.add_argument("--summary", action="store_true",
                    help="one line per weapon: damage-type mix and attack-type-aware average damage")
    ap.add_argument("--greased", action="store_true", help="use each weapon's best greased build")
    ap.add_argument("--spear-talisman", action="store_true", help="attacker wears Spear Talisman (counter)")
    ap.add_argument("--talismans", help="comma-separated talismans the attacker wears, e.g. "
                    "\"Two-Handed Sword Talisman,Claw Talisman\" (er-mechanics-talismans.py)")
    ap.add_argument("--window", type=int, default=10)
    ap.add_argument("--mirror", type=Path, default=CACHE / "builds.jsonl")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-buffs", action="store_true", help="leave out the corpus buff term (buffs.md section 10)")
    ap.add_argument("--no-skill", action="store_true", help="leave out the skill term (ashes-of-war.md section 13)")
    ap.add_argument("--no-exchange", action="store_true", help="leave out the exchange and stamina factors")
    ap.add_argument("--interrupt", type=Path,
                    help="an er-mechanics-interrupt.py --run result; its f_interrupt replaces f_exchange's "
                         "startup and hyperarmor part for the openers (docs/er-mechanics/interrupt.md section 6)")
    ap.add_argument("--no-react", action="store_true",
                    help="leave out the reaction dodge on every attack (ashes-of-war.md section 16a)")
    ap.add_argument("--no-buff-options", action="store_true",
                    help="score no skill as a buff; the corpus-weighted skill buffs go back into the base "
                    "(section 16b)")
    ap.add_argument("--no-follow-ups", action="store_true", help="score no skill follow-up press (section 16)")
    ap.add_argument("--no-neutral", action="store_true",
                    help="contest every attack at 2.5 m, not from the neutral game (docs/er-mechanics/neutral.md)")
    ap.add_argument("--disengage", nargs="?", const="sprint", choices=("sprint", "run"),
                    help="a dodge skill also scores the flask an escape away from the chaser buys over the "
                         "medium roll, the chaser following at this speed (default sprint; "
                         "er-mechanics-disengage, docs/er-mechanics/disengage.md)")
    ap.add_argument("--relative-speed", type=float, metavar="TAU",
                    help="multiply each row's score by exp(-(measure - its class's fastest) / TAU), class = "
                         "wepType within the same grip (`apply_relative_speed`; TAU in the measure's units; "
                         "450 for string_dps is read off the corpus, moveset.md section 7)")
    ap.add_argument("--relative-speed-measure", choices=SPEED_MEASURES, default="string_dps")
    ap.add_argument("--aggregate", metavar="KIND:X",
                    help="combine a row's openers another way (er-mechanics-moveset.AGGREGATE, moveset.md "
                         "section 8): standout:W blends the family matching mean toward the best opener, "
                         "openers:A is the matching mean over openers; default the family matching mean")
    ap.add_argument("--timing-mixup", nargs="?", const="crouch,entry", default="", metavar="PARTS",
                    help="crouch: crouch R1 from a held crouch; entry: rolling/backstep attacks may be "
                         "anticipated from their entry; both when given bare "
                         "(er-mechanics-timing-mixup, moveset.md section 9)")
    ap.add_argument("--paired-loop", action="store_true",
                    help="one-handed rows pair the best of PAIRED_LOOP_LEFTS (hatchets) at the affinity that "
                         "hits hardest at the row's stats, with roll-catch on the cross-hand links "
                         "(`paired_loop`, docs/er-mechanics/combo.md section 11)")
    ap.add_argument("--setup", action="store_true",
                    help="rank setups (implies --paired-loop): every left of --setup-lefts carries its own "
                         "weapon buff (a grease of --grease's tier, or a mountable skill's left-hand row), "
                         "chosen with its affinity, alongside the right's grease; one buff per hand "
                         "(SetupBuffs, docs/er-mechanics/combo.md section 11a)")
    ap.add_argument("--setup-lefts", default="all", metavar="WHICH",
                    help="with --setup: 'all' (every left weapon with an off-hand L1), 'hatchets' "
                         "(PAIRED_LOOP_LEFTS) or comma-separated names")
    ap.add_argument("--setup-left-buffs", default="all", metavar="WHICH",
                    help="with --setup: 'all', 'none' (the lefts get no weapon buff: body kits and status "
                         "only), or one option's name (e.g. 'Chilling Mist'), forced where it fits")
    ap.add_argument("--one-handed", action="store_true", help="score only one-handed sweep rows")
    ap.add_argument("--proc-opening", action="store_true",
                    help="add er-mechanics-proc-opening.slot_proc_opening to every slot's status HP (PROC_OPENING)")
    ap.add_argument("--paired-offhand", metavar="LEFT",
                    help="one-handed rows carry LEFT (Standard, max upgrade, the row's stats) in the left hand; "
                         "its off-hand L1 joins the engagements as a cross-hand follow-up "
                         "(er-mechanics-combo.paired_slots, docs/er-mechanics/combo.md), and those rows' "
                         "strings are scored per opening (er-mechanics-moveset.opening_credit)")
    ap.add_argument("--opponent-openers", choices=("families", "r1"), default=None,
                    help="what the pool throws at a dodger (section 16c); default families")
    ap.add_argument("--dodge-timing", choices=("react", "uniform"), default="react",
                    help="how a dodger times his dodge: by reaction (section 16c) or section 14a's uniform spread")
    ap.add_argument("--opponents-from", type=Path,
                    help="a full --sort score --json ranking to read the opponents' attacks from "
                    "(for a --weapon run, which otherwise has only its own rows)")
    ap.add_argument("--trades", choices=("zero", "priced"), default="zero",
                    help="what a trade (both hits land) is worth in the exchange and neutral contests: "
                         "zero, or priced by the two hits' damage, the opponent's read from --opponents-from "
                         "(else 388 for every one; EXCH.priced_net, docs/er-mechanics/exchange.md section 2a)")
    ap.add_argument("--trade-clamp", type=float, default=None, metavar="C",
                    help="with --trades priced: bound on the priced net (default EXCH.TRADE_CLAMP; inf: none)")
    ap.add_argument("--opponent-pool", choices=("r1", "families"), default="r1",
                    help="what the exchange and neutral contests throw at every scored attack: R1 #1 "
                         "per pool build, or (needs --opponents-from) each build's moveset family "
                         "openers at their use share read from that ranking "
                         "(NEUT.NeutralPool.from_results, docs/er-mechanics/neutral.md section 6)")
    ap.add_argument("--measure-all", action="store_true",
                    help="land, react and reach every mountable skill, not only the ones that can win")
    ap.add_argument("--build-aff", help="with --weapon: build this affinity instead of the sweep's")
    ap.add_argument("--build-stats", help="with --build-aff: str=..,dex=.. (other stats from the sweep row)")
    ap.add_argument("--build-grease", help="with --build-aff: a grease element, or none")
    ap.add_argument("--engagement-seconds", type=float, metavar="S",
                    help="neutral time between engagements (default er-mechanics-status.ENGAGEMENT_SECONDS, "
                         "INFERRED): status gauge refill, the spacing a visible skill buff must outlast, "
                         "sustain pacing, and the per-opening credit of --paired-offhand / --paired-loop "
                         "(sensitivity sweeps). Not the buffs' fight length: see --fight-seconds")
    ap.add_argument("--fight-seconds", type=float, nargs=2, metavar=("MIN", "MAX"), default=None,
                    help="fight length range, seconds, that buff uptime and recasts are averaged over "
                         "(default 180 300, er-mechanics-buffs.FIGHT_SECONDS_RANGE; sampled every "
                         "FIGHT_SAMPLE_STEP_S, equal weights). The landed hits at each point follow it "
                         "through their time bound (--flasks)")
    ap.add_argument("--flasks", default="corpus", metavar="KIND",
                    help="crimson flasks the defender drinks, for the landed hits per fight point "
                         "(er-mechanics-buffs.fight_hits; Mechanics.set_fight): duel = 0, invasion = every "
                         "crimson flask the window carries, corpus (default) = both mixed by the window's "
                         "planner tags (Duels vs Invasions + Co-op/Gank), or a number")
    ap.add_argument("--flask-eta", type=float, default=None, metavar="ETA",
                    help="share of a crimson drink that heals net (default er-mechanics-buffs.FLASK_ETA, 1, INFERRED)")
    ap.add_argument("--fight-hits", type=int, default=None, metavar="N",
                    help="pin the landed hits to N at every fight point, buffs and status sims alike "
                         "(5 = the fixed count before 2026-10-01)")
    ap.add_argument("--cerulean-flasks", default="corpus", metavar="N",
                    help="cerulean flasks (220 FP each at +12) the attacker spends on --setup left-hand skill "
                         "recasts, each drink charged FLASK_DRINK_FRAMES; corpus (default) = the window's "
                         "median, 0 = one FP bar")
    ap.add_argument("--fight-window", type=float, metavar="S",
                    help="stamina budget window, seconds (default er-mechanics-exchange.FIGHT_WINDOW_S, INFERRED)")
    ap.add_argument("--multi-hit-escape", action="store_true",
                    help="scale each later hitbox of a multi-hit attack by the chance it lands on a defender "
                         "the first hit did not stagger (extra_hit_land)")
    ap.add_argument("--sustain", choices=("window", "paced"),
                    help="add the defenders' healing over time to their HP (er-mechanics-buffs.sustain_factor, "
                         "buffs.md section 11): window = over the fixed fight, the same for every weapon; "
                         "paced = over the engagements each slot needs to kill")
    ap.add_argument("--jobs", type=int, default=default_jobs(),
                    help="score rows in this many forked workers (default: cores - 1); 1 is serial. "
                         "The output is the same either way")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    lower_priority()
    if a.selftest:
        return selftest()
    global TIMING_MIXUP, MULTI_HIT_ESCAPE, PROC_OPENING
    MULTI_HIT_ESCAPE = a.multi_hit_escape
    if a.setup:
        a.paired_loop = True
    if a.proc_opening:
        PROC_OPENING = _sibling("er-mechanics-proc-opening").slot_proc_opening
    TIMING_MIXUP = frozenset(p.strip() for p in a.timing_mixup.split(",") if p.strip())
    if TIMING_MIXUP - {"crouch", "entry"}:
        ap.error("--timing-mixup parts are crouch and entry")
    if a.aggregate:
        kind, _, x = a.aggregate.partition(":")
        if kind not in ("standout", "openers") or not x:
            ap.error("--aggregate wants standout:W or openers:A")
        MOVESET.AGGREGATE = (kind, float(x))

    rows = [json.loads(line) for line in a.sweep.open()]
    wanted = {w.strip().lower() for w in (a.weapon or "").split(",") if w.strip()}
    rows = [r for r in rows if r["rl"] == a.rl and (not wanted or r["weapon"].lower() in wanted)
            and not (a.one_handed and r["two"])]
    if not rows:
        raise SystemExit(f"{a.sweep} has no rows at RL {a.rl}" + (f" for {a.weapon}" if a.weapon else ""))
    defenders = Defenders(pvp_corpus(a.mirror, a.rl - a.window, a.rl + a.window))
    # Poise above the heaviest defender buys nothing, so `stagger` is the share of the window's
    # PvP builds whose menu poise (Bull-Goat applied when the planner computed it) is below the
    # hit's. Raw poise ranks that surplus (docs/er-mechanics/giant-crusher-adoption-gap.md).
    poises = defenders.poise
    tables, reg, pvp = AR.Tables(None), ATK.Regulation(None), PvpTables()
    pool = None if a.no_exchange else EXCH.Pool(EXCH.opponent_pool(reg, a.mirror, a.rl - a.window, a.rl + a.window))
    imatrix = INTR.load_matrix(a.interrupt) if a.interrupt else None
    mech = Mechanics(reg, tables, a.mirror, a.rl, a.window)
    mech.react, mech.buff_options, mech.follow_ups = not a.no_react, not a.no_buff_options, not a.no_follow_ups
    mech.measure_all = a.measure_all
    mech.no_buffs = a.no_buffs
    if a.engagement_seconds is not None:
        # Every reader of the engagement spacing takes it from here, before the workers fork.
        mech.st.ENGAGEMENT_SECONDS = a.engagement_seconds
        mech.ash.ENGAGEMENT_SECONDS = a.engagement_seconds
    # The fight's points and landed hits, after the engagement spacing it reads; the same before
    # the workers fork.
    if a.flasks not in ("corpus", "duel", "invasion") and not a.flasks.isdigit():
        ap.error("--flasks is corpus, duel, invasion or a number")
    if a.cerulean_flasks != "corpus" and not a.cerulean_flasks.isdigit():
        ap.error("--cerulean-flasks is corpus or a number")
    mech.set_fight(mech.buf.fight_points(*a.fight_seconds) if a.fight_seconds is not None else None,
                   flasks=a.flasks, eta=a.flask_eta, pinned=a.fight_hits, cerulean=a.cerulean_flasks)
    fk = mech.fight_kind
    print(f"# fight: {fk['kind']}; landed hits {fk['hits_min']}..{fk['hits_max']} (mean {fk['hits_mean']:.2f}) "
          f"over {len(mech.fight['fight_seconds'])} points; attacker cerulean flasks {fk['cerulean']}",
          file=sys.stderr)
    if a.fight_window is not None:
        EXCH.FIGHT_WINDOW_S = a.fight_window
    mech.ash.DISENGAGE = a.disengage
    if a.sustain:
        global SUSTAIN
        regen = mech.buf.corpus_regen(mech.buf_m, str(a.mirror), (a.rl - a.window, a.rl + a.window))
        eng_s, n_fight = mech.st.ENGAGEMENT_SECONDS, round(mech.buf.hits_mean(mech.fight["hits"]))
        fixed = mech.buf.sustain_factor(mech.buf_m, regen, lambda n: n * eng_s, engagements=n_fight)
        memo: dict = {}

        def sustain(hit, mode=a.sustain):
            if mode == "window":
                return fixed
            k = round(hit)
            if k not in memo:
                memo[k] = mech.buf.sustain_factor(mech.buf_m, regen, lambda n: n * eng_s, hit_hp=float(k))
            return memo[k]
        SUSTAIN = sustain
        print(f"# sustain {a.sustain}: {sum(1 for d in regen if d['rows'])} of {len(regen)} defenders heal over "
              f"time; fixed-fight factor {fixed:.5f}", file=sys.stderr)
    opp_rows_from = json.loads(a.opponents_from.read_text())["results"] if a.opponents_from else None
    if a.opponent_pool == "families" and opp_rows_from is None:
        ap.error("--opponent-pool families reads the openers from a ranking: give --opponents-from")
    if pool is not None:
        # The evading dodger's punish is an R1 (ashes-of-war.md section 16a): the pool's own R1 #1
        # strike frames, one per build.
        mech.strikes = pool.startup[pool.build_prof]
        if opp_rows_from is not None:
            # Each opponent's hit, from the ranking the opponents are read from.
            pool.set_damage(opp_rows_from)
        if a.trades == "priced":
            pool.trade_clamp = EXCH.TRADE_CLAMP if a.trade_clamp is None else a.trade_clamp
        if a.opponent_pool == "families":
            # Two passes, as the skill term's opponents: the families of a ranking scored against
            # R1 #1 become what every slot is contested against (neutral.md section 4).
            fam = NEUT.NeutralPool.from_results(pool, NEUT.pool_reaches(pool.raw), opp_rows_from,
                                                lambda r: {**r["slots"], **jump_openers(r["slots"])})
            print(f"# opponent pool: families from {a.opponents_from}; R1 #1 fallback "
                  f"{fam.cover['r1_fallback']:.3f} of builds, dropped "
                  f"{ {k: round(v, 3) for k, v in fam.cover['dropped'].items()} }, openers "
                  f"{ {k: round(v, 3) for k, v in sorted(fam.cover['openers'].items(), key=lambda x: -x[1])} }",
                  file=sys.stderr)
            pool = fam.pool
            if not a.no_neutral:
                mech.npool = fam
        elif not a.no_neutral:
            # The neutral game (docs/er-mechanics/neutral.md): the pool's R1 reach per build.
            mech.npool = NEUT.NeutralPool(pool, NEUT.pool_reaches(pool.raw))
        mech.pool = pool
    flat = OPT.GREASES[a.grease]
    talismans = [n.strip() for n in (a.talismans or "").split(",") if n.strip()]
    if a.spear_talisman and talismans and "Spear Talisman" not in talismans:
        talismans.append("Spear Talisman")
    gtab = GUARD.Tables()
    gbuilds = GUARD.blocker_corpus(a.mirror, a.rl - a.window, a.rl + a.window)
    blockers = GUARD.Blockers(gtab, gbuilds, curve=defense_curve)
    opening = blockers.opening_hits(gbuilds)
    own_ref = blockers.one_hand_guard_mean(opening)
    crit_ev = mech.cr.crit_evidence(mech.cr_t, a.rl, a.window, a.mirror, defenders=defenders)
    combo = combo_model = None
    loop_lefts = None
    if a.paired_offhand or a.paired_loop:
        combo = _sibling("er-mechanics-combo")
        combo_model = combo.Model(mirror=None)
    setup = None
    if a.paired_loop and not a.setup:
        loop_lefts = paired_loop_lefts(a.rl, a.window)
    elif a.setup:
        which = {"hatchets": PAIRED_LOOP_LEFTS, "all": "all"}.get(
            a.setup_lefts, [n.strip() for n in a.setup_lefts.split(",") if n.strip()])
        loop_lefts = paired_loop_lefts(a.rl, a.window, which, jobs=max(1, a.jobs), keep_no_catch=True)
        setup = SetupBuffs(mech, a.grease, mech.fight["fight_seconds"], a.setup_left_buffs)
        # Every skill's left-hand rows and every option's effect are read here, once, so the
        # forked workers inherit them instead of each reading them again.
        for info in loop_lefts.values():
            for i in range(len(AR.AFFINITIES)):
                aid = info["wid"] + i * 100
                if aid in tables.weapons:
                    for o in setup.options(info["wid"], i, tables.max_level(tables.weapons[aid]["reinforceTypeId"])):
                        setup.effect(o)
        setup.body()
        print(f"# setup: {len(loop_lefts)} lefts, {sum(1 for o in setup._skill.values() if o)} skills with a "
              f"left-hand weapon buff, fight {setup.fight_s[0]:.0f}-{setup.fight_s[-1]:.0f} s "
              f"({len(setup.fight_s)} points)", file=sys.stderr)

    # One sweep row scored: (result, pending skill-term item or None), or None for a row with no
    # build. Pure in its row, so `--jobs` can run it in forked workers (`_fork_map`).
    def score_row(row):
        b = build_for(row, a.greased)
        if b is None:
            return None
        if a.build_aff:
            stats = dict(b["stats"])
            for kv in (a.build_stats or "").split(","):
                if "=" in kv:
                    k, v = kv.split("=")
                    stats[k.strip()] = int(v)
            grease = a.build_grease if a.build_grease not in (None, "none") else None
            b = {"dmg": b["dmg"], "aff": a.build_aff, "grease": grease, "stats": stats}
        f_weight = (row.get("weight") or {}).get("factor", 1.0)
        wid = tables.find_weapon(row["weapon"], b["aff"])
        level = tables.max_level(tables.weapons[wid]["reinforceTypeId"])
        stats = {k: b["stats"][k] for k in OPT.DAMAGE_STATS}
        r = AR.attack_rating(tables, row["weapon"], b["aff"], level, stats, row["two"])
        ar_by = {el: r["damage"].get(el, {}).get("total", 0.0) for el in ELEMENTS}
        grease = (b["grease"], flat) if b["grease"] else None
        grease_tf = 1.0
        if grease and not a.no_buffs:
            # The right hand's grease held for its uptime over the fight and recast like every
            # other buff (`Mechanics.grease_plan`); its uses' time comes off the score below.
            gp = mech.grease_plan(a.grease, grease[0])
            grease, grease_tf = (grease[0], flat * gp["uptime"]), gp["time_factor"]
        base_id = tables.find_weapon(row["weapon"], "Standard")
        slots, hits = {}, {}
        crit = mech.cr.weapon_crit(mech.cr_t, crit_ev, base_id, b["aff"], level, stats, row["two"], defenders)
        choice = mech.skill_choice(base_id, b["aff"], level)
        buff = None if a.no_buffs else mech.buffs(row["weapon"], b["aff"], level, stats, row["two"], base_id,
                                                  choice, bool(b["grease"]))
        attacks = ATK.weapon_attacks(reg, wid, "both" if row["two"] else "one", level)
        # The configuration's own guard against the corpus's opening hits, with a repel its own R1 #1
        # punishes credited (`Blockers.own_guard`): a two-handed row guards with its weapon, a
        # one-handed row with the corpus shield it meets the stats for that stops the most
        # (`Blockers.best_left_shield`), times the share of the corpus's one-handers of its
        # weapon class that carry a shield at all (`Blockers.carried_left_shield`, `MEASURED`);
        # a one-handed row with a weapon in the left hand (`--paired-offhand`, `--paired-loop`)
        # cannot guard and gets 0 there.
        r1 = next((x for x in attacks if x["slot"] in ("r1_1", "2h_r1_1")), None)
        r1_start = (r1.get("hit_windows") or [[None]])[0][0] if r1 else None
        guard_left = carry = None
        if row["two"]:
            own = blockers.own_guard(GUARD.shield_guard(gtab, wid, level, two_handed=True), opening,
                                     startup=r1_start)
        else:
            own, left_g, carry = blockers.carried_left_shield(b["stats"], opening,
                                                              tables.weapons[base_id]["wepType"], r1_start)
            guard_left = left_g and {"name": left_g["name"], "weapon": left_g["weapon"], "level": left_g["level"],
                                     "own": round(own / carry, 4) if carry else 0.0}
        guard_parts, guard_memo = {}, {}

        def regard(k, rate):
            """Slot `k`'s guard pressure with the attacker's staminaAttackRate set to `rate`
            (a buff option or spill re-scores a slot under different buffs)."""
            mk = (k, round(rate, 6))
            if mk not in guard_memo:
                parts_k, cycle_k = guard_parts[k]
                guard_memo[mk] = blockers.slot_pressure([{**p, "stamina_rate": rate} for p in parts_k],
                                                        cycle=cycle_k)
            return guard_memo[mk]
        main_dmg = {}
        for atk in attacks:
            key = atk["slot"].removeprefix("2h_")
            hit = slot_hit(pvp, reg, base_id, atk, ar_by, defenders, grease, a.spear_talisman, talismans,
                           buff=buff)
            main_dmg[key] = hit["dmg"]
            parts = [hit.pop("guard_part")]
            first_frame = (atk.get("hit_windows") or [[None]])[0][0]
            p_stag0 = sum(p < hit["poise"] for p in poises) / len(poises) if poises else 1.0
            hit["extra_land"] = []
            # Every further hitbox of the animation that is a separate sweep hit (a multi-hit
            # unique R2, for one) is its own AtkParam row, scored the same way (with its own
            # damage type) and summed. Hitboxes that cannot hit an enemy, or that continue another
            # judge's sweep on the same attack index, add nothing (`sweep_hit` false).
            for extra in atk.get("other_hitboxes") or []:
                if not extra.get("sweep_hit", True):
                    continue
                if hit["startup"] is None or extra["frames"][0] < hit["startup"]:
                    hit["startup"] = extra["frames"][0]
                nums = ATK.attack_numbers(reg, wid, extra["judge"], level)
                more = slot_hit(pvp, reg, base_id, {**nums, "slot": atk["slot"]}, ar_by, defenders, grease,
                                a.spear_talisman, talismans, buff=buff)
                parts.append(more.pop("guard_part"))
                land = 1.0
                if MULTI_HIT_ESCAPE and first_frame is not None and extra["frames"][0] > first_frame:
                    land = extra_hit_land(extra["frames"][0] - first_frame, p_stag0, mech)
                hit["extra_land"].append(round(land, 4))
                for k in ("dmg", "med", "ctr"):
                    hit[k] += land * more[k]
                for el in ELEMENTS:
                    hit["by_type"][el] += land * more["by_type"][el]
                hit["hits"] += 1
            # The same-button cycle bounds the guard's regeneration between two throws.
            hit["guard"] = blockers.slot_pressure(parts, cycle=hit.get("next"))
            guard_parts[key] = (parts, hit.get("next"))
            hit["guard_own"], hit["guard_own_ref"] = own, own_ref
            hit["stagger"] = sum(p < hit["poise"] for p in poises) / len(poises) if poises else None
            hits[key] = hit
        mech_slots = mech.slots(row["weapon"], b["aff"], level, b["stats"], row["two"], wid, attacks, hits)
        for atk in attacks:
            key = atk["slot"].removeprefix("2h_")
            hit = hits[key]
            ms = mech_slots.get(key, {})
            exult = exultation_factor(mech, pvp, reg, base_id, atk, ar_by, defenders, grease, a.spear_talisman,
                                      talismans, hit, ms)
            if exult:
                hit["exultation"] = exult
                for k in ("dmg", "med", "ctr"):
                    hit[k] *= exult["factor"]
                hit["by_type"] = {el: v * exult["factor"] for el, v in hit["by_type"].items()}
            slots[key] = {"label": atk["label"], "anim": atk["anim"], "mv": atk["mv_phys"],
                          "release_lead_in": atk.get("release_lead_in"), **hit, **ms,
                          "crit_hp": crit["crit_hp"] if crit else 0.0,
                          "parry_hp": crit["parry_hp"] if crit else 0.0, "f_weight": f_weight}
            if pool is not None:
                slots[key]["exchange"] = EXCH.slot_exchange(pool, reg, base_id, atk, slots[key], entry_frames(key))
                if mech.npool is not None and (slots[key]["exchange"] or {}).get("strike_frame") is not None:
                    # A rolling or backstep attack keeps its neutral exchange (no strike frame).
                    held = {}
                    if "crouch" in TIMING_MIXUP and key == "crouch_r1":
                        # A held crouch closes at the crouch speed, with no dodge.
                        held = {"tools": (), "k": _sibling_cached("er-mechanics-timing-mixup").crouch_k()}
                    slots[key]["neutral"] = NEUT.slot_neutral(mech.npool, slots[key], atk, entry_frames(key),
                                                              **held)
                    lead = (atk.get("release_lead_in") or 0.0) + entry_frames(key)
                    slots[key]["neutral_in"] = {
                        "strike": slots[key]["exchange"]["strike_frame"], "reach": slots[key].get("reach"),
                        "poise": slots[key].get("poise") or 0.0, "active": slots[key].get("active") or 3.0,
                        "hyper": EXCH.hyper_windows(atk, lead)}
            if imatrix is not None and key != "counter":
                # Openers only: `slot_interrupt` returns None for chain follow-ups.
                slots[key]["interrupt"] = INTR.slot_interrupt(imatrix, row["weapon"], row["two"], key)
            slots[key]["score"] = slot_score(slots[key], entry_frames(key))
        skill = mech.skill(row["weapon"], b["aff"], level, b["stats"], row["two"], base_id, defenders.median)
        ms_slots, pair_fn, neutral_frames = slots, None, None
        loop = None
        spill = None
        if setup is not None and not row["two"]:
            spill_cache = {}

            def spill(opt, up):
                """The right hand's slots with a left skill's category-162 rows in place of the
                sweep's grease (`SetupBuffs` docstring): every slot's main hitbox hit again with
                them (other hitboxes scale the same, as in `_buff_moveset_fn`), its status rerun
                with their status row, the slot scored again. None when the skill has no such
                row for the right hand."""
                rows_r = opt.get("rows_right") if opt else None
                if not rows_r:
                    return None
                key = (rows_r, round(up, 4))
                if key not in spill_cache:
                    eff = setup.hand_effect(rows_r, "right")
                    if not eff["gated"]:
                        spill_cache[key] = None
                        return None
                    rb = setup.buff_dict(buff, eff, up, mech.def_buffs["factor"])
                    new = {k: dict(s) for k, s in slots.items()}
                    for atk in attacks:
                        k = atk["slot"].removeprefix("2h_")
                        if k not in new or not main_dmg.get(k):
                            continue
                        h = slot_hit(pvp, reg, base_id, atk, ar_by, defenders, None, a.spear_talisman, talismans,
                                     buff=rb)
                        r = h["dmg"] / main_dmg[k]
                        for f in ("dmg", "med", "ctr"):
                            new[k][f] = slots[k][f] * r
                        # The skill's rows can carry staminaAttackRate (Determination, Royal
                        # Knight's Resolve): the guard pressure is measured again with it.
                        new[k]["guard"] = regard(k, h["guard_part"]["stamina_rate"])
                    if eff["status_row"] is not None:
                        old = {k: s.get("status") or {} for k, s in new.items()}
                        mech.statuses(new, row["weapon"], b["aff"], level, b["stats"], row["two"], attacks, new,
                                      grease=eff["status_row"])
                        if up < 1.0:
                            for k, s in new.items():
                                s["status"] = {n: {**v, "hp_per_hit": up * v["hp_per_hit"] + (1.0 - up) *
                                                   old[k].get(n, {}).get("hp_per_hit", 0.0)}
                                               for n, v in (s.get("status") or {}).items()}
                    for k, s in new.items():
                        s["score"] = slot_score(s, entry_frames(k))
                    spill_cache[key] = new
                return spill_cache[key]
        if loop_lefts is not None and not row["two"]:
            loop = paired_loop(combo, combo_model, loop_lefts, base_id, slots, stats, tables, pvp, reg, defenders,
                               a.spear_talisman, talismans, poises, mech.st.ENGAGEMENT_SECONDS * SCORE_FPS,
                               mech.npool, setup, spill, grease_tf=grease_tf if setup is not None else 1.0)
            if loop:
                ms_slots, pair_fn = loop.pop("ms_slots"), loop.pop("pair_fn")
                neutral_frames = mech.st.ENGAGEMENT_SECONDS * SCORE_FPS
        if combo is not None and not row["two"] and loop is None and a.paired_offhand:
            left_id = tables.find_weapon(a.paired_offhand, "Standard")
            ar_l = AR.attack_rating(tables, a.paired_offhand, "Standard",
                                    tables.max_level(tables.weapons[left_id]["reinforceTypeId"]), stats, False)
            ar_l = {el: ar_l["damage"].get(el, {}).get("total", 0.0) for el in ELEMENTS}

            def left_hit(atk, lid=left_id, ar=ar_l):
                h = slot_hit(pvp, reg, lid, atk, ar, defenders, None, a.spear_talisman, talismans)
                h.pop("guard_part")
                h["stagger"] = sum(p < h["poise"] for p in poises) / len(poises) if poises else None
                return h

            def pair_fn(s, lid=left_id, hit_fn=left_hit):
                return left_weapon_guard(combo.paired_slots(combo_model, base_id, lid, s, hit_fn), s)
            ms_slots = pair_fn(slots)
            if ms_slots is not slots:
                # A landed opener's follow-up is credited as damage of the same opening: every
                # engagement costs the status model's neutral time between engagements
                # (`er-mechanics-moveset.opening_credit`, docs/er-mechanics/combo.md section 6).
                neutral_frames = mech.st.ENGAGEMENT_SECONDS * SCORE_FPS
        # The jump openers with their own neutral contest (`jump_openers`, neutral.md section 3).
        ms_slots = {**ms_slots, **jump_openers(ms_slots, mech.npool)}
        moveset = MOVESET.moveset_score(ms_slots, slot_score, entry_frames, opening=neutral_frames)
        if loop and loop.get("time_factor", 1.0) != 1.0:
            # `--setup`: the left buff's recasts take that share of the fight (`SetupBuffs`).
            moveset["score"] *= loop["time_factor"]
        if grease_tf != 1.0 and not (loop and setup is not None):
            # The right grease's recasts take that share of the fight, as a left buff's do (with
            # `--setup` the loop charged it already, only where the right hand kept the grease).
            moveset["score"] *= grease_tf
            moveset["grease_time_factor"] = round(grease_tf, 5)
        moveset["base_score"] = moveset["score"]
        if loop:
            moveset["paired_loop"] = loop
        result = {"weapon": row["weapon"], "two": row["two"], "aff": b["aff"], "grease": b["grease"], "level": level,
                  "stats": b["stats"], "slots": slots, "mix": weapon_mix(slots),
                  "best_slot": best_slot(slots), "skill": skill, "crit": crit, "moveset": moveset,
                  "skill_term": None, "buff": _buff_summary(buff),
                  "guard": {"own": round(own, 4), "own_ref": round(own_ref, 4), "left": guard_left,
                            "carry": None if carry is None else round(carry, 4),
                            "left_weapon": neutral_frames is not None},
                  "kind": row.get("kind"), "weight": row.get("weight")}
        item = None
        if not a.no_skill and moveset["score"]:
            # Scored after every row exists: a utility skill is valued against the opponents'
            # own R1 strings, read from these rows (`opponents_from_results`).
            buff_fn = None if buff is None else _buff_moveset_fn(
                mech, pvp, reg, base_id, row, b, level, stats, attacks, slots, main_dmg, ar_by, defenders,
                grease, a.spear_talisman, talismans, pair_fn if neutral_frames is not None else None,
                neutral_frames, grease_tf=grease_tf, regard=regard)
            item = (result, (row["weapon"], b["aff"], level, stats, row["two"], base_id, choice,
                             moveset["score"], defenders, reg, buff,
                             lambda o, c=crit, g=(own if neutral_frames is None else 0.0), bid=base_id,
                             fw=f_weight:
                             mech.skill_slot_extra(o, c, g, own_ref, bid, fw)),
                    skill_engagement({**slots, **jump_openers(slots, mech.npool)}, moveset, crit, crit_ev),
                    buff_fn)
        return result, item

    def finish(item):
        res, args, eng, buff_fn = item
        term = mech.skill_term(*args, engagement=eng, opponents=opponents, buff_fn=buff_fn)
        res["moveset"]["score"] = term["score"]
        res["skill_term"] = _skill_summary(term)
        return res

    # `--jobs 1` is the serial path. Otherwise the rows are scored twice over in forked workers:
    # once for the results the opponents are read from, then again (the pending item holds
    # closures, which do not pickle) to score the skill term against those opponents.
    jobs = max(1, a.jobs)
    if jobs == 1:
        scored = [s for s in map(score_row, rows) if s]
        results, pending = [s[0] for s in scored], [s[1] for s in scored if s[1]]
        need = []
    else:
        def first(i):
            s = score_row(rows[i])
            return s and (s[0], s[1] is not None)
        firsts = _fork_map(first, range(len(rows)), jobs)
        kept = [(i, s) for i, s in enumerate(firsts) if s]
        results = [s[0] for _, s in kept]
        need = [(j, i) for j, (i, s) in enumerate(kept) if s[1]]
        pending = need

    if pending:
        opp_pool = pool.raw if pool is not None else \
            EXCH.opponent_pool(reg, a.mirror, a.rl - a.window, a.rl + a.window)
        opp_rows = results if opp_rows_from is None else opp_rows_from
        # The jump family's openers are synthesized, not stored (er-effects-rs-8uha).
        opponents, _, _ = mech.ash.opponents_from_results(
            opp_pool, opp_rows, openers=a.opponent_openers, timing=a.dodge_timing, react=mech.react,
            slots_fn=lambda r: {**(r.get("slots") or {}), **jump_openers(r.get("slots") or {})})
        # A dodge skill reruns its opener's neutral contest with the dodge in the kit.
        opponents.npool = mech.npool
        if not need:
            for item in pending:
                finish(item)
        else:
            done = _fork_map(lambda i: finish(score_row(rows[i])[1]), [i for _, i in need], jobs)
            for (j, _), res in zip(need, done):
                results[j] = res
    if a.relative_speed:
        apply_relative_speed(results, tables, a.relative_speed, a.relative_speed_measure)

    if a.json:
        print(json.dumps({"rl": a.rl, "defenders": defenders.n, "distribution": defenders.distribution(),
                          "fight": mech.fight_kind, "results": results}, indent=1))
        return 0
    print(f"RL {a.rl}: scored against {defenders.n} PvP builds of RL {a.rl - a.window}-{a.rl + a.window} "
          f"(dmg = mean over them, med = their median defender); PvP rates applied; frames at 30 fps; "
          f"poise and hyperarmor in menu units; counter = defender in SpEffect {COUNTER_SPEFFECT} frames"
          f"{', attacker wears Spear Talisman' if a.spear_talisman else ''}.")
    print("\n".join(_fmt_dist(defenders.distribution())))
    if a.summary:
        key = {"damage": lambda r: -r["mix"]["avg"], "median": lambda r: -r["mix"]["avg_med"],
               "counter": lambda r: -r["mix"]["avg_ctr"]}.get(a.sort, lambda r: -r["mix"]["avg"])
        print(f"\nper weapon: share of slots by physical type, share of expected damage by element, and "
              f"the mean over slots (avg = corpus mean, med = median defender, ctr = counter hit)")
        print(f"  {'weapon':<32}{'grip':>5}{'build':>22}{'slots':>6}{'slash':>6}{'strk':>6}{'prce':>6}{'std':>6}"
              f"{'phys':>6}{'elem':>6}{'avg':>7}{'med':>7}{'ctr':>7}")
        for r in sorted([r for r in results if r["mix"]], key=key)[:a.top]:
            m = r["mix"]
            build = r["aff"] + (f"+{r['grease']}" if r["grease"] else "")
            print(f"  {r['weapon'][:31]:<32}{'2H' if r['two'] else '1H':>5}{build:>22}{m['slots']:>6}"
                  + "".join(f"{100 * m['types'][t]:>6.0f}" for t in PHYS_TYPES)
                  + f"{100 * m['elements']['physical']:>6.0f}{100 * (1 - m['elements']['physical']):>6.0f}"
                  f"{m['avg']:>7.0f}{m['avg_med']:>7.0f}{m['avg_ctr']:>7.0f}")
        return 0
    if a.weapon or a.all_slots:
        for res in results:
            m = res["mix"]
            print(f"\n{res['weapon']} {'2H' if res['two'] else '1H'} {res['aff']}"
                  f"{' + ' + res['grease'] + ' grease' if res['grease'] else ''}  "
                  + " ".join(f"{k}{v}" for k, v in res["stats"].items()))
            print("  mix: " + ", ".join(f"{t} {100 * m['types'][t]:.0f}%" for t in PHYS_TYPES if m["types"][t])
                  + "; damage " + ", ".join(f"{el} {100 * v:.0f}%" for el, v in m["elements"].items() if v >= 0.005)
                  + f"; mean over slots {m['avg']:.0f} (median defender {m['avg_med']:.0f}, counter {m['avg_ctr']:.0f})")
            print(f"  {'slot':<18}{'MV':>5}{'dmg':>7}{'med':>7}{'ctr':>7}{'type':>9}{'pvp':>6}{'poise':>7}{'stag%':>6}"
                  f"{'stam':>6}{'start':>7}{'active':>7}{'next':>6}{'roll':>6}{'HA':>7}{'ctrW':>6}"
                  f"{'reach':>7}{'adv':>6}{'advS':>6}{'parry':>6}{'status':>9}{'score':>7}")
            for s in res["slots"].values():
                print(f"  {s['label']:<18}{s['mv']:>5}{s['dmg']:>7.0f}{s['med']:>7.0f}{s['ctr']:>7.0f}{s['phys_type']:>9}"
                      f"{s['final_rate']:>6.2f}{s['poise']:>7.0f}{100 * (s['stagger'] or 0):>6.0f}{s['stamina']:>6}"
                      f"{s['startup'] or '-':>7}{s['active'] or '-':>7}"
                      f"{s['next'] or '-':>6}{s['roll'] or '-':>6}{s['hyperarmor']:>7.0f}{s['ctr_window']:>6}"
                      + _mech_cells(s))
            sk = res["skill"] or {}
            if "best_hit" in sk:
                print(f"  skill {sk['name']}: best hit {sk['best_hit']:.0f}, {sk['hits']} hits {sk['total']:.0f} "
                      f"vs the median defender, {sk['fp']} FP")
        return 0
    if a.sort == "score" and not a.slot:
        have = sorted([r for r in results if r["moveset"]["best_opener"]],
                      key=lambda r: -r["moveset"]["score"])[:a.top]
        print(f"\nranked by moveset score (module docstring; score = the weapon's, the other columns are "
              f"its best family's opener). per slot: damage + status "
              f"proc HP per hit, per second of commitment (first frame a roll or the same button is free), "
              f"x reach x expected frame advantage x stagger x parry. first = first hit, real frames; "
              f"reach m (i = class median, no pose); adv/advS = frame advantage when poise holds / breaks; "
              f"status = engagements to the first proc on a non-carrier (an engagement = the first hit plus its true "
              f"combos; {mech.st_dfs.n} builds, gauge refill between engagements, one proc live at a time); "
              f"stHP = expected status HP per landed hit over the fight schedule "
              f"(mean {mech.fight_kind['hits_mean']:.1f} engagements), in the score; "
              f"carP% = share of bolus carriers it procs within one engagement; "
              f"skill = the weapon's own skill, best single hit vs the median defender; "
              f"base = moveset score before the skill term, sk+ = SKILL_WEIGHT x the best skill it can "
              f"mount (scored), skC = the same for the corpus-weighted skills (shown only); "
              f"buff = the build's physical buff factor (attacker kits x defender kits); "
              f"fEx/fSt/fW = the opener's exchange, stamina and equip-weight factors; fit = the sweep's "
              f"weight fit in percent (shown only, not scored)")
        print(f"  {'weapon':<30}{'grip':>5}{'build':>20}  {'slot':<14}{'score':>7}{'base':>7}{'sk+':>5}{'skC':>5}{'buff':>6}"
              f"{'fEx':>5}{'fSt':>5}{'fW':>5}{'fit':>5}{'dmg':>6}{'first':>6}{'comm':>6}"
              f"{'reach':>7}{'adv':>6}{'advS':>6}{'stag%':>6}{'parry':>6}{'status':>11}{'stHP':>6}{'carP%':>6}  skill")
        for r in have:
            k = r["moveset"]["best_opener"]
            s, sc = r["slots"][k], r["slots"][k]["score"]
            build = r["aff"] + (f"+{r['grease']}" if r["grease"] else "")
            reach = "-" if s.get("reach") is None else f"{s['reach']:.2f}" + ("i" if s["reach_source"] == "inferred" else "")
            sk = r["skill"] or {}
            skill = sk.get("name", "-")
            if "best_hit" in sk:
                skill = (f"{sk['name'][:24]} {sk['best_hit']:.0f} ({sk['fp']} FP)" if sk["best_hit"]
                         else f"{sk['name'][:24]} no hit ({sk['fp']} FP)")
            mv = r["moveset"]
            ts = r.get("skill_term")
            if ts:
                top = ts.get("best")
                if top and (top.get("gain") or top.get("buff_gain")):
                    shown = top["buff"]["score"] if top.get("as") == "buff" else top["score"]
                    skill = f"{top['name'][:22]}{' (buff)' if top.get('as') == 'buff' else ''} {shown:.0f}; " + skill
            corpus = f"{ts['score_corpus'] - mv['base_score']:.0f}" if ts and ts.get("score_corpus") is not None else "-"
            print(f"  {r['weapon'][:29]:<30}{'2H' if r['two'] else '1H':>5}{build[:19]:>20}  {k:<14}{mv['score']:>7.0f}"
                  f"{mv['base_score']:>7.0f}{mv['score'] - mv['base_score']:>5.0f}{corpus:>5}{_buff_cell(r):>6}"
                  f"{sc['f_exchange']:>5.2f}{sc['f_stamina']:>5.2f}{sc['f_weight']:>5.2f}"
                  f"{_f(round(((r.get('weight') or {}).get('fit') or 0) * 100)) if r.get('weight') else '-':>5}"
                  f"{s['dmg']:>6.0f}{_f(s['startup']):>6}{_f(sc['commit']):>6}{reach:>7}{_f(s.get('adv')):>6}"
                  f"{_f(s.get('adv_stagger')):>6}{100 * (s['stagger'] or 0):>6.0f}"
                  f"{('yes' if s.get('parryable') else 'no'):>6}{_status_cell(s.get('status')):>11}"
                  f"{sc['status_hp']:>6.0f}{_carrier_cell(s.get('status')):>6}  {skill}")
            print(f"      {s['label']} {s['anim']}, +{r['level']}, "
                  + " ".join(f"{k}{v}" for k, v in r["stats"].items()))
        return 0
    slot = (a.slot or "r1_1").removeprefix("2h_")
    have = [r for r in results if slot in r["slots"]]
    key = {"score": lambda r: -((r["slots"][slot]["score"] or {}).get("score") or 0),"damage": lambda r: -r["slots"][slot]["dmg"], "median": lambda r: -r["slots"][slot]["med"],
           "counter": lambda r: -r["slots"][slot]["ctr"], "poise": lambda r: -r["slots"][slot]["poise"],
           "stagger": lambda r: (-(r["slots"][slot]["stagger"] or 0), -r["slots"][slot]["dmg"]),
           "per-frame": lambda r: -r["slots"][slot]["dmg"] / (r["slots"][slot]["next"] or 999),
           "roll": lambda r: (r["slots"][slot]["roll"] or 999, -r["slots"][slot]["dmg"]),
           "startup": lambda r: (r["slots"][slot]["startup"] or 999, -r["slots"][slot]["dmg"])}[a.sort]
    print(f"\nranked by {a.sort} of slot {slot}; dmg = mean over the corpus, med = median defender, ctr = "
          f"counter hit (mean), stag% = share of the window's {len(poises)} PvP builds one hit staggers, "
          f"next = frame the same button can start again, roll = frame a roll can start, dmg/next = damage "
          f"per frame of that cycle, ctrW = frames this attack itself can be counter-hit")
    print(f"  {'weapon':<32}{'grip':>5}{'build':>22}{'MV':>5}{'hits':>5}{'dmg':>6}{'med':>6}{'ctr':>6}{'type':>9}"
          f"{'pvp':>6}{'poise':>7}{'stag%':>6}{'start':>7}{'next':>6}{'roll':>6}{'HA':>6}{'ctrW':>6}{'dmg/next':>10}"
          f"{'reach':>7}{'adv':>6}{'advS':>6}{'parry':>6}{'status':>9}{'score':>7}")
    for r in sorted(have, key=key)[:a.top]:
        s = r["slots"][slot]
        build = r["aff"] + (f"+{r['grease']}" if r["grease"] else "")
        print(f"  {r['weapon'][:31]:<32}{'2H' if r['two'] else '1H':>5}{build:>22}{s['mv']:>5}{s['hits']:>5}"
              f"{s['dmg']:>6.0f}{s['med']:>6.0f}{s['ctr']:>6.0f}"
              f"{s['phys_type']:>9}{s['final_rate']:>6.2f}{s['poise']:>7.0f}{100 * (s['stagger'] or 0):>6.0f}"
              f"{s['startup'] or '-':>7}"
              f"{s['next'] or '-':>6}{s['roll'] or '-':>6}{s['hyperarmor']:>6.0f}{s['ctr_window']:>6}"
              f"{s['dmg'] / (s['next'] or 999):>10.1f}" + _mech_cells(s))
    return 0


def _skill_summary(term: dict | None) -> dict | None:
    """The skill term without its per-hit rows: value, and per option p, FP casts, damage, score."""
    if not term:
        return None
    keep = ("name", "p", "fp", "uses", "share", "hits", "dmg", "roll", "next", "stagger", "parryable",
            "score", "gain", "buff_roots", "error", "utility", "reach", "coverage", "reach_source",
            "projectile", "reach_measured", "measured", "dmg_all", "score_all", "landing", "landing_error",
            "variant", "react", "dmg_landing", "buff", "buff_gain", "as", "score_detail")
    brief = ("name", "sword_arts_id", "share", "dmg", "roll", "next", "score", "gain", "reach", "coverage",
             "reach_source", "projectile", "reach_measured", "classes", "measured", "dmg_all", "score_all",
             "variant", "react", "dmg_landing", "buff_gain", "neutral", "score_detail")
    best = term.get("best")
    return {"value": term["value"], "score": term["score"], "fp_bar": term["fp_bar"],
            "value_corpus": term.get("value_corpus"), "score_corpus": term.get("score_corpus"),
            "best": {k: best.get(k) for k in keep + ("sword_arts_id",) if k in best} if best else None,
            "options": [{k: o.get(k) for k in keep if k in o} for o in term["options"]],
            "available": [{k: o.get(k) for k in brief if k in o} | {"utility": (o.get("utility") or {}).get("kind"),
                                                                     "utility_detail": o.get("utility"),
                                                                     "buff_score": (o.get("buff") or {}).get("score")}
                          for o in term.get("available") or []]}


def _buff_summary(buff: dict | None) -> dict | None:
    if not buff:
        return None
    return {k: buff[k] for k in ("pre", "post", "flat", "def")} | {
        "skill_alternatives": [[p, [r for r, _, _ in x]] for p, x in buff["alternatives"]]}


def _buff_cell(r: dict) -> str:
    """The build's buff factor on its damage: `joint` physical x the defenders' standard factor."""
    b = r.get("buff")
    if not b:
        return "-"
    return f"{b['pre']['physical'] * b['post']['physical'] * b['def']['standard']:.3f}"


def _f(v) -> str:
    """A frame or advantage value for a table cell: `-` when absent, one decimal otherwise."""
    if v is None:
        return "-"
    return f"{v:.0f}" if float(v).is_integer() else f"{v:.1f}"


def _mech_cells(s: dict) -> str:
    """reach, adv, advS, parry, status and score cells of one slot."""
    reach = "-" if s.get("reach") is None else f"{s['reach']:.2f}" + ("i" if s.get("reach_source") == "inferred" else "")
    parry = "-" if s.get("parryable") is None else ("yes" if s["parryable"] else "no")
    score = "-" if not s.get("score") else f"{s['score']['score']:.0f}"
    return (f"{reach:>7}{_f(s.get('adv')):>6}{_f(s.get('adv_stagger')):>6}{parry:>6}"
            f"{_status_cell(s.get('status')):>9}{score:>7}")


if __name__ == "__main__":
    sys.exit(main())
