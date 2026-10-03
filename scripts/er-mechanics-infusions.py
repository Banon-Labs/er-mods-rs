#!/usr/bin/env python3
"""A weapon's best infusions: each one's scaling, the rune levels where it is the best pick, and
how its best RL 150 build ranks against every other weapon's best RL 150 build.

    python3 scripts/er-mechanics-infusions.py Lance
    python3 scripts/er-mechanics-infusions.py Lance --json
    python3 scripts/er-mechanics-infusions.py --selftest

Builds come from `er-builds-optimize.py`, not from a second model of how a player levels:
Vigor, Mind and Endurance are floors from the corpus's PvP builds at that RL (raised for medium
roll), every requirement is met, every starting class is tried, and the remaining points go where
they add the most damage (one motion-value-100 hit on the median defender of the RL window,
`docs/er-mechanics/defense.md`). A buffable affinity (Standard, Heavy, Keen, Quality on most
weapons; `isEnhance`) is also tried with each DLC Drawstring grease, +135 of one element added
after every multiplier (`docs/er-mechanics/grease.md`); a fire, lightning, magic, holy or other
elemental infusion cannot be greased. That is why a physical affinity plus grease overtakes the
elemental infusions once STR or DEX is high enough, and the RL where that happens is reported.

One-handed throughout. AR is the elements' sum with the grease included, before defenses; damage
is the optimizer's per-hit figure.

The RL 150 ranking reads `er-builds-optimize.py --grease-sweep 150-150 --every-class` (every
melee armament's best ungreasable, greased and Quality build at RL 150, one-handed, from its best
starting class). Each weapon's AR is recomputed from that build's stats with
`er-mechanics-ar.attack_rating`, its best configuration is the one with the most damage, and the
weapon is placed by AR and by damage among all of them.

Scaling numbers are the weapon row's `correct*` times the reinforce row's `correct*Rate` at the
top level: the number behind the letter grade the game shows.
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


OPT = _mod('er_builds_optimize', 'er-builds-optimize.py')
AR = OPT.AR
CARD = _mod('er_mechanics_weapon_card', 'er-mechanics-weapon-card.py')
STATS = ('str', 'dex', 'int', 'fth', 'arc')
LABEL = {'str': 'STR', 'dex': 'DEX', 'int': 'INT', 'fth': 'FTH', 'arc': 'ARC', 'vig': 'VIG', 'mnd': 'MND',
         'vit': 'END'}
TIER = 'dlc-drawstring'
RANK_RL = 150
RL_SWEEP = tuple(range(60, 201, 10))


def scaling(tables, weapon, affinity):
    """{stat: the scaling number at the top level}, only stats that scale."""
    w = tables.weapons[tables.find_weapon(weapon, affinity)]
    rf = tables.reinforce[w['reinforceTypeId'] + tables.max_level(w['reinforceTypeId'])]
    out = {}
    for s in STATS:
        _, cfield, rfield, _ = AR.STAT_FIELDS[s]
        v = w[cfield] * rf[rfield]
        if v > 0:
            out[s] = round(v, 1)
    return out


class Builder:
    """`er-builds-optimize`'s weapon path, one RL at a time."""

    def __init__(self, weapon, mirror=OPT.CACHE / 'builds.jsonl'):
        self.tables = AR.Tables(None)
        self.model = OPT.RES.Model()
        self.weapon = weapon
        self.base = self.tables.find_weapon(weapon)
        self.mirror = mirror
        self.affs = OPT.affinities(self.tables, self.base)

    def setup(self, rl, window=10, min_peers=15):
        rows = OPT.corpus_rows(self.mirror, rl - window, rl + window)
        aff0 = 'Standard' if 'Standard' in self.affs else self.affs[0]
        lvl0 = self.tables.max_level(self.tables.weapons[self.tables.find_weapon(self.weapon, aff0)]['reinforceTypeId'])
        arch = OPT.candidate_archetype(self.tables, self.weapon, aff0, lvl0, False,
                                       OPT.requirements(self.tables, self.weapon, aff0, lvl0, False))
        fl, _, _ = OPT.floors(rows, self.weapon, min_peers, 'pvp', arch)
        need_end = OPT.medium_roll_end(self.tables.weapons[self.base]['weight'],
                                       OPT.load_pool(rows, 'pvp', arch, min_peers))
        fl['vit'] = max(fl['vit'], need_end)
        return fl, OPT.bracket_defender(rows)

    def best(self, rl, affinity, fl, dfn):
        res = OPT.optimize(self.tables, self.model, self.weapon, rl, False, 'damage', fl, dfn, [affinity], TIER,
                           keep=1)
        if not res:
            return None
        r = res[0]
        g = r.get('grease')
        return {'affinity': affinity, 'rl': rl, 'class': r['class'], 'stats': r['stats'],
                'damage': round(r['score'], 1),
                'ar': round(sum(r['by_element'].values()), 1),
                'grease': OPT.GREASE_NAMES[TIER][g[0]] if g else None, 'grease_flat': g[1] if g else 0}


def rl_spans(b, rls=RL_SWEEP):
    """[{affinity, from, to}]: the affinity whose best build does the most damage at each RL."""
    spans = []
    for rl in rls:
        fl, dfn = b.setup(rl)
        builds = [x for x in (b.best(rl, a, fl, dfn) for a in b.affs) if x]
        if not builds:
            continue
        top = max(builds, key=lambda x: x['damage'])
        if spans and spans[-1]['affinity'] == top['affinity']:
            spans[-1]['to'] = rl
        else:
            spans.append({'affinity': top['affinity'], 'from': rl, 'to': rl, 'grease': top['grease']})
    return spans


def sweep_path(rl=RANK_RL):
    return OPT.CACHE / f'grease-sweep-{TIER}-{rl}-{rl}-every-class.jsonl'


def load_sweep(rl=RANK_RL):
    """The every-class grease sweep at `rl`. It takes minutes, so it is built by its own command
    rather than from here."""
    path = sweep_path(rl)
    if not path.exists():
        raise SystemExit(f"no RL {rl} sweep at {path}; build it with:\n  python3 "
                         f"{os.path.join(HERE, 'er-builds-optimize.py')} --grease-sweep {rl}-{rl} --grip 1h "
                         f"--jobs 16 --no-weight-charge --every-class")
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def sweep_best(tables, row):
    """(damage, AR, affinity) of a sweep row's best configuration."""
    best = None
    for key in ('elemental', 'greased', 'quality'):
        c = row.get(key)
        if not c:
            continue
        aff = c.get('aff') or ('Quality' if key == 'quality' else 'Standard')
        try:
            wid = tables.find_weapon(row['weapon'], aff)
        except SystemExit:
            continue
        lvl = tables.max_level(tables.weapons[wid]['reinforceTypeId'])
        ar = sum(v['total'] for v in AR.attack_rating(tables, row['weapon'], aff, lvl,
                                                      {k: c['stats'][k] for k in STATS}, False)['damage'].values())
        ar += OPT.GREASES[TIER] if c.get('grease') else 0
        if best is None or c['dmg'] > best[0]:
            best = (c['dmg'], ar, aff, c.get('class'))
    return best


def rank_at_150(tables, weapon, rows):
    bests = {r['weapon']: sweep_best(tables, r) for r in rows if not r['two']}
    bests = {k: v for k, v in bests.items() if v}
    me = bests.get(weapon)
    if not me:
        return None
    dmg = [v[0] for v in bests.values()]
    ar = [v[1] for v in bests.values()]
    return {'weapon': weapon, 'affinity': me[2], 'class': me[3], 'damage': round(me[0], 1), 'ar': round(me[1], 1),
            'ar_rank': CARD.rank(ar, me[1], 'high'), 'damage_rank': CARD.rank(dmg, me[0], 'high'),
            'of': len(bests)}


def a_class(name):
    """`a Heavy Knight`, `an Astrologer`: the starting classes beginning with a vowel."""
    return f"{'an' if name[:1].lower() in 'aeiou' else 'a'} {name}"


def _stats_line(st, keys=STATS):
    return ' / '.join(f'{LABEL[k]} {st[k]}' for k in keys if st[k] > 10)


def report(weapon, top=3, rls=RL_SWEEP):
    b = Builder(weapon)
    fl, dfn = b.setup(RANK_RL)
    at150 = sorted((x for x in (b.best(RANK_RL, a, fl, dfn) for a in b.affs) if x), key=lambda x: -x['damage'])
    spans = rl_spans(b, rls)
    rank = rank_at_150(b.tables, b.tables.names.get(b.base), load_sweep())
    order = []
    for s in spans:
        if s['affinity'] not in order:
            order.append(s['affinity'])
    order += [x['affinity'] for x in at150 if x['affinity'] not in order]
    out = []
    for a in order[:top]:
        build = next((x for x in at150 if x['affinity'] == a), None)
        sp = [s for s in spans if s['affinity'] == a]
        out.append({'affinity': a, 'scaling': scaling(b.tables, weapon, a), 'rl150': build, 'best_at_rl': sp,
                    'text': describe(a, scaling(b.tables, weapon, a), build, sp, rls)})
    kinds = {a: category(b.tables, weapon, a, b.affs) for a in b.affs}
    by_category = {}
    for kind in CATEGORIES:
        build = next((x for x in at150 if kinds[x['affinity']] == kind), None)
        if build is None:
            continue
        a = build['affinity']
        sp = [s for s in spans if s['affinity'] == a]
        text = describe(a, scaling(b.tables, weapon, a), build, sp, rls)
        buildup = status_buildup(b.tables, weapon, a, build['stats'])
        if buildup:
            text += ' ' + ', '.join(f'{k.capitalize()} buildup {v:.0f}' for k, v in buildup.items()) + '.'
        by_category[kind] = {'affinity': a, 'scaling': scaling(b.tables, weapon, a), 'rl150': build,
                             'best_at_rl': sp, 'status': buildup, 'text': text}
    return {'weapon': b.tables.names.get(b.base), 'floors_rl150': fl, 'rl_sweep': list(rls), 'spans': spans,
            'top': out, 'categories': kinds, 'by_category': by_category, 'rank_rl150': rank,
            'rank_text': rank_text(rank) if rank else None}


CATEGORIES = ('physical', 'elemental', 'status')
CATEGORY_PROBE_STAT = 40


def status_buildup(tables, weapon, affinity, stats):
    """`{status: buildup}` of `affinity` at the top level with `stats`, from the AR model."""
    wid = tables.find_weapon(weapon, affinity)
    lvl = tables.max_level(tables.weapons[wid]['reinforceTypeId'])
    r = AR.attack_rating(tables, weapon, affinity, lvl, {k: stats[k] for k in STATS}, False)
    return {k: round(v['total'], 1) for k, v in r.get('status', {}).items() if v['total'] > 0}


def category(tables, weapon, affinity, affs):
    """`physical`, `elemental` or `status`: what `affinity` adds over the weapon's base affinity.

    Read off the AR model at every stat `CATEGORY_PROBE_STAT` and the top level, so a weapon's own
    innate bleed or element does not make every infusion of it count. Status when it adds a
    buildup the base lacks (Cold adds frost and magic, and is status); elemental when it adds a
    damage element other than physical; physical otherwise (Occult only rescales)."""
    base = 'Standard' if 'Standard' in affs else affs[0]

    def added(aff):
        wid = tables.find_weapon(weapon, aff)
        lvl = tables.max_level(tables.weapons[wid]['reinforceTypeId'])
        r = AR.attack_rating(tables, weapon, aff, lvl, {k: CATEGORY_PROBE_STAT for k in STATS}, False)
        return ({k for k, v in r['damage'].items() if v['total'] > 0},
                {k for k, v in r.get('status', {}).items() if v['total'] > 0})

    dmg0, st0 = added(base)
    dmg, st = added(affinity)
    if st - st0:
        return 'status'
    if {k for k in dmg - dmg0 if k != 'physical'}:
        return 'elemental'
    return 'physical'


def describe(aff, sc, build, spans, rls):
    s = ', '.join(f'{LABEL[k]} {v:g}' for k, v in sorted(sc.items(), key=lambda kv: -kv[1]))
    line = f'Scaling {s}.'
    if build:
        line += (f" At RL 150 from {a_class(build['class'])} start: {_stats_line(build['stats'])}, {build['ar']:.0f} AR"
                 + (f" with {build['grease']}" if build['grease'] else '') + '.')
    if spans:
        parts = [(f"RL {x['from']}-{x['to']}" if x['from'] != x['to'] else f"RL {x['from']}") for x in spans]
        line += f" The best pick at {', '.join(parts)}"
        last = spans[-1]['to']
        line += ('.' if last == rls[-1] else f'; past RL {last} another infusion does more.')
    else:
        line += f' Never the best pick between RL {rls[0]} and {rls[-1]}.'
    return line


def rank_text(r):
    return (f"Best RL 150 build: {r['affinity']} from {a_class(r['class'])} start, {r['ar']:.0f} AR "
            f"({CARD.rank_text(r['ar_rank'], 'highest')}) and {r['damage']:.0f} damage per hit "
            f"({CARD.rank_text(r['damage_rank'], 'highest')}), against every weapon's best RL 150 build "
            f"with its best infusion and class.")


def selftest():
    r = report('Lance', rls=(100, 150, 200))
    assert r['top'] and r['spans'], r
    for x in r['top']:
        b = x['rl150']
        if b and b['affinity'] in ('Fire', 'Flame Art', 'Lightning', 'Sacred', 'Magic', 'Cold'):
            assert not b['grease'], b
    rk = r['rank_rl150']
    assert rk and 1 <= rk['ar_rank'][0] <= rk['of'] and rk['of'] > 300 and rk['class'], rk
    # The ranking's build and the per-infusion build both pick the best class: same AR.
    mine = next((x['rl150'] for x in r['top'] if x['affinity'] == rk['affinity']), None)
    assert mine and abs(mine['ar'] - rk['ar']) < 1.0, (mine, rk)
    keen = scaling(AR.Tables(None), 'Lance', 'Keen')
    assert keen['dex'] > keen['str'], keen
    want = {'Heavy': 'physical', 'Occult': 'physical', 'Fire': 'elemental', 'Magic': 'elemental',
            'Cold': 'status', 'Blood': 'status', 'Poison': 'status'}
    got = {a: r['categories'][a] for a in want if a in r['categories']}
    assert got == {a: want[a] for a in got} and len(got) == len(want), got
    assert set(r['by_category']) == set(CATEGORIES), r['by_category']
    for kind, x in r['by_category'].items():
        assert r['categories'][x['affinity']] == kind, (kind, x['affinity'])
    print(f"selftest ok: {r['rank_text']}")
    for x in r['top']:
        print(f"  {x['affinity']}: {x['text']}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('weapon', nargs='?')
    ap.add_argument('--top', type=int, default=3)
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.weapon:
        ap.error('weapon required')
    r = report(a.weapon, a.top)
    if a.json:
        json.dump(r, sys.stdout, indent=1, default=str)
        return 0
    print(r['weapon'])
    print('  best by RL: ' + ', '.join(f"{s['affinity']} {s['from']}-{s['to']}" for s in r['spans']))
    for x in r['top']:
        print(f"  {x['affinity']:<10} {x['text']}")
    for kind, x in r['by_category'].items():
        print(f"  top {kind:<9} {x['affinity']:<10} {x['text']}")
    if r['rank_text']:
        print(f"  {r['rank_text']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
