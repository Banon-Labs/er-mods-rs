#!/usr/bin/env python3
"""Do MSB placements sit on a ring (an arc with gaps) rather than scattered?

Input is the `placement_table` of `scripts/er-asset-join-check.py --out`, so the world frame and
the tile-twin handling are that tool's. Everything here works in the horizontal x/z plane.

Three tests, each against a shuffle null so a ring claim carries a p-value:

1. Big ring. Every circle through three of the chosen centres is scored by how many centres lie
   within `max(tol_abs, tol_rel * r)` of it, radius limited to `--rmin..--rmax`. The best count
   is compared with the best count after the same number of points are re-dropped uniformly
   inside the tiles they came from (per-tile counts kept, positions within each 256 m tile
   shuffled), so clustering at tile scale is in the null and only arrangement within tiles is
   tested.
2. Orientation on the best ring. The model's long axis (PCA of its vertices) and its up axis
   are rotated into the world and projected onto x/z, then compared with the ring's tangent and
   radial direction. Null: the same blocks with yaw drawn uniformly.
3. Site rings. For each site (a big block plus the rubble within `--site-radius`), a circle is
   fitted by least squares and its residual-to-radius ratio compared with the same number of
   points dropped uniformly in a disc of the site's extent. A ring has a small ratio, a filled
   disc a large one.

Usage: er-asset-ring-check.py <join-report.json> <verts-dir> [--order XZY] [--nulls 300]
"""

import argparse
import json
import math
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))


def rot(axis, deg):
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    if axis == "X":
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
    if axis == "Y":
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def matrix(order, r):
    angles = dict(zip("XYZ", r))
    m = np.eye(3)
    for axis in order:
        m = rot(axis, angles[axis]) @ m
    return m


def triples_circles(xy, idx):
    a, b, c = xy[idx[:, 0]], xy[idx[:, 1]], xy[idx[:, 2]]
    d = 2 * (a[:, 0] * (b[:, 1] - c[:, 1]) + b[:, 0] * (c[:, 1] - a[:, 1]) + c[:, 0] * (a[:, 1] - b[:, 1]))
    ok = np.abs(d) > 1e-9
    a2, b2, c2 = (a ** 2).sum(1), (b ** 2).sum(1), (c ** 2).sum(1)
    ux = (a2 * (b[:, 1] - c[:, 1]) + b2 * (c[:, 1] - a[:, 1]) + c2 * (a[:, 1] - b[:, 1])) / np.where(ok, d, 1)
    uy = (a2 * (c[:, 0] - b[:, 0]) + b2 * (a[:, 0] - c[:, 0]) + c2 * (b[:, 0] - a[:, 0])) / np.where(ok, d, 1)
    centre = np.stack([ux, uy], 1)
    r = np.linalg.norm(a - centre, axis=1)
    return centre, r, ok


def best_ring(xy, idx, args):
    centre, r, ok = triples_circles(xy, idx)
    ok &= (r >= args.rmin) & (r <= args.rmax)
    centre, r = centre[ok], r[ok]
    best = (0, None, None)
    for s in range(0, len(r), 20000):
        c, rr = centre[s:s + 20000], r[s:s + 20000]
        dist = np.linalg.norm(xy[None, :, :] - c[:, None, :], axis=2)
        tol = np.maximum(args.tol_abs, args.tol_rel * rr)[:, None]
        n = (np.abs(dist - rr[:, None]) <= tol).sum(1)
        i = int(np.argmax(n)) if len(n) else 0
        if len(n) and n[i] > best[0]:
            best = (int(n[i]), c[i], float(rr[i]))
    return best


def all_triples(n, cap, rng):
    if n * (n - 1) * (n - 2) // 6 <= cap:
        return np.array([(a, b, c) for a in range(n) for b in range(a + 1, n) for c in range(b + 1, n)])
    idx = rng.integers(0, n, size=(cap, 3))
    keep = (idx[:, 0] != idx[:, 1]) & (idx[:, 1] != idx[:, 2]) & (idx[:, 0] != idx[:, 2])
    return idx[keep]


def tile_of(pid):
    m = re.match(r"m60_(\d+)_(\d+)_00", pid.split("/")[-1].split("-")[0]) or re.match(r"m60_(\d+)_(\d+)_00", pid)
    return (int(m[1]), int(m[2])) if m else None


def site_groups(points, radius):
    """Each point's site: the nearest 078 within `radius` (a 078 is its own site), else itself."""
    anchors = [i for i, p in enumerate(points) if p["model"].endswith("078")]
    groups = []
    for i, p in enumerate(points):
        best = (radius, i)
        for a in anchors:
            d = math.dist((p["pos"][0], p["pos"][2]), (points[a]["pos"][0], points[a]["pos"][2]))
            if d <= best[0]:
                best = (d, a)
        groups.append(best[1])
    return groups


def shuffle_in_tiles(points, rng, groups=None):
    if groups is not None:
        # Rigid site null: each site keeps its internal layout and moves as one to a random
        # spot in its anchor's tile, so clustering at site scale stays in the null.
        moves = {}
        out = []
        for p, g in zip(points, groups):
            if g not in moves:
                t = tile_of(points[g]["id"])
                target = (256 * t[0] + rng.uniform(-128, 128), 256 * t[1] + rng.uniform(-128, 128))
                moves[g] = (target[0] - points[g]["pos"][0], target[1] - points[g]["pos"][2])
            out.append([p["pos"][0] + moves[g][0], p["pos"][2] + moves[g][1]])
        return np.array(out)
    out = []
    for p in points:
        t = tile_of(p["id"])
        out.append([256 * t[0] + rng.uniform(-128, 128), 256 * t[1] + rng.uniform(-128, 128)])
    return np.array(out)


def angular_gaps(xy, centre):
    ang = np.sort(np.degrees(np.arctan2(xy[:, 1] - centre[1], xy[:, 0] - centre[0])) % 360)
    gaps = np.diff(np.concatenate([ang, [ang[0] + 360]]))
    return 360 - gaps.max(), np.sort(gaps)[::-1]


def kasa(xy):
    a = np.column_stack([xy[:, 0], xy[:, 1], np.ones(len(xy))])
    b = (xy ** 2).sum(1)
    sol, *_ = np.linalg.lstsq(a, b, rcond=None)
    cx, cy = sol[0] / 2, sol[1] / 2
    r = math.sqrt(max(sol[2] + cx * cx + cy * cy, 0))
    res = np.linalg.norm(xy - [cx, cy], axis=1) - r
    return np.array([cx, cy]), r, float(np.sqrt((res ** 2).mean()))


def big_ring_test(name, points, args, rng):
    xy = np.array([[p["pos"][0], p["pos"][2]] for p in points])
    idx = all_triples(len(xy), args.triples, rng)
    n, centre, r = best_ring(xy, idx, args)
    groups = site_groups(points, args.site_radius) if args.rigid_sites else None
    null = []
    for _ in range(args.nulls):
        sx = shuffle_in_tiles(points, rng, groups)
        null.append(best_ring(sx, all_triples(len(sx), args.triples, rng), args)[0])
    null = np.array(null)
    p = float((1 + (null >= n).sum()) / (1 + len(null)))
    tol = max(args.tol_abs, args.tol_rel * r)
    on = np.abs(np.linalg.norm(xy - centre, axis=1) - r) <= tol
    cover, gaps = angular_gaps(xy[on], centre)
    res = np.linalg.norm(xy[on] - centre, axis=1) - r
    print(f"[{name}] n={len(xy)} best ring r={r:.0f} m centre=({centre[0]:.0f},{centre[1]:.0f}) "
          f"on-ring={n} tol={tol:.1f} m rms={np.sqrt((res**2).mean()):.1f} m "
          f"arc covered={cover:.0f} deg largest gaps={np.round(gaps[:4]).tolist()} "
          f"null on-ring mean={null.mean():.1f} p95={np.percentile(null,95):.0f} max={null.max()} p={p:.3f}")
    return [pt for pt, o in zip(points, on) if o], centre, r, p


def orientation_test(name, on_ring, centre, verts, order, rng, nulls):
    axes = {}
    for m in {p["model"] for p in on_ring}:
        v = verts[m] - verts[m].mean(0)
        w, vec = np.linalg.eigh(np.cov(v.T))
        axes[m] = vec[:, -1]

    def stats(yaw_override=None):
        tang, rad_up, horiz = [], [], []
        for i, p in enumerate(on_ring):
            r = list(p["rot"])
            if yaw_override is not None:
                r[1] = yaw_override[i]
            R = matrix(order, r)
            long = R @ axes[p["model"]]
            up = R @ np.array([0, 1, 0])
            pos = np.array([p["pos"][0], p["pos"][2]])
            radial = (pos - centre) / np.linalg.norm(pos - centre)
            tangent = np.array([-radial[1], radial[0]])
            lxz = np.array([long[0], long[2]])
            horiz.append(np.linalg.norm(lxz))
            if np.linalg.norm(lxz) > 1e-6:
                tang.append(abs(lxz @ tangent) / np.linalg.norm(lxz))
            uxz = np.array([up[0], up[2]])
            if np.linalg.norm(uxz) > 0.2:
                rad_up.append(uxz @ radial / np.linalg.norm(uxz))
        return np.mean(tang), np.mean(np.abs(rad_up)) if rad_up else float("nan"), np.mean(rad_up) if rad_up else float("nan"), np.mean(horiz)

    t, ru, rs, h = stats()
    null = np.array([stats(rng.uniform(-180, 180, len(on_ring)))[0] for _ in range(nulls)])
    p_hi = float((1 + (null >= t).sum()) / (1 + nulls))
    print(f"[{name}] order {order}: long axis |cos to tangent| mean={t:.3f} (random yaw null mean {null.mean():.3f}, "
          f"p(tangent)={p_hi:.3f}, p(radial)={float((1 + (null <= t).sum()) / (1 + nulls)):.3f}); "
          f"long axis horizontal share={h:.2f}; up-axis lean |cos to radial|={ru:.2f}, signed={rs:+.2f} (+ = leans outward)")


def site_rings(points, rubble, args, rng):
    rows = []
    for b in points:
        if not b["model"].endswith("078"):
            continue
        c = np.array([b["pos"][0], b["pos"][2]])
        near = [q for q in rubble if np.linalg.norm(np.array([q["pos"][0], q["pos"][2]]) - c) <= args.site_radius]
        if len(near) < 6:
            continue
        xy = np.array([[q["pos"][0], q["pos"][2]] for q in near] + [c.tolist()])
        cen, r, rms = kasa(xy)
        ratio = rms / r if r else float("inf")
        extent = np.linalg.norm(xy - xy.mean(0), axis=1).max()
        null = []
        for _ in range(args.nulls):
            rr = extent * np.sqrt(rng.uniform(0, 1, len(xy)))
            th = rng.uniform(0, 2 * math.pi, len(xy))
            sim = np.column_stack([rr * np.cos(th), rr * np.sin(th)])
            _, r2, rms2 = kasa(sim)
            null.append(rms2 / r2 if r2 else float("inf"))
        null = np.array(null)
        p = float((1 + (null <= ratio).sum()) / (1 + len(null)))
        cover, gaps = angular_gaps(xy, cen)
        off = np.linalg.norm(cen - c)
        rows.append((p, b["id"], len(xy), r, rms, ratio, float(np.median(null)), cover, off))
    rows.sort()
    for p, pid, n, r, rms, ratio, nm, cover, off in rows:
        print(f"  site {pid:32} n={n:2} r={r:5.1f} m rms={rms:5.1f} ratio={ratio:.2f} (disc null median {nm:.2f}) "
              f"arc={cover:3.0f} deg 078-to-centre={off:5.1f} m p(ring)={p:.3f}")
    ps = np.array([r[0] for r in rows])
    if len(ps):
        print(f"  sites={len(ps)} with p<0.05: {(ps < 0.05).sum()} (expected by chance {0.05*len(ps):.1f})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("report")
    ap.add_argument("verts")
    ap.add_argument("--order", default="XZY")
    ap.add_argument("--nulls", type=int, default=300)
    ap.add_argument("--triples", type=int, default=120000)
    ap.add_argument("--rmin", type=float, default=30.0)
    ap.add_argument("--rmax", type=float, default=2000.0)
    ap.add_argument("--tol-abs", type=float, default=8.0)
    ap.add_argument("--tol-rel", type=float, default=0.03)
    ap.add_argument("--site-radius", type=float, default=40.0)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--only", default="")
    ap.add_argument("--rigid-sites", action="store_true",
                    help="null moves each 078 site (078 plus rubble within --site-radius) as one rigid group")
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)
    table = [p for p in json.load(open(args.report))["placement_table"] if p["world"] == "m60"]
    for p in table:
        if tile_of(p["id"]) is None:
            raise SystemExit(f"no _00 tile for {p['id']}")
    verts = {}
    for name in os.listdir(args.verts):
        if name.endswith(".txt"):
            v = np.loadtxt(os.path.join(args.verts, name)).reshape(-1, 3)
            verts[name[:-4]] = np.unique(np.round(v, 2), axis=0)
    big = [p for p in table if p["model"][-3:] in ("076", "077", "078")]
    rubble = [p for p in table if p["model"][-3:] in ("079", "080", "081")]
    lim = [p for p in big if 39 <= tile_of(p["id"])[0] <= 46 and 33 <= tile_of(p["id"])[1] <= 40]
    sets = {"limgrave-076-078": lim, "all-m60-076-078": big, "all-m60-with-rubble": big + rubble}
    for name, pts in sets.items():
        if args.only and args.only != name:
            continue
        on, centre, r, p = big_ring_test(name, pts, args, rng)
        sel = [q for q in on if q["model"][-3:] in ("076", "077", "078")]
        if len(sel) >= 3:
            for order in (args.order, "YXZ"):
                orientation_test(name, sel, centre, verts, order, rng, args.nulls)
    if not args.only or args.only == "sites":
        print("[site rings: 078 + rubble within site radius, least-squares circle vs uniform disc]")
        site_rings(big, rubble, args, rng)


if __name__ == "__main__":
    main()
