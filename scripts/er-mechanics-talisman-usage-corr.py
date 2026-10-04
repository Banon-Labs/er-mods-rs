#!/usr/bin/env python3
"""How well each mechanics feature of a loadout predicts that players wear a talisman with it.

    python3 scripts/er-mechanics-talisman-affinity.py --json --out matrix.json
    python3 scripts/er-mechanics-talisman-usage-corr.py matrix.json "Millicent's Prosthesis" "Twinblade Talisman"
    python3 scripts/er-mechanics-talisman-usage-corr.py matrix.json --all      # every talisman, best metric
    python3 scripts/er-mechanics-talisman-usage-corr.py --selftest

The unit is the loadout a build fights with (`er-builds-embed.loadout`), not every armament it
carries: a build is counted once, for the right primary and the grip it is held in.

| loadout | grip measured |
| --- | --- |
| `is2h` set | the weapon two-handed (`both`) |
| the same armament in the right and left primary slots | powerstance (`dual`), when the weapon has a powerstance moveset |
| a right primary that is powerstanced by itself (`two_hand_is_pair`: `isDualBlade` and `bothHandEquipable` -- fists except Grafted Dragon, claws, hand-to-hand arts, perfume bottles, backhand blades, paired greatswords), any other left | two-handed (`both`): the left item rides on the back for its passive |
| two different armaments `can_powerstance` accepts (same powerstance category, or katana + wakizashi) | the pair's powerstance moveset (`pair`): the right weapon's clips, each hand's hits from its own weapon |
| anything else in the left hand (shield, seal, staff, a non-pairing weapon, empty) | the right primary one-handed (`one`) |
| no right primary | not measured: the left hand's own attacks are not in the matrix |

For each talisman metric (a matrix column with the grip taken out), every loadout reads the value
of its own grip, and Spearman's rho is taken against the share of that loadout's builds wearing
the talisman, over loadouts with at least `--min-builds` builds. The talisman's corpus base rate
and the number of builds the mapping dropped are printed with it.
"""
import argparse
import collections
import importlib.util
import json
import os
import sys
import unicodedata

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
_s = importlib.util.spec_from_file_location('er_builds_embed', os.path.join(HERE, 'er-builds-embed.py'))
E = importlib.util.module_from_spec(_s)
_s.loader.exec_module(E)

GRIPS = ('one', 'both', 'dual')


def rank(v):
    order = np.argsort(v, kind='mergesort')
    r = np.empty(len(v))
    i = 0
    while i < len(v):
        j = i
        while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
            j += 1
        r[order[i:j + 1]] = (i + j) / 2
        i = j + 1
    return r


def spearman(x, y):
    rx, ry = rank(np.asarray(x, float)), rank(np.asarray(y, float))
    if len(x) < 3 or rx.std() == 0 or ry.std() == 0:
        return float('nan')
    return float(np.corrcoef(rx, ry)[0, 1])


class Matrix:
    def __init__(self, path):
        m = json.load(open(path))
        self.columns = m['columns']
        names = {w['id']: w['name'] for w in m['weapons']}
        self.row = {names[w]: dict(zip(self.columns, r))
                    for w, r in zip(m['matrix']['weapons'], m['matrix']['rows'])}
        self.metrics = collections.defaultdict(dict)   # talisman -> metric -> {grip: column}
        for c in self.columns:
            parts = c.split('|')
            g = next((p for p in parts if p in GRIPS), None)
            t = parts[0]
            metric = '|'.join(p for p in parts[1:] if p != g)
            self.metrics[t].setdefault(metric, {})[g] = c

        self.grips = {w['name']: set(w.get('grips', ())) for w in m['weapons']}
        self.paired_2h = {w['name'] for w in m['weapons'] if w.get('two_hand_is_pair')}
        self.features = {w['name']: w['features'] for w in m['weapons']}
        # `can_powerstance` reads these two fields off `reg.weapon[id]`; a name-keyed stand-in.
        self.weapon = {w['name']: {'wepmotionCategory': w.get('wepmotionCategory'),
                                   'spAtkcategory': w.get('spAtkcategory')} for w in m['weapons']}

    def has_dual(self, name):
        return 'dual' in self.grips.get(name, ())

    def can_pair(self, right, left):
        """`IsEnableDualWielding` (`er-mechanics-powerstance-guard.can_powerstance`): the two
        armaments share a powerstance category, or are a katana with a wakizashi."""
        if right not in self.weapon or left not in self.weapon:
            return False
        return PSG.can_powerstance(self, right, left)

    def value(self, w, g, talisman, metric):
        """The metric for weapon `w` held in grip `g`. A mixed pair (`w` = (right, left)) plays
        the right weapon's powerstance clips (both share its category), and each hand's hits are
        that hand's own weapon's AtkParam rows: the successive counter takes the right weapon's
        right-hand gain plus the left weapon's left-hand gain over the right weapon's string
        period. Other metrics of a mixed pair read the right weapon's `dual` value."""
        if g != 'pair':
            col = self.metrics[talisman][metric].get(g, self.metrics[talisman][metric].get(None))
            return None if col is None else self.row[w].get(col)
        r, l = w
        if metric.startswith('successive|') and metric.split('|')[1] in ('r1_string_gain', 'gain_per_s'):
            fr = self.features[r][talisman]['successive'].get('dual') or {}
            fl = self.features[l][talisman]['successive'].get('dual') or {}
            if 'right_string_gain' not in fr or 'left_string_gain' not in fl:
                return None
            gain = fr['right_string_gain'] + fl['left_string_gain']
            if metric.endswith('r1_string_gain'):
                return gain
            period = fr.get('r1_period_frames')
            return round(gain / (period / 30), 3) if period else None
        if metric.endswith('first_threshold_s_max'):
            return None  # cold start is simulated per weapon; not for a mixed pair
        return self.value(r, 'dual', talisman, metric)


_p = importlib.util.spec_from_file_location('er_mechanics_powerstance_guard',
                                            os.path.join(HERE, 'er-mechanics-powerstance-guard.py'))
PSG = importlib.util.module_from_spec(_p)
_p.loader.exec_module(PSG)


def fold(name):
    """The planner writes `Miséricorde` and `Great Épée`; the regulation names are unaccented."""
    return name and unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode()


def loadout_key(lo, M):
    """(weapon, grip), or (None, reason) when the loadout is not measured. A mixed powerstance
    pair is ((right, left), 'pair')."""
    r, l = fold(lo['right']), fold(lo['left'])
    if lo['two_handed']:
        w = r or l
        return (w, 'both') if w in M.row else (None, 'weapon not in matrix')
    if not r:
        return None, 'no right primary'
    if r not in M.row:
        return None, 'weapon not in matrix'
    if l == r and M.has_dual(r):
        return r, 'dual'
    if l and l != r and M.has_dual(r) and M.has_dual(l) and M.can_pair(r, l):
        return (r, l), 'pair'
    if r in M.paired_2h:
        return r, 'both'
    return r, 'one'


def groups(corpus, M):
    by = collections.defaultdict(list)
    dropped = collections.Counter()
    for b in corpus:
        w, g = loadout_key(b['loadout'], M)
        if w is None:
            dropped[g] += 1
        else:
            by[(w, g)].append(b)
    return by, dropped


def correlate(M, by, talisman, metric, min_builds):
    xs, ys, pts = [], [], []
    for (w, g), bs in by.items():
        if len(bs) < min_builds:
            continue
        v = M.value(w, g, talisman, metric)
        if v is None:
            continue
        share = sum(f't:{talisman}' in b['tokens'] for b in bs) / len(bs)
        xs.append(float(v))
        ys.append(share)
        pts.append((w, g, len(bs), share, v))
    return spearman(xs, ys), pts


def report(M, corpus, by, dropped, talisman, min_builds, top):
    if talisman not in M.metrics:
        print(f'{talisman}: no mechanics column')
        return
    base = sum(f't:{talisman}' in b['tokens'] for b in corpus) / len(corpus)
    res = []
    for metric in M.metrics[talisman]:
        rho, pts = correlate(M, by, talisman, metric, min_builds)
        res.append((rho, metric, pts))
    res.sort(key=lambda t: -abs(t[0]) if t[0] == t[0] else 0)
    n = len(res[0][2]) if res else 0
    print(f'{talisman}: base rate {base:.1%}; {n} loadouts with {min_builds}+ builds; '
          f'dropped builds: {dict(dropped)}')
    for rho, metric, pts in res:
        print(f'  rho {rho:+.3f}  {metric}  ({len(pts)} loadouts)')
    rho, metric, pts = res[0]
    print(f'  most worn with (share, builds, {metric}):')
    for w, g, nb, share, v in sorted(pts, key=lambda p: -p[3])[:top]:
        name = ' + '.join(w) if isinstance(w, tuple) else w
        print(f'    {share:6.1%} {nb:5d} {v!s:>8}  {name} [{g}]')


def rank_successive(M, talisman, top, grips=GRIPS):
    """Every weapon in every grip it has, scored from the game data alone for a successive-hit
    talisman: counter gain per landed hit record (2x when both powerstance hands land), attack
    speed (hit records per second of the R1 / L1 string), their product (gain per second), that
    minus the host decay, and the cold-start time to the first threshold. No build is consulted.
    Sorted by gain per second; a weapon whose string never reaches the threshold sorts last."""
    rows = []
    for w, feats in M.features.items():
        f = (feats.get(talisman) or {}).get('successive')
        if not f:
            continue
        for g in grips:
            fg = f.get(g)
            if not fg or not fg.get('gain_per_s'):
                continue
            if g == 'both' and w in M.paired_2h:
                label = 'two-handed (powerstanced by itself)'
            else:
                label = {'one': 'one-handed', 'both': 'two-handed', 'dual': 'powerstance (x2)'}[g]
            cs = fg.get('cold_start_first_threshold') or {}
            first = cs['seconds'][1] if cs.get('reached') == 'always' else None
            rec = fg['r1_string_records'] or 1
            rows.append((fg['gain_per_s'], fg.get('net_per_s_upper'), fg.get('hits_per_s'),
                         round(fg['r1_string_gain'] / rec, 2), first, w, label))
    rows.sort(key=lambda r: -r[0])
    print(f'{talisman}: threshold {f["thresholds"][0]}, decay '
          f'{sum(x["per_s"] for x in f["host_decay"]):g}/s; ceiling (every hit record lands)')
    print(f'  {"gain/s":>7} {"net/s":>7} {"hits/s":>7} {"gain/hit":>8} {"to 1st":>6}  loadout')
    for gps, net, hps, gph, first, w, label in rows[:top]:
        print(f'  {gps:7.2f} {net:7.2f} {hps:7.2f} {gph:8.2f} {first if first is not None else "never":>6}  '
              f'{w} [{label}]')
    return rows


def selftest():
    fails = 0

    def check(name, got, want):
        nonlocal fails
        if got != want:
            fails += 1
            print(f'FAIL {name}: got {got!r}, want {want!r}')

    check('spearman monotone', round(spearman([1, 2, 3, 4], [10, 20, 30, 40]), 6), 1.0)
    check('spearman reversed', round(spearman([1, 2, 3, 4], [4, 3, 2, 1]), 6), -1.0)
    check('rank ties', list(rank(np.array([5, 1, 5]))), [1.5, 0.0, 1.5])

    class FakeM:
        row = {'A': {}, 'B': {}, 'C': {}, 'F': {}}
        paired_2h = {'F'}

        @staticmethod
        def has_dual(n):
            return n in ('A', 'B', 'D')

        @staticmethod
        def can_pair(r, l):
            return {r, l} == {'A', 'B'}
    lo = dict(right='A', left='A', two_handed=False)
    check('same pair is powerstance', loadout_key(lo, FakeM), ('A', 'dual'))
    check('two-handed', loadout_key(dict(lo, two_handed=True), FakeM), ('A', 'both'))
    check('mixed pair', loadout_key(dict(lo, left='B'), FakeM), (('A', 'B'), 'pair'))
    check('two dual-capable weapons that cannot pair', loadout_key(dict(lo, left='D'), FakeM), ('A', 'one'))
    # The real pairing rule on regulation-shaped rows: two straight swords pair, a katana pairs
    # with a wakizashi, a straight sword does not pair with a katana.
    rows = {'ss1': {'wepmotionCategory': 23, 'spAtkcategory': 0},
            'ss2': {'wepmotionCategory': 23, 'spAtkcategory': 0},
            'kat': {'wepmotionCategory': 29, 'spAtkcategory': 0},
            'wak': {'wepmotionCategory': 20, 'spAtkcategory': PSG.SPECIAL_CATEGORY_WAKIZASHI}}

    class Reg:
        weapon = rows
    check('accents fold', fold('Miséricorde'), 'Misericorde')
    check('two straight swords pair',PSG.can_powerstance(Reg, 'ss1', 'ss2'), True)
    check('katana + wakizashi pair', PSG.can_powerstance(Reg, 'kat', 'wak'), True)
    check('straight sword + katana do not pair', PSG.can_powerstance(Reg, 'ss1', 'kat'), False)
    check('right with a non-pairing left', loadout_key(dict(lo, left='C'), FakeM), ('A', 'one'))
    check('right with empty left', loadout_key(dict(lo, left=None), FakeM), ('A', 'one'))
    check('no right', loadout_key(dict(lo, right=None), FakeM), (None, 'no right primary'))
    check('fist with a shield is two-handed', loadout_key(dict(lo, right='F', left='C'), FakeM), ('F', 'both'))
    b = {'inventory': {'slots': [
        {'name': 'X', 'equipSet': [0], 'equipIndex': 0},
        {'name': 'S', 'equipSet': [1], 'equipIndex': 1},
        {'name': 'X', 'equipSet': [3], 'equipIndex': 3}]},
        'sets': {'weapons': [{'name': 'Default', 'active': True}]}}
    lo = E.loadout(b)
    check('loadout right/left', (lo['right'], lo['left'], lo['others']), ('X', 'X', ['S']))
    check('loadout token', E.loadout_token(lo), 'ld:X + X')
    print('selftest', 'failed' if fails else 'passed')
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('matrix', nargs='?', help='JSON from er-mechanics-talisman-affinity.py --json --out')
    ap.add_argument('talisman', nargs='*')
    ap.add_argument('--all', action='store_true', help='best metric per talisman, sorted by |rho|')
    ap.add_argument('--rank', action='store_true',
                    help='successive-hit talismans: score every weapon x grip from game data, no builds')
    ap.add_argument('--min-builds', type=int, default=15)
    ap.add_argument('--top', type=int, default=10)
    ap.add_argument('--mirror', type=E.Path, default=E.CACHE / 'builds.jsonl')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.matrix:
        ap.error('matrix is required')
    M = Matrix(a.matrix)
    if a.rank:
        for t in a.talisman:
            rank_successive(M, t, a.top)
        return 0
    corpus, _ = E.load_corpus(a.mirror, 125, 169)
    by, dropped = groups(corpus, M)
    if a.all:
        best = []
        for t in sorted(M.metrics):
            for metric in M.metrics[t]:
                rho, pts = correlate(M, by, t, metric, a.min_builds)
                if rho == rho:
                    best.append((rho, t, metric, len(pts)))
        best.sort(key=lambda r: -abs(r[0]))
        for rho, t, metric, n in best[:a.top * 4]:
            print(f'rho {rho:+.3f}  {t} | {metric}  ({n} loadouts)')
        return 0
    for t in a.talisman:
        report(M, corpus, by, dropped, t, a.min_builds, a.top)
    return 0


if __name__ == '__main__':
    sys.exit(main())
