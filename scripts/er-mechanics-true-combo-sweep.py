#!/usr/bin/env python3
"""List every measured true combo over a set of weapons, one JSON line per combo.

Runs `er-mechanics-frame-advantage.py --json` for each weapon in both grips and keeps the slot
pairs whose `on_break.verdict` is "true" (the next hit lands before the victim can roll out). The
output is the `--combos` input of `er-mechanics-class-roles.py`.

    python3 scripts/er-mechanics-true-combo-sweep.py WEAPONS.txt > combos.jsonl

WEAPONS.txt holds weapon names separated by commas or newlines.
"""
import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
#: One weapon in one grip, measured 2026-10-13 at 1.8-1.9 s (Uchigatana, Greatsword, Rivers of
#: Blood); the cap only stops a hung child.
FRAME_ADVANTAGE_TIMEOUT_S = 30


def combos(job):
    weapon, grip = job
    try:
        proc = subprocess.run([sys.executable, str(HERE / "er-mechanics-frame-advantage.py"), weapon,
                               "--grip", grip, "--json"], capture_output=True, text=True, cwd=HERE.parent,
                              timeout=FRAME_ADVANTAGE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        print(f"{weapon} ({grip}): frame-advantage took over {FRAME_ADVANTAGE_TIMEOUT_S} s", file=sys.stderr)
        return []
    try:
        records = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(f"{weapon} ({grip}): no frame-advantage output", file=sys.stderr)
        return []
    out = []
    for rec in records:
        for slot in rec.get("slots", []):
            for c in slot.get("combos") or []:
                ob = c.get("on_break") or {}
                if ob.get("verdict") == "true":
                    out.append({"weapon": weapon, "grip": grip, "from": slot.get("slot"),
                                "to": c.get("next"), "gap": ob.get("gap"), "escape": ob.get("escape")})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("weapons", type=Path)
    ap.add_argument("--jobs", type=int, default=8)
    a = ap.parse_args()
    names = [w.strip() for w in a.weapons.read_text().replace(",", "\n").splitlines() if w.strip()]
    with ThreadPoolExecutor(a.jobs) as ex:
        for rows in ex.map(combos, [(w, g) for w in names for g in ("one", "both")]):
            for r in rows:
                print(json.dumps(r), flush=True)


if __name__ == "__main__":
    main()
