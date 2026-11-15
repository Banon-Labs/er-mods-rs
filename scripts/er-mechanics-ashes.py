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
                 'randomCreateRadius', 'shootAngleXMaxRandom', 'shootAngleYMaxRandom', 'lifeRandomRange']
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

    def walk_bullet(frame, bt, seen=None):
        # A child bullet that carries the same AtkParam row as one already counted is the same
        # wave travelling on (Hoarfrost Stomp 2260 -> 2261 -> ... -> 2264, all atk 30300863),
        # counted once. A target is taken to be hit once per AtkParam row per cast, and
        # `numShoot` copies fan out rather than stack on one target (`INFERRED`).
        seen = set() if seen is None else seen
        if not bt:
            return
        if bt['atk_row'] not in seen:
            seen.add(bt['atk_row'])
            # Weapon-branch poise, attacks.md section 2: saWeaponDamage x correction/100 + flat.
            w = t.reg.weapon[weapon_id]
            poise = (w['saWeaponDamage'] * (bt['poise_correction'] or 0.0) * 0.01
                     + ((bt['poise_flat'] or 0.0) if bt['add_base_atk'] else 0.0))
            add('bullet', frame, bt, {'bullet': bt['bullet'], 'count': 1, 'num_shoot': bt['num_shoot'],
                                      'poise': round(poise * POISE_MENU, 2)})
        for child in bt['children'].values():
            walk_bullet(frame, child, seen)

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
    s = h['frame'] / TAE_FPS
    return max(s, h['record_s']) if h.get('record_s') is not None else s


def record_mask(hits):
    """Per `skill_hits` row: whether it can land on a defender every row touches at its own frame,
    under the hit priority records (`ATK.shared_landings`, the `HIT_RECORD` block of
    er-mechanics-attacks): the attacks of one record land once between them, plus one with a
    strictly lower priority than the entry; a bullet event's root and children rows share its
    record and its `created`, which is their launch, so they land together."""
    return ATK.shared_landings([(record_time(h), h.get('record'), h.get('priority'), h.get('created', i))
                                for i, h in enumerate(hits)])


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
    same defenders."""
    w = t.reg.weapon[weapon_id]
    total = 0.0
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
        total += h['pvp_damage_total']
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
    return {**parts[1], 'releases': [(r - first, w) for r, w in frames],
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
    dmg = pvp_damage(t, weapon_id, hits, defender, def_module, damage_fn) if hits else 0.0
    if hits:
        # Hits that share a hit priority record land once between them, a bullet launch's rows
        # together (bd er-effects-rs-dnok, er-effects-rs-xb3i).
        dmg = sum(h['pvp_damage_total'] for h, ok in zip(hits, record_mask(hits)) if ok)
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
    on the frame its life runs out (`VERIFIED` KillBullet 0x14039f0c0 path): life / dt plus that one."""
    dt = 1.0 / BULLET_FPS
    life = r['life'] if life is None else life
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
                margin=0.0):
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
    (for `_carrier_touch`'s filter)."""
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
            track = tuple(np.reshape(a, (window + 1, m)) for a in defender(T.ravel(), np.tile(who, window + 1)))
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
                    yield from fire_row(hit, cpos[u], cfr[u], None if cs is None else cs[u], (ut0 + end / BULLET_FPS)[u],
                                        who[ms], depth + 1, rs, me, ekey, fan, (0, 1), ('h', 0))
            if ivl is not None:
                lo, hi = r['intervalCreateTimeMin'], r['intervalCreateTimeMax']
                full = interval_frames(r, int(L.max()), (lo + hi) / 2, first, life)
                picks = range(len(full)) if cap is None or len(full) <= cap else \
                    [round(q * (len(full) - 1) / (cap - 1)) for q in range(cap)]
                crow = t.bullet[ivl['bullet']]
                for jj in picks:
                    pj = full[jj]
                    ok = np.where(code == 2, pj < L - 1, pj <= L - 1)
                    ms = np.nonzero(ok[inv])[0]
                    if not len(ms):
                        continue
                    sfr = _bchild_frame(crow, _frames_at(P, np.full(len(fly), pj), fly, fr, D, S,
                                                         (pj == L - 1) & natural, moves), owner)
                    u = inv[ms]
                    yield from fire_row(ivl, P[pj, u], sfr[u], None, ut0[u] + pj / BULLET_FPS, who[ms], depth + 1,
                                        rs, me, ekey, fan, (jj, len(full)), ('i', jj))

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
    touches anyone at its window's start (`INFERRED`, the landing's own rule)."""
    import numpy as np
    out = []
    for start, contact, source, raw in _hit_contacts_raw(t, weapon_id, sword_arts_id, hits):
        geom = None
        if source == 'pose':
            geom = contact_geometry(entries=raw)
        elif source == 'flight':
            geom = contact_geometry(bullet_points=raw)
        if source == 'timing':
            geom = {'T': np.array([start * TAE_FPS]), 'X': np.zeros(1), 'Z': np.full(1, -99.0),
                    'R': np.full(1, 1e3), 'PK': np.full(1, -math.inf)}
        out.append((start, contact, source, geom))
    return out


def _hit_contacts_raw(t, weapon_id, sword_arts_id, hits):
    """Per hit: (start real seconds, contact(distance_at) -> real seconds or None, source, the
    samples behind it: `window_points` entries for 'pose', [(seconds, x, z, disc, peak, launch
    seconds)] for 'flight': each bullet launched where `skill_bullet_launch` puts it and held back
    by the defender's body as the caster was at the launch (`bullet_launch`)."""
    _, reach_mod, _ = _pose()
    cat = SKILL_TAE_BASE + t.arts[sword_arts_id]['swordArtsTypeNew']
    tae = skill_tae(t, sword_arts_id) or {}
    prof = skill_profile(t, sword_arts_id, weapon_id)
    profile = reach_mod.defender_profile('idle')
    out = []
    for h in hits:
        events = tae.get(h.get('anim')) or []
        start = ATK.clip_to_real(events)(h['frame'] / TAE_FPS) if events else h['frame'] / TAE_FPS
        contact, source, raw = None, 'timing', None
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
        out.append((start, contact, source, raw))
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
            # A hit whose priority record another attack holds on him at an equal or lower
            # priority does nothing; a row of the holding launch lands (bd er-effects-rs-dnok,
            # er-effects-rs-xb3i).
            if x['rec'] is not None and not ATK.record_contact(held, x['rec'], x['prio'], x['created']):
                continue
            if last is not None and _update(tc) != _update(last) and (
                    lock_end is None or tc * TAE_FPS >= lock_end):
                break
            landed.append(x)
            last = tc
            cum += x['poise']
            broken = cum > p
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
    pk = [g['PK'][np.isfinite(g['PK'])] for g in geoms if g is not None]
    advance = max((float(p.max()) for p in pk if len(p)), default=0.0)
    total, outs = 0.0, []
    for d in land['used']:
        sh = land['share_by_distance'][d]
        value = [sum(hits[idx[j]].get('pvp_damage_total', 0.0) * sh[idx[j]]
                     for j in range(len(idx)) if starts[j] >= starts[k]) for k in range(len(idx))]
        plan = lookalike_pair(other, d, float(look['offset']), look.get('releases'), look.get('shows')) \
            if other is not None else None
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
    dodge cancel), and `path` [(x, z)] root motion per real frame (-Z forward). Cached."""
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
           'dodge': real['dodge'], 'path': path,
           'distance': round(math.hypot(*path[-1]), 2)}
    _MOTION[key] = out
    return out


def roll_motion():
    """`evasion_motion` of the four medium-roll directions."""
    return [m for m in (evasion_motion(ROLL_CATEGORY, a) for a in ROLL_ANIMS) if m]


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

    def _table(self, moves, key):
        """Every dodge the pool's attacks draw from a defender who waits, flattened: weight `W`
        (row x press x direction), start `S`, `FIRST` (the opener missed), `SECOND` (the chained
        hit missed too, or there is none), the direction's R1 frame `READY`, `NEAR` (the dodger's
        distance at that frame from where the attacker's advance left him) and the row's `END`.
        The dodge starts where `dodge_presses` puts it over the grid of starts that escape the
        opener. Cached on `key`."""
        np = self.np
        if key in self._tables:
            return self._tables[key]
        cols = {k: [] for k in ('W', 'S', 'FIRST', 'SECOND', 'READY', 'NEAR', 'END', 'ROW')}
        single = np.zeros(len(self.w))
        for m in moves:
            dist = np.array([math.hypot(x, ENGAGE_DISTANCE_M + z) for x, z in m['path']])
            ready = m['ready'] if m['ready'] is not None else len(m['path'])
            rx, rz = m['path'][min(int(ready), len(m['path']) - 1)]
            for i in range(len(self.w)):
                grid = np.arange(0.0, self.startup[i] + self.active[i] + 1.0, DODGE_STEP)
                if self.timing == 'uniform':
                    presses = [(self.startup[i] - tau, 1.0 / DODGE_TIMING_FRAMES)
                               for tau in range(DODGE_TIMING_FRAMES)]
                else:
                    ok = self._escapes(dist, m['iframes'], self.startup[i], self.active[i], self.reach[i], grid)
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
                cols['END'].append(np.full(len(s), self.end[i])), cols['ROW'].append(np.full(len(s), i))
        tab = {k: np.concatenate(v) for k, v in cols.items()}
        tab['single'] = single
        self._tables[key] = tab
        return tab

    def dodge_value(self, moves, key, strike, reach_me, hp_me):
        """Expected HP of answering one opponent string with `moves` (the directions equally used,
        `INFERRED`), the dodge timed by a defender who waits and reacts (`_table`). Avoided: the
        opener when `FIRST`; the chained hit when the opener was avoided and either `SECOND` or
        another dodge of the same kind catches it (that opponent's single-hit evade share).
        Punish: `hp_me` when the string needed one dodge, the dodger's R1 cancel plus `strike`
        comes before the attacker's `END`, and he is within `reach_me` of the attacker then."""
        np = self.np
        tab = self._table(moves, key)
        row = tab['ROW'].astype(int)
        first, second = tab['FIRST'], tab['SECOND']
        both = first * (second + ~second * tab['single'][row])
        avoided = float((tab['W'] * (first * self.hp[row] + both * self.hp2[row])).sum())
        # A dodger left outside his own reach runs the rest before he strikes (the neutral model's
        # run speed, `er-mechanics-neutral.frames_per_metre`), so a whiff can be punished from
        # outside it when the attacker's recovery is long enough.
        close = np.maximum(0.0, tab['NEAR'] - reach_me) * _neutral().frames_per_metre()
        pun = first & second & (tab['S'] + tab['READY'] + close + strike < tab['END'])
        p_punish = float((tab['W'] * pun).sum())
        return {'hp': avoided + p_punish * hp_me, 'evade_hp': avoided, 'punish_hp': p_punish * hp_me,
                'p_evade': float((tab['W'] * first).sum()), 'p_punish': p_punish}

    def buff_answer_value(self, start, ready, duration_s, taken, uninterruptible, strike, reach_me, hp_me):
        """Expected HP of answering one opponent string with a defensive buff cast on reaction, the
        way `dodge_value` answers it with a dodge: he presses at the opener's cue plus his reaction
        (`reaction_delays`), the buff is live from `start` frames later for `duration_s`; every hit
        whose first live frame falls inside it is taken at `taken`, the others whole (he does not
        dodge). Under an `uninterruptible` buff his own attack comes out at `ready` + `strike` (plus
        running to `reach_me` from where the attacker's advance left him) and punishes when that is
        before the attacker can act again (`END`) and the opener was taken under the buff."""
        np = self.np
        k = _neutral().frames_per_metre()
        near = np.maximum(0.0, ENGAGE_DISTANCE_M - self.advance)
        close = np.maximum(0.0, near - reach_me) * k
        avoided = punish = 0.0
        for delay, wd in reaction_delays():
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
    which have none, so the jump family drops out (er-effects-rs-8uha). A key the ranking has no
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


def _plain_cdf():
    """`_ND.cdf` as a plain function: `statistics.NormalDist.cdf`'s own arithmetic, 0.5 x
    erfc((mu - x) / (sigma x sqrt 2)), without the method call (`group_presses` reads millions of
    them). `_ND.cdf` itself when that arithmetic cannot be read off this Python's `statistics` or
    gives any other number on the sample values checked here."""
    from math import erfc
    try:
        mu, div = float(_ND._mu), float(_ND._sigma) * float(statistics._SQRT2)
    except (AttributeError, TypeError):
        return _ND.cdf

    def cdf(x):
        return 0.5 * erfc((mu - x) / div)
    xs = [i / 64.0 for i in range(-640, 641)] + [k * 0.1234567891 for k in range(-400, 401)] + [-0.0, 1e-300, -1e-300]
    return cdf if all(cdf(x) == _ND.cdf(x) for x in xs) else _ND.cdf


_CDF = _plain_cdf()
#: `_cdf_kept`'s values: {x: `_CDF(x)`}. `group_presses` reads the cdf of (end - aim) / sd, and a
#: run's ends sit on the half-frame grid and a stretch's middle on the quarter-frame grid, so the
#: same arguments come back across sets of attacks and earliest starts. Emptied past `CDF_KEPT`
#: entries.
_CDF_KEPT = {}
CDF_KEPT = 1_000_000


def _cdf_kept(x):
    """`_CDF(x)`, kept per x: the same number."""
    v = _CDF_KEPT.get(x)
    if v is None:
        if len(_CDF_KEPT) >= CDF_KEPT:
            _CDF_KEPT.clear()
        v = _CDF_KEPT[x] = _CDF(x)
    return v


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


#: His shortest reaction (`reaction_delays`): a floor at or before a decision time plus this moves
#: no press decided then (`lookalike_plan`).
_SOONEST_REACTION = min(d for d, _ in reaction_delays())


#: A run of true bytes (`_runs`).
_TRUE_RUN = re.compile(b'\x01+')


def _runs(ok):
    """[(first, last)] index runs where `ok` holds: the runs of 1 in its bytes as bools."""
    import numpy as np
    return [(m.start(), m.end() - 1) for m in _TRUE_RUN.finditer(np.asarray(ok, bool).tobytes())]


#: `_runs_kept`'s runs: {id(array): (array, step, runs)}, the array kept so its id names it; emptied
#: past `RUNS_KEPT` entries.
_RUNS_KEPT = {}
RUNS_KEPT = 50000


def _runs_kept(ok, step):
    """[(first, last) x `step`] of the runs of `ok` (`_runs`), kept per array: `group_presses` reads
    one member's escaping starts in every set he is alike in. An array is taken as unchanged while
    it lives (nothing writes an escaping-starts array once made); anything else is read each time."""
    import numpy as np
    if not isinstance(ok, np.ndarray):
        return [(a * step, b * step) for a, b in _runs(ok)]
    got = _RUNS_KEPT.get(id(ok))
    if got is None or got[0] is not ok or got[1] != step:
        if len(_RUNS_KEPT) >= RUNS_KEPT:
            _RUNS_KEPT.clear()
        got = _RUNS_KEPT[id(ok)] = (ok, step, [(a * step, b * step) for a, b in _runs(ok)])
    return got[2]


def dodge_presses(ok, cue, step=DODGE_STEP, floor=0.0):
    """[(dodge start, real frames from the attack's start; weight)]: when a defender who waits and
    reacts starts his dodge.

    `ok[i]` says whether a dodge started at i x `step` escapes the attack. He cannot start before
    `cue` + his reaction delay (`reaction_delays`), nor before `floor` (the frame his own recovery
    lets him roll, for a defender caught in it). Among the escaping runs still open then he
    takes the one he is likeliest to hit and aims at its middle, with a normal timing error
    (`DODGE_TIMING_SD_S`); a press that would come before he can react comes at the reaction
    instead. When no run is left he dodges at the reaction anyway, too late. So an early roll
    loses to a long or late live window and a late one to a fast startup."""
    sd = DODGE_TIMING_SD_S * TAE_FPS
    runs = [(a * step, b * step) for a, b in _runs(ok)]
    errors = _timing_errors(sd)
    out = []
    for delay, w in reaction_delays():
        t = max(cue + delay, floor)
        best = None
        for lo, hi in runs:
            if hi < t:
                continue
            lo2 = max(lo, t)
            half = (hi - lo2) / 2
            p = _ND.cdf(half / sd) - (_ND.cdf(-half / sd) if lo2 > t else 0.0) if sd > 0 else 1.0
            if best is None or p > best[0] + 1e-9:
                best = (p, lo2 + half)
        if best is None:
            out.append((t, w))
            continue
        out += [(max(t, best[1] + e), w / TIMING_POINTS) for e in errors]
    return out


def _single_presses(ok, cue, step, floor, memo):
    """`dodge_presses(ok, cue, step, floor)` with its runs and each reaction delay's presses kept in
    `memo` (`group_presses`' memo of a set of one attack): a delay's presses read only the earliest
    start it allows and its weight, so the same lists, read once per pair. `lookalike_plan` asks for
    them again at every floor of a release seen alone (Black Flame's profile at its second release
    node read them 5,780 times once the release had 27 members, 560 times with 7; 2026-11-13)."""
    held0 = len(memo) + len(memo.get('single', ()))
    if 'single runs' not in memo:
        memo['single runs'] = _runs_kept(ok, step)
    runs = memo['single runs']
    got = memo.setdefault('single', {})
    sd = DODGE_TIMING_SD_S * TAE_FPS
    out = []
    for delay, w in reaction_delays():
        t = max(cue + delay, floor)
        chunk = got.get((t, w))
        if chunk is None:
            best = None
            for lo, hi in runs:
                if hi < t:
                    continue
                lo2 = max(lo, t)
                half = (hi - lo2) / 2
                p = _CDF(half / sd) - (_CDF(-half / sd) if lo2 > t else 0.0) if sd > 0 else 1.0
                if best is None or p > best[0] + 1e-9:
                    best = (p, lo2 + half)
            chunk = got[(t, w)] = [(t, w)] if best is None else [(max(t, best[1] + e), w / TIMING_POINTS)
                                                                 for e in _timing_errors(sd)]
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


def group_presses(oks, weights, cue, step=DODGE_STEP, floor=0.0, memo=None):
    """`dodge_presses` for a defender who cannot tell apart the attacks whose escaping starts are
    `oks` (one bool array per attack on the `step` grid, a start past an array being caught), the
    attack j weighing `weights[j]`: one timing chosen against all of them. He cannot start before
    `cue` + his reaction delay, nor before `floor`; then he aims at the middle of a stretch of
    starts, taking the stretch whose weighted chance of a press inside an escaping run of each
    attack is largest (the first of equally sure ones), with `dodge_presses`' timing error. The
    stretches are each attack's runs and the stretches over which the set of attacks a start
    escapes stays the same, so a start that escapes two of them at once is found. One attack is
    `dodge_presses` itself. `memo`, a dict, keeps the stretches and each earliest start's aim for
    the next call on the same `oks` and `weights` (it is the caller's to key)."""
    import numpy as np
    if len(oks) == 1:
        if memo is None:
            return dodge_presses(oks[0], cue, step, floor)
        return _single_presses(oks[0], cue, step, floor, memo)
    delays = reaction_delays()
    if memo is not None and 'chunks' in memo:
        # Every delay's presses kept already: the same list, read without the setup below.
        chunks, out = memo['chunks'], []
        for delay, w in delays:
            got = chunks.get((max(cue + delay, floor), w))
            if got is None:
                break
            out += got
        else:
            return out
    kept = memo is not None
    memo = {} if memo is None else memo
    table = memo.get('aims')
    if table is None:
        table = memo['aims'] = _GroupAims(oks, weights, step)
    # The presses of one reaction delay read only the time he can press from and that delay's
    # weight: kept per pair.
    chunks = memo.setdefault('chunks', {})
    held0 = table.size + len(chunks)
    errors = _timing_errors(table.sd)
    out = []
    for delay, w in delays:
        t = max(cue + delay, floor)
        if (t, w) not in chunks:
            a = table.aim(t)
            chunks[(t, w)] = [(t, w)] if a is None else [(max(t, a + e), w / TIMING_POINTS) for e in errors]
        out += chunks[(t, w)]
    if kept:
        # Counted with the `lookalike_plan` memo it lives in (`LOOKALIKE_MEMO_ENTRIES`).
        _LOOK_HELD[0] += table.size + len(chunks) - held0
    return out


def _pick(cands, best=None):
    """`group_presses`' choice over `cands` [(aim, chance)] in order, from `best` (chance, aim): an
    aim is taken over the one so far when its chance is more than 1e-9 above it."""
    for a, p in cands:
        if best is None or p > best[0] + 1e-9:
            best = (p, a)
    return best


class _GroupAims:
    """`group_presses`' reads of one set of attacks (`oks`, `weights`): their escaping runs, the
    stretches it aims within, and each earliest start's aim (`aim`).

    The aims tried from earliest start t are the middles of the stretches with `hi` >= t, each from
    the later of its start and t, in order (equal ones once); the one taken is the first whose
    weighted chance of landing in an escaping run is more than 1e-9 above the best before it. That
    chance (`landed`) sums, per attack with a run that holds the aim, its weight times the share of
    a press aimed there with `dodge_presses`' timing error that lands inside the run, the part
    before t cut off where the run starts after t. Which runs hold a point is the same for every
    point of one place among the stretches' ends (`place`; every run is a stretch): kept per place
    with their weights' sum. A stretch that starts at or after t aims at its own middle, whose two
    `_ND.cdf` values per holding run are kept, and its chance per count of those runs' starts at
    or before t; one that holds t aims at the middle of what is left of it, read afresh unless its
    holding weights alone (each run's share is at most 1) cannot put it more than 1e-9 above the
    best before it, when it would not be taken. The same chances, in the same order, as reading
    every stretch with every call, so the same aim."""

    def __init__(self, oks, weights, step):
        import numpy as np
        n = max(len(o) for o in oks)
        pad = [np.concatenate([np.asarray(o, bool), np.zeros(n - len(o), bool)]) for o in oks]
        # A padded array's runs are its own (the pad adds no start that escapes).
        runs = [_runs_kept(o, step) for o in oks]
        code = np.zeros(n, np.int64)
        for j, o in enumerate(pad):
            code |= o.astype(np.int64) << j
        stretches = set(r for rs in runs for r in rs)
        cut = np.flatnonzero(np.diff(code)) + 1
        for a, b in zip(np.r_[0, cut], np.r_[cut, n]):
            if code[a]:
                stretches.add((int(a) * step, int(b - 1) * step))
        self.runs, self.stretches = runs, sorted(stretches)
        # Each attack's runs' starts: its runs are apart and in order, so the one that holds `a`, if
        # any, is the last that starts at or before it.
        self.starts = [[lo for lo, _ in rs] for rs in runs]
        self.weights, self.total = list(weights), float(sum(weights))
        self.sd = DODGE_TIMING_SD_S * TAE_FPS
        self.ends = sorted({x for s in self.stretches for x in s})
        self.middles = sorted((lo + (hi - lo) / 2, lo) for lo, hi in self.stretches)
        self.middle_aims = [a for a, _ in self.middles]
        self.los = [lo for lo, _ in self.stretches]
        # {place: ([(weight, run)] holding a point there, in attack order; their weights' sum over
        # the total)}, {aim: (the distinct starts of its runs, sorted; per run (weight, start, cdf
        # at its end, cdf at its start); {how many of the starts are at or before t: chance};
        # whether there is a timing error)}.
        self.places, self.held = {}, {}
        self.size = 0
        # Every run in attack order, then start order, as arrays: an attack's runs are apart, so the
        # runs with start <= x <= end are each holding attack's one run that holds x, in its order.
        flat = [(j, r) for j, rs in enumerate(runs) for r in rs]
        self.flat = [(self.weights[j], r) for j, r in flat]
        self.flat_lo = np.array([r[0] for _, r in flat], float)
        self.flat_hi = np.array([r[1] for _, r in flat], float)

    def place(self, x):
        i = bisect.bisect_left(self.ends, x)
        return i, i < len(self.ends) and self.ends[i] == x

    def holders(self, x):
        pk = self.place(x)
        got = self.places.get(pk)
        if got is None:
            import numpy as np
            flat = self.flat
            per = [flat[i] for i in np.flatnonzero((self.flat_lo <= x) & (self.flat_hi >= x)).tolist()]
            got = self.places[pk] = (per, sum(wj for wj, _ in per) / self.total)
            self.size += 1
        return got

    def landed(self, a, t):
        # A run's share: `_ND.cdf((hi - a) / sd)` less, when the run starts after t,
        # `_ND.cdf((lo - a) / sd)`; 1.0 with no timing error. An attack with no run holding `a`
        # adds w * 0.0, a 0 that moves neither the sum nor its rounding, so only those with one
        # are summed.
        got = self.held.get(a)
        if got is None:
            per, _ = self.holders(a)
            sd = self.sd
            rows = [(wj, lo, _cdf_kept((hi - a) / sd), _cdf_kept((lo - a) / sd)) for wj, (lo, hi) in per] \
                if sd > 0 else [(wj, lo, 1.0, 0.0) for wj, (lo, hi) in per]
            got = self.held[a] = (sorted({lo for _, lo, _, _ in rows}), rows, {}, sd > 0)
            self.size += 1
        los, rows, vals, timed = got
        at = bisect.bisect_right(los, t)
        v = vals.get(at)
        if v is None:
            if timed:
                v = sum(wj * (c1 - (c2 if lo > t else 0.0)) for wj, lo, c1, c2 in rows) / self.total
            else:
                v = sum(wj * 1.0 for wj, _, _, _ in rows) / self.total
            vals[at] = v
        return v

    def landed_once(self, a, per, t):
        # `landed` read afresh and not kept: an aim inside a stretch that holds t moves with t, so
        # no later call asks for it again.
        sd = self.sd
        if sd <= 0:
            return sum(wj * 1.0 for wj, _ in per) / self.total
        cdf = _cdf_kept
        return sum(wj * (cdf((hi - a) / sd) - (cdf((lo - a) / sd) if lo > t else 0.0))
                   for wj, (lo, hi) in per) / self.total

    def moving(self, a, t, best):
        # An aim inside a stretch that holds t: read afresh, unless its holding weights cannot lift
        # it more than 1e-9 over the best so far (a margin of 1e-12 under that, far above the
        # rounding of either sum, keeps the chance itself from deciding it): it would not be taken.
        per, bound = self.holders(a)
        if best is not None and bound < best[0] + 1e-9 - 1e-12:
            return best
        p = self.landed_once(a, per, t)
        return (p, a) if best is None or p > best[0] + 1e-9 else best

    def aim(self, t):
        # The aims of the stretches that hold t (start before it), in order: `t + x` keeps the
        # order of `x`, and equal ones are one aim.
        moving, prev = [], None
        for hi in sorted({hi for lo, hi in self.stretches[:bisect.bisect_left(self.los, t)] if hi >= t}):
            a = t + (hi - t) / 2
            if a != prev:
                moving.append(a)
                prev = a
        best, mi, nm, last = None, 0, len(moving), None
        held, bisect_right = self.held, bisect.bisect_right
        # The stretches that start at or after t, at their middles, in order (no middle under t
        # starts at or after it), equal ones once, merged with the moving ones; a moving aim equal
        # to one of them is that one. A middle's chance is `landed`'s, read inline.
        for a, lo in self.middles[bisect.bisect_left(self.middle_aims, t):]:
            if lo < t or a == last:
                continue
            last = a
            while mi < nm and moving[mi] < a:
                best = self.moving(moving[mi], t, best)
                mi += 1
            if mi < nm and moving[mi] == a:
                mi += 1
            got = held.get(a)
            p = None if got is None else got[2].get(bisect_right(got[0], t))
            if p is None:
                p = self.landed(a, t)
            if best is None or p > best[0] + 1e-9:
                best = (p, a)
        while mi < nm:
            best = self.moving(moving[mi], t, best)
            mi += 1
        return None if best is None else best[1]


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
                   events=None):
    """How a defender who sees only what is on screen times his first dodge of attack `me` of a
    group he cannot tell apart from its start (the comment above `_escape`).

    `oks[j]` is attack j's escaping starts per move (`reaction_outcome`'s `ok` for each of the
    family's moves, of its first burst that reaches him standing), None when nothing of it reaches
    him standing; `weights[j]` its weight; `splits[j][l]` the real frame from which j and l look
    different (inf when they never do, and on the diagonal). His options are the info times: `cue`
    and each split. Deciding at time f, facing j, he knows the group's members l with `splits[j][l]`
    > f and presses as `group_presses` presses against them from cue f (alone: `dodge_presses` from
    f, a reaction to the split). He takes the time whose presses escape the group most often,
    weighted (the first of equally good ones), averaged over the moves as the outcomes average
    them; a member that does not reach him is escaped whatever he does. Returns {'presses': per
    move, `me`'s presses at that time, 'at': the time, 'escape': {time: the group's escape share}}.
    One member and no split: `dodge_presses` from `cue`, exactly.

    `events[j]` (None: none) is the real frame member j releases (a tap's or an early release's
    clip switch, `release_frames`): something he sees happen, which he reacts to when it comes
    before his decision time, deciding then with what he knows then (`INFERRED`: a release is seen
    as it happens and not waited past; a member that has not released shows nothing). So waiting
    for a late split no longer costs him the early releases, which a release spread over its window
    makes the rule (without it a decision made at the last release reacts to the first that late).
    Callers pass it only for a group with a release spread over several frames; without it the
    plan is the one above, exactly.

    `tags[j]` (hashable, equal only for equal `oks[j]`) and `memo`, a dict, let calls at other
    floors, or for another member of the same group, reuse each set's aims, presses and escapes. A
    floor at or before his earliest press from a decision time (that time plus his shortest
    reaction) moves no press (`group_presses` takes the later of the two), so the presses and the
    escape decided then are shared by every such floor."""
    import numpy as np
    n = len(oks)
    K = len(oks[me])
    tags = list(range(n)) if tags is None else tags
    kept = memo is not None
    memo = _look_memo(memo) if kept else {}
    held0 = len(memo)
    ev = [math.inf] * n if events is None else [math.inf if e is None else float(e) for e in events]
    if ('times', cue) not in memo:
        memo[('times', cue)] = sorted({cue} | {max(cue, s) for row in splits for s in row if math.isfinite(s)})
    times = memo[('times', cue)]
    total = float(sum(weights))
    soonest = _SOONEST_REACTION
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
                                            memo.setdefault(('aims', key, k), {})) for k in range(K)]
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
        ix = [_press_index(p, step) for p in presses(j, f)[1]]
        rows, nos = [], set()
        for l in seen:
            if tno[l] not in nos and ('escape', tno[l], key, t, b, step) not in memo:
                nos.add(tno[l])
                rows.append(l)
        if len(rows) <= 2:
            for l in rows:
                if l not in okb:
                    okb[l] = [(np.asarray(o, bool).tobytes(), len(o)) for o in oks[l]]
                memo[('escape', tno[l], key, t, b, step)] = sum(
                    _escape_index(ob, nb, idx, ws) for (ob, nb), (idx, ws, _, _) in zip(okb[l], ix)) / K
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
            memo[('escape', tno[l], key, t, b, step)] = sum([per[k][r] for k in range(K)]) / K
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
        best, escape = None, {}
        for f, row in zip(times, rows):
            esc = 0.0
            for j, t, key in row:
                if t is None:
                    esc += weights[j]
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
                esc += weights[j] * e
            escape[f] = esc / total
            if best is None or escape[f] > best[1] + 1e-9:
                best = (f, escape[f])
        memo[pk] = (best[0], escape)
    at, escape = memo[pk]
    out = {'presses': presses(me, at)[1], 'at': at, 'escape': escape}
    if kept:
        _LOOK_HELD[0] += len(memo) - held0
    return out


def lookalike_pair(other, dist, split, releases=None, shows=None):
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
    (`lookalike_plan` `events`). One entry at 0 showing at once is the pair above, exactly."""
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
    theirs, memos = {}, {}

    def plan(moves, oks, cue, floor):
        fam = tuple(m['anim'] for m in moves)
        if fam not in theirs:
            got = []
            for dl, _ in rel:
                gs = other if dl == 0.0 else [shift_geometry(g, dl) for g in other]
                live = [g['T'].max() for g in gs if g is not None]
                if not live or _catches(gs, dist, None, [0.0])[0][0] < 0:
                    got.append(None)
                else:
                    grid = np.arange(0.0, max(live) + 1.0, DODGE_STEP)
                    got.append([_catches(gs, dist, m, grid)[0] < 0 for m in moves])
            theirs[fam] = got
        return lookalike_plan([oks] + theirs[fam], weights, splits, 0, cue, floor, memo=memos.setdefault(fam, {}),
                              events=events)['presses']
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
    """Empty the memos of flights and touch reads (`_HOMING_TOUCH`, `_HOMING_REACH`, `_CATCHES`) and
    the lookalike plans' (`forget_look_memos`): every answer they give is the one a fresh read
    gives, so this moves no number, only memory (attack-similarity empties them per attack set,
    whose attacks no other set reads)."""
    _HOMING_TOUCH.clear()
    _HOMING_REACH.clear()
    _HOMING_HELD[0] = 0
    _CATCHES.clear()
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
    return (p['bt']['bullet'], round(p['fire'], 4), _launch_key(p['launch']), p['dmg'], p['index'], p['aim'])


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
    if len(_MOVE_KEYS) > 4096:
        _MOVE_KEYS.clear()
    _MOVE_KEYS[id(move)] = (move, key)
    return key


def _homing_entries(part, reg, dist, moves, air, starts):
    """Per move of `moves` (None: he stands): {lineage key of each hitting shot of `part`'s tree
    (`fly_members`): the first real frame that shot, flown again against the defender on each
    dodge start (every move and start at once), touches him outside his invincibility (inf where it
    never does; a shot that touches him on no start of the move is left out)}. `air`: the geometry's
    `AIR` mark (a jump is immune only to such a hit). Memoised
    per move on the flight, not on the geometry, so a volley's legs share one flight and a family's
    four roll directions one batch. A distance of `HOMING_DISTS` is flown in one batch with the
    others of them it will be asked for (`_homing_batch`): every defender's shots fly on their own
    and each one's touches are read off its own flight (`_entry_first_touch`), so the numbers are
    those of flying it alone."""
    import numpy as np
    sig = _part_sig({**part, 'index': None})
    mkeys = [_move_key(mv) for mv in moves]
    sb = starts.tobytes()

    def keys_at(d):
        return [(sig, round(float(d), 4), mk, air, sb) for mk in mkeys]
    keys = keys_at(dist)
    if all(k in _HOMING_TOUCH for k in keys):
        return [_HOMING_TOUCH[k] for k in keys]
    dists = [dist] + _homing_batch(sig, dist, moves, air, len(starts), keys_at)
    s = len(starts)
    per = s * len(moves)
    n = per * len(dists)
    S = np.tile(starts, len(moves) * len(dists))
    # Each move's root motion, padded with its last frame to one length (he stands after his clip).
    paths = [np.zeros((1, 2)) if mv is None else np.array(mv['path'], float) for mv in moves]
    m = max(len(p) for p in paths)
    PX = np.array([np.concatenate([p[:, 0], np.full(m - len(p), p[-1, 0])]) for p in paths])
    PZ = np.array([np.concatenate([p[:, 1], np.full(m - len(p), p[-1, 1])]) for p in paths])
    mi = np.tile(np.repeat(np.arange(len(moves)), s), len(dists))
    still = np.tile(np.repeat(np.array([mv is None for mv in moves]), s), len(dists))
    iframes = np.tile(np.repeat(np.array([0.0 if mv is None else float(mv['iframes']) for mv in moves]), s),
                      len(dists))
    immune = np.tile(np.repeat(np.array([mv is not None and (not mv.get('air') or air) for mv in moves]), s),
                     len(dists))
    # Where he stands: the distance of each defender's flight (one number when it is one distance).
    ahead = dist if len(dists) == 1 else np.repeat(np.array(dists, float), per)
    ahead_d = np.array(dists, float)
    # The root motions flat, read at move x `m` + frame: one gather, the same numbers.
    PXf, PZf, row = PX.ravel(), PZ.ravel(), mi * m
    # The same with a 0 before each move's first frame: where he is before his dodge starts, read
    # at frame -1 (`moved`).
    PX1 = np.concatenate([np.zeros((len(moves), 1)), PX], axis=1)
    PZ1 = np.concatenate([np.zeros((len(moves), 1)), PZ], axis=1)

    def moved(T):
        """How far he has moved at real frame(s) `T`, (x, z) arrays (..., `per`): every move's, side
        by side. A defender's frame of his move is read off his dodge start alone, and every move and
        distance has the same starts: read once per start, then laid out per move (each distance's
        are the same). Before his start he has not moved (frame -1 of `PX1`, `PZ1`); from it he is
        at its frame, his last after his clip."""
        u = np.asarray(T, float)[..., None] - starts
        k = np.minimum(np.maximum(np.floor(u).astype(int), -1), m - 1) + 1
        x, z = np.empty(u.shape[:-1] + (per,)), np.empty(u.shape[:-1] + (per,))
        for j in range(len(moves)):
            at_j = slice(j * s, (j + 1) * s)
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
        return (np.where(moving, PXf[k], 0.0),
                (ahead if len(dists) == 1 else ahead[who]) + np.where(moving, PZf[k], 0.0), vuln)
    # The most he moves in one bullet step: his position changes once per real frame, by one step
    # of his root motion (or onto its first frame when the move starts).
    jump = max([0.0] + [float(np.hypot(*p[0])) for p in paths]
               + [float(np.hypot(*np.diff(p, axis=0).T).max()) for p in paths if len(p) > 1])
    margin = jump * math.ceil(TAE_FPS / BULLET_FPS) + 1e-6
    t = _HOMING_TABLES.get(reg) or AshTables(reg)
    launch, fire = part['launch'], part['fire']
    held = None
    if launch is not None and math.isfinite(launch['peak']):
        held = np.maximum(0.0, launch['peak'] - (where(fire)[1] - 2 * _pose()[1].PUSH_CAPSULE_RADIUS))
    outs = [[{} for _ in moves] for _ in dists]
    added = 0
    # An aimed part's shots are aimed at where he is on the frame each one is created, his roll
    # included (FUN_140390c20 reads the target's position then); one left unaimed keeps its launch
    # heading. Each entry is read as it is flown and let go, so one shot's paths are held at a time.
    for e in fly_members(t, part['bt'], n, fire, launch, at_each, part['aim'], held, margin=margin):
        if e['node']['bullet'] not in part['dmg']:
            continue
        tt = np.full(n, math.inf)
        tt[e['who']] = _entry_first_touch(e, fire, at_each, margin)[e['inv']]
        kept = False
        for di, out in enumerate(outs):
            for j, o in enumerate(out):
                v = tt[di * per + j * s:di * per + (j + 1) * s]
                # A shot that touches none of one move's defenders is left out: `_homing_touch` takes
                # the least of the shots' touches, which an inf never moves, so a missing key reads
                # as it would. Founding Rain of Stars' flights are 90% such answers (2026-11-13).
                if not np.isposinf(v).all():
                    o[e['key']] = v
                    kept = True
        if kept:
            added += tt.nbytes
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
    to `HOMING_BATCH_MEMBERS` defenders in all."""
    if dist not in HOMING_DISTS:
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
            lo = (ii.min() // HOMING_START_BUCKET) * HOMING_START_BUCKET
            hi = (ii.max() // HOMING_START_BUCKET + 1) * HOMING_START_BUCKET
            flown = np.arange(lo, hi) * DODGE_STEP
            tts = _homing_entries({**part, 'fire': part['fire'] - shift}, spec['reg'], dist, moves,
                                  bool(g.get('AIR')), flown)[j]
            pick = ii - lo
        else:
            shift, pick = 0.0, slice(None)
            tts = _homing_entries(part, spec['reg'], dist, moves, bool(g.get('AIR')), starts)[j]
        for k in (tts if part['index'] is None else part['index']):
            if k in tts:
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
        dl = dist + np.where(ul >= 0, pz[kl], 0.0)
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
    order = _geo_reads(g)[0]
    whole = order is None
    cols = np.arange(len(T)) if whole else order[_may_touch(g, dist, move)[order]]
    if not len(cols):
        return found
    lim = found if bound is None else np.asarray(bound, float)
    immune = not move.get('air') or bool(g.get('AIR'))
    iframes = move['iframes']
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
        xr = np.where(moving, px[k], 0.0)
        d = dist + np.where(moving, pz[k], 0.0)
        vuln = ~(moving & (u < iframes) & immune)
        if TL is not None:
            ul = TL[J][None, :] - st
            kl = np.clip(np.floor(ul).astype(int), 0, n - 1)
            dl = dist + np.where(ul >= 0, pz[kl], 0.0)
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
    for h, g in enumerate(geoms):
        if g is None:
            continue
        if 'HOMING' in g:
            tt = _homing_touch(g, dist, move, starts[:, 0])
            better = tt < best_t
            best_t[better], best_h[better] = tt[better], h
            continue
        if move is not None:
            # Only a first touch below the best so far is taken (`_geo_first_touch` `bound`).
            tt = _geo_first_touch(g, dist, move, starts, px, pz, n, radius, best_t)
            better = tt < best_t
            best_t[better], best_h[better] = tt[better], h
            continue
        u = g['T'][None, :] - starts
        xr, d, vuln = 0.0, np.full(u.shape, float(dist)), np.ones(u.shape, bool)
        held = _held(g, dist, move, u, starts, 1, None, radius)
        if held is None:
            held = np.maximum(0.0, g['PK'][None, :] - (d - 2 * radius))
        touch = vuln & (np.hypot(g['X'][None, :] - xr, g['Z'][None, :] + held + d) < g['R'][None, :])
        tt = np.where(touch, g['T'][None, :], math.inf).min(axis=1)
        better = tt < best_t
        best_t[better], best_h[better] = tt[better], h
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


def reaction_outcome(geoms, hit_value, dist, cue=0.0, moves=None, my_roll=None, advance=0.0,
                     strikes=None, floor=0.0, run=False, catches=None, plan=None):
    """A defender `dist` m ahead who waits for this attack and dodges on reaction (section 16).

    `geoms` is one `contact_geometry` per hit (None where a hit has no shapes), `hit_value[h]` what
    the attack is worth when hit h is the first to catch him (for a slot 1.0, for a skill the
    damage of that hit and of every later one that lands). `cue` is the real frame the attack
    becomes readable: its clip's start, or for a hit whose timing the attacker picks (an R2's
    release, a skill follow-up, a stance) the start of that part. Each of the medium roll's four
    directions (`roll_motion`) is taken equally (`INFERRED`, as section 14a), and for each the
    dodge starts where `dodge_presses` puts it, no earlier than `floor`.

    Returns `value` (expected worth when he reacts), `p_evade`, and, when `strikes` (the pool's
    R1 strike frames) and `my_roll` (the attacker's first roll frame from the same clip start)
    are given, `p_punish` and `punish_hp`: an evading dodger punishes when his R1 cancel plus his
    strike comes before the attacker can roll, and he ends within `PUNISH_REACH_M` of where the
    attacker's root motion (`advance` m forward) left him. With `run`, a dodger who ends further
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
    reaches him standing."""
    import numpy as np
    moves = moves if moves is not None else roll_motion()

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
    planned = plan(moves, [h < 0 for h in hs], cue, floor) if plan is not None else None
    bh, nm = int(base_h[0]), len(moves)
    for k, m in enumerate(moves):
        h = hs[k]
        # The catches read as a list (kept beside them in `catches`), the punish counts too
        # (below): the same numbers.
        hl = None if catches is None else catches.get(('list', dist, k))
        if hl is None:
            hl = h.tolist()
            if catches is not None:
                catches[('list', dist, k)] = hl
        nh = len(h)
        ready = m['ready'] if m['ready'] is not None else len(m['path'])
        x, z = m['path'][min(int(ready), len(m['path']) - 1)]
        gap = math.hypot(x, dist + z - advance)
        near = run or gap <= PUNISH_REACH_M
        close = max(0.0, gap - PUNISH_REACH_M) * _neutral().frames_per_metre() if run else 0.0
        punishes = ordered is not None and my_roll is not None and near and len(ordered)
        # The share of strikes + close below a bound: a binary search counts what the mean of the
        # comparison counted, the same number, without a pass over the strikes per press.
        arrive = ordered + close if punishes else None
        presses = planned[k] if planned is not None else dodge_presses(h < 0, cue, floor=floor)
        # Each press's grid index, weight and start (`_press_reads`, kept per plan's list).
        idx, ws, starts = _press_reads(presses, planned is not None)
        # Every press's count of strikes in time at once (the same numbers one search per press
        # gives), summed in the same order below.
        counts = np.searchsorted(arrive, my_roll - starts - ready,
                                 side='left').tolist() if punishes and presses else None
        na = len(arrive) if punishes else None
        for j in range(len(idx)):
            i = idx[j]
            hit = hl[i] if 0 <= i < nh else bh
            w = ws[j] / nm
            if hit >= 0:
                value += w * hit_value[hit]
                continue
            p_evade += w
            if punishes:
                p_punish += w * (counts[j] / na)
    return {'value': value, 'base': base, 'p_evade': p_evade, 'p_punish': p_punish,
            'punish_hp': p_punish * PUNISH_HP}


def sequence_outcome(bursts, dist, cue=0.0, floor=0.0, families=None, my_roll=None, strikes=None,
                     run=False, catches=None, caught=None, hp=None, plan=None):
    """`reaction_outcome` for an attack whose hits come in separate bursts (`bursts`: [(geoms,
    hit_value)] in time order), each dodged on its own. Caught by a burst he takes what its
    `hit_value` says; evading it he meets the next one, no earlier than the end of the last burst
    he dodged plus his move's recovery past its invincibility (`INFERRED`: he timed that dodge to
    cover the burst's end; a burst that does not reach him standing is not dodged and holds
    nothing back). He keeps to one family of moves (`families`: the roll's four directions, and the
    jump when a hit is `AIR`) and, knowing the attack, takes the one that leaves the attacker least
    (`value` - `punish_hp`). The punish is the last burst's that reaches him, when he evaded every
    one before it. `catches`, a dict, hands `reaction_outcome` one memo per (family index, burst
    index).

    `hit_value` credits a caught defender with every later hit, as he never left. With `caught`
    (a `CaughtModel` over the same geometries and the worth they carry: `caught.worth(dist)`, one
    number per geometry in burst order) each later hit and tick is worth what lands on him while
    he takes his hit reactions and then leaves (`CaughtModel`). `hp` caps what one caught defender
    takes at his health (the punish is not capped).

    `plan` (`reaction_outcome`'s) times his dodge of the first burst that reaches him standing, in
    each family; every later burst is dodged as before."""
    if families is None:
        families = [roll_motion()]
        if any(g is not None and g.get('AIR') for gs, _ in bursts for g in gs):
            families.append([jump_motion()])
    if caught is not None or hp is not None:
        out, at = [], 0
        for geoms, hit_value in bursts:
            if caught is not None:
                # Each catch is played only when `reaction_outcome` reads it.
                hv = caught.lazy(dist, at, len(geoms), hp)
            else:
                hv = [min(v, hp) for v in hit_value]
            at += len(geoms)
            out.append((geoms, hv))
        bursts = out
    best = None
    for fi, moves in enumerate(families):
        rec = max(max(0.0, (m['dodge'] or len(m['path'])) - m['iframes']) for m in moves)
        alive, value, punish, base, t = 1.0, 0.0, 0.0, None, floor
        for bi, (geoms, hit_value) in enumerate(bursts):
            o = reaction_outcome(geoms, hit_value, dist, cue, moves, my_roll=my_roll, strikes=strikes, floor=t,
                                 run=run, catches=None if catches is None else catches.setdefault((fi, bi), {}),
                                 plan=plan if base is None else None)
            if o['base'] > 0:
                base = o['base'] if base is None else base
                value += alive * o['value']
                punish = alive * o['p_punish']
                alive *= o['p_evade']
                # Only a burst he dodged holds back his next dodge: one that does not reach him
                # standing is not dodged (`reaction_outcome` returns at once).
                t = max([t] + [g['T'].max() + rec for g in geoms if g is not None])
        out = {'value': value, 'base': base or 0.0, 'p_evade': alive if base else 0.0, 'p_punish': punish,
               'punish_hp': punish * PUNISH_HP, 'moves': moves[0]['anim']}
        if best is None or out['value'] - out['punish_hp'] < best['value'] - best['punish_hp']:
            best = out
    return best


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


def _volley_burst(geoms, units, dist, cue, moves, my_roll, strikes, floor, run, memo, plan=None):
    """One burst of `volley_outcome`: None when no hit of it touches a defender who stands still,
    else {'p': {unit: chance a hit of it touches him}, 'p_clear': chance none does, 'p_punish':
    chance he evades it and punishes, 'still': the units that touch him standing still}. He picks
    his dodge as `reaction_outcome` does, to escape every hit of the burst (`dodge_presses`, or
    `plan` as `reaction_outcome` takes it)."""
    import numpy as np

    def each(key, move, starts):
        if memo is None:
            return _catches_each(geoms, dist, move, starts)
        if key not in memo:
            memo[key] = _catches_each(geoms, dist, move, starts)
        return memo[key]
    still = np.isfinite(each(('base', dist), None, [0.0])[0])
    if not still.any():
        return None
    live = [g['T'].max() for g in geoms if g is not None]
    grid = np.arange(0.0, max(live) + 1.0, DODGE_STEP)
    strikes = None if strikes is None else np.asarray(strikes, float)
    p = {u: 0.0 for u in units}
    clear = p_punish = 0.0
    reads = [np.isfinite(each((dist, k), m, grid)) for k, m in enumerate(moves)]
    planned = plan(moves, [~c.any(axis=1) for c in reads], cue, floor) if plan is not None else None
    for k, m in enumerate(moves):
        caught = reads[k]
        hit_any = caught.any(axis=1)
        ok = ~hit_any
        ready = m['ready'] if m['ready'] is not None else len(m['path'])
        x, z = m['path'][min(int(ready), len(m['path']) - 1)]
        gap = math.hypot(x, dist + z)
        near = run or gap <= PUNISH_REACH_M
        close = max(0.0, gap - PUNISH_REACH_M) * _neutral().frames_per_metre() if run else 0.0
        punishes = strikes is not None and my_roll is not None and near and len(strikes)
        # The share of strikes + close below a bound, by binary search (the same count as the mean
        # of the comparison `reaction_outcome` takes).
        arrive = np.sort(strikes + close) if punishes else None
        who = {}
        for s, w in (planned[k] if planned is not None else dodge_presses(ok, cue, floor=floor)):
            i = int(round(s / DODGE_STEP))
            i = i if 0 <= i < len(caught) else -1
            w /= len(moves)
            if hit_any[i] if i >= 0 else True:
                if i not in who:
                    who[i] = {units[j] for j in np.nonzero(caught[i] if i >= 0 else still)[0]}
                for u in who[i]:
                    p[u] += w
                continue
            clear += w
            if punishes:
                p_punish += w * (int(np.searchsorted(arrive, my_roll - s - ready, side='left')) / len(arrive))
    return {'p': p, 'p_clear': clear, 'p_punish': p_punish, 'still': {units[j] for j in np.nonzero(still)[0]}}


def volley_outcome(bursts, worth, dist, cue=0.0, floor=0.0, families=None, my_roll=None, strikes=None,
                   run=False, catches=None, hp=None, plan=None):
    """`sequence_outcome` for an attack made of independent threats: the shots of a fan that keep
    their own hit lists (Glintblade Phalanx's blades, Ancient Death Rancor's skulls). `bursts` is
    [(geoms, units)] in time order, `units[i]` the threat `geoms[i]` belongs to (one threat can fly
    in several bursts, one leg in each), `worth[unit]` what it lands, once. Each burst is dodged on its own as
    in `sequence_outcome`, no earlier than the end of the last one he dodged plus his move's
    recovery past its invincibility, and only a burst that touches him standing still is dodged. A
    threat lands the first time one of its legs touches him; being caught by one threat neither
    makes the others land nor stops him dodging them (`INFERRED`), and each burst places him back
    at `dist`, as `sequence_outcome` does, which suits shots fixed when the cast fires and not a
    chain that re-aims after his roll (the spell kit flies those against the roll,
    `fly_dynamic`). `value` sums each threat's worth times the chance it lands; `base` is what lands
    on a defender who never dodges; `p_evade` is the chance nothing lands, and the punish is the
    last reaching burst's, when nothing landed before it, so it is paid once per cast. Knowing the
    attack he keeps to the family of moves that leaves the attacker least. `catches`, a dict,
    memoises one `_catches_each` read per (family, burst, dodge). `hp` caps `value` and `base` at
    the defender's health: exact when the threats' worths sum to no more than it, else a bound
    (the chance of each set of threats landing together is not carried). `plan` as
    `sequence_outcome`'s: it times the dodge of the first burst that reaches him standing."""
    if families is None:
        families = [roll_motion()]
        if any(g is not None and g.get('AIR') for gs, _ in bursts for g in gs):
            families.append([jump_motion()])
    best = None
    for fi, moves in enumerate(families):
        rec = max(max(0.0, (m['dodge'] or len(m['path'])) - m['iframes']) for m in moves)
        alive = {u: 1.0 for u in worth}
        clear, value, punish, base, reached, t = 1.0, 0.0, 0.0, 0.0, set(), floor
        for bi, (geoms, units) in enumerate(bursts):
            memo = None if catches is None else catches.setdefault((fi, bi), {})
            o = _volley_burst(geoms, units, dist, cue, moves, my_roll, strikes, t, run, memo,
                              plan if not reached else None)
            if o is not None:
                for u in sorted(o['still'] - reached):
                    reached.add(u)
                    base += worth[u]
                for u, pu in o['p'].items():
                    value += alive[u] * pu * worth[u]
                    alive[u] *= 1.0 - pu
                punish = clear * o['p_punish']
                clear *= o['p_clear']
                t = max([t] + [g['T'].max() + rec for g in geoms if g is not None])
        if hp is not None:
            value, base = min(value, hp), min(base, hp)
        out = {'value': value, 'base': base, 'p_evade': clear if base else 0.0, 'p_punish': punish,
               'punish_hp': punish * PUNISH_HP, 'moves': moves[0]['anim']}
        if best is None or out['value'] - out['punish_hp'] < best['value'] - best['punish_hp']:
            best = out
    return best


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
#     from the dodge cancel, placed back at the distance (as every burst places him), and none of
#     the follow-up's samples before then count again (the roll judged them). His floor is taken
#     on the 60 Hz update grid (`DODGE_STEP`).
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


def _burst_presses(geoms, dist, moves, cue, floor, memo):
    """`reaction_outcome`'s reads of one burst without its worth: (index in `geoms` of the geometry
    that catches a defender standing at `dist`, or -1; [(move index, dodge start, weight, index of
    the geometry that catches that dodge or -1)]). `memo` as `reaction_outcome`'s `catches`.
    `geoms` may hold None (no samples), which keeps the others' indices."""
    import numpy as np

    def caught(key, move, starts):
        if key not in memo:
            memo[key] = _catches(geoms, dist, move, starts)
        return memo[key]
    base_h = int(caught(('base', dist), None, [0.0])[0][0])
    live = [g['T'].max() for g in geoms if g is not None]
    if not live or base_h < 0:
        return base_h, []
    grid = np.arange(0.0, max(live) + 1.0, DODGE_STEP)
    out = []
    for k, m in enumerate(moves):
        h, _ = caught((dist, k), m, grid)
        for s, w in dodge_presses(h < 0, cue, floor=floor):
            i = int(round(s / DODGE_STEP))
            out.append((k, s, w / len(moves), int(h[i]) if 0 <= i < len(h) else base_h))
    return base_h, out


def _punish_arrivals(m, dist, strikes, run):
    """(move `m`'s R1 ready frame, the sorted frames the pool's strikes take to land from it, or
    None when he does not punish from there): `reaction_outcome`'s rule, `advance` 0."""
    import numpy as np
    ready = m['ready'] if m['ready'] is not None else len(m['path'])
    x, z = m['path'][min(int(ready), len(m['path']) - 1)]
    gap = math.hypot(x, dist + z)
    if strikes is None or not len(strikes) or not (run or gap <= PUNISH_REACH_M):
        return ready, None
    close = max(0.0, gap - PUNISH_REACH_M) * _neutral().frames_per_metre() if run else 0.0
    return ready, np.sort(np.asarray(strikes, float) + close)


def _first_touch(geoms, dist, move, starts, after):
    """`_catches` with the samples before `after[i]` left out for dodge start `starts[i]` (the
    earlier dodge they fell in judged them): (index of the hit that first touches him outside his
    invincibility, its time) per start, -1 / inf when none does. The reference `_TouchTable` is
    checked against (selftest)."""
    import numpy as np
    radius = _pose()[1].PUSH_CAPSULE_RADIUS
    starts = np.asarray(starts, float)[:, None]
    after = np.asarray(after, float)[:, None]
    best_t = np.full(starts.shape[0], math.inf)
    best_h = np.full(starts.shape[0], -1)
    path = np.array(move['path'], float)
    px, pz, n = path[:, 0], path[:, 1], len(path)
    for h, g in enumerate(geoms):
        if g is None:
            continue
        u = g['T'][None, :] - starts
        k = np.clip(np.floor(u).astype(int), 0, n - 1)
        moving = u >= 0
        xr = np.where(moving, px[k], 0.0)
        d = dist + np.where(moving, pz[k], 0.0)
        immune = not move.get('air') or bool(g.get('AIR'))
        vuln = ~(moving & (u < move['iframes']) & immune) & (g['T'][None, :] >= after - 1e-9)
        held = _held(g, dist, move, u, starts, n, pz, radius)
        if held is None:
            held = np.maximum(0.0, g['PK'][None, :] - (d - 2 * radius))
        touch = vuln & (np.hypot(g['X'][None, :] - xr, g['Z'][None, :] + held + d) < g['R'][None, :])
        tt = np.where(touch, g['T'][None, :], math.inf).min(axis=1)
        better = tt < best_t
        best_t[better], best_h[better] = tt[better], h
    return best_h, best_t


def _touch_rows(g, dist, move, starts):
    """[dodge start, sample]: whether sample of `g` touches him on `move` (None: standing) started at
    each of `starts`, as `_catches` tests it, element by element."""
    import numpy as np
    radius = _pose()[1].PUSH_CAPSULE_RADIUS
    starts = np.asarray(starts, float)[:, None]
    u = g['T'][None, :] - starts
    if move is None:
        xr, d, vuln, n, pz = 0.0, np.full(u.shape, float(dist)), np.ones(u.shape, bool), 1, None
    else:
        path = np.array(move['path'], float)
        px, pz, n = path[:, 0], path[:, 1], len(path)
        k = np.clip(np.floor(u).astype(int), 0, n - 1)
        moving = u >= 0
        xr = np.where(moving, px[k], 0.0)
        d = dist + np.where(moving, pz[k], 0.0)
        immune = not move.get('air') or bool(g.get('AIR'))
        vuln = ~(moving & (u < move['iframes']) & immune)
    held = _held(g, dist, move, u, starts, n, pz, radius)
    if held is None:
        held = np.maximum(0.0, g['PK'][None, :] - (d - 2 * radius))
    return vuln & (np.hypot(g['X'][None, :] - xr, g['Z'][None, :] + held + d) < g['R'][None, :])


def _may_touch(g, dist, move):
    """Per sample of `g`: False where no dodge start on `move` (None: standing) can make it touch
    him at `dist` in `_catches`' test. A bound: his root keeps to the box of the move's path and
    the standing spot, and his body's hold-back is at most what his nearest place in it gives, so
    a sample whose disc misses that box by its radius never touches; only those are left out."""
    import numpy as np
    radius = _pose()[1].PUSH_CAPSULE_RADIUS
    if move is None:
        xs, zs = np.zeros(1), np.zeros(1)
    else:
        path = np.array(move['path'], float)
        xs, zs = np.append(path[:, 0], 0.0), np.append(path[:, 1], 0.0)
    dlo, dhi = dist + zs.min(), dist + zs.max()
    hmax = np.maximum(0.0, g['PK'] - (dlo - 2 * radius))
    dx = np.maximum(0.0, np.maximum(xs.min() - g['X'], g['X'] - xs.max()))
    lo_z, hi_z = g['Z'] + dlo, g['Z'] + dhi + hmax
    dz = np.where(lo_z > 0, lo_z, np.where(hi_z < 0, -hi_z, 0.0))
    return np.hypot(dx, dz) < g['R'] * (1.0 + 1e-9) + 1e-9


class _TouchTable:
    """Every `_catches` read of geometries `geoms` at `dist` on `move` (None: standing, which no
    start changes) for dodge starts on the update grid from `lo` to `hi`: per start, the first
    sample that touches him at or after any cut, in time order with ties to the lower geometry
    index (what `_catches` returns). Samples `_may_touch` rules out are left out. Starts past `hi`
    see every sample before them, so they read the standing table `stand`."""

    def __init__(self, geoms, dist, move, lo=0.0, hi=0.0, stand=None):
        import numpy as np
        self.lo, self.move, self.stand = float(lo), move, stand
        rows = int(round((hi - lo) / DODGE_STEP)) + 1 if move is not None else 1
        grid = self.lo + DODGE_STEP * np.arange(rows) if move is not None else np.zeros(1)
        Ts, gids, touches = [], [], []
        for h, g in enumerate(geoms):
            if g is None:
                continue
            keep = _may_touch(g, dist, move)
            if not keep.any():
                continue
            sub = {k: (v[keep] if k in ('T', 'X', 'Z', 'R', 'PK', 'TL') else v) for k, v in g.items()}
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
            j = self.nxt[np.minimum(r, self.nxt.shape[0] - 1), c]
            if past.any():
                h, t = self.stand.first(starts[past], cuts[past])
                out_h, out_t = self.gid[j].copy(), self.T[j].copy()
                out_h[past], out_t[past] = h, t
                return out_h, out_t
        return self.gid[j], self.T[j]


def _grid(x):
    return round(x / DODGE_STEP) * DODGE_STEP


class FollowupModel:
    """The reads of an attack made of an opening and a follow-up pressed in a window, worth left
    open (the rules above). `opening` and `follow` are [(contact geometry, index)], the follow-up
    at its earliest press, real frame `press` from the attack's start; the index is the geometry's
    place in the whole attack's time order (`CaughtModel`, `_bursts`). `delays` are the press
    delays the attacker may take (real frames, the first 0), `my_roll` his first roll frame at the
    earliest press, `strikes` the pool's punish strike frames. `outcome` scores one (distance,
    floor) for given worths; one model serves every build that flies the same shapes."""

    def __init__(self, opening, follow, press, delays, my_roll, strikes=None, run=True):
        import numpy as np
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
        self.families = [roll_motion()]
        if any(g is not None and g.get('AIR') for g, _ in list(opening) + list(follow)):
            self.families.append([jump_motion()])
        self.react = attacker_reactions()
        self._memo, self._rolls, self._stages, self._shapes, self._arrive = {}, {}, {}, {}, {}
        self._tabs = {}

    def _tab(self, dist, move, bi=None):
        """The `_TouchTable` of the whole follow-up (`bi` None) or of its burst `bi` at `dist` on
        `move` (None: standing), kept until `release(dist)`. The whole follow-up's grid reaches back
        past the longest delay, since a late press is read as his dodge started that much early."""
        key = (round(dist, 2), None if move is None else id(move), bi)
        if key not in self._tabs:
            gs = self.follow if bi is None else self.follow_bursts[bi][0]
            if move is None:
                self._tabs[key] = _TouchTable(gs, dist, None)
            else:
                top = max(g['T'].max() for g in gs) + 1.0
                lo = 0.0 if bi is not None else -(math.ceil(float(self.delays[-1])) + 1.0)
                self._tabs[key] = _TouchTable(gs, dist, move, lo, _grid(top + 0.5), self._tab(dist, None, bi))
        return self._tabs[key]

    def release(self, dist):
        """Drop the touch tables at `dist` (the shapes and stages read from them stay)."""
        d = round(dist, 2)
        for key in [k for k in self._tabs if k[0] == d]:
            del self._tabs[key]

    def _shares(self, m, s, dist, rolls):
        """The share of the pool's strikes that punish a dodge of move `m` started at `s` when the
        attacker can roll at `rolls` (`_punish_arrivals`); `s` and `rolls` broadcast."""
        import numpy as np
        key = (id(m), round(dist, 2))
        if key not in self._arrive:
            self._arrive[key] = _punish_arrivals(m, dist, self.strikes, self.run)
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

    def _rolled(self, dist, k, move, starts, afters=None):
        """Per dodge start in `starts` (the earliest-press timeline): (follow-up index that catches
        him on move `move` before its dodge cancel, or -1), the follow-up's samples before the
        matching `afters` entry left out (an earlier dodge judged them). One vectorized read for
        every start not seen before."""
        import numpy as np
        memo = self._rolls.setdefault((round(dist, 2), id(move)), {})
        afters = [-math.inf] * len(starts) if afters is None else list(afters)
        keys = list(zip(starts, afters))
        need = sorted({q for q in keys if q not in memo})
        if need:
            h, t = self._tab(dist, move).first([s for s, _ in need], [a for _, a in need])
            end = move['dodge'] if move['dodge'] is not None else len(move['path'])
            for (s, a), hh, tt in zip(need, h, t):
                memo[(s, a)] = int(hh) if hh >= 0 and tt < s + end else -1
        return np.array([memo[q] for q in keys], int)

    def _follow_presses(self, fi, dist, bi, floor, cut, cue, t):
        """`_burst_presses` of follow-up burst `bi` met from `t`, its samples before `floor` left
        out with `cut`: (its geometries as met, their indices, base index, presses), or None when
        none of it is left."""
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
        memo = {} if trimmed else self._memo.setdefault(('follow', fi, bi, None), {})
        if ('base', dist) not in memo:
            memo[('base', dist)] = self._tab(dist, None, bi).first([0.0], [at])
        grid = np.arange(0.0, max(g['T'].max() for g in gt if g is not None) + 1.0, DODGE_STEP)
        for k, m in enumerate(self.families[fi]):
            if (dist, k) not in memo:
                memo[(dist, k)] = self._tab(dist, m, bi).first(grid, np.full(len(grid), at))
        base_h, presses = _burst_presses(gt, dist, self.families[fi], cue, t, memo)
        return gt, ix, base_h, presses

    def _stage(self, fi, dist, floor, cut, cue):
        """The follow-up (earliest-press timeline) met from `floor`, its dodges timed from `cue` (0:
        he knows its timing, as `sequence_outcome`'s defender does; `press`: he reacts to the
        press): (per follow-up geometry, the weight of his being caught first by it; the chance he
        evades it all; the chance he then punishes; whether any of it reaches him standing). With
        `cut` its samples before `floor` are left out (the roll he is coming out of judged them)."""
        import numpy as np
        key = ('stage', fi, round(dist, 2), floor, cut, cue)
        if key in self._stages:
            return self._stages[key]
        moves = self.families[fi]
        rec = max(max(0.0, (m['dodge'] or len(m['path'])) - m['iframes']) for m in moves)
        coef = np.zeros(len(self.follow))
        alive, pp, reached, t = 1.0, 0.0, False, floor
        for bi in range(len(self.follow_bursts)):
            got = self._follow_presses(fi, dist, bi, floor, cut, cue, t)
            if got is None or got[2] < 0:
                continue
            gt, ix, _, presses = got
            reached = True
            ks, ss, ws, hits = (np.array([p[j] for p in presses]) for j in range(4))
            for j in np.nonzero(hits >= 0)[0]:
                coef[self.where[ix[hits[j]]]] += alive * ws[j]
            free = hits < 0
            ev, pb = float(ws[free].sum()), 0.0
            if self.my_roll is not None:
                for k in np.unique(ks[free]):
                    sel = free & (ks == k)
                    pb += float(ws[sel] @ self._shares(moves[int(k)], ss[sel], dist, self.my_roll))
            pp = alive * pb
            alive *= ev
            t = max([t] + [g['T'].max() + rec for g in gt if g is not None])
        out = (coef, alive if reached else 1.0, pp, reached)
        self._stages[key] = out
        return out

    def _late(self, fi, dist, floor, cut):
        """[per press delay: (as `_stage`)], the follow-up pressed that late against a defender free
        from `floor` (earliest-press timeline, samples before it left out with `cut`) who planned
        for the press at once. At no delay he meets it as planned (`_stage` from the attack's
        start). Late, his planned dodge of its first burst that reaches him (the same presses)
        meets the follow-up that much later: it catches him when it touches him outside that
        dodge's invincibility before its dodge cancel; else he is free from there and reacts to the
        press (`_stage` from `press`, `INFERRED`: he reads no delay ahead). If nothing reaches him
        after that dodge, it is his last, and his punish is read from it against the attacker's
        later roll."""
        import numpy as np
        key = ('late', fi, round(dist, 2), floor, cut)
        if key in self._stages:
            return self._stages[key]
        D = len(self.delays)
        out = [self._stage(fi, dist, floor, cut, 0.0)]
        plan = None
        for bi in range(len(self.follow_bursts)):
            got = self._follow_presses(fi, dist, bi, floor, cut, 0.0, floor)
            if got is not None and got[2] >= 0:
                plan = got[3]
                break
        if plan is None:
            # Nothing of it is left to plan for (his roll judged it all): pressed late it may reach
            # him after his floor, and he reacts to the press.
            out += [self._stage(fi, dist, _grid(floor - d), cut, self.press) for d in self.delays[1:]]
            self._stages[key] = out
            return out
        moves = self.families[fi]
        merged = {}
        for k, s, w, _ in plan:
            merged[(k, round(s, 6))] = merged.get((k, round(s, 6)), 0.0) + w
        coef, pe, pp = np.zeros((D, len(self.follow))), np.zeros(D), np.zeros(D)
        late = self.delays[1:]
        stage = {}
        for k in sorted({k for k, _ in merged}):
            m = moves[k]
            end = m['dodge'] if m['dodge'] is not None else len(m['path'])
            items = [(s, w) for (kk, s), w in merged.items() if kk == k]
            starts = np.concatenate([_grid(s) - late for s, _ in items]).tolist()
            afters = np.tile(floor - late if cut else np.full(D - 1, -math.inf), len(items)).tolist()
            hs = self._rolled(dist, k, m, starts, afters).reshape(len(items), D - 1)
            for j, (s, w) in enumerate(items):
                floors = (np.round((s + end - late) / DODGE_STEP) * DODGE_STEP).tolist()
                own = self._shares(m, s, dist, self.my_roll + late) if self.my_roll is not None else np.zeros(D - 1)
                for d in range(1, D):
                    h = hs[j, d - 1]
                    if h >= 0:
                        coef[d, h] += w
                        continue
                    fl = floors[d - 1]
                    if fl not in stage:
                        stage[fl] = self._stage(fi, dist, fl, True, self.press)
                    st = stage[fl]
                    if st[3]:
                        coef[d] += w * st[0]
                        pe[d] += w * st[1]
                        pp[d] += w * st[2]
                    else:
                        pe[d] += w
                        pp[d] += w * own[d - 1]
        out += [(coef[d], float(pe[d]), float(pp[d]), True) for d in range(1, D)]
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
        rec = max(max(0.0, (m['dodge'] or len(m['path'])) - m['iframes']) for m in moves)
        D = len(self.delays)
        reach = [bi for bi, (gs, _) in enumerate(self.open_bursts) if self._base('open', fi, bi, gs, dist) >= 0]
        early, alive, t, base, last = [], 1.0, floor, None, None
        for bi in reach:
            gs, ix = self.open_bursts[bi]
            base_h, presses = _burst_presses(gs, dist, moves, 0.0, t, self._memo[('open', fi, bi)])
            base = ix[base_h] if base is None else base
            if bi == reach[-1]:
                last = presses
                break
            ev = 0.0
            for k, s, w, hit in presses:
                if hit >= 0:
                    early.append((ix[hit], alive * w))
                else:
                    ev += w
            alive *= ev
            t = max([t] + [g['T'].max() + rec for g in gs])
        stages, sids = [], {}

        def sid_of(fl, cut):
            """The index in `stages` of `_late(fl, cut)`'s first delay; the others follow it."""
            if (fl, cut) not in sids:
                sids[(fl, cut)] = len(stages)
                stages.extend(self._late(fi, dist, fl, cut))
            return sids[(fl, cut)]
        out = {'early': early, 'alive': alive, 'base': base}
        if last is None:
            # No opening burst reaches him standing: no roll to see, the press at once, met from his
            # own floor.
            if base is None:
                for bi, (gs, ix) in enumerate(self.follow_bursts):
                    b = self._base('follow', fi, bi, gs, dist)
                    if b >= 0:
                        base = ix[b]
                        break
            out['base'] = base
            caught = np.full((1, D), -1, int)
            sid = (sid_of(_grid(t), False) + np.arange(D))[None, :]
            own = np.zeros((1, D))
            w, starts = np.ones(1), np.full(1, math.inf)
        else:
            gs, ix = self.open_bursts[reach[-1]]
            merged = {}
            for k, s, wt, hit in last:
                mk = (k, round(s, 6), ix[hit] if hit >= 0 else -1)
                merged[mk] = merged.get(mk, 0.0) + wt
            n = len(merged)
            caught = np.full((n, D), -1, int)
            sid = np.full((n, D), -1, int)
            own = np.zeros((n, D))
            w, starts = np.zeros(n), np.zeros(n)
            rolled = {}
            for i, ((k, s, hit), wt) in enumerate(merged.items()):
                w[i], starts[i] = wt, s
                if hit >= 0:
                    caught[i, :] = hit
                else:
                    rolled.setdefault(k, []).append(i)
            items = list(merged)
            for k, rows_k in rolled.items():
                m = moves[k]
                end = m['dodge'] if m['dodge'] is not None else len(m['path'])
                fh = self._rolled(dist, k, m, np.concatenate([_grid(items[i][1]) - self.delays for i in rows_k]).tolist())
                fh = fh.reshape(len(rows_k), D)
                for r_, i in enumerate(rows_k):
                    hit = fh[r_] >= 0
                    caught[i, hit] = self.follow_index[fh[r_, hit]]
                    sid[i, ~hit] = sid_of(_grid(_grid(items[i][1]) + end), True) + np.nonzero(~hit)[0]
                    if self.my_roll is not None:
                        own[i] = self._shares(m, items[i][1], dist, self.my_roll + self.delays)
        if not stages:
            stages.append((np.zeros(len(self.follow)), 1.0, 0.0, False))
        # Per press and attacker reaction: the first delay he can press at having seen it, or -1
        # when it shows only after the window closes (`outcome`).
        R = np.array([r for r, _ in self.react], float)
        seen_at = starts[:, None] + R[None, :] - self.press
        lo = np.searchsorted(self.delays, seen_at - 1e-9, side='left')
        lo = np.where(lo < D, lo, -1)
        out.update(w=w, starts=starts, seen_at=seen_at, lo=lo, caught=caught, sid=sid, own=own,
                   C=np.array([s_[0] for s_ in stages], float).reshape(len(stages), len(self.follow)),
                   PE=np.array([s_[1] for s_ in stages], float), PP=np.array([s_[2] for s_ in stages], float),
                   R=np.array([s_[3] for s_ in stages], bool))
        # The worths `outcome` can read, per delay: what catches him there, and the follow-up
        # geometries a stage met there weighs; the others are multiplied by 0 or not read.
        supp = [set(self.follow_index[np.nonzero(row)[0]].tolist()) for row in out['C']]
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

    def outcome(self, dist, floor, worth, see=True, read=False):
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
        every follow-up at once (only `worth(0)` is read)."""
        import numpy as np
        D = len(self.delays) if see else 1
        shapes = [self._shape(dist, floor, fi) for fi in range(len(self.families))]
        # Only the worths a shape can read are asked for (`_shape` 'need'); the rest stay 0, which
        # nothing reads or which a weight of 0 multiplies.
        need = [set() for _ in range(D)]
        for sh in shapes:
            for d in range(D):
                need[d] |= sh['need'][d]
            for d in (range(D) if read and see else (0,)):
                need[d] |= sh['fixed']
        WD = np.zeros((D, self.size))                                             # [delay, index]
        for d in range(D):
            if need[d]:
                idx = np.array(sorted(need[d]), int)
                WD[d, idx] = worth(d, idx)
        WF = WD[:, self.follow_index]
        cols = np.arange(D)[None, :]
        wq = np.array([w for _, w in self.react], float)
        best = None
        for fi, moves in enumerate(self.families):
            sh = shapes[fi]
            caught, sid = sh['caught'][:, :D], sh['sid'][:, :D]
            sidc = np.maximum(sid, 0)
            reached = (sid >= 0) & sh['R'][sidc]
            SV = sh['C'] @ WF.T                                                  # [stage, delay]
            val = np.where(caught >= 0, WD[cols, np.maximum(caught, 0)],
                           np.where(reached, SV[sidc, cols], 0.0))
            pun = np.where(caught >= 0, 0.0, np.where(reached, sh['PP'][sidc], sh['own'][:, :D]))
            eva = np.where(caught >= 0, 0.0, np.where(reached, sh['PE'][sidc], 1.0))
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
                base = float(WD[dr, sh['base']]) if sh['base'] is not None else 0.0
                out = {'value': sum(x * WD[dr, h] for h, x in sh['early']) + a * float(sh['w'] @ fv), 'base': base,
                       'p_evade': a * float(sh['w'] @ fe) if base else 0.0, 'p_punish': a * float(sh['w'] @ fp),
                       'delayed': a * float(sh['w'] @ ((idx > 0) * wq).sum(axis=1)), 'default': float(self.delays[dr])}
                out.update(punish_hp=out['p_punish'] * PUNISH_HP, moves=moves[0]['anim'])
                if fam is None or out['value'] - out['punish_hp'] > fam['value'] - fam['punish_hp'] + 1e-12:
                    fam = out
            if best is None or fam['value'] - fam['punish_hp'] < best['value'] - best['punish_hp']:
                best = fam
        return best


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
#     defender's own) and plays its damage level's reaction (`caught_branch`: the poise-break
#     level, or SpEffect 6352's while poise holds, `er-mechanics-frame-advantage.reaction_level`);
#     a reaction that locks carries him back by `displacement_fn` (knockback, `VERIFIED`, plus the
#     damage clip's root motion, `MEASURED`) and holds him until its roll cancel (DamageCount
#     chaining included) or its move cancel (`TAE` damage clips, `reaction`); level 0 (the
#     additive flinch) neither holds nor moves him and leaves a running reaction running. Two hits
#     in one update are one reaction (`_update`);
#   * when the reaction lets him he leaves straight away from the attacker: by the medium roll
#     that carries him furthest back (`roll_motion`, its invincibility included) from its roll
#     cancel and then at run speed, or at run speed (`er-mechanics-neutral.frames_per_metre`) from
#     its move cancel, whichever lands the smaller share of the attack's hits;
#   * a later hit lands only where its own geometry touches him on that path (held back by his
#     body as `_catches` holds it); a hit list that re-hits every `dmgHitRecordLifeTime`
#     (`CAUGHT` `lists`, the `full` rule's ticks) lands each tick only while a sample of its
#     geometry within one update of the tick touches him.
# `INFERRED`: a geometry with several hit lists starts them all at its first touch and reacts as
# its list with the most poise; the instances of one list (a fan, interval children) tick
# together; he leaves along the attack's axis, not to the side; the knockback left when he cancels
# into his escape is dropped.
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


def caught_list(t, atk_row, poise, n=1, rec_s=0.0, weight=1.0):
    """One hit list of a geometry's `CAUGHT` record: `n` hits (the `full` count of one instance),
    `rec` real frames apart (`dmgHitRecordLifeTime`), `w` the share of the geometry's worth one of
    its hits carries (a geometry's lists sum n x w to 1), `poise` per hit (menu units x
    `saRate`), and its two reaction branches (`caught_branch`)."""
    return {'n': int(max(1, n)), 'rec': float(rec_s) * TAE_FPS, 'w': float(weight), 'poise': float(poise or 0.0),
            'atk': atk_row, 'react': {True: caught_branch(t, atk_row, True), False: caught_branch(t, atk_row, False)}}


def caught_shape(g):
    """What `CaughtModel` reads of a geometry besides its arrays and its worth weights: one tuple
    per list (`n`, `rec`, `poise`, `atk`), None without a `CAUGHT` record."""
    c = g.get('CAUGHT') if g is not None else None
    return None if c is None else [(l['n'], round(l['rec'], 4), round(l['poise'], 4), l['atk']) for l in c['lists']]


def _escapes():
    """The two ways a caught defender leaves (`CaughtModel`): ('roll', the medium roll that ends
    furthest back), ('run', None)."""
    rolls = roll_motion()
    back = max(rolls, key=lambda m: m['path'][min(len(m['path']) - 1, int(m['dodge'] or len(m['path'])))][1]) \
        if rolls else None
    return ([('roll', back)] if back else []) + [('run', None)]


class CaughtModel:
    """A defender caught by one geometry of an attack, played out (the rules above). `geo` is
    [(contact geometry, frame)], every geometry with a `CAUGHT` record ({'lists': [`caught_list`],
    'wf': its share of its frame's worth}); `poise` is the defender's. `landed(dist)` is, per
    catching geometry h (in `_bursts` order), the share of each list's hits that land [h][g][l];
    `worth(dist, value)` what that leaves on him per catching geometry, from {frame: worth} (default
    the `value` given here) and each geometry's weights (`weights`, default this `geo`'s). Cached per
    distance."""

    def __init__(self, geo, poise, value=None):
        import numpy as np
        order = sorted(geo, key=lambda e: e[0]['T'].min())
        self.geoms = [g for g, _ in order]
        self.frames = [fr for _, fr in order]
        self.poise = float(poise)
        self.value = value
        self.lists = [g['CAUGHT']['lists'] for g in self.geoms]
        # Each geometry's hit priority record (`HPR`: (key, priority, created)) or None.
        self.hpr = [g.get('HPR') for g in self.geoms]
        # Every geometry's samples, each sorted by time, end to end: one touch test serves them all.
        cols = {k: [] for k in ('T', 'X', 'Z', 'R', 'PK', 'TL')}
        self.bounds, at = [], 0
        for g in self.geoms:
            o = np.argsort(g['T'], kind='stable')
            for k in ('T', 'X', 'Z', 'R', 'PK'):
                cols[k].append(np.asarray(g[k], float)[o])
            cols['TL'].append(np.asarray(g['TL'] if 'TL' in g else g['T'], float)[o])
            self.bounds.append((at, at + len(o)))
            at += len(o)
        for k, v in cols.items():
            setattr(self, k, np.concatenate(v) if v else np.zeros(0))
        self.any_tl = any('TL' in g for g in self.geoms)
        self.push = _pose()[1].PUSH_CAPSULE_RADIUS
        self.mps = 1.0 / _neutral().frames_per_metre()
        self.escapes = _escapes()
        self._roll = {}
        for _, roll in self.escapes:
            if roll is not None:
                path = np.array([p[1] for p in roll['path']], float)
                self._roll[id(roll)] = (path, np.arange(len(path), dtype=float),
                                        min(float(roll['dodge'] or len(path) - 1), len(path) - 1))
        # One geometry whose every list hits once: whoever it catches takes it all, nothing later.
        self.trivial = len(self.geoms) == 1 and all(l['n'] == 1 for l in self.lists[0])
        self._landed = {}
        self._weights = {}
        self._wmemo = {}

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

    def worth_of(self, dist, h, value=None, geo=None):
        """What lands on him when geometry `h` catches him at `dist`, from {frame: worth}. Kept
        for this model's own `value` and `geo` (`with_value` gives a copy its own)."""
        own = value is None and geo is None
        key = (round(dist, 2), h)
        if own and key in self._wmemo:
            return self._wmemo[key]
        value = self.value if value is None else value
        row = self.row(dist, h)
        s = 0.0
        for g, ws in enumerate(self.weights(geo)):
            v = value.get(self.frames[g], 0.0)
            for li, w in enumerate(ws):
                s += row[g][li] * w * v
        if own:
            self._wmemo[key] = s
        return s

    def worth(self, dist, value=None, geo=None):
        """`worth_of` for every catching geometry, in `_bursts` order."""
        return [self.worth_of(dist, h, value, geo) for h in range(len(self.geoms))]

    def lazy(self, dist, start, count, cap=None):
        """`worth_of` for geometries `start` .. `start + count - 1` as a sequence that plays each
        catch only when it is read (what `reaction_outcome` reads of a burst's `hit_value`), each
        capped at `cap`."""
        return _LazyWorth(self, dist, start, count, cap)

    # -- the play ----------------------------------------------------------------------------

    def row(self, dist, h):
        """[per geometry: [per list: share of its hits that land]] when geometry `h` catches him at
        `dist` (rounded to 0.01 m), the better of his two escapes. Cached."""
        key = round(dist, 2)
        rows = self._landed.setdefault(key, {})
        if h not in rows:
            rows[h] = [[1.0] * len(self.lists[0])] if self.trivial else self._best(h, key)
        return rows[h]

    def landed(self, dist):
        """`row` of every catching geometry."""
        return [self.row(dist, h) for h in range(len(self.geoms))]

    def _best(self, h, dist):
        best = None
        for name, roll in self.escapes:
            got = self._play(h, dist, name, roll)
            score = sum(sum(x) / len(x) for x in got if x)
            if best is None or score < best[0] - 1e-12:
                best = (score, got)
        return best[1]

    def _pos(self, segs, T):
        """(his distance ahead of the attacker's start, immune) at real frames `T` (an array) on
        trajectory `segs` [(t0, base, kind, data)], `kind` 'stand', 'hold' (data: the track),
        'run' or 'roll' (data: the roll motion)."""
        import numpy as np
        T = np.asarray(T, float)
        starts = np.array([s[0] for s in segs], float)
        idx = np.clip(np.searchsorted(starts, T, side='right') - 1, 0, len(segs) - 1)
        z = np.zeros(T.shape)
        immune = np.zeros(T.shape, bool)
        for j, (t0, base, kind, data) in enumerate(segs):
            m = idx == j
            if not m.any():
                continue
            u = np.maximum(T[m] - t0, 0.0)
            z[m] = base + self._along(kind, data, u)
            if kind == 'roll':
                immune[m] = u < data['iframes']
        return z, immune

    def _along(self, kind, data, u):
        import numpy as np
        if kind == 'stand':
            return np.zeros(u.shape)
        if kind == 'hold':
            return np.interp(u, np.arange(len(data)), data)
        if kind == 'run':
            return u * self.mps
        path, frames, end = self._roll[id(data)]
        rolled = np.interp(np.minimum(u, end), frames, path)
        return rolled + np.maximum(u - end, 0.0) * self.mps

    def _touch_all(self, segs):
        """Per sample of every geometry (`bounds` slices, each sorted by time): whether it touches
        him on `segs`, held back by his body as `_catches` holds it."""
        import numpy as np
        z, immune = self._pos(segs, self.T)
        zl = self._pos(segs, self.TL)[0] if self.any_tl else z
        held = np.maximum(0.0, self.PK - (zl - 2 * self.push))
        return ~immune & (np.hypot(self.X, self.Z + held + z) < self.R)

    @staticmethod
    def _escape_at(br, count, name):
        roll = br['roll'][max(1, min(count, 4)) - 1] if br['roll'] else None
        move = br['move']
        first, second = (roll, move) if name == 'roll' else (move, roll)
        for v in (first, second, br.get('len')):
            if v is not None:
                return float(v)
        return float(CAUGHT_FRAMES)

    def _play(self, h, dist, name, roll):
        """[per geometry: [per list: share of its hits that land]] when geometry `h` catches him at
        `dist` and he leaves by escape `name` (rules above)."""
        import numpy as np
        G = len(self.geoms)
        got = [[0] * len(ls) for ls in self.lists]
        first = _catches_each([self.geoms[h]], dist, None, [0.0])[0][0]
        tc = float(first) if math.isfinite(first) else float(self.geoms[h]['T'].min())
        segs = [(-math.inf, float(dist), 'stand', None)]
        state = {'cum': 0.0, 'count': 0, 'hold_t0': None, 'hold_update': None}
        started = {}                          # geometry -> its first hit's real frame
        nxt = {}                              # (geometry, list) -> next tick index

        def esc_seg(t0, base):
            return (t0, base, 'roll', roll) if name == 'roll' else (t0, base, 'run', None)

        def land(g, lis, T):
            nonlocal segs
            for li in lis:
                got[g][li] += 1
            l = max((self.lists[g][li] for li in lis), key=lambda l: l['poise'])
            state['cum'] += l['poise']
            broken = state['cum'] > self.poise
            if broken:
                state['cum'] = 0.0
            br = l['react'][broken]
            at = self._pos(segs, [T])[0][0]
            if br['locks']:
                again = state['hold_update'] is not None and _update(T / TAE_FPS) == state['hold_update']
                if not again:
                    state['count'] = min(state['count'] + 1, 4) if br['counts'] else 1
                    state['hold_t0'], state['hold_update'] = T, _update(T / TAE_FPS)
                t0 = state['hold_t0']
                base = self._pos([s for s in segs if s[0] < t0] or segs[:1], [t0])[0][0] if again else at
                segs = [s for s in segs if s[0] < t0] + [(t0, base, 'hold', br['track'])]
                te = T + self._escape_at(br, state['count'], name)
                segs.append(esc_seg(te, float(self._pos(segs, [te])[0][0])))
            elif segs[-1][2] == 'stand':
                # Free at once: a flinch with nothing running leaves him to go.
                segs = segs + [esc_seg(T, float(at))]

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

        take(h)
        land(h, list(range(len(self.lists[h]))), tc)
        started[h] = tc
        for li in range(len(self.lists[h])):
            nxt[(h, li)] = 1
        now = tc
        tch, drawn = None, None
        for _ in range(sum(l['n'] for ls in self.lists for l in ls) + G + 1):
            if drawn is not segs:
                # The path changed (a hit held or freed him): test every sample on the new one.
                tch, drawn = self._touch_all(segs), segs
                cs = np.concatenate([[0], np.cumsum(tch)])
            best = None
            for g in range(G):
                a, b = self.bounds[g]
                T = self.T[a:b]
                if g not in started:
                    if b == a or T[-1] < now - 1e-9 or blocked(g):
                        continue
                    ok = tch[a:b] & (T >= now - 1e-9)
                    if ok.any():
                        c = float(T[np.argmax(ok)])
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
                for li, l in enumerate(self.lists[g]):
                    k = nxt[(g, li)]
                    if k >= l['n'] or l['rec'] <= 0:
                        continue
                    taus = started[g] + l['rec'] * np.arange(k, l['n'])
                    keep = taus >= now - 1e-9
                    lo = a + np.searchsorted(T, taus - CAUGHT_TICK_TOL, side='left')
                    hi = a + np.searchsorted(T, taus + CAUGHT_TICK_TOL, side='right')
                    inside = keep & ((cs[hi] - cs[lo]) > 0)
                    if inside.any():
                        j = int(np.argmax(inside))
                        c = float(taus[j])
                        if best is None or c < best[0]:
                            best = (c, g, [li], k + j)
            if best is None:
                break
            c, g = best[0], best[1]
            if g not in started:
                started[g] = c
                take(g)
                for li in range(len(self.lists[g])):
                    nxt[(g, li)] = 1
            else:
                nxt[(g, best[2][0])] = best[3] + 1
            # Ticks of other lists that fell before this one and were not inside are lost.
            land(g, best[2], c)
            now = c
        return [[x / l['n'] for x, l in zip(row, ls)] for row, ls in zip(got, self.lists)]


class _LazyWorth:
    """`CaughtModel.lazy`: a burst's `hit_value`, each catch played and priced when first read."""

    __slots__ = ('model', 'dist', 'start', 'count', 'cap', 'seen')

    def __init__(self, model, dist, start, count, cap):
        self.model, self.dist, self.start, self.count, self.cap = model, dist, start, count, cap
        self.seen = {}

    def __len__(self):
        return self.count

    def __getitem__(self, i):
        i = int(i)
        if not -self.count <= i < self.count:
            raise IndexError(i)
        i %= self.count
        if i not in self.seen:
            v = self.model.worth_of(self.dist, self.start + i)
            self.seen[i] = v if self.cap is None else min(v, self.cap)
        return self.seen[i]

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
    advance = max((float(g['PK'][np.isfinite(g['PK'])].max()) for g in geoms
                   if np.isfinite(g['PK']).any()), default=0.0)
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
    # er-effects-rs-8uha: the jump family's opener is synthesized, so the stored slots alone drop
    # it; the shares that remain are renormalised, and a supplied jump opener starts at its
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
        steps = skill_evasion(t, bstep, claymore)
        check("Bloodhound's Step: 040080-040083, i-frames f0-10, R1 from 16-17, 4.5-5.2 m",
              ([m['anim'] for m in steps], [m['iframes'] for m in steps], [round(m['ready']) for m in steps],
               [m['distance'] for m in steps]),
              ([f'a756_{a:06d}' for a in (40080, 40081, 40082, 40083)], [10.0] * 4, [17, 16, 16, 16],
               [5.24, 4.72, 4.49, 4.58]), 'TAE a756 + hkx root motion')
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
        # `fly_shots` at each.
        alone = []
        for root in (10430100, 10500000, 10404000, 10408000, 210670000):
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
              "(Carian Phalanx, Rancorcall, Glintstone Stars, Cannon of Haima, Light of Miquella)",
              alone, [True] * 5, 'fly_members: every member flies on its own; fly_shots is fly_members with one')
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
        check('a run the defender reaches in time: he aims at its middle (f35) with the timing error',
              (round(sum(s * w for s, w in presses), 3), round(sum(w for _, w in presses), 6)), (35.0, 1.0),
              'dodge_presses')
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
              'heavier one when weighted (aims f35, f65)',
              tuple(round(sum(s * w for s, w in group_presses([a_, b_], ws, 0.0)), 3) for ws in ([1, 1], [1, 3])),
              (35.0, 65.0), 'group_presses')
        a_c = group_presses([a_, c_], [1, 1], 0.0)
        check('two attacks whose runs overlap: he aims at the middle of the starts that escape both (f38)',
              (round(sum(s * w for s, w in a_c), 3), round(_escape(a_, a_c), 6), round(_escape(c_, a_c), 6)),
              (38.0, 1.0, 1.0), 'group_presses: stretch f36-40')
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
              'ends within 3.69 m: only the forward roll (back 5.7 m, sideways 5.1 m away)',
              (round(reaction_outcome([brief], [1.0], 2.5, my_roll=90.0, strikes=[15.0])['p_punish'], 3),
               round(reaction_outcome([brief], [1.0], 2.5, my_roll=40.0, strikes=[15.0])['p_punish'], 3)),
              (0.25, 0.0), 'reaction_outcome: press ~f25 + ready 20 + strike 15 = 60; roll root motion')
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
        check(f"the Glintstone Dart cast as it opens: its early release (SpEffect {RELEASE_EARLY_SPEFFECT}) is let go "
              f"at f{krel and krel[0]['first']}, its part's press; the PvP ranking's waiting defender cannot tell the "
              "hold from it before then, so against the release at that frame alone he reacts from it, exactly the "
              f"hold read from that frame: he still escapes the bolt ({knows['p_evade']:.3f} -> {sees1['p_evade']:.3f}) "
              f"but rolls later, so he punishes less ({knows['p_punish']:.4f} -> {sees1['p_punish']:.4f}); one release "
              "at 0 is the pair without releases, bit for bit; a lookalike told apart from the cue changes nothing",
              (len(krel), kpart is not None and kpart['offset'] == float(krel[0]['first']) + (ko['first_offset'] or 0.0),
               all(abs(sees1[x] - late[x]) < 1e-9 for x in keys_), all(sees1[x] == nolist[x] for x in keys_),
               all(at0[x] == knows[x] for x in keys_), sees1['p_punish'] < knows['p_punish']),
              (1, True, True, True, True, True),
              'c0000.hks SwordArtsOneShot_onUpdate (L2 up in 100285); lookalike_plan')
        kth = [skill_release_visible(t, dart, krel[0]['anim'], krel[0]['first'], threshold=x) for x in (0, 0.05, 0.20)]
        check(f"the Dart's release at f{krel[0]['first']} shows {kpart['shows'][0]} real frames after its switch (the "
              f"blend into 040001 over its type-16 end, a bone 10 cm off the hold, release_visible; 3 in the "
              f"2026-11-11 survey, and {kth[1]} / {kth[2]} at 5 / 20 cm as there; 0 cm is the switch frame): against "
              f"it alone the hold is read from there, exactly ({svis['p_punish']:.4f})",
              (kpart['shows'][0], kth, all(abs(svis[x] - lvis[x]) < 1e-9 for x in keys_)), (3, [0, 2, 4], True),
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
              f"the hold is escaped every time by a defender who knows it ({fk['p_evade']:.3f}) and half the time by "
              f"one who cannot tell it from the release before then ({fs1['p_evade']:.3f}), as before releases were "
              f"spread; let go on any of its window's {len(fpart['releases'])} frames {fs['p_evade']:.3f}: its land "
              f"share {fk['dmg'] / fland['dmg']:.3f} -> {fs1['dmg'] / fland['dmg']:.3f} -> {fs['dmg'] / fland['dmg']:.3f}",
              (round(fk['p_evade'], 3), round(fs1['p_evade'], 3)), (1.0, 0.5), 'lookalike_pair, 2026-11-10 survey')
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
        pcw, pcs = t.find_weapon("Prelate's Inferno Crozier"), t.find_arts("Prelate's Charge")
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
    blind = FollowupModel([(opening, 0)], [(follow, 1)], 35.0, delays, my_roll=80.0)
    away = FollowupModel([(touch([40.0, 40.5, 41.0], 1.0), 0)], [(follow, 1)], 43.0, delays, my_roll=80.0)
    check('no roll seen before the window opens (it opens at f35, his roll shows from about f37), or no '
          'roll at all (the opening never reaches him): the press stays at once',
          tuple(round(m.outcome(2.5, 0.0, worth)['value'] - m.outcome(2.5, 0.0, worth, see=False)['value'], 12)
                for m in (blind, away)) + (round(away.outcome(2.5, 0.0, worth)['delayed'], 12),),
          (0.0, 0.0, 0.0), 'FollowupModel: attacker_reactions, no opening burst reaching him')
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
    # With no follow-up the model is `sequence_outcome` on the opening, punish included.
    two = [(touch([40.0, 40.5, 41.0]), 0), (touch(np.arange(70.0, 86.0, 0.5)), 1)]
    alone = FollowupModel(two, [], 100.0, [0.0], my_roll=90.0, strikes=[15.0])
    seq = sequence_outcome([([g], [3.0 - i]) for g, i in two], 2.5, my_roll=90.0, strikes=[15.0], run=True)
    got = alone.outcome(2.5, 0.0, lambda d, idx: np.array([3.0, 2.0])[idx])
    check('an opening with no follow-up scores as sequence_outcome does (value, evade, punish)',
          tuple(round(got[k] - seq[k], 9) for k in ('value', 'p_evade', 'p_punish', 'base')), (0.0, 0.0, 0.0, 0.0),
          'FollowupModel against sequence_outcome')
    print(f'{passed} passed, {failed} failed, {skipped} skipped')
    return 1 if failed else 0


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
