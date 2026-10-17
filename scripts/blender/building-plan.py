"""Print every placed part's bounds in the local frame of one reference part.

    blender --background <scene.blend> --python scripts/blender/building-plan.py -- \
        <reference-holder-name> <name-regex> [slice-height ...]

A building kit is modelled on a grid aligned with its pieces, so seen from one piece's frame the
whole ruin becomes axis-aligned and its walls, gaps and heights read off as plain numbers. The
frame is the reference holder's (the empty `assemble-msb-building.py` makes per MSB part), which
sits under the Y-up game frame, so y is height and x/z are the floor plan. With slice heights, it
also prints which x/z cells of a 1 m grid hold geometry at each height, as text, so a wall's gaps
can be read without a render.
"""

import math
import re
import sys

import bpy
from mathutils import Vector


def main():
    args = sys.argv[sys.argv.index("--") + 1:]
    reference, pattern = args[:2]
    cell = 1.0
    if "--cell" in args:
        at = args.index("--cell")
        cell = float(args[at + 1])
        del args[at : at + 2]
    slices = [float(h) for h in args[2:]]
    frame = bpy.data.objects[reference].matrix_world.inverted()
    points_by_part = {}
    for holder in bpy.data.objects:
        if not re.match(pattern, holder.name) or holder.type != "EMPTY":
            continue
        points = []
        for obj in holder.children_recursive:
            if obj.type != "MESH":
                continue
            to_frame = frame @ obj.matrix_world
            points.extend(to_frame @ v.co for v in obj.data.vertices)
        points_by_part[holder.name] = points
        lo = [min(p[i] for p in points) for i in range(3)]
        hi = [max(p[i] for p in points) for i in range(3)]
        print(f"{holder.name:18} x {lo[0]:7.2f}..{hi[0]:7.2f}  y {lo[1]:7.2f}..{hi[1]:7.2f}  z {lo[2]:6.2f}..{hi[2]:6.2f}")

    every = [p for pts in points_by_part.values() for p in pts]
    lo = [math.floor(min(p[i] for p in every)) for i in range(3)]
    hi = [math.ceil(max(p[i] for p in every)) for i in range(3)]
    print(f"all x {lo[0]}..{hi[0]} y {lo[1]}..{hi[1]} z {lo[2]}..{hi[2]}")
    for height in slices:
        cells = set()
        for p in every:
            if height - 0.5 <= p.y < height + 0.5:
                cells.add((math.floor(p.x / cell), math.floor(p.z / cell)))
        columns = range(math.floor(lo[0] / cell), math.ceil(hi[0] / cell) + 1)
        print(f"slice y={height} (rows are z from high to low, columns x from {lo[0]} by {cell} m)")
        for z in range(math.ceil(hi[2] / cell), math.floor(lo[2] / cell) - 1, -1):
            row = "".join("#" if (x, z) in cells else "." for x in columns)
            print(f"{z * cell:7.2f} {row}")


main()
