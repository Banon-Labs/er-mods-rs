#!/usr/bin/env python3
"""Jump attacks timed from the jump input: takeoff, air swing, landing, recovery and travel.

    python3 scripts/er-mechanics-jump.py --weapon Greatsword --weapon Giant-Crusher
    python3 scripts/er-mechanics-jump.py --selftest

Write-up: `docs/er-mechanics/moveset.md` section 6. Labels as the sibling docs use them:
`HKS` = the installed compiled `c0000.hks` read through `scripts/er-hks-disasm.py` (1.17.1
bytecode, its own debug line numbers), `BEHAVIOR` = the `c0000.behbnd` graph read through
`scripts/hkx-tagfile.py`, `TAE` = decoded TimeAct, `MEASURED` = computed here (root motion from
`scripts/er-hkx-pose.py`), `INFERRED` = a modelling choice or a reading whose consumer was not
traced.

The sequence, piece by piece:

1. Which jump (`HKS` `ExecJump`, lines 3584-3587): kind 2 `W_Jump_D` when `LocomotionState` is 1
   and `MoveSpeedIndex` is 2 (the sprint, the same index that picks the running attacks), kind 1
   `W_Jump_F` when `MoveSpeedLevel` > 0.6, else kind 0 `W_Jump_N`. The jump clip is the lower
   layer of that state (`BEHAVIOR`): N plays `a000_202000` with no stick and `a000_202010` with
   the stick forward, F `a000_202020`, D `a000_202030`.
2. The air attack (`HKS` `JumpCommonFunction`, lines 23560-23584): while SpEffect 140 is on the
   character (`env(1116, 140)`; 1116 is `GetSpEffectID`, the id the decompile's
   `GetSpEffectID(100280)` compiles to in `AttackRightHeavy2Start_onUpdate`) and
   `JumpAttackForm` is 0, an R1 / R2 / L1 request fires `Event_JumpNormalAttack_Add` and sets
   the form to 1 / 2 / 3. The jump clip puts SpEffect 140 on from frame 0 to 16-19 (`TAE`,
   type 66), and the request is ready only while its cancel id is open (attacks.md, section 4):
   JumpTable 4 (R1, R2) or 117 (L1), open from frame 6 in all four clips. So the earliest press
   is `takeoff` = frame 6 (`TAE`; that a press made earlier is held until then is `INFERRED`).
3. The swing (`BEHAVIOR`): the attack is the upper layer of the same state. Per hand and button
   a selector holds the air clip (`a<cat>_031030 / 031040 / 031050` for N / F / D; R2 0312x0,
   2H 0330x0 / 0332x0, powerstance 0345x0) and the landed clip (`031070`, `031270`, `033070`,
   `033270`, `034570`). The air clip's frame 0 is an airborne pose (pelvis 0.93 m, no takeoff
   crouch; the landed clip's frame 3 is a landing crouch, pelvis 0.51 m, `MEASURED`), so the
   swing starts at the press. On landing `JumpCommonFunction` sets `JumpAttack_Land` (N) or
   fires `W_Jump_Attack_Land_F` (F, D), both of which play the landed clip. The two clips are
   one swing authored twice: raise, swing on the same frames (`air_land_agreement`: 453 of 476
   pairs over every player TAE open their first hit within one frame, the rest within three,
   Claymore's a025 R2 air clip three frames early), and the air window runs on through the
   descent. The landed clip is taken to continue the air clip's clock (`INFERRED`: the
   selector's time handling was not decoded, and each state holds its own `hkbClipGenerator`).
   So every frame of the landed clip, recovery included, counts from the press; the first hit
   is `takeoff` + the air clip's first hit when that comes before the landing, else + the
   landed clip's.
4. Air phase (`MEASURED`): the jump clip carries the rise and fall as root motion with gravity
   off (JumpTable 27 on frames 0-24), and ends above the ground (N 0.45 m, F 0.24 m, D 0.48 m).
   The rest of the fall runs at the clip's last vertical and horizontal speed (`INFERRED`: the
   physics step after the clip was not read), which gives `landing`.
5. Travel and reach: horizontal travel is the jump clip's root motion (the air clip has none,
   `MEASURED`), then the landed clip's from the frame it takes over. The slot's world reach
   (`er-mechanics-reach`, measured on the landed clip from its own start) less the landed clip's
   root motion at the hit is the swing's reach from the body; adding the jump's travel to the hit
   gives the reach from where the jump started (`INFERRED`: air and landed swings are taken to
   have the same body-relative reach; the weapon bone of both follows the same raise-and-swing
   path, `a026` R_Weapon checked).

Not in the data here: the lower-body invulnerability the community template names for
JumpTable 132 (on for the whole jump clip); whether the air swing hits a standing target at its
height; the sprint's entry cost for the D jump (the running attacks' `SCORE_ENTRY_FRAMES` value
stands in, `INFERRED`).
"""

from __future__ import annotations

import argparse
import functools
import importlib.util
import math
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

_MODS: dict = {}


def _mod(name: str):
    if name not in _MODS:
        spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        _MODS[name] = mod
    return _MODS[name]


#: Jump kinds (`ExecJump`): the `a000` jump clip, and the offset from a landed clip to its air
#: clip (031070 - 40 = 031030 for N).
JUMP_KINDS = {
    "n": {"state": "Jump_N", "clip": 202010, "air_offset": 40, "label": "standing, stick forward"},
    "f": {"state": "Jump_F", "clip": 202020, "air_offset": 30, "label": "running"},
    "d": {"state": "Jump_D", "clip": 202030, "air_offset": 20, "label": "sprinting"},
}
#: The N jump with the stick at rest (`Jump_NN_CMSG`): no horizontal travel.
JUMP_N_STILL_CLIP = 202000
#: SpEffect the jump clip holds while an air attack may start (`JumpCommonFunction`).
AIR_ATTACK_SPEFFECT = 140
#: Cancel ids that make the attack request ready: JumpTable 4 `Cancel - RH Attack`, 117
#: `Cancel - L1 Attack` (template names `COMMUNITY`, the R1/R2 reading is attacks.md section 4).
AIR_ATTACK_CANCEL = {"right": 4, "l1": 117}
#: JumpTable 27 `Disable Gravity` (template name, `COMMUNITY`).
DISABLE_GRAVITY_JT = 27
TAE_ADD_SPEFFECT, TAE_JUMP_TABLE = 66, 0
JUMP_TABLE_STATE_GATE_OFFSET = 0xE
#: Landed clips of the jump slots and the slot keys that play them.
LAND_CLIPS = {31070: "jump_r1", 31270: "jump_r2", 33070: "jump_r1", 33270: "jump_r2", 34570: "dual_jump"}
#: The slot a sprint-entered jump borrows its entry cost from (`er-builds-pvp.SCORE_ENTRY_FRAMES`).
SPRINT_ENTRY_ALIAS = "run_r1"
FPS = 30


def _atk():
    return _mod("er-mechanics-attacks")


# --------------------------------------------------------------------------------------------
# the jump clip


@functools.lru_cache(maxsize=None)
def _jump_events(clip: int):
    _, _, events = _atk().resolve_events(0, clip)
    if events is None:
        raise SystemExit(f"no a000_{clip:06d} in the player TAE ({_atk().PLAYER_TAE_DIR})")
    return events


def _windows(events, kind: int, value: int) -> list[tuple[float, float]]:
    """Real-frame windows of TAE events of `kind` whose first s32 is `value` (ungated only)."""
    to_real = _atk().clip_to_real(events)
    out = []
    for e in events:
        if e.type != kind or len(e.params) < 4 or struct.unpack_from("<i", e.params, 0)[0] != value:
            continue
        if kind == TAE_JUMP_TABLE and struct.unpack_from("<H", e.params, JUMP_TABLE_STATE_GATE_OFFSET)[0]:
            continue
        out.append((to_real(e.start) * FPS, to_real(e.end) * FPS))
    return out


def takeoff(kind: str, hand: str = "right") -> dict:
    """{'press', 'window_end'}: the first frame after the jump input where SpEffect 140 and the
    attack's cancel id overlap in the jump clip, and the last frame SpEffect 140 allows."""
    events = _jump_events(JUMP_KINDS[kind]["clip"])
    spe = _windows(events, TAE_ADD_SPEFFECT, AIR_ATTACK_SPEFFECT)
    can = _windows(events, TAE_JUMP_TABLE, AIR_ATTACK_CANCEL[hand])
    starts = [max(a[0], b[0]) for a in spe for b in can if max(a[0], b[0]) < min(a[1], b[1])]
    if not starts:
        return {"press": None, "window_end": None}
    press = min(starts)
    return {"press": round(press, 1), "window_end": round(max(e for s, e in spe if s <= press), 1)}


@functools.lru_cache(maxsize=None)
def jump_path(clip: int) -> dict:
    """The jump clip's root motion per frame and the landing frame (module docstring item 4).

    {'forward': [m per frame], 'up': [m per frame], 'frames': clip length, 'landing': frame,
    'v_forward': m/frame after the clip, 'gravity_off': last frame of JumpTable 27}"""
    pose = _mod("er-hkx-pose")
    anim = pose.load_animation(0, clip)
    length = anim.duration * FPS
    n = int(math.floor(length + 1e-6))
    fwd, up = [], []
    for k in range(n + 1):
        x, y, z, _ = pose.root_motion(0, clip, min(k / FPS, anim.duration))
        fwd.append(-z)
        up.append(y)
    landing = next((k for k in range(1, n + 1) if up[k] <= 0.0 < max(up[:k] or [0.0])), None)
    v_fwd = fwd[-1] - fwd[-2]
    if landing is None:
        v_up = up[-1] - up[-2]
        landing = n + (up[-1] / -v_up if v_up < 0 else float("inf"))
    grav = _windows(_jump_events(clip), TAE_JUMP_TABLE, DISABLE_GRAVITY_JT)
    return {"forward": fwd, "up": up, "frames": length, "landing": round(landing, 1), "v_forward": v_fwd,
            "gravity_off": round(max((e for _, e in grav), default=0.0), 1)}


def jump_travel(clip: int, frame: float) -> float:
    """Forward root travel of the jump at `frame` after the input, carried on at the clip's last
    speed after it ends (`INFERRED`), stopping at the landing."""
    p = jump_path(clip)
    f = min(max(frame, 0.0), p["landing"])
    n = len(p["forward"]) - 1
    if f <= n:
        k = int(math.floor(f))
        k2 = min(k + 1, n)
        return p["forward"][k] + (p["forward"][k2] - p["forward"][k]) * (f - k)
    return p["forward"][n] + p["v_forward"] * (f - n)


# --------------------------------------------------------------------------------------------
# the attack clip


def parse_anim(anim: str) -> tuple[int, int]:
    """'a026_031070' -> (26, 31070)."""
    cat, num = anim.lstrip("a").split("_")
    return int(cat), int(num)


@functools.lru_cache(maxsize=None)
def land_root(anim: str) -> dict | None:
    """{real frame: forward root motion} sampler of a landed clip, or None when its HKX is not
    unpacked. Real frames go through the clip's TAE 608 speed (`clip_to_real`)."""
    atk, pose = _atk(), _mod("er-hkx-pose")
    cat, num = parse_anim(anim)
    _, _, events = atk.resolve_events(cat, num)
    src = atk._reach().hkx_source(cat, num)
    try:
        a = pose.load_animation(*src)
    except Exception:  # noqa: BLE001 -- an unpacked clip is optional input
        return None
    to_real = atk.clip_to_real(events or [])
    step = 1.0 / 120
    samples = []
    t = 0.0
    while t <= a.duration + 1e-9:
        samples.append((to_real(t) * FPS, -pose.root_motion(*src, t)[2]))
        t += step
    return {"samples": samples}


def land_forward(anim: str, frame: float) -> float:
    """The landed clip's forward root motion at real `frame` of its own clock (0 when unknown)."""
    lr = land_root(anim)
    if not lr or frame <= 0:
        return 0.0
    s = lr["samples"]
    for (f0, v0), (f1, v1) in zip(s, s[1:]):
        if f0 <= frame <= f1:
            return v0 if f1 == f0 else v0 + (v1 - v0) * (frame - f0) / (f1 - f0)
    return s[-1][1]


def air_anim(anim: str, kind: str) -> str:
    cat, num = parse_anim(anim)
    return f"a{cat:03d}_{num - JUMP_KINDS[kind]['air_offset']:06d}"


def _hit_judges(events) -> list[tuple[float, int]]:
    atk = _atk()
    return [(e.start, struct.unpack_from("<i", e.params, 8)[0]) for e in events or []
            if e.type == atk.TAE_ATTACK_BEHAVIOR
            and not struct.unpack_from("<H", e.params, atk.ATTACK_STATE_GATE_OFFSET)[0]]


@functools.lru_cache(maxsize=None)
def air_hit(anim: str, kind: str) -> float | None:
    """Real first-hit frame of the air clip that pairs with landed clip `anim`, counting only the
    judges the landed clip fires; None when the category has no such air clip. Claymore's R2 air
    clips (a025) open three frames before the landed one, most open on the same frame."""
    atk = _atk()
    cat, num = parse_anim(anim)
    _, _, land = atk.resolve_events(cat, num)
    judges = {j for _, j in _hit_judges(land)}
    _, _, air = atk.resolve_events(cat, num - JUMP_KINDS[kind]["air_offset"])
    starts = [t for t, j in _hit_judges(air) if j in judges]
    if not starts:
        return None
    return atk.real_frame(atk.clip_to_real(air)(min(starts)))


def sequence(slot: dict, kind: str, hand: str = "right", reach_is_proxy: bool = False) -> dict | None:
    """One jump attack from the jump input (module docstring). `slot` is an `er-builds-pvp` slot of
    `jump_r1`, `jump_r2` or `dual_jump` (fields `anim`, `startup`, `roll`, `next`, `reach`), whose
    frames are the landed clip's. `reach_is_proxy`: the slot's reach is not the landed clip's own
    (a powerstance slot's `r1_1 proxy`), so no landed-clip root motion is taken off it.

    Returns frames after the jump input: `press`, `first_hit`, `landing`, `recovery` (earliest of
    roll and next attack), `airborne` (first hit before the landing), `travel_at_hit` and
    `travel_total` (m), `reach` (m, from where the jump started) and `entry` (frames before the
    swing's own clock starts)."""
    if slot.get("startup") is None or not slot.get("anim"):
        return None
    t = takeoff(kind, hand)
    if t["press"] is None:
        return None
    clip = JUMP_KINDS[kind]["clip"]
    path = jump_path(clip)
    p, h, anim = t["press"], slot["startup"], slot["anim"]
    landing = path["landing"]
    # The air clip's own first hit when it comes before the landing (a slot's `startup` is the
    # landed clip's); otherwise the landed swing, on the shared clock.
    h_air = air_hit(anim, kind)
    airborne = h_air is not None and p + h_air < landing
    if airborne:
        h = h_air
    else:
        airborne = p + h < landing
    hit = p + h
    # Nothing cancels in the air: a landed-clip cancel that falls before the landing is held to it
    # (`INFERRED`: the air clips carry no roll or attack cancel id, the landing branch is the only
    # way out of the state). `held` is how far the landing pushed the recovery.
    ends = [f for f in (slot.get("roll"), slot.get("next")) if f]
    recovery = max(p + min(ends), landing) if ends else None
    held = max(0.0, landing - (p + min(ends))) if ends else 0.0
    # Travel: the jump's root motion until the landing, then the landed clip's own from the local
    # frame at which it takes over.
    land_local = max(landing - p, 0.0)
    if airborne:
        travel_hit = jump_travel(clip, hit)
    else:
        travel_hit = jump_travel(clip, landing) + land_forward(anim, h) - land_forward(anim, land_local)
    end = recovery if recovery is not None else hit
    travel_total = jump_travel(clip, landing) + max(0.0, land_forward(anim, end - p) - land_forward(anim, land_local))
    reach = slot.get("reach")
    if reach is not None:
        body = reach if reach_is_proxy else reach - land_forward(anim, h)
        reach = body + travel_hit
    return {"kind": kind, "press": p, "first_hit": round(hit, 1), "landing": landing,
            "recovery": None if recovery is None else round(recovery, 1), "airborne": airborne,
            "travel_at_hit": round(travel_hit, 3), "travel_total": round(travel_total, 3),
            "reach": None if reach is None else round(reach, 3), "entry": p, "held": round(held, 1)}


# --------------------------------------------------------------------------------------------
# slots for `er-mechanics-moveset`


def _jump_link(combo: dict, seq: dict) -> dict | None:
    """A cross-hand link of the landed-clip slot moved onto one jump (`jump_slots`), else None.

    The landed clip's clock starts at the press, so the link's `start` stays on it but is held to
    the landing (nothing cancels in the air, as for `roll` and `next`). The gap is counted from
    the hit the jump actually lands first (the air clip's when it comes first, `INFERRED`: the
    stagger it plays is the landed row's). Each side's verdict is re-read against its escape: true
    below it, tie at it, roll-out-able above it with the side's own `p_roll`."""
    if combo.get("start") is None or combo.get("first_hit") is None:
        return None
    floor = seq["landing"] - seq["press"]
    start = max(combo["start"], floor)
    shift = (start - combo["start"]) + (combo["first_hit"] - (seq["first_hit"] - seq["press"]))
    out = {**combo, "start": round(start, 1)}
    for side in ("on_break", "on_intact"):
        v = combo.get(side)
        if not v or v.get("verdict") not in ("true", "tie", "roll-out-able") or not v.get("escape"):
            continue
        gap = round(v["gap"] + shift, 1)
        verdict = "true" if gap < v["escape"] else "tie" if gap == v["escape"] else "roll-out-able"
        p = {"true": 1.0, "tie": 0.5}.get(verdict, v.get("p_roll", 0.0))
        if verdict != "true" and v.get("p_catch"):
            p += (1.0 - p) * v["p_catch"]  # roll-catch, as `er-mechanics-combo.paired_slots`
        out[side] = {**v, "gap": gap, "verdict": verdict, "p": p}
    return out


def jump_slots(slots: dict, entry_fn=None) -> dict:
    """{synthetic key: slot} for every jump slot in `slots` and every kind: `jump_r1_n`,
    `jump_r2_f`, `dual_jump_d`, ... Each is the landed-clip slot with `reach` replaced by the
    jump's (`sequence`), `startup` counted from the jump input, and `jump_entry` = the frames the
    moveset score charges before the swing's clock: the press frame, plus the sprint's entry for
    the D jump (`SPRINT_ENTRY_ALIAS` through `entry_fn`, `INFERRED`). `jump` keeps the sequence.
    A recovery the landing holds back (`held`) moves `roll` and `next` to the landing and takes
    the same frames off both frame advantages. Same-weapon follow-up links are dropped: the sweep
    measured them from the landed clip alone. A cross-hand link (`er-mechanics-combo.paired_slots`,
    the entries that carry `start` and `first_hit`) is kept and re-timed by `_jump_link`."""
    out = {}
    for base in ("jump_r1", "jump_r2", "dual_jump"):
        s = slots.get(base)
        if not s:
            continue
        hand = "l1" if base == "dual_jump" else "right"
        proxy = (s.get("reach_source") or "").endswith("proxy")
        for kind in JUMP_KINDS:
            seq = sequence(s, kind, hand, reach_is_proxy=proxy)
            if seq is None:
                continue
            entry = seq["entry"] + ((entry_fn(SPRINT_ENTRY_ALIAS) or 0.0) if kind == "d" and entry_fn else 0.0)
            floor = seq["landing"] - seq["press"]
            held = seq["held"]
            out[f"{base}_{kind}"] = {
                **s, "reach": seq["reach"] if seq["reach"] is not None else s.get("reach"),
                "startup": seq["first_hit"], "jump": seq, "jump_entry": entry,
                "combos": [c for c in (_jump_link(c, seq) for c in s.get("combos") or []) if c],
                **{k: (None if s.get(k) is None else max(s[k], floor)) for k in ("roll", "next")},
                **{k: (None if s.get(k) is None else s[k] - held) for k in ("adv", "adv_stagger")}}
    return out


# --------------------------------------------------------------------------------------------
# evidence checks


def air_land_agreement() -> dict:
    """Over every player TAE: the first-hit frame of each air clip against its landed clip
    (judges 150/160 1H, 350/360 2H, ungated events). {'pairs', 'max_diff', 'hist'}."""
    import glob
    import os
    atk = _atk()
    cats = sorted(int(os.path.basename(p)[1:-4]) for p in glob.glob(os.path.join(atk.PLAYER_TAE_DIR, "a*.tae")))
    hist, pairs = {}, 0
    for cat in cats:
        an = atk.tae_animations(cat) or {}
        for land, judge in ((31070, 150), (31270, 160), (33070, 350), (33270, 360)):
            if land not in an:
                continue

            def first(num):
                _, _, ev = atk.resolve_events(cat, num)
                s = [e.start for e in ev or [] if e.type == atk.TAE_ATTACK_BEHAVIOR
                     and struct.unpack_from("<i", e.params, 8)[0] == judge
                     and not struct.unpack_from("<H", e.params, atk.ATTACK_STATE_GATE_OFFSET)[0]]
                return round(min(s) * FPS) if s else None
            lf = first(land)
            for kind in JUMP_KINDS:
                num = land - JUMP_KINDS[kind]["air_offset"]
                if num not in an or lf is None:
                    continue
                af = first(num)
                if af is None:
                    continue
                d = af - lf
                hist[d] = hist.get(d, 0) + 1
                pairs += 1
    return {"pairs": pairs, "max_diff": max((abs(d) for d in hist), default=None), "hist": dict(sorted(hist.items()))}


def hks_checks() -> list[tuple[str, bool]]:
    """The HKS facts the model rests on, read from the installed bytecode."""
    h = _mod("er-hks-disasm")
    main = h.parse_file(h.DEFAULT_FILE)[3]
    h.name_functions(main)
    funcs = {f.name: f for f in h.walk(main)}
    out = []

    def consts(name):
        f = funcs.get(name)
        return [] if f is None else [c for c in f.consts]

    jc = consts("JumpCommonFunction")
    out.append(("JumpCommonFunction reads SpEffect 140 and fires Event_JumpNormalAttack_Add",
                140 in jc and 1116 in jc and "Event_JumpNormalAttack_Add" in jc))
    out.append(("JumpCommonFunction sets JumpAttack_Land and fires W_Jump_Attack_Land_F on landing",
                "JumpAttack_Land" in jc and "W_Jump_Attack_Land_F" in jc))
    ej = consts("ExecJump")
    out.append(("ExecJump picks W_Jump_N/F/D on MoveSpeedLevel 0.6 and MoveSpeedIndex",
                all(x in ej for x in ("W_Jump_N", "W_Jump_F", "W_Jump_D", "MoveSpeedLevel", "MoveSpeedIndex"))
                and any(isinstance(c, float) and abs(c - 0.6) < 1e-6 for c in ej)))
    hv = consts("AttackRightHeavy2Start_onUpdate")
    out.append(("env 1116 is GetSpEffectID: AttackRightHeavy2Start_onUpdate asks it for 100280",
                1116 in hv and 100280 in hv))
    return out


def behavior_checks() -> list[tuple[str, bool]]:
    """`Jump_N` holds the jump clip and both attack clips as layers; one `a026_031070`
    generator. Skipped (empty) when the unpacked behavior graph is absent."""
    import collections
    import bisect
    import os
    path = os.path.expanduser("~/er-extract/LOOK_HERE_WITCHY_RECURSIVE_20260713/sharded/chr/"
                              "c0000-behbnd-dcx/Behaviors/c0000.hkx")
    path = os.environ.get("ER_BEHBND_HKX", path)
    if not os.path.exists(path):
        return []
    tf = _mod("hkx-tagfile").Tagfile(open(path, "rb").read())
    names = collections.Counter()
    for it in tf.find_items("hkbClipGenerator"):
        p = tf.ptch.get(it["off"] + 0x98)
        if p is not None:
            names[tf.cstr(p)] += 1
    byoff = sorted(tf.items, key=lambda x: x["off"])
    offs = [x["off"] for x in byoff]
    kids = collections.defaultdict(list)
    for doff, tgt in tf.ptch.items():
        i = bisect.bisect_right(offs, doff) - 1
        kids[byoff[i]["idx"]].append(tgt)

    def name(it, off):
        p = tf.ptch.get(it["off"] + off)
        if p is None or tf.tname(tf.items[p]["type"]) != "char":
            return None
        return tf.cstr(p)

    def clips(idx, seen):
        if idx in seen:
            return set()
        seen.add(idx)
        it = tf.items[idx]
        if tf.tname(it["type"]) == "hkbClipGenerator":
            return {name(it, 0x98)}
        out = set()
        for t in kids[idx]:
            out |= clips(t, seen)
        return out
    state = next((it for it in tf.items if tf.tname(it["type"]).endswith("StateInfo")
                  and name(it, 0x68) == "Jump_N"), None)
    under = clips(tf.ptch.get(state["off"] + 0x60), set()) if state else set()
    return [("Jump_N plays a000_202000, a000_202010, a026_031030 and a026_031070",
             {"a000_202000", "a000_202010", "a026_031030", "a026_031070"} <= under),
            ("a026_031070 has a generator in several states (no shared clock to read off)",
             names.get("a026_031070", 0) > 1)]


# --------------------------------------------------------------------------------------------
# report


def weapon_rows(weapon: str, level: int = 0) -> list[dict]:
    """Jump R1/R2 of both grips of one weapon, every kind, from `er-mechanics-attacks`."""
    atk = _atk()
    reg = atk.Regulation(None)
    wid = reg.find_weapon(weapon)
    rows = []
    for grip in ("one", "both"):
        for a in atk.weapon_attacks(reg, wid, grip, level):
            base = a["slot"].removeprefix("2h_")
            if base not in ("jump_r1", "jump_r2") or not a.get("hit_windows"):
                continue
            cancel = a.get("cancel_frame") or {}
            slot = {"anim": a["anim"], "startup": a["hit_windows"][0][0], "roll": cancel.get("dodge"),
                    "next": cancel.get(base[-2:]), "reach": None}
            for kind in JUMP_KINDS:
                seq = sequence(slot, kind)
                if seq is None:
                    continue
                hyper = [(h["frames"][0] + seq["press"], h["frames"][1] + seq["press"]) for h in a.get("hyperarmor") or []]
                rows.append({"weapon": weapon, "grip": grip, "slot": base, "kind": kind, "anim": a["anim"],
                             "clip_hit": slot["startup"], "roll": slot["roll"], "next": slot["next"],
                             "poise_damage": a.get("poise_damage"), "mv": a.get("mv_phys"),
                             "hyperarmor": hyper, **seq})
    return rows


def selftest() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok   " if cond else "FAIL ") + msg)
        ok = ok and bool(cond)

    for name, cond in hks_checks():
        check(cond, f"HKS: {name}")
    for name, cond in behavior_checks():
        check(cond, f"BEHAVIOR: {name}")
    t = {k: takeoff(k) for k in JUMP_KINDS}
    check(all(v["press"] == 6 for v in t.values()), f"TAE: the air attack request is ready from frame 6 in N/F/D ({t})")
    check(takeoff("n", "l1")["press"] == 6, "TAE: the L1 (powerstance) request too, through JumpTable 117")
    check(t["f"]["window_end"] == 19 and t["n"]["window_end"] == 17, "SpEffect 140 ends at 17 (N), 19 (F)")
    still = jump_path(JUMP_N_STILL_CLIP)
    check(abs(still["forward"][-1]) < 0.01 and max(still["up"]) > 1.0,
          f"a000_202000 rises {max(still['up']):.2f} m and does not move forward")
    fwd = {k: round(jump_path(v["clip"])["forward"][-1], 2) for k, v in JUMP_KINDS.items()}
    check(fwd["n"] < fwd["f"] < fwd["d"], f"jump clip travel grows N < F < D ({fwd})")
    ld = {k: jump_path(v["clip"])["landing"] for k, v in JUMP_KINDS.items()}
    check(all(20 < v < 30 for v in ld.values()), f"landing 20-30 frames after the input ({ld})")
    agree = air_land_agreement()
    near = sum(n for d, n in agree["hist"].items() if abs(d) <= 1)
    check(agree["pairs"] > 400 and agree["max_diff"] <= 3 and near >= 0.95 * agree["pairs"],
          f"TAE: air and landed first hits, {near} of {agree['pairs']} within one frame, all within "
          f"{agree['max_diff']} {agree['hist']}")
    check(air_hit("a025_031270", "f") is not None and air_hit("a025_031270", "f") < 20,
          f"Claymore-class (a025) R2 air clip hits before the landed one ({air_hit('a025_031270', 'f')})")
    # A slot with hit 16, roll 42: first hit 22 in the air, recovery 48, reach adds the travel.
    slot = {"anim": "a026_031070", "startup": 16, "roll": 42, "next": 35, "reach": 3.0}
    seq = sequence(slot, "f")
    check(seq["first_hit"] == 22 and seq["airborne"] and seq["recovery"] == 41,
          f"sequence: first hit = press + clip hit, recovery = press + earliest end ({seq})")
    check(seq["reach"] > slot["reach"] - land_forward("a026_031070", 16),
          "the running jump reaches farther than the swing alone")
    quick = {**slot, "roll": 14, "next": 12, "adv": -5}
    qs = sequence(quick, "n")
    qj = jump_slots({"jump_r1": quick})["jump_r1_n"]
    check(qs["recovery"] == qs["landing"] and abs(qj["next"] - (qs["landing"] - 6)) < 1e-9
          and abs(qj["adv"] - (-5 - qs["held"])) < 1e-9,
          f"a cancel before the landing is held to it ({qs['recovery']}, held {qs['held']})")
    js = jump_slots({"jump_r1": {**slot, "dmg": 500.0}}, lambda k: 20.0 if k == SPRINT_ENTRY_ALIAS else 0.0)
    check(set(js) == {"jump_r1_n", "jump_r1_f", "jump_r1_d"} and js["jump_r1_d"]["jump_entry"] == 26.0
          and js["jump_r1_f"]["jump_entry"] == 6.0, "jump_slots: one slot per kind, the sprint entry on D")
    rows = {(r["grip"], r["slot"], r["kind"]): r for r in weapon_rows("Greatsword")}
    gs = rows.get(("both", "jump_r1", "f"))
    check(gs and gs["first_hit"] == gs["clip_hit"] + 6 and gs["hyperarmor"],
          f"Greatsword 2H running jump R1 from the input: {gs and (gs['first_hit'], gs['recovery'], gs['travel_at_hit'])}")
    print("selftest", "passed" if ok else "FAILED")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--weapon", action="append", default=[])
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.weapon:
        ap.error("--weapon or --selftest")
    for k, v in JUMP_KINDS.items():
        p, t = jump_path(v["clip"]), takeoff(k)
        print(f"jump {k} ({v['label']}, a000_{v['clip']}): press from frame {t['press']} to {t['window_end']}, "
              f"landing {p['landing']}, travel {jump_travel(v['clip'], p['landing']):.2f} m, "
              f"peak {max(p['up']):.2f} m")
    print(f"  {'weapon':<18}{'grip':<5}{'slot':<8}{'kind':<5}{'hit':>5}{'first':>7}{'land':>6}{'recov':>7}"
          f"{'air':>5}{'m@hit':>7}{'m tot':>7}{'poise':>7}  hyperarmor (from jump input)")
    for w in a.weapon:
        for r in weapon_rows(w):
            print(f"  {w[:17]:<18}{'2H' if r['grip'] == 'both' else '1H':<5}{r['slot']:<8}{r['kind']:<5}"
                  f"{r['clip_hit']:>5}{r['first_hit']:>7}{r['landing']:>6}{r['recovery'] or '-':>7}"
                  f"{'y' if r['airborne'] else 'n':>5}{r['travel_at_hit']:>7.2f}{r['travel_total']:>7.2f}"
                  f"{r['poise_damage'] or 0:>7}  {r['hyperarmor']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
