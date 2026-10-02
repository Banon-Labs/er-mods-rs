#!/usr/bin/env python3
"""Elden Ring self-buffs that stack with a melee build: buff spells, physick tears, great runes and
consumables, read from the installed regulation, with the stacking rules the executable applies.

Every value is read from `regulation.bin` through `scripts/er-param-read.py`. The item -> SpEffect
links are walked through the params themselves (MagicParam / EquipParamGoods `refId*` by
`refCategory`, then Bullet `spEffectIDForShooter` / `spEffectId0..4` / child bullets, then the
SpEffect `cycleOccurrenceSpEffectId` / `replaceSpEffectId` / `accumuOver/UnderFireId` links). How
each SpEffect column reaches a hit, and which entries replace which, is documented with addresses
and evidence labels in `docs/er-mechanics/buffs.md`.

  python3 scripts/er-mechanics-buffs.py --selftest
  python3 scripts/er-mechanics-buffs.py --table                 # every buff source with an effect
  python3 scripts/er-mechanics-buffs.py --show "Golden Vow" "Flame, Grant Me Strength"
  python3 scripts/er-mechanics-buffs.py --context "Golden Vow" "Flame, Grant Me Strength" \\
      "Radahn's Great Rune" --pvp --phys-type strike
  python3 scripts/er-mechanics-buffs.py --corpus [--rl 140-160]

Interface for the PvP ranking (see buffs.md section 6):

  m = Buffs()
  ctx = m.attack_context(["Golden Vow", "Howl of Shabriri"], pvp=True, phys_type='slash')
  ctx['ar_rate']['physical']    # multiplies the weapon part of each element's AR
  ctx['flat_add']['fire']       # flat add after all AR multipliers, times AtkParam byPoint/100
  ctx['pvp_rate']['physical']   # attacker atkPlayerDmgCorrectRate product (PvP only)
  d = m.defense_context(["Opaline Hardtear"], pvp=True)
  d['cut']['physical'], d['correct']['physical'], d['poise_div']
"""
import argparse
import importlib.util
import json
import math
import os
import struct
import sys
import xml.etree.ElementTree as ET

_HERE = os.path.dirname(os.path.abspath(__file__))


def _mod(name, fname):
    s = importlib.util.spec_from_file_location(name, os.path.join(_HERE, fname))
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


PR = _mod('er_param_read', 'er-param-read.py')
IN = _mod('er_item_name', 'er-item-name.py')
SMITHBOX = os.path.dirname(PR.PARAMDEF_DIR)
CORPUS = os.path.expanduser('~/.cache/er-build-planner/builds.jsonl')

ELEMENTS = ('physical', 'magic', 'fire', 'lightning', 'holy')
PHYS_TYPES = ('slash', 'strike', 'pierce', 'standard')
# Field stems in SpEffectParam order: the game's "thunder" is lightning, "dark" is holy.
_EL = {'physical': 'physics', 'magic': 'magic', 'fire': 'fire', 'lightning': 'thunder', 'holy': 'dark'}
_PT = {'slash': 'slash', 'strike': 'blow', 'pierce': 'thrust', 'standard': 'neutral'}
_PVP_EL = {'physical': 'Physics', 'magic': 'Magic', 'fire': 'Fire', 'lightning': 'Thunder', 'holy': 'Dark'}

# `FUN_140d4fdf0` (1.16.2; 1.17.1 below the 0xafefe9 boundary, same address class as grease.md's
# getters) switch 0..8 reads +0x48, +0x220, +0x224, +0x228, +0x22c, +0x4c, +0x50, +0x54, +0x1e0:
# the nine `*AttackPowerRate` columns. These are the products `FUN_1406832a0` multiplies into the
# weapon part of the hit (buffs.md section 2).
AP_RATE = {e: f'{_EL[e]}AttackPowerRate' for e in ELEMENTS}
AP_RATE_PT = {p: f'{_PT[p]}AttackPowerRate' for p in PHYS_TYPES}
AP_FLAT = {e: f'{_EL[e]}AttackPower' for e in ELEMENTS}
AP_FLAT_PT = {p: f'{_PT[p]}AttackPower' for p in PHYS_TYPES}
# The `*AttackRate` columns: collected into AttackInfo +0x90..+0xb0 by the same accumulator; their
# consumer on a normal hit is not traced (buffs.md, not established).
ATK_RATE = {e: f'{_EL[e]}AttackRate' for e in ELEMENTS}
ATK_RATE_PT = {p: f'{_PT[p]}AttackRate' for p in PHYS_TYPES}
PVP_ATK = {e: f'atkPlayerDmgCorrectRate_{_PVP_EL[e]}' for e in ELEMENTS}
PVE_ATK = {e: f'atkEnemyDmgCorrectRate_{_PVP_EL[e]}' for e in ELEMENTS}
PVP_DEF = {e: f'defPlayerDmgCorrectRate_{_PVP_EL[e]}' for e in ELEMENTS}
PVE_DEF = {e: f'defEnemyDmgCorrectRate_{_PVP_EL[e]}' for e in ELEMENTS}
CUT = {'magic': 'magicDamageCutRate', 'fire': 'fireDamageCutRate',
       'lightning': 'thunderDamageCutRate', 'holy': 'darkDamageCutRate'}
CUT_PT = {p: f'{_PT[p]}DamageCutRate' for p in PHYS_TYPES}
STATS = {'vig': 'addLifeForceStatus', 'mnd': 'addWillpowerStatus', 'vit': 'addEndureStatus',
         'str': 'addStrengthStatus', 'dex': 'addDexterityStatus', 'int': 'addMagicStatus',
         'fth': 'addFaithStatus', 'arc': 'addLuckStatus'}
# Status build-up adds, summed as ints by the same accumulator (`FUN_1404ff690` and siblings).
STATUS = {'poison': 'poizonAttackPower', 'rot': 'diseaseAttackPower', 'blood': 'bloodAttackPower',
          'death': 'curseAttackPower', 'frost': 'freezeAttackPower', 'sleep': 'sleepAttackPower',
          'madness': 'madnessAttackPower'}
OTHER = ('maxHpRate', 'maxMpRate', 'maxStaminaRate', 'staminaRecoverChangeSpeed', 'changeHpRate',
         'changeHpPoint', 'toughnessDamageCutRate', 'saAttackPowerRate', 'staminaAttackRate',
         'guardStaminaCutRate', 'equipWeightChangeRate')
META = ('effectEndurance', 'spCategory', 'categoryPriority', 'vfxId', 'stateInfo', 'wepParamChange',
        'conditionHp', 'conditionHpRate', 'saveCategory', 'isUseAtkParamAtkPowerCorrect',
        'effectTargetSelf', 'effectTargetFriend', 'effectTargetEnemy', 'effectTargetPlayer',
        'effectTargetSelfTarget', 'effectTargetOpposeTarget', 'effectTargetFriendlyTarget',
        'bAdjustStrengthAblity', 'bAdjustAgilityAblity', 'bAdjustMagicAblity',
        'bAdjustFaithAblity', 'magicSubCategoryChange1', 'magicSubCategoryChange2',
        'magicSubCategoryChange3', 'throwAttackParamChange', 'bGameClearBonus', 'vowType0')
# SpEffect -> SpEffect links followed when collecting what an item applies. `atkOccurrence` fires
# on the target of a hit and `spiritDeath` on a spirit ash, so neither belongs to a self-buff.
SP_LINKS = ('cycleOccurrenceSpEffectId', 'replaceSpEffectId', 'accumuOverFireId',
            'accumuUnderFireId')
BULLET_SP = ('spEffectIDForShooter', 'spEffectId0', 'spEffectId1', 'spEffectId2', 'spEffectId3',
             'spEffectId4')
BULLET_LINKS = ('HitBulletID', 'intervalCreateBulletId')

# Great rune goods 191..196 carry no refId. `ChrIns::ApplyRuneArcEffects` 0x1404a6d20 applies
# 600+10g .. 608+10g for the equipped rune g (host), 609+10g for an invader whose rune matches
# ConstantParam 0xc3, and 790 for a host with no rune; Rune Arc (goods 190) applies 3450
# (`stateInfo 277`), which only sets `PlayerGameData.runeArcActive` (buffs.md section 4).
GREAT_RUNE_SPEFFECT = {
    "Godrick's Great Rune": 600, "Radahn's Great Rune": 610, "Morgott's Great Rune": 620,
    "Rykard's Great Rune": 630, "Mohg's Great Rune": 640, "Malenia's Great Rune": 650,
}
# goodsType (Smithbox GOODS_TYPE): 5 sorcery, 16 incantation, 17/18 self-buff sorcery/incantation,
# 10 physick tear, 0 normal item.
SPELL_TYPES = (5, 16, 17, 18)
PVP_TAGS = ('Invasions', 'Duels', 'Co-op/Gank', '2v2', 'Ladder')
STATE_SKIPPED = (197, 315, 316)          # accumulator `0x1404f486a` skips these
# `spCategory` sets read out of `FUN_1405005a0` (R2c) and `FUN_140500c40` (R3), buffs.md section 3.
CAT_SAME_PRIO = frozenset((100, 200, 201))
CAT_SAME = frozenset((110, 130, 131, 132, 133, 140, 180, *range(150, 165), 1003, 1004, 1005, 1006))
CAT_E_SIDE = frozenset(range(165, 175))
CAT_REPLACE_SAME = frozenset((110, 130, 131, 132, 133, 140, 180, *range(150, 175)))
CAT_PRIO_GATE = frozenset((1003, 1004, 1005, 1006))
RUNE_ARC_NO_RUNE = 'Rune Arc (no great rune)'
STATE_MASK_GATED = (123, 124, 125, 126, 186)

# -- expected buff factors for the PvP ranking (buffs.md section 10) --------------------------
# Weapon-buff slots (section 3): greases, the weapon-buff incantations and, measured here, the
# buff ashes too (War Cry 1811, Braggart's Roar 1861, Cragblade 1821, Royal Knight's Resolve 1701
# are all 162 on the right hand, 163 on the left). One live entry per hand.
WEAPON_BUFF_CATS = frozenset((162, 163))
# `stateInfo` 384 / 385 on Royal Knight's Resolve / Determination: the buff ends on the owner's next
# registered hit (`VERIFIED`, buffs.md section 8). Player attack rows carry SpEffect 1665/1667,
# which `CalculateDamage2` sends to the attacker; gated on 384/385 it overwrites the weapon-buff
# slot. A contact with an i-framed target registers no hit and spends nothing, so one use per
# landed hit stands (blocked hits would spend one too; the ranking has no block share).
NEXT_HIT_STATES = (384, 385)
# A row whose duration is this short only lives while something keeps re-applying it (Thorny
# Cracked Tear's 1.5 s accumulator tiers). What re-applies it is not traced, so it gets no uptime.
REFRESHED_ROW_S = 2.0
# Fight shape. The fight a buff has to cover is 3 to 5 minutes (user, 2026-10-01: a PvP fight
# lasts that long, and a 60 s weapon buff cast once before a 25 s fight was free and never
# recast). Only buff duration, uptime and recast logic read the fight length; engagement spacing,
# status decay between hits and sustain pacing read `ENGAGEMENT_SECONDS` (5 s, `INFERRED`, the
# status module's). Landed hits are a schedule, one count per fight point (`fight_hits`): the
# hits it takes to empty the defender's HP and every flask he drinks, at most what that fight
# length leaves room for. `FIGHT_ENGAGEMENTS` is the one-HP-bar, no-flask count
# (`er-mechanics-status.Defenders.fight_engagements`, 5 at RL 150), the default where no schedule
# is passed. The ranking passes its own values; these are the defaults.
FIGHT_ENGAGEMENTS = 5
ENGAGEMENT_SECONDS = 5.0
FIGHT_SECONDS_RANGE = (180.0, 300.0)
#: Step the fight range is sampled at, equal weights. A recast count is a step function of the
#: fight length (ceil(fight / duration) - 1), so every quantity that depends on it (uptime,
#: recasts, time factor) is the mean over the sample points rather than its value at one length:
#: 10 s steps, 13 points over 180..300 s, fine enough for the shortest timed buffs (25 s greases).
FIGHT_SAMPLE_STEP_S = 10.0


def fight_points(lo=FIGHT_SECONDS_RANGE[0], hi=FIGHT_SECONDS_RANGE[1], step=FIGHT_SAMPLE_STEP_S):
    """The fight lengths (s) a buff's uptime and recasts are averaged over, `lo`..`hi` inclusive."""
    if hi <= lo:
        return (float(lo),)
    n = max(1, int(round((hi - lo) / step)))
    return tuple(lo + (hi - lo) * k / n for k in range(n + 1))


#: The fight lengths, as `fight_seconds` everywhere it is taken (a single number is also taken).
FIGHT_SECONDS = fight_points()
#: TAE frames per second (`er-mechanics-status.FPS`), for cast frames -> seconds.
CAST_FPS = 30.0
#: Frames one cast of a spell takes from the fight. No spell cast animation is read here (the
#: catalyst TimeAct that holds them is not mapped to `Magic` rows), so this is a stand-in
#: (`INFERRED`: about two seconds from press to being able to act, a typical self-buff incantation).
SPELL_CAST_FRAMES = 60.0
#: Frames one use of a consumable takes from the fight: `er-mechanics-status.cure_frame`, the bolus
#: clip's `ConsumeCurrentGoods` frame (31, `TAE`), standing in for every item clip (`INFERRED`, as
#: `er-builds-pvp.SetupBuffs` already does for greases). The ranking sets it from the TAE.
ITEM_CAST_FRAMES = 31.0
#: Median max FP of the RL 140-160 PvP corpus (`er-mechanics-ashes.FP_BAR_DEFAULT`); a spell is
#: recast at most as often as one bar pays for. The ranking sets its window's value.
FP_BAR_DEFAULT = 88.0
#: Uses of a crystal tear: the Flask of Wondrous Physick holds one charge (`COMMUNITY`), refilled
#: only at a Site of Grace, so a tear is never recast inside a fight.
TEAR_USES = 1

# -- landed hits per fight point (buffs.md section 10) -----------------------------------------
#: HP one Flask of Crimson Tears +12 heals: EquipParamGoods 1025 -> SpEffect 501012
#: `changeHpEstusFlaskPoint` -810 (`VERIFIED` regulation, `er-mechanics-disengage.flask`).
FLASK_HEAL_HP = 810.0
#: FP one Flask of Cerulean Tears +12 restores: EquipParamGoods 1075 -> SpEffect 501062
#: `changeMpEstusFlaskPoint` -220 (`VERIFIED` regulation).
CERULEAN_FP = 220.0
#: Frames a drink takes from the fight: the first frame anything (roll, R1, item, move) cancels the
#: crimson drink, f54 of the 55-frame `a000_050000` (`TAE`, `er-mechanics-disengage.flask`). That
#: `goodsUseAnim` 10 plays that clip is `INFERRED` (disengage.md section 3). The cerulean flask is
#: `goodsUseAnim` 19, which `c0000.hks` `ExecItem` sends to its own event, `Event_ItemDrinkingMP`
#: (`ITEM_DRINK_MP` 19, `common_define.hks`; crimson is `ITEM_DRINK` 10 -> `Event_ItemDrinking`);
#: the clip behind that event is not traced, so the crimson frames stand in (`INFERRED`).
FLASK_DRINK_FRAMES = 54.0
#: Share of a crimson drink that heals net (`INFERRED` 1: every drink lands). The disengage race
#: (`er-mechanics-disengage.heal_outcome`) gives the traded and denied shares per escape; folding
#: them in needs the escape mix of a whole fight, which is not modelled.
FLASK_ETA = 1.0
#: Share of the engagements the attacker lands a hit in (`INFERRED`: a symmetric fight, 0.5).
FIGHT_WIN_SHARE = 0.5
#: Crimson / cerulean flask split the corpus carries (`build.items.flasks`, `MEASURED` 2026-10-01:
#: 10 / 4 at +12 on 1134 / 1138 of the 1141 RL 140-160 PvP builds, the planner default).
CORPUS_CRIMSON, CORPUS_CERULEAN = 10, 4
#: Fight kinds by planner tag (duel etiquette, `COMMUNITY`: crimson flasks are not drunk in a
#: duel, cerulean and physick are; invasions and ganks drink everything). Other PvP tags (2v2,
#: Ladder, Fishing) and untagged builds say nothing about it and are left out of the split.
DUEL_TAGS = ('Duels',)
FLASK_TAGS = ('Invasions', 'Co-op/Gank')
#: The fight points of a mix of fight kinds are each kind's points repeated in proportion to its
#: share, the shares rounded to this many parts (4: quarters, 52 points for two kinds).
FIGHT_MIX_PARTS = 4


def fight_hits(fight_seconds=FIGHT_SECONDS, hp=1945.84, damage=471.5, flasks=0, heal=FLASK_HEAL_HP,
               eta=FLASK_ETA, engagement_s=ENGAGEMENT_SECONDS, win_share=FIGHT_WIN_SHARE,
               drink_frames=FLASK_DRINK_FRAMES):
    """Landed hits at each fight length, as a tuple aligned with `fight_seconds`.

    Two bounds, both read per point; the count is the smaller (at least 1):

        to kill  ceil((hp + flasks x heal x eta) / damage)      the defender's HP and his flasks
        by time  floor((f - flasks x drink) / engagement_s x win_share)

    `hp` is the corpus median max HP and `damage` the reference landed hit
    (`er-mechanics-status.FIGHT_REF_DAMAGE`); at RL 150 with no flask that is
    ceil(1945.84 / 471.5) = 5, the old fixed count. The time bound is the same fight seen from
    the clock: an engagement every `engagement_s`, the attacker winning `win_share` of them, and
    the defender's drinks taking their frames out of the fight. With every flask (10) the kill
    needs 22 hits and the time bound holds it to 16..22 over 180..300 s (`INFERRED` reading: a
    fight shorter than the kill ends on time). With none, 5 at every length."""
    fs = _fights(fight_seconds)
    kill = max(1, math.ceil((hp + flasks * heal * eta) / damage - 1e-9))
    drink_s = flasks * drink_frames / CAST_FPS
    return tuple(max(1, min(kill, int(max(0.0, f - drink_s) / engagement_s * win_share + 1e-9)))
                 for f in fs)


def fight_mix(kinds, parts=FIGHT_MIX_PARTS):
    """(fight points, hits) of a mix of fight kinds [(share, points, hits)]: each kind's points
    and hit counts repeated in proportion to its share (rounded to `parts`, then reduced), so the
    plain mean over the points every reader takes is the share-weighted mean. A kind whose share
    rounds to 0 is left out; with every share at 0, the first kind alone."""
    reps = [round(s * parts) for s, _, _ in kinds]
    if not any(reps):
        reps = [1] + [0] * (len(kinds) - 1)
    g = 0
    for r in reps:
        g = math.gcd(g, r)
    fs, hs = [], []
    for r, (_, pts, hits) in zip(reps, kinds):
        for _ in range(r // g):
            fs += list(pts)
            hs += list(hits)
    return tuple(fs), tuple(hs)


def tag_shares(rows):
    """{'duel': share, 'flasks': share} of the fight-kind tag mentions (`DUEL_TAGS`,
    `FLASK_TAGS`) over corpus rows carrying `tags`; a row tagged both counts once in each."""
    duel = sum(1 for r in rows for t in r.get('tags') or () if t in DUEL_TAGS)
    fl = sum(1 for r in rows for t in r.get('tags') or () if t in FLASK_TAGS)
    n = duel + fl
    return {'duel': duel / n if n else 0.0, 'flasks': fl / n if n else 0.0, 'mentions': (duel, fl)}


def _pairs(fight_seconds, hits):
    """[(fight s, landed hits)]: `hits` a number is the same count at every length, a sequence
    is one count per point of `fight_seconds` (a `fight_hits` / `fight_mix` schedule). A sequence
    of another length (a single length asked of a schedule) takes the schedule's mean, rounded."""
    fs = _fights(fight_seconds)
    if isinstance(hits, (tuple, list)):
        if len(hits) == len(fs):
            return list(zip(fs, (int(h) for h in hits)))
        return [(f, round(hits_mean(hits))) for f in fs]
    return [(f, int(hits)) for f in fs]


def hits_mean(hits):
    """Mean landed hits of a count or a schedule, for a reader that takes one number."""
    if isinstance(hits, (tuple, list)):
        return sum(hits) / len(hits) if hits else float(FIGHT_ENGAGEMENTS)
    return float(hits)


def _fights(fight_seconds):
    """`fight_seconds` as a tuple of lengths: a number is one length, a sequence is sample points."""
    if isinstance(fight_seconds, (tuple, list)):
        return tuple(float(f) for f in fight_seconds)
    return (float(fight_seconds),)


def recast_points(duration, fight_seconds=FIGHT_SECONDS, uses=None, hits=FIGHT_ENGAGEMENTS,
                  one_hit=False):
    """[(fight s, recasts)] of one buff at each fight length: as many recasts as keep it on the
    whole fight (ceil(fight / duration) - 1; a next-hit buff once per landed hit after the first,
    that point's `hits` - 1), at most `uses` - 1 (the first cast is made before contact and free,
    buffs.md section 10, `INFERRED`); `uses` None is no limit. A permanent (-1) or refreshed/0 s
    buff is never recast. `hits` is a count or a schedule (`_pairs`)."""
    out = []
    for f, h in _pairs(fight_seconds, hits):
        if one_hit:
            need = max(0, h - 1)
        elif duration < REFRESHED_ROW_S:
            need = 0
        else:
            need = max(0, math.ceil(f / duration - 1e-9) - 1)
        out.append((f, need if uses is None else min(need, max(0, int(uses) - 1))))
    return out


def drinks_needed(casts, refill):
    """Cerulean drinks `casts` casts need: `refill` = (casts payable after d drinks for
    d = 0, 1, ..., drink frames); the fewest d whose count reaches `casts`, at most the last."""
    paid = refill[0]
    for d, n in enumerate(paid):
        if n is None or n >= casts:
            return d
    return len(paid) - 1


def recast_plan(duration, fight_seconds=FIGHT_SECONDS, uses=None, cast_frames=0.0,
                hits=FIGHT_ENGAGEMENTS, one_hit=False, refill=None):
    """Mean over the fight lengths of one buff's uptime, recasts and time factor.

    Uptime: (1 + recasts) x duration / fight, at most 1, or (1 + recasts) / that point's landed
    hits for a next-hit buff; 1 for a permanent one, 0 for a refreshed one. Time factor: each
    recast takes its `cast_frames` out of the fight, 1 - recasts x cast / fight (the cast's
    punish exposure is not modelled, only its time). `refill` (`drinks_needed`) charges the
    cerulean drinks the casts need beyond one FP bar, each its drink frames, the same way."""
    pts = recast_points(duration, fight_seconds, uses, hits, one_hit)
    ups, ns, tfs, ds = [], [], [], []
    for (f, n), (_, h) in zip(pts, _pairs(fight_seconds, hits)):
        if one_hit:
            u = min(1.0, (1.0 + n) / max(h, 1))
        elif duration < 0:
            u = 1.0
        elif duration < REFRESHED_ROW_S:
            u = 0.0
        else:
            u = min(1.0, (1.0 + n) * duration / f)
        d = drinks_needed(1 + n, refill) if refill else 0
        ups.append(u)
        ns.append(n)
        ds.append(d)
        frames = n * (cast_frames or 0.0) + (d * refill[1] if refill else 0.0)
        tfs.append(max(0.0, 1.0 - frames / CAST_FPS / f))
    k = len(pts)
    return {'uptime': sum(ups) / k, 'recasts': sum(ns) / k, 'time_factor': sum(tfs) / k,
            'drinks': sum(ds) / k, 'points': [(f, n) for f, n in pts]}
# Planner tags -> the role a build plays, for the great rune (`ApplyRuneArcEffects`, section 4).
# Invasions -> invader is the tag's meaning; the rest -> host is `INFERRED` (a duel or gank build
# can also be a summoned cooperator, for whom the rune is inert).
ROLE_BY_TAG = {'Invasions': 'invader', 'Duels': 'host', 'Co-op/Gank': 'host', '2v2': 'host',
               'Ladder': 'host', 'Fishing': 'host'}
# Thorny Cracked Tear's tiers are refreshed rows (see REFRESHED_ROW_S); `#0` resolves none.
KIT_SELECTORS = {'Thorny Cracked Tear': 'Thorny Cracked Tear#0'}
#: Spells a build casts because of a catalyst it holds, whether or not the planner lists them
#: (user, PvP practice): a Frenzied Flame Seal is carried to cast Bestial Vitality, a faith
#: talisman swapped in for the cast and both switched off after. The corpus agrees where it
#: records anything: of 264 RL 140-160 PvP builds holding the seal, 257 list no spell and the
#: other 7 list Bestial Vitality.
IMPLIED_SPELLS = {'Frenzied Flame Seal': ('Bestial Vitality',)}


def _implied_spells(b):
    held = _names((b.get('inventory') or {}).get('slots'))
    return [s for name in held for s in IMPLIED_SPELLS.get(name, ())]


#: Equipment whose resident SpEffects can heal over time (section 11): param, source kind, columns.
#: In 1.17.1 that is Blessed Dew Talisman (2 HP/s), Icon Shield (3 HP/s) and each Royal Remains
#: piece (2 HP/s, category 0 rows, so the pieces stack).
REGEN_EQUIP = (
    ('EquipParamAccessory', 'talisman', ('refId', 'residentSpEffectId1', 'residentSpEffectId2',
                                         'residentSpEffectId3', 'residentSpEffectId4')),
    ('EquipParamWeapon', 'weapon', ('residentSpEffectId', 'residentSpEffectId1', 'residentSpEffectId2')),
    ('EquipParamProtector', 'armor', ('residentSpEffectId', 'residentSpEffectId2', 'residentSpEffectId3')),
)
#: Hand slots whose weapon is taken as held for its resident effect: `equipIndex` 0 is the first
#: right-hand slot, 3 the first left-hand one (corpus layout). That a resident effect needs the
#: weapon held, not only equipped, is `INFERRED`.
HELD_EQUIP_INDEX = (0, 3)


def _goods_refs(m, gid, g):
    """(kind, SpEffect roots, bullet roots) of a spell or a usable good, as `Buffs._sources`
    reads them; None for anything else."""
    t = g['goodsType']
    if t in SPELL_TYPES and gid in m.magic:
        mg = m.magic[gid]
        return ('spell', [mg[f'refId{i}'] for i in range(1, 11) if mg[f'refCategory{i}'] == 2],
                [mg[f'refId{i}'] for i in range(1, 11) if mg[f'refCategory{i}'] == 1])
    if t in (0, 10):
        refs = [g['refId_default'], g['refId_1']]
        return ('tear' if t == 10 else 'consumable', refs if g['refCategory'] == 2 else [],
                refs if g['refCategory'] == 1 else [])
    return None
ELEMENT_KEYS = ('physical', 'magic', 'fire', 'lightning', 'holy')
DAMAGE_KEYS = PHYS_TYPES + ('magic', 'fire', 'lightning', 'holy')


def f32(x):
    return struct.unpack('<f', struct.pack('<f', x))[0]


def _defaults(stem):
    root = ET.parse(os.path.join(SMITHBOX, 'Param Meta', stem + '.xml')).getroot().find('Field')
    return {c.tag: float(c.get('DefaultValue')) for c in root if c.get('DefaultValue') is not None}


def _enum(name):
    p = os.path.join(SMITHBOX, 'Param Enums', name + '.json')
    d = json.load(open(p, encoding='utf-8-sig'))
    return {int(o['Key']): next((n['Text'] for n in o.get('Names', [])
                                 if n.get('Language') == 'English'), None)
            for o in d.get('Options', [])}


def _effect_fields():
    out = []
    for d in (AP_RATE, AP_RATE_PT, AP_FLAT, AP_FLAT_PT, ATK_RATE, ATK_RATE_PT, PVP_ATK, PVE_ATK,
              PVP_DEF, PVE_DEF, CUT, CUT_PT, STATS, STATUS):
        out += list(d.values())
    return out + list(OTHER)


EFFECT_FIELDS = _effect_fields()
# Status columns are also how cures are written (negative build-up), so they do not make a row a
# buff by themselves; they are still summed for rows that are.
RELEVANT_FIELDS = [f for f in EFFECT_FIELDS
                   if f not in ('changeHpRate', 'changeHpPoint') and f not in STATUS.values()]
# Rows applied only after an in-combat event, named by the regulation: Sacred Bloody Flesh's
# "Damage Boost on Blood Loss" (20501212, cycled from 20501211).
ON_TRIGGER = frozenset((20501212,))


class Buffs:
    """Regulation-backed buff model: item names -> SpEffect rows -> attack/defense factors."""

    def __init__(self, regulation=None):
        files = PR.load(regulation)
        self._files = files
        self._regen_src = None

        def rows(stem, fields=None):
            return {r['id']: r for r in PR.rows(PR.param_bytes(files, stem), fields)[0]}

        self.sp = rows('SpEffectParam', set(EFFECT_FIELDS) | set(META) | set(SP_LINKS)
                       | {'deleteCriteriaDamage', 'motionInterval'})
        # `EquipParamWeapon.isEnhance`, the gate `CanUseGoods` reads before a grease or weapon-buff
        # item can be used (grease.md 3c, `VERIFIED`). 1 only on Standard/Heavy/Keen/Quality rows
        # and 10 unique weapons in 1.17.1.
        self.enhance = {i: r['isEnhance'] for i, r in rows('EquipParamWeapon', {'isEnhance'}).items()}
        self._kit_cache = {}
        self.bullet = rows('Bullet', set(BULLET_SP) | set(BULLET_LINKS))
        self.magic = rows('Magic', {f'refId{i}' for i in range(1, 11)} |
                          {f'refCategory{i}' for i in range(1, 11)} | {'mp'})
        self.goods = rows('EquipParamGoods', {'goodsType', 'refCategory', 'refId_default',
                                              'refId_1', 'maxNum'})
        # What limits a recast (`source_recast`): the ranking sets its window's FP bar and the
        # item clip's frame from the TAE.
        self.fp_bar = FP_BAR_DEFAULT
        self.item_cast_frames = ITEM_CAST_FRAMES
        self.vfx = rows('SpEffectVfxParam', {'playCategory', 'playPriority', 'effectType'})
        self.sp_default = _defaults('SpEffect')
        self.sp_names = PR.row_names('SpEffectParam')
        self.state_names = _enum('SP_EFFECT_TYPE')
        gname = {}
        for _, t in IN.load(IN.DEFAULT_CORPUS, 'GoodsName'):
            for k, v in t.items():
                if v and k not in gname and not v.startswith('['):
                    gname[k] = v
        self.goods_name = gname
        self.sources = self._sources()
        self.last_refused = []
        self.by_name = {}
        # Goods ids ascend spell -> consumable, so a bare name that two rows share (the Golden Vow
        # incantation 6600 and the Golden Vow item 2003170) means the lower id; `kind:name` picks.
        for s in self.sources:
            self.by_name.setdefault(s['name'], s)
            self.by_name.setdefault(f"{s['kind']}:{s['name']}", s)

    # -- item -> SpEffect ---------------------------------------------------------------------
    def _closure(self, roots_sp=(), roots_bullet=()):
        """Every SpEffect an item can put on the user, with the path it came through."""
        seen_sp, seen_b, out = set(), set(), []
        todo = [('sp', i, 'item') for i in roots_sp] + [('b', i, 'item') for i in roots_bullet]
        while todo:
            kind, i, via = todo.pop(0)
            if i is None or i <= 0:
                continue
            if kind == 'b':
                if i in seen_b or i not in self.bullet:
                    continue
                seen_b.add(i)
                b = self.bullet[i]
                todo += [('sp', b[f], f'bullet {i}.{f}') for f in BULLET_SP]
                todo += [('b', b[f], f'bullet {i}.{f}') for f in BULLET_LINKS]
            else:
                if i in seen_sp or i not in self.sp:
                    continue
                # A bullet's hit effects (`spEffectId0..4`) land on whatever the bullet hits. The
                # rows that can land on the caster carry `effectTargetSelfTarget`; frostbite, Black
                # Blade's max-HP cut and Rennala's defense cut carry `effectTargetOpposeTarget`
                # instead (`INFERRED` from that split, not from the code that reads the flags).
                if via.startswith('bullet') and not via.endswith('spEffectIDForShooter') \
                        and not self.sp[i]['effectTargetSelfTarget']:
                    continue
                seen_sp.add(i)
                out.append((i, via))
                todo += [('sp', self.sp[i][f], f'sp {i}.{f}') for f in SP_LINKS]
        return out

    def _sources(self):
        src = []
        for gid, g in sorted(self.goods.items()):
            name = self.goods_name.get(gid)
            if not name:
                continue
            t = g['goodsType']
            if t in SPELL_TYPES and gid in self.magic:
                m = self.magic[gid]
                sp = [m[f'refId{i}'] for i in range(1, 11) if m[f'refCategory{i}'] == 2]
                bl = [m[f'refId{i}'] for i in range(1, 11) if m[f'refCategory{i}'] == 1]
                kind = 'spell'
            elif t in (0, 10):
                refs = [g['refId_default'], g['refId_1']]
                sp = refs if g['refCategory'] == 2 else []
                bl = refs if g['refCategory'] == 1 else []
                kind = 'tear' if t == 10 else 'consumable'
            elif t == 15 and name in GREAT_RUNE_SPEFFECT:
                # `ApplyRuneArcEffects`: the host applies 600+10g .. 608+10g. Every one of them
                # is kept, relevant or not, because Rykard/Mohg/Malenia work through `stateInfo`.
                base = GREAT_RUNE_SPEFFECT[name]
                eff = [(i, 'ApplyRuneArcEffects') for i in range(base, base + 9)
                       if i in self.sp and (self.relevant(i) or self.sp[i]['stateInfo'])]
                src.append({'name': name, 'kind': 'great rune', 'goods': gid, 'entries': eff,
                            'invader': [(base + 9, 'ApplyRuneArcEffects invader')]
                            if base + 9 in self.sp and self.sp[base + 9]['spCategory'] else []})
                continue
            else:
                continue
            entries = self._closure(sp, bl)
            eff = [(i, via) for i, via in entries if self.relevant(i)]
            if eff:
                src.append({'name': name, 'kind': kind, 'goods': gid, 'entries': eff})
        # Rune Arc with no great rune equipped: `ApplyRuneArcEffects` takes the g < 0 branch.
        src.append({'name': RUNE_ARC_NO_RUNE, 'kind': 'great rune', 'goods': 190,
                    'entries': [(790, 'ApplyRuneArcEffects g<0')], 'invader': []})
        return src

    def nondefault(self, sid, fields=None):
        """Effect columns of SpEffect `sid` that differ from the paramdef default."""
        r = self.sp[sid]
        return {f: r[f] for f in (fields or EFFECT_FIELDS)
                if abs(float(r[f]) - self.sp_default.get(f, 0.0)) > 1e-6}

    def relevant(self, sid):
        """Columns that change a melee hit, the defender, stats or resources (HP drain/regen
        columns `changeHp*` are excluded: they are how poison and rot are written too)."""
        return self.nondefault(sid, RELEVANT_FIELDS)

    def meta(self, sid):
        r = self.sp[sid]
        m = {f: r[f] for f in META}
        m['stateInfo_name'] = self.state_names.get(r['stateInfo'])
        v = self.vfx.get(r['vfxId'])
        if v:
            m['vfx'] = {k: v[k] for k in ('playCategory', 'playPriority', 'effectType')}
        return m

    def resolve(self, names_or_ids, role='host'):
        """Names (goods / spell / great rune, optionally `kind:name`) or SpEffect ids ->
        [(speffect id, source name)].

        Accumulator tiers (rows reached through `accumuOverFireId`, e.g. Thorny Cracked Tear
        3558..3561) are alternatives, one per threshold: `name` takes the last tier, `name#k` tier
        k (1-based), `name#0` none. Rows in `ON_TRIGGER` need an in-combat event and are left out
        unless the name ends in `#triggered`.
        """
        out, unknown = [], []
        for x in names_or_ids:
            if isinstance(x, int):
                out.append((x, f'speffect {x}'))
                continue
            base, _, sel = x.partition('#')
            s = self.by_name.get(base)
            if s is None:
                unknown.append(x)
                continue
            entries = s['entries']
            if s['kind'] == 'great rune' and role != 'host':
                # Invaders get only the 609+10g row, and only for the rune that ConstantParam
                # 0xc3 names; cooperators get nothing (`ApplyRuneArcEffects`, buffs.md 4).
                entries = s['invader'] if role == 'invader' else []
            tiers = [i for i, via in entries if 'accumuOverFireId' in via]
            for i, via in entries:
                if i in tiers:
                    k = tiers.index(i) + 1
                    want = len(tiers) if sel in ('', 'triggered') else int(sel)
                    if k != want:
                        continue
                elif i in ON_TRIGGER and sel != 'triggered':
                    continue
                out.append((i, x))
        return out, unknown

    # -- stacking -----------------------------------------------------------------------------
    def _clash(self, n, e):
        """R2, `FUN_1404fc0c0` over one live entry `e` for the new row `n`."""
        if e['effectEndurance'] <= 0.0:          # `FUN_140500be0` skip (meaning `INFERRED`)
            return False
        if e['id'] == n['id'] and e['spCategory'] != 10:
            return True
        si = n['stateInfo']
        if si in (2, 5, 6) and e['stateInfo'] == si:
            return True
        if si == 107 and e['stateInfo'] == 109:
            return True
        c = n['spCategory']
        if c in CAT_SAME_PRIO:
            return e['spCategory'] == c and e['categoryPriority'] == n['categoryPriority']
        if c in CAT_SAME:
            return e['spCategory'] == c
        return e['spCategory'] in CAT_E_SIDE and e['spCategory'] == c

    def _target(self, n, e):
        """R3, `FUN_140500c40`: may `n` take over the slot of live entry `e`."""
        c = n['spCategory']
        if c == 20:
            return e['id'] == n['id']
        if e['spCategory'] != c:
            return False
        if c in CAT_SAME_PRIO:
            return e['categoryPriority'] == n['categoryPriority']
        if c in CAT_REPLACE_SAME:
            return True
        if c in CAT_PRIO_GATE:
            return e['categoryPriority'] >= n['categoryPriority']
        return False

    def stack(self, entries):
        """Apply entries in order through the add path; returns the survivors and the refusals.

        R1 (`FUN_1404fc690`): `spCategory >= 10000` is refused while one of that category lives.
        R2 (`FUN_1404fc0c0`): no clash -> a new entry, i.e. the buff stacks.
        R3 (`FUN_140500c40`): clash -> the first live entry R3 accepts is re-pointed at the new id
        with a full new duration; none -> the new buff is refused. See buffs.md section 3.
        """
        live, refused = [], []
        for sid, src in entries:
            n = self.sp.get(sid)
            if n is None:
                continue
            c = n['spCategory']
            if c >= 10000 and any(self.sp[s]['spCategory'] == c for s, _ in live):
                refused.append((sid, src, 'R1 apply-first'))
                continue
            if not any(self._clash(n, self.sp[s]) for s, _ in live):
                live.append((sid, src))
                continue
            k = next((k for k, (s, _) in enumerate(live) if self._target(n, self.sp[s])), None)
            if k is None:
                refused.append((sid, src, 'R3 no replace target'))
            else:
                refused.append((live[k][0], live[k][1], f'R3 replaced by {sid} ({src})'))
                live[k] = (sid, src)
        self.last_refused = refused
        return live

    # -- the two public contexts --------------------------------------------------------------
    def _active(self, active, pvp, hp_ratio, apply_stack, role='host'):
        entries, unknown = self.resolve(active, role)
        self.last_refused = []
        if apply_stack:
            entries = self.stack(entries)
        rows = []
        for sid, src in entries:
            r = self.sp[sid]
            # HP conditions: the code that sets the entry-inactive flag bits was not traced, so
            # "conditionHp = at or below this HP%" and "conditionHpRate = at or above" are
            # read from the paramdef names and the Ritual Shield row (100 = full HP).
            if r['conditionHp'] > 0 and hp_ratio * 100.0 > r['conditionHp']:
                continue
            if r['conditionHpRate'] > 0 and hp_ratio * 100.0 < r['conditionHpRate']:
                continue
            rows.append((r, src))
        return rows, unknown

    def attack_context(self, active, pvp=False, phys_type='standard', hand='right',
                       two_handed=False, left_two_handed=False, by_point=100.0, by_rate=100.0,
                       by_dmg=100.0, hp_ratio=1.0, sub_categories=(), role='host',
                       apply_stack=True):
        """Attacker-side factors from the named buffs for one hit (buffs.md section 2).

        Pre-defense, on the weapon part of the AR:
          `ar_rate[e]`  product of `*AttackPowerRate` (physical: physics x the `phys_type`
                        sub-type column), each entry times `by_rate/100` when it has
                        `isUseAtkParamAtkPowerCorrect`.
          `flat_add[e]` sum of `*AttackPower`, added after every AR multiplier, each entry times
                        `by_point/100` under the same flag.
        Post-defense, on the damage:
          `atk_rate[e]` product of `*AttackRate` (entries with 0 skipped; `by_dmg/100` under the
                        flag), already cut to a whole percent as `FUN_140d24a30` stores it.
          `pvp_rate[e]` attacker `atkPlayerDmgCorrectRate` product when `pvp`, else
                        `atkEnemyDmgCorrectRate`.
        `status` sums build-up adds. `stats` are attribute adds, which change AR through scaling:
        feed them to `er-mechanics-ar.py`.

        Every attacker column passes the same gate, `IsApplicableForCategory`: the hand rule on
        `wepParamChange`, then the sub-category mask. `sub_categories` is the attack's
        `ATK_SUB_CATEGORY` set (100 charged heavy, 111 charged skill, ...); an entry with nonzero
        `magicSubCategoryChange1..3` needs one of them in it, one with all three 0 always passes.
        The accumulator (AR rate, flat add, `*AttackRate`, status) also drops `stateInfo`
        197/315/316 and the roll-triggered 123..126/186 rows; the PvP rates do not.
        `role` is 'host', 'invader' or 'cooperator' (great runes, buffs.md section 4).
        """
        cat = {'right': 1, 'left': 2}[hand] if not two_handed else 12
        rows, unknown = self._active(active, pvp, hp_ratio, apply_stack, role)
        ar = dict.fromkeys(ELEMENTS, 1.0)
        atk = dict.fromkeys(ELEMENTS, 1.0)
        flat = dict.fromkeys(ELEMENTS, 0.0)
        pvpr = dict.fromkeys(ELEMENTS, 1.0)
        stats = dict.fromkeys(STATS, 0)
        status = dict.fromkeys(STATUS, 0)
        ar_sub = atk_sub = 1.0
        used = []
        subs = set(sub_categories)
        for r, src in rows:
            w = r['wepParamChange']
            if cat == 1 and w in (2, 3, 4):
                continue
            if cat == 2 and w in (1, 3, 4):
                continue
            if cat == 12 and not (w in (0, 1, 5, 6) or (w == 2 and left_two_handed)):
                continue
            need = {r[f'magicSubCategoryChange{i}'] for i in (1, 2, 3)} - {0}
            if need and not need & subs:
                continue
            used.append((r['id'], src))
            for e in ELEMENTS:
                col = (PVP_ATK if pvp else PVE_ATK)[e]
                pvpr[e] = f32(pvpr[e] * f32(r[col]))
            for k, f in STATS.items():
                stats[k] += int(r[f])
            si = r['stateInfo']
            if si in STATE_SKIPPED or si in STATE_MASK_GATED:
                continue
            for k, f in STATUS.items():
                status[k] += int(r[f])
            use = r['isUseAtkParamAtkPowerCorrect']
            cp = f32(by_point * 0.01) if use else 1.0
            cr = f32(by_rate * 0.01) if use else 1.0
            cd = f32(by_dmg * 0.01) if use else 1.0
            for e in ELEMENTS:
                rate = f32(r[AP_RATE[e]])
                ar[e] = f32(ar[e] * f32(rate * cr))
                ra = f32(r[ATK_RATE[e]])
                if ra != 0.0:
                    atk[e] = f32(atk[e] * f32(ra * cd))
                add = float(r[AP_FLAT[e]])
                if add > 0:
                    flat[e] += add * cp
            # Physical sub-type columns: their own products and adds, picked by the hit's type.
            sub = f32(r[AP_RATE_PT[phys_type]])
            ar_sub = f32(ar_sub * f32(sub * cr))
            ra = f32(r[ATK_RATE_PT[phys_type]])
            if ra != 0.0:
                atk_sub = f32(atk_sub * f32(ra * cd))
            add = float(r[AP_FLAT_PT[phys_type]])
            if add > 0:
                flat['physical'] += add * cp
        ar['physical'] = f32(ar['physical'] * ar_sub)
        atk['physical'] = f32(atk['physical'] * atk_sub)
        atk = {e: int(f32(v * 100.0)) / 100.0 for e, v in atk.items()}
        return {'ar_rate': ar, 'flat_add': flat, 'pvp_rate': pvpr, 'atk_rate': atk,
                'status': status, 'stats': stats, 'entries': used,
                'refused': list(self.last_refused), 'unknown': unknown}

    def defense_context(self, active, pvp=False, phys_type='standard', hp_ratio=1.0,
                        guarding=False, role='host', apply_stack=True):
        """Defender-side factors (defense.md section 2, buffs.md section 2).

        `cut[e]`     `*DamageCutRate` product (`CalculateDefenseModifiers`), the physical column
                     picked by `phys_type`; `stateInfo 335` rows are skipped and 158/204 rows
                     (shield buffs) count only when `guarding`.
        `correct[e]` `defPlayerDmgCorrectRate` product when the attacker is a player (`pvp`),
                     else `defEnemyDmgCorrectRate`.
        `poise_div`  `toughnessDamageCutRate` product; menu poise is divided by it.
        `max_hp_rate`, `stats` as in `er-mechanics-resources.py`.
        """
        rows, unknown = self._active(active, pvp, hp_ratio, apply_stack, role)
        cut = dict.fromkeys(ELEMENTS, 1.0)
        cor = dict.fromkeys(ELEMENTS, 1.0)
        poise, hp = 1.0, 1.0
        stats = dict.fromkeys(STATS, 0)
        for r, src in rows:
            si = r['stateInfo']
            cut_on = si != 335 and (guarding or si not in (158, 204))
            for e in ELEMENTS:
                col = CUT_PT[phys_type] if e == 'physical' else CUT[e]
                if cut_on:
                    cut[e] = f32(cut[e] * f32(r[col]))
                cor[e] = f32(cor[e] * f32(r[(PVP_DEF if pvp else PVE_DEF)[e]]))
            poise = f32(poise * f32(r['toughnessDamageCutRate']))
            if r['maxHpRate'] > 0:
                hp = f32(hp * f32(r['maxHpRate']))
            for k, f in STATS.items():
                stats[k] += int(r[f])
        return {'cut': cut, 'correct': cor, 'poise_div': poise, 'max_hp_rate': hp,
                'stats': stats, 'entries': [(r['id'], s) for r, s in rows],
                'refused': list(self.last_refused), 'unknown': unknown}

    # -- expected factors over the corpus (section 10) ----------------------------------------
    def can_take_weapon_buff(self, weapon_id):
        """`EquipParamWeapon.isEnhance` of the exact row (affinity included): whether a grease or
        weapon-buff item can be used on it (`CanUseGoods`, grease.md 3c, `VERIFIED`)."""
        return bool(self.enhance.get(weapon_id, 0))

    def one_hit(self, sid):
        """A row that ends on the owner's next registered hit (buffs.md section 8): as attacker,
        `stateInfo` 384/385; as defender, `deleteCriteriaDamage` 1, on a hit with damage above 0.
        I-framed contacts register no hit, so neither counts them."""
        r = self.sp[sid]
        return r['deleteCriteriaDamage'] == 1 or r['stateInfo'] in NEXT_HIT_STATES

    def uptime(self, sid, fight_seconds=FIGHT_SECONDS, hits=FIGHT_ENGAGEMENTS, uses=1.0):
        """Share of a fight's landed hits that a live row covers with `uses` casts, the mean over
        the fight lengths `fight_seconds` (a number or `fight_points`).

        Applied before the first hit (`INFERRED`: a PvP player buffs before contact). -1 is
        permanent (great runes). A one-hit row (`one_hit`) covers `uses` of the `hits` landed
        hits (a count, or a schedule over `fight_seconds`, `_pairs`). A refreshed row (under
        `REFRESHED_ROW_S`) or a 0-duration row gets none, since what keeps it alive is not traced.
        Otherwise `uses` x duration over the fight, at most 1."""
        r = self.sp[sid]
        d = r['effectEndurance']
        if self.one_hit(sid):
            ps = _pairs(fight_seconds, hits)
            return sum(min(1.0, uses / max(h, 1)) for _, h in ps) / len(ps)
        if d < 0:
            return 1.0
        if d < REFRESHED_ROW_S:
            return 0.0
        fs = _fights(fight_seconds)
        return sum(min(1.0, uses * d / f) for f in fs) / len(fs)

    def source_recast(self, name):
        """(uses, cast frames) of a kit source `name` (a `resolve` name, `kind:name` or bare):
        what limits its recasts and what each one costs.

        spell       the casts one FP bar (`fp_bar`) pays for, full casts only (`Magic.mp`; whether
                    a spell has the skills' half-cost rule is not traced), `SPELL_CAST_FRAMES`
        consumable  `EquipParamGoods.maxNum`, the most a player holds (`INFERRED` as what a PvP
                    player carries into the fight), `item_cast_frames`
        tear        `TEAR_USES` (one physick charge)
        great rune  no limit, permanent anyway
        Anything else (a SpEffect id, an unknown name) gets one use."""
        if not isinstance(name, str):
            return 1, 0.0
        s = self.by_name.get(name.partition('#')[0])
        if s is None:
            return 1, 0.0
        kind = s['kind']
        if kind == 'great rune':
            return None, 0.0
        if kind == 'tear':
            return TEAR_USES, self.item_cast_frames
        if kind == 'spell':
            mp = (self.magic.get(s['goods']) or {}).get('mp') or 0
            return (None if mp <= 0 else int(self.fp_bar // mp)), SPELL_CAST_FRAMES
        n = (self.goods.get(s['goods']) or {}).get('maxNum') or 1
        return max(1, int(n)), self.item_cast_frames

    def source_plans(self, live, fight_seconds=FIGHT_SECONDS, hits=FIGHT_ENGAGEMENTS, limits=None):
        """{source: (uses, cast frames, [(fight s, recasts)])} for the sources of the live
        entries [(sid, source)]. One cast puts all of a source's rows on, so a source is recast
        on its longest timed row (or once per landed hit when any of its rows is next-hit),
        `recast_points` with the source's `source_recast` limit, or `limits[source]` = (uses, cast
        frames) when given (a skill's FP-bar casts)."""
        rows = {}
        for sid, src in live:
            rows.setdefault(src, []).append(sid)
        out = {}
        for src, ids in rows.items():
            uses, cast = (limits or {}).get(src) or self.source_recast(src)
            one = any(self.one_hit(i) for i in ids)
            timed = [self.sp[i]['effectEndurance'] for i in ids
                     if not self.one_hit(i) and self.sp[i]['effectEndurance'] >= REFRESHED_ROW_S]
            dur = max(timed, default=-1.0)
            out[src] = (uses, cast, recast_points(dur, fight_seconds, uses, hits, one_hit=one))
        return out

    def planned_uptime(self, sid, points, hits=FIGHT_ENGAGEMENTS):
        """`uptime` of one row whose source makes `points` [(fight s, recasts)] recasts: the mean
        over the fight lengths of `uptime` with 1 + recasts uses and that point's landed hits."""
        hs = _pairs(tuple(f for f, _ in points), hits)
        return sum(self.uptime(sid, f, h, 1.0 + n) for (f, n), (_, h) in zip(points, hs)) / len(points)

    def skill_rows(self, root):
        """The relevant rows a skill's buff SpEffect puts on its user (root + cycled rows)."""
        return [i for i, _ in self._closure([root]) if self.relevant(i)]

    def kit_factors(self, active, role='host', two_handed=False, fight_seconds=FIGHT_SECONDS,
                    hits=FIGHT_ENGAGEMENTS, extra=(), drop_kit_weapon_buffs=True,
                    drop_skill_weapon_buffs=False, sub_categories=()):
        """Attacker factors of one kit, each row weighted by its uptime (section 10).

        `active` names the kit in application order, `extra` is [(root SpEffect, weight, uses)]
        from the weapon's skill (`er-mechanics-ashes.skill_term` 'buffs'), applied after the kit
        and replayed through the same stacking. Per element: `pre` multiplies the weapon part of
        the AR, `post` the damage after defense (`atkPlayerDmgCorrectRate` x `*AttackRate`),
        `flat` is added after the AR multipliers; each row contributes 1 + uptime x (factor - 1).
        `stats` are uptime-weighted attribute adds.

        Weapon-buff rows (`WEAPON_BUFF_CATS`) from the kit are dropped by default: the grease
        sweep owns that slot. A skill's own weapon-buff row is dropped when
        `drop_skill_weapon_buffs` (a greased build: the grease holds the slot).

        Recasts (`source_plans`): every kit source is recast as often as keeps it on the fight,
        at most its `source_recast` limit, and each recast takes its cast frames out of the
        fight; `time_factor` is the mean over the fight lengths of 1 - sum of recasts x cast /
        fight, and `post` carries it (the kit owner spends that share of the fight casting, not
        hitting). A skill's casts (`extra` uses, the FP bar's) are recast the same way, but its
        time is charged by its own option (`er-mechanics-ashes.buff_option`), not here."""
        fight_seconds = _fights(fight_seconds)
        key = (tuple(active), role, two_handed, fight_seconds, hits, tuple(extra),
               drop_kit_weapon_buffs, drop_skill_weapon_buffs, tuple(sub_categories),
               self.fp_bar, self.item_cast_frames)
        if key in self._kit_cache:
            return self._kit_cache[key]
        entries, unknown = self.resolve(list(active), role)
        weight, limits = {}, {}
        for root, w, uses in extra:
            src = f'skill {root}'
            weight[src] = (w, True)
            limits[src] = (uses, 0.0)
            entries += [(i, src) for i in self.skill_rows(root)]
        live = self.stack(entries)
        kept = []
        dropped = []
        for sid, src in live:
            from_skill = weight.get(src, (1.0, False))[1]
            if self.sp[sid]['spCategory'] in WEAPON_BUFF_CATS and \
                    (drop_skill_weapon_buffs if from_skill else drop_kit_weapon_buffs):
                dropped.append((sid, src))
            else:
                kept.append((sid, src))
        plans = self.source_plans(kept, fight_seconds, hits, limits)
        cost = [0.0] * len(fight_seconds)
        for src, (_, cast, pts) in plans.items():
            for k, (f, n) in enumerate(pts):
                cost[k] += n * (cast or 0.0) / CAST_FPS / f
        time_factor = sum(max(0.0, 1.0 - c) for c in cost) / len(cost)
        pre = dict.fromkeys(ELEMENT_KEYS, 1.0)
        post = dict.fromkeys(ELEMENT_KEYS, 1.0)
        flat = dict.fromkeys(ELEMENT_KEYS, 0.0)
        stats = dict.fromkeys(STATS, 0.0)
        used = []
        for sid, src in kept:
            w = weight.get(src, (1.0, False))[0]
            u = w * self.planned_uptime(sid, plans[src][2], hits)
            if u <= 0:
                continue
            f = self.attack_context([sid], pvp=True, two_handed=two_handed,
                                    sub_categories=sub_categories, apply_stack=False)
            if not f['entries']:
                continue                              # the hand / sub-category gate refused it
            for e in ELEMENT_KEYS:
                pre[e] *= 1.0 + u * (f['ar_rate'][e] - 1.0)
                post[e] *= 1.0 + u * (f['pvp_rate'][e] * f['atk_rate'][e] - 1.0)
                flat[e] += u * f['flat_add'][e]
            for k in STATS:
                stats[k] += u * f['stats'][k]
            used.append((sid, src, round(u, 4)))
        post = {e: v * time_factor for e, v in post.items()}
        out = {'pre': pre, 'post': post, 'flat': flat, 'stats': stats, 'entries': used,
               'dropped': dropped, 'unknown': unknown, 'time_factor': time_factor,
               'recasts': {s: sum(n for _, n in p[2]) / len(p[2]) for s, p in plans.items()}}
        self._kit_cache[key] = out
        return out

    def expected_attack(self, kits, two_handed=False, stat_ratio=None, alternatives=(), **kw):
        """Mean of `kit_factors` over `kits` (`corpus_kits`) and each kit's role weights.

        `stat_ratio(stats) -> {element: AR with the adds / AR without}` turns attribute adds into
        a `pre` factor for one weapon (`ar_stat_ratio`). `alternatives` is the weapon's skill
        choice as [(p, extra)] (`er-mechanics-ashes.skill_term` 'buff_alternatives'): a weapon
        holds one skill, so each kit is scored once per alternative with that skill's `extra`,
        weighted by p, and once with none for the rest of the mass. `joint` is the mean of
        pre x post, the single factor on an element's damage when the defense curve is taken as
        linear. `time_factor` (already in `post`) and `recasts_by_source` are the kit means."""
        acc = {k: dict.fromkeys(ELEMENT_KEYS, 0.0) for k in ('pre', 'post', 'joint', 'flat')}
        by_src, total = {}, 0.0
        tf_acc, rc_src = 0.0, {}
        rest = max(0.0, 1.0 - sum(p for p, _ in alternatives))
        branches = [(p, tuple(x)) for p, x in alternatives if p > 0] + ([(rest, ())] if rest > 0 else [])
        for k in kits:
            for role, w0 in k['roles'].items():
                for pb, extra in branches:
                    w = w0 * pb
                    f = self.kit_factors(k['active'], role, two_handed, extra=extra, **kw)
                    sr = stat_ratio(f['stats']) if stat_ratio and any(f['stats'].values()) else {}
                    for e in ELEMENT_KEYS:
                        p = f['pre'][e] * sr.get(e, 1.0)
                        acc['pre'][e] += w * p
                        acc['post'][e] += w * f['post'][e]
                        acc['joint'][e] += w * p * f['post'][e]
                        acc['flat'][e] += w * f['flat'][e]
                    for sid, src, u in f['entries']:
                        by_src[src] = by_src.get(src, 0.0) + w * u
                    for src, n in f['recasts'].items():
                        rc_src[src] = rc_src.get(src, 0.0) + w * n
                    tf_acc += w * f['time_factor']
                    total += w
        out = {k: {e: v / total for e, v in d.items()} for k, d in acc.items()} if total else \
            {k: dict.fromkeys(ELEMENT_KEYS, 1.0 if k != 'flat' else 0.0) for k in acc}
        out['kits'] = total
        out['time_factor'] = tf_acc / total if total else 1.0
        out['recasts_by_source'] = {s: v / total for s, v in sorted(rc_src.items(), key=lambda kv: -kv[1])} \
            if total else {}
        out['uptime_by_source'] = {s: v / total for s, v in sorted(by_src.items(), key=lambda kv: -kv[1])} \
            if total else {}
        return out

    def expected_defense(self, kits, fight_seconds=FIGHT_SECONDS, hits=FIGHT_ENGAGEMENTS):
        """Mean over defender kits of the post-defense factor on incoming player damage, per
        damage key (`DAMAGE_KEYS`: slash/strike/pierce/standard for physical, then the
        elements), `cut` x `correct` with uptime, plus the mean `max_hp_rate`.

        Each defender source is recast as `kit_factors` recasts an attacker's (`source_plans`).
        The defender's cast time is not charged: it is the defender's time, not the attacker's
        damage per hit; what it is worth to the attacker is a punish window, not modelled."""
        fight_seconds = _fights(fight_seconds)
        acc = dict.fromkeys(DAMAGE_KEYS, 0.0)
        hp, total = 0.0, 0.0
        for k in kits:
            for role, w in k['roles'].items():
                key = ('def', tuple(k['active']), role, fight_seconds, hits, self.fp_bar)
                if key not in self._kit_cache:
                    live = self.stack(self.resolve(list(k['active']), role)[0])
                    plans = self.source_plans(live, fight_seconds, hits)
                    fac, mh = dict.fromkeys(DAMAGE_KEYS, 1.0), 1.0
                    for sid, src in live:
                        u = self.planned_uptime(sid, plans[src][2], hits)
                        if u <= 0:
                            continue
                        for pt in PHYS_TYPES:
                            d = self.defense_context([sid], pvp=True, phys_type=pt, apply_stack=False)
                            fac[pt] *= 1.0 + u * (d['cut']['physical'] * d['correct']['physical'] - 1.0)
                        d = self.defense_context([sid], pvp=True, apply_stack=False)
                        for e in ELEMENT_KEYS[1:]:
                            fac[e] *= 1.0 + u * (d['cut'][e] * d['correct'][e] - 1.0)
                        mh *= 1.0 + u * (d['max_hp_rate'] - 1.0)
                    self._kit_cache[key] = (fac, mh)
                fac, mh = self._kit_cache[key]
                for dk in DAMAGE_KEYS:
                    acc[dk] += w * fac[dk]
                hp += w * mh
                total += w
        out = {dk: v / total for dk, v in acc.items()} if total else dict.fromkeys(DAMAGE_KEYS, 1.0)
        return {'factor': out, 'max_hp_rate': hp / total if total else 1.0, 'kits': total}

    # -- healing over time (section 11) -------------------------------------------------------
    def regen_row(self, sid):
        """(HP per second, max-HP percent per second, duration) of a row that heals its owner over
        time, or None. A heal is a negative `changeHpPoint` / `changeHpRate` (poison and rot write
        the same columns positive; `er-mechanics-status.proc_effect` subtracts them), ticking
        every `motionInterval` s. Rows that live under `REFRESHED_ROW_S` or 0 s (Crimson-Sapping's
        on-hit 0.25 s row, Crimsonwhorl's chained one-shot, Minor Erdtree's field ticks) are
        excluded: what re-applies them is not traced. Duration -1 is permanent (equipment)."""
        r = self.sp.get(sid)
        if r is None or r['motionInterval'] <= 0:
            return None
        pt, pct = 0.0 - r['changeHpPoint'], 0.0 - r['changeHpRate']
        if pt < 0 or pct < 0 or pt + pct <= 0:
            return None
        d = r['effectEndurance']
        if 0 <= d < REFRESHED_ROW_S:
            return None
        iv = r['motionInterval']
        return pt / iv, pct / iv, d

    def regen_sources(self):
        """{`kind:name`: [regen row ids]} for spells, consumables, tears (their whole closure,
        relevant or not) and equipment resident effects (`REGEN_EQUIP`)."""
        if self._regen_src is not None:
            return self._regen_src
        out = {}
        for gid, g in sorted(self.goods.items()):
            name = self.goods_name.get(gid)
            refs = _goods_refs(self, gid, g)
            if not name or refs is None:
                continue
            kind, sp, bl = refs
            ids = [i for i, _ in self._closure(sp, bl) if self.regen_row(i)]
            if ids:
                out.setdefault(f'{kind}:{name}', ids)
        files = self._files
        for stem, kind, cols in REGEN_EQUIP:
            names = PR.row_names(stem)
            for r in PR.rows(PR.param_bytes(files, stem), set(cols))[0]:
                name = names.get(r['id'])
                ids = [r[c] for c in cols if self.regen_row(r[c])]
                if name and ids:
                    out.setdefault(f'{kind}:{name}', ids)
        self._regen_src = out
        return out

    def regen_hp(self, rows, max_hp, seconds):
        """HP a defender with `rows` ([(HP/s, %/s, duration)]) regains over `seconds`, every row
        live from the fight's start (`INFERRED`, as `uptime`) and cast once."""
        total = 0.0
        for pt, pct, d in rows:
            t = seconds if d < 0 else min(d, seconds)
            total += (pt + max_hp * pct / 100.0) * t
        return total


# ---------------------------------------------------------------------------------------------
def _fmt_effect(nd):
    return ', '.join(f'{k} {round(v, 4) if isinstance(v, float) else v}' for k, v in nd.items())


def show(m, names):
    for n in names:
        s = m.by_name.get(n)
        if s is None:
            print(f'{n}: no buff source by that name')
            continue
        print(f"{n} ({s['kind']}, goods {s['goods']})")
        for sid, via in s['entries']:
            mt = m.meta(sid)
            print(f"  sp {sid} [{m.sp_names.get(sid, '')}] via {via}")
            print(f"    dur {mt['effectEndurance']} spCategory {mt['spCategory']} "
                  f"prio {mt['categoryPriority']} vfx {mt['vfxId']} {mt.get('vfx', '')} "
                  f"stateInfo {mt['stateInfo']} ({mt['stateInfo_name']}) wep {mt['wepParamChange']} "
                  f"condHp {mt['conditionHp']}/{mt['conditionHpRate']} "
                  f"useByPoint {mt['isUseAtkParamAtkPowerCorrect']}")
            print(f'    {_fmt_effect(m.nondefault(sid))}')


def table(m, kinds=None):
    for s in m.sources:
        if kinds and s['kind'] not in kinds:
            continue
        for sid, via in s['entries']:
            mt = m.meta(sid)
            print(f"{s['kind']:10s} | {s['name']:34s} | {sid:9d} | {mt['effectEndurance']:6.1f} | "
                  f"{mt['spCategory']:5d} | {mt['vfxId']:7d} | {mt['stateInfo']:4d} | "
                  f"{_fmt_effect(m.nondefault(sid))}")


def _names(slots):
    return [x.get('name') for x in (slots or []) if isinstance(x, dict) and x.get('name')]


def corpus(m, path=CORPUS, rl=(140, 160)):
    """How often each buff source appears in STR PvP builds (tag Strength, PvP by `isPvE` false or
    a PvP tag), level window inclusive."""
    counts, n = {}, 0
    for line in open(path, encoding='utf-8'):
        b = json.loads(line)['build']
        tags = b.get('tags') or []
        st = b.get('stats') or {}
        lvl = st.get('rl')
        if not isinstance(lvl, int) or not rl[0] <= lvl <= rl[1]:
            continue
        pvp = b.get('isPvE') is False or any(t in tags for t in PVP_TAGS)
        if not (pvp and 'Strength' in tags):
            continue
        n += 1
        items = b.get('items') or {}
        # The slot a name came from picks the kind: "Golden Vow" in the tools is the Shadow of
        # the Erdtree consumable (goods 2003170), not the incantation.
        have = {('spell', x) for x in _names((b.get('spells') or {}).get('slots')) + _implied_spells(b)}
        have |= {('item', x) for x in _names((items.get('tools') or {}).get('slots'))}
        have |= {('tear', x) for x in items.get('crystalTears') or []}
        if b.get('greatRune'):
            have.add(('great rune', b['greatRune']))
        for kind, x in have:
            keys = ('consumable', 'tear') if kind == 'item' else (kind,)
            s = next((m.by_name[f'{k}:{x}'] for k in keys if f'{k}:{x}' in m.by_name), None)
            if s is not None or x == 'Rune Arc':
                key = f"{s['kind'] if s else 'consumable'}:{x}"
                counts[key] = counts.get(key, 0) + 1
    return n, counts


def _offensive(m, source):
    """A source with any attacker column on any of its rows (AR rate, flat add, `*AttackRate`,
    `atkPlayerDmgCorrectRate`, attribute adds)."""
    cols = list(AP_RATE.values()) + list(AP_RATE_PT.values()) + list(AP_FLAT.values()) \
        + list(ATK_RATE.values()) + list(PVP_ATK.values()) + list(STATS.values())
    return any(m.nondefault(sid, cols) for sid, _ in source['entries'])


def corpus_kits(m, path=CORPUS, rl=(140, 160), archetype='Strength', order='offense-last'):
    """Every PvP build of the window as a buff kit: [{'active': names, 'roles': {role: w},
    'build': id}] (section 10).

    PvP = `isPvE` false or a PvP tag (as `corpus`); `archetype` is a planner tag (None = every
    PvP build, the defender corpus). A kit is the great rune (taken as active whenever it is set:
    Rune Arc use is not in the data, `INFERRED`), the crystal tears, the tool slots and any
    spell slots, resolved to buff sources the way `corpus` does. `order` puts the sources that
    raise the owner's damage last ('offense-last', the kit as its owner attacks) or first
    ('defense-last', the kit as its owner is hit), so where a kit holds two rows of one
    exclusive category (Exalted Flesh and Boiled Crab, both 151) the stacking replay keeps the
    one that fits the side being modelled (`INFERRED`). `roles` spreads the build over
    `ROLE_BY_TAG` of its tags, host when it has none."""
    kits = []
    for line in open(path, encoding='utf-8'):
        row = json.loads(line)
        b = row['build']
        tags = b.get('tags') or []
        lvl = (b.get('stats') or {}).get('rl')
        if b.get('isPvE') is True or not isinstance(lvl, int) or not rl[0] <= lvl <= rl[1]:
            continue
        if not (b.get('isPvE') is False or any(t in tags for t in PVP_TAGS + ('Fishing',))):
            continue
        if archetype and archetype not in tags:
            continue
        items = b.get('items') or {}
        cand = []
        if b.get('greatRune'):
            cand.append(('great rune', b['greatRune']))
        cand += [('tear', x) for x in items.get('crystalTears') or []]
        cand += [('item', x) for x in _names((items.get('tools') or {}).get('slots'))]
        cand += [('spell', x) for x in _names((b.get('spells') or {}).get('slots')) + _implied_spells(b)]
        names, seen = [], set()
        for kind, x in cand:
            keys = ('consumable', 'tear') if kind == 'item' else (kind,)
            k = next((f'{k}:{x}' for k in keys if f'{k}:{x}' in m.by_name), None)
            if k is None or k in seen:
                continue
            seen.add(k)
            names.append(k + ('#0' if x in KIT_SELECTORS else ''))
        off = {n: _offensive(m, m.by_name[n.partition('#')[0]]) for n in names}
        names.sort(key=lambda n: off[n] if order == 'offense-last' else not off[n])
        roles = {}
        for t in tags:
            if t in ROLE_BY_TAG:
                roles[ROLE_BY_TAG[t]] = roles.get(ROLE_BY_TAG[t], 0) + 1
        roles = roles or {'host': 1}
        n = sum(roles.values())
        kits.append({'active': names, 'roles': {r: v / n for r, v in roles.items()},
                     'build': b.get('id')})
    return kits


def build_regen(m, b):
    """A build's healing-over-time rows after stacking (section 11): [(sid, source)] and
    [(HP/s, %/s, duration)]. Spells (with `IMPLIED_SPELLS`), tools, crystal tears, talismans,
    armor and the held weapons (`HELD_EQUIP_INDEX`). The timed heals all sit in `spCategory` 161
    and replace each other (section 3), so they are applied weakest first: a player keeps the
    strongest one (`INFERRED`)."""
    src = m.regen_sources()
    items = b.get('items') or {}
    cand = [('spell', x) for x in _names((b.get('spells') or {}).get('slots')) + _implied_spells(b)]
    cand += [('item', x) for x in _names((items.get('tools') or {}).get('slots'))]
    cand += [('tear', x) for x in items.get('crystalTears') or []]
    cand += [('talisman', x) for x in _names((b.get('talismans') or {}).get('slots'))]
    for part in (b.get('protectors') or {}).values():
        cand += [('armor', x) for x in _names((part or {}).get('slots'))]
    cand += [('weapon', x.get('name')) for x in (b.get('inventory') or {}).get('slots') or []
             if isinstance(x, dict) and x.get('equipIndex') in HELD_EQUIP_INDEX]
    entries, seen = [], set()
    for kind, x in cand:
        keys = ('consumable', 'tear') if kind == 'item' else (kind,)
        k = next((f'{k}:{x}' for k in keys if f'{k}:{x}' in src), None)
        if k is None or k in seen:
            continue
        seen.add(k)
        entries += [(i, k) for i in src[k]]

    def worth(e):
        pt, pct, d = m.regen_row(e[0])
        return (d < 0, (pt + pct) * (d if d > 0 else 0.0))
    live = m.stack(sorted(entries, key=worth))
    return live, [m.regen_row(i) for i, _ in live]


def corpus_regen(m, path=CORPUS, rl=(140, 160)):
    """Every PvP build of the window (the `corpus_kits` defender filter) as
    {'build', 'hp' (computed max HP), 'live', 'rows'} (`build_regen`)."""
    out = []
    for line in open(path, encoding='utf-8'):
        b = json.loads(line)['build']
        tags = b.get('tags') or []
        lvl = (b.get('stats') or {}).get('rl')
        if b.get('isPvE') is True or not isinstance(lvl, int) or not rl[0] <= lvl <= rl[1]:
            continue
        if not (b.get('isPvE') is False or any(t in tags for t in PVP_TAGS + ('Fishing',))):
            continue
        hp = (b.get('computed') or {}).get('maxHealth')
        if not hp:
            continue
        live, rows = build_regen(m, b)
        out.append({'build': b.get('id'), 'hp': float(hp), 'live': live, 'rows': rows})
    return out


def sustain_factor(m, defenders, seconds_fn, hit_hp=None, engagements=FIGHT_ENGAGEMENTS,
                   iterations=30):
    """The share of a hit's damage that still counts toward the kill once the defenders' healing
    over time is added to their HP (section 11).

    Effective HP over a fight of n engagements is HP + R(T(n)), `T = seconds_fn(n)`. With a fixed
    fight (`hit_hp` None) n = `engagements` and the factor is the mean over defenders of
    HP / (HP + R): the same for every weapon. Paced (`hit_hp` = a weapon's landed damage per
    engagement), each defender's n solves n x hit = HP + R(T(n)) (fixed point; R is concave
    in T, so it converges from n = HP / hit), and the factor is HP / (n x hit): a weapon that
    needs more engagements gives the heals longer to tick."""
    acc = 0.0
    for d in defenders:
        hp = d['hp']
        if not d['rows']:
            acc += 1.0
            continue
        if hit_hp is None:
            acc += hp / (hp + m.regen_hp(d['rows'], hp, seconds_fn(engagements)))
            continue
        if hit_hp <= 0:
            continue
        n = hp / hit_hp
        for _ in range(iterations):
            nxt = (hp + m.regen_hp(d['rows'], hp, seconds_fn(n))) / hit_hp
            if abs(nxt - n) < 1e-6:
                break
            n = nxt
            if n > 1e4:                     # heals outpace the weapon: it never kills
                n = float('inf')
                break
        acc += hp / (n * hit_hp)
    return acc / len(defenders) if defenders else 1.0


def ar_stat_ratio(weapon, affinity, level, stats, two_handed, tables=None):
    """{element: AR with attribute adds / AR without} for one weapon build, as a function of an
    adds dict (`kit_factors` 'stats'). Adds are rounded to whole points; no cap is applied past
    99 (the correction graphs run past it). `er-mechanics-ar.attack_rating` does the AR."""
    ar = _mod('er_mechanics_ar', 'er-mechanics-ar.py')
    tables = tables or ar.Tables(None)
    base_stats = {k: int(v) for k, v in (stats or {}).items()}

    def rating(st):
        r = ar.attack_rating(tables, weapon, affinity, level, st, two_handed)['damage']
        return {e: r.get(e, {}).get('total', 0.0) for e in ELEMENT_KEYS}
    base = rating(base_stats)
    cache = {}

    def ratio(adds):
        key = tuple(sorted((k, int(round(v))) for k, v in adds.items() if int(round(v))))
        if key not in cache:
            st = dict(base_stats)
            for k, v in key:
                st[k] = st.get(k, 0) + v
            got = rating(st)
            cache[key] = {e: (got[e] / base[e] if base[e] else 1.0) for e in ELEMENT_KEYS}
        return cache[key]
    return ratio


def selftest(m):
    fails, count = [], [0]

    def check(label, got, want, tol=1e-4):
        count[0] += 1
        ok = abs(got - want) <= tol if isinstance(want, float) else got == want
        if not ok:
            fails.append(f'{label}: got {got!r}, want {want!r}')

    # The checker must be able to fail: a wrong value and a wrong stack order are caught.
    probe = []
    for got, want in [(1.2, 1.15), ([1], [2])]:
        before = len(fails)
        check('negative control', got, want)
        probe.append(len(fails) > before)
    del fails[:]
    count[0] = 0
    if not all(probe):
        print('selftest FAILED: the checker accepted a wrong value')
        return 1
    for label, fn in SELFTEST_CASES:
        try:
            fn(m, check)
        except Exception as ex:  # a missing row is a failure, not a crash
            fails.append(f'{label}: {type(ex).__name__}: {ex}')
    if fails:
        print('selftest FAILED:')
        for f in fails:
            print('  ' + f)
        return 1
    print(f'selftest ok ({count[0]} checks in {len(SELFTEST_CASES)} groups)')
    return 0


def _t_rows(m, check):
    """Regulation values of the rows the doc tables cite (1.17.1 regulation)."""
    for sid, field, want in [
            (1660000, 'atkPlayerDmgCorrectRate_Physics', 1.075), (1660000, 'atkEnemyDmgCorrectRate_Physics', 1.15),
            (1660000, 'defPlayerDmgCorrectRate_Physics', 0.95), (1660000, 'defEnemyDmgCorrectRate_Physics', 0.90),
            (1660000, 'effectEndurance', 80.0), (1660000, 'spCategory', 160),
            (1605000, 'atkPlayerDmgCorrectRate_Physics', 1.15), (1605000, 'atkEnemyDmgCorrectRate_Physics', 1.2),
            (1605000, 'atkPlayerDmgCorrectRate_Fire', 1.15), (1605000, 'effectEndurance', 30.0),
            (1605000, 'spCategory', 151),
            (1733000, 'atkPlayerDmgCorrectRate_Physics', 1.25), (1733000, 'defPlayerDmgCorrectRate_Physics', 1.3),
            (1733000, 'spCategory', 151),
            (3950, 'atkPlayerDmgCorrectRate_Physics', 1.15), (3950, 'atkEnemyDmgCorrectRate_Physics', 1.2),
            (3950, 'spCategory', 151),
            (500820, 'defPlayerDmgCorrectRate_Physics', 0.85), (500820, 'defEnemyDmgCorrectRate_Physics', 0.8),
            (500820, 'spCategory', 151),
            (511011, 'defPlayerDmgCorrectRate_Physics', 0.9), (511011, 'defEnemyDmgCorrectRate_Physics', 0.85),
            (511011, 'effectEndurance', 180.0), (511011, 'spCategory', 20),
            (3515, 'addStrengthStatus', 10), (511014, 'physicsAttackRate', 1.15),
            (511014, 'magicSubCategoryChange1', 100),
            (3558, 'atkPlayerDmgCorrectRate_Physics', 1.09), (3559, 'atkPlayerDmgCorrectRate_Physics', 1.13),
            (3560, 'atkPlayerDmgCorrectRate_Physics', 1.2),
            (600, 'addStrengthStatus', 5), (610, 'maxHpRate', 1.15), (620, 'maxHpRate', 1.25),
            (1632000, 'fireAttackPower', 40), (1632000, 'wepParamChange', 1),
            # Next-hit end (section 8): the buff row has no end-on-hit field; the attack's 1665/1667
            # cycles a 0-duration row into the same weapon-buff category.
            (1701, 'stateInfo', 384), (1703, 'stateInfo', 385), (1701, 'deleteCriteriaDamage', 0),
            (1665, 'cycleOccurrenceSpEffectId', 1661), (1661, 'spCategory', 162),
            (1661, 'effectEndurance', 0.0), (1701, 'spCategory', 162),
            (1667, 'cycleOccurrenceSpEffectId', 1666), (1666, 'spCategory', 163),
            (1703, 'spCategory', 163), (3507, 'deleteCriteriaDamage', 1),
            (503500, 'deleteCriteriaDamage', 1)]:
        check(f'sp {sid}.{field}', round(float(m.sp[sid][field]), 4), float(want))


def _t_sources(m, check):
    for name, kind, first in [("Golden Vow", 'spell', 1660000), ("consumable:Golden Vow", 'consumable', 20503170),
                              ("Flame, Grant Me Strength", 'spell', 1605000),
                              ("Opaline Hardtear", 'tear', 511011), ("Boiled Crab", 'consumable', 500820),
                              ("Radahn's Great Rune", 'great rune', 610)]:
        s = m.by_name[name]
        check(f'{name} kind', s['kind'], kind)
        check(f'{name} first row', s['entries'][0][0], first)
    check('Thorny default tier', m.resolve(['Thorny Cracked Tear'])[0], [(3561, 'Thorny Cracked Tear')])
    check('Thorny tier 2', m.resolve(['Thorny Cracked Tear#2'])[0], [(3559, 'Thorny Cracked Tear#2')])
    check('Sacred Bloody Flesh untriggered', [i for i, _ in m.resolve(['Sacred Bloody Flesh'])[0]], [20501210])


def _t_stacking(m, check):
    ids = lambda names: [i for i, _ in m.stack(m.resolve(names)[0])]
    check('GV + FGMS stack (160 vs 151)', ids(['Golden Vow', 'Flame, Grant Me Strength']), [1660000, 1605000])
    check('FGMS then Howl: Howl replaces (151)', ids(['Flame, Grant Me Strength', 'Howl of Shabriri'])[:1], [1733000])
    check('FGMS then Crab: Crab replaces (151)', ids(['Flame, Grant Me Strength', 'Boiled Crab']), [500820])
    check('Exalted Flesh then FGMS', ids(['Exalted Flesh', 'Flame, Grant Me Strength']), [1605000])
    check('GV spell then GV item (160)', ids(['Golden Vow', 'consumable:Golden Vow']), [20503170])
    check('two tears (20) stack', ids(['Opaline Hardtear', 'Strength-knot Crystal Tear']), [511011, 3515])
    check('FGMS twice keeps one', ids(['Flame, Grant Me Strength', 'Flame, Grant Me Strength']), [1605000])
    check('201: same priority (55) replaces', ids(['Pickled Turtle Neck', 'Well-Pickled Turtle Neck']),
          [20501170])
    check('201: priorities 50 and 55 stack', ids(['Rock Heart', 'Well-Pickled Turtle Neck']),
          [19980, 20501170])
    # Uplifting Aromatic: its 159 row is a new entry, its 160 row takes Golden Vow's slot.
    check('GV then Uplifting', ids(['Golden Vow', 'Uplifting Aromatic']), [503501, 503500])
    check('grease then Bloodflame (162)', ids(['Fire Grease', 'Bloodflame Blade'])[:1], [1632000])
    check('rune + tears + GV + FGMS', ids(["Radahn's Great Rune", 'Opaline Hardtear', 'Golden Vow',
                                           'Flame, Grant Me Strength']), [610, 511011, 1660000, 1605000])
    # R1: an apply-first category (>= 10000) is refused while one of that category lives.
    rot = next(i for i, r in m.sp.items() if r['spCategory'] == 10005 and r['effectEndurance'] > 0)
    rot2 = next(i for i, r in m.sp.items() if r['spCategory'] == 10005 and r['effectEndurance'] > 0 and i != rot)
    check('R1 apply-first', ids([rot, rot2]), [rot])


def _t_context(m, check):
    a = m.attack_context(['Golden Vow', 'Flame, Grant Me Strength'], pvp=True)
    check('GV x FGMS PvP phys', round(a['pvp_rate']['physical'], 5), round(f32(f32(1.075) * f32(1.15)), 5))
    check('GV x FGMS PvP magic (FGMS has none)', round(a['pvp_rate']['magic'], 5), 1.075)
    a = m.attack_context(['Golden Vow', 'Flame, Grant Me Strength'], pvp=False)
    check('GV x FGMS PvE phys', round(a['pvp_rate']['physical'], 4), round(f32(f32(1.15) * f32(1.2)), 4))
    check('no AR rate on body buffs', a['ar_rate']['physical'], 1.0)
    a = m.attack_context(['Bloodflame Blade'], by_point=90.0)
    check('Bloodflame right hand flat fire', round(a['flat_add']['fire'], 3), 36.0)
    a = m.attack_context(['Bloodflame Blade'], hand='left')
    check('Bloodflame not on left hand', a['flat_add']['fire'], 0.0)
    a = m.attack_context(['Spiked Cracked Tear'])
    check('Spiked tear off a normal R1', a['atk_rate']['physical'], 1.0)
    a = m.attack_context(['Spiked Cracked Tear'], sub_categories=(100,))
    check('Spiked tear on a charged heavy', round(a['atk_rate']['physical'], 4), 1.15)
    d = m.defense_context(['Opaline Hardtear', 'Boiled Crab'], pvp=True)
    check('Opaline x Crab PvP phys', round(d['correct']['physical'], 4), round(f32(f32(0.9) * f32(0.85)), 4))
    check('Opaline PvP fire', round(d['correct']['fire'], 4), 0.9)
    d = m.defense_context(["Morgott's Great Rune"])
    check('Morgott max HP rate', round(d['max_hp_rate'], 4), 1.25)
    a = m.attack_context(['Strength-knot Crystal Tear', "Godrick's Great Rune"])
    check('knot + Godrick STR', a['stats']['str'], 15)
    a = m.attack_context(['Strength-knot Crystal Tear', "Godrick's Great Rune"], role='invader')
    check('invader: Godrick inert', a['stats']['str'], 10)
    check('invader: Morgott inert', m.defense_context(["Morgott's Great Rune"], role='invader')['max_hp_rate'], 1.0)
    check('invader: Mohg row', [i for i, _ in m.resolve(["Mohg's Great Rune"], 'invader')[0]], [649])
    check('cooperator: no rune rows', m.resolve(["Radahn's Great Rune"], 'cooperator')[0], [])
    check('Rune Arc without a rune', round(m.defense_context([RUNE_ARC_NO_RUNE])['max_hp_rate'], 4), 1.1)
    d = m.defense_context(["Scholar's Shield"])
    check("Scholar's Shield off guard", d['cut']['magic'], 1.0)
    d = m.defense_context(["Scholar's Shield"], guarding=True)
    check("Scholar's Shield on guard", round(d['cut']['magic'], 4), 0.3)
    a = m.attack_context(['Spiked Cracked Tear', 'Spiked Cracked Tear'], sub_categories=(100,))
    check('same tear twice is one entry', round(a['atk_rate']['physical'], 4), 1.15)


def _t_expected(m, check):
    """Section 10: uptime, the weapon-buff slot, kit factors and the defender side."""
    grease = 'consumable:Drawstring Dragonbolt Grease'
    ids = lambda names: [i for i, _ in m.stack(m.resolve(names)[0])]
    check('War Cry 1811 is a right-hand weapon buff (162)', m.sp[1811]['spCategory'], 162)
    check("Braggart's Roar 1863 is the left-hand one (163)", m.sp[1863]['spCategory'], 163)
    check('War Cry replaces the right-hand grease row (one 162 entry)', ids([grease, 1811]),
          [1811, 20501413])
    gs = 2000000                                                # Greatsword; +100 Heavy, +400 Fire
    check('Heavy Greatsword takes greases (isEnhance)', m.can_take_weapon_buff(gs + 100), True)
    check('Fire Greatsword does not (isEnhance 0)', m.can_take_weapon_buff(gs + 400), False)
    # The factor arithmetic below is checked on a 25 s fight (no recast for any of these rows);
    # the fight range and the recasts are checked after it.
    f25 = 25.0
    check('uptime: 180 s tear over a 25 s fight', m.uptime(511011, f25), 1.0)
    check('uptime: 25 s grease over a 50 s fight', m.uptime(20501411, 50.0), 0.5)
    check('uptime: one-hit row (Uplifting 503500) covers 1 of 5 hits', m.uptime(503500), 0.2)
    check("uptime: Royal Knight's Resolve 1701 next hit, 2 casts", m.uptime(1701, uses=2), 0.4)
    check('uptime: Thorny tier 3558 (1.5 s, refreshed) none', m.uptime(3558), 0.0)
    check('uptime: great rune row permanent', m.uptime(620), 1.0)
    f = m.kit_factors(['consumable:Exalted Flesh'], fight_seconds=f25)
    check('Exalted Flesh kit: physical post x1.15', round(f['post']['physical'], 4), 1.15)
    check('Exalted Flesh kit: magic untouched', f['post']['magic'], 1.0)
    f = m.kit_factors([grease], fight_seconds=f25)
    check('kit grease dropped by default', ([i for i, _ in f['dropped']], f['flat']['lightning']),
          ([20501411, 20501413], 0.0))
    f = m.kit_factors([], extra=((1810, 1.0, 1.0),), fight_seconds=f25)
    check('War Cry from the skill: right-hand physical AR x1.075', round(f['pre']['physical'], 4), 1.075)
    f = m.kit_factors([], extra=((1810, 1.0, 1.0),), drop_skill_weapon_buffs=True, fight_seconds=f25)
    check('War Cry dropped on a greased build', f['pre']['physical'], 1.0)
    f = m.kit_factors([], extra=((1860, 0.5, 1.0),), two_handed=True, fight_seconds=f25)
    check("Braggart's Roar at weight 0.5, two-handed: 1 + 0.5 x 0.1", round(f['pre']['physical'], 4), 1.05)
    f = m.kit_factors(["great rune:Godrick's Great Rune"], role='invader')
    check('invader kit: Godrick adds nothing', f['stats']['str'], 0.0)
    e = m.expected_attack([{'active': ['consumable:Exalted Flesh'], 'roles': {'host': 1.0}},
                           {'active': [], 'roles': {'host': 1.0}}], fight_seconds=f25)
    check('expected over two kits: half of x1.15', round(e['post']['physical'], 4), 1.075)
    e = m.expected_attack([{'active': [], 'roles': {'host': 1.0}}],
                          alternatives=[(0.5, ((1860, 1.0, 1.0),)), (0.5, ((820, 1.0, 1.0),))],
                          fight_seconds=f25)
    check("skill alternatives are exclusive: half Braggart's Roar, half Sacred Blade",
          (round(e['pre']['physical'], 4), round(e['flat']['holy'], 2)), (1.05, 45.0))
    d = m.expected_defense([{'active': ['tear:Opaline Hardtear'], 'roles': {'host': 1.0}}], fight_seconds=f25)
    check('Opaline defender: every damage key x0.9', {round(v, 4) for v in d['factor'].values()}, {0.9})
    d = m.expected_defense([{'active': ['consumable:Boiled Crab'], 'roles': {'host': 1.0}}])
    check('Crab defender recast over 180-300 s: physical x0.85, fire x1',
          (round(d['factor']['strike'], 4), d['factor']['fire']), (0.85, 1.0))
    check('offense test: Exalted Flesh yes, Boiled Crab no',
          (_offensive(m, m.by_name['consumable:Exalted Flesh']), _offensive(m, m.by_name['consumable:Boiled Crab'])),
          (True, False))
    # The fight range and the recasts.
    check('fight points: 180..300 s in 10 s steps', (FIGHT_SECONDS[0], FIGHT_SECONDS[-1], len(FIGHT_SECONDS)),
          (180.0, 300.0, 13))
    pts = recast_points(60.0)
    check('a 60 s buff in a 180-300 s fight: 2 to 4 recasts at every length',
          (min(n for _, n in pts), max(n for _, n in pts)), (2, 4))
    plan = recast_plan(60.0, cast_frames=60.0)
    check('a 60 s buff: mean recasts within 2..4, full uptime, 2 s per recast off the fight',
          (2.0 <= plan['recasts'] <= 4.0, plan['uptime'], round(plan['time_factor'], 4)),
          (True, 1.0, round(sum(1.0 - n * 2.0 / f for f, n in pts) / len(pts), 4)))
    check('a 60 s buff with one use is never recast: uptime 60 / fight',
          round(recast_plan(60.0, uses=1)['uptime'], 4),
          round(sum(60.0 / f for f in FIGHT_SECONDS) / len(FIGHT_SECONDS), 4))
    rkr = recast_plan(10.0, one_hit=True)
    check("a next-hit buff (Royal Knight's Resolve) is recast once per landed hit, whatever the fight",
          (rkr['recasts'], rkr['uptime']), (4.0, 1.0))
    check('a tear is one physick charge', m.source_recast('tear:Opaline Hardtear')[0], TEAR_USES)
    check('a consumable is limited by maxNum (Exalted Flesh 10)', m.source_recast('consumable:Exalted Flesh')[0], 10)
    f = m.kit_factors(['consumable:Exalted Flesh'])
    tf = recast_plan(30.0, uses=10, cast_frames=m.item_cast_frames)['time_factor']
    check('Exalted Flesh (30 s) recast over 180-300 s: post x1.15 x the recast time factor',
          (round(f['time_factor'], 4), round(f['post']['physical'], 4)), (round(tf, 4), round(1.15 * tf, 4)))
    f = m.kit_factors([], extra=((1810, 1.0, 1.0),))
    up = m.uptime(1811)
    check('one War Cry cast over 180-300 s covers only its duration', round(f['pre']['physical'], 4),
          round(1.0 + 0.075 * up, 4))
    check('the engagement spacing is not the fight length', (ENGAGEMENT_SECONDS, FIGHT_ENGAGEMENTS), (5.0, 5))
    # Landed hits per fight point.
    check('no flask: ceil(1945.84 / 471.5) = 5 hits at every length', set(fight_hits()), {5})
    check('5 flasks: ceil((1945.84 + 5 x 810) / 471.5) = 13, under every time bound', set(fight_hits(flasks=5)), {13})
    h10 = fight_hits(flasks=10)
    check('10 flasks: kill at 22, time bound floor((f - 18 s) / 5 x 0.5) = 16 at 180 s',
          (h10[0], h10[-1], max(h10)), (16, 22, 22))
    check('time bound alone: 18 / 24 / 30 hits at 180 / 240 / 300 s',
          fight_hits((180.0, 240.0, 300.0), damage=1.0), (18, 24, 30))
    fs, hs = fight_mix([(0.255, (180.0, 300.0), (5, 5)), (0.745, (180.0, 300.0), (16, 22))])
    check('mix 1 : 3 (shares 0.255 / 0.745 in quarters): points repeated, mean hits 1/4 x 5 + 3/4 x 19',
          (len(fs), sum(hs) / len(hs)), (8, 0.25 * 5 + 0.75 * 19))
    check('tag shares count mentions; a row tagged both counts in each',
          tag_shares([{'tags': ['Duels', 'Invasions']}, {'tags': ['Co-op/Gank']}, {'tags': []}])['duel'], 1 / 3)
    rk = recast_plan(10.0, (180.0, 300.0), hits=(5, 21), one_hit=True)
    check('a next-hit buff per point: 4 and 20 recasts, full uptime', (rk['recasts'], rk['uptime']), (12.0, 1.0))
    rk = recast_plan(10.0, (180.0, 300.0), uses=9, hits=(5, 21), one_hit=True)
    check('capped at 9 uses: 4 / 8 recasts, uptime 5/5 and 9/21', (rk['recasts'], round(rk['uptime'], 4)),
          (6.0, round((1.0 + 9 / 21) / 2, 4)))
    refill = ((9, 15, 20), 54.0)
    rk = recast_plan(10.0, 300.0, uses=20, cast_frames=30.0, hits=21, one_hit=True, refill=refill)
    check('20 casts need 2 cerulean drinks: 19 x 30 f + 2 x 54 f off 300 s',
          (rk['drinks'], round(rk['time_factor'], 5)), (2.0, round(1.0 - (19 * 30.0 + 2 * 54.0) / 30.0 / 300.0, 5)))
    check('a one-hit row over a schedule: the mean of 1 / hits per point',
          round(m.uptime(503500, (180.0, 300.0), (5, 20)), 4), round((0.2 + 0.05) / 2, 4))


def _t_regen(m, check):
    """Section 11: heal rows, the sign split from damage over time, stacking, the factor."""
    check('Bestial Vitality 1685000: 5 HP per 1 s for 120 s', m.regen_row(1685000), (5.0, 0.0, 120.0))
    check('Bestial Vitality over a 25 s fight', m.regen_hp([m.regen_row(1685000)], 1500.0, 25.0), 125.0)
    check('Bestial Vitality per cast (600 HP)', m.regen_hp([m.regen_row(1685000)], 1500.0, 500.0), 600.0)
    check('poison 834 (positive changeHp) is not a heal', m.regen_row(834), None)
    check("Crimson-Sapping's 0.25 s on-hit row is left out", m.regen_row(20511023), None)
    src = m.regen_sources()
    check('Icon Shield resident heal 3 HP/s permanent', m.regen_row(src['weapon:Icon Shield'][0]),
          (3.0, 0.0, -1.0))

    def build(spells=(), tears=(), armor=(), left=None):
        inv = [{'name': 'Frenzied Flame Seal', 'equipIndex': 4}] + \
            ([{'name': left, 'equipIndex': 3}] if left else [])
        return {'spells': {'slots': [{'name': s} for s in spells]}, 'items': {'crystalTears': list(tears)},
                'protectors': {'body': {'slots': [{'name': a} for a in armor]}}, 'inventory': {'slots': inv}}
    live, rows = build_regen(m, build())
    check('a held Frenzied Flame Seal implies Bestial Vitality', [i for i, _ in live], [1685000])
    live, _ = build_regen(m, build(tears=['Crimsonburst Crystal Tear']))
    check('category 161 heals replace each other; the stronger one (Crimsonburst) is kept',
          [i for i, _ in live], [511009])
    _, rows = build_regen(m, build(armor=['Royal Remains Helm', 'Royal Remains Armor'], left='Icon Shield'))
    check('equipment heals stack (2 + 2 + 3 + seal 5 HP/s)', sum(r[0] for r in rows), 12.0)
    dfs = [{'hp': 1000.0, 'rows': [(2.0, 0.0, -1.0)]}, {'hp': 1000.0, 'rows': []}]
    check('fixed window: mean of 1000/1050 and 1', sustain_factor(m, dfs, lambda n: 5.0 * n, engagements=5),
          (1000.0 / 1050.0 + 1.0) / 2)
    check('paced: 100 HP hits against 10 HP per engagement keep 90%',
          sustain_factor(m, dfs[:1], lambda n: 5.0 * n, hit_hp=100.0), 0.9)


SELFTEST_CASES = [('rows', _t_rows), ('sources', _t_sources), ('stacking', _t_stacking),
                  ('context', _t_context), ('expected', _t_expected), ('regen', _t_regen)]


def print_expected(m, a):
    lo, hi = (int(x) for x in a.rl.split('-'))
    att = corpus_kits(m, CORPUS, (lo, hi), a.archetype or None, 'offense-last')
    dfn = corpus_kits(m, CORPUS, (lo, hi), None, 'defense-last')
    ratio, level = None, None
    if a.weapon:
        ar = _mod('er_mechanics_ar', 'er-mechanics-ar.py')
        t = ar.Tables(None)
        wid = t.find_weapon(a.weapon, a.aff)
        level = t.max_level(t.weapons[wid]['reinforceTypeId'])
        stats = {k: int(v) for k, v in (p.split('=') for p in a.stats.split(','))}
        ratio = ar_stat_ratio(a.weapon, a.aff, level, stats, a.two_handed, t)
        print(f'{a.weapon} {a.aff}+{level} {"2H" if a.two_handed else "1H"}: weapon-buff items '
              f'usable (isEnhance) = {m.can_take_weapon_buff(wid)}')
    extra = tuple((s, 1.0, 1.0) for s in a.skill_buff)
    fights = fight_points(*a.fight_seconds)
    e = m.expected_attack(att, a.two_handed, ratio, alternatives=[(1.0, extra)] if extra else (),
                          fight_seconds=fights)
    d = m.expected_defense(dfn, fight_seconds=fights)
    print(f'attackers: {len(att)} {a.archetype or "PvP"} builds of RL {lo}-{hi}; defenders: {len(dfn)} PvP builds; '
          f'fight {FIGHT_ENGAGEMENTS} landed hits, buffs over {fights[0]:.0f}-{fights[-1]:.0f} s '
          f'({len(fights)} points)')
    for k in ('pre', 'post', 'joint', 'flat'):
        print(f'  attacker {k:5} ' + '  '.join(f'{el} {v:.4f}' for el, v in e[k].items()))
    print(f"  attacker recast time factor {e['time_factor']:.4f} (in post)")
    print('  defender post ' + '  '.join(f'{k} {v:.4f}' for k, v in d['factor'].items())
          + f"  (max HP x{d['max_hp_rate']:.4f}, not applied)")
    print('  attacker uptime by source (mean over kits):')
    for s, u in list(e['uptime_by_source'].items())[:15]:
        print(f'    {u:6.3f}  {s}')
    return 0


def print_recast(m, a):
    """The recast rule per buff source (`source_recast`, `recast_plan`) over the fight range:
    the sources the window's PvP kits carry, most common first."""
    lo, hi = (int(x) for x in a.rl.split('-'))
    fights = fight_points(*a.fight_seconds)
    kits = corpus_kits(m, CORPUS, (lo, hi), None, 'defense-last')
    count = {}
    for k in kits:
        for n in k['active']:
            count[n] = count.get(n, 0) + 1
    print(f'fight {fights[0]:.0f}-{fights[-1]:.0f} s ({len(fights)} points), FP bar {m.fp_bar:.0f}, '
          f'item cast {m.item_cast_frames:.0f} f, spell cast {SPELL_CAST_FRAMES:.0f} f')
    print(f"  {'kits':>5} {'source':44} {'dur s':>6} {'uses':>5} {'cast f':>6} {'recasts':>7} "
          f"{'uptime':>6} {'time':>6}")
    for n, c in sorted(count.items(), key=lambda kv: -kv[1])[:a.top]:
        ents, _ = m.resolve([n])
        if not ents:
            continue
        plan = m.source_plans(ents, fights)[n]
        uses, cast, pts = plan
        timed = [m.sp[i]['effectEndurance'] for i, _ in ents if not m.one_hit(i)]
        dur = max(timed, default=0.0)
        up = max(m.planned_uptime(i, pts) for i, _ in ents)
        rec = sum(x for _, x in pts) / len(pts)
        tf = sum(max(0.0, 1.0 - x * cast / CAST_FPS / f) for f, x in pts) / len(pts)
        print(f"  {c:5d} {n:44} {dur:6.0f} {'-' if uses is None else uses:>5} {cast:6.0f} {rec:7.2f} "
              f"{up:6.3f} {tf:6.4f}")
    return 0


def print_regen(m, a):
    """Section 11: the healing-over-time sources and what the window's PvP defenders carry."""
    lo, hi = (int(x) for x in a.rl.split('-'))
    print('healing-over-time sources (HP/s, %max HP/s, duration s):')
    for k, ids in sorted(m.regen_sources().items()):
        print(f'  {k:42s} ' + '  '.join(f'{i}: {m.regen_row(i)[0]:.1f}/{m.regen_row(i)[1]:.2f}%/'
                                         f'{m.regen_row(i)[2]:.0f}' for i in ids))
    dfs = corpus_regen(m, CORPUS, (lo, hi))
    have = [d for d in dfs if d['rows']]
    by = {}
    for d in have:
        for _, s in d['live']:
            by[s] = by.get(s, 0) + 1
    hps = sorted(d['hp'] for d in dfs)
    med = hps[len(hps) // 2]
    # Sustain is paced by the engagements (the landed hits `ENGAGEMENT_SECONDS` apart), not by
    # the buffs' fight length.
    eng = a.engagement_seconds
    secs = FIGHT_ENGAGEMENTS * eng
    print(f'PvP defenders RL {lo}-{hi}: {len(dfs)} (median HP {med:.0f}); {len(have)} heal over time')
    for s, c in sorted(by.items(), key=lambda kv: -kv[1]):
        print(f'  {c:5d}  {s}')
    regen = [m.regen_hp(d['rows'], d['hp'], secs) for d in dfs]
    print(f'mean HP regained over {secs:.0f} s: {sum(regen) / len(regen):.1f} over all defenders, '
          f'{sum(regen) / max(len(have), 1):.1f} per healer')
    fixed = sustain_factor(m, dfs, lambda n: n * eng, engagements=FIGHT_ENGAGEMENTS)
    print(f'fixed-window factor (every weapon): {fixed:.5f}')
    for hit in (150, 250, 400, 600, 900):
        f = sustain_factor(m, dfs, lambda n: n * eng, hit_hp=hit)
        print(f'  paced, {hit:4d} HP per engagement: {f:.5f}')
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--regulation')
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--table', action='store_true')
    ap.add_argument('--kind', action='append')
    ap.add_argument('--show', nargs='+')
    ap.add_argument('--context', nargs='+')
    ap.add_argument('--pvp', action='store_true')
    ap.add_argument('--phys-type', default='standard', choices=PHYS_TYPES)
    ap.add_argument('--role', default='host', choices=('host', 'invader', 'cooperator'))
    ap.add_argument('--hand', default='right', choices=('right', 'left'))
    ap.add_argument('--two-handed', action='store_true')
    ap.add_argument('--sub', type=int, action='append', default=[],
                    help='ATK_SUB_CATEGORY of the attack (100 charged heavy, ...), repeatable')
    ap.add_argument('--guarding', action='store_true')
    ap.add_argument('--corpus', nargs='?', const=CORPUS)
    ap.add_argument('--rl', default='140-160')
    ap.add_argument('--expected', action='store_true',
                    help='expected attacker and defender factors over the corpus kits (section 10)')
    ap.add_argument('--archetype', default='Strength', help="planner tag of the attackers ('' = all PvP)")
    ap.add_argument('--weapon', help='with --expected: a weapon for the attribute-add AR ratio')
    ap.add_argument('--aff', default='Heavy')
    ap.add_argument('--stats', default='str=60,dex=12,int=9,fth=9,arc=9')
    ap.add_argument('--skill-buff', type=int, action='append', default=[],
                    help='root SpEffect of a skill buff to add to every kit (weight 1, one use)')
    ap.add_argument('--regen', action='store_true',
                    help='healing-over-time sources and the defenders carrying them (section 11)')
    ap.add_argument('--fight-seconds', type=float, nargs=2, metavar=('MIN', 'MAX'),
                    default=FIGHT_SECONDS_RANGE,
                    help='fight length range the buffs are averaged over (default 180 300)')
    ap.add_argument('--engagement-seconds', type=float, default=ENGAGEMENT_SECONDS,
                    help='with --regen: time between landed hits, the sustain pacing (default 5)')
    ap.add_argument('--recast', action='store_true',
                    help='the recast rule per buff source over the fight range (uses, cast, recasts, uptime)')
    ap.add_argument('--top', type=int, default=30, help='with --recast: sources shown')
    a = ap.parse_args()
    m = Buffs(a.regulation)
    if a.selftest:
        return selftest(m)
    if a.regen:
        return print_regen(m, a)
    if a.recast:
        return print_recast(m, a)
    if a.expected:
        return print_expected(m, a)
    if a.table:
        table(m, a.kind)
        return 0
    if a.show:
        show(m, a.show)
        return 0
    if a.context:
        print(json.dumps({
            'attack': m.attack_context(a.context, a.pvp, a.phys_type, a.hand, a.two_handed,
                                       sub_categories=a.sub, role=a.role),
            'defense': m.defense_context(a.context, a.pvp, a.phys_type, guarding=a.guarding,
                                         role=a.role)}, indent=1))
        return 0
    if a.corpus:
        lo, hi = (int(x) for x in a.rl.split('-'))
        n, counts = corpus(m, a.corpus, (lo, hi))
        print(f'STR PvP builds at RL {lo}-{hi}: {n}')
        for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f'  {v:5d} {100.0 * v / max(n, 1):5.1f}%  {k}')
        return 0
    ap.print_help()
    return 2


if __name__ == '__main__':
    sys.exit(main())
