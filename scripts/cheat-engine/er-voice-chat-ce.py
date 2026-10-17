#!/usr/bin/env python3
"""Start the Windows Cheat Engine beside a running Elden Ring on this machine, with the voice chat
table loaded.

    python3 scripts/cheat-engine/er-voice-chat-ce.py              # CE with er-voice-chat.CT open
    python3 scripts/cheat-engine/er-voice-chat-ce.py --autotest   # ...ticks, unticks, reticks it
    python3 scripts/cheat-engine/er-voice-chat-ce.py --dry-run    # print the command only

On Windows none of this is needed: open er-voice-chat.CT in Cheat Engine and tick the entry. Here
Cheat Engine has to run inside the game's pressure-vessel namespaces, which er-duel-rules-ce.py
already knows how to do; this reuses it.

`--autotest` copies er-voice-chat-autotest.lua into Cheat Engine's autorun folder. It records the
patch bytes and every readout before ticking, after ticking, after unticking and after ticking
again, in er-voice-chat-autotest.log beside eldenring.exe, and leaves the entry ticked.
"""

import argparse
import importlib.util
import os
import pathlib
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
TABLE = HERE / 'er-voice-chat.CT'
AUTORUN_SOURCE = HERE / 'er-voice-chat-autotest.lua'
AUTORUN_NAME = 'zz-er-voice-chat-autotest.lua'


def duel_launcher():
    spec = importlib.util.spec_from_file_location('er_duel_rules_ce', HERE / 'er-duel-rules-ce.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--autotest', action='store_true', help='tick, untick and retick the entry, logging each')
    ap.add_argument('--dry-run', action='store_true', help='print the command and change nothing')
    a = ap.parse_args()

    duel = duel_launcher()
    up = duel.frida_up()
    ce = pathlib.Path(os.environ.get('CE_EXE', duel.DEFAULT_CE))
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

    env = dict(os.environ)
    env['WINEPREFIX'] = str(up.prefix())
    copy = ce.parent / 'autorun' / AUTORUN_NAME
    if a.autotest:
        game_dir = pathlib.Path(os.readlink(f'/proc/{pid}/cwd'))
        log = game_dir / 'er-voice-chat-autotest.log'
        env['ER_VOICE_CHAT_AUTOTEST'] = duel.windows_path(TABLE)
        env['ER_VOICE_CHAT_AUTOTEST_LOG'] = duel.windows_path(log)
        command = up.container_prefix(pid) + [str(wine), str(ce)]
    else:
        command = up.container_prefix(pid) + [str(wine), str(ce), duel.windows_path(TABLE)]

    print(' '.join(command))
    if a.dry_run:
        return 0
    if a.autotest:
        shutil.copyfile(AUTORUN_SOURCE, copy)
        print(f'the result will be in {log}')
    elif copy.exists():
        copy.unlink()
    duel.LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(duel.LOG, 'wb') as handle:
        process = subprocess.Popen(command, env=env, stdout=handle, stderr=subprocess.STDOUT,
                                   start_new_session=True)
    print(f'Cheat Engine started (launcher pid {process.pid}, game pid {pid}); wine output in {duel.LOG}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
