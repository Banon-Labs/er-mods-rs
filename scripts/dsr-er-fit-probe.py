#!/usr/bin/env python3
"""Measure how a builder export fits a Dark Souls Remastered character onto Elden Ring's player
skeleton, how it moves, and whether it clears the gates the approved c2280 build sets.

Usage: dsr-er-fit-probe.py <source.flver> <FC_M_0000.flver> <export-dir> [--json <out>] [--gate]
       dsr-er-fit-probe.py --selftest

<source.flver> is the raw DSR character (`<id>.flver` out of `chr/<id>.chrbnd.dcx`) and
<FC_M_0000.flver> the vanilla Elden Ring donor, both as `mushroom-man-builder extract-inputs`
writes them; <export-dir> is what `mushroom-man-builder export-obj` wrote for that source. Game
files are read from there and never written into the repo. The player skeleton and the three
clips come from the unpacked c0000 anibnd through `er-hkx-pose.py` (`ER_PLAYER_SKELETON_HKX`,
`ER_PLAYER_HKX_ROOT`); `--selftest` needs none of it.

What it measures, all in Elden Ring model space with the export skinned the way the game does
(linear blend of the four heaviest Elden Ring bones per vertex):

- every triangle edge of the export against the same source edge times the fit's girth scale,
  at bind and in frame 0 of the standing idle a000_000000, and the triangles turned inside out
  at bind and in that idle frame -- split into hand and finger creases per side and the rest;
- the arm vertices (Elden Ring targets under `L_Clavicle`/`R_Clavicle`) the idle buries in the
  body, a bounding-box test per 0.05 m height band;
- how close the digit tips (vertices whose dominant source bone is a finger's last segment)
  close on the weapon bone in the idle, and how far the palm centroid (vertices whose dominant
  Elden Ring target is `<side>_Hand` or `<side>_Finger*`) strays from it over each clip;
- the gap between the two leg trunks (targets under `L_Calf`/`R_Calf`) below 0.3 m, in the idle
  and in the source's own bind;
- the worst frame, sampled every third frame, of the idle loop, R1 a020_030000 and running R1
  a020_030200: edges the pose stretches past 1.5x or squashes under 0.67x against the fitted bind
  shape, and how many of those cross the arm-to-body seam;
- where the fit put the source's weapon bone, against Elden Ring's `R_Weapon` at bind;
- the vanilla references in the same run: FC_M_0000 meshes 8 (right hand) and 10 (left hand) for
  the creases, palm and tip distances, and meshes 0-13 for the buried-arm heuristic.

Parts are decided by ancestry in the player skeleton, never by name substrings: arm is the
clavicle subtree, leg the thigh subtree, leg trunk the calf subtree, and everything else torso.

Gates (`--gate` exits 1 when any fails; the thresholds are c2280's exact counts in its own run,
on the export the builder writes for c2280 at base 6c74c523):

- bind inside-out = 0;
- idle inside-out = 0 outside hand and finger creases, and creases per side <= the vanilla hand's
  in this run (48 right, 43 left);
- worst-frame bad edges as a share of edges <= 126/5124 idle, 560/5124 R1 a020_030000 and
  534/5124 running R1 a020_030200;
- the fit's grip anchor <= 0.03 m from `R_Weapon` at bind, only when fit.json names one;
- idle palm centroid to weapon <= max(0.03, vanilla + 0.01) m per side;
- idle mean tip to weapon <= c2280's own unrounded value per side (`C2280_IDLE_TIP_TO_WEAPON_M`);
- buried arm vertices <= 14% of the arm vertices;
- idle leg-trunk gap >= the same gap in the source's bind.

Reported and never gated: the R1 palm gaps (those clips carry no finger tracks, so every hand is
skinned open there and even the vanilla hand strays 0.04 m), the seam counts, and the edge
counts against the source shape.

The export directory
====================

The probe reads four files from <export-dir>. The OBJ and the weight TSV are what `export-obj`
has always written; fit.json and source-map.tsv are the sidecars it writes beside them.

The OBJ: `v x y z` lines (Elden Ring model space, metres) and `f a b c` lines whose corners are
1-based vertex references (any `/vt/vn` parts are ignored). `vt` and `vn` lines are ignored.

The weight TSV: one header line, then rows of seven tab-separated columns
`vertex, slot, source_mesh_bone_slot, source_global_bone_index, source_bone, er_target_bone,
weight`. The probe reads `vertex` (0-based OBJ index), `er_target_bone` and `weight`; rows for one
vertex are summed per target, the heaviest four kept and normalised, as the patcher does. A
target spelled `<...>` (`<unused>`, `<missing>`) carries no skin weight.

fit.json: one UTF-8 JSON object.

    format       1                     required; any other value is refused
    character    "c2650"               optional; printed
    scale        1.0268                required number; the girth scale k: a source edge times k
                                       is the length the bind and idle distortion counts compare
                                       against (c2280: `FitParams::scale`, 1.225)
    params       {"arm_lean": 0.0}     required object, may be empty; the resolved fit
                                       parameters, printed and copied into --json, never read
    stretch      {"L UpperArm": 1.084} required object, may be empty; source bone name to the
                                       stretch along that bone the fit applied; printed, never
                                       read
    grip_anchor  [x, y, z] or null     required; where the fit put the source's right-hand
                                       weapon bone at bind, in Elden Ring model space (metres,
                                       +Y up, facing -Z, right hand on -X); null when the source
                                       has no weapon bone
    obj          "c2650.obj"           optional; the OBJ's file name inside <export-dir>
    weights      "c2650_weights.tsv"   optional; the weight TSV's file name inside <export-dir>

Without `obj` and `weights` the directory must hold exactly one `*.obj` and one `*weights.tsv`.

source-map.tsv: tab-separated UTF-8 with LF line ends. The first line is exactly these five
names joined by tabs:

    export_vertex  source_mesh  source_vertex  source_bone  role

then one row per OBJ vertex, in OBJ order, `export_vertex` counting 0, 1, 2, ... without gaps.

    export_vertex  the vertex's 0-based OBJ index (the weight TSV's `vertex`)
    source_mesh    0-based index of the mesh in <source.flver>, every mesh counted in file order;
                   -1 for a cap
    source_vertex  0-based index of the vertex in that mesh's vertex buffer; -1 for a cap
    source_bone    the name of the source bone carrying the largest weight on that source vertex,
                   resolved through the mesh's bone palette into the FLVER's bone table; on a tie
                   the higher bone-table index wins; `-` for a cap
    role           what that bone is, one of:
                     trunk       pelvis, spine, neck, head, jaw
                     arm         clavicle, upper arm, forearm, and their twist bones
                     hand        the hand or palm bone
                     finger      a finger or digit segment with a further segment below it
                     finger_tip  the last segment of a finger or digit chain (c2280
                                 `LArmDigit12`/`22`/`32`, c2650 `L Finger01`/`11`/`21`); a Nub
                                 is not a segment
                     leg         thigh, calf, foot, toe, and their twist bones
                     cloth       a cloth or other helper bone the fit collapses onto an ancestor
                     prop        a held item's subtree (`R_weapon` and below)
                     other       anything else carrying weight
                     cap         a vertex the fit added; its row is `-1, -1, -, cap`

The probe checks every non-cap row's `source_bone` against its own reading of the source FLVER,
so a row that points at the wrong source vertex is refused rather than measured. A cap's source
position, which the distortion counts compare against, is the middle of the source positions of
its non-cap neighbours.

Without source-map.tsv the export is read as an identity map onto the source's first mesh:
export vertex i is mesh 0 vertex i while i is below that mesh's vertex count, the rest are caps,
and each role comes from the bone names and hierarchy (`fallback_roles`). That is what the c2280
export is today. Without fit.json the girth scale is c2280's `FitParams::default()` 1.225, and the
grip anchor is null.
"""

import argparse
import importlib.util
import json
import math
import re
import struct
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

BAD_STRETCH = 1.5
BAD_SQUASH = 0.67
# c2280's `FitParams::default().scale`: the girth scale when an export carries no fit.json.
LEGACY_C2280_SCALE = 1.225
SOURCE_MAP = "source-map.tsv"
SOURCE_MAP_HEADER = ("export_vertex", "source_mesh", "source_vertex", "source_bone", "role")
FIT_JSON = "fit.json"
FIT_FORMAT = 1
ROLES = ("trunk", "arm", "hand", "finger", "finger_tip", "leg", "cloth", "prop", "other", "cap")
CLIPS = ("idle", "r1", "run_r1")
CLIP_LABELS = {"idle": "idle loop", "r1": "R1 a020_030000", "run_r1": "running R1 a020_030200"}
# The vanilla donor's hand meshes, and the skin-region meshes the body reference is taken from.
VANILLA_HANDS = (("R", 8), ("L", 10))
VANILLA_BODY_MESHES = range(14)
# Height of the bands the buried-arm test compares an arm vertex against, and the leg trunks'
# ceiling for the gap between them.
BAND_M = 0.05
LEG_GAP_CEILING_M = 0.3

# The gate thresholds: c2280's exact counts in its own run of this probe, on the export the
# builder writes for c2280 at base 6c74c523 (1108 vertices, 1708 triangles, 5124 edges). The tip
# distances are that run's `idle_L_tip_to_weapon_m` / `idle_R_tip_to_weapon_m` from --json, as
# written; a deliberate change to c2280's fit re-measures them the same way.
C2280_EDGES = 5124
C2280_WORST_BAD_EDGES = {"idle": 126, "r1": 560, "run_r1": 534}
C2280_IDLE_TIP_TO_WEAPON_M = {"L": 0.059783814283184536, "R": 0.056387579096454414}
BURIED_ARM_LIMIT = 0.14
GRIP_LIMIT_M = 0.03
PALM_FLOOR_M = 0.03
PALM_OVER_VANILLA_M = 0.01


# --- bytes -------------------------------------------------------------------------------------

def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def i16(b, o):
    return struct.unpack_from("<h", b, o)[0]


def vec3(b, o):
    return struct.unpack_from("<3f", b, o)


def utf16z(b, o):
    end = o
    while b[end : end + 2] != b"\0\0":
        end += 2
    return b[o:end].decode("utf-16-le")


# --- vectors -----------------------------------------------------------------------------------

def sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def length(a):
    return math.sqrt(sum(x * x for x in a))


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def qconj(q):
    return (-q[0], -q[1], -q[2], q[3])


def pose_tool():
    spec = importlib.util.spec_from_file_location("er_hkx_pose", Path(__file__).with_name("er-hkx-pose.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The pose tool's own quaternion product and rotation (x, y, z, w), so a skinned position is the
# same float the tool's forward kinematics would give. Importing it reads no game file.
HKX = pose_tool()
qmul, qrot = HKX._qmul, HKX._qrot


def fmt(p):
    return "({:+.3f}, {:+.3f}, {:+.3f})".format(*p)


def edges(f):
    return ((f[0], f[1]), (f[1], f[2]), (f[2], f[0]))


# --- FLVER -------------------------------------------------------------------------------------

class Flver:
    """The bone table and every mesh's vertices of a FLVER2 (DSR 0x2000C or Elden Ring 0x2001A).

    `meshes[m][v]` is `(position, [global bone index] * 4, [weight] * 4)`. A mesh with a bone
    palette (DSR) indexes it per vertex; one without (Elden Ring) indexes the bone table."""

    def __init__(self, data):
        b = self.data = data
        if b[:6] != b"FLVER\0":
            raise ValueError("not a FLVER")
        self.version = u32(b, 0x08)
        dummies, materials, nbones, nmesh = (u32(b, o) for o in (0x14, 0x18, 0x1C, 0x20))
        nvb, nface = u32(b, 0x24), u32(b, 0x50)
        bone_table = 0x80 + 0x40 * dummies + 0x20 * materials
        self.names, self.parents = [], []
        for i in range(nbones):
            o = bone_table + 0x80 * i
            name_off = u32(b, o + 0x0C)
            self.names.append(utf16z(b, name_off) if name_off < len(b) else "")
            self.parents.append(i16(b, o + 0x1C))
        self.mesh_table = bone_table + 0x80 * nbones
        self.face_table = self.mesh_table + 0x30 * nmesh
        self.vb_table = self.face_table + 0x20 * nface
        self.layout_table = self.vb_table + 0x20 * nvb
        self.meshes = [self._vertices(m) for m in range(nmesh)]

    def _mesh(self, m):
        return self.mesh_table + 0x30 * m

    def _list(self, count_at, offset_at):
        b = self.data
        return [u32(b, u32(b, offset_at) + 4 * i) for i in range(u32(b, count_at))]

    def _vertices(self, m):
        b, mesh = self.data, self._mesh(m)
        palette = self._list(mesh + 0x14, mesh + 0x1C)
        count, attrs = None, {}
        for vb_index in self._list(mesh + 0x28, mesh + 0x2C):
            vb = self.vb_table + 0x20 * vb_index
            layout = self.layout_table + 0x10 * u32(b, vb + 4)
            size, n, start = u32(b, vb + 8), u32(b, vb + 0x0C), u32(b, 0x0C) + u32(b, vb + 0x1C)
            if count not in (None, n):
                raise ValueError(f"mesh {m}: vertex buffers disagree on the count ({count}, {n})")
            count = n
            first = u32(b, layout + 0x0C)
            for k in range(u32(b, layout)):
                member = first + 0x14 * k
                off, kind, sem = u32(b, member + 4), u32(b, member + 8), u32(b, member + 0x0C)
                if sem in (0, 1, 2) and sem not in attrs:
                    attrs[sem] = (start, size, off, kind)
        if count is None:
            return []
        if 0 not in attrs:
            raise ValueError(f"mesh {m} has no position member")
        out = []
        for v in range(count):
            pos, idx, wts = None, [0] * 4, [0.0] * 4
            for sem, (start, size, off, kind) in attrs.items():
                o = start + v * size + off
                if sem == 0:
                    if kind not in (0x02, 0x03):
                        raise ValueError(f"mesh {m}: position type {kind:#x} is not read")
                    pos = vec3(b, o)
                elif sem == 1:
                    if kind in (0x1A, 0x16):
                        wts = [x / 32767 for x in struct.unpack_from("<4h", b, o)]
                    elif kind == 0x13:
                        wts = [x / 255 for x in b[o : o + 4]]
                    else:
                        raise ValueError(f"mesh {m}: bone weight type {kind:#x} is not read")
                else:
                    if kind in (0x11, 0x24):
                        idx = list(b[o : o + 4])
                    elif kind == 0x18:
                        idx = list(struct.unpack_from("<4H", b, o))
                    else:
                        raise ValueError(f"mesh {m}: bone index type {kind:#x} is not read")
            out.append((pos, [palette[i] if i < len(palette) else i for i in idx], wts))
        return out

    def lod0_triangles(self, m):
        """The mesh's first full-detail face set (flags 0), strips unrolled, degenerates dropped."""
        b, mesh = self.data, self._mesh(m)
        for index in self._list(mesh + 0x20, mesh + 0x24):
            fs = self.face_table + 0x20 * index
            if u32(b, fs) != 0:
                continue
            strip, count = b[fs + 4], u32(b, fs + 8)
            at = u32(b, 0x0C) + u32(b, fs + 0x0C)
            size = u32(b, fs + 0x18) or b[0x48] or 16
            if size == 16:
                ind, restart = list(struct.unpack_from(f"<{count}H", b, at)), 0xFFFF
            else:
                ind, restart = list(struct.unpack_from(f"<{count}I", b, at)), 0xFFFFFFFF
            return triangulate(bool(strip), ind, restart)
        return []

    def dominant_bone(self, m, v):
        """The bone carrying the most weight on a vertex; on a tie the higher bone index."""
        _, ids, ws = self.meshes[m][v]
        index = max(zip(ws, ids))[1]
        return self.names[index] if index < len(self.names) else ""


def triangulate(strip, ind, restart):
    tris = []
    if strip:
        flip = False
        for k in range(len(ind) - 2):
            a, b, c = ind[k : k + 3]
            if restart in (a, b, c):
                flip = False
                continue
            if a != b and b != c and a != c:
                tris.append((c, b, a) if flip else (a, b, c))
            flip = not flip
    else:
        for k in range(0, len(ind) - 2, 3):
            t = tuple(ind[k : k + 3])
            if len(set(t)) == 3:
                tris.append(t)
    return tris


# --- source roles ------------------------------------------------------------------------------

def _norm(name):
    return name.replace(" ", "").replace("_", "").strip().lower()


def bone_side(name):
    """`L` or `R` when the name says which side it is on (`L Hand`, `LArm1`, `R_weapon`)."""
    n = name.strip()
    if len(n) > 1 and n[0] in "LR" and (n[1] in " _" or n[1].isupper() or n[1].isdigit()):
        return n[0]
    return None


def fallback_roles(names, parents):
    """Every bone's source-map role, read off its name and the hierarchy.

    Used only when an export has no source-map.tsv: the builder's own classifier is the authority
    whenever it writes one."""
    children = defaultdict(list)
    for i, p in enumerate(parents):
        children[p].append(i)

    def is_finger(i):
        n = _norm(names[i])
        return ("finger" in n or "digit" in n) and not n.endswith("nub")

    def under_prop(i):
        while i >= 0:
            if "weapon" in _norm(names[i]):
                return True
            i = parents[i]
        return False

    roles = {}
    for i, name in enumerate(names):
        n = _norm(name)
        if under_prop(i):
            role = "prop"
        elif is_finger(i):
            role = "finger" if any(is_finger(c) for c in children[i]) else "finger_tip"
        elif "palm" in n or n.endswith("hand"):
            role = "hand"
        elif "cloth" in n:
            role = "cloth"
        elif any(k in n for k in ("clavicle", "upperarm", "forearm", "foretwist", "uparmtwist")) or \
                re.search(r"arm\d", n):
            role = "arm"
        elif any(k in n for k in ("thigh", "calf", "foot", "toe", "leg", "knee")):
            role = "leg"
        elif any(k in n for k in ("pelvis", "spine", "neck", "head", "jaw")):
            role = "trunk"
        else:
            role = "other"
        roles.setdefault(name, role)
    return roles


# --- the export directory ----------------------------------------------------------------------

def parse_fit(text):
    fit = json.loads(text)
    if not isinstance(fit, dict):
        raise ValueError("fit.json is not an object")
    if fit.get("format") != FIT_FORMAT:
        raise ValueError(f"fit.json format {fit.get('format')!r}; this probe reads {FIT_FORMAT}")
    for key in ("scale", "params", "stretch", "grip_anchor"):
        if key not in fit:
            raise ValueError(f"fit.json has no {key!r}")
    if not isinstance(fit["scale"], (int, float)) or not fit["scale"] > 0:
        raise ValueError("fit.json scale must be a positive number")
    if not isinstance(fit["params"], dict) or not isinstance(fit["stretch"], dict):
        raise ValueError("fit.json params and stretch must be objects")
    anchor = fit["grip_anchor"]
    if anchor is not None and not (isinstance(anchor, list) and len(anchor) == 3
                                   and all(isinstance(x, (int, float)) for x in anchor)):
        raise ValueError("fit.json grip_anchor must be [x, y, z] or null")
    for key in ("obj", "weights"):
        if key in fit and not (isinstance(fit[key], str) and "/" not in fit[key] and "\\" not in fit[key]):
            raise ValueError(f"fit.json {key} must be a bare file name")
    return fit


def legacy_fit():
    return {"format": FIT_FORMAT, "scale": LEGACY_C2280_SCALE, "params": {}, "stretch": {},
            "grip_anchor": None, "from": "absent: c2280 FitParams::default() scale assumed"}


def parse_obj(text):
    vs, faces = [], []
    for line in text.splitlines():
        p = line.split()
        if p and p[0] == "v":
            vs.append(tuple(map(float, p[1:4])))
        elif p and p[0] == "f":
            if len(p) != 4:
                raise ValueError(f"OBJ face is not a triangle: {line}")
            faces.append(tuple(int(t.split("/")[0]) - 1 for t in p[1:4]))
    for f in faces:
        if not all(0 <= i < len(vs) for i in f):
            raise ValueError(f"OBJ face {f} points past {len(vs)} vertices")
    return vs, faces


def parse_weights(text, n):
    """Per vertex: (dominant target including `<...>` rows, the four skin weights)."""
    acc = [defaultdict(float) for _ in range(n)]
    for line in text.splitlines()[1:]:
        if not line.strip():
            continue
        c = line.split("\t")
        if len(c) != 7:
            raise ValueError(f"weight row has {len(c)} columns: {line!r}")
        v = int(c[0])
        if not 0 <= v < n:
            raise ValueError(f"weight row names vertex {v} of {n}")
        acc[v][c[5]] += float(c[6])
    dominant, skin = [], []
    for a in acc:
        dominant.append(max(a, key=a.get) if a else "<none>")
        bones = {k: w for k, w in a.items() if not k.startswith("<")}
        top = sorted(bones.items(), key=lambda kv: -kv[1])[:4] or [("Spine2", 1.0)]
        total = sum(w for _, w in top)
        skin.append([(bone, w / total) for bone, w in top])
    return dominant, skin


def parse_source_map(text, n_export, source):
    """[(mesh, vertex, bone, role)] per export vertex, checked against the source FLVER."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines or tuple(lines[0].split("\t")) != SOURCE_MAP_HEADER:
        raise ValueError(f"{SOURCE_MAP} header must be {chr(9).join(SOURCE_MAP_HEADER)!r}")
    rows = lines[1:]
    if len(rows) != n_export:
        raise ValueError(f"{SOURCE_MAP} has {len(rows)} rows for {n_export} export vertices")
    out, wrong = [], []
    for i, line in enumerate(rows):
        c = line.split("\t")
        if len(c) != 5:
            raise ValueError(f"{SOURCE_MAP} row {i} has {len(c)} columns: {line!r}")
        vertex, mesh, src, bone, role = int(c[0]), int(c[1]), int(c[2]), c[3], c[4]
        if vertex != i:
            raise ValueError(f"{SOURCE_MAP} row {i} names export vertex {vertex}")
        if role not in ROLES:
            raise ValueError(f"{SOURCE_MAP} row {i}: role {role!r} is not one of {', '.join(ROLES)}")
        if (mesh, src, bone, role).count(-1) or role == "cap":
            if (mesh, src, bone, role) != (-1, -1, "-", "cap"):
                raise ValueError(f"{SOURCE_MAP} row {i}: a cap is -1, -1, -, cap: {line!r}")
        else:
            if not 0 <= mesh < len(source.meshes) or not 0 <= src < len(source.meshes[mesh]):
                raise ValueError(f"{SOURCE_MAP} row {i}: no vertex {src} in source mesh {mesh}")
            if source.dominant_bone(mesh, src) != bone:
                wrong.append((i, bone, source.dominant_bone(mesh, src)))
        out.append((mesh, src, bone, role))
    if wrong:
        sample = "; ".join(f"export {i}: map says {a!r}, source has {b!r}" for i, a, b in wrong[:4])
        raise ValueError(f"{SOURCE_MAP}: {len(wrong)} rows name a bone the source vertex is not "
                         f"dominated by ({sample})")
    return out


def identity_source_map(source, n_export):
    if not source.meshes:
        raise ValueError("the source FLVER has no mesh")
    first = source.meshes[0]
    if n_export < len(first):
        raise ValueError(f"no {SOURCE_MAP}, and the export's {n_export} vertices are fewer than "
                         f"the source's first mesh's {len(first)}: the export is not an identity "
                         "map, so it needs its source map")
    roles = fallback_roles(source.names, source.parents)
    out = []
    for v in range(len(first)):
        bone = source.dominant_bone(0, v)
        out.append((0, v, bone, roles.get(bone, "other")))
    return out + [(-1, -1, "-", "cap")] * (n_export - len(first))


def source_positions(source, smap, faces):
    """Each export vertex's source position; a cap takes the middle of its non-cap neighbours."""
    raw = [source.meshes[m][v][0] if m >= 0 else None for m, v, _, _ in smap]
    for extra, (m, _, _, _) in enumerate(smap):
        if m >= 0:
            continue
        rim = sorted({i for f in faces if extra in f for i in f if smap[i][0] >= 0})
        if not rim:
            raise ValueError(f"cap vertex {extra} has no source neighbour to stand on")
        raw[extra] = tuple(sum(raw[i][k] for i in rim) / len(rim) for k in range(3))
    return raw


class Export:
    """Everything the probe reads from an export directory, parsed and cross-checked."""

    def __init__(self, export_dir, source):
        export_dir = Path(export_dir)
        fit_path = export_dir / FIT_JSON
        self.fit = parse_fit(fit_path.read_text(encoding="utf-8")) if fit_path.is_file() else legacy_fit()
        self.obj_path = self._file(export_dir, "obj", "*.obj")
        self.weights_path = self._file(export_dir, "weights", "*weights.tsv")
        self.final, self.faces = parse_obj(self.obj_path.read_text(encoding="utf-8"))
        self.dominant_target, self.weights = parse_weights(
            self.weights_path.read_text(encoding="utf-8"), len(self.final))
        map_path = export_dir / SOURCE_MAP
        if map_path.is_file():
            self.source_map = parse_source_map(map_path.read_text(encoding="utf-8"), len(self.final), source)
            self.map_from = SOURCE_MAP
        else:
            self.source_map = identity_source_map(source, len(self.final))
            self.map_from = "identity onto source mesh 0, roles from bone names"
        self.raw = source_positions(source, self.source_map, self.faces)

    def _file(self, export_dir, key, pattern):
        if key in self.fit:
            path = export_dir / self.fit[key]
            if not path.is_file():
                raise ValueError(f"fit.json names {self.fit[key]!r}, which is not in {export_dir}")
            return path
        found = sorted(export_dir.glob(pattern))
        if len(found) != 1:
            raise ValueError(f"{export_dir} must hold exactly one {pattern}, found {len(found)}")
        return found[0]


# --- the player skeleton -----------------------------------------------------------------------

class Rig:
    """The player skeleton's names and parents, its bind pose and the sampled clip poses.

    A pose is a list of model-space `(translation, rotation xyzw, scale)` per bone."""

    def __init__(self, names, parents, bind, clips):
        self.names, self.parents, self.bind, self.clips = names, parents, bind, clips
        self.index = {n: i for i, n in enumerate(names)}

    def under(self, bone, ancestor):
        i = self.index.get(bone, -1)
        target = self.index.get(ancestor)
        while i >= 0:
            if i == target:
                return True
            i = self.parents[i]
        return False

    def part(self, bone):
        if self.under(bone, "L_Clavicle") or self.under(bone, "R_Clavicle"):
            return "arm"
        if self.under(bone, "L_Thigh") or self.under(bone, "R_Thigh"):
            return "leg"
        return "torso"

    def calf_side(self, bone):
        for side in "LR":
            if self.under(bone, f"{side}_Calf"):
                return side
        return None

    def at(self, pose, bone):
        return pose[self.index[bone]][0]

    def turn(self, pose, bone):
        i = self.index[bone]
        return qmul(pose[i][1], qconj(self.bind[i][1]))


def player_rig():
    """The rig from the unpacked c0000 anibnd: bind, and every third frame of the three clips."""
    hkx = HKX
    skeleton = hkx.load_skeleton()
    clips = {}
    for label, clip in (("idle", hkx.IDLE), ("r1", hkx.R1), ("run_r1", hkx.RUN_R1)):
        frames = len(hkx._frames(hkx.load_animation(*clip)))
        clips[label] = [hkx.pose_model(*clip, t=frame / 30.0) for frame in range(0, frames, 3)]
    return Rig(skeleton.names, skeleton.parents, hkx.pose_model(), clips)


def skin(positions, weights, rig, pose):
    """Linear blend skinning from the bind pose into `pose`, as the game does it."""
    out = []
    for v, ws in zip(positions, weights):
        acc = [0.0, 0.0, 0.0]
        for bone, w in ws:
            i = rig.index[bone]
            bt, bq, _ = rig.bind[i]
            pt, pq, _ = pose[i]
            moved = qrot(pq, qrot(qconj(bq), sub(v, bt)))
            for k in range(3):
                acc[k] += w * (pt[k] + moved[k])
        out.append(tuple(acc))
    return out


# --- measures ----------------------------------------------------------------------------------

def is_hand_bone(bone, side=None):
    sides = (side,) if side else ("L", "R")
    return any(bone == f"{s}_Hand" or bone.startswith(f"{s}_Finger") for s in sides)


def crease_side(dominants):
    """`L`/`R` when every corner of a triangle is carried by that side's hand or fingers."""
    for side in "LR":
        if all(is_hand_bone(d, side) for d in dominants):
            return side
    return None


def bad_edge(reference, current, a, b, scale=1.0):
    r = length(sub(reference[a], reference[b])) * scale
    return r > 1e-6 and not BAD_SQUASH <= length(sub(current[a], current[b])) / r <= BAD_STRETCH


def inside_out(rig, pose, bind_shape, posed, f, dominant):
    expected = qrot(rig.turn(pose, dominant[f[0]]),
                    cross(sub(bind_shape[f[1]], bind_shape[f[0]]), sub(bind_shape[f[2]], bind_shape[f[0]])))
    got = cross(sub(posed[f[1]], posed[f[0]]), sub(posed[f[2]], posed[f[0]]))
    return dot(expected, got) < 0


def idle_inside_out(rig, pose, bind_shape, posed, faces, dominant):
    """Inside-out triangles in a pose: creases per side, the rest, and where they sit."""
    creases, outside, where = Counter(), 0, Counter()
    for f in faces:
        if inside_out(rig, pose, bind_shape, posed, f, dominant):
            corners = [dominant[i] for i in f]
            side = crease_side(corners)
            if side:
                creases[side] += 1
            else:
                outside += 1
            where["+".join(sorted(set(corners)))] += 1
    return creases, outside, where


def buried(rig, posed, dominant):
    """Arm vertices whose |x| and |z| sit inside 90% of the body's within 0.05 m of their height."""
    arm = [i for i, d in enumerate(dominant) if rig.part(d) == "arm"]
    arm_set = set(arm)
    bands = defaultdict(list)
    for j in range(len(posed)):
        if j not in arm_set:
            bands[math.floor(posed[j][1] / BAND_M)].append(posed[j])
    count = 0
    for i in arm:
        x, y, z = posed[i]
        k = math.floor(y / BAND_M)
        band = [p for kk in (k - 1, k, k + 1) for p in bands.get(kk, ()) if abs(p[1] - y) < BAND_M]
        if band and abs(x) < max(abs(p[0]) for p in band) * 0.9 and abs(z) < max(abs(p[2]) for p in band) * 0.9:
            count += 1
    return count, len(arm)


def leg_gap(rig, positions, dominant):
    legs = {s: [] for s in "LR"}
    for i, d in enumerate(dominant):
        side = rig.calf_side(d)
        if side and positions[i][1] < LEG_GAP_CEILING_M:
            legs[side].append(i)
    if not legs["L"] or not legs["R"]:
        return None
    return min(length(sub(positions[a], positions[b])) for a in legs["L"] for b in legs["R"])


def centroid(points, sel):
    return tuple(sum(points[i][k] for i in sel) / len(sel) for k in range(3))


def measure(source, export, rig):
    """Every metric the gates and the report use, unrounded."""
    raw, final, faces, weights = export.raw, export.final, export.faces, export.weights
    scale = export.fit["scale"]
    dominant = [ws[0][0] for ws in weights]
    for bone in {b for ws in weights for b, _ in ws}:
        if bone not in rig.index:
            raise ValueError(f"the weight TSV targets {bone!r}, which the player skeleton lacks")
    m = {"vertices": len(final), "triangles": len(faces), "edges": 3 * len(faces)}

    bad = flipped = 0
    bad_by = Counter()
    for f in faces:
        group = "+".join(sorted({export.dominant_target[i] for i in f}))
        for a, b in edges(f):
            if bad_edge(raw, final, a, b, scale):
                bad += 1
                bad_by[group] += 1
        nr = cross(sub(raw[f[1]], raw[f[0]]), sub(raw[f[2]], raw[f[0]]))
        nf = cross(sub(final[f[1]], final[f[0]]), sub(final[f[2]], final[f[0]]))
        if dot(nr, nf) < 0:
            flipped += 1
    m.update(bind_bad_edges_vs_source_x_scale=bad, bind_inside_out=flipped,
             bind_bad_by_targets=dict(bad_by.most_common(14)))

    idle = rig.clips["idle"][0]
    posed = skin(final, weights, rig, idle)
    m["idle_bad_edges_vs_source_x_scale"] = sum(
        1 for f in faces for a, b in edges(f) if bad_edge(raw, posed, a, b, scale))
    creases, outside, where = idle_inside_out(rig, idle, final, posed, faces, dominant)
    m.update(idle_inside_out=sum(where.values()), idle_inside_out_outside_creases=outside,
             idle_inside_out_creases={s: creases[s] for s in "LR"},
             idle_inside_out_where=dict(where.most_common(10)))
    m["idle_arm_buried"], m["arm_vertices"] = buried(rig, posed, dominant)

    tips, palms = defaultdict(list), defaultdict(list)
    for i, (_, _, bone, role) in enumerate(export.source_map):
        if role == "finger_tip":
            side = bone_side(bone) or (dominant[i][0] if dominant[i][:2] in ("L_", "R_") else None)
            if side:
                tips[side].append(i)
        for side in "LR":
            if is_hand_bone(dominant[i], side):
                palms[side].append(i)
    m["tip_vertices"] = {s: len(tips[s]) for s in "LR"}
    m["palm_vertices"] = {s: len(palms[s]) for s in "LR"}
    for side in "LR":
        if tips[side]:
            grip = rig.at(idle, f"{side}_Weapon")
            m[f"idle_{side}_tip_to_weapon_m"] = sum(length(sub(posed[i], grip)) for i in tips[side]) / len(tips[side])
            m[f"bind_{side}_tip_to_weapon_m"] = (
                sum(length(sub(final[i], rig.at(rig.bind, f"{side}_Weapon"))) for i in tips[side]) / len(tips[side]))
    m["roles"] = dict(Counter(role for _, _, _, role in export.source_map).most_common())
    m["idle_leg_gap_m"] = leg_gap(rig, posed, dominant)
    m["source_bind_leg_gap_m"] = leg_gap(rig, raw, dominant)

    is_arm = [rig.part(d) == "arm" for d in dominant]
    seam = [f for f in faces if len({is_arm[i] for i in f}) == 2]
    m["seam_edges"] = 3 * len(seam)
    for label in CLIPS:
        gaps, worst, worst_seam, worst_by = {}, 0, 0, Counter()
        for pose in rig.clips[label]:
            p = skin(final, weights, rig, pose)
            worst_seam = max(worst_seam, sum(1 for f in seam for a, b in edges(f) if bad_edge(final, p, a, b)))
            count, by = 0, Counter()
            for f in faces:
                hits = sum(1 for a, b in edges(f) if bad_edge(final, p, a, b))
                if hits:
                    count += hits
                    by["+".join(sorted({rig.part(dominant[i]) for i in f}))] += hits
            if count > worst:
                worst, worst_by = count, by
            for side, sel in palms.items():
                gap = length(sub(rig.at(pose, f"{side}_Weapon"), centroid(p, sel)))
                gaps[side] = max(gaps.get(side, 0.0), gap)
        m[f"{label}_worst_frame_bad_edges"] = worst
        m[f"{label}_worst_by_part"] = dict(worst_by.most_common())
        m[f"{label}_worst_seam_bad_edges"] = worst_seam
        m[f"{label}_max_palm_to_weapon_m"] = {s: gaps[s] for s in sorted(gaps)}

    anchor = export.fit.get("grip_anchor")
    m["grip_anchor"] = anchor
    m["grip_anchor_to_weapon_m"] = (
        None if anchor is None else length(sub(tuple(anchor), rig.at(rig.bind, "R_Weapon"))))
    return m


def vanilla_reference(donor, rig):
    """The vanilla FC_M_0000 hands and body on the same clips: what the gates are relative to."""
    def vertices(mesh):
        out = []
        for pos, ids, ws in donor.meshes[mesh]:
            pairs = sorted([(donor.names[i], w) for i, w in zip(ids, ws) if w > 0], key=lambda t: -t[1])
            total = sum(w for _, w in pairs)
            out.append((pos, [(bone, w / total) for bone, w in pairs]))
        return out

    ref = {}
    idle = rig.clips["idle"][0]
    for side, mesh in VANILLA_HANDS:
        vs = vertices(mesh)
        final = [p for p, _ in vs]
        weights = [ws for _, ws in vs]
        dominant = [ws[0][0] for ws in weights]
        on_side = sum(1 for d in dominant if d.startswith(f"{side}_"))
        if on_side * 2 < len(dominant):
            raise ValueError(f"FC_M_0000 mesh {mesh} is not the {side} hand ({on_side} of "
                             f"{len(dominant)} vertices on {side}_ bones): the donor's layout moved")
        faces = donor.lod0_triangles(mesh)
        palm = [i for i, d in enumerate(dominant) if is_hand_bone(d, side)]
        tips = [i for i, d in enumerate(dominant) if d in tuple(f"{side}_Finger{n}2" for n in range(5))]
        posed = skin(final, weights, rig, idle)
        creases, outside, _ = idle_inside_out(rig, idle, final, posed, faces, dominant)
        r = {"triangles": len(faces), "palm_vertices": len(palm), "tip_vertices": len(tips),
             "idle_inside_out_creases": creases[side], "idle_inside_out_other": outside,
             "idle_bad_edges_vs_bind": sum(1 for f in faces for a, b in edges(f) if bad_edge(final, posed, a, b)),
             "idle_tip_to_weapon_m": sum(length(sub(posed[i], rig.at(idle, f"{side}_Weapon"))) for i in tips) / len(tips)}
        for label in CLIPS:
            r[f"{label}_max_palm_to_weapon_m"] = max(
                length(sub(rig.at(pose, f"{side}_Weapon"), centroid(skin(final, weights, rig, pose), palm)))
                for pose in rig.clips[label])
        ref[side] = r
    body = [v for mesh in VANILLA_BODY_MESHES for v in vertices(mesh)]
    posed = skin([p for p, _ in body], [ws for _, ws in body], rig, idle)
    count, arm = buried(rig, posed, [ws[0][0] for _, ws in body])
    ref["body"] = {"meshes": f"{VANILLA_BODY_MESHES.start}-{VANILLA_BODY_MESHES.stop - 1}",
                   "vertices": len(body), "idle_arm_buried": count, "arm_vertices": arm}
    return ref


# --- gates -------------------------------------------------------------------------------------

def gates(m, vanilla):
    """[(name, measured, limit, passed or None when it does not apply)]."""
    out = [("bind inside-out", m["bind_inside_out"], "= 0", m["bind_inside_out"] == 0),
           ("idle inside-out outside hand and finger creases", m["idle_inside_out_outside_creases"], "= 0",
            m["idle_inside_out_outside_creases"] == 0)]
    for side in "LR":
        limit = vanilla[side]["idle_inside_out_creases"]
        got = m["idle_inside_out_creases"][side]
        out.append((f"idle {side} hand creases inside out", got, f"<= {limit} (vanilla)", got <= limit))
    for label in CLIPS:
        got, limit = m[f"{label}_worst_frame_bad_edges"], C2280_WORST_BAD_EDGES[label]
        out.append((f"{CLIP_LABELS[label]} worst-frame bad edges", f"{got}/{m['edges']}",
                    f"<= {limit}/{C2280_EDGES}", got * C2280_EDGES <= limit * m["edges"]))
    anchor = m["grip_anchor_to_weapon_m"]
    out.append(("grip anchor to R_Weapon at bind", "-" if anchor is None else f"{anchor:.4f} m",
                f"<= {GRIP_LIMIT_M} m", None if anchor is None else anchor <= GRIP_LIMIT_M))
    for side in "LR":
        limit = max(PALM_FLOOR_M, vanilla[side]["idle_max_palm_to_weapon_m"] + PALM_OVER_VANILLA_M)
        got = m["idle_max_palm_to_weapon_m"].get(side)
        out.append((f"idle {side} palm centroid to weapon", "-" if got is None else f"{got:.4f} m",
                    f"<= {limit:.4f} m", got is not None and got <= limit))
        limit = C2280_IDLE_TIP_TO_WEAPON_M[side]
        got = m.get(f"idle_{side}_tip_to_weapon_m")
        out.append((f"idle {side} mean tip to weapon", "-" if got is None else f"{got:.6f} m",
                    f"<= {limit:.6f} m (c2280)", got is not None and got <= limit))
    arm = m["arm_vertices"]
    out.append(("idle arm vertices buried", f"{m['idle_arm_buried']}/{arm}", f"<= {BURIED_ARM_LIMIT:.0%}",
                arm > 0 and m["idle_arm_buried"] <= BURIED_ARM_LIMIT * arm))
    got, limit = m["idle_leg_gap_m"], m["source_bind_leg_gap_m"]
    out.append(("idle leg-trunk gap", "-" if got is None else f"{got:.4f} m",
                ">= source bind " + ("-" if limit is None else f"{limit:.4f} m"),
                got is not None and limit is not None and got >= limit))
    return out


# --- report ------------------------------------------------------------------------------------

def report(source_path, export, m, vanilla, verdicts):
    fit = export.fit
    print(f"== {source_path} -> {export.obj_path.parent}")
    print(f"  fit.json: {fit.get('from', 'present')}; character {fit.get('character', '-')}, "
          f"girth scale {fit['scale']}; source map: {export.map_from}")
    if fit["params"]:
        print("  params: " + ", ".join(f"{k} {v}" for k, v in fit["params"].items()))
    if fit["stretch"]:
        print("  stretch: " + ", ".join(f"{k} {v}" for k, v in fit["stretch"].items()))
    print(f"  {m['vertices']} vertices, {m['triangles']} triangles; roles: "
          + ", ".join(f"{k} {v}" for k, v in m["roles"].items()))
    print("== static distortion: each triangle edge's final length against source length x scale")
    print(f"  edges stretched >{BAD_STRETCH}x or squashed <{BAD_SQUASH}x: {m['bind_bad_edges_vs_source_x_scale']}"
          f"; triangles turned inside out: {m['bind_inside_out']}")
    for group, n in m["bind_bad_by_targets"].items():
        print(f"    {n:4d} bad edges on {group}")
    print("== the same mesh in the standing idle (a000_000000 frame 0), skinned as the game does")
    print(f"  edges stretched >{BAD_STRETCH}x or squashed <{BAD_SQUASH}x against the source's own shape: "
          f"{m['idle_bad_edges_vs_source_x_scale']}; triangles inside out: {m['idle_inside_out']} "
          f"({m['idle_inside_out_outside_creases']} outside hand creases, creases "
          f"L {m['idle_inside_out_creases']['L']} R {m['idle_inside_out_creases']['R']})")
    if m["idle_inside_out_where"]:
        print("  inside out on: " + ", ".join(f"{k} {v}" for k, v in m["idle_inside_out_where"].items()))
    print(f"  arm vertices buried inside the body: {m['idle_arm_buried']} of {m['arm_vertices']}")
    for side in "LR":
        if f"idle_{side}_tip_to_weapon_m" in m:
            print(f"  idle: {side} digit tips ({m['tip_vertices'][side]} vertices) sit "
                  f"{m[f'idle_{side}_tip_to_weapon_m']:.3f} m from the weapon bone on average "
                  f"({m[f'bind_{side}_tip_to_weapon_m']:.3f} m at bind)")
    gap, bind_gap = m["idle_leg_gap_m"], m["source_bind_leg_gap_m"]
    print("  idle: the two leg trunks come within "
          + ("-" if gap is None else f"{gap:.3f} m") + f" of each other below {LEG_GAP_CEILING_M} m"
          + ("" if bind_gap is None else f" ({bind_gap:.3f} m in the source's bind)"))
    for label in CLIPS:
        gaps = m[f"{label}_max_palm_to_weapon_m"]
        print(f"  {CLIP_LABELS[label]}: weapon bone to palm at most "
              + ", ".join(f"{s} {g:.3f} m" for s, g in gaps.items())
              + f"; worst frame has {m[f'{label}_worst_frame_bad_edges']} of {m['edges']} edges the pose"
              f" stretches or squashes past {BAD_STRETCH}x against the fitted bind shape, "
              f"{m[f'{label}_worst_seam_bad_edges']} of {m['seam_edges']} across the arm-to-body seam"
              " (" + ", ".join(f"{k} {v}" for k, v in m[f"{label}_worst_by_part"].items()) + ")")
    if m["grip_anchor"] is not None:
        print(f"  grip anchor {fmt(m['grip_anchor'])} sits {m['grip_anchor_to_weapon_m']:.4f} m from R_Weapon at bind")
    print("== vanilla FC_M_0000 in the same run")
    for side in "RL":
        v = vanilla[side]
        print(f"  {side} hand: {v['idle_inside_out_creases']} crease triangles inside out in the idle"
              f" ({v['idle_inside_out_other']} other), palm to weapon at most "
              + ", ".join(f"{CLIP_LABELS[c]} {v[f'{c}_max_palm_to_weapon_m']:.3f} m" for c in CLIPS)
              + f", tips {v['idle_tip_to_weapon_m']:.3f} m")
    body = vanilla["body"]
    print(f"  body meshes {body['meshes']}: {body['idle_arm_buried']} of {body['arm_vertices']} arm vertices buried")
    print("== gates")
    for name, got, limit, passed in verdicts:
        mark = "skip" if passed is None else ("pass" if passed else "FAIL")
        print(f"  {mark}  {name}: {got} ({limit})")


def run(source_path, donor_path, export_dir, json_out, gate):
    source = Flver(Path(source_path).read_bytes())
    donor = Flver(Path(donor_path).read_bytes())
    export = Export(export_dir, source)
    try:
        rig = player_rig()
    except (OSError, ValueError) as e:
        print(f"error: the player skeleton and clips are needed from the unpacked c0000 anibnd "
              f"(ER_PLAYER_SKELETON_HKX, ER_PLAYER_HKX_ROOT): {e}", file=sys.stderr)
        return 2
    m = measure(source, export, rig)
    vanilla = vanilla_reference(donor, rig)
    verdicts = gates(m, vanilla)
    report(source_path, export, m, vanilla, verdicts)
    failed = [v for v in verdicts if v[3] is False]
    if json_out:
        out = dict(m, what=f"{Path(source_path).name} on {export.obj_path}", fit=export.fit,
                   source_map=export.map_from, vanilla=vanilla,
                   gates=[{"name": n, "measured": g, "limit": l, "passed": p} for n, g, l, p in verdicts])
        Path(json_out).write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    if gate and failed:
        print(f"gate: {len(failed)} of {len(verdicts)} failed", file=sys.stderr)
        return 1
    return 0


# --- self-test ---------------------------------------------------------------------------------

def _flver_bytes(version, bones, meshes):
    """A FLVER2 with no dummies or materials, built for the self-test.

    bones: [(name, parent)]. meshes: [{"palette": [...], "buffers": [[(type, semantic,
    packed bytes per vertex)]], "face_sets": [(flags, strip, [indices])]}]."""
    nface = sum(len(m["face_sets"]) for m in meshes)
    nvb = sum(len(m["buffers"]) for m in meshes)
    head = 0x80
    bone_table = head
    mesh_table = bone_table + 0x80 * len(bones)
    face_table = mesh_table + 0x30 * len(meshes)
    vb_table = face_table + 0x20 * nface
    layout_table = vb_table + 0x20 * nvb
    tail = bytearray()
    tail_at = layout_table + 0x10 * nvb

    def put(blob):
        at = tail_at + len(tail)
        tail.extend(blob)
        while len(tail) % 4:
            tail.append(0)
        return at

    data = bytearray()
    tables = bytearray(tail_at)
    for i, (name, parent) in enumerate(bones):
        o = bone_table + 0x80 * i
        struct.pack_into("<I", tables, o + 0x0C, put(name.encode("utf-16-le") + b"\0\0"))
        struct.pack_into("<h", tables, o + 0x1C, parent)
    face_index = vb_index = 0
    for mi, mesh in enumerate(meshes):
        o = mesh_table + 0x30 * mi
        struct.pack_into("<I", tables, o + 0x14, len(mesh["palette"]))
        struct.pack_into("<I", tables, o + 0x1C, put(struct.pack(f"<{len(mesh['palette'])}I", *mesh["palette"])))
        faces = list(range(face_index, face_index + len(mesh["face_sets"])))
        struct.pack_into("<I", tables, o + 0x20, len(faces))
        struct.pack_into("<I", tables, o + 0x24, put(struct.pack(f"<{len(faces)}I", *faces)))
        for fi, (flags, strip, indices) in zip(faces, mesh["face_sets"]):
            fo = face_table + 0x20 * fi
            struct.pack_into("<I", tables, fo, flags)
            tables[fo + 4] = int(strip)
            struct.pack_into("<I", tables, fo + 8, len(indices))
            struct.pack_into("<I", tables, fo + 0x0C, len(data))
            data.extend(struct.pack(f"<{len(indices)}H", *indices))
            while len(data) % 16:
                data.append(0)
        face_index += len(faces)
        vbs = list(range(vb_index, vb_index + len(mesh["buffers"])))
        struct.pack_into("<I", tables, o + 0x28, len(vbs))
        struct.pack_into("<I", tables, o + 0x2C, put(struct.pack(f"<{len(vbs)}I", *vbs)))
        for vi, members in zip(vbs, mesh["buffers"]):
            size = sum(len(per_vertex[0]) for _, _, per_vertex in members)
            count = len(members[0][2])
            member_blob, offset = bytearray(), 0
            for kind, semantic, per_vertex in members:
                member_blob += struct.pack("<5I", 0, offset, kind, semantic, 0)
                offset += len(per_vertex[0])
            lo = layout_table + 0x10 * vi
            struct.pack_into("<I", tables, lo, len(members))
            struct.pack_into("<I", tables, lo + 0x0C, put(bytes(member_blob)))
            vo = vb_table + 0x20 * vi
            struct.pack_into("<4I", tables, vo, vi, vi, size, count)
            struct.pack_into("<I", tables, vo + 0x1C, len(data))
            for v in range(count):
                for _, _, per_vertex in members:
                    data.extend(per_vertex[v])
            while len(data) % 16:
                data.append(0)
        vb_index += len(vbs)
    out = bytes(tables) + bytes(tail)
    out += bytes(-len(out) % 16)
    header = bytearray(out[:head])
    header[:6] = b"FLVER\0"
    header[6:8] = b"L\0"
    struct.pack_into("<5I", header, 0x08, version, len(out), len(data), 0, 0)
    struct.pack_into("<3I", header, 0x1C, len(bones), len(meshes), nvb)
    header[0x48] = 16
    struct.pack_into("<2I", header, 0x50, nface, nvb)
    return bytes(header) + out[head:] + bytes(data)


def _pos(p):
    return struct.pack("<3f", *p)


def _w16(ws):
    return struct.pack("<4h", *(round(w * 32767) for w in ws))


def _w8(ws):
    return bytes(round(w * 255) for w in ws)


def _ids(ids):
    return bytes(ids)


def _synthetic_rig(clips):
    """A small symmetric player skeleton in an identity-rotation bind; poses move named bones."""
    layout = [("Master", -1, (0, 0, 0)), ("Pelvis", 0, (0, 1.0, 0)), ("Spine2", 1, (0, 1.3, 0))]
    for s, x in (("L", 1), ("R", -1)):
        layout += [(f"{s}_Clavicle", 2, (0.05 * x, 1.4, 0)), (f"{s}_UpperArm", None, (0.2 * x, 1.4, 0)),
                   (f"{s}_Hand", None, (0.5 * x, 1.0, 0)), (f"{s}_Finger1", None, (0.55 * x, 0.95, 0)),
                   (f"{s}_Finger12", None, (0.6 * x, 0.9, 0)), (f"{s}_Weapon", f"{s}_Hand", (0.55 * x, 0.98, 0)),
                   (f"{s}_Thigh", 1, (0.1 * x, 1.0, 0)), (f"{s}_Calf", None, (0.1 * x, 0.5, 0)),
                   (f"{s}_Foot", None, (0.1 * x, 0.1, 0)), (f"{s}_Toe0", None, (0.1 * x, 0.0, -0.1))]
    names, parents, bind = [], [], []
    for name, parent, at in layout:
        if parent is None:
            parent = len(names) - 1
        elif isinstance(parent, str):
            parent = names.index(parent)
        names.append(name)
        parents.append(parent)
        bind.append((tuple(map(float, at)), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0)))
    index = {n: i for i, n in enumerate(names)}

    def pose(moves):
        out = list(bind)
        for bone, (dt, q) in moves.items():
            t, _, s = bind[index[bone]]
            out[index[bone]] = (tuple(a + b for a, b in zip(t, dt)), q, s)
        return out
    return Rig(names, parents, bind, {label: [pose(m) for m in frames] for label, frames in clips.items()})


def selftest():
    failures = []

    def check(label, condition, detail=""):
        print(f"  {'ok  ' if condition else 'FAIL'}  {label}" + (f": {detail}" if detail and not condition else ""))
        if not condition:
            failures.append(label)

    def refuses(label, fn, needle):
        try:
            fn()
        except ValueError as e:
            check(label, needle in str(e), f"refused for another reason: {e}")
            return
        check(label, False, "accepted")

    # The reader: a DSR-style file with a bone palette, 16-bit weights and two meshes, the second
    # split over two vertex buffers with a float4 position.
    bones = [("Pelvis", -1), ("L Hand", 0), ("L Finger2", 1), ("L Finger21", 2), ("L Finger2Nub", -1),
             ("R_weapon", 0), ("R_W_Cloth_a1", 5), ("L Calf", 0), ("S_Cloth_a1", 0), ("master", -1),
             ("LArm1", 0), ("LArmPalm", 10), ("LArmDigit11", 11), ("LArmDigit12", 12)]
    dsr = _flver_bytes(0x2000C, bones, [
        {"palette": [1, 2, 3], "face_sets": [(0, False, [0, 1, 2])],
         "buffers": [[(0x02, 0, [_pos((1, 2, 3)), _pos((4, 5, 6)), _pos((7, 8, 9))]),
                      (0x1A, 1, [_w16((1, 0, 0, 0)), _w16((0.25, 0.75, 0, 0)), _w16((0.5, 0.5, 0, 0))]),
                      (0x11, 2, [_ids((0, 0, 0, 0)), _ids((0, 1, 0, 0)), _ids((1, 2, 0, 0))])]]},
        {"palette": [7, 0], "face_sets": [(0, False, [0, 1, 0])],
         "buffers": [[(0x03, 0, [_pos((0, 0.5, 0)) + b"\0" * 4, _pos((0, 0.25, 0)) + b"\0" * 4])],
                     [(0x13, 1, [_w8((1, 0, 0, 0)), _w8((0, 1, 0, 0))]),
                      (0x24, 2, [_ids((0, 0, 0, 0)), _ids((0, 1, 0, 0))])]]}])
    f = Flver(dsr)
    check("bone table names and parents", f.names[3] == "L Finger21" and f.parents[3] == 2, f"{f.names} {f.parents}")
    check("two meshes, every vertex", [len(m) for m in f.meshes] == [3, 2], str([len(m) for m in f.meshes]))
    check("positions read", f.meshes[0][1][0] == (4.0, 5.0, 6.0) and f.meshes[1][1][0] == (0.0, 0.25, 0.0),
          f"{f.meshes[0][1][0]} {f.meshes[1][1][0]}")
    check("palette resolves to the bone table", f.meshes[0][2][1][:2] == [2, 3] and f.meshes[1][1][1][:2] == [7, 0],
          f"{f.meshes[0][2][1]} {f.meshes[1][1][1]}")
    check("16-bit weights", abs(f.meshes[0][1][2][1] - 0.75) < 1e-4, str(f.meshes[0][1][2]))
    check("8-bit weights from a second buffer", f.meshes[1][1][2][:2] == [0.0, 1.0], str(f.meshes[1][1][2]))
    check("dominant bone, weight first", f.dominant_bone(0, 1) == "L Finger2", f.dominant_bone(0, 1))
    check("dominant bone, a tie goes to the higher index", f.dominant_bone(0, 2) == "L Finger21",
          f.dominant_bone(0, 2))
    check("a degenerate list triangle is dropped", f.lod0_triangles(1) == [], str(f.lod0_triangles(1)))

    # An Elden Ring-style file: no palette, a motion-blur face set ahead of a strip.
    er = _flver_bytes(0x2001A, [("Root", -1), ("Spine2", 0)], [
        {"palette": [], "face_sets": [(0x80000000, False, [0, 1, 2]), (0, True, [0, 1, 2, 3, 0xFFFF, 2, 3, 4])],
         "buffers": [[(0x02, 0, [_pos((i, 0, 0)) for i in range(5)]),
                      (0x11, 2, [_ids((1, 0, 0, 0))] * 5), (0x13, 1, [_w8((1, 0, 0, 0))] * 5)]]}])
    e = Flver(er)
    check("no palette reads global bone indices", e.meshes[0][0][1][0] == 1, str(e.meshes[0][0][1]))
    check("LOD0 skips the motion-blur set, unrolls the strip, restarts at 0xFFFF",
          e.lod0_triangles(0) == [(0, 1, 2), (3, 2, 1), (2, 3, 4)], str(e.lod0_triangles(0)))
    refuses("an unread weight type is refused, not guessed",
            lambda: Flver(_flver_bytes(0x2001A, [("Root", -1)], [
                {"palette": [], "face_sets": [], "buffers": [[(0x02, 0, [_pos((0, 0, 0))]),
                                                              (0x99, 1, [b"\0\0\0\0"])]]}])), "0x99")

    # Roles off the names and the hierarchy.
    roles = fallback_roles([n for n, _ in bones], [p for _, p in bones])
    expected = {"Pelvis": "trunk", "L Hand": "hand", "L Finger2": "finger", "L Finger21": "finger_tip",
                "R_weapon": "prop", "R_W_Cloth_a1": "prop", "L Calf": "leg", "S_Cloth_a1": "cloth",
                "master": "other", "LArm1": "arm", "LArmPalm": "hand", "LArmDigit11": "finger",
                "LArmDigit12": "finger_tip"}
    wrong = {k: roles.get(k) for k, v in expected.items() if roles.get(k) != v}
    check("fallback roles, c2280 and Biped spellings", not wrong, str(wrong))
    sides = {n: bone_side(n) for n in ("L Hand", "LArmDigit12", "R_weapon", "RArm1", "Root", "Rig_x", "Pelvis")}
    check("bone sides", sides == {"L Hand": "L", "LArmDigit12": "L", "R_weapon": "R", "RArm1": "R",
                                  "Root": None, "Rig_x": None, "Pelvis": None}, str(sides))

    # fit.json.
    good = {"format": 1, "scale": 1.5, "params": {}, "stretch": {"L Hand": 0.9}, "grip_anchor": None}
    check("fit.json read", parse_fit(json.dumps(good))["scale"] == 1.5)
    refuses("fit.json without a grip_anchor key", lambda: parse_fit(json.dumps(
        {k: v for k, v in good.items() if k != "grip_anchor"})), "grip_anchor")
    refuses("fit.json of another format", lambda: parse_fit(json.dumps(dict(good, format=2))), "format")
    refuses("fit.json with a two-number anchor", lambda: parse_fit(json.dumps(dict(good, grip_anchor=[1, 2]))),
            "grip_anchor")
    refuses("fit.json naming a path, not a file", lambda: parse_fit(json.dumps(dict(good, obj="../x.obj"))),
            "bare file name")

    # source-map.tsv against the DSR file above (mesh 0: L Hand, L Finger2, L Finger21).
    header = "\t".join(SOURCE_MAP_HEADER)
    rows = ["0\t0\t0\tL Hand\thand", "1\t0\t1\tL Finger2\tfinger", "2\t0\t2\tL Finger21\tfinger_tip",
            "3\t-1\t-1\t-\tcap"]
    smap = parse_source_map("\n".join([header] + rows) + "\n", 4, f)
    check("source map read", smap[2] == (0, 2, "L Finger21", "finger_tip") and smap[3][3] == "cap", str(smap))
    refuses("a source map with the wrong header",
            lambda: parse_source_map("\n".join(["vertex\tmesh"] + rows), 4, f), "header")
    refuses("a source map one row short", lambda: parse_source_map("\n".join([header] + rows[:3]), 4, f), "rows")
    refuses("a role outside the vocabulary",
            lambda: parse_source_map("\n".join([header, rows[0].replace("hand", "palm")] + rows[1:]), 4, f), "role")
    refuses("a row pointing at a vertex dominated by another bone",
            lambda: parse_source_map("\n".join([header, "0\t0\t1\tL Hand\thand"] + rows[1:]), 4, f), "dominated")
    refuses("a cap with a source vertex",
            lambda: parse_source_map("\n".join([header] + rows[:3] + ["3\t0\t-1\t-\tcap"]), 4, f), "cap")
    refuses("a row out of order",
            lambda: parse_source_map("\n".join([header, rows[1], rows[0]] + rows[2:]), 4, f), "names export")
    refuses("a vertex past its mesh",
            lambda: parse_source_map("\n".join([header] + rows[:3] + ["3\t1\t9\tL Calf\tleg"]), 4, f), "no vertex")
    ident = identity_source_map(f, 4)
    check("identity map onto mesh 0, the rest caps",
          [r[:2] for r in ident] == [(0, 0), (0, 1), (0, 2), (-1, -1)] and ident[2][3] == "finger_tip", str(ident))
    refuses("identity map on an export smaller than mesh 0", lambda: identity_source_map(f, 2), "identity")
    raw = source_positions(f, ident, [(0, 1, 3), (1, 2, 3)])
    check("a cap stands at the middle of its source neighbours", raw[3] == (4.0, 5.0, 6.0), str(raw[3]))

    # The measures, on a symmetric rig with an arm that sinks in the idle.
    rig = _synthetic_rig({"idle": [{}], "r1": [{}], "run_r1": [{}]})
    check("parts by ancestry: a toe is leg, a finger and the weapon are arm",
          (rig.part("L_Toe0"), rig.part("R_Finger12"), rig.part("L_Weapon"), rig.part("Spine2"))
          == ("leg", "arm", "arm", "torso"))
    check("leg trunk is the calf subtree", (rig.calf_side("L_Toe0"), rig.calf_side("R_Calf"), rig.calf_side("L_Thigh"))
          == ("L", "R", None))
    tri = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    check("an edge stretched 1.6x is bad, 1.4x is not",
          bad_edge(tri, [(0, 0, 0), (1.6, 0, 0), (0, 1, 0)], 0, 1)
          and not bad_edge(tri, [(0, 0, 0), (1.4, 0, 0), (0, 1, 0)], 0, 1))
    check("an edge squashed to 0.6x is bad, scale applies to the reference",
          bad_edge(tri, [(0, 0, 0), (0.6, 0, 0), (0, 1, 0)], 0, 1) and not bad_edge(tri, [(0, 0, 0), (2, 0, 0)], 0, 1, 2.0))
    flip = _synthetic_rig({"idle": [{"L_Finger1": ((0, -2, 0), (0, 0, 0, 1))}], "r1": [{}], "run_r1": [{}]})
    pose = flip.clips["idle"][0]
    for dominant, want in ((["Spine2", "Spine2", "L_Finger1"], (Counter(), 1)),
                           (["L_Hand", "L_Hand", "L_Finger1"], (Counter({"L": 1}), 0))):
        weights = [[(d, 1.0)] for d in dominant]
        posed = skin(tri, weights, flip, pose)
        creases, outside, _ = idle_inside_out(flip, pose, tri, posed, [(0, 1, 2)], dominant)
        check(f"a corner pulled across its triangle turns it inside out ({dominant[0]})",
              (creases, outside) == want, f"{creases} {outside}")
    body = [(0.3 * math.cos(a), 1.2, 0.3 * math.sin(a)) for a in (0, 1.6, 3.2, 4.8)]
    count, arm = buried(rig, body + [(0.1, 1.2, 0.05), (0.5, 1.2, 0.0)], ["Spine2"] * 4 + ["L_UpperArm"] * 2)
    check("an arm vertex inside the body band is buried, one outside is not", (count, arm) == (1, 2), f"{count}/{arm}")

    # A whole run through the export directory, every file the builder writes.
    src_bones = [("Pelvis", -1), ("L Hand", 0), ("L Finger1", 1), ("L Finger11", 2),
                 ("R Hand", 0), ("R Finger1", 4), ("R Finger11", 5), ("L Calf", 0), ("R Calf", 0)]
    verts = [((0.4, 0.8, 0), 1), ((0.45, 0.78, 0), 2), ((0.5, 0.7, 0), 3), ((0.5, 0.7, 0.05), 3),
             ((-0.4, 0.8, 0), 4), ((-0.45, 0.78, 0), 5), ((-0.5, 0.7, 0), 6), ((-0.5, 0.7, 0.05), 6),
             ((0.08, 0.1, 0), 7), ((0.08, 0.2, 0), 7), ((0.08, 0.1, 0.05), 7),
             ((-0.08, 0.1, 0), 8), ((-0.08, 0.2, 0), 8), ((-0.08, 0.1, 0.05), 8)]
    source = Flver(_flver_bytes(0x2000C, src_bones, [
        {"palette": list(range(len(src_bones))), "face_sets": [],
         "buffers": [[(0x02, 0, [_pos(p) for p, _ in verts]), (0x1A, 1, [_w16((1, 0, 0, 0))] * len(verts)),
                      (0x11, 2, [_ids((b, 0, 0, 0)) for _, b in verts])]]}]))
    k = 1.25
    targets = ["L_Hand", "L_Finger1", "L_Finger12", "L_Finger12", "R_Hand", "R_Finger1", "R_Finger12",
               "R_Finger12", "L_Calf", "L_Calf", "L_Calf", "R_Calf", "R_Calf", "R_Calf"]
    faces = [(0, 1, 2), (1, 3, 2), (4, 6, 5), (5, 6, 7), (8, 9, 10), (11, 13, 12)]
    obj = "".join("v {:.9f} {:.9f} {:.9f}\n".format(*(c * k for c in p)) for p, _ in verts)
    obj += "".join(f"f {a + 1}/{a + 1}/{a + 1} {b + 1}/{b + 1}/{b + 1} {c + 1}/{c + 1}/{c + 1}\n" for a, b, c in faces)
    tsv = "vertex\tslot\tsource_mesh_bone_slot\tsource_global_bone_index\tsource_bone\ter_target_bone\tweight\n"
    tsv += "".join(f"{i}\t-\t-\t-\t-\t{t}\t1.000000000\n" for i, t in enumerate(targets))
    names = [n for n, _ in src_bones]
    smap_text = header + "\n" + "".join(
        f"{i}\t0\t{i}\t{names[b]}\t{fallback_roles(names, [p for _, p in src_bones])[names[b]]}\n"
        for i, (_, b) in enumerate(verts))
    fit = {"format": 1, "character": "synthetic", "scale": k, "params": {"arm_lean": 0},
           "stretch": {}, "grip_anchor": [-0.56, 0.98, 0.0], "obj": "mesh.obj", "weights": "mesh_weights.tsv"}
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "mesh.obj").write_text(obj, encoding="utf-8")
        (d / "mesh_weights.tsv").write_text(tsv, encoding="utf-8")
        (d / SOURCE_MAP).write_text(smap_text, encoding="utf-8")
        (d / FIT_JSON).write_text(json.dumps(fit), encoding="utf-8")
        export = Export(d, source)
        sink = _synthetic_rig({"idle": [{"L_Hand": ((-0.3, 0.0, 0.0), (0, 0, 0, 1))}], "r1": [{}], "run_r1": [{}]})
        m = measure(source, export, sink)
        check("an exact fit has no bad edge and nothing inside out at bind",
              (m["bind_bad_edges_vs_source_x_scale"], m["bind_inside_out"]) == (0, 0),
              f"{m['bind_bad_edges_vs_source_x_scale']} {m['bind_inside_out']}")
        check("tips come from the source map's finger_tip rows, per side", m["tip_vertices"] == {"L": 2, "R": 2},
              str(m["tip_vertices"]))
        check("palms come from the Elden Ring targets", m["palm_vertices"] == {"L": 4, "R": 4}, str(m["palm_vertices"]))
        want_gap = 2 * 0.08 * k
        check("leg gap in the idle and in the source's bind",
              abs(m["idle_leg_gap_m"] - want_gap) < 1e-6 and abs(m["source_bind_leg_gap_m"] - 0.16) < 1e-6,
              f"{m['idle_leg_gap_m']} {m['source_bind_leg_gap_m']}")
        check("the grip anchor is measured against R_Weapon at bind",
              abs(m["grip_anchor_to_weapon_m"] - 0.01) < 1e-6, str(m["grip_anchor_to_weapon_m"]))
        check("the hand pulled in moves only the hand's vertices", m["idle_bad_edges_vs_source_x_scale"] > 0)
        (d / SOURCE_MAP).unlink()
        refuses("an export smaller than mesh 0 with no source map is refused", lambda: Export(
            d, Flver(_flver_bytes(0x2000C, src_bones, [{"palette": [0], "face_sets": [], "buffers": [[
                (0x02, 0, [_pos((0, 0, 0))] * 20), (0x1A, 1, [_w16((1, 0, 0, 0))] * 20),
                (0x11, 2, [_ids((0, 0, 0, 0))] * 20)]]}]))), "identity")
        (d / FIT_JSON).unlink()
        (d / "other.obj").write_text(obj, encoding="utf-8")
        refuses("without fit.json naming it, two OBJs are ambiguous", lambda: Export(d, source), "exactly one")

    # The gates: c2280's own numbers pass, and each gate fails on its own.
    vanilla = {"R": {"idle_inside_out_creases": 48, "idle_max_palm_to_weapon_m": 0.006},
               "L": {"idle_inside_out_creases": 43, "idle_max_palm_to_weapon_m": 0.012}}
    c2280 = {"bind_inside_out": 0, "idle_inside_out_outside_creases": 0, "idle_inside_out_creases": {"L": 0, "R": 0},
             "edges": 5124, "idle_worst_frame_bad_edges": 126, "r1_worst_frame_bad_edges": 560,
             "run_r1_worst_frame_bad_edges": 534, "grip_anchor_to_weapon_m": None,
             "idle_max_palm_to_weapon_m": {"L": 0.012, "R": 0.013},
             "idle_L_tip_to_weapon_m": C2280_IDLE_TIP_TO_WEAPON_M["L"],
             "idle_R_tip_to_weapon_m": C2280_IDLE_TIP_TO_WEAPON_M["R"],
             "idle_arm_buried": 82, "arm_vertices": 595, "idle_leg_gap_m": 0.079, "source_bind_leg_gap_m": 0.02}
    verdicts = gates(c2280, vanilla)
    check("c2280's own numbers pass every gate", all(v[3] is not False for v in verdicts),
          str([v for v in verdicts if v[3] is False]))
    check("no grip anchor skips that gate", [v[3] for v in verdicts if v[0].startswith("grip")] == [None])
    breaks = {
        "bind inside-out": {"bind_inside_out": 1},
        "idle inside-out outside hand and finger creases": {"idle_inside_out_outside_creases": 1},
        "idle R hand creases inside out": {"idle_inside_out_creases": {"L": 0, "R": 49}},
        "idle loop worst-frame bad edges": {"idle_worst_frame_bad_edges": 127},
        "R1 a020_030000 worst-frame bad edges": {"r1_worst_frame_bad_edges": 561},
        "running R1 a020_030200 worst-frame bad edges": {"run_r1_worst_frame_bad_edges": 535},
        "grip anchor to R_Weapon at bind": {"grip_anchor_to_weapon_m": 0.031},
        "idle L palm centroid to weapon": {"idle_max_palm_to_weapon_m": {"L": 0.031, "R": 0.013}},
        "idle R mean tip to weapon": {"idle_R_tip_to_weapon_m": C2280_IDLE_TIP_TO_WEAPON_M["R"] + 1e-9},
        "idle arm vertices buried": {"idle_arm_buried": 84},
        "idle leg-trunk gap": {"source_bind_leg_gap_m": 0.08},
    }
    for name, change in breaks.items():
        failed = [v[0] for v in gates(dict(c2280, **change), vanilla) if v[3] is False]
        check(f"gate fails alone: {name}", failed == [name], str(failed))
    check("the share of edges scales with the mesh: 252 of 10248 is c2280's idle share",
          not [v for v in gates(dict(c2280, edges=10248, idle_worst_frame_bad_edges=252), vanilla) if v[3] is False])

    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?", help="the raw DSR character FLVER")
    ap.add_argument("donor", nargs="?", help="the vanilla FC_M_0000.flver")
    ap.add_argument("export_dir", nargs="?", help="what mushroom-man-builder export-obj wrote")
    ap.add_argument("--json", metavar="OUT", help="write every metric, the vanilla references and the gates")
    ap.add_argument("--gate", action="store_true", help="exit 1 when any gate fails")
    ap.add_argument("--selftest", action="store_true", help="game-free checks of the reader, maps and gates")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not (args.source and args.donor and args.export_dir):
        ap.error("<source.flver> <FC_M_0000.flver> <export-dir> are needed")
    try:
        return run(args.source, args.donor, args.export_dir, args.json, args.gate)
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
