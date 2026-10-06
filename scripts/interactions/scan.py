#!/usr/bin/env python3
"""Generate hypotheses for interactions of the Piquebone-smoke kind, offline.

The known instance: a lingering bullet's hit asks, at impact, for the attacker's current weapon
buff, so state changed after firing rides the hit. This scan is not told that answer. It crosses

  * readers  -- places on the hit path that read attacker state at the hit (`readers.py`, static
                RE of 1.16.2), each with its gate, and
  * carriers -- every player-reachable bullet in the regulation (BehaviorParam_PC, goods and
                magic launches, followed through HitBulletID and intervalCreateBulletId), with
                its life, radius, re-hit settings, targeting, damage and hit context,

and keeps a (reader, carrier) pair when the reader's gate passes for the carrier, some
player-reachable state source feeds the reader, and the bullet can still be hitting at least
`--min-window` seconds after launch. Pairs are ranked by oddity.

    python3 scripts/interactions/scan.py                     # ranked table on stdout
    python3 scripts/interactions/scan.py --doc docs/er-mechanics/interaction-candidates.md
    python3 scripts/interactions/scan.py --selftest

`--selftest` checks that the scan rediscovers the Piquebone smoke x right-hand weapon buff pair
on its own (and the context table it relies on).
"""
import argparse
import collections
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import regdata  # noqa: E402
import readers  # noqa: E402
import skill_bullet_map  # noqa: E402

WID_RE = re.compile(r' on (.+?) \((\d+)\) anim')


# ---------------------------------------------------------------- state sources

class Sources:
    """Player-reachable SpEffect rows, each with how the player turns it on.

    kind: use (skill, item or spell: the player can do it while a bullet is out),
          trigger (a row a talisman, armor or weapon passive switches on by condition),
          equip (resident on gear: only a menu swap changes it).
    """

    def __init__(self, reg, smap):
        self.reg = reg
        self.by_id = collections.defaultdict(set)   # sp id -> {(kind, label)}

        def add(roots, kind, label, trigger_kind=None):
            roots = [r for r in roots if r and r > 0 and r in reg.sp]
            closure = reg.sp_closure(roots)
            for i in closure:
                k = kind if i in roots or trigger_kind is None else trigger_kind
                self.by_id[i].add((k, label))

        for sid, tags in smap['speffect'].items():
            sid = int(sid)
            label = tags[0].split(' on ')[0]
            add([sid], 'use', f'skill {label}')
        for g in reg.goods.values():
            nm = reg.name('EquipParamGoods', g['id'])
            if not nm:
                continue
            roots = [g['refId_default'], g['refId_1']]
            b = reg.beh.get(g['behaviorId'])
            if b and b['refType'] == 2:
                roots.append(b['refId'])
            add(roots, 'use', f'item {nm}')
        for m in reg.magic.values():
            nm = reg.name('EquipParamGoods', m['id'])
            if not nm:
                continue
            roots = []
            for i in range(1, 11):
                rid = m[f'refId{i}']
                if rid <= 0:
                    continue
                b = reg.beh.get(rid)
                if b and b['refType'] == 2:
                    roots.append(b['refId'])
                elif m[f'refCategory{i}'] == 2:
                    roots.append(rid)
            add(roots, 'use', f'spell {nm}')
        for a in reg.accessory.values():
            nm = reg.name('EquipParamAccessory', a['id'])
            if not nm:
                continue
            add([a['refId']] + [a[f'residentSpEffectId{i}'] for i in (1, 2, 3, 4)],
                'equip', f'talisman {nm}', 'trigger')
        for p in reg.protector.values():
            nm = reg.name('EquipParamProtector', p['id'])
            if not nm:
                continue
            add([p['residentSpEffectId'], p['residentSpEffectId2'], p['residentSpEffectId3']],
                'equip', f'armor {nm}', 'trigger')
        for w in reg.weapon.values():
            if w['id'] % 10000:
                continue
            nm = reg.name('EquipParamWeapon', w['id'])
            if not nm:
                continue
            add([w['residentSpEffectId'], w['residentSpEffectId1'], w['residentSpEffectId2']],
                'equip', f'weapon {nm}', 'trigger')

    def rows(self, pred):
        return [self.reg.sp[i] for i in sorted(self.by_id) if pred(self.reg.sp[i])]

    def best_kind(self, sid):
        kinds = {k for k, _ in self.by_id[sid]}
        for k in ('use', 'trigger', 'equip'):
            if k in kinds:
                return k
        return 'equip'

    def labels(self, sid, n=2):
        out = sorted({lab for _, lab in self.by_id[sid]})
        return out[:n]


# ---------------------------------------------------------------- carriers

SKILL_RE = re.compile(r'^(.*) \((\d+)\)$')


class Launch:
    __slots__ = ('beh', 'ctx', 'ctx_src', 'owners', 'weapons', 'root', 'skills')

    def __init__(self, beh, ctx, ctx_src, root):
        self.beh, self.ctx, self.ctx_src, self.root = beh, ctx, ctx_src, root
        self.owners, self.weapons, self.skills = set(), set(), set()


def variation_weapons(reg, bid):
    if bid < 100000000:
        return []
    var = (bid // 1000) % 100000
    if var <= 0:
        return []
    return [w['id'] for w in reg.weapon.values()
            if w['behaviorVariationId'] == var and w['id'] % 10000 == 0
            and reg.name('EquipParamWeapon', w['id'])]


def launches(reg, smap):
    """Every player-reachable bullet launch with its hit context and named owners."""
    out = {}

    def get(bid, ctx, src):
        b = reg.beh[bid]
        key = (bid, ctx)
        if key not in out:
            out[key] = Launch(bid, ctx, src, b['refId'])
        return out[key]

    # Only rows a TimeAct names or a weapon variation owns are launches. Rows that merely share a
    # bullet with a named skill are not: Eruption's TimeAct fires judge 3042 = BEH 300000042
    # (category 0), and attributing the unused category-1 row 300000048 to it produced the
    # Eruption + grease prediction the user measured as a negative on 2026-10-04.
    skill_beh = {int(k): v for k, v in smap['behavior'].items()}
    for bid, b in reg.beh.items():
        if b['refType'] != 1:
            continue
        ws = variation_weapons(reg, bid)
        tags = skill_beh.get(bid, [])
        src_label = 'BehaviorParam.category'
        if ws or tags:
            L = get(bid, b['category'], src_label)
            for w in ws:
                L.owners.add(f"{reg.name('EquipParamWeapon', w)} moveset")
                L.weapons.add(w)
            for t in tags:
                m = WID_RE.search(t)
                wn = m.group(1) if m and not m.group(1).isdigit() else ''
                L.owners.add(t.split(' on ')[0] + (f' ({wn})' if wn else ''))
                sk = SKILL_RE.match(t.split(' on ')[0])
                if sk:
                    L.skills.add(int(sk.group(2)))
                if m:
                    L.weapons.add(int(m.group(2)))
    for g in reg.goods.values():
        b = reg.beh.get(g['behaviorId'])
        nm = reg.name('EquipParamGoods', g['id'])
        if b and b['refType'] == 1 and nm:
            get(g['behaviorId'], g['spEffectCategory'], 'goods spEffectCategory').owners.add(f'item {nm}')
    for m in reg.magic.values():
        nm = reg.name('EquipParamGoods', m['id'])
        if not nm:
            continue
        for i in range(1, 11):
            # A spell names its bullet directly (refCategory 1; no Magic refId is a BehaviorParam
            # row), so the spawn carries an explicit bullet id and FUN_14038e210 takes the context
            # from Magic.spEffectCategory (3 or 4), or 0 if no magic id rides the spawn.
            rid = m[f'refId{i}']
            if m[f'refCategory{i}'] == 1 and rid in reg.bullet:
                key = (-(m['id'] * 100 + i), m['spEffectCategory'])   # no BehaviorParam row
                if key not in out:
                    out[key] = Launch(key[0], m['spEffectCategory'], 'Magic.spEffectCategory', rid)
                out[key].owners.add(f'spell {nm}')
    return list(out.values())


def rehits(bi):
    """(estimated hits per target, label). None = unknown cadence (record 0, not endless)."""
    life, rec = bi['life'], bi['rec']
    if bi['endless']:
        n = max(1, math.ceil(life / rec)) if rec > 0 else None
        return n, f"endless, record {rec:g}"
    if 0 < rec < life:
        return math.ceil(life / rec), f'record {rec:g}'
    if rec == 0:
        return None, 'record 0 (cadence unknown)'
    return 1, f'record {rec:g} >= life'


class Carrier:
    """One bullet node at one hit context, with every launch that reaches it."""

    def __init__(self, reg, bid, ctx):
        self.bid, self.ctx = bid, ctx
        self.bi = reg.bullet_info(bid)
        self.end = 0.0
        self.launches = []
        self.paths = []

    @property
    def owners(self):
        s = set()
        for L in self.launches:
            s |= L.owners
        return sorted(s)

    @property
    def weapons(self):
        s = set()
        for L in self.launches:
            s |= L.weapons
        return s

    @property
    def skills(self):
        s = set()
        for L in self.launches:
            s |= L.skills
        return s


def carriers(reg, smap, min_window):
    groups = {}
    for L in launches(reg, smap):
        for node in reg.chain(L.root):
            bi = reg.bullet_info(node['id'])
            a = bi['a']
            if a is None or not a['oppose'] or node['end'] < min_window:
                continue
            if max(bi['r'], bi['rmax']) <= 0:
                continue
            key = (node['id'], L.ctx)
            c = groups.get(key)
            if c is None:
                c = groups[key] = Carrier(reg, node['id'], L.ctx)
            c.end = max(c.end, node['end'])
            c.launches.append(L)
            c.paths.append((L.beh, node['path']))
    return list(groups.values())


# ---------------------------------------------------------------- crossing

AMMO_TYPES = (81, 83, 85, 86)   # arrow, greatarrow, bolt, greatbolt (EquipParamWeapon.wepType)


def buffable(reg, wids):
    """Whether a launching weapon can hold a grease: EquipParamWeapon.isEnhance on the base row.

    gemMountType says nothing about grease (every bow mounts ashes and has isEnhance 0), so it
    is not consulted. The base row is the Standard affinity; every ash in the measured set
    allows Standard, and status/element affinity rows have isEnhance 0.
    """
    if not wids:
        return None
    named = [w for w in wids if w in reg.weapon and reg.name('EquipParamWeapon', w)]
    if not named:
        return None
    return any(reg.weapon[w]['isEnhance'] for w in named)


def is_ammo(reg, wids):
    named = [w for w in wids if w in reg.weapon]
    return bool(named) and all(reg.weapon[w]['wepType'] in AMMO_TYPES for w in named)


def skill_own_buffs(reg, smap):
    """Skill id -> the stateInfo 152/153 rows its own TimeAct applies (Seppuku, the mists)."""
    out = collections.defaultdict(set)
    for sid, tags in smap['speffect'].items():
        s = reg.sp.get(int(sid))
        if not s or s['stateInfo'] not in (152, 153):
            continue
        for t in tags:
            sk = SKILL_RE.match(t.split(' on ')[0])
            if sk:
                out[int(sk.group(2))].add(int(sid))
    return out


def cross(reg, src, cars, own=None):
    own = own or {}
    r1_rows = src.rows(lambda s: s['stateInfo'] in (152, 153) and s['atkOccurrenceSpEffectId'] > 0)
    corr_rows = src.rows(lambda s: any(abs(s[f] - 1.0) > 1e-6 for f in regdata.ENEMY_CORR))
    stat_rows = src.rows(lambda s: any(s[f] for f in ('addStrengthStatus', 'addDexterityStatus',
                                                       'addMagicStatus', 'addFaithStatus',
                                                       'addLuckStatus')))
    blue_rows = src.rows(lambda s: s['stateInfo'] in (315, 316))
    spear_rows = src.rows(lambda s: s['stateInfo'] == 197)
    add_rows = src.rows(lambda s: s['stateInfo'] in (152, 153)
                        and any(s[f] > 0 for f in regdata.ELEMENT_ADDS))
    out = []
    for c in cars:
        bi, a = c.bi, c.bi['a']
        atk = a['row']
        dmg = a['damaging']
        n, rh = rehits(bi)
        wb = buffable(reg, c.weapons)
        ammo = is_ammo(reg, c.weapons)
        own_ids = set()
        for sk in c.skills:
            own_ids |= own.get(sk, set())
        own_note = ''
        if own_ids:
            own_note = ('the skill applies its own buff ' + ', '.join(str(i) for i in sorted(own_ids))
                        + ' first; a same-spCategory buff of yours is replaced until you re-apply it')

        def emit(rid, rows, note=''):
            out.append({'reader': rid, 'carrier': c, 'rows': rows, 'hits': n, 'rehit': rh,
                        'buffable': wb, 'ammo': ammo, 'note': note})

        def reaches(s):
            return regdata.status_reaches(reg.sp.get(s['atkOccurrenceSpEffectId']), atk)

        rows = [s for s in r1_rows if regdata.gate(c.ctx, s, atk) and reaches(s)]
        if rows:
            emit('R1', rows, own_note)
        if c.ctx == 12:
            rows = [s for s in r1_rows if regdata.gate(12, s, atk, True)
                    and not regdata.gate(12, s, atk, False) and reaches(s)]
            if rows:
                emit('R5', rows, 'two-hand the left weapon while the bullet is out')
        if dmg:
            rows = [s for s in corr_rows if regdata.gate(c.ctx, s, atk)]
            if rows:
                emit('R2', rows)
            if a['corr'] > 0:
                emit('R3', stat_rows + blue_rows)
            if spear_rows:
                emit('R4', spear_rows)
        ids = [atk[f'spEffectId{i}'] for i in range(5)] + bi['sp']
        rows = [reg.sp[i] for i in ids if i in reg.sp and reg.sp[i]['effectTargetAttacker']]
        if rows:
            emit('R6', rows)
        if not dmg:
            rows = [s for s in add_rows if regdata.gate(c.ctx, s, atk)]
            if own_ids:
                rows = [s for s in rows if s['id'] in own_ids]
            if rows:
                emit('S1', rows, 'only the skill\'s own buff is live at launch' if own_ids else '')
    return out


# A swap-then-grease row needs the bullet to outlast a weapon swap plus a grease use. The only
# measured fit is the Piquebone smoke (4 s, user test 2026-10-04); shorter windows score down.
SWAP_GREASE_WINDOW = 4.0


def score(reg, src, p):
    c, bi = p['carrier'], p['carrier'].bi
    rid = p['reader']
    base = {'R1': 3.0, 'R5': 1.5, 'S1': 1.5, 'R6': 2.0, 'R2': 2.0, 'R3': 0.5, 'R4': 0.5}[rid]
    s = base
    zero = not bi['a']['damaging']
    if zero and rid in ('R1', 'R5', 'S1', 'R6'):
        s += 2.0
    s += min(3.0, max(bi['r'], bi['rmax']) / 5.0)
    if p['hits'] is None:
        s += 1.0
    elif p['hits'] > 1:
        s += min(3.0, math.log2(p['hits']))
    s += min(2.0, c.end / 2.5)
    kinds = {src.best_kind(r['id']) for r in p['rows']}
    if 'use' in kinds or 'trigger' in kinds:
        s += 1.0
    if rid == 'R5' or (rid == 'R1' and c.ctx == 2):
        s += 1.5
    if rid in ('R1', 'S1') and p['buffable'] is False and not (p['ammo'] and c.ctx == 1):
        s -= 2.0
        if rid == 'R1' and c.end < SWAP_GREASE_WINDOW:
            s -= 2.0
    if rid == 'R1' and p['note']:
        s -= 1.0
    if rid in ('R1', 'R5') and all(
            (reg.sp.get(r['atkOccurrenceSpEffectId']) or {}).get('isUseStatusAilmentAtkPowerCorrect')
            for r in p['rows']):
        # Gate 2 passes any non-zero scale, but a 0.08 scale (Fires of Slumber) builds 8 % of the
        # grease's status per hit.
        s -= 2.0 * (1.0 - min(1.0, regdata.status_scale(bi['a']['row'])))
    return round(s, 2)


# ---------------------------------------------------------------- reporting

def _row_rank(reg, src, r):
    labels = ' '.join(lab for _, lab in src.by_id[r['id']])
    nm = reg.name('SpEffectParam', r['id'])
    pri = 0 if 'Blood Grease' in nm or 'Seppuku' in nm else 1 if 'Grease' in nm else 2
    kind = {'use': 0, 'trigger': 1, 'equip': 2}[src.best_kind(r['id'])]
    item = 0 if 'item ' in labels else 1
    return (pri, kind, item, r['id'])


def row_names(reg, src, rows, n=3):
    seen, out = set(), []
    for r in sorted(rows, key=lambda r: _row_rank(reg, src, r)):
        nm = reg.name('SpEffectParam', r['id']) or str(r['id'])
        nm = re.sub(r'^\[[^\]]*\]\s*', '', nm)
        nm = re.sub(r'\s*-\s*(Damage/)?(Bleed|Status)?\s*Buff.*$', '', nm)
        nm = re.sub(r'\s*-\s*(Right|Left)\b.*$', '', nm.replace('Drawstring ', ''))
        key = nm.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(f"{nm} {r['id']}")
        if len(out) >= n:
            break
    return out


def describe(reg, src, p):
    c, bi, rid = p['carrier'], p['carrier'].bi, p['reader']
    owners = c.owners[:2]
    who = '; '.join(owners) + (f' (+{len(c.owners) - 2})' if len(c.owners) > 2 else '')
    names = ', '.join(row_names(reg, src, p['rows']))
    win = f'{c.end:.1f} s'
    if rid == 'R1':
        sts = collections.Counter()
        for r in p['rows']:
            for k in reg.status_of(r['atkOccurrenceSpEffectId']):
                sts[k.replace('AttackPower', '').replace('poizon', 'poison').replace('disease', 'rot')
                    .replace('blood', 'bleed').replace('freeze', 'frost').replace('curse', 'death')] += 1
        eff = '/'.join(sorted(sts)) or 'non-status on-hit rows'
        hand = {1: 'right-hand', 2: 'left-hand', 12: 'right-hand'}.get(c.ctx, f'ctx {c.ctx}')
        try_ = f'{who}; within {win} apply a {hand} buff ({names})'
        if p['ammo'] and c.ctx == 1:
            try_ = (f'{who}: bow in the left hand, a greasable weapon in the right with a right-hand '
                    f'buff ({names}) already on it; two-hand the bow and fire. No swap: every arrow '
                    f'BehaviorParam row is category 1 whichever hand holds the bow')
        elif p['buffable'] is False:
            try_ = (f'{who}: fire it, then swap that hand to a greasable weapon and apply a {hand} '
                    f'buff ({names}) before the bullet ends ({win}); the launching weapon cannot '
                    f'hold one. Same order as the measured Piquebone case (bow fired, swap, grease, '
                    f'4 s smoke)')
        scale = regdata.status_scale(bi['a']['row'])
        exp = (f'every enemy it touches gets the buff\'s on-hit row per hit ({eff}), status '
               f'buildup x{scale:.2g} (AtkParam statusAilmentAtkPowerCorrectRate x _byPoint)')
        if p['note']:
            exp += f"; {p['note']}"
    elif rid == 'R5':
        try_ = f'{who} with a left-hand buff ({names}); two-hand the left weapon before it lands'
        exp = ('the left-hand buff starts riding the hits only while the left weapon is two-handed, '
               f"status buildup x{regdata.status_scale(bi['a']['row']):.2g}")
    elif rid == 'R2':
        try_ = f'{who}; within {win} switch on {names}'
        exp = 'later hits of the same bullet take the new damage multiplier'
    elif rid == 'R3':
        try_ = f'{who}; within {win} change attributes, durability or equip load'
        exp = 'later hits rescale with the attacker\'s current stats'
    elif rid == 'R4':
        try_ = f'{who} into an enemy mid-attack with Spear Talisman'
        exp = 'counter-hit boost applies to the bullet'
    elif rid == 'R6':
        try_ = f'{who}; move away or change state while it ticks'
        exp = f"{names} land on you at each hit, wherever you are"
    else:
        try_ = f'apply {names} before launching {who}'
        exp = 'the zero-damage bullet deals the buff\'s flat element add per hit'
        if p['note']:
            exp += f"; {p['note']}"
    if p.get('siblings'):
        try_ += f" [+{len(p['siblings'])} sibling bullets: {', '.join(str(b) for b in p['siblings'][:4])}]"
    return try_, exp


def carrier_line(reg, c):
    bi = c.bi
    a = bi['a']
    dmg = 'zero damage' if not a['damaging'] else 'damaging'
    return (f"bullet {c.bid} ({bi['name'][:40] or 'unnamed'}), ctx {c.ctx}, {dmg}, life {bi['life']:g} s, "
            f"r {bi['r']:g}->{bi['rmax']:g} m, AtkParam_Pc {bi['atk']}")


def links(reg, p):
    rid, c = p['reader'], p['carrier']
    rd = readers.READERS[rid]
    ctx_src = sorted({L.ctx_src for L in c.launches})
    ctx_ev = 'VERIFIED' if ctx_src == ['BehaviorParam.category'] else 'INFERRED'
    gate_ev = 'VERIFIED' if rid in ('R1', 'R5', 'R2') else rd['evidence']
    rehit_ev = 'VERIFIED' if p['hits'] not in (None,) and p['hits'] >= 1 else 'INFERRED'
    if p['hits'] and p['hits'] > 1:
        rehit_ev = 'INFERRED cadence'
    return (f"reader {rd['evidence']}; carrier rows VERIFIED; ctx {ctx_ev} ({'/'.join(ctx_src)}); "
            f"gate {gate_ev}; re-hit {p['rehit']} {rehit_ev}")


def run(min_window=1.0):
    reg = regdata.Reg()
    smap = skill_bullet_map.build()
    src = Sources(reg, smap)
    cars = carriers(reg, smap, min_window)
    pairs = cross(reg, src, cars, skill_own_buffs(reg, smap))
    for p in pairs:
        p['score'] = score(reg, src, p)
        p['known'] = readers.KNOWN.get((p['reader'], p['carrier'].bid))
        p['contradicted'] = readers.CONTRADICTED.get(p['carrier'].bid)
    pairs.sort(key=lambda p: (-p['score'], p['reader'], p['carrier'].bid, p['carrier'].ctx))
    return reg, src, cars, pairs


def grouped(pairs):
    """Collapse near-identical bullets (same reader, context, AtkParam, life, radius) to one row."""
    seen, out = {}, []
    for p in pairs:
        bi = p['carrier'].bi
        key = (p['reader'], p['carrier'].ctx, bi['atk'], round(bi['life'], 2),
               round(max(bi['r'], bi['rmax']), 2), bool(p['known']))
        if key in seen:
            seen[key].setdefault('siblings', []).append(p['carrier'].bid)
            continue
        seen[key] = p
        out.append(p)
    return out


def print_table(reg, src, pairs, top):
    for i, p in enumerate(grouped(pairs)[:top], 1):
        t, e = describe(reg, src, p)
        k = ' [known]' if p['known'] else ''
        print(f"{i:3} {p['score']:5.2f} {p['reader']}{k} | {carrier_line(reg, p['carrier'])} | {t} | {e}")


# Rows per reader in the doc's ranked table. R5 and R2 hold for nearly every context-12 or damaging
# carrier, so uncapped they push every other reader off the page.
CAPS = {'R5': 4, 'R2': 8, 'R3': 3, 'R4': 2, 'S1': 5, 'R6': 6}


def md_escape(s):
    return s.replace('|', '/')


def write_doc(path, reg, src, cars, pairs, top):
    by_reader = collections.Counter(p['reader'] for p in pairs)
    G = grouped(pairs)
    known = [p for p in G if p["known"]]
    L = []
    L.append('# Interaction candidates: attacker state read at the hit, carried by lingering bullets')
    L.append('')
    L.append('Generated by `python3 scripts/interactions/scan.py --doc '
             'docs/er-mechanics/interaction-candidates.md`. Tested in game so far: the Piquebone '
             'smoke (positive) and the two measured negatives below. Every ranked row is a '
             'hypothesis.')
    L.append('')
    L.append('Labels: `VERIFIED` = read in the 1.16.2 executable (Ghidra :8765, shift 0) or the '
             '1.17.1 regulation; `INFERRED` = follows from verified pieces, not traced end to end.')
    L.append('')
    L.append('## Method')
    L.append('')
    L.append('The scan is not told the Piquebone answer. It crosses two lists:')
    L.append('')
    L.append('- **Readers**: places on the hit path that read the attacker when a bullet hits. A '
             'bullet freezes some attacker state into its AttackInfo when it is created '
             '(`FUN_14038e380`: weapon ids, two-handing, `FUN_1404f4520` flat adds and '
             'AttackPowerRate/AttackRate products, `VERIFIED`), and HitBulletID children copy it. '
             'Anything read live at the hit can change after firing and still ride the bullet.')
    L.append('- **Carriers**: every player-reachable bullet (BehaviorParam_PC launches named by a '
             'weapon variation or a skill TimeAct, goods and spell launches) followed through '
             '`HitBulletID` and `intervalCreateBulletId`. A carrier is kept when its AtkParam hits '
             f'enemies (`opposeTarget`), it has a hit radius, and it can still be hitting at least '
             '1 s after launch (longest launch-to-last-hit path).')
    L.append('')
    L.append('A weapon buff rides a bullet only through two gates, both `VERIFIED` and both checked '
             'here: the hit-context byte the launch gives the bullet (`BehaviorParam.category` of '
             'the row the TimeAct or weapon fires, or `Magic.spEffectCategory` for a spell) must '
             'admit the buff\'s hand, and the bullet\'s AtkParam `statusAilmentAtkPowerCorrectRate` '
             'x `_byPoint` must be non-zero, because every grease and Seppuku on-hit row sets '
             '`isUseStatusAilmentAtkPowerCorrect` (CalculateDamage2 0x140448e12). The buff itself '
             'is a character-wide SpEffect entry read at the hit; no weapon id is compared, so a '
             'swap only matters for timing.')
    L.append('')
    L.append('A pair is kept when the reader\'s gate passes for the carrier\'s hit context and a '
             'player-reachable SpEffect row (skill, item, spell, talisman, armor or weapon passive) '
             'feeds the reader. Oddity score: zero damage, radius, re-hits, window length, sources '
             'the player can switch on mid-bullet, and cross-hand reads score up; a weapon that cannot '
             'hold a buff scores down.')
    L.append('')
    L.append('### Readers (static RE, 1.16.2)')
    L.append('')
    L.append('| id | reads | where | live or snapshot | gate | label |')
    L.append('|---|---|---|---|---|---|')
    for rid, r in readers.READERS.items():
        L.append(f"| {rid} | {md_escape(r['reads'])} | `{md_escape(r['where'])}` | {md_escape(r['live'])} | "
                 f"{md_escape(r['gate'])} | `{r['evidence']}` |")
    L.append('')
    L.append('Readers found on the same path with no state the player switches mid-bullet (not crossed):')
    L.append('')
    for nm, where, what, ev in readers.UNCROSSED:
        L.append(f'- {nm}: `{where}`; {what}. `{ev}`.')
    L.append('')
    L.append('## Totals')
    L.append('')
    L.append(f'- Carriers (bullet x hit context, window >= 1 s, hits enemies): {len(cars)}.')
    L.append(f'- Candidate pairs: {len(pairs)} ('
             + ', '.join(f'{k} {v}' for k, v in sorted(by_reader.items())) + f'); {len(G)} after '
             'collapsing near-identical sibling bullets (same reader, context, AtkParam, life and '
             'radius). Ranks below are over the collapsed list.')
    L.append(f'- Already documented, rediscovered by the scan: {len(known)} pairs '
             '(rank: ' + ', '.join(f"{p['known'].split(' (')[0]} #{G.index(p) + 1}" for p in known)
             + ').')
    L.append('')
    L.append('## Self-check: the known case')
    L.append('')
    for p in known:
        c = p['carrier']
        t, e = describe(reg, src, p)
        L.append(f"- #{G.index(p) + 1} {p['known']}: {carrier_line(reg, c)}. Gate passes for "
                 f"{', '.join(row_names(reg, src, p['rows'], 4))}.")
    L.append('')
    L.append('## Measured negatives (USER 2026-10-04)')
    L.append('')
    L.append('The selftest requires the scan to emit no R1/R5 pair for these, and requires each to be '
             'a carrier it sees, so the rejection comes from the gates. Field diff: '
             '`python3 scripts/interactions/carrier_diff.py`.')
    L.append('')
    for bid, (tried, why) in readers.MEASURED_NEGATIVE.items():
        ctxs = sorted({c.ctx for c in cars if c.bid == bid})
        L.append(f'- bullet {bid}, ctx {ctxs}: {tried}. Fails because {why}.')
    L.append('')
    L.append('## Measured negatives the gates do not explain')
    L.append('')
    L.append('Both gates pass for these, yet the user saw no grease status. They are kept out of the '
             'ranking; `scripts/frida/weapon-buff-bullet-hits.js` is the trace that would name the '
             'cause.')
    L.append('')
    for bid, (tried, why) in readers.CONTRADICTED.items():
        rs = sorted({p['reader'] for p in pairs if p['carrier'].bid == bid})
        L.append(f'- bullet {bid} (readers the scan would have ranked: {", ".join(rs)}): {tried}. {why}.')
    L.append('')
    L.append(f'## Ranked candidates (top {top}, known cases excluded)')
    L.append('')
    L.append('At most ' + ', '.join(f'{v} {k}' for k, v in sorted(CAPS.items())) + ' rows, so one '
             'reader that passes for nearly every carrier does not fill the table, and at most 2 rows per bullet. `ctx` is the hit '
             'context byte. Owners name the skill (weapon) or the weapon moveset that launches the bullet.')
    L.append('')
    L.append('| # | score | reader | carrier | try in game | expected | per-link labels |')
    L.append('|---|---|---|---|---|---|---|')
    n = 0
    per = collections.Counter()
    per_bullet = collections.Counter()
    for p in G:
        if p['known'] or p['contradicted'] or per[p['reader']] >= CAPS.get(p['reader'], top):
            continue
        if per_bullet[p['carrier'].bid] >= 2:
            continue
        per[p['reader']] += 1
        per_bullet[p['carrier'].bid] += 1
        n += 1
        if n > top:
            break
        t, e = describe(reg, src, p)
        L.append(f"| {n} | {p['score']} | {p['reader']} | {md_escape(carrier_line(reg, p['carrier']))}; "
                 f"{md_escape(p['rehit'])} | {md_escape(t)} | {md_escape(e)} | {md_escape(links(reg, p))} |")
    L.append('')
    L.append('## Caveats that apply to every row')
    L.append('')
    L.append('- Re-hit cadence for `dmgHitRecordLifeTime` 0 is not traced (`FUN_1403960a0` / DmgMan). '
             'The Piquebone smoke is no longer evidence for it: the user measured no grease status '
             'from it after a plain shot or an unlocked Rain of Arrows.')
    L.append('- R1 picks the first passing entry in list order; with two passing buffs which one '
             'wins is not traced.')
    L.append('- Goods and spell hit contexts come from `EquipParamGoods/Magic.spEffectCategory` '
             '(`FUN_14038e210`, `VERIFIED`); that a goods or spell launch fills that id rather than '
             'the behavior row is `INFERRED`.')
    L.append('- Source reachability is by row names and equip/skill/item links, not by a check that '
             'every row is obtainable in the shipped game.')
    L.append('- The sub-category mask is modelled as AtkParam `subCategory1..4` against '
             '`magicSubCategoryChange1..3` (`INFERRED`).')
    L.append('')
    with open(path, 'w') as fh:
        fh.write('\n'.join(L))


def selftest():
    sp = lambda w: {'wepParamChange': w, 'magParamChange': 0, 'miracleParamChange': 0,  # noqa: E731
                    'shamanParamChange': 0}
    assert regdata.accepts(1, sp(1)) and not regdata.accepts(1, sp(2))
    assert regdata.accepts(2, sp(2)) and not regdata.accepts(2, sp(1))
    assert not regdata.accepts(0, sp(1)) and regdata.accepts(0, sp(0))
    assert regdata.accepts(12, sp(2), True) and not regdata.accepts(12, sp(2), False)
    reg, src, cars, pairs = run()
    # Positive (user test 2026-10-04): Rain of Arrows with Piquebone, locked on. Of its chain only
    # the falling arrows 20003354 depend on a lock target (emitter 20003351 EmittePosType 6 "above
    # and behind target", arrow homingAngle 10), so they are the scan's carrier for it.
    hit = [p for p in pairs if p['reader'] == 'R1' and p['carrier'].bid == 20003354
           and p['carrier'].ctx == 1]
    assert hit, 'scan did not find the Rain of Arrows falling arrow x right-hand buff pair'
    p = hit[0]
    ids = {r['id'] for r in p['rows']}
    assert 1755 in ids and 3190 in ids, f'Seppuku 1755 / Blood Grease 3190 missing: {sorted(ids)[:20]}'
    assert p['known'], 'pair not flagged as known'
    assert reg.bullet[20003351]['EmittePosType'] == 6 and reg.bullet[20003354]['homingAngle'] > 0
    assert reg.bullet[20003300]['homingAngle'] == 0, 'plain shot should not home'
    # Measured negatives the gates do not explain (plain shot, unlocked Rain of Arrows): the smoke
    # passes both gates, so it must be flagged and kept out of the ranking, never scored as a hit.
    smoke = [q for q in pairs if q['carrier'].bid == 20003309]
    assert smoke and all(q['contradicted'] for q in smoke), 'smoke pairs must be flagged contradicted'
    smoke_atk = reg.atk[reg.bullet[20003309]['atkId_Bullet']]
    assert regdata.status_scale(smoke_atk) == 1.0 and regdata.accepts(1, reg.sp[3190])
    left = [q for q in pairs if q['reader'] == 'R1' and q['carrier'].bid == 20003309
            and q['carrier'].ctx == 2]
    assert left and 1755 not in {r['id'] for r in left[0]['rows']}, 'left-hand bolt should refuse 1755'
    owners = ' '.join(p['carrier'].owners)
    assert 'Piquebone' in owners, owners[:200]

    # Measured negatives (user test 2026-10-04). Each must be a carrier the scan sees, so the
    # rejection comes from the gates and not from the bullet being missed.
    for bid, (tried, why) in readers.MEASURED_NEGATIVE.items():
        seen = [c for c in cars if c.bid == bid]
        assert seen, f'{tried}: bullet {bid} is not a carrier, so the negative proves nothing'
        bad = [q for q in pairs if q['reader'] in ('R1', 'R5') and q['carrier'].bid == bid]
        assert not bad, f'{tried} is a measured negative but scored as {bad[0]["reader"]}: {why}'
    erupt = {c.ctx for c in cars if c.bid == 2019}
    assert erupt == {0}, f'Eruption 2019 should launch only at context 0 (BEH 300000042): {erupt}'
    mist = {c.ctx for c in cars if c.bid == 10722001}
    assert mist == {4}, f'Poison Mist cloud should be context 4: {mist}'
    # Each Eruption gate fails on its own: context 0 refuses the grease even with a nonzero
    # status scale, and its AtkParam zeroes the buildup even at context 1.
    soporific = reg.sp[3150]
    erupt_atk = reg.atk[reg.bullet[2019]['atkId_Bullet']]
    assert not regdata.accepts(0, soporific)
    assert regdata.accepts(1, soporific)
    assert not regdata.status_reaches(reg.sp[soporific['atkOccurrenceSpEffectId']], erupt_atk)
    # Poisonous Mist ash: context 1 passes the grease, but AtkParam_Pc 20 zeroes the buildup, so
    # its cloud spreads no grease; its repeated poison is its own bullet SpEffect 834.
    assert {c.ctx for c in cars if c.bid == 2416} == {1}
    on_mist = {r['id'] for q in pairs if q['reader'] == 'R1' and q['carrier'].bid == 2416
               for r in q['rows']}
    assert not on_mist & {3190, 3150, 3140, 1755, 831}, sorted(on_mist & {3190, 3150, 3140, 1755, 831})
    assert reg.bullet[2416]['spEffectId0'] == 834
    # And the positive passes both gates (falling arrow AtkParam_Pc 5036850, scale 0.65).
    arrow_atk = reg.atk[reg.bullet[20003354]['atkId_Bullet']]
    assert regdata.status_reaches(reg.sp[3191], arrow_atk) and regdata.status_scale(arrow_atk) > 0
    rank = pairs.index(p) + 1
    print(f'selftest ok: Rain of Arrows falling arrow x right-hand buff found at rank {rank} of '
          f'{len(pairs)} pairs, {len(p["rows"])} buff rows pass its gate; measured negatives '
          'rejected: ' + ', '.join(t for t, _ in readers.MEASURED_NEGATIVE.values())
          + '; flagged, gates pass but measured negative: '
          + ', '.join(t for t, _ in readers.CONTRADICTED.values()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--doc')
    ap.add_argument('--top', type=int, default=40)
    ap.add_argument('--min-window', type=float, default=1.0)
    o = ap.parse_args()
    if o.selftest:
        selftest()
        return
    reg, src, cars, pairs = run(o.min_window)
    if o.doc:
        write_doc(o.doc, reg, src, cars, pairs, o.top)
        print(f'{len(pairs)} pairs over {len(cars)} carriers -> {o.doc}')
    else:
        print_table(reg, src, pairs, o.top)
        print(f'{len(pairs)} pairs over {len(cars)} carriers')


if __name__ == '__main__':
    main()
