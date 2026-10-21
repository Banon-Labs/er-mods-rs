#!/usr/bin/env python3
"""Count the vkd3d-proton SPIR-V of dumped vertex shaders whose `Position` is decorated `Invariant`.

The design (section 4, point 3) rests cross-pass position equality under Proton on that decoration
and cites 393 of the first 400. This reads every `<hash>.spv` beside a vertex shader's `<hash>.dxil`.
"""
import collections
import os
import struct
import sys

DUMP = os.environ.get(
    'ER_VKD3D_DUMP_ROOT',
    '/home/banon/projects/er-mods-rs/.worktrees/er-shaders/target/vkd3d-dump/run1/spv')
OP_DECORATE, OP_MEMBER_DECORATE = 71, 72
DEC_BUILTIN, DEC_INVARIANT = 11, 18
BUILTIN_POSITION = 0


def is_vs(path):
    b = open(path, 'rb').read()
    if b[:4] != b'DXBC':
        return False
    n = struct.unpack_from('<I', b, 28)[0]
    for o in struct.unpack_from('<%dI' % n, b, 32):
        if b[o:o + 4] == b'DXIL':
            return struct.unpack_from('<I', b, o + 8)[0] >> 16 == 1
    return False


def scan(path):
    w = open(path, 'rb').read()
    words = struct.unpack('<%dI' % (len(w) // 4), w)
    if words[0] != 0x07230203:
        return None
    i = 5
    pos, inv = set(), set()
    while i < len(words):
        op = words[i] & 0xffff
        cnt = words[i] >> 16
        if cnt == 0:
            break
        if op == OP_DECORATE:
            target, dec = words[i + 1], words[i + 2]
            if dec == DEC_BUILTIN and words[i + 3] == BUILTIN_POSITION:
                pos.add(target)
            if dec == DEC_INVARIANT:
                inv.add(target)
        i += cnt
    if not pos:
        return 'no_position_builtin'
    return 'invariant' if pos & inv else 'not_invariant'


def main():
    c = collections.Counter()
    missing = []
    for f in sorted(os.listdir(DUMP)):
        if not f.endswith('.dxil'):
            continue
        d = os.path.join(DUMP, f)
        if not is_vs(d):
            continue
        s = d[:-5] + '.spv'
        if not os.path.exists(s):
            c['no_spv'] += 1
            continue
        r = scan(s)
        c[r] += 1
        if r == 'not_invariant':
            missing.append(f[:-5])
    print(dict(c))
    print('not invariant, first 20:', missing[:20])
    return 0


if __name__ == '__main__':
    sys.exit(main())
