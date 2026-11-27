#!/usr/bin/env python3
"""Dump what `er-mechanics-attack-similarity.compute` produces for a few names, single process, so
two checkouts' code can be compared bit for bit (`--compare`).

    python3 scripts/perf-similarity-dump.py --scripts <dir with the module> --out a.npz name...
    python3 scripts/perf-similarity-dump.py --compare a.npz b.npz

The dump holds every profile array of every record and component, every attacker build's nets and
records, and the records' JSON (sorted keys), plus each attack item's wall and CPU seconds. With
`--profile out.prof` the compute call runs under cProfile. A perf helper, not a model script."""
import argparse
import cProfile
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


def count_reads(A):
    """Wrap the model's memoised reads (`_homing_entries`'s flights, `_catches_read`) to count each
    key asked, each read made and each read made again for a key read before (a memo emptied in
    between: `forget_reads` per set, or a cap), with the seconds each spent. Moves no number."""
    import numpy as np
    n = {'homing_calls': 0, 'homing_flown': 0, 'homing_reflown': 0, 'homing_s': 0.0, 'homing_reflown_s': 0.0,
         'catches_read': 0, 'catches_reread': 0, 'catches_s': 0.0, 'catches_reread_s': 0.0,
         'forget_reads': 0, 'homing_cap_clears': 0}
    ever_h, ever_c = set(), set()
    he, cr, fr = A._homing_entries, A._catches_read, A.forget_reads

    def homing(part, reg, dist, moves, air, starts):
        n['homing_calls'] += 1
        sig = A._part_sig({**part, 'index': None})
        keys = [(sig, round(float(dist), 4), A._move_key(mv), air, starts.tobytes()) for mv in moves]
        if all(k in A._HOMING_TOUCH for k in keys):
            return he(part, reg, dist, moves, air, starts)
        before = len(A._HOMING_TOUCH)
        again = any(k in ever_h for k in keys)
        t = time.perf_counter()
        got = he(part, reg, dist, moves, air, starts)
        dt = time.perf_counter() - t
        if len(A._HOMING_TOUCH) < before:
            n['homing_cap_clears'] += 1
        n['homing_flown'] += 1
        n['homing_s'] += dt
        if again:
            n['homing_reflown'] += 1
            n['homing_reflown_s'] += dt
        ever_h.update(keys)
        return got

    def catches_read(geoms, dist, move, starts, radius):
        digests = [b'' if g is None else A._geo_reads(g)[1] for g in geoms]
        key = None if any(d is None for d in digests) else (
            tuple(digests), float(dist), A._move_key(move), starts.shape, starts.tobytes(), radius)
        t = time.perf_counter()
        got = cr(geoms, dist, move, starts, radius)
        dt = time.perf_counter() - t
        n['catches_read'] += 1
        n['catches_s'] += dt
        if key is not None:
            if key in ever_c:
                n['catches_reread'] += 1
                n['catches_reread_s'] += dt
            ever_c.add(key)
        return got

    def forget():
        n['forget_reads'] += 1
        # A new set: what an earlier set read is not a read made again.
        ever_h.clear()
        ever_c.clear()
        return fr()
    A._homing_entries, A._catches_read, A.forget_reads = homing, catches_read, forget
    del np
    return n


def count_forms(m):
    """Wrap the phase-2 forms to time, per attack set (`_profile_batch`), what every line of a key
    shares and what each line does on its own: each `OutcomeForm` / `FollowupForm` made (one per
    geometry fingerprint, `geo_fingerprint`) and each `net` asked of one (one per line and unit),
    the distance basis's `profile_arrays`, and per key how many lines (attacker builds and
    objective lines) it nets and how many forms they made. Each set prints one `forms {json}` line
    to stderr, from the process that read it (a set after the first runs in a fresh one). Moves no
    number."""
    st = {}
    cur = [None]

    def row():
        return st.setdefault(cur[0], {'lines': 0, 'outcome_forms': 0, 'outcome_init_s': 0.0, 'outcome_nets': 0,
                                      'outcome_net_s': 0.0, 'followup_forms': 0, 'followup_init_s': 0.0,
                                      'followup_nets': 0, 'followup_net_s': 0.0, 'basis_profiles': 0,
                                      'basis_profile_s': 0.0, 'key_s': 0.0, 'cpu_s': 0.0})

    def timed(cls, meth, n_key, s_key):
        f = getattr(cls, meth)

        def w(*args, **kwargs):
            t = time.perf_counter()
            try:
                return f(*args, **kwargs)
            finally:
                r = row()
                r[n_key] += 1
                r[s_key] += time.perf_counter() - t
        setattr(cls, meth, w)
    timed(m.OutcomeForm, '__init__', 'outcome_forms', 'outcome_init_s')
    timed(m.OutcomeForm, 'net', 'outcome_nets', 'outcome_net_s')
    timed(m.FollowupForm, '__init__', 'followup_forms', 'followup_init_s')
    timed(m.FollowupForm, 'net', 'followup_nets', 'followup_net_s')
    pa = m.profile_arrays

    def profile_arrays(*args, **kwargs):
        t = time.perf_counter()
        try:
            return pa(*args, **kwargs)
        finally:
            r = row()
            r['basis_profiles'] += 1
            r['basis_profile_s'] += time.perf_counter() - t
    m.profile_arrays = profile_arrays
    pws = m._profile_work_shared

    def work(key, *args, **kwargs):
        cur[0] = key
        r = row()
        r['lines'] = len((m._SIM.get('builds', {}).get(key) or {})) + len((m._SIM.get('objective', {}).get(key) or {}))
        t, c0 = time.perf_counter(), time.process_time()
        try:
            return pws(key, *args, **kwargs)
        finally:
            r['key_s'] += time.perf_counter() - t
            r['cpu_s'] += time.process_time() - c0
            cur[0] = None
    m._profile_work_shared = work
    pb = m._profile_batch

    def batch(keys):
        st.clear()
        t = time.perf_counter()
        got = pb(keys)
        print('forms ' + json.dumps({'set': list(keys), 'wall': round(time.perf_counter() - t, 3),
                                     'keys': {str(k): {f: (round(v, 4) if isinstance(v, float) else v)
                                                       for f, v in r.items()} for k, r in st.items()}}),
              file=sys.stderr, flush=True)
        return got
    m._profile_batch = batch


def dump(a):
    m = load(a.scripts)
    t0 = time.perf_counter()
    stats = m.S.parse_stats(m.DEFAULT_STATS)
    lines = {'lines': [m.parse_line(x) for x in a.line]} if a.line else {}
    c = m.Context(stats, m.DEFAULT_CATALYSTS, 150, False, **lines)
    if a.forms:
        count_forms(m)
    print(f'context {time.perf_counter() - t0:.1f} s', file=sys.stderr, flush=True)
    tm = m.Timing(out=sys.stderr)
    only = {m.T._norm(x) for x in a.names}
    counts = count_reads(c.A) if a.count else None
    if a.look_cap:
        print(f'LOOKALIKE_MEMO_ENTRIES {c.A.LOOKALIKE_MEMO_ENTRIES} -> {a.look_cap}', file=sys.stderr, flush=True)
        c.A.LOOKALIKE_MEMO_ENTRIES = a.look_cap
    if a.report_every:
        # Progress through a set that prints none: `lookalike_plan` calls made and the memo emptyings
        # (`forget_look_memos`), every `report_every` seconds, from the first plan call.
        import threading
        A, pc = c.A, {'plans': 0, 'forgets': 0, 't0': None}
        lp, flm = A.lookalike_plan, A.forget_look_memos

        def plan(*args, **kwargs):
            if pc['t0'] is None:
                pc['t0'] = time.perf_counter()
            pc['plans'] += 1
            return lp(*args, **kwargs)

        def forget():
            pc['forgets'] += 1
            return flm()
        A.lookalike_plan, A.forget_look_memos = plan, forget

        def report():
            while True:
                time.sleep(a.report_every)
                if pc['t0'] is not None:
                    print(f"progress {time.perf_counter() - pc['t0']:.0f} s: {pc['plans']} plans, "
                          f"{pc['forgets']} memo emptyings, held {A._LOOK_HELD[0]}", file=sys.stderr, flush=True)
        threading.Thread(target=report, daemon=True).start()
    if a.plan_digest:
        # Every `lookalike_plan` answer, in call order, as a digest of its repr (a float's repr is
        # exact), for comparing two checkouts on a set too long to finish: stops the process after
        # `--plan-limit` calls, writing the digests and the wall and CPU seconds from the first call.
        import hashlib
        import os
        A, st = c.A, {'digests': [], 't0': None, 'c0': None}
        lp2 = A.lookalike_plan

        def plan_digest(*args, **kwargs):
            if st['t0'] is None:
                st['t0'], st['c0'] = time.perf_counter(), time.process_time()
            got = lp2(*args, **kwargs)
            st['digests'].append(hashlib.blake2b(repr(got).encode(), digest_size=12).hexdigest())
            if len(st['digests']) % 1000 == 0:
                # (calls, wall, CPU) every 1,000 calls, so a stretch of the set can be timed alone.
                st.setdefault('marks', []).append((len(st['digests']), time.perf_counter() - st['t0'],
                                                   time.process_time() - st['c0']))
            if len(st['digests']) >= a.plan_limit:
                wall, cpu = time.perf_counter() - st['t0'], time.process_time() - st['c0']
                Path(a.plan_digest).write_text(json.dumps({'plans': len(st['digests']), 'wall': wall, 'cpu': cpu,
                                                           'marks': st.get('marks', []),
                                                           'digests': st['digests']}))
                print(f'{len(st["digests"])} plans in {wall:.1f} s wall, {cpu:.1f} s CPU; wrote {a.plan_digest}',
                      file=sys.stderr, flush=True)
                os._exit(0)
            return got
        A.lookalike_plan = plan_digest
    if a.sizes:
        # What a forked worker would pickle back to the parent per item (`pooled`'s result pipe).
        import pickle
        for fn_name in ('_input_work', '_profile_batch'):
            fn = getattr(m, fn_name)

            def wrapped(x, fn=fn, fn_name=fn_name):
                got = fn(x)
                t = time.perf_counter()
                n = len(pickle.dumps(got, protocol=pickle.HIGHEST_PROTOCOL))
                print(f'pickled {fn_name} {str(x)[:80]!r}: {n:,} bytes in {time.perf_counter() - t:.3f} s',
                      file=sys.stderr, flush=True)
                return got
            setattr(m, fn_name, wrapped)
    prof = cProfile.Profile() if a.profile else None
    if prof:
        prof.enable()
    orders = []
    so = m.set_order

    def set_order(*args, **kwargs):
        got = so(*args, **kwargs)
        orders.append(got)
        return got
    m.set_order = set_order
    kw = {}
    if a.measured:
        # The run's order with the long sets first by the trail's measured seconds (`set_order`).
        kw['measured'] = m.measured_seconds()
        print(f'measured seconds for {len(kw["measured"])} keys', file=sys.stderr, flush=True)
    if a.kind:
        # By the candidates, not compute's `kinds`, so a checkout without it is cut the same way.
        for kind, fn_name in (('spell', 'spell_candidates'), ('skill', 'skill_candidates')):
            if kind not in a.kind:
                setattr(m, fn_name, lambda c_: ([], []))
    res = m.compute(c, only, 1, print, tm, **kw)
    if prof:
        prof.disable()
        prof.dump_stats(a.profile)
    if counts is not None:
        print('read counts: ' + json.dumps({k: (round(v, 3) if isinstance(v, float) else v)
                                            for k, v in counts.items()}), file=sys.stderr, flush=True)
    out = {}
    for n, arr in res.A.items():
        out[f'A/{n}'] = arr
    for n, arr in res.C.items():
        out[f'C/{n}'] = arr
    for bi, b in enumerate(res.builds):
        for k, nn in sorted(b['nets'].items()):
            if isinstance(nn, str):
                continue
            out[f'B{bi}/{k}/net'] = np.asarray(nn[0])
            out[f'B{bi}/{k}/net_one'] = np.asarray(nn[1])
    for k, r in res.records.items():
        for nm, o in (r.get('objective') or {}).items():
            if isinstance(o, dict):
                for f in ('net', 'net_one'):
                    if isinstance(o.get(f), (list, np.ndarray)):
                        out[f'O/{k}/{nm}/{f}'] = np.asarray(o[f])
    doc = {'keys': res.keys, 'comp_keys': res.comp_keys,
           'records': res.records, 'builds': [{k: v for k, v in b.items() if k != 'nets'} for b in res.builds],
           'items': tm.items, 'stages': tm.stages, 'set_order': orders}
    out['__doc__'] = np.array(json.dumps(doc, sort_keys=True, default=m._json_default))
    np.savez(a.out, **out)
    print(f'wrote {a.out}: {len(out) - 1} arrays; stages {json.dumps(tm.stages)}', file=sys.stderr)


def compare(pa, pb):
    A, B = np.load(pa), np.load(pb)
    ka, kb = set(A.files) - {'__doc__'}, set(B.files) - {'__doc__'}
    rc = 0
    if ka != kb:
        print(f'array names differ: only in a {sorted(ka - kb)[:10]}, only in b {sorted(kb - ka)[:10]}')
        rc = 1
    worst, unequal = 0.0, []
    for k in sorted(ka & kb):
        x, y = A[k], B[k]
        if x.shape != y.shape or x.dtype != y.dtype:
            print(f'{k}: shape/dtype {x.shape} {x.dtype} vs {y.shape} {y.dtype}')
            rc = 1
            continue
        same = np.array_equal(x, y, equal_nan=True)
        if not same:
            d = np.nanmax(np.abs(x.astype(float) - y.astype(float))) if x.size else 0.0
            worst = max(worst, float(d))
            unequal.append((k, float(d)))
    da, db = json.loads(str(A['__doc__'])), json.loads(str(B['__doc__']))
    for part in ('keys', 'comp_keys', 'records', 'builds'):
        if da[part] != db[part]:
            print(f'{part} differ')
            rc = 1
    oa, ob = da.get('set_order') or [], db.get('set_order') or []
    print(f'set order: a {len(oa[0]) if oa else 0} sets, b {len(ob[0]) if ob else 0}, '
          f'{"the same order" if oa == ob else "different orders"}; a starts {oa[0][:4] if oa else None}, '
          f'b starts {ob[0][:4] if ob else None}')
    print(f'{len(ka & kb)} arrays compared, {len(unequal)} not bit-identical, max abs diff {worst!r}')
    for k, d in unequal[:20]:
        print(f'  {k}: {d!r}')
    ia, ib = da['items'].get('attacks', {}), db['items'].get('attacks', {})
    for k in sorted(set(ia) & set(ib), key=lambda k: -ia[k][1]):
        print(f'  attacks {k}: a {ia[k][1]:.1f} CPU s, b {ib[k][1]:.1f} CPU s ({ib[k][1] / max(ia[k][1], 1e-9):.2f}x)')
    return 1 if (rc or unequal) else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--scripts', help='the scripts directory whose module runs')
    ap.add_argument('--out')
    ap.add_argument('--profile')
    ap.add_argument('--sizes', action='store_true', help='print the pickled size of every pooled item')
    ap.add_argument('--count', action='store_true', help='count the memoised reads made and made again')
    ap.add_argument('--look-cap', type=int, default=None,
                    help="set er-mechanics-ashes.LOOKALIKE_MEMO_ENTRIES for the run")
    ap.add_argument('--report-every', type=float, default=None,
                    help='print lookalike_plan calls and memo emptyings every this many seconds')
    ap.add_argument('--measured', action='store_true',
                    help="deal the long sets first by the default trail's measured seconds (`set_order`)")
    ap.add_argument('--plan-digest', help='write each lookalike_plan answer digest, in call order, to this file')
    ap.add_argument('--plan-limit', type=int, default=20000, help='stop after this many lookalike_plan calls')
    ap.add_argument('--compare-plans', nargs=2, help='compare two --plan-digest files')
    ap.add_argument('--plan-from', type=int, default=None,
                    help='with --compare-plans, also time the calls from this one on')
    ap.add_argument('--compare', nargs=2)
    ap.add_argument('--line', action='append', default=[],
                    help="one more attacker build at this spread (the module's --line); repeatable")
    ap.add_argument('--kind', action='append', choices=('spell', 'skill'), default=[],
                    help="keep only this kind of the names' attacks (compute's `kinds`); repeatable")
    ap.add_argument('--forms', action='store_true',
                    help='time each phase-2 form made and each net asked of one, per attack set (`count_forms`)')
    ap.add_argument('names', nargs='*')
    a = ap.parse_args()
    if a.compare:
        return compare(*a.compare)
    if a.compare_plans:
        x, y = (json.loads(Path(p).read_text()) for p in a.compare_plans)
        dx, dy = x['digests'], y['digests']
        first = next((i for i, (p, q) in enumerate(zip(dx, dy)) if p != q), None)
        print(f'{min(len(dx), len(dy))} plans compared, {sum(p != q for p, q in zip(dx, dy))} differ'
              f'{"" if first is None else f" (first at call {first})"}; a {x["wall"]:.1f} s wall {x["cpu"]:.1f} s CPU, '
              f'b {y["wall"]:.1f} s wall {y["cpu"]:.1f} s CPU ({x["cpu"] / max(y["cpu"], 1e-9):.2f}x CPU)')
        mx, my = {m[0]: m for m in x.get('marks', [])}, {m[0]: m for m in y.get('marks', [])}
        both = sorted(set(mx) & set(my))
        if a.plan_from is not None and both:
            # The stretch from the mark at or after `--plan-from` to the last mark both runs hold.
            lo = next((n for n in both if n >= a.plan_from), both[-1])
            hi = both[-1]
            cx, cy = mx[hi][2] - mx[lo][2], my[hi][2] - my[lo][2]
            print(f'calls {lo} to {hi}: a {mx[hi][1] - mx[lo][1]:.1f} s wall {cx:.1f} s CPU, '
                  f'b {my[hi][1] - my[lo][1]:.1f} s wall {cy:.1f} s CPU ({cx / max(cy, 1e-9):.2f}x CPU)')
        return 1 if first is not None or len(dx) != len(dy) else 0
    dump(a)
    return 0


if __name__ == '__main__':
    sys.exit(main())
