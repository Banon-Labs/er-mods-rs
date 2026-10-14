#!/usr/bin/env python3
"""Measure how the Mushroom Man export fits c2280 to Elden Ring's skeleton, and how it moves.

Usage: mushroom-arm-fit-probe.py <dir> [<export-dir>]
       mushroom-arm-fit-probe.py <dir> --human <out-dir> <offset> <cut 0|1> \\
           [<refit-body 0|1> <fingers 0|1> [<socket 0|1> <spread m> [<blend m>]]]

The second form writes a candidate fit (a Python prototype of the builder's, kept so a change can
be scored here before it is written into `tools/mushroom-man-builder/src/flver/export.rs`) and
scores it; `--human <out> 0.08 0 1 1 1 0.08 0.2` is what the builder does today, to within
0.0000003 m and the same triangles.

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

# The mushroom's girth (`FitParams::scale` in tools/mushroom-man-builder/src/flver/export.rs); raw
# edge lengths times this are the shape the distortion counts compare against. `STRETCH` is the
# fixed height stretch of the first one-scale fit, which `--candidate` and the non-refit `--human`
# candidates still use.
SCALE = 1.225
STRETCH = 1.14
BAD_STRETCH = 1.5
BAD_SQUASH = 0.67
# Metres over which a digit's weight passes from the human finger's first bone to its second.
FINGER_BLEND = 0.02
# Which digits (1 thumb, 2, 3) pass to the human finger's second bone; the rest bend at the base.
# None do in the builder: splitting closed the tips 0.005 m more and added 124 stretched edges to
# the idle loop, most of them in the thumb.
SPLIT_DIGITS = ""


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
    # Vertices past c2280's own are caps the fit added over cut openings: give each the middle of
    # its rim's own positions, so its edges are measured against what they cover.
    for extra in range(len(raw), len(final)):
        rim = sorted({i for f in faces if extra in f for i in f if i < len(raw)})
        middle = tuple(sum(raw[i][0][k] for i in rim) / len(rim) for k in range(3))
        raw.append((middle, raw[rim[0]][1], raw[rim[0]][2]))
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

    names = list(src)
    palms, tips = {}, {}
    for i, (_, ids, ws) in enumerate(raw):
        name = names[max(zip(ws, ids))[1]]
        if name[1:].startswith(("ArmPalm", "ArmDigit")):
            palms.setdefault(name[0], []).append(i)
        if name[1:].startswith("ArmDigit") and name.endswith("2"):
            tips.setdefault(name[0], []).append(i)
    idle_report(raw, final, faces, skin_weights(weights_path, len(final)), palms, tips)


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

    write_export(out, positions, faces, rows)


def human_candidate(root, out, offset, cut, refit_body=False, fingers=False, socket=False, spread=0.0,
                    blend=0.0):
    """Rebind c2280 with human-length arms: its shoulder, elbow and wrist land on Elden Ring's
    `UpperArm`, `Forearm` and `Hand` joints, each segment stretched along its own axis to the human
    length (its girth keeps the global scale), and the hand moved whole onto the human hand, so a
    weapon the game holds at `R_Weapon` sits in the mushroom's palm. offset: metres the whole arm
    is set up and out across the human arm axis, which moves the hand off the weapon by as much.

    refit_body: instead of one scale and a fixed height stretch, the body is stretched upright in
    three bands so c2280's pelvis lands on the human pelvis and its shoulders on the human
    shoulders, with the neck and cap above keeping their own proportions. fingers: the palm spans
    the wrist to the human knuckles, and each of c2280's three digits is weighted to a human
    finger (thumb, middle, ring) split at that finger's middle joint, so the game's grip curls it.
    """
    hkx = pose_tool()
    src = bones(root / "c2280.flver")
    er = bones(root / "FC_M_0000.flver")
    names = list(src)
    raw = raw_vertices(root / "c2280.flver")
    _, faces = obj(root / "builder-export" / "c2280_route_a_scaled.obj")
    lo, hi = min(p[1] for p, _, _ in raw), max(p[1] for p, _, _ in raw)
    up = (0.0, 1.0, 0.0)

    if refit_body:
        def rel(v):
            return tuple(x * SCALE for x in v)
        hip, shoulder_y = src["Pelvis"]["pos"][1], src["LArm1"]["pos"][1]
        er_hip, er_shoulder_y = er["Pelvis"]["pos"][1], er["L_UpperArm"]["pos"][1]
        # spread: metres each leg trunk moves out at the ground, fading to nothing at the height
        # c2280's leg bone starts from, so the two trunks stand apart at the feet.
        leg_top = src["LLeg1"]["pos"][1] * er_hip / hip

        def body(v):
            y = v[1]
            if y <= hip:
                y = y * er_hip / hip
            elif y <= shoulder_y:
                y = er_hip + (y - hip) * (er_shoulder_y - er_hip) / (shoulder_y - hip)
            else:
                y = er_shoulder_y + (y - shoulder_y) * SCALE
            x = v[0] * SCALE
            if spread and y < leg_top:
                x += max(-1.0, min(1.0, x / 0.03)) * spread * smoothstep(0.0, 1.0, (leg_top - y) / leg_top)
            return (x, y, v[2] * SCALE)
        knee, ankle = er["L_Calf"]["pos"][1], er["L_Foot"]["pos"][1]
    else:
        rel = body = scaled

    def segment(a, b, joint_a, joint_b, shift):
        u_src = normalise(rel(sub(b, a)))
        stretch = length(sub(joint_b, joint_a)) / length(rel(sub(b, a)))
        q = arc_rotation(u_src, normalise(sub(joint_b, joint_a)))

        def place(v):
            w = rel(sub(v, a))
            w = tuple(x + (stretch - 1) * dot(w, u_src) * u for x, u in zip(w, u_src))
            return tuple(j + s + m for j, s, m in zip(joint_a, shift, hkx._qrot(q, w)))
        return place, q

    def mid(a, b):
        return tuple((x + y) / 2 for x, y in zip(a, b))

    parts = {}
    digit_split = {}
    for side in "LR":
        e = f"{side}_"
        shoulder, elbow, wrist = (src[f"{side}Arm{k}"]["pos"] for k in ("1", "2", "Palm"))
        er_shoulder, er_elbow, er_hand = (er[e + k]["pos"] for k in ("UpperArm", "Forearm", "Hand"))
        axis = normalise(sub(er_hand, er_shoulder))
        out_dir = normalise(sub(up, tuple(dot(up, axis) * a for a in axis)))
        none = (0.0, 0.0, 0.0)
        # socket: the arm roots where the body put c2280's own shoulder, not on the human joint.
        root = body(shoulder) if socket else er_shoulder
        upper, _ = segment(shoulder, elbow, root, er_elbow, none)
        fore, q_fore = segment(elbow, wrist, er_elbow, er_hand, none)

        if fingers:
            knuckles = mid(src[f"{side}ArmDigit22"]["pos"], src[f"{side}ArmDigit32"]["pos"])
            er_knuckles = mid(er[e + "Finger2"]["pos"], er[e + "Finger3"]["pos"])
            hand, q_hand = segment(wrist, knuckles, er_hand, er_knuckles, none)
            # Each digit hangs off the hand where the hand put its knuckle, turned with the hand,
            # and splits between the human finger's first and second bones at that finger's own
            # first-bone length.
            for digit, base, prev, first, second in (("1", "Digit12", "Digit11", "Finger01", "Finger02"),
                                                     ("2", "Digit22", "Digit21", "Finger2", "Finger21"),
                                                     ("3", "Digit32", "Digit31", "Finger3", "Finger31")):
                joint = src[f"{side}Arm{base}"]["pos"]
                anchor = hand(joint)
                along = normalise(rel(sub(joint, src[f"{side}Arm{prev}"]["pos"])))
                split = length(sub(er[e + second]["pos"], er[e + first]["pos"]))
                if digit not in SPLIT_DIGITS:
                    split = 1.0

                def finger(v, joint=joint, anchor=anchor, q=q_hand):
                    return tuple(a + m for a, m in zip(anchor, hkx._qrot(q, rel(sub(v, joint)))))
                parts[f"{side}Digit{digit}"] = (finger, e + first)
                digit_split[f"{side}Digit{digit}"] = (joint, along, split, e + first, e + second)
        else:
            def hand(v, wrist=wrist, er_hand=er_hand, q_fore=q_fore):
                return tuple(h + m for h, m in zip(er_hand, hkx._qrot(q_fore, rel(sub(v, wrist)))))

        # The arm leans `offset` out across the human arm axis from the shoulder to the elbow and
        # comes back in along the forearm, so the hand, and the weapon in it, stays put.
        u_elbow = dot(sub(er_elbow, er_shoulder), axis)
        u_hand = dot(sub(er_hand, er_shoulder), axis)
        u_root = dot(sub(root, er_shoulder), axis)

        def lean(fn, er_shoulder=er_shoulder, axis=axis, out_dir=out_dir, u_elbow=u_elbow, u_hand=u_hand,
                 u_root=u_root):
            def place(v):
                at = fn(v)
                u = dot(sub(at, er_shoulder), axis)
                k = offset * smoothstep(0.0, 1.0, (u_hand - u) / (u_hand - u_elbow))
                if socket:
                    # Rooted in the socket, the arm starts flush with the body and leans out by
                    # the elbow.
                    k *= smoothstep(0.0, 1.0, (u - u_root) / (u_elbow - u_root))
                return tuple(a + k * o for a, o in zip(at, out_dir))
            return place
        parts[f"{side}Arm1"] = (lean(upper), e + "UpperArm")
        parts[f"{side}Arm2"] = (lean(fore), e + "Forearm")
        parts[f"{side}Hand"] = (lean(hand), e + "Hand")

    def arm_key(name):
        if name in parts:
            return name
        if fingers and name[1:].startswith("ArmDigit") and name.endswith("2"):
            return f"{name[0]}Digit{name[-2]}"
        return f"{name[0]}Hand" if name[1:].startswith(("ArmPalm", "ArmDigit")) else None

    def arm_share(ws, bone_ids):
        total = sum(w for w in ws if w > 1e-4) or 1.0
        return sum(w for b, w in zip(bone_ids, ws) if w > 1e-4 and arm_key(names[b])) / total

    on_arm = [arm_share(ws, ids) >= 0.5 for _, ids, ws in raw]
    if cut:
        # The arm now roots at the human shoulder, inside the cap, so the triangles that joined it
        # to the mushroom's own shoulder would span 0.37 m. Drop them, give each side of the cut
        # only its own bones, and close both openings with a fan.
        spots = {}
        spot = [spots.setdefault(p, len(spots)) for p, _, _ in raw]
        kept = [f for f in faces if len({on_arm[i] for i in f}) == 1]
        cut_spots = {spot[i] for f in faces if len({on_arm[i] for i in f}) == 2 for i in f}
        holes = openings(kept, cut_spots, spot)
        faces = kept

    positions, rows = [], []
    for index, (p, bone_ids, ws) in enumerate(raw):
        place = [0.0, 0.0, 0.0]
        if cut:
            ws = [w if bool(arm_key(names[b])) == on_arm[index] else 0.0 for b, w in zip(bone_ids, ws)]
        total = sum(w for w in ws if w > 1e-4) or 1.0
        for bone_id, w in zip(bone_ids, ws):
            if w <= 1e-4:
                continue
            w /= total
            name = names[bone_id] if bone_id < len(names) else ""
            key = arm_key(name)
            if key:
                fn, er_bone = parts[key]
                at = fn(p)
                if key in digit_split:
                    joint, along, split, first, second = digit_split[key]
                    t = smoothstep(0.0, 1.0, (dot(rel(sub(p, joint)), along) - split) / FINGER_BLEND + 0.5)
                    rows.append((index, first, w * (1 - t)))
                    rows.append((index, second, w * t))
                else:
                    rows.append((index, er_bone, w))
            else:
                at = body(p)
                if name in ("LLeg1", "RLeg1"):
                    if refit_body:
                        part = "Thigh" if at[1] >= knee else "Calf" if at[1] >= ankle else "Foot"
                    else:
                        ny = (p[1] - lo) / (hi - lo)
                        part = "Foot" if ny < 0.10 else "Calf" if ny < 0.24 else "Thigh"
                    rows.append((index, f"{name[0]}_{part}", w))
                else:
                    rows.append((index, TORSO_MAP.get(name, "Spine2"), w))
            for k in range(3):
                place[k] += w * at[k]
        positions.append(tuple(place))
    if blend and not cut:
        # Around each socket the body takes a share of the upper arm's turn that fades to nothing
        # `blend` metres out, half at the socket itself, so the stretch spreads over the shoulder.
        sockets = {s: body(src[f"{s}Arm1"]["pos"]) for s in "LR"}
        by_vertex = defaultdict(list)
        for index, bone, w in rows:
            by_vertex[index].append((bone, w))
        rows = []
        for index, entries in sorted(by_vertex.items()):
            if not on_arm[index]:
                side = "L" if positions[index][0] > 0 else "R"
                d = length(sub(positions[index], sockets[side]))
                share = 0.5 * smoothstep(0.0, 1.0, 1.0 - d / blend)
                total = sum(w for _, w in entries) or 1.0
                if share > 1e-4:
                    entries = [(b, w * (1 - share) / total) for b, w in entries] + [(f"{side}_UpperArm", share)]
            rows += [(index, b, w) for b, w in entries]
    if cut:
        # Cap each opening from a new vertex at the middle of its rim, as the builder does.
        for rim in holes:
            middle, corners, seen = len(positions), [], set()
            for a, _ in rim:
                if spot[a] not in seen:
                    seen.add(spot[a])
                    corners.append(a)
            positions.append(tuple(sum(positions[c][k] for c in corners) / len(corners) for k in range(3)))
            bones_at = defaultdict(float)
            for index, bone, w in rows:
                if index in corners:
                    bones_at[bone] += w / len(corners)
            rows += [(middle, bone, w) for bone, w in bones_at.items()]
            faces = faces + [(b, a, middle) for a, b in rim]
    write_export(out, positions, faces, rows)


def openings(faces, cut_spots, spot):
    """The rim edges of every hole whose edges lie wholly on `cut_spots`, one list per hole, traced
    over positions (`spot[i]` is shared by the copies a texture seam makes of one vertex). A rim
    edge a->b of an existing triangle is crossed b->a by the cap, so the winding matches."""
    edges = {(spot[f[k]], spot[f[(k + 1) % 3]]) for f in faces for k in range(3)}
    rim = [(a, b) for f in faces for a, b in ((f[0], f[1]), (f[1], f[2]), (f[2], f[0]))
           if (spot[b], spot[a]) not in edges and spot[a] in cut_spots and spot[b] in cut_spots]
    parent = {s: s for a, b in rim for s in (spot[a], spot[b])}

    def root(s):
        while parent[s] != s:
            s = parent[s]
        return s
    for a, b in rim:
        ra, rb = root(spot[a]), root(spot[b])
        parent[max(ra, rb)] = min(ra, rb)
    holes = defaultdict(list)
    for a, b in rim:
        holes[root(spot[a])].append((a, b))
    return [holes[k] for k in sorted(holes)]


def write_export(out, positions, faces, rows):
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


def idle_report(raw, final, faces, weights, palms=None, tips=None):
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
    if palms:
        weapon_report(final, faces, weights, palms, tips or {}, raw, hkx, skeleton, bind, idle)


def weapon_report(final, faces, weights, palms, tips, raw, hkx, skeleton, bind, idle):
    """How far the game's weapon bone (`R_Weapon`, `L_Weapon`) sits from the mushroom's palm, and
    how many edges a clip stretches: the idle loop, a020_030000 (R1), and a020_030200 (running R1,
    where the legs move) frame by frame. Then how close the digit tips close on the weapon bone in
    the idle, where the game curls its own fingers around the grip."""
    posed = skin(final, weights, bind, idle, skeleton.index, hkx._qrot)
    for side, sel in sorted(tips.items()):
        grip = idle[skeleton.index[f"{side}_Weapon"]][0]
        mean = sum(length(sub(posed[i], grip)) for i in sel) / len(sel)
        print(f"  idle: {side} digit tips sit {mean:.3f} m from the weapon bone on average")
    dominant = [ws[0][0] for ws in weights]
    legs = {s: [i for i, d in enumerate(dominant) if d in (f"{s}_Foot", f"{s}_Calf") and posed[i][1] < 0.3]
            for s in "LR"}
    if legs["L"] and legs["R"]:
        gap = min(length(sub(posed[a], posed[b])) for a in legs["L"] for b in legs["R"])
        print(f"  idle: the two leg trunks come within {gap:.3f} m of each other below 0.3 m")

    def is_arm(bone):
        return bone[:2] in ("L_", "R_") and bone[2:].startswith(("UpperArm", "Forearm", "Hand", "Finger"))
    seam = [f for f in faces if len({is_arm(dominant[i]) for i in f}) == 2]
    for clip, label in ((hkx.IDLE, "idle loop"), (hkx.R1, "R1 a020_030000"), (hkx.RUN_R1, "running R1 a020_030200")):
        frames = len(hkx._frames(hkx.load_animation(*clip)))
        gaps, worst_bad, worst_seam = Counter(), 0, 0
        for frame in range(0, frames, 3):
            pose = hkx.pose_model(*clip, t=frame / 30.0)
            posed = skin(final, weights, bind, pose, skeleton.index, hkx._qrot)
            worst_seam = max(worst_seam, sum(
                1 for f in seam for a, b in ((f[0], f[1]), (f[1], f[2]), (f[2], f[0]))
                if (r := length(sub(final[a], final[b]))) > 1e-6
                and not BAD_SQUASH <= length(sub(posed[a], posed[b])) / r <= BAD_STRETCH))
            for side, sel in palms.items():
                palm = tuple(sum(posed[i][k] for i in sel) / len(sel) for k in range(3))
                gap = length(sub(pose[skeleton.index[f"{side}_Weapon"]][0], palm))
                gaps[side] = max(gaps[side], gap)
            # Against the fitted bind shape, not the raw one: this is what the pose itself does.
            bad = sum(1 for f in faces for a, b in ((f[0], f[1]), (f[1], f[2]), (f[2], f[0]))
                      if (r := length(sub(final[a], final[b]))) > 1e-6
                      and not BAD_SQUASH <= length(sub(posed[a], posed[b])) / r <= BAD_STRETCH)
            worst_bad = max(worst_bad, bad)
        print(f"  {label}: weapon bone to mushroom palm at most "
              + ", ".join(f"{s} {g:.2f} m" for s, g in sorted(gaps.items()))
              + f"; worst frame has {worst_bad} edges the pose stretches or squashes past"
              f" {BAD_STRETCH}x against the fitted bind shape, {worst_seam} of {3 * len(seam)}"
              " across the arm-to-body seam")


if __name__ == "__main__":
    if len(sys.argv) > 5 and sys.argv[2] == "--human":
        # <dir> --human <out-dir> <offset> <cut 0|1> [<refit-body 0|1> <fingers 0|1>]
        # ... [<refit-body 0|1> <fingers 0|1> [<socket 0|1> <spread m>]]
        flags = [a == "1" for a in sys.argv[5:9]] + [False, False, False]
        spread = float(sys.argv[9]) if len(sys.argv) > 9 else 0.0
        blend = float(sys.argv[10]) if len(sys.argv) > 10 else 0.0
        human_candidate(Path(sys.argv[1]), Path(sys.argv[3]), float(sys.argv[4]), *flags[:4], spread, blend)
        sys.argv[2:] = [sys.argv[3]]
    elif len(sys.argv) > 3 and sys.argv[2] == "--candidate":
        # <dir> --candidate <out-dir> <rise> <offset> <blend> <rigid 0|1>
        candidate(Path(sys.argv[1]), Path(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5]),
                  float(sys.argv[6]), sys.argv[7] == "1")
        sys.argv[2:] = [sys.argv[3]]
    main()
