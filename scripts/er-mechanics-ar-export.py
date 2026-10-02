#!/usr/bin/env python3
"""Export what a page needs to compute one weapon's attack rating and skill hits from any stats.

    python3 scripts/er-mechanics-ar-export.py Lance > lance.json
    python3 scripts/er-mechanics-ar-export.py Lance --skill "Charge Forth" > lance.json
    python3 scripts/er-mechanics-ar-export.py --selftest

The output pairs with `scripts/er-ar-calc.js`, a port of `er-mechanics-ar.py`'s scaling code.
For every affinity the weapon has and every upgrade level it carries each element's base attack,
its correction graph, and per stat the requirement, the scaling rate (weapon rate x reinforce
rate, or the attack-element row's overwrite) and the influence; arcane status build-up the same
way; and the constants the scaling reads. With `--skill`, the skill's hits as
`er-mechanics-ashes.skill_hits` lists them: frame, motion values per element and poise for a hit
taken from the weapon, flat attack for a bullet.

`--selftest` evaluates `er-ar-calc.js` under node over a grid of stats, levels, affinities and
grips and requires every element and status to match `er-mechanics-ar.attack_rating` to 1e-6.
"""
import argparse
import importlib.util
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


AR = _load('er_mechanics_ar', 'er-mechanics-ar.py')
JS = os.path.join(HERE, 'er-ar-calc.js')
NODE_TIMEOUT_S = 25


def export(tables, weapon, skill=None):
    base_id = tables.find_weapon(weapon)
    graphs, out = {}, {}
    for aff in AR.AFFINITIES:
        try:
            wid = tables.find_weapon(weapon, aff)
        except SystemExit:
            continue
        wep = tables.weapons[wid]
        aecp = tables.aecp.get(wep['attackElementCorrectId'], {})
        levels = []
        for level in range(tables.max_level(wep['reinforceTypeId']) + 1):
            reinf = tables.reinforce[wep['reinforceTypeId'] + level]
            elements = {}
            for name, suf, bfield, rfield, gfield in AR.ELEMENTS:
                base = wep[bfield] * reinf[rfield]
                if not base:
                    continue
                stats = {}
                for s in AR.STATS:
                    aname, cfield, rrate, pfield = AR.STAT_FIELDS[s]
                    if not aecp.get(f'is{aname}Correct_by{suf}'):
                        continue
                    over = aecp.get(f'overwrite{aname}CorrectRate_by{suf}', -1)
                    stats[s] = {'req': wep[pfield], 'infl': aecp.get(f'Influence{aname}CorrectRate_by{suf}', 100) * 0.01,
                                'rate': (over if over >= 0 else wep[cfield]) * reinf[rrate]}
                graphs[wep[gfield]] = tables.graphs.get(wep[gfield])
                elements[name] = {'base': base, 'graph': wep[gfield], 'stats': stats}
            status = {}
            arc_rate = wep['correctLuck'] * reinf['correctLuckRate']
            for slot in range(3):
                sp = wep.get(f'spEffectBehaviorId{slot}', -1)
                row = tables.speffects.get(sp + reinf.get(f'spEffectId{slot + 1}', 0)) if sp is not None and sp >= 0 else None
                for name, field, gfield in AR.STATUSES if row else ():
                    val = row.get(field, 0)
                    if not val:
                        continue
                    g = wep[gfield] if gfield else None
                    if g is not None:
                        graphs[g] = tables.graphs.get(g)
                    prev = status.get(name, {'base': 0, 'graph': g, 'req': wep['properLuck'], 'rate': arc_rate})
                    prev['base'] += val
                    status[name] = prev
            levels.append({'elements': elements, 'status': status})
        out[aff] = {'id': wid, 'levels': levels}
    w0 = tables.weapons[base_id]
    data = {
        'weapon': tables.names.get(base_id), 'id': base_id,
        'requirements': {s: w0[AR.STAT_FIELDS[s][3]] for s in AR.STATS},
        'always_two_handed': w0['wepType'] in AR.ALWAYS_TWO_HANDED_WEP_TYPES,
        'constants': {'twoHandStrMult': AR.TWO_HAND_STR_MULT, 'penaltyCapPct': AR.PENALTY_CAP_PCT,
                      'penaltyFullPct': AR.PENALTY_FULL_PCT,
                      'lowStatusAtkPowDown': tables.low_status_atk_pow_down,
                      'graphs': {str(k): {'v': [g[f'stageMaxVal{i}'] for i in range(5)],
                                          'g': [g[f'stageMaxGrowVal{i}'] for i in range(5)],
                                          'a': [g[f'adjPt_maxGrowVal{i}'] for i in range(5)]}
                                 for k, g in graphs.items() if g}},
        'affinities': out,
    }
    if skill is not None:
        data['skill'] = skill_hits(weapon, skill)
    return data


def skill_hits(weapon, skill):
    levers = _load('er_mechanics_ash_levers', 'er-mechanics-ash-levers.py')
    L = levers.Levers()
    wid = L.t.find_weapon(weapon)
    sid = L.t.find_arts(skill) if skill else L.t.reg.weapon[wid]['swordArtsParamId']
    _, hits, _ = L.cast(weapon, 'Standard', sid, {s: 99 for s in AR.STATS}, False)
    return {'name': L.t.arts_name(sid) if hasattr(L.t, 'arts_name') else skill, 'id': sid,
            'fp': levers.A.skill_fp(L.t, sid),
            'hits': [{'frame': h.get('frame'), 'from_weapon': h['from_weapon'], 'count': h.get('count', 1),
                      'mv': {k: v for k, v in h['mv'].items() if v}, 'flat': {k: v for k, v in h['flat'].items() if v},
                      'poise': h.get('poise')} for h in hits]}


def selftest():
    tables = AR.Tables()
    cases = []
    for weapon in ('Lance', 'Uchigatana', 'Claymore'):
        data = export(tables, weapon)
        for aff, a in data['affinities'].items():
            for level in sorted({0, len(a['levels']) - 1}):
                for st in ({'str': 10, 'dex': 10, 'int': 10, 'fth': 10, 'arc': 10},
                           {'str': 40, 'dex': 25, 'int': 30, 'fth': 18, 'arc': 45},
                           {'str': 80, 'dex': 60, 'int': 70, 'fth': 50, 'arc': 99}):
                    for two in (False, True):
                        ref = AR.attack_rating(tables, weapon, aff, level, st, two)
                        cases.append({'weapon': weapon, 'aff': aff, 'level': level, 'stats': st, 'two': two,
                                      'damage': {k: v['total'] for k, v in ref['damage'].items()},
                                      'status': {k: v['total'] for k, v in ref['status'].items()},
                                      'row': a['levels'][level], 'c': data['constants']})
    prog = (f"const E=require({json.dumps(JS)});const cases=JSON.parse(require('fs').readFileSync(0,'utf8'));"
            "let bad=[];for(const k of cases){const r=E.attackRating(k.c,k.row,k.stats,k.two);"
            "for(const [n,v] of Object.entries(k.damage)){if(Math.abs((r.damage[n]||0)-v)>1e-6)bad.push([k.weapon,k.aff,k.level,n,v,r.damage[n]]);}"
            "for(const [n,v] of Object.entries(k.status)){if(Math.abs((r.status[n]||0)-v)>1e-6)bad.push([k.weapon,k.aff,k.level,n,v,r.status[n]]);}}"
            "console.log(JSON.stringify({n:cases.length,bad:bad.slice(0,10),nbad:bad.length}));")
    res = subprocess.run(['node', '-e', prog], input=json.dumps(cases), capture_output=True, text=True,
                         timeout=NODE_TIMEOUT_S)
    if res.returncode != 0:
        raise SystemExit(f'node failed: {res.stderr[-2000:]}')
    r = json.loads(res.stdout)
    assert r['nbad'] == 0, r
    print(f"selftest ok: er-ar-calc.js matches er-mechanics-ar.py on {r['n']} cases")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('weapon', nargs='?')
    ap.add_argument('--skill', nargs='?', const='', default=None,
                    help="add the skill's hits; with no name, the weapon's own skill")
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.weapon:
        ap.error('weapon required')
    json.dump(export(AR.Tables(), a.weapon, a.skill), sys.stdout, separators=(',', ':'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
