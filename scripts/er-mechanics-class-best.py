#!/usr/bin/env python3
"""The best RL 150 build of an armament the melee ranking leaves out, ranked within its own class.

    python3 scripts/er-mechanics-class-best.py "Meteorite Staff"
    python3 scripts/er-mechanics-class-best.py Longbow --json
    python3 scripts/er-mechanics-class-best.py --selftest

`er-mechanics-infusions.py` ranks a weapon by one melee hit against every other weapon's, so it
skips the classes `er-builds-optimize.py`'s sweep skips (`SWEEP_SKIP_WEP_TYPES`). This script is
their counterpart, and it measures each class by what the class is used for:

| class | measure | ranked against |
| --- | --- | --- |
| staves, seals | the spell buff (sorcery or incantation scaling) of the best RL 150 build | the same class |
| bows, crossbows, ballistae, perfume bottles, fists | the attack rating of the best RL 150 build | the same class |
| arrows, greatarrows, bolts, ballista bolts | the ammunition's own damage and build-up, which no stat scales | the same class |

The builds come from `er-builds-optimize.optimize` exactly as the infusion ranking's do: Vigor,
Mind and Endurance floors from the corpus's PvP builds at RL 150, every requirement met, every
starting class tried, and the remaining points spent where they raise the measure most. A bow's
attack rating is the bow's own, the number its menu shows, before the arrow is added.
"""
import argparse
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _mod(name, fname):
    s = importlib.util.spec_from_file_location(name, os.path.join(HERE, fname))
    m = importlib.util.module_from_spec(s)
    sys.modules[name] = m
    s.loader.exec_module(m)
    return m


INF = _mod('er_mechanics_infusions', 'er-mechanics-infusions.py')
OPT, AR, CARD = INF.OPT, INF.AR, INF.CARD
RANK_RL = INF.RANK_RL
CASTING_WEP_TYPES = {57: 'sorcery', 61: 'incantation'}
#: The stat every ammunition row is read at; nothing scales it, so any value gives the same number.
AMMO_STATS = {k: 10 for k in AR.STATS}


def kind(wep_type):
    if wep_type in CASTING_WEP_TYPES:
        return 'casting'
    if wep_type in OPT.AMMO_WEP_TYPES:
        return 'ammo'
    return 'ar'


def best(tables, name):
    """The best RL 150 build of `name` by its class's measure, or None when no class can wield it."""
    wid = tables.find_weapon(name)
    k = kind(tables.weapons[wid]['wepType'])
    if k == 'ammo':
        r = AR.attack_rating(tables, name, 'Standard', 0, AMMO_STATS, False)
        damage = {e: round(v['total'], 1) for e, v in r['damage'].items() if v['total'] > 0}
        status = {s: round(v['total'], 1) for s, v in r['status'].items() if v['total'] > 0}
        return {'kind': k, 'value': round(sum(damage.values()), 1), 'damage': damage, 'status': status}
    b = INF.Builder(name)
    fl, dfn = b.setup(RANK_RL)
    res = OPT.optimize(b.tables, b.model, name, RANK_RL, False, 'spell_buff' if k == 'casting' else 'ar',
                       fl, dfn, b.affs, None, keep=1)
    if not res:
        return None
    r = res[0]
    out = {'kind': k, 'value': round(r['score'], 1), 'affinity': r['affinity'], 'class': r['class'],
           'stats': r['stats']}
    if k == 'casting':
        out['spell_buff'] = {s: round(v, 1) for s, v in r['ar']['spell_buff'].items()}
    else:
        out['damage'] = {e: round(v, 1) for e, v in r['by_element'].items() if v > 0}
        out['status'] = INF.status_buildup(b.tables, name, r['affinity'], r['stats'])
    return out


def peers(tables, wep_type):
    """Every base row of `wep_type` the game names, as `er-mechanics-weapon-card` picks base rows."""
    return sorted(wid for wid, w in tables.weapons.items()
                  if wid % 10000 == 0 and w.get('wepType') == wep_type
                  and (tables.names.get(wid) or '[').strip()[:1] not in ('', '['))


_POPULATION = {}


def population(tables, wep_type):
    """{name: value} of every peer's best build, computed once per class per process."""
    if wep_type not in _POPULATION:
        vals = {}
        for wid in peers(tables, wep_type):
            nm = tables.names[wid]
            try:
                b = best(tables, nm)
            except (Exception, SystemExit):           # a row the AR model cannot read
                continue
            if b:
                vals[nm] = b['value']
        _POPULATION[wep_type] = vals
    return _POPULATION[wep_type]


def report(name, tables=None):
    tables = tables or AR.Tables(None)
    wid = tables.find_weapon(name)
    wep_type = tables.weapons[wid]['wepType']
    me = best(tables, name)
    if me is None:
        return None
    pop = population(tables, wep_type)
    me['weapon'] = tables.names.get(wid)
    me['rank'] = CARD.rank(list(pop.values()), me['value'], 'high') if len(pop) > 1 else None
    me['of'] = len(pop)
    me['spell_kind'] = CASTING_WEP_TYPES.get(wep_type)
    return me


def selftest():
    def check(cond, msg):
        if not cond:
            raise SystemExit(f'selftest: {msg}')
    check(kind(57) == 'casting' and kind(61) == 'casting', 'staves and seals are casting')
    check(kind(81) == 'ammo' and kind(86) == 'ammo', 'arrows and ballista bolts are ammunition')
    check(kind(51) == 'ar' and kind(89) == 'ar' and kind(33) == 'ar', 'bows, perfume and fists are AR')
    print('selftest ok')
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('weapon', nargs='?')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.weapon:
        ap.error('weapon required')
    r = report(a.weapon)
    if a.json:
        json.dump(r, sys.stdout, indent=1)
        print()
        return 0
    if r is None:
        print(f'{a.weapon}: no starting class can wield it at RL {RANK_RL}')
        return 0
    rank = CARD.rank_text(r['rank'], 'highest') if r['rank'] else 'the only one of its class'
    print(f"{r['weapon']}: {r['kind']} {r['value']:g} ({rank}) {json.dumps({k: v for k, v in r.items() if k in ('affinity', 'class', 'stats', 'spell_buff', 'damage', 'status')})}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
