#!/usr/bin/env python3
"""Three independent rechecks of the vertex-layer design's prototype numbers.

1. Signature linkage: for every `<sweep>/blobs/<hash>.<carrier>.dxil`, ISG1 and OSG1 are
   byte-identical to the game vertex shader's, and PSV0 differs only where the design says it may.
2. Round trip: dxil-spirv on `<sweep>/work/<hash>/rt.dxil` (the no-carrier reassembly) gives the
   same SPIR-V bytes as on the original, for `--sample` shaders.
3. The V2 insertion point: vertex shaders with at least three `fsub` whose subtrahend is a component of
   `cbSceneParam` register 3 (`SC_CameraPos`).
"""
import argparse
import collections
import glob
import os
import random
import re
import struct
import subprocess
import sys
import tempfile

DUMP = os.environ.get(
    'ER_VKD3D_DUMP_ROOT',
    '/home/banon/projects/er-mods-rs/.worktrees/er-shaders/target/vkd3d-dump/run1/spv')


def parts(b):
    n = struct.unpack_from('<I', b, 28)[0]
    out = {}
    for o in struct.unpack_from('<%dI' % n, b, 32):
        out[b[o:o + 4].decode('latin1')] = b[o + 8:o + 8 + struct.unpack_from('<I', b, o + 4)[0]]
    return out


def linkage(sweep, carrier):
    c = collections.Counter()
    for f in glob.glob(os.path.join(sweep, 'blobs', '*.%s.dxil' % carrier)):
        h = os.path.basename(f).split('.')[0]
        v = parts(open(f, 'rb').read())
        o = parts(open(os.path.join(DUMP, h + '.dxil'), 'rb').read())
        c['n'] += 1
        c['isg1_equal'] += v['ISG1'] == o['ISG1']
        c['osg1_equal'] += v['OSG1'] == o['OSG1']
        c['psv0_equal'] += v['PSV0'] == o['PSV0']
        c['sfi0_equal'] += v['SFI0'] == o['SFI0']
    return dict(c)


def round_trip(sweep, tool, sample):
    dirs = sorted(glob.glob(os.path.join(sweep, 'work', '*', 'rt.dxil')))
    random.seed(3)
    dirs = random.sample(dirs, min(sample, len(dirs)))
    c = collections.Counter()
    with tempfile.TemporaryDirectory() as tmp:
        a, b = os.path.join(tmp, 'a.spv'), os.path.join(tmp, 'b.spv')
        for rt in dirs:
            h = os.path.basename(os.path.dirname(rt))
            for src, out in ((os.path.join(DUMP, h + '.dxil'), a), (rt, b)):
                subprocess.run([tool, src, '--invariant-position', '--ssbo-srv', '--output', out], timeout=30,
                               capture_output=True)
            c['n'] += 1
            c['spirv_identical'] += open(a, 'rb').read() == open(b, 'rb').read()
    return dict(c)


def camera_fsub(sweep):
    n = hit = 0
    for ll in glob.glob(os.path.join(sweep, 'work', '*', 'orig.ll')):
        t = open(ll).read()
        n += 1
        m = re.search(r'^; cbSceneParam\s+cbuffer\s+NA\s+NA\s+CB(\d+)\s', t, re.M)
        if not m:
            continue
        rid = m.group(1)
        handles = set(re.findall(
            r'(%\w+) = call %dx\.types\.Handle @dx\.op\.createHandle\(i32 57, i8 2, i32 ' + rid + ',', t))
        rows = set()
        for hnd in handles:
            rows |= set(re.findall(
                r'(%\w+) = call %dx\.types\.CBufRet\.f32 @dx\.op\.cbufferLoadLegacy\.f32\(i32 59, '
                r'%dx\.types\.Handle ' + re.escape(hnd) + r', i32 3\)', t))
        comps = set()
        for r in rows:
            comps |= set(re.findall(r'(%\w+) = extractvalue %dx\.types\.CBufRet\.f32 ' + re.escape(r)
                                    + r', [012]\b', t))
        subs = [s for s in re.findall(r'= fsub fast float %\w+, (%\w+)', t) if s in comps]
        hit += len(subs) >= 3
    return dict(vs=n, with_three_camera_fsub=hit)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sweep', required=True)
    ap.add_argument('--tool', required=True)
    ap.add_argument('--sample', type=int, default=60)
    a = ap.parse_args()
    for carrier in ('wobble', 'branchy'):
        print('linkage', carrier, linkage(a.sweep, carrier))
    print('round trip', round_trip(a.sweep, a.tool, a.sample))
    print('camera fsub', camera_fsub(a.sweep))
    return 0


if __name__ == '__main__':
    sys.exit(main())
