#!/usr/bin/env python3
"""Map every skill's TimeAct events to BehaviorParam_PC rows, bullet ids and SpEffect ids, offline.

    python3 scripts/interactions/skill_bullet_map.py OUT.json

For each SwordArtsParam row: every weapon whose own skill it is, plus one weapon per weapon type
that can mount its ash. `er-mechanics-ashes.skill_profile` resolves the TAE events. `build()`
returns, keyed by id, the skill/weapon tags that fire a behavior row, reach a bullet, or apply a
SpEffect (with the SpEffect's own link chain followed, so Seppuku's 1754 also names 1755).
"""
import collections
import importlib.util
import json
import os
import sys

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location('er_mechanics_ashes',
                                               os.path.join(SCRIPTS, 'er-mechanics-ashes.py'))
ASH = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ASH)


def _bullets_in(bt, out):
    if not isinstance(bt, dict):
        return
    if 'bullet' in bt and isinstance(bt['bullet'], int):
        out.add(bt['bullet'])
    for v in bt.values():
        if isinstance(v, dict):
            _bullets_in(v, out)
        elif isinstance(v, list):
            for x in v:
                _bullets_in(x, out)


def _speffects_in(node, out):
    if not isinstance(node, dict):
        return
    if isinstance(node.get('id'), int):
        out.add(node['id'])
    for v in (node.get('links') or {}).values():
        _speffects_in(v, out)


def build():
    t = ASH.AshTables()
    weapons = {i: w for i, w in t.reg.weapon.items() if i % 10000 == 0}
    by_skill = collections.defaultdict(set)
    for wid, w in weapons.items():
        by_skill[w['swordArtsParamId']].add(wid)
    for sid, gid in t.ash_gems().items():
        seen_types = set()
        for wid, w in weapons.items():
            if w['wepType'] in seen_types:
                continue
            if t.can_mount(wid, gid, level=25)[0]:
                seen_types.add(w['wepType'])
                by_skill[sid].add(wid)
    beh = collections.defaultdict(set)
    bul = collections.defaultdict(set)
    spe = collections.defaultdict(set)
    for sid in sorted(by_skill):
        if sid not in t.arts:
            continue
        name = t.arts_name(sid)
        for wid in sorted(by_skill[sid]):
            try:
                prof = ASH.skill_profile(t, sid, wid)
            except SystemExit:
                continue
            wname = t.reg.weapon_names.get(wid) or str(wid)
            for anim, acts in prof['anims'].items():
                tag = f'{name} ({sid}) on {wname} ({wid}) anim {anim}'
                for x in acts:
                    if x.get('kind') == 'speffect':
                        s = set()
                        _speffects_in(x.get('speffect') or {}, s)
                        for i in s:
                            spe[i].add(tag)
                    if x.get('kind') != 'bullet':
                        continue
                    if x.get('behavior_row') is not None:
                        beh[x['behavior_row']].add(tag)
                    s = set()
                    _bullets_in(x.get('bullet') or {}, s)
                    for b in s:
                        bul[b].add(tag)
    return {'behavior': {k: sorted(v) for k, v in beh.items()},
            'bullet': {k: sorted(v) for k, v in bul.items()},
            'speffect': {k: sorted(v) for k, v in spe.items()}}


def main():
    out = build()
    with open(sys.argv[1], 'w') as fh:
        json.dump(out, fh, indent=1)
    print(len(out['behavior']), 'behavior rows', len(out['bullet']), 'bullets',
          len(out['speffect']), 'speffects')


if __name__ == '__main__':
    main()
