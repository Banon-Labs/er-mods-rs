#!/usr/bin/env python3
"""The outcome form against `net_parts`, one core, for a few named skills: what a change to the
punish model must keep equal (`er-mechanics-attack-similarity.OutcomeForm` and `FollowupForm` are
the run's fast forms of `er-mechanics-spells.net_parts`; check 29 of the attack-similarity selftest
compares them on four entities, this on any).

    python3 scripts/er-mechanics-form-check.py [--scripts DIR] [--every 8] [--dump out.json] name...

Per entity of each named skill (its default cast, releases and follow-ups, `skill_job`): its bursts
(first and last live frame of each geometry, whether it is flown again after a dodge), the largest
`form_error` over every `--every`th situation of the distance basis, the catches played in a dodge
(`OutcomeForm.plays`), each side's seconds and the weighted mean net. A follow-up is read through
`FollowupForm` (its net and its fixed sequence). `--dump` writes every entity's per-situation nets,
so two checkouts' runs can be set side by side (`--compare a.json b.json`). The context is built
without attacker builds (`Context(builds=[])`): neither form reads them. A model check, not a run:
it never writes the similarity cache."""
import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

import numpy as np


def load(scripts):
    scripts = Path(scripts).resolve()
    sys.path.insert(0, str(scripts))
    spec = importlib.util.spec_from_file_location('er_mechanics_attack_similarity',
                                                  scripts / 'er-mechanics-attack-similarity.py')
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


def compare(a, b):
    A, B = json.loads(Path(a).read_text()), json.loads(Path(b).read_text())
    for k in sorted(set(A) | set(B)):
        x, y = A.get(k), B.get(k)
        if x is None or y is None:
            print(f'{k}: only in {"b" if x is None else "a"}')
            continue
        w = np.asarray(x['w'], float)
        nx, ny = np.asarray(x['net'], float), np.asarray(y['net'], float)
        d = ny - nx
        i = int(np.argmax(np.abs(d)))
        print(f"{k}: mean {w @ nx / w.sum():.2f} -> {w @ ny / w.sum():.2f}; moved in {int((np.abs(d) > 1e-6).sum())} "
              f"of {len(d)} situations, largest at {x['sit'][i]}: {nx[i]:.2f} -> {ny[i]:.2f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('names', nargs='*')
    ap.add_argument('--scripts', default=str(Path(__file__).resolve().parent))
    ap.add_argument('--every', type=int, default=8)
    ap.add_argument('--dump')
    ap.add_argument('--compare', nargs=2)
    ap.add_argument('--no-form', action='store_true', help='net_parts alone (a checkout without the forms)')
    ap.add_argument('--families', type=lambda s: [float(x) for x in s.split(',')], metavar='D1,D2,..',
                    help="also print each move family's sequence_outcome at these distances (floor 0)")
    ap.add_argument('--games', type=lambda s: [float(x) for x in s.split(',')], metavar='D1,D2,..',
                    help="for a follow-up, print the mixup's rows (FollowupModel.mixup's matrix) at these "
                         "distances, the first situation key of each")
    a = ap.parse_args()
    if a.compare:
        compare(*a.compare)
        return 0
    M = load(a.scripts)
    S, A = M.S, None
    t0 = time.time()
    c = M.Context(dict(M.DEFAULT_STATS_DICT) if hasattr(M, 'DEFAULT_STATS_DICT') else
                  {k: int(v) for k, v in (p.split('=') for p in M.DEFAULT_STATS.split(','))}, builds=[])
    A = c.A
    pairs = c.pairs[::a.every]
    sprint_pairs = c.sprint_pairs[::a.every]
    w = c.w[::a.every]
    print(f'context {time.time() - t0:.0f}s, {len(pairs)} of {len(c.pairs)} situations', flush=True)
    jobs, _ = M.skill_candidates(c)
    dump = {}
    for name in a.names:
        job = next((j for j in jobs if j['name'] == name), None)
        mid = c.SM.by_name.get(name.lower()) if name.startswith('spell:') is False else None
        if name.startswith('spell:'):
            mid = c.SM.by_name.get(name[6:].lower())
            job = None
        if job is None and mid is None:
            print(f'{name}: no such skill job or spell')
            continue
        for r in (M.skill_job(c, job) if job is not None else M.spell_job(c, mid)):
            if r.get('status') not in ('scored', 'partial'):
                continue
            x = M.profile_inputs(r)
            bursts = M._geo_bursts(x['geo'])
            shape = '; '.join(','.join(f"{float(g['T'].min()):.0f}-{float(g['T'].max()):.0f}{'h' if 'HOMING' in g else ''}"
                                       for g in gs) for gs, _ in bursts)
            sub = {**c.__dict__}
            if x.get('press') is not None:
                t1 = time.time()
                cc = type('C', (), {})()
                cc.__dict__.update(sub)
                cc.pairs, cc.sprint_pairs, cc.w, cc.W = pairs, sprint_pairs, w, float(w.sum())
                ff = M.FollowupForm(cc, x)
                if a.games:
                    seen, orig = set(), ff.model.mixup

                    def spy(dist, floor, *args, **kw):
                        g = orig(dist, floor, *args, **kw)
                        d_ = min(a.games, key=lambda q: abs(q - dist))
                        if abs(d_ - dist) < 0.26 and d_ not in seen:
                            seen.add(d_)
                            print(f'   game at {dist:g} m floor {floor:g}: value {g["value"]:.1f}, cols {g["cols"]}')
                            for r, row in zip(g['rows'], g['M']):
                                print(f"     {r}: {' '.join(f'{v:.0f}' for v in row)}")
                        return g
                    ff.model.mixup = spy
                ps = ff.parts(x['value'], x['value_one'], x['geo'])
                net = np.array([p['net'] for p in ps])
                fixed = np.array([p['fixed'] for p in ps])
                print(f"{name} {r['key']} [{shape}] follow-up net mean {w @ net / w.sum():.2f} fixed mean "
                      f"{w @ fixed / w.sum():.2f} {time.time() - t1:.1f}s", flush=True)
                dump[r['key']] = {'net': net.tolist(), 'w': w.tolist(), 'sit': [list(p) for p in pairs]}
                continue
            if a.families:
                cpoise = c.dfn.get('pool') or c.dfn.get('poise_pool') or c.dfn.get('poise')
                bursts_v = S._bursts(x['geo'], x['value'])
                cm = A.CaughtModel.of(x['geo'], cpoise, x['value'])
                fams = A.dodge_families(any(g.get('AIR') for g, _ in x['geo']))
                for dist in a.families:
                    for fam in fams:
                        o = A.sequence_outcome(bursts_v, dist, floor=0.0, my_roll=x['roll'], strikes=c.strikes,
                                               run=True, caught=cm, hp=c.dfn.get('hp_pool') or c.dfn.get('hp'),
                                               families=[fam])
                        print(f"   {dist:g} m {fam[0]['anim']}: value {o['value']:.1f} base {o['base']:.1f} "
                              f"evade {o['p_evade']:.3f} punish {o['p_punish']:.3f} "
                              f"net {o['value'] - o['punish_hp']:.1f}{' (stands)' if o['moves'] is None else ''}")
            t1 = time.time()
            parts = S.net_parts(A, x['geo'], x['value'], sprint_pairs if x.get('sprint') else pairs, x['roll'],
                                c.strikes, x['value_one'], defender=c.dfn)
            want = np.array([p['net'] for p in parts])
            tw = time.time() - t1
            line = f"{name} {r['key']} [{shape}] mean {w @ want / w.sum():.2f} net_parts {tw:.1f}s"
            if not a.no_form:
                t1 = time.time()
                cc = type('C', (), {})()
                cc.__dict__.update(sub)
                cc.pairs, cc.sprint_pairs = pairs, sprint_pairs
                form = M.OutcomeForm(cc, x['geo'], x['roll'], sprint=bool(x.get('sprint')))
                got = form.net(x['value'], x['geo'])
                tf = time.time() - t1
                line += (f" form {tf:.1f}s form_err {M.form_error(got, want)} max|d| {float(np.abs(got - want).max()):.2e}"
                         f" plays {len(form.plays)}")
            print(line, flush=True)
            dump[r['key']] = {'net': want.tolist(), 'w': w.tolist(), 'sit': [list(p) for p in pairs]}
    if a.dump:
        Path(a.dump).write_text(json.dumps(dump))
    return 0


if __name__ == '__main__':
    sys.exit(main())
