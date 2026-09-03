#!/usr/bin/env python3
"""Compact getFunctionByAddress summary from a Ghidra MCP daemon.

Usage: re117_fn.py <port> <addr> [<port> <addr> ...]
"""
import sys, os, json, subprocess
HERE = os.path.dirname(os.path.abspath(__file__))
Q = os.path.join(HERE, "ghidra", "mcp_query.py")
args = sys.argv[1:]
for i in range(0, len(args), 2):
    port, addr = args[i], args[i + 1]
    p = subprocess.run([sys.executable, Q, "getFunctionByAddress",
                        "--params", json.dumps({"address": addr}), "--port", port],
                       capture_output=True, text=True, timeout=25)
    try:
        d = json.loads(p.stdout)
    except Exception:
        print(f":{port} {addr}  RAW={p.stdout.strip()[:160]!r} ERR={p.stderr.strip()[:160]!r}")
        continue
    r = d.get("result")
    if not r:
        print(f":{port} {addr}  -> {d}")
        continue
    print(f":{port} {addr}  entry={r['entry']} size={r['size']} "
          f"callers={len(r.get('callers') or [])} callees={len(r.get('callees') or [])} "
          f"name={r['name']}")
