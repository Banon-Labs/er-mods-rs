#!/usr/bin/env python3
"""Player bone positions from Elden Ring animation clips, decoded offline.

Answers: for player clip `a<cat>_<anim>.hkx` at clip-local time `t` seconds, where is a bone
in character model space, with or without root motion. Stdlib only.

Inputs (both from the unpacked `c0000*.anibnd.dcx` shards, env-overridable):

    ER_PLAYER_SKELETON_HKX  c0000-anibnd-dcx/.../hkx/Skeleton.hkx (150 bones)
    ER_PLAYER_HKX_ROOT      the clips, located by `er-mechanics-attacks.py hkx_path()`

Coordinate convention (model space, metres):

    +Y up. The character faces -Z. Its right hand is on -X (the space is left-handed, as the
    FromSoft/Havok data is stored; flip X for a right-handed view).

    Measured, not assumed: in the bind pose the toes sit at z = -0.072 in front of the ankles
    at z = +0.063; on frame 0 of the idle clip a000_000000 the eye midpoint looks along -Z
    within 0.1 degrees; the forward attacks' reference-frame samples move along -Z
    (a020_030000 R1: -1.83 m). The clip's own `extractedMotion.forward` field says +Z; the data
    contradicts it, so it is not used.

Root motion: `hkaDefaultAnimatedReferenceFrame.referenceFrameSamples` are (x, y, z, yaw)
float4s spread evenly over the clip; they are linearly interpolated. With root motion, a model
point is `RootMotion(t) * FK(t)`: rotate about +Y by yaw, then translate. The `Master` bone's
own track (a small yaw and sway every clip carries) is applied as an ordinary track: with it the
idle gaze is along -Z, without it the gaze is 18.6 degrees off, so it belongs to the pose.

Format, as implemented and how it was checked:

- Tagfile: `TAG0` -> `SDKV`/`DATA`/`TYPE`(`TPTR TSTR TNA1 FSTR TBDY THSH TPAD`)/`INDX`
  (`ITEM PTCH`). Clips carry `TCRF`, an 8-byte id into the `*.compendium` (`TCM0`: `TCID`,
  `TYPE`) beside them, instead of their own `TYPE`. Objects are decoded through the type table
  (member name, offset, type; parents' members first). Checked: `TNA1` and `TBDY` are consumed
  to the exact byte in the skeleton and in every compendium; a pointer or array slot in `DATA`
  holds an `ITEM` index, and the `ITEM` entry gives type, offset and count.
- `hkaSplineCompressedAnimation` block: one 4-byte mask per track (byte 0: position quant bits
  0-1, rotation quant bits 2-5, scale quant bits 6-7; bytes 1-3: position/rotation/scale flags,
  low nibble static x/y/z/w, high nibble spline x/y/z/w), `maskAndQuantizationSize` bytes in
  all; then per track position, rotation, scale, each padded to 4. A spline channel is
  `u16 n, u8 degree, n+degree+2 u8 knots`, pad to 4 (vectors) or to the quantization's
  alignment (rotations), per-axis `(min, max)` for spline axes or one float for static axes,
  then n+1 control points. Checked on all 9340 player clips on disk: every block's walk ends
  exactly at that block's `floatBlockOffsets` entry.
- Evaluation: the block is `frame // (maxFramesPerBlock - 1)`, the knot parameter is the
  frame within the block, then a standard B-spline (The NURBS Book A2.1/A2.2); rotations are
  blended component-wise and normalised.
- Quantizations present in the corpus: positions and scales 16-bit, rotations THREECOMP40
  (467610 track-blocks) and THREECOMP48 (183). THREECOMP40: three 12-bit fields, centre 2047,
  scale sqrt(0.5)/2047, bits 36-37 the dropped component's slot, bit 38 its sign. THREECOMP48:
  three u16 with 15-bit fields centre 16383, dropped slot from x bit 15 and y bit 15, sign from
  z bit 15. The 12-bit centre is confirmed by exact 0.0 components in pure-yaw keys (a centre
  off by one would show 3.5e-4); the slot/sign layout by the self-test's frame-to-frame
  continuity and by the idle gaze.

Not validated: 8-bit position/scale, POLAR32, THREECOMP24 and STRAIGHT16 never occur in the
player clips, so they raise instead of guessing; UNCOMPRESSED is implemented and was never
exercised. Float tracks are skipped. Non-uniform scale is propagated component-wise, which is
exact only for the uniform scales these clips carry. The engine's own blending, IK
(`*_Foot_Target`, `*_Hand_Target`), ragdoll simulation and TAE-driven speed (event 608 changes playback
rate, not clip-local time) are outside this tool: times here are clip-local seconds.

Right-hand weapon bone: `R_Weapon` (index 119), child of `R_Hand`, 0.09 m out along the
hand's local +X into the palm; `L_Weapon` mirrors it and `R_Shield` hangs off it. That is the
bone's name and geometry; the engine code that parents the weapon model to it was not traced.

Hurtbox: `load_ragdoll` reads the 18 capsule bodies of `c0000.chrbnd -> c0000.HKX` and the
bone each follows; `ragdoll_capsules(pose_model(...))` places them on a pose (see the section
comment above `RAGDOLL_HKX_PATH`).

Usage:

    python3 scripts/er-hkx-pose.py 26 30000 --bone R_Weapon --times 0,0.2,0.4
    python3 scripts/er-hkx-pose.py 20 30000 --bone R_Weapon --bone Head --no-root-motion --json
    python3 scripts/er-hkx-pose.py --selftest
"""
import argparse
import json
import math
import os
import struct
import sys


def _sections(b, off, end):
    out = []
    while off < end:
        h, = struct.unpack_from('>I', b, off)
        size = h & 0x3fffffff
        if size < 8:
            raise ValueError(f'bad section size at {off:#x}')
        out.append((b[off + 4:off + 8].decode('ascii'), off + 8, off + size))
        off += size
    if off != end:
        raise ValueError('sections overrun their parent')
    return out


def _section_map(b, top):
    sec = {}
    for name, a, e in _sections(b, top[1], top[2]):
        sec[name] = (a, e)
        if name in ('TYPE', 'INDX'):
            for n2, a2, e2 in _sections(b, a, e):
                sec[n2] = (a2, e2)
    return sec


class _Packed:
    """Havok's variable-length big-endian integer stream."""

    def __init__(self, b, off, end):
        self.b, self.o, self.end = b, off, end

    def next(self):
        b0 = self.b[self.o]
        for limit, n, mask in ((0x80, 1, 0x7f), (0xc0, 2, 0x3fff), (0xe0, 3, 0x1fffff),
                               (0xe8, 4, 0x7ffffff), (0xf0, 5, 0x7ffffffff),
                               (0xf8, 8, 0xffffffffffffff)):
            if b0 < limit:
                v = int.from_bytes(self.b[self.o:self.o + n], 'big') & mask
                self.o += n
                return v
        v = int.from_bytes(self.b[self.o + 1:self.o + 9], 'big')
        self.o += 9
        return v


def _consumed(r):
    """True when the stream ended exactly, or on zero padding up to the 4-byte section
    alignment (`c0000.HKX`'s `TNA1` ends on two zero bytes; the skeleton's sections do not)."""
    tail = r.b[r.o:r.end]
    return r.o <= r.end and len(tail) < 4 and not any(tail) and r.end % 4 == 0


def _parse_types(path, b, sec):
    """The `TNA1`/`TBDY` type table; both sections must be consumed to the byte or to the
    zero padding that aligns the section end."""
    tstr = b[slice(*sec['TSTR'])].split(b'\0')
    fstr = b[slice(*sec['FSTR'])].split(b'\0')
    names = 'TNA1' if 'TNA1' in sec else 'TNAM'
    r = _Packed(b, *sec[names])
    types = [None] + [{'idx': i} for i in range(1, r.next())]
    for t in types[1:]:
        t['name'] = tstr[r.next()].decode()
        t['tmpl'] = [(tstr[r.next()].decode(), r.next()) for _ in range(r.next())]
    if not _consumed(r):
        raise ValueError(f'{path}: {names} not consumed exactly')
    body = 'TBDY' if 'TBDY' in sec else 'TBOD'
    r = _Packed(b, *sec[body])
    while r.o < r.end:
        ti = r.next()
        if ti == 0:
            continue
        t = types[ti]
        t['parent'] = r.next()
        fl = r.next()
        if fl & 0x01:
            t['sub'] = r.next()
        if fl & 0x02:
            t['ptr'] = r.next()
        if fl & 0x04:
            t['ver'] = r.next()
        if fl & 0x08:
            t['size'] = r.next()
            t['align'] = r.next()
        if fl & 0x10:
            t['abstract'] = r.next()
        if fl & 0x20:
            t['members'] = [(fstr[r.next()].decode(), r.next(), r.next(), r.next())
                            for _ in range(r.next() & 0xffff)]
        if fl & 0x40:
            t['ifaces'] = [(r.next(), r.next()) for _ in range(r.next())]
        if fl & 0x80:
            t['attr'] = r.next()
    if r.o != r.end:
        raise ValueError(f'{path}: {body} not consumed exactly')
    return types


_COMPENDIUM_CACHE = {}


def _compendium_types(path, type_id):
    """Types of the `*.compendium` beside `path` whose `TCID` holds `type_id`."""
    folder = os.path.dirname(path)
    for name in sorted(os.listdir(folder)):
        if not name.endswith('.compendium'):
            continue
        full = os.path.join(folder, name)
        if full not in _COMPENDIUM_CACHE:
            with open(full, 'rb') as handle:
                c = handle.read()
            top = _sections(c, 0, len(c))[0]
            if top[0] != 'TCM0':
                raise ValueError(f'{full}: not a TCM0 compendium')
            sec = _section_map(c, top)
            ids = [c[o:o + 8] for o in range(*sec['TCID'], 8)]
            _COMPENDIUM_CACHE[full] = (ids, _parse_types(full, c, sec))
        ids, types = _COMPENDIUM_CACHE[full]
        if type_id in ids:
            return types
    raise ValueError(f'{path}: no compendium beside it carries type id {type_id.hex()}')


class Tagfile:
    """A Havok 2018 tagfile (`TAG0`) decoded through its own type table."""

    def __init__(self, path):
        with open(path, 'rb') as handle:
            self.b = b = handle.read()
        top = _sections(b, 0, len(b))[0]
        if top[0] != 'TAG0':
            raise ValueError(f'{path}: not a TAG0 tagfile')
        sec = _section_map(b, top)
        self.data = sec['DATA'][0]
        self.items = [struct.unpack_from('<III', b, o) for o in range(*sec['ITEM'], 12)]
        self._memo = {}
        if 'TCRF' in sec:
            self.types = _compendium_types(path, b[sec['TCRF'][0]:sec['TCRF'][0] + 8])
        else:
            self.types = _parse_types(path, b, sec)

    def _inherit(self, ti, key):
        while ti:
            t = self.types[ti]
            if key in t:
                return t[key]
            ti = t.get('parent', 0)
        return None

    def members(self, ti):
        chain = []
        while ti:
            chain.append(self.types[ti])
            ti = self.types[ti].get('parent', 0)
        return [m for t in reversed(chain) for m in t.get('members', [])]

    def type_name(self, ti):
        return self.types[ti]['name'] if ti else None

    def objects(self, type_name):
        """Every pointed-to object whose type is `type_name`, decoded."""
        return [self.item(i) for i, (ft, _, _) in enumerate(self.items)
                if ft >> 28 == 1 and self.type_name(ft & 0xffffff) == type_name]

    def item(self, idx):
        if idx == 0:
            return None
        if idx in self._memo:
            return self._memo[idx]
        ft, off, count = self.items[idx]
        ti = ft & 0xffffff
        base = self.data + off
        if ft >> 28 == 1:
            val = self.read(ti, base)
        else:
            size = self._inherit(ti, 'size')
            sub = self._inherit(ti, 'sub')
            if sub & 0x1f == 4 and size == 1:
                val = self.b[base:base + count]
            else:
                val = [self.read(ti, base + k * size) for k in range(count)]
        self._memo[idx] = val
        return val

    def read(self, ti, off):
        sub = self._inherit(ti, 'sub')
        kind = sub & 0x1f
        b = self.b
        if kind == 7:
            return {name: self.read(mt, off + moff) for name, _, moff, mt in self.members(ti)}
        if kind == 4:
            size = {0x2000: 1, 0x4000: 2, 0x8000: 4, 0x10000: 8}[sub & 0x1e000]
            return int.from_bytes(b[off:off + size], 'little', signed=bool(sub & 0x200))
        if kind == 5:
            return struct.unpack_from('<f', b, off)[0]
        if kind == 2:
            return bool(b[off])
        if kind == 3:
            val = self.item(struct.unpack_from('<Q', b, off)[0])
            return None if val is None else bytes(val).split(b'\0')[0].decode()
        if kind == 8 and sub & 0x20:
            elem = self._inherit(ti, 'ptr')
            size = self._inherit(elem, 'size')
            return [self.read(elem, off + k * size) for k in range(sub >> 8)]
        if kind in (6, 8):
            # A 4-byte slot (`hkRelArray`, in the ragdoll's convex shapes) holds a 32-bit
            # item index; pointers and `hkArray` hold a 64-bit one.
            if self._inherit(ti, 'size') == 4:
                return self.item(struct.unpack_from('<I', b, off)[0])
            return self.item(struct.unpack_from('<Q', b, off)[0])
        if kind in (0, 1):
            return None
        raise ValueError(f'unhandled subtype {sub:#x} for {self.types[ti]["name"]}')


# Spline-compressed track decoding.

#: Rotation quantizations: (name, byte size, alignment).
ROT_QUANT = {0: ('POLAR32', 4, 4), 1: ('THREECOMP40', 5, 1), 2: ('THREECOMP48', 6, 2),
             3: ('THREECOMP24', 3, 1), 4: ('STRAIGHT16', 2, 2), 5: ('UNCOMPRESSED', 16, 4)}
#: Rotation quantizations whose decoder has been exercised on real data (see `ROT_SEEN`).
ROT_SEEN = set()
_SQRT_HALF = math.sqrt(0.5)
THREECOMP40_CENTER = 2047
THREECOMP48_CENTER = 16383


def _align(o, a):
    return (o + a - 1) // a * a


def _quat_threecomp40(d, o):
    v = int.from_bytes(d[o:o + 5], 'little')
    scale = _SQRT_HALF / 2047
    c = [((v >> s) & 0xfff) - THREECOMP40_CENTER for s in (0, 12, 24)]
    return _place([x * scale for x in c], (v >> 36) & 3, (v >> 38) & 1)


def _quat_threecomp48(d, o):
    x, y, z = struct.unpack_from('<HHH', d, o)
    shift = ((y >> 14) & 2) | ((x >> 15) & 1)
    scale = _SQRT_HALF / 16383
    c = [((k & 0x7fff) - THREECOMP48_CENTER) * scale for k in (x, y, z)]
    return _place(c, shift, z >> 15)


def _place(c, dropped, negate):
    """The three stored components fill every slot but `dropped`; that one is rebuilt."""
    w = math.sqrt(max(0.0, 1.0 - c[0] * c[0] - c[1] * c[1] - c[2] * c[2]))
    if negate:
        w = -w
    q = list(c)
    q.insert(dropped, w)
    return tuple(q)


def _quat(d, o, kind):
    ROT_SEEN.add(kind)
    if kind == 1:
        return _quat_threecomp40(d, o)
    if kind == 2:
        return _quat_threecomp48(d, o)
    if kind == 5:
        return struct.unpack_from('<4f', d, o)
    raise NotImplementedError(f'rotation quantization {ROT_QUANT[kind][0]} is not decoded')


class _Curve:
    """One B-spline (or constant) channel set for a track within a block."""

    def __init__(self, degree, knots, points):
        self.degree, self.knots, self.points = degree, knots, points

    def at(self, u):
        pts = self.points
        if len(pts) == 1:
            return pts[0]
        n = len(pts) - 1
        p = self.degree
        k = self.knots
        span = _find_span(n, p, u, k)
        basis = _basis(span, u, p, k)
        dim = len(pts[0])
        return tuple(sum(basis[j] * pts[span - p + j][c] for j in range(p + 1))
                     for c in range(dim))


def _find_span(n, p, u, k):
    """The NURBS Book A2.1 over knot vector `k` with control points 0..n."""
    if u >= k[n + 1]:
        return n
    if u <= k[p]:
        return p
    lo, hi = p, n + 1
    mid = (lo + hi) // 2
    while u < k[mid] or u >= k[mid + 1]:
        if u < k[mid]:
            hi = mid
        else:
            lo = mid
        mid = (lo + hi) // 2
    return mid


def _basis(i, u, p, k):
    """The NURBS Book A2.2: the p+1 nonzero basis functions at `u` in span `i`."""
    out = [1.0] + [0.0] * p
    left = [0.0] * (p + 1)
    right = [0.0] * (p + 1)
    for j in range(1, p + 1):
        left[j] = u - k[i + 1 - j]
        right[j] = k[i + j] - u
        saved = 0.0
        for r in range(j):
            den = right[r + 1] + left[j - r]
            tmp = out[r] / den if den else 0.0
            out[r] = saved + right[r + 1] * tmp
            saved = left[j - r] * tmp
        out[j] = saved
    return out


def _read_vector(d, o, flags, quant, default):
    """(curve, next offset) for a position or scale channel group."""
    if flags & 0xf0:
        n, p = struct.unpack_from('<HB', d, o)
        o += 3
        knots = list(d[o:o + n + p + 2])
        o = _align(o + n + p + 2, 4)
        ranges = []
        for c in range(3):
            if flags & (0x10 << c):
                ranges.append(struct.unpack_from('<ff', d, o))
                o += 8
            elif flags & (1 << c):
                ranges.append(struct.unpack_from('<f', d, o)[0])
                o += 4
            else:
                ranges.append(default)
        width = 1 if quant == 0 else 2
        full = 255.0 if quant == 0 else 65535.0
        points = []
        for _ in range(n + 1):
            pt = []
            for c in range(3):
                if flags & (0x10 << c):
                    q = d[o] if width == 1 else struct.unpack_from('<H', d, o)[0]
                    o += width
                    lo, hi = ranges[c]
                    pt.append(lo + (hi - lo) * q / full)
                else:
                    pt.append(ranges[c])
            points.append(tuple(pt))
        return _Curve(p, knots, points), _align(o, 4)
    if flags & 0x0f:
        pt = []
        for c in range(3):
            if flags & (1 << c):
                pt.append(struct.unpack_from('<f', d, o)[0])
                o += 4
            else:
                pt.append(default)
        return _Curve(0, [], [tuple(pt)]), _align(o, 4)
    return _Curve(0, [], [(default,) * 3]), o


def _read_rotation(d, o, flags, quant):
    name, size, align = ROT_QUANT[quant]
    if flags & 0xf0:
        n, p = struct.unpack_from('<HB', d, o)
        o += 3
        knots = list(d[o:o + n + p + 2])
        o = _align(o + n + p + 2, align)
        points = []
        for _ in range(n + 1):
            points.append(_quat(d, o, quant))
            o += size
        return _Curve(p, knots, points), _align(o, 4)
    if flags & 0x0f:
        o = _align(o, align)
        q = _quat(d, o, quant)
        return _Curve(0, [], [q]), _align(o + size, 4)
    return _Curve(0, [], [(0.0, 0.0, 0.0, 1.0)]), o


def _decode_block(anim, block):
    """[(position curve, rotation curve, scale curve)] per transform track in one block.

    The walk must end exactly at the block's float-track offset (or, with no float tracks,
    within the block's 16-byte tail); a layout mistake anywhere in the block breaks that."""
    d = anim['data']
    start = anim['blockOffsets'][block]
    ntracks = anim['numberOfTransformTracks']
    masks = [d[start + 4 * i:start + 4 * i + 4] for i in range(ntracks)]
    o = start + anim['maskAndQuantizationSize']
    tracks = []
    for q, pf, rf, sf in masks:
        pos, o = _read_vector(d, o, pf, q & 3, 0.0)
        rot, o = _read_rotation(d, o, rf, (q >> 2) & 0xf)
        scl, o = _read_vector(d, o, sf, (q >> 6) & 3, 1.0)
        tracks.append((pos, rot, scl))
    float_off = anim['floatBlockOffsets'][block] if anim['floatBlockOffsets'] else None
    if float_off is not None and o - start != float_off:
        raise ValueError(f'block {block}: transform data ends at +{o - start:#x}, '
                         f'float block starts at +{float_off:#x}')
    return tracks


# Quaternion helpers, (x, y, z, w).

def _qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def _qrot(q, v):
    x, y, z, w = q
    vx, vy, vz = v
    tx = 2 * (y * vz - z * vy)
    ty = 2 * (z * vx - x * vz)
    tz = 2 * (x * vy - y * vx)
    return (vx + w * tx + y * tz - z * ty,
            vy + w * ty + z * tx - x * tz,
            vz + w * tz + x * ty - y * tx)


def _qnorm(q):
    n = math.sqrt(sum(c * c for c in q))
    return tuple(c / n for c in q) if n else (0.0, 0.0, 0.0, 1.0)


def _yaw_quat(yaw):
    return (0.0, math.sin(yaw / 2), 0.0, math.cos(yaw / 2))


# Public API.

class Skeleton:
    """Bone names, parent indices and bind-pose local transforms of the player skeleton."""

    def __init__(self, names, parents, bind):
        self.names, self.parents, self.bind = names, parents, bind
        self.index = {n: i for i, n in enumerate(names)}


class Animation:
    """A decoded player clip: per-block track curves, bone binding and root motion."""

    def __init__(self, path, anim, binding, blocks):
        self.path = path
        self.duration = anim['duration']
        self.num_frames = anim['numFrames']
        self.frame_duration = anim['frameDuration']
        self.max_frames_per_block = anim['maxFramesPerBlock']
        self.block_duration = anim['blockDuration']
        self.num_blocks = anim['numBlocks']
        self.track_to_bone = list(binding['transformTrackToBoneIndices'])
        self.blocks = blocks
        motion = anim.get('extractedMotion') or {}
        self.up = tuple(motion.get('up') or (0.0, 1.0, 0.0, 0.0))[:3]
        self.forward = tuple(motion.get('forward') or (0.0, 0.0, 1.0, 0.0))[:3]
        self.root_samples = [tuple(s) for s in motion.get('referenceFrameSamples') or []]
        self.root_duration = motion.get('duration', self.duration)

    def local_tracks(self, t, normalize=True):
        """[(translation, rotation xyzw, scale)] per transform track at clip-local `t` seconds.

        The rotation is the B-spline blend of the control quaternions, normalised unless
        `normalize` is false (the self-test reads the raw blend's length)."""
        t = min(max(t, 0.0), self.duration)
        frame = t / self.frame_duration
        per_block = self.max_frames_per_block - 1
        block = min(int(frame // per_block), self.num_blocks - 1)
        u = frame - block * per_block
        out = []
        for pos, rot, scl in self.blocks[block]:
            q = rot.at(u)
            out.append((pos.at(u), _qnorm(q) if normalize else q, scl.at(u)))
        return out

    def root_motion(self, t):
        """(x, y, z, yaw radians) of the reference frame at `t`, linear between samples."""
        s = self.root_samples
        if not s:
            return (0.0, 0.0, 0.0, 0.0)
        if len(s) == 1:
            return s[0]
        t = min(max(t, 0.0), self.root_duration)
        f = t / self.root_duration * (len(s) - 1)
        i = min(int(f), len(s) - 2)
        a = f - i
        return tuple(s[i][c] + (s[i + 1][c] - s[i][c]) * a for c in range(4))


SKELETON_PATH = os.environ.get(
    'ER_PLAYER_SKELETON_HKX',
    os.path.expanduser('~/er-extract/LOOK_HERE_WITCHY_RECURSIVE_20260713/sharded/chr/'
                       'c0000-anibnd-dcx/INTERROOT_win64/chr/c0000/hkx/Skeleton.hkx'))
_SKELETON = None
_ANIM_CACHE = {}


def _attacks_module():
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location(
        'er_mechanics_attacks', os.path.join(here, 'er-mechanics-attacks.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_HKX_PATH = None


def hkx_path(category, anim):
    """The unpacked `a<cat>_<anim>.hkx`, located the way `er-mechanics-attacks.py` does."""
    global _HKX_PATH
    if _HKX_PATH is None:
        _HKX_PATH = _attacks_module().hkx_path
    return _HKX_PATH(category, anim)


def load_skeleton(path=None):
    """The player `Skeleton` (c0000): `.names`, `.parents`, `.bind` [(t, q xyzw, s)]."""
    global _SKELETON
    if path is None and _SKELETON is not None:
        return _SKELETON
    tf = Tagfile(path or SKELETON_PATH)
    sk = tf.objects('hkaSkeleton')
    if len(sk) != 1:
        raise ValueError(f'expected one hkaSkeleton, found {len(sk)}')
    sk = sk[0]
    bind = [(tuple(p['translation'][:3]), tuple(p['rotation']), tuple(p['scale'][:3]))
            for p in sk['referencePose']]
    out = Skeleton([b['name'] for b in sk['bones']], list(sk['parentIndices']), bind)
    if path is None:
        _SKELETON = out
    return out


def load_animation(category, anim):
    """The decoded `Animation` for player clip `a<category>_<anim>.hkx`."""
    key = (category, anim)
    if key in _ANIM_CACHE:
        return _ANIM_CACHE[key]
    path = hkx_path(category, anim)
    if path is None:
        raise FileNotFoundError(f'no unpacked a{category:03d}_{anim:06d}.hkx')
    out = load_animation_file(path)
    _ANIM_CACHE[key] = out
    return out


def load_animation_file(path):
    tf = Tagfile(path)
    anims = tf.objects('hkaSplineCompressedAnimation')
    bindings = tf.objects('hkaAnimationBinding')
    if len(anims) != 1 or len(bindings) != 1:
        raise ValueError(f'{path}: {len(anims)} spline animations, {len(bindings)} bindings')
    a = anims[0]
    if bindings[0]['animation'] is not a:
        raise ValueError(f'{path}: the binding does not point at the spline animation')
    if len(bindings[0]['transformTrackToBoneIndices']) != a['numberOfTransformTracks']:
        raise ValueError(f'{path}: binding and animation disagree on the track count')
    blocks = [_decode_block(a, i) for i in range(a['numBlocks'])]
    return Animation(path, a, bindings[0], blocks)


def _local_pose(skeleton, animation, t):
    pose = list(skeleton.bind)
    for track, value in enumerate(animation.local_tracks(t)):
        pose[animation.track_to_bone[track]] = value
    return pose


def _forward_kinematics(skeleton, pose):
    model = [None] * len(pose)
    for i, (lt, lr, ls) in enumerate(pose):
        p = skeleton.parents[i]
        if p < 0:
            model[i] = (lt, lr, ls)
            continue
        pt, pr, ps = model[p]
        scaled = (lt[0] * ps[0], lt[1] * ps[1], lt[2] * ps[2])
        r = _qrot(pr, scaled)
        model[i] = ((pt[0] + r[0], pt[1] + r[1], pt[2] + r[2]), _qnorm(_qmul(pr, lr)),
                    (ps[0] * ls[0], ps[1] * ls[1], ps[2] * ls[2]))
    return model


def bone_model_transforms(category, anim, t, with_root_motion=True):
    """{bone name: ((x, y, z), (qx, qy, qz, qw))} in model space at clip-local `t` seconds.

    With `with_root_motion` the reference-frame displacement is applied on top:
    `model = RootMotion(t) * FK(t)`, `RootMotion` being a yaw about +Y then a translation."""
    sk = load_skeleton()
    a = load_animation(category, anim)
    model = _forward_kinematics(sk, _local_pose(sk, a, t))
    if with_root_motion:
        x, y, z, yaw = a.root_motion(t)
        rq = _yaw_quat(yaw)
        moved = []
        for pos, rot, scl in model:
            r = _qrot(rq, pos)
            moved.append(((r[0] + x, r[1] + y, r[2] + z), _qnorm(_qmul(rq, rot)), scl))
        model = moved
    return {name: (m[0], m[1]) for name, m in zip(sk.names, model)}


def bone_model_positions(category, anim, t, with_root_motion=True):
    """{bone name: (x, y, z)} in model space (metres) at clip-local `t` seconds."""
    return {k: v[0] for k, v in bone_model_transforms(category, anim, t, with_root_motion).items()}


def root_motion(category, anim, t):
    """(x, y, z, yaw radians) displacement of the character from clip start at `t`."""
    return load_animation(category, anim).root_motion(t)


# Ragdoll (damage hurtbox) bodies.
#
# `c0000.HKX` in the chrbnd holds, measured: one `hknpRagdollData` with 18 `bodyCinfos`, each an
# `hknpCapsuleShape` (segment `a`..`b` in the body frame, radius `convexRadius`); an 18-bone
# ragdoll `hkaSkeleton`; `boneToBodyMap` = identity; a copy of the 150-bone animation skeleton;
# and two `hkaSkeletonMapper`s (animation -> ragdoll and back). The animation -> ragdoll mapper
# has 18 simple mappings and no chain mappings, every `aFromBTransform` identity to 3e-4 m. So a
# body's frame is its mapped animation bone's model frame: the self-test checks that each body's
# authored rest `position`/`orientation` equals that bone's bind-pose model transform.

RAGDOLL_HKX_PATH = os.environ.get(
    'ER_PLAYER_RAGDOLL_HKX',
    os.path.expanduser('~/er-extract/LOOK_HERE_WITCHY_RECURSIVE_20260713/sharded/chr/'
                       'c0000-chrbnd-dcx/c0000.HKX'))
HKXPWV_PATH = os.environ.get(
    'ER_PLAYER_HKXPWV', os.path.join(os.path.dirname(RAGDOLL_HKX_PATH), 'c0000.hkxpwv'))
#: `.hkxpwv` layout, measured on c0000: a 0x20-byte header (u32 version, u16 at +6 = count of
#: 4-byte records, u16 at +8 = animation bone count, u16 at +0xa = ragdoll body count), then those
#: 4-byte records, 8 bytes per bone, and 16 bytes per body. The 16-byte records are the table
#: the damage code reads (`ResCap+0xa0`, stride 0x10, part byte at +4, count at `(+0x88)+10`,
#: bd er-damage-hurtboxes-are-per-chr-hknp-ragdoll-not-param-2026-09-01). The sizes add up to the
#: file length exactly, which `read_hkxpwv` requires.
HKXPWV_HEADER, HKXPWV_BODY_STRIDE, HKXPWV_BONE_STRIDE, HKXPWV_SMALL_STRIDE = 0x20, 0x10, 8, 4
HKXPWV_PART_OFFSET = 4


class RagdollBody:
    """One hurtbox body: capsule `a`..`b` (body frame, metres), `radius`, the animation bone it
    follows, the mapper's `a_from_b` (translation, rotation xyzw) and the authored rest pose."""

    def __init__(self, index, name, bone, a, b, radius, a_from_b, rest):
        self.index, self.name, self.bone = index, name, bone
        self.a, self.b, self.radius = a, b, radius
        self.a_from_b, self.rest = a_from_b, rest


_RAGDOLL = None


def load_ragdoll(path=None):
    """[RagdollBody] in ragdoll body order, read from the chr's `.HKX`."""
    global _RAGDOLL
    if path is None and _RAGDOLL is not None:
        return _RAGDOLL
    tf = Tagfile(path or RAGDOLL_HKX_PATH)
    data = tf.objects('hknpRagdollData')
    if len(data) != 1:
        raise ValueError(f'expected one hknpRagdollData, found {len(data)}')
    data = data[0]
    capsules = tf.objects('hknpCapsuleShape')
    ragdoll_skeleton = data['skeleton']['name']
    mapper = [m['mapping'] for m in tf.objects('hkaSkeletonMapper')
              if m['mapping']['skeletonB']['name'] == ragdoll_skeleton]
    if len(mapper) != 1 or mapper[0]['chainMappings']:
        raise ValueError('expected one simple-mapping animation -> ragdoll mapper')
    by_ragdoll_bone = {s['boneB']: s for s in mapper[0]['simpleMappings']}
    body_to_bone = {body: bone for bone, body in enumerate(data['boneToBodyMap'])}
    anim_names = [b['name'] for b in mapper[0]['skeletonA']['bones']]
    sk = load_skeleton()
    if anim_names != sk.names:
        raise ValueError('the ragdoll file carries a different animation skeleton')
    out = []
    for i, c in enumerate(data['bodyCinfos']):
        shape = c['shape']
        if not any(shape is s for s in capsules):
            raise NotImplementedError(f"body {c['name']}: shape is not an hknpCapsuleShape")
        m = by_ragdoll_bone[body_to_bone[i]]
        t = m['aFromBTransform']
        out.append(RagdollBody(
            i, c['name'], m['boneA'], tuple(shape['a'][:3]), tuple(shape['b'][:3]),
            shape['convexRadius'], (tuple(t['translation'][:3]), tuple(t['rotation'])),
            (tuple(c['position'][:3]), tuple(c['orientation']))))
    if path is None:
        _RAGDOLL = out
    return out


def read_hkxpwv(path=None):
    """{'version', 'bone_count', 'body_count', 'bodies': [16-byte records]} of a `.hkxpwv`."""
    path = path or HKXPWV_PATH
    with open(path, 'rb') as handle:
        b = handle.read()
    version, = struct.unpack_from('<I', b, 0)
    small, bones, bodies = struct.unpack_from('<3H', b, 6)
    start = HKXPWV_HEADER + small * HKXPWV_SMALL_STRIDE + bones * HKXPWV_BONE_STRIDE
    if start + bodies * HKXPWV_BODY_STRIDE != len(b):
        raise ValueError(f'{path}: header counts do not add up to the file length')
    recs = [b[start + k * HKXPWV_BODY_STRIDE:start + (k + 1) * HKXPWV_BODY_STRIDE]
            for k in range(bodies)]
    return {'version': version, 'bone_count': bones, 'body_count': bodies, 'bodies': recs}


def ragdoll_capsules(model):
    """[(body, end a, end b)] in the space of `model`, a per-bone [(t, q, s)] list such as
    `_forward_kinematics` returns: body frame = bone frame * `a_from_b`."""
    out = []
    for body in load_ragdoll():
        pt, pq, _ = model[body.bone]
        mt, mq = body.a_from_b
        off = _qrot(pq, mt)
        origin = (pt[0] + off[0], pt[1] + off[1], pt[2] + off[2])
        q = _qmul(pq, mq)
        ends = []
        for p in (body.a, body.b):
            r = _qrot(q, p)
            ends.append((origin[0] + r[0], origin[1] + r[1], origin[2] + r[2]))
        out.append((body, ends[0], ends[1]))
    return out


def pose_model(category=None, anim=None, t=0.0):
    """Per-bone model transforms: the bind pose when `category` is None, else the clip at `t`
    (no root motion: model space, character origin at the model origin)."""
    sk = load_skeleton()
    if category is None:
        return _forward_kinematics(sk, sk.bind)
    return _forward_kinematics(sk, _local_pose(sk, load_animation(category, anim), t))


# Self-test.

IDLE = (0, 0)              # a000_000000, standing idle
R1 = (20, 30000)           # a020_030000, R1 #1 (`er-mechanics-attacks.py` SLOTS_ONE_HAND)
RUN_R1 = (20, 30200)       # a020_030200, running R1 (behavior state AttackRightLightDash)
THREECOMP48_CLIP = (120, 30500)   # every rotation track of block 0 is THREECOMP48
MULTI_BLOCK_CLIP = (0, 17180)     # two blocks
R1_OTHER = (26, 30000)            # a026_030000, R1 #1 of another motion category
#: Clips in which the character never leaves the ground (standing attacks, idle).
GROUNDED = (IDLE, R1, R1_OTHER, MULTI_BLOCK_CLIP)
FEET = ('L_Toe0', 'R_Toe0', 'L_Foot', 'R_Foot')
RIGID_SEGMENTS = {f'{s}_{b}' for s in 'LR' for b in (
    'Thigh', 'Calf', 'Foot', 'Toe0', 'Clavicle', 'UpperArm', 'Forearm', 'Hand')} | {
    'Spine', 'Spine1', 'Spine2', 'Neck', 'Head'}


def _frames(a):
    return [min(i * a.frame_duration, a.duration) for i in range(a.num_frames)]


def _gaze_degrees(model, sk):
    """Heading of the eye midpoint seen from `Head`, degrees from -Z (positive toward +X)."""
    head = model[sk.index['Head']][0]
    le, re = model[sk.index['L_eyeA']][0], model[sk.index['R_eyeA']][0]
    v = [(le[i] + re[i]) / 2 - head[i] for i in range(3)]
    return math.degrees(math.atan2(v[0], -v[2]))


def _angle(q1, q2):
    d = abs(sum(a * b for a, b in zip(q1, q2)))
    return math.degrees(2 * math.acos(min(1.0, d)))


def selftest():
    failures = []

    def check(name, ok, detail):
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
        if not ok:
            failures.append(name)

    sk = load_skeleton()
    n = len(sk.names)
    check('skeleton topology', n == 150 and all(p < i for i, p in enumerate(sk.parents))
          and len(set(sk.names)) == n,
          f'{n} bones, every parent index below its child, names unique')
    rw = sk.index.get('R_Weapon')
    check('right weapon bone', rw is not None and sk.names[sk.parents[rw]] == 'R_Hand',
          f"R_Weapon={rw}, parent {sk.names[sk.parents[rw]] if rw is not None else None}")

    bind_model = _forward_kinematics(sk, sk.bind)
    toe = bind_model[sk.index['L_Toe0']][0][2] - bind_model[sk.index['L_Foot']][0][2]
    check('bind pose faces -Z', toe < -0.1, f'L_Toe0 is {toe:+.3f} m in z from L_Foot')

    clips = {}
    for key in (IDLE, R1, RUN_R1, THREECOMP48_CLIP, MULTI_BLOCK_CLIP, R1_OTHER):
        clips[key] = load_animation(*key)
    check('block layout', True, f'{len(clips)} clips: every block walk ended on its float '
          f'block offset (load raises otherwise); rotation quantizations exercised: '
          f"{sorted(ROT_QUANT[k][0] for k in ROT_SEEN)}")

    # Idle frame 0 against the skeleton file: limb and spine segments keep their bind offsets.
    # (IK targets, `RootPos` height, weapon grips and armour/pectoral helpers are animated
    # offsets and differ by design.)
    idle = clips[IDLE]
    worst, worst_bone, seen = 0.0, None, 0
    for track, (lt, _, _) in enumerate(idle.local_tracks(0.0)):
        bone = idle.track_to_bone[track]
        if sk.names[bone] not in RIGID_SEGMENTS:
            continue
        seen += 1
        d = math.dist(lt, sk.bind[bone][0])
        if d > worst:
            worst, worst_bone = d, sk.names[bone]
    check('idle frame 0 limb offsets = bind', seen >= 20 and worst < 0.002,
          f'{seen} limb/spine tracks, worst |t - bind t| {worst * 1000:.2f} mm ({worst_bone})')
    idle_model = _forward_kinematics(sk, _local_pose(sk, idle, 0.0))
    gaze = _gaze_degrees(idle_model, sk)
    check('idle gaze along -Z', abs(gaze) < 3, f'eye heading {gaze:+.2f} deg from -Z')
    toes = [idle_model[sk.index[b]][0][1] for b in ('L_Toe0', 'R_Toe0')]
    head_y = idle_model[sk.index['Head']][0][1]
    check('idle stands on y=0', all(-0.03 < y < 0.06 for y in toes) and 1.3 < head_y < 1.9,
          f'toe heights {toes[0]:+.3f} {toes[1]:+.3f} m, head {head_y:.3f} m')

    # Every frame of every test clip: quaternion length, rigid bone lengths, and for clips
    # that stay on the ground, the lowest foot point on y=0 through the whole leg chain.
    for key, a in clips.items():
        worst_len, worst_rigid, worst_fk = 0.0, 0.0, 0.0
        foot_lo, foot_hi = math.inf, -math.inf
        lengths = {}
        for t in _frames(a):
            raw = a.local_tracks(t, normalize=False)
            worst_len = max(worst_len, max(abs(math.sqrt(sum(c * c for c in q)) - 1)
                                           for _, q, _ in raw))
            pose = _local_pose(sk, a, t)
            model = _forward_kinematics(sk, pose)
            low = min(model[sk.index[b]][0][1] for b in FEET)
            foot_lo, foot_hi = min(foot_lo, low), max(foot_hi, low)
            for i, p in enumerate(sk.parents):
                if p < 0:
                    continue
                d = math.dist(model[i][0], model[p][0])
                s = model[p][2]
                expect = math.sqrt(sum((pose[i][0][c] * s[c]) ** 2 for c in range(3)))
                worst_fk = max(worst_fk, abs(d - expect))
                lengths.setdefault(i, []).append(d)
        animated = {a.track_to_bone[k] for k, (pos, _, _) in enumerate(a.blocks[0])
                    if len(pos.points) > 1}
        for i, ds in lengths.items():
            if i not in animated:
                worst_rigid = max(worst_rigid, max(ds) - min(ds))
        tag = f'a{key[0]:03d}_{key[1]:06d}'
        check(f'{tag} quaternion length', worst_len < 0.02,
              f'{a.num_frames} frames, raw spline blend within {worst_len:.4f} of unit')
        if key in GROUNDED:
            check(f'{tag} feet on the ground', -0.03 < foot_lo and foot_hi < 0.05,
                  f'lowest foot/toe point per frame stays in [{foot_lo:+.3f}, {foot_hi:+.3f}] m')
        check(f'{tag} bone lengths', worst_rigid < 1e-4 and worst_fk < 1e-4,
              f'unanimated-offset bones vary by {worst_rigid:.2e} m; '
              f'model length vs local offset {worst_fk:.2e} m')

    # Block seam: the pose must not jump where the second block takes over.
    mb = clips[MULTI_BLOCK_CLIP]
    seam = (mb.max_frames_per_block - 1) * mb.frame_duration
    before = mb.local_tracks(seam - 1e-4)
    after = mb.local_tracks(seam)
    jump_t = max(math.dist(x[0], y[0]) for x, y in zip(before, after))
    jump_r = max(_angle(x[1], y[1]) for x, y in zip(before, after))
    check('block seam continuity', jump_t < 0.01 and jump_r < 2,
          f'at {seam:.3f} s: {jump_t * 1000:.2f} mm, {jump_r:.2f} deg')

    # THREECOMP48 against THREECOMP40: a120_030500 starts from the neutral stance, and its
    # rotations are all THREECOMP48, while the idle's are THREECOMP40. Same stance, two codecs.
    # Legs, spine and head only: the arms hold whatever the weapon category holds.
    stance48 = _forward_kinematics(sk, _local_pose(sk, clips[THREECOMP48_CLIP], 0.0))
    worst, worst_bone = 0.0, None
    stance_bones = sorted(b for b in RIGID_SEGMENTS if not b.endswith(
        ('Clavicle', 'UpperArm', 'Forearm', 'Hand')))
    for b in stance_bones:
        d = math.dist(stance48[sk.index[b]][0], idle_model[sk.index[b]][0])
        if d > worst:
            worst, worst_bone = d, b
    check('THREECOMP48 stance = THREECOMP40 stance', worst < 0.01,
          f'a120_030500 frame 0 vs a000_000000 frame 0, {len(stance_bones)} leg/spine bones, '
          f'worst {worst * 1000:.1f} mm ({worst_bone})')
    master = [p for blk in clips[THREECOMP48_CLIP].blocks for p in blk[0][1].points]
    tilt = max(max(abs(q[0]), abs(q[2])) for q in master)
    largest = {max(range(4), key=lambda c: abs(q[c])) for q in master}
    check('THREECOMP48 Master stays a pure yaw', tilt < 1e-3 and largest == {1, 3},
          f'{len(master)} keys, max |x|,|z| {tilt:.1e}, largest component in slots '
          f'{sorted(largest)} (both reconstruction slots exercised)')

    # Root motion against an independent direction: the idle gaze above.
    for key in (R1, RUN_R1):
        a = clips[key]
        x, y, z, yaw = a.root_motion(a.duration)
        check(f'a{key[0]:03d}_{key[1]:06d} root motion forward',
              z < -1.0 and abs(x) < 0.3 and abs(yaw) < 0.2 and z > -8,
              f'end displacement ({x:+.3f}, {y:+.3f}, {z:+.3f}) m, yaw {math.degrees(yaw):+.1f}')

    # The weapon bone during an R1: a swing, not a hand at rest.
    a = clips[R1]
    path, pts = 0.0, []
    for t in _frames(a):
        model = _forward_kinematics(sk, _local_pose(sk, a, t))
        pts.append(model[rw][0])
    path = sum(math.dist(p, q) for p, q in zip(pts, pts[1:]))
    span = max(math.dist(p, q) for p in pts for q in pts)
    check('R1 weapon arc', path > 1.5 and span > 0.8,
          f'R_Weapon path {path:.2f} m, widest chord {span:.2f} m (model space, no root motion)')

    # Ragdoll hurtbox bodies against two independent sources: the body count in the chr's
    # `.hkxpwv` (the game drops that map on a mismatch), and each body's own authored rest pose
    # against the animation skeleton's bind pose through the mapper.
    try:
        bodies = load_ragdoll()
        pwv = read_hkxpwv()
    except OSError as err:
        print(f'SKIP  ragdoll: {err}')
    else:
        check('ragdoll body count = hkxpwv body count', len(bodies) == pwv['body_count'] == 18,
              f"{len(bodies)} bodies, hkxpwv header {pwv['body_count']} bodies / "
              f"{pwv['bone_count']} bones")
        check('hkxpwv bone count = skeleton', pwv['bone_count'] == n,
              f"{pwv['bone_count']} vs {n}")
        worst_d, worst_a = 0.0, 0.0
        for body, _, _ in ragdoll_capsules(bind_model):
            bt, bq, _ = bind_model[body.bone]
            mt, mq = body.a_from_b
            off = _qrot(bq, mt)
            pos = tuple(bt[i] + off[i] for i in range(3))
            worst_d = max(worst_d, math.dist(pos, body.rest[0]))
            worst_a = max(worst_a, _angle(_qmul(bq, mq), body.rest[1]))
        check('ragdoll rest pose = mapped bone bind pose', worst_d < 0.002 and worst_a < 0.5,
              f'worst {worst_d * 1000:.2f} mm, {worst_a:.2f} deg over {len(bodies)} bodies')
        radii = [b.radius for b in bodies]
        lengths = [math.dist(b.a, b.b) for b in bodies]
        check('ragdoll capsule sizes', 0.03 < min(radii) and max(radii) < 0.2
              and max(lengths) < 0.6,
              f'radius {min(radii):.3f}..{max(radii):.3f} m, segment {min(lengths):.3f}..'
              f'{max(lengths):.3f} m')
        posed = ragdoll_capsules(idle_model)
        lo = min(min(a[1], b[1]) - body.radius for body, a, b in posed)
        hi = max(max(a[1], b[1]) + body.radius for body, a, b in posed)
        check('idle hurtbox stands on the ground', -0.05 < lo < 0.05 and head_y < hi < 2.0,
              f'vertical span {lo:+.3f}..{hi:.3f} m (head bone {head_y:.3f} m)')

    print('selftest', 'FAILED: ' + ', '.join(failures) if failures else 'ok')
    return 1 if failures else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('category', nargs='?', type=int)
    ap.add_argument('anim', nargs='?', type=int)
    ap.add_argument('--bone', action='append', help='bone name (repeatable; default R_Weapon)')
    ap.add_argument('--times', default=None, help='comma-separated clip-local seconds '
                    '(default: every frame)')
    ap.add_argument('--no-root-motion', action='store_true')
    ap.add_argument('--rotation', action='store_true', help='also print model rotation xyzw')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.category is None or args.anim is None:
        ap.error('category and anim are required')
    sk = load_skeleton()
    bones = args.bone or ['R_Weapon']
    for b in bones:
        if b not in sk.index:
            ap.error(f'no bone {b!r}; bones: {", ".join(sk.names)}')
    a = load_animation(args.category, args.anim)
    times = ([float(x) for x in args.times.split(',')] if args.times else _frames(a))
    rows = []
    for t in times:
        tf = bone_model_transforms(args.category, args.anim, t, not args.no_root_motion)
        rm = a.root_motion(t)
        row = {'t': t, 'root_motion': [round(c, 5) for c in rm], 'bones': {}}
        for b in bones:
            pos, rot = tf[b]
            row['bones'][b] = {'pos': [round(c, 5) for c in pos]}
            if args.rotation:
                row['bones'][b]['rot'] = [round(c, 5) for c in rot]
        rows.append(row)
    if args.json:
        print(json.dumps({'clip': f'a{args.category:03d}_{args.anim:06d}',
                          'duration': a.duration, 'frame_duration': a.frame_duration,
                          'root_motion_applied': not args.no_root_motion,
                          'convention': '+Y up, forward -Z, right hand on -X', 'rows': rows},
                         indent=1))
        return 0
    print(f'a{args.category:03d}_{args.anim:06d}  duration {a.duration:.4f} s  '
          f'{a.num_frames} frames  root motion {"off" if args.no_root_motion else "on"}  '
          f'(+Y up, forward -Z, right hand -X)')
    for row in rows:
        rm = row['root_motion']
        parts = [f"t={row['t']:.4f}", f'root=({rm[0]:+.3f},{rm[1]:+.3f},{rm[2]:+.3f},'
                 f'{math.degrees(rm[3]):+.1f}deg)']
        for b, v in row['bones'].items():
            p = v['pos']
            s = f'{b}=({p[0]:+.3f},{p[1]:+.3f},{p[2]:+.3f})'
            if 'rot' in v:
                s += ' q=(' + ','.join(f'{c:+.4f}' for c in v['rot']) + ')'
            parts.append(s)
        print('  '.join(parts))
    return 0


if __name__ == '__main__':
    sys.exit(main())
