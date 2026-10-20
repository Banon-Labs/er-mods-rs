#!/usr/bin/env python3
"""Buff sources another source beats outright: same stacking slot, at least as good on every PvP
number, no more FP, no more frames to cast, and at least as much uptime net of recasts.

Sources, effects and stacking come from `er-mechanics-buffs.py` (`Buffs`). Per source:

- effect: `attack_context` and `defense_context` against a player for one source alone, over all
  four physical types; every number is oriented so larger is better (an attack rate, a defense
  multiplier inverted, poise divisor, max HP, stats);
- slot: the `spCategory` of each entry that carries an effect (buffs.md section 3: one live entry
  per category for the body-buff families);
- cost: FP (`Magic.mp`, 0 for an item) and the frames from the press to being able to act;
- uptime: `recast_plan` over the fight lengths, uptime x time factor, with no limit on uses (user,
  2026-10-12: "item stock really is irrelevant").

Cast frames of a spell (c0000.hks and the c0000 behavior graph, `er-behbnd-tree.py`): the press
enters `MagicLaunchRight` (clip 045000), which goes to `MagicFireRight` (045010) when it ends;
a spell whose TimeAct holds 045012 is a hold spell (`CheckIfHoldMagic`), and a tap of it goes on
to `MagicFireRightCancel` (045011). The clips are read from `a<400 + Magic.refType>`, falling back
to a400 where the spell's TimeAct lacks one (the selector's first child is a400). The cast lands
on the first bullet, cast or SpEffect event and the player acts at the first R1 or roll gate of
the last clip. A consumable's frames are `Buffs.item_cast_frames`, one number for every item
(`INFERRED`: the item clips are not mapped per `goodsUseAnim` here), so an item is never ranked
against another item on its use time.

    python3 scripts/er-mechanics-buff-dominance.py [--kinds spell,consumable] [--json out.json]
"""
import argparse
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _mod(name, fname):
    s = importlib.util.spec_from_file_location(name, os.path.join(HERE, fname))
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


B = _mod('er_mechanics_buffs', 'er-mechanics-buffs.py')
ATK = _mod('er_mechanics_attacks', 'er-mechanics-attacks.py')
D = _mod('er_mechanics_disengage', 'er-mechanics-disengage.py')

LAUNCH, FIRE, FIRE_CANCEL, LOOP = 45000, 45010, 45011, 45012
SPELL_TAE_BASE, SPELL_TAE_DEFAULT = 400, 400
#: TimeAct events that put a spell's effect out: 1 attack, 2 bullet, 64 cast, 66/67 SpEffect.
CAST_EVENTS = (1, 2, 64, 66, 67)
EPS = 1e-6


def _clip(cat, anim):
    for c in (cat, SPELL_TAE_DEFAULT):
        got = ATK.resolve_events(c, anim)
        if got and got[2]:
            return c, got[2]
    return None, None


def spell_frames(ref_type):
    """(frame the cast lands, frame the player can act) from the press, or None."""
    cat = SPELL_TAE_BASE + ref_type
    hold = bool(ATK.resolve_events(cat, LOOP)[2]) if ATK.resolve_events(cat, LOOP) else False
    chain = [LAUNCH, FIRE] + ([FIRE_CANCEL] if hold else [])
    t, land, act = 0.0, None, None
    for k, anim in enumerate(chain):
        c, ev = _clip(cat, anim)
        if ev is None:
            return None
        to = ATK.clip_to_real(ev)
        hits = sorted(to(e.start) * 30.0 for e in ev if e.type in CAST_EVENTS)
        if land is None and hits:
            land = t + hits[0]
        info = D.clip(c, anim)
        if k == len(chain) - 1:
            g = info["gates"] if info else {}
            act = t + min((v for v in (g.get("r1"), g.get("roll")) if v is not None), default=info["length"] if info else 0.0)
        else:
            t += info["length"] if info and info["length"] else 0.0
    return (round(land, 1) if land is not None else None), round(act, 1)


#: Fields that do not reach a fight with a player, or only steer the entry itself.
SKIP = ('Enemy', 'Obj', 'effectEndurance', 'motionInterval', 'cycleOccurrence', 'vfx', 'spCategory',
        'categoryPriority', 'iconId', 'stateInfo', 'replaceSpEffectId', 'wepParamChange')
#: Field name pieces whose larger value is better for the user; a `CutRate` or a defense correct
#: rate is a multiplier on damage taken, so smaller is better. Any other field must be equal.
HIGHER = ('AttackPowerRate', 'AttackRate', 'atkPlayerDmgCorrectRate', 'AttackPower', 'changeHpPoint',
          'addLifeForceStatus', 'addStrengthStatus', 'addAgilityStatus', 'addMagicStatus',
          'addFaithStatus', 'addLuckStatus', 'addEndureStatus', 'addSpiritStatus', 'maxHpRate',
          'maxStaminaRate', 'haveSoulRate', 'staminaRecoverChangeSpeed', 'toughnessDamageCutRate')
LOWER = ('DamageCutRate', 'defPlayerDmgCorrectRate', 'DiffenceRate')


def effect_fields(m, s):
    """{field: value} over the entries of source `s` that carry an effect, PvP fields only."""
    out = {}
    for sid, _ in s['entries']:
        for k, v in m.nondefault(sid).items():
            if any(p in k for p in SKIP):
                continue
            out[k] = v
        # A state that does more than show an effect must match: 152 "Enable Attack Effect against
        # Enemy" is how Bloodflame Blade and Black Flame Blade put their bleed and black flame on
        # a hit, which no rate field shows.
        mt = m.meta(sid)
        if mt['stateInfo'] and 'VFX' not in (mt.get('stateInfo_name') or ''):
            out[f"state {mt['stateInfo']}"] = 1.0
    return out


def has_benefit(f):
    """Whether any field of `f` is better than its default; a source whose resolved fields are all
    penalties (Dragonbolt Blessing: only `defPlayerDmgCorrectRate_Thunder` 1.35) has an effect the
    resolver did not reach, and is left out rather than ranked."""
    for k, v in f.items():
        if k.startswith('state '):
            return True
        if any(p in k for p in LOWER) and 'toughness' not in k and v < 1.0 - EPS:
            return True
        if any(p in k for p in HIGHER) and v > (1.0 if 'Rate' in k else 0.0) + EPS:
            return True
    return False


def better_or_equal(y, x):
    """Every field of `x` is matched or bettered in `y`, and `y` has no field `x` lacks unless it
    is a better-is-larger or better-is-smaller one."""
    for k in set(x) | set(y):
        default = 1.0 if any(p in k for p in ('Rate',)) else 0.0
        a, b = x.get(k, default), y.get(k, default)
        if any(p in k for p in LOWER) and 'toughness' not in k:
            if b > a + EPS:
                return False
        elif any(p in k for p in HIGHER):
            if b < a - EPS:
                return False
        elif abs(a - b) > EPS:
            return False
    return True


def duration(m, s):
    """Seconds the source lasts: the longest entry, or the entry an effect cycles out of."""
    best = 0.0
    for sid, via in s['entries']:
        d = m.meta(sid)['effectEndurance']
        if via.startswith('sp ') and 'cycleOccurrence' in via:
            parent = int(via.split()[1].split('.')[0])
            d = m.meta(parent)['effectEndurance']
        best = d if d < 0 or best < 0 else max(best, d)
        if d < 0:
            return -1.0
    return best


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument('--kinds', default='spell,consumable')
    ap.add_argument('--json')
    a = ap.parse_args()
    m = B.Buffs()
    kinds = set(a.kinds.split(','))
    rows = []
    for s in m.sources:
        if s['kind'] not in kinds:
            continue
        cats = sorted({m.meta(sid)['spCategory'] for sid, _ in s['entries'] if m.nondefault(sid)})
        for sid, via in s['entries']:
            if via.startswith('sp ') and 'cycleOccurrence' in via:
                cats.append(m.meta(int(via.split()[1].split('.')[0]))['spCategory'])
        cats = sorted(set(cats) - {0, 20})
        dur = duration(m, s)
        if s['kind'] == 'spell':
            mg = m.magic.get(s['goods']) or {}
            fp = mg.get('mp') or 0
            fr = spell_frames(PR_magic_reftype(s['goods']))
            land, act = fr if fr else (None, None)
        else:
            fp, land, act = 0, None, m.item_cast_frames
        plan = B.recast_plan(dur, cast_frames=act or 0.0)
        rows.append({'name': s['name'], 'kind': s['kind'], 'cats': cats, 'duration': dur, 'fp': fp,
                     'land': land, 'act': act, 'uptime': round(plan['uptime'] * plan['time_factor'], 4),
                     'effect': effect_fields(m, s)})
    beaten = []
    for x in rows:
        for y in rows:
            if x is y or x['name'] == y['name'] or not set(x['cats']) & set(y['cats']) or not has_benefit(x['effect']) or not has_benefit(y['effect']):
                continue
            if not better_or_equal(y['effect'], x['effect']):
                continue
            if y['fp'] > x['fp'] or (y['act'] or 0) > (x['act'] or 0) + EPS or y['uptime'] < x['uptime'] - EPS:
                continue
            if better_or_equal(x['effect'], y['effect']) and \
                    (y['fp'], y['act'], -y['uptime']) == (x['fp'], x['act'], -x['uptime']) and y['name'] > x['name']:
                continue
            beaten.append((x, y))
    for x in rows:
        if x['effect'] and not has_benefit(x['effect']):
            print(f"unresolved: {x['name']} ({x['kind']}): only {', '.join(sorted(x['effect']))}")
    for x, y in beaten:
        print(f"{x['name']} ({x['kind']}, {x['fp']} FP, acts f{x['act']}, {x['duration']:g} s, uptime {x['uptime']}) "
              f"<= {y['name']} ({y['kind']}, {y['fp']} FP, acts f{y['act']}, {y['duration']:g} s, uptime {y['uptime']}); "
              f"slot {sorted(set(x['cats']) & set(y['cats']))}")
    if a.json:
        with open(a.json, 'w') as f:
            json.dump({'rows': [{k: v for k, v in r.items() if k != 'effect'} for r in rows],
                       'beaten': [[x['name'], y['name']] for x, y in beaten]}, f, indent=1)


_MAGIC = None


def PR_magic_reftype(goods):
    global _MAGIC
    if _MAGIC is None:
        PR = B.PR
        rs, _, _ = PR.rows(PR.param_bytes(PR.load(), 'Magic'), ['refType'], strict=False)
        _MAGIC = {r['id']: r['refType'] for r in rs}
    return _MAGIC.get(goods, 0)


if __name__ == '__main__':
    main()
