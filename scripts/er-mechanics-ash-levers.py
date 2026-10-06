#!/usr/bin/env python3
"""What to level for a weapon skill: the damage each stat adds to one cast, per affinity, offline.

    python3 scripts/er-mechanics-ash-levers.py "Lance" "Loretta's Slash"
    python3 scripts/er-mechanics-ash-levers.py "Claymore" "Lion's Claw" --affinity Heavy,Quality,Keen
    python3 scripts/er-mechanics-ash-levers.py "Wing of Astel" --stats str=12,dex=20,int=60 --json

The skill's total attack before defense is summed over every hit `skill_hits` reports, then
recomputed with each stat raised by `--step`; the difference is that stat's lever. A hit whose
attack comes from the weapon's motion values grows with whatever the affinity scales, while a
flat skill bullet grows only with the element its attack-element row corrects, so the answer
differs by affinity and often by hit. With no skill named, the weapon's own skill is used.
"""
import argparse
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STATS = ('str', 'dex', 'int', 'fth', 'arc')
AFFINITIES = ('Standard', 'Heavy', 'Keen', 'Quality', 'Fire', 'Flame Art', 'Lightning', 'Sacred',
              'Magic', 'Cold', 'Poison', 'Blood', 'Occult')


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


A = _load('er_mechanics_ashes', 'er-mechanics-ashes.py')


class Levers:
    def __init__(self):
        self.t = A.AshTables()
        self.ar_tables = None

    def cast(self, weapon, affinity, sid, stats, two_handed):
        ctx = A.WeaponContext(weapon, affinity, level=None, stats=stats, two_handed=two_handed,
                              ar_tables=self.ar_tables)
        self.ar_tables = ctx.tables
        hits = A.skill_hits(self.t, self.t.find_weapon(weapon), sid, ctx, ctx.level)
        return sum(sum(h['attack'].values()) * h.get('count', 1) for h in hits), hits, ctx

    def run(self, weapon, skill, affinities, stats, step, two_handed):
        wid = self.t.find_weapon(weapon)
        sid = self.t.find_arts(skill) if skill else self.t.reg.weapon[wid]['swordArtsParamId']
        out = {'weapon': weapon, 'skill': self.t.arts_name(sid) if hasattr(self.t, 'arts_name') else skill,
               'sword_arts_id': sid, 'fp': A.skill_fp(self.t, sid), 'stats': stats, 'step': step,
               'affinities': []}
        for aff in affinities:
            try:
                base, hits, ctx = self.cast(weapon, aff, sid, stats, two_handed)
            except (KeyError, ValueError, StopIteration, SystemExit):
                continue
            gain = {}
            for s in STATS:
                raised = dict(stats)
                raised[s] = min(99, raised[s] + step)
                gain[s] = round(self.cast(weapon, aff, sid, raised, two_handed)[0] - base, 1)
            out['affinities'].append({
                'affinity': aff, 'skill_attack': round(base, 1),
                'weapon_ar': {k: round(v, 1) for k, v in ctx.ar_by.items() if v},
                'gain_per_step': gain,
                'hits': [{'kind': h['kind'], 'from_weapon': h['from_weapon'],
                          'mv': {k: v for k, v in h['mv'].items() if v},
                          'flat': {k: v for k, v in h['flat'].items() if v},
                          'attack': {k: v for k, v in h['attack'].items() if v}} for h in hits],
            })
        out['affinities'].sort(key=lambda a: -a['skill_attack'])
        return out


def parse_stats(text):
    stats = {s: 20 for s in STATS}
    for part in filter(None, (text or '').split(',')):
        k, _, v = part.partition('=')
        stats[k.strip()] = int(v)
    return stats


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('weapon')
    ap.add_argument('skill', nargs='?')
    ap.add_argument('--affinity', help='comma list; default every affinity the weapon takes')
    ap.add_argument('--stats', help='str=20,dex=20,int=20,fth=20,arc=20 (unnamed stats are 20)')
    ap.add_argument('--step', type=int, default=10)
    ap.add_argument('--two-handed', action='store_true')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()
    affs = args.affinity.split(',') if args.affinity else AFFINITIES
    res = Levers().run(args.weapon, args.skill, affs, parse_stats(args.stats), args.step, args.two_handed)
    if args.json:
        print(json.dumps(res, indent=1))
        return
    print(f"{res['weapon']} / {res['skill']}  (FP {res['fp']}, +{res['step']} per stat from {res['stats']})")
    for a in res['affinities']:
        g = '  '.join(f"{s} {v:+.0f}" for s, v in a['gain_per_step'].items() if v)
        print(f"  {a['affinity']:<10} skill {a['skill_attack']:7.1f}   {g}")


if __name__ == '__main__':
    main()
