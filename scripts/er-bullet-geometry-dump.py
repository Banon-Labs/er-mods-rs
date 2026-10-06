#!/usr/bin/env python3
"""Dump the bullet trees a list of skills fire, with every Bullet field of every row in them.

Research helper for bullet launch geometry (bd bullet-launch-geometry-for-skill-reach-2026-09-29).
Prints one JSON object: {'skills': {name: {arts, weapon, roots: [{anim, frames, tree}]}},
'rows': {bullet id: full Bullet row}}.

    python3 scripts/er-bullet-geometry-dump.py ['Skill name' ...] > out.json
"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SKILLS = ['Thunderbolt', 'Lightning Slash', 'Divine Beast Frost Stomp', 'Hoarfrost Stomp',
                  'Glintblade Phalanx', 'Carian Sovereignty', 'Storm Stomp', 'Flame of the Redmanes',
                  'Vacuum Slice']


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(argv):
    ash = _load('er_mechanics_ashes', 'er-mechanics-ashes.py')
    t = ash.AshTables()
    rows, _, _ = ash.PR.rows(ash.PR.param_bytes(ash.PR.load(None), 'Bullet'), None)
    full = {r['id']: r for r in rows}
    ids = set()

    def walk(bt):
        ids.add(bt['bullet'])
        return {'id': bt['bullet'], 'name': bt['name'], 'dmg': ash._bullet_damages_self(bt),
                'atk': bt['atk_row'], 'mv': bt['mv'],
                'children': {k: walk(c) for k, c in bt['children'].items()}}

    out = {}
    for s in argv or DEFAULT_SKILLS:
        aid = t.find_arts(s)
        w = next((wid for wid, r in t.reg.weapon.items()
                  if r.get('swordArtsParamId') == aid and wid % 10000 == 0), 2000000)
        prof = ash.skill_profile(t, aid, w)
        roots = []
        for anim, acts in prof['anims'].items():
            if anim in prof['no_fp_anims']:
                continue
            for x in acts:
                if x['kind'] == 'bullet' and x.get('bullet'):
                    roots.append({'anim': anim, 'frames': x.get('frames'), 'tree': walk(x['bullet'])})
        out[s] = {'arts': aid, 'weapon': w, 'wname': t.reg.weapon_names.get(w), 'roots': roots}
    print(json.dumps({'skills': out, 'rows': {i: full[i] for i in sorted(ids) if i in full}},
                     default=str))


if __name__ == '__main__':
    main(sys.argv[1:])
