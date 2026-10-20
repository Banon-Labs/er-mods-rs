"""Rank one weapon's infusability, ash mountability and moveset coverage against every player weapon.

Read-only. Prints JSON on stdout.

    python3 scripts/er-mechanics-ash-mount-rank.py Shamshir [rows-sweep.json]

- Ash counts use `AshTables.can_mount` (`CanMountGemWithAffinityOnWeapon` 0x140d549d0) over every
  ash-of-war item (`AshTables.ash_gems`) at the weapon's maximum reinforcement level, per affinity.
- Affinities are the EquipParamWeapon rows `id + 100*affinity` that exist.
- Moveset coverage is `er-mechanics-attacks.weapon_attacks` per grip over every weapon of the same
  wepType, plus the powerstance (034xxx) entries in the weapon's motion-category TAE.
- With a rows-sweep JSON (er-mechanics-ash-table output rows), the weapon's best ash rows are ranked
  against the other weapons in that sweep.
"""
import collections
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ASH = _load('er_mechanics_ashes', 'er-mechanics-ashes.py')
ATK = ASH.ATK
PR = ATK.PR

AFFINITIES = {0: 'Standard', 1: 'Heavy', 2: 'Keen', 3: 'Quality', 4: 'Fire', 5: 'Flame Art',
              6: 'Lightning', 7: 'Sacred', 8: 'Magic', 9: 'Cold', 10: 'Poison', 11: 'Blood',
              12: 'Occult'}
MELEE_TYPES = {1, 3, 5, 7, 9, 11, 13, 14, 15, 16, 17, 19, 21, 23, 24, 25, 27, 28, 29, 31, 35, 37,
               39, 41, 88, 89, 91, 92, 93, 94, 95}
POWERSTANCE_RANGE = (34000, 34999)


def max_level(t, w):
    lv = 0
    for level in range(0, 26):
        if w['reinforceTypeId'] + level in t.reinforce_gem:
            lv = level
    return lv


def rank(per_weapon, key, pool, target):
    vals = sorted((per_weapon[i][key] for i in pool), reverse=True)
    v = per_weapon[target][key]
    return dict(value=v, rank=sum(1 for x in vals if x > v) + 1,
                tied=sum(1 for x in vals if x == v), of=len(vals), max=vals[0],
                median=vals[len(vals) // 2])


def main():
    key = sys.argv[1] if len(sys.argv) > 1 else 'Shamshir'
    sweep_path = sys.argv[2] if len(sys.argv) > 2 else None
    t = ASH.AshTables(None)
    rows, _, _ = PR.rows(PR.param_bytes(PR.load(None), 'EquipParamWeapon'), None)
    W = {r['id']: r for r in rows}
    names = t.reg.weapon_names
    target = t.find_weapon(key)
    gems = t.ash_gems()
    out = {'weapon': names[target], 'id': target, 'ash_items_total': len(gems)}

    # Smithbox names enemy-only copies "[NPC] ..."; those are not player equipment.
    base = [i for i, r in W.items() if i % 10000 == 0 and names.get(i)
            and not names[i].startswith('[') and r['wepType'] in ASH.WEP_TYPE_MOUNT_FLAG]
    per = {}
    for wid in base:
        w = W[wid]
        lv = max_level(t, w)
        affs = [a for a in AFFINITIES if wid + a * 100 in W]
        union, std = set(), 0
        for a in affs:
            ok = {sid for sid, g in gems.items() if t.can_mount(wid, g, a, lv)[0]}
            union |= ok
            if a == 0:
                std = len(ok)
        per[wid] = dict(name=names[wid], wepType=w['wepType'], gemMountType=w['gemMountType'],
                        isCustom=w.get('isCustom'), max_level=lv, affinities=len(affs),
                        ashes_standard=std, ashes_any_affinity=len(union),
                        default_skill=t.arts_name(w['swordArtsParamId']),
                        default_skill_id=w['swordArtsParamId'], weight=w['weight'],
                        req=[w['properStrength'], w['properAgility'], w['properMagic'],
                             w['properFaith'], w['properLuck']])

    out['ash_items_by_weptype_flag'] = {
        f'{wt} {flag}': sum(1 for g in gems.values() if t.gem[g][f'canMountWep_{flag}'])
        for wt, flag in ASH.WEP_TYPE_MOUNT_FLAG.items()}
    melee = [i for i in base if per[i]['wepType'] in MELEE_TYPES]
    same = [i for i in base if per[i]['wepType'] == per[target]['wepType']]
    out['target'] = per[target]
    out['melee_total'] = len(melee)
    out['melee_infusable'] = sum(1 for i in melee if per[i]['affinities'] > 1)
    out['melee_no_ash'] = sum(1 for i in melee if per[i]['ashes_any_affinity'] == 0)
    out['melee_gemMountType'] = dict(collections.Counter(per[i]['gemMountType'] for i in melee))
    fields = ('ashes_standard', 'ashes_any_affinity', 'affinities')
    out['rank_melee'] = {f: rank(per, f, melee, target) for f in fields}
    out['rank_same_type'] = {f: rank(per, f, same, target) for f in fields}
    out['rank_melee_infusable_only'] = {
        f: rank(per, f, [i for i in melee if per[i]['affinities'] > 1], target) for f in fields}
    reqsum = {i: sum(per[i]['req']) for i in melee}
    v = reqsum[target]
    out['req_sum'] = dict(value=v, strictly_lower=sum(1 for x in reqsum.values() if x < v),
                          tied=sum(1 for x in reqsum.values() if x == v), of=len(reqsum),
                          median=sorted(reqsum.values())[len(reqsum) // 2])
    out['same_type'] = sorted(({**per[i], 'id': i} for i in same),
                              key=lambda r: (-r['ashes_any_affinity'], r['name']))

    moveset = {}
    for wid in same:
        w = t.reg.weapon[wid]
        one = [r['slot'] for r in ATK.weapon_attacks(t.reg, wid, 'one')]
        both = [r['slot'] for r in ATK.weapon_attacks(t.reg, wid, 'both')]
        cat = w['wepmotionCategory']
        ps = sorted(k for k in (ATK.tae_animations(cat) or {}) if POWERSTANCE_RANGE[0] <= k <= POWERSTANCE_RANGE[1])
        moveset[names[wid]] = dict(
            one=len(one), both=len(both),
            missing_one=[s for s, *_ in ATK.SLOTS_ONE_HAND if s not in one],
            missing_both=[s for s, *_ in ATK.SLOTS_ONE_HAND if '2h_' + s not in both],
            spAtkcategory=w.get('spAtkcategory'), wepmotionCategory=cat,
            powerstance_entries=len(ps), isDualBlade=W[wid].get('isDualBlade'))
    out['slots_defined'] = len(ATK.SLOTS_ONE_HAND)
    out['moveset_same_type'] = moveset
    sp = t.reg.weapon[target].get('spAtkcategory')
    out['powerstance_entries_target_cat'] = sorted(
        k for k in (ATK.tae_animations(t.reg.weapon[target]['wepmotionCategory']) or {})
        if POWERSTANCE_RANGE[0] <= k <= POWERSTANCE_RANGE[1])
    out['spAtkcategory_entries'] = sorted((ATK.tae_animations(sp) or {}).keys()) if sp else []

    if sweep_path:
        name = names[target]
        srows = json.load(open(sweep_path))
        best = {}
        for r in srows:
            if r['weapon'] not in best or r['score'] > best[r['weapon']]['score']:
                best[r['weapon']] = r
        order = sorted(best.values(), key=lambda r: -r['score'])
        bestdps = {}
        for r in srows:
            bestdps[r['weapon']] = max(bestdps.get(r['weapon'], 0), r['dps'])
        od = sorted(bestdps.items(), key=lambda kv: -kv[1])
        out['sweep'] = dict(
            weapons=len(best),
            rank_best_score=[i + 1 for i, r in enumerate(order) if r['weapon'] == name][0],
            rank_best_dps=[i + 1 for i, kv in enumerate(od) if kv[0] == name][0],
            median_best_score=sorted(r['score'] for r in best.values())[len(best) // 2],
            top10=[(r['weapon'], r['affinity'], r['grip'], r['ash'], r['score'], r['dps'])
                   for r in order[:10]],
            best_row=best[name])
        mine = sorted((r for r in srows if r['weapon'] == name), key=lambda r: -r['score'])
        out['sweep']['top_rows'] = [(r['ash'], r['affinity'], r['grip'], r['cat'], r['score'],
                                     r['dps'], r['first'], r['last']) for r in mine[:10]]
        per_ash = collections.defaultdict(dict)
        for r in srows:
            d = per_ash[r['ash']]
            d[r['weapon']] = max(d.get(r['weapon'], -1e9), r['score'])
        cmp = []
        for ash in {r['ash'] for r in mine}:
            o = sorted(per_ash[ash].items(), key=lambda kv: -kv[1])
            pos = [i + 1 for i, kv in enumerate(o) if kv[0] == name][0]
            cmp.append(dict(ash=ash, score=per_ash[ash][name], rank=pos, of=len(o), best=o[0]))
        out['sweep']['per_ash'] = sorted(cmp, key=lambda c: -c['score'])
    json.dump(out, sys.stdout, indent=1, default=str)


if __name__ == '__main__':
    main()
