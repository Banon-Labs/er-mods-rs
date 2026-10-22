#!/usr/bin/env python3
"""How flexible a weapon is across stat builds, measured against every melee armament.

    python3 scripts/er-mechanics-weapon-flex.py Shamshir > flex.json

For each archetype build at RL 150 (the STR 18 / DEX 80 Goldilocks build 1H and 2H, Quality
55/55, pure STR 80, INT 60, FTH 60, ARC 60) every melee weapon's best affinity at max upgrade is
found with `er-mechanics-ar.attack_rating`; a weapon whose requirement is unmet is dropped, as
`er-builds-optimize.weapons_for` does. Two scores per weapon: total AR, and one motion-value-100
hit on the optimizer's median RL 140-160 defender (`bracket_defender`), so split damage pays its
second defense. The STR 18 / DEX 80 builds are also scored with a Drawstring grease (+135).

The weapon is then placed among the curved swords (`wepType` 9), among a comparison set (every
curved sword plus the 25 melee weapons most often equipped in a right-hand slot of the planner
mirror), and among all melee weapons. Also emitted: each weapon's requirements and weight, the
target's scaling numbers and CalcCorrectGraph ids per affinity, and a flexibility index (mean and
minimum, over the six 1H archetypes, of the weapon's best damage as a share of the comparison
set's best).

Scaling letters use the community thresholds S 175, A 140, B 90, C 60, D 25, E above 0
(`COMMUNITY`, not read from the executable). JSON goes to stdout, progress to stderr.
"""
import collections
import importlib.util
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import er_corpus  # noqa: E402  the corpus floor every mirror read goes through
_s = importlib.util.spec_from_file_location('er_builds_optimize', os.path.join(HERE, 'er-builds-optimize.py'))
OPT = importlib.util.module_from_spec(_s)
sys.modules['er_builds_optimize'] = OPT
_s.loader.exec_module(OPT)
AR, DEF = OPT.AR, OPT.DEF

# Not wielded as a melee armament: unarmed, bows, crossbows, ballistae, staves, seals, shields,
# ammunition, torches, perfume bottles.
NON_MELEE = {33, 50, 51, 53, 55, 56, 57, 61, 65, 67, 69, 81, 83, 85, 86, 87, 89}
CURVED_SWORD = 9
ELEM = ('physical', 'magic', 'fire', 'lightning', 'holy')
BASE = {'str': 16, 'dex': 16, 'int': 9, 'fth': 9, 'arc': 9}
USER = {'str': 18, 'dex': 80, 'int': 7, 'fth': 8, 'arc': 9}   # build 188e74da444029
BUILDS = {
    'STR18/DEX80 2H': (USER, True),
    'STR18/DEX80 1H': (USER, False),
    'Quality 55/55 1H': ({**BASE, 'str': 55, 'dex': 55}, False),
    'STR80 1H': ({**BASE, 'str': 80}, False),
    'INT60 1H': ({**BASE, 'int': 60}, False),
    'FTH60 1H': ({**BASE, 'fth': 60}, False),
    'ARC60 1H': ({**BASE, 'arc': 60}, False),
}
FLEX_ARCH = ['STR18/DEX80 1H', 'Quality 55/55 1H', 'STR80 1H', 'INT60 1H', 'FTH60 1H', 'ARC60 1H']


def letter(v):
    return ('S' if v >= 175 else 'A' if v >= 140 else 'B' if v >= 90 else 'C' if v >= 60
            else 'D' if v >= 25 else 'E' if v > 0 else '-')


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else 'Shamshir'
    t = AR.Tables(None)
    cat = set(json.loads((OPT.CACHE / 'catalog.json').read_text())['armament'].values())
    melee = {b: t.names[b] for b, w in t.weapons.items()
             if not b % 10000 and b in cat and w['wepType'] not in NON_MELEE and t.names.get(b)}
    curved = {b for b in melee if t.weapons[b]['wepType'] == CURVED_SWORD}
    sham = t.find_weapon(target)

    use, nbuilds = collections.Counter(), 0
    for row in er_corpus.iter_rows(OPT.CACHE / 'builds.jsonl'):
        b = row['build']
        nbuilds += 1
        use.update({sl['name'] for sl in (b.get('inventory') or {}).get('slots', [])
                    if any(e in (0, 1, 2) for e in (sl.get('equipSet') or []))})
    name2id = {n: b for b, n in melee.items()}
    popular = [(n, c) for n, c in use.most_common() if n in name2id][:25]
    cset = curved | {name2id[n] for n, _ in popular}
    dfn = OPT.bracket_defender(OPT.corpus_rows(OPT.CACHE / 'builds.jsonl', 140, 160))

    def best(bid, stats, two, greased=False):
        rows = []
        for aff in OPT.affinities(t, bid):
            wid = bid + AR.AFFINITIES.index(aff) * 100
            lvl = t.max_level(t.weapons[wid]['reinforceTypeId'])
            r = AR.attack_rating(t, bid, aff, lvl, stats, two)
            if any(v.get('scaling', 0.0) < 0 for v in r['damage'].values()):
                continue
            opts = [None]
            if greased and t.weapons[wid].get('isEnhance'):
                opts = [(el, OPT.GREASES['dlc-drawstring']) for el in OPT.GREASE_ELEMENTS]
            for g in opts:
                by = {el: r['damage'].get(el, {}).get('total', 0.0) for el in ELEM}
                if g:
                    by[g[0]] += g[1]
                rows.append({'aff': aff, 'ar': round(sum(by.values()), 1),
                             'dmg': round(DEF.damage(by, 100.0, dfn)['total'], 1),
                             'bleed': round(r['status'].get('bleed', {}).get('total', 0.0), 1),
                             'grease': g[0] if g else None})
        if not rows:
            return None
        return {'ar': max(rows, key=lambda r: r['ar']), 'dmg': max(rows, key=lambda r: r['dmg']), 'affs': rows}

    res = {'target': target, 'n_melee': len(melee), 'n_curved': len(curved), 'nbuilds_mirror': nbuilds,
           'popular': popular, 'set': sorted(t.names[b] for b in cset), 'defender': dfn, 'builds': {}}
    cache = {}
    for label, (stats, two) in BUILDS.items():
        for greased in ((False, True) if label.startswith('STR18/DEX80') else (False,)):
            key = label + (' +grease135' if greased else '')
            per = {b: r for b in melee if (r := best(b, stats, two, greased))}
            cache[key] = per
            out = {'stats': stats, 'two_handed': two}
            for metric in ('ar', 'dmg'):
                m = {'target': per[sham][metric] if sham in per else None}
                pools = {'curved': [b for b in curved if b in per], 'set': [b for b in cset if b in per],
                         'all_melee': list(per)}
                for pn, pool in pools.items():
                    ranked = sorted(pool, key=lambda b: per[b][metric][metric], reverse=True)
                    top = per[ranked[0]][metric][metric]
                    m[pn] = {'n': len(pool),
                             'rank': ranked.index(sham) + 1 if sham in per else None,
                             'pct_of_best': round(100 * per[sham][metric][metric] / top, 1) if sham in per else None,
                             'top5': [(t.names[b], per[b][metric]['aff'], per[b][metric][metric]) for b in ranked[:5]]}
                out[metric] = m
            if label.startswith('ARC'):
                bl = sorted(((max(a['bleed'] for a in per[b]['affs']), t.names[b]) for b in cset if b in per),
                            reverse=True)
                out['bleed_set_top5'] = bl[:5]
                out['bleed_target_rank_set'] = [n for _, n in bl].index(target) + 1
            out['target_affs'] = per[sham]['affs'] if sham in per else None
            res['builds'][key] = out
            print(key, 'done', file=sys.stderr)

    flex = []
    for b in melee:
        ratios = []
        for label in FLEX_ARCH:
            per = cache[label]
            top = max(per[x]['dmg']['dmg'] for x in cset if x in per)
            ratios.append(per[b]['dmg']['dmg'] / top if b in per else 0.0)
        flex.append({'name': t.names[b], 'curved': b in curved, 'in_set': b in cset,
                     'mean_pct': round(100 * statistics.mean(ratios), 1), 'min_pct': round(100 * min(ratios), 1),
                     'per_arch': [round(100 * x, 1) for x in ratios]})
    flex.sort(key=lambda r: -r['mean_pct'])
    res['flex_arch'] = FLEX_ARCH
    res['flex'] = flex

    res['req'] = [{'name': t.names[b], 'curved': b in curved, 'in_set': b in cset,
                   'req': {k: t.weapons[b][AR.STAT_FIELDS[k][3]] for k in BASE},
                   'weight': t.weapons[b]['weight'], 'affinities': len(OPT.affinities(t, b))} for b in melee]

    sc = {}
    for aff in OPT.affinities(t, sham):
        wid = sham + AR.AFFINITIES.index(aff) * 100
        w = t.weapons[wid]
        rf = t.reinforce[w['reinforceTypeId'] + t.max_level(w['reinforceTypeId'])]
        sc[aff] = {'row': wid, 'aecp': w['attackElementCorrectId'],
                   'base': {e[0]: round(w[e[2]] * rf[e[3]], 1) for e in AR.ELEMENTS if w[e[2]]},
                   'graph': {e[0]: w[e[4]] for e in AR.ELEMENTS if w[e[2]]},
                   'bleed_graph': w.get('correctType_Blood'),
                   'scaling': {k: f'{letter(v)} {v:.1f}' for k in BASE
                               if (v := w[AR.STAT_FIELDS[k][1]] * rf[AR.STAT_FIELDS[k][2]]) > 0}}
    res['target_scaling'] = sc
    json.dump(res, sys.stdout, indent=1, default=str)


if __name__ == '__main__':
    main()
