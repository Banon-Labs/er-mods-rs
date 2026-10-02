#!/usr/bin/env python3
"""Compare `er-mechanics-exchange.py --rank <mode>` rankings and collect the stamina table.

    python3 scripts/er-exchange-stamina-report.py <dir> [--out <dir>/rank-stamina.json]

`<dir>` holds `rank-<mode>.txt` (the printed ranking, run with `--top 400`) and
`rank-<mode>.json` (`--dump`) for the modes `er-mechanics-exchange.RANK_MODES` names; missing ones
are skipped. It prints each tracked weapon's rank and score per mode and writes one JSON with the
rankings, the rank changes and the per-slot stamina numbers (docs/er-mechanics/exchange.md
section 3)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROW = re.compile(r"^  (\S.*?)\s+(1H|2H)\s+(.+?)\s+(\S+)\s+(-?\d+)\s+(-?\d+)\s")
TRACKED = ("Giant-Crusher", "Greatsword", "Hand Axe", "Claymore", "Lance")
MODES = ("none", "stamina-swings", "stamina", "stamina-w5", "stamina-w20", "all-swings", "all")


def ranking(path: Path) -> list[dict]:
    """[{weapon, grip, build, slot, score}] in printed order."""
    out, on = [], False
    for line in path.read_text().splitlines():
        if line.startswith("ranked by moveset score"):
            on = True
            continue
        m = ROW.match(line) if on else None
        if m and m.group(1) != "weapon":
            out.append({"weapon": m.group(1).strip(), "grip": m.group(2), "build": m.group(3).strip(),
                        "slot": m.group(4), "score": int(m.group(5))})
    return out


def rank_of(rows: list[dict], weapon: str, grip: str | None = None) -> tuple[int, dict] | None:
    for i, r in enumerate(rows, 1):
        if r["weapon"] == weapon and (grip is None or r["grip"] == grip):
            return i, r
    return None


def selftest() -> int:
    line = "  Hand Axe                         2H     Heavy+lightning  r1_1             1324   1324    0 0.946"
    m = ROW.match(line)
    ok = bool(m) and m.group(1).strip() == "Hand Axe" and m.group(5) == "1324" and m.group(4) == "r1_1"
    print(("ok   " if ok else "FAIL ") + "parses a ranking row")
    return 0 if ok else 1


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir", type=Path)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    ranks = {m: ranking(a.dir / f"rank-{m}.txt") for m in MODES if (a.dir / f"rank-{m}.txt").exists()}
    print(f"{'weapon':<16}" + "".join(f"{m:>18}" for m in ranks))
    changes = {}
    for w in TRACKED:
        cells, changes[w] = [], {}
        for m, rows in ranks.items():
            got = rank_of(rows, w, "2H")
            cells.append(f"{got[0]:>4} {got[1]['slot']:<7}{got[1]['score']:>5}" if got else f"{'-':>18}")
            changes[w][m] = {"rank": got[0], **got[1]} if got else None
        print(f"{w:<16}" + "".join(f"{c:>18}" for c in cells))
    for m, rows in ranks.items():
        print(f"\ntop 30, {m}: " + ", ".join(f"{r['weapon']} {r['grip']} {r['slot']} {r['score']}" for r in rows[:30]))
    dumps = {}
    for m in MODES:
        p = a.dir / f"rank-{m}.json"
        if p.exists():
            dumps[m] = json.loads(p.read_text())
    slots = next(iter(dumps.values()))["slots"] if dumps else []
    out = {"tracked_2h": changes, "rankings": ranks, "slots": slots,
           "meta": {k: v for k, v in (next(iter(dumps.values())) if dumps else {}).items() if k != "slots"}}
    if a.out:
        a.out.write_text(json.dumps(out, indent=1))
        print("\nwrote", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
