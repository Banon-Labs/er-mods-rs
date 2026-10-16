#!/usr/bin/env python3
"""The duel ruleset's ban lists, resolved against the installed game, as a Rust table.

    python3 scripts/er-duel-rules-gen.py            # print the table
    python3 scripts/er-duel-rules-gen.py --write    # rewrite crates/er-duel-rules-core/src/tables.rs
    python3 scripts/er-duel-rules-gen.py --check    # exit 1 when that file no longer matches
    python3 scripts/er-duel-rules-gen.py --selftest

The ruleset names things in English ("Bloodhound's Step", "Rot Greases", "Winged Crystal Tear").
`er_duel_rules_core` compares param ids. This script is the only place one becomes the other:
each name is looked up in the game's own item text (`er-item-name.py`) and param tables
(`er-param-read.py`), and every SpEffect, bullet or skill row is reached by following the links
the game itself follows (`er-mechanics-buffs.Buffs._closure`). A name that resolves to nothing
stops the script instead of producing an empty ban.

Every row in the output carries the name it was reached from, so the table is also the list a
person reads to check what the DLL will call a rule break.

`--check` skips (exit 0) when the game or the item text cache is absent, the same line the other
regulation-backed gates draw.
"""

import argparse
import importlib.util
import io
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, 'crates', 'er-duel-rules-core', 'src', 'tables.rs')
# The shareable Frida agent; its data block between the markers is written here.
FRIDA_OUT = os.path.join(HERE, 'frida', 'er-duel-rules.js')
FRIDA_BEGIN = '// --- BEGIN GENERATED (scripts/er-duel-rules-gen.py --write) ---'
FRIDA_END = '// --- END GENERATED ---'
# Where each param sits in SoloParamRepository and its struct name, read from the same upstream
# list the DLL compiles against, so the agent and the DLL cannot disagree about a slot.
UPSTREAM_SOLO_PARAMS = os.path.join(os.path.dirname(ROOT), 'fromsoftware-rs', 'crates', 'eldenring',
                                    'src', 'cs', 'solo_param_repository.rs')
# The param columns the agent reads, by param.
FRIDA_PARAM_FIELDS = {
    'EquipParamWeapon': ('wepType', 'weight', 'physGuardCutRate', 'reinforceTypeId',
                         'spEffectBehaviorId0', 'spEffectBehaviorId1', 'spEffectBehaviorId2',
                         'isEnhance'),
    'ReinforceParamWeapon': ('physicsGuardCutRate',),
    'EquipParamProtector': ('weight', 'residentSpEffectId', 'residentSpEffectId2',
                            'residentSpEffectId3'),
    'EquipParamAccessory': ('weight', 'refId', 'refCategory', 'residentSpEffectId1',
                            'residentSpEffectId2', 'residentSpEffectId3', 'residentSpEffectId4'),
    'SpEffectParam': ('spCategory', 'equipWeightChangeRate', 'addEndureStatus'),
    'CalcCorrectGraph': tuple(f'{p}{i}' for p in ('stageMaxVal', 'stageMaxGrowVal',
                                                  'adjPt_maxGrowVal') for i in range(5)),
}


def _mod(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# `goodsType` of a crystal tear (er-mechanics-buffs `_sources`: 10 is a tear, 0 a consumable).
GOODS_TYPE_TEAR = 10
# `spCategory` of a weapon buff: 162 on the right weapon, 163 on the left (bd
# buff-ashes-share-grease-slot-162-isenhance-2026-09-29, `VERIFIED` against 1.17.1 regulation).
CAT_WEAPON_BUFF = (162, 163)
# A buff that "stays for its duration" outlives a refreshed marker row; the 2 s line is
# er-mechanics-buffs `REFRESHED_ROW_S`, below which rows are re-applied by whatever placed them.
BUFF_MIN_SECONDS = 2.0
# Player skill clips live in TimeAct `a<600 + swordArtsTypeNew>` (er-mechanics-ashes
# `SKILL_TAE_BASE`), so a runtime anim id divided by 1e6 names the skill.
SKILL_TAE_BASE = 600
# TAE events that put a SpEffect on the character (er-mechanics-ashes module doc): 66, 67, 401
# carry it in the first int, 331 (WeaponArtFPConsumption variant) carries two.
TAE_SPEFFECT_EVENTS = (66, 67, 401)
TAE_SPEFFECT_PAIR_EVENT = 331
# TAE event 2 BulletBehavior: the behavior judge sits at +8 (er-mechanics-ashes `anim_actions`).
TAE_BULLET_EVENT = 2
# TAE event 0 is a JumpTable; its first int is the table id.
TAE_JUMP_TABLE_EVENT = 0
# JumpTable 119 is the contact parry route (bd parry-model-static-facts-2026-10-10).
JUMP_TABLE_PARRY = 119
# SpEffectParam row names (Smithbox) that mark an equipment effect.
EQUIPMENT_ROW_PREFIXES = ('[Talisman]', '[Armor]', '[Protector]')

# Weapon classes, by a weapon the ruleset itself names or that is unambiguous. The class code is
# read from that weapon's `EquipParamWeapon.wepType`, never written down here.
WEAPON_CLASS_EXEMPLARS = {
    'DAGGER': 'Dagger',
    'CURVED_SWORD': 'Shamshir',
    'TWINBLADE': 'Twinblade',
    'THRUSTING_SWORD': 'Estoc',
    'HEAVY_THRUSTING_SWORD': 'Sword Lance',
    'AXE': 'Hand Axe',
    'GREAT_SPEAR': 'Lance',
    'FIST': 'Star Fist',
    'CLAW': 'Raptor Talons',
    'SMALL_SHIELD': 'Buckler',
    'MEDIUM_SHIELD': 'Kite Shield',
    'GREATSHIELD': 'Dragon Towershield',
    'THRUSTING_SHIELD': 'Dueling Shield',
    'BACKHAND_BLADE': 'Backhand Blade',
    'LIGHT_GREATSWORD': 'Milady',
}

# Weapons the ruleset names, by base id (affinity and upgrade stripped).
WEAPONS = {
    'SMITHSCRIPT_CIRQUE': 'Smithscript Cirque',
    'SMITHSCRIPT_SHIELD': 'Smithscript Shield',
    'SMITHSCRIPT_DAGGER': 'Smithscript Dagger',
    'CLAWS_OF_NIGHT': 'Claws of Night',
    'SPREAD_CROSSBOW': 'Spread Crossbow',
    'MILADY': 'Milady',
    'BACKHAND_BLADE': 'Backhand Blade',
    'REVERSE_BLADED_SWORD': 'Reverse-Bladed Sword',
    'CLEANROT_KNIGHTS_SWORD': "Cleanrot Knight's Sword",
    'PATA': 'Pata',
    'STONE_CLUB': 'Stone Club',
    'CLUB': 'Club',
    'SWORD_LANCE': 'Sword Lance',
    'RAPTOR_TALONS': 'Raptor Talons',
    'WAKIZASHI': 'Wakizashi',
    'MOHGWYNS_SACRED_SPEAR': "Mohgwyn's Sacred Spear",
    'STAR_FIST': 'Star Fist',
    'IDUS_SWORD': 'Idus Sword',
    'SHAMSHIR': 'Shamshir',
    'LEONTIELS_GREATSWORD': "Leontiel's Greatsword",
}
# "Ripple weapons" in the sleep-grease ban: every weapon whose name says Ripple.
RIPPLE_PATTERN = r'\bRipple\b'
# Affinity offsets inside a weapon id (checked below against the game's own row names).
AFFINITIES = {'LIGHTNING': (600, 'Lightning'), 'COLD': (900, 'Cold')}

TALISMANS = {
    'CRUCIBLE_FEATHER': 'Crucible Feather Talisman',
    'ALL_CRUCIBLES': 'Talisman of All Crucibles',
    'FINE_CRUCIBLE_FEATHER': 'Fine Crucible Feather Talisman',
    # The ruleset's "Red Feather"; the game calls it a branchsword.
    'RED_FEATHER': 'Red-Feathered Branchsword',
    'TWO_HANDED_SWORD': 'Two-Handed Sword Talisman',
    'SPEAR': 'Spear Talisman',
    # Skill damage: x1.10 / x1.15 on magicSubCategory 111/112 (Warrior Jar Shard, Shard of
    # Alexander), charged skills and spells (Godfrey Icon), and the stance chain Rellana's Cameo
    # adds through the skill's TimeAct.
    'WARRIOR_JAR_SHARD': 'Warrior Jar Shard',
    'SHARD_OF_ALEXANDER': 'Shard of Alexander',
    'RELLANAS_CAMEO': "Rellana's Cameo",
    'GODFREY_ICON': 'Godfrey Icon',
}

# Armour sets banned piece by piece.
ARMOR_SET_PATTERNS = {'Briar': r'\bBriar\b', 'Rakshasa': r'\bRakshasa'}

SKILLS = {
    'SPINNING_SLASH': 'Spinning Slash',
    'STORM_ASSAULT': 'Storm Assault',
    'STORMCALLER': 'Stormcaller',
    'SWORD_DANCE': 'Sword Dance',
    'FLAMING_STRIKE': 'Flaming Strike',
    'CHILLING_MIST': 'Chilling Mist',
    'STORM_STOMP': 'Storm Stomp',
    'DETERMINATION': 'Determination',
    'ROYAL_KNIGHTS_RESOLVE': "Royal Knight's Resolve",
    'BARBARIC_ROAR': 'Barbaric Roar',
    'ENDURE': 'Endure',
    'QUICKSTEP': 'Quickstep',
    'BLOODHOUNDS_STEP': "Bloodhound's Step",
    'RAPTOR_OF_THE_MISTS': 'Raptor of the Mists',
    'OATH_OF_VENGEANCE': 'Oath of Vengeance',
    'MULETA': 'Muleta',
    'SAVAGE_CLAWS': 'Savage Claws',
    'RED_BEAR_HUNT': 'Red Bear Hunt',
    'BLIND_SPOT': 'Blind Spot',
    'SWIFT_SLASH': 'Swift Slash',
    'SHRIEK_OF_SORROW': 'Shriek of Sorrow',
    'NEEDLE_PIERCER': 'Needle Piercer',
    'PAINFUL_STRIKE': 'Painful Strike',
}
# Skills whose TimeAct carries JumpTable 119, the contact parry, are exactly a692..a699 (full c0000
# TAE scan, bd parry-model-static-facts-2026-10-10). "All Parries" is that range.
PARRY_TYPE_RANGE = range(92, 100)
# The fast Sword Dance: a624's 040000 family (dagger, straight and curved sword -- Shamshir --,
# katana, axe, light greatsword) and 045800 family (backhand blade), whose TimeAct events match
# 040000's frame for frame (hits on frames 23/35). 040200 (great weapons, 30/42), 040300 (spears,
# halberds, scythes) and 042400 (twinblades) are the slow ones. From `er-mechanics-ashes.py
# variants "Sword Dance"` and bd skill-opening-varies-by-weapon-class-diffcategory-2026-10-08.
SWORD_DANCE_FAST_CLIPS = (40000, 40005, 40010, 40015, 45800, 45805, 45810, 45815)
# On-hit heals the ruleset keeps legal by name ("manually triggerable").
ALLOWED_HEAL_SKILLS = ('Blood Tax', 'Prayerful Strike', 'Lifesteal Fist', "Taker's Flames",
                       'Devourer of Worlds')

TEARS_BANNED_BY_NAME = ('Deflecting Hardtear', 'Leaden Hardtear', 'Winged Crystal Tear',
                        'Windy Crystal Tear', 'Stonebarb Cracked Tear')
# Tears whose heal is a reaction to hitting, the same shape as the ruleset's legal examples
# (Swaddling Cloth, Blood Tax); the ruleset names Cerulean-Sapping as legal outright.
TEARS_ON_HIT_HEAL = ('Crimson-Sapping Cracked Tear', 'Cerulean-Sapping Cracked Tear')

# Flasks: Crimson is legal, Cerulean is counted.
CRIMSON_FLASK = r'^Flask of Crimson Tears'
CERULEAN_FLASK = r'^Flask of Cerulean Tears'
WONDROUS_PHYSICK = r'^Flask of Wondrous Physick'
FAN_DAGGERS = 'Fan Daggers'
# `EquipParamGoods.goodsType` of spirit ashes: 7 plain, 8 renowned (measured on 1.17.1: these two
# types name 84 of the 94 BuddyParam trigger SpEffects and nothing else does).
SPIRIT_ASH_GOODS_TYPES = (7, 8)
# SpEffectParam row names of an active Rune Arc: "Rune Arc" (490, 491, 499) and
# "[Item] <name>'s Great Rune: Effect N"; group 1 is the rune's name.
RUNE_ARC_ROW = r"^(?:Rune Arc$|\[Item\] (.+?Great Rune): Effect \d+$)"
SPELLS = {
    'GRAVITATIONAL_MISSILE': 'Gravitational Missile',
    'NIGHT_MAIDENS_MIST': "Night Maiden's Mist",
    # The charged, aimable base-game incantation the ruleset describes, not Midra's.
    'FLAME_OF_FRENZY': 'The Flame of Frenzy',
    'ROAR_OF_RUGALEA': 'Roar of Rugalea',
}
# The grease names that inflict each status: Soporific Grease and Eternal Sleep Grease both sleep.
GREASE_STATUS = {'ROT': ('Rot',), 'SLEEP': ('Sleep', 'Soporific'), 'POISON': ('Poison',)}

ATTACK_FIELDS = ('AttackPowerRate', 'AttackRate', 'AttackPower', 'atkPlayerDmgCorrectRate')
STAT_FIELDS = ('addLifeForceStatus', 'addWillpowerStatus', 'addEndureStatus',
               'addStrengthStatus', 'addDexterityStatus', 'addMagicStatus', 'addFaithStatus',
               'addLuckStatus')
STAT_CHANGE_FIELDS = ('changeStrengthPoint', 'changeAgilityPoint', 'changeMagicPoint',
                      'changeFaithPoint', 'changeLuckPoint')
# Status build-up a row puts on its holder.
STATUS_BUILDUP_FIELDS = ('poizonAttackPower', 'diseaseAttackPower', 'bloodAttackPower',
                         'curseAttackPower', 'freezeAttackPower', 'sleepAttackPower',
                         'madnessAttackPower')
# Flat elemental attack added to the holder's attacks.
ELEMENT_ATTACK_POWER_FIELDS = ('physicsAttackPower', 'magicAttackPower', 'fireAttackPower',
                               'thunderAttackPower', 'darkAttackPower', 'slashAttackPower',
                               'blowAttackPower', 'thrustAttackPower', 'neutralAttackPower')
# stateInfo values that are a buff on their own. 8 is the invisibility Unseen Form applies.
BUFF_STATES = (8,)
# Status resistance a row adds to its holder (the cured meats, Dragonbolt Blessing).
RESIST_POINT_FIELDS = tuple(f'change{s}ResistPoint' for s in
                            ('Poison', 'Disease', 'Blood', 'Curse', 'Freeze', 'Sleep', 'Madness'))
RESIST_RATE_FIELDS = tuple(f'regist{s}ChangeRate' for s in
                           ('Poizon', 'Disease', 'Blood', 'Curse', 'Freeze', 'Sleep', 'Madness'))
STATUS_DISABLE_FIELDS = tuple(f'disable{s}' for s in
                              ('Poison', 'Disease', 'Blood', 'Curse', 'Freeze', 'Sleep', 'Madness'))
# `BehaviorParam_PC.refType`.
BEHAVIOR_REF_BULLET = 1
BEHAVIOR_REF_SPEFFECT = 2
# Area buffs re-applied every second or so while the player stands in them, whose rows are
# therefore shorter than BUFF_MIN_SECONDS. Holy Ground's row lasts 2.9 s and passes on its own.
AREA_BUFF_SPELLS = ('Terra Magica',)


class Missing(Exception):
    pass


class Game:
    def __init__(self):
        self.PR = _mod('er_param_read', 'er-param-read.py')
        self.IN = _mod('er_item_name', 'er-item-name.py')
        self.B = _mod('er_mechanics_buffs', 'er-mechanics-buffs.py')
        self.ATK = _mod('er_mechanics_attacks', 'er-mechanics-attacks.py')
        self.buffs = self.B.Buffs()
        files = self.PR.load()
        self.files = files

        def rows(stem, fields=None):
            return {r['id']: r for r in self.PR.rows(self.PR.param_bytes(files, stem), fields)[0]}

        self.weapon = rows('EquipParamWeapon', {'wepType', 'reinforceTypeId'})
        self.arts = rows('SwordArtsParam', {'swordArtsTypeNew'})
        self.goods = rows('EquipParamGoods', {'goodsType', 'refCategory', 'refId_default',
                                              'refId_1'})
        self.magic = self.buffs.magic
        # Every column: the buff test reads FP regen and the target flags, which the buff model
        # does not load.
        self.sp = rows('SpEffectParam')
        self.sp_names = self.PR.row_names('SpEffectParam')
        self.behavior = rows('BehaviorParam_PC', {'refType', 'refId'})
        missing = [f for f in RESIST_POINT_FIELDS + RESIST_RATE_FIELDS + STATUS_DISABLE_FIELDS
                   + ('fallDamageRate', 'toughnessDamageCutRate', 'behaviorId',
                      'cycleOccurrenceSpEffectId')
                   if f not in next(iter(self.sp.values()))]
        if missing:
            raise Missing(f'SpEffectParam has no {missing}')
        self.text = {s: self.IN.game_names(s) for s in (
            'WeaponName', 'AccessoryName', 'ProtectorName', 'GoodsName', 'ArtsName')}
        self.weapon_rows = self.PR.row_names('EquipParamWeapon')
        self._ash = None
        self._skill_weapon = None

    def ash(self):
        """er-mechanics-ashes and its tables, loaded once on first use (about 4 s)."""
        if self._ash is None:
            ASH = _mod('er_mechanics_ashes', 'er-mechanics-ashes.py')
            t = ASH.AshTables()
            # A weapon each skill can be performed with: its own, else the first that mounts it.
            # Which one does not matter here; it only picks the behavior variation a bullet judge
            # resolves through.
            weapons = {}
            for w in sorted(x for x in t.reg.weapon
                            if x % 10000 == 0 and x in self.text['WeaponName']):
                native = t.reg.weapon[w].get('swordArtsParamId')
                if native:
                    weapons.setdefault(native, w)
                for sid in ASH.mountable_skills(t, w):
                    weapons.setdefault(sid, w)
            self._ash = (ASH, t)
            self._skill_weapon = weapons
        return self._ash

    # -- names -> ids -------------------------------------------------------------------------
    def one(self, stem, name, where=lambda i: True):
        hits = sorted(i for i, t in self.text[stem].items() if t == name and where(i))
        if not hits:
            raise Missing(f'{stem}: no row is called {name!r}')
        return hits[0]

    def weapon_base(self, name):
        return self.one('WeaponName', name, lambda i: i % 10000 == 0 and i in self.weapon)

    def goods_matching(self, pattern):
        rx = re.compile(pattern)
        return sorted((i, t) for i, t in self.text['GoodsName'].items()
                      if rx.search(t) and i in self.goods)

    # -- links the game follows ----------------------------------------------------------------
    def closure(self, sp=(), bullets=()):
        """Every SpEffect reachable from the roots, including through a row's `behaviorId`
        (BehaviorParam_PC refType 1 is a bullet, 2 a SpEffect): the Crimsonburst Dried Tear heals
        through 20511011 -> behavior 6010 -> bullet 211101000 -> 20511012."""
        sp, bullets = list(sp), list(bullets)
        while True:
            rows = [i for i, _ in self.buffs._closure(sp, bullets)]
            more_sp, more_b = [], []
            for s in rows:
                beh = self.behavior.get(self.sp[s]['behaviorId'])
                if beh is None:
                    continue
                if beh['refType'] == BEHAVIOR_REF_BULLET and beh['refId'] not in bullets:
                    more_b.append(beh['refId'])
                elif beh['refType'] == BEHAVIOR_REF_SPEFFECT and beh['refId'] not in sp:
                    more_sp.append(beh['refId'])
            if not more_sp and not more_b:
                return rows
            sp += more_sp
            bullets += more_b

    def heal_rows(self, sp=(), bullets=()):
        """The rows in `closure` that heal, plus heals a bullet lands on friends including the
        user (`effectTargetSelf` with `effectTargetFriendlyTarget`, not `effectTargetSelfTarget`):
        the Crimsonburst Dried Tear's 20511012 reaches its user that way."""
        rows = self.closure(sp, bullets)
        out = {s for s in rows if self.heals(s)}
        shot = list(bullets)
        for s in rows:
            beh = self.behavior.get(self.sp[s]['behaviorId'])
            if beh is not None and beh['refType'] == BEHAVIOR_REF_BULLET:
                shot.append(beh['refId'])
        for b in self.bullet_closure(shot):
            for f in self.B.BULLET_SP:
                s = self.buffs.bullet[b][f]
                if (s in self.sp and self.sp[s]['effectTargetSelf']
                        and self.sp[s]['effectTargetFriendlyTarget'] and self.heals(s)):
                    out.add(s)
        return sorted(out)

    def goods_refs(self, gid):
        g = self.goods[gid]
        refs = [g['refId_default'], g['refId_1']]
        if g['refCategory'] == 2:
            return refs, []
        if g['refCategory'] == 1:
            return [], refs
        return [], []

    def magic_refs(self, mid):
        m = self.magic.get(mid)
        if m is None:
            return [], []
        sp = [m[f'refId{i}'] for i in range(1, 11) if m[f'refCategory{i}'] == 2]
        # Category 1 is a bullet. The DLC spells leave the category at 0 on every slot past the
        # second and still name bullets there (Roar of Rugalea's 210680000..210680053), so a
        # category-0 id the Bullet table holds is taken as one too.
        bl = [m[f'refId{i}'] for i in range(1, 11)
              if m[f'refCategory{i}'] == 1
              or (m[f'refCategory{i}'] == 0 and m[f'refId{i}'] in self.buffs.bullet)]
        return sp, bl

    def bullet_closure(self, roots):
        seen, todo = set(), list(roots)
        while todo:
            b = todo.pop()
            if b is None or b <= 0 or b in seen or b not in self.buffs.bullet:
                continue
            seen.add(b)
            row = self.buffs.bullet[b]
            todo += [row[f] for f in self.B.BULLET_LINKS]
        return seen

    def skill_speffects(self, arts_id):
        """Every SpEffect a skill puts on its user: the TimeAct's own SpEffect events, and what
        its bullets apply on hit or to the shooter (Rallying Standard's banner is a bullet whose
        hit row 1768 is the buff). Links followed."""
        t = SKILL_TAE_BASE + self.arts[arts_id]['swordArtsTypeNew']
        roots = []
        judges = []
        for events in (self.ATK.tae_animations(t) or {}).values():
            for e in events:
                if e.type in TAE_SPEFFECT_EVENTS and len(e.params) >= 4:
                    roots.append(int.from_bytes(e.params[0:4], 'little', signed=True))
                elif e.type == TAE_SPEFFECT_PAIR_EVENT and len(e.params) >= 8:
                    roots.append(int.from_bytes(e.params[0:4], 'little', signed=True))
                    roots.append(int.from_bytes(e.params[4:8], 'little', signed=True))
                elif e.type == TAE_BULLET_EVENT and len(e.params) >= 12:
                    judges.append(int.from_bytes(e.params[8:12], 'little', signed=True))
        if judges:
            ASH, tables = self.ash()
            weapon = self._skill_weapon.get(arts_id)
            if weapon is not None:
                for judge in judges:
                    res = ASH.resolve_judge(tables, weapon, judge, via_bullet=True)
                    todo = [res.get('bullet')]
                    while todo:
                        bt = todo.pop()
                        if not bt:
                            continue
                        roots += bt['on_hit_speffects']
                        if bt['shooter_speffect']:
                            roots.append(bt['shooter_speffect'])
                        todo += list(bt['children'].values())
        return self.closure(roots)

    # -- row classification ----------------------------------------------------------------------
    def heals(self, sid):
        r = self.sp[sid]
        return r['changeHpPoint'] < 0 or r['changeHpRate'] < 0

    def from_equipment(self, sid):
        """A row the param names as a talisman's or armour's effect. Skills carry these as
        conditional events (Unsheathe applies Rellana's Cameo's 20382204 only when the cameo is
        worn), and the ruleset leaves equipment effects alone."""
        return self.sp_names.get(sid, '').startswith(EQUIPMENT_ROW_PREFIXES)

    def harmful(self, sid):
        """A row that hurts whoever holds it: status build-up, HP lost over time. Frostbite,
        the mists and the pots reach a caster's closure because their bullets can hit the
        caster too; they are not buffs."""
        r = self.sp[sid]
        return (any(r[f] > 0 for f in STATUS_BUILDUP_FIELDS)
                or r['changeHpPoint'] > 0 or r['changeHpRate'] > 0)

    def benefit(self, sid):
        """The first column that leaves the holder better off -- more damage dealt, less taken,
        attributes, pools, equip load, poise, FP over time -- or None. Oil Pot's coating (fire
        damage taken x1.5) has none."""
        r = self.sp[sid]
        for f in self.B.EFFECT_FIELDS:
            v = r[f]
            if f.startswith(('atkPlayerDmgCorrectRate', 'atkEnemyDmgCorrectRate')) and v > 1 + 1e-6:
                return f
            if f.startswith(('defPlayerDmgCorrectRate', 'defEnemyDmgCorrectRate')) and v < 1 - 1e-6:
                return f
            if f.endswith(('AttackPowerRate', 'AttackRate')) and v > 1 + 1e-6:
                return f
            if f in ELEMENT_ATTACK_POWER_FIELDS and v > 0:
                return f
            if f.endswith('DamageCutRate') and 0 <= v < 1 - 1e-6:
                return f
            if f in STAT_FIELDS + STAT_CHANGE_FIELDS and v > 0:
                return f
            if f in ('maxHpRate', 'maxMpRate', 'maxStaminaRate', 'equipWeightChangeRate',
                     'saAttackPowerRate') and v > 1 + 1e-6:
                return f
            if f == 'staminaRecoverChangeSpeed' and v > 0:
                return f
            if f == 'guardStaminaCutRate' and 0 <= v < 1 - 1e-6:
                return f
        if r['motionInterval'] > 0 and (r['changeMpPoint'] < 0 or r['changeMpRate'] < 0):
            return 'changeMp (FP over time)'
        # Status resistance: the cured meats, boluses' long rows, Dragonbolt Blessing.
        for f in RESIST_POINT_FIELDS:
            if r[f] > 0:
                return f
        for f in RESIST_RATE_FIELDS:
            if r[f] > 1 + 1e-6:
                return f
        for f in STATUS_DISABLE_FIELDS:
            if r[f]:
                return f
        # Fall damage (Assassin's Approach, Soft Cotton) and poise damage taken.
        for f in ('fallDamageRate', 'toughnessDamageCutRate'):
            if 0 <= r[f] < 1 - 1e-6:
                return f
        return None

    def cycled_benefit(self, sid, depth=0):
        """The benefit of the short row a long parent re-applies on a cycle (Ironjar Aromatic's
        40 s 503520 carries its damage cut in the 0.2 s 503522), or None."""
        c = self.sp[sid]['cycleOccurrenceSpEffectId']
        if depth > 6 or c <= 0 or c not in self.sp:
            return None
        return self.benefit(c) or self.cycled_benefit(c, depth + 1)

    def body_buff(self, sid):
        """A buff that stays on the player whatever they hold: it helps, it does not hurt, it
        outlives a refreshed marker, and it is not a weapon buff. The state-only buffs in
        `BUFF_STATES` count too (Unseen Form changes no number)."""
        r = self.sp[sid]
        if r['spCategory'] in CAT_WEAPON_BUFF or self.from_equipment(sid) or self.harmful(sid):
            return False
        d = r['effectEndurance']
        if 0 <= d < BUFF_MIN_SECONDS:
            return False
        return (self.benefit(sid) is not None or self.cycled_benefit(sid) is not None
                or r['stateInfo'] in BUFF_STATES)

    def boosts_attack(self, sid):
        r = self.sp[sid]
        for f in self.B.EFFECT_FIELDS:
            if not any(f.endswith(a) or f.startswith(a) for a in ATTACK_FIELDS):
                continue
            v = float(r[f])
            if f.endswith('Rate') or f.startswith('atkPlayer'):
                if v > 1.0 + 1e-6:
                    return True
            elif v > 0:
                return True
        return False

    def boosts_stats(self, sid):
        return any(int(self.sp[sid][f]) > 0 for f in STAT_FIELDS)


def build(g):
    out = {}
    # Weapon classes.
    out['wep_type'] = {}
    for const, name in WEAPON_CLASS_EXEMPLARS.items():
        wid = g.weapon_base(name)
        out['wep_type'][const] = (g.weapon[wid]['wepType'], name)
    # Named weapons.
    out['weapon'] = {c: (g.weapon_base(n), n) for c, n in WEAPONS.items()}
    rx = re.compile(RIPPLE_PATTERN)
    out['ripple'] = sorted((i, t) for i, t in g.text['WeaponName'].items()
                           if i % 10000 == 0 and i in g.weapon and rx.search(t))
    if not out['ripple']:
        raise Missing('no Ripple weapon')
    out['affinity'] = {}
    for const, (offset, word) in AFFINITIES.items():
        probe = out['weapon']['REVERSE_BLADED_SWORD'][0] + offset
        row_name = g.weapon_rows.get(probe, '')
        if not row_name.startswith(word):
            raise Missing(f'affinity {word}: row {probe} is named {row_name!r}')
        out['affinity'][const] = (offset, word)
    out['talisman'] = {c: (g.one('AccessoryName', n), n) for c, n in TALISMANS.items()}
    out['armor'] = []
    for set_name, pattern in ARMOR_SET_PATTERNS.items():
        prx = re.compile(pattern)
        hits = sorted((i, t) for i, t in g.text['ProtectorName'].items() if prx.search(t))
        if not hits:
            raise Missing(f'no {set_name} armour')
        out['armor'] += hits
    # Skills.
    type_of = {}
    for const, name in SKILLS.items():
        aid = g.one('ArtsName', name, lambda i: i in g.arts)
        type_of[const] = (g.arts[aid]['swordArtsTypeNew'], name)
    out['skill'] = type_of
    parry_types = set()
    for type_new in PARRY_TYPE_RANGE:
        for events in (g.ATK.tae_animations(SKILL_TAE_BASE + type_new) or {}).values():
            if any(e.type == TAE_JUMP_TABLE_EVENT and len(e.params) >= 4
                   and int.from_bytes(e.params[0:4], 'little', signed=True) == JUMP_TABLE_PARRY
                   for e in events):
                parry_types.add(type_new)
                break
    out['parry'] = sorted((r['swordArtsTypeNew'], g.text['ArtsName'][i])
                          for i, r in g.arts.items()
                          if r['swordArtsTypeNew'] in parry_types and i in g.text['ArtsName'])
    if not any(n == 'Parry' for _, n in out['parry']):
        raise Missing('no parry skill carries JumpTable 119')
    # Chilling Mist's own weapon buff rows.
    cm = g.one('ArtsName', SKILLS['CHILLING_MIST'], lambda i: i in g.arts)
    out['chilling_mist'] = sorted({s for s in g.skill_speffects(cm)
                                   if g.sp[s]['spCategory'] in CAT_WEAPON_BUFF})
    if not out['chilling_mist']:
        raise Missing('Chilling Mist puts no weapon buff on the user')

    # Flasks.
    crimson = g.goods_matching(CRIMSON_FLASK)
    cerulean = g.goods_matching(CERULEAN_FLASK)
    if not crimson or not cerulean:
        raise Missing('flasks')
    out['cerulean'] = sorted({s for gid, _ in cerulean for s in g.closure(*g.goods_refs(gid))})
    allowed = set()
    for gid, _ in crimson + cerulean + g.goods_matching(WONDROUS_PHYSICK):
        allowed |= set(g.closure(*g.goods_refs(gid)))

    # Tears: everything a tear can do is allowed except what the ruleset bans.
    tears = sorted((i, t) for i, t in g.text['GoodsName'].items()
                   if i in g.goods and g.goods[i]['goodsType'] == GOODS_TYPE_TEAR
                   and not t.startswith('['))
    tear_ban, tear_rows = [], {}
    for gid, name in tears:
        rows = g.closure(*g.goods_refs(gid))
        tear_rows[gid] = rows
        why = []
        if name in TEARS_BANNED_BY_NAME:
            why.append('Named')
        if any(g.boosts_stats(s) for s in rows):
            why.append('Stat')
        if any(g.boosts_attack(s) for s in rows):
            why.append('Damage')
        if name not in TEARS_ON_HIT_HEAL and g.heal_rows(*g.goods_refs(gid)):
            why.append('Heal')
        if why:
            tear_ban.append((gid, name, why[0]))
        allowed |= set(rows)
    for name in TEARS_BANNED_BY_NAME:
        if not any(n == name for _, n, _ in tear_ban):
            raise Missing(f'tear {name!r}')
    out['tear_ban'] = tear_ban

    # Spells and consumables: body buffs and heals.
    buffs, heals = {}, {}
    for gid, g_row in sorted(g.goods.items()):
        name = g.text['GoodsName'].get(gid)
        if not name or name.startswith('['):
            continue
        is_spell = g_row['goodsType'] in g.B.SPELL_TYPES
        if is_spell:
            sp, bl = g.magic_refs(gid)
        elif g_row['goodsType'] == 0:
            sp, bl = g.goods_refs(gid)
        else:
            continue
        rows = g.closure(sp, bl)
        # A weapon-buff spell's own self rows (Vyke's Dragonbolt's equip-load row) go with the
        # weapon buff, which the ruleset restricts by setup rather than bans.
        weapon_buff = any(g.sp[s]['spCategory'] in CAT_WEAPON_BUFF for s in rows)
        area = name in AREA_BUFF_SPELLS
        for s in rows:
            if s in allowed:
                continue
            if g.heals(s):
                heals.setdefault(s, name)
            elif not weapon_buff and (g.body_buff(s) or (area and g.benefit(s) is not None)):
                buffs.setdefault(s, name)
        for s in g.heal_rows(sp, bl):
            if s not in allowed:
                heals.setdefault(s, name)
    for name in AREA_BUFF_SPELLS:
        if name not in buffs.values():
            raise Missing(f'area buff {name!r} reaches no benefit row')
    # Skills: body buffs and heals the TimeAct applies. A buff skill's TimeAct applies both hands'
    # weapon chains, so a weapon buff here does not excuse a body buff the way it does for spells.
    status_skills = {}
    for aid in sorted(g.arts):
        name = g.text['ArtsName'].get(aid)
        if not name:
            continue
        rows = g.skill_speffects(aid)
        # Status a skill applies through its bullets and its own events (Poisonous Mist's cloud,
        # Hoarfrost Stomp's frost). Melee hits of a skill carry none on a Club or Stone Club: all
        # 164 AtkParam rows of the 45 skills they can mount have no status on-hit row (measured
        # on 1.17.1).
        if any(g.sp[s][f] > 0 for s in rows for f in STATUS_BUILDUP_FIELDS):
            status_skills.setdefault(g.arts[aid]['swordArtsTypeNew'], name)
        for s in rows:
            if s in allowed:
                continue
            if g.heals(s):
                if name not in ALLOWED_HEAL_SKILLS:
                    heals.setdefault(s, name)
            elif g.body_buff(s):
                buffs.setdefault(s, name)
    out['body_buff'] = sorted(buffs.items())
    out['heal'] = sorted(heals.items())
    out['status_skills'] = sorted(status_skills.items())
    for need in ('Poisonous Mist', 'Hoarfrost Stomp'):
        if need not in status_skills.values():
            raise Missing(f'status skill {need!r} not found')

    # Equipment that regenerates HP on its own: talismans, armour, shields.
    shield_types = {out['wep_type'][c][0] for c in ('SMALL_SHIELD', 'MEDIUM_SHIELD', 'GREATSHIELD',
                                                     'THRUSTING_SHIELD')}
    regen = []
    for key, ids in sorted(g.buffs.regen_sources().items()):
        kind, _, name = key.partition(':')
        if kind == 'talisman':
            regen.append(('Talisman', g.one('AccessoryName', name), name))
        elif kind == 'armor':
            for i, t in g.text['ProtectorName'].items():
                if t == name:
                    regen.append(('Armor', i, name))
        elif kind == 'weapon':
            wid = g.one('WeaponName', name, lambda i: i % 10000 == 0 and i in g.weapon)
            if g.weapon[wid]['wepType'] in shield_types:
                regen.append(('Shield', wid, name))
    out['regen_equipment'] = sorted(set(regen), key=lambda r: (r[0], r[1]))

    # Greases.
    greases = {}
    for const, words in GREASE_STATUS.items():
        for word in words:
            rows = set()
            for gid, name in g.goods_matching(rf'\b{word}\b.*Grease$'):
                rows |= {s for s in g.closure(*g.goods_refs(gid))
                         if g.sp[s]['spCategory'] in CAT_WEAPON_BUFF}
            if not rows:
                raise Missing(f'{word} grease')
            greases.setdefault(const, set()).update(rows)
    out['grease'] = {c: sorted(rows) for c, rows in greases.items()}
    # Every grease's weapon buff rows: a grease can only be applied to a weapon whose row has
    # isEnhance set (CanUseGoods), so one on any other weapon was carried there.
    every = set()
    for gid, name in g.goods_matching(r'Grease$'):
        every |= {s for s in g.closure(*g.goods_refs(gid))
                  if g.sp[s]['spCategory'] in CAT_WEAPON_BUFF}
    if not every:
        raise Missing('no grease applies a weapon buff')
    out['grease_all'] = sorted(every)

    # Projectiles.
    fan = g.one('GoodsName', FAN_DAGGERS, lambda i: i in g.goods)
    out['fan_dagger_bullets'] = sorted(g.bullet_closure(g.goods_refs(fan)[1]))
    if not out['fan_dagger_bullets']:
        raise Missing('Fan Daggers fire no bullet')
    others = set()
    banned_spells = {}
    for const, name in SPELLS.items():
        mid = g.one('GoodsName', name, lambda i: i in g.magic)
        banned_spells[const] = (mid, name)
    banned_ids = {mid for mid, _ in banned_spells.values()}
    # A bullet another player-obtainable spell or item also fires is no evidence of the banned
    # one. Rows the item text does not name (NPC and cut spells such as 53043 and 2050090, which
    # reuse The Flame of Frenzy's bullets) cannot be cast by a player and are not subtracted.
    for mid in g.magic:
        if mid not in banned_ids and mid in g.text['GoodsName']:
            others |= g.bullet_closure(g.magic_refs(mid)[1])
    for gid in g.goods:
        if gid not in banned_ids and gid in g.text['GoodsName']:
            others |= g.bullet_closure(g.goods_refs(gid)[1])
    out['spell_bullets'] = {}
    for const, (mid, name) in banned_spells.items():
        own = g.bullet_closure(g.magic_refs(mid)[1]) - others
        if not own:
            raise Missing(f'{name}: no bullet of its own')
        out['spell_bullets'][const] = (name, sorted(own))

    # Rune Arc: the item's own rows and every Great Rune effect row it switches on (ApplyRuneArcEffects
    # applies "<rune>: Effect 0..9" for the equipped rune). Any of them on a player means an active
    # Rune Arc; the activation SFX row is a one-off and left out.
    rune_arc = []
    for sid, row_name in sorted(g.sp_names.items()):
        m = re.match(RUNE_ARC_ROW, row_name or '')
        if m and sid in g.sp:
            rune_arc.append((sid, m.group(1) or 'Rune Arc'))
    if not any(n == 'Rune Arc' for _, n in rune_arc) or len({n for _, n in rune_arc}) < 2:
        raise Missing('Rune Arc or Great Rune effect rows')
    out['rune_arc'] = rune_arc

    # Spirit Ashes: summoning applies the BuddyParam row's trigger SpEffect to the summoner, the
    # key of WorldChrMan's SummonBuddyManager trigger map. Named by the spirit ash item
    # (goodsType 7 or 8) whose effect reference is that row.
    buddy = g.PR.rows(g.PR.param_bytes(g.files, 'BuddyParam'), {'triggerSpEffectId'})[0]
    triggers = {r['triggerSpEffectId'] for r in buddy if r['triggerSpEffectId'] > 0}
    named_by = {}
    for gid, r in sorted(g.goods.items()):
        if r['goodsType'] not in SPIRIT_ASH_GOODS_TYPES or gid not in g.text['GoodsName']:
            continue
        for k in ('refId_default', 'refId_1'):
            if r[k] in triggers:
                named_by.setdefault(r[k], g.text['GoodsName'][gid])
    if not named_by:
        raise Missing('no spirit ash names a BuddyParam trigger SpEffect')
    out['spirit_ash'] = sorted((s, named_by.get(s, 'spirit summon')) for s in triggers)
    return out


def rust_str(s):
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


def render(t):
    w = io.StringIO()
    p = lambda s='': w.write(s + '\n')
    p('//! The ruleset\'s ban lists as param ids, generated by `scripts/er-duel-rules-gen.py` from')
    p('//! the installed game. Do not edit: rerun `python3 scripts/er-duel-rules-gen.py --write`.')
    p('//!')
    p('//! Every row keeps the name it was reached from, so this file is also the list to read when')
    p('//! checking what the DLL will report.')
    p()
    p('use crate::Named;')
    p()
    p('/// `EquipParamWeapon.wepType`, read off the named weapon.')
    p('pub mod wep_type {')
    for c, (v, n) in t['wep_type'].items():
        p(f'    /// Class of {n}.')
        p(f'    pub const {c}: u16 = {v};')
    p('}')
    p()
    p('/// Weapons by base id (`id - id % 10000`: affinity and upgrade stripped).')
    p('pub mod weapon {')
    for c, (v, n) in t['weapon'].items():
        p(f'    /// {n}')
        p(f'    pub const {c}: i32 = {v};')
    p('}')
    p()
    p('/// Affinity offset inside a weapon id (`id % 10000 / 100 * 100`).')
    p('pub mod affinity {')
    for c, (v, n) in t['affinity'].items():
        p(f'    /// {n}')
        p(f'    pub const {c}: i32 = {v};')
    p('}')
    p()
    p('/// `EquipParamAccessory` row ids.')
    p('pub mod talisman {')
    for c, (v, n) in t['talisman'].items():
        p(f'    /// {n}')
        p(f'    pub const {c}: i32 = {v};')
    p('}')
    p()
    p('/// `SwordArtsParam.swordArtsTypeNew`: the skill\'s clips play from TimeAct `a<600 + this>`.')
    p('pub mod skill {')
    for c, (v, n) in t['skill'].items():
        p(f'    /// {n}')
        p(f'    pub const {c}: i32 = {v};')
    p('}')
    p()

    def named(const, doc, rows):
        p(f'/// {doc}')
        p(f'pub const {const}: &[Named] = &[')
        for i, n in rows:
            p(f'    Named {{ id: {i}, name: {rust_str(n)} }},')
        p('];')
        p()

    def ids(const, doc, rows, ty='i32'):
        p(f'/// {doc}')
        p(f'pub const {const}: &[{ty}] = &[')
        for i in rows:
            p(f'    {i},')
        p('];')
        p()

    named('RIPPLE_WEAPONS', 'Weapons the sleep-grease ban calls Ripple weapons, by base id.',
          t['ripple'])
    named('BANNED_ARMOR', 'Every piece of the Briar and Rakshasa sets (`EquipParamProtector`).',
          t['armor'])
    named('PARRY_SKILLS', 'Skills whose clips carry the contact parry (swordArtsTypeNew).',
          t['parry'])
    ids('SWORD_DANCE_FAST_CLIPS', 'Sword Dance clips the light weapons play (clip id inside '
        'a624).', SWORD_DANCE_FAST_CLIPS)
    ids('CHILLING_MIST_BUFFS', 'Weapon buff rows Chilling Mist puts on the user\'s weapon.',
        t['chilling_mist'])
    p('/// Why a crystal tear is banned.')
    p('#[derive(Clone, Copy, Debug, PartialEq, Eq)]')
    p('pub enum TearBan {')
    p('    /// Named by the ruleset.')
    p('    Named,')
    p('    /// Raises an attribute.')
    p('    Stat,')
    p('    /// Raises damage dealt.')
    p('    Damage,')
    p('    /// Restores HP.')
    p('    Heal,')
    p('}')
    p()
    p('/// Crystal tears banned in the flask (`EquipParamGoods` ids).')
    p('pub const BANNED_TEARS: &[(Named, TearBan)] = &[')
    for i, n, why in t['tear_ban']:
        p(f'    (Named {{ id: {i}, name: {rust_str(n)} }}, TearBan::{why}),')
    p('];')
    p()
    named('BODY_BUFFS', 'SpEffect rows that buff the player whatever they hold, with the spell, '
          'item or skill that applies them.', t['body_buff'])
    named('HEALS', 'SpEffect rows that restore HP outside the Crimson flask and the on-hit heals '
          'the ruleset keeps.', t['heal'])
    p('/// Equipment that regenerates HP on its own: (slot, param id, name).')
    p('pub const REGEN_EQUIPMENT: &[(crate::EquipKind, Named)] = &[')
    for kind, i, n in t['regen_equipment']:
        p(f'    (crate::EquipKind::{kind}, Named {{ id: {i}, name: {rust_str(n)} }}),')
    p('];')
    p()
    ids('ROT_GREASE', 'Weapon buff rows of the rot greases.', t['grease']['ROT'])
    ids('SLEEP_GREASE', 'Weapon buff rows of the sleep greases.', t['grease']['SLEEP'])
    ids('POISON_GREASE', 'Weapon buff rows of the poison greases.', t['grease']['POISON'])
    ids('GREASE_BUFFS', 'Weapon buff rows of every grease. A grease takes only on a weapon row with '
        '`isEnhance` set.', t['grease_all'])
    ids('CERULEAN_FLASK', 'SpEffect rows a Flask of Cerulean Tears applies.', t['cerulean'])
    ids('FAN_DAGGER_BULLETS', 'Bullets Fan Daggers fire.', t['fan_dagger_bullets'])
    named('RUNE_ARC_EFFECTS', 'SpEffect rows of an active Rune Arc: the item\'s own and every Great '
          'Rune effect it switches on.', t['rune_arc'])
    named('STATUS_SKILLS', 'Skills that apply status (swordArtsTypeNew), for the 2H Stone Club/Club '
          'status ban.', t['status_skills'])
    named('SPIRIT_ASH_TRIGGERS', 'SpEffect rows that summon a spirit (`BuddyParam.triggerSpEffectId`), '
          'named by the spirit ash that applies them.', t['spirit_ash'])
    p('/// Bullets only the banned spells fire: (spell, bullets).')
    p('pub const SPELL_BULLETS: &[(&str, &[i32])] = &[')
    for c, (n, bl) in t['spell_bullets'].items():
        p(f'    ({rust_str(n)}, &[{", ".join(str(b) for b in bl)}]),')
    p('];')
    return w.getvalue()


def frida_param_fields(g):
    """{param: {index, struct, fields: {name: [byte offset, type]}}} for the agent's param reads.

    Offsets come from the paramdef the same way `er-param-read.rows` picks one: the layout whose
    row size equals this regulation's row stride, or nothing."""
    src = open(UPSTREAM_SOLO_PARAMS, encoding='utf-8').read()
    out = {}
    for stem, wanted in FRIDA_PARAM_FIELDS.items():
        m = re.search(rf'\(\s*{stem}\s*,\s*(\w+)\s*,\s*(\d+)\s*\)', src)
        if m is None:
            raise Missing(f'{stem} is not in {UPSTREAM_SOLO_PARAMS}')
        p = g.PR.param_bytes(g.files, stem)
        param_type = g.PR.param_type_of(p)
        stride = g.PR.row_stride(p)
        fields, size = g.PR.layout(g.PR.paramdef(param_type, modern=True))
        if stride is not None and size != stride:
            fields, size = g.PR.layout(g.PR.paramdef(param_type, modern=False))
            if size != stride:
                raise Missing(f'{param_type}: no paramdef layout matches row stride {stride}')
        by_name = {f['name']: f for f in fields}
        cols = {}
        for name in wanted:
            f = by_name.get(name)
            if f is None:
                raise Missing(f'{param_type}.{name}: absent')
            if f['bits']:
                # A one-bit flag in a u8 unit: [byte offset, 'bit', bit index].
                if f['bits'] != 1 or f['bty'] not in ('u8', 'dummy8'):
                    raise Missing(f'{param_type}.{name}: a {f["bits"]}-bit field in {f["bty"]}')
                cols[name] = [f['off'], 'bit', f['shift']]
            else:
                cols[name] = [f['off'], f['type']]
        if m.group(1) != param_type:
            raise Missing(f'{stem}: upstream says {m.group(1)}, the regulation says {param_type}')
        out[stem] = {'index': int(m.group(2)), 'struct': param_type, 'fields': cols}
    return out


def render_js(t, params):
    """The agent's data block: the same tables as tables.rs, in JS."""
    import json
    named = lambda rows: [[i, n] for i, n in rows]
    data = {
        'wepType': {c: v for c, (v, _) in t['wep_type'].items()},
        'weapon': {c: v for c, (v, _) in t['weapon'].items()},
        'affinity': {c: v for c, (v, _) in t['affinity'].items()},
        'talisman': {c: v for c, (v, _) in t['talisman'].items()},
        'skill': {c: v for c, (v, _) in t['skill'].items()},
        'rippleWeapons': named(t['ripple']),
        'bannedArmor': named(t['armor']),
        'parrySkills': named(t['parry']),
        'swordDanceFastClips': list(SWORD_DANCE_FAST_CLIPS),
        'chillingMistBuffs': t['chilling_mist'],
        'bannedTears': [[i, n, why] for i, n, why in t['tear_ban']],
        'bodyBuffs': named(t['body_buff']),
        'heals': named(t['heal']),
        'regenEquipment': [[k, i, n] for k, i, n in t['regen_equipment']],
        'rotGrease': t['grease']['ROT'],
        'sleepGrease': t['grease']['SLEEP'],
        'poisonGrease': t['grease']['POISON'],
        'greaseBuffs': t['grease_all'],
        'ceruleanFlask': t['cerulean'],
        'fanDaggerBullets': t['fan_dagger_bullets'],
        'spiritAshTriggers': named(t['spirit_ash']),
        'statusSkills': named(t['status_skills']),
        'runeArcEffects': named(t['rune_arc']),
        'spellBullets': [[n, bl] for n, bl in t['spell_bullets'].values()],
    }
    return (f'{FRIDA_BEGIN}\n'
            f'const T = {json.dumps(data, sort_keys=True)};\n'
            f'const PARAM_FIELDS = {json.dumps(params, sort_keys=True)};\n'
            f'{FRIDA_END}')


def with_frida_block(agent, block):
    begin, end = agent.find(FRIDA_BEGIN), agent.find(FRIDA_END)
    if begin < 0 or end < begin:
        raise SystemExit(f'{FRIDA_OUT}: generated-block markers missing')
    return agent[:begin] + block + agent[end + len(FRIDA_END):]


def rustfmt(text):
    """The table as `cargo fmt` would leave it, so `--check` and the formatting gate agree."""
    try:
        done = subprocess.run(['rustfmt', '--edition', '2024', '--emit', 'stdout'],
                              input=text, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise SystemExit(f'er-duel-rules-gen: rustfmt unavailable: {e}')
    if done.returncode != 0:
        raise SystemExit(f'er-duel-rules-gen: rustfmt failed: {done.stderr}')
    return done.stdout


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--write', action='store_true')
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--test-agent', action='store_true',
                    help='run the Frida agent\'s rule tests under node (needs no game data)')
    a = ap.parse_args()
    if a.test_agent:
        test = os.path.join(HERE, 'test-er-duel-rules-frida.cjs')
        try:
            return subprocess.run(['node', test], timeout=25).returncode
        except OSError as e:
            print(f'er-duel-rules-gen: node is required to test the Frida agent: {e}', file=sys.stderr)
            return 1
    try:
        g = Game()
    except SystemExit as e:
        if a.check or a.selftest:
            print(f'er-duel-rules-gen: SKIP (no game data: {e})')
            return 0
        raise
    t = build(g)
    text = rustfmt(render(t))
    params = frida_param_fields(g)
    agent_now = open(FRIDA_OUT, encoding='utf-8').read()
    agent = with_frida_block(agent_now, render_js(t, params))
    if a.selftest:
        assert t['wep_type']['THRUSTING_SHIELD'][0] != t['wep_type']['MEDIUM_SHIELD'][0]
        assert t['skill']['BLOODHOUNDS_STEP'][0] == 156, t['skill']['BLOODHOUNDS_STEP']
        assert any(n == 'Parry' for _, n in t['parry'])
        assert t['body_buff'] and t['heal'] and t['tear_ban']
        assert params['EquipParamWeapon']['index'] == 0, params['EquipParamWeapon']
        assert params['SpEffectParam']['fields']['spCategory'][1] == 'u16'
        print('er-duel-rules-gen selftest: PASS')
        return 0
    if a.check:
        stale = [path for path, have, want in ((OUT, open(OUT, encoding='utf-8').read()
                                                if os.path.exists(OUT) else '', text),
                                               (FRIDA_OUT, agent_now, agent)) if have != want]
        for path in stale:
            print(f'er-duel-rules-gen: {path} is stale; rerun with --write', file=sys.stderr)
        if stale:
            return 1
        print('er-duel-rules-gen: up to date')
        return 0
    if a.write:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, 'w', encoding='utf-8') as f:
            f.write(text)
        with open(FRIDA_OUT, 'w', encoding='utf-8') as f:
            f.write(agent)
        print(f'wrote {OUT}\nwrote {FRIDA_OUT}')
        return 0
    sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
