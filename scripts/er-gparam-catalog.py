#!/usr/bin/env python3
"""Catalogue the groups and fields of Elden Ring's GPARAM (graphics param) files.

Reads the WitchyBND XML form of every `param/drawparam/*.gparam.dcx` in an already-unpacked
corpus and prints one line per distinct field: its group, the field's short name (`name2`, what
the game code keys on), its editor display name (`name1`), the value type, how many gparam files
carry it, which value ids occur, and one sample value with the file, id and time of day it came
from.

    python3 scripts/er-gparam-catalog.py                 # every field across every file
    python3 scripts/er-gparam-catalog.py --file m60_00_0000   # one file, every value row

The corpus root defaults to the 2026-07-13 recursive Witchy unpack and is overridable with
`ER_GPARAM_XML_ROOT`. Read-only: it writes nothing but stdout.
"""
import argparse
import collections
import os
import sys
import xml.etree.ElementTree as ET

DEFAULT_ROOT = os.path.join(
    os.environ.get('HOME', ''),
    'er-extract/LOOK_HERE_WITCHY_RECURSIVE_20260713/sharded/param/drawparam')
SUFFIX = '.gparam.dcx.xml'
# Files whose values are preferred as the sample: the overworld base first.
SAMPLE_PREFERENCE = ['m60_00_0000', 'mapbase', 'm00_00_0000_weatherbase',
                     'm00_00_0000_weatheroutdoor', 'default', 'm_template']


def find_files(root):
    files = {}
    for dirpath, _dirs, names in os.walk(root):
        for name in names:
            if name.endswith(SUFFIX):
                files[name[:-len(SUFFIX)]] = os.path.join(dirpath, name)
    return files


def groups_of(path):
    for index, group in enumerate(ET.parse(path).getroot().find('groups')):
        yield index, group


def dump_file(path):
    for index, group in groups_of(path):
        params = group.findall('param')
        print(f"[{index:2d}] {group.get('name2')!r} (editor {group.get('name1')!r}) fields={len(params)}")
        for param in params:
            print(f"    {param.get('name2')} | {param.get('name1')} | {param.get('type')}")
            for value in param.findall('value'):
                print(f"        id={value.get('id'):>6s} tod={value.get('timeOfDay'):>6s}  {value.text}")


def catalogue(files):
    order = sorted(files, key=lambda n: (SAMPLE_PREFERENCE.index(n)
                                         if n in SAMPLE_PREFERENCE else 99, n))
    groups = collections.OrderedDict()
    group_index = {}
    for name in order:
        for index, group in groups_of(files[name]):
            key = (group.get('name2'), group.get('name1'))
            group_index.setdefault(key, index)
            fields = groups.setdefault(key, collections.OrderedDict())
            for param in group.findall('param'):
                info = fields.setdefault(param.get('name1'), {
                    'name2': param.get('name2'), 'type': param.get('type'), 'files': 0,
                    'ids': set(), 'sample': None, 'sample_from': None})
                info['files'] += 1
                values = param.findall('value')
                info['ids'].update(int(v.get('id')) for v in values)
                if info['sample'] is None and values:
                    info['sample'] = values[0].text
                    info['sample_from'] = (f"{name} id={values[0].get('id')} "
                                           f"tod={values[0].get('timeOfDay')}")
    print(f'# {len(files)} gparam files')
    print('# group: [index] name2 (editor name1); field: name2 | name1 | type | files | ids | sample')
    for key in sorted(groups, key=lambda k: group_index[k]):
        fields = groups[key]
        print(f'\n[{group_index[key]:2d}] {key[0]!r} (editor {key[1]!r}) fields={len(fields)}')
        for name1, info in fields.items():
            ids = sorted(info['ids'])
            id_text = ','.join(map(str, ids[:8])) + ('...' if len(ids) > 8 else '')
            print(f"    {info['name2']:36s} | {name1:58s} | {info['type']:6s} | "
                  f"files={info['files']:3d} | ids={id_text} | "
                  f"sample={info['sample']!r} ({info['sample_from']})")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--root', default=os.environ.get('ER_GPARAM_XML_ROOT', DEFAULT_ROOT))
    parser.add_argument('--file', help='dump every value row of one gparam (name without suffix)')
    args = parser.parse_args()
    files = find_files(args.root)
    if not files:
        sys.exit(f'no *{SUFFIX} under {args.root}')
    if args.file:
        if args.file not in files:
            sys.exit(f'{args.file} not found under {args.root}')
        dump_file(files[args.file])
    else:
        catalogue(files)


if __name__ == '__main__':
    main()
