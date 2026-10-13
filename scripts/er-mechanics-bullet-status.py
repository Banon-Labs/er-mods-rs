#!/usr/bin/env python3
"""Which status rows reach a skill's hits, swing and bullet, per weapon and affinity. Offline.

The rule (`VERIFIED`, 1.16.2 :8765, see `er-mechanics-ash-table.Table.hit_buildup`): a bullet hit
carries the weapon's status rows (hit slots 5..7) scaled by its own AtkParam's
`statusAilmentAtkPowerCorrectRate`, and its Bullet row's `spEffectId0..4` in slots 0..4.

  python3 scripts/er-mechanics-bullet-status.py dig 508 "Great Stars"     # one skill's chain
  python3 scripts/er-mechanics-bullet-status.py census                    # every ash's bullet scales
  python3 scripts/er-mechanics-bullet-status.py compare 508 "Great Stars,Zweihander" Cold
  python3 scripts/er-mechanics-bullet-status.py rows 107625 6401          # SpEffect rows
  python3 scripts/er-mechanics-bullet-status.py bullets 2810 2811         # Bullet rows, diffed
  python3 scripts/er-mechanics-bullet-status.py atks 0 30000812           # AtkParam_Pc rows
"""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(n, f):
    if n in sys.modules:
        return sys.modules[n]
    s = importlib.util.spec_from_file_location(n, HERE / f)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


T = load('er_mechanics_ash_table', 'er-mechanics-ash-table.py')
A, AR = T.A, T.AR
ST = load('er_mechanics_status', 'er-mechanics-status.py')


def dig(sid=508, weapon='Great Stars'):
    t = A.AshTables(None)
    st = ST.Tables()
    print('arts', sid, t.arts_name(sid))
    for w in ['Great Stars', 'Rotten Battle Hammer', 'Great Omenkiller Cleaver']:
        for aff in ['Standard', 'Cold', 'Blood', 'Heavy']:
            try:
                bid = st.ar.find_weapon(w, aff)
            except SystemExit:
                continue
            wr = st.ar.weapons[bid]
            print(w, aff, bid, 'behVar', t.reg.weapon.get(bid, {}).get('behaviorVariationId'),
                  'spAttrVar', wr.get('spAttributeVariationValue'), 'spEff',
                  [wr.get(f'spEffectBehaviorId{k}') for k in range(3)], 'reinf', wr['reinforceTypeId'],
                  [st.ar.reinforce[wr['reinforceTypeId'] + 25].get(f'spEffectId{k}') for k in (1, 2, 3)])
    wid = t.find_weapon(weapon)
    prof = A.skill_profile(t, sid, wid, 25)
    for a, evs in prof['anims'].items():
        for x in evs:
            if x['kind'] in ('melee', 'bullet'):
                print(a, x['kind'], x['judge'], x['behavior_row'], x.get('atk_row'), x.get('behavior_category'),
                      x['bullet']['bullet'] if x['kind'] == 'bullet' else '')

    def dump_b(bt, d=0):
        if not bt:
            return
        a = st.atk.get(bt['atk_row'], {})
        print(' ' * d, 'bullet', bt['bullet'], bt['name'], 'atk', bt['atk_row'], 'spEff', bt['on_hit_speffects'],
              'stRate', a.get('statusAilmentAtkPowerCorrectRate'), a.get('statusAilmentAtkPowerCorrectRate_byPoint'),
              'atkSp', [a.get(f'spEffectId{i}') for i in range(5)], 'disableHitSp', a.get('disableHitSpEffect'))
        for c in bt['children'].values():
            dump_b(c, d + 2)
    seen = set()
    for a, evs in prof['anims'].items():
        for x in evs:
            if x['kind'] == 'bullet' and x['bullet']['bullet'] not in seen:
                seen.add(x['bullet']['bullet'])
                dump_b(x['bullet'])
            if x['kind'] == 'melee' and x['atk_row'] not in seen:
                seen.add(x['atk_row'])
                a2 = st.atk[x['atk_row']]
                print('melee atk', x['atk_row'], x['atk_name'], 'stRate', a2.get('statusAilmentAtkPowerCorrectRate'),
                      a2.get('statusAilmentAtkPowerCorrectRate_byPoint'), [a2.get(f'spEffectId{i}') for i in range(5)])


def rows(ids):
    st = ST.Tables()
    for rid in ids:
        r = st.sp.get(rid)
        if not r:
            print(rid, 'missing')
            continue
        print(rid, st.sp_names.get(rid), 'stateInfo', r.get('stateInfo'), 'status', ST.row_status(r),
              'useAtkCorrect', r.get('isUseStatusAilmentAtkPowerCorrect'),
              'refusal', ST.hostile_target_refusal(r))


def bullets(ids):
    PR = A.PR
    rs, _, _ = PR.rows(PR.param_bytes(PR.load(None), 'Bullet'), None)
    by = {r['id']: r for r in rs}
    base = None
    for bid in ids:
        r = by[bid]
        if base is None:
            base = r
            print(bid, {k: v for k, v in r.items() if v not in (0, -1, 0.0)})
        else:
            print(bid, 'diff', {k: v for k, v in r.items() if v != base.get(k)})


def atks(ids):
    PR = A.PR
    rs, _, _ = PR.rows(PR.param_bytes(PR.load(None), 'AtkParam_Pc'), None)
    by = {r['id']: r for r in rs}
    for aid in ids:
        r = by.get(aid)
        print(aid, {k: v for k, v in r.items() if v not in (0, -1, 0.0) and not k.startswith('_')} if r else 'missing')


def named(pattern):
    import re
    PR = A.PR
    for stem in ('Bullet', 'BehaviorParam_PC', 'AtkParam_Pc'):
        names = PR.row_names(stem)
        hits = sorted(i for i, n in names.items() if n and re.search(pattern, n))
        print(stem, hits)
    rs, _, _ = PR.rows(PR.param_bytes(PR.load(None), 'Bullet'), None)
    print('bullet fields', [k for k in rs[0] if re.search(r'(?i)inherit|sp|atk|attribute', k)])
    rs, _, _ = PR.rows(PR.param_bytes(PR.load(None), 'BehaviorParam_PC'), None)
    print('behavior rows refId 2810..2817', [(r['id'], r['refType'], r['refId'], r['variationId'] if 'variationId' in r else None)
                                             for r in rs if r['refType'] == 1 and 2810 <= r['refId'] <= 2817])


def emit_types():
    import collections
    PR = A.PR
    rs, _, _ = PR.rows(PR.param_bytes(PR.load(None), 'Bullet'), None)
    names = PR.row_names('Bullet')
    by = collections.defaultdict(list)
    for r in rs:
        by[r['EmittePosType']].append(r['id'])
    for k, ids in sorted(by.items()):
        print(k, len(ids), [names.get(i) for i in ids if names.get(i)][:12])


def walk(bt, out):
    if not bt:
        return
    out.append(bt)
    for c in bt['children'].values():
        walk(c, out)


def census():
    """Per ash-of-war skill: the AtkParam status scale of every bullet it can fire, over one
    weapon per (wepType, behaviorVariationId) that can mount it."""
    import collections
    t = A.AshTables(None)
    st = ST.Tables()
    reps = {}
    for wid, w in sorted(t.reg.weapon.items()):
        if wid % 10000 == 0:
            reps.setdefault((w['wepType'], w['behaviorVariationId']), wid)
    gems = t.ash_gems()
    per = collections.defaultdict(dict)        # sid -> {bullet id: (rate, byPoint, damages, spEff)}
    for sid, gem in sorted(gems.items()):
        if sid not in t.arts:
            continue
        for wid in reps.values():
            if not t.can_mount(wid, gem, 0, 25)[0]:
                continue
            prof = A.skill_profile(t, sid, wid, 25)
            for evs in prof['anims'].values():
                for x in evs:
                    if x['kind'] != 'bullet':
                        continue
                    nodes = []
                    walk(x['bullet'], nodes)
                    for n in nodes:
                        a = st.atk.get(n['atk_row'], {})
                        per[sid][n['bullet']] = (a.get('statusAilmentAtkPowerCorrectRate', 0),
                                                 a.get('statusAilmentAtkPowerCorrectRate_byPoint', 0),
                                                 A._bullet_damages_self(n), tuple(n['on_hit_speffects']), n['atk_row'])
    kinds = collections.Counter()
    lists = collections.defaultdict(list)
    for sid, bs in sorted(per.items()):
        dmg = [v for v in bs.values() if v[2]]
        carriers = [v for v in bs.values() if not v[2] and v[0] > 0]
        if not dmg:
            k = 'no damaging bullet' + (', status-scale carrier' if carriers else '')
        elif all(v[0] == 0 for v in dmg):
            k = 'damaging bullets all scale 0' + (', carrier >0' if carriers else '')
        elif all(v[0] > 0 for v in dmg):
            k = 'damaging bullets all scale >0'
        else:
            k = 'mixed'
        kinds[k] += 1
        lists[k].append(f"{t.arts_name(sid)}[{','.join(str(v[0]) for v in dmg)}]")
    for k, n in kinds.most_common():
        print(f'{n:4d}  {k}: {"; ".join(lists[k])}')
    print('ashes with bullets', len(per))


def old_status_hp(tab, two, hits, string_slots):
    """`er-mechanics-ash-table.Table.status_hp` as it was before 2026-10-08 (AtkParam slots 0..4
    for bullet hits too)."""
    S_, st, d = tab.status_tables()
    ws = S_.weapon_status(st, tab.weapon, tab.affinity, tab.level, tab.stats, two)
    out, cut = {}, {}
    for atk_row, n in ([(h['atk_row'], h.get('count', 1)) for h in hits]
                       + [(s['attack']['atk_row'], max(1, len(s['attack'].get('hit_windows') or [])))
                          for s in string_slots]):
        for name, v in S_.status_per_hit(st, ws, {'atk_row': atk_row}, d).items():
            if name not in cut:
                cut[name] = 1.0
                if v['proc_speffect']:
                    pe = S_.proc_effect(st, v['proc_speffect'], d['hp'])
                    if pe['ticks'] > 1 and pe['interval'] > 0 and pe['hp_total']:
                        cut[name] = min(pe['ticks'], int(S_.ENGAGEMENT_SECONDS / pe['interval'])) / pe['ticks']
            out[name] = out.get(name, 0.0) + v['hp_per_hit'] * n * cut[name]
    return {k: round(v) for k, v in out.items() if v}


def compare(sid, weapons, affinity='Cold', stats='str=18,dex=80,int=7,fth=8,arc=9'):
    """Old vs new status_hp of one skill's ash-table rows, per weapon and grip, plus the per-hit
    build-up the new reader gives each hit."""
    st_ = {k: int(v) for k, v in (p.split('=') for p in stats.split(','))}
    armor = ['Verdigris Gauntlets', 'Verdigris Armor', 'Verdigris Greaves']
    A.mountable_skills = lambda t, wid, aff=0, level=25: [sid]
    tab = None
    for w in weapons:
        if tab is None:
            tab = T.Table(w, affinity, st_, armor, True)
        tab.set_weapon(w, affinity)
        seen = []
        real = tab.status_hp
        tab.status_hp = lambda two, hits, used: seen.append((two, hits, used)) or real(two, hits, used)
        rows = tab.rows((False, True))
        tab.status_hp = real
        for r, (two, hits, used) in zip(rows, seen):
            _, st, _ = tab.status_tables()
            ws = ST.weapon_status(st, tab.weapon, tab.affinity, tab.level, tab.stats, two)
            per = [(h['kind'], h['atk_row'], h.get('bullet'), tab.hit_buildup(st, ws, h['atk_row'], h.get('bullet'))[0])
                   for h in hits]
            print(w, r['grip'], r['cat'], 'old', old_status_hp(tab, two, hits, used), 'new', r['status_hp'],
                  'r1s', len(used), per,
                  'swings', tab.status_hp(two, [h for h in hits if h['kind'] == 'melee'], []),
                  'bullets', tab.status_hp(two, [h for h in hits if h['kind'] == 'bullet'], []),
                  'string', tab.status_hp(two, [], used))


if __name__ == '__main__':
    if sys.argv[1:2] == ['compare']:
        compare(int(sys.argv[2]), sys.argv[3].split(','), *sys.argv[4:5])
    elif sys.argv[1:2] == ['census']:
        census()
    elif sys.argv[1:2] == ['emit']:
        emit_types()
    elif sys.argv[1:2] == ['named']:
        named(sys.argv[2])
    elif sys.argv[1:2] == ['atks']:
        atks([int(x) for x in sys.argv[2:]])
    elif sys.argv[1:2] == ['bullets']:
        bullets([int(x) for x in sys.argv[2:]])
    elif sys.argv[1:2] == ['rows']:
        rows([int(x) for x in sys.argv[2:]])
    elif sys.argv[1:2] == ['dig']:
        dig(int(sys.argv[2]), sys.argv[3])
    else:
        dig()
