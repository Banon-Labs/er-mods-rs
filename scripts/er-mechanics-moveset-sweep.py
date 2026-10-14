#!/usr/bin/env python3
"""Per-weapon moveset metrics for every base weapon, both grips, from er-mechanics-attacks.

For each weapon and grip: every attack slot's anim, AtkParam row, motion value, hit count,
first and last hit frame, cancel frames and recovery after the hit (all from
`er-mechanics-attacks.weapon_attacks`, real frames at 30 fps), plus R1-chain metrics: the time
each hit lands when every next R1 comes out on the previous step's earliest R1 cancel frame.

Usage:

    python3 scripts/er-mechanics-moveset-sweep.py OUT.json [name ...]   # no names = all base weapons
"""
import importlib.util
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ATK = _load('er_mechanics_attacks', 'er-mechanics-attacks.py')


def slim(row):
    hw = row.get('hit_windows') or []
    return {
        'anim': row['anim'], 'tae_entry': row['tae_entry'], 'atk_row': row['atk_row'],
        'behavior_row': row['behavior_row'], 'mv': row['mv_phys'],
        'hits': row.get('hits'), 'max_hits': row.get('max_hits'),
        'poise': row['poise_damage'], 'stamina': row['stamina_cost'],
        'first_hit': hw[0][0] if hw else None, 'last_hit': hw[-1][1] if hw else None,
        'cancel': row.get('cancel_frame') or {},
        'recovery_after_hit': row.get('recovery_after_hit') or {},
        'len': row.get('anim_frames'), 'hyperarmor': row.get('hyperarmor') or [],
        'other_hitboxes': row.get('other_hitboxes') or [],
    }


def chain_metrics(slots, prefix):
    keys = [f'{prefix}r1_{i}' for i in range(1, 7)]
    steps = [slots[k] for k in keys if k in slots and slots[k]['first_hit'] is not None]
    if not steps:
        return None
    t, hit_times, mv_total = 0, [], 0
    for i, s in enumerate(steps):
        hit_times.append(t + s['first_hit'])
        mv_total += s['mv'] * (s['hits'] or 1)
        if i + 1 < len(steps):
            t += s['cancel'].get('r1') or s['len']
    n3 = min(3, len(steps))
    mv3 = sum(s['mv'] * (s['hits'] or 1) for s in steps[:n3])
    return {
        'steps': len(steps), 'mvs': [s['mv'] for s in steps],
        'hits_per_step': [s['hits'] for s in steps],
        'first_hits': [s['first_hit'] for s in steps],
        'r1_cancel': [s['cancel'].get('r1') for s in steps],
        'dodge_cancel': [s['cancel'].get('dodge') for s in steps],
        'dodge_after_hit': [s['recovery_after_hit'].get('dodge') for s in steps],
        'move_after_hit': [s['recovery_after_hit'].get('move') for s in steps],
        'hit_times': hit_times, 'hit_gaps': [b - a for a, b in zip(hit_times, hit_times[1:])],
        'mv_total': mv_total, 'chain_frames_to_last_hit': hit_times[-1],
        'mv_per_sec_full_chain': (round(mv_total / (hit_times[-1] / 30.0), 2)
                                  if len(steps) > 1 else None),
        'mv_first3': mv3, 'frames_to_hit3': hit_times[n3 - 1],
        'mv_per_sec_first3': round(mv3 / (hit_times[n3 - 1] / 30.0), 2),
        'stamina_per_step': [s['stamina'] for s in steps],
    }


def weapon_metrics(reg, wid):
    w = reg.weapon[wid]
    out = {'id': wid, 'name': reg.weapon_names.get(wid), 'wepType': w['wepType'],
           'wepmotionCategory': w['wepmotionCategory'], 'spAtkcategory': w['spAtkcategory'],
           'behaviorVariationId': w['behaviorVariationId'], 'grips': {}}
    for grip, prefix in (('one', ''), ('both', '2h_')):
        slots = {r['slot']: slim(r) for r in ATK.weapon_attacks(reg, wid, grip)}
        own = w['spAtkcategory']
        out['grips'][grip] = {
            'slots': slots, 'chain': chain_metrics(slots, prefix),
            'spAtk_override_slots': [k for k, s in slots.items()
                                     if own and s['anim'].startswith(f'a{own:03d}_')],
            'own_behavior_slots': [k for k, s in slots.items()
                                   if s['behavior_row'] // 1000 % 100000
                                   == w['behaviorVariationId']],
        }
    return out


def base_weapons(reg):
    for wid in sorted(reg.weapon):
        name = reg.weapon_names.get(wid)
        if name and not name.startswith('[') and not wid % 10000:
            yield wid


def main():
    out_path, names = sys.argv[1], sys.argv[2:]
    reg = ATK.Regulation(None)
    ids = [reg.find_weapon(n) for n in names] if names else list(base_weapons(reg))
    result, errors, t0 = [], [], time.time()
    for wid in ids:
        try:
            result.append(weapon_metrics(reg, wid))
        except Exception as e:  # keep the sweep going and report what failed
            errors.append({'id': wid, 'name': reg.weapon_names.get(wid), 'error': repr(e)})
    with open(out_path, 'w') as f:
        json.dump({'weapons': result, 'errors': errors, 'seconds': round(time.time() - t0, 1)},
                  f, indent=1)
    print(len(result), 'weapons', len(errors), 'errors', round(time.time() - t0, 1), 's')


if __name__ == '__main__':
    main()
