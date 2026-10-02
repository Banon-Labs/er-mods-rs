#!/usr/bin/env python3
"""The numbers the weapon insight page shows under "Speed and cost", from the mechanics tools.

    python3 scripts/er-mechanics-weapon-card.py Lance
    python3 scripts/er-mechanics-weapon-card.py Lance --json
    python3 scripts/er-mechanics-weapon-card.py --selftest

Each value is read from the program that owns it, so the page carries no hand-copied number:

| value | source |
| --- | --- |
| R1 first hit, real frames at 30 fps | `er-mechanics-attacks.weapon_attacks`, slot `r1_1`, `hit_windows[0][0]` |
| R1 reach, metres forward of where the attack started | `er-mechanics-reach.weapon_reach`, slot `r1_1`, `world_reach_m` |
| stamina per R1, one- and two-handed | `er-mechanics-attacks.weapon_attacks` `stamina_cost` of `r1_1` / `2h_r1_1` |
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


ATT = _mod('er_mechanics_attacks', 'er-mechanics-attacks.py')
REACH = _mod('er_mechanics_reach', 'er-mechanics-reach.py')


def speed_and_cost(weapon):
    rc = REACH.Reach()
    wid = rc.reg.find_weapon(weapon)
    one = {a['slot']: a for a in ATT.weapon_attacks(rc.reg, wid, 'one')}
    both = {a['slot']: a for a in ATT.weapon_attacks(rc.reg, wid, 'both')}
    reach = {r['slot']: r for r in REACH.weapon_reach(rc, wid, 'one')}
    r1, r1_2h = one.get('r1_1'), both.get('2h_r1_1')
    return {'weapon': rc.reg.weapon_names.get(wid), 'id': wid,
            'r1_first_hit_frame': r1['hit_windows'][0][0] if r1 and r1['hit_windows'] else None,
            'r1_reach_m': (reach.get('r1_1') or {}).get('world_reach_m'),
            'r1_stamina_one_handed': r1['stamina_cost'] if r1 else None,
            'r1_stamina_two_handed': r1_2h['stamina_cost'] if r1_2h else None}


def selftest():
    c = speed_and_cost('Lance')
    assert c['r1_first_hit_frame'] == 18, c
    assert round(c['r1_reach_m'], 1) == 4.5, c
    assert (c['r1_stamina_one_handed'], c['r1_stamina_two_handed']) == (19, 22), c
    print(f'selftest ok: {c}')
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
    c = speed_and_cost(a.weapon)
    if a.json:
        json.dump(c, sys.stdout, indent=1)
        return 0
    print(f"{c['weapon']}: R1 hits on frame {c['r1_first_hit_frame']:g} and reaches {c['r1_reach_m']:.1f} m; "
          f"{c['r1_stamina_one_handed']} stamina per R1 one-handed, {c['r1_stamina_two_handed']} two-handed")
    return 0


if __name__ == '__main__':
    sys.exit(main())
