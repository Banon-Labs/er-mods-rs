#!/usr/bin/env python3
"""Did the launch that just started bring a DLL up? Answers at the measured time, not a guess.

A launch writes a fresh me3 log (`~/.local/share/me3/logs/<profile>/<stamp>.log`) and, once the
game process loads our natives, each shell recreates its own log beside the game exe. The gap
between the two births is the launch's real startup time. Measured 2026-10-02 on this machine:
3.8 s and 4.0 s for two good `r3-view` launches, while a launch that never started a game left a
5-line me3 log and no DLL log at all. So the answer is due a little after the longest gap seen,
and waiting minutes for a line that cannot come tells the user nothing they could not see.

Usage: er-launch-check.py <profile-name> <dll-log-name> [--since <epoch seconds>]
  e.g. er-launch-check.py r3-view er-r3-view.log
Exit 0 with the gap when the DLL log was born after the newest me3 log; exit 1 at the deadline,
with the me3 log's tail, when it was not. Every success appends its gap to the history file, and
the deadline is the longest gap on record times `MARGIN` (or `SEED_GAP_S` with no history yet).
"""
import argparse
import ctypes
import os
import select
import sys
import time
from pathlib import Path

ME3_LOGS = Path(os.environ.get('ME3_LOG_ROOT', Path.home() / '.local/share/me3/logs'))
GAME_DIR = Path(os.environ.get(
    'ER_GAME_DIR', Path.home() / '.local/share/Steam/steamapps/common/ELDEN RING/Game'))
HISTORY = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'er-mods-rs' / 'launch-gaps.tsv'
#: The longer of the two gaps measured before this script existed.
SEED_GAP_S = 4.0
MARGIN = 1.5
#: How long to wait for the me3 log itself to appear, from `--since`.
ME3_LOG_GRACE_S = 3.0
IN_CREATE, IN_MOVED_TO, IN_CLOSE_WRITE = 0x100, 0x80, 0x8


class DirEvents:
    """inotify on the directories a launch writes into: `wait(until)` returns when any file there
    is created, renamed in or closed after writing, or when `until` passes, whichever is first."""

    def __init__(self, *dirs: Path):
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.fd = self.libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        for d in dirs:
            if d.is_dir():
                self.libc.inotify_add_watch(self.fd, str(d).encode(), IN_CREATE | IN_MOVED_TO | IN_CLOSE_WRITE)

    def wait(self, until: float) -> None:
        left = until - time.time()
        if left <= 0 or self.fd < 0:
            return
        ready, _, _ = select.select([self.fd], [], [], left)
        if ready:
            try:
                os.read(self.fd, 65536)
            except BlockingIOError:
                pass


def birth(path: Path) -> float | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return getattr(st, 'st_birthtime', None) or st.st_mtime


def longest_gap() -> float:
    try:
        gaps = [float(line.split('\t')[1]) for line in HISTORY.read_text().splitlines() if '\t' in line]
    except (OSError, ValueError):
        gaps = []
    return max(gaps, default=SEED_GAP_S)


def newest_me3_log(profile: str, since: float) -> Path | None:
    logs = sorted((ME3_LOGS / profile).glob('*.log'), key=lambda p: birth(p) or 0)
    if logs and (birth(logs[-1]) or 0) >= since:
        return logs[-1]
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('profile', help='me3 profile name, the directory under ~/.local/share/me3/logs')
    ap.add_argument('dll_log', help="a shell's log beside the game exe, e.g. er-r3-view.log")
    ap.add_argument('--since', type=float, default=None, help='launch start, epoch seconds (default: now - 1)')
    a = ap.parse_args()
    since = a.since if a.since is not None else time.time() - 1.0

    events = DirEvents(ME3_LOGS / a.profile, GAME_DIR)
    deadline = since + ME3_LOG_GRACE_S
    me3 = newest_me3_log(a.profile, since)
    while me3 is None and time.time() < deadline:
        events.wait(deadline)
        me3 = newest_me3_log(a.profile, since)
    if me3 is None:
        print(f'launch-check: FAILED -- no me3 log for {a.profile} appeared within {ME3_LOG_GRACE_S:.0f} s')
        return 1

    start = birth(me3) or since
    budget = longest_gap() * MARGIN
    dll = GAME_DIR / a.dll_log
    while True:
        b = birth(dll)
        if b is not None and b >= start:
            gap = b - start
            HISTORY.parent.mkdir(parents=True, exist_ok=True)
            with HISTORY.open('a') as f:
                f.write(f'{a.profile}\t{gap:.3f}\t{me3.name}\n')
            print(f'launch-check: OK -- {a.dll_log} up {gap:.1f} s after {me3}')
            return 0
        if time.time() >= start + budget:
            break
        events.wait(start + budget)
    tail = me3.read_text(errors='replace').splitlines()[-3:]
    print(f'launch-check: FAILED -- {a.dll_log} not up {budget:.1f} s after {me3} '
          f'(longest good gap on record x{MARGIN}); me3 log ends:')
    for line in tail:
        print('  ' + line[:200])
    return 1


if __name__ == '__main__':
    sys.exit(main())
