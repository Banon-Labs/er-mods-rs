#!/usr/bin/env python3
"""Whether a player can run behind someone casting a skill and backstab them before they can roll.

    python3 scripts/er-mechanics-backstab.py Lance
    python3 scripts/er-mechanics-backstab.py Lance --skill "Charge Forth"
    python3 scripts/er-mechanics-backstab.py --selftest

# What a backstab needs (`VERIFIED`, 1.16.2 named dump)

`FUN_140485cf0` runs the backstab test for a victim when `throwState` is None, the victim is not
already in a pair animation (`FUN_1404efc40`), `IsImmuneToThrow` 0x1403f3d30 is false, and
`ValidateThrowForHKSSpEffects` 0x140485990 passes. Then `ThrowPoseChecks` 0x140485720 is called
with the backstab-attempt flag set, so the ThrowParam 10000000 `_start` limits apply:

* distance below `Dist_start` 1.7 m, height within `upperYRange_start` / `lowerYRange_start`;
* the victim within `diffAngMyToDef_start` 70 degrees of where the attacker faces;
* the two facings within `DiffAngMin_start`..`DiffAngMax_start`, -35..35 degrees.

`IsImmuneToThrow` is true while `actionModifiersFlags` bit 0x1, 0x4 or 0x8 is set (TAE JumpTable
94, 68 and 67 in `0ChrActionFlag` 0x1404275e0), at zero HP, or while
`ChrCtrlModifierData.allowedThrowDefType` is 0xff. That byte is HKS act 103
(`ctrlModifier+0x21`, `HksAct` 0x14040ccbc); in `c0000.hks` it is set to 255 only by the prayer,
item-dash and riding states and by `SetThrowDefInvalid` / `SetThrowInvalid` (throws and ladders),
never by an attack or skill state. `ValidateThrowForHKSSpEffects` refuses a victim carrying a
SpEffect with `throwCondition` 1 or 11..15. `skill_blocks_backstab` checks every clip of a skill
for those JumpTables and SpEffects; on the Lance's 43 skills (266 clips) there are none. So a
player is backstabbable through their own skill. That the defense byte is throwable by default
outside those states is `INFERRED`: its reset was not found, but an idle player can be backstabbed
and no idle or move state sets it.

# The race (`circle`)

The caster is locked on and turns toward the circler as fast as the skill's TimeAct lets it
(`er-mechanics-reach.turn_rate_at`: 0 under Disable Turning, else the 224 speed, else 720 deg/s),
and moves by the clip's own root motion. The circler starts `START_DISTANCE_M` in front, reacts
after `REACTION_S` (the ranking's median visual reaction plus one network delay), then runs at
`speed` to a point `TARGET_BEHIND_M` behind the caster, facing the caster. The backstab is open from
the first instant the `_start` limits hold until the caster's first roll frame
(`er-mechanics-ar-export.commitment`). The margin is that window in seconds; none when the circler
never gets there in time. Run speed is the locked-on run, 4.01 m/s (`neutral.md`); that it holds
sideways is `INFERRED`.
"""
import argparse
import importlib.util
import json
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _mod(name, fname):
    s = importlib.util.spec_from_file_location(name, os.path.join(HERE, fname))
    m = importlib.util.module_from_spec(s)
    sys.modules[name] = m
    s.loader.exec_module(m)
    return m


A = _mod('er_mechanics_ashes', 'er-mechanics-ashes.py')
R = _mod('er_mechanics_reach', 'er-mechanics-reach.py')
EXPORT = _mod('er_mechanics_ar_export', 'er-mechanics-ar-export.py')

BACKSTAB_THROW_ROW = 10000000
#: TAE JumpTable ids whose `actionModifiersFlags` bit makes `IsImmuneToThrow` true (0x1404284d9
#: bit 0x1, 0x140428288 bit 0x4, 0x14042827e bit 0x8; ids from the table at 0x140428650).
THROW_IMMUNE_JUMP_TABLES = (94, 68, 67)
#: `throwCondition` values on the victim that `ValidateThrowForHKSSpEffects` refuses a backstab for.
THROW_BLOCKING_CONDITIONS = (1, 11, 12, 13, 14, 15)
RUN_SPEED = 4.01
SPRINT_SPEED = 6.04
#: Where the circler stands when the skill starts, metres in front of the caster (`INFERRED`: a
#: close-range miss, inside backstab range).
START_DISTANCE_M = 1.5
#: Where the circler runs to, metres behind the caster (`INFERRED`: well inside `Dist_start`).
TARGET_BEHIND_M = 1.0
REACTION_S = A.REACTION_MEDIAN_S + A.NETWORK_ONE_WAY_S
STEP_S = 1.0 / 600


def throw_limits(t=None):
    """The `_start` limits of the backstab row, as `ThrowPoseChecks` reads them for an attempt."""
    crits = _mod('er_mechanics_crits', 'er-mechanics-crits.py')
    r = crits.Tables().throw[BACKSTAB_THROW_ROW]
    return {'dist': r['Dist_start'], 'diff_min': r['DiffAngMin_start'], 'diff_max': r['DiffAngMax_start'],
            'my_to_def': r['diffAngMyToDef_start']}


def skill_blocks_backstab(t, sid):
    """[(anim, what)] in any clip of the skill that makes its user unthrowable."""
    blocking = {k for k, v in t.speffect.items() if v.get('throwCondition') in THROW_BLOCKING_CONDITIONS}
    out = []
    for anim, events in (A.skill_tae(t, sid) or {}).items():
        for e in events:
            if e.type == A.EV_JUMP_TABLE and struct.unpack_from('<i', e.params, 0)[0] in THROW_IMMUNE_JUMP_TABLES:
                out.append((anim, f"JumpTable {struct.unpack_from('<i', e.params, 0)[0]}"))
            elif e.type in A.EV_SPEFFECT or e.type == A.EV_WA_SPEFFECT:
                for sp in struct.unpack_from('<ii', e.params, 0):
                    if sp in blocking:
                        out.append((anim, f'SpEffect {sp}'))
    return out


def _wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def circle(t, wid, sid, roll_frame, limits, speed=RUN_SPEED, pose=None):
    """{'open_s', 'close_s', 'margin_s'}: when the circler first meets the backstab limits, when the
    caster can roll, and the difference (None when the limits are never met before the roll)."""
    pose = pose or R._pose_module()
    prof = A.skill_profile(t, sid, wid)
    anim = A.main_anim(prof)
    events = (A.skill_tae(t, sid) or {}).get(anim) or []
    category = A.SKILL_TAE_BASE + t.arts[sid]['swordArtsTypeNew']
    turns, disabled = R.turn_windows(events)
    windows = R.speed_windows(events)
    close_s = roll_frame / A.TAE_FPS
    clip_end = max([e.end for e in events] + [0.0])
    cx = cz = 0.0
    face = 0.0                      # yaw of the caster, 0 = facing +z toward the circler
    px, pz = 0.0, START_DISTANCE_M
    # The clip's motion comes from the HKX it plays, which an entry may borrow (`hkx_source`).
    src = R.hkx_source(category, anim)
    try:
        prev = pose.root_motion(*src, 0.0) if pose else None
    except FileNotFoundError:
        prev = None
    moves = prev is not None
    prev = prev or (0.0, 0.0, 0.0, 0.0)
    clip_t = real_t = 0.0
    while real_t < close_s:
        dt_real = STEP_S / R.speed_at(clip_t, windows)
        # caster: root motion in its own frame (forward is -z in the clip), then turn toward the circler
        cur = pose.root_motion(*src, min(clip_t + STEP_S, clip_end)) if moves else prev
        fwd, side, yaw = -(cur[2] - prev[2]), cur[0] - prev[0], cur[3] - prev[3]
        prev = cur
        cx += fwd * math.sin(face) + side * math.cos(face)
        cz += fwd * math.cos(face) - side * math.sin(face)
        face += yaw
        # the circler's body stops the caster: two push capsules cannot overlap (`reach`
        # `front_contact_times`, the circler holding his ground is `INFERRED` there too)
        gx, gz = cx - px, cz - pz
        gap = math.hypot(gx, gz)
        if gap < 2 * R.PUSH_CAPSULE_RADIUS:
            k = 2 * R.PUSH_CAPSULE_RADIUS / max(gap, 1e-9)
            cx, cz = px + gx * k, pz + gz * k
        want = math.atan2(px - cx, pz - cz)
        rate = math.radians(R.turn_rate_at(clip_t, turns, disabled, 'locked')) * dt_real
        d = _wrap(want - face)
        face += max(-rate, min(rate, d))
        # circler: after reacting, run to the point behind the caster
        if real_t >= REACTION_S:
            tx, tz = cx - TARGET_BEHIND_M * math.sin(face), cz - TARGET_BEHIND_M * math.cos(face)
            dx, dz = tx - px, tz - pz
            dist = math.hypot(dx, dz)
            if dist > 1e-6:
                # go round the side rather than through the caster
                ox, oz = px - cx, pz - cz
                if math.hypot(ox, oz) < 1.0 and (dx * ox + dz * oz) < 0:
                    dx, dz = dx + oz, dz - ox
                    dist = math.hypot(dx, dz)
                step = min(dist, speed * dt_real)
                px += dx / dist * step
                pz += dz / dist * step
        # the backstab limits, the circler facing the caster
        sep = math.hypot(cx - px, cz - pz)
        p_face = math.atan2(cx - px, cz - pz)
        if sep < limits['dist'] and limits['diff_min'] <= math.degrees(_wrap(p_face - face)) <= limits['diff_max']:
            return {'open_s': round(real_t, 3), 'close_s': round(close_s, 3), 'margin_s': round(close_s - real_t, 3),
                    'root_motion': moves}
        clip_t += STEP_S
        real_t += dt_real
    return {'open_s': None, 'close_s': round(close_s, 3), 'margin_s': None, 'root_motion': moves}


def weapon_backstabs(weapon, skill=None):
    """Per skill the weapon fires with a hit: blocking events, and the race at run and sprint speed."""
    tables = EXPORT.AR.Tables(None)
    ashes = EXPORT.ash_options(tables, weapon)
    t = EXPORT._levers()[1].t
    wid = t.find_weapon(weapon)
    limits = throw_limits()
    pose = R._pose_module()
    out = []
    for s in ashes['options']:
        if skill and s['name'] != skill:
            continue
        roll = s['commitment']['roll']
        run = circle(t, wid, s['id'], roll, limits, RUN_SPEED, pose)
        sprint = circle(t, wid, s['id'], roll, limits, SPRINT_SPEED, pose)
        # A sprinter can always run instead; the straight-line chase is not optimal at every speed.
        if run['open_s'] is not None and (sprint['open_s'] is None or run['open_s'] < sprint['open_s']):
            sprint = dict(run)
        out.append({'id': s['id'], 'name': s['name'], 'blocked_by': skill_blocks_backstab(t, s['id']),
                    'run': run, 'sprint': sprint})
    return {'weapon': weapon, 'limits': limits, 'reaction_s': REACTION_S, 'start_m': START_DISTANCE_M,
            'skills': out}


def selftest():
    lim = throw_limits()
    assert (round(lim['dist'], 2), lim['diff_min'], lim['diff_max'], lim['my_to_def']) == (1.7, -35.0, 35.0, 70.0), lim
    out = weapon_backstabs('Lance')
    assert len(out['skills']) == 43 and not any(s['blocked_by'] for s in out['skills']), out['skills'][:2]
    for s in out['skills']:
        for k in ('run', 'sprint'):
            r = s[k]
            assert r['open_s'] is None or REACTION_S <= r['open_s'] <= r['close_s'], s
        # sprinting never arrives later than running
        if s['run']['open_s'] is not None:
            assert s['sprint']['open_s'] is not None and s['sprint']['open_s'] <= s['run']['open_s'] + 1e-6, s
    still = [s['name'] for s in out['skills'] if not s['run']['root_motion']]
    assert len(still) <= 2, f'no root motion found for {still}'
    opened = [s for s in out['skills'] if s['run']['margin_s']]
    assert opened and len(opened) < len(out['skills']), [(s['name'], s['run']) for s in out['skills']]
    print(f"selftest ok: Lance, {len(opened)} of {len(out['skills'])} skills open to a backstab at run speed")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('weapon', nargs='?')
    ap.add_argument('--skill')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.weapon:
        ap.error('weapon required')
    out = weapon_backstabs(a.weapon, a.skill)
    if a.json:
        json.dump(out, sys.stdout, separators=(',', ':'))
        return 0
    for s in sorted(out['skills'], key=lambda s: -(s['run']['margin_s'] or -1)):
        r, sp = s['run'], s['sprint']
        fmt = lambda x: f"open {x['open_s']:.2f}s, roll {x['close_s']:.2f}s, margin {x['margin_s']:+.2f}s" \
            if x['margin_s'] is not None else f"never before the roll at {x['close_s']:.2f}s"
        print(f"{s['name']:<28} run: {fmt(r)} | sprint: {fmt(sp)}"
              + (f" | blocked: {s['blocked_by']}" if s['blocked_by'] else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
