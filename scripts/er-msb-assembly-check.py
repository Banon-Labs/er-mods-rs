#!/usr/bin/env python3
"""Check which Y-rotation sign assembles MSB-placed asset OBJs into one connected building.

The OBJs come from `cargo run -p er-flver --example flver-obj`, which negates X to turn the
game's left-handed space right-handed. A yaw therefore flips sign too, and this measures that
instead of assuming it: for each sign, every piece is placed at its MSB transform and the
nearest-vertex gap from each piece to any other is summed. The sign that assembles the building
has gaps near zero, because the pieces were modelled to meet.

Usage: er-msb-assembly-check.py <msb-part-dir> <obj-dir> <part-name-regex>
"""

import math
import os
import re
import sys
import xml.etree.ElementTree as ET


def read_obj(path, step=7):
    verts = []
    with open(path) as handle:
        for i, line in enumerate(handle):
            if line.startswith("v "):
                verts.append(tuple(float(x) for x in line.split()[1:4]))
    return verts[::step]


def parts(msb_dir, pattern):
    out = []
    for name in sorted(os.listdir(msb_dir)):
        if not re.match(pattern, name):
            continue
        root = ET.parse(os.path.join(msb_dir, name)).getroot()

        def vec(tag):
            node = root.find(tag)
            return tuple(float(node.find(axis).text) for axis in "XYZ")

        out.append((root.find("Name").text, root.find("ModelName").text, vec("Position"), vec("Rotation"), vec("Scale")))
    return out


def place(verts, pos, rot, scale, sign):
    yaw = math.radians(rot[1]) * sign
    c, s = math.cos(yaw), math.sin(yaw)
    px, py, pz = -pos[0], pos[1], pos[2]
    placed = []
    for x, y, z in verts:
        x, y, z = x * scale[0], y * scale[1], z * scale[2]
        placed.append((c * x + s * z + px, y + py, -s * x + c * z + pz))
    return placed


def gap_score(pieces):
    total = 0.0
    for i, a in enumerate(pieces):
        best = math.inf
        for j, b in enumerate(pieces):
            if i == j:
                continue
            for p in a[::5]:
                for q in b[::5]:
                    d = (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2
                    if d < best:
                        best = d
        total += math.sqrt(best)
    return total


def main():
    msb_dir, obj_dir, pattern = sys.argv[1:4]
    found = parts(msb_dir, pattern)
    meshes = {model: read_obj(os.path.join(obj_dir, model + ".obj")) for _, model, *_ in found}
    for sign in (1, -1):
        pieces = [place(meshes[model], pos, rot, scale, sign) for _, model, pos, rot, scale in found]
        print(f"yaw sign {sign:+d}: summed nearest gap {gap_score(pieces):.2f} m over {len(pieces)} pieces")


if __name__ == "__main__":
    main()
