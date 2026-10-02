#!/usr/bin/env python3
"""Build the warp-sweep target list: every flagged point, plus controls, in shuffled order.

Controls are the point of this file. A sweep of suspects alone can only ever confirm them --
it has no way to show the oracle also gets the easy cases right, and a run where everything
reads `stood` is then indistinguishable from a harness that cannot detect a fall at all. So
`inside-collision` points are mixed in, and the order is shuffled, so neither the harness nor
the person watching the screen can tell which kind is loading.

Usage:
    python3 scripts/build-sweep-targets.py [--all] OUT.jsonl TSV [TSV ...]
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import random

CONTROL_SEED = 7
SHUFFLE_SEED = 11


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--all", action="store_true", help="take every point, not just suspects plus controls"
    )
    ap.add_argument("out", metavar="OUT.jsonl")
    ap.add_argument("tsvs", metavar="TSV", nargs="+")
    args = ap.parse_args()
    take_all = args.all
    out_path, tsvs = args.out, args.tsvs
    rows = []
    for path in tsvs:
        entries = list(csv.DictReader(open(path, encoding="utf-8"), delimiter="\t"))
        if take_all:
            # Every point, shuffled. With the offline oracle scored at 3/14 against live
            # outcomes there is nothing left to pre-filter with, so the whole catalog goes in
            # and the shuffle makes any prefix of the run an unbiased sample of it -- a sweep
            # stopped after an hour still says something about the catalog as a whole.
            rows += [(r, r["verdict"], path) for r in entries]
            continue
        bad = [r for r in entries if r["verdict"] == "nothing-below"]
        good = [r for r in entries if r["verdict"] == "inside-collision"]
        rng = random.Random(CONTROL_SEED)
        controls = rng.sample(good, min(max(len(bad) // 3, 3), len(good)))
        rows += [(r, "predicted-bad", path) for r in bad]
        rows += [(r, "predicted-good", path) for r in controls]
    random.Random(SHUFFLE_SEED).shuffle(rows)
    with open(out_path, "w", encoding="utf-8") as fh:
        for r, expected, source in rows:
            fh.write(
                json.dumps(
                    {
                        "map": r["map"],
                        "index": int(r["index"]),
                        "x": float(r["x"]),
                        "y": float(r["y"]),
                        "z": float(r["z"]),
                        "yaw_deg": 0.0,
                        "expected": expected,
                        "source": source.rsplit("/", 1)[-1],
                    }
                )
                + "\n"
            )
    print(out_path, len(rows), dict(collections.Counter(e for _, e, _ in rows)))


if __name__ == "__main__":
    main()
