"""Save every image a .blend uses as a small PNG, named after the materials that use it.

    blender --background <scene.blend> --python scripts/blender/export-textures.py -- <out-dir> [size]

For looking at what each material actually is (stone, trim, ivy, roof) without opening the GUI:
Blender decodes the BCn DDS files itself, which most image tools on this machine cannot.
"""

import os
import sys

import bpy


def main():
    args = sys.argv[sys.argv.index("--") + 1:]
    out_dir = args[0]
    size = int(args[1]) if len(args) > 1 else 256
    os.makedirs(out_dir, exist_ok=True)
    users = {}
    for mat in bpy.data.materials:
        if not mat.node_tree:
            continue
        for node in mat.node_tree.nodes:
            if node.type == "TEX_IMAGE" and node.image:
                users.setdefault(node.image.name, []).append(mat.name)
    for image in list(bpy.data.images):
        if not image.size[0]:
            continue
        copy = image.copy()
        copy.scale(size, size)
        names = "+".join(sorted(users.get(image.name, ["unused"])))
        path = os.path.join(out_dir, f"{names}__{os.path.splitext(image.name)[0]}.png")
        copy.filepath_raw = path
        copy.file_format = "PNG"
        copy.save()
        print(f"wrote {path}")


main()
