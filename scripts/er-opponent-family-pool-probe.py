#!/usr/bin/env python3
"""Probe: the neutral contest (`er-mechanics-neutral.neutral_exchange`) against the R1-only
opponent pool versus a pool that throws each weapon's moveset family openers at their use share.

    python3 scripts/er-opponent-family-pool-probe.py <ranking.json> [limit] > out.jsonl

Read-only. The family pool is built from a stored `er-builds-pvp.py --sort score --json` ranking:
each exchange-pool build's weapon and grip is looked up in it, and every family's best opener
(`moveset.families[*].opener`, jump openers synthesized with `er-builds-pvp.jump_openers`) enters
at its share, renormalised over the openers that have a `neutral_in`. Each row's family scores are
then re-derived with the new contest factor (the depth-0 arithmetic of `slot_score`; a family whose
engagement has follow-up links keeps its link part as stored, so those rows are approximate) and
combined with the matching mean. Prints one JSON object per line: pool info first, then a row per
ranking row.
"""
import collections
import importlib.util
import json
import sys
import time

import numpy as np

HERE = __import__("pathlib").Path(__file__).resolve().parent


def load(n):
    spec = importlib.util.spec_from_file_location(n.replace('-', '_'), HERE / f'{n}.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


pvp = load('er-builds-pvp')
EX, NEUT = pvp.EXCH, pvp.NEUT
SRC = sys.argv[1]
LIMIT = int(sys.argv[2]) if len(sys.argv) > 2 else 0
raw = EX.opponent_pool(None, EX.CACHE / 'builds.jsonl', 140, 160)
pool = EX.Pool(raw)
npool = NEUT.NeutralPool(pool, NEUT.pool_reaches(raw))
res = json.load(open(SRC))['results']
by = {f"{EX.plain_name(r['weapon'])}|{'2h' if r['two'] else '1h'}": r for r in res}
_JUMP = {}


def slot_of(r, key):
    s = r['slots'].get(key)
    if s is None and key.startswith('jump_'):
        k = id(r)
        if k not in _JUMP:
            _JUMP[k] = pvp.jump_openers(r['slots'], npool)
        s = _JUMP[k].get(key)
    return s or {}


def opener_inputs(r, key):
    ni = slot_of(r, key).get('neutral_in')
    if not ni or not ni.get('reach') or ni.get('strike') is None:
        return None
    return (float(ni['strike']), float(ni['reach']), float(ni['poise'] or 0.0),
            [tuple(h) for h in ni['hyper']], float(ni.get('active') or 3.0))


class WPool:
    """Rows = (profile, opener); entries = (build, row) with weight share / builds."""

    def __init__(self, family):
        rows, idx = [], {}
        ent_row, ent_w, ent_poise = [], [], []
        self.cover = collections.Counter()
        self.opener_w = collections.Counter()
        nb = len(raw['builds'])
        for key, poise in raw['builds']:
            pk = pool.keys.index(key)
            r = by.get(key)
            opts = []
            if family and r:
                for fn, f in ((r.get('moveset') or {}).get('families') or {}).items():
                    if not f.get('share'):
                        continue
                    inp = opener_inputs(r, f['opener'])
                    if inp:
                        opts.append((f['share'], (key, f['opener']), inp))
                    else:
                        self.cover['dropped:' + fn] += f['share'] / nb
                tot = sum(o[0] for o in opts)
                opts = [(w / tot, k, i) for w, k, i in opts] if tot else []
            if not opts:
                opts = [(1.0, (key, 'r1_1'), (pool.startup[pk], npool.reach[pk], pool.poise_dealt[pk],
                                              pool.hyper[pk], npool.active[pk]))]
                if family:
                    self.cover['r1-fallback builds'] += 1
            for w, ident, inp in opts:
                if ident not in idx:
                    idx[ident] = len(rows)
                    rows.append(inp)
                ent_row.append(idx[ident])
                ent_w.append(w / nb)
                ent_poise.append(poise)
                self.opener_w[ident[1]] += w / nb
        self.rows = rows
        self.startup = np.array([x[0] for x in rows])
        self.reach = np.array([x[1] for x in rows])
        self.poise_dealt = np.array([x[2] for x in rows])
        self.hyper = [x[3] for x in rows]
        self.active = np.array([x[4] for x in rows])
        self.er = np.array(ent_row)
        self.ew = np.array(ent_w)
        self.ep = np.array(ent_poise)
        self.my_poise = pool.my_poise


def wneutral(P, strike, reach, poise, hyper, active=3.0):
    """`NEUT.neutral_exchange` with weighted entries (same arithmetic)."""
    reach = float(reach)
    dist = np.maximum(P.reach, reach)
    my_start, my_ifr = NEUT.arrival(dist - reach, NEUT.DEFAULT_TOOLS, npool.k)
    their_start, their_ifr = NEUT.arrival(dist - P.reach, npool.tools, npool.k)
    mine = my_start + strike
    theirs = their_start + P.startup
    they_miss = (my_ifr > 0) & (theirs >= 0) & (theirs + P.active <= my_ifr)
    i_miss = (their_ifr > 0) & (mine >= 0) & (mine + active <= their_ifr)
    first = ((mine < theirs) & ~i_miss) | (they_miss & ~i_miss)
    second = ((mine > theirs) & ~they_miss) | (i_miss & ~they_miss)
    their = [NEUT._window_at(h, t - s) for h, t, s in zip(P.hyper, mine, their_start)]
    tb = np.array([b for b, _ in their])[P.er]
    tm = np.array([m for _, m in their])[P.er]
    breaks = poise * tm >= P.ep + tb
    my_w = [NEUT._window_at(hyper, t - s) for t, s in zip(theirs, my_start)]
    dealt = P.poise_dealt * np.array([m for _, m in my_w])
    room = dealt - np.array([b for b, _ in my_w])
    pbm = np.searchsorted(P.my_poise, room, side='right') / len(P.my_poise)
    fb, sb = first[P.er], second[P.er]
    wb, iwb = (they_miss & ~i_miss)[P.er], (i_miss & ~they_miss)[P.er]
    W = P.ew.sum()
    win = float((P.ew * (fb & (breaks | wb))).sum() / W)
    loss = float((P.ew * np.where(sb, np.where(iwb, 1.0, pbm[P.er]), 0.0)).sum() / W)
    return 1.0 + EX.EXCHANGE_WEIGHT * (win - loss), win, loss


def emit(obj):
    print(json.dumps(obj), flush=True)


t0 = time.time()
R1, FAM = WPool(False), WPool(True)
info = {'rows_r1': len(R1.rows), 'rows_fam': len(FAM.rows), 'cover': dict(FAM.cover),
        'opener_share_fam': {k: round(v, 3) for k, v in FAM.opener_w.most_common()}}
for nm, P in (('r1', R1), ('fam', FAM)):
    w = np.bincount(P.er, weights=P.ew, minlength=len(P.rows))
    w /= w.sum()
    info[nm] = {'strike': round(float((w * P.startup).sum()), 2), 'reach': round(float((w * P.reach).sum()), 3),
                'poise_dealt': round(float((w * P.poise_dealt).sum()), 1),
                'hyper_share': round(float(w[[bool(h) for h in P.hyper]].sum()), 3)}
chk = []
for r in res[:20]:
    inp = opener_inputs(r, 'r1_1')
    if inp:
        a = NEUT.neutral_exchange(npool, *inp)['f_neutral']
        chk.append(abs(a - wneutral(R1, *inp)[0]))
info['validation_max_abs'] = max(chk)
emit({'info': info})

FPS, CW = pvp.SCORE_FPS, pvp.SCORE_CRIT_WEIGHT
for r in (res[:LIMIT] if LIMIT else res):
    fams = (r.get('moveset') or {}).get('families') or {}
    sc = {'r1': [], 'fam': []}
    per = {}
    for fn, f in fams.items():
        d = f.get('detail') or {}
        inp = opener_inputs(r, f['opener'])
        f_old, hw_old = d.get('f_contest'), d.get('hit_worth')
        if not inp or not d.get('rate') or f_old is None or hw_old is None or d.get('f_interrupt', 1.0) != 1.0:
            for k in sc:
                sc[k].append(f['score'])
            continue
        react = slot_of(r, f['opener']).get('react') or {}
        for nm, P in (('r1', R1), ('fam', FAM)):
            fn_new = wneutral(P, *inp)[0]
            per.setdefault(fn, {})[nm] = round(fn_new, 4)
            if not react:
                sc[nm].append(f['score'] * fn_new / f_old)
                continue
            hw_new = hw_old + (1.0 - react.get('react_share', 0.0)) * (fn_new - f_old)
            fs, crit = d.get('f_sustain', 1.0), d.get('crit_hp', 0)
            parry, whiff = d.get('parry_hp', 0), d.get('whiff_hp', 0)
            num = d['rate'] * d['commit'] / FPS
            D = ((num + parry + whiff) / fs - CW * crit) / hw_old if hw_old else 0.0
            num_new = fs * (hw_new * D + CW * crit) - parry - whiff
            sc[nm].append(f['score'] * (num_new / num if num else 1.0))
    m = {}
    for nm in sc:
        v = [x for x in sc[nm] if x and x > 0]
        m[nm] = sum(x * x for x in v) / sum(v) if v else 0.0
    emit({'weapon': r['weapon'], 'two': r['two'], 'stored': r['moveset']['score'], **m, 'per': per,
          'depth': {fn: f.get('depth') for fn, f in fams.items()}})
emit({'done_s': round(time.time() - t0, 1)})
