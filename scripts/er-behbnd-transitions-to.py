#!/usr/bin/env python3
"""Every way into a named hkbStateMachine state of a behavior graph, walked up to the root.

    python3 scripts/er-behbnd-transitions-to.py DrawStanceNoSyncLoop
    python3 scripts/er-behbnd-transitions-to.py DrawStanceNoSyncLoop_Upper --graph <c0000.hkx>

For the state, prints each transition that targets it (wildcard, or from which sibling state)
with the event id and name; then, for the state machine that owns it, the states in other
state machines whose generator subtree contains that machine, and the transitions into those,
up to `--depth` levels. A machine entered fresh begins in its start state with no transition,
so a state with no incoming transition can still be entered that way.

Layouts are those of er-behbnd-attack-map.py (hkbStateMachine +0xE0 states, +0xF0 wildcard
transitions; StateInfo +0x58 transitions, +0x60 generator, +0x68 name, +0x70 id;
TransitionInfo 72 bytes, +0x30 eventId, +0x34 toStateId).
"""
import argparse, importlib.util, os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
_s = importlib.util.spec_from_file_location('amap', os.path.join(HERE, 'er-behbnd-attack-map.py'))
amap = importlib.util.module_from_spec(_s); _s.loader.exec_module(amap)

DEFAULT_GRAPH = os.environ.get('ER_C0000_BEHAVIOR_HKX', os.path.expanduser(
    '~/er-extract/1171-20261004-witchy/chr/c0000-behbnd-dcx-wanibnd/Behaviors/c0000.hkx'))


class Index:
    def __init__(self, path):
        self.g = g = amap.Graph(path)
        tf = g.tf
        self.sms = []          # (sm_item_idx, name, {sid: (name, gen, si_idx)}, [(from, eid, to)])
        for sm in tf.find_items('hkbStateMachine'):
            states = {}
            trans = []
            for sp in g.arr(sm, amap.SM_STATES):
                if sp is None:
                    continue
                si = tf.items[sp]
                sid = struct.unpack_from('<i', tf.d, tf.data_off + si['off'] + amap.SI_ID)[0]
                states[sid] = (g.s(si, amap.SI_NAME), g.ptr(si, amap.SI_GEN), sp)
            srcs = [(None, g.ptr(sm, amap.SM_WILD))]
            for sid, (_n, _g, sp) in states.items():
                srcs.append((sid, g.ptr(tf.items[sp], amap.SI_TRANS)))
            for frm, a in srcs:
                if a is None:
                    continue
                body = g.ptr(tf.items[a], 0x18)
                if body is None:
                    continue
                bi = tf.items[body]
                for k in range(bi['count']):
                    base = tf.data_off + bi['off'] + k * amap.TI_SIZE
                    eid, to = struct.unpack_from('<ii', tf.d, base + amap.TI_EVENT)
                    trans.append((frm, eid, to))
            self.sms.append((sm['idx'], g.s(sm, amap.NAME_OFF), states, trans))
        # child pointers of every item, built once: patches sorted by data offset, sliced by
        # each item's [off, end) range.
        import bisect
        pt = sorted(tf.ptch.items())
        keys = [d for d, _t in pt]
        self.children = {}
        for it in tf.items:
            if it is None or 'off' not in it:
                continue
            lo = bisect.bisect_left(keys, it['off'])
            hi = bisect.bisect_left(keys, g.item_end(it['idx']))
            self.children[it['idx']] = [t for _d, t in pt[lo:hi]]

    def ev(self, eid):
        return self.g.events[eid] if 0 <= eid < len(self.g.events) else f'<{eid}>'

    def reaches(self, start, target, seen=None, depth=0):
        """Does the generator subtree at item `start` contain item `target`? Stops at nested
        state machines other than the target, so the answer is the nearest enclosing state."""
        if start is None or depth > 40:
            return False
        if start == target:
            return True
        seen = seen if seen is not None else set()
        if start in seen:
            return False
        seen.add(start)
        tf = self.g.tf
        it = tf.items[start]
        if depth and tf.tname(it['type']) == 'hkbStateMachine':
            return False
        for tgt in self.children.get(start, ()):
            if self.reaches(tgt, target, seen, depth + 1):
                return True
        return False

    def report(self, state_name, depth, indent=''):
        for smi, smname, states, trans in self.sms:
            for sid, (n, _gen, _sp) in states.items():
                if n != state_name:
                    continue
                print(f'{indent}state {n!r} id {sid} in SM {smname!r} (item {smi})')
                for frm, eid, to in trans:
                    if to != sid:
                        continue
                    src = 'WILDCARD' if frm is None else f'from {states.get(frm, ("?",))[0]!r} ({frm})'
                    print(f'{indent}  <- event {eid} {self.ev(eid)!r}  {src}')
                if depth <= 0:
                    continue
                for psmi, psmname, pstates, _pt in self.sms:
                    if psmi == smi:
                        continue
                    for psid, (pn, pgen, _psp) in pstates.items():
                        if self.reaches(pgen, smi):
                            print(f'{indent}  SM {smname!r} is inside state {pn!r} of {psmname!r}:')
                            self.report(pn, depth - 1, indent + '    ')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('state', nargs='+')
    ap.add_argument('--graph', default=DEFAULT_GRAPH)
    ap.add_argument('--depth', type=int, default=2)
    a = ap.parse_args()
    ix = Index(a.graph)
    for s in a.state:
        ix.report(s, a.depth)


if __name__ == '__main__':
    main()
