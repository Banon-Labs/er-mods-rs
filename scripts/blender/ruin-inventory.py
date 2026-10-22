"""List what a kit-bash of a ruin needs to know: each part's materials, and the column centres.

    blender --background <scene.blend> --python scripts/blender/ruin-inventory.py -- \
        <reference-holder> <name-regex> [column-height]

Frame: the reference holder's local frame (y up), as `building-plan.py` prints. Columns are found
by slicing every mesh at one height (default 4 m, mid-shaft) and clustering the vertices on that
slice within 0.15 m of it into 1.2 m blobs; a blob's centre and size are printed per part.
"""

import re
import sys

import bpy


def main():
    args = sys.argv[sys.argv.index("--") + 1:]
    reference, pattern = args[:2]
    height = float(args[2]) if len(args) > 2 else 4.0
    frame = bpy.data.objects[reference].matrix_world.inverted()
    for holder in sorted((o for o in bpy.data.objects if o.type == "EMPTY" and re.match(pattern, o.name)), key=lambda o: o.name):
        materials = {}
        points = []
        for obj in holder.children_recursive:
            if obj.type != "MESH":
                continue
            to_frame = frame @ obj.matrix_world
            for poly in obj.data.polygons:
                slot = obj.material_slots[poly.material_index].material if obj.material_slots else None
                key = slot.name.split(".")[0] if slot else "-"
                materials[key] = materials.get(key, 0) + 1
            for v in obj.data.vertices:
                p = to_frame @ v.co
                if abs(p.y - height) < 0.15:
                    points.append(p)
        blobs = []
        for p in points:
            for blob in blobs:
                if abs(blob["c"][0] - p.x) < 1.2 and abs(blob["c"][1] - p.z) < 1.2:
                    blob["pts"].append(p)
                    n = len(blob["pts"])
                    blob["c"] = ((blob["c"][0] * (n - 1) + p.x) / n, (blob["c"][1] * (n - 1) + p.z) / n)
                    break
            else:
                blobs.append({"c": (p.x, p.z), "pts": [p]})
        print(f"{holder.name}: materials {dict(sorted(materials.items()))}")
        for blob in sorted(blobs, key=lambda b: (round(b['c'][0]), b['c'][1])):
            xs = [p.x for p in blob["pts"]]
            zs = [p.z for p in blob["pts"]]
            print(f"   blob x {min(xs):6.2f}..{max(xs):6.2f}  z {min(zs):6.2f}..{max(zs):6.2f}  ({len(xs)} verts)")


main()
