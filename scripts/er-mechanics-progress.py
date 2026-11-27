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
import re
import sys
import time
from pathlib import Path

#: Seconds between two counter lines: one per redraw of the bar that reads them
#: (`er-weapon-build.REDRAW_SECONDS`), so the counter moves on every redraw without filling the log.
COUNT_SECONDS = 2.0


def hms(epoch: float) -> str:
    """`13:39:02`: a wall-clock time as a counter line shows it."""
    return time.strftime("%H:%M:%S", time.localtime(epoch))


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
    first.

    Each line ends with the wall-clock time it was written and the time the rest would end at,
    `[13:39:02, ends ~13:44:50]` (`wall`), so the next line's time shows how far off the last
    estimate was (user, 2026-11-19, of "207/208 attack sets 6h37m ... ~5m48s left" still running
    34 minutes later: "We need timestamps so we can both see when you say 5 mins left, and the
    next line is timestamped with something that isn't 5 minutes").

    `older` is the part of `weight` taken from times measured on older code; each call names its
    item's part (`o`), and while some of it is left the line says how much of the time left rests
    on it, `62% of the estimate from older code's times`: the time left is the weight left at one
    pace, so that share is the older part's share of the weight left.

    `jobs` (how many items run at once) with `sizes` (each item's weight, by its index) makes the
    time left wall-clock rather than the items' seconds end to end (`left`): `start(i)` says item
    `i` started, and each call names the item that ended (`i`) and its own seconds (`s`). Without
    them, the rest of the weight at the pace so far: the first line of 2026-11-21's attack sets,
    whose first set ran alone, put 3 workers' work end to end."""

    def __init__(self, total: int, unit: str, clock=time.monotonic, out=None, weight=None,
                 wall=time.time, older=None, jobs=None, sizes=None):
        self.total, self.unit, self.clock, self.out, self.wall = max(0, total), unit, clock, out, wall
        self.weight = weight if weight and weight > 0 else None
        self.older = older if self.weight is not None and older and older > 0 else None
        self.i, self.done, self.odone, self.last = 0, 0.0, 0.0, None
        # The wall-clock model: the items' sizes, the ones not yet ended, the start of each in
        # flight, and the seconds and the weight of the items that ended with their seconds given.
        self.sizes = list(sizes) if jobs and sizes is not None and len(sizes) == self.total else None
        self.jobs = max(1, int(jobs)) if self.sizes is not None else None
        self.open = set(range(self.total)) if self.sizes is not None else set()
        self.flight, self.spent, self.timed = {}, 0.0, 0.0
        self.start = clock()

    def started(self, i: int) -> None:
        """Item `i` started now (again, when it is dealt again)."""
        if self.sizes is not None and i in self.open:
            self.flight[i] = self.clock()

    def __call__(self, n: int = 1, w: float = 0.0, o: float = 0.0, i: int | None = None,
                 s: float | None = None) -> None:
        self.i = min(self.total, self.i + n)
        self.done += w
        self.odone += o
        if i is not None:
            self.open.discard(i)
            self.flight.pop(i, None)
            if s is not None and self.sizes is not None:
                self.spent, self.timed = self.spent + max(0.0, s), self.timed + self.sizes[i]
        now = self.clock()
        if self.i == self.total or self.last is None or now - self.last >= COUNT_SECONDS:
            self.last = now
            at, left = self.wall(), self.left(now)
            ends = "" if left is None else f", ends ~{hms(at + left)}"
            print(f"  {self.i}/{self.total} {self.unit}  {self.pace(now)}  [{hms(at)}{ends}]",
                  file=self.out or sys.stderr, flush=True)

    def left(self, now: float) -> float | None:
        """Seconds the rest takes at the pace so far, or None at the last item or before any of
        the estimated cost is done: with a `weight`, the rest of that cost at the cost done per
        second, else the items left at the seconds per item. Items still running count for nothing
        until they end, so early on, while the dearest run, it is an upper bound; at the end, while
        the last ones run past their estimate, it is a lower bound.

        With `jobs` and `sizes`, wall-clock instead: the items' own seconds per unit of weight so
        far (`s` over the sizes of the items that ended, so it holds however many ran at once) puts
        each item's seconds left at its size times that, less what it has run if it is in flight
        (none once it is past it); the rest takes those seconds over `jobs` workers, but no less
        than the longest of them. None before an item has ended with its seconds."""
        if self.i >= self.total:
            return None
        if self.sizes is not None:
            if self.timed <= 0:
                return None
            per = self.spent / self.timed
            rest = [max(0.0, per * self.sizes[k] - (now - self.flight[k] if k in self.flight else 0.0))
                    for k in self.open]
            return max(sum(rest) / self.jobs, max(rest, default=0.0))
        t = now - self.start
        if self.weight is None:
            return (t / self.i if self.i else 0.0) * (self.total - self.i)
        share = min(1.0, self.done / self.weight)
        return t * (1 - share) / share if share > 0 else None

    def pace(self, now: float) -> str:
        """The time since the counter was made, the wall seconds per item so far (items that run
        side by side share it) and, before the last item, the time the rest takes at that pace
        (`left`), after the share of the estimated cost done when there is a `weight`."""
        t = now - self.start
        per = t / self.i if self.i else 0.0
        each = f"{per:.0f}" if per >= 100 else f"{per:.3g}" if per >= 0.001 or per == 0 else "<0.001"
        out = f"{span(t)}, {each} s/item"
        if self.i < self.total and self.weight is not None:
            out += f", {100 * min(1.0, self.done / self.weight):.1f}% of the estimated work"
        left = self.left(now)
        if left is not None:
            out += f", ~{span(left)} left"
            if self.older is not None and self.weight > self.done and self.older > self.odone:
                share = min(1.0, (self.older - self.odone) / (self.weight - self.done))
                out += f", {100 * share:.0f}% of the estimate from older code's times"
        return out

    def running(self, label: str, ran: float, expected: float | None = None,
                source: str = "measurement", older: tuple[float, float] | None = None) -> None:
        """One line, on the counter's stream, for an item that has run `ran` seconds and not
        ended: `  still running: <label>  <ran> ...  [<wall>]`. With `expected` (its seconds by
        `source`) it says how much of that has run and when the rest would end, or that it has run
        past it; with none, that there is no `source`. A counter line counts an item only when it
        ends, so without this a set that runs for hours leaves the last counter line, and its time
        left, standing: 2026-11-19's "207/208 attack sets ... ~5m48s left" at 13:04, its last set
        then running since 10:44 and still running at 13:45 with nothing printed. The head is no
        `i/N unit`, so `er-mechanics-pipeline-watch.COUNTER` never reads it as a counter.

        With no `expected` but `older` (its seconds on older code, and those scaled to this code),
        it says what older code took, so a stall can be judged: `older code took 2h43m (scaled
        4h31m)`, then when the scaled time ends or that it has run past it."""
        at = self.wall()
        if expected is None and older is not None:
            raw, scaled = older
            what = f", older code took {span(raw)} (scaled {span(scaled)})"
            ends = f", ends ~{hms(at + scaled - ran)}" if ran < scaled else ""
            what += "" if ran < scaled else ", past it"
        elif expected is None:
            what, ends = f", no {source}", ""
        elif ran < expected:
            what, ends = f" of ~{span(expected)} expected ({source})", f", ends ~{hms(at + expected - ran)}"
        else:
            what, ends = f", past the ~{span(expected)} expected ({source})", ""
        print(f"  still running: {label}  {span(ran)}{what}  [{hms(at)}{ends}]",
              file=self.out or sys.stderr, flush=True)

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

    def bare(buf):
        """The lines without their wall-clock `  [...]`, which the checks of the pace leave to
        the one check that names a wall clock."""
        return [re.sub(r"  \[[^]]*\]$", "", x) for x in buf.getvalue().splitlines()]

    # The first item and the last always print, the rest at most once per `COUNT_SECONDS`.
    clk = [0.0]
    err = io.StringIO()
    cnt = Count(10, "weapons", clock=lambda: clk[0], out=err)
    for i in range(9):
        clk[0] = i * 0.5
        cnt()
    cnt.skip(1)
    check("Count: first, one per COUNT_SECONDS, last", bare(err),
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
    check("Count: elapsed, s/item and the time left; none at N/N", bare(err),
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
          (bare(err), zero.pace(10.0)),
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
          (bare(err), clk_.pace(250.0)),
          (["  1/2 toys  0s, <0.001 s/item, ~0s left"], "4m10s, 250 s/item, ~4m10s left"))
    # The wall clock: when each line was written and when the rest would end, so a later line's
    # time shows how far off the estimate was. The last line has no end left to estimate.
    clk, wal = [0.0], [36000.0]
    err = io.StringIO()
    cnt = Count(3, "sets", clock=lambda: clk[0], out=err, weight=10.0, wall=lambda: wal[0])
    clk[0], wal[0] = 600.0, 36600.0
    cnt(w=8.0)
    clk[0], wal[0] = 2400.0, 38400.0
    cnt(w=1.0)
    clk[0], wal[0] = 3000.0, 39000.0
    cnt(w=1.0)
    check("Count: each line's wall time and the time the rest ends at; none at N/N",
          err.getvalue().splitlines(),
          [f"  1/3 sets  10m00s, 600 s/item, 80.0% of the estimated work, ~2m30s left  "
           f"[{hms(36600)}, ends ~{hms(36750)}]",
           f"  2/3 sets  40m00s, 1200 s/item, 90.0% of the estimated work, ~4m27s left  "
           f"[{hms(38400)}, ends ~{hms(38666.67)}]",
           f"  3/3 sets  50m00s, 1000 s/item  [{hms(39000)}]"])
    # With `older`, the share of the weight left that older code's times make up; none once that
    # part is done, and none at N/N.
    err = io.StringIO()
    clk = [0.0]
    cnt = Count(3, "sets", clock=lambda: clk[0], out=err, weight=100.0, older=80.0)
    clk[0] = 10.0
    cnt(w=10.0)
    clk[0] = 90.0
    cnt(w=80.0, o=80.0)
    clk[0] = 100.0
    cnt(w=10.0)
    check("Count(older=): the share of the estimate left from older code's times, none once it is done",
          bare(err),
          ["  1/3 sets  10s, 10 s/item, 10.0% of the estimated work, ~1m30s left, "
           "89% of the estimate from older code's times",
           "  2/3 sets  1m30s, 45 s/item, 90.0% of the estimated work, ~10s left",
           "  3/3 sets  1m40s, 33.3 s/item"])
    # With `jobs` and `sizes` the time left is wall-clock: each item's seconds left at the items'
    # own seconds per weight, an item in flight less what it has run, over the workers, and no less
    # than the longest. Item 0 runs alone and ends at 10 s in its 10 s (1 s per unit); items 1 and 2
    # start then on 2 workers. At 40 s: 70 + 20 + 40 unstarted over 2 is 65, the longest 70. At 60 s
    # item 2 ends in its 50 s: 50 + 40 over 2 is 45, the longest 50. Item 1 past its 100 s leaves
    # it nothing, so the unstarted 40 binds. None before an item has ended with its seconds. The
    # first line, at 10 s, has nothing in flight yet: 100 + 50 + 40 over 2 is 95, the longest 100.
    clk = [0.0]
    err = io.StringIO()
    cnt = Count(4, "sets", clock=lambda: clk[0], out=err, weight=200.0, jobs=2, sizes=[10.0, 100.0, 50.0, 40.0])
    cnt.started(0)
    early = cnt.left(5.0)
    clk[0] = 10.0
    cnt(w=10.0, i=0, s=10.0)
    cnt.started(1)
    cnt.started(2)
    at40 = cnt.left(40.0)
    clk[0] = 60.0
    cnt(w=50.0, i=2, s=50.0)
    late = cnt.left(200.0)
    check("Count(jobs=, sizes=): wall-clock time left over the workers, no less than the longest item",
          (early, at40, bare(err), late),
          (None, 70.0, ["  1/4 sets  10s, 10 s/item, 5.0% of the estimated work, ~1m40s left",
                        "  2/4 sets  1m00s, 30 s/item, 30.0% of the estimated work, ~50s left"], 40.0))
    check("Count: without jobs and sizes, the weight left at the pace so far",
          Count(2, "sets", clock=lambda: 0.0, weight=10.0, jobs=2).sizes, None)
    check("hms: a wall-clock time", hms(time.mktime((2026, 11, 19, 13, 39, 2, 0, 0, -1))), "13:39:02")
    check("span: seconds, minutes, hours", [span(x) for x in (0, 59.4, 61, 3599, 3600, 7322)],
          ["0s", "59s", "1m01s", "59m59s", "1h00m", "2h02m"])
    # The pipeline watcher still reads the counter at the head of the line.
    watch = importlib.util.spec_from_file_location(
        "er_mechanics_pipeline_watch", Path(__file__).with_name("er-mechanics-pipeline-watch.py"))
    pw = importlib.util.module_from_spec(watch)
    watch.loader.exec_module(pw)
    m = pw.COUNTER.match("  3/200 attack sets  1m30s, 30 s/item, ~1h38m left  [13:39:02, ends ~15:17:00]")
    check("COUNTER reads a paced line", m and m.groups(), ("3", "200", "attack"))
    # An item still running: how long, against its expected seconds when there are some, with the
    # wall clock; the counter itself is untouched, and the watcher never takes the line for one.
    err = io.StringIO()
    cnt = Count(208, "attack sets", clock=lambda: 0.0, out=err, wall=lambda: 36000.0)
    cnt.running("Founding Rain of Stars and 3 more", 1800.0, 7200.0, "same-code measurement")
    cnt.running("Founding Rain of Stars and 3 more", 10860.0, 7200.0, "same-code measurement")
    cnt.running("Flame Fall Upon Them", 10860.0, None, "same-code measurement")
    cnt.running("x", 5.0)
    run_lines = err.getvalue().splitlines()
    check("Count.running: the time run, against the expected seconds when known, with the wall clock",
          run_lines,
          [f"  still running: Founding Rain of Stars and 3 more  30m00s of ~2h00m expected "
           f"(same-code measurement)  [{hms(36000)}, ends ~{hms(41400)}]",
           f"  still running: Founding Rain of Stars and 3 more  3h01m, past the ~2h00m expected "
           f"(same-code measurement)  [{hms(36000)}]",
           f"  still running: Flame Fall Upon Them  3h01m, no same-code measurement  [{hms(36000)}]",
           f"  still running: x  5s, no measurement  [{hms(36000)}]"])
    check("Count.running: no counter line, and the counter where it was",
          ([bool(pw.COUNTER.match(x)) for x in run_lines], cnt.i, cnt.last), ([False] * 4, 0, None))
    # With no same-code seconds but older code's, what older code took and that scaled to this
    # code, then when the scaled time ends or that the set has run past it; same-code seconds,
    # when there are some, keep their line.
    err = io.StringIO()
    cnt = Count(59, "attack sets", clock=lambda: 0.0, out=err, wall=lambda: 36000.0)
    cnt.running("Cannon of Haima and 1 more", 4500.0, None, "same-code measurement", older=(9806.8, 16239.1))
    cnt.running("Cannon of Haima and 1 more", 17000.0, None, "same-code measurement", older=(9806.8, 16239.1))
    cnt.running("x", 60.0, 120.0, "same-code measurement", older=(10.0, 20.0))
    check("Count.running(older=): what older code took, scaled, when no same-code seconds",
          err.getvalue().splitlines(),
          [f"  still running: Cannon of Haima and 1 more  1h15m, older code took 2h43m (scaled 4h30m)  "
           f"[{hms(36000)}, ends ~{hms(36000 + 16239.1 - 4500)}]",
           f"  still running: Cannon of Haima and 1 more  4h43m, older code took 2h43m (scaled 4h30m), past it  "
           f"[{hms(36000)}]",
           f"  still running: x  1m00s of ~2m00s expected (same-code measurement)  [{hms(36000)}, ends ~{hms(36060)}]"])
    # `counted` counts an item once the loop is done with it, a `continue` included.
    err, seen = io.StringIO(), []
    for x in counted(iter(range(4)), "rows", clock=lambda: 0.0, out=err):
        if x == 1:
            continue
        seen.append(x)
    check("counted: every item, the counter at N/N", (seen, bare(err)),
          ([0, 2, 3], ["  1/4 rows  0s, 0 s/item, ~0s left", "  4/4 rows  0s, 0 s/item"]))
    err = io.StringIO()
    check("counted: nothing to count prints nothing",
          (list(counted([], "rows", out=err)), err.getvalue()), ([], ""))
    print(f"{len(fails)} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv[1:] else print(__doc__) or 0)
