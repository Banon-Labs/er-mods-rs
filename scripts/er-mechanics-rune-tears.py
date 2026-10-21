#!/usr/bin/env python3
"""The great rune and the two crystal tears for one weapon build, chosen from every one the
regulation has, by what each does for that build.

    python3 scripts/er-mechanics-rune-tears.py "Poleblade of the Bud" --affinity Standard \\
        --stats vig=58,mnd=8,end=38,str=20,dex=22,int=7,fth=8,arc=68
    python3 scripts/er-mechanics-rune-tears.py Uchigatana --affinity Keen --two-handed \\
        --stats vig=50,mnd=10,end=30,str=18,dex=60,int=9,fth=9,arc=12 --fight duel --role host --json
    python3 scripts/er-mechanics-rune-tears.py --selftest

The pool is read, never listed: every `EquipParamGoods` row with `goodsType` 15 (great runes) or
10 (crystal tears) that has a name, one row per name. A great rune's rows are the SpEffect rows
Smithbox names after it, `[Item] <rune>: Effect 0` onwards, which is the 600 + 10g stride
`ApplyRuneArcEffects` applies (buffs.md section 4; the link is `INFERRED` from names and stride,
as `er-mechanics-resources.GREAT_RUNE_SPEFFECT` notes). A tear's rows are its goods refs walked
through `Buffs._closure`.

Every kit, one rune and two different tears, is scored for the build being made:

| term | what it is | where it comes from |
| --- | --- | --- |
| offense | the focus weapon's motion-value-100 hit on the RL window's median defender with the kit, over the hit without it, less 1 | `Buffs.kit_factors` (rows by role, uptime over the fight model's lengths, physick never recast), attribute adds through the weapon's own AR (rounded the way `er-mechanics-buffs.ar_stat_ratio` rounds them), `er-mechanics-defense.damage` |
| defense | 1 - kept / HP rate. Kept is the share of the window's incoming PvP damage that still lands: the kit's `cut x correct` per damage type, uptime-weighted, over `er-mechanics-armor-targets.damage_mix`'s per-type shares, times the defense-curve change its attribute adds make (`damage_mix` again at the raised defense). HP rate is the max HP with the kit's rows over without (`er-mechanics-resources`, VIG adds included) | `Buffs.defense_context`, `Buffs.planned_uptime`, `damage_mix`, `Model.resources` |
| sustain | the same quotient with the kit's heals and drains added to HP: heals over time (`Buffs.regen_row`) and a 0 s row a live row cycles (Bloodsucking Cracked Tear's HP loss), over buffs.md section 11's `window` fold, `FIGHT_ENGAGEMENTS` x `ENGAGEMENT_SECONDS` = 25 s; divided by the fight model's attackers (below) | `Buffs.regen_hp` |

    total = offense + defense + sustain = offense - (kept / effective HP rate - 1)   (one attacker)

That is the combination `er-mechanics-gear-synergy.py` already scores gear with: a damage gain
less its survivability cost, `damage taken / max HP - 1`, "the one downside every item can be
put in the same unit as damage". No weight is added: one percent more damage dealt and one
percent less damage kept per HP count the same. The best total is the pick; equal totals go to
the kit the window's PvP builds carry more often (`er-mechanics-armor-targets.adoption`).

The fight model (`fight`, `--fight`) is one of `er-mechanics-buffs.FIGHT_KINDS`. `invasion`, the
default, is 10 s to 1 h (`fight_kind_points`, log-spaced) against five attackers and three hosts'
HP bars, each drinking the window's median crimson flasks (`er-mechanics-pouch.Pouch._fight`
builds it the same way); `duel` is the 180-300 s fight against one, the window's duel and flask
fights mixed by their tag shares. Uptimes and recasts are read over the model's lengths, and a
tear is one physick charge per encounter (`TEAR_USES`), so in a long invasion it covers less.

Several attackers: the outcome is the damage dealt before dying, offense x effective HP /
(attackers x incoming rate). A cut or a max HP rate scales effective HP at any attacker count, so
`defense` has no attacker term. A flat heal or drain, HP per second, offsets heal rate /
(attackers x incoming rate) of the drain, so `sustain` is the one-attacker quotient above divided
by the model's `attackers`: a fifth of the duel's in an invasion. In that race the heal's term is
heal rate / (attackers x incoming rate x HP rate), linear in 1 / attackers.

The role (`role`) decides which rune rows are live (buffs.md section 4: a host gets 600+10g..,
an invader only 609+10g and only for Mohg's rune, a cooperator nothing). Unless one is asked for,
the role is the fight model's (`FIGHT_KINDS[fight]['role']`): invader in an invasion, where only
Mohg's rows are live. A duel keeps `corpus`, its default before the fight models: the role mix
of the window's PvP builds read from their planner tags (`ROLE_BY_TAG`: Invasions is invader,
every other PvP tag and no tag is host), the same split `er-mechanics-buffs.corpus_kits` gives
every kit in `er-builds-pvp.py`'s buff term. `corpus`, `host`, `invader`, `cooperator` or a
{role: weight} dict score that role or mix under either model.

A row that applies only while its owner holds a state (`invocationConditionsStateChange*`) is
left out of every term: `Buffs` would apply it as if always on, and when the state holds is not
modelled.

What the model cannot value is said, per item, in `unvalued` and in the CLI's table, and
contributes nothing to the total: FP, stamina and equip load (Godrick's MND/END, Radahn's FP and
stamina, Greenspill, Greenburst, Cerulean and Winged tears), poise and stagger, status
resistance and cures, rows gated on an attack sub-category (Spiked: charged heavies), a state
(Crimsonwhorl) or an HP threshold (Crimson Bubbletear's one heal below 20% HP), accumulator
tiers that live 1.5 s (Thorny), behaviors a row spawns (Ruptured, Oil-Soaked), instant
restores (the physick is drunk before contact, at full HP and FP), the great rune `stateInfo`
effects whose numbers are not traced (Rykard 199, Mohg 441, Malenia 449), and the status
build-up attribute adds raise on the focus weapon (reported, not scored). Those item names are
examples of what the column reader finds; the reader itself names no item.
"""
from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
import math
import os
import re
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.expanduser('~/.cache/er-build-planner/builds.jsonl')
#: `EquipParamGoods.goodsType` of a great rune and of a crystal tear (Smithbox `GOODS_TYPE`).
GREAT_RUNE_GOODS_TYPE = 15
CRYSTAL_TEAR_GOODS_TYPE = 10
#: Tears the Flask of Wondrous Physick holds (two different ones, `COMMUNITY`).
TEAR_SLOTS = 2
#: `stats` sum minus this is the rune level (`er-builds-optimize.LEVEL_OFFSET`).
LEVEL_OFFSET = 79
DAMAGE_STATS = ('str', 'dex', 'int', 'fth', 'arc')
#: `vit` is the planner's spelling of Endurance, still taken for `end`.
STAT_ALIAS = {'vit': 'end'}
ELEMENTS = ('physical', 'magic', 'fire', 'lightning', 'holy')
#: `er-mechanics-armor-targets.TYPES`: the incoming-damage shares are keyed by these.
DAMAGE_TYPES = ('slash', 'strike', 'pierce', 'standard', 'magic', 'fire', 'lightning', 'holy')
PHYS_TYPES = ('slash', 'strike', 'pierce', 'standard')
#: `er-mechanics-defense.defender` keys its armor multipliers with `physical` for standard.
ARMOR_KEY = {'standard': 'physical'}
ROLES = ('host', 'invader', 'cooperator')
#: `stateInfo` values whose effect the model reads: 50 is a heal over time (`Buffs.regen_row`
#: reads the row's own columns), 277 is Rune Arc's trigger (it only arms the rune rows).
READ_STATE_INFO = frozenset((50, 277))
#: Columns that set when or for how long a row lives, what it targets or links to, never what it
#: does; the classifier skips them (`unvalued`). Gates among them are read separately.
BOOKKEEPING_PREFIXES = ('effectTarget', 'vfx', 'pad', 'unknown', 'accumu', 'bAdjust', 'isUse', 'iconId')
BOOKKEEPING = frozenset((
    'id', '_off', 'effectEndurance', 'motionInterval', 'spCategory', 'categoryPriority', 'saveCategory',
    'cycleOccurrenceSpEffectId', 'replaceSpEffectId', 'eraseOnBonfireRecover', 'isContractSpEffectLife',
    'bCurrHPIndependeMaxHP', 'isWaitModeDelete', 'magParamChange', 'miracleParamChange', 'shamanParamChange',
    'wepParamChange', 'dmypolyId', 'deleteCriteriaDamage', 'isDisableNetSync', 'stateInfo', 'conditionHp',
    'conditionHpRate', 'magicSubCategoryChange1', 'magicSubCategoryChange2', 'magicSubCategoryChange3',
    'invocationConditionsStateChange1', 'invocationConditionsStateChange2', 'invocationConditionsStateChange3',
    'spAttribute', 'spAttributeVariationValue', 'isSilence', 'isIgnoreNoDamage', 'effectTargetSelfTarget'))
#: Columns the model has no damage-unit value for, by pattern, and what each changes.
UNVALUED_COLUMNS = (
    (r'^(maxMpRate|addWillpowerStatus|changeMp.*|.*ConsumptionRate)$', 'FP'),
    (r'^addEndureStatus$', 'Endurance: stamina and equip load'),
    (r'^(maxStaminaRate|staminaRecoverChangeSpeed|consumeStaminaRate|changeStamina.*)$', 'stamina'),
    (r'^equipWeightChangeRate$', 'equip load'),
    (r'^(saAttackPowerRate|staminaAttackRate)$', 'poise and stamina damage dealt'),
    (r'^(toughnessDamageCutRate|dmgLv_.*|changeSaRecoveryVelocity|saReceiveDamageRate)$', 'poise and stagger taken'),
    (r'^change.*ResistPoint$', 'status resistance'),
    (r'^behaviorId$', 'spawns a behavior (an attack or effect the model does not read)'),
    (r'^change(Hp|Mp)EstusFlask.*$', 'flask healing'),
    (r'^applyIdOnGetSoul$', 'an effect applied on a kill'),
)
#: How many kits the ranking keeps.
TOP = 10


def _mod(name, fname):
    s = importlib.util.spec_from_file_location(name, os.path.join(HERE, fname))
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


B = _mod('er_mechanics_buffs_rune_tears', 'er-mechanics-buffs.py')
SUSTAIN_SECONDS = B.FIGHT_ENGAGEMENTS * B.ENGAGEMENT_SECONDS


# --------------------------------------------------------------------------------------------
# the pool

def great_runes(m):
    """[{'name', 'goods', 'base', 'rows'}] for every named `goodsType` 15 good, in goods order.
    `base` is the first SpEffect row Smithbox names `[Item] <name>: ...`; `rows` are base..base+9.
    Each rune is also made a `Buffs` source (`great rune:<name>`) when `Buffs` has none, with the
    rule `Buffs._sources` applies: host rows base..base+8 that are relevant or carry a
    `stateInfo`, invader row base+9 when it has a category."""
    out = []
    for gid, g in sorted(m.goods.items()):
        name = m.goods_name.get(gid)
        if g['goodsType'] != GREAT_RUNE_GOODS_TYPE or not name:
            continue
        prefix = f'[Item] {name}:'
        named = sorted(i for i, n in m.sp_names.items() if n and n.startswith(prefix))
        if not named:
            out.append({'name': name, 'goods': gid, 'base': None, 'rows': []})
            continue
        base = named[0]
        if f'great rune:{name}' not in m.by_name:
            eff = [(i, 'ApplyRuneArcEffects') for i in range(base, base + 9)
                   if i in m.sp and (m.relevant(i) or m.sp[i]['stateInfo'])]
            inv = [(base + 9, 'ApplyRuneArcEffects invader')] \
                if base + 9 in m.sp and m.sp[base + 9]['spCategory'] else []
            src = {'name': name, 'kind': 'great rune', 'goods': gid, 'entries': eff, 'invader': inv}
            m.sources.append(src)
            m.by_name.setdefault(name, src)
            m.by_name[f'great rune:{name}'] = src
        out.append({'name': name, 'goods': gid, 'base': base, 'rows': [i for i in range(base, base + 10) if i in m.sp]})
    return out


def crystal_tears(m):
    """[{'name', 'goods', 'rows': [(sid, via)]}] for every named `goodsType` 10 good, one per name
    (the lowest goods id: Crimson, Cerulean and Ruptured Crystal Tear have two rows each), the
    rows being everything the goods refs reach (`Buffs._closure`, relevant or not)."""
    out, seen = [], set()
    for gid, g in sorted(m.goods.items()):
        name = m.goods_name.get(gid)
        if g['goodsType'] != CRYSTAL_TEAR_GOODS_TYPE or not name or name in seen:
            continue
        seen.add(name)
        refs = B._goods_refs(m, gid, g)
        rows = m._closure(refs[1], refs[2]) if refs else []
        out.append({'name': name, 'goods': gid, 'rows': rows})
    return out


# --------------------------------------------------------------------------------------------
# the window: fight, roles, incoming damage, defender

def fight_schedule(rows, fight=B.DEFAULT_FIGHT, st=None):
    """(fight, info): the fight points and landed hits at each of fight model `fight`
    (`er-mechanics-buffs.FIGHT_KINDS`, its lengths `fight_kind_points`), from the window's median
    max HP and `FIGHT_REF_DAMAGE`. A duel is what `er-builds-pvp` `Mechanics.set_fight` builds by
    default (`flasks="corpus"`): no flask in a duel and the median crimson flasks in an invasion or
    gank, mixed by the window's tag shares (buffs.md section 10). An invasion is its own lengths
    against its `targets` hosts' HP bars, each drinking the median crimson flasks, as
    `er-mechanics-pouch.Pouch._fight` builds it. `rows` are `er-mechanics-status.corpus_rows`."""
    st = st or _status()
    kind = B.FIGHT_KINDS[fight]
    fs = tuple(B.fight_kind_points(fight))
    targets = kind['targets']
    hp = st.quantiles([r['hp'] for r in rows], (0.5,)).get(0.5, 0.0)
    v = sorted(int(r['flasks']['crimson']) for r in rows if (r.get('flasks') or {}).get('crimson') is not None)
    crimson = v[len(v) // 2] if v else B.CORPUS_CRIMSON

    def sched(k):
        return B.fight_hits(fs, hp * targets, st.FIGHT_REF_DAMAGE, k * targets, B.FLASK_HEAL_HP, B.FLASK_ETA,
                            st.ENGAGEMENT_SECONDS)

    if fight == 'duel':
        sh = B.tag_shares(rows)
        pts, hits = B.fight_mix([(sh['duel'], fs, sched(0)), (sh['flasks'], fs, sched(crimson))])
        duel_share = sh['duel']
    else:
        pts, hits, duel_share = fs, sched(crimson), 0.0
    return ({'fight_seconds': pts, 'hits': hits},
            {'kind': fight, 'attackers': kind['attackers'], 'targets': targets, 'seconds': [min(fs), max(fs)],
             'points': len(pts), 'median_hp': hp, 'crimson': crimson, 'duel_share': duel_share,
             'hits_mean': B.hits_mean(hits)})


def role_mix(rows):
    """{role: weight} of the window's PvP builds: each build split evenly over the roles its tags
    name (`ROLE_BY_TAG`), host when none, the mean over builds (`corpus_kits`'s per-kit roles)."""
    acc, n = {}, 0
    for r in rows:
        roles = {}
        for t in r.get('tags') or ():
            if t in B.ROLE_BY_TAG:
                roles[B.ROLE_BY_TAG[t]] = roles.get(B.ROLE_BY_TAG[t], 0) + 1
        roles = roles or {'host': 1}
        k = sum(roles.values())
        for role, c in roles.items():
            acc[role] = acc.get(role, 0.0) + c / k
        n += 1
    return {role: v / n for role, v in sorted(acc.items())} if n else {'host': 1.0}


def default_role(fight):
    """The role scored when none is asked for: the fight model's (`FIGHT_KINDS[fight]['role']`,
    invader in an invasion), except a duel, which keeps `corpus`, its default before the models."""
    return 'corpus' if fight == 'duel' else B.FIGHT_KINDS[fight]['role']


def resolve_role(role, mix=None):
    """{role: weight} for `role`: `corpus` is the window's `mix`, a role name is that role alone,
    a dict is normalised."""
    if isinstance(role, dict):
        tot = sum(role.values())
        return {k: v / tot for k, v in role.items() if v > 0}
    if role == 'corpus':
        return dict(mix or {'host': 1.0})
    if role not in ROLES:
        raise SystemExit(f'unknown role {role!r}; one of corpus, {", ".join(ROLES)}')
    return {role: 1.0}


_ST = {}


def _status():
    if 'm' not in _ST:
        _ST['m'] = _mod('er_mechanics_status_rune_tears', 'er-mechanics-status.py')
    return _ST['m']


class Context:
    """What every pick in one RL window reads, built once: the buff, resource, AR and defense
    models, the window's fights (one per fight model, `schedule`), role mix, adoption and
    incoming damage."""

    def __init__(self, rl=150, window=10, mirror=CORPUS, regulation=None):
        self.rl, self.window, self.mirror = rl, window, mirror
        self.m = B.Buffs(regulation)
        self.res = _mod('er_mechanics_resources_rune_tears', 'er-mechanics-resources.py').Model(regulation)
        self.AT = _mod('er_mechanics_armor_targets_rune_tears', 'er-mechanics-armor-targets.py')
        self.DEF, self.AR = self.AT.DEF, self.AT.AR
        self.tables = self.AR.Tables(regulation)
        self.dt = self.DEF.Tables(regulation)
        st = _status()
        rows = st.corpus_rows(mirror, rl, window)
        self.status_rows = rows
        self._sched = {}
        self.roles = role_mix(rows)
        lo, hi = rl - window, rl + window
        self.builds = self.AT.corpus_builds(lo, hi, Path(mirror))
        self.adoption = self.AT.adoption(self.builds)
        self.runes = great_runes(self.m)
        self.tears = crystal_tears(self.m)
        self.regen_src = self.m.regen_sources()
        self._reg = None
        self._mix = {}
        self._full = None
        self._defender = None
        self._row = {}

    def schedule(self, fight=B.DEFAULT_FIGHT):
        """`fight_schedule` of fight model `fight` in this window, (fight, info), built once."""
        if fight not in self._sched:
            self._sched[fight] = fight_schedule(self.status_rows, fight)
        return self._sched[fight]

    # -- incoming damage ------------------------------------------------------------------------
    def mix(self, defense):
        """`damage_mix` of the window's builds against flat `defense` (cached per defense)."""
        key = tuple(sorted(defense.items()))
        if key not in self._mix:
            if self._reg is None:
                self._reg = self.AT.ATK.Regulation(None)
            self._mix[key] = self.AT.damage_mix(self.builds, defense, self.tables, self._reg)
        return self._mix[key]

    def defender(self):
        """The window's median defender, the one `er-builds-optimize.weapon_best` scores a hit on."""
        if self._defender is None:
            opt = _mod('er_builds_optimize_rune_tears', 'er-builds-optimize.py')
            self._defender = opt.bracket_defender(opt.corpus_rows(Path(self.mirror), self.rl - self.window,
                                                                  self.rl + self.window))
        return self._defender

    def full_rows(self):
        """Every SpEffectParam column of every row (for `unvalued`), read once."""
        if self._full is None:
            PR = B.PR
            self._full = {r['id']: r for r in PR.rows(PR.param_bytes(self.m._files, 'SpEffectParam'), None)[0]}
            self._full_default = B._defaults('SpEffect')
        return self._full


# --------------------------------------------------------------------------------------------
# the build being made

class Build:
    """The focus weapon as built, its hit without buffs, the build's own HP and defense, and the
    fight model it is scored under (`fight`; `attackers`, default the model's, is what `sustain`
    is divided by)."""

    def __init__(self, ctx, weapon, affinity, level, stats, two_handed=False, talismans=(), armor=(),
                 grease=None, defender=None, fight=B.DEFAULT_FIGHT, attackers=None):
        self.ctx = ctx
        self.fight = fight
        self.sched, self.sched_info = ctx.schedule(fight)
        self.attackers = attackers or B.FIGHT_KINDS[fight]['attackers']
        self.weapon, self.affinity, self.level, self.two_handed = weapon, affinity, level, two_handed
        self.stats = {STAT_ALIAS.get(k, k): v for k, v in stats.items()}
        self.talismans, self.armor = tuple(talismans), tuple(armor)
        self.grease = grease
        self.defender = defender or ctx.defender()
        self._ar = {}
        r = self.rating({}, {})
        self.ar0, self.status0 = r
        self.hit0 = self.hit(self.ar0, dict.fromkeys(ELEMENTS, 1.0), dict.fromkeys(ELEMENTS, 1.0),
                             dict.fromkeys(ELEMENTS, 0.0))
        self.hp0 = ctx.res.resources(self.stats, self.talismans, self.armor)['max_hp']
        st = self._def_stats()
        me = ctx.DEF.defender(ctx.dt, st, self.armor, list(self.talismans), None, pvp=True)
        self.defense0 = {e: me['defense'][e] for e in ELEMENTS}
        self.armor_mult = me['armor_mult']
        base = ctx.mix(self.defense0)
        self.share = base['share']
        self.mean_hit0 = base['mean_hit']

    def _def_stats(self):
        return dict(self.stats)

    def rating(self, adds, rate_adds):
        """({element: AR}, {status: build-up}) with attribute `adds` rounded to whole points and
        scaling-rate adds kept fractional, as `er-mechanics-buffs.ar_stat_ratio` rounds them."""
        key = tuple(sorted((k, int(round(v))) for k, v in adds.items() if k in DAMAGE_STATS and int(round(v))))
        rkey = tuple(sorted((k, round(v, 6)) for k, v in (rate_adds or {}).items() if round(v, 6)))
        if (key, rkey) not in self._ar:
            st = {k: self.stats[k] for k in DAMAGE_STATS}
            for k, v in key:
                st[k] += v
            r = self.ctx.AR.attack_rating(self.ctx.tables, self.weapon, self.affinity, self.level, st,
                                          self.two_handed, dict(rkey) or None)
            self._ar[(key, rkey)] = ({e: r['damage'].get(e, {}).get('total', 0.0) for e in ELEMENTS},
                                     {s: v['total'] for s, v in r['status'].items()})
        return self._ar[(key, rkey)]

    def hit(self, ar, pre, post, flat):
        """The motion-value-100 hit on the defender: pre-defense `pre` and `flat` (and the grease's
        flat add, as `er-builds-optimize.Scorer` adds it), post-defense `post`, per element."""
        scaled = {e: ar[e] * pre[e] + flat[e] for e in ELEMENTS}
        if self.grease:
            scaled[self.grease[0]] += self.grease[1]
        by = self.ctx.DEF.damage(scaled, 100.0, self.defender)['by_type']
        return sum(by[e] * post[e] for e in ELEMENTS)

    # -- per row, cached -----------------------------------------------------------------------
    def row(self, sid):
        """{'factor': {damage type: cut x correct}, 'hp': max HP with the row / without, 'curve':
        incoming damage at the defense the row's attribute adds give / at the build's own}."""
        if sid in self.ctx_rows:
            return self.ctx_rows[sid]
        m, ctx = self.ctx.m, self.ctx
        fac = {}
        for t in DAMAGE_TYPES:
            pt = t if t in PHYS_TYPES else 'standard'
            d = m.defense_context([sid], pvp=True, phys_type=pt, apply_stack=False)
            el = 'physical' if t in PHYS_TYPES else t
            fac[t] = d['cut'][el] * d['correct'][el]
        hp = ctx.res.resources(self.stats, self.talismans, self.armor, None, [sid])['max_hp'] / self.hp0
        curve = 1.0
        if any(m.sp[sid][f] for f in B.STATS.values()):
            me = ctx.DEF.defender(ctx.dt, self._def_stats(), self.armor, list(self.talismans), None, pvp=True,
                                  extra_speffects=[sid])
            dfn = {e: me['defense'][e] for e in ELEMENTS}
            if dfn != self.defense0:
                curve = ctx.mix(dfn)['mean_hit'] / self.mean_hit0
        self.ctx_rows[sid] = {'factor': fac, 'hp': hp, 'curve': curve}
        return self.ctx_rows[sid]

    @property
    def ctx_rows(self):
        return self.__dict__.setdefault('_rows', {})

    # -- one kit -------------------------------------------------------------------------------
    def entries(self, rune, tears, role):
        """The kit's rows for `role` in application order, [(sid, source)], with the rows that wait
        on a state (`invocationConditionsStateChange*`) left out: `Buffs` applies them as if always
        on, and when their state holds is not modelled (`unvalued` reports them)."""
        m = self.ctx.m
        active = ([f'great rune:{rune}'] if rune else []) + \
            [f'tear:{t}' for t in tears if f'tear:{t}' in m.by_name]
        return [(sid, src) for sid, src in m.resolve(active, role)[0] if not state_gated(self.ctx, sid)]

    def terms(self, rune, tears, role):
        """{'offense', 'defense', 'sustain', 'total', ...} of one kit for one role, over the build's
        fight model; `sustain` is the one-attacker quotient over `attackers` (module docstring)."""
        m, fight = self.ctx.m, self.sched
        ents = self.entries(rune, tears, role)
        # `kit_factors` takes the rows by id: every one is a single-use source, which is what a
        # tear is (`TEAR_USES`) and what a permanent rune row needs (it is never recast).
        f = m.kit_factors([sid for sid, _ in ents], 'host', self.two_handed, **fight)
        ar, status = self.rating(f['stats'], f['rate_points'])
        hit = self.hit(ar, f['pre'], f['post'], f['flat'])
        offense = hit / self.hit0 - 1.0 if self.hit0 else 0.0
        live = m.stack(ents)
        plans = m.source_plans(live, fight['fight_seconds'], fight['hits'])
        fac = dict.fromkeys(DAMAGE_TYPES, 1.0)
        hp_rate, curve, rows = 1.0, 1.0, []
        for sid, src in live:
            u = m.planned_uptime(sid, plans[src][2], fight['hits'])
            if u <= 0:
                continue
            r = self.row(sid)
            for t in DAMAGE_TYPES:
                fac[t] *= 1.0 + u * (r['factor'][t] - 1.0)
            hp_rate *= 1.0 + u * (r['hp'] - 1.0)
            curve *= 1.0 + u * (r['curve'] - 1.0)
            rows.append((sid, src, round(u, 4)))
        w = {t: self.share[t] * self.armor_mult[ARMOR_KEY.get(t, t)] for t in DAMAGE_TYPES}
        tw = sum(w.values()) or 1.0
        kept = curve * sum(w[t] * fac[t] for t in DAMAGE_TYPES) / tw
        hp_kit = self.hp0 * hp_rate
        heal, drain = self.heals(tears, hp_kit)
        ehp_rate = max(1e-6, (hp_kit + heal - drain) / self.hp0)
        defense = 1.0 - kept / hp_rate
        sustain = (kept / hp_rate - kept / ehp_rate) / self.attackers
        status_x = {s: (status[s] / v if v else 1.0) for s, v in self.status0.items()}
        return {'offense': offense, 'defense': defense, 'sustain': sustain, 'total': offense + defense + sustain,
                'hit': hit, 'kept': kept, 'hp_rate': hp_rate, 'heal': heal, 'drain': drain,
                'status_x': status_x, 'rows': rows}

    def heals(self, tears, hp):
        """(HP healed, HP drained) over `SUSTAIN_SECONDS` by the tears' rows: heals over time
        (`Buffs.regen_sources`, category 161 heals replacing each other weakest first as
        `build_regen` applies them) and drains, a 0 s row with positive `changeHp*` that a live
        row cycles, applied once per that row's `motionInterval` for its duration (`INFERRED`:
        the cycle period is the parent's interval, as the heals tick on their own; Bloodsucking
        Cracked Tear then drains 20 HP + 0.2% max HP a second for 180 s, the figure the community
        wiki gives, `COMMUNITY`)."""
        m = self.ctx.m
        entries = [(i, f'tear:{t}') for t in tears for i in self.ctx.regen_src.get(f'tear:{t}', ())]

        def worth(e):
            pt, pct, d = m.regen_row(e[0])
            return (d < 0, (pt + pct) * (d if d > 0 else 0.0))
        live = m.stack(sorted(entries, key=worth))
        heal = m.regen_hp([m.regen_row(i) for i, _ in live], hp, SUSTAIN_SECONDS)
        drain = 0.0
        for t in tears:
            for sid, per_s, dur in tear_drains(self.ctx, t):
                drain += (per_s[0] + hp * per_s[1] / 100.0) * (SUSTAIN_SECONDS if dur < 0 else min(dur, SUSTAIN_SECONDS))
        return heal, drain


def tear_drains(ctx, name):
    """[(sid, (HP per s, max-HP percent per s), duration)] of the HP-loss rows one tear cycles."""
    tear = next((t for t in ctx.tears if t['name'] == name), None)
    out = []
    for sid, via in (tear or {}).get('rows', ()):
        mm = re.match(r'sp (\d+)\.cycleOccurrenceSpEffectId$', via)
        r = ctx.m.sp[sid]
        if not mm or r['effectEndurance'] != 0.0 or (r['changeHpPoint'] <= 0 and r['changeHpRate'] <= 0):
            continue
        p = ctx.m.sp[int(mm.group(1))]
        if p['motionInterval'] <= 0:
            continue
        iv = p['motionInterval']
        out.append((sid, (max(0.0, r['changeHpPoint']) / iv, max(0.0, r['changeHpRate']) / iv), p['effectEndurance']))
    return out


# --------------------------------------------------------------------------------------------
# what the model cannot value

def state_gated(ctx, sid):
    """A row that applies only while its owner holds a `stateInfo` (`invocationConditionsStateChange1..3`)."""
    r = ctx.full_rows().get(sid) or {}
    return any(r.get(f'invocationConditionsStateChange{i}', 0) for i in (1, 2, 3))


def scored_rows(ctx, rows):
    """The rows of `rows` [(sid, via)] that `terms` can score: a heal over time, or a scored column
    on a row that waits on no state, no attack sub-category, and lives 2 s or more (or for good)."""
    out = []
    for sid, _ in rows:
        r = ctx.m.sp.get(sid)
        if r is None or state_gated(ctx, sid):
            continue
        if ctx.m.regen_row(sid):
            out.append(sid)
            continue
        if any(r[f'magicSubCategoryChange{i}'] for i in (1, 2, 3)) or 0.0 <= r['effectEndurance'] < B.REFRESHED_ROW_S:
            continue
        if set(_nondefault(ctx, sid)) & VALUED:
            out.append(sid)
    return out


def _nondefault(ctx, sid):
    full = ctx.full_rows()
    r = full.get(sid)
    if r is None:
        return {}
    d = ctx._full_default
    return {k: v for k, v in r.items() if isinstance(v, (int, float)) and abs(float(v) - d.get(k, 0.0)) > 1e-6}


def _valued_columns():
    """Columns `terms` scores: attacker rates and adds, the PvP correct rates, cut rates, attribute
    adds that feed AR, HP or defense, max HP, and the HP columns of a heal or drain row."""
    out = set()
    for d in (B.AP_RATE, B.AP_RATE_PT, B.AP_FLAT, B.AP_FLAT_PT, B.ATK_RATE, B.ATK_RATE_PT, B.PVP_ATK, B.PVP_DEF,
              B.CUT, B.CUT_PT, B.RATE_POINTS):
        out |= set(d.values())
    out |= {B.STATS[k] for k in ('vig', 'str', 'dex', 'int', 'fth', 'arc')} | {'maxHpRate'}
    return out


VALUED = _valued_columns()
#: PvE twins of the PvP columns the model scores: they do nothing in PvP, so they are neither.
PVE_ONLY = set(B.PVE_ATK.values()) | set(B.PVE_DEF.values())


#: The `via` of a row applied when the item is used, not by another row: a tear's goods ref, a
#: great rune's `ApplyRuneArcEffects` (Rune Arc is used before contact as well).
ON_USE_VIA = ('item', 'ApplyRuneArcEffects')
NO_COLUMN = ('no SpEffect column of its rows changes anything the model or the column reader knows; '
             'its effect lives outside those columns (game code keyed on the row id)')


def unvalued(ctx, rows, role_note=None):
    """Plain reasons for what `rows` [(sid, via)] do that `terms` does not score, one line per
    kind (rows and columns of one kind are joined)."""
    m = ctx.m
    out, groups, states = [], {}, {}

    def add(text):
        if text not in out:
            out.append(text)
    for sid, via in rows:
        r = m.sp.get(sid)
        if r is None:
            continue
        nd = _nondefault(ctx, sid)
        vals = {k for k in nd if k in VALUED}
        regen = m.regen_row(sid) is not None
        hp_cols = {'changeHpPoint', 'changeHpRate'} & set(nd)
        cycled = bool(re.match(r'sp \d+\.cycleOccurrenceSpEffectId$', via))
        drain = cycled and r['effectEndurance'] == 0.0 and (r['changeHpPoint'] > 0 or r['changeHpRate'] > 0)
        accum = 'accumuOverFireId' in via or 'accumuOverFireId' in nd
        subs = sorted({r[f'magicSubCategoryChange{i}'] for i in (1, 2, 3)} - {0})
        if subs and vals:
            add(f"row {sid} applies only to attacks of sub-category {', '.join(map(str, subs))} "
                f"(100 is a charged heavy); the share of such hits in a fight is not modelled")
        gates = sorted({nd.get(f'invocationConditionsStateChange{i}', 0) for i in (1, 2, 3)} - {0})
        if gates and (vals or hp_cols or set(nd) & set(B.CUT.values())):
            add(f"row {sid} applies only while its owner holds stateInfo {', '.join(map(str, gates))} "
                f"({', '.join(str(m.state_names.get(g)) for g in gates)}); when that is, is not modelled")
        if 'accumuOverFireId' in via and vals:
            groups.setdefault('accumulator', []).append(f'{sid} ({r["effectEndurance"]:g} s)')
        elif vals and 0.0 <= r['effectEndurance'] < B.REFRESHED_ROW_S and not subs:
            add(f'row {sid} lives {r["effectEndurance"]:g} s; what re-applies it is not traced (uptime 0)')
        if hp_cols and not regen and not drain:
            parent = int(via.split()[1].split('.')[0]) if via.startswith('sp ') else None
            cond = {k: v for k, v in nd.items() if k in ('conditionHp', 'conditionHpRate')}
            if not cond and parent is not None:
                cond = {k: v for k, v in _nondefault(ctx, parent).items() if k in ('conditionHp', 'conditionHpRate')}
            change = ', '.join(f'{k} {nd[k]:g}' for k in sorted(hp_cols))
            if cond:
                add(f'row {sid} is a one-time HP change ({change}) gated on HP '
                    f'({", ".join(f"{k} {v:g}" for k, v in cond.items())}); whether the fight reaches that HP '
                    f'while the item lives is not modelled')
            elif r['effectEndurance'] == 0.0 and via in ON_USE_VIA:
                add(f'row {sid} is an instant HP change ({change}) on use; the item is used before contact, at '
                    f'full HP (buffs.md section 10), so it restores nothing there')
            elif not gates:
                add(f'row {sid} changes HP ({change}, {r["effectEndurance"]:g} s) on an event the model does not '
                    f'count (a heal under 2 s or on a landed hit)')
        instant_fp = {'changeMpPoint', 'changeMpRate'} & set(nd) and r['effectEndurance'] == 0.0 and via in ON_USE_VIA
        if instant_fp:
            add(f'row {sid} is an instant FP restore on use, before contact at full FP')
        si = r['stateInfo']
        if si and si not in READ_STATE_INFO and not accum:
            states[si] = m.state_names.get(si)
        cures = [k for k, f in B.STATUS.items() if nd.get(f, 0) < 0]
        if cures:
            groups.setdefault('cures', []).extend(c for c in cures if c not in groups.get('cures', []))
        for col, v in sorted(nd.items()):
            if col in VALUED or col in PVE_ONLY or col in BOOKKEEPING or col.startswith(BOOKKEEPING_PREFIXES):
                continue
            if col in ('changeHpPoint', 'changeHpRate') or col in B.STATUS.values():
                continue
            if instant_fp and col in ('changeMpPoint', 'changeMpRate'):
                continue
            what = next((w for pat, w in UNVALUED_COLUMNS if re.match(pat, col)), None)
            if what:
                item = f'{col} {v:g}'
                if item not in groups.setdefault(what, []):
                    groups[what].append(item)
            else:
                add(f'row {sid}: column {col} {v:g} is not read')
    if 'accumulator' in groups:
        add(f"rows {', '.join(groups.pop('accumulator'))} are accumulator tiers; what keeps one on is not traced "
            f"(uptime 0, buffs.md section 10)")
    if 'cures' in groups:
        add(f"cures {', '.join(groups.pop('cures'))} (status is not in the model)")
    for what, items in groups.items():
        add(f'{what}: {", ".join(items)} (no damage-unit value in this model)')
    if states:
        add('stateInfo ' + ', '.join(f'{k} ({v})' for k, v in sorted(states.items()))
            + ': not in the model, its numbers are not traced')
    if role_note:
        add(role_note)
    return out


def rune_note(ctx, rune, role_w):
    """The role caveat of a rune for the roles the score is weighted over (buffs.md section 4)."""
    src = ctx.m.by_name.get(f"great rune:{rune['name']}") or {}
    notes = []
    if role_w.get('invader'):
        inv = [i for i, _ in src.get('invader', ())]
        notes.append(f"for an invader ({role_w['invader']:.0%} of the weight) only row {inv[0]} applies"
                     if inv else f"inert for an invader ({role_w['invader']:.0%} of the weight)")
    if role_w.get('cooperator'):
        notes.append(f"inert for a cooperator ({role_w['cooperator']:.0%} of the weight)")
    return '; '.join(notes) or None


# --------------------------------------------------------------------------------------------
# the pick

def combine(by_role, weights):
    """Role-weighted mean of the terms (`offense`, `defense`, `sustain`, `total`)."""
    return {k: sum(weights[r] * by_role[r][k] for r in weights) for k in ('offense', 'defense', 'sustain', 'total')}


def rank_key(kit, adoption):
    """Best total first; equal totals (within 1e-9) to the kit the window's PvP builds carry more
    often, then by name."""
    seen = adoption.get(('great rune', kit['great_rune']), 0) + sum(adoption.get(('tear', t), 0)
                                                                     for t in kit['crystal_tears'])
    return (-round(kit['total'], 9), -seen, kit['great_rune'] or '', tuple(kit['crystal_tears']))


def _rounded(d, nd=4):
    return {k: round(v, nd) for k, v in d.items()}


def pick(weapon, affinity, level, stats, two_handed=False, role=None, fight=B.DEFAULT_FIGHT, **kw):
    """The great rune and two crystal tears that maximise `total` for this build (module
    docstring), from every great rune and crystal tear in the regulation.

    `fight` is the fight model (`er-mechanics-buffs.FIGHT_KINDS`, default `invasion`); `role`
    None is that model's (`default_role`). `kw`: `rl` (default the stats' own), `window` (10),
    `talismans`, `armor` (weight the incoming
    damage types by the set's absorption), `grease` ((element, flat) as `Scorer` takes it),
    `defender` (the hit's defender; default the window's median), `context` (a `Context` to reuse),
    `top` (kits kept in `scores`), `mirror`. Returns `great_rune`, `crystal_tears`, `scores`
    (best kits with their terms), `runes` and `tears` (each item alone, with what is not valued),
    `unvalued`, `role`, `rule`."""
    rl = kw.get('rl') or sum(stats[k] for k in ('vig', 'mnd', 'end', 'str', 'dex', 'int', 'fth', 'arc')) - LEVEL_OFFSET
    ctx = kw.get('context') or Context(rl, kw.get('window', 10), kw.get('mirror', CORPUS))
    weights = resolve_role(default_role(fight) if role is None else role, ctx.roles)
    b = Build(ctx, weapon, affinity, level, stats, two_handed, kw.get('talismans', ()), kw.get('armor', ()),
              kw.get('grease'), kw.get('defender'), fight)
    top = kw.get('top', TOP)

    def score(rune, tears):
        by_role = {r: b.terms(rune, tears, r) for r in weights}
        out = {'great_rune': rune, 'crystal_tears': list(tears), **combine(by_role, weights)}
        out['by_role'] = {r: _rounded({k: by_role[r][k] for k in ('offense', 'defense', 'sustain', 'total')})
                          for r in weights}
        any_r = next(iter(by_role.values()))
        out['status_x'] = _rounded(any_r['status_x'])
        out['detail'] = {r: {'kept': round(v['kept'], 4), 'hp_rate': round(v['hp_rate'], 4), 'heal': round(v['heal'], 1),
                             'drain': round(v['drain'], 1)} for r, v in by_role.items()}
        return out

    unval = {}
    runes = []
    for r in ctx.runes:
        s = score(r['name'], ())
        rows = [(i, 'ApplyRuneArcEffects') for i in r['rows']]
        s['unvalued'] = unval[r['name']] = unvalued(ctx, rows, rune_note(ctx, r, weights))
        s['adoption'] = ctx.adoption.get(('great rune', r['name']), 0)
        runes.append(s)
    tears = []
    for t in ctx.tears:
        s = score(None, (t['name'],))
        s['adoption'] = ctx.adoption.get(('tear', t['name']), 0)
        s['effect'] = any(abs(v[k]) > 1e-9 for v in s['by_role'].values() for k in ('offense', 'defense', 'sustain'))
        s['valued'] = s['effect'] or bool(scored_rows(ctx, t['rows'])) or bool(tear_drains(ctx, t['name']))
        if s['valued'] and not s['effect']:
            s['note'] = ('valued, and worth nothing here: its scored columns do not reach this build '
                         '(an element the weapon does not deal, say)')
        why = unvalued(ctx, t['rows'])
        s['unvalued'] = unval[t['name']] = why or ([] if s['valued'] else [NO_COLUMN])
        tears.append(s)
    pool = [t['crystal_tears'][0] for t in tears if t['effect']]
    if len(pool) < TEAR_SLOTS:
        rest = sorted((t for t in tears if not t['effect']), key=lambda t: -t['adoption'])
        pool += [t['crystal_tears'][0] for t in rest[:TEAR_SLOTS - len(pool)]]
    kits = [score(r['name'], pair) for r in ctx.runes for pair in itertools.combinations(pool, TEAR_SLOTS)]
    kits.sort(key=lambda k: rank_key(k, ctx.adoption))
    runes.sort(key=lambda k: rank_key(k, ctx.adoption))
    tears.sort(key=lambda k: rank_key({**k, 'great_rune': ''}, ctx.adoption))
    best = kits[0]
    lo, hi = b.sched_info['seconds']
    rule = ('Every great rune and crystal tear in the regulation; each kit of one rune and two different tears '
            'scored for this build as offense (the focus hit with the kit over without, less 1) plus survivability '
            f'(1 - incoming PvP damage kept over max HP, heals and drains in HP over {b.attackers} attacker(s)), '
            f'uptime over the {fight} model\'s {lo:g}-{hi:g} s fight and rune rows by role; best total wins.')
    return {'great_rune': best['great_rune'], 'crystal_tears': best['crystal_tears'], 'total': round(best['total'], 4),
            'scores': [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in kit.items()} for kit in kits[:top]],
            'runes': [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in s.items()} for s in runes],
            'tears': [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in s.items()} for s in tears],
            'unvalued': {n: unval[n] for n in [best['great_rune']] + best['crystal_tears']},
            'all_unvalued': unval, 'role': weights, 'kits_scored': len(kits), 'tear_pool': pool,
            'build': {'weapon': weapon, 'affinity': affinity, 'level': level, 'two_handed': two_handed,
                      'rl': rl, 'hit': round(b.hit0, 1), 'max_hp': b.hp0},
            'fight': {k: (round(v, 4) if isinstance(v, float) else v) for k, v in b.sched_info.items()},
            'incoming_share': _rounded(b.share), 'sustain_seconds': SUSTAIN_SECONDS, 'rule': rule}


# --------------------------------------------------------------------------------------------
# CLI

def _fmt_terms(s):
    return (f"{s['total']:+.4f}  off {s['offense']:+.4f}  def {s['defense']:+.4f}  sus {s['sustain']:+.4f}")


def print_pick(res, top_items=40):
    b = res['build']
    print(f"{b['weapon']} {b['affinity']} +{b['level']} {'two' if b['two_handed'] else 'one'}-handed, RL {b['rl']}: "
          f"hit {b['hit']:g} on the window's median defender, max HP {b['max_hp']}")
    f = res['fight']
    print(f"fight {f['kind']}: {f['seconds'][0]:g}-{f['seconds'][1]:g} s over {f['points']} points, "
          f"{f['attackers']} attacker(s), {f['targets']} target(s), {f['hits_mean']:g} landed hits on average")
    print(f"role {', '.join(f'{k} {v:.3f}' for k, v in res['role'].items())}; sustain window "
          f"{res['sustain_seconds']:g} s, divided by the attackers")
    print('incoming PvP damage share: ' + '  '.join(f'{k} {v:.3f}' for k, v in res['incoming_share'].items()))
    print(f"\npick: {res['great_rune']} + {' + '.join(res['crystal_tears'])}  total {res['total']:+.4f}")
    for n, why in res['unvalued'].items():
        for w in why:
            print(f'  {n}: not valued: {w}')
    print(f"\ntop kits ({res['kits_scored']} scored; tear pool {len(res['tear_pool'])} valued tears):")
    for i, k in enumerate(res['scores'], 1):
        print(f"  {i:2d}. {_fmt_terms(k)}  {k['great_rune']} + {' + '.join(k['crystal_tears'])}")
    print('\ngreat runes alone:')
    for s in res['runes']:
        print(f"  {_fmt_terms(s)}  {s['great_rune']:24} carried {s['adoption']:4d}")
        for w in s['unvalued']:
            print(f'        not valued: {w}')
    print('\ncrystal tears alone:')
    for s in res['tears'][:top_items]:
        print(f"  {_fmt_terms(s)}  {s['crystal_tears'][0]:34} carried {s['adoption']:4d}"
              + ('' if s['valued'] else '  (nothing valued)'))
        if s.get('note'):
            print(f"        {s['note']}")
        for w in s['unvalued']:
            print(f'        not valued: {w}')
    st = res['scores'][0].get('status_x') or {}
    if any(abs(v - 1) > 1e-4 for v in st.values()):
        print('\nthe pick moves the focus weapon\'s status build-up (not scored): '
              + ', '.join(f'{k} x{v:.3f}' for k, v in st.items()))


def _parse_stats(s):
    out = {}
    for part in s.split(','):
        k, v = part.split('=')
        out[STAT_ALIAS.get(k.strip().lower(), k.strip().lower())] = int(v)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('weapon', nargs='?')
    ap.add_argument('--affinity', default='Standard')
    ap.add_argument('--level', type=int, help='upgrade level (default the weapon\'s highest)')
    ap.add_argument('--stats', help='vig,mnd,end,str,dex,int,fth,arc (vit is taken for end)')
    ap.add_argument('--two-handed', action='store_true')
    ap.add_argument('--fight', default=B.DEFAULT_FIGHT, choices=tuple(B.FIGHT_KINDS))
    ap.add_argument('--role', choices=('corpus',) + ROLES,
                    help="default the fight model's: invader in an invasion, corpus in a duel")
    ap.add_argument('--window', type=int, default=10)
    ap.add_argument('--talismans', default='', help='comma-separated talisman names')
    ap.add_argument('--top', type=int, default=TOP)
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.weapon or not a.stats:
        ap.error('name a weapon and --stats')
    stats = _parse_stats(a.stats)
    rl = sum(stats[k] for k in ('vig', 'mnd', 'end', 'str', 'dex', 'int', 'fth', 'arc')) - LEVEL_OFFSET
    ctx = Context(rl, a.window)
    level = a.level
    if level is None:
        wid = ctx.tables.find_weapon(a.weapon, a.affinity)
        level = ctx.tables.max_level(ctx.tables.weapons[wid]['reinforceTypeId'])
    tal = [t.strip() for t in a.talismans.split(',') if t.strip()]
    res = pick(a.weapon, a.affinity, level, stats, a.two_handed, a.role, a.fight, context=ctx, talismans=tal,
               top=a.top)
    if a.json:
        res.pop('all_unvalued', None)
        print(json.dumps(res, indent=1))
    else:
        print_pick(res)
    return 0


# --------------------------------------------------------------------------------------------
# selftest

def selftest():
    fails, count = [], [0]

    def check(label, got, want, tol=1e-6):
        count[0] += 1
        ok = abs(got - want) <= tol if isinstance(want, float) and isinstance(got, (int, float)) else got == want
        if not ok:
            fails.append(f'{label}: got {got!r}, want {want!r}')

    # The checker must be able to fail.
    before = len(fails)
    check('negative control', 1.0, 2.0)
    if len(fails) == before:
        print('selftest FAILED: the checker accepted a wrong value')
        return 1
    del fails[:]
    count[0] = 0

    # Synthetic: the combination, the roles, the tie-break.
    for t in ({'offense': 0.05, 'kept': 0.9, 'hp': 1.25, 'ehp': 1.25},
              {'offense': 0.0, 'kept': 1.0, 'hp': 1.0, 'ehp': 1.1}):
        defense = 1 - t['kept'] / t['hp']
        sustain = t['kept'] / t['hp'] - t['kept'] / t['ehp']
        check('total = offense - (kept / effective HP - 1), the gear-synergy form',
              t['offense'] + defense + sustain, t['offense'] - (t['kept'] / t['ehp'] - 1))
    by = {'host': {'offense': 0.1, 'defense': 0.2, 'sustain': 0.0, 'total': 0.3},
          'invader': {'offense': 0.1, 'defense': 0.0, 'sustain': 0.0, 'total': 0.1}}
    check('role weighting is the weighted mean', round(combine(by, {'host': 0.75, 'invader': 0.25})['total'], 9), 0.25)
    check('corpus role resolves to the window mix', resolve_role('corpus', {'host': 0.7, 'invader': 0.3}),
          {'host': 0.7, 'invader': 0.3})
    check('a dict role is normalised', resolve_role({'host': 3, 'invader': 1}), {'host': 0.75, 'invader': 0.25})
    check('role mix from tags: invasions invader, duels host, untagged host',
          role_mix([{'tags': ['Invasions']}, {'tags': ['Duels', 'Invasions']}, {'tags': []}]),
          {'host': 0.5, 'invader': 0.5})
    adopt = {('great rune', 'A'): 5, ('tear', 'x'): 1, ('tear', 'y'): 9}
    kits = [{'great_rune': 'A', 'crystal_tears': ['x', 'z'], 'total': 0.2},
            {'great_rune': 'A', 'crystal_tears': ['x', 'y'], 'total': 0.2},
            {'great_rune': 'B', 'crystal_tears': ['x', 'y'], 'total': 0.3}]
    order = [k['crystal_tears'] + [k['great_rune']] for k in sorted(kits, key=lambda k: rank_key(k, adopt))]
    check('best total first, equal totals to the more carried kit', order, [['x', 'y', 'B'], ['x', 'y', 'A'], ['x', 'z', 'A']])
    check('a kit holds two different tears', all(a != b for a, b in itertools.combinations(['p', 'q', 'r'], TEAR_SLOTS)), True)

    # Real regulation: the pool, the classifier and the terms.
    m = B.Buffs()
    runes = great_runes(m)
    check('great runes from goodsType 15, linked by name, equal the hand table in er-mechanics-buffs',
          {r['name']: r['base'] for r in runes}, dict(B.GREAT_RUNE_SPEFFECT))
    check('the name link keeps the 10-row stride', [r['base'] - runes[0]['base'] for r in runes],
          [10 * i for i in range(len(runes))])
    tears = crystal_tears(m)
    names = [t['name'] for t in tears]
    check('every named goodsType 10 good, one per name', len(names), len(set(names)))
    check('the tear pool is the regulation, not the corpus (37 named tears in 1.17.1)', len(names), 37)
    for n in ('Opaline Hardtear', 'Crimson Bubbletear', 'Strength-knot Crystal Tear', 'Ruptured Crystal Tear'):
        check(f'{n} is in the pool', n in names, True)
    ctx = Context(150, 10)
    b = Build(ctx, 'Poleblade of the Bud', 'Standard', 10,
              {'vig': 58, 'mnd': 8, 'end': 38, 'str': 20, 'dex': 22, 'int': 7, 'fth': 8, 'arc': 68})
    ar = B.ar_stat_ratio('Poleblade of the Bud', 'Standard', 10, {k: b.stats[k] for k in DAMAGE_STATS}, False,
                         ctx.tables)({'str': 5, 'dex': 5, 'arc': 5})
    mine = b.rating({'str': 5, 'dex': 5, 'arc': 5}, {})[0]
    check('the AR with adds equals ar_stat_ratio x the AR without', round(sum(mine.values()), 4),
          round(sum(b.ar0[e] * ar[e] for e in ELEMENTS), 4))
    t = b.terms("Morgott's Great Rune", (), 'host')
    check("Morgott's for a host: defense 1 - 1 / HP x1.25 (max HP truncated), nothing else",
          (round(t['defense'], 3), round(t['offense'], 9), round(t['sustain'], 9)),
          (round(1 - 1 / (int(b.hp0 * 1.25) / b.hp0), 3), 0.0, 0.0))
    t = b.terms("Morgott's Great Rune", (), 'invader')
    check("Morgott's for an invader is inert", round(t['total'], 9), 0.0)
    t = b.terms(None, ('Opaline Hardtear',), 'host')
    fs = b.sched['fight_seconds']
    up = sum(min(1.0, 180.0 / f) for f in fs) / len(fs)
    check('Opaline Hardtear: kept 1 - uptime x 0.1 over the fight lengths, one use', round(t['kept'], 4),
          round(1 - 0.1 * up, 4))
    check('Opaline Hardtear in an invasion: defense 0.1 x its uptime there, no attacker term',
          round(t['defense'], 4), round(0.1 * up, 4))
    t = b.terms(None, ('Crimsonburst Crystal Tear',), 'host')
    check('Crimsonburst heals 7 HP/s over the 25 s window', round(t['heal'], 3), 175.0)
    t = b.terms(None, ('Bloodsucking Cracked Tear',), 'host')
    check('Bloodsucking drains 20 HP + 0.2% max HP per s over the window', round(t['drain'], 1),
          round((20 + 0.002 * b.hp0) * SUSTAIN_SECONDS, 1))
    check('Bloodsucking raises the hit', t['offense'] > 0, True)

    # The fight models (`er-mechanics-buffs.FIGHT_KINDS`): invasion by default, its role and lengths,
    # and the attacker count reaching a flat heal and not a cut.
    check('invasion is the default fight model, five attackers', (B.DEFAULT_FIGHT, b.fight, b.attackers),
          ('invasion', 'invasion', 5))
    check("the fight points are the invasion model's by default", tuple(fs), tuple(B.fight_kind_points('invasion')))
    check('invasion points run 10 s to 1 h', (round(fs[0], 6), round(fs[-1], 6)), (10.0, 3600.0))
    check("the default role is the fight model's: invader in an invasion, corpus in a duel",
          (default_role('invasion'), default_role('duel')), ('invader', 'corpus'))
    bd = Build(ctx, 'Poleblade of the Bud', 'Standard', 10, b.stats, fight='duel')
    b5 = Build(ctx, 'Poleblade of the Bud', 'Standard', 10, b.stats, fight='duel', attackers=5)
    check('a duel reads the 180-300 s points (repeated by tag share), one attacker',
          (set(bd.sched['fight_seconds']), bd.attackers), (set(B.FIGHT_SECONDS), 1))
    od, o5 = (x.terms(None, ('Opaline Hardtear',), 'host') for x in (bd, b5))
    check('a percentage cut (Opaline Hardtear) scores the same defense at 1 and 5 attackers',
          o5['defense'], float(od['defense']))
    check('and it moves no sustain at either count', (round(od['sustain'], 12), round(o5['sustain'], 12)), (0.0, 0.0))
    md, mi = (x.terms("Morgott's Great Rune", (), 'host') for x in (bd, b))
    check("Morgott's (a permanent max HP rate) scores the same defense under both models",
          mi['defense'], float(md['defense']))
    cd, ci = (x.terms(None, ('Crimsonburst Crystal Tear',), 'host') for x in (bd, b))
    check('Crimsonburst in a duel: sustain 1 - 1 / (1 + 175 HP / max HP)', cd['sustain'], 1 - 1 / (1 + 175.0 / b.hp0))
    check('a flat heal (Crimsonburst) is worth its duel sustain / 5 in an invasion', ci['sustain'], cd['sustain'] / 5)
    check('the heal itself, in HP over the window, is the same under both', ci['heal'], float(cd['heal']))
    sd, si = (x.terms(None, ('Bloodsucking Cracked Tear',), 'host') for x in (bd, b))
    check('a flat drain (Bloodsucking) costs its duel sustain / 5 in an invasion', si['sustain'], sd['sustain'] / 5)
    check('the drain is a cost', sd['sustain'] < 0, True)
    why = {x['name']: unvalued(ctx, x['rows']) for x in ctx.tears}
    check('Spiked: the charged-heavy gate is reported', any('sub-category 100' in w for w in why['Spiked Cracked Tear']), True)
    check('Thorny: the 1.5 s accumulator tiers are reported', any('accumulator tier' in w for w in why['Thorny Cracked Tear']), True)
    check('Crimson Bubbletear: the HP-gated heal is reported', any('gated on HP' in w for w in why['Crimson Bubbletear']), True)
    check('Greenspill: stamina is reported', any(w.startswith('stamina') for w in why['Greenspill Crystal Tear']), True)
    check('Ruptured: the spawned behavior is reported', any('behavior' in w for w in why['Ruptured Crystal Tear']), True)
    check('Opaline Hardtear: nothing left unvalued', why['Opaline Hardtear'], [])
    check('Deflecting Hardtear: no column read, nothing valued', why['Deflecting Hardtear'], [])
    check("Crimson Crystal Tear: the instant heal on use is reported",
          any('instant HP change' in w for w in why['Crimson Crystal Tear']), True)
    rw = {r['name']: unvalued(ctx, [(i, 'ApplyRuneArcEffects') for i in r['rows']]) for r in ctx.runes}
    check("Godrick's: FP and Endurance reported", sum(1 for w in rw["Godrick's Great Rune"]
                                                      if w.startswith(('FP', 'Endurance'))), 2)
    check("Mohg's: stateInfo 441 reported", any('stateInfo 441' in w for w in rw["Mohg's Great Rune"]), True)
    check('Crimsonwhorl: its state-gated cut row is left out of the terms',
          round(b.terms(None, ('Crimsonwhorl Bubbletear',), 'host')['total'], 9), 0.0)
    res = pick('Poleblade of the Bud', 'Standard', 10, b.stats, context=ctx)
    check('the pick defaults to the invasion model and the invader role', (res['fight']['kind'], res['role']),
          ('invasion', {'invader': 1.0}))
    tears_by = {t['crystal_tears'][0]: t for t in res['tears']}
    check('the pick is the best-scored kit', (res['great_rune'], res['crystal_tears']),
          (res['scores'][0]['great_rune'], res['scores'][0]['crystal_tears']))
    check('the pick holds two different tears from the regulation pool',
          len(set(res['crystal_tears'])) == TEAR_SLOTS and set(res['crystal_tears']) <= set(names), True)
    check('every kit of the pool is scored: runes x pairs of tears that move a term',
          res['kits_scored'], len(ctx.runes) * math.comb(len(res['tear_pool']), TEAR_SLOTS))
    check('Magic-Shrouding on a physical weapon: valued, worth nothing, out of the pair pool',
          (tears_by['Magic-Shrouding Cracked Tear']['valued'], tears_by['Magic-Shrouding Cracked Tear']['effect'],
           'Magic-Shrouding Cracked Tear' in res['tear_pool']), (True, False, False))
    check('Spiked and Thorny are not valued (gate, accumulator), so not "worth nothing"',
          (tears_by['Spiked Cracked Tear']['valued'], tears_by['Thorny Cracked Tear']['valued']), (False, False))
    if fails:
        print('selftest FAILED:')
        for f in fails:
            print('  ' + f)
        return 1
    print(f'selftest passed ({count[0]} checks)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
