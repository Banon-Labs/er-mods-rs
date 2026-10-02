#!/usr/bin/env python3
"""Weapon <-> talisman affinity from the game's mechanics: how a weapon's moveset engages each
talisman's trigger.

The usage half of this association is `scripts/er-builds-embed.py pairs "<weapon>"` (what
planner builds wear together). This is the mechanics half. A talisman relates to a weapon
through its condition, not through an unconditional multiplier: Claw Talisman needs jump attacks,
Winged Sword Insignia needs successive hits, Lord of Blood's Exultation needs blood loss. For each
base weapon and each talisman this emits the facts that decide how often the weapon meets the
talisman's condition. Nothing here launches the game; everything is regulation data, TimeAct
events or code read in the executable.

    python3 scripts/er-mechanics-talisman-affinity.py --weapon "Backhand Blade"
    python3 scripts/er-mechanics-talisman-affinity.py --weapon 64500000 --json
    python3 scripts/er-mechanics-talisman-affinity.py --json --out matrix.json     # every weapon
    python3 scripts/er-mechanics-talisman-affinity.py --talismans                   # trigger kinds
    python3 scripts/er-mechanics-talisman-affinity.py --selftest

Labels as in `docs/er-mechanics/`: `VERIFIED` = regulation value, TimeAct event, or code read in
the named 1.16.2 Ghidra dump and byte-checked in `eldenring-deobf-1.17.1.bin`; `INFERRED` = fits
the data but the consumer was not traced; `COMMUNITY` = outside claim. A feature whose basis is
not established is emitted as `unknown` with the reason, never filled in.

Trigger kinds (from each talisman's SpEffect closure, `er-mechanics-talismans.py`):

    subcategory   magicSubCategoryChange1..3 against AtkParam_Pc.subCategory1..4 of each hit
                  (`CheckMagicSubCategoryChangeMask`, talismans.md 2a). Measured as the share of
                  the moveset's slots and hit records whose row carries one of the values, per
                  grip, and the share of the default skill's damaging rows (111 / 112 live there).
    successive    the successive-hit counter (below). Measured as counter gain per slot, per R1
                  string and per second, the host decay, and the cold-start time to the first
                  threshold.
    counter_hit   Spear Talisman (stateInfo 197). Counter rows 31 / 45 raise only
                  thrustDamageCutRate, so it can only act on pierce hits: measured as the pierce
                  share of the moveset (talismans.md 2c).
    critical      throwAttackParamChange (Dagger Talisman): every weapon can land a critical; the
                  weapon-dependent fact is its EquipParamWeapon.throwAtkRate.
    status        exultations: a presence gate whose presence a status proc is taken to fire
                  (`INFERRED`, talismans.md 3b). Measured as the statuses the weapon itself
                  builds (`VERIFIED` rows) and their per-hit base build-up.
    presence_tae  any other presence gate (Rellana's Cameo 502/504, Godfrey Icon 497): whether the
                  weapon's own TimeAct (moveset category or default skill) applies an SpEffect
                  carrying that stateInfo.
    element       unconditional rows that touch a subset of elements (scorpion charms): the
                  weapon's base attack share in those elements.
    independent   no weapon-dependent condition (HP gates, resources, defense, always-on).

The successive-hit counter (`VERIFIED`, addresses 1.16.2 / 1.17.1, byte-checked by --selftest):

    per landed hit   CalculateDamage2 0x1404483b0 / 0x140448910 walks the 13 SpEffect slots of
                     the AttackDamageInfo (`mov ebx,0xd` 0x1404489eb / 0x140448f4b). Slots 0-4 are
                     AtkParam_Pc.spEffectId0..4 (copied in FUN_140d24440 0x140d244b0 / 0x140d25c30,
                     skipped when disableHitSpEffect). A slot whose SpEffectParam has
                     effectTargetAttacker (+0x160 bit 1, 0x140448aed / 0x14044904d) is applied to
                     the attacker (FUN_1403e8b70 -> Apply -> FUN_1404fd090).
    one entry each   FUN_1404fc0c0 reuses an existing entry with the same id only through
                     FUN_140500510 0x140500510 / 0x1405012e0, which refuses an entry with
                     controlFlags & 2, the flag a zero-duration row gets at creation
                     (FUN_1404ff010). The 314 rows (6900-6909) have effectEndurance 0, spCategory 0
                     and stateInfo 314, so no other match applies: every landed hit adds its own
                     entry, even two in one frame.
    counted once     FUN_1404fae40 (the per-frame SpEffect update) ticks every entry, then calls
                     FUN_1404fe4e0, which adds to every accumulator whose host stateInfo
                     (303 + id) is present the sum FUN_1404f70e0 0x1404f70e0 / 0x1404f7eb0 of
                     accumuVal (+0x184) over entries with stateInfo 314 (+0x156) activated this
                     update (flags & 4). An unconditional new entry starts in state 2
                     (IsHpDependend, inverted), is activated on its first tick and removed on the
                     next (FUN_140500be0, controlFlags & 2): one count per entry.
    decay            a host row's ActivateInterval 0x1405011c0 adds its own accumuVal every
                     motionInterval (Winged Sword / Millicent's: -1 per 0.5 s; Godskin: -1 per
                     0.8 s). Active stage rows also decay (-1 per 1.0 / 0.7 / 0.4 s). The counter
                     is clamped at 0 (FUN_1404fe170) and has no upper clamp in FUN_1404fe4e0.

So every hit record that lands counts, including each blade of a multi-hitbox swing: Backhand
Blade's 2H R1 #1 is two hit records (AtkParam 6400200 index 0, 6400203 index 1), each carrying
spEffectId0 6902 (+6), so one swing that lands both adds 12. Which hit records land is
geometry; the counts here are the ceiling the TimeAct allows (`max_hits`, attacks.md), with the
`INFERRED` sweep count beside it.

Not established, so not modelled (reported as unknown): how the stage rows (spCategory 120 for
stages 1-3, 20 for stage 4) interact once more than one threshold is met, and the order of the
four host rows in the entry list, which decides which fire id the single per-accumulator queue
slot (FUN_1404fe450) holds. Stage 2+ timing therefore is not computed; stage 1 from a cold start
is, because no stage row exists before it. Which machine runs CalculateDamage2 for a hit on a
remote player in online play was not traced.
"""
import argparse
import collections
import importlib.util
import json
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _mod(name, fname):
    s = importlib.util.spec_from_file_location(name, os.path.join(_HERE, fname))
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


PR = _mod('er_param_read', 'er-param-read.py')
TAL = _mod('er_mechanics_talismans', 'er-mechanics-talismans.py')
ST = _mod('er_mechanics_status', 'er-mechanics-status.py')
_LAZY = {}


def _ash_mod():
    if 'ash' not in _LAZY:
        _LAZY['ash'] = _mod('er_mechanics_ashes', 'er-mechanics-ashes.py')
    return _LAZY['ash']


FPS = 30
ELEMENTS = TAL.ELEMENTS
#: SpEffectParam.stateInfo of the rows AtkParam hits add to the successive-hit counter
#: (FUN_1404f70e0 compares +0x156 with 0x13a).
ACCUM_ADD_STATE_INFO = 314
#: Accumulator host stateInfo range (`IsStateInfoFrom303To312` 0x140d50770); accumulator id is
#: stateInfo - 303 (FUN_140d4fd80).
ACCUM_HOST_STATE_INFO = range(303, 313)
#: Spear Talisman's stateInfo (talismans.md 2c).
COUNTER_BOOST_STATE_INFO = 197
#: stateInfo values whose rows are switched on by a critical, a kill or a spirit death, the same
#: for every weapon (talismans.md 3b).
WEAPON_INDEPENDENT_EVENTS = {199: 'rune award', 288: 'critical rows (all weapons)',
                             289: 'critical rows (all weapons)', 475: 'spirit death',
                             483: 'critical rows (all weapons)'}
#: Presence gates (invocationConditionsStateChange) that the wearer's own state or every weapon
#: meets, so they say nothing about the weapon (talismans.md 3b, 3c, 4).
NON_WEAPON_GATES = {288: 'critical rows of every weapon', 289: 'critical rows of every weapon',
                    483: 'critical rows of every weapon', 2: 'status received by the wearer',
                    5: 'status received by the wearer', 6: 'status received by the wearer',
                    260: 'status received by the wearer', 436: 'status received by the wearer',
                    437: 'status received by the wearer', 466: 'crouching',
                    496: 'flask animation'}
AFFINITIES = ST.AR.AFFINITIES

# Byte checks of the executable sites the successive-counter answer rests on. (label, 1.16.2 VA,
# 1.17.1 VA, bytes). Each sequence is position-independent and identical in both images.
EXE_SITES = [
    ('FUN_1404f70e0: sum accumuVal of activated stateInfo 314 entries', 0x1404f70e0, 0x1404f7eb0,
     '33c048ba00000000000000f0488551287440488b49084885c97437' '41b83a0100008b5160f7c203000c80751df6c2047418'
     '488b114885d274106644398256010000750603828401' '0000488b49304885c975cfc3'),
    ('FUN_1404fe4e0 calls the sum', 0x1404fe50a, 0x1404ff2da, 'e8d18bffff'),
    ('CalculateDamage2: 13 SpEffect slots from AttackDamageInfo+0x74', 0x1404489d2, 0x140448f32,
     '4d8d6e74f2410f108e680100000f57c0f3410f10b644020000bb0d000000'),
    ('CalculateDamage2: effectTargetAttacker (+0x160 bit 1) -> applied to the attacker',
     0x140448aed, 0x14044904d, '0fb68160010000d1e883e00184c00f84010200004885f6'),
    ('FUN_140d24440: slots 0-4 = AtkParam+0x18 (spEffectId0..4)', 0x140d244b0, 0x140d25c30,
     '488d44245848c7442458ffffffff482bd0'),
    ('FUN_140d24440: indexed read [paramRow + 0x18 + 4i]', 0x140d24500, 0x140d25c80,
     '4883fb04770a488d049a8b4c0470'),
    ('FUN_140500510: no reuse of an entry with controlFlags & 2', 0x140500510, 0x1405012e0,
     '8b4160c1e81ff6d0a801750332c0c3f641700275f7'),
]


def _find_image(name, env):
    if os.environ.get(env):
        return os.environ[env]
    d = os.path.dirname(_HERE)
    while True:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
        up = os.path.dirname(d)
        if up == d:
            return None
        d = up


def _image_bytes(path, va, n):
    with open(path, 'rb') as f:
        f.seek(va - 0x140000000)
        return f.read(n)


# ------------------------------------------------------------------------------------------
# Tables


class Data:
    """Every table the features read, decoded once."""

    def __init__(self, regulation=None, affinity='Standard'):
        ASH = _ash_mod()
        self.ASH = ASH
        self.ash = ASH.AshTables(regulation)
        self.reg = self.ash.reg
        self.ATT = ASH.ATK
        self.tal = TAL.Talismans(regulation)
        self.sp = self.tal.sp
        files = PR.load(regulation)

        def table(stem, fields):
            rows, _, _ = PR.rows(PR.param_bytes(files, stem), fields)
            return {r['id']: r for r in rows}

        self.atk = table('AtkParam_Pc', ['disableHitSpEffect'] + [f'spEffectId{i}' for i in range(5)]
                         + [f'subCategory{i}' for i in range(1, 5)])
        self.wep = table('EquipParamWeapon', ['throwAtkRate', 'attackBasePhysics', 'attackBaseMagic',
                                              'attackBaseFire', 'attackBaseThunder', 'attackBaseDark',
                                              'swordArtsParamId', 'reinforceTypeId', 'wepmotionCategory']
                         + [f'spEffectBehaviorId{k}' for k in range(3)])
        self.reinforce = table('ReinforceParamWeapon', ['spEffectId1', 'spEffectId2', 'spEffectId3'])
        self.affinity = affinity
        self.triggers = {t.name: talisman_triggers(self, t) for t in self.tal}
        self._tae_sp = {}

    # -- per AtkParam row ------------------------------------------------------------------
    def counter_gain(self, atk_row):
        """Successive-counter gain one landed hit of `atk_row` adds (see module doc)."""
        a = self.atk.get(atk_row)
        if not a or a['disableHitSpEffect']:
            return 0
        g = 0
        for i in range(5):
            sid = a[f'spEffectId{i}']
            r = self.sp.get(sid) if sid > 0 else None
            if r and r['stateInfo'] == ACCUM_ADD_STATE_INFO and r['effectTargetAttacker']:
                g += r['accumuVal']
        return g

    def subcats(self, atk_row):
        a = self.atk.get(atk_row)
        if not a:
            return ()
        return tuple(v for v in (a['subCategory1'], a['subCategory2'], a['subCategory3'],
                                 a['subCategory4']) if v)

    def row_statuses(self, sid):
        r = self.sp.get(sid)
        if not r or ST.hostile_target_refusal(r):
            return None
        return ST.row_status(r)

    # -- per weapon ------------------------------------------------------------------------
    def weapon_name(self, wid):
        return self.reg.weapon_names.get(wid)

    def find_weapon(self, key):
        return self.ash.find_weapon(key)

    def tae_speffect_states(self, category):
        """{stateInfo: [(anim, SpEffect id)]} applied by TAE events 66/67/401/331 in one category."""
        if category not in self._tae_sp:
            out = collections.defaultdict(list)
            anims = self.ATT.tae_animations(category) or {}
            for anim, events in anims.items():
                for e in events:
                    ids = []
                    if e.type in self.ASH.EV_SPEFFECT and len(e.params) >= 4:
                        ids = [struct.unpack_from('<i', e.params, 0)[0]]
                    elif e.type == self.ASH.EV_WA_SPEFFECT and len(e.params) >= 8:
                        ids = list(struct.unpack_from('<ii', e.params, 0))
                    for sid in ids:
                        r = self.sp.get(sid) if sid > 0 else None
                        if r and r['stateInfo']:
                            out[r['stateInfo']].append((anim, sid))
            self._tae_sp[category] = dict(out)
        return self._tae_sp[category]


# ------------------------------------------------------------------------------------------
# Talisman triggers


def _touches_attack(f):
    return (any(TAL.ATTACK_RATE[e] in f for e in ELEMENTS) or any(k in f for k in TAL.POWER_RATE)
            or any(f'atkPlayerDmgCorrectRate_{TAL.EL_SUFFIX[e]}' in f for e in ELEMENTS)
            or any(f'atkEnemyDmgCorrectRate_{TAL.EL_SUFFIX[e]}' in f for e in ELEMENTS)
            or 'staminaAttackRate' in f)


def _elements_touched(f):
    out = set()
    for e in ELEMENTS:
        for k in (TAL.ATTACK_RATE[e], f'atkPlayerDmgCorrectRate_{TAL.EL_SUFFIX[e]}',
                  f'atkEnemyDmgCorrectRate_{TAL.EL_SUFFIX[e]}'):
            if k in f and abs(f[k] - 1.0) > 1e-6:
                out.add(e)
    return out


def talisman_triggers(d, t):
    """The weapon-dependent trigger kinds of one talisman, from its SpEffect closure.

    Returns {'kinds': {kind: spec}, 'independent': [reasons]}. Every spec carries `label`."""
    kinds = {}
    indep = []
    full = d.sp
    exult = ST.EXULTATIONS.get(t.name)
    for s in t.speffects:
        f = s.fields
        r = full.get(s.id, {})
        si = r.get('stateInfo', 0)
        gates = [r.get(f'invocationConditionsStateChange{i}', 0) for i in (1, 2, 3)]
        gates = [g for g in gates if g]
        if si in ACCUM_HOST_STATE_INFO and r.get('accumuOverVal', -1) > 0:
            k = kinds.setdefault('successive', {'accumulator': si - 303, 'hosts': [], 'label':
                                                'VERIFIED (exe + regulation, module doc)'})
            k['hosts'].append({'row': s.id, 'threshold': r['accumuOverVal'],
                               'fires': r['accumuOverFireId'], 'accumuVal': r['accumuVal'],
                               'motionInterval': round(r['motionInterval'], 4)})
            continue
        if si in ACCUM_HOST_STATE_INFO:
            continue                                    # stage rows: values, not a trigger
        if si in WEAPON_INDEPENDENT_EVENTS:
            indep.append(f'{s.id}: {WEAPON_INDEPENDENT_EVENTS[si]}')
            continue
        if si == COUNTER_BOOST_STATE_INFO:
            kinds['counter_hit'] = {'row': s.id, 'label': 'VERIFIED (talismans.md 2c)'}
            continue
        if r.get('throwAttackParamChange'):
            kinds['critical'] = {'row': s.id, 'label': 'VERIFIED throw gate (talismans.md 2c)'}
            continue
        if gates and exult:
            kinds['status'] = {'statuses': list(exult[2]), 'presence_state_info': gates,
                               'label': 'VERIFIED presence gate; that a proc of these statuses '
                                        'fires the presence is INFERRED (madness / sleep '
                                        'pairings COMMUNITY)'}
            continue
        if gates and all(g in NON_WEAPON_GATES for g in gates):
            indep.append(f'{s.id}: ' + ', '.join(sorted({NON_WEAPON_GATES[g] for g in gates})))
            continue
        if gates:
            k = kinds.setdefault('presence_tae', {'state_info': [], 'rows': [], 'label':
                                                  'VERIFIED TAE (events 66/67/401/331)'})
            k['state_info'] = sorted(set(k['state_info']) | set(gates))
            k['rows'].append(s.id)
            continue
        if s.id in TAL.TIMED and s.subcats():
            indep.append(f'{s.id}: timed row, trigger {"not found" if s.id == 19991 else "not weapon-linked"}')
            continue
        if s.subcats() and _touches_attack(f):
            k = kinds.setdefault('subcategory', {'values': [], 'rows': [], 'label':
                                                 'VERIFIED (subcategory mask, talismans.md 2a)'})
            k['values'] = sorted(set(k['values']) | set(s.subcats()))
            k['rows'].append(s.id)
            continue
        if _touches_attack(f):
            if (r.get('conditionHp', -1) >= 0 or r.get('conditionHpRate', -1) >= 0):
                indep.append(f'{s.id}: HP gate')
                continue
            if s.id in TAL.TIMED or s.id in TAL.STAGE_OF:
                continue
            if si in (315, 316):
                indep.append(f'{s.id}: equipped weight, not the weapon moveset')
                continue
            els = _elements_touched(f)
            if els and els != set(ELEMENTS):
                kinds['element'] = {'elements': sorted(els), 'row': s.id, 'label': 'VERIFIED regulation'}
            else:
                indep.append(f'{s.id}: unconditional on every weapon hit')
    return {'kinds': kinds, 'independent': indep if not kinds else []}


# ------------------------------------------------------------------------------------------
# Weapon profile


def _hits_of(d, wid, r):
    """Every hit record of one slot row: own windows and other hitboxes."""
    out = []
    for w in r.get('hit_window_detail', []):
        out.append({'judge': r['judge'], 'atk_row': r['atk_row'], 'frames': tuple(w['frames']),
                    'records': w['hits'], 'sweep': w['hits'] if w['sweep_hit'] else 0,
                    'phys_type': r['phys_type']})
    for o in r.get('other_hitboxes', []):
        n = d.ATT.attack_numbers(d.reg, wid, o['judge'])
        out.append({'judge': o['judge'], 'atk_row': n['atk_row'] if n else None,
                    'frames': tuple(o['frames']), 'records': o['hits'],
                    'sweep': o['hits'] if o['sweep_hit'] else 0,
                    'phys_type': n['phys_type'] if n else None})
    for h in out:
        h['gain'] = d.counter_gain(h['atk_row']) if h['atk_row'] else 0
        h['subcats'] = d.subcats(h['atk_row']) if h['atk_row'] else ()
    return out


def grip_profile(d, wid, grip):
    rows = d.ATT.weapon_attacks(d.reg, wid, grip)
    slots = []
    for r in rows:
        hits = _hits_of(d, wid, r)
        slots.append({'slot': r['slot'], 'label': r['label'], 'atk_row': r['atk_row'],
                      'hits': hits, 'cancel_r1': (r.get('cancel_frame') or {}).get('r1'),
                      'records': sum(h['records'] for h in hits),
                      'sweep': sum(h['sweep'] for h in hits),
                      'counter_gain': sum(h['gain'] * h['records'] for h in hits),
                      'counter_gain_sweep': sum(h['gain'] * h['sweep'] for h in hits)})
    prefix = '2h_' if grip == 'both' else ''
    by = {s['slot']: s for s in slots}
    chain = []
    for k in range(1, 7):
        s = by.get(f'{prefix}r1_{k}')
        if s is None:
            break
        chain.append(s)
    return {'grip': grip, 'slots': slots, 'r1_chain': chain}


def skill_rows(d, wid):
    """Damaging AtkParam rows of the weapon's default skill, and its TAE SpEffect stateInfos."""
    ASH = d.ASH
    sid = d.wep[wid]['swordArtsParamId']
    if sid is None or sid < 0 or sid not in d.ash.arts:
        return {'sword_arts_id': sid, 'name': None, 'rows': [], 'state_info': {}}
    anims = ASH.skill_tae(d.ash, sid) or {}
    twins = {a for a, ev in anims.items() if a % 10 >= 5 and a - 5 in anims
             and not any(e.type == ASH.EV_FP for e in ev)}
    rows = []

    def walk(bt):
        if not bt:
            return
        if any(bt['mv'].values()) or (bt['add_base_atk'] and any(bt['flat'].values())):
            rows.append(bt['atk_row'])
        for c in bt.get('children', {}).values():
            walk(c)

    states = collections.defaultdict(list)
    for a in sorted(anims):
        if a in twins:
            continue
        for e in anims[a]:
            p = e.params
            if e.type == ASH.EV_ATTACK:
                if struct.unpack_from('<i', p, 0)[0] == ASH.ATTACK_TYPE_PARRY:
                    continue
                res = ASH.resolve_judge(d.ash, wid, struct.unpack_from('<i', p, 8)[0])
                if res.get('kind') == 'melee':
                    rows.append(res['atk_row'])
            elif e.type == ASH.EV_BULLET:
                res = ASH.resolve_judge(d.ash, wid, struct.unpack_from('<i', p, 8)[0], via_bullet=True)
                if res.get('kind') == 'bullet':
                    walk(res.get('bullet'))
            elif e.type == ASH.EV_PC_BEHAVIOR:
                flags, judge = struct.unpack_from('<Ii', p, 4)
                if flags & 8:
                    res = ASH.resolve_judge(d.ash, wid, judge, event_flags=flags)
                    if res.get('kind') == 'melee':
                        rows.append(res['atk_row'])
            ids = []
            if e.type in ASH.EV_SPEFFECT and len(p) >= 4:
                ids = [struct.unpack_from('<i', p, 0)[0]]
            elif e.type == ASH.EV_WA_SPEFFECT and len(p) >= 8:
                ids = list(struct.unpack_from('<ii', p, 0))
            for x in ids:
                r = d.sp.get(x) if x > 0 else None
                if r and r['stateInfo']:
                    states[r['stateInfo']].append((a, x))
    return {'sword_arts_id': sid, 'name': d.ash.arts_name(sid), 'rows': rows, 'state_info': dict(states)}


def weapon_statuses(d, wid, grips):
    """Statuses the weapon builds: its own hit slots 5-7 (affinity row, +0) and the AtkParam
    spEffectId0..4 rows of its moveset. Base build-up per hit, before arcane / requirement."""
    base = wid
    aff = AFFINITIES.index(d.affinity) if d.affinity in AFFINITIES else 0
    row_id = base + aff * 100
    w = d.wep.get(row_id)
    out = {}
    if w:
        rf = d.reinforce.get(w['reinforceTypeId'], {})
        for k in range(3):
            sid = w[f'spEffectBehaviorId{k}'] + rf.get(f'spEffectId{k + 1}', 0)
            st = d.row_statuses(sid) if sid > 0 else None
            if st:
                e = out.setdefault(st[0], {'weapon_row': 0, 'atk_rows': {}})
                e['weapon_row'] += st[1]
                e['source'] = f'EquipParamWeapon {row_id} spEffectBehaviorId{k} -> {sid}'
    for g in grips.values():
        for s in g['slots']:
            for h in s['hits']:
                a = d.atk.get(h['atk_row']) if h['atk_row'] else None
                if not a or a['disableHitSpEffect']:
                    continue
                for i in range(5):
                    sid = a[f'spEffectId{i}']
                    st = d.row_statuses(sid) if sid > 0 else None
                    if st:
                        e = out.setdefault(st[0], {'weapon_row': 0, 'atk_rows': {}})
                        e['atk_rows'][h['atk_row']] = st[1]
    return {'affinity': d.affinity, 'row': row_id, 'statuses': out}


def weapon_profile(d, wid):
    grips = {g: grip_profile(d, wid, g) for g in ('one', 'both')}
    w = d.wep[wid]
    return {'id': wid, 'name': d.weapon_name(wid), 'grips': grips, 'skill': skill_rows(d, wid),
            'status': weapon_statuses(d, wid, grips), 'throwAtkRate': w['throwAtkRate'],
            'base_attack': {'physical': w['attackBasePhysics'], 'magic': w['attackBaseMagic'],
                            'fire': w['attackBaseFire'], 'lightning': w['attackBaseThunder'],
                            'holy': w['attackBaseDark']},
            'motion_category': w['wepmotionCategory']}


# ------------------------------------------------------------------------------------------
# Successive counter: cold start to a threshold


def cold_start(chain, threshold, decay_val, decay_s, sweep=False, max_s=30.0):
    """First time the counter reaches `threshold` while the R1 chain is repeated at its earliest
    inputs and every hit record lands at its window's first frame (the ceiling).

    The host's decay tick phase relative to the first swing is not fixed by anything the player
    controls, so every phase is tried and (min, max) seconds and hits are returned. No stage row
    exists before the first threshold, so the host decay is the only one (module doc)."""
    if not chain or decay_s <= 0:
        return None
    period = sum(s['cancel_r1'] or 0 for s in chain) if all(s['cancel_r1'] for s in chain) else None
    events = collections.defaultdict(int)
    hits_at = collections.defaultdict(int)
    offset, horizon = 0.0, max_s * FPS
    while offset < horizon:
        o = offset
        for s in chain:
            for h in s['hits']:
                n = h['sweep'] if sweep else h['records']
                if n and h['gain']:
                    fr = int(round(o + h['frames'][0]))
                    events[fr] += h['gain'] * n
                    hits_at[fr] += n
            o += s['cancel_r1'] or 0
        if period is None or period <= 0:
            break
        offset += period
    step = decay_s * FPS
    results = []
    for phase10 in range(int(step * 10)):
        phase = phase10 / 10.0
        c, hits, next_decay = 0, 0, phase
        for fr in range(int(horizon) + 1):
            while next_decay <= fr:
                c = max(0, c + decay_val)
                next_decay += step
            c += events.get(fr, 0)
            hits += hits_at.get(fr, 0)
            if c >= threshold:
                results.append((fr / FPS, hits))
                break
        else:
            results.append(None)
    if any(r is None for r in results):
        reached = [r for r in results if r]
        return {'reached': 'some phases' if reached else 'never', 'within_s': max_s,
                **({'seconds': [min(r[0] for r in reached), max(r[0] for r in reached)]} if reached else {})}
    return {'reached': 'always', 'seconds': [round(min(r[0] for r in results), 3), round(max(r[0] for r in results), 3)],
            'hits': [min(r[1] for r in results), max(r[1] for r in results)]}


# ------------------------------------------------------------------------------------------
# Features


def _share(num, den):
    return round(num / den, 4) if den else None


def subcategory_features(prof, values):
    vals = set(values)
    out = {}
    for g, gp in prof['grips'].items():
        slots = gp['slots']
        m_slots = [s['slot'] for s in slots if any(vals & set(h['subcats']) for h in s['hits'])]
        rec = sum(s['records'] for s in slots)
        m_rec = sum(h['records'] for s in slots for h in s['hits'] if vals & set(h['subcats']))
        out[g] = {'slots': len(slots), 'matched_slots': m_slots,
                  'slot_share': _share(len(m_slots), len(slots)),
                  'hit_record_share': _share(m_rec, rec)}
    rows = prof['skill']['rows']
    m = [r for r in rows if vals & set(_SUBCATS_CACHE.get(r, ()))]
    out['skill'] = {'sword_arts_id': prof['skill']['sword_arts_id'], 'name': prof['skill']['name'],
                    'damaging_rows': len(rows), 'matched_rows': len(m),
                    'row_share': _share(len(m), len(rows))}
    return out


_SUBCATS_CACHE = {}


def successive_features(prof, spec):
    hosts = sorted(spec['hosts'], key=lambda h: h['threshold'])
    decays = [h for h in hosts if h['accumuVal'] < 0 and h['motionInterval'] > 0]
    out = {'thresholds': [h['threshold'] for h in hosts],
           'host_decay': [{'row': h['row'], 'per_s': round(-h['accumuVal'] / h['motionInterval'], 4)}
                          for h in decays]}
    decay_per_s = sum(x['per_s'] for x in out['host_decay'])
    for g, gp in prof['grips'].items():
        chain = gp['r1_chain']
        gain = sum(s['counter_gain'] for s in chain)
        gain_sw = sum(s['counter_gain_sweep'] for s in chain)
        period = sum(s['cancel_r1'] for s in chain) if chain and all(s['cancel_r1'] for s in chain) else None
        per_s = round(gain / (period / FPS), 3) if period else None
        first = hosts[0] if hosts else None
        cs = cs_sw = None
        if first and decays:
            cs = cold_start(chain, first['threshold'], decays[0]['accumuVal'], decays[0]['motionInterval'])
            cs_sw = cold_start(chain, first['threshold'], decays[0]['accumuVal'], decays[0]['motionInterval'],
                               sweep=True)
        out[g] = {'slot_gain': {s['slot']: s['counter_gain'] for s in gp['slots']},
                  'r1_chain': [s['slot'] for s in chain],
                  'r1_string_gain': gain, 'r1_string_gain_sweep': gain_sw,
                  'r1_string_records': sum(s['records'] for s in chain),
                  'r1_period_frames': round(period, 2) if period else None,
                  'gain_per_s': per_s,
                  'net_per_s_upper': round(per_s - decay_per_s, 3) if per_s is not None else None,
                  'cold_start_first_threshold': cs,
                  'cold_start_first_threshold_sweep': cs_sw}
    out['unknown'] = ('stage 2+ timing: stage-row coexistence (spCategory 120 / 20) and host order '
                      'in the entry list not traced')
    return out


def counter_hit_features(prof):
    out = {}
    for g, gp in prof['grips'].items():
        rec = sum(s['records'] for s in gp['slots'])
        pier = sum(h['records'] for s in gp['slots'] for h in s['hits'] if h['phys_type'] == 'pierce')
        out[g] = {'pierce_slots': [s['slot'] for s in gp['slots']
                                   if any(h['phys_type'] == 'pierce' for h in s['hits'])],
                  'pierce_hit_record_share': _share(pier, rec)}
    return out


def status_features(prof, statuses):
    have = prof['status']['statuses']
    out = {}
    for st in statuses:
        e = have.get(st)
        out[st] = None if not e else {'weapon_row_buildup': e['weapon_row'],
                                      'atk_rows': len(e['atk_rows']),
                                      'atk_row_buildup_max': max(e['atk_rows'].values(), default=0)}
    out['builds_any'] = any(v for k, v in out.items() if k != 'builds_any')
    out['affinity'] = prof['status']['affinity']
    return out


def presence_features(d, prof, spec):
    found = {}
    moveset = d.tae_speffect_states(prof['motion_category'])
    for si in spec['state_info']:
        hits = [('skill', a, x) for a, x in prof['skill']['state_info'].get(si, [])]
        hits += [('moveset', a, x) for a, x in moveset.get(si, [])]
        found[si] = sorted(set(hits))[:6]
    return {'state_info': spec['state_info'], 'applied_by_weapon': any(found.values()),
            'events': {k: [list(x) for x in v] for k, v in found.items()},
            'scope': 'default skill TAE and the moveset category TAE (imports not followed)'}


def element_features(prof, spec):
    ba = prof['base_attack']
    tot = sum(ba.values())
    return {'elements': spec['elements'],
            'base_attack_share': _share(sum(ba[e] for e in spec['elements']), tot)}


def features(d, prof):
    for gp in prof['grips'].values():
        for s in gp['slots']:
            for h in s['hits']:
                if h['atk_row']:
                    _SUBCATS_CACHE[h['atk_row']] = h['subcats']
    for r in prof['skill']['rows']:
        _SUBCATS_CACHE[r] = d.subcats(r)
    out = {}
    for name, trig in d.triggers.items():
        if not trig['kinds']:
            continue
        f = {}
        for kind, spec in trig['kinds'].items():
            if kind == 'subcategory':
                f[kind] = subcategory_features(prof, spec['values'])
            elif kind == 'successive':
                f[kind] = successive_features(prof, spec)
            elif kind == 'counter_hit':
                f[kind] = counter_hit_features(prof)
            elif kind == 'critical':
                f[kind] = {'throwAtkRate': prof['throwAtkRate']}
            elif kind == 'status':
                f[kind] = status_features(prof, spec['statuses'])
            elif kind == 'presence_tae':
                f[kind] = presence_features(d, prof, spec)
            elif kind == 'element':
                f[kind] = element_features(prof, spec)
            f[kind]['label'] = spec['label']
        out[name] = f
    return out


def flat_vector(feat):
    """One numeric row: (column, value) pairs, None where a value is undefined."""
    cols = []
    for name in sorted(feat):
        for kind, f in sorted(feat[name].items()):
            p = f'{name}|{kind}'
            if kind == 'subcategory':
                for g in ('one', 'both'):
                    cols.append((f'{p}|{g}|slot_share', f[g]['slot_share']))
                    cols.append((f'{p}|{g}|hit_record_share', f[g]['hit_record_share']))
                cols.append((f'{p}|skill|row_share', f['skill']['row_share']))
            elif kind == 'successive':
                for g in ('one', 'both'):
                    cols.append((f'{p}|{g}|r1_string_gain', f[g]['r1_string_gain']))
                    cols.append((f'{p}|{g}|gain_per_s', f[g]['gain_per_s']))
                    cs = f[g]['cold_start_first_threshold']
                    cols.append((f'{p}|{g}|first_threshold_s_max',
                                 cs['seconds'][1] if cs and cs.get('reached') == 'always' else None))
            elif kind == 'counter_hit':
                for g in ('one', 'both'):
                    cols.append((f'{p}|{g}|pierce_hit_record_share', f[g]['pierce_hit_record_share']))
            elif kind == 'critical':
                cols.append((f'{p}|throwAtkRate', f['throwAtkRate']))
            elif kind == 'status':
                cols.append((f'{p}|builds_any', int(bool(f['builds_any']))))
            elif kind == 'presence_tae':
                cols.append((f'{p}|applied_by_weapon', int(f['applied_by_weapon'])))
            elif kind == 'element':
                cols.append((f'{p}|base_attack_share', f['base_attack_share']))
    return cols


# ------------------------------------------------------------------------------------------
# Output


def base_weapons(d):
    out = []
    for wid, w in d.reg.weapon.items():
        if wid % 10000:
            continue
        nm = d.weapon_name(wid)
        if not nm or nm.startswith('[') or wid not in d.wep:
            continue
        # wepType 0 is a consumable's virtual weapon (pots, throwing knives, stones); 81-86 are
        # arrows, greatarrows, bolts and greatbolts.
        if w.get('wepType') in (0, 81, 83, 85, 86):
            continue
        out.append(wid)
    return sorted(out)


def _fmt_cs(cs):
    if not cs:
        return 'n/a'
    if cs.get('reached') != 'always':
        return f"{cs['reached']} within {cs['within_s']:g}s"
    a, b = cs['seconds']
    return f"{a:.2f}-{b:.2f}s, {cs['hits'][0]}-{cs['hits'][1]} hits"


def print_weapon(d, prof, feat):
    print(f"{prof['name']} ({prof['id']})  default skill: {prof['skill']['name']}  "
          f"status ({prof['status']['affinity']}): "
          f"{', '.join(prof['status']['statuses']) or 'none'}  throwAtkRate {prof['throwAtkRate']}")
    for g, gp in prof['grips'].items():
        print(f"  {g}-handed slots: " + ', '.join(
            f"{s['slot']}:{s['records']}h/+{s['counter_gain']}" for s in gp['slots']))
    print()
    print(f"{'talisman':34} {'kind':12} feature (1H | 2H)")
    for name in sorted(feat):
        for kind, f in feat[name].items():
            if kind == 'subcategory':
                vals = d.triggers[name]['kinds'][kind]['values']
                txt = (f"{{{','.join(map(str, vals))}}} slots {f['one']['slot_share']} | {f['both']['slot_share']}"
                       f"  hits {f['one']['hit_record_share']} | {f['both']['hit_record_share']}"
                       f"  skill rows {f['skill']['matched_rows']}/{f['skill']['damaging_rows']}")
            elif kind == 'successive':
                txt = (f"R1 string +{f['one']['r1_string_gain']} | +{f['both']['r1_string_gain']}"
                       f"  /s {f['one']['gain_per_s']} | {f['both']['gain_per_s']}"
                       f"  (decay {sum(x['per_s'] for x in f['host_decay']):g}/s)"
                       f"  to {f['thresholds'][0]}: {_fmt_cs(f['one']['cold_start_first_threshold'])}"
                       f" | {_fmt_cs(f['both']['cold_start_first_threshold'])}")
            elif kind == 'counter_hit':
                txt = f"pierce hit share {f['one']['pierce_hit_record_share']} | {f['both']['pierce_hit_record_share']}"
            elif kind == 'critical':
                txt = f"throwAtkRate {f['throwAtkRate']}"
            elif kind == 'status':
                txt = ', '.join(f"{k} {'row ' + str(v['weapon_row_buildup']) if v['weapon_row_buildup'] else ''}"
                                f"{' atk rows ' + str(v['atk_rows']) if v['atk_rows'] else ''}".strip()
                                if v else f'{k} none' for k, v in f.items()
                                if k not in ('builds_any', 'affinity', 'label'))
            elif kind == 'presence_tae':
                txt = f"stateInfo {f['state_info']} applied by weapon TAE: {f['applied_by_weapon']}"
            elif kind == 'element':
                txt = f"{'/'.join(f['elements'])} base attack share {f['base_attack_share']}"
            else:
                txt = ''
            print(f"{name[:34]:34} {kind:12} {txt}")
    print()
    print('Counts are the TimeAct ceiling (every hit record lands); labels per kind in --json.')


def talisman_table(d):
    rows = []
    for t in d.tal:
        trig = d.triggers[t.name]
        if trig['kinds']:
            for k, spec in trig['kinds'].items():
                detail = {kk: vv for kk, vv in spec.items() if kk != 'label'}
                rows.append((t.name, k, json.dumps(detail, default=str)[:110], spec['label']))
    return rows


# ------------------------------------------------------------------------------------------
# Selftest


def selftest():
    ok = fails = 0
    skips = []

    def check(name, got, want):
        nonlocal ok, fails
        if got == want:
            ok += 1
        else:
            fails += 1
            print(f'FAIL {name}: got {got!r}, want {want!r}')

    # 1. Executable sites, both images.
    for build, fname, env, col in (('1.16.2', 'eldenring-deobf.bin', 'ER_DEOBF_1162', 1),
                                   ('1.17.1', 'eldenring-deobf-1.17.1.bin', 'ER_DEOBF_1171', 2)):
        path = _find_image(fname, env)
        if not path:
            skips.append(f'{build} image {fname} absent')
            continue
        for site in EXE_SITES:
            want = bytes.fromhex(site[3])
            check(f'{build} {site[0]} at {site[col]:#x}', _image_bytes(path, site[col], len(want)).hex(),
                  want.hex())
    d = Data()
    # 2. The 314 adder rows: on the attacker, zero duration, accumuVal 2..20 (regulation).
    adders = {i: r for i, r in d.sp.items() if r['stateInfo'] == ACCUM_ADD_STATE_INFO}
    check('stateInfo 314 rows are 6900-6909', sorted(adders), list(range(6900, 6910)))
    check('314 rows target the attacker', all(r['effectTargetAttacker'] for r in adders.values()), True)
    check('314 rows are zero-duration', all(r['effectEndurance'] == 0 for r in adders.values()), True)
    check('314 rows accumuVal', [adders[i]['accumuVal'] for i in sorted(adders)], list(range(2, 21, 2)))
    # 3. Talisman thresholds and decay from regulation.
    for name, want_thr, want_decay in (("Winged Sword Insignia", [17, 30, 45, 60], 2.0),
                                       ("Rotten Winged Sword Insignia", [17, 30, 45, 60], 2.0),
                                       ("Millicent's Prosthesis", [17, 30, 45, 60], 2.0),
                                       ("Godskin Swaddling Cloth", [32], 1.25)):
        spec = d.triggers[name]['kinds'].get('successive')
        check(f'{name} is successive', spec is not None, True)
        if spec:
            check(f'{name} thresholds', sorted(h['threshold'] for h in spec['hosts']), want_thr)
            dec = sum(-h['accumuVal'] / h['motionInterval'] for h in spec['hosts']
                      if h['accumuVal'] < 0 and h['motionInterval'] > 0)
            check(f'{name} host decay /s', round(dec, 4), want_decay)
    # 4. Trigger kinds of a few talismans.
    for name, kind in (('Claw Talisman', 'subcategory'), ('Two-Handed Sword Talisman', 'subcategory'),
                       ('Shard of Alexander', 'subcategory'), ('Spear Talisman', 'counter_hit'),
                       ('Dagger Talisman', 'critical'), ("Lord of Blood's Exultation", 'status'),
                       ("Rellana's Cameo", 'presence_tae'), ('Magic Scorpion Charm', 'element')):
        check(f'{name} kind {kind}', kind in d.triggers[name]['kinds'], True)
    for name in ('Ritual Sword Talisman', "Bull-Goat's Talisman", 'Blade of Mercy'):
        check(f'{name} weapon-independent', d.triggers[name]['kinds'], {})
    # 5. Backhand Blade: the multi-hitbox 2H R1 #1 counts both blades.
    bb = d.find_weapon('Backhand Blade')
    check('Backhand Blade id', bb, 64500000)
    prof = weapon_profile(d, bb)
    s = next(x for x in prof['grips']['both']['slots'] if x['slot'] == '2h_r1_1')
    check('BB 2H R1 #1 hit rows', sorted(h['atk_row'] for h in s['hits']), [6400200, 6400203])
    check('BB 2H R1 #1 gains', [h['gain'] for h in s['hits']], [6, 6])
    check('BB 2H R1 #1 counter gain', s['counter_gain'], 12)
    feat = features(d, prof)
    claw = feat['Claw Talisman']['subcategory']
    check('BB Claw matches the jump slots (2H)', sorted(claw['both']['matched_slots']),
          ['2h_jump_r1', '2h_jump_r2'])
    two = feat['Two-Handed Sword Talisman']['subcategory']
    check('BB Two-Handed Sword matches no 1H slot', two['one']['matched_slots'], [])
    # 6. A status weapon: Rivers of Blood builds bleed.
    rob = d.find_weapon('Rivers of Blood')
    rp = weapon_profile(d, rob)
    check('Rivers of Blood builds bleed', 'bleed' in rp['status']['statuses'], True)
    # 7. cold_start on a synthetic chain: +8 per hit every 10 frames, decay -1 / 15 frames.
    chain = [{'hits': [{'frames': (5, 8), 'records': 1, 'sweep': 1, 'gain': 8}], 'cancel_r1': 10}]
    cs = cold_start(chain, 17, -1, 0.5, max_s=5)
    check('synthetic cold start reaches 17', cs['reached'], 'always')
    check('synthetic cold start hits', cs['hits'], [3, 3])
    print(f'{ok} passed, {fails} failed' + (f"; skipped: {'; '.join(skips)}" if skips else ''))
    return 1 if fails else 0


# ------------------------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--weapon', help='weapon name or EquipParamWeapon id: one weapon view')
    ap.add_argument('--affinity', default='Standard', help='affinity for the weapon status rows')
    ap.add_argument('--json', action='store_true', help='JSON (all weapons unless --weapon)')
    ap.add_argument('--out', help='write the JSON here instead of stdout')
    ap.add_argument('--talismans', action='store_true', help='list each talisman trigger kind')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    d = Data(affinity=a.affinity)
    if a.talismans:
        for name, kind, detail, label in talisman_table(d):
            print(f'{name[:34]:34} {kind:12} {detail}  [{label}]')
        indep = sorted(t.name for t in d.tal if not d.triggers[t.name]['kinds'])
        print(f'\n{len(indep)} talismans have no weapon-dependent trigger.')
        return 0
    if a.weapon:
        wid = d.find_weapon(a.weapon)
        prof = weapon_profile(d, wid)
        feat = features(d, prof)
        if a.json:
            out = {'weapon': {'id': wid, 'name': prof['name']}, 'features': feat,
                   'vector': dict(flat_vector(feat))}
            text = json.dumps(out, indent=1, default=str)
            if a.out:
                with open(a.out, 'w') as f:
                    f.write(text)
            else:
                print(text)
        else:
            print_weapon(d, prof, feat)
        return 0
    weapons = base_weapons(d)
    rows, columns, failed = [], None, []
    out_weapons = []
    for wid in weapons:
        try:
            prof = weapon_profile(d, wid)
        except (KeyError, TypeError, ValueError) as e:
            failed.append({'id': wid, 'name': d.weapon_name(wid), 'error': repr(e)})
            continue
        if not any(gp['slots'] for gp in prof['grips'].values()):
            continue
        feat = features(d, prof)
        vec = flat_vector(feat)
        if columns is None:
            columns = [c for c, _ in vec]
        rows.append([v for _, v in vec])
        out_weapons.append({'id': wid, 'name': prof['name'], 'features': feat})
    out = {'about': __doc__.split('\n\n')[0],
           'talismans': {t.name: {'kinds': {k: {kk: vv for kk, vv in s.items()}
                                            for k, s in d.triggers[t.name]['kinds'].items()},
                                  'independent': d.triggers[t.name]['independent']} for t in d.tal},
           'columns': columns, 'matrix': {'weapons': [w['id'] for w in out_weapons], 'rows': rows},
           'weapons': out_weapons, 'failed': failed}
    text = json.dumps(out, default=str)
    if a.out:
        with open(a.out, 'w') as f:
            f.write(text)
        print(f'{len(out_weapons)} weapons, {len(columns or [])} columns, {len(failed)} failed -> {a.out}')
    else:
        print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
