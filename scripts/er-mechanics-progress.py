#!/usr/bin/env python3
"""Progress counters for the mechanics scripts a build pipeline runs.

A long loop prints `  i/N <unit>  <elapsed>, <s>/item, ~<left> left` to stderr. The
`er-mechanics-pipeline-watch.COUNTER` match reads its head, so the duel build's bar has a counter
that moves while the work does; the tail is for whoever watches the log in a tail tab, who sees
how long the counter has run, its pace so far and when it will end at that pace (user directive
2026-11-11: "so I could see progression and we actually knew how long stages took"). The line
comes at most once per `COUNT_SECONDS` and always at the last item, which drops the estimate.

    P = <this module, loaded by path>
    for x in P.counted(rows, "rows"):
        ...

    python3 scripts/er-mechanics-progress.py --selftest
"""
from __future__ import annotations

import importlib.util
import io
import sys
import time
from pathlib import Path

#: Seconds between two counter lines: one per redraw of the bar that reads them
#: (`er-weapon-build.REDRAW_SECONDS`), so the counter moves on every redraw without filling the log.
COUNT_SECONDS = 2.0


def span(seconds: float) -> str:
    """`45s`, `3m05s` or `2h07m`: a duration as a counter line shows it."""
    s = max(0, round(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{s % 3600 // 60:02d}m"


class Count:
    """A counter, `  i/N <unit>  <pace>` on stderr (`pace`). Call it once per item; it prints at
    most once per `COUNT_SECONDS`, and always at the last item. `skip(n)` counts `n` items the
    work turned out not to need, so the counter still ends at N/N.

    `weight` is the whole run's estimated cost when items differ in cost; each call then names its
    item's share (`w`) and the time left is the rest of that cost at the pace so far. That is only
    as good as the weight's fit to time: r11 of attack similarity read "~212h12m left" after 2 of
    944 sets counted by items, and its geometric cost estimate was off by 8x from set to set
    (55 s against 433 s per 1% of it, 2026-11-11), so a weight is fitted from measured seconds
    first."""

    def __init__(self, total: int, unit: str, clock=time.monotonic, out=None, weight=None):
        self.total, self.unit, self.clock, self.out = max(0, total), unit, clock, out
        self.weight = weight if weight and weight > 0 else None
        self.i, self.done, self.last = 0, 0.0, None
        self.start = clock()

    def __call__(self, n: int = 1, w: float = 0.0) -> None:
        self.i = min(self.total, self.i + n)
        self.done += w
        now = self.clock()
        if self.i == self.total or self.last is None or now - self.last >= COUNT_SECONDS:
            self.last = now
            print(f"  {self.i}/{self.total} {self.unit}  {self.pace(now)}",
                  file=self.out or sys.stderr, flush=True)

    def pace(self, now: float) -> str:
        """The time since the counter was made, the wall seconds per item so far (items that run
        side by side share it) and, before the last item, the time the rest takes at that pace:
        with a `weight`, the share of the estimated cost done and the rest of it at the cost done
        per second. Items still running count for nothing until they end, so early on, while the
        dearest run, the time left is still an upper bound."""
        t = now - self.start
        per = t / self.i if self.i else 0.0
        each = f"{per:.0f}" if per >= 100 else f"{per:.3g}" if per >= 0.001 or per == 0 else "<0.001"
        out = f"{span(t)}, {each} s/item"
        if self.i < self.total:
            if self.weight is None:
                out += f", ~{span(per * (self.total - self.i))} left"
            else:
                share = min(1.0, self.done / self.weight)
                out += f", {100 * share:.1f}% of the estimated work"
                if share > 0:
                    out += f", ~{span(t * (1 - share) / share)} left"
        return out

    def skip(self, n: int) -> None:
        if n > 0:
            self(n)


def counted(items, unit: str, clock=time.monotonic, out=None):
    """`items` again, one at a time, counting each one done (`Count`) once the loop asks for the
    next: `for x in counted(rows, "rows"):` puts a loop on the bar. A loop that breaks out early
    stops its counter where it left."""
    items = items if hasattr(items, "__len__") else list(items)
    tick = Count(len(items), unit, clock, out)
    for x in items:
        yield x
        tick()


def selftest() -> int:
    fails = []

    def check(label, got, want):
        print(f"{'ok' if got == want else 'FAIL'}  {label}: {got!r}")
        if got != want:
            fails.append(label)

    # The first item and the last always print, the rest at most once per `COUNT_SECONDS`.
    clk = [0.0]
    err = io.StringIO()
    cnt = Count(10, "weapons", clock=lambda: clk[0], out=err)
    for i in range(9):
        clk[0] = i * 0.5
        cnt()
    cnt.skip(1)
    check("Count: first, one per COUNT_SECONDS, last", err.getvalue().splitlines(),
          ["  1/10 weapons  0s, 0 s/item, ~0s left", "  5/10 weapons  2s, 0.4 s/item, ~2s left",
           "  9/10 weapons  4s, 0.444 s/item, ~0s left", "  10/10 weapons  4s, 0.4 s/item"])
    # The pace a tail tab shows: elapsed, seconds per item, and the rest at that pace.
    clk = [0.0]
    err = io.StringIO()
    cnt = Count(200, "attack sets", clock=lambda: clk[0], out=err)
    for i in range(3):
        clk[0] = 30.0 * (i + 1)
        cnt()
    clk[0] = 30.0 * 200 + 600
    cnt.skip(197)
    check("Count: elapsed, s/item and the time left; none at N/N", err.getvalue().splitlines(),
          ["  1/200 attack sets  30s, 30 s/item, ~1h39m left",
           "  2/200 attack sets  1m00s, 30 s/item, ~1h39m left",
           "  3/200 attack sets  1m30s, 30 s/item, ~1h38m left",
           "  200/200 attack sets  1h50m, 33 s/item"])
    # With a weight the time left is the rest of the estimated cost at the cost done per second:
    # r11's second set, 27m02s in, at 7.6% of the work, shows 5h28m where the count read 212h.
    clk = [0.0]
    err = io.StringIO()
    cnt = Count(944, "attack sets", clock=lambda: clk[0], out=err, weight=1000.0)
    clk[0] = 955.0
    cnt(w=43.0)
    clk[0] = 1622.0
    cnt(w=33.0)
    cnt.skip(941)
    clk[0] = 5000.0
    cnt(w=924.0)
    zero = Count(3, "sets", clock=lambda: 10.0, out=io.StringIO(), weight=5.0)
    zero.start = 0.0
    check("Count(weight=): the share of the estimated work and the rest of it; none before any",
          (err.getvalue().splitlines(), zero.pace(10.0)),
          (["  1/944 attack sets  15m55s, 955 s/item, 4.3% of the estimated work, ~5h54m left",
            "  2/944 attack sets  27m02s, 811 s/item, 7.6% of the estimated work, ~5h28m left",
            "  944/944 attack sets  1h23m, 5.3 s/item"],
           "10s, 0 s/item, 0.0% of the estimated work"))
    err = io.StringIO()
    cnt = Count(2, "toys", clock=lambda: 2e-5, out=err)
    cnt.start = 0.0
    cnt()
    clk_ = Count(2, "sets", clock=lambda: 250.0, out=io.StringIO())
    clk_.start, clk_.i = 0.0, 1
    check("Count: no exponent at either end of the pace",
          (err.getvalue().splitlines(), clk_.pace(250.0)),
          (["  1/2 toys  0s, <0.001 s/item, ~0s left"], "4m10s, 250 s/item, ~4m10s left"))
    check("span: seconds, minutes, hours", [span(x) for x in (0, 59.4, 61, 3599, 3600, 7322)],
          ["0s", "59s", "1m01s", "59m59s", "1h00m", "2h02m"])
    # The pipeline watcher still reads the counter at the head of the line.
    watch = importlib.util.spec_from_file_location(
        "er_mechanics_pipeline_watch", Path(__file__).with_name("er-mechanics-pipeline-watch.py"))
    pw = importlib.util.module_from_spec(watch)
    watch.loader.exec_module(pw)
    m = pw.COUNTER.match("  3/200 attack sets  1m30s, 30 s/item, ~1h38m left")
    check("COUNTER reads a paced line", m and m.groups(), ("3", "200", "attack"))
    # `counted` counts an item once the loop is done with it, a `continue` included.
    err, seen = io.StringIO(), []
    for x in counted(iter(range(4)), "rows", clock=lambda: 0.0, out=err):
        if x == 1:
            continue
        seen.append(x)
    check("counted: every item, the counter at N/N", (seen, err.getvalue().splitlines()),
          ([0, 2, 3], ["  1/4 rows  0s, 0 s/item, ~0s left", "  4/4 rows  0s, 0 s/item"]))
    err = io.StringIO()
    check("counted: nothing to count prints nothing",
          (list(counted([], "rows", out=err)), err.getvalue()), ([], ""))
    print(f"{len(fails)} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv[1:] else print(__doc__) or 0)
