#!/usr/bin/env python3
"""Which way a chainsaw loop moves its victim, from one chainsaw-driver.js watcher log.

The driver run needs `sustain.trace` (a `vtrace` position per free character per frame) and
leaves the victims free after the equip (`pinOthers: 'untilEquip'` or false). For every hit the
victim's position `--before` frames earlier and `--after` frames later are compared, and the move
is split into the component away from the player (`radial`, + is pushed away, - is pulled in) and
the sideways one. The player's facing comes from the hit record's orientation quaternion, taking
forward as +z rotated by it; `fwd_dot_target` reports how well that forward points at the victim,
which checks the convention on a locked-on run instead of assuming it.

    python3 scripts/er-mechanics-chainsaw-pull.py <watch-log>
"""
import argparse
import ast
import math
import sys


def records(path):
    for line in open(path, encoding='utf-8'):
        if line.startswith('AGENT {'):
            try:
                yield ast.literal_eval(line[6:].strip())
            except (ValueError, SyntaxError):
                continue


def forward(q):
    x, y, z, w = q
    # Third column of the rotation matrix: +z rotated by q, flattened to the ground plane.
    fx = 2 * (x * z + w * y)
    fz = 1 - 2 * (x * x + y * y)
    n = math.hypot(fx, fz) or 1.0
    return fx / n, fz / n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('log')
    ap.add_argument('--before', type=int, default=1)
    ap.add_argument('--after', type=int, default=8)
    a = ap.parse_args(argv)
    trace = {}
    hits = []
    pin = None
    for r in records(a.log):
        if r.get('kind') == 'vtrace':
            trace.setdefault(r['chr'], {})[r['frame']] = r['pos']
        elif r.get('kind') == 'result' and not hits:
            # The driver reports its hits inside the verdict, not as messages of their own.
            hits = ((r.get('summary') or {}).get('verdict') or {}).get('hitDetail') or []
        elif r.get('kind') == 'hit' and r.get('damage'):
            # lure-trace.js sends each damaging hit on its own, with the player's position at that moment.
            hits.append(r)
        elif r.get('kind') == 'hb' and r.get('playerPin'):
            pin = r['playerPin']['pos']
    if pin is None and not all(h.get('player') for h in hits):
        print('no player pin in the log; the run needs sustain.lock, or hits that carry the player position', file=sys.stderr)
        return 1
    rows = []
    for h in hits:
        v = h.get('victim')
        t = trace.get(v, {})
        f = h['frame']
        p0, p1 = t.get(f - a.before), t.get(f + a.after)
        if p0 is None or p1 is None:
            continue
        me = h.get('player') or pin
        rx, rz = p0[0] - me[0], p0[2] - me[2]
        rn = math.hypot(rx, rz) or 1.0
        ux, uz = rx / rn, rz / rn
        mx, mz = p1[0] - p0[0], p1[2] - p0[2]
        fx, fz = forward(h['quat']) if h.get('quat') else (0.0, 0.0)
        rows.append((f, v, h['atk'], round(rn, 2), round(mx * ux + mz * uz, 3), round(-mx * uz + mz * ux, 3),
                     round(mx * fx + mz * fz, 3), round(fx * ux + fz * uz, 3)))
    print('frame victim atk dist radial sideways along_facing fwd_dot_target')
    for row in rows:
        print(*row)
    by = {}
    for row in rows:
        by.setdefault(row[1], []).append(row)
    for v, rs in by.items():
        n = len(rs)
        print(f'{v}: hits={n} mean_radial={sum(r[4] for r in rs) / n:.3f} mean_sideways={sum(r[5] for r in rs) / n:.3f} '
              f'mean_along_facing={sum(r[6] for r in rs) / n:.3f} mean_fwd_dot_target={sum(r[7] for r in rs) / n:.2f}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
