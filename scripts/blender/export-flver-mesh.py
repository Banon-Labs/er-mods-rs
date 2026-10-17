"""Write objects of a .blend as the text mesh `er-asset-pack` packs into a FLVER.

    blender --background <scene.blend> --python scripts/blender/export-flver-mesh.py -- \
        <name-prefix>[,<name-prefix>...] <out.txt>

Every mesh object whose name starts with one of the prefixes is taken in its parent holder's
local frame (the part frame `assemble-msb-building.py` makes, which is the OBJ frame `flver-obj`
writes: the FLVER's own with x negated). Here x is negated back, which by itself gives the
game's winding, so the output is in the FLVER's left-handed frame facing the way Blender shows
it. One `mesh <material>` block per material.

Normals: a corner takes the area-weighted normals of the faces meeting at its position within
35 degrees of its own face, so columns and mouldings come out smooth and edges stay hard.
Corners equal in position, normal, tangent and UV share one vertex, because a FLVER mesh holds
at most 65535.

UVs: the file stores FLVER v (Blender shows 1 - v, as `flver-obj` writes it), and each face is
shifted by whole tiles to sit near zero, because the FLVER keeps UVs as i16 / 2048 and so cannot
reach past +-16. Objects named `restore_*` are long planar-mapped prisms whose faces span many
tiles, so they are first sliced on an 8 m grid.
"""

import math
import sys

import bmesh
import bpy
from mathutils import Vector

CELL = 8.0
SMOOTH_COS = math.cos(math.radians(35.0))


def tangent_of(points, uvs, normal):
    e1, e2 = points[1] - points[0], points[2] - points[0]
    du1, dv1 = uvs[1][0] - uvs[0][0], uvs[1][1] - uvs[0][1]
    du2, dv2 = uvs[2][0] - uvs[0][0], uvs[2][1] - uvs[0][1]
    det = du1 * dv2 - du2 * dv1
    if abs(det) < 1e-12:
        t = normal.orthogonal().normalized()
        return t, 1.0
    t = (e1 * dv2 - e2 * dv1) / det
    b = (e2 * du1 - e1 * du2) / det
    t = t - normal * normal.dot(t)
    if t.length < 1e-9:
        return normal.orthogonal().normalized(), 1.0
    t.normalize()
    w = 1.0 if normal.cross(t).dot(b) >= 0 else -1.0
    return t, w


def key_of(co):
    return (round(co.x, 3), round(co.y, 3), round(co.z, 3))


def main():
    prefixes, out = sys.argv[sys.argv.index("--") + 1:][:2]
    prefixes = tuple(prefixes.split(","))
    blocks = {}
    for obj in bpy.data.objects:
        if obj.type != "MESH" or not obj.name.startswith(prefixes):
            continue
        to_part = obj.parent.matrix_world.inverted() @ obj.matrix_world if obj.parent else obj.matrix_world
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.transform(to_part)
        if obj.name.startswith("restore_"):
            # Slice on an 8 m grid so no face spans more tiles than an i16 UV can hold.
            for axis in range(3):
                lo = math.floor(min(v.co[axis] for v in bm.verts) / CELL)
                hi = math.ceil(max(v.co[axis] for v in bm.verts) / CELL)
                for k in range(lo + 1, hi):
                    normal = Vector([1.0 if i == axis else 0.0 for i in range(3)])
                    geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
                    bmesh.ops.bisect_plane(bm, geom=geom, plane_co=normal * (k * CELL), plane_no=normal)
        bmesh.ops.triangulate(bm, faces=bm.faces)
        bm.normal_update()
        faces = [f for f in bm.faces if f.calc_area() > 1e-10]
        around = {}
        for face in faces:
            weighted = face.normal * face.calc_area()
            for v in face.verts:
                around.setdefault(key_of(v.co), []).append((face.normal.copy(), weighted))
        uv_layer = bm.loops.layers.uv.active
        for face in faces:
            material = obj.data.materials[face.material_index].name.split(".")[0]
            block = blocks.setdefault(material, {"v": [], "f": [], "index": {}})
            loops = list(face.loops)
            uv = [tuple(l[uv_layer].uv) for l in loops]
            du = math.floor(min(u for u, _ in uv))
            dv = math.floor(min(v for _, v in uv))
            uv = [(u - du, v - dv) for u, v in uv]
            # To the FLVER frame: negate x and store v flipped. The order stays: the mirror alone
            # turns Blender's counter-clockwise front faces into From's clockwise ones.
            points = [Vector((-l.vert.co.x, l.vert.co.y, l.vert.co.z)) for l in loops]
            flver_uv = [(u, 1.0 - v) for u, v in uv]
            face_n = Vector((-face.normal.x, face.normal.y, face.normal.z)).normalized()
            tangent, w = tangent_of(points, flver_uv, face_n)
            corner = []
            for l, p, (u, v) in zip(loops, points, flver_uv):
                total = Vector((0.0, 0.0, 0.0))
                for n, weighted in around[key_of(l.vert.co)]:
                    if n.dot(face.normal) >= SMOOTH_COS:
                        total += weighted
                n = Vector((-total.x, total.y, total.z))
                n = n.normalized() if n.length > 1e-12 else face_n
                t = (tangent - n * n.dot(tangent))
                t = t.normalized() if t.length > 1e-9 else tangent
                vertex = (p, n, t, w, u, v)
                key = (key_of(p), tuple(round(c, 2) for c in n), tuple(round(c, 2) for c in t), w, round(u, 4), round(v, 4))
                index = block["index"].get(key)
                if index is None:
                    index = len(block["v"])
                    block["index"][key] = index
                    block["v"].append(vertex)
                corner.append(index)
            if len(set(corner)) == 3:
                block["f"].append(tuple(corner))
        bm.free()

    with open(out, "w") as handle:
        for material, block in sorted(blocks.items()):
            handle.write(f"mesh {material}\n")
            for p, n, t, w, u, v in block["v"]:
                handle.write(
                    f"v {p.x:.5f} {p.y:.5f} {p.z:.5f} {n.x:.4f} {n.y:.4f} {n.z:.4f} "
                    f"{t.x:.4f} {t.y:.4f} {t.z:.4f} {w:.0f} {u:.5f} {v:.5f}\n"
                )
            for a, b, c in block["f"]:
                handle.write(f"f {a} {b} {c}\n")
            lo = [min(x[0][i] for x in block["v"]) for i in range(3)]
            hi = [max(x[0][i] for x in block["v"]) for i in range(3)]
            uvmax = max(max(abs(x[4]), abs(x[5])) for x in block["v"])
            flag = "  OVER 65535" if len(block["v"]) > 65535 else ""
            print(f"{material}: {len(block['v'])} vertices, {len(block['f'])} triangles, "
                  f"bounds {[round(a, 2) for a in lo]}..{[round(a, 2) for a in hi]}, max |uv| {uvmax:.2f}{flag}")
    print(f"wrote {out}")


main()
