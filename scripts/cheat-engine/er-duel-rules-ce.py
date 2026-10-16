#!/usr/bin/env python3
"""Start the Windows Cheat Engine beside a running Elden Ring on this machine, with the duel rules
table loaded.

    python3 scripts/cheat-engine/er-duel-rules-ce.py              # CE with er-duel-rules.CT open
    python3 scripts/cheat-engine/er-duel-rules-ce.py --autotest   # ...and ticks the entry itself
    python3 scripts/cheat-engine/er-duel-rules-ce.py --dry-run    # print the command only

On Windows none of this is needed: open er-duel-rules.CT in Cheat Engine and tick the entry. Here
the game runs under Proton inside pressure-vessel, and a wineserver is per mount namespace, so a
Cheat Engine started beside the container would see no eldenring.exe at all. It is started inside
the game's namespaces with Proton's own wine, the way `scripts/er-frida-up.py` starts
frida-server.

Cheat Engine is the 7.5 release, unpacked from its installer with innoextract rather than
installed (the installer carries third-party offers): `$CE_EXE`, else
`~/.local/share/cheat-engine-win/app/cheatengine-x86_64.exe`.

`--autotest` copies er-duel-rules-autotest.lua into Cheat Engine's autorun folder and names the
table in `ER_DUEL_RULES_AUTOTEST`, so Cheat Engine attaches and ticks the entry with no hands; it
notes what it did in er-duel-rules-autotest.log beside eldenring.exe.

`--ui-test` copies er-duel-rules-ui-autotest.lua instead: Cheat Engine opens the ban editor and
the Rules window, presses their buttons, checks the rules file they write (into
`~/.cache/er-ce/ui-test`, never the game's), writes `ui-autotest.log` there and closes itself. It
opens no process, but runs in the game's container like everything else here.

A launch without either flag removes both autorun copies, so a later hand-started Cheat Engine
behaves as installed.
"""

import argparse
import importlib.util
import os
import pathlib
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
SCRIPTS = HERE.parent
TABLE = HERE / 'er-duel-rules.CT'
REFEREE = HERE / 'er-duel-rules.lua'
AUTORUN_SOURCE = HERE / 'er-duel-rules-autotest.lua'
AUTORUN_NAME = 'zz-er-duel-rules-autotest.lua'
UI_AUTORUN_SOURCE = HERE / 'er-duel-rules-ui-autotest.lua'
UI_AUTORUN_NAME = 'zz-er-duel-rules-ui-autotest.lua'
DEFAULT_CE = pathlib.Path.home() / '.local/share/cheat-engine-win/app/cheatengine-x86_64.exe'
CACHE = pathlib.Path(os.environ.get('ER_FRIDA_CACHE', pathlib.Path.home() / '.cache' / 'er-ce'))
LOG = CACHE / 'cheat-engine.log'
UI_TEST_DIR = CACHE / 'ui-test'


def frida_up():
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location('er_frida_up', SCRIPTS / 'er-frida-up.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def windows_path(path: pathlib.Path) -> str:
    """Wine's Z: drive is the Unix root."""
    return 'Z:' + str(path).replace('/', '\\')


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--autotest', action='store_true', help='attach and tick the entry with no hands')
    ap.add_argument('--ui-test', action='store_true',
                    help='drive the ban editor and Rules window, log the result, close Cheat Engine')
    ap.add_argument('--dry-run', action='store_true', help='print the command and change nothing')
    ap.add_argument('--table', type=pathlib.Path, default=TABLE)
    a = ap.parse_args()

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

    table = a.table.resolve()
    env = dict(os.environ)
    env['WINEPREFIX'] = str(up.prefix())
    autorun_dir = ce.parent / 'autorun'
    wanted = None
    if a.autotest:
        game_dir = pathlib.Path(os.readlink(f'/proc/{pid}/cwd'))
        env['ER_DUEL_RULES_AUTOTEST'] = windows_path(table)
        env['ER_DUEL_RULES_AUTOTEST_LOG'] = windows_path(game_dir / 'er-duel-rules-autotest.log')
        command = up.container_prefix(pid) + [str(wine), str(ce)]
        wanted = (AUTORUN_SOURCE, AUTORUN_NAME)
    elif a.ui_test:
        env['ER_DUEL_RULES_UI_TEST'] = windows_path(REFEREE)
        env['ER_DUEL_RULES_UI_TEST_DIR'] = windows_path(UI_TEST_DIR)
        command = up.container_prefix(pid) + [str(wine), str(ce)]
        wanted = (UI_AUTORUN_SOURCE, UI_AUTORUN_NAME)
    else:
        command = up.container_prefix(pid) + [str(wine), str(ce), windows_path(table)]

    print(' '.join(command))
    if a.dry_run:
        return 0
    for name in (AUTORUN_NAME, UI_AUTORUN_NAME):
        copy = autorun_dir / name
        if wanted is not None and name == wanted[1]:
            shutil.copyfile(wanted[0], copy)
        elif copy.exists():
            copy.unlink()
    if a.ui_test:
        UI_TEST_DIR.mkdir(parents=True, exist_ok=True)
        print(f'the result will be in {UI_TEST_DIR / "ui-autotest.log"}')
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, 'wb') as handle:
        process = subprocess.Popen(command, env=env, stdout=handle, stderr=subprocess.STDOUT,
                                   start_new_session=True)
    print(f'Cheat Engine started (launcher pid {process.pid}, game pid {pid}); wine output in {LOG}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
