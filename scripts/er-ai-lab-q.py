#!/usr/bin/env python3
"""Ask the running AI lab (scripts/er-ai-lab.py) a Lua question about one NPC.

    python3 scripts/er-ai-lab-q.py 'return ai:GetDist(TARGET_LOCALPLAYER)'

`ai` is bound to LAB_AI[<think>] (Moongrum by default). _lab.lua empties LAB_AI on every apply,
about every 2 s, so a handle exists only between one of his own AI calls and the next apply; when
the question lands in that gap the answer says so (`__no_handle__`) rather than guessing.
"""

from __future__ import annotations

import argparse
import json
import urllib.request


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("code", help="Lua statements; `ai` is the NPC's handle")
    ap.add_argument("--think", type=int, default=523590100)
    ap.add_argument("--url", default="http://127.0.0.1:8770/api/eval")
    args = ap.parse_args()
    code = (f'local ai = LAB_AI[{args.think}]; if ai == nil then return "__no_handle__" end; '
            + args.code)
    body = json.dumps({"code": code}).encode()
    req = urllib.request.Request(args.url, body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        r = json.loads(resp.read())
    print(json.dumps(r))
    return 0 if r.get("ok") and "__no_handle__" not in str(r.get("out", "")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
