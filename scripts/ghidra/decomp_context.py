#!/usr/bin/env python3
"""Print only the lines of a decompiled function around a pattern, via the MCP daemon.

    python3 scripts/ghidra/decomp_context.py <address> '<pat1|pat2>' [context] [--port N]

The daemon on :8765 serves the 1.16.2 named dump, so addresses are 1.16.2 VAs.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from mcp_query import query  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("address")
    ap.add_argument("patterns")
    ap.add_argument("context", nargs="?", type=int, default=4)
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    r = query("getDecompiledCode", {"address": a.address}, port=a.port).get("result", "")
    lines = r.splitlines()
    for line in lines[:12]:
        if "(" in line and not line.startswith(" "):
            print(line)
            break
    pats = a.patterns.split("|")
    shown = set()
    for i, line in enumerate(lines):
        if any(p in line for p in pats):
            for j in range(max(0, i - a.context), min(len(lines), i + a.context + 1)):
                if j not in shown:
                    print(j, lines[j])
                    shown.add(j)
            print("--")


if __name__ == "__main__":
    main()
