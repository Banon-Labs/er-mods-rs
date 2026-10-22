#!/usr/bin/env python3
"""Pass and group tallies of the dumped vertex shaders by debug name, under the design's group rule.

The design (section 7.5) defines a group key as the vertex shader's debug-name stem with the pass token and the
`@[...]` suffix removed. This applies that rule literally, with the pass token taken as the last
underscore token from a fixed pass list wherever it sits in the stem (a `_Skin` or `_FT` suffix
can follow it), and prints the counts the design cites so they can be compared line by line.
"""
import argparse
import collections
import json
import os
import sys

PASSES = ['Gbuf', 'GBuf', 'GBufDpt', 'DptA', 'Dpt', 'Velo', 'Fwd', 'FwdSdw', 'FwdDpt', 'FwdSsr',
          'Pnt', 'PntCol', 'FurDir', 'Spec']


def split(name):
    stem = name.split('@')[0]
    if stem.endswith('.vpo.pdb'):
        stem = stem[:-len('.vpo.pdb')]
    toks = stem.split('_')
    for i in range(len(toks) - 1, 0, -1):
        if toks[i] in PASSES:
            return toks[i], '_'.join(toks[:i] + toks[i + 1:])
    return '', stem


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--names', required=True)
    ap.add_argument('--sweep', required=True)
    a = ap.parse_args()
    names = json.load(open(a.names))
    rows = [json.loads(line) for line in open(os.path.join(a.sweep, 'results.jsonl'))]
    wob = {r['hash']: r for r in rows if r['carrier'] == 'wobble'}
    per = collections.Counter()
    app = collections.Counter()
    groups = collections.defaultdict(set)
    other = []
    for h, n in names.items():
        p, g = split(n)
        per[p] += 1
        if wob[h]['status'] == 'applied':
            app[p] += 1
        if p:
            groups[g].add(p)
        else:
            other.append((n, wob[h]['status'], wob[h].get('refusal_class')))
    print('pass vs applied', sorted(((k or 'other', per[k], app[k]) for k in per), key=lambda x: -x[1]))
    mat = {g: s for g, s in groups.items()}
    gb = [g for g, s in mat.items() if s & {'Gbuf', 'GBuf'}]
    nod = [g for g in gb if not (mat[g] & {'Dpt', 'DptA'})]
    nov = [g for g in gb if 'Velo' not in mat[g]]
    fwd_only = [g for g, s in mat.items() if not (s & {'Gbuf', 'GBuf'})]
    print('material groups', len(mat), 'with gbuf', len(gb), 'gbuf without own depth', len(nod),
          'gbuf without velo', len(nov), 'groups without gbuf', len(fwd_only))
    print('all groups without velo', sum(1 for s in mat.values() if 'Velo' not in s))
    applied_other = [o for o in other if o[1] == 'applied']
    print('other', len(other), 'applied', len(applied_other))
    for o in sorted(applied_other):
        print('  applied other:', o[0])
    return 0


if __name__ == '__main__':
    sys.exit(main())
