#!/usr/bin/env python3
"""Run and read back scripts/frida/chainsaw-driver.js (docs/er-mechanics/chainsaw/driver.md).

    python3 scripts/er-chainsaw-drive.py command [--preset chainsaw|control-wheel|control-starcaller] [--set key=value ...]
    python3 scripts/er-chainsaw-drive.py report [log] [--since epoch_seconds]
    python3 scripts/er-chainsaw-drive.py --selftest

`command` prints the two commands that start a drive: the Wine-side Frida server, then the watcher
with the driver agent and its config. It launches nothing. The watcher must run as its own
background task with no timeout around it (AGENTS.md, Frida rules); the drive starts the moment the
agent loads and ends on its own verdict, after which input is the player's again.

`report` reads the watcher's log (default ~/.cache/er-frida/hits.jsonl) and prints every step with
the semaphore that released it, every tap, every native setup action, and the final verdict with
the block verification. Exit status 0 on `success` or `control_done`, 1 on any other verdict, 2
when no verdict is in the log yet.
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
AGENT = REPO / 'scripts' / 'frida' / 'chainsaw-driver.js'
DEFAULT_LOG = pathlib.Path(os.environ.get('ER_FRIDA_LOG', pathlib.Path.home() / '.cache' / 'er-frida' / 'hits.jsonl'))

PRESETS = {
    'chainsaw': {'mode': 'chainsaw'},
    'control-wheel': {'mode': 'control', 'controlWeapon': 'source'},
    'control-starcaller': {'mode': 'control', 'controlWeapon': 'target'},
}


def parse_value(text):
    try:
        return json.loads(text)
    except ValueError:
        return text


def build_config(preset, sets):
    cfg = dict(PRESETS[preset])
    for item in sets:
        key, _, value = item.partition('=')
        if not key or not _:
            raise ValueError(f'--set wants key=value, got {item!r}')
        cfg[key] = parse_value(value)
    return cfg


def commands(cfg):
    up = f'python3 {REPO}/scripts/er-frida-up.py'
    watch = (f'uv run --with frida python3 {REPO}/scripts/er-frida-watch.py --agent {AGENT} '
             f"--role chainsaw-drive --config-json '{json.dumps(cfg, sort_keys=True)}'")
    return [up, watch]


def payloads(lines, since=0.0):
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get('at', 0) < since:
            continue
        p = rec.get('message', {}).get('payload')
        if isinstance(p, dict) and p.get('kind') in ('armed', 'start', 'drive', 'result'):
            yield p


def short(value, limit=220):
    text = json.dumps(value, sort_keys=True)
    return text if len(text) <= limit else text[:limit] + '...'


def report(evs):
    out = []
    result = None
    for e in evs:
        kind = e['kind']
        if kind == 'armed':
            out.append(f"armed     hooks={e.get('hooks')} block={short(e.get('block'))} xinput={e.get('xinput')}")
        elif kind == 'start':
            result = None
            out.append(f"start     frame {e.get('frame')}")
        elif kind == 'drive':
            r = e.get('rec', {})
            tag = f"f{r.get('frame')} a{r.get('attempt')}{' dry' if r.get('dry') else ''}"
            if r.get('kind') == 'step':
                out.append(f"{tag:<14} {r.get('from')} -> {r.get('to')}  ({r.get('why')}, waited {r.get('waited')})  sem {short(r.get('sem'))}")
            elif r.get('kind') == 'tap':
                out.append(f"{tag:<14}   tap {r.get('label')}")
            elif r.get('kind') in ('fail', 'verdict', 'abort', 'retry', 'sem', 'native_request'):
                body = {k: v for k, v in r.items() if k not in ('kind', 'frame', 'state', 'attempt', 'dry')}
                out.append(f"{tag:<14}   {r.get('kind')} {short(body)}")
        elif kind == 'result':
            result = e
    if result is None:
        out.append('no verdict in the log yet')
        return out, 2
    summary = result.get('summary', {})
    verdict = (summary.get('verdict') or {}).get('verdict')
    block = result.get('block', {})
    out.append('')
    out.append(f'verdict   {verdict}  (attempts {summary.get("attempt")})')
    out.append(f'learned   {short(summary.get("learned"))}')
    out.append(f'block     held={block.get("held")} padDeviceTracksStamp={block.get("padDeviceTracksStamp")} '
               f'polls={block.get("polls")} stamped={block.get("stamped")}')
    out.append(f'foreign   {short(block.get("foreignInputSeen"))}  (input the user made that the block replaced)')
    for a in summary.get('attempts', []):
        out.append(f'attempt {a.get("attempt")}: outcome={a.get("outcome")} refusal={short(a.get("refusal"))} equip={short(a.get("equip"))}')
    return out, 0 if verdict in ('success', 'control_done') else 1


def selftest():
    ok = True

    def check(name, cond):
        nonlocal ok
        print(('ok   ' if cond else 'FAIL ') + name)
        ok = ok and cond

    cfg = build_config('control-starcaller', ['attempts=5', 'listNav=stick'])
    check('a preset and --set overrides build one config', cfg == {'mode': 'control', 'controlWeapon': 'target', 'attempts': 5, 'listNav': 'stick'})
    cmd = commands(cfg)
    check('the watcher command names the driver agent by absolute path', str(AGENT) in cmd[1] and cmd[1].count('--config-json') == 1)
    check('no command carries a timeout', not any('timeout' in c for c in cmd))
    lines = [
        json.dumps({'at': 1, 'message': {'payload': {'kind': 'start', 'frame': 0}}}),
        json.dumps({'at': 2, 'message': {'payload': {'kind': 'drive', 'rec': {'kind': 'step', 'frame': 5, 'attempt': 1, 'from': 'PRECHECK', 'to': 'SETUP', 'why': 'w', 'sem': {'x': 1}, 'waited': 10}}}}),
        json.dumps({'at': 3, 'message': {'payload': {'kind': 'result', 'summary': {'verdict': {'verdict': 'success'}, 'attempt': 1, 'attempts': []}, 'block': {'held': True}}}}),
    ]
    out, code = report(payloads(lines))
    check('a success verdict exits 0', code == 0 and any('verdict   success' in l for l in out))
    check('a step line names both states and its semaphore', any('PRECHECK -> SETUP' in l and '"x": 1' in l for l in out))
    out, code = report(payloads(lines[:2]))
    check('no verdict yet exits 2', code == 2)
    out, code = report(payloads(lines, since=2.5))
    check('--since drops earlier records', code == 0 and not any('PRECHECK' in l for l in out))
    node = subprocess.run(['node', str(AGENT), '--selftest'], capture_output=True, text=True, timeout=25)
    check('the driver state machine selftest passes under node', node.returncode == 0)
    if node.returncode != 0:
        print(node.stdout[-2000:])
    return ok


def main(argv):
    if '--selftest' in argv:
        return 0 if selftest() else 1
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('command')
    c.add_argument('--preset', choices=sorted(PRESETS), default='chainsaw')
    c.add_argument('--set', action='append', default=[], metavar='key=value', help='config override; the value is JSON when it parses as JSON')
    r = sub.add_parser('report')
    r.add_argument('log', nargs='?', type=pathlib.Path, default=DEFAULT_LOG)
    r.add_argument('--since', type=float, default=0.0)
    args = ap.parse_args(argv)
    if args.cmd == 'command':
        for line in commands(build_config(args.preset, args.set)):
            print(line)
        return 0
    if not args.log.is_file():
        print(f'no log at {args.log}')
        return 2
    with args.log.open(encoding='utf-8') as fh:
        out, code = report(payloads(fh, args.since))
    print('\n'.join(out))
    return code


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
