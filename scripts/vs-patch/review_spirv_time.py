#!/usr/bin/env python3
"""Time dxil-spirv on game vertex shaders and on their spliced variants: the translation vkd3d-proton runs
inside `CreatePipelineState` before it compiles the Vulkan pipeline.

Wall time per process, so it includes process start; the trivial-shader row measures that floor.
One process at a time. Prints medians and p95 in milliseconds.
"""
import argparse
import glob
import os
import random
import statistics
import subprocess
import sys
import tempfile
import time

DUMP = os.environ.get(
    'ER_VKD3D_DUMP_ROOT',
    '/home/banon/projects/er-mods-rs/.worktrees/er-shaders/target/vkd3d-dump/run1/spv')


def run(tool, path, out):
    t = time.perf_counter()
    p = subprocess.run([tool, path, '--invariant-position', '--ssbo-srv', '--output', out], timeout=30,
                       capture_output=True)
    return (time.perf_counter() - t) * 1000, p.returncode


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sweep', required=True)
    ap.add_argument('--tool', required=True)
    ap.add_argument('--carrier', default='wobble')
    ap.add_argument('--sample', type=int, default=80)
    a = ap.parse_args()
    blobs = sorted(glob.glob(os.path.join(a.sweep, 'blobs', '*.%s.dxil' % a.carrier)))
    random.seed(2)
    blobs = random.sample(blobs, min(a.sample, len(blobs)))
    orig, var = [], []
    fails = 0
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, 'o.spv')
        for b in blobs:
            h = os.path.basename(b).split('.')[0]
            to, ro = run(a.tool, os.path.join(DUMP, h + '.dxil'), out)
            tv, rv = run(a.tool, b, out)
            fails += (ro != 0) + (rv != 0)
            orig.append(to)
            var.append(tv)
    for name, xs in (('original', orig), ('variant', var)):
        print('%s n=%d median=%.2f p95=%.2f' % (name, len(xs), statistics.median(xs), pct(xs, 0.95)))
    print('failures', fails)
    return 0


if __name__ == '__main__':
    sys.exit(main())
