#!/usr/bin/env python3
"""Read the FLVER2 header bounding box out of every extracted chr .flver, offline.

One-off measurement script for a camera-framing question: does NpcParam.hitHeight
(physics capsule height) track model bbox height? Reads the already-witchy-unpacked
chr corpus (raw, uncompressed .flver files -- no DCX/Oodle involved), so this is a
pure header parse: no archive tooling needed.

Corpus root (WitchyBND recursive unpack, `foo.chrbnd.dcx` -> `foo-chrbnd-dcx/`,
sharded into `_chunk_NNNN` subdirectories plus a few top-level entries):
    /home/banon/er-extract/LOOK_HERE_WITCHY_RECURSIVE_20260713/sharded/chr/

FLVER2 header layout (verified against c0000 -- known-degenerate FLT_MAX/-FLT_MAX
bbox, meshCount=0 -- and c4600/c4760/c2010, which come out at sane metre-scale
heights: 8.9m, 28.0m, 2.8m respectively):
    0x00  magic "FLVER\0"          (6 bytes)
    0x06  endian marker "L\0"/"B\0" (2 bytes)
    0x08  version               i32
    0x0C  dataOffset            i32
    0x10  dataSize              i32
    0x14  dummyCount            i32
    0x18  materialCount         i32
    0x1C  boneCount             i32
    0x20  meshCount             i32
    0x24  vertexBufferCount     i32
    0x28  bboxMin               3x f32
    0x34  bboxMax               3x f32

Output: TSV with header row, one data row per chrid:
    chrid  bboxHeight  meshCount  materialCount  boneCount  degenerate  flver_path
"degenerate" = 1 when bboxMin/bboxMax describes no real geometry (FLT_MAX sentinel,
or a NaN/infinite/zero-extent result), 0 otherwise.
"""
import glob
import os
import re
import struct
import sys

CORPUS_ROOT = os.environ.get(
    "ER_CHR_WITCHY_CORPUS",
    "/home/banon/er-extract/LOOK_HERE_WITCHY_RECURSIVE_20260713/sharded/chr",
)
CHRBND_DIR_RE = re.compile(r"^(c\d{4})-chrbnd-dcx")
FLT_MAX = struct.unpack("<f", bytes.fromhex("ffff7f7f"))[0]  # 3.4028235e+38


def find_chrbnd_dirs(root):
    """chrid -> path to its unpacked `cNNNN-chrbnd-dcx[...]` directory."""
    found = {}
    for top in sorted(os.listdir(root)):
        top_path = os.path.join(root, top)
        if not os.path.isdir(top_path):
            continue
        if top.startswith("_chunk_"):
            base, names = top_path, os.listdir(top_path)
        else:
            base, names = root, [top]
        for name in names:
            m = CHRBND_DIR_RE.match(name)
            if m:
                found[m.group(1)] = os.path.join(base, name)
    return found


def flver_bbox(path):
    """(bboxMin, bboxMax, meshCount, materialCount, boneCount) or None if not a FLVER."""
    with open(path, "rb") as f:
        b = f.read(0x40)
    if b[:6] != b"FLVER\x00":
        return None
    dummy_count, material_count, bone_count, mesh_count, vb_count = struct.unpack_from(
        "<5i", b, 0x14
    )
    bbox_min = struct.unpack_from("<3f", b, 0x28)
    bbox_max = struct.unpack_from("<3f", b, 0x34)
    return bbox_min, bbox_max, mesh_count, material_count, bone_count


def is_degenerate(bbox_min, bbox_max, mesh_count):
    if mesh_count == 0:
        return True
    for v in (*bbox_min, *bbox_max):
        if v != v or v in (float("inf"), float("-inf")) or abs(v) >= FLT_MAX * 0.999:
            return True
    height = bbox_max[1] - bbox_min[1]
    if not (height > 0) or height != height:
        return True
    return False


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else None
    out = open(out_path, "w") if out_path else sys.stdout

    chrbnd_dirs = find_chrbnd_dirs(CORPUS_ROOT)
    print(
        f"chrid\tbboxHeight\tbboxMinY\tbboxMaxY\tmeshCount\tmaterialCount\tboneCount\tdegenerate\tflverPath",
        file=out,
    )
    n_total = 0
    n_degenerate = 0
    n_no_flver = 0
    for chrid in sorted(chrbnd_dirs):
        d = chrbnd_dirs[chrid]
        candidates = glob.glob(os.path.join(d, chrid + ".flver")) or glob.glob(
            os.path.join(d, "*.flver")
        )
        if not candidates:
            n_no_flver += 1
            print(f"{chrid}\tNO_FLVER\t\t\t\t\t\t\t{d}", file=out)
            continue
        flver_path = candidates[0]
        parsed = flver_bbox(flver_path)
        n_total += 1
        if parsed is None:
            n_no_flver += 1
            print(f"{chrid}\tBAD_MAGIC\t\t\t\t\t\t\t{flver_path}", file=out)
            continue
        bbox_min, bbox_max, mesh_count, material_count, bone_count = parsed
        deg = is_degenerate(bbox_min, bbox_max, mesh_count)
        if deg:
            n_degenerate += 1
        height = bbox_max[1] - bbox_min[1]
        print(
            f"{chrid}\t{height!r}\t{bbox_min[1]!r}\t{bbox_max[1]!r}\t{mesh_count}\t{material_count}\t{bone_count}\t{int(deg)}\t{flver_path}",
            file=out,
        )
        out.flush()

    print(
        f"# done: {len(chrbnd_dirs)} chrbnd dirs, {n_total} parsed flvers, "
        f"{n_degenerate} degenerate, {n_no_flver} missing/bad",
        file=out,
    )
    if out_path:
        out.close()


if __name__ == "__main__":
    main()
