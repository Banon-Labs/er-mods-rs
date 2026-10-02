#!/usr/bin/env python3
"""Powerstance, off-hand attacks and blocking, per attack, from the installed regulation.

Write-up: `docs/er-mechanics/powerstance-guard.md`. Labels follow `docs/er-mechanics/attacks.md`:
`VERIFIED` = regulation value or code read out of the 1.16.2 executable (named Ghidra dump on
:8765, shift 0), `TAE` = decoded animation event or behavior-graph state, `MEASURED` = counted
over the planner corpus, `INFERRED` = fits the data, consumer not traced, `COMMUNITY` = outside
claim (here: Smithbox's decompiled `c0000.hks` and WitchyBND's TAE template).

Powerstance and off-hand:

* `c0000.behbnd` state machine `AttackDualWield_SM` plays `a<cat>_0340x0` (`AttackDualLight1..6`),
  034200 dash, 034300 rolling, 034310 crouch, 034400 backstep; `Jump_LandAttack_Dual` plays
  034570 (`TAE`). Their hits are judges 800-895. Judges 600-7xx are mounted: `RideAttack_R_*`
  plays 0380xx and `RideAttack_L_*` 0390xx (`TAE`).
* Each AttackBehavior event names its hand in byte +0xd, `Source` (0 default, 1 right, 2 left).
  `CSChrTaeAnimEvent::AttackBehavior` 0x1404266d0 turns 1/2 into the right/left weapon slot and
  resolves the judge against that slot's weapon (`VERIFIED`). A powerstance L1 is therefore two or
  three separate hits, each with its own weapon's base, scaling, poise and stamina damage.
* Off-hand L1 (`AttackLeft_SM`) plays 035000..035050 with judges 400..450 and Source 0, i.e. the
  reference hand, which is the left weapon for these states. The hit then uses the left weapon
  for base, reinforce, scaling, poise and guard numbers, and the 2H STR bonus is off because
  `+0xf5` is set only from `ChrIns::IsTwoHanding` (`FUN_14068ffa0`, `VERIFIED`).
* `attacks.md` section 1's "second weapon's base" term is the bow added to an arrow, not a
  powerstance term (`VERIFIED` in 0x1406832a0 and the status screen `FUN_14065c120`).
* Eligibility is the behavior script's `IsEnableDualWielding` (`COMMUNITY` decompile; the compiled
  vanilla HKS carries the same identifiers): not while two-handing, and the two hands'
  `GetEquipWeaponCategory` values equal, from a fixed list. Dagger: a weapon with
  `GetEquipWeaponSpecialCategoryNumber` (env 345 = `EquipParamWeapon.spAtkcategory`, `VERIFIED`)
  104 pairs only with another 104; a katana also pairs with a 104 in the left hand. The HKS enum
  values 20..52 equal `wepmotionCategory` (dagger 20, straight sword 23, colossal sword 26,
  colossal weapon 31); that env 225 returns `wepmotionCategory` is `INFERRED`.

Blocking (1.16.2 code, `VERIFIED` unless marked):

* Repel (the attacker bounces), `FUN_140447180` from `CalculateDamage`: attacker value
  `int(attackBaseRepel * guardAtkRateCorrection / 100 [+ guardAtkRate] + clamp(STR - overStrength,
  0, 10) + durability term)` (0x14068c080) against defender `int(guardBreakCorrection / 100 *
  guardBaseRepel * SpEffect rate) + clamp(STR - overStrength, 0, 10) + durability term`
  (`FUN_14068c3c0`, 0 when the defender lacks the stats). Repelled when defender >= attacker.
  The -5/-10 term is weapon durability status (AtRisk/Broken), not a hand. `overStrength` is 99
  on every weapon read, so the STR term is 0. A repelled hit keeps
  `GameSystemCommonParam.flickDamageCutRateSuccessGurad` (0.5) of its HP damage
  (`FUN_1404f7280`), and its stamina charge and guard-break test are untouched: both run before
  the repel multiplier, and `CalculateDamage2` deducts ADI+0x22c with no repel check.
* Guard break is stamina only: `stamina <= stamina damage` breaks the guard. On a player the
  breaking hit stays guarded (`CSPlayerDamageModule` vtable slot 6 returns 0), so chip applies.
* Stamina damage to the blocker (`FUN_140684540`):
  `g = clamp((statBonus + staminaGuardDef * staminaGuardDefRate + 1.0) * (1 + guard
  AtkParam.guardStaminaCutRate / 100) * SpEffect, 0, 100)`, `0` without the stats;
  `damage = (1 - g / 100) * info+0x28 + guard BehaviorParam.stamina`,
  `* 0.9` when the blocker two-hands, `* 0.7` when that weapon's `weaponCategory` is 12 (shields);
  then `* FinalDamageRateParam.staminaRate` (1.25) when both are players. info+0x28 is the
  attacker's stamina damage times the product of its SpEffect `staminaAttackRate`
  (`FUN_14068aa80`; Hammer Talisman 1.4, Determination 3, Royal Knight's Resolve 4, Cragblade
  1.5).
* Chip damage (`CalculateGuardDamage` 0x140689460): fraction of each element that passes
  `= (100 - (1 + typeCut / 100) * (cut * reinforce cut rate + statBonus) * durability * (1 +
  guard AtkParam.guardRate / 100) * info+0x5c) / 100`, multiplied onto the per-element damage
  after defense (`CalculateDamageBasic` 0x1406849d0). info+0x5c is the attacker's guard-cut
  cancel `(1 + weapon guardCutCancelRate / 100) (1 + attack guardCutCancelRate / 100)`
  (`FUN_140652610`): -50 on 88 weapon rows (shotels, scythes, sickles), nonzero on 168 attack rows.
* Guard reaction: `GUARD_REACTION_TABLE[guardLevel][damage level]`, small / middle / large, with
  the blocker's R1 at f5 / f10 / f50 and its 20% guard regeneration paused for 10 / 27 / 49
  frames. A repelled attacker can roll at f22 (small repel), which is the blocker's punish window.
* Every stat bonus above goes through CalcCorrectGraph 160/161/163, which are flat 0 in the
  1.17.1 regulation, so the bonuses are 0 (`VERIFIED` regulation).

Usage:

    python3 scripts/er-mechanics-powerstance-guard.py dual Greatsword Greatsword
    python3 scripts/er-mechanics-powerstance-guard.py offhand Giant-Crusher
    python3 scripts/er-mechanics-powerstance-guard.py block Greatsword --shield "Brass Shield"
    python3 scripts/er-mechanics-powerstance-guard.py adoption --rl 140-160
    python3 scripts/er-mechanics-powerstance-guard.py --selftest

Corpus blockers (write-up section 8): `Blockers` turns the PvP builds of an RL window into the
guard each one raises, and `Blockers.slot_pressure` scores one attack slot against all of them:
stamina drained, the share one attack breaks from full stamina, the share it bounces off, chip
HP, and the guard-pressure factor `er-builds-pvp.py --sort score` multiplies in.
`Blockers.own_guard` is the defensive side: how much of the corpus's opening hit a guard stops,
with a repel the configuration's own R1 can punish credited; `Blockers.best_left_shield` picks
the left-hand shield of a one-handed configuration from the shields the corpus carries.

    python3 scripts/er-mechanics-powerstance-guard.py blockers --rl 140-160
    python3 scripts/er-mechanics-powerstance-guard.py pressure Giant-Crusher --grip both --rl 140-160

For the PvP ranking: `powerstance_rows`, `offhand_rows`, `can_powerstance`, `shield_guard`,
`block_hit`, `block_matrix`, `blocker_corpus`, `Blockers` and `guard_score_factor` take and return
plain dicts.
"""
import argparse
import collections
import importlib.util
import json
import math
import os
import struct
import sys
import unicodedata

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.expanduser('~/.cache/er-build-planner')
#: Smithbox's decompiled player behavior script, used only as a `COMMUNITY` cross-check.
HKS_DECOMPILE = os.environ.get('ER_C0000_HKS', os.path.expanduser(
    '~/projects/er-effects-rs-main-compare/.worktrees/no-death-loss-body/.deps/Smithbox/'
    'Documentation/ER/c0000.hks'))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ATK = _load('er_mechanics_attacks', 'er-mechanics-attacks.py')
PR = ATK.PR

#: AttackBehavior (event 1) arguments (WitchyBND `TAE.Template.ER.xml`, `COMMUNITY`, with the
#: +0xd hand byte `VERIFIED` in 0x1404266d0): s32 AttackType, s32 AttackIndex, s32 judge,
#: u8 DirectionType, u8 Source.
TAE_ARG_ATTACK_INDEX, TAE_ARG_JUDGE, TAE_ARG_SOURCE = 4, 8, 13
SOURCE_DEFAULT, SOURCE_RIGHT, SOURCE_LEFT = 0, 1, 2

#: (key, label, animation, behavior-graph state), `TAE`.
DUAL_SLOTS = [
    ('dual_1', 'L1 #1', 34000, 'AttackDualLight1'),
    ('dual_2', 'L1 #2', 34010, 'AttackDualLight2'),
    ('dual_3', 'L1 #3', 34020, 'AttackDualLight3'),
    ('dual_4', 'L1 #4', 34030, 'AttackDualLight4'),
    ('dual_5', 'L1 #5', 34040, 'AttackDualLight5'),
    ('dual_6', 'L1 #6', 34050, 'AttackDualLight6'),
    ('dual_dash', 'running L1', 34200, 'AttackDualDash'),
    ('dual_roll', 'rolling L1', 34300, 'AttackDualRolling'),
    ('dual_crouch', 'crouch L1', 34310, 'AttackDualStealth'),
    ('dual_bstep', 'backstep L1', 34400, 'AttackDualBackStep'),
    ('dual_jump', 'jump L1', 34570, 'Jump_LandAttack_Dual'),
]
DUAL_JUDGES = range(800, 900)
#: (key, label, judge, animation, state), `TAE`. 035000 is played by both `AttackLeftLight1`
#: and `AttackLeftHeavy1`; 035010..035050 only by `AttackLeftHeavy2..6`.
OFFHAND_SLOTS = [
    ('left_1', 'off-hand L1 #1', 400, 35000, 'AttackLeftLight1'),
    ('left_2', 'off-hand L1 #2', 410, 35010, 'AttackLeftHeavy2'),
    ('left_3', 'off-hand L1 #3', 420, 35020, 'AttackLeftHeavy3'),
    ('left_4', 'off-hand L1 #4', 430, 35030, 'AttackLeftHeavy4'),
    ('left_5', 'off-hand L1 #5', 440, 35040, 'AttackLeftHeavy5'),
    ('left_6', 'off-hand L1 #6', 450, 35050, 'AttackLeftHeavy6'),
]
#: L1 recovery: input `Input - LH Attack` 9 or `Input - Common` 87, cancel `Cancel - LH Attack`
#: 16 or `Cancel - L1 Attack` 117 (names `COMMUNITY`, WitchyBND template; the consumer is the
#: same `UpdateFromManipulator` pairing attacks.md section 4 verified for R1).
L1_INPUT_IDS, L1_CANCEL_IDS = (9, 87), (16, 117)

#: `IsEnableDualWielding` categories (`COMMUNITY` HKS decompile, `LUA/Enums.txt` values):
#: short sword 20, claw 22, straight sword 23, twinblade 24, large sword 25, extra-large sword
#: 26, rapier 27, curved sword 28, katana 29, axe 30, extra-large axe/hammer 31, large axe 32,
#: hammer 33, flail 34, large hammer 35, spear 36, large spear 37, halberd 38, large rapier 39,
#: large curved sword 40, fist 42, whip 43, large scythe 50 and the unnamed 53, 55..59.
POWERSTANCE_CATEGORIES = frozenset(
    {20, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 42, 43,
     50, 53, 55, 56, 57, 58, 59})
CATEGORY_SHORT_SWORD, CATEGORY_KATANA = 20, 29
#: `spAtkcategory` 104: only the Wakizashi in 1.17.1 (`VERIFIED` regulation).
SPECIAL_CATEGORY_WAKIZASHI = 104

#: `EquipParamWeapon.wepType` of shields (`VERIFIED` regulation: Buckler-class 65, Kite/Brass/
#: Heater 67, every towershield/greatshield 69).
SHIELD_TYPES = {65: 'small shield', 67: 'medium shield', 69: 'greatshield'}
#: `EquipParamWeapon.weaponCategory` 12: every shield row (`VERIFIED` regulation); the two-hand
#: guard factor in `FUN_140684540` tests it.
WEAPON_CATEGORY_SHIELD = 12
#: `FUN_140684540` two-handed guard factors (`VERIFIED`, 0x142a1a64c and 0x14329e66c).
TWO_HAND_GUARD, TWO_HAND_SHIELD_GUARD = 0.9, 0.7
#: The +1.0 inside the guard-boost sum (`ADDSS XMM2, XMM6` at 0x140684735, XMM6 = 1.0).
GUARD_BOOST_ADD = 1.0
#: Guard behavior judges set by TAE JumpTable 3 (`_ChrActionFlag` 0x1404275e0 at 0x140427683 raises
#: the guard flag and stores ArgB as the judge in one write). Every one-handed `GuardOn` /
#: `GuardStart` / guard-reaction clip (a00/a02/a03) sets 460; the same clips in a10..a16 set 470,
#: and that a1x is the two-handed guard is `INFERRED` from the 2H attack clips there (`TAE`).
#: 461/471 are thrusting-shield attacks and Flame Spit / Tongues of Fire, 480 the parry skills:
#: never a plain raised guard. 460/470 rows are neutral on every weapon read, so the two give
#: the same numbers today.
GUARD_JUDGE_DEFAULT = 460
GUARD_JUDGE_TWO_HAND = 470

#: Guard reaction (`HksEnv` env 237 `GetGuardLevelAction` -> `FUN_140686f00`): the 5x12 int table at
#: 0x143b340b0 (1.16.2) / 0x143b380c0 (1.17.1), row = the guard weapon's `guardLevel`, column = the
#: hit's damage level. The selftest reads both images. The behavior script (`COMMUNITY` c0000.hks
#: l.4585) plays 1 `GuardDamageSmall`, 3 `Middle`, 4 `Large`, and no reaction for 0. SpEffect 171
#: raising a result below 3 to 3 is not modelled.
GUARD_REACTION_TABLE = (
    (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    (0, 1, 3, 3, 4, 1, 3, 3, 1, 3, 4, 3),
    (0, 1, 1, 3, 3, 1, 3, 3, 1, 3, 4, 3),
    (0, 1, 1, 3, 3, 1, 3, 3, 1, 3, 4, 3),
    (0, 1, 1, 1, 3, 1, 3, 3, 1, 3, 4, 3),
)
GUARD_REACTION_TABLE_VA = {'1.16.2': 0x143b340b0, '1.17.1': 0x143b380c0}
GUARD_REACTION_NAME = {0: 'none', 1: 'small', 3: 'middle', 4: 'large'}
#: Per guard reaction, the first frame the blocker can R1 out of it, and the frames its stamina
#: regeneration stays at 0% (clips 19200/19210/19220, TAE 225 = 0 over that span; disengage.md
#: section 2, `TAE`). A guard break (19500): roll f43, R1 f55, regen 0% for f0-50.
GUARD_REACTION_R1 = {'none': 0, 'small': 5, 'middle': 10, 'large': 50}
GUARD_REACTION_REGEN_PAUSE = {'none': 0, 'small': 10, 'middle': 27, 'large': 49}
GUARD_BREAK_R1, GUARD_BREAK_REGEN_PAUSE = 55, 50
#: `FUN_140d22e90` (table 0x142bafc88): a player defender reacts to `dmgLevel_vsPlayer` through
#: this remap when it is nonzero, else to `dmgLevel`; a guarded hit keeps that level
#: (`FUN_140690250`). Same table as `er-mechanics-frame-advantage.REMAP` (the selftest compares).
DAMAGE_LEVEL_REMAP = (0, 0, 8, 1, 2, 3, 7, 4, 6, 9, 5, 10, 11)
#: Stamina regeneration while guarding (`FUN_1404016d0`): `GetStaminaRecoverySpeed` (45 + talisman
#: adds) x `ctrlModifier+0x14` / 100 per second. Every `GuardOn`/`GuardStart` clip carries TAE 225
#: (`SetSPRegenRatePercent`) = 20 for its whole length (`TAE`), so a raised guard regenerates 20%.
#: Per second is `INFERRED`; talisman adds are not read for blockers.
BASE_STAMINA_REGEN_PER_S = 45.0
GUARD_REGEN_RATE = 0.20
GUARD_REGEN_PER_FRAME = BASE_STAMINA_REGEN_PER_S * GUARD_REGEN_RATE / 30.0
#: A repelled attacker plays `W_Repelled_Small` (31910/33910/35910) or `W_Repelled_Large`
#: (31920/...), picked by a damage level not traced here. Small allows roll and R1 at f22, large
#: roll f27, R1 f30 (`TAE`, categories 20/23/31 identical). The small one is used: it is the
#: shorter window, so the blocker's punish is not overstated.
REPELLED_ATTACKER_FREE = 22
#: A repel's punish in the score: a blocker whose own R1 lands inside that window (its guard
#: reaction's R1 frame plus its R1's first hit frame below `REPELLED_ATTACKER_FREE`) is taken to
#: land one hit worth as much as the attacker's (`INFERRED` weight).
SCORE_REPEL_PUNISH = 1.0

GUARD_FIELDS = ['guardBaseRepel', 'attackBaseRepel', 'staminaGuardDef', 'physGuardCutRate',
                'magGuardCutRate', 'fireGuardCutRate', 'thunGuardCutRate', 'darkGuardCutRate',
                'guardLevel', 'wepType', 'reinforceTypeId', 'isDualBlade', 'weaponCategory',
                'wepmotionCategory', 'spAtkcategory', 'overStrength', 'saGuardCutRate',
                'properStrength', 'properAgility', 'properMagic', 'properFaith', 'properLuck',
                'slashGuardCutRate', 'blowGuardCutRate', 'thrustGuardCutRate',
                'guardCutCancelRate']
REINFORCE_GUARD_FIELDS = ['staminaGuardDefRate', 'physicsGuardCutRate', 'magicGuardCutRate',
                          'fireGuardCutRate', 'thunderGuardCutRate', 'darkGuardCutRate']
GUARD_ATK_FIELDS = ['guardBreakCorrection', 'guardStaminaCutRate', 'guardRate',
                    'finalDamageRateId', 'guardCutCancelRate', 'dmgLevel', 'dmgLevel_vsPlayer']
#: (element, EquipParamWeapon cut, ReinforceParamWeapon rate, attacks.py MV key)
ELEMENT_CUTS = [
    ('physical', 'physGuardCutRate', 'physicsGuardCutRate', 'mv_phys'),
    ('magic', 'magGuardCutRate', 'magicGuardCutRate', 'mv_mag'),
    ('fire', 'fireGuardCutRate', 'fireGuardCutRate', 'mv_fire'),
    ('lightning', 'thunGuardCutRate', 'thunderGuardCutRate', 'mv_light'),
    ('holy', 'darkGuardCutRate', 'darkGuardCutRate', 'mv_holy'),
]
#: `atkAttribute` 0 slash, 1 strike, 2 pierce (defense.md) -> the defender's type-specific cut.
TYPE_CUT_FIELDS = {0: 'slashGuardCutRate', 1: 'blowGuardCutRate', 2: 'thrustGuardCutRate'}


class Tables(ATK.Regulation):
    """attacks.py's regulation plus the guard columns this module reads."""

    def __init__(self, regulation=None):
        super().__init__(regulation)
        files = PR.load(regulation)
        rows, _, _ = PR.rows(PR.param_bytes(files, 'EquipParamWeapon'), GUARD_FIELDS)
        for r in rows:
            if r['id'] in self.weapon:
                self.weapon[r['id']].update({k: r[k] for k in GUARD_FIELDS})
        rows, _, _ = PR.rows(PR.param_bytes(files, 'ReinforceParamWeapon'), REINFORCE_GUARD_FIELDS)
        for r in rows:
            self.reinforce.setdefault(r['id'], {}).update({k: r[k] for k in REINFORCE_GUARD_FIELDS})
        rows, _, _ = PR.rows(PR.param_bytes(files, 'AtkParam_Pc'), GUARD_ATK_FIELDS)
        self.atk_guard = {r['id']: r for r in rows}
        rows, _, _ = PR.rows(PR.param_bytes(files, 'FinalDamageRateParam'), None)
        self.final_rate = {r['id']: r for r in rows}
        # The HP share a repelled hit keeps (`FUN_1404f7280`: min with 1 - the defender's largest
        # SpEffect flickDamageCutRate / 100, no such SpEffect modelled): 0.5 in 1.17.1.
        rows, _, _ = PR.rows(PR.param_bytes(files, 'GameSystemCommonParam'),
                             ['flickDamageCutRateSuccessGurad'])
        self.repel_chip = max(0.0, min(1.0, rows[0]['flickDamageCutRateSuccessGurad']))

    def name(self, wid):
        return self.weapon_names.get(wid) or str(wid)


def _hits(anims, anim):
    """[(judge, source, attack index, (f0, f1))] of one animation's AttackBehavior events."""
    out = []
    for e in anims.get(anim, []):
        if e.type != ATK.TAE_ATTACK_BEHAVIOR:
            continue
        out.append((struct.unpack_from('<i', e.params, TAE_ARG_JUDGE)[0], e.params[TAE_ARG_SOURCE],
                    struct.unpack_from('<i', e.params, TAE_ARG_ATTACK_INDEX)[0],
                    (round(e.start * ATK.TAE_FPS), round(e.end * ATK.TAE_FPS))))
    return out


def _l1_recovery(events):
    """First frame (30 fps) the next L1 can start, attacks.md section 4 pairing."""
    windows = {'input': [], 'cancel': [], 'unresolved_early': 0}
    for e in events:
        if e.type == ATK.TAE_JUMP_TABLE:
            jid = struct.unpack_from('<i', e.params, 0)[0]
            if struct.unpack_from('<H', e.params, ATK.JUMP_TABLE_STATE_GATE_OFFSET)[0]:
                continue
            if jid in L1_INPUT_IDS:
                windows['input'].append((e.start, e.end))
            if jid in L1_CANCEL_IDS:
                windows['cancel'].append((e.start, e.end))
        elif e.type == ATK.TAE_JUMP_TABLE_EARLY:
            jid, early_type = struct.unpack_from('<hh', e.params, 0)
            if jid in L1_CANCEL_IDS and early_type in (ATK.EARLY_DEFAULT,
                                                       ATK.EARLY_WEAPON_WEIGHT_RATE):
                # weaponWeightRate is 0.0 on every row (attacks.md s.4), so both types open at 0.
                w = ATK._early_interval(e, 0.0)
                if w:
                    windows['cancel'].append(w)
    t = ATK._first_open(windows, True)
    return round(t * ATK.TAE_FPS) if t is not None else None


def _level_of(level, wid):
    return level.get(wid, 0) if isinstance(level, dict) else level


def _slot_row(reg, key, label, anim, state, anims, category, hand_weapon, level, allowed):
    """One animation's damaging hits, each resolved against the weapon of its hand."""
    hits = []
    for judge, source, index, frames in _hits(anims, anim):
        if not allowed(judge):
            continue
        wid = hand_weapon(source)
        nums = ATK.attack_numbers(reg, wid, judge, _level_of(level, wid))
        if nums is None or (nums['mv_phys'] + nums['mv_mag'] + nums['mv_fire']
                            + nums['mv_light'] + nums['mv_holy']) <= 0:
            continue  # zero-MV 1-frame extras deal no damage (attacks.md s.4)
        hits.append({'judge': judge, 'hand': {SOURCE_RIGHT: 'right', SOURCE_LEFT: 'left'}.get(
                         source, 'default'),
                     'weapon': wid, 'attack_index': index, 'frames': frames,
                     **{k: nums[k] for k in ('behavior_row', 'atk_row', 'mv_phys', 'mv_mag',
                                             'mv_fire', 'mv_light', 'mv_holy', 'poise_damage',
                                             'stamina_cost', 'stamina_damage',
                                             'guard_level_base', 'atk_attribute')}})
    if not hits:
        return None
    hits.sort(key=lambda h: h['frames'][0])
    hyper = []
    for e in anims.get(anim, []):
        if e.type != ATK.TAE_TOUGHNESS:
            continue
        row = reg.toughness.get(e.params[0], {})
        # Byte 1 picks the weapon term, byte 2 the hand (attacks.md s.2; 2 = left, `INFERRED`).
        wid = hand_weapon(SOURCE_LEFT if e.params[2] == SOURCE_LEFT else SOURCE_RIGHT)
        bonus = (ATK.TOUGHNESS_SCALE * row.get('correctionRate', 0.0)
                 * reg.weapon[wid]['toughnessCorrectRate'] if e.params[1] in (1, 2) else 0.0)
        hyper.append({'frames': (round(e.start * ATK.TAE_FPS), round(e.end * ATK.TAE_FPS)),
                      'toughness_row': e.params[0], 'poise_bonus': round(bonus, 3)})
    hit_end = max(h['frames'][1] for h in hits)
    rec = ATK.recovery_details(reg.weapon[hand_weapon(SOURCE_RIGHT)], anim, anims[anim],
                               [h['frames'] for h in hits])
    next_l1 = _l1_recovery(anims[anim])
    # FUN_1404428f0 charges BehaviorParam stamina when it creates a hitbox and returns early only
    # when the event's damage slot (AttackIndex) already holds the same behavior id (attacks.md
    # s.4), so every distinct (slot, judge) pays (INFERRED: the charge order was read, the early
    # return's effect on the charge was not).
    per_index = {(h['attack_index'], h['judge']): h for h in hits}
    return {
        'slot': key, 'label': label, 'anim': f'a{category:03d}_{anim:06d}', 'state': state,
        'hits': hits, 'hit_count': len(hits),
        'mv_sum': sum(h['mv_phys'] for h in hits),
        'poise_sum': round(sum(h['poise_damage'] for h in hits), 3),
        'poise_max': max(h['poise_damage'] for h in hits),
        'stamina_cost_sum': sum(h['stamina_cost'] for h in per_index.values()),
        'stamina_damage_sum': round(sum(h['stamina_damage'] for h in hits), 3),
        'first_hit': hits[0]['frames'][0], 'last_hit': hit_end,
        'hyperarmor': hyper,
        'next_l1': next_l1, 'next_l1_after_hit': next_l1 - hit_end if next_l1 is not None else None,
        'dodge': rec['cancel_frame'].get('dodge'), 'anim_frames': rec['anim_frames'],
    }


def powerstance_rows(reg, right, left, level=0):
    """Powerstance slots for `right` + `left` (EquipParamWeapon ids); `level` int or {id: level}.

    The animation comes from the right weapon's `wepmotionCategory` TAE (`INFERRED`; the pair
    shares it except katana + Wakizashi). Each hit uses its Source hand's weapon.
    """
    category = reg.weapon[right]['wepmotionCategory']
    anims = ATK.tae_animations(category) or {}

    def hand(source):
        return left if source == SOURCE_LEFT else right

    rows = [_slot_row(reg, key, label, anim, state, anims, category, hand, level,
                      lambda j: j in DUAL_JUDGES) for key, label, anim, state in DUAL_SLOTS]
    return [r for r in rows if r]


def offhand_rows(reg, weapon, level=0):
    """Off-hand L1 slots of `weapon` in the left hand (no powerstance pair)."""
    category = reg.weapon[weapon]['wepmotionCategory']
    anims = ATK.tae_animations(category) or {}
    rows = [_slot_row(reg, key, label, anim, state, anims, category, lambda s: weapon, level,
                      lambda j, want=judge: j == want)
            for key, label, judge, anim, state in OFFHAND_SLOTS]
    return [r for r in rows if r]


def can_powerstance(reg, right, left, two_handing=False):
    """`IsEnableDualWielding` from the behavior script (see the module docstring)."""
    if two_handing:
        return False
    a, b = reg.weapon[right], reg.weapon[left]
    rk, lk = a['wepmotionCategory'], b['wepmotionCategory']
    rs, ls = a.get('spAtkcategory'), b.get('spAtkcategory')
    if rk == CATEGORY_SHORT_SWORD:
        if rs == SPECIAL_CATEGORY_WAKIZASHI:
            return ls == SPECIAL_CATEGORY_WAKIZASHI
        return lk == CATEGORY_SHORT_SWORD and ls != SPECIAL_CATEGORY_WAKIZASHI
    if rk == CATEGORY_KATANA:
        return lk == CATEGORY_KATANA or ls == SPECIAL_CATEGORY_WAKIZASHI
    return rk in POWERSTANCE_CATEGORIES and lk == rk


# ---------------------------------------------------------------------------------------------
# Blocking.


def shield_guard(reg, shield, level=0, guard_judge=None, two_handed=False):
    """The defender's guard numbers for `shield` (any weapon id) held up at `level`. The judge
    defaults to 460 one-handed and 470 two-handed (`GUARD_JUDGE_TWO_HAND`)."""
    if guard_judge is None:
        guard_judge = GUARD_JUDGE_TWO_HAND if two_handed else GUARD_JUDGE_DEFAULT
    s = reg.weapon[shield]
    rf = reg.reinforce.get(s['reinforceTypeId'] + level, {})
    bid = reg.resolve_behavior_id(guard_judge, s['behaviorVariationId'])
    b = reg.behavior.get(bid) or {}
    g_atk = reg.atk_guard.get(b.get('refId'), {}) if b.get('refType') == 0 else {}
    boost = (s['staminaGuardDef'] * rf.get('staminaGuardDefRate', 1.0) + GUARD_BOOST_ADD) \
        * (1 + g_atk.get('guardStaminaCutRate', 0) / 100.0)
    boost = max(0.0, min(100.0, boost))
    two = 1.0
    if two_handed:
        two = TWO_HAND_SHIELD_GUARD if s['weaponCategory'] == WEAPON_CATEGORY_SHIELD else TWO_HAND_GUARD
    return {
        'weapon': shield, 'name': reg.name(shield), 'level': level, 'wepType': s['wepType'],
        'guard_behavior_row': bid,
        'guard_base_repel': s['guardBaseRepel'],
        # FUN_14068c3c0 without SpEffects and durability loss; the STR term is 0 because
        # overStrength is 99 on every row.
        'repel_value': int(g_atk.get('guardBreakCorrection', 100) / 100.0 * s['guardBaseRepel']),
        'stamina_guard_def': s['staminaGuardDef'] * rf.get('staminaGuardDefRate', 1.0),
        'guard_boost': boost,
        'guard_behavior_stamina': b.get('stamina', 0),
        'two_hand_factor': two,
        'guard_rate': g_atk.get('guardRate', 0),
        'cuts': {el: s[field] * rf.get(rate, 1.0) for el, field, rate, _ in ELEMENT_CUTS},
        'type_cuts': {k: s[f] for k, f in TYPE_CUT_FIELDS.items()},
        'guard_level_class': s['guardLevel'],
        'sa_guard_cut_rate': s['saGuardCutRate'],
    }


def attacker_repel(reg, hit, strength=0):
    """0x14068c080 without durability loss: int(base + clamp(STR - overStrength, 0, 10))."""
    over = reg.weapon[hit['weapon']]['overStrength'] if 'weapon' in hit else 99
    return int(hit['guard_level_base'] + max(0, min(10, strength - over)))


def pvp_stamina_rate(reg, hit):
    """FinalDamageRateParam[finalDamageRateId].staminaRate (+0x14), 1.0 when the row is absent."""
    fid = reg.atk_guard.get(hit['atk_row'], {}).get('finalDamageRateId')
    return reg.final_rate.get(fid, {}).get('staminaRate', 1.0)


def guard_cut_cancel(reg, hit):
    """info+0x5c (`FUN_140652610`): (1 + weapon guardCutCancelRate / 100) x (1 + attack
    guardCutCancelRate / 100). It multiplies the guard's cut of every element. The attack's
    AttackInfo+0xf4 bypass (1.0) is not identified and not modelled. 1.0 without a weapon id."""
    w = reg.weapon.get(hit.get('weapon'), {}).get('guardCutCancelRate', 0) if 'weapon' in hit else 0
    a = reg.atk_guard.get(hit.get('atk_row'), {}).get('guardCutCancelRate', 0)
    return (1 + w / 100.0) * (1 + a / 100.0)


def guarded_damage_level(reg, hit):
    """The damage level a player blocker's guard reaction is looked up with: `dmgLevel_vsPlayer`
    through `DAMAGE_LEVEL_REMAP` when nonzero, else `dmgLevel` (frame-advantage.md section 1)."""
    row = reg.atk_guard.get(hit.get('atk_row'), {})
    v = row.get('dmgLevel_vsPlayer') or 0
    level = row.get('dmgLevel', hit.get('dmg_level', 0)) or 0
    return DAMAGE_LEVEL_REMAP[v] if 0 < v < len(DAMAGE_LEVEL_REMAP) else level


def guard_reaction(guard_level, damage_level):
    """'none', 'small', 'middle' or 'large' from `GUARD_REACTION_TABLE`."""
    if not 0 <= guard_level < len(GUARD_REACTION_TABLE) or not 0 <= damage_level < 12:
        return 'none'
    return GUARD_REACTION_NAME.get(GUARD_REACTION_TABLE[guard_level][damage_level], 'none')


def block_hit(reg, hit, ar, guard, strength=0, pvp=True, stamina_rate=1.0):
    """One hit into a raised guard. `ar` {element: attack rating} of the hitting weapon.

    `stamina_rate` is the attacker's product of SpEffect `staminaAttackRate` (Hammer Talisman 1.4,
    Determination 3, Royal Knight's Resolve 4, Cragblade 1.5): `FUN_14068aa80` multiplies it into
    info+0x28, the stamina damage, before the blocker's guard boost.

    `chip_fraction` is the share of each element the guard passes, with the attacker's
    guard-cut cancel (`guard_cut_cancel`) in; it multiplies each element's damage after defense.
    `chip_hp_rate` is what a repel leaves of that chip (`Tables.repel_chip`, 1 when not repelled).
    `chip_raw` is AR * MV * fraction * that rate before defense, for comparing attacks only.
    A repel charges the full stamina and can still break the guard: both are computed before the
    repel multiplier in `CalculateDamage`.
    """
    atk = attacker_repel(reg, hit, strength)
    stamina = ((1 - guard['guard_boost'] / 100.0) * hit['stamina_damage'] * stamina_rate
               + guard['guard_behavior_stamina']) * guard['two_hand_factor']
    if pvp:
        stamina *= pvp_stamina_rate(reg, hit)
    repelled = guard['repel_value'] >= atk
    hp_rate = reg.repel_chip if repelled else 1.0
    gcc = guard_cut_cancel(reg, hit)
    type_cut = guard['type_cuts'].get(hit.get('atk_attribute'), 0)
    frac, chip = {}, {}
    for el, _, _, mv in ELEMENT_CUTS:
        extra = (1 + type_cut / 100.0) if el == 'physical' else 1.0
        frac[el] = (100.0 - extra * guard['cuts'][el] * (1 + guard['guard_rate'] / 100.0) * gcc) / 100.0
        raw = ar.get(el, 0.0) * hit[mv] / 100.0
        if raw:
            chip[el] = raw * max(0.0, frac[el]) * hp_rate
    return {
        'attacker_repel': atk, 'defender_repel': guard['repel_value'],
        'repelled': repelled,
        'stamina_to_blocker': max(0, int(stamina)),
        'guard_cut_cancel': gcc, 'chip_hp_rate': hp_rate,
        'reaction': guard_reaction(guard['guard_level_class'], guarded_damage_level(reg, hit)),
        'chip_fraction': frac, 'chip_raw': chip, 'chip_raw_total': sum(chip.values()),
    }


def blocker_ready(reaction, broken=False):
    """(R1 frame, regen-pause frames) of the blocker after a guard reaction or a guard break."""
    if broken:
        return GUARD_BREAK_R1, GUARD_BREAK_REGEN_PAUSE
    return GUARD_REACTION_R1[reaction], GUARD_REACTION_REGEN_PAUSE[reaction]


def repel_punishes(reaction, startup):
    """Whether a blocker whose R1 first hits on frame `startup` lands it before a repelled
    attacker can roll (`REPELLED_ATTACKER_FREE`); False when `startup` is unknown."""
    if startup is None or startup != startup:
        return False
    return GUARD_REACTION_R1[reaction] + startup < REPELLED_ATTACKER_FREE


def _ar_for(ar, hit):
    """`ar` is {element: AR} for one weapon or {weapon id: {element: AR}} for a pair."""
    if any(isinstance(v, dict) for v in ar.values()):
        return ar.get(hit.get('weapon'), {})
    return ar


def block_matrix(reg, attacks, shields, ar, strength=0, blocker_stamina=None, pvp=True,
                 stamina_rate=1.0):
    """[{attack, shield, ...}] for attack rows (attacks.py or this module) and shield specs.

    `shields` holds weapon ids or (id, level) tuples. `hits_to_break` counts identical
    attacks until `blocker_stamina` is reached, with no regeneration between them.
    `stamina_rate` is the attacker's `staminaAttackRate` product (`block_hit`).
    """
    out = []
    for row in attacks:
        # This module's rows carry a hit list; attacks.py rows are one hit (`hits` is a count).
        hits = row['hits'] if isinstance(row.get('hits'), list) else [row]
        for spec in shields:
            g = shield_guard(reg, *spec) if isinstance(spec, tuple) else shield_guard(reg, spec)
            per = [block_hit(reg, h, _ar_for(ar, h), g, strength, pvp, stamina_rate) for h in hits]
            stam = sum(p['stamina_to_blocker'] for p in per)
            out.append({
                'attack': row['label'], 'shield': g['name'], 'shield_level': g['level'],
                'stamina_to_blocker': stam,
                'hits_to_break': (-(-blocker_stamina // stam) if stam and blocker_stamina else None),
                'chip_raw_total': round(sum(p['chip_raw_total'] for p in per), 1),
                'chip_fraction_phys': round(per[0]['chip_fraction']['physical'], 4),
                'repelled': any(p['repelled'] for p in per),
                'attacker_repel': max(p['attacker_repel'] for p in per),
                'defender_repel': g['repel_value'],
                'guard_cut_cancel': round(per[0]['guard_cut_cancel'], 4),
                'reaction': per[0]['reaction'],
            })
    return out


# ---------------------------------------------------------------------------------------------
# Corpus blockers: which guard each PvP build of an RL window raises, and what one attack slot does
# to that distribution.

#: `er-builds-pvp.PVP_TAGS`, `is_pvp` and `pvp_corpus`'s filter, repeated so the ranking can import
#: this module without a cycle. The selftest checks both select the same builds.
PVP_TAGS = {'Invasions', 'Duels', 'Co-op/Gank', '2v2', 'Ladder', 'Fishing'}
LEVEL_OFFSET = 79
ATTRS = ('vig', 'mnd', 'vit', 'str', 'dex', 'int', 'fth', 'arc')
ELEMENTS = ('physical', 'magic', 'fire', 'lightning', 'holy')
#: Planner `computed.absorption` key per damage type (as `er-builds-pvp.ABSORB_KEY`).
ABSORB_KEY = {'slash': 'slash', 'strike': 'strike', 'pierce': 'pierce', 'standard': 'physical',
              'magic': 'magic', 'fire': 'fire', 'lightning': 'lightning', 'holy': 'holy'}
FINAL_RATE = {'physical': 'physRate', 'magic': 'magRate', 'fire': 'fireRate',
              'lightning': 'thunRate', 'holy': 'darkRate'}
#: Physical type name -> `atkAttribute`, for the defender's type-specific guard cut.
PHYS_ATTRIBUTE = {'slash': 0, 'strike': 1, 'pierce': 2, 'standard': 3}
#: The left hand when the planner lists nothing there (`VERIFIED` regulation row 110000
#: "Unarmed": wepmotionCategory 42 `FIST`, staminaGuardDef 10, physGuardCutRate 30).
UNARMED = 110000
#: Guard hand (`COMMUNITY` Smithbox `c0000.hks`, `Guard_Activate` and `IsEnableGuard`): the left
#: hand's weapon unless the style is `HAND_RIGHT_BOTH`, then the right hand's. A powerstance pair
#: refuses the guard (`IsEnableDualWielding ~= -1`), and `IsWeaponCanGuard` looks the guard
#: hand's category up in `WeaponCategoryID` from the compiled `common_define.hks` (column 2 while
#: one-handing, column 3 while two-handing), refusing only an explicit `FALSE`.
#: The table is `VERIFIED` bytecode, read by `scripts/er-hks-weapon-category-table.py` and
#: compared in the selftest; that the category is `wepmotionCategory` is `INFERRED` (module
#: docstring). One-handed, only these guard from the left hand: torch 21 and shields 47, 48, 49,
#: 57. So a bare left hand (`FIST` 42), a seal or staff (41) and every melee weapon cannot.
GUARD_LEFT_ONE_HAND = frozenset({21, 47, 48, 49, 57})
#: Two-handed, every listed category guards except bow 44, greatbow 45, crossbow 46, light bow 51
#: and ballista 52. Unlisted categories (54, 59) return nil, which is not `FALSE`, so they guard.
GUARD_REFUSED_TWO_HAND = frozenset({44, 45, 46, 51, 52})
#: `EquipParamWeapon.weaponCategory` -> a label for the left hand that cannot guard.
LEFT_KIND = {8: 'catalyst', 10: 'bow', 11: 'bow', 13: 'bow', 14: 'bow'}
#: 2H strength for a requirement check (`FUN_140690390` applies x1.5 while two-handing; that the
#: guard's own requirement test reads the same value is `INFERRED`).
TWO_HAND_STR = 1.5
PERCENTILES = (10, 25, 50, 75, 90)

#: Guard pressure in the combined score (`er-builds-pvp.py --sort score`). Both weights are
#: modelling choices (`INFERRED`); the corpus supplies who can block and with what, not how often.
#: The share of an attacker's hits that meet a raised guard when the defender has one up.
SCORE_GUARD_BLOCK_RATE = 0.25
#: How much a guard that stops the corpus's opening hit completely is worth to the weapon's own
#: score, relative to a guard that stops nothing.
SCORE_GUARD_OWN_WEIGHT = 0.1


def plain_name(name):
    """The planner writes `Miséricorde` and `Great Épée`; the regulation names drop the accents."""
    return ''.join(c for c in unicodedata.normalize('NFKD', name) if not unicodedata.combining(c))


def is_pvp(build):
    if build.get('isPvE') is True:
        return False
    return build.get('isPvE') is False or bool(set(build.get('tags') or []) & PVP_TAGS)


def blocker_corpus(mirror=None, rl_lo=140, rl_hi=160):
    """The builds `er-builds-pvp.pvp_corpus` keeps (same filter, same order), whole build dicts."""
    out = []
    with open(mirror or os.path.join(CACHE, 'builds.jsonl')) as handle:
        for line in handle:
            b = json.loads(line)['build']
            st = b.get('stats') or {}
            try:
                st = {'rl': int(st['rl']), **{k: int(st[k]) for k in ATTRS}}
            except (KeyError, TypeError, ValueError):
                continue
            if not rl_lo <= st['rl'] <= rl_hi or not is_pvp(b):
                continue
            if sum(st[k] for k in ATTRS) - LEVEL_OFFSET != st['rl']:
                continue
            c = b.get('computed') or {}
            if not c.get('defenses') or not c.get('absorption'):
                continue
            out.append(b)
    return out


def _active_slots(build):
    """{equip position: slot} worn in the build's active weapon set (`er-builds-embed.equipped`)."""
    sets = (build.get('sets') or {}).get('weapons') or []
    active = next((i for i, s in enumerate(sets) if s.get('active')), 0)
    out = {}
    for s in (build.get('inventory') or {}).get('slots') or []:
        es = s.get('equipSet')
        pos = (es[active] if active < len(es) else None) if isinstance(es, list) else s.get('equipIndex')
        if pos is not None and s.get('name'):
            out[pos] = s
    return out


def _somber_level(upgrade, max_level):
    """A smithing level (0-25) on a weapon of `max_level` (`INFERRED` equivalence: somber +n is
    smithing +floor(2.5 n), the planner stores one slider for both)."""
    if max_level >= 25:
        return min(upgrade, max_level)
    return max(n for n in range(max_level + 1) if math.floor(2.5 * n) <= upgrade)


class Blockers:
    """The guard every build of a PvP corpus raises, as arrays aligned with the corpus order.

    Per build: the guard weapon (left hand one-handed, right hand two-handed), its affinity row
    and level, whether the build meets its stat requirements (`FUN_14068c3c0` and `FUN_140684540`
    drop repel and guard boost to 0 otherwise), max stamina, flat defense and absorption.
    `curve(attack, defenses)` is the defense curve over an array; the ranking passes its own
    `er-builds-pvp.defense_curve` (the default vectorises the scalar one and is slow).
    """

    def __init__(self, reg, builds, curve=None):
        self.reg = reg
        self.curve = curve
        self._per_guard = {}
        self._builds = builds
        self._opening = None
        self._own_cache = {}
        self._shields = None
        self.n = len(builds)
        ar = _ar_module()
        self._aff = {a.lower(): i for i, a in enumerate(ar.AFFINITIES)}
        self._by_name = {}
        for wid in sorted(reg.weapon):
            n = reg.weapon_names.get(wid)
            if n and wid % 10000 == 0 and n not in self._by_name:
                self._by_name[n] = wid
        self.stamina = np.array([float((b.get('computed') or {}).get('maxStamina') or 0) for b in builds])
        comp = [b['computed'] for b in builds]
        self.defense = {el: np.array([c['defenses'][el] for c in comp], float) for el in ELEMENTS}
        self.mult = {t: 1.0 - np.array([c['absorption'][k] for c in comp], float) / 100.0
                     for t, k in ABSORB_KEY.items()}
        self.guards, self.kind, index, self.unmatched = [], [], [], collections.Counter()
        keys = {}
        for b in builds:
            desc = self.guard_of(b)
            self.kind.append(desc['kind'])
            if desc['weapon'] is None:
                index.append(-1)
                continue
            key = (desc['weapon'], desc['level'], desc['two_handed'], desc['stats_met'])
            if key not in keys:
                g = shield_guard(reg, desc['weapon'], desc['level'], two_handed=desc['two_handed'])
                if not desc['stats_met']:
                    g = dict(g, guard_boost=0.0, repel_value=0)
                keys[key] = len(self.guards)
                self.guards.append(dict(g, kind=desc['kind'], stats_met=desc['stats_met']))
            index.append(keys[key])
        self.index = np.array(index, int)
        self.can = self.index >= 0

    # -- which guard -------------------------------------------------------------------------

    def _weapon_id(self, slot):
        base = self._by_name.get(plain_name(slot['name']))
        if base is None:
            self.unmatched[slot['name']] += 1
            return None
        wid = base + 100 * self._aff.get((slot.get('infusion') or 'Standard').lower(), 0)
        return wid if wid in self.reg.weapon else base

    def _level(self, wid, slot, build):
        up = slot.get('upgrade') if slot.get('upgrade') is not None else build.get('weaponUpgrade')
        rt = self.reg.weapon[wid]['reinforceTypeId']
        top = 0
        while rt + top + 1 in self.reg.reinforce:
            top += 1
        return _somber_level(int(up or 0), top)

    def guard_of(self, build):
        """{'kind', 'weapon', 'level', 'two_handed', 'stats_met'}; weapon None when the build's
        loaded stance cannot raise a guard (`GUARD_LEFT_ONE_HAND`, `GUARD_REFUSED_TWO_HAND`)."""
        pos = _active_slots(build)
        st = build.get('stats') or {}
        two = bool(build.get('is2h'))
        none = {'weapon': None, 'level': 0, 'two_handed': two, 'stats_met': False}
        if two:
            slot = pos.get(0)
            wid = UNARMED if slot is None else self._weapon_id(slot)
            if wid is None:
                return dict(none, kind='unmatched name')
            if self.reg.weapon[wid]['wepmotionCategory'] in GUARD_REFUSED_TWO_HAND:
                return dict(none, kind='two-handed bow or crossbow')
            kind = 'two-handed shield' if self.reg.weapon[wid]['weaponCategory'] == \
                WEAPON_CATEGORY_SHIELD else 'two-handed weapon'
        else:
            slot = pos.get(3)
            if slot is None:
                wid = UNARMED
            else:
                wid = self._weapon_id(slot)
                if wid is None:
                    return dict(none, kind='unmatched name')
            if self.reg.weapon[wid]['wepmotionCategory'] not in GUARD_LEFT_ONE_HAND:
                return dict(none, kind='bare left hand' if slot is None else
                            'left ' + LEFT_KIND.get(self.reg.weapon[wid]['weaponCategory'], 'weapon'))
            # A pair would refuse the guard, but no torch or shield category powerstances.
            kind = 'shield' if self.reg.weapon[wid]['wepType'] in SHIELD_TYPES else 'other guard item'
        level = 0 if slot is None else self._level(wid, slot, build)
        return {'kind': kind, 'weapon': wid, 'level': level, 'two_handed': two,
                'stats_met': self.meets(wid, st, two)}

    def meets(self, wid, stats, two=False):
        """Whether `stats` meet weapon `wid`'s requirements (STR x1.5 two-handed)."""
        w = self.reg.weapon[wid]
        need = {'str': w['properStrength'], 'dex': w['properAgility'], 'int': w['properMagic'],
                'fth': w['properFaith']}
        have = {k: float(stats.get(k) or 0) * (TWO_HAND_STR if k == 'str' and two else 1.0) for k in need}
        return all(have[k] >= need[k] for k in need)

    # -- the distribution ----------------------------------------------------------------------

    def distribution(self):
        """Who blocks with what: kind shares, the guard weapons, and the guard numbers' spread."""
        kinds = collections.Counter(self.kind)
        by_guard = collections.Counter(int(i) for i in self.index if i >= 0)
        top = [(self.guards[i]['name'], self.guards[i]['level'], self.guards[i]['two_hand_factor'], c)
               for i, c in by_guard.most_common(15)]

        def spread(vals):
            v = np.array(vals, float)
            if not len(v):
                return {}
            return {'mean': float(v.mean()), **{f'p{q}': float(np.percentile(v, q)) for q in PERCENTILES}}

        idx = self.index[self.can]
        return {'builds': self.n, 'can_block': float(self.can.mean()) if self.n else 0.0,
                'kinds': dict(kinds.most_common()), 'top_guards': top,
                'guard_boost': spread([self.guards[i]['guard_boost'] for i in idx]),
                'phys_cut': spread([self.guards[i]['cuts']['physical'] for i in idx]),
                'repel': spread([self.guards[i]['repel_value'] for i in idx]),
                'stats_unmet': int(sum(not self.guards[i]['stats_met'] for i in idx)),
                'stamina': spread(self.stamina[self.can]),
                'unmatched_names': self.unmatched.most_common(5)}

    # -- one attack slot against the distribution -----------------------------------------------

    def _unguarded(self, scaled, phys, fr, post):
        """Per element, per build: the hit's damage with no guard (`er-builds-pvp.corpus_hit`)."""
        curve = self.curve or _vector_curve()
        per = {}
        for el in ELEMENTS:
            key = el if el != 'physical' else phys
            d = curve(scaled.get(el, 0.0), self.defense[el])
            if key in self.mult:
                d = d * self.mult[key]
            if fr:
                d = d * fr[FINAL_RATE[el]]
            if post:
                d = d * post.get(el, 1.0)
            per[el] = np.maximum(d, 0.0)
        return per

    def slot_pressure(self, parts, block_rate=SCORE_GUARD_BLOCK_RATE, cycle=None):
        """One attack slot into every build's raised guard.

        `parts` is one entry per separate hit of the slot: {'hit': attack row (`stamina_damage`,
        `guard_level_base`, `atk_row`, `phys_type`, and `weapon` for its guard-cut cancel),
        'scaled': {element: attack after the motion value and PvP weapon rate}, 'fr':
        FinalDamageRateParam row or None, 'post': {element: after-defense factor} or None, 'n':
        sweep hits of that row, 'stamina_rate': the attacker's `staminaAttackRate` product (1 when
        absent)}. `er-builds-pvp.slot_hit` computes all of these. `cycle` is the frames until the
        same attack can be thrown again (the slot's same-button recovery), or None.

        Per blocker: stamina summed over the hits (`block_hit`: attacker `staminaAttackRate`, PvP
        rate and 2H factor in), `broken` when it reaches max stamina (one attack from full; a
        repel still charges it and can still break, `CalculateDamage` order), `repelled` when any
        hit bounces (`FUN_140447180`: defender repel >= attacker repel), chip = each element's
        unguarded damage x the share the guard passes with the attacker's guard-cut cancel in
        (`CalculateDamageBasic` 0x1406849d0 multiplies `victimGuardDefRate` into the same product
        as armor absorption, `VERIFIED`), times `Tables.repel_chip` on a repelled hit.
        With `cycle`, the guard regenerates `GUARD_REGEN_PER_FRAME` over the frames between two
        throws that are past its guard reaction's 0% window (`blocker_ready`), and `drain` is the
        stamina net of that / max stamina, capped at 1; without it, no regeneration.
        The blocked hit is worth `value` = min(1, chip / unguarded + drain) of a landed one
        (`INFERRED`: a full drain is a guard break, taken as one landed hit's worth), minus
        `SCORE_REPEL_PUNISH` when the hit is repelled and the blocker's own R1 #1 (`startups`)
        lands before the repelled attacker can roll (`repel_punishes`).
        `factor` = 1 - `block_rate` x mean over every build of (can block) x (1 - value).
        """
        if not self.n:
            return None
        g_idx = range(len(self.guards))
        ng = len(self.guards)
        stam = np.zeros(ng)
        repel = np.zeros(ng, bool)
        pause = np.zeros(ng)
        ready = np.zeros(ng)
        dmg = np.zeros(self.n)
        chip = np.zeros(self.n)
        safe = np.where(self.index >= 0, self.index, 0)
        for p in parts:
            hit = dict(p['hit'])
            if hit.get('phys_type') in PHYS_ATTRIBUTE:
                hit['atk_attribute'] = PHYS_ATTRIBUTE[hit['phys_type']]
            n = p.get('n', 1) or 0
            rate = p.get('stamina_rate', 1.0)
            per = self._unguarded(p['scaled'], hit.get('phys_type', 'standard'), p.get('fr'), p.get('post'))
            key = (hit.get('atk_row'), hit.get('atk_attribute'), hit['stamina_damage'],
                   hit['guard_level_base'], hit.get('weapon'), round(rate, 6))
            if key not in self._per_guard:
                s_, r_, f_ = np.zeros(ng), np.zeros(ng, bool), np.zeros((ng, len(ELEMENTS)))
                pz_, rd_ = np.zeros(ng), np.zeros(ng)
                for i in g_idx:
                    r = block_hit(self.reg, hit, {}, self.guards[i], pvp=True, stamina_rate=rate)
                    s_[i], r_[i] = r['stamina_to_blocker'], r['repelled']
                    f_[i] = [max(0.0, r['chip_fraction'][el]) * r['chip_hp_rate'] for el in ELEMENTS]
                    rd_[i], pz_[i] = blocker_ready(r['reaction'])
                self._per_guard[key] = (s_, r_, f_, pz_, rd_)
            s_, r_, fracs, pz_, rd_ = self._per_guard[key]
            stam += s_ * n
            if n > 0:
                repel |= r_
                pause = np.maximum(pause, pz_)
                ready = np.maximum(ready, rd_)
            for k, el in enumerate(ELEMENTS):
                dmg += per[el] * n
                chip += per[el] * fracs[safe, k] * n
        # CalculateDamageBasic lifts a total in (0, 1) to 1 (ceil), guarded or not.
        chip = np.where((chip > 0) & (chip < 1), np.ceil(chip), chip)
        can = self.can
        st_b = stam[safe]
        regen = np.zeros(self.n)
        if cycle:
            regen = GUARD_REGEN_PER_FRAME * np.maximum(0.0, cycle - pause[safe])
        with np.errstate(divide='ignore', invalid='ignore'):
            drain = np.where(self.stamina > 0,
                             np.minimum(1.0, np.maximum(0.0, st_b - regen) / self.stamina), 1.0)
            share = np.where(dmg > 0, chip / dmg, 0.0)
        rep_b = repel[safe] & can
        punish = rep_b & (ready[safe] + self.startups() < REPELLED_ATTACKER_FREE)
        value = np.minimum(1.0, share + drain) - SCORE_REPEL_PUNISH * punish
        loss = np.where(can, 1.0 - value, 0.0)
        broken = (st_b >= self.stamina) & can
        m = can.sum()

        def mean(a):
            return float(a[can].mean()) if m else None

        return {'can_block': float(can.mean()), 'stamina': mean(st_b), 'drain': mean(drain),
                'regen': mean(regen),
                'break_share': float(broken.sum() / m) if m else None,
                'repel_share': float(rep_b.sum() / m) if m else None,
                'punish_share': float(punish.sum() / m) if m else None,
                'chip': mean(chip), 'chip_share': mean(share), 'value': mean(value),
                'loss': float(loss.mean()), 'factor': 1.0 - block_rate * float(loss.mean())}

    # -- the defensive side: a guard against the corpus's opening hits --------------------------

    def opening_hits(self, builds):
        """Each build's right-hand R1 #1 as it holds it (2H when `is2h`): one attack row per build
        with its `weapon` id (None where no row resolves), the incoming side of `own_guard`."""
        cache, out = {}, []
        for b in builds:
            slot = _active_slots(b).get(0)
            wid = self._weapon_id(slot) if slot else None
            if wid is None:
                out.append(None)
                continue
            grip = 'both' if b.get('is2h') else 'one'
            level = self._level(wid, slot, b)
            key = (wid, grip, level)
            if key not in cache:
                try:
                    rows = ATK.weapon_attacks(self.reg, wid, grip, level) or []
                except (KeyError, SystemExit, TypeError):
                    rows = []  # catalysts and bows without an R1 clip the module resolves
                want = ('2h_' if grip == 'both' else '') + 'r1_1'
                row = next((r for r in rows if r['slot'] == want), None)
                cache[key] = dict(row, weapon=wid) if row else None
            out.append(cache[key])
        if builds is self._builds:
            self._opening = out
        return out

    def startups(self):
        """Each build's R1 #1 first hit frame (`opening_hits` of the corpus, NaN where none), as an
        array aligned with the corpus: the blocker's punish after a repel."""
        if self._opening is None:
            self.opening_hits(self._builds)
        if not hasattr(self, '_startups'):
            self._startups = np.array([(h.get('hit_windows') or [[np.nan]])[0][0] if h else np.nan
                                       for h in self._opening], float)
        return self._startups

    def _own_parts(self, guard, opening, stamina):
        """Per incoming opening hit into `guard`: the value without a punish credit, whether it is
        repelled, and the blocker's R1 frame out of the guard reaction (arrays, cached)."""
        key = (guard['weapon'], guard['level'], guard['two_hand_factor'], guard['guard_boost'],
               guard['repel_value'], id(opening), stamina)
        if key not in self._own_cache:
            base, rep, ready = [], [], []
            for hit in opening:
                if hit is None:
                    continue
                h = dict(hit)
                if h.get('phys_type') in PHYS_ATTRIBUTE:
                    h['atk_attribute'] = PHYS_ATTRIBUTE[h['phys_type']]
                r = block_hit(self.reg, h, {}, guard, pvp=True)
                passed = max(0.0, r['chip_fraction']['physical']) * r['chip_hp_rate'] \
                    + r['stamina_to_blocker'] / stamina
                base.append(1.0 - min(1.0, passed))
                rep.append(r['repelled'])
                ready.append(blocker_ready(r['reaction'])[0])
            self._own_cache[key] = (np.array(base), np.array(rep, bool), np.array(ready, float))
        return self._own_cache[key]

    def own_guard(self, guard, opening, stamina=None, startup=None):
        """How much of the corpus's opening hit `guard` (a `shield_guard` dict) stops.

        Per incoming hit: 1 - min(1, physical pass share + stamina drained / `stamina`), the pass
        share with the attacker's guard-cut cancel in and halved on a repel (`block_hit`); a
        repel still charges its stamina. A repelled hit adds `SCORE_REPEL_PUNISH` when the
        configuration's own R1 first hits on frame `startup` early enough to land before the
        attacker can roll (`repel_punishes`); so the value can exceed 1. Physical only, because
        the pass share is what the guard owns; the elements pass at their own cut rates.
        `stamina` defaults to the corpus median max stamina.
        """
        if stamina is None:
            stamina = float(np.median(self.stamina[self.stamina > 0])) if self.n else 150.0
        base, rep, ready = self._own_parts(guard, opening, stamina)
        if not len(base):
            return 0.0
        v = base
        if startup is not None and startup == startup:
            v = base + SCORE_REPEL_PUNISH * (rep & (ready + startup < REPELLED_ATTACKER_FREE))
        return float(v.mean())

    def one_hand_guard_mean(self, opening, stamina=None):
        """`own_guard` averaged over the one-handing builds' own guards (a `MEASURED` distribution),
        each with that build's own R1 as the repel punish: what the corpus's one-handed
        configurations block with. Builds that cannot guard count as 0."""
        vals = []
        starts = self.startups()
        for b, (k, i) in enumerate(zip(self.kind, self.index)):
            if k.startswith('two-handed'):
                continue
            if i < 0:
                vals.append(0.0)
                continue
            vals.append(self.own_guard(self.guards[i], opening, stamina, starts[b]))
        return float(np.mean(vals)) if vals else 0.0

    def shield_options(self):
        """The distinct one-handed shields the corpus carries (weapon id with affinity, the
        highest level seen), as `shield_guard` dicts: what a one-handed configuration picks its
        left hand from (`best_left_shield`)."""
        if self._shields is None:
            top = {}
            for g in self.guards:
                if g['kind'] == 'shield':
                    top[g['weapon']] = max(top.get(g['weapon'], 0), g['level'])
            self._shields = [shield_guard(self.reg, w, lvl) for w, lvl in sorted(top.items())]
        return self._shields

    def best_left_shield(self, stats, opening, startup=None, stamina=None):
        """(own_guard, shield dict) of the corpus shield that stops the most of the opening hit
        for a one-handed configuration with `stats` (requirements met one-handed) whose own R1
        first hits on frame `startup`; (0.0, None) when it meets none."""
        best = (0.0, None)
        for g in self.shield_options():
            if not self.meets(g['weapon'], stats, False):
                continue
            v = self.own_guard(g, opening, stamina, startup)
            if best[1] is None or v > best[0]:
                best = (v, g)
        return best


def guard_score_factor(pressure, own=None, own_ref=None, own_weight=SCORE_GUARD_OWN_WEIGHT):
    """The score multiplier: `pressure['factor']` x (1 + `own_weight` x (own - own_ref)).

    `own` is `Blockers.own_guard` of the scored configuration's guard, `own_ref` the corpus mean
    the configuration is compared with; either missing leaves the defensive term at 1."""
    f = pressure['factor'] if pressure else 1.0
    if own is not None and own_ref is not None:
        f *= 1.0 + own_weight * (own - own_ref)
    return f


_MODS = {}


def _ar_module():
    if 'ar' not in _MODS:
        _MODS['ar'] = _load('er_mechanics_ar', 'er-mechanics-ar.py')
    return _MODS['ar']


def _vector_curve():
    """`er-mechanics-defense.defense_curve` over an array of defenses."""
    if 'curve' not in _MODS:
        dm = _load('er_mechanics_defense', 'er-mechanics-defense.py')
        _MODS['curve'] = np.vectorize(dm.defense_curve, otypes=[float])
    return _MODS['curve']


def scaled_for(reg, hit, ar):
    """{element: attack} of one hit for the CLI: AR x MV / 100, no grease, talisman or PvP weapon
    rate (the ranking passes its own `scaled`, which has them)."""
    return {el: ar.get(el, 0.0) * hit[mv] / 100.0 for el, _, _, mv in ELEMENT_CUTS}


def final_rate_row(reg, hit):
    fid = reg.atk_guard.get(hit['atk_row'], {}).get('finalDamageRateId')
    return reg.final_rate.get(fid) if fid is not None and fid >= 0 else None


# ---------------------------------------------------------------------------------------------
# Corpus adoption.


def adoption(rl_lo=1, rl_hi=713, kind='pvptag'):
    """Shields and powerstance pairs over `~/.cache/er-build-planner/builds.jsonl`.

    Filters as `er-builds-adoption-gap.py`: not PvE; `pvptag` = Strength tag and a PvP tag,
    `tag` = Strength tag, `str60` = STR 60+, `all` = any. Deduplicated on (user, equipped tokens).
    Right hand = `equipIndex` 0-2, left 3-5 (`VERIFIED` in the planner bundle).
    """
    gap = _load('er_builds_adoption_gap', 'er-builds-adoption-gap.py')
    embed = gap.EMBED
    reg = Tables()
    by_name = {}
    for wid in sorted(reg.weapon):
        n = reg.weapon_names.get(wid)
        if n and wid % 10000 == 0 and n not in by_name:
            by_name[n] = wid
    seen, builds, unknown = set(), [], collections.Counter()
    with open(os.path.join(CACHE, 'builds.jsonl')) as handle:
        for line in handle:
            row = json.loads(line)
            b = row['build']
            st = embed.stats_of(b)
            if st is None or not rl_lo <= st['rl'] <= rl_hi:
                continue
            tags = set(b.get('tags') or [])
            if b.get('isPvE') or 'PvE' in tags:
                continue
            if kind == 'pvptag' and not ('Strength' in tags and tags & gap.PVP_TAGS):
                continue
            if kind == 'tag' and 'Strength' not in tags:
                continue
            if kind == 'str60' and st['str'] < 60:
                continue
            if sum(st[k] for k in embed.ATTRS) - embed.LEVEL_OFFSET != st['rl']:
                continue
            key = (row.get('user'), tuple(embed.tokens(b)))
            if key in seen:
                continue
            seen.add(key)
            pos = {}
            for p, s in gap.slots_at((b.get('inventory') or {}).get('slots'),
                                     embed.active_set(b, 'weapons')):
                wid = by_name.get(s['name'])
                if wid is None:
                    unknown[s['name']] += 1
                    continue
                pos[p] = wid
            builds.append({'pos': pos, 'user': row.get('user')})
    c = collections.Counter()
    users = collections.defaultdict(set)
    shield_names, pair_names, left_names = (collections.Counter() for _ in range(3))
    for bld in builds:
        pos = bld['pos']

        def count(label):
            c[label] += 1
            users[label].add(bld['user'])

        right = [pos[p] for p in (0, 1, 2) if p in pos]
        left = [pos[p] for p in (3, 4, 5) if p in pos]
        shields = [w for w in left if reg.weapon[w]['wepType'] in SHIELD_TYPES]
        if shields:
            count('shield, any left slot')
            for kind_ in sorted({SHIELD_TYPES[reg.weapon[w]['wepType']] for w in shields}):
                count(kind_ + ', any left slot')
            for w in shields:
                shield_names[reg.name(w)] += 1
        if 3 in pos and reg.weapon[pos[3]]['wepType'] in SHIELD_TYPES:
            count('shield in Left Hand 1')
        if not left:
            count('left hand empty')
        for w in left:
            if reg.weapon[w]['wepType'] not in SHIELD_TYPES:
                left_names[reg.name(w)] += 1
        pairs = [(r, l) for r in right for l in left if can_powerstance(reg, r, l)]
        if pairs:
            count('powerstance pair, any slots')
            for r, l in set(pairs):
                pair_names[f'{reg.name(r)} + {reg.name(l)}'] += 1
            for cat in sorted({reg.weapon[r]['wepmotionCategory'] for r, _ in pairs}):
                count(f'powerstance pair, wepmotionCategory {cat}')
            if any(r == l for r, l in pairs):
                count('powerstance pair, same weapon')
        if 0 in pos and 3 in pos and can_powerstance(reg, pos[0], pos[3]):
            count('powerstance pair, Right Hand 1 + Left Hand 1')
    return {'filter': kind, 'rl': [rl_lo, rl_hi], 'builds': len(builds),
            'users': len({b['user'] for b in builds}),
            'counts': {k: {'builds': v, 'users': len(users[k])} for k, v in c.most_common()},
            'top_shields': shield_names.most_common(10), 'top_pairs': pair_names.most_common(12),
            'top_left_weapons': left_names.most_common(12),
            'unmatched_names': unknown.most_common(5)}


# ---------------------------------------------------------------------------------------------
# CLI.


def weapon_ar(name, level, stats, affinity='Standard', two_handed=False):
    """{element: AR} from `er-mechanics-ar.py`. Off-hand and powerstance hits are one-handed
    (`+0xf5` comes from `ChrIns::IsTwoHanding` only, and powerstance requires one-handing)."""
    ar_mod = _load('er_mechanics_ar', 'er-mechanics-ar.py')
    r = ar_mod.attack_rating(ar_mod.Tables(None), name, affinity, level, stats, two_handed)
    return {k: v['total'] for k, v in r['damage'].items()}


def _dash(v):
    return '-' if v is None else v


def print_slots(rows):
    print(f"{'slot':16}{'hits':>5}{'MV sum':>8}{'poise':>8}{'stam':>6}{'first':>6}{'last':>6}"
          f"{'L1':>5}{'roll':>5}{'len':>5}  per hit (hand judge MV poise frames)  hyperarmor")
    for r in rows:
        per = '; '.join(f"{h['hand'][0]} {h['judge']} {h['mv_phys']} {h['poise_damage']:.2f}"
                        f" f{h['frames'][0]}-{h['frames'][1]}" for h in r['hits'])
        hyper = '; '.join(f"f{h['frames'][0]}-{h['frames'][1]} +{h['poise_bonus']}"
                          for h in r['hyperarmor'])
        print(f"{r['label']:16}{r['hit_count']:>5}{r['mv_sum']:>8}{r['poise_sum']:>8.2f}"
              f"{r['stamina_cost_sum']:>6}{r['first_hit']:>6}{r['last_hit']:>6}"
              f"{_dash(r['next_l1']):>5}{_dash(r['dodge']):>5}{_dash(r['anim_frames']):>5}"
              f"  {per}  {hyper}")
    print('stam = stamina cost summed over hits; L1/roll = first frame the next L1 / a roll can'
          ' start (30 fps); len = clip frames; poise internal (x10 = menu).')


def print_block(reg, matrix):
    print(f"{'attack':24}{'shield':28}{'atkRep':>7}{'defRep':>7}{'bounce':>7}{'stamDmg':>8}"
          f"{'toBreak':>8}{'chip%':>7}{'chipRaw':>8}")
    for x in matrix:
        print(f"{x['attack']:24}{x['shield'] + ' +' + str(x['shield_level']):28}"
              f"{x['attacker_repel']:>7}{x['defender_repel']:>7}{'yes' if x['repelled'] else '':>7}"
              f"{x['stamina_to_blocker']:>8}{_dash(x['hits_to_break']):>8}"
              f"{100 * x['chip_fraction_phys']:>7.1f}{x['chip_raw_total']:>8.1f}")
    print('stamDmg = blocker stamina lost per attack, PvP x FinalDamageRateParam.staminaRate;'
          ' toBreak = identical attacks until the blocker stamina is gone (no regen);'
          ' chip% = physical share that passes the guard; chipRaw = AR x MV x that, before defense.')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('mode', nargs='?', choices=('dual', 'offhand', 'block', 'adoption', 'blockers',
                                                'pressure'))
    ap.add_argument('--affinity', default='Standard', help='pressure: attacker affinity')
    ap.add_argument('weapons', nargs='*', help='EquipParamWeapon id or name (dual: right left)')
    ap.add_argument('--level', type=int, default=25, help='attacker reinforce level')
    ap.add_argument('--shield', action='append', default=[], help='defender guard weapon')
    ap.add_argument('--shield-level', type=int, default=25)
    ap.add_argument('--two-handed-guard', action='store_true')
    ap.add_argument('--blocker-stamina', type=int, default=150,
                    help='default 150: median maxStamina of the Strength+PvP corpus (MEASURED)')
    ap.add_argument('--stats', default='str=66,dex=18,int=9,fth=14,arc=9')
    ap.add_argument('--attacks', choices=('rh', 'dual', 'offhand'), default='rh')
    ap.add_argument('--grip', choices=('one', 'both'), default='one', help='with --attacks rh')
    ap.add_argument('--rl', default='1-713')
    ap.add_argument('--filter', default='pvptag', choices=('pvptag', 'tag', 'str60', 'all'))
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.mode is None:
        ap.error('mode required')
    if a.mode == 'adoption':
        lo, hi = (int(x) for x in a.rl.split('-'))
        print(json.dumps(adoption(lo, hi, a.filter), indent=1))
        return 0
    if a.mode in ('blockers', 'pressure'):
        return corpus_main(a)
    if not a.weapons:
        ap.error('weapon required')
    reg = Tables()
    ids = [reg.find_weapon(w) for w in a.weapons]
    right, left = ids[0], ids[1] if len(ids) > 1 else ids[0]
    if a.mode in ('dual', 'offhand'):
        rows = powerstance_rows(reg, right, left, a.level) if a.mode == 'dual' \
            else offhand_rows(reg, right, a.level)
        if a.mode == 'dual' and not can_powerstance(reg, right, left):
            print(f'note: {reg.name(right)} + {reg.name(left)} does not pass IsEnableDualWielding')
        if a.json:
            print(json.dumps(rows, indent=1))
        else:
            print_slots(rows)
        return 0
    stats = dict(kv.split('=') for kv in a.stats.split(',') if kv)
    if a.attacks == 'dual':
        attacks = powerstance_rows(reg, right, left, a.level)
    elif a.attacks == 'offhand':
        attacks = offhand_rows(reg, right, a.level)
    else:
        attacks = [dict(r, weapon=right) for r in ATK.weapon_attacks(reg, right, a.grip, a.level)]
    two = a.attacks == 'rh' and a.grip == 'both'
    ar = {wid: weapon_ar(reg.name(wid), a.level, stats, two_handed=two) for wid in {right, left}}
    shields = [(reg.find_weapon(s), a.shield_level) for s in a.shield] or [
        (31130000, a.shield_level), (32130000, a.shield_level)]
    shields = [(sid, lvl, None, a.two_handed_guard) for sid, lvl in shields]
    m = block_matrix(reg, attacks, shields, ar, int(stats.get('str', 0)), a.blocker_stamina)
    if a.json:
        print(json.dumps(m, indent=1))
    else:
        print('AR ' + '; '.join(f'{reg.name(w)} ' + ', '.join(f'{k} {v:.0f}' for k, v in d.items())
                                for w, d in ar.items()))
        print_block(reg, m)
    return 0


def slot_parts(reg, wid, attack, level, ar):
    """`Blockers.slot_pressure` parts for one attacks.py row and its separate sweep hitboxes, with
    `scaled_for` damage (the CLI's; the ranking builds its own from `slot_hit`)."""
    parts = [{'hit': dict(attack, weapon=wid), 'scaled': scaled_for(reg, attack, ar),
              'fr': final_rate_row(reg, attack), 'n': attack.get('own_sweep_hits', 1)}]
    for extra in attack.get('other_hitboxes') or []:
        if not extra.get('sweep_hit', True):
            continue
        nums = ATK.attack_numbers(reg, wid, extra['judge'], level)
        if nums:
            parts.append({'hit': dict(nums, weapon=wid), 'scaled': scaled_for(reg, nums, ar),
                          'fr': final_rate_row(reg, nums), 'n': 1})
    return parts


def corpus_main(a):
    lo, hi = (int(x) for x in a.rl.split('-'))
    reg = Tables()
    builds = blocker_corpus(None, lo, hi)
    bl = Blockers(reg, builds)
    if a.mode == 'blockers':
        d = bl.distribution()
        opening = bl.opening_hits(builds)
        d['one_hand_guard_mean'] = bl.one_hand_guard_mean(opening)
        d['opening_hits_resolved'] = sum(h is not None for h in opening)
        print(json.dumps(d, indent=1))
        return 0
    if not a.weapons:
        raise SystemExit('pressure: weapon required')
    ar_mod = _ar_module()
    art = ar_mod.Tables(None)
    wid = art.find_weapon(a.weapons[0], a.affinity)
    level = min(a.level, art.max_level(art.weapons[wid]['reinforceTypeId']))
    stats = {k: int(v) for k, v in (kv.split('=') for kv in a.stats.split(',') if kv)}
    two = a.grip == 'both'
    ar = weapon_ar(a.weapons[0], level, stats, a.affinity, two)
    rows = ATK.weapon_attacks(reg, wid, a.grip, level)
    opening = bl.opening_hits(builds)
    ref = bl.one_hand_guard_mean(opening)
    r1 = next((r for r in rows if r['slot'] in ('r1_1', '2h_r1_1')), None)
    startup = (r1.get('hit_windows') or [[None]])[0][0] if r1 else None
    if two:
        own, left = bl.own_guard(shield_guard(reg, wid, level, two_handed=True), opening,
                                 startup=startup), None
    else:
        own, left = bl.best_left_shield(stats, opening, startup)
    print(f'{reg.name(wid)} +{level} {"2H" if two else "1H"}; {bl.n} PvP builds of RL {lo}-{hi}, '
          f'{100 * bl.can.mean():.0f}% can raise a guard; AR '
          + ', '.join(f'{k} {v:.0f}' for k, v in ar.items() if v))
    print(f'own guard{" (left " + left["name"] + " +" + str(left["level"]) + ")" if left else ""}: '
          f'{own:.3f} of the corpus opening hit stopped, repel punish credited '
          f'(one-handers\' own guards: {ref:.3f})')
    print(f"{'slot':18}{'stam':>6}{'drain%':>7}{'break%':>7}{'bounce%':>8}{'pun%':>6}{'chip':>6}"
          f"{'chip%':>6}{'value':>7}{'factor':>7}")
    for r in rows:
        cf = r.get('cancel_frame') or {}
        button = 'r2' if r['slot'].removeprefix('2h_').startswith('r2') else 'r1'
        cycle = cf.get(button)
        p = bl.slot_pressure(slot_parts(reg, wid, r, level, ar), cycle=cycle)
        if p is None or p['stamina'] is None:
            continue
        f = guard_score_factor(p, own, ref)
        print(f"{r['label'][:17]:18}{p['stamina']:>6.0f}{100 * p['drain']:>7.0f}"
              f"{100 * p['break_share']:>7.0f}{100 * p['repel_share']:>8.0f}"
              f"{100 * p['punish_share']:>6.0f}{p['chip']:>6.0f}"
              f"{100 * p['chip_share']:>6.0f}{p['value']:>7.2f}{f:>7.3f}")
    print('stam = blocker stamina lost (mean over builds that can guard); drain = share of their max'
          ' stamina net of guard regen over the same-button cycle; break = share one attack breaks'
          ' from full; bounce = share it is repelled by; pun = share whose own R1 punishes the'
          ' repel; chip = HP through the guard; value = a blocked hit\'s worth against a landed one;'
          ' factor = guard pressure x own-guard term (SCORE_GUARD_BLOCK_RATE, SCORE_GUARD_OWN_WEIGHT).')
    return 0


def _hks_dual_categories(path):
    """Categories `IsEnableDualWielding` accepts, read from the decompiled HKS text."""
    import re
    text = open(path, encoding='utf-8', errors='replace').read()
    start = text.find('function IsEnableDualWielding')
    body = text[start:text.find('\nend', start)]
    enums_path = os.path.join(os.path.dirname(path), 'LUA', 'Enums.txt')
    enums = {}
    if os.path.exists(enums_path):
        enums = {n: int(v) for n, v in re.findall(
            r'(WEAPON_CATEGORY_\w+)\s*=\s*(\d+)', open(enums_path, encoding='utf-8').read())}
    names = set(re.findall(r'rightKind == (WEAPON_CATEGORY_\w+)', body))
    out = set()
    for n in names:
        unnamed = re.fullmatch(r'WEAPON_CATEGORY_CAT(\d+)', n)
        out.add(int(unnamed.group(1)) if unnamed else enums.get(n))
    return out


def selftest():
    """Checks against references this module does not compute from its own tables."""
    reg = Tables()
    passes, fails, skips = [], [], []

    def check(name, got, want, source):
        (passes if got == want else fails).append(f'{name}: got {got!r} want {want!r} [{source}]')

    # 1. Behavior graph: which state plays which clip (c0000.behbnd, independent of the TAE).
    beh = os.environ.get('ER_BEHBND_JSON')
    if beh and os.path.exists(beh):
        rows = json.load(open(beh))
        states = {r['state']: r['anims'] for rs in rows.values() for r in rs}
        src = 'TAE c0000.behbnd'
        check('AttackDualLight1 plays 034000', 34000 in states.get('AttackDualLight1', []), True, src)
        check('RideAttack_R_Top plays 038000', 38000 in states.get('RideAttack_R_Top', []), True, src)
        check('AttackLeftLight1 plays 035000', 35000 in states.get('AttackLeftLight1', []), True, src)
    else:
        skips.append('behavior graph: set ER_BEHBND_JSON to `er-behbnd-attack-map.py ... --json`'
                     ' output')

    # 2. The Source byte in the powerstance clips: judge 800 right, 805 left (a26/a31).
    for cat in (26, 31):
        anims = ATK.tae_animations(cat)
        if anims is None:
            skips.append(f'a{cat}.tae absent')
            continue
        src = {j: s for j, s, _, _ in _hits(anims, 34000)}
        check(f'a{cat}_034000 judge 800 Source', src.get(800), SOURCE_RIGHT, f'TAE a{cat}.tae')
        check(f'a{cat}_034000 judge 805 Source', src.get(805), SOURCE_LEFT, f'TAE a{cat}.tae')

    # 3. Row names Smithbox authored apart from this tool: 4xx is the off-hand chain.
    names = {r['slot']: reg.atk_names.get(r['hits'][0]['atk_row']) for r in offhand_rows(reg, 1000000)}
    check('dagger off-hand L1 #1 row name', names.get('left_1'),
          'Default - Dagger - Left 1H Light #1', 'COMMUNITY: Smithbox AtkParam_Pc row names')

    # 4. The HKS decompile's category list against POWERSTANCE_CATEGORIES.
    if os.path.exists(HKS_DECOMPILE):
        check('IsEnableDualWielding categories', _hks_dual_categories(HKS_DECOMPILE),
              set(POWERSTANCE_CATEGORIES), 'COMMUNITY: Smithbox c0000.hks + LUA/Enums.txt')
    else:
        skips.append('HKS decompile absent: ' + HKS_DECOMPILE)

    # 5. EXE constants of the guard formulas, read out of the 1.16.2 flat image.
    img = ATK.DEOBF_1162
    if os.path.exists(img):
        src = 'EXE eldenring-deobf.bin (1.16.2)'
        for va, want in ((0x142a1a64c, TWO_HAND_GUARD), (0x14329e66c, TWO_HAND_SHIELD_GUARD),
                         (0x14329e678, GUARD_BOOST_ADD), (0x14329e6d8, 100.0),
                         (0x1429e5c30, -5.0), (0x1429ce438, -10.0)):
            check(f'float at {va:#x}', round(ATK._read_f32(img, va), 6), want, src)
        # The guard-boost sum adds XMM6 (= 1.0) at 0x140684735: F3 0F 58 D6 = ADDSS XMM2, XMM6.
        check('ADDSS XMM2, XMM6 at 0x140684735', ATK._image_read(img, 0x140684735, 4).hex(),
              'f30f58d6', src)
    else:
        skips.append('EXE image absent: ' + img)

    # 6. Regulation facts the formulas lean on.
    src = 'VERIFIED regulation'
    # Ammunition (wepType 81/83/85/86) carries 0; arrows are not melee attackers or guards.
    check('overStrength 99 on every non-ammunition row',
          {w['overStrength'] for w in reg.weapon.values()
           if w['wepType'] not in (81, 83, 85, 86)}, {99}, src)
    check('Wakizashi is the only spAtkcategory 104', sorted(
        reg.name(w) for w in reg.weapon if w % 10000 == 0
        and reg.weapon[w].get('spAtkcategory') == SPECIAL_CATEGORY_WAKIZASHI), ['Wakizashi'], src)
    check('Greatsword + Greatsword powerstance', can_powerstance(reg, 4000000, 4000000), True,
          'COMMUNITY: colossal swords powerstance')
    check('Greatsword + Giant-Crusher powerstance', can_powerstance(reg, 4000000, 23110000),
          False, 'COMMUNITY: no cross-category powerstance')

    # 7. Direction checks with no outside number behind them: a colossal R1 into a medium shield
    #    costs more stamina than a straight-sword R1 into a greatshield, and only the lighter
    #    weapon's attackBaseRepel is at or below a greatshield's guardBaseRepel.
    ls = [dict(r, weapon=2000000, label='Longsword R1')
          for r in ATK.weapon_attacks(reg, 2000000, 'one', 25) if r['slot'] == 'r1_1']
    gc = [dict(r, weapon=23110000, label='Giant-Crusher R1')
          for r in ATK.weapon_attacks(reg, 23110000, 'one', 25) if r['slot'] == 'r1_1']
    m = block_matrix(reg, ls + gc, [(32130000, 25), (31130000, 25)], {'physical': 500.0})
    by = {(x['attack'], x['shield']): x for x in m}
    src = 'sanity only: expected direction, no outside number'
    check('Giant-Crusher R1 into Brass costs more than Longsword R1 into Fingerprint',
          by[('Giant-Crusher R1', 'Brass Shield')]['stamina_to_blocker']
          > by[('Longsword R1', 'Fingerprint Stone Shield')]['stamina_to_blocker'], True, src)
    check('Longsword R1 bounces off a greatshield (guardBaseRepel 70 vs attackBaseRepel 60)',
          by[('Longsword R1', 'Fingerprint Stone Shield')]['repelled'], True,
          src)
    check('Giant-Crusher R1 does not bounce off a greatshield',
          by[('Giant-Crusher R1', 'Fingerprint Stone Shield')]['repelled'], False, src)

    # 8. Corpus blockers.
    #    a. The guard table against the bytecode (its own decoder, read from the game file).
    hks_tool = os.path.join(HERE, 'er-hks-weapon-category-table.py')
    hks_path = os.environ.get('ER_COMMON_DEFINE_HKS', os.path.expanduser(
        '~/er-extract/LOOK_HERE_ALL_ASSETS_20260713/action/script/common_define.hks'))
    if os.path.exists(hks_tool) and os.path.exists(hks_path):
        table = _load('er_hks_weapon_category_table', 'er-hks-weapon-category-table.py').extract(hks_path)
        t_, f_ = table['TRUE'], table['FALSE']
        src = 'VERIFIED common_define.hks WeaponCategoryID'
        check('one-handed left guard categories', {r['cells'][0] for r in table['rows']
                                                   if r['cells'][1] == t_}, set(GUARD_LEFT_ONE_HAND), src)
        check('two-handed refused categories', {r['cells'][0] for r in table['rows']
                                                if r['cells'][2] == f_}, set(GUARD_REFUSED_TWO_HAND), src)
    else:
        skips.append('common_define.hks or its decoder absent: ' + hks_path)
    check('Unarmed is wepmotionCategory 42 (FIST, no one-handed guard)',
          reg.weapon[UNARMED]['wepmotionCategory'], 42, 'VERIFIED regulation')

    #    b. The filter matches er-builds-pvp.pvp_corpus build for build.
    mirror = os.path.join(CACHE, 'builds.jsonl')
    if os.path.exists(mirror):
        pvp = _load('er_builds_pvp_for_selftest', 'er-builds-pvp.py')
        from pathlib import Path
        theirs = pvp.pvp_corpus(Path(mirror), 140, 160)
        mine = [b['computed'] for b in blocker_corpus(mirror, 140, 160)]
        check('blocker_corpus == er-builds-pvp.pvp_corpus (RL 140-160)', mine == theirs, True,
              'er-builds-pvp.py')
    else:
        skips.append('planner corpus absent: ' + mirror)

    #    c. Synthetic blockers: slot_pressure reuses block_hit, so its stamina equals block_matrix's.
    def build(left, two=False, stamina=150.0, right='Longsword', stats=None):
        slots = [{'name': right, 'equipIndex': 0, 'infusion': 'Standard', 'upgrade': 25}]
        if left:
            slots.append({'name': left, 'equipIndex': 3, 'infusion': 'Standard', 'upgrade': 25})
        return {'stats': stats or {'str': 40, 'dex': 20, 'int': 9, 'fth': 9}, 'is2h': two,
                'weaponUpgrade': 25, 'inventory': {'slots': slots},
                'computed': {'maxStamina': stamina,
                             'defenses': {k: 100 for k in ABSORB_KEY.values()},
                             'absorption': {k: 20.0 for k in ABSORB_KEY.values()}}}

    bl = Blockers(reg, [build('Brass Shield'), build(None), build('Dagger'),
                        build(None, True, right='Greatsword')])
    src = 'block_hit / HKS guard hand'
    check('kinds: shield, bare hand, left dagger, two-handed greatsword', bl.kind,
          ['shield', 'bare left hand', 'left weapon', 'two-handed weapon'], src)
    gs2 = [r for r in ATK.weapon_attacks(reg, 4000000, 'both', 25) if r['slot'] == '2h_r1_1'][0]
    ar = {'physical': 500.0}
    p = bl.slot_pressure([{'hit': gs2, 'scaled': scaled_for(reg, gs2, ar), 'fr': None, 'n': 1}])
    want = block_matrix(reg, [dict(gs2, label='x')], [(31130000, 25)], ar)[0]['stamina_to_blocker']
    want2 = block_matrix(reg, [dict(gs2, label='x')], [(4000000, 25, GUARD_JUDGE_DEFAULT, True)],
                         ar)[0]['stamina_to_blocker']
    check('slot_pressure stamina = block_matrix (Brass +25, 2H Greatsword +25)', p['stamina'],
          (want + want2) / 2, src)
    check('two of four synthetic builds can guard', p['can_block'], 0.5, src)
    check('a 100% physical shield passes no chip of a physical hit',
          bl.guards[bl.index[0]]['cuts']['physical'] >= 100.0, True, 'VERIFIED regulation')
    none = bl.slot_pressure([{'hit': gs2, 'scaled': scaled_for(reg, gs2, ar), 'fr': None, 'n': 1}],
                            block_rate=0.0)
    check('block_rate 0 leaves the factor at 1', none['factor'], 1.0, 'definition')
    dg = [r for r in ATK.weapon_attacks(reg, 1000000, 'one', 25) if r['slot'] == 'r1_1'][0]
    pd = bl.slot_pressure([{'hit': dg, 'scaled': scaled_for(reg, dg, ar), 'fr': None, 'n': 1}])
    src = 'sanity only: expected direction, no outside number'
    check('a 2H Greatsword R1 drains more than a Dagger R1', p['drain'] > pd['drain'], True, src)
    check('guard pressure favours the Greatsword R1', p['factor'] > pd['factor'], True, src)
    check('a Dagger R1 bounces off the Brass Shield (40 <= 50)', pd['repel_share'], 0.5, src)
    gs_guard = shield_guard(reg, 4000000, 25, two_handed=True)
    dg_guard = shield_guard(reg, 1000000, 25, two_handed=True)
    check('a 2H Greatsword guard stops more than a 2H Dagger guard',
          bl.own_guard(gs_guard, [gs2, dg]) > bl.own_guard(dg_guard, [gs2, dg]), True, src)

    # 9. The guard dig's formulas against regulation rows (docs/er-mechanics/powerstance-guard.md
    #    section 3; the numbers are the ones that section's tables quote).
    files = PR.load(None)
    sp_rows, _, _ = PR.rows(PR.param_bytes(files, 'SpEffectParam'), ['staminaAttackRate'])
    sp = {r['id']: round(r['staminaAttackRate'], 4) for r in sp_rows}
    src = 'VERIFIED regulation SpEffectParam.staminaAttackRate'
    for sid, want in ((320700, 1.4), (1691, 3.0), (1693, 3.0), (1701, 4.0), (1703, 4.0),
                      (1821, 1.5), (1826, 1.2)):
        check(f'SpEffect {sid} staminaAttackRate', sp.get(sid), want, src)
    brass = 31130000
    shotel = reg.find_weapon('Shotel')

    def r1_of(w, grip='one'):
        want = ('2h_' if grip == 'both' else '') + 'r1_1'
        return [dict(r, weapon=w, label=f'{w} {grip}') for r in ATK.weapon_attacks(reg, w, grip, 25)
                if r['slot'] == want]

    def one(w, grip='one', rate=1.0):
        return block_matrix(reg, r1_of(w, grip), [(brass, 25)], {'physical': 500.0},
                            blocker_stamina=150, stamina_rate=sp.get(320700) if rate == 'hammer' else rate)[0]
    src = 'powerstance-guard.md section 3 (Brass Shield +25, 150 stamina)'
    check('Greatsword R1 into Brass: 45 stamina, 4 hits', (one(4000000)['stamina_to_blocker'],
                                                          one(4000000)['hits_to_break']), (45, 4), src)
    check('... with Hammer Talisman: 63 stamina, 3 hits',
          (one(4000000, rate='hammer')['stamina_to_blocker'], one(4000000, rate='hammer')['hits_to_break']),
          (63, 3), src)
    check('Giant-Crusher 2H R1 into Brass with Hammer: 78 -> 109',
          (one(23110000, 'both')['stamina_to_blocker'], one(23110000, 'both', 'hammer')['stamina_to_blocker']),
          (78, 109), src)
    d = one(1000000)
    check('a repelled Dagger R1 still charges 17 stamina and keeps half the chip',
          (d['repelled'], d['stamina_to_blocker']), (True, 17), src)
    src = 'VERIFIED regulation guardCutCancelRate / GameSystemCommonParam'
    check('weapon rows with guardCutCancelRate -50', sum(w['guardCutCancelRate'] == -50
                                                        for w in reg.weapon.values()), 88, src)
    check('AtkParam_Pc rows with a nonzero guardCutCancelRate',
          sum(a['guardCutCancelRate'] != 0 for a in reg.atk_guard.values()), 168, src)
    s = one(shotel)
    check('Shotel R1 into a 100% physical shield passes 50% physical (gcc 0.5)',
          (s['guard_cut_cancel'], s['chip_fraction_phys']), (0.5, 0.5), src)
    check('flickDamageCutRateSuccessGurad (repelled chip share)', reg.repel_chip, 0.5, src)
    check('a repelled hit keeps that share of its chip',
          block_hit(reg, dict(r1_of(1000000)[0]), {'physical': 500.0},
                    dict(shield_guard(reg, brass, 25), cuts=dict(shield_guard(reg, brass, 25)['cuts'],
                                                                   physical=50.0)))['chip_hp_rate'],
          0.5, src)
    src = 'definitions: guard reaction table and regen pause'
    check('Greatsword R1 (dmgLevel 2) on a medium shield is a small reaction',
          guard_reaction(3, 2), 'small', 'TABLE row 3 column 2')
    check('a colossal R1 (dmgLevel 3) on a greatshield is small, on a straight sword middle',
          (guard_reaction(4, 3), guard_reaction(1, 3)), ('small', 'middle'), 'TABLE')
    check('no regeneration inside the cycle when the reaction pause covers it',
          bl.slot_pressure([{'hit': gs2, 'scaled': scaled_for(reg, gs2, ar), 'fr': None, 'n': 1}],
                           cycle=5)['regen'], 0.0, src)
    check('regen over a long cycle lowers the drain',
          bl.slot_pressure([{'hit': gs2, 'scaled': scaled_for(reg, gs2, ar), 'fr': None, 'n': 1}],
                           cycle=120)['drain'] < p['drain'], True, src)
    fa = _load('er_mechanics_frame_advantage_for_selftest', 'er-mechanics-frame-advantage.py')
    check('DAMAGE_LEVEL_REMAP == frame-advantage REMAP', DAMAGE_LEVEL_REMAP, fa.REMAP,
          'er-mechanics-frame-advantage.py')
    for build, path in (('1.16.2', ATK.DEOBF_1162), ('1.17.1', ATK.DEOBF_1171)):
        if os.path.exists(path):
            raw = ATK._image_read(path, GUARD_REACTION_TABLE_VA[build], 5 * 12 * 4)
            vals = struct.unpack('<60i', raw)
            check(f'guard reaction table at {GUARD_REACTION_TABLE_VA[build]:#x} ({build})',
                  tuple(tuple(vals[r * 12:(r + 1) * 12]) for r in range(5)), GUARD_REACTION_TABLE,
                  f'EXE {os.path.basename(path)}')
        else:
            skips.append(f'guard reaction table: {path} absent')
    src = 'sanity only: expected direction, no outside number'
    check('a repel the blocker can punish raises its own guard\'s value',
          bl.own_guard(shield_guard(reg, brass, 25), [dg], startup=10.0)
          > bl.own_guard(shield_guard(reg, brass, 25), [dg]), True, src)
    pd_ham = bl.slot_pressure([{'hit': dg, 'scaled': scaled_for(reg, dg, ar), 'fr': None, 'n': 1,
                                'stamina_rate': 4.0}])
    check('Royal Knight\'s Resolve x4 drains more than a plain Dagger R1', pd_ham['drain'] > pd['drain'],
          True, src)

    for line in passes:
        print('PASS', line)
    for line in skips:
        print('SKIP', line)
    for line in fails:
        print('FAIL', line)
    print(f'{len(passes)} passed, {len(fails)} failed, {len(skips)} skipped')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
