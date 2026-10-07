#!/usr/bin/env python3
"""Rest at a grace without a grace: respawn dead enemies, restart map events, refill flasks.

Calls the AI lab's graceRest RPC (scripts/frida/spawn-npc.js), which runs the game's own
ResetWorld() on the frame thread, then BonfirelikeRecovery() and the chest refill. The lab
(scripts/er-ai-lab.py) must be attached. Recipe: bd grace-rest-recipe-1171-2026-10-06.

Event flags a restarted event would read can be cleared first, so an encounter that finished
for good comes back. --preset names a known set; --clear takes raw ids.

    python3 scripts/er-reset-world.py
    python3 scripts/er-reset-world.py --preset great-jar
    python3 scripts/er-reset-world.py --clear 1047419201 1047412350
"""

import argparse
import json
import pathlib
import select
import subprocess
import sys
import time
import urllib.request

RPC = "http://127.0.0.1:8770/api/rpc"
LOG = pathlib.Path.home() / ".cache/er-frida/ai-lab.jsonl"

# Flags to clear before the reset, and flags to read afterwards as proof.
PRESETS = {
    # North Caelid Great Jar: three Knights of the Great Jar as invader signs. 1047419200 (trial
    # accepted) stays set; 1047412350 turning 1 after the reset means the signs were placed.
    "great-jar": {
        "clear": [1047419201, 1047412350, 1047412220, 1047412221, 1047412222,
                  1047410230, 1047410231, 1047410232],
        "check": [1047419200, 1047419201, 1047412350],
        "expect": {1047412350: 1},
    },
}


def rpc(method, *args):
    body = json.dumps({"agent": "spawn", "method": method, "args": list(args)}).encode()
    req = urllib.request.Request(RPC, body, {"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        out = json.load(r)
    if not out.get("ok"):
        sys.exit(f"{method} failed: {out}")
    return out["out"]


def log_size():
    return LOG.stat().st_size if LOG.exists() else 0


def lab_events(offset, deadline):
    """The lab's log events written after `offset`, as they arrive, until `deadline`.

    `tail -f` follows the file and `select` returns the moment a line lands, so the wait ends on
    an event rather than on a polling interval.
    """
    tail = subprocess.Popen(["tail", "-c", f"+{offset + 1}", "-f", str(LOG)],
                            stdout=subprocess.PIPE, text=True)
    try:
        while (left := deadline - time.time()) > 0:
            if not select.select([tail.stdout], [], [], left)[0]:
                return
            line = tail.stdout.readline()
            if not line:
                return
            try:
                yield json.loads(line)
            except ValueError:
                continue
    finally:
        tail.kill()
        tail.wait()


def wait_for_reset(offset, deadline):
    states = []
    for ev in lab_events(offset, deadline):
        kind = ev.get("kind")
        if kind == "grace-rest" and not ev.get("ok"):
            return f"refused: {ev.get('why')} {ev.get('state', '')}".strip(), states
        if kind == "hook-error" and str(ev.get("where", "")).startswith("grace"):
            return f"error: {ev.get('error')}", states
        if kind == "grace-rest-state":
            states.append(ev["state"])
            if ev["state"] == 0:
                return "done", states
    return "timed out", states


def read_flags(ids):
    return {k: v[1] for k, v in rpc("eventFlags", ids, None).items()}


def wait_for_flags(check, expect, offset, deadline):
    """Re-read the proof flags on each lab event until `expect` holds or `deadline` passes.

    The restarted map events set their flags on later frames, so a single read taken the moment
    the reset finishes reports them unset.
    """
    flags = read_flags(check)
    for _ in lab_events(offset, deadline):
        if all(flags.get(str(k)) == v for k, v in expect.items()):
            break
        flags = read_flags(check)
    return flags


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--preset", choices=sorted(PRESETS))
    ap.add_argument("--clear", type=int, nargs="*", default=[], help="event flag ids to set to 0")
    ap.add_argument("--timeout", type=float, default=10.0)
    a = ap.parse_args()

    preset = PRESETS.get(a.preset, {"clear": [], "check": [], "expect": {}})
    clear = preset["clear"] + a.clear
    if clear:
        changed = {k: v for k, v in rpc("eventFlags", clear, 0).items() if v[0] != v[1]}
        print(f"cleared {len(clear)} flags, changed: {changed or 'none'}")

    offset = log_size()
    rpc("graceRest")
    verdict, states = wait_for_reset(offset, time.time() + a.timeout)
    print(f"reset: {verdict}, respawn states {states}")
    if verdict != "done":
        sys.exit(1)

    if preset["check"]:
        flags = wait_for_flags(preset["check"], preset["expect"], log_size(),
                               time.time() + a.timeout)
        print("flags:", flags)


if __name__ == "__main__":
    main()
