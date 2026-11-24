#!/usr/bin/env python3
"""Run a long model command to its end through what can kill it whole: start it again when it dies
by a signal (the kernel's OOM killer, systemd-oomd, a SIGTERM), once memory is back. The command
must keep its finished work and read it back: `er-mechanics-attack-similarity.py` keeps each
attack set (`.sets`, `.only-sets`) and, inside one run, stops and deals again only the set started
last when memory runs short (`MemoryGuard`), so this wrapper is for the run's own process dying.

    python3 scripts/er-mechanics-resume.py --log <file> [--tries 20] [--resume-gib 16] [--stop-gib 4] -- <command ...>
    python3 scripts/er-mechanics-resume.py --selftest

The command runs in a session of its own, its output appended to --log, each attempt's start and
end written there too. It is started again only when it ended by a signal; an exit with a status
(a failure of its own, 3 for a busy pooled-compute lock) ends this wrapper with that status. Under
--stop-gib of available memory the wrapper stops the command's whole process group itself, before
the kernel picks a process to kill, which may be one of the user's. A SIGTERM, SIGINT or SIGHUP
to the wrapper stops the command's group and ends the wrapper.

The command also runs in a systemd user scope of its own (`systemd-run --user --scope`, `--no-scope`
leaves it in the wrapper's): systemd-oomd sends SIGKILL to every process of the leaf cgroup it
picks (systemd-oomd(8)), and a child stays in its parent's cgroup, so without the scope a kill of
the command's cgroup killed this wrapper too and nothing started it again (2026-11-18: oomctl
monitors user@1000.service for swap over 90% and memory pressure over 80% for 30 s).

What this replaces (target/v28-mem/resume.py, 2026-11-18): it ran `memwatch.py <pid>` and reaped
the command only after memwatch returned, and memwatch watched while /proc/<pid> existed, which an
exited child its parent has not reaped keeps (state Z): a command that ended by itself left both
waiting for ever. It also stopped the command at 8 GiB available, where the model's own guard now
stops one set, and restarted it on any memwatch exit."""
import argparse
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

G = 2**30


def mem_available():
    """MemAvailable in bytes, None where /proc/meminfo has none."""
    try:
        with open('/proc/meminfo') as f:
            for ln in f:
                if ln.startswith('MemAvailable:'):
                    return int(ln.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return None


def group_alive(pgid):
    """Whether any process of group `pgid` is still there to signal."""
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def stop_group(pgid, grace_s=30.0, say=print, reap=None):
    """SIGTERM to the group, then SIGKILL to what is left after `grace_s`; returns once none is left
    or a further `grace_s` has passed. A group already gone is no error. `reap` (the leader's
    `Popen.poll`) is called while waiting: an exited leader not yet reaped still counts as a process
    of its group."""
    for sig, wait_s in ((signal.SIGTERM, grace_s), (signal.SIGKILL, grace_s)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        end = time.monotonic() + wait_s
        while time.monotonic() < end:
            if reap is not None:
                reap()
            if not group_alive(pgid):
                return
            time.sleep(0.2)
        say(f'group {pgid} still has processes {wait_s:g} s after {sig.name}')


def scoped(cmd):
    """`cmd` run in a systemd user scope of its own (`systemd-run --user --scope`, which runs the
    command as its own process, so its exit status and death by a signal come back unchanged), or
    `cmd` itself where no user manager answers. `--expand-environment=no`: systemd-run otherwise
    expands `$` words in the command itself, and `sh -c 'kill -9 $$'` ran as `kill -9 $` and exited 0
    (systemd 262, this selftest, 2026-11-18)."""
    import shutil
    if not shutil.which('systemd-run') or not os.environ.get('XDG_RUNTIME_DIR'):
        return list(cmd)
    return ['systemd-run', '--user', '--scope', '--quiet', '--collect', '--expand-environment=no', '--'] + list(cmd)


def supervise(cmd, log, tries=20, resume_gib=16.0, stop_gib=4.0, poll_s=5.0, available=mem_available,
              say=None, grace_s=30.0, scope=True):
    """Run `cmd` as the module doc says; returns its exit status (0, its own failure's), or 1 when
    it died by a signal `tries` times. `scope` runs it in a cgroup of its own (`scoped`)."""
    run_cmd = scoped(cmd) if scope else list(cmd)

    def note(msg):
        line = f'resume: {msg} {time.strftime("%Y-%m-%dT%H:%M:%S")}'
        with open(log, 'a') as f:
            f.write(line + '\n')
        (say or (lambda m: print(m, flush=True)))(line)
    state = {'p': None, 'quit': None}

    def on_signal(signum, frame):
        state['quit'] = signum
    old = {s: signal.signal(s, on_signal) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    try:
        for n in range(1, tries + 1):
            while (available() or 0) < resume_gib * G and state['quit'] is None:
                note(f'attempt {n} waits for {resume_gib:g} GiB available ({(available() or 0) / G:.1f} now)')
                end = time.monotonic() + 60
                while time.monotonic() < end and state['quit'] is None and (available() or 0) < resume_gib * G:
                    time.sleep(min(poll_s, 5.0))
            if state['quit'] is not None:
                note(f'stopped by {signal.Signals(state["quit"]).name} before attempt {n}')
                return 128 + state['quit']
            note(f'attempt {n} starts: {" ".join(cmd)}')
            with open(log, 'a') as out:
                p = subprocess.Popen(run_cmd, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
            state['p'] = p
            stopped = None
            while p.poll() is None:
                if state['quit'] is not None:
                    stopped = signal.Signals(state['quit']).name
                    break
                got = available()
                if got is not None and got < stop_gib * G:
                    stopped = f'{got / G:.1f} GiB available, under {stop_gib:g}'
                    break
                time.sleep(poll_s)
            if stopped is not None:
                note(f'attempt {n} stopped: {stopped}')
                stop_group(p.pid, grace_s, note, p.poll)
                p.wait()
            else:
                # The command ended by itself; what it left running in its group goes with it.
                if group_alive(p.pid):
                    stop_group(p.pid, grace_s, note, p.poll)
            rc = p.returncode
            note(f'attempt {n} ended rc={rc}')
            if state['quit'] is not None:
                return 128 + state['quit']
            if stopped is None and rc >= 0:
                return rc
        note(f'gave up after {tries} attempts that died by a signal')
        return 1
    finally:
        for s, h in old.items():
            signal.signal(s, h)


def selftest():
    ok = True

    def check(name, cond, got=''):
        nonlocal ok
        print(f"{'PASS' if cond else 'FAIL'} {name}{': ' + str(got) if got != '' else ''}", flush=True)
        ok = ok and bool(cond)
    quiet = []
    with tempfile.TemporaryDirectory() as tmp:
        log = str(Path(tmp) / 'run.log')
        t0 = time.monotonic()
        rc = supervise(['true'], log, poll_s=0.05, say=quiet.append, available=lambda: 64 * G)
        check('a command that ends at once ends the wrapper at once with its status (target/v28-mem/resume.py '
              'waited for ever)', rc == 0 and time.monotonic() - t0 < 5, (rc, round(time.monotonic() - t0, 2)))
        rc = supervise(['sh', '-c', 'exit 3'], log, poll_s=0.05, say=quiet.append, available=lambda: 64 * G)
        check('an exit with a status is not started again', rc == 3 and sum('starts' in m for m in quiet) == 2, rc)
        mark = Path(tmp) / 'once'
        quiet.clear()
        rc = supervise(['sh', '-c', f'if [ -e {mark} ]; then echo second; exit 0; fi; touch {mark}; kill -9 $$'],
                       log, poll_s=0.05, say=quiet.append, available=lambda: 64 * G)
        check('a command killed by a signal is started again and its second attempt ends the wrapper',
              rc == 0 and sum('starts' in m for m in quiet) == 2 and 'second' in Path(log).read_text(), quiet)
        quiet.clear()
        rc = supervise(['sh', '-c', 'kill -9 $$'], log, tries=2, poll_s=0.05, say=quiet.append,
                       available=lambda: 64 * G)
        check('a command that always dies by a signal ends the wrapper after --tries', rc == 1
              and sum('starts' in m for m in quiet) == 2, quiet)
        # Low memory: the group (the command and a child of it) is stopped, the next attempt waits
        # until memory is back, then runs to its end.
        quiet.clear()
        mark2 = Path(tmp) / 'twice'
        seq = {'n': 0}

        def avail():
            seq['n'] += 1
            return 1 * G if 5 <= seq['n'] < 12 else 64 * G
        t0 = time.monotonic()
        rc = supervise(['sh', '-c', f'if [ -e {mark2} ]; then exit 0; fi; touch {mark2}; sleep 30 & sleep 30'],
                       log, poll_s=0.05, say=quiet.append, available=avail, grace_s=5)
        check('under --stop-gib the whole group is stopped, the next attempt waits for --resume-gib and then ends',
              rc == 0 and any('stopped: 1.0 GiB' in m for m in quiet) and any('waits for 16 GiB' in m for m in quiet)
              and time.monotonic() - t0 < 20, quiet)
        # The command's cgroup is not this wrapper's, so systemd-oomd's kill of the one leaves the
        # other to start it again.
        if scoped(['true'])[:1] == ['systemd-run']:
            cg = Path(tmp) / 'cgroup'
            rc = supervise(['sh', '-c', f'cat /proc/self/cgroup > {cg}'], log, poll_s=0.05, say=quiet.append,
                           available=lambda: 64 * G)
            mine = Path('/proc/self/cgroup').read_text().strip()
            theirs = cg.read_text().strip() if cg.exists() else ''
            check('the command runs in a cgroup of its own', rc == 0 and theirs and theirs != mine, (mine, theirs))
        else:
            print('SKIP the command runs in a cgroup of its own: no systemd user manager here', flush=True)
    print('ok' if ok else 'FAILED')
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--log')
    ap.add_argument('--tries', type=int, default=20)
    ap.add_argument('--resume-gib', type=float, default=16.0)
    ap.add_argument('--stop-gib', type=float, default=4.0)
    ap.add_argument('--no-scope', action='store_true',
                    help="leave the command in this wrapper's cgroup (systemd-oomd then kills both)")
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('cmd', nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    cmd = a.cmd[1:] if a.cmd[:1] == ['--'] else a.cmd
    if not cmd or not a.log:
        ap.error('--log and a command are needed')
    return supervise(cmd, a.log, a.tries, a.resume_gib, a.stop_gib, scope=not a.no_scope)


if __name__ == '__main__':
    sys.exit(main())
