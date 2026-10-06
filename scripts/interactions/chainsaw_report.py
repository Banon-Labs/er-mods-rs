#!/usr/bin/env python3
"""Timeline of a scripts/frida/chainsaw-probe.js run (docs/er-mechanics/chainsaw/).

    python3 scripts/interactions/chainsaw_report.py [log] [--since epoch_seconds]
    python3 scripts/interactions/chainsaw_report.py --selftest

The log defaults to the watcher's ~/.cache/er-frida/hits.jsonl. Prints one line per equip, switch,
TimeAct equip-state, broadcast and extra-hook event, collapses consecutive hits with the same
(attack, launch weapon, held right weapon) into one line with a count and damage range, and ends
with the hits grouped by attack id, so a chainsaw run reads as: which attacks kept landing after
the right-hand weapon changed, and which weapon each was computed from.
"""
import collections
import json
import os
import sys

DEFAULT_LOG = os.path.join(os.path.expanduser('~'), '.cache', 'er-frida', 'hits.jsonl')


def events(lines, since=0.0):
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get('at', 0) < since:
            continue
        p = rec.get('message', {}).get('payload')
        if isinstance(p, dict) and 'kind' in p:
            yield p


def report(evs):
    out = []
    t0 = None
    run = None
    by_atk = collections.defaultdict(lambda: {'n': 0, 'dmg': [], 'launch': set(), 'held': set()})

    def flush():
        if run:
            d = run['dmg']
            out.append(f"{run['t']:9.3f}  hit x{run['n']:<3} atk {run['atk']} launch {run['launch']} "
                       f"held_r {run['held']} dmg {min(d)}..{max(d)}")

    for e in evs:
        t0 = e['t'] if t0 is None else t0
        t = (e['t'] - t0) / 1000.0
        if e['kind'] == 'hit':
            key = (e['atk'], e['launch_weapon'], e['held_r'])
            a = by_atk[e['atk']]
            a['n'] += 1
            a['dmg'].append(e['damage'])
            a['launch'].add(e['launch_weapon'])
            a['held'].add(e['held_r'])
            if run and run['key'] == key:
                run['n'] += 1
                run['dmg'].append(e['damage'])
                continue
            flush()
            run = {'key': key, 't': t, 'n': 1, 'atk': e['atk'], 'launch': e['launch_weapon'],
                   'held': e['held_r'], 'dmg': [e['damage']]}
            continue
        flush()
        run = None
        rest = {k: v for k, v in e.items() if k not in ('kind', 't')}
        out.append(f"{t:9.3f}  {e['kind']:<6} {json.dumps(rest, sort_keys=True)}")
    flush()
    out.append('')
    out.append('hits by attack id:')
    for atk in sorted(by_atk, key=lambda k: (k is None, k)):
        a = by_atk[atk]
        out.append(f"  atk {atk}: {a['n']} hits, dmg {min(a['dmg'])}..{max(a['dmg'])}, "
                   f"launch {sorted(a['launch'], key=str)}, held_r {sorted(a['held'], key=str)}")
    return out


def selftest():
    def rec(p, at=10.0):
        return json.dumps({'at': at, 'message': {'type': 'send', 'payload': p}})
    lines = [
        rec({'kind': 'armed', 't': 1000, 'hooks': 5}),
        rec({'kind': 'hit', 't': 1100, 'atk': 7, 'launch_weapon': 100, 'held_r': 100, 'damage': 50}),
        rec({'kind': 'hit', 't': 1200, 'atk': 7, 'launch_weapon': 100, 'held_r': 100, 'damage': 60}),
        rec({'kind': 'equip', 't': 1300, 'slot': 0, 'held_r': 200}),
        rec({'kind': 'hit', 't': 1400, 'atk': 7, 'launch_weapon': 200, 'held_r': 200, 'damage': 90}),
        'not json',
        rec({'kind': 'hit', 't': 1500, 'atk': 8, 'launch_weapon': 200, 'held_r': 200, 'damage': 9}, at=1.0),
    ]
    out = report(events(lines, since=5.0))
    assert out[1].startswith('    0.100  hit x2   atk 7 launch 100 held_r 100 dmg 50..60'), out
    assert 'equip' in out[2] and '"held_r": 200' in out[2], out
    assert out[3].startswith('    0.400  hit x1   atk 7 launch 200 held_r 200 dmg 90..90'), out
    assert out[-1] == '  atk 7: 3 hits, dmg 50..90, launch [100, 200], held_r [100, 200]', out
    assert not any('atk 8' in o for o in out), 'the --since filter dropped nothing'
    print('selftest ok')
    return 0


def main(argv):
    if '--selftest' in argv:
        return selftest()
    since = 0.0
    if '--since' in argv:
        i = argv.index('--since')
        since = float(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    path = argv[0] if argv else DEFAULT_LOG
    with open(path, encoding='utf-8') as f:
        for line in report(events(f, since)):
            print(line)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
