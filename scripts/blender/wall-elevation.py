"""Print a ray-cast elevation of one wall line of a building, as text.

    blender --background <scene.blend> --python scripts/blender/wall-elevation.py -- \
        <reference-holder> <name-regex> <axis x|z> <from> <to> [--cell 0.25] [--json out.json]

Works in the reference holder's local frame (y up, the frame `building-plan.py` prints in). For
axis `x`, rays travel along +x from <from> and count a hit only before <to>, over a grid of
(z, y) cells: the result is the face-on outline of whatever masonry stands between those two x
planes, with openings as gaps. Axis `z` does the same across a gable or facade. Vertex slicing
misses flat faces; rays do not.
"""

import json
import math
import re
import sys

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree


def main():
    args = sys.argv[sys.argv.index("--") + 1:]
    cell = 0.25
    out = None
    if "--cell" in args:
        at = args.index("--cell")
        cell = float(args[at + 1])
        del args[at : at + 2]
    if "--json" in args:
        at = args.index("--json")
        out = args[at + 1]
        del args[at : at + 2]
    reference, pattern, axis, start, stop = args[:5]
    start, stop = float(start), float(stop)
    frame = bpy.data.objects[reference].matrix_world.inverted()
    verts, polys = [], []
    for holder in bpy.data.objects:
        if holder.type != "EMPTY" or not re.match(pattern, holder.name):
            continue
        for obj in holder.children_recursive:
            if obj.type != "MESH":
                continue
            to_frame = frame @ obj.matrix_world
            base = len(verts)
            verts.extend(to_frame @ v.co for v in obj.data.vertices)
            polys.extend([base + i for i in p.vertices] for p in obj.data.polygons)
    tree = BVHTree.FromPolygons(verts, polys)
    lo = [min(v[i] for v in verts) for i in range(3)]
    hi = [max(v[i] for v in verts) for i in range(3)]
    other = 2 if axis == "x" else 0
    along = Vector((1, 0, 0)) if axis == "x" else Vector((0, 0, 1))
    reach = stop - start
    rows = []
    for yi in range(math.ceil(hi[1] / cell), math.floor(lo[1] / cell) - 1, -1):
        row = []
        for ui in range(math.floor(lo[other] / cell), math.ceil(hi[other] / cell) + 1):
            u = (ui + 0.5) * cell
            y = (yi + 0.5) * cell
            origin = Vector((start, y, u)) if axis == "x" else Vector((u, y, start))
            hit = tree.ray_cast(origin, along, reach)
            row.append(hit[0] is not None)
        rows.append((yi * cell, row))
    u0 = math.floor(lo[other] / cell) * cell
    print(f"elevation along {axis} from {start} to {stop}; columns are {'z' if axis == 'x' else 'x'} from {u0} by {cell}")
    for y, row in rows:
        print(f"{y:6.2f} " + "".join("#" if h else "." for h in row))
    if out:
        with open(out, "w") as handle:
            json.dump({"u0": u0, "cell": cell, "rows": [[y, row] for y, row in rows]}, handle)


main()
