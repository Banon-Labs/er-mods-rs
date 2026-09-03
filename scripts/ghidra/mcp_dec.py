#!/usr/bin/env python3
"""Print a decompiled function (or any string-returning MCP result) from the headless Ghidra
MCP daemon in readable form. Companion to mcp_query.py, which JSON-escapes the body.

Usage:
  python3 scripts/ghidra/mcp_dec.py <address-or-name> [--port 8765]
  python3 scripts/ghidra/mcp_dec.py --method <camelCaseMethod> --params '{...}' [--port 8765]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcp_query import query  # noqa: E402


def _emit(res):
    if isinstance(res, str):
        print(res)
    elif isinstance(res, dict):
        for k, v in res.items():
            if isinstance(v, str) and "\n" in v:
                print(f"--- {k} ---")
                print(v)
            else:
                print(f"{k}: {v}")
    else:
        print(json.dumps(res, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", nargs="?", help="function address (0x...) or name")
    ap.add_argument("--method", help="explicit MCP method instead of a decompile")
    ap.add_argument("--params", default="{}")
    ap.add_argument("--port", type=int, default=int(os.environ.get("GPORT", "8765")))
    a = ap.parse_args()
    if a.method:
        method, params = a.method, json.loads(a.params)
    elif a.target:
        if a.target.lower().startswith("0x"):
            method, params = "getDecompiledCode", {"address": a.target}
        else:
            method, params = "decompileFunctionByName", {"name": a.target}
    else:
        ap.error("need a target or --method")
    r = query(method, params, port=a.port)
    if "error" in r and r.get("error"):
        print(json.dumps(r["error"], indent=2), file=sys.stderr)
        return 1
    _emit(r.get("result", r))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
