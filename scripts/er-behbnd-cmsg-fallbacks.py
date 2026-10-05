#!/usr/bin/env python3
"""Every CustomManualSelectorGenerator (CMSG) in the player behavior graph, and the clip it falls
back to when the current context's animation is not one of its children.

    python3 scripts/er-behbnd-cmsg-fallbacks.py                 # summary table
    python3 scripts/er-behbnd-cmsg-fallbacks.py --cmsg DrawStanceRightLoop_CMSG
    python3 scripts/er-behbnd-cmsg-fallbacks.py --json
    python3 scripts/er-behbnd-cmsg-fallbacks.py --borrow   # skill nodes whose child 0 hits
    python3 scripts/er-behbnd-cmsg-fallbacks.py --weapons  # weapon-category nodes, per weapon
    python3 scripts/er-behbnd-cmsg-fallbacks.py --selftest

Inputs: the 1.17.1 extract under ~/er-extract/1171-20261004-witchy (`ER_C0000_BEHAVIOR_HKX`,
`ER_PLAYER_TAE_DIR`). "Hits" counts TimeAct events of type 1 (attack), 2 (bullet) and 307 (PC
behavior) in the clip, imports followed (`er-mechanics-attacks.resolve_events`).

Written for docs/er-mechanics/chainsaw/cmsg-fallbacks.md.

The selection rule (`VERIFIED`, 1.16.2 named dump and 1.17.0 dump; 1.17.1 = 1.17.0 + 0x70):

* `CustomManualSelectorGenerator` select `FUN_1419b9440` [1.17.1 0x1419bb2b0] computes
  `id = resolve(offsetType, animId) + animId` (`FUN_1419ba0f0`, which calls the game callback
  only for offsetType 0xb and 0xd..0x12 and returns 0 otherwise).
* The name lookup `FUN_1419b9480` [1.17.1 0x1419bb2f0] formats `a%03d_%06d` from that id and
  takes the first child whose node name (`hkbNode::m_name`, +0x48) contains it; then tries
  `a000_%06d`; then returns index 0 (`count - 1` only when the array is empty).
* The resolver `FUN_14041aba0` [1.17.1 0x14041b0d0]: 0x12 = (swordArtsTypeNew + 600) * 1e6,
  0x11 = (magic refType + 400) * 1e6 when refType < 200, 0xd / 0xe / 0x10 = the hand weapon's
  motion category (0x10 picks the hand by arm style), 0xb = the idle category (+300 for the
  female variant when it exists), 0xf = an animation-controller lookup. The weapon-category
  cases go through `FUN_1403f1d40`, which first tries the weapon's `spAtkcategory`, then its
  motion category, then a hard-coded category (48 for hand 0, 23 for hand 1), keeping the
  first that the character's animation set has.

Offsets of the serialized node (checked against Ghidra's `CustomManualSelectorGenerator`):
+0x48 name, +0x98 generators, +0xa8 offsetType, +0xac animId, +0xb6 changeType.
"""

import argparse
import bisect
import collections
import importlib.util
import json
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXTRACT_1171 = os.path.expanduser('~/er-extract/1171-20261004-witchy/chr')
BEHBND_GRAPH = os.environ.get(
    'ER_C0000_BEHAVIOR_HKX',
    os.path.join(EXTRACT_1171, 'c0000-behbnd-dcx-wanibnd/Behaviors/c0000.hkx'))
# The 1.17.1 TimeAct copy; the shared helpers default to the 2026-07-13 extract, which is
# identical for a0x-a9x (bd c0000-hks-1171-extract-chainsaw-rule-unchanged-2026-10-04).
os.environ.setdefault('ER_PLAYER_TAE_DIR',
                      os.path.join(EXTRACT_1171, 'c0000-anibnd-dcx-wanibnd/INTERROOT_win64/chr/c0000/tae'))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


BMAP = _load('er_behbnd_attack_map', 'er-behbnd-attack-map.py')
ATK = _load('er_mechanics_attacks', 'er-mechanics-attacks.py')

CMSG_TYPE = 'CustomManualSelectorGenerator'
OFF_NAME, OFF_GENS, OFF_TYPE, OFF_ANIM, OFF_CHANGE = 0x48, 0x98, 0xa8, 0xac, 0xb6
#: offsetType -> what the resolver reads (`FUN_14041aba0`); names are this repo's labels.
OFFSET_KIND = {0: 'none', 0xb: 'idle category', 0xd: 'weapon category hand 1',
               0xe: 'weapon category hand 0', 0xf: 'animation-controller lookup',
               0x10: 'weapon category by arm style', 0x11: 'magic (400 + refType)',
               0x12: 'skill (600 + swordArtsTypeNew)'}
#: TimeAct event types that put damage in the world (attack, bullet, PC behavior).
HIT_EVENTS = (1, 2, 307)
CHILD_RE = re.compile(r'a(\d{3})_(\d{6})')


def tae_categories():
    d = ATK.PLAYER_TAE_DIR
    out = set()
    for name in os.listdir(d):
        stem = name[1:-4]
        if name.startswith('a') and name.endswith('.tae') and stem.isdigit():
            out.add(int(stem))
    return out


def clip_hits(category, anim):
    """Number of attack/bullet/behavior events the clip's TimeAct fires (imports followed), or
    None when the category has no such animation."""
    _c, _a, events = ATK.resolve_events(category, anim)
    if events is None:
        return None
    return sum(1 for e in events if e.type in HIT_EVENTS)


def tae_has(category, anim):
    return anim in (ATK.tae_animations(category) or {})


class Cmsgs:
    def __init__(self, path=BEHBND_GRAPH):
        self.g = g = BMAP.Graph(path)
        self.tf = tf = g.tf
        self._index()
        self.nodes = []
        for it in tf.find_items(CMSG_TYPE):
            base = tf.data_off + it['off']
            otype, anim = struct.unpack_from('<ii', tf.d, base + OFF_TYPE)
            change = tf.d[base + OFF_CHANGE]
            children = []
            for k, ci in enumerate(g.arr(it, OFF_GENS)):
                if ci is None:
                    children.append({'index': k, 'name': None, 'clips': []})
                    continue
                cit = tf.items[ci]
                children.append({'index': k, 'name': g.s(cit, OFF_NAME),
                                 'type': tf.tname(cit['type']),
                                 'clips': sorted(self.clips_under(ci))})
            self.nodes.append({'item': it['idx'], 'name': g.s(it, OFF_NAME), 'offsetType': otype,
                               'animId': anim, 'changeType': change, 'children': children})
        self._states()

    def _index(self):
        """item index -> items its blob points at (the patch table, bucketed once)."""
        tf = self.tf
        order = sorted(tf.items, key=lambda x: x['off'])
        starts = [it['off'] for it in order]
        self.out = collections.defaultdict(list)
        for doff, tgt in tf.ptch.items():
            k = bisect.bisect_right(starts, doff) - 1
            if k >= 0:
                self.out[order[k]['idx']].append(tgt)

    def clips_under(self, start):
        """Animation names of every hkbClipGenerator below a generator."""
        tf, out, stack, seen = self.tf, set(), [start], set()
        while stack:
            i = stack.pop()
            if i is None or i in seen:
                continue
            seen.add(i)
            it = tf.items[i]
            if tf.tname(it['type']) == 'hkbClipGenerator':
                n = self.g.s(it, BMAP.CLIP_ANIM)
                if n:
                    out.add(n)
                continue
            stack.extend(self.out.get(i, ()))
        return out

    def _states(self):
        """State names whose generator reaches each CMSG (pointer walk inside the state's tree)."""
        tf, g = self.tf, self.g
        want = {n['item']: n for n in self.nodes}
        for n in self.nodes:
            n['states'] = []
        for sm in tf.find_items('hkbStateMachine'):
            for sp in g.arr(sm, BMAP.SM_STATES):
                if sp is None:
                    continue
                si = tf.items[sp]
                gen = g.ptr(si, BMAP.SI_GEN)
                for hit in self._reach(gen, want):
                    want[hit]['states'].append(g.s(si, BMAP.SI_NAME))

    def _reach(self, start, want):
        out, stack, seen = set(), [start], set()
        while stack:
            i = stack.pop()
            if i is None or i in seen:
                continue
            seen.add(i)
            if i in want:
                out.add(i)
                continue                      # a CMSG's own children are not states of it
            it = self.tf.items[i]
            if self.tf.tname(it['type']) == 'hkbStateMachine':
                continue                      # nested machines own their states
            stack.extend(self.out.get(i, ()))
        return out

    def by_name(self, name):
        return [n for n in self.nodes if n['name'] == name]


def select(node, category):
    """The child index `FUN_1419b9480` returns for a resolved category: (index, how)."""
    kids = node['children']
    if not kids:
        return -1, 'empty'
    for key, how in ((f"a{category:03d}_{node['animId']:06d}", 'own'),
                     (f"a000_{node['animId']:06d}", 'a000')):
        for c in kids:
            if c['name'] and key in c['name']:
                return c['index'], how
    return 0, 'child 0'


def child_label(c):
    if c['name'] and CHILD_RE.search(c['name']):
        return c['name']
    return f"{c['name']} -> {','.join(map(str, c['clips']))}"


def child_clip(c):
    """(category, anim) the child plays, from its name."""
    m = CHILD_RE.search(c['name'] or '')
    return (int(m.group(1)), int(m.group(2))) if m else None


def analyse(node, cats):
    """Every category in `cats` that misses this node's own child, split by whether that
    category's TimeAct has the animation the node asks for."""
    rows = []
    for cat in sorted(cats):
        idx, how = select(node, cat)
        if how == 'own':
            continue
        rows.append({'category': cat, 'index': idx, 'how': how,
                     'tae_has_anim': tae_has(cat, node['animId'])})
    return rows


def own_categories(node):
    """Categories that have their own child: the skills (or weapons) this node was built for."""
    out = set()
    for c in node['children']:
        cc = child_clip(c)
        if cc and cc[1] == node['animId']:
            out.add(cc[0])
    return sorted(out)


#: `FUN_1403f1d40`'s last-resort category per hand (`VERIFIED` 1.16.2: 48000000 + anim for
#: hand 0, 23000000 + anim for hand 1; hand 2, the idle category, has none).
HAND_FALLBACK = {0: 48, 1: 23}
#: offsetType -> hands the resolver may ask (0x10 picks one by arm style, so both).
WEAPON_HANDS = {0xd: (1,), 0xe: (0,), 0x10: (0, 1), 0xb: (2,)}


def weapon_category(weapon, hand, anim):
    """The category `FUN_1403f1d40` resolves for this weapon in this hand (`DATA`: animation
    existence is read from the TimeAct, the game asks the loaded animation set)."""
    sp = weapon.get('spAtkcategory') or 0
    mc = weapon['wepmotionCategory']
    if hand in (0, 1) and sp and tae_has(sp, anim):
        return sp
    if tae_has(mc, anim):
        return mc
    fb = HAND_FALLBACK.get(hand)
    if fb is not None and tae_has(fb, anim):
        return fb
    return mc


def weapon_fallbacks(cm, reg, weapons):
    """Weapon-category nodes whose child 0 plays for some weapon: [(node, child0, hits,
    resolved category, weapon names)] where the resolved category has the animation in its
    TimeAct yet the node has neither its child nor an a000 child."""
    out = []
    for n in cm.nodes:
        hands = WEAPON_HANDS.get(n['offsetType'])
        if not hands or not n['children']:
            continue
        by_cat = collections.defaultdict(set)
        for wid in weapons:
            w = reg.weapon[wid]
            for h in hands:
                cat = weapon_category(w, h, n['animId'])
                idx, how = select(n, cat)
                if how == 'child 0' and tae_has(cat, n['animId']):
                    by_cat[cat].add(reg.weapon_names.get(wid) or str(wid))
        if by_cat:
            c0 = child_clip(n['children'][0])
            out.append({'name': n['name'], 'offsetType': n['offsetType'], 'animId': n['animId'],
                        'states': sorted(set(n['states'])), 'child0': child_label(n['children'][0]),
                        'child0_hits': clip_hits(*c0) if c0 else None,
                        'categories': {c: {'own_hits': clip_hits(c, n['animId']),
                                           'weapons': sorted(v)} for c, v in sorted(by_cat.items())}})
    return out


def universe(offset_type, cats):
    if offset_type == 0x12:
        return {c for c in cats if 600 <= c < 1000}
    if offset_type == 0x11:
        return {c for c in cats if 400 <= c < 600}
    return None


def report(nodes, cats, only=None):
    out = []
    for n in nodes:
        if only and n['name'] != only:
            continue
        kid0 = n['children'][0] if n['children'] else None
        c0 = child_clip(kid0) if kid0 else None
        entry = {'name': n['name'], 'offsetType': n['offsetType'],
                 'kind': OFFSET_KIND.get(n['offsetType'], '?'), 'animId': n['animId'],
                 'changeType': n['changeType'], 'states': sorted(set(n['states'])),
                 'children': [child_label(c) for c in n['children']],
                 'child0': child_label(kid0) if kid0 else None,
                 'child0_hits': clip_hits(*c0) if c0 else None}
        entry['own'] = own_categories(n)
        u = universe(n['offsetType'], cats)
        if u is not None:
            rows = analyse(n, u)
            entry['fallback_with_own_anim'] = [r['category'] for r in rows
                                               if r['tae_has_anim'] and r['how'] == 'child 0']
            entry['own_anim_hits'] = {r['category']: clip_hits(r['category'], n['animId'])
                                      for r in rows if r['tae_has_anim'] and r['how'] == 'child 0'}
            entry['fallback_without_anim'] = [r['category'] for r in rows
                                              if not r['tae_has_anim'] and r['how'] == 'child 0']
            entry['a000'] = [r['category'] for r in rows if r['how'] == 'a000']
        out.append(entry)
    return out


def skill_names():
    """swordArtsTypeNew + 600 -> skill names (SwordArtsParam)."""
    ash = _load('er_mechanics_ashes', 'er-mechanics-ashes.py')
    t = ash.AshTables()
    out = collections.defaultdict(list)
    for sid, row in sorted(t.arts.items()):
        out[600 + row['swordArtsTypeNew']].append(t.arts_name(sid))
    return out


def borrow_table(cm, cats):
    """Skill-category nodes whose child 0 fires hits, grouped by the clip child 0 plays. Every
    skill category without its own child gets that clip when the node activates; the TimeAct
    that fires is child 0's (argTaeId is parsed from the selected clip name)."""
    names = skill_names()
    groups = collections.OrderedDict()
    for n in cm.nodes:
        if n['offsetType'] != 0x12 or not n['children']:
            continue
        c0 = child_clip(n['children'][0])
        if not c0:
            continue
        hits = clip_hits(*c0)
        if not hits:
            continue
        g = groups.setdefault(c0, {'clip': f'a{c0[0]:03d}_{c0[1]:06d}', 'hits': hits,
                                   'animId': n['animId'], 'nodes': [], 'states': set(), 'own': set(),
                                   'real_misses': {}})
        g['nodes'].append(n['name'])
        g['states'].update(n['states'])
        g['own'].update(own_categories(n))
        for r in analyse(n, {x for x in cats if 600 <= x < 1000}):
            if r['tae_has_anim'] and r['how'] == 'child 0':
                h = clip_hits(r['category'], n['animId'])
                if h:
                    g['real_misses'][r['category']] = h
    out = []
    for c0, g in groups.items():
        g['skill0'] = '/'.join(names.get(c0[0], [])) or '?'
        g['own_skills'] = [f"a{c}:{'/'.join(names.get(c, [])) or '?'}" for c in sorted(g['own'])]
        g['states'] = sorted(g['states'])
        g['own'] = sorted(g['own'])
        g['real_misses'] = {f"a{c}:{'/'.join(names.get(c, [])) or '?'}": h
                            for c, h in sorted(g['real_misses'].items())}
        out.append(g)
    out.sort(key=lambda g: -g['hits'])
    return out


def selftest():
    passed = failed = 0

    def check(name, got, want):
        nonlocal passed, failed
        ok = got == want
        passed += ok
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'} {name}: got {got!r} want {want!r}")

    if not os.path.exists(BEHBND_GRAPH):
        print(f'SKIP: {BEHBND_GRAPH} absent (set ER_C0000_BEHAVIOR_HKX)')
        return 0
    c = Cmsgs()
    loop = c.by_name('DrawStanceRightLoop_CMSG')
    check('DrawStanceRightLoop_CMSG is unique', len(loop), 1)
    n = loop[0]
    check('offsetType is skill (0x12)', n['offsetType'], 0x12)
    check('animId', n['animId'], 40051)
    check('changeType NONE', n['changeType'], 0)
    check('child 0', child_clip(n['children'][0]), (839, 40051))
    check('a832 (Starscourge) has no own child', select(n, 832), (0, 'child 0'))
    check('a832.tae has no 040051', tae_has(832, 40051), False)
    check('a610 (Wild Strikes) has its own child', select(n, 610)[1], 'own')
    check('child 0 a839_040051 fires hits', (clip_hits(839, 40051) or 0) > 0, True)
    check('W_DrawStanceRightLoop reaches it', 'DrawStanceRightLoop' in ' '.join(n['states']), True)
    # A fallback that needs no weapon swap: Spinning Chain (a625) has its own no-FP stance start,
    # but the no-FP start node has no a625 child, so it plays Spinning Wheel's.
    nomp = c.by_name('DrawStanceRightStart_NoMP_CMSG')[0]
    check('no-FP start child 0', child_clip(nomp['children'][0]), (839, 40055))
    check('a625 has its own 040055 with hits', (clip_hits(625, 40055) or 0) > 0, True)
    check('a625 falls back to child 0', select(nomp, 625), (0, 'child 0'))
    # A weapon-category node (offsetType 0xd) with the same shape: the thrusting shields (a057)
    # carry their own 030600 but the node lists no a057 child.
    hs = c.by_name('AttackRightHeavySpecial1Start_CMSG')[0]
    check('heavy special start offsetType', hs['offsetType'], 0xd)
    check('heavy special start child 0', child_clip(hs['children'][0]), (30, 30600))
    check('a057 falls back to child 0', select(hs, 57), (0, 'child 0'))
    check('CMSG count', len(c.nodes) > 4000, True)
    print(f'{passed} passed, {failed} failed')
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--cmsg', help='one node by name')
    ap.add_argument('--borrow', action='store_true',
                    help='skill nodes whose child 0 hits: the clip any skill without its own plays')
    ap.add_argument('--weapons', action='store_true',
                    help='weapon-category nodes whose child 0 plays for some player weapon')
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    c = Cmsgs()
    if args.borrow:
        rows = borrow_table(c, tae_categories())
        if args.json:
            print(json.dumps(rows, indent=1))
            return 0
        for r in rows:
            print(f"{r['clip']} ({r['skill0']}) hits {r['hits']}  animId {r['animId']}  "
                  f"nodes {','.join(r['nodes'])}")
            print(f"    states: {','.join(r['states'])}")
            print(f"    built for: {', '.join(r['own_skills'])}")
            if r['real_misses']:
                print(f"    categories with their own clip but no child: {r['real_misses']}")
        return 0
    if args.weapons:
        reg = ATK.Regulation(None)
        weapons = [wid for wid, name in sorted(reg.weapon_names.items())
                   if wid in reg.weapon and wid % 10000 == 0 and name and not name.startswith('[')]
        rows = weapon_fallbacks(c, reg, weapons)
        if args.json:
            print(json.dumps(rows, indent=1))
            return 0
        for r in rows:
            print(f"{r['name']}  offsetType {r['offsetType']:#x}  animId {r['animId']}  child0 {r['child0']} "
                  f"hits {r['child0_hits']}  states {','.join(r['states'])}")
            for cat, v in r['categories'].items():
                print(f"    a{cat:03d} (own clip hits {v['own_hits']}): {', '.join(v['weapons'][:12])}"
                      + (f' +{len(v["weapons"]) - 12}' if len(v['weapons']) > 12 else ''))
        return 0
    rows = report(c.nodes, tae_categories(), args.cmsg)
    if args.json:
        print(json.dumps(rows, indent=1))
        return 0
    for r in rows:
        print(f"{r['name']}  offsetType {r['offsetType']:#x} ({r['kind']})  animId {r['animId']}  "
              f"change {r['changeType']}  children {len(r['children'])}  child0 {r['child0']} "
              f"hits {r['child0_hits']}")
        print(f"    states: {', '.join(r['states']) or '-'}")
        if 'fallback_with_own_anim' in r:
            print(f"    child-0 fallback, category HAS the anim: {r['fallback_with_own_anim']}")
            print(f"    child-0 fallback, category lacks it: {len(r['fallback_without_anim'])} categories")
            if r['a000']:
                print(f"    a000 fallback: {r['a000']}")
        if args.cmsg:
            print('    children: ' + ', '.join(r['children']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
