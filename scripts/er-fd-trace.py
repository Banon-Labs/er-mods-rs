#!/usr/bin/env python3
"""Name the files a running process is reading and writing, and how hard.

`/proc/<pid>/io` says a process issued 260,000 write syscalls that moved almost no bytes, which is
the signature of many small writes rather than of disk pressure -- but it does not say to which
file, and that is the only part that names a fix. This walks `/proc/<pid>/fd` and `fdinfo` on a
short interval and reports, per path:

    samples     how many ticks the descriptor existed for
    advanced    ticks where its offset moved, so a file written steadily stands out from one
                written once
    bytes       total offset advance across the run, which for an append-only log is what was
                written to it
    opens       distinct descriptor numbers seen for that path, so a file being opened and closed
                repeatedly is visible as itself rather than as one busy handle

The offset is a proxy, not a syscall count: three `write` calls inside one `writeln!` advance it
once. It still separates "this file is hot" from "this file is not", which is the question, and it
needs no ptrace, no LD_PRELOAD, and nothing injected into a game running under Proton.

Usage:
  python3 scripts/er-fd-trace.py --pid <pid> [--seconds 30] [--interval 0.1]
  python3 scripts/er-fd-trace.py --wait-for-game [--seconds 30]
  python3 scripts/er-fd-trace.py --selftest
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import er_run_lib  # noqa: E402

DEFAULT_INTERVAL_SECONDS = 0.1
DEFAULT_SECONDS = 30.0


def wait_tick_or_exit(pid: int, interval: float) -> bool:
    """Wait one sample period, returning early -- and `True` -- if `pid` exits during it.

    The period is the instrument here: offsets and counters change without any notification, so
    there is nothing to wait on for "the next write". What can be waited on is the one event that
    ends the trace, the process exiting, and a pidfd delivers that edge-triggered. So the gap
    between samples is a bounded wait on the target's pidfd rather than a blind sleep, and a
    process that dies mid-period ends the trace at once instead of one tick later.
    """
    return er_run_lib.wait_for_exit(pid, interval)


def fd_offsets(pid: int) -> dict[int, tuple[str, int]]:
    """`{fd: (target path, offset)}` for every regular-file descriptor the process holds."""
    out: dict[int, tuple[str, int]] = {}
    fd_dir = Path(f"/proc/{pid}/fd")
    try:
        entries = os.listdir(fd_dir)
    except OSError:
        return out
    for entry in entries:
        try:
            fd = int(entry)
        except ValueError:
            continue
        try:
            target = os.readlink(fd_dir / entry)
        except OSError:
            continue
        if not target.startswith("/") or target.startswith(("/dev/", "/proc/", "/sys/")):
            continue
        try:
            raw = Path(f"/proc/{pid}/fdinfo/{entry}").read_text(encoding="utf-8")
        except OSError:
            continue
        offset = 0
        for line in raw.splitlines():
            if line.startswith("pos:"):
                try:
                    offset = int(line.split()[1])
                except (IndexError, ValueError):
                    offset = 0
                break
        out[fd] = (target, offset)
    return out


def trace(pid: int, seconds: float, interval: float) -> list[tuple]:
    """Sample until the window closes or the process goes away; return rows sorted by advance."""
    advanced: defaultdict[str, int] = defaultdict(int)
    moved_bytes: defaultdict[str, int] = defaultdict(int)
    samples: defaultdict[str, int] = defaultdict(int)
    opens: defaultdict[str, set] = defaultdict(set)
    previous: dict[int, tuple[str, int]] = {}
    deadline = time.time() + seconds
    ticks = 0
    while time.time() < deadline:
        current = fd_offsets(pid)
        if not current and not Path(f"/proc/{pid}").exists():
            break
        ticks += 1
        for fd, (path, offset) in current.items():
            samples[path] += 1
            opens[path].add(fd)
            was = previous.get(fd)
            if was is not None and was[0] == path and offset > was[1]:
                advanced[path] += 1
                moved_bytes[path] += offset - was[1]
        previous = current
        if wait_tick_or_exit(pid, interval):
            break
    rows = [
        (path, samples[path], advanced[path], moved_bytes[path], len(opens[path]))
        for path in samples
    ]
    rows.sort(key=lambda row: (-row[2], -row[3]))
    return rows


def thread_io(pid: int) -> dict[int, tuple[str, int, int]]:
    """`{tid: (comm, syscr, syscw)}` for every thread of the process.

    `/proc/<pid>/io` is a process total, which is enough to say a syscall storm exists and useless
    for saying whose it is. The per-thread files split it, and this process names its own threads
    (`er-quickload-*`), so the answer to "is this ours" is a string rather than an inference.
    """
    out: dict[int, tuple[str, int, int]] = {}
    task_dir = Path(f"/proc/{pid}/task")
    try:
        entries = os.listdir(task_dir)
    except OSError:
        return out
    for entry in entries:
        try:
            tid = int(entry)
        except ValueError:
            continue
        try:
            comm = (task_dir / entry / "comm").read_text(encoding="utf-8").strip()
            raw = (task_dir / entry / "io").read_text(encoding="utf-8")
        except OSError:
            continue
        counts = {}
        for line in raw.splitlines():
            name, _, value = line.partition(":")
            try:
                counts[name.strip()] = int(value)
            except ValueError:
                continue
        out[tid] = (comm, counts.get("syscr", 0), counts.get("syscw", 0))
    return out


def trace_threads(pid: int, seconds: float, interval: float) -> list[tuple]:
    """Sample per-thread io counters across the window; return `(tid, comm, dr, dw)` rows."""
    first: dict[int, tuple[str, int, int]] = {}
    last: dict[int, tuple[str, int, int]] = {}
    deadline = time.time() + seconds
    while time.time() < deadline:
        current = thread_io(pid)
        if not current and not Path(f"/proc/{pid}").exists():
            break
        for tid, value in current.items():
            first.setdefault(tid, value)
            last[tid] = value
        if wait_tick_or_exit(pid, interval):
            break
    rows = []
    for tid, (comm, reads, writes) in last.items():
        _, first_reads, first_writes = first.get(tid, (comm, reads, writes))
        rows.append((tid, comm, reads - first_reads, writes - first_writes))
    rows.sort(key=lambda row: -(row[2] + row[3]))
    return rows


def report_threads(rows: list[tuple], limit: int = 20) -> str:
    lines = [f"{'tid':>8} {'read':>10} {'write':>10}  comm"]
    total_r = sum(row[2] for row in rows)
    total_w = sum(row[3] for row in rows)
    for tid, comm, reads, writes in rows[:limit]:
        lines.append(f"{tid:>8} {reads:>10,} {writes:>10,}  {comm}")
    lines.append(f"{'total':>8} {total_r:>10,} {total_w:>10,}  ({len(rows)} threads)")
    return "\n".join(lines)


def report(rows: list[tuple], limit: int = 20) -> str:
    lines = [f"{'advanced':>9} {'bytes':>12} {'opens':>6} {'samples':>8}  path"]
    for path, samples, advanced, moved, opens in rows[:limit]:
        lines.append(f"{advanced:>9} {moved:>12,} {opens:>6} {samples:>8}  {path}")
    if not rows:
        lines.append("  (no regular-file descriptors seen)")
    return "\n".join(lines)


def selftest() -> int:
    """Trace this very process while it writes a file in a way the tracer must notice."""
    failures = 0
    checks: list[tuple[str, bool]] = []
    scratch = Path(os.environ.get("TMPDIR", "/tmp")) / f"er-fd-trace-selftest-{os.getpid()}.log"
    handle = scratch.open("w", encoding="utf-8")
    before = fd_offsets(os.getpid())
    checks.append(
        ("the open scratch file appears in this process's fd table", any(
            target == str(scratch) for target, _ in before.values()
        ))
    )
    handle.write("x" * 4096)
    handle.flush()
    after = fd_offsets(os.getpid())
    grew = any(
        target == str(scratch) and offset >= 4096 for target, offset in after.values()
    )
    checks.append(("a write advances the recorded offset", grew))
    handle.close()
    scratch.unlink(missing_ok=True)
    checks.append(
        ("a dead pid yields no descriptors rather than raising", fd_offsets(999999) == {})
    )
    checks.append(
        ("the tick wait on a live process runs out without reporting an exit",
         wait_tick_or_exit(os.getpid(), 0.01) is False)
    )
    checks.append(
        ("the tick wait on a dead pid reports the exit", wait_tick_or_exit(999999, 0.01) is True)
    )
    rows = trace(999999, 5.0, 0.01)
    checks.append(("tracing a dead pid ends at once with no rows", rows == []))
    for label, ok in checks:
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
        failures += 0 if ok else 1
    print(f"\n{'selftest failed' if failures else 'selftest passed'}: {failures} failing check(s)")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--pid", type=int)
    parser.add_argument(
        "--wait-for-game",
        action="store_true",
        help="poll for eldenring.exe instead of taking a pid",
    )
    parser.add_argument("--seconds", type=float, default=DEFAULT_SECONDS)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--threads",
        action="store_true",
        help="report per-thread read/write syscall counts instead of per-file offsets",
    )
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    pid = args.pid
    if pid is None and args.wait_for_game:
        # There is no unprivileged notification for a process appearing (the proc connector
        # needs CAP_NET_ADMIN, and inotify does not work on /proc), so this re-reads /proc once
        # per interval. The wait between reads is a bounded timer wait on an event nothing sets.
        tick = threading.Event()
        deadline = time.monotonic() + args.seconds
        while pid is None and time.monotonic() < deadline:
            pid = er_run_lib.game_pid()
            if pid is None:
                tick.wait(args.interval)
    if pid is None:
        parser.error("one of --pid or --wait-for-game is required, and no game was found")

    print(f"tracing pid {pid} for {args.seconds}s at {args.interval}s")
    if args.threads:
        print(report_threads(trace_threads(pid, args.seconds, args.interval), args.limit))
    else:
        print(report(trace(pid, args.seconds, args.interval), args.limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
