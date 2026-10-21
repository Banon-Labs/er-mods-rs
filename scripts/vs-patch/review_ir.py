#!/usr/bin/env python3
"""Recount IR-level claims of the vertex-layer design from the sweep's disassemblies.

Reads `<sweep>/work/<hash>/orig.ll` (the game's vertex shader as `dxc -dumpbin` prints it) and
`<sweep>/results.jsonl`, and prints one block per claim: pass tallies, signature element order
against register order, the cbuffers each vertex shader binds, fast-math flags on the game's own position
arithmetic, and which inputs feed `SV_Position`.
"""
import argparse
import collections
import json
import os
import re
import sys

SIG_RE = re.compile(
    r'^!(\d+) = !\{i32 (\d+), !"([A-Za-z_0-9]+)", i8 (\d+), i8 (\d+), !(\d+), i8 (\d+), '
    r'i32 (\d+), i8 (\d+), i32 (-?\d+), i8 (-?\d+)')


def metadata(text):
    nodes = {}
    for line in text.splitlines():
        m = re.match(r'^!(\d+) = (.*)$', line)
        if m:
            nodes[int(m.group(1))] = m.group(2)
    return nodes


def signatures(text):
    nodes = metadata(text)
    ep = re.search(r'^!dx\.entryPoints = !\{!(\d+)\}', text, re.M)
    if not ep:
        return None, None
    entry = nodes[int(ep.group(1))]
    refs = re.findall(r'!(\d+|null)', entry)
    sig_ref = int(refs[0]) if refs and refs[0] != 'null' else None
    if sig_ref is None:
        return [], []
    io = re.findall(r'(!\d+|null)', nodes[sig_ref])

    def elements(ref):
        if ref == 'null':
            return []
        out = []
        for el in re.findall(r'!(\d+)', nodes[int(ref[1:])]):
            m = SIG_RE.match('!%s = %s' % (el, nodes[int(el)]))
            if m:
                out.append(dict(id=int(m.group(2)), name=m.group(3), comp=int(m.group(4)),
                                sv=int(m.group(5)), rows=int(m.group(8)), cols=int(m.group(9)),
                                row=int(m.group(10)), col=int(m.group(11))))
        return out
    return elements(io[0]), elements(io[1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sweep', required=True)
    a = ap.parse_args()
    rows = [json.loads(line) for line in open(os.path.join(a.sweep, 'results.jsonl'))]
    base = {r['hash']: r for r in rows if r['carrier'] == 'none'}
    wob = {r['hash']: r for r in rows if r['carrier'] == 'wobble'}
    print('vs rows', len(base))
    per = collections.Counter(r['pass'] for r in base.values())
    app = collections.Counter(r['pass'] for h, r in wob.items() if r['status'] == 'applied')
    print('pass: vs applied', sorted(((k, per[k], app[k]) for k in per), key=lambda x: -x[1]))
    groups_by_pass = collections.defaultdict(set)
    for r in base.values():
        groups_by_pass[r['group']].add(r['pass'])
    gbuf_groups = [g for g, s in groups_by_pass.items() if 'Gbuf' in s]
    no_depth = [g for g in gbuf_groups if not ({'Dpt', 'DptA'} & groups_by_pass[g])]
    velo = [g for g, s in groups_by_pass.items() if 'Velo' in s]
    print('groups', len(groups_by_pass), 'with gbuf', len(gbuf_groups), 'gbuf without own depth', len(no_depth),
          'with velo', len(velo))
    gbuf_no_velo = [g for g in gbuf_groups if 'Velo' not in groups_by_pass[g]]
    print('gbuf groups without velo', len(gbuf_no_velo))

    in_order = out_order = 0
    cb = collections.Counter()
    pos_type = collections.Counter()
    fast_pos = collections.Counter()
    n = 0
    for h in base:
        path = os.path.join(a.sweep, 'work', h, 'orig.ll')
        if not os.path.exists(path):
            continue
        text = open(path).read()
        n += 1
        ins, outs = signatures(text)
        if ins is None:
            continue
        for els, which in ((ins, 'in'), (outs, 'out')):
            regs = [(e['row'], e['col']) for e in els]
            if regs != sorted(regs):
                if which == 'in':
                    in_order += 1
                else:
                    out_order += 1
        for e in ins:
            if e['name'] == 'POSITION' and e['id'] is not None:
                pos_type[(e['comp'], e['cols'])] += 1
                break
        for m in set(re.findall(r'^; cbuffer (\w+)', text, re.M)):
            cb[m] += 1
        ops = re.findall(r'= (fmul|fadd|fsub|fdiv)( fast| reassoc| nnan| ninf| nsz| arcp)* float', text)
        fast_pos['fast' if any(o[1] for o in ops) else 'none'] += 1
        fast_pos['has_precise_op'] += 1 if re.search(r'= (fmul|fadd|fsub) float', text) else 0
    print('orig.ll read', n)
    print('input element order differs from register order', in_order)
    print('output element order differs from register order', out_order)
    print('first POSITION input (component type, cols)', pos_type.most_common())
    print('cbuffers bound by VS', cb.most_common(12))
    print('float ops with fast flags', dict(fast_pos))
    return 0


if __name__ == '__main__':
    sys.exit(main())
