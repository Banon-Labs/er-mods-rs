#!/usr/bin/env python3
"""The "chainsaw glitch" class: every skill that keeps attacking while L2 is held, the weapons
that carry each one, and what the behavior script checks about the weapon in the swinging hand.

    python3 scripts/er-mechanics-chainsaw-class.py
    python3 scripts/er-mechanics-chainsaw-class.py --json
    python3 scripts/er-mechanics-chainsaw-class.py --selftest

Written for docs/er-mechanics/chainsaw/affected-class.md; the labels mean what they mean there.

The rule, read out of the player behavior script `c0000.hks` (`er-hks-disasm.py`):

* `ExecArtsStance` starts every stance skill. For the ids `IsAttackStanceArts` lists it requires
  FP above 0 (`env(1001)`) and L2 held (`env(1106, ACTION_ARM_L2)`), then plays
  `W_DrawStanceRightStart`.
* The behavior graph `c0000.behbnd` plays clip 040050 (040055 without FP) in that state, 040051
  (040056) in `W_DrawStanceRightLoop` and 040053/040054/040058 in `W_DrawStanceRightEnd`
  (`er-behbnd-attack-map.py`).
* `DrawStanceRightLoop_Upper_onUpdate` ends the loop only when L2 is released
  (`env(1108, ACTION_ARM_L2) <= 0` or `env(1107, ACTION_ARM_L2)`), or when FP runs out and
  `c_SwordArtsID` is 25 or 239. Nothing in it asks which weapon is in the hand except whether
  it is a bow or crossbow.
* `c_SwordArtsID` is `SwordArtsParam.swordArtsTypeNew`: `SwordArtsOneShot_onUpdate` passes
  `c_SwordArtsID + 600` to `env(1114, ..)` as the TimeAct category, and the skill TimeAct is
  `a<600 + swordArtsTypeNew>.tae` (`er-mechanics-ashes.py`).

So a skill repeats while held when its swordArtsTypeNew is in `IsAttackStanceArts` and its loop
clip 040051 carries a hitbox. The other stance skills (Square Off, Unsheathe, the bow skills)
share the start/loop/end states but their loop clip holds a pose and hits nothing.
"""

import argparse
import collections
import importlib.util
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ASH = _load('er_mechanics_ashes', 'er-mechanics-ashes.py')
HKS = _load('er_hks_disasm', 'er-hks-disasm.py')

#: Stance clips (`c0000.behbnd`, `er-behbnd-attack-map.py`): start, loop, end, each with FP.
CLIP_START, CLIP_LOOP, CLIP_END = 40050, 40051, 40053
#: The no-FP loop copy (`W_DrawStanceRightLoop` plays 040051 and 040056).
CLIP_LOOP_NO_FP = 40056
#: HKS predicates over `c_SwordArtsID` (= swordArtsTypeNew).
ATTACK_STANCE_FN = 'IsAttackStanceArts'
STANCE_FN = 'IsStanceArts'
#: The weapon categories the loop update diverts away from the attack loop
#: (`DrawStanceRightLoop_Upper_onUpdate` lines 11510 and 11520, `GetEquipType` = `env(225, hand)`
#: compared against these `WEAPON_CATEGORY_*` globals), as wepmotionCategory values
#: (`er-hks-weapon-category-table.py`, common_define.hks).
DIVERTED_CATEGORIES = {44: 'ARROW (bow)', 45: 'LARGE_ARROW (greatbow)', 51: 'SMALL_ARROW (light bow)',
                       46: 'CROSSBOW'}
#: Skills the tutorial names, for the selftest: SwordArtsParam id -> carrier weapon name.
NAMED = {110: None, 125: 'Flail', 1039: "Ghiza's Wheel", 5090: 'Dancing Blade of Ranah'}
#: Non-repeating skills for the selftest: Lion's Claw (one shot), Quickstep (step),
#: Square Off (a stance whose loop holds a pose), Starcaller Cry (the target's own skill).
NEGATIVES = (100, 800, 115, 1032)


def hks_eq_constants(fn_name, path=HKS.DEFAULT_FILE):
    """The numeric constants a predicate compares its argument against (`EQ` with a constant
    operand), in program order. `IsAttackStanceArts` is a chain of `EQ R0, K` that returns `TRUE`
    on any match, so this is its member list."""
    _data, _end, _hdr, main = HKS.parse_file(path)
    HKS.name_functions(main)
    for f in HKS.walk(main):
        if f.name == fn_name:
            out = []
            for ins in f.instrs:
                if ins.name in ('EQ', 'EQ_BK') and ins.c & 0x100:
                    v = f.consts[ins.c & 0xFF]
                    if isinstance(v, (int, float)):
                        out.append(int(v))
            return out
    raise SystemExit(f'{fn_name} not found in {path}')


def clip_hits(t, sword_arts_id, weapon_id, clip):
    """What a skill clip hits with on this weapon: [(event, frames, judge, behavior row, atk row,
    kind)] for every attack/bullet event, resolved through the weapon's behaviorVariationId the
    way `PlayerIns::ResolveBehaviorId` 0x140652280 does (`VERIFIED` in er-mechanics-attacks)."""
    tae = ASH.skill_tae(t, sword_arts_id) or {}
    if clip not in tae:
        return None
    out = []
    for x in ASH.anim_actions(t, weapon_id, tae[clip]):
        if x['event'] in ('attack', 'bullet') and x.get('kind') in ('melee', 'bullet'):
            out.append({'event': x['event'], 'frames': x['frames'], 'judge': x['judge'],
                        'behavior_row': x['behavior_row'], 'atk_row': x.get('atk_row'),
                        'mv_phys': (x.get('mv') or {}).get('physical')})
    return out


def max_level(t, weapon_id):
    base = t.reg.weapon[weapon_id]['reinforceTypeId']
    return max(lv for lv in range(26) if base + lv in t.reinforce_gem)


def player_weapons(t):
    """Base (Standard, +0 id) player weapons: id a multiple of 10000, a name without the `[NPC]`
    style prefix."""
    out = []
    for wid, name in sorted(t.reg.weapon_names.items()):
        if wid in t.reg.weapon and wid % 10000 == 0 and name and not name.startswith('['):
            out.append(wid)
    return out


def carriers(t, sword_arts_id, weapons):
    """{'innate': [...], 'ash': {...}} for one skill.

    innate: weapons whose EquipParamWeapon.swordArtsParamId is the skill, with gemMountType
    (0 = no ash slot, the skill is locked; 2 = an ash can replace it).
    ash: the ash-of-war item that grants the skill (`AshTables.ash_gems`) and every weapon
    `can_mount` accepts it on (`CanMountGemWithAffinityOnWeapon` 0x140d549d0, `VERIFIED`),
    at the gem's defaultWepAttr and the weapon's highest upgrade level."""
    innate = []
    for wid in weapons:
        w = t.reg.weapon[wid]
        if w['swordArtsParamId'] == sword_arts_id:
            innate.append({'id': wid, 'name': t.reg.weapon_names[wid], 'wepType': w['wepType'],
                           'wepmotionCategory': w['wepmotionCategory'], 'gemMountType': w['gemMountType'],
                           'locked': w['gemMountType'] == 0})
    gem = t.ash_gems().get(sword_arts_id)
    mount = []
    if gem is not None:
        affinity = t.gem[gem]['defaultWepAttr']
        for wid in weapons:
            if t.can_mount(wid, gem, affinity, max_level(t, wid))[0]:
                w = t.reg.weapon[wid]
                mount.append({'id': wid, 'name': t.reg.weapon_names[wid], 'wepType': w['wepType'],
                              'class_flag': ASH.WEP_TYPE_MOUNT_FLAG.get(w['wepType'])})
    return {'innate': innate,
            'ash': None if gem is None else {'gem': gem, 'gem_name': t.gem_names.get(gem),
                                             'mount_flags': sorted(k[len('canMountWep_'):] for k, v in t.gem[gem].items()
                                                                   if k.startswith('canMountWep_') and v),
                                             'weapons': mount}}


def reference_weapon(t, sword_arts_id, weapons):
    """A weapon the skill is innate to, else the first one its ash mounts on, else Longsword."""
    for wid in weapons:
        if t.reg.weapon[wid]['swordArtsParamId'] == sword_arts_id:
            return wid
    gem = t.ash_gems().get(sword_arts_id)
    if gem is not None:
        for wid in weapons:
            if t.can_mount(wid, gem, t.gem[gem]['defaultWepAttr'], max_level(t, wid))[0]:
                return wid
    return t.find_weapon('Longsword')


def build(t=None):
    t = t or ASH.AshTables()
    attack_stance = hks_eq_constants(ATTACK_STANCE_FN)
    stance = hks_eq_constants(STANCE_FN)
    by_type = collections.defaultdict(list)
    for sid, row in sorted(t.arts.items()):
        by_type[row['swordArtsTypeNew']].append(sid)
    weapons = player_weapons(t)
    skills = []
    for type_new in attack_stance:
        for sid in by_type.get(type_new, []):
            ref = reference_weapon(t, sid, weapons)
            row = t.arts[sid]
            skills.append({'sword_arts_id': sid, 'name': t.arts_name(sid), 'swordArtsTypeNew': type_new,
                           'tae': f'a{600 + type_new}', 'reference_weapon': t.reg.weapon_names.get(ref),
                           'loop_hits': clip_hits(t, sid, ref, CLIP_LOOP),
                           'start_hits': clip_hits(t, sid, ref, CLIP_START),
                           'fp': {h: row[f'useMagicPoint_{h}'] for h in ('L2', 'R1', 'R2')},
                           'isRefRightArts': row['isRefRightArts'],
                           'carriers': carriers(t, sid, weapons)})
    # Stance skills outside the attack-stance list: their loop clip, for the contrast.
    holds = []
    for type_new in stance:
        if type_new in attack_stance:
            continue
        for sid in by_type.get(type_new, []):
            ref = reference_weapon(t, sid, weapons)
            holds.append({'sword_arts_id': sid, 'name': t.arts_name(sid), 'swordArtsTypeNew': type_new,
                          'reference_weapon': t.reg.weapon_names.get(ref),
                          'loop_hits': clip_hits(t, sid, ref, CLIP_LOOP)})
    unused = [n for n in attack_stance if n not in by_type]
    return {'hks_attack_stance_ids': attack_stance, 'hks_stance_ids': stance,
            'attack_stance_ids_without_a_row': unused, 'skills': skills, 'stance_non_attack': holds,
            'diverted_categories': DIVERTED_CATEGORIES}


def target_check(t, sword_arts_id, target_name):
    """The source skill's loop clip resolved against a target weapon: the hitboxes it creates
    and their physical motion values (`clip_hits` with the target's behaviorVariationId)."""
    wid = t.find_weapon(target_name)
    w = t.reg.weapon[wid]
    return {'target': target_name, 'id': wid, 'wepmotionCategory': w['wepmotionCategory'],
            'behaviorVariationId': w['behaviorVariationId'],
            'loop_hits': clip_hits(t, sword_arts_id, wid, CLIP_LOOP)}


#: Spinning Wheel (Ghiza's Wheel): its loop a839_040051 is child 0 of `DrawStanceRightLoop_CMSG`, so
#: it is the clip that plays when the held weapon's own skill TimeAct has no 040051 (measured on
#: Starscourge, drive-watch27: every attack-stance source looped 839040051 there).
FALLBACK_LOOP_SKILL = 1039


def loop_carriers(t, weapons=None):
    """Every player weapon that turns the carried loop into damage once it is the held weapon.

    A weapon whose own skill has a 040051 plays that loop instead (`own_loop`); otherwise the
    fallback Spinning Wheel loop plays and its judges resolve through this weapon's
    behaviorVariationId. Bows and crossbows are left out: the loop update diverts them."""
    weapons = weapons or player_weapons(t)
    out = []
    for wid in weapons:
        w = t.reg.weapon[wid]
        if w['wepmotionCategory'] in DIVERTED_CATEGORIES:
            continue
        own = w['swordArtsParamId']
        own_tae = ASH.skill_tae(t, own) or {}
        hits = clip_hits(t, FALLBACK_LOOP_SKILL, wid, CLIP_LOOP) or []
        # clip_hits keeps only 'melee' (damaging, any element or flat) and 'bullet' events.
        dmg = hits
        if not dmg:
            continue
        atk = [t.reg.atk.get(h['atk_row'], {}) for h in dmg]
        out.append({'id': wid, 'name': t.reg.weapon_names[wid], 'behaviorVariationId': w['behaviorVariationId'],
                    'own_skill': t.arts_name(own), 'own_loop': CLIP_LOOP in own_tae,
                    'hits': len(dmg), 'atk_rows': sorted({h['atk_row'] for h in dmg}),
                    'atk_names': sorted({str(t.reg.atk_names.get(h['atk_row'])) for h in dmg}),
                    'mv_phys_max': max(h['mv_phys'] or 0 for h in dmg),
                    'dmg_level_max': max(a.get('dmgLevel', 0) for a in atk)})
    out.sort(key=lambda r: (-r['dmg_level_max'], -r['mv_phys_max'], r['name']))
    return out


def print_report(r, t):
    print(f"IsAttackStanceArts ids (c0000.hks): {r['hks_attack_stance_ids']}")
    print(f"  ids with no SwordArtsParam row: {r['attack_stance_ids_without_a_row']}")
    print()
    for s in r['skills']:
        loop = s['loop_hits']
        print(f"{s['sword_arts_id']:5d} {s['name']}  typeNew {s['swordArtsTypeNew']} ({s['tae']})  "
              f"FP L2/R1/R2 {s['fp']['L2']}/{s['fp']['R1']}/{s['fp']['R2']}  isRefRightArts {s['isRefRightArts']}")
        print(f"      loop clip 040051 on {s['reference_weapon']}: "
              + (f"{len(loop)} hit events, judges {sorted({h['judge'] for h in loop})}" if loop else 'NO HITS'))
        c = s['carriers']
        for w in c['innate']:
            print(f"      innate: {w['name']} ({w['id']}) wepType {w['wepType']} "
                  f"{'LOCKED (gemMountType 0)' if w['locked'] else 'ash slot (gemMountType %d)' % w['gemMountType']}")
        if c['ash']:
            a = c['ash']
            by_class = collections.defaultdict(list)
            for w in a['weapons']:
                by_class[w['class_flag']].append(w['name'])
            print(f"      ash: {a['gem_name']} (EquipParamGem {a['gem']}), {len(a['weapons'])} weapons, "
                  f"flags {a['mount_flags']}")
            for cls, names in sorted(by_class.items()):
                print(f"        {cls} ({len(names)}): {', '.join(names)}")
        else:
            print('      ash: none (no EquipParamGem row grants it)')
    print()
    print('Stance skills whose loop does not attack:')
    for s in r['stance_non_attack']:
        print(f"  {s['sword_arts_id']:5d} {s['name']} typeNew {s['swordArtsTypeNew']}: "
              f"loop 040051 {'absent' if s['loop_hits'] is None else (str(len(s['loop_hits'])) + ' hits')}")
    print()
    tc = target_check(t, 110, 'Starscourge Greatsword')
    print(f"Wild Strikes loop on {tc['target']} (behaviorVariationId {tc['behaviorVariationId']}): "
          + ', '.join(f"judge {h['judge']} -> row {h['behavior_row']} atk {h['atk_row']} MV {h['mv_phys']}"
                      for h in tc['loop_hits']))


def selftest():
    t = ASH.AshTables()
    passed = failed = 0

    def check(name, got, want):
        nonlocal passed, failed
        ok = got == want
        passed += ok
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'} {name}: got {got!r} want {want!r}")

    if not os.path.exists(HKS.DEFAULT_FILE):
        print(f'SKIP: {HKS.DEFAULT_FILE} absent (set ER_HKS_FILE)')
        return 0
    attack_stance = set(hks_eq_constants(ATTACK_STANCE_FN))
    stance = set(hks_eq_constants(STANCE_FN))
    check('IsAttackStanceArts is a subset of IsStanceArts', attack_stance <= stance, True)
    weapons = player_weapons(t)
    tae_ok = ASH.skill_tae(t, 110) is not None
    for sid, carrier in NAMED.items():
        tn = t.arts[sid]['swordArtsTypeNew']
        check(f'{t.arts_name(sid)} ({sid}) typeNew {tn} in IsAttackStanceArts', tn in attack_stance, True)
        if tae_ok:
            ref = reference_weapon(t, sid, weapons)
            hits = clip_hits(t, sid, ref, CLIP_LOOP)
            check(f'{t.arts_name(sid)} loop clip 040051 hits', bool(hits), True)
        if carrier:
            wid = t.find_weapon(carrier)
            check(f'{carrier} carries {t.arts_name(sid)}', t.reg.weapon[wid]['swordArtsParamId'], sid)
    check('Wild Strikes is an ash of war', 110 in t.ash_gems(), True)
    check("Ghiza's Wheel skill is locked (gemMountType 0)",
          t.reg.weapon[t.find_weapon("Ghiza's Wheel")]['gemMountType'], 0)
    check('Dancing Blade of Ranah skill is locked (gemMountType 0)',
          t.reg.weapon[t.find_weapon('Dancing Blade of Ranah')]['gemMountType'], 0)
    for sid in NEGATIVES:
        tn = t.arts[sid]['swordArtsTypeNew']
        check(f'{t.arts_name(sid)} ({sid}) typeNew {tn} not in IsAttackStanceArts', tn in attack_stance, False)
    if tae_ok:
        check('Square Off loop clip 040051 has no hit', clip_hits(t, 115, t.find_weapon('Longsword'), CLIP_LOOP), [])
        tc = target_check(t, 110, 'Starscourge Greatsword')
        check('Wild Strikes loop resolves to damaging hits on Starscourge Greatsword',
              bool(tc['loop_hits']) and all(h['mv_phys'] for h in tc['loop_hits']), True)
    else:
        print('SKIP TAE checks: player TimeAct not found')
    print(f'{passed} passed, {failed} failed')
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--loop-carriers', action='store_true',
                    help='weapons that deal damage with the carried loop once held (target side of the swap)')
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    t = ASH.AshTables()
    if args.loop_carriers:
        rows = loop_carriers(t)
        if args.json:
            print(json.dumps(rows, indent=1))
        else:
            for x in rows:
                print(f"dmgLevel {x['dmg_level_max']}  MV {x['mv_phys_max']:4}  {x['name']} ({x['id']}, var "
                      f"{x['behaviorVariationId']})  own skill {x['own_skill']}{' [own loop plays]' if x['own_loop'] else ''}"
                      f"  atk {x['atk_rows']} {x['atk_names']}")
            print(f'{len(rows)} weapons')
        return 0
    r = build(t)
    if args.json:
        r['target_check'] = target_check(t, 110, 'Starscourge Greatsword')
        print(json.dumps(r, indent=1, default=str))
    else:
        print_report(r, t)
    return 0


if __name__ == '__main__':
    sys.exit(main())
