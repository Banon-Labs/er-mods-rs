#!/usr/bin/env python3
"""Chain attacks: which attacks of every weapon carry the Twinblade Talisman's subcategory, and how
fast each moveset reaches and repeats them.

A chain attack is not a player-facing move name. It is an AtkParam_Pc row whose subCategory1..4
holds 104, the value in Twinblade Talisman's magicSubCategoryChange (SpEffect 321200, x1.45), and
`CheckMagicSubCategoryChangeMask` matches the talisman to a hit by that value alone
(talismans.md 2a). So "how many chain attacks a weapon has" is the count of its moveset slots with
a hit on such a row, per grip:

    one     one-handed right hand
    both    two-handed (for a weapon powerstanced by itself, this is its powerstance)
    dual    the same weapon in both hands, L1 string (`er-mechanics-moveset.dual_attacks`)
    skill   the default skill's damaging rows (counted, not timed: a skill is one action)

Timing, under the ceiling the rest of the mechanics scripts use (every input at its earliest
accepted frame, every hit landing at its window's first frame, 30 fps TimeAct frames):

    to_chain_s      first R1 (or L1) pressed at 0; each earlier string slot is cancelled into the
                    next at its `cancel_frame` r1 (l1 for dual); the clock stops on the first
                    frame of the first hit that carries 104.
    period_s        the whole string looped: the sum of every slot's cancel frame. The last slot's
                    cancel frame is when the next press restarts the string at slot 1.
    chain_hits      hit records carrying 104 in one pass of the string (a slot can have several,
                    and a powerstance slot has one per hand).
    chain_hits_per_s chain_hits / period_s: how often x1.45 lands while the string is looped.

`rank` is the moveset's place among every weapon x grip that has a chain attack, fastest first.

    python3 scripts/er-mechanics-chain-attacks.py                   # every weapon, by to_chain_s
    python3 scripts/er-mechanics-chain-attacks.py --sort rate       # by chain_hits_per_s
    python3 scripts/er-mechanics-chain-attacks.py --weapon Twinblade
    python3 scripts/er-mechanics-chain-attacks.py --survey          # which slots ever carry 104
    python3 scripts/er-mechanics-chain-attacks.py --json --out chains.json
"""
import argparse
import collections
import importlib.util
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
# A caller that already loaded the affinity module (er-mechanics-gear-synergy) shares its copy.
A = sys.modules.get('er_mechanics_talisman_affinity')
if A is None:
    _s = importlib.util.spec_from_file_location('er_mechanics_talisman_affinity',
                                                os.path.join(_HERE, 'er-mechanics-talisman-affinity.py'))
    A = importlib.util.module_from_spec(_s)
    _s.loader.exec_module(A)

FPS = A.FPS
TALISMAN = 'Twinblade Talisman'
GRIP_LABEL = {'one': 'one-handed', 'both': 'two-handed', 'dual': 'powerstance'}


def chain_values(d):
    return set(d.triggers[TALISMAN]['kinds']['subcategory']['values'])


_ar = importlib.util.spec_from_file_location('er_mechanics_ar', os.path.join(_HERE, 'er-mechanics-ar.py'))
AR = importlib.util.module_from_spec(_ar)
_ar.loader.exec_module(AR)
#: AtkParam_Pc motion value per AR element (`er-mechanics-attacks.attack_numbers`).
MV_FIELD = {'physical': 'atkPhysCorrection', 'magic': 'atkMagCorrection', 'fire': 'atkFireCorrection',
            'lightning': 'atkThunCorrection', 'holy': 'atkDarkCorrection'}
#: Twinblade Talisman's bonus on a chain hit (talismans.md 1, SpEffect 321200).
CHAIN_BONUS = 0.45


def hit_damage(reg, ar, atk_row):
    """Pre-defense damage of one hit: sum over elements of AR x motion value / 100. Flat
    `isAddBaseAtk` adds are left out."""
    a = reg.atk.get(atk_row)
    if not a or not ar:
        return 0.0
    return sum(v['total'] * a[MV_FIELD[k]] / 100.0 for k, v in ar['damage'].items())


def grip_chain(gp, vals, grip, paired_2h, reg=None, ar=None):
    """Chain facts of one grip profile, or None when no slot of it carries a chain value."""
    marked = [s['slot'] for s in gp['slots'] if any(vals & set(h['subcats']) for h in s['hits'])]
    out = {'grip': grip, 'label': GRIP_LABEL[grip] + (' (powerstanced by itself)'
                                                      if grip == 'both' and paired_2h else ''),
           'slots': len(gp['slots']), 'chain_slots': marked,
           'string': [s['slot'] for s in gp['r1_chain']]}
    if not marked:
        return out
    string = gp['r1_chain']
    elapsed, to_chain, chain_hits, chain_dmg = 0.0, None, 0, 0.0
    for s in string:
        hits = [h for h in s['hits'] if vals & set(h['subcats'])]
        if hits and to_chain is None:
            to_chain = elapsed + min(h['frames'][0] for h in hits)
        chain_hits += sum(h['records'] for h in hits)
        chain_dmg += sum(hit_damage(reg, ar, h['atk_row']) * h['records'] for h in hits if h['atk_row'])
        if s['cancel_r1'] is None:
            elapsed = None
            break
        elapsed += s['cancel_r1']
    out['to_chain_s'] = round(to_chain / FPS, 3) if to_chain is not None else None
    out['period_s'] = round(elapsed / FPS, 3) if elapsed else None
    out['chain_hits'] = chain_hits
    out['chain_hits_per_s'] = round(chain_hits / (elapsed / FPS), 3) if elapsed and chain_hits else None
    out['outside_string'] = [x for x in marked if x not in out['string']]
    # Damage of the chain hits in one loop without the talisman, and the talisman's added
    # damage per second while the string is looped (bonus x damage / period).
    out['chain_damage'] = round(chain_dmg, 1)
    out['bonus_per_s'] = round(CHAIN_BONUS * chain_dmg / (elapsed / FPS), 1) if elapsed else None
    return out


def weapon_ar(tables, wid, stats, two_handed):
    try:
        lvl = tables.max_level(tables.weapons[wid]['reinforceTypeId'])
        return AR.attack_rating(tables, wid, 'Standard', lvl, stats, two_handed)
    except (KeyError, SystemExit):
        return None


def weapon_chains(d, wid, vals, tables=None, stats=None):
    prof = A.weapon_profile(d, wid)
    grips = {}
    for g, gp in prof['grips'].items():
        ar = weapon_ar(tables, wid, stats, g == 'both') if tables else None
        grips[g] = grip_chain(gp, vals, g, prof['two_hand_is_pair'], d.reg, ar)
    rows = prof['skill']['rows']
    skill = {'name': prof['skill']['name'], 'damaging_rows': len(rows),
             'chain_rows': len([r for r in rows if vals & set(d.subcats(r))])}
    return {'id': wid, 'name': prof['name'], 'two_hand_is_pair': prof['two_hand_is_pair'],
            'grips': grips, 'skill': skill}


def all_chains(d, stats):
    vals = chain_values(d)
    tables = AR.Tables()
    out, failed = [], []
    for wid in A.base_weapons(d):
        try:
            w = weapon_chains(d, wid, vals, tables, stats)
        except (KeyError, TypeError, ValueError) as e:
            failed.append({'id': wid, 'name': d.weapon_name(wid), 'error': repr(e)})
            continue
        if any(g['slots'] for g in w['grips'].values()):
            out.append(w)
    timed = sorted((g['to_chain_s'], w['name'], g['grip']) for w in out for g in w['grips'].values()
                   if g.get('to_chain_s') is not None)
    rank = {(n, g): i + 1 for i, (_, n, g) in enumerate(timed)}
    for w in out:
        for g in w['grips'].values():
            g['rank'] = rank.get((w['name'], g['grip']))
            g['of'] = len(timed)
    return {'chain_values': sorted(vals), 'weapons': out, 'failed': failed}


#: Animation ids of the ordinary moveset (R1/R2 strings, running, rolling, jumping, counters,
#: powerstance, off-hand). A skill TimeAct that carries one replaces that move while the skill's
#: TimeAct file is the one the game reads it from.
MOVESET_ANIMS = range(30000, 40000)


def skill_chain_scan(d):
    """Every SwordArtsParam (default skills and ashes of war) on every weapon that can fire it:
    the animations of its TimeAct whose melee hits (event 1, or event 307 with flag 8) or bullet
    hits resolve to an AtkParam row carrying a chain value."""
    ASH = d.ASH
    vals = chain_values(d)
    import struct
    found = []
    for sid in sorted(d.ash.arts):
        anims = ASH.skill_tae(d.ash, sid) or {}
        if not anims:
            continue
        judges = []
        for a, ev in anims.items():
            for e in ev:
                p = e.params
                if e.type == ASH.EV_ATTACK and len(p) >= 12:
                    if struct.unpack_from('<i', p, 0)[0] == ASH.ATTACK_TYPE_PARRY:
                        continue
                    judges.append((a, struct.unpack_from('<i', p, 8)[0], 1, False))
                elif e.type == ASH.EV_BULLET and len(p) >= 12:
                    judges.append((a, struct.unpack_from('<i', p, 8)[0], 1, True))
                elif e.type == ASH.EV_PC_BEHAVIOR and len(p) >= 12:
                    flags, judge = struct.unpack_from('<Ii', p, 4)
                    if flags & 8:
                        judges.append((a, judge, flags, False))
        if not judges:
            continue
        moveset_anims = sorted({a for a, *_ in judges if a in MOVESET_ANIMS})
        for wid in A.base_weapons(d):
            if sid not in ASH.mountable_skills(d.ash, wid):
                continue
            hits = []
            for a, judge, flags, bullet in judges:
                if a in MOVESET_ANIMS:
                    continue  # the weapon's own moves sharing the file, already in the ranking
                res = ASH.resolve_judge(d.ash, wid, judge, event_flags=flags, via_bullet=bullet)
                rows = []
                if res.get('kind') == 'melee':
                    rows = [res['atk_row']]
                elif res.get('kind') == 'bullet':
                    stack = [res.get('bullet')]
                    while stack:
                        bt = stack.pop()
                        if bt:
                            rows.append(bt.get('atk_row'))
                            stack.extend((bt.get('children') or {}).values())
                for r in rows:
                    if r and vals & set(d.subcats(r)):
                        hits.append({'anim': a, 'judge': judge, 'atk_row': r})
            if hits or moveset_anims:
                found.append({'skill': d.ash.arts_name(sid), 'sword_arts_id': sid, 'weapon': d.weapon_name(wid),
                              'weapon_id': wid, 'default': d.wep[wid]['swordArtsParamId'] == sid,
                              'chain_hits': hits, 'moveset_anims': moveset_anims})
    return found


def survey(res):
    by_slot = collections.Counter()
    weapons_with = collections.Counter()
    count_hist = collections.Counter()
    for w in res['weapons']:
        for g in w['grips'].values():
            for s in g['chain_slots']:
                by_slot[(g['grip'], s)] += 1
            if g['slots']:
                weapons_with[g['grip']] += 1
                count_hist[(g['grip'], len(g['chain_slots']))] += 1
    print('slots carrying a chain value (grip, slot: weapons)')
    for (g, s), n in sorted(by_slot.items(), key=lambda x: (x[0][0], -x[1])):
        print(f'  {g:5} {s:14} {n}')
    print('\nchain slots per moveset (grip: count -> movesets)')
    for g in ('one', 'both', 'dual'):
        hist = {k: n for (gg, k), n in count_hist.items() if gg == g}
        print(f'  {g:5} of {weapons_with[g]:3}: ' + ', '.join(f'{k} -> {n}' for k, n in sorted(hist.items())))
    sk = collections.Counter(w['skill']['chain_rows'] > 0 for w in res['weapons'])
    print(f'\ndefault skills with a chain row: {sk[True]} of {sum(sk.values())}')


def table(res, sort, top):
    rows = [(w, g) for w in res['weapons'] for g in w['grips'].values() if g.get('to_chain_s') is not None]
    keys = {'rate': lambda r: -(r[1]['chain_hits_per_s'] or 0),
            'bonus': lambda r: -(r[1].get('bonus_per_s') or 0),
            'damage': lambda r: -(r[1].get('chain_damage') or 0),
            'time': lambda r: r[1]['rank']}
    rows.sort(key=keys[sort])
    print(f'{"rank":>4} {"weapon":30} {"grip":30} {"to chain":>8} {"hits":>4} {"period":>6} {"hits/s":>6} '
          f'{"chain dmg":>9} {"bonus/s":>7}  chain slots')
    for w, g in rows[:top]:
        print(f'{g["rank"]:>4} {w["name"][:30]:30} {g["label"][:30]:30} {g["to_chain_s"]:8.3f} '
              f'{g["chain_hits"]:4} {g["period_s"] or 0:6.3f} {g["chain_hits_per_s"] or 0:6.3f} '
              f'{g.get("chain_damage") or 0:9.1f} {g.get("bonus_per_s") or 0:7.1f}  '
              f'{", ".join(g["chain_slots"])}')
    none = [(w['name'], g['grip']) for w in res['weapons'] for g in w['grips'].values()
            if g['slots'] and not g['chain_slots']]
    print(f'\n{len(rows)} movesets with a chain attack; {len(none)} with none.')


def show_weapon(res, name):
    key = A.fold(name) if hasattr(A, 'fold') else name.lower()
    for w in res['weapons']:
        if w['name'].lower() == name.lower():
            print(f'{w["name"]} ({w["id"]})')
            for g in w['grips'].values():
                print(f'  {g["label"]}: {len(g["chain_slots"])} of {g["slots"]} slots carry 104 '
                      f'({", ".join(g["chain_slots"]) or "none"}); string {"/".join(g["string"])}')
                if g.get('to_chain_s') is not None:
                    print(f'    first chain hit at {g["to_chain_s"]} s, string loops every {g["period_s"]} s, '
                          f'{g["chain_hits"]} chain hits per loop = {g["chain_hits_per_s"]}/s, '
                          f'rank {g["rank"]} of {g["of"]}')
            print(f'  skill {w["skill"]["name"]}: {w["skill"]["chain_rows"]} of '
                  f'{w["skill"]["damaging_rows"]} damaging rows carry 104')
            return 0
    print(f'no weapon named {name!r}', file=sys.stderr)
    return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--weapon')
    ap.add_argument('--survey', action='store_true')
    ap.add_argument('--sort', choices=('time', 'rate', 'damage', 'bonus'), default='time')
    ap.add_argument('--stats', default='str=80,dex=80,int=80,fth=80,arc=80',
                    help='stats for the reference AR (max upgrade, Standard affinity)')
    ap.add_argument('--top', type=int, default=40)
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--out')
    ap.add_argument('--from', dest='src', help='read a previous --json result instead of the regulation')
    ap.add_argument('--skills', action='store_true',
                    help='every skill / ash of war x mountable weapon: chain hits and moveset anims it overrides')
    a = ap.parse_args()
    if a.skills:
        found = skill_chain_scan(A.Data())
        if a.out:
            with open(a.out, 'w') as f:
                json.dump(found, f)
        by = collections.defaultdict(lambda: {'weapons': set(), 'anims': set(), 'moveset': set(), 'default': False})
        for r in found:
            e = by[r['skill']]
            if r['chain_hits']:
                e['weapons'].add(r['weapon'])
                e['anims'] |= {h['anim'] for h in r['chain_hits']}
            e['moveset'] |= set(r['moveset_anims'])
            e['default'] |= r['default']
        print(f'{"skill":32} {"chain anims":24} {"moveset anims overridden":28} weapons with a chain hit')
        for k, e in sorted(by.items(), key=lambda x: (-len(x[1]['weapons']), x[0])):
            print(f'{k[:32]:32} {",".join(map(str, sorted(e["anims"])))[:24]:24} '
                  f'{",".join(map(str, sorted(e["moveset"])))[:28]:28} '
                  f'{len(e["weapons"])}: {", ".join(sorted(e["weapons"]))[:80]}')
        return 0
    if a.src:
        with open(a.src) as f:
            res = json.load(f)
    else:
        stats = {k: int(v) for k, v in (p.split('=') for p in a.stats.split(','))}
        res = all_chains(A.Data(), stats)
        res['reference'] = {'stats': stats, 'affinity': 'Standard', 'level': 'max'}
    if a.json:
        text = json.dumps(res, default=str)
        if a.out:
            with open(a.out, 'w') as f:
                f.write(text)
            print(f'{len(res["weapons"])} weapons, {len(res["failed"])} failed -> {a.out}')
        else:
            print(text)
        return 0
    if a.weapon:
        return show_weapon(res, a.weapon)
    if a.survey:
        survey(res)
        return 0
    table(res, a.sort, a.top)
    return 0


if __name__ == '__main__':
    sys.exit(main())
