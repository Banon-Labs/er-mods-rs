"""Restore the First Step church by repeating the ruin's own most intact bays.

    blender --background target/church-restore/first-step-church-ruin.blend \
        --python scripts/blender/kitbash-church.py -- <out.blend>

The ruin is a kit: both arcades and both aisle walls are the same few models (507/508 arcade,
506 aisle wall) placed in 180-degree pairs, and each copy is broken in the same places. So the
restored walls are made of the one bay of each line that still stands to full height, cut out
of the ruin with planes and repeated on the bay grid, the far side turned 180 degrees the way
From's own pairs are. Those copies carry the ruin's real columns, capitals, moulded arches,
plinths and pilasters; what no bay keeps anywhere (the top of the walls, the roofs, the
bellcote) comes from `restore-church.py`, made flush with the cut copies.

Measured in the tower frame (`AEG004_501_1200`, y up, z along the nave) with `wall-profile.py`
and `wall-elevation.py`:

- left arcade columns at z 8.58, 14.58 (and 26.88, 32.88 on the 508 piece), centre x -5.70;
  right arcade columns at z 8.78, 14.88, 20.88, 26.88, centre x 5.72;
- the left arcade bay z 8.58..14.58 stands complete to y 10.9;
- the right aisle wall z 8.5..12.5 stands complete to y 9.9;
- the arcades run from the facade mass (z 3.05) to the east wall (z 37.9); the aisle walls
  between the corner pieces 503/504/559 (z ~9.7..30.7), which stay as they are.

The ruin pieces this replaces (506, 507, 508) are deleted from the scene; in game their
models are stripped to draw nothing and their collision stays.
"""

import importlib.util
import os
import sys

import bmesh
import bpy
from mathutils import Matrix, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("restore", os.path.join(HERE, "restore-church.py"))
restore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore)

TOWER = restore.TOWER
REPLACED = ("AEG004_506_", "AEG004_507_", "AEG004_508_")
DROP = ("m60_fort_230", "AEG004_501_ID13")  # ivy and floor rubble

BAY = 6.0
ARCADE_SOURCE = {"x": (-7.4, -3.6), "y": (-1.0, 10.9), "z": (8.58, 14.58)}
ARCADE_LEFT = {"x0": -5.70, "first": 2.58}
ARCADE_RIGHT = {"x0": 5.72, "first": 2.85}
ARCADE_RUN = (3.05, 37.9)
ARCADE_TOP = 13.0

OUTER_SOURCE = {"x": (7.3, 11.2), "y": (-1.0, 9.9), "z": (8.6, 12.5)}
OUTER_STRIP = OUTER_SOURCE["z"][1] - OUTER_SOURCE["z"][0]
OUTER_RIGHT_RUN = (9.9, 31.1)
OUTER_LEFT_RUN = (9.5, 30.8)
OUTER_TOP = restore.OUTER_TOP


def material_index(mats, mat):
    if mat not in mats:
        mats.append(mat)
    return mats.index(mat)


def gather(frame, holders, mats):
    """Every mesh under `holders`, in `frame`, as one bmesh using the shared material list."""
    bm = bmesh.new()
    for holder in holders:
        for obj in holder.children_recursive:
            if obj.type != "MESH":
                continue
            part = bmesh.new()
            part.from_mesh(obj.data)
            part.transform(frame @ obj.matrix_world)
            for face in part.faces:
                slot = obj.material_slots[face.material_index].material
                face.material_index = material_index(mats, slot)
            mesh = bpy.data.meshes.new("tmp")
            part.to_mesh(mesh)
            part.free()
            bm.from_mesh(mesh)
            bpy.data.meshes.remove(mesh)
    return bm


def clip(bm, lo, hi):
    """Keep only what lies inside the box lo..hi, cutting faces at its planes."""
    for axis in range(3):
        normal = Vector([1.0 if i == axis else 0.0 for i in range(3)])
        for value, sign in ((hi[axis], 1.0), (lo[axis], -1.0)):
            if value is None:
                continue
            co = Vector((0.0, 0.0, 0.0))
            co[axis] = value
            geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
            bmesh.ops.bisect_plane(bm, geom=geom, plane_co=co, plane_no=normal * sign, clear_outer=True)


def copy_of(bm):
    out = bm.copy()
    return out


def module(src, box, mats):
    bm = copy_of(src)
    clip(bm, (box["x"][0], box["y"][0], box["z"][0]), (box["x"][1], box["y"][1], box["z"][1]))
    drop = [f for f in bm.faces if mats[f.material_index].name.split(".")[0] in DROP]
    bmesh.ops.delete(bm, geom=drop, context="FACES")
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
    return bm


def turn(matrix_world_shift, half_turn):
    """Translation, after an optional 180-degree turn about y (x, z negated)."""
    rot = Matrix.Diagonal((-1.0, 1.0, -1.0, 1.0)) if half_turn else Matrix.Identity(4)
    return Matrix.Translation(matrix_world_shift) @ rot


def lay(out, piece, matrix, z_run):
    """Append `piece` moved by `matrix` into `out`, cut to the z run."""
    bm = copy_of(piece)
    bm.transform(matrix)
    clip(bm, (None, None, z_run[0]), (None, None, z_run[1]))
    mesh = bpy.data.meshes.new("tmp")
    bm.to_mesh(mesh)
    bm.free()
    out.from_mesh(mesh)
    bpy.data.meshes.remove(mesh)


def to_object(name, bm, mats, holder):
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    for mat in mats:
        mesh.materials.append(mat)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.parent = holder
    obj.matrix_parent_inverse.identity()
    return obj


def face_extent(bm, mats, material, y_band, z_band):
    """x range of `material` vertices in a y and z band: where a wall's two faces stand."""
    xs = [v.co.x for f in bm.faces if mats[f.material_index].name.startswith(material) for v in f.verts
          if y_band[0] <= v.co.y <= y_band[1] and z_band[0] <= v.co.z <= z_band[1]]
    return (min(xs), max(xs)) if xs else None


def metres_per_uv(bm, mats, material):
    """Median world length per UV unit of a material's faces, so new faces match its tiling."""
    uv_layer = bm.loops.layers.uv.active
    ratios = []
    for f in bm.faces:
        if not mats[f.material_index].name.startswith(material):
            continue
        for loop in f.loops:
            a, b = loop, loop.link_loop_next
            d3 = (a.vert.co - b.vert.co).length
            d2 = (a[uv_layer].uv - b[uv_layer].uv).length
            if d3 > 0.2 and d2 > 1e-4:
                ratios.append(d3 / d2)
    ratios.sort()
    return ratios[len(ratios) // 2] if ratios else None


def main():
    out = sys.argv[sys.argv.index("--") + 1]
    elsewhere = set()
    for obj in bpy.data.objects:
        if obj.name.startswith("AEG004_95"):
            elsewhere.update([obj, *obj.children_recursive])
    bpy.data.batch_remove(elsewhere)
    print(f"ivy faces removed: {restore.strip_ivy()}")

    holder = bpy.data.objects[TOWER]
    frame = holder.matrix_world.inverted()
    church = [o for o in bpy.data.objects if o.type == "EMPTY" and o.name.startswith(("AEG004_5", "AEG007_55"))]
    mats = []
    src = gather(frame, church, mats)

    arcade = module(src, ARCADE_SOURCE, mats)
    outer = module(src, OUTER_SOURCE, mats)
    wall_metres = metres_per_uv(arcade, mats, restore.WALL_MATERIAL)
    print(f"{restore.WALL_MATERIAL}: {wall_metres:.2f} m per UV unit on the cut bay")

    arcades = bmesh.new()
    left_x0 = ARCADE_LEFT["x0"]
    for k in range(7):
        z0 = ARCADE_LEFT["first"] + BAY * k
        shift = Vector((ARCADE_LEFT["x0"] - left_x0, 0.0, z0 - ARCADE_SOURCE["z"][0]))
        lay(arcades, arcade, turn(shift, False), ARCADE_RUN)
        z0 = ARCADE_RIGHT["first"] + BAY * k
        # Turned 180 degrees, the source window z0..z1 lands on -z1..-z0 and x on -x.
        shift = Vector((ARCADE_RIGHT["x0"] + left_x0, 0.0, z0 + ARCADE_SOURCE["z"][1]))
        lay(arcades, arcade, turn(shift, True), ARCADE_RUN)

    outers = bmesh.new()
    k = 0
    while True:
        z0 = OUTER_RIGHT_RUN[0] + OUTER_STRIP * k
        if z0 >= OUTER_RIGHT_RUN[1]:
            break
        lay(outers, outer, turn(Vector((0.0, 0.0, z0 - OUTER_SOURCE["z"][0])), False), OUTER_RIGHT_RUN)
        k += 1
    right_face = face_extent(outer, mats, restore.WALL_MATERIAL, (8.5, 9.9), OUTER_SOURCE["z"])
    k = 0
    while True:
        z0 = OUTER_LEFT_RUN[0] + OUTER_STRIP * k
        if z0 >= OUTER_LEFT_RUN[1]:
            break
        shift = Vector((restore.OUTER_X[0] + restore.OUTER_X[1], 0.0, z0 + OUTER_SOURCE["z"][1]))
        lay(outers, outer, turn(shift, True), OUTER_LEFT_RUN)
        k += 1

    kb = [to_object("kb_arcades", arcades, mats, holder), to_object("kb_outer", outers, mats, holder)]
    arcade_face = face_extent(arcade, mats, restore.WALL_MATERIAL, (10.3, 10.9), (10.5, 12.5))
    print(f"arcade spandrel faces at x {arcade_face}, aisle wall faces at x {right_face}")

    # Retire the replaced ruin pieces from the scene.
    doomed = set()
    for obj in bpy.data.objects:
        if obj.name.startswith(REPLACED):
            doomed.update([obj, *obj.children_recursive])
    bpy.data.batch_remove(doomed)

    restore.TEXTURE_METRES[restore.WALL_MATERIAL] = wall_metres
    # Flush tops: the cut bays end at y 10.9 / 9.9, the new courses carry on with the faces'
    # measured thickness.
    restore.ARCADE_HALF = (arcade_face[1] - arcade_face[0]) / 2
    parts = restore.build(holder)
    for obj in list(parts):
        if obj.name.startswith("restore_arcade_"):
            parts.remove(obj)
            bpy.data.objects.remove(obj)
    wall = restore.material(restore.WALL_MATERIAL)
    trim = restore.material(restore.TRIM_MATERIAL)
    tops = []
    half = restore.ARCADE_HALF
    for side, x in (("L", left_x0 + 0.0), ("R", ARCADE_RIGHT["x0"])):
        centre = (arcade_face[0] + arcade_face[1]) / 2 - left_x0 + x if side == "L" else -((arcade_face[0] + arcade_face[1]) / 2 - left_x0) + x
        tops.append(restore.box(f"restore_arcadetop_{side}", (centre - half, centre + half), (ARCADE_SOURCE["y"][1], ARCADE_TOP), ARCADE_RUN, wall))
        tops.append(restore.box(f"restore_arcadecap_{side}", (centre - half - 0.12, centre + half + 0.12), (ARCADE_TOP - 0.25, ARCADE_TOP), ARCADE_RUN, trim))
    for obj in tops:
        obj.parent = holder
        obj.matrix_parent_inverse.identity()
        restore.project_uvs(obj)
    for obj in kb + parts + tops:
        print(f"{obj.name}: {len(obj.data.vertices)} verts, {len(obj.data.polygons)} faces")
    bpy.ops.wm.save_as_mainfile(filepath=out)
    print(f"saved {out}")


main()
