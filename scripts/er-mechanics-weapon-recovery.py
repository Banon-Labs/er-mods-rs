#!/usr/bin/env python3
"""Per-weapon startup, roll-cancel, recovery, reach, weight and stamina, as JSON on stdout.

Rows come from `er-mechanics-attacks.weapon_attacks` (TAE hit windows and cancel frames, 30 fps
real frames) and `er-mechanics-reach.weapon_reach` (world reach). Progress goes to stderr.

    python3 scripts/er-mechanics-weapon-recovery.py              # curved swords + PvP staples
    python3 scripts/er-mechanics-weapon-recovery.py --all        # every base weapon, R1 chain only
    python3 scripts/er-mechanics-weapon-recovery.py --no-reach   # skip the slower reach pass
"""
import argparse
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


RCH = _load('er_mechanics_reach', 'er-mechanics-reach.py')
ATK = RCH.ATK
PR = ATK.PR

#: Non-curved-sword weapons added to the curved-sword class for the comparison set.
STAPLES = ['Uchigatana', 'Rapier', 'Misericorde', 'Wakizashi']
CURVED_SWORD_WEP_TYPE = 9


def summarise(rows):
    out = {}
    for r in rows:
        hw = r.get('hit_windows')
        if not hw:
            continue
        key = r['slot'].replace('2h_', '')
        cf = r['cancel_frame']
        out[key] = {
            'anim': r['anim'], 'atk_row': r['atk_row'], 'mv': r['mv_phys'],
            'stamina': r['stamina_cost'], 'poise': r['poise_damage'],
            'first_hit': hw[0][0], 'last_hit': hw[-1][1],
            'r1_cancel': cf.get('r1'), 'roll_cancel': cf.get('dodge'), 'move': cf.get('move'),
            'roll_after_hit': r['recovery_after_hit'].get('dodge'),
            'move_after_hit': r['recovery_after_hit'].get('move'),
            'len': r.get('anim_frames'), 'hyperarmor': r.get('hyperarmor'),
        }
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--no-reach', action='store_true')
    a = ap.parse_args()
    no_reach = a.no_reach or a.all
    rc = RCH.Reach(None)
    reg = rc.reg
    wrows, _, _ = PR.rows(PR.param_bytes(PR.load(None), 'EquipParamWeapon'),
                          ['weight', 'properStrength', 'properAgility', 'properMagic',
                           'properFaith', 'properLuck'])
    extra = {r['id']: r for r in wrows}
    names = reg.weapon_names
    base = [i for i in reg.weapon
            if i % 10000 == 0 and names.get(i) and not names[i].startswith('[')]
    if a.all:
        ids = base
    else:
        ids = sorted(i for i in base if reg.weapon[i]['wepType'] == CURVED_SWORD_WEP_TYPE)
        ids += [reg.find_weapon(n) for n in STAPLES]
    res = []
    for wid in ids:
        e, w = extra[wid], reg.weapon[wid]
        item = {'id': wid, 'name': names[wid], 'wepType': w['wepType'],
                'motion': w['wepmotionCategory'], 'spAtk': w['spAtkcategory'],
                'variation': w['behaviorVariationId'], 'weight': round(e['weight'], 2),
                'req': [e['properStrength'], e['properAgility'], e['properMagic'],
                        e['properFaith'], e['properLuck']],
                'stamRate': w['staminaConsumptionRate']}
        for grip in ('one', 'both'):
            try:
                s = summarise(ATK.weapon_attacks(reg, wid, grip))
            except Exception as ex:  # noqa: BLE001 - recorded, sweep continues
                item[grip] = {'error': repr(ex)}
                continue
            if a.all:
                s = {k: v for k, v in s.items() if k.startswith('r1_')}
            if not no_reach:
                try:
                    for rr in RCH.weapon_reach(rc, wid, grip):
                        k = rr['slot'].replace('2h_', '')
                        if k in s:
                            s[k]['world_reach'] = rr.get('world_reach_m')
                except Exception as ex:  # noqa: BLE001
                    item[grip + '_reach_error'] = repr(ex)
            item[grip] = s
        res.append(item)
        print(wid, names[wid], file=sys.stderr, flush=True)
    json.dump(res, sys.stdout, indent=1)
    return 0


if __name__ == '__main__':
    sys.exit(main())
