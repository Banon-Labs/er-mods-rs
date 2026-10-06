#!/usr/bin/env python3
"""Which ash a PvP build mounts on a weapon, against what the skill term says it is worth.

    python3 scripts/er-builds-ash-choice.py --pvp rank.json [--rl 150 --window 10] [--json out.json]

Evidence, not an objective: nothing here feeds back into the ranking (ashes-of-war.md section 18).

One choice per corpus slot: a weapon that takes ashes (`gemMountType` 2) and has a row in the
ranking, the mounted skill (`effective_skill`, the weapon's own when none is named) among the
skills that ranking row lists as `skill_term.available` (union over the weapon's rows). A slot
whose mounted skill is not in that set is counted and dropped.

1. Per skill: mounts, exposure (slots whose weapon could have taken it), where it sits (equip
   index 0 the primary right hand, 1-2 the right-hand swap slots, 3-5 the left hand) and which
   PvP tags its builds carry.
2. Per skill, across skills: Spearman of the mount rate (mounts / exposure) with each candidate
   feature.
3. A conditional logit over each slot's choice set, by slot group and by tag group, with a
   bootstrap over builds: the coefficient of the model's value (log of the option score over the
   row's moveset score) alone, then beside the candidate features.
"""

from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
MIRROR = Path.home() / '.cache/er-build-planner/builds.jsonl'
BOOT = 400
SEED = 20260930
GANG_TAGS = {'Co-op/Gank', '2v2'}
GROUPS = {'primary': (0,), 'swap': (1, 2), 'left': (3, 4, 5)}
FEATURES = ('value', 'own', 'dodge', 'buff', 'parry', 'fp')
VALUE_FLOOR = 0.1
#: Skills whose mount rate is split by tag group with a build bootstrap.
TAG_SKILLS = ("Bloodhound's Step", 'Endure', 'Quickstep', 'Parry', 'Flaming Strike', 'Sword Dance')
TAG_GROUPS = {'duels': lambda t: 'Duels' in t, 'invasions': lambda t: 'Invasions' in t,
              'gank/2v2': lambda t: bool(t & GANG_TAGS),
              'duels only': lambda t: 'Duels' in t and not (t & GANG_TAGS) and 'Invasions' not in t,
              'invasion or gank, no duels': lambda t: bool(t & (GANG_TAGS | {'Invasions'})) and 'Duels' not in t}


def _mod(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def be_nice():
    try:
        os.setpriority(os.PRIO_PROCESS, 0, 19)
    except OSError:
        pass
    try:
        subprocess.run(['ionice', '-c', '3', '-p', str(os.getpid())], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
    except OSError:
        pass


def corpus(mirror, rl_lo, rl_hi):
    """`corpus_slots(..., 'pvp')` with the build's tags kept."""
    embed = _mod('er_builds_embed', 'er-builds-embed.py')
    ash = _mod('er_mechanics_ashes', 'er-mechanics-ashes.py')
    out, seen = [], set()
    with open(mirror) as fh:
        for line in fh:
            row = json.loads(line)
            b = row['build']
            st = embed.stats_of(b)
            if st is None or not rl_lo <= st['rl'] <= rl_hi:
                continue
            tags = set(b.get('tags') or [])
            if b.get('isPvE') or 'PvE' in tags:
                continue
            if not (b.get('isPvE') is False or tags & ash.PVP_TAGS):
                continue
            if sum(st[k] for k in embed.ATTRS) - embed.LEVEL_OFFSET != st['rl']:
                continue
            key = (row.get('user'), tuple(embed.tokens(b)))
            if key in seen:
                continue
            seen.add(key)
            active = embed.active_set(b, 'weapons')
            slots = []
            for s in (b.get('inventory') or {}).get('slots') or []:
                es = s.get('equipSet')
                pos = (es[active] if active < len(es) else None) if isinstance(es, list) \
                    else s.get('equipIndex')
                if pos is not None and s.get('name'):
                    slots.append({**s, 'pos': pos, 'name': ash.plain(s['name'])})
            out.append({'tags': tags, 'slots': slots})
    return out


def options_by_weapon(results, t, ash):
    """{plain weapon: {skill: features}} from the ranking's `available` options, the value being
    the best over the weapon's rows of log(option score / row moveset score)."""
    out = collections.defaultdict(dict)
    for r in results:
        if r.get('kind') == 'unique':
            continue
        base = (r.get('moveset') or {}).get('base_score')
        if not base:
            continue
        for o in (r.get('skill_term') or {}).get('available') or []:
            sc = max(o.get('score') or 0.0, o.get('buff_score') or 0.0)
            # Floored at a tenth of the moveset: a skill with no scored option (score 0) would
            # otherwise sit at -12 and separate the logit on its own.
            v = math.log(max(sc, VALUE_FLOOR * base) / base)
            name = o['name']
            cls = set(o.get('classes') or [])
            prev = out[ash.plain(r['weapon'])].get(name)
            if prev is not None and prev['value'] >= v:
                continue
            ud = o.get('utility_detail') or {}
            out[ash.plain(r['weapon'])][name] = {
                'value': v, 'sid': o.get('sword_arts_id'),
                'dodge': float(o.get('utility') == 'dodge' or 'i-frames' in cls),
                'buff': float(o.get('utility') == 'endure' or (bool(cls & {'buff'}) and not cls & {'melee', 'bullet'})),
                'parry': float('parry' in cls),
                'distance': max(ud.get('distance') or [0.0]),
                'reach': o.get('reach') or 0.0,
            }
    return out


def choices(rows, opts, t, ash):
    """[(build index, slot group, tags, chosen, [(skill, feature dict)])], plus drop counts."""
    out, drop = [], collections.Counter()
    fp_cache = {}
    for bi, r in enumerate(rows):
        for s in r['slots']:
            try:
                wid = t.find_weapon(s['name'])
            except SystemExit:
                continue
            w = t.reg.weapon[wid]
            if w['gemMountType'] != 2:
                continue
            avail = opts.get(s['name'])
            if not avail:
                drop['weapon not ranked'] += 1
                continue
            name, _ = ash.effective_skill(t, s)
            if name not in avail:
                drop['skill not in the row'] += 1
                continue
            group = next((g for g, ps in GROUPS.items() if s['pos'] in ps), None)
            if group is None:
                continue
            own = t.arts_name(w['swordArtsParamId'])
            cand = []
            for n, f in avail.items():
                sid = f['sid']
                if sid not in fp_cache:
                    try:
                        fp_cache[sid] = float(t.fp_cost(sid) or 0)
                    except Exception:
                        fp_cache[sid] = 0.0
                cand.append((n, {**f, 'own': float(n == own), 'fp': fp_cache[sid] / 10.0}))
            out.append((bi, group, r['tags'], name, cand))
    return out, drop


def pad(data):
    """[(X (k, f), chosen)] -> X (n, K, f), mask (n, K), chosen (n,)."""
    k = max(x.shape[0] for x, _ in data)
    f = data[0][0].shape[1]
    X = np.zeros((len(data), k, f))
    M = np.zeros((len(data), k), dtype=bool)
    C = np.zeros(len(data), dtype=int)
    for i, (x, c) in enumerate(data):
        X[i, :len(x)] = x
        M[i, :len(x)] = True
        C[i] = c
    return X, M, C


def _probs(X, M, beta):
    u = np.where(M, X @ beta, -np.inf)
    u = u - u.max(1, keepdims=True)
    p = np.exp(u)
    return p / p.sum(1, keepdims=True), u


def clogit(P, l2=1e-3, iters=50):
    """Conditional logit by Newton on padded arrays `P` = (X, mask, chosen)."""
    X, M, C = P
    nf = X.shape[2]
    beta = np.zeros(nf)
    rows = np.arange(len(C))
    for _ in range(iters):
        p, _ = _probs(X, M, beta)
        m = np.einsum('nk,nkf->nf', p, X)
        g = (X[rows, C] - m).sum(0) - l2 * beta
        h = -np.einsum('nk,nkf,nkg->fg', p, X, X) + m.T @ m - l2 * np.eye(nf)
        step = np.linalg.solve(h, g)
        big = np.abs(step).max()
        if big > 2.0:
            step = step * (2.0 / big)
        beta -= step
        if np.abs(step).max() < 1e-8:
            break
    return beta


def loglik(P, beta):
    X, M, C = P
    p, _ = _probs(X, M, beta)
    return float(np.log(p[np.arange(len(C)), C]).sum())


def design(chs, feats):
    out = []
    for bi, group, tags, name, cand in chs:
        x = np.array([[f[k] for k in feats] for _, f in cand], dtype=float)
        c = [n for n, _ in cand].index(name)
        out.append((bi, x, c))
    return out


def fit_boot(rows_d, feats, boot, seed):
    data = [(x, c) for _, x, c in rows_d]
    if len(data) < 10:
        return None
    P = pad(data)
    beta = clogit(P)
    null = sum(-math.log(len(x)) for x, _ in data)
    ll = loglik(P, beta)
    builds = sorted({b for b, _, _ in rows_d})
    by_b = collections.defaultdict(list)
    for i, (b, _, _) in enumerate(rows_d):
        by_b[b].append(i)
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(boot):
        pick = rng.integers(0, len(builds), len(builds))
        sel = np.array([e for i in pick for e in by_b[builds[i]]])
        draws.append(clogit((P[0][sel], P[1][sel], P[2][sel]), iters=20))
    draws = np.array(draws)
    lo, hi = np.percentile(draws, (2.5, 97.5), axis=0)
    return {'n': len(data), 'builds': len(builds), 'pseudo_r2': 1 - ll / null,
            'coef': {k: [float(beta[i]), float(lo[i]), float(hi[i])] for i, k in enumerate(feats)}}


def spearman(x, y):
    rx = np.argsort(np.argsort(x)).astype(float)
    ry = np.argsort(np.argsort(y)).astype(float)
    if rx.std() == 0 or ry.std() == 0:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


#: Fight formats for `--format-check`, read off the build's tags.
FORMATS = {
    'all': lambda t: True,
    'duels only': lambda t: 'Duels' in t and not (t & (GANG_TAGS | {'Invasions'})),
    'any duels': lambda t: 'Duels' in t,
    'invasion or gank, no duels': lambda t: bool(t & (GANG_TAGS | {'Invasions'})) and 'Duels' not in t,
}


def format_check(results, rows, fmt, ash, boot, seed=SEED):
    """`er-mechanics-ashes.ash_adoption_check` with primary adoption counted only in builds of
    fight format `fmt`: log1p(primary adoption) ~ class dummies + within-class score percentile
    + ash. Primary = the weapon at equip index 0 of the active set."""
    gap = _mod('er_builds_adoption_gap', 'er-builds-adoption-gap.py')
    weapon_rows, _ = gap.weight_tables()
    wep_type = {ash.plain(n): r['wepType'] for n, r in weapon_rows.items()}
    pred = FORMATS[fmt]
    adoption = collections.Counter()
    builds = 0
    for r in rows:
        if not pred(r['tags']):
            continue
        builds += 1
        prim = next((s['name'] for s in r['slots'] if s['pos'] == 0), None)
        if prim:
            adoption[prim] += 1
    best = {}
    for r in results:
        name = ash.plain(r['weapon'])
        score = (r.get('moveset') or {}).get('score')
        if score is None or name not in wep_type:
            continue
        unique = r.get('kind') == 'unique'
        prev = best.get(name)
        if prev is None or score > prev[0]:
            best[name] = (score, unique or (prev[1] if prev else False))
        elif unique:
            best[name] = (prev[0], True)
    names = sorted(best)
    by_class = collections.defaultdict(list)
    for n in names:
        by_class[wep_type[n]].append(n)
    pct = {}
    for members in by_class.values():
        scores = sorted(best[n][0] for n in members)
        for n in members:
            s = best[n][0]
            lo = scores.index(s)
            hi = len(scores) - 1 - scores[::-1].index(s)
            pct[n] = 0.5 if len(scores) == 1 else ((lo + hi) / 2) / (len(scores) - 1)
    classes = sorted(by_class)
    col = {c: i for i, c in enumerate(classes)}
    x = np.zeros((len(names), len(classes) + 2))
    y = np.zeros(len(names))
    for i, n in enumerate(names):
        x[i, col[wep_type[n]]] = 1.0
        x[i, -2] = pct[n]
        x[i, -1] = 0.0 if best[n][1] else 1.0
        y[i] = math.log1p(adoption.get(n, 0))

    def fit(idx):
        coef, *_ = np.linalg.lstsq(x[idx], y[idx], rcond=None)
        return coef[-2], coef[-1]

    b_pct, b_ash = fit(np.arange(len(names)))
    rng = np.random.default_rng(seed)
    draws = np.array([fit(rng.integers(0, len(names), len(names))) for _ in range(boot)])
    return {'format': fmt, 'builds': builds, 'adopted': int(sum(adoption.get(n, 0) > 0 for n in names)),
            'ash_coef': float(b_ash), 'ash_ci': [float(v) for v in np.percentile(draws[:, 1], (2.5, 97.5))],
            'pct_coef': float(b_pct), 'pct_ci': [float(v) for v in np.percentile(draws[:, 0], (2.5, 97.5))]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--format-check', action='store_true',
                    help='only the weapon-level ash check, primary adoption split by fight format')
    ap.add_argument('--pvp', required=True)
    ap.add_argument('--mirror', default=str(MIRROR))
    ap.add_argument('--rl', type=int, default=150)
    ap.add_argument('--window', type=int, default=10)
    ap.add_argument('--boot', type=int, default=BOOT)
    ap.add_argument('--min-exposure', type=int, default=30)
    ap.add_argument('--json')
    a = ap.parse_args()
    be_nice()
    ash = _mod('er_mechanics_ashes', 'er-mechanics-ashes.py')
    t = ash.AshTables()
    with open(a.pvp) as fh:
        results = json.load(fh)['results']
    if a.format_check:
        rows = corpus(a.mirror, a.rl - a.window, a.rl + a.window)
        out = []
        for fmt in FORMATS:
            c = format_check(results, rows, fmt, ash, max(a.boot, 1000))
            out.append(c)
            print(f"{fmt:28} builds {c['builds']:4} adopted {c['adopted']:3}  ash {c['ash_coef']:+.3f} "
                  f"[{c['ash_ci'][0]:+.3f},{c['ash_ci'][1]:+.3f}]  pct {c['pct_coef']:+.3f} "
                  f"[{c['pct_ci'][0]:+.3f},{c['pct_ci'][1]:+.3f}]")
        if a.json:
            with open(a.json, 'w') as fh:
                json.dump(out, fh, indent=1)
        return
    opts = options_by_weapon(results, t, ash)
    report = {}
    for label, lo, hi in (('window', a.rl - a.window, a.rl + a.window), ('all RL', 1, 713)):
        rows = corpus(a.mirror, lo, hi)
        chs, drop = choices(rows, opts, t, ash)
        print(f'\n=== {label} (RL {lo}-{hi}): {len(rows)} builds, {len(chs)} choices; dropped {dict(drop)}')
        # 1. per skill
        st = collections.defaultdict(lambda: {'mounts': 0, 'exposure': 0, 'own': 0, 'groups': collections.Counter(),
                                              'gang': 0, 'inv': 0, 'duel': 0, 'feat': None, 'pct': []})
        base = collections.Counter()
        for bi, group, tags, name, cand in chs:
            vals = sorted(f['value'] for _, f in cand)
            for n, f in cand:
                e = st[n]
                e['exposure'] += 1
                e['feat'] = f
                if n == name:
                    e['mounts'] += 1
                    e['own'] += int(f['own'])
                    e['groups'][group] += 1
                    e['gang'] += bool(tags & GANG_TAGS)
                    e['inv'] += 'Invasions' in tags
                    e['duel'] += 'Duels' in tags
                    e['pct'].append(vals.index(f['value']) / max(1, len(vals) - 1))
            base['n'] += 1
            base['gang'] += bool(tags & GANG_TAGS)
            base['inv'] += 'Invasions' in tags
            base['duel'] += 'Duels' in tags
        print(f"all choices: gang {base['gang'] / base['n']:.2f} invasions {base['inv'] / base['n']:.2f} "
              f"duels {base['duel'] / base['n']:.2f}")
        top = sorted(st.items(), key=lambda kv: -kv[1]['mounts'])[:25]
        print(f"{'skill':28} mnt  expo  rate  own  prim swap left  gang  inv duel  mdl-pct  value  fp")
        for n, e in top:
            g = e['groups']
            m = max(1, e['mounts'])
            print(f"{n[:28]:28} {e['mounts']:4} {e['exposure']:5} {e['mounts'] / e['exposure']:.3f} {e['own']:4} "
                  f"{g['primary']:4} {g['swap']:4} {g['left']:4}  {e['gang'] / m:.2f} {e['inv'] / m:.2f} {e['duel'] / m:.2f}"
                  f"  {np.mean(e['pct']) if e['pct'] else float('nan'):.2f}  {e['feat']['value']:+.2f} {e['feat']['fp'] * 10:3.0f}")
        # 1b. mount rate by tag group, bootstrap over builds
        rng = np.random.default_rng(SEED)
        tag_rates = {}
        print('\nmount rate (mounts / exposure) by tag group, [95% CI over builds]; build share = builds carrying it:')
        bix = sorted({c[0] for c in chs})
        for sk in TAG_SKILLS:
            line = []
            for gname, pred in TAG_GROUPS.items():
                per_b = collections.defaultdict(lambda: [0, 0])
                for bi, group, tags, name, cand in chs:
                    if pred(tags) and any(n == sk for n, _ in cand):
                        per_b[bi][0] += name == sk
                        per_b[bi][1] += 1
                if not per_b:
                    continue
                arr = np.array(list(per_b.values()), dtype=float)
                rate_g = arr[:, 0].sum() / arr[:, 1].sum()
                share = float((arr[:, 0] > 0).mean())
                bs = []
                for _ in range(a.boot):
                    s = arr[rng.integers(0, len(arr), len(arr))]
                    bs.append(s[:, 0].sum() / s[:, 1].sum())
                lo_, hi_ = np.percentile(bs, (2.5, 97.5))
                tag_rates.setdefault(sk, {})[gname] = [rate_g, float(lo_), float(hi_), share, len(arr)]
                line.append(f'{gname} {rate_g:.3f} [{lo_:.3f},{hi_:.3f}] b{share:.2f} n{len(arr)}')
            print(f'  {sk[:20]:20} ' + '; '.join(line))
        del bix
        # 2. across skills
        names =[n for n, e in st.items() if e['exposure'] >= a.min_exposure]
        rate = np.array([st[n]['mounts'] / st[n]['exposure'] for n in names])
        cand_feats = {
            'model value': [st[n]['feat']['value'] for n in names],
            'dodge (i-frames)': [st[n]['feat']['dodge'] for n in names],
            'buff': [st[n]['feat']['buff'] for n in names],
            'parry': [st[n]['feat']['parry'] for n in names],
            'fp cost (low = +)': [-st[n]['feat']['fp'] for n in names],
            'move distance (dodges)': [st[n]['feat']['distance'] for n in names],
            'reach': [st[n]['feat']['reach'] for n in names],
            'gang lift': [(st[n]['gang'] / max(1, st[n]['mounts'])) - base['gang'] / base['n'] for n in names],
            'invasion lift': [(st[n]['inv'] / max(1, st[n]['mounts'])) - base['inv'] / base['n'] for n in names],
            'swap-slot share': [st[n]['groups']['swap'] / max(1, st[n]['mounts']) for n in names],
        }
        sp = {k: spearman(np.array(v, dtype=float), rate) for k, v in cand_feats.items()}
        print(f'\nSpearman with mount rate over {len(names)} skills (exposure >= {a.min_exposure}):')
        for k, v in sp.items():
            print(f'  {k:24} {v if v is None else round(v, 3)}')
        # 3. conditional logit
        d_all = design(chs, FEATURES)
        idx = {id(c): i for i, c in enumerate(chs)}
        fits = {}
        subsets = {'every slot': chs}
        for g in GROUPS:
            subsets[g] = [c for c in chs if c[1] == g]
        subsets['gang/2v2 builds'] = [c for c in chs if c[2] & GANG_TAGS]
        subsets['invasion builds'] = [c for c in chs if 'Invasions' in c[2]]
        subsets['duel builds'] = [c for c in chs if 'Duels' in c[2]]
        print('\nconditional logit (coef [95% CI over builds]):')
        for sname, sub in subsets.items():
            rows_d = [d_all[idx[id(c)]] for c in sub]
            for feats in (('value',), FEATURES):
                cols = [FEATURES.index(k) for k in feats]
                rd = [(b, x[:, cols], c) for b, x, c in rows_d]
                f = fit_boot(rd, feats, a.boot, SEED)
                fits[f'{sname} | {"+".join(feats)}'] = f
                if f is None:
                    continue
                cs = '  '.join(f"{k} {v[0]:+.2f} [{v[1]:+.2f},{v[2]:+.2f}]" for k, v in f['coef'].items())
                print(f"  {sname:16} n {f['n']:4} R2 {f['pseudo_r2']:.3f}  {cs}")
        report[label] = {'drop': dict(drop), 'spearman': sp, 'logit': fits, 'tag_rates': tag_rates,
                         'skills': {n: {k: (dict(v) if isinstance(v, collections.Counter) else v)
                                        for k, v in e.items() if k not in ('pct', 'feat')}
                                    | {'value': e['feat']['value'], 'fp': e['feat']['fp'] * 10}
                                    for n, e in top}}
    if a.json:
        with open(a.json, 'w') as fh:
            json.dump(report, fh, indent=1, default=float)


if __name__ == '__main__':
    main()
