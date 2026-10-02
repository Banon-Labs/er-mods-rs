#!/usr/bin/env python3
"""Every Elden Ring talisman, what its SpEffects change, and when.

Reads the installed regulation through `scripts/er-param-read.py` (Smithbox paramdefs and row
names), the game's own item text through `scripts/er-item-name.py`, and the executable facts
written up in `docs/er-mechanics/talismans.md`. Nothing here launches the game.

Labels, as in the rest of `docs/er-mechanics/`: `VERIFIED` = a regulation value, or read out of the
1.16.2 executable and carried to 1.17.1; `INFERRED` = fits the data and names but the consumer was
not traced; `COMMUNITY` = outside claim; `GAME TEXT` = the item's own `AccessoryInfo` line.

What a talisman is (`VERIFIED`, `EquipParamAccessory`): a row with `refCategory 2` whose `refId`
is a `SpEffectParam` row, plus `residentSpEffectId1..4`. The closure followed from there is every
SpEffect reachable through `cycleOccurrenceSpEffectId`, `replaceSpEffectId`,
`atkOccurrenceSpEffectId`, `accumuOverFireId`, `accumuUnderFireId`, `spiritDeathSpEffectId` and
`applyIdOnGetSoul`, plus the rows the regulation names after the talisman (`[Talisman] <name>...`)
that nothing links to: those are started by a state check in code or script (see `trigger`).

    python3 scripts/er-mechanics-talismans.py                      # one line per talisman
    python3 scripts/er-mechanics-talismans.py --affects attack     # filter by system
    python3 scripts/er-mechanics-talismans.py --show "Claw Talisman" --json
    python3 scripts/er-mechanics-talismans.py --slot jump_r2 --talismans "Claw Talisman,Two-Handed Sword Talisman" --grip 2h
    python3 scripts/er-mechanics-talismans.py --corpus              # STR PvP RL 140-160 usage
    python3 scripts/er-mechanics-talismans.py --selftest

Library interface (the PvP model calls these):

    m = load_module()                     # importlib; the filename is hyphenated
    t = m.Talismans()                     # one regulation read, cached
    t.get("Claw Talisman")                # -> Talisman: .effects, .speffects, .text, .weight
    m.multipliers(t, ["Claw Talisman", "Shard of Alexander"], ctx) -> dict
    m.defender_modifiers(t, ["Bull-Goat's Talisman"], incoming) -> dict

`ctx` (attack context; every key optional):
    slot          attack slot key from er-mechanics-attacks.py: r1_1..r1_6, r2_1, r2_1c, r2_2,
                  r2_2c, run_r1, run_r2, roll_r1, bstep_r1, crouch_r1, jump_r1, jump_r2, counter;
                  a `2h_` prefix is the two-handed slot (same as grip='2h')
    grip          '1h' | '2h'
    weapon_id     EquipParamWeapon row: the slot's subcategories are then read from that
                  weapon's own AtkParam row instead of the majority table
    hand          'right' (default) | 'left'
    subcategories iterable of AtkParam.subCategory values; overrides the slot default table.
                  Pass the real `AtkParam_Pc.subCategory1..4` of the hit when you have it
                  (`attack_subcategories(t, atk_row)` reads them).
    phys_type     'slash' | 'strike' | 'pierce' | 'standard'
    pvp           True (default): the defender is a PvP character, so atkPlayerDmgCorrectRate
                  applies; False: atkEnemyDmgCorrectRate
    critical      the hit is a critical (AtkParam throwFlag 2: backstab, riposte); gates
                  Dagger Talisman's throwAttackParamChange
    counter_hit   the defender has a counter-frame SpEffect (stateInfo 110) active
    counter_elements  elements whose counter rate is above 1; default ('physical',) for a
                  pierce hit, else none (the counter rows 31 / 45 only raise thrust)
    hp_ratio      attacker HP / max HP (Ritual Sword needs exactly 1.0, Red-Feathered <= 0.19999999)
    equip_weight  attacker's absolute equipped weight (Blue Dancer Charm; not a load ratio)
    successive_stage  0..4, the Winged Sword / Rotten Winged / Millicent's stage reached
    active        set of timed buffs that are on for this hit (keys of `TIMED`): 'lord_of_blood',
                  'kindred_of_rot', 'aged_one', 'st_trina', 'blade_of_mercy', 'rellana_stance',
                  'rellana', 'dried_bouquet', 'crusade', 'godfrey_window', 'sharpshot'.
                  Nothing timed is on by default.

multipliers() returns (each a product over applicable SpEffects, `VERIFIED` placement):
    {'power_rate': {element: x},     # *AttackPowerRate: weapon AR before defense (Blue Dancer)
     'damage_rate': {element: x},    # *AttackRate: after the defense curve, truncated to a
                                     # whole percent (Axe, Shard of Alexander, Dagger, Godfrey)
     'damage_correct': {element: x}, # atkPlayer/atkEnemyDmgCorrectRate: after the defense curve
                                     # (Claw, Two-Handed Sword, Ritual Sword, Rellana ...)
     'counter_rate': {element: x},   # Spear Talisman on the counter-hit rate of counter elements
     'stamina_damage': x,            # staminaAttackRate on the hit's stamina damage (Hammer)
     'applied': [(talisman, speffect id, field, value, what)],
     'skipped': [(talisman, speffect id, reason)]}
Per element e of the weapon part of a hit:
    damage[e] = curve(AR[e] * power_rate[e] * MV, DEF[e]) * absorption ... * damage_rate[e]
                * damage_correct[e] * (counter-hit rate[e] * counter_rate[e] on a counter)
Flat SpEffect adds (greases) are not multiplied by any of these.

`incoming` (defender context; every key optional): pvp (True: the attacker is a player, so
defPlayerDmgCorrectRate), guarded, hp_ratio, being_critted (receiving a critical),
equip_load_ratio (Verdigris Discus), active (set: 'lords_bestowal', 'lords_bestowal_mounted').
defender_modifiers() returns
    {'damage_taken': {element: x},     # defPlayer/defEnemyDmgCorrectRate product
     'absorption_mult': {type: x},     # SpEffect *DamageCutRate product (below 1 = less damage)
     'poise_mult': x,                  # 1 / product(toughnessDamageCutRate)
     'poise_damage_taken': x,          # saReceiveDamageRate product
     'guard_stamina_mult': x,          # guardStaminaMult on a guarded hit
     'headshot_bonus_removed': bool,   # Crucible Knot
     'applied': [...], 'skipped': [...]}
"""
import argparse, collections, importlib.util, json, os, re, struct, sys, xml.etree.ElementTree as ET

_HERE = os.path.dirname(os.path.abspath(__file__))


def _mod(name, fname):
    s = importlib.util.spec_from_file_location(name, os.path.join(_HERE, fname))
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


def load_module():
    return sys.modules[__name__]


PR = _mod('er_param_read', 'er-param-read.py')
IN = _mod('er_item_name', 'er-item-name.py')
SMITHBOX = os.path.dirname(PR.PARAMDEF_DIR)
CACHE = os.path.expanduser('~/.cache/er-build-planner')

ELEMENTS = ('physical', 'magic', 'fire', 'lightning', 'holy')
# SpEffect column suffix per element: the game's "dark" is holy, "thunder" is lightning.
EL_SUFFIX = {'physical': 'Physics', 'magic': 'Magic', 'fire': 'Fire', 'lightning': 'Thunder',
             'holy': 'Dark'}
ATTACK_RATE = {'physical': 'physicsAttackRate', 'magic': 'magicAttackRate', 'fire': 'fireAttackRate',
               'lightning': 'thunderAttackRate', 'holy': 'darkAttackRate'}
POWER_RATE = ('physicsAttackPowerRate', 'slashAttackPowerRate', 'blowAttackPowerRate',
              'thrustAttackPowerRate', 'neutralAttackPowerRate')
PHYS_POWER_RATE = {'slash': 'slashAttackPowerRate', 'strike': 'blowAttackPowerRate',
                   'pierce': 'thrustAttackPowerRate', 'standard': 'neutralAttackPowerRate'}
CUT_RATE = {'standard': 'neutralDamageCutRate', 'strike': 'blowDamageCutRate',
            'slash': 'slashDamageCutRate', 'pierce': 'thrustDamageCutRate',
            'magic': 'magicDamageCutRate', 'fire': 'fireDamageCutRate',
            'lightning': 'thunderDamageCutRate', 'holy': 'darkDamageCutRate'}

LINK_FIELDS = ('replaceSpEffectId', 'cycleOccurrenceSpEffectId', 'atkOccurrenceSpEffectId',
               'accumuOverFireId', 'accumuUnderFireId', 'spiritDeathSpEffectId', 'applyIdOnGetSoul')
# Fields every talisman row sets that change nothing a build cares about: who the effect may
# target, the buff icon, and which spell families the row's param-change flags cover.
QUIET = {'effectTargetSelf', 'effectTargetFriend', 'effectTargetPlayer', 'effectTargetAI',
         'effectTargetLive', 'effectTargetGhost', 'effectTargetOpposeTarget',
         'effectTargetFriendlyTarget', 'bCurrHPIndependeMaxHP', 'iconId', 'magParamChange',
         'miracleParamChange', 'shamanParamChange', 'isDisableNetSync', 'stateInfo_name'}

# `AtkParam.subCategory1..4` / `SpEffectParam.magicSubCategoryChange1..3` values talismans use.
# Names are Smithbox's ATK_SUB_CATEGORY enum (`COMMUNITY` naming, values `VERIFIED`).
SUBCAT = {100: 'charged heavy attack', 101: 'horseback attack', 102: 'jump attack',
          103: 'guard counter', 104: 'final chain attack', 105: 'ammunition attack',
          106: 'roar attack', 107: 'breath attack', 108: 'thrown pot', 109: 'perfume',
          110: 'charged spell', 111: 'charged skill', 112: 'skill', 113: 'ranged skill',
          116: 'Shriek of Milos', 118: 'ammunition on-hit', 119: 'thrown item',
          120: 'two-handed attack', 121: 'backstep / rolling attack', 122: 'dash attack',
          123: 'magma attack', 124: 'storm attack', 127: 'stomp / kick', 129: 'dance attack',
          131: 'hefty thrown pot'}

# Which AtkParam subcategories each moveset slot carries (`VERIFIED`: resolved through
# er-mechanics-attacks.py's behavior chain for every named base weapon, 458 weapons, and
# `AtkParam_Pc.subCategory1..4` read for each; `--slot-survey` re-runs it). The value here is
# the majority set; `attack_subcategories()` gives a weapon's exact row. Notes from the survey:
#   - 104 (final chain) sits on the last R1 of a chain: r1_3 on 105 weapons, r1_4 on 178 ...
#   - 120 (two-handed) is on 2H attacks of 403 of 434 weapons; the rest (paired and a few
#     others) have 2H rows without it.
#   - crouch_r1 fires the rolling R1 judge (attacks.md), so it carries 121.
SLOT_SUBCATS = {
    'r1_1': (), 'r1_2': (), 'r1_3': (), 'r1_4': (104,), 'r1_5': (104,), 'r1_6': (104,),
    'r2_1': (), 'r2_1c': (100,), 'r2_2': (), 'r2_2c': (100,),
    'run_r1': (122,), 'run_r2': (122,), 'roll_r1': (121,), 'bstep_r1': (121,),
    'crouch_r1': (121,), 'jump_r1': (102,), 'jump_r2': (102,), 'counter': (103,),
}


def _meta(stem):
    root = ET.parse(os.path.join(SMITHBOX, 'Param Meta', stem + '.xml')).getroot().find('Field')
    defaults, refs = {}, []
    for c in root:
        if c.get('DefaultValue') is not None:
            defaults[c.tag] = float(c.get('DefaultValue'))
        if c.get('Refs') and 'SpEffectParam' in c.get('Refs').split(','):
            refs.append(c.tag)
    return defaults, refs


def _enum(name):
    p = os.path.join(SMITHBOX, 'Param Enums', name + '.json')
    if not os.path.exists(p):
        return {}
    out = {}
    for o in json.load(open(p, encoding='utf-8-sig')).get('Options', []):
        names = o.get('Names') or []
        out[int(o.get('Key'))] = next((n.get('Text') for n in names if n.get('Language') == 'English'), None)
    return out


def _fmg(stem):
    out = {}
    for _, table in IN.load(IN.DEFAULT_CORPUS, stem):
        for k, v in table.items():
            if v and k not in out:
                out[k] = v
    return out


def _norm(name):
    return re.sub(r'\s+', ' ', name or '').strip().lower()


# ------------------------------------------------------------------------------------------
# Effect classification: which game system consumes each SpEffect field.

def _systems(fields):
    """Group a SpEffect row's non-default fields into (system, summary) pairs."""
    f = fields
    out = []

    def same(keys):
        vals = [f[k] for k in keys if k in f]
        return vals and len(vals) == len(keys) and len({round(v, 4) for v in vals}) == 1

    def el_block(prefix, label):
        keys = [prefix + EL_SUFFIX[e] for e in ELEMENTS]
        present = [k for k in keys if k in f]
        if not present:
            return
        if same(keys):
            out.append((label, f'all elements x{f[keys[0]]:.4g}'))
        else:
            out.append((label, ', '.join(f'{e} x{f[prefix + EL_SUFFIX[e]]:.4g}' for e in ELEMENTS
                                         if prefix + EL_SUFFIX[e] in f)))

    ar = [ATTACK_RATE[e] for e in ELEMENTS]
    if any(k in f for k in ar):
        out.append(('attack power', ('AR x%.4g' % f[ar[0]] if same(ar) else 'AR ' +
                    ', '.join(f'{e} x{f[ATTACK_RATE[e]]:.4g}' for e in ELEMENTS if ATTACK_RATE[e] in f))
                    + ' (*AttackRate)'))
    if any(k in f for k in POWER_RATE):
        out.append(('attack power', 'physical AR x%.4g (*AttackPowerRate)' % f.get(POWER_RATE[0], 1)))
    el_block('atkPlayerDmgCorrectRate_', 'damage vs players')
    el_block('atkEnemyDmgCorrectRate_', 'damage vs enemies')
    el_block('defPlayerDmgCorrectRate_', 'damage taken from players')
    el_block('defEnemyDmgCorrectRate_', 'damage taken from enemies')
    cut = [CUT_RATE[k] for k in CUT_RATE]
    if any(k in f for k in cut):
        if same(cut):
            out.append(('absorption', f'all damage taken x{f[cut[0]]:.4g} (*DamageCutRate)'))
        elif all(k in f for k in cut[4:]) and not any(k in f for k in cut[:4]):
            out.append(('absorption', f'non-physical damage taken x{f[cut[4]]:.4g} (*DamageCutRate)'))
        else:
            out.append(('absorption', ', '.join(f'{k} {f[k]:.4g}' for k in cut if k in f)))
    simple = {
        'toughnessDamageCutRate': ('poise', lambda v: f'poise x{1 / v:.4g} (damage to poise x{v:.4g})'),
        'staminaAttackRate': ('stamina damage', lambda v: f'stamina damage to guarding target x{v:.4g}'),
        'guardStaminaMult': ('guard', lambda v: f'stamina lost when blocking x{v:.4g}'),
        'maxHpRate': ('HP', lambda v: f'max HP x{v:.4g}'),
        'maxMpRate': ('FP', lambda v: f'max FP x{v:.4g}'),
        'maxStaminaRate': ('stamina', lambda v: f'max stamina x{v:.4g}'),
        'equipWeightChangeRate': ('equip load', lambda v: f'max equip load x{v:.4g}'),
        'staminaRecoverChangeSpeed': ('stamina', lambda v: f'stamina recovery +{v:g}/s'),
        'changeHpEstusFlaskCorrectRate': ('HP', lambda v: f'Crimson flask healing x{v:.4g}'),
        'changeMpEstusFlaskCorrectRate': ('FP', lambda v: f'Cerulean flask FP x{v:.4g}'),
        'extendLifeRate': ('spells', lambda v: f'buff duration x{v:.4g}'),
        'dexterityCancelSystemOnlyAddDexterity': ('spells', lambda v: f'casting speed as +{v:g} DEX'),
        'artsConsumptionRate': ('FP', lambda v: f'skill FP cost x{v:.4g}'),
        'magicConsumptionRate': ('FP', lambda v: f'sorcery FP cost x{v:.4g}'),
        'miracleConsumptionRate': ('FP', lambda v: f'incantation FP cost x{v:.4g}'),
        'changeMagicSlot': ('spells', lambda v: f'memory slots +{v:g}'),
        'fallDamageRate': ('misc', lambda v: f'fall damage x{v:.4g}'),
        'targetPriority': ('misc', lambda v: f'enemy target priority {v:.4g}'),
        'hearingSearchEnemyRate': ('misc', lambda v: f'noise heard by enemies x{v:.4g}'),
        'soulRate': ('misc', lambda v: f'runes x{v:.4g}'),
        'itemDropRate': ('misc', lambda v: f'item discovery field {v:.4g}'),
        'bowDistRate': ('ranged', lambda v: f'bow range +{v:g}%'),
        'saReceiveDamageRate': ('poise', lambda v: f'poise damage taken x{v:.4g}'),
    }
    for k, (sysname, fmt) in simple.items():
        if k in f:
            out.append((sysname, fmt(f[k])))
    stats = [('addLifeForceStatus', 'VIG'), ('addWillpowerStatus', 'MND'), ('addEndureStatus', 'END'),
             ('addStrengthStatus', 'STR'), ('addDexterityStatus', 'DEX'), ('addMagicStatus', 'INT'),
             ('addFaithStatus', 'FTH'), ('addLuckStatus', 'ARC')]
    s = [f'{lab} +{f[k]}' for k, lab in stats if k in f]
    if s:
        out.append(('attributes', ', '.join(s)))
    res = [('changePoisonResistPoint', 'poison'), ('changeDiseaseResistPoint', 'rot'),
           ('changeBloodResistPoint', 'bleed'), ('changeFreezeResistPoint', 'frost'),
           ('changeSleepResistPoint', 'sleep'), ('changeMadnessResistPoint', 'madness'),
           ('changeCurseResistPoint', 'death')]
    s = [f'{lab} +{f[k]}' for k, lab in res if k in f]
    if s:
        out.append(('resistance', ', '.join(s)))
    st = [('poizonAttackPower', 'poison'), ('diseaseAttackPower', 'rot'), ('bloodAttackPower', 'bleed'),
          ('freezeAttackPower', 'frost'), ('sleepAttackPower', 'sleep'), ('madnessAttackPower', 'madness')]
    s = [f'{lab} {f[k]}' for k, lab in st if k in f]
    if s:
        out.append(('status buildup', 'build-up on the wearer ' + ', '.join(s)))
    end = f.get('effectEndurance', -1)
    per = f.get('motionInterval', 0)
    # A row that lasts no longer than its interval fires once (ActivateInterval, talismans.md 4).
    per = per if per and (end < 0 or per < end) else 0
    once = '' if per or end < 0 else ' once'
    if 'changeHpPoint' in f or 'changeHpRate' in f:
        out.append(('HP', 'HP %+g%s%s' % (-f.get('changeHpPoint', 0),
                    ' %+g%% max' % -f['changeHpRate'] if 'changeHpRate' in f else '',
                    f' every {per:g}s' if per else once)))
    if 'changeMpPoint' in f:
        out.append(('FP', 'FP %+g%s' % (-f['changeMpPoint'], f' every {per:g}s' if per else once)))
    return out


# ------------------------------------------------------------------------------------------
# Trigger / condition of each SpEffect row, as the game decides it. Keys are the row's own
# stateInfo, condition fields, or the talisman. `evidence` follows the module labels; the
# addresses are in talismans.md.

STATEINFO_CONDITION = {
    48: ('label only; the HP field is the gate', 'no stateInfo 48 consumer found'),
    49: ('label only; the HP field is the gate', 'no stateInfo 49 consumer found'),
    50: ('label (FP regeneration)', 'INFERRED'),
    54: ('label (hearing)', 'INFERRED'),
    66: ('item discovery: adds itemDropRate to the ARC curve', 'VERIFIED'),
    71: ('spells only (wepParamChange 3 fails weapon contexts)', 'hand gate VERIFIED'),
    75: ('label only (regen is not gated on it)', 'VERIFIED'),
    76: ('label only (rune gain not gated on it)', 'VERIFIED'),
    158: ('only on a guarded hit', 'VERIFIED'),
    159: ('breaks on death, keeps runes', 'INFERRED'),
    168: ('bow range: added to the weapon bowDistRate', 'VERIFIED'),
    193: ('extends buffs that carry isExtendSpEffectLife (51 rows)', 'VERIFIED'),
    197: ('counter-hits: multiplies the counter-hit rate', 'VERIFIED'),
    199: ('on rune award (kill): applies applyIdOnGetSoul', 'VERIFIED'),
    288: ('critical rows apply 350501 -> 350502 heal', 'VERIFIED params'),
    289: ('critical rows apply 350601 -> 350602 FP', 'VERIFIED params'),
    290: ('roll i-frames +3 (TAE, pre-1.17 extraction); cut-rate penalty always on', 'VERIFIED'),
    303: ('accumulator 1 (successive hits)', 'VERIFIED'),
    304: ('accumulator 2 (successive hits)', 'VERIFIED'),
    305: ('accumulator 3 (successive hits)', 'VERIFIED'),
    315: ('scaled by equipped weight, graph 50', 'VERIFIED'),
    316: ('scaled by equipped weight, graph 51', 'VERIFIED'),
    335: ('only while receiving a critical', 'VERIFIED'),
    367: ('label; the throw gate decides (criticals only)', 'VERIFIED throw gate, no 367 compare found'),
    450: ('head hit keeps part multiplier 1.0', 'VERIFIED'),
    473: ('backstep i-frames (TAE); cut-rate penalty always on', 'VERIFIED'),
    475: ('spirit death gives spiritDeathSpEffectId', 'VERIFIED'),
    483: ('critical rows apply 20382301 -> 20382302', 'VERIFIED params'),
    496: ('flask animations apply 20382003 (and 20382004 mounted)', 'VERIFIED TAE'),
    497: ('a525 anims 45010/45110 apply 330901', 'VERIFIED TAE'),
    503: ('stance TAE applies 20382205 / 20382201 chain', 'VERIFIED TAE'),
}


def trigger_evidence(s):
    """Evidence label for when row `s` is active (its values are always `VERIFIED` regulation)."""
    f = s.fields
    parts = []
    if s.id in VERDIGRIS:
        parts.append('trigger not found; band from row name INFERRED')
    elif s.id == 19991:
        parts.append('trigger not found')
    elif s.via == 'row name':
        parts.append('started by AtkParam spEffectId / TAE: VERIFIED')
    elif s.via == 'applyIdOnGetSoul':
        parts.append('on rune award: VERIFIED')
    elif s.via == 'spiritDeathSpEffectId':
        parts.append('on spirit death: VERIFIED')
    elif s.via in ('cycleOccurrenceSpEffectId', 'replaceSpEffectId', 'accumuOverFireId') and (
            s.id in TIMED or s.id in STAGE_OF or f.get('effectEndurance', -1) > 1):
        parts.append('event chain from the talisman row: VERIFIED')
    if s.subcats():
        parts.append(CONDITION_EVIDENCE['subcategory'])
    if f.get('conditionHpRate', -1) >= 0 or f.get('conditionHp', -1) >= 0:
        parts.append(CONDITION_EVIDENCE['hp'])
    si = f.get('stateInfo', 0)
    if si in STATEINFO_CONDITION:
        parts.append(STATEINFO_CONDITION[si][1])
    if any(f.get(f'invocationConditionsStateChange{i}', 0) for i in (1, 2, 3)):
        parts.append(CONDITION_EVIDENCE['invocation'])
    if f.get('throwAttackParamChange'):
        parts.append(CONDITION_EVIDENCE['throw'])
    return '; '.join(dict.fromkeys(parts)) or 'always on: VERIFIED'


CONDITION_EVIDENCE = {
    'subcategory': 'subcategory match VERIFIED',
    'hp': 'HP gate VERIFIED',
    'invocation': 'presence gate VERIFIED',
    'throw': 'throw gate VERIFIED',
}


class SpEffect:
    __slots__ = ('id', 'name', 'fields', 'links', 'via')

    def __init__(self, sid, name, fields, links, via):
        self.id, self.name, self.fields, self.links, self.via = sid, name, fields, links, via

    def get(self, k, d=None):
        return self.fields.get(k, d)

    def systems(self):
        return _systems(self.fields)

    def subcats(self):
        return tuple(v for v in (self.get('magicSubCategoryChange1', 0), self.get('magicSubCategoryChange2', 0),
                                 self.get('magicSubCategoryChange3', 0)) if v)

    def condition(self):
        f = self.fields
        c = []
        if self.id in TIMED:
            c.append(f"timed buff (ctx active '{TIMED[self.id]}')")
        elif self.via == 'applyIdOnGetSoul':
            c.append('on rune award (a kill)')
        elif self.via == 'spiritDeathSpEffectId':
            c.append('when the summoned spirit dies')
        if self.id in STAGE_OF:
            c.append(f'successive-hit stage {STAGE_OF[self.id]} (threshold {STAGE_THRESHOLDS[STAGE_OF[self.id] - 1]})')
        if self.id in VERDIGRIS:
            c.append('equip load ratio in (%g, %g] (INFERRED band)' % VERDIGRIS[self.id])
        if f.get('conditionHpRate', -1) >= 0:
            c.append(f"HP >= {f['conditionHpRate']:g}% of max")
        if f.get('conditionHp', -1) >= 0:
            c.append(f"HP <= {f['conditionHp']:g}% of max")
        sc = self.subcats()
        if sc:
            c.append('attack subcategory in {' + ', '.join(f'{v} {SUBCAT.get(v, "?")}' for v in sc) + '}')
        si = f.get('stateInfo', 0)
        if si and si in STATEINFO_CONDITION:
            c.append(STATEINFO_CONDITION[si][0])
        elif si:
            c.append(f'stateInfo {si}')
        for i in (1, 2, 3):
            v = f.get(f'invocationConditionsStateChange{i}', 0)
            if v:
                c.append(f'owner has an effect with stateInfo {v}')
        if f.get('throwAttackParamChange'):
            c.append('throw (critical) attacks only')
        w = f.get('wepParamChange', 0)
        if w:
            c.append({1: 'right hand', 2: 'left hand', 3: 'spells / self', 4: 'kick'}.get(w, f'wepParamChange {w}'))
        if f.get('effectEndurance', -1) > 0:
            c.append(f"lasts {f['effectEndurance']:g}s")
        return c

    def to_json(self):
        return {'id': self.id, 'name': self.name, 'via': self.via, 'links': self.links,
                'condition': self.condition(), 'systems': self.systems(),
                'fields': {k: (round(v, 6) if isinstance(v, float) else v) for k, v in self.fields.items()
                           if k not in QUIET}}


class Talisman:
    def __init__(self, row, name, text, speffects):
        self.id = row['id']
        self.name = name
        self.text = text
        self.weight = round(row['weight'], 4)
        self.group = row['accessoryGroup']
        self.speffects = speffects
        self.resident = [s for s in speffects if s.via in ('refId', 'resident')]

    def systems(self):
        out = []
        for s in self.speffects:
            for sysname, what in s.systems():
                out.append((sysname, what, s))
        return out

    def affects(self):
        return sorted({x[0] for x in self.systems()})

    def to_json(self):
        return {'id': self.id, 'name': self.name, 'text': self.text, 'weight': self.weight,
                'group': self.group, 'affects': self.affects(),
                'speffects': [s.to_json() for s in self.speffects]}


class Talismans:
    """Every equippable talisman, with its SpEffect closure. One regulation read."""

    def __init__(self, regulation=None):
        self.defaults, refs = _meta('SpEffect')
        self.links = [f for f in dict.fromkeys(refs + list(LINK_FIELDS))]
        self.stateinfo_names = _enum('SP_EFFECT_TYPE')
        files = PR.load(regulation)
        self._files = files
        acc, _, _ = PR.rows(PR.param_bytes(files, 'EquipParamAccessory'))
        self.sp = {r['id']: r for r in PR.rows(PR.param_bytes(files, 'SpEffectParam'))[0]}
        self.sp_names = PR.row_names('SpEffectParam')
        names = _fmg('AccessoryName')
        info = _fmg('AccessoryInfo')
        self.all_rows = acc
        self.skipped = []
        self.by_id, self.by_name = {}, {}
        named_prefix = collections.defaultdict(list)
        for sid, nm in self.sp_names.items():
            m = re.match(r'\[Talisman\] (.+?)(?:\s*[(:-].*)?$', nm or '')
            if m:
                named_prefix[_norm(m.group(1))].append(sid)
        for a in acc:
            nm = names.get(a['id'])
            if not nm or nm.startswith('[ERROR]') or a['refCategory'] != 2:
                self.skipped.append((a['id'], nm, 'no AccessoryName text' if not nm else
                                     'text marked [ERROR] (cut item)' if nm.startswith('[ERROR]')
                                     else f"refCategory {a['refCategory']}"))
                continue
            t = Talisman(a, nm, info.get(a['id']), self._closure(a, named_prefix.get(_norm(nm), [])))
            self.by_id[a['id']] = t
            self.by_name[_norm(nm)] = t

    def _nondefault(self, r):
        return {k: v for k, v in r.items() if k not in ('id', '_off')
                and abs(float(v) - self.defaults.get(k, 0)) > 1e-6}

    def _closure(self, a, related):
        roots = ([(a['refId'], 'refId')] if a['refId'] > 0 else []) + \
            [(a[f'residentSpEffectId{i}'], 'resident') for i in range(1, 5) if a[f'residentSpEffectId{i}'] > 0]
        out, seen, q = [], set(), list(roots)
        while q:
            sid, via = q.pop(0)
            if sid in seen or sid not in self.sp:
                continue
            seen.add(sid)
            r = self.sp[sid]
            links = {f: r[f] for f in self.links if r.get(f, -1) > 0}
            out.append(SpEffect(sid, self.sp_names.get(sid), self._nondefault(r), links, via))
            q.extend((v, f) for f, v in links.items())
            # Rows waiting on a stateInfo that only this row sets (Godfrey Icon's 330901 waits on
            # 497). A stateInfo other effects also set (accumulators, guarding) links nothing.
            si = r.get('stateInfo', 0)
            if si and self._stateinfo_users(si) == [sid]:
                for other in self._by_invocation(si):
                    q.append((other, f'invocation {si}'))
        for sid in related:
            if sid not in seen and sid in self.sp:
                q = [(sid, 'row name')]
                while q:
                    x, via = q.pop(0)
                    if x in seen or x not in self.sp:
                        continue
                    seen.add(x)
                    r = self.sp[x]
                    links = {f: r[f] for f in self.links if r.get(f, -1) > 0}
                    out.append(SpEffect(x, self.sp_names.get(x), self._nondefault(r), links, via))
                    q.extend((v, f) for f, v in links.items())
        return out

    _inv_index = None
    _si_index = None

    def _stateinfo_users(self, si):
        if self._si_index is None:
            self._si_index = collections.defaultdict(list)
            for r in self.sp.values():
                if r.get('stateInfo', 0):
                    self._si_index[r['stateInfo']].append(r['id'])
        return self._si_index.get(si, [])

    def _by_invocation(self, si):
        if self._inv_index is None:
            self._inv_index = collections.defaultdict(list)
            for r in self.sp.values():
                for i in (1, 2, 3):
                    v = r.get(f'invocationConditionsStateChange{i}', 0)
                    if v:
                        self._inv_index[v].append(r['id'])
        return self._inv_index.get(si, [])

    def get(self, key):
        if isinstance(key, Talisman):
            return key
        if isinstance(key, int) or str(key).isdigit():
            return self.by_id.get(int(key))
        return self.by_name.get(_norm(key))

    def __iter__(self):
        return iter(sorted(self.by_id.values(), key=lambda t: t.id))

    _graphs = None

    def graph(self, gid):
        """CalcCorrectGraph row `gid` as a function (the usual 5-stage curve with adjPt exponents)."""
        if self._graphs is None:
            rows, _, _ = PR.rows(PR.param_bytes(self._files, 'CalcCorrectGraph'))
            self._graphs = {r['id']: r for r in rows}
        r = self._graphs[gid]
        xs = [r[f'stageMaxVal{i}'] for i in range(5)]
        ys = [r[f'stageMaxGrowVal{i}'] for i in range(5)]
        adj = [r[f'adjPt_maxGrowVal{i}'] for i in range(5)]

        def g(x):
            if x <= xs[0]:
                return ys[0]
            for i in range(4):
                if x <= xs[i + 1]:
                    q = (x - xs[i]) / (xs[i + 1] - xs[i])
                    e = adj[i]
                    q = q ** e if e > 0 else 1 - (1 - q) ** -e
                    return ys[i] + (ys[i + 1] - ys[i]) * q
            return ys[4]
        return g

    _atk = None

    def atk(self):
        if self._atk is None:
            rows, _, _ = PR.rows(PR.param_bytes(self._files, 'AtkParam_Pc'),
                                 ['subCategory1', 'subCategory2', 'subCategory3', 'subCategory4'])
            self._atk = {r['id']: r for r in rows}
        return self._atk


def attack_subcategories(t, atk_row):
    """`AtkParam_Pc.subCategory1..4` of one attack row, zeros dropped (`VERIFIED` field)."""
    r = t.atk().get(atk_row)
    if r is None:
        return ()
    return tuple(v for v in (r['subCategory1'], r['subCategory2'], r['subCategory3'], r['subCategory4']) if v)


# ------------------------------------------------------------------------------------------
# The PvP model's interface.

_ATT = {}


def weapon_slot_subcategories(t, weapon_id, slot, grip='1h'):
    """Exact subcategories of one weapon's slot, through er-mechanics-attacks.py's behavior chain."""
    if 'mod' not in _ATT:
        _ATT['mod'] = _mod('er_mechanics_attacks', 'er-mechanics-attacks.py')
        _ATT['reg'] = _ATT['mod'].Regulation()
    att, reg = _ATT['mod'], _ATT['reg']
    two = slot.startswith('2h_') or grip == '2h'
    base = slot[3:] if slot.startswith('2h_') else slot
    judge = next((j for k, _, j, _, _ in att.SLOTS_ONE_HAND if k == base), None)
    if judge is None:
        return None
    n = att.attack_numbers(reg, weapon_id, judge + (att.TWO_HAND_JUDGE_OFFSET if two else 0))
    return None if n is None else attack_subcategories(t, n['atk_row'])


def _ctx_subcats(ctx, t=None):
    if ctx.get('subcategories') is not None:
        return set(ctx['subcategories'])
    slot = ctx.get('slot') or ''
    if t is not None and ctx.get('weapon_id') and slot:
        exact = weapon_slot_subcategories(t, ctx['weapon_id'], slot, ctx.get('grip', '1h'))
        if exact is not None:
            return set(exact)
    two = slot.startswith('2h_') or ctx.get('grip') == '2h'
    base = slot[3:] if slot.startswith('2h_') else slot
    sc = set(SLOT_SUBCATS.get(base, ()))
    if two:
        sc.add(120)
    return sc


def f32(x):
    return struct.unpack('<f', struct.pack('<f', x))[0]


# Timed buffs: SpEffect row -> the name `ctx['active']` / `incoming['active']` uses for it. Each
# is started by an event, not by wearing the talisman (talismans.md section 3 has the chains).
TIMED = {
    321601: 'lord_of_blood',      # blood-loss presence (stateInfo 379) nearby, 20 s
    321701: 'kindred_of_rot',     # poison / rot presence (380), 20 s
    20380601: 'aged_one',         # madness presence (495), 30 s
    20381601: 'st_trina',         # sleep presence (480), 30 s
    20382302: 'blade_of_mercy',   # after landing a critical, 20 s (about 21 s effective)
    20382203: 'rellana_stance',   # while holding the stance, 0.2 s pulses
    20382204: 'rellana',          # the stance attack's 10 s buff
    20381001: 'dried_bouquet',    # after the summoned spirit dies, 30 s
    20380501: 'crusade',          # after a kill (rune award), 20 s
    330901: 'godfrey_window',     # a525 anims 45010/45110 only, subcategory 38 (unresolved)
    19991: 'sharpshot',           # 0.1 s pulses, trigger not found (precision aim per the item text)
    20382003: 'lords_bestowal',   # while drinking a flask
    20382004: 'lords_bestowal_mounted',
    20382016: 'lords_bestowal_torrent',
}
# Successive-hit boost rows -> stage (Winged Sword Insignia, Rotten Winged, Millicent's).
STAGE_OF = {}
for _base in (320804, 320814, 312505):
    for _i in range(4):
        STAGE_OF[_base + _i] = _i + 1
# Hits of the most common increment (+8, AtkParam spEffect 6903) to reach each stage's threshold
# 17 / 30 / 45 / 60, before the -1 per 0.5 s decay (`VERIFIED` params).
STAGE_THRESHOLDS = (17, 30, 45, 60)


def _hp_gate(f, hp):
    """`VERIFIED` (FUN_1405012a0): hpRate <= conditionHp*0.01f, hpRate >= conditionHpRate*0.01f."""
    hp = f32(hp)
    if f.get('conditionHpRate', -1) >= 0 and not hp >= f32(f['conditionHpRate'] * f32(0.01)):
        return f"needs HP >= {f['conditionHpRate']:g}% (exactly full for 100)"
    if f.get('conditionHp', -1) >= 0 and not hp <= f32(f32(f['conditionHp']) * f32(0.01)):
        return f"needs HP <= {f['conditionHp']:g}% (0.19999999 for 20)"
    return None


def _timed_gate(s, active):
    name = TIMED.get(s.id)
    if name is not None and name not in active:
        return f'timed buff {name!r} not in active'
    return None


def _attack_gate(t, s, ctx):
    """None when SpEffect row `s` applies to the attack in `ctx`, else the reason it does not."""
    f = s.fields
    si = f.get('stateInfo', 0)
    active = set(ctx.get('active') or ())
    why = _hp_gate(f, ctx.get('hp_ratio', 1.0)) or _timed_gate(s, active)
    if why:
        return why
    if s.id in STAGE_OF and ctx.get('successive_stage', 0) != STAGE_OF[s.id]:
        return f'successive-hit stage {STAGE_OF[s.id]} not the ctx successive_stage'
    sc = s.subcats()
    if sc and not set(sc) & _ctx_subcats(ctx, t):
        return 'attack subcategory ' + '/'.join(map(str, sc)) + ' not on this attack'
    if f.get('throwAttackParamChange') and not ctx.get('critical'):
        return 'critical (throw) attacks only'
    w = f.get('wepParamChange', 0)
    hand = ctx.get('hand', 'right')
    if w in (3, 4):
        return f'wepParamChange {w}: not a weapon-attack context'
    if w == 1 and hand == 'left' or w == 2 and hand == 'right' and ctx.get('grip') != '2h':
        return f'wepParamChange {w} does not match the {hand} hand'
    if si == 197 and not ctx.get('counter_hit'):
        return 'counter-hits only'
    return None


def blue_dancer_factor(t, rate, stateinfo, weight):
    """`VERIFIED` FUN_1404f3450: 1 + (rate - 1) * CalcCorrectGraph[50 or 51](min(weight, 100) / 100).

    `weight` is absolute equipped weight (weapons in all six slots, armor, talismans; arrows not
    counted), not a load ratio. None means unknown and returns the full rate."""
    if weight is None:
        return rate
    g = t.graph(50 if stateinfo == 315 else 51)
    return 1 + (rate - 1) * g(min(weight, 100.0) / 100.0)


def multipliers(t, talismans, ctx=None):
    """Attack-side multipliers of the equipped talismans for one attack context (see module doc)."""
    ctx = dict(ctx or {})
    pvp = ctx.get('pvp', True)
    out = {'power_rate': {e: 1.0 for e in ELEMENTS}, 'damage_rate': {e: 1.0 for e in ELEMENTS},
           'damage_correct': {e: 1.0 for e in ELEMENTS}, 'counter_rate': {e: 1.0 for e in ELEMENTS},
           'stamina_damage': 1.0, 'applied': [], 'skipped': []}
    pt = ctx.get('phys_type', 'standard')
    counter_els = set(ctx.get('counter_elements') or (('physical',) if pt == 'pierce' else ()))
    raw_rate = {e: 1.0 for e in ELEMENTS}
    seen_groups = set()
    for key in talismans:
        tal = t.get(key)
        if tal is None:
            out['skipped'].append((key, None, 'unknown talisman'))
            continue
        if tal.group in seen_groups:
            out['skipped'].append((tal.name, None, f'accessoryGroup {tal.group} already equipped'))
            continue
        seen_groups.add(tal.group)
        for s in tal.speffects:
            f = s.fields
            touches = (any(ATTACK_RATE[e] in f for e in ELEMENTS) or any(k in f for k in POWER_RATE)
                       or any(('atkPlayerDmgCorrectRate_' if pvp else 'atkEnemyDmgCorrectRate_') + EL_SUFFIX[e] in f
                              for e in ELEMENTS) or 'staminaAttackRate' in f)
            if not touches:
                continue
            why = _attack_gate(t, s, ctx)
            if why:
                out['skipped'].append((tal.name, s.id, why))
                continue
            si = f.get('stateInfo', 0)
            if si == 197:
                # Spear: skipped by the accumulator; FUN_1404f5310 multiplies its *AttackRate into
                # the counter-hit rate of the elements whose counter rate is above 1.
                for e in ELEMENTS:
                    v = f.get(ATTACK_RATE[e])
                    if v is not None and e in counter_els:
                        out['counter_rate'][e] *= v
                        out['applied'].append((tal.name, s.id, ATTACK_RATE[e], v, 'counter-hit rate'))
                continue
            if si in (315, 316):
                # Blue Dancer: skipped by the accumulator, scaled by weight, pre-defense, all five.
                v = blue_dancer_factor(t, f.get('physicsAttackPowerRate', 1.0), si, ctx.get('equip_weight'))
                for e in ELEMENTS:
                    out['power_rate'][e] *= v
                out['applied'].append((tal.name, s.id, 'physicsAttackPowerRate', v, 'weight-scaled power rate'))
                continue
            for e in ELEMENTS:
                v = f.get(ATTACK_RATE[e])
                if v is not None:
                    raw_rate[e] = f32(raw_rate[e] * f32(v))
                    out['applied'].append((tal.name, s.id, ATTACK_RATE[e], v, 'post-defense attack rate'))
                k = ('atkPlayerDmgCorrectRate_' if pvp else 'atkEnemyDmgCorrectRate_') + EL_SUFFIX[e]
                if k in f:
                    out['damage_correct'][e] *= f[k]
                    out['applied'].append((tal.name, s.id, k, f[k], 'damage correction'))
            if any(k in f for k in POWER_RATE):
                v = f.get('physicsAttackPowerRate', 1.0) * f.get(PHYS_POWER_RATE.get(pt, 'neutralAttackPowerRate'), 1.0)
                out['power_rate']['physical'] *= v
                out['applied'].append((tal.name, s.id, 'physicsAttackPowerRate', v, 'physical power rate'))
            if 'staminaAttackRate' in f:
                out['stamina_damage'] *= f['staminaAttackRate']
                out['applied'].append((tal.name, s.id, 'staminaAttackRate', f['staminaAttackRate'], 'stamina damage'))
    # FUN_140d24a30: the *AttackRate product is stored as (short)(int)(rate * 100), so the damage
    # sees it truncated to a whole percent (1.17f * 100 = 116.99999 -> 1.16).
    for e in ELEMENTS:
        out['damage_rate'][e] = int(f32(raw_rate[e] * f32(100.0))) / 100.0
    return out


def defender_modifiers(t, talismans, incoming=None):
    """Defender-side modifiers of the equipped talismans for one incoming hit (see module doc)."""
    inc = dict(incoming or {})
    pvp = inc.get('pvp', True)
    hp = inc.get('hp_ratio', 1.0)
    active = set(inc.get('active') or ())
    out = {'damage_taken': {e: 1.0 for e in ELEMENTS}, 'absorption_mult': {k: 1.0 for k in CUT_RATE},
           'poise_mult': 1.0, 'poise_damage_taken': 1.0, 'guard_stamina_mult': 1.0,
           'headshot_bonus_removed': False, 'applied': [], 'skipped': []}
    tough = 1.0
    seen_groups = set()
    for key in talismans:
        tal = t.get(key)
        if tal is None:
            out['skipped'].append((key, None, 'unknown talisman'))
            continue
        if tal.group in seen_groups:
            out['skipped'].append((tal.name, None, f'accessoryGroup {tal.group} already equipped'))
            continue
        seen_groups.add(tal.group)
        for s in tal.speffects:
            f = s.fields
            si = f.get('stateInfo', 0)
            dkey = 'defPlayerDmgCorrectRate_' if pvp else 'defEnemyDmgCorrectRate_'
            if si == 450:
                # Crucible Knot: a head hit keeps part multiplier 1.0 (damage and poise).
                out['headshot_bonus_removed'] = True
                out['applied'].append((tal.name, s.id, 'stateInfo', 450, 'headshot multiplier removed'))
                continue
            touches = (any(dkey + EL_SUFFIX[e] in f for e in ELEMENTS) or any(v in f for v in CUT_RATE.values())
                       or 'toughnessDamageCutRate' in f or 'guardStaminaMult' in f
                       or 'saReceiveDamageRate' in f)
            if not touches:
                continue
            why = _hp_gate(f, hp) or _timed_gate(s, active)
            if why:
                pass
            elif si in (158, 204) and not inc.get('guarded'):
                why = 'only on a guarded hit'
            elif si == 335 and not inc.get('being_critted'):
                why = 'only while receiving a critical'
            elif s.id in VERDIGRIS:
                lr = inc.get('equip_load_ratio')
                lo, hi = VERDIGRIS[s.id]
                if lr is None or not (lo < lr <= hi):
                    why = 'equip load outside this Verdigris band (band INFERRED)'
            if why:
                out['skipped'].append((tal.name, s.id, why))
                continue
            for e in ELEMENTS:
                k = dkey + EL_SUFFIX[e]
                if k in f:
                    out['damage_taken'][e] *= f[k]
                    out['applied'].append((tal.name, s.id, k, f[k], 'damage taken'))
            for typ, k in CUT_RATE.items():
                if k in f:
                    out['absorption_mult'][typ] *= f[k]
            if any(k in f for k in CUT_RATE.values()):
                out['applied'].append((tal.name, s.id, '*DamageCutRate', f.get('neutralDamageCutRate',
                                       f.get('magicDamageCutRate')), 'absorption'))
            if 'toughnessDamageCutRate' in f:
                tough *= f['toughnessDamageCutRate']
                out['applied'].append((tal.name, s.id, 'toughnessDamageCutRate', f['toughnessDamageCutRate'], 'poise'))
            if 'saReceiveDamageRate' in f:
                out['poise_damage_taken'] *= f['saReceiveDamageRate']
                out['applied'].append((tal.name, s.id, 'saReceiveDamageRate', f['saReceiveDamageRate'], 'poise damage'))
            if 'guardStaminaMult' in f and inc.get('guarded'):
                out['guard_stamina_mult'] *= f['guardStaminaMult']
                out['applied'].append((tal.name, s.id, 'guardStaminaMult', f['guardStaminaMult'], 'guard stamina'))
    out['poise_mult'] = 1.0 / tough
    return out


# Verdigris Discus rows -> equip-load ratio band. No code or param applies 19986/19987 (no
# immediate in either image, no link field); the bands are read off the row names "Heavy Weight"
# and "Overweight" and the load tiers of resources.md (`INFERRED`).
VERDIGRIS = {19986: (0.7, 1.0), 19987: (1.0, 99.0)}


# ------------------------------------------------------------------------------------------
# Corpus: how often each talisman is worn in STR PvP builds at RL 140-160.

def corpus_counts(rl_lo=140, rl_hi=160):
    """Per-filter talisman counts, using er-builds-adoption-gap.py's three filters and dedup."""
    gap = _mod('er_builds_adoption_gap', 'er-builds-adoption-gap.py')
    emb = gap.EMBED
    path = os.path.join(CACHE, 'builds.jsonl')
    counts = {k: collections.Counter() for k in gap.FILTERS}
    totals = collections.Counter()
    for kind in gap.FILTERS:
        seen = set()
        for line in open(path):
            row = json.loads(line)
            b = row['build']
            st = emb.stats_of(b)
            if st is None or not rl_lo <= st['rl'] <= rl_hi:
                continue
            tags = set(b.get('tags') or [])
            if b.get('isPvE') or 'PvE' in tags:
                continue
            if kind == 'tag' and 'Strength' not in tags:
                continue
            if kind == 'pvptag' and not ('Strength' in tags and tags & gap.PVP_TAGS):
                continue
            if kind == 'str60' and st['str'] < 60:
                continue
            if sum(st[k] for k in emb.ATTRS) - emb.LEVEL_OFFSET != st['rl']:
                continue
            key = (row.get('user'), tuple(emb.tokens(b)))
            if key in seen:
                continue
            seen.add(key)
            totals[kind] += 1
            worn = gap.slots_at((b.get('talismans') or {}).get('slots'), emb.active_set(b, 'talismans'))
            for n in {s['name'] for _, s in worn}:
                counts[kind][n] += 1
    return totals, counts


# ------------------------------------------------------------------------------------------

def slot_survey(t):
    """Re-derive SLOT_SUBCATS from every named base weapon (see the table's comment)."""
    att = _mod('er_mechanics_attacks', 'er-mechanics-attacks.py')
    reg = att.Regulation()
    stats = collections.defaultdict(collections.Counter)
    for wid, w in reg.weapon.items():
        if wid % 10000 or not 1000000 <= wid < 50000000:
            continue
        nm = reg.weapon_names.get(wid)
        if not nm or nm.startswith('['):
            continue
        for two in (False, True):
            for key, _, judge, _, _ in att.SLOTS_ONE_HAND:
                n = att.attack_numbers(reg, wid, judge + (att.TWO_HAND_JUDGE_OFFSET if two else 0))
                if n:
                    stats[('2h_' if two else '') + key][attack_subcategories(t, n['atk_row'])] += 1
    return stats


def selftest():
    t = Talismans()
    fails = []

    def check(label, got, want):
        ok = got == want if not isinstance(want, float) else abs(got - want) < 1e-4
        print(('ok   ' if ok else 'FAIL ') + label + f': {got!r}' + ('' if ok else f' != {want!r}'))
        if not ok:
            fails.append(label)

    check('talisman count', len(t.by_id), 154)
    check('skipped rows', sorted(x[0] for x in t.skipped), [6100, 204000, 999999999])
    check('Claw subcategory 102', t.get('Claw Talisman').resident[0].subcats(), (102,))
    m = multipliers(t, ['Claw Talisman'], {'slot': 'jump_r2'})
    check('Claw on jump R2, PvP', m['damage_correct']['physical'], 1.075)
    m = multipliers(t, ['Claw Talisman'], {'slot': 'r1_1'})
    check('Claw on R1', m['damage_correct']['physical'], 1.0)
    m = multipliers(t, ['Two-Handed Sword Talisman', 'Claw Talisman'], {'slot': '2h_jump_r1', 'pvp': False})
    check('2H + Claw on 2H jump, PvE', m['damage_correct']['fire'], 1.15 * 1.15)
    m = multipliers(t, ['Axe Talisman'], {'slot': 'r2_1c'})
    check('Axe on charged R2, post-defense rate', m['damage_rate']['physical'], 1.1)
    check('Axe does not touch damage correction', m['damage_correct']['physical'], 1.0)
    check('Axe off on uncharged R2', multipliers(t, ['Axe Talisman'], {'slot': 'r2_1'})['damage_rate']['fire'], 1.0)
    m = multipliers(t, ['Ritual Sword Talisman'], {'slot': 'r1_1', 'hp_ratio': 0.9999})
    check('Ritual Sword off below full HP', m['damage_correct']['physical'], 1.0)
    m = multipliers(t, ['Ritual Sword Talisman'], {'slot': 'r1_1', 'hp_ratio': 1.0})
    check('Ritual Sword at full HP', m['damage_correct']['physical'], 1.1)
    # 20 * 0.01f rounds to 0.19999999f, so exactly 20% HP is outside (FUN_1405012a0).
    check('Red-Feathered off at exactly 20%',
          multipliers(t, ['Red-Feathered Branchsword'], {'hp_ratio': 0.2})['damage_correct']['physical'], 1.0)
    check('Red-Feathered at 19%',
          multipliers(t, ['Red-Feathered Branchsword'], {'hp_ratio': 0.19})['damage_correct']['physical'], 1.2)
    # Dagger: 1.17f * 100 = 116.99999 in float32, stored as the short 116.
    check('Dagger on a critical, truncated', multipliers(t, ['Dagger Talisman'], {'critical': True})['damage_rate']['holy'], 1.16)
    check('Dagger off a critical', multipliers(t, ['Dagger Talisman'], {})['damage_rate']['holy'], 1.0)
    check('Shard of Alexander on a skill', multipliers(t, ['Shard of Alexander'], {'subcategories': [112]})['damage_rate']['physical'], 1.15)
    m = multipliers(t, ['Spear Talisman'], {'counter_hit': True, 'phys_type': 'pierce'})
    check('Spear on a thrust counter', m['counter_rate']['physical'], 1.15)
    check('Spear leaves damage_rate alone', m['damage_rate']['physical'], 1.0)
    check('Spear off without a counter', multipliers(t, ['Spear Talisman'], {'phys_type': 'pierce'})['counter_rate']['physical'], 1.0)
    # Blue Dancer against CalcCorrectGraph 50's points (0,1) (0.08,0.9) (0.16,0.6) (0.2,0.25) (0.3,0).
    for w, want in ((0, 1.15), (8, 1.135), (16, 1.09), (20, 1.0375), (30, 1.0), (60, 1.0)):
        got = multipliers(t, ['Blue Dancer Charm'], {'equip_weight': w})['power_rate']['fire']
        check(f'Blue Dancer at weight {w}', got, want)
    check('Blade of Mercy off by default', multipliers(t, ['Blade of Mercy'], {})['damage_correct']['physical'], 1.0)
    check('Blade of Mercy when active', multipliers(t, ['Blade of Mercy'], {'active': {'blade_of_mercy'}})['damage_correct']['physical'], 1.12)
    check('Rotten Winged stage 4', multipliers(t, ['Rotten Winged Sword Insignia'], {'successive_stage': 4})['damage_correct']['magic'], 1.13)
    check('Hammer stamina damage', multipliers(t, ['Hammer Talisman'], {})['stamina_damage'], 1.4)
    check('graph 50 at 0.16', t.graph(50)(0.16), 0.6)
    m = multipliers(t, ['Arsenal Charm', 'Great-Jar\'s Arsenal'], {})
    check('same accessoryGroup counted once', len([s for s in m['skipped'] if 'accessoryGroup' in s[2]]), 1)
    d = defender_modifiers(t, ["Bull-Goat's Talisman"], {})
    check('Bull-Goat poise x1/0.75', d['poise_mult'], 1 / 0.75)
    d = defender_modifiers(t, ['Dragoncrest Greatshield Talisman'], {'pvp': True})
    check('Dragoncrest Greatshield vs players', d['damage_taken']['physical'], 0.95)
    d = defender_modifiers(t, ['Crucible Scale Talisman'], {})
    check('Crucible Scale off outside a critical', d['absorption_mult']['slash'], 1.0)
    d = defender_modifiers(t, ['Crucible Scale Talisman'], {'being_critted': True})
    check('Crucible Scale while critted', d['absorption_mult']['slash'], 0.7)
    d = defender_modifiers(t, ['Crucible Feather Talisman'], {})
    check('Crucible Feather penalty always on', d['absorption_mult']['fire'], 1.3)
    d = defender_modifiers(t, ['Pearl Shield Talisman', 'Greatshield Talisman'], {'guarded': True})
    check('Pearl Shield on a guarded hit', d['absorption_mult']['fire'], 0.8)
    check('Greatshield guard stamina', d['guard_stamina_mult'], 0.8)
    d = defender_modifiers(t, ['Pearl Shield Talisman'], {})
    check('Pearl Shield unguarded', d['absorption_mult']['fire'], 1.0)
    d = defender_modifiers(t, ['Blue-Feathered Branchsword', 'Ritual Shield Talisman'], {'hp_ratio': 0.1})
    check('Blue-Feathered low HP, Ritual Shield off', d['damage_taken']['physical'], 0.5)
    check('Crucible Knot', defender_modifiers(t, ['Crucible Knot Talisman'], {})['headshot_bonus_removed'], True)
    d = defender_modifiers(t, ["Talisman of Lord's Bestowal"], {'active': {'lords_bestowal'}})
    check("Lord's Bestowal while drinking", d['poise_mult'], 1 / 0.65)
    # Independent grounding: the slot table against a fresh survey of every weapon.
    stats = slot_survey(t)
    for slot, want in SLOT_SUBCATS.items():
        got = stats[slot].most_common(1)[0][0] if stats[slot] else None
        check(f'slot {slot} majority subcategories', got, tuple(want))
    print('selftest:', 'FAIL ' + ', '.join(fails) if fails else 'all passed')
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--affects', help='only talismans touching this system (substring, e.g. attack, poise)')
    ap.add_argument('--show', help='one talisman in full (name or row id)')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--talismans', help='comma list for --slot / --incoming evaluation')
    ap.add_argument('--slot')
    ap.add_argument('--grip', choices=('1h', '2h'))
    ap.add_argument('--pve', action='store_true')
    ap.add_argument('--ctx', help='extra attack/incoming context as JSON')
    ap.add_argument('--incoming', action='store_true', help='evaluate defender_modifiers instead')
    ap.add_argument('--corpus', action='store_true')
    ap.add_argument('--slot-survey', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--markdown', action='store_true', help='the per-talisman table of talismans.md')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    t = Talismans()
    if a.markdown:
        print('| id | talisman | effect (`VERIFIED` regulation) | condition | system | trigger evidence |')
        print('|---|---|---|---|---|---|')
        for x in t:
            for i, s in enumerate(x.speffects):
                sy = s.systems()
                if not sy and i:
                    continue
                eff = '; '.join(f'{n}: {w}' for n, w in sy) or '(no field)'
                cond = '; '.join(s.condition()) or 'always (equipped)'
                ev = trigger_evidence(s)
                print(f"| {x.id if not i else ''} | {x.name if not i else ''} | {s.id}: {eff} | {cond} "
                      f"| {', '.join(sorted({n for n, _ in sy})) or '-'} | {ev} |")
        return 0
    if a.corpus:
        totals, counts = corpus_counts()
        if a.json:
            print(json.dumps({'totals': totals, 'counts': counts}, indent=1))
            return 0
        print('builds:', dict(totals))
        print(f"{'talisman':38}{'pvptag':>8}{'tag':>6}{'str60':>7}")
        for n, c in counts['pvptag'].most_common():
            print(f"{n:38}{c:>8}{counts['tag'][n]:>6}{counts['str60'][n]:>7}")
        return 0
    if a.slot_survey:
        for k, v in slot_survey(t).items():
            print(k, dict(v.most_common(6)))
        return 0
    if a.talismans:
        names = [x.strip() for x in a.talismans.split(',') if x.strip()]
        ctx = json.loads(a.ctx) if a.ctx else {}
        ctx.setdefault('pvp', not a.pve)
        if a.slot:
            ctx['slot'] = a.slot
        if a.grip:
            ctx['grip'] = a.grip
        r = defender_modifiers(t, names, ctx) if a.incoming else multipliers(t, names, ctx)
        print(json.dumps(r, indent=1, default=str))
        return 0
    if a.show:
        tal = t.get(a.show)
        if tal is None:
            raise SystemExit(f'no talisman {a.show!r}')
        if a.json:
            print(json.dumps(tal.to_json(), indent=1))
            return 0
        print(f'{tal.name} ({tal.id}) weight {tal.weight} group {tal.group}: {tal.text}')
        for s in tal.speffects:
            print(f'  {s.id} [{s.via}] {s.name}')
            for c in s.condition():
                print(f'      when: {c}')
            for sysname, what in s.systems():
                print(f'      {sysname}: {what}')
        return 0
    rows = [x for x in t if not a.affects or any(a.affects.lower() in s for s in x.affects())]
    if a.json:
        print(json.dumps([x.to_json() for x in rows], indent=1))
        return 0
    for x in rows:
        eff = '; '.join(f'{n}: {w}' for n, w, _ in x.systems()) or '(no field on its own rows)'
        print(f'{x.id:>5} {x.name:36} {eff}')
    print(f'{len(rows)} talismans; skipped rows: {t.skipped}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
