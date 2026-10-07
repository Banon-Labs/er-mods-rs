#!/usr/bin/env python3
"""Decide whether an invasion spawn point has collision under it, from the collision mesh.

# Why this replaces the navmesh audit

`scripts/aip-underfloor-audit.py` asks the navmesh, and the navmesh is the *walkable* subset
of the world: a catacomb floor or a raised platform can carry full collision and no navmesh at
all. Measured live 2026-09-21 on two points that audit called `under-floor-no-catch` --
`m30_10_00_00` #3 and `m12_02_00_00` #59 -- the player landed solidly on both, and the engine
read the settled height back within 0.04 m of the request. Two false positives out of two.

# What this reads instead

Every `hkcdStaticMeshTree::Section` inside a map's `h*` collision carries a float axis-aligned
box: `min` at `+0x10` and `max` at `+0x20` of its 96-byte record, in map-local coordinates and
needing no de-quantization. The boxes are a superset of the triangles inside them, which is what
makes the negative verdict sound: if no box in a point's vertical column sits below it, then no
triangle does either, and nothing can catch a fall from there.

A box *containing* the point is weaker evidence -- the geometry inside it may be metres away --
so `inside-collision` means "not demonstrably falling", not "standing on stone".

Usage:
    python3 scripts/collision-column-audit.py --msb [--tsv out.tsv]
    python3 scripts/collision-column-audit.py --aip [--tsv out.tsv]
    python3 scripts/collision-column-audit.py --selftest
"""

from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import os
import struct
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP_ROOT = os.environ.get(
    "ER_COLLISION_ROOT",
    "/home/banon/er-extract/LOOK_HERE_WITCHY_RECURSIVE_20260713/sharded/map",
)
AIP_ROOT = os.environ.get(
    "ER_AIP_CORPUS_ROOT",
    "/home/banon/er-extract/LOOK_HERE_WITCHY_RECURSIVE_20260713/sharded/other",
)
MSB_ORACLE = os.environ.get(
    "ER_MSB_INVASION_POINTS",
    os.path.expanduser("~/er-extract/invasion_points.20260804.jsonl"),
)

SECTION_STRIDE = 96
SECTION_MIN = 0x10
SECTION_MAX = 0x20
# The column is widened by this much so a point authored on the lip of a box is not missed.
COLUMN_SLACK = 0.5


def _tagfile_module():
    spec = importlib.util.spec_from_file_location(
        "hkx_tagfile", os.path.join(REPO, "scripts", "hkx-tagfile.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# `hknpPhysicsSystemData::bodyCinfoWithAttachment`, 0xc0 bytes: the body's position is the
# `hkVector4` at +0x30 and its orientation the quaternion at +0x40.
BODY_STRIDE = 0xC0
BODY_POSITION = 0x30
BODY_ORIENTATION = 0x40


def section_boxes(map_dir, prefix, tagfile_mod):
    """Every collision section box of one map, in map space.

    The section boxes are stored in the shape's own space and the body places that shape in the
    map. Skipping the body translation is not a rounding error: `h60_50_38_00_503800` carries a
    body at `(512, 0, 512)` and boxes spanning `x[-566,-384] z[-640,-384]`, which land exactly on
    the tile's own `x[-54,128] z[-128,128]` once it is applied -- and half a kilometre away if it
    is not. That single omission is what made this audit report "nothing below" for three points a
    live warp then proved solid.
    """
    compendia = glob.glob(os.path.join(map_dir, f"{prefix}*-hkxbdt", "*.compendium"))
    compendium = open(compendia[0], "rb").read() if compendia else None
    boxes = []
    for path in glob.glob(os.path.join(map_dir, f"{prefix}*-hkxbdt", f"{prefix}*.hkx")):
        try:
            tf = tagfile_mod.Tagfile(open(path, "rb").read(), compendium)
        except Exception:
            continue
        base = tf.data_off
        bodies = []
        for item in tf.items:
            if tf.tname(item["type"]) != "hknpPhysicsSystemData::bodyCinfoWithAttachment":
                continue
            for k in range(item["count"]):
                off = base + item["off"] + BODY_STRIDE * k
                pos = struct.unpack_from("<3f", tf.d, off + BODY_POSITION)
                quat = struct.unpack_from("<4f", tf.d, off + BODY_ORIENTATION)
                bodies.append((pos, quat))
        # One shape per file in this data, so a single body is the placement for every section
        # in it. A file that ever carries two bodies with different transforms would need the
        # shape-to-body mapping instead of this, so refuse rather than silently pick the first.
        if len(bodies) > 1:
            positions = {b[0] for b in bodies}
            if len(positions) > 1:
                raise ValueError(f"{path}: {len(bodies)} bodies with differing transforms")
        pos = bodies[0][0] if bodies else (0.0, 0.0, 0.0)
        quat = bodies[0][1] if bodies else (0.0, 0.0, 0.0, 1.0)
        rotated = abs(quat[0]) + abs(quat[1]) + abs(quat[2]) > 1e-4
        for item in tf.items:
            if tf.tname(item["type"]) != "hkcdStaticMeshTree::Section":
                continue
            for k in range(item["count"]):
                off = base + item["off"] + SECTION_STRIDE * k
                lo = struct.unpack_from("<3f", tf.d, off + SECTION_MIN)
                hi = struct.unpack_from("<3f", tf.d, off + SECTION_MAX)
                boxes.append(place_box(lo, hi, pos, quat if rotated else None))
    return boxes


def rotate(quat, v):
    """Rotate a vector by a quaternion given as (x, y, z, w)."""
    qx, qy, qz, qw = quat
    tx = 2.0 * (qy * v[2] - qz * v[1])
    ty = 2.0 * (qz * v[0] - qx * v[2])
    tz = 2.0 * (qx * v[1] - qy * v[0])
    return (
        v[0] + qw * tx + (qy * tz - qz * ty),
        v[1] + qw * ty + (qz * tx - qx * tz),
        v[2] + qw * tz + (qx * ty - qy * tx),
    )


def place_box(lo, hi, pos, quat):
    """Put one shape-space box into map space under a body's transform.

    A rotated body cannot have its extents shifted -- the eight corners are rotated and the
    axis-aligned hull of the result is taken instead. That hull is larger than the true shape,
    which errs toward reporting collision that may not be there; for an audit whose only sound
    verdict is the negative one ("nothing below"), erring that way is the safe direction.
    """
    if quat is None:
        return (
            (lo[0] + pos[0], lo[1] + pos[1], lo[2] + pos[2]),
            (hi[0] + pos[0], hi[1] + pos[1], hi[2] + pos[2]),
        )
    corners = [
        rotate(quat, (lo[0] if i & 1 else hi[0], lo[1] if i & 2 else hi[1], lo[2] if i & 4 else hi[2]))
        for i in range(8)
    ]
    return (
        tuple(min(c[a] for c in corners) + pos[a] for a in range(3)),
        tuple(max(c[a] for c in corners) + pos[a] for a in range(3)),
    )


def classify(boxes, x, y, z):
    """Verdict for one point against a map's collision boxes."""
    column = [
        (lo, hi)
        for lo, hi in boxes
        if lo[0] - COLUMN_SLACK <= x <= hi[0] + COLUMN_SLACK
        and lo[2] - COLUMN_SLACK <= z <= hi[2] + COLUMN_SLACK
    ]
    if not column:
        return "no-collision-column", None, None, 0
    inside = [b for b in column if b[0][1] - COLUMN_SLACK <= y <= b[1][1] + COLUMN_SLACK]
    below = [b for b in column if b[1][1] < y]
    above = [b for b in column if b[0][1] > y]
    nearest_below = max((b[1][1] for b in below), default=None)
    nearest_above = min((b[0][1] for b in above), default=None)
    if inside:
        return "inside-collision", nearest_above, nearest_below, len(column)
    if below:
        return "drop-to-collision", nearest_above, nearest_below, len(column)
    return "nothing-below", nearest_above, nearest_below, len(column)


def area_prefix(map_name):
    """`m12_02_00_00` -> (`map/m12/m12_02_00_00`, `h12`)."""
    area = map_name.split("_")[0]
    return os.path.join(MAP_ROOT, area, map_name), "h" + area[1:]


def parse_aip(path):
    b = open(path, "rb").read()
    count = struct.unpack_from("<I", b, 12)[0]
    pts = [struct.unpack_from("<4f", b, 0x10 + 0x10 * i) for i in range(count)]
    return os.path.basename(path)[:-4], pts


def iter_msb():
    with open(MSB_ORACLE, encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            pts = rec.get("invasion_points") or []
            if not pts:
                continue
            out = []
            for e in pts:
                p = e["Position"]
                out.append(
                    (float(p["X"]), float(p["Y"]), float(p["Z"]), float(e["Rotation"]["Y"]))
                )
            yield rec["map"], out


def iter_aip():
    for sub in ("autoinvadepoint-aipbnd-dcx", "autoinvadepoint_dlc02-aipbnd-dcx"):
        for path in sorted(glob.glob(os.path.join(AIP_ROOT, sub, "*.aip"))):
            yield parse_aip(path)


def run(source, args):
    tagfile_mod = _tagfile_module()
    rows = []
    counts = {}
    no_boxes = []
    for map_name, pts in source:
        map_dir, prefix = area_prefix(map_name)
        boxes = section_boxes(map_dir, prefix, tagfile_mod) if os.path.isdir(map_dir) else []
        if not boxes:
            no_boxes.append(map_name)
        for i, (x, y, z, yaw) in enumerate(pts):
            verdict, above, below, n = classify(boxes, x, y, z)
            counts[verdict] = counts.get(verdict, 0) + 1
            rows.append((map_name, i, x, y, z, yaw, verdict, above, below, n))

    if args.tsv:
        with open(args.tsv, "w", encoding="utf-8") as fh:
            fh.write("map\tindex\tx\ty\tz\tyaw\tverdict\tnearest_above\tnearest_below\tcolumn_boxes\n")
            for r in rows:
                a = "" if r[7] is None else f"{r[7]:.3f}"
                b = "" if r[8] is None else f"{r[8]:.3f}"
                fh.write(
                    f"{r[0]}\t{r[1]}\t{r[2]:.3f}\t{r[3]:.3f}\t{r[4]:.3f}\t{r[5]:.4f}"
                    f"\t{r[6]}\t{a}\t{b}\t{r[9]}\n"
                )

    print(f"{len(rows)} points")
    for k in sorted(counts, key=lambda k: -counts[k]):
        print(f"  {counts[k]:6d}  {k}")
    if no_boxes:
        print(f"maps with no collision read: {len(no_boxes)} -> {no_boxes[:6]}")
    bad = [r for r in rows if r[6] == "nothing-below"]
    print(f"\nfalls with nothing beneath ({len(bad)}):")
    for r in sorted(bad, key=lambda r: -(r[7] - r[3]) if r[7] is not None else 0)[:30]:
        ceiling = "none" if r[7] is None else f"{r[7] - r[3]:.1f} m above"
        print(
            f"  {r[0]} #{r[1]:<4d} ({r[2]:9.2f},{r[3]:9.2f},{r[4]:9.2f})"
            f"  ceiling {ceiling}, column boxes {r[9]}"
        )
    return 0


def selftest():
    failures = []
    boxes = [
        ((-5.0, 0.0, -5.0), (5.0, 2.0, 5.0)),
        ((-5.0, 20.0, -5.0), (5.0, 22.0, 5.0)),
    ]
    cases = [
        ((0.0, 1.0, 0.0), "inside-collision"),
        ((0.0, 10.0, 0.0), "drop-to-collision"),
        ((0.0, -50.0, 0.0), "nothing-below"),
        ((99.0, 1.0, 99.0), "no-collision-column"),
    ]
    for (x, y, z), want in cases:
        got, _, _, _ = classify(boxes, x, y, z)
        if got != want:
            failures.append(f"({x},{y},{z}): want {want}, got {got}")
    # The ceiling and floor reported for a point between the two slabs name the right boxes.
    _, above, below, n = classify(boxes, 0.0, 10.0, 0.0)
    if above != 20.0 or below != 2.0 or n != 2:
        failures.append(f"between slabs: above={above} below={below} n={n}")
    # Column slack admits a point just outside a box edge rather than dropping it.
    got, _, _, _ = classify(boxes, 5.4, 1.0, 0.0)
    if got != "inside-collision":
        failures.append(f"edge slack: want inside-collision, got {got}")

    # An untransformed body leaves the box where it was, and a translation moves it whole.
    lo, hi = place_box((-1.0, -2.0, -3.0), (1.0, 2.0, 3.0), (0.0, 0.0, 0.0), None)
    if lo != (-1.0, -2.0, -3.0) or hi != (1.0, 2.0, 3.0):
        failures.append(f"identity placement: {lo} {hi}")
    lo, hi = place_box((-1.0, -2.0, -3.0), (1.0, 2.0, 3.0), (512.0, 0.0, 512.0), None)
    if lo != (511.0, -2.0, 509.0) or hi != (513.0, 2.0, 515.0):
        failures.append(f"translated placement: {lo} {hi}")
    # A quarter turn about y swaps the x and z extents of a box that is longer in z.
    lo, hi = place_box((-1.0, -2.0, -3.0), (1.0, 2.0, 3.0), (0.0, 0.0, 0.0), (0.0, 0.7071068, 0.0, 0.7071068))
    if abs(lo[0] + 3.0) > 1e-3 or abs(hi[0] - 3.0) > 1e-3 or abs(hi[2] - 1.0) > 1e-3:
        failures.append(f"quarter turn: {lo} {hi}")
    # Rotation must not disturb the axis it turns about.
    if abs(lo[1] + 2.0) > 1e-6 or abs(hi[1] - 2.0) > 1e-6:
        failures.append(f"quarter turn moved y: {lo} {hi}")
    for f in failures:
        print(f"FAIL {f}")
    print("selftest: " + ("FAILED" if failures else "ok"))
    return 1 if failures else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--msb", action="store_true")
    ap.add_argument("--aip", action="store_true")
    ap.add_argument("--tsv")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.msb:
        return run(iter_msb(), args)
    if args.aip:
        return run(iter_aip(), args)
    ap.error("pick --msb or --aip")


if __name__ == "__main__":
    sys.exit(main())
