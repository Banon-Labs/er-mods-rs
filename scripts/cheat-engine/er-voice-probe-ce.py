#!/usr/bin/env python3
"""Start a second Cheat Engine inside the running game's container that runs the positional voice
probe (er-voice-probe.lua) once, logs it beside eldenring.exe and closes itself.

    python3 scripts/cheat-engine/er-voice-probe-ce.py --voice-probe   # run the probe
    python3 scripts/cheat-engine/er-voice-probe-ce.py --dry-run       # print the command only
    python3 scripts/cheat-engine/er-voice-probe-ce.py                 # remove a leftover autorun copy

The Cheat Engine is started the way er-duel-rules-ce.py starts one: Proton's wine inside the
game's pressure-vessel namespaces, because a wineserver is per mount namespace. A Cheat Engine the
user already has attached is left alone; this one opens its own handle to eldenring.exe.

--voice-probe copies er-voice-probe.lua into Cheat Engine's autorun folder as
zz-er-voice-probe.lua and passes the log path, the PCM path and ER_VOICE_PROBE_CLOSE=1 through the
environment, plus ER_VOICE_PROBE_BANK naming the Wwise bank from scripts/er-voice-bank.py, which the
probe loads with AK::SoundEngine::LoadBankMemoryCopy before anything else (--no-bank runs the
original probe without it). The copy is removed as soon as the log shows Cheat Engine has run it, and again on
every exit path, so a Cheat Engine started later behaves as installed. The script then waits for
the probe's `done` line or for that Cheat Engine to exit, whichever is first, and prints the log.
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
REPO = SCRIPTS.parent
AUTORUN_SOURCE = HERE / 'er-voice-probe.lua'
AUTORUN_NAME = 'zz-er-voice-probe.lua'
DEFAULT_PCM = REPO / 'target' / 'voice-mock' / 'tts-48k.pcm'
DEFAULT_BANK = REPO / 'target' / 'voice-bank' / 'er_voicechat.bnk'
DEFAULT_CE =pathlib.Path.home() / '.local/share/cheat-engine-win/app/cheatengine-x86_64.exe'
CACHE = pathlib.Path(os.environ.get('ER_FRIDA_CACHE', pathlib.Path.home() / '.cache' / 'er-ce'))
WINE_LOG = CACHE / 'cheat-engine-voice-probe.log'
LOG_NAME = 'er-voice-probe.log'
PCM_BYTES_NEEDED = 1920 * 100


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
    ap.add_argument('--voice-probe', action='store_true', help='run the probe once and print its log')
    ap.add_argument('--dry-run', action='store_true', help='print the command and change nothing')
    ap.add_argument('--pcm', type=pathlib.Path, default=DEFAULT_PCM,
                    help='raw s16le 48 kHz mono, at least 2 s (default: %(default)s)')
    ap.add_argument('--bank', type=pathlib.Path, default=DEFAULT_BANK,
                    help='Wwise bank loaded with LoadBankMemoryCopy before the probe; build it with '
                         'scripts/er-voice-bank.py (default: %(default)s)')
    ap.add_argument('--no-bank', action='store_true',
                    help='the original probe: load no bank, only post Play_VoiceChat')
    ap.add_argument('--prefill', type=int, default=10,
                    help='20 ms chunks pushed before Play, so the voice starts with data (default: %(default)s)')
    ap.add_argument('--event', default='VoiceChat',
                    help='post Play_<event>/Stop_<event> (VoiceChatSine with the --sine-test bank)')
    ap.add_argument('--event-id', default=None,
                    help='control: post this loaded game event id (hex) on the object instead, then '
                         'StopPlayingID it; e.g. 0x3FE983A8 = cs_smain looping Wwise Silence')
    ap.add_argument('--reload-bank', action='store_true',
                    help='UnloadBank the bank id first, so a rebuilt bank with the same id replaces the loaded one')
    ap.add_argument('--inspect', action='store_true',
                    help='read-only Wwise queries (node loaded? what is the game playing?); builds no object')
    ap.add_argument('--wait', type=float, default=150.0,
                    help='seconds to wait for the probe to finish (default: %(default)s)')
    a = ap.parse_args()

    ce = pathlib.Path(os.environ.get('CE_EXE', DEFAULT_CE))
    copy = ce.parent / 'autorun' / AUTORUN_NAME
    if not a.voice_probe:
        if copy.exists() and not a.dry_run:
            copy.unlink()
            print(f'removed {copy}')
        return 0

    up = frida_up()
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
    pcm = a.pcm.resolve()
    if not pcm.is_file() or pcm.stat().st_size < PCM_BYTES_NEEDED:
        print(f'{pcm} is missing or shorter than {PCM_BYTES_NEEDED} bytes', file=sys.stderr)
        return 1
    bank = None if a.no_bank else a.bank.resolve()
    if bank is not None and not bank.is_file():
        print(f'{bank} is missing; run python3 {SCRIPTS}/er-voice-bank.py or pass --no-bank', file=sys.stderr)
        return 1

    game_dir =pathlib.Path(os.readlink(f'/proc/{pid}/cwd'))
    log = game_dir / LOG_NAME
    env = dict(os.environ)
    env['WINEPREFIX'] = str(up.prefix())
    env['ER_VOICE_PROBE_LOG'] = windows_path(log)
    env['ER_VOICE_PROBE_PCM'] = windows_path(pcm)
    env['ER_VOICE_PROBE_CLOSE'] = '1'
    env['ER_VOICE_PROBE_EVENT'] = a.event
    env['ER_VOICE_PROBE_EVENT_ID'] = a.event_id or ''
    env['ER_VOICE_PROBE_INSPECT'] = '1' if a.inspect else ''
    env['ER_VOICE_PROBE_RELOAD'] = '1' if a.reload_bank else ''
    env['ER_VOICE_PROBE_PREFILL'] =str(max(0, min(a.prefill, 30)))
    env['ER_VOICE_PROBE_BANK'] =windows_path(bank) if bank is not None else ''
    command = up.container_prefix(pid) + [str(wine), str(ce)]
    print(' '.join(command))
    if a.dry_run:
        return 0

    if log.exists():
        log.unlink()
    copy.write_bytes(AUTORUN_SOURCE.read_bytes())
    WINE_LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(WINE_LOG, 'wb') as handle:
            process = subprocess.Popen(command, env=env, stdout=handle, stderr=subprocess.STDOUT,
                                       start_new_session=True)
        print(f'Cheat Engine started (launcher pid {process.pid}, game pid {pid}); '
              f'probe log {log}; wine output {WINE_LOG}')
        # Woken by a write in the log's directory (inotify) or by the launcher exiting (pidfd);
        # the deadline only bounds how long a probe that never logs `done` is waited on.
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
                    if copy.exists() and 'voice probe armed' in text:
                        copy.unlink()
                    if text.rstrip().endswith('done') or process.poll() is not None:
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        print(f'the probe did not finish within {a.wait:.0f} s', file=sys.stderr)
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
