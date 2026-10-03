#!/usr/bin/env python3
"""Which weapons are another weapon's twin: the same moveset and the same build rules.

    python3 scripts/er-mechanics-weapon-twins.py "Backhand Blade"
    python3 scripts/er-mechanics-weapon-twins.py Lance --json
    python3 scripts/er-mechanics-weapon-twins.py --selftest

A twin is a weapon a player could swap in without changing how they build or how they play:

* moveset: every attack slot, one- and two-handed, has the same animation, physical motion
  value, hit windows, hit count and extra hitboxes (`er-mechanics-attacks.py weapon_attacks`);
* build rules, from `EquipParamWeapon`: it takes ashes of war the same way (`gemMountType`), it
  can be infused the same way (`disableGemAttr`), and it upgrades the same way
  (`reinforceTypeId`: smithing against somber stones).

Only comparable weapons are considered: the same class (`wepType`) and the same build rules. A
weapon that breaks a build rule is not compared at all, however much of the moveset it shares:
Bloodfiend's Sacred Spear swings exactly like Lance but cannot be infused, so a player choosing
Lance never weighs it. Each comparable weapon is printed with the share of the moveset it
matches; the ones that match all of it are twins.
"""
import argparse
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_s = importlib.util.spec_from_file_location('er_mechanics_attacks', os.path.join(HERE, 'er-mechanics-attacks.py'))
ATT = importlib.util.module_from_spec(_s)
sys.modules['er_mechanics_attacks'] = ATT
_s.loader.exec_module(ATT)

# EquipParamWeapon fields a twin must share, with what a difference means to a player.
BUILD_RULES = {
    'gemMountType': 'takes ashes of war differently',
    'disableGemAttr': 'can be infused differently',
    'reinforceTypeId': 'upgrades differently (smithing / somber stones)',
}


def moveset(reg, wid):
    out = {}
    for grip in ('one', 'both'):
        for a in ATT.weapon_attacks(reg, wid, grip):
            extra = [(o['frames'], o['mv_phys']) for o in a.get('other_hitboxes', [])]
            out[a['slot']] = (a['anim'], a['mv_phys'], json.dumps(a['hit_windows']), a['hits'], json.dumps(extra))
    return out


def build_fields(reg):
    """`BUILD_RULES` fields of every weapon row; the attack module's table does not carry them."""
    if not hasattr(reg, '_twin_fields'):
        rows = ATT.PR.rows(ATT.PR.param_bytes(ATT.PR.load(), 'EquipParamWeapon'), list(BUILD_RULES), strict=False)[0]
        reg._twin_fields = {r['id']: r for r in rows}
    return reg._twin_fields


def comparable(reg, wid, oid):
    """Same class and every `BUILD_RULES` field equal: a weapon a player choosing `wid` weighs."""
    fields = build_fields(reg)
    return (reg.weapon[oid]['wepType'] == reg.weapon[wid]['wepType']
            and all(fields[oid][k] == fields[wid][k] for k in BUILD_RULES))


def twins(reg, wid):
    """Every comparable weapon with its moveset match, twins (a full match) first."""
    mine = moveset(reg, wid)
    rows = []
    for oid in sorted(reg.weapon):
        # A bracketed name ("[NPC] Reduvia") is a row no player can hold, so no player weighs it.
        name = reg.weapon_names.get(oid)
        if oid == wid or oid % 10000 or not name or name.startswith('[') or not comparable(reg, wid, oid):
            continue
        theirs = moveset(reg, oid)
        slots = sorted(set(mine) | set(theirs))
        same = sum(1 for s in slots if mine.get(s) is not None and mine.get(s) == theirs.get(s))
        rows.append({'id': oid, 'name': reg.weapon_names[oid], 'moveset_match': same, 'slots': len(slots),
                     'twin': same == len(slots)})
    rows.sort(key=lambda r: (-r['twin'], -r['moveset_match'] / max(r['slots'], 1)))
    return rows


# Where this weapon is better than a comparable one, field by field. Lower is better for a
# requirement, the weight and the stamina multiplier; higher is better for a scaling rate.
# Base attack is left out: it is the +0 value before scaling and upgrades, which no player sees.
STAT_LABEL = {'Strength': 'STR', 'Agility': 'DEX', 'Magic': 'INT', 'Faith': 'FTH', 'Luck': 'ARC'}
LOWER_BETTER = {'weight': 'weight', 'staminaConsumptionRate': 'stamina use x',
                **{f'proper{k}': f'{v} requirement' for k, v in STAT_LABEL.items()}}
HIGHER_BETTER = {f'correct{k}': f'{v} scaling' for k, v in STAT_LABEL.items()}


def advantages(reg, wid, oid):
    """What `wid` has over comparable `oid`: lower costs, more scaling, and attacks that hit
    sooner or harder in a slot whose hit structure is the same. A slot whose hits differ in
    number is left out: neither side is better there by a single number."""
    rows = {r['id']: r for r in ATT.PR.rows(ATT.PR.param_bytes(ATT.PR.load(), 'EquipParamWeapon'),
                                            list(LOWER_BETTER) + list(HIGHER_BETTER), strict=False)[0]
            if r['id'] in (wid, oid)}
    me, other = rows[wid], rows[oid]
    out = []
    for f, label in LOWER_BETTER.items():
        if me[f] < other[f]:
            out.append({'what': label, 'this': round(me[f], 3), 'other': round(other[f], 3)})
    for f, label in HIGHER_BETTER.items():
        if me[f] > other[f]:
            out.append({'what': label, 'this': me[f], 'other': other[f]})
    for grip in ('one', 'both'):
        mine = {a['slot']: a for a in ATT.weapon_attacks(reg, wid, grip)}
        theirs = {a['slot']: a for a in ATT.weapon_attacks(reg, oid, grip)}
        for slot, a in mine.items():
            b = theirs.get(slot)
            if not b or a['hits'] != b['hits'] or a.get('other_hitboxes') or b.get('other_hitboxes'):
                continue
            if a['hit_windows'] and b['hit_windows'] and a['hit_windows'][0][0] < b['hit_windows'][0][0]:
                out.append({'what': f'{a["label"]} first hit frame', 'this': a['hit_windows'][0][0],
                            'other': b['hit_windows'][0][0]})
            if a['mv_phys'] > b['mv_phys']:
                out.append({'what': f'{a["label"]} motion value', 'this': a['mv_phys'], 'other': b['mv_phys']})
    return out


def selftest():
    reg = ATT.Regulation()
    bhb = twins(reg, reg.find_weapon('Backhand Blade'))
    assert [r['name'] for r in bhb if r['twin']] == ['Reverse-Bladed Sword'], bhb
    assert "Curseblade's Cirque" not in [r['name'] for r in bhb], bhb      # somber, no ashes
    lance = twins(reg, reg.find_weapon('Lance'))
    names = [r['name'] for r in lance]
    # Same 30 attacks as Lance, but neither can be infused: not comparable, so not listed.
    assert "Bloodfiend's Sacred Spear" not in names and "Mohgwyn's Sacred Spear" not in names, names
    assert not any(r['twin'] for r in lance), lance
    lw, mw = reg.find_weapon('Lance'), reg.find_weapon("Messmer Soldier's Spear")
    assert {'what': 'DEX requirement', 'this': 14, 'other': 16} in advantages(reg, lw, mw)
    assert any(a['what'] == 'R2 #1 first hit frame' for a in advantages(reg, mw, lw))
    assert not any(a['what'].startswith('R2 #1') for a in advantages(reg, lw, mw))
    print(f"selftest ok: Backhand Blade's twin is Reverse-Bladed Sword; Lance has no twin, comparable {names}")
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
    reg = ATT.Regulation()
    wid = reg.find_weapon(a.weapon)
    rows = twins(reg, wid)
    if a.json:
        for r in rows:
            r['advantages'] = advantages(reg, wid, r['id'])
        print(json.dumps({'weapon': wid, 'name': reg.weapon_names.get(wid), 'comparable': rows}, indent=1))
        return 0
    print(f"{reg.weapon_names.get(wid)} ({wid}): twins {[r['name'] for r in rows if r['twin']] or 'none'}")
    for r in rows:
        print(f"  {'TWIN ' if r['twin'] else '     '}{r['name']:<32} moveset {r['moveset_match']}/{r['slots']}")
        for adv in advantages(reg, wid, r['id']):
            print(f"        better: {adv['what']} {adv['this']} vs {adv['other']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
