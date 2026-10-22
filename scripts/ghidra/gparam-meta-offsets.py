#!/usr/bin/env python3
"""List the GPARAM fields a GX param manager registers, with the byte offset each one binds.

Every GX param manager in Elden Ring (`GXLightSetManager`, the tone-map filter manager, the
volumetric-fog manager, ...) has a registration function that `GXEnvironmentManager::Setup`
calls once. It names the group (`L"LightSet", L"LightSet ParamEditor"`) and then, for each field,
passes the field's two names (`name2`, the key the gparam file uses, and `name1`, its editor
label) together with either a meta-info object whose second qword is `manager + offset` or, for
an enum field, `manager + offset` as a direct argument. This script decompiles those functions
through the Ghidra MCP daemon and prints, per field:

    group | name2 | name1 | manager offset | meta-info type

    python3 scripts/ghidra/gparam-meta-offsets.py 0x141a408f0              # 1.16.2 LightSet
    python3 scripts/ghidra/gparam-meta-offsets.py --port 8767 0x141a426f0  # same on 1.17.0

The offset is relative to the manager object handed to the registration function, not to a
per-keyframe param instance; see bd `er-gparam-sun-lighting-pipeline-1171-2026-10-15` for how
the LightSet one maps onto `GXLightSet`. Labels Ghidra left as `&DAT_<va>` are read out of the
flat image named by `--image`. Read-only: it writes nothing but stdout.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcp_query import query  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_IMAGE = {8765: 'eldenring-deobf.bin', 8767: 'eldenring-deobf-1.17.bin'}
IMAGE_BASE = 0x140000000

WSTR = r'(?:L"(?:[^"\\]|\\.)*"|&?(?:DAT|UNK)_[0-9a-fA-F]+)'
WSTR_PAIR = re.compile(rf'({WSTR})\s*,\s*({WSTR})')
GROUP_CALL = re.compile(rf'({WSTR})\s*,\s*({WSTR})\s*\)\s*$')
META_ARG = re.compile(r'\(GXMetaInfo<[^>]*>\s*\*\)\s*&?(\w+)')
POINTER = (r'(?:\(\w+\)\s*)?param_1\s*\+\s*(?:0x[0-9a-fA-F]+|\d+)'
           r'|&\(?param_1\)?->[\w.]+|&\(param_1->\w+\)[\w.]*')
PTR_ASSIGN = re.compile(rf'(\w+)\._8_8_\s*=\s*({POINTER})')
TYPE_ASSIGN = re.compile(r'(\w+)\._0_8_\s*=\s*([\w:<>, ]+?)::vftable')
ENUM_ARG = re.compile(rf'{WSTR}\s*,\s*{WSTR}\s*,\s*({POINTER})\s*,')
SIGNATURE = re.compile(r'\w[\w\s*:<>]*?\(\s*([\w:<>]+(?:\s*\*+)?)\s*param_1')
SCALE = {'undefined8': 8, 'longlong': 8, 'ulonglong': 8, 'undefined4': 4, 'int': 4, 'uint': 4,
         'float': 4, 'undefined2': 2, 'short': 2, 'ushort': 2}


class Image:
    def __init__(self, path):
        self.data = open(path, 'rb').read() if path and os.path.exists(path) else b''

    def wstring(self, va):
        off = va - IMAGE_BASE
        if not self.data or not 0 <= off < len(self.data):
            return f'<DAT_{va:x}>'
        end = off
        while end + 1 < len(self.data) and self.data[end:end + 2] != b'\x00\x00':
            end += 2
        return self.data[off:end].decode('utf-16le', 'replace')


def label(token, image):
    if token.startswith('L"'):
        return token[2:-1]
    return image.wstring(int(token.split('_')[-1], 16))


def struct_layout(name, port):
    """Field-name offsets and total size of a Ghidra structure, or ({}, None)."""
    result = query('getStructure', {'name': name}, port=port).get('result')
    if not isinstance(result, dict):
        return {}, None
    fields = {f['fieldName']: f['offset'] for f in result.get('fields', []) if f['fieldName']}
    return fields, result.get('size')


class Resolver:
    def __init__(self, text, port):
        sig = SIGNATURE.search(text)
        ptype = ' '.join(sig.group(1).split()) if sig else 'longlong'
        base = ptype.rstrip('* ').split('::')[-1]
        self.scale = SCALE.get(base, 1) if ptype.endswith('*') else 1
        self.struct = {}
        if ptype.endswith('*') and base not in SCALE and not base.startswith('undefined'):
            # `param_1 + n` on a typed pointer is scaled by the structure's size.
            self.struct, size = struct_layout(base, port)
            self.scale = size or 1
        self.ptype = ptype

    def offset(self, spelling):
        spelling = ' '.join(spelling.split())
        m = re.search(r'param_1\s*\+\s*(0x[0-9a-fA-F]+|\d+)', spelling)
        if m:
            cast = re.match(r'\((\w+)\)', spelling)
            return int(m.group(1), 0) * (1 if cast else self.scale)
        m = re.search(r'->(\w+)', spelling)
        if m:
            field = m.group(1)
            if field in self.struct:
                return self.struct[field]
            tail = re.search(r'_0x([0-9a-fA-F]+)$', field)
            if tail:
                return int(tail.group(1), 16)
        return None


def decompile(address, port):
    result = query('getDecompiledCode', {'address': address.lower().removeprefix('0x')},
                   port=port).get('result')
    if not isinstance(result, str):
        raise SystemExit(f'{address}: no decompilation ({result!r})')
    return result


def rows(text, port, image):
    resolve = Resolver(text, port)
    pointer, meta_type, group = {}, {}, None
    for statement in text.split(';'):
        # Join continuation lines only, so a double space inside a label survives.
        flat = re.sub(r'\s*\n\s*', ' ', statement).strip()
        for local, spelling in PTR_ASSIGN.findall(flat):
            pointer[local] = resolve.offset(spelling)
        for local, kind in TYPE_ASSIGN.findall(flat):
            if 'GXMetaInfoParam' not in kind:
                meta_type[local] = kind.strip()
        start = GROUP_CALL.search(flat)
        if start:
            group = label(start.group(1), image)
            yield ('group', group, label(start.group(2), image))
            continue
        pair = WSTR_PAIR.search(flat)
        if not pair:
            continue
        name2, name1 = label(pair.group(1), image), label(pair.group(2), image)
        meta = META_ARG.search(flat)
        if meta:
            local = meta.group(1)
            yield ('field', group, name2, name1, pointer.get(local), meta_type.get(local))
            continue
        enum = ENUM_ARG.search(flat)
        if enum:
            yield ('field', group, name2, name1, resolve.offset(enum.group(1)), 'enum')


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('addresses', nargs='+', help='registration function entry VAs')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--image', help='flat image for &DAT_ labels (default by port)')
    args = parser.parse_args()
    image = Image(args.image or os.path.join(REPO, DEFAULT_IMAGE.get(args.port, '')))
    for address in args.addresses:
        text = decompile(address, args.port)
        print(f'# {address} (port {args.port}) param_1: {Resolver(text, args.port).ptype}')
        for row in rows(text, args.port, image):
            if row[0] == 'group':
                print(f'GROUP {row[1]!r} (editor {row[2]!r})')
            else:
                _, group, name2, name1, offset, kind = row
                off = f'0x{offset:x}' if offset is not None else '?'
                print(f'  {group} | {name2} | {name1} | {off} | {kind}')


if __name__ == '__main__':
    main()
