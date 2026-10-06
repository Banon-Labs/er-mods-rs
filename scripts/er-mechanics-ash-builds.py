#!/usr/bin/env python3
"""Every ash a weapon can fire, each at the RL 150 build that hits hardest with it, per affinity.

    python3 scripts/er-mechanics-ash-builds.py Lance > lance-ash-builds.json
    python3 scripts/er-mechanics-ash-builds.py Lance --skill "Flaming Strike" --text
    python3 scripts/er-mechanics-ash-builds.py --selftest

A skill is not ranked at one stat block: Flaming Strike on a Flame Art weapon is a Faith build and on
a Heavy one a Strength build, and each is judged at its own best spread. So for every skill the
weapon can fire (`er-mechanics-ar-export.ash_options`), every affinity the skill allows and both
grips, the spread is built the way `er-mechanics-ash-stats.optimal` builds it:

* Vigor, Mind and Endurance are the RL window's PvP floors (`er-mechanics-infusions.Builder.setup`).
* Every requirement of the weapon at that affinity and grip is met.
* The remaining points go where they add the most damage to one cast after PvP defense on the
  window's median defender (`AshScorer`, `er-builds-optimize.spend`). The starting classes tried
  are the `CLASS_CANDIDATES` that leave the most points free after the floors and requirements.
* A greasable affinity is then tried with each Drawstring grease at that spread and keeps the best.
  Grease is flat and does not scale with stats, so it is chosen after the spread rather than inside
  it (it moves the optimum only through defense, which is not linear).

Each build carries its primary stats: the damage stats whose scaling makes up at least
`PRIMARY_SHARE` of the cast's damage (`er-mechanics-ash-stats.contributions`). For the page, an
ash keeps one row per distinct set of primary stats, the affinity that hits hardest with it.
"""
import argparse
import importlib.util
import json
import multiprocessing as mp
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _mod(name, fname):
    s = importlib.util.spec_from_file_location(name, os.path.join(HERE, fname))
    m = importlib.util.module_from_spec(s)
    sys.modules[name] = m
    s.loader.exec_module(m)
    return m


STATS_MOD = _mod('er_mechanics_ash_stats', 'er-mechanics-ash-stats.py')
EXPORT = _mod('er_mechanics_ar_export', 'er-mechanics-ar-export.py')
OPT, INF, A, AR = STATS_MOD.OPT, STATS_MOD.INF, STATS_MOD.A, STATS_MOD.AR
DAMAGE_STATS = OPT.DAMAGE_STATS
RL = 150
#: Starting classes tried per build, the ones with the most points left after floors and requirements.
CLASS_CANDIDATES = 4
#: A damage stat is primary when its scaling makes up this share of the cast's damage, in percent.
PRIMARY_SHARE = 10.0

_JOB: dict = {}


def _build(job):
    sid, aff, two = job
    o = _JOB
    levers, b, fl, dfn, weapon = o['levers'], o['b'], o['fl'], o['dfn'], o['weapon']
    try:
        probe = AR.attack_rating(b.tables, weapon, aff, 0, {k: 10 for k in DAMAGE_STATS}, two)
    except (KeyError, ValueError, SystemExit):
        return None
    level = probe['max_level']
    need = OPT.requirements(b.tables, weapon, aff, level, two)
    starts = []
    for cls in OPT.RES.CLASS_ROWS:
        cls_level, base = b.model.class_base(cls)
        st = {k: max(base[k], fl.get(k, 0), need.get(k, 0)) for k in OPT.STATS}
        points = o['rl'] - (sum(st.values()) - OPT.LEVEL_OFFSET)
        if points >= 0 and o['rl'] >= cls_level:
            starts.append((points, cls, st))
    starts.sort(key=lambda x: -x[0])
    best = None
    for points, cls, st in starts[:CLASS_CANDIDATES]:
        scorer = STATS_MOD.AshScorer(levers, weapon, aff, sid, two, dfn, None)
        scorer.floor = dict(st)
        st, left = OPT.spend(st, points, scorer)
        for k in ('vig', 'vit', 'mnd'):
            add = min(left, OPT.STAT_CAP - st[k])
            st[k] += add
            left -= add
        dmg = scorer.score(st)
        if best is None or dmg > best[0]:
            best = (dmg, cls, st)
    if best is None:
        return None
    dmg, cls, st = best
    grease = None
    for g in OPT.grease_options(b.tables, weapon, aff, INF.TIER):
        if g is None:
            continue
        d = STATS_MOD.AshScorer(levers, weapon, aff, sid, two, dfn, g).score(st)
        if d > dmg:
            dmg, grease = d, g
    scorer = STATS_MOD.AshScorer(levers, weapon, aff, sid, two, dfn, grease)
    hits = scorer.hits(st)
    A.pvp_damage(levers.t, scorer.wid, hits, dfn, OPT.DEF)
    parts = STATS_MOD.contributions(scorer, st)
    total = sum(g['damage'] for g in parts)
    share = {k: 0.0 for k in DAMAGE_STATS}
    for g in parts:
        for p in g['parts']:
            if p['source'] in share:
                share[p['source']] += p['damage']
    primary = [k for k in sorted(DAMAGE_STATS, key=lambda k: -share[k])
               if total and 100.0 * share[k] / total >= PRIMARY_SHARE]
    return {'skill_id': sid, 'affinity': aff, 'two_handed': two, 'level': level, 'class': cls, 'stats': st,
            'grease': OPT.GREASE_NAMES[INF.TIER][grease[0]] if grease else None,
            'damage': round(dmg, 1), 'first_hit': round(hits[0]['pvp_damage_total'], 1) if hits else 0.0,
            'primary': primary, 'contributions': parts}


def ash_builds(weapon, rl=RL, skill=None, jobs=None):
    """{'options': [...], 'unranked': [...], 'builds': [...]}: `ash_options` plus one build per
    (skill, affinity, grip)."""
    tables = AR.Tables(None)
    ashes = EXPORT.ash_options(tables, weapon)
    if skill:
        ashes['options'] = [s for s in ashes['options'] if s['name'] == skill]
        if not ashes['options']:
            raise SystemExit(f'{skill} is not a skill {weapon} can fire with a hit')
    levers = EXPORT._levers()[1]
    b = INF.Builder(weapon)
    fl, dfn = b.setup(rl)
    _JOB.update(levers=levers, b=b, fl=fl, dfn=dfn, weapon=weapon, rl=rl)
    work = [(s['id'], aff, two) for s in ashes['options'] for aff in s['affinities'] for two in (False, True)]
    with mp.get_context('fork').Pool(jobs) as pool:
        builds = [r for r in pool.map(_build, work, chunksize=1) if r]
    return {'weapon': weapon, 'rl': rl, 'floors': fl, 'defender_builds': dfn['n'], 'primary_share': PRIMARY_SHARE,
            'options': ashes['options'], 'unranked': ashes['unranked'], 'builds': builds}


def distinct(builds):
    """Per skill and grip, the hardest-hitting build of each distinct set of primary stats."""
    keep = {}
    for r in builds:
        key = (r['skill_id'], r['two_handed'], tuple(sorted(r['primary'])))
        if key not in keep or r['damage'] > keep[key]['damage']:
            keep[key] = r
    return sorted(keep.values(), key=lambda r: -r['damage'])


def selftest():
    printed = []
    for skill in ('Flaming Strike', 'Charge Forth'):
        out = ash_builds('Lance', skill=skill)
        one = [r for r in out['builds'] if not r['two_handed']]
        by_aff = {r['affinity']: r for r in one}
        for r in one:
            st = r['stats']
            assert sum(st.values()) - OPT.LEVEL_OFFSET == RL, r
            assert all(st[k] >= out['floors'][k] for k in OPT.SURVIVAL), r
        if skill == 'Flaming Strike':
            assert set(by_aff) == {'Standard', 'Heavy', 'Keen', 'Quality', 'Fire', 'Flame Art'}, sorted(by_aff)
            # Its bullet's AtkParam overrides the attack element row to 51040, which scales fire with
            # Strength only, so every affinity is a Strength build of it -- Flame Art included.
            assert all(r['primary'] == ['str'] for r in one), [(r['affinity'], r['primary']) for r in one]
        else:
            # Weapon hits take the weapon's scaling: Flame Art pulls in Faith, Heavy does not.
            assert 'fth' in by_aff['Flame Art']['primary'], by_aff['Flame Art']
            assert 'fth' not in by_aff['Heavy']['primary'], by_aff['Heavy']
            assert len(distinct(one)) >= 2, distinct(one)
        printed.append((skill, distinct(one)))
    print('selftest ok: distinct one-handed Lance builds per skill:')
    for skill, rows in printed:
        for r in rows:
            print(f"  {skill:<15} {r['affinity']:<10} {'/'.join(r['primary']):<10} {r['damage']:>7} "
                  + ' '.join(f"{k}={r['stats'][k]}" for k in DAMAGE_STATS))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('weapon', nargs='?')
    ap.add_argument('--skill', help='only this skill')
    ap.add_argument('--rl', type=int, default=RL)
    ap.add_argument('--jobs', type=int)
    ap.add_argument('--text', action='store_true', help='print the distinct builds instead of JSON')
    ap.add_argument('--page', metavar='JSON',
                    help="reduce a saved run to what the Ashes page reads (its `DATA.ash_builds`) and print it")
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.page:
        with open(a.page) as fh:
            out = json.load(fh)
        keys = ('skill_id', 'affinity', 'two_handed', 'class', 'stats', 'grease', 'damage', 'first_hit', 'primary')
        json.dump({'weapon': out['weapon'], 'rl': out['rl'], 'floors': out['floors'],
                   'defender_builds': out['defender_builds'],
                   'rows': [{k: r[k] for k in keys} for r in distinct(out['builds'])]}, sys.stdout, separators=(',', ':'))
        return 0
    if not a.weapon:
        ap.error('weapon required')
    out = ash_builds(a.weapon, a.rl, a.skill, a.jobs)
    if a.text:
        names = {s['id']: s['name'] for s in out['options']}
        for r in distinct(out['builds']):
            print(f"{names[r['skill_id']]:<28} {'2h' if r['two_handed'] else '1h'} {r['affinity']:<10} "
                  f"{'/'.join(r['primary']) or '-':<10} {r['damage']:>7}  "
                  + ' '.join(f"{k}={r['stats'][k]}" for k in DAMAGE_STATS))
        return 0
    json.dump(out, sys.stdout, separators=(',', ':'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
