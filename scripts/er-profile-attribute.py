#!/usr/bin/env python3
"""Attribute an `er-boot-profiler` sample stream to threads, and our DLL's samples to functions.

The profiler writes one jsonl line per tick holding every thread's rip, its cumulative user and
kernel time, and (where it could walk one) a short stack. This reads that stream back and answers
the only question that decides whether our code is slowing a load down:

    how many of the GAME MAIN THREAD's ticks are executing under our DLL?

A sample whose rip is in our DLL is self time; a sample whose *stack* touches our DLL is inclusive
time -- the main thread is inside our hook, whatever it is calling. Inclusive main-thread time is
the number that sits on the critical path. A worker thread's self time does not, on a 16-core host,
and reporting it as if it did is how a concurrent cost gets mistaken for a serial one.

Usage:
  python3 scripts/er-profile-attribute.py <profile.jsonl> [lo_ms] [hi_ms] [dll_size]

Emitted rvas are module-relative, so they feed straight into:
  llvm-symbolizer --obj=<dll> --relative-address <rva>
"""

from __future__ import annotations

import json
import sys
from collections import Counter

DEFAULT_DLL_SIZE = 0x400000


def load(path: str) -> tuple[int, dict, list[dict]]:
    """Return (dll_base, header, ticks). The build banner carries the base, the first json the rest."""
    dll_base = 0
    header: dict = {}
    ticks: list[dict] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith("build "):
                for token in line.split():
                    if token.startswith("base="):
                        dll_base = int(token.split("=", 1)[1], 16)
                continue
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if obj.get("kind") == "header":
                header = obj
            elif "ms" in obj:
                ticks.append(obj)
    return dll_base, header, ticks


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    path = sys.argv[1]
    lo_ms = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    hi_ms = int(sys.argv[3]) if len(sys.argv) > 3 else 10**9
    dll_size = int(sys.argv[4], 0) if len(sys.argv) > 4 else DEFAULT_DLL_SIZE

    dll_base, header, ticks = load(path)
    if not dll_base:
        print("no build banner with base= in this file")
        return 1
    interval = int(header.get("interval_ms", 25))
    print(f"dll_base=0x{dll_base:x} size=0x{dll_size:x} interval={interval}ms ncpu={header.get('ncpu')}")

    def in_dll(addr: int) -> bool:
        return dll_base <= addr < dll_base + dll_size

    self_rva: Counter = Counter()
    thread_self: Counter = Counter()
    thread_incl: Counter = Counter()
    thread_total: Counter = Counter()
    names: dict[int, str] = {}
    first_ms = last_ms = None
    n_ticks = 0

    for tick in ticks:
        ms = tick["ms"]
        if ms < lo_ms or ms > hi_ms:
            continue
        if first_ms is None:
            first_ms = ms
        last_ms = ms
        n_ticks += 1
        for thread in tick.get("t", []):
            tid = thread["id"]
            if "n" in thread:
                names[tid] = thread["n"]
            thread_total[tid] += 1
            rip = thread.get("rip", 0)
            stack = thread.get("stk", [])
            hit_self = in_dll(rip)
            if hit_self:
                thread_self[tid] += 1
                self_rva[(tid, rip - dll_base)] += 1
            if hit_self or any(in_dll(frame) for frame in stack):
                thread_incl[tid] += 1

    if first_ms is None:
        print("no ticks in window")
        return 1
    span = (last_ms - first_ms) / 1000.0
    print(f"window {first_ms}..{last_ms} ms ({span:.2f}s), {n_ticks} ticks")
    print()
    print(f"{'tid':>6} {'name':<32} {'ticks':>6} {'self':>6} {'incl':>6} {'incl s':>8}")
    for tid, total in thread_total.most_common():
        if thread_self[tid] == 0 and thread_incl[tid] == 0:
            continue
        incl = thread_incl[tid]
        print(
            f"{tid:>6} {names.get(tid, ''):<32} {total:>6} "
            f"{thread_self[tid]:>6} {incl:>6} {incl * interval / 1000:>8.2f}"
        )
    print()
    print("top in-DLL rvas:")
    for (tid, rva), count in self_rva.most_common(30):
        print(f"  tid {tid:<6} 0x{rva:<8x} {count:>4} ticks ({count * interval / 1000:.2f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
