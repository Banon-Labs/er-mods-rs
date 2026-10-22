"""Print where a wall line is solid, and how far its face stands out, along z at one height.

    blender --background <scene.blend> --python scripts/blender/wall-profile.py -- \
        <reference-holder> <name-regex> <x-from> <x-to> <height> [--step 0.05]

Rays run along x from <x-from> toward <x-to> at the given height (reference holder frame, y up),
one per z step. Output: the solid z intervals with the depth of first hit in each, so columns,
piers and buttresses read off as exact numbers instead of 1 m slice cells.
"""

import re
import sys

import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree


def main():
    args = sys.argv[sys.argv.index("--") + 1:]
    step = 0.05
    if "--step" in args:
        at = args.index("--step")
        step = float(args[at + 1])
        del args[at : at + 2]
    reference, pattern, x0, x1, height = args[0], args[1], float(args[2]), float(args[3]), float(args[4])
    frame = bpy.data.objects[reference].matrix_world.inverted()
    bm = bmesh.new()
    for holder in bpy.data.objects:
        if holder.type != "EMPTY" or not re.match(pattern, holder.name):
            continue
        for obj in holder.children_recursive:
            if obj.type == "MESH":
                part = bmesh.new()
                part.from_mesh(obj.data)
                part.transform(frame @ obj.matrix_world)
                mesh = bpy.data.meshes.new("tmp")
                part.to_mesh(mesh)
                bm.from_mesh(mesh)
                part.free()
    tree = BVHTree.FromBMesh(bm)
    direction = Vector((1.0 if x1 > x0 else -1.0, 0, 0))
    span = abs(x1 - x0)
    z, run = -3.0, None
    rows = []
    while z <= 44.0:
        hit = tree.ray_cast(Vector((x0, height, z)), direction, span)
        depth = round(hit[0].x, 2) if hit[0] is not None else None
        if depth is not None and run is None:
            run = [z, z, [depth]]
        elif depth is not None:
            run[1] = z
            run[2].append(depth)
        elif run is not None:
            rows.append(run)
            run = None
        z = round(z + step, 4)
    if run:
        rows.append(run)
    for a, b, depths in rows:
        print(f"z {a:6.2f}..{b:6.2f}  width {b - a:5.2f}  face x {min(depths):6.2f}..{max(depths):6.2f}")


main()
