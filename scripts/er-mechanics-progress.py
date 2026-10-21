#!/usr/bin/env python3
"""Progress counters for the mechanics scripts a build pipeline runs.

A long loop prints `  i/N <unit>` to stderr, the line `er-mechanics-pipeline-watch.COUNTER`
reads, so the duel build's bar has a counter that moves while the work does. The line comes at
most once per `COUNT_SECONDS` and always at the last item.

    P = <this module, loaded by path>
    for x in P.counted(rows, "rows"):
        ...

    python3 scripts/er-mechanics-progress.py --selftest
"""
from __future__ import annotations

import io
import sys
import time

#: Seconds between two counter lines: one per redraw of the bar that reads them
#: (`er-weapon-build.REDRAW_SECONDS`), so the counter moves on every redraw without filling the log.
COUNT_SECONDS = 2.0


class Count:
    """A counter, `  i/N <unit>` on stderr. Call it once per item; it prints at most once per
    `COUNT_SECONDS`, and always at the last item. `skip(n)` counts `n` items the work turned out
    not to need, so the counter still ends at N/N."""

    def __init__(self, total: int, unit: str, clock=time.monotonic, out=None):
        self.total, self.unit, self.clock, self.out = max(0, total), unit, clock, out
        self.i, self.last = 0, None

    def __call__(self, n: int = 1) -> None:
        self.i = min(self.total, self.i + n)
        now = self.clock()
        if self.i == self.total or self.last is None or now - self.last >= COUNT_SECONDS:
            self.last = now
            print(f"  {self.i}/{self.total} {self.unit}", file=self.out or sys.stderr, flush=True)

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
          ["  1/10 weapons", "  5/10 weapons", "  9/10 weapons", "  10/10 weapons"])
    # `counted` counts an item once the loop is done with it, a `continue` included.
    err, seen = io.StringIO(), []
    for x in counted(iter(range(4)), "rows", clock=lambda: 0.0, out=err):
        if x == 1:
            continue
        seen.append(x)
    check("counted: every item, the counter at N/N", (seen, err.getvalue().splitlines()),
          ([0, 2, 3], ["  1/4 rows", "  4/4 rows"]))
    err = io.StringIO()
    check("counted: nothing to count prints nothing",
          (list(counted([], "rows", out=err)), err.getvalue()), ([], ""))
    print(f"{len(fails)} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv[1:] else print(__doc__) or 0)
