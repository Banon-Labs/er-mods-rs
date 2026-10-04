#!/usr/bin/env python3
"""Field-by-field diff of lingering bullets that do and do not carry a weapon buff, offline.

    python3 scripts/interactions/carrier_diff.py            # the measured set
    python3 scripts/interactions/carrier_diff.py --all      # every differing field, not just the short list

For each case it prints how the bullet is launched (the row the TimeAct, weapon or spell names),
the hit-context byte that launch gives it, the Bullet and AtkParam_Pc fields that differ across
the set, and the gate verdict for Blood Grease 3190, Soporific Grease 3150 and Seppuku 1755.

The context byte comes from `FUN_14038e210` (1.16.2, shift 0): `BehaviorParam.category` when the
spawn has no explicit bullet id, else `EquipParamGoods.spEffectCategory`, else
`Magic.spEffectCategory`, else 0. A spell launches its bullet by id (`Magic.refCategory` 1), so
it takes the magic branch, or 0 if the spawn carried no magic id; both refuse every
`wepParamChange` 1 row (`regdata.accepts`).

Cases and their in-game status (the user's observations, 2026-10-04):

    piquebone    20003309  Piquebone smoke, any arrow row      carries the grease
    mist_aow     2416      Poisonous Mist ash cloud             poisons; its own bullet SpEffect 834
    eruption     2019      Eruption puddles                     does not carry the grease
    poison_mist  10722001  Poison Mist incantation cloud        does not carry the grease

The second gate, `regdata.status_reaches`, is printed as the status scale row.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import regdata  # noqa: E402

CASES = [
    # name, bullet, launch kind, launch id, status
    ('piquebone', 20003309, 'behavior', 105040851, 'carries (USER)'),
    ('rancor', 2021, 'behavior', 301302901, 'carries (USER)'),
    ('mist_aow', 2416, 'behavior', 300000162, 'own 834 (USER)'),
    ('eruption', 2019, 'behavior', 300000042, 'does not (USER)'),
    ('poison_mist', 10722001, 'magic', 7220, 'does not (USER)'),
]

BULLET_SHORT = ('life', 'hitRadius', 'hitRadiusMax', 'initVellocity', 'dmgHitRecordLifeTime',
                'isEndlessHit', 'isUseSharedHitList', 'isHitBothTeam', 'isPenetrateChr',
                'launchConditionType', 'FollowType', 'atkAttribute', 'spAttribute',
                'createLimitGroupId', 'atkId_Bullet', 'spEffectId0', 'spEffectIDForShooter',
                'dmgCalcSide')
ATK_SHORT = ('atkPhys', 'atkFire', 'atkPhysCorrection', 'atkFireCorrection', 'opposeTarget',
             'friendlyTarget', 'selfTarget', 'atkAttribute', 'spAttribute', 'atkType',
             'isArrowAtk', 'disableHitSpEffect', 'isDisableNoDamage', 'subCategory1',
             'subCategory2', 'subCategory3', 'subCategory4', 'statusAilmentAtkPowerCorrectRate',
             'statusAilmentAtkPowerCorrectRate_byPoint', 'spEffectAtkPowerCorrectRate_byRate',
             'spEffectAtkPowerCorrectRate_byPoint', 'spEffectAtkPowerCorrectRate_byDmg',
             'spEffectId0', 'isDisableBothHandsAtkBonus', 'throwFlag')
BUFFS = (3190, 3150, 1755)


def launch_ctx(reg, kind, lid):
    if kind == 'behavior':
        b = reg.beh[lid]
        return b['category'], f"BehaviorParam_PC {lid} category {b['category']} -> bullet {b['refId']}"
    m = reg.magic[lid]
    roots = [m[f'refId{i}'] for i in range(1, 11) if m[f'refCategory{i}'] == 1 and m[f'refId{i}'] > 0]
    return m['spEffectCategory'], (f"Magic {lid} spEffectCategory {m['spEffectCategory']}, "
                                   f"bullet by id {roots} (no behavior row; 0 if the spawn has no magic id)")


def chain_to(reg, root, target):
    for n in reg.chain(root):
        if n['id'] == target:
            return n['path']
    return None


def rows(reg, show_all):
    out = []
    for name, bid, kind, lid, status in CASES:
        ctx, how = launch_ctx(reg, kind, lid)
        root = reg.beh[lid]['refId'] if kind == 'behavior' else [
            reg.magic[lid][f'refId{i}'] for i in range(1, 11) if reg.magic[lid][f'refCategory{i}'] == 1][0]
        b = reg.bullet[bid]
        a = reg.atk.get(b['atkId_Bullet'])
        verdict = {s: regdata.gate(ctx, reg.sp[s], a) for s in BUFFS}
        if kind == 'magic':
            verdict0 = {s: regdata.gate(0, reg.sp[s], a) for s in BUFFS}
            verdict = {s: verdict[s] or verdict0[s] for s in BUFFS}
        reach = {s: regdata.status_reaches(reg.sp[reg.sp[s]['atkOccurrenceSpEffectId']], a)
                 for s in BUFFS}
        out.append({'name': name, 'bid': bid, 'how': how, 'ctx': ctx, 'status': status,
                    'scale': regdata.status_scale(a), 'reach': reach,
                    'path': chain_to(reg, root, bid), 'bullet': b, 'atk': a, 'verdict': verdict})
    bkeys = [k for k in reg.bullet[bid].keys() if k not in ('id', '_off')] if show_all else BULLET_SHORT
    akeys = [k for k in out[0]['atk'].keys() if k not in ('id', '_off')] if show_all else ATK_SHORT
    if show_all:
        bkeys = [k for k in bkeys if len({repr(r['bullet'][k]) for r in out}) > 1]
        akeys = [k for k in akeys if len({repr(r['atk'][k]) for r in out}) > 1]
    return out, bkeys, akeys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--all', action='store_true')
    o = ap.parse_args()
    reg = regdata.Reg()
    out, bkeys, akeys = rows(reg, o.all)
    names = [r['name'] for r in out]
    w = 14
    print('field'.ljust(44) + ''.join(n.ljust(w) for n in names))

    def line(label, vals):
        print(label[:43].ljust(44) + ''.join(str(v)[:w - 1].ljust(w) for v in vals))
    line('in game', [r['status'] for r in out])
    line('bullet', [r['bid'] for r in out])
    line('hit context byte (ADI+0xda)', [r['ctx'] for r in out])
    for s in BUFFS:
        line(f'gate 1 (context) {s} {reg.name("SpEffectParam", s)[7:27]}',
             ['yes' if r['verdict'][s] else 'no' for r in out])
    line('gate 2 status scale (ADI+0x140 x +0x13c)', [round(r['scale'], 3) for r in out])
    for s in BUFFS:
        line(f'grease status reaches enemy, {s}',
             ['yes' if r['verdict'][s] and r['reach'][s] else 'no' for r in out])
    for k in bkeys:
        line(f'Bullet.{k}', [round(r['bullet'][k], 3) if isinstance(r['bullet'][k], float)
                             else r['bullet'][k] for r in out])
    for k in akeys:
        line(f'AtkParam_Pc.{k}', [r['atk'][k] for r in out])
    print()
    for r in out:
        print(f"{r['name']}: {r['how']}; chain {r['path']}")


if __name__ == '__main__':
    main()
