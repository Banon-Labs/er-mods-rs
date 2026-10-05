#!/usr/bin/env python3
"""Print the generator tree under a behavior-graph state, with selector variable bindings.

Answers "how does the graph pick this clip": which state holds a node, which selector sits above
it, and which behavior variable (set from c0000.hks with SetVariable) drives that selector.

    python3 scripts/er-behbnd-tree.py --state DrawStanceRightStart
    python3 scripts/er-behbnd-tree.py --var IsEnoughArtPointsL2_DrawStanceRightStart
    python3 scripts/er-behbnd-tree.py --node SwordArtsOneShotComboEnd_24_CMSG
    python3 scripts/er-behbnd-tree.py --into SwordArtsOneShotComboEnd_2   # inbound transitions
    python3 scripts/er-behbnd-tree.py --selftest

Input: `ER_C0000_BEHAVIOR_HKX`, default the 1.17.1 extract
~/er-extract/1171-20261004-witchy/chr/c0000-behbnd-dcx-wanibnd/Behaviors/c0000.hkx.

The tagfile carries no member bodies for these types (`Tagfile.member_map` is empty for them), so
the offsets below were measured from the patch table: which offset of each item type points at
which item type, over the first 200 items of that type. State machine offsets are the ones
`er-behbnd-attack-map.py` already pins. Written for docs/er-mechanics/chainsaw/fallback-test-plan.md.
"""
import argparse
import collections
import importlib.util
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GRAPH = os.environ.get('ER_C0000_BEHAVIOR_HKX', os.path.expanduser(
    '~/er-extract/1171-20261004-witchy/chr/c0000-behbnd-dcx-wanibnd/Behaviors/c0000.hkx'))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BMAP = _load("er_behbnd_attack_map", "er-behbnd-attack-map.py")

# Layout measured on the 1.17.1 c0000.hkx (the tagfile carries no member bodies for these types):
# every hkbBindable keeps its hkbVariableBindingSet pointer at +0x18; the set keeps its Binding
# array at +0x18; a Binding is 0x28 bytes, memberPath at +0, variableIndex at +0x1c, bindingType
# at +0x21. hkbManualSelectorGenerator: generators at +0x98, selectedGeneratorIndex (i16) at +0xa8.
# hkbBehaviorGraphStringData: variableNames array at +0x38.
BINDSET, BIND_ARR, BIND_SIZE, BIND_PATH, BIND_VAR, BIND_TYPE = 0x18, 0x18, 0x28, 0, 0x1c, 0x21
MSG_SELECTED = 0xa8
STR_VARS = 0x38


class Tree:
    def __init__(self, path=GRAPH):
        self.g = BMAP.Graph(path)
        self.tf = tf = self.g.tf
        self.vars = []
        for it in tf.find_items('hkbBehaviorGraphStringData'):
            for off, tgt, cnt in tf.arrays_in(it, 0x58):
                if off - it['off'] == STR_VARS:
                    self.vars = tf.strs_of(tgt, cnt)
        self.byname = collections.defaultdict(list)
        for it in tf.items:
            tn = tf.tname(it['type'])
            if tn.startswith('hkb') or tn.startswith('Custom'):
                n = self.str_member(it, BMAP.NAME_OFF)
                if n:
                    self.byname[n].append(it['idx'])

    def str_member(self, it, off):
        p = self.tf.ptch.get(it['off'] + off)
        if p is None or self.tf.tname(self.tf.items[p]['type']) != 'char':
            return None
        return self.tf.cstr(p)

    def raw(self, it, off, fmt):
        return struct.unpack_from(fmt, self.tf.d, self.tf.data_off + it['off'] + off)[0]

    def bindings(self, it):
        """[(memberPath, variableName, bindingType)] of a node's hkbVariableBindingSet."""
        tf = self.tf
        bs = tf.ptch.get(it['off'] + BINDSET)
        if bs is None or tf.tname(tf.items[bs]['type']) != 'hkbVariableBindingSet':
            return []
        arr = tf.ptch.get(tf.items[bs]['off'] + BIND_ARR)
        if arr is None:
            return []
        ai = tf.items[arr]
        out = []
        for k in range(ai['count']):
            base = ai['off'] + k * BIND_SIZE
            p = tf.ptch.get(base + BIND_PATH)
            path = tf.cstr(p) if p is not None else '?'
            vi = struct.unpack_from('<i', tf.d, tf.data_off + base + BIND_VAR)[0]
            btype = tf.d[tf.data_off + base + BIND_TYPE]
            name = self.vars[vi] if 0 <= vi < len(self.vars) else f'#{vi}'
            out.append((path, name, btype))
        return out

    def children(self, idx):
        """Pointer targets of an item that are graph nodes (generators/modifiers)."""
        tf = self.tf
        it = tf.items[idx]
        end = self.g.item_end(idx)
        out = []
        for doff in sorted(d for d in tf.ptch if it['off'] <= d < end):
            t = tf.ptch[doff]
            tn = tf.tname(tf.items[t]['type'])
            if tn in ('char', 'hkbVariableBindingSet'):
                continue
            out.append(t)
        return out

    def describe(self, idx):
        tf = self.tf
        it = tf.items[idx]
        tn = tf.tname(it['type'])
        name = self.str_member(it, BMAP.NAME_OFF)
        extra = ''
        if tn == 'CustomManualSelectorGenerator':
            ot, anim = struct.unpack_from('<ii', tf.d, tf.data_off + it['off'] + 0xa8)
            kids = [c for c in self.g.arr(it, 0x98)]
            k0 = self.str_member(tf.items[kids[0]], BMAP.NAME_OFF) if kids and kids[0] is not None else None
            extra = f' offsetType={ot:#x} animId={anim} children={len(kids)} child0={k0!r}'
        elif tn == 'hkbManualSelectorGenerator':
            extra = f' selectedGeneratorIndex={self.raw(it, MSG_SELECTED, "<h")}'
        elif tn == 'hkbClipGenerator':
            extra = f' anim={self.str_member(it, BMAP.CLIP_ANIM)}'
        b = self.bindings(it)
        if b:
            extra += ' bind=' + ','.join(f'{p}<-{v}' for p, v, _ in b)
        return f'{tn} {name!r}{extra}'

    def dump(self, idx, depth=0, seen=None, maxdepth=8, out=None):
        seen = set() if seen is None else seen
        out = [] if out is None else out
        tn = self.tf.tname(self.tf.items[idx]['type'])
        line = '  ' * depth + self.describe(idx)
        if idx in seen:
            out.append(line + ' (seen)')
            return out
        seen.add(idx)
        out.append(line)
        if tn in ('hkbClipGenerator', 'CustomManualSelectorGenerator') or depth >= maxdepth:
            return out
        if tn == 'hkbStateMachine' and depth > 0:
            return out
        for c in self.children(idx):
            ctn = self.tf.tname(self.tf.items[c]['type'])
            if ctn in ('T*', 'hkbBlenderGeneratorChild', 'hkbBoneWeightArray', 'hkReal'):
                # array bodies (hkbGenerator*[]) and blender child records are walked through
                for cc in self.children(c):
                    self.dump(cc, depth + 1, seen, maxdepth, out)
                continue
            self.dump(c, depth + 1, seen, maxdepth, out)
        return out

    def states(self):
        """(sm name, state name, stateId, generator item, transitions array item, sm item)."""
        tf, g = self.tf, self.g
        for sm in tf.find_items('hkbStateMachine'):
            smname = g.s(sm, BMAP.NAME_OFF)
            for sp in g.arr(sm, BMAP.SM_STATES):
                if sp is None:
                    continue
                si = tf.items[sp]
                sid = struct.unpack_from('<i', tf.d, tf.data_off + si['off'] + BMAP.SI_ID)[0]
                yield smname, g.s(si, BMAP.SI_NAME), sid, g.ptr(si, BMAP.SI_GEN), \
                    g.ptr(si, BMAP.SI_TRANS), sm

    def transitions(self, arr_item):
        tf = self.tf
        if arr_item is None:
            return []
        body = self.g.ptr(tf.items[arr_item], 0x18)
        if body is None:
            return []
        bi = tf.items[body]
        out = []
        for k in range(bi['count']):
            base = tf.data_off + bi['off'] + k * BMAP.TI_SIZE
            eid, to = struct.unpack_from('<ii', tf.d, base + BMAP.TI_EVENT)
            ev = self.g.events[eid] if 0 <= eid < len(self.g.events) else f'#{eid}'
            out.append((ev, to))
        return out

    def into(self, state_name):
        """Every transition (wildcard or from a sibling state) that lands on `state_name`."""
        rows = []
        bysm = collections.defaultdict(dict)
        allst = list(self.states())
        for smname, sname, sid, gen, trans, sm in allst:
            bysm[sm['idx']][sid] = sname
        for sm in self.tf.find_items('hkbStateMachine'):
            names = bysm[sm['idx']]
            smname = self.g.s(sm, BMAP.NAME_OFF)
            for ev, to in self.transitions(self.g.ptr(sm, BMAP.SM_WILD)):
                if names.get(to) == state_name:
                    rows.append((smname, '*', ev))
        for smname, sname, sid, gen, trans, sm in allst:
            for ev, to in self.transitions(trans):
                if bysm[sm['idx']].get(to) == state_name:
                    rows.append((smname, sname, ev))
        return rows


def selftest():
    t = Tree()
    ok = True

    def check(name, cond):
        nonlocal ok
        print(('ok  ' if cond else 'FAIL') + ' ' + name)
        ok &= bool(cond)

    check('variable names read', len(t.vars) > 100)
    check('IsEnoughArtPointsL2 is a variable', 'IsEnoughArtPointsL2' in t.vars)
    st = [s for s in t.states() if s[1] == 'DrawStanceRightLoop']
    check('DrawStanceRightLoop state found', bool(st))
    tree = '\n'.join(t.dump(st[0][3]))
    check('loop tree reaches DrawStanceRightLoop_CMSG', 'DrawStanceRightLoop_CMSG' in tree)
    rows = t.into('DrawStanceRightLoop')
    check('W_DrawStanceRightLoop enters the loop', any(r[2] == 'W_DrawStanceRightLoop' for r in rows))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--state', action='append', default=[])
    ap.add_argument('--node', action='append', default=[], help='dump the tree under a named node')
    ap.add_argument('--var', action='append', default=[])
    ap.add_argument('--into', action='append', default=[])
    ap.add_argument('--depth', type=int, default=8)
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    t = Tree()
    for s in a.state:
        for smname, sname, sid, gen, trans, sm in t.states():
            if sname == s:
                print(f'== {smname} / {sname} (stateId {sid})')
                for ev, to in t.transitions(trans):
                    print(f'   out: {ev} -> stateId {to}')
                print('\n'.join(t.dump(gen, maxdepth=a.depth)))
    for n in a.node:
        for idx in t.byname.get(n, []):
            print('\n'.join(t.dump(idx, maxdepth=a.depth)))
    for v in a.var:
        for idx, it in enumerate(t.tf.items):
            tn = t.tf.tname(it['type'])
            if not (tn.startswith('hkb') or tn.startswith('Custom')):
                continue
            for p, name, _ in t.bindings(it):
                if name == v:
                    print(f'{v}: {t.describe(idx)}')
    for s in a.into:
        for row in t.into(s):
            print(f'{s} <- {row[0]} / {row[1]} on {row[2]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
