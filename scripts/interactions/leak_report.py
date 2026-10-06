#!/usr/bin/env python3
"""Regulation side of docs/er-mechanics/weapon-buff-leak.md.

    python3 scripts/interactions/leak_report.py sp <id>...   dump SpEffectParam rows
    python3 scripts/interactions/leak_report.py rates        status-rate spread over skill hits
    python3 scripts/interactions/leak_report.py leaks        cross-weapon leak class
    python3 scripts/interactions/leak_report.py all          rates and leaks (one skill walk)

`rates` takes every AtkParam_Pc row a skill reaches, split into skill bullets (every node of
every bullet chain a skill TimeAct fires) and skill swings (melee events of the same TimeAct),
and counts statusAilmentAtkPowerCorrectRate / _byPoint.

`leaks` lists every (bullet, context) that passes both gates of regdata.py for some
player-reachable weapon buff (stateInfo 152/153 with an on-hit row) while the launching weapon
cannot hold that buff: ammo (the bow or crossbow has isEnhance 0), or a skill bullet whose
weapons all have isEnhance 0. The skill walk (er-mechanics-ashes over every skill and weapon
type) takes about a minute, so run it in the background.
"""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import regdata  # noqa: E402
import scan  # noqa: E402
import skill_bullet_map as SBM  # noqa: E402

ASH = SBM.ASH


def cmd_sp(reg, ids):
    for i in ids:
        s = reg.sp.get(int(i))
        print(f'== {i} {reg.name("SpEffectParam", int(i))}')
        if s is None:
            continue
        for k, v in s.items():
            if v not in (0, -1, 0.0, 1.0, b'', '') or k in ('wepParamChange', 'stateInfo'):
                print(f'  {k} = {v}')


def skill_hits():
    """skill name -> {'melee': {atk ids}, 'bullet': {bullet ids}}, from the skill TimeActs."""
    t = ASH.AshTables()
    weapons = {i: w for i, w in t.reg.weapon.items() if i % 10000 == 0}
    by_skill = collections.defaultdict(set)
    for wid, w in weapons.items():
        by_skill[w['swordArtsParamId']].add(wid)
    for sid, gid in t.ash_gems().items():
        seen = set()
        for wid, w in weapons.items():
            if w['wepType'] in seen:
                continue
            if t.can_mount(wid, gid, level=25)[0]:
                seen.add(w['wepType'])
                by_skill[sid].add(wid)
    out = collections.defaultdict(lambda: {'melee': set(), 'bullet': set(), 'weapons': set()})
    for sid in sorted(by_skill):
        if sid not in t.arts:
            continue
        name = f'{t.arts_name(sid)} ({sid})'
        out[name]['weapons'] |= by_skill[sid]
        for wid in sorted(by_skill[sid]):
            try:
                prof = ASH.skill_profile(t, sid, wid)
            except SystemExit:
                continue
            for acts in prof['anims'].values():
                for x in acts:
                    if x.get('kind') == 'melee' and x.get('atk_row') is not None:
                        out[name]['melee'].add(x['atk_row'])
                    elif x.get('kind') == 'bullet':
                        s = set()
                        SBM._bullets_in(x.get('bullet') or {}, s)
                        out[name]['bullet'] |= s
    return out


def rate_pair(reg, aid):
    a = reg.atk.get(aid)
    if a is None:
        return None
    return (a['statusAilmentAtkPowerCorrectRate'], a['statusAilmentAtkPowerCorrectRate_byPoint'])


def cmd_rates(reg, hits):
    for kind in ('bullet', 'melee'):
        by_atk = collections.defaultdict(set)   # atk id -> skills
        for sk, h in hits.items():
            if kind == 'melee':
                ids = h['melee']
            else:
                ids = set()
                for b in h['bullet']:
                    for node in reg.chain(b):
                        aid = reg.bullet.get(node['id'], {}).get('atkId_Bullet', -1)
                        if aid is not None and aid >= 0:
                            ids.add(aid)
            for aid in ids:
                by_atk[aid].add(sk)
        dist = collections.Counter()
        skills_by_pair = collections.defaultdict(set)
        for aid, sks in by_atk.items():
            p = rate_pair(reg, aid)
            if p is None:
                continue
            dist[p] += 1
            skills_by_pair[p] |= sks
        n = sum(dist.values())
        print(f'== skill {kind} AtkParam_Pc rows: {n} rows, {len({s for v in by_atk.values() for s in v})} skills')
        for p, c in dist.most_common():
            print(f'  rate/byPoint {p[0]}/{p[1]}: {c} rows, {len(skills_by_pair[p])} skills')
        if kind == 'bullet':
            full = skills_by_pair.get((100, 100), set())
            print(f'  skills with a 100/100 bullet row ({len(full)}):')
            for s in sorted(full):
                print('   ', s)
    # Skills whose every weapon has isEnhance 0: a buff they carry can only live on the other weapon.
    print('== skill bullets of skills on weapons that cannot hold a buff (all mounting weapons isEnhance 0)')
    dist = collections.Counter()
    full = []
    for sk, h in sorted(hits.items()):
        ws = [w for w in h['weapons'] if w in reg.weapon]
        if not ws or any(reg.weapon[w]['isEnhance'] for w in ws):
            continue
        pairs = set()
        for b in h['bullet']:
            for node in reg.chain(b):
                aid = reg.bullet[node['id']]['atkId_Bullet']
                if aid >= 0 and rate_pair(reg, aid):
                    pairs.add((aid, rate_pair(reg, aid)))
        for _, p in pairs:
            dist[p] += 1
        if any(p == (100, 100) for _, p in pairs):
            full.append(sk)
    for p, c in dist.most_common(6):
        print(f'  rate/byPoint {p[0]}/{p[1]}: {c} rows')
    print(f'  skills with a 100/100 bullet row ({len(full)}):', ', '.join(full))
    rancor = [s for s in hits if s.startswith('Familial Rancor')]
    for s in rancor:
        print('== ', s, 'melee', sorted(hits[s]['melee']), 'bullets', sorted(hits[s]['bullet']))
        for b in sorted(hits[s]['bullet']):
            for node in reg.chain(b):
                aid = reg.bullet[node['id']]['atkId_Bullet']
                print(f'   bullet {node["id"]} atk {aid} rates {rate_pair(reg, aid)}')


def cmd_leaks(reg, smap):
    src = scan.Sources(reg, smap)
    cars = scan.carriers(reg, smap, 0.0)
    r1 = src.rows(lambda s: s['stateInfo'] in (152, 153) and s['atkOccurrenceSpEffectId'] > 0)
    rows = []
    for c in cars:
        atk = c.bi['a']['row']
        ok = [s for s in r1 if regdata.gate(c.ctx, s, atk)
              and regdata.status_reaches(reg.sp.get(s['atkOccurrenceSpEffectId']), atk)]
        hands = sorted({s['wepParamChange'] for s in ok})
        if not ok or not any(h in (1, 2) for h in hands):
            continue
        ammo = scan.is_ammo(reg, c.weapons)
        wb = scan.buffable(reg, c.weapons)
        if ammo:
            why = 'ammo (launcher isEnhance 0)'
        elif wb is False:
            why = 'skill weapon isEnhance 0'
        else:
            continue
        rows.append((why, c, hands, regdata.status_scale(atk)))
    by_why = collections.Counter(r[0] for r in rows)
    print('== leak class:', len(rows), 'bullet/context pairs', dict(by_why))
    print('== summary: class | ctx | unique bullets | full-scale (1.0) bullets | lingering (life >= 1 s)')
    groups = collections.defaultdict(list)
    for r in rows:
        groups[(r[0], r[1].ctx)].append(r)
    for (why, ctx), rs in sorted(groups.items()):
        full = sum(1 for r in rs if r[3] >= 1.0)
        ling = sum(1 for r in rs if r[1].bi['life'] >= 1.0)
        print(f'  {why} | {ctx} | {len({r[1].bid for r in rs})} | {full} | {ling}')
        owners = collections.Counter()
        for r in rs:
            for o in {o.split(' (')[0] if ' moveset' not in o else o for o in r[1].owners}:
                owners[o] += 1
        print('    top owners:', '; '.join(f'{o} {n}' for o, n in owners.most_common(12)))
    for why, c, hands, sc in sorted(rows, key=lambda r: (r[0], -r[3], r[1].bid)):
        owners = '; '.join(c.owners[:3])
        print(f'{why} | bullet {c.bid} {c.bi["name"]} | ctx {c.ctx} | hands {hands} | '
              f'atk {c.bi["atk"]} scale {sc:g} | life {c.bi["life"]:g} r {max(c.bi["r"], c.bi["rmax"]):g} '
              f'| {owners}')


def main():
    reg = regdata.Reg()
    cmd = sys.argv[1]
    if cmd == 'sp':
        cmd_sp(reg, sys.argv[2:])
        return
    if cmd in ('rates', 'all'):
        cmd_rates(reg, skill_hits())
    if cmd in ('leaks', 'all'):
        cmd_leaks(reg, SBM.build())


if __name__ == '__main__':
    main()
