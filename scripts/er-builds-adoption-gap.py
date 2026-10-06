#!/usr/bin/env python3
"""Adoption of STR PvP weapons in the planner corpus against `er-builds-pvp.py`'s per-attack model.

    python3 scripts/er-builds-pvp.py --rl 150 --json > pvp150.json
    python3 scripts/er-builds-adoption-gap.py --pvp pvp150.json

Written for docs/er-mechanics/giant-crusher-adoption-gap.md. Three corpus filters, all RL 140-160
and not PvE, deduplicated on (user, equipped tokens) as `er-builds-embed.load_corpus` does:
`tag` (Strength tag), `pvptag` (Strength plus Invasions, Duels, Co-op/Gank, 2v2 or Ladder) and
`str60` (STR 60 or more, whatever the tags). Right hand is `equipIndex` 0-2, left 3-5 (the
corpus puts seals and shields at 3-5).

Equip load is recomputed from `EquipParamWeapon.weight` and `EquipParamProtector.weight` over the
active set, because the planner stores only `maxEquipLoad`. Poise is `computed.poise.altered`
(Bull-Goat applied) when present, else `original`, in menu units.
"""

from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import statistics
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = Path.home() / '.cache/er-build-planner'
PVP_TAGS = {'Invasions', 'Duels', 'Co-op/Gank', '2v2', 'Ladder'}
#: Roll thresholds as fractions of max equip load (COMMUNITY: 30% light, 70% medium).
LIGHT, MEDIUM = 0.299, 0.699
FILTERS = ('tag', 'pvptag', 'str60')


def _sibling(name: str):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), HERE / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


EMBED = _sibling('er-builds-embed')
AR = _sibling('er-mechanics-ar')
EPR = AR.EPR


def weight_tables():
    files = EPR.load(None)
    wrows, _, _ = EPR.rows(EPR.param_bytes(files, 'EquipParamWeapon'), ['weight', 'wepType'])
    wnames = EPR.row_names('EquipParamWeapon')
    weapon = {}
    for r in sorted(wrows, key=lambda r: r['id']):
        n = wnames.get(r['id'])
        if n and r['id'] % 10000 == 0 and n not in weapon:
            weapon[n] = r
    prows, _, _ = EPR.rows(EPR.param_bytes(files, 'EquipParamProtector'), ['weight'])
    pnames = EPR.row_names('EquipParamProtector')
    prot = {}
    for r in prows:
        n = pnames.get(r['id'])
        if n and n not in prot:
            prot[n] = r['weight']
    return weapon, prot


def plain_name(name: str) -> str:
    """The planner writes `Miséricorde` and `Great Épée`; the regulation row names drop the accents."""
    return ''.join(c for c in unicodedata.normalize('NFKD', name) if not unicodedata.combining(c))


def slots_at(slots, active):
    """(position, slot) pairs worn in set `active` (same rule as `er-builds-embed.equipped`)."""
    out = []
    for s in slots or []:
        es = s.get('equipSet')
        p = (es[active] if active < len(es) else None) if isinstance(es, list) else s.get('equipIndex')
        if p is not None and s.get('name'):
            out.append((p, {**s, 'name': plain_name(s['name'])}))
    return out


def corpus(mirror, kind, rl_lo, rl_hi, weapon, prot):
    out, seen, why = [], set(), collections.Counter()
    for line in mirror.open():
        row = json.loads(line)
        b = row['build']
        st = EMBED.stats_of(b)
        if st is None or not rl_lo <= st['rl'] <= rl_hi:
            continue
        tags = set(b.get('tags') or [])
        if b.get('isPvE') or 'PvE' in tags:
            continue
        if kind == 'tag' and 'Strength' not in tags:
            continue
        if kind == 'pvptag' and not ('Strength' in tags and tags & PVP_TAGS):
            continue
        if kind == 'str60' and st['str'] < 60:
            continue
        if sum(st[k] for k in EMBED.ATTRS) - EMBED.LEVEL_OFFSET != st['rl']:
            why['RL disagrees with attributes'] += 1
            continue
        key = (row.get('user'), tuple(EMBED.tokens(b)))
        if key in seen:
            why['duplicate'] += 1
            continue
        seen.add(key)
        wep = slots_at((b.get('inventory') or {}).get('slots'), EMBED.active_set(b, 'weapons'))
        pa = EMBED.active_set(b, 'protectors')
        armor = [s['name'] for part in ('head', 'body', 'arms', 'legs')
                 for _, s in slots_at(((b.get('protectors') or {}).get(part) or {}).get('slots'), pa)]
        c = b.get('computed') or {}
        po = c.get('poise') or {}
        tools = [s.get('name') or '' for s in ((b.get('items') or {}).get('tools') or {}).get('slots') or []]
        out.append({
            'user': row.get('user'), 'date': str(row.get('updatedAt'))[:7], 'st': st, 'is2h': b.get('is2h'),
            'right': [s for p, s in wep if p in (0, 1, 2)], 'primary': next((s['name'] for p, s in wep if p == 0), None), 'weps': [s for _, s in wep],
            'load': sum(weapon.get(s['name'], {}).get('weight', 0) for _, s in wep) + sum(prot.get(a, 0) for a in armor),
            'armor_load': sum(prot.get(a, 0) for a in armor), 'max_load': c.get('maxEquipLoad'), 'poise': po.get('altered', po.get('original')),
            'stamina': c.get('maxStamina'), 'grease': sorted({t for t in tools if 'Grease' in t}),
            'unknown': [a for a in armor if a not in prot] + [s['name'] for _, s in wep if s['name'] not in weapon]})
    return out, why


def pct(xs, qs=(10, 25, 50, 75, 90)):
    xs = sorted(x for x in xs if x is not None)
    return {q: round(xs[min(len(xs) - 1, int(q / 100 * len(xs)))], 2) for q in qs}


def ranks(v):
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(v):
        j = i
        while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(a, b):
    ra, rb = ranks(a), ranks(b)
    ma, mb = statistics.mean(ra), statistics.mean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den if den else float('nan')


def describe(kind, rs, why, weapon, sweep):
    n = len(rs)
    users = collections.Counter(r['user'] for r in rs)
    right = collections.Counter()
    for r in rs:
        right.update({s['name'] for s in r['right']})
    per_user = collections.defaultdict(set)
    for r in rs:
        per_user[r['user']].update(s['name'] for s in r['right'])
    uvote = collections.Counter(w for ws in per_user.values() for w in ws)
    gc = [r for r in rs if 'Giant-Crusher' in {s['name'] for s in r['right']}]
    print(f'\n===== filter {kind}: n={n} {dict(why)}; users {len(users)}; top user {users.most_common(1)}; '
          f'users with 3+ builds {sum(1 for v in users.values() if v >= 3)} holding '
          f'{sum(v for v in users.values() if v >= 3)} builds')
    print(f'Giant-Crusher (right hand): {len(gc)}/{n} = {100 * len(gc) / n:.1f}%; one vote per user '
          f'{uvote["Giant-Crusher"]}/{len(per_user)}; dates {[r["date"] for r in gc]}; '
          f'infusions {[s.get("infusion") for r in gc for s in r["right"] if s["name"] == "Giant-Crusher"]}; '
          f'grease {[r["grease"] for r in gc]}')
    print('top right-hand weapons: builds, share, users, sweep?, wepType, weight')
    for w, c in right.most_common(25):
        print(f'  {w:<34}{c:>4} {100 * c / n:5.1f}% {uvote[w]:>4}  {"sweep" if w in sweep else "not in sweep":<13}'
              f'{weapon.get(w, {}).get("wepType", "?"):>4} {weapon.get(w, {}).get("weight", "?"):>6}')
    occ = [s['name'] for r in rs for s in r['right'] if s['name'] in weapon]
    outside = [w for w in occ if w not in sweep]
    b_out = sum(1 for r in rs if any(s['name'] in weapon and s['name'] not in sweep for s in r['right']))
    print(f'right-hand slots outside the grease sweep: {len(outside)}/{len(occ)} = {100 * len(outside) / len(occ):.1f}%; '
          f'builds with one or more: {b_out}/{n} = {100 * b_out / n:.1f}%')
    wt = collections.Counter(weapon[w]['wepType'] for w in occ)
    ex = {}
    for w in occ:
        ex.setdefault(weapon[w]['wepType'], collections.Counter())[w] += 1
    print('wepType share of right-hand slots:')
    for t, c in wt.most_common(12):
        print(f'  {t:>3} {100 * c / len(occ):5.1f}%  e.g. {ex[t].most_common(2)}')
    print('infusions of sweep weapons (right hand):',
          collections.Counter(s.get('infusion') for r in rs for s in r['right'] if s['name'] in sweep).most_common(8))
    g = [r for r in rs if r['grease']]
    print(f'builds with any grease among their tools: {len(g)}/{n} = {100 * len(g) / n:.1f}%;',
          collections.Counter(t for r in rs for t in r['grease']).most_common(6))
    ratio = [r['load'] / r['max_load'] for r in rs if r['max_load']]
    print('equip load ratio', pct(ratio), f'light {sum(x <= LIGHT for x in ratio)} medium '
          f'{sum(LIGHT < x <= MEDIUM for x in ratio)} heavy {sum(x > MEDIUM for x in ratio)} of {len(ratio)}')
    for cand in ('Giant-Crusher', 'Greatsword'):
        fit = tot = 0
        for r in rs:
            if not r['max_load']:
                continue
            tot += 1
            prim = max((weapon.get(s['name'], {}).get('weight', 0) for s in r['right']), default=0)
            fit += (r['load'] - prim + weapon[cand]['weight']) / r['max_load'] <= MEDIUM
        print(f'stay at medium roll after swapping the heaviest right-hand weapon for {cand}: {fit}/{tot}')
    print('armor-only weight', pct(r['armor_load'] for r in rs), '| max equip load', pct(r['max_load'] for r in rs))
    print('unresolved names:', collections.Counter(u for r in rs for u in r['unknown']).most_common(4))
    print('poise', pct(r['poise'] for r in rs), '| endurance', pct(r['st']['vit'] for r in rs),
          '| max stamina', pct(r['stamina'] for r in rs), '| STR', pct(r['st']['str'] for r in rs))
    print('is2h', dict(collections.Counter(r['is2h'] for r in rs)), '| year',
          sorted(collections.Counter(r['date'][:4] for r in rs).items()))
    primary = collections.Counter(r['primary'] for r in rs if r['primary'])
    print(f'primary slot (right hand position 0), {sum(primary.values())} builds; Giant-Crusher {primary["Giant-Crusher"]}:',
          [(w, c) for w, c in primary.most_common(12)])
    return right, primary


def features(slot, weapon_row, poises):
    s = slot
    return {
        'r1 damage': s['dmg'],
        'r1 damage per chain cycle': s['dmg'] / (s['next'] or 999),
        'r1 startup (lower first)': -(s['startup'] or 99),
        'r1 roll frame (lower first)': -(s['roll'] or 99),
        'r1 roll after last hit (lower first)': -((s['roll'] or 99) - (s['startup'] or 0) - (s['active'] or 0)),
        'r1 poise': s['poise'],
        'r1 staggers defender share': sum(p < s['poise'] for p in poises) / len(poises),
        'r1 stamina (lower first)': -s['stamina'],
        'weight (lower first)': -weapon_row['weight'],
        'r1 hyperarmor': s['hyperarmor'],
    }


def join(model, right, rs, weapon, sweep, two, what):
    poises = sorted(r['poise'] for r in rs if r['poise'] is not None)
    names, adopt, feats = [], [], collections.defaultdict(list)
    for w in sorted(sweep):
        m = model.get((w, two))
        if not m or 'r1_1' not in m['slots']:
            continue
        names.append(w)
        adopt.append(right[w])
        for k, v in features(m['slots']['r1_1'], weapon[w], poises).items():
            feats[k].append(v)
        r2 = m['slots'].get('r2_1')
        feats['r2 damage'].append(r2['dmg'] if r2 else 0)
        feats['r2 startup (lower first)'].append(-(r2['startup'] or 99) if r2 else -99)
    adopted = [i for i, a in enumerate(adopt) if a]
    grip = '2H' if two else '1H'
    print(f'\n===== {grip} r1_1 against adoption ({what}): {len(names)} sweep weapons, {len(adopted)} adopted. Spearman rho')
    for k, v in feats.items():
        print(f'  {k:<40} all {spearman(v, adopt):+.2f}   adopted only '
              f'{spearman([v[i] for i in adopted], [adopt[i] for i in adopted]):+.2f}')
    order = {k: {i: p + 1 for p, i in enumerate(sorted(range(len(names)), key=lambda i: -v[i]))} for k, v in feats.items()}
    dmg = sorted(range(len(names)), key=lambda i: -feats['r1 damage'][i])
    print(f'  adoption held by the model top 20 by r1 damage: {sum(adopt[i] for i in dmg[:20])}; '
          f'by its bottom half: {sum(adopt[i] for i in dmg[len(names) // 2:])}')
    print('  model top 10 by r1 damage (adoption):', [(names[i], adopt[i]) for i in dmg[:10]])
    top = sorted(range(len(names)), key=lambda i: -adopt[i])[:12]
    cols = ('r1 damage', 'r1 damage per chain cycle', 'r1 startup (lower first)', 'r1 roll frame (lower first)', 'r1 poise')
    print(f'  {"most adopted":<30}{"n":>4}' + ''.join(f'{c[:14]:>16}' for c in cols))
    for i in top + [names.index('Giant-Crusher')]:
        print(f'  {names[i][:29]:<30}{adopt[i]:>4}' + ''.join(f'{order[c][i]:>16}' for c in cols))
    gi = names.index('Giant-Crusher')
    print(f'  Giant-Crusher r1 staggers {100 * feats["r1 staggers defender share"][gi]:.0f}% of defenders in one hit; '
          f'median weapon {100 * statistics.median(feats["r1 staggers defender share"]):.0f}%')


def trade(model, right, rs):
    """How often an opponent's R1 breaks Giant-Crusher's R1 hyperarmor in one hit, with and without
    the PvP ToughnessParam `unk1` reduction (attacks.md section 2)."""
    pmed = statistics.median(r['poise'] for r in rs if r['poise'] is not None)
    for two in (True, False):
        s = model[('Giant-Crusher', two)]['slots']['r1_1']
        pool = []
        for w, c in right.items():
            m = model.get((w, two))
            if m and 'r1_1' in m['slots']:
                pool += [m['slots']['r1_1']['poise']] * c
        thr = pmed + s['hyperarmor']
        for label, mult in (('no PvP reduction', 1.0), ('PvP unk1 0.45', 0.45)):
            broken = sum(p * mult >= thr for p in pool) / len(pool)
            print(f'GC {"2H" if two else "1H"} R1 hyperarmor +{s["hyperarmor"]:.0f} on median poise {pmed}: {label}: '
                  f'{100 * broken:.0f}% of adoption-weighted opponent R1s break it in one hit (n={len(pool)})')


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--pvp', type=Path, required=True, help='er-builds-pvp.py --json output')
    ap.add_argument('--mirror', type=Path, default=CACHE / 'builds.jsonl')
    ap.add_argument('--rl-lo', type=int, default=140)
    ap.add_argument('--rl-hi', type=int, default=160)
    a = ap.parse_args()
    weapon, prot = weight_tables()
    doc = json.load(a.pvp.open())
    # `er-builds-pvp.py --json` writes {rl, defenders, distribution, results}; older dumps were the bare list.
    model = {(x['weapon'], x['two']): x for x in (doc['results'] if isinstance(doc, dict) else doc)}
    sweep = {x['weapon'] for x in model.values()}
    kept = {}
    for kind in FILTERS:
        rs, why = corpus(a.mirror, kind, a.rl_lo, a.rl_hi, weapon, prot)
        kept[kind] = (rs, describe(kind, rs, why, weapon, sweep))
    rs, (right, primary) = kept['tag']
    for two in (True, False):
        join(model, right, rs, weapon, sweep, two, 'any right-hand slot')
    join(model, primary, rs, weapon, sweep, True, 'primary slot')
    trade(model, right, rs)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
