#!/usr/bin/env python3
"""Sweep every unpacked <chr>.behbnd.dcx and report, per chr:
  - total declared behavior-graph event names
  - how many are FIREABLE (a transition consumes them and lands on a real state)
  - per name-prefix: numeric band, and how often the number in the name equals the
    animation id of the clip the target state actually plays.

Usage:
  scripts/er-behbnd-corpus-sweep.py <sharded-chr-root> <out.json> [--only c4500,c3200]
"""
import importlib.util, os, sys, glob, json, re, collections

_m = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'er-behbnd-attack-map.py')
_s = importlib.util.spec_from_file_location('bmap', _m)
g = importlib.util.module_from_spec(_s); _s.loader.exec_module(g)

NUMRE = re.compile(r'^([A-Za-z_]+?)(\d{3,6})$')


def sweep_dir(d):
    rows, evs = {}, []
    graphs = sorted(glob.glob(os.path.join(d, 'Behaviors', '*.hkx')))
    for hk in graphs:
        gr = g.Graph(hk)
        evs += gr.events
        rows.update(gr.event_to_anim())
    fire = {k: v[0] for k, v in rows.items()}
    pre = collections.defaultdict(list)
    for e in evs:
        mm = NUMRE.match(e)
        if mm:
            pre[mm.group(1)].append(int(mm.group(2)))
    idm = collections.defaultdict(lambda: [0, 0])
    for e, r in fire.items():
        mm = NUMRE.match(e)
        if not mm:
            continue
        p, n = mm.group(1), int(mm.group(2))
        idm[p][0] += 1
        if r['anims'] and n in r['anims']:
            idm[p][1] += 1
    return {'graphs': len(graphs), 'events': len(evs), 'fireable': len(fire),
            'prefix_all': {k: [min(v), max(v), len(v)] for k, v in pre.items()},
            'prefix_fire_idmatch': dict(idm)}


def _one(d):
    chrid = os.path.basename(d).split('-')[0]
    try:
        return chrid, sweep_dir(d)
    except Exception as e:
        return chrid, {'error': repr(e)[:140]}


if __name__ == '__main__':
    import multiprocessing as mp
    root, outp = sys.argv[1], sys.argv[2]
    only = None
    if '--only' in sys.argv:
        only = set(sys.argv[sys.argv.index('--only') + 1].split(','))
    dirs = sorted(glob.glob(root + '/*/*behbnd*') + glob.glob(root + '/*behbnd*'))
    if only:
        dirs = [d for d in dirs if os.path.basename(d).split('-')[0] in only]
    out = {}
    with mp.Pool(int(os.environ.get('SWEEP_JOBS', '10'))) as pool:
        for chrid, r in pool.imap_unordered(_one, dirs):
            out[chrid] = r
            print(chrid, r.get('fireable', r.get('error')), file=sys.stderr, flush=True)
    json.dump(out, open(outp, 'w'))
