#!/usr/bin/env python3
"""Lay out a raised diamond arena and write it as one geom-lab `spawnlist` command.

The arena is a floor raised `--floor` metres above the ground, ringed by a wall whose top is
`--wall` metres above that floor. From outside the wall stands floor + wall high, too tall to jump;
from inside it stands only `--wall` high, low enough to jump over and drop out. The way in is
`--steps` up the outside of one edge, each step and the wall above the top one lower than a jump
(a player's feet rise 1.15-1.17 m, standing or running). Round inlays mark two start corners.

Only the stretch of the entrance wall over the steps, `--step-width` wide, is that low. Every other
wall stands `--tall` above the floor: out of reach of any jump but below eye height, so the steps
are the one way in and out and the fight stays visible. The default low wall is 1.0 m. Height
alone cannot tell a standing jump from a running one, since both rise the same, but the logged jumps show feet above 1.0 m over 0.5-1.2 m of forward
travel when running and only about 0.1 m when standing, so a 1.0 m wall should want a run-up.

With `--previous` (the spawnlist command that built what stands now) and `--log` (the geom-lab log
with its result), the output is instead a batch that spawns only what changed and then hides what
it replaces.

Collision comes only from AEG099_002, the invisible wall, scaled into boxes (its Havok box is
14.925 x 8.333 x 0.13 m with its origin at the bottom centre; bd
geom-asset-hide-spawn-swap-scaled-collision-frida-proven-1171-2026-10-17). What you see is other
models drawn over those boxes with their own collision off.

A diamond cannot be tiled with rectangles, so the floor is `--bands` strips running along one pair of
edges. Each strip runs past the other pair of edges by up to its width times the cotangent of the
diamond's angle; the wall is `--thick` thick and sits outside every edge, so that overhang is buried
inside it. `--selftest` checks that every point of the diamond is under a strip and that no strip
pokes out of the wall.

Coordinates are the block's own frame (PlayerIns+0x6c0 block_position). A model's local x lands on
world (cos yaw, -sin yaw), the convention geom-lab's `place` and `polygon` use.

Usage: er-arena-build.py --block 0x3c2a2400 --centre=-43.83,92.29,79.07 --seq 61000
           [--previous built.json --log geom-lab.jsonl]
"""

import argparse
import json
import math
import sys

AEG099_002 = (14.925, 8.333, 0.13)
# FLVER header bounds ({size, min}) of the models drawn over the boxes, read with
# scripts/er-asset-slab-census.py's reader; names from Smithbox's ER asset aliases.
FLOOR_TILE = ("AEG240_491", (8.0, 0.27, 8.0), (-4.0, 5.8, -4.0))  # dark stone tiled floor, tileable
# Small stone castle parapet segment (same materials as the 21.1 m AEG030_139), repeated along each
# wall piece near its own length so the brickwork and the merlons keep their proportions.
WALL = ("AEG030_144", (2.259, 2.39, 0.731), (-1.15, -0.004, -0.381))
BLOCK = ("AEG230_939", (7.95, 2.49, 2.11), (0.0, -0.07, -1.05))  # rectangular stone block
MARKER = ("AEG020_978", (8.065, 0.349, 8.065), (-4.032, 0.011, -4.033))  # round stone, flower pattern
# Tall stone pedestal with a brazier on top. Spawned with AssetSfxParamRelativeID 0 it lights
# itself: AssetModelSfxParam row 20983000, effect 800023 (a point light) at dummy poly 200, y 1.42
# (bd asset-model-sfx-lights-spawned-geom-2720-2026-10-21).
LIGHT = ("AEG020_983", (0.838, 1.43, 0.846), (-0.419, 0.041, -0.423))
# Metal lever, the commonest placed one (49 placements). It starts and stops the duel; making it
# pullable is separate work, so for now it is only placed.
LEVER = ("AEG027_002", (0.674, 1.195, 1.232), (-0.337, -0.2, -0.516))


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def unit(a):
    n = math.hypot(a[0], a[1])
    return (a[0] / n, a[1] / n)


def yaw_of(d):
    """The yaw that puts a model's local x on direction d."""
    return math.atan2(-d[1], d[0])


def rotate(yaw, x, z):
    """Model-local (x, z) to world (x, z) at this yaw."""
    c, s = math.cos(yaw), math.sin(yaw)
    return (x * c + z * s, -x * s + z * c)


def diamond(a, b):
    return [(a / 2, 0.0), (0.0, b / 2), (-a / 2, 0.0), (0.0, -b / 2)]


def outward(verts, i):
    """Unit outward normal of edge i (verts[i] -> verts[i + 1]); the polygon is centred on 0."""
    p, q = verts[i], verts[(i + 1) % len(verts)]
    e = unit(sub(q, p))
    m = (e[1], -e[0])
    if dot(m, ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)) < 0:
        m = (-m[0], -m[1])
    return m


def clip(poly, n, s, keep_below):
    """Sutherland-Hodgman against the half-plane n.p <= s (or >= s)."""
    out = []
    inside = (lambda p: dot(n, p) <= s + 1e-9) if keep_below else (lambda p: dot(n, p) >= s - 1e-9)
    for i, p in enumerate(poly):
        q = poly[(i + 1) % len(poly)]
        if inside(p):
            out.append(p)
        if inside(p) != inside(q):
            t = (s - dot(n, p)) / dot(n, sub(q, p))
            out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    return out


def box(centre_xz, along, length, width, bottom, height):
    """A box footprint: centre, the direction of its length, length, width, bottom y, height."""
    return {"c": centre_xz, "u": along, "len": length, "wid": width, "y0": bottom, "h": height}


def floor_strips(verts, bands):
    """Strips along edge 0 (and its opposite, edge 2), stacked across between them."""
    u = unit(sub(verts[1], verts[0]))
    n = outward(verts, 0)
    s_top = dot(n, verts[0])
    s_bot = dot(n, verts[2])
    strips = []
    for i in range(bands):
        lo = s_bot + (s_top - s_bot) * i / bands
        hi = s_bot + (s_top - s_bot) * (i + 1) / bands
        piece = clip(clip(list(verts), n, hi, True), n, lo, False)
        ts = [dot(u, p) for p in piece]
        t0, t1 = min(ts), max(ts)
        mid_t, mid_s = (t0 + t1) / 2, (lo + hi) / 2
        strips.append(((u[0] * mid_t + n[0] * mid_s, u[1] * mid_t + n[1] * mid_s), u, t1 - t0, hi - lo))
    return strips


def wall_pieces(verts, thick, entrance, gap, low):
    """One wall per edge, inner face on the edge, `thick` outward and running thick/2 past each
    corner so neighbours close. The entrance edge is two pieces either side of a `gap` gap, or with
    no gap three pieces whose middle one, `low` wide over the steps, is the low one. Each piece is
    (edge, centre, direction, length, is_low)."""
    out = []
    for i in range(len(verts)):
        p, q = verts[i], verts[(i + 1) % len(verts)]
        e = unit(sub(q, p))
        m = outward(verts, i)
        length = math.hypot(*sub(q, p))
        spans = [(-thick / 2, length + thick / 2, False)]
        if i == entrance and gap > 0:
            spans = [(-thick / 2, (length - gap) / 2, False), ((length + gap) / 2, length + thick / 2, False)]
        elif i == entrance:
            spans = [(-thick / 2, (length - low) / 2, False), ((length - low) / 2, (length + low) / 2, True),
                     ((length + low) / 2, length + thick / 2, False)]
        for a, b, is_low in spans:
            t = (a + b) / 2
            c = (p[0] + e[0] * t + m[0] * thick / 2, p[1] + e[1] * t + m[1] * thick / 2)
            out.append((i, c, e, b - a, is_low))
    return out


def collider(tag, footprint, origin):
    """AEG099_002 scaled to the box; its origin is its bottom centre."""
    f = footprint
    return {
        "tag": tag,
        "asset": "AEG099_002",
        "pos": [origin[0] + f["c"][0], f["y0"], origin[2] + f["c"][1]],
        "rot": [0.0, yaw_of(f["u"]), 0.0],
        "scale": [f["len"] / AEG099_002[0], f["h"] / AEG099_002[1], f["wid"] / AEG099_002[2]],
    }


def visual(tag, model, footprint, origin, top_align=None):
    """`model` scaled so its bounds fill the box and moved so the two coincide; collision off.
    `top_align` keeps the model's own thickness (scaled with its footprint) and puts its top on the
    box's top instead, for a floor tile over a box that runs down into the ground."""
    name, size, lo = model
    f = footprint
    yaw = yaw_of(f["u"])
    sx, sz = f["len"] / size[0], f["wid"] / size[2]
    sy = f["h"] / size[1] if top_align is None else (sx + sz) / 2
    cx, cz = (lo[0] + size[0] / 2) * sx, (lo[2] + size[2] / 2) * sz
    dx, dz = rotate(yaw, cx, cz)
    y = f["y0"] - lo[1] * sy if top_align is None else top_align - (lo[1] + size[1]) * sy
    return {
        "tag": tag,
        "asset": name,
        "pos": [origin[0] + f["c"][0] - dx, y, origin[2] + f["c"][1] - dz],
        "rot": [0.0, yaw, 0.0],
        "scale": [sx, sy, sz],
        "collision": False,
    }


def layout(args):
    verts = diamond(*args.diamond)
    ground = args.centre[1] + args.ground
    bottom = ground - args.sink
    floor_top = ground + args.floor
    items = []
    for i, (c, u, length, width) in enumerate(floor_strips(verts, args.bands)):
        f = box(c, u, length, width, bottom, floor_top - bottom)
        items.append(collider("floor%d" % i, f, args.centre))
        for j in range(args.tiles):
            t = -length / 2 + length * (j + 0.5) / args.tiles
            tc = (c[0] + u[0] * t, c[1] + u[1] * t)
            items.append(visual("floor%d.tile%d" % (i, j), FLOOR_TILE, box(tc, u, length / args.tiles, width, bottom, floor_top - bottom), args.centre, top_align=floor_top))
    piece = {}
    for edge, c, e, length, is_low in wall_pieces(verts, args.thick, args.entrance, args.gap, args.step_width):
        # Tagged by edge, so a change to one edge leaves the others' tags (and pieces) alone.
        piece[edge] = piece.get(edge, -1) + 1
        wall_top = floor_top + (args.wall if is_low else args.tall)
        f = box(c, e, length, args.thick, bottom, wall_top - bottom)
        items.append(collider("wall%d.%d" % (edge, piece[edge]), f, args.centre))
        # Whole copies of the segment, each stretched by length/n so they abut exactly.
        n = max(1, round(length / WALL[1][0]))
        for k in range(n):
            t = -length / 2 + length * (k + 0.5) / n
            sc = (c[0] + e[0] * t, c[1] + e[1] * t)
            items.append(visual("wall%d.%d.look%d" % (edge, piece[edge], k), WALL, box(sc, e, length / n, args.thick, ground - 0.1, wall_top - ground + 0.1), args.centre))
    # The way in: steps up the outside of the entrance edge, highest against the wall (or against
    # the floor's edge when the wall has a gap), and with a gap a stone face on the floor's edge.
    p, q = verts[args.entrance], verts[(args.entrance + 1) % len(verts)]
    e = unit(sub(q, p))
    m = outward(verts, args.entrance)
    mid = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
    start = 0.0 if args.gap > 0 else args.thick
    for k, top in enumerate(sorted(args.steps, reverse=True)):
        out = start + args.step_depth * (k + 0.5)
        sc = (mid[0] + m[0] * out, mid[1] + m[1] * out)
        items.append(collider("step%d" % k, box(sc, e, args.step_width, args.step_depth, bottom, ground + top - bottom), args.centre))
        items.append(visual("step%d.look" % k, BLOCK, box(sc, e, args.step_width, args.step_depth, ground - 0.1, top + 0.1), args.centre))
    if args.gap > 0:
        rc = (mid[0] - m[0] * 0.15, mid[1] - m[1] * 0.15)
        items.append(visual("riser.look", BLOCK, box(rc, e, args.gap, 0.3, ground - 0.1, args.floor + 0.1), args.centre))
    # Start positions: a round inlay `--marker-inset` metres in from each named corner, along the
    # diagonal, its top just proud of the floor.
    for v in args.markers:
        d = unit(verts[v])
        mc = (verts[v][0] - d[0] * args.marker_inset, verts[v][1] - d[1] * args.marker_inset)
        name, size, lo = MARKER
        s = args.marker_size / size[0]
        f = box(mc, (-d[0], -d[1]), args.marker_size, args.marker_size * size[2] / size[0], 0.0, (lo[1] + size[1]) * s)
        items.append(visual("start%d" % v, MARKER, f, args.centre, top_align=floor_top + 0.03))
    # Lights: a brazier pedestal on the railing at each named corner, standing on the wall top where
    # the two walls' centre lines cross, i.e. thick/2 / sin(half the corner angle) out along the
    # corner's bisector. Every corner joins two tall pieces, so the top is floor + `--tall`.
    name, size, lo = LIGHT
    for v in args.lights:
        a, b = verts[v - 1], verts[(v + 1) % len(verts)]
        u1, u2 = unit(sub(a, verts[v])), unit(sub(b, verts[v]))
        bis = unit((u1[0] + u2[0], u1[1] + u2[1]))
        half = math.acos(max(-1.0, min(1.0, u1[0] * bis[0] + u1[1] * bis[1])))
        d = -(args.thick / 2) / math.sin(half)
        lc = (verts[v][0] + bis[0] * d, verts[v][1] + bis[1] * d)
        items.append({
            "tag": "light%d" % v,
            "asset": name,
            "pos": [args.centre[0] + lc[0], floor_top + args.tall - lo[1], args.centre[2] + lc[1]],
            "rot": [0.0, yaw_of(bis), 0.0],
            "scale": [1.0, 1.0, 1.0],
            "sfx": 0,
        })
    # The lever: on the ground beside the outer step, `--lever-gap` clear of the steps' side, on the
    # `--lever-side` end of the entrance edge (+1 toward the edge's second vertex), across the edge.
    if args.lever_side:
        name, size, lo = LEVER
        p, q = verts[args.entrance], verts[(args.entrance + 1) % len(verts)]
        e = unit(sub(q, p))
        m = outward(verts, args.entrance)
        mid = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
        along = args.lever_side * (args.step_width / 2 + args.lever_gap + max(size[0], size[2]) / 2)
        out = args.thick + args.step_depth * (len(args.steps) - 0.5)
        lc = (mid[0] + e[0] * along + m[0] * out, mid[1] + e[1] * along + m[1] * out)
        items.append({
            "tag": "lever",
            "asset": name,
            "pos": [args.centre[0] + lc[0], ground, args.centre[2] + lc[1]],
            "rot": [0.0, yaw_of(e), 0.0],
            "scale": [1.0, 1.0, 1.0],
        })
    return verts, items


def diff(old_cmd, log_path, new_items, seq, block):
    """A batch that brings a built arena to `new_items`: spawn what is new or moved first, then
    hide what was removed or replaced, so the floor is never missing under the player. The old
    pieces are found by tag: the latest `spawnlist` result for each tag in the geom-lab log, since
    an update spawns only the pieces it changes."""
    key = lambda it: json.dumps([it["asset"], [round(x, 4) for x in it["pos"] + it["rot"] + it["scale"]], it.get("collision")])
    old = {it["tag"]: it for it in old_cmd["items"]}
    new = {it["tag"]: it for it in new_items}
    spawned = {}
    with open(log_path) as f:
        for line in f:
            payload = json.loads(line).get("message", {}).get("payload", {})
            if payload.get("kind") == "spawnlist":
                spawned.update({r["tag"]: r for r in payload["items"] if r["ok"]})
    missing = sorted(set(old) - set(spawned))
    if missing:
        sys.exit("no spawnlist result in the log for %s" % ", ".join(missing))
    add = [it for t, it in new.items() if t not in old or key(old[t]) != key(it)]
    drop = [{"ptr": spawned[t]["ptr"], "name": spawned[t]["name"]} for t in old if t not in new or key(old[t]) != key(new[t])]
    cmds = []
    if add:
        cmds.append({"op": "spawnlist", "block": block, "items": add})
    if drop:
        cmds.append({"op": "unspawn", "enable": False, "ptrs": drop})
    return {"seq": seq, "op": "batch", "cmds": cmds}, len(add), len(drop)


def selftest():
    args = parse([
        "--block", "0x0", "--centre", "0,0,0", "--seq", "1",
    ])
    verts, items = layout(args)
    strips = floor_strips(verts, args.bands)

    def in_strip(p, s):
        c, u, length, width = s
        d = sub(p, c)
        n = (-u[1], u[0])
        return abs(dot(d, u)) <= length / 2 + 1e-6 and abs(dot(d, n)) <= width / 2 + 1e-6

    def outside_by(p):
        return max(dot(outward(verts, i), p) - dot(outward(verts, i), verts[i]) for i in range(4))

    # Every point of the diamond is under some strip.
    misses = 0
    for xi in range(-110, 111):
        for zi in range(-95, 96):
            p = (xi / 10, zi / 10)
            if outside_by(p) <= -0.01 and not any(in_strip(p, s) for s in strips):
                misses += 1
    assert misses == 0, misses
    # No strip corner pokes out past the wall's outer face.
    worst = 0.0
    for c, u, length, width in strips:
        n = (-u[1], u[0])
        for a in (-1, 1):
            for b in (-1, 1):
                corner = (c[0] + u[0] * a * length / 2 + n[0] * b * width / 2, c[1] + u[1] * a * length / 2 + n[1] * b * width / 2)
                worst = max(worst, outside_by(corner))
    assert worst < args.thick, worst
    # Wall inner faces lie on the edges: a wall centre is thick/2 outside its edge.
    pieces = wall_pieces(verts, args.thick, args.entrance, args.gap, args.step_width)
    for edge, c, e, length, is_low in pieces:
        assert abs(dot(outward(verts, edge), c) - dot(outward(verts, edge), verts[edge]) - args.thick / 2) < 1e-9
    # The low stretch is the steps' width, centred on the entrance edge like the steps.
    low = [(edge, c, length) for edge, c, e, length, is_low in pieces if is_low]
    p, q = verts[args.entrance], verts[(args.entrance + 1) % len(verts)]
    m = outward(verts, args.entrance)
    mid = ((p[0] + q[0]) / 2 + m[0] * args.thick / 2, (p[1] + q[1]) / 2 + m[1] * args.thick / 2)
    assert len(low) == 1 and low[0][0] == args.entrance and abs(low[0][2] - args.step_width) < 1e-9, low
    assert math.hypot(low[0][1][0] - mid[0], low[0][1][1] - mid[1]) < 1e-9, low
    # The heights the design rests on, against a measured jump rise of 1.15-1.17 m: the wall too tall
    # from outside, low enough from inside, and each step and the wall above the top step a jump.
    tops = sorted(args.steps)
    rises = [b - a for a, b in zip([0.0] + tops, tops + [args.floor + args.wall])]
    assert args.floor + args.wall > 1.9 and args.wall <= 1.0 and max(rises) < 1.0, rises
    # The other walls keep everyone in without hiding the view: above the highest rise seen
    # anywhere (1.30 m), below eye height.
    assert 1.3 < args.tall < 1.6, args.tall
    # Each brazier stands on both walls' centre lines at its corner, on top of the tall wall.
    for it in (it for it in items if it["tag"].startswith("light")):
        v = int(it["tag"][5:])
        lc = (it["pos"][0] - args.centre[0], it["pos"][2] - args.centre[2])
        for edge in (v - 1) % len(verts), v:
            m = outward(verts, edge)
            assert abs(dot(m, lc) - dot(m, verts[edge]) - args.thick / 2) < 1e-9, (it["tag"], edge)
        assert abs(it["pos"][1] + LIGHT[2][1] - (args.centre[1] + args.ground + args.floor + args.tall)) < 1e-9
        assert it["sfx"] == 0
    tags = [it["tag"] for it in items]
    assert len(tags) == len(set(tags)) and "start0" in tags and "start2" in tags
    # A collider's box: a 14.925 m long, 0.13 m thick AEG099_002 at scale 1 stays itself.
    c = collider("t", box((0, 0), (1, 0), 14.925, 0.13, 0, 8.333), (0, 0, 0))
    assert all(abs(k - 1) < 1e-9 for k in c["scale"]) and abs(c["rot"][1]) < 1e-12
    # A visual fitted to a box lands with its bounds centred on the box: with the model's x
    # running 0..size, the spawn point sits half a box length behind the box centre.
    v = visual("t", ("M", (2.0, 1.0, 2.0), (0.0, 0.0, -1.0)), box((5, 5), (1, 0), 4.0, 2.0, 0, 1), (0, 0, 0))
    assert abs(v["pos"][0] - 3.0) < 1e-9 and abs(v["pos"][2] - 5.0) < 1e-9, v
    print("selftest ok: %d strips cover the diamond, worst overhang %.2f m of %.2f m wall, %d items" % (len(strips), worst, args.thick, len(items)))


def parse(argv):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--block", required=True)
    p.add_argument("--centre", required=True, type=lambda s: [float(x) for x in s.split(",")])
    p.add_argument("--seq", required=True, type=int)
    p.add_argument("--diamond", default=[21.0, 18.0], type=lambda s: [float(x) for x in s.split(",")])
    p.add_argument("--ground", type=float, default=0.1, help="ground above the centre's y")
    p.add_argument("--sink", type=float, default=1.0, help="how far the boxes go below the ground")
    p.add_argument("--floor", type=float, default=1.2, help="floor top above the ground")
    p.add_argument("--wall", type=float, default=1.0, help="entrance wall top above the floor")
    p.add_argument("--tall", type=float, default=1.4, help="every other wall's top above the floor")
    p.add_argument("--thick", type=float, default=0.6)
    p.add_argument("--bands", type=int, default=4)
    p.add_argument("--tiles", type=int, default=4, help="floor tiles per band")
    p.add_argument("--entrance", type=int, default=0, help="edge index with the way in")
    p.add_argument("--gap", type=float, default=0.0, help="width of an opening in the entrance wall")
    floats = lambda s: [float(x) for x in s.split(",")]
    p.add_argument("--steps", type=floats, default=[0.7, 1.4], help="step tops above the ground")
    p.add_argument("--step-width", type=float, default=2.4)
    p.add_argument("--step-depth", type=float, default=1.0)
    p.add_argument("--markers", type=lambda s: [int(x) for x in s.split(",") if x], default=[0, 2], help="corners with a start marker")
    p.add_argument("--marker-inset", type=float, default=3.0)
    p.add_argument("--marker-size", type=float, default=2.0)
    p.add_argument("--lights", type=lambda s: [int(x) for x in s.split(",") if x], default=[0, 1, 2, 3], help="corners with a lit brazier on the railing")
    p.add_argument("--lever-side", type=int, default=1, choices=(-1, 0, 1), help="which side of the steps the lever stands on; 0 for none")
    p.add_argument("--lever-gap", type=float, default=0.6, help="space between the lever and the steps")
    p.add_argument("--previous", help="the spawnlist command that built what is standing now")
    p.add_argument("--log", help="geom-lab log holding that command's result (pointers by tag)")
    return p.parse_args(argv)


def main():
    if "--selftest" in sys.argv:
        selftest()
        return
    args = parse(sys.argv[1:])
    _, items = layout(args)
    if args.previous:
        with open(args.previous) as f:
            cmd, added, dropped = diff(json.load(f), args.log, items, args.seq, args.block)
        print("%d to spawn, %d to hide" % (added, dropped), file=sys.stderr)
    else:
        cmd = {"seq": args.seq, "op": "spawnlist", "block": args.block, "items": items}
    print(json.dumps(cmd))


if __name__ == "__main__":
    main()
