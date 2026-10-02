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

    weapon (+ mounted EquipParamGem) --GetSwordArtsParamIdForWeapon 0x140673f70--> SwordArtsParam id
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
    judge --PlayerIns::ResolveBehaviorId 0x140652280 with the weapon's behaviorVariationId-->
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

`attack` follows `hit_attack` (the verified 0x1406832a0 formula). `pvp_damage` applies the
defense curve, absorption, vsPlayerDmgCorrectRate and the hit's FinalDamageRateParam the way
er-builds-pvp.py does for R1s, and writes `pvp_damage` into each hit. `skill_profile` gives the
full event list (buffs, hyperarmor, i-frames) for anything that is not a hit.
"""

import argparse
import collections
import importlib.util
import json
import math
import os
import statistics
import struct
import sys
import types
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))


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
#: TAE event types (WitchyBND template names, handlers per the doc).
EV_JUMP_TABLE, EV_ATTACK, EV_BULLET, EV_COMMON = 0, 1, 2, 5
EV_SPEFFECT = (66, 67, 401)
EV_PC_BEHAVIOR, EV_FP, EV_WA_SPEFFECT, EV_TOUGHNESS = 307, 330, 331, 795
JT_INVINCIBLE = 8          # `_ChrActionFlag` case 8: actionModifiersFlags |= 2 (attacks.md)
#: Event 1 Args+0 (AttackType) 64 marks a parry hitbox: the 307 handler's own error text calls
#: damage type 64 parry (0x14042a580), and Parry/Buckler Parry/Golden Parry fire event 1 with
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

#: `CheckIfWepTypeCanEquipGem` 0x140d29e00 (`VERIFIED`, 1.16.2): EquipParamWeapon.wepType ->
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
                 'isPenetrateMap', 'isInheritSpeedToChild', 'intervalCreateWaitTime']
#: A safety bound on bullet chains; the deepest skill chain in the regulation is well inside it.
BULLET_DEPTH_CAP = 32
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
                                               'spEffectAtkPowerCorrectRate_byPoint'])
        self.final_rate = table('FinalDamageRateParam')
        # Knockback on a player defender (section 15, per-hit landing).
        self.knockback = table('KnockBackParam')
        self.protector_knockback = {i: (r['knockBack'], r['knockbackParamId']) for i, r in
                                    table('EquipParamProtector', ['knockBack', 'knockbackParamId']).items()}
        self.regulation = regulation
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
        """`GetSwordArtsParamIdForWeapon` 0x140673f70 (`VERIFIED`): the mounted gem's
        swordArtsParamId when a gem is mounted, else EquipParamWeapon.swordArtsParamId."""
        if gem_id is not None and gem_id in self.gem:
            return self.gem[gem_id]['swordArtsParamId']
        return self.reg.weapon[weapon_id]['swordArtsParamId']

    def active_skill(self, right_skill, left_skill, two_handed_right=True):
        """Which hand's skill L2 fires, `FUN_14047f770` (`VERIFIED`): two-handing uses that
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
        """`CalculateFpConsumption` 0x14068b220 (`VERIFIED`): ceil(rate x useMagicPoint_<cast>),
        rate = product of active SpEffectParam.artsConsumptionRate. The behavior script picks
        `cast` (R1, R2, L1, L2). -1 in the param means that input does not cast the skill."""
        base = self.arts[sword_arts_id][f'useMagicPoint_{cast}']
        return base if base <= 0 else math.ceil(arts_consumption_rate * base)

    @staticmethod
    def has_enough_fp(fp, cost):
        """`CanCastAow` 0x14047fa20 / `HaveEnoughFP` 0x14047fc60 (`VERIFIED`; 1.17.1 has the same
        code at 0x1404801c0 reading 0.5 at 0x1432a1920): the full skill plays when fp > 0 and
        int(cost * 0.5) <= fp. Event 330 then spends min(cost, fp)
        (FP is clamped at 0), so between half and full cost the skill is full and FP ends at 0."""
        return fp > 0 and int(cost * 0.5) <= fp

    def can_mount(self, weapon_id, gem_id, affinity=0, level=0):
        """`CanMountGemWithAffinityOnWeapon` 0x140d549d0 (`VERIFIED`). Returns (ok, reason).

        Not modelled: `_CanMountGem` 0x14078a3f0 first requires the whetblade event flag
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
    """{anim id: [TaeEvent]} of `a<600 + swordArtsTypeNew>.tae`, or None."""
    row = t.arts.get(sword_arts_id)
    if row is None:
        return None
    cat = SKILL_TAE_BASE + row['swordArtsTypeNew']
    if cat not in _SKILL_TAE:
        _SKILL_TAE[cat] = ATK.tae_animations(cat)
    return _SKILL_TAE[cat]


def _frames(e):
    end = e.end if e.end < 1e6 else None
    return (round(e.start * TAE_FPS), round(end * TAE_FPS) if end is not None else None)


def resolve_judge(t, weapon_id, judge, level=0, literal=False, via_bullet=False, event_flags=1):
    """What a behavior judge fires for this weapon: melee, bullet (with its tree) or SpEffect.

    `literal`: the judge is a BehaviorParam_PC row id as is (event 5 CommonBehavior and 307 with
    flag 4, `VERIFIED` 0x1404269e0 / 0x140652470). `via_bullet`: the event is 2 BulletBehavior,
    the only path that honours refType 1 and 2 (`VERIFIED`: events 1/5/307 hand a refType other
    than 0 to `FUN_1404428f0`, which finds no AtkParam and creates nothing)."""
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
        # `0x14068ffa0`: attackNotFromWeapon = (flags & 0x68) == 0 && hitSourceType != 0.
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
        # Applied to the attacker itself (`VERIFIED` 0x1403c1020 -> FUN_1403e8b70).
        sid = b['refId']
        return {**base, 'kind': 'speffect', 'speffect': t.speffect_summary(sid), 'effect': t.effect_kind(sid)}
    return {**base, 'kind': f"refType {b['refType']}"}


def bullet_tree(t, bid, depth=0, seen=None):
    """A Bullet row, its AtkParam, the SpEffects it applies on hit, and child bullets.

    Followed to any depth (Phantom Slash's damaging 2663 is five links down), each row once."""
    seen = set() if seen is None else seen
    r = t.bullet.get(bid)
    if r is None or bid in seen or depth > BULLET_DEPTH_CAP:
        return None
    seen.add(bid)
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
           'children': {}}
    for link in ('HitBulletID', 'intervalCreateBulletId'):
        child = r[link]
        if child and child > 0 and child != bid:
            c = bullet_tree(t, child, depth + 1, seen)
            if c:
                out['children'][link] = c
    return out


def anim_actions(t, weapon_id, events, level=0):
    """The events of one skill animation that do something, in frame order."""
    out = []
    for e in sorted(events, key=lambda e: e.start):
        p = e.params
        fr = _frames(e)
        if e.type == EV_ATTACK:
            res = resolve_judge(t, weapon_id, struct.unpack_from('<i', p, 8)[0], level)
            if struct.unpack_from('<i', p, 0)[0] == ATTACK_TYPE_PARRY:
                res['kind'] = 'parry'
            # The attack index (type-1 Args[1]) keys the hit list (`ATK.hit_records`).
            out.append({'event': 'attack', 'frames': fr, 'fr_s': (e.start, e.end),
                        'attack_index': struct.unpack_from('<i', p, ATK.ATTACK_INDEX_OFFSET)[0], **res})
        elif e.type == EV_BULLET:
            out.append({'event': 'bullet', 'frames': fr,
                        **resolve_judge(t, weapon_id, struct.unpack_from('<i', p, 8)[0], level,
                                        via_bullet=True)})
        elif e.type == EV_COMMON:
            out.append({'event': 'common', 'frames': fr,
                        **resolve_judge(t, weapon_id, struct.unpack_from('<i', p, 4)[0], level,
                                        literal=True)})
        elif e.type == EV_PC_BEHAVIOR:
            # +4 u32 flags, +8 judge (0x14042a580). Flag 8 resolves like event 1; flag 4 reads
            # BehaviorParam_PC[judge] as is (judge - 100 while a roll-trigger state is set).
            flags, judge = struct.unpack_from('<Ii', p, 4)
            res = resolve_judge(t, weapon_id, judge, level, literal=not flags & 8, event_flags=flags)
            out.append({'event': f'pc-behavior {flags}', 'frames': fr, **res})
        elif e.type == EV_FP:
            # `CSChrSwordArtsModule::ConsumeFp` 0x14047f640 spends the cost reserved when the
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


def skill_profile(t, sword_arts_id, weapon_id, level=0):
    """Everything the skill does on this weapon, per animation, plus a classification. Cached on
    the tables per (skill, weapon, level); callers treat the result as read-only."""
    key = (sword_arts_id, weapon_id, level)
    if key not in t._profiles:
        t._profiles[key] = _skill_profile(t, sword_arts_id, weapon_id, level)
    return t._profiles[key]


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
    """The first animation that charges FP (the skill's opening, with FP), else the first."""
    if profile['fp_anims']:
        return profile['fp_anims'][0]
    return next(iter(profile['anims']), None)


# ---------------------------------------------------------------------------------------------
# damage

class WeaponContext:
    """The firing weapon as the attack-power builder sees it: base attack per element, the stat
    scaling multiplier per element (`FUN_140690390`) and ReinforceParamWeapon.baseAtkRate."""

    def __init__(self, weapon, affinity='Standard', level=None, stats=None, two_handed=False,
                 ar_tables=None):
        self.AR = _load('er_mechanics_ar', 'er-mechanics-ar.py')
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
    """Attack per element before defense for one AtkParam row, `0x1406832a0` (`VERIFIED`):

        from the weapon:  (base_el x MV/100 + (isAddBaseAtk ? flat_el x baseAtkRate : 0)) x scaling_el
        not from weapon:  flat_el x scaling of the weapon at AttackInfo+0xf0 (melee: none, so 1)

    base_el is the weapon's upgraded base attack (EquipParamWeapon.attackBase* x
    ReinforceParamWeapon.*AtkRate) and scaling_el the stat multiplier, so base_el x scaling_el is
    the AR. Bullets always take the first branch (`0x14038e380` never sets
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


def skill_hits(t, weapon_id, sword_arts_id, ctx, level=0, anim=None):
    """Every damaging hit of one skill animation, with its attack per element before defense.

    This is the function a PvP ranking calls. `weapon_id` is the base id (behaviorVariationId and
    saWeaponDamage are the same across affinities); `ctx` is a `WeaponContext` for the weapon as
    built (affinity, level, stats, grip)."""
    prof = skill_profile(t, sword_arts_id, weapon_id, level)
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

    def walk_anim(a):
        for x in prof['anims'].get(a, []):
            f = x['frames'][0]
            if x['kind'] == 'melee':
                add('melee', f, x, {'poise': round((x['poise'] or 0.0) * POISE_MENU, 2), 'count': 1,
                                    'judge': x['judge'], 'stamina_cost': x['stamina_cost'], 'anim': a})
            elif x['kind'] == 'bullet':
                n = len(out)
                walk_bullet(f, x['bullet'])
                for h in out[n:]:
                    h['anim'] = a
                    h['root_bullet'] = x['bullet']['bullet']

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


PHYS_TYPE = {0: 'slash', 1: 'strike', 2: 'pierce', 3: 'standard'}


def phys_type(t, weapon_id, atk_row):
    """AtkParam.atkAttribute; 252/253 defer to the weapon's pair (`INFERRED`, as er-builds-pvp)."""
    attr = t.reg.atk.get(atk_row, {}).get('atkAttribute', 3)
    w = t.reg.weapon[weapon_id]
    if attr == 252:
        attr = w['atkAttribute']
    elif attr == 253:
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
    FinalDamageRateParam row (`VERIFIED` 0x1406852f2: both sides players; skills mostly use row
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
    with open(mirror) as fh:
        for line in fh:
            row = json.loads(line)
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
    (`ash_gems`) that `can_mount` accepts at this affinity and level (`VERIFIED` 0x140d549d0,
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
    `has_enough_fp` (half the cost, `VERIFIED` 0x14047fc60). None when the skill costs nothing."""
    if cost <= 0:
        return None
    n, rest = divmod(int(fp_bar), int(cost))
    return n + (1 if AshTables.has_enough_fp(rest, cost) else 0)


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
    behavior script's hand-over frame is not traced)."""
    opening = main_anim(prof)
    last = hits[-1].get('anim', opening) if hits else opening
    after = max((h['frame'] for h in hits if h.get('anim', opening) == last), default=0) / TAE_FPS
    rec = anim_recovery(t, weapon_id, sword_arts_id, last, after)
    cat = SKILL_TAE_BASE + t.arts[sword_arts_id]['swordArtsTypeNew']

    def handover(anim):
        # (frames, whether the hand-over happens by itself). A leap hands over when it lands,
        # which the behavior script decides; the landing can only come once the falling hitbox is
        # live, the last hitbox window to open (Gold Breaker's 040000 no-damage hitbox f70-135;
        # Ground Slam's body hitbox 500 f49-135, after the take-off box 504 at f23). Its start is
        # the hand-over (`INFERRED`: the earliest landing, on flat ground). Otherwise the first
        # R1/R2 cancel, a press the player times, else the clip length.
        boxes = [x['frames'][0] for x in prof['anims'].get(anim, [])
                 if x['kind'] in ('melee', 'no-damage hitbox', 'body hitbox')]
        events = (skill_tae(t, sword_arts_id) or {}).get(anim)
        if boxes and events:
            return ATK.real_frame(ATK.clip_to_real(events)(max(boxes) / TAE_FPS)), True
        o = anim_recovery(t, weapon_id, sword_arts_id, anim)
        cands = [v for v in (o.get('r1'), o.get('r2')) if v]
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
    # Where the first hit's animation starts, and where the defender can first read its timing
    # (section 16): the skill's start, unless a press the attacker times hands over to it.
    first_offset = windup + (hand[0] if first != opening else 0.0)
    cue = first_offset if first != opening and not hand[1] else 0.0
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


def skill_followups(t, sword_arts_id, weapon_id, level=0):
    """The follow-ups a skill's opening offers (section 16): per TAE event 66 carrying a
    `FOLLOW_UP_SPEFFECTS` id, the button, the follow-up animation and the real frames of the
    opening between which that press starts it (where the SpEffect window, the button's input
    window and its cancel window overlap, attacks.md section 4). Flaming Strike on a Lance: 100050
    f30-44, R2 input 87 from f21, R2 cancel 116 f30-35 then 4 from f35, so R2 at f30-44 plays
    040010, the melee swing that applies the fire buff."""
    prof = skill_profile(t, sword_arts_id, weapon_id, level)
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
        anim = opening + step
        if anim not in prof['anims'] or anim in prof['no_fp_anims']:
            continue
        w = rec[button]
        pairs = [(max(i[0], c[0], e.start), min(i[1], c[1], e.end)) for i in w['input'] for c in w['cancel']]
        pairs = [(a, b) for a, b in pairs if a < b]
        if not pairs:
            continue
        out.append({'anim': anim, 'button': button, 'speffect': sp,
                    'first': ATK.real_frame(to_real(min(a for a, _ in pairs))),
                    'last': ATK.real_frame(to_real(max(b for _, b in pairs)))})
    return out


def skill_option(t, weapon_id, sword_arts_id, ctx, level, defender=None, def_module=None,
                 damage_fn=None, poises=None, follow=None):
    """One skill on one weapon as built: its damage, commitment, stagger share, FP and the buff
    SpEffects it puts on its user.

    With `follow` (one `skill_followups` entry) the option is the opening and then that follow-up,
    pressed at its first frame: both animations' hits, the follow-up's recovery, the FP of both
    presses, and `parts` so each is landed and reacted to on its own (section 16)."""
    prof = skill_profile(t, sword_arts_id, weapon_id, level)
    hits = skill_hits(t, weapon_id, sword_arts_id, ctx, level)
    commit = skill_commit(t, weapon_id, sword_arts_id, prof, hits) if hits else {}
    parts = [{'hits': hits, 'offset': commit.get('first_offset') or 0.0, 'cue': commit.get('cue') or 0.0}]
    fp = skill_fp(t, sword_arts_id)
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
            'variant': f"then {follow['button'].upper()} {follow['anim']:06d}" if follow else None}


# Bullet flight (ashes-of-war.md section 15). The per-frame rule and the child hand-over are
# `VERIFIED` in the 1.16.2 code (bd bullet-launch-geometry-for-skill-reach-2026-09-29):
#   FUN_14039ac20 / FUN_14039f850  v += (-g Y + a v_hat) dt, |v| clamped to [minV, maxV],
#                                  p += v dt; `gravity/accelInRange` while the distance flown is
#                                  <= `dist`, the `OutRange` pair after; acceleration starts after
#                                  `accelTime` s
#   FUN_14039bba0                  HitBulletID is created when the parent hits or expires, at the
#                                  contact point or the parent's position
#   FUN_14039da40                  the child's `launchConditionType`: 0/3 always, 5 on a hit,
#                                  4 on expiry, 1/2 on water, 254/255 never
#   FUN_140390c20                  `EmittePosType` 2 = 1.0 m above the emit point, flying level
#   FUN_14039ab10                  an interval child starts at the parent's position when it spawns
# Placement of the root bullet (the launch dummy poly) is not decoded: `BULLET_ORIGIN_M` ahead at
# `BULLET_ORIGIN_HEIGHT_M` (`INFERRED`). The ground is flat, no character is hit on the way, and
# homing is off (no target), so the reach is how far the skill carries when it misses.

#: Emit point of a skill's root bullet, metres ahead of the caster's start position (`INFERRED`
#: placeholder: about an arm and a grip).
BULLET_ORIGIN_M = 1.0
#: Height of that emit point above the ground (`INFERRED` placeholder).
BULLET_ORIGIN_HEIGHT_M = 1.0
#: `EmittePosType` 2 raises the emit point by this much (`VERIFIED` .data 0x143b15b5c, read only by
#: FUN_140390c20).
EMIT_RAISE_M = 1.0
EMIT_RAISED_LEVEL = 2
#: `FollowType` values that attach the bullet to the shooter or target instead of free flight.
FOLLOW_ATTACHED = (1, 2, 3, 5)
#: `launchConditionType` of a child: which parent endings create it (`VERIFIED` FUN_14039da40;
#: 6 depends on a hit-record flag and is taken as always, `INFERRED`).
LAUNCH_ON = {0: ('hit', 'expire'), 3: ('hit', 'expire'), 6: ('hit', 'expire'), 5: ('hit',),
             4: ('expire',)}
BULLET_FPS = 60
#: Flight time simulated for a bullet whose `life` is -1 (no timer).
BULLET_LIFE_CAP_S = 5.0
#: Interval children simulated per parent, spread over its flight.
BULLET_INTERVAL_CAP = 12
#: Longest bullet reach reported (`INFERRED` placeholder): a homing bolt with a target is not
#: modelled, and the cap sits above the 5.6 m where er-builds-pvp's reach factor saturates
#: (`SCORE_REACH_REF_M` 2.5 x 1.5 ** 2), so it changes the shown value, not the score.
BULLET_REACH_CAP_M = 25.0
_FLIGHT = {}


def _bullet_damages_self(bt):
    return any(bt['mv'].values()) or (bt['add_base_atk'] and any(bt['flat'].values()))


def _unit(yaw_deg, pitch_deg):
    y, p = math.radians(yaw_deg), math.radians(pitch_deg)
    return (math.cos(p) * math.sin(y), math.sin(p), math.cos(p) * math.cos(y))


def bullet_flight(r, pos, heading, speed=None):
    """One Bullet row's flight: (path [(x, y, z)], ending 'hit' | 'expire', final unit direction,
    final speed). `pos` is where it is created, `heading` (yaw, pitch) in degrees with +z forward
    and +y up, `speed` overrides `initVellocity` (`isInheritSpeedToChild`)."""
    dt = 1.0 / BULLET_FPS
    life = r['life'] if r['life'] > 0 else (BULLET_LIFE_CAP_S if r['life'] < 0 else dt)
    if r['FollowType'] in FOLLOW_ATTACHED:
        return [pos], 'expire', _unit(*heading), 0.0
    v0 = r['initVellocity'] if speed is None else speed
    d = _unit(*heading)
    v = [v0 * c for c in d]
    p = list(pos)
    path = [tuple(p)]
    flown, t = 0.0, 0.0
    radius = max(r['hitRadius'], 0.0)
    # The bullet also moves on the frame its life runs out (`VERIFIED` KillBullet 0x14039f0b0
    # path): life / dt frames plus that one.
    for _ in range(max(1, round(life / dt)) + 1):
        inside = flown <= r['dist']
        g = r['gravityInRange'] if inside else r['gravityOutRange']
        a = r['accelInRange'] if inside else r['accelOutRange']
        s = math.sqrt(sum(c * c for c in v))
        hat = [c / s for c in v] if s > 1e-9 else list(d)
        if t < r['accelTime']:
            a = 0.0
        v = [v[0] + a * hat[0] * dt, v[1] + (a * hat[1] - g) * dt, v[2] + a * hat[2] * dt]
        s = math.sqrt(sum(c * c for c in v))
        if r['maxVellocity'] > 0 and s > 1e-9:
            clamped = min(max(s, r['minVellocity']), r['maxVellocity'])
            v = [c * clamped / s for c in v]
            s = clamped
        step = [c * dt for c in v]
        p = [p[i] + step[i] for i in range(3)]
        flown += s * dt
        t += dt
        path.append(tuple(p))
        if s > 1e-9:
            d = [c / s for c in v]
        if not r['isPenetrateMap'] and v[1] < 0 and p[1] <= radius:
            # The step can carry a fast bullet below the ground (Lightning Slash's 200 m/s bolt
            # ends a frame at y = -2.3): it stops on the ground, where its child is created.
            path[-1] = (p[0], radius, p[2])
            return path, 'hit', d, s
    return path, 'expire', d, math.sqrt(sum(c * c for c in v))


def bullet_reach(t, bt):
    """(distance, arc) of a skill's bullet tree fired straight ahead, or (0, 0) (`INFERRED`
    placement, `VERIFIED` flight rules above). Distance: the farthest horizontal point any
    damaging bullet reaches from the caster's start position, plus its `max(hitRadius,
    hitRadiusMax)`, at most `BULLET_REACH_CAP_M`. `arc`: the azimuth that bullet covers seen from
    the caster: the `numShoot` fan of its tree plus the angle its radius subtends at its farthest
    point, 360 when the radius encloses the caster. Cached per root bullet id."""
    if not bt:
        return 0.0, 0.0
    if bt['bullet'] in _FLIGHT:
        return _FLIGHT[bt['bullet']]
    best = [0.0, 0.0]
    # Every shot of the fan flies; the farthest decides the reach.
    for node, path, _, fan in fly_tree(t, bt):
        if not _bullet_damages_self(node):
            continue
        far = max(path, key=lambda q: math.hypot(q[0], q[2]))
        dist = math.hypot(far[0], far[2])
        rad = node['radius_m']
        reach = dist + rad
        if reach > best[0]:
            arc = 360.0 if rad >= dist else min(360.0, fan + 2 * math.degrees(math.asin(rad / dist)))
            best[:] = [reach, arc]
    _FLIGHT[bt['bullet']] = (round(min(best[0], BULLET_REACH_CAP_M), 3), round(best[1], 1))
    return _FLIGHT[bt['bullet']]


_TREE = {}


def fly_tree(t, bt):
    """[(node, path, t0, fan)] for every shot of a skill's bullet tree fired straight ahead with
    the rules above: `path` = [(x, y, z)] at `BULLET_FPS` from `t0` seconds after the bullet
    event, `fan` the `numShoot` fan of the tree down to that node. Parents come before their
    children. Cached per root bullet id."""
    if bt['bullet'] in _TREE:
        return _TREE[bt['bullet']]
    out = []

    def fire(node, pos, yaw, pitch, fan, speed, depth, t0):
        r = t.bullet.get(node['bullet'])
        if r is None or depth > BULLET_DEPTH_CAP:
            return
        fan = max(fan, node['fan_deg'])
        shots = max(1, r['numShoot'])
        for i in range(shots):
            y = yaw + r['shootAngle'] + i * r['shootAngleInterval']
            pch = 0.0 if r['EmittePosType'] == EMIT_RAISED_LEVEL else \
                pitch + r['shootAngleXZ'] + i * r['shootAngleXInterval']
            start = (pos[0], pos[1] + (EMIT_RAISE_M if r['EmittePosType'] == EMIT_RAISED_LEVEL else 0.0),
                     pos[2])
            path, ending, d, s = bullet_flight(r, start, (y, pch), speed)
            out.append((node, path, t0, fan))
            y_end = math.degrees(math.atan2(d[0], d[2]))
            p_end = math.degrees(math.asin(max(-1.0, min(1.0, d[1]))))
            child_speed = s if r['isInheritSpeedToChild'] else None
            hit = node['children'].get('HitBulletID')
            if hit and ending in LAUNCH_ON.get(t.bullet.get(hit['bullet'], {}).get('launchConditionType', 0), ()):
                fire(hit, path[-1], y_end, p_end, fan, child_speed, depth + 1,
                     t0 + (len(path) - 1) / BULLET_FPS)
            ivl = node['children'].get('intervalCreateBulletId')
            if ivl:
                start_i = int(r['intervalCreateWaitTime'] * BULLET_FPS)
                every = max(1, int(round(r['intervalCreateTimeMin'] * BULLET_FPS)))
                spawn = list(range(start_i, len(path), every))
                if len(spawn) > BULLET_INTERVAL_CAP:
                    spawn = [spawn[round(k * (len(spawn) - 1) / (BULLET_INTERVAL_CAP - 1))]
                             for k in range(BULLET_INTERVAL_CAP)]
                for k in spawn:
                    fire(ivl, path[k], y_end, p_end, fan, None, depth + 1, t0 + k / BULLET_FPS)

    fire(bt, (0.0, BULLET_ORIGIN_HEIGHT_M, BULLET_ORIGIN_M), 0.0, 0.0, 0.0, None, 0, 0.0)
    _TREE[bt['bullet']] = out
    return out


def skill_reach_factors(t, weapon_id, sword_arts_id, option, grip='one'):
    """The reach and coverage a skill option is scored with, the same columns a slot carries.

      melee   `er-mechanics-reach.skill_reach` on the animation of the first melee hit: world
              reach and the footprint's `coverage_factor`, read like a slot's (`MEASURED` pose)
      bullet  `bullet_reach` on each bullet event of the hit animations (`INFERRED` motion model,
              origin `BULLET_ORIGIN_M`); coverage from its arc and the late turn the TimeAct
              allows before the event (`turn_details`), through `coverage_factor`
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
            d, arc = bullet_reach(t, x["bullet"])
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
# `VERIFIED` in 1.16.2 (shift 0, named dump on :8765):
#   FUN_140d24b10          AttackDamageInfo+0x58 = AtkParam.knockbackDist (+0x10); the same
#                          function writes +0x40 attackParamId and +0x44 the pc/npc type.
#   FUN_140446750          called from ApplyDamage 0x1404497d0 after the poise damage. With
#                          lv = FUN_140690250(level) (the poise remap, frame-advantage.md) the
#                          defender's knockback is started only when lv != 0:
#                              dist = (1 - clamp(resist, 0, 1)) x max(dot, 0.4) x info+0x58 x info+0x200
#                          resist = knockback module vtable slot 2; lv == 0 sets resist to 1.
#   FUN_140689ad0          player slot 2: chest EquipParamProtector.knockBack x 0.01. Every
#                          protector row has knockBack 0 and knockbackParamId 1 (regulation).
#   FUN_1404504e0          on the damage animation (HksAct 0x14040d31d): ContTime c and DecTime d of
#                          KnockBackParam[row] for the animation type (FUN_140d3c900 row+4i,
#                          FUN_140d3c920 row+0x3c+4i), speed v = dist x clamp(c / (0.5 d + c), 0, 1) / c,
#                          total time c + d (FUN_1404506a0).
#   FUN_1404508c0          per frame: the speed while more than d is left, then scaled by
#                          remaining / d. So the defender moves exactly `dist`, at v for c seconds
#                          and slowing linearly to 0 over d.
# `MEASURED` (er-hkx-pose): the small, middle, large, push and minimum damage clips carry no root
# motion; small blow (a000_005400) and exlarge (005450) carry 4.4 m and 6.9 m backwards.
# `INFERRED`: the attack direction (info+0x1c0) is taken along the line to the defender, so
# max(dot, 0.4) = 1 for a defender straight ahead; info+0x200 and the module's per-frame
# multiplier (+0x48) are taken as 1.0; the animation type the behavior script passes for each
# damage level is paired by name below; a knockback and the clip's root motion add.
#
# The attacker's hit stop (FUN_140448310 -> FUN_14044fce0) freezes the attacker, scaled by the
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
#: FUN_140446750 floors the facing term at .rdata 0x14329e65c = 0.4 (`VERIFIED`); the dot itself
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
    KnockBackParam times `cont` and `dec` started (`VERIFIED` FUN_1404504e0 / FUN_1404508c0).
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
    samples behind it: `window_points` entries for 'pose', [(seconds, x, z, disc)] for 'flight')."""
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
                for node, path, t0, _ in fly_tree(t, root):
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
                                pts.append((start + t0 + (k + u) / BULLET_FPS, p3[0], p3[2], d))
                pts.sort()
                source, raw = 'flight', pts

                def contact(dist_at, pts=pts):
                    for when, x, z, d in pts:
                        if math.hypot(x, z - dist_at(when)) < d:
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
        pending = list(info)
        while pending:
            # The next hit to reach him, from where the earlier ones left him (a swing whose
            # window opens first can reach him after a bullet fired later: Stormcaller at 3 m).
            timed = [(x['contact'](dist_at) if geometry else x['start'], k) for k, x in enumerate(pending)]
            timed = [(tc, k) for tc, k in timed if tc is not None]
            if not timed:
                break
            tc, k = min(timed)
            x = pending.pop(k)
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
                   strikes=None, share=None):
    """The landed damage of one part of a skill once a waiting defender may dodge it on reaction
    (section 16). `hits` and `land` are one `skill_landing` call; the hit shapes are the ones the
    landing played (`_hit_contacts`), moved `offset` real frames on (where that animation starts
    in the skill), and the defender reads the timing from `cue`. When hit k is the first to catch
    him, the part is worth the hits that open at or after k, each at its landing share at that
    distance. Returns `dmg` = the mean over the landing's distances of (1 - share) x landed +
    share x `reaction_outcome` value, plus `react_factors`."""
    import numpy as np
    share = REACT_SHARE if share is None else share
    if not hits or not land or land['dmg'] <= 0 or not land.get('used'):
        return None
    anim = hits[0].get('anim')
    idx = [i for i, h in enumerate(hits) if h.get('anim') == anim]
    con = _hit_contacts(t, weapon_id, sword_arts_id, [hits[i] for i in idx])
    starts = [c[0] for c in con]
    if land['geometry'] == 'measured':
        geoms = [c[3] for c in con]
    else:
        # The landing found no contact at any distance and let the lock alone decide: every hit
        # touches when its window opens, wherever he is (`INFERRED`, the same rule).
        geoms = [{'T': np.array([s * TAE_FPS]), 'X': np.zeros(1), 'Z': np.full(1, -99.0),
                  'R': np.full(1, 1e3), 'PK': np.full(1, -math.inf)} for s in starts]
    geoms = [None if g is None else {**g, 'T': g['T'] + offset} for g in geoms]
    pk = [g['PK'][np.isfinite(g['PK'])] for g in geoms if g is not None]
    advance = max((float(p.max()) for p in pk if len(p)), default=0.0)
    total, outs = 0.0, []
    for d in land['used']:
        sh = land['share_by_distance'][d]
        value = [sum(hits[idx[j]].get('pvp_damage_total', 0.0) * sh[idx[j]]
                     for j in range(len(idx)) if starts[j] >= starts[k]) for k in range(len(idx))]
        o = reaction_outcome(geoms, value, d, cue, my_roll=my_roll, advance=advance, strikes=strikes)
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
            casts = float(uses if uses is not None else engagements)
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
#: (`er-mechanics-frame-advantage.py`, `VERIFIED` 0x140690250 path).
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
    true-combo follow-up of it, that hit (gap `follow_up_gap`). None when the slot has no hit."""
    s = slots.get(key)
    if not s or s.get('startup') is None:
        return None
    fc = s.get('front_contact') or {}
    at = fc.get(ENGAGE_DISTANCE_M, fc.get(str(ENGAGE_DISTANCE_M))) if isinstance(fc, dict) else None
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
                           react=True):
    """`Opponents` for the exchange pool (`er-mechanics-exchange.opponent_pool`): each pool build's
    weapon and grip looked up in the ranking's own rows (`er-builds-pvp` results; `dmg` is the
    sweep build's corpus-mean damage, standing in for that player's own).

    `openers` `r1`: R1 #1 as the exchange throws it and its R1 #2 when R1 #1 lists it in `combos`
    (section 14a). `families` (the default, `OPPONENT_OPENERS`): every moveset family's best
    engagement at that family's use share, the opener with its first chained follow-up. A key
    the ranking has no row for takes `fallback` (no follow-up). Returns (Opponents, matched share,
    pool-weighted means)."""
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
            for fam in ((r.get('moveset') or {}).get('families') or {}).values():
                links = fam.get('links') or []
                row = _opener_row(slots, fam.get('opener'), fallback, links[0] if links else None)
                if row and fam.get('share'):
                    rows.append((fam['share'], {**row, 'opener': fam.get('opener')}))
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
            with_roll = n.neutral_exchange(*args, tools=n.DEFAULT_TOOLS)
            with_step = n.neutral_exchange(*args, tools=tuple(n.DEFAULT_TOOLS) + (tool,))
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


def reaction_delays():
    """[(real frames from the moment the attack is visible to the moment the dodge is live on the
    attacker's machine, weight)]: `REACTION_POINTS` quantiles of the reaction time plus two
    network legs."""
    out = []
    for k in range(REACTION_POINTS):
        z = _ND.inv_cdf((k + 0.5) / REACTION_POINTS)
        s = REACTION_MEDIAN_S * math.exp(REACTION_LOG_SD * z) + 2 * NETWORK_ONE_WAY_S
        out.append((s * TAE_FPS, 1.0 / REACTION_POINTS))
    return out


def _runs(ok):
    """[(first, last)] index runs where `ok` holds."""
    out, start = [], None
    for i, v in enumerate(ok):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(ok) - 1))
    return out


def dodge_presses(ok, cue, step=DODGE_STEP):
    """[(dodge start, real frames from the attack's start; weight)]: when a defender who waits and
    reacts starts his dodge.

    `ok[i]` says whether a dodge started at i x `step` escapes the attack. He cannot start before
    `cue` + his reaction delay (`reaction_delays`). Among the escaping runs still open then he
    takes the one he is likeliest to hit and aims at its middle, with a normal timing error
    (`DODGE_TIMING_SD_S`); a press that would come before he can react comes at the reaction
    instead. When no run is left he dodges at the reaction anyway, too late. So an early roll
    loses to a long or late live window and a late one to a fast startup."""
    sd = DODGE_TIMING_SD_S * TAE_FPS
    runs = [(a * step, b * step) for a, b in _runs(ok)]
    errors = [sd * _ND.inv_cdf((k + 0.5) / TIMING_POINTS) for k in range(TIMING_POINTS)]
    out = []
    for delay, w in reaction_delays():
        t = cue + delay
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


def contact_geometry(entries=None, bullet_points=None):
    """One hit's sampled contact shapes as arrays in real frames from its clip's start: `T`, `X`,
    `Z`, `R` (contact disc radius against the idle defender) and `PK` (the attacker's forward root
    motion so far, which the defender's body holds back; minus infinity for a bullet). A melee
    window is `er-mechanics-reach.window_points` entries [(seconds, peak, ((x, z, disc), ...))]
    with the defender at z = -distance; bullet points are [(seconds, x, z, disc)] with the defender
    at z = +distance, stored negated so one test serves both."""
    import numpy as np
    t, x, z, r, pk = [], [], [], [], []
    for when, peak, pts in entries or ():
        for px, pz, d in pts:
            t.append(when * TAE_FPS), x.append(px), z.append(pz), r.append(d), pk.append(peak)
    for when, px, pz, d in bullet_points or ():
        t.append(when * TAE_FPS), x.append(px), z.append(-pz), r.append(d), pk.append(-math.inf)
    if not t:
        return None
    return {k: np.array(v, float) for k, v in (('T', t), ('X', x), ('Z', z), ('R', r), ('PK', pk))}


def _catches(geoms, dist, move, starts):
    """Per dodge start in `starts` (real frames): (index of the hit that first touches the defender
    outside his invincibility, its time), -1 / inf when none does. The defender stands `dist` m
    straight ahead; from the start he follows the dodge's root motion (`move['path']`, one (x, z)
    per real frame, his forward toward the attacker), invincible for `move['iframes']` frames, and
    stands still after the clip. `move` None is no dodge. A melee sample is held back by his body
    as in `er-mechanics-reach.window_contact_time`."""
    import numpy as np
    radius = _pose()[1].PUSH_CAPSULE_RADIUS
    starts = np.asarray(starts, float)[:, None]
    best_t = np.full(starts.shape[0], math.inf)
    best_h = np.full(starts.shape[0], -1)
    if move is not None:
        path = np.array(move['path'], float)
        px, pz, n = path[:, 0], path[:, 1], len(path)
    for h, g in enumerate(geoms):
        if g is None:
            continue
        u = g['T'][None, :] - starts
        if move is None:
            xr, d, vuln = 0.0, np.full(u.shape, float(dist)), np.ones(u.shape, bool)
        else:
            k = np.clip(np.floor(u).astype(int), 0, n - 1)
            moving = u >= 0
            xr = np.where(moving, px[k], 0.0)
            d = dist + np.where(moving, pz[k], 0.0)
            vuln = ~(moving & (u < move['iframes']))
        held = np.maximum(0.0, g['PK'][None, :] - (d - 2 * radius))
        touch = vuln & (np.hypot(g['X'][None, :] - xr, g['Z'][None, :] + held + d) < g['R'][None, :])
        tt = np.where(touch, g['T'][None, :], math.inf).min(axis=1)
        better = tt < best_t
        best_t[better], best_h[better] = tt[better], h
    return best_h, best_t


def reaction_outcome(geoms, hit_value, dist, cue=0.0, moves=None, my_roll=None, advance=0.0,
                     strikes=None):
    """A defender `dist` m ahead who waits for this attack and dodges on reaction (section 16).

    `geoms` is one `contact_geometry` per hit (None where a hit has no shapes), `hit_value[h]` what
    the attack is worth when hit h is the first to catch him (for a slot 1.0, for a skill the
    damage of that hit and of every later one that lands). `cue` is the real frame the attack
    becomes readable: its clip's start, or for a hit whose timing the attacker picks (an R2's
    release, a skill follow-up, a stance) the start of that part. Each of the medium roll's four
    directions (`roll_motion`) is taken equally (`INFERRED`, as section 14a), and for each the
    dodge starts where `dodge_presses` puts it.

    Returns `value` (expected worth when he reacts), `p_evade`, and, when `strikes` (the pool's
    R1 strike frames) and `my_roll` (the attacker's first roll frame from the same clip start)
    are given, `p_punish` and `punish_hp`: an evading dodger punishes when his R1 cancel plus his
    strike comes before the attacker can roll, and he ends within `PUNISH_REACH_M` of where the
    attacker's root motion (`advance` m forward) left him."""
    import numpy as np
    moves = moves if moves is not None else roll_motion()
    base_h, _ = _catches(geoms, dist, None, [0.0])
    base = hit_value[base_h[0]] if base_h[0] >= 0 else 0.0
    live = [g['T'].max() for g in geoms if g is not None]
    if not live or base <= 0:
        return {'value': base, 'base': base, 'p_evade': 0.0, 'p_punish': 0.0, 'punish_hp': 0.0}
    grid = np.arange(0.0, max(live) + 1.0, DODGE_STEP)
    strikes = None if strikes is None else np.asarray(strikes, float)
    value = p_evade = p_punish = 0.0
    for m in moves:
        h, _ = _catches(geoms, dist, m, grid)
        ok = h < 0
        ready = m['ready'] if m['ready'] is not None else len(m['path'])
        x, z = m['path'][min(int(ready), len(m['path']) - 1)]
        near = math.hypot(x, dist + z - advance) <= PUNISH_REACH_M
        for s, w in dodge_presses(ok, cue):
            i = int(round(s / DODGE_STEP))
            hit = int(h[i]) if 0 <= i < len(h) else int(base_h[0])
            w /= len(moves)
            if hit >= 0:
                value += w * hit_value[hit]
                continue
            p_evade += w
            if strikes is not None and my_roll is not None and near and len(strikes):
                p_punish += w * float(np.mean(strikes < my_roll - s - ready))
    return {'value': value, 'base': base, 'p_evade': p_evade, 'p_punish': p_punish,
            'punish_hp': p_punish * PUNISH_HP}


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
    casts = float(uses) if uses is not None else float(engagements)
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
        # and, with `react`, dodged on reaction from its own cue (section 16).
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
                                       part['cue'], o['roll'], strikes)
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
                casts = float(o['uses'] if o['uses'] is not None else engagements)
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
    skill buffs its user in any paid animation, its follow-ups included."""
    try:
        base = _skill_variant_scored(t, weapon_id, sid, ctx, level, best_score, fp_bar, engagements, score_fn,
                                     defender, def_module, damage_fn, poises, slot_extra_fn, engagement,
                                     opponents)
    except (SystemExit, KeyError, StopIteration, TypeError) as exc:
        return [{'sword_arts_id': sid, 'name': t.arts_name(sid), 'error': str(exc)}]
    out = [base]
    follows = []
    try:
        follows = skill_followups(t, sid, weapon_id, level) if follow_ups or buff_fn else []
    except (SystemExit, KeyError, StopIteration, TypeError):
        follows = []
    for f in follows if follow_ups else []:
        try:
            v = _skill_variant_scored(t, weapon_id, sid, ctx, level, best_score, fp_bar, engagements, score_fn,
                                      defender, def_module, damage_fn, poises, slot_extra_fn, engagement,
                                      opponents, follow=f)
        except (SystemExit, KeyError, StopIteration, TypeError):
            v = None
        if v is not None and v.get('score') is not None:
            out.append(v)
    if buff_fn is not None:
        prof = skill_profile(t, sid, weapon_id, level)
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
            casts = float(uses if uses is not None else engagements)
            buffed = buff_fn(base, tuple(roots), casts)
            b = buff_option(t, sid, weapon_id, buffed, best_score, engagement, opponents, uses, cast,
                            engagements, roots=roots) if buffed else None
            if b:
                base['buff'] = b
                base['buff_gain'] = b['gain']
    return out


def _skill_variant_scored(t, weapon_id, sid, ctx, level, best_score, fp_bar, engagements, score_fn,
                          defender, def_module, damage_fn, poises, slot_extra_fn, engagement, opponents,
                          follow=None):
    """One variant of `_skill_option_scored` (`skill_option` with `follow`), or None when the
    follow-up has no hit."""
    o = skill_option(t, weapon_id, sid, ctx, level, defender, def_module, damage_fn, poises, follow)
    if o is None:
        return None
    uses = fp_uses(fp_bar, o['fp'])
    share = 1.0 if uses is None else min(1.0, uses / max(engagements, 1))
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
    print('  Every animation of the skill TimeAct is listed; which variant a given weapon and grip '
          'plays is the behavior script\'s choice (not traced). An id 5 above another without an '
          'fp line is its without-FP copy.')
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
    prof = skill_profile(t, sid, wid, a.level)
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
        prof = skill_profile(t, sid, wid, level)
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
          'EXE 0x140673f70')
    check('wepType->flag: Greatsword is colossal', WEP_TYPE_MOUNT_FLAG[t.reg.weapon[greatsword]['wepType']],
          'SwordGigantic', 'EXE 0x140d29e00 + COMMUNITY class')
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
              True, 'EXE 0x1406832a0 with MV 240')
        storm = t.find_arts('Storm Stomp')
        check('Storm Stomp classes', skill_profile(t, storm, claymore)['classes'], ['bullet'],
              'COMMUNITY: a stomp that releases a wind burst')
        sh = skill_hits(t, claymore, storm, ctx, 25)
        check('Storm Stomp bullet = 50 x baseAtkRate x scaling (AECP 51030)',
              round(sh[0]['attack']['physical'], 1),
              round(50 * ctx.base_atk_rate * ctx.multiplier('physical', 51030), 1),
              'EXE 0x1406832a0 flag-clear branch, k = baseAtkRate (FUN_140d53bf0); '
              'REGULATION AtkParam 30300870 overwriteAttackElementCorrectId')
        check('Storm Stomp PvP rate row', sh[0]['final_rate_id'], 10000, 'REGULATION AtkParam 30300870')
        check('FP: 20-cost skill with 10 FP plays in full', t.has_enough_fp(10, 20), True,
              'EXE 0x14047fc60, 0.5 at 0x1432a1920 (1.17.1)')
        check('FP: 20-cost skill with 9 FP plays the no-FP version', t.has_enough_fp(9, 20), False,
              'EXE 0x14047fc60')
        check('FP cost with Carian Filigreed-style 0.75 rate', t.fp_cost(lion, 'L2', 0.75), 15,
              'EXE 0x14068b220 ceil(rate x base)')
        check('one-handed, seal in left: right skill fires', t.active_skill(lion, 10, False), lion,
              'EXE 0x14047f770, SwordArtsParam 10 isRefRightArts')
        check('one-handed, shield with Parry in left: left skill fires',
              t.active_skill(lion, t.find_arts('Parry'), False), t.find_arts('Parry'), 'EXE 0x14047f770')
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
              'EXE 0x14047fc60: 5 x 16 = 80, 8 left >= int(16 x 0.5)')
        check('FP bar 85 pays for 5 casts of 16', fp_uses(85, 16), 5, 'EXE 0x14047fc60: 5 left < 8')
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
              (lion in ch, round(sum(ch.values()), 6)), (False, 1.0), 'EXE 0x140d549d0 via can_mount')
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
        dend = defensive_buff(t, endure, claymore)
        check('Endure: 1650 from f4 for 3 s, x0.6 PvP damage taken, reaction override to the flinch',
              (dend['root'], dend['start'], dend['duration_s'], dend['damage_taken'], dend['uninterruptible']),
              (1650, 4, 3.0, 0.6, True), 'REGULATION 1650 spCategory 1001 dmgLv_* 1 -> REMAP 0 (frame-advantage)')
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
              (True, True, False, False), 'EXE 0x140d549d0 via can_mount')
        glint = t.gem[t.ash_gems()[t.find_arts('Glintblade Phalanx')]]
        wrong = next(i for i in range(13) if not glint[f'configurableWepAttr{i:02d}'] and i != glint['defaultWepAttr'])
        check('an affinity the ash does not allow keeps it off (Glintblade Phalanx)',
              t.find_arts('Glintblade Phalanx') in mountable_skills(t, claymore, wrong, 25), False,
              'EquipParamGem configurableWepAttr, EXE 0x140d549d0')
        level = {**{k: 0 for k in BULLET_FIELDS}, 'life': 0.5, 'initVellocity': 10.0, 'maxVellocity': 10.0,
                 'minVellocity': 10.0, 'hitRadius': 0.1, 'dist': 0.0}
        path, ending, _, _ = bullet_flight(level, (0.0, 1.0, 0.0), (0.0, 0.0))
        check('bullet flight: 10 m/s level, no gravity, 0.5 s -> 31 frames (the expiry frame moves too), expires',
              (round(path[-1][2], 6), ending), (round(31 * 10 / 60, 6), 'expire'),
              'FUN_14039ac20 per-frame rule, 60 fps')
        path, ending, _, _ = bullet_flight({**level, 'gravityOutRange': 20.0}, (0.0, 1.0, 0.0), (0.0, 0.0))
        check('with gravity 20 it lands before its life ends (hit at height <= radius)',
              (ending, path[-1][1] <= 0.1, 2.5 < path[-1][2] < 3.5), ('hit', True, True),
              'FUN_14039f850; flat ground (INFERRED)')
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
              (0.6, round(0.6 * 0.09 / (0.6 + 0.09), 4), 0.0), 'FUN_1404504e0 / FUN_1404508c0')
        check('every protector row: knockBack 0 (no resist), knockbackParamId 1',
              set(t.protector_knockback.values()), {(KNOCKBACK_RESIST, KNOCKBACK_PLAYER_ROW)},
              'EquipParamProtector; FUN_140689ad0 / FUN_140451390')
        kb = t.knockback[KNOCKBACK_PLAYER_ROW]
        check('KnockBackParam 1 large: ContTime 0.09 s, DecTime 1.2 s',
              (round(kb['damage_L_ContTime'], 3), round(kb['damage_L_DecTime'], 3)), (0.09, 1.2), 'regulation')
        caller = t.find_arts('Stormcaller')
        storm_hits = skill_hits(t, claymore, caller, ctx, 25)
        for h in storm_hits:
            h['pvp_damage_total'] = 100.0
        broke = skill_landing(t, claymore, caller, storm_hits, [72.0], distances=(ENGAGE_DISTANCE_M,))
        held = skill_landing(t, claymore, caller, storm_hits, None, distances=(ENGAGE_DISTANCE_M,))
        check('Stormcaller at 2.5 m, poise 72: swing 1 and its bullet reach him on one update '
              '(one large stagger, roll at 35); swing 2 lands 18.7 frames on and restarts it at '
              'DamageCount 2 (roll at 15), its bullet 0.4 frames later lands, swing 3 20 frames '
              'later is escaped',
              broke['hit_share'], [1.0, 1.0, 1.0, 1.0, 0.0, 0.0],
              'frame-advantage.reaction large 35/15/5/0, TAE a623 040000 + hkx, bullet flight')
        check('Stormcaller with poise that holds: large -> 0 (SpEffect 6352), no lock; only the '
              'first swing and its bullet, which touch on the same update, land',
              held['hit_share'], [1.0, 1.0, 0.0, 0.0, 0.0, 0.0],
              'frame-advantage.md section 1; ExecAddDamage leaves the state')
        root = _clip_root_back(7)
        check('damage clips: small blow carries its target 4 m+ back, large none',
              (root is not None and root(2.0) > 4.0, _clip_root_back(3) is None), (True, True),
              'a000_005400 / 005300 root motion (MEASURED)')
        bolt = skill_profile(t, t.find_arts('Thunderbolt'), claymore)
        bolt_hits = skill_hits(t, claymore, t.find_arts('Thunderbolt'), ctx, 25)
        con = _hit_contacts(t, claymore, t.find_arts('Thunderbolt'), bolt_hits[:1])
        check("Thunderbolt's 200 m/s bolt (3.3 m a frame) is found on a defender 2.5 m ahead",
              (con[0][2], con[0][1](lambda _w: ENGAGE_DISTANCE_M) is not None), ('flight', True),
              f"Bullet 2080 sampled every {CONTACT_STEP_M} m ({main_anim(bolt)})")
        lsl_tree = fly_tree(t, lb)
        check("Lightning Slash: the falling bolt stops on the ground, so its blast starts there",
              round(lsl_tree[2][1][0][1], 3), round(t.bullet[lsl_tree[1][0]['bullet']]['hitRadius'], 3),
              'Bullet 2091 -> 2092 (HitBulletID at the contact point, FUN_14039bba0)')
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
    s.add_argument('--json', action='store_true')
    s = sub.add_parser('list', help='every skill with its FP and class')
    s.add_argument('--mountable', help='only ashes this weapon accepts (Standard affinity)')
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
     'ashrank': cmd_ashrank}.get(a.cmd, lambda *_: ap.print_help())(t, a)
    return 0


if __name__ == '__main__':
    sys.exit(main())
