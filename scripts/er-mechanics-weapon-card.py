#!/usr/bin/env python3
"""The numbers the weapon insight page shows under "Speed and cost", from the mechanics tools.

    python3 scripts/er-mechanics-weapon-card.py Lance
    python3 scripts/er-mechanics-weapon-card.py Lance --json
    python3 scripts/er-mechanics-weapon-card.py --selftest

Each value is read from the program that owns it, so the page carries no hand-copied number,
and is ranked against the same slot of every other base weapon (consumables and ammunition
left out): 1st is the fastest first hit, the longest reach, the cheapest stamina, and weapons
with the same value share a place.

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


#: Consumables' virtual weapons (0) and ammunition (81, 83, 85, 86) have no R1 to compare.
NOT_WEAPONS = (0, 81, 83, 85, 86)
#: (value, which way is better). A lower frame and a lower stamina cost are better; a longer
#: reach is better.
FIELDS = {'r1_first_hit_frame': 'low', 'r1_reach_m': 'high',
          'r1_stamina_one_handed': 'low', 'r1_stamina_two_handed': 'low'}


def card(rc, wid):
    one = {a['slot']: a for a in ATT.weapon_attacks(rc.reg, wid, 'one')}
    both = {a['slot']: a for a in ATT.weapon_attacks(rc.reg, wid, 'both')}
    r1, r1_2h = one.get('r1_1'), both.get('2h_r1_1')
    reach = None
    if r1:
        slot = next(s for s in ATT.SLOTS_ONE_HAND if s[0] == 'r1_1')
        r = REACH.attack_reach(rc, wid, slot[0], slot[1], slot[2], slot[3], 'one')
        reach = (r or {}).get('world_reach_m')
    return {'weapon': rc.reg.weapon_names.get(wid), 'id': wid,
            'r1_first_hit_frame': r1['hit_windows'][0][0] if r1 and r1['hit_windows'] else None,
            'r1_reach_m': reach,
            'r1_stamina_one_handed': r1['stamina_cost'] if r1 else None,
            'r1_stamina_two_handed': r1_2h['stamina_cost'] if r1_2h else None}


def base_weapons(rc):
    out = []
    for wid, w in rc.reg.weapon.items():
        nm = rc.reg.weapon_names.get(wid)
        if wid % 10000 == 0 and nm and not nm.startswith('[') and w.get('wepType') not in NOT_WEAPONS:
            out.append(wid)
    return sorted(out)


def population(rc):
    """{field: [value of every base weapon that has it]}: the same slot of every other weapon."""
    out = {f: [] for f in FIELDS}
    for wid in base_weapons(rc):
        try:
            c = card(rc, wid)
        except Exception:                               # weapon rows the attack module cannot read
            continue
        for f in FIELDS:
            if c[f] is not None:
                out[f].append(c[f])
    return out


def rank(values, v, better):
    """(place, tied, of): competition rank, 1 = best; `tied` counts the others with the same value."""
    key = round(v, 3)
    vals = [round(x, 3) for x in values]
    ahead = sum(1 for x in vals if (x < key if better == 'low' else x > key))
    return ahead + 1, sum(1 for x in vals if x == key) - 1, len(vals)


def ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def rank_text(r, word):
    place, tied, of = r
    return f"{'tied ' if tied else ''}{ordinal(place)} {word} of {of}"


WORD = {'r1_first_hit_frame': 'fastest', 'r1_reach_m': 'longest',
        'r1_stamina_one_handed': 'cheapest', 'r1_stamina_two_handed': 'cheapest'}


def speed_and_cost(weapon, rc=None, pop=None):
    rc = rc or REACH.Reach()
    wid = rc.reg.find_weapon(weapon)
    c = card(rc, wid)
    pop = pop or population(rc)
    c['rank'] = {f: rank(pop[f], c[f], better) for f, better in FIELDS.items() if c[f] is not None}
    c['rank_text'] = {f: rank_text(r, WORD[f]) for f, r in c['rank'].items()}
    # A value the attack or reach module could not read (no R1 hit, no reach trace) is None and
    # has no rank; its clause is left out rather than printed as a number it does not have.
    rt = c['rank_text']
    speed = cost = None
    if c['r1_first_hit_frame'] is not None:
        speed = f"R1 hits on frame {c['r1_first_hit_frame']:g} ({rt['r1_first_hit_frame']})"
        if c['r1_reach_m'] is not None:
            speed += f" and reaches {c['r1_reach_m']:.1f} m ({rt['r1_reach_m']})"
        speed += '.'
    if c['r1_stamina_one_handed'] is not None:
        cost = f"Stamina per R1 one-handed ({rt['r1_stamina_one_handed']})"
        if c['r1_stamina_two_handed'] is not None:
            cost += f", {c['r1_stamina_two_handed']} two-handed ({rt['r1_stamina_two_handed']})"
        cost += '.'
    c['text'] = {'speed': speed, 'cost': cost}
    return c


def selftest():
    rc = REACH.Reach()
    pop = population(rc)
    c = speed_and_cost('Lance', rc, pop)
    assert c['r1_first_hit_frame'] == 18, c
    assert round(c['r1_reach_m'], 1) == 4.5, c
    assert (c['r1_stamina_one_handed'], c['r1_stamina_two_handed']) == (19, 22), c
    assert rank([10, 12, 12, 18], 12, 'low') == (2, 1, 4) and rank([1.0, 2.0], 2.0, 'high') == (1, 0, 2)
    for f, (place, tied, of) in c['rank'].items():
        assert 1 <= place <= of and of > 300, (f, c['rank'][f])
    print(f"selftest ok: Lance {c['text']}")
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
    print(f"{c['weapon']}: {c['text']['speed']} {c['r1_stamina_one_handed']} {c['text']['cost']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
