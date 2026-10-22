#!/usr/bin/env python3
"""Regression check: a throttled pool must not leave later pools on one CPU.

On 2026-10-20 the interrupt pass pinned combo-loadout to CPU 1 (`throttle`) and never gave the
other CPUs back, and the whiff stage's plain fork pool inherited that pin: ten workers shared
one core at 10% each for hours. This checks both halves. `caller_throttled` restores the caller's
affinity, and workers started by `_worker_init` each sit on their own core whatever the caller holds.
"""
import importlib.util
import multiprocessing as mp
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_BARRIER = None


def _affinity(_):
    # Both workers wait here, so each item lands on a different worker.
    _BARRIER.wait(timeout=20)
    return sorted(os.sched_getaffinity(0))


def main() -> int:
    AI = load("er_mechanics_ash_interrupt", "er-mechanics-ash-interrupt.py")
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok   " if cond else "FAIL ") + msg)
        ok = ok and bool(cond)

    before = os.sched_getaffinity(0)
    cpus = AI.allowed_cpus()
    if len(cpus) < 3:
        print(f"skip: {len(cpus)} usable CPUs, need 3")
        return 0
    with AI.caller_throttled(cpus[0]):
        check(os.sched_getaffinity(0) == {cpus[0]}, "caller_throttled pins the caller to one CPU")
        ctx = mp.get_context("fork")
        global _BARRIER
        _BARRIER = ctx.Barrier(2)
        workers = cpus[1:3]
        with ctx.Pool(2, initializer=AI._worker_init, initargs=(workers, ctx.Value("i", 0))) as pool:
            seen = pool.map(_affinity, range(2), chunksize=1)
        check(sorted(map(tuple, seen)) == sorted((c,) for c in workers),
              f"_worker_init puts each worker on its own core under a pinned caller ({seen})")
    check(os.sched_getaffinity(0) == before, "caller_throttled gives the caller its CPUs back")
    print("throttle affinity: " + ("ok" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
