#!/usr/bin/env python3
"""Print a Wwise sound bank's chunks and HIRC objects as an indented tree, using bnnm's wwiser parser.

    python3 scripts/er-wwise-dump.py <bank.bnk> [--id 0x1234 ...] [--type CAkSound ...] [--depth N]

wwiser is not vendored: it is read from $WWISER_DIR (default ~/tools/wwiser, a clone of
https://github.com/bnnm/wwiser). Output goes to stdout only. With --id or --type only the matching
HIRC objects are printed; --summary prints the bank header and a count of HIRC object types.
`load(path)` is importable for other scripts (er-voice-bank.py validates its output with it).
"""

import argparse
import collections
import logging
import os
import pathlib
import sys

WWISER_DIR = pathlib.Path(os.environ.get('WWISER_DIR', pathlib.Path.home() / 'tools' / 'wwiser'))


def _wwiser():
    if not (WWISER_DIR / 'wwiser' / 'parser' / 'wparser.py').is_file():
        sys.exit(f'no wwiser at {WWISER_DIR}; git clone https://github.com/bnnm/wwiser there or set WWISER_DIR')
    sys.path.insert(0, str(WWISER_DIR))
    from wwiser.parser import wparser
    return wparser


def load(path):
    """Parse one bank; returns the wwiser root node (raises if wwiser reports parse errors)."""
    logging.disable(logging.CRITICAL)
    wparser = _wwiser()
    parser = wparser.Parser()
    parser.parse_bank(str(path))
    banks = parser.get_banks()
    if not banks:
        raise ValueError(f'wwiser could not parse {path}')
    return banks[0]


def errors(node):
    out = []
    stack = [node]
    while stack:
        n = stack.pop()
        if n.get_nodename() in ('error', 'skip'):
            out.append(dict(n.get_attrs()))
        stack.extend(n.get_children() or [])
    return out


def fmt(node):
    a = node.get_attrs()
    name = a.get('name', '')
    val = a.get('value')
    extra = ''
    if 'valuefmt' in a:
        extra = f' ({a["valuefmt"]})'
    elif 'hashname' in a:
        extra = f' ({a["hashname"]})'
    if isinstance(val, int) and a.get('type') in ('tid', 'sid', 'u32', 'U32'):
        sval = f'{val} 0x{val:08X}'
    else:
        sval = '' if val is None else str(val)
    tag = node.get_nodename()
    if tag in ('object', 'list'):
        idx = f'[{a["index"]}]' if 'index' in a else ''
        cnt = f' count={a["count"]}' if 'count' in a else ''
        return f'{tag} {name}{idx}{cnt}'
    return f'{name} = {sval}{extra}'


def walk(node, depth, max_depth, lines):
    lines.append('  ' * depth + fmt(node))
    if max_depth is not None and depth >= max_depth:
        return
    for c in node.get_children() or []:
        walk(c, depth + 1, max_depth, lines)


def hirc_objects(root):
    for chunk in root.get_children() or []:
        if chunk.get_attrs().get('name') == 'HircChunk':
            for lst in chunk.get_children() or []:
                if lst.get_nodename() == 'list':
                    for obj in lst.get_children() or []:
                        yield obj


def obj_id(obj):
    n = obj.find1(name='ulID')
    return n.get_attrs().get('value') if n is not None else None


def obj_type(obj):
    return obj.get_attrs().get('name')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('bank', type=pathlib.Path)
    ap.add_argument('--id', action='append', default=[], help='HIRC object id (hex or decimal)')
    ap.add_argument('--type', action='append', default=[], help='HIRC object class, e.g. CAkSound')
    ap.add_argument('--depth', type=int, default=None)
    ap.add_argument('--summary', action='store_true')
    a = ap.parse_args()
    root = load(a.bank)
    print(f'bank {a.bank.name}: version {root.get_version()} id 0x{(root.get_id() or 0):08X}')
    errs = errors(root)
    if errs:
        print(f'wwiser reported {len(errs)} error/skip nodes: {errs[:5]}')
    ids = {int(x, 0) for x in a.id}
    types = set(a.type)
    lines = []
    if a.summary:
        counts = collections.Counter(obj_type(o) for o in hirc_objects(root))
        for k, v in counts.most_common():
            print(f'  {k}: {v}')
        return 0 if not errs else 1
    if not ids and not types:
        walk(root, 0, a.depth, lines)
    else:
        for o in hirc_objects(root):
            if (ids and obj_id(o) in ids) or (types and obj_type(o) in types):
                walk(o, 0, a.depth, lines)
    print('\n'.join(lines))
    return 0 if not errs else 1


if __name__ == '__main__':
    sys.exit(main())
