#!/usr/bin/env python3
"""Measure how the Route A export places c2280's arms against Elden Ring's bind skeleton.

Usage: mushroom-arm-fit-probe.py <dir> [<export-dir>]

<dir> holds the raw `c2280.flver` and the vanilla `FC_M_0000.flver` (both written by
`mushroom-man-builder extract-inputs`); <export-dir> (default `<dir>/builder-export`) holds
`c2280_route_a_scaled.obj` + `c2280_route_a_weights.tsv` as `mushroom-man-builder export-obj`
writes them. Game files are read from there and never written into the repo.

It prints the bind-pose joints of both skeletons, where the exported vertices land against the
Elden Ring bones they are weighted to, and which triangle edges the export stretches, squashes or
turns inside out before any animation runs. Then it skins the same mesh into frame 0 of the
standing idle, decoded offline by `er-hkx-pose.py`, and counts the same damage there plus the arm
vertices that end up inside the body: the idle is what a player looks at, not the bind pose.
"""

import importlib.util
import math
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

# The export's global reshape (`RouteAParams::default` in tools/mushroom-man-builder/src/flver/export.rs).
SCALE = 1.225
STRETCH = 1.14
BAD_STRETCH = 1.5
BAD_SQUASH = 0.67


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


def matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


# Row-vector matrices, as System.Numerics builds them; SoulsFormats composes a FLVER bone's local
# transform as scale * rotX * rotZ * rotY * translation, and a bone's world transform is its local
# transform times its parent's world transform.
def rot_x(a):
    c, s = math.cos(a), math.sin(a)
    return [[1, 0, 0, 0], [0, c, s, 0], [0, -s, c, 0], [0, 0, 0, 1]]


def rot_y(a):
    c, s = math.cos(a), math.sin(a)
    return [[c, 0, -s, 0], [0, 1, 0, 0], [s, 0, c, 0], [0, 0, 0, 1]]


def rot_z(a):
    c, s = math.cos(a), math.sin(a)
    return [[c, s, 0, 0], [-s, c, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]


def bones(path):
    b = path.read_bytes()
    dummy, material, count = u32(b, 0x14), u32(b, 0x18), u32(b, 0x1C)
    table = 0x80 + 0x40 * dummy + 0x20 * material
    out = []
    for i in range(count):
        o = table + i * 0x80
        t, r, s = vec3(b, o), vec3(b, o + 0x10), vec3(b, o + 0x20)
        name_off = u32(b, o + 0x0C)
        name = utf16z(b, name_off) if name_off < len(b) else ""
        local = [[s[0], 0, 0, 0], [0, s[1], 0, 0], [0, 0, s[2], 0], [0, 0, 0, 1]]
        for m in (rot_x(r[0]), rot_z(r[2]), rot_y(r[1])):
            local = matmul(local, m)
        local[3][0], local[3][1], local[3][2] = t
        out.append({"name": name, "parent": i16(b, o + 0x1C), "local": local})
    for bone in out:
        world, cur = bone["local"], bone
        while cur["parent"] >= 0:
            cur = out[cur["parent"]]
            world = matmul(world, cur["local"])
        bone["pos"] = tuple(world[3][:3])
    return {bone["name"]: bone for bone in out}


def raw_vertices(path):
    """The first mesh's vertices: position, global bone indices, weights."""
    b = path.read_bytes()
    dummy, material, nbones, nmesh = (u32(b, o) for o in (0x14, 0x18, 0x1C, 0x20))
    nface, nvb = u32(b, 0x50), u32(b, 0x24)
    mesh = 0x80 + 0x40 * dummy + 0x20 * material + 0x80 * nbones
    vb_table = mesh + 0x30 * nmesh + 0x20 * nface
    layout_table = vb_table + 0x20 * nvb
    mesh_bones = [u32(b, u32(b, mesh + 0x1C) + 4 * i) for i in range(u32(b, mesh + 0x14))]
    vb = vb_table + 0x20 * u32(b, u32(b, mesh + 0x2C))
    layout = layout_table + 0x10 * u32(b, vb + 4)
    size, count, start = u32(b, vb + 8), u32(b, vb + 0x0C), u32(b, 0x0C) + u32(b, vb + 0x1C)
    first = u32(b, layout + 0x0C)
    members = [
        (u32(b, first + 0x14 * i + 4), u32(b, first + 0x14 * i + 8), u32(b, first + 0x14 * i + 0x0C))
        for i in range(u32(b, layout))
    ]
    verts = []
    for v in range(count):
        base = start + v * size
        pos, idx, wts = None, [0] * 4, [0.0] * 4
        for off, fmt_id, sem in members:
            o = base + off
            if sem == 0 and fmt_id == 0x02:
                pos = vec3(b, o)
            elif sem == 1 and fmt_id in (0x1A, 0x16):
                wts = [x / 32767 for x in struct.unpack_from("<4h", b, o)]
            elif sem == 1 and fmt_id == 0x13:
                wts = [x / 255 for x in b[o : o + 4]]
            elif sem == 2 and fmt_id in (0x11, 0x24):
                idx = list(b[o : o + 4])
            elif sem == 2 and fmt_id == 0x18:
                idx = list(struct.unpack_from("<4H", b, o))
        verts.append((pos, [mesh_bones[i] if i < len(mesh_bones) else i for i in idx], wts))
    return verts


def obj(path):
    vs, faces = [], []
    for line in path.read_text().splitlines():
        p = line.split()
        if p and p[0] == "v":
            vs.append(tuple(map(float, p[1:4])))
        elif p and p[0] == "f":
            faces.append(tuple(int(t.split("/")[0]) - 1 for t in p[1:4]))
    return vs, faces


def dominant_targets(path, n):
    acc = [defaultdict(float) for _ in range(n)]
    for line in path.read_text().splitlines()[1:]:
        c = line.split("\t")
        acc[int(c[0])][c[5]] += float(c[6])
    return [max(a, key=a.get) if a else "<none>" for a in acc]


def sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def length(a):
    return math.sqrt(sum(x * x for x in a))


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def fmt(p):
    return "({:+.3f}, {:+.3f}, {:+.3f})".format(*p)


def main():
    root = Path(sys.argv[1])
    src = bones(root / "c2280.flver")
    er = bones(root / "FC_M_0000.flver")

    print("== c2280 bind joints: raw, and where the export's global scale puts them")
    for name in ["Pelvis", "Spine3", "Neck", "Head", "LArm1", "LArm2", "LArmPalm", "LArmDigit22", "LLeg1"]:
        p = src[name]["pos"]
        print(f"  {name:12s} raw {fmt(p)}  scaled {fmt((p[0] * SCALE, p[1] * STRETCH * SCALE, p[2] * SCALE))}")

    print("== Elden Ring FC_M_0000 bind joints")
    for name in ["Pelvis", "Spine2", "Neck", "Head", "L_Clavicle", "L_Shoulder", "L_UpperArm",
                 "L_Forearm", "L_Hand", "L_Thigh", "L_Calf", "L_Foot"]:
        print(f"  {name:12s} {fmt(er[name]['pos']) if name in er else '(absent)'}")
    print("  arm-ish ER bones:", [n for n in er if any(k in n for k in ("Arm", "Shoulder", "Clav", "Hand", "Elbow"))])

    raw = raw_vertices(root / "c2280.flver")
    export = Path(sys.argv[2]) if len(sys.argv) > 2 else root / "builder-export"
    final, faces = obj(export / "c2280_route_a_scaled.obj")
    assert len(raw) == len(final), (len(raw), len(final))
    weights_path = export / "c2280_route_a_weights.tsv"
    dom = dominant_targets(weights_path, len(final))

    print("== exported left-side vertices, grouped by the ER bone that carries most of their weight")
    for bone in ["Spine2", "L_Shoulder", "L_UpperArm", "L_Forearm", "L_Hand"]:
        sel = [i for i in range(len(final)) if dom[i] == bone and final[i][0] > 0]
        if not sel:
            continue
        xs = [final[i][0] for i in sel]
        ys = [final[i][1] for i in sel]
        rx = [raw[i][0][0] for i in sel]
        joint = er.get(bone, {}).get("pos")
        print(f"  {bone:11s} n={len(sel):4d} final x {min(xs):+.3f}..{max(xs):+.3f} y {min(ys):+.3f}..{max(ys):+.3f}"
              f"  raw x {min(rx):+.3f}..{max(rx):+.3f}  ER joint {fmt(joint) if joint else '-'}")

    print("== static distortion: each triangle edge's final length against raw length x global scale")
    bad, flipped, total, worst = Counter(), Counter(), Counter(), []
    for f in faces:
        group = tuple(sorted({dom[i] for i in f}))
        total[group] += 1
        for a, b in ((f[0], f[1]), (f[1], f[2]), (f[2], f[0])):
            r = length(sub(raw[a][0], raw[b][0])) * SCALE
            if r < 1e-6:
                continue
            ratio = length(sub(final[a], final[b])) / r
            if ratio > BAD_STRETCH or ratio < BAD_SQUASH:
                bad[group] += 1
                worst.append((ratio, dom[a], dom[b], raw[a][0], raw[b][0]))
        nr = cross(sub(raw[f[1]][0], raw[f[0]][0]), sub(raw[f[2]][0], raw[f[0]][0]))
        nf = cross(sub(final[f[1]], final[f[0]]), sub(final[f[2]], final[f[0]]))
        if sum(x * y for x, y in zip(nr, nf)) < 0:
            flipped[group] += 1
    print(f"  triangles {len(faces)}; edges stretched >{BAD_STRETCH}x or squashed <{BAD_SQUASH}x: "
          f"{sum(bad.values())}; triangles turned inside out: {sum(flipped.values())}")
    for group in sorted(set(bad) | set(flipped), key=lambda g: -(bad[g] + flipped[g]))[:14]:
        print(f"    {bad[group]:4d} bad edges, {flipped[group]:3d} inside out, of {total[group]:4d} triangles on {group}")
    worst.sort(key=lambda w: -abs(math.log(w[0])))
    print("  worst edges:")
    for ratio, a, b, pa, pb in worst[:8]:
        print(f"    x{ratio:5.2f}  {a} -> {b}  raw {fmt(pa)} -> {fmt(pb)}")

    idle_report(raw, final, faces, skin_weights(weights_path, len(final)))


# A candidate rebind, so a fit can be scored here before it is written into the builder.
GLOBAL = (SCALE, SCALE * STRETCH, SCALE)
TORSO_MAP = {"Pelvis": "Pelvis", "Spine1": "Spine", "Spine2": "Spine1", "Spine3": "Spine2",
             "Neck": "Neck", "Head": "Head"}


def scaled(v):
    return tuple(a * g for a, g in zip(v, GLOBAL))


def normalise(v):
    n = length(v)
    return tuple(x / n for x in v)


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def arc_rotation(a, b):
    """The shortest rotation (a quaternion, xyzw) taking unit vector `a` onto unit vector `b`."""
    c = cross(a, b)
    return normalise((c[0], c[1], c[2], 1.0 + dot(a, b)))


def smoothstep(e0, e1, x):
    t = min(1.0, max(0.0, (x - e0) / (e1 - e0)))
    return t * t * (3 - 2 * t)


def candidate(root, out, rise, offset, blend, rigid):
    """Rebind c2280 with its arms laid along Elden Ring's arms at their own (stubby) length.

    rise: 0 keeps each arm's root at the mushroom's shoulder, 1 moves it onto the ER shoulder
    joint. offset: metres the arm is set out from the ER arm axis, so a fat body does not swallow
    it. blend: half-width of the elbow and wrist weight blends. rigid: whole arm on the upper arm.
    """
    hkx = pose_tool()
    src = bones(root / "c2280.flver")
    er = bones(root / "FC_M_0000.flver")
    names = list(src)
    raw = raw_vertices(root / "c2280.flver")
    _, faces = obj(root / "builder-export" / "c2280_route_a_scaled.obj")
    ys = [p for p, _, _ in raw]
    lo, hi = min(p[1] for p in ys), max(p[1] for p in ys)

    arms = {}
    for side, er_side in (("L", "L_"), ("R", "R_")):
        joint = src[f"{side}Arm1"]["pos"]
        tip = src[f"{side}ArmPalm"]["pos"]
        shoulder = er[f"{er_side}UpperArm"]["pos"]
        elbow = er[f"{er_side}Forearm"]["pos"]
        hand = er[f"{er_side}Hand"]["pos"]
        axis = normalise(sub(hand, shoulder))
        up = (0.0, 1.0, 0.0)
        out_dir = normalise(sub(up, tuple(dot(up, axis) * a for a in axis)))
        rotation = arc_rotation(normalise(scaled(sub(tip, joint))), axis)
        start = tuple(s + (t - s) * rise for s, t in zip(scaled(joint), shoulder))
        start = tuple(s + offset * o for s, o in zip(start, out_dir))
        arms[side] = {"joint": joint, "rotation": rotation, "start": start, "shoulder": shoulder,
                      "axis": axis, "elbow_u": length(sub(elbow, shoulder)),
                      "wrist_u": length(sub(elbow, shoulder)) + length(sub(hand, elbow)), "er": er_side}

    def arm_place(side, v):
        a = arms[side]
        moved = hkx._qrot(a["rotation"], scaled(sub(v, a["joint"])))
        return tuple(s + m for s, m in zip(a["start"], moved))

    def arm_bones(side, p):
        a = arms[side]
        if rigid:
            return [(a["er"] + "UpperArm", 1.0)]
        u = dot(sub(p, a["shoulder"]), a["axis"])
        fore = smoothstep(a["elbow_u"] - blend, a["elbow_u"] + blend, u)
        hand = smoothstep(a["wrist_u"] - blend, a["wrist_u"] + blend, u)
        return [(a["er"] + "UpperArm", 1 - fore), (a["er"] + "Forearm", fore - hand), (a["er"] + "Hand", hand)]

    positions, rows = [], []
    for index, (p, bone_ids, ws) in enumerate(raw):
        place = [0.0, 0.0, 0.0]
        total = sum(w for w in ws if w > 1e-4) or 1.0
        for bone_id, w in zip(bone_ids, ws):
            if w <= 1e-4:
                continue
            w /= total
            name = names[bone_id] if bone_id < len(names) else ""
            side = name[0] if name[1:4] == "Arm" else None
            if side:
                at = arm_place(side, p)
                for er_bone, share in arm_bones(side, at):
                    if share > 1e-4:
                        rows.append((index, er_bone, w * share))
            else:
                at = scaled(p)
                if name in ("LLeg1", "RLeg1"):
                    ny = (p[1] - lo) / (hi - lo)
                    part = "Foot" if ny < 0.10 else "Calf" if ny < 0.24 else "Thigh"
                    rows.append((index, f"{name[0]}_{part}", w))
                else:
                    rows.append((index, TORSO_MAP.get(name, "Spine2"), w))
            for k in range(3):
                place[k] += w * at[k]
        positions.append(tuple(place))

    out.mkdir(parents=True, exist_ok=True)
    with open(out / "c2280_route_a_scaled.obj", "w") as f:
        for p in positions:
            f.write("v {:.9f} {:.9f} {:.9f}\n".format(*p))
        for t in faces:
            f.write("f {} {} {}\n".format(t[0] + 1, t[1] + 1, t[2] + 1))
    with open(out / "c2280_route_a_weights.tsv", "w") as f:
        f.write("vertex\tslot\tsource_mesh_bone_slot\tsource_global_bone_index\tsource_bone\ter_target_bone\tweight\n")
        for index, bone, w in rows:
            f.write(f"{index}\t-\t-\t-\t-\t{bone}\t{w:.9f}\n")


def skin_weights(path, n):
    """Each vertex's Elden Ring weights as `patch.rs` writes them: summed per bone, the heaviest
    four kept, normalised to one."""
    acc = [defaultdict(float) for _ in range(n)]
    for line in path.read_text().splitlines()[1:]:
        c = line.split("\t")
        if not c[5].startswith("<"):
            acc[int(c[0])][c[5]] += float(c[6])
    out = []
    for a in acc:
        top = sorted(a.items(), key=lambda kv: -kv[1])[:4] or [("Spine2", 1.0)]
        total = sum(w for _, w in top)
        out.append([(bone, w / total) for bone, w in top])
    return out


def pose_tool():
    spec = importlib.util.spec_from_file_location("er_hkx_pose", Path(__file__).with_name("er-hkx-pose.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def qconj(q):
    return (-q[0], -q[1], -q[2], q[3])


def skin(positions, weights, bind, pose, index, qrot):
    """Linear blend skinning from the bind pose into `pose`, as the game does it."""
    out = []
    for v, ws in zip(positions, weights):
        acc = [0.0, 0.0, 0.0]
        for bone, w in ws:
            bt, bq, _ = bind[index[bone]]
            pt, pq, _ = pose[index[bone]]
            moved = qrot(pq, qrot(qconj(bq), sub(v, bt)))
            for k in range(3):
                acc[k] += w * (pt[k] + moved[k])
        out.append(tuple(acc))
    return out


def idle_report(raw, final, faces, weights):
    """The same mesh posed into frame 0 of the standing idle a000_000000."""
    hkx = pose_tool()
    skeleton = hkx.load_skeleton()
    bind, idle = hkx.pose_model(), hkx.pose_model(*hkx.IDLE)
    posed = skin(final, weights, bind, idle, skeleton.index, hkx._qrot)
    dominant = [ws[0][0] for ws in weights]

    def turn(bone):
        i = skeleton.index[bone]
        return hkx._qmul(idle[i][1], qconj(bind[i][1]))

    bad = flipped = 0
    by_part = Counter()

    def part(bone):
        for key in ("arm", "hand", "shoulder", "clavicle"):
            if key in bone.lower():
                return "arm"
        return "leg" if any(k in bone for k in ("Thigh", "Calf", "Foot")) else "torso"

    for f in faces:
        where = "+".join(sorted({part(dominant[i]) for i in f}))
        for a, b in ((f[0], f[1]), (f[1], f[2]), (f[2], f[0])):
            r = length(sub(raw[a][0], raw[b][0])) * SCALE
            if r > 1e-6 and not BAD_SQUASH <= length(sub(posed[a], posed[b])) / r <= BAD_STRETCH:
                bad += 1
                by_part[where] += 1
        expected = hkx._qrot(turn(dominant[f[0]]), cross(sub(final[f[1]], final[f[0]]), sub(final[f[2]], final[f[0]])))
        got = cross(sub(posed[f[1]], posed[f[0]]), sub(posed[f[2]], posed[f[0]]))
        if sum(x * y for x, y in zip(expected, got)) < 0:
            flipped += 1

    arm = [i for i, d in enumerate(dominant) if d.startswith(("L_", "R_")) and "Thigh" not in d
           and "Calf" not in d and "Foot" not in d]
    body = [i for i in range(len(posed)) if i not in set(arm)]
    buried = 0
    for i in arm:
        x, y, z = posed[i]
        band = [posed[j] for j in body if abs(posed[j][1] - y) < 0.05]
        if band and abs(x) < max(abs(p[0]) for p in band) * 0.9 and abs(z) < max(abs(p[2]) for p in band) * 0.9:
            buried += 1
    left = [posed[i] for i in arm if posed[i][0] > 0]
    print("== the same mesh in the standing idle (a000_000000 frame 0), skinned as the game does")
    print(f"  edges stretched >{BAD_STRETCH}x or squashed <{BAD_SQUASH}x against the mushroom's own shape: {bad}"
          f"; triangles inside out: {flipped}")
    print("  bad edges by where the triangle sits: " + ", ".join(f"{k} {v}" for k, v in by_part.most_common()))
    print(f"  arm vertices buried inside the body: {buried} of {len(arm)}")
    if left:
        print("  left arm in idle: x {:+.3f}..{:+.3f}  y {:+.3f}..{:+.3f}".format(
            min(p[0] for p in left), max(p[0] for p in left), min(p[1] for p in left), max(p[1] for p in left)))


if __name__ == "__main__":
    if len(sys.argv) > 3 and sys.argv[2] == "--candidate":
        # <dir> --candidate <out-dir> <rise> <offset> <blend> <rigid 0|1>
        candidate(Path(sys.argv[1]), Path(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5]),
                  float(sys.argv[6]), sys.argv[7] == "1")
        sys.argv[2:] = [sys.argv[3]]
    main()
