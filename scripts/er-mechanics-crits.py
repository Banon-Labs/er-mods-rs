#!/usr/bin/env python3
"""Critical hits and parrying: crit hits and damage, parry tools, per-weapon parry exposure.

Write-up: `docs/er-mechanics/crits.md`. Labels as there: `VERIFIED` (regulation value, or read
out of the executable), `TAE` (decoded animation event), `INFERRED`, `COMMUNITY`, `SITE`.

Crit hits (`TAE` + regulation). A crit is a throw: ThrowParam picks the attacker animation,
the animation fires TAE event 304 `ThrowAttackBehavior` (Args+4 = behavior judge), and the
judge resolves through `PlayerIns::ResolveBehaviorId` exactly like any other attack
(`er-mechanics-attacks.py`), family fallback included. The attacking animation is looked up
the way attacks are (`motion_category`) and then followed through the TAE's own
`ImportOtherAnim` reference: dagger `a020_031710` is an import of `a023_031710`, colossal and
greatsword categories import `a032_*`.

    backstab  ThrowParam 10000000 (throwType 1)  attacker 031710  victim a000_070000
    riposte   ThrowParam 30000000 (throwType 20) attacker 031700  victim a000_070010

Crit damage per hit, per element (`VERIFIED`, attack-power builder 0x1406832a0, 1.17.1
0x1406840f0; the read of EquipParamWeapon+0xe0 is at 0x140683453 / 0x1406842a3):

    AR_el * MV_el / 100 * (throwAtkRate + 100) * 0.01    then the ordinary defense step

The factor is taken only when `AttackInfo+0x109` is set, which the `ThrowAttackBehavior`
(TAE 304) damage path sets to 1 (`FUN_14043fc30`, 1.17.1 0x140440190); guard counters and
every other swing do not get it. SpEffects with `throwAttackParamChange` apply their attack
modifiers only to hits with that byte set (`IsApplicableForCategory` 0x140500930, 1.17.1
0x140501700): the Dagger Talisman's 1.17 `*AttackRate` is crit-only.

Parrying (`VERIFIED`, `FUN_14044a910`, 1.17.1 0x14044ae70). Both routes end in the same test
on the attacker: its `actionModifiersFlags & 0x400`, set while its animation has TAE type 0
JumpTable 5 open, plus an angle check. So an attack is parryable exactly while JT5 is open.

    hitbox   the parry skill's TAE type 1 AttackBehavior with AttackType 64 (hit type 0x40,
             judge 591 "Parry Attack") touches the attacker.
    contact  the attacker's own hit touches a parrier whose JumpTable 119 state is open, and
             the hit's AtkParam_Pc.isDisableParry is 0. Still needs the attacker's JT5.

Usage:

    python3 scripts/er-mechanics-crits.py Dagger Misericorde
    python3 scripts/er-mechanics-crits.py --table
    python3 scripts/er-mechanics-crits.py --parry-tools
    python3 scripts/er-mechanics-crits.py Greatsword --json
    python3 scripts/er-mechanics-crits.py --factor --rl 150
    python3 scripts/er-mechanics-crits.py --rescore pvp150.json   # er-builds-pvp --sort score --json
    python3 scripts/er-mechanics-crits.py --selftest

Crit factor (crits.md section 7): a crit's HP on the RL window's PvP corpus, weighted by how
often one happens per exchange (corpus parry-tool carriage, the defenders' parry exposure and
Misericorde swap share are `SITE`; `PARRY_LAND` and `BACKSTAB_PER_EXCHANGE` are `INFERRED`), plus
the HP a parryable attack costs, which replaces a flat parry multiplier.

Interface for other modules (the PvP ranking): `load_tables()`, `crit_profile(t, weapon)`,
`crit_attack(t, weapon, ...)`, `crit_damage(t, weapon, ...)`, `parry_exposure(t, weapon)`,
`parry_tools(t)`, `crit_evidence(t, rl, window, ...)`, `weapon_crit(t, ev, weapon, ...)`.
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

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ATTACKS = _load('er_mechanics_attacks', 'er-mechanics-attacks.py')
AR = _load('er_mechanics_ar', 'er-mechanics-ar.py')
DEF = _load('er_mechanics_defense', 'er-mechanics-defense.py')
TAE = _load('er_tae_event_scan', 'er-tae-event-scan.py')
PR = ATTACKS.PR

#: Crit kinds: (attacker animation, ThrowParam row). `TAE`: event 304 in the resolved
#: animation fires judge 500 (backstab) / 510 (riposte); `VERIFIED` regulation: ThrowParam
#: rows with AtkChrId == DefChrId == 0 carry these atkAnimIds.
CRIT_KINDS = {'backstab': (31710, 10000000), 'riposte': (31700, 30000000)}
TAE_THROW_ATTACK_BEHAVIOR = 304   # Args: u16, s16 index, s32 judge at +4
TAE_ATTACK_BEHAVIOR = 1           # Args: s32 AttackType, s32 index, s32 judge at +8
TAE_JUMP_TABLE = 0
JT_GET_PARRIED = 5                # WitchyBND template: "Get-Parried Window"
JT_FORCE_PARRY_MODE = 119         # WitchyBND template: "TryToInvokeForceParryMode"
ATTACK_TYPE_PARRY = 64            # WitchyBND template AttackType "Parry"
#: Parry-type weapon skills (SwordArtsParam row names). Their TimeAct category is
#: `600 + swordArtsTypeNew` (`INFERRED`: a692..a699 are exactly the files holding AttackType 64
#: events, and 302..309 carry swordArtsTypeNew 92..99).
PARRY_SWORD_ARTS = (302, 303, 305, 306, 307, 309, 1196)
SWORD_ART_TAE_BASE = 600
TAE_FPS = ATTACKS.TAE_FPS
#: `ImportOtherAnim` mini-header: type 1 at +0, imported full anim id (category * 1e6 + anim)
#: at +0x18 (read from the files: a020_031700 -> 23031700).
MINI_IMPORT_TYPE, MINI_IMPORT_ID_OFFSET, ANIM_ID_SCALE = 1, 0x18, 1000000
MAX_IMPORT_HOPS = 4
#: `atkAttribute` 252/253 mean "the weapon's own attribute" (as er-builds-pvp.py reads them).
WEAPON_ATTRIBUTE = {252: 'atkAttribute', 253: 'atkAttribute2'}
PHYS_TYPE = {0: 'slash', 1: 'strike', 2: 'pierce', 3: 'standard'}
VS_PLAYER = {'physical': 'Physics', 'magic': 'Magic', 'fire': 'Fire', 'lightning': 'Thunder',
             'holy': 'Dark'}
MV_KEY = {'physical': 'atkPhysCorrection', 'magic': 'atkMagCorrection',
          'fire': 'atkFireCorrection', 'lightning': 'atkThunCorrection',
          'holy': 'atkDarkCorrection'}

#: Reference RL 150 attackers (sum - 79 = 150) used by `--table`; each weapon takes the one
#: with the highest one-handed AR. The defender is fixed.
REF_ATTACKERS = {
    'strength': {'vig': 60, 'mnd': 15, 'end': 30, 'str': 80, 'dex': 20, 'int': 9, 'fth': 9,
                 'arc': 6},
    'quality': {'vig': 60, 'mnd': 15, 'end': 27, 'str': 50, 'dex': 50, 'int': 9, 'fth': 9,
                'arc': 9},
    'dexterity': {'vig': 60, 'mnd': 15, 'end': 30, 'str': 20, 'dex': 80, 'int': 9, 'fth': 9,
                  'arc': 6},
    'faith': {'vig': 60, 'mnd': 15, 'end': 26, 'str': 30, 'dex': 30, 'int': 9, 'fth': 50,
              'arc': 9},
}
REF_ATTACKER = REF_ATTACKERS['quality']
REF_DEFENDER = {'vig': 60, 'mnd': 20, 'end': 40, 'str': 44, 'dex': 40, 'int': 9, 'fth': 9,
                'arc': 7}
REF_DEFENDER_ARMOR = ('Banished Knight Helm', 'Banished Knight Armor',
                      'Banished Knight Gauntlets', 'Banished Knight Greaves')
TABLE_WEAPONS = ('Greatsword', 'Giant-Crusher', 'Erdsteel Dagger', 'Misericorde', 'Uchigatana',
                 'Dagger', 'Zweihander', 'Claymore', "Cleanrot Knight's Sword",
                 "Banished Knight's Halberd", "Executioner's Greataxe")
CORPUS = os.path.expanduser('~/.cache/er-build-planner/builds.jsonl')
PVP_TAGS = {'Invasions', 'Duels', 'Co-op', 'Gank', '2v2', 'Ladder'}
#: `SITE`: the planner's inventory equipIndex 0..2 is the right hand, 3..5 the left
#: (shields sit at 3/4/5 in 1206 of 1352 equipped cases).
LEFT_HAND_FIRST_INDEX = 3


class Tables:
    """Regulation tables this module reads, plus the attack, AR and defense modules' own."""

    def __init__(self, regulation=None):
        files = PR.load(regulation)

        def table(stem, fields=None):
            rows, _, _ = PR.rows(PR.param_bytes(files, stem), fields)
            return {r['id']: r for r in rows}

        self.reg = ATTACKS.Regulation(regulation)
        self.weapon = table('EquipParamWeapon', [
            'throwAtkRate', 'enableThrow', 'enableParry', 'parryDamageLife', 'attackBaseParry',
            'defenseBaseParry', 'swordArtsParamId', 'wepType', 'atkAttribute', 'atkAttribute2']
            + [f'vsPlayerDmgCorrectRate_{v}' for v in VS_PLAYER.values()])
        self.atk = table('AtkParam_Pc', list(MV_KEY.values()) + [
            'isDisableParry', 'throwFlag', 'throwTypeId', 'finalDamageRateId', 'atkAttribute',
            'atkSuperArmorCorrection', 'dmgLevel', 'dmgLevel_vsPlayer', 'throwDamageAttribute'])
        self.throw = table('ThrowParam')
        self.final_rate = table('FinalDamageRateParam')
        self.sword_arts = table('SwordArtsParam', ['swordArtsTypeNew'])
        self.speffect = {i: r for i, r in table(
            'SpEffectParam', ['throwAttackParamChange'] + list(CRIT_SPEFFECT_FIELDS.values())
        ).items() if r['throwAttackParamChange']}
        self.sword_art_names = PR.row_names('SwordArtsParam')
        # SpEffects the throw branch of CalculateDamage reads on the victim (stateInfo 335), and
        # the talismans that carry one (refId or residentSpEffectId1..4).
        self.crit_cut = {i: r for i, r in table(
            'SpEffectParam', ['stateInfo'] + list(CRIT_CUT_FIELDS.values())).items()
            if r['stateInfo'] == STATE_INFO_CRIT_CUT}
        acc_names = PR.row_names('EquipParamAccessory')
        self.crit_cut_talismans = {}
        for aid, r in table('EquipParamAccessory', ['refId'] + [
                f'residentSpEffectId{k}' for k in range(1, 5)]).items():
            refs = [r['refId']] + [r[f'residentSpEffectId{k}'] for k in range(1, 5)]
            hit = next((s for s in refs if s in self.crit_cut), None)
            if hit is not None and acc_names.get(aid):
                self.crit_cut_talismans[acc_names[aid]] = hit
        self.ar = AR.Tables(regulation)
        self._def = None

    @property
    def defense(self):
        if self._def is None:
            self._def = DEF.Tables()
        return self._def

    def find_weapon(self, key):
        return self.reg.find_weapon(key)


def load_tables(regulation=None):
    return Tables(regulation)


# ------------------------------------------------------------------ TimeAct

_RAW = {}


def _tae_path(category):
    for name in (f'a{category:02d}.tae', f'a{category}.tae'):
        path = os.path.join(ATTACKS.PLAYER_TAE_DIR, name)
        if os.path.exists(path):
            return path
    return None


def _raw_tae(category):
    """(bytes, {anim id: anim header offset}) of `a<category>.tae`, or None."""
    if category not in _RAW:
        path = _tae_path(category)
        if path is None:
            _RAW[category] = None
        else:
            with open(path, 'rb') as handle:
                b = handle.read()
            count, table = struct.unpack_from('<iq', b, TAE.OFF_ANIM_COUNT)
            index = {}
            for i in range(count):
                aid, aoff = struct.unpack_from('<qq', b, table + i * TAE.ANIM_ENTRY_SIZE)
                index[aid] = aoff
            _RAW[category] = (b, index)
    return _RAW[category]


_EVENTS = {}


def tae_events(category, anim):
    """Events of `a<category>_<anim>`, or None when the file or the animation is absent."""
    if category not in _EVENTS:
        path = _tae_path(category)
        _EVENTS[category] = TAE.parse(path)[1] if path else None
    anims = _EVENTS[category]
    return None if anims is None else anims.get(anim)


def import_source(category, anim):
    """(category, anim) an `ImportOtherAnim` entry points at, or None for a standard entry."""
    raw = _raw_tae(category)
    if raw is None or anim not in raw[1]:
        return None
    b, index = raw
    mini = struct.unpack_from('<q', b, index[anim] + 0x18)[0]
    if struct.unpack_from('<i', b, mini)[0] != MINI_IMPORT_TYPE:
        return None
    full = struct.unpack_from('<i', b, mini + MINI_IMPORT_ID_OFFSET)[0]
    return full // ANIM_ID_SCALE, full % ANIM_ID_SCALE


def resolve_anim(category, anim):
    """Follow `ImportOtherAnim` to the source entry. (category, anim, events).

    An import entry keeps a few events of its own (sound, effects) and none of the
    attack events, so its events are the union of its own and the source's (`INFERRED`: the
    dagger crits would otherwise fire no damage event at all).
    """
    events = []
    for _ in range(MAX_IMPORT_HOPS):
        events += tae_events(category, anim) or []
        src = import_source(category, anim)
        if src is None:
            break
        category, anim = src
    return category, anim, events


def _frames(e):
    return round(e.start * TAE_FPS), round(e.end * TAE_FPS)


def _jump_table_windows(events, jid):
    return [_frames(e) for e in events if e.type == TAE_JUMP_TABLE
            and struct.unpack_from('<i', e.params, 0)[0] == jid]


# ------------------------------------------------------------------ crits

#: SpEffectParam fields a `throwAttackParamChange` effect multiplies, per element. That these
#: are the fields the builder's SpEffect factor reads for crits is `INFERRED` from the rows
#: (Dagger Talisman 320900: 1.17 on all five, nothing else set).
CRIT_SPEFFECT_FIELDS = {'physical': 'physicsAttackRate', 'magic': 'magicAttackRate',
                        'fire': 'fireAttackRate', 'lightning': 'thunderAttackRate',
                        'holy': 'darkAttackRate'}
DAGGER_TALISMAN_SPEFFECT = 320900
#: `VERIFIED` (EXE, CalculateDamage 0x1404472b0 / 1.17.1 0x140447810; the compare
#: `cmp byte [r15+0xd9], 2` at 0x140447c08 / 0x140448168 is byte-checked in both images): when the
#: hit's throwFlag copy is 2 and its AtkParam row has throwDamageAttribute 1, the victim's
#: `CalculateDamageCutRates` (0x1404f5210 / 0x1404f5fe0) multiplies each element by the cut rates
#: of its SpEffects with stateInfo 335. Regulation: 335 is carried by the Crucible Scale Talisman
#: (340600) and the Talisman of All Crucibles (20382402), 0.7 on every element; the crit rows with
#: throwDamageAttribute 1 are the backstab judges 500/501/505/506, never a riposte 510/511.
STATE_INFO_CRIT_CUT = 335
CRIT_CUT_FIELDS = {'physical': 'neutralDamageCutRate', 'magic': 'magicDamageCutRate',
                   'fire': 'fireDamageCutRate', 'lightning': 'thunderDamageCutRate',
                   'holy': 'darkDamageCutRate'}
CRUCIBLE_SCALE_SPEFFECT = 340600


def crit_multiplier(t, weapon_id):
    """(throwAtkRate + 100) * 0.01, as the builder computes it."""
    return (t.weapon[weapon_id]['throwAtkRate'] + 100) * 0.01


def crit_speffect_rates(t, speffect_ids):
    """Per-element product of the crit-only SpEffects among `speffect_ids`."""
    out = {el: 1.0 for el in MV_KEY}
    for sid in speffect_ids:
        sp = t.speffect.get(sid)
        if sp and sp['throwAttackParamChange']:
            for el, f in CRIT_SPEFFECT_FIELDS.items():
                out[el] *= sp[f]
    return out


def crit_hits(t, weapon_id, kind):
    """Hits of one crit: [{'judge', 'atk_row', 'frames', MV per element, ...}] (in order)."""
    w = t.reg.weapon[weapon_id]
    anim = CRIT_KINDS[kind][0]
    category, src_anim, events = resolve_anim(ATTACKS.motion_category(w, anim), anim)
    hits = []
    for e in sorted(events, key=lambda x: x.start):
        if e.type != TAE_THROW_ATTACK_BEHAVIOR:
            continue
        judge = struct.unpack_from('<i', e.params, 4)[0]
        bid = t.reg.resolve_behavior_id(judge, w['behaviorVariationId'])
        b = t.reg.behavior.get(bid)
        if b is None or b['refType'] != 0 or b['refId'] not in t.atk:
            continue
        a = t.atk[b['refId']]
        hits.append({'judge': judge, 'behavior_row': bid, 'atk_row': b['refId'],
                     'atk_name': t.reg.atk_names.get(b['refId']), 'frames': _frames(e),
                     'mv': {el: a[f] for el, f in MV_KEY.items()},
                     'throw_flag': a['throwFlag'], 'final_rate_id': a['finalDamageRateId'],
                     'atk_attribute': a['atkAttribute'],
                     'throw_damage_attribute': a['throwDamageAttribute']})
    return {'kind': kind, 'anim': f'a{category:03d}_{src_anim:06d}', 'hits': hits,
            'mv_total': sum(h['mv']['physical'] for h in hits)}


def anim_frames(category, anim):
    """Clip length in 30 fps frames from the unpacked hkx, or None."""
    path = ATTACKS.hkx_path(category, anim)
    clip = ATTACKS.hkx_duration(path) if path else None
    return round(clip[0] * TAE_FPS) if clip else None


def crit_profile(t, weapon):
    """Crit numbers of one weapon that need no stats: multiplier, hits, animation lengths."""
    wid = t.find_weapon(weapon)
    w = t.weapon[wid]
    out = {'weapon': t.reg.weapon_names.get(wid), 'id': wid, 'throwAtkRate': w['throwAtkRate'],
           'crit_multiplier': crit_multiplier(t, wid), 'enableThrow': w['enableThrow']}
    for kind, (_, throw_row) in CRIT_KINDS.items():
        c = crit_hits(t, wid, kind)
        cat, anim = (int(x) for x in c['anim'][1:].split('_'))
        th = t.throw.get(throw_row, {})
        victim = th.get('defAnimId')
        c.update({'attacker_frames': anim_frames(cat, anim),
                  'victim_anim': f'a000_{victim:06d}' if victim else None,
                  'victim_frames': anim_frames(0, victim) if victim else None,
                  'throw_type': th.get('throwType'), 'range_m': th.get('Dist')})
        out[kind] = c
    return out


def _phys_type(t, wid, atk_attribute):
    if atk_attribute in WEAPON_ATTRIBUTE:
        atk_attribute = t.weapon[wid][WEAPON_ATTRIBUTE[atk_attribute]]
    return PHYS_TYPE.get(atk_attribute, 'standard')


def crit_attack(t, weapon, kind='riposte', affinity='Standard', level=None, stats=None,
                two_handed=False, pvp=True, speffects=()):
    """Pre-defense attack of every hit of one crit, per element.

    Each hit: `attack_by` (AR x MV x crit multiplier x crit-only SpEffects, x the weapon's
    vsPlayerDmgCorrectRate when `pvp`), `phys` (the physical type its absorption uses),
    `final_rate` (its FinalDamageRateParam row when `pvp`, else None) and `crit_cut` (the row's
    throwDamageAttribute is 1, so a victim's stateInfo 335 SpEffects cut it; section 2 of
    crits.md). `weapon` is a name or id; its base row plus `affinity` picks the infusion.
    """
    sp_rate = crit_speffect_rates(t, speffects)
    wid = t.find_weapon(weapon)
    base = (wid // 10000) * 10000
    aff_id = t.ar.find_weapon(base, affinity)
    level = t.ar.max_level(t.ar.weapons[aff_id]['reinforceTypeId']) if level is None else level
    ar = AR.attack_rating(t.ar, base, affinity, level, stats or REF_ATTACKER, two_handed)
    ar_by = {el: d['total'] for el, d in ar['damage'].items()}
    mult = crit_multiplier(t, aff_id)
    c = crit_hits(t, aff_id, kind)
    hits = []
    for h in c['hits']:
        attack = {el: ar_by.get(el, 0.0) * h['mv'][el] / 100.0 * mult * sp_rate[el]
                  for el in MV_KEY}
        if pvp:
            attack = {el: v * t.weapon[aff_id][f'vsPlayerDmgCorrectRate_{VS_PLAYER[el]}']
                      for el, v in attack.items()}
        fr = t.final_rate.get(h['final_rate_id']) if pvp and h['final_rate_id'] >= 0 else None
        hits.append({'judge': h['judge'], 'mv': h['mv']['physical'], 'attack_by': attack,
                     'attack': sum(attack.values()),
                     'phys': _phys_type(t, aff_id, h['atk_attribute']), 'final_rate': fr,
                     'crit_cut': h['throw_damage_attribute'] == 1})
    return {'weapon': ar['weapon'], 'affinity': affinity, 'level': level, 'kind': kind,
            'two_handed': two_handed, 'ar': ar['total'], 'crit_multiplier': mult,
            'mv_total': c['mv_total'], 'attack': sum(h['attack'] for h in hits), 'hits': hits}


def crit_cut_rates(t, speffect_id):
    """Per-element damage factor of a stateInfo 335 SpEffect (physical: the neutral column; the
    Crucible rows carry the same value in all four physical columns)."""
    r = t.crit_cut[speffect_id]
    return {el: r[f] for el, f in CRIT_CUT_FIELDS.items()}


def crit_damage(t, weapon, kind='riposte', affinity='Standard', level=None, stats=None,
                two_handed=False, defender=None, pvp=True, speffects=(), defender_speffects=()):
    """Damage of one crit on `defender` (a `er-mechanics-defense.py` defender dict).

    `defender=None` returns the pre-defense attack only. `pvp` applies the weapon's
    vsPlayerDmgCorrectRate and each hit's FinalDamageRateParam row, as er-builds-pvp.py does
    for ordinary attacks. `speffects`: attacker SpEffect ids; only the crit-only ones
    (`throwAttackParamChange`, e.g. DAGGER_TALISMAN_SPEFFECT) are applied here.
    `defender_speffects`: victim SpEffect ids; the stateInfo 335 ones (Crucible Scale
    Talisman) cut the hits whose row has throwDamageAttribute 1.
    """
    c = crit_attack(t, weapon, kind, affinity, level, stats, two_handed, pvp, speffects)
    cut = {el: 1.0 for el in MV_KEY}
    for sid in defender_speffects:
        if sid in t.crit_cut:
            cut = {el: v * crit_cut_rates(t, sid)[el] for el, v in cut.items()}
    total_damage, per_hit = 0.0, []
    for h in c['hits']:
        hit = {'judge': h['judge'], 'mv': h['mv'], 'attack': h['attack']}
        if defender is not None:
            dmg = DEF.damage(h['attack_by'], 100.0, defender, h['phys'], h['final_rate'])
            by = {el: v * (cut[el] if h['crit_cut'] else 1.0) for el, v in dmg['by_type'].items()}
            hit['damage'] = sum(by.values())
            hit['final_rate'] = h['final_rate']['physRate'] if h['final_rate'] else 1.0
            total_damage += hit['damage']
        per_hit.append(hit)
    return {**{k: c[k] for k in ('weapon', 'affinity', 'level', 'kind', 'two_handed', 'ar',
                                 'crit_multiplier', 'mv_total', 'attack')},
            'damage': total_damage if defender is not None else None, 'hits': per_hit}


def reference_defender(t, armor=REF_DEFENDER_ARMOR, pvp=True):
    return DEF.defender(t.defense, REF_DEFENDER, armor=armor, pvp=pvp)


# ------------------------------------------------------------------ parry

def parry_exposure(t, weapon, grips=('one', 'both')):
    """Share of the moveset (attack slots of `er-mechanics-attacks.py`) that can be parried.

    Per slot: `classic` = its animation has JumpTable 5 open, which both parry routes require;
    `contact` = its AtkParam row has isDisableParry 0, so it is also parried by touching a
    parrier in the JumpTable 119 state (a wider catch, never a new exposure). Each slot counts
    once; the exposure is the share with JT5.
    """
    wid = t.find_weapon(weapon)
    slots = []
    for grip in grips:
        for r in ATTACKS.weapon_attacks(t.reg, wid, grip):
            cat, anim = (int(x) for x in r['anim'][1:].split('_'))
            _, _, events = resolve_anim(cat, anim)
            classic = _jump_table_windows(events, JT_GET_PARRIED)
            contact = t.atk[r['atk_row']]['isDisableParry'] == 0
            react = parry_reaction_share(r, classic) if classic else 0.0
            slots.append({'slot': r['slot'], 'label': r['label'], 'anim': r['anim'],
                          'atk_row': r['atk_row'], 'classic_windows': classic,
                          'classic': bool(classic), 'contact': contact,
                          'parryable': bool(classic), 'reactable': react})
    n = len(slots)

    def share(key, subset=None):
        s = [x for x in slots if subset is None or x['slot'].startswith('2h_') == subset]
        return round(sum(x[key] for x in s) / len(s), 3) if s else None
    return {'weapon': t.reg.weapon_names.get(wid), 'id': wid, 'slots': n,
            'exposure': share('parryable'), 'classic': share('classic'),
            'contact': share('contact'), 'exposure_1h': share('parryable', False),
            'exposure_2h': share('parryable', True), 'reactive': share('reactable'),
            'detail': slots}


#: The shield a parrying defender blocks with when he does not parry: Buckler +25, the shield of
#: the Buckler Parry (`INFERRED`: the corpus names the parry skill, not which shield carries it).
PARRY_SHIELD, PARRY_SHIELD_LEVEL = 'Buckler', 25
#: Slot weight = (damage per committed frame) ** this. A power above 1 lets a slot that is far
#: better than the rest decide the weapon's parryability (`INFERRED`, user 2026-10-04: "if the
#: only moveset that isn't parryable is the best moveset by far, then maybe the others don't
#: matter quite as much"; the windup penalty in `er-mechanics-gear-synergy` uses 2 for the same
#: reason).
PARRY_VALUE_POWER = 2
_GUARD = []


def _guard():
    if not _GUARD:
        g = _load('er_mechanics_powerstance_guard', 'er-mechanics-powerstance-guard.py')
        reg = g.Tables()
        _GUARD.extend((g, reg, g.shield_guard(reg, reg.find_weapon(PARRY_SHIELD), PARRY_SHIELD_LEVEL)))
    return _GUARD


def parry_guard_leak(weapon, ar_by):
    """{slot: share of the slot's pre-defense damage that a raised `PARRY_SHIELD` lets through}
    (`er-mechanics-powerstance-guard.block_hit` chip over the unblocked attack). `ar_by` is
    {element: attack rating} of the build. Status buildup through the guard is not counted."""
    g, reg, guard = _guard()
    out = {}
    for grip in ('one', 'both'):
        for r in g.ATK.weapon_attacks(reg, reg.find_weapon(weapon), grip):
            raw = sum(ar_by.get(el, 0.0) * r[mv] / 100.0 for el, _, _, mv in g.ELEMENT_CUTS)
            if raw:
                out[r['slot']] = round(g.block_hit(reg, r, ar_by, guard)['chip_raw_total'] / raw, 3)
    return out


MV_FIELDS = (('physical', 'mv_phys'), ('magic', 'mv_mag'), ('fire', 'mv_fire'),
             ('lightning', 'mv_light'), ('holy', 'mv_holy'))


#: Status buildup a blocked hit keeps (docs/er-mechanics/status.md section 1c, `VERIFIED`):
#: 1 - cancel x min(1, shield GuardResist x ReinforceParamWeapon rate / 100), the same for every
#: status on a Buckler (19, rate 1.0). `cancel` is the attacker's guardCutCancelRate term, taken as
#: 1 here (`INFERRED`: 18 weapon bases carry -30 or -50 and are not read).
_STATUS = []


def _status():
    if not _STATUS:
        st = _load('er_mechanics_status', 'er-mechanics-status.py')
        tables = st.Tables()
        _STATUS.extend((st, tables, st.corpus_defender(st.corpus_rows())))
    return _STATUS


def parry_shield_status_leak():
    """Bleed build-up a raised `PARRY_SHIELD` lets through (every status is the same on a
    Buckler)."""
    _, reg, guard = _guard()
    files = PR.load(None)
    w = {r['id']: r for r in PR.rows(PR.param_bytes(files, 'EquipParamWeapon'),
                                      ['bloodGuardResist', 'reinforceTypeId'])[0]}[guard['weapon']]
    rf = {r['id']: r for r in PR.rows(PR.param_bytes(files, 'ReinforceParamWeapon'),
                                       ['bloodGuardResistRate'])[0]}.get(w['reinforceTypeId'] + PARRY_SHIELD_LEVEL, {})
    return round(1.0 - min(1.0, max(0.0, w['bloodGuardResist'] * rf.get('bloodGuardResistRate', 1.0) / 100.0)), 3)


def status_share(t, weapon, affinity, stats, ar_by):
    """Share of the R1's worth that is status: its proc HP per hit (`er-mechanics-status`
    `status_per_hit` against the RL 150 corpus median defender) over that plus the R1's damage on
    the reference defender. 0 for a build with no status."""
    st, tables, dfn = _status()
    level = tables.ar.max_level(tables.ar.weapons[tables.ar.find_weapon(weapon, affinity)]['reinforceTypeId'])
    ws = st.weapon_status(tables, weapon, affinity, level, stats)
    if not ws['sources']:
        return 0.0
    row = next((r for r in ATTACKS.weapon_attacks(t.reg, t.find_weapon(weapon), 'one')
                if r['slot'] == 'r1_1'), None)
    if row is None:
        return 0.0
    s = sum(v['hp_per_hit'] for v in st.status_per_hit(tables, ws, row, dfn).values())
    by = {el: ar_by.get(el, 0.0) * row[k] / 100.0 for el, k in MV_FIELDS}
    d = sum(DEF.damage(by, 100.0, reference_defender(t), _phys_type(t, t.find_weapon(weapon),
                                                                    row['atk_attribute']))['by_type'].values())
    return round(s / (s + d), 3) if s + d else 0.0


def _slot_value(reg, wid, row, ar_by):
    """Damage per committed frame: the slot's attack over every sweep hitbox, its own and the
    other judges', divided by its earliest dodge cancel, the commitment `er-builds-pvp.slot_score`
    divides by."""
    def attack(nums):
        return sum(ar_by.get(el, 0.0) * nums[k] / 100.0 for el, k in MV_FIELDS)
    dmg = attack(row) * max(1, row.get('own_sweep_hits') or 1)
    for x in row.get('other_hitboxes') or []:
        nums = ATTACKS.attack_numbers(reg, wid, x['judge']) if x.get('sweep_hit') else None
        if nums:
            dmg += attack(nums)
    end = (row.get('cancel_frame') or {}).get('dodge')
    return dmg / end if dmg and end else 0.0


def build_ar(t, weapon, affinity='Standard', stats=None):
    """{element: attack rating} one-handed at the affinity's highest upgrade."""
    base = (t.find_weapon(weapon) // 10000) * 10000
    aff_id = t.ar.find_weapon(base, affinity)
    level = t.ar.max_level(t.ar.weapons[aff_id]['reinforceTypeId'])
    ar = AR.attack_rating(t.ar, base, affinity, level, stats or REF_ATTACKER, False)
    return {el: d['total'] for el, d in ar['damage'].items()}


def parryability(t, weapon, ar_by, affinity='Standard', stats=None, grips=('one', 'both')):
    """The weapon's moveset as a parrying defender sees it: per slot, the share of defenders who
    parry it on reaction (`parry_reaction_share`) times what a block of it would have stopped,
    weighted by `_slot_value` ** `PARRY_VALUE_POWER`. A block's leak blends the damage leak
    (`parry_guard_leak`) and the status leak (`parry_shield_status_leak`) by the build's
    `status_share` (`INFERRED`: the R1's share stands for every slot's). `naive` is the plain
    share of parryable slots, as `parry_exposure` gives it."""
    pe = parry_exposure(t, weapon, grips)
    sigma = status_share(t, weapon, affinity, stats, ar_by)
    s_leak = parry_shield_status_leak()
    leak = {k: round((1 - sigma) * v + sigma * s_leak, 3) for k, v in parry_guard_leak(weapon, ar_by).items()}
    wid = t.find_weapon(weapon)
    rows = {r['slot']: r for g in grips for r in ATTACKS.weapon_attacks(t.reg, wid, g)}
    num = den = 0.0
    slots = []
    for s in pe['detail']:
        v = _slot_value(t.reg, wid, rows[s['slot']], ar_by) ** PARRY_VALUE_POWER
        p = s['reactable'] * (1.0 - leak.get(s['slot'], 0.0))
        num, den = num + v * p, den + v
        slots.append({'slot': s['slot'], 'label': s['label'], 'value': v, 'reactable': s['reactable'],
                      'leak': leak.get(s['slot']), 'parry': round(p, 3)})
    return {'weapon': pe['weapon'], 'naive': pe['exposure'], 'reactive': pe['reactive'],
            'weighted': round(num / den, 3) if den else None, 'status_share': sigma,
            'status_leak': s_leak, 'slots': slots}


_REACTION = []


def parry_reaction_share(row, classic):
    """Share of defenders who parry the slot on reaction: one who presses Parry at his reaction
    delay (`er-mechanics-ashes.reaction_delays`, network legs included) has its hitbox open from
    `PARRY_HITBOX[0]` frames later, and that must come no later than both the last JumpTable 5
    frame and the first active frame. The attack is taken as visible from its first frame, and the
    JumpTable 5 clip frames as real frames (`INFERRED`: a slot whose clip plays faster than speed 1
    is read slightly late)."""
    if not _REACTION:
        _REACTION.extend(_load('er_mechanics_ashes', 'er-mechanics-ashes.py').reaction_delays())
    starts = sorted(s for s, _ in (row.get('hit_windows') or [])
                    + [x['frames'] for x in row.get('other_hitboxes') or [] if x['hits']])
    if not starts:
        return 0.0
    # Each window guards the first swing that starts with or after it.
    latest = max(min(e, next((h for h in starts if h >= s - 1), e)) for s, e in classic)
    return round(sum(w for d, w in _REACTION if d + PARRY_HITBOX[0] <= latest), 3)


#: Medium roll (`er-mechanics-disengage.tool('roll medium')`): i-frames f0-13, skill gate f20.
ROLL_IFRAMES, ROLL_TO_SKILL = 13, 20
#: Parry (302) hitbox, clip frames of a692_040000 (`parry_tools`). Its play speed is taken as 1.
PARRY_HITBOX = (4, 6)
#: Hitbox windows closer than this many real frames are one swing.
SAME_SWING_GAP = 2


def _swings(windows):
    out = []
    for s, e in sorted(windows):
        if out and s - out[-1][1] <= SAME_SWING_GAP:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def parry_follow_up(t, weapon, grips=('one', 'both')):
    """Slots a defender can roll the first swing of and parry a later one.

    Per later swing: `locked` when the attacker has no dodge or guard cancel before it starts;
    `gap` the real frames from the earlier swing's last active frame to the later one's first;
    `presses` how many frames the Parry can be pressed on and land, or None. The defender takes a
    medium roll whose i-frames cover the earlier swing whole, so the press comes no sooner than
    that roll's skill gate, and the Parry hitbox must touch a JumpTable 5 window of the later
    swing no later than its first active frame (`INFERRED`: a hit landing on the same frame is
    parried, and the parry clip plays at speed 1)."""
    wid = t.find_weapon(weapon)
    out = []
    for grip in grips:
        for r in ATTACKS.weapon_attacks(t.reg, wid, grip):
            sw = _swings((r.get('hit_windows') or []) + [x['frames'] for x in r.get('other_hitboxes') or []
                                                          if x['hits']])
            if len(sw) < 2:
                continue
            cat, anim = (int(x) for x in r['tae_entry'][1:].split('_'))
            _, _, events = ATTACKS.resolve_events(cat, anim)
            if not events:
                continue
            to_real = ATTACKS.clip_to_real(events)
            jt5 = [(ATTACKS.real_frame(to_real(e.start)), ATTACKS.real_frame(to_real(e.end)))
                   for e in events if e.type == TAE_JUMP_TABLE
                   and struct.unpack_from('<i', e.params, 0)[0] == JT_GET_PARRIED]
            cf = r.get('cancel_frame') or {}
            bail = [cf[k] for k in ('dodge', 'guard') if cf.get(k) is not None]
            for k in range(1, len(sw)):
                (a0, a1), (b0, _) = sw[k - 1], sw[k]
                first_press = math.ceil(a1 - ROLL_IFRAMES) + ROLL_TO_SKILL
                presses = None
                if a1 - ROLL_IFRAMES <= a0:
                    for s, e in jt5:
                        if not a1 < s <= b0:
                            continue
                        lo = max(first_press, s - PARRY_HITBOX[1])
                        hi = min(e, b0) - PARRY_HITBOX[0]
                        if hi >= lo:
                            presses = max(presses or 0, math.floor(hi) - math.ceil(lo) + 1)
                out.append({'slot': r['slot'], 'label': r['label'], 'swing': k + 1,
                            'swings': len(sw), 'first': sw[k - 1], 'later': sw[k],
                            'gap': round(b0 - a1, 1), 'jt5': jt5,
                            'locked': not bail or min(bail) >= b0, 'presses': presses})
    return out


def parry_tools(t):
    """Every parry-type skill: TAE category, parry hitbox windows, weapons that carry it."""
    carriers = collections.defaultdict(list)
    for wid, w in t.weapon.items():
        name = t.reg.weapon_names.get(wid)
        if wid % 10000 == 0 and name and w['swordArtsParamId'] in PARRY_SWORD_ARTS:
            carriers[w['swordArtsParamId']].append(name)
    out = []
    for art in PARRY_SWORD_ARTS:
        row = t.sword_arts.get(art)
        if row is None:
            continue
        category = SWORD_ART_TAE_BASE + row['swordArtsTypeNew']
        anims = []
        raw = _raw_tae(category)
        for anim in sorted(raw[1]) if raw else []:
            _, _, events = resolve_anim(category, anim)
            parry = [(*_frames(e), struct.unpack_from('<i', e.params, 8)[0]) for e in events
                     if e.type == TAE_ATTACK_BEHAVIOR
                     and struct.unpack_from('<i', e.params, 0)[0] == ATTACK_TYPE_PARRY]
            if parry:
                anims.append({'anim': f'a{category:03d}_{anim:06d}', 'parry_hitbox': parry,
                              'force_parry_mode': _jump_table_windows(events, JT_FORCE_PARRY_MODE),
                              'frames': anim_frames(category, anim)})
        out.append({'sword_art': art, 'name': t.sword_art_names.get(art), 'tae_category': category,
                    'animations': anims, 'default_on': sorted(carriers.get(art, []))})
    return out


def corpus_parry_share(t, path=CORPUS):
    """STR PvP builds (STR >= every other damage stat) carrying a parry skill, by hand."""
    names = {}
    for wid, n in t.reg.weapon_names.items():
        if n and wid % 10000 == 0 and wid in t.weapon:
            names.setdefault(n, wid)
    parry_names = {t.sword_art_names.get(a) for a in PARRY_SWORD_ARTS}
    total, left, any_hand, by_tool = 0, 0, 0, collections.Counter()
    for line in open(path, encoding='utf-8'):
        b = json.loads(line)['build']
        pvp = b.get('isPvE') is False or set(b.get('tags') or []) & PVP_TAGS
        st = b.get('stats') or {}
        if not pvp or st.get('str', 0) < max(st.get(k, 0) for k in ('dex', 'int', 'fth', 'arc')):
            continue
        total += 1
        hits = []
        for s in (b.get('inventory') or {}).get('slots') or []:
            wid = names.get(s.get('name'))
            if s.get('equipIndex') is None or wid is None:
                continue
            art = s.get('weaponArt') or t.sword_art_names.get(t.weapon[wid]['swordArtsParamId'])
            if art in parry_names:
                hits.append((s['equipIndex'] >= LEFT_HAND_FIRST_INDEX, art))
        if hits:
            any_hand += 1
            left += any(h[0] for h in hits)
            by_tool[next(h[1] for h in hits)] += 1
    return {'str_pvp_builds': total, 'with_parry_skill': any_hand, 'left_hand': left,
            'share': round(any_hand / total, 3) if total else None, 'by_skill': dict(by_tool)}


# ------------------------------------------------------------------ crit factor

#: Weights of the crit factor (crits.md section 7). `INFERRED`: modelling choices, no match data
#: exists to measure them. PARRY_LAND is the chance a parry-tool carrier parries one parryable
#: attack it faces in an exchange; BACKSTAB_PER_EXCHANGE the chance an exchange ends in a
#: backstab. Everything else in the factor is measured on the RL window's PvP corpus.
PARRY_LAND = 0.10
BACKSTAB_PER_EXCHANGE = 0.02
#: A right-hand weapon other than the primary with at least this throwAtkRate is counted as a
#: crit swap (`SITE`: the Misericorde, 40, is the most carried one). `INFERRED`: that it is
#: carried for crits and swapped to after a parry is `COMMUNITY`, not measured.
SWAP_MIN_THROW_RATE = 30
RIGHT_HAND_LAST_POS = LEFT_HAND_FIRST_INDEX - 1
_PVP = {}


def _pvp_module():
    """`er-builds-pvp.py`, loaded on first use: its corpus filter, `Defenders` and `corpus_hit`."""
    if 'm' not in _PVP:
        _PVP['m'] = _load('er_builds_pvp', 'er-builds-pvp.py')
    return _PVP['m']


def _fold(name):
    return unicodedata.normalize('NFKD', name or '').encode('ascii', 'ignore').decode().lower().strip()


def _weapon_ids(t):
    if not hasattr(t, '_wid_by_name'):
        out = {}
        for wid, n in t.reg.weapon_names.items():
            if n and wid % 10000 == 0 and wid in t.weapon and wid in t.ar.weapons:
                out.setdefault(_fold(n), wid)
        t._wid_by_name = out
    return t._wid_by_name


def window_builds(rl, window, mirror=CORPUS):
    """The builds `er-builds-pvp.pvp_corpus` keeps for RL `rl` +- `window`, in the same order,
    whole (it keeps only `computed`): so `Defenders([b['computed'] for b in ...])` is its corpus."""
    P = _pvp_module()
    out = []
    with open(mirror, encoding='utf-8') as handle:
        for line in handle:
            b = json.loads(line)['build']
            st = P.EMBED.stats_of(b)
            if st is None or not rl - window <= st['rl'] <= rl + window or not P.is_pvp(b):
                continue
            if sum(st[k] for k in P.EMBED.ATTRS) - P.OPT.LEVEL_OFFSET != st['rl']:
                continue
            c = b.get('computed') or {}
            if c.get('defenses') and c.get('absorption'):
                out.append(b)
    return out


def build_weapons(t, b):
    """Weapons of the build's active set: [{'pos', 'id', 'name', 'infusion', 'art'}]; `pos` 0..2
    is the right hand, 3..5 the left. Names the regulation does not know are dropped."""
    P = _pvp_module()
    active = P.EMBED.active_set(b, 'weapons')
    ids = _weapon_ids(t)
    out = []
    for s in P.EMBED.equipped((b.get('inventory') or {}).get('slots'), active):
        es = s.get('equipSet')
        pos = (es[active] if active < len(es) else None) if isinstance(es, list) else s.get('equipIndex')
        wid = ids.get(_fold(s.get('name')))
        if pos is None or wid is None:
            continue
        art = s.get('weaponArt')
        if not art or art == 'No Skill':
            art = t.sword_art_names.get(t.weapon[wid]['swordArtsParamId'])
        aff = s.get('infusion') or 'Standard'
        if aff not in AR.AFFINITIES or (wid + AR.AFFINITIES.index(aff) * 100) not in t.ar.weapons:
            aff = 'Standard'
        out.append({'pos': pos, 'id': wid, 'name': s.get('name'), 'infusion': aff, 'art': art})
    return sorted(out, key=lambda w: w['pos'])


def build_talismans(b):
    P = _pvp_module()
    slots = (b.get('talismans') or {}).get('slots')
    return [s['name'] for s in P.EMBED.equipped(slots, P.EMBED.active_set(b, 'talismans')) if s.get('name')]


def _build_stats(b):
    st = b.get('stats') or {}
    return {k: int(st.get(k, 0)) for k in AR.STATS}


def corpus_crit(t, hits, defenders, cut_share=0.0, cut=None):
    """Mean HP damage of one crit (`crit_attack` hits) over a `er-builds-pvp.Defenders` corpus.

    A crit is never a counter hit (the victim is in the throw), so the counter factor is 1.
    `cut_share` of the corpus wears a stateInfo 335 SpEffect with per-element rates `cut`; it
    scales the `crit_cut` hits by 1 - share + share x rate after defense (`INFERRED`: the mean
    assumes wearing it is independent of the wearer's defense)."""
    P = _pvp_module()
    ones = {el: 1.0 for el in MV_KEY}
    total = 0.0
    for h in hits:
        post = None
        if h['crit_cut'] and cut_share and cut:
            post = {el: 1.0 - cut_share + cut_share * cut[el] for el in MV_KEY}
        total += P.corpus_hit(h['attack_by'], h['phys'], defenders, h['final_rate'], ones, post)['mean']
    return total


def crit_evidence(t, rl=150, window=10, mirror=CORPUS, defenders=None):
    """Everything the crit factor measures on the RL window's PvP corpus (crits.md section 7).

    `parry_tool`: share of builds whose active set carries a parry skill in either hand.
    `crit_cut`: share wearing a stateInfo 335 talisman (Crucible Scale). `swap`: share with a
    right-hand weapon other than the primary at throwAtkRate >= SWAP_MIN_THROW_RATE.
    `exposure`: mean `parry_exposure` of each build's primary (lowest right-hand position)
    weapon in its own grip (`is2h`). `riposte_hp` / `backstab_hp`: mean over builds of the best
    crit among their own right-hand weapons (their stats and infusion, max level, their grip)
    on the corpus itself: what a parried or backstabbed player loses."""
    P = _pvp_module()
    builds = window_builds(rl, window, mirror)
    defenders = defenders or P.Defenders([b['computed'] for b in builds])
    parry_names = {t.sword_art_names.get(a) for a in PARRY_SWORD_ARTS}
    tool, tool_left, by_skill, cut_by = 0, 0, collections.Counter(), collections.Counter()
    swap, swap_by, exposure, crit_rows, unknown = 0, collections.Counter(), [], [], 0
    exp_cache, crit_cache = {}, {}
    cut_names = t.crit_cut_talismans
    for b in builds:
        ws = build_weapons(t, b)
        tools = [w for w in ws if w['art'] in parry_names]
        if tools:
            tool += 1
            tool_left += any(w['pos'] >= LEFT_HAND_FIRST_INDEX for w in tools)
            by_skill[tools[0]['art']] += 1
        worn = [n for n in build_talismans(b) if n in cut_names]
        if worn:
            cut_by[worn[0]] += 1
        right = [w for w in ws if w['pos'] <= RIGHT_HAND_LAST_POS]
        if not right:
            unknown += 1
            continue
        primary = right[0]
        swaps = [w for w in right[1:] if w['id'] != primary['id']
                 and t.weapon[w['id']]['throwAtkRate'] >= SWAP_MIN_THROW_RATE]
        if swaps:
            swap += 1
            swap_by[(swaps[0]['id'], swaps[0]['infusion'])] += 1
        grip = 'both' if b.get('is2h') else 'one'
        key = (primary['id'], grip)
        if key not in exp_cache:
            try:
                exp_cache[key] = parry_exposure(t, primary['id'], grips=(grip,))['exposure']
            except (SystemExit, KeyError, ZeroDivisionError, TypeError):
                exp_cache[key] = None
        if exp_cache[key] is not None:
            exposure.append(exp_cache[key])
        stats = _build_stats(b)
        best = {}
        for w in right:
            for kind in CRIT_KINDS:
                ck = (w['id'], w['infusion'], grip, kind, tuple(sorted(stats.items())))
                if ck not in crit_cache:
                    try:
                        c = crit_attack(t, w['id'], kind, w['infusion'], None, stats, grip == 'both')
                        crit_cache[ck] = corpus_crit(t, c['hits'], defenders) if c['hits'] else 0.0
                    except (SystemExit, KeyError, StopIteration):
                        crit_cache[ck] = None
                if crit_cache[ck] is not None:
                    best[kind] = max(best.get(kind, 0.0), crit_cache[ck])
        if best:
            crit_rows.append(best)
    n = len(builds)
    cut_share = sum(cut_by.values()) / n if n else 0.0
    cut = crit_cut_rates(t, CRUCIBLE_SCALE_SPEFFECT)
    # The backstab numbers above were taken with no talisman cut; a backstab row always has
    # throwDamageAttribute 1 (section 2), so the corpus share scales all of it.
    bs_scale = sum(1.0 - cut_share + cut_share * cut[el] for el in MV_KEY) / len(MV_KEY)
    rip = [r['riposte'] for r in crit_rows if 'riposte' in r]
    bs = [r['backstab'] * bs_scale for r in crit_rows if 'backstab' in r]
    return {'rl': rl, 'window': window, 'builds': n, 'no_known_right_hand': unknown,
            'parry_tool': tool / n if n else 0.0, 'parry_tool_left': tool_left / n if n else 0.0,
            'parry_by_skill': dict(by_skill.most_common()),
            'crit_cut': cut_share, 'crit_cut_by_talisman': dict(cut_by.most_common()),
            'swap': swap / n if n else 0.0,
            'swap_by_weapon': {f'{t.reg.weapon_names.get(k[0])} ({k[1]})': v
                               for k, v in swap_by.most_common(8)},
            'swap_weapon': swap_by.most_common(1)[0][0] if swap_by else None,
            'exposure': sum(exposure) / len(exposure) if exposure else 0.0,
            'exposure_builds': len(exposure),
            'riposte_hp': sum(rip) / len(rip) if rip else 0.0,
            'backstab_hp': sum(bs) / len(bs) if bs else 0.0, 'crit_builds': len(crit_rows),
            '_defenders': defenders}


def weapon_crit(t, ev, weapon, affinity='Standard', level=None, stats=None, two_handed=False,
                defenders=None):
    """The crit terms of one attacker build, in HP per exchange (crits.md section 7).

        crit_hp   = q_riposte x riposte_eff + BACKSTAB_PER_EXCHANGE x backstab
        q_riposte = parry_tool x PARRY_LAND x exposure        (the attacker parries)
        riposte_eff = (1 - swap) x riposte + swap x max(riposte, swap weapon's riposte)
        parry_hp  = parry_tool x PARRY_LAND x corpus riposte_hp   (per use of a parryable slot)

    `ev` is `crit_evidence`; riposte and backstab are this weapon's crits on the corpus
    (backstab cut by the corpus Crucible share). The swap weapon is the corpus's most carried
    one at the attacker's stats, one-handed."""
    defenders = defenders or ev['_defenders']
    out = {}
    for kind in CRIT_KINDS:
        c = crit_attack(t, weapon, kind, affinity, level, stats, two_handed)
        cut = crit_cut_rates(t, CRUCIBLE_SCALE_SPEFFECT)
        out[kind] = corpus_crit(t, c['hits'], defenders, ev['crit_cut'], cut) if c['hits'] else 0.0
    swap_rip = None
    if ev.get('swap_weapon'):
        swap_id, aff = ev['swap_weapon']
        try:
            c = crit_attack(t, swap_id, 'riposte', aff, None, stats, False)
            swap_rip = corpus_crit(t, c['hits'], defenders)
        except (SystemExit, KeyError):
            swap_rip = None
    rip_eff = out['riposte'] if swap_rip is None else \
        (1.0 - ev['swap']) * out['riposte'] + ev['swap'] * max(out['riposte'], swap_rip)
    q_rip = ev['parry_tool'] * PARRY_LAND * ev['exposure']
    return {'riposte_hp': out['riposte'], 'backstab_hp': out['backstab'], 'swap_riposte_hp': swap_rip,
            'riposte_eff_hp': rip_eff, 'q_riposte': q_rip, 'q_backstab': BACKSTAB_PER_EXCHANGE,
            'crit_hp': q_rip * rip_eff + BACKSTAB_PER_EXCHANGE * out['backstab'],
            'parry_hp': parry_cost(ev)}


def parry_cost(ev):
    """HP a parryable attack is expected to cost per use: a defender carries a tool, lands the
    parry and ripostes for the corpus's mean riposte."""
    return ev['parry_tool'] * PARRY_LAND * ev['riposte_hp']


def implied_parry_land(ev, factor, dmg):
    """The PARRY_LAND an old multiplicative parry factor implies for a slot dealing `dmg`:
    (1 - factor) x dmg = parry_tool x land x riposte_hp."""
    denom = ev['parry_tool'] * ev['riposte_hp']
    return (1.0 - factor) * dmg / denom if denom else None


# ------------------------------------------------------------------ output

def best_reference_stats(t, weapon):
    """(label, stats) of the REF_ATTACKERS line with the highest one-handed AR."""
    base = (t.find_weapon(weapon) // 10000) * 10000
    level = t.ar.max_level(t.ar.weapons[base]['reinforceTypeId'])
    return max(REF_ATTACKERS.items(),
               key=lambda kv: AR.attack_rating(t.ar, base, 'Standard', level, kv[1])['total'])


def table_rows(t, weapons=TABLE_WEAPONS):
    d = reference_defender(t)
    rows = []
    for name in weapons:
        prof = crit_profile(t, name)
        exp = parry_exposure(t, name)
        label, stats = best_reference_stats(t, name)
        row = {'weapon': name, 'stats': label, 'throwAtkRate': prof['throwAtkRate'],
               'bs_mv': prof['backstab']['mv_total'], 'rip_mv': prof['riposte']['mv_total'],
               'bs_hits': len(prof['backstab']['hits']), 'rip_hits': len(prof['riposte']['hits']),
               'exposure': exp['exposure'], 'exposure_1h': exp['exposure_1h'],
               'exposure_2h': exp['exposure_2h']}
        for kind, key in (('backstab', 'bs'), ('riposte', 'rip')):
            for two in (False, True):
                c = crit_damage(t, name, kind, stats=stats, two_handed=two, defender=d)
                row[f'{key}_{"2h" if two else "1h"}'] = round(c['damage'])
        row['ar_1h'] = round(crit_damage(t, name, 'riposte', stats=stats)['ar'])
        rows.append(row)
    return rows


def print_table(rows):
    cols = [('weapon', 26), ('stats', 9), ('throwAtkRate', 5), ('ar_1h', 5), ('bs_mv', 5), ('rip_mv', 6),
            ('bs_1h', 6), ('bs_2h', 6), ('rip_1h', 6), ('rip_2h', 6), ('exposure', 6),
            ('exposure_1h', 6), ('exposure_2h', 6)]
    heads = {'throwAtkRate': 'crit', 'exposure': 'parry', 'exposure_1h': 'p1H', 'exposure_2h': 'p2H'}
    print(' '.join(f'{heads.get(c, c):>{w}}' if i else f'{heads.get(c, c):<{w}}'
                   for i, (c, w) in enumerate(cols)))
    for r in rows:
        print(' '.join(f'{r[c]!s:>{w}}' if i else f'{r[c]!s:<{w}}' for i, (c, w) in enumerate(cols)))
    print(f'RL 150 attacker: the REF_ATTACKERS line named in "stats", Standard, max level; '
          f'defender {REF_DEFENDER} in '
          f'{", ".join(REF_DEFENDER_ARMOR)}; PvP rates on. crit = throwAtkRate (+%).')


def factor_rows(t, ev, weapons=TABLE_WEAPONS):
    """`weapon_crit` per weapon and grip, for the REF_ATTACKERS line with the best 1H AR."""
    rows = []
    for name in weapons:
        label, stats = best_reference_stats(t, name)
        for two in (False, True):
            w = weapon_crit(t, ev, name, stats=stats, two_handed=two)
            rows.append({'weapon': name, 'stats': label, 'grip': '2H' if two else '1H',
                         **{k: (round(v, 4) if k.startswith('q_') else round(v, 1))
                            for k, v in w.items() if v is not None}})
    return rows


def print_factor(ev, rows):
    print(f"RL {ev['rl']} +- {ev['window']}: {ev['builds']} PvP builds (er-builds-pvp corpus)")
    print(f"  parry tool carried {100 * ev['parry_tool']:.1f}% (left hand {100 * ev['parry_tool_left']:.1f}%): "
          + ', '.join(f'{k} {v}' for k, v in ev['parry_by_skill'].items()))
    print(f"  crit swap carried {100 * ev['swap']:.1f}%: "
          + ', '.join(f'{k} {v}' for k, v in ev['swap_by_weapon'].items()))
    print(f"  crit-cut talisman worn {100 * ev['crit_cut']:.1f}%: "
          + ', '.join(f'{k} {v}' for k, v in ev['crit_cut_by_talisman'].items()))
    print(f"  mean parry exposure of the primary weapon {ev['exposure']:.3f} ({ev['exposure_builds']} builds)")
    print(f"  mean own best riposte {ev['riposte_hp']:.0f} HP, backstab {ev['backstab_hp']:.0f} HP "
          f"({ev['crit_builds']} builds)")
    print(f"  weights (INFERRED): PARRY_LAND {PARRY_LAND}, BACKSTAB_PER_EXCHANGE {BACKSTAB_PER_EXCHANGE}; "
          f"parry cost {parry_cost(ev):.1f} HP per parryable use")
    cols = [('weapon', 26), ('stats', 9), ('grip', 4), ('riposte_hp', 7), ('backstab_hp', 7),
            ('swap_riposte_hp', 7), ('riposte_eff_hp', 7), ('crit_hp', 7), ('parry_hp', 7)]
    heads = {'riposte_hp': 'rip', 'backstab_hp': 'bs', 'swap_riposte_hp': 'swapR', 'riposte_eff_hp': 'ripEff',
             'crit_hp': 'critHP', 'parry_hp': 'parryHP'}
    print(' '.join(f'{heads.get(c, c):>{w}}' if i else f'{heads.get(c, c):<{w}}' for i, (c, w) in enumerate(cols)))
    for r in rows:
        print(' '.join(f"{r.get(c, '-')!s:>{w}}" if i else f"{r.get(c, '-')!s:<{w}}" for i, (c, w) in enumerate(cols)))
    print('HP are corpus means (er-builds-pvp Defenders); critHP and parryHP are per exchange / per use.')


#: Mirrors `er-builds-pvp.SCORE_BEST_SLOT_EXCLUDED` and `SCORE_FPS` for `rescore`.
_BEST_EXCLUDED = ('jump_r1', 'jump_r2', 'counter')
_SCORE_FPS = 30.0


def is_integrated(result):
    """True when an `er-builds-pvp.py` result was scored with this factor already: its slot
    scores carry `crit_hp` (and `parry_hp`) and no `f_parry`."""
    return any('crit_hp' in s['score'] for s in result['slots'].values() if s.get('score'))


def rescore(t, pvp_json, window=10):
    """Re-rank an `er-builds-pvp.py --sort score --json` output with the crit factor in place of
    its parry factor, the way section 8's integration would: per slot, the score's numerator
    gains `crit_hp` and loses `parry_hp` when the slot is parryable, and `f_parry` is dropped.
    Returns rows with the old and new best slot and score."""
    with open(pvp_json, encoding='utf-8') as handle:
        data = json.load(handle)
    ev = crit_evidence(t, data['rl'], window)
    rows = []
    for r in data['results']:
        try:
            w = weapon_crit(t, ev, r['weapon'], r['aff'], r['level'], r['stats'], r['two'])
        except (SystemExit, KeyError, StopIteration):
            continue
        scored = [s['score'] for s in r['slots'].values() if s.get('score')]
        if is_integrated(r):
            # Already integrated: the score carries crit_hp / parry_hp and has no f_parry.
            # Nothing to re-rank; report how far the stored terms are from this module's.
            parry = [s['score']['parry_hp'] for s in r['slots'].values()
                     if s.get('score') and s.get('parryable')]
            rows.append({'weapon': r['weapon'], 'grip': '2H' if r['two'] else '1H', 'integrated': True,
                         'crit_hp': w['crit_hp'], 'stored_crit_hp': scored[0]['crit_hp'],
                         'parry_hp': w['parry_hp'], 'stored_parry_hp': parry[0] if parry else None})
            continue
        old = new = None
        for k, s in r['slots'].items():
            sc = s.get('score')
            if not sc or k in _BEST_EXCLUDED:
                continue
            num = sc['rate'] * sc['commit'] / _SCORE_FPS
            num_new = num + w['crit_hp'] - (w['parry_hp'] if s.get('parryable') else 0.0)
            score = sc['score'] / sc.get('f_parry', 1.0) * num_new / num
            old = max(old or (sc['score'], k), (sc['score'], k))
            new = max(new or (score, k), (score, k))
        if old:
            rows.append({'weapon': r['weapon'], 'grip': '2H' if r['two'] else '1H', 'old': old, 'new': new,
                         'crit_hp': w['crit_hp'], 'riposte_hp': w['riposte_hp'], 'parry_hp': w['parry_hp']})
    if any(x.get('integrated') for x in rows):
        return ev, rows
    by_old = sorted(rows, key=lambda x: -x['old'][0])
    for i, x in enumerate(by_old):
        x['old_rank'] = i + 1
    rows = sorted(rows, key=lambda x: -x['new'][0])
    for i, x in enumerate(rows):
        x['new_rank'] = i + 1
    return ev, rows


def print_rescore(ev, rows, top):
    if any(x.get('integrated') for x in rows):
        def gap(key):
            d = [abs(x[key] - x[f'stored_{key}']) for x in rows if x.get(f'stored_{key}') is not None]
            return (max(d), len(d)) if d else (None, 0)
        (dc, nc), (dp, np_) = gap('crit_hp'), gap('parry_hp')
        print(f"this er-builds-pvp output already scores with the crit factor (crit_hp / parry_hp in "
              f"the score, no f_parry); nothing to re-rank. Stored vs this module: crit_hp max "
              f"|diff| {dc if dc is None else round(dc, 3)} over {nc} rows, parry_hp max |diff| "
              f"{dp if dp is None else round(dp, 3)} over {np_} rows.")
        return
    print(f"crit factor on er-builds-pvp scores: crit HP per exchange added, parry cost "
          f"{parry_cost(ev):.1f} HP per parryable use subtracted, f_parry dropped")
    print(f"{'new':>4}{'old':>5}  {'weapon':<32}{'grip':>5}{'old':>7}{'slot':>12}{'new':>7}{'slot':>12}"
          f"{'critHP':>8}{'rip':>7}")
    for x in rows[:top]:
        print(f"{x['new_rank']:>4}{x['old_rank']:>5}  {x['weapon'][:31]:<32}{x['grip']:>5}{x['old'][0]:>7.0f}"
              f"{x['old'][1]:>12}{x['new'][0]:>7.0f}{x['new'][1]:>12}{x['crit_hp']:>8.1f}{x['riposte_hp']:>7.0f}")
    moves = sorted(rows, key=lambda x: -abs(x['old_rank'] - x['new_rank']))[:10]
    print('largest moves: ' + ', '.join(f"{x['weapon']} {x['grip']} {x['old_rank']}->{x['new_rank']}" for x in moves))


def print_weapon(t, name):
    prof = crit_profile(t, name)
    exp = parry_exposure(t, name)
    print(f"{prof['weapon']} ({prof['id']}): throwAtkRate {prof['throwAtkRate']} -> "
          f"x{prof['crit_multiplier']:.2f}")
    for kind in CRIT_KINDS:
        c = prof[kind]
        hits = ', '.join(f"j{h['judge']} row {h['atk_row']} MV {h['mv']['physical']} "
                         f"f{h['frames'][0]}" for h in c['hits'])
        print(f"  {kind:8} {c['anim']} ({c['attacker_frames']} f, victim {c['victim_anim']} "
              f"{c['victim_frames']} f): {hits}; MV total {c['mv_total']}")
    print(f"  parry exposure {exp['exposure']} (1H {exp['exposure_1h']}, 2H {exp['exposure_2h']}; "
          f"classic {exp['classic']}, contact {exp['contact']})")
    for s in exp['detail']:
        print(f"    {s['label']:24} {s['anim']}  JT5 {s['classic_windows'] or '-'}  "
              f"contact {int(s['contact'])}")


# ------------------------------------------------------------------ selftest

def selftest(t=None):
    t = t or Tables()
    failures = []

    def check(name, got, want):
        ok = got == want
        print(f"{'ok  ' if ok else 'FAIL'} {name}: {got!r}" + ('' if ok else f' (want {want!r})'))
        if not ok:
            failures.append(name)

    # Regulation values the doc quotes.
    check('Dagger throwAtkRate', t.weapon[1000000]['throwAtkRate'], 30)
    check('Misericorde throwAtkRate', t.weapon[1030000]['throwAtkRate'], 40)
    check('Greatsword throwAtkRate', t.weapon[4000000]['throwAtkRate'], 0)
    check('backstab ThrowParam anim', t.throw[10000000]['atkAnimId'], 31710)
    check('riposte ThrowParam anim', t.throw[30000000]['atkAnimId'], 31700)
    # TAE import chain and crit judges.
    check('dagger riposte import', import_source(20, 31700), (23, 31700))
    check('crusher backstab import', import_source(31, 31710), (32, 31710))
    check('Dagger backstab', [(h['judge'], h['mv']['physical']) for h in crit_hits(t, 1000000, 'backstab')['hits']],
          [(500, 294)])
    check('Dagger riposte', [(h['judge'], h['mv']['physical']) for h in crit_hits(t, 1000000, 'riposte')['hits']],
          [(510, 420)])
    check('Giant-Crusher riposte', [(h['judge'], h['mv']['physical']) for h in crit_hits(t, 23110000, 'riposte')['hits']],
          [(510, 53), (511, 210)])
    check('Greatsword backstab MV', crit_hits(t, 4000000, 'backstab')['mv_total'], 220)
    check('Uchigatana riposte MV', crit_hits(t, 9000000, 'riposte')['mv_total'], 345)
    # Parry: dagger fully exposed; colossal 1H exposed except jump/counter, 2H never.
    dag = parry_exposure(t, 'Dagger')
    check('Dagger contact', dag['contact'], 1.0)
    check('Dagger jump R1 not parryable', next(s['parryable'] for s in dag['detail'] if s['slot'] == 'jump_r1'), False)
    check('Dagger exposure', dag['exposure'], round(30 / 36, 3))
    gc = parry_exposure(t, 'Giant-Crusher')
    check('Giant-Crusher 2H exposure', gc['exposure_2h'], 0.0)
    check('Giant-Crusher jump R1 classic', next(s['classic'] for s in gc['detail'] if s['slot'] == 'jump_r1'), False)
    check('Giant-Crusher R1 #1 JT5', next(s['classic_windows'] for s in gc['detail'] if s['slot'] == 'r1_1'), [(23, 24)])
    # Roll-then-parry: the Lance's running R2 leaves 3 press frames on its third swing; no Dagger
    # slot swings twice.
    lance = {(f['slot'], f['swing']): f for f in parry_follow_up(t, 'Lance', grips=('one',))}
    check('Lance running R2 swing 3', (lance[('run_r2', 3)]['gap'], lance[('run_r2', 3)]['presses']), (13, 3))
    check('Dagger multi-swing slots', parry_follow_up(t, 'Dagger'), [])
    # Reaction: the Lance's crouch R1 opens JumpTable 5 on frame 12, sooner than any reaction plus
    # the Parry's 4 startup frames; its running R2's window on the third swing (40) is reachable.
    lance = {s['slot']: s for s in parry_exposure(t, 'Lance', grips=('one',))['detail']}
    check('Lance crouch R1 reactable', lance['crouch_r1']['reactable'], 0)
    check('Lance running R2 reactable', lance['run_r2']['reactable'], 1.0)
    # Guard leak through Buckler +25: physical cut 74, lightning 16.
    leak = parry_guard_leak('Lance', {'physical': 500.0})
    check('Lance R1 physical leak', leak['r1_1'], 0.26)
    check('Lance R1 lightning leak', parry_guard_leak('Lance', {'lightning': 500.0})['r1_1'], 0.84)
    # Buckler GuardResist 19 at rate 1.0 (status.md section 1c): 81% of build-up gets through.
    check('Buckler status leak', parry_shield_status_leak(), 0.81)
    check('Lance has no status', status_share(t, 'Lance', 'Standard', None, {'physical': 500.0}), 0.0)
    tools = {x['name']: x for x in parry_tools(t)}
    check('Parry skill category', tools['Parry']['tae_category'], 692)
    check('Parry hitbox a692_040000', tools['Parry']['animations'][0]['parry_hitbox'], [(4, 6, 591)])
    check('Golden Parry judge', {h[2] for a in tools['Golden Parry']['animations'] for h in a['parry_hitbox']},
          {591, 3690})
    # Damage shape: pre-defense attack = AR x MV x (1 + rate/100) x vsPlayer.
    c = crit_damage(t, 'Dagger', 'riposte', stats=REF_ATTACKER, pvp=False)
    check('Dagger riposte attack shape', round(c['attack'], 3), round(c['ar'] * 4.20 * 1.30, 3))
    check('Dagger Talisman crit-only', t.speffect[DAGGER_TALISMAN_SPEFFECT]['throwAttackParamChange'], 1)
    ct = crit_damage(t, 'Dagger', 'riposte', stats=REF_ATTACKER, pvp=False,
                     speffects=(DAGGER_TALISMAN_SPEFFECT,))
    check('Dagger Talisman x1.17', round(ct['attack'] / c['attack'], 4), 1.17)
    # Crucible Scale: stateInfo 335, cuts backstab rows (throwDamageAttribute 1) and not ripostes.
    check('crit-cut talismans', {k: v for k, v in t.crit_cut_talismans.items()},
          {'Crucible Scale Talisman': 340600, 'Talisman of All Crucibles': 20382402})
    check('Crucible Scale rates', {round(v, 4) for v in crit_cut_rates(t, CRUCIBLE_SCALE_SPEFFECT).values()},
          {0.7})
    check('crit victim SpEffects carry no crit cut', [s for s in (90, 9641, 19385) if s in t.crit_cut], [])
    check('Dagger backstab / riposte crit_cut', ([h['crit_cut'] for h in crit_attack(t, 'Dagger', 'backstab')['hits']],
                                                 [h['crit_cut'] for h in crit_attack(t, 'Dagger', 'riposte')['hits']]),
          ([True], [False]))
    d = reference_defender(t)
    for kind, want in (('backstab', 0.7), ('riposte', 1.0)):
        plain = crit_damage(t, 'Dagger', kind, stats=REF_ATTACKER, defender=d)['damage']
        cut = crit_damage(t, 'Dagger', kind, stats=REF_ATTACKER, defender=d,
                          defender_speffects=(CRUCIBLE_SCALE_SPEFFECT,))['damage']
        check(f'Crucible Scale on a Dagger {kind}', round(cut / plain, 4), want)
    # The corpus path equals the scalar defense path on a corpus of identical defenders.
    P = _pvp_module()
    one = {'defenses': {el: 110 for el in MV_KEY}, 'absorption': {k: 25.0 for k in P.ABSORB_KEY.values()},
           'poise': {'original': 60}}
    dfs = P.Defenders([one, one])
    for name, kind in (('Greatsword', 'riposte'), ('Dagger', 'backstab')):
        c = crit_attack(t, name, kind, stats=REF_ATTACKER)
        scalar = crit_damage(t, name, kind, stats=REF_ATTACKER, defender=dfs.median)['damage']
        check(f'{name} {kind}: corpus mean equals scalar damage', round(corpus_crit(t, c['hits'], dfs), 3),
              round(scalar, 3))
    # The factor is the formula it states.
    ev = {'parry_tool': 0.5, 'exposure': 0.8, 'swap': 0.25, 'swap_weapon': (1030000, 'Standard'),
          'crit_cut': 0.0, 'riposte_hp': 1000.0, '_defenders': dfs}
    w = weapon_crit(t, ev, 'Greatsword', stats=REF_ATTACKERS['strength'])
    eff = 0.75 * w['riposte_hp'] + 0.25 * max(w['riposte_hp'], w['swap_riposte_hp'])
    check('weapon_crit crit_hp', round(w['crit_hp'], 6), round(
        0.5 * PARRY_LAND * 0.8 * eff + BACKSTAB_PER_EXCHANGE * w['backstab_hp'], 6))
    check('parry cost', round(w['parry_hp'], 6), round(0.5 * PARRY_LAND * 1000.0, 6))
    check('implied PARRY_LAND of a 0.85 factor at 600 HP', round(implied_parry_land(ev, 0.85, 600.0), 4), 0.18)
    check('rescore detects integrated output', (
        is_integrated({'slots': {'r1_1': {'score': {'score': 1.0, 'crit_hp': 40.0, 'parry_hp': 0.0}}}}),
        is_integrated({'slots': {'r1_1': {'score': {'score': 1.0, 'f_parry': 0.85}}, 'x': {'score': None}}})),
        (True, False))
    # Same builds as er-builds-pvp's corpus.
    check('window builds = er-builds-pvp corpus', len(window_builds(150, 10)),
          len(P.pvp_corpus(P.Path(CORPUS), 140, 160)))
    print('selftest:', 'FAIL ' + ', '.join(failures) if failures else 'all passed')
    return not failures


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('weapons', nargs='*')
    ap.add_argument('--table', action='store_true', help='the RL 150 comparison table')
    ap.add_argument('--parry-tools', action='store_true')
    ap.add_argument('--corpus', action='store_true', help='parry-skill share in STR PvP builds')
    ap.add_argument('--factor', action='store_true',
                    help='crit factor: corpus evidence of the RL window, then per weapon')
    ap.add_argument('--rescore', metavar='PVP_JSON',
                    help='re-rank an er-builds-pvp.py --sort score --json output with the crit factor')
    ap.add_argument('--top', type=int, default=30)
    ap.add_argument('--rl', type=int, default=150)
    ap.add_argument('--window', type=int, default=10)
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    t = Tables()
    if a.selftest:
        return 0 if selftest(t) else 1
    if a.rescore:
        ev, rows = rescore(t, a.rescore, a.window)
        if a.json:
            print(json.dumps(rows, indent=1))
        else:
            print_rescore(ev, rows, a.top)
        return 0
    if a.factor:
        ev = crit_evidence(t, a.rl, a.window)
        rows = factor_rows(t, ev, a.weapons or TABLE_WEAPONS)
        if a.json:
            print(json.dumps({'evidence': {k: v for k, v in ev.items() if not k.startswith('_')},
                              'weapons': rows}, indent=1, default=str))
        else:
            print_factor(ev, rows)
        return 0
    if a.table:
        rows = table_rows(t, a.weapons or TABLE_WEAPONS)
        print(json.dumps(rows, indent=1)) if a.json else print_table(rows)
    if a.parry_tools:
        tools = parry_tools(t)
        if a.json:
            print(json.dumps(tools, indent=1))
        else:
            for x in tools:
                print(f"{x['name']} ({x['sword_art']}, a{x['tae_category']}): default on "
                      f"{', '.join(x['default_on']) or '-'}")
                for an in x['animations']:
                    print(f"  {an['anim']} parry hitbox {an['parry_hitbox']} force-parry "
                          f"{an['force_parry_mode']} length {an['frames']} f")
    if a.corpus:
        print(json.dumps(corpus_parry_share(t), indent=1))
    if a.weapons and not a.table:
        for name in a.weapons:
            if a.json:
                print(json.dumps({'crits': crit_profile(t, name),
                                  'parry': parry_exposure(t, name)}, indent=1, default=str))
            else:
                print_weapon(t, name)
    return 0


if __name__ == '__main__':
    sys.exit(main())
