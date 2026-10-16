#!/usr/bin/env python3
"""Independent recount of the vertex-layer design's corpus numbers, for its adversarial review.

Reads the vkd3d-proton dump (`ER_VKD3D_DUMP_ROOT`, default run1's `spv/`) with its own container
parser rather than the prototype's, so a number both tools agree on was counted twice by different
code. Prints one block per question and writes `vs_names.json` beside `--out`.
"""
import argparse
import collections
import glob
import json
import os
import struct
import sys

DUMP = os.environ.get(
    'ER_VKD3D_DUMP_ROOT',
    '/home/banon/projects/er-mods-rs/.worktrees/er-shaders/target/vkd3d-dump/run1/spv')


def parts(b):
    if b[:4] != b'DXBC':
        raise ValueError('not a container')
    n = struct.unpack_from('<I', b, 28)[0]
    offs = struct.unpack_from('<%dI' % n, b, 32)
    out = {}
    for o in offs:
        cc = b[o:o + 4].decode('latin1')
        sz = struct.unpack_from('<I', b, o + 4)[0]
        out.setdefault(cc, []).append(b[o + 8:o + 8 + sz])
    return out


def kind(p):
    d = p.get('DXIL', [None])[0]
    if d is None:
        return None
    v = struct.unpack_from('<I', d, 0)[0]
    return (v >> 16, (v >> 4) & 0xf, v & 0xf)


def ildn(p):
    d = p.get('ILDN', [None])[0]
    if d is None:
        return None
    _, ln = struct.unpack_from('<HH', d, 0)
    return d[4:4 + ln].decode('latin1').rstrip('\0')


def sig(d):
    if d is None:
        return []
    n, off = struct.unpack_from('<II', d, 0)
    els = []
    for i in range(n):
        _, no, si, sv, ct, reg, mask, rw, _, mp = struct.unpack_from('<IIIIIIBBHI', d, off + i * 32)
        e = d.index(b'\0', no)
        els.append(dict(name=d[no:e].decode(), idx=si, sv=sv, ct=ct, reg=reg, mask=mask, rw=rw, mp=mp))
    return els


def load_all(root):
    res = []
    for f in sorted(glob.glob(root + '/*.dxil')):
        b = open(f, 'rb').read()
        try:
            p = parts(b)
        except (ValueError, struct.error):
            continue
        res.append((os.path.basename(f)[:-5], b, p))
    return res


def pass_token(name):
    stem = name.split('@')[0].replace('.vpo.pdb', '')
    t = stem.split('_')
    return t[-1] if len(t) > 1 else '?'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    allc = load_all(DUMP)
    print('containers', len(allc))
    print('kinds', collections.Counter(kind(p)[0] if kind(p) else None for _, _, p in allc))
    vs = [(h, b, p) for h, b, p in allc if kind(p) and kind(p)[0] == 1]
    print('vs', len(vs), 'models', collections.Counter(kind(p)[1:] for _, _, p in vs))
    rts = collections.Counter()
    for _, _, p in allc:
        k = kind(p)
        if k and k[0] in (0, 1, 2, 3, 4):
            r = p.get('RTS0')
            if r:
                ver, _, _, _, _, flags = struct.unpack_from('<IIIIII', r[0], 0)
                rts[(ver, flags)] += 1
            else:
                rts['none'] += 1
    print('raster rts0 (version, flags)', dict(rts))
    print('vs part sets', collections.Counter(tuple(sorted(p.keys())) for _, _, p in vs).most_common(6))
    names = {h: (ildn(p) or '') for h, _, p in vs}
    print('vs pass tokens', collections.Counter(pass_token(n) for n in names.values()).most_common(40))
    order_differs = 0
    for _, _, p in vs:
        els = sig(p.get('OSG1', [None])[0])
        regs = [(e['reg'], e['mask'] & -e['mask'] if e['mask'] else 0) for e in els]
        if regs != sorted(regs):
            order_differs += 1
    print('osg1 element order differs from register order', order_differs)
    os.makedirs(a.out, exist_ok=True)
    json.dump(names, open(os.path.join(a.out, 'vs_names.json'), 'w'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
