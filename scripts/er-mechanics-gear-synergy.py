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
| `move:104` chain final hit | Twinblade Talisman | percentile, among every base weapon, of the damage its chain final hits deal per second while its light-attack string is looped, in its best grip (one-handed, two-handed, or powerstanced with both hands' chain hits counted), at max upgrade and 40 in every stat (`er-mechanics-chain-attacks.py`) |
| `successive` | successive-hit stage rows (Winged Sword Insignia, Rotten Winged Sword Insignia, Millicent's Prosthesis) | percentile, among every base weapon, of the counter its light-attack string adds per second net of the host's decay, in its fastest grip: one-handed, two-handed, or powerstanced, where both hands' hits each add (multi-hitbox swings count each landed record) |
| `ammo` | a row gated on subcategory 105 or 118 (Arrow's Sting, Arrow's Soaring Sting) | 1 for a weapon that loads arrows or bolts (`EquipParamWeapon` `arrowSlotEquipable` / `boltSlotEquipable`), whose every hit is its ammunition; 0 otherwise |
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

Three gates decide what is listed (user 2026-10-04):

- Magnitude. A row whose net damage effect on the weapon is 1% or less is dropped
  (`net_benefit`: each channel's gain times the share of the weapon's damage it reaches).
- Percentile. An item is listed on a weapon only when fewer than 35% (`GATE_SHARE`) of the base
  weapons the item engages score higher with it. Every item's scores over every base weapon are
  computed once per process (`Population.item_scores`). This applies to the pierce-pinned Spear
  Talisman too.
- Two-handing. Two-Handed Sword Talisman and Axe Talisman also need the weapon's best RL 150
  build in the top 35% by AR, Heavy as its top physical infusion (STR alone, which two-handing
  counts at x1.5), and a two-handed moveset worth more than the one-handed one by
  `er-mechanics-crits._slot_value`, damage per committed frame (`grip_moveset`; for Axe Talisman
  only the charged R2s, the attacks its row 321300 boosts in either grip). The build comes from
  the board (`gen-r3-weapon-boards.py`); without one these two are withheld.

Critical-damage rows (`throwAttackParamChange`, the Dagger Talisman's x1.17) are not scored
either, but they get their own block, "critical gear", on a weapon whose critical is higher than
normal: its throwAtkRate (the (throwAtkRate + 100) * 0.01 crit factor, Misericorde 40 against a
Longsword's 0) above the median of every base weapon that can crit (enableThrow), with its rank
among them. A weapon at or below the median prints nothing; --json carries the figure, the rank
and the gear under `critical` either way.

Weapons also pair with each other: a second weapon the behavior script lets the first
powerstance with (`er-mechanics-powerstance-guard.can_powerstance`) unlocks the dual L1 moves,
listed with their first hit frame and motion value from `er-mechanics-moveset.dual_attacks`.
Given the board's RL 150 build, the row names the best partner among those weapons rather than a
second copy (`best_partner`): the most damage on the median RL 150 defender at that build, then
reach, weight and scaling, keeping this weapon when nothing beats it. A bow's range gear (Arrow's
Reach Talisman's `bowDistRate`) is listed under `ranged`, beside the scored rows.
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
#: Ammunition: 105 (ammunition attack) and 118 (ammunition on-hit), the subcategories Arrow's Sting,
#: Arrow's Soaring Sting and Sharpshot Talisman gate on, are one channel, so a row naming both
#: counts once. 113 (ranged skill) on the same rows is a bow's skill, which any bow's ash gives it,
#: so like `move:112` on an ash weapon it says nothing about this bow.
AMMO_CHANNEL = 'ammo'
AMMO_SUBCATS = {105, 118}
RANGED_SKILL_SUBCATS = {113}
# Moves whose quality is a slot of the moveset, by the slot names `weapon_attacks` uses. The chain
# final hit (104) is not a slot: it is the end of a string, measured by `chain` below.
MOVE_SLOTS = {100: ('r2_1c', 'r2_2c'), 102: ('jump_r1', 'jump_r2'),
              121: ('roll_r1', 'bstep_r1', 'crouch_r1'), 122: ('run_r1', 'run_r2'), 127: ()}
CHAIN_CHANNEL = 'move:104'
CH = _mod('er_mechanics_chain_attacks', 'er-mechanics-chain-attacks.py')
#: How a grip is named in player-facing copy.
GRIP_TEXT = {'one': 'one-handed R1', 'both': 'two-handed R1', 'dual': 'powerstanced L1'}
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
            # EquipParamAccessory.accessoryGroup: the game refuses two talismans of one group
            # together, and a group is one talisman's tiers (Warrior Jar Shard and Shard of
            # Alexander, Winged Sword Insignia and its rotten version), so `rank` keeps the best.
            self.items[tal.name]['group'] = tal.group
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
        benefits, cost, other, critical, ranged = [], {'damage_taken': 1.0, 'max_hp': 1.0}, [], [], []
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
                    seen = set()
                    for sc in s.subcats():
                        ch = (AMMO_CHANNEL if sc in AMMO_SUBCATS else None if sc in RANGED_SKILL_SUBCATS
                              or sc in SKIP_SUBCATS else f'move:{sc}')
                        if ch and ch not in seen:
                            seen.add(ch)
                            benefits.append({'channel': ch, 'gain': gain, 'mult': mult, 'row': s.id})
                elif gates and all(g in presence for g in gates):
                    for st in {x for g in gates for x in presence[g]}:
                        benefits.append({'channel': f'status:{st}', 'gain': gain, 'mult': mult, 'row': s.id})
                elif not gates and si in (0,) and len(mult) < len(ELEMENTS) and gain > 0:
                    for e, v in mult.items():
                        benefits.append({'channel': f'element:{e}', 'gain': v - 1, 'row': s.id})
            if not gated and f.get('bowDistRate', 0) > 0:
                # Arrow's Reach Talisman: range, added to the weapon's bowDistRate (talismans.md).
                ranged.append({'bowDistRate': f['bowDistRate'], 'row': s.id})
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
                'critical': critical, 'range': ranged}


def survivability_cost(item):
    c = item['cost']
    return (c['damage_taken'] / max(c['max_hp'], 1e-6) - 1) + c.get('armor_absorption', 0.0)


class Population:
    """Per-slot motion value per frame and per-weapon measures across every base weapon, for
    percentiles. Built once per process."""

    def __init__(self, d, tables):
        self.d = d
        self.tables = tables
        self._vectors = None
        self._scores = {}
        self.slot_q = {}
        self.status = {}
        self.succ = []
        self.chain = []
        files = PR.load()
        wep = PR.rows(PR.param_bytes(files, 'EquipParamWeapon'),
                      ['enableThrow', 'arrowSlotEquipable', 'boltSlotEquipable'], strict=False)[0]
        self.can_crit = {r['id'] for r in wep if r['enableThrow']}
        #: Weapons that load arrows or bolts: bows, light bows, greatbows, crossbows, ballistae.
        self.ammo = {r['id'] for r in wep if r['arrowSlotEquipable'] or r['boltSlotEquipable']}
        #: throwAtkRate of every base weapon that can crit: the population "higher than normal" is
        #: read against. Bows, catalysts, shields, torches and whips have enableThrow 0.
        self.crit = [d.wep[w]['throwAtkRate'] for w in AFF.base_weapons(d) if w in self.can_crit]
        for wid in AFF.base_weapons(d):
            for st, v in innate_row_statuses(d, wid).items():
                self.status.setdefault(st, []).append(v)
            try:
                prof = AFF.weapon_profile(d, wid)
            except Exception:                           # weapon rows the attack module cannot read
                prof = None
            s = successive(d, wid, prof) if prof else None
            if s:
                self.succ.append(s['net_per_s'])
            c = chain(d, tables, wid, prof) if prof else None
            if c:
                self.chain.append(c['damage_per_s'])
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

    def vectors(self):
        """{base weapon id: `weapon_vector`} of every base weapon the vector can be read for. Built
        once, on first use (about a minute)."""
        if self._vectors is None:
            self._vectors = {}
            for wid in AFF.base_weapons(self.d):
                try:
                    self._vectors[wid] = weapon_vector(self.d, self, wid, self.tables,
                                                       AFF.weapon_profile(self.d, wid))
                except Exception:                       # weapon rows the attack module cannot read
                    continue
        return self._vectors

    def item_scores(self, gear, setup=True):
        """{gear name: [synergy score on every base weapon it engages]}: the population the
        percentile gate (`passes_gate`) reads each item's score against. Built once per `setup`."""
        if setup not in self._scores:
            out = {}
            for wv in self.vectors().values():
                for name, item in gear.items.items():
                    sc, parts = synergy(item, wv, setup)
                    if parts:
                        out.setdefault(name, []).append(sc)
            self._scores[setup] = out
        return self._scores[setup]


#: Percentile gate (user 2026-10-04: "Don't include suggestions for something if its in the bottom
#: 65% for interactions compared to others"). A gear item is listed on a weapon only when fewer than
#: this share of the base weapons the item engages score higher with it than this weapon does.
GATE_SHARE = 0.35
#: The two talismans two-handing a heavy STR weapon is for, and the moveset slots each is judged
#: on: every slot for Two-Handed Sword Talisman (subcategory 120 is on every two-handed attack), the
#: charged R2s for Axe Talisman (row 321300 boosts subcategory 100, charged heavies, in either grip).
TWO_HAND_GEAR = {'Two-Handed Sword Talisman': None, 'Axe Talisman': ('r2_1c', 'r2_2c')}
#: The physical infusion that scales on STR alone, which two-handing counts at x1.5.
TWO_HAND_AFFINITY = 'Heavy'


#: Magnitude floor (user 2026-10-04: "I wouldn't include anything that gives mere 1% bonuses"): a
#: row is dropped when its net damage effect on the weapon (`net_benefit`) is this or less.
BENEFIT_FLOOR = 0.01
#: Channels whose engagement is the share of the weapon's damage the effect reaches (an element's
#: share of the attack, the piercing share of hits, the share of two-handed slots or skill rows
#: carrying the subcategory, ammunition). The other channels' engagement is a percentile of how
#: good the weapon's move is, and the effect reaches that move whole.
COVERAGE_CHANNELS = ('element:', 'pierce', 'move:120', 'move:112', AMMO_CHANNEL)


def net_benefit(parts):
    """The row's net damage effect on the weapon: each channel's gain times the share of the
    weapon's damage it reaches (1 for a percentile channel), summed. Neither the setup discount
    nor the survivability cost enters: they weigh the row, they do not change what it adds."""
    return sum(p[2] * (p[1] if p[0].startswith(COVERAGE_CHANNELS) else 1.0) for p in parts)


def ahead_share(values, v):
    """Share of `values` strictly greater than `v` (ties with `v` count as level with it)."""
    if not values:
        return 0.0
    return sum(1 for x in values if x > v + 1e-9) / len(values)


def passes_gate(values, v, share=GATE_SHARE):
    """True when `v` is in the top `share` of `values`: fewer than `share` of them beat it."""
    return ahead_share(values, v) < share


_CRITS = {}


def crits():
    """(`er-mechanics-crits` module, its Tables), loaded once."""
    if not _CRITS:
        m = sys.modules.get('er_mechanics_crits') or _mod('er_mechanics_crits', 'er-mechanics-crits.py')
        _CRITS['mod'], _CRITS['t'] = m, m.Tables()
    return _CRITS['mod'], _CRITS['t']


def grip_moveset(weapon, slots=None, affinity='Standard', stats=None):
    """How the weapon's two-handed moveset compares with its one-handed one: the summed
    `er-mechanics-crits._slot_value` (damage per committed frame) of every slot the two grips share,
    or only `slots`, two-handed over one-handed. Both grips are valued at the same one-handed
    attack rating, so the ratio is the moveset alone; the x1.5 STR of two-handing is not in it."""
    cr, t = crits()
    wid = t.find_weapon(weapon)
    ar = cr.build_ar(t, weapon, affinity, stats)
    one = {r['slot']: r for r in cr.ATTACKS.weapon_attacks(t.reg, wid, 'one')}
    both = {r['slot'].removeprefix('2h_'): r for r in cr.ATTACKS.weapon_attacks(t.reg, wid, 'both')}
    keys = [k for k in one if k in both and (slots is None or k in slots)]
    v1 = sum(cr._slot_value(t.reg, wid, one[k], ar) for k in keys)
    v2 = sum(cr._slot_value(t.reg, wid, both[k], ar) for k in keys)
    return {'slots': keys, 'one': round(v1, 3), 'both': round(v2, 3),
            'ratio': round(v2 / v1, 3) if v1 else None}


def two_hand_verdict(ar_rank, top_physical, ratio):
    """{'pass', 'checks'} for Two-Handed Sword Talisman or Axe Talisman on one weapon: `ar_rank`
    (place, tied, of) of its best RL 150 build's AR among every weapon's must be in the top
    `GATE_SHARE`, its top physical infusion must be `TWO_HAND_AFFINITY`, and `ratio`
    (`grip_moveset`) must be above 1."""
    checks = {'ar': bool(ar_rank) and (ar_rank[0] - 1) / ar_rank[2] < GATE_SHARE,
              'heavy': top_physical == TWO_HAND_AFFINITY,
              'moveset': ratio is not None and ratio > 1.0}
    return {'pass': all(checks.values()), 'checks': checks}


def two_hand_check(weapon, item, build):
    """`two_hand_verdict` for `item` on `weapon` with what the board knows of the weapon's RL 150
    build: `build` is {'ar_rank', 'ar', 'top_physical', 'affinity', 'stats'} or None, and None
    fails every check (nothing to read the AR or the infusion from)."""
    if not build:
        return {'pass': False, 'checks': {'ar': False, 'heavy': False, 'moveset': False},
                'why': 'no RL 150 build to read its AR and top physical infusion from'}
    mv = grip_moveset(weapon, TWO_HAND_GEAR[item], build.get('affinity') or 'Standard', build.get('stats'))
    v = two_hand_verdict(build.get('ar_rank'), build.get('top_physical'), mv['ratio'])
    return {**v, 'ar_rank': list(build['ar_rank']) if build.get('ar_rank') else None, 'ar': build.get('ar'),
            'top_physical': build.get('top_physical'), 'moveset': mv,
            'charged_only': TWO_HAND_GEAR[item] is not None}


#: The talisman whose counter defines the successive channel. Winged Sword Insignia, its rotten
#: version and Millicent's Prosthesis share its thresholds 17/30/45/60 and its 2-per-second host
#: decay (`er-mechanics-talisman-affinity --selftest` checks all three).
SUCCESSIVE_REFERENCE = 'Rotten Winged Sword Insignia'


def successive(d, wid, prof=None):
    """How fast the weapon's light-attack string fills the successive-hit counter, in the grip that
    fills it fastest: one-handed R1, two-handed R1 (for a weapon powerstanced by itself that is its
    powerstance), or two copies powerstanced (L1, where both hands' hits each add their own
    entry, bd `successive-counter-counts-every-landed-hit-record-2026-10-02`). Counter per second
    net of the decay, and the seconds and hits from an empty counter to the first threshold. None
    when the string adds nothing."""
    spec = d.triggers[SUCCESSIVE_REFERENCE]['kinds']['successive']
    try:
        prof = prof or AFF.weapon_profile(d, wid)
        f = AFF.successive_features(prof, spec)
    except Exception:                                   # weapon rows the attack module cannot read
        return None
    best = None
    for grip in AFF.GRIPS:
        g = f.get(grip) or {}
        net = g.get('net_per_s_upper')
        if net is None or g.get('r1_string_gain', 0) <= 0:
            continue
        if best is None or net > best['net_per_s']:
            cs = g.get('cold_start_first_threshold') or {}
            best = {'grip': grip, 'paired': grip == 'both' and prof['two_hand_is_pair'],
                    'net_per_s': net, 'gain_per_s': g['gain_per_s'], 'hits_per_s': g.get('hits_per_s'),
                    'threshold': spec['hosts'] and min(h['threshold'] for h in spec['hosts']),
                    'reached': cs.get('reached'), 'seconds': (cs.get('seconds') or [None])[0],
                    'hits': (cs.get('hits') or [None])[0]}
    return best


def chain(d, tables, wid, prof=None):
    """Twinblade Talisman's engagement: the damage the weapon's chain final hits (subcategory 104)
    deal per second while its light-attack string is looped at the earliest inputs, in the grip
    where that is highest (`er-mechanics-chain-attacks.grip_chain`; one-handed, two-handed or
    powerstanced, both hands' chain hits counted). Damage is attack rating at max upgrade,
    Standard affinity and `REFERENCE_STAT` in every stat, times each hit's motion values, before
    defense. None when no grip of the weapon has a chain hit."""
    try:
        prof = prof or AFF.weapon_profile(d, wid)
    except Exception:                                   # weapon rows the attack module cannot read
        return None
    vals = CH.chain_values(d)
    ref = {s: REFERENCE_STAT for s in ('str', 'dex', 'int', 'fth', 'arc')}
    best = None
    for grip, gp in prof['grips'].items():
        ar = CH.weapon_ar(tables, wid, ref, grip == 'both')
        c = CH.grip_chain(gp, vals, grip, prof['two_hand_is_pair'], d.reg, ar)
        if not c.get('period_s') or not c.get('chain_damage'):
            continue
        rate = c['chain_damage'] / c['period_s']
        if best is None or rate > best['damage_per_s']:
            best = {'grip': grip, 'paired': grip == 'both' and prof['two_hand_is_pair'],
                    'damage_per_s': round(rate, 1), 'to_chain_s': c['to_chain_s'], 'period_s': c['period_s'],
                    'chain_hits': c['chain_hits'], 'chain_damage': c['chain_damage']}
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
    if wid in pop.ammo:
        v[AMMO_CHANNEL] = 1.0
    hits = [h for s in prof['grips']['one']['slots'] for h in s['hits']]
    rec = sum(h['records'] for h in hits)
    if rec:
        v['pierce'] = sum(h['records'] for h in hits if h['phys_type'] == 'pierce') / rec
    s = successive(d, wid, prof)
    if s and s['net_per_s'] > 0:
        v['successive'] = pop.pct(pop.succ, s['net_per_s'])
        v['_successive'] = s
    c = chain(d, tables, wid, prof)
    if c:
        v[CHAIN_CHANNEL] = pop.pct(pop.chain, c['damage_per_s'])
        v['_chain'] = c
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


def channel_detail(ch, wv):
    """The measure behind a successive or chain channel, for a consumer that words its own row
    (`gen-r3-weapon-boards.py`): {'grip_text', ...the measure}. Empty for other channels."""
    m = wv.get('_successive') if ch == 'successive' else wv.get('_chain') if ch == CHAIN_CHANNEL else None
    if not m:
        return {}
    return {'detail': {**m, 'grip_text': chain_grip_text(m)}}


def chain_grip_text(m):
    """The grip of a `successive` or `chain` measure as the board says it: a weapon powerstanced
    by itself is two-handed to the game but powerstanced to the player."""
    if m.get('paired'):
        return 'powerstanced R1'
    return GRIP_TEXT.get(m.get('grip'), 'R1')


def describe(parts, affinity, item, wv):
    """One line of player-facing copy for a ranked row, from its channels and costs."""
    out, seen = [], set()
    for ch, e, g, _, b in parts:
        pct = f'+{g * 100:.0f}%'
        kind, _, arg = ch.partition(':')
        if ch == CHAIN_CHANNEL and wv.get('_chain'):
            c = wv['_chain']
            text = (f"{pct} on the final hit of a chain; its {chain_grip_text(c)} string reaches it in "
                    f"{c['to_chain_s']:.1f} s and repeats every {c['period_s']:.1f} s, more chain damage a "
                    f"second than {e * 100:.0f}% of weapons")
        elif kind == 'move':
            label = TAL.SUBCAT.get(int(arg), arg)
            text = {'two-handed attack': f'{pct} on two-handed attacks',
                    'skill': f'{pct} on its skill'}.get(label, f'{pct} on {label}s')
        elif kind == 'pierce':
            text = f'{pct} counter-hit damage; {e * 100:.0f}% of its hits pierce'
        elif kind == AMMO_CHANNEL:
            text = f'{pct} on arrows and bolts'
        elif kind == 'status':
            text = f'{pct} damage after {STATUS_TEXT.get(arg, arg)} procs nearby'
        elif kind == 'successive':
            s = wv.get('_successive') or {}
            text = f"{pct} damage once successive hits reach {s.get('threshold')}"
            if s.get('reached') == 'always' and s.get('seconds') is not None:
                text += (f"; {s['hits']} landed hits of its {chain_grip_text(s)} string get there in "
                         f"{s['seconds']:.1f} s and it outpaces the decay by {s['net_per_s']:.1f} a second, "
                         f"faster than {e * 100:.0f}% of weapons")
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


def rank(d, pop, gear, tables, weapon, top=12, min_score=0.01, setup=True, gate=True, build=None):
    """`setup` False scores without the setup discount (the selftest's comparison). `gate` False
    skips the percentile gate and the two-handing gate (scoring tests). `build` is what the board
    knows of the weapon's best RL 150 build, for `two_hand_check`: {'ar_rank', 'ar',
    'top_physical', 'affinity', 'stats'}; without it the `TWO_HAND_GEAR` talismans are withheld.

    Dropped rows are listed under `gated` with the reason. The gate reads the item's score on this
    weapon against its score on every base weapon it engages; the channels' own engagement
    percentiles feed the score and are not a second gate."""
    wid = d.find_weapon(weapon)
    prof = AFF.weapon_profile(d, wid)
    wv = weapon_vector(d, pop, wid, tables, prof)
    pop_scores = pop.item_scores(gear, setup) if gate else {}
    rows, gated = [], []
    for name, item in gear.items.items():
        sc, parts = synergy(item, wv, setup)
        if not parts or sc < min_score:
            continue
        extra = {}
        if gate:
            net = net_benefit(parts)
            extra['net_benefit'] = round(net, 4)
            if round(net, 4) <= BENEFIT_FLOOR:
                gated.append({'name': name, 'why': f'adds {net:.1%} to this weapon, {BENEFIT_FLOOR:.0%} or less'})
                continue
            values = pop_scores.get(name, [])
            ahead = ahead_share(values, sc)
            extra['ahead_share'] = round(ahead, 3)
            if not passes_gate(values, sc):
                gated.append({'name': name, 'why': f'{ahead:.0%} of {len(values)} weapons score higher'})
                continue
            if name in TWO_HAND_GEAR:
                th = two_hand_check(d.weapon_name(wid), name, build)
                if not th['pass']:
                    gated.append({'name': name, 'why': th.get('why') or 'fails ' + ', '.join(
                        k for k, ok in th['checks'].items() if not ok), 'two_hand': th})
                    continue
                extra['two_hand'] = th
        rows.append({'name': name, 'kind': item['kind'], 'score': round(sc, 4), **extra,
                     'channels': [{'channel': p[0], 'engagement': round(p[1], 3),
                                   'discount': discount(wv, p[0], setup), 'gain': round(p[2], 4),
                                   'row': p[4]['row'], **channel_detail(p[0], wv)} for p in parts],
                     'survivability_cost': round(survivability_cost(item), 4),
                     'other_costs': item['other_costs'],
                     'why': describe(parts, None, item, wv), 'group': item.get('group')})
    rows.sort(key=lambda r: -r['score'])
    # One talisman per accessoryGroup: the lower tiers of the best one are not a second option.
    seen_groups = set()
    kept = []
    for r in rows:
        g = r.pop('group')
        if r['kind'] == 'talisman' and g and g > 0:
            if g in seen_groups:
                continue
            seen_groups.add(g)
        kept.append(r)
    rows = kept
    shown = rows[:top]
    # Gear on the pierce channel (Spear Talisman) is always listed for a weapon any of whose hits
    # pierce, ranked or not (user directive 2026-10-02), once it has passed the percentile gate
    # (2026-10-04) like every other row; `pinned` says why it is there.
    if wv.get('pierce', 0) > 0:
        for r in rows[top:]:
            if any(c['channel'] == 'pierce' for c in r['channels']):
                shown.append(dict(r, pinned='pierce'))
    return {'weapon': d.weapon_name(wid), 'id': wid, 'powerstance': powerstance(d, wid, build),
            'cross_hand': cross_hand(d, wid),
            'vector': {k: round(x, 3) for k, x in wv.items() if not k.startswith('_')},
            'setup': {ch: s if setup else {'discount': 1.0, 'parts': []} for ch, s in wv['_setup'].items()},
            'setup_frames': {f: {k: x for k, x in s.items() if k != 'module'} for f, s in setup_frames().items()},
            'gear': shown, 'gated': gated, 'critical': critical(d, pop, gear, wid),
            'ranged': ranged_gear(pop, gear, wid)}


def ranged_gear(pop, gear, wid):
    """Gear that lengthens the weapon's range (`bowDistRate`, Arrow's Reach Talisman), for a weapon
    that loads arrows or bolts. Range is not damage, so these rows are listed beside the scored ones
    rather than ranked against them; every bow gets the same, so no percentile applies."""
    if wid not in pop.ammo:
        return []
    out = []
    for name, item in gear.items.items():
        for r in item['range']:
            out.append({'name': name, 'kind': item['kind'], 'bowDistRate': r['bowDistRate'], 'row': r['row'],
                        'survivability_cost': round(survivability_cost(item), 4),
                        'other_costs': item['other_costs']})
    return sorted(out, key=lambda r: -r['bowDistRate'])


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


_PARTNER = {}


def _loaded(name, fname):
    return sys.modules.get(name) or _mod(name, fname)


def partner_context():
    """What `best_partner` measures with, loaded once: the optimizer, its AR tables, the median
    defender of the RL 150 corpus window (`er-mechanics-infusions.Builder.setup`'s), and the reach
    tables."""
    if not _PARTNER:
        inf = _loaded('er_mechanics_infusions', 'er-mechanics-infusions.py')
        opt = inf.OPT
        rows = opt.corpus_rows(opt.CACHE / 'builds.jsonl', inf.RANK_RL - 10, inf.RANK_RL + 10)
        card = _loaded('er_mechanics_weapon_card', 'er-mechanics-weapon-card.py')
        _PARTNER.update(inf=inf, opt=opt, tables=inf.AR.Tables(None), dfn=opt.bracket_defender(rows),
                        reach=card.REACH, rc=card.REACH.Reach())
    return _PARTNER


def partner_measure(wid, build):
    """One candidate partner at the board's RL 150 build: {'id', 'name', 'affinity', 'damage' (one
    motion-value-100 hit on the median defender, `er-builds-optimize.Scorer`, one-handed at max
    upgrade with the build's affinity when the weapon takes it, Standard otherwise), 'reach' (m,
    `er-mechanics-reach.weapon_length`), 'weight', 'scaling' (of the build's highest damage stat)}."""
    c = partner_context()
    t, opt = c['tables'], c['opt']
    name = t.names.get(wid)
    affs = opt.affinities(t, wid)
    aff = build.get('affinity') if build.get('affinity') in affs else ('Standard' if 'Standard' in affs else affs[0])
    lvl = t.max_level(t.weapons[t.find_weapon(name, aff)]['reinforceTypeId'])
    stats = build['stats']
    dmg = opt.Scorer(t, name, aff, lvl, False, 'damage', c['dfn']).score(stats)
    top = max(c['inf'].STATS, key=lambda k: stats.get(k, 0))
    return {'id': wid, 'name': name, 'affinity': aff, 'damage': round(dmg, 1),
            'reach': c['reach'].weapon_length(c['rc'], wid), 'weight': t.weapons[wid]['weight'],
            'stat': top, 'scaling': c['inf'].scaling(t, name, aff).get(top, 0.0)}


#: How much better a partner must be on an axis to count as better (`best_partner`).
PARTNER_MARGIN = {'damage': 0.005, 'reach': 0.05, 'weight': 0.5, 'scaling': 1.0}


def partner_axes(m, ref):
    """{axis: +1 better, -1 worse, 0 level} of candidate `m` against the observed weapon `ref`."""
    def cmp(diff, margin):
        return 1 if diff > margin else -1 if diff < -margin else 0
    out = {'damage': cmp(m['damage'] / ref['damage'] - 1 if ref['damage'] else 0, PARTNER_MARGIN['damage']),
           'weight': cmp(ref['weight'] - m['weight'], PARTNER_MARGIN['weight']),
           'scaling': cmp(m['scaling'] - ref['scaling'], PARTNER_MARGIN['scaling'])}
    if m['reach'] is not None and ref['reach'] is not None:
        out['reach'] = cmp(m['reach'] - ref['reach'], PARTNER_MARGIN['reach'])
    return out


def pick_partner(ref, cands):
    """The best partner of the observed weapon `ref` among `cands` (measures, `ref` included):
    damage first, every candidate within 1% of the most damage counted level on it, and among
    those the one that beats `ref` on the most axes net of the ones it loses (reach, weight,
    scaling); `ref` itself wins a tie there, and the most damage breaks the rest."""
    top = max(c['damage'] for c in cands)
    level = [c for c in cands if c['damage'] >= 0.99 * top]

    def key(c):
        ax = partner_axes(c, ref)
        return (sum(ax.values()), c['id'] == ref['id'], c['damage'])
    return max(level, key=key)


def partner_text(best, ref):
    """The axes `best` beats the observed weapon on, in short numbers."""
    ax = partner_axes(best, ref)
    out = []
    if ax.get('damage') == 1:
        out.append(f"{(best['damage'] / ref['damage'] - 1) * 100:.0f}% more damage vs average defences")
    if ax.get('reach') == 1:
        out.append(f"+{best['reach'] - ref['reach']:.2f} m reach")
    if ax.get('weight') == 1:
        out.append(f"{ref['weight'] - best['weight']:g} lighter")
    if ax.get('scaling') == 1:
        out.append(f"{best['stat'].upper()} scaling {best['scaling']:.0f} against {ref['scaling']:.0f}")
    return out


def best_partner(wid, partner_ids, build):
    """{'observed', 'best', 'beats'} for the powerstance row, or None without a build."""
    if not build or not build.get('stats'):
        return None
    cands = []
    for o in dict.fromkeys([wid, *partner_ids]):
        try:
            cands.append(partner_measure(o, build))
        except (Exception, SystemExit):                 # a row the AR tables cannot evaluate
            if o == wid:
                return None
    ref = cands[0]
    best = pick_partner(ref, cands)
    return {'observed': ref, 'best': best, 'beats': partner_text(best, ref) if best['id'] != wid else []}


def powerstance(d, wid, build=None):
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
    adds = f"adds a {best['label']} that hits on frame {best['first_hit']:g} ({best['rank_text']})." if best else ''
    if best:
        line += ' ' + adds
    out = {'partners': [d.weapon_name(o) for o in partner_ids], 'moves': moves, 'heading': head, 'text': line}
    # The best partner (user 2026-10-04: "you should always suggest the best variation"): named
    # with the axes it beats this weapon on, or this weapon again when nothing does.
    choice = best_partner(wid, partner_ids, build)
    if choice:
        out['partner'] = choice
        b = choice['best']
        if b['id'] != wid:
            out['heading'] = b['name']
            out['text'] = (f"Powerstance with {b['name']} rather than a second {name}: "
                           f"{', '.join(choice['beats'])}." + (f" The pair {adds}" if adds else ''))
    return out


#: Cross-hand pairs (`er-mechanics-combo.py`): a right-hand attack cancelled into the left
#: weapon's off-hand L1 #1. Measured on the mirror 2026-10-04: 40 of the 48 one-handed builds
#: with a Hand Axe in the left primary slot hold a right weapon with a true link into its L1,
#: against 130 of the 330 base weapons -- the pair is chosen for the link.
_COMBO = {}
#: A link is ranked by margin / windup ** WINDUP_POWER. The Crozier's charged R2 into a Hand Axe
#: has the most room of any opener (7.1 frames: its knockdown, reaction level 9, opens the escape
#: at 63 frames against 25 for a level 2) but a 50.3-frame windup, and the user ranks it below the
#: halberd R1 #1 (1.1 frames, 16 of windup) and the Cipher Pata running R2 (2.1, 15.9) (2026-10-04:
#: "make the windup penalty much stronger"). The halberd passes it above a power of 1.63. Measured
#: on the mirror, the share of used over unused (right, off-hand) pairs the score orders correctly
#: over the seven most used off-hands is 0.606 at 2 against 0.602 at 1, so the planner data does
#: not object.
WINDUP_POWER = 2


def combo_model():
    if not _COMBO:
        _COMBO['mod'] = cm = _mod('er_mechanics_combo', 'er-mechanics-combo.py')
        _COMBO['model'] = m = cm.Model()
        reg = m.reg
        _COMBO['weapons'] = ws = cm.base_weapons(reg)
        _COMBO['lefts'] = [w for w in ws if reg.weapon[w]['wepmotionCategory'] not in cm.LEFT_NO_ATTACK
                           and reg.weapon[w]['wepmotionCategory'] not in cm.psg().GUARD_LEFT_ONE_HAND
                           and m.offhand(w).get('left_1')]
    return _COMBO


def cross_link(right, left):
    """The best true link from a right-hand opener into `left`'s off-hand L1 #1, or None: margin =
    frames between the L1's first hit and the escape the opener's reaction allows (roll or guard,
    victim poise broken, `on_break`), larger is safer. Only pairs whose L1 is an off-hand attack
    (`left_mode` 'offhand': no guard, no powerstance)."""
    c = combo_model()
    cm, m = c['mod'], c['model']
    if right == left or cm.left_mode(m.reg, right, left) != 'offhand':
        return None
    rows = m.rows(right)
    true = []
    for lk in m.cross_links(right, left):
        if lk['next'] != 'left_1' or lk['on_break']['verdict'] != 'true':
            continue
        # Windup: frames from the press to the opener's first hit, its setup's exposed frames
        # included (`slot_setup`). A charged R2 is all windup (user, 2026-10-04: "charging an R2
        # is a long windup"), which the margin alone rewards: the Prelate's Inferno Crozier's
        # charged R2 has the most room of any opener into a Hand Axe and nobody throws it.
        s = slot_setup(rows[lk['first']], SLOT_FAMILY.get(lk['first']))
        margin = round(lk['on_break']['escape'] - lk['gap'], 1)
        windup = round(s['startup'] + s['exposed'], 1)
        true.append({'opener': lk['first'], 'label': rows[lk['first']]['label'], 'gap': lk['gap'],
                     'escape': lk['on_break']['escape'], 'stagger': lk['stagger'], 'margin': margin,
                     'windup': windup,
                     'rate': round(margin / windup ** WINDUP_POWER, 4) if windup > 0 else margin})
    if not true:
        return None
    best = max(true, key=lambda t: (t['rate'], t['stagger'] or 0))
    # The guaranteed follow-up's size, `pair_value`'s proxy: the L1's AR x motion values at max
    # upgrade and 40 STR/DEX, times the share of the window's builds the opener staggers.
    damage = cm.damage_proxy(m.reg, left, m.offhand(left)['left_1'])
    return dict(best, openers=[t['label'] for t in true if t['rate'] == best['rate']],
                damage=round(damage, 1), value=round(damage * (best['stagger'] or 0), 1))


def _partners(d, wid, side, top):
    """`side` 'left': off-hands `wid` (right hand) links into; 'right': right weapons that link
    into `wid`'s L1. Grouped by the partner's L1 clip (off-hands sharing it link identically)."""
    c = combo_model()
    reg = c['model'].reg
    pool = c['lefts'] if side == 'left' else c['weapons']
    found = []
    for o in pool:
        if o not in reg.weapon:
            continue
        lk = cross_link(wid, o) if side == 'left' else cross_link(o, wid)
        if lk:
            found.append((o, lk))
    # Frames of room per frame of windup first: the corpus picks the pair for the link (Zweihander
    # + Hand Axe), and a bigger follow-up only breaks the tie.
    found.sort(key=lambda x: (-x[1]['rate'], -x[1]['value'], reg.weapon_names[x[0]]))
    out, groups = [], {}
    for o, lk in found:
        key = (c['model'].offhand(o)['left_1']['anim'] if side == 'left'
               else c['model'].rows(o)[lk['opener']]['anim'], lk['margin'])
        if key in groups:
            groups[key]['same'].append(d.weapon_name(o))
            continue
        groups[key] = row = {'name': d.weapon_name(o), 'id': o, **lk, 'same': []}
        out.append(row)
    return {'pairs': len(found), 'of': len(pool), 'best': out[:top]}


def _cover_partners(d, wid, side, top):
    """Roll cover (`er-mechanics-combo.roll_cover`, chased): `side` 'left' is the off-hands whose L1
    catches a roll after one of `wid`'s openers, 'right' the right weapons whose openers set up
    `wid`'s L1. Each pair keeps its best opener by (cases caught, metres run); partners that share
    the L1 clip (left) or the opener clip (right) with the same result are grouped. The board
    shows this beside the true-combo partner: they answer different defenders, and the user ranks
    knives (true combo) and axes and stone clubs (roll cover) as equally good off-hands for a
    halberd (2026-10-04)."""
    c = combo_model()
    cm, m = c['mod'], c['model']
    reg = m.reg
    rc = c.setdefault('rc', cm.reach_module().Reach())
    pool = c['lefts'] if side == 'left' else c['weapons']
    found = []
    for o in pool:
        if o == wid or o not in reg.weapon:
            continue
        right, left = (wid, o) if side == 'left' else (o, wid)
        try:
            rows = m.rows(right)
        except Exception:                               # weapon rows the attack module cannot read
            continue
        best = None
        for key in cm.OPENERS:
            if key not in rows:
                continue
            x = cm.roll_cover(m, right, left, key, rc)
            if x and x.get('feasible') and (best is None or (x['share'], x['run_m']) > (best['share'], best['run_m'])):
                best = x
        if best and best['share'] > 0:
            found.append((o, best))
    found.sort(key=lambda x: (-x[1]['share'], -x[1]['run_m'], reg.weapon_names[x[0]]))
    out, groups = [], {}
    for o, x in found:
        clip = m.offhand(o)['left_1']['anim'] if side == 'left' else m.rows(o)[x['opener']]['anim']
        key = (clip, x['share'], x['run_m'])
        if key in groups:
            groups[key]['same'].append(d.weapon_name(o))
            continue
        groups[key] = row = {'name': d.weapon_name(o), 'id': o, 'opener': x['opener'], 'label': x['label'],
                             'caught': sum(x['caught'].values()), 'cases': len(x['caught']),
                             'run_m': x['run_m'], 'press': x['press'], 'hit': x['hit'], 'same': []}
        out.append(row)
    return {'pairs': len(found), 'of': len(pool), 'best': out[:top]}


def cross_hand(d, wid, top=3):
    """Both directions of the cross-hand link for one weapon: as the right hand ('offhands') and,
    when its own L1 is an off-hand attack, as the left ('mains'). None for a weapon with neither."""
    c = combo_model()
    if wid not in c['model'].reg.weapon:
        return None
    try:
        offhands = _partners(d, wid, 'left', top) if c['model'].rows(wid) else None
    except Exception:                                   # weapon rows the attack module cannot read
        offhands = None
    mains = _partners(d, wid, 'right', top) if wid in c['lefts'] else None
    try:
        cover_off = _cover_partners(d, wid, 'left', top) if c['model'].rows(wid) else None
    except Exception:                                   # weapon rows the attack module cannot read
        cover_off = None
    cover_main = _cover_partners(d, wid, 'right', top) if wid in c['lefts'] else None
    parts = {'offhands': offhands, 'mains': mains, 'cover_offhands': cover_off, 'cover_mains': cover_main}
    if not any(p and p['best'] for p in parts.values()):
        return None
    return parts


def build():
    d = AFF.Data()
    tables = AR.Tables()
    return d, Population(d, tables), Gear(d), tables


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
    # Scoring checks run ungated; the gate has its own checks below.
    lance = rank(d, pop, gear, tables, 'Lance', top=60, gate=False)
    names = {r['name']: r for r in lance['gear']}
    assert 'Ritual Sword Talisman' not in names, 'HP-gated rows are not weapon synergy'
    assert 'Lance Talisman' not in names, 'horseback is left out'
    # Lance builds no status and takes ashes: infusion and ash gear is open to every such weapon.
    for n in ('White Mask', "Lord of Blood's Exultation", "Kindred of Rot's Exultation", 'Shard of Alexander',
              'Fire Scorpion Charm'):
        assert n not in names, (n, names.get(n))
    # Lance's slow R1 string barely outpaces the decay; Backhand Blade's two blades fill it fast.
    # Powerstanced (two Lances, both thrusts landing) it fills the counter, still below the median.
    assert lance['vector']['successive'] < 0.5, lance['vector']
    assert successive(d, d.find_weapon('Lance'))['grip'] == 'dual'
    bhb = rank(d, pop, gear, tables, 'Backhand Blade', top=60, gate=False)
    # Above Lance, but powerstanced daggers (17.3 a second net) now outpace its 11.4.
    assert bhb['vector']['successive'] > lance['vector']['successive'], (bhb['vector'], lance['vector'])
    dg = successive(d, d.find_weapon('Dagger'))
    assert dg['grip'] == 'dual' and dg['net_per_s'] > successive(d, d.find_weapon('Backhand Blade'))['net_per_s'], dg
    rw = 'Rotten Winged Sword Insignia'
    bnames = {r['name']: r for r in bhb['gear']}
    assert rw in bnames and (rw not in names or names[rw]['score'] < bnames[rw]['score']), (names.get(rw), bnames.get(rw))
    # Tiers of one talisman (one accessoryGroup) never both list; the higher tier wins.
    for weapon in ('Backhand Blade', 'Misericorde', 'Lance'):
        listed = {r['name'] for r in rank(d, pop, gear, tables, weapon, top=60)['gear']}
        assert not {'Warrior Jar Shard', 'Shard of Alexander'} <= listed, (weapon, listed)
        assert not {'Winged Sword Insignia', 'Rotten Winged Sword Insignia'} <= listed, (weapon, listed)
    # All three successive-hit talismans feed the channel, and Millicent's also its DEX.
    for n in ('Winged Sword Insignia', 'Rotten Winged Sword Insignia', "Millicent's Prosthesis"):
        assert any(b['channel'] == 'successive' for b in gi[n]['benefits']), (n, gi[n]['benefits'])
    assert any(b['channel'] == CHAIN_CHANNEL for b in gi['Twinblade Talisman']['benefits'])
    # Powerstance doubling: Cross-Naginata's dual L1 lands both blades (+8 each), so its fastest
    # grip is the powerstance; Backhand Blade is powerstanced by itself, so its grip is two-handed
    # and the board calls it powerstanced.
    xn = AFF.weapon_profile(d, d.find_weapon('Cross-Naginata'))
    assert successive(d, xn['id'], xn)['grip'] == 'dual', successive(d, xn['id'], xn)
    bs = successive(d, d.find_weapon('Backhand Blade'))
    assert bs['grip'] == 'both' and bs['paired'] and chain_grip_text(bs) == 'powerstanced R1', bs
    # Twinblade Talisman: the Twinblade's chain is fastest powerstanced (dual_3 at 2.2 s, both hands),
    # and every weapon with a chain grip gets a percentile.
    tw = chain(d, tables, d.find_weapon('Twinblade'))
    assert tw['grip'] == 'dual' and tw['to_chain_s'] == 2.2 and tw['chain_hits'] == 2, tw
    assert len(pop.chain) > 400, len(pop.chain)
    assert chain(d, tables, d.find_weapon('Longbow')) is None
    uchi = {r['name'] for r in rank(d, pop, gear, tables, 'Uchigatana', top=60, gate=False)['gear']}
    assert "Lord of Blood's Exultation" in uchi and 'Shard of Alexander' not in uchi, uchi
    rob = {r['name'] for r in rank(d, pop, gear, tables, 'Rivers of Blood', top=60, gate=False)['gear']}
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
    # A weapon with any piercing hit always lists Spear Talisman, however far down it ranks.
    mis1 = rank(d, pop, gear, tables, 'Misericorde', top=1)
    assert mis1['vector']['pierce'] > 0 and any(
        r['name'] == 'Spear Talisman' for r in mis1['gear']), mis1['gear']
    # Setup discount: neutral channels whole, a prior input discounted by its exposed frames.
    sf = setup_frames()
    assert sf['roll']['iframes'] > 0 and sf['roll']['exposed'] == round(sf['roll']['ready'] - sf['roll']['iframes'], 1)
    assert sf['backstep']['iframes'] == 0 and sf['backstep']['exposed'] == sf['backstep']['ready'], sf['backstep']
    assert sf['run']['exposed'] == 0, sf['run']
    after = rank(d, pop, gear, tables, 'Misericorde', top=60, gate=False)
    before = rank(d, pop, gear, tables, 'Misericorde', top=60, setup=False, gate=False)
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
    # Cross-hand into a Hand Axe's off-hand L1: the mirror's picks (Beast Claw R1 #3, the
    # Zweihander's rolling R1) lead, and the Crozier's charged R2, the most room (7.1 frames) behind
    # a 50.3-frame windup, ranks below the halberd's R1 #1 and the Cipher Pata's running R2.
    ha = _partners(d, d.find_weapon('Hand Axe'), 'right', 400)
    by = {n: r for r in ha['best'] for n in [r['name'], *r['same']]}
    assert ha['best'][0]['name'] == 'Beast Claw' and by['Beast Claw']['margin'] == 4.1, ha['best'][0]
    zw = by['Zweihander']
    assert zw['margin'] == 6.1 and zw['windup'] == 21.0 and ha['best'].index(zw) < 4, zw
    crozier = by["Prelate's Inferno Crozier"]
    assert crozier['margin'] == 7.1 and crozier['rate'] < by["Banished Knight's Halberd"]['rate'] \
        < by['Cipher Pata']['rate'], (crozier, by["Banished Knight's Halberd"], by['Cipher Pata'])
    # Percentile gate: in the top 35% of the item's scores over the weapons it engages, ties level.
    ten = [x / 10 for x in range(1, 11)]
    assert passes_gate(ten, 0.7) and not passes_gate(ten, 0.6), (ahead_share(ten, 0.7), ahead_share(ten, 0.6))
    assert passes_gate([0.1] * 9 + [0.2], 0.1) is True and ahead_share([0.1] * 9 + [0.2], 0.1) == 0.1
    scores = pop.item_scores(gear)
    assert len(scores['Claw Talisman']) > 400, len(scores['Claw Talisman'])
    gs_heavy = {'ar_rank': (7, 0, 416), 'ar': 955.6, 'top_physical': 'Heavy', 'affinity': 'Heavy', 'stats': None}
    lance_g = rank(d, pop, gear, tables, 'Lance', top=60, build=dict(gs_heavy, ar_rank=(114, 0, 416)))
    assert all(r['ahead_share'] < GATE_SHARE for r in lance_g['gear']), lance_g['gear']
    dropped = {g['name']: g for g in lance_g['gated']}
    # Lance's charged R2s are slow for their motion value: 78% of weapons get more from Axe Talisman.
    assert 'Axe Talisman' in dropped and 'two_hand' not in dropped['Axe Talisman'], dropped.get('Axe Talisman')
    # Claw Talisman: Dagger's jump attacks are among the best, Greatsword's among the worst.
    claw = scores['Claw Talisman']
    dg_claw = synergy(gear.items['Claw Talisman'], pop.vectors()[d.find_weapon('Dagger')])[0]
    gsw_claw = synergy(gear.items['Claw Talisman'], pop.vectors()[d.find_weapon('Greatsword')])[0]
    assert passes_gate(claw, dg_claw) and not passes_gate(claw, gsw_claw), (dg_claw, gsw_claw)
    # Two-handing gate, one condition failing at a time.
    assert two_hand_verdict((7, 0, 416), 'Heavy', 1.14)['pass']
    assert two_hand_verdict((307, 0, 416), 'Heavy', 1.14)['checks'] == {'ar': False, 'heavy': True, 'moveset': True}
    assert two_hand_verdict((7, 0, 416), 'Keen', 1.14)['checks'] == {'ar': True, 'heavy': False, 'moveset': True}
    assert two_hand_verdict((7, 0, 416), 'Heavy', 0.99)['checks'] == {'ar': True, 'heavy': True, 'moveset': False}
    assert not two_hand_verdict(None, 'Heavy', 1.14)['pass']
    # On real movesets: Greatsword's two-handed moveset is worth more than its one-handed one,
    # Uchigatana's slightly less.
    th = two_hand_check('Greatsword', 'Two-Handed Sword Talisman', gs_heavy)
    assert th['pass'] and th['moveset']['ratio'] > 1.1, th
    uh = two_hand_check('Uchigatana', 'Two-Handed Sword Talisman', dict(gs_heavy, ar_rank=(146, 0, 416)))
    assert uh['checks'] == {'ar': True, 'heavy': True, 'moveset': False}, uh
    ax = two_hand_check('Greatsword', 'Axe Talisman', gs_heavy)
    assert ax['charged_only'] and set(ax['moveset']['slots']) <= {'r2_1c', 'r2_2c'}, ax
    gsr = rank(d, pop, gear, tables, 'Greatsword', top=60, build=gs_heavy)
    assert 'Two-Handed Sword Talisman' in {r['name'] for r in gsr['gear']}, gsr['gated']
    bare = {g['name']: g for g in rank(d, pop, gear, tables, 'Greatsword', top=60)['gated']}
    assert bare['Two-Handed Sword Talisman']['why'].startswith('no RL 150 build'), bare['Two-Handed Sword Talisman']
    # Ammunition: Arrow's Sting reaches a bow through the ammo channel and never a sword; Arrow's
    # Reach (range, not damage) is listed beside the scored rows on the bow only.
    assert [b['channel'] for b in gi["Arrow's Sting Talisman"]['benefits']] == [AMMO_CHANNEL]
    bow = rank(d, pop, gear, tables, 'Longbow', top=60)
    assert "Arrow's Sting Talisman" in {r['name'] for r in bow['gear']}, (bow['gear'], bow['gated'])
    assert "Arrow's Reach Talisman" in {r['name'] for r in bow['ranged']}, bow['ranged']
    sword = rank(d, pop, gear, tables, 'Longsword', top=60)
    assert AMMO_CHANNEL not in sword['vector'] and not sword['ranged'], sword['vector']
    assert "Arrow's Sting Talisman" not in {r['name'] for r in sword['gear'] + sword['gated']}
    # Magnitude floor: Starscourge Heirloom's STR on a Shotel adds 1% or less, so it goes.
    sh = synergy(gi['Starscourge Heirloom'], pop.vectors()[d.find_weapon('Shotel')])
    assert sh[1] and net_benefit(sh[1]) <= BENEFIT_FLOOR + 5e-5, net_benefit(sh[1])
    shotel = rank(d, pop, gear, tables, 'Shotel', top=60, min_score=-1.0)
    floor = {g['name']: g['why'] for g in shotel['gated'] if g['why'].startswith('adds ')}
    assert 'Starscourge Heirloom' in floor and 'Starscourge Heirloom' not in {r['name'] for r in shotel['gear']}, floor
    assert all(r['net_benefit'] > BENEFIT_FLOOR for r in shotel['gear'] + lance_g['gear'])
    # Powerstance partner: at a Heavy STR build a Broadsword out-damages a Longsword, so the
    # Longsword's row names it; nothing in its class beats an Uchigatana, which keeps its copy.
    str_build = {'affinity': 'Heavy', 'stats': {'vig': 60, 'mnd': 15, 'vit': 30, 'str': 80, 'dex': 18,
                                                'int': 9, 'fth': 9, 'arc': 7}}
    ls = powerstance(d, d.find_weapon('Longsword'), str_build)
    assert ls['heading'] == 'Broadsword' and ls['text'].startswith(
        'Powerstance with Broadsword rather than a second Longsword: ') and 'more damage' in ls['text'], ls['text']
    up = powerstance(d, d.find_weapon('Uchigatana'), str_build)
    assert up['partner']['best']['name'] == 'Uchigatana' and up['heading'] != 'Uchigatana' and up['text'].startswith(
        'Powerstance with another Uchigatana'), (up['heading'], up['text'])
    assert pick_partner({'id': 1, 'damage': 100, 'reach': 1.0, 'weight': 5, 'scaling': 100},
                        [{'id': 1, 'damage': 100, 'reach': 1.0, 'weight': 5, 'scaling': 100},
                         {'id': 2, 'damage': 100.3, 'reach': 1.0, 'weight': 5, 'scaling': 100}])['id'] == 1
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
        for c in (ps.get('partner') or {}).get('observed'), (ps.get('partner') or {}).get('best'):
            if c:
                print(f"    {c['name']:<28} {c['affinity']:<9} damage {c['damage']:g}  reach {c['reach']}  "
                      f"weight {c['weight']:g}  {c['stat'].upper()} scaling {c['scaling']:g}")
    for side, part in (out['cross_hand'] or {}).items():
        if part and part['best'] and side.startswith('cover'):
            print(f"  roll cover {side[6:]}: {part['pairs']} of {part['of']} catch a mashed roll")
            for r in part['best']:
                print(f"    {r['name']:<34} {r['label']}: {r['caught']} of {r['cases']} cases, run {r['run_m']:g} m, "
                      f"L1 at {r['press']:g}, hit {r['hit']:g}, +{len(r['same'])} sharing it")
        elif part and part['best']:
            print(f"  cross-hand {side}: {part['pairs']} of {part['of']} link into the off-hand L1")
            for r in part['best']:
                print(f"    {r['name']:<34} {' / '.join(r['openers'])}: windup {r['windup']:g} f, margin {r['margin']:g} f, "
                      f"L1 {r['damage']:g}, +{len(r['same'])} sharing it")
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
        pin = '  (always listed: its hits pierce)' if r.get('pinned') == 'pierce' else ''
        print(f"  {r['score']:+.3f} {r['name']:<34} {r['kind']:<8} {ch}{cost} {'; '.join(r['other_costs'])}{pin}")
        th = r.get('two_hand')
        if th:
            print(f"         AR rank {th['ar_rank']}, top physical {th['top_physical']}, "
                  f"2H/1H moveset x{th['moveset']['ratio']}")
    for g in out['gated']:
        print(f"  dropped {g['name']}: {g['why']}")
    for g in out['ranged']:
        print(f"  range: {g['name']} +{g['bowDistRate']}% (row {g['row']})")
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
