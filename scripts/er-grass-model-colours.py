#!/usr/bin/env python3
"""How white (or any colour) each grass/plant model is, measured from its own albedo texels.

    uv run --with pillow --with texture2ddecoder python3 scripts/er-grass-model-colours.py AEG001_254 AEG098_202 ...

For each model: the FLVER out of the extracted geombnd is exported with the er-flver `flver-obj`
example (built at target/debug/examples/flver-obj), the material's matbin XML names its albedo
(`*_a.tif`), the matching `.dds` is decoded, and every triangle is sampled at random barycentric
points through its UVs (wrapping, as the sampler does). Texels with alpha under 128 are skipped
as cut-out. Prints the opaque sample count, the mean RGB and the fraction of near-white texels
(all channels above 170), so a flower model can be told from a grass one without anyone looking
at a picture.

Inputs: ER_EXTRACT_ROOT (default ~/er-extract/LOOK_HERE_WITCHY_RECURSIVE_20260713/sharded).
"""
import os
import pathlib
import random
import re
import subprocess
import sys
import struct
import tempfile

import texture2ddecoder

from PIL import Image

ROOT = pathlib.Path(os.environ.get(
    "ER_EXTRACT_ROOT",
    pathlib.Path.home() / "er-extract/LOOK_HERE_WITCHY_RECURSIVE_20260713/sharded"))
REPO = pathlib.Path(__file__).resolve().parent.parent
FLVER_OBJ = REPO / "target/debug/examples/flver-obj"
MATBINS = ROOT / "material/allmaterial-matbinbnd-dcx-wmatbinbnd"


def find_one(base, pattern):
    for p in base.rglob(pattern):
        return p
    return None


_dds_index = None


def dds_for(name):
    global _dds_index
    if _dds_index is None:
        _dds_index = {p.stem.lower(): p for p in (ROOT / "asset/aet").rglob("*.dds")}
    return _dds_index.get(name.lower())


_matbins = None


def _matbin_index():
    global _matbins
    if _matbins is None:
        _matbins = {p.name[: -len(".matbin.xml")].lower(): p for p in MATBINS.rglob("*.matbin.xml")}
    return _matbins


def albedo_of(matxml):
    # The FLVER spells the matxml as a Windows path, which pathlib on Linux does not split.
    stem = matxml.replace("\\", "/").split("/")[-1].rsplit(".", 1)[0]
    mb = _matbin_index().get(stem.lower())
    if mb is None:
        return None
    for path in re.findall(r"<Path>([^<]*)</Path>", mb.read_text(errors="replace")):
        stem = path.replace("\\", "/").split("/")[-1].rsplit(".", 1)[0]
        if stem.lower().endswith("_a"):
            return stem
    return None


_dds_cache = {}


def load_dds(path):
    """Top mip as RGBA. Pillow cannot read a DX10-header BC1 (DXGI 71/72), which the plant
    atlases use, so the block formats go through texture2ddecoder."""
    if path in _dds_cache:
        return _dds_cache[path]
    raw = path.read_bytes()
    h, w = struct.unpack_from("<II", raw, 12)
    fourcc = raw[84:88]
    dxgi = struct.unpack_from("<I", raw, 128)[0] if fourcc == b"DX10" else None
    off = 148 if fourcc == b"DX10" else 128
    decoders = {71: ("decode_bc1", 8), 72: ("decode_bc1", 8), 74: ("decode_bc2", 16), 75: ("decode_bc2", 16),
                77: ("decode_bc3", 16), 78: ("decode_bc3", 16), 98: ("decode_bc7", 16), 99: ("decode_bc7", 16)}
    legacy = {b"DXT1": ("decode_bc1", 8), b"DXT3": ("decode_bc2", 16), b"DXT5": ("decode_bc3", 16)}
    dec = decoders.get(dxgi) if dxgi is not None else legacy.get(fourcc)
    if dec is None:
        im = Image.open(path).convert("RGBA")
    else:
        size = max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * dec[1]
        bgra = getattr(texture2ddecoder, dec[0])(raw[off:off + size], w, h)
        im = Image.frombytes("RGBA", (w, h), bgra, "raw", "BGRA")
    _dds_cache[path] = im
    return im


def measure(model, rng):
    geom = None
    for d in (ROOT / "asset").rglob(f"{model.lower()}-geombnd-dcx"):
        geom = d
        break
    if geom is None:
        return f"{model}: no extracted geombnd"
    flver = next(geom.glob("*.flver"), None)
    if flver is None:
        return f"{model}: no flver"
    raw = flver.read_bytes()
    mats = sorted(set(re.findall(r"[\w\\:]+\.(?:matxml|mtd)", raw.decode("utf-16-le", errors="ignore"))))
    with tempfile.TemporaryDirectory() as tmp:
        obj = pathlib.Path(tmp) / "m.obj"
        subprocess.run([str(FLVER_OBJ), str(flver), str(obj)], check=True, capture_output=True, timeout=20)
        lines = obj.read_text().splitlines()
    vt, faces, ys = [], [], []
    for line in lines:
        p = line.split()
        if not p:
            continue
        if p[0] == "vt":
            vt.append((float(p[1]), float(p[2])))
        elif p[0] == "v":
            ys.append(float(p[2]))
        elif p[0] == "f":
            faces.append([int(x.split("/")[1]) - 1 for x in p[1:4]])
    albedos = [a for a in (albedo_of(m) for m in mats) if a]
    if not albedos or not faces:
        return f"{model}: materials {mats} albedo {albedos} faces {len(faces)}"
    dds = dds_for(albedos[0])
    if dds is None:
        return f"{model}: albedo {albedos[0]} not extracted"
    im = load_dds(dds)
    w, h = im.size
    px = im.load()
    n = white = 0
    acc = [0, 0, 0]
    for a, b, c in faces:
        for _ in range(16):
            r1, r2 = rng.random(), rng.random()
            if r1 + r2 > 1:
                r1, r2 = 1 - r1, 1 - r2
            u = vt[a][0] + r1 * (vt[b][0] - vt[a][0]) + r2 * (vt[c][0] - vt[a][0])
            v = vt[a][1] + r1 * (vt[b][1] - vt[a][1]) + r2 * (vt[c][1] - vt[a][1])
            r, g, bl, al = px[int((u % 1) * w) % w, int((v % 1) * h) % h]
            if al < 128:
                continue
            n += 1
            acc[0] += r
            acc[1] += g
            acc[2] += bl
            if min(r, g, bl) > 170:
                white += 1
    if n == 0:
        return f"{model}: no opaque samples ({albedos[0]})"
    mean = [round(x / n) for x in acc]
    height = round(max(ys) - min(ys), 2) if ys else None
    return f"{model}: tris {len(faces)} height {height} albedo {albedos[0]} samples {n} meanRGB {mean} white {white / n:.3f}"


def main():
    rng = random.Random(1)
    for model in sys.argv[1:]:
        try:
            print(measure(model, rng), flush=True)
        except Exception as e:  # one bad model must not hide the rest
            print(f"{model}: error {e}", flush=True)


if __name__ == "__main__":
    main()
