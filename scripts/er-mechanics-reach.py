#!/usr/bin/env python3
"""Reach, hitbox shape, tracking (turn speed) and animation play speed per weapon attack.

Write-up: `docs/er-mechanics/reach.md`. Labels as in the other `docs/er-mechanics` docs:
`VERIFIED` = regulation value or read out of the executable (1.16.2 named dump on :8765,
re-found in `eldenring-deobf-1.17.1.bin`), `TAE` = decoded animation event, `MEASURED` =
read out of an unpacked model or clip, `INFERRED` = fits the data, consumer not traced,
`COMMUNITY` = outside claim (Smithbox annotations, WitchyBND TimeAct template).

Hit shapes (`VERIFIED` regulation, meaning `COMMUNITY` Smithbox annotation):

    AtkParam_Pc.hit<i>_Radius, hit<i>_DmyPoly1, hit<i>_DmyPoly2, i = 0..15
    DmyPoly2 == -1 -> sphere at DmyPoly1, otherwise a capsule DmyPoly1..DmyPoly2
    hit<i>_hitType is the hit part (0 tip, 1 middle, 2 root, 3 map collision), not the shape
    hitSourceType 0 weapon, 1 body

Weapon dummies (`MEASURED`): `EquipParamWeapon.equipModelId` names `parts/wp_a_<id>.partsbnd`;
its FLVER2 dummy table (0x40 bytes each, right after the 0x80 header) gives each dummy's
ReferenceID and position. The model origin is the grip and the blade runs along -Y. A dummy's
position is in its parent bone's space; most weapon bones are identity, the Shotel's is a half
turn about Y, so every position is carried through the bone chain (`flver_bone_to_model`). Ids
100/110/120/130 are on most weapon models; 100 is at the tip.

Body dummies (`MEASURED` c0000.flver, lookup `INFERRED`): a hit shape whose unprefixed dummy ids
are not on the weapon model, or whose source is the body, is read off the player model and
follows that dummy's attach bone. Dane's Footwork's kicks are such shapes (R_Foot, R_Knee).
Rigged weapon models (`weapon_attached`): the claws' and fist-hands' hit dummies attach to player
skeleton bones and are placed the same way; whips' and flails' attach to the model's own bones,
whose animation is not decoded, so those rows get no world reach (`world_reach_error`) and the
PvP score uses the class fallback (`class_fallback`).

    weapon reach = max over the hit shapes' dummy points of |point| + radius   (from the grip)

Character side: placing the grip in the world at the hit frame needs the arm pose and the root
motion of the clip. `scripts/er-hkx-pose.py` decodes the player clips; when it is present,
`world_reach` below poses the weapon bone at every hit frame and reports the forward distance of
the farthest hit point from the character's position at animation start.

Defender side (`MEASURED`): the player's hurtbox is the 18 ragdoll capsules of `c0000.HKX`, posed
in idle. `defender_hurtbox()` gives per-direction radii; each reach row gains `target_centre_m`
(world reach + idle front radius, an upper bound) and `contact_centre_m` (the farthest defender
centre still touched, hit height kept), both for a defender facing the attacker.

Tracking (`VERIFIED` handlers, `TAE` windows):

    TAE 224 SetTurnSpeed (0x14042c480): f32 deg/s, u8 IsLockOnCheck (1: only while locked on),
        u8 priority at +5 (read only when the event's +0x1a word is >= 0x19, else -1)
        -> CSChrActionFlagModule::SetTurnSpeed 0x140406a60 (lower-or-equal priority overwrites)
    ChrCtrlJointModifier::CalculateTurnSpeed 0x1403c4090 picks, first that is >= 0:
        joint turn speed (TAE 704 path), the TAE 224 turn speed, the behavior-data turn speed
        (module +0xc0, field +0x250, set from HKS), else the modifier's own turnVelocity;
        times 0.017453292 (deg -> rad, 0x14329e62c)
    TAE 0 JumpTable 7 (case 0x140427853): actionModifiersFlags |= 0x8000, named
        "Disable Turning" by the community template. Read by the ChrCtrl update FUN_1403cbff0
        (0x1403cc10f): bit 15 set clears its may-rotate flag and it zeroes rotationUpdate.

Spatial coverage (`MEASURED` pose and dummies; the metrics are modelling choices, `INFERRED`):
every 60 Hz sample of a live damaging shape becomes spheres along the capsule; each sphere's
contact disc (sphere radius plus the idle defender's widest hurtbox radius at that height) is
laid on the ground plane of the attacker's start position. The union is the footprint: where a
defender can stand and be hit. `sweep_row_arc_deg` (azimuth covered per forward distance,
averaged), `sweep_mean_width_m`, the swing shape of the far end (sweep / slam / thrust) and the
hit heights come from it; `coverage_factor` combines the arc with the turn budget.

Play speed (`VERIFIED`):

    TAE 608 AnimSpeedGradient (0x140426420): speed = start + (end - start) * min(progress, 1),
        stored as the pending multiplier at CSChrBehaviorModule+0x15c4 (0x14041bda0)
    each behavior update latches +0x15c4 into +0x15c0 and resets +0x15c4 to 1.0 (0x14041d6ac)
    CSChrBehaviorModule::Update 0x14041d760: graph dt = dt * behaviorDataFactor * debugSpeed
        * animSpeedGradientMultiplier(+0x15c0)

So clip time runs `speed` times faster than real time inside a 608 window. Real frames below are
`30 * integral of dt / speed` (30 fps is the attacks-doc convention, `INFERRED`).
`behaviorDataFactor` (thunk 0x140416270 -> 0x144cb547a) is a debug override, 1.0 unless a
`GlobalDebugFlags` play-speed switch is set (`VERIFIED`, bd
behaviordatafactor-is-debug-gated-1-in-retail-2026-09-29); EquipParamWeapon and SpEffectParam have
no play-speed column (`VERIFIED`: no such field in either paramdef).

Usage:

    python3 scripts/er-mechanics-reach.py Greatsword
    python3 scripts/er-mechanics-reach.py Giant-Crusher --grip both --json
    python3 scripts/er-mechanics-reach.py --table
    python3 scripts/er-mechanics-reach.py --selftest
"""
import argparse
import glob
import importlib.util
import json
import math
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ATK = _load('er_mechanics_attacks', 'er-mechanics-attacks.py')
PR = ATK.PR

#: Unpacked weapon parts (`parts/wp_a_<model>.partsbnd.dcx` -> `wp_a_<model>-partsbnd-dcx/`).
PARTS_ROOT = os.environ.get(
    'ER_PARTS_ROOT',
    os.path.expanduser('~/er-extract/LOOK_HERE_WITCHY_RECURSIVE_20260713/sharded/parts'))
SMITHBOX_ER = os.environ.get(
    'ER_SMITHBOX_PARAM_DIR', os.path.expanduser('~/.local/share/smithbox/app/Assets/PARAM/ER'))
DEOBF_1162 = os.environ.get('ER_DEOBF_1162', os.path.join(ROOT, 'eldenring-deobf.bin'))
DEOBF_1171 = os.environ.get('ER_DEOBF_1171', os.path.join(ROOT, 'eldenring-deobf-1.17.1.bin'))
TAE_TEMPLATE_ER = ATK.TAE_TEMPLATE_ER

TAE_FPS = ATK.TAE_FPS
TAE_ATTACK_BEHAVIOR = ATK.TAE_ATTACK_BEHAVIOR
TAE_JUMP_TABLE = ATK.TAE_JUMP_TABLE
TAE_SET_TURN_SPEED = 224
TAE_ANIM_SPEED_GRADIENT = 608
#: Turn-related types seen in the template; only 224 (and one 703) occur in player attack clips.
TAE_TURN_TYPES = (224, 703, 704, 705, 706)
JT_DISABLE_TURNING = 7
JUMP_TABLE_STATE_GATE_OFFSET = ATK.JUMP_TABLE_STATE_GATE_OFFSET
#: TAE 1 Args+0xe: SpEffect state-info id. Nonzero skips the hitbox unless the attacker has it
#: (`VERIFIED`: 0x1404266d0 `movzx edx, word [rbx+0xe]` -> 0x1404f95a0 on the SpEffect module).
#: The Lance's judge 5000-5005 hitboxes carry 187 (only SpEffect 1908 has that state info).
ATTACK_STATE_INFO_OFFSET = 0xe

#: FLVER2 layout (SoulsFormats FLVER2; header 0x80, dummy 0x40, material 0x20, bone 0x80).
FLVER_HEADER_SIZE, FLVER_DUMMY_SIZE = 0x80, 0x40
FLVER_MATERIAL_SIZE, FLVER_BONE_SIZE = 0x20, 0x80
#: Header byte 0x49 is the Unicode flag; a bone's name offset is the i32 at +0xc.
FLVER_UNICODE_OFFSET, FLVER_BONE_NAME_OFFSET = 0x49, 0xc
#: Dummy fields: position f32x3 @0, forward @0x10, ReferenceID s16 @0x1c, ParentBoneIndex s16
#: @0x1e, upward @0x20, AttachBoneIndex s16 @0x2c.
DUMMY_REF_OFFSET, DUMMY_ATTACH_OFFSET = 0x1c, 0x2c
#: AtkParam dummy ids >= 10000 carry a model prefix: 10xxx right weapon, 11xxx left weapon
#: (`INFERRED` from the value ranges; 21xxx occurs and is left unresolved).
DMY_PREFIX_RIGHT, DMY_PREFIX_LEFT = 10, 11
HIT_SLOTS = 16
HIT_PART = {0: 'tip', 1: 'middle', 2: 'root', 3: 'map'}
HIT_SOURCE = {0: 'weapon', 1: 'body'}

#: Weapons the doc tables: the five asked for, then the Strength-tag primaries from
#: `docs/er-mechanics/giant-crusher-adoption-gap.md` (shields left out).
TABLE_WEAPONS = ['Greatsword', 'Giant-Crusher', 'Erdsteel Dagger', 'Uchigatana', 'Lance',
                 "Cleanrot Knight's Sword", 'Shamshir', "Devonia's Hammer",
                 "Fire Knight's Greatsword", 'Claymore', 'Ruins Greatsword', 'Zweihander']
#: Slots the table prints (keys of er-mechanics-attacks SLOTS_ONE_HAND, `2h_` for two hands).
TABLE_SLOTS = ['r1_1', 'r2_1', 'r2_1c', 'run_r1', 'roll_r1', '2h_r1_1', '2h_r2_1', '2h_run_r1']


# --------------------------------------------------------------------------- regulation

class Reach:
    """Regulation tables plus the hit-shape columns of AtkParam_Pc."""

    def __init__(self, regulation=None):
        self.reg = ATK.Regulation(regulation)
        files = PR.load(regulation)
        fields = ['hitSourceType'] + [f'hit{i}_{k}' for i in range(HIT_SLOTS)
                                      for k in ('Radius', 'DmyPoly1', 'DmyPoly2', 'hitType')]
        fields += [f'hti{i}_Priority' for i in range(HIT_SLOTS)]
        rows, _, _ = PR.rows(PR.param_bytes(files, 'AtkParam_Pc'), fields)
        self.atk_shape = {r['id']: r for r in rows}
        wrows, _, _ = PR.rows(PR.param_bytes(files, 'EquipParamWeapon'), ['equipModelId'])
        self.model = {r['id']: r['equipModelId'] for r in wrows}


def hit_shapes(atk_row):
    """The row's spheres and capsules, in hit-slot order."""
    out = []
    for i in range(HIT_SLOTS):
        d1, d2 = atk_row[f'hit{i}_DmyPoly1'], atk_row[f'hit{i}_DmyPoly2']
        radius = atk_row[f'hit{i}_Radius']
        if d1 == -1 or radius <= 0:
            continue
        out.append({
            'slot': i,
            'shape': 'sphere' if d2 == -1 else 'capsule',
            'dmy': (d1,) if d2 == -1 else (d1, d2),
            'radius': round(radius, 4),
            'part': HIT_PART.get(atk_row[f'hit{i}_hitType'], atk_row[f'hit{i}_hitType']),
            'priority': atk_row[f'hti{i}_Priority'],
        })
    return out


# --------------------------------------------------------------------------- FLVER dummies

_PARTS_INDEX = None


def weapon_flver_path(model_id):
    """`WP_A_<model>.flver` in the unpacked parts tree, or None."""
    global _PARTS_INDEX
    if _PARTS_INDEX is None:
        _PARTS_INDEX = {}
        for d in glob.glob(os.path.join(PARTS_ROOT, '_chunk_*', 'wp_a_*-partsbnd-dcx')) + \
                glob.glob(os.path.join(PARTS_ROOT, 'wp_a_*-partsbnd-dcx')):
            _PARTS_INDEX.setdefault(os.path.basename(d), d)
    d = _PARTS_INDEX.get(f'wp_a_{model_id:04d}-partsbnd-dcx')
    path = d and os.path.join(d, f'WP_A_{model_id:04d}.flver')
    return path if path and os.path.exists(path) else None


def read_flver(path):
    """{'bbox': (min, max), 'dummies': [...], 'bones': [...]} from a FLVER2 file."""
    with open(path, 'rb') as handle:
        b = handle.read()
    if b[:6] != b'FLVER\0' or b[6:8] != b'L\0':
        raise ValueError(f'{path}: not a little-endian FLVER')
    dummy_count, material_count, bone_count = struct.unpack_from('<3i', b, 0x14)
    bbox = (struct.unpack_from('<3f', b, 0x28), struct.unpack_from('<3f', b, 0x34))
    dummies = []
    for i in range(dummy_count):
        o = FLVER_HEADER_SIZE + i * FLVER_DUMMY_SIZE
        ref, parent = struct.unpack_from('<hh', b, o + DUMMY_REF_OFFSET)
        dummies.append({'id': ref, 'pos': struct.unpack_from('<3f', b, o), 'parent': parent,
                        'attach': struct.unpack_from('<h', b, o + DUMMY_ATTACH_OFFSET)[0]})
    bones = []
    base = FLVER_HEADER_SIZE + dummy_count * FLVER_DUMMY_SIZE + material_count * FLVER_MATERIAL_SIZE
    unicode = b[FLVER_UNICODE_OFFSET]
    for i in range(bone_count):
        o = base + i * FLVER_BONE_SIZE
        bones.append({'t': struct.unpack_from('<3f', b, o), 'r': struct.unpack_from('<3f', b, o + 0x10),
                      'parent': struct.unpack_from('<h', b, o + 0x1c)[0],
                      's': struct.unpack_from('<3f', b, o + 0x20),
                      'name': _flver_string(b, struct.unpack_from('<i', b, o + FLVER_BONE_NAME_OFFSET)[0],
                                            unicode)})
    return {'bbox': bbox, 'dummies': dummies, 'bones': bones}


def _flver_string(b, offset, unicode):
    if not 0 < offset < len(b):
        return None
    if unicode:
        end = offset
        while end + 1 < len(b) and b[end:end + 2] != b'\0\0':
            end += 2
        return b[offset:end].decode('utf-16le', 'replace')
    end = b.index(b'\0', offset)
    return b[offset:end].decode('shift_jis', 'replace')


def _rot_axis(axis, angle, v):
    c, s = math.cos(angle), math.sin(angle)
    x, y, z = v
    if axis == 0:
        return (x, c * y - s * z, s * y + c * z)
    if axis == 1:
        return (c * x + s * z, y, -s * x + c * z)
    return (c * x - s * y, s * x + c * y, z)


def flver_bone_to_model(flver, bone, v):
    """`v` in FLVER bone `bone`'s space carried to model space through the bone chain.

    Each bone's local transform is scale, then rotation about X, then Z, then Y (radians), then
    translation: SoulsFormats `FLVER.Node.ComputeLocalTransform` (`COMMUNITY`), confirmed here
    (`MEASURED`): on c0000.flver it puts 138 of the 150 bones shared with the HKX skeleton on the
    HKX bind pose within 1 mm (R_Foot, R_Knee to 2e-6 m); the 12 off are IK and `*_Dummy` helpers.
    The X-Y-Z and Z-Y-X orders miss by 1.6 m and 1.9 m."""
    while 0 <= bone < len(flver['bones']):
        node = flver['bones'][bone]
        v = tuple(v[i] * node['s'][i] for i in range(3))
        for axis in (0, 2, 1):
            v = _rot_axis(axis, node['r'][axis], v)
        v = tuple(v[i] + node['t'][i] for i in range(3))
        bone = node['parent']
    return v


def dummy_positions(flver):
    """{ReferenceID: [model-space position]}. A dummy's position is in its parent bone's space,
    so it is carried through `flver_bone_to_model`. Most weapon bones are identity; the Shotel's
    (wp_a_0402) dummy parent is a half turn about Y, and without it three of its dummies lie
    outside the model's own bounding box (`MEASURED`, the self-test checks it). A parent index
    past the bone table is reported as unresolved."""
    out, unresolved = {}, []
    for d in flver['dummies']:
        p = d['parent']
        if p >= len(flver['bones']):
            unresolved.append(d['id'])
            continue
        out.setdefault(d['id'], []).append(flver_bone_to_model(flver, p, d['pos']))
    return out, unresolved


_FLVER_CACHE = {}


def weapon_dummies(model_id):
    if model_id not in _FLVER_CACHE:
        path = weapon_flver_path(model_id)
        _FLVER_CACHE[model_id] = dummy_positions(read_flver(path)) + (path,) if path else None
    return _FLVER_CACHE[model_id]


def attached_dummies(flver):
    """{ReferenceID: [(model-space position, attach bone name or None, attach bone's bind model
    position or None)]}.

    FLVER dummy fields (SoulsFormats `FLVER.Dummy`, `COMMUNITY`): the position is in the
    `ParentBoneIndex` bone's space, and the dummy then follows the `AttachBoneIndex` bone; -1
    follows nothing inside the model."""
    out = {}
    for d in flver['dummies']:
        if d['parent'] >= len(flver['bones']) or d['attach'] >= len(flver['bones']):
            continue
        attach = d['attach']
        name = flver['bones'][attach]['name'] if attach >= 0 else None
        bind = flver_bone_to_model(flver, attach, (0.0, 0.0, 0.0)) if attach >= 0 else None
        out.setdefault(d['id'], []).append((flver_bone_to_model(flver, d['parent'], d['pos']), name, bind))
    return out


_ATTACH_CACHE = {}


def weapon_attached(model_id):
    """`attached_dummies` of a weapon model, or {} when it is not unpacked.

    A sword's hit dummies attach to nothing (-1) and sit in grip space. Some weapon models are
    rigged instead (`MEASURED`, a sweep of every model): the claws and fist-hands (Beast Claw
    1631, Red Bear's Claw 1630, Poisoned Hand 1168, Madding Hand 1169) carry 377-bone copies of
    the player skeleton, their model origin is the character origin, and their hit dummies attach
    to `R_Hand` / `R_Finger*`; whips, flails and a few others (21 models) attach them to the
    model's own bones (`Born24`, `Body`), animated by the weapon's own anibnd."""
    if model_id not in _ATTACH_CACHE:
        path = weapon_flver_path(model_id)
        _ATTACH_CACHE[model_id] = attached_dummies(read_flver(path)) if path else {}
    return _ATTACH_CACHE[model_id]


#: The player body model. Its dummies carry the hit shapes of attacks that are not on the weapon
#: model: Dane's Footwork (a246) kicks use c0000 dummies 5 (attach bone R_Foot), 930 (R_Knee) and
#: 240 (attach -1), while its weapon model wp_a_1370 has only dummies 400 and 401.
BODY_FLVER_PATH = os.environ.get(
    'ER_PLAYER_FLVER',
    os.path.expanduser('~/er-extract/LOOK_HERE_WITCHY_RECURSIVE_20260713/sharded/chr/'
                       'c0000-chrbnd-dcx/c0000.flver'))
_BODY = []


def body_dummies():
    """`attached_dummies` of c0000.flver, or {} when it is not unpacked. An attach of -1 on the
    body model follows nothing inside it, so it moves with the character (root motion)."""
    if not _BODY:
        _BODY.append(attached_dummies(read_flver(BODY_FLVER_PATH))
                     if os.path.exists(BODY_FLVER_PATH) else {})
    return _BODY[0]


def rigged_shape_points(shape, attached, skeleton_names):
    """How a weapon-source shape whose dummies attach to bones of the weapon model is placed:
    ('body', points) when every dummy attaches to a bone the player skeleton also has (the claws:
    the model is a copy of the player rig, so the dummy rides that bone like a body dummy),
    ('unposed', bone names) when one attaches to a bone of the model's own (whips: that bone is
    animated by the weapon's anibnd, which is not decoded), else None (a grip-space dummy)."""
    points, own = [], []
    for dmy in shape['dmy']:
        local = dmy % 1000 if dmy >= 10000 else dmy
        entries = attached.get(local)
        if not entries or entries[0][1] is None:
            return None
        p, bone, bind = entries[0]
        if bone in skeleton_names:
            points.append((p, bone, bind))
        else:
            own.append(bone)
    return ('unposed', own) if own else ('body', points)


def body_shape_points(shape, source, weapon_dummies_):
    """The shape's body-model points [(bind position, attach bone, attach bind)], or None.

    A shape resolves on the body when its source is the body (`hitSourceType` 1), or when it is a
    weapon-source shape whose unprefixed dummy ids are absent from the weapon model and present
    on c0000. The engine's dummy lookup was not traced; this is `INFERRED` from Dane's Footwork,
    whose weapon-source kick capsules name c0000's R_Foot and R_Knee dummies (5 and 930)."""
    body = body_dummies()
    points = []
    for dmy in shape['dmy']:
        if dmy >= 10000:
            return None
        if source != 'body' and dmy in weapon_dummies_:
            return None
        if dmy not in body:
            return None
        points.append(body[dmy][0])
    return points


def shape_extent(shape, dummies):
    """Farthest reach of one shape from the grip (m), or None when a dummy is missing.

    `extent` is |point| + radius over the shape's end points (a capsule's farthest point from
    the origin is at one of its ends); `blade` is the same along -Y only.
    """
    points = []
    for dmy in shape['dmy']:
        local = dmy % 1000 if dmy >= 10000 else dmy
        if local not in dummies:
            return None
        points.extend(dummies[local])
    r = shape['radius']
    return {'extent': round(max(math.sqrt(sum(c * c for c in p)) for p in points) + r, 3),
            'blade': round(max(-p[1] for p in points) + r, 3),
            'points': [tuple(round(c, 3) for c in p) for p in points]}


# --------------------------------------------------------------------------- TAE timing

def speed_windows(events):
    """[(start s, end s, speed at start, speed at end)] of TAE 608 events."""
    out = []
    for e in events:
        if e.type == TAE_ANIM_SPEED_GRADIENT:
            s0, s1 = struct.unpack_from('<ff', e.params, 0)
            out.append((e.start, e.end, s0, s1))
    return out


def speed_at(t, windows):
    """Play-speed multiplier at clip time t. Overlapping 608 events: the later one in TAE order
    is taken (each writes the same pending field; `INFERRED`, no overlap occurs in the clips
    tabled here)."""
    speed = 1.0
    for start, end, s0, s1 in windows:
        if start <= t < end:
            progress = (t - start) / (end - start) if end > start else 1.0
            speed = s0 + (s1 - s0) * min(progress, 1.0)
    return speed


def real_seconds(t, windows, step=1.0 / 600):
    """Real seconds taken to reach clip time t (integral of dt / speed)."""
    if not windows:
        return t
    total, x = 0.0, 0.0
    while x < t:
        h = min(step, t - x)
        total += h / speed_at(x + h / 2, windows)
        x += h
    return total


def _frame(t):
    return round(t * TAE_FPS, 1)


def _turn_integral(turns, disabled, a0, a1, windows):
    """({lock: degrees}, {lock: free real seconds}) of turning over clip time [a0, a1).

    Inside a Disable Turning window the rotation request is zeroed whatever the 224 speed is
    (`VERIFIED` write, `JT7_READER` below), so those frames add nothing. Elsewhere the last usable 224
    event run wins (priority -1 always overwrites, `INFERRED` from SetTurnSpeed); a frame with no
    usable 224 event falls back to the HKS rate (`DEFAULT_TURN_DEG_PER_S`, when it is known)."""
    deg = {'locked': 0.0, 'unlocked': 0.0}
    free = {'locked': 0.0, 'unlocked': 0.0}
    if a1 <= a0:
        return deg, free
    cuts = sorted({a0, a1} | {x for w in turns for x in (w['start'], w['end'])}
                  | {x for d in disabled for x in d})
    cuts = [c for c in cuts if a0 <= c <= a1]
    for a, b in zip(cuts, cuts[1:]):
        mid = (a + b) / 2
        if any(s <= mid < e for s, e in disabled):
            continue
        dt_real = real_seconds(b, windows) - real_seconds(a, windows)
        active = [w for w in turns if w['start'] <= mid < w['end']]
        # A lock-on-only event returns before SetTurnSpeed when not locked on, which leaves
        # the default turn rate in charge (CalculateTurnSpeed's fallbacks).
        for lock in ('locked', 'unlocked'):
            usable = [w for w in active if lock == 'locked' or not w['lock_on_only']]
            if usable:
                deg[lock] += usable[-1]['deg_per_s'] * dt_real
            else:
                free[lock] += dt_real
                if DEFAULT_TURN_DEG_PER_S is not None:
                    deg[lock] += DEFAULT_TURN_DEG_PER_S * dt_real
    return deg, free


#: The turn rate in force during attack frames with no usable TAE 224 event. Next in
#: `CalculateTurnSpeed` is behavior-data +0x250, written by the HKS `SetTurnSpeed` act (id 2004,
#: `HksAct` 0x14040cbd0, `VERIFIED` write). No attack state or helper in `c0000.hks` reaches that
#: act (`scripts/er-hks-disasm.py --reaches SetTurnSpeed`: only move, roll and step states, and
#: the move values only for AI-controlled players), and `ChrIns::PreBehaviorSafe` -> FUN_1404146b0
#: resets +0x250 to -1.0 every frame (`VERIFIED` store). So the joint modifier's own
#: `turnVelocity` applies: 720.0, stored by the `ChrCtrlJointModifier` constructor
#: (`TURN_VELOCITY_STORE`, `VERIFIED`). That the player's modifier keeps it is `INFERRED`: the only
#: `SetTurnVelocity` caller found is the enemy path, and an inlined write was not ruled out.
DEFAULT_TURN_DEG_PER_S = 720.0


def turn_details(events, hit_start, windows, hit_end=None):
    """TAE 224 windows, Disable-Turning windows, the turn budget before the first hit and the
    turn available while the hitbox is live (`hit_start` to `hit_end`)."""
    turns, disabled = [], []
    for e in events:
        if e.type == TAE_SET_TURN_SPEED:
            deg, lock_only, priority = struct.unpack_from('<fBb', e.params, 0)
            turns.append({'frames': (_frame(e.start), _frame(e.end)), 'start': e.start,
                          'end': e.end, 'deg_per_s': round(deg, 2),
                          'lock_on_only': bool(lock_only), 'priority': priority})
        elif e.type == TAE_JUMP_TABLE:
            jid = struct.unpack_from('<i', e.params, 0)[0]
            gated = struct.unpack_from('<H', e.params, JUMP_TABLE_STATE_GATE_OFFSET)[0]
            if jid == JT_DISABLE_TURNING and not gated:
                disabled.append((e.start, e.end))
    budget, free = _turn_integral(turns, disabled, 0.0, hit_start or 0.0, windows)
    late_start = _clip_time_before(hit_start or 0.0, TURN_LATE_FRAMES / TAE_FPS, windows)
    late, _ = _turn_integral(turns, disabled, late_start, hit_start or 0.0, windows)
    lockin = _turn_lockin(turns, disabled, hit_start or 0.0, windows)
    live, _ = _turn_integral(turns, disabled, hit_start or 0.0,
                             hit_end if hit_end is not None else hit_start or 0.0, windows)
    return {
        'turn_windows': [{k: v for k, v in w.items() if k not in ('start', 'end')} for w in turns],
        'disable_turning': [(_frame(s), _frame(e)) for s, e in disabled],
        'turn_budget_deg_locked': round(budget['locked'], 1),
        'turn_budget_deg_unlocked': round(budget['unlocked'], 1),
        'default_turn_frames_locked': round(free['locked'] * TAE_FPS, 1),
        'default_turn_frames_unlocked': round(free['unlocked'] * TAE_FPS, 1),
        'turn_live_deg_locked': round(live['locked'], 1),
        'turn_live_deg_unlocked': round(live['unlocked'], 1),
        'turn_late_deg_locked': round(late['locked'], 1),
        'turn_late_deg_unlocked': round(late['unlocked'], 1),
        'turn_lockin_frames_locked': lockin['locked'],
        'turn_lockin_frames_unlocked': lockin['unlocked'],
    }


#: The late-turn window: real frames before the first hit in which a defender's dodge is a
#: reaction to the swing already under way (`INFERRED`, about a third of a second).
TURN_LATE_FRAMES = 10


def _clip_time_before(t, real_dt, windows):
    """The clip time whose real time is `real_dt` seconds before clip time t (0 at the start)."""
    target = real_seconds(t, windows) - real_dt
    if target <= 0:
        return 0.0
    lo, hi = 0.0, t
    for _ in range(40):
        mid = (lo + hi) / 2
        if real_seconds(mid, windows) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _turn_lockin(turns, disabled, hit_start, windows):
    """{lock: real frames from the last instant the attacker can still turn to the first hit}:
    the direction is locked in that long before the hitbox arrives. None when it cannot turn
    at all before the hit."""
    out = {}
    for lock in ('locked', 'unlocked'):
        last = None
        cuts = sorted({0.0, hit_start} | {x for w in turns for x in (w['start'], w['end'])}
                      | {x for d in disabled for x in d})
        cuts = [c for c in cuts if 0.0 <= c <= hit_start]
        for a, b in zip(cuts, cuts[1:]):
            mid = (a + b) / 2
            if any(s <= mid < e for s, e in disabled):
                continue
            usable = [w for w in turns if w['start'] <= mid < w['end']
                      and (lock == 'locked' or not w['lock_on_only'])]
            rate = usable[-1]['deg_per_s'] if usable else DEFAULT_TURN_DEG_PER_S
            if rate:
                last = b
        out[lock] = None if last is None else round(
            (real_seconds(hit_start, windows) - real_seconds(last, windows)) * TAE_FPS, 1)
    return out


# --------------------------------------------------------------------------- per attack

#: TAE (SDT/ER) animation header: animFileOffset at +0x18 points at the mini-header, whose first
#: int is 1 for "import other animation" with the source id (category * 1000000 + anim) at +0x18
#: (`MEASURED`: every a263 entry is such an import, e.g. a263_030000 -> 137030000; layout from
#: SoulsFormats `TAE.Animation.MiniHeader.ImportOtherAnim`, `COMMUNITY`).
TAE_MINI_HEADER_OFFSET, TAE_MINI_IMPORT, TAE_IMPORT_ID_OFFSET = 0x18, 1, 0x18
#: A standard mini-header (type 0) with the byte at +0x19 set plays another clip's HKX, whose id
#: is at +0x1c (`MEASURED`: a026_030310, the crouch R1, names 26030300, the rolling R1 clip;
#: SoulsFormats `MiniHeader.Standard.ImportsHKX` / `ImportHKXSourceAnimID`, `COMMUNITY`).
TAE_MINI_IMPORTS_HKX, TAE_HKX_SOURCE_OFFSET = 0x19, 0x1c
_IMPORTS, _HKX_SOURCES = {}, {}


def hkx_source(category, anim):
    """(category, anim) of the HKX clip a TAE entry plays; itself unless it imports one."""
    tae_imports(category)
    return _HKX_SOURCES[category].get(anim, (category, anim))


def tae_imports(category):
    """{anim: (source category, source anim)} for the import-only entries of a<cat>.tae."""
    if category not in _IMPORTS:
        out, hkx = {}, {}
        _HKX_SOURCES[category] = hkx
        path = os.path.join(ATK.PLAYER_TAE_DIR, f'a{category}.tae')
        if os.path.exists(path):
            with open(path, 'rb') as handle:
                b = handle.read()
            count, table = struct.unpack_from('<i', b, 0x54)[0], struct.unpack_from('<q', b, 0x58)[0]
            for i in range(count):
                anim, offset = struct.unpack_from('<qq', b, table + 16 * i)
                mini = struct.unpack_from('<q', b, offset + TAE_MINI_HEADER_OFFSET)[0]
                if not 0 < mini < len(b) - 0x20:
                    continue
                kind = struct.unpack_from('<i', b, mini)[0]
                if kind == TAE_MINI_IMPORT:
                    src = struct.unpack_from('<i', b, mini + TAE_IMPORT_ID_OFFSET)[0]
                    out[anim] = (src // 1000000, src % 1000000)
                elif kind == 0 and b[mini + TAE_MINI_IMPORTS_HKX]:
                    src = struct.unpack_from('<i', b, mini + TAE_HKX_SOURCE_OFFSET)[0]
                    if src >= 0:
                        hkx[anim] = (src // 1000000, src % 1000000)
        _IMPORTS[category] = out
    return _IMPORTS[category]


def resolve_clip(category, anim, depth=4):
    """(category, anim, events) after following import-only TAE entries."""
    for _ in range(depth):
        anims = ATK.tae_animations(category) or {}
        events = anims.get(anim)
        source = tae_imports(category).get(anim)
        if events or source is None:
            return category, anim, events
        category, anim = source
    return category, anim, None


def _clip(reg, weapon_id, slot, anim, grip):
    """(TAE category, anim id, events, note) the attack is read from, imports followed.

    The animation comes from `er-mechanics-attacks.slot_animation`, so both modules read the
    same TAE entry: a colossal weapon's crouch R1 is its rolling R1 (`IsUseStealthAttack`),
    not the a023 straight-sword crouch clip the EXE fallback would find."""
    anim, category, note = ATK.slot_animation(reg.weapon[weapon_id], slot, anim, grip)
    return (*resolve_clip(category, anim), note)


#: TAE 307 PCBehavior: Args+4 u32 flags, Args+8 judge. With flag 8 the judge resolves through the
#: weapon's behaviorVariationId like event 1 (`VERIFIED` 0x14042a580; `er-mechanics-ashes`
#: `anim_actions`). Skill TimeActs fire some of their hits this way; slot clips are read as before.
TAE_PC_BEHAVIOR, PC_BEHAVIOR_WEAPON_FLAG = 307, 8


def attack_reach(rc, weapon_id, slot, label, judge, anim, grip, clip=None):
    """One attack slot, or None when the weapon has no such attack.

    `clip` = (TAE category, anim id) reads that TimeAct entry instead of the slot's motion
    category: a skill's `a<600 + swordArtsTypeNew>` animation (`skill_reach`). Its hitboxes then
    also include event 307 judges with flag 8."""
    reg = rc.reg
    nums = ATK.attack_numbers(reg, weapon_id, judge)
    if nums is None:
        return None
    if clip is not None:
        cat, src_anim, events = resolve_clip(*clip)
        crouch_note = None
    else:
        cat, src_anim, events, crouch_note = _clip(reg, weapon_id, slot, anim, grip)
    if events is None and crouch_note:
        return None
    model = rc.model.get(weapon_id)
    dm = weapon_dummies(model) if model is not None else None
    dummies, bad_dummies, flver = dm if dm else ({}, [], None)
    attached = weapon_attached(model) if dm else {}
    pose_mod = _pose_module()
    skeleton = set(pose_mod.load_skeleton().names) if pose_mod is not None else set()
    hitboxes, first_hit, first_end, hit_windows, gated_windows = [], None, None, [], []
    judges = [(judge, None, 0)]
    if events is not None:
        judges = []
        for e in events:
            if e.type == TAE_PC_BEHAVIOR and clip is not None:
                flags, j = struct.unpack_from('<Ii', e.params, 4)
                if flags & PC_BEHAVIOR_WEAPON_FLAG:
                    judges.append((j, (e.start, e.end), 0))
                continue
            if e.type != TAE_ATTACK_BEHAVIOR:
                continue
            j = struct.unpack_from('<i', e.params, 8)[0]
            state_info = struct.unpack_from('<H', e.params, ATTACK_STATE_INFO_OFFSET)[0]
            judges.append((j, (e.start, e.end), state_info))
    seen = set()
    for j, window, state_info in judges:
        n = ATK.attack_numbers(reg, weapon_id, j)
        if n is None:
            continue
        row = rc.atk_shape.get(n['atk_row'])
        if row is None:
            continue
        damaging = (n['mv_phys'] + n['mv_mag'] + n['mv_fire'] + n['mv_light'] + n['mv_holy'] > 0
                    or n['poise_damage'] > 0)
        if window is not None and damaging:
            entry = (_frame(window[0]), _frame(window[1]), j)
            if state_info:
                gated_windows.append(entry + (state_info,))
            else:
                hit_windows.append(entry)
                if first_hit is None or window[0] < first_hit:
                    first_hit = window[0]
                    first_end = window[1]
        if (j, n['atk_row']) in seen:
            continue
        seen.add((j, n['atk_row']))
        src = HIT_SOURCE.get(row['hitSourceType'], row['hitSourceType'])
        for shape in hit_shapes(row):
            rigged = rigged_shape_points(shape, attached, skeleton) if src == 'weapon' else None
            ext = shape_extent(shape, dummies) if src == 'weapon' and \
                (rigged is None or rigged[0] == 'unposed') else None
            body = rigged[1] if rigged and rigged[0] == 'body' else \
                (body_shape_points(shape, src, dummies) if ext is None and rigged is None else None)
            hitboxes.append({'judge': j, 'atk_row': n['atk_row'], 'source': src,
                             'state_info': state_info, 'damaging': damaging, **shape,
                             **({'extent_m': ext['extent'], 'blade_m': ext['blade'],
                                 'points': ext['points']} if ext else {'extent_m': None}),
                             **({'body_points': body} if body else {}),
                             **({'unposed_bones': rigged[1]} if rigged and rigged[0] == 'unposed'
                                else {})})
    reaches = [h['extent_m'] for h in hitboxes
               if h.get('extent_m') is not None and h['damaging'] and not h['state_info']]
    out = {
        'slot': slot, 'label': label, 'anim': f'a{cat:03d}_{src_anim:06d}', 'judge': judge,
        'atk_row': nums['atk_row'], 'model': model, 'flver': flver,
        'weapon_reach_m': max(reaches) if reaches else None,
        'hit_windows': hit_windows, 'state_gated_windows': gated_windows, 'hitboxes': hitboxes,
        'unresolved_dummies': bad_dummies,
    }
    if crouch_note:
        out['crouch_fallback'] = crouch_note
    if events is not None:
        windows = speed_windows(events)
        out['speed_windows'] = [(_frame(s), _frame(e), round(a, 3), round(b, 3))
                                for s, e, a, b in windows]
        if first_hit is not None:
            out['first_hit_frame_clip'] = _frame(first_hit)
            out['first_hit_frame_real'] = round(real_seconds(first_hit, windows) * TAE_FPS, 1)
        out.update(turn_details(events, first_hit, windows, first_end))
        clip_cat, clip_anim = hkx_source(cat, src_anim)
        out['hkx'] = f'a{clip_cat:03d}_{clip_anim:06d}'
        pose = world_reach(clip_cat, clip_anim, hit_windows, hitboxes, dummies)
        if pose:
            front = pose.pop('front_contact_t', None) or {}
            points = pose.pop('window_points', None) or {}
            # Per-window contact samples, in real seconds from the clip's start: a skill's per-hit
            # landing (`er-mechanics-ashes.skill_landing`) and every attack's reaction dodge
            # (`er-mechanics-ashes.reaction_outcome`, through `slot_contacts` for a slot).
            out['window_contacts'] = {
                key: [(real_seconds(t, windows), peak, pts) for t, peak, pts in entries]
                for key, entries in points.items()}
            out.update(pose)
            out['front_contact_frame_real'] = {
                dist: round(real_seconds(t, windows) * TAE_FPS, 1) for dist, t in front.items()}
        cover = coverage_factor(out)
        if cover:
            out.update(cover)
    return out


def weapon_reach(rc, weapon_id, grip='one'):
    rows = []
    for slot, label, judge, anim, _ in ATK.SLOTS_ONE_HAND:
        if grip == 'both':
            slot, label = '2h_' + slot, '2H ' + label
            judge, anim = judge + ATK.TWO_HAND_JUDGE_OFFSET, anim + ATK.TWO_HAND_ANIM_OFFSET
        r = attack_reach(rc, weapon_id, slot, label, judge, anim, grip)
        if r is not None and (r['hit_windows'] or r.get('flver') is None):
            rows.append(r)
    return rows


# --------------------------------------------------------------------------- world placement

_POSE = False


def _pose_module():
    """`scripts/er-hkx-pose.py`, or None when it is absent or fails to load."""
    global _POSE
    if _POSE is False:
        path = os.path.join(HERE, 'er-hkx-pose.py')
        _POSE = None
        if os.path.exists(path):
            try:
                _POSE = _load('er_hkx_pose', 'er-hkx-pose.py')
            except Exception:  # noqa: BLE001 -- optional input, reported as absent
                _POSE = None
    return _POSE


#: Weapon model frame on the hand's weapon bone: a half turn about Z, weapon (x, y, z) ->
#: bone (-x, -y, z), grip at the bone origin (`INFERRED`, two measurements): the Lance R1 thrust
#: (a037_030000 f18-21) drives `R_Weapon` toward the character's front (-Z) while the bone's +Y
#: points along it, so the blade (-Y in the FLVER) maps to bone +Y; and of the two half turns that
#: do that, only this one puts the Giant-Crusher's tip dummy 100 (x = -0.403) on the leading face
#: of the head in all three swings tested (a031_030000, a031_030010, a031_032000). The engine code
#: that attaches the model was not traced.
WEAPON_TO_BONE = (-1.0, -1.0, 1.0)
POSE_SAMPLE_HZ = 60


def _qrot(q, v):
    x, y, z, w = q
    vx, vy, vz = v
    tx, ty, tz = 2 * (y * vz - z * vy), 2 * (z * vx - x * vz), 2 * (x * vy - y * vx)
    return (vx + w * tx + (y * tz - z * ty), vy + w * ty + (z * tx - x * tz),
            vz + w * tz + (x * ty - y * tx))


# --------------------------------------------------------------------------- defender hurtbox

#: The defender's pose: standing idle a000_000000 frame 0 (`MEASURED` clip). Hurtboxes are the
#: chr's ragdoll bodies keyframed onto its animated skeleton every frame (`VERIFIED`, bd
#: er-damage-hurtboxes-are-per-chr-hknp-ragdoll-not-param-2026-09-01), so a defender in another
#: animation has other extents; idle is the reference stance.
DEFENDER_CLIP = (0, 0)
#: Horizontal directions in the defender's model space (it faces -Z, right hand on -X).
HURTBOX_DIRECTIONS = {'front': (0.0, -1.0), 'back': (0.0, 1.0), 'right': (-1.0, 0.0),
                      'left': (1.0, 0.0)}
#: Movement/push capsule, for comparison only (`VERIFIED` InitForPlayer 0x140460050 immediates,
#: from the bd entry above): not the damage hurtbox.
PUSH_CAPSULE_RADIUS, PUSH_CAPSULE_HALF_HEIGHT = 0.4, 1.5
CONTACT_SEGMENT_SAMPLES = 32
_DEFENDER = {}


def defender_capsules(pose_name='idle'):
    """[(body name, end a, end b, radius)] of the player hurtbox in model space, or None."""
    if pose_name not in _DEFENDER:
        pose = _pose_module()
        caps = None
        if pose is not None:
            try:
                model = pose.pose_model(*DEFENDER_CLIP) if pose_name == 'idle' \
                    else pose.pose_model()
                caps = [(b.name, a, e, b.radius) for b, a, e in pose.ragdoll_capsules(model)]
            except (OSError, ValueError, KeyError):
                caps = None
        _DEFENDER[pose_name] = caps
    return _DEFENDER[pose_name]


def defender_hurtbox(pose_name='idle'):
    """Horizontal distance from the character origin to the farthest hurtbox surface point in
    each direction (`MEASURED`: c0000 ragdoll capsules posed on `pose_name`, 'idle' or 'bind'),
    the largest over every direction, and the vertical span. None without the pose decoder."""
    caps = defender_capsules(pose_name)
    if caps is None:
        return None
    out = {'pose': 'a000_000000 frame 0' if pose_name == 'idle' else 'bind pose',
           'label': 'MEASURED', 'bodies': len(caps)}
    for label, (ux, uz) in HURTBOX_DIRECTIONS.items():
        best = max(((max(ux * p[0] + uz * p[2] for p in (a, e)) + r, name)
                    for name, a, e, r in caps))
        out[f'{label}_m'] = round(best[0], 3)
        out[f'{label}_body'] = best[1]
    best = max((max(math.hypot(p[0], p[2]) for p in (a, e)) + r, name)
               for name, a, e, r in caps)
    out['max_any_direction_m'], out['max_body'] = round(best[0], 3), best[1]
    out['bottom_m'] = round(min(min(a[1], e[1]) - r for _, a, e, r in caps), 3)
    out['top_m'] = round(max(max(a[1], e[1]) + r for _, a, e, r in caps), 3)
    return out


def contact_centre_distance(point, radius, caps):
    """Largest distance along -Z from the attacker's start position to the centre of a defender
    who faces the attacker (yawed 180 degrees) at which a sphere of `radius` at `point` still
    touches one of the defender's capsules; None when no capsule is level with the sphere."""
    cx, cy, cz = point
    best = None
    for _, a, e, r in caps:
        big = radius + r
        for k in range(CONTACT_SEGMENT_SAMPLES + 1):
            u = k / CONTACT_SEGMENT_SAMPLES
            q = [a[i] + (e[i] - a[i]) * u for i in range(3)]
            # Defender point in the attacker's frame: (-qx, qy, -qz - d).
            lateral = (cx + q[0]) ** 2 + (cy - q[1]) ** 2
            if lateral > big * big:
                continue
            d = math.sqrt(big * big - lateral) - cz - q[2]
            if best is None or d > best:
                best = d
    return best


def _root_peak_forward(pose, category, anim, until):
    """A function of clip seconds t (0 <= t <= `until`): the largest forward root motion (-Z)
    reached at any 1/POSE_SAMPLE_HZ sample up to t. `front_contact_times` needs it to know how
    far the attacker was held back by a body in its path."""
    step = 1 / POSE_SAMPLE_HZ
    peaks, running = [], 0.0
    for k in range(math.ceil(until / step) + 1):
        running = max(running, -pose.root_motion(category, anim, k * step)[2])
        peaks.append(running)

    def peak(t):
        return peaks[max(0, min(len(peaks) - 1, math.floor(t / step + 1e-9)))]
    return peak


#: A body dummy's attach bone must sit on the same bind position in c0000.flver and in the HKX
#: skeleton, or the dummy is not placed (m).
BODY_BIND_TOLERANCE_M = 1e-3


def _hkx_bind(pose):
    """{bone name: (bind model position, rotation)} of the HKX player skeleton."""
    sk = pose.load_skeleton()
    return {name: (m[0], m[1]) for name, m in zip(sk.names, pose.pose_model())}


def _qconj(q):
    return (-q[0], -q[1], -q[2], q[3])


def _shape_world_points(h, model, root, bind):
    """(end points, far end, anchor) of one hit shape in model space with root motion, or None.

    A weapon shape rides the hand's weapon bone (`WEAPON_TO_BONE`); `far` is the end farthest
    from the grip and `anchor` the grip. A body shape's dummy follows its attach bone: bind
    position carried by (bone now) x (bone at bind)^-1; an attach of -1 rides the root motion
    (`INFERRED`: the dummy is fixed in the character's model space). `far` is the DmyPoly1 end,
    and `anchor` is that end too."""
    if h.get('points'):
        bone = 'L_Weapon' if any(d // 1000 == DMY_PREFIX_LEFT for d in h['dmy']) else 'R_Weapon'
        origin, q = model[bone]

        def place(p):
            off = _qrot(q, tuple(c * s for c, s in zip(p, WEAPON_TO_BONE)))
            return tuple(origin[i] + off[i] for i in range(3))

        far = max(h['points'], key=lambda p: sum(c * c for c in p))
        return [place(p) for p in h['points']], place(far), origin
    ends = []
    for p, bone, flver_bind in h['body_points']:
        if bone is None:
            x, y, z, yaw = root
            r = _qrot((0.0, math.sin(yaw / 2), 0.0, math.cos(yaw / 2)), p)
            ends.append((r[0] + x, r[1] + y, r[2] + z))
            continue
        if bone not in bind or bone not in model:
            return None
        b_origin, b_q = bind[bone]
        if max(abs(b_origin[i] - flver_bind[i]) for i in range(3)) > BODY_BIND_TOLERANCE_M:
            return None
        local = _qrot(_qconj(b_q), tuple(p[i] - b_origin[i] for i in range(3)))
        origin, q = model[bone]
        off = _qrot(q, local)
        ends.append(tuple(origin[i] + off[i] for i in range(3)))
    return ends, ends[0], ends[0]


def world_reach(category, anim, hit_windows, hitboxes, dummies):
    """Forward reach of the hit shapes while they are live, from the pose decoder, or None.

    Model space from `scripts/er-hkx-pose.py`: +Y up, the character faces -Z at animation start,
    root motion included. For every damaging, ungated weapon shape and every 1/60 s of its hit
    window, each dummy point is carried through the weapon bone; forward reach is the largest
    `-z + radius`. `standing` subtracts the root motion at that instant (reach from where the
    body is when the hit lands); `root_motion_to_hit_m` is the forward root motion at the first
    hit frame (the lunge).
    """
    pose = _pose_module()
    if pose is None or not hit_windows:
        return None
    by_judge = {}
    live = {j for _, _, j in hit_windows}
    for h in hitboxes:
        if not h['damaging'] or h['state_info']:
            continue
        if h.get('unposed_bones') and h['judge'] in live:
            # A partial placement would drop the part of the weapon that reaches farthest.
            return {'world_reach_error': 'hit dummies ride the weapon model own bones '
                    f"{sorted(set(h['unposed_bones']))} (its anibnd is not decoded)"}
        if (h['source'] == 'weapon' and h.get('points')) or h.get('body_points'):
            by_judge.setdefault(h['judge'], []).append(h)
    best = None
    caps = defender_capsules('idle')
    contact = None
    samples = []
    try:
        bind = _hkx_bind(pose)
        peak_forward = _root_peak_forward(pose, category, anim,
                                          max(e for _, e, _ in hit_windows) / TAE_FPS)
        for start, end, judge in hit_windows:
            shapes = by_judge.get(judge, [])
            if not shapes:
                continue
            n = max(1, round((end - start) / TAE_FPS * POSE_SAMPLE_HZ))
            for k in range(n + 1):
                t = (start + (end - start) * k / n) / TAE_FPS
                model = pose.bone_model_transforms(category, anim, t, with_root_motion=True)
                root = pose.root_motion(category, anim, t)
                for h in shapes:
                    placed = _shape_world_points(h, model, root, bind)
                    if placed is None:
                        continue
                    ends, far, anchor = placed
                    for w in ends:
                        forward = -w[2] + h['radius']
                        if best is None or forward > best[0]:
                            best = (forward, t, w, -root[2], -anchor[2], h['judge'], h['dmy'])
                        if caps:
                            d = contact_centre_distance(w, h['radius'], caps)
                            if d is not None and (contact is None or d > contact[0]):
                                contact = (d, t, w)
                    samples.append({'t': t, 'judge': judge, 'win': start, 'shape': h['slot'],
                                    'radius': h['radius'], 'root': root,
                                    'peak_forward': max(peak_forward(t), -root[2]),
                                    'far': far, 'points': _capsule_points(h['shape'], ends)})
    except (OSError, ValueError, KeyError) as err:
        return {'world_reach_error': str(err)}
    if best is None:
        return None
    first = min(w[0] for w in hit_windows) / TAE_FPS
    lunge = -pose.root_motion(category, anim, first)[2]
    forward, t, w, root_fwd, grip_fwd, judge, dmy = best
    front = defender_hurtbox('idle')
    extra = {}
    if front is not None:
        # Bound: the reach point meets the defender's most forward body point, whatever its
        # height. Contact: the same, with each hit point's height and lateral offset kept.
        extra['target_centre_m'] = round(forward + front['front_m'], 3)
        if contact is not None:
            extra['contact_centre_m'] = round(contact[0], 3)
            extra['contact_at_frame_clip'] = _frame(contact[1])
            extra['contact_point_height_y'] = round(contact[2][1], 3)
    extra.update(sweep_coverage(samples))
    extra['front_contact_t'] = front_contact_times(samples)
    extra['window_points'] = window_points(samples)
    return {
        **extra,
        'world_reach_m': round(forward, 3),
        'standing_reach_m': round(forward - root_fwd, 3),
        'root_motion_to_hit_m': round(lunge, 3),
        'reach_at_frame_clip': _frame(t),
        'reach_point': {'lateral_x': round(w[0], 3), 'height_y': round(w[1], 3),
                        'grip_forward_m': round(grip_fwd - root_fwd, 3), 'judge': judge,
                        'dmy': dmy},
    }


# --------------------------------------------------------------------------- spatial coverage

#: Spacing of the points a capsule is sampled at along its axis (m). A 60 Hz pose sample of a
#: capsule becomes spheres of the capsule's radius at these points.
CAPSULE_POINT_SPACING_M = 0.1
#: Height slices of the defender profile and row height of the footprint raster (m).
DEFENDER_SLICE_M = 0.05
FOOTPRINT_ROW_M = 0.05
#: Footprint cells nearer the attacker's start position than this are left out of the arc: the
#: arc is meant to describe the swing, not the hilt passing the attacker's own body.
SWEEP_MIN_RANGE_M = 1.0
#: Hit points nearer the attacker's body (at that instant) than this are left out of the swing
#: shape and the hit heights, for the same reason.
SWING_MIN_RANGE_M = 0.8


def _capsule_points(shape, pts):
    """Centres of the spheres a shape is sampled as, from its end points `pts` (any one space):
    a sphere is its one point, a capsule its two end points and evenly spaced points between
    them. Interpolating placed end points equals placing interpolated weapon-space points, the
    weapon being rigid; a body capsule may span two bones, so only placed ends are right for it."""
    if shape != 'capsule' or len(pts) != 2:
        return list(pts)
    a, b = pts
    n = max(1, math.ceil(math.dist(a, b) / CAPSULE_POINT_SPACING_M))
    return [tuple(a[i] + (b[i] - a[i]) * k / n for i in range(3)) for k in range(n + 1)]


_PROFILE = {}


def defender_profile(pose_name='idle'):
    """{slice index: radius}: per height slice of DEFENDER_SLICE_M, the largest horizontal
    distance from the defender's vertical axis to its hurtbox surface, in any direction. Taking
    every direction makes it rotation-free, so the footprint below does not depend on which way
    the defender faces; it is an upper bound on a defender who faces the attacker. None without
    the pose decoder."""
    if pose_name not in _PROFILE:
        caps = defender_capsules(pose_name)
        prof = None
        if caps:
            prof = {}
            for _, a, e, r in caps:
                for k in range(CONTACT_SEGMENT_SAMPLES + 1):
                    u = k / CONTACT_SEGMENT_SAMPLES
                    q = [a[i] + (e[i] - a[i]) * u for i in range(3)]
                    horiz = math.hypot(q[0], q[2])
                    for j in range(math.floor((q[1] - r) / DEFENDER_SLICE_M),
                                   math.ceil((q[1] + r) / DEFENDER_SLICE_M) + 1):
                        dy = j * DEFENDER_SLICE_M - q[1]
                        if abs(dy) <= r:
                            rr = horiz + math.sqrt(r * r - dy * dy)
                            if rr > prof.get(j, -1.0):
                                prof[j] = rr
        _PROFILE[pose_name] = prof
    return _PROFILE[pose_name]


def contact_disc_radius(height, radius, profile):
    """Horizontal distance from a hit sphere's centre within which a defender's axis is touched,
    or None when the sphere is above or below every hurtbox slice."""
    best = None
    lo = math.floor((height - radius) / DEFENDER_SLICE_M)
    hi = math.ceil((height + radius) / DEFENDER_SLICE_M)
    for j in range(lo, hi + 1):
        if j not in profile:
            continue
        dy = j * DEFENDER_SLICE_M - height
        if abs(dy) > radius:
            continue
        d = profile[j] + math.sqrt(radius * radius - dy * dy)
        if best is None or d > best:
            best = d
    return best


def _merge(intervals):
    out = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def _azimuth(x, z):
    """Degrees from the attacker's start facing (-Z), positive to its right (-X)."""
    return math.degrees(math.atan2(-x, -z))


def footprint(samples, profile, row=FOOTPRINT_ROW_M, min_range=SWEEP_MIN_RANGE_M):
    """Where a defender can stand and be hit: the union, in the ground plane of the attacker's
    start position, of each sampled hit sphere's contact disc.

    Returns {rows: {row index: [[x0, x1], ...]}, area_m2, depth_m, mean_width_m, arc_deg,
    row_arc_deg, arc_left_deg, arc_right_deg}. `arc_deg` is the measure of the azimuths (about
    the start position, beyond `min_range`) the footprint covers anywhere; left/right are its
    extremes. `row_arc_deg` is the azimuth the footprint covers at one forward distance, averaged
    over the rows it occupies: the angular tolerance at a typical range, which is what a turn
    adds to. The union `arc_deg` is dominated by the rows nearest the attacker.
    """
    rows = {}
    for s in samples:
        for p in s['points']:
            d = contact_disc_radius(p[1], s['radius'], profile)
            if d is None:
                continue
            cx, cz = p[0], p[2]
            for iz in range(math.floor((cz - d) / row), math.ceil((cz + d) / row) + 1):
                zc = (iz + 0.5) * row
                dz = zc - cz
                if abs(dz) >= d:
                    continue
                dx = math.sqrt(d * d - dz * dz)
                rows.setdefault(iz, []).append((cx - dx, cx + dx))
    rows = {k: _merge(v) for k, v in rows.items()}
    area = sum(b - a for v in rows.values() for a, b in v) * row
    depth = len(rows) * row
    arcs, row_arcs = [], []
    for iz, spans in rows.items():
        zc = (iz + 0.5) * row
        row_arc = 0.0
        for a, b in spans:
            pieces = [(a, b)]
            if abs(zc) < min_range:
                c = math.sqrt(min_range * min_range - zc * zc)
                pieces = [(a, min(b, -c)), (max(a, c), b)]
            for x0, x1 in pieces:
                if x1 <= x0:
                    continue
                # Behind the start position a span crossing x = 0 wraps through +-180 degrees.
                split = [(x0, x1)] if not (zc > 0 and x0 < 0 < x1) else [(x0, -1e-9), (1e-9, x1)]
                for u0, u1 in split:
                    f0, f1 = _azimuth(u0, zc), _azimuth(u1, zc)
                    arcs.append((min(f0, f1), max(f0, f1)))
                    row_arc += abs(f1 - f0)
        if row_arc > 0:
            row_arcs.append(row_arc)
    arcs = _merge(arcs)
    return {
        'rows': rows,
        'area_m2': round(area, 3),
        'depth_m': round(depth, 3),
        'mean_width_m': round(area / depth, 3) if depth else 0.0,
        'arc_deg': round(sum(b - a for a, b in arcs), 1),
        'row_arc_deg': round(sum(row_arcs) / len(row_arcs), 1) if row_arcs else 0.0,
        'arc_left_deg': round(min(a for a, _ in arcs), 1) if arcs else None,
        'arc_right_deg': round(max(b for _, b in arcs), 1) if arcs else None,
    }


#: Distances straight ahead of the attacker's start position (m) at which `front_contact_times`
#: places the defender's axis.
FRONT_CONTACT_DISTANCES_M = (1.5, 2.0, 2.5, 3.0)


def front_contact_times(samples, distances=FRONT_CONTACT_DISTANCES_M):
    """{distance: clip seconds of the first sample whose hit shape touches a defender whose axis
    stands `distance` metres straight ahead of the attacker's start facing (-Z)}; a distance the
    attack never touches is left out.

    The hit window opens when the hitbox goes live, wherever it is: a horizontal swing that
    starts at the attacker's side is live before it reaches the front. This is the frame a
    defender the attacker faced when pressing the button is first hit, turning left out.

    The defender's body stops the attacker's root motion: the two push capsules
    (`PUSH_CAPSULE_RADIUS`) cannot overlap, so the attacker's root gets no nearer than
    `dist - 2 * PUSH_CAPSULE_RADIUS`. Every forward metre the clip would carry it beyond that is
    taken off the hit points, and taken off for good, since a clip that steps back afterwards
    steps back from where the body was stopped (`peak_forward`, the largest forward root motion
    so far). Without it a lunge such as the Giant-Crusher two-handed R2 (4.7 m of root motion
    before its hitbox is live) walks through a defender at 3 m and "misses" him. `INFERRED`:
    the defender is taken to hold his ground; the push each body gives the other was not read.
    Lateral root motion and yaw are left as the clip has them."""
    profile = defender_profile('idle')
    out = {}
    if not profile:
        return out
    for s in sorted(samples, key=lambda s: s['t']):
        for dist in distances:
            if dist in out:
                continue
            held = max(0.0, s.get('peak_forward', 0.0) - (dist - 2 * PUSH_CAPSULE_RADIUS))
            for p in s['points']:
                d = contact_disc_radius(p[1], s['radius'], profile)
                if d is not None and math.hypot(p[0], p[2] + held + dist) < d:
                    out[dist] = s['t']
                    break
    return out


def window_points(samples):
    """{(window start clip frame, judge): [(clip seconds, peak forward, ((x, z, disc), ...))]}:
    every sampled hit sphere of each hit window, in time order, with the radius of its contact
    disc against the idle defender (`contact_disc_radius`). `window_contact_time` reads it to ask
    whether one hit of a multi-hit attack touches a defender standing at a given distance."""
    profile = defender_profile('idle')
    out = {}
    if not profile:
        return out
    for s in sorted(samples, key=lambda s: s['t']):
        pts = []
        for p in s['points']:
            d = contact_disc_radius(p[1], s['radius'], profile)
            if d is not None:
                pts.append((p[0], p[2], d))
        out.setdefault((s['win'], s['judge']), []).append(
            (s['t'], s.get('peak_forward', 0.0), tuple(pts)))
    return out


def window_contact_time(entries, distance_at):
    """The first time in `entries` (one window of `window_points`, times in any unit
    `distance_at` takes) at which a hit sphere touches a defender whose axis stands
    `distance_at(time)` metres straight ahead of the attacker's start position, or None.

    The attacker is held back by the defender's body exactly as in `front_contact_times`; a
    defender pushed back by an earlier hit is simply farther away (`distance_at` grows)."""
    for when, peak, pts in entries:
        dist = distance_at(when)
        held = max(0.0, peak - (dist - 2 * PUSH_CAPSULE_RADIUS))
        for x, z, d in pts:
            if math.hypot(x, z + held + dist) < d:
                return when
    return None


def _body_frame(p, root):
    """A model-space point relative to the attacker's root at that instant (position and yaw)."""
    x, y, z = p[0] - root[0], p[1] - root[1], p[2] - root[2]
    c, s = math.cos(-root[3]), math.sin(-root[3])
    return (c * x + s * z, y, -s * x + c * z)


def swing_shape(samples, min_range=SWING_MIN_RANGE_M):
    """How the far end of each damaging shape moves while it is live, relative to the attacker's
    body: horizontal arc length, vertical travel and radial (in and out) travel, in metres, plus
    the lowest and highest hit-sphere surface beyond `min_range` of the body."""
    tracks = {}
    lo = hi = None
    for s in samples:
        key = (s['judge'], s['win'], s['shape'])
        tracks.setdefault(key, []).append((s['t'], _body_frame(s['far'], s['root'])))
        for p in s['points']:
            b = _body_frame(p, s['root'])
            if math.hypot(b[0], b[2]) < min_range:
                continue
            lo = b[1] - s['radius'] if lo is None else min(lo, b[1] - s['radius'])
            hi = b[1] + s['radius'] if hi is None else max(hi, b[1] + s['radius'])
    horiz = vert = radial = 0.0
    for track in tracks.values():
        track.sort()
        for (_, p), (_, q) in zip(track, track[1:]):
            rp, rq = math.hypot(p[0], p[2]), math.hypot(q[0], q[2])
            dphi = math.radians(abs((_azimuth(q[0], q[2]) - _azimuth(p[0], p[2]) + 180) % 360 - 180))
            horiz += dphi * (rp + rq) / 2
            vert += abs(q[1] - p[1])
            radial += abs(rq - rp)
    parts = {'sweep': horiz, 'slam': vert, 'thrust': radial}
    return {
        'swing_horizontal_m': round(horiz, 3),
        'swing_vertical_m': round(vert, 3),
        'swing_radial_m': round(radial, 3),
        'swing_shape': max(parts, key=parts.get) if any(parts.values()) else None,
        'hit_height_min_m': None if lo is None else round(lo, 3),
        'hit_height_max_m': None if hi is None else round(hi, 3),
    }


def sweep_coverage(samples):
    """Footprint and swing-shape columns of one attack (see `footprint` and `swing_shape`)."""
    if not samples:
        return {}
    out = swing_shape(samples)
    profile = defender_profile('idle')
    if profile:
        fp = footprint(samples, profile)
        out.update({'sweep_area_m2': fp['area_m2'], 'sweep_depth_m': fp['depth_m'],
                    'sweep_mean_width_m': fp['mean_width_m'], 'sweep_arc_deg': fp['arc_deg'],
                    'sweep_row_arc_deg': fp['row_arc_deg'],
                    'sweep_arc_left_deg': fp['arc_left_deg'],
                    'sweep_arc_right_deg': fp['arc_right_deg']})
    return out


#: Coverage factor weights. Every one is a modelling choice (`INFERRED`): nothing in the game
#: weighs a wide swing or a late turn against damage.
#:
#:     turn = s * turn_late_deg_locked + (1 - s) * turn_late_deg_unlocked
#:     arc_eff = min(360, sweep_row_arc_deg + 2 * min(turn, sat))
#:     coverage_factor = clamp((arc_eff / ref) ** e, lo, hi)
#:
#: with s = `COVER_TRACK_LOCKED_SHARE`, sat = `COVER_TRACK_SAT_DEG`, ref = `COVER_REF_ARC_DEG`,
#: e = `COVER_EXP`, (lo, hi) = `COVER_CLAMP`.
#: The turn is the one available in the last `TURN_LATE_FRAMES` real frames before the hit,
#: because turning before the defender commits to a dodge does not follow the dodge. It widens
#: the arc on both sides, because the attacker can rotate either way toward the target. It
#: saturates because a defender's dodge only displaces it so far in
#: azimuth: a roll of about 2.5 m at 3 m range is about 40 degrees. The reference is near the median
#: arc_eff of the tabled weapons' R1 slots (166 degrees, 1H and 2H), so a typical R1 scores 1.0.
COVER_TRACK_LOCKED_SHARE = 0.5
COVER_TRACK_SAT_DEG = 60.0
COVER_REF_ARC_DEG = 165.0
COVER_EXP = 0.5
COVER_CLAMP = (0.7, 1.4)


def coverage_factor(row):
    """{coverage_factor, coverage_arc_eff_deg, coverage_turn_deg} for one reach row, or None
    when the row has no footprint."""
    arc = row.get('sweep_row_arc_deg')
    if arc is None:
        return None
    share = COVER_TRACK_LOCKED_SHARE
    turn = (share * (row.get('turn_late_deg_locked') or 0.0)
            + (1 - share) * (row.get('turn_late_deg_unlocked') or 0.0))
    arc_eff = min(360.0, arc + 2 * min(turn, COVER_TRACK_SAT_DEG))
    f = min(max((arc_eff / COVER_REF_ARC_DEG) ** COVER_EXP, COVER_CLAMP[0]), COVER_CLAMP[1])
    return {'coverage_factor': round(f, 4), 'coverage_arc_eff_deg': round(arc_eff, 1),
            'coverage_turn_deg': round(turn, 1)}


# --------------------------------------------------------------------------- public API

_RC = None


def reach_profile(weapon, grip='both', regulation=None):
    """Per-attack reach/tracking/speed rows for one weapon (id or name). For the PvP ranking.

    Each row carries `weapon_reach_m` (grip to farthest hit point), `first_hit_frame_clip` and
    `first_hit_frame_real`, `turn_budget_deg_locked/unlocked` (degrees the attacker can turn
    between animation start and the first hit), `speed_windows`, and, when the pose decoder is
    present, `world_reach_m` (forward distance of the farthest hit point from the start position).
    """
    global _RC
    if _RC is None or regulation is not None:
        _RC = Reach(regulation)
    wid = _RC.reg.find_weapon(weapon)
    return weapon_reach(_RC, wid, grip)


#: The columns `reach_summary` and `skill_reach` keep.
SUMMARY_KEEP = ('weapon_reach_m', 'world_reach_m', 'root_motion_to_hit_m', 'first_hit_frame_clip',
                'target_centre_m', 'contact_centre_m',
                'first_hit_frame_real', 'front_contact_frame_real',
                'turn_budget_deg_locked', 'turn_budget_deg_unlocked',
                'turn_live_deg_locked', 'turn_live_deg_unlocked', 'default_turn_frames_unlocked',
                'turn_late_deg_locked', 'turn_late_deg_unlocked', 'turn_lockin_frames_locked',
                'turn_lockin_frames_unlocked',
                'sweep_area_m2', 'sweep_mean_width_m', 'sweep_arc_deg', 'sweep_row_arc_deg',
                'swing_shape', 'hit_height_min_m', 'hit_height_max_m',
                'coverage_factor', 'coverage_arc_eff_deg', 'coverage_turn_deg')
_SKILL = {}


def skill_reach(weapon, category, anim, judge):
    """The `reach_summary` columns of one skill animation on one weapon, or None.

    `category` is the skill TimeAct (`600 + SwordArtsParam.swordArtsTypeNew`), `anim` the
    animation holding the skill's first hit and `judge` that hit's behavior judge (resolved through
    the weapon's behaviorVariationId, like a slot's). The hit shapes, weapon dummies, pose and
    footprint are read exactly as for an attack slot (`attack_reach` with `clip`), so a skill gets
    the same reach and coverage a slot does. The clip is measured from its own first frame: for a
    hit in a follow-up animation (Stamp's 040010) the lead-in's movement is not added (`INFERRED`
    hand-over). Cached per (weapon, category, anim)."""
    global _RC
    if _RC is None:
        _RC = Reach()
    wid = _RC.reg.find_weapon(weapon)
    key = (wid, category, anim)
    if key not in _SKILL:
        row = attack_reach(_RC, wid, f'skill_a{category}_{anim:06d}', 'skill', judge, anim, 'one',
                           clip=(category, anim))
        _SKILL[key] = {k: row.get(k) for k in SUMMARY_KEEP + ('window_contacts',)} if row else None
    return _SKILL[key]


def reach_summary(weapon, grip='both', regulation=None):
    """{slot: {weapon_reach_m, world_reach_m, first_hit_frame_real, turn budgets, sweep
    columns, coverage_factor, ...}} (the keys in `SUMMARY_KEEP`; absent columns are None)."""
    keep = SUMMARY_KEEP
    if regulation is not None:
        return {r['slot']: {k: r.get(k) for k in keep}
                for r in reach_profile(weapon, grip, regulation)}
    key = (weapon, grip)
    if key not in _SUMMARY:
        rows = reach_profile(weapon, grip)
        _SUMMARY[key] = {r['slot']: {k: r.get(k) for k in keep} for r in rows}
        _remember_contacts(key, rows)
    return _SUMMARY[key]


def _remember_contacts(key, rows):
    # Only the latest weapon's samples are kept: a whole ranking's would be gigabytes, and the
    # class medians load peer weapons that never ask for theirs.
    _CONTACTS.clear()
    _CONTACTS[key] = {r['slot']: r.get('window_contacts') or {} for r in rows}


def slot_contacts(weapon, grip='both'):
    """{slot: `window_contacts`} of one weapon and grip: every damaging hit window's sampled hit
    spheres, in real seconds from the slot clip's start (`window_points`). The reaction dodge of
    `er-mechanics-ashes.reaction_outcome` reads them. Only the most recent weapon is kept, so a
    weapon read earlier is measured again (about 0.4 s)."""
    key = (weapon, grip)
    if key not in _CONTACTS:
        _remember_contacts(key, reach_profile(weapon, grip))
    return _CONTACTS[key]


_SUMMARY = {}
_CONTACTS = {}
#: Fewest measured values a class-median tier needs before it is used.
CLASS_FALLBACK_MIN = 3


def _median(values):
    v = sorted(values)
    n = len(v)
    return None if not n else (v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2)


def _class_peers(wid):
    """Base ids (affinity 0) of the named weapons sharing `wid`'s `EquipParamWeapon.wepType`."""
    reg = _RC.reg
    kind = reg.weapon[wid]['wepType']
    return sorted(i for i, w in reg.weapon.items()
                  if w['wepType'] == kind and i % 10000 == 0 and reg.weapon_names.get(i)
                  and not reg.weapon_names[i].startswith('['))


def _measured(row, distance):
    """(world reach, coverage factor, front-contact delay at `distance`) of one summary row;
    each None when that measurement is missing. The delay is the real frames from the first hit
    to the first contact with a defender `distance` m straight ahead."""
    fc = row.get('front_contact_frame_real') or {}
    at = fc.get(distance, fc.get(str(distance)))
    first = row.get('first_hit_frame_real')
    delay = at - first if at is not None and first is not None else None
    return row.get('world_reach_m'), row.get('coverage_factor'), delay


def class_fallback(weapon, grip='one', slot=None, distance=2.5):
    """Stand-in values for a slot whose pose measurement is missing, so that a missing value is
    neither rewarded nor punished against measured weapons (`INFERRED`, a modelling choice).

    {'world_reach_m', 'coverage_factor', 'front_contact_delay_real'}, each the median over the
    first tier with at least `CLASS_FALLBACK_MIN` measured values: the weapon's class
    (`EquipParamWeapon.wepType`) at the same slot and grip, the class at any slot of that grip,
    then `TABLE_WEAPONS` at the same slot, then `TABLE_WEAPONS` at any slot; `basis` names the tier
    each came from. A neutral 1.0 is not a stand-in: measured slots have a median reach factor of
    1.18, a median coverage of 0.98, and first hit to 2.5 m contact takes a median 1.5 frames
    (mean 4.5) - so a first-hit strike frame always wins the exchange earlier than it would."""
    global _RC
    if _RC is None:
        _RC = Reach()
    wid = _RC.reg.find_weapon(weapon)
    tiers = [('class ' + str(_RC.reg.weapon[wid]['wepType']), _class_peers(wid)),
             ('reference weapons', [_RC.reg.find_weapon(n) for n in TABLE_WEAPONS])]
    out, basis = {}, {}
    names = ('world_reach_m', 'coverage_factor', 'front_contact_delay_real')
    for label, peers in tiers:
        rows = [(k, r) for p in peers for k, r in reach_summary(p, grip).items()]
        for same_slot in (True, False):
            pool = [_measured(r, distance) for k, r in rows if not same_slot or k == slot]
            for i, name in enumerate(names):
                if name in out:
                    continue
                vals = [m[i] for m in pool if m[i] is not None]
                if len(vals) >= CLASS_FALLBACK_MIN:
                    out[name] = _median(vals)
                    basis[name] = f"{label}{' same slot' if same_slot else ' any slot'} median n={len(vals)}"
        if len(out) == len(names):
            break
    return {**out, 'basis': basis, 'label': 'INFERRED'}


# --------------------------------------------------------------------------- output

def _dash(v):
    return '-' if v is None else v


def print_weapon(rc, wid, rows):
    name = rc.reg.weapon_names.get(wid)
    print(f"{name} ({wid}) model wp_a_{rc.model.get(wid, 0):04d}")
    print(f"{'slot':18} {'reach':>5} {'world':>5} {'stand':>5} {'tgt':>5} {'cont':>5} {'fwd':>5} {'hitC':>5} {'hitR':>5} "
          f"{'turnL':>6} {'turnU':>6} {'free':>4} {'arcR':>5} {'wid':>5} {'swing':>6} {'cov':>5}"
          f"  speed / shapes")
    for r in rows:
        shapes = '; '.join(
            f"{'' if h['damaging'] else '~'}{'*' if h['state_info'] else ''}"
            f"j{h['judge']} {h['shape'][0]}{'-'.join(map(str, h['dmy']))} r{h['radius']}"
            f" {h['part']}->{_dash(h.get('extent_m'))}" for h in r['hitboxes'])
        speed = ' '.join(f"x{a}@{s}-{e}" for s, e, a, _ in r.get('speed_windows', []))
        print(f"{r['label']:18} {_dash(r['weapon_reach_m']):>5} {_dash(r.get('world_reach_m')):>5} "
              f"{_dash(r.get('standing_reach_m')):>5} {_dash(r.get('target_centre_m')):>5} "
              f"{_dash(r.get('contact_centre_m')):>5} {_dash(r.get('root_motion_to_hit_m')):>5} "
              f"{_dash(r.get('first_hit_frame_clip')):>5} {_dash(r.get('first_hit_frame_real')):>5} "
              f"{_dash(r.get('turn_budget_deg_locked')):>6} {_dash(r.get('turn_budget_deg_unlocked')):>6} "
              f"{_dash(r.get('default_turn_frames_locked')):>4} "
              f"{_dash(r.get('sweep_row_arc_deg')):>5} {_dash(r.get('sweep_mean_width_m')):>5} "
              f"{_dash(r.get('swing_shape')):>6} {_dash(r.get('coverage_factor')):>5}"
              f"  {speed}  {shapes}")
    print('reach = grip to farthest hit point (m); world = farthest hit point forward of the start '
          'position; stand = the same from where the body is at that instant; fwd = root motion '
          'to the first hit; tgt = world + the idle defender\'s front hurtbox radius, the farthest '
          'defender centre if the reach point met its most forward body point (defender facing '
          'the attacker); cont = the same with each hit point\'s height and lateral offset kept; '
          'hitC/hitR = first hit frame in clip / '
          'real 30 fps frames; turnL/turnU = degrees of TAE turn before the first hit, locked on '
          '/ not; free = real frames before the hit with neither a turn event nor Disable Turning. '
          'arcR = azimuth (deg) the hit footprint covers at one forward distance, averaged over '
          'its rows; wid = its mean width (m); swing = the far end\'s dominant motion (sweep, '
          'slam, thrust); cov = coverage factor (arc plus turn, INFERRED weights). '
          'Shapes: c capsule, s sphere, ~ no damage, * gated on a SpEffect state (both left out '
          'of reach).')


def print_table(rc):
    head = ['weapon'] + TABLE_SLOTS
    print('\t'.join(head))
    for name in TABLE_WEAPONS:
        try:
            wid = rc.reg.find_weapon(name)
        except SystemExit:
            print(f'{name}\t(not found)')
            continue
        rows = {r['slot']: r for g in ('one', 'both') for r in weapon_reach(rc, wid, g)}
        cells = [name]
        for slot in TABLE_SLOTS:
            r = rows.get(slot)
            if r is None:
                cells.append('-')
                continue
            cells.append(f"{_dash(r['weapon_reach_m'])}/{_dash(r.get('world_reach_m'))}/"
                         f"{_dash(r.get('standing_reach_m'))} "
                         f"f{_dash(r.get('first_hit_frame_real'))} "
                         f"t{_dash(r.get('turn_budget_deg_locked'))}")
        print('\t'.join(cells))


# --------------------------------------------------------------------------- selftest

#: Executable facts checked against both images: (1.16.2 VA, 1.17.1 VA, what).
EXE_SITES = {
    'tae224': (0x14042c480, 0x14042c9d0),
    'set_turn_speed': (0x140406a60, 0x140406f90),
    'tae608': (0x140426420, 0x140426970),
    'calc_turn_speed': (0x1403c4090, 0x1403c40a0),
    'speed_latch': (0x14041d6ac, 0x14041dbec),
}
IMAGE_BASE = 0x140000000
#: `mov r15d, [rdx+0x40]; shr r15, 0xf; not r15b; and r15b, 1` inside the ChrCtrl update
#: FUN_1403cbff0 (1.16.2; 1.17.1 0x1403cc000). The byte is the may-rotate flag: it is also cleared
#: by SpEffect state 435, `disableMove`, a ladder and a throw, and when it is clear the function
#: stores a zero vector into `ChrCtrl.rotationUpdate` (`VERIFIED`, 1.16.2 decompile). That this
#: zero request is what the joint modifier then turns toward, so a Disable Turning window blocks
#: turning whatever the TAE 224 speed, is `INFERRED`: the modifier's read of `rotationUpdate`
#: was not traced.
JT7_READER = (0x1403cc10f, 0x1403cc11f)
JT7_READER_BYTES = bytes.fromhex('448b7a4049c1ef0f41f6d74180e701')
#: `mov dword [rcx+8], 720.0` in the `ChrCtrlJointModifier` constructor (1.16.2 0x1403c3c50,
#: 1.17.1 0x1403c3c60): `turnVelocity`. The only store of that immediate to +8 in either image.
TURN_VELOCITY_STORE = (0x1403c3c6a, 0x1403c3c7a)
TURN_VELOCITY_STORE_BYTES = bytes.fromhex('c7410800003444')


def _image(path, va, size):
    with open(path, 'rb') as handle:
        handle.seek(va - IMAGE_BASE)
        return handle.read(size)


def _rel32_target(path, va):
    """Target of the `call`/`jmp rel32` at va."""
    b = _image(path, va, 5)
    return va + 5 + struct.unpack_from('<i', b, 1)[0]


def _find(path, va, size, pattern):
    """Offset of a regex byte pattern in [va, va + size), or -1."""
    m = re.search(pattern, _image(path, va, size), re.S)
    return m.start() if m else -1


def _exe_checks(check, skips):
    for build, path, idx in (('1.16.2', DEOBF_1162, 0), ('1.17.1', DEOBF_1171, 1)):
        if not os.path.exists(path):
            skips.append(f'EXE {build}: {path} absent')
            continue
        src = f'EXE {os.path.basename(path)}'
        tae224, set_turn = EXE_SITES['tae224'][idx], EXE_SITES['set_turn_speed'][idx]
        # 224: `cmp byte [rbx+4], 1` (IsLockOnCheck), `movss xmm1, [rbx]`, then a call to
        # SetTurnSpeed with the priority byte `movzx r8d, byte [rbx+5]` or 0xff.
        body = _image(path, tae224, 0x60)
        check(f'{build} 224 checks IsLockOnCheck byte +4', b'\x80\x7b\x04\x01' in body, True, src)
        check(f'{build} 224 reads priority byte +5', b'\x44\x0f\xb6\x43\x05' in body, True, src)
        call_at = body.find(b'\xe8', body.find(b'\x41\xb0\xff'))
        check(f'{build} 224 calls SetTurnSpeed',
              call_at >= 0 and _rel32_target(path, tae224 + call_at) == set_turn, True, src)
        # 608: interpolate and tail-jump into a two-instruction setter of +0x15c4.
        h608 = EXE_SITES['tae608'][idx]
        body = _image(path, h608, 0x80)
        jmp_at = body.find(b'\xe9', 0x60)
        setter = _rel32_target(path, h608 + jmp_at) if jmp_at >= 0 else None
        check(f'{build} 608 setter writes behavior +0x15c4',
              setter and _image(path, setter, 9), b'\xf3\x0f\x11\x89\xc4\x15\x00\x00\xc3', src)
        latch = EXE_SITES['speed_latch'][idx]
        check(f'{build} latch +0x15c4 -> +0x15c0, reset 1.0', _image(path, latch, 0x16),
              b'\x8b\x83\xc4\x15\x00\x00\x89\x83\xc0\x15\x00\x00'
              b'\xc7\x83\xc4\x15\x00\x00\x00\x00\x80\x3f', src)
        # Update multiplies the graph dt by +0x15c0 (`mulss xmmN, [reg+0x15c0]`).
        check(f'{build} behavior Update multiplies by +0x15c0',
              _find(path, latch, 0x300, rb'\xf3(?:\x44)?\x0f\x59[\x80-\xbf]\xc0\x15\x00\x00') >= 0,
              True, src)
        # CalculateTurnSpeed ends with `mulss xmm0, [rip+X]`, X = 0.017453292.
        calc = EXE_SITES['calc_turn_speed'][idx]
        off = _find(path, calc, 0x90, rb'\xf3\x0f\x59\x05')
        const = None
        if off >= 0:
            disp = struct.unpack('<i', _image(path, calc + off + 4, 4))[0]
            const = struct.unpack('<f', _image(path, calc + off + 8 + disp, 4))[0]
        check(f'{build} turn speed deg->rad constant', const and round(const, 7),
              round(math.pi / 180, 7), src)
        # JumpTable 7 case: `or qword [rbx+0x40], 0x8000`.
        table = 0x140428650 if idx == 0 else 0x140428650 + 0x550
        case7 = IMAGE_BASE + struct.unpack('<I', _image(path, table + 4 * 6, 4))[0]
        check(f'{build} JumpTable 7 sets actionModifiersFlags 0x8000', _image(path, case7, 8),
              b'\x48\x81\x4b\x40\x00\x80\x00\x00', src)
        # The reader: ChrCtrl's update loads actionModifiersFlags and keeps !(bit 15) as the
        # may-rotate flag; with it clear the rotation request is zeroed.
        check(f'{build} ChrCtrl reads actionModifiersFlags bit 15 as may-rotate',
              _image(path, JT7_READER[idx], len(JT7_READER_BYTES)), JT7_READER_BYTES, src)
        store = _image(path, TURN_VELOCITY_STORE[idx], len(TURN_VELOCITY_STORE_BYTES))
        check(f'{build} joint modifier turnVelocity = {DEFAULT_TURN_DEG_PER_S}',
              store == TURN_VELOCITY_STORE_BYTES
              and struct.unpack('<f', store[3:])[0] == DEFAULT_TURN_DEG_PER_S, True, src)


def _smithbox_enum(name):
    path = os.path.join(SMITHBOX_ER, 'Param Enums', f'{name}.json')
    with open(path, encoding='utf-8') as handle:
        data = json.load(handle)
    return {int(o['Key']): o['Names'][0]['Text'] for o in data['Options']}


def selftest():
    failures, passes, skips = [], [], []

    def check(name, got, want, source):
        (passes if got == want else failures).append(f'{name}: got {got!r} want {want!r} [{source}]')

    rc = Reach()

    # 1. Shape and part meaning, from Smithbox (authored apart from this tool).
    try:
        parts = _smithbox_enum('ATK_PARAM_HIT_TYPE')
        src = 'COMMUNITY: Smithbox Param Enums ATK_PARAM_HIT_TYPE'
        check('hitType 0 is the tip', parts.get(0), 'Normal (tip)', src)
        check('hitType 2 is the root', parts.get(2), 'Root', src)
        check('hitSourceType 0 is the weapon', _smithbox_enum('ATK_PARAM_HIT_SOURCE').get(0),
              'Weapon', 'COMMUNITY: Smithbox ATK_PARAM_HIT_SOURCE')
        with open(os.path.join(SMITHBOX_ER, 'Param Annotations', 'English', 'ATK_PARAM_ST.json'),
                  encoding='utf-8') as handle:
            note = handle.read()
        check('DmyPoly2 -1 makes a sphere (annotation)', '-1 makes it a sphere' in note, True,
              'COMMUNITY: Smithbox Param Annotations ATK_PARAM_ST')
    except OSError as err:
        skips.append(f'Smithbox enums: {err}')
    uchi = rc.atk_shape[900000]
    check('Uchigatana R1 hit1 (120, -1) reads as a sphere',
          [s['shape'] for s in hit_shapes(uchi)], ['capsule', 'sphere'], 'regulation AtkParam_Pc 900000')

    # 2. FLVER dummies against the same file's header bounding box (independent field).
    path = weapon_flver_path(612)
    if path is None:
        skips.append('FLVER: ' + PARTS_ROOT + ' has no wp_a_0612')
    else:
        fl = read_flver(path)
        lo, hi = fl['bbox']
        inside = all(lo[i] - 0.05 <= d['pos'][i] <= hi[i] + 0.05 for d in fl['dummies']
                     for i in range(3))
        check('Greatsword dummies inside the header bbox', inside, True, 'FLVER header bbox')
        dummies, bad = dummy_positions(fl)
        check('Greatsword weapon bones identity', bad, [], 'FLVER bone table')
        tip = dummies[100][0]
        check('Greatsword dummy 100 within 0.15 m of the blade end (bbox min Y)',
              abs(tip[1] - lo[1]) < 0.15, True, 'FLVER header bbox')
        order = [s['id'] for s in fl['dummies']]
        check('every hit dummy 100/110/120 present', {100, 110, 120} <= set(order), True,
              'FLVER dummy table')

    # 2b. A dummy under a non-identity parent bone: the Shotel's parent is a half turn about Y.
    # Carried through the bone its dummies fall inside the header bbox; read raw, three do not.
    path = weapon_flver_path(402)
    if path is None:
        skips.append('FLVER: ' + PARTS_ROOT + ' has no wp_a_0402')
    else:
        fl = read_flver(path)
        lo, hi = fl['bbox']

        def inside(p):
            return all(lo[i] - 0.01 <= p[i] <= hi[i] + 0.01 for i in range(3))
        placed = [p for ps in dummy_positions(fl)[0].values() for p in ps]
        check('Shotel dummies carried through their parent bone lie inside the bbox',
              (len(placed), all(inside(p) for p in placed)), (len(fl['dummies']), True),
              'FLVER header bbox (independent field)')
        check('Shotel dummies read raw: 3 fall outside the bbox',
              sum(not inside(d['pos']) for d in fl['dummies']), 3, 'FLVER header bbox')

    # 2c. Body dummies: c0000.flver's bone chain lands R_Foot / R_Knee on the HKX bind pose, and
    # Dane's Footwork's kick capsules resolve on them and get a world reach.
    body = body_dummies()
    pose = _pose_module()
    if not body or pose is None:
        skips.append('body dummies: c0000.flver or the pose decoder absent')
    else:
        bind = _hkx_bind(pose)
        check('c0000 dummy 5 follows R_Foot, 930 R_Knee', (body[5][0][1], body[930][0][1]),
              ('R_Foot', 'R_Knee'), 'c0000.flver AttachBoneIndex')
        check('c0000.flver R_Foot bind = HKX bind within 1 mm',
              max(abs(body[5][0][2][i] - bind['R_Foot'][0][i]) for i in range(3)) < 1e-3, True,
              'c0000.flver bone chain vs Skeleton.hkx referencePose (independent files)')
        dane = {r['slot']: r for r in weapon_reach(rc, rc.reg.find_weapon("Dane's Footwork"), 'one')}
        r1 = dane.get('r1_1') or {}
        check("Dane's Footwork R1 #1 kick has a world reach and a 2.5 m front contact",
              (r1.get('world_reach_m') is not None,
               2.5 in (r1.get('front_contact_frame_real') or {})), (True, True),
              'MEASURED a246_030000 + c0000 dummies 5/930/240')
        claw = weapon_attached(1631)
        if claw:
            p, bone, fbind = claw[120][0]
            check('Beast Claw (wp_a_1631) dummy 120 rides R_Hand, whose rest = HKX bind within 1 mm',
                  (bone, max(abs(fbind[i] - bind['R_Hand'][0][i]) for i in range(3)) < 1e-3),
                  ('R_Hand', True), 'wp_a_1631.flver bone chain vs Skeleton.hkx (independent files)')
            bc = {r['slot']: r for r in weapon_reach(rc, rc.reg.find_weapon('Beast Claw'), 'one')}
            check('Beast Claw R1 #1, placed on R_Hand, touches a defender 2.5 m ahead',
                  2.5 in (bc['r1_1'].get('front_contact_frame_real') or {}), True,
                  'MEASURED a062_030000 (grip-space placement put the claw at floor level, 1 m aside)')
        whip = {r['slot']: r for r in weapon_reach(rc, rc.reg.find_weapon('Whip'), 'one')}
        check('Whip R1 #1 is left unposed (dummies on the whip model own bones)',
              (whip['r1_1'].get('world_reach_m'), 'own bones' in whip['r1_1'].get('world_reach_error', '')),
              (None, True), 'wp_a_1200.flver AttachBoneIndex -> Born* bones')

    # 3. TAE: event fields as named by the community template (authored apart from this tool).
    if os.path.exists(TAE_TEMPLATE_ER):
        with open(TAE_TEMPLATE_ER, encoding='utf-8') as handle:
            text = handle.read()
        src = 'COMMUNITY: WitchyBND TAE.Template.ER.xml'
        for eid, fields in ((224, ['TurnSpeed', 'IsLockOnCheck']),
                            (608, ['SpeedAtStart', 'SpeedAtEnd'])):
            i = text.find(f'<event id="{eid}"')
            block = text[i:text.find('</event>', i)]
            check(f'template {eid} fields', re.findall(r'name="(\w+)"', block)[1:3], fields, src)
        check('template JumpTable 7 name', '7: Disable Turning' in text, True, src)
    else:
        skips.append('TAE template absent')

    # 4. Known clip values, decoded straight out of a26.tae (the Greatsword's category).
    anims = ATK.tae_animations(26)
    if anims is None:
        skips.append('a26.tae absent')
    else:
        ev = anims[30000]
        w = speed_windows(ev)
        check('a026_030000 plays at 1.34 for clip frames 0-17',
              [(_frame(s), _frame(e), round(a, 2)) for s, e, a, _ in w], [(0.0, 17.0, 1.34)],
              'TAE a26.tae')
        # Real time of the first hit: 17 clip frames at 1.34 plus 5 at 1.0.
        check('a026_030000 hit at clip 22 is real frame 17.7',
              round(real_seconds(22 / TAE_FPS, w) * TAE_FPS, 1), round(17 / 1.34 + 5, 1),
              'arithmetic on the decoded window')
        t = turn_details(ev, 22 / TAE_FPS, w)
        check('a026_030000 turn windows', [(x['frames'], x['deg_per_s'], x['lock_on_only'])
                                           for x in t['turn_windows']],
              [((11.0, 14.0), 720.0, True), ((14.0, 15.0), 360.0, False),
               ((15.0, 16.0), 720.0, False)], 'TAE a26.tae')
    gs = next(r for r in reach_profile('Greatsword', 'one') if r['slot'] == 'r1_1')
    check('Greatsword R1 reach = |dummy 100| + 0.4',
          gs['weapon_reach_m'], round(1.749 + 0.4, 3), 'FLVER wp_a_0612 + AtkParam 400000')

    # 5. TAE entries that borrow another entry's events or clip (header bytes, not a table here).
    if ATK.tae_animations(263) is None:
        skips.append('a263.tae absent')
    else:
        src = 'TAE mini-header'
        check('a263_030000 imports its events from a137_030000', resolve_clip(263, 30000)[:2],
              (137, 30000), src)
        check('a026_030310 (crouch R1) plays the a026_030300 clip', hkx_source(26, 30310),
              (26, 30300), src)

    # 6. The weapon-frame constant, re-measured (these are the measurements it was taken from,
    #    so they guard against a decoder change rather than prove the attachment).
    pose = _pose_module()
    if pose is None or ATK.hkx_path(37, 30000) is None:
        skips.append('pose decoder or a037_030000.hkx absent')
    else:
        src = 'MEASURED scripts/er-hkx-pose.py'
        _, q = pose.bone_model_transforms(37, 30000, 20 / TAE_FPS, with_root_motion=False)['R_Weapon']
        blade = _qrot(q, tuple(c * s for c, s in zip((0.0, -1.0, 0.0), WEAPON_TO_BONE)))
        check('Lance thrust f20: blade within 15 deg of forward (-Z)',
              -blade[2] > math.cos(math.radians(15)), True, src)
        gs_pose = next(r for r in reach_profile('Greatsword', 'one') if r['slot'] == 'r1_1')
        check('Greatsword R1 world reach > standing reach > weapon reach',
              gs_pose.get('world_reach_m', 0) > gs_pose.get('standing_reach_m', 0)
              > gs_pose['weapon_reach_m'], True, src)

    # 7. Defender hurtbox: the ragdoll against the chr's own body map, and the contact geometry
    #    against a hand-solved case.
    synthetic = [('pole', (0.0, 0.0, 0.0), (0.0, 2.0, 0.0), 0.3)]
    got = contact_centre_distance((0.0, 1.0, -1.0), 0.2, synthetic)
    check('contact distance, vertical pole r0.3 vs sphere r0.2 one metre ahead',
          got and round(got, 6), 1.5, 'arithmetic: 1 + 0.2 + 0.3')
    hb = defender_hurtbox('idle')
    if pose is None or hb is None:
        skips.append('ragdoll: pose decoder or c0000.HKX absent')
    else:
        src = 'MEASURED c0000.HKX / c0000.hkxpwv'
        try:
            check('hurtbox bodies = hkxpwv body count', hb['bodies'],
                  pose.read_hkxpwv()['body_count'], src)
        except OSError as err:
            skips.append(f'hkxpwv: {err}')
        check('idle hurtbox: every direction 0.15..0.6 m',
              all(0.15 < hb[f'{k}_m'] < 0.6 for k in HURTBOX_DIRECTIONS), True, src)
        gc = next(r for r in reach_profile('Giant-Crusher', 'both') if r['slot'] == '2h_r1_1')
        check('Giant-Crusher 2H R1 #1: world <= contact <= world + front radius',
              gc['world_reach_m'] <= gc.get('contact_centre_m', 0) <= gc['target_centre_m'],
              True, src)
        # A point 2 m ahead touches a defender at 2 m; the same point after 3 m of root motion
        # is held back by the defender's body to 2 m - 0.8 m of root, 1 m short of it.
        probe = {'t': 0.0, 'radius': 0.2, 'points': [(0.0, 1.0, -2.0)]}
        check('front contact: a body in the path holds the root at dist - 2 * push radius',
              (list(front_contact_times([dict(probe, peak_forward=0.0)], (2.0,))),
               list(front_contact_times([dict(probe, peak_forward=3.0)], (2.0,)))),
              ([2.0], []), 'arithmetic on the idle profile')
        # The same probe through the per-window form a skill's per-hit landing reads: it agrees
        # with `front_contact_times`, and a defender pushed 1 m further back is out of reach.
        entries = window_points([dict(probe, peak_forward=0.0, win=0, judge=1)])[(0, 1)]
        check('window contact: the probe touches at 2 m and misses a defender pushed to 3 m',
              (window_contact_time(entries, lambda _t: 2.0), window_contact_time(entries, lambda _t: 3.0)),
              (0.0, None), 'arithmetic on the idle profile')
        gc2 = {r['slot']: r for r in reach_profile('Giant-Crusher', 'both')}
        check('Giant-Crusher 2H R2 #1 (4.7 m lunge) touches a defender at every front distance',
              sorted(gc2['2h_r2_1'].get('front_contact_frame_real', {})),
              list(FRONT_CONTACT_DISTANCES_M), src)
        check('Giant-Crusher 2H crouch R1 is its rolling R1 (IsUseStealthAttack), as in '
              'er-mechanics-attacks',
              (gc2['2h_crouch_r1']['anim'], gc2['2h_crouch_r1'].get('first_hit_frame_real')),
              ('a031_032300', gc2['2h_roll_r1'].get('first_hit_frame_real')),
              'c0000.hks IsUseStealthAttack')
        lion = skill_reach('Claymore', 600, 40000, 3000)
        check("skill_reach: Lion's Claw (a600 040000, judge 3000) on a Claymore is a slam that "
              'reaches past the blade by its leap',
              (lion['swing_shape'], lion['world_reach_m'] > 2 * lion['weapon_reach_m'],
               lion['coverage_factor'] is not None),
              ('slam', True, True), 'TAE a600 + hkx a600_040000, as a slot is read')
        flver = os.path.join(os.path.dirname(pose.RAGDOLL_HKX_PATH), 'c0000.flver')
        if os.path.exists(flver):
            lo, hi = read_flver(flver)['bbox']
            if lo[0] > hi[0]:
                skips.append('hurtbox inside the body mesh: c0000.flver has no mesh (header '
                             'bounding box unset, zero meshes)')
    # 8. Spatial coverage against hand-solved geometry.
    src = 'arithmetic'
    flat = {j: 0.3 for j in range(0, 40)}
    check('contact disc: sphere r0.2 level with a 0.3 m slice reaches 0.5 m',
          round(contact_disc_radius(1.0, 0.2, flat), 6), 0.5, src)
    fp = footprint([{'points': [(0.0, 1.0, -3.0)], 'radius': 0.2}], flat)
    check('footprint of one disc r0.5 has area pi r^2 within 2%',
          abs(fp['area_m2'] - math.pi * 0.25) / (math.pi * 0.25) < 0.02, True, src)
    half = math.degrees(math.asin(0.5 / 3.0))
    check('footprint arc of that disc 3 m ahead is +-asin(0.5/3) within 1 deg',
          abs(fp['arc_right_deg'] - half) < 1 and abs(fp['arc_left_deg'] + half) < 1, True, src)
    arc = [{'t': k / 10, 'judge': 1, 'win': 0, 'shape': 0, 'radius': 0.1,
            'root': (0.0, 0.0, 0.0, 0.0),
            'far': (-2 * math.sin(math.radians(9 * k)), 1.0, -2 * math.cos(math.radians(9 * k))),
            'points': []} for k in range(11)]
    sw = swing_shape(arc)
    check('a 90 deg level arc of radius 2 is a sweep of length pi',
          (sw['swing_shape'], round(sw['swing_horizontal_m'], 2), sw['swing_vertical_m']),
          ('sweep', round(math.pi, 2), 0.0), src)
    if anims is not None:
        t = turn_details(anims[30000], 22 / TAE_FPS, speed_windows(anims[30000]))
        # Unblocked 224 frames before the hit: f13-14 at 720 (lock-on only), f14-15 at 360,
        # f15-16 at 720, all inside the x1.34 window; f11-13 and f21-22 are under Disable
        # Turning. f16-17 (x1.34) and f17-21 have no 224 event and turn at the default rate,
        # which is also 720, so not locked on (f13-14 falls back to it) gives the same total.
        free = DEFAULT_TURN_DEG_PER_S * (1 / 1.34 + 4) / TAE_FPS
        want = round(1800 / TAE_FPS / 1.34 + free, 1)
        check('a026_030000 turn budget skips the Disable Turning frames',
              (t['turn_budget_deg_locked'], t['turn_budget_deg_unlocked']), (want, want),
              'TAE a26.tae + JT7_READER')

    # 9. The executable, both builds.
    _exe_checks(check, skips)

    for line in passes:
        print('PASS', line)
    for line in skips:
        print('SKIP', line)
    for line in failures:
        print('FAIL', line)
    print(f'{len(passes)} passed, {len(failures)} failed, {len(skips)} skipped')
    return 1 if failures else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('weapon', nargs='?', help='EquipParamWeapon id or name')
    ap.add_argument('--grip', choices=('one', 'both'), default='one')
    ap.add_argument('--regulation')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--table', action='store_true', help='the doc table, tab separated')
    ap.add_argument('--hurtbox', action='store_true',
                    help='the player hurtbox extents (idle and bind pose)')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    rc = Reach(a.regulation)
    if a.hurtbox:
        for name in ('idle', 'bind'):
            print(json.dumps(defender_hurtbox(name)))
        print(json.dumps({'push_capsule_radius_m': PUSH_CAPSULE_RADIUS,
                          'push_capsule_half_height_m': PUSH_CAPSULE_HALF_HEIGHT,
                          'note': 'movement capsule, not the damage hurtbox'}))
        return 0
    if a.table:
        print_table(rc)
        return 0
    if not a.weapon:
        ap.error('weapon required')
    wid = rc.reg.find_weapon(a.weapon)
    rows = weapon_reach(rc, wid, a.grip)
    if a.json:
        print(json.dumps({'weapon': wid, 'name': rc.reg.weapon_names.get(wid), 'attacks': rows},
                         indent=1, default=str))
    else:
        print_weapon(rc, wid, rows)
    return 0


if __name__ == '__main__':
    sys.exit(main())
