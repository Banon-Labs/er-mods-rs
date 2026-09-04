#!/usr/bin/env python3
"""Sweep crates/ for struct-field offsets that a raw inline `+0x..` scanner cannot see.

Three shapes:
  (a) named constants  -- `const FOO_OFFSET: usize = 0x..;`
  (b) #[repr(C)] structs mirroring game types -- offsets implied by declaration order
  (c) offsets passed as an argument to a read helper -- `read_field(ptr, OFF)`

Writes JSON artifacts to the path given by --out (default /tmp).
"""
import re, os, json, argparse

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "crates")

DECL = re.compile(
    r'^\s*(?:pub(?:\([^)]*\))?\s+)?(const|static)\s+([A-Z][A-Z0-9_]*)\s*:\s*'
    r'(usize|u64|u32|u16|u8|isize|i64|i32|i16)\s*=\s*(0x[0-9a-fA-F_]+|\d+)\s*;')

def sweep_named(maxval=0x20000):
    rows = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in ('target', '.git')]
        for fn in filenames:
            if not fn.endswith('.rs'):
                continue
            p = os.path.join(dirpath, fn)
            lines = open(p, encoding='utf-8', errors='replace').read().split('\n')
            for i, l in enumerate(lines, 1):
                m = DECL.match(l)
                if not m:
                    continue
                _kind, name, ty, val = m.groups()
                v = int(val.replace('_', ''), 16) if val.lower().startswith('0x') else int(val)
                if v == 0 or v > maxval:
                    continue
                rows.append(dict(file=p, line=i, name=name, ty=ty, val=v, raw=val, text=l.strip()))
    return rows

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='/tmp')
    a = ap.parse_args()
    rows = sweep_named()
    json.dump(rows, open(os.path.join(a.out, 'named_all.json'), 'w'), indent=0)
    print(f"named-constant candidates: {len(rows)}")

if __name__ == '__main__':
    main()
