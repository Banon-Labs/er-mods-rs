#!/usr/bin/env python3
"""Find auto-invade points that sit underneath a walkable surface.

# What this answers

The invasion-warp feature drops the player at an `.aip` auto-invade point. Some of those
warps land the player under the floor, which reads in game as falling through the world.
This script decides, offline, which points those are: for every point it shoots a vertical
ray through the tile's navmesh and reports what walkable surface is above and below.

# Why navmesh and not collision

The authoritative floor is the `h*` collision mesh, but ELDEN RING stores it as
`fsnpCustomParamCompressedMeshShape` -- a quantized `hkcdStaticMeshTree` that has to be
un-quantized section by section. The navmesh in `m*-nvmhktbnd-dcx` is the same geometry's
walkable subset stored as plain `hkVector4` vertices with an explicit edge list, so it
needs no decoding at all. It under-reports (a ledge with no navmesh is still solid
ground), which is why a point with no navmesh overhead is reported as `no-cover` rather
than as good.

# Frames

Overworld navmesh vertices are tile-local and span the tile's own 256 m box, which is the
frame the `.aip` records are already in -- `--verify-frames` prints the per-tile agreement
so that claim is checked rather than assumed.

Usage:
    uv run --with numpy python3 scripts/aip-underfloor-audit.py [--tsv out.tsv]
    uv run --with numpy python3 scripts/aip-underfloor-audit.py --selftest
"""

import argparse
import glob
import importlib.util
import os
import struct
import sys

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

AIP_ROOTS = os.environ.get(
    "ER_AIP_CORPUS_ROOT",
    "/home/banon/er-extract/LOOK_HERE_WITCHY_RECURSIVE_20260713/sharded/other",
)
NAVMESH_ROOT = os.environ.get(
    "ER_NAVMESH_ROOT",
    "/home/banon/er-extract/LOOK_HERE_WITCHY_RECURSIVE_20260713/sharded/map",
)

# A landing is treated as standing on a surface when a navmesh triangle is within this many
# metres of it. The engine snaps a warped player down onto collision, so a point authored a
# little above its floor is normal; `1.5` covers that without swallowing a real drop.
ON_SURFACE_TOLERANCE = 1.5
# Cover this far above a point is what makes it "underneath" something rather than merely in
# open air below a distant cliff.
COVER_CEILING = 60.0
# A point this close to any navmesh vertex in three dimensions is standing on walkable
# geometry, whatever the vertical column through its exact `xz` happens to cross.
NEAR_VERTEX = 2.0


def _tagfile_module():
    spec = importlib.util.spec_from_file_location(
        "hkx_tagfile", os.path.join(REPO, "scripts", "hkx-tagfile.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parse_aip(path):
    """Decode one `.aip` file -> (block name, list of (x, y, z, yaw))."""
    b = open(path, "rb").read()
    if b[:4] != b"FPIA":
        raise ValueError(f"{path}: magic {b[:4]!r}")
    count = struct.unpack_from("<I", b, 12)[0]
    expect = 0x10 + 0x10 * count
    if len(b) != expect:
        raise ValueError(f"{path}: {len(b)} bytes, {count} points wants {expect}")
    pts = [struct.unpack_from("<4f", b, 0x10 + 0x10 * i) for i in range(count)]
    return os.path.basename(path)[:-4], pts


def load_navmesh(path, tagfile_mod):
    """Return (vertices (n,3) float32, triangles (m,3) int32) for one `n*.hkx`.

    The item table alone identifies everything needed, so no `TBDY` member offsets are
    required: the face array is `hkaiNavMesh::Face`, the edge array `hkaiNavMesh::Edge`,
    and the vertex array the sole `hkVector4`. Layouts are fixed-width and were confirmed
    against the data -- a face's `startEdgeIndex` advances by exactly its `numEdges`, and
    the first faces' edges close into triangles.
    """
    tf = tagfile_mod.Tagfile(open(path, "rb").read())
    faces = next((i for i in tf.items if tf.tname(i["type"]) == "hkaiNavMesh::Face"), None)
    edges = next((i for i in tf.items if tf.tname(i["type"]) == "hkaiNavMesh::Edge"), None)
    verts = next((i for i in tf.items if tf.tname(i["type"]) == "hkVector4"), None)
    if not faces or not edges or not verts or verts["count"] == 0:
        return np.zeros((0, 3), np.float32), np.zeros((0, 3), np.int32)

    buf = tf.d
    base = tf.data_off

    v = np.frombuffer(
        buf, dtype=np.float32, count=verts["count"] * 4, offset=base + verts["off"]
    ).reshape(-1, 4)[:, :3]

    f = np.frombuffer(
        buf, dtype=np.int32, count=faces["count"] * 4, offset=base + faces["off"]
    ).reshape(-1, 4)
    start_edge = f[:, 0]
    num_edges = (f[:, 2] & 0xFFFF).astype(np.int32)

    e = np.frombuffer(
        buf, dtype=np.uint32, count=edges["count"] * 5, offset=base + edges["off"]
    ).reshape(-1, 5)
    edge_a = e[:, 0].astype(np.int64)

    # Fan-triangulate each face from its first edge's vertex.
    tris = []
    for s, n in zip(start_edge.tolist(), num_edges.tolist()):
        if n < 3 or s < 0 or s + n > edge_a.shape[0]:
            continue
        ring = edge_a[s : s + n]
        for k in range(1, n - 1):
            tris.append((ring[0], ring[k], ring[k + 1]))
    if not tris:
        return v, np.zeros((0, 3), np.int32)
    return v, np.asarray(tris, dtype=np.int64)


def surfaces_under_and_over(v, tris, px, pz):
    """Heights of every navmesh triangle crossed by the vertical line at (px, pz)."""
    if tris.shape[0] == 0:
        return np.zeros(0, np.float32)
    a = v[tris[:, 0]]
    b = v[tris[:, 1]]
    c = v[tris[:, 2]]
    # Barycentric containment in the xz plane.
    v0x, v0z = b[:, 0] - a[:, 0], b[:, 2] - a[:, 2]
    v1x, v1z = c[:, 0] - a[:, 0], c[:, 2] - a[:, 2]
    v2x, v2z = px - a[:, 0], pz - a[:, 2]
    den = v0x * v1z - v1x * v0z
    ok = np.abs(den) > 1e-9
    if not ok.any():
        return np.zeros(0, np.float32)
    inv = np.zeros_like(den)
    inv[ok] = 1.0 / den[ok]
    u = (v2x * v1z - v1x * v2z) * inv
    w = (v0x * v2z - v2x * v0z) * inv
    inside = ok & (u >= -1e-4) & (w >= -1e-4) & (u + w <= 1.0 + 1e-4)
    if not inside.any():
        return np.zeros(0, np.float32)
    y = a[:, 1] + u * (b[:, 1] - a[:, 1]) + w * (c[:, 1] - a[:, 1])
    return y[inside]


def nearest_vertex_distance(v, x, y, z):
    """Distance from a point to the closest navmesh vertex, in three dimensions."""
    if v.shape[0] == 0:
        return None
    dx = v[:, 0] - x
    dy = v[:, 1] - y
    dz = v[:, 2] - z
    return float(np.sqrt((dx * dx + dy * dy + dz * dz).min()))


def classify(y, hits, near=None):
    """Label one point from the surface heights the vertical line crossed.

    `near` is the distance to the closest navmesh vertex. The column test alone is
    brittle at the metre scale: navmesh triangles do not tile a slope exactly, so a point
    standing on a step can have its own height fall through a seam and read as though the
    only surface were the one overhead. Two of the first three overworld points flagged
    this way had navmesh within a metre of them sideways, so a point that close to real
    walkable geometry is treated as standing on it.
    """
    if near is not None and near <= NEAR_VERTEX:
        return "on-surface", None, None
    if hits.size == 0:
        return "no-navmesh", None, None
    above = hits[hits > y + ON_SURFACE_TOLERANCE]
    below = hits[hits < y - ON_SURFACE_TOLERANCE]
    on = hits[np.abs(hits - y) <= ON_SURFACE_TOLERANCE]
    nearest_above = float(above.min()) if above.size else None
    nearest_below = float(below.max()) if below.size else None
    if on.size:
        return "on-surface", nearest_above, nearest_below
    if nearest_above is not None and nearest_above - y <= COVER_CEILING:
        if nearest_below is None:
            return "under-floor-no-catch", nearest_above, nearest_below
        return "under-floor", nearest_above, nearest_below
    if nearest_below is not None:
        return "above-surface", nearest_above, nearest_below
    return "no-cover", nearest_above, nearest_below


def navmesh_paths_for(block):
    """Every `n*.hkx` belonging to one overworld block name such as `m60_33_40_00`."""
    area = block.split("_")[0]
    pat = os.path.join(NAVMESH_ROOT, area, block, f"{block}-nvmhktbnd-dcx", "n*.hkx")
    return sorted(glob.glob(pat))


# Overworld tiles are 256 m, and a block name `m60_XX_YY_00` places its origin at
# `256*XX` along x and `256*YY` along z. Proven rather than assumed: `m60_52_41_00`'s
# point #14 sits at local `z = 265.21`, nine metres past its own tile, and the surface
# under it is in `m60_51_42_00` at `248.32` against the point's `248.33`.
TILE_SIZE = 256.0


def _tile_grid(block):
    parts = block.split("_")
    return int(parts[1]), int(parts[2])


def tile_mesh(block, cache, tagfile_mod):
    """Vertices and triangles of one block's navmesh, in that block's own local frame."""
    if block in cache:
        return cache[block]
    vs, ts = [], []
    base = 0
    for path in navmesh_paths_for(block):
        v, t = load_navmesh(path, tagfile_mod)
        if v.shape[0]:
            vs.append(v)
            if t.shape[0]:
                ts.append(t + base)
            base += v.shape[0]
    if vs:
        out = (
            np.concatenate(vs),
            np.concatenate(ts) if ts else np.zeros((0, 3), np.int64),
        )
    else:
        out = (np.zeros((0, 3), np.float32), np.zeros((0, 3), np.int64))
    if len(cache) > 48:
        cache.clear()
    cache[block] = out
    return out


def neighbourhood_mesh(block, cache, tagfile_mod):
    """The block and its eight neighbours, all expressed in the block's local frame.

    Returns `(vertices, triangles, own_is_empty)`.
    """
    area = block.split("_")[0]
    i, j = _tile_grid(block)
    vs, ts = [], []
    base = 0
    own_empty = True
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            name = f"{area}_{i + di:02d}_{j + dj:02d}_00"
            v, t = tile_mesh(name, cache, tagfile_mod)
            if v.shape[0] == 0:
                continue
            if di == 0 and dj == 0:
                own_empty = False
            if di or dj:
                v = v + np.array(
                    [TILE_SIZE * di, 0.0, TILE_SIZE * dj], dtype=np.float32
                )
            vs.append(v)
            if t.shape[0]:
                ts.append(t + base)
            base += v.shape[0]
    if not vs:
        return np.zeros((0, 3), np.float32), np.zeros((0, 3), np.int64), own_empty
    return (
        np.concatenate(vs),
        np.concatenate(ts) if ts else np.zeros((0, 3), np.int64),
        own_empty,
    )


def msb_navmesh_paths(map_name):
    """Every navmesh piece of one non-overworld map, e.g. `m10_00_00_00`.

    Legacy-dungeon pieces are authored in map-local space and tile against each other --
    `n10_00_00_00_000700` covers `z[24.0, 46.3]`, `_000800` covers `z[8.0, 24.0]` -- so
    their union is directly comparable to an MSB region's `Position`, with no per-piece
    placement transform to apply.
    """
    area = map_name.split("_")[0]
    pat = os.path.join(
        NAVMESH_ROOT, area, map_name, f"{map_name}-nvmhktbnd-dcx", "n*.hkx"
    )
    return sorted(glob.glob(pat))


def run_msb(args):
    """Audit the MSB `InvasionPoint` regions -- the legacy dungeons the `.aip` table omits."""
    import json

    tagfile_mod = _tagfile_module()
    oracle = os.environ.get(
        "ER_MSB_INVASION_POINTS",
        os.path.expanduser("~/er-extract/invasion_points.20260804.jsonl"),
    )
    if not os.path.exists(oracle):
        sys.exit(f"no MSB invasion-point table at {oracle} (set ER_MSB_INVASION_POINTS)")

    rows = []
    counts = {}
    no_mesh_maps = []
    with open(oracle, encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            pts = rec.get("invasion_points") or []
            if not pts:
                continue
            name = rec["map"]
            paths = msb_navmesh_paths(name)
            vs, ts, base = [], [], 0
            for p in paths:
                v, t = load_navmesh(p, tagfile_mod)
                if v.shape[0]:
                    vs.append(v)
                    if t.shape[0]:
                        ts.append(t + base)
                    base += v.shape[0]
            v = np.concatenate(vs) if vs else np.zeros((0, 3), np.float32)
            t = np.concatenate(ts) if ts else np.zeros((0, 3), np.int64)
            if v.shape[0] == 0:
                no_mesh_maps.append(name)
            for i, e in enumerate(pts):
                pos = e["Position"]
                x, y, z = float(pos["X"]), float(pos["Y"]), float(pos["Z"])
                yaw = float(e["Rotation"]["Y"])
                hits = surfaces_under_and_over(v, t, x, z)
                label, above, below = classify(
                    y, hits, nearest_vertex_distance(v, x, y, z)
                )
                counts[label] = counts.get(label, 0) + 1
                rows.append((name, i, x, y, z, yaw, label, above, below))

    _report(rows, counts, args, f"{len(rows)} MSB invasion points")
    if no_mesh_maps:
        print(f"maps with no navmesh at all: {len(no_mesh_maps)} -> {no_mesh_maps[:6]}")
    return 0


def _report(rows, counts, args, headline):
    if args.tsv:
        with open(args.tsv, "w", encoding="utf-8") as fh:
            fh.write("block\tindex\tx\ty\tz\tyaw\tverdict\tnearest_above\tnearest_below\n")
            for r in rows:
                above = "" if r[7] is None else f"{r[7]:.3f}"
                below = "" if r[8] is None else f"{r[8]:.3f}"
                fh.write(
                    f"{r[0]}\t{r[1]}\t{r[2]:.3f}\t{r[3]:.3f}\t{r[4]:.3f}\t{r[5]:.4f}"
                    f"\t{r[6]}\t{above}\t{below}\n"
                )
    print(headline)
    for label in sorted(counts, key=lambda k: -counts[k]):
        print(f"  {counts[label]:6d}  {label}")
    bad = [r for r in rows if r[6].startswith("under-floor")]
    bad.sort(key=lambda r: -(r[7] - r[3]))
    print(f"\nworst {min(len(bad), 25)} of {len(bad)} under-floor points:")
    for r in bad[:25]:
        catch = "none" if r[8] is None else f"{r[8]:.1f}"
        print(
            f"  {r[0]} #{r[1]:<3d} ({r[2]:8.2f},{r[3]:9.2f},{r[4]:8.2f})"
            f"  floor {r[7] - r[3]:6.2f} m above, catch below {catch}"
        )


def run(args):
    tagfile_mod = _tagfile_module()
    aip_files = []
    for sub in ("autoinvadepoint-aipbnd-dcx", "autoinvadepoint_dlc02-aipbnd-dcx"):
        aip_files += sorted(glob.glob(os.path.join(AIP_ROOTS, sub, "*.aip")))
    if not aip_files:
        sys.exit(f"no .aip files under {AIP_ROOTS} (set ER_AIP_CORPUS_ROOT)")

    rows = []
    missing = []
    counts = {}
    cache = {}
    for path in aip_files:
        block, pts = parse_aip(path)
        v, t, own_empty = neighbourhood_mesh(block, cache, tagfile_mod)
        if own_empty:
            missing.append(block)
        if args.verify_frames and v.shape[0]:
            px = np.array([p[0] for p in pts])
            pz = np.array([p[2] for p in pts])
            print(
                f"frames {block}: navmesh x[{v[:, 0].min():.1f},{v[:, 0].max():.1f}] "
                f"z[{v[:, 2].min():.1f},{v[:, 2].max():.1f}] "
                f"y[{v[:, 1].min():.1f},{v[:, 1].max():.1f}] | "
                f"aip x[{px.min():.1f},{px.max():.1f}] z[{pz.min():.1f},{pz.max():.1f}]"
            )
        for i, (x, y, z, yaw) in enumerate(pts):
            hits = surfaces_under_and_over(v, t, x, z)
            label, above, below = classify(y, hits, nearest_vertex_distance(v, x, y, z))
            counts[label] = counts.get(label, 0) + 1
            rows.append((block, i, x, y, z, yaw, label, above, below))

    _report(rows, counts, args, f"points {len(rows)} across {len(aip_files)} blocks")
    if missing:
        blanked = sum(1 for r in rows if r[0] in set(missing))
        print(
            f"blocks whose own navmesh is empty: {len(missing)} "
            f"({blanked} points lean entirely on neighbours) -> {missing[:6]}"
        )
    return 0


def selftest():
    """Synthetic navmesh: one floor slab at y=10, one at y=0, over the unit square."""

    def slab(y, tag):
        v = np.array(
            [[-5, y, -5], [5, y, -5], [5, y, 5], [-5, y, 5]], dtype=np.float32
        )
        t = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int64)
        return v, t, tag

    v_lo, t_lo, _ = slab(0.0, "lo")
    v_hi, t_hi, _ = slab(10.0, "hi")
    v = np.concatenate([v_lo, v_hi])
    t = np.concatenate([t_lo, t_hi + 4])

    cases = [
        (0.0, "on-surface"),
        (10.0, "on-surface"),
        (5.0, "under-floor"),
        # Below everything, with the lower slab still overhead inside `COVER_CEILING`:
        # covered, and nothing beneath to land on.
        (-40.0, "under-floor-no-catch"),
        # Above everything: a normal drop onto the upper slab, not a defect.
        (20.0, "above-surface"),
        # Far enough below that no slab counts as cover, and still nothing to land on.
        (-400.0, "no-cover"),
    ]
    failures = []
    for y, want in cases:
        hits = surfaces_under_and_over(v, t, 0.0, 0.0)
        got, _, _ = classify(y, hits)
        if got != want:
            failures.append(f"y={y}: want {want}, got {got}")

    # A point outside the slab footprint sees nothing at all.
    got, _, _ = classify(0.0, surfaces_under_and_over(v, t, 99.0, 99.0))
    if got != "no-navmesh":
        failures.append(f"outside footprint: want no-navmesh, got {got}")

    # A point over only the lower slab, with the ceiling removed, must not read as covered.
    got, _, _ = classify(5.0, surfaces_under_and_over(v_lo, t_lo, 0.0, 0.0))
    if got != "above-surface":
        failures.append(f"no ceiling: want above-surface, got {got}")

    # The near-vertex rule rescues a point that falls through a seam in the walkable
    # surface: nothing in its own column, but real geometry a hand's width away.
    seam = np.array([[0, 0, 0], [3, 0, 0], [3, 0, 3]], dtype=np.float32)
    seam_t = np.array([[0, 1, 2]], dtype=np.int64)
    got, _, _ = classify(
        0.5,
        surfaces_under_and_over(seam, seam_t, -1.0, -1.0),
        nearest_vertex_distance(seam, -1.0, 0.5, -1.0),
    )
    if got != "on-surface":
        failures.append(f"near-vertex rescue: want on-surface, got {got}")
    # ... but only a hand's width. Far enough away and the verdict stands.
    got, _, _ = classify(
        0.5,
        surfaces_under_and_over(seam, seam_t, -20.0, -20.0),
        nearest_vertex_distance(seam, -20.0, 0.5, -20.0),
    )
    if got != "no-navmesh":
        failures.append(f"near-vertex out of range: want no-navmesh, got {got}")

    # Ray/triangle maths: a sloped slab reports the interpolated height, not a vertex.
    vs = np.array([[-1, 0, -1], [1, 0, -1], [1, 4, 1], [-1, 4, 1]], dtype=np.float32)
    ts = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int64)
    mid = surfaces_under_and_over(vs, ts, 0.0, 0.0)
    if mid.size == 0 or abs(float(mid[0]) - 2.0) > 1e-3:
        failures.append(f"slope midpoint: want 2.0, got {mid}")

    for f in failures:
        print(f"FAIL {f}")
    print("selftest: " + ("FAILED" if failures else "ok"))
    return 1 if failures else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tsv", help="write the full per-point table here")
    ap.add_argument(
        "--verify-frames",
        action="store_true",
        help="print each block's navmesh bounds beside its point bounds",
    )
    ap.add_argument(
        "--msb",
        action="store_true",
        help="audit the MSB InvasionPoint regions (legacy dungeons) instead of the .aip table",
    )
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    return run_msb(args) if args.msb else run(args)


if __name__ == "__main__":
    sys.exit(main())
