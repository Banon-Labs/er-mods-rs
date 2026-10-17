#!/usr/bin/env python3
"""Do MSB placements of a set of asset models join into bigger structures, or stand alone?

Every placement of the named models is put into world space and every pair whose bounds touch is
measured: the share of each side's vertices that lands within 2 cm of the other's (a shared edge
when pieces were authored to meet), the share within 0.5 m (mere contact or interpenetration),
and whether their relative rotation is a 90-degree step. Placements are then grouped twice: by
touching bounds, and by real vertex coincidence.

World frame. m60 MSB positions are local to the tile centre. A level-L tile `m60_XX_YY_LL` is
`256 * 2**L` metres wide and centred at `size * XX + (size - 256) / 2` on x (and YY on z).
Measured 2026-10-17 on the AEG091 impostors in `m60_10_09_02`: every `m60_XX_YY_00-AEG091_*`
part sits at exactly the local position this predicts. The `_1L` blocks are checked against
their `_0L` twin and dropped when every transform matches. Other maps keep their own frame,
keyed by map name.

Rotation. MSB rotations are Euler degrees, but the order is not pinned anywhere in this repo
(`scripts/blender/assemble-msb-building.py` only proved the yaw sign). So all six orders are
tried, each pair is scored under each, and the order with the most coincident vertices across
all pairs is reported along with the per-order totals. If nothing joins, the order cannot be
told apart and does not matter for the verdict.

Usage:
  er-asset-join-check.py --placements <model\\tmsb\\tpart tsv> --verts <dir of MODEL.txt>
      --models AEG007_076,AEG007_077 [--out <report.json>]

`MODEL.txt` is `cargo run -p er-flver --example flver-positions -- <MODEL.flver>`.
"""

import argparse
import glob
import itertools
import json
import math
import os
import re
from collections import defaultdict

import numpy as np

MAPSTUDIO = os.path.expanduser("~/er-extract/LOOK_HERE_WITCHY_RECURSIVE_20260713/sharded/map/mapstudio")
COINCIDE = 0.02
CONTACT = 0.5


def read_part(msb, part):
    hits = glob.glob(os.path.join(MAPSTUDIO, "_chunk_*", f"{msb}-msb-dcx", "Part", "Asset", f"{part}.xml"))
    if not hits:
        return None
    text = open(hits[0], encoding="utf-8-sig", errors="replace").read()

    def vec(tag):
        m = re.search(rf"<{tag}>\s*<X>([^<]+)</X>\s*<Y>([^<]+)</Y>\s*<Z>([^<]+)</Z>", text)
        return tuple(float(v) for v in m.groups())

    return vec("Position"), vec("Rotation"), vec("Scale")


def world_key(msb, pos):
    m = re.match(r"m60_(\d+)_(\d+)_(\d)(\d)$", msb)
    if not m:
        return msb.rsplit("_", 1)[0] if msb.endswith("_99") else msb, pos
    xx, yy, lod, level = int(m[1]), int(m[2]), int(m[3]), int(m[4])
    size = 256.0 * 2**level
    off = (size - 256.0) / 2.0
    return f"m60{'_lod' if lod else ''}", (pos[0] + size * xx + off, pos[1], pos[2] + size * yy + off)


def rot(axis, deg):
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    if axis == "X":
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
    if axis == "Y":
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def matrix(order, r):
    """`order` is the order the axes are applied in, so "XZY" means X first and Y last."""
    angles = dict(zip("XYZ", r))
    m = np.eye(3)
    for axis in order:
        m = rot(axis, angles[axis]) @ m
    return m


def load_verts(path):
    v = np.loadtxt(path, dtype=np.float64).reshape(-1, 3)
    return np.unique(np.round(v, 2), axis=0)


class CellIndex:
    """Points bucketed by 2 cm cell; `near` answers "is any point within `COINCIDE`"."""

    def __init__(self, pts, cell):
        self.cell = cell
        self.pts = pts
        q = np.floor(pts / cell).astype(np.int64)
        self.keys = self._pack(q)
        self.order = np.argsort(self.keys, kind="stable")
        self.sorted = self.keys[self.order]

    @staticmethod
    def _pack(q):
        q = q + (1 << 20)
        return (q[:, 0] << 42) | (q[:, 1] << 21) | q[:, 2]

    def near(self, pts, radius):
        q = np.floor(pts / self.cell).astype(np.int64)
        hit = np.zeros(len(pts), dtype=bool)
        reach = int(math.ceil(radius / self.cell))
        for d in itertools.product(range(-reach, reach + 1), repeat=3):
            k = self._pack(q + np.array(d))
            i = np.searchsorted(self.sorted, k)
            i = np.minimum(i, len(self.sorted) - 1)
            ok = (self.sorted[i] == k) & ~hit
            if not ok.any():
                continue
            # A cell can hold several points; walk the run of equal keys.
            idx = np.nonzero(ok)[0]
            start = i[idx]
            while len(idx):
                cand = self.pts[self.order[start]]
                close = np.linalg.norm(cand - pts[idx], axis=1) <= radius
                hit[idx[close]] = True
                start = start + 1
                keep = (~close) & (start < len(self.sorted))
                keep[keep] &= self.sorted[start[keep]] == k[idx[keep]]
                idx, start = idx[keep], start[keep]
        return hit


def right_angle_step(ra, rb):
    """Relative rotation is a signed axis permutation, i.e. a whole number of 90-degree turns."""
    rel = ra.T @ rb
    return bool(np.all(np.isclose(np.abs(rel), np.round(np.abs(rel)), atol=math.sin(math.radians(1)))))


def components(nodes, edges):
    parent = {n: n for n in nodes}

    def find(n):
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    for a, b in edges:
        parent[find(a)] = find(b)
    groups = defaultdict(list)
    for n in nodes:
        groups[find(n)].append(n)
    return sorted(groups.values(), key=len, reverse=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--placements", required=True)
    ap.add_argument("--verts", required=True)
    ap.add_argument("--models", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()
    models = set(args.models.split(","))

    raw = []
    for line in open(args.placements):
        model, msb, part = line.rstrip("\n").split("\t")
        if model in models:
            t = read_part(msb, part)
            if t:
                raw.append((model, msb, part) + t)

    # A `_1L` block that repeats its `_0L` twin exactly is a copy, not more placements.
    by_block = defaultdict(dict)
    for model, msb, part, pos, r, s in raw:
        by_block[msb][part] = (pos, r, s)
    dup_report = {}
    drop = set()
    for msb, parts in by_block.items():
        m = re.match(r"(m60_\d+_\d+_)1(\d)$", msb)
        if not m:
            continue
        twin = by_block.get(f"{m[1]}0{m[2]}", {})
        same = all(p in twin and twin[p] == v for p, v in parts.items())
        dup_report[msb] = {"parts": len(parts), "identical_to_twin": same}
        if same:
            drop.add(msb)

    verts = {m: load_verts(os.path.join(args.verts, f"{m}.txt")) for m in models}
    place = []
    for model, msb, part, pos, r, s in raw:
        if msb in drop:
            continue
        world, wpos = world_key(msb, pos)
        place.append({"id": f"{msb}/{part}", "model": model, "msb": msb, "world": world,
                      "pos": np.array(wpos), "rot": r, "scale": np.array(s)})

    orders = ["".join(p) for p in itertools.permutations("XYZ")]
    for p in place:
        p["R"] = {o: matrix(o, p["rot"]) for o in orders}

    def world_pts(p, order):
        return (verts[p["model"]] * p["scale"]) @ p["R"][order].T + p["pos"]

    # Candidate pairs from a generous bounding sphere, then exact per-order bounds.
    radius = {m: float(np.max(np.linalg.norm(v, axis=1))) for m, v in verts.items()}
    pairs = []
    for a, b in itertools.combinations(range(len(place)), 2):
        pa, pb = place[a], place[b]
        if pa["world"] != pb["world"]:
            continue
        reach = radius[pa["model"]] * pa["scale"].max() + radius[pb["model"]] * pb["scale"].max()
        if np.linalg.norm(pa["pos"] - pb["pos"]) <= reach + CONTACT:
            pairs.append((a, b))

    cache = {}

    def pts(i, order):
        key = (i, order)
        if key not in cache:
            cache[key] = world_pts(place[i], order)
        return cache[key]

    results = []
    totals = {o: 0 for o in orders}
    for a, b in pairs:
        row = {"a": place[a]["id"], "b": place[b]["id"], "by_order": {}}
        for o in orders:
            A, B = pts(a, o), pts(b, o)
            lo, hi = np.maximum(A.min(0), B.min(0)), np.minimum(A.max(0), B.max(0))
            if np.any(lo > hi + CONTACT):
                row["by_order"][o] = None
                continue
            ib, ia = CellIndex(B, COINCIDE), CellIndex(A, COINCIDE)
            ca, cb = ib.near(A, COINCIDE), ia.near(B, COINCIDE)
            ib5, ia5 = CellIndex(B, CONTACT), CellIndex(A, CONTACT)
            na, nb = ib5.near(A, CONTACT), ia5.near(B, CONTACT)
            row["by_order"][o] = {
                "coincide_a": int(ca.sum()), "coincide_b": int(cb.sum()),
                "frac_a": float(ca.mean()), "frac_b": float(cb.mean()),
                "contact_frac_a": float(na.mean()), "contact_frac_b": float(nb.mean()),
                "right_angle_step": right_angle_step(place[a]["R"][o], place[b]["R"][o]),
            }
            totals[o] += int(ca.sum() + cb.sum())
        row["dist"] = float(np.linalg.norm(place[a]["pos"] - place[b]["pos"]))
        row["same_transform"] = bool(np.allclose(place[a]["pos"], place[b]["pos"], atol=1e-3)
                                     and np.allclose(place[a]["rot"], place[b]["rot"], atol=1e-3))
        results.append(row)

    best = max(orders, key=lambda o: totals[o])
    touching = [(r["a"], r["b"]) for r in results if r["by_order"].get(best)]
    joined = [(r["a"], r["b"]) for r in results
              if r["by_order"].get(best) and max(r["by_order"][best]["frac_a"], r["by_order"][best]["frac_b"]) >= 0.01]
    ids = [p["id"] for p in place]

    def yaw_only(p):
        return abs(p["rot"][0]) < 0.5 and abs(p["rot"][2]) < 0.5

    report = {
        "placements": len(place),
        "dropped_lod_twins": dup_report,
        "per_model": {m: sum(p["model"] == m for p in place) for m in sorted(models)},
        "yaw_only_placements": {m: sum(p["model"] == m and yaw_only(p) for p in place) for m in sorted(models)},
        "yaw_multiple_of_90": {m: sum(p["model"] == m and yaw_only(p) and abs((p["rot"][1] + 360) % 90) < 0.5 for p in place)
                               for m in sorted(models)},
        "candidate_pairs": len(pairs),
        "coincident_vertex_totals_by_order": totals,
        "best_order": best,
        "touching_groups": [g for g in components(ids, touching) if len(g) > 1],
        "joined_groups": [g for g in components(ids, joined) if len(g) > 1],
        "pairs": sorted(results, key=lambda r: -max((v["frac_a"] + v["frac_b"]) if v else 0 for v in r["by_order"].values())),
        "placement_table": [{"id": p["id"], "model": p["model"], "world": p["world"],
                             "pos": [round(x, 3) for x in p["pos"]], "rot": p["rot"], "scale": list(p["scale"])}
                            for p in place],
    }
    text = json.dumps(report, indent=1)
    if args.out:
        open(args.out, "w").write(text)
    print(json.dumps({k: report[k] for k in ("placements", "per_model", "yaw_only_placements", "yaw_multiple_of_90",
                                              "candidate_pairs", "coincident_vertex_totals_by_order", "best_order")}, indent=1))
    print("dropped lod twins:", {k: v for k, v in dup_report.items()})
    print("touching groups:", len(report["touching_groups"]), [len(g) for g in report["touching_groups"]])
    print("joined groups (>=1% verts within 2 cm):", len(report["joined_groups"]), [len(g) for g in report["joined_groups"]])
    for r in report["pairs"][:25]:
        v = r["by_order"][best]
        if not v:
            continue
        print(f"{r['a']:40} {r['b']:40} d={r['dist']:6.1f} same={r['same_transform']} "
              f"co={v['coincide_a']}/{v['coincide_b']} ({v['frac_a']:.3f}/{v['frac_b']:.3f}) "
              f"contact={v['contact_frac_a']:.2f}/{v['contact_frac_b']:.2f} r90={v['right_angle_step']}")


if __name__ == "__main__":
    main()
