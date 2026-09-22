#!/usr/bin/env python3
"""Attribute an `er-boot-profiler` sample stream to threads, and our DLL's samples to functions.

The profiler writes one jsonl line per tick holding every thread's rip, its cumulative user and
kernel time, and (where it could walk one) a short stack. This reads that stream back and answers
the only question that decides whether our code is slowing a load down:

    how many of the game main thread's ticks are executing under our DLL?

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
    print()
    print(buckets(ticks, dll_base, dll_size, header, lo_ms, hi_ms))
    return 0


# The game's own image, so a rip can be called "in the game" rather than merely "not in our dll".
# Its base is in the header; the span is the largest `.text`+data extent Elden Ring 1.17 occupies.
GAME_IMAGE_SPAN = 0x4000000


def buckets(ticks, dll_base, dll_size, header, lo_ms, hi_ms, width_ms=500):
    """Per-bucket picture of what the game's main thread was doing, and whether it was doing it.

    The column that decides a load-time question is `cpu%`: jiffies the main thread actually burned
    over the bucket's wall time. A thread pegged near one core is working, and a slower load under
    our dll means more work. A thread at a fraction of a core is waiting -- on a fence, a present,
    an io completion -- and then the load is paced by something other than instructions, which no
    amount of making our code cheaper will move. The module split says where the working half went.
    """
    game_base = int(header.get("module_base", 0))
    rows = ["  ms       cpu%   game  ourdll  other   (main-thread rip, ticks per bucket)"]
    main_tid = None
    by_bucket: dict[int, list] = {}
    clusters: Counter = Counter()
    for tick in ticks:
        ms = tick["ms"]
        if ms < lo_ms or ms > hi_ms:
            continue
        threads = tick.get("t", [])
        if not threads:
            continue
        if main_tid is None:
            main_tid = threads[0]["id"]
        main = next((t for t in threads if t["id"] == main_tid), None)
        if main is None:
            continue
        by_bucket.setdefault(ms // width_ms, []).append((ms, main))
        rip = main.get("rip", 0)
        in_game = game_base <= rip < game_base + GAME_IMAGE_SPAN
        in_dll = dll_base <= rip < dll_base + dll_size
        if rip and not in_game and not in_dll:
            # 1 MB buckets: coarse enough that one module's hot code lands together, fine enough
            # that two adjacent modules do not merge into one meaningless total.
            clusters[rip >> 20] += 1
    for bucket in sorted(by_bucket):
        entries = by_bucket[bucket]
        first_ms, first = entries[0]
        last_ms, last = entries[-1]
        jiffies = (last.get("u", 0) + last.get("k", 0)) - (first.get("u", 0) + first.get("k", 0))
        # Windows thread times are 100ns units, so one fully busy core is 10,000,000 per second.
        span_s = max((last_ms - first_ms) / 1000.0, 0.001)
        cpu_pct = jiffies / 1e7 / span_s * 100.0
        rips = [e.get("rip", 0) for _, e in entries]
        in_game = sum(1 for r in rips if game_base <= r < game_base + GAME_IMAGE_SPAN)
        in_dll = sum(1 for r in rips if dll_base <= r < dll_base + dll_size)
        rows.append(
            f"  {bucket * width_ms:<7} {cpu_pct:>5.0f}  {in_game:>5}  {in_dll:>6}  "
            f"{len(entries) - in_game - in_dll:>5}"
        )
    rows.append("")
    rows.append("main-thread rips outside the game image and outside our dll, by 1MB region:")
    for page, count in clusters.most_common(12):
        rows.append(f"  0x{page << 20:012x}  {count:>5} ticks")
    return "\n".join(rows)


if __name__ == "__main__":
    raise SystemExit(main())
