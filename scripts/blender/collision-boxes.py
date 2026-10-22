"""Turn drawn geometry into axis-aligned boxes for collision, in its holder's frame.

    blender --background <scene.blend> --python scripts/blender/collision-boxes.py -- \
        <name-prefix>[,<prefix>...] <out.json> [--cell 0.25] [--top 12.5] [--gap 1.3]

Collision for a rebuilt asset is made of scaled copies of the invisible wall AEG099_002, whose
Havok box scales with the spawn's scale (measured 2026-10-17: a copy at scale 0.1 in x held the
player at its centre and let them fall 3 m off it). So the shape only has to be boxes:

1. every cell of a grid whose centre lies within half a cell diagonal of the surface is solid;
2. along x and along z, runs of empty cells shorter than `--gap` between two solid cells are
   filled, which closes the inside of walls and columns (about 1 m thick) but not the arcade
   openings or the aisles (3 m and more);
3. solid cells merge greedily into boxes (x, then z, then y).

Only cells below `--top` are kept: nothing higher can be reached. Output: a JSON list of
{"centre": [x, y, z], "size": [sx, sy, sz]} in the FLVER frame of the holder (x negated back
from Blender's), ready to place against the holder's part.
"""

import json
import math
import sys

import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree


def option(args, name, default):
    if name in args:
        at = args.index(name)
        value = float(args[at + 1])
        del args[at : at + 2]
        return value
    return default


def main():
    args = sys.argv[sys.argv.index("--") + 1:]
    cell = option(args, "--cell", 0.25)
    top = option(args, "--top", 12.5)
    gap = option(args, "--gap", 1.3)
    reach_arg = option(args, "--reach", 0.0)
    prefixes, out = tuple(args[0].split(",")), args[1]
    bm = bmesh.new()
    for obj in bpy.data.objects:
        if obj.type != "MESH" or not obj.name.startswith(prefixes):
            continue
        part = bmesh.new()
        part.from_mesh(obj.data)
        to_holder = obj.parent.matrix_world.inverted() @ obj.matrix_world if obj.parent else obj.matrix_world
        part.transform(to_holder)
        mesh = bpy.data.meshes.new("tmp")
        part.to_mesh(mesh)
        part.free()
        bm.from_mesh(mesh)
        bpy.data.meshes.remove(mesh)
    tree = BVHTree.FromBMesh(bm)
    lo = Vector([math.floor(min(v.co[i] for v in bm.verts) / cell) * cell for i in range(3)])
    hi = Vector([math.ceil(max(v.co[i] for v in bm.verts) / cell) * cell for i in range(3)])
    hi.y = min(hi.y, top)
    n = [int(round((hi[i] - lo[i]) / cell)) for i in range(3)]
    reach = reach_arg or cell * math.sqrt(3) / 2
    solid = set()
    for ix in range(n[0]):
        for iy in range(n[1]):
            for iz in range(n[2]):
                c = lo + Vector(((ix + 0.5) * cell, (iy + 0.5) * cell, (iz + 0.5) * cell))
                hit = tree.find_nearest(c, reach)
                if hit[0] is not None:
                    solid.add((ix, iy, iz))
    surface = len(solid)
    span = int(gap / cell)
    for axis in (0, 2):
        filled = set()
        for (ix, iy, iz) in solid:
            for k in range(2, span + 2):
                step = (k, 0, 0) if axis == 0 else (0, 0, k)
                other = (ix + step[0], iy, iz + step[2])
                if other in solid:
                    for j in range(1, k):
                        filled.add((ix + (j if axis == 0 else 0), iy, iz + (j if axis == 2 else 0)))
                    break
        solid |= filled
    print(f"grid {n}, surface cells {surface}, solid after fill {len(solid)}")

    boxes = []
    left = set(solid)
    for key in sorted(solid):
        if key not in left:
            continue
        x0, y0, z0 = key
        x1 = x0
        while (x1 + 1, y0, z0) in left:
            x1 += 1
        z1 = z0
        while all((x, y0, z1 + 1) in left for x in range(x0, x1 + 1)):
            z1 += 1
        y1 = y0
        while all((x, y1 + 1, z) in left for x in range(x0, x1 + 1) for z in range(z0, z1 + 1)):
            y1 += 1
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                for z in range(z0, z1 + 1):
                    left.discard((x, y, z))
        a = lo + Vector((x0 * cell, y0 * cell, z0 * cell))
        b = lo + Vector(((x1 + 1) * cell, (y1 + 1) * cell, (z1 + 1) * cell))
        centre = (a + b) / 2
        size = b - a
        # To the FLVER frame: x negated.
        boxes.append({"centre": [round(-centre.x, 3), round(centre.y, 3), round(centre.z, 3)],
                      "size": [round(size.x, 3), round(size.y, 3), round(size.z, 3)]})
    with open(out, "w") as handle:
        json.dump(boxes, handle)
    print(f"{len(boxes)} boxes -> {out}")


main()
