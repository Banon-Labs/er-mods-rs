"""Assemble MSB-placed ELDEN RING assets into a textured Blender scene.

Run headless:

    blender --background --python scripts/blender/assemble-msb-building.py -- \
        <msb-part-asset-dir> <obj-dir> <part-name-regex> <out.blend>

Inputs: the Witchy-unpacked MSB part XMLs for one map tile, and OBJs written by
`cargo run -p er-flver --example flver-obj` (X already negated into right-handed space).
Each part becomes an object at its MSB position, with the yaw negated to match that mirror
(the sign `scripts/er-msb-assembly-check.py` measures). Everything hangs under one empty that
turns the game's Y-up into Blender's Z-up, so the raw game frame is one parent away.

Materials: each OBJ `usemtl` name is a matbin; its first `*_a.tif` albedo is looked up as an
unpacked `.dds` under the asset texture tree and wired into a Principled BSDF. Missing textures
leave a plain grey material and are listed at the end.
"""

import math
import os
import re
import sys
import xml.etree.ElementTree as ET

import bpy

EXTRACT = os.environ.get("ER_EXTRACT_ROOT", os.path.expanduser("~/er-extract"))
SHARDED = os.path.join(EXTRACT, "LOOK_HERE_WITCHY_RECURSIVE_20260713/sharded")
MATBIN_ROOT = os.path.join(SHARDED, "material")
TEXTURE_ROOT = os.path.join(SHARDED, "asset/aet")


def index_files(root, suffix):
    found = {}
    for directory, _, files in os.walk(root):
        for name in files:
            if name.endswith(suffix):
                found.setdefault(name, os.path.join(directory, name))
    return found


def albedo_for(material, matbins, textures):
    path = matbins.get(material + ".matbin.xml")
    if not path:
        return None
    with open(path, encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    for tif in re.findall(r"<Path>([^<]+)</Path>", text):
        name = tif.rsplit("\\", 1)[-1]
        if name.lower().endswith("_a.tif"):
            dds = textures.get(name[:-4] + ".dds")
            if dds:
                return dds
    return None


def part_transforms(msb_dir, pattern):
    for name in sorted(os.listdir(msb_dir)):
        if not re.match(pattern, name):
            continue
        root = ET.parse(os.path.join(msb_dir, name)).getroot()

        def vec(tag):
            node = root.find(tag)
            return tuple(float(node.find(axis).text) for axis in "XYZ")

        yield root.find("Name").text, root.find("ModelName").text, vec("Position"), vec("Rotation"), vec("Scale")


def main():
    msb_dir, obj_dir, pattern, out_blend = sys.argv[sys.argv.index("--") + 1:][:4]
    bpy.ops.wm.read_factory_settings(use_empty=True)
    matbins = index_files(MATBIN_ROOT, ".matbin.xml")
    textures = index_files(TEXTURE_ROOT, ".dds")

    root = bpy.data.objects.new("game_frame_y_up", None)
    root.rotation_euler = (math.radians(90), 0, 0)
    bpy.context.scene.collection.objects.link(root)

    materials = {}
    missing = set()
    for name, model, pos, rot, scale in part_transforms(msb_dir, pattern):
        obj_path = os.path.join(obj_dir, model + ".obj")
        if not os.path.exists(obj_path):
            print(f"skip {name}: no {obj_path}")
            continue
        before = set(bpy.data.objects)
        bpy.ops.wm.obj_import(filepath=obj_path, forward_axis="Y", up_axis="Z")
        imported = [o for o in bpy.data.objects if o not in before]
        holder = bpy.data.objects.new(name, None)
        bpy.context.scene.collection.objects.link(holder)
        holder.parent = root
        holder.location = (-pos[0], pos[1], pos[2])
        holder.rotation_mode = "YXZ"
        holder.rotation_euler = (math.radians(rot[0]), -math.radians(rot[1]), -math.radians(rot[2]))
        holder.scale = scale
        for obj in imported:
            obj.parent = holder
            for slot in obj.material_slots:
                mat = slot.material
                if mat is None:
                    continue
                key = mat.name.split(".")[0]
                if key not in materials:
                    dds = albedo_for(key, matbins, textures)
                    mat.use_nodes = True
                    bsdf = mat.node_tree.nodes.get("Principled BSDF")
                    if dds and bsdf:
                        tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
                        tex.image = bpy.data.images.load(dds)
                        mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
                    elif not dds:
                        missing.add(key)
                    materials[key] = mat
                slot.material = materials[key]
        print(f"placed {name} ({model}) with {len(imported)} meshes")

    bpy.ops.wm.save_as_mainfile(filepath=out_blend)
    print(f"saved {out_blend}; materials {len(materials)}, without albedo: {sorted(missing)}")


main()
