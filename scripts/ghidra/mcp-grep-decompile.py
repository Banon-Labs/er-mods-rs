#!/usr/bin/env python3
"""Decompile every function in an address range through the Ghidra MCP daemon and grep it.

Answers "which function in this module touches field X" when the field has no data xrefs
(struct members never do). Walks function entries with `getFunctionByAddress`, so it only
visits functions Ghidra already knows, and prints each matching line under its function.

    python3 scripts/ghidra/mcp-grep-decompile.py 140407000 140408700 'releasedActions'
    python3 scripts/ghidra/mcp-grep-decompile.py --port 8767 14074a000 14074b000 'FUN_'
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcp_query import query  # noqa: E402

#: Function starts are 16-byte aligned in this image; a step this size never skips one.
ALIGN = 0x10


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('lo', help='first address (hex)')
    ap.add_argument('hi', help='end address, exclusive (hex)')
    ap.add_argument('pattern', help='regex matched against each decompiled line')
    ap.add_argument('--port', type=int, default=8765)
    a = ap.parse_args()
    lo, hi, pat = int(a.lo, 16), int(a.hi, 16), re.compile(a.pattern)
    seen, addr = set(), lo
    while addr < hi:
        r = query('getFunctionByAddress', {'address': '%x' % addr}, port=a.port,
                  timeout=20).get('result')
        if not isinstance(r, dict) or 'entry' not in r:
            addr += ALIGN
            continue
        entry, end = int(r['entry'], 16), int(r['bodyEnd'], 16)
        if entry not in seen:
            seen.add(entry)
            code = query('getDecompiledCode', {'address': '%x' % entry}, port=a.port,
                         timeout=25).get('result')
            lines = [ln.strip() for ln in (code or '').splitlines() if pat.search(ln)] \
                if isinstance(code, str) else []
            if lines:
                print('==', r['name'], hex(entry))
                for ln in lines:
                    print('   ', ln)
        addr = (max(end + 1, addr + 1) + ALIGN - 1) & ~(ALIGN - 1)
    return 0


if __name__ == '__main__':
    sys.exit(main())
