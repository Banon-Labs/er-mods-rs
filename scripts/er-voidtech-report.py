#!/usr/bin/env python3
"""Per-attempt report of the void tech hostile (scripts/frida/ai-lua/mods/brain_voidtech.lua).

Reads the AI lab log (scripts/er-ai-lab.py --log, with --extra-agent void=scripts/frida/void-trace.js
and the hostile set as the trace's watch target) and prints one row per jump-cast attempt: the
press delay the brain used, the frames from the in-air cast clip (045070) to the landed one
(045074), how many times FP was charged, and the hits and damage the player took from it before
the next attempt. A void double shows up as two FP charges, or more hits than the same spell
lands from a cast on the ground.

    python3 scripts/er-voidtech-report.py <lab log> [--since <epoch seconds>]
"""
import argparse
import json


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('log')
    ap.add_argument('--since', type=float, default=0.0)
    args = ap.parse_args()

    events = []
    with open(args.log, encoding='utf-8') as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get('at', 0) < args.since:
                continue
            events.append(d)

    # One attempt per in-air cast clip (045070): the brain's own "voidtech" lines do not map one to
    # one onto jumps (measured: a decision's goal list sometimes ran twice before the next plan), so
    # the clip is the unit and the latest brain line before it supplies the delay.
    attempts = []
    cur = None
    plan = {'n': '-', 'delay': '-', 'd': '-'}
    for d in events:
        kind = d.get('kind')
        if kind == 'ai' and d.get('what') == 'voidtech' and d.get('do') == 'jump-cast':
            plan = {'n': d.get('n'), 'delay': d.get('delay'), 'd': d.get('d')}
        elif kind == 'clip' and d.get('src') == 'void' and d.get('clip', '').endswith('_045070'):
            cur = dict(plan, air=d.get('frame'), land=None, fp=0, fp_spent=0, hits=0, damage=0, fp_frames=[])
            attempts.append(cur)
        elif cur is None:
            continue
        elif kind == 'clip' and d.get('src') == 'void' and d.get('clip', '').endswith('_045074'):
            if cur['land'] is None:
                cur['land'] = d.get('frame')
        elif kind == 'fp' and d.get('delta', 0) < 0:
            cur['fp'] += 1
            cur['fp_spent'] -= d['delta']
            cur['fp_frames'].append(d.get('frame'))
        elif kind == 'hit' and d.get('taken') and d.get('damage', 0) > 0:
            cur['hits'] += 1
            cur['damage'] += d['damage']

    print(f"{'n':>3} {'delay':>6} {'dist':>5} {'air->land':>9} {'cast-land':>9} {'fp charges':>10} {'fp':>4} {'hits':>4} {'dmg':>5}")
    for a in attempts:
        gap = (a['land'] - a['air']) if a['land'] is not None else None
        # Frames from landing to the first FP charge: 0 is the cast on the landing frame itself.
        cast = (a['fp_frames'][0] - a['land']) if a['land'] is not None and a['fp_frames'] else None
        print(f"{a['n']:>3} {a['delay']:>6} {a['d']:>5} {gap if gap is not None else '-':>9} "
              f"{cast if cast is not None else '-':>9} {a['fp']:>10} {a['fp_spent']:>4} {a['hits']:>4} {a['damage']:>5}")
    doubles = [a for a in attempts if a['fp'] >= 2]
    print(f"attempts {len(attempts)}, with two or more FP charges {len(doubles)}")


if __name__ == '__main__':
    main()
