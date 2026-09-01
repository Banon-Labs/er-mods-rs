#!/usr/bin/env python3
"""Thin CLI over the Ghidra MCP daemon: `gq.py <method> key=value ...`.

Complements scripts/ghidra/mcp_query.py, which requires a single --params JSON blob.
This form accepts flat key=value pairs and unwraps the common string payload keys
(decompiled_code / disassembly / ...) so decompiler output is readable in a terminal.
Port defaults to 8765 (1.16.2); set GPORT=8767 for the 1.17 dump.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcp_query import query


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    method = sys.argv[1]
    port = int(os.environ.get("GPORT", "8765"))
    params = {}
    for kv in sys.argv[2:]:
        k, _, v = kv.partition("=")
        if v.lstrip("-").isdigit():
            v = int(v)
        params[k] = v
    res = query(method, params, port=port)
    res = res.get("result", res)
    if isinstance(res, dict):
        for k in ("decompiled_code", "decompilation", "code", "disassembly", "text"):
            if isinstance(res.get(k), str):
                print(res[k])
                return
    print(json.dumps(res, indent=2))


main()
