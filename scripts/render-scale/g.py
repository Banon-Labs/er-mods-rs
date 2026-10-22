#!/usr/bin/env python3
"""Query a Ghidra MCP daemon and print the result.

Usage: g.py <port> <method> [<json params>] [<max chars, 0 = all>]
An xref listing (`getXrefsTo` / `getXrefsFrom`) prints one line per reference instead of JSON.
"""
import json
import sys

sys.path.insert(0, "/home/banon/projects/er-mods-rs/scripts/ghidra")
from mcp_query import query  # noqa: E402


def q(method, port=8765, **params):
    return query(method, params, port=port, timeout=25)


if __name__ == "__main__":
    port = int(sys.argv[1])
    method = sys.argv[2]
    params = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
    limit = int(sys.argv[4]) if len(sys.argv) > 4 else 20000
    r = q(method, port, **params)
    res = r.get("result", r)
    if method.startswith("getXrefs") and isinstance(res, dict) and "items" in res:
        print("total", res.get("totalCount"))
        for it in res["items"]:
            print(it.get("fromFunction"), it.get("fromAddress"), "->", it.get("toAddress"),
                  it.get("toFunction", ""), it.get("refType"))
        sys.exit(0)
    text = json.dumps(res, indent=1) if isinstance(res, (dict, list)) else str(res)
    print(text if limit == 0 else text[:limit])
