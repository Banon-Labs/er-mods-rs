#!/usr/bin/env python3
"""Check the sweep's variant containers against DXC's own validator, independently of the splicer.

For each `<sweep>/blobs/<hash>.<carrier>.dxil` picked, run `dxv -o=<signed>` (the validator writes
the container it would sign) and compare: the digest at bytes 4..20, every other byte, and the
parts present against the original game vertex shader. A container whose digest dxv computes differently would
be refused by the native D3D12 runtime and accepted by vkd3d-proton, which never reads the digest,
so a Proton run cannot catch it.

Writes one JSON line per container to `--out` and prints the totals.
"""
import argparse
import collections
import concurrent.futures
import glob
import json
import os
import random
import struct
import subprocess
import sys
import tempfile

DXV = os.path.expanduser('~/tools/dxc/bin/dxv')
DUMP = os.environ.get(
    'ER_VKD3D_DUMP_ROOT',
    '/home/banon/projects/er-mods-rs/.worktrees/er-shaders/target/vkd3d-dump/run1/spv')


def part_names(b):
    n = struct.unpack_from('<I', b, 28)[0]
    offs = struct.unpack_from('<%dI' % n, b, 32)
    return [b[o:o + 4].decode('latin1') for o in offs]


def check(path, tmp):
    base = os.path.basename(path)
    h, carrier, _ = base.split('.')
    b = open(path, 'rb').read()
    out = os.path.join(tmp, base)
    p = subprocess.run([DXV, '-o=' + out, path], capture_output=True, text=True, timeout=30)
    rec = dict(blob=base, carrier=carrier, dxv_ok=p.returncode == 0 and 'succeeded' in p.stdout)
    if os.path.exists(out):
        s = open(out, 'rb').read()
        rec['digest_equal'] = s[4:20] == b[4:20]
        rec['rest_equal'] = s[:4] == b[:4] and s[20:] == b[20:]
        os.remove(out)
    orig = open(os.path.join(DUMP, h + '.dxil'), 'rb').read()
    rec['parts'] = part_names(b)
    rec['orig_parts'] = part_names(orig)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sweep', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--carriers', default='wobble,branchy,loop,proof_lift')
    ap.add_argument('--sample', type=int, default=0, help='0 means every blob')
    ap.add_argument('--jobs', type=int, default=8)
    a = ap.parse_args()
    paths = []
    for c in a.carriers.split(','):
        paths += sorted(glob.glob(os.path.join(a.sweep, 'blobs', '*.%s.dxil' % c)))
    if a.sample:
        random.seed(1)
        paths = random.sample(paths, min(a.sample, len(paths)))
    tot = collections.Counter()
    with tempfile.TemporaryDirectory() as tmp, open(a.out, 'w') as f:
        with concurrent.futures.ThreadPoolExecutor(a.jobs) as ex:
            for rec in ex.map(lambda p: check(p, tmp), paths):
                f.write(json.dumps(rec) + '\n')
                tot['n'] += 1
                tot['dxv_ok'] += rec['dxv_ok']
                tot['digest_equal'] += bool(rec.get('digest_equal'))
                tot['rest_equal'] += bool(rec.get('rest_equal'))
                tot['parts ' + ','.join(rec['parts'])] += 1
                missing = sorted(set(rec['orig_parts']) - set(rec['parts']))
                tot['missing vs orig ' + ','.join(missing)] += 1
    for k, v in sorted(tot.items()):
        print(k, v)
    return 0


if __name__ == '__main__':
    sys.exit(main())
