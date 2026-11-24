#!/usr/bin/env python3
"""One pooled run at a time: an exclusive `fcntl.flock` on a lock file every checkout shares.

    python3 scripts/er-mechanics-pool-lock.py --status     # who holds it, if anyone
    python3 scripts/er-mechanics-pool-lock.py --selftest

`er-mechanics-attack-similarity.py`'s pooled compute (`--jobs` above 1) and
`er-mechanics-interrupt.py --build-cache` (`--jobs` above 1) each hold it while their workers run,
so two pooled runs never share the cores, whatever worktree each runs from. On 2026-11-16 a wait
loop (`while [ -e /proc/PID ] && grep -q r21 /proc/PID/cmdline`) let a pooled `--jobs` run start
while another was going; a lock the kernel holds for the running process cannot be fooled that
way, and it is let go when the process ends, however it ends. A second run waits for it and says
who holds it (pid, working directory, what it runs); with `--no-wait` it exits nonzero instead.
Selftest-only and single-core paths do not take it.

The lock file is `~/.cache/er-build-planner/pooled-compute.lock` (`ER_POOL_LOCK` overrides it);
the holder writes one JSON line into it ({pid, cwd, what, started}) once it holds it."""
import argparse
import contextlib
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def lock_path():
    """The shared lock file: `ER_POOL_LOCK`, else ~/.cache/er-build-planner/pooled-compute.lock."""
    env = os.environ.get('ER_POOL_LOCK')
    return Path(env) if env else Path.home() / '.cache' / 'er-build-planner' / 'pooled-compute.lock'


class Busy(SystemExit):
    """Raised by `held(wait=False)` when another process holds the lock: exit status 3."""

    def __init__(self, holder):
        self.holder = holder
        super().__init__(3)


def holder(path=None):
    """What the lock file says of its holder ({pid, cwd, what, started}), None when it says nothing
    readable. Only meaningful while someone holds the lock (`status`)."""
    path = Path(path) if path else lock_path()
    try:
        text = path.read_text().strip()
        return json.loads(text) if text else None
    except (OSError, ValueError):
        return None


def describe(h):
    if not h:
        return 'another process (it wrote no holder line)'
    return f"pid {h.get('pid')} in {h.get('cwd')} ({h.get('what')}, since {h.get('started')})"


def status(path=None):
    """(held, holder): whether some process holds the lock now, read by trying it without waiting."""
    path = Path(path) if path else lock_path()
    if not path.exists():
        return False, None
    fd = os.open(path, os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True, holder(path)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False, None
    finally:
        os.close(fd)


@contextlib.contextmanager
def held(what, wait=True, log=None, path=None):
    """Hold the pooled-compute lock while the block runs. `what` names the run in the holder line.
    Held by another process: with `wait`, say who holds it (to `log`, default stderr) and wait for
    it; without, say so and raise `Busy` (exit status 3)."""
    path = Path(path) if path else lock_path()
    say = log or (lambda msg: print(msg, file=sys.stderr, flush=True))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            h = holder(path)
            if not wait:
                say(f'pooled-compute lock {path} is held by {describe(h)}; --no-wait, not starting')
                raise Busy(h)
            say(f'pooled-compute lock {path} is held by {describe(h)}; waiting for it')
            t0 = time.time()
            fcntl.flock(fd, fcntl.LOCK_EX)
            say(f'pooled-compute lock {path} taken after {time.time() - t0:.0f} s of waiting')
        line = json.dumps({'pid': os.getpid(), 'cwd': os.getcwd(), 'what': what,
                           'started': time.strftime('%Y-%m-%dT%H:%M:%S')}) + '\n'
        os.ftruncate(fd, 0)
        os.pwrite(fd, line.encode(), 0)
        try:
            yield
        finally:
            os.ftruncate(fd, 0)
    finally:
        os.close(fd)


def _child(path, what, hold_s, wait):
    """`held` in this process for `hold_s` seconds (the selftest's other run)."""
    try:
        with held(what, wait=wait, path=path):
            print('HELD', flush=True)
            time.sleep(hold_s)
    except Busy:
        print('BUSY', flush=True)
        return 3
    return 0


def exclusive_check(take):
    """(ok, detail): with another process holding a temporary lock file (`ER_POOL_LOCK` pointed at it
    for the call), `take(wait)` (a caller's way of taking the pool lock, e.g. a pooled run's
    `pool_held`) raises `Busy` naming that process's pid when `wait` is False. The check the
    callers' selftests make of their own lock path."""
    me = [sys.executable, str(Path(__file__).resolve())]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'pool.lock'
        holder_p = subprocess.Popen(me + ['--child', str(path), 'selftest holder', '10'], stdout=subprocess.PIPE,
                                    text=True)
        try:
            holder_p.stdout.readline()
            old = os.environ.get('ER_POOL_LOCK')
            os.environ['ER_POOL_LOCK'] = str(path)
            msgs = []
            try:
                try:
                    with take(False, msgs.append):
                        return False, 'took a lock another process holds'
                except Busy as b:
                    h = b.holder or {}
                    ok = h.get('pid') == holder_p.pid and any(f'pid {holder_p.pid}' in m for m in msgs)
                    return ok, f"Busy, holder pid {h.get('pid')} (the holder is {holder_p.pid}): {' '.join(msgs)}"
            finally:
                if old is None:
                    os.environ.pop('ER_POOL_LOCK', None)
                else:
                    os.environ['ER_POOL_LOCK'] = old
        finally:
            holder_p.kill()
            holder_p.wait(timeout=10)


def selftest():
    """Two real processes on a temporary lock file: the second waits for the first and names it;
    with --no-wait it exits 3 at once; a lock held by an ended process is free."""
    ok = True

    def check(name, cond, got=''):
        nonlocal ok
        print(f"{'PASS' if cond else 'FAIL'} {name}{': ' + str(got) if got != '' else ''}")
        ok = ok and bool(cond)
    me = [sys.executable, str(Path(__file__).resolve())]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'pool.lock'
        first = subprocess.Popen(me + ['--child', str(path), 'first run', '3'], stdout=subprocess.PIPE, text=True)
        check('the first run takes the lock', first.stdout.readline().strip() == 'HELD')
        st, h = status(path)
        check('--status sees it held, by the first run\'s pid and cwd', st and h and h['pid'] == first.pid
              and h['cwd'] == os.getcwd() and h['what'] == 'first run', h)
        t0 = time.time()
        busy = subprocess.run(me + ['--child', str(path), 'second run', '0', '--no-wait'], capture_output=True,
                              text=True, timeout=20)
        check('a second run with --no-wait exits 3 at once and names the holder\'s pid and cwd',
              busy.returncode == 3 and 'BUSY' in busy.stdout and f'pid {first.pid}' in busy.stderr
              and os.getcwd() in busy.stderr and time.time() - t0 < 2.5,
              (busy.returncode, busy.stderr.strip()))
        t0 = time.time()
        waiter = subprocess.run(me + ['--child', str(path), 'third run', '0'], capture_output=True, text=True,
                                timeout=30)
        waited = time.time() - t0
        first.wait(timeout=30)
        check('a second run that waits says who holds it, waits for the first to end, then holds it',
              waiter.returncode == 0 and 'HELD' in waiter.stdout and f'pid {first.pid}' in waiter.stderr
              and 'waiting for it' in waiter.stderr and waited >= 1.0, (round(waited, 1), waiter.stderr.strip()))
        st, _ = status(path)
        check('once both ended the lock is free and names no holder', not st and holder(path) is None)
        killed = subprocess.Popen(me + ['--child', str(path), 'killed run', '60'], stdout=subprocess.PIPE, text=True)
        killed.stdout.readline()
        killed.kill()
        killed.wait(timeout=10)
        quick = subprocess.run(me + ['--child', str(path), 'after kill', '0', '--no-wait'], capture_output=True,
                               text=True, timeout=20)
        check('a run killed while it held the lock leaves it free (the kernel lets go)',
              quick.returncode == 0 and 'HELD' in quick.stdout, (quick.returncode, quick.stderr.strip()))
    xok, xdetail = exclusive_check(lambda w, log: held('in-process', wait=w, log=log))
    check('exclusive_check (what the pooled scripts\' selftests read): held(wait=False) in this process '
          'raises Busy naming the holder', xok, xdetail)
    print(f"{'ok' if ok else 'FAILED'}")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--status', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--child', nargs=3, metavar=('PATH', 'WHAT', 'SECONDS'), help=argparse.SUPPRESS)
    ap.add_argument('--no-wait', action='store_true', help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.child:
        return _child(a.child[0], a.child[1], float(a.child[2]), not a.no_wait)
    if a.selftest:
        return selftest()
    st, h = status()
    print(f'{lock_path()}: ' + (f'held by {describe(h)}' if st else 'free'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
