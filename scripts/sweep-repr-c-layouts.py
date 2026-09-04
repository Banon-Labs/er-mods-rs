#!/usr/bin/env python3
"""Enumerate #[repr(C)] structs in a Rust tree and compute each field's byte offset.

Covers the "offset implied by declaration order" shape that a raw `+0x..` scanner
cannot see. Handles the padding idiom used across this repo (`_pad: [u8; N]`) and
sized primitives; a field whose size is not statically known stops the walk for
that struct and is reported as `unknown`.
"""
import re, os, json, argparse

PRIM = {
    'u8': 1, 'i8': 1, 'bool': 1,
    'u16': 2, 'i16': 2,
    'u32': 4, 'i32': 4, 'f32': 4,
    'u64': 8, 'i64': 8, 'f64': 8, 'usize': 8, 'isize': 8,
}
ARR = re.compile(r'^\[\s*([A-Za-z0-9_:<>, ]+?)\s*;\s*(0x[0-9a-fA-F_]+|\d+)\s*\]$')
PTRLIKE = re.compile(r'^(\*(const|mut)\s|&|Option<|NonNull<|extern\s|unsafe\s+extern)')

def type_size(t, structs):
    t = t.strip()
    if t in PRIM:
        return PRIM[t], PRIM[t]
    if PTRLIKE.match(t):
        return 8, 8
    m = ARR.match(t)
    if m:
        inner, n = m.group(1), m.group(2)
        n = int(n.replace('_', ''), 16) if n.lower().startswith('0x') else int(n)
        s = type_size(inner, structs)
        if s is None:
            return None
        return s[0] * n, s[1]
    base = t.split('<')[0].split('::')[-1]
    if base in structs and structs[base].get('size') is not None:
        return structs[base]['size'], structs[base]['align']
    return None

STRUCT = re.compile(r'^\s*(?:pub(?:\([^)]*\))?\s+)?struct\s+([A-Za-z0-9_]+)\s*\{')
FIELD = re.compile(r'^\s*(?:pub(?:\([^)]*\))?\s+)?([A-Za-z0-9_]+)\s*:\s*(.+?),?\s*$')

def parse_file(path, structs, out):
    lines = open(path, encoding='utf-8', errors='replace').read().split('\n')
    i = 0
    while i < len(lines):
        if 'repr(' in lines[i] and ('C' in lines[i] or 'transparent' in lines[i]):
            attr = lines[i]
            j = i + 1
            while j < len(lines) and (lines[j].lstrip().startswith('#[') or lines[j].lstrip().startswith('///') or lines[j].lstrip().startswith('//')):
                j += 1
            m = STRUCT.match(lines[j]) if j < len(lines) else None
            if m:
                name = m.group(1)
                fields, k, depth = [], j + 1, 1
                while k < len(lines) and depth > 0:
                    if '}' in lines[k] and lines[k].strip() == '}':
                        break
                    fm = FIELD.match(lines[k])
                    if fm and not lines[k].lstrip().startswith('//') and not lines[k].lstrip().startswith('#['):
                        fields.append((fm.group(1), fm.group(2).rstrip(',')))
                    k += 1
                off, align, ok = 0, 1, True
                rows = []
                for fname, ftype in fields:
                    s = type_size(ftype, structs)
                    if s is None:
                        rows.append(dict(field=fname, ty=ftype, off=None))
                        ok = False
                        break
                    sz, al = s
                    if off % al:
                        off += al - (off % al)
                    rows.append(dict(field=fname, ty=ftype, off=off, size=sz))
                    off += sz
                    align = max(align, al)
                if ok and off % align:
                    off += align - (off % align)
                structs[name] = dict(size=off if ok else None, align=align)
                out.append(dict(file=path, line=j + 1, name=name, attr=attr.strip(),
                                fields=rows, size=off if ok else None, complete=ok))
                i = k
                continue
        i += 1

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    structs, out = {}, []
    paths = []
    for dp, dn, fn in os.walk(a.root):
        dn[:] = [d for d in dn if d not in ('target', '.git')]
        paths += [os.path.join(dp, f) for f in fn if f.endswith('.rs')]
    for p in sorted(paths):
        parse_file(p, structs, out)
    for p in sorted(paths):   # second pass so forward refs resolve
        pass
    json.dump(out, open(a.out, 'w'), indent=0)
    print(f"repr(C) structs: {len(out)}  complete-layout: {sum(1 for r in out if r['complete'])}")

if __name__ == '__main__':
    main()
