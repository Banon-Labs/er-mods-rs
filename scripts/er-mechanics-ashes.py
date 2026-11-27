#!/usr/bin/env python3
"""Ashes of war and weapon skills: from the equipped item to what each skill does, offline.

    python3 scripts/er-mechanics-ashes.py skill "Lion's Claw" --weapon Claymore
    python3 scripts/er-mechanics-ashes.py skill 700 --weapon Greatsword --json
    python3 scripts/er-mechanics-ashes.py list [--mountable Claymore]
    python3 scripts/er-mechanics-ashes.py adoption --rl 150 --window 10 --filter pvptag
    python3 scripts/er-mechanics-ashes.py pvp --rl 150 [--top 25]
    python3 scripts/er-mechanics-ashes.py --selftest

Written for docs/er-mechanics/ashes-of-war.md; the labels (`VERIFIED`, `TAE`, `INFERRED`,
`COMMUNITY`, `MEASURED`) mean what they mean there and in attacks.md. The chain:

    weapon (+ mounted EquipParamGem) --GetSwordArtsParamIdForWeapon 0x140675270--> SwordArtsParam id
      (a mounted gem's swordArtsParamId wins over EquipParamWeapon.swordArtsParamId, `VERIFIED`)
    SwordArtsParam.swordArtsTypeNew --> TimeAct a<600 + swordArtsTypeNew>.tae, anims 04xxxx (`TAE`)
    each animation's events:
      1   AttackBehavior(judge)          melee hitbox
      2   BulletBehavior(judge)          projectile / area
      307 PCBehavior(type, judge)        type 8 resolves like an attack (see doc)
      330 WeaponArtFPConsumption         the FP charge, at that frame
      331 AddSpEffect_WeaponArts(a, b)   SpEffect a with enough FP, b without
      66/67/401 AddSpEffect(id)          SpEffect on self while the event runs
      795 hyperarmor window (ToughnessParam row), JumpTable 8 invincibility frames
    judge --PlayerIns::ResolveBehaviorId 0x140653580 with the weapon's behaviorVariationId-->
      BehaviorParam_PC row: refType 0 AtkParam_Pc, 1 Bullet (-> atkId_Bullet, spEffectId0-4,
      HitBulletID, intervalCreateBulletId), 2 SpEffectParam

Interface for a PvP ranking (see `skill_hits`):

    t = AshTables()
    ctx = WeaponContext('Claymore', 'Heavy', level=None, stats={'str': 60, 'dex': 12},
                        two_handed=True)                      # level None = max
    sid = t.weapon_skill(t.find_weapon('Claymore'), gem_id)  # or t.find_arts("Lion's Claw")
    hits = skill_hits(t, t.find_weapon('Claymore'), sid, ctx, ctx.level)
    # -> [{'kind': 'melee'|'bullet', 'frame', 'anim', 'atk_row', 'mv': {el: pct},
    #      'flat': {el: value}, 'from_weapon', 'attack': {el: attack before defense},
    #      'poise' (menu units), 'final_rate_id', 'count', ...}]
    total = pvp_damage(t, weapon_id, hits, defender, er_mechanics_defense)

The scored form (ashes-of-war.md sections 13-15): `mountable_skills` per weapon as built,
`skill_pairings` over `corpus_slots(..., 'pvp')` and `skill_choice` for the corpus mix, then
`skill_term`, which returns the weapon score with the best mountable skill's option value (each
skill scored with its own reach and coverage, `skill_reach_factors`), the corpus-weighted value
beside it, and the corpus skills' buff roots for `er-mechanics-buffs.expected_attack`.

`attack` follows `hit_attack` (the verified 0x1406845a0 formula). `pvp_damage` applies the
defense curve, absorption, vsPlayerDmgCorrectRate and the hit's FinalDamageRateParam the way
er-builds-pvp.py does for R1s, and writes `pvp_damage` into each hit. `skill_profile` gives the
full event list (buffs, hyperarmor, i-frames) for anything that is not a hit.
"""

import argparse
import bisect
import collections
import importlib.util
import json
import math
import os
import re
import statistics
import struct
import sys
import types
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import er_corpus  # noqa: E402  the corpus floor every mirror read goes through


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ATK = _load('er_mechanics_attacks', 'er-mechanics-attacks.py')
PR = ATK.PR

CACHE = os.path.expanduser('~/.cache/er-build-planner')

#: Skill TimeAct file = 600 + SwordArtsParam.swordArtsTypeNew (`TAE`: holds for 274 of the 278
#: rows; the picking code is in the behavior script, not traced).
SKILL_TAE_BASE = 600

# Which animation of the skill TimeAct plays depends on the weapon and grip. The one-shot skill
# state `SwordArtsOneShot` (c0000.behbnd) picks one CustomManualSelectorGenerator by the behavior
# variable `SwordArtsOneShotCategory`; every CMSG resolves `a<600 + swordArtsTypeNew>_<animId>`
# (offsetType 0x12, `FUN_14041b160`), so the clip always comes from the skill's own a6xx file
# and only the animId moves. `ExecAttack` (c0000.hks lines 1853-1886, 1.17.1) writes the variable
# from `GetSwordArtsDiffCategory(c_SwordArtsID, env(358), env(225, hand))` (common_define.hks
# lines 888-956), and the follow-up states read `SwordArtsOneShotComboCategory` (line 1901).
#: `common_define.hks` of the installed build (1.17.1 extract; the 2026-07-13 copy has the same
#: `SwordArtsCategory` table and `GetSwordArtsDiffCategory` body).
COMMON_DEFINE_HKS = os.environ.get('ER_COMMON_DEFINE_HKS', os.path.expanduser(
    '~/er-extract/1171-20261008-common-define/action/script/common_define.hks'))
#: diff category -> opening animId (`ExecAttack` lines 1856-1876 give the selector index, the
#: `SwordArtsOneShot CategorySelector` children give the animId; any other category is index 0).
ONE_SHOT_VARIANT = {2: 40200, 61: 40200, 3: 40300, 20: 42000, 53: 42000, 24: 42400, 28: 42800,
                    42: 44200, 55: 44200, 56: 44200, 62: 44200, 47: 44700, 48: 44800,
                    58: 45800, 57: 45900}
#: diff category -> offset of the follow-up from 040010 / 040020 (`SwordArtsOneShotComboCategory`,
#: `ExecAttack` lines 1887-1901, and the `SwordArtsOneShotComboEnd(_2) CategorySelector` children;
#: ComboEnd_2 has no _42 or _59 child, so those fall back to its index-0 child 040020).
COMBO_VARIANT = {2: 200, 3: 300, 24: 2400, 42: 4200, 55: 4200, 62: 4200, 58: 5800, 57: 5900}
COMBO2_VARIANT = {2: 200, 3: 300, 24: 2400, 58: 5800}
#: Constants of `GetSwordArtsDiffCategory` (common_define.hks line 888-947): skill ids that never
#: vary (170 <= id < 255 except 196, id >= 300 except 305/357/399, 272, 275), the one fixed answer
#: (169 -> 45), and the categories it special-cases.
DIFF_FIXED_ZERO = (272, 275)
DIFF_KEEP_HIGH = (305, 357, 399)
WEAPON_CATEGORY_CLAW, WEAPON_CATEGORY_FIST = 22, 42
WEAPON_CATEGORY_LIGHT_LARGE_SWORD, WEAPON_CATEGORY_LARGE_RAPIER = 60, 39
WEAPON_CATEGORY_DUELING_SHIELD = 57
WEAPON_CATEGORY_SMALL_SHIELD, WEAPON_CATEGORY_LARGE_SHIELD = 48, 47
#: Numeric constants the emulation below hard-codes, compared with the bytecode by the selftest.
DIFF_CONSTANTS = {170, 255, 196, 300, 305, 357, 399, 275, 272, 0, 169, 45, 10, 39, 90, 98, 151, 152,
                  225, -1, 2, 4, 3, 5, 8, 9, 1, 9000, 99}
#: TAE event types (WitchyBND template names, handlers per the doc).
EV_JUMP_TABLE, EV_ATTACK, EV_BULLET, EV_COMMON = 0, 1, 2, 5
EV_SPEFFECT = (66, 67, 401)
EV_PC_BEHAVIOR, EV_FP, EV_WA_SPEFFECT, EV_TOUGHNESS = 307, 330, 331, 795
JT_INVINCIBLE = 8          # `_ChrActionFlag` case 8: actionModifiersFlags |= 2 (attacks.md)
#: Event 1 Args+0 (AttackType) 64 marks a parry hitbox: the 307 handler's own error text calls
#: damage type 64 parry (0x14042ab60), and Parry/Buckler Parry/Golden Parry fire event 1 with
#: 64 where attacks carry 0 (`INFERRED` for event 1).
ATTACK_TYPE_PARRY = 64
#: BehaviorParam_PC rows 500/504 (AtkParam 300/304): the generic body hitbox of steps and leaps.
BODY_HITBOX_ROWS = (500, 504)
TAE_FPS = ATK.TAE_FPS
POISE_MENU = 10.0          # internal poise x10 = menu units (attacks.md section 2)
#: A SpEffect id used by more than this many skill TimeActs is an animation-state marker the
#: whole skill system shares (counter frames, stance flags), not a skill's own buff.
SHARED_MARKER_MIN_FILES = 15

AFFINITIES = ['Standard', 'Heavy', 'Keen', 'Quality', 'Fire', 'Flame Art', 'Lightning',
              'Sacred', 'Magic', 'Cold', 'Poison', 'Blood', 'Occult']
ELEMENTS = ('physical', 'magic', 'fire', 'lightning', 'holy')
MV_FIELD = {'physical': 'atkPhysCorrection', 'magic': 'atkMagCorrection',
            'fire': 'atkFireCorrection', 'lightning': 'atkThunCorrection',
            'holy': 'atkDarkCorrection'}
FLAT_FIELD = {'physical': 'atkPhys', 'magic': 'atkMag', 'fire': 'atkFire',
              'lightning': 'atkThun', 'holy': 'atkDark'}

#: `CheckIfWepTypeCanEquipGem` 0x140d2cc70 (`VERIFIED`): EquipParamWeapon.wepType ->
#: the EquipParamGem flag it reads.
WEP_TYPE_MOUNT_FLAG = {
    1: 'Dagger', 3: 'SwordNormal', 5: 'SwordLarge', 7: 'SwordGigantic', 9: 'SaberNormal',
    11: 'SaberLarge', 13: 'katana', 14: 'SwordDoubleEdge', 15: 'SwordPierce', 16: 'RapierHeavy',
    17: 'AxeNormal', 19: 'AxeLarge', 21: 'HammerNormal', 23: 'HammerLarge', 24: 'Flail',
    25: 'SpearNormal', 27: 'SpearLarge', 28: 'SpearHeavy', 29: 'SpearAxe', 31: 'Sickle',
    35: 'Knuckle', 37: 'Claw', 39: 'Whip', 41: 'AxhammerLarge', 50: 'BowSmall', 51: 'BowNormal',
    53: 'BowLarge', 55: 'ClossBow', 56: 'Ballista', 57: 'Staff', 59: 'Sorcery', 61: 'Talisman',
    65: 'ShieldSmall', 67: 'ShieldNormal', 69: 'ShieldLarge', 87: 'Torch', 88: 'HandToHand',
    89: 'PerfumeBottle', 90: 'ThrustingShield', 91: 'ThrowingWeapon', 92: 'ReverseHandSword',
    93: 'LightGreatsword', 94: 'GreatKatana', 95: 'BeastClaw'}

GEM_FIELDS = (['swordArtsParamId', 'defaultWepAttr', 'isSpecialSwordArt', 'rank', 'sortId',
               'spEffectId0', 'spEffectId1', 'spEffectId2']
              + [f'configurableWepAttr{i:02d}' for i in range(24)]
              + [f'canMountWep_{v}' for v in WEP_TYPE_MOUNT_FLAG.values()])
WEAPON_EXTRA = ['swordArtsParamId', 'gemMountType', 'disableGemAttr', 'restrictSpecialSwordArt',
                'wepType', 'isDualBlade', 'atkAttribute', 'atkAttribute2',
                'wepmotionOneHandId', 'wepmotionBothHandId',
                'vsPlayerDmgCorrectRate_Physics', 'vsPlayerDmgCorrectRate_Magic',
                'vsPlayerDmgCorrectRate_Fire', 'vsPlayerDmgCorrectRate_Thunder',
                'vsPlayerDmgCorrectRate_Dark']
VS_PLAYER = {'physical': 'Physics', 'magic': 'Magic', 'fire': 'Fire', 'lightning': 'Thunder',
             'holy': 'Dark'}
BULLET_FIELDS = ['atkId_Bullet', 'HitBulletID', 'intervalCreateBulletId', 'numShoot', 'life',
                 'spEffectId0', 'spEffectId1', 'spEffectId2', 'spEffectId3', 'spEffectId4',
                 'spEffectIDForShooter', 'isPenetrateChr', 'isAttackSFX', 'dmgCalcSide',
                 'intervalCreateTimeMin', 'intervalCreateTimeMax', 'isHitBothTeam',
                 'isUseSharedHitList', 'dmgHitRecordLifeTime', 'shootInterval',
                 'initVellocity', 'maxVellocity', 'minVellocity', 'accelInRange', 'accelOutRange',
                 'accelTime', 'gravityInRange', 'gravityOutRange', 'dist', 'hitRadius',
                 'hitRadiusMax', 'shootAngle', 'shootAngleInterval', 'shootAngleXZ',
                 'shootAngleXInterval', 'EmittePosType', 'launchConditionType', 'FollowType',
                 'isPenetrateMap', 'isInheritSpeedToChild', 'intervalCreateWaitTime',
                 'lockShootLimitAng', 'homingAngle', 'homingBeginDist', 'hormingStopRange',
                 'attachEffectType', 'targetYOffsetRange', 'sfxPostureType', 'isOwnerOverrideInitAngle',
                 'randomCreateRadius', 'shootAngleXMaxRandom', 'shootAngleYMaxRandom', 'lifeRandomRange',
                 'autoSearchNPCThinkID']
#: A safety bound on bullet chains, `er-mechanics-spells.MAX_DEPTH`'s: the deepest Bullet chain is
#: Wave of Gold's 4000, 59 links (`REGULATION`, 2026-11-12), which 32 cut at 33 of its 60 legs.
BULLET_DEPTH_CAP = 64
#: SpEffect fields that describe bookkeeping rather than an effect.
SPEFFECT_BOOKKEEPING_PREFIX = ('effectTarget', 'vowType', 'pad', 'unk', 'reserve', 'vfx',
                               'effectAppear', '_off', 'id', 'icon', 'spCategory',
                               'categoryPriority', 'AppearAi', 'dmypoly', 'saveCategory',
                               'isDisableNetSync', 'isUseStatusAilmentAtkPowerCorrect',
                               'isContractSpEffectLife', 'isWaitModeDelete', 'effectEndurance',
                               'motionInterval', 'invocationConditions', 'isIgnoreNoDamage')
SPEFFECT_LINKS = ('replaceSpEffectId', 'cycleOccurrenceSpEffectId', 'atkOccurrenceSpEffectId')
#: Substrings of SpEffect fields that make a row a buff (or debuff) for `is_buff`.
BUFF_FIELD_KEYS = ('AttackPower', 'AttackRate', 'DamageCutRate', 'DmgCorrectRate', 'Diffence',
                   'defFlickPower', 'toughness', 'saReceiveDamageRate', 'dmgLv_', 'maxHpRate',
                   'maxMpRate', 'maxStaminaRate', 'changeStamina',
                   'staminaRecover', 'regist', 'guardDefFlickPowerRate', 'guardStaminaCutRate',
                   'guardStaminaMult', 'NoGuardDamageRate', 'hpRecoverRate', 'consumeStaminaRate',
                   'artsConsumptionRate', 'regainRate', 'changeStrengthPoint')
#: Status build-up carried by a SpEffect (`<status>AttackPower`): on a weapon buff it is what
#: the weapon inflicts, on a bullet's on-hit row it is what the target receives.
STATUS_BUILDUP = ('poizonAttackPower', 'diseaseAttackPower', 'bloodAttackPower', 'curseAttackPower',
                  'freezeAttackPower', 'sleepAttackPower', 'madnessAttackPower')


def improves(key, value):
    """Whether a SpEffect field value helps its holder. Rates are multipliers: attack-side
    rates help above 1, taken-damage rates (cut rates, `def*DmgCorrectRate`, stamina use, skill
    FP use) help below 1. A row with only unhelpful values is a penalty (Wild Strikes' no-FP 865
    at x0.55 attack, Igon's Drake Hunt's 120410), not a buff."""
    if not isinstance(value, (int, float)):
        return True
    lower_is_better = ('DamageCutRate', 'defPlayerDmgCorrectRate', 'defEnemyDmgCorrectRate',
                       'defObjDmgCorrectRate', 'saReceiveDamageRate', 'consumeStaminaRate',
                       'artsConsumptionRate', 'guardStaminaMult', 'NoGuardDamageRate')
    if any(k in key for k in lower_is_better):
        return value < 1
    if key.endswith('Rate') or 'DmgCorrectRate' in key:
        return value > 1
    return value > 0


def plain(name):
    """The planner writes accents (`Miséricorde`) that the regulation row names drop."""
    return ''.join(c for c in unicodedata.normalize('NFKD', name or '')
                   if not unicodedata.combining(c))


class AshTables:
    """Every param the skill chain reads, decoded once."""

    def __init__(self, regulation=None):
        self.reg = ATK.Regulation(regulation)          # weapon/behavior/atk/reinforce/toughness
        files = PR.load(regulation)

        def table(stem, fields=None):
            rows, _, _ = PR.rows(PR.param_bytes(files, stem), fields)
            return {r['id']: r for r in rows}

        self.gem = table('EquipParamGem', GEM_FIELDS)
        self.arts = table('SwordArtsParam')
        extra = table('EquipParamWeapon', WEAPON_EXTRA)
        for wid, row in self.reg.weapon.items():
            row.update(extra.get(wid, {}))
        self.bullet = table('Bullet', BULLET_FIELDS)
        self.speffect = table('SpEffectParam')
        self.reinforce_gem = table('ReinforceParamWeapon', ['enableGemRank'])
        self.atk_extra = table('AtkParam_Pc', ['atkMag', 'atkFire', 'atkThun', 'atkDark',
                                               'finalDamageRateId', 'overwriteAttackElementCorrectId',
                                               'spEffectId0', 'spEffectId1', 'spEffectId2',
                                               'spEffectId3', 'spEffectId4', 'hitSourceType',
                                               'subCategory1', 'subCategory2', 'knockbackDist',
                                               'spEffectAtkPowerCorrectRate_byPoint', 'opposeTarget'])
        self.final_rate = table('FinalDamageRateParam')
        # Knockback on a player defender (section 15, per-hit landing).
        self.knockback = table('KnockBackParam')
        self.protector_knockback = {i: (r['knockBack'], r['knockbackParamId']) for i, r in
                                    table('EquipParamProtector', ['knockBack', 'knockbackParamId']).items()}
        self.regulation = regulation
        # The tables a `HOMING` record names by its regulation (`homing_spec`), so the record stays
        # plain data a forked worker can hand back.
        _HOMING_TABLES.setdefault(regulation, self)
        self._fa_tables = None
        self.arts_names = PR.row_names('SwordArtsParam')
        self.gem_names = PR.row_names('EquipParamGem')
        self.sp_names = PR.row_names('SpEffectParam')
        self.bullet_names = PR.row_names('Bullet')
        self.behavior_names = PR.row_names('BehaviorParam_PC')
        # Names missing from the Smithbox list (DLC rows) fall back to the ash item's name.
        for gid, g in sorted(self.gem.items()):
            name = self.gem_names.get(gid) or ''
            if name.startswith('Ash of War: ') and not self.arts_names.get(g['swordArtsParamId']):
                self.arts_names[g['swordArtsParamId']] = name[len('Ash of War: '):]
        self._sp_mode = None
        self._marker_ids = None
        self._arts_index = None
        self._weapon_index = None
        self._profiles = {}

    # -- names -------------------------------------------------------------------------------
    def arts_name(self, sid):
        return self.arts_names.get(sid) or f'SwordArts {sid}'

    def find_arts(self, key):
        """A SwordArtsParam id from an id or a (case-insensitive) skill / ash name."""
        if str(key).lstrip('-').isdigit():
            return int(key)
        low = plain(str(key)).lower().removeprefix('ash of war: ')
        if self._arts_index is None:
            self._arts_index = collections.defaultdict(list)
            for i, n in sorted(self.arts_names.items()):
                if n and i in self.arts:
                    self._arts_index[plain(n).lower()].append(i)
        hits = self._arts_index.get(low)
        if not hits:
            raise SystemExit(f'no skill named {key!r}')
        return hits[0]

    def find_weapon(self, key):
        """An EquipParamWeapon id (base, Standard) from an id or name; accents ignored."""
        if str(key).isdigit():
            return self.reg.find_weapon(key)
        low = plain(str(key)).lower()
        if self._weapon_index is None:
            self._weapon_index = collections.defaultdict(list)
            for i, n in sorted(self.reg.weapon_names.items()):
                if n and i in self.reg.weapon and i % 10000 == 0:
                    self._weapon_index[plain(n).lower()].append(i)
        hits = self._weapon_index.get(low)
        if not hits:
            return self.reg.find_weapon(key)
        return hits[0]

    # -- the equipped item -> skill ----------------------------------------------------------
    def weapon_skill(self, weapon_id, gem_id=None):
        """`GetSwordArtsParamIdForWeapon` 0x140675270 (`VERIFIED`): the mounted gem's
        swordArtsParamId when a gem is mounted, else EquipParamWeapon.swordArtsParamId."""
        if gem_id is not None and gem_id in self.gem:
            return self.gem[gem_id]['swordArtsParamId']
        return self.reg.weapon[weapon_id]['swordArtsParamId']

    def active_skill(self, right_skill, left_skill, two_handed_right=True):
        """Which hand's skill L2 fires, `FUN_14047fdb0` (`VERIFIED`): two-handing uses that
        weapon's skill; one-handing uses the left weapon's, unless the left skill row has
        isRefRightArts set (the `No Skill` row 10 does, so seals, torches and bare left hands
        defer to the right weapon). Arguments are SwordArtsParam ids (None = nothing held)."""
        if two_handed_right or left_skill is None:
            return right_skill
        row = self.arts.get(left_skill)
        if row is None or row['isRefRightArts']:
            return right_skill
        return left_skill

    def fp_cost(self, sword_arts_id, cast='L2', arts_consumption_rate=1.0):
        """`CalculateFpConsumption` 0x14068c520 (`VERIFIED`): ceil(rate x useMagicPoint_<cast>),
        rate = product of active SpEffectParam.artsConsumptionRate. The behavior script picks
        `cast` (R1, R2, L1, L2). -1 in the param means that input does not cast the skill."""
        base = self.arts[sword_arts_id][f'useMagicPoint_{cast}']
        return base if base <= 0 else math.ceil(arts_consumption_rate * base)

    @staticmethod
    def has_enough_fp(fp, cost):
        """`CanCastAow` 0x140480060 / `HaveEnoughFP` 0x1404802a0 (`VERIFIED`; it reads the 0.5 at
        0x1432a3be0): the full skill plays when fp > 0 and
        int(cost * 0.5) <= fp. Event 330 then spends min(cost, fp)
        (FP is clamped at 0), so between half and full cost the skill is full and FP ends at 0."""
        return fp > 0 and int(cost * 0.5) <= fp

    def can_mount(self, weapon_id, gem_id, affinity=0, level=0):
        """`CanMountGemWithAffinityOnWeapon` 0x140d57e70 (`VERIFIED`). Returns (ok, reason).

        Not modelled: `_CanMountGem` 0x14078b720 first requires the whetblade event flag
        for `affinity`; without it only defaultWepAttr is offered."""
        w = self.reg.weapon.get(weapon_id)
        g = self.gem.get(gem_id)
        if w is None or g is None:
            return False, 'no such row'
        if w['gemMountType'] not in (1, 2):
            return False, f"gemMountType {w['gemMountType']} (only 1 and 2 accept gems)"
        flag = WEP_TYPE_MOUNT_FLAG.get(w['wepType'])
        if flag is None or not g[f'canMountWep_{flag}']:
            return False, f"canMountWep_{flag or w['wepType']} is 0"
        disable = w['disableGemAttr'] & 1
        if not (disable and affinity == 0):
            configurable = 0 <= affinity < 24 and g[f'configurableWepAttr{affinity:02d}']
            if not (not disable and configurable):
                if disable or g['defaultWepAttr'] != affinity:
                    return False, f'affinity {affinity} not configurable and not defaultWepAttr'
        rank_row = self.reinforce_gem.get(w['reinforceTypeId'] + level, {})
        if g['rank'] > rank_row.get('enableGemRank', 0):
            return False, f"gem rank {g['rank']} > enableGemRank {rank_row.get('enableGemRank', 0)}"
        if w['restrictSpecialSwordArt'] & 1 and g['isSpecialSwordArt'] & 1:
            return False, 'restrictSpecialSwordArt blocks isSpecialSwordArt gem'
        return True, 'ok'

    def ash_gems(self):
        """{swordArtsParamId: gem id} for the ash-of-war items a player can hold.

        Several rows name the same ash; the low ids (40-191, 1000) carry sortId 999999 and
        different mount flags (row 40 allows daggers for Lion's Claw, row 10000 does not). The
        rows with a real sortId are the inventory items (`INFERRED` from the sort order; the
        item-drop tables were not read)."""
        out = {}
        for gid, g in sorted(self.gem.items(), key=lambda kv: (kv[1]['sortId'] == 999999, kv[0])):
            if (self.gem_names.get(gid) or '').startswith('Ash of War: '):
                out.setdefault(g['swordArtsParamId'], gid)
        return out

    # -- SpEffects ---------------------------------------------------------------------------
    def _mode(self):
        if self._sp_mode is None:
            counts = collections.defaultdict(collections.Counter)
            for r in self.speffect.values():
                for k, v in r.items():
                    counts[k][v] += 1
            self._sp_mode = {k: c.most_common(1)[0][0] for k, c in counts.items()}
        return self._sp_mode

    def speffect_summary(self, sid, depth=0, seen=None):
        """The fields of a SpEffect row that differ from the most common value of that field
        across all rows, minus bookkeeping, plus its duration and linked rows."""
        seen = set() if seen is None else seen
        r = self.speffect.get(sid)
        if r is None or sid in seen:
            return None
        seen.add(sid)
        mode = self._mode()
        effects = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()
                   if v != mode.get(k) and not k.startswith(SPEFFECT_BOOKKEEPING_PREFIX)
                   and k not in SPEFFECT_LINKS}
        out = {'id': sid, 'name': self.sp_names.get(sid), 'duration_s': round(r['effectEndurance'], 3),
               # effectTargetSelf 0: the row cannot land on its caster, so on a bullet it is an
               # effect on whoever is hit (frostbite, bleed), not a buff (`INFERRED` from the
               # field name and its use: 0 on every frost/bleed row checked, 1 on Golden Vow).
               'can_target_self': bool(r.get('effectTargetSelf')),
               'contract_life': r.get('isContractSpEffectLife'), 'state_info': r.get('stateInfo'),
               'effects': effects, 'links': {}}
        if depth < 3:
            for link in SPEFFECT_LINKS:
                child = r.get(link, -1)
                if child and child > 0 and child != sid:
                    s = self.speffect_summary(child, depth + 1, seen)
                    if s:
                        out['links'][link] = s
        return out

    def marker_ids(self):
        """SpEffect ids shared by many skill TimeActs (see SHARED_MARKER_MIN_FILES)."""
        if self._marker_ids is None:
            files = collections.Counter()
            for sid in self.arts:
                ids = set()
                for events in (skill_tae(self, sid) or {}).values():
                    for e in events:
                        if e.type in EV_SPEFFECT:
                            ids.add(struct.unpack_from('<i', e.params, 0)[0])
                files.update(ids)
            self._marker_ids = {i for i, n in files.items() if n > SHARED_MARKER_MIN_FILES}
        return self._marker_ids

    def effect_kind(self, sid):
        """`buff`: changes a fight stat and outlasts the animation (effectEndurance above 1 s,
        -1, or a linked row that does). `stance effect`: changes a fight stat only while the
        TimeAct event keeps it alive (Stamp's 6340 half damage, Wild Strikes' 6362 reaction
        cap). `marker`: neither."""
        if not self.is_buff(sid):
            return 'marker'
        s = self.speffect_summary(sid)

        def lasts(summary):
            d = summary['duration_s']
            return d < 0 or d > 1.0 or any(lasts(c) for c in summary['links'].values())
        return 'buff' if lasts(s) else 'stance effect'

    def is_buff(self, sid):
        """A SpEffect that changes a stat a fight reads (damage dealt or taken, poise, stagger
        level, HP/FP/stamina), directly or through a linked row. Markers and animation flags
        (for example 147 `atkFlickPower`, 45 counter-hit frames) are not buffs."""
        if sid in self.marker_ids():
            return False
        s = self.speffect_summary(sid)

        def touches(summary):
            if not summary:
                return False
            eff = summary['effects']
            # Status build-up is a buff only on the caster's weapon (wepParamChange 1 or 2:
            # Chilling Mist's 826 frost); elsewhere it is what the receiver suffers (Seppuku's
            # 1753 bleeds its own caster).
            weapon_row = eff.get('wepParamChange') in (1, 2)
            if any(any(k in key for k in BUFF_FIELD_KEYS) and improves(key, v) for key, v in eff.items()
                   if key not in STATUS_BUILDUP or weapon_row):
                return True
            # changeHp/Mp Rate/Point: positive drains (Bloody Slash's 1763 HP cost, Carian
            # Retaliation's 1515 FP charge), negative restores (Holy Ground 1641).
            if any(eff.get(k, 0) < 0 for k in ('changeHpRate', 'changeHpPoint', 'changeMpRate', 'changeMpPoint')):
                return True
            # atkOccurrenceSpEffectId is applied to whoever the buffed weapon hits, so it says
            # nothing about the caster.
            return any(touches(c) for link, c in summary['links'].items()
                       if link != 'atkOccurrenceSpEffectId')
        return touches(s)

    def harms_target(self, sid):
        """A row that hurts whoever receives it: status build-up, HP drain, or damage cut rates
        above 1 (Hoarfrost's 1800: frost 70, cuts x1.2 = takes 20% more)."""
        s = self.speffect_summary(sid)
        if not s:
            return False
        eff = s['effects']
        return (any(k in eff for k in STATUS_BUILDUP) or eff.get('changeHpRate', 0) > 0
                or eff.get('changeHpPoint', 0) > 0
                or any('DamageCutRate' in k and v > 1 for k, v in eff.items()))


# ---------------------------------------------------------------------------------------------
# TimeAct

_SKILL_TAE = {}


def skill_tae(t, sword_arts_id):
    """{anim id: [TaeEvent]} of `a<600 + swordArtsTypeNew>.tae`, or None. An entry that imports
    another animation (`ImportOtherAnim`) and has no attack event of its own carries the source's
    events too (`ATK.resolve_events`): Stormcaller's twinblade opening a623_042400 holds no events
    and imports a623_040000."""
    row = t.arts.get(sword_arts_id)
    if row is None:
        return None
    cat = SKILL_TAE_BASE + row['swordArtsTypeNew']
    if cat not in _SKILL_TAE:
        raw = ATK.tae_animations(cat)
        _SKILL_TAE[cat] = None if raw is None else {a: ATK.resolve_events(cat, a)[2] or [] for a in raw}
    return _SKILL_TAE[cat]


_ARTS_CATEGORY = {}


def sword_arts_categories(path=COMMON_DEFINE_HKS):
    """{swordArtsTypeNew: [diff categories]}, the `SwordArtsCategory` table of common_define.hks,
    read by executing its main chunk symbolically (`er-hks-weapon-category-table`). Empty when the
    file is absent, which leaves every skill on its index-0 animations."""
    if path not in _ARTS_CATEGORY:
        table = None
        if os.path.exists(path):
            wct = _load('er_hks_weapon_category_table', 'er-hks-weapon-category-table.py')
            code, consts = wct.parse_main_function(open(path, 'rb').read())
            table = wct.run_main_chunk(code, consts).get('SwordArtsCategory')
        _ARTS_CATEGORY[path] = ({int(k): [int(c) for c in v.as_list()] for k, v in table.array.items()}
                                if table is not None else {})
    return _ARTS_CATEGORY[path]


def is_two_handed(grip):
    """The grip spellings callers use ('one'/'both', '1H'/'2H', a bool) -> two-handed or not."""
    return grip in (True, 'both', 'two', '2H', '2h')


def diff_category(arts_type, idle_category, weapon_category, categories, one_handed=True):
    """`GetSwordArtsDiffCategory(c_SwordArtsID, env(358), env(225, c_SwordArtsHand))`
    (common_define.hks lines 888-956) for a skill used from the hand that holds the weapon.

    `env(225, hand)` is that hand's `wepmotionCategory` and `env(358)` the idle category: the
    right weapon's `wepmotionOneHandId` while one-handing, the two-handed weapon's
    `wepmotionBothHandId` otherwise (`VERIFIED`: both are `PlayerIns` vtable +0x248
    `0x140657bb0`, reading the int[3] that `0x1404d1d60` fills from
    EquipParamWeapon +0xe7 / +0xf0 / +0xf1 by ChrAsm arm style). The left-hand shield branch
    (`c_SwordArtsHand == HAND_LEFT` while one-handing, shield skills cast from the off hand) is not
    modelled."""
    a = arts_type
    if (170 <= a < 255 and a != 196) or (a >= 300 and a not in DIFF_KEEP_HIGH) or a in DIFF_FIXED_ZERO:
        return 0
    if a == 169:
        return 45
    if weapon_category == WEAPON_CATEGORY_LIGHT_LARGE_SWORD:
        return 0
    alt = WEAPON_CATEGORY_FIST if weapon_category == WEAPON_CATEGORY_CLAW else weapon_category
    idle = idle_category - 10 if idle_category >= 10 else idle_category
    if weapon_category == WEAPON_CATEGORY_LARGE_RAPIER:
        idle = 0
    if a in (90, 98, 151, 152) and weapon_category == WEAPON_CATEGORY_DUELING_SHIELD and one_handed:
        return 0
    row = categories.get(a, [])
    if weapon_category in row:
        return weapon_category
    if alt in row:
        return alt
    if 90 <= a <= 99:
        return 0
    return idle if idle in row else 0


def weapon_diff_category(t, sword_arts_id, weapon_id, grip='one'):
    """`diff_category` for this skill on this weapon held in `grip`."""
    w = t.reg.weapon[weapon_id]
    two = is_two_handed(grip)
    idle = w.get('wepmotionBothHandId' if two else 'wepmotionOneHandId', 0) or 0
    return diff_category(t.arts[sword_arts_id]['swordArtsTypeNew'], idle, w['wepmotionCategory'],
                         sword_arts_categories(), one_handed=not two)


def _frames(e):
    end = e.end if e.end < 1e6 else None
    return (round(e.start * TAE_FPS), round(end * TAE_FPS) if end is not None else None)


def resolve_judge(t, weapon_id, judge, level=0, literal=False, via_bullet=False, event_flags=1):
    """What a behavior judge fires for this weapon: melee, bullet (with its tree) or SpEffect.

    `literal`: the judge is a BehaviorParam_PC row id as is (event 5 CommonBehavior and 307 with
    flag 4, `VERIFIED` 0x140426fc0 / 0x140653770). `via_bullet`: the event is 2 BulletBehavior,
    the only path that honours refType 1 and 2 (`VERIFIED`: events 1/5/307 hand a refType other
    than 0 to `FUN_140442ee0`, which finds no AtkParam and creates nothing)."""
    w = t.reg.weapon[weapon_id]
    bid = judge if literal else t.reg.resolve_behavior_id(judge, w['behaviorVariationId'])
    b = t.reg.behavior.get(bid)
    if b is None:
        return {'judge': judge, 'behavior_row': bid, 'kind': 'unresolved'}
    base = {'judge': judge, 'behavior_row': bid, 'behavior_name': t.behavior_names.get(bid),
            'stamina_cost': int(b['stamina'] * w['staminaConsumptionRate']),
            'behavior_category': b['category']}
    if b['refType'] == 0:
        nums = ATK.attack_numbers(t.reg, weapon_id, judge, level)
        extra = t.atk_extra.get(b['refId'], {})
        a = t.reg.atk.get(b['refId'], {})
        flat = {el: (a.get('atkPhys', 0) if el == 'physical' else extra.get(FLAT_FIELD[el], 0))
                for el in ELEMENTS}
        mv = {el: a.get(MV_FIELD[el], 0) for el in ELEMENTS}
        # `0x1406912a0`: attackNotFromWeapon = (flags & 0x68) == 0 && hitSourceType != 0.
        from_weapon = bool(event_flags & 0x68) or not extra.get('hitSourceType', 0)
        # The not-from-weapon branch reads only the flat fields: MV 100 on such a row (Raptor of
        # the Mists' 550, the body rows 300/304) multiplies nothing.
        damaging = (any(mv.values()) or (a.get('isAddBaseAtk') and any(flat.values()))) if from_weapon \
            else any(flat.values())
        kind = 'melee' if damaging else 'no-damage hitbox'
        if literal and bid in BODY_HITBOX_ROWS:
            # 307 flag 4 on rows 500/504 (AtkParam 300: flat 30 physical; 304: nothing, both
            # hitSourceType 1): the generic body hitbox the step and leap skills carry, the same
            # rows for every weapon. Not a skill hit.
            kind = 'body hitbox'
        return {**base, 'kind': kind, 'atk_row': b['refId'], 'atk_name': t.reg.atk_names.get(b['refId']),
                'mv': mv, 'flat': flat, 'add_base_atk': bool(a.get('isAddBaseAtk')),
                'from_weapon': from_weapon, 'disable_2h': bool(a.get('isDisableBothHandsAtkBonus')),
                'poise': nums['poise_damage'] if nums else None,
                'poise_flat': a.get('atkSuperArmor', 0.0),
                'final_rate_id': extra.get('finalDamageRateId', -1),
                'element_correct_override': extra.get('overwriteAttackElementCorrectId', -1),
                'on_hit_speffects': [s for s in (extra.get(f'spEffectId{i}') for i in range(5)) if s and s > 0]}
    if not via_bullet:
        return {**base, 'kind': f"refType {b['refType']} outside a bullet event (creates nothing)"}
    if b['refType'] == 1:
        return {**base, 'kind': 'bullet', 'bullet': bullet_tree(t, b['refId'])}
    if b['refType'] == 2:
        # Applied to the attacker itself (`VERIFIED` 0x1403c1030 -> FUN_1403e8d50).
        sid = b['refId']
        return {**base, 'kind': 'speffect', 'speffect': t.speffect_summary(sid), 'effect': t.effect_kind(sid)}
    return {**base, 'kind': f"refType {b['refType']}"}


def bullet_tree(t, bid, depth=0, path=()):
    """A Bullet row, its AtkParam, the SpEffects it applies on hit, and child bullets.

    Followed to any depth (Phantom Slash's damaging 2663 is five links down). A row two parents
    name hangs under both, as the game makes it under both: Stars of Ruin's 40 m/s legs
    10422032-10422035 are each the `HitBulletID` of two stars (bd er-effects-rs-vkuf; one `seen`
    set for the whole walk used to give them to the first star only, 32 shots flown, not 36). A
    row is not followed again below itself (`path`): no Bullet row reaches itself through
    another, and the two that name themselves (20002202, 200002827) stop at the
    `child != bid` test below (`REGULATION`, all 15,475 rows scanned 2026-11-12). The tree
    is a tree: a shared row is two nodes, so a walk over it meets the row once per parent."""
    r = t.bullet.get(bid)
    if r is None or bid in path or depth > BULLET_DEPTH_CAP:
        return None
    path += (bid,)
    a = t.reg.atk.get(r['atkId_Bullet'], {})
    extra = t.atk_extra.get(r['atkId_Bullet'], {})
    out = {'bullet': bid, 'name': t.bullet_names.get(bid), 'atk_row': r['atkId_Bullet'],
           'atk_name': t.reg.atk_names.get(r['atkId_Bullet']),
           'mv': {el: a.get(MV_FIELD[el], 0) for el in ELEMENTS},
           'flat': {el: (a.get('atkPhys', 0) if el == 'physical' else extra.get(FLAT_FIELD[el], 0))
                    for el in ELEMENTS},
           'add_base_atk': bool(a.get('isAddBaseAtk')),
           'poise_correction': a.get('atkSuperArmorCorrection'), 'poise_flat': a.get('atkSuperArmor'),
           'final_rate_id': extra.get('finalDamageRateId', -1),
           'element_correct_override': extra.get('overwriteAttackElementCorrectId', -1),
           'disable_2h': bool(a.get('isDisableBothHandsAtkBonus')),
           'atk_attribute': a.get('atkAttribute'),
           'num_shoot': r['numShoot'], 'life_s': round(r['life'], 3),
           'shared_hit_list': bool(r['isUseSharedHitList']),
           'attach_effect': r.get('attachEffectType', 0) or 0,
           'hit_record_s': round(r['dmgHitRecordLifeTime'], 3),
           'penetrates': bool(r['isPenetrateChr']), 'sticks_in_target': bool(r['isAttackSFX']),
           'on_hit_speffects': [r[f'spEffectId{i}'] for i in range(5) if r[f'spEffectId{i}'] > 0],
           'shooter_speffect': r['spEffectIDForShooter'] if r['spEffectIDForShooter'] > 0 else None,
           'radius_m': round(max(r['hitRadius'], r['hitRadiusMax']), 3),
           'fan_deg': max(0, r['numShoot'] - 1) * abs(r['shootAngleInterval']),
           'oppose': bool(extra.get('opposeTarget')),
           'children': {}}
    for link in ('HitBulletID', 'intervalCreateBulletId'):
        child = r[link]
        if child and child > 0 and child != bid:
            c = bullet_tree(t, child, depth + 1, path)
            if c:
                out['children'][link] = c
    return out


def anim_actions(t, weapon_id, events, level=0):
    """The events of one skill animation that do something, in frame order. An attack or bullet
    event carries the hit priority record it takes (`record`, an index into the animation's TAE
    341 records, or None; `ATK.event_record`), its `priority` byte and `record_s`, the clip second
    it first holds the record (every player TAE has content version 0x25, `ATK` selftest)."""
    out = []
    records = ATK.priority_records(events)
    for e in sorted(events, key=lambda e: e.start):
        p = e.params
        fr = _frames(e)
        if e.type in (EV_ATTACK, EV_BULLET):
            rec, prio = ATK.event_record(e, records)
            tag = {'record': rec, 'priority': prio, 'record_s': ATK.record_attach_time(e, records, rec)}
        if e.type == EV_ATTACK:
            res = resolve_judge(t, weapon_id, struct.unpack_from('<i', p, 8)[0], level)
            if struct.unpack_from('<i', p, 0)[0] == ATTACK_TYPE_PARRY:
                res['kind'] = 'parry'
            # The attack index (type-1 Args[1]) keys the hit list (`ATK.hit_records`).
            out.append({'event': 'attack', 'frames': fr, 'fr_s': (e.start, e.end),
                        'attack_index': struct.unpack_from('<i', p, ATK.ATTACK_INDEX_OFFSET)[0], **tag, **res})
        elif e.type == EV_BULLET:
            # +0 the DummyPoly the root shot leaves from, +0xc its orientation byte
            # (`bullet_launch`; `BulletBehavior` 0x140426e60), +8 the judge.
            out.append({'event': 'bullet', 'frames': fr, 'fr_s': (e.start, e.end),
                        'dummy': struct.unpack_from('<i', p, 0)[0], 'orient': p[12] if len(p) > 12 else 0, **tag,
                        **resolve_judge(t, weapon_id, struct.unpack_from('<i', p, 8)[0], level,
                                        via_bullet=True)})
        elif e.type == EV_COMMON:
            out.append({'event': 'common', 'frames': fr,
                        **resolve_judge(t, weapon_id, struct.unpack_from('<i', p, 4)[0], level,
                                        literal=True)})
        elif e.type == EV_PC_BEHAVIOR:
            # +4 u32 flags, +8 judge (0x14042ab60). Flag 8 resolves like event 1; flag 4 reads
            # BehaviorParam_PC[judge] as is (judge - 100 while a roll-trigger state is set).
            flags, judge = struct.unpack_from('<Ii', p, 4)
            res = resolve_judge(t, weapon_id, judge, level, literal=not flags & 8, event_flags=flags)
            out.append({'event': f'pc-behavior {flags}', 'frames': fr, **res})
        elif e.type == EV_FP:
            # `CSChrSwordArtsModule::ConsumeFp` 0x14047fc80 spends the cost reserved when the
            # behavior script started the skill, once per reservation (`VERIFIED`).
            out.append({'event': 'fp', 'frames': fr, 'kind': 'fp'})
        elif e.type == EV_WA_SPEFFECT:
            # Classified on the enough-FP arm only: the other arm is what a short cast gets
            # (Wild Strikes' 865 is a -45% attack penalty, not a buff).
            enough, short = struct.unpack_from('<ii', p, 0)
            out.append({'event': 'skill speffect', 'frames': fr, 'kind': 'speffect',
                        'speffect': t.speffect_summary(enough) if enough > 0 else None,
                        'speffect_no_fp': t.speffect_summary(short) if short > 0 else None,
                        'effect': t.effect_kind(enough) if enough > 0 else 'none'})
        elif e.type in EV_SPEFFECT:
            sid = struct.unpack_from('<i', p, 0)[0]
            if sid > 0 and sid not in t.marker_ids():
                out.append({'event': f'speffect {e.type}', 'frames': fr, 'kind': 'speffect',
                            'speffect': t.speffect_summary(sid), 'effect': t.effect_kind(sid)})
        elif e.type == EV_TOUGHNESS:
            row_id, source = p[0], p[1]
            tr = t.reg.toughness.get(row_id, {})
            w = t.reg.weapon[weapon_id]
            bonus = (ATK.TOUGHNESS_SCALE * tr.get('correctionRate', 0.0) * w['toughnessCorrectRate']
                     if source in (1, 2) else 0.0)
            # Args byte 1: 1/2 add the hand weapon's toughnessCorrectRate term, 3 the item's
            # refVirtualWepId (not modelled), anything else no bonus: the window then only
            # refills poise to the row's floor and applies its PvP rates (attacks.md section 2).
            out.append({'event': 'hyperarmor', 'frames': fr, 'kind': 'hyperarmor',
                        'toughness_row': row_id, 'weapon_term': source,
                        'poise_bonus_menu': round(bonus * POISE_MENU, 2),
                        'pvp_poise_damage_taken': round(tr.get('unk1', 1.0), 3),
                        'pvp_hp_damage_taken': round(tr.get('unk2', 1.0), 3)})
        elif e.type == EV_JUMP_TABLE and struct.unpack_from('<i', p, 0)[0] == JT_INVINCIBLE:
            out.append({'event': 'invincible', 'frames': fr, 'kind': 'iframes'})
    return out


def skill_profile(t, sword_arts_id, weapon_id, level=0, grip='one', stance=None):
    """Everything the skill does on this weapon, per animation, plus a classification and the
    animation this weapon and grip open with (`opening`, read by `main_anim`). Cached on the
    tables per (skill, weapon, level, grip, stance); callers treat the result as read-only.

    `stance` ('r1' / 'r2') picks which attack a stance skill's entry hands over to
    (`stance_buttons`); None is the attack its first FP-charging clip is, and a button the skill
    does not offer is ignored."""
    two = is_two_handed(grip)
    key = (sword_arts_id, weapon_id, level, two, stance)
    if key not in t._profiles:
        base_key = (sword_arts_id, weapon_id, level)
        if base_key not in t._profiles:
            t._profiles[base_key] = _skill_profile(t, sword_arts_id, weapon_id, level)
        base = t._profiles[base_key]
        t._profiles[key] = {**base, **_opening(t, base, sword_arts_id, weapon_id, two, stance)}
    return t._profiles[key]


#: The stance states' fixed clips (`BEH`, docs/er-mechanics/chainsaw/affected-class.md):
#: `W_DrawStanceRightStart` plays 040050; from its loop or itself, `W_DrawStanceRightAttackLight`
#: (R1) plays 040060 and `W_DrawStanceRightAttackHeavy` (R2) 040070 (`ArtsStanceCommonFunction`,
#: HKS line 11547). The skill is in the stance from the L2 press and hands over on the button's
#: press, which the TimeAct's cancel windows on 040050 admit (`anim_recovery`).
STANCE_START = 40050
STANCE_ATTACK = {'r1': 40060, 'r2': 40070}


def stance_buttons(prof):
    """[(button, attack clip)] a stance skill's entry hands over to: `STANCE_START` is in its
    TimeAct and neither it nor its loop (`STANCE_START + 1`, when present) hits or charges FP,
    and each `STANCE_ATTACK` clip present, not a without-FP copy, charges FP or hits. Empty for
    any other skill. Unsheathe: [('r1', 40060), ('r2', 40070)]; a bow skill's single 040060
    shot: [('r1', 40060)]. Wild Strikes is left out: its loop 040051 is the attack (an attack
    stance, `lead_in`), and its 040060 / 040070 finish that loop."""
    an = prof['anims']
    entry = an.get(STANCE_START)
    if entry is None or any(x['kind'] in ('melee', 'bullet', 'fp')
                            for x in entry + an.get(STANCE_START + 1, [])):
        return []
    return [(b, a) for b, a in STANCE_ATTACK.items()
            if an.get(a) and a not in prof['no_fp_anims']
            and any(x['kind'] in ('melee', 'bullet', 'fp') for x in an[a])]


def _opening(t, prof, sword_arts_id, weapon_id, two, stance=None):
    """The opening and follow-up animation ids for this weapon and grip.

    The index-0 opening is the first animation that charges FP. When that is 040000 the skill
    enters `SwordArtsOneShot`, which plays `ONE_SHOT_VARIANT[diff category]` instead
    (Spinning Slash: 040000 on a straight sword, 040200 on a greatsword, 040300 on a halberd,
    042400 on a twinblade). A variant the skill TimeAct lacks is reported and the index-0
    animation kept: the graph would fall back to `a000_<id>` or another skill's clip there.

    A stance skill (`stance_buttons`) is cast L2 into `STANCE_START` and then a button:
    `stance_entry` and `stance_button` say which, `opening` is that button's attack clip and
    `stance_buttons` lists every button the stance offers. The default button is the one whose
    attack is the index-0 opening, else R1: Moon-and-Fire Stance's lowest FP clip is its
    follow-up 040010 (`SwordArtsOneShotComboEnd`, reached from 040060 by R1 in f35-50), which the
    index-0 rule took as its opening."""
    generic = prof['fp_anims'][0] if prof['fp_anims'] else next(iter(prof['anims']), None)
    cat = weapon_diff_category(t, sword_arts_id, weapon_id, two)
    out = {'grip': 'both' if two else 'one', 'diff_category': cat, 'generic_opening': generic,
           'opening': generic, 'one_shot': generic == 40000, 'variant_missing': None,
           'stance_entry': None, 'stance_button': None, 'stance_buttons': []}
    if generic == 40000 and cat in ONE_SHOT_VARIANT:
        want = ONE_SHOT_VARIANT[cat]
        if want in prof['anims']:
            out['opening'] = want
        else:
            out['variant_missing'] = want
    offered = dict(stance_buttons(prof))
    if offered:
        button = stance if stance in offered else next((b for b, a in offered.items() if a == generic),
                                                         next(iter(offered)))
        out.update(opening=offered[button], stance_entry=STANCE_START, stance_button=button,
                   stance_buttons=list(offered))
    return out


def stance_label(prof):
    """'stance R2 040070' for a stance cast (`_opening`), else None: the variant name a sweep row
    and a skill option carry."""
    b = prof.get('stance_button')
    return f"stance {b.upper()} {prof['opening']:06d}" if b else None


def stance_row(prof):
    """{'variant', 'stance'} a sweep row of a stance cast carries, {} for any other cast. Code
    that re-reads a row's cast passes `row.get('stance')` back to `skill_profile`, and caches keyed
    on a row add `variant_key`."""
    return {'variant': stance_label(prof), 'stance': prof['stance_button']} if prof.get('stance_button') else {}


def variant_key(x):
    """'|stance r2' for a row or option cast through a stance button, '' otherwise: the cache-key
    suffix that keeps two variants of one (weapon, affinity, ash, grip) apart."""
    return f"|stance {x['stance']}" if x.get('stance') else ''


def stance_fp(t, sword_arts_id, button):
    """FP of a stance cast that hands over on `button`: the L2 column (0 or -1 on these skills)
    plus the button's (Unsheathe R1 10, R2 15; SwordArtsParam `useMagicPoint_*`)."""
    row = t.arts[sword_arts_id]
    return max(0, row['useMagicPoint_L2']) + max(0, row[f'useMagicPoint_{button.upper()}'])


def stance_windup(t, sword_arts_id, prof):
    """Real frames from the L2 press to the stance attack's start: `STANCE_START`'s earliest
    cancel for the button that plays it (`anim_recovery`), else its clip length. 0 when the
    profile is not a stance cast. Unsheathe on an Uchigatana: R1 at f8, R2 at f6."""
    if prof.get('stance_entry') is None:
        return 0.0
    rec = anim_recovery(t, prof['weapon_id'], sword_arts_id, prof['stance_entry'])
    if rec.get(prof['stance_button']) is not None:
        return float(rec[prof['stance_button']])
    clip = ATK.clip_length(SKILL_TAE_BASE + t.arts[sword_arts_id]['swordArtsTypeNew'], prof['stance_entry'])
    return float(ATK.real_frame(clip[0])) if clip else 0.0


def lead_in(t, sword_arts_id, prof):
    """Real frames the cast plays before `main_anim`, 0 for nearly every skill.

    A held skill whose FP-charging opening is its loop clip (`...1`) starts on the uncharged clip
    before it (`...0`) and enters the loop when that clip ends: c0000.hks
    `SwordArtsOneShot_onUpdate` fires `W_SwordArtsLoopLoop` on `env(IsAnimEnd, 0)` while L2 is
    held. Wild Strikes (110) is the only one of the 278 skills with that shape: a610_040050,
    32 frames with no hit and its first R1 cancel at f19, then the loop a610_040051 with hits at
    f5 and f24 whose R1 and roll cancels open at f0. Scored from the loop alone, the cast hit at f5
    and cost nothing before it (2026-10-12). That the loop starts at the clip's end and not at its
    f19 flag is the HKS reading, not a measurement.

    A stance cast (`stance_windup`) plays `STANCE_START` first and leaves it on the press of the
    button whose attack is `main_anim`, at that button's earliest cancel: Unsheathe's 040050
    hands over to the R1 slash 040060 at f8 and the R2 slash 040070 at f6."""
    if prof.get('stance_entry') is not None:
        return stance_windup(t, sword_arts_id, prof)
    op = main_anim(prof)
    anims = {int(a) for a in prof['anims']}
    if op is None or op % 10 != 1 or op - 1 not in anims or op - 1 in {int(a) for a in prof['fp_anims']}:
        return 0.0
    D = sys.modules.get('er_mechanics_disengage')
    if D is None:
        D = sys.modules['er_mechanics_disengage'] = _load('er_mechanics_disengage', 'er-mechanics-disengage.py')
    c = D.clip(SKILL_TAE_BASE + t.arts[sword_arts_id]['swordArtsTypeNew'], op - 1)
    return float(c['length']) if c and c.get('length') else 0.0


def untimed_hits(prof, hits):
    """Whether every hit of the cast is in a clip other than `main_anim` while the opening itself
    hits nothing: the hits' frames are then from the start of a clip the cast reaches only later,
    at a time the clips do not give. Erdtree Slam on the Rotten Staff (skill 1193): the jump
    a793_040000 (149 frames, no hit), a fall loop 040003, and the landing 040004 with every hit at
    its f0-f1, which a sweep counting frames from the cast scored at 50780 damage per second."""
    op = main_anim(prof)
    return bool(hits) and op is not None and all(h.get('anim') not in (None, op) for h in hits)


def lead_in_hyperarmor(prof):
    """The hyperarmor events of the clip `lead_in` counts, in cast frames (Wild Strikes'
    a610_040050: f10 to f35, running into the loop's own f0 to f41; a stance cast's
    `STANCE_START`)."""
    op = main_anim(prof)
    an = prof['anims']
    pre = prof.get('stance_entry') or (op - 1 if op else None)
    return [e for e in an.get(pre, an.get(str(pre), [])) if e['kind'] == 'hyperarmor'] if pre else []


def lead_in_ha_start(prof, start):
    """`start`, or the first hyperarmor frame of the lead-in clip when that is earlier."""
    return min([start] + [e['frames'][0] for e in lead_in_hyperarmor(prof)])


def followup_anim(prof, step):
    """The animation a follow-up press plays: `opening + step`, except after a one-shot opening,
    where `SwordArtsOneShotComboCategory` picks 040010 / 040020 plus the category's offset. A stance
    attack's follow-up window opens the same `SwordArtsOneShotComboEnd` states (Moon-and-Fire
    Stance 040060: 100054 f35-50 -> 040010), read the same way (`INFERRED`: the category a stance
    leaves is not traced; its only carrier is one weapon class)."""
    if not prof.get('one_shot') and not prof.get('stance_button'):
        return prof['opening'] + step
    table = COMBO_VARIANT if step == 10 else COMBO2_VARIANT
    return 40000 + step + table.get(prof.get('diff_category'), 0)


def _skill_profile(t, sword_arts_id, weapon_id, level=0):
    arts = t.arts.get(sword_arts_id)
    if arts is None:
        raise SystemExit(f'SwordArtsParam has no row {sword_arts_id}')
    anims = skill_tae(t, sword_arts_id) or {}
    per_anim = {a: anim_actions(t, weapon_id, anims[a], level) for a in sorted(anims)}
    fp_anims = [a for a, acts in per_anim.items() if any(x['kind'] == 'fp' for x in acts)]
    no_fp = no_fp_twins(per_anim)
    classes = set()
    # Classified on what the skill does when it is paid for. An animation whose id ends in 5-9
    # and has a sibling 5 lower is that sibling's "without FP" copy (it drops event 330 and fires
    # the weaker `No FP` rows); those are left out. Follow-up animations (Ground Slam's landing,
    # Wild Strikes' later swings) are kept even though some do not charge FP themselves.
    for anim, acts in per_anim.items():
        if anim in no_fp:
            continue
        for x in acts:
            if x['kind'] == 'melee':
                classes.add('melee')
            elif x['kind'] == 'bullet' and x.get('bullet'):
                classes.add('bullet' if bullet_damages(x['bullet']) else
                            'buff' if bullet_buffs(t, x['bullet']) else
                            'bullet (status only)' if bullet_harms(t, x['bullet']) else
                            'bullet (no damage)')
            elif x['kind'] == 'speffect' and x.get('effect') in ('buff', 'stance effect'):
                classes.add(x['effect'])
            elif x['kind'] == 'iframes':
                classes.add('i-frames')
            elif x['kind'] == 'parry':
                classes.add('parry')
    fp = {h: arts[f'useMagicPoint_{h}'] for h in ('L1', 'L2', 'R1', 'R2')}
    return {'sword_arts_id': sword_arts_id, 'name': t.arts_name(sword_arts_id),
            'tae': f"a{SKILL_TAE_BASE + arts['swordArtsTypeNew']}", 'weapon_id': weapon_id,
            'weapon': t.reg.weapon_names.get(weapon_id), 'fp_cost': fp,
            'arts_speed_type': arts['artsSpeedType'], 'fp_anims': fp_anims,
            'no_fp_anims': sorted(no_fp),
            'classes': sorted(classes), 'anims': per_anim}


def no_fp_twins(per_anim):
    """Animations that are the `without FP` copy of the animation 5 ids lower (`TAE`: Lion's
    Claw 040005 fires judge 3001 = `No FP` row 300300821 where 040000 fires 3000; Quickstep
    040085-040088 mirror 040080-040083 without event 330)."""
    return {a for a in per_anim if a % 10 >= 5 and a - 5 in per_anim
            and not any(x['kind'] == 'fp' for x in per_anim[a])}


def bullet_damages(bt):
    if not bt:
        return False
    if any(bt['mv'].values()) or (bt['add_base_atk'] and any(bt['flat'].values())):
        return True
    return any(bullet_damages(c) for c in bt['children'].values())


def bullet_harms(t, bt):
    """A bullet that deals no damage but puts status on whoever it touches (the mists)."""
    if not bt:
        return False
    if any(t.harms_target(s) for s in bt['on_hit_speffects']):
        return True
    return any(bullet_harms(t, c) for c in bt['children'].values())


def bullet_buffs(t, bt):
    """A bullet that deals nothing but applies a buff on hit (Golden Vow, Holy Ground)."""
    if not bt:
        return False
    if any(t.is_buff(s) and t.speffect.get(s, {}).get('effectTargetSelf') and not t.harms_target(s)
           for s in bt['on_hit_speffects']):
        return True
    return any(bullet_buffs(t, c) for c in bt['children'].values())


def main_anim(profile):
    """The skill's opening with FP on this weapon and grip (`skill_profile` 'opening'); for a
    profile built without one, the first animation that charges FP, else the first."""
    if profile.get('opening') is not None:
        return profile['opening']
    if profile['fp_anims']:
        return profile['fp_anims'][0]
    return next(iter(profile['anims']), None)


# ---------------------------------------------------------------------------------------------
# damage

_AR_MODULE = []


def _ar_module():
    """`er-mechanics-ar`, loaded once. `WeaponContext` loaded it on every construction, which
    re-ran the module and its imports: 57% of one skill cast's time (cProfile, 300 casts of
    Alabaster Lords' Pull, 2026-11-14), and a stat spend builds thousands of contexts."""
    if not _AR_MODULE:
        _AR_MODULE.append(_load('er_mechanics_ar', 'er-mechanics-ar.py'))
    return _AR_MODULE[0]


class WeaponContext:
    """The firing weapon as the attack-power builder sees it: base attack per element, the stat
    scaling multiplier per element (`FUN_140691690`) and ReinforceParamWeapon.baseAtkRate."""

    def __init__(self, weapon, affinity='Standard', level=None, stats=None, two_handed=False,
                 ar_tables=None):
        self.AR = _ar_module()
        self.tables = ar_tables or self.AR.Tables(None)
        base_id = self.tables.find_weapon(weapon, affinity)
        self.wep = self.tables.weapons[base_id]
        if level is None:
            level = self.tables.max_level(self.wep['reinforceTypeId'])
        self.level = level
        self.stats = self.AR.normalise_stats(stats)
        self.rating = self.AR.attack_rating(self.tables, weapon, affinity, level, stats, two_handed)
        self.reinf = self.tables.reinforce[self.wep['reinforceTypeId'] + level]
        self.two = self.rating['two_handed_bonus']
        # The grip as held, which picks the skill's animation (`skill_profile`); `two` is the
        # strength bonus, which some weapons do not get.
        self.two_handed = bool(two_handed)
        self.ar_by = {el: self.rating['damage'].get(el, {}).get('total', 0.0) for el in ELEMENTS}
        self.base_by = {el: self.rating['damage'].get(el, {}).get('base', 0.0) for el in ELEMENTS}
        self.base_atk_rate = self.reinf['baseAtkRate']

    def multiplier(self, el, aecp_override=-1, disable_two_hand=False):
        """Stat scaling for one element; `aecp_override` is AtkParam.overwriteAttackElementCorrectId."""
        name, suf, _, _, gfield = next(e for e in self.AR.ELEMENTS if e[0] == el)
        aecp_id = aecp_override if aecp_override >= 0 else self.wep['attackElementCorrectId']
        str_mult = self.AR.TWO_HAND_STR_MULT if self.two and not disable_two_hand else 1.0
        return self.AR.element_multiplier(self.tables, self.wep, self.reinf,
                                          self.tables.aecp.get(aecp_id, {}), suf, self.wep[gfield],
                                          self.stats, str_mult)


def hit_attack(ctx, row, from_weapon=True):
    """Attack per element before defense for one AtkParam row, `0x1406845a0` (`VERIFIED`):

        from the weapon:  (base_el x MV/100 + (isAddBaseAtk ? flat_el x baseAtkRate : 0)) x scaling_el
        not from weapon:  flat_el x scaling of the weapon at AttackInfo+0xf0 (melee: none, so 1)

    base_el is the weapon's upgraded base attack (EquipParamWeapon.attackBase* x
    ReinforceParamWeapon.*AtkRate) and scaling_el the stat multiplier, so base_el x scaling_el is
    the AR. Bullets always take the first branch (`0x14038e390` never sets
    `attackNotFromWeapon`); a melee hit takes the second when AtkParam.hitSourceType != 0 and
    the event did not pass flag 8 (event 307 flag 8 forces the weapon branch). The durability,
    counter and SpEffect factors are left out."""
    out = {}
    for el in ELEMENTS:
        if not from_weapon:
            out[el] = float(row['flat'][el])
            continue
        flat = row['flat'][el] if row['add_base_atk'] else 0.0
        m = ctx.multiplier(el, row.get('element_correct_override', -1), row.get('disable_2h', False))
        out[el] = (ctx.base_by[el] * row['mv'][el] / 100.0 + flat * ctx.base_atk_rate) * m
    return out


def ctx_grip(ctx):
    """'both' or 'one' for a `WeaponContext` (or any object with `two_handed`)."""
    return 'both' if getattr(ctx, 'two_handed', False) else 'one'


def skill_hits(t, weapon_id, sword_arts_id, ctx, level=0, anim=None, stance=None):
    """Every damaging hit of one skill animation, with its attack per element before defense.

    This is the function a PvP ranking calls. `weapon_id` is the base id (behaviorVariationId and
    saWeaponDamage are the same across affinities); `ctx` is a `WeaponContext` for the weapon as
    built (affinity, level, stats, grip); `stance` the button a stance cast hands over on
    (`skill_profile`)."""
    prof = skill_profile(t, sword_arts_id, weapon_id, level, ctx_grip(ctx), stance)
    anim = anim if anim is not None else main_anim(prof)
    out = []

    def add(kind, frame, row, extra):
        attack = hit_attack(ctx, row, row.get('from_weapon', True))
        if sum(attack.values()) <= 0:
            return
        out.append({'kind': kind, 'frame': frame, 'atk_row': row['atk_row'], 'atk_name': row['atk_name'],
                    'mv': row['mv'], 'flat': row['flat'] if row['add_base_atk'] else {},
                    'from_weapon': row.get('from_weapon', True),
                    'disable_2h': row.get('disable_2h', False),
                    'element_correct_override': row.get('element_correct_override', -1),
                    'attack': {el: round(v, 2) for el, v in attack.items()},
                    'final_rate_id': row.get('final_rate_id', -1), **extra})

    def walk_bullet(frame, bt, seen=None, rec=None):
        # A child bullet that carries the same AtkParam row as one already counted is the same
        # wave travelling on (Hoarfrost Stomp 2260 -> 2261 -> ... -> 2264, all atk 30300863),
        # listed once, and `numShoot` copies fan out rather than stack on one target (`INFERRED`).
        # `rec` is the DmgHitRecord the node inherits (`HIT_LIST_NOTE`): the Bullet id of the row
        # that made it, which every row under it shares as `hit_list` (`cast_mask` lands one).
        seen = set() if seen is None else seen
        if not bt:
            return
        own = rec if rec is not None else (
            bt['bullet'] if bt.get('shared_hit_list') or bt.get('attach_effect') else None)
        if bt['atk_row'] not in seen:
            seen.add(bt['atk_row'])
            # Weapon-branch poise, attacks.md section 2: saWeaponDamage x correction/100 + flat.
            w = t.reg.weapon[weapon_id]
            poise = (w['saWeaponDamage'] * (bt['poise_correction'] or 0.0) * 0.01
                     + ((bt['poise_flat'] or 0.0) if bt['add_base_atk'] else 0.0))
            add('bullet', frame, bt, {'bullet': bt['bullet'], 'count': 1, 'num_shoot': bt['num_shoot'],
                                      'poise': round(poise * POISE_MENU, 2), 'hit_list': own})
        # A row with `attachEffectType` drops the record it would hand down (FUN_14039bba0).
        down = own if not bt.get('attach_effect') else None
        for child in bt['children'].values():
            walk_bullet(frame, child, seen, down)

    def tag(x, a):
        # The hit priority record the event takes (`HIT_RECORD` in er-mechanics-attacks): one key
        # per record of the animation; a bullet's children copy their root's record, priority and
        # id (FUN_14039bba0). `created` doubles as that id: every row of one bullet event gets the
        # event's (`walk_anim`), so they are one launch to `ATK.record_contact`.
        rec = x.get('record')
        return {'record': (a, rec) if rec is not None else None, 'priority': x.get('priority'),
                'record_s': x.get('record_s'), 'created': len(out)}

    def walk_anim(a):
        for x in prof['anims'].get(a, []):
            f = x['frames'][0]
            if x['kind'] == 'melee':
                add('melee', f, x, {'poise': round((x['poise'] or 0.0) * POISE_MENU, 2), 'count': 1,
                                    'judge': x['judge'], 'stamina_cost': x['stamina_cost'], 'anim': a,
                                    **tag(x, a)})
            elif x['kind'] == 'bullet':
                n = len(out)
                walk_bullet(f, x['bullet'])
                rt = tag(x, a)
                for h in out[n:]:
                    h['anim'] = a
                    h['root_bullet'] = x['bullet']['bullet']
                    h.update(rt)
                    h['created'] = n
                    # One record per launch (`HIT_LIST_NOTE`): the event's, not the row's alone.
                    if h.get('hit_list') is not None:
                        h['hit_list'] = (a, n, h['hit_list'])

    walk_anim(anim)
    if not out and anim is not None:
        # The opening only leaps (Ground Slam 040000 -> 040003/040004): take the damaging
        # animations of the same ten-block, without the no-FP copies (`INFERRED` sequence).
        for a in prof['anims']:
            if a != anim and a // 10 == anim // 10 and a not in prof['no_fp_anims']:
                walk_anim(a)
    if not out and anim is not None:
        # A stance (Stamp 040000) whose attack is its own FP-charging follow-up (040010).
        # Only the first such follow-up: the others are the same attack for other grips.
        for a in prof['fp_anims']:
            if a != anim and not out:
                walk_anim(a)
    for h in out:
        h['fp_anim'] = anim
    return out


def record_time(h):
    """Real-clip seconds at which hit row `h` first holds its hit priority record: its window's
    start, or the record's when the record opens later (`ATK.record_attach_time`)."""
    s = h.get('frame', 0) / TAE_FPS
    return max(s, h['record_s']) if h.get('record_s') is not None else s


def record_mask(hits):
    """Per `skill_hits` row: whether it can land on a defender every row touches at its own frame,
    under the hit priority records (`ATK.shared_landings`, the `HIT_RECORD` block of
    er-mechanics-attacks): the attacks of one record land once between them, plus one with a
    strictly lower priority than the entry; a bullet event's root and children rows share its
    record and its `created`, which is their launch, so they land together."""
    return ATK.shared_landings([(record_time(h), h.get('record'), h.get('priority'), h.get('created', i))
                                for i, h in enumerate(hits)])


#: The bullet's own hit list (`VERIFIED` 1.16.2 named dump, ashes-of-war.md "`isUseSharedHitList`";
#: bd dmghitrecord-inherited-down-bullet-lineage-1162-2026-11-12, er-effects-rs-7bye): a launch
#: whose Bullet row has `isUseSharedHitList` or `attachEffectType` makes one DmgHitRecord
#: (FUN_14038fe80) that its whole `numShoot` volley shares, and every child it makes on hit, expiry
#: or interval copies it with the attack info (FUN_14039bba0 -> FUN_14038b740, +0x638) unless the
#: parent's row has `attachEffectType`. A victim in the record is not hit again by any of them for
#: its `dmgHitRecordLifeTime` (handed down from the first row above 0, `er-mechanics-spells.
#: record_lifetime`). So Wave of Gold's 4000 -> 4001 -> ... -> 4045 travelling wave, which changes
#: AtkParam row as it goes (905, 907, 906, 908 at FinalDamageRate 0.8 / 0.7 / 0.65 / 0.6), and
#: Surge of Faith's flame 2371 with the 2375 it leaves, are one hit per victim each. `skill_hits`
#: listed every AtkParam row of such a tree and the sums priced them all, until 2026-11-14.
#: Not modelled: a member that still touches him once the record lifetime runs out hits again.
#: Where a member can be made after the lifetime (its parents' lives summed past it), that may
#: under-count: Smithing Art Spears (200002313 then 200002314, both penetrating and standing, list
#: 1.0 s), Discus Hurl (list 0.1 s, 200002852 made at up to 0.55 s), Spearcall Ritual (list 0.2 s).
#: Familial Rancor's and Bear Witness!'s late members follow a parent that dies on the hit or has
#: flown on, so one hit stands there (`INFERRED`, regulation read 2026-11-14).
HIT_LIST_NOTE = 'VERIFIED: FUN_14038fe80, FUN_14039bba0, FUN_14038b740'


def cast_mask(hits, value=None):
    """Per `skill_hits` row: whether it lands on one defender every row reaches, in one cast. The
    hit priority records first (`record_mask`), then the bullet hit lists (`HIT_LIST_NOTE`): of
    the rows one record holds (`hit_list`) only the one worth most lands, `value(row)` (default the
    attack before defense), the first on a tie. Rows with neither a record nor a list all land, so
    a caller's own rows (no `record`, no `hit_list`) keep their plain sum."""
    if not any(h.get('record') is not None or h.get('hit_list') is not None for h in hits):
        return [True] * len(hits)
    value = value or (lambda h: sum(h.get('attack', {}).values()))
    ok = record_mask(hits)
    best = {}
    for i, h in enumerate(hits):
        key = h.get('hit_list')
        if key is not None and ok[i] and (key not in best or value(h) > value(hits[best[key]])):
            best[key] = i
    return [ok[i] and (h.get('hit_list') is None or best[h['hit_list']] == i) for i, h in enumerate(hits)]


PHYS_TYPE = {0: 'slash', 1: 'strike', 2: 'pierce', 3: 'standard'}


def phys_type(t, weapon_id, atk_row):
    """AtkParam.atkAttribute; 253 takes the weapon's `atkAttribute`, 252 its `atkAttribute2`
    (`VERIFIED`, `er-mechanics-attacks.WEAPON_ATTRIBUTE_REF`: 0x140686e5d-0x140686e7a,
    `cmp cl,0xfd` -> +0x104, `cmp cl,0xfc` -> +0x191). This read them swapped until 2026-10-19,
    so a claw's Impaling Thrust (252, the claw's pierce) was scored as slash."""
    attr = t.reg.atk.get(atk_row, {}).get('atkAttribute', 3)
    w = t.reg.weapon[weapon_id]
    if attr == 253:
        attr = w['atkAttribute']
    elif attr == 252:
        attr = w['atkAttribute2']
    return PHYS_TYPE.get(attr, 'standard')


def skill_buffs(t, prof, anim=None, on_target=False):
    """The buff SpEffects the paid-for animation applies: events 331/66/67/401 on self, a
    bullet judge resolving to refType 2 (self), and bullets whose on-hit SpEffects can land on
    the caster's side (Golden Vow's area). With `on_target`, instead the on-hit SpEffects that
    cannot target the caster: the effects the skill puts on whoever it hits (frostbite, bleed)."""
    anim = anim if anim is not None else main_anim(prof)
    out, seen = [], set()

    def take(s, from_bullet=False):
        if not s or s['id'] in seen:
            return
        on_hit = from_bullet and (not s['can_target_self'] or t.harms_target(s['id']))
        if on_target:
            if on_hit:
                seen.add(s['id'])
                out.append(s)
            return
        if t.is_buff(s['id']) and not on_hit:
            seen.add(s['id'])
            out.append({**s, 'effect': t.effect_kind(s['id'])})

    def walk_bullet(bt):
        if not bt:
            return
        for sid in bt['on_hit_speffects']:
            take(t.speffect_summary(sid), True)
        for c in bt['children'].values():
            walk_bullet(c)

    for x in prof['anims'].get(anim, []):
        if x['kind'] == 'speffect':
            take(x.get('speffect'))
        elif x['kind'] == 'bullet':
            walk_bullet(x.get('bullet'))
        elif x['kind'] == 'melee':
            # AtkParam spEffectId0/1 of a melee hit land on the target (Blood Tax 1830).
            for sid in x.get('on_hit_speffects') or []:
                take(t.speffect_summary(sid), True)
    return out


def describe_speffect(s):
    """One line: id, name, duration, the effect fields, then linked rows in arrows."""
    if not s:
        return ''
    eff = ', '.join(f'{k}={v}' for k, v in s['effects'].items() if k not in ('stateInfo',))
    dur = 'no timer' if s['duration_s'] == 0 else ('permanent' if s['duration_s'] < 0 else f"{s['duration_s']}s")
    line = f"{s['id']} {s['name'] or ''} [{dur}] {eff}".rstrip()
    for link, c in s['links'].items():
        line += f' -> ({link}) ' + describe_speffect(c)
    return line


def pvp_damage(t, weapon_id, hits, defender, defense_module, damage_fn=None):
    """Sum of `hits` against `defender` with the weapon's vsPlayer rates and the hit's own
    FinalDamageRateParam row (`VERIFIED` 0x1406865f2: both sides players; skills mostly use row
    10000, 0.8 on every element, where R1s use 1.0).

    `damage_fn(attack, phys_type, final_rate_row) -> damage` replaces the one-defender model:
    er-builds-pvp.py passes its corpus mean (`corpus_hit`) so a skill hit and a slot hit meet the
    same defenders.

    The sum is one cast on one defender: only the rows `cast_mask` lands count (the hit priority
    records and the bullet hit lists), each marked `lands`. Every row still gets its own
    `pvp_damage_total`, which `skill_landing` plays with geometry."""
    w = t.reg.weapon[weapon_id]
    for h in hits:
        attack = {el: v * w.get(f'vsPlayerDmgCorrectRate_{VS_PLAYER[el]}', 1.0)
                  for el, v in h['attack'].items()}
        fr = t.final_rate.get(h['final_rate_id']) if h['final_rate_id'] >= 0 else None
        h['final_rate'] = fr['physRate'] if fr else 1.0
        h['phys_type'] = phys_type(t, weapon_id, h['atk_row'])
        if damage_fn is not None:
            d = damage_fn(attack, h['phys_type'], fr)
        else:
            d = defense_module.damage(attack, 100.0, defender, h['phys_type'], fr)['total']
        h['pvp_damage'] = round(d, 1)
        h['pvp_damage_total'] = d * h.get('count', 1)
    total = 0.0
    for h, ok in zip(hits, cast_mask(hits, lambda h: h['pvp_damage_total'])):
        h['lands'] = ok
        total += h['pvp_damage_total'] if ok else 0.0
    return total


# ---------------------------------------------------------------------------------------------
# corpus adoption

PVP_TAGS = {'Invasions', 'Duels', 'Co-op/Gank', '2v2', 'Ladder'}
#: Weapons a skill is put on for `pvp` when no build in the window pairs it (common STR picks
#: across weapon classes, from the corpus top weapons).
STR_REFERENCE_WEAPONS = ('Greatsword', 'Claymore', 'Zweihander', 'Giant-Crusher', 'Lance',
                         'Great Stars', 'Hand Axe', 'Dagger', 'Longsword', 'Brick Hammer')
FILTERS = ('pvptag', 'tag', 'str60', 'pvp')


def corpus_slots(mirror, rl_lo, rl_hi, kind):
    """Builds of the window with their active-set weapon slots, same filters and dedup as
    er-builds-adoption-gap.py (`pvptag`: Strength tag plus a PvP tag; `tag`: Strength tag;
    `str60`: STR 60+; `pvp`: every build with `isPvE` false or a PvP tag, the pairing corpus of
    `skill_pairings`); PvE builds dropped."""
    embed = _load('er_builds_embed', 'er-builds-embed.py')
    out, seen = [], set()
    for row in er_corpus.iter_rows(mirror):
        b = row['build']
        st = embed.stats_of(b)
        if st is None or not rl_lo <= st['rl'] <= rl_hi:
            continue
        tags = set(b.get('tags') or [])
        if b.get('isPvE') or 'PvE' in tags:
            continue
        if kind == 'tag' and 'Strength' not in tags:
            continue
        if kind == 'pvp' and not (b.get('isPvE') is False or tags & PVP_TAGS):
            continue
        if kind == 'pvptag' and not ('Strength' in tags and tags & PVP_TAGS):
            continue
        if kind == 'str60' and st['str'] < 60:
            continue
        if sum(st[k] for k in embed.ATTRS) - embed.LEVEL_OFFSET != st['rl']:
            continue
        key = (row.get('user'), tuple(embed.tokens(b)))
        if key in seen:
            continue
        seen.add(key)
        active = embed.active_set(b, 'weapons')
        slots = []
        for s in (b.get('inventory') or {}).get('slots') or []:
            es = s.get('equipSet')
            pos = (es[active] if active < len(es) else None) if isinstance(es, list) \
                else s.get('equipIndex')
            if pos is not None and s.get('name'):
                slots.append({**s, 'pos': pos, 'name': plain(s['name'])})
        out.append({'stats': st, 'is2h': bool(b.get('is2h')), 'slots': slots,
                    'computed': b.get('computed') or {}})
    return out


def effective_skill(t, slot):
    """(skill name, source): the planner's weaponArt when it names a skill, else the weapon's own."""
    art = slot.get('weaponArt')
    if art and art != 'No Skill':
        return plain(art), 'ash'
    try:
        wid = t.find_weapon(slot['name'])
    except SystemExit:
        return None, 'unknown weapon'
    sid = t.reg.weapon[wid]['swordArtsParamId']
    return t.arts_name(sid), 'built-in'


def adoption(t, rows):
    """Per skill: builds that equip it anywhere, in the right hand (pos 0-2), as an ash vs
    built-in, and the weapons it sits on."""
    stats = collections.defaultdict(lambda: {'builds': 0, 'right': 0, 'ash': 0, 'builtin': 0, 'l2': 0,
                                              'weapons': collections.Counter(),
                                              'pairs': collections.Counter()})
    for r in rows:
        # The skill L2 fires with the primary pair (right slot 0, left slot 3) as the build
        # holds it, per `active_skill`.
        prim = {s['pos']: s for s in r['slots'] if s['pos'] in (0, 3)}
        ids = {}
        for pos, s in prim.items():
            name, _ = effective_skill(t, s)
            try:
                ids[pos] = t.find_arts(name) if name else None
            except SystemExit:
                ids[pos] = None
        if 0 in ids or 3 in ids:
            l2 = t.active_skill(ids.get(0), ids.get(3), r['is2h'])
            if l2 is not None:
                stats[t.arts_name(l2)]['l2'] += 1
        seen, right = set(), set()
        for s in r['slots']:
            name, src = effective_skill(t, s)
            if not name:
                continue
            e = stats[name]
            if name not in seen:
                e['builds'] += 1
                seen.add(name)
            if s['pos'] in (0, 1, 2) and name not in right:
                e['right'] += 1
                right.add(name)
            e['ash' if src == 'ash' else 'builtin'] += 1
            e['weapons'][s['name']] += 1
            e['pairs'][(s['name'], s.get('infusion') or 'Standard')] += 1
    return stats


# ---------------------------------------------------------------------------------------------
# the skill term of the PvP ranking (ashes-of-war.md section 13)

#: Pseudo-builds a weapon's own pairing counts are shrunk toward its weapon class with, and a
#: class toward the whole corpus (`INFERRED` strength: most weapons have under 10 PvP builds).
SKILL_PAIRING_ALPHA = 5.0
#: Candidates kept per weapon by probability; the dropped mass is treated as no damaging skill.
SKILL_CHOICE_TOP = 6
#: Share of the skill's option value (below) added to the weapon score (`INFERRED`).
SKILL_WEIGHT = 0.5
#: Median max FP of the RL 140-160 PvP corpus (planner `computed.maxFP`, `MEASURED` 2026-09-29:
#: 1118 builds, quartiles 78 / 88 / 121). The ranking passes its own window's value.
FP_BAR_DEFAULT = 88.0
#: Landed hits in a fight: `er-mechanics-status.Defenders.fight_engagements` at RL 150.
ENGAGEMENTS_DEFAULT = 5
#: Neutral time between engagements (`er-mechanics-status.ENGAGEMENT_SECONDS`, `INFERRED`): what a
#: visible buff has to outlast not to be waited out. Not the fight length (`FIGHT_SECONDS`), which
#: only the buffs' recasts read. `er-builds-pvp.py --engagement-seconds` sets it.
ENGAGEMENT_SECONDS = 5.0
OWN = 'own'


def skill_pairings(t, rows):
    """Which skill the corpus fires from each weapon, from `corpus_slots` rows.

    {'weapon': {base weapon id: Counter}, 'class': {wepType: Counter}, 'all': Counter}. A slot
    whose `weaponArt` is empty, `No Skill` or the weapon's own skill counts as `OWN`; a mounted
    ash counts as its SwordArtsParam id. The class and corpus counts take only weapons that
    accept ashes (`gemMountType` 2), so a unique weapon's fixed skill does not read as a choice."""
    wep = collections.defaultdict(collections.Counter)
    cls = collections.defaultdict(collections.Counter)
    allc = collections.Counter()
    for r in rows:
        for s in r['slots']:
            try:
                wid = t.find_weapon(s['name'])
            except SystemExit:
                continue
            w = t.reg.weapon[wid]
            art = s.get('weaponArt')
            key = OWN
            if art and art != 'No Skill':
                try:
                    sid = t.find_arts(plain(art))
                except SystemExit:
                    continue
                key = OWN if sid == w['swordArtsParamId'] else sid
            wep[wid][key] += 1
            if w['gemMountType'] == 2:
                cls[w['wepType']][key] += 1
                allc[key] += 1
    return {'weapon': wep, 'class': cls, 'all': allc}


def skill_choice(t, pairings, weapon_id, affinity=0, level=25):
    """[(SwordArtsParam id, p)] for one weapon as built, highest first: the corpus pairing of
    that weapon, shrunk toward its class and the corpus by `SKILL_PAIRING_ALPHA`, over the skills
    it can carry at this affinity and level (`can_mount`, `VERIFIED`). A weapon that takes no
    ash gets its own skill with p 1. At most `SKILL_CHOICE_TOP` entries."""
    w = t.reg.weapon[weapon_id]
    own = w['swordArtsParamId']
    gems = t.ash_gems()

    def ok(key):
        if key == OWN:
            return True
        g = gems.get(key)
        return g is not None and t.can_mount(weapon_id, g, affinity, level)[0]

    def shrink(counter, prior):
        keys = {k for k in counter if ok(k)} | set(prior)
        n = sum(counter.get(k, 0) for k in keys)
        return {k: (counter.get(k, 0) + SKILL_PAIRING_ALPHA * prior.get(k, 0.0)) / (n + SKILL_PAIRING_ALPHA)
                for k in keys}

    base = {k: c for k, c in pairings['all'].items() if ok(k)}
    tot = sum(base.values())
    p = {k: c / tot for k, c in base.items()} if tot else {OWN: 1.0}
    p = shrink(pairings['class'].get(w['wepType'], {}), p)
    p = shrink(pairings['weapon'].get(weapon_id, {}), p)
    merged = collections.Counter()
    for k, v in p.items():
        merged[own if k == OWN else k] += v
    # Ties break on the lower id. `most_common` would keep the iteration order of `keys`, a set
    # holding the string `OWN` beside int ids, so the order moved with the string hash seed: the
    # three spears' sixth skill was Impaling Thrust in one run and Chilling Mist in the next.
    top = sorted(merged.items(), key=lambda kv: (-kv[1], kv[0]))[:SKILL_CHOICE_TOP]
    return [(sid, v) for sid, v in top if v > 0]


_MOUNTABLE = {}


def mountable_skills(t, weapon_id, affinity=0, level=25):
    """[SwordArtsParam id] a weapon can fire as built: its own skill, then every ash-of-war item
    (`ash_gems`) that `can_mount` accepts at this affinity and level (`VERIFIED` 0x140d57e70,
    which also applies the affinities the ash itself allows). A weapon that takes no ash
    (`gemMountType` 0) has only its own skill. Cached."""
    key = (weapon_id, affinity, level)
    if key not in _MOUNTABLE:
        own = t.reg.weapon[weapon_id]['swordArtsParamId']
        out = [own] if own in t.arts else []
        for sid, gem in sorted(t.ash_gems().items()):
            if sid != own and sid in t.arts and t.can_mount(weapon_id, gem, affinity, level)[0]:
                out.append(sid)
        _MOUNTABLE[key] = out
    return _MOUNTABLE[key]


def fp_uses(fp_bar, cost):
    """Casts one FP bar pays for: the full ones, plus one more when what is left passes
    `has_enough_fp` (half the cost, `VERIFIED` 0x1404802a0). None when the skill costs nothing."""
    if cost <= 0:
        return None
    n, rest = divmod(int(fp_bar), int(cost))
    return n + (1 if AshTables.has_enough_fp(rest, cost) else 0)


def _hits_cap(engagements):
    """Casts a free skill (no FP limit) is taken to have: one per landed hit, the most any point
    of a schedule (`er-mechanics-buffs.fight_hits`) lands, so no point is capped by it."""
    if isinstance(engagements, (tuple, list)):
        return float(max(engagements, default=ENGAGEMENTS_DEFAULT))
    return float(engagements)


def _use_share(uses, engagements):
    """Share of the fight's landed hits `uses` casts cover, capped at 1: the mean over a
    schedule's points of min(1, uses / hits), or that of one count."""
    hs = engagements if isinstance(engagements, (tuple, list)) else (engagements,)
    return sum(min(1.0, uses / max(h, 1)) for h in hs) / len(hs)


def anim_recovery(t, weapon_id, sword_arts_id, anim, after=0.0):
    """{action: first real frame it can start} for one skill animation, the way attacks.md
    section 4 reads a slot: `recovery_windows` over the skill TimeAct's JumpTable events, TAE 608
    play speed applied. Only openings at or after `after` (clip seconds, the last hit's start)
    count: a cancel window open before the hit abandons the skill, it does not recover from it.
    Skill hit frames (`skill_hits` 'frame') are clip frames; these are real frames (Hoarah Loux's
    Earthshaker: hit at clip 113 = real 61.7, roll at real 76.7)."""
    events = (skill_tae(t, sword_arts_id) or {}).get(anim)
    if not events:
        return {}
    win = ATK.recovery_windows(events, t.reg.weapon[weapon_id]['weaponWeightRate'])
    to_real = ATK.clip_to_real(events)
    out = {}
    for key, _, inputs, _ in ATK.RECOVERY_ACTIONS:
        w = win[key]
        if inputs is None:
            pairs = [(c[0], c[1]) for c in w['cancel']]
        else:
            pairs = [(max(i[0], c[0]), min(i[1], c[1])) for i in w['input'] for c in w['cancel']]
        starts = [max(s, after) for s, e in pairs if max(s, after) < e]
        out[key] = ATK.real_frame(to_real(min(starts))) if starts else None
    return out


def skill_commit(t, weapon_id, sword_arts_id, prof, hits):
    """{'roll', 'next', 'lead'}: the frames from the skill's start to the first roll and the
    first R1 after its last hit, in the shape `er-builds-pvp.slot_score` reads (it takes the
    smaller as the commitment).

    Read on the animation that holds the last hit. When that is not the opening (Stamp's attack
    040010 after the stance 040000, Ground Slam's landing after the leap) the opening's first
    R1/R2 cancel is added as a lead-in, or its clip length when it has none (`INFERRED`: the
    behavior script's hand-over frame is not traced).

    A stance cast (`_opening` `stance_entry`) adds its entry clip the same way, at the earliest
    cancel of the one button that plays the opening (`stance_windup`): Unsheathe's R1 slash
    hits at 8 + 13, its R2 slash at 6 + 15. The press is the attacker's to time, so the defender
    reads the attack from it (`cue`)."""
    opening = main_anim(prof)
    last = hits[-1].get('anim', opening) if hits else opening
    after = max((h['frame'] for h in hits if h.get('anim', opening) == last), default=0) / TAE_FPS
    rec = anim_recovery(t, weapon_id, sword_arts_id, last, after)
    cat = SKILL_TAE_BASE + t.arts[sword_arts_id]['swordArtsTypeNew']

    def handover(anim, button=None):
        # (frames, whether the hand-over happens by itself). A leap hands over when it lands,
        # which the behavior script decides; the landing can only come once the falling hitbox is
        # live, the last hitbox window to open (Gold Breaker's 040000 no-damage hitbox f70-135;
        # Ground Slam's body hitbox 500 f49-135, after the take-off box 504 at f23). Its start is
        # the hand-over (`INFERRED`: the earliest landing, on flat ground). Otherwise the first
        # R1/R2 cancel (`button`'s alone when one is named), a press the player times, else the
        # clip length.
        boxes = [x['frames'][0] for x in prof['anims'].get(anim, [])
                 if x['kind'] in ('melee', 'no-damage hitbox', 'body hitbox')] if button is None else []
        events = (skill_tae(t, sword_arts_id) or {}).get(anim)
        if boxes and events:
            return ATK.real_frame(ATK.clip_to_real(events)(max(boxes) / TAE_FPS)), True
        o = anim_recovery(t, weapon_id, sword_arts_id, anim)
        cands = ([o[button]] if o.get(button) is not None else []) if button else \
            [v for v in (o.get('r1'), o.get('r2')) if v]
        if cands:
            return min(cands), False
        clip = ATK.clip_length(cat, anim)
        return (ATK.real_frame(clip[0]) if clip else 0.0), True

    lead = 0.0
    first = hits[0].get('anim', opening) if hits else opening
    windup, hand = 0.0, None
    if last != opening or first != opening:
        hand = handover(opening)
    if last != opening:
        lead = hand[0]
    # A wind-up in front of the opening: the lowest animation of the opening's ten-block, when it
    # is not the opening, not a without-FP copy and has no hit (Wild Strikes 040050, 35 frames,
    # before the looping 040051 that charges FP). Its hand-over is added the same way (`INFERRED`).
    entry = min(a for a in prof['anims'] if a // 10 == opening // 10) if opening is not None else None
    if entry is not None and entry != opening and entry not in prof['no_fp_anims'] and not any(
            x['kind'] in ('melee', 'bullet') for x in prof['anims'][entry]) and entry != last:
        windup = handover(entry)[0]
        lead += windup
    elif prof.get('stance_entry') is not None and prof['stance_entry'] not in (opening, last):
        # A stance: L2 into `STANCE_START`, which may sit in another ten-block (Unsheathe's
        # 040050 before 040060 / 040070), left on the opening's own button.
        windup = handover(prof['stance_entry'], prof['stance_button'])[0]
        lead += windup
    stance = windup > 0 and prof.get('stance_entry') is not None
    # Where the first hit's animation starts, and where the defender can first read its timing
    # (section 16): the skill's start, unless a press the attacker times hands over to it (the
    # follow-up's, or a stance's button).
    first_offset = windup + (hand[0] if first != opening else 0.0)
    cue = first_offset if (first != opening and not hand[1]) or stance else 0.0
    roll, nxt = rec.get('dodge'), rec.get('r1')
    events = (skill_tae(t, sword_arts_id) or {}).get(last) or []
    hit_real = ATK.real_frame(ATK.clip_to_real(events)(after)) + lead if events else None
    return {'roll': roll + lead if roll is not None else None,
            'next': nxt + lead if nxt is not None else None, 'lead': lead, 'anim': last,
            'first_offset': first_offset, 'cue': cue,
            # The shape `er-mechanics-frame-advantage.advantage` reads: the last hit's real start
            # frame and every action's first frame after it, both from the skill's start.
            'hit_windows': [(hit_real, hit_real)] if hit_real is not None else [],
            'cancel_frame': {k: (v + lead if v is not None else None) for k, v in rec.items()}}


#: `er-mechanics-crits.JT_GET_PARRIED`: an animation with this JumpTable open can be parried.
JT_GET_PARRIED = 5


def skill_parryable(t, sword_arts_id, anims):
    """Whether any of `anims` opens JumpTable 5, the test `er-mechanics-crits.parry_exposure`
    applies to an attack slot (`VERIFIED` there: both parry routes require it)."""
    tae = skill_tae(t, sword_arts_id) or {}
    return any(e.type == EV_JUMP_TABLE and struct.unpack_from('<i', e.params, 0)[0] == JT_GET_PARRIED
               for a in anims for e in tae.get(a, []))


def skill_fp(t, sword_arts_id):
    """The FP one use charges: `useMagicPoint_L2`, or for a stance skill that charges on its
    follow-up (Unsheathe) the highest of the four columns."""
    row = t.arts[sword_arts_id]
    l2 = row['useMagicPoint_L2']
    return l2 if l2 > 0 else max(row[f'useMagicPoint_{h}'] for h in ('L1', 'L2', 'R1', 'R2'))


def skill_followups(t, sword_arts_id, weapon_id, level=0, grip='one', stance=None):
    """The follow-ups a skill's opening offers (section 16): per TAE event 66 carrying a
    `FOLLOW_UP_SPEFFECTS` id, the button, the follow-up animation and the real frames of the
    opening between which that press starts it (where the SpEffect window, the button's input
    window and its cancel window overlap, attacks.md section 4). Flaming Strike on a Lance: 100050
    f30-44, R2 input 87 from f21, R2 cancel 116 f30-35 then 4 from f35, so R2 at f30-44 plays
    040010, the melee swing that applies the fire buff. On a weapon whose diff category moves the
    opening, the follow-up moves with it (`followup_anim`). `stance` reads a stance skill's attack
    for that button (`skill_profile`); each entry names it as `stance`."""
    prof = skill_profile(t, sword_arts_id, weapon_id, level, grip, stance)
    opening = main_anim(prof)
    events = (skill_tae(t, sword_arts_id) or {}).get(opening) or []
    if not events:
        return []
    to_real = ATK.clip_to_real(events)
    rec = ATK.recovery_windows(events, t.reg.weapon[weapon_id]['weaponWeightRate'])
    out = []
    for e in events:
        if e.type not in EV_SPEFFECT:
            continue
        sp = struct.unpack_from('<i', e.params, 0)[0]
        if sp not in FOLLOW_UP_SPEFFECTS:
            continue
        button, step = FOLLOW_UP_SPEFFECTS[sp]
        anim = followup_anim(prof, step)
        if anim not in prof['anims'] or anim in prof['no_fp_anims']:
            continue
        w = rec[button]
        pairs = [(max(i[0], c[0], e.start), min(i[1], c[1], e.end)) for i in w['input'] for c in w['cancel']]
        pairs = [(a, b) for a, b in pairs if a < b]
        if not pairs:
            continue
        out.append({'anim': anim, 'button': button, 'speffect': sp,
                    'first': ATK.real_frame(to_real(min(a for a, _ in pairs))),
                    'last': ATK.real_frame(to_real(max(b for _, b in pairs))),
                    'stance': prof.get('stance_button')})
    return out


#: TAE event 66 SpEffect on a charged skill's opening during which letting go of L2 plays the early
#: release, the opening's clip + 1 (`VERIFIED` HKS reading, docs/er-mechanics/chainsaw/
#: fallback-test-plan.md section 4; the window -> clip map is `scripts/er-fallback-routes.py`
#: `WINDOW_DEST`; c0000.hks decompiled `SwordArtsOneShot_onUpdate`: `env(1108, ACTION_ARM_L2)` <= 0
#: and `env(1116, 100285)` -> `W_SwordArtsChargeCancelEarly`, bd
#: skill-tap-vs-hold-speffect-100285-l2-up-2026-11-10). 100286, the late release (Carian Grandeur
#: alone), is not read: which clip it plays is not established.
RELEASE_EARLY_SPEFFECT = 100285


def skill_releases(t, sword_arts_id, weapon_id, level=0, grip='one', stance=None):
    """[`skill_followups`-shaped entry] for the early release of skill `sword_arts_id`'s opening on
    weapon `weapon_id`: when the opening carries a `RELEASE_EARLY_SPEFFECT` window and the profile
    has the clip after it, that clip let go at the window's first real frame, button 'l2', 'release'
    'tap'. L2 up on any frame of the window plays it, and L2 let go before the window waits for its
    first frame (`VERIFIED` c0000.hks `SwordArtsOneShot_onUpdate`): 'first' is the earliest release
    and 'last' the window's end, the attacker's choice between them (`release_frames`;
    attack-similarity scores it as the mean of its release frames, or, with an opening hit before
    it, as a follow-up press, `FollowupForm`). `skill_option` prices it as a follow-up pressed there, so the
    tap plays the opening up to that frame and its own clip from it, while the hold plays the
    opening on: the two part there (`er-mechanics-spells.skill_lookalikes`, `skill_release_part`).
    Round 5 survey (params and TimeActs, `VERIFIED`): 21 skills carry `AtkParam.subCategory` 111
    (charged skill) rows, every one in the opening clip, so the opening scores the full charge; 28
    carry the window."""
    prof = skill_profile(t, sword_arts_id, weapon_id, level, grip, stance)
    opening = main_anim(prof)
    events = (skill_tae(t, sword_arts_id) or {}).get(opening) or [] if opening is not None else []
    if not events or opening + 1 not in prof['anims'] or opening + 1 in prof['no_fp_anims']:
        return []
    wins = [e for e in events if e.type in EV_SPEFFECT
            and struct.unpack_from('<i', e.params, 0)[0] == RELEASE_EARLY_SPEFFECT]
    if not wins:
        return []
    to_real = ATK.clip_to_real(events)
    return [{'anim': opening + 1, 'button': 'l2', 'speffect': RELEASE_EARLY_SPEFFECT,
             'first': ATK.real_frame(to_real(min(e.start for e in wins))),
             'last': ATK.real_frame(to_real(max(e.end for e in wins))),
             'stance': prof.get('stance_button'), 'release': 'tap'}]


#: Real frames between the releases of a window whose own outcome is read first (`release_read`):
#: the window's first and last real frames and every this many between.
RELEASE_STEP = 5
#: HP: two neighbouring releases read whose mean nets differ by more than this have the release
#: halfway between them read too, down to every frame (`release_read`). With `RELEASE_STEP` 5 it
#: lands within 3% or 5 HP of reading every frame on every release attack-similarity.md section 2c
#: measures; no grid of members did (section 2c's table).
RELEASE_TOL = 4.0


def release_frames(first, last):
    """[(release frame, weight)]: where in its window [`first`, `last`) (real frames; the end left
    out, `INFERRED` as `skill_followups`' windows) the attacker lets go, the frame being his choice
    on any frame of it (`VERIFIED` c0000.hks: `MagicFireRight_Upper_onUpdate` for SpEffect 100610,
    `SwordArtsOneShot_onUpdate` for 100285). He draws it uniformly over the window (`INFERRED`: the
    members of a group the defender cannot tell apart are weighed equally, `lookalike_plan`, so the
    attacker is taken to mix as evenly as the defender expects): every real frame of the window,
    first, first + 1, ... before `last`, each weighed 1 / their count. Every one is a member of the
    group the defender plans against, since where he decides moves with them (a coarser set of
    members moved Revenger's Blade's release by up to 50 HP, attack-similarity.md section 2c);
    which of them have their own outcome read is `release_read`'s. One frame, or no window (`last`
    None), is the earliest release alone, [(first, 1.0)], how a release was scored before."""
    first = float(first)
    n = 1 if last is None else max(1, math.ceil(float(last) - first - 1e-9))
    return [(first + i, 1.0 / n) for i in range(n)]


def release_read(n, read, summary, step=RELEASE_STEP, tol=RELEASE_TOL):
    """{index: `read(index)`} for the releases 0 .. n - 1 of one window (`release_frames`, in frame
    order), read where it matters: the first, the last and every `step` between, then, while two
    neighbouring ones read have `summary`s (a mean net, HP) more than `tol` apart, the one halfway
    between them. A release not read is `release_fill`'s mix of the two read around it. The net of a
    release moves little within a window except where the defender's plan turns (Revenger's Blade's
    release: 366 HP let go by f34, under 22 from f38, attack-similarity.md section 2c), and that is
    where it is read frame by frame. `tol` 0 and `step` 1 read every one."""
    got = {}

    def get(i):
        if i not in got:
            got[i] = read(i)
        return got[i]
    nodes = sorted(set(range(0, n, max(1, int(step)))) | {n - 1})
    for i in nodes:
        get(i)
    todo = list(zip(nodes, nodes[1:]))
    while todo:
        a, b = todo.pop()
        if b - a > 1 and abs(summary(got[a]) - summary(got[b])) > tol:
            m = (a + b) // 2
            get(m)
            todo += [(a, m), (m, b)]
    return got


#: HP: two neighbouring nodes of a release (`release_nodes`) whose mean nets differ by more than this
#: over the share of the window between them have every frame between them added
#: (`release_fill_in`): the frames between two nodes are read as their straight line, which could be
#: off the release's net by about that much.
RELEASE_SPLIT_HP = 1.0


def release_nodes(n, step=RELEASE_STEP):
    """The releases 0 .. n - 1 of one window (`release_frames`, in frame order) that stand for it:
    the first, the last and every `step` between (sorted indices). The frames between two nodes are
    read as the straight line between the two (`node_weights`), so the window's ends, where a
    release can differ from every other (Rock Blaster's first frame nets 705 HP and every later one
    97, attack-similarity.md section 2c), are always read, and so is the last release, after which
    the defender knows the hold."""
    return sorted(set(range(0, n, max(1, int(step)))) | {n - 1})


def node_weights(nodes, frames):
    """{node: weight}: each of `frames` ({index: weight}) shared between the two nodes around it in
    proportion to how near it is to each (the straight line between them), a node its own, a frame
    outside the nodes its nearest node's."""
    out = {a: 0.0 for a in nodes}
    for i, w in frames.items():
        k = bisect.bisect_left(nodes, i)
        if k < len(nodes) and nodes[k] == i:
            out[i] += w
        elif k == 0 or k == len(nodes):
            out[nodes[min(k, len(nodes) - 1)]] += w
        else:
            a, b = nodes[k - 1], nodes[k]
            s = (i - a) / (b - a)
            out[a] += (1 - s) * w
            out[b] += s * w
    return out


def release_fill_in(nodes, nets, n, hp=RELEASE_SPLIT_HP):
    """`nodes` with every frame between two neighbours added where their mean nets (`nets`, HP, per
    node) differ by more than `hp` over the window's share between them ((b - a) / `n`): one round
    that reads such a stretch frame by frame. Halving it round after round, each round reading every
    node again, cost more for no closer net (attack-similarity.md section 2c). The same list when
    none is added."""
    add = [i for (a, na), (b, nb) in zip(zip(nodes, nets), list(zip(nodes, nets))[1:])
           if b - a > 1 and (b - a) / n * abs(na - nb) > hp for i in range(a + 1, b)]
    return sorted(set(nodes) | set(add)) if add else nodes


#: Metres: a bone of the released pose this far from where the held pose has it is a release the
#: defender can see (`release_visible`; `INFERRED`, attack-similarity.md section 2c gives what 5 and
#: 20 cm would move).
VISIBLE_M = 0.10
#: Real frames after a release `release_visible` looks for it to show; one that has not shown by then
#: is taken to show then.
VISIBLE_SCAN = 30
#: Bones left out of `release_visible`'s distance: targets and dummies, which are not drawn.
VISIBLE_SKIP = ('_Target', 'Dummy')
_VISIBLE = {}
_VISIBLE_CLIPS = {}


class _PoseClip:
    """One player clip of TimeAct category `cat` for `release_visible`: its events (TAE speed
    included, `clip_to_real`), its decoded animation (the clip it imports, `hkx_source`), its own
    events' type-16 blend-in time."""

    def __init__(self, cat, anim):
        _, _, ev = ATK.resolve_events(cat, anim)
        self.events = ev or []
        own = (ATK.tae_animations(cat) or {}).get(anim) or []
        self.anim = _pose()[0].load_animation(*ATK._reach().hkx_source(cat, anim))
        self.length = self.anim.duration
        self.to_real = ATK.clip_to_real(self.events)
        self.real_length = self.to_real(self.length)
        # The engine blends into a clip over the end time of its type-16 event, the last in event
        # order (`VERIFIED` 1.16.2: CustomTransitionEffect activate 0x1419bd150 -> FUN_1419bd2b0 ->
        # the CSChrTimeActModule callback -> FUN_1401aa350 reads type 0x10's end into binding+0x18;
        # bd player-blend-time-is-tae-type16-end-2026-11-11); its own events first, then the
        # resolved (imported) ones; none blends at once.
        self.blend_s = 0.0
        for evs in (own, self.events):
            e16 = [e for e in evs if e.type == 16]
            if e16:
                self.blend_s = max(0.0, float(e16[-1].end))
                break

    def clip_t(self, real_s):
        """The clip time at which `real_s` real seconds have played (the end past it)."""
        if self.to_real(self.length) <= real_s:
            return self.length
        lo, hi = 0.0, self.length
        for _ in range(60):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if self.to_real(mid) < real_s else (lo, mid)
        return lo


def _pose_clip(cat, anim):
    if (cat, anim) not in _VISIBLE_CLIPS:
        _VISIBLE_CLIPS[(cat, anim)] = _PoseClip(cat, anim)
    return _VISIBLE_CLIPS[(cat, anim)]


def _place(base, rel):
    """Root `rel` (x, y, z, yaw from a clip's start) placed where `base` stands."""
    bx, by, bz, byaw = base
    x, y, z, yaw = rel
    c, s = math.cos(byaw), math.sin(byaw)
    return (bx + c * x + s * z, by + y, bz - s * x + c * z, byaw + yaw)


def release_visible(cat, hold, release, at, loop=None, threshold=None):
    """Real frames from a release to the first frame the defender can see it: the release switches
    from clip `hold` to clip `release` (TimeAct category `cat`) at real frame `at` of `hold`, and
    the engine blends into `release` over its type-16 event's end time on Havok's smooth curve
    3w^2 - 2w^3 (`VERIFIED` FUN_141499950, `_PoseClip`), so on the switch frame the screen still
    shows the hold. Per real frame k = 0, 1, ... after the switch, the released pose (each bone's
    local transform blended at that weight, translation and scale linear and rotation nlerp on the
    short arc, as hkbBlendingTransitionEffectUtils::generateInternal blends; the root's per-frame
    motion blended alike, the release clip placed where the hold stood) against the held one (the
    hold clip running on, then `loop` when given, the last frame of the last one past its end): the
    first k at which some bone, `VISIBLE_SKIP` left out, is `threshold` (default `VISIBLE_M`) m or
    more from where the hold has it; `VISIBLE_SCAN` when none is within it or within the release
    clip. Root motion and TAE speed events count. Cached per (category, clips, frame, threshold).
    A threshold of 0 is 0: the switch frame, as releases were read before. `INFERRED`: whether the
    blend timer reads 0 or one frame on the switch frame (a frame either way); that a pose 10 cm off
    is seen."""
    threshold = VISIBLE_M if threshold is None else float(threshold)
    key = (cat, hold, release, round(float(at), 6), loop, threshold)
    if key in _VISIBLE:
        return _VISIBLE[key]
    if threshold <= 0:
        _VISIBLE[key] = 0
        return 0
    P = _pose()[0]
    sk = P.load_skeleton()
    keep = [i for i, nm in enumerate(sk.names) if not any(s in nm for s in VISIBLE_SKIP)]
    clips = [_pose_clip(cat, hold)] + ([_pose_clip(cat, loop)] if loop is not None else [])
    rel = _pose_clip(cat, release)

    def held(real_s):
        # The held side's local pose and root (from the hold clip's start) at real seconds.
        base = (0.0, 0.0, 0.0, 0.0)
        for i, c in enumerate(clips):
            if real_s <= c.real_length or i == len(clips) - 1:
                t = c.clip_t(min(real_s, c.real_length))
                return P._local_pose(sk, c.anim, t), _place(base, c.anim.root_motion(t))
            base = _place(base, c.anim.root_motion(c.length))
            real_s -= c.real_length

    def model(pose, root):
        x0, y0, z0, yaw = root
        c, s = math.cos(yaw), math.sin(yaw)
        return [(c * p[0] + s * p[2] + x0, p[1] + y0, -s * p[0] + c * p[2] + z0)
                for p, _q, _s in P._forward_kinematics(sk, pose)]

    def blend(pf, pt, w):
        if w >= 1.0:
            return pt
        if w <= 0.0:
            return pf
        out = []
        for (tf, qf, sf), (tt, qt, st) in zip(pf, pt):
            sgn = -1.0 if sum(a * b for a, b in zip(qf, qt)) < 0 else 1.0
            q = tuple((1 - w) * a + w * sgn * b for a, b in zip(qf, qt))
            nq = math.sqrt(sum(v * v for v in q)) or 1.0
            out.append((tuple(a + (b - a) * w for a, b in zip(tf, tt)), tuple(v / nq for v in q),
                        tuple(a + (b - a) * w for a, b in zip(sf, st))))
        return out
    start = float(at) / TAE_FPS
    _, root0 = held(start)
    acc, prev_h, prev_r = root0, root0, root0
    out = VISIBLE_SCAN
    for k in range(VISIBLE_SCAN + 1):
        if k / TAE_FPS > rel.real_length:
            out = k
            break
        ph, rh = held(start + k / TAE_FPS)
        tr = rel.clip_t(k / TAE_FPS)
        pr = P._local_pose(sk, rel.anim, tr)
        rr = _place(root0, rel.anim.root_motion(tr))
        w = 1.0 if rel.blend_s <= 0 else min(1.0, (k / TAE_FPS) / rel.blend_s)
        w = 3 * w * w - 2 * w * w * w
        if k > 0:
            acc = tuple(acc[i] + (1 - w) * (rh[i] - prev_h[i]) + w * (rr[i] - prev_r[i]) for i in range(4))
        pb, phm = model(blend(ph, pr, w), acc), model(ph, rh)
        if max(math.dist(pb[i], phm[i]) for i in keep) >= threshold:
            out = k
            break
        prev_h, prev_r = rh, rr
    _VISIBLE[key] = out
    return out


def skill_release_visible(t, sword_arts_id, release_anim, at, threshold=None):
    """`release_visible` of a charged skill's early release: the opening (`release_anim` - 1) let
    go at real frame `at` of it into `release_anim`, in the skill's TimeAct (`SKILL_TAE_BASE` +
    `swordArtsTypeNew`). 0 when a clip of the pair has no unpacked animation to read (the switch
    frame, as before)."""
    cat = SKILL_TAE_BASE + t.arts[sword_arts_id]['swordArtsTypeNew']
    try:
        return release_visible(cat, release_anim - 1, release_anim, at, threshold=threshold)
    except (FileNotFoundError, KeyError, ValueError, IndexError, TypeError):
        return 0


def release_fill(got, n):
    """Per release 0 .. n - 1: (a, b, share), the release being (1 - share) x `got[a]` + share x
    `got[b]`, a and b the releases read on either side (`release_read`); (i, i, 0.0) for one read."""
    idx = sorted(got)
    out = []
    for i in range(n):
        if i in got:
            out.append((i, i, 0.0))
            continue
        k = bisect.bisect_left(idx, i)
        a, b = idx[k - 1], idx[k]
        out.append((a, b, (i - a) / (b - a)))
    return out


def skill_release_part(t, weapon_id, sword_arts_id, ctx, level, defender=None, def_module=None, damage_fn=None,
                       poises=None, stance=None):
    """The early release of a charged skill's opening as a part of `skill_option`'s ({'hits', 'offset',
    'cue'}: the release clip's hits and its press, the window's first frame from the skill's start,
    `skill_releases`), what the hold looks like up to that frame (`skill_reaction`'s `look`), plus
    'releases': [(real frames after that press, weight)] of `release_frames` over the window, each
    a later release of the same clip (`lookalike_pair`), and 'shows': per release the real frames
    from it to the first frame he can see it (`skill_release_visible`); None when the opening has no
    early release or it has no hit."""
    rel = skill_releases(t, sword_arts_id, weapon_id, level, ctx_grip(ctx), stance)
    if not rel:
        return None
    o = skill_option(t, weapon_id, sword_arts_id, ctx, level, defender, def_module, damage_fn, poises,
                     follow=rel[0], stance=stance)
    parts = (o or {}).get('parts') or []
    if len(parts) < 2 or not parts[1].get('hits'):
        return None
    first = float(rel[0]['first'])
    frames = release_frames(first, rel[0]['last'])
    # 'roll': the attacker's roll frame after the release let go at the window's first frame, which
    # a later release moves with it (`lookalike_pair` `other_roll`, version 25).
    return {**parts[1], 'releases': [(r - first, w) for r, w in frames], 'roll': o.get('roll'),
            'shows': [skill_release_visible(t, sword_arts_id, rel[0]['anim'], r) for r, _ in frames]}


def skill_option(t, weapon_id, sword_arts_id, ctx, level, defender=None, def_module=None,
                 damage_fn=None, poises=None, follow=None, stance=None):
    """One skill on one weapon as built: its damage, commitment, stagger share, FP and the buff
    SpEffects it puts on its user.

    With `follow` (one `skill_followups` entry) the option is the opening and then that follow-up,
    pressed at its first frame: both animations' hits, the follow-up's recovery, the FP of both
    presses, and `parts` so each is landed and reacted to on its own (section 16).

    With `stance` ('r1' / 'r2', one of the profile's `stance_buttons`) a stance skill is cast
    into that button's attack: its hits, its recovery, its own FP (`stance_fp`) and the stance
    entry in front (`skill_commit`). `variant` names it ('stance R2 040070')."""
    if follow is not None and follow.get('stance'):
        stance = follow['stance']
    prof = skill_profile(t, sword_arts_id, weapon_id, level, ctx_grip(ctx), stance)
    hits = skill_hits(t, weapon_id, sword_arts_id, ctx, level, stance=stance)
    commit = skill_commit(t, weapon_id, sword_arts_id, prof, hits) if hits else {}
    parts = [{'hits': hits, 'offset': commit.get('first_offset') or 0.0, 'cue': commit.get('cue') or 0.0}]
    fp = stance_fp(t, sword_arts_id, prof['stance_button']) if prof.get('stance_button') \
        else skill_fp(t, sword_arts_id)
    buffs = [s['id'] for s in skill_buffs(t, prof) if s.get('effect') == 'buff']
    if follow is not None:
        more = skill_hits(t, weapon_id, sword_arts_id, ctx, level, anim=follow['anim'])
        if not more:
            return None
        events = (skill_tae(t, sword_arts_id) or {}).get(follow['anim']) or []
        after = max(h['frame'] for h in more) / TAE_FPS
        rec = anim_recovery(t, weapon_id, sword_arts_id, follow['anim'], after)
        press = (commit.get('first_offset') or 0.0 if hits and hits[0].get('anim') == main_anim(prof) else 0.0) \
            + follow['first']
        hit_real = press + ATK.real_frame(ATK.clip_to_real(events)(after)) if events else None
        commit = {'roll': press + rec['dodge'] if rec.get('dodge') is not None else None,
                  'next': press + rec['r1'] if rec.get('r1') is not None else None, 'lead': press,
                  'hit_windows': [(hit_real, hit_real)] if hit_real is not None else [],
                  'cancel_frame': {k: (press + v if v is not None else None) for k, v in rec.items()},
                  'first_offset': parts[0]['offset'], 'cue': parts[0]['cue']}
        parts.append({'hits': more, 'offset': press, 'cue': press})
        hits = hits + more
        fp += t.arts[sword_arts_id][f"useMagicPoint_{follow['button'].upper()}"]
        buffs += [s['id'] for s in skill_buffs(t, prof, anim=follow['anim'])
                  if s.get('effect') == 'buff' and s['id'] not in buffs]
    # Hits that share a hit priority record land once between them, a bullet launch's rows
    # together (bd er-effects-rs-dnok, er-effects-rs-xb3i), and one row per bullet hit list
    # (`HIT_LIST_NOTE`): `pvp_damage` sums what `cast_mask` lands.
    dmg = pvp_damage(t, weapon_id, hits, defender, def_module, damage_fn) if hits else 0.0
    stagger = None
    if hits and poises:
        top = 0.0
        for h in hits:
            fr = t.final_rate.get(h['final_rate_id']) if h['final_rate_id'] >= 0 else None
            top = max(top, (h.get('poise') or 0.0) * (fr['saRate'] if fr else 1.0))
        stagger = sum(p < top for p in poises) / len(poises)
    return {'sword_arts_id': sword_arts_id, 'name': t.arts_name(sword_arts_id), 'classes': prof['classes'],
            'fp': fp, 'hits': len(hits), 'dmg': dmg,
            'best_hit': max((h['pvp_damage'] for h in hits), default=0.0),
            'stagger': stagger, **{k: commit.get(k) for k in ('roll', 'next', 'lead', 'hit_windows',
                                                               'cancel_frame', 'first_offset', 'cue')},
            'fp_anims': prof['fp_anims'], 'last_atk_row': hits[-1]['atk_row'] if hits else None,
            'parryable': skill_parryable(t, sword_arts_id, {h.get('anim') for h in hits}) if hits else None,
            'hit_rows': hits, 'buff_roots': buffs, 'parts': parts,
            'variant': ' '.join(v for v in (stance_label(prof), follow and
                                             f"then {follow['button'].upper()} {follow['anim']:06d}") if v)
            or None, 'stance': prof.get('stance_button')}


# Bullet flight (ashes-of-war.md section 15). The per-frame rule and the child hand-over are
# `VERIFIED` in the 1.16.2 code (bd bullet-launch-geometry-for-skill-reach-2026-09-29); the
# addresses are those functions in the 2.7.2.0 image:
#   FUN_14039ac30 / FUN_14039f860  v += (-g Y + a v_hat) dt, |v| clamped to [minV, maxV],
#                                  p += v dt; `gravity/accelInRange` while the distance flown is
#                                  <= `dist`, the `OutRange` pair after; acceleration starts after
#                                  `accelTime` s
#   FUN_14039bbb0                  HitBulletID is created when the parent hits or expires, at the
#                                  contact point or the parent's position
#   FUN_14039da50                  the `launchConditionType` gate: 0/3 always, 5 on a hit,
#                                  4 on expiry, 1/2 on water, 254/255 never; on a hit the
#                                  parent's own value decides, on expiry the child's (`launches`)
#   FUN_140390c30                  `EmittePosType` 2 = 1.0 m above the emit point, flying level
#   FUN_14039ab20                  an interval child starts at the parent's position when it spawns,
#                                  never when the parent is made (`interval_frames`)
# Placement of the root bullet (the launch dummy poly) is not decoded: `BULLET_ORIGIN_M` ahead at
# `BULLET_ORIGIN_HEIGHT_M` (`INFERRED`). The ground is flat, no character is hit on the way, and
# homing is off (no target), so the reach is how far the skill carries when it misses.
#
# Lock-on aim (`VERIFIED` 2026-11-09, 1.16.2 named dump; `fly_tree` with a `target`). Every shot,
# root or child, is built by FUN_140390c20 (2.7.2.0 FUN_140390c30, the list above). When
# `EmittePosType` is 0 or 2 (`(type & 0xfd) == 0`), the spawn request's target slot holds a
# character (FUN_140394d70: type byte 1-3 or 5) and the request's +0x190 int is not 1, it turns the shot's base frame toward the
# target's position (FUN_140394910) with FUN_14038f120: within `lockShootLimitAng` of the target
# direction it snaps onto it, else it turns by that angle toward it (the cone is the byte at
# BulletParam +0x91, which the call site loads into XMM3 and multiplies by deg->rad; +0x91 is
# `lockShootLimitAng` in the paramdef layout). The shoot angles are applied after the aim. A child
# keeps its parent's target: FUN_14039bba0 copies the parent's attack info (FUN_14038b740, whose
# +0x140 `NetSyncData_` target struct it copies with FUN_140393ce0), never writes that struct, and
# writes 0 to the +0x190 int; the manager entry holds the request at +0x330 (FUN_14038fe80 calls
# FUN_14038d130(entry + 0x330, request), RDX untouched), so the entry's +0x470 / +0x4c0 that
# FUN_140390c20 reads are those two. So a chain
# of expiry-made segments re-aims at the target at every link: Glintstone Nails' speed-0 stops
# (`lockShootLimitAng` 90) and its moving legs (13). The stops' `homingAngle` 999 does nothing: a
# bullet at speed 0 flies nothing, so it never passes `homingBeginDist`, and its speed is not
# above 1e-4 (the in-flight homing below). FUN_14038f120 turns the whole 3D forward, and the point
# is the target's lock-on DummyPoly (220 on c0000, 1.24 m up on the idle pose, `lock_point`) plus
# `targetYOffsetRange` (decoded 2026-11-12, "Launch frames" below). Every flight takes it (round 4:
# `row_shots` in `fly_members`, which flies the punish geometry, and the spell record counts,
# `er-mechanics-spells._fly_records`; until then the punish geometry turned the horizontal heading
# only). That the caster is locked on to the defender, so the target slot holds him, is `INFERRED`.
# The aim point is his position, not a lead: `predictionShootObserveTime` (+0xb8), which would lead
# it, is 0 on every player bullet (below).
#
# `predictionShootObserveTime` (bd er-effects-rs-7bye, round 4; `VERIFIED` 1.16.2 named dump and
# objdump of `eldenring-deobf.bin`). FUN_140390c20 reads it right after the aim point (0x140390f32
# `ucomiss xmm12, [rdi+0xb8]`): when it is not 0 the point becomes FUN_1403adff0's. That one asks the
# target's `CSTargetVelocityRecorder` (FUN_1403940e0 -> FUN_1403e9340 -> FUN_1402dbb80) for his mean
# velocity over the last `predictionShootObserveTime` seconds (it sums the newest of its 240 frame
# records, each a move and its dt written by FUN_1402dbdd0 / FUN_1402da5d0, until their time passes
# that, and divides the move by the time), then FUN_1403ae720 steps him and the bullet forward at
# 1/30 s (.rdata 0x14329e630; the bullet from `initVellocity` with `accelInRange`, then
# `accelOutRange` past a distance FUN_140d33d10 reads off the row, within `minVellocity` /
# `maxVellocity`) until the bullet has flown as far as he is from the spawn point, and returns where
# he is then. A standing target, or one with no velocity record,
# keeps his position. The second call (0x140390fe7) only fills a debug record. No player bullet sets
# it: of the 21 Bullet rows that do (all of 15,475), none is reached from a `BehaviorParam_PC`
# `refType` 1 row, a Magic `refCategory` 1 ref or an `EquipParamGoods` `refId_default` through
# `HitBulletID` and `intervalCreateBulletId` (`REGULATION` scan, 2026-11-12), so no flight here leads.
#: `EmittePosType` values whose shots are aimed at the target (FUN_140390c20, `VERIFIED`).
AIM_EMIT_TYPES = (0, 2)

#: Emit point of a root bullet whose DummyPoly is not placed (`bullet_launch`), metres ahead of
#: where the caster's root is at the event (`INFERRED` placeholder: about an arm and a grip).
BULLET_ORIGIN_M = 1.0
#: Height of that emit point above the caster's root (`INFERRED` placeholder).
BULLET_ORIGIN_HEIGHT_M = 1.0
#: `EmittePosType` 2 raises the emit point by this much (`VERIFIED` .data 0x143b1cb5c, read only by
#: FUN_140390c30).
EMIT_RAISE_M = 1.0
EMIT_RAISED_LEVEL = 2
#: `FollowType` values that attach the bullet to the shooter or target instead of free flight.
FOLLOW_ATTACHED = (1, 2, 3, 5)
#: `launchConditionType`: which parent endings create a `HitBulletID` child (`VERIFIED` FUN_14039da50;
#: whose value is read is `launches`'). 6 passes only when the gate's third argument is 0: never on
#: expiry, where `KillBullet` passes 0 (`VERIFIED`), and on a hit by a flag of the hit record that is
#: not traced, taken as passing (`INFERRED`).
LAUNCH_ON = {0: ('hit', 'expire'), 3: ('hit', 'expire'), 6: ('hit',), 5: ('hit',), 4: ('expire',)}
#: `attachEffectType` values that make the gate read the parent's value on expiry too
#: (FUN_140d34100: `attachEffectType - 4 < 3`).
GATE_KEEPS_PARENT = (4, 5, 6)


def launches(t, parent, child, ending):
    """Whether Bullet row `parent` makes its `HitBulletID` child `child` when it ends by `ending`
    ('hit': on a character or the map; 'expire'). `VERIFIED` 2026-11-12, 1.16.2 named dump (bd
    er-effects-rs-rggx): FUN_14039da40 reads `launchConditionType` on the row of the state at
    CSBulletIns +0x4a0, the state index. A hit (FUN_14039dcc0 -> FUN_14039baf0) leaves the index
    on the fly or wait state, whose row is the parent's own (FUN_14039ee00); `KillBullet` moves it
    to the explosion state, whose row is the child's, before the expiry gate. It takes the row of
    the state at +0x4a4 (the parent's own) instead when that row's `attachEffectType` is 4 to 6
    (FUN_140d34100). An interval child passes no gate (FUN_14039ab10 calls FUN_14039bba0 itself)."""
    p = t.bullet.get(parent) or {}
    if ending == 'hit' or p.get('attachEffectType', 0) in GATE_KEEPS_PARENT:
        lct = p.get('launchConditionType', 0)
    else:
        lct = (t.bullet.get(child) or {}).get('launchConditionType', 0)
    return ending in LAUNCH_ON.get(lct, ())
BULLET_FPS = 60
#: Flight time simulated for a bullet whose `life` is -1 (no timer).
BULLET_LIFE_CAP_S = 5.0
#: How long a searching attached bullet hovers before its search may launch it (`VERIFIED`
#: 2026-11-16, 1.16.2 named dump :8765 + eldenring-deobf.bin; addresses 1.16.2, not yet found on
#: 2.7.2.0). FUN_14039ee00 gives a bullet that FUN_140399390 starts attached (`FollowType` 1, 2 or 5)
#: the state list .data 0x142a25838 [1, 2, 3] when its row's `autoSearchNPCThinkID` (+0x64) is
#: above 0, else [1, 3]: state 1 (CSBulletIns +0xd90, ctor FUN_1403ab900) is the hover, 2 the fly
#: state on the `HitBulletID` row, 3 the explosion. State 1's begin (vtable 0x142a274a0 slot 3,
#: 0x1403ab9c0) sets its timer (+0x28) to 2.0 (.data 0x143b15c20); its update (slot 2,
#: 0x1403abc00) follows the owner (FUN_14039afc0) and, while the timer is above 0, only counts it
#: down; at 0 or below it runs the bullet's targeting system (+0xb60, a CSTargetingSystemBase
#: 0x14033cf30 over the owner interface at +0xb30, FUN_140399b80) and, with a target, files a
#: launch request with CSBulletManager (FUN_1403992b0 -> FUN_1403a43d0). CSBulletManager::
#: UpdateNetworking (0x1403a7d50) moves it to the list FUN_14039ef00 reads each UpdateBullet: that
#: sets the target, turns the bullet toward it (FUN_140397840) and sets +0xd81 bit 1, and the
#: hover ends into the fly state. The targeting system's think row is the state row's
#: `autoSearchNPCThinkID` (FUN_1403ac750 -> GetNpcThinkParam at the launch), and the owner
#: interface gives the bullet's position with the caster's facing (slot 7, 0x1403ac2d0:
#: GetPhysicsOrientation of the owner's ChrCtrl) and team. So an endless searching row launches
#: no sooner than this, and then when the caster faces a target into its think row's sight
#: (NpcThinkParam 1000100, the spells' blades: eye_dist 13, eye_angX 60, eye_angY 50; 1000102,
#: the Glintblade Phalanx ash: 13, 30, 12; which eye fields the targeting system reads is
#: `INFERRED`, the accessors 0x140d41f80-0x140d42000 feed the AI perception code, FUN_14032c2b0,
#: not traced from +0xb60). The model launches it at the earliest, `flight_moves`.
SEARCH_ARM_S = 2.0
#: `FollowType` values FUN_140399390 starts attached ((FollowType & 7) - 1 & 0xfa == 0 and not 6).
SEARCH_ATTACHED = (1, 2, 5)


def search_launched(r):
    """Whether Bullet row `r` hovers attached and is launched by its own target search
    (`SEARCH_ARM_S`): an endless row (`life` -1) that starts attached with an `autoSearchNPCThinkID`."""
    return r.get('life', 0) < 0 and int(r.get('FollowType') or 0) in SEARCH_ATTACHED \
        and (r.get('autoSearchNPCThinkID') or 0) > 0


def search_cues(t, entries):
    """Per entry of `fly_shots` output (its order, its `keys`), the seconds after the bullet event
    from which that shot's timing is readable to the defender: 0.0 (the cast shows it), or, for a
    shot a searching row launches (`search_launched`: its `HitBulletID` child, made when the hover
    ends) and every shot below it, that launch. The caster picks the launch (`VERIFIED` above
    `SEARCH_ARM_S`: the search faces the caster's facing), so the defender's reaction runs from it,
    not from the cast (`burst_cue`); the model launches at the earliest, `SEARCH_ARM_S`."""
    out = []
    for i, e in enumerate(entries):
        p = e[5]
        if p < 0:
            out.append(0.0)
            continue
        launched = search_launched(t.bullet.get(entries[p][0]['bullet']) or {}) and entries.keys[i][-1][1] == 'h'
        out.append(float(e[2]) if launched else out[p])
    return out


def tree_cue(t, entries, chosen, start):
    """`CUE` of a geometry made of `fly_shots` entries `chosen` (indices) of a bullet event at
    `start` real seconds: the real frame its timing is readable from when every chosen shot is
    under a search launch (`search_cues`), the earliest of them; None when one is not (the cast
    shows it). A hovering searching row's own samples are left out of that choice: they follow the
    cast point (`FollowType` 1) for the whole hover and never come toward him, so they carry no
    timing to read (`INFERRED`; the Glintblade Phalanx ash's root 2300 carries its blades'
    AtkParam 300200867, so a hit's entries include it)."""
    cues = search_cues(t, entries)
    got = [cues[i] for i in chosen if not search_launched(t.bullet.get(entries[i][0]['bullet']) or {})]
    if not got or min(got) <= 0.0:
        return None
    return (start + min(got)) * TAE_FPS


def burst_cue(geoms, cue=0.0):
    """The real frame a defender reads burst `geoms`' timing from: the latest of `cue` (the clip's
    start, or the part's) and the earliest frame any geometry of the burst is readable from. A
    geometry is readable from the later of its search launch (`CUE`, `tree_cue`) and the press
    the attacker times to start the attack (`PRESS_CUE`: a stance's button or a hand-over he
    times, `skill_commit` 'cue', which `er-mechanics-spells.SpellModel.skill_attack` puts on every
    geometry of the skill, version 23), else from 0. So a burst is read from a search launch only
    when every geometry of it carries one, as before. `reaction_outcome`, `_volley_burst`,
    `_burst_presses`, `er-mechanics-spells.CastThreats.dynamic_outcome` and
    `er-mechanics-attack-similarity._reaction_form` time the dodge from it; a strike or a strafe
    needs no timing read and runs from the attack's start (`FollowupModel.strafe_escape`)."""
    rs = [max(g.get('CUE') or 0.0, g.get('PRESS_CUE') or 0.0) for g in geoms if g is not None]
    return max(cue, min(rs)) if rs else cue
#: Interval children simulated per parent, spread over its flight.
BULLET_INTERVAL_CAP = 12
#: Slack on `interval_frames`' frame arithmetic, in frames: a child due on the very update its
#: parent's life runs out is not made (Stars of Ruin's 0.1 s timers at 0.05 s: one star each).
INTERVAL_EPS = 1e-6


def interval_frames(r, frames=None, interval=None, first=None, life=None):
    """Path indices (moves made before it, as `bullet_flight` counts them, at `BULLET_FPS`) at which
    Bullet row `r` makes its `intervalCreateBulletId` child. `VERIFIED` 1.16.2 named dump
    (2026-11-12; 1.16.2 addresses): the launch, FUN_14039e8a0, sets the interval timer
    (CSBulletIns +0xb20) to rand[`intervalCreateTimeMin`, `intervalCreateTimeMax`] and the wait
    timer (+0xb24) to 0, and makes no child; each update, `CSBulletIns::UpdateBullet` 0x14039a100
    takes dt off the life and adds it to the wait timer before the fly state's OnUpdate
    (0x1403939d0, every update, the first included) calls FUN_14039ab10, which returns while
    `intervalCreateTimeMax` is 0, while the life just left is in (-1, 0] or while the wait timer is
    below `intervalCreateWaitTime`, else takes dt off the interval timer and, once that is <= 0,
    makes the child at the bullet's position (before this update's move) and adds a fresh
    interval to the timer (FUN_14039ed30, the remainder kept). So the j-th child comes
    ceil(j x interval) updates after the wait has run, and only while life is left: a 0.1 s timer
    at 0.05 s makes one, not three, which is the twelve stars Stars of Ruin's `GoodsInfo` names.
    `interval` defaults to `intervalCreateTimeMin` (the most children the draw allows); `first`
    (seconds, default `interval`) is the first draw, the timer the launch sets, and every later
    child comes `interval` after the one before (the refill FUN_14039ed30 adds a fresh draw to the
    remainder, `VERIFIED`; the flights take its mean, `draw_quantile`). `frames` is the flown
    path's length (None: the row's own life, and nothing for an endless row). A row flown as one
    point (`FOLLOW_ATTACHED` with no follow point, a length of 1) keeps the one-point rule it had:
    a child at its start when it has no wait."""
    if r.get('intervalCreateBulletId', -1) <= 0 or not r.get('intervalCreateTimeMax'):
        return []
    if frames is not None and frames <= 1 and r['FollowType'] in FOLLOW_ATTACHED:
        return [0] if r['intervalCreateWaitTime'] <= 0 else []
    # `life`: the bullet's own, its row's plus the drawn `lifeRandomRange` share (`shot_draws`).
    life = r['life'] if life is None else life
    if -1 < life <= 0 or (life <= -1 and frames is None):
        return []
    step = max(r['intervalCreateTimeMin'] if interval is None else interval, 1.0 / BULLET_FPS) * BULLET_FPS
    lead = step if first is None else max(first, 1.0 / BULLET_FPS) * BULLET_FPS
    wait = max(1, math.ceil(r['intervalCreateWaitTime'] * BULLET_FPS - INTERVAL_EPS))
    out = []
    while True:
        n = wait - 1 + math.ceil(lead + len(out) * step - INTERVAL_EPS)
        if (life > 0 and n >= life * BULLET_FPS - INTERVAL_EPS) or (frames is not None and n > frames):
            return out
        out.append(n - 1)
#: Longest bullet reach reported (`INFERRED` placeholder): the reach flies with no target, so a
#: homing bolt flies straight there (`homes`), and the cap sits above the 5.6 m where er-builds-pvp's reach factor saturates
#: (`SCORE_REACH_REF_M` 2.5 x 1.5 ** 2), so it changes the shown value, not the score.
BULLET_REACH_CAP_M = 25.0
_FLIGHT = {}

# Launch point of a root shot (bd er-effects-rs-doax). `VERIFIED`: a root shot's base matrix is
# the caster's live DummyPoly matrix (FUN_140390c20 -> FUN_140391850 -> FUN_1403f13c0, bd
# bullet-launch-geometry-for-skill-reach-2026-09-29). `TAE`: the bullet event (2) and the cast
# event (64) name that DummyPoly in their first int: 9 on all four Swift Slash bursts
# (a877_040000), 10100 on 236 of the spell casts. `bullet_launch` places it with the clip's pose
# at the event's clip time, root motion and yaw included (`MEASURED` er-hkx-pose):
#   * an id under 1000 is the body model's (the prefix rule below): c0000.flver's dummy
#     (`er-mechanics-reach.body_dummies`) rides its attach bone, the reach model's body-shape rule
#     (`_shape_world_points`). Swift Slash's 9 sits 0.23 m ahead of the Pelvis (bind 1.07 m up);
#   * 10xxx / 11xxx: dummy xxx of the right-hand weapon's model 0 / 1 (a dual blade's twin), riding
#     R_Weapon / L_Weapon, the reach model's weapon-shape rule (`WEAPON_TO_BONE`); 20xxx / 21xxx the
#     left-hand weapon's, which the model does not know for a right-hand cast (`held_models`);
#   * any other id, a dummy not found, a shield's skill (its hand is not known, `held_models`), or
#     a clip with no unpacked HKX: the root's position at the event plus `BULLET_ORIGIN_M` ahead
#     and `BULLET_ORIGIN_HEIGHT_M` up (`INFERRED` placeholder); with no clip at all, the caster's
#     start position (the old placement).
#
# Which way the shot starts (`VERIFIED` 2026-11-10, 1.16.2 named dump on :8765; addresses 1.16.2).
# The event's orientation byte (`orient`) picks it: TAE 2 `BulletBehavior` (0x140426e60) hands
# Args byte 0xc, TAE 64 (FUN_140429db0) Args byte 5, to FUN_1403c0730 (TaeUseContext +0x48), and
# FUN_1403c0320 passes it as the last argument of FUN_1403f13c0, the launch-matrix builder. For a
# player outside its two special branches that goes to FUN_1403fb5f0:
#   * 0: position the dummy's, forward the character's physics orientation (FUN_1403fb0b0 ->
#     `ChrCtrl::GetPhysicsOrientation`; with `IsBulletAimAngleSet`, a bow's aim angles instead),
#     which is level: the caster's facing;
#   * not 0: the dummy's own frame (FUN_1403f51a0: forward its matrix's third row, normalised;
#     the other two rebuilt from it), position the dummy's.
# FUN_140390c20 then turns the shot toward the lock-on target (FUN_14038f120, in 3D, within
# `lockShootLimitAng`) unless the byte is 1. Over the regulation's TimeActs (`MEASURED`): skill
# bullet events 625 with 0 and 76 with 2 (Firebreather, Fires of Slumber, Flame Spear, the
# breaths); spell casts 696 with 0, 62 with 2 (the breaths, Comet Azur, Placidusax's Ruin) and
# Dragonmaw's 1. `INFERRED`: that a player casting a skill takes FUN_1403fb5f0 (FUN_1403f13c0's
# other branches need the main player's `chrFlags1c5 & 6` or an event-entity check, whose setters
# were not read); that the dummy matrix's third row is the FLVER dummy's forward vector (+0x10)
# carried by its bones (the FD4 location modifier that builds the matrix, vtable +0x68, was not
# read).
# So a shot with byte 0 heads level along the caster's facing and keeps the placeholder height,
# `BULLET_ORIGIN_HEIGHT_M` above the root (the dummy's own is kept as `dummy_y`): in game the 3D
# lock-on turn pitches it toward the target's aim point, and a player's shot targets kind 1, the
# `LockTgtMan` entry (FUN_14038e740 -> `PadManipulator` vtable +0xa0 -> FUN_140394f90, `VERIFIED`),
# which FUN_140394910 aims at through the entry's own stored position; where the lock-on manager
# writes that position was not read (a player's hit height is 1.5 m, `InitForPlayer` 0x140460050,
# but the 0.8 x hit height of FUN_140394910 is kind 5's, a follow target's). So the vertical part of
# the aim is not modelled and no height is assumed for it, and from a raised hand (Thunderbolt's
# DummyPoly 1 at 2.0 m, Glintblade Phalanx's at 2.1 m) a level shot would pass over every defender.
# A shot with any other byte leaves the dummy itself, height included, along the dummy's posed
# forward axis, pitch included (`_dummy_axes`), with no vertical turn either.
#
# One shot per event (`VERIFIED`, 1.16.2): `BulletBehavior` keeps running after an event's first
# call when Args byte 0xd is 1, but its shooter FUN_1403c2d60 spawns only on the first
# (`firstExecution`, request +0x10), as the cast shooter FUN_1403c1450 does, so the model's single
# root per event is the game's.
#
# Hit priority records (`VERIFIED` 1.16.2; modelled: `record_mask`, `skill_landing`, `CaughtModel`
# `HPR`, er-mechanics-attacks `HIT_RECORD` block, which carries the rule and its addresses). TAE event
# 341 (FUN_1404298d0, thread one) keeps one `DmgHitRecord` ("priorityRecord") in the caster's
# `HitPriorityRecord` slot Args byte 0 (0 below TAE version 0x25) for as long as the event runs
# without a gap (FUN_14049ad80 -> FUN_1404a49c0, carried frame to frame by FUN_1404a5090). An attack
# takes the record of the slot whose window covers its event time when its priority byte is not
# negative: a swing (FUN_1404428f0 and its goods and magic twins) always slot 0 (`xor edx,edx` at
# 0x140442d61) with priority Args byte 0xc; a skill bullet (FUN_1403c2d60) slot Args byte 0x10 with
# priority Args byte 0x12; a cast bullet (FUN_1403c1450) slot 0 (`xor edx,edx` at 0x1403c14fd). The
# record reaches a bullet's attack at +0x288 (spawn data +0xa0 -> bullet +0xaf0 -> FUN_1405271a0 in
# FUN_1403960a0), a swing's through FUN_140521970; the priority is the attack's +0x155 (its debug
# label is "damage priority") and is copied into each contact. Each frame DmgMan (FUN_140527c10)
# registers every attack's contacts in its record first (FUN_140521bf0 -> FUN_14051b210: per victim
# the first registrant at the lowest priority and its id; a strictly lower one replaces both and
# clears the landed flag), then resolves them in the same list order (FUN_140523710 ->
# FUN_14051b870): the entry's own id passes even after the flag is set, any other attack only
# while the flag is clear at the entry's priority; a refused one puts him in its own lists, so it
# never hits him; a pass sets the flag (FUN_14051b370) before the team gate and the damage are
# read. A swing's id is -1 (never the entry's own); a bullet's is its launch (`BulletAttackInfo`
# +0x658, the launch entry's id, which children copy), so a launch's copies and descendants are one
# attack. The attacks of one record land one hit per victim (a launch: all its rows), plus one for
# each later contact with a strictly lower priority; one frame's tie goes to the attack created
# first (DmgMan's list, reversed by each FUN_140527d40 pass). Swift Slash (a877 040000): the four
# bursts take slot 1's record (event 341 f27-34), both blades slot 0's (f28-38), all priority 0, so
# one burst and one blade reach a defender (four and two before bd er-effects-rs-dnok). Thread one
# runs before thread two each frame (HavokBehavior task, group 74, then ChrIns_BehaviorSafe, 76,
# whose first call is `RunTaeMulthreadThread2`) while `Behavior.EnableMultiThread` holds (default
# true in `CSHkBehManager`), and the slot's per-frame cleanup FUN_1404a4cb0 runs after both, in
# `ChrIns::PrePhysicsSafe1` (group 81), before DmgMan (120-122). Not read: the scheduler loop that
# walks the step table, and whether a debug property turns the flag off.
#
# Which model a prefixed id names (`VERIFIED`, 1.16.2): `PlayerIns::GetLocationMdlDmypolyModifier`
# (0x1406560e0) sends an id to `CSChrAsmModelIns::GetDmyPoly` (0x1409ea1c0), which takes the slot
# from FUN_140b659a0 and the dummy within it as id % 1000 (`GetDummyPolyGroup`, 0x140b65b10). With
# t = (id / 1000) % 100: 0 is the body model (c0000); 1-4 slots 2-5; 10-13 (and 30-33) slots 11-14;
# 20-23 slots 7-10; with `chrAsmArmStyle` 2 the 10-13 ids are read as 20-23. `INFERRED` from that
# redirect and from AtkParam 600000071's 11120/11100 on a dual blade's left blade: 10-13 are the
# right-hand weapon's models 0-3 and 20-23 the left-hand weapon's, model 1 of a dual blade being its
# twin in the left hand. Only one-handed casts are scored, so arm style 2 is not played.
#
# The defender's body stops the caster's root motion (`er-mechanics-reach.front_contact_times`):
# the root gets no nearer than `dist - 2 * PUSH_CAPSULE_RADIUS`, and what the clip would carry it
# past that is taken off. A shot is created where the held-back caster is when it launches, so a
# bullet sample carries `peak` (the clip's largest forward root motion up to the launch, as the
# reach model's `peak_forward`) and the launch time, and `_catches` takes off
# `max(0, peak - (d - 2 * PUSH_CAPSULE_RADIUS))` with `d` where the defender is at the launch.
#: DummyPoly ids from this one up carry a model prefix (FUN_140b659a0, above).
DUMMY_PREFIXED = 1000
#: (id / 1000) % 100 -> (the model in `held_models`, the bone that carries it): the right-hand
#: weapon's models 0 and 1 (FUN_140b659a0 slots 11, 12; 30/31 read the same slots) and the left-hand
#: weapon's (slots 7, 8). Model 1 of a right-hand dual blade is its twin, in the left hand.
DUMMY_WEAPON = {10: ('R0', 'R_Weapon'), 30: ('R0', 'R_Weapon'), 11: ('R1', 'L_Weapon'), 31: ('R1', 'L_Weapon'),
                20: ('L0', 'L_Weapon'), 21: ('L1', 'R_Weapon')}
#: FLVER dummy record: forward vector at +0x10, upward at +0x20 (`er-mechanics-reach` reads the rest).
DUMMY_FORWARD_OFFSET, DUMMY_UPWARD_OFFSET = 0x10, 0x20
_LAUNCH = {}
_BIND = []
_AXES = {}


#: `wepType`s of the shields (`WEP_TYPE_MOUNT_FLAG`): a shield's skill is cast from the off hand,
#: which the skill model does not play (`diff_category`), so the hand that holds it is not known.
SHIELD_WEP_TYPES = (65, 67, 69, 90)


def held_models(t, weapon_id, hand='right'):
    """{'R0': the weapon's `EquipParamWeapon.equipModelId`, 'R1': the same for a dual blade
    (`isDualBlade`: its twin), the left hand's 'L0' / 'L1' None}: the models `bullet_launch` reads a
    prefixed DummyPoly off (`DUMMY_WEAPON`). `hand` 'left' puts the weapon in the left hand ('L0',
    'L1'), the right hand's unknown. None for a shield, whose hand is not known: its launches fall
    back to the placeholder."""
    w = (t.reg.weapon.get(weapon_id) or {}) if t is not None else {}
    if w.get('wepType') in SHIELD_WEP_TYPES:
        return None
    reach = _pose()[1]
    if reach._RC is None:
        reach._RC = reach.Reach()
    model = reach._RC.model.get(weapon_id)
    pair = (model, model if w.get('isDualBlade') else None)
    side = ('L', 'R') if hand == 'left' else ('R', 'L')
    return {f'{side[0]}0': pair[0], f'{side[0]}1': pair[1], f'{side[1]}0': None, f'{side[1]}1': None}


def _dummy_axes(path):
    """{ReferenceID: [(forward, upward) in the model's space, `None` for a record whose parent bone
    is out of range]} of a FLVER's dummies, in file order (`er-mechanics-reach.read_flver` order):
    each vector carried through its parent bone's chain, rotation only. Cached per file."""
    if path not in _AXES:
        reach = _pose()[1]
        fl = reach.read_flver(path)
        with open(path, 'rb') as handle:
            b = handle.read()
        out = {}
        for i, d in enumerate(fl['dummies']):
            o = reach.FLVER_HEADER_SIZE + i * reach.FLVER_DUMMY_SIZE
            if d['parent'] >= len(fl['bones']):
                out.setdefault(d['id'], []).append(None)
                continue
            zero = reach.flver_bone_to_model(fl, d['parent'], (0.0, 0.0, 0.0))
            axes = []
            for off in (DUMMY_FORWARD_OFFSET, DUMMY_UPWARD_OFFSET):
                v = reach.flver_bone_to_model(fl, d['parent'], struct.unpack_from('<3f', b, o + off))
                axes.append(tuple(v[k] - zero[k] for k in range(3)))
            out.setdefault(d['id'], []).append(tuple(axes))
        _AXES[path] = out
    return _AXES[path]


def _flight_frame(p):
    """A model-space point (the character faces -Z) in the flight frame (+z ahead)."""
    return (p[0], p[1], -p[2])


def _first_axes(path, local):
    axes = [a for a in _dummy_axes(path).get(local, []) if a is not None] if path else []
    return axes[0] if axes else None


def _place_dummy(pose, reach, clip, clip_s, root, dummy, models):
    """(model-space position, model-space forward axis or None) of DummyPoly `dummy` at clip
    seconds `clip_s` of HKX clip `clip` ((category, anim)), root motion included, by the rules
    above; None when it is not placed. The axis is the FLVER dummy's forward vector carried as its
    position is (`_dummy_axes`)."""
    if dummy is None or dummy < 0:
        return None
    if dummy >= DUMMY_PREFIXED:
        slot, bone = DUMMY_WEAPON.get((dummy // 1000) % 100, (None, None))
        model = models.get(slot) if slot else None
        dm = reach.weapon_dummies(model) if model is not None else None
        pts = (dm[0] if dm else {}).get(dummy % 1000)
        if not pts:
            return None
        tr = pose.bone_model_transforms(*clip, clip_s, with_root_motion=True)
        if bone not in tr:
            return None
        origin, q = tr[bone]
        off = reach._qrot(q, tuple(c * s for c, s in zip(pts[0], reach.WEAPON_TO_BONE)))
        axes = _first_axes(dm[2], dummy % 1000)
        axis = reach._qrot(q, tuple(c * s for c, s in zip(axes[0], reach.WEAPON_TO_BONE))) if axes else None
        return tuple(origin[i] + off[i] for i in range(3)), axis
    body = reach.body_dummies()
    if dummy not in body:
        return None
    if not _BIND:
        _BIND.append(reach._hkx_bind(pose))
    tr = pose.bone_model_transforms(*clip, clip_s, with_root_motion=True)
    entry = body[dummy][0]
    placed = reach._shape_world_points({'body_points': [entry]}, tr, root, _BIND[0])
    if not placed:
        return None
    axes = _first_axes(reach.BODY_FLVER_PATH, dummy)
    axis = None
    if axes:
        bone = entry[1]
        if bone is None:
            yaw = root[3]
            axis = reach._qrot((0.0, math.sin(yaw / 2), 0.0, math.cos(yaw / 2)), axes[0])
        elif bone in _BIND[0] and bone in tr:
            local = reach._qrot(reach._qconj(_BIND[0][bone][1]), axes[0])
            axis = reach._qrot(tr[bone][1], local)
    return placed[0][0], axis


def root_offset(category, anim, clip_s):
    """{'pos': the caster's root in the flight frame, 'yaw': his facing (flight degrees, +x = 90),
    'peak': the clip's largest forward root motion up to `clip_s`} at clip seconds `clip_s` of TAE
    entry `anim` (the HKX it plays, `er-mechanics-reach.hkx_source`), or None without a clip."""
    pose, reach, _ = _pose()
    try:
        clip = reach.hkx_source(category, anim)
        root = pose.root_motion(*clip, clip_s)
        peak = max(reach._root_peak_forward(pose, *clip, clip_s)(clip_s), -root[2])
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return None
    return {'pos': _flight_frame(root[:3]), 'yaw': -math.degrees(root[3]), 'peak': peak, 'clip': clip,
            'root': root}


def bullet_launch(category, anim, clip_s, dummy=None, models=None, base=None, orient=0):
    """Where the root shot of a bullet / cast event at clip seconds `clip_s` of TAE entry `anim`
    (category `category`) is created and which way it starts: {'pos': (x, y, z) in the flight
    frame, metres from the caster's start position, 'yaw' / 'pitch': flight degrees (+x = yaw 90,
    up positive), 'aim': whether the lock-on turn applies to it, 'peak': forward root motion up to
    the launch (minus infinity with no clip: no hold), 'src': 'dummy axis' | 'dummy' | 'root' |
    'start', 'dummy_y': the placed dummy's own height} (rules above). `orient` is the event's
    orientation byte: 0 heads along the caster's facing, level, from the dummy's place at the
    placeholder height ('dummy'); any other byte along the dummy's own posed forward axis from the
    dummy itself ('dummy axis'), and 1 also skips the lock-on turn. `models` is `held_models`
    (None: the hands are not known, and no dummy is placed); `base` a `root_offset` the clip starts
    from (a spell's release clip after its start clip: the release's pose turned by the base yaw
    and moved by its position)."""
    key = (category, anim, round(clip_s, 5), dummy, None if models is None else tuple(sorted(models.items())),
           None if base is None else (tuple(round(c, 5) for c in base['pos']), round(base['yaw'], 4),
                                      round(base['peak'], 5)), orient)
    if key in _LAUNCH:
        return _LAUNCH[key]
    pose, reach, _ = _pose()
    ro = root_offset(category, anim, clip_s)
    aim = orient != 1
    if ro is None:
        out = {'pos': (0.0, BULLET_ORIGIN_HEIGHT_M, BULLET_ORIGIN_M), 'yaw': 0.0, 'pitch': 0.0, 'aim': aim,
               'peak': -math.inf, 'src': 'start', 'dummy_y': None}
    else:
        x, y, z, yaw = ro['root']
        try:
            placed = None if models is None else _place_dummy(pose, reach, ro['clip'], clip_s, ro['root'], dummy, models)
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            placed = None
        dummy_y, heading, pitch = None, ro['yaw'], 0.0
        if placed is not None and orient and placed[1] is not None:
            # The dummy's own frame (FUN_1403f51a0): from the dummy, along its forward axis.
            p, a = _flight_frame(placed[0]), _flight_frame(placed[1])
            n = math.sqrt(sum(c * c for c in a)) or 1.0
            a = tuple(c / n for c in a)
            pos, src, dummy_y = p, 'dummy axis', p[1]
            heading = math.degrees(math.atan2(a[0], a[2]))
            pitch = math.degrees(math.asin(max(-1.0, min(1.0, a[1]))))
        elif placed is not None:
            # Ahead and aside: the dummy's. Up: the placeholder above the root, since the shot
            # heads level along the facing and the lock-on turn's vertical part is not modelled
            # (above).
            p = _flight_frame(placed[0])
            pos, src, dummy_y = (p[0], y + BULLET_ORIGIN_HEIGHT_M, p[2]), 'dummy', p[1]
        else:
            # Facing after a yaw about +Y: model (-sin, -cos), so flight (-sin, +cos).
            pos = (x - BULLET_ORIGIN_M * math.sin(yaw), y + BULLET_ORIGIN_HEIGHT_M,
                   -z + BULLET_ORIGIN_M * math.cos(yaw))
            src = 'root'
        out = {'pos': pos, 'yaw': heading, 'pitch': pitch, 'aim': aim, 'peak': ro['peak'], 'src': src,
               'dummy_y': dummy_y}
    if base is not None:
        c, s = math.cos(math.radians(base['yaw'])), math.sin(math.radians(base['yaw']))
        px, py, pz = out['pos']
        bx, by, bz = base['pos']
        # A turn by the base yaw in the flight frame takes (0, 1) to (sin, cos).
        out = {**out, 'pos': (bx + px * c + pz * s, by + py, bz - px * s + pz * c), 'yaw': base['yaw'] + out['yaw'],
               'peak': max(base['peak'], bz + (out['peak'] if math.isfinite(out['peak']) else 0.0)),
               'dummy_y': None if out['dummy_y'] is None else by + out['dummy_y']}
    _LAUNCH[key] = out
    return out


def skill_bullet_launch(t, weapon_id, sword_arts_id, anim, frame, root_bullet):
    """`bullet_launch` of the bullet event at clip frame `frame` of skill clip `anim` whose root
    bullet is `root_bullet` (a `skill_hits` bullet hit's `anim` / `frame` / `root_bullet`), or None
    when the profile has no such event. A follow-up's clip is placed from its own start, as its
    melee hits are (`er-mechanics-reach.skill_reach`: the opening's travel is not added, `INFERRED`
    hand-over)."""
    prof = skill_profile(t, sword_arts_id, weapon_id)
    x = next((x for x in prof['anims'].get(anim, []) if x['kind'] == 'bullet' and x.get('bullet')
              and x['bullet']['bullet'] == root_bullet and x['frames'][0] == frame), None)
    if x is None:
        return None
    cat = SKILL_TAE_BASE + t.arts[sword_arts_id]['swordArtsTypeNew']
    clip_s = x['fr_s'][0] if x.get('fr_s') else frame / TAE_FPS
    return bullet_launch(cat, anim, clip_s, x.get('dummy'), held_models(t, weapon_id), orient=x.get('orient', 0))


def _launch_start(launch):
    """(position, yaw, pitch, aims) a root shot is fired from: `launch` or the old fixed
    placement; `aims` False when the event's orientation byte skips the lock-on turn."""
    if launch is None:
        return (0.0, BULLET_ORIGIN_HEIGHT_M, BULLET_ORIGIN_M), 0.0, 0.0, True
    return tuple(launch['pos']), launch['yaw'], launch.get('pitch', 0.0), launch.get('aim', True)


def _launch_key(launch):
    if launch is None:
        return None
    return tuple(round(c, 4) for c in launch['pos']) + (round(launch['yaw'], 3), round(launch.get('pitch', 0.0), 3),
                                                        launch.get('aim', True))


def _bullet_damages_self(bt):
    return any(bt['mv'].values()) or (bt['add_base_atk'] and any(bt['flat'].values()))


def _unit(yaw_deg, pitch_deg):
    y, p = math.radians(yaw_deg), math.radians(pitch_deg)
    return (math.cos(p) * math.sin(y), math.sin(p), math.cos(p) * math.cos(y))


# Launch frames in 3D (bd er-effects-rs-7bye, round 3). `VERIFIED` 2026-11-12 on the 1.16.2 named
# dump (:8765) and `eldenring-deobf.bin` (objdump); addresses are 1.16.2. A frame is its three
# rows (right, up, forward), +z forward and +y up as in `_unit`; the bullet flies along its
# normalised forward row (FUN_14039e8a0: v0 = initVellocity x normalise(row 3)).
#   * In flight the orientation follows the velocity whenever |v| > 1e-4 (FUN_14039f850 ->
#     FUN_14051a0e0 -> FUN_14051a520): forward v / |v|, up the world +Y with its part along
#     forward taken off, and (0, 0, 1) going straight down or (0, 0, -1) straight up (FUN_14051a3d0,
#     a 90 or -90 degree turn about +Y). A bullet at rest keeps its launch frame.
#   * A child's frame (FUN_14039bba0) is its parent's at that moment; `EmittePosType` 2 levels it to
#     the parent's heading, `isOwnerOverrideInitAngle` (+0x9c bit 6, read at 0x14039c697) replaces
#     it with the owner's `GetPhysicsOrientation`, and otherwise FUN_14039b500 applies the child's
#     `sfxPostureType` (+0xc0, read at 0x14039b52a): 1 turns the frame so its up is world +Y
#     (FUN_14038fc20, the shortest turn, FUN_1403ae200), 2 so its up is the hit normal (+Y on an
#     expiry, `KillBullet`; the ground's on a map hit). On a character hit the normal is the
#     contact's (round 4, `VERIFIED` chain): FUN_14039dcc0 passes hit record +0x10, which
#     FUN_14038c100 copies from AttackDamageInfo +0x1b0, which FUN_140523710 sets from its contact
#     entry +0x10 (DmgIns +0x238, FUN_140522590), which FUN_140521c80 fills from an hknp closest-hit
#     shape cast against the victim's body (the result's normal, w 0, beside the position, w 1). That
#     it is the victim's surface normal at the contact, as hknp hands it, is `INFERRED` (Havok's
#     query was not read). The model keeps +Y there: no player row's flight depends on it. Of the 434
#     `sfxPostureType` 2 rows made by a `HitBulletID` link from a player spell, skill or goods tree,
#     the 398 a non-penetrating parent can make on a character have no row that moves or turns by
#     shoot angles among themselves and the rows under them, leaving aside the rows whose frame is
#     not their parent's (owner override, `EmittePosType` 1 and 2) (`REGULATION` scan,
#     2026-11-12); the two that do (Magma Guillotine's 2863, Bloodboon's 10631001) have only
#     penetrating parents, so they are made on the map.
#   * Each shot (FUN_140390c20): `EmittePosType` 0 and 2 take the stored spawn frame (2: 1 m up,
#     .data 0x143b15b5c), turn it toward the lock-on point (FUN_14038f120 with lockShootLimitAng x
#     deg->rad in XMM3, 0x140391096) and apply the shoot angles; then every type gets the random
#     spread (`frame_spread`, flown as its expected layout, "Random draws" below) and
#     `sfxPostureType` 1 levels the shot itself
#     (`cmp byte [rdi+0xc0], 1` at 0x1403913bf). 5 keeps the stored frame, 1 is placed by
#     FUN_1403914b0 (`random_ground_point`); neither is aimed or angled.
#   * FUN_14038f120: the forward turns onto the direction to the point when within the limit, else
#     by the limit toward it about forward x direction; the frame is rebuilt with right = +Y x
#     forward and up = forward x right, both left at length cos(pitch) (not normalised), so a
#     limit of 0 keeps the forward and drops any roll.
#   * The shoot angles are a local rotation L x frame (`mtx44mul(&local_1a8, ...)`), L's rows
#     (cos y, 0, -sin y), (-sin p sin y, cos p, -sin p cos y), (cos p sin y, sin p, cos p cos y),
#     y = shootAngle + i shootAngleInterval, p = shootAngleXZ + i shootAngleXInterval (the angle is
#     negated, `xor` with -0.0 at .rdata 0x14329f470, and the up term carries the -1.0 of .rdata
#     0x1432792b4, so a positive p climbs). On a pitched frame this is not yaw + y, pitch + p.
#: Map hit, frame and follow constants (`VERIFIED`, above and `bullet_flight`).
MOVING_SPEED = 1e-4
#: FollowType 1, 3, 5 move this share of the way to their follow point per 1/30 s (FUN_14039afc0:
#: v = k x (P - p) / dt x dt / (1/30), k = FUN_1403a3dc0's .data 0x143b15bbc, 1.0 for FollowType
#: 2; .rdata 0x14329e630 is 1/30).
FOLLOW_RATE = 0.2
FOLLOW_RATE_FEET = 1.0
FOLLOW_STEP_S = 1.0 / 30
#: FollowType 5 returns to the owner's model point (0, hitHeight x 0.5, 0) (FUN_1403995b0 ->
#: FUN_140394350, kind 3, read by FUN_140394910 through the model matrix). A player's hit height
#: (`InitForPlayer`, the reach comment above).
FOLLOW_RETURN_SHARE = 0.5
PLAYER_HIT_HEIGHT_M = 1.5
#: The lock-on point of a player: his only DummyPoly in 220-228 (CS::ChrSlotSys::AddActPntSlot and
#: FUN_14049ce50 scan 0xdc-0xe4; FUN_140394910 returns the ActPntNode's cached position for the
#: lock target, kind 1), c0000.flver dummy 220 on Spine2. Its idle-pose place is `lock_point`.
LOCK_DUMMY = 220
_LOCK = []


def _vadd(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _vscale(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


def _vdot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _vcross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _vnorm(a, fallback=(0.0, 0.0, 1.0)):
    n = math.sqrt(_vdot(a, a))
    return _vscale(a, 1.0 / n) if n > 1e-12 else fallback


def _vrotate(v, axis, angle):
    """Rodrigues: `v` turned by `angle` radians about the unit `axis`."""
    c, s = math.cos(angle), math.sin(angle)
    kv = _vcross(axis, v)
    return _vadd(_vadd(_vscale(v, c), _vscale(kv, s)), _vscale(axis, _vdot(axis, v) * (1.0 - c)))


def look_frame(d):
    """FUN_14051a520's frame along direction `d` with the world +Y up (above)."""
    f = _vnorm(d)
    fy = f[1]
    if abs(fy) < 1.0 - 1e-7:
        u = _vnorm((-fy * f[0], 1.0 - fy * fy, -fy * f[2]))
    else:
        u = (0.0, 0.0, 1.0) if fy < 0 else (0.0, 0.0, -1.0)
    return (_vcross(u, f), u, f)


def yaw_frame(yaw_deg):
    """A level frame facing `yaw_deg` (+z 0, +x 90): the caster's, or a level child's."""
    return look_frame(_unit(yaw_deg, 0.0))


def frame_heading(frame):
    """(yaw, pitch) in degrees of the frame's normalised forward row."""
    f = _vnorm(frame[2])
    return math.degrees(math.atan2(f[0], f[2])), math.degrees(math.asin(max(-1.0, min(1.0, f[1]))))


def frame_level(frame):
    """`sfxPostureType` 1: the frame turned the shortest way until its up is world +Y."""
    r, u, f = (_vnorm(v) for v in frame)
    c = max(-1.0, min(1.0, u[1]))
    if c > 1.0 - 1e-9:
        return (r, u, f)
    axis = _vcross(u, (0.0, 1.0, 0.0))
    if math.sqrt(_vdot(axis, axis)) < 1e-9:
        axis = r
    axis = _vnorm(axis)
    ang = math.acos(c)
    return tuple(_vrotate(v, axis, ang) for v in (r, u, f))


def frame_shot(frame, yaw_deg, pitch_deg):
    """The shoot angles applied in `frame` (L x frame, above)."""
    y, p = math.radians(yaw_deg), math.radians(pitch_deg)
    cy, sy, cp, sp = math.cos(y), math.sin(y), math.cos(p), math.sin(p)
    local = ((cy, 0.0, -sy), (-sp * sy, cp, -sp * cy), (cp * sy, sp, cp * cy))
    r, u, f = frame
    return tuple(_vadd(_vadd(_vscale(r, a), _vscale(u, b)), _vscale(f, c)) for a, b, c in local)


def frame_aim(frame, pos, point, limit_deg):
    """FUN_14038f120: the frame at `pos` turned toward `point` within `limit_deg` (above)."""
    f = _vnorm(frame[2])
    d = _vnorm(tuple(point[i] - pos[i] for i in range(3)), f)
    limit = math.radians(limit_deg)
    if _vdot(d, f) <= math.cos(limit):
        axis = _vcross(f, d)
        n = math.sqrt(_vdot(axis, axis))
        nf = _vrotate(f, _vscale(axis, 1.0 / n), limit) if n > 1e-12 else f
    else:
        nf = d
    nf = _vnorm(nf, f)
    right = _vcross((0.0, 1.0, 0.0), nf)
    return (right, _vcross(nf, right), nf)


def child_frame(child, parent, owner):
    """FUN_14039bba0's frame for Bullet row `child` made by a bullet whose frame is `parent`, the
    owner facing as frame `owner` (above)."""
    if child.get('isOwnerOverrideInitAngle'):
        return owner
    if child['EmittePosType'] == EMIT_RAISED_LEVEL:
        f = parent[2]
        h = (f[0], 0.0, f[2])
        if math.sqrt(_vdot(h, h)) < 1e-9:
            # Straight up or down: the level turn of the up row (FUN_14038fc20's).
            return frame_level(parent)
        return look_frame(h)
    if child.get('sfxPostureType') in (1, 2):
        return frame_level(parent)
    return parent


def random_ground_point(r, owner_pos, want):
    """`EmittePosType` 1 (FUN_1403914b0 -> FUN_140390890): a point drawn uniformly in the square of
    half-side `randomCreateRadius` around the owner, dropped to the ground by a ray from his hit
    height (ten draws, a ground normal with y > 0.5), in a world-axis frame. The draw is random;
    this takes the one nearest `want` (x, z), the spot it lands the most on, as `record_hits`
    picks the defender's distance (`INFERRED` reading of `full`)."""
    R = max(r.get('randomCreateRadius') or 0.0, 0.0)
    x = min(max(want[0], owner_pos[0] - R), owner_pos[0] + R)
    z = min(max(want[1], owner_pos[2] - R), owner_pos[2] + R)
    return (x, 0.0, z), ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def lock_point(dist, bearing_deg=0.0):
    """The defender's lock-on point (`LOCK_DUMMY` on the idle pose, a000_000000 frame 0, `MEASURED`
    er-hkx-pose) when he stands `dist` m from the caster at `bearing_deg` (+x 90) facing him. Falls
    back to the middle of his idle hurtbox height without the pose decoder."""
    if not _LOCK:
        pose, reach, _ = _pose()
        got = None
        try:
            got = _place_dummy(pose, reach, (0, 0), 0.0, (0.0, 0.0, 0.0, 0.0), LOCK_DUMMY, {}) if pose else None
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            got = None
        if got:
            _LOCK.append(tuple(got[0]))
        else:
            prof = reach.defender_profile('idle') if reach else None
            mid = (min(prof) + max(prof)) / 2 * reach.DEFENDER_SLICE_M if prof else 1.0
            _LOCK.append((0.0, mid, 0.0))
    ox, oy, oz = _LOCK[0]
    b = math.radians(bearing_deg)
    cx, cz = dist * math.sin(b), dist * math.cos(b)
    # He faces the caster: his model -Z points back along the bearing, so model (x, z) turns by it.
    c, s = math.cos(b), math.sin(b)
    return (cx + ox * c + oz * s, oy, cz - ox * s + oz * c)


# Random draws (bd er-effects-rs-7bye, round 4). `VERIFIED` 2026-11-12, 1.16.2 named dump (:8765)
# and `eldenring-deobf.bin`; addresses are 1.16.2.
#   * The spread, FUN_140390210, which FUN_140390c20 calls for every `EmittePosType` once the aim
#     and the shoot angles are applied (LAB_140391367) and before `sfxPostureType` 1's levelling,
#     when `shootAngleXMaxRandom` or `shootAngleYMaxRandom` is not 0: a = rand[-X/2, X/2] and
#     b = rand[-Y/2, Y/2] from the spawn entry's CSRand (+0x9a0), and the frame becomes M x frame,
#     M's rows (cos b, sin b sin a, -sin b cos a), (0, cos a, sin a), (sin b, -cos b sin a,
#     cos b cos a) (the signs from .rdata 0x143279450, (1, 1, -1, 1)): the forward pitched by a
#     about the frame's right, then turned by b about the pitched up. Each field is the full width.
#   * The life, SetBulletLife 0x14039b990: `life` + rand[0, 1] x `lifeRandomRange` when life >= 0.
#   * The interval timer: rand[min, max] at the launch (FUN_14039e8a0), and a fresh rand[min, max]
#     added to the remainder at each child (FUN_14039ed30).
# Flown as the draw's expected layout (a modelling choice, not a game fact; `draw_quantile`): the n
# draws one bullet makes of a row (the row's `numShoot` shots, times its spawns for an interval
# child) take the n stratified quantiles of each uniform range, so they cover it as the draws do on
# average, and a single draw takes the middle, its mean (the nominal angles, life + range / 2, the
# mean first interval). Every interval after the first is the mean. So a fan's count and catch are
# those of its draws spread as expected, and one shot's are those of its mean draw, which is not the
# mean over its draws.
#: Steps (fractions of n) that order each variable's strata: the turn b, the pitch a, the life, the
#: first interval. Each gives a permutation of the n strata, so every variable keeps its mean.
DRAW_STEPS = (0.0, 0.6180339887498949, 0.7548776662466927, 0.5698402909980532)


def draw_quantile(i, n, var=0):
    """Draw `i` of `n` of uniform variable `var` (`DRAW_STEPS`' order) as a quantile in (0, 1): the
    middle of stratum pi(i), pi the variable's permutation of the n strata; 0.5 for one draw."""
    if n <= 1:
        return 0.5
    if var == 0:
        return (i + 0.5) / n
    s = max(1, round(n * DRAW_STEPS[var]))
    for d in range(n):
        for c in (s + d, s - d):
            if 1 <= c < n and math.gcd(c, n) == 1:
                return ((i * c) % n + 0.5) / n
    return (i + 0.5) / n


def shot_draws(r, i, n):
    """(pitch a, turn b, life, first interval) of draw `i` of `n` of Bullet row `r` (above)."""
    xr, yr = r.get('shootAngleXMaxRandom') or 0.0, r.get('shootAngleYMaxRandom') or 0.0
    a = xr * (draw_quantile(i, n, 1) - 0.5)
    b = yr * (draw_quantile(i, n, 0) - 0.5)
    life = r['life']
    if life >= 0 and (r.get('lifeRandomRange') or 0.0) > 0:
        life = life + draw_quantile(i, n, 2) * r['lifeRandomRange']
    lo, hi = r.get('intervalCreateTimeMin') or 0.0, r.get('intervalCreateTimeMax') or 0.0
    return a, b, life, lo + draw_quantile(i, n, 3) * (hi - lo)


def frame_spread(frame, a_deg, b_deg):
    """FUN_140390210's turn of `frame` by the drawn pitch `a_deg` and turn `b_deg` (above)."""
    a, b = math.radians(a_deg), math.radians(b_deg)
    ca, sa, cb, sb = math.cos(a), math.sin(a), math.cos(b), math.sin(b)
    m = ((cb, sb * sa, -sb * ca), (0.0, ca, sa), (sb, -cb * sa, cb * ca))
    r, u, f = frame
    return tuple(_vadd(_vadd(_vscale(r, x), _vscale(u, y)), _vscale(f, z)) for x, y, z in m)


# The same frame rules on k bullets at once (`fly_members`, `_fly_rows`): a frame array is (k, 3, 3),
# its rows right, up, forward. Each is written with the scalar helper's operations in its order.
def _bdot(a, b):
    return a[..., 0] * b[..., 0] + a[..., 1] * b[..., 1] + a[..., 2] * b[..., 2]


def _bcross(a, b):
    import numpy as np
    return np.stack([a[..., 1] * b[..., 2] - a[..., 2] * b[..., 1], a[..., 2] * b[..., 0] - a[..., 0] * b[..., 2],
                     a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0]], axis=-1)


def _bnorm(v, fallback=None):
    import numpy as np
    n = np.sqrt(_bdot(v, v))
    ok = n > 1e-12
    out = v * (1.0 / np.where(ok, n, 1.0))[..., None]
    if fallback is None:
        fallback = np.array((0.0, 0.0, 1.0))
    return np.where(ok[..., None], out, fallback)


def _brotate(v, axis, angle):
    import numpy as np
    c, s = np.cos(angle), np.sin(angle)
    kv = _bcross(axis, v)
    return v * c[..., None] + kv * s[..., None] + axis * (_bdot(axis, v) * (1.0 - c))[..., None]


def _blook(d):
    """`look_frame` of directions `d` (k, 3)."""
    import numpy as np
    f = _bnorm(d)
    fy = f[..., 1]
    u = _bnorm(np.stack([-fy * f[..., 0], 1.0 - fy * fy, -fy * f[..., 2]], axis=-1))
    pole = np.zeros(f.shape)
    pole[..., 2] = np.where(fy < 0, 1.0, -1.0)
    u = np.where((np.abs(fy) < 1.0 - 1e-7)[..., None], u, pole)
    return np.stack([_bcross(u, f), u, f], axis=-2)


def _blevel(fr):
    """`frame_level` of frames `fr` (k, 3, 3)."""
    import numpy as np
    r, u, f = (_bnorm(fr[..., i, :]) for i in range(3))
    c = np.clip(u[..., 1], -1.0, 1.0)
    axis = _bcross(u, np.array((0.0, 1.0, 0.0)))
    axis = _bnorm(np.where((np.sqrt(_bdot(axis, axis)) < 1e-9)[..., None], r, axis))
    ang = np.arccos(c)
    turned = np.stack([_brotate(v, axis, ang) for v in (r, u, f)], axis=-2)
    return np.where((c > 1.0 - 1e-9)[..., None, None], np.stack([r, u, f], axis=-2), turned)


def _bmix(fr, m):
    """Rows m x `fr`: each new row the frame's rows weighted by a row of the 3 x 3 `m`."""
    import numpy as np
    return np.stack([fr[..., 0, :] * a + fr[..., 1, :] * b + fr[..., 2, :] * c for a, b, c in m], axis=-2)


def _bshot(fr, yaw_deg, pitch_deg):
    """`frame_shot`: the shoot angles applied in frames `fr`."""
    y, p = math.radians(yaw_deg), math.radians(pitch_deg)
    cy, sy, cp, sp = math.cos(y), math.sin(y), math.cos(p), math.sin(p)
    return _bmix(fr, ((cy, 0.0, -sy), (-sp * sy, cp, -sp * cy), (cp * sy, sp, cp * cy)))


def _bspread(fr, a_deg, b_deg):
    """`frame_spread` on frames `fr`."""
    a, b = math.radians(a_deg), math.radians(b_deg)
    ca, sa, cb, sb = math.cos(a), math.sin(a), math.cos(b), math.sin(b)
    return _bmix(fr, ((cb, sb * sa, -sb * ca), (0.0, ca, sa), (sb, -cb * sa, cb * ca)))


def _baim(fr, pos, point, limit_deg):
    """`frame_aim` of frames `fr` at `pos` toward `point` (both (k, 3))."""
    import numpy as np
    f = _bnorm(fr[..., 2, :])
    d = _bnorm(np.asarray(point, float) - pos, f)
    limit = math.radians(limit_deg)
    axis = _bcross(f, d)
    n = np.sqrt(_bdot(axis, axis))
    turned = _brotate(f, axis * (1.0 / np.where(n > 1e-12, n, 1.0))[..., None], np.full(n.shape, limit))
    nf = np.where((_bdot(d, f) <= math.cos(limit))[..., None], np.where((n > 1e-12)[..., None], turned, f), d)
    nf = _bnorm(nf, f)
    right = _bcross(np.array((0.0, 1.0, 0.0)), nf)
    return np.stack([right, _bcross(nf, right), nf], axis=-2)


def _bchild_frame(child, parent, owner):
    """`child_frame` for k children of row `child` whose parents' frames are `parent` (k, 3, 3)."""
    import numpy as np
    if child.get('isOwnerOverrideInitAngle'):
        return np.broadcast_to(np.asarray(owner, float), parent.shape).copy()
    if child['EmittePosType'] == EMIT_RAISED_LEVEL:
        f = parent[..., 2, :]
        h = np.stack([f[..., 0], np.zeros(f.shape[:-1]), f[..., 2]], axis=-1)
        flat = np.sqrt(_bdot(h, h)) < 1e-9
        out = _blook(np.where(flat[..., None], np.array((0.0, 0.0, 1.0)), h))
        return np.where(flat[..., None, None], _blevel(parent), out) if flat.any() else out
    if child.get('sfxPostureType') in (1, 2):
        return _blevel(parent)
    return parent


def row_shots(r, pos, frames, point=None, aims=True, want=None, slot=(0, 1)):
    """[(start (k, 3), frame (k, 3, 3), life seconds, first interval seconds)] of each of Bullet row
    `r`'s `numShoot` shots made by k bullets at `pos` (k, 3) whose spawn frames are `frames`
    (k, 3, 3), in FUN_140390c20's order: `EmittePosType` 1 on `random_ground_point` (toward `want`
    (k, 2), the defender's (x, z), or under the caster), 0 and 2 from `pos` (2 raised), turned
    toward the lock-on `point` (k, 3; None: no target) when `aims` (the event's orientation byte is
    not 1), then the shoot angles; then every type the drawn spread (draw `slot[0] x numShoot + s`
    of `slot[1] x numShoot`, `shot_draws`) and `sfxPostureType` 1's levelling."""
    import numpy as np
    emit = r['EmittePosType']
    shots = max(1, r['numShoot'])
    pos = np.asarray(pos, float)
    k = len(pos)
    out = []
    for s in range(shots):
        a, b, life, first = shot_draws(r, slot[0] * shots + s, slot[1] * shots)
        if emit == 1:
            R = max(r.get('randomCreateRadius') or 0.0, 0.0)
            w = np.zeros((k, 2)) if want is None else np.asarray(want, float)
            start = np.stack([np.minimum(np.maximum(w[:, 0], -R), R), np.zeros(k),
                              np.minimum(np.maximum(w[:, 1], -R), R)], axis=1)
            fr = np.broadcast_to(np.eye(3), (k, 3, 3)).copy()
        else:
            start = pos.copy()
            if emit == EMIT_RAISED_LEVEL:
                start[:, 1] += EMIT_RAISE_M
            fr = np.asarray(frames, float)
            if emit in AIM_EMIT_TYPES and point is not None and aims:
                fr = _baim(fr, start, point, r.get('lockShootLimitAng') or 0)
            if emit in AIM_EMIT_TYPES:
                fr = _bshot(fr, r['shootAngle'] + s * r['shootAngleInterval'],
                            r['shootAngleXZ'] + s * r['shootAngleXInterval'])
        if a or b:
            fr = _bspread(fr, a, b)
        if r.get('sfxPostureType') == 1:
            fr = _blevel(fr)
        out.append((start, fr, life, first))
    return out


def shot_frames(r, frame, pos, point=None, aims=True, slot=(0, 1)):
    """`row_shots` for one bullet: [(start position, frame)]."""
    import numpy as np
    shots = row_shots(r, [pos], np.array([frame], float), None if point is None else [point], aims, slot=slot)
    return [(tuple(map(float, s[0])), tuple(tuple(map(float, row)) for row in f[0])) for s, f, _, _ in shots]


#: {(row id, speed, moves): (distance flown, signed speed) per frame}: the straight flight's speed
#: profile (`_lay_rows`).
_PROFILE = {}


def _speed_profile(r, v0, moves):
    """(distance along its heading, signed speed) after each of `moves` moves of Bullet row `r`
    flying straight at `v0` (its acceleration acts along its velocity, so the profile is every
    heading's), from `_fly_loop` far above the ground; cached."""
    import numpy as np
    key = (r.get('id', id(r)), float(v0), moves)
    if key not in _PROFILE:
        if len(_PROFILE) > 20000:
            _PROFILE.clear()
        P, _, _, _, _, V = _fly_loop(r, np.array([(0.0, 1e6, 0.0)]), np.array([(0.0, 0.0, 1.0)]),
                                     np.array([float(v0)]), moves, None, False, speeds=True)
        _PROFILE[key] = (P[:, 0, 2] - P[0, 0, 2], V[:, 0])
    return _PROFILE[key]


def _fly_loop_one(r, start, d, v0, moves, aim, ground, speeds=False):
    """`_fly_loop` for one bullet, in plain floats: the same operations in the same order, so the
    same numbers (the selftest flies members together and alone), without numpy's per-call cost on
    arrays of one (most stored flights are one bullet against one defender)."""
    import numpy as np
    dt = 1.0 / BULLET_FPS
    radius = max(r['hitRadius'], 0.0)
    pen = bool(r['isPenetrateMap'])
    dx, dy, dz = (float(c) for c in np.asarray(d, float)[0])
    v0 = float(np.asarray(v0, float)[0])
    vx, vy, vz = dx * v0, dy * v0, dz * v0
    px, py, pz = (float(c) for c in np.asarray(start, float)[0])
    P = [(px, py, pz)]
    V = [vz] if speeds else None
    L, hit, Sf, alive = moves + 1, False, 0.0, True
    on = turning = aim is not None and homes(r)
    rate = math.radians(r.get('homingAngle') or 0) * dt
    cr, sr = math.cos(rate), math.sin(rate)
    gin, gout, ain, aout = r['gravityInRange'], r['gravityOutRange'], r['accelInRange'], r['accelOutRange']
    begin, stop_r, dist, accel_t = r['homingBeginDist'], r['hormingStopRange'], r['dist'], r['accelTime']
    vmax, vmin = r['maxVellocity'], r['minVellocity']
    flown, t = 0.0, 0.0
    for j in range(moves):
        if turning and j and on and flown > begin:
            ax, ay, az = (float(c) for c in np.asarray(aim(j), float)[0])
            tx, ty, tz = ax - px, ay - py, az - pz
            far = math.sqrt(tx * tx + ty * ty + tz * tz)
            if far < stop_r:
                on = False
            else:
                sv = math.sqrt(vx * vx + vy * vy + vz * vz)
                if sv > HOMING_MIN_SPEED and far > 0:
                    hx, hy, hz = vx / sv, vy / sv, vz / sv
                    ux, uy, uz = tx / far, ty / far, tz / far
                    cos = min(max(hx * ux + hy * uy + hz * uz, -1.0), 1.0)
                    wx, wy, wz = ux - cos * hx, uy - cos * hy, uz - cos * hz
                    wn = math.sqrt(wx * wx + wy * wy + wz * wz)
                    if cos >= cr:
                        vx, vy, vz = ux * sv, uy * sv, uz * sv
                    elif wn > 1e-12:
                        vx, vy, vz = sv * (cr * hx + sr * wx / wn), sv * (cr * hy + sr * wy / wn), \
                            sv * (cr * hz + sr * wz / wn)
        g = gin if gin == gout else (gin if flown <= dist else gout)
        if t < accel_t or (ain == 0 and aout == 0):
            nvx, nvy, nvz = vx, vy + (0.0 - g) * dt, vz
        else:
            a = ain if ain == aout else (ain if flown <= dist else aout)
            s = math.sqrt(vx * vx + vy * vy + vz * vz)
            hx, hy, hz = (vx / s, vy / s, vz / s) if s > 1e-9 else (dx, dy, dz)
            nvx, nvy, nvz = vx + a * hx * dt, vy + (a * hy - g) * dt, vz + a * hz * dt
        s = math.sqrt(nvx * nvx + nvy * nvy + nvz * nvz)
        if vmax > 0 and s > 1e-9 and (s > vmax or s < vmin):
            cl = min(max(s, vmin), vmax)
            nvx, nvy, nvz, s = nvx * cl / s, nvy * cl / s, nvz * cl / s, cl
        vx, vy, vz = nvx, nvy, nvz
        px, py, pz = px + vx * dt, py + vy * dt, pz + vz * dt
        flown = flown + s * dt
        t += dt
        if s > 1e-9:
            dx, dy, dz = vx / s, vy / s, vz / s
        if not pen:
            fall = vy < 0 and py <= radius
            if fall:
                py = radius
            if fall or (ground and s <= 1e-9 and py <= radius):
                L, hit, Sf, alive = j + 2, True, s, False
        P.append((px, py, pz))
        if speeds:
            V.append(vz)
        if not alive:
            break
    if alive:
        Sf = math.sqrt(vx * vx + vy * vy + vz * vz)
    return (np.array(P)[:, None, :], np.array([L]), np.array([hit]), np.array([[dx, dy, dz]]), np.array([Sf]),
            (np.array(V)[:, None] if speeds else None))


def _fly_loop(r, start, d, v0, moves, aim, ground, speeds=False):
    """`_fly_rows`' frame loop for k free bullets (homing, gravity), and the straight profile. Each
    vector is a (3, k) array, its rows x, y, z, so a frame is a few dozen operations on all k at
    once, each sum taken over the rows in order as `_fly_loop_one` takes it; one bullet goes to
    `_fly_loop_one`."""
    import numpy as np
    k = len(start)
    if k == 1:
        return _fly_loop_one(r, start, d, v0, moves, aim, ground, speeds)
    dt = 1.0 / BULLET_FPS
    radius = max(r['hitRadius'], 0.0)
    pen = bool(r['isPenetrateMap'])
    D = np.array(d, float).T.copy()
    v0 = np.asarray(v0, float)
    V_ = D * v0[None, :]
    p = np.array(start, float).T.copy()
    P = np.empty((moves + 1, k, 3))
    P[0] = p.T
    VZ = np.empty((moves + 1, k)) if speeds else None
    if speeds:
        VZ[0] = V_[2]
    L = np.full(k, moves + 1)
    hit = np.zeros(k, bool)
    alive = np.ones(k, bool)
    every = True
    flown = np.zeros(k)
    Sf = np.zeros(k)
    turning = aim is not None and homes(r)
    on = np.full(k, turning)
    rate = math.radians(r.get('homingAngle') or 0) * dt
    cr, sr = math.cos(rate), math.sin(rate)
    gin, gout, ain, aout = r['gravityInRange'], r['gravityOutRange'], r['accelInRange'], r['accelOutRange']
    vmax, vmin = r['maxVellocity'], r['minVellocity']
    t = 0.0
    last = moves
    with np.errstate(divide='ignore', invalid='ignore'):
        for j in range(moves):
            if turning and j:
                m = on & alive & (flown > r['homingBeginDist'])
                if m.any():
                    to = np.asarray(aim(j), float).T - p
                    far = np.sqrt((to * to).sum(axis=0))
                    if r['hormingStopRange'] > 0:
                        stop = m & (far < r['hormingStopRange'])
                        on &= ~stop
                        m = m & ~stop
                    sv = np.sqrt((V_ * V_).sum(axis=0))
                    ok = m & (sv > HOMING_MIN_SPEED) & (far > 0)
                    if ok.any():
                        h = V_ / sv
                        u = to / far
                        cos = np.minimum(np.maximum((h * u).sum(axis=0), -1.0), 1.0)
                        w = u - cos * h
                        wn = np.sqrt((w * w).sum(axis=0))
                        # FUN_140398270: within the frame's turn the velocity goes onto the point
                        # (acos(cos) <= rate), else it turns by the whole of it toward the point;
                        # the speed is kept.
                        snap = ok & (cos >= cr)
                        turn = ok & ~snap & (wn > 1e-12)
                        if turn.all():
                            V_ = sv * (cr * h + sr * w / wn)
                        elif snap.all():
                            V_ = u * sv
                        else:
                            V_ = np.where(snap, u * sv, np.where(turn, sv * (cr * h + sr * w / wn), V_))
            g = gin if gin == gout else np.where(flown <= r['dist'], gin, gout)
            if t < r['accelTime'] or (ain == 0 and aout == 0):
                nv = V_.copy()
                nv[1] = V_[1] + (0.0 - g) * dt
            else:
                a = ain if ain == aout else np.where(flown <= r['dist'], ain, aout)
                s = np.sqrt((V_ * V_).sum(axis=0))
                hat = np.where(s > 1e-9, V_ / s, D)
                G = a * hat
                G[1] = G[1] - g
                nv = V_ + G * dt
            s = np.sqrt((nv * nv).sum(axis=0))
            if vmax > 0:
                # Only a speed outside [min, max] is scaled (one inside would be scaled by 1).
                big = (s > 1e-9) & ((s > vmax) | (s < vmin))
                if big.any():
                    cl = np.minimum(np.maximum(s, vmin), vmax)
                    nv = np.where(big, nv * cl / s, nv)
                    s = np.where(big, cl, s)
            if every:
                V_ = nv
                p = p + V_ * dt
                flown = flown + s * dt
                D = np.where(s > 1e-9, V_ / s, D)
            else:
                V_ = np.where(alive, nv, V_)
                p = np.where(alive, p + V_ * dt, p)
                flown = flown + np.where(alive, s * dt, 0.0)
                D = np.where(alive & (s > 1e-9), V_ / s, D)
            t += dt
            if not pen:
                # The step can carry a fast bullet below the ground (Lightning Slash's 200 m/s bolt
                # ends a frame at y = -2.3): it stops on the ground, where its child is created.
                fall = alive & (V_[1] < 0) & (p[1] <= radius)
                # A bullet at rest on the ground (`random_ground_point`) meets the map on its first
                # update: the map contact comes from its attack's sweep (FUN_14051acc0 ->
                # FUN_14038b610 -> FUN_140398f90, `VERIFIED`), which a sphere resting on the ground
                # touches (`INFERRED`: the sweep's shape test was not read).
                end = fall | (alive & (s <= 1e-9) & (p[1] <= radius)) if ground else fall
                if end.any():
                    p[1] = np.where(fall, radius, p[1])
                    L[end], hit[end], Sf[end] = j + 2, True, s[end]
                    alive &= ~end
                    every = False
            P[j + 1] = p.T
            if speeds:
                VZ[j + 1] = V_[2]
            if not every and not alive.any():
                last = j + 1
                break
    Sf = np.where(alive, np.sqrt((V_ * V_).sum(axis=0)), Sf)
    return P[:last + 1], L, hit, D.T.copy(), Sf, (VZ[:last + 1] if speeds else None)


def _lay_rows(r, start, d, v0, moves, ground):
    """`_fly_rows` for free bullets that neither turn nor fall: the speed profile laid along each
    heading (`_speed_profile`), stopped where it meets the ground as the frame loop stops it."""
    import numpy as np
    k = len(start)
    radius = max(r['hitRadius'], 0.0)
    P = np.empty((moves + 1, k, 3))
    L = np.full(k, moves + 1)
    hit = np.zeros(k, bool)
    D = np.array(d, float)
    S = np.zeros(k)
    for u in np.unique(v0):
        ix = np.nonzero(v0 == u)[0]
        far, vz = _speed_profile(r, u, moves)
        P[:, ix] = start[ix][None] + far[:, None, None] * d[ix][None]
        S[ix] = abs(vz[-1])
        D[ix] = d[ix] * (1.0 if vz[-1] >= 0 else -1.0)
        if r['isPenetrateMap']:
            continue
        y = P[1:, ix, 1]
        vy = vz[1:, None] * d[ix, 1][None]
        end = (vy < 0) & (y <= radius)
        if ground:
            end |= (np.abs(vz[1:])[:, None] <= 1e-9) & (y <= radius)
        got = end.any(axis=0)
        for c in np.nonzero(got)[0]:
            m, j = ix[c], int(np.argmax(end[:, c])) + 1
            if vy[j - 1, c] < 0:
                P[j, m, 1] = radius
            P[j + 1:, m] = P[j, m]
            L[m], hit[m], S[m] = j + 1, True, abs(vz[j])
            D[m] = d[m] * (1.0 if vz[j] >= 0 else -1.0)
    return P, L, hit, D, S


def flight_moves(r, life=None):
    """How many moves a bullet of row `r` makes over `life` seconds (default the row's): an endless
    one (life < 0) `BULLET_LIFE_CAP_S`' worth, one of life 0 a single update. The bullet also moves
    on the frame its life runs out (`VERIFIED` KillBullet 0x14039f0c0 path): life / dt plus that one.
    A searching attached row (`search_launched`) hovers `SEARCH_ARM_S`, the earliest its search
    launches it."""
    dt = 1.0 / BULLET_FPS
    life = r['life'] if life is None else life
    if life < 0 and search_launched(r):
        life = SEARCH_ARM_S
    life = life if life > 0 else (BULLET_LIFE_CAP_S if life < 0 else dt)
    return max(1, round(life / dt)) + 1


def _fly_rows(r, start, dirs, speed=None, life=None, aim=None, follow=None, ground=False):
    """The flight of k bullets of Bullet row `r` at once, by the rules `bullet_flight` documents:
    `start` (k, 3) where they are created, `dirs` (k, 3) their unit directions, `speed` (k,)
    overriding `initVellocity` (`isInheritSpeedToChild`), `life` seconds (default the row's; a
    drawn one, `shot_draws`), `aim(j)` -> (k, 3) the point a row that `homes` turns toward before
    move j, `follow(j)` -> (k, 3) an attached row's follow point, `ground` whether one at rest on
    the ground meets the map. Returns (P (N + 1, k, 3) positions, L (k,) how many each has, hit (k,)
    whether it ended on the map, D (k, 3) final directions, S (k,) final speeds); one that ends
    holds its last position after. Every flight of the model goes through here: the reach, the
    punish geometry (`fly_members`) and the record counts (`er-mechanics-spells._fly_records`)."""
    import numpy as np
    start = np.asarray(start, float)
    d = np.array(dirs, float)
    k = len(start)
    dt = 1.0 / BULLET_FPS
    moves = flight_moves(r, life)
    if r['FollowType'] in FOLLOW_ATTACHED:
        if follow is None:
            return start[None].copy(), np.ones(k, int), np.zeros(k, bool), d, np.zeros(k)
        # FUN_14039afc0 (`FOLLOW_RATE`): each update the velocity is set to carry it that share of
        # the way to the follow point, and UpdateBullet moves it by v dt.
        share = (FOLLOW_RATE_FEET if (int(r['FollowType']) & 7) == 2 else FOLLOW_RATE) * dt / FOLLOW_STEP_S
        P = np.empty((moves + 1, k, 3))
        P[0] = p = start.copy()
        s = np.zeros(k)
        for j in range(moves):
            step = (np.asarray(follow(j), float) - p) * share
            p = p + step
            P[j + 1] = p
            n = np.sqrt(_bdot(step, step))
            s = n / dt
            mv = s > MOVING_SPEED
            d = np.where(mv[:, None], step * (1.0 / np.where(mv, n, 1.0))[:, None], d)
        return P, np.full(k, moves + 1), np.zeros(k, bool), d, s
    v0 = np.full(k, float(r['initVellocity'])) if speed is None else np.asarray(speed, float) * np.ones(k)
    if (aim is None or not homes(r)) and not (r['gravityInRange'] or r['gravityOutRange']):
        return _lay_rows(r, start, d, v0, moves, ground)
    return _fly_loop(r, start, d, v0, moves, aim, ground)[:5]


def bullet_flight(r, pos, heading, speed=None, aim3d=None, follow=None, ground=False, life=None):
    """One Bullet row's flight (`_fly_rows` with one bullet): (path [(x, y, z)], ending 'hit' |
    'expire', final unit direction, final speed). `pos` is where it is created, `heading` its
    direction, (yaw, pitch) in degrees with +z forward and +y up or a vector, `speed` overrides
    `initVellocity` (`isInheritSpeedToChild`). With `aim3d` (move index -> (x, y, z), the point it
    turns toward) a row that `homes` turns in 3D as FUN_140398270 turns it (the rule above
    `homes`: from the second move, once it has flown more than `homingBeginDist`, `homingAngle`
    deg/s toward the point, off for good inside `hormingStopRange`), before the move's
    acceleration as `CSBulletFlyState::OnUpdate` orders them. With `follow` (move index -> (x, y,
    z)) a row attached by its `FollowType` moves toward that point each move as FUN_14039afc0
    moves it (`FOLLOW_RATE`), and without one it stays a single point. With `ground` a bullet at
    rest whose sphere reaches the ground hits the map on its first update. `life` replaces the
    row's (a drawn life, `shot_draws`)."""
    import numpy as np
    d = _unit(*heading) if len(heading) == 2 else _vnorm(tuple(heading))
    P, L, hit, D, S = _fly_rows(r, [pos], [d], None if speed is None else [speed], life,
                                None if aim3d is None else (lambda j: [aim3d(j)]),
                                None if follow is None else (lambda j: [follow(j)]), ground)
    return ([tuple(map(float, q)) for q in P[:int(L[0]), 0]], 'hit' if hit[0] else 'expire',
            tuple(map(float, D[0])), float(S[0]))


def bullet_reach(t, bt, launch=None):
    """(distance, arc) of a skill's bullet tree fired straight ahead, or (0, 0) (`VERIFIED` flight
    rules above), from `launch` (`bullet_launch`; None: the old fixed placement). Distance: the
    farthest horizontal point any damaging bullet reaches from the caster's start position, plus
    its `max(hitRadius, hitRadiusMax)`, at most `BULLET_REACH_CAP_M`. `arc`: the azimuth that
    bullet covers seen from the caster: the `numShoot` fan of its tree plus the angle its radius
    subtends at its farthest point, 360 when the radius encloses the caster. Cached per root
    bullet id and launch."""
    if not bt:
        return 0.0, 0.0
    key = bt['bullet'] if launch is None else (bt['bullet'], _launch_key(launch))
    if key in _FLIGHT:
        return _FLIGHT[key]
    best = [0.0, 0.0]
    # Every shot of the fan flies; the farthest decides the reach.
    for node, path, _, fan in fly_tree(t, bt, launch=launch):
        if not _bullet_damages_self(node):
            continue
        far = max(path, key=lambda q: math.hypot(q[0], q[2]))
        dist = math.hypot(far[0], far[2])
        rad = node['radius_m']
        reach = dist + rad
        if reach > best[0]:
            arc = 360.0 if rad >= dist else min(360.0, fan + 2 * math.degrees(math.asin(rad / dist)))
            best[:] = [reach, arc]
    _FLIGHT[key] = (round(min(best[0], BULLET_REACH_CAP_M), 3), round(best[1], 1))
    return _FLIGHT[key]


_TREE = {}
_SHOTS = {}


class _Entries(list):
    """`fly_shots`' entries, with `keys`: each entry's lineage key (`fly_members`), by position."""

    def __init__(self, *a):
        super().__init__(*a)
        self.keys = []


def _standing(target):
    """`fly_members`' defender for one who stands at `target` (x, z), never invincible."""
    import numpy as np
    x, z = float(target[0]), float(target[1])
    return lambda T, who: (np.full(np.shape(T), x), np.full(np.shape(T), z), np.ones(np.shape(T), bool))


def fly_shots(t, bt, target=None, launch=None):
    """Every shot of tree `bt` with its lineage: [(node, path, t0, fan, shot, parent, aims)], `shot`
    the index of the root shot it descends from (0..numShoot-1), `parent` the index in this list of
    the shot that created it (-1 for a root shot), `aims` whether its row is one the lock-on aim
    turns (`EmittePosType` in `AIM_EMIT_TYPES` and `lockShootLimitAng` > 0, whatever `target` is);
    `.keys` the entries' lineage keys. `target` (x, z): a defender standing there facing the caster,
    whom the shots are aimed at when created and a homing row turns toward in flight (his lock-on
    point, `lock_point`), and on whom a carrier dies (`carrier`); None flies everything with no one
    there. `launch` (`bullet_launch`) is where the root shot starts and the heading it starts on;
    None the old fixed placement. `fly_members` with that one defender; cached per root bullet id,
    target and launch."""
    key = (bt['bullet'], None if target is None else (round(target[0], 3), round(target[1], 3)),
           _launch_key(launch))
    if key in _SHOTS:
        return _SHOTS[key]
    out = _Entries()
    for e in fly_members(t, bt, 1, 0.0, launch, None if target is None else _standing(target), aim=True):
        n = int(e['L'][0])
        out.append((e['node'], [tuple(map(float, q)) for q in e['P'][:n, 0]], float(e['t0'][0]), e['fan'],
                    e['shot'], e['parent'], e['aims']))
        out.keys.append(e['key'])
    _SHOTS[key] = out
    return out


def _flight(r, pos, heading, speed=None):
    """`bullet_flight` with no target, as (path array (N, 3), ending, final direction, final speed)."""
    import numpy as np
    path, ending, d, s = bullet_flight(r, pos, heading, speed)
    return np.array(path, float), ending, d, s


# In-flight homing (bd er-effects-rs-rw25). `VERIFIED` 2026-11-10 on the 1.16.2 named dump (:8765
# decompiles) and `eldenring-deobf.bin` (objdump); the addresses are 1.16.2 and have not been
# found on the 2.7.2.0 image. `CSBulletFlyState::OnUpdate` 0x1403939d0 runs once a frame with the
# frame time in XMM2 (kept in XMM6 and handed on to each call):
#   * its first update after the fly state starts (`field_0x29` 0, cleared by its OnCreation
#     0x140393930) only sets that byte. Every later one, when `FollowType & 3` is 0 (BulletParam
#     +0x9a: `and al, 7; test al, 0xfb`), calls the homing FUN_140398270, then the acceleration
#     (FUN_14039ac20, FUN_14039a810); an attached bullet takes FUN_14039afc0 instead;
#   * `CSBulletIns::UpdateBullet` 0x14039a100 moves the bullet after that state update
#     (FUN_14039f850: p += v dt, and the distance flown at CSBulletIns+0xb10 += |v dt|, zeroed at the
#     launch by FUN_14039e8a0).
# FUN_140398270 (dt copied to XMM10 at 0x14039829a):
#   1. returns unless bit 3 of CSBulletIns+0xd80 is set. The constructor 0x140395810 stores 8 there
#      (0x140395983), the reset FUN_140395e10 sets it (`& 0xe8 | 8`) and the launch FUN_14039e8a0
#      keeps it (`& 0x1f`). Between 0x140380000 and 0x1403c0000 the only write that clears it is
#      step 6's (0x14039847f), so every bullet starts with homing on (a writer outside that range
#      is not ruled out: `INFERRED`);
#   2. returns when `homingAngle` (s16, +0x82) is negative, and while the distance flown is at most
#      `homingBeginDist` (+0x40);
#   3. returns unless the bullet has a target (FUN_140394d00 on CSBulletIns+0xd30: a character
#      handle, or a stored point that is not zero);
#   4. the turn rate is `homingAngle` deg/s and the stop range `hormingStopRange` (+0x24). With bit
#      2 of CSBulletIns+0xd81 set, `PlayerCommonParam.flareOverrideHomingAngle` (when >= 0 and
#      larger) and `flareOverrideHomingStopRange` (when >= 0) replace them; that bit's setter is not
#      read and it is not modelled;
#   5. returns unless |v| > 1e-4 (`___real_38d1b717`). The aim point is the target's position read
#      that frame (FUN_140394b40 -> FUN_140394910: for a character, its model matrix times the
#      stored offset) plus `targetYOffsetRange` (+0xa0) up;
#   6. when the 3D distance to that point is below the stop range it clears bit 3 (`and byte
#      [rbx+0xd80], 0xf7`): homing is off for the rest of the flight;
#   7. otherwise it rotates v about v x (the direction to the point) by min(the angle to it,
#      rate x deg->rad x dt): `mulss xmm10, xmm7` (dt x rate) at 0x1403984cb, its cosine, `maxss`
#      with the cosine to the point at 0x140398538, acos, FUN_140399890 builds the rotation. The
#      speed is kept; the turn is in 3D.
# So a shot turns toward where the target is at most `homingAngle` deg per second of game time,
# frame by frame, from its second frame and once it has flown more than `homingBeginDist`, until it
# comes within `hormingStopRange` of the aim point. Which way the rotation turns (toward the point,
# not away) is read from the axis v x d with a positive angle, not from FUN_140399890's body
# (`INFERRED`).
# Modelled (`homes`, `_fly_loop`): that rule at `BULLET_FPS`, in 3D, toward the defender's lock-on
# point (`lock_point`: DummyPoly 220, which FUN_140394910 returns for the lock target) plus the
# row's `targetYOffsetRange`, where he is on that frame (round 4: every flight, the punish geometry's
# with the record counts'; the 2D turn of the horizontal heading it replaces kept a shot's straight
# climb or fall). That the target slot holds the defender is the aim's `INFERRED` lock-on; with no
# target (`fly_tree` without one, the reach) a shot flies straight.
# `isEnableAutoHoming` (+0x9c bit 4), `hormingOffsetRange` (+0x54) and `homingAngleX` (+0xd4) are not
# read by FUN_140398270. `isEnableAutoHoming` is read once in the whole image (`VERIFIED` 2026-11-12,
# a scan of `eldenring-deobf.bin` for byte, dword and `bt` reads of +0x9c bit 4 and +0x98 bit 20: one
# hit, `movzx eax, byte [rcx+0x9c]; shr eax, 4` at 0x14040afea), by FUN_14040afa0. That picks a
# target for a shot only when `GameSettings.autoTarget` is on, the shooter is the main player and he
# is neither locked on (`IsLockedOn`) nor aiming (`PrecisionShootingRequested`): the auto-target of an
# unlocked cast, the nearest character inside a cone ahead. It does nothing to a shot of a caster who
# is locked on, which is every shot here. What reads the other two is not traced.
#: Speed a shot must be above to turn (`VERIFIED` FUN_140398270, .rdata `0x38d1b717`).
HOMING_MIN_SPEED = 1e-4


def homes(r):
    """Whether Bullet row `r` turns toward its target in flight (FUN_140398270, above):
    `homingAngle` above 0 and free flight (`FollowType & 3` 0)."""
    return (r.get('homingAngle') or 0) > 0 and (int(r.get('FollowType') or 0) & 3) == 0


def carrier(t, node):
    """Whether a bullet of tree node `node` dies on a defender it touches and makes its
    `HitBulletID` there, in the punish flight (`fly_members`): its AtkParam targets opponents
    (`opposeTarget`), it does not pass through characters (`isPenetrateChr` 0), it does no damage of
    its own and its child is made on a hit (`launches`; not a sliding `FollowType` 4 row). A flight
    with a defender is a spell's or a goods cast's (a weapon skill's flies with no target,
    `_hit_contacts_raw`), whose bullets are priced by their flat attack fields (FUN_1406832a0's flat
    branch, `er-mechanics-spells.spell_node_damaging`), so no damage is no flat field above 0 and no
    `isAddBaseAtk` (Cannon of Haima's ball, AtkParam_Pc 40800, has corrections of 100 and no flat
    attack: it carries the blast). The game kills every such bullet on a landed touch
    (FUN_1403aba90 -> KillBullet(4), the child at the contact point through FUN_14039dcc0,
    `VERIFIED`, `er-mechanics-spells._fly_records`), damaging or not. The punish flight leaves a
    damaging one flying through him: it is read for its first touch (`_catches`), which is the
    landed one, and nothing after it moves the catch, while one he is invincible to flies on in the
    game as here (`INFERRED`: a touch on an invincible defender lands nothing, so it does not kill
    the bullet)."""
    r = t.bullet.get(node['bullet']) or {}
    hit = node['children'].get('HitBulletID')
    return bool(node.get('oppose') and not node['penetrates'] and not any(node['flat'].values())
                and not node['add_base_atk'] and hit is not None and r.get('FollowType') != 4
                and launches(t, node['bullet'], hit['bullet'], 'hit'))


_DEDUP_W = {}


def _dedup_weights(n, i):
    """(n, 2) fixed random weights for block `i` (n columns) of `fly_members`' member grouping."""
    import numpy as np
    if (n, i) not in _DEDUP_W:
        _DEDUP_W[(n, i)] = np.random.default_rng(1009 * i + n).standard_normal((n, 2))
    return _DEDUP_W[(n, i)]


def _lock_at(x, z, yoff=0.0):
    """`lock_point` of defenders standing at (x, z) (arrays) facing the caster, raised `yoff`."""
    import numpy as np
    if not _LOCK:
        lock_point(0.0)
    ox, oy, oz = _LOCK[0]
    x, z = np.asarray(x, float), np.asarray(z, float)
    # The bearing's cosine and sine, z / rho and x / rho (rho the distance; 1 and 0 under him).
    rho = np.hypot(x, z)
    near = rho > 0
    c = np.where(near, z / np.where(near, rho, 1.0), 1.0)
    s = np.where(near, x / np.where(near, rho, 1.0), 0.0)
    return np.stack([x + ox * c + oz * s, np.full(x.shape, oy + yoff), z - ox * s + oz * c], axis=-1)


#: Sampling step of a carrier's swept move (`_carrier_touch`) and of the record flight's touches
#: (`er-mechanics-spells._fly_records`): FUN_1405226e0 casts the attack's shape over the whole move.
SWEEP_STEP_M = 0.05
#: Moves of a flight `_entry_first_touch` cuts at once, in time order.
FIRST_TOUCH_FRAMES = 6
_DISC_SLICES = []


def _profile():
    if not _HOMING_PROFILE:
        _HOMING_PROFILE.append(_pose()[1].defender_profile('idle'))
    return _HOMING_PROFILE[0]


def disc_radii(heights, radius):
    """`er-mechanics-reach.contact_disc_radius` of a sphere of `radius` at each of `heights` against
    the idle hurtbox, NaN where it touches no slice: the same numbers, for an array at once."""
    import numpy as np
    prof = _profile()
    if not _DISC_SLICES:
        js = sorted(prof)
        _DISC_SLICES.append((np.array(js) * _pose()[1].DEFENDER_SLICE_M, np.array([prof[j] for j in js], float)))
    ys, rs = _DISC_SLICES[0]
    h = np.asarray(heights, float).ravel()
    out = np.full(h.shape, np.nan)
    for a in range(0, len(h), 50000):
        dy = ys[None, :] - h[a:a + 50000, None]
        ok = np.abs(dy) <= radius
        d = np.where(ok, rs[None, :] + np.sqrt(np.maximum(radius * radius - dy * dy, 0.0)), -np.inf)
        out[a:a + 50000] = np.where(ok.any(axis=1), d.max(axis=1), np.nan)
    return out.reshape(np.shape(heights))


def _frames_at(P, idx, ix, fr0, D, S, at_end, memo=None):
    """Frames of bullets `ix` (positions among a flight's k) after their move into `P[idx]`: along
    their last move faster than `MOVING_SPEED` up to there (FUN_14039f850's frame follows the
    velocity), along their final direction `D` where `at_end` (a ground hit's last position is put
    on the ground, so its last step is not its velocity), their launch frame `fr0` if they never
    moved. `memo`, a dict kept by the caller for one `P`, holds every flight's moves and the last
    one that moved up to each position, read once for every call on that `P` (`fly_members` asks
    once per interval child: a cloud that never moves read the whole flight's moves each time, 64%
    of Founding Rain of Stars' flight time; nine of its flights took 102 s before and 38 s after,
    the same numbers, 2026-11-13); each number is the one the read of the flights asked for gives,
    since every operation is per flight and per position."""
    import numpy as np
    idx = np.asarray(idx, int)
    out = np.array(fr0[ix], float)
    end = np.asarray(at_end, bool) & (S[ix] > MOVING_SPEED)
    if end.any():
        out[end] = _blook(D[ix[end]])
    rest = np.nonzero(~end & (idx >= 1))[0]
    if len(rest):
        m, j = ix[rest], idx[rest]
        st = P[j, m] - P[j - 1, m]
        mv = np.sqrt(_bdot(st, st)) * BULLET_FPS > MOVING_SPEED
        if mv.any():
            out[rest[mv]] = _blook(st[mv])
        back = rest[~mv]
        if len(back):
            # A bullet that did not move into P[idx]: its last move that did, if any.
            m, j = ix[back], idx[back]
            if memo is None:
                steps = P[1:, m] - P[:-1, m]
                moving = np.sqrt(_bdot(steps, steps)) * BULLET_FPS > MOVING_SPEED
                last = np.maximum.accumulate(np.where(moving, np.arange(len(steps))[:, None], -1), axis=0)
                s = last[j - 1, np.arange(len(back))]
                g = s >= 0
                if g.any():
                    out[back[g]] = _blook(steps[s[g], np.nonzero(g)[0]])
            else:
                if 'last' not in memo:
                    steps = P[1:] - P[:-1]
                    moving = np.sqrt(_bdot(steps, steps)) * BULLET_FPS > MOVING_SPEED
                    memo['steps'] = steps
                    memo['last'] = np.maximum.accumulate(np.where(moving, np.arange(len(steps))[:, None], -1),
                                                         axis=0)
                s = memo['last'][j - 1, m]
                g = s >= 0
                if g.any():
                    out[back[g]] = _blook(memo['steps'][s[g], m[g]])
    return out


def _touch_samples(P, L, radius, t0, who, fire, defender, margin=0.0):
    """The samples of k flights (P (F, k, 3), L (k,)) that touch their defenders, by the record
    flight's rule (`er-mechanics-spells._fly_records`): each flight's start point, then each move
    swept every `SWEEP_STEP_M` from its start (excluded) to its end, a sample touching when the idle
    hurtbox's contact disc at its height reaches the defender's axis (`disc_radii`, inclusive),
    outside his invincibility, where `defender(T, who)` puts him at the sample's real frame. Only
    moves whose height reaches the hurtbox and that pass within its widest slice, `radius` and
    `margin` (the most he moves in a move) of where he is as they start are sampled. Returns (start
    touches (k,), and per touching move sample: the move's flight, the index of the position the
    move ends at, its time in moves, the sample, the move's length)."""
    import numpy as np
    prof = _profile()
    low, top = min(prof) * _pose()[1].DEFENDER_SLICE_M, max(prof) * _pose()[1].DEFENDER_SLICE_M
    wide = max(prof.values())
    F, k = P.shape[:2]
    x, z, vuln = defender(fire + t0 * TAE_FPS, who)
    d0 = disc_radii(P[0, :, 1], radius)
    first = vuln & ~np.isnan(d0) & (np.hypot(P[0, :, 0] - x, P[0, :, 2] - z) <= np.nan_to_num(d0, nan=-1.0))
    none = (first, np.zeros(0, int), np.zeros(0, int), np.zeros(0), np.zeros((0, 3)), np.zeros(0))
    if F < 2:
        return none
    p0, p1 = P[:-1], P[1:]
    valid = np.arange(1, F)[:, None] < L[None, :]
    lo = np.minimum(p0[..., 1], p1[..., 1]) - radius
    hi = np.maximum(p0[..., 1], p1[..., 1]) + radius
    ks, ms = np.nonzero(valid & (lo <= top) & (hi >= low))
    if not len(ks):
        return none
    a0, a1 = p0[ks, ms], p1[ks, ms]
    x, z, _ = defender(fire + (t0[ms] + ks / BULLET_FPS) * TAE_FPS, who[ms])
    ax, az = a1[:, 0] - a0[:, 0], a1[:, 2] - a0[:, 2]
    n2 = ax * ax + az * az
    u = np.where(n2 < 1e-12, 0.0, np.clip(((x - a0[:, 0]) * ax + (z - a0[:, 2]) * az) / np.where(n2 < 1e-12, 1.0, n2),
                                          0.0, 1.0))
    near = np.hypot(a0[:, 0] + u * ax - x, a0[:, 2] + u * az - z) <= wide + radius + margin
    ks, ms, a0, a1 = ks[near], ms[near], a0[near], a1[near]
    if not len(ks):
        return none
    seg = a1 - a0
    ln = np.sqrt(_bdot(seg, seg))
    ns = np.maximum(1, np.ceil(ln / SWEEP_STEP_M)).astype(int)
    rep = np.repeat(np.arange(len(ks)), ns)
    jj = np.arange(int(ns.sum())) - np.repeat(np.cumsum(ns) - ns, ns) + 1
    q = a0[rep] + seg[rep] * jj[:, None] / ns[rep][:, None]
    when = ks[rep] + jj / ns[rep]
    x, z, vuln = defender(fire + (t0[ms[rep]] + when / BULLET_FPS) * TAE_FPS, who[ms[rep]])
    h = np.hypot(q[:, 0] - x, q[:, 2] - z)
    # No disc is wider than the widest slice plus the radius: only samples within that are read.
    cand = np.nonzero(vuln & (h <= wide + radius))[0]
    d = disc_radii(q[cand, 1], radius)
    hits = cand[~np.isnan(d) & (h[cand] <= np.nan_to_num(d, nan=-1.0))]
    return first, ms[rep[hits]], ks[rep[hits]] + 1, when[hits], q[hits], ln[rep[hits]]


def _carrier_touch(P, L, radius, t0, who, fire, defender, margin=0.0):
    """Per flight of P (F, k, 3) its first touch on its defender (`_touch_samples`): (index of the
    position the touching move ends at, -1 for none, 0 for its start point; the touching sample;
    that move's speed)."""
    import numpy as np
    k = P.shape[1]
    first, ms, kk, when, q, ln = _touch_samples(P, L, radius, t0, who, fire, defender, margin)
    kc, pt, sp = np.full(k, -1), np.zeros((k, 3)), np.zeros(k)
    if len(ms):
        order = np.lexsort((when, ms))
        lead = order[np.concatenate([[True], ms[order][1:] != ms[order][:-1]])]
        kc[ms[lead]], pt[ms[lead]], sp[ms[lead]] = kk[lead], q[lead], ln[lead] * BULLET_FPS
    kc[first], pt[first] = 0, P[0, first]
    return kc, pt, sp


def path_touches(path, radius, target):
    """[(index of the position a move ends at, its first sample touching a defender standing at
    `target` (x, z))] along one flight `path` (N, 3), the start point as index 0 (`_touch_samples`):
    every touch the record flight plays (`er-mechanics-spells._fly_records`)."""
    import numpy as np
    P = np.asarray(path, float)[:, None, :]
    first, _, kk, when, q, _ = _touch_samples(P, np.array([len(P)]), radius, np.zeros(1), np.zeros(1, int), 0.0,
                                              _standing(target))
    out = [(0, tuple(map(float, P[0, 0])))] if first[0] else []
    seen = set()
    for i in np.argsort(when, kind='stable'):
        if kk[i] not in seen:
            seen.add(kk[i])
            out.append((int(kk[i]), tuple(map(float, q[i]))))
    return out


def fly_members(t, bt, n=1, fire=0.0, launch=None, defender=None, aim=True, held=None, cap=BULLET_INTERVAL_CAP,
                margin=0.0, bound=None):
    """Every bullet of tree `bt`, fired at real frame `fire` from `launch` (`bullet_launch`; None the
    fixed placement), flown against `n` defenders at once: `defender(T, who)` -> (x, z, vulnerable),
    arrays of the shape of `T`, where member `who` is at real frames `T` and whether he can be hit
    there (None: no one, so nothing is aimed, nothing homes and nothing touches). Yields one dict per
    shot in `fly_shots`' order (each shot, then its `HitBulletID`'s, then its interval children's):
    'node', 'who' (m,) the members it is made for, 'inv' (m,) which of its u flights each member's
    is, the flights 'P' (F, u, 3) positions at `BULLET_FPS`, 'L' (u,) how many each has, 't0' (u,)
    seconds after `fire` its first position is at, 'fwho' (u,) the member each was flown against,
    'code' (u,) how it ended (0 expiry, 1 the map, 2 on its defender, a `carrier`); 'shot' the root
    shot it descends from, 'parent' the index of the shot that made it (-1 for a root shot), 'aims',
    'key' its lineage (a tuple of (row, 'r' | 'h' | 'i', spawn, shot) per link: the same shot in
    every flight of the tree, whatever its members), 'fan'. Members whose shot reads the same (its
    spawn place, frame, speed and time, and where the member is and whether he can be hit over its
    flight) share one flight: it is theirs, and so is what it touches.

    The rules, `VERIFIED` unless marked (bd er-effects-rs-7bye; "Launch frames in 3D" above):
    - each shot leaves its spawn frame as FUN_140390c20 builds it (`row_shots`: shoot angles in the
      frame, the drawn spread, `sfxPostureType` 1, `EmittePosType` 1's ground point toward the
      member, aimed at his lock-on point plus `targetYOffsetRange` within `lockShootLimitAng` when
      `aim` and the row aims), every child its parent's frame of that moment (`_frames_at`) as
      FUN_14039bba0 builds it (`_bchild_frame`), the root its launch's;
    - it flies by `_fly_rows`: a row that `homes` turns in 3D toward the member's lock-on point
      plus `targetYOffsetRange` where he is each move; an attached row follows its `FollowType`'s
      point (3 the member's lock-on point, 5 the caster's (0, hitHeight / 2, 0), 1 and 2 the cast
      point, the caster's hand taken to stay put, `INFERRED`); one at rest on the ground meets the map;
    - a `carrier` dies on the first landed touch on its member (`_carrier_touch`) and makes its
      `HitBulletID` at the contact point, at that move, with that move's speed;
    - at the end of its flight a bullet makes its `HitBulletID` by `launches` (a map hit or expiry)
      along its final direction, with its final speed when `isInheritSpeedToChild`, and its interval
      children while it lives (`interval_frames` at the mean interval after its drawn first one; one
      made on the move it dies on its defender is not made), at most `cap` of them per bullet spread
      over its life (None: all).
    `held` (n,) moves each member's root shot that far back toward the caster (the defender's body
    holding him back, `contact_geometry`). `margin`: the most a member moves in one move of a bullet
    (for `_carrier_touch`'s filter).

    `bound` (n,), None for none: per member, a real frame the caller already has a touch at (it
    may lower it as entries come in, `_homing_entries` `least`). A child is made only for the
    members whose bound is past the frame the child is made at: every touch of the child and of
    what it makes comes at or after that frame (a shot's samples start at its `t0`, and a child's
    `t0` is its parent's plus a part of its flight), so for a caller that keeps only each member's
    earliest touch the children left out change nothing. The children made, and every other
    member's flights, are the same: each member flies on its own."""
    import numpy as np
    pos0, yaw0, pitch0, root_aims = _launch_start(launch)
    owner = np.array(yaw_frame(yaw0))
    root_frame = np.array(look_frame(_unit(yaw0, pitch0)))
    back = np.array((0.0, FOLLOW_RETURN_SHARE * PLAYER_HIT_HEIGHT_M, 0.0))
    cast = np.asarray(pos0, float)
    count = [0]

    def fire_row(node, pos, frames, speed, t0, who, depth, root, parent, key, fan, slot, how):
        r = t.bullet.get(node['bullet'])
        if r is None or depth > BULLET_DEPTH_CAP or not len(who):
            return
        fan = max(fan, node['fan_deg'])
        # The event's orientation byte 1 skips the turn for the root shot (FUN_140390c20); a child
        # is spawned with 0 there (FUN_14039bba0) and is turned.
        aims = bool(r['EmittePosType'] in AIM_EMIT_TYPES and (r.get('lockShootLimitAng') or 0) > 0
                    and (depth or root_aims))
        yoff = r.get('targetYOffsetRange') or 0.0
        shots = max(1, r['numShoot'])
        window = max(flight_moves(r, shot_draws(r, slot[0] * shots + s, slot[1] * shots)[2]) for s in range(shots))
        m = len(who)
        speed = None if speed is None else np.asarray(speed, float)
        track = None
        if defender is not None:
            # Where each member is, and whether he can be hit, at every position the row's shots
            # take (his place changes once a real frame, so these are all he does while they fly).
            T = fire + (t0[None, :] + np.arange(window + 1)[:, None] / BULLET_FPS) * TAE_FPS
            # Read on `T` (positions, members) against the members as one row: `defender` is
            # elementwise in (frame, member), so each entry is the one the flat read of `T` against
            # the members tiled gives, without the tiled member arrays (until 2026-11-21 flat: the
            # line was 29% of Founding Rain of Stars' set, py-spy, .worktrees/haima
            # target/haima/prof-frs.txt). Laid out as the flat read's reshape was, C order.
            track = tuple(np.ascontiguousarray(np.broadcast_to(a, T.shape)) for a in defender(T, who[None, :]))
        # Members whose shots read the same (where it is made, its frame, speed and time, and the
        # member's place and invincibility over its flight) fly the same and touch him alike: each
        # such set is flown once, by its first member (`fly`), and `inv` hands it back to all.
        inv = np.arange(m)
        rep = inv
        if m > 1:
            # Each member's inputs summed against two fixed random weightings: equal inputs give
            # equal sums; unequal ones equal sums with no practical chance.
            cols = [pos, np.reshape(frames, (m, 9)), (np.zeros(m) if speed is None else speed)[:, None],
                    t0[:, None]]
            if track is not None:
                cols += [track[0].T, track[1].T, track[2].T]
            H = sum(np.asarray(c, float) @ _dedup_weights(c.shape[1], i) for i, c in enumerate(cols))
            _, rep, inv = np.unique(H, axis=0, return_index=True, return_inverse=True)
            inv = inv.ravel()
        fly = np.arange(len(rep))
        upos, ufr, ut0, uwho = pos[rep], np.asarray(frames, float)[rep], t0[rep], who[rep]
        uspeed = None if speed is None else speed[rep]
        point = want = ux = uz = None
        if track is not None:
            ux, uz = track[0][:, rep], track[1][:, rep]
            point, want = _lock_at(ux[0], uz[0], yoff), np.stack([ux[0], uz[0]], axis=1)
        ft = int(r['FollowType'])
        tracks = track is not None and (homes(r) if ft not in FOLLOW_ATTACHED else ft == 3)
        spot = None
        if ft in FOLLOW_ATTACHED and ft != 3:
            spot = np.broadcast_to(back if ft == 5 else cast, (len(fly), 3))
        hit = node['children'].get('HitBulletID')
        ivl = node['children'].get('intervalCreateBulletId')
        dies = defender is not None and carrier(t, node)
        # The member's lock-on point on each move (plus `targetYOffsetRange` for a homing row;
        # FollowType 3 follows the point itself), for the longest of the row's shots.
        lead = _lock_at(ux, uz, 0.0 if ft == 3 else yoff) if tracks else None
        for s, (start, fr, life, first) in enumerate(row_shots(r, upos, ufr, point, aim and aims, want, slot)):
            rs = s if root is None else root
            steer = follow = None
            if tracks:
                pts = lead[:flight_moves(r, life)]
                if ft == 3:
                    def follow(j, pts=pts):
                        return pts[j]
                else:
                    def steer(j, pts=pts):
                        return pts[j]
            elif spot is not None:
                def follow(j, spot=spot):
                    return spot
            P, L, ended, D, S = _fly_rows(r, start, _bnorm(fr[:, 2, :]), uspeed, life, steer, follow, True)
            code = ended.astype(int)
            cpt = cspd = None
            if dies:
                kc, cpt, cspd = _carrier_touch(P, L, max(r['hitRadius'], r['hitRadiusMax'], 0.0), ut0, uwho, fire,
                                               defender, margin)
                L = np.where(kc >= 0, kc + 1, L)
                code = np.where(kc >= 0, 2, code)
            me = count[0]
            count[0] += 1
            # The flights' moves, read once for every child made off them (`_frames_at` `memo`).
            moves = {}
            ekey = key + ((node['bullet'],) + how + (s,),)
            yield {'node': node, 'P': P, 'L': L, 't0': ut0, 'fwho': uwho, 'who': who, 'inv': inv, 'shot': rs,
                   'parent': parent, 'aims': aims, 'key': ekey, 'fan': fan, 'code': code,
                   'track': None if ux is None else (ux, uz)}
            natural = code < 2
            if hit is not None:
                sel = np.where(code >= 1, launches(t, node['bullet'], hit['bullet'], 'hit'),
                               launches(t, node['bullet'], hit['bullet'], 'expire'))
                ms = np.nonzero(sel[inv])[0]
                if len(ms):
                    end = L - 1
                    cpos = P[end, fly]
                    if cpt is not None:
                        cpos = np.where(natural[:, None], cpos, cpt)
                    cs = None
                    if r['isInheritSpeedToChild']:
                        cs = S if cspd is None else np.where(natural, S, cspd)
                    cfr = _bchild_frame(t.bullet[hit['bullet']], _frames_at(P, end, fly, fr, D, S, natural, moves),
                                        owner)
                    u = inv[ms]
                    tc = (ut0 + end / BULLET_FPS)[u]
                    if bound is not None:
                        # Members already touched by the frame this child is made at (`bound`).
                        live = bound[who[ms]] > fire + tc * TAE_FPS
                        ms, u, tc = ms[live], u[live], tc[live]
                    yield from fire_row(hit, cpos[u], cfr[u], None if cs is None else cs[u], tc,
                                        who[ms], depth + 1, rs, me, ekey, fan, (0, 1), ('h', 0))
            if ivl is not None:
                # Each flight's interval children are its own (2026-11-18): the bullet makes them on
                # its own timer over its own life (`interval_frames`, `VERIFIED` FUN_14039ab10), so
                # the frames come from its own length `L[f]`, one due on the move it dies on its
                # defender is not made, and the cap and the draw slots (`shot_draws`: the n draws one
                # bullet makes take the n strata) count the children it makes. They were taken from
                # `L.max()` over every flight of the call, so a flight's children and draws read its
                # batch-mates: tree 10672000 against a roll carried 0.3 m back at 4 m from frame 72
                # made 12 children alone and 20 in its 40-start bucket, and 51 answers of trees
                # 10672000 and 10672005 read another number on a two-row grid than on the whole one
                # (target/wf/verify-goldenland probe.py, solo2.py). Flights that make the same
                # children are flown together, each set by its own slots; two sets yield entries of
                # one key for disjoint members (`_homing_entries` takes the least).
                lo, hi = r['intervalCreateTimeMin'], r['intervalCreateTimeMax']
                crow = t.bullet[ivl['bullet']]
                made = {}
                sets = {}
                for f in range(len(fly)):
                    lf = int(L[f])
                    if lf not in made:
                        made[lf] = interval_frames(r, lf, (lo + hi) / 2, first, life)
                    full = made[lf]
                    if code[f] == 2 and full and full[-1] == lf - 1:
                        full = full[:-1]
                    sets.setdefault(tuple(full), []).append(f)
                for full, fs in sorted(sets.items(), key=lambda kv: (len(kv[0]), kv[0])):
                    if not full:
                        continue
                    picks = range(len(full)) if cap is None or len(full) <= cap else \
                        [round(q * (len(full) - 1) / (cap - 1)) for q in range(cap)]
                    mine = np.zeros(len(fly), bool)
                    mine[fs] = True
                    ms = np.nonzero(mine[inv])[0]
                    u = inv[ms]
                    for jj in picks:
                        pj = full[jj]
                        mj, uj, tc = ms, u, ut0[u] + pj / BULLET_FPS
                        if bound is not None:
                            # Members already touched by the frame this child is made at (`bound`).
                            live = bound[who[ms]] > fire + tc * TAE_FPS
                            if not live.any():
                                continue
                            mj, uj, tc = ms[live], u[live], tc[live]
                        sfr = _bchild_frame(crow, _frames_at(P, np.full(len(fly), pj), fly, fr, D, S,
                                                             (pj == L - 1) & natural, moves), owner)
                        yield from fire_row(ivl, P[pj, uj], sfr[uj], None, tc, who[mj],
                                            depth + 1, rs, me, ekey, fan, (jj, len(full)), ('i', jj))

    P0 = np.tile(cast, (n, 1))
    if held is not None:
        P0[:, 2] -= np.asarray(held, float)
    yield from fire_row(bt, P0, np.broadcast_to(root_frame, (n, 3, 3)), None, np.zeros(n), np.arange(n), 0, None, -1,
                        (), 0.0, (0, 1), ('r', 0))


def fly_dynamic(t, bt, defender, start, launch=None):
    """`fly_shots` against one defender who moves: `defender(T, who)` (`fly_members`'), each shot
    aimed when it is created at where he is then, a homing row turning after him and a carrier dying
    on him, the root fired at real frame `start` from `launch`. [(node, path array (N, 3), t0 seconds
    after `start`, root shot index)]. Not cached."""
    return [(e['node'], e['P'][:int(e['L'][0]), 0], float(e['t0'][0]), e['shot'])
            for e in fly_members(t, bt, 1, start, launch, defender, aim=True)]


def _entry_first_touch(e, fire, defender, margin=0.0):
    """Per flight of a `fly_members` entry (`e['fwho']`, the member each is flown against; `e['inv']`
    hands them to the entry's members), the first real frame a sample of it touches him outside his
    invincibility, inf where none does. The samples are
    `er-mechanics-spells.SpellModel._point_arrays`' (the stored geometry's): each move cut every
    `CONTACT_STEP_M` from its start, plus the last position, at the heights the idle hurtbox has a
    contact disc (`disc_radii`), a sample touching inside its disc (strictly). Only the moves whose
    height reaches the hurtbox and that pass within the largest disc and `margin` of where he is as
    they start are cut: no other can touch him. They are cut a few frames at a time in time order,
    each only while its start is before the member's first touch so far: a later sample cannot come
    earlier, so the earliest of those cut is the earliest of all."""
    import numpy as np
    P, L, t0, who = e['P'], e['L'], e['t0'], e['fwho']
    radius = e['node']['radius_m']
    prof = _profile()
    S_ = _pose()[1].DEFENDER_SLICE_M
    low, top = min(prof) * S_, max(prof) * S_
    reach = max(prof.values()) + radius
    F, k = P.shape[:2]
    out = np.full(k, math.inf)
    li = L - 1
    q = P[li, np.arange(k)]
    T = fire + (t0 + li / BULLET_FPS) * TAE_FPS
    x, z, vuln = defender(T, who)
    d = disc_radii(q[:, 1], radius)
    touch = vuln & ~np.isnan(d) & (np.hypot(q[:, 0] - x, q[:, 2] - z) < np.nan_to_num(d, nan=-1.0))
    out[touch] = T[touch]
    if F < 2:
        return out
    p0, p1 = P[:-1], P[1:]
    valid = np.arange(F - 1)[:, None] < li[None, :]
    lo = np.minimum(p0[..., 1], p1[..., 1]) - radius
    hi = np.maximum(p0[..., 1], p1[..., 1]) + radius
    keep = valid & (lo <= top) & (hi >= low)
    track = e.get('track')
    if track is not None and len(track[0]) >= F - 1:
        # Where he is as each move starts is the flight's own track (`fly_members`). The moves are
        # read a few at a time in time order, each block only for the flights whose first touch so
        # far comes after the block starts (a later sample cannot come earlier).
        for c0 in range(0, F - 1, FIRST_TOUCH_FRAMES):
            c1 = min(F - 1, c0 + FIRST_TOUCH_FRAMES)
            act = np.nonzero(fire + (t0 + c0 / BULLET_FPS) * TAE_FPS < out)[0]
            if not len(act):
                break
            q0, q1 = p0[c0:c1][:, act], p1[c0:c1][:, act]
            x, z = track[0][c0:c1][:, act], track[1][c0:c1][:, act]
            ax, az = q1[..., 0] - q0[..., 0], q1[..., 2] - q0[..., 2]
            n2 = ax * ax + az * az
            w = np.where(n2 < 1e-12, 0.0, np.clip(((x - q0[..., 0]) * ax + (z - q0[..., 2]) * az)
                                                  / np.where(n2 < 1e-12, 1.0, n2), 0.0, 1.0))
            near = keep[c0:c1][:, act] & (np.hypot(q0[..., 0] + w * ax - x, q0[..., 2] + w * az - z)
                                          <= reach + margin + 1e-6)
            kk, mm = np.nonzero(near)
            if not len(kk):
                continue
            ks, ms = kk + c0, act[mm]
            a0 = p0[ks, ms]
            seg = p1[ks, ms] - a0
            ns = np.maximum(1, np.ceil(np.sqrt(_bdot(seg, seg)) / CONTACT_STEP_M)).astype(int)
            rep = np.repeat(np.arange(len(ks)), ns)
            u = (np.arange(int(ns.sum())) - np.repeat(np.cumsum(ns) - ns, ns)) / ns[rep]
            q = a0[rep] + seg[rep] * u[:, None]
            T = fire + (t0[ms[rep]] + (ks[rep] + u) / BULLET_FPS) * TAE_FPS
            x, z, vuln = defender(T, who[ms[rep]])
            h = np.hypot(q[:, 0] - x, q[:, 2] - z)
            cand = np.nonzero(vuln & (h < reach))[0]
            d = disc_radii(q[cand, 1], radius)
            touch = cand[~np.isnan(d) & (h[cand] < np.nan_to_num(d, nan=-1.0))]
            np.minimum.at(out, ms[rep[touch]], T[touch])
        return out
    else:
        ks, ms = np.nonzero(keep)
        if not len(ks):
            return out
        a0 = p0[ks, ms]
        seg = p1[ks, ms] - a0
        T0 = fire + (t0[ms] + ks / BULLET_FPS) * TAE_FPS
        x, z, _ = defender(T0, who[ms])
        ax, az = seg[:, 0], seg[:, 2]
        n2 = ax * ax + az * az
        w = np.where(n2 < 1e-12, 0.0, np.clip(((x - a0[:, 0]) * ax + (z - a0[:, 2]) * az)
                                              / np.where(n2 < 1e-12, 1.0, n2), 0.0, 1.0))
        near = np.hypot(a0[:, 0] + w * ax - x, a0[:, 2] + w * az - z) <= reach + margin + 1e-6
        ks, ms, a0, seg, T0 = ks[near], ms[near], a0[near], seg[near], T0[near]
        if not len(ks):
            return out
    ln = np.sqrt(_bdot(seg, seg))
    # `ks` is in time order (np.nonzero walks the moves first).
    cuts = np.searchsorted(ks, np.arange(0, F - 1 + FIRST_TOUCH_FRAMES, FIRST_TOUCH_FRAMES))
    for a, b in zip(cuts[:-1], cuts[1:]):
        if a == b:
            continue
        sel = a + np.nonzero(T0[a:b] < out[ms[a:b]])[0]
        if not len(sel):
            continue
        ns = np.maximum(1, np.ceil(ln[sel] / CONTACT_STEP_M)).astype(int)
        rep = np.repeat(sel, ns)
        u = (np.arange(int(ns.sum())) - np.repeat(np.cumsum(ns) - ns, ns)) / np.repeat(ns, ns)
        q = a0[rep] + seg[rep] * u[:, None]
        T = fire + (t0[ms[rep]] + (ks[rep] + u) / BULLET_FPS) * TAE_FPS
        x, z, vuln = defender(T, who[ms[rep]])
        h = np.hypot(q[:, 0] - x, q[:, 2] - z)
        # No disc is wider than `reach`: only samples within it are read.
        cand = np.nonzero(vuln & (h < reach))[0]
        d = disc_radii(q[cand, 1], radius)
        touch = cand[~np.isnan(d) & (h[cand] < np.nan_to_num(d, nan=-1.0))]
        np.minimum.at(out, ms[rep[touch]], T[touch])
    return out


def fly_tree(t, bt, target=None, launch=None):
    """[(node, path, t0, fan)] for every shot of a skill's bullet tree fired straight ahead with
    the rules above: `path` = [(x, y, z)] at `BULLET_FPS` from `t0` seconds after the bullet
    event, `fan` the `numShoot` fan of the tree down to that node. Parents come before their
    children. With `target` (x, z) each shot is aimed at it when created and a homing row turns
    toward it in flight (`fly_shots`); without one every shot flies straight. `launch`
    is where the root shot starts (`bullet_launch`, None the old fixed placement). Cached per root
    bullet id, target and launch."""
    key = bt['bullet'] if target is None else (bt['bullet'], round(target[0], 3), round(target[1], 3))
    if launch is not None:
        key = (key, _launch_key(launch))
    if key not in _TREE:
        _TREE[key] = [e[:4] for e in fly_shots(t, bt, target, launch)]
    return _TREE[key]


# Map contact and the child it creates (bd er-effects-rs-rw25; `VERIFIED` 2026-11-10, 1.16.2 named
# dump :8765, addresses 1.16.2). A bullet's hit record goes through FUN_140399e50, which picks the
# handler by the hit handle's type (bits 28-31): 1 a character (FUN_140398ba0), 6 an object
# (FUN_140398da0), 8 or none (0, the map) FUN_140398f90, the force-erase flag 0x10 FUN_140399130.
# FUN_140398f90 (the map, with its water checks: `isIgnoreSfxIfHitWater`,
# `isIgnoreMoveStateIfHitWater`) calls FUN_14039dcc0 and then KillBullet(4) unless the row's
# `FollowType` is 4 (FUN_140398ac0: it slides along the ground). FUN_14039dcc0 first asks
# FUN_1403aba90 whether the hit counts: for the map (type 0, and 8's map case FUN_1403ac0f0) only
# when `isPenetrateMap` (BulletParam +0x9b bit 0) is 0; for a character when `isPenetrateChr`
# (+0x98 bit 0) is 0; for an object when `isPenetrateObj` (+0x98 bit 1) is 0. A counted hit
# creates the HitBulletID child at the contact point (FUN_14038d800 of the hit record) through
# FUN_14039baf0 -> FUN_14039da40 (the parent's `launchConditionType` on a hit, `launches`) ->
# FUN_14039bba0; expiry creates it at the bullet's position (KillBullet). So a carrier that does no
# damage itself and does not pass through the map bursts where it meets the ground: the model
# already ends such a flight on the flat ground (`bullet_flight`: falling, at `hitRadius` or less)
# and creates the child there. Its contact with the defender's body is not modelled (the flight
# hits no character). The map collision shape is not decoded: the flat-ground rule above is
# `INFERRED`.
#
# Free aim (the user, 2026-11-10: Great Oracular Bubble "can be free aimed directly at the
# floor"; a game fact from the user, not traced: the launch's aim angles come from
# `IsBulletAimAngleSet`, FUN_1403fb0b0, whose setter was not read). A caster who is not locked on
# may pitch a spell's shot down so its carrier bursts on the ground (`floor_launch`): the shot then
# has no lock-on target, so no lock-on aim and no homing (`INFERRED`: free aim is aiming without
# the lock). The spell models offer it where it can matter (`floor_bursts`: the damage is only on
# a child the carrier's ground contact creates) at each of `FLOOR_AIM_M` ahead of the caster's
# start, and the outcome takes the better aim per situation (`er-mechanics-spells.net_parts_aims`).
# A weapon skill is not offered it: no reading shows a skill's bullet taking the aim pitch.
#: Ground points (metres ahead of the caster's start) a free-aimed shot may be put down at.
FLOOR_AIM_M = (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0)
_FLOOR = {}


def floor_bursts(t, bt, test):
    """Whether tree `bt`'s damage rides only on a child its root creates by meeting the ground: the
    root flies free (`FollowType` not attached), stops on the map (`isPenetrateMap` 0) and does no
    damage itself (`test`), and a damaging node hangs under it by `HitBulletID` links each made on
    a hit (`launches`: the parent's `launchConditionType` decides)."""
    r = t.bullet.get(bt['bullet']) if bt else None
    if r is None or r['FollowType'] in FOLLOW_ATTACHED or r['isPenetrateMap'] or test(bt):
        return False

    def walk(node):
        for key, ch in node['children'].items():
            if key != 'HitBulletID':
                continue
            if launches(t, node['bullet'], ch['bullet'], 'hit') and (test(ch) or walk(ch)):
                return True
        return False
    return walk(bt)


def floor_launch(t, bt, launch, ground_m):
    """`launch` (`bullet_launch` or None) pitched down so tree `bt`'s root shot (its first) meets
    the flat ground `ground_m` metres from the caster's start, free-aimed (no lock-on aim), or None
    when it cannot (a level shot that falls short, a point nearer than its launch, a raised
    `EmittePosType` 2 shot, which flies level, a point its life does not reach). Found by bisection
    on the pitch; the flight meets the ground at a frame's end (`bullet_flight`), so the point is
    hit to within its last frame's step. Cached."""
    r = t.bullet.get(bt['bullet'])
    key = (bt['bullet'], _launch_key(launch), ground_m)
    if key in _FLOOR:
        return _FLOOR[key]
    out = None
    pos, yaw, pitch, _ = _launch_start(launch)
    if r is not None and r['EmittePosType'] != EMIT_RAISED_LEVEL:
        import numpy as np

        def reach(p):
            """(ground contact's distance from the caster's start, or inf; its last step): the root's
            first shot from a launch pitched `p`, flown as `fly_members` flies it."""
            start, fr, life, _ = row_shots(r, [pos], np.array([look_frame(_unit(yaw, p))]), aims=False)[0]
            P, L, hit, _, _ = _fly_rows(r, start, _bnorm(fr[:, 2, :]), None, life, None, None, True)
            path = P[:int(L[0]), 0]
            q = path[-1]
            step = math.hypot(*(q[i] - path[-2][i] for i in (0, 2))) if len(path) > 1 else 0.0
            return (math.hypot(q[0], q[2]) if hit[0] else math.inf), step
        lo, hi = -89.9 - r['shootAngleXZ'], 0.0 - r['shootAngleXZ']
        if reach(lo)[0] <= ground_m:
            for _ in range(60):
                mid = (lo + hi) / 2
                if reach(mid)[0] < ground_m:
                    lo = mid
                else:
                    hi = mid
                if hi - lo < 1e-6:
                    break
            p = (lo + hi) / 2
            got, step = reach(p)
            if abs(got - ground_m) <= step + 0.01:
                base = launch or {'pos': pos, 'yaw': yaw, 'peak': -math.inf, 'src': 'start', 'dummy_y': None}
                out = {**base, 'pitch': p, 'aim': False, 'floor_m': ground_m}
    _FLOOR[key] = out
    return out


def skill_reach_factors(t, weapon_id, sword_arts_id, option, grip='one'):
    """The reach and coverage a skill option is scored with, the same columns a slot carries.

      melee   `er-mechanics-reach.skill_reach` on the animation of the first melee hit: world
              reach and the footprint's `coverage_factor`, read like a slot's (`MEASURED` pose)
      bullet  `bullet_reach` on each bullet event of the hit animations, launched where
              `bullet_launch` puts the event's DummyPoly (the clip's root motion included);
              coverage from its arc and the late turn the TimeAct allows before the event
              (`turn_details`), through `coverage_factor`
    Reach is the larger of the two; coverage the larger that exists. With neither, the weapon
    class median a slot without a pose gets (`class_fallback`, source `inferred`)."""
    _, reach_mod, _ = _pose()
    hits = option.get('hit_rows') or []
    cat = SKILL_TAE_BASE + t.arts[sword_arts_id]['swordArtsTypeNew']
    out = {'projectile': False, 'melee_reach': None, 'bullet_reach': None}
    covers = []
    melee = [h for h in hits if h['kind'] == 'melee']
    if melee:
        row = reach_mod.skill_reach(weapon_id, cat, melee[0]['anim'], melee[0]['judge'])
        if row and row.get('world_reach_m') is not None:
            out['melee_reach'] = row['world_reach_m']
            if row.get('coverage_factor') is not None:
                covers.append(row['coverage_factor'])
    prof = skill_profile(t, sword_arts_id, weapon_id)
    tae = skill_tae(t, sword_arts_id) or {}
    for anim in sorted({h.get('anim') for h in hits if h['kind'] == 'bullet'} - {None}):
        events = tae.get(anim) or []
        windows = reach_mod.speed_windows(events)
        for x in prof['anims'].get(anim, []):
            if x['kind'] != 'bullet' or not x.get('bullet'):
                continue
            launch = bullet_launch(cat, anim, x['fr_s'][0] if x.get('fr_s') else x['frames'][0] / TAE_FPS,
                                   x.get('dummy'), held_models(t, weapon_id), orient=x.get('orient', 0))
            d, arc = bullet_reach(t, x["bullet"], launch)
            if d <= 0:
                continue
            out['projectile'] = True
            out['bullet_reach'] = max(out['bullet_reach'] or 0.0, round(d, 3))
            turn = reach_mod.turn_details(events, x['frames'][0] / TAE_FPS, windows)
            c = reach_mod.coverage_factor({'sweep_row_arc_deg': arc, **turn})
            if c:
                covers.append(c['coverage_factor'])
    measured = [v for v in (out['melee_reach'], out['bullet_reach']) if v is not None]
    if measured:
        out['reach'] = max(measured)
        out['reach_source'] = 'bullet' if out['bullet_reach'] == out['reach'] else 'world'
    fb = None
    if not measured or not covers:
        fb = reach_mod.class_fallback(weapon_id, grip, None)
    if not measured:
        out['reach'], out['reach_source'] = fb.get('world_reach_m'), 'inferred'
    out['coverage'] = max(covers) if covers else fb.get('coverage_factor')
    return out


# ---------------------------------------------------------------------------------------------
# per-hit landing of a multi-hit skill (section 15)
#
# `VERIFIED` in 1.16.2 (named dump on :8765); the addresses are those functions in 2.7.2.0:
#   FUN_140d27980          AttackDamageInfo+0x58 = AtkParam.knockbackDist (+0x10); the same
#                          function writes +0x40 attackParamId and +0x44 the pc/npc type.
#   FUN_140446d90          called from ApplyDamage 0x140449e10 after the poise damage. With
#                          lv = FUN_140691550(level) (the poise remap, frame-advantage.md) the
#                          defender's knockback is started only when lv != 0:
#                              dist = (1 - clamp(resist, 0, 1)) x max(dot, 0.4) x info+0x58 x info+0x200
#                          resist = knockback module vtable slot 2; lv == 0 sets resist to 1.
#   FUN_14068add0          player slot 2: chest EquipParamProtector.knockBack x 0.01. Every
#                          protector row has knockBack 0 and knockbackParamId 1 (regulation).
#   FUN_140450b20          on the damage animation (HksAct 0x14040d8dd): ContTime c and DecTime d of
#                          KnockBackParam[row] for the animation type (FUN_140d3f7b0 row+4i,
#                          FUN_140d3f7d0 row+0x3c+4i), speed v = dist x clamp(c / (0.5 d + c), 0, 1) / c,
#                          total time c + d (FUN_140450ce0).
#   FUN_140450f00          per frame: the speed while more than d is left, then scaled by
#                          remaining / d. So the defender moves exactly `dist`, at v for c seconds
#                          and slowing linearly to 0 over d.
# `MEASURED` (er-hkx-pose): the small, middle, large, push and minimum damage clips carry no root
# motion; small blow (a000_005400) and exlarge (005450) carry 4.4 m and 6.9 m backwards.
# `INFERRED`: the attack direction (info+0x1c0) is taken along the line to the defender, so
# max(dot, 0.4) = 1 for a defender straight ahead; info+0x200 and the module's per-frame
# multiplier (+0x48) are taken as 1.0; the animation type the behavior script passes for each
# damage level is paired by name below; a knockback and the clip's root motion add.
#
# The attacker's hit stop (FUN_140448950 -> FUN_140450320) freezes the attacker, scaled by the
# defender's NpcParam.hitStopType; rows 0 and 1 (`Human`) have type 0, so a player target is taken
# to cause none (`INFERRED`: which NpcParam row a player reads was not traced).

#: Damage level (frame-advantage.md) -> KnockBackParam column stem (`INFERRED` by the names:
#: 小 small, 中 middle, 大 large, 大吹っ飛び big blow, プッシュ push, 叩きつけ slam, 小吹っ飛び small blow,
#: 極小 minimum, 打ち上げ uppercut, ブレス breath).
KNOCKBACK_COLUMN = {1: 'damage_S', 2: 'damage_M', 3: 'damage_L', 4: 'damage_BlowM', 5: 'damage_Push',
                    6: 'damage_Strike', 7: 'damage_BlowS', 8: 'damage_Min', 9: 'damage_Uppercut',
                    10: 'damage_BlowM', 11: 'damage_Breath'}
#: KnockBackParam row and resist every player armor gives (regulation: all 838 protector rows
#: carry knockBack 0, knockbackParamId 1; the selftest re-reads it).
KNOCKBACK_PLAYER_ROW = 1
KNOCKBACK_RESIST = 0.0
#: FUN_140446d90 floors the facing term at .rdata 0x1432a3bdc = 0.4 (`VERIFIED`); the dot itself
#: is 1 for a defender straight ahead (`INFERRED`, above).
KNOCKBACK_FACING_FLOOR = 0.4
KNOCKBACK_FACING_DOT = 1.0
#: The defender distances a hit is tested at (`er-mechanics-reach.FRONT_CONTACT_DISTANCES_M`).
LANDING_DISTANCES_M = (1.5, 2.0, 2.5, 3.0)
#: One game update (60 Hz, the rate the pose and bullets are sampled at). Hits that land within
#: one update of each other land together, and reach the behavior script as one damage event, so
#: DamageCount rises once (`INFERRED`: `ExecDamage` runs once per update from
#: `DamageCommonFunction`, frame-advantage.md; which of the two levels it then reads was not traced,
#: the later one is taken).
UPDATE_S = 1.0 / 60


def _update(seconds):
    """The 60 Hz game update a real time falls on."""
    return round(seconds / UPDATE_S)


#: Spacing a bullet's path is sampled at between two frames for a contact (the reach module's
#: `CAPSULE_POINT_SPACING_M`).
CONTACT_STEP_M = 0.1
_ROOT = {}


def knockback_offset(dist, cont, dec, tau):
    """Metres a defender has been pushed `tau` seconds after a knockback of `dist` with the
    KnockBackParam times `cont` and `dec` started (`VERIFIED` FUN_140450b20 / FUN_140450f00).
    A negative `dist` pulls him in (Gravitas's bullets carry -1.6 to -5.0)."""
    if dist == 0 or cont <= 0 or tau <= 0:
        return 0.0
    v = dist * min(1.0, max(0.0, cont / (0.5 * dec + cont))) / cont
    if tau <= cont:
        return v * tau
    u = min(tau - cont, dec)
    return v * cont + (v * (u - u * u / (2 * dec)) if dec > 0 else 0.0)


def _fa_tables(t):
    if t._fa_tables is None:
        t._fa_tables = _pose()[2].Tables(t.reg, t.regulation)
    return t._fa_tables


def _clip_root_back(level):
    """Seconds -> metres the damage clip of `level` carries its character backwards (+Z), or
    None when it carries none (`MEASURED` root motion; the first clip of the level, the front
    hit, is taken, `INFERRED`)."""
    if level not in _ROOT:
        pose, _, fa = _pose()
        info = fa.LEVELS.get(level)
        track = None
        if info and info[2]:
            clip = info[2][0]
            try:
                path = ATK.hkx_path(0, clip)
                length = ATK.hkx_duration(path)[0] if path else 0.0
                track = [max(0.0, pose.root_motion(0, clip, k / BULLET_FPS)[2])
                         for k in range(int(length * BULLET_FPS) + 1)]
            except (OSError, ValueError, KeyError, TypeError):
                track = None
            if track and max(track) < 1e-3:
                track = None
        _ROOT[level] = track
    track = _ROOT[level]
    if track is None:
        return None
    return lambda tau: track[max(0, min(len(track) - 1, int(tau * BULLET_FPS)))]


def displacement_fn(t, level, atk_row):
    """tau -> metres the defender has moved back since a hit of `atk_row` played the damage
    level `level` on him: the knockback plus the clip's root motion (see above)."""
    if not level:
        return lambda tau: 0.0
    row = t.knockback.get(KNOCKBACK_PLAYER_ROW) or {}
    col = KNOCKBACK_COLUMN.get(level)
    cont = row.get(f'{col}_ContTime', 0.0) if col else 0.0
    dec = row.get(f'{col}_DecTime', 0.0) if col else 0.0
    dist = (t.atk_extra.get(atk_row) or {}).get('knockbackDist', 0.0) or 0.0
    dist *= (1.0 - KNOCKBACK_RESIST) * max(KNOCKBACK_FACING_DOT, KNOCKBACK_FACING_FLOOR)
    root = _clip_root_back(level)
    return lambda tau: knockback_offset(dist, cont, dec, tau) + (root(tau) if root else 0.0)


_REACTION = {}


def _reaction(level):
    """`er-mechanics-frame-advantage.reaction`, cached per damage level."""
    if level not in _REACTION:
        _REACTION[level] = _pose()[2].reaction(level) if level else None
    return _REACTION[level]


def _hit_contacts(t, weapon_id, sword_arts_id, hits):
    """Per hit: (start real seconds, contact(distance_at) -> real seconds or None, source,
    `contact_geometry` of the same samples). A hit with no pose or flight gets a geometry that
    touches anyone at its window's start (`INFERRED`, the landing's own rule). A measured geometry
    carries its clip's whole root motion as `ADV` (`clip_advance`), where the attacker ends up for
    the punish (`attack_advance`)."""
    import numpy as np
    out = []
    cat = SKILL_TAE_BASE + t.arts[sword_arts_id]['swordArtsTypeNew']
    # A geometry carries its clip's turn rules (`TURN`): the attacker's facing follows them as he
    # tracks the defender (`_facing`, version 27).
    for h, (start, contact, source, raw, cue) in zip(hits, _hit_contacts_raw(t, weapon_id, sword_arts_id, hits)):
        geom = None
        if source == 'pose':
            geom = contact_geometry(entries=raw)
        elif source == 'flight':
            geom = contact_geometry(bullet_points=raw)
            if geom is not None and cue is not None:
                geom['CUE'] = cue
        if geom is not None and h.get('anim') is not None:
            adv = clip_advance(cat, h['anim'])
            if adv is not None:
                geom['ADV'] = adv
            # The clip's turn rules, which the attacker's facing follows (`clip_turn`, version 27).
            geom['TURN'] = clip_turn(cat, h['anim'], (skill_tae(t, sword_arts_id) or {}).get(h['anim']))
        if source == 'timing':
            geom = {'T': np.array([start * TAE_FPS]), 'X': np.zeros(1), 'Z': np.full(1, -99.0),
                    'R': np.full(1, 1e3), 'PK': np.full(1, -math.inf)}
        out.append((start, contact, source, geom))
    return out


def _hit_contacts_raw(t, weapon_id, sword_arts_id, hits):
    """Per hit: (start real seconds, contact(distance_at) -> real seconds or None, source, the
    samples behind it: `window_points` entries for 'pose', [(seconds, x, z, disc, peak, launch
    seconds)] for 'flight': each bullet launched where `skill_bullet_launch` puts it and held back
    by the defender's body as the caster was at the launch (`bullet_launch`), and the flight's
    `CUE` (`tree_cue`: the real frame a search launches its shots, None for the cast's timing)."""
    _, reach_mod, _ = _pose()
    cat = SKILL_TAE_BASE + t.arts[sword_arts_id]['swordArtsTypeNew']
    tae = skill_tae(t, sword_arts_id) or {}
    prof = skill_profile(t, sword_arts_id, weapon_id)
    profile = reach_mod.defender_profile('idle')
    out = []
    for h in hits:
        events = tae.get(h.get('anim')) or []
        start = ATK.clip_to_real(events)(h['frame'] / TAE_FPS) if events else h['frame'] / TAE_FPS
        contact, source, raw, cue = None, 'timing', None, None
        if h['kind'] == 'melee':
            row = reach_mod.skill_reach(weapon_id, cat, h['anim'], h['judge'])
            entries = ((row or {}).get('window_contacts') or {}).get((h['frame'], h['judge']))
            if entries:
                source, raw = 'pose', entries

                def contact(dist_at, entries=entries):
                    return reach_mod.window_contact_time(entries, dist_at)
        elif h['kind'] == 'bullet' and profile:
            root = next((x['bullet'] for x in prof['anims'].get(h['anim'], [])
                         if x['kind'] == 'bullet' and x.get('bullet')
                         and x['bullet']['bullet'] == h.get('root_bullet') and x['frames'][0] == h['frame']),
                        None)
            if root:
                pts = []
                launch = skill_bullet_launch(t, weapon_id, sword_arts_id, h['anim'], h['frame'], h['root_bullet'])
                peak = launch['peak'] if launch else -math.inf
                entries = fly_shots(t, root, None, launch)
                cue = tree_cue(t, entries, [i for i, e in enumerate(entries) if e[0]['atk_row'] == h['atk_row']], start)
                for node, path, t0, _ in fly_tree(t, root, launch=launch):
                    if node['atk_row'] != h['atk_row']:
                        continue
                    for k, q in enumerate(path):
                        # A 200 m/s bolt moves 3.3 m a frame, past a body 0.6 m across: the
                        # segment to the next frame is sampled every `CONTACT_STEP_M`.
                        nxt = path[k + 1] if k + 1 < len(path) else q
                        n = max(1, math.ceil(math.dist(q, nxt) / CONTACT_STEP_M))
                        for j in range(n if k + 1 < len(path) else 1):
                            u = j / n
                            p3 = [q[i] + (nxt[i] - q[i]) * u for i in range(3)]
                            d = reach_mod.contact_disc_radius(p3[1], node['radius_m'], profile)
                            if d is not None:
                                pts.append((start + t0 + (k + u) / BULLET_FPS, p3[0], p3[2], d, peak, start))
                pts.sort()
                source, raw = 'flight', pts
                push = reach_mod.PUSH_CAPSULE_RADIUS

                def contact(dist_at, pts=pts, peak=peak, start=start, push=push):
                    # The caster is held back where the defender stood when the shot left.
                    held = max(0.0, peak - (dist_at(start) - 2 * push)) if math.isfinite(peak) else 0.0
                    for when, x, z, d, _, _ in pts:
                        if math.hypot(x, z - held - dist_at(when)) < d:
                            return when
                    return None
        if contact is None:
            # No pose or no flight for this hit: it is taken to touch when its window opens.
            def contact(dist_at, start=start):
                return start
        out.append((start, contact, source, raw, cue if source == 'flight' else None))
    return out


def skill_landing(t, weapon_id, sword_arts_id, hits, poises, distances=LANDING_DISTANCES_M):
    """Which hits of a skill land on a defender, and the damage they carry (section 15).

    For each defender distance D (the defender's axis D metres straight ahead) and each corpus
    poise p, the hits are played in the order they reach him:
      * a hit touches when its own hit shapes (pose samples of its window, `window_contacts`) or
        its bullets (`fly_tree`) reach the defender, who stands at D plus whatever earlier hits
        pushed him back (`displacement_fn`);
      * the first hit that touches opens; after it a hit lands only while the defender cannot
        leave: the same real frame as the previous landed hit, or before the first roll or guard
        frame of the last damage animation a landed hit started
        (`er-mechanics-frame-advantage.reaction`, DamageCount chaining included). Once he can
        leave, no later hit counts;
      * poise damage (hit poise x FinalDamageRateParam.saRate, the stagger rule) accumulates from
        full. A break plays the hit's raw damage level and the poise refills (frame-advantage.md:
        two toughness updates later); it is not regenerated otherwise within one skill
        (`INFERRED`). While poise holds, the level goes through SpEffect 6352: 0 (an additive
        flinch that neither locks nor pushes, and leaves a running damage animation running) for
        small to large, a reaction of its own for small blow, exlarge, fling, upper and breath.
        Every reaction that plays starts its knockback.
    Hits in another animation than the first hit's are left out (the hand-over is not traced).
    Returns {'dmg', 'dmg_all', 'by_distance': {D: {'hits', 'dmg'}}, 'hit_share': [per hit],
    'connects': [D...], 'sources': [...]} with dmg the mean over the distances the skill connects
    at (all four when it connects at none) and the poises."""
    fa = _pose()[2]
    ft = _fa_tables(t)
    total_all = sum(h.get('pvp_damage_total', 0.0) for h in hits)
    if not hits:
        return {'dmg': 0.0, 'dmg_all': 0.0, 'by_distance': {}, 'hit_share': [], 'connects': [],
                'sources': [], 'geometry': None}
    anim = hits[0].get('anim')
    idx = [i for i, h in enumerate(hits) if h.get('anim') == anim]
    contacts = _hit_contacts(t, weapon_id, sword_arts_id, [hits[i] for i in idx])
    order = sorted(range(len(idx)), key=lambda k: contacts[k][0])
    info = []
    for k in order:
        h = hits[idx[k]]
        fr = t.final_rate.get(h['final_rate_id']) if h['final_rate_id'] >= 0 else None
        row = h['atk_row']
        # The level a poise break plays, and the one it plays while poise holds (SpEffect 6352
        # leaves small blow, exlarge, fling, upper and breath reacting, frame-advantage.md).
        levels = {broken: fa.reaction_level(ft, row, broken) if row in ft.atk else 0
                  for broken in (True, False)}
        info.append({'i': idx[k], 'contact': contacts[k][1], 'start': contacts[k][0],
                     'rec': h.get('record'), 'prio': h.get('priority'), 'created': h.get('created', idx[k]),
                     'list': h.get('hit_list'),
                     'dmg': h.get('pvp_damage_total', 0.0),
                     'poise': (h.get('poise') or 0.0) * (fr['saRate'] if fr else 1.0),
                     'level': levels[True],
                     'on': {broken: (_reaction(lv), displacement_fn(t, lv, row))
                            for broken, lv in levels.items()}})
    # Without a poise distribution the defender's poise is taken to hold (`INFERRED`).
    pool = collections.Counter(poises or [math.inf])
    n_poise = sum(pool.values())

    def play(dist, p):
        breaks = []                         # (real seconds, displacement fn)

        def dist_at(when):
            off = 0.0
            for j, (tb, move) in enumerate(breaks):
                if when <= tb:
                    break
                end = breaks[j + 1][0] if j + 1 < len(breaks) else when
                off += move(min(when, end) - tb)
            return dist + off

        landed, cum, count, lock_end, last = [], 0.0, 0, None, None
        held = {}                           # hit priority record -> its entry (`ATK.record_contact`)
        lists = set()                       # bullet hit lists that have hit him (`HIT_LIST_NOTE`)
        pending = list(info)
        while pending:
            # The next hit to reach him, from where the earlier ones left him (a swing whose
            # window opens first can reach him after a bullet fired later: Stormcaller at 3 m).
            # One frame's ties go to the lower priority, then to the hit created first (DmgMan).
            timed = [(x['contact'](dist_at) if geometry else x['start'], x['prio'] or 0, x['created'], k)
                     for k, x in enumerate(pending)]
            timed = [(tc, k) for tc, _, _, k in sorted(r for r in timed if r[0] is not None)]
            if not timed:
                break
            tc, k = timed[0]
            x = pending.pop(k)
            # A bullet whose hit list already holds him does not touch him again
            # (`HIT_LIST_NOTE`), so it registers nothing in a priority record either.
            if x['list'] is not None and x['list'] in lists:
                continue
            # A hit whose priority record another attack holds on him at an equal or lower
            # priority does nothing; a row of the holding launch lands (bd er-effects-rs-dnok,
            # er-effects-rs-xb3i).
            if x['rec'] is not None and not ATK.record_contact(held, x['rec'], x['prio'], x['created']):
                continue
            if last is not None and _update(tc) != _update(last) and (
                    lock_end is None or tc * TAE_FPS >= lock_end):
                break
            landed.append(x)
            if x['list'] is not None:
                lists.add(x['list'])
            last = tc
            cum += x['poise']
            # Toughness at 0 or below breaks (`FUN_140486bf0`, `VERIFIED`; `CaughtModel` and
            # `er-mechanics-interrupt` read it so); `>` until 2026-11-16.
            broken = cum >= p
            if broken:
                cum = 0.0
            react, move = x['on'][broken]
            if react and react['locks']:
                # A new damage animation: the lock and the push restart at this hit. Two hits in
                # one update reach the behavior script once, so they count once.
                again = bool(breaks) and _update(tc) == _update(breaks[-1][0])
                if not again:
                    count = min(count + 1, 4) if react['counts'] else 1
                    breaks.append((tc, move))
                else:
                    breaks[-1] = (breaks[-1][0], move)
                esc = [v for v in (react['roll'][max(count, 1)], react['guard']) if v is not None]
                lock_end = tc * TAE_FPS + min(esc) if esc else None
        return landed

    def over(dists):
        by, share = {}, collections.Counter()
        for dist in dists:
            n_hits = dmg = 0.0
            for p, w in pool.items():
                landed = play(dist, p)
                n_hits += w * len(landed)
                dmg += w * sum(x['dmg'] for x in landed)
                for x in landed:
                    share[(dist, x['i'])] += w
            by[dist] = {'hits': round(n_hits / n_poise, 3), 'dmg': dmg / n_poise}
        return by, share

    geometry = True
    by_distance, share = over(distances)
    connects = [d for d in distances if by_distance[d]['hits'] > 0]
    use = connects
    if not connects:
        # The measured shapes and flights touch the defender at none of the distances: each hit
        # is then taken to touch when its window opens, and only the lock decides (`INFERRED`).
        geometry = False
        by_distance, share = over(distances[:1])
        use = distances[:1]
    dmg = sum(by_distance[d]['dmg'] for d in use) / len(use)
    hit_share = [round(sum(share[(d, i)] for d in use) / (n_poise * len(use)), 3) for i in range(len(hits))]
    return {'dmg': dmg, 'dmg_all': total_all, 'by_distance': by_distance, 'hit_share': hit_share,
            'connects': connects, 'sources': [contacts[k][2] for k in range(len(idx))],
            'geometry': 'measured' if geometry else 'timing only', 'used': use,
            'share_by_distance': {d: [share[(d, i)] / n_poise for i in range(len(hits))] for d in use}}


def skill_reaction(t, weapon_id, sword_arts_id, hits, land, offset=0.0, cue=0.0, my_roll=None,
                   strikes=None, share=None, look=None):
    """The landed damage of one part of a skill once a waiting defender may dodge it on reaction
    (section 16). `hits` and `land` are one `skill_landing` call; the hit shapes are the ones the
    landing played (`_hit_contacts`), moved `offset` real frames on (where that animation starts
    in the skill), and the defender reads the timing from `cue`. When hit k is the first to catch
    him, the part is worth the hits that open at or after k, each at its landing share at that
    distance. Returns `dmg` = the mean over the landing's distances of (1 - share) x landed +
    share x `reaction_outcome` value, plus `react_factors`.

    `look` is a part ({'hits', 'offset'}, `skill_release_part`: a charged skill's early release)
    the defender cannot tell this one from until `look['offset']`, its release (the comment above
    `_escape`): his dodge is timed by `lookalike_pair` between the two, their shapes read the way
    this part's are (measured, or touching when the window opens when the landing found no
    contact). With `look['releases']` the release is let go on any of those frames after the press
    (`release_frames`), each a lookalike until its own release. None, he knows the attack from
    `cue`."""
    import numpy as np
    share = REACT_SHARE if share is None else share
    if not hits or not land or land['dmg'] <= 0 or not land.get('used'):
        return None

    def shapes(hs, at):
        # The contact shapes of the hits of `hs`' first animation, `at` real frames on.
        anim = hs[0].get('anim')
        ix = [i for i, h in enumerate(hs) if h.get('anim') == anim]
        con = _hit_contacts(t, weapon_id, sword_arts_id, [hs[i] for i in ix])
        if land['geometry'] == 'measured':
            gs = [c[3] for c in con]
        else:
            # The landing found no contact at any distance and let the lock alone decide: every
            # hit touches when its window opens, wherever he is (`INFERRED`, the same rule).
            gs = [{'T': np.array([c[0] * TAE_FPS]), 'X': np.zeros(1), 'Z': np.full(1, -99.0),
                   'R': np.full(1, 1e3), 'PK': np.full(1, -math.inf)} for c in con]
        return ix, [c[0] for c in con], [shift_geometry(g, at) for g in gs]
    idx, starts, geoms = shapes(hits, offset)
    other = shapes(look['hits'], float(look['offset']))[2] if look is not None and look.get('hits') else None
    # Where the part's root motion leaves the attacker, held back by his body (`attack_advance`,
    # `advance_at`): the largest `PK` alone overshot him when the travel passed the distance
    # (Savage Claws, 8.93 m, at 2 m).
    advance = attack_advance(geoms)
    total, outs = 0.0, []
    for d in land['used']:
        sh = land['share_by_distance'][d]
        value = [sum(hits[idx[j]].get('pvp_damage_total', 0.0) * sh[idx[j]]
                     for j in range(len(idx)) if starts[j] >= starts[k]) for k in range(len(idx))]
        plan = lookalike_pair(other, d, float(look['offset']), look.get('releases'), look.get('shows'),
                              look.get('roll')) if other is not None else None
        o = reaction_outcome(geoms, value, d, cue, my_roll=my_roll, advance=advance, strikes=strikes, plan=plan)
        outs.append(o)
        total += (1.0 - share) * land['by_distance'][d]['dmg'] + share * o['value']
    f = react_factors(outs, share) or {}
    return {'dmg': total / len(land['used']), 'dmg_landing': land['dmg'], 'cue': cue, **f}


def skill_contest_inputs(t, weapon_id, sword_arts_id, option):
    """What `er-mechanics-exchange` needs to contest a skill option the way it contests a slot:
    `strike` (real frame, from the skill's start, at which its first part first touches a defender
    `ENGAGE_DISTANCE_M` ahead: the same shapes and bullet flights the landing plays; else its first
    hit's start), `poise` (that hit's poise in menu units x `FinalDamageRateParam.saRate`),
    `hyper` (the part's TAE 795 windows, real frames from the skill's start, as
    `er-mechanics-exchange.hyper_windows` returns them) and `stamina` (the `stamina_cost` of every
    melee event of the option: each event that creates a hitbox charges it, attacks.md section 3).
    None when the option has no hit."""
    parts = option.get('parts') or [{'hits': option.get('hit_rows') or [], 'offset': 0.0}]
    hits = parts[0]['hits'] if parts else []
    if not hits:
        return None
    offset = parts[0].get('offset') or 0.0
    anim = hits[0].get('anim')
    idx = [h for h in hits if h.get('anim') == anim]
    con = _hit_contacts(t, weapon_id, sword_arts_id, idx)
    best = None
    for h, (start, contact, _, _) in zip(idx, con):
        when = contact(lambda _w: ENGAGE_DISTANCE_M)
        when = start if when is None else when
        if best is None or when < best[0]:
            best = (when, h)
    when, h = best
    fr = t.final_rate.get(h['final_rate_id']) if h.get('final_rate_id', -1) >= 0 else None
    poise = (h.get('poise') or 0.0) * (fr['saRate'] if fr else 1.0)
    prof = skill_profile(t, sword_arts_id, weapon_id)
    events = (skill_tae(t, sword_arts_id) or {}).get(anim) or []
    to_real = ATK.clip_to_real(events) if events else (lambda s: s)
    hyper = []
    for x in prof['anims'].get(anim, []):
        if x['kind'] != 'hyperarmor':
            continue
        a, b = x['frames']
        a = to_real(a / TAE_FPS) * TAE_FPS + offset
        b = to_real(b / TAE_FPS) * TAE_FPS + offset if b is not None else math.inf
        hyper.append((a, b, x['poise_bonus_menu'], x.get('pvp_poise_damage_taken') or 1.0))
    stamina = sum(x.get('stamina_cost') or 0 for x in option.get('hit_rows') or [] if x['kind'] == 'melee')
    cat = SKILL_TAE_BASE + t.arts[sword_arts_id]['swordArtsTypeNew']
    return {'strike': round(when * TAE_FPS + offset, 1), 'poise': poise, 'hyper': hyper,
            'stamina': stamina, 'tae_entry': f'a{cat:03d}_{anim:06d}' if anim is not None else None}


def skill_buff_alternatives(t, weapon_id, choice, level=0, fp_bar=FP_BAR_DEFAULT,
                            engagements=ENGAGEMENTS_DEFAULT):
    """`skill_term`'s `buff_alternatives` without scoring any hit: [(p, ((root, 1.0, casts),
    ...))] for each skill in `choice` that buffs its user. The ranking needs these before it
    scores the slots, because the buffs scale every slot."""
    out = []
    for sid, p in choice:
        try:
            prof = skill_profile(t, sid, weapon_id, level)
        except (SystemExit, KeyError, StopIteration, TypeError):
            continue
        roots = [s['id'] for s in skill_buffs(t, prof) if s.get('effect') == 'buff']
        if roots:
            uses = fp_uses(fp_bar, skill_fp(t, sid))
            casts = float(uses if uses is not None else _hits_cap(engagements))
            out.append((p, tuple((r, 1.0, casts) for r in roots)))
    return out


# ---------------------------------------------------------------------------------------------
# utility ashes: what a skill with no scored hit is worth in an engagement (section 14)

#: The medium roll, a000 `027110`-`027113` (`er-builds-pvp.SCORE_ENTRY_FRAMES` names 027110 as the
#: medium roll, `VERIFIED` TAE): the four directions, forward -Z, back +Z, +X, -X by root motion.
#: The sweep builds sit at medium load (`er-builds-optimize --roll medium`).
ROLL_CATEGORY, ROLL_ANIMS = 0, (27110, 27111, 27112, 27113)
#: Stamina a roll and a rolling skill charge: c0000.hks `ExecEvasion` calls
#: `AddStamina(STAMINA_REDUCE_ROLLING)` and `ExecAttack` `AddStamina(STAMINA_REDUCE_ARTS_QUICKSTEP)`
#: for the rolling arts (every one but swordArtsTypeNew 313); common_define.hks sets both to -12
#: (`VERIFIED`, `er-hks-disasm.py`). With FP the step's charge is multiplied by
#: `STAMINA_CONSUMERATE_LOWSTATUS` 1.5 on one branch; which branch is the short-FP one is read from
#: the constant's name (`INFERRED`), so the step and the roll cost the same and stamina drops out.
STAMINA_ROLL = STAMINA_STEP = 12
#: Section 14a's dodge timing, kept for comparison (`Opponents(timing='uniform')`): the press lands
#: uniformly over this many frames before the incoming hit (`INFERRED`: one second, the span
#: `er-builds-pvp.SCORE_ADV_SPAN` gives frame advantage). Section 16 times it by reaction instead.
DODGE_TIMING_FRAMES = 30
#: Distance to the opponent when the exchange starts (`er-mechanics-exchange.STRIKE_DISTANCE_M`).
ENGAGE_DISTANCE_M = 2.5
#: Opponent attack numbers for a pool build the ranking has no row for (and for every one in a
#: `--weapon` run without `--opponents-from`): pool-weighted means of the RL 140-160 exchange
#: pool's R1 #1 over the full RL 150 ranking (`MEASURED` 2026-09-29, `opponents_from_results`;
#: 95.3% of pool builds matched; `startup` is the exchange strike frame at 2.5 m, 16.6).
#: No chained R1 #2 is assumed for them.
OPPONENT_FALLBACK = {'active': 3.2, 'recovery': 10.7, 'reach': 3.69, 'hp': 388.0, 'startup': 16.6}
#: SpEffect category whose dmgLv_* override the reaction when poise breaks
#: (`er-mechanics-frame-advantage.py`, `VERIFIED` 0x140691550 path).
REACTION_OVERRIDE_CATEGORY = 1001
_MOTION = {}
_POSE = []


def _pose():
    if not _POSE:
        _POSE.append(_load('er_hkx_pose', 'er-hkx-pose.py'))
        _POSE.append(_load('er_mechanics_reach', 'er-mechanics-reach.py'))
        _POSE.append(_load('er_mechanics_frame_advantage', 'er-mechanics-frame-advantage.py'))
    return _POSE


_NEUTRAL = []


def _neutral():
    """`er-mechanics-neutral`, loaded once."""
    if not _NEUTRAL:
        _NEUTRAL.append(_load('er_mechanics_neutral', 'er-mechanics-neutral.py'))
    return _NEUTRAL[0]


#: Score a dodge skill's disengage term (`er-mechanics-disengage.skill_vs_roll`, disengage.md):
#: the heal an escape away from the chaser buys over the same escape with the medium roll. None
#: (off) by default; `er-builds-pvp.py --disengage [sprint|run]` sets how fast the chaser follows.
#: `opponents_from_results` then also records each opponent's chasing attacks.
DISENGAGE = None
_DISENGAGE = []


def _disengage():
    """`er-mechanics-disengage`, loaded once."""
    if not _DISENGAGE:
        m = _load('er_mechanics_disengage', 'er-mechanics-disengage.py')
        # It reads the dodge and reaction model from this module rather than loading a second copy.
        m._MODS['er-mechanics-ashes'] = types.SimpleNamespace(
            evasion_motion=evasion_motion, reaction_delays=reaction_delays, dodge_presses=dodge_presses,
            DODGE_STEP=DODGE_STEP)
        _DISENGAGE.append(m)
    return _DISENGAGE[0]


def _jt8_unconditional(e):
    """A JumpTable 8 (invincible) event with no stateInfo condition. The medium roll's second
    window f13-16 carries 290 at Args+14 (u16), the stateInfo `er-mechanics-talismans` lists as
    `roll i-frames +3`, and is left out (`INFERRED`: the field gates the window on that state)."""
    p = e.params
    return (e.type == EV_JUMP_TABLE and struct.unpack_from('<i', p, 0)[0] == JT_INVINCIBLE
            and struct.unpack_from('<H', p, 14)[0] == 0)


def evasion_motion(category, anim):
    """One dodge animation, in real frames from its first frame: `iframes` (the frame the
    unconditional invincibility from frame 0 ends), `ready` (first R1 cancel), `dodge` (first
    dodge cancel), `move` (first move cancel: the first frame he can walk or run, `punish_lead`),
    and `path` [(x, z)] root motion per real frame (-Z forward). Cached."""
    key = (category, anim)
    if key in _MOTION:
        return _MOTION[key]
    pose, reach, _ = _pose()
    cat, src, events = reach.resolve_clip(category, anim)
    if not events:
        _MOTION[key] = None
        return None
    to_real = ATK.clip_to_real(events)
    end = 0.0
    for e in sorted((e for e in events if _jt8_unconditional(e)), key=lambda e: e.start):
        if e.start * TAE_FPS <= end + 0.5:
            end = max(end, to_real(min(e.end, 1e3)) * TAE_FPS)
    win = ATK.recovery_windows(events, 0.0)
    first = {k: ATK._first_open(win[k], inputs is not None) for k, _, inputs, _ in ATK.RECOVERY_ACTIONS}
    real = {k: (to_real(v) * TAE_FPS if v is not None else None) for k, v in first.items()}
    clip = pose.load_animation(*reach.hkx_source(cat, src))
    path, steps = [], int(clip.duration * TAE_FPS) + 1
    samples = [(to_real(i / TAE_FPS) * TAE_FPS, clip.root_motion(i / TAE_FPS)) for i in range(steps)]
    j = 0
    for f in range(int(samples[-1][0]) + 1):
        while j + 1 < len(samples) and samples[j + 1][0] <= f:
            j += 1
        x, _, z, _ = samples[j][1]
        path.append((x, z))
    out = {'anim': f'a{category:03d}_{anim:06d}', 'iframes': round(end, 1), 'ready': real['r1'],
           'dodge': real['dodge'], 'move': real['move'], 'path': path,
           'distance': round(math.hypot(*path[-1]), 2)}
    _MOTION[key] = out
    return out


def roll_motion():
    """`evasion_motion` of the four medium-roll directions."""
    return [m for m in (evasion_motion(ROLL_CATEGORY, a) for a in ROLL_ANIMS) if m]


def dodge_families(air=False):
    """The families of moves a reacting defender picks among (`sequence_outcome`, `volley_outcome`,
    `FollowupModel`, attack-similarity's `OutcomeForm`): each medium-roll direction alone, and the
    jump when a hit is `AIR`. Knowing the attack he takes the one that leaves the attacker least,
    as he picks the jump over the roll; the directions are the lock-on's, relative to him facing
    the attacker (`roll_motion`). His first dodge's timing within a family is still his lookalike
    group's (`lookalike_plan`, one plan per family). Until version 22 the four directions were one
    family pressed a quarter each (`INFERRED`, section 14a), which against Divine Beast Frost
    Stomp, whose side rolls always escape and whose forward and back rolls never do, gave an evade
    of 0.5 where any defender picks a side (`MEASURED` 2026-11-16)."""
    out = [[m] for m in roll_motion()]
    if air:
        out.append([jump_motion()])
    return out


#: The jump in place, a000 `202000` (`er-mechanics-jump.JUMP_KINDS`, stick at rest: no travel).
#: Its JumpTable 132 makes the jumper immune to an AtkParam with `isInvalidatedByNoDamageInAir`
#: and to nothing else (moveset.md section 6a, `VERIFIED` 1.16.2).
JUMP_CLIP = 202000
JT_AIR_IMMUNE = 132
_JUMP = []


def _jump():
    """`er-mechanics-jump`, loaded once."""
    if not _JUMP:
        _JUMP.append(_load('er_mechanics_jump', 'er-mechanics-jump.py'))
    return _JUMP[0]


def jump_motion():
    """The jump as an evasion move, `evasion_motion`'s keys plus `air` (its invincibility holds only
    against an `AIR` hit, `_catches`): `iframes` from frame 0 to the end of the jump clip, which
    cuts JumpTable 132 short (`INFERRED`: the flag goes with the clip; the event runs to frame 31),
    `ready` and `dodge` at the landing (`INFERRED`: the landing's own recovery is not read), `path`
    the clip's travel, none at rest. Cached."""
    key = ('jump', JUMP_CLIP)
    if key not in _MOTION:
        J = _jump()
        p = J.jump_path(JUMP_CLIP)
        win = J._windows(J._jump_events(JUMP_CLIP), J.TAE_JUMP_TABLE, JT_AIR_IMMUNE)
        end = min(max((e for s, e in win if s <= 0.5), default=0.0), p['frames'])
        path = [(0.0, -J.jump_travel(JUMP_CLIP, f)) for f in range(int(math.ceil(p['landing'])) + 1)]
        _MOTION[key] = {'anim': f'a000_{JUMP_CLIP:06d}', 'iframes': round(end, 1), 'ready': p['landing'],
                        'dodge': p['landing'], 'path': path, 'distance': round(math.hypot(*path[-1]), 2),
                        'air': True}
    return _MOTION[key]


def skill_evasion(t, sword_arts_id, weapon_id):
    """The paid animations of a dodge skill: the animations of its opening's ten-block, without
    the without-FP copies, that have invincibility from frame 0 (Bloodhound's Step 040080-040083,
    four directions). Which ten-block the behavior script plays per grip and lock-on is not traced;
    the opening's is taken (`INFERRED`)."""
    prof = skill_profile(t, sword_arts_id, weapon_id)
    opening = main_anim(prof)
    if opening is None:
        return []
    cat = SKILL_TAE_BASE + t.arts[sword_arts_id]['swordArtsTypeNew']
    out = []
    for a in prof['anims']:
        if a // 10 != opening // 10 or a in prof['no_fp_anims']:
            continue
        m = evasion_motion(cat, a)
        if m and m['iframes'] > 0:
            out.append(m)
    return out


def defensive_buff(t, sword_arts_id, weapon_id):
    """The defensive part of a buff skill's paid opening, or None: per buff root SpEffect, the
    PvP damage-taken rate (`defPlayerDmgCorrectRate_*`, mean over elements) and whether it is a
    category-1001 reaction override whose dmgLv_* remap every level it sets to 0 (the reaction
    when poise breaks becomes the additive flinch: `er-mechanics-frame-advantage`, `VERIFIED`).
    `start` is the real frame the event applying it opens, `ready` the opening's first R1."""
    prof = skill_profile(t, sword_arts_id, weapon_id)
    opening = main_anim(prof)
    if opening is None:
        return None
    _, _, fa = _pose()
    best = None
    for x in prof['anims'].get(opening, []):
        s = x.get('speffect') if x['kind'] == 'speffect' else None
        if not s or x.get('effect') != 'buff':
            continue
        row = t.speffect[s['id']]
        rates = [row.get(f'defPlayerDmgCorrectRate_{VS_PLAYER[el]}', 1.0) for el in ELEMENTS]
        levels = [row.get(f, 0) for f in fa.DMG_LV_FIELDS[1:]]
        override = (row.get('spCategory') == REACTION_OVERRIDE_CATEGORY and any(levels)
                    and all(fa.remap(v, i + 1) == 0 for i, v in enumerate(levels) if v))
        taken = sum(rates) / len(rates)
        if taken >= 1.0 and not override:
            continue
        events = (skill_tae(t, sword_arts_id) or {}).get(opening) or []
        to_real = ATK.clip_to_real(events)
        rec = anim_recovery(t, weapon_id, sword_arts_id, opening)
        # `vfxId` puts an effect on the character while the SpEffect runs (SpEffectParam, the
        # regulation value): the opponent sees the buff start.
        cand = {'root': s['id'], 'name': s['name'], 'duration_s': row['effectEndurance'],
                'damage_taken': round(taken, 3), 'uninterruptible': override,
                'visible': (row.get('vfxId') or -1) > 0,
                'start': ATK.real_frame(to_real(x['frames'][0] / TAE_FPS)), 'ready': rec.get('r1')}
        if best is None or cand['duration_s'] > best['duration_s']:
            best = cand
    return best


def utility_profile(t, sword_arts_id, weapon_id):
    """What a skill with no scored hit does in an engagement, or None: `dodge` (i-frames only),
    `defense` (a buff with a damage-taken cut or a reaction override) or `parry`."""
    prof = skill_profile(t, sword_arts_id, weapon_id)
    classes = set(prof['classes'])
    if 'parry' in classes:
        return {'kind': 'parry'}
    if classes == {'i-frames'}:
        moves = skill_evasion(t, sword_arts_id, weapon_id)
        return {'kind': 'dodge', 'moves': moves} if moves else None
    if 'buff' in classes or 'stance effect' in classes:
        d = defensive_buff(t, sword_arts_id, weapon_id)
        return {'kind': 'defense', **d} if d else None
    return None


class Opponents:
    """The attacks a dodge answers (section 14a, timed by section 16): per opponent row (weight
    `w`), the opener it throws (`startup` = its first live frame on a defender 2.5 m ahead,
    `active` frames, world `reach`, `hp`, `cue` = the frame its timing becomes readable, `advance`
    = how far its root motion carries the attacker) and, when the HKS chains a follow-up onto it,
    that hit too: `gap2` frames after the first hit, `reach2` measured from where the attacker
    started, `active2`, `hp2`. `end` is the frame, from the opener's start, at which the
    attacker can roll or attack again after the string."""

    FIELDS = ('cue', 'startup', 'active', 'reach', 'hp', 'end', 'gap2', 'reach2', 'active2', 'hp2',
              'advance', 'land', 'skill_escape_hp')

    def __init__(self, rows, timing='react'):
        import numpy as np
        self.np = np
        self.rows = rows
        self.timing = timing
        self.w = np.array([r['w'] for r in rows], float)
        self.w = self.w / self.w.sum()
        for f in self.FIELDS:
            setattr(self, f, np.array([r.get(f) or 0.0 for r in rows], float))
        self.startup = np.where(self.startup > 0, self.startup, OPPONENT_FALLBACK['startup'])
        self.active = np.maximum(1, np.ceil(self.active)).astype(int)
        self.active2 = np.ceil(self.active2).astype(int)
        self.follow = self.gap2 > 0
        self.end = np.where(self.end > 0, self.end, self.startup + self.active + OPPONENT_FALLBACK['recovery'])
        self.land = np.array([r.get('land', 1.0) for r in rows], float)
        # What a dodge avoids is the raw hit (the dodge model itself decides whether it lands);
        # what a defensive buff cuts is the hit as it lands on a defender who may also react.
        self.mean_hp = float((self.w * self.hp).sum())
        self.mean_hp_landed = float((self.w * self.hp * self.land).sum())
        self._tables = {}
        # {(row, move, first start, strike, reach): `_second_roll`} (version 27).
        self._seconds = {}

    def _escapes(self, dist, iframes, start, active, reach, starts):
        """[len(starts)]: a dodge started at each of `starts` (real frames from the opener's start)
        leaves every live frame of a hit (`start` .. `start` + `active` - 1) inside its
        invincibility or farther than `reach` from the attacker. `dist[u]` is the dodger's distance
        u frames into the dodge (the attacker `ENGAGE_DISTANCE_M` straight ahead, standing still
        after the clip); before the dodge he stands at that distance."""
        np = self.np
        f = start + np.arange(max(1, int(active)))
        u = f[None, :] - np.asarray(starts, float)[:, None]
        d = np.where(u < 0, ENGAGE_DISTANCE_M, dist[np.clip(np.floor(u).astype(int), 0, len(dist) - 1)])
        inv = (u >= 0) & (u < iframes)
        return np.all(inv | (d > reach), axis=1)

    def _table(self, moves, key, strike=None, reach_me=None):
        """Every dodge the pool's attacks draw from a defender who waits, flattened: weight `W`
        (row x press x direction), start `S`, `FIRST` (the opener missed), `SECOND` (the chained
        hit missed too, or there is none), the direction's R1 frame `READY`, `NEAR` (the dodger's
        distance at that frame from where the attacker's advance left him), its move cancel `MOVE`
        and `FAR` (that distance at the move cancel), `BF` / `BD` the frames between the two and
        that distance on each (padded with nan / inf; `punish_lead`'s rule) and the row's `END`.
        The dodge starts where `dodge_presses` puts it over the grid of starts that escape the
        opener, aimed (version 26, the rule every outcome's dodge follows since version 25) for what
        each press is worth to him: escaping the opener, plus his punish from it when `strike` (his
        strike frames) and `reach_me` are given, read as `dodge_value` reads it (`_ExchangeGain`).
        Until version 26 the openers were timed by escape alone. Cached on (`key`, `strike`,
        `reach_me`).

        Version 27: `MI` the direction's index in `moves` (he picks one per opener, `dodge_value`), and
        for a press that escapes the opener but whose roll does not escape the chained hit, his second
        roll of the same direction out of the first, as every outcome chains them (`dodge_chain`'s
        rule, `_second_roll`): `ESC2` the share of its presses that escape the chained hit, `PUN2` the
        share that escape it and punish from that roll (with `strike` and `reach_me`). Until then the
        chained hit's second dodge was the opener's single-hit evade share (`single`, still kept)."""
        np = self.np
        key = (key, strike, reach_me)
        if key in self._tables:
            return self._tables[key]
        cols = {k: [] for k in ('W', 'S', 'FIRST', 'SECOND', 'READY', 'NEAR', 'MOVE', 'FAR', 'BF', 'BD', 'END',
                                'ROW', 'MI', 'ESC2', 'PUN2')}
        single = np.zeros(len(self.w))

        def frames(m):
            ready = m['ready'] if m['ready'] is not None else len(m['path'])
            walk = ready if m.get('move') is None else max(float(m['move']), ready)
            return ready, walk, list(range(int(ready) + 1, int(walk)))
        width = max([len(frames(m)[2]) for m in moves] + [1])
        for mi, m in enumerate(moves):
            dist = np.array([math.hypot(x, ENGAGE_DISTANCE_M + z) for x, z in m['path']])
            ready, walk, between = frames(m)
            rx, rz = m['path'][min(int(ready), len(m['path']) - 1)]
            mx, mz = m['path'][min(int(walk), len(m['path']) - 1)]
            bf = np.full(width, np.nan)
            bf[:len(between)] = between
            for i in range(len(self.w)):
                grid = np.arange(0.0, self.startup[i] + self.active[i] + 1.0, DODGE_STEP)
                if self.timing == 'uniform':
                    presses = [(self.startup[i] - tau, 1.0 / DODGE_TIMING_FRAMES)
                               for tau in range(DODGE_TIMING_FRAMES)]
                else:
                    ok = self._escapes(dist, m['iframes'], self.startup[i], self.active[i], self.reach[i], grid)
                    if strike is not None and reach_me is not None:
                        # What escaping from each start is worth to him: 1, plus 1 where he punishes
                        # from it. That rises where a press first escapes the chained hit too, so it
                        # goes in as values (`dodge_presses` `val`, every rise an aim tried), not as a
                        # `PressGain`, whose search assumes a gain that only falls as the press comes later.
                        val = ok * (1.0 + _ExchangeGain(self, i, m, dist, strike, reach_me)(grid))
                        presses = dodge_presses(ok, self.cue[i], val=val)
                    else:
                        presses = dodge_presses(ok, self.cue[i])
                s = np.array([p for p, _ in presses])
                w = np.array([q for _, q in presses]) * self.w[i] / len(moves)
                first = self._escapes(dist, m['iframes'], self.startup[i], self.active[i], self.reach[i], s)
                second = self._escapes(dist, m['iframes'], self.startup[i] + self.gap2[i], self.active2[i],
                                       self.reach2[i], s) if self.follow[i] else np.ones(len(s), bool)
                single[i] += float((w * first).sum()) / self.w[i]
                cols['W'].append(w), cols['S'].append(s), cols['FIRST'].append(first)
                cols['SECOND'].append(second), cols['READY'].append(np.full(len(s), float(ready)))
                adv = self.advance[i] if self.timing == 'react' else 0.0
                cols['NEAR'].append(np.full(len(s), math.hypot(rx, ENGAGE_DISTANCE_M + rz - adv)))
                cols['MOVE'].append(np.full(len(s), float(walk)))
                cols['FAR'].append(np.full(len(s), math.hypot(mx, ENGAGE_DISTANCE_M + mz - adv)))
                bd = np.full(width, np.inf)
                bd[:len(between)] = [math.hypot(m['path'][f][0], ENGAGE_DISTANCE_M + m['path'][f][1] - adv)
                                     for f in between]
                cols['BF'].append(np.tile(bf, (len(s), 1))), cols['BD'].append(np.tile(bd, (len(s), 1)))
                cols['END'].append(np.full(len(s), self.end[i])), cols['ROW'].append(np.full(len(s), i))
                cols['MI'].append(np.full(len(s), mi))
                # His second roll where the first escapes the opener and not the chained hit.
                esc2, pun2 = np.zeros(len(s)), np.zeros(len(s))
                for j in np.nonzero(first & ~second)[0].tolist():
                    esc2[j], pun2[j] = self._second_roll(i, m, float(s[j]), strike, reach_me)
                cols['ESC2'].append(esc2), cols['PUN2'].append(pun2)
        tab = {k: np.concatenate(v) for k, v in cols.items()}
        tab['single'] = single
        self._tables[key] = tab
        return tab

    def _second_roll(self, i, m, s, strike=None, reach_me=None):
        """(share of his presses that escape row `i`'s chained hit, share that escape it and punish)
        for a defender who escaped the opener with move `m` from real frame `s` and whose roll does not
        escape the chained hit (`_table`, version 27): he rolls the same direction again, as every
        outcome chains its rolls (`dodge_chain`): no sooner than `s` plus `m`'s dodge cancel, aimed by
        `dodge_presses` from the row's cue over the starts that escape it, out of where the first roll
        has him at that start (its whole clip, `track_extend`), facing the attacker (`oriented`; the
        attacker `ENGAGE_DISTANCE_M` ahead, standing, as `_escapes` takes him). Before the second roll
        starts he is on the first, which covers what its invincibility or distance covers. His punish
        from it reads `_ExchangeGain`'s lead (the R1 cancel in reach, else in reach before the move
        cancel, else the move cancel plus his run in), from where it starts, against the row's
        advance and `END`."""
        np = self.np
        key = (i, m['anim'], round(s, 6), strike, reach_me)
        got = self._seconds.get(key)
        if got is not None:
            return got
        P = np.asarray(m['path'], float)
        n = len(P)
        iframes, reach2 = float(m['iframes']), self.reach2[i]
        fl = s + dodge_end(m)
        f0 = self.startup[i] + self.gap2[i]
        frames = f0 + np.arange(max(1, int(self.active2[i])))
        grid = np.arange(0.0, f0 + max(1, int(self.active2[i])) + 1.0, DODGE_STEP)
        u1 = frames - s
        p1 = P[np.clip(np.floor(u1).astype(int), 0, n - 1)]
        cov1 = ((u1 >= 0) & (u1 < iframes)) | (np.hypot(p1[:, 0], ENGAGE_DISTANCE_M + p1[:, 1]) > reach2)
        # Where the first roll has him at each second start, and the second's turn toward the attacker.
        q = P[np.clip(np.floor(grid - s).astype(int), 0, n - 1)]
        r = np.hypot(q[:, 0], ENGAGE_DISTANCE_M + q[:, 1])
        c, sn = (ENGAGE_DISTANCE_M + q[:, 1]) / r, -q[:, 0] / r
        u2 = frames[None, :] - grid[:, None]
        k2 = np.clip(np.floor(u2).astype(int), 0, n - 1)
        x2 = q[:, 0:1] + c[:, None] * P[k2, 0] - sn[:, None] * P[k2, 1]
        z2 = q[:, 1:2] + sn[:, None] * P[k2, 0] + c[:, None] * P[k2, 1]
        cov2 = (u2 < iframes) | (np.hypot(x2, ENGAGE_DISTANCE_M + z2) > reach2)
        ok2 = np.all(np.where(u2 < 0, cov1[None, :], cov2), axis=1) & (grid >= fl - 1e-9)
        presses = dodge_presses(ok2, self.cue[i], floor=fl)
        esc = pun = 0.0
        adv = self.advance[i] if self.timing == 'react' else 0.0
        ready = m['ready'] if m['ready'] is not None else n
        walk = ready if m.get('move') is None else max(float(m['move']), ready)
        for s2, w in presses:
            j = int(round(s2 / DODGE_STEP))
            if not (0 <= j < len(grid)) or not ok2[j]:
                continue
            esc += w
            if strike is None or reach_me is None:
                continue
            qx, qz, cj, sj = q[j, 0], q[j, 1], c[j], sn[j]

            def gap(f):
                px_, pz_ = P[min(int(f), n - 1)]
                return math.hypot(qx + cj * px_ - sj * pz_, ENGAGE_DISTANCE_M + qz + sj * px_ + cj * pz_ - adv)
            if gap(ready) <= reach_me:
                lead = float(ready)
            else:
                between = [f for f in range(int(ready) + 1, int(walk)) if gap(f) <= reach_me]
                lead = float(between[0]) if between else \
                    float(walk) + max(0.0, gap(walk) - reach_me) * _neutral().frames_per_metre()
            if s2 + lead + float(strike) < self.end[i]:
                pun += w
        self._seconds[key] = (esc, pun)
        return esc, pun

    def dodge_value(self, moves, key, strike, reach_me, hp_me):
        """Expected HP of answering one opponent string with `moves`, the dodge timed by a defender
        who waits and reacts (`_table`). Knowing the opener he takes, per opponent row, the
        direction of `moves` that is worth most to him (version 27, as every outcome picks his
        direction, `dodge_families`; until then the directions were equally used, `INFERRED`).
        Avoided: the opener when `FIRST`; the chained hit when the opener was avoided and either
        `SECOND` or his second roll out of the first escapes it (`ESC2`, version 27; until then the
        opponent's single-hit evade share). Punish: `hp_me` from his last escaping roll when his
        strike lands before the attacker's `END`: pressed at the dodge's R1 cancel when he is within
        `reach_me` of the attacker then, else at its move cancel plus the run from there
        (`punish_lead`'s rule); from the second roll, `PUN2`."""
        np = self.np
        tab = self._table(moves, key, strike, reach_me)
        row = tab['ROW'].astype(int)
        first, second = tab['FIRST'], tab['SECOND']
        both = first * (second + ~second * tab['ESC2'])
        av_each = tab['W'] * (first * self.hp[row] + both * self.hp2[row])
        # A dodger left outside his own reach runs the rest before he strikes (the neutral model's
        # run speed, `er-mechanics-neutral.frames_per_metre`), from his dodge's move cancel and
        # from where the dodge has put him then (`punish_lead`), so a whiff can be punished from
        # outside it when the attacker's recovery is long enough.
        inside = tab['NEAR'] <= reach_me
        # The first frame between the R1 and the move cancel the dodge has carried him into reach.
        reached = tab['BD'] <= reach_me
        between = reached.any(axis=1)
        at = tab['BF'][np.arange(len(between)), np.argmax(reached, axis=1)]
        lead = np.where(inside, tab['READY'], np.where(between, at, tab['MOVE']))
        close = np.where(inside | between, 0.0,
                         np.maximum(0.0, tab['FAR'] - reach_me) * _neutral().frames_per_metre())
        pun = (first & second & (tab['S'] + lead + close + strike < tab['END'])) + (first & ~second) * tab['PUN2']
        # Per opener, the direction worth most to him (avoided plus punished); the first of equals.
        M = len(moves)
        mi = tab['MI'].astype(int)
        V = np.zeros((len(self.w), M))
        np.add.at(V, (row, mi), av_each + tab['W'] * pun * hp_me)
        best = np.zeros(len(self.w), int)
        for r_ in range(len(self.w)):
            for k_ in range(1, M):
                if V[r_, k_] > V[r_, best[r_]] + 1e-12:
                    best[r_] = k_
        # The weights carried 1 / M for each direction: the one he takes carries the row's whole.
        sel = (mi == best[row]) * float(M)
        avoided = float((sel * av_each).sum())
        p_punish = float((sel * tab['W'] * pun).sum())
        return {'hp': avoided + p_punish * hp_me, 'evade_hp': avoided, 'punish_hp': p_punish * hp_me,
                'p_evade': float((sel * tab['W'] * first).sum()), 'p_punish': p_punish}

    def buff_answer_value(self, start, ready, duration_s, taken, uninterruptible, strike, reach_me, hp_me):
        """Expected HP of answering one opponent string with a defensive buff cast on reaction, the
        way `dodge_value` answers it with a dodge: he presses at the opener's cue plus his reaction
        (`reaction_delays`) and his timing error (`press_errors`, version 28), the buff is live from
        `start` frames later for `duration_s`; every hit
        whose first live frame falls inside it is taken at `taken`, the others whole (he does not
        dodge). Under an `uninterruptible` buff his own attack comes out at `ready` + `strike` (plus
        running to `reach_me` from where the attacker's advance left him) and punishes when that is
        before the attacker can act again (`END`) and the opener was taken under the buff."""
        np = self.np
        k = _neutral().frames_per_metre()
        near = np.maximum(0.0, ENGAGE_DISTANCE_M - self.advance)
        close = np.maximum(0.0, near - reach_me) * k
        avoided = punish = 0.0
        # His reaction plus each timing error (`pressed`, version 28: every press of his carries it).
        for delay, wd in [(r + e, w * q) for r, w in reaction_delays() for e, q in press_errors()]:
            press = self.cue + delay
            lo, hi = press + start, press + start + duration_s * TAE_FPS
            cut1 = (self.startup >= lo) & (self.startup < hi)
            t2 = self.startup + self.gap2
            cut2 = self.follow & (t2 >= lo) & (t2 < hi)
            avoided += wd * float((self.w * (1.0 - taken) * (self.hp * cut1 + self.hp2 * cut2)).sum())
            if uninterruptible:
                pun = cut1 & (press + ready + close + strike < self.end)
                punish += wd * float((self.w * pun).sum())
        return {'hp': avoided + punish * hp_me, 'avoided_hp': avoided, 'punish_hp': punish * hp_me,
                'p_punish': punish}


class _ExchangeGain:
    """What a dodge of move `m` started at s against opponent row `i` gains the dodger beyond
    escaping the opener (`Opponents._table`, version 26): 1 where `dodge_value` counts his punish from
    it (the chained hit missed too, `SECOND`, and his strike, pressed at the R1 cancel in `reach_me`,
    else between it and the move cancel, else at the move cancel plus his run in, lands before the
    row's `END`), 0 elsewhere. `dist` is the dodger's distance per frame of the dodge
    (`Opponents._escapes`). Called on an array of starts (`_table` hands it the grid of starts as
    `dodge_presses`' values); `sig` names what it reads."""

    __slots__ = ('opp', 'i', 'dist', 'iframes', 'lead', 'sig')

    def __init__(self, opp, i, m, dist, strike, reach_me):
        n = len(m['path'])
        ready = m['ready'] if m['ready'] is not None else n
        walk = ready if m.get('move') is None else max(float(m['move']), ready)
        adv = opp.advance[i] if opp.timing == 'react' else 0.0

        def gap(f):
            x, z = m['path'][min(int(f), n - 1)]
            return math.hypot(x, ENGAGE_DISTANCE_M + z - adv)
        if gap(ready) <= reach_me:
            lead, close = float(ready), 0.0
        else:
            between = [f for f in range(int(ready) + 1, int(walk)) if gap(f) <= reach_me]
            lead, close = (float(between[0]), 0.0) if between else \
                (float(walk), max(0.0, gap(walk) - reach_me) * _neutral().frames_per_metre())
        self.opp, self.i, self.dist, self.iframes = opp, i, dist, m['iframes']
        self.lead = lead + close + float(strike)
        self.sig = ('xgain', m['anim'], float(self.lead), float(opp.startup[i]), float(opp.gap2[i]),
                    float(opp.active2[i]), float(opp.reach2[i]), bool(opp.follow[i]), float(opp.end[i]))

    def __call__(self, s):
        import numpy as np
        s = np.asarray(s, float)
        flat = s.ravel()
        o, i = self.opp, self.i
        second = o._escapes(self.dist, self.iframes, o.startup[i] + o.gap2[i], o.active2[i], o.reach2[i], flat) \
            if o.follow[i] else np.ones(len(flat), bool)
        return (second & (flat + self.lead < o.end[i])).astype(float).reshape(s.shape)


#: What the pool's players throw at a dodger (section 16c): `families`, each moveset family's best
#: opener at its use share (`er-mechanics-moveset`, the ranking's own model of how a weapon is used),
#: or `r1`, R1 #1 alone (section 14a).
OPPONENT_OPENERS = 'families'
#: Share of a string an opponent with an attacking left hand opens with L1 instead (the off-hand
#: L1 #1, or powerstance L1 #1 with a pair), as `er-mechanics-disengage.paired_rows` builds those
#: rows. None (off) by default: the corpus records no button use, so any value is `INFERRED`;
#: `er-mechanics-disengage.py pool --paired-strings` sets it to measure the effect.
PAIRED_STRINGS = None


def _opener_row(slots, key, fallback, chain=None):
    """One `Opponents` row from a ranking slot: the opener `key` and, when `chain` names a
    true-combo follow-up of it, that hit (gap `follow_up_gap`). None when the slot has no hit.

    A jump opener (`jump_entry`, synthesized by `er-builds-pvp.jump_openers`) keeps the landed
    clip's `front_contact`, which counts from the landing clip, not the jump input. Its start is
    the `neutral_in` strike instead (from the input, 22 f on Alabaster Lord's Sword 2H against the
    landed clip's 17.5), else its first hit from the input."""
    s = slots.get(key)
    if not s or s.get('startup') is None:
        return None
    fc = s.get('front_contact') or {}
    at = fc.get(ENGAGE_DISTANCE_M, fc.get(str(ENGAGE_DISTANCE_M))) if isinstance(fc, dict) else None
    if 'jump_entry' in s:
        at = (s.get('neutral_in') or {}).get('strike')
    start = at if at is not None else s['startup']
    reach = s.get('reach') or fallback['reach']
    advance = max(0.0, reach - (s.get('weapon_reach') or reach))
    row = {'cue': s.get('release_lead_in') or 0.0, 'startup': start,
           'active': s.get('active') or fallback['active'], 'reach': reach,
           'hp': s.get('dmg') or fallback['hp'], 'advance': advance,
           'land': (s.get('react') or {}).get('land', 1.0)}
    last, off = s, 0.0
    s2 = slots.get(chain) if chain else None
    if s2 and s2.get('startup') is not None and s.get('follow_up_gap'):
        row.update(gap2=s['follow_up_gap'], reach2=advance + (s2.get('reach') or reach),
                   active2=s2.get('active') or row['active'], hp2=s2.get('dmg') or 0.0)
        last, off = s2, start + s['follow_up_gap'] - s2['startup']
    ends = [v for v in (last.get('next'), last.get('roll')) if v]
    row['end'] = off + min(ends) if ends else start + row['active'] + fallback['recovery']
    return row


def opponents_from_results(pool_raw, results, fallback=OPPONENT_FALLBACK, openers=None, timing='react',
                           react=True, slots_fn=None):
    """`Opponents` for the exchange pool (`er-mechanics-exchange.opponent_pool`): each pool build's
    weapon and grip looked up in the ranking's own rows (`er-builds-pvp` results; `dmg` is the
    sweep build's corpus-mean damage, standing in for that player's own).

    `openers` `r1`: R1 #1 as the exchange throws it and its R1 #2 when R1 #1 lists it in `combos`
    (section 14a). `families` (the default, `OPPONENT_OPENERS`): every moveset family's best
    engagement at that family's use share, the opener with its first chained follow-up, the shares
    renormalised over the openers that resolve. `slots_fn(row)` gives a row's slots with the
    synthesized jump openers (`er-builds-pvp.jump_openers`); without it the stored slots alone,
    which have none, so the jump family drops out. A key the ranking has no
    row for takes `fallback` (no follow-up). Returns (Opponents, matched share, pool-weighted
    means)."""
    openers = openers or OPPONENT_OPENERS
    by = {}
    # The off-hand L1 a dodge skill leaves the stagger before and a roll does not
    # (`er-mechanics-disengage.offhand_escapes`), per opponent key and opener.
    paired = _disengage().Paired(pool_raw, results, plain) if DISENGAGE or PAIRED_STRINGS else None
    escapes = _disengage().offhand_escapes(pool_raw, results, plain, paired=paired) if DISENGAGE else {}
    # The L1-opened strings of the pool's left hands, when the pool throws them (`PAIRED_STRINGS`).
    extra = _disengage().paired_rows(pool_raw, results, plain, paired=paired) if PAIRED_STRINGS else {}
    for r in results:
        slots = r.get('slots') or {}
        key = f"{plain(r['weapon'])}|{'2h' if r['two'] else '1h'}"
        rows = []
        if openers == 'families':
            fam_slots = slots_fn(r) if slots_fn else slots
            for fam in ((r.get('moveset') or {}).get('families') or {}).values():
                links = fam.get('links') or []
                row = _opener_row(fam_slots, fam.get('opener'), fallback, links[0] if links else None)
                if row and fam.get('share'):
                    rows.append((fam['share'], {**row, 'opener': fam.get('opener')}))
            total = sum(share for share, _ in rows)
            rows = [(share / total, row) for share, row in rows] if total else []
        if not rows:
            s = slots.get('r1_1') or {}
            chain = 'r1_2' if any((c.get('next') or '').removeprefix('2h_') == 'r1_2'
                                  for c in s.get('combos') or []) else None
            row = _opener_row(slots, 'r1_1', fallback, chain)
            rows = [(1.0, {**row, 'opener': 'r1_1'})] if row else []
        if rows and DISENGAGE:
            # What this player chases an escaper with (`er-mechanics-disengage.chaser_options`).
            chase = _disengage().chaser_options(slots)
            esc = escapes.get(key) or {}
            rows = [(share, {**row, 'key': key, 'chase': chase, 'skill_escape_hp': esc.get(row['opener'], 0.0)})
                    for share, row in rows]
        if rows and extra.get(key):
            # PAIRED_STRINGS of each attacking left hand's strings open with its L1 instead.
            p = PAIRED_STRINGS
            armed = sum(s for s, _ in extra[key])
            l1 = [(p * s, {**row, 'key': key, **({'chase': rows[0][1]['chase']} if 'chase' in rows[0][1] else {}),
                           **({} if DISENGAGE else {'skill_escape_hp': 0.0})}) for s, row in extra[key]]
            rows = [(share * (1.0 - p * armed), row) for share, row in rows] + l1
        if rows:
            by[key] = rows
    counts = collections.Counter(k for k, _ in pool_raw['builds'])
    rows, hit = [], 0
    for k, n in counts.items():
        mine = by.get(plain(k))
        hit += n if mine else 0
        for share, row in mine or [(1.0, fallback)]:
            rows.append({'w': n * share, **row, **({} if react else {'land': 1.0})})
    total = sum(r['w'] for r in rows)
    means = {f: sum(r['w'] * (r.get(f) or 0.0) for r in rows) / total for f in Opponents.FIELDS}
    means['follow'] = sum(r['w'] for r in rows if r.get('gap2')) / total if total else 0.0
    n_builds = sum(counts.values())
    return Opponents(rows, timing), hit / n_builds if n_builds else 0.0, means


def utility_value(t, sword_arts_id, weapon_id, eng, opp):
    """HP per engagement a utility skill adds over what the player has without it, and the frames
    it adds to the engagement's commitment. `eng` is the weapon's best opener as the ranking
    scores it: `numerator` (HP per engagement), `commit`, `dmg`, `strike`, `reach`, `exchange`
    (`er-mechanics-exchange.exchange` shares, may be None) and `crit` / `crit_ev` for a parry.

      dodge    in place of a medium roll, against the attacks the opponent throws first
               (exchange `p_second`): opponents.dodge_value(step) - dodge_value(roll)
      defense  one engagement per cast when the buff outlasts the cast by the opener's strike
               frame: losses become trades (the opener lands, `dmg`) under a reaction override,
               and every hit taken (1 - win) is cut by 1 - damage_taken; the frames before the
               buff lands are added to the commitment
      parry    carrying a parry tool on the weapon itself: (1 - corpus parry-tool share) x
               PARRY_LAND x corpus exposure x the weapon's riposte (`er-mechanics-crits`)
    Returns None when the skill is not a utility skill or the inputs are missing."""
    prof = utility_profile(t, sword_arts_id, weapon_id)
    if prof is None:
        return None
    ex = eng.get('exchange') or {}
    if prof['kind'] == 'dodge':
        if opp is None:
            return None
        # A punish lands on an attacker still recovering, who cannot dodge it: the raw hit.
        hp_me = eng.get('dmg_raw', eng['dmg'])
        step = opp.dodge_value(prof['moves'], ('skill', sword_arts_id), eng['strike'], eng['reach'], hp_me)
        roll = opp.dodge_value(roll_motion(), 'roll', eng['strike'], eng['reach'], hp_me)
        share = ex.get('p_second', 0.5)
        out = {'kind': 'dodge', 'hp': share * (step['hp'] - roll['hp']), 'commit': 0.0,
               'p_second': share, 'step': step, 'roll': roll, 'evade_gain_hp': share * (step['hp'] - roll['hp']),
               'iframes': [m['iframes'] for m in prof['moves']], 'ready': [m['ready'] for m in prof['moves']],
               'distance': [m['distance'] for m in prof['moves']]}
        # The neutral game (`er-mechanics-neutral`): the same committed exchange, closed with the
        # dodge in the kit as well as the roll. It changes the committed share of the opener's
        # worth, (1 - REACT_SHARE) x f, as `slot_score` weighs it.
        ni, npool = eng.get('neutral_in'), getattr(opp, 'npool', None)
        if ni and ni.get('reach') and npool is not None:
            n = _neutral()
            fwd = min(prof['moves'], key=lambda m: m['path'][-1][1])
            tool = n.tool_from_motion(fwd)
            args = (npool, ni['strike'], ni['reach'], ni['poise'], ni['hyper'], ni.get('active') or 3.0)
            with_roll = n.neutral_exchange(*args, tools=n.DEFAULT_TOOLS, dmg=eng.get('dmg'))
            with_step = n.neutral_exchange(*args, tools=tuple(n.DEFAULT_TOOLS) + (tool,), dmg=eng.get('dmg'))
            gain = (1.0 - REACT_SHARE) * hp_me * (with_step['f_neutral'] - with_roll['f_neutral'])
            out.update(hp=out['hp'] + gain, neutral_gain_hp=gain,
                       neutral={'roll': with_roll['f_neutral'], 'step': with_step['f_neutral'],
                                'dodge_in': with_step['dodge_in']})
        # Disengaging (disengage.md): the same answered strings, the dodge taken away from the
        # attacker and followed by a flask when the room it leaves is worth drinking in, against
        # the medium roll doing the same. In the same frame as the evade term: `p_second`.
        if DISENGAGE:
            d = _disengage()
            dis = d.skill_vs_roll(opp, prof['moves'], v=d.speed(DISENGAGE))
            gain = share * dis['hp']
            # The skill button skips the roll's stagger-count gate (disengage.md section 2): an
            # opener that lands and staggers is left before a cross-hand L1 the roll cannot
            # leave. Weighted by the opener's landing chance on a reacting defender.
            esc = share * float((opp.w * opp.land * opp.skill_escape_hp).sum())
            gain += esc
            out.update(hp=out['hp'] + gain, disengage_gain_hp=gain, stagger_escape_hp=esc,
                       disengage={k: dis[k] for k in ('hp', 'attempts', 'tool', 'base', 'speed')}
                       | {'skill_heal_hp': dis['skill']['heal_hp'], 'roll_heal_hp': dis['roll']['heal_hp'],
                          'skill_p_safe': dis['skill']['p_safe'], 'roll_p_safe': dis['roll']['p_safe'],
                          'skill_p_trade': dis['skill']['p_trade'], 'roll_p_trade': dis['roll']['p_trade']})
        return out
    if prof['kind'] == 'defense':
        remaining = prof['duration_s'] * TAE_FPS + prof['start'] - (prof['ready'] or 0.0)
        covered = 1.0 if remaining >= eng['strike'] else 0.0
        contest = eng.get('neutral') or ex
        win, loss = contest.get('win', 0.0), contest.get('loss', 0.0)
        hp_raw = eng.get('dmg_raw', eng['dmg'])
        mean_hp = opp.mean_hp if opp else OPPONENT_FALLBACK['hp']
        # Section 14b's term, kept for comparison: every engagement a committed exchange at 2.5 m,
        # the opener's landed damage and the opponents' landed hits.
        legacy_through = ex.get('loss', 0.0) * eng['dmg'] if prof['uninterruptible'] else 0.0
        legacy_cut = (1.0 - ex.get('win', 0.0)) * (1.0 - prof['damage_taken']) * \
            (opp.mean_hp_landed if opp else mean_hp)
        # Cast ahead (the neutral model, neutral.md section 4). Only a committed defender trades
        # into the buff: the waiting share dodges whether it is on or not. And a buff the opponent
        # sees start (`visible`) that ends before the next engagement would come (the fight's
        # engagement spacing) is waited out, so it covers nothing (`INFERRED`).
        spacing = ENGAGEMENT_SECONDS
        waited = bool(prof.get('visible')) and prof['duration_s'] < spacing
        committed = 1.0 - REACT_SHARE
        through = committed * loss * hp_raw if prof['uninterruptible'] else 0.0
        cut = committed * (1.0 - win) * (1.0 - prof['damage_taken']) * mean_hp
        ahead = 0.0 if waited else covered
        out = {'kind': 'defense', 'hp': ahead * (through + cut), 'commit': ahead * prof['start'],
               'use': 'ahead', 'covered': covered, 'waited_out': waited, 'spacing_s': spacing,
               'through_hp': through, 'cut_hp': cut, 'legacy_hp': covered * (legacy_through + legacy_cut),
               'legacy_through_hp': legacy_through, 'legacy_cut_hp': legacy_cut,
               **{k: prof[k] for k in ('root', 'duration_s', 'damage_taken', 'uninterruptible', 'start',
                                       'ready', 'visible')}}
        if opp is not None:
            # Cast on reaction, as an answer to the opponent's string in place of a roll (the dodge
            # utility's frame: the share of exchanges he strikes first, `p_second`).
            ans = opp.buff_answer_value(prof['start'], prof['ready'] or 0.0, prof['duration_s'],
                                        prof['damage_taken'], prof['uninterruptible'], eng['strike'],
                                        eng['reach'], hp_raw)
            roll = opp.dodge_value(roll_motion(), 'roll', eng['strike'], eng['reach'], hp_raw)
            share = ex.get('p_second', 0.5)
            react_hp = share * (ans['hp'] - roll['hp'])
            out.update(answer=ans, roll=roll, answer_hp=react_hp)
            if react_hp > 0 and utility_multiplier({'hp': react_hp, 'commit': 0.0}, eng) > \
                    utility_multiplier(out, eng):
                out.update(hp=react_hp, commit=0.0, use='answer')
        return out
    crit, ev = eng.get('crit'), eng.get('crit_ev')
    if not crit or not ev:
        return None
    crits = _load('er_mechanics_crits', 'er-mechanics-crits.py')
    q = (1.0 - ev['parry_tool']) * crits.PARRY_LAND * ev['exposure']
    return {'kind': 'parry', 'hp': q * crit['riposte_eff_hp'], 'commit': 0.0, 'q_riposte': q,
            'riposte_hp': crit['riposte_eff_hp']}


def utility_multiplier(u, eng):
    """The engagement's score multiplier from `utility_value`: (N + hp) / N x C / (C + commit)."""
    n, c = eng['numerator'], eng['commit']
    if not n or not c:
        return 1.0
    return (n + u['hp']) / n * c / (c + u['commit'])


# ---------------------------------------------------------------------------------------------
# reaction: a defender who sees the attack coming and rolls (section 16)

#: Human visual reaction time, from the attack becoming visible to the dodge press: lognormal with
#: this median and log spread (`INFERRED`: outside measurements of simple visual reaction time put
#: the median near 0.25 s; nothing in the game data says it).
REACTION_MEDIAN_S = 0.25
REACTION_LOG_SD = 0.2
#: One-way delay between the two machines (`INFERRED`: a 100 ms round trip). It is paid twice: the
#: defender sees the attack this late, and his roll reaches the attacker's machine this late. That
#: the attacker's machine decides the hit is `INFERRED` (community knowledge, not traced).
NETWORK_ONE_WAY_S = 0.05
#: Timing error of a dodge the defender schedules ahead once he has recognised the attack, normal
#: with this sd (`INFERRED`).
DODGE_TIMING_SD_S = 0.05
#: Share of openers thrown at a defender who waits and reacts rather than commits (`INFERRED`: no
#: data says it; the committed half is the exchange factor's case, `er-mechanics-exchange`).
REACT_SHARE = 0.5
#: Quantile points the reaction time and the timing error are integrated over.
REACTION_POINTS, TIMING_POINTS = 9, 7
#: Dodge starts are tried every 60 Hz update: half a 30 fps real frame.
DODGE_STEP = 0.5
#: A dodger who evaded punishes with an R1: the exchange pool's R1 #1 damage and reach, pool
#: weighted over the RL 150 ranking (`MEASURED` 2026-09-29, `OPPONENT_FALLBACK`).
PUNISH_HP = OPPONENT_FALLBACK['hp']
PUNISH_REACH_M = OPPONENT_FALLBACK['reach']
#: TAE event 66 SpEffects the behavior script reads to send a button to a skill's follow-up
#: (`W_SwordArtsOneShotComboEnd` / `_2`, `COMMUNITY` c0000.hks `SwordArtsOneShot_onUpdate`): R2 with
#: 100050 / 100051, R1 with 100054 / 100055. The follow-up animation is taken to be the opening's
#: id + 10 / + 20 (`INFERRED`: the only other FP-charging animations of that ten-block pair).
FOLLOW_UP_SPEFFECTS = {100050: ('r2', 10), 100051: ('r2', 20), 100054: ('r1', 10), 100055: ('r1', 20)}
_ND = statistics.NormalDist()


_REACTION_DELAYS = []
_TIMING_ERRORS = {}


def reaction_delays():
    """[(real frames from the moment the attack is visible to the moment the dodge is live on the
    attacker's machine, weight)]: `REACTION_POINTS` quantiles of the reaction time plus two
    network legs. Worked out once (the constants do not change) and handed out as a fresh list."""
    if not _REACTION_DELAYS:
        for k in range(REACTION_POINTS):
            z = _ND.inv_cdf((k + 0.5) / REACTION_POINTS)
            s = REACTION_MEDIAN_S * math.exp(REACTION_LOG_SD * z) + 2 * NETWORK_ONE_WAY_S
            _REACTION_DELAYS.append((s * TAE_FPS, 1.0 / REACTION_POINTS))
    return list(_REACTION_DELAYS)


def _timing_errors(sd):
    """The `TIMING_POINTS` quantiles of a normal timing error of sd `sd` (real frames), worked out
    once per sd."""
    if sd not in _TIMING_ERRORS:
        _TIMING_ERRORS[sd] = [sd * _ND.inv_cdf((k + 0.5) / TIMING_POINTS) for k in range(TIMING_POINTS)]
    return _TIMING_ERRORS[sd]


#: His shortest reaction (`reaction_delays`).
_SOONEST_REACTION = min(d for d, _ in reaction_delays())
#: His earliest press after a decision: his shortest reaction plus his earliest timing error
#: (version 28: the error is his hand's on every press, one he presses at once included, so a press
#: can come up to that error before the reaction he decided on). A floor at or before a decision
#: time plus this moves no press decided then (`lookalike_plan`, `burst_after`).
_SOONEST_PRESS = _SOONEST_REACTION + min(_timing_errors(DODGE_TIMING_SD_S * TAE_FPS))


def press_errors():
    """[(real frames a press lands off the moment he means it, weight)]: the `TIMING_POINTS`
    quantiles of `DODGE_TIMING_SD_S`, equally weighted (version 28: every press of his carries them,
    `_AimTable.presses`, the strike's `er-mechanics-spells.strike_segments`, the strafe's
    `FollowupModel.strafe_escape`)."""
    return [(e, 1.0 / TIMING_POINTS) for e in _timing_errors(DODGE_TIMING_SD_S * TAE_FPS)]


def pressed(intent, floor, w):
    """[(press, weight)] of one press he means at real frame `intent`, of weight `w`: `intent` plus
    each timing error (`press_errors`), a press before `floor` (None: none) coming out at the floor,
    equal presses merged (version 28's rule, `_AimTable.presses`): the error is his hand's whenever
    he presses, at his reaction too, and only his recovery's floor, which no input beats, truncates
    it (`INFERRED`: a press made while the recovery still holds him comes out at its end, as before)."""
    out = []
    for e, q in press_errors():
        s = intent + e
        if floor is not None and s < floor:
            s = floor
        if out and out[-1][0] == s:
            out[-1][1] += 1
        else:
            out.append([s, 1])
    return [(s, w if k == TIMING_POINTS else _press_weight(w, k)) for s, k in out]


#: `_press_weight`'s floats, {(w, k): w * k / `TIMING_POINTS`}.
_PRESS_W = {}


def _press_weight(w, k):
    """`w * k / TIMING_POINTS` as one float object per (w, k): the `lookalike_plan` memos hold
    millions of presses, each a tuple with its own weight until 2026-11-18, 24 bytes apiece for a
    handful of values (the weights are `reaction_delays`'). With it and `_AimTable.kept_presses`'
    dropped aim memo, Swift Slash's set peaked at 1.94 GiB of its own instead of 2.12
    (`MEASURED` 2026-11-18, target/wf/memory/setmem.py). The same number either way; a zero
    weight is not kept, so -0.0 and 0.0 (equal keys) each keep their own sign."""
    if w == 0.0 or w != w:
        return w * k / TIMING_POINTS
    got = _PRESS_W.get((w, k))
    if got is None:
        if len(_PRESS_W) >= 4096:
            _PRESS_W.clear()
        got = _PRESS_W[(w, k)] = w * k / TIMING_POINTS
    return got


#: A run of true bytes (`_runs`).
_TRUE_RUN = re.compile(b'\x01+')


def _runs(ok):
    """[(first, last)] index runs where `ok` holds: the runs of 1 in its bytes as bools."""
    import numpy as np
    return [(m.start(), m.end() - 1) for m in _TRUE_RUN.finditer(np.asarray(ok, bool).tobytes())]


#: `_strikes_key`'s digests: {id(array): (array, digest)}, the array kept so its id names it.
_STRIKES_KEY = {}


def _strikes_key(arrive):
    """A key equal only for equal sorted strike arrays (`PressGain.sig`): a digest of their bytes,
    read once per array (an array is taken as unchanged while it lives, as `_ordered` takes it)."""
    import hashlib
    import numpy as np
    got = _STRIKES_KEY.get(id(arrive))
    if got is None or got[0] is not arrive:
        if len(_STRIKES_KEY) >= 64:
            _STRIKES_KEY.clear()
        got = _STRIKES_KEY[id(arrive)] = (arrive, hashlib.blake2b(
            np.ascontiguousarray(np.asarray(arrive, float)).tobytes(), digest_size=12).hexdigest())
    return got[1]


class PressGain:
    """What a dodge press of his at real frame s that escapes is worth to him beyond escaping, in
    hits: the share of the pool's strikes that punish from it, `reaction_outcome`'s count
    (`searchsorted(strikes + close, (my_roll - s) - ready)` over their number, `punish_lead`'s
    `ready` and `close`). `ordered` is the pool's strikes sorted (`_ordered`), `run` whether he runs
    in, `my_roll` the attacker's roll frame: kept so a lookalike group can make each member's own
    (`PressGain.of`). Called on an array of starts it gives their shares; `sig` is equal only for
    equal gains (the plans' memos key on it)."""

    __slots__ = ('arrive', 'my_roll', 'ready', 'n', 'sig', 'ordered', 'run')

    def __init__(self, ordered, close, my_roll, ready, run):
        self.ordered, self.run = ordered, run
        self.arrive = ordered + close
        self.my_roll, self.ready, self.n = float(my_roll), float(ready), len(ordered)
        self.sig = ('gain', _strikes_key(ordered), float(close), self.my_roll, self.ready)

    def __call__(self, s):
        import numpy as np
        # `np.searchsorted(self.arrive, x, side='left')` is this method call (`_wrapfunc`).
        return self.arrive.searchsorted(self.my_roll - np.asarray(s, float) - self.ready) / self.n

    @staticmethod
    def of(m, dist, advance, run, ordered, my_roll):
        """The gain of move `m` at `dist` against an attacker carried `advance` forward whose roll
        frame is `my_roll`, the pool's strikes `ordered` (sorted): None when he does not punish from
        it (`punish_lead`'s `near`, no strikes, no roll frame), as `reaction_outcome` counts none."""
        if ordered is None or my_roll is None or not len(ordered):
            return None
        # Kept per what it reads (`_PRESS_GAINS`): a plan makes every member's again on each call.
        try:
            key = (id(m), id(ordered), dist, advance, run, my_roll)
            got = _PRESS_GAINS.get(key)
        except TypeError:
            key = got = None
        if got is not None and got[0] is m and got[1] is ordered:
            return got[2]
        ready, close, near = punish_lead(m, dist, advance, run)
        g = PressGain(ordered, close, my_roll, ready, run) if near else None
        if key is not None:
            if len(_PRESS_GAINS) >= PRESS_GAINS_CACHE:
                _PRESS_GAINS.clear()
            _PRESS_GAINS[key] = (m, ordered, g)
        return g


#: `PressGain.of`'s gains: {(id(move), id(strikes), dist, advance, run, my_roll): (move, strikes, its
#: gain)}, the move and the strikes kept so their ids name them (a move and a strikes array are taken
#: as unchanged while they live, as `_move_key` and `_ordered` take them); emptied past
#: `PRESS_GAINS_CACHE` entries (each may hold a strikes array alive, ~14 kB for the pool's).
_PRESS_GAINS = {}
PRESS_GAINS_CACHE = 4096


class _TrackGain:
    """`PressGain` of a press made out of an earlier roll (`carried`, version 26): the dodge starts
    from where the track has him at its start (on the update grid, as the press is read), so its
    `punish_lead` is read from there, per start. Called on an array of starts it gives their
    shares; `sig` as `PressGain`'s."""

    __slots__ = ('m', 'track', 'dist', 'advance', 'run', 'ordered', 'my_roll', 'n', 'sig', 'kept')

    def __init__(self, m, track, dist, advance, run, ordered, my_roll):
        self.m, self.track, self.dist, self.advance, self.run = m, track, dist, advance, run
        self.ordered, self.my_roll, self.n = ordered, float(my_roll), len(ordered)
        self.sig = ('tgain', _strikes_key(ordered), _move_key(m), track_key(track), float(dist),
                    repr(advance), bool(run), self.my_roll)
        self.kept = {}

    def lead(self, s):
        """`punish_lead` of a press at real frame `s` (read at its grid start)."""
        g = _grid(float(s))
        if g not in self.kept:
            x, z, _ = track_at(self.track, [g])
            self.kept[g] = punish_lead(self.m, self.dist, self.advance, self.run, (float(x[0]), float(z[0])))
        return self.kept[g]

    def __call__(self, s):
        import numpy as np
        s = np.asarray(s, float)
        flat = s.ravel()
        ug, inv = np.unique(np.round(flat / DODGE_STEP) * DODGE_STEP, return_inverse=True)
        leads = [self.lead(v) for v in ug.tolist()]
        ready = np.array([r for r, _, _ in leads], float)[inv]
        close = np.array([c for _, c, _ in leads], float)[inv]
        near = np.array([nr for _, _, nr in leads], bool)[inv]
        res = np.searchsorted(self.ordered, self.my_roll - flat - ready - close, side='left') / self.n
        return np.where(near, res, 0.0).reshape(s.shape)


def press_gain(m, track, dist, advance, run, ordered, my_roll):
    """What an escaping press of move `m` out of `track` gains him beyond escaping: `PressGain.of`
    for an empty track (he dodges from where he stood), else a `_TrackGain`; None when no punish is
    counted (no strikes, no roll frame)."""
    if not track:
        return PressGain.of(m, dist, advance, run, ordered, my_roll)
    if ordered is None or my_roll is None or not len(ordered):
        return None
    return _TrackGain(m, track, dist, advance, run, ordered, my_roll)


#: A press aimed this far past a run's left edge falls on the run's first start (`_AimTable`): far
#: above float rounding, far below a frame.
EDGE_EPS = 1e-6
#: {digest of an `_AimTable`'s content: {floor: (first candidate, worths)}}: the worths `_aims` reads,
#: shared by every table of that content (`_AimTable._shared`) and kept across the emptying of the
#: `lookalike_plan` memos, so a table made again reads them again. Emptied past `AIM_WORTHS_BYTES`
#: of worths and by `forget_reads`; a table holding one keeps it.
_AIM_WORTHS = {}
_AIM_WORTHS_HELD = [0]
AIM_WORTHS_BYTES = 256 << 20
#: What one kept worth at a reaction (`_AimTable._aims`' `ws[('at', floor)]`, a dict entry of a float
#: key and a float) is counted as against `AIM_WORTHS_BYTES`.
AIM_AT_BYTES = 100


def forget_aim_worths():
    """Empty `_AIM_WORTHS`: a table made later reads its worths afresh, the same numbers."""
    _AIM_WORTHS.clear()
    _AIM_WORTHS_HELD[0] = 0


class _AimTable:
    """Where a reacting defender aims his dodge against the attacks whose escaping starts are `oks`
    (one bool array per attack on the `step` grid, a start past an array caught), attack j weighing
    `weights[j]`, `gains[j]` (a `PressGain`, or None: no punish counted) what an escaping press is
    worth to him beyond escaping (version 25; the comment above `dodge_presses`).

    He means to press at a, no sooner than his reaction t, and his presses are max(F, a + e) over
    the timing errors e (`_timing_errors`, `TIMING_POINTS` of them, equally weighted), F his floor
    (his recovery; none: -inf). Version 28: the error applies to a press at his reaction too (a = t),
    where until then max(t, a + e) clamped every early error to the reaction and a press at once
    carried none; so a defender reacting to a later event (a lookalike's release, or the frame a
    release would have shown) pressed exactly on time where a defender who knew the attack could
    only aim with the error, and knowing it could leave him worse off (the Glintstone Dart, version
    27: escape 0.929, punish 0.125 knowing it is the hold, 0.968 and 0.139 not). With the error on
    every press the plans of a later decision are among the knowing defender's: for each reaction
    he may aim at any a >= t, and a later decision only raises t.
    He takes the aim that maximises
    what they are worth to him: per attack its weight times, per press, 1 + its gain where the press
    escapes (a start read as `reaction_outcome` reads it, `round(s / step)`), 0 where it is caught.
    `vals[j]` (a float array on the grid, or None) replaces the 1 by what escaping from that start
    leaves him (`FollowupModel`: the follow-up he then meets, `dodge_presses` `val`), the gain then
    counted where `oks[j]` holds. Exact over every aim: as a grows each press moves later (or stays
    at F), so between the frames where a press enters a start worth more than the one before (a =
    that start's left edge - e) what he gets only falls (a press keeps its start or moves to one
    worth no more, and a gain falls as the press comes later); so the best is at once (a = t) or
    just past one of those entries, and those are the aims tried. The first of equally good ones in
    time is taken. `aim(t, F)` is None for at once."""

    def __init__(self, oks, weights, step, gains=None, vals=None):
        import numpy as np
        self.step = step
        self.errors = np.array(_timing_errors(DODGE_TIMING_SD_S * TAE_FPS), float)
        live = [j for j in range(len(oks)) if weights[j] != 0]
        width = max([len(oks[j]) for j in live] + [0])
        self.mat = np.zeros((len(live), width + 1), bool)
        self.vals = []
        edges = set()
        for r, j in enumerate(live):
            o = np.asarray(oks[j], bool)
            self.mat[r, :len(o)] = o
            v = None if vals is None or vals[j] is None else np.asarray(vals[j], float)
            if v is None:
                edges.update((lo - 0.5) * step for lo, _ in _runs(o))
                self.vals.append(None)
            else:
                # Every start worth more to him than the one before it (gained or not), the first
                # included: where a press entering it can raise what he gets.
                full = np.zeros(width + 1)
                full[:len(v)] = v
                self.vals.append(full)
                up = np.concatenate([[full[0] > 0 or self.mat[r, 0]],
                                     (full[1:] > full[:-1]) | (self.mat[r, 1:] & ~self.mat[r, :-1])])
                edges.update((i - 0.5) * step for i in np.nonzero(up)[0].tolist())
        self.width = width
        self.w = [float(weights[j]) for j in live]
        self.gains = [None if gains is None else gains[j] for j in live]
        self.edges = np.array(sorted(edges), float)
        self.size = 0
        self.plain = None
        self.kept = {}
        self.fast = None
        self.cands = None
        self.worths = None

    def _shared(self):
        """This table's worths per floor (`_aims`), in a dict every table of equal content shares
        (`_AIM_WORTHS`): the `lookalike_plan` memos that hold the tables are emptied past
        `LOOKALIKE_MEMO_ENTRIES`, and the plans asked again then make the same tables again (33,415
        of the 90,244 tables of the first 25 min of Flame Fall Upon Them and Giantsflame Take Thee's
        set, `MEASURED` 2026-11-21, .worktrees/ffut/target/ffut/old-pair.jsonl). Content
        is everything `_worth` reads: the members' escaping starts and values, their weights, each
        gain by its `sig` (a `PressGain`'s or a `_TrackGain`'s, equal only for equal gains), the step
        and the timing errors. A table with any other gain keeps its own."""
        import hashlib
        if self.worths is None:
            if all(g is None or type(g) in (PressGain, _TrackGain) for g in self.gains):
                h = hashlib.blake2b(digest_size=16)
                h.update(repr((self.step, self.width, self.mat.shape, self.w, self.errors.tolist(),
                               [None if g is None else g.sig for g in self.gains],
                               [None if v is None else len(v) for v in self.vals])).encode())
                h.update(self.mat.tobytes())
                for v in self.vals:
                    if v is not None:
                        h.update(v.tobytes())
                key = h.digest()
                if _AIM_WORTHS_HELD[0] > AIM_WORTHS_BYTES:
                    forget_aim_worths()
                got = _AIM_WORTHS.get(key)
                if got is None:
                    got = _AIM_WORTHS[key] = {}
                self.worths = got
            else:
                self.worths = {}
        return self.worths

    def _cands(self):
        """(every aim past a reaction `aim` may try, sorted, each once: an edge less a timing error
        plus `EDGE_EPS`; per aim the latest of its edges plus `EDGE_EPS`, which `aim` tests against
        t plus his earliest error): made once per table."""
        import numpy as np
        if self.cands is None:
            e = self.errors
            v = (self.edges[:, None] - e[None, :] + EDGE_EPS).ravel()
            th = np.repeat(self.edges + EDGE_EPS, len(e))
            order = np.lexsort((th, v))
            v, th = v[order], th[order]
            # The last of equal aims holds their latest edge.
            last = np.concatenate((v[1:] != v[:-1], [True])) if len(v) else np.zeros(0, bool)
            self.cands = (v[last], th[last])
        return self.cands

    def aim(self, t, floor=None):
        """Where he aims from reaction `t` at floor `floor` (the class comment). Its candidates past
        t are an edge less an error plus `EDGE_EPS` past t whose edge plus `EDGE_EPS` is past t plus
        his earliest error (`_cands`, sorted and each once, as `np.unique` reads them). What an aim
        is worth reads only that aim and the floor (`_worth`: elementwise per aim, the same numbers
        in any batch), so each candidate's worth is kept per floor (`worths`) from the earliest t
        asked on; a later t reads the kept ones, and only t itself is read again. Until 2026-11-20
        every call read every candidate past t: `lookalike_plan` asks one table again at each
        decision time and reaction delay, so Flame Fall Upon Them's set alone made 2,783,425 calls,
        2,490,805 of them on a table and floor an earlier t had read, and read 182.7 million aims
        of which 149.9 million had been read before (`MEASURED` 2026-11-20,
        .worktrees/ffut/target/ffut/stats1.jsonl); with Giantsflame Take Thee, its set in a full
        run, `aim` was 57% of the set's time (py-spy, prof2.txt there)."""
        return self._aims([t], floor)[0]

    def _aims(self, ts, floor=None):
        """`aim(t, floor)` of each of `ts`, the worths at the reactions themselves read in one
        `_worth` batch (elementwise per aim, so the numbers `aim` reads one at a time)."""
        import numpy as np
        e = self.errors
        v, th = self._cands()
        js = [int(v.searchsorted(t, 'right')) for t in ts]
        sels = [th[j:] > t + e[0] for t, j in zip(ts, js)]
        live = [i for i, s in enumerate(sels) if s.any()]
        out = [None] * len(ts)
        if not live:
            return out
        fk = None if floor is None else np.float64(floor).tobytes()
        ws = self._shared()
        got = ws.get(fk)
        j0 = min(js[i] for i in live)
        if got is None or got[0] > j0:
            hi = len(v) if got is None else got[0]
            # A copy: `_worth`'s answer may be a row of a larger array (its running sum over the
            # members), which a kept view would hold whole (2026-11-21, kept as views: 19 GB resident
            # 25 min into Flame Fall Upon Them and Giantsflame Take Thee's set, against 4.4 GB before
            # this cache; `MEASURED`, .worktrees/ffut/target/ffut/new2-pair.jsonl).
            W = self._worth(v[j0:hi], None).copy() if floor is None else self._floor_worths(v, j0, hi, floor, ws)
            got = ws[fk] = (j0, W if got is None else np.concatenate([W, got[1]]))
            _AIM_WORTHS_HELD[0] += W.nbytes
        # The worths at the reactions themselves, kept per floor and reaction with the candidates'
        # (`ws`, shared by every table of this content and kept past the `lookalike_plan` memos'
        # emptying): `_worth` is elementwise per aim, so each is the number a batch of them gives.
        # Why (2026-11-21, .worktrees/haima): with the candidates' worths shared, these reads were
        # 80% of what `_worth` still took in Cannon of Haima's set (py-spy,
        # target/haima/prof-haima-a.txt).
        u0 = ws.get(('at', fk))
        if u0 is None:
            u0 = ws[('at', fk)] = {}
        need = [t for t in dict.fromkeys(ts[i] for i in live) if t not in u0]
        if need:
            u0.update(zip(need, self._worth(np.array(need, float), floor).tolist()))
            _AIM_WORTHS_HELD[0] += AIM_AT_BYTES * len(need)
        for r, i in enumerate(live):
            j, sel = js[i], sels[i]
            A = np.concatenate([[ts[i]], v[j:][sel]])
            U = np.concatenate([[u0[ts[i]]], got[1][j - got[0]:][sel]])
            # The first aim within 1e-12 (relative) of the best: at once on a tie, else the earliest.
            top = float(U.max())
            best = int(np.argmax(U >= top - 1e-12 * max(1.0, abs(top))))
            out[i] = None if best == 0 else float(A[best])
        return out

    def _none_worths(self, v, j, ws):
        """The worths at no floor of candidates `v[j:]` (`_aims`' kept `ws[None]`, read down to `j`
        first when it does not reach there)."""
        import numpy as np
        got = ws.get(None)
        if got is None or got[0] > j:
            hi = len(v) if got is None else got[0]
            W = self._worth(v[j:hi], None).copy()
            got = ws[None] = (j, W if got is None else np.concatenate([W, got[1]]))
            _AIM_WORTHS_HELD[0] += W.nbytes
        return got[1][j - got[0]:]

    def _floor_worths(self, v, j0, hi, floor, ws):
        """`_worth(v[j0:hi], floor)`, each aim whose presses all come after `floor` read off the
        worths at no floor (`_none_worths`): `_worth` moves a press to the floor only where an aim
        plus a timing error is under it (`np.maximum`), so for an aim with every one of its presses
        past it the presses, and all `_worth` reads of them, are those with no floor; and `_worth`
        is elementwise per aim (the same numbers in any batch, `aim`). Only the aims a press of which
        the floor moves are read at the floor. Why (2026-11-21, .worktrees/haima): a lookalike
        group's tables are read at each floor of the situations (Cannon of Haima's tap: 49,942
        reads of 7,515 tables in the first 107 s of its set), and of the 1,509,681 aims read at a
        floor 634 had a press the floor moved (`MEASURED`, target/haima/aimstats-haima.log)."""
        import numpy as np
        A = v[j0:hi]
        moved = ~(A[:, None] + self.errors[None, :] > floor).all(axis=1)
        if moved.all():
            return self._worth(A, floor).copy()
        free = np.nonzero(~moved)[0]
        W = np.empty(len(A))
        W[free] = self._none_worths(v, j0 + int(free[0]), ws)[free - free[0]]
        if moved.any():
            W[moved] = self._worth(A[moved], floor)
        return W

    def prime(self, reqs):
        """Read the presses of each reaction of `reqs` [(t, w, floor)] that `kept` does not hold
        yet, as `kept_presses` reads them, their aims in one `_aims` batch per floor: the callers'
        reaction delays (`reaction_delays`) ask a table for nine at once."""
        want = {}
        for t, w, floor in reqs:
            lo, fl = self.key(t, floor)
            if (lo, fl, w) not in self.kept:
                want.setdefault(fl, {}).setdefault((lo, w), None)
        for fl, ks in want.items():
            los = list(dict.fromkeys(lo for lo, _ in ks))
            aims = dict(zip(los, self._aims(los, fl)))
            for lo, w in ks:
                a = aims[lo]
                self.kept[(lo, fl, w)] = pressed(lo if a is None else a, fl, w)

    def _worth(self, A, floor):
        """What his presses are worth to him aiming at each of `A` (an array of aims) from floor
        `floor`: per aim, the members' weighted escapes and gains at its presses."""
        import numpy as np
        e = self.errors
        S = A[:, None] + e[None, :]
        if floor is not None:
            S = np.maximum(floor, S)
        idx = np.round(S / self.step).astype(np.int64)
        idx = np.where((idx >= 0) & (idx < self.width), idx, self.width)
        # The members with neither a gain nor values as one weighted coverage per start (a large
        # group's most), then the others one by one; the sum's order moves it by rounding only,
        # under the 1e-12 margin.
        if self.plain is None:
            plain = [r for r in range(len(self.w)) if self.gains[r] is None and self.vals[r] is None]
            self.plain = np.zeros(self.width + 1)
            for r in plain:
                self.plain += self.w[r] * self.mat[r]
        U = self.plain[idx].sum(axis=1)
        # The members with a `PressGain` and no values, read together (`_gain_rows`): each one's
        # escaping presses at S, their count and their shares summed over his timing errors, the
        # same numbers each member's own reads below give (a gain is elementwise in S; each
        # member's row of a C-contiguous (members, aims, errors) array is summed as that member's
        # (aims, errors) array alone is: `MEASURED` bit for bit on numpy 2.5.4, 1-1000 aims by 1-200
        # members, 2026-11-17). They are added to U one by one in member order below, as before: the
        # order of those additions is kept (numpy's sum over members does not keep it, `MEASURED`
        # the same day: 5 of 1,742 such sums differ in their last bits).
        pos, rows, groups, wv, every = self._gain_rows()
        if pos:
            OK = self.mat[rows][:, idx]
            terms = np.empty((len(rows), len(A)))
            for at, arrive, n, mr, rd, pick in groups:
                GS = arrive.searchsorted((mr[:, None, None] - S[None]) - rd[:, None, None]) / n
                terms[at] = (OK[at] * GS[pick]).sum(axis=2)
            # Each member's w * (count + shares), elementwise as its own line computes it.
            WB = wv[:, None] * (OK.sum(axis=2) + terms)
            if every:
                # Every member past the plain ones is one of these: U plus each row in member
                # order, which is what `np.add.accumulate` adds (a running sum, one row after
                # another; `MEASURED` bit for bit against the loop, 2026-11-17).
                U = np.add.accumulate(np.concatenate([U[None], WB]), axis=0)[-1]
        # Members whose `PressGain`s are equal read the same shares at S: read once per gain. Its
        # `sig` names everything its call reads (the strikes' bytes, `close`, `my_roll`, `ready`);
        # any other gain is read per object.
        shares = {}
        for r, w in enumerate(self.w if not (pos and every) else ()):
            g, v = self.gains[r], self.vals[r]
            if g is None and v is None:
                continue
            if r in pos:
                U += WB[pos[r]]
                continue
            ok = self.mat[r][idx]
            base = ok.sum(axis=1) if v is None else v[idx].sum(axis=1)
            if g is None:
                U += w * base
                continue
            gk = g.sig if type(g) is PressGain else id(g)
            gs = shares.get(gk)
            if gs is None:
                gs = shares[gk] = g(S)
            U += w * (base + (ok * gs).sum(axis=1))
        return U

    def _gain_rows(self):
        """(position of each member with a `PressGain` and no values among them {member: i}, those
        members in order as an index array, their groups, their weights as an array, whether every
        member with a gain or values is one of them): a group is the members whose gains read one
        strikes array (`arrive`: equal strikes' bytes and `close`, `sig`), as (their positions,
        `arrive`, its length, the distinct (my_roll, ready) pairs as two arrays, each member's
        pair). Made once per table."""
        import numpy as np
        if self.fast is None:
            pos, rows, by = {}, [], {}
            for r in range(len(self.w)):
                g = self.gains[r]
                if type(g) is not PressGain or self.vals[r] is not None:
                    continue
                pos[r] = len(rows)
                rows.append(r)
                by.setdefault((g.sig[1], g.sig[2]), []).append(r)
            groups = []
            for members in by.values():
                g0 = self.gains[members[0]]
                pairs = {}
                pick = [pairs.setdefault((self.gains[r].my_roll, self.gains[r].ready), len(pairs)) for r in members]
                groups.append((np.array([pos[r] for r in members], np.int64), g0.arrive, g0.n,
                               np.array([p[0] for p in pairs], float), np.array([p[1] for p in pairs], float),
                               np.array(pick, np.int64)))
            every = all(r in pos for r in range(len(self.w)) if self.gains[r] is not None or self.vals[r] is not None)
            self.fast = (pos, np.array(rows, np.int64), groups, np.array([self.w[r] for r in rows], float), every)
        return self.fast

    def key(self, t, floor=None):
        """(the earliest he means to press, the floor as far as it moves a press, None for none) of
        a reaction at `t` and `floor`: equal for any two that give the same presses. A floor at or
        before his earliest press (t plus his earliest error) moves none; a floor past t plus his
        latest error puts every press meant before `floor` - that error at the floor, so meaning
        them from there is the same."""
        e0, e1 = float(self.errors[0]), float(self.errors[-1])
        if floor is None or not floor > t + e0:
            return float(t), None
        return max(float(t), float(floor) - e1), float(floor)

    def presses(self, t, w, floor=None):
        """The presses of one reaction delay of weight `w`: `t` the earliest he can mean to press
        (his reaction), `floor` the frame before which a press comes out at it (None: none), as
        `pressed` of the aim (`key`; kept per its key and `w`: a new list each call, so a caller may
        change it)."""
        return list(self.kept_presses(t, w, floor))

    def kept_presses(self, t, w, floor=None):
        """`presses`' list as `kept` holds it ({(`key`, `w`): presses}), not a copy: the caller must
        not change it. `group_presses` and `_single_presses` hand out these lists, `kept` being their
        memo of them (until 2026-11-18 each held a copy of every list beside this one: 99.5 and
        50.2 MiB of copies at the end of Swift Slash's set, `MEASURED` by
        target/wf/memory/setmem.py --look-census). The aim
        reads no weight, and until 2026-11-18 it was kept per key as well, for a second weight on
        one key; every caller's weights are `reaction_delays`', all 1 / `REACTION_POINTS`, so none
        came, and that memo held 69.5 MiB at the end of Swift Slash's set (`MEASURED`,
        target/wf/memory/setmem.py --look-census). A second weight reads the aim again, the same."""
        lo, fl = self.key(t, floor)
        got = self.kept.get((lo, fl, w))
        if got is None:
            a = self.aim(lo, fl)
            got = self.kept[(lo, fl, w)] = pressed(lo if a is None else a, fl, w)
        return got


def dodge_presses(ok, cue, step=DODGE_STEP, floor=0.0, gain=None, val=None):
    """[(dodge start, real frames from the attack's start; weight)]: when a defender who waits and
    reacts starts his dodge.

    `ok[i]` says whether a dodge started at i x `step` escapes the attack. He cannot mean to press
    before `cue` + his reaction delay (`reaction_delays`), and a press before `floor` (the frame his
    own recovery lets him roll, for a defender caught in it) comes out at the floor. He aims his
    press with a normal timing error (`DODGE_TIMING_SD_S`, `TIMING_POINTS` quantiles), a press at
    his reaction included (version 28, `pressed`: until then an error that would put a press before
    the reaction was clamped to it, so a press at once carried none, and a defender reacting to a
    later event could land presses a defender who knew the attack could not aim). He aims where his
    presses are worth most to him
    (`_AimTable`): one for each that escapes, plus, with `gain` (a `PressGain`: this burst is the
    last of the attack that reaches him, so his punish is decided by this press), the share of the
    pool's strikes that punish from it. So he weighs a hit he lands as one he avoids (`INFERRED`;
    the attacker's own worth is not his to weigh, which keeps one plan per geometry for every build
    that flies it), and an early press that escapes is worth more than a late one. `val` (a float
    array on the grid, or None) is what escaping from each start leaves him, when that is not 1: the
    follow-up an opening's roll meets (`FollowupModel._shape`), the gain then counted where `ok`
    holds (`_AimTable`).

    Until version 25 he took the escaping run he was likeliest to land in and aimed at its middle,
    whatever his punish: a defender who could not tell the Glintstone Dart from its release punished
    it 0.50, one who knew it 0.33 (`MEASURED`, the PvP ranking's selftest). When no run is left he
    dodges at the reaction anyway (with his error), too late. So an early roll loses to a long or late live window
    and a late one to a fast startup."""
    table = _aim_table(ok, step, gain, val)
    delays = reaction_delays()
    table.prime([(cue + delay, w, floor) for delay, w in delays])
    out = []
    for delay, w in delays:
        out += table.presses(cue + delay, w, floor)
    return out


#: `_aim_table`'s tables: {(the escaping starts' bytes, `step`, the gain's `sig`, the values' bytes):
#: `_AimTable`}, emptied past `AIM_TABLES` entries.
_AIM_TABLES = {}
AIM_TABLES = 20000


def _aim_table(ok, step, gain=None, val=None):
    """`dodge_presses`' `_AimTable` of one attack, kept per what it reads (its escaping starts, the
    grid step, the gain's `sig`, the values): an outcome hands the same starts again at every floor
    and cue, and a table read again gives the same aims."""
    import numpy as np
    o = np.asarray(ok, bool)
    key = (o.tobytes(), len(o), step, None if gain is None else gain.sig,
           None if val is None else np.asarray(val, float).tobytes())
    got = _AIM_TABLES.get(key)
    if got is None:
        if len(_AIM_TABLES) >= AIM_TABLES:
            _AIM_TABLES.clear()
        got = _AIM_TABLES[key] = _AimTable([o], [1.0], step, [gain], None if val is None else [val])
    return got


def _single_presses(ok, cue, step, floor, memo, gain=None):
    """`dodge_presses(ok, cue, step, floor, gain)` with its aims and each reaction delay's presses
    kept in `memo` (`group_presses`' memo of a set of one attack): a delay's presses read only the
    earliest start it allows and its weight, so the same lists, read once per pair. `lookalike_plan`
    asks for them again at every floor of a release seen alone (Black Flame's profile at its second
    release node read them 5,780 times once the release had 27 members, 560 times with 7;
    2026-11-13)."""
    held0 = len(memo) + len(memo.get('single', ()))
    if 'single aims' not in memo:
        memo['single aims'] = _AimTable([ok], [1.0], step, [gain])
    table = memo['single aims']
    # The table's own lists, keyed as it keeps them (`_AimTable.kept_presses`).
    got = memo.setdefault('single', table.kept)
    delays = reaction_delays()
    table.prime([(cue + delay, w, floor) for delay, w in delays])
    out = []
    for delay, w in delays:
        k = table.key(cue + delay, floor) + (w,)
        chunk = got.get(k)
        if chunk is None:
            chunk = table.kept_presses(cue + delay, w, floor)
        out += chunk
    # Counted with the `lookalike_plan` memo it lives in (`LOOKALIKE_MEMO_ENTRIES`).
    _LOOK_HELD[0] += len(memo) + len(got) - held0
    return out


# ---------------------------------------------------------------------------------------------
# what is on screen: attacks the defender cannot tell apart
#
# `dodge_presses` is handed the escaping starts of the attack he faces, so the defender knows from
# the attack's first frame which attack it is and when every shot reaches him. What he sees is
# less: two spells whose `Magic.refType` names one TimeAct play the same clips, and a chargeable
# spell's tap and charge both play 045000 and then 045010 up to the tap's release (`VERIFIED`
# c0000.hks `MagicFireRight_Upper_onUpdate`; `er-mechanics-spells` `Lookalikes` has the rule for
# spells and for a charged skill's tap and hold). A plan (`lookalike_plan`) gives
# him what he sees instead: until the frame that splits a group (its `splits`), he either picks
# one timing against every attack of the group (`group_presses`) or waits for the split and
# reacts from it with his usual delay, and he takes whichever escapes the group more often, per
# situation. The outcomes (`reaction_outcome`, `sequence_outcome`, `volley_outcome`, the spell
# kit's `dynamic_outcome`, attack-similarity's `_reaction_form`) take a plan for the first burst
# that reaches him; every later burst comes after the split, so it is dodged as before. The PvP
# ranking's `skill_reaction` takes one for a charged skill cast as it opens, the hold, against its
# early release (`lookalike_pair`, `skill_release_part`).
# A release (a spell's tap, a hold spell let go early, a charged skill's early release) is let go on
# a frame of its window the attacker picks and the defender cannot see coming: it is one member per
# release frame of `release_frames`, its weight split over them, each told from the hold and from
# the later ones at its own release; what the attacker nets is their weighted mean (the callers).
# `INFERRED`: which plan he follows is his best one against the group, members weighted as the
# caller weighs them; a burst after the first is dodged knowing the attack; he decides once, at the
# cue or at one split, except that a release he sees before then he reacts to as it comes
# (`lookalike_plan` `events`; a release seen is not waited past).

def _escape(ok, presses, step=DODGE_STEP):
    """The share of `presses` ([(dodge start, weight)]) that escape: `ok[i]` for a start at i x
    `step`, and a start past the grid is caught, as `reaction_outcome` reads it. `ok` is read as
    its bytes as bools, one per start."""
    import numpy as np
    okb, n = np.asarray(ok, bool).tobytes(), len(ok)
    out = 0.0
    for s, w in presses:
        i = int(round(s / step))
        if 0 <= i < n and okb[i]:
            out += w
    return out


def _press_index(presses, step=DODGE_STEP):
    """(each press's grid index, as `_escape` reads it; its weight) of `presses`, as lists for
    `_escape_index` and then as numpy arrays."""
    import numpy as np
    idx, ws = [int(round(s / step)) for s, _ in presses], [w for _, w in presses]
    return idx, ws, np.array(idx, np.int64), np.array(ws, float)


def _escape_index(okb, n, idx, ws):
    """`_escape` of escaping starts `okb` (bytes, `n` of them) and presses read by `_press_index`:
    the same additions in the same order."""
    out = 0.0
    for i, w in zip(idx, ws):
        if 0 <= i < n and okb[i]:
            out += w
    return out


def group_presses(oks, weights, cue, step=DODGE_STEP, floor=0.0, memo=None, gains=None):
    """`dodge_presses` for a defender who cannot tell apart the attacks whose escaping starts are
    `oks` (one bool array per attack on the `step` grid, a start past an array being caught), the
    attack j weighing `weights[j]` and his gain from an escaping press against it `gains[j]` (a
    `PressGain` or None; `gains` None: none): one timing chosen against all of them. He cannot start
    before `cue` + his reaction delay, nor before `floor`; then he aims where his presses are worth
    most to him summed over the attacks by weight (`_AimTable`, the first of equally good aims), with
    `dodge_presses`' timing error, so a start that escapes two of them at once is found. One attack
    is `dodge_presses` itself. `memo`, a dict, keeps the aims for the next call on the same `oks`,
    `weights` and `gains` (it is the caller's to key)."""
    if len(oks) == 1:
        g = None if gains is None else gains[0]
        if memo is None:
            return dodge_presses(oks[0], cue, step, floor, g)
        return _single_presses(oks[0], cue, step, floor, memo, g)
    delays = reaction_delays()
    if memo is not None and 'chunks' in memo:
        # Every delay's presses kept already: the same list, read without the setup below.
        chunks, out, tab = memo['chunks'], [], memo['aims']
        for delay, w in delays:
            got = chunks.get(tab.key(cue + delay, floor) + (w,))
            if got is None:
                break
            out += got
        else:
            return out
    kept = memo is not None
    memo = {} if memo is None else memo
    table = memo.get('aims')
    if table is None:
        table = memo['aims'] = _AimTable(oks, weights, step, gains)
    # The presses of one reaction delay read only the time he can press from and that delay's
    # weight: kept per pair, in the table's own lists (`_AimTable.kept_presses`).
    chunks = memo.setdefault('chunks', table.kept)
    held0 = table.size + len(chunks)
    table.prime([(cue + delay, w, floor) for delay, w in delays])
    out = []
    for delay, w in delays:
        k = table.key(cue + delay, floor) + (w,)
        got = chunks.get(k)
        out += got if got is not None else table.kept_presses(cue + delay, w, floor)
    if kept:
        # Counted with the `lookalike_plan` memo it lives in (`LOOKALIKE_MEMO_ENTRIES`).
        _LOOK_HELD[0] += table.size + len(chunks) - held0
    return out


#: Entries the `lookalike_plan` memos of this process may gain together, the `group_presses` memos
#: inside them counted with them, before every one is emptied (`_look_memo`). Their keys carry each
#: call's decision times, floors and cues, so a set with many release members adds to them without
#: end: r12's set 272 (Flame Fall Upon Them, Giantsflame Take Thee) grew about 3 GB a minute in one
#: process to 44 GB unfinished, and 37 s into it 96 memos held 501,072 entries and the 13,012
#: inside them about 1.47 million more (r13, 2026-11-12). `INFERRED`: 2 million entries is about
#: 1 GB, that count beside the growth of an unprobed run of the same set interpolated to 37 s.
LOOKALIKE_MEMO_ENTRIES = 2_000_000
#: {id(memo): memo}: every `lookalike_plan` memo since the last emptying (`forget_look_memos`).
_LOOK_MEMOS = {}
#: The entries they have gained since.
_LOOK_HELD = [0]


def forget_look_memos():
    """Empty every `lookalike_plan` memo (`_LOOK_MEMOS`) in place: a caller holding one keeps a
    dict that fills again. Every answer they kept is the one a fresh read gives (each entry is keyed
    by everything it read, and the numbers the keys name sets by go with them), so this moves no
    number, only memory and time."""
    for m in _LOOK_MEMOS.values():
        m.clear()
    _LOOK_MEMOS.clear()
    _LOOK_HELD[0] = 0


def _look_memo(memo):
    """`memo`, kept among `_LOOK_MEMOS`; all of them emptied first when they have gained more than
    `LOOKALIKE_MEMO_ENTRIES`."""
    if _LOOK_HELD[0] > LOOKALIKE_MEMO_ENTRIES:
        forget_look_memos()
    _LOOK_MEMOS[id(memo)] = memo
    return memo


def lookalike_plan(oks, weights, splits, me, cue=0.0, floor=0.0, step=DODGE_STEP, tags=None, memo=None,
                   events=None, gains=None):
    """How a defender who sees only what is on screen times his first dodge of attack `me` of a
    group he cannot tell apart from its start (the comment above `_escape`).

    `oks[j]` is attack j's escaping starts per move (`reaction_outcome`'s `ok` for each of the
    family's moves, of its first burst that reaches him standing), None when nothing of it reaches
    him standing; `weights[j]` its weight; `splits[j][l]` the real frame from which j and l look
    different (inf when they never do, and on the diagonal). `gains[j]` (per move a `PressGain` or
    None; `gains` or `gains[j]` None: none) is what an escaping press is worth to him beyond
    escaping against j (`dodge_presses`). His options are the info times: `cue` and each split.
    Deciding at time f, facing j, he knows the group's members l with `splits[j][l]` > f and presses
    as `group_presses` presses against them from cue f (alone: `dodge_presses` from f, a reaction to
    the split). He takes the time whose presses are worth most to him over the group, weighted, by
    the measure `group_presses` aims by (the first of equally good ones; version 25: until then the
    escape share alone), averaged over the moves as the outcomes average them; a member that does
    not reach him is escaped whatever he does and adds 1. Returns {'presses': per move, `me`'s
    presses at that time, 'at': the time, 'escape': {time: the group's escape share}, 'worth':
    {time: what the presses are worth to him, the measure he chose by}}. One member and no split:
    `dodge_presses` from `cue`, exactly.

    `events[j]` (None: none) is the real frame member j releases (a tap's or an early release's
    clip switch, `release_frames`): something he sees happen, which he reacts to when it comes
    before his decision time, deciding then with what he knows then (`INFERRED`: a release is seen
    as it happens and not waited past; a member that has not released shows nothing). So waiting
    for a late split no longer costs him the early releases, which a release spread over its window
    makes the rule (without it a decision made at the last release reacts to the first that late).
    Callers pass it only for a group with a release spread over several frames; without it the
    plan is the one above, exactly.

    `tags[j]` (hashable, equal only for equal `oks[j]`) and `memo`, a dict, let calls at other
    floors, or for another member of the same group, reuse each set's aims, presses and escapes;
    each tag is taken with its member's gains (`PressGain.sig`). A floor at or before his earliest
    press from a decision time (that time plus his shortest reaction and his earliest timing error,
    `_SOONEST_PRESS`) moves no press (`_AimTable.key`), so the presses and the escape decided then
    are shared by every such floor."""
    import numpy as np
    n = len(oks)
    K = len(oks[me])
    tags = list(range(n)) if tags is None else tags
    gl = [None] * n if gains is None else [None if g is None or all(x is None for x in g) else g for g in gains]
    if any(g is not None for g in gl):
        tags = [tags[j] if gl[j] is None else (tags[j], tuple(None if x is None else x.sig for x in gl[j]))
                for j in range(n)]
    kept = memo is not None
    memo = _look_memo(memo) if kept else {}
    held0 = len(memo)
    ev = [math.inf] * n if events is None else [math.inf if e is None else float(e) for e in events]
    if ('times', cue) not in memo:
        memo[('times', cue)] = sorted({cue} | {max(cue, s) for row in splits for s in row if math.isfinite(s)})
    times = memo[('times', cue)]
    total = float(sum(weights))
    soonest = _SOONEST_PRESS
    # One small number per set of escaping starts the calls hand in (`tags`, and which members do
    # not reach him): the keys below name it instead of carrying the tuple.
    ids = memo.setdefault('tag ids', {})
    tid = ids.setdefault((tuple(tags), tuple(o is None for o in oks)), len(ids))

    def binds(t):
        # The floor as far as it moves a press decided at t: None when it moves none.
        return floor if floor > t + soonest else None

    def when(j, f):
        # When he decides facing j: at f, or at j's release when he sees it first.
        return max(cue, min(f, ev[j]))

    # One small number per set of members he cannot tell apart, by their escaping starts and
    # weights (`seen_at`): the keys below name it instead of carrying its tuple.
    sets = memo.setdefault('set ids', {})
    # Which members do not reach him, as one small number: with the group's splits it fixes who is
    # still alike to whom when.
    nones = memo.setdefault('none ids', {})
    nid = nones.setdefault(tuple(o is None for o in oks), len(nones))
    local = {}

    def seen_at(j, t):
        # The members still alike to j at t that reach him, read once per (member, time) and set of
        # members that reach him (the group's splits fix them, whatever their escaping starts), and
        # their set's number, once per call.
        got = local.get((j, t))
        if got is None:
            sk = ('seen', nid, j, t)
            seen = memo.get(sk)
            if seen is None:
                seen = memo[sk] = [l for l in range(n) if splits[j][l] > t and oks[l] is not None]
            got = local[(j, t)] = (seen, sets.setdefault(tuple((tags[l], weights[l]) for l in seen), len(sets)))
        return got

    def presses(j, f):
        # What he presses facing j, deciding at f, per move. A floor that moves none of them
        # (`binds`) is no floor: `group_presses` starts every press at the later of the decision
        # time plus a reaction delay and the floor, and such a floor is under every one of those,
        # so the presses read once serve every such floor, as the escapes below do.
        t = when(j, f)
        seen, key = seen_at(j, t)
        # The presses read only the set he cannot tell apart (`key`), when and from what floor:
        # members still alike at t share them.
        gk = ('group presses', key, t, binds(t), step)
        got = memo.get(gk)
        if got is None:
            got = memo[gk] = [group_presses([oks[l][k] for l in seen], [weights[l] for l in seen], t, step, floor,
                                            memo.setdefault(('aims', key, k), {}),
                                            [None if gl[l] is None else gl[l][k] for l in seen]) for k in range(K)]
        return key, got
    # Each member's escaping starts per move as `_escape` reads them (bytes, length), and per move
    # every member's as one bool matrix padded with False to one length, plus a last False column
    # for a press off the grid: made once per call, when an escape is read.
    okb, mats = {}, []

    def fill(j, f, t):
        # `sum(_escape(oks[l][k], presses[k], step) for k in range(K)) / K` for j and every member
        # alike to j at t (`seen_at`) whose escape against these presses is not kept: the same
        # additions in the same order, each member's adds a 0 where a press is caught, which moves
        # no sum, and numpy's running sum adds left to right.
        seen, key = seen_at(j, t)
        b = binds(t)
        # Each press's grid index and weight, as `_escape` reads them.
        ps = presses(j, f)[1]
        ix = [_press_index(p, step) for p in ps]
        rows, nos = [], set()
        for l in seen:
            if tno[l] not in nos and ('escape', tno[l], key, t, b, step) not in memo:
                nos.add(tno[l])
                rows.append(l)
        # Each move's press starts, and each gain's shares at them (a gain is elementwise in its
        # starts, so its shares at the escaping ones are these, picked): read once per call for
        # every member alike that holds an equal `PressGain` (`sig`), any other gain per object.
        starts_k, shares = {}, {}

        def share_at(k, g):
            gk = (k, g.sig if type(g) is PressGain else id(g))
            got = shares.get(gk)
            if got is None:
                if k not in starts_k:
                    starts_k[k] = np.array([s for s, _ in ps[k]], float)
                got = shares[gk] = g(starts_k[k])
            return got

        def gained(l):
            # What his escaping presses gain him against l beyond escaping, per move averaged as
            # the escape is (0.0 without gains): the measure `group_presses` aims by.
            if gl[l] is None:
                return 0.0
            out = 0.0
            for k in range(K):
                g = gl[l][k]
                if g is None:
                    continue
                o = np.asarray(oks[l][k], bool)
                _, _, idx, ws = ix[k]
                hit = (idx >= 0) & (idx < len(o))
                okv = np.zeros(len(idx), bool)
                okv[hit] = o[idx[hit]]
                if okv.any():
                    out += float((ws[okv] * share_at(k, g)[okv]).sum())
            return out / K
        if len(rows) <= 2:
            for l in rows:
                if l not in okb:
                    okb[l] = [(np.asarray(o, bool).tobytes(), len(o)) for o in oks[l]]
                e = sum(_escape_index(ob, nb, idx, ws) for (ob, nb), (idx, ws, _, _) in zip(okb[l], ix)) / K
                memo[('escape', tno[l], key, t, b, step)] = (e, e + gained(l))
            return
        if not mats:
            live = [l for l in range(n) if oks[l] is not None]
            width = max(len(oks[l][k]) for l in live for k in range(K))
            for k in range(K):
                m = np.zeros((n, width + 1), bool)
                for l in live:
                    o = np.asarray(oks[l][k], bool)
                    m[l, :len(o)] = o
                mats.append(m)
            mats.append(width)
        width = mats[K]
        per = []
        for k in range(K):
            _, _, idx, ws = ix[k]
            cols = np.where((idx >= 0) & (idx < width), idx, width)
            v = np.where(mats[k][rows][:, cols], ws, 0.0)
            per.append(np.cumsum(v, axis=1)[:, -1].tolist() if v.shape[1] else [0.0] * len(rows))
        for r, l in enumerate(rows):
            e = sum([per[k][r] for k in range(K)]) / K
            memo[('escape', tno[l], key, t, b, step)] = (e, e + gained(l))
    # The decision time is the same for every member of the group read with the same escaping starts
    # at one floor, and a floor no press can meet (at or before the cue plus his shortest reaction,
    # as `binds`) is no floor: one plan serves every such call, and each (member, time) escape every
    # floor that moves none of its presses.
    pk = ('plan', tid, cue, floor if floor > cue + soonest else None)
    if pk not in memo:
        # Per decision time, each member's (index, when he decides, the set alike to him then) in
        # member order, and one small number per member's escaping starts (`tags`): they read no
        # floor, so they are read once per set of escaping starts and cue.
        sk = ('plan rows', tid, cue)
        if sk not in memo:
            tnos = memo.setdefault('tag nos', {})
            memo[sk] = ([[(j, None, None) if oks[j] is None else (j, when(j, f), seen_at(j, when(j, f))[1])
                          for j in range(n)] for f in times], [tnos.setdefault(tg, len(tnos)) for tg in tags])
        rows, tno = memo[sk]
        best, escape, worth = None, {}, {}
        for f, row in zip(times, rows):
            esc = val = 0.0
            for j, t, key in row:
                if t is None:
                    esc += weights[j]
                    val += weights[j]
                    continue
                # The escape reads j's escaping starts (`tags[j]`) and the presses of the set he
                # cannot tell from j then (`seen_at`'s number, which names every member's starts
                # and weight), when and from what floor: kept per those, so a plan with other
                # escaping starts for a member outside that set (another member's free aim) reads
                # it once with the others.
                ek = ('escape', tno[j], key, t, floor if floor > t + soonest else None, step)
                e = memo.get(ek)
                if e is None:
                    fill(j, f, t)
                    e = memo[ek]
                esc += weights[j] * e[0]
                val += weights[j] * e[1]
            escape[f], worth[f] = esc / total, val / total
            if best is None or worth[f] > best[1] + 1e-9:
                best = (f, worth[f])
        memo[pk] = (best[0], escape, worth)
    at, escape, worth = memo[pk]
    out = {'presses': presses(me, at)[1], 'at': at, 'escape': escape, 'worth': worth}
    if kept:
        _LOOK_HELD[0] += len(memo) - held0
    return out


def lookalike_pair(other, dist, split, releases=None, shows=None, other_roll=None):
    """`reaction_outcome`'s `plan` at `dist` for an attack a defender cannot tell from `other` (its
    contact geometries, met as one burst, as `skill_reaction` meets a part) until real frame
    `split`, the two weighted equally (`INFERRED`, as `lookalike_plan`'s callers): `lookalike_plan`
    between the attack's own escaping starts and `other`'s on the same moves, `other` escaped
    whatever he does when it does not reach him standing. A charged skill's hold and its early
    release (`skill_reaction` `look`).

    `releases` ([(real frames after `split`, weight)], `release_frames`; None: [(0, 1)]) makes
    `other` one member per entry, moved that many frames on (`shift_geometry`: the release clip
    plays whole from its press, `skill_option`), its weights summing to the attack's, each told
    from the attack where it shows, its release plus `shows[i]` real frames (`release_visible`;
    None: 0), two of them where the first shows, and each a release he reacts to when he sees it
    (`lookalike_plan` `events`). One entry at 0 showing at once is the pair above, exactly.

    `other_roll` is `other`'s roll frame at its earliest release (a later release's that much
    later): with it, and a `punish` context the outcome hands in (`reaction_outcome`'s plan call),
    each release's escaping presses gain him its punish as the attack's gain him its own
    (`lookalike_plan` `gains`); without it none of the group's are counted (version 25)."""
    import numpy as np
    rel = [(0.0, 1.0)] if not releases else [(float(dl), float(w)) for dl, w in releases]
    n = len(rel)
    seen = [dl + float(s) for (dl, _), s in zip(rel, shows if shows is not None else [0.0] * n)]
    splits = [[math.inf] + [split + v for v in seen]] + [
        [split + seen[i]] + [math.inf if i == j else split + min(seen[i], seen[j]) for j in range(n)]
        for i in range(n)]
    weights = [1.0] + [w for _, w in rel]
    events = [None] + [split + v for v in seen] if n > 1 else None
    # One memo per family: at this distance the attack's own escaping starts are its one geometry's,
    # so every floor's plan shares the escapes it can (`lookalike_plan` `memo`).
    theirs, memos, adv = {}, {}, {}

    def shifted(dl):
        return other if dl == 0.0 else [shift_geometry(g, dl) for g in other]

    def plan(moves, oks, cue, floor, gains=None, punish=None):
        fam = tuple(m['anim'] for m in moves)
        if fam not in theirs:
            got = []
            for dl, _ in rel:
                gs = shifted(dl)
                live = [g['T'].max() for g in gs if g is not None]
                if not live or _catches(gs, dist, None, [0.0])[0][0] < 0:
                    got.append(None)
                else:
                    grid = np.arange(0.0, max(live) + 1.0, DODGE_STEP)
                    got.append([_catches(gs, dist, m, grid)[0] < 0 for m in moves])
            theirs[fam] = got
        gl = None
        if punish is not None and other_roll is not None:
            ordered, run = punish
            gl = [gains]
            for i, (dl, _) in enumerate(rel):
                if theirs[fam][i] is None:
                    gl.append(None)
                    continue
                if dl not in adv:
                    adv[dl] = attack_advance(shifted(dl))
                gl.append([PressGain.of(m, dist, adv[dl], run, ordered, other_roll + dl) for m in moves])
        return lookalike_plan([oks] + theirs[fam], weights, splits, 0, cue, floor, memo=memos.setdefault(fam, {}),
                              events=events, gains=gl)['presses']
    return plan


def contact_geometry(entries=None, bullet_points=None, air=False):
    """One hit's sampled contact shapes as arrays in real frames from its clip's start: `T`, `X`,
    `Z`, `R` (contact disc radius against the idle defender) and `PK` (the attacker's forward root
    motion so far, which the defender's body holds back; minus infinity for a bullet). A melee
    window is `er-mechanics-reach.window_points` entries [(seconds, peak, ((x, z, disc), ...))]
    with the defender at z = -distance; bullet points are [(seconds, x, z, disc)] with the defender
    at z = +distance, stored negated so one test serves both. A bullet point may carry two more,
    (peak, launch seconds): the caster's forward root motion when the shot left and when that was
    (`bullet_launch`). Then `PK` is that peak and `TL` the launch in real frames, so `_catches`
    holds the shot back by what the defender's body held the caster back by at the launch; a
    melee sample is held back at its own time (`TL` = `T`). `air`: every AtkParam of the hit has
    `isInvalidatedByNoDamageInAir`, so a jump is immune to it (`AIR`, `jump_motion`)."""
    import numpy as np
    t, x, z, r, pk, tl = [], [], [], [], [], []
    for when, peak, pts in entries or ():
        for px, pz, d in pts:
            t.append(when * TAE_FPS), x.append(px), z.append(pz), r.append(d), pk.append(peak)
            tl.append(when * TAE_FPS)
    launched = False
    for p in bullet_points or ():
        when, px, pz, d = p[:4]
        peak, at = (p[4], p[5]) if len(p) > 5 else (-math.inf, when)
        launched = launched or math.isfinite(peak)
        t.append(when * TAE_FPS), x.append(px), z.append(-pz), r.append(d), pk.append(peak)
        tl.append(at * TAE_FPS)
    if not t:
        return None
    out = {k: np.array(v, float) for k, v in (('T', t), ('X', x), ('Z', z), ('R', r), ('PK', pk))}
    if launched:
        out['TL'] = np.array(tl, float)
    if air:
        out['AIR'] = True
    return out


def shift_geometry(g, frames):
    """`contact_geometry` `g` moved `frames` real frames later (its launch times with it)."""
    if g is None:
        return None
    out = {**g, 'T': g['T'] + frames}
    if 'TL' in g:
        out['TL'] = g['TL'] + frames
    if g.get('CUE') is not None:
        out['CUE'] = g['CUE'] + frames
    if g.get('PRESS_CUE') is not None:
        out['PRESS_CUE'] = g['PRESS_CUE'] + frames
    if g.get('CHAIN') is not None:
        # A chain part's frames move with it (`split_chains`).
        i, n, lo, hi = g['CHAIN']
        out['CHAIN'] = (i, n, lo + frames, hi + frames)
    if g.get('TURN') is not None:
        # The clip's turn rules move with it (`clip_turn`, version 27).
        out['TURN'] = turn_shift(g['TURN'], frames)
    if 'T0' in g:
        out['T0'] = g['T0'] + frames
    if 'HOMING' in g:
        parts = [{**p, 'fire': p['fire'] + frames} for p in g['HOMING']['parts']]
        out['HOMING'] = {**g['HOMING'], 'parts': parts, 'sig': tuple(_part_sig(p) for p in parts)}
    return out


# A geometry flown again after each dodge (bd er-effects-rs-rw25; round 4, bd er-effects-rs-7bye).
# A contact geometry is one fixed set of samples, read against wherever the defender is. A shot
# whose path depends on where he goes is flown again: a geometry with a damaging shot that homes or
# is created by a shot that does, or that is made, or created by a shot made, by a `carrier` (which
# dies on him and makes its child where it touches him: where that is depends on his dodge), so
# `homing_lineage`, carries a `HOMING` record (`homing_record` of its `homing_part`s: the trees and
# the shots of each it holds; `homing_spec` for a whole tree), and `_catches` / `_catches_each` fly
# those shots again for each dodge start, against the defender on that dodge (`_homing_touch`,
# `fly_members`: each homing row turning toward his lock-on point, each carrier dying on him; a shot
# whose stored flight was aimed is aimed again at where he is when it is created, roll included, as
# FUN_140390c20 aims it, and an unaimed one leaves on its launch heading). A geometry with neither
# is not flown again, so its stored aim at where he stood stays (not modelled: a roll started before
# such a shot exists does not move its aim). Its stored samples are the flight against him standing:
# they give its live frames (bursts, the dodge grid), and the caught defender's play-out
# (`CaughtModel`) still reads them for the hits after the one that catches him (`INFERRED`
# simplification: those later shots are taken to fly as against him standing). The flights again
# take no DmgHitRecord: a carrier dies on its first landed touch even where a record another bullet
# put him in would let it fly on (`INFERRED` simplification; the record counts keep the records,
# `er-mechanics-spells._fly_records`).
#: Entries kept by `_homing_touch`'s memo before it is cleared.
HOMING_TOUCH_CACHE = 50000
#: Bytes of first-touch arrays `_homing_touch`'s memo holds before it is cleared. An entry is one
#: move's touches per shot at one distance, and a flight of a part with many shots against many
#: defenders (`HOMING_DISTS`, `HOMING_BATCH_MEMBERS`) holds 8 bytes per shot and defender, so the
#: entry count alone does not bound it.
HOMING_TOUCH_BYTES = 4 << 30
#: The bytes `_HOMING_TOUCH` holds now.
_HOMING_HELD = [0]


def forget_reads():
    """Empty the memos of flights and touch reads (`_HOMING_TOUCH`, `_HOMING_REACH`, `_CATCHES`), the
    aim tables, the attacker's facings (`_FACING`, since 2026-11-18) and the lookalike plans'
    (`forget_look_memos`): every answer they give is the one a fresh read
    gives, so this moves no number, only memory (attack-similarity empties them per attack set,
    whose attacks no other set reads)."""
    _HOMING_TOUCH.clear()
    _HOMING_REACH.clear()
    _HOMING_HELD[0] = 0
    _CATCHES.clear()
    _AIM_TABLES.clear()
    forget_aim_worths()
    _FACING.clear()
    _FACING_HELD[0] = 0
    forget_look_memos()
_HOMING_TOUCH = {}
_HOMING_PROFILE = []
_HOMING_TABLES = {}
#: The distances (m, rounded as the callers round them) a caller will ask `_homing_entries` for at
#: many of them: one flight asked for at one of them is flown in one batch with the others
#: (`_homing_batch`), which spares a call per distance of the per-frame work. Empty, the default:
#: one distance per flight. attack-similarity sets its situations' distances and run-in gaps.
HOMING_DISTS = ()
#: The most defenders (distances x moves x dodge starts) one batched flight holds.
HOMING_BATCH_MEMBERS = 6000
#: A dodge grid from 0 is flown rounded up to a multiple of this many starts (`_homing_touch`).
HOMING_START_BUCKET = 40
#: {(part signature, `AIR`): {distance: whether a shot of the part touches him standing there}},
#: from the flights against a defender who stands (`_homing_batch`).
_HOMING_REACH = {}


def homing_lineage(t, entries):
    """Per `fly_shots` entry: whether it is flown again after a dodge (above): its row `homes` or is
    a `carrier`, or a shot above it in its chain is."""
    out = []
    for e in entries:
        out.append(homes(t.bullet.get(e[0]['bullet']) or {}) or carrier(t, e[0]) or (e[5] >= 0 and out[e[5]]))
    return out


def _part_sig(p):
    sig = (p['bt']['bullet'], round(p['fire'], 4), _launch_key(p['launch']), p['dmg'], p['index'], p['aim'])
    # A chain part's rows (`split_chains`) tell it from the whole tree's part; a part without them
    # keeps the signature it had.
    return sig + (p['rows'],) if p.get('rows') is not None else sig


def _part_keys(part, tts):
    """The keys of `tts` (one move's {lineage key: touches}, `_homing_entries`) that are shots of
    `part`: every one when it names no shots, those of its `index`, and for a chain part
    (`split_chains`, its `rows`) every shot of one of those Bullet rows that this flight makes. A
    chain part is named by its rows, not by the keys one flight made: each flight's interval
    children are its own (`fly_members`), so a flight whose parent makes another number of children
    than the one the keys were read off, with more than `BULLET_INTERVAL_CAP` of them, keys its
    children by other slots, and a key list read off one flight would leave them out."""
    rows = part.get('rows')
    if rows is not None:
        return [k for k in tts if k[-1][0] in rows and (part['index'] is None or k in part['index'])]
    return list(tts) if part['index'] is None else [k for k in part['index'] if k in tts]


def _key_nodes(bt, key):
    """The tree nodes a `fly_members` lineage key passes, root first; None when the tree has no such
    path."""
    nodes, node = [], None
    for i, tok in enumerate(key):
        if i == 0:
            node = bt if tok[0] == bt['bullet'] else None
        else:
            node = node['children'].get('HitBulletID' if tok[1] == 'h' else 'intervalCreateBulletId')
        if node is None or node['bullet'] != tok[0]:
            return None
        nodes.append(node)
    return nodes


def homing_part(t, bt, start, launch, test, index=None, aim=True):
    """(one part of a `HOMING` record, whether it is flown again): tree `bt` flown from `start` real
    seconds out of `launch` (`bullet_launch` or None), `test(node)` saying which nodes hit, `index`
    the lineage keys (`fly_shots(...).keys`, `fly_members`) of the shots the geometry holds (None:
    all), `aim` whether its stored flight was aimed at the defender standing (`fly_shots` with a
    target). Flown again (`_homing_entries`), an aimed part's shots are aimed at where he is when each
    is created, so a roll he started before a shot exists moves its aim (`VERIFIED` FUN_140390c20,
    the comment above `AIM_EMIT_TYPES`), its homing rows turn after him in flight and its carriers
    die on him; an unaimed part keeps its launch heading (no caller makes one since 2026-11-13: a
    weapon skill's, `er-mechanics-spells.SpellModel.skill_attack`, is aimed as a spell's is). The
    stored samples keep the aim at where he stood. It is flown again when one of those
    shots is damaging and its row, or one above it, `homes` or is a `carrier` (`homing_lineage`).
    Plain data, so a forked worker can hand it back: the hitting nodes by bullet id (`dmg`, every
    node of the tree `test` passes)."""
    dmg, todo = set(), [bt]
    while todo:
        node = todo.pop()
        if test(node):
            dmg.add(node['bullet'])
        todo += list(node['children'].values())

    def again(node):
        return homes(t.bullet.get(node['bullet']) or {}) or carrier(t, node)
    flown = False
    if index is None:
        todo = [(bt, False)]
        while todo and not flown:
            node, up = todo.pop()
            up = up or again(node)
            flown = up and node['bullet'] in dmg
            todo += [(c, up) for c in node['children'].values()]
    else:
        for key in index:
            nodes = _key_nodes(bt, key)
            if nodes and nodes[-1]['bullet'] in dmg and any(again(n) for n in nodes):
                flown = True
                break
    part = {'bt': bt, 'fire': start * TAE_FPS, 'launch': launch, 'dmg': tuple(sorted(dmg)),
            'index': None if index is None else tuple(sorted(index)), 'aim': bool(aim)}
    return part, flown


def homing_record(t, parts):
    """The `HOMING` record of a geometry made of `parts` (`homing_part`): the tables by their
    regulation (`AshTables` registers itself), and `sig`, what tells two records apart
    (`geo_fingerprint`)."""
    _HOMING_TABLES.setdefault(t.regulation, t)
    return {'reg': t.regulation, 'parts': list(parts), 'sig': tuple(_part_sig(p) for p in parts)}


def homing_spec(t, bt, start, launch, test, aim=True):
    """`homing_record` of the whole tree `bt` (`homing_part`), or None when it is not flown again."""
    part, flown = homing_part(t, bt, start, launch, test, aim=aim)
    return homing_record(t, [part]) if flown else None


#: {id(move): (move, its `_move_key`)}: the move dicts the outcomes hand in (`evasion_motion`'s,
#: cached and never changed) are read once each; holding the dict keeps its id its own.
_MOVE_KEYS = {}


def _move_key(move):
    import numpy as np
    if move is None:
        return None
    got = _MOVE_KEYS.get(id(move))
    if got is not None and got[0] is move:
        return got[1]
    key = (float(move['iframes']), bool(move.get('air')), np.asarray(move['path'], float).tobytes())
    if move.get('track'):
        # A move he starts out of an earlier one (`carried`): where that leaves him reads too.
        key = key + (track_key(move['track']),)
    if len(_MOVE_KEYS) > 4096:
        _MOVE_KEYS.clear()
    _MOVE_KEYS[id(move)] = (move, key)
    return key


# ---------------------------------------------------------------------------------------------
# the roll he is in (version 26)
#
# User, 2026-11-16: "I don't know what the model is doing, I just want it to be accurate, and
# logically consistent". Until version 26 a defender who escaped one burst of an attack met the
# next one back at the distance he started from, free to dodge again from the burst's last live
# frame plus his roll's recovery past its invincibility (`INFERRED` then), whatever his roll did.
# The spell kit's re-aimed casts already carried him (`er-mechanics-spells.CastThreats._touches`:
# each roll from where the last one left him, the next no sooner than the last one's start plus its
# dodge cancel). Now every outcome does: an escaping press leaves him on that roll's root motion
# from its start, invincible for its own frames, standing where its clip leaves him, and able to
# dodge again only from its start plus its dodge cancel; the next burst is read against that
# (`dodge_chain`, `FollowupModel`, `volley_outcome`). A track is where he has been carried:
# ((start, x, z, move), ...) in time order, each segment the root motion `move['path']` from real
# frame `start` added to (x, z), the place it started from (sideways, back from where he first
# stood); before the first segment he stands where he started.
#
# Version 27 (the same user words): two of version 26's stand-ins replaced by what the clips say.
# * His place. Version 26 stood him where the first whole frame of his roll's dodge cancel (f21)
#   left him; the clip goes on carrying him past it (the back roll 3.71 m back at f21, 3.17 m at
#   its end; a side roll 3.42 m aside at f21, 4.41 m at f56, `MEASURED` from `roll_motion`). Now a
#   segment is the whole clip (`track_extend`): free to dodge again from its dodge cancel, he is on
#   its root motion until he next acts, a later press starting from where it has him then.
# * The attacker's facing. Version 26 turned the attacker fully to face where the last roll left
#   him before each later burst and not at all within one (`INFERRED`). Now his facing follows the
#   clip's own turn rules every update (`clip_turn`, `_facing`): locked on, he turns toward the
#   defender at most the rate the clip allows that frame, read by `er-mechanics-reach.turn_rate_at`
#   from the TAE (0 under JumpTable 7 'Disable Turning', else the last usable TAE 224 SetTurnSpeed,
#   else `DEFAULT_TURN_DEG_PER_S`, 720 deg/s; the handlers and the store `VERIFIED` there, that the
#   player's modifier keeps 720 `INFERRED` there). Each sample is read in the facing it was made
#   in (`_turn_apply`): a melee sample at its own frame, a shot at its launch (`TL`). Savage Claws
#   turns at 600 deg/s through its first five claws and is locked from f70; Rosus's Summons is
#   locked from f41.1, before its first burst (`TAE`, 2026-11-16). The tracks stay in the attack's
#   start frame (no more turning the track, version 26's `face_track`), and a roll out of a track
#   is taken facing the attacker (`oriented`).
# A homing or aimed shot is flown again at where the track puts him (`_homing_touch`), with no
# facing applied (its aim at creation follows him). The spell kit's re-aimed casts
# (`er-mechanics-spells.CastThreats`) chain their re-rolls by `dodge_presses` too (version 27).

#: A defender who stands where his track left him (`still_move`): one frame of no travel, no
#: invincibility, every cancel at once.
STILL = {'anim': 'still', 'iframes': 0.0, 'ready': 0.0, 'dodge': 0.0, 'move': 0.0, 'path': [(0.0, 0.0)],
         'distance': 0.0}
#: The start of a segment that has always held: a track whose rolls are over by the next burst,
#: collapsed to where they left him (`collapse_track`).
TRACK_EVER = -1e9
#: {id(move): (move, px, pz)}: a move's root motion as arrays (`track_at`).
_PATH_ARRAYS = {}
#: {(id(move), track key): carried move}: one dict per carried move, so the memos that key on a
#: move's identity or its `anim` see one move (`carried`).
_CARRIED = {}
CARRIED_CACHE = 50000


def _path_arrays(m):
    """(px, pz) of move `m`'s root motion, kept per dict."""
    import numpy as np
    got = _PATH_ARRAYS.get(id(m))
    if got is None or got[0] is not m:
        p = np.asarray(m['path'], float)
        if len(_PATH_ARRAYS) > 4096:
            _PATH_ARRAYS.clear()
        got = _PATH_ARRAYS[id(m)] = (m, p[:, 0].copy(), p[:, 1].copy())
    return got[1], got[2]


def dodge_end(m):
    """The real frame of move `m` he can dodge again from: its dodge cancel, else its clip's end."""
    return float(m['dodge']) if m['dodge'] is not None else float(len(m['path']))


def track_key(track):
    """A key equal only for equal tracks: each segment's start, place and move."""
    return tuple((float(t0), float(x), float(z), _move_key(m)) for t0, x, z, m in track)


def track_at(track, T, air=False):
    """(x, z, immune) of a defender carried along `track` at real frames `T` (any shape): sideways
    and back from where he first stood, and whether the segment he is on still holds its
    invincibility (only against an `AIR` hit for a move with `air`, `_catches`' rule). Each segment's
    root motion is read as `_catches` reads a dodge (`path[floor(u)]`, its last frame after its
    clip); before the first he stands where he started."""
    import numpy as np
    T = np.asarray(T, float)
    x, z, imm = np.zeros(T.shape), np.zeros(T.shape), np.zeros(T.shape, bool)
    for t0, ox, oz, m in track:
        on = T >= t0
        if not on.any():
            continue
        px, pz = _path_arrays(m)
        u = T - t0
        k = np.clip(np.floor(np.where(on, u, 0.0)).astype(np.int64), 0, len(px) - 1)
        x = np.where(on, ox + px[k], x)
        z = np.where(on, oz + pz[k], z)
        hold = (u < float(m['iframes'])) & (not m.get('air') or bool(air))
        imm = np.where(on, hold, imm)
    return x, z, imm


def track_extend(track, move, start):
    """`track` with `move` started at real frame `start` from where the track has him then, as it
    carries an escaped defender: on its whole root motion, standing at its last frame after the
    clip, until a later segment (his next press) takes over from where this one has him then
    (`track_at`). Version 27: until then the segment was cut at the clip's dodge cancel, which
    stood him where f21 left him (`INFERRED` then)."""
    x, z, _ = track_at(track, [float(start)])
    return tuple(track) + ((float(start), float(x[0]), float(z[0]), move),)


def collapse_track(track, at):
    """`track` as one segment at where it leaves him, when every segment's clip is over by real
    frame `at` (his place is then fixed and no invincibility is left: every read from `at` on is
    the same), else `track` itself. Equal collapsed tracks merge (`dodge_chain`)."""
    if not track:
        return track
    t0, _, _, m = track[-1]
    if t0 <= TRACK_EVER or at < t0 + len(m['path']) - 1 or at < t0 + float(m['iframes']):
        return track
    x, z, _ = track_at(track, [float(at)])
    return ((TRACK_EVER, float(x[0]), float(z[0]), STILL),)


#: {(id(move), cos, sin): (move, the move turned)} (`_turned`).
_TURNED = {}


def _turned(m, c, s):
    """Move `m` with its root motion turned by the angle of cosine `c` and sine `s` (x' = c x - s z,
    z' = s x + c z), its `anim` naming the turn; `m` itself when it does not move (`STILL`)."""
    import numpy as np
    px, pz = _path_arrays(m)
    if not (px.any() or pz.any()):
        return m
    key = (id(m), round(c, 12), round(s, 12))
    got = _TURNED.get(key)
    if got is not None and got[0] is m:
        return got[1]
    path = list(zip((c * px - s * pz).tolist(), (s * px + c * pz).tolist()))
    out = {**m, 'path': path, 'anim': f"{m['anim']}@{math.degrees(math.atan2(s, c)):.6f}"}
    if len(_TURNED) > 20000:
        _TURNED.clear()
    _TURNED[key] = (m, out)
    return out


def oriented(m, track, dist, ahead=0.0):
    """Move `m` as he starts it out of `track` (version 27): its root motion turned so that its
    forward (-z, toward the attacker) points at the attacker's root, `ahead` m up the attack's axis
    (a number, or a function of (real frame, his z) giving it), from where the track's last segment
    leaves him (its clip's end; a collapsed track, its one place, the same point). `m` itself for an
    empty track or one that left him on the axis. The lock-on keeps him facing the attacker and
    `roll_motion`'s directions are relative to that (`INFERRED`: read where his last roll ends, not
    at each press; a press made during its slide sees a bearing at most 7 degrees off, a side roll's
    3.42 m aside at its dodge cancel against 4.41 m at its end, 2.5 m out, `MEASURED` on
    `roll_motion`). Until version 27 the track was turned instead (`face_track`), the attacker
    facing where the last roll left him."""
    if not track:
        return m
    t0, _, _, sm = track[-1]
    f = 0.0 if t0 <= TRACK_EVER else float(t0) + len(sm['path']) - 1
    x, z, _ = track_at(track, [f])
    x, z = float(x[0]), float(z[0])
    if abs(x) < 1e-12:
        return m
    a = ahead(f, z) if callable(ahead) else float(ahead)
    r = math.hypot(x, dist + z - a)
    return _turned(m, (dist + z - a) / r, -x / r)


# ---------------------------------------------------------------------------------------------
# the attacker's facing (version 27; the comment above `STILL`)

#: {(category, anim): `clip_turn`'s profile}.
_CLIP_TURN = {}
#: Radians the attacker may turn in one update (`DODGE_STEP`) outside every TAE window of a clip
#: (`er-mechanics-reach.DEFAULT_TURN_DEG_PER_S`, 720 deg/s: 12 degrees an update).
_DEFAULT_TURN_STEP = []


def _default_turn_step():
    if not _DEFAULT_TURN_STEP:
        _DEFAULT_TURN_STEP.append(math.radians(_pose()[1].DEFAULT_TURN_DEG_PER_S) * DODGE_STEP / TAE_FPS)
    return _DEFAULT_TURN_STEP[0]


def clip_turn(category, anim, events=None):
    """The attacker's turn over TAE entry (`category`, `anim`), on the update grid from its first
    real frame: {'t0': 0.0, 'rate': radians he may turn in each update (`DODGE_STEP` real frames)
    from then, read at the update's middle by `er-mechanics-reach.turn_rate_at` (0 under JumpTable 7,
    else the last usable TAE 224 rate, else 720 deg/s; locked on), 'fwd': his root's forward travel
    at each update's start (the HKX root motion, -Z), None when the clip is not read, 'sig': what
    names it}. Past 'rate' he turns at 720 deg/s (the clip's windows are over); past 'fwd' his root
    stays at its last. Real frames follow the clip's TAE 608 play speed (`ATK.clip_to_real`).
    `events` are the entry's (default `ATK.resolve_events`). Cached."""
    import numpy as np
    key = (category, anim)
    if key in _CLIP_TURN:
        return _CLIP_TURN[key]
    R = _pose()[1]
    if events is None:
        try:
            events = ATK.resolve_events(category, anim)[2] or []
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            events = []
    turns, disabled = R.turn_windows(events)
    to_real = ATK.clip_to_real(events) if events else (lambda s: s)
    end_c = max([w['end'] for w in turns] + [e for _, e in disabled] + [0.0])
    end_c = min(end_c, 1e3 / TAE_FPS)
    h = DODGE_STEP
    # Clip time against real time every 1/600 s of clip, inverted by interpolation (monotone).
    cg = np.arange(0.0, end_c + 1.0 / 600, 1.0 / 600)
    rg = np.array([to_real(float(c)) for c in cg]) * TAE_FPS
    n = int(math.ceil(rg[-1] / h)) if len(rg) and rg[-1] > 0 else 0
    mid = (np.arange(n) + 0.5) * h
    cm = np.interp(mid, rg, cg) if n else np.zeros(0)
    rate = np.array([R.turn_rate_at(float(c), turns, disabled) for c in cm], float)
    rate = np.radians(rate) * h / TAE_FPS
    fwd = None
    try:
        clip = _pose()[0].load_animation(category, anim)
        dur = float(clip.duration)
        # Real frames against clip time over the whole clip (608 windows past the turn events too).
        cc = np.arange(0.0, dur + 1.0 / 600, 1.0 / 600)
        rr = np.array([to_real(float(c)) for c in cc]) * TAE_FPS
        m = int(math.ceil(rr[-1] / h)) + 1
        ct = np.minimum(np.interp(np.arange(m) * h, rr, cc), dur)
        fwd = np.array([-float(clip.root_motion(float(c))[2]) for c in ct], float)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        fwd = None
    out = {'t0': 0.0, 'rate': rate, 'fwd': fwd, 'sig': ('clip', int(category), int(anim))}
    _CLIP_TURN[key] = out
    return out


def turn_shift(turn, frames):
    """`turn` (a `clip_turn` profile, or None) started `frames` real frames later."""
    if turn is None or not frames:
        return turn
    return {**turn, 't0': float(turn['t0']) + float(frames)}


#: {key: (theta array or None)} (`_facing`), the one read last at the end: past `FACING_CACHE`
#: entries or `FACING_BYTES` of arrays the one read longest ago goes. Until 2026-11-18 it was emptied
#: at 20,000 entries only, and kept across attack sets: one set of Ghostflame Ignition held 10,422
#: entries and 718 MiB of arrays in it (the timing log's item of 2026-11-18 09:10), so a long set
#: could hold about 1.4 GB in it. `MEASURED` 2026-11-18 by target/wf/memory/setmem.py: Swift
#: Slash's set held 12,312 entries and 710 MiB at its largest; with 256 MiB it held at most 6,952
#: and turned the attacker 9,040 times where keeping every facing turns him 5,735 times (43,472
#: reads), and Divine Beast Frost Stomp's set read none of its 5,185 facings twice. Emptied with
#: the other reads at each set's start and end (`forget_reads`): what it keeps is what a fresh read
#: gives, so neither moves a number.
_FACING = {}
FACING_CACHE = 20000
FACING_BYTES = 256 << 20
#: The array bytes `_FACING` holds now.
_FACING_HELD = [0]


def _turn_track(turn, x, D, n):
    """(facing, root drift x, root drift D), each [defender, update]: the attacker's facing (radians
    toward +x from the attack's axis) at each update `turn['t0'] + j * DODGE_STEP`, j < `n`, for
    defenders at (x, D) [defender, update] (sideways, ahead of the attacker's start), and how far
    his turning has moved his root off where his clip's root motion would put it straight down the
    axis. He faces each on the profile's first update (his clip starts facing him), then turns
    toward where the defender is on the next update, by at most that update's rate (`clip_turn`).
    His root goes each update's forward root motion (`fwd`'s step) along the facing he had then, so
    a lunge made while he turns bends toward the defender; the drift is that path less the straight
    one. His bearing is read from that root held 2 x `PUSH_CAPSULE_RADIUS` short of the defender
    along his facing (the body stop every hit reads, `_held`). `INFERRED`: the root turns with the
    facing (the HKX root motion is in the character's own frame); the clip's lateral root motion
    is not read (`fwd` is its forward part, as `PK` and `ADV` take it)."""
    import numpy as np
    fwd = turn.get('fwd')
    S = x.shape[0]
    if fwd is not None and len(fwd):
        f = np.asarray(fwd, float)[np.minimum(np.arange(n), len(fwd) - 1)]
    else:
        f = np.zeros(n)
    step = np.diff(f, append=f[-1] if n else 0.0)
    r2 = 2 * _pose()[1].PUSH_CAPSULE_RADIUS
    rate = np.full(n, _default_turn_step())
    r0 = np.asarray(turn['rate'], float)[:n]
    rate[:len(r0)] = r0
    if S == 1:
        # One path (a caught defender's play, `CaughtModel._touch_all`): the same steps in plain
        # floats, which numpy's per-call cost on one element made the dearest line of a play.
        return _turn_track_one(x[0].tolist(), D[0].tolist(), f.tolist(), step.tolist(), rate.tolist(), r2, n)
    th, dx, dz = np.empty((S, n)), np.zeros((S, n)), np.zeros((S, n))

    def bearing(j, t):
        # From his root (straight path plus drift), held short of the defender along facing `t`.
        rx, rz = dx[:, j], f[j] + dz[:, j]
        st, ct = np.sin(t), np.cos(t)
        fd = (x[:, j] - rx) * st + (D[:, j] - rz) * ct
        held = np.maximum(0.0, r2 - fd)
        return np.arctan2(x[:, j] - (rx - held * st), D[:, j] - (rz - held * ct))
    th[:, 0] = bearing(0, np.zeros(S))
    for j in range(n - 1):
        t = th[:, j]
        if step[j] != 0.0:
            dx[:, j + 1] = dx[:, j] + step[j] * np.sin(t)
            dz[:, j + 1] = dz[:, j] + step[j] * (np.cos(t) - 1.0)
        else:
            dx[:, j + 1], dz[:, j + 1] = dx[:, j], dz[:, j]
        r = rate[j]
        if r <= 0.0:
            th[:, j + 1] = t
        else:
            th[:, j + 1] = t + np.clip(bearing(j + 1, t) - t, -r, r)
    return th, dx, dz


def _turn_track_one(x, D, f, step, rate, r2, n):
    """`_turn_track` of one defender path, in floats: the same operations in the same order (sin,
    cos, the hold-back, the bearing, the clamp), returned as (1, n) arrays."""
    import numpy as np
    th, dx, dz = [0.0] * n, [0.0] * n, [0.0] * n

    def bearing(j, t):
        rx, rz = dx[j], f[j] + dz[j]
        st, ct = math.sin(t), math.cos(t)
        fd = (x[j] - rx) * st + (D[j] - rz) * ct
        held = max(0.0, r2 - fd)
        return math.atan2(x[j] - (rx - held * st), D[j] - (rz - held * ct))
    th[0] = bearing(0, 0.0)
    for j in range(n - 1):
        t = th[j]
        if step[j] != 0.0:
            dx[j + 1] = dx[j] + step[j] * math.sin(t)
            dz[j + 1] = dz[j] + step[j] * (math.cos(t) - 1.0)
        else:
            dx[j + 1], dz[j + 1] = dx[j], dz[j]
        r = rate[j]
        th[j + 1] = t if r <= 0.0 else t + min(max(bearing(j + 1, t) - t, -r), r)
    return np.array([th]), np.array([dx]), np.array([dz])


def _facing(turn, dist, move, starts, upto):
    """(facing, root drift x, root drift D), each [start, update] (`_turn_track`), against a
    defender `dist` m ahead who dodges `move` (`carried` on its track) from each of `starts`, read
    as `_catches` reads his place (`path[floor(u)]`, on the track before the start), from the
    profile's first update to `upto` (rounded up to a block of 32 updates); None when no facing is
    read: no profile, no move, or a defender who never leaves the axis (then he faces him
    throughout, his root goes straight, and every sample reads as before version 27)."""
    import numpy as np
    if turn is None or move is None:
        return None
    starts = np.asarray(starts, float).ravel()
    h = DODGE_STEP
    t0 = float(turn['t0'])
    n = int(math.ceil(max(0.0, float(upto) - t0) / h / 32.0) * 32) + 1
    key = (turn['sig'], t0, float(dist), _move_key(move), starts.tobytes(), n)
    if key in _FACING:
        # Read again: last to go.
        got = _FACING[key] = _FACING.pop(key)
        return got
    tg = t0 + h * np.arange(n)
    px, pz = _path_arrays(move)
    u = tg[None, :] - starts[:, None]
    k = np.clip(np.floor(u).astype(np.int64), 0, len(px) - 1)
    on = u >= 0
    trk = move.get('track')
    if trk:
        OX, OZ, _ = track_at(trk, starts)
        tx, tz, _ = track_at(trk, tg)
        x = np.where(on, OX[:, None] + px[k], tx[None, :])
        z = np.where(on, OZ[:, None] + pz[k], tz[None, :])
    else:
        x = np.where(on, px[k], 0.0)
        z = np.where(on, pz[k], 0.0)
    out = _turn_track(turn, x, dist + z, n) if x.any() else None
    if out is not None and not (out[0].any() or out[1].any() or out[2].any()):
        out = None
    size = 0 if out is None else sum(a.nbytes for a in out)
    while _FACING and (len(_FACING) >= FACING_CACHE or _FACING_HELD[0] + size > FACING_BYTES):
        old = _FACING.pop(next(iter(_FACING)))
        _FACING_HELD[0] -= 0 if old is None else sum(a.nbytes for a in old)
    _FACING[key] = out
    _FACING_HELD[0] += size
    return out


def _drift_max(rot):
    """None when `rot` (`_facing`'s) is None, else the farthest his turning moves his root (m)."""
    import numpy as np
    if rot is None:
        return None
    return float(np.hypot(rot[1], rot[2]).max())


def _turn_when(g, J):
    """The real frame each sample `J` of `g` takes its facing from: a shot's launch (`TL`), a melee
    sample's own frame (a finite `PK`), else the geometry's first sample (a shot with no launch read,
    `INFERRED`: it leaves on the facing of its first sample)."""
    import numpy as np
    if 'TL' in g:
        return g['TL'][J]
    T = g['T'][J]
    return np.where(np.isfinite(g['PK'][J]), T, float(g['T0']) if 'T0' in g else float(np.nanmin(g['T'])))


def _turn_index(turn, tq, n):
    """Update index of `turn` each real frame of `tq` reads (clipped to the n computed)."""
    import numpy as np
    return np.clip(np.floor((np.asarray(tq, float) - float(turn['t0'])) / DODGE_STEP + 1e-9).astype(np.int64), 0, n - 1)


def _turn_apply(x, D, th, pk, ddx=0.0, ddz=0.0):
    """(x, D) of a defender as a sample made with the attacker facing `th` reads him: turned by -th
    about the attacker's root as the sample has it, `pk` up the axis (a finite `PK`; else his
    start) moved by his turning's drift (`ddx`, `ddz`, `_turn_track`), so a defender straight down
    the facing is straight down the sample's axis at his distance from that root. Written so a
    facing and drift of exactly 0 return (x, D) unchanged to the bit."""
    import numpy as np
    p = np.where(np.isfinite(pk), pk, 0.0)
    c1, s = np.cos(th) - 1.0, np.sin(th)
    vx, vz = x - ddx, D - p - ddz
    return x + (c1 * vx - s * vz - ddx), D + (s * vx + c1 * vz - ddz)


def carried(move, track):
    """`move` started out of `track` (`_catches`' dodge move, its `track` key read by every touch
    reader): from its start he moves on from where the track has him then; before it he is on the
    track. An empty track is `move` itself, the defender who stood where he started. One dict per
    (move, track) (`_CARRIED`); its `anim` names the track too, so a caught defender's play
    (`CaughtModel.dodge_key`) and the forms' play keys tell carried moves apart."""
    import hashlib
    if not track:
        return move
    tk = track_key(track)
    key = (id(move), tk)
    got = _CARRIED.get(key)
    if got is not None and got[0] is move:
        return got[1]
    tag = hashlib.blake2b(repr((move['anim'], tk)).encode(), digest_size=8).hexdigest()
    out = {**move, 'anim': f"{move['anim']}~{tag}", 'track': tuple(track), 'base': move}
    if len(_CARRIED) >= CARRIED_CACHE:
        _CARRIED.clear()
    _CARRIED[key] = (move, out)
    return out


def still_move(track):
    """(move, start): the defender on `track` who does not dodge again, as `_catches` reads a dodge:
    the track's last segment started at its own start out of the segments before it."""
    t0, _, _, m = track[-1]
    if t0 <= TRACK_EVER:
        return carried(STILL, track), 0.0
    return carried(m, track[:-1]), float(t0)


def _shift_track(track, frames):
    """`track` `frames` real frames later (a collapsed segment holds at every frame)."""
    if not track or not frames:
        return track
    return tuple((t0 if t0 <= TRACK_EVER else t0 + frames, x, z, m) for t0, x, z, m in track)


def _shift_move(move, frames):
    """`move` with its track `frames` real frames later (a move with no track is itself)."""
    trk = move.get('track') if move is not None else None
    if not trk or not frames:
        return move
    return carried(move['base'], _shift_track(trk, frames))


def _origins(move, starts):
    """(x, z) arrays where `move` (`carried`) starts from at each of `starts`, None for a move with no
    track (he starts where he stood)."""
    trk = move.get('track') if move is not None else None
    if not trk:
        return None
    x, z, _ = track_at(trk, starts)
    return x, z


def _homing_entries(part, reg, dist, moves, air, starts, keep=True, least=False):
    """Per move of `moves` (None: he stands): {lineage key of each hitting shot of `part`'s tree
    (`fly_members`): the first real frame that shot, flown again against the defender on each
    dodge start (every move and start at once), touches him outside his invincibility (inf where it
    never does; a shot that touches him on no start of the move is left out)}. `air`: the geometry's
    `AIR` mark (a jump is immune only to such a hit). Memoised
    per move on the flight, not on the geometry, so a volley's legs share one flight and a family's
    four roll directions one batch. A distance of `HOMING_DISTS` is flown in one batch with the
    others of them it will be asked for (`_homing_batch`): every defender's shots fly on their own
    and each one's touches are read off its own flight (`_entry_first_touch`), so the numbers are
    those of flying it alone. `keep` False neither reads nor keeps the memo (`_homing_many`'s
    batches, never asked for again).

    `least` (with `keep` False only): the caller reads only each defender's earliest touch over the
    part's shots (`_part_keys`, the least of them, as `_homing_many` reads them), so no shot is
    made for a defender a counted shot already touched by the frame it would be made at
    (`fly_members` `bound`). Each defender's least touch is the same; a later shot's own touches
    may then read inf. Why (2026-11-19, .worktrees/gl-stall/target/glh): Golden Land then R2's shot
    2792 (a 3.5 m burst) is followed by 2793-2797, whose interval children (2795) and their homing
    2796 and 2797 were 76% of its flight time (py-spy, prof1), flown for every defender, those 2792
    had touched included; with `least` the same 5,531 answers took 107 s where 165 (chk1)."""
    import numpy as np
    sig = _part_sig({**part, 'index': None, 'rows': None})
    mkeys = [_move_key(mv) for mv in moves]
    # `starts` per move (a list, `keep` False only): each move flown from its own starts alone.
    each = isinstance(starts, (list, tuple))
    if each and keep:
        raise ValueError('_homing_entries: starts per move are flown with keep False only')
    sb = None if each else starts.tobytes()

    def keys_at(d):
        return [(sig, round(float(d), 4), mk, air, sb) for mk in mkeys]
    keys = keys_at(dist)
    if keep and all(k in _HOMING_TOUCH for k in keys):
        return [_HOMING_TOUCH[k] for k in keys]
    dists =[dist] + (_homing_batch(sig, dist, moves, air, len(starts), keys_at) if keep else [])
    if each:
        mstarts = [np.asarray(x, float).ravel() for x in starts]
        offs = np.concatenate([[0], np.cumsum([len(x) for x in mstarts])]).astype(int)
        per = int(offs[-1])
        mi = np.repeat(np.arange(len(moves)), np.diff(offs))
        S = np.concatenate(mstarts) if per else np.zeros(0)
    else:
        s = len(starts)
        mstarts = [starts] * len(moves)
        offs = s * np.arange(len(moves) + 1)
        per = s * len(moves)
        mi = np.tile(np.repeat(np.arange(len(moves)), s), len(dists))
        S = np.tile(starts, len(moves) * len(dists))
    n = per * len(dists)
    # Each move's root motion, padded with its last frame to one length (he stands after his clip).
    paths = [np.zeros((1, 2)) if mv is None else np.array(mv['path'], float) for mv in moves]
    m = max(len(p) for p in paths)
    PX = np.array([np.concatenate([p[:, 0], np.full(m - len(p), p[-1, 0])]) for p in paths])
    PZ = np.array([np.concatenate([p[:, 1], np.full(m - len(p), p[-1, 1])]) for p in paths])
    still = np.array([mv is None for mv in moves])[mi]
    iframes = np.array([0.0 if mv is None else float(mv['iframes']) for mv in moves])[mi]
    immune = np.array([mv is not None and (not mv.get('air') or air) for mv in moves])[mi]
    # Where he stands: the distance of each defender's flight (one number when it is one distance).
    ahead = dist if len(dists) == 1 else np.repeat(np.array(dists, float), per)
    ahead_d = np.array(dists, float)
    # The root motions flat, read at move x `m` + frame: one gather, the same numbers.
    PXf, PZf, row = PX.ravel(), PZ.ravel(), mi * m
    # The same with a 0 before each move's first frame: where he is before his dodge starts, read
    # at frame -1 (`moved`).
    PX1 = np.concatenate([np.zeros((len(moves), 1)), PX], axis=1)
    PZ1 = np.concatenate([np.zeros((len(moves), 1)), PZ], axis=1)
    # A move out of an earlier one (`carried`, version 26): it starts from where its track has him
    # at each start, and before it he is on the track. Per move, the origins at the starts.
    tracks = [mv.get('track') if mv is not None else None for mv in moves]
    origins = [track_at(trk, mstarts[j])[:2] if trk else None for j, trk in enumerate(tracks)]
    if any(tracks):
        OXs, OZs = np.zeros(n), np.zeros(n)
        for di in range(len(dists)):
            for j, o in enumerate(origins):
                if o is not None:
                    at_ = slice(di * per + offs[j], di * per + offs[j + 1])
                    OXs[at_], OZs[at_] = o[0], o[1]

    def moved(T):
        """How far he has moved at real frame(s) `T`, (x, z) arrays (..., `per`): every move's, side
        by side. A defender's frame of his move is read off his dodge start alone, and every move and
        distance has the same starts (unless each move has its own): read once per start, then laid
        out per move (each distance's are the same). Before his start he has not moved (frame -1 of
        `PX1`, `PZ1`); from it he is at its frame, his last after his clip. A carried move: from its
        origin, on its track before."""
        x, z = None, None
        for j in range(len(moves)):
            u = np.asarray(T, float)[..., None] - mstarts[j]
            k = np.minimum(np.maximum(np.floor(u).astype(int), -1), m - 1) + 1
            if x is None:
                x, z = np.empty(u.shape[:-1] + (per,)), np.empty(u.shape[:-1] + (per,))
            at_j = slice(offs[j], offs[j + 1])
            if tracks[j]:
                tx, tz, _ = track_at(tracks[j], np.asarray(T, float)[..., None])
                on = u >= 0
                x[..., at_j] = np.where(on, origins[j][0] + PX1[j][k], tx)
                z[..., at_j] = np.where(on, origins[j][1] + PZ1[j][k], tz)
            else:
                x[..., at_j], z[..., at_j] = PX1[j][k], PZ1[j][k]
        return x, z

    def where(T):
        """Where he is at real frame(s) `T`: arrays (n,) for one frame, (len(T), n) for several (his
        moves laid out per distance, each where he stands plus how far he has moved)."""
        x0, z0 = moved(T)
        if len(dists) == 1:
            return x0, ahead + z0
        x, z = np.empty(x0.shape[:-1] + (n,)), np.empty(x0.shape[:-1] + (n,))
        for di, d in enumerate(ahead_d):
            at_d = slice(di * per, (di + 1) * per)
            x[..., at_d] = x0
            np.add(d, z0, out=z[..., at_d])
        return x, z

    def at_each(T, who):
        u = T - S[who]
        k = row[who] + np.minimum(np.maximum(np.floor(u).astype(int), 0), m - 1)
        moving = (u >= 0) & ~still[who]
        vuln = ~(moving & (u < iframes[who]) & immune[who])
        if not any(tracks):
            return (np.where(moving, PXf[k], 0.0),
                    (ahead if len(dists) == 1 else ahead[who]) + np.where(moving, PZf[k], 0.0), vuln)
        x = np.where(moving, OXs[who] + PXf[k], 0.0)
        z = np.where(moving, OZs[who] + PZf[k], 0.0)
        Tb = np.broadcast_to(np.asarray(T, float), u.shape)
        for j, trk in enumerate(tracks):
            if not trk:
                continue
            sel = (mi[who] == j) & ~moving
            if sel.any():
                tx, tz, ti = track_at(trk, Tb[sel], air)
                x[sel], z[sel], vuln[sel] = tx, tz, ~ti
        return x, (ahead if len(dists) == 1 else ahead[who]) + z, vuln
    # The most he moves in one bullet step: his position changes once per real frame, by one step
    # of his root motion (or onto its first frame when the move starts); a carried move's track too.
    seg_paths = [np.asarray(sm['path'], float) for trk in tracks if trk for _, _, _, sm in trk]
    jump = max([0.0] + [float(np.hypot(*p[0])) for p in paths]
               + [float(np.hypot(*np.diff(p, axis=0).T).max()) for p in paths + seg_paths if len(p) > 1])
    margin = jump * math.ceil(TAE_FPS / BULLET_FPS) + 1e-6
    t = _HOMING_TABLES.get(reg) or AshTables(reg)
    launch, fire = part['launch'], part['fire']
    held = None
    if launch is not None and math.isfinite(launch['peak']):
        held = np.maximum(0.0, launch['peak'] - (where(fire)[1] - 2 * _pose()[1].PUSH_CAPSULE_RADIUS))
    outs = [[{} for _ in moves] for _ in dists]
    added = 0
    # Each defender's earliest counted touch so far (`least`), which `fly_members` reads before it
    # makes a child.
    bound = np.full(n, math.inf) if least and not keep else None
    # An aimed part's shots are aimed at where he is on the frame each one is created, his roll
    # included (FUN_140390c20 reads the target's position then); one left unaimed keeps its launch
    # heading. Each entry is read as it is flown and let go, so one shot's paths are held at a time.
    for e in fly_members(t, part['bt'], n, fire, launch, at_each, part['aim'], held, margin=margin, bound=bound):
        if e['node']['bullet'] not in part['dmg']:
            continue
        tt = np.full(n, math.inf)
        tt[e['who']] = _entry_first_touch(e, fire, at_each, margin)[e['inv']]
        if bound is not None and _part_keys(part, {e['key']: None}):
            np.minimum(bound, tt, out=bound)
        kept = False
        for di, out in enumerate(outs):
            for j, o in enumerate(out):
                v = tt[di * per + offs[j]:di * per + offs[j + 1]]
                # A shot that touches none of one move's defenders is left out: `_homing_touch` takes
                # the least of the shots' touches, which an inf never moves, so a missing key reads
                # as it would. Founding Rain of Stars' flights are 90% such answers (2026-11-13).
                if not np.isposinf(v).all():
                    # Flights whose interval children differ yield one key apiece (`fly_members`),
                    # each inf off its own members.
                    o[e['key']] = v if e['key'] not in o else np.minimum(o[e['key']], v)
                    kept = True
        if kept:
            added += tt.nbytes
    if not keep:
        return outs[0]
    # Each kept answer is a view of its flight's `tt`, so the bytes held are the flights' kept.
    if len(_HOMING_TOUCH) > HOMING_TOUCH_CACHE or _HOMING_HELD[0] + added > HOMING_TOUCH_BYTES:
        _HOMING_TOUCH.clear()
        _HOMING_REACH.clear()
        _HOMING_HELD[0] = 0
    _HOMING_HELD[0] += added
    for d, out in zip(dists, outs):
        for k, o in zip(keys_at(d), out):
            _HOMING_TOUCH[k] = o
        if all(mv is None for mv in moves):
            # Where a shot of the part touches him standing, its other flights there are asked for.
            touched = any(bool(np.isfinite(x).any()) for o in out for x in o.values())
            _HOMING_REACH.setdefault((sig, air), {})[round(float(d), 4)] = touched
    return outs[0]


def _homing_batch(sig, dist, moves, air, s, keys_at):
    """The other distances of `HOMING_DISTS` to fly in one batch with the flight of the part of
    signature `sig` at `dist` against `moves` from `s` dodge starts: none when `dist` is not one of
    them. A defender who stands is asked for at every distance; a dodging one only where a shot of
    the part touches him standing (his moves are read where the attack reaches him standing,
    `reaction_outcome`), which its flights standing tell (`_HOMING_REACH`). Those not flown yet, up
    to `HOMING_BATCH_MEMBERS` defenders in all.

    None for a move carried out of an earlier roll (`carried`, version 26): its track was laid at
    `dist` (each roll turned to face the attacker from there, `oriented`), so the same move is
    seldom asked for at another distance (only a track that left him on the axis, which `oriented`
    does not turn, reads the same at every distance) and its flights there are thrown away. From
    version 27, when a `_TouchTable` began flying every homing geometry against every carried move,
    each such flight took about 26 distances with it. Golden Land on a Large Club then R2 at frame
    68 flew 7,919 extra distances for its first 228 carried tables; of the 8,157 batched keys its
    first 6.5 min held, 94 were ever read, all of them by 30 s: 1.7 s a table, 15 times the 0.12 s
    each takes alone, and the set stood unfinished after 3.5 h in the kit run of 11-17 (2026-11-18,
    target/wf/blowup/diag.py, digA and fix2). Each member flies alone, so the numbers are the same:
    on 48 homing trees with interval children, a carried move's flight batched with 3 to 7 other
    distances and flown alone gave the same bytes at 2, 3 and 8 m (2026-11-18,
    target/wf/verify-blowup/batchexact.py with draws=1, batchexact3.py)."""
    if dist not in HOMING_DISTS or any(mv is not None and mv.get('track') for mv in moves):
        return []
    room = HOMING_BATCH_MEMBERS // (s * len(moves)) - 1
    standing = all(mv is None for mv in moves)
    reach = _HOMING_REACH.get((sig, air)) or {}
    out = []
    for d in HOMING_DISTS:
        if len(out) >= room:
            break
        if d == dist or not (standing or reach.get(round(float(d), 4))):
            continue
        if not all(k in _HOMING_TOUCH for k in keys_at(d)):
            out.append(d)
    return out


#: {(id(geometry), distance, `_move_key`, dodge starts' bytes): (the geometry, `_homing_touch`'s
#: answer)}: answers flown ahead of the read that asks for them (`_homing_ready`), each taken by it.
_HOMING_READY = {}
#: Defenders (moves x starts) `_homing_many` flies in one call at most.
HOMING_MANY_MEMBERS = 3000


def _homing_many(g, dist, reqs):
    """Per (move, starts) of `reqs`: `_homing_touch(g, dist, move, starts)`, every move flown in as
    few calls as `HOMING_MANY_MEMBERS` lets (`_homing_entries` with `keep` False and `least`, each
    move from its own lattice starts), each answer read off its own defender's flights. The same numbers as
    `_homing_touch`: per part the same shift, the same shifted move and fire, a start's flight is
    its own (`_homing_entries`; its interval children too since 2026-11-18, `fly_members`), and its
    touches are the least over the same shots plus the same shift.

    Why: the follow-up read each carried move alone (the four moves of each burst table, each
    press delay's roll, each one-start standing table), and a flight call costs more than its
    defenders. Flown together, Golden Land on a Large Club then R2 at frame 68 reached 929 of its
    first form's stages in 140 CPU s where reading them alone took 304 (2026-11-18,
    target/wf/goldenland run3 against run2; with neither this nor `_RowsTable`, run1 made 335 in
    140)."""
    import numpy as np
    spec = g['HOMING']
    air = bool(g.get('AIR'))
    reqs = [(mv, np.asarray(s, float).ravel()) for mv, s in reqs]
    out = [np.full(len(s), math.inf) for _, s in reqs]
    for part in spec['parts']:
        shift = math.floor(part['fire'] / DODGE_STEP) * DODGE_STEP
        lat = []
        for r, (mv, s) in enumerate(reqs):
            ii = np.round((s - shift) / DODGE_STEP).astype(int)
            if len(s) and np.array_equal(ii * DODGE_STEP + shift, s):
                lat.append((r, ii))
                continue
            # Off the lattice: flown as `_homing_touch` flies it.
            tts = _homing_entries(part, spec['reg'], dist, [mv], air, s)[0]
            for k in _part_keys(part, tts):
                out[r] = np.minimum(out[r], tts[k][slice(None)] + 0.0)
        # Chunks of up to `HOMING_MANY_MEMBERS` defenders, each move flown from its own starts
        # (`_homing_entries` with a start list per move). Until 2026-11-19 a chunk flew every move on
        # its starts' union: Golden Land then R2 at 1.5 m flew 110,178 defenders for 79,711 asked in
        # its first 90 s (.worktrees/gl-stall prof2).
        lat.sort(key=lambda e: (int(e[1].min()), int(e[1].max())))
        chunks, cur, size = [], [], 0
        for e in lat:
            if cur and size + len(e[1]) > HOMING_MANY_MEMBERS:
                chunks.append(cur)
                cur, size = [], 0
            cur.append(e)
            size += len(e[1])
        if cur:
            chunks.append(cur)
        fire = {**part, 'fire': part['fire'] - shift}
        for ch in chunks:
            mv_ = [_shift_move(reqs[r][0], -shift) for r, _ in ch]
            res = _homing_entries(fire, spec['reg'], dist, mv_, air, [ii * DODGE_STEP for _, ii in ch], keep=False,
                                  least=True)
            for (r, ii), tts in zip(ch, res):
                for k in _part_keys(part, tts):
                    out[r] = np.minimum(out[r], tts[k] + shift)
    return out


def _homing_ready(dist, reqs):
    """Fly ahead, together (`_homing_many`), the homing shots that `_homing_touch` will be asked
    to fly against carried moves: `reqs` [(geometries, move, dodge starts)], each answer kept until
    that read takes it (`_HOMING_READY`). Returns the keys it set, for `_homing_forget`. A read that
    asks for other starts than were flown ahead flies as before."""
    import numpy as np
    by = {}
    for geoms, mv, s in reqs:
        if mv is None or not mv.get('track'):
            continue
        s = np.asarray(s, float).ravel()
        if not len(s):
            continue
        mk = _move_key(mv)
        for g in geoms:
            if g is None or 'HOMING' not in g:
                continue
            k = (id(g), float(dist), mk, s.tobytes())
            if k in _HOMING_READY:
                continue
            got = by.setdefault(id(g), (g, {}))[1]
            if k not in got:
                got[k] = (mv, s)
    keys = []
    for g, todo in by.values():
        if len(todo) < 2:
            continue
        for k, b in zip(todo, _homing_many(g, dist, list(todo.values()))):
            _HOMING_READY[k] = (g, b)
            keys.append(k)
    return keys


def _homing_forget(keys):
    """Drop the answers `_homing_ready` set that no read took."""
    for k in keys:
        _HOMING_READY.pop(k, None)


def _homing_touch(g, dist, move, starts):
    """`_catches_each`'s column for a geometry with a `HOMING` record: per dodge start (real frames),
    the first real frame one of its shots, flown again against the defender on that dodge
    (`_homing_entries`), touches him outside his invincibility; inf where none does. He stands
    `dist` m ahead and follows `move` from each start (None: he stands); the launch is held back by
    his body where he is at the launch, as `_held` holds a stored sample. A roll direction is flown
    with the other three of `roll_motion`, which the callers read next.

    A flight read against a dodge started a whole number of `DODGE_STEP`s later, fired that much
    later, is the same flight that much later (the defender's place is read off his start alone).
    So a part is flown from the fraction of a step its fire time has (`fire0`), against the starts
    taken back by the rest (`shift`), and its touches are put back by it: the releases of one cast
    (`release_frames`), which differ by whole frames, share their flights. The callers' dodge grids
    lie on the `DODGE_STEP` lattice and end with the geometry's live frames, which the aim at his
    lock-on point makes differ by distance; such a grid is flown widened to whole blocks of
    `HOMING_START_BUCKET` lattice starts, so the flights at many distances and releases share one
    grid (`_homing_batch`), and each member flies alone (the starts asked for read the same
    flights). A grid off the lattice is flown as it is."""
    import numpy as np
    spec = g['HOMING']
    starts = np.asarray(starts, float).ravel()
    if _HOMING_READY and move is not None and move.get('track'):
        # Flown ahead with other carried moves (`_homing_ready`).
        got = _HOMING_READY.pop((id(g), float(dist), _move_key(move), starts.tobytes()), None)
        if got is not None and got[0] is g:
            return got[1]
    n = len(starts)
    family = roll_motion() if move is not None else []
    moves = family if any(mv is move for mv in family) else [move]
    j = next(i for i, mv in enumerate(moves) if mv is move)
    best = np.full(n, math.inf)
    for part in spec['parts']:
        shift = math.floor(part['fire'] / DODGE_STEP) * DODGE_STEP
        lattice = (starts - shift) / DODGE_STEP
        ii = np.round(lattice).astype(int) if n else np.zeros(0, int)
        if n and np.array_equal(ii * DODGE_STEP + shift, starts):
            bucket = ((ii.min() // HOMING_START_BUCKET) * HOMING_START_BUCKET,
                      (ii.max() // HOMING_START_BUCKET + 1) * HOMING_START_BUCKET)
            # A carried move's track moves back with the starts (the same flight that much earlier).
            mv_ = [_shift_move(mv, -shift) for mv in moves]
            fire_ = {**part, 'fire': part['fire'] - shift}
            if move is not None and move.get('track'):
                # A carried move (`carried`) is flown on the starts asked for alone: its track is its
                # own, so no other grid reads the bucket's other starts (`_homing_batch` flies it at
                # no other distance either), and each start flies on its own, so the numbers are the
                # same (2026-11-18, target/wf/goldenland chkG and chkG2: 948 such answers, flown
                # alone or together by `_homing_many`, against the bucketed flights, every byte equal;
                # interval children included since 2026-11-18, `fly_members`).
                lo, hi = ii.min(), ii.max() + 1
            else:
                lo, hi = bucket
            tts = _homing_entries(fire_, spec['reg'], dist, mv_, bool(g.get('AIR')),
                                  np.arange(lo, hi) * DODGE_STEP)[j]
            pick = ii - lo
        else:
            shift, pick = 0.0, slice(None)
            tts = _homing_entries(part, spec['reg'], dist, moves, bool(g.get('AIR')), starts)[j]
        for k in _part_keys(part, tts):
            best = np.minimum(best, tts[k][pick] + shift)
    return best


def _held(g, dist, move, u, starts, n, pz, radius):
    """What the defender's body holds each sample of `g` back by (`contact_geometry`): `PK` less
    the closest the caster's root may come, `d - 2 * radius`, with `d` where the defender is at
    the sample (melee) or at the shot's launch (`TL`)."""
    import numpy as np
    if 'TL' not in g:
        return None
    ul = g['TL'][None, :] - starts
    if move is None:
        dl = np.full(ul.shape, float(dist))
    else:
        kl = np.clip(np.floor(ul).astype(int), 0, n - 1)
        o = _origins(move, starts)
        if o is None:
            dl = dist + np.where(ul >= 0, pz[kl], 0.0)
        else:
            # Carried (`carried`): from where the track has him, and on it before the start.
            dl = dist + np.where(ul >= 0, o[1] + pz[kl], track_at(move['track'], g['TL'][None, :])[1])
    return np.maximum(0.0, g['PK'][None, :] - (dl - 2 * radius))


#: {id(geometry): (geometry, its arrays, its samples' positions in time order, its digest)}: what
#: `_catches` reads of a geometry once (`_geo_reads`). A `contact_geometry` is never changed once
#: made (a moved one is a new dict, `shift_geometry`), and the entry is taken only while the dict
#: and its arrays are the ones it was read from; holding the dict keeps its id its own.
_GEO_READS = {}
#: The samples of one geometry `_geo_first_touch` tests against the dodge starts at once, the first
#: time; each next block is twice as many.
FIRST_TOUCH_BLOCK = 256
#: {(geometry digests, distance, `_move_key`, dodge starts, body radius): `_catches`' answer}:
#: equal reads asked again (a lookalike group's plan reads each member's first burst, which that
#: member's own outcome reads too; builds and objective lines whose attack has the same shapes read
#: the same geometry). A read of a geometry with a `HOMING` record is not kept here (its flights
#: are, `_homing_entries`).
_CATCHES = {}
#: Entries `_CATCHES` keeps before it is emptied.
CATCHES_CACHE = 20000


def _geo_reads(g):
    """(`g`'s samples' positions in time order, None when a time is NaN; a digest of everything
    `_catches` reads of it, None for a geometry with a `HOMING` record)."""
    import hashlib
    import numpy as np
    arrs = tuple(g.get(k) for k in ('T', 'X', 'Z', 'R', 'PK', 'TL'))
    got = _GEO_READS.get(id(g))
    if got is not None and got[0] is g and all(a is b for a, b in zip(got[1], arrs)):
        return got[2], got[3]
    # A NaN time has no place in the order (and the table's min would carry it): such a geometry is
    # read as the whole table.
    order = None if np.isnan(g['T']).any() else np.argsort(g['T'], kind='stable')
    digest = None
    if 'HOMING' not in g:
        h = hashlib.blake2b(repr(bool(g.get('AIR'))).encode(), digest_size=16)
        if g.get('CHAIN') is not None:
            # A chain part's shared frame goes to the later part (`_catches_read`).
            h.update(repr(('chain',) + tuple(float(x) for x in g['CHAIN'])).encode())
        if g.get('HPR') is not None:
            # A tie in one hit priority record goes by its priority (`_catches_read`).
            h.update(repr(('hpr', g['HPR'])).encode())
        if g.get('TURN') is not None:
            # The attacker's facing is read from the clip's turn rules (`_facing`, version 27).
            h.update(repr(('turn', g['TURN']['sig'], float(g['TURN']['t0']), g.get('T0'))).encode())
        for k, a in zip(('T', 'X', 'Z', 'R', 'PK', 'TL'), arrs):
            if a is not None:
                a = np.ascontiguousarray(a)
                h.update(f'{k} {a.dtype} {a.shape}'.encode() + a.tobytes())
        digest = h.digest()
    if len(_GEO_READS) > 2048:
        _GEO_READS.clear()
    _GEO_READS[id(g)] = (g, arrs, order, digest)
    return order, digest


def _geo_first_touch(g, dist, move, starts, px, pz, n, radius, bound=None):
    """`_catches`' read of one geometry `g` (no `HOMING` record) against a defender who dodges
    `move` (path `px`, `pz`, `n` frames long) from each of `starts` ((N, 1) real frames): per start,
    the first real frame a sample touches him outside his invincibility, inf where none does. With
    `bound` (per start), a start whose first touch is at or after its bound may read any number at
    or after it instead (`_catches` takes only a first touch below the best so far).

    Each (start, sample) pair read is read with the same operations on the same numbers as the
    whole table (`_catches` before this) reads it; the pairs left out are those that cannot move
    the answer:
    - A sample `_may_touch` rules out (its disc misses every place he can be at, with room for
      rounding) never touches.
    - The rest are read in time order, a block at a time, each start only while the block's first
      sample is before both its first touch so far and its bound: a later sample cannot come
      earlier, and the earliest of the samples read is the earliest of all.
    A geometry with a NaN time (which the table's min carries to every start it touches) is read
    as the whole table."""
    import numpy as np
    T, X, Z, R, PK, TL = g['T'], g['X'], g['Z'], g['R'], g['PK'], g.get('TL')
    found = np.full(starts.shape[0], math.inf)
    # The attacker's facing as he tracks this defender (version 27, `_facing`), None when it is his
    # start's throughout.
    turn = g.get('TURN')
    rot = _facing(turn, dist, move, starts[:, 0], float(np.nanmax(TL if TL is not None else T))) \
        if turn is not None else None
    order = _geo_reads(g)[0]
    whole = order is None
    cols = np.arange(len(T)) if whole else order[_may_touch(g, dist, move, _drift_max(rot))[order]]
    if not len(cols):
        return found
    lim = found if bound is None else np.asarray(bound, float)
    immune = not move.get('air') or bool(g.get('AIR'))
    iframes = move['iframes']
    # A move out of an earlier one (`carried`, version 26): before its start he is on the track, and
    # it starts from where the track has him then.
    trk = move.get('track')
    if trk:
        OX, OZ, _ = track_at(trk, starts)
        air = bool(g.get('AIR'))
    pos, size = 0, len(cols) if whole else FIRST_TOUCH_BLOCK
    while pos < len(cols):
        J = cols[pos:pos + size]
        act = np.arange(len(found)) if whole else np.nonzero(np.minimum(found, lim) > T[J[0]])[0]
        if not len(act):
            break
        st = starts[act]
        TJ = T[J][None, :]
        u = TJ - st
        k = np.clip(np.floor(u).astype(int), 0, n - 1)
        moving = u >= 0
        if trk:
            tx, tz, ti = track_at(trk, TJ, air)
            xr = np.where(moving, OX[act] + px[k], tx)
            d = dist + np.where(moving, OZ[act] + pz[k], tz)
            vuln = ~((moving & (u < iframes) & immune) | (~moving & ti))
        else:
            xr = np.where(moving, px[k], 0.0)
            d = dist + np.where(moving, pz[k], 0.0)
            vuln = ~(moving & (u < iframes) & immune)
        if TL is not None:
            ul = TL[J][None, :] - st
            kl = np.clip(np.floor(ul).astype(int), 0, n - 1)
            if trk:
                tl_ = track_at(trk, TL[J][None, :])
                xl = np.where(ul >= 0, OX[act] + px[kl], tl_[0])
                dl = dist + np.where(ul >= 0, OZ[act] + pz[kl], tl_[1])
            else:
                xl = np.where(ul >= 0, px[kl], 0.0)
                dl = dist + np.where(ul >= 0, pz[kl], 0.0)
        if rot is not None:
            # Each sample read in the facing it was made in, about his root where his turning has
            # moved it (`_turn_apply`).
            ji = _turn_index(turn, _turn_when(g, J), rot[0].shape[1])
            th, ddx, ddz = rot[0][act][:, ji], rot[1][act][:, ji], rot[2][act][:, ji]
            xr, d = _turn_apply(xr, d, th, PK[J][None, :], ddx, ddz)
            if TL is not None:
                xl, dl = _turn_apply(xl, dl, th, PK[J][None, :], ddx, ddz)
        if TL is not None:
            held = np.maximum(0.0, PK[J][None, :] - (dl - 2 * radius))
        else:
            held = np.maximum(0.0, PK[J][None, :] - (d - 2 * radius))
        touch = vuln & (np.hypot(X[J][None, :] - xr, Z[J][None, :] + held + d) < R[J][None, :])
        found[act] = np.minimum(found[act], np.where(touch, TJ, math.inf).min(axis=1))
        pos += size
        size *= 2
    return found


def _catches(geoms, dist, move, starts):
    """Per dodge start in `starts` (real frames): (index of the hit that first touches the defender
    outside his invincibility, its time), -1 / inf when none does. The defender stands `dist` m
    straight ahead; from the start he follows the dodge's root motion (`move['path']`, one (x, z)
    per real frame, his forward toward the attacker), invincible for `move['iframes']` frames (with
    `move['air']`, only to a hit whose geometry has `AIR`), and stands still after the clip. `move`
    None is no dodge. A melee sample is held back by his body as in
    `er-mechanics-reach.window_contact_time`; a bullet sample with a launch (`TL`) by what his body
    held the caster back by when the shot left (`_held`). A geometry with a `HOMING` record is flown
    again against each dodge (`_homing_touch`). A read asked for again is answered from `_CATCHES`
    (copies, so a caller that changes its answer changes no other)."""
    import numpy as np
    radius = _pose()[1].PUSH_CAPSULE_RADIUS
    starts = np.asarray(starts, float)
    digests = [b'' if g is None else _geo_reads(g)[1] for g in geoms]
    key = None
    if all(d is not None for d in digests):
        key = (tuple(digests), float(dist), _move_key(move), starts.shape, starts.tobytes(), radius)
        got = _CATCHES.get(key)
        if got is not None:
            return got[0].copy(), got[1].copy()
    best_h, best_t = _catches_read(geoms, dist, move, starts, radius)
    if key is not None:
        if len(_CATCHES) >= CATCHES_CACHE:
            _CATCHES.clear()
        _CATCHES[key] = (best_h.copy(), best_t.copy())
    return best_h, best_t


def _catches_read(geoms, dist, move, starts, radius):
    """`_catches` read afresh."""
    import numpy as np
    starts = starts[:, None]
    best_t = np.full(starts.shape[0], math.inf)
    best_h = np.full(starts.shape[0], -1)
    if move is not None:
        path = np.array(move['path'], float)
        px, pz, n = path[:, 0], path[:, 1], len(path)
    # Each chain part's last frame, the one it shares with the next part (`split_chains`).
    ends = np.array([g['CHAIN'][3] if g is not None and g.get('CHAIN') is not None else math.nan
                     for g in geoms] + [math.nan], float)

    # Each geometry's hit priority record (`HPR`: (key, priority, created)) or None, as arrays with
    # a last entry for best_h -1: the record's index among those here (-1 none), priority, created.
    hprs = [None if g is None else g.get('HPR') for g in geoms]
    rkeys = {}
    rec_i = np.array([-1 if r is None else rkeys.setdefault(r[0], len(rkeys)) for r in hprs] + [-2], int)
    rec_p = np.array([(0.0, 0.0) if r is None else (float(r[1]), float(r[2])) for r in hprs] + [(0.0, 0.0)], float)

    def take(h, tt):
        better = tt < best_t
        ch = geoms[h].get('CHAIN')
        if ch is not None and ch[0] > 0:
            # The frame a chain part shares with the part before it is this one's: a tie there goes
            # to it (`_live`'s reading, `split_chains`).
            better |= (tt == best_t) & np.isfinite(tt) & (tt == ch[2]) & (ends[best_h] == tt)
        r = hprs[h]
        if r is not None:
            # One frame's tie in one record goes to the lower priority, then to the attack created
            # first, as `CaughtModel._play` breaks it (DmgMan's list, FUN_140527d40; version 21:
            # until then the earlier geometry in the list took it, and the play could land both).
            bp = rec_p[best_h]
            wins = (rec_i[best_h] == rec_i[h]) & ((rec_p[h, 0] < bp[:, 0])
                                                  | ((rec_p[h, 0] == bp[:, 0]) & (rec_p[h, 1] < bp[:, 1])))
            better |= (tt == best_t) & np.isfinite(tt) & wins
        best_t[better], best_h[better] = tt[better], h
    for h, g in enumerate(geoms):
        if g is None:
            continue
        if 'HOMING' in g:
            take(h, _homing_touch(g, dist, move, starts[:, 0]))
            continue
        if move is not None:
            # Only a first touch below the best so far is taken (`_geo_first_touch` `bound`); a
            # chain part's tie, and a tie a hit priority record can win, is read with a bound a
            # hair later.
            bound = best_t if g.get('CHAIN') is None and g.get('HPR') is None else np.nextafter(best_t, math.inf)
            take(h, _geo_first_touch(g, dist, move, starts, px, pz, n, radius, bound))
            continue
        u = g['T'][None, :] - starts
        xr, d, vuln = 0.0, np.full(u.shape, float(dist)), np.ones(u.shape, bool)
        held = _held(g, dist, move, u, starts, 1, None, radius)
        if held is None:
            held = np.maximum(0.0, g['PK'][None, :] - (d - 2 * radius))
        touch = vuln & (np.hypot(g['X'][None, :] - xr, g['Z'][None, :] + held + d) < g['R'][None, :])
        take(h, np.where(touch, g['T'][None, :], math.inf).min(axis=1))
    return best_h, best_t


#: `_ordered`'s sorts: {id(strikes): (strikes, their frames sorted)}, the array kept so its id names it.
_ORDERED = {}


def _ordered(strikes):
    """`strikes` (the pool's R1 strike frames) as floats, sorted: one sort per array, which every
    `reaction_outcome` of a run reads, where it sorted them once per call and move (a sampled
    profile of r19's Black Flame set put 16% of its samples on that line). An array is taken as
    unchanged while it lives, as
    `er-mechanics-spells.strike_table` takes it; a list (a selftest's) is sorted each time."""
    import numpy as np
    if not isinstance(strikes, np.ndarray):
        return np.sort(np.asarray(strikes, float))
    got = _ORDERED.get(id(strikes))
    if got is None or got[0] is not strikes:
        if len(_ORDERED) >= 8:
            _ORDERED.clear()
        got = _ORDERED[id(strikes)] = (strikes, np.sort(np.asarray(strikes, float)))
    return got[1]


#: `_press_reads` of the plans' press lists: {id(list): (list, its reads)}, the list kept so its id
#: names it; emptied past `PRESS_READS_CACHE` entries.
_PRESS_READS = {}
PRESS_READS_CACHE = 20000


def _press_reads(presses, keep=False):
    """([grid index of each press's start, as `reaction_outcome` reads it], [its weight], its starts
    as an array) of `presses` [(start, weight)]. With `keep` (a plan's list, which its memo holds
    and no caller changes) kept per list: a plan hands the same list to every outcome that reads
    it."""
    import numpy as np
    got = _PRESS_READS.get(id(presses)) if keep else None
    if got is None or got[0] is not presses:
        got = (presses, [int(round(s / DODGE_STEP)) for s, _ in presses], [w for _, w in presses],
               np.array([s for s, _ in presses], float))
        if keep:
            if len(_PRESS_READS) >= PRESS_READS_CACHE:
                _PRESS_READS.clear()
            _PRESS_READS[id(presses)] = got
    return got[1], got[2], got[3]


def _punish_ctx(ordered, my_roll, run):
    """What a plan needs to make each lookalike's `PressGain` (`lookalike_plan` `gains`): (the
    pool's strikes sorted, `run`), or None when no punish is counted (no strikes, no roll frame)."""
    if ordered is None or my_roll is None or not len(ordered):
        return None
    return ordered, run


def _plan_call(plan, moves, oks, cue, floor, gains=None, punish=None):
    """`plan(moves, oks, cue, floor)`, with `gains` and `punish` as keywords when a punish is
    counted (a plan made before version 25, a selftest's, takes the four alone)."""
    if punish is None:
        return plan(moves, oks, cue, floor)
    return plan(moves, oks, cue, floor, gains=gains, punish=punish)


def reaction_outcome(geoms, hit_value, dist, cue=0.0, moves=None, my_roll=None, advance=None,
                     strikes=None, floor=0.0, run=False, catches=None, plan=None, aim_punish=True):
    """A defender `dist` m ahead who waits for this attack and dodges on reaction (section 16).

    `geoms` is one `contact_geometry` per hit (None where a hit has no shapes), `hit_value[h]` what
    the attack is worth when hit h is the first to catch him (for a slot 1.0, for a skill the
    damage of that hit and of every later one that lands). `cue` is the real frame the attack
    becomes readable: its clip's start, or for a hit whose timing the attacker picks (an R2's
    release, a skill follow-up, a stance) the start of that part. Each move of `moves` is taken
    equally, and for each the dodge starts where `dodge_presses` puts it, no earlier than `floor`;
    with `moves` None he takes the medium roll's direction that leaves the attacker least
    (`dodge_families`; until version 22 the four were taken equally, `INFERRED`).

    Returns `value` (expected worth when he reacts), `p_evade`, and, when `strikes` (the pool's
    R1 strike frames) and `my_roll` (the attacker's first roll frame from the same clip start)
    are given, `p_punish` and `punish_hp`: an evading dodger punishes when his R1 cancel plus his
    strike comes before the attacker can roll, and he ends within `PUNISH_REACH_M` of where the
    attacker's root motion left him (`advance`, `attack_advance`; None: `geoms`' own, the caller
    of one burst of a longer attack passing the whole attack's). With `run`, a dodger who ends further
    away runs the rest in at run speed before he strikes (`er-mechanics-neutral.frames_per_metre`,
    as `Opponents.dodge_value` does): an attacker who stays at range is punished when his roll
    comes late enough.

    `catches`, a dict, memoises both `_catches` reads under ('base', `dist`) and (`dist`, index in
    `moves`). Neither read depends on `cue`, `floor`, `my_roll`, `strikes` or `run`, and the dodge
    grid depends only on `geoms`, so one dict serves every call on the same `geoms` and `moves`
    (`sequence_outcome` keys one per family and burst).

    `plan(moves, oks, cue, floor)`, when given, times the dodge instead of `dodge_presses`: it is
    handed each move's escaping starts and returns each move's presses (`lookalike_plan`: what a
    defender who cannot tell this attack from others presses). It is called only when the attack
    reaches him standing; with `aim_punish`, also with `gains` (his per move `PressGain`) and
    `punish` (the strikes and `run`, `_punish_ctx`) as keywords. A burst every geometry of which a
    search launches (`CUE`) is read from that launch instead, when it is later than `cue`
    (`burst_cue`).

    `aim_punish` (version 25): this burst's punish is his to take (it is the last of the attack that
    reaches him), so he aims his dodge for it too (`dodge_presses` `gain`); False, at escaping
    alone (a later burst decides his punish: `sequence_outcome`)."""
    import numpy as np
    advance = attack_advance(geoms) if advance is None else advance
    if moves is None:
        # No family named: he picks his roll's direction knowing the attack (`dodge_families`).
        best = None
        for fi, fam in enumerate(dodge_families()):
            o = reaction_outcome(geoms, hit_value, dist, cue, fam, my_roll, advance, strikes, floor, run,
                                 None if catches is None else catches.setdefault(('family', fi), {}), plan,
                                 aim_punish)
            if best is None or o['value'] - o['punish_hp'] < best['value'] - best['punish_hp']:
                best = o
        return best
    cue = burst_cue(geoms, cue)

    def caught(key, move, starts):
        # `starts` a list, or a function that makes it: the dodge grid is made only for a read the
        # memo does not hold.
        if catches is None:
            return _catches(geoms, dist, move, starts() if callable(starts) else starts)
        if key not in catches:
            catches[key] = _catches(geoms, dist, move, starts() if callable(starts) else starts)
        return catches[key]
    base_h, _ = caught(('base', dist), None, [0.0])
    base = hit_value[base_h[0]] if base_h[0] >= 0 else 0.0
    if base <= 0 or all(g is None for g in geoms):
        return {'value': base, 'base': base, 'p_evade': 0.0, 'p_punish': 0.0, 'punish_hp': 0.0}

    def grid():
        return np.arange(0.0, max(g['T'].max() for g in geoms if g is not None) + 1.0, DODGE_STEP)
    # The strikes in order, sorted once per array (`_ordered`): adding one `close` to each keeps the
    # order, so `ordered + close` is the sort of `strikes + close`, the same numbers.
    ordered = None if strikes is None else _ordered(strikes)
    value = p_evade = p_punish = 0.0
    hs = [caught((dist, k), m, grid)[0] for k, m in enumerate(moves)]
    # What an escaping press gains him beyond escaping, per move: this burst's punish, when it is his
    # to take (`aim_punish`; `dodge_presses`).
    punish = _punish_ctx(ordered, my_roll, run) if aim_punish else None
    gains = [PressGain.of(m, dist, advance, run, ordered, my_roll) for m in moves] if punish else [None] * len(moves)
    planned = _plan_call(plan, moves, [h < 0 for h in hs], cue, floor, gains, punish) if plan is not None else None
    bh, nm = int(base_h[0]), len(moves)
    # A catch in his own dodge is played from it when `hit_value` can (`_LazyWorth.mid`, a
    # `CaughtModel`'s worths, version 25): from the dodge's start on the grid, at the catch frame.
    mid = getattr(hit_value, 'mid', None)
    for k, m in enumerate(moves):
        h = hs[k]
        # The catches read as a list (kept beside them in `catches`), the punish counts too
        # (below): the same numbers.
        hl = None if catches is None else catches.get(('list', dist, k))
        if hl is None:
            hl = h.tolist()
            if catches is not None:
                catches[('list', dist, k)] = hl
        tl = None
        if mid is not None:
            tl = None if catches is None else catches.get(('times', dist, k))
            if tl is None:
                tl = caught((dist, k), m, grid)[1].tolist()
                if catches is not None:
                    catches[('times', dist, k)] = tl
        nh = len(h)
        # His strike's press: the R1 cancel in reach, else the move cancel plus his run in.
        ready, close, near = punish_lead(m, dist, advance, run)
        punishes = ordered is not None and my_roll is not None and near and len(ordered)
        # The share of strikes + close below a bound: a binary search counts what the mean of the
        # comparison counted, the same number, without a pass over the strikes per press.
        arrive = ordered + close if punishes else None
        presses = planned[k] if planned is not None else dodge_presses(h < 0, cue, floor=floor, gain=gains[k])
        # Each press's grid index, weight and start (`_press_reads`, kept per plan's list).
        idx, ws, starts = _press_reads(presses, planned is not None)
        # Every press's count of strikes in time at once (the same numbers one search per press
        # gives), summed in the same order below.
        counts = np.searchsorted(arrive, my_roll - starts - ready,
                                 side='left').tolist() if punishes and presses else None
        na = len(arrive) if punishes else None
        for j in range(len(idx)):
            i = idx[j]
            inside = 0 <= i < nh
            hit = hl[i] if inside else bh
            w = ws[j] / nm
            if hit >= 0:
                # Caught in his dodge (the catch at or after its start), or before it, standing.
                if inside and tl is not None and tl[i] >= i * DODGE_STEP - 1e-9:
                    value += w * mid(hit, m, i * DODGE_STEP, tl[i])
                else:
                    value += w * hit_value[hit]
                continue
            p_evade += w
            if punishes:
                p_punish += w * (counts[j] / na)
    return {'value': value, 'base': base, 'p_evade': p_evade, 'p_punish': p_punish,
            'punish_hp': p_punish * PUNISH_HP}


def sequence_outcome(bursts, dist, cue=0.0, floor=0.0, families=None, my_roll=None, strikes=None,
                     run=False, catches=None, caught=None, hp=None, plan=None, run_floor=None,
                     advance=None):
    """`reaction_outcome` for an attack whose hits come in separate bursts (`bursts`: [(geoms,
    hit_value)] in time order), each dodged on its own (`dodge_chain`, version 26). Caught by a
    burst he takes what its `hit_value` says; evading it he meets the next one on the roll that
    evaded it: from where it carries him, no sooner than its start plus its dodge cancel, and caught
    in it when the next burst touches him outside its invincibility before he can dodge again (a
    burst that does not touch him where he is is not dodged and changes nothing). Until version 26
    each later burst put him back at the distance, free from the end of the last burst he dodged
    plus his move's recovery past its invincibility (`INFERRED` then). He keeps to one family of
    moves (`families`, default `dodge_families`: each roll direction, and the jump when a hit is
    `AIR`) and, knowing the attack, takes the one that leaves the attacker least (`value` -
    `punish_hp`), or stands when that leaves less still (`stand_or`). The punish is the last
    escaping press's, from where it started, against the whole attack's root motion (`advance`,
    None: `attack_advance` of every burst's geometries). `catches`, a dict, hands `dodge_chain` one
    memo per (family index, burst index).

    `hit_value` credits a caught defender with every later hit, as he never left. With `caught`
    (a `CaughtModel` over the same geometries and the worth they carry: `caught.worth(dist)`, one
    number per geometry in burst order) each later hit and tick is worth what lands on him while
    he takes his hit reactions and then leaves (`CaughtModel`), his whiff's recovery holding him to
    `floor` for a roll and `run_floor` (his move cancel; None: `floor`) for a run, and a catch in a
    dodge of his (or in the roll he is still on) played from that dodge (`_LazyWorth.mid`). `hp`
    caps what one caught defender takes at his health (the punish is not capped).

    `plan` (`reaction_outcome`'s) times his dodge of the first burst that reaches him standing, in
    each family; every later burst is dodged as before."""
    if families is None:
        families = dodge_families(any(g is not None and g.get('AIR') for gs, _ in bursts for g in gs))
    if advance is None:
        advance = attack_advance([g for gs, _ in bursts for g in gs])
    if caught is not None or hp is not None:
        out, at = [], 0
        for geoms, hit_value in bursts:
            if caught is not None:
                # Each catch is played only when it is read, held by the situation's floors (a
                # catch after a dodge of his is played from that dodge, `mid`).
                hv = caught.lazy(dist, at, len(geoms), hp, (floor, floor if run_floor is None else run_floor))
            else:
                hv = [hp_cap(v, hp) for v in hit_value]
            at += len(geoms)
            out.append((geoms, hv))
        bursts = out
    last = last_reaching(bursts, dist, catches)
    geoms_only = [gs for gs, _ in bursts]
    best = None
    for fi, moves in enumerate(families):
        memo = None
        if catches is not None:
            # One memo per (family, burst), as `reaction_outcome` kept them (`last_reaching` reads
            # family 0's standing reads).
            memo = _ChainMemo(catches, fi)
        ch = dodge_chain(geoms_only, dist, moves, floor, cue, my_roll, strikes, run, advance, memo, plan, last)
        value = 0.0
        for bi, h, play, wt in ch['events']:
            hv = bursts[bi][1]
            mid = getattr(hv, 'mid', None)
            if play is None or mid is None:
                value += wt * hv[h]
            else:
                value += wt * mid(h, *play)
        base = bursts[ch['first']][1][ch['first_h']] if ch['first'] is not None else 0.0
        punish = ch['p_punish']
        out = {'value': value, 'base': base, 'p_evade': ch['p_evade'] if base else 0.0, 'p_punish': punish,
               'punish_hp': punish * PUNISH_HP, 'moves': moves[0]['anim']}
        if best is None or out['value'] - out['punish_hp'] < best['value'] - best['punish_hp']:
            best = out
    return stand_or(best)


class _ChainMemo(dict):
    """`dodge_chain`'s per-burst memos ({burst index: memo}) kept in a caller's dict of
    (family index, burst index) memos (`sequence_outcome`'s `catches`, the layout
    `reaction_outcome` and `net_parts`' first touch read)."""

    def __init__(self, catches, fi):
        super().__init__()
        self.catches, self.fi = catches, fi

    def setdefault(self, bi, default=None):
        return self.catches.setdefault((self.fi, bi), {} if default is None else default)


def last_reaching(bursts, dist, catches=None):
    """The index in `bursts` ([(geoms, hit_value)]) of the last burst that reaches a defender
    standing at `dist` (its first catch worth above 0, `reaction_outcome`'s `base`), None for none:
    the burst whose dodge decides his punish (`sequence_outcome` keeps the last reaching burst's),
    so the only one he aims for it (`reaction_outcome` `aim_punish`, version 25). `catches` as
    `sequence_outcome`'s: the standing read is kept under family 0's memo, which every family reads."""
    out = None
    for bi, (geoms, hit_value) in enumerate(bursts):
        if all(g is None for g in geoms):
            continue
        memo = None if catches is None else catches.setdefault((0, bi), {})
        if memo is not None and ('base', dist) in memo:
            got = memo[('base', dist)]
        else:
            got = _catches(geoms, dist, None, [0.0])
            if memo is not None:
                memo[('base', dist)] = got
        bh = int(got[0][0])
        if bh >= 0 and hit_value[bh] > 0:
            out = bi
    return out


def stand_or(best):
    """`best` (an outcome dict: the evasion family the defender picks), or standing where that
    leaves the attacker less: he takes the attack as one who never dodges does (`base`: the first
    reaching catch, played out by `CaughtModel` when there is one) and is never punished. The same
    choice `er-mechanics-spells.CastThreats.dynamic_outcome`, `volley_outcome`,
    `FollowupModel.mixup`'s 'stand' row and `er-mechanics-attack-similarity.OutcomeForm` give him
    (version 23; `sequence_outcome` and `volley_outcome` had no stand). A tie keeps the evasion."""
    if best is not None and best['value'] - best['punish_hp'] > best['base']:
        return {'value': best['base'], 'base': best['base'], 'p_evade': 0.0, 'p_punish': 0.0, 'punish_hp': 0.0,
                'moves': None}
    return best


def burst_after(bursts, bi, cue=0.0, floor=-math.inf):
    """The earliest real frame burst `bi` of `bursts` ([geoms]) reads where a defender free from
    `floor` is: its first sample, or launch (`TL`), or his first press, no sooner than his reaction to
    its cue (`burst_cue` plus his shortest reaction and earliest timing error, `_SOONEST_PRESS`) nor
    than `floor`. A track
    whose clips are over by then reads as one segment (`collapse_track`)."""
    gs = [g for g in bursts[bi] if g is not None]
    # His first press, read where the outcomes read a press: on the update grid (`round(s / step)`).
    lo = _grid(max(burst_cue(gs, cue) + _SOONEST_PRESS, floor))
    for g in gs:
        lo = min(lo, float(g['T'].min()))
        if 'TL' in g:
            lo = min(lo, float(g['TL'].min()))
    return lo


class _Ahead:
    """Where the attacker's root is up the axis at a real frame, against a defender whose place
    along it is `dist` + z: his root motion so far over `geoms` (`attack_advance` up to then),
    stopped by the defender's body (`advance_at`); what `oriented` turns a roll out of a track to
    face. Kept per frame."""

    def __init__(self, geoms, dist):
        self.geoms, self.dist, self.kept = [g for g in geoms if g is not None], float(dist), {}

    def __call__(self, t, z):
        k = float(t)
        if k not in self.kept:
            self.kept[k] = attack_advance(self.geoms, upto=k)
        return advance_at(self.kept[k], self.dist, float(z))


def dodge_chain(bursts, dist, moves, floor=0.0, cue=0.0, my_roll=None, strikes=None, run=False, advance=None,
                catches=None, plan=None, aim=None, units=None):
    """The worth-free reads of a defender who dodges every burst of `bursts` ([geoms] in time order,
    a geometry None where it has no samples) with the moves `moves` (one family, each taken
    equally), version 26 (the comment above `STILL`).

    He starts standing at `dist`, free from `floor`. Each burst that touches him where he is, if he
    does not dodge it (his track continued), he dodges as `reaction_outcome` does: his presses from
    `burst_cue` plus his reaction, no sooner than his floor, aimed by `dodge_presses` for escaping
    (and, on burst `aim`, for his punish, `press_gain`), or `plan`'s for his first dodge
    (`reaction_outcome` `plan`; `gains` and `punish` with it when that burst is `aim`). A press that
    escapes leaves him on that roll from its start on the update grid, from where his track had him
    then, free again from its start plus its dodge cancel (`dodge_end`), on its whole root motion
    until he next presses (version 27, `track_extend`); one that is caught ends him there. A burst
    that does not touch him where he is is not dodged and changes nothing. A roll out of a track is
    taken facing the attacker (`oriented`), and every sample is read in the facing the attacker had
    when he made it, tracking the defender under his clip's turn rules (`_facing`, version 27:
    until then the attacker turned fully to face him before each later burst and not within one).
    His punish is the last escaping press's, from where it started (`punish_lead` `origin`), against
    the attacker carried `advance` forward ((peak, end), `attack_advance` of the whole attack; None:
    these bursts'), with `run` as `reaction_outcome`'s.

    Returns {'events': [(burst index, geometry index, play, weight)], 'p_evade': the chance he is
    never caught, 'p_punish', 'first': the first burst that touches him standing, 'first_h' its
    catching geometry}. A play is None for a catch before any dodge of his (he stands where he
    started, held by the situation's floors, as `reaction_outcome`'s plain `hit_value`), else (move,
    its start, the catch frame): caught in his own dodge of that move, carried (`carried`), or in the
    track he is still on (`still_move`), `CaughtModel.worth_of` `dodge`. A defender who never left
    the spot he started from reads every burst exactly as `reaction_outcome` reads it.

    `catches`, a dict per burst index ({bi: memo}), keeps the reads: the root's under
    `reaction_outcome`'s keys, a carried state's under its move's `anim`.

    `units` (version 27: `volley_outcome` on this chain), per burst the threat each geometry belongs
    to: then `_volley_chain`'s states are returned instead of events."""
    import numpy as np
    if advance is None:
        advance = attack_advance([g for gs in bursts for g in gs])
    if units is not None:
        return _volley_chain(bursts, units, dist, moves, floor, cue, my_roll, strikes, run, advance, catches, plan,
                             aim)
    ordered = None if strikes is None else _ordered(strikes)
    punish_on = _punish_ctx(ordered, my_roll, run)
    nm = len(moves)
    ahead = _Ahead([g for gs in bursts for g in gs], dist)
    # States: track key -> [track, floor, mass, punish mass]. The root has the empty track.
    states = {(): [(), float(floor), 1.0, 0.0]}
    events, first, first_h = [], None, -1
    for bi, gs in enumerate(bursts):
        if all(g is None for g in gs):
            continue
        memo = None if catches is None else catches.setdefault(bi, {})
        if len(states) > 1 or () not in states:
            # Tracks whose clips are over by the time this burst reads him are where they left him.
            merged = {}
            for tk, (trk, fl, ms, pm) in states.items():
                at = burst_after(bursts, bi, cue, fl)
                trk2 = collapse_track(trk, at) if at >= fl else trk
                k2 = track_key(trk2)
                if k2 in merged:
                    merged[k2][2] += ms
                    merged[k2][3] += pm
                else:
                    merged[k2] = [trk2, fl, ms, pm]
            states = merged

        def read(key, move, starts):
            if memo is None:
                return _catches(gs, dist, move, starts() if callable(starts) else starts)
            if key not in memo:
                memo[key] = _catches(gs, dist, move, starts() if callable(starts) else starts)
            return memo[key]

        def grid():
            return np.arange(0.0, max(g['T'].max() for g in gs if g is not None) + 1.0, DODGE_STEP)
        cue_b = burst_cue(gs, cue)
        nxt = {}

        def put(trk, fl, ms, pm):
            k = track_key(trk)
            if k in nxt:
                nxt[k][2] += ms
                nxt[k][3] += pm
            else:
                nxt[k] = [trk, fl, ms, pm]
        for tk, (trk, fl, mass, pmass) in states.items():
            root = not trk
            if root:
                sh, st = read(('base', dist), None, [0.0])
                smove, sstart = None, 0.0
            else:
                smove, sstart = still_move(trk)
                sh, st = read(('base', dist, smove['anim']), smove, [sstart])
            bh, btc = int(sh[0]), float(st[0])
            if bh < 0:
                put(trk, fl, mass, pmass)
                continue
            if root and first is None:
                first, first_h = bi, bh
            # Each roll out of the track taken facing the attacker (`oriented`, version 27).
            oms = [oriented(m, trk, dist, ahead) for m in moves]
            mvs = [carried(m, trk) for m in oms]
            keys = [(dist, k) if root else (dist, k, mv['anim']) for k, mv in enumerate(mvs)]
            got = [read(keys[k], mv, grid) for k, mv in enumerate(mvs)]
            gains = [press_gain(m, trk, dist, advance, run, ordered, my_roll) for m in oms] \
                if punish_on and bi == aim else [None] * nm
            planned = None
            if plan is not None and root:
                planned = _plan_call(plan, moves, [h < 0 for h, _ in got], cue_b, fl, gains,
                                     punish_on if bi == aim else None)
            for k, m in enumerate(oms):
                h, tt = got[k]
                hl = None if memo is None else memo.get(('list',) + keys[k])
                if hl is None:
                    hl = (h.tolist(), tt.tolist())
                    if memo is not None:
                        memo[('list',) + keys[k]] = hl
                hl, tl = hl
                nh = len(hl)
                presses = planned[k] if planned is not None else dodge_presses(h < 0, cue_b, floor=fl, gain=gains[k])
                idx, ws, starts = _press_reads(presses, planned is not None)
                # His punish from each escaping press (the gain he aimed by on burst `aim`, read
                # alike on every burst: only the last escaping press's is kept).
                share = gains[k] if gains[k] is not None else \
                    (press_gain(m, trk, dist, advance, run, ordered, my_roll) if punish_on else None)
                # Each escaping press's share read in one call (a gain is elementwise in its
                # starts, so the same numbers as one call per press), and the track a press from
                # grid start i extends to read once per i (the same tuple of the same floats).
                shares, ext = None, {}
                if share is not None:
                    esc = [j for j in range(len(idx))
                           if (hl[idx[j]] if 0 <= idx[j] < nh else bh) < 0]
                    if esc:
                        shares = dict(zip(esc, share(starts[esc]).tolist()))
                for j in range(len(idx)):
                    i = idx[j]
                    inside = 0 <= i < nh
                    hit = hl[i] if inside else bh
                    wt = mass * ws[j] / nm
                    if hit >= 0:
                        if inside and tl[i] >= i * DODGE_STEP - 1e-9:
                            play = (mvs[k], i * DODGE_STEP, tl[i])
                        elif root:
                            play = None
                        else:
                            play = (smove, sstart, tl[i] if inside else btc)
                        events.append((bi, hit, play, wt))
                        continue
                    p = shares[j] if share is not None else 0.0
                    got_i = ext.get(i)
                    if got_i is None:
                        s0 = i * DODGE_STEP
                        trk2 = track_extend(trk, m, s0)
                        got_i = ext[i] = (trk2, track_key(trk2), s0 + dodge_end(m))
                    trk2, k2, fl2 = got_i
                    if k2 in nxt:
                        nxt[k2][2] += wt
                        nxt[k2][3] += wt * p
                    else:
                        nxt[k2] = [trk2, fl2, wt, wt * p]
        states = nxt
    return {'events': events, 'p_evade': float(sum(s[2] for s in states.values())),
            'p_punish': float(sum(s[3] for s in states.values())), 'first': first, 'first_h': first_h}


def _catches_each(geoms, dist, move, starts):
    """`_catches` hit by hit: an array [dodge start, hit] of the real frame each hit first touches
    the defender outside his invincibility, inf where it never does."""
    import numpy as np
    radius = _pose()[1].PUSH_CAPSULE_RADIUS
    starts = np.asarray(starts, float)[:, None]
    out = np.full((starts.shape[0], len(geoms)), math.inf)
    if move is not None:
        path = np.array(move['path'], float)
        px, pz, n = path[:, 0], path[:, 1], len(path)
    for h, g in enumerate(geoms):
        if g is None:
            continue
        if 'HOMING' in g:
            out[:, h] = _homing_touch(g, dist, move, starts[:, 0])
            continue
        if move is not None:
            out[:, h] = _geo_first_touch(g, dist, move, starts, px, pz, n, radius)
            continue
        u = g['T'][None, :] - starts
        xr, d, vuln = 0.0, np.full(u.shape, float(dist)), np.ones(u.shape, bool)
        held = _held(g, dist, move, u, starts, 1, None, radius)
        if held is None:
            held = np.maximum(0.0, g['PK'][None, :] - (d - 2 * radius))
        touch = vuln & (np.hypot(g['X'][None, :] - xr, g['Z'][None, :] + held + d) < g['R'][None, :])
        out[:, h] = np.where(touch, g['T'][None, :], math.inf).min(axis=1)
    return out


def _volley_chain(bursts, units, dist, moves, floor, cue, my_roll, strikes, run, advance, catches, plan, aim):
    """`dodge_chain` for a volley (`units[bi][j]` the threat geometry j of burst bi belongs to;
    version 27): the same chain of rolls, read per threat. A state is where his rolls carried him
    (track, floor) and which threats have landed so far; a threat lands once (its own hit list), so
    a threat that has landed is no longer dodged, and only a burst whose threats still to land touch
    him where he is is dodged. He presses as `dodge_chain` has him press, to escape every threat of
    the burst still to land (`dodge_presses` from `burst_cue` and his floor, aimed for his punish
    on burst `aim`; `plan` for his first dodge); a press caught by some of them adds them to what
    landed and carries him on as one that escapes does (a threat that lands neither stops him nor
    makes the others land, `INFERRED` as before). His punish is the last escaping press's, lost when
    a later press is caught, and counted only where nothing landed. Returns {'states': [(landed
    threats as a frozenset, mass)], 'p_evade': the mass none landed on, 'p_punish', 'first' (the
    first burst that touches him standing, None for none)}. Until version 27 `volley_outcome`
    summed each burst's chances over his states and chained the bursts as independent (`INFERRED`
    then), which is no longer read. `catches` as `dodge_chain`'s ({bi: memo}), the keys prefixed
    'each' (the reads are `_catches_each`')."""
    import numpy as np
    ordered = None if strikes is None else _ordered(strikes)
    punish_on = _punish_ctx(ordered, my_roll, run)
    nm = len(moves)
    ahead = _Ahead([g for gs in bursts for g in gs], dist)
    # States: (track key, landed) -> [track, floor, mass, punish mass, landed].
    states = {((), frozenset()): [(), float(floor), 1.0, 0.0, frozenset()]}
    first = None
    for bi, gs in enumerate(bursts):
        if all(g is None for g in gs):
            continue
        memo = None if catches is None else catches.setdefault(bi, {})
        us = units[bi]
        if any(s_[0] for s_ in states.values()):
            merged = {}
            for trk, fl, ms, pm, ld in states.values():
                at = burst_after(bursts, bi, cue, fl)
                trk2 = collapse_track(trk, at) if at >= fl else trk
                k2 = (track_key(trk2), ld)
                if k2 in merged:
                    merged[k2][2] += ms
                    merged[k2][3] += pm
                else:
                    merged[k2] = [trk2, fl, ms, pm, ld]
            states = merged

        def each(key, move, starts):
            if memo is None:
                return _catches_each(gs, dist, move, starts() if callable(starts) else starts)
            if ('each',) + key not in memo:
                memo[('each',) + key] = _catches_each(gs, dist, move, starts() if callable(starts) else starts)
            return memo[('each',) + key]

        def grid():
            return np.arange(0.0, max(g['T'].max() for g in gs if g is not None) + 1.0, DODGE_STEP)
        cue_b = burst_cue(gs, cue)
        nxt = {}

        def put(trk, fl, ms, pm, ld):
            k_ = (track_key(trk), ld)
            if k_ in nxt:
                nxt[k_][2] += ms
                nxt[k_][3] += pm
            else:
                nxt[k_] = [trk, fl, ms, pm, ld]
        for trk, fl, mass, pmass, ld in states.values():
            root = not trk
            # The threats of this burst still to land, per geometry.
            live = np.array([g is not None and us[j] not in ld for j, g in enumerate(gs)], bool)
            if root:
                still = np.isfinite(each(('base', dist), None, [0.0])[0])
            else:
                smove, sstart = still_move(trk)
                still = np.isfinite(each(('base', dist, smove['anim']), smove, [sstart])[0])
            still = still & live
            if not still.any():
                put(trk, fl, mass, pmass, ld)
                continue
            if root and first is None:
                first = bi
            oms = [oriented(m, trk, dist, ahead) for m in moves]
            mvs = [carried(m, trk) for m in oms]
            reads = [np.isfinite(each((dist, k) if root else (dist, k, mv['anim']), mv, grid)) & live[None, :]
                     for k, mv in enumerate(mvs)]
            gains = [press_gain(m, trk, dist, advance, run, ordered, my_roll) for m in oms] \
                if punish_on and bi == aim else [None] * nm
            planned = None
            if plan is not None and root:
                planned = _plan_call(plan, moves, [~c.any(axis=1) for c in reads], cue_b, fl, gains,
                                     punish_on if bi == aim else None)
            for k, m in enumerate(oms):
                caught = reads[k]
                hit_any = caught.any(axis=1)
                share = gains[k] if gains[k] is not None else \
                    (press_gain(m, trk, dist, advance, run, ordered, my_roll) if punish_on else None)
                who = {}
                presses = planned[k] if planned is not None else dodge_presses(~hit_any, cue_b, floor=fl,
                                                                               gain=gains[k])
                for s, w in presses:
                    i0 = int(round(s / DODGE_STEP))
                    i = i0 if 0 <= i0 < len(caught) else -1
                    wt = mass * w / nm
                    s0 = i0 * DODGE_STEP
                    t2, f2 = track_extend(trk, m, s0), s0 + dodge_end(m)
                    if hit_any[i] if i >= 0 else True:
                        if i not in who:
                            who[i] = frozenset(us[j] for j in np.nonzero(caught[i] if i >= 0 else still)[0])
                        put(t2, f2, wt, 0.0, ld | who[i])
                        continue
                    sh = float(share(np.array([s]))[0]) if share is not None else 0.0
                    put(t2, f2, wt, wt * sh, ld)
        states = nxt
    return {'states': [(s_[4], s_[2]) for s_ in states.values()],
            'p_evade': float(sum(s_[2] for s_ in states.values() if not s_[4])),
            'p_punish': float(sum(s_[3] for s_ in states.values() if not s_[4])), 'first': first}


def volley_outcome(bursts, worth, dist, cue=0.0, floor=0.0, families=None, my_roll=None, strikes=None,
                   run=False, catches=None, hp=None, plan=None, advance=None):
    """`sequence_outcome` for an attack made of independent threats: the shots of a fan that keep
    their own hit lists (Glintblade Phalanx's blades, Ancient Death Rancor's skulls). `bursts` is
    [(geoms, units)] in time order, `units[i]` the threat `geoms[i]` belongs to (one threat can fly
    in several bursts, one leg in each), `worth[unit]` what it lands, once. The bursts are chained as
    `dodge_chain` chains them (version 27, `_volley_chain`): each press carries him on its roll, a
    burst whose threats still to land touch him where he is is dodged (no sooner than his last
    roll's start plus its dodge cancel), and a threat lands the first time one of its legs touches
    him; being caught by one threat neither makes the others land nor stops him dodging them, so
    every press carries him on, caught or not (`INFERRED`). A shot is flown again at where his roll
    has him when it homes or is aimed (`_homing_touch`); the stored samples of one that is neither
    keep its aim at where he stood, read in the facing the attacker had at its launch (`_facing`;
    the chain that re-aims after his roll is the spell kit's `fly_dynamic`). The chain carries which
    threats landed together, so `value` is each such set's worth times its chance, and `hp` caps
    each set at the defender's health exactly (until version 27 the bursts' chances were chained as
    independent and the cap was a bound when the worths summed past his health). `base` is what
    lands on a defender who never dodges; `p_evade` is the chance nothing lands, and the punish is
    the last escaping press's where nothing landed, so it is paid once per cast. Knowing the attack
    he keeps to the family of moves that leaves the attacker least, or stands when that leaves less
    still (`stand_or`). `catches`, a dict, memoises one `_catches_each` read per (family, burst,
    dodge). `plan` and `advance` as `sequence_outcome`'s: `plan` times the dodge of the first burst
    that reaches him standing."""
    import numpy as np
    if families is None:
        families = dodge_families(any(g is not None and g.get('AIR') for gs, _ in bursts for g in gs))
    if advance is None:
        advance = attack_advance([g for gs, _ in bursts for g in gs])
    # The last burst that touches him standing decides his punish, so it is the one he aims for it
    # (`dodge_chain` `aim`, version 25); the standing read is family 0's memo's.
    last = None
    # What lands on a defender who never dodges: every threat a burst touches him with standing
    # where he started (version 26: read here, since the carried states leave that spot).
    base, reached = 0.0, set()
    for bi, (geoms, units) in enumerate(bursts):
        if all(g is None for g in geoms):
            continue
        memo = None if catches is None else catches.setdefault((0, bi), {})
        if memo is not None and ('each', 'base', dist) in memo:
            got = memo[('each', 'base', dist)]
        else:
            got = _catches_each(geoms, dist, None, [0.0])
            if memo is not None:
                memo[('each', 'base', dist)] = got
        if np.isfinite(got[0]).any():
            last = bi
            for u in sorted({units[j] for j in np.nonzero(np.isfinite(got[0]))[0]} - reached):
                reached.add(u)
                base += worth[u]
    best = None
    gbursts = [gs for gs, _ in bursts]
    ubursts = [us for _, us in bursts]
    base_f = base if hp is None else hp_cap(base, hp)
    for fi, moves in enumerate(families):
        ch = dodge_chain(gbursts, dist, moves, floor, cue, my_roll, strikes, run, advance,
                         None if catches is None else _ChainMemo(catches, fi), plan, last, units=ubursts)
        # Each set of threats that landed together, its worth capped at his health (`hp_cap`).
        sets = {}
        for ld, ms in ch['states']:
            sets[ld] = sets.get(ld, 0.0) + ms
        value = 0.0
        for ld in sorted(sets, key=lambda s_: sorted(map(repr, s_))):
            if ld:
                v = sum(worth[u] for u in sorted(ld, key=repr))
                value += sets[ld] * (v if hp is None else hp_cap(v, hp))
        punish = ch['p_punish']
        out = {'value': value, 'base': base_f, 'p_evade': ch['p_evade'] if base_f else 0.0, 'p_punish': punish,
               'punish_hp': punish * PUNISH_HP, 'moves': moves[0]['anim']}
        if best is None or out['value'] - out['punish_hp'] < best['value'] - best['punish_hp']:
            best = out
    return stand_or(best)


# ---------------------------------------------------------------------------------------------
# a follow-up pressed late (bd er-effects-rs-me5p)
#
# User, 2026-11-10: "you can delay your followup slightly if you think your opponent will attempt
# to roll a projectile from the first part". `sequence_outcome` cannot show that: a burst the
# defender evaded holds his next dodge back to the burst's last live frame plus his roll's recovery
# (`INFERRED` there), not to his own roll, so where a follow-up lands against his roll does not
# depend on when he rolled. `FollowupModel` plays the opening as `sequence_outcome` does and then
# meets the follow-up with the roll he actually made:
#   * the press. `skill_followups`' window is where the follow-up's SpEffect, input and cancel
#     windows overlap; a press inside both of the latter is ready on that frame (`VERIFIED`,
#     attacks.md section 4: `CSChrActionRequestModule::UpdateFromManipulator`), so any frame of
#     [first, last) starts the follow-up (`INFERRED`: the behavior script changes state on the
#     frame the bit is ready; TAE event ends exclusive). Every follow-up hit is clip time from the
#     press (`skill_option` offsets the part by it), so a later press moves the whole follow-up
#     clip, its hits, its launches and the attacker's roll frame, by the delay.
#   * his roll. The opening's bursts are dodged as in `sequence_outcome` (timed on the opening
#     alone). A press of his on the last opening burst that reaches him standing leaves him
#     rolling from that start: the follow-up catches him when it touches him outside that roll's
#     invincibility before its dodge cancel (`_catches` on the roll's path); otherwise he is free
#     from the dodge cancel, where it had carried him (version 26, `track_extend`; until then
#     placed back at the distance), and none of the follow-up's samples before then count again
#     (the roll judged them). His floor is taken on the 60 Hz update grid (`DODGE_STEP`).
#   * his plan. He knows the attack as pressed at once, as `sequence_outcome`'s defender knows
#     every attack (cue 0), and from that floor dodges the follow-up's bursts as it does. A press
#     made late meets his planned dodge of the follow-up's first burst that reaches him, which now
#     comes that much early: it catches him when it touches him outside that dodge's
#     invincibility before its dodge cancel; else he is free again and reacts to the press itself
#     (cue = the press, `skill_option`'s part cue). If nothing reaches him after his last dodge,
#     he punishes from it against the attacker's roll frame, which a late press makes later.
#   * the attacker's choice. He presses at once unless the roll showed on his machine at least his
#     own reaction (`attacker_reactions`) before the window's first frame; then he presses at the
#     delay that leaves him most against that press (`INFERRED`: the model's attacker knows where
#     the roll he saw puts the defender). A defender caught by an earlier opening burst, or who
#     does not roll the opening (no opening burst reaches him standing), gets the press at once.
#     So a defender who stays meets the earliest press, and the policy never does worse than it.
# `INFERRED` too: the defender plans for the press at once and reads no delay ahead; he times his
# roll of the opening on the opening alone; the opening's own samples after his roll are not tested
# again; a defender caught mid-roll is played as `CaughtModel` places him.

def attacker_reactions():
    """[(real frames from the defender's dodge showing on the attacker's machine to the attacker's
    press, weight)]: `REACTION_POINTS` quantiles of `REACTION_MEDIAN_S` / `REACTION_LOG_SD`
    (`INFERRED`: the defender's reaction taken as the attacker's) and no network leg, since the
    dodge start `dodge_presses` gives is already on the attacker's machine (`reaction_delays`)
    and his own press is local (`INFERRED`, as there)."""
    out = []
    for k in range(REACTION_POINTS):
        z = _ND.inv_cdf((k + 0.5) / REACTION_POINTS)
        out.append((REACTION_MEDIAN_S * math.exp(REACTION_LOG_SD * z) * TAE_FPS, 1.0 / REACTION_POINTS))
    return out


def _burst_presses(geoms, dist, moves, cue, floor, memo, plan=None, gains=None, punish=None, vals=None, track=()):
    """`reaction_outcome`'s reads of one burst without its worth: (index in `geoms` of the geometry
    that catches a defender at `dist` who does not dodge, or -1; [(move index, dodge start, weight,
    index of the geometry that catches that dodge or -1, play)]). `memo` as `reaction_outcome`'s
    `catches`. `geoms` may hold None (no samples), which keeps the others' indices. The presses are
    read from `burst_cue`, or are `plan`'s (`reaction_outcome`'s: a lookalike group's) when given; a
    caught press's play is (move, the dodge's start on the grid, the catch frame) when it is caught
    in that dodge (version 25), which `FollowupModel` plays from that dodge (`reaction_outcome`'s
    `mid`), else None. `gains` (per move a `PressGain`) and `punish` (`_punish_ctx`) when this burst
    decides his punish, as `reaction_outcome` hands them with `aim_punish`. `vals(k, grid,
    escaping)` (None: 1 each) gives, on move k, (the starts where a gain is counted, what escaping
    from each start leaves him, the gain): `dodge_presses`' `ok`, `val` and `gain`; a plan, which
    reads no follow-up, ignores it.

    `track` (version 26, `dodge_chain`'s rule): where earlier rolls carried him. He does not dodge
    is that track continued (`still_move`), each dodge starts from where it has him (`carried`;
    the plays then name the carried move), and a press caught before its dodge starts is caught in
    the track: its play is (the still move, its start, the catch frame). The reads are kept under
    the moves' `anim`s (`memo` keys ('base', dist, anim) and (dist, k, anim))."""
    import numpy as np
    cue = burst_cue(geoms, cue)

    def caught(key, move, starts):
        if key not in memo:
            memo[key] = _catches(geoms, dist, move, starts)
        return memo[key]
    if track:
        smove, sstart = still_move(track)
        sh, st = caught(('base', dist, smove['anim']), smove, [sstart])
    else:
        smove, sstart = None, 0.0
        sh, st = caught(('base', dist), None, [0.0])
    base_h, btc = int(sh[0]), float(st[0])
    live = [g['T'].max() for g in geoms if g is not None]
    if not live or base_h < 0:
        return base_h, []
    grid = np.arange(0.0, max(live) + 1.0, DODGE_STEP)
    mvs = [carried(m, track) for m in moves]
    keys = [(dist, k) if not track else (dist, k, mv['anim']) for k, mv in enumerate(mvs)]
    # A carried move's homing shots, every move's flown together (`_homing_ready`).
    ready = _homing_ready(dist, [(geoms, mv, grid) for k, mv in enumerate(mvs) if keys[k] not in memo]) \
        if track else []
    hs = [caught(keys[k], mv, grid)[0] for k, mv in enumerate(mvs)]
    _homing_forget(ready)
    gains = [None] * len(moves) if gains is None else gains
    planned = _plan_call(plan, moves, [h < 0 for h in hs], cue, floor, gains if punish is not None else None,
                         punish) if plan is not None else None
    out = []
    for k, m in enumerate(moves):
        h = hs[k]
        if planned is None:
            if vals is not None:
                okg, val, gk = vals(k, grid, h < 0)
                presses = dodge_presses(okg, cue, floor=floor, gain=gk, val=val)
            else:
                presses = dodge_presses(h < 0, cue, floor=floor, gain=gains[k] if punish is not None else None)
        else:
            presses = planned[k]
        tk = caught(keys[k], mvs[k], grid)[1]
        for s, w in presses:
            i = int(round(s / DODGE_STEP))
            inside = 0 <= i < len(h)
            hit = int(h[i]) if inside else base_h
            play = None
            if hit >= 0:
                if inside and tk[i] >= i * DODGE_STEP - 1e-9:
                    # A catch at or after the dodge's start is made in it.
                    play = (mvs[k], i * DODGE_STEP, float(tk[i]))
                elif track:
                    # Before it, in the track he is still on.
                    play = (smove, sstart, float(tk[i]) if inside else btc)
            out.append((k, s, w / len(moves), hit, play))
    return base_h, out


#: {(category, anim): (peak, end)} of `clip_advance`.
_CLIP_ADVANCE = {}


def clip_advance(category, anim):
    """(peak, end) metres of the attacker's forward root motion (-Z) over the whole HKX clip TAE
    entry (`category`, `anim`) plays: its largest at any 1/60 s sample, and where it is at the
    clip's end; None when the clip is not read. What a geometry of that clip carries as `ADV`
    (`_hit_contacts`), the root motion past its last hit sample that `PK` does not see. Cached."""
    key = (category, anim)
    if key not in _CLIP_ADVANCE:
        got = None
        try:
            clip = _pose()[0].load_animation(category, anim)
            n = max(1, math.ceil(clip.duration * 60))
            fwd = [-clip.root_motion(clip.duration * k / n)[2] for k in range(n + 1)]
            got = (max(0.0, max(fwd)), fwd[-1])
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            got = None
        _CLIP_ADVANCE[key] = got
    return _CLIP_ADVANCE[key]


def attack_advance(geoms, upto=None):
    """(peak, end) metres the attacker's root motion carries him forward over an attack of
    contact geometries `geoms`, before the defender's body stops it (`advance_at`): `peak` the
    largest of its samples' `PK` (his forward root motion so far at each hit sample) and of its
    clips' `ADV` peaks (the whole clip, `clip_advance`), `end` where the last clip played leaves
    him (its `ADV` end; `peak` when no geometry carries one). With `upto` (a real frame), only the
    samples at or before it, `PK` alone (where he is by then). (0, 0) when nothing says he moves:
    a bullet's samples carry no `PK` unless its launch does. The one rule every punish path reads
    (`punish_lead`, `strike_segments`)."""
    import numpy as np
    peak, end, last = 0.0, None, -math.inf
    for g in geoms or ():
        if g is None:
            continue
        pk = g['PK'] if upto is None else g['PK'][g['T'] <= upto]
        pk = pk[np.isfinite(pk)]
        if len(pk):
            peak = max(peak, float(pk.max()))
        adv = g.get('ADV') if upto is None else None
        if adv is not None:
            peak = max(peak, float(adv[0]))
            if float(g['T'].max()) >= last:
                last, end = float(g['T'].max()), float(adv[1])
    return peak, (peak if end is None else min(end, peak))


def advance_at(advance, dist, z=0.0):
    """How far forward the attacker's root ends, his `advance` ((peak, end), `attack_advance`, or
    one number for both) stopped by the defender's body as every hit is (`_held`): the two push
    capsules keep his root `2 * PUSH_CAPSULE_RADIUS` short of the defender's place on his axis,
    `dist + z` (`z` the dodge's travel along it), and what the clip would carry him past that is
    taken off for good, so a clip that steps back steps back from there. The axis alone, as the
    hits' hold-back reads it: a defender who rolled aside does not stop him (`INFERRED`, as there)."""
    peak, end = (advance, advance) if not isinstance(advance, tuple) else advance
    if peak <= 0.0:
        return end
    lim = max(0.0, dist + z - 2 * _pose()[1].PUSH_CAPSULE_RADIUS)
    return end - max(0.0, peak - lim)


def punish_lead(m, dist, advance=0.0, run=True, origin=None):
    """(frame of dodge `m` he presses his strike from, the real frames his run in adds, whether he
    strikes at all), for a dodger who evaded an attacker `dist` m ahead whose root motion carried
    him `advance` forward ((peak, end) m, `attack_advance`, or one number; stopped by the
    defender's body where he is then, `advance_at`). `origin` (x, z), version 26: the dodge starts
    that far sideways and back from where he first stood (`track_at`, an earlier roll carried him
    there), and both his place and the attacker's advance stopped by his body are read from there
    on the attack's axis. Within `PUNISH_REACH_M` of the attacker at the dodge's R1 cancel
    (`ready`) he presses there. Farther, with `run`, he runs the rest in at run speed
    (`er-mechanics-neutral.frames_per_metre`) from where the dodge has put him at its move cancel
    (`move`): no sooner, since the behavior script starts a walk or a run only through the
    move-cancel check (env 2000, `VERIFIED`); the medium roll's is f22 in all four directions
    against its R1 at f20, Bloodhound's Step's f29 against f17 (`MEASURED` from the clips' TAE,
    `ATK.recovery_windows`). A dodge that carries him into reach on a frame between the two
    presses there, the dodge's own travel (`MEASURED` root motion: the step goes on 1.2 m after
    its R1 opens). Without `run` he strikes only from in reach at the R1 cancel. A dodge that names
    no move cancel (the jump, `jump_motion`) runs from its R1 frame."""
    n = len(m['path'])
    ox, oz = (0.0, 0.0) if origin is None else (float(origin[0]), float(origin[1]))

    def gap_at(f):
        x, z = m['path'][min(int(f), n - 1)]
        x, z = ox + x, oz + z
        return math.hypot(x, dist + z - advance_at(advance, dist, z))
    ready = m['ready'] if m['ready'] is not None else n
    if gap_at(ready) <= PUNISH_REACH_M:
        return ready, 0.0, True
    if not run:
        return ready, 0.0, False
    move = m.get('move')
    move = ready if move is None else max(float(move), ready)
    for f in range(int(ready) + 1, int(move)):
        if gap_at(f) <= PUNISH_REACH_M:
            return float(f), 0.0, True
    return move, max(0.0, gap_at(move) - PUNISH_REACH_M) * _neutral().frames_per_metre(), True


def _punish_arrivals(m, dist, strikes, run, advance=0.0, origin=None):
    """(the frame of move `m` his strike is pressed from, the sorted frames the pool's strikes take
    to land from it, or None when he does not punish from there): `reaction_outcome`'s rule
    (`punish_lead`, from `origin` when earlier rolls carried him), the attacker carried `advance`
    forward (`attack_advance`)."""
    import numpy as np
    lead, close, near = punish_lead(m, dist, advance, run, origin)
    if strikes is None or not len(strikes) or not near:
        return lead, None
    return lead, np.sort(np.asarray(strikes, float) + close)


def _first_touch(geoms, dist, move, starts, after):
    """`_catches` with the samples before `after[i]` left out for dodge start `starts[i]` (the
    earlier dodge they fell in judged them): (index of the hit that first touches him outside his
    invincibility, its time) per start, -1 / inf when none does. The reference `_TouchTable` is
    checked against (selftest)."""
    import numpy as np
    starts = np.asarray(starts, float)
    after = np.asarray(after, float)[:, None]
    best_t = np.full(starts.shape[0], math.inf)
    best_h = np.full(starts.shape[0], -1)
    for h, g in enumerate(geoms):
        if g is None:
            continue
        # `_touch_rows`' test (the attacker's facing included, version 27), the samples before
        # `after` left out.
        touch = _touch_rows(g, dist, move, starts) & (g['T'][None, :] >= after - 1e-9)
        tt = np.where(touch, g['T'][None, :], math.inf).min(axis=1)
        better = tt < best_t
        best_t[better], best_h[better] = tt[better], h
    return best_h, best_t


def _touch_rows(g, dist, move, starts, ref=False):
    """[dodge start, sample]: whether sample of `g` touches him on `move` (None: standing) started at
    each of `starts`, as `_catches` tests it, element by element. With a move, `_touch_turned` reads
    it; `ref` reads every pair here instead, as until 2026-11-19 (the selftest's reference)."""
    import numpy as np
    radius = _pose()[1].PUSH_CAPSULE_RADIUS
    starts = np.asarray(starts, float)[:, None]
    if not ref and move is not None and len(g['T']):
        rot = _facing(g['TURN'], dist, move, starts[:, 0], float(np.nanmax(g['TL'] if 'TL' in g else g['T']))) \
            if g.get('TURN') is not None else None
        return _touch_turned(g, dist, move, starts[:, 0], rot, radius)
    u = g['T'][None, :] - starts
    rot = None
    if move is None:
        xr, d, vuln, n, pz = 0.0, np.full(u.shape, float(dist)), np.ones(u.shape, bool), 1, None
    else:
        path = np.array(move['path'], float)
        px, pz, n = path[:, 0], path[:, 1], len(path)
        k = np.clip(np.floor(u).astype(int), 0, n - 1)
        moving = u >= 0
        immune = not move.get('air') or bool(g.get('AIR'))
        o = _origins(move, starts)
        if o is None:
            xr = np.where(moving, px[k], 0.0)
            d = dist + np.where(moving, pz[k], 0.0)
            vuln = ~(moving & (u < move['iframes']) & immune)
        else:
            # Carried (`carried`, version 26): on the track before the start.
            tx, tz, ti = track_at(move['track'], g['T'][None, :], bool(g.get('AIR')))
            xr = np.where(moving, o[0] + px[k], tx)
            d = dist + np.where(moving, o[1] + pz[k], tz)
            vuln = ~((moving & (u < move['iframes']) & immune) | (~moving & ti))
        turn = g.get('TURN')
        if turn is not None and len(g['T']):
            rot = _facing(turn, dist, move, starts[:, 0], float(np.nanmax(g['TL'] if 'TL' in g else g['T'])))
    if rot is None:
        held = _held(g, dist, move, u, starts, n, pz, radius)
        if held is None:
            held = np.maximum(0.0, g['PK'][None, :] - (d - 2 * radius))
        return vuln & (np.hypot(g['X'][None, :] - xr, g['Z'][None, :] + held + d) < g['R'][None, :])
    # The attacker's facing (version 27): each sample read in the facing it was made in, the
    # hold-back read from where it turns him at the launch (`_geo_first_touch`'s rule).
    ji = _turn_index(g['TURN'], _turn_when(g, np.arange(len(g['T']))), rot[0].shape[1])
    th, ddx, ddz = rot[0][:, ji], rot[1][:, ji], rot[2][:, ji]
    PK = g['PK'][None, :]
    xr, d = _turn_apply(xr, d, th, PK, ddx, ddz)
    if 'TL' in g:
        ul = g['TL'][None, :] - starts
        kl = np.clip(np.floor(ul).astype(int), 0, n - 1)
        o = _origins(move, starts)
        if o is None:
            xl, dl = np.where(ul >= 0, px[kl], 0.0), dist + np.where(ul >= 0, pz[kl], 0.0)
        else:
            tl_ = track_at(move['track'], g['TL'][None, :])
            xl, dl = np.where(ul >= 0, o[0] + px[kl], tl_[0]), dist + np.where(ul >= 0, o[1] + pz[kl], tl_[1])
        dl = _turn_apply(xl, dl, th, PK, ddx, ddz)[1]
    else:
        dl = d
    held = np.maximum(0.0, PK - (dl - 2 * radius))
    return vuln & (np.hypot(g['X'][None, :] - xr, g['Z'][None, :] + held + d) < g['R'][None, :])


def _touch_turned(g, dist, move, s, rot, radius):
    """`_touch_rows` of `g` against `move` from each start of `s` (1-D), with the attacker's facing
    `rot` (`_facing`'s; None when none is read: each sample read in the facing it was made in, the
    hold-back read from where it turns him at the launch, `_geo_first_touch`'s rule):
    `_touch_rows`' table (`ref`), read once per start and group of samples that read the defender
    alike instead of once per pair.

    A pair reads of the defender only what its sample's time (`T`), launch time (`TL`), facing
    update (`_turn_index` of `_turn_when`) and `PK` give: his place and invincibility at the time,
    his place at the launch, the facing and drift at the update, all turned about the attacker's root
    `PK` up the axis (`_turn_apply`), and the hold-back from those. Samples whose four are equal (to
    the bit) are one group, read with `_touch_rows`' own operations on the group's values; each
    sample then tests its own disc (`X`, `Z`, `R`) against its group's read, the same last
    operations in the same order. numpy's elementwise functions give an element the same bits
    wherever it sits in an array (`MEASURED` 2026-11-19 for cos, sin and hypot, target/wf/blowup2),
    so every pair's answer is the one `ref` gives.

    Why (2026-11-19, target/wf/blowup2): Ghostflame Ignition then R1's set reads, for every roll
    track of its follow-up stages, a table of the R1's ghostflame (3,001 samples over f72-267, all
    launched at one frame with one `PK`, so 824 groups) over 200-500 starts; `_turn_apply` (two
    cosines and two sines a pair) was 53% of its time and the tables 90% (py-spy, p1)."""
    import numpy as np
    T, TL = g['T'], g.get('TL')
    S, N = len(s), len(T)
    if not S:
        return np.zeros((0, N), bool)
    path = np.array(move['path'], float)
    px, pz, n = path[:, 0], path[:, 1], len(path)
    ji = _turn_index(g['TURN'], _turn_when(g, np.arange(N)), rot[0].shape[1]) if rot is not None \
        else np.zeros(N, np.int64)
    PKs = g['PK']
    # The groups: samples whose time, launch time, facing update and `PK` are equal to the bit.
    key = np.ascontiguousarray(np.stack([T, T if TL is None else TL, ji.astype(float), PKs], axis=1))
    _, first, gi = np.unique(key.view(np.dtype((np.void, key.dtype.itemsize * 4))).ravel(),
                             return_index=True, return_inverse=True)
    gi = np.asarray(gi).ravel()
    Tg, jg = T[first], ji[first]
    starts = s[:, None]
    # `_touch_rows` (`ref`) on each group's values: the same operations in the same order.
    u = Tg[None, :] - starts
    k = np.clip(np.floor(u).astype(int), 0, n - 1)
    moving = u >= 0
    immune = not move.get('air') or bool(g.get('AIR'))
    o = _origins(move, starts)
    if o is None:
        xr = np.where(moving, px[k], 0.0)
        d = dist + np.where(moving, pz[k], 0.0)
        vuln = ~(moving & (u < move['iframes']) & immune)
    else:
        # Carried (`carried`, version 26): on the track before the start.
        tx, tz, ti = track_at(move['track'], Tg[None, :], bool(g.get('AIR')))
        xr = np.where(moving, o[0] + px[k], tx)
        d = dist + np.where(moving, o[1] + pz[k], tz)
        vuln = ~((moving & (u < move['iframes']) & immune) | (~moving & ti))
    PK = PKs[first][None, :]
    if rot is None:
        # No facing read (none given, or he never leaves the axis): `_touch_rows`' hold-back,
        # `_held` at the launch or his own place.
        if TL is not None:
            ul = TL[first][None, :] - starts
            kl = np.clip(np.floor(ul).astype(int), 0, n - 1)
            if o is None:
                dl = dist + np.where(ul >= 0, pz[kl], 0.0)
            else:
                dl = dist + np.where(ul >= 0, o[1] + pz[kl], track_at(move['track'], TL[first][None, :])[1])
            held = np.maximum(0.0, PK - (dl - 2 * radius))
        else:
            held = np.maximum(0.0, PK - (d - 2 * radius))
        return vuln[:, gi] & (np.hypot(g['X'][None, :] - xr[:, gi], g['Z'][None, :] + held[:, gi] + d[:, gi])
                              < g['R'][None, :])
    # `_turn_apply`'s cosine less 1 and sine, once per start and distinct update.
    uj, jinv = np.unique(jg, return_inverse=True)
    jinv = np.asarray(jinv).ravel()
    tu = rot[0][:, uj]
    c1u, snu = np.cos(tu) - 1.0, np.sin(tu)
    ddx, ddz = rot[1][:, jg], rot[2][:, jg]
    # `_turn_apply` of his place, the same operations in the same order.
    p = np.where(np.isfinite(PK), PK, 0.0)
    c1, sn = c1u[:, jinv], snu[:, jinv]
    vx, vz = xr - ddx, d - p - ddz
    xr, d = xr + (c1 * vx - sn * vz - ddx), d + (sn * vx + c1 * vz - ddz)
    if TL is not None:
        # His place at the launch, once per start and distinct (launch time, update, `PK`) of the
        # groups, then `_turn_apply`'s D.
        lk = np.ascontiguousarray(np.stack([TL[first], jg.astype(float), PKs[first]], axis=1))
        _, lf, li = np.unique(lk.view(np.dtype((np.void, lk.dtype.itemsize * 3))).ravel(),
                              return_index=True, return_inverse=True)
        li = np.asarray(li).ravel()
        TLl, jl, PKl = TL[first][lf], jg[lf], PKs[first][lf][None, :]
        ul = TLl[None, :] - starts
        kl = np.clip(np.floor(ul).astype(int), 0, n - 1)
        if o is None:
            xl, dl = np.where(ul >= 0, px[kl], 0.0), dist + np.where(ul >= 0, pz[kl], 0.0)
        else:
            tl_ = track_at(move['track'], TLl[None, :])
            xl, dl = np.where(ul >= 0, o[0] + px[kl], tl_[0]), dist + np.where(ul >= 0, o[1] + pz[kl], tl_[1])
        dxl, dzl = rot[1][:, jl], rot[2][:, jl]
        pl = np.where(np.isfinite(PKl), PKl, 0.0)
        c1l, snl = c1u[:, jinv[lf]], snu[:, jinv[lf]]
        wx, wz = xl - dxl, dl - pl - dzl
        dl = (dl + (snl * wx + c1l * wz - dzl))[:, li]
    else:
        dl = d
    held = np.maximum(0.0, PK - (dl - 2 * radius))
    # Each sample's own disc against its group's read.
    return vuln[:, gi] & (np.hypot(g['X'][None, :] - xr[:, gi], g['Z'][None, :] + held[:, gi] + d[:, gi])
                          < g['R'][None, :])


def _may_touch(g, dist, move, turned=None):
    """Per sample of `g`: False where no dodge start on `move` (None: standing) can make it touch
    him at `dist` in `_catches`' test. A bound: his root keeps to the box of the move's path and
    the standing spot, and his body's hold-back is at most what his nearest place in it gives, so
    a sample whose disc misses that box by its radius never touches; only those are left out.
    `turned` (version 27): None, or the farthest the attacker's turning moves his root
    (`_drift_max`) when his facing is read (`_facing`), which turns the defender's place about that
    root (`_turn_apply`) and keeps only his distance from it: a sample is then left out when its
    distance from the root misses every distance the box gives by its radius and that drift."""
    import numpy as np
    if turned is not None and turned is not False and move is not None:
        return _may_touch_turned(g, dist, move, float(turned))
    radius = _pose()[1].PUSH_CAPSULE_RADIUS
    if move is None:
        xs, zs = np.zeros(1), np.zeros(1)
    else:
        path = np.array(move['path'], float)
        xs, zs = np.append(path[:, 0], 0.0), np.append(path[:, 1], 0.0)
        if move.get('track'):
            # Carried (version 26): anywhere on the track, and the move from anywhere on it.
            tx = np.concatenate([[0.0]] + [ox + _path_arrays(m)[0] for _, ox, _, m in move['track']])
            tz = np.concatenate([[0.0]] + [oz + _path_arrays(m)[1] for _, _, oz, m in move['track']])
            xs = np.array([tx.min() + min(0.0, xs.min()), tx.max() + max(0.0, xs.max())])
            zs = np.array([tz.min() + min(0.0, zs.min()), tz.max() + max(0.0, zs.max())])
    dlo, dhi = dist + zs.min(), dist + zs.max()
    hmax = np.maximum(0.0, g['PK'] - (dlo - 2 * radius))
    dx = np.maximum(0.0, np.maximum(xs.min() - g['X'], g['X'] - xs.max()))
    lo_z, hi_z = g['Z'] + dlo, g['Z'] + dhi + hmax
    dz = np.where(lo_z > 0, lo_z, np.where(hi_z < 0, -hi_z, 0.0))
    return np.hypot(dx, dz) < g['R'] * (1.0 + 1e-9) + 1e-9


def _box(move):
    """(x range, z range) his root keeps to on `move` (and its track) and the spot he started from."""
    import numpy as np
    path = np.array(move['path'], float)
    xs, zs = np.append(path[:, 0], 0.0), np.append(path[:, 1], 0.0)
    if move.get('track'):
        tx = np.concatenate([[0.0]] + [ox + _path_arrays(m)[0] for _, ox, _, m in move['track']])
        tz = np.concatenate([[0.0]] + [oz + _path_arrays(m)[1] for _, _, oz, m in move['track']])
        return ((tx.min() + min(0.0, xs.min()), tx.max() + max(0.0, xs.max())),
                (tz.min() + min(0.0, zs.min()), tz.max() + max(0.0, zs.max())))
    return (xs.min(), xs.max()), (zs.min(), zs.max())


def _may_touch_turned(g, dist, move, drift=0.0):
    """`_may_touch` when the facing is read: per sample, his distance from the attacker's root (`PK`
    up the axis, finite, else his start, moved at most `drift` by the attacker's turning) over the
    box, against the sample's own distance from it under every hold-back his body can give
    (`_turn_apply` keeps the first; the hold-back moves the sample along the facing by at most his
    distance from the root plus the two push radii)."""
    import numpy as np
    radius = _pose()[1].PUSH_CAPSULE_RADIUS
    (xlo, xhi), (zlo, zhi) = _box(move)
    p = np.where(np.isfinite(g['PK']), g['PK'], 0.0)
    dlo, dhi = dist + zlo - p, dist + zhi - p
    ax = 0.0 if xlo <= 0.0 <= xhi else min(abs(xlo), abs(xhi))
    az = np.where((dlo <= 0) & (dhi >= 0), 0.0, np.minimum(np.abs(dlo), np.abs(dhi)))
    rmin = np.maximum(0.0, np.hypot(ax, az) - drift)
    rmax = np.hypot(max(abs(xlo), abs(xhi)), np.maximum(np.abs(dlo), np.abs(dhi))) + drift
    # The sample's point in his coordinates is (X, -(Z + held)); from the root, (X, -(Z + held) - p).
    hmax = np.where(np.isfinite(g['PK']), rmax + 2 * radius, 0.0)
    a0, a1 = g['Z'] + p, g['Z'] + p + hmax
    qz_lo = np.where((a0 <= 0) & (a1 >= 0), 0.0, np.minimum(np.abs(a0), np.abs(a1)))
    qz_hi = np.maximum(np.abs(a0), np.abs(a1))
    qmin, qmax = np.hypot(g['X'], qz_lo), np.hypot(g['X'], qz_hi)
    tol = g['R'] * (1.0 + 1e-9) + 1e-9
    return (qmin < rmax + tol) & (qmax > rmin - tol)


class _TouchTable:
    """Every `_catches` read of geometries `geoms` at `dist` on `move` (None: standing, which no
    start changes) for dodge starts on the update grid from `lo` to `hi`: per start, the first
    sample that touches him at or after any cut, in time order with ties to the lower geometry
    index (what `_catches` returns). Samples `_may_touch` rules out are left out. Starts past `hi`
    see every sample before them, so they read the standing table `stand`. `starts` (a move's
    only): the table's rows are those dodge starts instead of the grid from `lo` to `hi`, read by
    row (`first_at`; `_RowsTable`)."""

    def __init__(self, geoms, dist, move, lo=0.0, hi=0.0, stand=None, starts=None):
        import numpy as np
        if starts is not None:
            starts = np.asarray(starts, float)
            lo = hi = float(starts[0])
        self.lo, self.move, self.stand = float(lo), move, stand
        rows = int(round((hi - lo) / DODGE_STEP)) + 1 if move is not None else 1
        if starts is not None:
            rows = len(starts)
        if rows <= 0:
            # A grid that starts past its end (a burst whose last sample is over before he can press
            # out of the roll he is in, `_tab`): every start he can make is past it and reads `stand`
            # (`first`). Until 2026-11-17 this built a table of negative rows and stopped the run.
            self.T, self.gid, self.n = np.array([math.inf]), np.array([-1]), 0
            self.nxt = np.zeros((0, 1), np.int32)
            return
        grid = self.lo + DODGE_STEP * np.arange(rows) if move is not None else np.zeros(1)
        if starts is not None:
            grid = starts
        Ts, gids, touches = [], [], []
        for h, g in enumerate(geoms):
            if g is None:
                continue
            if 'HOMING' in g:
                # A geometry with a homing record is flown again after him from every start, as
                # `_catches` reads it (`_homing_touch`; version 27: until then the table read its
                # stored samples). Its first touch per start is one sample of the table there, so a
                # cut past it finds none of it (a touch before the cut was judged by the roll he is
                # coming out of, which read the same flight).
                tt = np.asarray(_homing_touch(g, dist, move, grid), float)
                vals = np.unique(tt[np.isfinite(tt)])
                if not len(vals):
                    continue
                touches.append(tt[:, None] == vals[None, :])
                Ts.append(vals)
                gids.append(np.full(len(vals), h))
                continue
            turned = _drift_max(_facing(g['TURN'], dist, move, grid, float(np.nanmax(g['TL'] if 'TL' in g else g['T'])))) \
                if move is not None and g.get('TURN') is not None and len(g['T']) else None
            keep = _may_touch(g, dist, move, turned)
            if not keep.any():
                continue
            sub = {k: (v[keep] if k in ('T', 'X', 'Z', 'R', 'PK', 'TL') else v) for k, v in g.items()}
            # The facing a shot with no launch read takes is its geometry's first sample's
            # (`_turn_when`), whichever samples are kept.
            sub['T0'] = float(np.nanmin(g['T']))
            touches.append(_touch_rows(sub, dist, move, grid))
            Ts.append(sub['T'])
            gids.append(np.full(int(keep.sum()), h))
        T = np.concatenate(Ts) if Ts else np.zeros(0)
        gid = np.concatenate(gids) if gids else np.zeros(0, int)
        order = np.lexsort((gid, T))
        n = len(T)
        self.T, self.gid = np.append(T[order], math.inf), np.append(gid[order], -1)
        touch = np.concatenate(touches, axis=1)[:, order] if touches else np.zeros((rows, 0), bool)
        idx = np.where(touch, np.arange(n, dtype=np.int32)[None, :], np.int32(n))
        nxt = np.minimum.accumulate(idx[:, ::-1], axis=1)[:, ::-1] if n else idx
        self.nxt = np.concatenate([nxt, np.full((rows, 1), n, np.int32)], axis=1)
        self.n = n

    def first(self, starts, cuts):
        """(index of the geometry that first touches him at or after each cut, its time) per dodge
        start, -1 / inf when none does."""
        import numpy as np
        starts = np.asarray(starts, float)
        cuts = np.broadcast_to(np.asarray(cuts, float), starts.shape)
        c = np.searchsorted(self.T[:-1], cuts - 1e-9, side='left')
        if self.move is None:
            j = self.nxt[0, c]
        else:
            r = np.rint((starts - self.lo) / DODGE_STEP).astype(int)
            if len(r) and r.min() < 0:
                raise ValueError(f'_TouchTable: a dodge start {starts.min()} before the grid at {self.lo}')
            past = r >= self.nxt.shape[0]
            if not self.nxt.shape[0]:
                return self.stand.first(starts, cuts)
            j = self.nxt[np.minimum(r, self.nxt.shape[0] - 1), c]
            if past.any():
                h, t = self.stand.first(starts[past], cuts[past])
                out_h, out_t = self.gid[j].copy(), self.T[j].copy()
                out_h[past], out_t[past] = h, t
                return out_h, out_t
        return self.gid[j], self.T[j]

    def first_at(self, rows, cuts):
        """`first` of the starts in rows `rows` of the table (a table of `starts`, `_RowsTable`)."""
        import numpy as np
        rows = np.asarray(rows, int)
        cuts = np.broadcast_to(np.asarray(cuts, float), rows.shape)
        c = np.searchsorted(self.T[:-1], cuts - 1e-9, side='left')
        j = self.nxt[rows, c]
        return self.gid[j], self.T[j]


class _RowsTable:
    """A `_TouchTable` of geometries `geoms` at `dist` on `move` over the grid `lo` .. `hi`, whose
    rows are read only when `first` asks for them: the rows one `first` asks for and are not read
    yet are read as one `_TouchTable` of those starts (`starts`; at least two rows, so the
    attacker's facing takes the many-defender path of `_turn_track` as the whole grid does).
    Until 2026-11-19 each run of consecutive rows was a table of its own, so a read of starts a
    frame or more apart (every other row) made one two-row table per start, each turning the
    attacker over the whole follow-up on its own (`_facing`, a loop over updates); Golden Land then
    R2 at frame 68 asks for 3.1 starts a read (5,404 in 1,737 reads, .worktrees/gl-stall chkrows).

    Exact: a row of a `_TouchTable` reads only its own start (every touch test, the facing and a
    homing shot's flight are per start; a flight is flown per start on its own, `_homing_entries`,
    its interval children included since 2026-11-18, `fly_members`), and `first` answers a row with the first of its own touches, in (time, geometry) order, at or
    after the cut. The samples `_may_touch` leaves out are those that touch on no row it reads, so
    a table over fewer rows may leave out more of them, none that a row of it touches.

    Why: the whole follow-up's table of a carried move (`FollowupModel._tab` with `bi` None) is read
    by `_rolled` alone, at the few starts of one roll on each press delay's clock, while the whole
    table read every start from before the longest delay to past the follow-up's last frame: in
    Ghostflame Ignition then R2's set, 176,368 rows read for 7,042 asked (2026-11-18,
    target/wf/goldenland/ghostBase)."""

    def __init__(self, geoms, dist, move, lo, hi, stand):
        self.geoms, self.dist, self.lo, self.move, self.stand = geoms, dist, float(lo), move, stand
        self.rows = int(round((hi - lo) / DODGE_STEP)) + 1
        #: {row: (the `_TouchTable` that read it, its row there)}.
        self.at = {}

    def _rows_read(self, need):
        """The rows a read of rows `need` (sorted) reads: those, two at least (the facing's
        one-defender path is another sum of the same steps)."""
        if len(need) > 1:
            return list(need)
        a = need[0]
        return [a, a + 1] if a + 1 < self.rows else [a - 1, a]

    def _starts_of(self, rows):
        """The dodge starts of grid rows `rows`."""
        import numpy as np
        return self.lo + DODGE_STEP * np.asarray(rows, float)

    def _need(self, starts):
        """The rows of `starts` on the grid not read yet, sorted, and which starts are past it."""
        import numpy as np
        r = np.rint((np.asarray(starts, float) - self.lo) / DODGE_STEP).astype(int)
        past = r >= self.rows
        return sorted({int(x) for x in r[~past].tolist()} - self.at.keys()), r, past

    def plan(self, starts):
        """The grids of the `_TouchTable`s `first(starts)` would read (`_homing_ready` flies their
        homing shots ahead)."""
        import numpy as np
        if self.rows <= 0 or not len(np.atleast_1d(starts)):
            return []
        need = self._need(starts)[0]
        return [self._starts_of(self._rows_read(need))] if need else []

    def _read(self, need):
        """Read rows `need` (sorted, none read yet), one `_TouchTable` of their starts."""
        rows = self._rows_read(need)
        # Every row asked of it is one of its starts, so it reads no standing table.
        tab = _TouchTable(self.geoms, self.dist, self.move, stand=None, starts=self._starts_of(rows))
        want = set(need)
        for i, r in enumerate(rows):
            if r in want:
                self.at[r] = (tab, i)

    def _stand(self):
        """The standing table, made when a start past the grid first asks for it (`stand` may be a
        function that makes it: a carried move's is a flight of its own, `_stand_tab`)."""
        if callable(self.stand):
            self.stand = self.stand()
        return self.stand

    def first(self, starts, cuts):
        """`_TouchTable.first`."""
        import numpy as np
        starts = np.asarray(starts, float)
        cuts = np.broadcast_to(np.asarray(cuts, float), starts.shape)
        r = np.rint((starts - self.lo) / DODGE_STEP).astype(int)
        if len(r) and r.min() < 0:
            raise ValueError(f'_TouchTable: a dodge start {starts.min()} before the grid at {self.lo}')
        if self.rows <= 0:
            return self._stand().first(starts, cuts)
        past = r >= self.rows
        need = sorted({int(x) for x in r[~past].tolist()} - self.at.keys())
        if need:
            self._read(need)
        out_h = np.full(len(r), -1, np.int64)
        out_t = np.full(len(r), math.inf)
        by = {}
        for i, x in enumerate(r.tolist()):
            if x < self.rows:
                tab, rr = self.at[x]
                got = by.setdefault(id(tab), (tab, [], []))
                got[1].append(i)
                got[2].append(rr)
        for tab, idx, rows in by.values():
            idx = np.array(idx, int)
            h, t = tab.first_at(rows, cuts[idx])
            out_h[idx], out_t[idx] = h, t
        if past.any():
            h, t = self._stand().first(starts[past], cuts[past])
            out_h[past], out_t[past] = h, t
        return out_h, out_t


class _StillTable:
    """A `_TouchTable` of a defender who does not dodge again, carried on a track (`still_move`,
    version 26): read at the still move's own start whatever start is asked (it is the stand of a
    carried move's table, read for starts past that table's grid: he never starts that move)."""

    def __init__(self, table, start):
        self.table, self.start = table, float(start)

    def first(self, starts, cuts):
        import numpy as np
        n = len(np.atleast_1d(np.asarray(starts, float)))
        return self.table.first(np.full(n, self.start), np.broadcast_to(np.asarray(cuts, float), (n,)))


def _grid(x):
    return round(x / DODGE_STEP) * DODGE_STEP


class FollowupModel:
    """The reads of an attack made of an opening and a follow-up pressed in a window, worth left
    open (the rules above). `opening` and `follow` are [(contact geometry, index)], the follow-up
    at its earliest press, real frame `press` from the attack's start; the index is the geometry's
    place in the whole attack's time order (`CaughtModel`, `_bursts`). `delays` are the press
    delays the attacker may take (real frames, the first 0), `my_roll` his first roll frame at the
    earliest press, `strikes` the pool's punish strike frames. `outcome` scores one (distance,
    floor) for given worths; one model serves every build that flies the same shapes. `look` (an
    `er-mechanics-spells.Lookalikes` member, the opening's group: until the press the attack is its
    opening) times his first dodge of the opening; None, he knows it from its first frame."""

    def __init__(self, opening, follow, press, delays, my_roll, strikes=None, run=True, look=None):
        import numpy as np
        self.look = look
        self.open_bursts = self._bursts(opening)
        self.follow_bursts = self._bursts(follow)
        self.follow = [g for g, _ in follow]
        self.follow_index = np.array([i for _, i in follow], int)
        self.where = {i: j for j, (_, i) in enumerate(follow)}
        self.size = 1 + max([i for _, i in list(opening) + list(follow)], default=-1)
        self.press = float(press)
        self.delays = np.asarray(delays, float)
        if not len(self.delays) or self.delays[0] != 0.0:
            raise ValueError(f'FollowupModel: delays must start at 0, got {delays}')
        self.my_roll = my_roll
        self.strikes = None if strikes is None else np.asarray(strikes, float)
        self.run = run
        self.families = dodge_families(any(g is not None and g.get('AIR') for g, _ in list(opening) + list(follow)))
        # Where the whole attack's root motion leaves the attacker for the punish (`attack_advance`).
        self.all_geoms = [g for g, _ in list(opening) + list(follow)]
        self.advance = attack_advance(self.all_geoms)
        self.react = attacker_reactions()
        self._memo, self._rolls, self._stages, self._shapes, self._arrive = {}, {}, {}, {}, {}
        self._tabs = {}
        # `_stand_values`' answers, kept per the escape tables they read (`_stand_values`).
        self._svals = {}
        # The catches made in his own dodge (version 25, `_mid`): each a column past `size` of the
        # worth tables `outcome` and `mixup` read, (kind, whole-attack index, move, start, catch
        # frame, delay).
        self.plays, self._play_ids = [], {}
        self.open_end = max([float(g['T'].max()) for g, _ in opening if g is not None] + [-math.inf])

    def _mid(self, kind, h, move, start, catch, d=None):
        """The column of a catch by geometry `h` (whole-attack index) in his dodge of `move` started at
        `start`, made at `catch` (version 25; `CaughtModel.worth_of` `dodge`). 'open': an opening
        geometry catches his roll of it, on the attack's clock at every press delay. 'follow': the
        follow-up pressed `delays[d]` late catches his roll of the opening; `start` and `catch` are on
        the earliest-press timeline (`_rolled`'s). When every opening sample is over before that
        catch, nothing of the opening can touch him after it and the play is the earliest press's
        with the dodge started `delays[d]` earlier, one column for every delay alike (kept as delay
        None); else the play is that delay's own, on its clock."""
        if kind == 'follow':
            if self.open_end < catch - 1e-9:
                key = ('follow', h, move['anim'], float(start), float(catch), None)
            else:
                dl = float(self.delays[d])
                # A carried move's track moves to that delay's clock with it (version 26).
                move = _shift_move(move, dl)
                key = ('follow', h, move['anim'], float(start) + dl, float(catch) + dl, int(d))
        else:
            key = ('open', h, move['anim'], float(start), float(catch), None)
        if key not in self._play_ids:
            self._play_ids[key] = self.size + len(self.plays)
            self.plays.append((key[0], h, move, key[3], key[4], key[5]))
        return self._play_ids[key]

    @staticmethod
    def _stage_values(sh, WF, WD):
        """[stage, delay] of what lands on him in each stage of `_shape` `sh`: its per-geometry
        weights on the follow-up's worths `WF`, plus its catches in his own dodge (`MID`) on their
        columns of `WD` (version 25)."""
        SV = sh['C'] @ WF.T
        for si, mid in enumerate(sh['MID']):
            for pid, x in mid.items():
                SV[si] = SV[si] + x * WD[:, pid]
        return SV

    def _worth_table(self, need, worth, mid_worth, D):
        """[delay, column] of what lands per column asked for in `need[d]`: `worth(d, indices)` for
        the attack's geometries, and for a catch in his dodge (`_mid`) `mid_worth(d, h, move, start,
        catch)` on that delay's clock ('follow' plays of every delay alike on the earliest press's,
        d = 0; one of a single delay only there, 0 elsewhere); without `mid_worth`, the geometry's own
        worth, as before version 25."""
        import numpy as np
        WD = np.zeros((D, self.size + len(self.plays)))
        for d in range(D):
            std = sorted(x for x in need[d] if x < self.size)
            if std:
                idx = np.array(std, int)
                WD[d, idx] = worth(d, idx)
            for x in sorted(x for x in need[d] if x >= self.size):
                kind, h, move, s, tc, dd = self.plays[x - self.size]
                if mid_worth is None:
                    WD[d, x] = float(worth(d, np.array([h], int))[0])
                elif kind == 'follow' and dd is None:
                    WD[d, x] = float(mid_worth(0, h, move, s, tc))
                elif kind == 'follow' and dd != d:
                    WD[d, x] = 0.0
                else:
                    WD[d, x] = float(mid_worth(d, h, move, s, tc))
        return WD

    def _tab(self, dist, move, bi=None):
        """The `_TouchTable` of the whole follow-up (`bi` None) or of its burst `bi` at `dist` on
        `move` (None: standing), kept until `release(dist)`. The whole follow-up's grid reaches back
        past the longest delay, since a late press is read as his dodge started that much early."""
        key = (round(dist, 2), None if move is None else move['anim'], bi)
        if key not in self._tabs:
            if move is None:
                gs = self.follow if bi is None else self.follow_bursts[bi][0]
                self._tabs[key] = _TouchTable(gs, dist, None)
            else:
                gs, lo, hi = self._tab_grid(dist, move, bi)
                # A move carried out of an earlier roll (version 26): past the grid he never starts it,
                # so those starts read the track he is on (`_stand_tab`). The whole follow-up's table
                # is read by `_rolled` alone, at a few starts: its rows are read as asked, and the
                # standing table only when a start past its grid is (`_RowsTable`).
                trk_ = move.get('track') or ()
                if bi is None:
                    self._tabs[key] = _RowsTable(gs, dist, move, lo, hi, lambda: self._stand_tab(dist, trk_, None))
                else:
                    self._tabs[key] = _TouchTable(gs, dist, move, lo, hi, self._stand_tab(dist, trk_, bi))
        return self._tabs[key]

    def _tab_grid(self, dist, move, bi=None):
        """(geometries, first start, last start) of `_tab`'s table of `move` (not None)."""
        gs = self.follow if bi is None else self.follow_bursts[bi][0]
        top = max(g['T'].max() for g in gs) + 1.0
        lo = 0.0 if bi is not None else -(math.ceil(float(self.delays[-1])) + 1.0)
        trk = move.get('track')
        if bi is not None and trk and trk[-1][0] > TRACK_EVER:
            # A burst's table of a move out of a roll: he presses no sooner than that roll's
            # start plus its dodge cancel, so no earlier start is read (`_burst_starts`).
            lo = max(lo, math.floor((trk[-1][0] + dodge_end(trk[-1][3])) / DODGE_STEP) * DODGE_STEP)
        return gs, lo, _grid(top + 0.5)

    # -- flying ahead (`_homing_ready`) ------------------------------------------------------------
    # The reads below name the touch tables a later read will build, so that their homing shots are
    # flown together beforehand: each `_ahead_*` adds (geometries, move, dodge starts) to `reqs` for a
    # table not built yet. A wrong guess costs a flight and moves no number (`_homing_ready`).

    def _ahead_tab(self, dist, move, bi, reqs):
        """`_tab(dist, move, bi)`'s burst table (`bi` not None) and its standing table."""
        import numpy as np
        if (round(dist, 2), move['anim'], bi) in self._tabs:
            return
        gs, lo, hi = self._tab_grid(dist, move, bi)
        rows = int(round((hi - lo) / DODGE_STEP)) + 1
        if rows > 0:
            reqs.append((gs, move, float(lo) + DODGE_STEP * np.arange(rows)))
        self._ahead_still(dist, move.get('track') or (), bi, reqs)

    def _ahead_still(self, dist, track, bi, reqs):
        """`_stand_tab(dist, track, bi)`."""
        import numpy as np
        if not track:
            return
        smove, s0 = still_move(track)
        if (round(dist, 2), smove['anim'], bi, 'still') not in self._tabs:
            gs = self.follow if bi is None else self.follow_bursts[bi][0]
            reqs.append((gs, smove, float(s0) + DODGE_STEP * np.arange(1)))

    def _ahead_rolled(self, dist, move, starts, afters, reqs):
        """`_rolled(dist, k, move, starts, afters)`'s reads of the whole follow-up's table."""
        afters = [-math.inf] * len(starts) if afters is None else list(afters)
        memo = self._rolls.get((round(dist, 2), move['anim']), {})
        need = sorted({q for q in zip(starts, afters) if q not in memo})
        if not need or not move.get('track'):
            return
        tab = self._tab(dist, move)
        if isinstance(tab, _RowsTable):
            for grid in tab.plan([s for s, _ in need]):
                reqs.append((self.follow, move, grid))

    def _ahead_stage(self, fi, dist, floor, cut, cue, track, reqs):
        """`_stage(fi, dist, floor, cut, cue, track)`'s first reads: every burst's standing table
        out of `track` (`_last_follow`), and the first burst's tables as it meets him."""
        if not track or ('stage', fi, round(dist, 2), floor, cut, cue, track_key(track)) in self._stages:
            return
        for bi in range(len(self.follow_bursts)):
            self._ahead_still(dist, track, bi, reqs)
        if not self.follow_bursts:
            return
        at = burst_after([g for g, _ in self.follow_bursts], 0, cue, floor)
        trk = collapse_track(track, at) if at >= floor else track
        self._ahead_still(dist, trk, 0, reqs)
        if not trk:
            return
        for m in self._moves(fi, dist, trk):
            self._ahead_tab(dist, carried(m, trk), 0, reqs)

    def _ahead_late(self, fi, dist, floor, cut, track, reqs):
        """`_late(fi, dist, floor, cut, track)`'s first reads (its stage at cue 0)."""
        if ('late', fi, round(dist, 2), floor, cut, track_key(track)) not in self._stages:
            self._ahead_stage(fi, dist, floor, cut, 0.0, track, reqs)

    @staticmethod
    def _burst_starts(tab, grid, at):
        """`tab.first` over `grid` (cut at `at`), the starts before the table's grid read as not
        caught: a move out of a roll is never pressed before that roll's start plus its dodge cancel
        (`_tab`), so `dodge_presses` reads none of them (its presses start at his floor)."""
        import numpy as np
        grid = np.asarray(grid, float)
        h, t = np.full(len(grid), -1, int), np.full(len(grid), math.inf)
        sel = grid >= tab.lo - 1e-9
        if sel.any():
            h[sel], t[sel] = tab.first(grid[sel], np.full(int(sel.sum()), at))
        return h, t

    def _stand_tab(self, dist, track, bi=None):
        """The table of a defender at `dist` who does not dodge: standing where he started (`_tab`
        with no move), or carried on `track` (version 26), read at the still move's start
        (`still_move`; `_follow_still`)."""
        if not track:
            return self._tab(dist, None, bi)
        smove, s0 = still_move(track)
        key = (round(dist, 2), smove['anim'], bi, 'still')
        if key not in self._tabs:
            gs = self.follow if bi is None else self.follow_bursts[bi][0]
            self._tabs[key] = _StillTable(_TouchTable(gs, dist, smove, s0, s0, None), s0)
        return self._tabs[key]

    def _follow_still(self, dist, bi, cut_at, track=()):
        """(index of the follow-up burst `bi`'s geometry that first touches a defender at `dist` who
        does not dodge, its frame) with the samples before `cut_at` left out: standing where he
        started, or on `track`."""
        if not track:
            return self._tab(dist, None, bi).first([0.0], [cut_at])
        _, s0 = still_move(track)
        return self._stand_tab(dist, track, bi).first([s0], [cut_at])

    def release(self, dist):
        """Drop the touch tables at `dist` (the shapes and stages read from them stay)."""
        d = round(dist, 2)
        for key in [k for k in self._tabs if k[0] == d]:
            del self._tabs[key]

    def _shares(self, m, s, dist, rolls, origin=None):
        """The share of the pool's strikes that punish a dodge of move `m` started at `s` when the
        attacker can roll at `rolls` (`_punish_arrivals`); `s` and `rolls` broadcast. `origin`
        (version 26): where earlier rolls had carried him when it started (`punish_lead`)."""
        import numpy as np
        key = (id(m), round(dist, 2), None if origin is None else (round(origin[0], 9), round(origin[1], 9)))
        if key not in self._arrive:
            self._arrive[key] = _punish_arrivals(m, dist, self.strikes, self.run, self.advance, origin)
        ready, arrive = self._arrive[key]
        q = np.asarray(rolls, float) - np.asarray(s, float) - ready
        if arrive is None:
            return np.zeros(np.shape(q))
        return np.searchsorted(arrive, q, side='left') / len(arrive)

    @staticmethod
    def _bursts(parts):
        """[(geometries, their indices)] in time order, grouped as `_bursts` groups them."""
        out = []
        for g, i in sorted(parts, key=lambda e: e[0]['T'].min()):
            if out and g['T'].min() <= out[-1][2]:
                out[-1][0].append(g), out[-1][1].append(i)
                out[-1][2] = max(out[-1][2], g['T'].max())
            else:
                out.append([[g], [i], g['T'].max()])
        return [(gs, ix) for gs, ix, _ in out]

    def _rolled(self, dist, k, move, starts, afters=None, times=False):
        """Per dodge start in `starts` (the earliest-press timeline): (follow-up index that catches
        him on move `move` before its dodge cancel, or -1), the follow-up's samples before the
        matching `afters` entry left out (an earlier dodge judged them). One vectorized read for
        every start not seen before. With `times`, (those indices, the catch frames on the same
        timeline, inf for none)."""
        import numpy as np
        memo = self._rolls.setdefault((round(dist, 2), move['anim']), {})
        afters = [-math.inf] * len(starts) if afters is None else list(afters)
        keys = list(zip(starts, afters))
        need = sorted({q for q in keys if q not in memo})
        if need:
            h, t = self._tab(dist, move).first([s for s, _ in need], [a for _, a in need])
            end = move['dodge'] if move['dodge'] is not None else len(move['path'])
            for (s, a), hh, tt in zip(need, h, t):
                memo[(s, a)] = (int(hh), float(tt)) if hh >= 0 and tt < s + end else (-1, math.inf)
        hs = np.array([memo[q][0] for q in keys], int)
        return (hs, np.array([memo[q][1] for q in keys], float)) if times else hs

    def _follow_reaches(self, dist, bi, floor, cut, track=()):
        """Whether follow-up burst `bi`, its samples before `floor` left out with `cut`, touches a
        defender at `dist` who does not dodge it, standing where he started or carried on `track`
        (version 26; `_follow_presses`' base read; it reads no family)."""
        gs, _ = self.follow_bursts[bi]
        if all(cut and g['T'].max() < floor - 1e-9 for g in gs):
            return False
        trimmed = bool(cut) and any(g['T'].min() < floor - 1e-9 for g in gs)
        return int(self._follow_still(dist, bi, floor if trimmed else -math.inf, track)[0][0]) >= 0

    def _last_follow(self, dist, floor, cut, track=()):
        """The last follow-up burst that reaches him where he is (`_follow_reaches`), None for none:
        the one whose dodge decides his punish (`last_reaching`)."""
        return max((bi for bi in range(len(self.follow_bursts))
                    if self._follow_reaches(dist, bi, floor, cut, track)), default=None)

    def _ahead(self, t, dist, z):
        """Where the attacker's root is along the axis at real frame `t` (the whole attack's `PK` up
        to then, stopped by the defender's body `z` from his start, `advance_at`)."""
        return advance_at(attack_advance(self.all_geoms, upto=t), dist, z)

    def _moves(self, fi, dist, track=()):
        """Family `fi`'s moves as he starts them out of `track`: facing the attacker (`oriented`,
        version 27; until then the track was turned to face him, `face_track`)."""
        if not track:
            return self.families[fi]
        key = ('moves', fi, round(dist, 2), track_key(track))
        if key not in self._stages:
            self._stages[key] = [oriented(m, track, dist, lambda t, z: self._ahead(t, dist, z))
                                 for m in self.families[fi]]
        return self._stages[key]

    def _from_track(self, dist, fi, k, track=()):
        """(move k of family `fi` oriented out of `track`, that move carried on it): his roll of the
        opening as the follow-up reads it (`_rolled`)."""
        m = self._moves(fi, dist, track)[k]
        return m, carried(m, track)

    def _gains(self, fi, dist, track=()):
        """(per move of family `fi` its gain, `press_gain` out of `track`, the `_punish_ctx`) at
        `dist`: what aiming for his punish reads (`reaction_outcome` `aim_punish`), the same count as
        `_shares` at the earliest press's roll frame."""
        import numpy as np
        key = ('gains', fi, round(dist, 2), track_key(track))
        if key not in self._stages:
            ordered = None if self.strikes is None else np.sort(self.strikes)
            punish = _punish_ctx(ordered, self.my_roll, self.run)
            self._stages[key] = ([press_gain(m, track, dist, self.advance, self.run, ordered, self.my_roll)
                                  for m in self._moves(fi, dist, track)] if punish else None, punish)
        return self._stages[key]

    def _follow_presses(self, fi, dist, bi, floor, cut, cue, t, aim=False, track=()):
        """`_burst_presses` of follow-up burst `bi` met from `t`, its samples before `floor` left
        out with `cut`, for a defender carried on `track` (version 26; empty: standing where he
        started): (its geometries as met, their indices, base index, presses), or None when none of
        it is left. With `aim` the burst decides his punish and he aims for it too
        (`reaction_outcome` `aim_punish`)."""
        import numpy as np
        gs, ix = self.follow_bursts[bi]
        # A geometry is left whole by the cut or loses its samples before it (`_trim`); only which
        # survive (their last frames are unchanged) and the reads below depend on it.
        gt = [g if not cut or g['T'].max() >= floor - 1e-9 else None for g in gs]
        if all(g is None for g in gt):
            return None
        trimmed = bool(cut) and any(g['T'].min() < floor - 1e-9 for g in gs)
        at = floor if trimmed else -math.inf
        # The reads `_burst_presses` makes, from the burst's touch tables; a trimmed one is used once.
        memo = {} if trimmed else self._memo.setdefault(('follow', fi, bi, None, track_key(track)), {})
        oms = self._moves(fi, dist, track)
        mvs = [carried(m, track) for m in oms]
        if track:
            smove, _ = still_move(track)
            bkey = ('base', dist, smove['anim'])
        else:
            bkey = ('base', dist)
        if bkey not in memo:
            memo[bkey] = self._follow_still(dist, bi, at, track)
        grid = np.arange(0.0, max(g['T'].max() for g in gt if g is not None) + 1.0, DODGE_STEP)
        for k, mv in enumerate(mvs):
            key = (dist, k) if not track else (dist, k, mv['anim'])
            if key not in memo:
                memo[key] = self._burst_starts(self._tab(dist, mv, bi), grid, at)
        gains, punish = self._gains(fi, dist, track) if aim else (None, None)
        base_h, presses = _burst_presses(gt, dist, oms, cue, t, memo, gains=gains, punish=punish, track=track)
        return gt, ix, base_h, presses

    def _stage(self, fi, dist, floor, cut, cue, track=()):
        """The follow-up (earliest-press timeline) met from `floor`, its dodges timed from `cue` (0:
        he knows its timing, as `sequence_outcome`'s defender does; `press`: he reacts to the
        press), by a defender standing where he started or carried on `track` (version 26: where
        the roll he escaped the opening with left him, `dodge_chain`'s rule, each follow-up burst
        dodged on the roll that met the burst before): (per follow-up geometry, the weight of his
        being caught first by it standing where he started; the chance he evades it all; the chance
        he then punishes; whether any of it reaches him; {(follow-up index, move anim, start, catch
        frame): [move, weight]} of the catches in a dodge of his or in the roll he is still on, raw
        until the delay is known (`_resolve`); the share no burst reached, whose punish is the
        caller's own; the presses of his first dodge of it and the track he made them from, None
        when nothing reaches him). With `cut` its samples before `floor` are left out (the roll he
        is coming out of judged them, `_rolled`). Every catch in a dodge of his is a play
        (`_mid` 'follow'), at any delay alike when the opening is over by then, else on that delay's
        clock (version 26: until then one made before the opening was over was weighed on its
        geometry)."""
        import numpy as np
        key = ('stage', fi, round(dist, 2), floor, cut, cue, track_key(track))
        if key in self._stages:
            return self._stages[key]
        coef = np.zeros(len(self.follow))
        raw = {}
        # Where his rolls carry him (version 26, `dodge_chain`'s rule): track key -> [track, floor,
        # mass, punish mass, untouched (no follow-up burst has reached him yet)].
        states = {track_key(track): [track, floor, 1.0, 0.0, True]}
        reached, first = False, None
        last = self._last_follow(dist, floor, cut, track)
        nb = len(self.follow_bursts)
        for bi in range(nb):
            if any(s[0] for s in states.values()):
                # Tracks whose clips are over by the time this burst reads him stand where they left
                # him (`collapse_track`).
                merged = {}
                for trk, fl, ms, pm, un in states.values():
                    at = burst_after([g for g, _ in self.follow_bursts], bi, cue, fl)
                    trk2 = collapse_track(trk, at) if at >= fl else trk
                    k2 = (track_key(trk2), un)
                    if k2 in merged:
                        merged[k2][2] += ms
                        merged[k2][3] += pm
                    else:
                        merged[k2] = [trk2, fl, ms, pm, un]
                states = merged
            nxt = {}

            def put(trk, fl, ms, pm, un):
                k_ = (track_key(trk), un)
                if k_ in nxt:
                    nxt[k_][2] += ms
                    nxt[k_][3] += pm
                else:
                    nxt[k_] = [trk, fl, ms, pm, un]
            for trk, fl, mass, pmass, un in states.values():
                got = self._follow_presses(fi, dist, bi, floor, cut, cue, fl, aim=bi == last, track=trk)
                if got is None or got[2] < 0:
                    put(trk, fl, mass, pmass, un)
                    continue
                gt, ix, _, presses = got
                reached = True
                if un and first is None:
                    first = (presses, trk)
                for k, s, w, hit, play in presses:
                    wt = mass * w
                    if hit >= 0:
                        if play is None:
                            coef[self.where[ix[hit]]] += wt
                        else:
                            # A catch in a dodge of his, or in the roll he is still on: a play, kept
                            # raw until the delay it is read at is known (`_resolve`; version 26:
                            # until then one made while an opening sample could still touch him was
                            # weighed on its geometry, as a catch of a defender standing).
                            rk = (int(ix[hit]),) + (play[0]['anim'], float(play[1]), float(play[2]))
                            if rk not in raw:
                                raw[rk] = [play[0], 0.0]
                            raw[rk][1] += wt
                        continue
                    m = self._moves(fi, dist, trk)[k]
                    s0 = _grid(s)
                    o = track_at(trk, [s0]) if trk else None
                    share = 0.0
                    if self.my_roll is not None:
                        share = float(self._shares(m, s, dist, self.my_roll,
                                                   None if o is None else (float(o[0][0]), float(o[1][0]))))
                    put(track_extend(trk, m, s0), s0 + dodge_end(m), wt, wt * share, False)
            states = nxt
        pe = float(sum(s_[2] for s_ in states.values()))
        pp = float(sum(s_[3] for s_ in states.values()))
        untouched = float(sum(s_[2] for s_ in states.values() if s_[4]))
        out = (coef, pe if reached else 1.0, pp, reached, raw, untouched if reached else 1.0, first)
        self._stages[key] = out
        return out

    def _resolve(self, raw, d):
        """{column: weight} of a stage's raw plays (`_stage`) read with the follow-up pressed
        `delays[d]` late (`_mid` 'follow')."""
        out = {}
        for (h, _, s, tc), (move, w) in raw.items():
            pid = self._mid('follow', h, move, s, tc, d)
            out[pid] = out.get(pid, 0.0) + w
        return out

    def _resolved(self, st, d):
        """`_stage` output `st` as `_late` hands it on: (coef, evade, punish, reached, {column: weight}
        at delay `d`, the untouched share)."""
        return st[0], st[1], st[2], st[3], self._resolve(st[4], d), st[5]

    def _late_ahead(self, fi, dist, floor, cut, moves, merged, ptracks):
        """The homing shots `_late`'s loop flies against carried moves, flown together first
        (`_homing_ready`): his planned dodge on each delay's clock, read here as the loop reads it
        (`_rolled`, kept), then the stages he meets where it does not catch him. Returns the keys
        set, for `_homing_forget`."""
        import numpy as np
        D = len(self.delays)
        late = self.delays[1:]
        per = []
        reqs = []
        for k in sorted({k for k, _ in merged}):
            m = moves[k]
            mvs = [carried(m, pt) for pt in ptracks]
            items = [(s, w) for (kk, s), w in merged.items() if kk == k]
            groups = {}
            for d in range(1, D):
                groups.setdefault(mvs[d - 1]['anim'], []).append(d)
            calls = []
            for ds in groups.values():
                starts = [_grid(s) - float(late[d - 1]) for s, _ in items for d in ds]
                afters = [float(floor - late[d - 1]) if cut else -math.inf for _ in items for d in ds]
                self._ahead_rolled(dist, mvs[ds[0] - 1], starts, afters, reqs)
                calls.append((ds, starts, afters))
            per.append((k, m, mvs, items, calls))
        ready = _homing_ready(dist, reqs)
        reqs = []
        for k, m, mvs, items, calls in per:
            end = dodge_end(m)
            for ds, starts, afters in calls:
                h_ = self._rolled(dist, k, mvs[ds[0] - 1], starts, afters).reshape(len(items), len(ds))
                for j, (s, _) in enumerate(items):
                    floors = (np.round((s + end - late) / DODGE_STEP) * DODGE_STEP).tolist()
                    for c_, d in enumerate(ds):
                        if h_[j, c_] < 0:
                            self._ahead_stage(fi, dist, floors[d - 1], True, self.press,
                                              track_extend(ptracks[d - 1], m, _grid(s) - float(late[d - 1])), reqs)
        return ready + _homing_ready(dist, reqs)

    def _late(self, fi, dist, floor, cut, track=()):
        """[per press delay: (coef, evade, punish, reached, {column: weight}, the untouched share
        whose punish is the caller's own)], the follow-up pressed that late against a defender free
        from `floor` (earliest-press timeline, samples before it left out with `cut`) who planned
        for the press at once, standing where he started or carried on `track` (the roll he escaped
        the opening with, on the attack's clock: pressed `d` late, every frame of his is `d` earlier on
        the follow-up's clock, his track's too, `_shift_track`; version 27, since his roll's slide past
        its dodge cancel is carried, version 26's settled track moved with no delay). At no delay he
        meets it as planned (`_stage` from the attack's start).
        Late, his planned dodge of its first burst that reaches him (the same presses, from where
        that burst found him) meets the follow-up that much later: it catches him when it touches
        him outside that dodge's invincibility before its dodge cancel (a play from that dodge at
        any delay, `_mid`; version 26: until then one made before the opening was over was weighed
        on its geometry); else he is free from there, where that dodge left him, and reacts to the
        press (`_stage` from `press`, `INFERRED`: he reads no delay ahead). If nothing reaches him
        after that dodge, it is his last, and his punish is read from it against the attacker's
        later roll."""
        import numpy as np
        key = ('late', fi, round(dist, 2), floor, cut, track_key(track))
        if key in self._stages:
            return self._stages[key]
        D = len(self.delays)
        st0 = self._stage(fi, dist, floor, cut, 0.0, track)
        out = [self._resolved(st0, 0)]
        if st0[6] is None:
            # Nothing of it is left to plan for (his roll judged it all): pressed late it may reach
            # him after his floor, and he reacts to the press.
            reqs = []
            for d in self.delays[1:]:
                self._ahead_stage(fi, dist, _grid(floor - d), cut, self.press, _shift_track(track, -float(d)), reqs)
            ready = _homing_ready(dist, reqs)
            out += [self._resolved(self._stage(fi, dist, _grid(floor - d), cut, self.press,
                                               _shift_track(track, -float(d))), di)
                    for di, d in enumerate(self.delays[1:], 1)]
            _homing_forget(ready)
            self._stages[key] = out
            return out
        plan, ptrack = st0[6]
        moves = self._moves(fi, dist, ptrack)
        merged = {}
        for k, s, w, *_ in plan:
            merged[(k, round(s, 6))] = merged.get((k, round(s, 6)), 0.0) + w
        coef, pe, pp = np.zeros((D, len(self.follow))), np.zeros(D), np.zeros(D)
        mids = [{} for _ in range(D)]
        late = self.delays[1:]
        stage = {}
        # Per delay, where the plan found him on the follow-up's clock (`_shift_track`: the same
        # track for all when it is settled or empty).
        ptracks = [_shift_track(ptrack, -float(d)) for d in late]
        ready = self._late_ahead(fi, dist, floor, cut, moves, merged, ptracks)
        for k in sorted({k for k, _ in merged}):
            m = moves[k]
            # His planned dodge, out of where the plan found him, on each delay's clock.
            mvs = [carried(m, pt) for pt in ptracks]
            end = dodge_end(m)
            items = [(s, w) for (kk, s), w in merged.items() if kk == k]
            hs = np.full((len(items), D - 1), -1, int)
            ts = np.full((len(items), D - 1), math.inf)
            groups = {}
            for d in range(1, D):
                groups.setdefault(mvs[d - 1]['anim'], []).append(d)
            for ds in groups.values():
                mv_ = mvs[ds[0] - 1]
                starts = [_grid(s) - float(late[d - 1]) for s, _ in items for d in ds]
                afters = [float(floor - late[d - 1]) if cut else -math.inf for _ in items for d in ds]
                h_, t_ = self._rolled(dist, k, mv_, starts, afters, times=True)
                h_, t_ = h_.reshape(len(items), len(ds)), t_.reshape(len(items), len(ds))
                for c_, d in enumerate(ds):
                    hs[:, d - 1], ts[:, d - 1] = h_[:, c_], t_[:, c_]
            for j, (s, w) in enumerate(items):
                floors = (np.round((s + end - late) / DODGE_STEP) * DODGE_STEP).tolist()
                o = track_at(ptrack, [_grid(s)]) if ptrack else None
                origin = None if o is None else (float(o[0][0]), float(o[1][0]))
                own = self._shares(m, s, dist, self.my_roll + late, origin) if self.my_roll is not None \
                    else np.zeros(D - 1)
                for d in range(1, D):
                    h = hs[j, d - 1]
                    if h >= 0:
                        # The late press catches him in his planned dodge: that play.
                        pid = self._mid('follow', int(self.follow_index[h]), mvs[d - 1], _grid(s) - float(late[d - 1]),
                                        float(ts[j, d - 1]), d)
                        mids[d][pid] = mids[d].get(pid, 0.0) + w
                        continue
                    fl = floors[d - 1]
                    # Free from its dodge cancel, on its whole root motion (version 27).
                    after = track_extend(ptracks[d - 1], m, _grid(s) - float(late[d - 1]))
                    sk = (fl, track_key(after))
                    if sk not in stage:
                        stage[sk] = self._stage(fi, dist, fl, True, self.press, after)
                    st = stage[sk]
                    coef[d] += w * st[0]
                    pe[d] += w * st[1]
                    pp[d] += w * (st[2] + st[5] * own[d - 1])
                    for pid, x in self._resolve(st[4], d).items():
                        mids[d][pid] = mids[d].get(pid, 0.0) + w * x
        _homing_forget(ready)
        out += [(coef[d], float(pe[d]), float(pp[d]), True, mids[d], 0.0) for d in range(1, D)]
        self._stages[key] = out
        return out

    def _base(self, part, fi, bi, gs, dist):
        """Index in `gs` of the geometry that catches a defender standing at `dist`, or -1 (the
        read `_burst_presses` memoises under the same key)."""
        memo = self._memo.setdefault((part, fi, bi, None) if part == 'follow' else (part, fi, bi), {})
        if ('base', dist) not in memo:
            memo[('base', dist)] = self._tab(dist, None, bi).first([0.0], [-math.inf]) if part == 'follow' \
                else _catches(gs, dist, None, [0.0])
        return int(memo[('base', dist)][0][0])

    def _open_vals(self, fi, dist, track=()):
        """`_burst_presses`' `vals` for his roll of the last opening burst that reaches him, on family
        `fi` at `dist` (version 25), out of `track` (version 26: where earlier opening rolls carried
        him): he aims it, as `dodge_presses` aims every dodge, for what it is worth to him, here
        against the follow-up pressed at once, which he plans for (the comment above
        `attacker_reactions`). From an escaping start s: 0 when the follow-up catches him on that
        roll (`_rolled`); else, free from its dodge cancel where it left him, what its stage leaves
        him (`_stage` at cue 0: the chance he escapes it plus his punish share, plus his own punish
        from the roll on the share none of it reaches) when any of it reaches him then, or 1 plus
        his own punish (`_shares` at the earliest press's roll frame, the gain counted on those
        starts alone). Until version 25 he aimed at the middle of the opening's escaping run and met
        the follow-up as that left him. Kept per (move, grid, track)."""
        import numpy as np

        def vals(k, grid, escaping):
            key = ('open vals', fi, round(dist, 2), k, len(grid), track_key(track))
            if key not in self._stages:
                # His roll out of where earlier opening rolls carried him, facing the attacker
                # (`_from_track`, version 27: that track is no longer taken as settled).
                m, mv = self._from_track(dist, fi, k, track)
                end = dodge_end(m)
                gains = self._gains(fi, dist, track)[0]
                gk = None if gains is None else gains[k]
                val = np.zeros(len(grid))
                own = np.zeros(len(grid), bool)
                idx = np.nonzero(np.asarray(escaping, bool))[0]
                if len(idx):
                    starts = [_grid(float(grid[i])) for i in idx]
                    # Flown together first (`_homing_ready`): the roll, then the stages it frees him to.
                    reqs = []
                    self._ahead_rolled(dist, mv, starts, None, reqs)
                    ready = _homing_ready(dist, reqs)
                    fh = self._rolled(dist, k, mv, starts)
                    reqs = []
                    for s, h in zip(starts, fh.tolist()):
                        if h < 0:
                            self._ahead_stage(fi, dist, _grid(s + end), True, 0.0, track_extend(track, m, s), reqs)
                    ready += _homing_ready(dist, reqs)
                    for i, s, h in zip(idx.tolist(), starts, fh.tolist()):
                        if h >= 0:
                            continue
                        # Free from its dodge cancel, on its whole root motion (version 27).
                        after = track_extend(track, m, s)
                        st = self._stage(fi, dist, _grid(s + end), True, 0.0, after)
                        if not st[3]:
                            val[i], own[i] = 1.0, True
                        elif st[5] > 0 and gk is not None:
                            val[i] = st[1] + st[2] + st[5] * float(gk(np.array([s]))[0])
                        else:
                            val[i] = st[1] + st[2]
                    _homing_forget(ready)
                self._stages[key] = (own, val, gk)
            return self._stages[key]
        return vals

    def _shape(self, dist, floor, fi):
        """What `outcome` reads of family `fi` at one (distance, floor), worth left open: the
        opening's catches before its last burst that reaches him standing (`early`), his presses
        on that burst merged where they are equal (`w`, `vis`: the share of the attacker's
        reactions that see it before the window opens), and per press and delay the index that
        catches him (`caught`) or the follow-up stage he meets (`sid` into `stages`) and his
        punish when no follow-up burst reaches him (`own`)."""
        import numpy as np
        key = (round(dist, 2), round(floor * 2) / 2, fi)
        if key in self._shapes:
            return self._shapes[key]
        moves = self.families[fi]
        D = len(self.delays)
        reach = [bi for bi, (gs, _) in enumerate(self.open_bursts) if self._base('open', fi, bi, gs, dist) >= 0]
        early, base = [], None
        # The opening's bursts up to its last that reaches him standing, each dodged on the roll that
        # met the burst before (version 26, `dodge_chain`'s rule): track key -> [track, floor, mass].
        # `last` collects, per carried state, its presses on that last burst; `free` the states it
        # does not reach.
        last, free = [], []
        if reach:
            states = {(): [(), float(floor), 1.0]}
            gbursts = [gs for gs, _ in self.open_bursts]
            for bi in range(reach[-1] + 1):
                gs, ix = self.open_bursts[bi]
                if any(s_[0] for s_ in states.values()):
                    merged = {}
                    for trk, fl, ms in states.values():
                        at = burst_after(gbursts, bi, 0.0, fl)
                        trk2 = collapse_track(trk, at) if at >= fl else trk
                        k2 = track_key(trk2)
                        if k2 in merged:
                            merged[k2][2] += ms
                        else:
                            merged[k2] = [trk2, fl, ms]
                    states = merged
                nxt = {}
                for trk, fl, mass in states.values():
                    root = not trk
                    # His first dodge, of the first opening burst that reaches him standing, is timed
                    # by the opening's lookalike group (`look`), as `sequence_outcome`'s `plan` times it.
                    plan = self.look.plan(dist) if self.look is not None and root and bi == reach[0] else None
                    # His roll of the last one, knowing the attack, is aimed for what it leaves him
                    # against the follow-up pressed at once, as he plans it (`_open_vals`, version 25).
                    vals = self._open_vals(fi, dist, trk) if bi == reach[-1] and plan is None else None
                    # His rolls out of the track facing the attacker (`_moves`, version 27).
                    oms = self._moves(fi, dist, trk)
                    base_h, presses = _burst_presses(gs, dist, oms, 0.0, fl, self._memo.setdefault(('open', fi, bi), {}),
                                                     plan, vals=vals, track=trk)
                    if root and base is None and base_h >= 0:
                        base = ix[base_h]
                    if base_h < 0:
                        k_ = track_key(trk)
                        if k_ in nxt:
                            nxt[k_][2] += mass
                        else:
                            nxt[k_] = [trk, fl, mass]
                        continue
                    if bi == reach[-1]:
                        last.append((trk, mass, presses))
                        continue
                    for k, s, w, hit, play in presses:
                        if hit >= 0:
                            early.append((ix[hit] if play is None else self._mid('open', ix[hit], *play), mass * w))
                        else:
                            s0 = _grid(s)
                            t2 = track_extend(trk, oms[k], s0)
                            k_ = track_key(t2)
                            if k_ in nxt:
                                nxt[k_][2] += mass * w
                            else:
                                nxt[k_] = [t2, s0 + dodge_end(oms[k]), mass * w]
                states = nxt
            free = list(states.values())
        # Version 27: an opening burst after the last that reaches him standing can still touch him
        # where a roll carried him (until then it was never read). Each such burst is met as the last
        # one is (his press there aimed for what it leaves him against the follow-up, `_open_vals`),
        # and the press that meets the follow-up is then that burst's. `pend` holds the states still
        # free: (track, floor, mass, (the track he pressed out of, move index, start) of the press
        # that put him there, None for none); `finals` the presses an opening burst caught.
        pend, finals = [], []
        if reach:
            gs_l, ix_l = self.open_bursts[reach[-1]]
            for trk, mass, presses in last:
                oms = self._moves(fi, dist, trk)
                for k, s, wt, hit, play in presses:
                    if hit >= 0:
                        # Caught in his roll of the opening, or in the track he was still on: that
                        # play (`_mid`, version 25), else the index.
                        hid = ix_l[hit] if play is None else self._mid('open', ix_l[hit], *play)
                        finals.append((mass * wt, s, k, hid, trk))
                    else:
                        s0 = _grid(s)
                        pend.append((track_extend(trk, oms[k], s0), s0 + dodge_end(oms[k]), mass * wt, (trk, k, s)))
            pend += [(trk, fl, mass, None) for trk, fl, mass in free]
            gbursts = [gs for gs, _ in self.open_bursts]
            for bi in range(reach[-1] + 1, len(self.open_bursts)):
                gs, ix = self.open_bursts[bi]
                nxt_p = []
                for trk, fl, mass, info in pend:
                    at = burst_after(gbursts, bi, 0.0, fl)
                    tr = collapse_track(trk, at) if at >= fl else trk
                    oms = self._moves(fi, dist, tr)
                    base_h, presses = _burst_presses(gs, dist, oms, 0.0, fl, self._memo.setdefault(('open', fi, bi), {}),
                                                     None, vals=self._open_vals(fi, dist, tr), track=tr)
                    if base_h < 0:
                        nxt_p.append((tr, fl, mass, info))
                        continue
                    for k, s, wt, hit, play in presses:
                        if hit >= 0:
                            hid = ix[hit] if play is None else self._mid('open', ix[hit], *play)
                            finals.append((mass * wt, s, k, hid, tr))
                        else:
                            s0 = _grid(s)
                            nxt_p.append((track_extend(tr, oms[k], s0), s0 + dodge_end(oms[k]), mass * wt, (tr, k, s)))
                pend = nxt_p
        stages, sids = [], {}

        def sid_of(fl, cut, trk=()):
            """The index in `stages` of `_late(fl, cut, trk)`'s first delay; the others follow it."""
            k_ = (fl, cut, track_key(trk))
            if k_ not in sids:
                sids[k_] = len(stages)
                stages.extend(self._late(fi, dist, fl, cut, trk))
            return sids[k_]
        out = {'early': early, 'alive': 1.0, 'base': base}
        rows = []                               # (weight, start, move index, open hit, track, floor)
        if not reach:
            # No opening burst reaches him standing: no roll to see, the press at once, met from his
            # own floor.
            for bi, (gs, ix) in enumerate(self.follow_bursts):
                b = self._base('follow', fi, bi, gs, dist)
                if b >= 0:
                    base = ix[b]
                    break
            out['base'] = base
            rows.append((1.0, math.inf, -1, -1, (), _grid(floor)))
        else:
            merged = {}
            for trk, fl, mass, info in pend:
                if info is None:
                    # Carried out of every later opening burst's reach (version 26): no roll of it to
                    # see, the press at once, met from his own floor where his track has him.
                    rows.append((mass, math.inf, -1, -1, trk, _grid(fl)))
                    continue
                mk = (track_key(info[0]), info[1], round(info[2], 6), -1)
                if mk not in merged:
                    merged[mk] = [info[0], 0.0]
                merged[mk][1] += mass
            for wt, s, k, hid, trk in finals:
                mk = (track_key(trk), k, round(s, 6), hid)
                if mk not in merged:
                    merged[mk] = [trk, 0.0]
                merged[mk][1] += wt
            for (tk, k, s, hid), (trk, wt) in merged.items():
                rows.append((wt, s, k, hid, trk, None))
        n = len(rows)
        caught = np.full((n, D), -1, int)
        sid = np.full((n, D), -1, int)
        own = np.zeros((n, D))
        w = np.array([r[0] for r in rows], float)
        starts = np.array([r[1] for r in rows], float)
        ks = np.array([r[2] for r in rows], int)
        oh = np.array([r[3] for r in rows], int)
        rolls = [None] * n
        # The homing shots the rows below fly against carried moves, flown together first
        # (`_homing_ready`): each roll on each delay's clock, and the stage it meets.
        reqs = []
        for wt, s, k, hid, trk, fl in rows:
            if k < 0:
                self._ahead_late(fi, dist, fl, False, trk, reqs)
            elif hid < 0:
                s0 = _grid(s)
                tc_ = collapse_track(trk, s0) if trk else trk
                m, _ = self._from_track(dist, fi, k, tc_)
                groups = {}
                for d in range(D):
                    mv_ = carried(m, _shift_track(tc_, -float(self.delays[d])))
                    groups.setdefault(mv_['anim'], (mv_, []))[1].append(d)
                for mv_, ds in groups.values():
                    self._ahead_rolled(dist, mv_, [s0 - float(self.delays[d]) for d in ds], None, reqs)
                self._ahead_late(fi, dist, _grid(s0 + dodge_end(m)), True, track_extend(tc_, m, s0), reqs)
        ready = _homing_ready(dist, reqs)
        for i, (wt, s, k, hid, trk, fl) in enumerate(rows):
            if k < 0:
                sid[i, :] = sid_of(fl, False, trk) + np.arange(D)
            elif hid >= 0:
                caught[i, :] = hid
            else:
                s0 = _grid(s)
                # The track he pressed out of, collapsed when its clips are over by then.
                tc_ = collapse_track(trk, s0) if trk else trk
                # His roll facing the attacker (`_from_track`, version 27), on each delay's clock: a
                # track still moving at the press moves `d` earlier with it (`_shift_track`).
                m, _ = self._from_track(dist, fi, k, tc_)
                end = dodge_end(m)
                mvd = [carried(m, _shift_track(tc_, -float(d))) for d in self.delays]
                fh = np.full(D, -1, int)
                ft = np.full(D, math.inf)
                groups = {}
                for d in range(D):
                    groups.setdefault(mvd[d]['anim'], []).append(d)
                for ds in groups.values():
                    h_, t_ = self._rolled(dist, k, mvd[ds[0]], [s0 - float(self.delays[d]) for d in ds], times=True)
                    fh[ds], ft[ds] = h_, t_
                hit = fh >= 0
                # The follow-up catches him in his roll of the opening: that play, from the roll
                # (version 25; `_mid` 'follow').
                for d in np.nonzero(hit)[0].tolist():
                    caught[i, d] = self._mid('follow', int(self.follow_index[fh[d]]), mvd[d], s0 - float(self.delays[d]),
                                             float(ft[d]), d)
                # Free from its dodge cancel, on its whole root motion (version 27; version 26 stood
                # him where its dodge cancel had carried him).
                after = track_extend(tc_, m, s0)
                sid[i, ~hit] = sid_of(_grid(s0 + end), True, after) + np.nonzero(~hit)[0]
                # The roll and where it started, his punish's (`_shares`; `mixup`'s 'none' column).
                o = track_at(tc_, [s0]) if tc_ else None
                rolls[i] = (m, None if o is None else (float(o[0][0]), float(o[1][0])))
                if self.my_roll is not None:
                    own[i] = self._shares(m, s, dist, self.my_roll + self.delays, rolls[i][1])
        _homing_forget(ready)
        if not stages:
            stages.append((np.zeros(len(self.follow)), 1.0, 0.0, False, {}, 1.0))
        # Per press and attacker reaction: the first delay he can press at having seen it, or -1
        # when it shows only after the window closes (`outcome`).
        R = np.array([r for r, _ in self.react], float)
        seen_at = starts[:, None] + R[None, :] - self.press
        lo = np.searchsorted(self.delays, seen_at - 1e-9, side='left')
        lo = np.where(lo < D, lo, -1)
        out.update(w=w, starts=starts, ks=ks, open_hit=oh, seen_at=seen_at, lo=lo, caught=caught, sid=sid, own=own, rolls=rolls,
                   C=np.array([s_[0] for s_ in stages], float).reshape(len(stages), len(self.follow)),
                   PE=np.array([s_[1] for s_ in stages], float), PP=np.array([s_[2] for s_ in stages], float),
                   R=np.array([s_[3] for s_ in stages], bool), MID=[s_[4] for s_ in stages],
                   U=np.array([s_[5] for s_ in stages], float))
        # The worths `outcome` can read, per delay: what catches him there, and the follow-up
        # geometries a stage met there weighs, its catches in his dodge too; the others are
        # multiplied by 0 or not read.
        supp = [set(self.follow_index[np.nonzero(row)[0]].tolist()) | set(mid) for row, mid in zip(out['C'], out['MID'])]
        need = []
        for d in range(D):
            s_ = set(caught[:, d][caught[:, d] >= 0].tolist())
            for sd in set(sid[:, d][sid[:, d] >= 0].tolist()):
                if out['R'][sd]:
                    s_ |= supp[sd]
            need.append(s_)
        out['need'] = need
        out['fixed'] = {h for h, _ in early} | ({base} if base is not None else set())
        self._shapes[key] = out
        return out

    def _caught_rows(self, catch_escapes):
        """The roll rows' catches by the opening priced for the attacker who sees the caught defender
        leave (`_CaughtRows`)."""
        return _CaughtRows(self, catch_escapes)

    def outcome(self, dist, floor, worth, see=True, read=False, mid_worth=None, catch_escapes=None):
        """The attack at `dist` m against a defender whose recovery ends at `floor`: `value`,
        `base`, `p_evade`, `p_punish`, `punish_hp` and `moves` as `sequence_outcome` gives them,
        `delayed` (the share of defenders the attacker presses late on) and `default` (the delay
        he presses at when he has seen no roll). `worth(d, indices)` gives, per index asked for of
        the whole attack, what lands when that geometry catches him first with the press
        `delays[d]` late (capped as the caller caps); only the ones this call can read are asked.

        The scored rule: he presses at once unless a roll showed before the window opened; seen, he
        takes the delay that leaves him most among those after he saw it. With `read` he also
        picks the default delay itself, per family (a read made before any roll shows: the user's
        "if you think your opponent will attempt to roll"), still reacting to a roll that shows
        before it; a defender he has not seen roll then meets that delay. `see` False presses
        every follow-up at once (only `worth(0)` is read). `mid_worth(d, h, move, start, catch)`
        prices a catch made in his own dodge (`_worth_table`, version 25).

        `catch_escapes(column)` (version 26), when given: for a catch by the opening (a whole-attack
        index, or an 'open' play of `_mid`), `CaughtModel.escape_table` of it per delay (None where
        it gives none): the attacker who sees that caught defender leave presses for it as the stand
        row's attacker does (`_stand_values`), at the column's delay unless he saw the escape in
        time. Until version 26 a press caught by the opening was priced at the column's delay."""
        import numpy as np
        D = len(self.delays) if see else 1
        shapes = [self._shape(dist, floor, fi) for fi in range(len(self.families))]
        stand_of = self._caught_rows(catch_escapes if see else None)
        # Only the worths a shape can read are asked for (`_shape` 'need'); the rest stay 0, which
        # nothing reads or which a weight of 0 multiplies.
        need = [set() for _ in range(D)]
        for sh in shapes:
            for d in range(D):
                need[d] |= sh['need'][d]
            for d in (range(D) if read and see else (0,)):
                need[d] |= sh['fixed']
        WD = self._worth_table(need, worth, mid_worth, D)                        # [delay, column]
        WF = WD[:, self.follow_index]
        cols = np.arange(D)[None, :]
        wq = np.array([w for _, w in self.react], float)
        best = None
        for fi, moves in enumerate(self.families):
            sh = shapes[fi]
            caught, sid = sh['caught'][:, :D], sh['sid'][:, :D]
            sidc = np.maximum(sid, 0)
            reached = (sid >= 0) & sh['R'][sidc]
            SV = self._stage_values(sh, WF, WD)                                  # [stage, delay]
            val = np.where(caught >= 0, WD[cols, np.maximum(caught, 0)],
                           np.where(reached, SV[sidc, cols], 0.0))
            # His own punish from the opening roll on the share no follow-up burst reaches (`U`, version
            # 26: a carried defender can be out of one burst's reach and in another's).
            pun = np.where(caught >= 0, 0.0, np.where(sid >= 0, sh['PP'][sidc] + sh['U'][sidc] * sh['own'][:, :D],
                                                      sh['own'][:, :D]))
            eva = np.where(caught >= 0, 0.0, np.where(sid >= 0, sh['PE'][sidc], 1.0))
            net = val - pun * PUNISH_HP
            n = len(net)
            rows = np.arange(n)
            # suf[i, j]: the delay from j on that leaves him most against press i (the earliest of
            # equals).
            suf = np.empty((n, D), int)
            suf[:, D - 1] = D - 1
            for j in range(D - 2, -1, -1):
                suf[:, j] = np.where(net[:, j] >= net[rows, suf[:, j + 1]], j, suf[:, j + 1])
            lo = sh['lo'] if see else np.full(sh['lo'].shape, -1)
            pick_rows = rows[:, None]
            a = sh['alive']
            fam = None
            for dr in (range(D) if read and see else (0,)):
                seen = (lo >= 0) & (sh['seen_at'] <= self.delays[dr] + 1e-9)
                idx = np.where(seen, suf[pick_rows, np.maximum(lo, 0)], dr)       # [press, reaction]
                fv, fp, fe = ((m_[pick_rows, idx] * wq).sum(axis=1) for m_ in (val, pun, eva))
                # A press the opening caught: the attacker presses for the caught defender's escape
                # (`_caught_rows`, version 26).
                fv = stand_of.rows(sh, fv, dr)
                base = float(WD[dr, sh['base']]) if sh['base'] is not None else 0.0
                out = {'value': stand_of.early(sh, WD, dr) + a * float(sh['w'] @ fv), 'base': base,
                       'p_evade': a * float(sh['w'] @ fe) if base else 0.0, 'p_punish': a * float(sh['w'] @ fp),
                       'delayed': a * float(sh['w'] @ ((idx > 0) * wq).sum(axis=1)), 'default': float(self.delays[dr])}
                out.update(punish_hp=out['p_punish'] * PUNISH_HP, moves=moves[0]['anim'])
                if fam is None or out['value'] - out['punish_hp'] > fam['value'] - fam['punish_hp'] + 1e-12:
                    fam = out
            if best is None or fam['value'] - fam['punish_hp'] < best['value'] - best['punish_hp']:
                best = fam
        return best

    def strafe_escape(self, dist, run_floor, speed=None):
        """The share of a strafing defender's starts that no opening geometry touches at `dist`
        (`mixup`'s strafe row): he walks sideways at `speed` (default `STRAFE_SPEED_MPS`, either
        side, the better one) from his reaction to the attack's start, no earlier than his move
        cancel `run_floor`. His reaction is a dodge's (`reaction_delays`: the reaction time and two
        network legs, he sees the attack one leg late and his walk reaches the attacker's machine
        one leg late), as every reaction of his is (version 23; it was `attacker_reactions`, no
        network leg, which no game reason set apart), with a dodge's timing error (`pressed`,
        version 28: every press of his carries it). It runs from the attack's start, not from
        `burst_cue`: a walk needs no timing read, only that an attack is coming, as his strike
        (`er-mechanics-spells.strike_segments`). The flight of a homing shot is flown again
        against his path (`_catches`, `HOMING`)."""
        import numpy as np
        key = ('strafe', round(dist, 2), round(run_floor * 2) / 2, speed)
        if key in self._stages:
            return self._stages[key]
        gs = [g for gs_, _ in self.open_bursts for g in gs_]
        # His reaction plus each timing error, no sooner than his move cancel (`pressed`, version 28).
        delays = [p for r, w in reaction_delays() for p in pressed(r, run_floor, w)]
        starts = np.array([s for s, _ in delays], float)
        wq = np.array([w for _, w in delays], float)
        best = 0.0
        for side in (1.0, -1.0):
            h, _ = _catches(gs, dist, strafe_motion(side, speed), starts)
            best = max(best, float(wq[h < 0].sum()))
        self._stages[key] = best
        return best

    def strafe_ok(self):
        """Whether `mixup` offers the strafe row: only against an opening whose shots are aimed when
        created. A shot a search launches (`CUE`, `search_cues`) is aimed at its launch, which the
        caster times to his facing (`VERIFIED` above `SEARCH_ARM_S`), so a defender walking
        `STRAFE_FRAMES` from the cast is found wherever the caster then faces him; `strafe_escape`
        flies the shots as aimed at creation and cannot say where that leaves him."""
        return not any(g is not None and g.get('CUE') is not None for gs, _ in self.open_bursts for g in gs)

    def _stand_values(self, stand_escapes, cols_d):
        """The stand row's worths per column `delays[dr]` of `cols_d` (`mixup`), version 25.
        `stand_escapes(d)` is `CaughtModel.escape_table` of the opening's standing catch with the
        follow-up pressed `delays[d]` late: per pool poise and escape, when he leaves, his score
        and what lands. The attacker applies the roll rows' rule to the caught defender's escape: he
        presses at `delays[dr]` unless the escape showed his reaction (`attacker_reactions`) before
        then, and from the first delay he can press at having seen it he takes the one that lands
        most against that escape (the earliest of equals). Knowing that, per poise the defender
        takes the escape whose score, so pressed, is least (the first of equals within 1e-12, as
        `CaughtModel._best_at`), and the row is what lands then, summed over the poises. Until
        version 25 the press came at the column's delay whatever he did.

        Kept per the tables read (`STAND_VALUES_KEPT`; 2026-11-18). The answer reads nothing but
        the tables, `cols_d` and this model's `delays`, `react` and `press`, which are set once, and
        the tables are never written (`CaughtModel.escape_table`), so the same table objects give
        the same answer to the bit. Each `outcome` call builds its own `_CaughtRows`, which read every
        catch of the opening again, and `FollowupForm._worths` hands every situation key of a
        distance the same tables: Waves of Darkness then R2 f68 made 7,330 calls in its first 396 s
        of CPU, 6,802 of them on table objects an earlier call had read, 16 distinct in content, and
        spent 27% of its sampled time here (`MEASURED` 2026-11-18, `target/wf/waves/p2`, py-spy). A
        table is named by its arrays' ids, each held by a weak reference: an entry whose arrays have
        gone is never read."""
        import numpy as np
        import weakref
        D = len(self.delays)
        tabs = [stand_escapes(d) for d in range(D)]
        arrs = [a for t in tabs for a in t[:3]]
        key = (tuple(map(id, arrs)), tuple(cols_d))
        got = self._svals.get(key)
        if got is not None and all(r() is a for r, a in zip(got[0], arrs)):
            return list(got[1])
        TE = np.stack([t[0] for t in tabs], axis=2)                    # [poise, escape, delay]
        S = np.stack([t[1] for t in tabs], axis=2)
        C = np.stack([t[2] for t in tabs], axis=2)
        P, E, _ = C.shape
        # suf[p, e, j]: the delay from j on that lands most against escape e (the earliest of equals).
        suf = np.empty((P, E, D), int)
        suf[..., D - 1] = D - 1
        for j in range(D - 2, -1, -1):
            nxt = suf[..., j + 1]
            suf[..., j] = np.where(C[..., j] >= np.take_along_axis(C, nxt[..., None], 2)[..., 0], j, nxt)
        R = np.array([r for r, _ in self.react], float)
        wq = np.array([w for _, w in self.react], float)
        pi, ei = np.arange(P)[:, None, None], np.arange(E)[None, :, None]
        rp = np.arange(P)
        out = []
        for dr in cols_d:
            seen_at = TE[:, :, dr][..., None] + R[None, None, :] - self.press        # [poise, escape, reaction]
            lo = np.searchsorted(self.delays, seen_at - 1e-9, side='left')
            seen = (lo < D) & (seen_at <= self.delays[dr] + 1e-9)
            idx = np.where(seen, suf[pi, ei, np.minimum(lo, D - 1)], dr)
            sc = (S[pi, ei, idx] * wq).sum(axis=2)                                    # [poise, escape]
            cv = (C[pi, ei, idx] * wq).sum(axis=2)
            # Per poise the first escape whose score is under the best so far by more than 1e-12,
            # escape by escape (every poise at once: the same comparisons), then the poises' worths
            # added in poise order.
            best = np.zeros(P, np.int64)
            for e in range(1, E):
                best = np.where(sc[:, e] < sc[rp, best] - 1e-12, e, best)
            tot = 0.0
            for x in cv[rp, best].tolist():
                tot += x
            out.append(tot)
        if len(self._svals) >= STAND_VALUES_KEPT:
            self._svals.clear()
        self._svals[key] = (tuple(weakref.ref(a) for a in arrs), tuple(out))
        return out

    def mixup(self, dist, floor, worth, open_worth=None, open_roll=None, run_floor=None, strafe_share=1.0,
              blind=True, stand_escapes=None, mid_worth=None, open_mid=None, catch_escapes=None):
        """The attack at (`dist`, `floor`) as the two-sided choice it is (bd er-effects-rs-me5p; the
        user, 2026-11-16: "I think the pebble/R2 interaction is what is called a 50/50"): a
        zero-sum game of the attacker's net, solved for its mixed-strategy value (`zero_sum_game`).

        The defender's pure strategies (rows):
        - 'roll' per move family: he dodges the opening as `outcome` has him do, then the follow-up
          as planned for the press at once, reacting to a later press (`outcome`'s rule); a press
          the opening catches meets the stand row's rule with `catch_escapes` (`outcome`'s, version
          26);
        - 'stand': he takes the opening's first catch standing (`base`, the opening's own hit) and
          is played out of the rest by `CaughtModel` (`worth`), so a follow-up lands only where it
          catches him while he takes his hit reaction and leaves; offered when the opening reaches
          him standing. With `stand_escapes(d)` (`CaughtModel.escape_table` of that catch, the
          follow-up `delays[d]` late) the attacker reacts to the caught defender's escape as he
          reacts to a roll (`_stand_values`, version 25); without it he presses at the column's
          delay whatever the defender does, as before;
        - 'strafe' (with `run_floor`): he walks sideways from his move cancel (`strafe_escape`, on a
          dodge's reaction and network delay); a start that escapes the opening leaves him free, and he meets the
          follow-up reacting to its press as a free defender does (`_stage` from the press), and one
          that does not is caught as 'stand' is. `strafe_share` (`INFERRED`, default 1.0: the
          geometry as flown) scales the escaping share: desync and execution, which no
          param establishes. Offered only against an opening aimed at its shots' creation
          (`strafe_ok`): never against a search launch.
        The attacker's (columns): 'none' (no follow-up, his roll at `open_roll`; offered with
        `open_worth`, the worth of a catch by the opening alone, and `open_roll`), and per default
        delay `delays[dr]` (all of them with `blind`, else the press at once only) the press policy
        `outcome` scores: at `delays[dr]` unless a roll showed before the window opened, then the
        delay that leaves him most against it.

        Returns {'value', 'rows', 'cols', 'M' (rows x cols, attacker net), 'x' (the defender's mix),
        'y' (the attacker's), 'fixed' (`outcome`'s scored net: the roll rows against the press
        policy at once)}. `worth` and `open_worth` as `outcome`'s `worth`, `open_worth(indices)`
        indexed as the whole attack is; `mid_worth` as `outcome`'s, and `open_mid(h, move, start,
        catch)` a catch in his roll of the opening priced by the opening alone (version 25)."""
        import numpy as np
        D = len(self.delays)
        cols_d = list(range(D)) if blind else [0]
        # The roll rows' catches by the opening, priced for the attacker who sees the caught defender
        # leave (`catch_escapes`, `outcome`'s; version 26).
        stand_of = self._caught_rows(catch_escapes)
        shapes = [self._shape(dist, floor, fi) for fi in range(len(self.families))]
        stand = shapes[0]['base']
        if stand is not None and stand in set(self.follow_index.tolist()):
            stand = None
        need = [set() for _ in range(D)]
        for sh in shapes:
            for d in range(D):
                need[d] |= sh['need'][d] | sh['fixed']
        if stand is not None:
            for d in range(D):
                need[d].add(stand)
            if run_floor is not None and strafe_share > 0 and self.strafe_ok():
                # The strafe row's stages read their catches in his dodge too (`_stage` 'mids').
                for dr in cols_d:
                    need[dr] |= set(self._resolve(self._stage(0, dist, _grid(floor - self.delays[dr]), False,
                                                              self.press)[4], dr))
        WD = self._worth_table(need, worth, mid_worth, D)
        WF = WD[:, self.follow_index]
        cols = np.arange(D)[None, :]
        wq = np.array([w for _, w in self.react], float)
        none = open_worth is not None and open_roll is not None

        def open_of(hs):
            # `open_worth` of whole-attack indices, and of a catch in his roll of the opening by the
            # opening alone (`_mid` 'open': `open_mid`, else that geometry's own, as before version 25).
            out = {}
            std = [h for h in hs if h < self.size]
            if std:
                out.update(zip(std, np.atleast_1d(open_worth(np.array(std, int))).tolist()))
            for h in (h for h in hs if h >= self.size):
                _, g, move, s, tc, _ = self.plays[h - self.size]
                out[h] = float(open_mid(g, move, s, tc)) if open_mid is not None else \
                    float(np.atleast_1d(open_worth(np.array([g], int)))[0])
            return out
        rows, M = [], []
        for fi, moves in enumerate(self.families):
            sh = shapes[fi]
            caught, sid = sh['caught'], sh['sid']
            sidc = np.maximum(sid, 0)
            reached = (sid >= 0) & sh['R'][sidc]
            SV = self._stage_values(sh, WF, WD)
            val = np.where(caught >= 0, WD[cols, np.maximum(caught, 0)], np.where(reached, SV[sidc, cols], 0.0))
            pun = np.where(caught >= 0, 0.0, np.where(sid >= 0, sh['PP'][sidc] + sh['U'][sidc] * sh['own'],
                                                      sh['own']))
            net = val - pun * PUNISH_HP
            n = len(net)
            prow = np.arange(n)
            suf = np.empty((n, D), int)
            suf[:, D - 1] = D - 1
            for j in range(D - 2, -1, -1):
                suf[:, j] = np.where(net[:, j] >= net[prow, suf[:, j + 1]], j, suf[:, j + 1])
            lo, a = sh['lo'], sh['alive']
            row = []
            if none:
                oh = sh['open_hit']
                hs = sorted({h for h, _ in sh['early']} | set(oh[oh >= 0].tolist()))
                omap = open_of(hs) if hs else {}
                v = sum(x * omap[h] for h, x in sh['early'])
                vi = np.array([omap[h] if h >= 0 else 0.0 for h in oh.tolist()])
                pi = np.zeros(n)
                for i in range(n):
                    if oh[i] < 0 and sh['ks'][i] >= 0 and math.isfinite(sh['starts'][i]):
                        # His roll as he made it, from where it started (`_shape` 'rolls', version 27).
                        rm, ro = sh['rolls'][i] if sh['rolls'][i] is not None else (moves[int(sh['ks'][i])], None)
                        pi[i] = float(self._shares(rm, sh['starts'][i], dist, open_roll, ro))
                row.append(v + a * float(sh['w'] @ vi) - a * float(sh['w'] @ pi) * PUNISH_HP)
            for dr in cols_d:
                seen = (lo >= 0) & (sh['seen_at'] <= self.delays[dr] + 1e-9)
                idx = np.where(seen, suf[prow[:, None], np.maximum(lo, 0)], dr)
                fv = (val[prow[:, None], idx] * wq).sum(axis=1)
                fp = (pun[prow[:, None], idx] * wq).sum(axis=1)
                # A press the opening caught: the attacker presses for that caught defender's escape,
                # as in the stand row (`_CaughtRows`, version 26).
                fv = stand_of.rows(sh, fv, dr)
                row.append(stand_of.early(sh, WD, dr) + a * float(sh['w'] @ fv)
                           - a * float(sh['w'] @ fp) * PUNISH_HP)
            rows.append(f"roll:{moves[0]['anim']}")
            M.append(row)
        if stand is not None:
            # What lands on him caught standing, per column: the attacker presses at its delay unless
            # he saw the caught defender leave before then (`_stand_values`, version 25).
            sv = self._stand_values(stand_escapes, cols_d) if stand_escapes is not None \
                else [float(WD[dr, stand]) for dr in cols_d]
            row = [float(open_worth(np.array([stand]))[0])] if none else []
            row += sv
            rows.append('stand')
            M.append(row)
            if run_floor is not None and strafe_share > 0 and self.strafe_ok():
                e = strafe_share * self.strafe_escape(dist, run_floor)
                srow = [(1 - e) * row[0]] if none else []
                for ci, dr in enumerate(cols_d):
                    # Free and unhurt, he meets the follow-up as `_late` meets a press nothing of his
                    # plan covers: pressed `d` late it is his floor `d` earlier, read from the press.
                    st = self._stage(0, dist, _grid(floor - self.delays[dr]), False, self.press)
                    free = float(st[0] @ WF[dr]) + sum(x * WD[dr, pid] for pid, x in self._resolve(st[4], dr).items()) \
                        - st[2] * PUNISH_HP if st[3] else 0.0
                    srow.append(e * free + (1 - e) * sv[ci])
                rows.append('strafe')
                M.append(srow)
        M = np.array(M, float)
        value, x, y = zero_sum_game(M)
        fixed = min(M[i][1 if none else 0] for i, r in enumerate(rows) if r.startswith('roll:'))
        return {'value': value, 'rows': rows, 'cols': (['none'] if none else []) + [float(self.delays[d]) for d in cols_d],
                'M': M, 'x': x, 'y': y, 'fixed': fixed}


class _CaughtRows:
    """A follow-up's roll rows' catches by the opening (`FollowupModel.outcome` and `mixup`, version
    26): the stand row's rule (`FollowupModel._stand_values`) applied to every catch of the opening,
    the attacker pressing at the column's delay unless he saw that caught defender's escape in time,
    then at the delay that lands most against it. `catch_escapes(column)` gives, per catch column (a
    whole-attack index or an 'open' play), `CaughtModel.escape_table` of it at each delay, or None
    (then the column's worth stands, as before). Kept per column."""

    def __init__(self, model, catch_escapes):
        self.model, self.ce, self.kept = model, catch_escapes, {}

    def values(self, col):
        """Per default delay, what lands on the defender that catch holds, so pressed; None when
        `catch_escapes` gives no table."""
        if self.ce is None:
            return None
        if col not in self.kept:
            f = self.ce(col)
            self.kept[col] = None if f is None else self.model._stand_values(f, list(range(len(self.model.delays))))
        return self.kept[col]

    def rows(self, sh, fv, dr):
        """`fv` (per press, what lands at default delay `delays[dr]`) with each press the opening's last
        burst caught priced so."""
        import numpy as np
        if self.ce is None:
            return fv
        out = fv
        for i in np.nonzero(sh['open_hit'] >= 0)[0].tolist():
            sv = self.values(int(sh['open_hit'][i]))
            if sv is not None:
                if out is fv:
                    out = np.array(fv, float)
                out[i] = sv[dr]
        return out

    def early(self, sh, WD, dr):
        """What the opening's catches before its last reaching burst land at default delay `delays[dr]`
        (`_shape` 'early'), each priced so."""
        tot = 0.0
        for h, x in sh['early']:
            sv = self.values(h)
            tot += x * (sv[dr] if sv is not None else WD[dr, h])
        return tot


#: A strafing defender's speed (`FollowupModel.strafe_escape`): the run clip's root motion, 4.01 m/s
#: (`MEASURED`, `er-mechanics-neutral` selftest: walk 1.50, run 4.01, sprint 6.04). That a locked-on
#: defender strafes at the run clip's speed is `INFERRED` (the lock-on directional clips are not read).
STRAFE_SPEED_MPS = 4.01
#: Real frames a strafe path covers (`strafe_motion`); past it he stands, as after a dodge clip.
STRAFE_FRAMES = 150


def strafe_motion(side=1.0, speed=None):
    """A sideways walk as an evasion move (`evasion_motion`'s keys): `path` straight to his right
    (`side` 1) or left at `speed` m/s from his first frame, no invincibility, and every cancel at
    once (he is walking, not committed to a clip). Cached per side and speed."""
    v = STRAFE_SPEED_MPS if speed is None else float(speed)
    key = ('strafe', float(side), v)
    if key not in _MOTION:
        path = [(side * v * f / TAE_FPS, 0.0) for f in range(STRAFE_FRAMES)]
        _MOTION[key] = {'anim': f'strafe{"+" if side > 0 else "-"}{v:g}', 'iframes': 0.0, 'ready': 0.0, 'dodge': 0.0,
                        'move': 0.0, 'path': path, 'distance': round(v * STRAFE_FRAMES / TAE_FPS, 2)}
    return _MOTION[key]


def zero_sum_game(M):
    """(value, row mix, column mix) of the zero-sum game whose column player wins `M[r, c]` and
    whose row player loses it: the column player maximises, the row player minimises. Solved
    exactly as the row player's linear programme max sum(z) s.t. Q^T z <= 1, z >= 0 on Q = M
    shifted above 0 (z = his mix over the value), by the simplex method with Bland's rule (no
    cycling); the column mix is its dual."""
    import numpy as np
    M = np.asarray(M, float)
    m, n = M.shape
    shift = float(M.min()) - 1.0
    T = np.zeros((n + 1, m + n + 1))
    T[:n, :m] = (M - shift).T
    T[:n, m:m + n] = np.eye(n)
    T[:n, -1] = 1.0
    T[n, :m] = -1.0
    basis = list(range(m, m + n))
    eps = 1e-12
    for _ in range(100 * (n + m)):
        neg = np.nonzero(T[n, :-1] < -eps)[0]
        if not len(neg):
            break
        j = int(neg[0])
        col = T[:n, j]
        pos = col > eps
        if not pos.any():
            raise ValueError('zero_sum_game: unbounded programme')
        ratio = np.where(pos, T[:n, -1] / np.where(pos, col, 1.0), np.inf)
        cand = np.nonzero(ratio <= ratio.min() + 1e-12)[0]
        i = int(min(cand, key=lambda r: basis[r]))
        T[i] /= T[i, j]
        for r in range(n + 1):
            if r != i and T[r, j] != 0.0:
                T[r] -= T[r, j] * T[i]
        basis[i] = j
    total = T[n, -1]
    x = np.zeros(m)
    for r, b in enumerate(basis):
        if b < m:
            x[b] = T[r, -1]
    y = T[n, m:m + n].copy()
    vq = 1.0 / total
    return vq + shift, x * vq, y * vq


# ---------------------------------------------------------------------------------------------
# a caught defender (bd er-effects-rs-doax, er-effects-rs-n7dd)
#
# `sequence_outcome`'s `hit_value` credits a defender caught by one hit with every later hit: he
# never leaves and every later burst reaches him wherever it lands. `CaughtModel` plays him out
# instead, on what the ash model already measures for a caught defender (section 15, the
# `skill_landing` rules):
#   * he stands at the situation's distance when the catching hit first touches a defender who
#     stands there (`INFERRED`: a defender caught mid-roll is placed so too);
#   * each hit that lands applies its poise (`poise`, cumulative, refilled on a break, against the
#     defender's own: since version 21 each pool build's own, `computed.poise.altered` (Bull-Goat's
#     Talisman) else `original`, the plays mixed by the builds' shares, where until then every
#     defender held with the median `original`, 62 at RL 150; the pool's own median is 68, and 14.3%
#     of it holds above Savage Claws' 108-poise first claw, `MEASURED` 2026-11-16) and it breaks
#     him when it takes him to 0 or below (`>=`, `FUN_140486bf0`), and plays its damage level's reaction (`caught_branch`: the poise-break
#     level, or SpEffect 6352's while poise holds, `er-mechanics-frame-advantage.reaction_level`);
#     a reaction that locks carries him back by `displacement_fn` (knockback, `VERIFIED`, plus the
#     damage clip's root motion, `MEASURED`) and holds him until its roll cancel (DamageCount
#     chaining included) or its move cancel (`TAE` damage clips, `reaction`); level 0 (the
#     additive flinch) neither holds nor moves him and leaves a running reaction running. Two hits
#     in one update are one reaction (`_update`);
#   * when the reaction lets him he leaves: by a medium roll (`roll_motion`, its invincibility
#     included) from its roll cancel and then, from the roll's move cancel (f22; its dodge cancel,
#     f21, until version 21), at run speed the way it carried him, or at run speed
#     (`er-mechanics-neutral.frames_per_metre`) from its move cancel, straight back or straight to
#     a side (`_escapes`: the roll that ends furthest back, each roll that ends more to a side than
#     back, and the three runs), whichever lands the smaller share of the attack's hits. Until
#     2026-11-14 he left straight back only, which kept him inside a lingering line laid along the
#     attack axis for as long as it lasted (Ghostflame Ignition's R1 trail, `_escapes`);
#   * a later hit lands only where its own geometry touches him on that path (held back by his
#     body as `_catches` holds it); a hit list that re-hits every `dmgHitRecordLifeTime`
#     (`CAUGHT` `lists`, the `full` rule's ticks) lands each tick only while a sample of its
#     geometry within one update of the tick touches him;
#   * a list whose bullets are its own (`rows`, over the geometry's `B`: a bullet tree is one
#     geometry) starts at the first touch of its own samples, not of the geometry's: when the
#     geometry catches him only the lists live at that frame land, a list whose bullets have
#     all ended by then never lands, and one whose bullets come later lands where they touch him
#     on his path, its own poise and reaction applied then (2026-11-16, the user: "I'm quite
#     curious if the ranking for rosus summons model correctly accomodates for the projectiles
#     since they hit at different times". Rosus's Summons' three 2 m bursts, Bullet 2872 ->
#     2873 -> 2874, each `life` 0.23 s and `isPenetrateChr` 1 with no shared record, landed all
#     three on the frame the first touched him, with one burst's poise and one reaction);
#   * his own whiff's recovery holds him (2026-11-16, version 21; the user: "So you're saying that
#     the opponent has to swing, and wiff before your first claw attack lands, and all the other 5
#     will also land?"). The punish situation's two floors (`er-mechanics-spells.punish_situations`:
#     the real frames from the end of his whiff's last hit window, which is the punish's frame 0,
#     to the whiff's dodge cancel, and to its move cancel or the dodge cancel, whichever is later)
#     are the same floors `dodge_presses` keeps his dodge behind. A catch before a floor finds him
#     still in that recovery: he leaves by a roll no sooner than the dodge floor and by a run no
#     sooner than the move floor (the behavior script starts a walk or a run only through the
#     move-cancel check, env 2000, `VERIFIED` per `punish_lead`), standing where he is until then,
#     and the hits that come meanwhile land where their geometry touches him there. Level 0 leaves
#     it running: `ExecAddDamage` returns false and the caller's state is not changed
#     (frame-advantage.md section 1, `COMMUNITY`). A reaction that locks him starts a damage state
#     (`W_DamageLv<n>`, the same source), which ends the recovery: from then on its own cancels
#     release him, the floors no longer. The floors are the caller's, after the attacker's run in has
#     used them up (`net_parts`' `choose`); None (the strike's pricing, the selftests' plays) holds
#     him to nothing, as before. Until version 21 a flinch freed him on the catching frame, so a
#     defender caught at f17 by Savage Claws' first claw with 41 frames of recovery left rolled out
#     at f17;
#   * he may jump out when a hit is `AIR` (version 21), as a dodger may (`sequence_outcome`'s
#     families): the jump in place (`jump_motion`), its invincibility only against `AIR` samples
#     (`_catches`' rule), through the roll's gate (`INFERRED`), then running back from its landing;
#   * what one catch lands is capped at that defender's own health (`worth_of` `hp`: each pool
#     build's health with its poise, per run of poises that play alike, version 21);
# `INFERRED`: lists of one geometry live at the same frame start together and react as the one
# with the most poise; a list without `rows` (a melee window, a list of a record shared down the
# lineage, a list whose bullets home or are aimed at him and move after they are made, which
# follow him where the stored samples, the flight against him standing, do not: Stars of Ruin's
# stars) is read on every sample of its geometry, as before (Ghostflame Ignition's hop explosion
# with its trail);
# the instances of one list (a fan, interval children) tick together; the knockback left when he
# cancels into his escape is dropped; the knockback itself is straight back.
#: Real frames of a hit's displacement track (`caught_branch`); every track is flat well before.
CAUGHT_FRAMES = 150
#: Half-width, in real frames, of the window a tick looks for a touching sample in: one update.
CAUGHT_TICK_TOL = 0.5
_BRANCH = {}


def caught_branch(t, atk_row, broken):
    """What one hit of AtkParam `atk_row` does to a caught defender whose poise it breaks
    (`broken`) or not: {'level', 'locks', 'counts', 'move' (real frames to his move cancel), 'roll'
    (to his roll cancel at DamageCount 1-4), 'len', 'track' (metres he has been carried back, per
    real frame from the hit: `displacement_fn`)}. A level whose reaction clips are missing is
    taken not to hold him (`INFERRED`). Cached per row and branch."""
    key = (atk_row, broken)
    if key not in _BRANCH:
        fa = _pose()[2]
        ft = _fa_tables(t)
        lv = fa.reaction_level(ft, atk_row, broken) if atk_row in ft.atk else 0
        r = _reaction(lv)
        move = displacement_fn(t, lv, atk_row)
        track = [round(move(k / TAE_FPS), 4) for k in range(CAUGHT_FRAMES + 1)]
        if r is None or not r.get('locks'):
            out = {'level': lv, 'locks': False, 'counts': False, 'move': 0.0, 'roll': (0.0,) * 4, 'len': 0.0,
                   'track': track}
        else:
            out = {'level': lv, 'locks': True, 'counts': bool(r['counts']), 'move': r['move'],
                   'roll': tuple(r['roll'].get(c) for c in (1, 2, 3, 4)), 'len': r.get('len'), 'track': track}
        _BRANCH[key] = out
    return _BRANCH[key]


def stun_branch():
    """What a bleed or frost proc makes a caught defender play on a hit whose own reaction is level
    0 (poise held, hyperarmor included): HKS `ExecDamage` raises the hit to `DAMAGE_LEVEL_SMALL` when
    the bleed or frost clear-flag bit is set (`COMMUNITY`, the constants in the installed
    `c0000.hks`; `er-mechanics-proc-opening.proc_reaction`, which decides when, docs/er-mechanics/
    status.md section 11). In `caught_branch`'s shape, level 1: the `a000_0051xx` clips' cancels
    (`er-mechanics-frame-advantage.reaction(1)`: roll at 10 / 7 / 4 / 0 for the 1st to 4th stagger
    in a row, move 15, length 34, `MEASURED` from the TAE), counted toward DamageCount as every
    level-1 stagger is. The track is the clip's root motion alone: the knockback is the engine's,
    read from the hit's own damage level (`ChrIns::GetDamageLevel`, level 0, none), not the clip the
    script promotes it to (`INFERRED`). None when the level's clips are missing."""
    key = 'stun'
    if key not in _BRANCH:
        r = _reaction(1)
        root = _clip_root_back(1)
        track = [round(root(k / TAE_FPS), 4) if root else 0.0 for k in range(CAUGHT_FRAMES + 1)]
        _BRANCH[key] = None if r is None or not r.get('locks') else {
            'level': 1, 'locks': True, 'counts': bool(r['counts']), 'move': r['move'],
            'roll': tuple(r['roll'].get(c) for c in (1, 2, 3, 4)), 'len': r.get('len'), 'track': track}
    return _BRANCH[key]


def caught_list(t, atk_row, poise, n=1, rec_s=0.0, weight=1.0, rows=None):
    """One hit list of a geometry's `CAUGHT` record: `n` hits (the `full` count of one instance),
    `rec` real frames apart (`dmgHitRecordLifeTime`), `w` the share of the geometry's worth one of
    its hits carries (a geometry's lists sum n x w to 1), `poise` per hit (menu units x
    `saRate`), and its two reaction branches (`caught_branch`). `rows`: the Bullet rows whose
    samples are this list's (the geometry's `B`, `er-mechanics-spells.SpellModel.flight`), so
    `CaughtModel` lands it when its own bullets touch him; None: every sample of the geometry."""
    return {'n': int(max(1, n)), 'rec': float(rec_s) * TAE_FPS, 'w': float(weight), 'poise': float(poise or 0.0),
            'atk': atk_row, 'react': {True: caught_branch(t, atk_row, True), False: caught_branch(t, atk_row, False)},
            'rows': None if not rows else tuple(sorted(int(b) for b in rows))}


def caught_shape(g):
    """What `CaughtModel` reads of a geometry besides its arrays and its worth weights: one tuple
    per list (`n`, `rec`, `poise`, `atk`, and its `rows` when it has them), None without a `CAUGHT`
    record."""
    c = g.get('CAUGHT') if g is not None else None
    return None if c is None else [(l['n'], round(l['rec'], 4), round(l['poise'], 4), l['atk'])
                                   + ((l['rows'],) if l.get('rows') and 'B' in g else ()) for l in c['lists']]


# A bullet chain split per list (bd er-effects-rs-fse5). The outcomes price a catch by the geometry
# that makes it (`reaction_outcome`'s `hit_value[h]`, attack-similarity's `OutcomeForm` per
# geometry), and `CaughtModel` plays geometry h's catch from its first touch on a defender who
# stands. One geometry holding a chain of lists, each list's bullets made on the frame the last
# one's end (Rosus's Summons: Bullet 2872 -> 2873 -> 2874, `HitBulletID` at expiry, 0.25 s apart at
# one spot), so credited a dodge that escapes the first burst and is first touched by a later one
# with the play from the first burst's standing touch: all three bursts (`MEASURED` 2026-11-16, the
# issue: a quarter of the dodge presses at floor 50, 4.5-5.5 m, are first touched by burst 2 or 3).
# `split_chains` makes such a geometry one geometry per list, so which burst first touches him is
# the catching geometry, in `reaction_outcome` and `OutcomeForm` alike, with no change to either:
#   * a chain is every list with its own `rows` over the geometry's `B`, each sample one list's,
#     and in time order each list's last sample on the frame of the next one's first (`_live`'s
#     reading: the frame a bullet ends on is its child's first). Lists live together, a list without
#     `rows` (a melee window, a record's, a list whose bullets home or are aimed at him after they are
#     made: Stars of Ruin's) or a gap between two lists keep the geometry whole, as before. A gap
#     would make the parts separate bursts (`er-mechanics-spells._bursts`), each dodged on its own,
#     which the whole geometry never was;
#   * each part keeps its frame, its `wf` and its list's `w`, so the worth weights sum as before
#     (`CaughtModel.weights`), and every other key of the geometry (`HPR`, `AIR`, `TOUGHNESS`,
#     `GUARD`, `EVENT`); a `HOMING` record keeps the shots of its own rows (its part's `rows`,
#     `_part_keys`: each flight's shots of those rows, whatever slots its interval children take),
#     so the flight again is one flight for all of them (`_homing_entries` keys it with no `index`
#     or `rows`); a part none of whose shots in the flight with no target is flown again keeps its
#     stored samples. Until 2026-11-19 the part kept the lineage keys of the flight with no target
#     (`fly_shots`), which name every shot of a flight against a dodge only while its interval rows
#     make no more children than that flight's and, past `BULLET_INTERVAL_CAP`, the same number;
#   * `CHAIN` (i, n, first frame, the next part's first frame or inf) marks a part. The frame two
#     parts share belongs to the later (`_catches_read` gives it a tie there; a part that has not
#     caught him does not catch on it in `CaughtModel._play`), and the escape a caught defender takes
#     weighs the parts of one chain as the lists of the one geometry were (`CaughtModel._best`).
# `INFERRED`: the parts keep the whole geometry's `AIR` (all its AtkParams' mark; the jump family
# stays as it was).


def _index_flown(t, bt, dmg, index):
    """Whether a `homing_part` of tree `bt` holding the shots `index` (lineage keys) is flown again:
    `homing_part`'s rule on its `dmg` (the hitting Bullet ids)."""
    for key in index:
        nodes = _key_nodes(bt, key)
        if nodes and nodes[-1]['bullet'] in dmg and any(
                homes(t.bullet.get(n['bullet']) or {}) or carrier(t, n) for n in nodes):
            return True
    return False


def _chain_parts(t, g):
    """`g` split into one geometry per list when its `CAUGHT` lists are a chain (above), else None."""
    import numpy as np
    c = g.get('CAUGHT') if g is not None else None
    if c is None or len(c['lists']) < 2 or 'CHAIN' in g or 'B' not in g:
        return None
    ls = c['lists']
    T = np.asarray(g['T'], float)
    B = np.asarray(g['B'])
    if len(B) != len(T) or np.isnan(T).any() or not all(l.get('rows') for l in ls):
        return None
    masks = [np.isin(B, l['rows']) for l in ls]
    if any(not m.any() for m in masks) or not (np.sum(masks, axis=0) == 1).all():
        return None
    order = sorted(range(len(ls)), key=lambda i: (float(T[masks[i]].min()), i))
    lo = [float(T[masks[i]].min()) for i in order]
    hi = [float(T[masks[i]].max()) for i in order]
    if any(abs(hi[k] - lo[k + 1]) > 1e-6 for k in range(len(order) - 1)):
        return None
    spec = g.get('HOMING')
    if spec is not None and len(spec['parts']) != 1:
        return None
    entries = None
    if spec is not None:
        p = spec['parts'][0]
        entries = fly_shots(t, p['bt'], None, p['launch'])
    n = len(order)
    arrays = [k for k, v in g.items() if isinstance(v, np.ndarray) and v.shape[:1] == T.shape]
    out = []
    for k, i in enumerate(order):
        m = masks[i]
        sub = {**g, **{a: np.asarray(g[a])[m] for a in arrays}}
        sub['CAUGHT'] = {'lists': [ls[i]], 'wf': c['wf']}
        sub['CHAIN'] = (k, n, lo[k], lo[k + 1] if k + 1 < n else math.inf)
        if spec is not None:
            p = spec['parts'][0]
            rows = set(ls[i]['rows'])
            index = [key for e, key in zip(entries, entries.keys) if e[0]['bullet'] in rows]
            if p['index'] is not None:
                index = [key for key in index if key in set(p['index'])]
            sub.pop('HOMING')
            if index and _index_flown(t, p['bt'], p['dmg'], index):
                # Named by its rows (`_part_keys`): the keys read here are the no-target flight's,
                # and a flight against a dodge keys its interval children by its own count.
                sub['HOMING'] = homing_record(t, [{**p, 'rows': tuple(sorted(rows))}])
        out.append(sub)
    return out


def split_chains(t, geo):
    """`geo` ([(contact geometry, frame)], None) with every geometry whose `CAUGHT` lists are a
    bullet chain split into one geometry per list (the comment above), each at the whole one's
    frame; the others as they were. A list given is changed in place (the callers keep it) and
    returned."""
    if not geo:
        return geo
    out = []
    for g, fr in geo:
        parts = _chain_parts(t, g)
        out += [(p, fr) for p in parts] if parts else [(g, fr)]
    if isinstance(geo, list):
        geo[:] = out
        return geo
    return out


def hp_cap(x, hp):
    """What a catch worth `x` leaves on a defender of health `hp`: `x` for None, min(x, hp) for a
    number, and for a pool [(health, share)] the shares' mean of each build's min (version 21: the
    cap is not linear, so the median health's cap is not the pool's)."""
    if hp is None:
        return x
    if not isinstance(hp, (list, tuple)):
        return min(x, hp)
    return sum(w * min(x, c) for c, w in hp)


#: Directions a caught defender runs off in (`_escapes`), (x, z) per metre, +z away from the
#: attacker: straight back, then straight to either side.
ESCAPE_RUNS = ((0.0, 1.0), (1.0, 0.0), (-1.0, 0.0))


def _escapes():
    """The ways a caught defender leaves (`CaughtModel`), (name, roll motion or None, the (x, z)
    he runs on in after it): ('roll', the medium roll that ends furthest back, back), ('run',
    None, back), then each other medium roll that ends further to a side than back, running on
    the way it carried him, and ('run', None, each side). The sides are 2026-11-14: with only the
    two back escapes a defender caught in a lingering line laid along the attack axis stayed in it
    while he left, as far as it reached. Ghostflame Ignition's R1 follow-up lays its 5 s ghostflame
    trail (Bullet 2294) 2.8 to 19.7 m ahead, and a defender caught 2.6 m out took 9 of its 10 ticks
    (`MEASURED`). The four medium-roll root motions are `roll_motion`'s (`TAE`): the back roll ends
    3.6 m back with no sideways travel, the side rolls 3.3 and 3.4 m to the sides. That he takes
    whichever lands the least is this model's rule for the back roll and the run already
    (`INFERRED`)."""
    rolls = roll_motion()

    def end(m):
        return m['path'][min(len(m['path']) - 1, int(m['dodge'] or len(m['path'])))]
    back = max(rolls, key=lambda m: end(m)[1]) if rolls else None
    out = ([('roll', back, ESCAPE_RUNS[0])] if back else []) + [('run', None, ESCAPE_RUNS[0])]
    for m in rolls:
        x, z = end(m)
        if m is not back and abs(x) > abs(z):
            out.append(('roll', m, (x / math.hypot(x, z), z / math.hypot(x, z))))
    return out + [('run', None, u) for u in ESCAPE_RUNS[1:]]


#: What a `CaughtModel` keeps of its `escape_table`s, {(id(value), id(geo)): (value, geo, {(dist, h,
#: held, the cap's key): the tables})} (the value and geometries kept so their ids name them): at
#: most `ESCAPE_VALUES` values (a shifted value holds its shifted geometries alive) and
#: `ESCAPE_TABLES` tables each; and of its capped sums (`_cap_sum`). Each emptied when full.
#: `ESCAPE_TABLES` was 4096 until 2026-11-18: a follow-up form keeps one model per press delay, and
#: Waves of Darkness then R2 f68's 28 models held 1,507 MB of tables after 218 of the 3,483
#: situations (`MEASURED`, `target/wf/waves/exact.py` with `EXACT_SIZES`, every 16th situation),
#: on the way to a bound of 28 x 2 x 4096 tables of 23 KB, 5.3 GB; the full set's run reached its
#: 10 GiB cap after 2,848 s of CPU with two of its six forms read (`target/wf/waves/a1`; a form
#: holds its models until its key's last line that reads it). A table is asked for again only
#: within one distance (`dist` is in its key and `FollowupForm` walks the distances once per pass);
#: the set's second mixup pass read 41,262 tables in its last 4,781 of 5,514 situation keys, over 94
#: distances and 28 models: under 20 per model and distance. A table read again after the memo was
#: emptied is the same table read afresh.
ESCAPE_VALUES = 2
ESCAPE_TABLES = 512
CAP_SUMS = 200000
#: `FollowupModel._stand_values`' answers a model keeps (emptied when full); an entry is its tables'
#: ids, weak references and the answer, a few kilobytes.
STAND_VALUES_KEPT = 4096


class CaughtModel:
    """A defender caught by one geometry of an attack, played out (the rules above). `geo` is
    [(contact geometry, frame)], every geometry with a `CAUGHT` record ({'lists': [`caught_list`],
    'wf': its share of its frame's worth}); `poise` is the defender's, or the pool's [(poise,
    share)], each played and the plays mixed by share (`_best`). `landed(dist)` is, per
    catching geometry h (in `_bursts` order), the share of each list's hits that land [h][g][l];
    `worth(dist, value)` what that leaves on him per catching geometry, from {frame: worth} (default
    the `value` given here) and each geometry's weights (`weights`, default this `geo`'s). Each
    takes `floors`, (his dodge floor, his move floor) on the attack's clock or None (the rules
    above: his whiff's recovery). Cached per distance and the floors that hold him (`held`)."""

    def __init__(self, geo, poise, value=None):
        import numpy as np
        order = sorted(geo, key=lambda e: e[0]['T'].min())
        self.geoms = [g for g, _ in order]
        self.frames = [fr for _, fr in order]
        # The poises he holds with, [(poise, share)] ascending (version 21: each pool build's own,
        # `er-mechanics-spells.SpellModel.defender` `poise_pool`); a number is one at share 1.
        # An entry (poise, share, health) carries that build's health too (`SpellModel.defender`
        # `pool`): a cap on what one catch takes (`worth_of` `hp`) is then each build's own.
        if isinstance(poise, (int, float)):
            poise = [(float(poise), 1.0)]
        total = float(sum(e[1] for e in poise))
        merged, self.pool_hp = {}, {}
        for e in poise:
            if e[1] <= 0:
                continue
            p, w = float(e[0]), float(e[1]) / total
            merged[p] = merged.get(p, 0.0) + w
            if len(e) > 2 and e[2] is not None:
                self.pool_hp.setdefault(p, []).append((float(e[2]), w))
        self.pool = sorted(merged.items())
        if self.pool_hp and len(self.pool_hp) != len(self.pool):
            raise ValueError('CaughtModel: a pool gives the health of some builds only')
        self.poise = self.pool[0][0] if len(self.pool) == 1 else None
        self.value = value
        self.lists = [g['CAUGHT']['lists'] for g in self.geoms]
        # Each geometry's hit priority record (`HPR`: (key, priority, created)) or None.
        self.hpr = [g.get('HPR') for g in self.geoms]
        # A chain part (`split_chains`): the frame it shares with the next part, which is the next
        # part's (inf for none), and its share of the escape's score (the whole geometry's lists'
        # mean, `_best`): 1 / its chain's parts.
        self.ends = [g['CHAIN'][3] if g.get('CHAIN') is not None else math.inf for g in self.geoms]
        self.share = [1.0 / g['CHAIN'][1] if g.get('CHAIN') is not None else 1.0 for g in self.geoms]
        # Every geometry's samples, each sorted by time, end to end: one touch test serves them all.
        cols = {k: [] for k in ('T', 'X', 'Z', 'R', 'PK', 'TL')}
        self.bounds, at = [], 0
        # Per geometry, per list: which of its time-sorted samples are the list's own (`rows` over
        # the geometry's `B`), None for all of them, and the span of real frames they cover.
        self.masks, self.spans = [], []
        for g, ls in zip(self.geoms, self.lists):
            o = np.argsort(g['T'], kind='stable')
            for k in ('T', 'X', 'Z', 'R', 'PK'):
                cols[k].append(np.asarray(g[k], float)[o])
            cols['TL'].append(np.asarray(g['TL'] if 'TL' in g else g['T'], float)[o])
            self.bounds.append((at, at + len(o)))
            at += len(o)
            B = np.asarray(g['B'])[o] if 'B' in g and len(g['B']) == len(o) else None
            T = np.asarray(g['T'], float)[o]
            ms = [None if B is None or not l.get('rows') else np.isin(B, l['rows']) for l in ls]
            ms = [None if m is None or m.all() or not m.any() else m for m in ms]
            self.masks.append(ms)
            self.spans.append(None if all(m is None for m in ms) else
                              [(float(T[0]), float(T[-1])) if m is None else (float(T[m][0]), float(T[m][-1]))
                               for m in ms])
        for k, v in cols.items():
            setattr(self, k, np.concatenate(v) if v else np.zeros(0))
        self.any_tl = any('TL' in g for g in self.geoms)
        # The attacker's facing as he tracks the caught defender (version 27, `_facing`'s rule): per
        # clip turn profile, the samples that read it and the frame each takes its facing from
        # (`_turn_when`); a geometry with no profile reads none.
        self.turns = []
        groups = {}
        for gi, g in enumerate(self.geoms):
            tr = g.get('TURN')
            if tr is None:
                continue
            k = (tr['sig'], float(tr['t0']))
            if k not in groups:
                groups[k] = len(self.turns)
                self.turns.append((tr, [], []))
            a, b = self.bounds[gi]
            o = np.argsort(g['T'], kind='stable')
            self.turns[groups[k]][1].append(np.arange(a, b))
            self.turns[groups[k]][2].append(np.asarray(_turn_when(g, o), float))
        self.turns = [(tr, np.concatenate(ix), np.concatenate(tq)) for tr, ix, tq in self.turns]
        self.push = _pose()[1].PUSH_CAPSULE_RADIUS
        self.mps = 1.0 / _neutral().frames_per_metre()
        self.escapes = _escapes()
        # Per sample, whether its geometry is `AIR` (a jump's invincibility holds only against such
        # a hit, `_catches`). With one, the jump is an escape too, as it is a dodge
        # (`sequence_outcome`'s families): in place, then running back from its landing (version 21).
        self.AIRS = np.concatenate([np.full(len(g['T']), bool(g.get('AIR'))) for g in self.geoms]) \
            if self.geoms else np.zeros(0, bool)
        if self.AIRS.any():
            self.escapes = self.escapes + [('jump', jump_motion(), ESCAPE_RUNS[0])]
        self._roll = {}
        for _, roll, _ in self.escapes:
            if roll is not None:
                path = np.array(roll['path'], float)
                # He runs on from the roll's move cancel (f22 against its dodge cancel f21): the
                # behavior script starts a run only through the move-cancel check (env 2000,
                # `VERIFIED`, `punish_lead`); until version 21 he ran from the dodge cancel.
                go = max(float(roll['move']), float(roll['dodge'] or 0.0)) if roll.get('move') is not None \
                    else float(roll['dodge'] or len(path) - 1)
                self._roll[id(roll)] = (path[:, 0], path[:, 1], np.arange(len(path), dtype=float),
                                        min(go, len(path) - 1))
        # One geometry whose every list hits once: whoever it catches takes it all, nothing later.
        self.trivial = len(self.geoms) == 1 and all(l['n'] == 1 for l in self.lists[0]) and self.spans[0] is None
        self._landed = {}
        self._mixed = {}
        self._weights = {}
        self._wmemo = {}
        self._tc = {}                         # (dist, h) -> the frame geometry h catches him standing
        self._dodges = {}                     # anim -> the dodge move a `dodge_key` names

    @staticmethod
    def of(geo, poise, value=None):
        """A `CaughtModel` when every geometry of `geo` has a `CAUGHT` record, else None (the old
        rule then holds)."""
        if not geo or poise is None or any(g is None or 'CAUGHT' not in g for g, _ in geo):
            return None
        return CaughtModel(geo, poise, value)

    def with_value(self, value):
        """The same model (its plays shared) priced on another {frame: worth}."""
        import copy
        out = copy.copy(self)
        out.value = value
        out._wmemo = {}
        return out

    def weights(self, geo=None):
        """Per geometry in `_bursts` order: [per list: share of the frame's worth one hit carries
        times its hit count] (`wf` x `w` x `n`), from `geo` (another build's geometries on the same
        shapes) or this one's. Cached per `geo`."""
        key = id(geo)
        if key not in self._weights:
            gs = self.geoms if geo is None else [g for g, _ in sorted(geo, key=lambda e: e[0]['T'].min())]
            self._weights[key] = (geo, [[g['CAUGHT']['wf'] * l['w'] * l['n'] for l in g['CAUGHT']['lists']]
                                        for g in gs])
        return self._weights[key][1]

    def worth_of(self, dist, h, value=None, geo=None, floors=None, hp=None, dodge=None):
        """What lands on him when geometry `h` catches him at `dist`, from {frame: worth}, held by
        `floors` (`held`), capped at his health when `hp` is given: each pool build's own when the
        pool carries it (version 21), else `hp` (`hp_cap`: a number, or [(health, share)]). The cap
        is taken per run of pool poises that play alike, before they are mixed: one defender takes
        at most his own health. Kept for this model's own `value` and `geo` (`with_value` gives a
        copy its own). `dodge` (move, its start, the catch frame), version 25: `h` catches him in
        that dodge, which holds him in place of `floors` (`dodge_key`)."""
        own = value is None and geo is None
        held = self.dodge_key(*dodge) if dodge is not None else self.held(dist, h, floors)
        capk = None if hp is None else ('pool' if self.pool_hp else
                                        (tuple(map(tuple, hp)) if isinstance(hp, (list, tuple)) else float(hp)))
        key = (round(dist, 2), h, held, capk)
        if own and key in self._wmemo:
            return self._wmemo[key]
        value = self.value if value is None else value
        weights = self.weights(geo)
        s = 0.0
        for rows, w_run, ps, _ in self.runs(dist, h, held):
            x = 0.0
            for g, ws in enumerate(weights):
                v = value.get(self.frames[g], 0.0)
                for li, w in enumerate(ws):
                    x += rows[g][li] * w * v
            if hp is None:
                s += w_run * x
            elif self.pool_hp:
                s += sum(w * min(x, c) for p in ps for c, w in self.pool_hp[p])
            else:
                s += w_run * hp_cap(x, hp)
        if own:
            self._wmemo[key] = s
        return s

    def escape_table(self, dist, h, value=None, geo=None, floors=None, hp=None, dodge=None):
        """Per pool poise (`pool` order) and escape (`escapes` order) when geometry `h` catches him
        at `dist` held by `floors`, as arrays [poise, escape]: the frame that escape first starts
        (`_play` 'te', inf when he never leaves), its score (the share of the hits that land, which
        he takes the least of), and what lands, times that poise's share and capped as `worth_of`
        caps it. Summing, per poise, the entry of the escape `worth_of` takes (the least score, the
        first of equals) gives `worth_of`. What an attacker who sees him leave reads
        (`FollowupModel.mixup`'s stand row, version 25; its roll rows' catches by the opening,
        version 26). `dodge` as `worth_of`'s: the catch is in that dodge of his."""
        import numpy as np
        held = self.dodge_key(*dodge) if dodge is not None else self.held(dist, h, floors)
        value = self.value if value is None else value
        # Kept per what it reads (`ESCAPE_TABLES`): the stand row asks again for every situation
        # whose floors hold him alike. The tables are read, never written, by their callers.
        capk = None if hp is None else ('pool' if self.pool_hp else
                                        (tuple(map(tuple, hp)) if isinstance(hp, (list, tuple)) else float(hp)))
        memos = self.__dict__.setdefault('_escapes', {})
        vk = (id(value), id(geo))
        held_vg = memos.get(vk)
        if held_vg is None or held_vg[0] is not value or held_vg[1] is not geo:
            if len(memos) >= ESCAPE_VALUES:
                memos.clear()
            held_vg = memos[vk] = (value, geo, {})
        memo = held_vg[2]
        mk = (dist, h, held, capk)
        got = memo.get(mk)
        if got is None:
            if len(memo) >= ESCAPE_TABLES:
                memo.clear()
            got = memo[mk] = self._escape_table(dist, h, value, geo, held, hp)
        return got

    def _cap_sum(self, p, x):
        """`sum(w * min(x, c) for c, w in self.pool_hp[p])`, kept per (p, x, the type of x): the same
        sum of the same terms in the same order (`_caps`). The type is in the key because `sum` adds
        Python floats compensated and a float subclass (numpy's) plainly, so equal values of the two
        types may sum to different bits."""
        caps = self.__dict__.setdefault('_caps', {})
        k = (p, x, type(x))
        got = caps.get(k)
        if got is None:
            if len(caps) >= CAP_SUMS:
                caps.clear()
            got = caps[k] = sum(w * min(x, c) for c, w in self.pool_hp[p])
        return got

    def _escape_table(self, dist, h, value, geo, held, hp):
        """`escape_table` read afresh."""
        import numpy as np
        weights = self.weights(geo)
        runs = self.runs(dist, h, held)
        E = len(runs[0][3])
        P = len(self.pool)
        TE, S, C = np.full((P, E), math.inf), np.zeros((P, E)), np.zeros((P, E))
        at = {p: i for i, (p, _) in enumerate(self.pool)}
        for _, _, ps, alls in runs:
            for e, (te, score, rows) in enumerate(alls):
                x = 0.0
                for g, ws in enumerate(weights):
                    v = value.get(self.frames[g], 0.0)
                    for li, w in enumerate(ws):
                        x += rows[g][li] * w * v
                for p in ps:
                    i = at[p]
                    wp = self.pool[i][1]
                    TE[i, e], S[i, e] = te, score
                    if hp is None:
                        C[i, e] = wp * x
                    elif self.pool_hp:
                        C[i, e] = self._cap_sum(p, x)
                    else:
                        C[i, e] = wp * hp_cap(x, hp)
        return TE, S, C

    def worth(self, dist, value=None, geo=None, floors=None):
        """`worth_of` for every catching geometry, in `_bursts` order."""
        return [self.worth_of(dist, h, value, geo, floors) for h in range(len(self.geoms))]

    def lazy(self, dist, start, count, cap=None, floors=None):
        """`worth_of` for geometries `start` .. `start + count - 1` as a sequence that plays each
        catch only when it is read (what `reaction_outcome` reads of a burst's `hit_value`), each
        capped at `cap`, held by `floors`."""
        return _LazyWorth(self, dist, start, count, cap, floors)

    # -- his whiff's recovery ----------------------------------------------------------------

    def catch_at(self, dist, h):
        """The real frame geometry `h` first touches him standing at `dist` (rounded to 0.01 m),
        where `_play` starts his catch: its first sample's frame when it never touches him there."""
        key = (round(dist, 2), h)
        if key not in self._tc:
            first = _catches_each([self.geoms[h]], key[0], None, [0.0])[0][0]
            self._tc[key] = float(first) if math.isfinite(first) else float(self.geoms[h]['T'].min())
        return self._tc[key]

    def held(self, dist, h, floors):
        """(dodge floor, move floor) of `floors` (the situation's, on the attack's clock: None for
        none) that hold him when geometry `h` catches him at `dist`, each rounded to half a frame as
        the situations are, and None where it comes no later than the catch (every escape starts
        there or after, so it binds nothing); None when neither holds him. Plays and worths are kept
        under it, so every pair of floors that holds him alike shares one play."""
        if not floors or self.trivial:
            return None
        tc = self.catch_at(dist, h)
        out = tuple(None if f is None or round(f * 2) / 2 <= tc + 1e-9 else round(f * 2) / 2 for f in floors)
        return None if out == (None, None) else out

    def dodge_key(self, move, start, catch):
        """The `held` of a defender caught in his own dodge (version 25; the comment above
        `CaughtModel`): ('dodge', the move's anim, its start, the catch frame), the move kept so the
        play can read it. A start on the update grid and a catch on a sample's frame name one play."""
        self._dodges[move['anim']] = move
        return ('dodge', move['anim'], float(start), float(catch))

    def held_run(self, dist, run):
        """`run` (his move floor) rounded to half a frame where it can hold him at `dist`: after the
        frame some geometry catches him standing there (`held`); else None, which plays as any
        move floor at or before every catch. An outcome that reads no other move floor keys on it."""
        if run is None or self.trivial:
            return None
        r = round(run * 2) / 2
        return r if any(r > self.catch_at(dist, h) + 1e-9 for h in range(len(self.geoms))) else None

    # -- the play ----------------------------------------------------------------------------

    def row(self, dist, h, held=None):
        """[per geometry: [per list: share of its hits that land]] when geometry `h` catches him at
        `dist` (rounded to 0.01 m), held by `held` (`held`'s output), the best of his escapes
        (`_escapes`). Cached."""
        key = round(dist, 2)
        rows = self._mixed.setdefault((key, held), {})
        if h not in rows:
            out = None
            for got, w, _, _ in self.runs(key, h, held):
                out = [[w * x for x in r] for r in got] if out is None else \
                    [[a + w * x for a, x in zip(ra, r)] for ra, r in zip(out, got)]
            rows[h] = out
        return rows[h]

    def runs(self, dist, h, held=None):
        """[(`row` at one run of pool poises that play alike, its share, its poises, every escape's
        (first escape start, score, row) there)] (`_best`). Cached."""
        key = round(dist, 2)
        rows = self._landed.setdefault((key, held), {})
        if h not in rows:
            one = [[1.0] * len(self.lists[0])]
            rows[h] = [(one, 1.0, [p for p, _ in self.pool], [(math.inf, 1.0, one)])] if self.trivial \
                else self._best(h, key, held)
        return rows[h]

    def landed(self, dist, floors=None):
        """`row` of every catching geometry, held by `floors`."""
        return [self.row(dist, h, self.held(dist, h, floors)) for h in range(len(self.geoms))]

    def _best(self, h, dist, held=None):
        """`runs`' plays: per poise of the pool, the best of his escapes, as [(row, share, poises,
        every escape's (first escape start, score, row))]. Exact with one play per run of pool poises
        that play alike: a play compares only the poise damage it has dealt (`_play`'s `bounds`), so
        every poise in (the largest it dealt that did not break him, the smallest that did] plays
        the same, every escape's play included, and the next play starts at the first poise of the
        pool past it.

        Its plays share their touch tests (`_drawn`): every escape and every poise of one catch
        starts on the same path, and a poise that breaks him no sooner than another plays the same
        path as far as it does."""
        self._drawn = {}
        try:
            if len(self.pool) == 1:
                got, _, _, alls = self._best_at(h, dist, held, self.pool[0][0])
                return [(got, 1.0, [self.pool[0][0]], alls)]
            out, i, P = [], 0, self.pool
            while i < len(P):
                got, _, hi, alls = self._best_at(h, dist, held, P[i][0])
                w, ps = 0.0, []
                while i < len(P) and P[i][0] <= hi:
                    w += P[i][1]
                    ps.append(P[i][0])
                    i += 1
                out.append((got, w, ps, alls))
            return out
        finally:
            self._drawn = None

    def _touch_drawn(self, segs, lo=None):
        """`_touch_all(segs, lo)`, kept for the plays of one `_best` call (`_drawn`): a path is named
        by what `_pos` and `_touch_all` read of each segment (its start, place, kind and motion; the
        run it is taken on only for a run or a roll, the kinds whose motion reads it). Read, never
        written, by `_play`. 2026-11-18: one catch of Golden Land then R2 at frame 68 was played 4
        times per pool poise run, each play's first path the same."""
        drawn = self.__dict__.get('_drawn')
        if drawn is None:
            return self._touch_all(segs, lo)
        key = (lo,) + tuple((s[0], s[1], s[2], s[3], id(s[4]), s[5] if s[3] in ('run', 'roll') else None)
                            for s in segs)
        got = drawn.get(key)
        if got is None:
            got = drawn[key] = self._touch_all(segs, lo)
        return got

    def _best_at(self, h, dist, held, poise, stun=None):
        """(the best of his escapes holding with `poise`, and the span of poises every escape's play
        is the same at: (lo, hi], and per escape of `escapes` (the frame it first starts, its score,
        its row)). An escape's score is the share of the attack's hits that land, the parts of one
        chain weighed as one geometry: he takes the least (`INFERRED`). `stun` as `_play`'s."""
        best, bounds, alls = None, [-math.inf, math.inf], []
        rows = self.__dict__.setdefault('_rows', {})
        for name, roll, run in self.escapes:
            info = {}
            got = self._play(h, dist, name, roll, run, held, poise, bounds, info, stun)
            score = sum(sum(x) / len(x) * s for x, s in zip(got, self.share) if x)
            # Kept as one tuple per distinct row (`_rows`): `runs` keeps every play's rows, and the
            # plays of one attack land a few shares in many ways alike. Read, never written.
            got = tuple(tuple(r) for r in got)
            got = rows.setdefault(got, got)
            alls.append((info.get('te', math.inf), score, got))
            if best is None or score < best[0] - 1e-12:
                best = (score, got)
        return best[1], bounds[0], bounds[1], alls

    def _pos(self, segs, T, air=None):
        """(his distance ahead of the attacker's start, his offset to the side, immune) at real
        frames `T` (an array) on trajectory `segs` [(t0, base x, base z, kind, data, run)], `kind`
        'stand', 'hold' (data: the track, straight back), 'run' or 'roll' (data: the roll or jump
        motion), `run` the (x, z) he runs on in. A jump's invincibility (`air`) holds only where
        `air` (per element of `T`: its geometry is `AIR`) says so, `_catches`' rule."""
        import numpy as np
        T = np.asarray(T, float)
        starts = np.array([s[0] for s in segs], float)
        idx = np.clip(np.searchsorted(starts, T, side='right') - 1, 0, len(segs) - 1)
        z, x = np.zeros(T.shape), np.zeros(T.shape)
        immune = np.zeros(T.shape, bool)
        for j, (t0, bx, bz, kind, data, run) in enumerate(segs):
            m = idx == j
            if not m.any():
                continue
            u = np.maximum(T[m] - t0, 0.0)
            dx, dz = self._along(kind, data, u, run)
            z[m], x[m] = bz + dz, bx + dx
            if kind in ('roll', 'dodge'):
                im = u < data['iframes']
                if data.get('air'):
                    im = im & (np.asarray(air, bool)[m] if air is not None else False)
                immune[m] = im
        return z, x, immune

    def _t_lists(self):
        """Each geometry's sample times (`bounds` slices of `T`, in time order) as a list of floats,
        made once: what `_play` bisects."""
        got = self.__dict__.get('_tl')
        if got is None:
            got = self._tl = [self.T[a:b].tolist() for a, b in self.bounds]
        return got

    def _at1(self, segs, T):
        """(his offset to the side, his distance ahead) at one real frame `T` on `segs`, as floats:
        `_pos(segs, [T])`'s, each step the same operation on the one element. The segment is
        `searchsorted`'s (`bisect_right` is its binary search, step for step), `np.maximum` and
        `np.minimum` keep the first of equals as Python's `max` and `min` do (and a NaN, which no
        frame here is), and an interpolation is `np.interp` itself. Why (2026-11-21,
        .worktrees/haima): the play of a caught defender (`_play`) reads where he is at each hit
        that lands, one frame at a time, and those one-element `_pos` calls were 31% of Crystal
        Barrage's set (py-spy, target/haima/prof-cb.txt)."""
        import bisect
        import numpy as np
        T = float(T)
        j = min(max(bisect.bisect_right([s[0] for s in segs], T) - 1, 0), len(segs) - 1)
        t0, bx, bz, kind, data, run = segs[j]
        u = T - t0
        if not (u >= 0.0 or u != u):
            u = 0.0
        if kind == 'stand':
            dx = dz = 0.0
        elif kind == 'hold':
            dx, dz = 0.0, np.interp(u, np.arange(len(data)), data)
        elif kind == 'run':
            dx, dz = u * self.mps * run[0], u * self.mps * run[1]
        elif kind == 'dodge':
            path = np.asarray(data['path'], float)
            k = min(max(int(np.floor(u)), 0), len(path) - 1)
            dx, dz = path[k, 0], path[k, 1]
        else:
            px, pz, frames, end = self._roll[id(data)]
            on = u - end
            if not (on >= 0.0 or on != on):
                on = 0.0
            on = on * self.mps
            ue = u if (u <= end or u != u) else end
            dx, dz = np.interp(ue, frames, px) + on * run[0], np.interp(ue, frames, pz) + on * run[1]
        return float(bx + dx), float(bz + dz)

    def _along(self, kind, data, u, run):
        """(sideways, back) metres moved `u` real frames into a segment of `kind`."""
        import numpy as np
        if kind == 'stand':
            return np.zeros(u.shape), np.zeros(u.shape)
        if kind == 'hold':
            return np.zeros(u.shape), np.interp(u, np.arange(len(data)), data)
        if kind == 'run':
            return u * self.mps * run[0], u * self.mps * run[1]
        if kind == 'dodge':
            # The dodge he was caught in (version 25): its root motion read per real frame as
            # `_catches` read it when it found the catch (`path[floor(u)]`), still at its last
            # frame; his escape replaces it from its cancels (`_play`).
            path = np.asarray(data['path'], float)
            k = np.clip(np.floor(u).astype(int), 0, len(path) - 1)
            return path[k, 0], path[k, 1]
        px, pz, frames, end = self._roll[id(data)]
        on = np.maximum(u - end, 0.0) * self.mps
        return (np.interp(np.minimum(u, end), frames, px) + on * run[0],
                np.interp(np.minimum(u, end), frames, pz) + on * run[1])

    def _touch_all(self, segs, lo=None):
        """Per sample of every geometry (`bounds` slices, each sorted by time): whether it touches
        him on `segs`, held back by his body as `_catches` holds it. With `lo`, only the samples at
        or after real frame `lo` are tested, the others read False (`_play` reads none before its
        catch less `CAUGHT_TICK_TOL`): every test is the sample's own, so those tested read the same."""
        import numpy as np
        if lo is None:
            sel, T, AIRS, TL, PK, X, Z, R = None, self.T, self.AIRS, self.TL, self.PK, self.X, self.Z, self.R
        else:
            sel = np.nonzero(self.T >= lo)[0]
            T, AIRS, TL, PK, X, Z, R = (a[sel] for a in (self.T, self.AIRS, self.TL, self.PK, self.X, self.Z, self.R))
        z, x, immune = self._pos(segs, T, AIRS)
        if self.any_tl:
            zl, xl, _ = self._pos(segs, TL)
        else:
            zl, xl = z, x
        if self.turns and any(s[1] != 0.0 or s[3] not in ('stand', 'hold') for s in segs):
            # Each sample read in the facing the attacker had when he made it, tracking him along
            # this path (version 27; until then a caught defender's later bursts were read on the
            # catching burst's line).
            x, z, xl, zl = x.copy(), z.copy(), xl.copy(), zl.copy()
            if sel is not None:
                at = np.full(len(self.T), -1, np.int64)
                at[sel] = np.arange(len(sel))
            for tr, ix, tq in self.turns:
                n = int(math.ceil(max(0.0, float(tq.max()) - float(tr['t0'])) / DODGE_STEP)) + 1
                tg = float(tr['t0']) + DODGE_STEP * np.arange(n)
                pz, px, _ = self._pos(segs, tg)
                if not px.any():
                    continue
                ft = _turn_track(tr, px[None, :], pz[None, :], n)
                ji = _turn_index(tr, tq, n)
                th, ddx, ddz = ft[0][0][ji], ft[1][0][ji], ft[2][0][ji]
                if not (th.any() or ddx.any() or ddz.any()):
                    continue
                if sel is None:
                    jx = ix
                else:
                    keep = at[ix] >= 0
                    jx, th, ddx, ddz = at[ix][keep], th[keep], ddx[keep], ddz[keep]
                x[jx], z[jx] = _turn_apply(x[jx], z[jx], th, PK[jx], ddx, ddz)
                xl[jx], zl[jx] = _turn_apply(xl[jx], zl[jx], th, PK[jx], ddx, ddz)
        held = np.maximum(0.0, PK - (zl - 2 * self.push))
        got = ~immune & (np.hypot(X - x, Z + held + z) < R)
        if sel is None:
            return got
        out = np.zeros(len(self.T), bool)
        out[sel] = got
        return out

    @staticmethod
    def _escape_at(br, count, name):
        roll = br['roll'][max(1, min(count, 4)) - 1] if br['roll'] else None
        move = br['move']
        # A jump leaves through the roll's gate (`INFERRED`: `ExecEvasion` takes the jump with the
        # roll and the backstep, which all clear DamageCount, frame-advantage.md section 3).
        first, second = (roll, move) if name in ('roll', 'jump') else (move, roll)
        for v in (first, second, br.get('len')):
            if v is not None:
                return float(v)
        return float(CAUGHT_FRAMES)

    def _live(self, g, tc):
        """(the lists of geometry `g` that land when it catches him at real frame `tc`, those whose
        own bullets have all ended by then and so never land). A list's bullets are live when
        their span holds `tc` (the frame a bullet ends on is its child's first, so the later one
        is taken there); none such, the ones within `CAUGHT_TICK_TOL` of it, else the nearest (a
        homing tree flown again after him can catch a few frames off its stored samples, whose
        times are the flight against him standing). Every list when they have no `rows`."""
        spans = self.spans[g]
        every = list(range(len(self.lists[g])))
        if spans is None:
            return every, []
        live = [li for li in every if spans[li][0] - 1e-9 <= tc < spans[li][1]]
        if not live:
            live = [li for li in every if spans[li][0] - CAUGHT_TICK_TOL <= tc <= spans[li][1] + CAUGHT_TICK_TOL]
        if not live:
            gap = [max(spans[li][0] - tc, tc - spans[li][1], 0.0) for li in every]
            live = [li for li in every if gap[li] <= min(gap) + 1e-9]
        gone = [li for li in every if li not in live and spans[li][1] < tc - CAUGHT_TICK_TOL]
        return live, gone

    def _play(self, h, dist, name, roll, run=ESCAPE_RUNS[0], held=None, poise=None, bounds=None, info=None,
              stun=None):
        """[per geometry: [per list: share of its hits that land]] when geometry `h` catches him at
        `dist` and he leaves by escape `name` (rules above), `roll` its roll motion, running on
        along `run` (`_escapes`), his whiff's recovery holding him to `held` (`held`: its dodge
        floor for a roll, its move floor for a run; None, nothing holds him), his poise `poise`
        (None: the pool's one). `bounds` [lo, hi], when given, is narrowed to the poises this play
        takes alike: above every cumulative poise damage it dealt that did not break him, and at or
        below every one that did. `info`, a dict, is given 'te': the frame his escape first starts
        (the attacker can see it: `FollowupModel.mixup`'s stand row).

        `held` a `dodge_key` (version 25): `h` catches him at that frame in his own dodge of that
        move from that start, so he is where its root motion has carried him and moves on with it
        until he escapes; its dodge cancel holds a roll (or jump) out and its move cancel a run out,
        as the whiff's floors do (the behavior script starts a walk or a run only through the
        move-cancel check, `punish_lead`), and a reaction that locks him ends the dodge as it ends a
        whiff's recovery. Until then a defender caught mid-roll was played as one who stood at the
        distance (`INFERRED` there).

        `stun` (version 32), when given: {'land': f(gauges, g, lis, T, br, l)} that takes every
        landing (its geometry, lists, frame, its own reaction branch and the list it reads poise
        from) and gives the branch to play, `gauges` a dict kept for this play
        (`er-mechanics-spells.StatusCaught`: a bleed or frost proc raises a held hit to
        `stun_branch`). None plays as before."""
        import bisect
        import numpy as np
        poise = self.poise if poise is None else poise
        gauges = {} if stun is not None else None
        G = len(self.geoms)
        Tl = self._t_lists()
        got = [[0] * len(ls) for ls in self.lists]
        segs = [(-math.inf, 0.0, float(dist), 'stand', None, run)]
        if held is not None and isinstance(held[0], str):
            m = self._dodges[held[1]]
            s0, tc = held[2], held[3]
            trk = m.get('track')
            if trk:
                # A dodge out of an earlier one (`carried`, version 26): the track he was carried
                # on, then this dodge from where it had him at its start.
                for t0, ox, oz, sm in trk:
                    segs.append((t0, ox, float(dist) + oz, 'stand' if sm is STILL else 'dodge',
                                 None if sm is STILL else sm, run))
                x0, z0, _ = track_at(trk, [float(s0)])
                segs.append((s0, float(x0[0]), float(dist) + float(z0[0]), 'dodge', m['base'], run))
            else:
                segs.append((s0, 0.0, float(dist), 'dodge', m, run))
            end = float(m['dodge']) if m['dodge'] is not None else float(len(m['path']))
            go = max(float(m['move']), end) if m.get('move') is not None else end
            floor = s0 + (end if name in ('roll', 'jump') else go)
        else:
            tc = self.catch_at(dist, h)
            # The real frame his whiff's recovery lets this escape start (None: it does not hold
            # him); a reaction that locks him ends the recovery (rules above).
            floor = None if held is None else held[0 if name in ('roll', 'jump') else 1]
        state = {'cum': 0.0, 'count': 0, 'hold_t0': None, 'hold_update': None, 'floor': floor}
        started = {}                          # geometry -> its first hit's real frame
        nxt = {}                              # (geometry, list) -> next tick index

        def at_(path, T):
            return self._at1(path, T)

        def esc_seg(t0, base):
            if info is not None and 'te' not in info:
                info['te'] = float(t0)
            return (t0, *base, 'roll', roll, run) if name in ('roll', 'jump') else (t0, *base, 'run', None, run)

        def land(g, lis, T):
            nonlocal segs
            for li in lis:
                got[g][li] += 1
            l = max((self.lists[g][li] for li in lis), key=lambda l: l['poise'])
            state['cum'] += l['poise']
            # A hit that takes his toughness to 0 or below breaks it (`FUN_140486bf0`, `VERIFIED`,
            # frame-advantage.md section 1; `er-mechanics-interrupt` reads `dealt >= poise` too).
            # Until version 21 this was `>`, which kept a poise equal to the damage standing.
            broken = state['cum'] >= poise
            if bounds is not None:
                if broken:
                    bounds[1] = min(bounds[1], state['cum'])
                else:
                    bounds[0] = max(bounds[0], state['cum'])
            if broken:
                state['cum'] = 0.0
            br = l['react'][broken]
            if stun is not None:
                br = stun['land'](gauges, g, lis, T, br, l)
            at = at_(segs, T)
            if br['locks']:
                # A damage state: his whiff's recovery is over, its cancels release him now.
                state['floor'] = None
                again = state['hold_update'] is not None and _update(T / TAE_FPS) == state['hold_update']
                if not again:
                    state['count'] = min(state['count'] + 1, 4) if br['counts'] else 1
                    state['hold_t0'], state['hold_update'] = T, _update(T / TAE_FPS)
                t0 = state['hold_t0']
                base = at_([s for s in segs if s[0] < t0] or segs[:1], t0) if again else at
                segs = [s for s in segs if s[0] < t0] + [(t0, *base, 'hold', br['track'], run)]
                te = T + self._escape_at(br, state['count'], name)
                segs.append(esc_seg(te, at_(segs, te)))
            elif segs[-1][3] in ('stand', 'dodge'):
                # A flinch with nothing running leaves him to go: at once, or when his whiff's
                # recovery (or the dodge he was caught in) lets this escape start, where it has
                # left him by then.
                t0 = T if state['floor'] is None else max(T, state['floor'])
                segs = segs + [esc_seg(t0, at if segs[-1][3] == 'stand' else at_(segs, t0))]

        # Hit priority records (bd er-effects-rs-dnok, er-effects-rs-xb3i): record key -> (the
        # entry's priority, the launch holding it: `HPR`'s third field, shared by the geometries of
        # one bullet launch, `ATK.record_contact`). A geometry of a record another launch holds
        # lands only with a strictly lower priority; the holding launch's geometries land and keep
        # ticking; once another holds it, its own later ticks land nothing either.
        owner = {}

        def holds(g):
            r = self.hpr[g]
            return r is None or owner.get(r[0], (None, r[2]))[1] == r[2]

        def blocked(g):
            r = self.hpr[g]
            return r is not None and r[0] in owner and owner[r[0]][1] != r[2] and not r[1] < owner[r[0]][0]

        def take(g):
            r = self.hpr[g]
            if r is not None and (r[0] not in owner or r[1] < owner[r[0]][0]):
                owner[r[0]] = (r[1], r[2])

        lstart = {}                           # (geometry, list) -> its first hit's real frame

        def catch(g, c):
            # Geometry `g` catches him at `c`: the lists whose bullets are live then land; those
            # whose bullets have all ended never do; the rest wait for their own bullets (`_live`).
            started[g] = c
            take(g)
            live, gone = self._live(g, c)
            for li in range(len(self.lists[g])):
                nxt[(g, li)] = 1 if li in live else self.lists[g][li]['n'] if li in gone else 0
            for li in live:
                lstart[(g, li)] = c
            land(g, live, c)

        catch(h, tc)
        now = tc
        tch, drawn = None, None
        for _ in range(sum(l['n'] for ls in self.lists for l in ls) + G + 1):
            if drawn is not segs:
                # The path changed (a hit held or freed him): test every sample on the new one.
                # Every read below is of a sample at or after `now` less `CAUGHT_TICK_TOL` (and 1e-9),
                # and `now` never falls more than 1e-9 a step below the catch: the samples a frame
                # and more before that are not tested.
                tch, drawn = self._touch_drawn(segs, tc - CAUGHT_TICK_TOL - 1.0), segs
                cs = np.concatenate([[0], np.cumsum(tch)])
                own = {}
                # Per sample, the first touching sample at or after it (`_next_true`), made when a
                # read first asks for it; per (geometry, list) with own bullets, the same of those.
                ntl, own_n = None, {}
            best = None
            for g in range(G):
                a, b = self.bounds[g]
                if g not in started:
                    if b == a or Tl[g][-1] < now - 1e-9 or blocked(g):
                        continue
                    # The first sample of `g` at or after `now` less 1e-9 that touches him: its
                    # samples are in time order, so those at or after it start at `bisect_left`, and
                    # the first touching one from there is `ntl`'s (until 2026-11-21 a mask of every
                    # sample of `g` and its `argmax`, per geometry and per hit: 29% of Crystal
                    # Barrage's set, py-spy, target/haima/prof-cb.txt). A chain part's later samples
                    # are all past its end once one is.
                    if ntl is None:
                        ntl = _next_true(tch)
                    j = int(ntl[a + bisect.bisect_left(Tl[g], now - 1e-9)])
                    if j < b and (not self.ends[g] < math.inf or Tl[g][j - a] < self.ends[g] - 1e-9):
                        # A chain part catches no later than the frame before the next part's first:
                        # that frame is the next part's (`split_chains`).
                        c = Tl[g][j - a]
                        # One frame's tie in one record: the lower priority, then the attack
                        # created first (DmgMan's list, reversed by each FUN_140527d40 pass).
                        r = self.hpr[g]
                        tie = best is not None and abs(c - best[0]) < 1e-9 and r is not None \
                            and self.hpr[best[1]] is not None and self.hpr[best[1]][0] == r[0] \
                            and best[1] not in started \
                            and (r[1], r[2]) < (self.hpr[best[1]][1], self.hpr[best[1]][2])
                        if best is None or c < best[0] - 1e-9 or tie:
                            best = (c, g, list(range(len(self.lists[g]))))
                    continue
                if not holds(g):
                    continue
                T = self.T[a:b]
                for li, l in enumerate(self.lists[g]):
                    k = nxt[(g, li)]
                    m = self.masks[g][li]
                    if k == 0:
                        # Its own bullets come later: the first of them to touch him on his path (the
                        # first at or after `now` less 1e-9, as above).
                        i = bisect.bisect_left(Tl[g], now - 1e-9)
                        if m is None:
                            if ntl is None:
                                ntl = _next_true(tch)
                            j = int(ntl[a + i]) - a
                        else:
                            if (g, li) not in own_n:
                                own_n[(g, li)] = _next_true(tch[a:b] & m)
                            j = int(own_n[(g, li)][i])
                        if j < b - a:
                            c = Tl[g][j]
                            if best is None or c < best[0] - 1e-9:
                                best = (c, g, [li], 0)
                        continue
                    if k >= l['n'] or l['rec'] <= 0:
                        continue
                    if m is None:
                        cl = cs[a:b + 1] - cs[a]
                    else:
                        if (g, li) not in own:
                            own[(g, li)] = np.concatenate([[0], np.cumsum(tch[a:b] & m)])
                        cl = own[(g, li)]
                    taus = lstart[(g, li)] + l['rec'] * np.arange(k, l['n'])
                    keep = taus >= now - 1e-9
                    lo = np.searchsorted(T, taus - CAUGHT_TICK_TOL, side='left')
                    hi = np.searchsorted(T, taus + CAUGHT_TICK_TOL, side='right')
                    inside = keep & ((cl[hi] - cl[lo]) > 0)
                    if inside.any():
                        j = int(np.argmax(inside))
                        c = float(taus[j])
                        if best is None or c < best[0]:
                            best = (c, g, [li], k + j)
            if best is None:
                break
            c, g = best[0], best[1]
            if g not in started:
                catch(g, c)
            else:
                li = best[2][0]
                if nxt[(g, li)] == 0:
                    lstart[(g, li)] = c
                nxt[(g, li)] = best[3] + 1
                # Ticks of other lists that fell before this one and were not inside are lost.
                land(g, best[2], c)
            now = c
        return [[x / l['n'] for x, l in zip(row, ls)] for row, ls in zip(got, self.lists)]


def _next_true(mask):
    """Per index i of bool array `mask`, the first index at or after i where it holds (its length
    where none does), plus one entry for i = its length: an int array one longer than `mask`."""
    import numpy as np
    n = len(mask)
    idx = np.where(mask, np.arange(n), n)
    return np.concatenate([np.minimum.accumulate(idx[::-1])[::-1], [n]]).astype(np.int64)


class _LazyWorth:
    """`CaughtModel.lazy`: a burst's `hit_value`, each catch played and priced when first read."""

    __slots__ = ('model', 'dist', 'start', 'count', 'cap', 'floors', 'seen')
    #: It plays a catch made in his own dodge (`mid`): `reaction_outcome` asks for those.
    plays_dodges = True

    def __init__(self, model, dist, start, count, cap, floors=None):
        self.model, self.dist, self.start, self.count, self.cap = model, dist, start, count, cap
        self.floors = floors
        self.seen = {}

    def __len__(self):
        return self.count

    def __getitem__(self, i):
        i = int(i)
        if not -self.count <= i < self.count:
            raise IndexError(i)
        i %= self.count
        if i not in self.seen:
            self.seen[i] = self.model.worth_of(self.dist, self.start + i, floors=self.floors, hp=self.cap)
        return self.seen[i]

    def mid(self, i, move, start, catch):
        """What lands when geometry `i` of the burst catches him at real frame `catch` in his own
        dodge of `move` started at `start` (`CaughtModel.worth_of` `dodge`, version 25), capped
        as the rest; played when first read."""
        k = ('mid', int(i), move['anim'], float(start), float(catch))
        if k not in self.seen:
            self.seen[k] = self.model.worth_of(self.dist, self.start + int(i), hp=self.cap,
                                               dodge=(move, start, catch))
        return self.seen[k]

    def __iter__(self):
        return (self[i] for i in range(self.count))


def react_factors(outcomes, share=REACT_SHARE):
    """The scored reaction terms from `reaction_outcome` per defender distance: `land` (expected
    share of the attack's worth that lands: the committed defenders take all of it, the reacting
    ones what `value` says) and `whiff_hp` (the punish the reacting ones land), each the mean over
    the distances."""
    outs = [o for o in outcomes if o and o['base'] > 0]
    if not outs:
        return None
    n = len(outs)
    land = sum((1.0 - share) + share * o['value'] / o['base'] for o in outs) / n
    return {'land': land, 'p_evade': sum(o['p_evade'] for o in outs) / n,
            'p_punish': sum(o['p_punish'] for o in outs) / n,
            'whiff_hp': share * sum(o['punish_hp'] for o in outs) / n, 'react_share': share}


def slot_reaction(contacts, distances, cue=0.0, my_roll=None, strikes=None, fallback=None):
    """`react_factors` of one attack slot: `contacts` its `er-mechanics-reach.slot_contacts`
    windows (every damaging hit window of the clip, all worth the slot's whole damage: `INFERRED`
    that a multi-window slot lands whole), at each of `distances` it reaches. Without shapes,
    `fallback` = (first live frame, active frames) touches at any distance while he is not
    invincible (`INFERRED`)."""
    import numpy as np
    geoms = [contact_geometry(entries) for entries in (contacts or {}).values()]
    geoms = [g for g in geoms if g is not None]
    advance = attack_advance(geoms)
    if not geoms and fallback and fallback[0] is not None:
        t0, active = fallback
        n = max(1, int(math.ceil((active or 1.0) / DODGE_STEP)))
        geoms = [{'T': np.array([t0 + k * DODGE_STEP for k in range(n)]), 'X': np.zeros(n),
                  'Z': np.full(n, -99.0), 'R': np.full(n, 1e3), 'PK': np.full(n, -math.inf)}]
    if not geoms:
        return None
    value = [1.0] * len(geoms)
    outs = [reaction_outcome(geoms, value, d, cue, my_roll=my_roll, advance=advance, strikes=strikes)
            for d in distances]
    return react_factors(outs)


#: The recast rule every buff shares (`recast_plan`, `fight_points`), loaded once.
BUFFS = _load('er_mechanics_buffs', 'er-mechanics-buffs.py')
#: Fight lengths a buff option's uptime and recasts are averaged over
#: (`er-mechanics-buffs.FIGHT_SECONDS`, 180..300 s). Read at call time, so
#: `er-builds-pvp.py --fight-seconds` reaches it.
FIGHT_SECONDS = BUFFS.FIGHT_SECONDS


def _buff_duration(t, root, depth=0):
    """Longest duration (s) on a buff root and the rows it cycles into; -1 when one is permanent."""
    s = t.speffect_summary(root)
    if not s or depth > 6:
        return 0.0
    out = s['duration_s']
    for c in s['links'].values():
        d = _buff_duration(t, c['id'], depth + 1) if c else 0.0
        out = -1.0 if -1.0 in (out, d) else max(out, d)
    return out


def _buff_damage_taken(t, root, depth=0):
    """Lowest PvP damage-taken rate (`defPlayerDmgCorrectRate_*`, mean over elements) on a buff root
    and the rows it cycles into: Braggart's Roar's 1860 carries none itself, its cycled 1861 has 0.9."""
    row = t.speffect.get(root)
    if not row or depth > 6:
        return 1.0
    out = sum(row.get(f'defPlayerDmgCorrectRate_{VS_PLAYER[el]}', 1.0) for el in ELEMENTS) / len(ELEMENTS)
    for link in SPEFFECT_LINKS:
        nxt = row.get(link, -1)
        if nxt and nxt > 0 and nxt != root:
            out = min(out, _buff_damage_taken(t, nxt, depth + 1))
    return out


def buff_option(t, sword_arts_id, weapon_id, buffed_score, best_score, eng, opp, uses, cast_frames,
                engagements=ENGAGEMENTS_DEFAULT, fight_seconds=None, roots=()):
    """A skill used as a buff (section 16b): the moveset score with its buff rows held,
    `buffed_score` (the caller re-scores every slot with them; uptime comes from
    `er-mechanics-buffs.uptime` with the casts one FP bar pays for), plus what its damage-taken
    cut saves, minus the time of the casts made during the fight.

    The first cast is made before contact, like every other buff in buffs.md section 10
    (`INFERRED`), so it costs nothing. Recasts follow the rule every buff shares
    (`er-mechanics-buffs.recast_plan`, averaged over the fight lengths `fight_seconds`): a buff
    shorter than the fight is recast ceil(fight / duration) - 1 times, at most the casts left, and
    each recast takes `cast_frames` (the cast's first roll frame) out of the fight, the time factor
    1 - recasts x cast / fight. (Until 2026-10-01 a recast's frames were spread over the fight's
    engagements as extra commitment, C / (C + recasts x cast / engagements): that rule and
    `er-builds-pvp.SetupBuffs`' time factor disagreed, and only stayed out of sight while the
    fight was 25 s and few buffs were ever recast.) The cut is section 14b's (1 - win) x
    (1 - taken) x the opponents' mean hit, times the uptime.

        score = buffed x (N + cut) / N x time factor"""
    if not buffed_score or not eng or not eng.get('numerator') or not eng.get('commit'):
        return None
    if fight_seconds is None:
        # Read at call time, so `er-builds-pvp.py --fight-seconds` reaches it.
        fight_seconds = FIGHT_SECONDS
    durs = [_buff_duration(t, r) for r in roots]
    dur = -1.0 if -1.0 in durs else max(durs, default=0.0)
    casts = float(uses) if uses is not None else _hits_cap(engagements)
    plan = BUFFS.recast_plan(dur, fight_seconds, uses=casts, cast_frames=cast_frames or 0.0,
                             hits=engagements)
    recasts, uptime, tf = plan['recasts'], plan['uptime'], plan['time_factor']
    taken = min((_buff_damage_taken(t, r) for r in roots), default=1.0)
    ex = eng.get('exchange') or {}
    cut = uptime * (1.0 - ex.get('win', 0.0)) * (1.0 - taken) * \
        (opp.mean_hp_landed if opp else OPPONENT_FALLBACK['hp'])
    n = eng['numerator']
    score = buffed_score * (n + cut) / n * tf
    return {'score': score, 'gain': max(0.0, score - best_score), 'buffed': buffed_score,
            'duration_s': dur, 'recasts': recasts, 'uptime': uptime, 'cut_hp': cut, 'time_factor': tf,
            'cast_frames': cast_frames, 'roots': list(roots)}


def skill_term(t, weapon_id, choice, ctx, level, best_score, fp_bar=FP_BAR_DEFAULT,
               engagements=ENGAGEMENTS_DEFAULT, score_fn=None, defender=None, def_module=None,
               damage_fn=None, poises=None, slot_extra_fn=None, engagement=None, opponents=None,
               available=None, reach=False, factor_bound=1.0, grip='one', measure_all=False,
               react=False, strikes=None, buff_fn=None, follow_ups=True, neutral_fn=None):
    """The skill's contribution to a weapon's PvP score.

    Every skill is scored the same way:
      * its hits are scored like a slot: `score_fn` (er-builds-pvp `slot_score`) on
        {dmg, roll, next, stagger, parryable} plus whatever `slot_extra_fn(option)` returns (the
        ranking adds frame advantage, crit and guard terms there), plus with `reach` the skill's
        own reach and coverage (`skill_reach_factors`); without `score_fn`, damage per second of
        commitment;
      * `share` = casts one FP bar pays for (`fp_uses`, the corpus median bar) over the fight's
        `engagements` landed hits, capped at 1;
      * `gain` = max(0, skill score - `best_score`): the skill is an option a player takes when
        it beats the weapon's best slot, so a weak skill never lowers the weapon.

    Two values come out of that:
      * `value` (scored): the best of `available` (`mountable_skills`: every skill the weapon can
        carry as built, its own included), max of share x gain. The ash slot is an option the
        player fills per matchup, so it is worth its best ash (section 15). Without `available`,
        the skills of `choice`.
      * `value_corpus` (shown only): sum over `choice` (`skill_choice`, what the corpus mounts,
        with probability p) of p x share x gain, the section 13 term.
    `score` = best_score + `SKILL_WEIGHT` x value, `score_corpus` the same with value_corpus.

    Reach is measured only where it can change the answer: the neutral score (reach and coverage
    1.0) times `factor_bound` (the largest reach x coverage factor the scorer allows) bounds the
    measured one, so an option whose bound cannot beat the best so far is left unmeasured
    (`reach_measured` false). The corpus options are all measured.
    `buff_alternatives` = [(p, ((root SpEffect, 1.0, casts), ...))], one entry per corpus skill
    that buffs its user, for `er-mechanics-buffs.expected_attack(alternatives=...)`: the skills
    are alternatives (one per weapon), never applied together.

    Section 16 adds three things. With `react`, each landed part of a skill is dodged on reaction
    by the waiting share of defenders (`skill_reaction`), and `strikes` (the pool's R1 strike
    frames) prices the punish of an evaded one as `whiff_hp`. With `follow_ups`, a skill whose
    opening offers a follow-up press (`skill_followups`) is also scored as the opening plus that
    follow-up, a separate option. With `buff_fn(option, roots, casts) -> buffed moveset score`, a
    skill that buffs its user is also scored as a buff (`buff_option`), and an option's worth is
    the larger of share x gain and the buff's gain.

    Section 17 (`neutral_fn(option, slot) -> dict`): once an option's reach is measured, its
    committed share meets the neutral-game contest (`er-mechanics-neutral.neutral_exchange`)
    instead of the 2.5 m one, the way a slot's does; `slot_extra_fn` puts the 2.5 m contest and
    the stamina factor on it first (`er-builds-pvp.Mechanics.skill_exchange`)."""
    cache = {}

    def evaluate(sid):
        if sid not in cache:
            cache[sid] = _skill_option_scored(
                t, weapon_id, sid, ctx, level, best_score, fp_bar, engagements, score_fn, defender,
                def_module, damage_fn, poises, slot_extra_fn, engagement, opponents, buff_fn,
                follow_ups)
        return cache[sid]

    def worth(o):
        return max(o['share'] * o['gain'], o.get('buff_gain') or 0.0)

    def rescore(o, slot):
        if score_fn:
            return score_fn(slot)
        ends = [f for f in (o['roll'], o['next']) if f]
        dmg = slot['dmg'] * (slot.get('react') or {}).get('land', 1.0)
        return {'score': dmg / min(ends) * TAE_FPS} if dmg and ends else None

    def measure(o):
        # The per-hit landing (`skill_landing`) and, with `reach`, the skill's own reach and
        # coverage. Both can only lower or re-weight the neutral all-hits score `bound` uses.
        if o.get('_slot') is None or o.get('measured'):
            return
        o['measured'] = True
        slot = dict(o['_slot'])
        # Each part (the opening, and a follow-up when the option has one) is landed on its own
        # and, with `react`, dodged on reaction from its own cue (section 16). A charged skill cast
        # as it opens holds the charge, and until the early release's frame it looks like that
        # release (`skill_release_part`): the waiting defender cannot tell which is coming
        # (`skill_reaction` `look`, the comment above `_escape`).
        look = None
        if react and len(o.get('parts') or []) == 1:
            try:
                look = skill_release_part(t, weapon_id, o['sword_arts_id'], ctx, level, defender, def_module,
                                          damage_fn, poises, o.get('stance'))
            except (SystemExit, KeyError, StopIteration, TypeError, ValueError, FileNotFoundError, OSError) as exc:
                o['release_error'], look = str(exc), None
            if look is not None:
                o['release_look'] = {'offset': look['offset'], 'hits': len(look['hits'])}
        land, dmg, reacts, lands = None, 0.0, [], []
        for part in o.get('parts') or [{'hits': o['hit_rows'], 'offset': 0.0, 'cue': 0.0}]:
            try:
                land = skill_landing(t, weapon_id, o['sword_arts_id'], part['hits'], poises)
            except (SystemExit, KeyError, StopIteration, TypeError, ValueError, FileNotFoundError,
                    OSError) as exc:
                o['landing_error'], land = str(exc), None
            if not land:
                break
            lands.append(land)
            got = land['dmg']
            if react:
                try:
                    r = skill_reaction(t, weapon_id, o['sword_arts_id'], part['hits'], land, part['offset'],
                                       part['cue'], o['roll'], strikes, look=look)
                except (SystemExit, KeyError, StopIteration, TypeError, ValueError, FileNotFoundError,
                        OSError) as exc:
                    o['react_error'], r = str(exc), None
                if r:
                    got = r['dmg']
                    reacts.append(r)
            dmg += got
        if land:
            o['dmg_all'], o['dmg'] = o['dmg'], dmg
            o['dmg_landing'] = sum(x['dmg'] for x in lands)
            slot['dmg'] = dmg
            o['landing'] = {k: lands[0][k] for k in ('by_distance', 'hit_share', 'connects', 'sources',
                                                     'geometry')}
            if reacts:
                # The punish belongs to the part the attacker recovers from, the last one.
                o['react'] = {'p_evade': [round(r.get('p_evade', 0.0), 3) for r in reacts],
                              'land': [round(r.get('land', 1.0), 3) for r in reacts],
                              'cue': [r['cue'] for r in reacts],
                              'p_punish': round(reacts[-1].get('p_punish', 0.0), 3),
                              'whiff_hp': reacts[-1].get('whiff_hp', 0.0)}
                slot['whiff_hp'] = o['react']['whiff_hp']
                if o['dmg_landing'] > 0 and len(reacts) == len(lands):
                    # The slot's shape (section 16a): the landed damage, and the share of it the
                    # reaction leaves, so `slot_score` splits the defenders between the waiting
                    # share (this) and the committed share, which meets the contest the caller
                    # put in `exchange` exactly as a slot's does. Without a contest the score is
                    # unchanged: (1 - s) x 1 + land - (1 - s) = land.
                    slot['dmg'] = o['dmg_landing']
                    slot['react'] = {'land': dmg / o['dmg_landing'], 'react_share': REACT_SHARE,
                                     'whiff_hp': o['react']['whiff_hp']}
        if reach:
            o['reach_measured'] = True
            try:
                f = skill_reach_factors(t, weapon_id, o['sword_arts_id'], o, grip)
            except (SystemExit, KeyError, StopIteration, TypeError, ValueError, FileNotFoundError) as exc:
                o['reach_error'], f = str(exc), None
            if f:
                o.update({k: f[k] for k in ('reach', 'coverage', 'reach_source', 'projectile',
                                            'melee_reach', 'bullet_reach')})
                slot.update(reach=f['reach'], coverage=f['coverage'])
        if neutral_fn is not None:
            # The contest started from the neutral game with the skill's own reach
            # (`er-mechanics-neutral`), as a slot's is.
            nt = neutral_fn(o, slot)
            if nt:
                slot['neutral'] = nt
                o['neutral'] = {k: nt[k] for k in ('f_neutral', 'win', 'loss', 'outreach')}
        sc = rescore(o, slot)
        o['score'] = sc['score'] if sc else None
        o['score_detail'] = sc
        o['gain'] = max(0.0, o['score'] - best_score) if o['score'] is not None else 0.0
        if land:
            # The section 15 all-hits number, kept for comparison (never scored).
            sc_all = rescore(o, {**slot, 'dmg': o['dmg_all'], 'react': None})
            o['score_all'] = sc_all['score'] if sc_all else None

    opts, value_corpus, buffs = [], 0.0, []
    for sid, p in choice:
        variants = evaluate(sid)
        o = variants[0]
        if 'error' not in o:
            for v in variants:
                measure(v)
            top = max(variants, key=worth)
            value_corpus += p * worth(top)
            if o['buff_roots']:
                casts = float(o['uses'] if o['uses'] is not None else _hits_cap(engagements))
                buffs.append((p, tuple((root, 1.0, casts) for root in o['buff_roots'])))
            o = top
        opts.append({**o, 'p': p})

    pool = [v for sid in (available if available is not None else [s for s, _ in choice])
            for v in evaluate(sid)]
    pool = [o for o in pool if 'error' not in o]
    if measure_all:
        for o in pool:
            measure(o)

    def bound(o):
        if o.get('_slot') is None or o.get('measured'):
            return worth(o)
        # Unmeasured, the contest multiplies the whole score; measured with a reaction it only
        # meets the committed share, so the unmeasured score is divided by it to stay a bound.
        f_c = min(1.0, ((o['_slot'].get('exchange') or {}).get('f_exchange') or 1.0))
        hit = o['share'] * max(0.0, (o['score'] or 0.0) / f_c * (factor_bound if reach else 1.0) - best_score)
        return max(hit, o.get('buff_gain') or 0.0)

    best, value = None, 0.0
    for o in sorted(pool, key=bound, reverse=True):
        if bound(o) <= value:
            break
        measure(o)
        v = worth(o)
        if v > value:
            best, value = o, v
    if best is not None:
        best['as'] = 'buff' if (best.get('buff_gain') or 0.0) >= best['share'] * best['gain'] and \
            best.get('buff_gain') else ('utility' if best.get('utility') else 'hits')
    return {'options': opts, 'value': value, 'score': best_score + SKILL_WEIGHT * value,
            'value_corpus': value_corpus, 'score_corpus': best_score + SKILL_WEIGHT * value_corpus,
            'best': best, 'available': pool, 'buff_alternatives': buffs, 'fp_bar': fp_bar,
            'engagements': engagements}


def _skill_option_scored(t, weapon_id, sid, ctx, level, best_score, fp_bar, engagements, score_fn,
                         defender, def_module, damage_fn, poises, slot_extra_fn, engagement, opponents,
                         buff_fn=None, follow_ups=True):
    """The options of one skill for `skill_term`: the skill as it opens, then (section 16) the
    opening plus each follow-up press it offers. Each is scored with neutral reach and coverage
    (`_slot` keeps the slot dict for the reach pass). The first carries the buff option when the
    skill buffs its user in any paid animation, its follow-ups included.

    A stance skill (`stance_buttons`) is one option per button its stance hands over on, the
    first of them carrying the follow-ups and the buff: Unsheathe's R1 slash and its R2 slash,
    each with its own FP, hits and recovery."""
    try:
        stances = skill_profile(t, sid, weapon_id, level, ctx_grip(ctx))['stance_buttons'] or [None]
        base = _skill_variant_scored(t, weapon_id, sid, ctx, level, best_score, fp_bar, engagements, score_fn,
                                     defender, def_module, damage_fn, poises, slot_extra_fn, engagement,
                                     opponents, stance=stances[0])
    except (SystemExit, KeyError, StopIteration, TypeError) as exc:
        return [{'sword_arts_id': sid, 'name': t.arts_name(sid), 'error': str(exc)}]
    out = [base]
    for b in stances[1:]:
        try:
            v = _skill_variant_scored(t, weapon_id, sid, ctx, level, best_score, fp_bar, engagements, score_fn,
                                      defender, def_module, damage_fn, poises, slot_extra_fn, engagement,
                                      opponents, stance=b)
        except (SystemExit, KeyError, StopIteration, TypeError):
            v = None
        if v is not None and (v.get('score') is not None or v.get('utility')):
            out.append(v)
    follows, pressed = [], []
    try:
        # `follows` (the first stance's, which the buff reads) and every stance's (`pressed`).
        pressed = [f for b in stances for f in skill_followups(t, sid, weapon_id, level, ctx_grip(ctx), b)] \
            if follow_ups or buff_fn else []
        follows = [f for f in pressed if f['stance'] == stances[0]]
    except (SystemExit, KeyError, StopIteration, TypeError):
        follows, pressed = [], []
    for f in pressed if follow_ups else []:
        try:
            v = _skill_variant_scored(t, weapon_id, sid, ctx, level, best_score, fp_bar, engagements, score_fn,
                                      defender, def_module, damage_fn, poises, slot_extra_fn, engagement,
                                      opponents, follow=f)
        except (SystemExit, KeyError, StopIteration, TypeError):
            v = None
        if v is not None and v.get('score') is not None:
            out.append(v)
    if buff_fn is not None:
        prof = skill_profile(t, sid, weapon_id, level, ctx_grip(ctx))
        roots, fp, cast = list(base['buff_roots']), base['fp'], None
        opening = main_anim(prof)
        if opening is not None:
            cast = anim_recovery(t, weapon_id, sid, opening).get('dodge')
        for f in follows:
            more = [s['id'] for s in skill_buffs(t, prof, anim=f['anim']) if s.get('effect') == 'buff']
            if more:
                roots += [r for r in more if r not in roots]
                fp += t.arts[sid][f"useMagicPoint_{f['button'].upper()}"]
                rec = anim_recovery(t, weapon_id, sid, f['anim']).get('dodge')
                cast = f['first'] + rec if rec is not None else cast
        if roots:
            uses = fp_uses(fp_bar, fp)
            casts = float(uses if uses is not None else _hits_cap(engagements))
            buffed = buff_fn(base, tuple(roots), casts)
            b = buff_option(t, sid, weapon_id, buffed, best_score, engagement, opponents, uses, cast,
                            engagements, roots=roots) if buffed else None
            if b:
                base['buff'] = b
                base['buff_gain'] = b['gain']
    return out


def _skill_variant_scored(t, weapon_id, sid, ctx, level, best_score, fp_bar, engagements, score_fn,
                          defender, def_module, damage_fn, poises, slot_extra_fn, engagement, opponents,
                          follow=None, stance=None):
    """One variant of `_skill_option_scored` (`skill_option` with `follow` or `stance`), or None
    when the follow-up has no hit."""
    o = skill_option(t, weapon_id, sid, ctx, level, defender, def_module, damage_fn, poises, follow, stance)
    if o is None:
        return None
    uses = fp_uses(fp_bar, o['fp'])
    share = 1.0 if uses is None else _use_share(uses, engagements)
    o.update(uses=uses, share=share, score=None, gain=0.0, _slot=None, reach_measured=False)
    ends = [f for f in (o['roll'], o['next']) if f]
    # A skill whose TimeAct has no FP-charging animation has no identified opening
    # (`main_anim` falls back to the first animation), and a parry's hits need a caught
    # attack first. Neither is scored as an opener (Golden Retaliation, SwordArtsParam 1196,
    # in `er-mechanics-crits.PARRY_SWORD_ARTS`, has no event 330 anywhere; its counter-blast
    # read as a 2450-score opener on the Erdtree Greatshield).
    o['unscored'] = ('no FP-charging opening' if not o['fp_anims'] else
                     'parry' if 'parry' in o['classes'] else None)
    if engagement and (o['dmg'] <= 0 or o['unscored'] == 'parry') \
            and (o['fp_anims'] or 'parry' in o['classes']):
        # A skill with no scored hit: its utility (section 14) as a multiplier on the best
        # opener's engagement, taken like any option when it beats the moveset.
        u = utility_value(t, sid, weapon_id, engagement, opponents)
        if u is not None:
            o['utility'] = u
            o['score'] = best_score * utility_multiplier(u, engagement)
            o['gain'] = max(0.0, o['score'] - best_score)
    elif o['dmg'] > 0 and ends and not o['unscored']:
        slot = {'dmg': o['dmg'], 'roll': o['roll'], 'next': o['next'], 'stagger': o['stagger'] or 0.0,
                'parryable': o['parryable'], 'status': {}}
        if slot_extra_fn:
            slot.update(slot_extra_fn(o) or {})
        sc = score_fn(slot) if score_fn else {'score': o['dmg'] / min(ends) * TAE_FPS}
        o['score'] = sc['score'] if sc else None
        o['score_detail'] = sc
        if o['score'] is not None:
            o['_slot'] = slot
            o['gain'] = max(0.0, o['score'] - best_score)
    return o


# ---------------------------------------------------------------------------------------------
# reports

def print_profile(t, prof, level):
    print(f"{prof['name']} (SwordArtsParam {prof['sword_arts_id']}, TimeAct {prof['tae']}) on "
          f"{prof['weapon']} ({prof['weapon_id']}): FP L2 {prof['fp_cost']['L2']} R2 {prof['fp_cost']['R2']}"
          f"; classes {', '.join(prof['classes']) or 'none'}")
    print(f"  {'2H' if prof.get('grip') == 'both' else '1H'} opens with {main_anim(prof):06d} "
          f"(diff category {prof.get('diff_category')}, GetSwordArtsDiffCategory)"
          + (f"; variant {prof['variant_missing']:06d} is not in the TimeAct"
             if prof.get('variant_missing') else ''))
    if prof.get('stance_buttons'):
        legs = []
        for b in prof['stance_buttons']:
            pb = skill_profile(t, prof['sword_arts_id'], prof['weapon_id'], level, prof.get('grip'), b)
            legs.append(f"{b.upper()} -> {pb['opening']:06d} from f{lead_in(t, prof['sword_arts_id'], pb):g}")
        print(f"  a stance: L2 into {prof['stance_entry']:06d}, then " + ', '.join(legs))
    print('  Every animation of the skill TimeAct is listed; the other variants are what other '
          'weapon classes and grips play. An id 5 above another without an fp line is its '
          'without-FP copy.')
    for anim, acts in prof['anims'].items():
        if not acts:
            continue
        tag = ' (charges FP)' if anim in prof['fp_anims'] else \
            ' (without-FP copy)' if anim in prof['no_fp_anims'] else ''
        print(f'  anim {anim:06d}{tag}')
        for x in acts:
            f0, f1 = x['frames']
            fr = f'f{f0}-{f1 if f1 is not None else "end"}'
            k = x['kind']
            if k in ('melee', 'no-damage hitbox', 'body hitbox', 'parry'):
                mv = '/'.join(str(v) for v in x['mv'].values())
                flat = '/'.join(str(v) for v in x['flat'].values())
                dmg = (f'MV {mv}' + (f' + flat {flat}' if x['add_base_atk'] else '')) if x['from_weapon'] \
                    else f'flat {flat} (not from the weapon)'
                print(f"    {fr:10} {k:6} judge {x['judge']} -> {x['behavior_row']} atk {x['atk_row']} "
                      f"{dmg} poise {round((x['poise'] or 0) * POISE_MENU, 1)} stam {x['stamina_cost']}")
                _print_on_hit(t, x.get('on_hit_speffects') or [], 17)
            elif k == 'bullet':
                _print_bullet(t, x.get('bullet'), fr, f"judge {x['judge']} -> {x['behavior_row']}", 4)
            elif k == 'speffect':
                s = x.get('speffect')
                head = f"    {fr:10} {x['event']} [{x.get('effect', 'marker')}] "
                print(head + (describe_speffect(s) if s else 'nothing with enough FP'))
                if x.get('speffect_no_fp'):
                    print(f"    {'':10}   without FP: {describe_speffect(x['speffect_no_fp'])}")
            elif k == 'hyperarmor':
                bonus = f"+{x['poise_bonus_menu']} poise" if x['weapon_term'] in (1, 2) else \
                    ('item term (not modelled)' if x['weapon_term'] == 3 else 'no bonus, refill only')
                print(f"    {fr:10} hyperarmor row {x['toughness_row']} {bonus}"
                      f" (PvP: poise dmg x{x['pvp_poise_damage_taken']}, HP dmg x{x['pvp_hp_damage_taken']})")
            elif k in ('fp', 'iframes'):
                print(f"    {fr:10} {k}")
            else:
                print(f"    {fr:10} {x['event']} judge {x.get('judge')} {k}")


def _print_links(summary, indent):
    for link, c in (summary or {}).get('links', {}).items():
        print(' ' * indent + f"-> {link} {c['id']} {c['name'] or ''} duration {c['duration_s']}s "
              f"{json.dumps(c['effects'])[:150]}")
        _print_links(c, indent + 2)


def _print_on_hit(t, ids, indent):
    for sid in ids:
        s = t.speffect_summary(sid)
        if s:
            who = 'on the target' if (not s['can_target_self'] or t.harms_target(sid)) else 'on self/allies'
            print(' ' * indent + f'on hit ({who}): {describe_speffect(s)}')


def _print_bullet(t, bt, fr, head, indent):
    if not bt:
        print(' ' * indent + f'{fr:10} bullet {head} (no Bullet row)')
        return
    mv = '/'.join(str(v) for v in bt['mv'].values())
    flat = '/'.join(str(v) for v in bt['flat'].values())
    print(' ' * indent + f"{fr:10} bullet {head} Bullet {bt['bullet']} x{bt['num_shoot']} atk {bt['atk_row']} "
          f"MV {mv} flat {flat}{' (added)' if bt['add_base_atk'] else ''} atkSuperArmor {bt['poise_flat']}"
          f"{' shared hit list' if bt['shared_hit_list'] else ''}")
    _print_on_hit(t, bt['on_hit_speffects'], indent + 13)
    for link, c in bt['children'].items():
        _print_bullet(t, c, '', f'<- {link}', indent + 2)


def cmd_skill(t, a):
    wid = t.find_weapon(a.weapon)
    sid = t.find_arts(a.skill)
    gem = t.ash_gems().get(sid)
    if gem is not None and not any(t.can_mount(wid, gem, aff, 25)[0] for aff in range(13)):
        print(f"warning: {t.reg.weapon_names.get(wid)} cannot mount {t.arts_name(sid)} "
              f"({t.can_mount(wid, gem, t.gem[gem]['defaultWepAttr'], 25)[1]}); its rows may not resolve")
    elif gem is None and t.reg.weapon[wid]['swordArtsParamId'] != sid:
        print(f"warning: {t.arts_name(sid)} is not an ash and not this weapon's own skill")
    prof = skill_profile(t, sid, wid, a.level, a.grip)
    if a.json:
        print(json.dumps(prof, indent=1, default=str))
    else:
        print_profile(t, prof, a.level)


def cmd_list(t, a):
    gems = t.ash_gems()
    wid = t.find_weapon(a.mountable) if a.mountable else None
    for sid in sorted(t.arts):
        if sid in (0, 1, 10):
            continue
        g = gems.get(sid)
        if wid is not None and (g is None or not t.can_mount(wid, g, 0)[0]):
            continue
        # Classify on a weapon that can carry it: the probe weapon, or any mountable one.
        probe = wid if wid is not None else _probe_weapon(t, sid, g)
        classes = skill_profile(t, sid, probe)['classes'] if probe else []
        a_row = t.arts[sid]
        print(f"{sid:6} {t.arts_name(sid)[:34]:34} {'ash' if g else 'unique':6} FP {a_row['useMagicPoint_L2']:>3}"
              f"/{a_row['useMagicPoint_R2']:>3}  {'+'.join(classes) or '-'}")


def _probe_weapon(t, sid, gem_id):
    """A weapon carrying this skill: the first mountable weapon for an ash, else the first
    weapon whose built-in skill it is."""
    for wid in sorted(t.reg.weapon):
        if wid % 10000:
            continue
        if gem_id is not None and t.can_mount(wid, gem_id, t.gem[gem_id]['defaultWepAttr'])[0]:
            return wid
        if gem_id is None and t.reg.weapon[wid]['swordArtsParamId'] == sid:
            return wid
    return None


def opening_timing(t, sid, wid, prof, anim):
    """(first hit clip frame, last hit clip frame, roll real frame, R1 real frame, has TAE 608)
    of one opening animation; a leap opening takes its hits from the same ten-block."""
    acts = list(prof['anims'].get(anim, []))
    if not any(x['kind'] in ('melee', 'bullet') for x in acts):
        acts = [x for a in prof['anims'] if a // 10 == anim // 10 and a not in prof['no_fp_anims']
                for x in prof['anims'][a]]
    hits = [x['frames'][0] for x in acts if x['kind'] in ('melee', 'bullet')]
    first, last = (min(hits), max(hits)) if hits else (None, None)
    rec = anim_recovery(t, wid, sid, anim, (last or 0) / TAE_FPS)
    speed = bool(ATK._reach().speed_windows((skill_tae(t, sid) or {}).get(anim) or []))
    return first, last, rec.get('dodge'), rec.get('r1'), speed


def weapon_category_names():
    """{wepmotionCategory: 'TWINBLADE', ...} from common_define.hks `WEAPON_CATEGORY_*`."""
    if not os.path.exists(COMMON_DEFINE_HKS):
        return {}
    wct = _load('er_hks_weapon_category_table', 'er-hks-weapon-category-table.py')
    return {v: n.removeprefix('WEAPON_CATEGORY_') for n, v in wct.extract(COMMON_DEFINE_HKS)['categories'].items()}


def opening_variants(t, sid=None):
    """[row] per (skill, wepmotionCategory, grip, opening): the animation each carrier class opens
    with and its timing next to the index-0 opening's. Carriers are base weapons that mount the
    ash or ship with the skill."""
    gems = t.ash_gems()
    base = [w for w in sorted(t.reg.weapon) if w % 10000 == 0]
    names = weapon_category_names()
    out = []
    for s in ([sid] if sid is not None else sorted(t.arts)):
        if s in (0, 1, 10):
            continue
        g = gems.get(s)
        carriers = [w for w in base if (g is not None and t.can_mount(w, g, 0)[0])
                    or t.reg.weapon[w].get('swordArtsParamId') == s]
        by_class = collections.defaultdict(list)
        for w in carriers:
            by_class[t.reg.weapon[w]['wepmotionCategory']].append(w)
        for mc, ws in sorted(by_class.items()):
            for two in (False, True):
                seen = set()
                for w in ws:
                    try:
                        p = skill_profile(t, s, w, 0, two)
                    except (SystemExit, KeyError, TypeError):
                        continue
                    if p['generic_opening'] is None or (p['opening'], p['variant_missing']) in seen:
                        continue
                    seen.add((p['opening'], p['variant_missing']))
                    gen = opening_timing(t, s, w, p, p['generic_opening'])
                    var = gen if p['opening'] == p['generic_opening'] else opening_timing(t, s, w, p, p['opening'])
                    out.append({'skill': t.arts_name(s), 'sword_arts_id': s, 'tae': p['tae'],
                                'class': mc, 'class_name': names.get(mc, str(mc)),
                                'grip': '2H' if two else '1H', 'weapon': t.reg.weapon_names.get(w),
                                'diff_category': p['diff_category'], 'generic': p['generic_opening'],
                                'opening': p['opening'], 'variant_missing': p['variant_missing'],
                                'generic_timing': gen, 'timing': var})
    return out


def cmd_variants(t, a):
    rows = opening_variants(t, t.find_arts(a.skill) if a.skill else None)
    moved = [r for r in rows if r['opening'] != r['generic']]
    differ = {(r['sword_arts_id'], r['class']) for r in moved if r['timing'] != r['generic_timing']}
    pairs = {(r['sword_arts_id'], r['class']) for r in rows}
    shown = rows if a.skill else moved
    print('skill\tclass\tgrip\tweapon\tdiff cat\topening\tfirst hit\tlast hit\troll\tR1\t608\t'
          'index-0 first/last/roll/R1')
    for r in shown:
        f = r['timing']
        g = r['generic_timing']
        print(f"{r['skill']}\t{r['class']} {r['class_name']}\t{r['grip']}\t{r['weapon']}\t{r['diff_category']}\t"
              f"{r['opening']:06d}\t{f[0]}\t{f[1]}\t{f[2]}\t{f[3]}\t{'yes' if f[4] else '-'}\t"
              f"{g[0]}/{g[1]}/{g[2]}/{g[3]}")
    print(f'{len(pairs)} (skill, class) pairs; {len({(r["sword_arts_id"], r["class"]) for r in moved})} open '
          f'with a variant; {len(differ)} of those differ in timing from the index-0 opening, across '
          f'{len({s for s, _ in differ})} skills; {sum(1 for r in rows if r["variant_missing"])} rows name '
          'a variant the TimeAct lacks')


def cmd_adoption(t, a):
    rows = corpus_slots(a.mirror, a.rl - a.window, a.rl + a.window, a.filter)
    stats = adoption(t, rows)
    print(f'{len(rows)} builds, RL {a.rl - a.window}-{a.rl + a.window}, filter {a.filter}; '
          'builds = builds with the skill on any equipped weapon (active set)')
    print('right = builds with it on a right-hand slot; L2 = builds whose primary pair fires it on L2 '
          '(one-handed unless the build is flagged 2H); ash/own = slots holding it as a mounted ash '
          'or as the weapon\'s own skill')
    print(f"  {'skill':34}{'builds':>7}{'share':>7}{'right':>7}{'L2':>5}{'ash':>5}{'own':>5}  classes  top weapons")
    for name, e in sorted(stats.items(), key=lambda kv: -kv[1]['builds'])[:a.top]:
        try:
            sid = t.find_arts(name)
            gem = t.ash_gems().get(sid)
            probe = _probe_weapon(t, sid, gem)
            classes = '+'.join(skill_profile(t, sid, probe)['classes']) if probe else '?'
        except SystemExit:
            classes = '?'
        top = ', '.join(f'{w} {n}' for w, n in e['weapons'].most_common(3))
        print(f"  {name[:33]:34}{e['builds']:>7}{100 * e['builds'] / max(1, len(rows)):>6.1f}%"
              f"{e['right']:>7}{e['l2']:>5}{e['ash']:>5}{e['builtin']:>5}  {classes:8} {top}")


def cmd_term(t, a):
    """The skill term (section 13) for one weapon as built."""
    def_mod = _load('er_mechanics_defense', 'er-mechanics-defense.py')
    opt = _load('er_builds_optimize', 'er-builds-optimize.py')
    rows = corpus_slots(a.mirror, a.rl - a.window, a.rl + a.window, a.filter)
    pairings = skill_pairings(t, rows)
    defender = opt.bracket_defender([{'computed': r['computed']} for r in rows])
    fps = sorted(r['computed']['maxFP'] for r in rows if (r['computed'] or {}).get('maxFP'))
    fp_bar = statistics.median(fps) if fps else FP_BAR_DEFAULT
    poises = [p for p in ((r['computed'].get('poise') or {}).get('original') for r in rows) if p is not None]
    stats = {k: int(v) for k, v in (p.split('=') for p in a.stats.split(','))}
    wid = t.find_weapon(a.weapon)
    ctx = WeaponContext(a.weapon, a.aff, None, stats, a.two)
    choice = skill_choice(t, pairings, wid, AFFINITIES.index(a.aff), ctx.level)
    grip = 'both' if a.two else 'one'
    term = skill_term(t, wid, choice, ctx, ctx.level, a.best, fp_bar, a.engagements, None, defender,
                      def_mod, None, poises,
                      available=mountable_skills(t, wid, AFFINITIES.index(a.aff), ctx.level),
                      reach=True, grip=grip, measure_all=True)
    print(f"{a.weapon} {a.aff}+{ctx.level} {'2H' if a.two else '1H'}: {len(rows)} {a.filter} builds of RL "
          f"{a.rl - a.window}-{a.rl + a.window}; median FP bar {fp_bar:.0f}; {a.engagements} landed hits a fight; "
          f"score here = damage per second of commitment vs the median defender (the ranking passes its own)")
    print(f"  {'skill':30}{'p':>6}{'FP':>4}{'uses':>5}{'share':>6}{'hits':>5}{'dmg':>6}{'roll':>6}{'next':>6}"
          f"{'stag%':>6}{'score':>7}{'gain':>7}  buffs")
    for o in term['options']:
        if 'error' in o:
            print(f"  {o['name'][:29]:30}{o['p']:>6.2f}  error {o['error']}")
            continue
        print(f"  {o['name'][:29]:30}{o['p']:>6.2f}{o['fp']:>4}{o['uses'] if o['uses'] is not None else '-':>5}"
              f"{o['share']:>6.2f}{o['hits']:>5}{o['dmg']:>6.0f}{o['roll'] or '-':>6}{o['next'] or '-':>6}"
              f"{100 * (o['stagger'] or 0):>6.0f}{o['score'] or 0:>7.0f}{o['gain']:>7.0f}  "
              f"{','.join(str(b) for b in o['buff_roots']) or '-'}")
    print(f"  corpus-weighted option value {term['value_corpus']:.1f}; score {a.best:.0f} -> "
          f"{term['score_corpus']:.1f} (SKILL_WEIGHT {SKILL_WEIGHT})")
    print(f"\n  every skill it can mount ({len(term['available'])}): reach m (b = bullet model, i = class "
          f"median), cov = coverage factor; score = damage per second of commitment here; dmg = the "
          f"hits that land (`skill_landing`), all = every hit, @2.5 = hits landed on a defender 2.5 m "
          f"ahead (mean over the corpus poises), all-sc = the score with every hit")
    print(f"  {'skill':30}{'FP':>4}{'share':>6}{'hits':>5}{'@2.5':>6}{'dmg':>6}{'all':>6}{'roll':>6}{'next':>6}"
          f"{'reach':>7}{'cov':>6}{'score':>7}{'all-sc':>7}{'gain':>7}  classes")
    for o in sorted(term['available'], key=lambda o: -(o['share'] * o['gain'] or o['dmg'] / 1e6)):
        reach = '-' if o.get('reach') is None else \
            f"{o['reach']:.2f}{ {'bullet': 'b', 'inferred': 'i'}.get(o.get('reach_source'), '')}"
        at = ((o.get('landing') or {}).get('by_distance') or {}).get(ENGAGE_DISTANCE_M)
        print(f"  {o['name'][:29]:30}{o['fp']:>4}{o['share']:>6.2f}{o['hits']:>5}"
              f"{at['hits'] if at else '-':>6}{o['dmg']:>6.0f}{o.get('dmg_all', o['dmg']):>6.0f}"
              f"{o['roll'] or '-':>6}{o['next'] or '-':>6}{reach:>7}{o.get('coverage') or 0:>6.2f}"
              f"{o['score'] or 0:>7.0f}{o.get('score_all') or o['score'] or 0:>7.0f}{o['gain']:>7.0f}  "
              f"{'+'.join(o['classes']) or '-'}")
    b = term['best']
    print(f"  best available: {b['name'] if b else 'none'}; option value {term['value']:.1f}; "
          f"score {a.best:.0f} -> {term['score']:.1f}")


def cmd_landing(t, a):
    """Per hit of each named skill on one weapon as built: when it starts, how its contact is
    found, its damage and poise, and the share of the corpus poises it lands on at each of the
    `LANDING_DISTANCES_M` (section 15, `skill_landing`)."""
    def_mod = _load('er_mechanics_defense', 'er-mechanics-defense.py')
    opt = _load('er_builds_optimize', 'er-builds-optimize.py')
    rows = corpus_slots(a.mirror, a.rl - a.window, a.rl + a.window, a.filter)
    defender = opt.bracket_defender([{'computed': r['computed']} for r in rows])
    poises = [p for p in ((r['computed'].get('poise') or {}).get('original') for r in rows) if p is not None]
    stats = {k: int(v) for k, v in (p.split('=') for p in a.stats.split(','))}
    wid = t.find_weapon(a.weapon)
    ctx = WeaponContext(a.weapon, a.aff, None, stats, a.two)
    print(f"{a.weapon} {a.aff}+{ctx.level} {'2H' if a.two else '1H'}: {len(poises)} corpus poises "
          f"(median {statistics.median(poises):.1f}), damage on the median defender")
    for name in a.skills.split(','):
        sid = t.find_arts(name.strip())
        hits = skill_hits(t, wid, sid, ctx, ctx.level)
        pvp_damage(t, wid, hits, defender, def_mod)
        land = skill_landing(t, wid, sid, hits, poises)
        ft = _fa_tables(t)
        fa = _pose()[2]
        tae = skill_tae(t, sid) or {}
        print(f"\n{t.arts_name(sid)}: {len(hits)} hits, all {land['dmg_all']:.0f} -> landed {land['dmg']:.0f} "
              f"(geometry {land['geometry']}; connects at {land['connects']})")
        print(f"  {'#':>2} {'kind':6}{'anim':>7}{'f':>4}{'real':>6}{'contact':>8}{'atk':>11}{'lv':>3}{'knock':>6}"
              f"{'poise':>7}{'dmg':>6}  " + ''.join(f"{d:>6}" for d in LANDING_DISTANCES_M) + '  touch')
        sources = iter(land['sources'])
        same = [h for h in hits if h.get('anim') == hits[0].get('anim')]
        touch = iter(''.join('t' if c(lambda _w, d=d: d) is not None else '.' for d in LANDING_DISTANCES_M)
                     for _, c, _, _ in _hit_contacts(t, wid, sid, same))
        for i, h in enumerate(hits):
            events = tae.get(h.get('anim')) or []
            real = ATK.real_frame(ATK.clip_to_real(events)(h['frame'] / TAE_FPS)) if events else '-'
            mine = h.get('anim') == hits[0].get('anim')
            src = next(sources) if mine else 'other anim'
            tt = next(touch) if mine else '-'
            fr = t.final_rate.get(h['final_rate_id']) if h['final_rate_id'] >= 0 else None
            lv = fa.reaction_level(ft, h['atk_row'], True) if h['atk_row'] in ft.atk else '-'
            kd = (t.atk_extra.get(h['atk_row']) or {}).get('knockbackDist', 0.0)
            shares = []
            for d in LANDING_DISTANCES_M:
                one = skill_landing(t, wid, sid, hits, poises, distances=(d,))
                shares.append(one['hit_share'][i] if one['geometry'] == 'measured' else None)
            print(f"  {i:>2} {h['kind']:6}{h.get('anim') or 0:>7}{h['frame']:>4}{real:>6}{src:>8}{h['atk_row']:>11}"
                  f"{lv:>3}{kd:>6.2f}{(h.get('poise') or 0) * (fr['saRate'] if fr else 1.0):>7.1f}"
                  f"{h.get('pvp_damage_total', 0):>6.0f}  " + ''.join('     -' if s is None else f"{s:>6.2f}"
                                                                     for s in shares) + f"  {tt}")
        print('  hits landed (mean over poises): ' + ', '.join(
            f"{d} m {v['hits']}" for d, v in land['by_distance'].items()))


def cmd_pvp(t, a):
    """Per-hit numbers of the popular skills, each on its most common weapon in the window."""
    ar_mod = _load('er_mechanics_ar', 'er-mechanics-ar.py')
    def_mod = _load('er_mechanics_defense', 'er-mechanics-defense.py')
    opt = _load('er_builds_optimize', 'er-builds-optimize.py')
    rows = corpus_slots(a.mirror, a.rl - a.window, a.rl + a.window, a.filter)
    defender = opt.bracket_defender([{'computed': r['computed']} for r in rows])
    stats = adoption(t, rows)
    tables = ar_mod.Tables(None)
    names = a.skills.split(',') if a.skills else \
        [n for n, _ in sorted(stats.items(), key=lambda kv: -kv[1]['builds'])[:a.top]]
    print(f'RL {a.rl}+-{a.window} {a.filter}: {len(rows)} builds; median defender; PvP rates applied; '
          'weapon at max level, stats = median of builds pairing that weapon with the skill')
    for name in names:
        try:
            sid = t.find_arts(name)
        except SystemExit:
            print(f'  {name}: not found')
            continue
        e = stats.get(t.arts_name(sid))
        pair = e['pairs'].most_common(1)[0][0] if e and e['pairs'] else None
        if pair is None:
            # Nobody in the window pairs it: put it on the first STR reference weapon that takes
            # it, Heavy when the ash allows Heavy, else the ash's default affinity.
            gem = t.ash_gems().get(sid)
            for ref in STR_REFERENCE_WEAPONS:
                rid = t.find_weapon(ref)
                if gem is not None:
                    g = t.gem[gem]
                    aff = 1 if g['configurableWepAttr01'] else g['defaultWepAttr']
                    if t.can_mount(rid, gem, aff, 25)[0]:
                        pair = (ref, AFFINITIES[aff])
                        break
            if pair is None:
                wid = _probe_weapon(t, sid, gem)
                pair = (t.reg.weapon_names.get(wid), 'Standard') if wid else None
        if pair is None:
            print(f'  {name}: no weapon')
            continue
        wname, aff = pair
        users = [r for r in rows if any(s['name'] == wname and effective_skill(t, s)[0] == t.arts_name(sid)
                                        for s in r['slots'])] or rows
        st = {k: int(statistics.median(r['stats'][k] for r in users)) for k in ('str', 'dex', 'int', 'fth', 'arc')}
        two = sum(r['is2h'] for r in users) * 2 > len(users)
        try:
            wid = t.find_weapon(wname)
            ctx = WeaponContext(wname, aff, None, st, two, tables)
        except (SystemExit, KeyError, StopIteration) as exc:
            print(f'  {name} on {wname} {aff}: {exc}')
            continue
        level = ctx.level
        hits = skill_hits(t, wid, sid, ctx, level)
        total = pvp_damage(t, wid, hits, defender, def_mod)
        prof = skill_profile(t, sid, wid, level, two)
        print(f"\n{t.arts_name(sid)} on {wname} {aff}+{level} {'2H' if two else '1H'} "
              f"STR {st['str']} DEX {st['dex']} INT {st['int']} FTH {st['fth']} ARC {st['arc']}; "
              f"AR {round(sum(ctx.ar_by.values()))}; classes {'+'.join(prof['classes']) or '-'}; "
              f"FP {t.arts[sid]['useMagicPoint_L2']}; anim {main_anim(prof)}")
        for h in hits:
            mv = '/'.join(str(v) for v in h['mv'].values())
            print(f"  f{h['frame']:<4} {h['kind']:6} atk {h['atk_row']} MV {mv}"
                  f"{' flat ' + '/'.join(str(v) for v in h['flat'].values()) if h['flat'] else ''}"
                  f"{'' if h['from_weapon'] else ' (not from weapon)'}"
                  f" attack {round(sum(h['attack'].values()))} -> {h['pvp_damage']} dmg"
                  f" ({h['phys_type']}, PvP x{h['final_rate']}), poise {h['poise']}")
        for s in skill_buffs(t, prof):
            print(f"  {s['effect']} {describe_speffect(s)[:400]}")
        for s in skill_buffs(t, prof, on_target=True):
            print(f'  on hit {describe_speffect(s)[:300]}')
        print(f'  total {round(total)} (the hits above, one each)')


#: Bootstrap resamples and seed of `ash_adoption_check`.
CHECK_BOOT = 2000
CHECK_SEED = 20260929


def ash_adoption_check(results, mirror, rl_lo, rl_hi, boot=CHECK_BOOT, seed=CHECK_SEED):
    """The within-class ash coefficient of a ranking (section 14): a check on the skill term,
    never a target it is fitted to.

        log1p(primary adoption) ~ class dummies + within-class score percentile + ash

    One row per weapon: its best grip's `moveset.score` from `er-builds-pvp.py --sort score
    --json` `results`; the percentile is its rank within its `wepType` class, 0 worst to 1 best
    (ties averaged, a class of one at 0.5); `ash` is 1 unless the sweep row's `kind` is `unique`.
    Adoption is `er-mechanics-moveset.grip_shares(mirror, rl_lo, rl_hi)['_adoption']`, a weapon
    absent from it at 0. The CI is a percentile bootstrap over weapons."""
    import numpy as np
    moveset = _load('er_mechanics_moveset', 'er-mechanics-moveset.py')
    gap = _load('er_builds_adoption_gap', 'er-builds-adoption-gap.py')
    from pathlib import Path
    adoption = {plain(k): v for k, v in moveset.grip_shares(Path(mirror), rl_lo, rl_hi)['_adoption'].items()}
    weapon_rows, _ = gap.weight_tables()
    wep_type = {plain(n): r['wepType'] for n, r in weapon_rows.items()}
    best = {}
    for r in results:
        name = plain(r['weapon'])
        score = (r.get('moveset') or {}).get('score')
        if score is None or name not in wep_type:
            continue
        unique = r.get('kind') == 'unique'
        prev = best.get(name)
        if prev is None or score > prev[0]:
            best[name] = (score, unique or (prev[1] if prev else False))
        elif unique:
            best[name] = (prev[0], True)
    names = sorted(best)
    by_class = collections.defaultdict(list)
    for n in names:
        by_class[wep_type[n]].append(n)
    pct = {}
    for members in by_class.values():
        scores = sorted(best[n][0] for n in members)
        for n in members:
            s = best[n][0]
            lo = scores.index(s)
            hi = len(scores) - 1 - scores[::-1].index(s)
            pct[n] = 0.5 if len(scores) == 1 else ((lo + hi) / 2) / (len(scores) - 1)
    classes = sorted(by_class)
    col = {c: i for i, c in enumerate(classes)}
    x = np.zeros((len(names), len(classes) + 2))
    y = np.zeros(len(names))
    for i, n in enumerate(names):
        x[i, col[wep_type[n]]] = 1.0
        x[i, -2] = pct[n]
        x[i, -1] = 0.0 if best[n][1] else 1.0
        y[i] = math.log1p(adoption.get(n, 0))

    def fit(rows):
        coef, *_ = np.linalg.lstsq(x[rows], y[rows], rcond=None)
        return coef[-2], coef[-1]

    every = np.arange(len(names))
    b_pct, b_ash = fit(every)
    rng = np.random.default_rng(seed)
    draws = np.array([fit(rng.integers(0, len(names), len(names))) for _ in range(boot)])
    return {'weapons': len(names), 'classes': len(classes), 'ash_weapons': int(x[:, -1].sum()),
            'adopted': int(sum(adoption.get(n, 0) > 0 for n in names)),
            'ash_coef': float(b_ash), 'ash_ratio': float(math.exp(b_ash)),
            'ash_ci': [float(v) for v in np.percentile(draws[:, 1], (2.5, 97.5))],
            'pct_coef': float(b_pct), 'pct_ci': [float(v) for v in np.percentile(draws[:, 0], (2.5, 97.5))],
            'ash_over_pct': float(b_ash / b_pct) if b_pct else None}


def cmd_compare(t, a):
    """Two `er-builds-pvp.py --sort score --json` rankings side by side (section 15): the top rows
    of the new one, the largest rank moves, which skill is each row's best option, and the rows of
    one weapon class."""
    def ranked(path):
        with open(path) as fh:
            rows = [r for r in json.load(fh)['results'] if r['moveset'].get('best_opener')]
        rows.sort(key=lambda r: -r['moveset']['score'])
        return {(r['weapon'], r['two']): (i + 1, r) for i, r in enumerate(rows)}

    old, new = ranked(a.before), ranked(a.pvp)

    def cells(r):
        ts = r.get('skill_term') or {}
        b = ts.get('best') or {}
        mv = r['moveset']
        corpus = (ts.get('score_corpus') or mv['base_score']) - mv['base_score']
        reach = '-' if b.get('reach') is None else f"{b['reach']:.1f}{ {'bullet': 'b', 'inferred': 'i'}.get(b.get('reach_source'), '')}"
        every = b.get('score_all')
        shown = (b.get('buff') or {}).get('score') if b.get('as') == 'buff' else b.get('score')
        name = (b.get('name') or '-')[:20] + (' (b)' if b.get('as') == 'buff' else '')
        return (f"score {mv['score']:6.0f} base {mv['base_score']:6.0f} sk+ {mv['score'] - mv['base_score']:5.0f} "
                f"skC {corpus:5.0f}  best {name:24} {shown or 0:5.0f} "
                f"(all hits {'-' if every is None else f'{every:5.0f}'}) reach {reach:>6}")

    def label(k):
        return f"{k[0][:30]:30} {'2H' if k[1] else '1H'}"

    gained = sum((r['moveset']['score'] - r['moveset']['base_score']) > 0.5 for _, r in new.values())
    print(f"{len(new)} rows ({len(old)} before); {gained} gain from their skill")
    print(f"\ntop {a.top} of {a.pvp}")
    for k, (i, r) in sorted(new.items(), key=lambda kv: kv[1][0])[:a.top]:
        print(f"  {i:4} ({old.get(k, ('-',))[0]:>4}) {label(k)} {r['aff'][:9]:9} {cells(r)}")
    moves = sorted(((old[k][0] - i, k) for k, (i, _) in new.items() if k in old), reverse=True)
    for title, part in (('largest rises', moves[:a.top]), ('largest falls', moves[::-1][:a.top // 2])):
        print(f'\n{title}')
        for d, k in part:
            print(f"  {old[k][0]:4} -> {new[k][0]:4} ({d:+5}) {label(k)} {cells(new[k][1])}")
    best = collections.Counter(((r.get('skill_term') or {}).get('best') or {}).get('name') or '-'
                               for _, r in new.values())
    print('\nbest option per row: ' + ', '.join(f'{n} {c}' for n, c in best.most_common(20)))
    if a.cls:
        kind = t.reg.weapon[t.find_weapon(a.cls)]['wepType']
        print(f'\nwepType {kind} (the class of {a.cls})')
        for k, (i, r) in sorted(new.items(), key=lambda kv: kv[1][0]):
            try:
                wid = t.find_weapon(k[0])
            except SystemExit:
                continue
            if t.reg.weapon[wid]['wepType'] == kind:
                print(f"  {i:4} ({old.get(k, ('-',))[0]:>4}) {label(k)} {r.get('kind') or '-':8} {r['aff'][:9]:9} {cells(r)}")


def _ranks(values):
    """Ranks 1..n, ties averaged."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return out


def spearman(x, y):
    """Spearman rank correlation (Pearson on tie-averaged ranks); None when either side is flat."""
    rx, ry = _ranks(x), _ranks(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    sy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return sxy / (sx * sy) if sx and sy else None


def class_mounts(t, rows, kind):
    """Counter {SwordArtsParam id: slots} of the ash-capable weapons of `wepType` `kind` in
    `corpus_slots` rows: the mounted ash, or the weapon's own skill when none is named."""
    out = collections.Counter()
    for r in rows:
        for s in r['slots']:
            try:
                wid = t.find_weapon(s['name'])
            except SystemExit:
                continue
            w = t.reg.weapon[wid]
            if w['wepType'] != kind or w['gemMountType'] != 2:
                continue
            name, _ = effective_skill(t, s)
            try:
                out[t.find_arts(name)] += 1
            except SystemExit:
                continue
    return out


def option_scores(result):
    """{skill name: the model's best score for it on one ranking row}: over the row's scored
    options (`skill_term` 'available', every variant), the larger of its hit or utility score and
    its buff score (section 16)."""
    out = {}
    for o in (result.get('skill_term') or {}).get('available') or []:
        v = max(o.get('score') or 0.0, o.get('buff_score') or 0.0)
        if v > out.get(o['name'], -1.0):
            out[o['name']] = v
    return out


def cmd_ashrank(t, a):
    """The model's order of the ashes a weapon class mounts against how often the PvP corpus
    mounts them (section 16 validation). Nothing is fitted to it."""
    rows = corpus_slots(a.mirror, a.rl - a.window, a.rl + a.window, 'pvp')
    kind = t.reg.weapon[t.find_weapon(a.cls)]['wepType']
    mounts = class_mounts(t, rows, kind)
    names = [t.arts_name(s) for s, _ in mounts.most_common(a.top)] if not a.names else \
        [t.arts_name(t.find_arts(n.strip())) for n in a.names.split(',') if n.strip()]
    names += [n.strip() for n in (a.extra or '').split(',') if n.strip() and n.strip() not in names]
    count = {t.arts_name(s): c for s, c in mounts.items()}
    print(f"wepType {kind} ({a.cls}): corpus mounts, RL {a.rl - a.window}-{a.rl + a.window} pvp: "
          + ', '.join(f"{n} {count.get(n, 0)}" for n in names))
    out = {}
    for path in a.pvp:
        with open(path) as fh:
            results = json.load(fh)['results']
        for r in results:
            if a.weapons and r['weapon'] not in a.weapons.split(','):
                continue
            try:
                if t.reg.weapon[t.find_weapon(r['weapon'])]['wepType'] != kind:
                    continue
            except SystemExit:
                continue
            sc = option_scores(r)
            have = [n for n in names if n in sc]
            rho = spearman([count.get(n, 0) for n in have], [sc[n] for n in have]) if len(have) > 2 else None
            label = f"{os.path.basename(path)} {r['weapon']} {'2H' if r['two'] else '1H'} {r['aff']}" \
                    f"{'+' + r['grease'] if r.get('grease') else ''}"
            order = sorted(have, key=lambda n: -sc[n])
            best = ((r.get('skill_term') or {}).get('best') or {})
            print(f"\n{label}: base {r['moveset']['base_score']:.0f}, score {r['moveset']['score']:.0f}, "
                  f"best {best.get('name')} ({best.get('as')}); Spearman {rho if rho is None else round(rho, 3)}")
            for i, n in enumerate(order, 1):
                print(f"  {i:2} {n[:28]:28} model {sc[n]:7.0f}  corpus {count.get(n, 0)}")
            out[label] = rho
    if a.json_out:
        with open(a.json_out, 'w') as fh:
            json.dump(out, fh, indent=1)


def cmd_check(t, a):
    with open(a.pvp) as fh:
        results = json.load(fh)['results']
    c = ash_adoption_check(results, a.mirror, a.rl - a.window, a.rl + a.window)
    print(f"{c['weapons']} weapons in {c['classes']} classes ({c['ash_weapons']} ash-capable, "
          f"{c['adopted']} adopted as primary), RL {a.rl - a.window}-{a.rl + a.window}")
    print(f"  ash: {c['ash_coef']:+.3f} (x{c['ash_ratio']:.2f} adoption), CI [{c['ash_ci'][0]:+.3f}, "
          f"{c['ash_ci'][1]:+.3f}]")
    print(f"  within-class score percentile 0 -> 1: {c['pct_coef']:+.3f}, CI [{c['pct_ci'][0]:+.3f}, "
          f"{c['pct_ci'][1]:+.3f}]; ash / percentile = {c['ash_over_pct']:.2f}")


# ---------------------------------------------------------------------------------------------
# selftest

def _selftest_openings(t, check):
    """The weapon- and grip-dependent opening (`skill_profile` 'opening'), anchored on the
    behavior script it emulates."""
    if not os.path.exists(COMMON_DEFINE_HKS):
        print(f'SKIP opening checks: {COMMON_DEFINE_HKS} not found')
        return
    spec = importlib.util.spec_from_file_location('er_hks_disasm', os.path.join(HERE, 'er-hks-disasm.py'))
    hks = importlib.util.module_from_spec(spec)
    sys.modules['er_hks_disasm'] = hks          # its dataclasses resolve through sys.modules
    spec.loader.exec_module(hks)
    _, _, _, main = hks.parse_file(COMMON_DEFINE_HKS)
    hks.name_functions(main)
    fn = next(f for f in hks.walk(main) if f.name == 'GetSwordArtsDiffCategory')
    check('GetSwordArtsDiffCategory constants match the emulation',
          {int(c) for c in fn.consts if isinstance(c, (int, float))}, DIFF_CONSTANTS,
          'HKS common_define.hks GetSwordArtsDiffCategory (lines 888-956)')
    spin = t.find_arts('Spinning Slash')
    check('SwordArtsCategory[3] (Spinning Slash, a603)',
          sword_arts_categories().get(t.arts[spin]['swordArtsTypeNew']), [0, 2, 3, 24, 58],
          'HKS common_define.hks main chunk line 756')
    bastard = t.find_weapon('Bastard Sword')
    check('Bastard Sword idle categories (wepmotionOneHandId, wepmotionBothHandId)',
          (t.reg.weapon[bastard]['wepmotionOneHandId'], t.reg.weapon[bastard]['wepmotionBothHandId']), (2, 12),
          'REGULATION EquipParamWeapon +0xf0/+0xf1, read by EXE 0x1404d1d60 into env(358)')
    want = {('Longsword', 'one'): (40000, 18), ('Longsword', 'both'): (40000, 18),
            ('Bastard Sword', 'one'): (40200, 26), ('Bastard Sword', 'both'): (40200, 26),
            ('Halberd', 'one'): (40300, 27), ('Twinblade', 'both'): (42400, 18),
            ('Backhand Blade', 'one'): (45800, 18), ('Milady', 'both'): (40000, 18)}
    got = {}
    for (name, grip) in want:
        wid = t.find_weapon(name)
        p = skill_profile(t, spin, wid, 0, grip)
        got[(name, grip)] = (main_anim(p), opening_timing(t, spin, wid, p, main_anim(p))[0])
    check('Spinning Slash opening and first hit frame per weapon class and grip', got, want,
          'HKS ExecAttack lines 1853-1886 + GetSwordArtsDiffCategory; BEH SwordArtsOneShot CategorySelector; '
          'TAE a603 040000/040200/040300/042400/045800 (light greatsword returns 0, common_define line 899)')
    tw = skill_profile(t, spin, t.find_weapon('Twinblade'), 0, 'both')
    check('Spinning Slash follow-up on a twinblade is 042410', followup_anim(tw, 10), 42410,
          'HKS ExecAttack line 1901 SwordArtsOneShotComboCategory 24 -> 3; BEH ComboEnd_24_CMSG')
    sc = t.find_arts('Stormcaller')
    check('Stormcaller twinblade opening 042400 imports 040000 (same first hit)',
          [x['frames'][0] for x in skill_profile(t, sc, t.find_weapon('Twinblade'), 0, 'one')['anims'][42400]
           if x['kind'] == 'melee'][:1],
          [x['frames'][0] for x in skill_profile(t, sc, t.find_weapon('Twinblade'), 0, 'one')['anims'][40000]
           if x['kind'] == 'melee'][:1], 'TAE a623 042400 ImportOtherAnim -> 040000')


def selftest():
    t = AshTables(None)
    passed = failed = skipped = 0

    def check(name, got, want, source):
        nonlocal passed, failed
        ok = got == want
        passed += ok
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'} {name}: got {got!r} want {want!r}  [{source}]")

    claymore = t.find_weapon('Claymore')
    dagger = t.find_weapon('Dagger')
    greatsword = t.find_weapon('Greatsword')
    lion = t.find_arts("Lion's Claw")
    lion_gem = t.ash_gems()[lion]
    check("Lion's Claw on Claymore (Standard)", t.can_mount(claymore, lion_gem, 0)[0], True,
          'COMMUNITY: Lion\'s Claw mounts on greatswords')
    check("Lion's Claw on Dagger", t.can_mount(dagger, lion_gem, 0)[0], False,
          'COMMUNITY: not on daggers')
    bstep = t.find_arts("Bloodhound's Step")
    check("Bloodhound's Step on Dagger", t.can_mount(dagger, t.ash_gems()[bstep], 0)[0], True,
          'COMMUNITY')
    check('Claymore built-in skill', t.arts_name(t.weapon_skill(claymore)), "Lion's Claw",
          'COMMUNITY: Claymore ships with Lion\'s Claw')
    check('mounted gem wins', t.weapon_skill(claymore, t.ash_gems()[bstep]), bstep,
          'EXE 0x140675270')
    check('wepType->flag: Greatsword is colossal', WEP_TYPE_MOUNT_FLAG[t.reg.weapon[greatsword]['wepType']],
          'SwordGigantic', 'EXE 0x140d2cc70 + COMMUNITY class')
    # The jump family's opener is synthesized, so the stored slots alone drop it; the
    # shares that remain are renormalised, and a supplied jump opener starts at its
    # `neutral_in` strike from the jump input, not the landed clip's 2.5 m contact.
    fam_row = {'weapon': 'A', 'two': False,
               'moveset': {'families': {'r1': {'opener': 'r1_1', 'share': 0.4},
                                        'jump': {'opener': 'jump_r1_f', 'share': 0.4},
                                        'move': {'opener': 'run_r1', 'share': 0.2}}},
               'slots': {'r1_1': {'startup': 14.0, 'front_contact': {'2.5': 16.0}, 'dmg': 300.0, 'next': 30.0}}}
    jump = {'jump_r1_f': {'startup': 20.0, 'jump_entry': 6.0, 'front_contact': {'2.5': 17.5}, 'dmg': 500.0,
                          'next': 50.0, 'neutral_in': {'strike': 22.0}}}
    check("the exchange pool's damage fallback is this module's opponent fallback hit",
          _load('er_mechanics_exchange', 'er-mechanics-exchange.py').OPPONENT_HP_FALLBACK, OPPONENT_FALLBACK['hp'],
          'er-mechanics-exchange.OPPONENT_HP_FALLBACK')
    row_b = {'weapon': 'B', 'two': False, 'moveset': {'families': {'r1': {'opener': 'r1_1', 'share': 1.0}}},
             'slots': fam_row['slots']}
    pool_raw = {'builds': [['A|1h', 50.0], ['B|1h', 50.0]]}
    alone, _, _ = opponents_from_results(pool_raw, [fam_row, row_b], openers='families')
    both, _, _ = opponents_from_results(pool_raw, [fam_row, row_b], openers='families',
                                        slots_fn=lambda r: {**r['slots'], **(jump if r['weapon'] == 'A' else {})})
    check('families without the jump openers: A keeps its whole build on R1 (renormalised), as B does',
          [round(float(x), 6) for x in alone.w], [0.5, 0.5], 'er-effects-rs-8uha')
    check('families with them: A throws R1 and the jump at a quarter each, the jump from its input strike '
          '(22, not 17.5)', ([round(float(x), 6) for x in both.w], [float(x) for x in both.startup]),
          ([0.25, 0.25, 0.5], [16.0, 22.0, 16.0]), 'er-effects-rs-8uha, er-builds-pvp.jump_openers')
    tae_ok = skill_tae(t, lion) is not None
    if not tae_ok:
        print('SKIP TAE checks: ER_PLAYER_TAE_DIR not found')
        skipped += 1
    else:
        check("Lion's Claw TimeAct", f"a{SKILL_TAE_BASE + t.arts[lion]['swordArtsTypeNew']}", 'a600', 'TAE')
        p = skill_profile(t, lion, claymore)
        check("Lion's Claw classes", p['classes'], ['melee'], 'COMMUNITY: a flip slam, no projectile')
        ctx = WeaponContext('Claymore', 'Standard', 25, {'str': 40, 'dex': 20}, False)
        hits = skill_hits(t, claymore, lion, ctx, 25)
        check("Lion's Claw FP hit MV", [h['mv']['physical'] for h in hits], [240], 'REGULATION row 300300820')
        check("Lion's Claw attack = AR x 2.4", abs(hits[0]['attack']['physical'] - ctx.ar_by['physical'] * 2.4) < 0.5,
              True, 'EXE 0x1406845a0 with MV 240')
        storm = t.find_arts('Storm Stomp')
        check('Storm Stomp classes', skill_profile(t, storm, claymore)['classes'], ['bullet'],
              'COMMUNITY: a stomp that releases a wind burst')
        sh = skill_hits(t, claymore, storm, ctx, 25)
        check('Storm Stomp bullet = 50 x baseAtkRate x scaling (AECP 51030)',
              round(sh[0]['attack']['physical'], 1),
              round(50 * ctx.base_atk_rate * ctx.multiplier('physical', 51030), 1),
              'EXE 0x1406845a0 flag-clear branch, k = baseAtkRate (FUN_140d57090); '
              'REGULATION AtkParam 30300870 overwriteAttackElementCorrectId')
        check('Storm Stomp PvP rate row', sh[0]['final_rate_id'], 10000, 'REGULATION AtkParam 30300870')
        check('FP: 20-cost skill with 10 FP plays in full', t.has_enough_fp(10, 20), True,
              'EXE 0x1404802a0, 0.5 at 0x1432a3be0')
        check('FP: 20-cost skill with 9 FP plays the no-FP version', t.has_enough_fp(9, 20), False,
              'EXE 0x1404802a0')
        check('FP cost with Carian Filigreed-style 0.75 rate', t.fp_cost(lion, 'L2', 0.75), 15,
              'EXE 0x14068c520 ceil(rate x base)')
        check('one-handed, seal in left: right skill fires', t.active_skill(lion, 10, False), lion,
              'EXE 0x14047fdb0, SwordArtsParam 10 isRefRightArts')
        check('one-handed, shield with Parry in left: left skill fires',
              t.active_skill(lion, t.find_arts('Parry'), False), t.find_arts('Parry'), 'EXE 0x14047fdb0')
        endure = t.find_arts('Endure')
        pe = skill_profile(t, endure, claymore)
        check('Endure classes', pe['classes'], ['buff'], 'COMMUNITY: a poise buff')
        sp = next(x for x in pe['anims'][40000] if x['event'] == 'skill speffect')
        check('Endure SpEffect / duration', (sp['speffect']['id'], sp['speffect']['duration_s']),
              (1650, 3.0), 'REGULATION SpEffectParam 1650')
        check('Endure PvP damage taken', sp['speffect']['effects'].get('defPlayerDmgCorrectRate_Physics'),
              0.6, 'REGULATION SpEffectParam 1650')
        hoar = t.find_arts('Hoarfrost Stomp')
        ph = skill_profile(t, hoar, claymore)
        check('Hoarfrost Stomp classes', ph['classes'], ['bullet'],
              'COMMUNITY: frost mist along the ground')
        check('Hoarfrost frost lands on the target, not the caster',
              ([s['id'] for s in skill_buffs(t, ph)], [s['id'] for s in skill_buffs(t, ph, on_target=True)]),
              ([], [1800, 1801]), 'REGULATION Bullet 2260 spEffectId0, SpEffect 1800 effectTargetSelf 0')
        vow = t.find_arts('Golden Vow')
        pv = skill_profile(t, vow, claymore)
        check('Golden Vow is a buff delivered by an area bullet', (pv['classes'], [s['id'] for s in skill_buffs(t, pv)]),
              (['buff'], [1730]), 'COMMUNITY: buffs self and allies nearby; REGULATION SpEffect 1730')
        wild = t.find_arts('Wild Strikes')
        pw = skill_profile(t, wild, claymore)
        fp_events = sum(x['kind'] == 'fp' for x in pw['anims'][40051])
        check('Wild Strikes charges FP per swing', fp_events, 2, 'TAE a610 040051')
        longsword = t.find_weapon('Longsword')
        cases = [('Sacred Blade', ['buff', 'bullet', 'melee'], 'weapon holy buff 821 (+90 holy) through 820'),
                 ('Phantom Slash', ['bullet', 'melee'], 'damaging bullet 2663 five links down'),
                 ('Bloody Slash', ['melee'], 'its 1763 is an HP cost, 1764 bleed lands on the target'),
                 ('Poisonous Mist', ['buff', 'bullet (status only)', 'melee'], 'the cloud poisons, the weapon buff 831'),
                 ('Seppuku', ['buff'], '1753 bleeds the caster; 1755 is the buff')]
        for name, want, why in cases:
            check(f'{name} classes', skill_profile(t, t.find_arts(name), longsword)['classes'], want,
                  f'REGULATION + TAE: {why} (audit subagent, 2026-09-29)')
        shield = t.find_weapon('Heater Shield')
        check('Parry / Buckler Parry / Golden Parry are parries',
              [skill_profile(t, t.find_arts(n), shield)['classes'] for n in ('Parry', 'Buckler Parry', 'Golden Parry')],
              [['parry'], ['parry'], ['parry']], 'COMMUNITY')
        check("Bloodhound's Step is i-frames only",
              skill_profile(t, bstep, claymore)['classes'], ['i-frames'], 'COMMUNITY: a dash with invincibility')
        rkr = t.find_arts("Royal Knight's Resolve")
        check("Royal Knight's Resolve classes", skill_profile(t, rkr, claymore)['classes'], ['buff'],
              'COMMUNITY: a buff to the next attack')
        # Section 13, the skill term.
        check('FP bar 88 pays for 6 casts of 16 (the last on half cost)', fp_uses(88, 16), 6,
              'EXE 0x1404802a0: 5 x 16 = 80, 8 left >= int(16 x 0.5)')
        check('FP bar 85 pays for 5 casts of 16', fp_uses(85, 16), 5, 'EXE 0x1404802a0: 5 left < 8')
        check('a free skill has no FP limit', fp_uses(85, 0), None, 'SwordArtsParam -1/0 cost')
        check("Lion's Claw on Claymore: roll / R1 free after the hit",
              {k: v for k, v in skill_commit(t, claymore, lion, skill_profile(t, lion, claymore, 25), hits).items()
               if k in ('roll', 'next')}, {'roll': 62.8, 'next': 69.7}, 'TAE a600 040000 JumpTable, 608 speed')
        star = t.find_weapon('Starscourge Greatsword')
        pair = {'weapon': {}, 'class': {}, 'all': collections.Counter({lion: 50, OWN: 10})}
        check('a unique weapon keeps its own skill', skill_choice(t, pair, star, 0, 10),
              [(t.reg.weapon[star]['swordArtsParamId'], 1.0)], 'EquipParamWeapon gemMountType')
        ch = dict(skill_choice(t, pair, dagger, 0, 25))
        check("Lion's Claw cannot go on a Dagger, so its corpus mass goes to the dagger's own skill",
              (lion in ch, round(sum(ch.values()), 6)), (False, 1.0), 'EXE 0x140d57e70 via can_mount')
        pair['weapon'][claymore] = collections.Counter({storm: 20})
        ch = dict(skill_choice(t, pair, claymore, 1, 25))
        check('20 own-weapon pairings outweigh the corpus prior', round(ch[storm], 3),
              round(20 / (20 + SKILL_PAIRING_ALPHA), 3), 'SKILL_PAIRING_ALPHA shrinkage')
        n = len(hits)
        check('pvp_damage takes a damage_fn', pvp_damage(t, claymore, hits, None, None, lambda a, p, f: 100.0),
              100.0 * n, 'interface for er-builds-pvp corpus_hit')
        term = skill_term(t, claymore, [(lion, 1.0)], ctx, 25, 0.0, damage_fn=lambda a, p, f: 300.0)
        o = term['options'][0]
        check("Lion's Claw option: 300 dmg over 62.8 frames, 4 casts of 20 in 88 FP over 5 hits",
              (round(o['score'], 1), o['uses'], o['share']), (round(300 / 62.8 * 30, 1), 4, 0.8),
              'skill_term default score')
        check('option value = p x share x gain, score = best + SKILL_WEIGHT x value',
              round(term['score'], 3), round(SKILL_WEIGHT * 0.8 * o['score'], 3), 'section 13')
        high = skill_term(t, claymore, [(lion, 1.0)], ctx, 25, 1e6, damage_fn=lambda a, p, f: 300.0)
        check('a skill weaker than the best slot adds nothing', high['value'], 0.0, 'option value floor')
        wc = skill_term(t, claymore, [(t.find_arts('War Cry'), 0.5)], ctx, 25, 0.0)
        check('War Cry hands its buff roots to the buff term as one alternative',
              [(p, [r for r, _, _ in x]) for p, x in wc['buff_alternatives']],
              [(0.5, [1810, 1812])], 'TAE event 331 -> SpEffect 1810/1812')
        # Section 14, utility ashes.
        rolls = roll_motion()
        check('medium roll: i-frames f0-13 in all four directions (f13-16 needs stateInfo 290)',
              [m['iframes'] for m in rolls], [13.0] * 4, 'TAE a000 027110-027113 JumpTable 8')
        check('medium roll: R1 from frame 20, forward 3.65 m', (round(rolls[0]['ready']), rolls[0]['distance']),
              (20, 3.65), 'TAE a000 027110 + hkx root motion')
        # He walks or runs no sooner than the move cancel (`punish_lead`): f22 in every direction.
        # Forward at 9 m he still has 1.99 m to run at f22 (old rule: from f20, 2.08 m: 35.59); back
        # at 3.5 m he presses at f22 + 26.60 (old 20 + 25.78); forward at 6 m he is in reach at f20.
        kf = _neutral().frames_per_metre()
        fwd9, back35, fwd6 = punish_lead(rolls[0], 9.0, 0.0, True), punish_lead(rolls[1], 3.5), punish_lead(rolls[0], 6.0)
        x22, z22 = rolls[0]['path'][22]
        check('medium roll: move cancel f22 in all four directions; a punish that has to run starts there, one in '
              'reach at the R1 cancel f20, and without run he does not strike from out of reach',
              ([round(m['move']) for m in rolls], fwd9[0], round(fwd9[1], 6), round(fwd9[0] + fwd9[1], 2),
               round(back35[0] + back35[1], 2), fwd6, punish_lead(rolls[1], 3.5, 0.0, False)[2]),
              ([22] * 4, rolls[0]['move'], round((math.hypot(x22, 9.0 + z22) - PUNISH_REACH_M) * kf, 6), 36.91, 48.6,
               (rolls[0]['ready'], 0.0, True), False), 'TAE a000 027110-027113 (move JumpTable 11/78) + hkx')
        steps = skill_evasion(t, bstep, claymore)
        check("Bloodhound's Step: 040080-040083, i-frames f0-10, R1 from 16-17, 4.5-5.2 m",
              ([m['anim'] for m in steps], [m['iframes'] for m in steps], [round(m['ready']) for m in steps],
               [m['distance'] for m in steps]),
              ([f'a756_{a:06d}' for a in (40080, 40081, 40082, 40083)], [10.0] * 4, [17, 16, 16, 16],
               [5.24, 4.72, 4.49, 4.58]), 'TAE a756 + hkx root motion')
        # The step travels on after its R1 opens: a punish it carries into reach before its move
        # cancel is pressed on that frame, never at the move cancel (`punish_lead`).
        sp = steps[0]
        sp_frames = [f for f in range(int(sp['ready']) + 1, int(sp['move']))
                     if math.hypot(sp['path'][f][0], 7.5 + sp['path'][f][1]) <= PUNISH_REACH_M]
        check("Bloodhound's Step forward: move cancel f29 (R1 f17); at 7.5 m it carries him into reach on a frame "
              "between, and he presses there",
              (round(sp['move']), punish_lead(sp, 7.5)), (29, (float(sp_frames[0]), 0.0, True)) if sp_frames else None,
              'TAE a756 040080 + hkx root motion')
        check('Quickstep: i-frames f0-9', [m['iframes'] for m in skill_evasion(t, t.find_arts('Quickstep'), claymore)],
              [9.0] * 4, 'TAE a755')
        dend = defensive_buff(t, endure, longsword)
        check('Endure: 1650 from f4 for 3 s, x0.6 PvP damage taken, reaction override to the flinch',
              (dend['root'], dend['start'], dend['duration_s'], dend['damage_taken'], dend['uninterruptible']),
              (1650, 4, 3.0, 0.6, True), 'REGULATION 1650 spCategory 1001 dmgLv_* 1 -> REMAP 0 (frame-advantage)')
        dend = defensive_buff(t, endure, claymore)
        check('Endure on a greatsword opens 040200: 1655 from f4 for 3.5 s',
              (main_anim(skill_profile(t, endure, claymore)), dend['root'], dend['start'], dend['duration_s']),
              (40200, 1655, 4, 3.5), 'HKS GetSwordArtsDiffCategory idle category 2; TAE 040200; REGULATION 1655')
        _selftest_openings(t, check)
        check('War Cry is not a defensive buff', defensive_buff(t, t.find_arts('War Cry'), claymore), None,
              'REGULATION 1810/1812: attack only')
        far = Opponents([{'w': 1, 'active': 3, 'reach': 99.0, 'hp': 300.0, 'recovery': 0.0}], 'uniform')
        check('uniform timing (section 14a): the hit is avoided for tau <= i-frames - active (roll 11, step 8 of 30)',
              (round(far.dodge_value(rolls, 'r', 18, 4.0, 500)['p_evade'] * DODGE_TIMING_FRAMES, 6),
               round(far.dodge_value(steps, 's', 18, 4.0, 500)['p_evade'] * DODGE_TIMING_FRAMES, 6)),
              (11.0, 8.0), 'Opponents._table, timing uniform')
        near = Opponents([{'w': 1, 'active': 3, 'reach': 0.1, 'hp': 300.0, 'recovery': 0.0}], 'uniform')
        check('a 0.1 m attack never reaches a back step (the forward one passes through the attacker)',
              round(near.dodge_value(steps[1:2], 'b', 18, 4.0, 500)['p_evade'], 9), 1.0, 'Opponents._table')
        slow = Opponents([{'w': 1, 'active': 3, 'reach': 99.0, 'hp': 300.0, 'startup': 30.0}])
        fast = Opponents([{'w': 1, 'active': 3, 'reach': 99.0, 'hp': 300.0, 'startup': 5.0}])
        check('reaction timing (section 16): a 3-frame hit at f30 is dodged by any direction, one at f5 '
              'comes before the fastest reaction',
              (round(slow.dodge_value(rolls, 'r', 18, 4.0, 500)['p_evade'], 3),
               round(fast.dodge_value(rolls, 'r', 18, 4.0, 500)['p_evade'], 3)), (1.0, 0.0),
              'dodge_presses: reaction >= (0.25 x exp(-1.59 x 0.2) + 0.1) x 30 = 8.5 frames')
        eng = {'numerator': 600.0, 'commit': 30.0, 'dmg': 500.0, 'strike': 18.0, 'reach': 4.0,
               'exchange': {'win': 0.2, 'loss': 0.1, 'p_second': 0.5}}
        ue = utility_value(t, endure, claymore, eng, far)
        check('Endure, section 14b term kept: loss x dmg + (1 - win) x 0.4 x opponent hp',
              round(ue['legacy_hp'], 6), round(0.1 * 500 + 0.8 * 0.4 * 300, 6), 'section 14')
        check('Endure cast ahead: its 3 s VFX buff ends before the 5 s engagement spacing, so it is waited '
              'out; the committed share alone would be (1 - REACT_SHARE) x the same',
              (ue['waited_out'], ue['visible'], round(ue['through_hp'] + ue['cut_hp'], 6)),
              (True, True, round((1 - REACT_SHARE) * (0.1 * 500 + 0.8 * 0.4 * 300), 6)), 'section 17, vfxId 8570')
        check('Endure cast as an answer (0.4 x 300 when the hit falls in the buff) loses to the roll',
              ue['answer_hp'] < 0 and ue['hp'] == 0.0 and ue['commit'] == 0.0, True, 'section 17')
        check('its multiplier: (N + hp) / N x C / (C + commit)', round(utility_multiplier(ue, eng), 6),
              round((600 + ue['hp']) / 600 * 30 / (30 + ue['commit']), 6), 'section 14')
        ub = utility_value(t, bstep, claymore, eng, far)
        check("Bloodhound's Step through reach alone, uniform timing: 0.5 x (8 - 11) / 30 x 300 HP",
              round(ub['hp'], 6), round(0.5 * (8 - 11) / 30 * 300, 6), 'section 14')
        up = utility_value(t, t.find_arts('Parry'), shield, {**eng, 'crit': {'riposte_eff_hp': 1000.0},
                                                             'crit_ev': {'parry_tool': 0.2, 'exposure': 0.5}}, far)
        check('Parry on the weapon: (1 - 0.2) x PARRY_LAND x 0.5 x riposte', round(up['hp'], 6),
              round(0.8 * _load('er_mechanics_crits', 'er-mechanics-crits.py').PARRY_LAND * 0.5 * 1000, 6),
              'er-mechanics-crits weapon_crit terms')
        ut = skill_term(t, claymore, [(endure, 0.5), (bstep, 0.5)], ctx, 25, 1000.0, engagement=eng, opponents=far)
        got = {o['name']: round(o['gain'], 3) for o in ut['options']}
        check('skill_term values Endure as a utility option and floors the step at 0',
              (got['Endure'], got["Bloodhound's Step"]),
              (round(1000.0 * utility_multiplier(ue, eng) - 1000.0, 3), 0.0), 'section 14')
        # Section 15, every mountable skill and the skill's own reach.
        check('a unique weapon can fire only its own skill', mountable_skills(t, star, 0, 10),
              [t.reg.weapon[star]['swordArtsParamId']], 'EquipParamWeapon gemMountType')
        cm, dm = mountable_skills(t, claymore, 1, 25), mountable_skills(t, dagger, 0, 25)
        check("Claymore Heavy mounts Lion's Claw and Stormcaller; a Dagger mounts neither",
              (lion in cm, t.find_arts('Stormcaller') in cm, lion in dm, t.find_arts('Stormcaller') in dm),
              (True, True, False, False), 'EXE 0x140d57e70 via can_mount')
        glint = t.gem[t.ash_gems()[t.find_arts('Glintblade Phalanx')]]
        wrong = next(i for i in range(13) if not glint[f'configurableWepAttr{i:02d}'] and i != glint['defaultWepAttr'])
        check('an affinity the ash does not allow keeps it off (Glintblade Phalanx)',
              t.find_arts('Glintblade Phalanx') in mountable_skills(t, claymore, wrong, 25), False,
              'EquipParamGem configurableWepAttr, EXE 0x140d57e70')
        level = {**{k: 0 for k in BULLET_FIELDS}, 'life': 0.5, 'initVellocity': 10.0, 'maxVellocity': 10.0,
                 'minVellocity': 10.0, 'hitRadius': 0.1, 'dist': 0.0}
        path, ending, _, _ = bullet_flight(level, (0.0, 1.0, 0.0), (0.0, 0.0))
        check('bullet flight: 10 m/s level, no gravity, 0.5 s -> 31 frames (the expiry frame moves too), expires',
              (round(path[-1][2], 6), ending), (round(31 * 10 / 60, 6), 'expire'),
              'FUN_14039ac30 per-frame rule, 60 fps')
        path, ending, _, _ = bullet_flight({**level, 'gravityOutRange': 20.0}, (0.0, 1.0, 0.0), (0.0, 0.0))
        check('with gravity 20 it lands before its life ends (hit at height <= radius)',
              (ending, path[-1][1] <= 0.1, 2.5 < path[-1][2] < 3.5), ('hit', True, True),
              'FUN_14039f860; flat ground (INFERRED)')
        # In-flight homing (bd er-effects-rs-rw25): a target 100 m to the side of a 10 m/s shot.
        hom = {**level, 'life': 1.0, 'minVellocity': 0.0, 'dist': 100.0, 'homingAngle': 60,
               'homingBeginDist': 0.0, 'hormingStopRange': 0.0}
        aside = lambda k: (100.0, 1.0, 0.0)  # noqa: E731
        plain, _, _, _ = bullet_flight(level, (0.0, 1.0, 0.0), (0.0, 0.0))
        check('a shot whose homingAngle is 0 flies the same path with a target as without one',
              bullet_flight(level, (0.0, 1.0, 0.0), (0.0, 0.0), aim3d=aside)[0] == plain, True,
              'FUN_140398270: rate 0 turns nothing; homes()')
        hp, _, hd, _ = bullet_flight(hom, (0.0, 1.0, 0.0), (0.0, 0.0), aim3d=aside)
        check('homingAngle 60 at 60 fps turns 1 deg a frame from the second frame: 60 deg after the 61 '
              'moves of 1 s, at the straight shot\'s speed and height',
              (round(math.degrees(math.atan2(hd[0], hd[2])), 6), len(hp),
               round(sum(math.dist(a, b) for a, b in zip(hp, hp[1:])), 6), {q[1] for q in hp}),
              (60.0, 61 + 1, round(61 * 10 / 60, 6), {1.0}),
              'FUN_140398270 rate x deg->rad x dt (0x1403984cb); OnUpdate 0x1403939d0 skips the first update')
        _, _, bd, _ = bullet_flight({**hom, 'homingBeginDist': 2.05}, (0.0, 1.0, 0.0), (0.0, 0.0), aim3d=aside)
        check('homingBeginDist 2.05 m at 10 m/s: no turn until the 13th frame has more than that flown: 48 deg',
              round(math.degrees(math.atan2(bd[0], bd[2])), 6), 48.0, 'FUN_140398270: flown <= homingBeginDist returns')
        _, _, sd, _ = bullet_flight({**hom, 'hormingStopRange': 200.0}, (0.0, 1.0, 0.0), (0.0, 0.0), aim3d=aside)
        near = lambda k: (0.3, 1.0, 3.0)  # noqa: E731
        np_, _, nd, _ = bullet_flight({**hom, 'hormingStopRange': 1.0}, (0.0, 1.0, 0.0), (0.0, 0.0), aim3d=near)
        stop_at = next(k for k, q in enumerate(np_) if math.hypot(0.3 - q[0], 3.0 - q[2]) < 1.0)
        check('inside hormingStopRange the turn stops for good: none from 200 m, and with 1 m the heading '
              'is fixed from the frame it comes within 1 m of a target 3 m ahead',
              (round(math.degrees(math.atan2(sd[0], sd[2])), 9),
               all(abs(math.atan2(b[0] - a[0], b[2] - a[2]) - math.atan2(nd[0], nd[2])) < 1e-9
                   for a, b in zip(np_[stop_at:], np_[stop_at + 1:]))), (0.0, True),
              'FUN_140398270 0x14039847f clears CSBulletIns+0xd80 bit 3')
        check('homes(): an attached row (FollowType 1, the Phalanx root) never turns; FollowType 4 does',
              (homes({'homingAngle': 90, 'FollowType': 1}), homes({'homingAngle': 90, 'FollowType': 4}),
               homes({'homingAngle': -1, 'FollowType': 0})), (False, True, False),
              'OnUpdate 0x1403939d0: FollowType & 3 (BulletParam +0x9a) picks FUN_14039afc0')
        # The Phalanx roots hover until their own search launches them (`SEARCH_ARM_S`), no sooner
        # than 2.0 s; their blades and the rows without a search keep the old rules.
        ph = [t.bullet.get(b) or {} for b in (10430000, 2300, 10430001, 2301)]
        check('search_launched: the Glintblade Phalanx roots (spell 10430000, ash 2300: life -1, FollowType 1, '
              'autoSearchNPCThinkID 1000100 / 1000102) hover 2.0 s (121 moves at 60 fps), their blades '
              '(10430001, 2301: life 1 s, no search) fly their own life',
              ([search_launched(r) for r in ph], [r.get('autoSearchNPCThinkID') for r in ph],
               [flight_moves(r) for r in ph]),
              ([True, True, False, False], [1000100, 1000102, 0, 0], [121, 121, 61, 61]),
              'REGULATION Bullet; FUN_14039ee00 state list 0x142a25838, timer .data 0x143b15c20 = 2.0')
        # The defender reads a search launch's timing from the launch, not the cast (`search_cues`,
        # `burst_cue`): the caster picks it. Each tree's blades are read from the root's last move
        # (121 moves at 60 fps), the roots from the cast.
        sc_ = []
        for root_ in (10430000, 2300):
            ent_ = fly_shots(t, bullet_tree(t, root_))
            sc_.append(sorted({(e[0]['bullet'], round(q, 6)) for e, q in zip(ent_, search_cues(t, ent_))}))
        check('search_cues: the Glintblade Phalanx blades (10430001, 2301) are read from their root\'s search '
              'launch, 121 / 60 s after the event; the roots from the cast',
              sc_, [[(10430000, 0.0), (10430001, round(121 / BULLET_FPS, 6))],
                    [(2300, 0.0), (2301, round(121 / BULLET_FPS, 6))]],
              'fly_shots lineage; FUN_14039ee00 hover state, HitBulletID at its end')
        import numpy as np
        g0_ = {'T': np.arange(130.0, 140.0), 'X': np.zeros(10), 'Z': np.full(10, -2.5), 'R': np.full(10, 1.0),
               'PK': np.full(10, -math.inf)}
        gc_ = {**g0_, 'CUE': 121.0}
        ro_ = [reaction_outcome(gs_, [1.0] * len(gs_), 2.5, cue=q) for gs_, q in
               (([gc_], 0.0), ([g0_], 121.0), ([g0_], 0.0), ([gc_, g0_], 0.0))]
        check('burst_cue: a burst every geometry of which carries a search launch is read from it (never before '
              'the part\'s own cue), one that does not from the cue; reaction_outcome on a CUE 121 burst is the '
              'plain burst read from cue 121, and lands more than read from the cast',
              (burst_cue([gc_]), burst_cue([gc_], 150.0), burst_cue([gc_, g0_]), burst_cue([None, gc_]),
               shift_geometry(gc_, 10.0)['CUE'], ro_[0] == ro_[1], ro_[0]['value'] > ro_[2]['value'], ro_[3] == ro_[2]),
              (121.0, 150.0, 0.0, 121.0, 131.0, True, True, True), 'burst_cue, reaction_outcome, shift_geometry')
        # Version 23: a skill's timed press (`PRESS_CUE`, `skill_commit` 'cue') is read by every dodge
        # through `burst_cue`, as `skill_reaction` reads part 0's cue; a search launch after it is
        # read from the launch.
        gp_ = {**g0_, 'PRESS_CUE': 40.0}
        check('burst_cue: a burst every geometry of which carries a timed press is read from it, the later of '
              'a press and a search launch per geometry, the earliest geometry; shift_geometry moves the press; '
              'reaction_outcome on a PRESS_CUE 40 burst is the plain burst read from cue 40',
              (burst_cue([gp_]), burst_cue([gp_, g0_]), burst_cue([{**gc_, 'PRESS_CUE': 40.0}]),
               burst_cue([{**g0_, 'PRESS_CUE': 140.0}, {**gc_, 'PRESS_CUE': 140.0}]), burst_cue([gp_], 60.0),
               shift_geometry(gp_, 10.0)['PRESS_CUE'],
               reaction_outcome([gp_], [1.0], 2.5) == reaction_outcome([g0_], [1.0], 2.5, cue=40.0)),
              (40.0, 0.0, 121.0, 140.0, 60.0, 50.0, True), 'burst_cue, shift_geometry (version 23)')
        # Version 23: standing is an option wherever rolling is (`stand_or`). A first burst worth 10 he
        # can roll, then a lingering one worth 1000 that finds every roll after its i-frames: rolling
        # the first only meets the second, so he stands and takes the 10 (a defender who stands is
        # caught by the first and played out by its own worth here, `hit_value`).
        g1_ = {'T': np.arange(20.0, 23.0), 'X': np.zeros(3), 'Z': np.full(3, -2.5), 'R': np.full(3, 1.0),
               'PK': np.full(3, -math.inf)}
        g2_ = {'T': np.arange(40.0, 140.0), 'X': np.zeros(100), 'Z': np.full(100, -2.5), 'R': np.full(100, 12.0),
               'PK': np.full(100, -math.inf)}
        st_ = sequence_outcome([([g1_], [10.0]), ([g2_], [1000.0])], 2.5)
        st2_ = sequence_outcome([([g1_], [10.0]), ([g2_], [1.0])], 2.5)
        vo_ = volley_outcome([([g1_], ['a']), ([g2_], ['b'])], {'a': 10.0, 'b': 1000.0}, 2.5)
        check('stand_or: a roll that only meets a dearer burst is not taken, he stands and takes the first catch '
              '(value, punish, moves); worth 1 behind it he rolls; a volley whose standing take is every threat '
              'never stands for less; stand_or keeps a tie on the evasion',
              ((st_['value'], st_['p_punish'], st_['p_evade'], st_['moves']), st2_['moves'] is not None,
               st2_['value'] < 10.0, vo_['value'] - vo_['punish_hp'] <= vo_['base'],
               stand_or({'value': 5.0, 'punish_hp': 0.0, 'base': 5.0, 'moves': 'x'})['moves']),
              ((10.0, 0.0, 0.0, None), True, True, True, 'x'), 'stand_or, sequence_outcome, volley_outcome')
        # Version 23: the strafe reacts as a dodge does, reaction plus both network legs; version 28,
        # with a dodge's timing error on each (63 starts: 9 reactions x 7 errors, the move cancel at f5
        # under none of them). A walk started by about f11.6 leaves g1_ (f20-22) in time. Version 27 read
        # the 9 reactions alone (f8.45 to f13.31): the first 7 walked out, 49 of 63. With the errors
        # (-2.20 to +2.20 f) the reactions f10.09 to f11.44 lose 1, 1, 2 and 2 late presses and
        # f12.10 and f13.31 gain 3 and 1 early ones: 7 + 7 + 7 + 6 + 6 + 5 + 5 + 3 + 1 = 47.
        fm_ = FollowupModel([(g1_, 0)], [(g2_, 1)], 30.0, [0.0], None)
        rd_ = [p for r, w in reaction_delays() for p in pressed(r, 5.0, w)]
        sv_ = max(float(sum(w for (_, w), h in zip(rd_, _catches([g1_], 2.5, strafe_motion(side), [s for s, _ in rd_])[0])
                            if h < 0)) for side in (1.0, -1.0))
        check('strafe_escape: the walk starts at each reaction_delays quantile (reaction + 2 network legs, '
              f'median {reaction_delays()[REACTION_POINTS // 2][0]:.1f} f) plus each timing error (version 28), no '
              'sooner than the move cancel: 47 of 63 starts walk out',
              (round(fm_.strafe_escape(2.5, 5.0), 9), round(sv_ * 63, 9), len(rd_)), (round(47 / 63, 9), 47.0, 63),
              'FollowupModel.strafe_escape (version 23), pressed (version 28)')
        # Launch frames in 3D (bd er-effects-rs-7bye, round 3).
        hd = lambda fr: tuple(round(x, 6) + 0.0 for x in frame_heading(fr))  # noqa: E731
        up = look_frame(_unit(0.0, 89.0))
        check('shoot angles on a level frame add to its yaw and pitch; on a frame climbing 89 deg a yaw of 90 '
              'goes level and sideways (Meteorite of Astel 10470103 under the rising 10470101), not up',
              (hd(frame_shot(yaw_frame(20.0), 30.0, -15.0)), hd(frame_shot(up, 90.0, 0.0))),
              ((50.0, -15.0), (90.0, 0.0)), 'FUN_140390c20: mtx44mul(L, frame), L from .rdata 0x1432792b0')
        check('sfxPostureType 1 keeps the yaw and drops the pitch; a frame going straight down levels onto +z '
              '(FUN_14051a3d0 gives it up (0, 0, 1))',
              (hd(frame_level(frame_shot(yaw_frame(40.0), 0.0, 70.0))), hd(frame_level(look_frame((0.0, -1.0, 0.0)))),
               look_frame((0.0, -1.0, 0.0))[1]), ((40.0, 0.0), (0.0, 0.0), (0.0, 0.0, 1.0)),
              'FUN_14038fc20 (cmp [rdi+0xc0], 1 at 0x1403913bf); FUN_14051a520')
        aimed = frame_aim(yaw_frame(0.0), (0.0, 1.0, 0.0), (10.0, 1.0, 10.0), 30.0)
        check('the aim at creation turns by lockShootLimitAng at most (30 of the 45 deg to a point aside), '
              'a limit of 0 keeps the forward, and the rebuilt rows are cos(pitch) long',
              (hd(aimed), hd(frame_aim(yaw_frame(10.0), (0.0, 1.0, 0.0), (5.0, 3.0, 5.0), 0.0)),
               round(math.sqrt(_vdot(*(frame_aim(yaw_frame(0.0), (0.0, 0.0, 0.0), (0.0, 1.0, 1.0), 90.0)[0],) * 2)), 6)),
              ((30.0, 0.0), (10.0, 0.0), round(math.cos(math.radians(45.0)), 6)),
              'FUN_14038f120, XMM3 = lockShootLimitAng x deg->rad (0x140391096)')
        check('isOwnerOverrideInitAngle takes the owner frame; EmittePosType 2 levels the parent heading',
              (child_frame({'isOwnerOverrideInitAngle': 1, 'EmittePosType': 0}, up, yaw_frame(5.0)) == yaw_frame(5.0),
               hd(child_frame({'EmittePosType': 2}, frame_shot(yaw_frame(30.0), 0.0, 60.0), yaw_frame(0.0)))),
              (True, (30.0, 0.0)), 'FUN_14039bba0 (0x14039c697; the EmittePosType 2 branch)')
        ret = {**level, 'life': 0.5, 'FollowType': 5}
        fp, _, _, _ = bullet_flight(ret, (0.0, 1.0, 10.0), (0.0, 0.0), follow=lambda k: (0.0, 1.0, 0.0))
        check('an attached row follows its point a tenth of the way a frame at 60 fps (FollowType 5: 10 m '
              'out to 0.9^31 x 10 after its 31 updates); with no point it stays put',
              (round(fp[-1][2], 6), len(fp), bullet_flight(ret, (0.0, 1.0, 10.0), (0.0, 0.0))[0]),
              (round(10 * 0.9 ** 31, 6), 32, [(0.0, 1.0, 10.0)]),
              'FUN_14039afc0: k 0.2 (.data 0x143b15bbc) x dt / (1/30)')
        rest = {**level, 'initVellocity': 0.0, 'maxVellocity': 0.0, 'minVellocity': 0.0, 'hitRadius': 0.5}
        check('a bullet at rest on the ground meets the map on its first update only when asked (ground=True)',
              (bullet_flight(rest, (0.0, 0.0, 0.0), (0.0, 0.0), ground=True)[1],
               bullet_flight(rest, (0.0, 0.0, 0.0), (0.0, 0.0))[1]), ('hit', 'expire'),
              'map contact through the attack sweep, FUN_14051acc0 -> FUN_14038b610 (shape test INFERRED)')
        lk = lock_point(5.0)
        check(f'the lock-on point is c0000 DummyPoly 220 on the idle pose, {lk[1]:.3f} m up, 0.08 m in front '
              f'of a defender 5 m out facing the caster; at bearing 90 the same point turned',
              (1.2 < lk[1] < 1.3, abs(lk[2] - (5.0 - 0.08)) < 0.01,
               all(abs(a - b) < 1e-9 for a, b in zip(lock_point(5.0, 90.0), (lk[2], lk[1], -lk[0])))),
              (True, True, True), 'AddActPntSlot 0xdc-0xe4, FUN_14049ce50, FUN_140394910 kind 1; MEASURED er-hkx-pose')
        import numpy as np
        comet = bullet_tree(t, 10402000)
        still = fly_shots(t, comet, (0.0, 8.0))
        moving = fly_dynamic(t, comet, _standing((0.0, 8.0)), 0.0)
        check("fly_dynamic on a standing target is fly_shots aimed at him (Glintstone Cometshard's bolt)",
              len(still) == len(moving) and all(max(math.dist(a, b) for a, b in zip(s[1], m[1])) < 1e-9
                                                for s, m in zip(still, moving)), True, 'fly_members')

        def stepper(T, who):
            T = np.asarray(T, float)
            return np.where(T > 0.5, 3.0, 0.0), np.full(T.shape, 8.0), np.ones(T.shape, bool)
        side = fly_dynamic(t, comet, stepper, 0.0)[0][1]
        turn = math.degrees(math.atan2(side[-1][0] - side[-2][0], side[-1][2] - side[-2][2]))
        check(f"Glintstone Cometshard's bolt (25 deg/s, 1.13 s), aimed at a defender 8 m ahead who then "
              f"steps 3 m aside, turns after him: {turn:.1f} deg by its end, at most 25 x 1.13",
              5.0 < turn <= 25 * 1.13 + 1e-6, True, 'Bullet 10402000 homingAngle 25, FUN_140398270')
        # One flight model (round 4): the defenders a flight is flown against together are each
        # flown as alone. Carian Phalanx (attached blades), Rancorcall (homing skulls), Glintstone
        # Stars (homing stars that die on him), Cannon of Haima (a falling homing carrier) and Light
        # of Miquella (a carrier at 999 m/s): three defenders 3, 6 and 10 m out, against
        # `fly_shots` at each. Elden Stars (10672000) and its row 10672005 make interval children
        # whose number and draws depend on the flight's own length: until 2026-11-18 they were taken
        # from the longest flight of the call, and these two failed this check
        # (target/wf/interval/batchsolo.py, bs-trial.jsonl).
        alone = []
        for root in (10430100, 10500000, 10404000, 10408000, 210670000, 10672000, 10672005):
            fan = bullet_tree(t, root)
            if fan is None:
                alone.append(None)
                continue
            far3 = np.array([3.0, 6.0, 10.0])
            batch = {}
            for e in fly_members(t, fan, 3, 0.0, None, lambda T, who: (np.zeros(np.shape(T)), far3[who],
                                                                        np.ones(np.shape(T), bool))):
                for m, c in zip(e['who'], e['inv']):
                    batch[(int(m), e['key'])] = e['P'][:int(e['L'][c]), c]
            same = True
            for m, dd in enumerate(far3):
                one = fly_shots(t, fan, (0.0, float(dd)))
                keys = {k for (mm, k) in batch if mm == m}
                same = same and keys == set(one.keys) and all(
                    len(batch[(m, k)]) == len(e[1]) and float(np.abs(batch[(m, k)] - np.array(e[1])).max()) < 1e-9
                    for k, e in zip(one.keys, one))
            alone.append(same)
        check("a flight against several defenders at once is each one's flight alone, shot for shot "
              "(Carian Phalanx, Rancorcall, Glintstone Stars, Cannon of Haima, Light of Miquella, Elden "
              "Stars and its interval row 10672005: each flight's interval children its own)",
              alone, [True] * 7, 'fly_members: every member flies on its own; fly_shots is fly_members with one')
        # Interval children: none when the parent is made, then one an interval after the wait
        # while it has life left. Stars of Ruin chains twelve stationary 0.1 s timers at 0.05 s.
        sor_timers = [t.bullet[b]['intervalCreateBulletId'] for b in range(10422000, 10422012)]
        sor_flown = fly_shots(t, bullet_tree(t, 10422000))
        check("interval children (moves made before each): a 0.1 s timer at 0.05 s makes one, 2 in, not "
              "three from 0; a 0.12 s timer at 0.07 s one, 4 in; a 1 s bullet at 0.1 s nine; flown, Stars "
              "of Ruin's twelve timers drop one star each, the twelve its GoodsInfo text names (each fired, "
              "and flown, by fly_members against no one as by fly_shots)",
              (interval_frames(t.bullet[10422000]), interval_frames(t.bullet[10404000]),
               interval_frames({**t.bullet[10404000], 'life': 1.0, 'intervalCreateTimeMin': 0.1,
                                'intervalCreateTimeMax': 0.1}),
               sorted(n['bullet'] for n, *_ in sor_flown if n['bullet'] in sor_timers) == sorted(sor_timers),
               len(list(fly_members(t, bullet_tree(t, 10422000), 1, 0.0, None, None))) == len(sor_flown)),
              ([2], [4], [5, 11, 17, 23, 29, 35, 41, 47, 53], True, True),
              'FUN_14039e8a0 sets the timer to rand[min, max]; UpdateBullet 0x14039a100 takes dt off the '
              'life first; FUN_14039ab10; GoodsInfo 4220 "Fires twelve dark shooting stars"')
        # A row two parents name hangs under both (bd er-effects-rs-vkuf): Stars of Ruin's 40 m/s
        # legs 10422032-10422035 are each the HitBulletID of two stars' first legs. And a chain deeper than the
        # old cap of 32 is followed to its end: Wave of Gold's 4000 is 60 legs.
        def tree_count(root):
            got, todo = collections.Counter(), [root]
            while todo:
                node = todo.pop()
                if node:
                    got[node['bullet']] += 1
                    todo += list(node['children'].values())
            return got
        sor_tree = tree_count(bullet_tree(t, 10422000))
        sor_legs = collections.Counter(n['bullet'] for n, *_ in sor_flown if 10422030 <= n['bullet'] <= 10422037)
        ground = sorted(n['bullet'] for n, p, *_ in sor_flown if 10422015 <= n['bullet'] <= 10422026
                        and p[-1][1] <= t.bullet[n['bullet']]['hitRadius'] + 1e-6)
        check("a Bullet row two parents name hangs under both: Stars of Ruin's tree is 36 nodes, 10422032-10422035 "
              "twice (one seen set for the whole walk gave 32). Flown from the fixed placement, the three first "
              "legs that meet the ground make no second leg (their own launchConditionType 4 refuses a hit, "
              "bd er-effects-rs-rggx): 33 shots, 9 second legs. Wave of Gold's 59-link chain is all 60 legs "
              "(BULLET_DEPTH_CAP 32 kept 33)",
              (sum(sor_tree.values()), sorted(b for b, c in sor_tree.items() if c == 2), len(sor_flown),
               sum(sor_legs.values()), ground, sum(tree_count(bullet_tree(t, 4000)).values())),
              (36, [10422032, 10422033, 10422034, 10422035], 33, 9, [10422016, 10422018, 10422021], 60),
              'bullet_tree: the ancestor-path guard; HitBulletID of 10422017/19, 18/20, 21/23, 22/24 (REGULATION); '
              'launches')
        # Map contact and free aim (bd er-effects-rs-rw25): Great Oracular Bubble's bubble does no
        # damage, stops on the map and bursts into its 3 m child where it lands.
        gob = bullet_tree(t, 10511000)
        put = {g: floor_launch(t, gob, None, g) for g in (4.0, 8.0)}
        lands = {g: [(n['bullet'], round(math.hypot(p[-1][0], p[-1][2]), 2), round(p[-1][1], 2))
                     for n, p, _, _ in fly_tree(t, gob, launch=fl)] for g, fl in put.items() if fl}
        check("Great Oracular Bubble free-aimed at the ground 4 m / 8 m ahead: the bubble meets it there "
              "and its 3 m burst (10511001) is made at that point; level it never lands",
              (floor_bursts(t, gob, lambda n: n['bullet'] == 10511001), sorted(lands),
               all(abs(v[0][1] - g) <= 0.1 and v[1][0] == 10511001 and v[1][1:] == v[0][1:] for g, v in lands.items()),
               fly_tree(t, gob)[-1][1][-1][1] > 0.9),
              (True, [4.0, 8.0], True, True),
              'FUN_140398f90 -> FUN_14039dcc0 (FUN_1403aba90: isPenetrateMap 0) -> FUN_14039bba0 at the contact')
        # The frame rules on many bullets at once are the scalar helpers' (round 4).
        frs = [yaw_frame(20.0), look_frame(_unit(-35.0, 70.0)), frame_shot(look_frame(_unit(10.0, -89.0)), 30.0, 20.0),
               look_frame((0.0, -1.0, 0.0))]
        fa = np.array(frs)
        pos3, pt3 = np.array([(0.0, 1.0, 0.0)] * 4), np.array([(3.0, 2.0, 5.0)] * 4)

        def gap(b, s):
            return float(np.abs(np.asarray(b) - np.array(s, float)).max())
        check('the batched frame rules give the scalar ones: look, level, shoot angles, spread, aim',
              max(gap(_blook(fa[:, 2]), [look_frame(f[2]) for f in frs]), gap(_blevel(fa), [frame_level(f) for f in frs]),
                  gap(_bshot(fa, 25.0, -40.0), [frame_shot(f, 25.0, -40.0) for f in frs]),
                  gap(_bspread(fa, 7.0, -50.0), [frame_spread(f, 7.0, -50.0) for f in frs]),
                  gap(_baim(fa, pos3, pt3, 20.0), [frame_aim(f, (0.0, 1.0, 0.0), (3.0, 2.0, 5.0), 20.0) for f in frs]))
              < 1e-12, True, '_blook, _blevel, _bshot, _bspread, _baim')
        # The random spread (FUN_140390210) and its expected layout.
        sp = frame_spread(yaw_frame(0.0), 30.0, 40.0)[2]
        b40, a30 = math.radians(40.0), math.radians(30.0)
        rain = t.bullet[10421002]
        yaws = sorted(round(frame_heading(f)[0], 6) for _, f in shot_frames(rain, yaw_frame(0.0), (0.0, 1.0, 0.0)))
        check("the spread pitches the forward about the frame's right, then turns it about the pitched up: "
              "(sin b, -cos b sin a, cos b cos a); Founding Rain's 17 clouds (shootAngleYMaxRandom 360) are "
              "laid 360 / 17 deg apart around the full turn, and every variable's 17 draws are the 17 strata",
              (gap(sp, (math.sin(b40), -math.cos(b40) * math.sin(a30), math.cos(b40) * math.cos(a30))) < 1e-12,
               round(yaws[1] - yaws[0], 4), round(yaws[-1] - yaws[0], 4),
               all(sorted(draw_quantile(i, 17, v) for i in range(17)) == [(i + 0.5) / 17 for i in range(17)]
                   for v in range(4)), draw_quantile(0, 1, 2)),
              (True, round(360 / 17, 4), round(360 * 16 / 17, 4), True, 0.5),
              'FUN_140390210 (rows from .rdata 0x143279450); Bullet 10421002 numShoot 17; draw_quantile')
        # Two traced rules no player bullet reaches (round 4, the comments above AIM_EMIT_TYPES and in
        # "Launch frames"): predictionShootObserveTime's lead, and a character hit's normal under a
        # sfxPostureType 2 child whose frame would move a flight.
        rfiles = PR.load(t.regulation)
        brows = {r['id']: r for r in PR.rows(PR.param_bytes(rfiles, 'Bullet'), [
            'predictionShootObserveTime', 'HitBulletID', 'intervalCreateBulletId', 'sfxPostureType', 'isPenetrateChr',
            'initVellocity', 'maxVellocity', 'accelInRange', 'shootAngle', 'shootAngleXZ', 'shootAngleInterval',
            'shootAngleXInterval', 'isOwnerOverrideInitAngle', 'EmittePosType'])[0]}
        proots = {r['refId'] for r in t.reg.behavior.values() if r.get('refType') == 1}
        proots |= {g['refId_default'] for g in PR.rows(PR.param_bytes(rfiles, 'EquipParamGoods'), ['refId_default'])[0]}
        for mg in PR.rows(PR.param_bytes(rfiles, 'Magic'), None)[0]:
            proots |= {mg[f'refId{i}'] for i in range(1, 11) if mg.get(f'refCategory{i}') == 1}
        preach, hpar = set(), collections.defaultdict(set)
        todo = list(proots)
        while todo:
            b = todo.pop()
            if b <= 0 or b in preach or b not in brows:
                continue
            preach.add(b)
            for c in (brows[b]['HitBulletID'], brows[b]['intervalCreateBulletId']):
                todo.append(c)
            if brows[b]['HitBulletID'] > 0:
                hpar[brows[b]['HitBulletID']].add(b)

        def moves_frame(b, seen):
            if b <= 0 or b in seen or b not in brows:
                return False
            seen.add(b)
            r = brows[b]
            own = not (r['isOwnerOverrideInitAngle'] or r['EmittePosType'] in (1, 2)) and any(
                r[k] for k in ('initVellocity', 'maxVellocity', 'accelInRange', 'shootAngle', 'shootAngleXZ',
                               'shootAngleInterval', 'shootAngleXInterval'))
            return own or moves_frame(r['HitBulletID'], seen) or moves_frame(r['intervalCreateBulletId'], seen)
        posture = [b for b in preach if brows[b]['sfxPostureType'] == 2 and b in hpar
                   and any(not brows[p]['isPenetrateChr'] for p in hpar[b])]
        check('no bullet a player spell, skill or goods reaches leads its aim (predictionShootObserveTime 0 on '
              'all of them, set on 21 rows in all), and none of the sfxPostureType 2 children a character hit '
              'can make has a moving or angled bullet at or under it',
              (sum(1 for r in brows.values() if r['predictionShootObserveTime']),
               sorted(b for b in preach if brows[b]['predictionShootObserveTime']), len(posture),
               [b for b in posture if moves_frame(b, set())]),
              (21, [], 398, []), 'REGULATION scan; FUN_140390c20 0x140390f32, FUN_1403adff0; FUN_14039b500')
        lsl = skill_profile(t, t.find_arts('Lightning Slash'), claymore)
        lb = next(x['bullet'] for x in lsl['anims'][main_anim(lsl)] if x['kind'] == 'bullet')
        check("Lightning Slash's 200 m/s bolt drops, it does not fly 30 m: reach under 4 m",
              bullet_reach(t, lb)[0] < 4.0, True, 'Bullet 2091 shootAngleXZ -90; child on its ending')
        stomp = skill_profile(t, t.find_arts('Divine Beast Frost Stomp'), claymore)
        sb = next(x['bullet'] for x in stomp['anims'][main_anim(stomp)] if x['kind'] == 'bullet')
        check('Divine Beast Frost Stomp hops a few metres per landing: 9-14 m, not 160',
              9.0 < bullet_reach(t, sb)[0] < 14.0, True, 'EmittePosType 2 children, launchConditionType 5')
        lf = skill_reach_factors(t, claymore, lion, {'hit_rows': hits})
        check("Lion's Claw on a Claymore: world reach from its own TimeAct, not a projectile",
              (lf['reach_source'], lf['projectile'], round(lf['reach'], 2)), ('world', False, 6.13),
              'er-mechanics-reach.skill_reach, a600 040000 + hkx')
        hf = skill_reach_factors(t, claymore, hoar, {'hit_rows': skill_hits(t, claymore, hoar, ctx, 25)})
        check('Hoarfrost Stomp is a projectile with the bullet model reach',
              (hf['projectile'], hf['reach_source'], hf['reach'] > 5.0), (True, 'bullet', True),
              'bullet_reach (INFERRED)')
        wsc = skill_commit(t, claymore, wild, pw, skill_hits(t, claymore, wild, ctx, 25))
        check(f"Wild Strikes: the 040050 wind-up is a lead-in before the looping 040051 ({wsc['lead']} frames)",
              wsc['lead'] > 0, True, 'TAE a610 040050 (no hit, no FP) + section 13 lead-in rule')
        gs = skill_commit(t, claymore, t.find_arts('Ground Slam'), skill_profile(t, t.find_arts('Ground Slam'), claymore),
                          skill_hits(t, claymore, t.find_arts('Ground Slam'), ctx, 25))
        check(f"Ground Slam: the leap 040000 hands over when its falling hitbox 500 opens (f49), "
              f"lead {gs['lead']}", 40 < gs['lead'] < 60, True, 'TAE a715 040000 body hitbox 500 f49-135 (INFERRED landing)')
        both = skill_term(t, claymore, [(storm, 1.0)], ctx, 25, 0.0, available=[storm, lion],
                          damage_fn=lambda a, p, f: 300.0 * len(a) / 5)
        check('best available: the corpus mix is kept apart and never beats the best option',
              (both['value'] >= both['value_corpus'], both['best']['name'] in ("Lion's Claw", 'Storm Stomp'),
               round(both['score_corpus'] - SKILL_WEIGHT * both['value_corpus'], 6)),
              (True, True, 0.0), 'section 15')
        # Per-hit landing (section 15).
        check('knockback: v for ContTime, then linear to 0 over DecTime, totals the AtkParam distance',
              (round(knockback_offset(0.6, 0.09, 1.2, 5.0), 9), round(knockback_offset(0.6, 0.09, 1.2, 0.09), 4),
               knockback_offset(0.6, 0.0, 1.2, 1.0)),
              (0.6, round(0.6 * 0.09 / (0.6 + 0.09), 4), 0.0), 'FUN_140450b20 / FUN_140450f00')
        check('every protector row: knockBack 0 (no resist), knockbackParamId 1',
              set(t.protector_knockback.values()), {(KNOCKBACK_RESIST, KNOCKBACK_PLAYER_ROW)},
              'EquipParamProtector; FUN_14068add0 / FUN_1404519d0')
        kb = t.knockback[KNOCKBACK_PLAYER_ROW]
        check('KnockBackParam 1 large: ContTime 0.09 s, DecTime 1.2 s',
              (round(kb['damage_L_ContTime'], 3), round(kb['damage_L_DecTime'], 3)), (0.09, 1.2), 'regulation')
        caller = t.find_arts('Stormcaller')
        storm_hits = skill_hits(t, claymore, caller, ctx, 25)
        for h in storm_hits:
            h['pvp_damage_total'] = 100.0
        broke = skill_landing(t, claymore, caller, storm_hits, [72.0], distances=(ENGAGE_DISTANCE_M,))
        held = skill_landing(t, claymore, caller, storm_hits, None, distances=(ENGAGE_DISTANCE_M,))
        # Each swing (priority 0) and its bullet (priority 1) take one a623_040000 event 341 record
        # (f24-29, f47-52, f72-76): the swing reaches him first or on the same update, so its
        # bullet lands nothing (bd er-effects-rs-dnok). Both bullets landed before (1.0 at
        # positions 1 and 3, measured 2026-11-10).
        check('Stormcaller at 2.5 m, poise 72: swing 1 reaches him first (one large stagger, roll at '
              '35) and its bullet, in its record, lands nothing; swing 2 lands 18.7 frames on and '
              'restarts it at DamageCount 2 (roll at 15), its bullet again nothing, swing 3 20 frames '
              'later is escaped',
              broke['hit_share'], [1.0, 0.0, 1.0, 0.0, 0.0, 0.0],
              'frame-advantage.reaction large 35/15/5/0, TAE a623 040000 (341 records) + hkx, bullet flight')
        check('Stormcaller with poise that holds: large -> 0 (SpEffect 6352), no lock; only the '
              'first swing lands (its bullet shares its record)',
              held['hit_share'], [1.0, 0.0, 0.0, 0.0, 0.0, 0.0],
              'frame-advantage.md section 1; ExecAddDamage leaves the state; TAE a623 040000 341')
        root = _clip_root_back(7)
        check('damage clips: small blow carries its target 4 m+ back, large none',
              (root is not None and root(2.0) > 4.0, _clip_root_back(3) is None), (True, True),
              'a000_005400 / 005300 root motion (MEASURED)')
        # The Glintblade Phalanx ash's blades are read from their search launch (`tree_cue`): its
        # bullet hits' geometries carry `CUE`, 121 / 60 s after their event.
        gp_sid = t.find_arts('Glintblade Phalanx')
        gp_hits = [h for h in skill_hits(t, claymore, gp_sid, ctx, 25) if h['kind'] == 'bullet']
        gp_con = _hit_contacts(t, claymore, gp_sid, gp_hits)
        check('the Glintblade Phalanx ash\'s bullet hits are read from the search launch: CUE = event + 121 / 60 s',
              (bool(gp_con), all(c[3] is not None and abs(c[3]['CUE'] - (c[0] + 121 / BULLET_FPS) * TAE_FPS) < 1e-6
                                 for c in gp_con)),
              (True, True), '_hit_contacts, tree_cue')
        bolt = skill_profile(t, t.find_arts('Thunderbolt'), claymore)
        bolt_hits = skill_hits(t, claymore, t.find_arts('Thunderbolt'), ctx, 25)
        con = _hit_contacts(t, claymore, t.find_arts('Thunderbolt'), bolt_hits[:1])
        # The bolt leaves DummyPoly 1 on the raised right hand, whose own height (2.0 m) the launch
        # keeps aside (`bullet_launch`: the shot's pitch is not decoded, bd er-effects-rs-doax).
        h0 = bolt_hits[0]
        launch = skill_bullet_launch(t, claymore, t.find_arts('Thunderbolt'), h0['anim'], h0['frame'], h0['root_bullet'])
        path = fly_tree(t, bullet_tree(t, h0['root_bullet']), launch=launch)[0][1]
        check("Thunderbolt's 200 m/s bolt (3.3 m a frame) is found on a defender 2.5 m ahead; it leaves DummyPoly 1 "
              "on the raised hand (2.0 m up, kept aside)",
              (con[0][2], con[0][1](lambda _w: ENGAGE_DISTANCE_M) is not None, launch['src'],
               round(launch['dummy_y'], 1), round(math.dist(path[0], path[1]), 2)),
              ('flight', True, 'dummy', 2.0, 3.33),
              f"Bullet 2080 sampled every {CONTACT_STEP_M} m ({main_anim(bolt)}); TAE event 2 dummy 1; c0000 dummy 1 "
              "on R_Hand")
        lsl_tree = fly_tree(t, lb)
        check("Lightning Slash: the falling bolt stops on the ground, so its blast starts there",
              round(lsl_tree[2][1][0][1], 3), round(t.bullet[lsl_tree[1][0]['bullet']]['hitRadius'], 3),
              'Bullet 2091 -> 2092 (HitBulletID at the contact point, FUN_14039bbb0)')
        # Section 16: reaction, follow-ups, buffs as options.
        delays = reaction_delays()
        check('reaction delay: median (0.25 + 2 x 0.05) s = 10.5 real frames',
              round(delays[REACTION_POINTS // 2][0], 3), 10.5, 'REACTION_MEDIAN_S, NETWORK_ONE_WAY_S (INFERRED)')
        import numpy as np
        ok = np.zeros(200, bool)
        ok[60:81] = True                          # dodge starts f30-40 escape
        presses = dodge_presses(ok, 0.0)
        # The run's first start is f30, read as `round(s / 0.5)`, so a press escapes from f29.75 on;
        # the earliest timing error is -1.4652 sd x 1.5 = -2.198 frames.
        e_lo = -_timing_errors(DODGE_TIMING_SD_S * TAE_FPS)[0]
        check('a run the defender reaches in time: he aims as early as every press of his timing error still '
              'escapes (f29.75 + 2.198 = f31.948; version 25: until then at its middle, f35, which no gain '
              'of his asks for)',
              (round(sum(s * w for s, w in presses), 3), round(sum(w for _, w in presses), 6),
               round(_escape(ok, presses), 6)), (round(29.75 + e_lo + EDGE_EPS, 3), 1.0, 1.0),
              'dodge_presses, _AimTable')
        late = dodge_presses(ok, 35.0)
        check('a run that closes before he can react: he dodges at the reaction, too late',
              all(s > 40.0 for s, _ in late), True, 'dodge_presses')
        # What is on screen: attacks he cannot tell apart (`group_presses`, `lookalike_plan`).
        a_, b_, c_ = np.zeros(200, bool), np.zeros(200, bool), np.zeros(200, bool)
        a_[60:81] = True                          # attack a: starts f30-40 escape
        b_[120:141] = True                        # attack b: f60-70
        c_[72:97] = True                          # attack c: f36-48, overlapping a's f36-40
        check('one attack: group_presses is dodge_presses, exactly',
              group_presses([ok], [1.0], 0.0, floor=12.0) == dodge_presses(ok, 0.0, floor=12.0), True,
              'group_presses')
        check('two attacks whose runs do not meet, alike to him: one timing, the first run on a tie, the '
              'heavier one when weighted (aims f31.948, f61.948: each run entered with every press)',
              tuple(round(sum(s * w for s, w in group_presses([a_, b_], ws, 0.0)), 3) for ws in ([1, 1], [1, 3])),
              (round(29.75 + e_lo + EDGE_EPS, 3), round(59.75 + e_lo + EDGE_EPS, 3)), 'group_presses')
        a_c = group_presses([a_, c_], [1, 1], 0.0)
        check('two attacks whose runs overlap: he aims where every press escapes both (f35.75 + 2.198, inside '
              'the starts f36-40 that escape both)',
              (round(sum(s * w for s, w in a_c), 3), round(_escape(a_, a_c), 6), round(_escape(c_, a_c), 6)),
              (round(35.75 + e_lo + EDGE_EPS, 3), 1.0, 1.0), 'group_presses: stretch f36-40')
        # Version 25: he aims for his punish too. The pool's strikes land f10 / f15 / f20 after his
        # R1 cancel, which a roll reaches 20 frames in, against an attacker who can roll at f60: a
        # press at s punishes with every strike that lands before f60, (60 - s) - 20 > strike. A run
        # f20-23, too short for every press of his timing error, and a run f30-50 he can enter
        # whole: escaping alone takes the long run (escape 1.0, no strike in time from it); aiming
        # for his punish he takes the short one, 6 presses of 7 escaping with two strikes of three
        # in time, worth 1.48 hits to him against 1.05.
        pgain = PressGain(np.array([10.0, 15.0, 20.0]), 0.0, 60.0, 20.0, True)
        okg = np.zeros(200, bool)
        okg[40:47] = True                          # f20-23
        okg[60:101] = True                         # f30-50
        plain_ = dodge_presses(okg, 0.0)
        aimed_ = dodge_presses(okg, 0.0, gain=pgain)

        def worth_(ps):
            return sum(w * (1.0 + float(pgain(np.array([s]))[0])) for s, w in ps
                       if 0 <= int(round(s / DODGE_STEP)) < len(okg) and okg[int(round(s / DODGE_STEP))])
        # Every aim tried, by brute force on a 0.01-frame grid from the reaction: none is worth more to
        # him than the one taken (`_AimTable`'s claim that only the run entries need trying).
        brute_ = True
        for delay_, _ in reaction_delays():
            tab_ = _AimTable([okg], [1.0], DODGE_STEP, [pgain])
            got_ = tab_.presses(delay_, 1.0)
            # Version 28: he means to press no sooner than his reaction, and each press is that
            # plus his error (`pressed`).
            for a_try in np.arange(delay_, 60.0, 0.01):
                if worth_(pressed(a_try, None, 1.0)) > worth_(got_) + 1e-9:
                    brute_ = False
        check('aiming for his punish: against runs f20-23 and f30-50 and strikes that punish a press before f30, '
              'escaping alone takes the long run and aiming for the punish the short one, worth more to him; no '
              'aim on a 0.01-frame grid from each reaction is worth more to him than the one taken',
              (round(_escape(okg, plain_), 4), round(worth_(plain_), 4), round(_escape(okg, aimed_), 4),
               round(worth_(aimed_), 4), brute_), (1.0, 1.0476, 0.8571, 1.4762, True), '_AimTable, PressGain')
        # A table's worths at a floor (`_floor_worths`): each aim with every press past the floor
        # read off the worths at no floor, the others at the floor, against `_worth` at the floor of
        # every aim at once, bit for bit; a group of members with gains of their own roll frames, a
        # member with none, and floors under, inside and past the candidates.
        okf_ = [okg, np.roll(okg, 9), np.roll(okg, -13)]
        gf_ = [PressGain(np.array([10.0, 15.0, 20.0]), 0.0, 60.0 + 3 * j_, 20.0, True) for j_ in range(2)] + [None]
        fl_n = fl_bad = fl_free = fl_moved = 0
        for floor_ in (-5.0, 8.0, 19.3, 31.0, 47.75, 120.0):
            tabf_ = _AimTable(okf_, [1.0, 0.5, 2.0], DODGE_STEP, gf_)
            vf_, _ = tabf_._cands()
            for j0_ in (0, len(vf_) // 3):
                # The no-floor worths read first from a later candidate, so `_none_worths` extends them.
                ws_ = {}
                tabf_._none_worths(vf_, j0_ + 5, ws_)
                got_ = tabf_._floor_worths(vf_, j0_, len(vf_), floor_, ws_)
                want_ = tabf_._worth(vf_[j0_:], floor_)
                moved_ = ~(vf_[j0_:, None] + tabf_.errors[None, :] > floor_).all(axis=1)
                fl_n += 1
                fl_free += int((~moved_).sum())
                fl_moved += int(moved_.sum())
                fl_bad += int(np.asarray(got_, float).tobytes() != np.asarray(want_, float).tobytes())
        check('a table\'s worths at a floor read off its worths at no floor where no press of an aim meets the '
              'floor (_floor_worths) are _worth at the floor of every aim, bit for bit',
              (fl_n, fl_free > 0, fl_moved > 0, fl_bad), (12, True, True, 0),
              '_AimTable._floor_worths, _none_worths')
        # And the aims themselves: each reaction's aim read by `_aims` (the worths at the candidates
        # and at the reactions kept per floor, shared by every table of one content), from a table
        # that reads them first and from a second of the same content that finds them kept, is the
        # aim of every candidate and the reaction read in one `_worth` batch.

        def aim_ref(tab, t_, floor_):
            v_, th_ = tab._cands()
            j_ = int(v_.searchsorted(t_, 'right'))
            sel_ = th_[j_:] > t_ + tab.errors[0]
            if not sel_.any():
                return None
            A_ = np.concatenate([[t_], v_[j_:][sel_]])
            U_ = tab._worth(A_, floor_)
            top_ = float(U_.max())
            b_ = int(np.argmax(U_ >= top_ - 1e-12 * max(1.0, abs(top_))))
            return None if b_ == 0 else float(A_[b_])
        forget_aim_worths()
        aim_n = aim_bad = aim_set = 0
        for floor_ in (None, -5.0, 8.0, 19.3, 31.0, 47.75):
            for rep_ in range(2):
                tabs_ = _AimTable(okf_, [1.0, 0.5, 2.0], DODGE_STEP, gf_)
                ts_ = [float(x) for x in np.arange(-3.0, 60.0, 1.7)]
                aims_ = tabs_._aims(ts_[::-1] if rep_ else ts_, floor_)
                aims_ = aims_[::-1] if rep_ else aims_
                for tr_, ar_ in zip(ts_, aims_):
                    aim_n += 1
                    aim_set += ar_ is not None
                    aim_bad += int(ar_ != aim_ref(tabs_, tr_, floor_))
        check('a table\'s aims read with the worths kept per floor and reaction (_aims, shared by every table of one '
              'content) are those of reading every candidate and the reaction in one batch, read first and read again',
              (aim_n, aim_set > 0, aim_bad), (6 * 2 * 38, True, 0), '_AimTable._aims, _floor_worths, _AIM_WORTHS')
        # Version 28: every press carries his timing error, one he means at his reaction included. The
        # 7 errors are 1.5 frames x the normal quantiles at (k + 0.5) / 7: +-2.198, +-1.187, +-0.549, 0.
        check('v28 pressed: a press meant at f20 lands at f17.802, 18.813, 19.451, 20, 20.549, 21.187, 22.198, '
              'a seventh each; a floor at f19 puts the first two there (2/7); a floor at f23 all of them',
              ([round(s, 3) for s, _ in pressed(20.0, None, 1.0)],
               [(round(s, 3), round(w * 7, 9)) for s, w in pressed(20.0, 19.0, 1.0)], pressed(20.0, 23.0, 1.0)),
              ([17.802, 18.813, 19.451, 20.0, 20.549, 21.187, 22.198],
               [(19.0, 2.0), (19.451, 1.0), (20.0, 1.0), (20.549, 1.0), (21.187, 1.0), (22.198, 1.0)], [(23.0, 1.0)]),
              'pressed, press_errors (DODGE_TIMING_SD_S, TIMING_POINTS)')
        # A run of 3 grid starts, f30-31 (a press escapes from f29.75 to f31.25, the grid's rounding):
        # at most 3 of his 7 errors fit in it (-0.549, 0, +0.549 span 1.098 frames; any 4 span 1.736).
        # Knowing the attack from the cue (his reactions f8.45-13.31) he aims each reaction at it: 3/7
        # each, 27/63. Reacting from f20.1 instead (a split: the earliest he can mean to press is
        # f28.55, 29.28, 29.77, 30.19, 30.60, 31.04, 31.54, 32.20, 33.41) the last three can no
        # longer aim all three in: 3 x 6 + 2 + 2 + 1 = 23 of 63. Version 27 clamped the errors at the
        # reaction, so the four reactions f29.77-31.04 inside the run pressed there exactly and
        # escaped whole: 3 + 3 + 4 x 7 = 34 of 63, more than knowing it from the cue.
        okn_ = np.zeros(200, bool)
        okn_[60:63] = True
        tabn_ = _aim_table(okn_, DODGE_STEP)
        per_n = [round(_escape(okn_, tabn_.presses(20.1 + d, 1.0)) * 7, 9) for d, _ in reaction_delays()]
        later_ = [round(_escape(okn_, dodge_presses(okn_, f_)), 9) for f_ in np.arange(0.0, 30.0, 0.1)]
        laterg_ = [round(worth_(dodge_presses(okg, f_, gain=pgain)), 9) for f_ in np.arange(0.0, 30.0, 0.1)]
        check('v28: a defender who reacts to a later event never does better than one who knows the attack from '
              'its cue: a 1.5-frame run escaped 27/63 from the cue and 23/63 from f20.1 (version 27: 34/63, the '
              'reactions inside the run pressing with no error), and from every decision time f0-29.9 (0.1 apart) '
              'neither the escape nor, aiming for his punish, his worth is above the cue\'s',
              (round(_escape(okn_, dodge_presses(okn_, 0.0)), 6), round(_escape(okn_, dodge_presses(okn_, 20.1)), 6),
               per_n, max(later_) <= later_[0] + 1e-9, max(laterg_) <= laterg_[0] + 1e-9),
              (round(27 / 63, 6), round(23 / 63, 6), [3.0] * 6 + [2.0, 2.0, 1.0], True, True),
              '_AimTable.presses, pressed (version 28)')
        early = lookalike_plan([[a_], [b_]], [1, 1], [[math.inf, 10.0], [10.0, math.inf]], 0)
        check('a split at f10, before either run: he waits for it and reacts as dodge_presses from f10 '
              '(escape 1.0 against 0.5 committing)',
              (early['at'], early['presses'] == [dodge_presses(a_, 10.0)], round(early['escape'][10.0], 6),
               round(early['escape'][0.0], 6)), (10.0, True, 1.0, 0.5), 'lookalike_plan')
        late_ = lookalike_plan([[a_], [c_]], [1, 1], [[math.inf, 45.0], [45.0, math.inf]], 1)
        check('a split at f45, after the overlap: he commits at the cue to the timing that escapes both '
              '(waiting escapes neither)',
              (late_['at'], late_['presses'] == [a_c], round(late_['escape'][0.0], 6),
               round(late_['escape'][45.0], 6)), (0.0, True, 1.0, 0.0), 'lookalike_plan')
        lone = lookalike_plan([[ok, a_]], [1.0], [[math.inf]], 0, floor=12.0)
        check('one member and no split: the plan is dodge_presses from the cue, per move',
              lone['presses'] == [dodge_presses(ok, 0.0, floor=12.0), dodge_presses(a_, 0.0, floor=12.0)], True,
              'lookalike_plan')
        d_ = np.zeros(200, bool)
        d_[160:181] = True                        # a hold: f80-90
        rs = [[math.inf, 10.0, 10.0], [10.0, math.inf, 45.0], [10.0, 45.0, math.inf]]
        blind = lookalike_plan([[a_], [b_], [d_]], [0.5, 0.5, 1.0], rs, 2)
        seen_ = lookalike_plan([[a_], [b_], [d_]], [0.5, 0.5, 1.0], rs, 2, events=[10.0, 45.0, None])
        check('a release at f10 (escapes f30-40), one at f45 (f60-70), the hold (f80-90): decided at f45 he used to '
              'react to the first release that late and be caught (escape 0.75 either way); seeing it as it comes '
              'he waits for f45 and escapes all three',
              (round(blind['escape'][45.0], 6), round(blind['escape'][10.0], 6), seen_['at'],
               round(seen_['escape'][45.0], 6), round(seen_['escape'][10.0], 6)), (0.75, 0.75, 45.0, 1.0, 0.75),
              'lookalike_plan events')

        def plans_(cap):
            # Every member, cue and floor of a three-member group, one memo per events, with the
            # memos allowed `cap` entries.
            old_ = LOOKALIKE_MEMO_ENTRIES
            globals()['LOOKALIKE_MEMO_ENTRIES'] = cap
            forget_look_memos()
            try:
                out_ = []
                for ev_ in (None, [10.0, 45.0, None]):
                    memo_ = {}
                    out_ += [lookalike_plan([[a_, b_], [b_, c_], [d_, a_]], [0.5, 0.5, 1.0], rs, me_, cue_, fl_,
                                            memo=memo_, events=ev_)
                             for me_ in range(3) for cue_ in (0.0, 5.0) for fl_ in (0.0, 12.0, 30.0, 50.0)]
                return out_, _LOOK_HELD[0]
            finally:
                globals()['LOOKALIKE_MEMO_ENTRIES'] = old_
                forget_look_memos()
        kept_, held_ = plans_(10 ** 9)
        emptied_, _ = plans_(0)
        check('the lookalike plans\' memos emptied before every call give every plan a kept memo gives, and '
              'count what they gain', (emptied_ == kept_, held_ > 100, len(kept_)), (True, True, 48),
              'LOOKALIKE_MEMO_ENTRIES')
        # Version 28: knowing is never worse. Every plan above, and the same group with gains (a punish
        # counted on some members' moves), against the defender who knows the member he faces from
        # the cue (`dodge_presses` on its own starts, cue, floor and gain): his worth by the plan's own
        # measure (per press 1 + its gain where it escapes, averaged over the moves) is never less.
        # Version 27's clamp broke it. The second group pairs the toy's 1.5-frame run with a run at
        # f60-70 the same timing cannot meet, split at f20.1: the plan waits for the split and reacts
        # to it, which the clamp made worth 34/63 against the short run where knowing it gave 27/63
        # (`MEASURED` 2026-11-16 with the clamp put back in the process: 32 of the second group's 128
        # member moves worse off knowing, 0.4286 against 0.5397; none of the first group's 192).
        groups_ = [([[a_, b_], [b_, c_], [d_, a_]], [0.5, 0.5, 1.0], rs, [[pgain, None], [None, pgain], [pgain, pgain]],
                    [10.0, 45.0, None]),
                   ([[okn_, okn_], [b_, b_]], [1.0, 1.0], [[math.inf, 20.1], [20.1, math.inf]],
                    [[None, pgain], [pgain, None]], [None, 20.1])]

        def wpl_(ok_, ps_, g_):
            return sum(w * (1.0 + (float(g_(np.array([s]))[0]) if g_ is not None else 0.0)) for s, w in ps_
                       if 0 <= int(round(s / DODGE_STEP)) < len(ok_) and ok_[int(round(s / DODGE_STEP))])
        worse_, n_ = [], 0
        for gi_, (grp_, gw_, gs_, gns_, gev_) in enumerate(groups_):
            for gains_ in (None, gns_):
                for ev_ in (None, gev_):
                    for me_ in range(len(grp_)):
                        for cue_ in (0.0, 5.0):
                            for fl_ in (0.0, 12.0, 30.0, 50.0):
                                p_ = lookalike_plan(grp_, gw_, gs_, me_, cue_, fl_, events=ev_, gains=gains_)
                                for k_ in range(2):
                                    g_ = None if gains_ is None else gains_[me_][k_]
                                    ok_ = grp_[me_][k_]
                                    know_ = wpl_(ok_, dodge_presses(ok_, cue_, floor=fl_, gain=g_), g_)
                                    plan_ = wpl_(ok_, p_['presses'][k_], g_)
                                    n_ += 1
                                    if know_ < plan_ - 1e-9:
                                        worse_.append((gi_, gains_ is not None, ev_ is not None, me_, cue_, fl_, k_,
                                                       round(know_, 4), round(plan_, 4)))
        check('v28: in every plan of a three-member group and of a two-member group split at f20.1 (with and '
              'without seen releases, and with gains), the defender who knows the member he faces is never worse '
              'off by the plan\'s own measure than the plan (320 member moves; the cases where he would be)',
              (n_, worse_), (320, []), 'lookalike_plan, dodge_presses, pressed (version 28)')
        check('release_frames: every real frame of the window, the end left out, weighed alike (13 for f13-26, '
              '81 for Gelmir\'s Fury\'s f34.3-115); one frame, or no window, the first alone',
              ([round(r, 6) for r, _ in release_frames(13.0, 26.0)][:3], len(release_frames(13.0, 26.0)),
               round(sum(w for _, w in release_frames(13.0, 26.0)), 9), len(release_frames(34.266, 114.965)),
               release_frames(64.0, 65.0), release_frames(10.0, None)),
              ([13.0, 14.0, 15.0], 13, 1.0, 81, [(64.0, 1.0)], [(10.0, 1.0)]),
              'release_frames (INFERRED uniform release)')
        # `release_read` on a net that holds, falls over five frames, then holds: read at the ends and
        # every RELEASE_STEP, and frame by frame where it falls; filled in exactly where it holds.
        stepf = [366.0] * 12 + [300.0, 200.0, 130.0, 20.0, -8.0] + [-10.0 - k for k in range(5)]
        rgot = release_read(len(stepf), lambda i: stepf[i], lambda v: v)
        rfill = [(1 - s) * stepf[a] + s * stepf[b] for a, b, s in release_fill(rgot, len(stepf))]
        every_ = release_read(len(stepf), lambda i: stepf[i], lambda v: v, step=1, tol=0.0)
        check(f'release_read reads the ends, every {RELEASE_STEP} frames and the turn frame by frame ({sorted(rgot)}), '
              f'and the mean of the filled-in releases is within {RELEASE_TOL:g} HP of every frame read '
              f'({sum(rfill) / len(stepf):.2f} against {sum(stepf) / len(stepf):.2f}); step 1, tolerance 0 reads every '
              'one; one release is read alone',
              ({0, 5, 10, 15, 20, 21} <= set(rgot) and {12, 13, 14} <= set(rgot),
               abs(sum(rfill) - sum(stepf)) / len(stepf) <= RELEASE_TOL, sorted(every_) == list(range(len(stepf))),
               release_read(1, lambda i: 7.0, lambda v: v), release_fill({0: 7.0}, 1)),
              (True, True, True, {0: 7.0}, [(0, 0, 0.0)]), 'release_read, release_fill')
        # `release_nodes` and `node_weights`: 13 frames read at their ends and every RELEASE_STEP, the
        # frames between shared out along the straight line.
        nd13 = release_nodes(13)
        nw13 = {k: round(v, 9) for k, v in node_weights(nd13, {i: 1.0 for i in range(13)}).items()}
        check(f'release_nodes reads 13 frames at {nd13}, the frames between weighing on the two nodes around '
              f'them ({nw13}); one frame is one node',
              (nd13, nw13, release_nodes(1)),
              ([0, 5, 10, 12], {0: 3.0, 5: 5.0, 10: 3.5, 12: 1.5}, [0]), 'release_nodes, node_weights')
        # `release_fill_in` on a first frame far off the others (Rock Blaster's): its stretch read
        # frame by frame in one round, which a second round leaves as it is.
        rbo = release_fill_in(nd13, [705.0, 96.7, 96.7, 96.7], 13)
        check(f'release_fill_in reads every frame of a stretch whose two nodes differ by more than '
              f'{RELEASE_SPLIT_HP:g} HP over its share, in one round ({rbo}); a second round on it, and nodes '
              'that agree, keep the same list',
              (rbo, release_fill_in(rbo, [705.0] + [96.7] * 7, 13) is rbo,
               release_fill_in(nd13, [96.7] * 4, 13) is nd13),
              ([0, 1, 2, 3, 4, 5, 10, 12], True, True), 'release_fill_in')
        every = {'T': np.arange(30.0, 70.0, 0.5), 'X': np.zeros(80), 'Z': np.full(80, -99.0),
                 'R': np.full(80, 1e3), 'PK': np.full(80, -math.inf)}
        brief = {'T': np.array([30.0, 30.5, 31.0]), 'X': np.zeros(3), 'Z': np.full(3, -99.0),
                 'R': np.full(3, 1e3), 'PK': np.full(3, -math.inf)}
        check('a live window longer than the roll\'s i-frames cannot be rolled; a 1-frame one at f30 can',
              (round(reaction_outcome([every], [1.0], 2.5)['p_evade'], 3),
               round(reaction_outcome([brief], [1.0], 2.5)['p_evade'], 3)), (0.0, 1.0),
              'reaction_outcome + _catches (i-frames 13, medium roll)')
        check('an evading dodger punishes when his R1 cancel + strike beats the attacker\'s roll and he '
              'ends within 3.69 m: only the forward roll does (back 5.7 m, sideways 5.1 m away), and every '
              'direction escapes, so he takes it (dodge_families; a quarter of the presses before version 22)',
              (round(reaction_outcome([brief], [1.0], 2.5, my_roll=90.0, strikes=[15.0])['p_punish'], 3),
               round(reaction_outcome([brief], [1.0], 2.5, my_roll=40.0, strikes=[15.0])['p_punish'], 3),
               round(reaction_outcome([brief], [1.0], 2.5, moves=roll_motion(), my_roll=90.0,
                                      strikes=[15.0])['p_punish'], 3)),
              (1.0, 0.0, 0.25), 'reaction_outcome: press ~f25 + ready 20 + strike 15 = 60; roll root motion')
        check('with run, the back and side rolls run the last ~2 m in and punish a late roll too',
              (round(reaction_outcome([brief], [1.0], 2.5, my_roll=90.0, strikes=[15.0], run=True)['p_punish'], 3),
               round(reaction_outcome([brief], [1.0], 2.5, my_roll=40.0, strikes=[15.0], run=True)['p_punish'], 3)),
              (1.0, 0.0), 'reaction_outcome run: ~2 m x frames_per_metre added to the strike')
        jm = jump_motion()
        check('the jump in place: immune from f0 to the clip end, lands at f26.2, no travel',
              (jm['iframes'], round(jm['ready'], 1), jm['distance']), (25.0, 26.2, 0.0),
              'er-mechanics-jump jump_path 202000; JumpTable 132 f0-31')

        def burst(lo, air):
            return contact_geometry(bullet_points=[(f / TAE_FPS, 0.0, 0.0, 1e3) for f in np.arange(lo, lo + 16.0, 0.5)],
                                    air=air)
        check('a 16-frame in-air-invalidated burst: the jump clears it, a plain one it does not, the roll '
              '(13 i-frames) neither',
              tuple(round(reaction_outcome([burst(40.0, air)], [1.0], 2.5, moves=mv)['p_evade'], 6)
                    for air, mv in ((True, [jm]), (False, [jm]), (True, None))), (1.0, 0.0, 0.0),
              'jump_motion + _catches AIR; press window 55.5 - 25 .. 40')
        three = [([burst(lo, True)], [3.0 - k]) for k, lo in enumerate((40.0, 100.0, 160.0))]
        one = contact_geometry(bullet_points=[(f / TAE_FPS, 0.0, 0.0, 1e3) for f in np.arange(40.0, 176.0, 0.5)],
                               air=True)
        got3, got1 = sequence_outcome(three, 2.5), sequence_outcome([([one], [1.0])], 2.5)
        check('three bursts are jumped one by one; one long burst outlasts the jump',
              (round(got3['value'], 6), got3['moves'], round(got1['value'], 6)), (0.0, 'a000_202000', 1.0),
              'sequence_outcome')
        later = {**brief, 'T': brief['T'] + 70.0}
        vo = volley_outcome([([every], ['a']), ([later], ['b'])], {'a': 1.0, 'b': 1.0}, 2.5)
        check('two independent threats: the long one lands, the brief one 70 frames on is still rolled',
              (round(vo['value'], 6), round(vo['base'], 6), round(vo['p_evade'], 6)), (1.0, 2.0, 0.0),
              'volley_outcome (INFERRED independence)')
        nails = bullet_tree(t, 210451000)
        reach = {aim: {s for n, p, _, _, s, _, _ in fly_shots(t, nails, aim) if n['atk_row'] == 210451000
                       and any(math.hypot(q[0], q[2] - 6.0) < 0.5 for q in p)} for aim in (None, (0.0, 6.0))}
        check('Glintstone Nails (Bullet 210451000, numShoot 6 from -60 by 20): straight, one nail passes a '
              'point 6 m ahead; aimed at it at every link, all six do',
              (len(reach[None]), len(reach[(0.0, 6.0)])), (1, 6),
              'lockShootLimitAng 60 / 90 / 13 on the chain, launchConditionType 4 (FUN_14039da50)')
        leg = t.bullet[210451002]
        fast, slow = _flight(leg, (0.3, 1.0, 2.0), (37.0, 0.0)), bullet_flight(leg, (0.3, 1.0, 2.0), (37.0, 0.0))
        check('a row without gravity flies its level profile laid along the heading: the same path, ending '
              'and speed as bullet_flight (Bullet 210451002 at yaw 37)',
              (float(np.abs(fast[0] - np.array(slow[0])).max()) < 1e-9, fast[1] == slow[1],
               abs(fast[3] - slow[3]) < 1e-9), (True, True, True), '_flight, FUN_14039ac30 rule')
        check("a target changes nothing when no row of the tree aims: Lightning Slash's 2090-2093 "
              "(lockShootLimitAng 0)",
              (fly_tree(t, lb, (0.0, 3.0)) == fly_tree(t, lb),
               {t.bullet[n['bullet']]['lockShootLimitAng'] for n, _, _, _ in fly_tree(t, lb)}), (True, {0}),
              'row_shots: lockShootLimitAng 0 aims nothing')
        lance = t.find_weapon('Lance')
        fs = t.find_arts('Flaming Strike')
        check('Flaming Strike: R2 at f30-44 of 040000 (SpEffect 100050 f30-44, R2 cancel from f30) '
              'plays the 040010 swing that applies the fire buff',
              [(f['anim'], f['button'], f['first'], f['last']) for f in skill_followups(t, fs, lance)],
              [(40010, 'r2', 30, 44)], 'TAE a663 040000 events 66/0; COMMUNITY c0000.hks SwordArtsOneShot_onUpdate')
        lctx = WeaponContext('Lance', 'Heavy', 25, {'str': 60, 'dex': 20}, True)
        fo = skill_option(t, lance, fs, lctx, 25, damage_fn=lambda a, p, f: 100.0,
                          follow=skill_followups(t, fs, lance)[0])
        check('Flaming Strike with its follow-up: two parts, the second read from its own press (f30), '
              'FP 4 + 10, the 1775/1777 buff roots',
              ([p['cue'] for p in fo['parts']], fo['fp'], sorted(fo['buff_roots'])),
              ([0.0, 30], 14, [1775, 1777]), 'SwordArtsParam 214 useMagicPoint_L2/R2; TAE a663 040010')
        # A charged skill cast as it opens holds the charge, and up to its early release it looks
        # like that release (`skill_release_part`, `lookalike_pair`): the Glintstone Kris's Dart.
        kris, dart = t.find_weapon('Glintstone Kris'), t.find_arts('Glintstone Dart')
        kctx = WeaponContext('Glintstone Kris', 'Standard', 10, {'str': 16, 'dex': 18, 'int': 65}, False)
        krel = skill_releases(t, dart, kris)

        def kdmg(a, p, f):
            return 100.0
        ko = skill_option(t, kris, dart, kctx, 10, damage_fn=kdmg)
        kpart = skill_release_part(t, kris, dart, kctx, 10, damage_fn=kdmg)
        kp0 = ko['parts'][0]
        kland = skill_landing(t, kris, dart, kp0['hits'], None)
        kargs = (t, kris, dart, kp0['hits'], kland, kp0['offset'], kp0['cue'], ko['roll'], [15.0, 20.0])
        # The release at its window's first frame alone (`release_frames` of a one-frame window).
        kone = {**kpart, 'releases': [(0.0, 1.0)], 'shows': None}
        knows, sees1, sees = skill_reaction(*kargs), skill_reaction(*kargs, look=kone), skill_reaction(*kargs, look=kpart)
        nolist = skill_reaction(*kargs, look={**kpart, 'releases': None, 'shows': None})
        # The release at its first frame showing where its pose does (`skill_release_visible`).
        kvis = {**kpart, 'releases': [(0.0, 1.0)], 'shows': kpart['shows'][:1]}
        svis = skill_reaction(*kargs, look=kvis)
        lvis = skill_reaction(t, kris, dart, kp0['hits'], kland, kp0['offset'], kpart['offset'] + kpart['shows'][0],
                              ko['roll'], [15.0, 20.0])
        at0 = skill_reaction(*kargs, look={**kone, 'offset': 0.0})
        late = skill_reaction(t, kris, dart, kp0['hits'], kland, kp0['offset'], kpart['offset'], ko['roll'], [15.0, 20.0])
        kterm = skill_term(t, kris, [(dart, 1.0)], kctx, 10, 0.0, damage_fn=kdmg, react=True, strikes=[15.0, 20.0])
        kh = next(v for v in kterm['available'] if len(v.get('parts') or []) == 1)
        keys_ = ('dmg', 'p_evade', 'p_punish')

        def knet(o):
            # What the attacker nets: what lands less the punish (`react_factors`).
            return o['dmg'] - o['whiff_hp']
        check(f"the Glintstone Dart cast as it opens: its early release (SpEffect {RELEASE_EARLY_SPEFFECT}) is let go "
              f"at f{krel and krel[0]['first']}, its part's press; the PvP ranking's waiting defender cannot tell the "
              "hold from it before then. Since version 22 he picks the roll direction (dodge_families), and one "
              "escapes both from the cue. He escapes the bolt "
              f"({knows['p_evade']:.3f} -> {sees1['p_evade']:.3f}) and punishes {knows['p_punish']:.4f} -> "
              f"{sees1['p_punish']:.4f} (all releases {sees['p_punish']:.4f}; the hold read from the release "
              f"{late['p_punish']:.4f}). Version 25: he aims for his punish too, so knowing the attack is never worse "
              f"for him than not telling it from its release (attacker net {knet(knows):.2f} against "
              f"{knet(sees1):.2f} / {knet(sees):.2f}; in version 24 he punished 0.3294 knowing it and 0.5000 not, "
              "aiming at the middle of the escaping run, MEASURED); one release at 0 is the pair without releases, "
              "bit for bit; a "
              "lookalike told apart from the cue changes nothing. Knowing it is never worse for him against the "
              "release at its first frame or let go on any frame of its window, by his own measure (escape plus "
              "punish) and by the attacker's net. Version 28 restores the second: version 27's selftest runs at "
              "20:52-21:06 that day measured him escaping 0.929 and punishing 0.125 knowing the hold, 0.968 and "
              "0.139 against all the releases (its final run, 21:59, 1.000 and 0 either way), and its check was "
              "weakened to the first release alone, because a press clamped at his reaction to a release carried "
              "no timing error while his own aim did; every press carries it now (`pressed`)",
              (len(krel), kpart is not None and kpart['offset'] == float(krel[0]['first']) + (ko['first_offset'] or 0.0),
               round(sees1['p_evade'], 6) == round(knows['p_evade'], 6), all(sees1[x] == nolist[x] for x in keys_),
               all(at0[x] == knows[x] for x in keys_),
               knet(knows) <= knet(sees1) + 1e-9, knet(knows) <= knet(sees) + 1e-9,
               all(knows['p_evade'] + knows['p_punish'] >= o['p_evade'] + o['p_punish'] - 1e-9 for o in (sees1, sees))),
              (1, True, True, True, True, True, True, True),
              'c0000.hks SwordArtsOneShot_onUpdate (L2 up in 100285); lookalike_plan (MEASURED 2026-11-16)')
        kth = [skill_release_visible(t, dart, krel[0]['anim'], krel[0]['first'], threshold=x) for x in (0, 0.05, 0.20)]
        check(f"the Dart's release at f{krel[0]['first']} shows {kpart['shows'][0]} real frames after its switch (the "
              f"blend into 040001 over its type-16 end, a bone 10 cm off the hold, release_visible; 3 in the "
              f"2026-11-11 survey, and {kth[1]} / {kth[2]} at 5 / 20 cm as there; 0 cm is the switch frame): against "
              f"it alone he still escapes ({svis['p_evade']:.3f}; read from there {lvis['p_evade']:.3f}, punish "
              f"{lvis['p_punish']:.4f} -> {svis['p_punish']:.4f}: a roll direction escapes both before it shows; "
              f"0.929 since version 27, the attacker tracking a side roll)",
              (kpart['shows'][0], kth, round(svis['p_evade'], 6) == round(lvis['p_evade'], 6) == round(knows['p_evade'], 6)),
              (3, [0, 2, 4], True),
              'release_visible; c0000.hks SwordArtsOneShot_onUpdate')
        check(f"the Dart's window f{krel[0]['first']}-{krel[0]['last']} gives a release on each of its "
              f"{len(kpart['releases'])} real frames (release_frames): the hold read against all of them, escape "
              f"{sees1['p_evade']:.3f} -> {sees['p_evade']:.3f}, punish {sees1['p_punish']:.4f} -> {sees['p_punish']:.4f}; "
              "skill_term reads the hold with them",
              ([round(d, 6) for d, _ in kpart['releases']], round(sum(w for _, w in kpart['releases']), 9),
               (kh.get('release_look') or {}).get('offset') == kpart['offset'],
               kh['react']['p_evade'] == [round(sees['p_evade'], 3)]),
              ([float(k) for k in range(13)], 1.0, True, True), 'release_frames over [13, 26); lookalike_pair releases')
        falx, rev = t.find_weapon('Falx'), t.find_arts("Revenger's Blade")
        fctx = WeaponContext('Falx', 'Standard', None, {'str': 40, 'dex': 40}, False)
        fo = skill_option(t, falx, rev, fctx, fctx.level, damage_fn=kdmg)
        fp0 = fo['parts'][0]
        fland = skill_landing(t, falx, rev, fp0['hits'], None)
        fargs = (t, falx, rev, fp0['hits'], fland, fp0['offset'], fp0['cue'], fo['roll'], [15.0, 20.0])
        fpart = skill_release_part(t, falx, rev, fctx, fctx.level, damage_fn=kdmg)
        fk, fs1 = skill_reaction(*fargs), skill_reaction(*fargs, look={**fpart, 'releases': [(0.0, 1.0)], 'shows': None})
        fs = skill_reaction(*fargs, look=fpart)
        check(f"Revenger's Blade on the Falx cast as it opens: released early at f{fpart and fpart['offset']} alone, "
              f"the hold is escaped every time by a defender who knows it ({fk['p_evade']:.3f}) and, since version 22 "
              f"picks a roll direction that escapes both (dodge_families; half the time before, pressing the four "
              f"a quarter each), by one who cannot tell it from the release before then ({fs1['p_evade']:.3f}); let "
              f"go on any of its window's {len(fpart['releases'])} frames {fs['p_evade']:.3f}: its land "
              f"share {fk['dmg'] / fland['dmg']:.3f} -> {fs1['dmg'] / fland['dmg']:.3f} -> {fs['dmg'] / fland['dmg']:.3f}. "
              "Version 27: the attacker tracks a side roll until his JumpTable 7, so no direction escapes every "
              "time: 0.929 known, 0.929 not told from the release, 0.929 over all its frames (MEASURED 2026-11-16; "
              "1.0 each in version 26); knowing it is never less. Version 28: not told from the release it was "
              "0.679, as at 3 m (and 2.5 m rolling forward or back) his plan waited for the release, to react to it "
              "with no timing error, and met the hold too late (escape 0); with the error on that reaction too "
              "waiting is worth less to him, and he commits from the cue and escapes the hold",
              (round(fk['p_evade'], 3), round(fs1['p_evade'], 3), round(fs['p_evade'], 3),
               all(fk['p_evade'] + fk['p_punish'] >= o['p_evade'] + o['p_punish'] - 1e-9 for o in (fs1, fs))),
              (0.929, 0.929, 0.929, True),
              'lookalike_pair, 2026-11-10 survey; MEASURED 2026-11-16')
        # A stance: L2 into 040050 (no hit, no FP), then R1 -> 040060 or R2 -> 040070.
        uchi, unsh = t.find_weapon('Uchigatana'), t.find_arts('Unsheathe')
        uctx = WeaponContext('Uchigatana', 'Keen', 25, {'str': 20, 'dex': 60}, False)
        check('Unsheathe: 040050 hands over on R1 at f8 and on R2 at f6 (its TimeAct cancels)',
              [(b, A_lead) for b in ('r1', 'r2')
               for A_lead in [lead_in(t, unsh, skill_profile(t, unsh, uchi, 0, 'one', b))]],
              [('r1', 8.0), ('r2', 6.0)], 'TAE a614 040050 JumpTable R1 / R2 cancels')
        uo = {b: skill_option(t, uchi, unsh, uctx, 25, damage_fn=lambda a, p, f: 100.0, stance=b) for b in ('r1', 'r2')}
        check('Unsheathe R1: the 040060 slash (clip f13) hits 8 + 13 real frames from the L2 press, 10 FP; '
              'R2: the 040070 slash (clip f15) at 6 + 15, 15 FP; each read from its press',
              {b: (o['variant'], o['first_offset'], o['hit_windows'][0][0], o['fp'], o['cue'])
               for b, o in uo.items()},
              {'r1': ('stance R1 040060', 8.0, 21.0, 10, 8.0), 'r2': ('stance R2 040070', 6.0, 21.0, 15, 6.0)},
              'TAE a614 040050/040060/040070; SwordArtsParam 114 useMagicPoint_R1/R2; BEH W_DrawStanceRightAttack*')
        uterm = skill_term(t, uchi, [(unsh, 1.0)], uctx, 25, 0.0, damage_fn=lambda a, p, f: 100.0)
        check('Unsheathe is two options in the sweep, one per button, and the better is taken',
              (sorted(o['variant'] for o in uterm['available']),
               uterm['best']['variant'] == max(uterm['available'], key=lambda o: o['score'])['variant']),
              (['stance R1 040060', 'stance R2 040070'], True), 'skill_term pool, _skill_option_scored')
        stances = {}
        for s in sorted(t.arts):
            w = next((w for w in sorted(t.reg.weapon) if w % 10000 == 0 and t.reg.weapon[w]['swordArtsParamId'] == s),
                     None) or _probe_weapon(t, s, t.ash_gems().get(s))
            if w is not None:
                bs = skill_profile(t, s, w, 0, 'one')['stance_buttons']
                if bs:
                    stances[t.arts_name(s)] = bs
        check('the stance skills, read from the TimeActs: an entry 040050 and loop with no hit or FP, attacks on '
              'R1 040060 / R2 040070 (one shot for the bows)',
              stances,
              {**{n: ['r1', 'r2'] for n in ('Unsheathe', 'Square Off', 'Night-and-Flame Stance', 'Transient Moonlight',
                                             'Overhead Stance', 'Wing Stance', 'Moon-and-Fire Stance')},
               **{n: ['r1'] for n in ('Through and Through', 'Barrage', 'Mighty Shot', 'Enchanted Shot', 'Sky Shot',
                                      'Rain of Arrows', "Igon's Drake Hunt", 'Repeating Fire', 'Fan Shot',
                                      'Rancor Shot')}},
              'TAE a6xx 040050/040051/040060/040070; docs/er-mechanics/chainsaw/affected-class.md IsStanceArts')
        mf = t.find_arts('Moon-and-Fire Stance')
        rtb = t.find_weapon("Rellana's Twin Blades")
        check('Moon-and-Fire Stance opens with its stance attack 040060, not its R1 follow-up 040010 '
              '(100054 f35-50 of 040060)',
              (main_anim(skill_profile(t, mf, rtb)), [(f['button'], f['anim'], f['first']) for f in skill_followups(t, mf, rtb)]),
              (40060, [('r1', 40010, 35)]), 'TAE a918 040060 event 66 100054; BEH SwordArtsOneShotComboEnd')
        check('Wild Strikes is not a stance cast (its loop 040051 hits): lead-in still the 040050 clip',
              (skill_profile(t, wild, claymore)['stance_buttons'], lead_in(t, wild, pw)), ([], 32.0),
              'TAE a610 040051 hits')
        roar = t.find_arts("Braggart's Roar")
        check("Braggart's Roar lasts 60 s (root 1860 cycles 1861)", _buff_duration(t, 1860), 60.0,
              'REGULATION SpEffect 1861 effectEndurance')
        beng = {'numerator': 600.0, 'commit': 30.0, 'exchange': {'win': 0.2}}
        bo = buff_option(t, roar, lance, 1100.0, 1000.0, beng, far, 5, 60.0, roots=[1860], fight_seconds=25.0)
        check("buff option: a 60 s roar over a 25 s fight is cast before contact (no recast), and its x0.9 "
              "damage taken cuts (1 - win) x 0.1 x the opponents' landed hit",
              (bo['recasts'], round(bo['score'], 3)), (0.0, round(1100.0 * (600 + 0.8 * 0.1 * 300) / 600, 3)),
              'section 16b; REGULATION 1861 defPlayerDmgCorrectRate 0.9')
        bo = buff_option(t, roar, lance, 1100.0, 1000.0, beng, far, 5, 60.0, roots=[1860])
        pts = BUFFS.recast_points(60.0, FIGHT_SECONDS, uses=5)
        tf = sum(1.0 - n * 60.0 / BUFFS.CAST_FPS / f for f, n in pts) / len(pts)
        check("over a 180-300 s fight the 60 s roar is recast 2 to 4 times (5 casts allowed), stays on, and "
              "each recast's 60 frames come off the fight",
              (2.0 <= bo['recasts'] <= 4.0, bo['uptime'], round(bo['score'], 3)),
              (True, 1.0, round(1100.0 * (600 + 0.8 * 0.1 * 300) / 600 * tf, 3)),
              'er-mechanics-buffs.recast_plan; user directive 2026-10-01 (3 to 5 minute fight)')
        check('the engagement spacing a visible buff must outlast is 5 s, not the fight length',
              (ENGAGEMENT_SECONDS, FIGHT_SECONDS[0] >= 180.0), (5.0, True), 'er-mechanics-status.ENGAGEMENT_SECONDS')
        bb = t.find_weapon('Backhand Blade')
        swift = t.find_arts('Swift Slash')
        so = skill_option(t, bb, swift, lctx, 25, damage_fn=lambda a, p, f: 100.0)
        melee = [(h['judge'], h['atk_row']) for h in so['hit_rows'] if h['kind'] == 'melee']
        idx = {x['judge']: x['attack_index'] for x in skill_profile(t, swift, bb)['anims'][40000]
               if x['event'] == 'attack'}
        check('Swift Slash on a Backhand Blade: two blades on attack indices 0 and 1 (separate hit lists) '
              'and four delayed 2074 bursts, one bullet event each',
              (sorted(melee), [idx[6020], idx[6021]], sum(h['kind'] == 'bullet' for h in so['hit_rows'])),
              ([(6020, 600000070), (6021, 600000071)], [0, 1], 4),
              'TAE a877 040000; AtkParam 600000071 dummies 11120/11100 on the left blade (isDualBlade 1)')
        ci = skill_contest_inputs(t, bb, swift, so)
        check('its contest inputs: first contact 2.5 m ahead at or after the f28 swing, no hyperarmor, '
              '50 stamina (the right blade\'s row; the left blade\'s charges 0)',
              (ci['strike'] >= 28, ci['hyper'], ci['stamina']), (True, [], 50), 'TAE a877 040000; AtkParam')
        # bd er-effects-rs-doax: each burst leaves DummyPoly 9 where the dash has carried the caster.
        bursts = [h for h in so['hit_rows'] if h['kind'] == 'bullet']
        launches = [skill_bullet_launch(t, bb, swift, h['anim'], h['frame'], h['root_bullet']) for h in bursts]
        check('Swift Slash: the four bursts (f27/29/30/33) leave c0000 DummyPoly 9 (TAE event 2 +0), which rides the '
              'Pelvis 0.1-0.4 m ahead of the root, after 0.49/1.57/2.83/5.12 m of forward root motion',
              ([h['frame'] for h in bursts], [x['src'] for x in launches],
               all(0.1 < x['pos'][2] - x['peak'] < 0.4 for x in launches), [round(x['peak'], 2) for x in launches]),
              ([27, 29, 30, 33], ['dummy'] * 4, True, [0.49, 1.57, 2.83, 5.12]),
              'TAE a877_040000 event 2 (9, 0, judge); c0000.flver dummy 9 on Pelvis; HKX root motion')
        push = _pose()[1].PUSH_CAPSULE_RADIUS
        profile = _pose()[1].defender_profile('idle')

        def touches_by_hand(h, launch, dist, hold):
            """The burst flown from its launch moved back by the hold, tested point by point."""
            held = max(0.0, launch['peak'] - (dist - 2 * push)) if hold else 0.0
            moved = {**launch, 'pos': (launch['pos'][0], launch['pos'][1], launch['pos'][2] - held)}
            root = bullet_tree(t, h['root_bullet'])
            for node, path, _, _ in fly_tree(t, root, launch=moved):
                if node['atk_row'] != h['atk_row']:
                    continue
                for q in path:
                    d = _pose()[1].contact_disc_radius(q[1], node['radius_m'], profile)
                    if d is not None and math.hypot(q[0], q[2] - dist) < d:
                        return True
            return False
        con = _hit_contacts(t, bb, swift, bursts)
        model = [c(lambda _w: 1.5) is not None for _, c, _, _ in con]
        by_hand = [touches_by_hand(h, x, 1.5, True) for h, x in zip(bursts, launches)]
        check('Swift Slash at 1.5 m: the bursts the landing credits are the ones flown by hand from the dash-shifted '
              'DummyPoly, held back where the defender\'s body stops the dash (all four)',
              (model, sum(model)), (by_hand, 4), 'bullet_launch; er-mechanics-reach.front_contact_times')
        check('the f33 burst, launched 5.3 m out past a defender at 1.5 m who stopped the dash: touches him held '
              'back, misses him without the hold',
              (touches_by_hand(bursts[3], launches[3], 1.5, True), touches_by_hand(bursts[3], launches[3], 1.5, False)),
              (True, False), 'push capsules: the root stops at dist - 0.8 m')
        far = [c(lambda _w: 8.0) is not None for _, c, _, _ in con]
        old = {'pos': (0.0, BULLET_ORIGIN_HEIGHT_M, BULLET_ORIGIN_M), 'yaw': 0.0, 'peak': -math.inf}
        check('Swift Slash at 8 m: the landing credits the bursts flown by hand from the dash; the old 1 m '
              'placement reached none',
              (far, any(far), [touches_by_hand(h, old, 8.0, False) for h in bursts]),
              ([touches_by_hand(h, x, 8.0, True) for h, x in zip(bursts, launches)], True, [False] * 4),
              'bullet_launch; scratchpad ssv/placement.log (model 0 bursts at 6-10 m)')
        # The event's orientation byte and the prefixes (FUN_1403fb5f0 / FUN_140b659a0, 1.16.2).
        check('Swift Slash: its bursts carry orientation byte 0, so they head along the facing, level',
              ([x['orient'] for x in skill_profile(t, swift, bb)['anims'][40000] if x['kind'] == 'bullet'],
               [(round(x['yaw'], 3), round(x['pitch'], 3)) for x in launches]), ([0] * 4, [(0.0, 0.0)] * 4),
              'TAE a877_040000 event 2 Args byte 0xc')
        # bd er-effects-rs-dnok: hit priority records (`HIT_RECORD` in er-mechanics-attacks).
        check('Swift Slash: the four bursts take slot 1\'s record (event 341 f27-34), both blades slot '
              '0\'s (f28-38), all priority 0, so one burst and one blade land: 200 of 600',
              (sorted({(h['kind'], h['record'], h['priority']) for h in so['hit_rows']}),
               [h['kind'] for h, ok in zip(so['hit_rows'], record_mask(so['hit_rows'])) if ok], so['dmg']),
              ([('bullet', (40000, 1), 0), ('melee', (40000, 0), 0)], ['bullet', 'melee'], 200.0),
              'TAE a877_040000 events 341 / 1 Args 0xc / 2 Args 0x10, 0x12; FUN_14051b210 / FUN_14051b870 '
              '(1.16.2)')
        sland = skill_landing(t, bb, swift, so['hit_rows'], None, distances=(1.5, 8.0))
        check('Swift Slash on a defender who stays: at most one burst and one blade land wherever it '
              'connects (1.5 and 8 m)',
              [(d, round(sum(x for h, x in zip(so['hit_rows'], sland['share_by_distance'][d])
                             if h['kind'] == 'bullet'), 3) <= 1.0,
                round(sum(x for h, x in zip(so['hit_rows'], sland['share_by_distance'][d])
                          if h['kind'] == 'melee'), 3) <= 1.0) for d in sland['used']],
              [(d, True, True) for d in sland['used']], 'skill_landing with the record rule')
        cv, sof = t.find_weapon('Cranial Vessel Candlestand'), t.find_arts('Surge of Faith')
        cvo = skill_option(t, cv, sof, WeaponContext('Cranial Vessel Candlestand', 'Standard', 10,
                                                     {'str': 40, 'fai': 40}, False), 10,
                           damage_fn=lambda a, p, f: 100.0)
        root = [h for h in cvo['hit_rows'] if h['kind'] == 'bullet' and h['anim'] == 40000 and h['frame'] == 110
                and h['root_bullet'] == 2373]
        same = [h for h in cvo['hit_rows'] if root and h.get('record') == root[0]['record']]
        cmask = record_mask(cvo['hit_rows'])
        check('a child shares its parent\'s record and launch: Surge of Faith 040000 f110 bullet 2373 '
              '(atk 301217912) and its child 2375 (atk 301217914) carry one key, the f110 shots 2373 and '
              '2374 and their children share it, and one launch of them lands, the one created first, '
              'both its rows',
              ([h['atk_row'] for h in root], len({h['record'] for h in root}), root[0]['record'] is not None,
               sorted({h['root_bullet'] for h in same}),
               sorted(h['root_bullet'] for h, ok in zip(cvo['hit_rows'], cmask) if ok and any(h is x for x in same))),
              ([301217912, 301217914], 1, True, [2373, 2374], [2373, 2373]),
              'FUN_14039bba0 -> FUN_14038b740 copies BulletAttackInfo +0x640 / +0x648 / +0x658; '
              'FUN_14051b870 0x14051b8c9 passes the entry\'s own id (1.16.2)')
        # The bullet hit lists (`HIT_LIST_NOTE`) on top of the records: every 237x flame row has
        # `isUseSharedHitList` and leaves 2375 in the same list, so each launch lands once.
        check('Surge of Faith lands 6 of its 22 rows on one defender: the f24 swing, the f62 pillar 2370 '
              '(no list; its record\'s other launches are priority 1), and one flame of each later record, '
              'its child 2375 held off by the flame\'s list: 600 at 100 a row, not 2,200',
              ([(h['frame'], h['bullet'] if h['kind'] == 'bullet' else 'melee', h['lands'])
                for h in cvo['hit_rows'] if h['lands']], len(cvo['hit_rows']),
               sorted({h['hit_list'][2] for h in cvo['hit_rows'] if h['kind'] == 'bullet' and h['hit_list']}),
               cvo['dmg']),
              ([(24, 'melee', True), (62, 2370, True), (77, 2371, True), (88, 2373, True), (99, 2372, True),
                (110, 2373, True)], 22, [2371, 2372, 2373, 2374], 600.0),
              'Bullet 2370 isUseSharedHitList 0, 2371-2375 1, HitBulletID 2375; skill TAE 040000 events 2 / '
              '341; FUN_14038fe80, FUN_14039bba0 -> FUN_14038b740 +0x638 (1.16.2)')
        srs, wog = t.find_weapon('Sacred Relic Sword'), t.find_arts('Wave of Gold')
        wgo = skill_option(t, srs, wog, WeaponContext('Sacred Relic Sword', 'Standard', 10,
                                                      {'str': 15, 'dex': 24, 'fai': 67}, False), 10,
                           damage_fn=lambda a, p, f: 100.0)
        check('Wave of Gold is one hit: its wave 4000 -> ... -> 4045 changes AtkParam row as it travels '
              '(905, 907, 906, 908) inside the one 4 s list the 17-shot launch makes, so one row lands',
              ([(h['bullet'], h['atk_row'], h['hit_list']) for h in wgo['hit_rows']],
               [h['bullet'] for h in wgo['hit_rows'] if h['lands']], wgo['dmg']),
              ([(4000, 300310905, (40000, 0, 4000)), (4024, 300310907, (40000, 0, 4000)),
                (4036, 300310906, (40000, 0, 4000)), (4045, 300310908, (40000, 0, 4000))], [4000], 100.0),
              'Bullet 4000 numShoot 17 isUseSharedHitList 1 dmgHitRecordLifeTime 4, HitBulletID chain to '
              '4045; FinalDamageRateParam 30000-30003; FUN_14038fe80 (1.16.2)')
        pcw, pcs =t.find_weapon("Prelate's Inferno Crozier"), t.find_arts("Prelate's Charge")
        pco = skill_option(t, pcw, pcs, WeaponContext("Prelate's Inferno Crozier", 'Standard', 10,
                                                      {'str': 50, 'fai': 30}, False), 10,
                           damage_fn=lambda a, p, f: 100.0)
        fire = [h for h in pco['hit_rows'] if h.get('root_bullet') == 2311]
        # The carrier 2311 (AtkParam_Pc 0, no damage) is no hit row; as a contact it would come
        # first in its launch, at the child's priority and id.
        carrier = [(record_time(h) - 0.05, h['record'], h['priority'], h['created']) for h in fire]
        check("Prelate's Charge: the carrier 2311 deals nothing, so its fire 2312 (atk 30000835) is the "
              "event's only row, at the event's record and priority 2; a carrier that touches him first "
              "does not stop it (one launch), while the f36 swing (priority 0) that lands first does",
              ([h['atk_row'] for h in fire], [(h['record'], h['priority']) for h in fire],
               ATK.shared_landings(carrier + [(record_time(h), h['record'], h['priority'], h['created'])
                                              for h in fire]),
               [h['kind'] for h, ok in zip(pco['hit_rows'], record_mask(pco['hit_rows'])) if ok]),
              ([30000835], [((40000, 0), 2)], [True, True], ['melee', 'melee']),
              'TAE a613_040000 events 1 / 2 / 341; Bullet 2311 HitBulletID 2312; BulletAttackInfo +0x648 / '
              '+0x658 copied by FUN_14038b740, FUN_14051b870 0x14051b8c9 (1.16.2)')
        thunder = skill_profile(t, t.find_arts('Thundercloud Form'), t.find_weapon('Dragon King\'s Cragblade'))
        check('Thundercloud Form keeps every hit: its six AtkParam 303401201 swings (priority -1) and the '
              'f84 one (priority 0) open no record, the clip has no event 341',
              [(x['frames'][0], x['priority'], x['record']) for x in thunder['anims'][40000] if x['event'] == 'attack'],
              [(34, -1, None), (42, -1, None), (50, -1, None), (58, -1, None), (67, -1, None), (75, -1, None),
               (84, 0, None)], 'TAE a826_040000 (no event 341)')
        def rec_geo(frame, hpr):
            # Samples from `frame` on for 170 frames that touch him anywhere within 100 m (past his
            # roll's invincibility and his run), one hit list.
            n = 1 if frame < 31 else 170
            return ({'T': frame + np.arange(n, dtype=float), 'X': np.zeros(n), 'Z': np.full(n, -2.5),
                     'R': np.full(n, 100.0), 'PK': np.full(n, -9.0), 'HPR': hpr,
                     'CAUGHT': {'lists': [caught_list(t, 300000000, 0.0)], 'wf': 1.0}}, frame)
        rows = [CaughtModel.of([rec_geo(30.0, (k, p0, 0)), rec_geo(31.0, (k2, p1, c1))], 62.0,
                               {30.0: 100.0, 31.0: 100.0}).row(2.5, 0)[1][0]
                for k, k2, p0, p1, c1 in (('r', 'r', 0, 0, 1), ('r', 'r', 1, 0, 1), ('r', 'r', 0, 1, 1),
                                          ('r', 's', 0, 0, 1), ('r', 'r', 0, 0, 0))]
        check('a caught defender: a later attack of the record lands nothing at an equal or higher priority, '
              're-opens it at a strictly lower one, an attack of another record lands, and a later '
              'geometry of the holding launch lands',
              rows, [0.0, 1.0, 0.0, 1.0, 1.0],
              'FUN_14051b210 (a strictly lower priority replaces the entry and clears the landed flag); '
              'FUN_14051b870 0x14051b8c9 (the entry\'s own id passes), 1.16.2')
        torch, fb = t.find_weapon('Steel-Wire Torch'), t.find_arts('Firebreather')
        fbp = skill_profile(t, fb, torch)['anims']
        fbl = {a: skill_bullet_launch(t, torch, fb, a, x['frames'][0], x['bullet']['bullet'])
               for a in (40000, 40040) for x in fbp.get(a, []) if x['kind'] == 'bullet'}
        fbx = next(x for x in fbp[40000] if x['kind'] == 'bullet')
        check("Firebreather's breath carries byte 2: it leaves c0000 DummyPoly 92 itself along the dummy's posed axis, "
              "which at f29 points 28 degrees aside and 25 down; its other-side clip 040040 mirrors it",
              (fbx['orient'], fbx['dummy'], fbl[40000]['src'], fbl[40000]['pos'][1] == fbl[40000]['dummy_y'],
               round(fbl[40000]['yaw']), round(fbl[40000]['pitch']), round(fbl[40040]['yaw'] + fbl[40000]['yaw'])),
              (2, 92, 'dummy axis', True, 28, -25, 0), 'TAE a671_040000 / 040040 event 2; FUN_1403f51a0')
        gd, bw = t.find_weapon('Grafted Dragon'), t.find_arts('Bear Witness!')
        cat_bw = SKILL_TAE_BASE + t.arts[bw]['swordArtsTypeNew']
        right = bullet_launch(cat_bw, 40040, 49 / TAE_FPS, 20300, held_models(t, gd))
        left = bullet_launch(cat_bw, 40040, 49 / TAE_FPS, 20300, held_models(t, gd, 'left'))
        check("Bear Witness! 040040's DummyPoly 20300 names the left-hand weapon's model: unknown for a right-hand cast "
              "(the placeholder), the Grafted Dragon's dummy 300 when it is in the left hand",
              ((20300 // 1000) % 100, right['src'], left['src']), (20, 'root', 'dummy'),
              'FUN_140b659a0: 20 -> ChrAsm slot 7')
    # The dummy matrix's forward row is the FLVER dummy's forward vector (`INFERRED`): read so, the
    # posed axes of the byte-2 events point where each attack goes (`MEASURED`).
    axes = {}
    for cat in sorted({SKILL_TAE_BASE + r['swordArtsTypeNew'] for r in t.arts.values()}):
        for anim in ATK.tae_animations(cat) or {}:
            for e in ATK.resolve_events(cat, anim)[2] or []:
                if e.type == EV_BULLET and len(e.params) > 12 and e.params[12]:
                    got = bullet_launch(cat, anim, e.start, struct.unpack_from('<i', e.params, 0)[0],
                                        {'R0': None, 'R1': None, 'L0': None, 'L1': None}, orient=e.params[12])
                    if got['src'] == 'dummy axis':
                        axes.setdefault(cat, []).append((anim, round(e.start * TAE_FPS), got['yaw'], got['pitch']))

    def axes_of(name):
        return axes.get(SKILL_TAE_BASE + t.arts[t.find_arts(name)]['swordArtsTypeNew'], [])
    bows = axes_of('Mighty Shot') + axes_of('Enchanted Shot') + axes_of('Repeating Fire')
    sky = axes_of('Sky Shot')
    ring = [a for a in axes_of('Light') if a[1] == 26]
    check("posed dummy axes: the bow skills' arrows leave along the facing (within 5 degrees, level within 7), Sky "
          "Shot's 80 degrees up, Light's eight f26 beams spread round him and down",
          (bool(bows) and all(abs(y) < 5.0 and abs(p) < 7.0 for _, _, y, p in bows),
           bool(sky) and all(p > 70.0 for *_, p in sky),
           len(ring) == 8 and max(y for _, _, y, _ in ring) - min(y for _, _, y, _ in ring) > 90.0
           and all(p < -30.0 for *_, p in ring)),
          (True, True, True), 'c0000.flver dummy forward +0x10; HKX pose; TAE event 2 byte 0xc')
    # The HP cap (bd er-effects-rs-doax): what one caught defender takes stops at his health; the
    # punish does not.
    import numpy as np
    every = {'T': np.arange(40.0, 56.0, 0.5), 'X': np.zeros(32), 'Z': np.full(32, -99.0), 'R': np.full(32, 1e3),
             'PK': np.full(32, -math.inf)}
    plain = sequence_outcome([([every], [5000.0])], 2.5, my_roll=90.0, strikes=[15.0])
    capped = sequence_outcome([([every], [5000.0])], 2.5, my_roll=90.0, strikes=[15.0], hp=1900.0)
    check('a 5000 HP attack on a 1900 HP defender: lands at most 1900, the punish unchanged',
          (round(capped['base'], 1), capped['value'] <= 1900.0 + 1e-9, round(capped['value'] / plain['value'], 4),
           capped['punish_hp'] == plain['punish_hp']),
          (1900.0, True, 0.38, True), 'SpellModel.defender hp: CalcCorrectGraph 100 at the median Vigor')
    # The caught defender (bd er-effects-rs-n7dd): a lingering area of 13 half-second ticks around
    # him. A flinch (level 0, Swift Slash's burst row) lets him go at once; a middle stagger (its
    # blade row, poise 405 against 62) holds him one tick more, then DamageCount lets him roll out.
    n = 400
    pool = {'T': np.arange(30.0, 30.0 + n * 0.5, 0.5), 'X': np.zeros(n), 'Z': np.full(n, -2.5), 'R': np.full(n, 1.0),
            'PK': np.full(n, -math.inf)}
    shares = {}
    for row in (600000074, 600000070):
        g = {**pool, 'CAUGHT': {'lists': [caught_list(t, row, 405.0, 13, 0.5, 1 / 13)], 'wf': 1.0}}
        cm = CaughtModel.of([(g, 30.0)], 62.0, {30.0: 1300.0})
        shares[row] = (round(cm.landed(2.5)[0][0][0] * 13, 3), round(cm.worth(2.5)[0], 1))
    check('a caught defender takes a lingering area\'s ticks only while he is still inside: 1 of 13 when the tick '
          'flinches him, 2 when it staggers him (then the DamageCount 2 roll cancel at 10 frames < the 15-frame '
          'tick), not 13',
          (shares[600000074], shares[600000070]), ((1.0, 100.0), (2.0, 200.0)),
          'frame-advantage reaction (TAE damage clips), displacement_fn, roll_motion')
    # A lingering line laid along the attack axis, as Ghostflame Ignition's R1 follow-up lays its
    # trail (Bullet 2294: hitRadius 1.0, life 5.0 s, record 0.5 s, AtkParam_Pc 303401305 with
    # dmgLevel 0 and no poise damage, `REGULATION`; flown 2.8 to 19.7 m ahead with a contact disc of
    # about 1.4 m, `MEASURED` 2026-11-14), 10 ticks. Its ticks flinch him (level 0), so he leaves at
    # once: straight back he stays in it for 8 (rolling) or 9 (running) of them, to a side 1
    # (`_escapes`, 2026-11-14).
    zs, tt = np.arange(2.8, 19.7, 0.25), np.arange(72.0, 222.0, 0.5)
    n = len(tt) * len(zs)
    line = {'T': np.repeat(tt, len(zs)), 'X': np.zeros(n), 'Z': -np.tile(zs, len(tt)), 'R': np.full(n, 1.4),
            'PK': np.full(n, -math.inf), 'CAUGHT': {'lists': [caught_list(t, 303401305, 0.0, 10, 0.5, 0.1)], 'wf': 1.0}}
    cm = CaughtModel.of([(line, 72.0)], 62.0, {72.0: 1273.0})
    back = [cm._play(0, 4.3, nm, roll, run)[0][0] * 10 for nm, roll, run in cm.escapes if run == ESCAPE_RUNS[0]]
    check('a caught defender leaves a lingering line laid along the attack axis to the side: 1 of its 10 ticks '
          '(127.3 of 1273 HP) 4.3 m out, where leaving straight back (the only way out until 2026-11-14) took 8 '
          'rolling or 9 running',
          ([round(x, 3) for x in back], round(cm.landed(4.3)[0][0][0] * 10, 3), round(cm.worth(4.3)[0], 1)),
          ([8.0, 9.0], 1.0, 127.3), '_escapes: roll_motion side rolls (TAE), run speed; Bullet 2294')
    # Bullets of one tree that hit at different times (2026-11-16, the user: "I'm quite curious if
    # the ranking for rosus summons model correctly accomodates for the projectiles since they hit
    # at different times"). Rosus's Summons' three bursts, Bullet 2872 -> 2873 -> 2874 (`HitBulletID`
    # at expiry, each `life` 0.23 s, `hitRadius` 2.0, `isPenetrateChr` 1, no shared record,
    # AtkParam_Pc 301412902-904 dmgLevel 2, `REGULATION`), stand 4.5 m ahead 7.5 real frames apart
    # (`MEASURED` fly_tree, 2026-11-16). One geometry, a list per burst with its own rows: until
    # 2026-11-16 all three landed on the frame the first touched him, with one burst's poise.
    T3 = np.arange(52.0, 74.5, 0.5)
    B3 = np.where(T3 < 59.5, 2872, np.where(T3 < 67.0, 2873, 2874))
    spot = {'T': T3, 'X': np.zeros(len(T3)), 'Z': np.full(len(T3), -4.5), 'R': np.full(len(T3), 2.4),
            'PK': np.full(len(T3), -math.inf), 'B': B3}

    def bursts(rows):
        return {**spot, 'CAUGHT': {'lists': [caught_list(t, 301412902 + k, 202.5, 1, 0.0, 1 / 3,
                                                         (2872 + k,) if rows else None) for k in range(3)],
                                   'wf': 1.0}}
    per = {(rows, p): [round(x, 3) for x in CaughtModel.of([(bursts(rows), 52.0)], p, {52.0: 939.3}).row(4.5, 0)[0]]
           for rows in (True, False) for p in (62.0, 700.0)}
    cm = CaughtModel.of([(bursts(True), 52.0)], 62.0, {52.0: 939.3})
    check('bullets of one tree land when each touches him: Rosus\'s Summons\' three bursts all land on a defender '
          'whose poise each breaks (202.5 > 62: its stagger holds him in the 2 m burst), only the first on one whose '
          'poise holds (he leaves at once); without rows all three land at the first touch either way (the old '
          'rule); caught at f60 the first burst has ended and never lands',
          (per[(True, 62.0)], per[(True, 700.0)], per[(False, 700.0)], cm._live(0, 60.0)),
          ([1.0, 1.0, 1.0], [1.0, 0.0, 0.0], [1.0, 1.0, 1.0], ([1], [0])),
          'Bullet 2872-2874, AtkParam_Pc 301412902-904 (REGULATION); caught_branch (TAE damage clips)')
    # His whiff's recovery holds him (version 21): three hits at f30, f40 and f50 on a 1 m disc around
    # him 2.5 m out, each Swift Slash's burst row (level 0 even when it breaks him). Free at once he
    # rolls out under the first; still in his recovery to f45 he stands and takes the second too,
    # and a run out waits for the move floor (f55) and takes all three. A middle stagger at f30
    # (Swift Slash's blade row) ends the recovery: its DamageCount 1 roll cancel (25 frames) lets him
    # roll at f55 whatever the floor, so the f70 hit misses with floors at f80.
    def disc(frame, row, poise):
        T = np.array([frame, frame + 0.5])
        return ({'T': T, 'X': np.zeros(2), 'Z': np.full(2, -2.5), 'R': np.full(2, 1.0), 'PK': np.full(2, -math.inf),
                 'CAUGHT': {'lists': [caught_list(t, row, poise)], 'wf': 1.0}}, frame)
    flin = CaughtModel.of([disc(30.0, 600000074, 405.0), disc(40.0, 600000074, 405.0), disc(50.0, 600000074, 405.0)],
                          62.0, {30.0: 100.0, 40.0: 100.0, 50.0: 100.0})
    roll_esc = next(e for e in flin.escapes if e[0] == 'roll')
    run_esc = next(e for e in flin.escapes if e[0] == 'run')
    held = flin.held(2.5, 0, (45.0, 55.0))
    stag = CaughtModel.of([disc(30.0, 600000070, 405.0), disc(40.0, 600000074, 405.0),
                           disc(50.0, 600000074, 405.0), disc(70.0, 600000074, 405.0)],
                          62.0, {30.0: 100.0, 40.0: 100.0, 50.0: 100.0, 70.0: 100.0})
    check('a caught defender in his whiff\'s recovery: free at once he takes 1 of 3 flinching hits; held to a f45 '
          'dodge floor he takes 2 rolling out and 3 running out (move floor f55); a floor at or before the catch '
          'holds nothing; a middle stagger ends the recovery, so the f70 hit misses with both floors at f80',
          ([x[0] for x in flin.landed(2.5)[0]], [x[0] for x in flin._play(0, 2.5, 'roll', roll_esc[1], roll_esc[2], held)],
           [x[0] for x in flin._play(0, 2.5, 'run', None, run_esc[2], held)], held, flin.held(2.5, 0, (30.0, 12.0)),
           [x[0] for x in stag.landed(2.5, (80.0, 80.0))[0]]),
          ([1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [1.0, 1.0, 1.0], (45.0, 55.0), None, [1.0, 1.0, 1.0, 0.0]),
          'punish_situations (dodge and move floors); frame-advantage.md section 1 (ExecAddDamage keeps the '
          'state, W_DamageLv<n> replaces it, COMMUNITY); the TAE damage clips')
    # Caught in his own dodge (version 25): three flinching hits at f30, f42 and f45 that touch him
    # anywhere (radius 1000 m). Back-rolling from f25 (13 frames of invincibility) he is caught at f42
    # by the second, still in that roll: its dodge cancel (f21 into it, f46) holds a roll out, so the
    # f45 hit lands too (200), where the play from a standing catch at f42 rolled out at once and
    # took the second alone (100). Rolling from f16 he is caught at f30 by the first; his roll out at
    # f37 then covers f42 and f45 (100), where standing at f30 he rolled out at once and the f45 hit
    # found him past that roll's invincibility (200). `reaction_outcome` prices each catch made in
    # a dodge so (`_LazyWorth.mid`).
    def wide(frame):
        T = np.array([frame, frame + 0.5])
        return ({'T': T, 'X': np.zeros(2), 'Z': np.full(2, -2.5), 'R': np.full(2, 1000.0), 'PK': np.full(2, -math.inf),
                 'CAUGHT': {'lists': [caught_list(t, 600000074, 405.0)], 'wf': 1.0}}, frame)
    mgeo = [wide(30.0), wide(42.0), wide(45.0)]
    mcm = CaughtModel.of(mgeo, 62.0, {30.0: 100.0, 42.0: 100.0, 45.0: 100.0})
    mback = max(roll_motion(), key=lambda m: m['path'][min(len(m['path']) - 1, int(m['dodge']))][1])
    mgs = [g for g, _ in mgeo]
    mc25, mc16 = _catches(mgs, 2.5, mback, [25.0]), _catches(mgs, 2.5, mback, [16.0])
    mid_r = reaction_outcome(mgs, mcm.lazy(2.5, 0, 3), 2.5, moves=[mback])
    std_r = reaction_outcome(mgs, [mcm.worth_of(2.5, h) for h in range(3)], 2.5, moves=[mback])
    check('caught in his own dodge (version 25): back-rolling from f25 the f42 hit catches him in the roll and its '
          'dodge cancel holds him for the f45 hit (200, standing 100); rolling from f16 the f30 hit catches him and '
          'his roll out from that roll\'s cancel covers the rest (100, standing 200); reaction_outcome prices its '
          'catches so',
          ((int(mc25[0][0]), float(mc25[1][0])), (int(mc16[0][0]), float(mc16[1][0])),
           mcm.worth_of(2.5, 1), mcm.worth_of(2.5, 1, dodge=(mback, 25.0, 42.0)),
           mcm.worth_of(2.5, 0), mcm.worth_of(2.5, 0, dodge=(mback, 16.0, 30.0)),
           round(mid_r['value'], 3) != round(std_r['value'], 3)),
          ((1, 42.0), (0, 30.0), 100.0, 200.0, 200.0, 100.0, True),
          'CaughtModel dodge_key / _play; roll_motion (TAE: invincible f0-13, dodge cancel f21, move cancel f22)')
    # The pool's poises (version 21): a mix of plays, one per run of poises that play alike, the same
    # numbers as playing each poise and weighting it.
    two = [disc(30.0, 600000070, 50.0), disc(40.0, 600000070, 50.0), disc(50.0, 600000074, 405.0)]
    ppool = ((20.0, 1.0), (45.0, 2.0), (49.0, 1.0), (50.0, 1.0), (60.0, 3.0), (120.0, 3.0))
    mixed = CaughtModel.of(two, ppool, {30.0: 100.0, 40.0: 100.0, 50.0: 100.0})
    each = {p: [x[0] for x in CaughtModel.of(two, p, {30.0: 100.0, 40.0: 100.0, 50.0: 100.0}).landed(2.5)[0]]
            for p, _ in ppool}
    want = [sum(w / 11.0 * each[p][g] for p, w in ppool) for g in range(3)]
    check('a caught defender over a pool of poises lands each pool poise\'s play at its share (a poise at or '
          'under the hits\' 50 is broken, staggered and takes the second: 5 of 11; over it he leaves after the first)',
          ([round(x[0], 9) for x in mixed.landed(2.5)[0]], [round(x, 9) for x in want]),
          ([1.0, 0.454545455, 0.0], [1.0, 0.454545455, 0.0]), 'CaughtModel._best; FUN_140486bf0 (0 or below breaks)')
    # Each pool build's own health caps what its own play lands (version 21): a 20-poise build of
    # 150 HP is staggered and takes both 100 HP hits, a 120-poise one of 1000 HP takes the first:
    # 0.5 x 150 + 0.5 x 100, where the median's cap on the mixed worth (150) took 150.
    hpm = CaughtModel.of(two, [(20.0, 1.0, 150.0), (120.0, 1.0, 1000.0)], {30.0: 100.0, 40.0: 100.0, 50.0: 100.0})
    check('a caught defender\'s health caps each pool build\'s own play: 125 HP, not min(150, 150)',
          (round(hpm.worth_of(2.5, 0), 6), round(hpm.worth_of(2.5, 0, hp=150.0), 6),
           round(hp_cap(150.0, [(150.0, 0.5), (1000.0, 0.5)]), 6)),
          (150.0, 125.0, 150.0), 'CaughtModel.worth_of, hp_cap')
    # The jump (version 21): a caught defender may jump when a hit is `AIR`, and its invincibility
    # holds only against `AIR` samples (`_catches`' rule): one air and one plain sample on him at
    # f35, he jumped at f30.
    def low(frame, air):
        g, fr = disc(frame, 600000074, 405.0)
        return ({**g, 'AIR': True} if air else g), fr
    jm = CaughtModel.of([low(30.0, True), low(35.0, False)], 62.0, {30.0: 100.0, 35.0: 100.0})
    jseg = [(-math.inf, 0.0, 2.5, 'stand', None, ESCAPE_RUNS[0]), (30.0, 0.0, 2.5, 'roll', jump_motion(), ESCAPE_RUNS[0])]
    jt = jm._touch_all(jseg)
    check('a caught defender jumps only when a hit is AIR, immune in the air to AIR samples alone (the AIR one '
          'at his take-off misses, the plain one 5 frames later lands)',
          ([e[0] for e in jm.escapes].count('jump'), [e[0] for e in flin.escapes].count('jump'),
           bool(jt[jm.bounds[0][0]]), bool(jt[jm.bounds[1][0]]), jump_motion()['iframes'] > 5.0),
          (1, 0, False, True, True), '_catches: immune = not move air or geometry AIR; jump_motion')
    # Where a caught defender is at one frame (`_at1`, what `_play` reads at each hit) is `_pos` of
    # that one frame, bit for bit, on every path the plays above take (standing, held, running,
    # rolling, jumping, caught in his own dodge), each model played afresh.
    at1_, kinds_, at1_bad = CaughtModel._at1, set(), [0, 0]

    def at1_check(self, segs, T_):
        got_ = at1_(self, segs, T_)
        z_, x_, _ = self._pos(segs, [T_])
        at1_bad[0] += 1
        at1_bad[1] += int((float(x_[0]), float(z_[0])) != got_ or np.float64(got_[0]).tobytes()
                          != x_[:1].tobytes() or np.float64(got_[1]).tobytes() != z_[:1].tobytes())
        kinds_.add(segs[min(max(int(np.searchsorted(np.array([s[0] for s in segs], float), T_, side='right')) - 1, 0),
                            len(segs) - 1)][3])
        return got_
    CaughtModel._at1 = at1_check
    try:
        fresh_ = [CaughtModel.of([disc(30.0, 600000074, 405.0), disc(40.0, 600000074, 405.0),
                                  disc(50.0, 600000074, 405.0)], 62.0, {30.0: 100.0, 40.0: 100.0, 50.0: 100.0}),
                  CaughtModel.of([disc(30.0, 600000070, 405.0), disc(40.0, 600000074, 405.0), disc(50.0, 600000074, 405.0),
                                  disc(70.0, 600000074, 405.0)], 62.0,
                                 {30.0: 100.0, 40.0: 100.0, 50.0: 100.0, 70.0: 100.0}),
                  CaughtModel.of(two, ppool, {30.0: 100.0, 40.0: 100.0, 50.0: 100.0}),
                  CaughtModel.of([low(30.0, True), low(35.0, False)], 62.0, {30.0: 100.0, 35.0: 100.0})]
        for cm_ in fresh_:
            for fl_ in (None, (45.0, 55.0), (80.0, 80.0)):
                cm_.landed(2.5, fl_)
        mfresh_ = CaughtModel.of(mgeo, 62.0, {30.0: 100.0, 42.0: 100.0, 45.0: 100.0})
        mfresh_.worth_of(2.5, 1, dodge=(mback, 25.0, 42.0))
        mfresh_.worth_of(2.5, 0, dodge=(mback, 16.0, 30.0))
    finally:
        CaughtModel._at1 = at1_
    check('where a caught defender is at one frame (_at1) is _pos of that frame, bit for bit, on every kind of path '
          'his plays take',
          (at1_bad[0] > 0, at1_bad[1], sorted(kinds_)), (True, 0, ['dodge', 'hold', 'roll', 'run', 'stand']),
          'CaughtModel._at1, _pos')
    # One frame's tie in one hit priority record goes to the lower priority, then the attack made
    # first, in `_catches` as in `CaughtModel._play` (version 21: the list order took it).
    def tied(prio, created):
        return {'T': np.array([30.0]), 'X': np.zeros(1), 'Z': np.full(1, -2.5), 'R': np.full(1, 1.0),
                'PK': np.full(1, -math.inf), 'HPR': (('rec', 0, 0), prio, created)}
    check('a same-frame tie in one hit priority record: the lower priority catches, then the attack created first',
          (int(_catches([tied(1, 0), tied(0, 1)], 2.5, None, [0.0])[0][0]),
           int(_catches([tied(0, 1), tied(0, 0)], 2.5, None, [0.0])[0][0]),
           int(_catches([tied(0, 0), {**tied(0, 1), 'HPR': (('other', 0, 0), 0, 1)}], 2.5, None, [0.0])[0][0])),
          (1, 1, 0), 'FUN_14051b210, DmgMan list order (FUN_140527d40)')
    # The worth of a catch depends on when it is made (bd er-effects-rs-fse5): the same three bursts
    # as a chain, each burst's samples starting on the frame the last one's end (as fly_tree flies
    # Bullet 2872 -> 2873 -> 2874, `MEASURED` 2026-11-16: f52.83-60.33, 60.33-67.83, 67.83-75.33), in
    # a 4 m spot so a medium roll (13 frames of invincibility) ends inside it. A roll whose
    # invincibility covers burst 1 is first touched by burst 2; the whole geometry priced that catch
    # as burst 1's standing catch, all three bursts.
    T3c = np.concatenate([np.arange(52.0 + 7.5 * k, 59.75 + 7.5 * k, 0.5) for k in range(3)])
    nc = len(T3c)
    wide = {'T': T3c, 'X': np.zeros(nc), 'Z': np.full(nc, -4.5), 'R': np.full(nc, 4.0), 'PK': np.full(nc, -math.inf),
            'B': np.repeat([2872, 2873, 2874], nc // 3),
            'CAUGHT': {'lists': [caught_list(t, 301412902 + k, 202.5, 1, 0.0, 1 / 3, (2872 + k,)) for k in range(3)],
                       'wf': 1.0}}
    chain = split_chains(t, [(wide, 52.0)])
    cw = CaughtModel.of([(wide, 52.0)], 62.0, {52.0: 939.3})
    cs = CaughtModel.of(chain, 62.0, {52.0: 939.3})
    sgrid = np.arange(0.0, 76.0, DODGE_STEP)
    by2, same = [], True
    for mv in roll_motion():
        hs, ts = _catches([g for g, _ in chain], 4.5, mv, sgrid)
        _, tw = _catches([wide], 4.5, mv, sgrid)
        same = same and bool(np.array_equal(ts, tw))
        by2.append((int((hs == 1).sum()), float(sgrid[hs == 1].min()), float(ts[hs == 1].min())))
    check('a bullet chain is one geometry per list (split_chains): Rosus\'s Summons\' bursts as three parts at one '
          'frame; a defender who stands is played as before (all three land, 939.3); every dodge start is touched '
          'on the frame it was; in each medium roll the starts f46.5-52 escape burst 1 and are first touched by '
          'burst 2 (the f59.5 the bursts share is burst 2\'s) and are credited bursts 2-3 (626.2), where the whole '
          'geometry credited all three',
          ([(g['CHAIN'][:3], len(g['T']), g['CAUGHT']['wf']) for g, _ in chain], cs.row(4.5, 0),
           round(cs.worth_of(4.5, 0), 1), same, by2, cs.row(4.5, 1), round(cs.worth_of(4.5, 1), 1),
           round(cw.worth_of(4.5, 0), 1)),
          ([((0, 3, 52.0), 16, 1.0), ((1, 3, 59.5), 16, 1.0), ((2, 3, 67.0), 16, 1.0)], [[1.0], [1.0], [1.0]],
           939.3, True, [(12, 46.5, 59.5)] * 4, [[0.0], [1.0], [1.0]], 626.2, 939.3),
          'Bullet 2872-2874 HitBulletID at expiry (REGULATION); roll_motion (TAE); bd er-effects-rs-fse5')
    # A chain part's homing shots are every shot of its rows in each flight (2026-11-19, `_part_keys`),
    # not the lineage keys of the flight with no target: each flight's interval children are its own
    # (`fly_members`), and past `BULLET_INTERVAL_CAP` a flight that makes another number of them takes
    # other slots. No tree of the regulation reaches it today (no chain-split list set with a `HOMING`
    # record has an interval row over the cap, `MEASURED` 2026-11-19, target/wf/splitcap/reach.py and
    # reach2.py), so the tree is made here: Bullet 4980812 (a carrier: it dies on him and makes its
    # child there) with its 0.11-0.25 s timer set to 0.03 s and one shot, its children 4980813 and
    # their 4980814, under ids of their own. With no one there it flies its whole second and makes
    # 32 children (`interval_frames`: the k-th on move ceil(1.8 k), while that is under its 60), of
    # which 12 are flown; against a roll it dies on him sooner or later and makes
    # another number, so 12 other slots.
    class _OwnRows:
        """`t` with Bullet rows of its own, under a regulation of its own (`_homing_entries` finds
        the tables by it)."""

        def __init__(self, base, bullet):
            self._base, self.bullet, self.regulation = base, bullet, ('selftest chain cap', base.regulation)

        def __getattr__(self, k):
            return getattr(self._base, k)
    syn = {4980812: 990000012, 4980813: 990000013, 4980814: 990000014}
    srows = dict(t.bullet)
    for old, new in syn.items():
        r = dict(t.bullet[old])
        for f in ('HitBulletID', 'intervalCreateBulletId'):
            r[f] = syn.get(r[f], r[f])
        if old == 4980812:
            r.update(intervalCreateTimeMin=0.03, intervalCreateTimeMax=0.03, numShoot=1)
        srows[new] = r
    ts = _OwnRows(t, srows)
    sbt = bullet_tree(ts, 990000012)
    sspec = homing_spec(ts, sbt, 0.0, None, lambda nd: nd['bullet'] in (990000013, 990000014))
    nt = [e['key'] for e in fly_members(ts, sbt, 1, 0.0, None, None, cap=None) if e['key'][-1][1] == 'i']
    T2 = np.concatenate([np.arange(20.0, 30.5, 0.5), np.arange(30.0, 40.5, 0.5)])
    n21 = 21
    sg = {'T': T2, 'X': np.zeros(len(T2)), 'Z': np.full(len(T2), -3.0), 'R': np.full(len(T2), 0.5),
          'PK': np.full(len(T2), -math.inf), 'B': np.array([990000013] * n21 + [990000014] * (len(T2) - n21)),
          'HOMING': sspec,
          'CAUGHT': {'lists': [caught_list(t, 4980630, 10.0, 1, 0.0, 0.5, (b,)) for b in (990000013, 990000014)],
                     'wf': 1.0}}
    sparts = split_chains(ts, [(sg, 20.0)])
    whole = sspec['parts'][0] if sspec else None
    noaim = fly_shots(ts, sbt)
    sstarts = np.arange(0.0, 60.0, DODGE_STEP)
    by_index = by_part = finite = 0
    for dist in (3.07, 4.57) if whole is not None else ():
        for j, mv in enumerate(roll_motion()):
            tts = _homing_entries(whole, sspec['reg'], dist, roll_motion(), False, sstarts)[j]
            for (p, _), b in zip(sparts, (990000013, 990000014)):
                want = np.full(len(sstarts), math.inf)
                old = np.full(len(sstarts), math.inf)
                keys = {k for e, k in zip(noaim, noaim.keys) if e[0]['bullet'] == b}
                for k, v in tts.items():
                    if k[-1][0] == b:
                        want = np.minimum(want, v)
                        if k in keys:
                            old = np.minimum(old, v)
                finite += int(np.isfinite(want).sum())
                by_index += int((old != want).sum())
                by_part += int(('HOMING' in p) and not np.array_equal(_homing_touch(p, dist, mv, sstarts), want))
    check('a chain part flown again after a dodge takes every shot of its rows that flight makes: a carrier '
          f'with a 0.03 s timer makes 32 children with no one there, and against a roll another number, so '
          f'the no-target flight\'s keys miss children a dodge flight makes (the old reading: {by_index} of '
          f'{2 * 4 * 2 * len(sstarts)} dodge starts read another touch) and the parts read the whole flight\'s '
          f'touches by their rows',
          (sbt is not None and t.bullet.get(990000012) is None, len(nt), [('HOMING' in p, p.get('CHAIN', (0, 0))[:2])
                                                                       for p, _ in sparts],
           finite > 0, by_index > 0, by_part),
          (True, 32, [(True, (0, 2)), (True, (1, 2))], True, True, 0),
          'fly_members (each flight\'s interval children its own, BULLET_INTERVAL_CAP); _chain_parts, _part_keys')
    # `_homing_many` (each move flown from its own starts, no shot made for a defender already
    # touched: `least`) against `_homing_touch` of each read alone, on the chain's parts, moves
    # carried on a roll (each start set its own) and one not.
    trk_ = track_extend((), roll_motion()[1], 8.0)
    reqs_ = [(carried(mv, trk_), np.arange(18.0, 40.0, DODGE_STEP)) for mv in roll_motion()] + \
        [(carried(roll_motion()[0], trk_), np.arange(30.0, 52.0, DODGE_STEP)),
         (roll_motion()[2], np.arange(10.0, 20.0, DODGE_STEP))]
    many_n = many_bad = many_fin = 0
    for dist in (3.07, 4.57):
        for p, _ in sparts:
            if 'HOMING' not in p:
                continue
            for (mv, s_), gv in zip(reqs_, _homing_many(p, dist, reqs_)):
                wv = _homing_touch(p, dist, mv, s_)
                many_n += 1
                many_fin += int(np.isfinite(wv).sum())
                many_bad += int(np.asarray(gv, float).tobytes() != np.asarray(wv, float).tobytes())
    check('the homing shots flown ahead together (_homing_many: each move from its own starts, no child '
          'made for a defender a counted shot touched before it) give every read\'s touches as it flies alone',
          (many_n, many_fin > 0, many_bad), (2 * 2 * len(reqs_), True, 0),
          '_homing_many, _homing_entries (least, starts per move), fly_members bound')
    # A follow-up pressed late (bd er-effects-rs-me5p). Samples that touch wherever he is (radius
    # 1000 m): the opening at f40-41, which he rolls from about f34 (the middle of the starts
    # f28-40 that escape it); the follow-up 1-2 frames after a press whose window opens at f43.
    def touch(frames, radius=1e3):
        return contact_geometry(bullet_points=[(f / TAE_FPS, 0.0, 0.0, radius) for f in frames])
    opening, follow = touch([40.0, 40.5, 41.0]), touch([44.0, 44.5, 45.0])
    delays = [float(d) for d in range(16)]
    fm = FollowupModel([(opening, 0)], [(follow, 1)], 43.0, delays, my_roll=80.0)
    k0 = 0
    rolled = fm._rolled(2.5, k0, roll_motion()[k0], [34.0, 34.0 - 4.0])
    check('a roll from f34 (i-frames to f47, dodge cancel f55): the follow-up pressed at f43 lands at f44-45 '
          'inside the i-frames and misses; pressed 4 frames later it lands at f48-49 and catches him',
          tuple(int(x) for x in rolled), (-1, 0), 'FollowupModel._rolled: _catches on the roll path, dodge cancel')

    def worth(d, idx):
        return np.array([2.0, 1.0])[idx]
    early, late = fm.outcome(2.5, 0.0, worth, see=False), fm.outcome(2.5, 0.0, worth)
    check('the attacker who saw the roll before the window opened presses late and catches most rollers; '
          'pressed at once the follow-up catches only the earliest ones; a defender who stays meets the '
          'press at once either way',
          (early['value'] < 0.25, late['value'] > early['value'] + 0.4, late['delayed'] > 0.4,
           late['value'] - late['punish_hp'] >= early['value'] - early['punish_hp'], late['base'] == early['base'] == 2.0),
          (True, True, True, True, True), 'FollowupModel.outcome, attacker_reactions')
    # A follow-up that catches him in his roll of the opening is its own play (version 25, `_mid`):
    # priced by `mid_worth`; one column per (start, catch) on the earliest press's timeline when the
    # opening is over by then (here every such catch is, the opening ending at f41); priced as the
    # geometry's own worth it gives the outcome without plays exactly.
    fmm = FollowupModel([(opening, 0)], [(follow, 1)], 43.0, delays, my_roll=80.0)
    mo0 = fmm.outcome(2.5, 0.0, worth)
    mo1 = fmm.outcome(2.5, 0.0, worth, mid_worth=lambda d, h, mv, s, tc: float(worth(d, [h])[0]))
    mo7 = fmm.outcome(2.5, 0.0, worth, mid_worth=lambda d, h, mv, s, tc: 7.0)
    check('a follow-up that catches him in his roll of the opening is a play of its own: every one a column kept '
          'once for every delay (the opening is over first), priced by mid_worth, and the outcome without them '
          'when priced as the follow-up geometry\'s own worth',
          (len(fmm.plays) > 0, all(p[0] == 'follow' and p[5] is None for p in fmm.plays),
           round(mo1['value'] - mo0['value'], 12), mo7['value'] > mo0['value']), (True, True, 0.0, True),
          'FollowupModel._mid, _worth_table')
    blind = FollowupModel([(opening, 0)], [(follow, 1)], 35.0, delays, my_roll=80.0)
    away = FollowupModel([(touch([40.0, 40.5, 41.0], 1.0), 0)], [(follow, 1)], 43.0, delays, my_roll=80.0)
    check('no roll seen before the window opens (it opens at f35, his roll shows from about f37), or no '
          'roll at all (the opening never reaches him): the press stays at once',
          tuple(round(m.outcome(2.5, 0.0, worth)['value'] - m.outcome(2.5, 0.0, worth, see=False)['value'], 12)
                for m in (blind, away)) + (round(away.outcome(2.5, 0.0, worth)['delayed'], 12),),
          (0.0, 0.0, 0.0), 'FollowupModel: attacker_reactions, no opening burst reaching him')
    # The stand row sees the caught defender leave (version 25). An opening flinch at f20 and a
    # follow-up 1 frame after a press whose window opens at f43, both touching him anywhere; his
    # whiff's recovery holds him to f40. Caught standing he rolls out at f40 (invincible to f53):
    # pressed 0-8 frames late the follow-up finds him invincible, from 9 on it catches him. An
    # attacker whose column is a delay of 3 to 8 sees the roll start about 5-10 frames after f40 and
    # presses late enough for it on the share of his reactions that see it in time; column 0 cannot
    # wait, and from 9 the column's own press already catches him.
    def wide_(frames):
        T_ = np.array(frames, float)
        return {'T': T_, 'X': np.zeros(len(T_)), 'Z': np.full(len(T_), -2.5), 'R': np.full(len(T_), 1000.0),
                'PK': np.full(len(T_), -math.inf), 'CAUGHT': {'lists': [caught_list(t, 600000074, 405.0)], 'wf': 1.0}}
    sop, sfo = wide_([20.0, 20.5]), wide_([44.0, 44.5])
    sfm = FollowupModel([(sop, 0)], [(sfo, 1)], 43.0, delays, my_roll=80.0)
    scm = {d: CaughtModel.of([(sop, 20.0), (shift_geometry(sfo, float(d)), 44.0 + d)], 62.0,
                             {20.0: 100.0, 44.0 + d: 100.0}) for d in range(len(delays))}

    def sworth(d, idx):
        return np.array([scm[d].worth_of(2.5, int(h), floors=(40.0, 40.0)) for h in idx])
    sm0 = sfm.mixup(2.5, 0.0, sworth)
    sm1 = sfm.mixup(2.5, 0.0, sworth, stand_escapes=lambda d: scm[d].escape_table(2.5, 0, floors=(40.0, 40.0)))
    srow0, srow1 = sm0['M'][sm0['rows'].index('stand')], sm1['M'][sm1['rows'].index('stand')]
    TE_, S_, C_ = scm[0].escape_table(2.5, 0, floors=(40.0, 40.0))
    check('the stand row\'s attacker sees the caught defender roll out (f40) as he sees a roll: at columns 3-8 he '
          'presses late enough on the reactions that see it in time, so more lands than at the column\'s own delay; '
          'column 0 cannot wait and from 9 the column already catches him; with no reaction in time the row is '
          'worth_of exactly (escape_table)',
          ([round(x, 1) for x in srow0], [round(x, 1) for x in srow1],
           float(C_[0, int(np.argmin(S_[0]))]) == sworth(0, [0])[0]),
          ([100.0] * 9 + [200.0] * 7, [100.0, 100.0, 100.0, 111.1, 133.3, 166.7, 177.8, 188.9, 200.0] + [200.0] * 7,
           True), 'FollowupModel._stand_values, CaughtModel.escape_table, attacker_reactions')
    # The touch table (`_TouchTable`) gives `_first_touch`'s reads exactly: two geometries, one with
    # launch times and a hold-back peak, the other melee-held, most samples out of reach.
    rng = np.random.default_rng(7)
    m_ = 400
    gA = {'T': np.sort(rng.uniform(0, 60, m_)), 'X': rng.uniform(-6, 6, m_), 'Z': rng.uniform(-12, 3, m_),
          'R': rng.uniform(0.1, 1.2, m_), 'PK': np.where(rng.random(m_) < 0.5, -math.inf, rng.uniform(0, 1.5, m_))}
    gA['TL'] = gA['T'] - rng.uniform(0, 8, m_)
    gB = {'T': np.sort(rng.uniform(10, 50, m_)), 'X': rng.uniform(-4, 4, m_), 'Z': rng.uniform(-9, 1, m_),
          'R': rng.uniform(0.1, 1.0, m_), 'PK': rng.uniform(-1, 1, m_)}
    starts = np.arange(-10.0, 70.0, DODGE_STEP)
    cuts = np.where(rng.random(len(starts)) < 0.5, -math.inf, rng.uniform(0, 60, len(starts)))
    same, touched = True, 0
    for dist_ in (2.0, 4.5):
        stand_ = _TouchTable([gA, None, gB], dist_, None)
        for mv in roll_motion():
            tab = _TouchTable([gA, None, gB], dist_, mv, -10.0, 61.0, stand_)
            got_, want_ = tab.first(starts, cuts), _first_touch([gA, None, gB], dist_, mv, starts, cuts)
            same = same and np.array_equal(got_[0], want_[0]) and np.array_equal(got_[1], want_[1])
            touched += int((want_[0] >= 0).sum())
        same = same and np.array_equal(stand_.first([0.0], [-math.inf])[0], _catches([gA, None, gB], dist_, None,
                                                                                   [0.0])[0])
    check('the touch table reads what _first_touch reads, start by start (cuts, starts before and past its grid, '
          'samples out of reach left out)', (same, touched > 100), (True, True), '_TouchTable against _first_touch')
    # A table whose rows are read as asked (`_RowsTable`) answers as the whole table: lone rows,
    # runs, the grid's last row, starts past it (the standing table) and every start at once.
    same_r, made = True, []
    for dist_ in (2.0, 4.5):
        stand_ = _TouchTable([gA, None, gB], dist_, None)
        for mv in roll_motion():
            whole_ = _TouchTable([gA, None, gB], dist_, mv, -10.0, 61.0, stand_)
            rows_ = _RowsTable([gA, None, gB], dist_, mv, -10.0, 61.0, lambda s=stand_: made.append(1) or s)
            for pick in (np.arange(0, len(starts), 17), np.arange(5, 9), np.array([142]), np.array([150, 3]),
                         np.arange(len(starts))):
                a_, b_ = rows_.first(starts[pick], cuts[pick]), whole_.first(starts[pick], cuts[pick])
                same_r = same_r and np.array_equal(a_[0], b_[0]) and np.array_equal(a_[1], b_[1])
    check('a touch table read row by row as asked (_RowsTable) answers as the whole table',
          (same_r, len(made)), (True, 8), '_RowsTable against _TouchTable')
    # With no follow-up the model is `sequence_outcome` on the opening, punish included.
    two = [(touch([40.0, 40.5, 41.0]), 0), (touch(np.arange(70.0, 86.0, 0.5)), 1)]
    alone = FollowupModel(two, [], 100.0, [0.0], my_roll=90.0, strikes=[15.0])
    seq = sequence_outcome([([g], [3.0 - i]) for g, i in two], 2.5, my_roll=90.0, strikes=[15.0], run=True)
    got = alone.outcome(2.5, 0.0, lambda d, idx: np.array([3.0, 2.0])[idx])
    check('an opening with no follow-up scores as sequence_outcome does (value, evade, punish)',
          tuple(round(got[k] - seq[k], 9) for k in ('value', 'p_evade', 'p_punish', 'base')), (0.0, 0.0, 0.0, 0.0),
          'FollowupModel against sequence_outcome')
    # The mixup (bd er-effects-rs-me5p; the user, 2026-11-16, "what is called a 50/50"): the game's
    # value, solved exactly. Known games first, then the equilibrium conditions on a random one: the
    # attacker's mix earns at least the value against every row, the defender's concedes at most
    # the value to every column.
    games = [(np.array([[1.0, -1.0], [-1.0, 1.0]]), 0.0), (np.array([[0, 1, -1], [-1, 0, 1], [1, -1, 0]], float), 0.0),
             (np.array([[3.0, 1.0], [2.0, 0.0]]), 2.0)]
    solved = [zero_sum_game(M_) for M_, _ in games]
    R_ = np.random.default_rng(11).normal(size=(4, 9))
    v_, x_, y_ = zero_sum_game(R_)
    check('zero_sum_game: matching pennies 0 at (1/2, 1/2), rock-paper-scissors 0 uniform, a dominated row '
          'and column 2 in pure strategies; on a random 4 x 9 game each mix holds the value against every '
          'pure reply and both mixes are distributions',
          ([round(v, 9) for v, _, _ in solved], [round(float(z), 9) for z in solved[0][1]],
           [round(float(z), 9) for z in solved[1][2]], [round(float(z), 9) for z in solved[2][1]],
           bool((R_ @ y_ >= v_ - 1e-9).all() and (x_ @ R_ <= v_ + 1e-9).all()),
           round(float(x_.sum()), 9), round(float(y_.sum()), 9), bool((x_ >= -1e-12).all() and (y_ >= -1e-12).all())),
          ([0.0, 0.0, 2.0], [0.5, 0.5], [round(1 / 3, 9)] * 3, [0.0, 1.0], True, 1.0, 1.0,
           True), 'zero_sum_game: LP duality')

    def worth2(d, idx):
        return np.array([0.5, 1.0])[idx]
    mix = fm.mixup(2.5, 0.0, worth2, open_worth=lambda idx: np.array([0.3, 0.0])[idx], open_roll=60.0)
    once = fm.outcome(2.5, 0.0, worth2)
    M_ = mix['M']
    check('FollowupModel.mixup: its fixed sequence is outcome\'s scored net; the rows are the roll and standing '
          'to take the opening, the columns no follow-up and the press policy at every default delay; the '
          'solution holds its value against every pure reply',
          (round(mix['fixed'] - (once['value'] - once['punish_hp']), 9), mix['rows'][-1], mix['cols'][:2], len(mix['cols']),
           bool((M_ @ mix['y'] >= mix['value'] - 1e-9).all() and (mix['x'] @ M_ <= mix['value'] + 1e-9).all())),
          (0.0, 'stand', ['none', 0.0], 1 + len(delays), True), 'FollowupModel.mixup, zero_sum_game')

    # Version 22: the defender picks his roll's direction (`dodge_families`). A long live line
    # straight down his axis (late, so no i-frames cover it) catches a forward or back roll and
    # misses a side roll: knowing the attack he rolls aside and evades it all, where the four
    # directions pressed a quarter each evaded half.
    fams = dodge_families()
    TT, ZZ = np.meshgrid(np.arange(20.0, 110.0, 1.0), np.arange(-9.0, 0.01, 0.25), indexing='ij')
    line = {'T': TT.ravel(), 'X': np.zeros(TT.size), 'Z': ZZ.ravel(), 'R': np.full(TT.size, 0.5),
            'PK': np.full(TT.size, -math.inf)}
    picked = sequence_outcome([([line], [1.0])], 2.5)
    blend = sequence_outcome([([line], [1.0])], 2.5, families=[roll_motion()])
    check('dodge_families: each roll direction its own family (four), the jump a fifth with AIR; against a '
          'line down his axis he rolls aside and evades it all, the old blend of the four evaded half',
          (len(fams), [len(f) for f in fams], len(dodge_families(True)), round(picked['p_evade'], 3),
           round(blend['p_evade'], 3), picked['moves'] in {m['anim'] for m in roll_motion()}),
          (4, [1, 1, 1, 1], 5, 1.0, 0.5, True), 'dodge_families, sequence_outcome (MEASURED 2026-11-16)')
    # Version 22: one rule for where the attacker ends (`attack_advance`, `advance_at`): his root
    # motion, stopped 2 x PUSH_CAPSULE_RADIUS short of the defender as every hit is held back.
    push2 = 2 * _pose()[1].PUSH_CAPSULE_RADIUS
    adv_g = {'T': np.array([30.0, 40.0]), 'X': np.zeros(2), 'Z': np.full(2, -99.0), 'R': np.full(2, 0.1),
             'PK': np.array([2.0, 5.0]), 'ADV': (6.0, 4.0)}
    back = next(m for m in roll_motion() if m['path'][-1][1] > 1.0)
    check('attack_advance / advance_at: the larger of PK and the clip\'s ADV peak, ending where the last clip '
          'ends; up to a frame, PK alone; stopped 2 x push short of him (Savage Claws\' 8.93 m at 2 m is 1.2 m, '
          'not past him); a back roll at 6 m is in R1 reach of an attacker carried 7 m and must run in to one '
          'who stood',
          (attack_advance([adv_g, None]), attack_advance([adv_g], upto=35.0),
           round(advance_at((8.93, 8.93), 2.0), 6), round(advance_at((6.0, 4.0), 10.0), 6),
           round(advance_at((6.0, 4.0), 3.0), 6), punish_lead(back, 6.0, (7.0, 7.0))[1] == 0.0,
           punish_lead(back, 6.0, 0.0)[1] > 0.0),
          ((6.0, 4.0), (2.0, 2.0), round(2.0 - push2, 6), 4.0, round(4.0 - (6.0 - (3.0 - push2)), 6), True, True),
          'attack_advance, advance_at, punish_lead (er-mechanics-reach.front_contact_times rule)')
    _selftest_v26(check, t)
    print(f'{passed} passed, {failed} failed, {skipped} skipped')
    return 1 if failed else 0


def _selftest_v26(check, t):
    """Version 26's checks: the roll he escaped with carries him into the next burst (place, floor,
    facing), in `sequence_outcome`, `volley_outcome` and `FollowupModel`; a follow-up catch made
    while the opening could still touch him is a play; a roll row's press the opening caught meets
    the stand row's rule; the exchange model's dodge aims for the punish. Bursts are bullet-style
    samples (the defender at Z = -distance, `contact_geometry`), PK -inf (no hold-back)."""
    import numpy as np

    def disc_(frames, x, z, r):
        T_ = np.asarray(frames, float)
        return {'T': T_, 'X': np.full(len(T_), float(x)), 'Z': np.full(len(T_), -float(z)), 'R': np.full(len(T_), float(r)),
                'PK': np.full(len(T_), -math.inf)}
    rolls = roll_motion()
    back = next(m for m in rolls if m['path'][-1][1] > 1.0)
    side = next(m for m in rolls if m['path'][-1][0] > 1.0)
    bz21, sx21 = back['path'][21][1], side['path'][21][0]
    # Place. A 3-frame burst over everything at f40-42 he can only roll through (presses f29.5-40:
    # i-frames f0-13), then a 60-frame burst of radius 0.5 on the spot he started from. A back roll
    # leaves him bz21 = 3.709 m back on the first whole frame of its dodge cancel (f21; the cancel
    # opens at f20.99999): 3.709 > 0.5, so the second burst never touches him and nothing lands; on the
    # spot (version 25 put him back there) it touches at f100.
    b1 = disc_([40.0, 41.0, 42.0], 0.0, 2.5, 1e3)
    b2 = disc_(np.arange(100.0, 160.0, 1.0), 0.0, 2.5, 0.5)
    pa = sequence_outcome([([b1], [10.0]), ([b2], [100.0])], 2.5, families=[[back]])
    spot = _catches([b2], 2.5, None, [0.0])
    check('v26 place: a back roll through a burst leaves him 3.709 m back (its dodge cancel, f21); a 0.5 m burst on '
          'the spot he started from then never touches him (version 25 put him back on it, touched at f100)',
          (round(bz21, 3), round(pa['value'], 6), round(pa['p_evade'], 6), int(spot[0][0]), float(spot[1][0])),
          (3.709, 0.0, 1.0, 0, 100.0), 'dodge_chain, track_extend, roll_motion (TAE)')
    # Floor. The first press pinned at f30 (a plan): i-frames to f43 cover f40-42; his next dodge comes no
    # sooner than f30 + 21 = f51 (its dodge cancel, 20.99999 read on the 0.5 grid). A burst over everything
    # at f50.5-52 finds him in that roll (vulnerable from f43) at f50.5: caught in his roll, never pressed.
    # Version 25 freed him at the burst's end plus his recovery past the i-frames, f42 + 8 = f50, and he
    # rolled through it.
    def pin(moves, oks, cue, floor, **_kw):
        return [[(30.0, 1.0)] for _ in moves]
    bf = disc_([50.5, 51.0, 51.5, 52.0], 0.0, 2.5, 1e3)
    chf = dodge_chain([[b1], [bf]], 2.5, [back], plan=pin)
    ev = chf['events']
    check('v26 floor: escaping f40-42 with a roll from f30, his next dodge waits for f51 (start + dodge cancel); a '
          'burst at f50.5 catches him in that roll (version 25 freed him at f50 and he rolled it)',
          (round(sum(w for *_, w in ev), 6), sorted({(bi, h, p[0]['anim'], p[1], p[2]) for bi, h, p, _ in ev}),
           round(chf['p_evade'], 6), round(30.0 + dodge_end(back), 4)),
          (1.0, [(1, 0, back['anim'], 30.0, 50.5)], 0.0, 51.0),
          'dodge_chain: floor = start + dodge_end; still_move; dodge_end f20.99999 (TAE)')
    _selftest_v27(check, t, disc_, b1)
    # Volley. The same place rule: threat 'a' over everything at f40-42, threat 'b' the 0.5 m burst on the
    # spot; a back roll escapes 'a' and is 3.709 m from 'b': nothing lands; both land on a defender who
    # never dodges (base 101).
    vo2 = volley_outcome([([b1], ['a']), ([b2], ['b'])], {'a': 1.0, 'b': 100.0}, 2.5, families=[[back]])
    check('v26 volley: the back roll that escapes threat a carries him 3.709 m from threat b on the spot: nothing '
          'lands; standing he takes both',
          (round(vo2['value'], 6), round(vo2['base'], 6), round(vo2['p_evade'], 6)), (0.0, 101.0, 1.0),
          'volley_outcome, _volley_burst track')
    # Follow-up. The opening over everything at f40-41 (rolled through), the follow-up a 0.5 m burst on the
    # spot from f70 to f130, pressed at f43, delay 0 only: every roll direction leaves him off that spot
    # (version 27, the whole clip: back 3.71 m sliding to 3.17, forward 3.27 sliding to 3.65, aside 3.42
    # sliding to 4.41), so nothing of it lands; version 25 met it back on the spot (60 frames he cannot
    # roll), where it lands.
    fo = disc_(np.arange(70.0, 130.0, 1.0), 0.0, 2.5, 0.5)
    op = disc_([40.0, 40.5, 41.0], 0.0, 2.5, 1e3)
    fmv = FollowupModel([(op, 0)], [(fo, 1)], 43.0, [0.0], my_roll=200.0)

    def worth2(d, idx):
        return np.array([2.0, 1.0])[idx]
    fov = fmv.outcome(2.5, 0.0, worth2)
    check('v26 follow-up: the roll that escapes the opening carries him off the spot the follow-up lays (0.5 m, '
          'f70-130); nothing of it lands where version 25 put him back on it',
          (round(fov['value'], 6), round(fov['p_evade'], 6), fov['base'], int(_catches([fo], 2.5, None, [0.0])[0][0])),
          (0.0, 1.0, 2.0, 0), 'FollowupModel._shape / _stage / _late with the settled track')
    # A carried move's touch table read for a start past its grid reads him still on his track (he
    # never starts that move; `_StillTable`): settled 0.2 m behind the spot, inside the 0.5 m follow-up
    # from f70, both reads give (0, f70).
    trk_r = ((TRACK_EVER, 0.0, 0.2, STILL),)
    far_ = fmv._tab(2.5, carried(back, trk_r)).first([500.0], [-math.inf])
    still_ = fmv._follow_still(2.5, 0, -math.inf, trk_r)
    check('v26: a carried move\'s table past its grid reads the still track (0.2 m behind the spot: caught at f70)',
          ((int(far_[0][0]), float(far_[1][0])), (int(still_[0][0]), float(still_[1][0]))), ((0, 70.0), (0, 70.0)),
          'FollowupModel._tab / _stand_tab / _StillTable')
    # A follow-up catch while the opening could still touch him is a play (version 26). The opening never
    # reaches him (a sample 1000 m aside at f40 and f200: it is not over until f200); the follow-up touches
    # everywhere at f44 and again at f70 (one burst): he rolls through f44 and f70 catches him in that roll,
    # before f200. Version 25 weighed it on the follow-up geometry (it was not over); now it is a play of
    # that delay (`_mid` 'follow', delay 0), priced by mid_worth: 7 a unit of weight against the geometry's 1.
    opf = disc_([40.0, 200.0], 1000.0, 2.5, 0.5)
    fof = disc_([44.0, 70.0], 0.0, 2.5, 1e3)
    fmp = FollowupModel([(opf, 0)], [(fof, 1)], 43.0, [0.0], my_roll=200.0)
    pv1 = fmp.outcome(2.5, 0.0, worth2, mid_worth=lambda d, h, mv, s, tc: 1.0)
    pv7 = fmp.outcome(2.5, 0.0, worth2, mid_worth=lambda d, h, mv, s, tc: 7.0)
    plays_f = [p for p in fmp.plays if p[0] == 'follow']
    check('v26 item 2: a follow-up catch made before the opening is over is a play of that delay (here f70 < f200), '
          'priced by mid_worth (7 against 1 a unit of weight), not the geometry\'s worth',
          (len(plays_f) > 0, all(p[5] == 0 for p in plays_f), pv1['value'] > 0, round(pv7['value'] / pv1['value'], 9)),
          (True, True, True, 7.0), 'FollowupModel._stage raw plays, _resolve, _mid')
    # A roll row's press the opening caught (version 26). The stand row's setup: an opening flinch at
    # f20-20.5 over everything, the follow-up at f44 a frame after a press whose window opens at f43, his
    # whiff's recovery holding a caught defender to f40 (he rolls out there). With his dodge floor at f30
    # every roll press (at f30) comes after the opening caught him: every roll row is the stand row's
    # catch, so with the caught defender's escape (`catch_escapes`) each roll row is the stand row (100
    # to f2, 111.1, 133.3, 166.7, 177.8, 188.9, then 200). Version 25 had the attacker read the press at
    # f30 as a roll he saw before the window opened (f30 + his reaction < f43) and press at f9, where
    # the follow-up catches the roll-out at f40: 200 at every column, for a roll that never happened.
    def wide2(frames):
        T_ = np.array(frames, float)
        return {'T': T_, 'X': np.zeros(len(T_)), 'Z': np.full(len(T_), -2.5), 'R': np.full(len(T_), 1000.0),
                'PK': np.full(len(T_), -math.inf), 'CAUGHT': {'lists': [caught_list(t, 600000074, 405.0)], 'wf': 1.0}}
    delays = [float(d) for d in range(16)]
    sop, sfo = wide2([20.0, 20.5]), wide2([44.0, 44.5])
    sfm = FollowupModel([(sop, 0)], [(sfo, 1)], 43.0, delays, my_roll=80.0)
    scm = {d: CaughtModel.of([(sop, 20.0), (shift_geometry(sfo, float(d)), 44.0 + d)], 62.0,
                             {20.0: 100.0, 44.0 + d: 100.0}) for d in range(len(delays))}

    def sworth(d, idx):
        return np.array([scm[d].worth_of(2.5, int(h), floors=(40.0, 40.0)) for h in idx])

    def esc(col):
        return (lambda d: scm[d].escape_table(2.5, 0, floors=(40.0, 40.0))) if col == 0 else None
    rm0 = sfm.mixup(2.5, 30.0, sworth, stand_escapes=esc(0))
    rm1 = sfm.mixup(2.5, 30.0, sworth, stand_escapes=esc(0), catch_escapes=esc)
    rows0 = {r: [round(x, 1) for x in rm0['M'][i]] for i, r in enumerate(rm0['rows'])}
    rows1 = {r: [round(x, 1) for x in rm1['M'][i]] for i, r in enumerate(rm1['rows'])}
    stand_row = [100.0, 100.0, 100.0, 111.1, 133.3, 166.7, 177.8, 188.9, 200.0] + [200.0] * 7
    check('v26 item 3: a roll row whose press the opening caught meets the stand row\'s rule (the attacker presses '
          'for the caught defender\'s escape): each roll row is the stand row; version 25 priced it at the column\'s '
          'delay',
          (rows1['stand'], sorted({tuple(v) for r, v in rows1.items() if r.startswith('roll:')}),
           sorted({tuple(v) for r, v in rows0.items() if r.startswith('roll:')})),
          (stand_row, [tuple(stand_row)], [tuple([200.0] * 16)]),
          'FollowupModel._CaughtRows, _stand_values, CaughtModel.escape_table')
    # The exchange model's dodge aims for its punish (version 26). An opener at f40-42 and a chained hit at
    # f49 (gap 9), both in reach everywhere: a roll escapes the opener from f29.5 to f40 (i-frames 13) and
    # both only from f36.5 (36.5 + 13 > 49), and his punish (R1 at f20, strike 18, the string ends at f200)
    # needs both missed. Aimed for escape alone (version 25) he presses at the earliest aim that escapes
    # the opener every time, f29.5-33.6, and never punishes; aimed for what each press is worth (1 for the
    # opener, 1 for the punish) he presses where that is most, which a scan of every aim 0.01 frame apart
    # confirms is the table's.
    opp = Opponents([{'w': 1, 'active': 3, 'reach': 99.0, 'hp': 300.0, 'startup': 40.0, 'gap2': 9.0, 'active2': 1,
                      'reach2': 99.0, 'hp2': 100.0, 'end': 200.0}])
    plain = opp._table(rolls, 'v25')
    aimed = opp._table(rolls, 'v26', 18, 99.0)
    dv = opp.dodge_value(rolls, 'v26', 18, 99.0, 500.0)
    m0 = rolls[0]
    dist0 = np.array([math.hypot(x, ENGAGE_DISTANCE_M + z) for x, z in m0['path']])
    errs = np.array(_timing_errors(DODGE_TIMING_SD_S * TAE_FPS), float)
    gain0 = _ExchangeGain(opp, 0, m0, dist0, 18, 99.0)
    grid0 = np.arange(0.0, 44.0, DODGE_STEP)
    ok0 = opp._escapes(dist0, m0['iframes'], 40.0, 3, 99.0, grid0)

    def worth_at(a, t0):
        # What his 7 presses aimed at `a` are worth to him, each read at its start on the grid.
        S_ = np.maximum(t0, a + errs)
        i_ = np.round(S_ / DODGE_STEP).astype(int)
        inside = (i_ >= 0) & (i_ < len(ok0))
        okv = np.where(inside, ok0[np.clip(i_, 0, len(ok0) - 1)], False)
        return float((okv * (1.0 + gain0(i_ * DODGE_STEP))).sum())
    t0 = max(opp.cue[0] + d for d, _ in reaction_delays())
    scan = max(worth_at(a, t0) for a in np.arange(t0, 44.0, 0.01))
    S_aim = aimed['S'][aimed['ROW'] == 0][:len(errs)]
    i_aim = np.round(S_aim / DODGE_STEP).astype(int)
    got_best = float((ok0[i_aim] * (1.0 + gain0(i_aim * DODGE_STEP))).sum())
    check('v26 item 4: the exchange model\'s dodge aims for its punish: aimed for escape alone it never punishes '
          '(presses f29.5-33.6, before the f36.5 both hits need); aimed for escape and punish it punishes, at the '
          'best aim a scan of every aim 0.01 frame apart finds',
          (round(float((plain['W'] * plain['FIRST'] * plain['SECOND']).sum()), 6), dv['p_punish'] > 0.5,
           round(got_best, 6) == round(scan, 6), round(float(plain['S'].min()), 1) >= 29.0),
          (0.0, True, True, True), 'Opponents._table, _ExchangeGain, dodge_presses (version 25 aim rule)')


def _selftest_v27(check, t, disc_, b1):
    """Version 27's checks: the roll's whole slide, the attacker's facing from the clip's turn rules,
    a roll out of a track facing the attacker, the volley on the shared chain, a caught defender's
    play in the attacker's facing, a homing geometry in the follow-up's tables, and an opening
    burst after the last that reaches him standing."""
    import numpy as np
    rolls = roll_motion()
    back = next(m for m in rolls if m['path'][-1][1] > 1.0)
    fwd = next(m for m in rolls if m['path'][-1][1] < -1.0)
    side = next(m for m in rolls if m['path'][-1][0] > 1.0)
    # The clip's turn rules (`clip_turn`): Savage Claws' opening (a6xx 040000) sets TAE 224 300 deg/s
    # f0-15, 600 deg/s f15-53 and f53-63, none f63-70 (720 deg/s), and JumpTable 7 f70-150. An update is
    # half a real frame at 30 fps: 300 x 0.5 / 30 = 5 deg, 600 -> 10, 720 -> 12, 0 under JumpTable 7; 300
    # updates to f150.
    claws = clip_turn(SKILL_TAE_BASE + t.arts[4070]['swordArtsTypeNew'], 40000)
    check('v27 clip_turn: Savage Claws turns 5 / 10 / 10 / 12 / 0 deg an update at f5 / f20 / f60 / f65 / f75 '
          '(TAE 224 300 and 600 deg/s, none, JumpTable 7 from f70), 300 updates to f150',
          ([round(math.degrees(claws['rate'][int(f * 2)]), 6) for f in (5, 20, 60, 65, 75)], len(claws['rate'])),
          ([5.0, 10.0, 10.0, 12.0, 0.0], 300), 'TAE a6xx 040000 224 / JumpTable 7; er-mechanics-reach.turn_rate_at')
    # The facing (`_turn_track`): a defender at (0, 3) who is at (1, 3) from the next update on, a turn of
    # 0.1 rad an update: 0, 0.1, 0.2, 0.3, then his bearing atan2(1, 3) = 0.32175. With no turn on updates
    # 1 and 2 (JumpTable 7): 0, 0.1, 0.1, 0.1, 0.2, 0.3, 0.32175.
    toy = {'t0': 0.0, 'rate': np.full(10, 0.1), 'fwd': None, 'sig': ('toy',)}
    xs, ds = np.array([[0.0] + [1.0] * 6]), np.full((1, 7), 3.0)
    th1 = _turn_track(toy, xs, ds, 7)[0][0]
    th2 = _turn_track({**toy, 'rate': np.array([0.1, 0.0, 0.0, 0.1, 0.1, 0.1, 0.1])}, xs, ds, 7)[0][0]
    # His root goes his clip's forward root motion along his facing: locked (no turn) at the bearing of a
    # defender at (1, 3), atan2(1, 3), a 2 m lunge (0.1 m an update) ends 2 x (sin, cos - 1) = (0.632456,
    # -0.102633) off the straight path.
    lunge = {'t0': 0.0, 'rate': np.zeros(21), 'fwd': np.arange(21) * 0.1, 'sig': ('lunge',)}
    _, ldx, ldz = _turn_track(lunge, np.full((1, 21), 1.0), np.full((1, 21), 3.0), 21)
    check('v27 _turn_track: he faces the defender at the clip\'s start, then turns toward him by at most the '
          'update\'s rate: 0, 0.1, 0.2, 0.3, 0.32175 (his bearing); none while turning is off; a lunge made '
          'facing him bends his root toward him (2 m at 18.43 deg: 0.632456 aside, 0.102633 short)',
          ([round(x, 5) for x in th1], [round(x, 5) for x in th2], round(float(ldx[0, -1]), 6), round(float(ldz[0, -1]), 6)),
          ([0.0, 0.1, 0.2, 0.3, 0.32175, 0.32175, 0.32175], [0.0, 0.1, 0.1, 0.1, 0.2, 0.3, 0.32175], 0.632456, -0.102633),
          '_turn_track (clip_turn rule)')
    # A sample read in the facing it was made in: a 0.5 m disc 3 m down the axis at f10 (a shot with no
    # launch read: its facing is its first sample's, f10). A defender who steps 1 m aside from f2 stands at
    # (1, 3), 1 m from the disc: no touch with no turn rules (version 26) or with turning off throughout;
    # at 720 deg/s the attacker faces him by f3 (18.43 deg, 12 an update), which puts him at (0, 3.1623),
    # 0.162 m from the disc: caught at f10.
    step = {'anim': 'step', 'iframes': 0.0, 'ready': None, 'dodge': None, 'move': None, 'path': [(1.0, 0.0)],
            'distance': 1.0}
    dg = {'T': np.array([10.0]), 'X': np.zeros(1), 'Z': np.array([-3.0]), 'R': np.array([0.5]), 'PK': np.array([-math.inf])}
    free_ = int(_catches([{**dg, 'TURN': {'t0': 0.0, 'rate': np.zeros(0), 'fwd': None, 'sig': ('free',)}}], 3.0, step,
                         [2.0])[0][0])
    off_ = int(_catches([{**dg, 'TURN': {'t0': 0.0, 'rate': np.zeros(40), 'fwd': None, 'sig': ('off',)}}], 3.0, step,
                        [2.0])[0][0])
    none_ = int(_catches([dg], 3.0, step, [2.0])[0][0])
    xx, dd = np.array([0.0, 1.5, -2.25, 1e-300]), np.array([3.0, 2.5, 7.0, 1e300])
    same = all(np.array_equal(a, b) for a, b in zip(_turn_apply(xx, dd, np.zeros(4), np.array([1.0, -math.inf, 0.3, 2.0])),
                                                     (xx, dd)))
    check('v27 facing: a disc 3 m down the axis at f10 misses a defender stepped 1 m aside with no turn rules or '
          'turning off, and catches him once the attacker has turned to face him (720 deg/s); a facing of 0 reads '
          'every place unchanged to the bit',
          (free_, off_, none_, same), (0, -1, -1, True), '_facing, _turn_apply, _geo_first_touch')
    # A turned table read once per group of shots that read the defender alike (`_touch_turned`,
    # 2026-11-19) against every pair read whole (`_touch_rows` `ref`): 400 shots over f40-270 (half
    # on whole frames, launched at one of two frames with one of three `PK`s or none finite, so
    # groups hold several), 0.2-1.4 m discs scattered 0-12 m down the axis, with and without launch
    # reads, turning 720 deg/s with a 2.4 m lunge and with no turn read, 2.5 m out, against a back roll
    # carried out of a side roll from f30, a back roll alone (on the axis: no facing read) and a side
    # roll alone, from every start f20-300 in order and shuffled.
    rs_ = np.random.default_rng(1119)
    nt_ = 400
    tt_ = np.sort(rs_.uniform(40.0, 270.0, nt_))
    tt_ = np.where(rs_.random(nt_) < 0.5, np.floor(tt_), tt_)
    tg_ = {'T': tt_, 'X': rs_.normal(0.0, 2.5, nt_), 'Z': -rs_.uniform(0.0, 12.0, nt_), 'R': rs_.uniform(0.2, 1.4, nt_),
           'PK': rs_.choice(np.array([0.4, 1.3, 2.2, np.nan]), nt_),
           'TL': np.where(rs_.random(nt_) < 0.5, 38.0, np.minimum(tt_, 75.0)),
           'TURN': {'t0': 0.0, 'rate': np.full(600, math.radians(720.0) * DODGE_STEP / TAE_FPS),
                    'fwd': np.linspace(0.0, 2.4, 120), 'sig': ('selftest turned table',)}}
    tg2_ = {k: v for k, v in tg_.items() if k != 'TL'}
    tg3_ = {k: v for k, v in tg_.items() if k != 'TURN'}
    ts_ = np.arange(20.0, 300.5, DODGE_STEP)
    same_t, touched_t = True, 0
    for tgv in (tg_, tg2_, tg3_):
        for tmv in (carried(back, track_extend((), side, 30.0)), back, side):
            for tsv in (ts_, rs_.permutation(ts_)):
                tp_, tf_ = _touch_rows(tgv, 2.5, tmv, tsv), _touch_rows(tgv, 2.5, tmv, tsv, ref=True)
                same_t = same_t and tp_.dtype == tf_.dtype and np.array_equal(tp_, tf_)
                touched_t += int(tf_.sum())
    check('turned table: read once per group of shots that read the defender alike, it is the table every pair '
          'read whole gives (carried and plain rolls, with and without launch reads and turns, starts in '
          'order and shuffled; some pairs touch)',
          (same_t, touched_t > 0), (True, True), '_touch_turned against _touch_rows ref')
    # The slide (version 27): a back roll from f30 is 3.7094 m back at f51 (its f21), 3.1699 m at its clip's
    # end (f49) and after; version 26 stood him at 3.7094 throughout. A 0.3 m disc 5.6699 m out at f100
    # touches him there and missed the f21 place (0.539 m off).
    trb = track_extend((), back, 30.0)
    zb = track_at(trb, [51.0, 79.0, 200.0])[1]
    b3 = disc_([100.0], 0.0, 2.5 + float(back['path'][-1][1]), 0.3)
    sm3, ss3 = still_move(trb)
    caught3 = _catches([b3], 2.5, sm3, [ss3])
    check('v27 slide: a back roll from f30 is 3.7094 m back at its dodge cancel and 3.1699 m from its clip\'s end '
          'on; a 0.3 m disc at the end place catches him at f100 (version 26 stood him 0.539 m off it)',
          ([round(float(z), 4) for z in zb], int(caught3[0][0]), float(caught3[1][0])),
          ([3.7094, 3.1699, 3.1699], 0, 100.0), 'track_extend (whole clip), track_at, roll_motion (TAE)')
    # A roll out of a track faces the attacker (`oriented`): from a side roll's end (4.4068 m aside, 2.5 m
    # out, attacker root at his start) a forward roll's end (0, -3.6486) turns by c = 2.5 / 5.0666 = 0.49343,
    # s = -4.4068 / 5.0666 = -0.86977: (c x - s z, s x + c z) = (-3.1735, -1.8004), toward the attacker.
    ofw = oriented(fwd, track_extend((), side, 30.0), 2.5, 0.0)
    check('v27 oriented: a forward roll out of a side roll\'s end runs toward the attacker: (-3.1735, -1.8004), '
          'not straight down the axis',
          tuple(round(float(v), 4) for v in ofw['path'][-1]), (-3.1735, -1.8004), 'oriented, _turned')
    # The volley on the chain (item 3): threat a over everything at f5-6 (before any press: it lands on
    # every defender), threat b a 5 m disc 5.67 m out from f100 to f200; the family takes the back and the
    # forward roll a half each. The back roller is in b (3.17 m back) and every roll from there keeps him
    # in it; the forward roller, 1.15 m behind the attacker's start, is 6.8 m from it. a and b worth 1000
    # each, health 1500: half the defenders take both (capped 1500), half a alone (1000): 1250. Chained as
    # independent (version 26) a landed 1.0 and b 0.5, 1500 capped once: 1500.
    va = disc_([5.0, 6.0], 0.0, 2.5, 1e3)
    vb = disc_(np.arange(100.0, 201.0, 1.0), 0.0, 5.67, 5.0)
    vo3 = volley_outcome([([va], ['a']), ([vb], ['b'])], {'a': 1000.0, 'b': 1000.0}, 2.5, families=[[back, fwd]],
                         hp=1500.0)
    check('v27 volley: the chain carries which threats landed together, so the cap at his health is exact: half '
          'take both (1500), half one (1000): 1250 (independent chances gave 1500); standing he takes both',
          (round(vo3['value'], 6), round(vo3['base'], 6), round(vo3['p_evade'], 6)), (1250.0, 1500.0, 0.0),
          'volley_outcome on dodge_chain (_volley_chain)')
    # A caught defender's play in the attacker's facing (item 4): a caught defender standing 1 m aside at
    # 3 m (on his escape path) against a 0.1 m disc 3.1623 m down the axis at f10 (his distance from the
    # attacker): faced (720 deg/s, rotated onto the axis) he is on it; read on the attack's line, 1.01 m off.
    cg = {'T': np.array([10.0]), 'X': np.zeros(1), 'Z': np.array([-math.hypot(1.0, 3.0)]), 'R': np.array([0.1]),
          'PK': np.array([-math.inf]), 'CAUGHT': {'lists': [caught_list(t, 600000074, 405.0)], 'wf': 1.0}}
    segs_ = [(-math.inf, 1.0, 3.0, 'stand', None, ESCAPE_RUNS[0])]
    cm_t = CaughtModel([({**cg, 'TURN': {'t0': 0.0, 'rate': np.zeros(0), 'fwd': None, 'sig': ('free',)}}, 10.0)], 62.0)
    cm_n = CaughtModel([(cg, 10.0)], 62.0)
    check('v27 caught play: a caught defender 1 m aside is read in the attacker\'s facing (on a 0.1 m disc at his '
          'distance down the axis); read on the attack\'s line (version 26) he is 1.01 m off it',
          (bool(cm_t._touch_all(segs_)[0]), bool(cm_n._touch_all(segs_)[0])), (True, False),
          'CaughtModel._touch_all with turns')
    # A homing geometry in the follow-up's tables (item 5): Rancorcall's skulls (Bullet 10500000, flown
    # again after him) fired at f15, against a back roll 3 m out from every start f0-120: the table's
    # first touch is `_catches`' flown one at every start, and against a defender standing f37 where its
    # stored samples (a 1 cm disc on him at f15, version 26's read) said f15.
    rec = homing_spec(t, bullet_tree(t, 10500000), 0.5, None, _bullet_damages_self)
    hg = {'T': np.array([15.0, 120.0]), 'X': np.zeros(2), 'Z': np.full(2, -3.0), 'R': np.full(2, 0.01),
          'PK': np.full(2, -math.inf), 'HOMING': rec}
    hgrid = np.arange(0.0, 121.0, DODGE_STEP)
    href = _catches([hg], 3.0, back, hgrid)
    htab = _TouchTable([hg], 3.0, back, 0.0, 120.0, _TouchTable([hg], 3.0, None)).first(hgrid, np.full(len(hgrid), -math.inf))
    hst = _TouchTable([hg], 3.0, None).first([0.0], [-math.inf])
    hstored = {k: v for k, v in hg.items() if k != 'HOMING'}
    check('v27 homing table: a homing geometry\'s table reads are its flights after him (equal to _catches at every '
          'start), standing f37 where its stored samples gave f15',
          (np.array_equal(href[0], htab[0]) and np.array_equal(href[1], htab[1]), int(np.isfinite(href[1]).sum()) > 0,
           float(hst[1][0]), float(_TouchTable([hstored], 3.0, None).first([0.0], [-math.inf])[1][0])),
          (True, True, 37.0, 15.0), '_TouchTable HOMING (_homing_touch)')
    # An opening burst after the last that reaches him standing (item 6): A over everything at f40-41,
    # rolled through with a back roll; B a 3.6 m disc 6.5 m out from f60 to f140, 4 m from him standing
    # (never reaches him there) but around the back roller (6.0-6.2 m) and every back roll he can make from
    # there (to 9.9 m at most): caught by B, worth 5. Version 26 never read B: nothing lands.
    oa = disc_([40.0, 40.5, 41.0], 0.0, 2.5, 1e3)
    ob = disc_(np.arange(60.0, 141.0, 1.0), 0.0, 6.5, 3.6)
    fo6 = disc_([70.0], 1000.0, 2.5, 0.5)
    fm6 = FollowupModel([(oa, 0), (ob, 1)], [(fo6, 2)], 43.0, [0.0], my_roll=300.0)
    fm6.families = [[back]]

    def worth6(d, idx):
        return np.array([2.0, 5.0, 1.0])[idx]
    o6 = fm6.outcome(2.5, 0.0, worth6)
    check('v27 opening burst past the last that reaches him standing: it catches the back roller where his roll '
          'carried him (worth 5); version 26 never read it',
          (round(o6['value'], 6), round(o6['p_evade'], 6), o6['base'], int(_catches([ob], 2.5, None, [0.0])[0][0])),
          (5.0, 0.0, 2.0, -1), 'FollowupModel._shape pend / finals')
    # The exchange model picks his direction (item 8). An opener 3 m long from f40 for 30 frames: a forward
    # roll ends 1.15 m from the attacker and escapes none of it; the back and side rolls end 5.7 and 5.1 m
    # away. The four taken equally (version 26) escape at most three quarters; picked per opener, he is worth
    # what his best direction alone is.
    opp8 = Opponents([{'w': 1, 'active': 30, 'reach': 3.0, 'hp': 300.0, 'startup': 40.0, 'end': 200.0}])
    alone = [opp8.dodge_value([m], ('v27 alone', k), 18, 0.1, 500.0) for k, m in enumerate(rolls)]
    picked8 = opp8.dodge_value(rolls, 'v27 picked', 18, 0.1, 500.0)
    t8 = opp8._table(rolls, 'v27 picked', 18, 0.1)
    even8 = float((t8['W'] * t8['FIRST']).sum())
    check('v27 exchange direction: per opener he takes his best direction (worth its own value), a forward roll '
          'escaping none of a long 3 m opener; taken equally (version 26) the four escape at most 3/4',
          (round(picked8['hp'], 6) == round(max(a['hp'] for a in alone), 6),
           round(alone[[m['anim'] for m in rolls].index(fwd['anim'])]['p_evade'], 6), even8 <= 0.75 + 1e-9,
           round(picked8['p_evade'], 6) > round(even8, 6)),
          (True, 0.0, True, True), 'Opponents.dodge_value (MI), _table')
    # His second roll at the chained hit (item 8): the opener at f40-42 escaped by a back roll from f30
    # (invincible to f43), a chained hit over everything 1 frame long. At f50 (gap 10) his next roll comes
    # no sooner than f51 (f30 + its dodge cancel): it cannot escape, 0.0. At f90 (gap 50) any roll from
    # f77.5 to f90 holds its invincibility over it and he presses there: 1.0. Version 26 gave both the
    # opener's single-hit evade share.
    o50 = Opponents([{'w': 1, 'active': 3, 'reach': 99.0, 'hp': 300.0, 'startup': 40.0, 'gap2': 10.0, 'active2': 1,
                      'reach2': 99.0, 'hp2': 100.0, 'end': 200.0}])
    o90 = Opponents([{'w': 1, 'active': 3, 'reach': 99.0, 'hp': 300.0, 'startup': 40.0, 'gap2': 50.0, 'active2': 1,
                      'reach2': 99.0, 'hp2': 100.0, 'end': 200.0}])
    check('v27 exchange second roll: out of a back roll from f30 he rolls again no sooner than f51, so a chained hit '
          'at f50 lands (0.0) and one at f90 is escaped (1.0); version 26 read both at the single-hit share',
          (round(o50._second_roll(0, back, 30.0)[0], 9), round(o90._second_roll(0, back, 30.0)[0], 9)), (0.0, 1.0),
          'Opponents._second_roll (dodge_chain rule)')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--selftest', action='store_true')
    sub = ap.add_subparsers(dest='cmd')
    s = sub.add_parser('skill', help='what one skill does on one weapon')
    s.add_argument('skill')
    s.add_argument('--weapon', required=True)
    s.add_argument('--level', type=int, default=0)
    s.add_argument('--grip', choices=('1H', '2H'), default='1H')
    s.add_argument('--json', action='store_true')
    s = sub.add_parser('list', help='every skill with its FP and class')
    s.add_argument('--mountable', help='only ashes this weapon accepts (Standard affinity)')
    s = sub.add_parser('variants', help='the opening each weapon class and grip plays (diff category)')
    s.add_argument('skill', nargs='?', help='one skill, every class shown; omitted: every skill, moved rows only')
    s = sub.add_parser('compare', help='two er-builds-pvp --json rankings side by side (section 15)')
    s.add_argument('--pvp', required=True, help='the new ranking')
    s.add_argument('--before', required=True, help='the ranking to compare against')
    s.add_argument('--top', type=int, default=25)
    s.add_argument('--cls', help='also list every row of this weapon\'s wepType class, e.g. Lance')
    s = sub.add_parser('ashrank', help='model order of a class\'s ashes vs corpus mounts (section 16)')
    s.add_argument('--pvp', nargs='+', required=True, help='er-builds-pvp --json rankings')
    s.add_argument('--cls', required=True, help='a weapon of the class, e.g. Lance')
    s.add_argument('--weapons', help='comma-separated weapon names to keep')
    s.add_argument('--top', type=int, default=9, help='the class\'s most mounted skills to compare')
    s.add_argument('--extra', help='comma-separated skills to add at their corpus count (e.g. Impaling Thrust)')
    s.add_argument('--names', help='compare exactly these comma-separated skills instead of the top N')
    s.add_argument('--rl', type=int, default=150)
    s.add_argument('--window', type=int, default=10)
    s.add_argument('--mirror', default=os.path.join(CACHE, 'builds.jsonl'))
    s.add_argument('--json-out', help='write {row: Spearman} here')
    for name in ('adoption', 'pvp', 'term', 'check', 'landing'):
        s = sub.add_parser(name)
        s.add_argument('--rl', type=int, default=150)
        s.add_argument('--window', type=int, default=10)
        s.add_argument('--filter', choices=FILTERS, default='pvp' if name in ('term', 'landing') else 'pvptag')
        s.add_argument('--mirror', default=os.path.join(CACHE, 'builds.jsonl'))
        s.add_argument('--top', type=int, default=40)
        if name in ('pvp', 'landing'):
            s.add_argument('--skills', required=name == 'landing',
                           help='comma-separated skill names' + (' instead of the top N' if name == 'pvp' else ''))
        if name == 'check':
            s.add_argument('--pvp', required=True, help='er-builds-pvp.py --sort score --json output')
        if name in ('term', 'landing'):
            s.add_argument('--weapon', required=True)
            s.add_argument('--aff', default='Heavy', choices=AFFINITIES)
            s.add_argument('--stats', default='str=60,dex=12,int=9,fth=9,arc=9')
            s.add_argument('--two', action='store_true')
            s.add_argument('--best', type=float, default=0.0, help="the weapon's best slot score")
            s.add_argument('--engagements', type=int, default=ENGAGEMENTS_DEFAULT)
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.cmd == 'check':
        cmd_check(None, a)
        return 0
    t = AshTables(None)
    {'skill': cmd_skill, 'list': cmd_list, 'adoption': cmd_adoption, 'pvp': cmd_pvp,
     'term': cmd_term, 'compare': cmd_compare, 'landing': cmd_landing,
     'ashrank': cmd_ashrank, 'variants': cmd_variants}.get(a.cmd, lambda *_: ap.print_help())(t, a)
    return 0


if __name__ == '__main__':
    sys.exit(main())
