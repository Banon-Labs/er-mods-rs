#!/usr/bin/env python3
"""The stats a rune-level 150 build puts into one weapon to hit hardest with one skill.

    python3 scripts/er-mechanics-ash-stats.py Lance
    python3 scripts/er-mechanics-ash-stats.py Lance --skill "Lion's Claw" --two-handed --json
    python3 scripts/er-mechanics-ash-stats.py --selftest

Three steps, each borrowed from a tool that already makes that decision, so this file adds only
the last one:

1. Vigor, Mind and Endurance are what PvP builds at that RL actually carry: the median of the
   corpus's PvP builds in RL +- 10 of the weapon's stat archetype, Endurance raised until the
   weapon keeps medium roll (`er-mechanics-infusions.Builder.setup`, which is
   `er-builds-optimize.floors` and `medium_roll_end`). Those points come out of the pool first.
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

    def __init__(self, levers, weapon, affinity, sid, two_handed, defender, grease):
        self.levers, self.weapon, self.affinity, self.sid = levers, weapon, affinity, sid
        self.two_handed, self.defender, self.grease = two_handed, defender, grease
        self.wid = levers.t.find_weapon(weapon)
        self.cache = {}

    def hits(self, st):
        _, hits, _ = self.levers.cast(self.weapon, self.affinity, self.sid,
                                      {k: st[k] for k in DAMAGE_STATS}, self.two_handed)
        if self.grease:
            el, flat = self.grease
            for h in hits:
                if h['kind'] == 'melee' and h['from_weapon']:
                    by_point = self.levers.t.atk_extra.get(h['atk_row'], {}).get(
                        'spEffectAtkPowerCorrectRate_byPoint', 100) / 100.0
                    h['attack'] = dict(h['attack'], **{el: h['attack'].get(el, 0.0) + flat * by_point})
        return hits

    def score(self, st):
        key = tuple(st[k] for k in DAMAGE_STATS)
        if key not in self.cache:
            self.cache[key] = A.pvp_damage(self.levers.t, self.wid, self.hits(st), self.defender, OPT.DEF)
        return self.cache[key]


#: The groups a cast's hits are split into for `contributions`, by `skill_hits` kind.
HIT_GROUPS = (('weapon', 'melee'), ('bullet', 'bullet'))
STEP = 10


def contributions(scorer, stats, step=STEP):
    """For each group of the skill's hits that exists: hits per cast, damage per cast after PvP
    defense, and per damage stat what `step` more points of it add to that damage.

    Weapon hits are the skill's melee hits, which take the weapon's attack times their motion
    value (`hit_attack`); bullets take their own flat attack and the scaling of their attack
    element row. A stat that adds nothing to a group is reported with 0, so the page can say so."""
    def by_group(st):
        hits = scorer.hits(st)
        out = {}
        for name, kind in HIT_GROUPS:
            sel = [h for h in hits if h['kind'] == kind]
            if sel:
                out[name] = (A.pvp_damage(scorer.levers.t, scorer.wid, sel, scorer.defender, OPT.DEF),
                             sum(h.get('count', 1) for h in sel))
        return out
    base = by_group(stats)
    raised = {k: by_group(dict(stats, **{k: min(OPT.STAT_CAP, stats[k] + step)})) for k in DAMAGE_STATS}
    out = []
    for name, _ in HIT_GROUPS:
        if name not in base:
            continue
        dmg, n = base[name]
        rows = []
        for k in DAMAGE_STATS:
            gain = raised[k][name][0] - dmg
            rows.append({'stat': k, 'value': stats[k], 'gain': round(gain, 1),
                         'pct': round(100.0 * gain / dmg, 2) if dmg else 0.0})
        rows.sort(key=lambda r: -r['gain'])
        out.append({'group': name, 'hits': n, 'damage': round(dmg, 1), 'step': step, 'stats': rows})
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
                               INF.TIER, keep=1)
    if not weapon_best:
        raise SystemExit(f'no RL {rl} build wields {weapon}')
    wb = weapon_best[0]
    aff, level, grease = wb['affinity'], wb['level'], wb['grease']
    need = OPT.requirements(b.tables, weapon, aff, level, two_handed)
    best = None
    for cls in OPT.RES.CLASS_ROWS:
        cls_level, base = b.model.class_base(cls)
        st = {k: max(base[k], fl.get(k, 0), need.get(k, 0)) for k in OPT.STATS}
        points = rl - (sum(st.values()) - OPT.LEVEL_OFFSET)
        if points < 0 or rl < cls_level:
            continue
        scorer = AshScorer(levers, weapon, aff, sid, two_handed, dfn, grease)
        scorer.floor = dict(st)
        st, left = OPT.spend(st, points, scorer)
        for k in ('vig', 'vit', 'mnd'):
            add = min(left, OPT.STAT_CAP - st[k])
            st[k] += add
            left -= add
        dmg = scorer.score(st)
        if best is None or dmg > best['skill_damage']:
            best = {'class': cls, 'stats': st, 'skill_damage': dmg, 'scorer': scorer}
    floors = {k: max(fl[k], b.model.class_base(best['class'])[1][k]) for k in OPT.SURVIVAL}
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
    gains = {s['stat']: s['gain'] for s in groups['weapon']['stats']}
    assert gains['str'] > 0 and all(v <= 0.05 for k, v in gains.items() if k != 'str'), gains
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
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
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
        print(f"  {g['group']}: {g['hits']} hits, {g['damage']} per cast; +{g['step']} points: "
              + ', '.join(f"{s['stat']} +{s['gain']} ({s['pct']}%)" for s in g['stats']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
