#!/usr/bin/env python3
"""Per-block walkable extents from an arena-extents.js Frida log.

Each block id the player stood in gets: the longest horizontal distance between any two points
walked (the convex hull diameter on the x/z plane), the axis-aligned x and z spans, and the
height range, which is where ramps and tiers show up. Walk the edges of an arena and the
diameter is its longest diagonal, whatever the shape.

    python3 scripts/arena-extents.py <watch log>
    python3 scripts/arena-extents.py --selftest
"""

from __future__ import annotations

import argparse
import json
import math
import sys


def hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    upper: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def diameter(points: list[tuple[float, float]]) -> tuple[float, tuple, tuple]:
    h = hull(points)
    best = (0.0, h[0] if h else None, h[0] if h else None)
    for i, a in enumerate(h):
        for b in h[i + 1:]:
            d = math.dist(a, b)
            if d > best[0]:
                best = (d, a, b)
    return best


def read_log(path: str, jump: float = 10.0, since: float = 0.0) -> dict[str, list[tuple[float, float, float]]]:
    """Points grouped into segments, one per stay in a block. A jump of more than `jump` metres
    between two samples inside a block is stitched out rather than split on. Several arenas can
    share one block id (two in 0x2e000000, 2026-10-21), so restart the measurement with `since`
    when moving to another arena in the same block."""
    segments: dict[str, list[tuple[float, float, float]]] = {}
    key, last, raw_last, n = None, None, None, 0
    offset = (0.0, 0.0, 0.0)
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                record = json.loads(line)
                payload = record["message"].get("payload") or {}
            except (ValueError, KeyError, AttributeError):
                continue
            if payload.get("kind") != "pt" or record.get("at", 0.0) < since:
                continue
            raw = tuple(payload["pos"])
            if key is None or not key.startswith(payload["block"] + "#"):
                n += 1
                key = f"{payload['block']}#{n}"
                offset = (0.0, 0.0, 0.0)
            elif math.dist(raw_last, raw) > jump:
                # The physics position re-bases by about 32 m mid-walk with no move behind it
                # (2026-10-21: x 32.01 -> 0.16 in 0.07 s, y and z unchanged). Carry the walk on
                # from the last stitched point instead of counting it as a warp.
                offset = tuple(l - r for l, r in zip(last, raw))
            pos = tuple(r + o for r, o in zip(raw, offset))
            segments.setdefault(key, []).append(pos)
            last, raw_last = pos, raw
    return segments


def report(blocks: dict[str, list[tuple[float, float, float]]]) -> list[dict]:
    rows = []
    for block, pts in blocks.items():
        xz = [(p[0], p[2]) for p in pts]
        d, a, b = diameter(xz)
        rows.append({
            "segment": block,
            "start": [round(v, 1) for v in pts[0]],
            "points": len(pts),
            "diagonal_m": round(d, 2),
            "x_span_m": round(max(p[0] for p in pts) - min(p[0] for p in pts), 2),
            "z_span_m": round(max(p[2] for p in pts) - min(p[2] for p in pts), 2),
            "y_min": round(min(p[1] for p in pts), 2),
            "y_max": round(max(p[1] for p in pts), 2),
            "diagonal_ends": [a, b],
        })
    return rows


def selftest() -> int:
    square = [(x, 0.0, z) for x in (0.0, 10.0) for z in (0.0, 10.0)] + [(5.0, 2.0, 5.0)]
    circle = [(20 * math.cos(t / 50 * math.tau), 0.0, 20 * math.sin(t / 50 * math.tau)) for t in range(50)]
    rows = {r["segment"]: r for r in report({"sq": square, "ci": circle})}
    ok = (
        abs(rows["sq"]["diagonal_m"] - math.hypot(10, 10)) < 0.01
        and rows["sq"]["y_max"] == 2.0
        and abs(rows["ci"]["diagonal_m"] - 40.0) < 0.1
    )
    print("selftest", "ok" if ok else "FAILED", rows["sq"]["diagonal_m"], rows["ci"]["diagonal_m"])
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log", nargs="?")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--since", type=float, default=0.0, help="ignore points logged before this epoch time")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.log:
        ap.error("log path required")
    for row in report(read_log(args.log, since=args.since)):
        print(json.dumps(row))
    return 0


if __name__ == "__main__":
    sys.exit(main())
