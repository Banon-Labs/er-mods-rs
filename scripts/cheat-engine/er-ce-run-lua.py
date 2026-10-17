#!/usr/bin/env python3
"""Run one Cheat Engine Lua script against the running Elden Ring, log it, and close that Cheat
Engine again.

    python3 scripts/cheat-engine/er-ce-run-lua.py scripts/cheat-engine/er-audio-background.lua
    python3 scripts/cheat-engine/er-ce-run-lua.py --dry-run <script.lua>

The Cheat Engine starts inside the game's pressure-vessel namespaces, the same way
er-voice-probe-ce.py starts one. The script is copied into Cheat Engine's autorun folder as
zz-er-ce-run.lua and acts only when ER_CE_RUN_LOG names its log, which this launcher sets to
<script name>.log beside eldenring.exe; ER_CE_RUN_CLOSE=1 asks it to close Cheat Engine when it
writes its `done` line. The autorun copy is removed once the log appears, and on every exit path.
"""

import argparse
import importlib.util
import os
import pathlib
import select
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
SCRIPTS = HERE.parent
AUTORUN_NAME = 'zz-er-ce-run.lua'
DEFAULT_CE = pathlib.Path.home() / '.local/share/cheat-engine-win/app/cheatengine-x86_64.exe'
CACHE = pathlib.Path(os.environ.get('ER_FRIDA_CACHE', pathlib.Path.home() / '.cache' / 'er-ce'))


def frida_up():
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location('er_frida_up', SCRIPTS / 'er-frida-up.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def windows_path(path: pathlib.Path) -> str:
    """Wine's Z: drive is the Unix root."""
    return 'Z:' + str(path).replace('/', '\\')


def read_log(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding='utf-8', errors='replace')
    except FileNotFoundError:
        return ''


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('script', type=pathlib.Path, help='the Cheat Engine Lua script to run')
    ap.add_argument('--dry-run', action='store_true', help='print the command and change nothing')
    ap.add_argument('--wait', type=float, default=60.0,
                    help='seconds to wait for the script to log `done` (default: %(default)s)')
    a = ap.parse_args()

    script = a.script.resolve()
    if not script.is_file():
        print(f'no script at {script}', file=sys.stderr)
        return 1
    up = frida_up()
    ce = pathlib.Path(os.environ.get('CE_EXE', DEFAULT_CE))
    if not ce.is_file():
        print(f'no Cheat Engine at {ce}; unpack the 7.5 installer there or set CE_EXE', file=sys.stderr)
        return 1
    pid = up.game_pid()
    if pid is None:
        print('eldenring.exe is not running; start the game first', file=sys.stderr)
        return 1
    wine = up.wine_binary()
    if wine is None:
        print('no Proton wine binary found under Steam', file=sys.stderr)
        return 1

    game_dir = pathlib.Path(os.readlink(f'/proc/{pid}/cwd'))
    log = game_dir / (script.stem + '.log')
    env = dict(os.environ)
    env['WINEPREFIX'] = str(up.prefix())
    env['ER_CE_RUN_LOG'] = windows_path(log)
    env['ER_CE_RUN_CLOSE'] = '1'
    command = up.container_prefix(pid) + [str(wine), str(ce)]
    print(' '.join(command))
    if a.dry_run:
        return 0

    copy = ce.parent / 'autorun' / AUTORUN_NAME
    if log.exists():
        log.unlink()
    copy.write_bytes(script.read_bytes())
    wine_log = CACHE / (script.stem + '-wine.log')
    wine_log.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(wine_log, 'wb') as handle:
            process = subprocess.Popen(command, env=env, stdout=handle, stderr=subprocess.STDOUT,
                                       start_new_session=True)
        print(f'Cheat Engine started (launcher pid {process.pid}, game pid {pid}); log {log}')
        # Woken by a write in the log's directory (inotify) or by the launcher exiting (pidfd);
        # the deadline only bounds how long a script that never logs `done` is waited on.
        import er_run_lib  # on sys.path through frida_up()
        pidfd = os.pidfd_open(process.pid)
        try:
            with er_run_lib.DirectoryWatch(log.parent) as watch:
                if not watch.available:
                    print(f'cannot watch {log.parent} with inotify', file=sys.stderr)
                    return 1
                deadline = time.monotonic() + a.wait
                while True:
                    text = read_log(log)
                    if copy.exists() and text:
                        copy.unlink()
                    if text.rstrip().endswith('done') or process.poll() is not None:
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        print(f'the script did not finish within {a.wait:.0f} s', file=sys.stderr)
                        break
                    ready, _, _ = select.select([watch.fd, pidfd], [], [], remaining)
                    if watch.fd in ready:
                        os.read(watch.fd, 65536)
        finally:
            os.close(pidfd)
    finally:
        if copy.exists():
            copy.unlink()
    print(read_log(log), end='')
    return 0 if read_log(log).rstrip().endswith('done') else 2


if __name__ == '__main__':
    sys.exit(main())
