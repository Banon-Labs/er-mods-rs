#!/usr/bin/env python3
"""Gear that has synergy with a weapon: talismans, armor and other weapons whose effects the
weapon's own moveset engages, net of what the gear costs.

    python3 scripts/er-mechanics-gear-synergy.py Lance
    python3 scripts/er-mechanics-gear-synergy.py Lance --json
    python3 scripts/er-mechanics-gear-synergy.py --selftest

Weapon and gear are vectors over one set of channels, and synergy is their dot product minus
the gear's cost. Nothing comes from which items builds happen to wear together
(`er-builds-embed.py pairs` answers that question); everything is regulation data and the
weapon's own TimeAct, through `er-mechanics-talisman-affinity.py`'s weapon profile.

Channels, each with how the weapon side is measured:

| channel | gear row that feeds it | weapon engagement, 0..1 |
| --- | --- | --- |
| `move:<subcategory>` | attack rate gated on an AtkParam subcategory (Claw Talisman: jump, Retaliatory Crossed-Tree: roll) | percentile, among every base weapon whose slot carries the subcategory, of the slot's motion value per frame to its first hit; the weapon's slot must carry the subcategory itself (the row is read, not the slot name) |
| `move:120` two-handed | Two-Handed Sword Talisman | share of the two-handed slots whose rows carry 120 |
| `move:112` skill | Shard of Alexander, Warrior Jar Shard | share of the built-in skill's damaging rows that carry it, only for a weapon that takes no ash (any ash weapon fires a skill) |
| `element:<e>` | an ungated attack rate on some elements only (scorpion charms) | the element's share of the base row's attack |
| `status:<s>` | a row gated on a status-presence stateInfo (Lord of Blood's Exultation, White Mask, Mushroom Crown) | percentile of the weapon's own per-hit build-up among every weapon whose base row builds the status |
| `pierce` | the counter-hit row (Spear Talisman) | pierce share of the moveset's hit records |
| `successive` | successive-hit stage rows (Winged Sword Insignia) | percentile, among every base weapon, of the counter its R1 string adds per second net of the host's decay, in its faster grip (multi-hitbox swings count each landed record) |
| `stat:<STR..>` | ungated attribute points (Millicent's Prosthesis DEX, Silver Tear Mask ARC) | attack gained from those points at every attribute 40, as a fraction |

The weapon is read as it comes, before any ash or infusion. Gear an infusion or an ash would
make work (a Blood Lance and Lord of Blood's Exultation) works the same on every weapon that
takes them, so it says nothing about this one and is not listed.

Setup discount (user directive 2026-10-02: "diminish the value of any attack that requires a
prior input, such as jump, or backstep"). An attack thrown out of a roll, backstep, crouch or
jump spends frames before its own swing in which the attacker can be hit, and a neutral attack
spends none. Each move channel's weapon weight is multiplied by

    discount = startup / (startup + exposed)

where `startup` is the attack's own first-hit frame on its own clock and `exposed` is the frames
of the prior input in which the attacker is open to a hit. The ratio is the share of the
commitment that is the attack itself: an entry as long as the swing's own startup halves the
channel, an entry of zero leaves it whole, and it never goes negative or above one. Every frame
is read from TAE at run time through the sibling tools, medium load like the sweep builds:

| family | slots | exposed |
| --- | --- | --- |
| neutral | R1/R2 strings, the chain final hit (104), charged heavies (100), two-handed (120), skill (112) | 0: the charge is the attack's own startup |
| run | `run_r1`, `run_r2` | the sprint loop `a000_020200`'s first R1 input-and-cancel frame; the loop opens none, because HKS picks the running attack from `MoveSpeedIndex` (`er-builds-pvp`), so 0 |
| roll | `roll_r1` | the medium roll `a000_027110`'s first R1 frame less its unconditional JumpTable 8 i-frames from frame 0 (`er-mechanics-ashes.evasion_motion`) |
| backstep | `bstep_r1` | the medium backstep `a000_027000`'s first R1 frame less its i-frames; its only JumpTable 8 is gated on the Fine Crucible Feather stateInfo, so every frame counts |
| crouch | `crouch_r1` | the standing crouch `a000_390000`'s first R1 frame, which has no i-frames |
| jump | `jump_r1`, `jump_r2` | every frame from the input of the N jump (`a000_202010`, standing with the stick forward) to the first hit, the press frame (`er-mechanics-jump.takeoff`) included: `er-mechanics-jump.sequence`'s `first_hit`, with the swing's own clock as `startup` |

A channel fed by several slots (`move:121` is the rolling, backstep and crouch R1) has its weight
split evenly over the weapon's slots that carry the subcategory, each part discounted by its own
family, so the channel's discount is the mean of the parts. The time a player must hold the dodge
button before the engine sets the sprint index is not traced and is not counted.

Gear benefit is the damage multiplier minus one (x1.2 -> 0.2), the player-damage correction
included. Rows behind an HP threshold, a timer, a kill, a flask or another weapon-independent gate
are not synergy: every weapon gets them equally.

Critical-damage rows (`throwAttackParamChange`, the Dagger Talisman's x1.17) are not scored
either, but they get their own block, "critical gear", on a weapon whose critical is higher than
normal: its throwAtkRate (the (throwAtkRate + 100) * 0.01 crit factor, Misericorde 40 against a
Longsword's 0) above the median of every base weapon that can crit (enableThrow), with its rank
among them. A weapon at or below the median prints nothing; --json carries the figure, the rank
and the gear under `critical` either way.

Weapons also pair with each other: a second weapon the behavior script lets the first
powerstance with (`er-mechanics-powerstance-guard.can_powerstance`) unlocks the dual L1 moves,
listed with their first hit frame and motion value from `er-mechanics-moveset.dual_attacks`.
These are moves rather than a multiplier, so they are reported beside the ranked gear, not
scored against it. Horseback (101) is left out because PvP has no
horse, and guard counters (103) because blocking is out of scope for these views.

Cost is survivability, the one downside every item can be put in the same unit as damage: the
extra damage a player takes, from a damage-taken or absorption row, divided by the max HP the item
leaves (Fire Scorpion Charm: physical damage taken x1.1). For armor it also includes the
absorption given up against the best piece of the same slot at the same weight or lighter, so a
piece whose bonus comes with paper defense is pushed down. Other downsides (stamina, FP, flask
healing, resistance) are listed beside the row but not netted, since nothing in the game puts
them in damage units.
"""
import argparse
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _mod(name, fname):
    s = importlib.util.spec_from_file_location(name, os.path.join(HERE, fname))
    m = importlib.util.module_from_spec(s)
    sys.modules[name] = m
    s.loader.exec_module(m)
    return m


AFF = _mod('er_mechanics_talisman_affinity', 'er-mechanics-talisman-affinity.py')
TAL, ST, PR = AFF.TAL, AFF.ST, AFF.PR
AR = ST.AR
ELEMENTS = TAL.ELEMENTS
EL_SUFFIX = TAL.EL_SUFFIX
SKIP_SUBCATS = {101: 'horseback: no horse in PvP', 103: 'guard counter: blocking is out of scope'}
# Moves whose quality is a slot of the moveset, by the slot names `weapon_attacks` uses.
MOVE_SLOTS = {100: ('r2_1c', 'r2_2c'), 102: ('jump_r1', 'jump_r2'), 104: ('r1_4', 'r1_5', 'r1_6'),
              121: ('roll_r1', 'bstep_r1', 'crouch_r1'), 122: ('run_r1', 'run_r2'), 127: ()}
STATS = [('addStrengthStatus', 'STR', 'str'), ('addDexterityStatus', 'DEX', 'dex'),
         ('addMagicStatus', 'INT', 'int'), ('addFaithStatus', 'FTH', 'fth'), ('addLuckStatus', 'ARC', 'arc')]
REFERENCE_STAT = 40
#: The a000 clips the prior-input families start from (module docstring, setup discount).
SETUP_CLIPS = {'run': 20200, 'roll': 27110, 'backstep': 27000, 'crouch': 390000}
#: Family of each slot a move channel reads; a slot not named is neutral.
SLOT_FAMILY = {'run_r1': 'run', 'run_r2': 'run', 'roll_r1': 'roll', 'bstep_r1': 'backstep',
               'crouch_r1': 'crouch', 'jump_r1': 'jump', 'jump_r2': 'jump'}
#: The jump the jump slots are timed from: the N jump, as `er-mechanics-interrupt.jump_entry`.
JUMP_KIND = 'n'
_SETUP = {}
COST_TEXT = {'maxStaminaRate': ('max stamina', 1), 'maxMpRate': ('max FP', 1),
             'changeHpEstusFlaskCorrectRate': ('Crimson flask healing', 1),
             'changeMpEstusFlaskCorrectRate': ('Cerulean flask FP', 1)}
RESIST = ('changePoisonResistPoint', 'changeDiseaseResistPoint', 'changeBloodResistPoint',
          'changeFreezeResistPoint', 'changeSleepResistPoint', 'changeMadnessResistPoint')


def _f(s, k, d):
    return s.fields.get(k, d)


def status_presence(d):
    """{presence stateInfo: (statuses)} from the exultation talismans' own gates."""
    out = {}
    for name, (_, _, statuses) in ST.EXULTATIONS.items():
        tal = d.tal.get(name)
        for s in tal.speffects if tal else ():
            for i in (1, 2, 3):
                g = _f(s, f'invocationConditionsStateChange{i}', 0)
                if g:
                    out[g] = tuple(statuses)
    return out


class Gear:
    """Every talisman, armor piece and weapon with an effect, as channel benefits and costs."""

    def __init__(self, d):
        self.d = d
        presence = status_presence(d)
        self.items = {}
        for tal in d.tal:
            self.items[tal.name] = self._item('talisman', tal.speffects, presence)
        files = PR.load()
        prot = PR.rows(PR.param_bytes(files, 'EquipParamProtector'), None, strict=False)[0]
        pn = PR.row_names('EquipParamProtector')
        self.armor_cost = self._armor_cost(prot)
        for r in prot:
            nm = pn.get(r['id'])
            ids = [r['residentSpEffectId'], r['residentSpEffectId2'], r['residentSpEffectId3']]
            if not nm or nm in self.items or not any(i > 0 for i in ids):
                continue
            item = self._item('armor', self._closure(ids), presence)
            item['cost']['armor_absorption'] = self.armor_cost.get(r['id'], 0.0)
            self.items[nm] = item
        wep = PR.rows(PR.param_bytes(files, 'EquipParamWeapon'),
                      ['residentSpEffectId', 'residentSpEffectId1', 'residentSpEffectId2'], strict=False)[0]
        for r in wep:
            nm = d.reg.weapon_names.get(r['id'])
            ids = [r['residentSpEffectId'], r['residentSpEffectId1'], r['residentSpEffectId2']]
            if r['id'] % 10000 or not nm or nm.startswith('[') or nm in self.items or not any(i > 0 for i in ids):
                continue
            self.items[nm] = self._item('weapon', self._closure(ids), presence)

    def _closure(self, ids):
        fake = {'refId': -1, 'residentSpEffectId1': ids[0], 'residentSpEffectId2': ids[1],
                'residentSpEffectId3': ids[2], 'residentSpEffectId4': -1}
        return self.d.tal._closure(fake, [])

    @staticmethod
    def _armor_cost(prot):
        """Armor id -> damage taken more than the best piece of its slot at its weight or lighter."""
        by_slot = {}
        for r in prot:
            by_slot.setdefault(r['protectorCategory'], []).append(r)
        out = {}
        for rows in by_slot.values():
            for r in rows:
                cut = r['neutralDamageCutRate']
                best = min((o['neutralDamageCutRate'] for o in rows if o['weight'] <= r['weight']), default=cut)
                out[r['id']] = max(0.0, cut / best - 1) if best > 0 else 0.0
        return out

    def _item(self, kind, speffects, presence):
        benefits, cost, other, critical = [], {'damage_taken': 1.0, 'max_hp': 1.0}, [], []
        # A presence gate on a row holds for the rows it links to: Lord of Blood's Exultation
        # waits on stateInfo 379 in 321600 and does its damage in 321601, the buff 321600 cycles.
        inherited = {s.id: set() for s in speffects}
        for s in speffects:
            own = {s.fields.get(f'invocationConditionsStateChange{i}', 0) for i in (1, 2, 3)} - {0}
            inherited[s.id] |= own
            for child in s.links.values():
                if child in inherited:
                    inherited[child] |= inherited[s.id]
        for s in speffects:
            f = s.fields
            presence_gate = inherited[s.id] and all(g in presence for g in inherited[s.id])
            gated = (f.get('conditionHp', -1) >= 0 or f.get('conditionHpRate', -1) >= 0
                     or (s.id in TAL.TIMED and not presence_gate)
                     or s.via in ('applyIdOnGetSoul', 'spiritDeathSpEffectId'))
            mult = {}
            for e in ELEMENTS:
                v = f.get(TAL.ATTACK_RATE[e], 1.0) * f.get('atkPlayerDmgCorrectRate_' + EL_SUFFIX[e], 1.0)
                if e == 'physical':
                    v *= f.get('physicsAttackPowerRate', 1.0)
                if abs(v - 1.0) > 1e-6:
                    mult[e] = v
            si = f.get('stateInfo', 0)
            gates = sorted(inherited[s.id])
            if mult and not gated:
                gain = max(mult.values()) - 1
                if si == AFF.COUNTER_BOOST_STATE_INFO:
                    benefits.append({'channel': 'pierce', 'gain': gain, 'mult': mult, 'row': s.id})
                elif f.get('throwAttackParamChange'):
                    # Crit-only (`er-mechanics-crits`): reported in its own block, not scored.
                    if gain > 0:
                        critical.append({'gain': gain, 'mult': mult, 'row': s.id})
                elif s.id in TAL.STAGE_OF:
                    if TAL.STAGE_OF[s.id] == 1:
                        benefits.append({'channel': 'successive', 'gain': gain, 'mult': mult, 'row': s.id})
                elif s.subcats():
                    for sc in s.subcats():
                        if sc not in SKIP_SUBCATS:
                            benefits.append({'channel': f'move:{sc}', 'gain': gain, 'mult': mult, 'row': s.id})
                elif gates and all(g in presence for g in gates):
                    for st in {x for g in gates for x in presence[g]}:
                        benefits.append({'channel': f'status:{st}', 'gain': gain, 'mult': mult, 'row': s.id})
                elif not gates and si in (0,) and len(mult) < len(ELEMENTS) and gain > 0:
                    for e, v in mult.items():
                        benefits.append({'channel': f'element:{e}', 'gain': v - 1, 'row': s.id})
            if not gated and not gates and not s.subcats():
                for k, lab, key in STATS:
                    if f.get(k):
                        benefits.append({'channel': f'stat:{lab}', 'points': f[k], 'key': key, 'row': s.id})
                taken = [f.get('defPlayerDmgCorrectRate_' + EL_SUFFIX['physical'], 1.0),
                         f.get('neutralDamageCutRate', 1.0)]
                cost['damage_taken'] *= taken[0] * taken[1]
                cost['max_hp'] *= f.get('maxHpRate', 1.0)
                for k, (lab, _) in COST_TEXT.items():
                    if f.get(k, 1.0) < 1.0:
                        other.append(f'{lab} x{f[k]:.3g}')
                if any(f.get(k, 0) < 0 for k in RESIST):
                    other.append('lower status resistance')
        return {'kind': kind, 'benefits': benefits, 'cost': cost, 'other_costs': sorted(set(other)),
                'critical': critical}


def survivability_cost(item):
    c = item['cost']
    return (c['damage_taken'] / max(c['max_hp'], 1e-6) - 1) + c.get('armor_absorption', 0.0)


class Population:
    """Per-slot motion value per frame and per-weapon measures across every base weapon, for
    percentiles. Built once per process."""

    def __init__(self, d):
        self.d = d
        self.slot_q = {}
        self.status = {}
        self.succ = []
        files = PR.load()
        self.can_crit = {r['id'] for r in PR.rows(PR.param_bytes(files, 'EquipParamWeapon'), ['enableThrow'],
                                                  strict=False)[0] if r['enableThrow']}
        #: throwAtkRate of every base weapon that can crit: the population "higher than normal" is
        #: read against. Bows, catalysts, shields, torches and whips have enableThrow 0.
        self.crit = [d.wep[w]['throwAtkRate'] for w in AFF.base_weapons(d) if w in self.can_crit]
        for wid in AFF.base_weapons(d):
            for st, v in innate_row_statuses(d, wid).items():
                self.status.setdefault(st, []).append(v)
            s = successive(d, wid)
            if s:
                self.succ.append(s['net_per_s'])
            try:
                rows = d.ATT.weapon_attacks(d.reg, wid, 'one')
            except Exception:                           # weapon rows the attack module cannot read
                continue
            for r in rows:
                q = slot_quality(r)
                if q:
                    self.slot_q.setdefault(r['slot'], []).append(q)

    @staticmethod
    def pct(values, v):
        if not values:
            return 0.0
        return sum(1 for x in values if x <= v) / len(values)


#: The talisman whose counter defines the successive channel. Winged Sword Insignia, its rotten
#: version and Millicent's Prosthesis share its thresholds 17/30/45/60 and its 2-per-second host
#: decay (`er-mechanics-talisman-affinity --selftest` checks all three).
SUCCESSIVE_REFERENCE = 'Rotten Winged Sword Insignia'


def successive(d, wid, prof=None):
    """How fast the weapon's R1 string fills the successive-hit counter, in the grip that fills
    it faster: counter per second net of the decay, and the seconds and hits from an empty
    counter to the first threshold. None when the string adds nothing."""
    spec = d.triggers[SUCCESSIVE_REFERENCE]['kinds']['successive']
    try:
        f = AFF.successive_features(prof or AFF.weapon_profile(d, wid), spec)
    except Exception:                                   # weapon rows the attack module cannot read
        return None
    best = None
    for grip in ('one', 'both'):
        g = f.get(grip) or {}
        net = g.get('net_per_s_upper')
        if net is None or g.get('r1_string_gain', 0) <= 0:
            continue
        if best is None or net > best['net_per_s']:
            cs = g.get('cold_start_first_threshold') or {}
            best = {'grip': grip, 'net_per_s': net, 'gain_per_s': g['gain_per_s'],
                    'threshold': spec['hosts'] and min(h['threshold'] for h in spec['hosts']),
                    'reached': cs.get('reached'), 'seconds': (cs.get('seconds') or [None])[0],
                    'hits': (cs.get('hits') or [None])[0]}
    return best


def innate_row_statuses(d, wid):
    """{status: build-up per hit} the weapon's own row carries at +0, before any infusion: the
    spEffectBehaviorId slots of the base row (`er-mechanics-talisman-affinity.weapon_statuses`)."""
    w = d.wep.get(wid)
    out = {}
    if not w:
        return out
    rf = d.reinforce.get(w['reinforceTypeId'], {})
    for k in range(3):
        sid = w[f'spEffectBehaviorId{k}'] + rf.get(f'spEffectId{k + 1}', 0)
        st = d.row_statuses(sid) if w[f'spEffectBehaviorId{k}'] > 0 else None
        if st:
            out[st[0]] = out.get(st[0], 0) + st[1]
    return out


def slot_quality(r):
    if not r.get('hit_windows') or not r.get('mv_phys'):
        return None
    return r['mv_phys'] / max(r['hit_windows'][0][0], 1)


def setup_frames():
    """{family: {'clip', 'iframes', 'ready', 'exposed'}} for run, roll, backstep and crouch, and
    {'clip', 'press'} for the jump, read from TAE (module docstring, setup discount). Cached."""
    if not _SETUP:
        ash = _mod('er_mechanics_ashes', 'er-mechanics-ashes.py')
        for fam, clip in SETUP_CLIPS.items():
            m = ash.evasion_motion(0, clip)
            if m is None:
                raise SystemExit(f'no a000_{clip:06d} in the player TAE: the {fam} setup cannot be measured')
            ready = None if m['ready'] is None else round(m['ready'], 1)
            exposed = 0.0 if ready is None else max(0.0, ready - m['iframes'])
            _SETUP[fam] = {'clip': m['anim'], 'iframes': m['iframes'], 'ready': ready, 'exposed': round(exposed, 1)}
        jmp = _mod('er_mechanics_jump', 'er-mechanics-jump.py')
        _SETUP['jump'] = {'clip': f"a000_{jmp.JUMP_KINDS[JUMP_KIND]['clip']:06d}",
                          'press': jmp.takeoff(JUMP_KIND)['press'], 'module': jmp}
    return _SETUP


def slot_setup(r, family):
    """One slot's part of a move channel: {'slot', 'family', 'startup', 'exposed', 'factor'}, with
    `factor` = startup / (startup + exposed)."""
    startup = r['hit_windows'][0][0]
    exposed = 0.0
    if family == 'jump':
        js = setup_frames()['jump']
        seq = js['module'].sequence({'anim': r['anim'], 'startup': startup}, JUMP_KIND)
        if seq is None:
            raise SystemExit(f"{r['slot']} {r['anim']}: no jump sequence, the jump setup cannot be measured")
        startup, exposed = seq['first_hit'] - seq['press'], seq['first_hit']
    elif family:
        exposed = setup_frames()[family]['exposed']
    total = startup + exposed
    return {'slot': r['slot'], 'family': family or 'neutral', 'startup': round(startup, 1),
            'exposed': round(exposed, 1), 'factor': round(startup / total, 4) if total > 0 else 1.0}


def weapon_vector(d, pop, wid, tables, prof):
    """{channel: engagement 0..1} of one weapon as it comes, before any ash or infusion, and
    `_setup` {channel: {'discount', 'parts'}} for every channel it carries (1 for all but the
    prior-input move channels).

    An infusion's element or status and an ash's skill are open to every weapon that takes them,
    so they say nothing about this weapon; only what its own base row and moveset carry counts."""
    affinity = 'Standard'
    v = {}
    setup = {}
    one = {r['slot']: r for r in d.ATT.weapon_attacks(d.reg, wid, 'one')}
    sub_of = {s['slot']: {x for h in s['hits'] for x in h['subcats']}
              for g in prof['grips'].values() for s in g['slots']}
    for sc, slots in MOVE_SLOTS.items():
        best = 0.0
        parts = []
        for slot in slots:
            r = one.get(slot)
            if r and sc in sub_of.get(slot, ()) and slot_quality(r):
                best = max(best, pop.pct(pop.slot_q.get(slot, []), slot_quality(r)))
                parts.append(slot_setup(r, SLOT_FAMILY.get(slot)))
        if best:
            v[f'move:{sc}'] = best
            setup[f'move:{sc}'] = {'discount': round(sum(p['factor'] for p in parts) / len(parts), 4),
                                   'parts': parts}
    both = prof['grips']['both']['slots']
    if both:
        v['move:120'] = sum(1 for s in both if 120 in sub_of.get(s['slot'], ())) / len(both)
    rows = prof['skill']['rows']
    if rows and d.reg.weapon[wid]['gemMountType'] == 0:     # a skill no ash can replace
        v['move:112'] = sum(1 for r in rows if set(d.subcats(r)) & {111, 112}) / len(rows)
    row_id = wid + AR.AFFINITIES.index(affinity) * 100
    w = d.wep.get(row_id, d.wep[wid])
    base = {'physical': w['attackBasePhysics'], 'magic': w['attackBaseMagic'], 'fire': w['attackBaseFire'],
            'lightning': w['attackBaseThunder'], 'holy': w['attackBaseDark']}
    tot = sum(base.values()) or 1
    for e, x in base.items():
        if x:
            v[f'element:{e}'] = x / tot
    # Status: how hard the weapon's own build-up hits, as a percentile of every weapon whose base
    # row builds that status. A hit row that builds it (a moveset AtkParam) is read the same way.
    d.affinity = affinity
    for st, e in AFF.weapon_statuses(d, wid, prof['grips'])['statuses'].items():
        per_hit = max([e['weapon_row']] + list(e['atk_rows'].values()))
        if per_hit > 0:
            v[f'status:{st}'] = pop.pct(pop.status.get(st, []), per_hit)
    hits = [h for s in prof['grips']['one']['slots'] for h in s['hits']]
    rec = sum(h['records'] for h in hits)
    if rec:
        v['pierce'] = sum(h['records'] for h in hits if h['phys_type'] == 'pierce') / rec
    s = successive(d, wid, prof)
    if s and s['net_per_s'] > 0:
        v['successive'] = pop.pct(pop.succ, s['net_per_s'])
        v['_successive'] = s
    ref = {s: REFERENCE_STAT for s in ('str', 'dex', 'int', 'fth', 'arc')}
    name = d.weapon_name(wid)
    try:
        top = tables.max_level(tables.weapons[tables.find_weapon(name, affinity)]['reinforceTypeId'])
        base_ar = _total(AR.attack_rating(tables, name, affinity, top, ref))
        v['_stat_ar'] = {}
        for _, lab, key in STATS:
            v['_stat_ar'][key] = (base_ar, lambda pts, key=key: _total(
                AR.attack_rating(tables, name, affinity, top, {**ref, key: REFERENCE_STAT + pts})) / base_ar - 1)
    except SystemExit:
        pass
    for ch in v:
        if not ch.startswith('_') and ch not in setup:
            setup[ch] = {'discount': 1.0, 'parts': []}
    v['_setup'] = setup
    return v


def _total(ar):
    return sum(x['total'] for x in ar['damage'].values()) or 1.0


def discount(wv, ch, setup=True):
    """The channel's setup discount for this weapon (1 with `setup` off or on a neutral channel)."""
    return (wv.get('_setup') or {}).get(ch, {}).get('discount', 1.0) if setup else 1.0


def synergy(gear, wv, setup=True):
    """(score, [(channel, engagement, gain, contribution, row)]) of one gear item for a weapon
    vector; the contribution is engagement x setup discount x gain."""
    parts = []
    for b in gear['benefits']:
        ch = b['channel']
        if ch.startswith('stat:'):
            fn = (wv.get('_stat_ar') or {}).get(b['key'])
            if not fn:
                continue
            g = fn[1](b['points'])
            if g > 1e-4:
                parts.append((ch, 1.0, g, g, b))
            continue
        e = wv.get(ch, 0.0)
        gain = b['gain']
        if not ch.startswith('element:') and 'mult' in b and len(b['mult']) < len(ELEMENTS):
            # Only some elements boosted: weigh by the weapon's share of each.
            gain = sum(wv.get(f'element:{el}', 0.0) * (m - 1) for el, m in b['mult'].items())
        if e > 0 and gain > 1e-4:
            parts.append((ch, e, gain, e * discount(wv, ch, setup) * gain, b))
    score = sum(p[3] for p in parts) - survivability_cost(gear)
    return score, parts


STATUS_TEXT = {'bleed': 'blood loss', 'poison': 'poison or rot', 'scarlet_rot': 'poison or rot',
               'madness': 'madness', 'sleep': 'sleep'}


def describe(parts, affinity, item, wv):
    """One line of player-facing copy for a ranked row, from its channels and costs."""
    out, seen = [], set()
    for ch, e, g, _, b in parts:
        pct = f'+{g * 100:.0f}%'
        kind, _, arg = ch.partition(':')
        if kind == 'move':
            label = TAL.SUBCAT.get(int(arg), arg)
            text = {'two-handed attack': f'{pct} on two-handed attacks',
                    'skill': f'{pct} on its skill'}.get(label, f'{pct} on {label}s')
        elif kind == 'pierce':
            text = f'{pct} counter-hit damage; {e * 100:.0f}% of its hits pierce'
        elif kind == 'status':
            text = f'{pct} damage after {STATUS_TEXT.get(arg, arg)} procs nearby'
        elif kind == 'successive':
            s = wv.get('_successive') or {}
            grip = 'two-handed ' if s.get('grip') == 'both' else ''
            text = f"{pct} damage once successive hits reach {s.get('threshold')}"
            if s.get('reached') == 'always' and s.get('seconds') is not None:
                text += (f"; {s['hits']} landed {grip}R1s get there in {s['seconds']:.1f} s and its R1 string "
                         f"outpaces the decay by {s['net_per_s']:.1f} a second, faster than "
                         f"{e * 100:.0f}% of weapons")
        elif kind == 'stat':
            text = f"+{b['points']} {arg}: {pct} attack at {REFERENCE_STAT} in every stat"
        elif kind == 'element':
            text = f'{pct} {arg} damage'
        else:
            text = ch
        if text not in seen:
            seen.add(text)
            out.append(text)
    line = '. '.join(out)
    if affinity:
        line += f'. {affinity} affinity'
    cost = survivability_cost(item)
    downs = ([f'{cost * 100:.1f}% more damage taken'] if cost >= 0.005 else []) + item['other_costs']
    if downs:
        line += '. Costs ' + ', '.join(downs)
    return line + '.'


def critical(d, pop, gear, wid):
    """The weapon's critical figure against every base weapon that can crit, and, when it is above
    the median, the gear whose crit-only rows raise critical damage.

    The figure is the weapon's throwAtkRate, which `er-mechanics-crits` reads as the
    (throwAtkRate + 100) * 0.01 factor on backstabs and ripostes. A crit-only gear row gains the
    same share on every weapon, but its value in damage scales with that factor, so it is worth
    pointing out only on a weapon whose factor is above normal."""
    import statistics
    card = _mod('er_mechanics_weapon_card', 'er-mechanics-weapon-card.py')
    rate = d.wep[wid]['throwAtkRate']
    median = statistics.median(pop.crit)
    r = card.rank(pop.crit, rate, 'high')
    out = {'throwAtkRate': rate, 'multiplier': round((rate + 100) * 0.01, 3), 'can_crit': wid in pop.can_crit,
           'median_throwAtkRate': median, 'rank': list(r), 'rank_text': card.rank_text(r, 'highest'),
           'above_normal': wid in pop.can_crit and rate > median, 'gear': []}
    if not out['above_normal']:
        return out
    for name, item in gear.items.items():
        for c in item['critical']:
            out['gear'].append({'name': name, 'kind': item['kind'], 'gain': round(c['gain'], 4), 'row': c['row'],
                                'survivability_cost': round(survivability_cost(item), 4),
                                'other_costs': item['other_costs']})
    out['gear'].sort(key=lambda g: -g['gain'])
    out['why'] = (f"{d.weapon_name(wid)}'s critical multiplier is x{out['multiplier']:.2f} "
                  f"(throwAtkRate {rate}), {out['rank_text']} weapons that can crit; "
                  f"the median is x{(median + 100) * 0.01:.2f}")
    return out


def rank(d, pop, gear, tables, weapon, top=12, min_score=0.01, setup=True):
    """`setup` False scores without the setup discount (the selftest's comparison)."""
    wid = d.find_weapon(weapon)
    prof = AFF.weapon_profile(d, wid)
    wv = weapon_vector(d, pop, wid, tables, prof)
    rows = []
    for name, item in gear.items.items():
        sc, parts = synergy(item, wv, setup)
        if not parts or sc < min_score:
            continue
        rows.append({'name': name, 'kind': item['kind'], 'score': round(sc, 4),
                     'channels': [{'channel': p[0], 'engagement': round(p[1], 3),
                                   'discount': discount(wv, p[0], setup), 'gain': round(p[2], 4),
                                   'row': p[4]['row']} for p in parts],
                     'survivability_cost': round(survivability_cost(item), 4),
                     'other_costs': item['other_costs'],
                     'why': describe(parts, None, item, wv)})
    rows.sort(key=lambda r: -r['score'])
    return {'weapon': d.weapon_name(wid), 'id': wid, 'powerstance': powerstance(d, wid),
            'vector': {k: round(x, 3) for k, x in wv.items() if not k.startswith('_')},
            'setup': {ch: s if setup else {'discount': 1.0, 'parts': []} for ch, s in wv['_setup'].items()},
            'setup_frames': {f: {k: x for k, x in s.items() if k != 'module'} for f, s in setup_frames().items()},
            'gear': rows[:top], 'critical': critical(d, pop, gear, wid)}


#: Dual moves whose first hit counts from the button press, so one weapon's frame compares with
#: another's. The later L1s count from their own clip and the jump L1 from its landing.
RANKED_DUAL_SLOTS = ('dual_1', 'dual_dash', 'dual_roll', 'dual_crouch', 'dual_bstep')
_DUAL_POP = {}


def _dual_moves(mv, reg, wid):
    out = []
    for s in mv.dual_attacks(reg, wid):
        hits = s['hits']
        if hits:
            out.append({'slot': s['slot'], 'label': s['label'], 'anim': s['anim'],
                        'first_hit': min(h['frames'][0] for h in hits),
                        'mv_phys': sum(h['mv_phys'] for h in hits), 'hits': len(hits)})
    return out


def _dual_population(d, psg, mv):
    """{dual slot: [first hit of every base weapon that powerstances with itself]}."""
    if not _DUAL_POP:
        for o in AFF.base_weapons(d):
            if o not in d.reg.weapon or not psg.can_powerstance(d.reg, o, o):
                continue
            try:
                moves = _dual_moves(mv, d.reg, o)
            except Exception:                           # weapon rows the attack module cannot read
                continue
            for m in moves:
                _DUAL_POP.setdefault(m['slot'], []).append(m['first_hit'])
    return _DUAL_POP


def motion_category_name(value):
    """Smithbox's `WEPMOTION_CATEGORY` name, the category powerstance pairs on (37 Great Spear)."""
    path = os.path.join(os.path.dirname(PR.PARAMDEF_DIR), 'Param Enums', 'WEPMOTION_CATEGORY.json')
    with open(path) as fh:
        opts = json.load(fh)['Options']
    return next((o['Names'][0]['Text'] for o in opts if o['Key'] == str(value)), None)


def powerstance(d, wid):
    """The weapons `wid` powerstances with (in the left hand), the dual moves a same-weapon pair
    gets with their first hit (real frame) and summed physical motion value, each ranked against
    the same move of every weapon that powerstances with itself, and the page line for the move
    that ranks best."""
    psg = _mod('er_mechanics_powerstance_guard', 'er-mechanics-powerstance-guard.py')
    mv = _mod('er_mechanics_moveset', 'er-mechanics-moveset.py')
    card = _mod('er_mechanics_weapon_card', 'er-mechanics-weapon-card.py')
    reg = d.reg
    partner_ids = [o for o in AFF.base_weapons(d) if o in reg.weapon and psg.can_powerstance(reg, wid, o)]
    if not partner_ids:
        return None
    pop = _dual_population(d, psg, mv)
    moves = _dual_moves(mv, reg, wid)
    for m in moves:
        if m['slot'] in RANKED_DUAL_SLOTS and pop.get(m['slot']):
            m['rank'] = card.rank(pop[m['slot']], m['first_hit'], 'low')
            m['rank_text'] = card.rank_text(m['rank'], 'fastest')
    ranked = [m for m in moves if 'rank' in m]
    best = min(ranked, key=lambda m: m['rank'][0] / m['rank'][2]) if ranked else None
    name = d.weapon_name(wid)
    cats = {reg.weapon[o]['wepmotionCategory'] for o in partner_ids}
    cls = motion_category_name(cats.pop()) if len(cats) == 1 else None
    others = len(partner_ids) - (wid in partner_ids)
    kind = f'{cls.lower()}s' if cls else 'weapons'
    head = f'A second {cls.lower()}' if cls else 'A powerstance partner'
    line = f'Powerstance with another {name} or any of {others} other {kind}'
    if best:
        line += (f" adds a {best['label']} that hits on frame {best['first_hit']:g} "
                 f"({best['rank_text']}).")
    return {'partners': [d.weapon_name(o) for o in partner_ids], 'moves': moves,
            'heading': head, 'text': line}


def build():
    d = AFF.Data()
    return d, Population(d), Gear(d), AR.Tables()


def main_text_runs(weapon):
    """The plain-text report runs to the end (it read a removed field once and only --json was tested)."""
    import contextlib
    import io
    argv = sys.argv
    sys.argv = [argv[0], weapon, '--top', '3']
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            return main() == 0
    finally:
        sys.argv = argv


def selftest():
    d, pop, gear, tables = build()
    gi = gear.items
    assert any(b['channel'] == 'move:102' for b in gi['Claw Talisman']['benefits']), gi['Claw Talisman']
    assert any(b['channel'].startswith('status:') for b in gi['White Mask']['benefits']), gi['White Mask']
    assert any(b['channel'].startswith('status:') for b in gi['Mushroom Crown']['benefits']), gi['Mushroom Crown']
    assert any(b['channel'] == 'element:fire' for b in gi['Fire Scorpion Charm']['benefits'])
    assert survivability_cost(gi['Fire Scorpion Charm']) > 0
    assert any(b['channel'] == 'stat:DEX' for b in gi["Millicent's Prosthesis"]['benefits'])
    lance = rank(d, pop, gear, tables, 'Lance', top=60)
    names = {r['name']: r for r in lance['gear']}
    assert 'Ritual Sword Talisman' not in names, 'HP-gated rows are not weapon synergy'
    assert 'Lance Talisman' not in names, 'horseback is left out'
    # Lance builds no status and takes ashes: infusion and ash gear is open to every such weapon.
    for n in ('White Mask', "Lord of Blood's Exultation", "Kindred of Rot's Exultation", 'Shard of Alexander',
              'Fire Scorpion Charm'):
        assert n not in names, (n, names.get(n))
    # Lance's slow R1 string barely outpaces the decay; Backhand Blade's two blades fill it fast.
    assert lance['vector']['successive'] < 0.3 and 'Rotten Winged Sword Insignia' not in names, lance['vector']
    bhb = rank(d, pop, gear, tables, 'Backhand Blade', top=60)
    assert bhb['vector']['successive'] > 0.8 and 'Rotten Winged Sword Insignia' in {r['name'] for r in bhb['gear']}
    uchi = {r['name'] for r in rank(d, pop, gear, tables, 'Uchigatana', top=60)['gear']}
    assert "Lord of Blood's Exultation" in uchi and 'Shard of Alexander' not in uchi, uchi
    rob = {r['name'] for r in rank(d, pop, gear, tables, 'Rivers of Blood', top=60)['gear']}
    assert "Lord of Blood's Exultation" in rob, rob
    assert lance['powerstance'] and 'Lance' in lance['powerstance']['partners']
    assert any(m['slot'] == 'dual_crouch' and m['anim'].endswith('034310') for m in lance['powerstance']['moves'])
    ps = lance['powerstance']
    assert ps['heading'] == 'A second great spear', ps['heading']
    assert ps['text'].startswith('Powerstance with another Lance or any of 9 other great spears adds a ') \
        and ' fastest of ' in ps['text'], ps['text']
    assert any(c['row'] == 320900 for c in gi['Dagger Talisman']['critical']), gi['Dagger Talisman']
    assert not lance['critical']['above_normal'] and not lance['critical']['gear'], lance['critical']
    mis = rank(d, pop, gear, tables, 'Misericorde', top=3)['critical']
    longsword = rank(d, pop, gear, tables, 'Longsword', top=3)['critical']
    assert mis['throwAtkRate'] > longsword['throwAtkRate'], (mis, longsword)
    assert mis['above_normal'] and not longsword['above_normal'], (mis, longsword)
    assert 'Dagger Talisman' in {g['name'] for g in mis['gear']}, mis
    # Setup discount: neutral channels whole, a prior input discounted by its exposed frames.
    sf = setup_frames()
    assert sf['roll']['iframes'] > 0 and sf['roll']['exposed'] == round(sf['roll']['ready'] - sf['roll']['iframes'], 1)
    assert sf['backstep']['iframes'] == 0 and sf['backstep']['exposed'] == sf['backstep']['ready'], sf['backstep']
    assert sf['run']['exposed'] == 0, sf['run']
    after = rank(d, pop, gear, tables, 'Misericorde', top=60)
    before = rank(d, pop, gear, tables, 'Misericorde', top=60, setup=False)
    su = after['setup']
    for ch in ('move:100', 'move:104', 'move:120'):
        assert su[ch]['discount'] == 1.0 and all(p['family'] == 'neutral' for p in su[ch]['parts']), (ch, su[ch])
    fam = {}
    for s in su.values():
        for p in s['parts']:
            fam.setdefault(p['family'], []).append(p['factor'])
    assert max(fam['jump']) < min(fam['roll']) and max(fam['backstep']) < min(fam['roll']), fam
    assert max(fam['roll']) < min(fam['run']) == 1.0, fam
    a = {r['name']: r['score'] for r in after['gear']}
    b = {r['name']: r['score'] for r in before['gear']}
    for low in ('Retaliatory Crossed-Tree', 'Claw Talisman'):
        for high in ('Twinblade Talisman', 'Two-Handed Sword Talisman'):
            assert a[low] / a[high] < b[low] / b[high], (low, high, a[low], a[high], b[low], b[high])
    assert b['Retaliatory Crossed-Tree'] > b['Two-Handed Sword Talisman'] > a['Retaliatory Crossed-Tree'], (a, b)
    assert main_text_runs('Lance') and main_text_runs('Misericorde')
    print(f"selftest ok: Lance top {[r['name'] for r in lance['gear'][:6]]}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('weapon', nargs='?')
    ap.add_argument('--top', type=int, default=12)
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.weapon:
        ap.error('weapon required')
    d, pop, gear, tables = build()
    out = rank(d, pop, gear, tables, a.weapon, a.top)
    if a.json:
        json.dump(out, sys.stdout, indent=1)
        return 0
    print(f"{out['weapon']} ({out['id']})")
    ps = out['powerstance']
    if ps:
        print(f"  powerstance with {len(ps['partners'])} weapons: {', '.join(ps['partners'])}")
        for m in ps['moves']:
            print(f"    {m['label']:<12} {m['anim']}  first hit f{m['first_hit']:.0f}  {m['hits']} hits  "
                  f"MV {m['mv_phys']}  {m.get('rank_text', '')}")
        print(f"  {ps['heading']}: {ps['text']}")
    sf = out['setup_frames']
    print('  setup frames: ' + ', '.join(
        f"{f} {s['clip']} " + (f"press f{s['press']:g}" if f == 'jump' else
                               f"R1 f{s['ready']:g} i-frames f{s['iframes']:g} exposed {s['exposed']:g}"
                               if s['ready'] is not None else 'no R1 window, exposed 0')
        for f, s in sf.items()))
    print('  setup discount per channel, startup / (startup + exposed):')
    for ch, s in sorted(out['setup'].items()):
        parts = ', '.join(f"{p['slot']} {p['family']} {p['startup']:g}/({p['startup']:g}+{p['exposed']:g})"
                          f"={p['factor']:.2f}" for p in s['parts'])
        print(f"    {ch:<18} x{s['discount']:.2f}  {parts}".rstrip())
    for r in out['gear']:
        ch = ', '.join(f"{c['channel']} {c['engagement']:.2f}x{c['gain']:+.3f}"
                       + (f" setup x{c['discount']:.2f}" if c['discount'] < 1 else '') for c in r['channels'])
        cost = f" cost {r['survivability_cost']:.3f}" if r['survivability_cost'] else ''
        print(f"  {r['score']:+.3f} {r['name']:<34} {r['kind']:<8} {ch}{cost} {'; '.join(r['other_costs'])}")
    crit = out['critical']
    if crit['above_normal']:
        print(f"  critical gear: {crit['why']}.")
        for g in crit['gear']:
            cost = f" cost {g['survivability_cost']:.3f}" if g['survivability_cost'] else ''
            print(f"    {g['name']:<34} {g['kind']:<8} +{g['gain'] * 100:.0f}% critical damage (row {g['row']})"
                  f"{cost} {'; '.join(g['other_costs'])}".rstrip())
    return 0


if __name__ == '__main__':
    sys.exit(main())
