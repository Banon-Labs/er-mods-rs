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

Candidates are the weapons of the same class (`wepType`). Every candidate is printed with the
share of the moveset it matches and each build rule it breaks, so a near miss says why it is
not a twin rather than disappearing.
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


def twins(reg, wid):
    fields = build_fields(reg)
    w = {**reg.weapon[wid], **fields[wid]}
    mine = moveset(reg, wid)
    rows = []
    for oid, o in sorted(reg.weapon.items()):
        if oid == wid or oid % 10000 or o['wepType'] != w['wepType'] or not reg.weapon_names.get(oid):
            continue
        o = {**o, **fields[oid]}
        theirs = moveset(reg, oid)
        slots = sorted(set(mine) | set(theirs))
        same = sum(1 for s in slots if mine.get(s) is not None and mine.get(s) == theirs.get(s))
        broken = {k: (w[k], o[k], why) for k, why in BUILD_RULES.items() if w[k] != o[k]}
        rows.append({'id': oid, 'name': reg.weapon_names[oid], 'moveset_match': same, 'slots': len(slots),
                     'build_rules_broken': {k: {'this': a, 'other': b, 'meaning': m} for k, (a, b, m) in broken.items()},
                     'twin': same == len(slots) and not broken})
    rows.sort(key=lambda r: (-r['twin'], -r['moveset_match'] / max(r['slots'], 1), len(r['build_rules_broken'])))
    return rows


def selftest():
    reg = ATT.Regulation()
    bhb = twins(reg, reg.find_weapon('Backhand Blade'))
    assert [r['name'] for r in bhb if r['twin']] == ['Reverse-Bladed Sword'], bhb
    curse = next(r for r in bhb if r['name'] == "Curseblade's Cirque")
    assert not curse['twin'] and curse['build_rules_broken'], curse
    lance = twins(reg, reg.find_weapon('Lance'))
    blood = next(r for r in lance if r['name'] == "Bloodfiend's Sacred Spear")
    assert blood['moveset_match'] == blood['slots'], blood
    assert not blood['twin'] and 'disableGemAttr' in blood['build_rules_broken'], blood
    print("selftest ok: Backhand Blade's twin is Reverse-Bladed Sword; Bloodfiend's Sacred Spear shares "
          "Lance's whole moveset but is not its twin (" + ', '.join(blood['build_rules_broken']) + ')')
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
        print(json.dumps({'weapon': wid, 'name': reg.weapon_names.get(wid), 'candidates': rows}, indent=1))
        return 0
    print(f"{reg.weapon_names.get(wid)} ({wid}): twins {[r['name'] for r in rows if r['twin']] or 'none'}")
    for r in rows:
        why = '; '.join(f"{v['meaning']} ({k} {v['this']} vs {v['other']})" for k, v in r['build_rules_broken'].items())
        print(f"  {'TWIN ' if r['twin'] else '     '}{r['name']:<32} moveset {r['moveset_match']}/{r['slots']}  {why}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
