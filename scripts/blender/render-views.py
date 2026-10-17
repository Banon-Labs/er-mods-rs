"""Render a .blend from fixed views around the objects whose name matches a regex.

    blender --background <scene.blend> --python scripts/blender/render-views.py -- \
        <name-regex> <out-dir> [--resolution N] [--light studio|flat|matcap] [--frame <object>]

Workbench engine with texture colour, so a headless render takes seconds. Views: top
orthographic, four orthographic sides, and four perspective three-quarter views, all framed on
the world-space bounding box of the matched objects' meshes (children included). With
`--frame`, the side views are square to that object's axes instead of the world's, so a
building's elevations come out face-on.
"""

import argparse
import math
import os
import re
import sys

import bpy
from mathutils import Matrix, Vector


def matched_meshes(pattern):
    roots = [o for o in bpy.data.objects if re.match(pattern, o.name)]
    meshes = set()
    for root in roots:
        for obj in [root, *root.children_recursive]:
            if obj.type == "MESH":
                meshes.add(obj)
    return meshes


def bounds(meshes):
    lo = Vector((math.inf,) * 3)
    hi = Vector((-math.inf,) * 3)
    for obj in meshes:
        for corner in obj.bound_box:
            p = obj.matrix_world @ Vector(corner)
            lo = Vector(map(min, lo, p))
            hi = Vector(map(max, hi, p))
    return lo, hi


def heading(frame_name):
    """Yaw of an object's local X axis around world Z, so views can square up to it."""
    if not frame_name:
        return 0.0
    x_axis = bpy.data.objects[frame_name].matrix_world.to_3x3() @ Vector((1, 0, 0))
    return math.atan2(x_axis.y, x_axis.x)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pattern")
    parser.add_argument("out_dir")
    parser.add_argument("--resolution", type=int, default=1200)
    parser.add_argument("--light", default="flat")
    parser.add_argument("--frame")
    parser.add_argument("--only", help="comma-separated view names to render")
    parser.add_argument("--eye", help="x,y,z in --frame space: render one view from here instead")
    parser.add_argument("--target", help="x,y,z in --frame space the --eye view looks at")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    os.makedirs(args.out_dir, exist_ok=True)
    meshes = matched_meshes(args.pattern)
    for obj in bpy.data.objects:
        if obj.type == "MESH":
            obj.hide_render = obj not in meshes
    lo, hi = bounds(meshes)
    centre = (lo + hi) / 2
    size = (hi - lo).length
    print(f"bounds {tuple(round(v, 2) for v in lo)} .. {tuple(round(v, 2) for v in hi)}")

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = args.light.upper()
    scene.display.shading.color_type = "TEXTURE"
    scene.display.shading.show_cavity = True
    scene.render.resolution_x = args.resolution
    scene.render.resolution_y = args.resolution
    scene.world = scene.world or bpy.data.worlds.new("world")
    scene.world.color = (0.55, 0.62, 0.7)

    cam_data = bpy.data.cameras.new("view")
    cam = bpy.data.objects.new("view", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    cam_data.clip_end = size * 10

    yaw = heading(args.frame)
    spin = Matrix.Rotation(yaw, 3, "Z")
    views = [("top", Vector((0, 0, 1)), True)]
    for name, angle in (("side_a", 0), ("end_a", 90), ("side_b", 180), ("end_b", 270)):
        a = math.radians(angle)
        views.append((name, spin @ Vector((math.sin(a), -math.cos(a), 0)), True))
    for name, angle in (("persp_1", 315), ("persp_2", 45), ("persp_3", 135), ("persp_4", 225)):
        a = math.radians(angle)
        views.append((name, spin @ Vector((math.sin(a), -math.cos(a), 0.6)).normalized(), False))
    wanted = set(args.only.split(",")) if args.only else None
    if args.eye:
        # An extra perspective view from a point in the --frame object's local space.
        to_world = bpy.data.objects[args.frame].matrix_world
        eye = to_world @ Vector(map(float, args.eye.split(",")))
        target = to_world @ Vector(map(float, args.target.split(",")))
        cam_data.type = "PERSP"
        cam_data.lens = 18
        cam.location = eye
        cam.rotation_euler = (target - eye).to_track_quat("-Z", "Y").to_euler()
        scene.render.filepath = os.path.join(args.out_dir, "eye.png")
        bpy.ops.render.render(write_still=True)
        print(f"wrote {scene.render.filepath}")
        return

    for name, direction, ortho in views:
        if wanted and name not in wanted:
            continue
        cam_data.type = "ORTHO" if ortho else "PERSP"
        cam_data.ortho_scale = size * 1.02
        cam_data.lens = 40
        distance = size * (1.3 if ortho else 1.05)
        cam.location = centre + direction * distance
        if name == "top":
            cam.rotation_euler = (0, 0, yaw)
        else:
            # A camera looks down its local -Z with local +Y as screen-up.
            cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", "Y").to_euler()
        scene.render.filepath = os.path.join(args.out_dir, f"{name}.png")
        bpy.ops.render.render(write_still=True)
        print(f"wrote {scene.render.filepath}")


main()
