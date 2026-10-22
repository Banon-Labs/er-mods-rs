"""Restore the First Step church (m60_42_36) in Blender: strip the ivy, add the missing masonry
and put a roof on it.

    blender --background target/church-restore/first-step-church-ruin.blend \
        --python scripts/blender/restore-church.py -- <out.blend>

Input is the scene `assemble-msb-building.py` builds. Every dimension below was measured in the
local frame of the tower part `AEG004_501_1200` (x across the nave, y up, z along it, from the
facade at z~0 to the east wall at z~40) with `building-plan.py` and `wall-elevation.py`:

- both arcades stand on a 6 m bay grid, piers centred at z = 9, 15, 21, 27, 33, arches springing
  at y 7 with crowns near y 10, wall tops near y 13; arcade walls are 1 m thick at x -5.38 / 5.48;
- the outer aisle walls are about 1.2 m thick at x -9.1 / 8.75, top near y 10.8, with no openings;
- the east wall runs z 38..40, with two small aisle windows each side;
- the tower ends in a 4 m wide, 1 m deep bellcote (x -1.75..2.25, z 2.55..3.45) broken at y ~30-32.

New masonry is built slightly inside the ruin's faces, so wherever the ruin still stands its own
carved surface shows and the new wall only appears in the gaps and above the broken tops.
Everything new is a child of the tower holder with an identity local transform, so its vertex
coordinates are the tower-local frame: the same frame a FLVER placed at that part would use, with
x negated back.
"""

import math
import sys

import bmesh
import bpy

TOWER = "AEG004_501_1200"
IVY_MATERIAL = "m60_fort_230"
WALL_MATERIAL = "AEG004_501_ID01"
TRIM_MATERIAL = "AEG004_501_ID05"
ROOF_MATERIAL = "AEG004_501_ID02"
TEXTURE_METRES = {WALL_MATERIAL: 3.0, TRIM_MATERIAL: 2.0, ROOF_MATERIAL: 2.5}

NAVE_CENTRE = 0.05
ARCADE_X = (-5.38, 5.48)
ARCADE_HALF = 0.45
ARCADE_TOP = 13.0
ARCADE_BAYS = [(3.4, 8.6), (9.4, 14.6), (15.4, 20.6), (21.4, 26.6), (27.4, 32.6), (33.4, 37.6)]
ARCH_SPRING = 7.0
ARCH_CROWN = 10.1
OUTER_X = (-9.1, 8.75)
OUTER_HALF = 0.55
OUTER_TOP = 10.8
EAST_Z = (38.4, 40.2)
RIDGE = 18.5
NAVE_LENGTH = (0.0, 40.6)
AISLE_LENGTH = (-0.6, 40.6)


def material(name):
    mat = bpy.data.materials.get(name)
    if mat is None:
        raise SystemExit(f"material {name} not in the scene")
    return mat


def strip_ivy():
    removed = 0
    for obj in list(bpy.data.objects):
        if obj.type != "MESH":
            continue
        slots = [i for i, slot in enumerate(obj.material_slots) if slot.material and slot.material.name.startswith(IVY_MATERIAL)]
        if not slots:
            continue
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        doomed = [f for f in bm.faces if f.material_index in slots]
        removed += len(doomed)
        bmesh.ops.delete(bm, geom=doomed, context="FACES")
        loose = [v for v in bm.verts if not v.link_faces]
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
        bm.to_mesh(obj.data)
        bm.free()
        if not obj.data.polygons:
            bpy.data.objects.remove(obj)
    return removed


def prism(name, profile, axis, lo, hi, mat):
    """Extrude a closed 2D profile between two planes. Axis `x`: profile is (z, y); `z`: (x, y)."""
    bm = bmesh.new()
    rings = []
    for depth in (lo, hi):
        ring = []
        for u, y in profile:
            co = (depth, y, u) if axis == "x" else (u, y, depth)
            ring.append(bm.verts.new(co))
        rings.append(ring)
    n = len(profile)
    bm.faces.new(rings[0][::-1])
    bm.faces.new(rings[1])
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((rings[0][i], rings[0][j], rings[1][j], rings[1][i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    mesh.materials.append(mat)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def box(name, x, y, z, mat):
    return prism(name, [(x[0], y[0]), (x[1], y[0]), (x[1], y[1]), (x[0], y[1])], "z", z[0], z[1], mat)


def pointed_arch(left, right, sill, spring, crown, steps=10):
    """Outline of a pointed opening in (u, y), counter-clockwise from the bottom left."""
    a = (right - left) / 2
    mid = left + a
    h = crown - spring
    c = (h * h - a * a) / (2 * a)
    r = a + c
    pts = [(left, sill), (right, sill), (right, spring)]
    # Right half: arc centred at (mid - c, spring) from the right jamb up to the crown.
    start = 0.0
    end = math.atan2(h, c)
    for i in range(1, steps):
        t = start + (end - start) * i / steps
        pts.append((mid - c + r * math.cos(t), spring + r * math.sin(t)))
    pts.append((mid, crown))
    end = math.pi - math.atan2(h, c)
    for i in range(1, steps):
        t = end + (math.pi - end) * i / steps
        pts.append((mid + c + r * math.cos(t), spring + r * math.sin(t)))
    pts.append((left, spring))
    return pts


def cut(target, cutters):
    for cutter in cutters:
        mod = target.modifiers.new("cut", "BOOLEAN")
        mod.operation = "DIFFERENCE"
        mod.solver = "EXACT"
        mod.object = cutter
        bpy.context.view_layer.objects.active = target
        bpy.ops.object.modifier_apply(modifier=mod.name)
        bpy.data.objects.remove(cutter)


def project_uvs(obj):
    """Planar UVs per face from its dominant axis, scaled so each material tiles at its real size."""
    mesh = obj.data
    if not mesh.uv_layers:
        mesh.uv_layers.new(name="UVMap")
    uv = mesh.uv_layers.active.data
    for poly in mesh.polygons:
        mat = mesh.materials[poly.material_index]
        metres = TEXTURE_METRES.get(mat.name, 3.0)
        n = poly.normal
        axis = max(range(3), key=lambda i: abs(n[i]))
        for li in poly.loop_indices:
            co = mesh.vertices[mesh.loops[li].vertex_index].co
            if axis == 0:
                u, v = co.z, co.y
            elif axis == 1:
                u, v = co.x, co.z
            else:
                u, v = co.x, co.y
            uv[li].uv = (u / metres, v / metres)


def build(holder):
    wall = material(WALL_MATERIAL)
    trim = material(TRIM_MATERIAL)
    roof = material(ROOF_MATERIAL)
    parts = []

    for side, x in zip(("L", "R"), ARCADE_X):
        body = box(f"restore_arcade_{side}", (x - ARCADE_HALF, x + ARCADE_HALF), (0.0, ARCADE_TOP), (3.0, 38.0), wall)
        cutters = []
        for i, (lo, hi) in enumerate(ARCADE_BAYS):
            outline = pointed_arch(lo, hi, -0.5, ARCH_SPRING, ARCH_CROWN)
            cutters.append(prism(f"cut{i}", outline, "x", x - 1.0, x + 1.0, wall))
        cut(body, cutters)
        parts.append(body)

    for side, x in zip(("L", "R"), OUTER_X):
        parts.append(box(f"restore_outer_{side}", (x - OUTER_HALF, x + OUTER_HALF), (0.0, OUTER_TOP), (-0.5, 40.4), wall))
        parts.append(box(f"restore_cornice_{side}", (x - OUTER_HALF - 0.2, x + OUTER_HALF + 0.2), (OUTER_TOP, OUTER_TOP + 0.3), (-0.7, 40.6), trim))

    left_outer = OUTER_X[0] - OUTER_HALF
    right_outer = OUTER_X[1] + OUTER_HALF
    gable = [
        (left_outer, 0.0), (right_outer, 0.0), (right_outer, OUTER_TOP), (ARCADE_X[1] + ARCADE_HALF, ARCADE_TOP),
        (NAVE_CENTRE, RIDGE), (ARCADE_X[0] - ARCADE_HALF, ARCADE_TOP), (left_outer, OUTER_TOP),
    ]
    east = prism("restore_east_wall", [(x, y) for x, y in gable], "z", EAST_Z[0], EAST_Z[1], wall)
    windows = []
    for x0 in (6.0, -6.9):
        for sill, spring, crown in ((3.0, 4.0, 4.6), (7.0, 8.0, 8.6)):
            windows.append(prism("cutw", pointed_arch(x0, x0 + 0.9, sill, spring, crown, 6), "z", EAST_Z[0] - 1, EAST_Z[1] + 1, wall))
    cut(east, windows)
    parts.append(east)
    # The facade below y 12 still stands, door and windows included, so only its gable is new.
    west_gable = [
        (ARCADE_X[0] - ARCADE_HALF, 12.0), (ARCADE_X[1] + ARCADE_HALF, 12.0), (ARCADE_X[1] + ARCADE_HALF, ARCADE_TOP),
        (NAVE_CENTRE, RIDGE), (ARCADE_X[0] - ARCADE_HALF, ARCADE_TOP),
    ]
    parts.append(prism("restore_west_gable", west_gable, "z", -0.5, 1.5, wall))

    thickness = 0.25
    for side, eave_x in (("L", ARCADE_X[0] - ARCADE_HALF - 0.5), ("R", ARCADE_X[1] + ARCADE_HALF + 0.5)):
        slope = (RIDGE - ARCADE_TOP) / (NAVE_CENTRE - (ARCADE_X[0] - ARCADE_HALF if side == "L" else ARCADE_X[1] + ARCADE_HALF))
        eave_y = ARCADE_TOP + slope * (eave_x - (ARCADE_X[0] - ARCADE_HALF if side == "L" else ARCADE_X[1] + ARCADE_HALF))
        profile = [(eave_x, eave_y), (NAVE_CENTRE, RIDGE), (NAVE_CENTRE, RIDGE + thickness), (eave_x, eave_y + thickness)]
        parts.append(prism(f"restore_nave_roof_{side}", profile, "z", *NAVE_LENGTH, roof))

    for side, inner, outer in (("L", ARCADE_X[0] - ARCADE_HALF, left_outer - 0.4), ("R", ARCADE_X[1] + ARCADE_HALF, right_outer + 0.4)):
        top = ARCADE_TOP - 0.05
        bottom = OUTER_TOP + 0.3 - (0.4 * (ARCADE_TOP - OUTER_TOP) / abs(inner - outer))
        profile = [(inner, top), (outer, bottom), (outer, bottom + thickness), (inner, top + thickness)]
        parts.append(prism(f"restore_aisle_roof_{side}", profile, "z", *AISLE_LENGTH, roof))

    bell = box("restore_bellcote", (-1.6, 2.1), (27.0, 32.5), (2.6, 3.4), wall)
    openings = [prism("cutb", pointed_arch(lo, hi, 28.4, 29.8, 30.5, 6), "z", 2.0, 4.0, wall) for lo, hi in ((-1.05, -0.2), (0.2, 1.05))]
    cut(bell, openings)
    parts.append(bell)
    parts.append(prism("restore_bellcote_gable", [(-1.75, 32.5), (2.25, 32.5), (0.25, 34.3)], "z", 2.55, 3.45, trim))
    parts.append(box("restore_bellcote_finial", (0.1, 0.4), (34.0, 35.4), (2.85, 3.15), trim))
    parts.append(box("restore_bellcote_cross", (-0.25, 0.75), (34.85, 35.1), (2.85, 3.15), trim))

    collection = bpy.data.collections.new("restoration")
    bpy.context.scene.collection.children.link(collection)
    for obj in parts:
        if obj.name not in collection.objects:
            for other in obj.users_collection:
                other.objects.unlink(obj)
            collection.objects.link(obj)
        obj.parent = holder
        obj.matrix_parent_inverse.identity()
        project_uvs(obj)
    return parts


def main():
    out = sys.argv[sys.argv.index("--") + 1]
    # The AEG004_95x parts on this tile are a different structure 170 m away.
    elsewhere = set()
    for obj in bpy.data.objects:
        if obj.name.startswith("AEG004_95"):
            elsewhere.update([obj, *obj.children_recursive])
    bpy.data.batch_remove(elsewhere)
    removed = strip_ivy()
    print(f"ivy faces removed: {removed}")
    holder = bpy.data.objects[TOWER]
    parts = build(holder)
    for obj in parts:
        print(f"{obj.name}: {len(obj.data.vertices)} verts, {len(obj.data.polygons)} faces")
    bpy.ops.wm.save_as_mainfile(filepath=out)
    print(f"saved {out}")


# `kitbash-church.py` imports the builders above.
if __name__ == "__main__":
    main()
