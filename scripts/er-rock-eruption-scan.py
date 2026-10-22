#!/usr/bin/env python3
"""Score every rock placement of an m60 tile as a minor terrain eruption or a main feature.

For each rock asset placed for tile `m60_XX_YY_00` (parts in the tile's own MSB, plus the
`m60_XX_YY_00-AEG...` parts its parent `_01`/`_02` blocks hold for it), the rock is put into the
tile's frame and measured against the tile's terrain map pieces:

- exposed height: highest point of the rock above the terrain under it;
- exposed footprint: xz area of 0.25 m cells where the rock's top surface is above the terrain;
- exposed volume: sum over those cells of (rock top - terrain);
- buried fraction: share of rock surface area whose triangle centroid is below the terrain;
- support: other assets whose lowest geometry over the rock's footprint lies on the rock's top
  (they would float if it were hidden), and assets that merely touch it.

Frames. A level-L m60 block `m60_XX_YY_LL` is `256 * 2**L` metres wide and its MSB positions are
local to its centre at `size * XX + (size - 256) / 2` (measured on the AEG091 impostors, see
`scripts/er-asset-join-check.py`). Terrain map pieces and their `.grass` files sit at the origin
of the `_00` tile. Rotation is Euler degrees; `--calibrate` scores every axis order and sign
against the terrain and the default order is the one that wins there.

Grass. For each minor rock the `.grass` vertices (SoulsFormats GRASS: 0x28 header, 0x14 volumes,
0x24 vertices = position + float[6] slot densities, 0x18 faces = normal + three u32 indices,
then one 0x18 bounds box per volume) inside its exposed footprint are listed, together with the
average densities of a ring around it and whether grass triangles cover the footprint at all.

Usage:
  er-rock-eruption-scan.py --tile m60_42_36_00 [--tile ...] [--out-dir target/rock-scan]
  er-rock-eruption-scan.py --tile m60_42_36_00 --calibrate
  er-rock-eruption-scan.py --tile m60_42_36_00 --part m60_42_36_00-AEG004_145_1013

Needs numpy: `uv run --with numpy python3 scripts/er-rock-eruption-scan.py ...`.
"""

import argparse
import glob
import json
import math
import os
import re
import struct
import subprocess
import sys
from collections import defaultdict

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXTRACT = os.path.join(
    os.environ.get("ER_EXTRACT_ROOT", os.path.expanduser("~/er-extract")),
    "LOOK_HERE_WITCHY_RECURSIVE_20260713/sharded",
)
MAPSTUDIO = os.path.join(EXTRACT, "map/mapstudio")
DUMP_BIN = os.path.join(REPO, "target/release/examples/flver-tri-dump")
CELL = 0.25
MARGIN = 48.0
ROCK_TEXTURE = re.compile(r"^AET00[12]_")
# Terrain pieces whose material is one of these are not ground: map-piece props that use an asset
# material. Water surfaces do count, because they hide whatever is below them and the lake
# pieces carry no bed under the water.
NOT_GROUND_MTD = re.compile(r"AEG\d", re.I)

# Split between a minor eruption and a main feature (see `classify`).
MINOR_MAX_HEIGHT = 3.0
MINOR_MAX_PROTRUSION = 1.0
MINOR_MAX_AREA_1M = 80.0
RING = 2.0


# --------------------------------------------------------------------------- inputs

def tile_coords(tile):
    m = re.match(r"m60_(\d+)_(\d+)_00$", tile)
    if not m:
        sys.exit(f"{tile}: expected m60_XX_YY_00")
    return int(m[1]), int(m[2])


def block_offset(msb):
    """Centre of an m60 block in world metres, from its name."""
    m = re.match(r"m60_(\d+)_(\d+)_\d(\d)$", msb)
    xx, yy, level = int(m[1]), int(m[2]), int(m[3])
    size = 256.0 * 2**level
    off = (size - 256.0) / 2.0
    return np.array([size * xx + off, 0.0, size * yy + off])


def parent_blocks(tx, ty):
    return [f"m60_{tx // 2:02d}_{ty // 2:02d}_01", f"m60_{tx // 4:02d}_{ty // 4:02d}_02"]


_msb_dirs = {}


def msb_dir(msb):
    if msb not in _msb_dirs:
        hits = glob.glob(os.path.join(MAPSTUDIO, "_chunk_*", f"{msb}-msb-dcx"))
        _msb_dirs[msb] = hits[0] if hits else None
    return _msb_dirs[msb]


def read_vec(text, tag):
    m = re.search(rf"<{tag}>\s*<X>([^<]+)</X>\s*<Y>([^<]+)</Y>\s*<Z>([^<]+)</Z>", text)
    return np.array([float(v) for v in m.groups()])


def tile_parts(tile, kind="Asset"):
    """Every part placed for `tile`: its own MSB, plus the prefixed parts of its parent blocks."""
    tx, ty = tile_coords(tile)
    out = []
    sources = [(tile, None)] + [(b, tile + "-") for b in parent_blocks(tx, ty)]
    for msb, prefix in sources:
        d = msb_dir(msb)
        if d is None:
            continue
        for path in sorted(glob.glob(os.path.join(d, "Part", kind, "*.xml"))):
            name = os.path.basename(path)[:-4]
            if prefix is not None and not name.startswith(prefix):
                continue
            text = open(path, encoding="utf-8-sig", errors="replace").read()
            model = re.search(r"<ModelName>([^<]*)</ModelName>", text)
            world = read_vec(text, "Position") + block_offset(msb)
            out.append({
                "name": name, "msb": msb, "model": model[1] if model else "",
                "pos": world - block_offset(tile), "rot": read_vec(text, "Rotation"),
                "scale": read_vec(text, "Scale"),
            })
    return out


def flver_path(model):
    if model.startswith("AEG"):
        fam = model[:6].lower()
        hits = glob.glob(os.path.join(EXTRACT, "asset/aeg", fam, "_chunk_*",
                                      f"{model.lower()}-geombnd-dcx", f"{model}.flver"))
        return hits[0] if hits else None
    return None


def load_tri(path):
    b = open(path, "rb").read()
    nv, nt, nm = struct.unpack_from("<3I", b)
    o = 12
    pos = np.frombuffer(b, np.float32, nv * 3, o).reshape(-1, 3).astype(np.float64)
    o += nv * 12
    idx = np.frombuffer(b, np.uint32, nt * 3, o).reshape(-1, 3).astype(np.int64)
    o += nt * 12
    mat = np.frombuffer(b, np.uint16, nt, o).astype(np.int64)
    o += nt * 2
    mats = [line.split("\t") for line in b[o:].decode("utf-8", "replace").splitlines()]
    return pos, idx, mat, mats


class Cache:
    """`.tri` dumps of FLVERs, made on demand by the `flver-tri-dump` example."""

    def __init__(self, root):
        self.root = os.path.join(root, "tri")
        os.makedirs(self.root, exist_ok=True)
        self.mem = {}

    def ensure(self, flvers):
        todo = [f for f in flvers if f and not os.path.exists(self._out(f))]
        # 200 per call: the 200 largest AEG FLVERs (1.4 GB) dump in 3.4 s, so the 30 s cap is a
        # backstop with ~9x headroom, not a limit a batch approaches.
        for i in range(0, len(todo), 200):
            subprocess.run([DUMP_BIN, self.root] + todo[i:i + 200], check=True, timeout=30)

    def _out(self, flver):
        return os.path.join(self.root, os.path.basename(flver)[:-6] + ".tri")

    def get(self, flver):
        if flver not in self.mem:
            p = self._out(flver)
            self.mem[flver] = load_tri(p) if os.path.exists(p) else None
        return self.mem[flver]


_albedo = None


def matbin_albedo(name):
    """Albedo texture names of a matbin, by matbin name."""
    global _albedo
    if _albedo is None:
        _albedo = {}
        for directory, _, files in os.walk(os.path.join(EXTRACT, "material")):
            for f in files:
                if f.endswith(".matbin.xml"):
                    _albedo.setdefault(f[:-11], os.path.join(directory, f))
    path = _albedo.get(name)
    if path is None:
        return []
    text = open(path, encoding="utf-8", errors="replace").read()
    return [p.rsplit("\\", 1)[-1] for p in re.findall(r"<Path>([^<]+)</Path>", text)
            if p.lower().endswith("_a.tif")]


def material_kind(name, mtd):
    """`rock`, `lod`, `veg` or `other` for one FLVER material."""
    stem = mtd.rsplit("\\", 1)[-1].rsplit(".", 1)[0]
    if re.search(r"LOD", name + stem, re.I):
        return "lod"
    if re.search(r"grass|veg|vesit|leaf|moss_card", name + stem, re.I):
        return "veg"
    if any(ROCK_TEXTURE.match(t) for t in matbin_albedo(stem)):
        return "rock"
    return "other"


# --------------------------------------------------------------------------- geometry

def axis_rot(axis, deg):
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    if axis == "X":
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
    if axis == "Y":
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def rot_matrix(rot, order="XZY", signs=(1, 1, 1)):
    """`order` is the order the axes are applied in, so "XZY" means X first and Y last."""
    angles = dict(zip("XYZ", rot * np.array(signs)))
    m = np.eye(3)
    for axis in order:
        m = axis_rot(axis, angles[axis]) @ m
    return m


def place(verts, part, order="XZY", signs=(1, 1, 1)):
    return (verts * part["scale"]) @ rot_matrix(part["rot"], order, signs).T + part["pos"]


class Grid:
    def __init__(self, x0, z0, nx, nz, cell=CELL):
        self.x0, self.z0, self.nx, self.nz, self.cell = x0, z0, nx, nz, cell

    def ij(self, x, z):
        return (np.floor((x - self.x0) / self.cell).astype(np.int64),
                np.floor((z - self.z0) / self.cell).astype(np.int64))

    def centre(self, i, j):
        return self.x0 + (i + 0.5) * self.cell, self.z0 + (j + 0.5) * self.cell


def rasterize(tri, grid, reduce="max"):
    """Per-cell max (or min) of the triangles' y at cell centres; NaN where none covers.

    Returns (flat cell index, value) pairs already reduced, as a dict-like sparse result
    `(cells, values)`.
    """
    if len(tri) == 0:
        return np.zeros(0, np.int64), np.zeros(0)
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    xmin = np.minimum(np.minimum(a[:, 0], b[:, 0]), c[:, 0])
    xmax = np.maximum(np.maximum(a[:, 0], b[:, 0]), c[:, 0])
    zmin = np.minimum(np.minimum(a[:, 2], b[:, 2]), c[:, 2])
    zmax = np.maximum(np.maximum(a[:, 2], b[:, 2]), c[:, 2])
    i0 = np.ceil((xmin - grid.x0) / grid.cell - 0.5).astype(np.int64)
    i1 = np.floor((xmax - grid.x0) / grid.cell - 0.5).astype(np.int64)
    j0 = np.ceil((zmin - grid.z0) / grid.cell - 0.5).astype(np.int64)
    j1 = np.floor((zmax - grid.z0) / grid.cell - 0.5).astype(np.int64)
    i0, j0 = np.maximum(i0, 0), np.maximum(j0, 0)
    i1, j1 = np.minimum(i1, grid.nx - 1), np.minimum(j1, grid.nz - 1)
    w, h = i1 - i0 + 1, j1 - j0 + 1
    ok = (w > 0) & (h > 0)
    d = (b[:, 2] - c[:, 2]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 2] - c[:, 2])
    ok &= np.abs(d) > 1e-12
    cells, vals = [], []
    span = int(max(w[ok].max(), h[ok].max())) if ok.any() else 0
    step = 16
    for bi in range(0, span, step):
        for bj in range(0, span, step):
            sel = ok & (w > bi) & (h > bj)
            if not sel.any():
                continue
            idx = np.nonzero(sel)[0]
            for di in range(bi, min(bi + step, span)):
                for dj in range(bj, min(bj + step, span)):
                    s = idx[(w[idx] > di) & (h[idx] > dj)]
                    if len(s) == 0:
                        continue
                    ii, jj = i0[s] + di, j0[s] + dj
                    px, pz = grid.centre(ii, jj)
                    l1 = ((b[s, 2] - c[s, 2]) * (px - c[s, 0]) + (c[s, 0] - b[s, 0]) * (pz - c[s, 2])) / d[s]
                    l2 = ((c[s, 2] - a[s, 2]) * (px - c[s, 0]) + (a[s, 0] - c[s, 0]) * (pz - c[s, 2])) / d[s]
                    l3 = 1 - l1 - l2
                    inside = (l1 >= -1e-9) & (l2 >= -1e-9) & (l3 >= -1e-9)
                    if not inside.any():
                        continue
                    y = l1 * a[s, 1] + l2 * b[s, 1] + l3 * c[s, 1]
                    cells.append((ii * grid.nz + jj)[inside])
                    vals.append(y[inside])
    if not cells:
        return np.zeros(0, np.int64), np.zeros(0)
    cells, vals = np.concatenate(cells), np.concatenate(vals)
    order = np.lexsort((vals if reduce == "min" else -vals, cells))
    cells, vals = cells[order], vals[order]
    first = np.ones(len(cells), bool)
    first[1:] = cells[1:] != cells[:-1]
    return cells[first], vals[first]


def dense(grid, cells, vals):
    out = np.full(grid.nx * grid.nz, np.nan)
    out[cells] = vals
    return out


# --------------------------------------------------------------------------- terrain and grass

def tile_pieces(tile):
    pat = os.path.join(EXTRACT, "map/m60", tile, f"{tile}_*-mapbnd-dcx")
    return sorted(glob.glob(pat))


def terrain_triangles(tile, cache, tx, ty, grid):
    """Ground triangles of `tile` and its 8 neighbours, in `tile`'s frame."""
    tris, used = [], []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            other = f"m60_{tx + dx:02d}_{ty + dy:02d}_00"
            shift = np.array([256.0 * dx, 0.0, 256.0 * dy])
            for piece in tile_pieces(other):
                fl = glob.glob(os.path.join(piece, "*.flver"))
                cache.ensure(fl)
                for f in fl:
                    t = cache.get(f)
                    if t is None:
                        continue
                    pos, idx, mat, mats = t
                    keep = np.array([not NOT_GROUND_MTD.search(m[1] if len(m) > 1 else "") for m in mats])
                    if len(keep) and not keep.any():
                        continue
                    sel = keep[mat] if len(keep) else np.ones(len(idx), bool)
                    tri = pos[idx[sel]] + shift
                    cx = tri[:, :, 0].mean(1)
                    cz = tri[:, :, 2].mean(1)
                    inwin = ((cx > grid.x0 - 8) & (cx < grid.x0 + grid.nx * grid.cell + 8)
                             & (cz > grid.z0 - 8) & (cz < grid.z0 + grid.nz * grid.cell + 8))
                    if inwin.any():
                        tris.append(tri[inwin])
                        used.append(os.path.basename(f))
    return (np.concatenate(tris) if tris else np.zeros((0, 3, 3))), used


def read_grass(path):
    b = open(path, "rb").read()
    h = struct.unpack_from("<10I", b, 0)
    if h[0] != 1 or h[1] != 0x28 or h[2] != 0x14 or h[4] != 0x24 or h[6] != 0x18:
        raise ValueError(f"{path}: unexpected GRASS header {h}")
    nvol, nv, nf = h[3], h[5], h[7]
    o = 0x28 + nvol * 0x14
    v = np.frombuffer(b, np.float32, nv * 9, o).reshape(-1, 9).astype(np.float64)
    o += nv * 0x24
    fr = np.frombuffer(b, np.uint8, nf * 0x18, o).reshape(-1, 0x18)
    faces = fr[:, 12:].copy().view(np.uint32).reshape(-1, 3).astype(np.int64)
    o += nf * 0x18
    if o + nvol * 0x18 != len(b):
        raise ValueError(f"{path}: size {len(b)} != parsed {o + nvol * 0x18}")
    return v[:, :3], v[:, 3:], faces


def tile_grass(tile):
    out = []
    for piece in tile_pieces(tile):
        for g in glob.glob(os.path.join(piece, "*.grass")):
            pos, dens, faces = read_grass(g)
            out.append({"file": os.path.basename(g), "pos": pos, "dens": dens, "faces": faces})
    return out


def grass_slots(tile):
    """MapPiece name -> its six GrassParamId values."""
    d = msb_dir(tile)
    slots = {}
    for path in glob.glob(os.path.join(d, "Part", "MapPiece", "*.xml")):
        text = open(path, encoding="utf-8-sig", errors="replace").read()
        ids = [int(x) for x in re.findall(r"<GrassParamId\d>(-?\d+)</GrassParamId\d>", text)]
        slots[os.path.basename(path)[:-4]] = ids
    return slots


# --------------------------------------------------------------------------- per-tile analysis

def model_geometry(model, cache):
    """Rock-material triangles (LOD0, no vegetation cards) and all LOD0 vertices of a model."""
    f = flver_path(model)
    if f is None:
        return None
    cache.ensure([f])
    t = cache.get(f)
    if t is None:
        return None
    pos, idx, mat, mats = t
    kinds = [material_kind(m[0], m[1] if len(m) > 1 else "") for m in mats]
    kind_of_tri = np.array([kinds[m] for m in mat]) if len(mat) else np.zeros(0, "<U5")
    lod0 = kind_of_tri != "lod"
    rock = kind_of_tri == "rock"
    solid = lod0 & (kind_of_tri != "veg")
    ext = np.abs(pos).max() if len(pos) else 0.0
    n_solid = int(solid.sum())
    return {
        "rock_tris": idx[rock], "solid_tris": idx[solid], "pos": pos,
        "lod0_verts": np.unique(idx[lod0].ravel()) if lod0.any() else np.zeros(0, np.int64),
        "rock_share": float(rock.sum()) / n_solid if n_solid else 0.0,
        "sane": ext < 500.0,
        "materials": sorted({f"{m[0]}:{k}" for m, k in zip(mats, kinds)}),
    }


def analyse_tile(tile, cache, args):
    tx, ty = tile_coords(tile)
    grid = Grid(-128 - MARGIN, -128 - MARGIN, int((256 + 2 * MARGIN) / CELL), int((256 + 2 * MARGIN) / CELL))
    tris, used = terrain_triangles(tile, cache, tx, ty, grid)
    tc, tv = rasterize(tris, grid, "max")
    terrain = dense(grid, tc, tv)

    parts = tile_parts(tile)
    # Neighbouring tiles' assets can rest on or touch this tile's rocks near the edges.
    others = list(parts)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            nb = f"m60_{tx + dx:02d}_{ty + dy:02d}_00"
            if msb_dir(nb) is None:
                continue
            for p in tile_parts(nb):
                p = dict(p)
                p["pos"] = p["pos"] + np.array([256.0 * dx, 0.0, 256.0 * dy])
                if abs(p["pos"][0]) < 128 + MARGIN and abs(p["pos"][2]) < 128 + MARGIN:
                    others.append(p)

    geo = {}
    for p in others:
        if p["model"] not in geo:
            geo[p["model"]] = model_geometry(p["model"], cache)

    # Stone buildings (AEG007 fort kit) and gimmicks (AEG099) also sample AET001/AET002, so the
    # texture test alone is not enough; the asset family keeps it to natural rock and cliff.
    families = tuple(args.families.split(","))
    textured = [p for p in parts if geo.get(p["model"]) and geo[p["model"]]["rock_share"] >= 0.5
                and geo[p["model"]]["sane"] and len(geo[p["model"]]["rock_tris"])]
    rocks = [p for p in textured if p["model"].startswith(families)]
    skipped = sorted({p["model"] for p in textured} - {p["model"] for p in rocks})
    if skipped and not args.quiet:
        print(f"{tile}: rock-textured models outside {args.families}, not scored: {', '.join(skipped)}")
    if args.part:
        ASKED.update(args.part)
        rocks = [p for p in rocks if p["name"] in args.part]

    # Bounding boxes of every other placement, for the touch test.
    placed_other = []
    for p in others:
        g = geo.get(p["model"])
        if not g or not g["sane"] or not len(g["lod0_verts"]):
            continue
        v = place(g["pos"][g["lod0_verts"]], p, args.order)
        placed_other.append((p, v, v.min(0), v.max(0)))

    rows = []
    for p in rocks:
        g = geo[p["model"]]
        world = place(g["pos"], p, args.order)
        tri = world[g["rock_tris"]]
        lo, hi = tri.reshape(-1, 3).min(0), tri.reshape(-1, 3).max(0)
        i0, j0 = grid.ij(lo[0], lo[2])
        i1, j1 = grid.ij(hi[0], hi[2])
        i0, j0 = max(int(i0), 0), max(int(j0), 0)
        i1, j1 = min(int(i1), grid.nx - 1), min(int(j1), grid.nz - 1)
        if i1 < i0 or j1 < j0:
            continue
        sub = Grid(grid.x0 + i0 * CELL, grid.z0 + j0 * CELL, i1 - i0 + 1, j1 - j0 + 1)
        tcells, top = rasterize(tri, sub, "max")
        _, bottom = rasterize(tri, sub, "min")
        si, sj = tcells // sub.nz, tcells % sub.nz
        ground = terrain[(si + i0) * grid.nz + (sj + j0)]
        have = ~np.isnan(ground)
        gnd = np.where(have, ground, -np.inf)
        above = top - gnd
        exposed = have & (above > 0.05)
        exposed_area = float(exposed.sum()) * CELL * CELL
        exposed_height = float(above[exposed].max()) if exposed.any() else 0.0
        # A cell where terrain triangles barely miss can sample a far lower surface (a cliff
        # foot); the 98th percentile is the height an observer actually sees.
        height_p98 = float(np.percentile(above[exposed], 98)) if exposed.any() else 0.0
        # Rock meshes are open shells, so the rock counts as solid from its top down to the ground.
        exposed_volume = float(above[exposed].sum()) * CELL * CELL
        mean_protrusion = exposed_volume / exposed_area if exposed_area else 0.0
        area_50 = float((have & (above > 0.5)).sum()) * CELL * CELL
        area_100 = float((have & (above > 1.0)).sum()) * CELL * CELL
        if exposed.any():
            ex_x, ex_z = sub.centre(si[exposed], sj[exposed])
            span = float(max(np.ptp(ex_x), np.ptp(ex_z))) + CELL
        else:
            span = 0.0
        floating = have & (bottom - gnd > 0.15)
        floating_area = float(floating.sum()) * CELL * CELL

        # Terrain under each vertex, and the area-weighted buried share of the surface.
        vi, vj = grid.ij(world[:, 0], world[:, 2])
        vin = (vi >= 0) & (vi < grid.nx) & (vj >= 0) & (vj < grid.nz)
        vground = np.full(len(world), np.nan)
        vground[vin] = terrain[vi[vin] * grid.nz + vj[vin]]
        cen = tri.mean(1)
        area = 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
        ci, cj = grid.ij(cen[:, 0], cen[:, 2])
        cin = (ci >= 0) & (ci < grid.nx) & (cj >= 0) & (cj < grid.nz)
        cg = np.full(len(cen), np.nan)
        cg[cin] = terrain[ci[cin] * grid.nz + cj[cin]]
        buried = float(area[cg > cen[:, 1]].sum() / area.sum()) if area.sum() else 0.0

        # Who rests on it, who touches it.
        topmap = np.full(sub.nx * sub.nz, np.nan)
        topmap[tcells] = top
        gndmap = np.full(sub.nx * sub.nz, np.nan)
        gndmap[tcells] = gnd
        expmap = np.zeros(sub.nx * sub.nz, bool)
        expmap[tcells] = exposed
        rests, touches = [], []
        for q, v, qlo, qhi in placed_other:
            if q["name"] == p["name"]:
                continue
            if (qlo > hi + 0.3).any() or (qhi < lo - 0.3).any():
                continue
            qi, qj = sub.ij(v[:, 0], v[:, 2])
            m = (qi >= 0) & (qi < sub.nx) & (qj >= 0) & (qj < sub.nz)
            if not m.any():
                continue
            cell = qi[m] * sub.nz + qj[m]
            vy = v[m, 1]
            ex = expmap[cell]
            if not ex.any():
                continue
            cell, vy = cell[ex], vy[ex]
            gap = vy - topmap[cell]
            near = np.abs(gap) < 0.3
            if near.any():
                touches.append(q["name"])
            # Lowest vertex of `q` per footprint cell: on the rock top, clear of the ground.
            o = np.lexsort((vy, cell))
            c2, y2 = cell[o], vy[o]
            first = np.ones(len(c2), bool)
            first[1:] = c2[1:] != c2[:-1]
            c2, y2 = c2[first], y2[first]
            lowgap = y2 - topmap[c2]
            on_top = (lowgap > -0.2) & (lowgap < 0.4) & (topmap[c2] - gndmap[c2] > 0.25)
            if on_top.sum() >= 4 and on_top.mean() >= 0.3:
                rests.append(q["name"])
        rows.append({
            "name": p["name"], "model": p["model"], "msb": p["msb"],
            "pos": [round(float(x), 2) for x in p["pos"]],
            "rot": [round(float(x), 2) for x in p["rot"]], "scale": [round(float(x), 3) for x in p["scale"]],
            "exposed_height": round(exposed_height, 2), "exposed_area": round(exposed_area, 2),
            "exposed_volume": round(exposed_volume, 2), "buried_fraction": round(buried, 3),
            "height_p98": round(height_p98, 2), "mean_protrusion": round(mean_protrusion, 2),
            "area_above_0_5m": round(area_50, 2), "area_above_1m": round(area_100, 2),
            "exposed_span": round(span, 2),
            "floating_area": round(floating_area, 2),
            "terrain_under_vertices": {
                "min": round(float(np.nanmin(vground)), 2) if np.isfinite(vground).any() else None,
                "max": round(float(np.nanmax(vground)), 2) if np.isfinite(vground).any() else None,
                "missing": int(np.isnan(vground).sum())},
            "supports": sorted(set(rests)), "touches": sorted(set(touches) - set(rests)),
            "_cells": (sub, tcells[exposed]),
        })
    for r in rows:
        r["class"] = classify(r)
    return rows, used, grid, terrain


def classify(r):
    """Minor eruption when small in every measure and nothing rests on it."""
    if r["terrain_under_vertices"]["min"] is None:
        return "no_terrain"
    if r["exposed_area"] == 0:
        return "buried"
    small = (r["height_p98"] <= MINOR_MAX_HEIGHT and r["mean_protrusion"] <= MINOR_MAX_PROTRUSION
             and r["area_above_1m"] <= MINOR_MAX_AREA_1M)
    if not small:
        return "feature"
    if r["supports"]:
        return "support"
    return "minor"


# Parts named with `--part` are being hidden by hand, so they get grass data whatever their class.
ASKED = set()


def grass_fill(rows, tile, grass):
    """Grass vertices under each minor rock's exposed footprint and the ring to fill with."""
    if not grass:
        return
    for r in rows:
        # A rock named with `--part` gets its grass too, whatever its class: it is being hidden by hand.
        if r["class"] != "minor" and r["name"] not in ASKED:
            continue
        sub, cells = r["_cells"]
        fp = set(cells.tolist())
        reach = int(math.ceil(RING / sub.cell))
        ci, cj = cells // sub.nz, cells % sub.nz
        inside, ring, cover = [], [], 0
        rx0, rz0 = sub.x0 - RING, sub.z0 - RING
        rx1, rz1 = sub.x0 + sub.nx * sub.cell + RING, sub.z0 + sub.nz * sub.cell + RING
        fpmask = np.zeros((sub.nx + 2 * reach, sub.nz + 2 * reach), bool)
        fpmask[ci + reach, cj + reach] = True
        # Dilate the footprint by `RING` (square structuring element, cheap and close enough).
        dil = np.zeros_like(fpmask)
        for di in range(-reach, reach + 1):
            for dj in range(-reach, reach + 1):
                dil |= np.roll(np.roll(fpmask, di, 0), dj, 1)
        for gi, g in enumerate(grass):
            pos = g["pos"]
            m = (pos[:, 0] >= rx0) & (pos[:, 0] < rx1) & (pos[:, 2] >= rz0) & (pos[:, 2] < rz1)
            for vi in np.nonzero(m)[0]:
                i = int(math.floor((pos[vi, 0] - sub.x0) / sub.cell)) + reach
                j = int(math.floor((pos[vi, 2] - sub.z0) / sub.cell)) + reach
                if not (0 <= i < fpmask.shape[0] and 0 <= j < fpmask.shape[1]):
                    continue
                entry = {"file": g["file"], "index": int(vi),
                         "pos": [round(float(x), 2) for x in pos[vi]],
                         "dens": [round(float(x), 3) for x in g["dens"][vi]]}
                if fpmask[i, j]:
                    inside.append(entry)
                elif dil[i, j]:
                    ring.append(entry)
            # Does any grass triangle cover each footprint cell centre?
            tri = pos[g["faces"]]
            cx = tri[:, :, 0]
            cz = tri[:, :, 2]
            near = ((cx.max(1) >= rx0) & (cx.min(1) < rx1) & (cz.max(1) >= rz0) & (cz.min(1) < rz1))
            if near.any():
                c, _ = rasterize(tri[near], sub, "max")
                cover += len(fp & set(c.tolist()))
        r["grass_inside"] = inside
        r["grass_ring_count"] = len(ring)
        r["grass_ring_mean"] = ([round(float(x), 3) for x in np.mean([e["dens"] for e in ring], 0)]
                                if ring else None)
        r["grass_ring_files"] = sorted({e["file"] for e in ring})
        r["grass_cover_fraction"] = round(cover / len(fp), 3) if fp else 0.0


# --------------------------------------------------------------------------- calibration

def rim_vertices(pos, tris):
    """Vertex indices on the open boundary of a mesh, after welding coincident positions."""
    if not len(tris):
        return np.zeros(0, np.int64)
    key = np.round(pos * 1000).astype(np.int64)
    _, weld = np.unique(key, axis=0, return_inverse=True)
    weld = weld.ravel()
    t = weld[tris]
    e = np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
    e.sort(1)
    uniq, count = np.unique(e, axis=0, return_counts=True)
    rim_welded = np.unique(uniq[count == 1].ravel())
    first = np.full(weld.max() + 1, -1)
    first[weld[::-1]] = np.arange(len(weld))[::-1]
    return first[rim_welded]

def calibrate(tile, cache, args):
    """Score every rotation order and sign set by how much rock floats above the terrain."""
    import itertools

    tx, ty = tile_coords(tile)
    grid = Grid(-128 - MARGIN, -128 - MARGIN, int((256 + 2 * MARGIN) / CELL), int((256 + 2 * MARGIN) / CELL))
    tris, _ = terrain_triangles(tile, cache, tx, ty, grid)
    tc, tv = rasterize(tris, grid, "max")
    terrain = dense(grid, tc, tv)
    parts = tile_parts(tile)
    tilted = []
    for p in parts:
        g = model_geometry(p["model"], cache)
        if g and g["sane"] and g["rock_share"] >= 0.5 and (abs(p["rot"][0]) > 3 or abs(p["rot"][2]) > 3):
            tilted.append((p, g))
    print(f"{len(tilted)} tilted rock placements")
    # Rock meshes are open shells whose rim the artist sinks into the ground. With the right
    # rotation the rim vertices sit at or under the terrain; with a wrong one they lift into air.
    rims = []
    for p, g in tilted:
        rims.append((p, g["pos"][rim_vertices(g["pos"], g["rock_tris"])]))
    print(f"{sum(len(r) for _, r in rims)} rim vertices")
    results = []
    for order in ["".join(o) for o in itertools.permutations("XYZ")]:
        for signs in itertools.product((1, -1), repeat=3):
            lifted, total = 0, 0
            floating, footprint = 0, 0
            for (p, g), (_, rim) in zip(tilted, rims):
                c, bottom = rasterize(place(g["pos"], p, order, signs)[g["rock_tris"]], grid, "min")
                gnd = terrain[c]
                ok = ~np.isnan(gnd)
                floating += int((bottom[ok] - gnd[ok] > 0.15).sum())
                footprint += int(ok.sum())
                if not len(rim):
                    continue
                w = place(rim, p, order, signs)
                i, j = grid.ij(w[:, 0], w[:, 2])
                ok = (i >= 0) & (i < grid.nx) & (j >= 0) & (j < grid.nz)
                gnd = terrain[i[ok] * grid.nz + j[ok]]
                have = ~np.isnan(gnd)
                lifted += int((w[ok][have, 1] - gnd[have] > 0.3).sum())
                total += int(have.sum())
            results.append((floating / max(footprint, 1), lifted / max(total, 1), order, signs))
    results.sort()
    print("ranked by floating share of footprint (rock underside more than 0.15 m above terrain);")
    print("rim share = rim vertices more than 0.3 m above terrain")
    for share, rim_share, order, signs in results:
        print(f"order {order} signs {signs}: floating {share:.4f} rim {rim_share:.4f}")


# --------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tile", action="append", required=True)
    ap.add_argument("--part", action="append")
    ap.add_argument("--order", default="XZY")
    ap.add_argument("--families", default="AEG004,AEG100",
                    help="asset families scored as rock (AEG004 rocks, AEG100 cliffs)")
    ap.add_argument("--out-dir", default=os.path.join(REPO, "target/rock-scan"))
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--quiet", action="store_true", help="counts only, no per-rock table")
    args = ap.parse_args()
    if not os.path.exists(DUMP_BIN):
        sys.exit(f"{DUMP_BIN} missing: cargo build -p er-flver --example flver-tri-dump --release")
    cache = Cache(args.out_dir)
    if args.calibrate:
        for t in args.tile:
            calibrate(t, cache, args)
        return
    summary = {}
    for tile in args.tile:
        rows, used, grid, terrain = analyse_tile(tile, cache, args)
        grass_fill(rows, tile, tile_grass(tile))
        rows.sort(key=lambda r: (r["exposed_volume"], r["exposed_area"]))
        counts = defaultdict(int)
        for r in rows:
            counts[r["class"]] += 1
        summary[tile] = dict(counts)
        if not args.quiet:
            print(f"\n== {tile}: {len(rows)} rock placements, terrain from {len(used)} pieces")
            print(f"{'part':38} {'model':11} {'h_exp':>6} {'area':>7} {'a>0.5':>6} {'a>1':>6} {'span':>5} "
                  f"{'vol':>7} {'buried':>6} {'h98':>5} {'prot':>5} {'sup':>3} {'tch':>3} class   grass in/ring/cover")
            for r in rows:
                g = ""
                if r["class"] == "minor":
                    g = f"{len(r['grass_inside'])}/{r['grass_ring_count']}/{r['grass_cover_fraction']}"
                print(f"{r['name']:38} {r['model']:11} {r['exposed_height']:6.2f} {r['exposed_area']:7.1f} "
                      f"{r['area_above_0_5m']:6.1f} {r['area_above_1m']:6.1f} {r['exposed_span']:5.1f} "
                      f"{r['exposed_volume']:7.1f} {r['buried_fraction']:6.2f} {r['height_p98']:5.2f} {r['mean_protrusion']:5.2f} "
                      f"{len(r['supports']):3d} {len(r['touches']):3d} {r['class']:7} {g}")
        os.makedirs(args.out_dir, exist_ok=True)
        for r in rows:
            r.pop("_cells", None)
        with open(os.path.join(args.out_dir, f"{tile}.json"), "w") as f:
            json.dump({"tile": tile, "thresholds": {
                "minor_max_height_p98": MINOR_MAX_HEIGHT, "minor_max_mean_protrusion": MINOR_MAX_PROTRUSION,
                "minor_max_area_above_1m": MINOR_MAX_AREA_1M, "ring_m": RING, "cell_m": CELL,
                "rotation_order": args.order},
                "grass_slots": grass_slots(tile), "terrain_pieces": used,
                "hide": [r["name"] for r in rows if r["class"] == "minor"],
                "rocks": rows}, f, indent=1)
    print("\ncounts:", json.dumps(summary))


if __name__ == "__main__":
    main()
