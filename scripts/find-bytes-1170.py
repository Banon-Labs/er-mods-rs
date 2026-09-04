#!/usr/bin/env python3
"""Search the 1.16.2 and/or 1.17 de-Arxan'd images for a hex byte pattern (?? = wildcard).

Used to confirm a struct-field displacement across versions when the RVA mapper cannot
anchor a short accessor: search for the accessor's byte shape with the displacement byte
wildcarded, then read what displacement the 1.17 copy actually carries.
"""
import sys, os, re, argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGES = {
    '1162': os.path.join(ROOT, 'eldenring-deobf.bin'),
    '1170': os.path.join(ROOT, 'eldenring-deobf-1.17.bin'),
}
BASE = 0x140000000

def compile_pattern(hexstr):
    toks = hexstr.split()
    parts = []
    for t in toks:
        if t == '??':
            parts.append(b'.')
        else:
            parts.append(re.escape(bytes([int(t, 16)])))
    return re.compile(b''.join(parts), re.S)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pattern', help='hex bytes, ?? for wildcard, e.g. "48 81 c1 ?? 07 00 00 b2 01"')
    ap.add_argument('--image', choices=['1162', '1170', 'both'], default='both')
    ap.add_argument('--context', type=lambda s: int(s, 0), default=0, help='extra bytes to print')
    ap.add_argument('--limit', type=int, default=20)
    a = ap.parse_args()
    rx = compile_pattern(a.pattern)
    names = ['1162', '1170'] if a.image == 'both' else [a.image]
    n = len(a.pattern.split())
    for nm in names:
        data = open(IMAGES[nm], 'rb').read()
        hits = 0
        for m in rx.finditer(data):
            hits += 1
            if hits > a.limit:
                print(f'  {nm}: ...more than {a.limit} hits')
                break
            va = BASE + m.start()
            blob = data[m.start():m.start() + n + a.context]
            print(f'  {nm}  0x{va:x}  {blob.hex(" ")}')
        if hits == 0:
            print(f'  {nm}: no match')
        else:
            print(f'  {nm}: {min(hits, a.limit + 1)} hit(s) shown, {hits} total')

if __name__ == '__main__':
    main()
