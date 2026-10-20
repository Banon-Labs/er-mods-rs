#!/usr/bin/env python3
"""Census of every Dark Souls Remastered chrbnd, against what mushroom-man-builder assumes of c2280.

Usage: dsr-chr-census.py <out-dir> [<dsr-chr-dir>]
       dsr-chr-census.py --selftest

mushroom-man-builder fits c2280 by bone name, takes only its first mesh, and copies exactly three
textures out of the chrbnd's own TPF. This reads every `c*.chrbnd.dcx` in the DSR install and
records, per character, whether each of those holds: the skeleton (how many of the bones
`export_c2280` looks up are present), mesh and material counts, vertex and LOD0 triangle totals,
and where the textures live (an embedded `.tpf`, or a `.chrtpfbhd` header with a sibling
`.chrtpfbdt`). Read-only over the install; writes `dsr-chr-census.tsv` and `dsr-bone-freq.txt`
into <out-dir>, never into the repo.

`--selftest` needs no game: it builds two chrbnds in a temporary directory -- a zlib DCX holding a
BND3 with a two-mesh FLVER (one mesh on a weapon bone) and a one-texture TPF, and one holding no
FLVER -- runs the census over them, and checks every column it fills.
"""

import collections
import glob
import os
import re
import struct
import sys
import tempfile
import zlib

DEFAULT_DSR_CHR = os.path.expanduser(
    "~/.local/share/Steam/steamapps/common/DARK SOULS REMASTERED/chr"
)

# The bones export_c2280 looks up by name (tools/mushroom-man-builder/src/fit/mushroom.rs).
C2280_BONES = ["Pelvis", "Spine1", "Spine2", "Spine3", "Neck", "Head", "LLeg1", "RLeg1"] + [
    f"{side}Arm{bone}"
    for side in "LR"
    for bone in ["1", "2", "Palm", "Digit12", "Digit22", "Digit32"]
]


def dcx(data):
    if data[:4] != b"DCX\0":
        raise ValueError(f"not a DCX: {data[:4]!r}")
    compressed = struct.unpack_from(">I", data, 0x20)[0]
    algorithm = data[0x28:0x2C]
    dca = data.find(b"DCA\0")
    start = dca + struct.unpack_from(">I", data, dca + 4)[0]
    if algorithm != b"DFLT":
        raise ValueError(f"DCX algorithm {algorithm!r}")
    return zlib.decompress(data[start : start + compressed])


def reverse_bits(byte):
    return int(f"{byte:08b}"[::-1], 2)


def bnd3(data):
    """Name -> bytes. Format byte decoding follows SoulsFormats' Binder.ReadFormat."""
    if data[:4] != b"BND3":
        raise ValueError(f"not a BND3: {data[:4]!r}")
    raw = data[0x0C]
    bit_big_endian = data[0x0E] != 0
    as_is = bit_big_endian or ((raw & 1) and not (raw & 0x80))
    fmt = raw if as_is else reverse_bits(raw)
    count = struct.unpack_from("<i", data, 0x10)[0]
    cursor = 0x20
    files = {}
    for _ in range(count):
        size = struct.unpack_from("<i", data, cursor + 4)[0]
        cursor += 8
        if fmt & 0x10:
            offset = struct.unpack_from("<Q", data, cursor)[0]
            cursor += 8
        else:
            offset = struct.unpack_from("<I", data, cursor)[0]
            cursor += 4
        if fmt & 0x02:
            cursor += 4
        name = ""
        if fmt & 0x0C:
            name_offset = struct.unpack_from("<I", data, cursor)[0]
            cursor += 4
            end = data.index(b"\0", name_offset)
            name = data[name_offset:end].decode("shift_jis", "replace")
        if fmt & 0x20:
            cursor += 4
        files[name.replace("\\", "/").split("/")[-1]] = data[offset : offset + size]
    return files


def utf16z(data, offset):
    end = offset
    while data[end : end + 2] != b"\0\0":
        end += 2
    return data[offset:end].decode("utf-16-le", "replace")


def flver(data):
    if data[:6] != b"FLVER\0":
        raise ValueError(f"not a FLVER: {data[:6]!r}")
    version = struct.unpack_from("<I", data, 0x08)[0]
    dummies, materials, bones, meshes, _ = struct.unpack_from("<5I", data, 0x14)
    bbox_min = struct.unpack_from("<3f", data, 0x28)
    bbox_max = struct.unpack_from("<3f", data, 0x34)
    face_set_count = struct.unpack_from("<I", data, 0x50)[0]
    bone_table = 0x80 + 0x40 * dummies + 0x20 * materials
    mesh_table = bone_table + 0x80 * bones
    face_set_table = mesh_table + 0x30 * meshes
    vertex_buffer_table = face_set_table + 0x20 * face_set_count
    names = []
    for i in range(bones):
        name_offset = struct.unpack_from("<I", data, bone_table + i * 0x80 + 0x0C)[0]
        names.append(utf16z(data, name_offset) if name_offset < len(data) else "")
    material_names = []
    for i in range(materials):
        name_offset, mtd_offset = struct.unpack_from("<II", data, 0x80 + 0x40 * dummies + 0x20 * i)
        material_names.append(
            (
                utf16z(data, name_offset) if name_offset < len(data) else "",
                utf16z(data, mtd_offset) if mtd_offset < len(data) else "",
            )
        )
    face_sets = []
    for i in range(face_set_count):
        flags, strip = struct.unpack_from("<IB", data, face_set_table + i * 0x20)
        index_count = struct.unpack_from("<I", data, face_set_table + i * 0x20 + 8)[0]
        face_sets.append((flags, strip, index_count))
    mesh_info = []
    for i in range(meshes):
        mesh = mesh_table + i * 0x30
        material = struct.unpack_from("<I", data, mesh + 4)[0]
        default_bone, mesh_bone_count, _, mesh_bone_offset = struct.unpack_from(
            "<iIII", data, mesh + 0x10
        )
        mesh_bones = struct.unpack_from(f"<{mesh_bone_count}i", data, mesh_bone_offset)
        # A rigid mesh lists no bones and hangs from its default bone.
        bound = {names[b] for b in mesh_bones if 0 <= b < len(names)} or (
            {names[default_bone]} if 0 <= default_bone < len(names) else set()
        )
        fs_count, fs_offset, vb_count, vb_offset = struct.unpack_from("<4I", data, mesh + 0x20)
        buffers = struct.unpack_from(f"<{vb_count}I", data, vb_offset)
        vertices = (
            struct.unpack_from("<I", data, vertex_buffer_table + buffers[0] * 0x20 + 0x0C)[0]
            if buffers
            else 0
        )
        lod0 = [face_sets[j] for j in struct.unpack_from(f"<{fs_count}I", data, fs_offset)]
        lod0 = [fs for fs in lod0 if fs[0] == 0]
        triangles = 0
        if lod0:
            _, strip, index_count = lod0[0]
            # A strip's count is an upper bound: restarts and degenerates are not subtracted.
            triangles = index_count - 2 if strip else index_count // 3
        mesh_info.append((material, vertices, triangles, bound))
    height = bbox_max[1] - bbox_min[1]
    return {
        "version": version,
        "bones": names,
        "material_names": material_names,
        "meshes": mesh_info,
        "materials": materials,
        "height": height if height > 0 else float("nan"),
    }


def tpf(data):
    if data[:4] != b"TPF\0":
        raise ValueError(f"not a TPF: {data[:4]!r}")
    count = struct.unpack_from("<I", data, 0x08)[0]
    encoding = data[0x0E]
    textures = []
    for i in range(count):
        entry = 0x10 + i * 0x14
        offset, size = struct.unpack_from("<II", data, entry)
        name_offset = struct.unpack_from("<I", data, entry + 12)[0]
        if encoding == 1:
            name = utf16z(data, name_offset)
        else:
            name = data[name_offset : data.index(b"\0", name_offset)].decode("shift_jis", "replace")
        dds = data[offset : offset + size]
        fourcc = dds[0x54:0x58].decode("latin1") if dds[:4] == b"DDS " else "?"
        textures.append((name, fourcc))
    return textures


# Bone and material names a held weapon, shield or staff hangs from in DSR: `R_weapon`,
# `L_Shield`, `R_Wepon_Case` (sic), `WP_A_0402`, `c2560_weapon_2`, `c5370_sword`, ...
WEAPON_NAME = re.compile(r"(?i)weapon|wepon|\bwep\b|wp_|shield|sword|wand|stick|arrow")


def is_weapon_mesh(mesh, material_names):
    """A mesh carried only by weapon bones, or whose material is named for a weapon."""
    material, _, _, bound = mesh
    name = material_names[material][0] if material < len(material_names) else ""
    return bool(WEAPON_NAME.search(name)) or (
        bool(bound) and all(WEAPON_NAME.search(bone) for bone in bound)
    )


def census(chr_dir):
    rows = []
    bone_freq = collections.Counter()
    for path in sorted(glob.glob(os.path.join(chr_dir, "c*.chrbnd.dcx"))):
        chr_id = os.path.basename(path).split(".")[0]
        with open(path, "rb") as fh:
            files = bnd3(dcx(fh.read()))
        flvers = sorted(n for n in files if n.lower().endswith(".flver"))
        tpfs = sorted(n for n in files if n.lower().endswith(".tpf"))
        tpfbhd = [n for n in files if n.lower().endswith(".chrtpfbhd")]
        bdt = os.path.exists(os.path.join(chr_dir, f"{chr_id}.chrtpfbdt"))
        if not flvers:
            rows.append({"chr": chr_id, "status": "no-flver"})
            continue
        model = flver(files[flvers[0]])
        bone_freq.update(set(model["bones"]))
        textures = [t for name in tpfs for t in tpf(files[name])]
        weapons = [m for m in model["meshes"] if is_weapon_mesh(m, model["material_names"])]
        held = [
            f"{model['material_names'][m[0]][0]}@{'+'.join(sorted(m[3]))}"
            for m in weapons
            if m[0] < len(model["material_names"])
        ]
        rows.append(
            {
                "chr": chr_id,
                "status": "ok",
                "height": f"{model['height']:.2f}",
                "meshes": len(model["meshes"]),
                "materials": model["materials"],
                "verts": sum(m[1] for m in model["meshes"]),
                "body_verts": sum(m[1] for m in model["meshes"] if m not in weapons),
                "weapon_meshes": len(weapons),
                "weapon_detail": " ".join(held),
                "mesh0_verts": model["meshes"][0][1] if model["meshes"] else 0,
                "lod0_tris": sum(m[2] for m in model["meshes"]),
                "c2280_bones": sum(1 for n in C2280_BONES if n in model["bones"]),
                "bone_count": len(model["bones"]),
                "flvers": len(flvers),
                "tpf_textures": len(textures),
                "fourccs": ",".join(
                    f"{k}:{v}" for k, v in sorted(collections.Counter(t[1] for t in textures).items())
                ),
                "tex_names": ",".join(t[0] for t in textures),
                "tpfbhd": len(tpfbhd),
                "bdt": int(bdt),
                "flver_version": f"0x{model['version']:X}",
                "bones": " ".join(model["bones"]),
            }
        )
    return rows, bone_freq


def write_census(out_dir, rows, bone_freq):
    columns = list(next(r for r in rows if r["status"] == "ok").keys())
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "dsr-chr-census.tsv"), "w") as fh:
        fh.write("\t".join(columns) + "\n")
        for row in rows:
            fh.write("\t".join(str(row.get(c, "")) for c in columns) + "\n")
    with open(os.path.join(out_dir, "dsr-bone-freq.txt"), "w") as fh:
        for name, n in bone_freq.most_common():
            fh.write(f"{n}\t{name}\n")


# The game-free selftest: each container below is built the way the readers above parse it, with
# nothing taken from a game file.


def _utf16z(text):
    return text.encode("utf-16-le") + b"\0\0"


def _synthetic_flver(bones, meshes, materials):
    """A minimal little-endian FLVER2.

    `bones` is `(name, parent)`, `materials` is `(name, mtd)`, and `meshes` is `(material, bone
    indices, vertex count, LOD0 triangle-list index count)`. Each mesh gets one vertex buffer, a
    LOD0 face set and its motion-blur copy.
    """
    face_sets = [(mesh, flags) for mesh in range(len(meshes)) for flags in (0, 0x80000000)]
    material_table = 0x80
    bone_table = material_table + 0x20 * len(materials)
    mesh_table = bone_table + 0x80 * len(bones)
    face_set_table = mesh_table + 0x30 * len(meshes)
    buffer_table = face_set_table + 0x20 * len(face_sets)
    data = bytearray(buffer_table + 0x20 * len(meshes))

    def tail(blob):
        offset = len(data)
        data.extend(blob)
        return offset

    data[0:8] = b"FLVER\0L\0"
    struct.pack_into("<I", data, 0x08, 0x2000C)
    struct.pack_into("<5I", data, 0x14, 0, len(materials), len(bones), len(meshes), len(meshes))
    struct.pack_into("<3f", data, 0x28, -0.5, 0.0, -0.5)
    struct.pack_into("<3f", data, 0x34, 0.5, 1.8, 0.5)
    struct.pack_into("<I", data, 0x50, len(face_sets))
    for i, (name, mtd) in enumerate(materials):
        struct.pack_into("<II", data, material_table + 0x20 * i, tail(_utf16z(name)), tail(_utf16z(mtd)))
    for i, (name, parent) in enumerate(bones):
        struct.pack_into("<I", data, bone_table + 0x80 * i + 0x0C, tail(_utf16z(name)))
        struct.pack_into("<h", data, bone_table + 0x80 * i + 0x1C, parent)
    for i, (material, mesh_bones, vertices, _) in enumerate(meshes):
        at = mesh_table + 0x30 * i
        own_sets = [j for j, (mesh, _) in enumerate(face_sets) if mesh == i]
        struct.pack_into("<I", data, at + 0x04, material)
        bone_list = tail(struct.pack(f"<{len(mesh_bones)}i", *mesh_bones))
        struct.pack_into("<iIII", data, at + 0x10, -1, len(mesh_bones), 0, bone_list)
        set_list = tail(struct.pack(f"<{len(own_sets)}I", *own_sets))
        struct.pack_into("<IIII", data, at + 0x20, len(own_sets), set_list, 1, tail(struct.pack("<I", i)))
        struct.pack_into("<I", data, buffer_table + 0x20 * i + 0x0C, vertices)
    for j, (mesh, flags) in enumerate(face_sets):
        struct.pack_into("<IB", data, face_set_table + 0x20 * j, flags, 0)
        struct.pack_into("<I", data, face_set_table + 0x20 * j + 8, meshes[mesh][3])
    return bytes(data)


def _synthetic_tpf(textures):
    """A UTF-16 PC TPF of `(name, fourcc)` textures, each a bare 0x80-byte DDS header."""
    names = [_utf16z(name) for name, _ in textures]
    payloads = [b"DDS " + bytes(0x50) + fourcc + bytes(0x28) for _, fourcc in textures]
    name_at = 0x10 + 0x14 * len(textures)
    data_at = name_at + sum(map(len, names))
    out = bytearray(b"TPF\0" + struct.pack("<II", sum(map(len, payloads)), len(textures)))
    out += bytes([0, 3, 1, 0])
    for name, payload in zip(names, payloads):
        out += struct.pack("<II", data_at, len(payload)) + bytes([0, 1, 1, 0])
        out += struct.pack("<II", name_at, 0)
        name_at += len(name)
        data_at += len(payload)
    return bytes(out) + b"".join(names) + b"".join(payloads)


def _synthetic_bnd3(files):
    """A BND3 of `(path, bytes)` with DSR's format byte (0x74, stored bit-reversed)."""
    out = bytearray(b"BND3" + b"07D7R6\0\0" + bytes([0x74, 0, 0, 0]))
    out += struct.pack("<i", len(files)) + bytes(12)
    names = [path.encode("shift_jis") + b"\0" for path, _ in files]
    name_at = len(out) + 0x18 * len(files)
    data_at = name_at + sum(map(len, names))
    for (_, blob), name in zip(files, names):
        out += struct.pack("<B3xiIiIi", 0x40, len(blob), data_at, 0, name_at, len(blob))
        name_at += len(name)
        data_at += len(blob)
    return bytes(out) + b"".join(names) + b"".join(blob for _, blob in files)


def _synthetic_dcx(payload):
    """A DCX `DFLT` container: DCS sizes, DCP algorithm, then the zlib stream after DCA."""
    compressed = zlib.compress(payload)
    header = bytearray(0x4C)
    header[0:4] = b"DCX\0"
    struct.pack_into(">5I", header, 0x04, 0x10000, 0x18, 0x24, 0x44, 0x4C)
    header[0x18:0x1C] = b"DCS\0"
    struct.pack_into(">II", header, 0x1C, len(payload), len(compressed))
    header[0x24:0x2C] = b"DCP\0DFLT"
    struct.pack_into(">I", header, 0x2C, 0x20)
    header[0x44:0x48] = b"DCA\0"
    struct.pack_into(">I", header, 0x48, 8)
    return bytes(header) + compressed


def selftest():
    root = "N:\\FRPG\\data\\INTERROOT_x64\\chr\\c9990\\"
    flver_bytes = _synthetic_flver(
        bones=[("Pelvis", -1), ("Spine1", 0), ("LLeg1", 0), ("LArm1", 1), ("R_weapon", 3)],
        meshes=[(0, [0, 1, 2, 3], 120, 300), (1, [4], 30, 60)],
        materials=[("body", "C_DullLeather[DSB].mtd"), ("lantern", "P_Metal[DSB].mtd")],
    )
    tpf_bytes = _synthetic_tpf([("c9990", b"DXT1"), ("c9990_n", b"DX10")])
    failures = []

    def check(what, got, want):
        if got != want:
            failures.append(f"{what}: got {got!r}, want {want!r}")

    with tempfile.TemporaryDirectory(prefix="dsr-chr-census-") as tmp:
        chr_dir = os.path.join(tmp, "chr")
        os.makedirs(chr_dir)
        binder = _synthetic_bnd3(
            [
                (root + "c9990.flver", flver_bytes),
                (root + "c9990.tpf", tpf_bytes),
                (root + "c9990.chrtpfbhd", b"BHF3"),
            ]
        )
        with open(os.path.join(chr_dir, "c9990.chrbnd.dcx"), "wb") as fh:
            fh.write(_synthetic_dcx(binder))
        with open(os.path.join(chr_dir, "c9990.chrtpfbdt"), "wb") as fh:
            fh.write(b"BDF3")
        with open(os.path.join(chr_dir, "c9991.chrbnd.dcx"), "wb") as fh:
            fh.write(_synthetic_dcx(_synthetic_bnd3([(root + "c9991.hkx", b"hkx")])))

        rows, bone_freq = census(chr_dir)
        check("row count", len(rows), 2)
        row = rows[0]
        for column, want in {
            "chr": "c9990",
            "status": "ok",
            "height": "1.80",
            "meshes": 2,
            "materials": 2,
            "verts": 150,
            "body_verts": 120,
            "weapon_meshes": 1,
            "weapon_detail": "lantern@R_weapon",
            "mesh0_verts": 120,
            "lod0_tris": 120,
            "c2280_bones": 4,
            "bone_count": 5,
            "flvers": 1,
            "tpf_textures": 2,
            "fourccs": "DX10:1,DXT1:1",
            "tex_names": "c9990,c9990_n",
            "tpfbhd": 1,
            "bdt": 1,
            "flver_version": "0x2000C",
            "bones": "Pelvis Spine1 LLeg1 LArm1 R_weapon",
        }.items():
            check(column, row.get(column), want)
        check("no-flver row", rows[1], {"chr": "c9991", "status": "no-flver"})
        check("bone frequency", dict(bone_freq), {n: 1 for n in row["bones"].split()})

        out_dir = os.path.join(tmp, "out")
        write_census(out_dir, rows, bone_freq)
        with open(os.path.join(out_dir, "dsr-chr-census.tsv")) as fh:
            lines = fh.read().splitlines()
        check("tsv lines", len(lines), 3)
        check("tsv header", lines[0].split("\t")[:2], ["chr", "status"])
        check("tsv no-flver row", lines[2].split("\t")[:2], ["c9991", "no-flver"])

    # The weapon rule on its own: a mesh is a weapon by its material name or by bones that are all
    # weapon bones, and a body mesh with one weapon bone among others is not.
    check("weapon by material", is_weapon_mesh((0, 0, 0, {"Spine"}), [("WP_A_0402", "")]), True)
    check("weapon by bones", is_weapon_mesh((0, 0, 0, {"R_weapon", "L_Shield"}), [("x", "")]), True)
    check("body with a weapon bone", is_weapon_mesh((0, 0, 0, {"R_weapon", "Spine"}), [("x", "")]), False)

    for failure in failures:
        print(f"FAIL {failure}")
    print(f"dsr-chr-census selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


def main():
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(selftest())
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    out_dir = sys.argv[1]
    chr_dir = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_DSR_CHR
    rows, bone_freq = census(chr_dir)
    write_census(out_dir, rows, bone_freq)
    print(f"{len(rows)} chrbnds -> {out_dir}")


if __name__ == "__main__":
    main()
