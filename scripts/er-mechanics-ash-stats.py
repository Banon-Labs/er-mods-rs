#!/usr/bin/env python3
"""The stats a rune-level 150 build puts into one weapon to hit hardest with one skill.

    python3 scripts/er-mechanics-ash-stats.py Lance
    python3 scripts/er-mechanics-ash-stats.py Lance --skill "Lion's Claw" --two-handed --json
    python3 scripts/er-mechanics-ash-stats.py --selftest

Three steps, each borrowed from a tool that already makes that decision, so this file adds only
the last one:

1. Vigor, Mind and Endurance are what PvP builds at that RL actually carry. Vigor is the median of
   the corpus's PvP builds in RL +- 10 of the weapon's stat archetype; Mind and Endurance are the
   median of the PvP builds nearest the spread in INT/FTH and STR/DEX, re-read after each spend
   (`er-builds-optimize.spend_floored`), Endurance at least what keeps the weapon at medium roll
   (`er-mechanics-infusions.Builder.setup`). Those points come out of the pool first.
2. The infusion is the weapon's own best at that RL: the affinity, starting class and grease whose
   build deals the most per hit (`er-builds-optimize.optimize`, the same call the Weapon card's
   "Top Infusions" makes), restricted to the affinities the skill can be mounted at
   (`er-mechanics-ashes.mountable_skills`).
3. With that affinity and grease, every starting class is tried again and the points left after
   the floors and the weapon's requirements go where they add the most damage to one cast of the
   skill: the sum over `skill_hits` of each hit after PvP defense on the RL window's median
   defender (`er-mechanics-ashes.pvp_damage`), with the optimizer's greedy look-ahead and swap pass
   (`er-builds-optimize.spend`).

The grease is added to the skill's melee hits that come from the weapon, times each hit's
`spEffectAtkPowerCorrectRate_byPoint` (docs/er-mechanics/grease.md section 1). It is left off skill
bullets: a right-hand grease row is `wepParamChange 1`, and category 0 (skill bullets) accepts only
the 0/5/6 group (ashes-of-war.md section 4, the filter is `VERIFIED`, the category meaning
`INFERRED`).
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


INF = _mod('er_mechanics_infusions', 'er-mechanics-infusions.py')
OPT = INF.OPT
AR = OPT.AR
LEV = _mod('er_mechanics_ash_levers', 'er-mechanics-ash-levers.py')
A = LEV.A
DAMAGE_STATS = OPT.DAMAGE_STATS
RL = 150


class AshScorer(OPT.Scorer):
    """`er-builds-optimize.Scorer` whose score is one cast of a skill after PvP defense."""

    def __init__(self, levers, weapon, affinity, sid, two_handed, defender, grease, stance=None):
        self.levers, self.weapon, self.affinity, self.sid = levers, weapon, affinity, sid
        self.two_handed, self.defender, self.grease = two_handed, defender, grease
        #: The button a stance skill hands over on (`er-mechanics-ashes.skill_profile`).
        self.stance = stance
        self.wid = levers.t.find_weapon(weapon)
        self.cache = {}
        self.cap = OPT.stat_caps(levers.ar_tables, weapon, affinity)

    def hits(self, st):
        return OPT.skill_cast_hits(self.levers, self.weapon, self.affinity, self.sid, st, self.two_handed,
                                   self.grease, self.stance)

    def score(self, st):
        key = tuple(st[k] for k in DAMAGE_STATS)
        if key not in self.cache:
            self.cache[key] = A.pvp_damage(self.levers.t, self.wid, self.hits(st), self.defender, OPT.DEF)
        return self.cache[key]


#: The groups a cast's hits are split into for `contributions`, by `skill_hits` kind.
HIT_GROUPS = (('weapon', 'melee'), ('bullet', 'bullet'))
SOURCES = ('base',) + DAMAGE_STATS + ('grease',)


def stat_terms(ctx, el, aecp_override=-1, disable_2h=False):
    """{stat: its term} of `element_multiplier` for one element, so the multiplier is
    1 + the sum of the terms. None when a requirement is unmet: the multiplier is then the
    penalty alone and does not split by stat."""
    AR_ = ctx.AR
    name, suf, _, _, gfield = next(e for e in AR_.ELEMENTS if e[0] == el)
    aecp = ctx.tables.aecp.get(aecp_override if aecp_override >= 0 else ctx.wep['attackElementCorrectId'], {})
    str_mult = AR_.TWO_HAND_STR_MULT if ctx.two and not disable_2h else 1.0
    terms = {}
    for s in AR_.STATS:
        aname, cfield, rfield, pfield = AR_.STAT_FIELDS[s]
        if not aecp.get(f'is{aname}Correct_by{suf}'):
            terms[s] = 0.0
            continue
        over = aecp.get(f'overwrite{aname}CorrectRate_by{suf}', -1)
        infl = aecp.get(f'Influence{aname}CorrectRate_by{suf}', 100) * 0.01
        rate = (over if over >= 0 else ctx.wep[cfield]) * ctx.reinf[rfield]
        m = infl * AR_.stat_multiplier(ctx.tables, ctx.wep[pfield], ctx.stats.get(s, 0), rate, ctx.wep[gfield],
                                       str_mult if s == 'str' else 1.0)
        if m < 1.0:
            return None
        terms[s] = m - 1.0
    return terms


def hit_sources(ctx, h, grease):
    """{source: attack} for one hit, summing to its attack: the weapon's unscaled part, each stat's
    share of the scaling (`hit_attack` = base part x (1 + sum of the stat terms)), and the grease."""
    out = dict.fromkeys(SOURCES, 0.0)
    for el in A.ELEMENTS:
        if not h['from_weapon']:
            out['base'] += h['attack'].get(el, 0.0)
            continue
        part = ctx.base_by[el] * h['mv'].get(el, 0) / 100.0 + h['flat'].get(el, 0.0) * ctx.base_atk_rate
        terms = stat_terms(ctx, el, h['element_correct_override'], h['disable_2h'])
        if terms is None:
            out['base'] += part * ctx.multiplier(el, h['element_correct_override'], h['disable_2h'])
            continue
        out['base'] += part
        for s, v in terms.items():
            out[s] += part * v
    if grease and h['kind'] == 'melee' and h['from_weapon']:
        out['grease'] = sum(h['attack'].values()) - sum(out.values())
    return out


def contributions(scorer, stats):
    """For each group of the skill's hits that exists: hits per cast, damage per cast after PvP
    defense, and that damage split by where its attack comes from -- the weapon's unscaled attack,
    each damage stat, and the grease.

    The attack splits exactly, because the stat multiplier is one plus a term per stat. Defense is
    not linear in attack, so each hit's damage is divided in proportion to its attack. Weapon hits
    are the skill's melee hits; bullets take their flat attack and the scaling of their own attack
    element row."""
    hits = scorer.hits(stats)
    A.pvp_damage(scorer.levers.t, scorer.wid, hits, scorer.defender, OPT.DEF)
    ctx = A.WeaponContext(scorer.weapon, scorer.affinity, stats={k: stats[k] for k in DAMAGE_STATS},
                          two_handed=scorer.two_handed, ar_tables=scorer.levers.ar_tables)
    out = []
    for name, kind in HIT_GROUPS:
        sel = [h for h in hits if h['kind'] == kind]
        if not sel:
            continue
        dmg = dict.fromkeys(SOURCES, 0.0)
        for h in sel:
            src = hit_sources(ctx, h, scorer.grease)
            atk = sum(src.values())
            for k, v in src.items():
                dmg[k] += h['pvp_damage_total'] * v / atk if atk else 0.0
        total = sum(dmg.values())
        parts = [{'source': k, 'damage': round(v, 1), 'pct': round(100.0 * v / total, 1)}
                 for k, v in sorted(dmg.items(), key=lambda kv: -kv[1]) if v > 0.05]
        out.append({'group': name, 'hits': sum(h.get('count', 1) for h in sel), 'damage': round(total, 1),
                    'parts': parts})
    return out


def skill_affinities(levers, weapon, sid):
    """The affinities `sid` can be fired at on `weapon` (`mountable_skills` at the top level)."""
    t = levers.t
    wid = t.find_weapon(weapon)
    level = AR.Tables(None).max_level(t.reg.weapon[wid]['reinforceTypeId'])
    return [aff for i, aff in enumerate(AR.AFFINITIES) if sid in A.mountable_skills(t, wid, i, level)]


def optimal(weapon, skill=None, rl=RL, two_handed=False, levers=None):
    levers = levers or LEV.Levers()
    t = levers.t
    wid = t.find_weapon(weapon)
    sid = t.find_arts(skill) if skill else t.reg.weapon[wid]['swordArtsParamId']
    b = INF.Builder(weapon)
    fl, dfn = b.setup(rl)
    affs = [a for a in b.affs if a in skill_affinities(levers, weapon, sid)]
    if not affs:
        raise SystemExit(f'{t.arts_name(sid)} cannot be mounted on {weapon} at any affinity it takes')
    weapon_best = OPT.optimize(b.tables, b.model, weapon, rl, two_handed, 'damage', fl, dfn, affs,
                               INF.TIER, keep=1, pool=b.pool)
    if not weapon_best:
        raise SystemExit(f'no RL {rl} build wields {weapon}')
    wb = weapon_best[0]
    aff, level, grease = wb['affinity'], wb['level'], wb['grease']
    need = OPT.requirements(b.tables, weapon, aff, level, two_handed)
    best = None
    for cls in OPT.RES.CLASS_ROWS:
        cls_level, base = b.model.class_base(cls)
        st = {k: max(base[k], fl.get(k, 0), need.get(k, 0)) for k in OPT.STATS}
        if rl < cls_level:
            continue
        scorer = AshScorer(levers, weapon, aff, sid, two_handed, dfn, grease)
        spent = OPT.spend_floored(st, rl, scorer, b.pool)
        if spent is None:
            continue
        st, floors = spent
        dmg = scorer.score(st)
        if best is None or dmg > best['skill_damage']:
            best = {'class': cls, 'stats': st, 'skill_damage': dmg, 'scorer': scorer, 'floors': floors}
    floors = best['floors']
    weapon_spread = {k: wb['stats'][k] for k in OPT.STATS}
    return {
        'rl': rl, 'two_handed': two_handed, 'skill': t.arts_name(sid), 'skill_id': sid,
        'affinity': aff, 'level': level,
        'grease': OPT.GREASE_NAMES[INF.TIER][grease[0]] if grease else None,
        'grease_element': grease[0] if grease else None, 'grease_flat': grease[1] if grease else 0,
        'class': best['class'], 'stats': best['stats'], 'survival_floors': floors,
        'skill_damage': round(best['skill_damage'], 1),
        'contributions': contributions(best['scorer'], best['stats']),
        'weapon_build': {'class': wb['class'], 'stats': weapon_spread, 'damage_per_hit': round(wb['score'], 1)},
        'defender_builds': dfn['n'],
    }


# --------------------------------------------------------------------------------------------
# conflict: the stats a weapon's normal hit wants against the stats its skill wants

#: `er-mechanics-ash-table.py`'s `--armor` default. Armor moves only the table's hyperarmor
#: columns, not the `score` the conflict picks an ash by.
ASH_TABLE_ARMOR = ('Verdigris Gauntlets', 'Verdigris Armor', 'Verdigris Greaves')


def sweep_pick(row):
    """(affinity, grease element or None, class, stats, damage) of an RL sweep row's configuration
    with the most damage, the one `er-mechanics-infusions.sweep_best` ranks the weapon by."""
    best = None
    for key in ('elemental', 'greased', 'quality'):
        c = row.get(key)
        if c and (best is None or c['dmg'] > best[1]['dmg']):
            best = (key, c)
    if best is None:
        return None
    key, c = best
    return (c.get('aff') or ('Quality' if key == 'quality' else 'Standard'), c.get('grease'), c.get('class'),
            {k: c['stats'][k] for k in OPT.STATS}, c['dmg'])


class Conflict:
    """What a weapon's normal hit and its skill each give up at the other's best stats, at RL 150,
    one-handed, on the affinity and grease the RL 150 hit ranking picked for it
    (`er-mechanics-infusions.load_sweep`, the rows the R3 boards rank by).

    Every spread here comes from the optimizer's own spend (`OPT.spend_floored` from each starting
    class, with the sweep's floors `OPT.sweep_floor` and the conditioning pool), so the hit and the
    skill are spent the same way and differ only in what they score:

    - S_hit: the most damage per hit (`OPT.Scorer` `damage`, the sweep's grease added).
    - The skill: the weapon's own when it takes no ash (`gemMountType` 0), whether or not the ash
      table can time it (a stance skill on the button whose cast deals more at S_hit). Otherwise
      every damaging ash `er-mechanics-ash-table.Table.rows` lists at S_hit (its own filters:
      blacklist, unique, unobtainable, untimed), each spent for its cast damage (`AshScorer`, one
      plain spend from S_hit's class) and scored by the table at that spread
      (`Table.rows(only=...)`); the ash with the highest score there is the skill. A
      no-damage ash (a step, a buff) is never picked: the table values it by the weapon hit out of
      it, which wants S_hit by construction, and the `hit_skill` objective counts a cast's damage
      only. The pick is then re-scored exactly at its own spread (`Table.rows(only=...)`).
    - S_skill: the chosen skill's cast damage, spent from S_hit's class and Wretch, then from the
      class `spend_two` finds; from every class with `every_class`.
    - S_comb: the `hit_skill` objective (`OPT.SKILL_SHARE` unless given), spent from S_hit's and
      S_skill's classes and `spend_two`'s; from every class with `every_class`. An ash weapon is
      also spent with the damaging ash the table ranks first at S_hit, and keeps the better.

    The hit is the sweep's damage objective; the cast is `AshScorer`'s (vsPlayer rates and each
    hit's FinalDamageRateParam row), both on the RL 140-160 median defender.

    A cast's own HP cost (Bloody Slash's 1763: 5% of max HP + 50, `er-mechanics-self-damage`) is
    charged in the two places that pick: the table's `score` (`table_rows`, at the spread's max HP)
    and the `hit_skill` objective's skill term (`skill_hp_cost`, reported as `skill_hp_cost`).
    `AshScorer` stays the cast's damage: the cost reads VIG alone, which no damage spend moves."""

    def __init__(self, rl=RL, skill_weight=None, every_class=False):
        self.every_class = every_class
        if rl != RL:
            raise SystemExit(f'the ash table scores on the RL {RL} defender only')
        # The ash table imports this module by name; run as a script, this module is `__main__`.
        sys.modules.setdefault('er_mechanics_ash_stats', sys.modules[__name__])
        self.T = sys.modules.get('er_mechanics_ash_table') or _mod('er_mechanics_ash_table',
                                                                  'er-mechanics-ash-table.py')
        self.rl = rl
        self.weight = OPT.SKILL_SHARE if skill_weight is None else skill_weight
        rows = OPT.corpus_rows(OPT.CACHE / 'builds.jsonl', rl - 10, rl + 10)
        self.dfn = OPT.bracket_defender(rows)
        self.pool, _ = OPT.conditioning_pool(rows, 'pvp')
        self.tables = AR.Tables(None)
        fl, load = OPT.sweep_corpus_stats({rl: rows}, [rl], 15, 'pvp')
        OPT._SWEEP.update(tables=self.tables, rls=[rl], floors=fl, other_load=load, roll='medium',
                          pools={rl: self.pool})
        self.tab = self.T.Table('Dagger', 'Keen', {k: 10 for k in DAMAGE_STATS}, list(ASH_TABLE_ARMOR), True)
        self.lv = self.tab.lev
        OPT.skill_levers(self.lv)
        self.model = OPT.RES.Model()

    def spend(self, scorer, floor, need, classes):
        """(score, class, spread) of the best spread `scorer` reaches from any of `classes`."""
        best = None
        for cls in classes:
            cls_level, base = self.model.class_base(cls)
            if self.rl < cls_level:
                continue
            start = {k: max(base[k], floor.get(k, 0), need.get(k, 0)) for k in OPT.STATS}
            got = OPT.spend_floored(start, self.rl, scorer, self.pool)
            if got is None:
                continue
            s = scorer.score(got[0])
            if best is None or s > best[0]:
                best = (s, cls, got[0])
        return best

    def spend_two(self, scorer, floor, need, first):
        """`spend` from the classes in `first`, then from the one other class whose base frees the
        most points under the best of those spreads, when any frees one. Freed: in each stat the
        spread leaves at its class's base, what the other class's base is lower by; less what its
        base forces above the spread elsewhere. Every class has the same stat total at one RL, so a
        class matters only through the stats it lets a spread drop (`INFERRED` shortcut for the
        sweep's every-class search; `--every-class` measures with that search instead)."""
        best = self.spend(scorer, floor, need, first)
        if best is None:
            return None
        _, cls0, st = best
        b0 = self.model.class_base(cls0)[1]

        def freed(cls):
            b = self.model.class_base(cls)[1]
            return (sum(max(0, b0[k] - b[k]) for k in OPT.STATS if st[k] <= b0[k])
                    - sum(max(0, b[k] - st[k]) for k in OPT.STATS))

        other = max((c for c in OPT.RES.CLASS_ROWS if c not in first), key=freed)
        if freed(other) > 0:
            again = self.spend(scorer, floor, need, [other])
            if again and again[0] > best[0]:
                best = again
        return best

    def table_rows(self, weapon, affinity, stats, only=None):
        """The ash table's rows at spread `stats`, each cast's own HP cost charged at that spread's
        max HP (`er-mechanics-ash-table.apply_hp_cost`; VIG read off `stats`, no talismans)."""
        self.tab.stats = {k: stats[k] for k in DAMAGE_STATS}
        self.tab.max_hp = float(self.model.resources(dict(stats))['max_hp']) if 'vig' in stats else \
            float(self.T.TARGET_HP)
        self.tab.set_weapon(weapon, affinity)
        return self.tab.rows((False,), only)

    def one(self, row):
        import time
        t0 = time.time()
        w = row['weapon']
        pick = sweep_pick(row)
        if pick is None:
            return {'weapon': w, 'skipped': 'no configuration in the sweep row'}
        aff, gel, cls_sweep, s_sweep, _ = pick
        grease = (gel, OPT.GREASES[INF.TIER]) if gel else None
        tb, t = self.tables, self.lv.t
        lvl = tb.max_level(tb.weapons[tb.find_weapon(w, aff)]['reinforceTypeId'])
        need = OPT.requirements(tb, w, aff, lvl, False)
        floor = OPT.sweep_floor(tb.find_weapon(w), aff, lvl, False, need, self.rl)
        classes = list(OPT.RES.CLASS_ROWS)
        hit = OPT.Scorer(tb, w, aff, lvl, False, 'damage', self.dfn, grease)
        # S_hit re-spent today, so both sides meet today's floors; the sweep's own is kept beside.
        hit_best, cls_hit, s_hit = self.spend(hit, floor, need, classes)
        took = {'hit': time.time() - t0}
        twid = t.find_weapon(w)
        fixed = t.reg.weapon[twid].get('gemMountType') == 0
        names = {t.arts_name(s): s for s in A.mountable_skills(t, twid, AR.AFFINITIES.index(aff), lvl)}
        own = t.reg.weapon[twid]['swordArtsParamId']
        t1 = time.time()
        rows = [] if fixed else self.table_rows(w, aff, s_hit)
        took['table'] = time.time() - t1
        t1 = time.time()
        best_row = max(rows, key=lambda r: r['score'], default=None)
        if fixed:
            # The weapon's own skill is the only one it fires, so the table is not read: it also
            # leaves out a cast it cannot time (`untimed_hits`, Death's Poker's Ghostflame
            # Ignition), and timing does not move which stats a cast wants. A stance skill takes
            # the button whose cast deals more at S_hit, as the table keeps a row per button.
            btns = A.skill_profile(t, own, twid, lvl, False)['stance_buttons'] or [None]
            pool = [{'ash': t.arts_name(own), 'cat': None, 'score': None, 'stance': b} for b in btns]
        else:
            pool = [r for r in rows if r['cat'] != 'utility']
        cands = []
        for r in pool:
            sid = own if fixed else names.get(r['ash'])
            if sid is None or r['cat'] == 'utility':
                continue
            sc = AshScorer(self.lv, w, aff, sid, False, self.dfn, grease, r.get('stance'))
            at_hit = sc.score(s_hit)
            if at_hit <= 0:
                continue
            est = at_hit
            if not fixed:
                # A screen, so one plain spend (no conditioned floors) from S_hit's class, then the
                # table's own score of the ash at that spread. Not the score at S_hit scaled by the
                # cast's rise: a quick weapon's score is the cast plus its R1 string, which falls
                # where the R1s stop scaling (Star Fist on Heavy: Blinkbolt 616 so estimated, 248
                # scored, in the 2026-11-14 sample).
                got = OPT.spend_floored({k: max(self.model.class_base(cls_hit)[1][k], floor.get(k, 0),
                                                 need.get(k, 0)) for k in OPT.STATS}, self.rl, sc)
                if got is None:
                    continue
                again = [x for x in self.table_rows(w, aff, got[0], only={sid})
                         if x['ash'] == r['ash'] and x.get('stance') == r.get('stance')]
                if not again:
                    continue
                est = again[0]['score']
            cands.append({'row': r, 'sid': sid, 'scorer': sc, 'at_hit': at_hit, 'est': est})
        took['screen'] = time.time() - t1
        out = {'weapon': w, 'kind': row.get('kind'), 'fixed': fixed, 'affinity': aff, 'level': lvl,
               'grease': OPT.GREASE_NAMES[INF.TIER][gel] if gel else None,
               'sweep': {'class': cls_sweep, 'stats': s_sweep, 'same_spread': all(s_sweep[k] == s_hit[k]
                                                                                 for k in OPT.STATS)},
               'hit': {'class': cls_hit, 'stats': s_hit, 'damage': round(hit_best, 2)},
               'table_best_at_hit': {'ash': best_row['ash'], 'cat': best_row['cat'], 'score': best_row['score']}
               if best_row else None,
               'candidates': len(cands)}
        if not cands:
            sk = AshScorer(self.lv, w, aff, own, False, self.dfn, grease)
            d = sk.score(s_hit)
            comb = OPT.Scorer(tb, w, aff, lvl, False, 'hit_skill', self.dfn, grease, skill=own,
                              skill_weight=self.weight)
            out.update(skill={'name': t.arts_name(own), 'sid': own, 'kind': 'utility' if d <= 0 else 'none',
                              'class': cls_hit,
                              'stats': s_hit, 'damage': round(d, 2)},
                       cross={'hit_at_skill': round(hit_best, 2), 'skill_at_hit': round(d, 2)},
                       loss={'hit': 0.0, 'skill': 0.0},
                       comb={'skill': t.arts_name(own), 'class': cls_hit, 'stats': s_hit,
                             'value': round(comb.score(s_hit), 2), 'at_hit': round(comb.score(s_hit), 2),
                             'hit': round(hit_best, 2), 'skill_damage': round(d, 2)},
                       seconds=round(time.time() - t0, 1), took={k: round(v, 1) for k, v in took.items()})
            return out
        ch = max(cands, key=lambda c: c['est'])
        sc, sid, r0 = ch['scorer'], ch['sid'], ch['row']
        t1 = time.time()
        if self.every_class:
            skill_best, cls_skill, s_skill = self.spend(sc, floor, need, classes)
        else:
            skill_best, cls_skill, s_skill = self.spend_two(sc, floor, need, list(dict.fromkeys([cls_hit, 'Wretch'])))
        took['skill'] = time.time() - t1
        t1 = time.time()
        again = [] if fixed else [r for r in self.table_rows(w, aff, s_skill, only={sid})
                                  if r['ash'] == r0['ash'] and r.get('stance') == r0.get('stance')]
        took['rescore'] = time.time() - t1
        t1 = time.time()
        # The objective counts one skill. An ash weapon mounts whichever pays most, so it is spent
        # for the pick above and for the damaging ash the table ranks first at S_hit, and the
        # better of the two stands.
        at_hit_pick = max(cands, key=lambda c: c['row']['score'] if c['row']['score'] is not None else c['est'])
        combs = []
        for c, first in ((ch, [cls_hit, cls_skill]), (at_hit_pick, [cls_hit])):
            if combs and c is ch:
                continue
            cs = OPT.Scorer(tb, w, aff, lvl, False, 'hit_skill', self.dfn, grease, skill=c['sid'],
                            skill_weight=self.weight, stance=c['row'].get('stance'))
            if self.every_class:
                got = self.spend(cs, floor, need, classes)
            else:
                got = self.spend_two(cs, floor, need, list(dict.fromkeys(first)))
            combs.append({'skill': c['row']['ash'], 'stance': c['row'].get('stance'), 'class': got[1],
                          'stats': got[2], 'value': round(got[0], 2), 'at_hit': round(cs.score(s_hit), 2),
                          'hit': round(cs.hit_score(got[2]), 2), 'skill_damage': round(cs.skill_score(got[2]), 2),
                          'skill_hp_cost': round(cs.skill_hp_cost(got[2]), 2)})
        took['comb'] = time.time() - t1
        hit_at_skill, skill_at_hit = hit.score(s_skill), sc.score(s_hit)
        best_comb = max(combs, key=lambda c: c['value'])
        out.update(
            skill={'name': r0['ash'], 'sid': sid, 'stance': r0.get('stance'), 'cat': r0['cat'], 'kind': 'damaging',
                   'class': cls_skill, 'stats': s_skill, 'damage': round(skill_best, 2),
                   'table_score_at_hit': r0['score'],
                   'table_score_at_screen': None if fixed else ch['est'],
                   'table_score_at_skill': again[0]['score'] if again else None},
            cross={'hit_at_skill': round(hit_at_skill, 2), 'skill_at_hit': round(skill_at_hit, 2)},
            loss={'hit': round(1.0 - hit_at_skill / hit_best, 4) if hit_best else 0.0,
                  'skill': round(1.0 - skill_at_hit / skill_best, 4) if skill_best else 0.0},
            comb=dict(best_comb, at_hit=max(c['at_hit'] for c in combs)), comb_by_skill=combs,
            seconds=round(time.time() - t0, 1), took={k: round(v, 1) for k, v in took.items()})
        return out


def conflict_run(out_path, weapons=None, limit=None, skill_weight=None, every_class=False):
    """`Conflict.one` over the RL 150 sweep's one-handed rows (or the `weapons` named), appended to
    `out_path` one JSON line per weapon as each ends; a weapon already in the file is skipped, so
    a stopped run resumes."""
    import time
    rows = [r for r in INF.load_sweep() if not r['two']]
    if weapons:
        want = set(weapons)
        rows = [r for r in rows if r['weapon'] in want]
        missing = want - {r['weapon'] for r in rows}
        if missing:
            raise SystemExit(f'not in the RL {RL} sweep: {sorted(missing)}')
    done = set()
    if os.path.exists(out_path):
        with open(out_path) as fh:
            done = {json.loads(line)['weapon'] for line in fh if line.strip()}
    todo = [r for r in rows if r['weapon'] not in done][:limit]
    t0 = time.time()
    c = Conflict(skill_weight=skill_weight, every_class=every_class)
    print(f'setup {time.time() - t0:.1f}s; {len(todo)} weapons to measure, {len(done)} already in {out_path}',
          file=sys.stderr, flush=True)
    with open(out_path, 'a') as fh:
        for i, r in enumerate(todo, 1):
            try:
                res = c.one(r)
            except (Exception, SystemExit) as e:  # named in the report's `skipped`, never dropped
                res = {'weapon': r['weapon'], 'skipped': repr(e)}
            fh.write(json.dumps(res) + '\n')
            fh.flush()
            print(f"[{i}/{len(todo)}] {r['weapon']}: {res.get('seconds', '-')}s"
                  + (f" skipped {res['skipped']}" if 'skipped' in res else
                     f" skill {res['skill']['name']}, loss hit {res['loss']['hit']:.1%} skill {res['loss']['skill']:.1%}"),
                  file=sys.stderr, flush=True)
    import resource
    print(f'measured {len(todo)} weapons in {time.time() - t0:.0f}s, peak resident '
          f'{resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.0f} MB', file=sys.stderr, flush=True)


def conflict_report(path, top=25):
    """The weapons whose hit and skill want the most different stats, and how the ranking moves.

    Ranks are over the weapons in `path` (`INF.CARD.rank`, 1 = best): by the hit at S_hit (today's
    ranking), by the `hit_skill` objective at S_hit (the skill counted, stats unchanged) and at
    S_comb (the skill counted and the stats re-spent for it). `gain` is S_comb's objective over
    S_hit's: what re-spending for the skill recovers, zero when the hit's stats already serve it."""
    with open(path) as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    ok = [r for r in rows if 'skipped' not in r]
    hits = [r['hit']['damage'] for r in ok]
    at_hit = [r['comb']['at_hit'] for r in ok]
    comb = [r['comb']['value'] for r in ok]
    for r in ok:
        r['rank_hit'] = INF.CARD.rank(hits, r['hit']['damage'], 'high')[0]
        r['rank_at_hit'] = INF.CARD.rank(at_hit, r['comb']['at_hit'], 'high')[0]
        r['rank_comb'] = INF.CARD.rank(comb, r['comb']['value'], 'high')[0]
        r['gain'] = r['comb']['value'] / r['comb']['at_hit'] - 1.0 if r['comb']['at_hit'] else 0.0
    out = {'weapons': len(rows), 'measured': len(ok), 'skipped': [(r['weapon'], r['skipped']) for r in rows
                                                                   if 'skipped' in r],
           'seconds': round(sum(r.get('seconds', 0) for r in ok), 1),
           'damaging_skill': sum(r['skill']['kind'] == 'damaging' for r in ok),
           'skill_moves_stats': sum(r['loss']['skill'] > 0.005 for r in ok),
           'sweep_spread_drift': sum(not r['sweep']['same_spread'] for r in ok),
           'rows': sorted(ok, key=lambda r: -r['gain'])[:top]}
    return out


def _spread(st):
    return '/'.join(str(st[k]) for k in DAMAGE_STATS)


def print_conflict_report(rep):
    print(f"{rep['measured']} of {rep['weapons']} weapons measured in {rep['seconds']:.0f}s of weapon time; "
          f"{rep['damaging_skill']} carry a damaging skill, {rep['skill_moves_stats']} of them lose over 0.5% of "
          f"the skill at the hit's stats; {rep['sweep_spread_drift']} re-spent S_hit differ from the cached sweep's")
    if rep['skipped']:
        print('skipped: ' + '; '.join(f'{w} ({why})' for w, why in rep['skipped']))
    print(f"{'weapon':<26} {'aff':<9} {'skill':<24} {'S_hit STR/DEX/INT/FTH/ARC':<26} {'S_skill':<16} "
          f"{'hit loss':>8} {'skill loss':>10} {'gain':>6}  rank hit > +skill > re-spent  [objective's skill: S_comb]")
    for r in rep['rows']:
        c = r['comb']
        print(f"{r['weapon'][:26]:<26} {r['affinity'][:9]:<9} {r['skill']['name'][:24]:<24} "
              f"{_spread(r['hit']['stats']):<26} {_spread(r['skill']['stats']):<16} "
              f"{r['loss']['hit']:>8.1%} {r['loss']['skill']:>10.1%} {r['gain']:>6.1%}  "
              f"{r['rank_hit']} > {r['rank_at_hit']} > {r['rank_comb']}  [{c['skill']}: {_spread(c['stats'])}]")


def selftest():
    lv = LEV.Levers()
    r = optimal('Lance', rl=RL, levers=lv)
    st = r['stats']
    assert sum(st.values()) - OPT.LEVEL_OFFSET == RL, st
    req = OPT.requirements(AR.Tables(None), 'Lance', r['affinity'], r['level'], False)
    assert all(st[k] >= req[k] for k in DAMAGE_STATS), (st, req)
    assert all(st[k] >= r['survival_floors'][k] for k in OPT.SURVIVAL), r
    # Charge Forth is all weapon motion value, so the skill wants what the weapon's own build wants.
    assert r['skill'] == 'Charge Forth' and r['skill_damage'] > 0, r
    w = r['weapon_build']['stats']
    assert abs(st['str'] - w['str']) <= 3, (st, w)
    # Five weapon hits and no bullet; on Heavy only Strength scales them.
    groups = {g['group']: g for g in r['contributions']}
    assert set(groups) == {'weapon'} and groups['weapon']['hits'] == 5, r['contributions']
    assert abs(sum(g['damage'] for g in groups.values()) - r['skill_damage']) < 0.5, r
    parts = {p['source']: p['damage'] for p in groups['weapon']['parts']}
    assert set(parts) == {'base', 'str', 'grease'}, parts
    # The split reproduces each hit's attack, at stats that scale on several elements too.
    for weapon, aff, case in (('Lance', 'Heavy', st),
                              ('Lance', 'Quality', {'str': 40, 'dex': 40, 'int': 9, 'fth': 9, 'arc': 9}),
                              ('Lance', 'Lightning', {'str': 20, 'dex': 60, 'int': 9, 'fth': 9, 'arc': 9})):
        sc = AshScorer(lv, weapon, aff, r['skill_id'], False, None, None)
        ctx = A.WeaponContext(weapon, aff, stats={k: case[k] for k in DAMAGE_STATS}, ar_tables=lv.ar_tables)
        for h in sc.hits(case):
            got, want = sum(hit_sources(ctx, h, None).values()), sum(h['attack'].values())
            assert abs(got - want) < 0.05, (aff, h['atk_row'], got, want)
    # A skill with a bullet gets a bullet group.
    lc = optimal('Claymore', 'Lightning Slash', levers=lv)
    assert 'bullet' in {g['group'] for g in lc['contributions']}, lc['contributions']
    print(f"selftest ok: Lance / {r['skill']} at RL {RL}: {r['affinity']}"
          f"{' + ' + r['grease'] if r['grease'] else ''}, {r['class']}, "
          + ' '.join(f'{k}={st[k]}' for k in OPT.STATS) + f", {r['skill_damage']} per cast")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('weapon', nargs='?')
    ap.add_argument('--skill', help="skill name; the weapon's own when left out")
    ap.add_argument('--rl', type=int, default=RL)
    ap.add_argument('--two-handed', action='store_true')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--conflict', metavar='OUT.jsonl',
                    help='measure every RL 150 sweep weapon (or --weapons) into this file (`Conflict`), resuming')
    ap.add_argument('--weapons', help='--conflict: comma-separated weapons instead of the whole sweep')
    ap.add_argument('--limit', type=int, help='--conflict: measure at most this many more weapons')
    ap.add_argument('--skill-weight', type=float, help="--conflict: the hit_skill objective's skill share")
    ap.add_argument('--every-class', action='store_true',
                    help='--conflict: spend the skill and the hit_skill objective from every class (about 10x '
                         'the spends), not through `Conflict.spend_two`')
    ap.add_argument('--conflict-report', metavar='OUT.jsonl', help='rank and print a --conflict file')
    ap.add_argument('--top', type=int, default=25)
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.conflict:
        conflict_run(a.conflict, [w.strip() for w in a.weapons.split(',')] if a.weapons else None, a.limit,
                     a.skill_weight, a.every_class)
        return 0
    if a.conflict_report:
        rep = conflict_report(a.conflict_report, a.top)
        if a.json:
            json.dump(rep, sys.stdout, indent=1)
        else:
            print_conflict_report(rep)
        return 0
    if not a.weapon:
        ap.error('weapon required')
    r = optimal(a.weapon, a.skill, a.rl, a.two_handed)
    if a.json:
        json.dump(r, sys.stdout, indent=1)
        return 0
    st = r['stats']
    print(f"{a.weapon} / {r['skill']} at RL {r['rl']}{' two-handed' if r['two_handed'] else ''}: "
          f"{r['affinity']} +{r['level']}{' with ' + r['grease'] if r['grease'] else ''}, from a {r['class']} start")
    print('  ' + ' '.join(f'{k}={st[k]}' for k in OPT.STATS))
    print(f"  survival floors from PvP builds: " + ' '.join(f'{k}={v}' for k, v in r['survival_floors'].items()))
    print(f"  {r['skill_damage']} damage per cast on the median of {r['defender_builds']} defenders")
    print(f"  weapon's own best spread: " + ' '.join(f'{k}={v}' for k, v in r['weapon_build']['stats'].items()))
    for g in r['contributions']:
        print(f"  {g['group']}: {g['hits']} hits, {g['damage']} per cast: "
              + ', '.join(f"{p['source']} {p['damage']} ({p['pct']}%)" for p in g['parts']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
