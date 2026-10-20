#!/usr/bin/env python3
"""What a Dark Souls Remastered character's Havok file says about its loose parts.

Usage: dsr-chr-physics.py <chr-id> [--match <text>] [--dsr-chr-dir <dir>]
                          [--types | --clips | --clip <file> | --survey]

Reads `chr/<id>.chrbnd.dcx`, takes the `<id>.hkx` inside it (a Havok 2015 `TAG0` tagfile) and
prints, for the bones whose names contain `--match` (default `tail`): where each sits in the
animation skeleton, which ragdoll body drives it through the skeleton mapper, and that body's
joint (hinge or limited hinge, its limits, its axis in model space). Other modes:

- `--types`: every object type the file holds.
- `--clips`: every clip in `chr/<id>.anibnd.dcx`, and how many of the matched bones it animates.
- `--clip <file>`: how far each matched bone turns against its parent through that clip, and,
  for up to 12 bones, their heights above the feet and reach behind the pelvis at five times.
- `--survey`: every chrbnd in the install, with the ragdoll bodies that are not a Biped's limbs.

Measured with it, 2026-10-15: Priscilla's (c2730) tail has 4 dynamic ragdoll bodies on
`tail_00/02/04/06` joined by unlimited hinges about the model X axis, `tail_07` to the tip ride
the last one, and all 27 of her clips animate every tail bone; her idle (`a00_0000`) arcs the
tail from the pelvis down onto the ground at `tail_06`-`tail_07`. Her sleeve (`*_sode_*`) and
hair bones move only in her clips: no ragdoll body drives them. Across all 193 chrbnds, cloth
bodies (`Ragdoll_Cloth*`, `*Skirt*`) are in two ragdolls only, c4150 and c4160 (Bloatheads).
Read-only; writes nothing.

The binder reader is `dsr-chr-census.py`'s and the tagfile decoder `er-hkx-pose.py`'s.
"""

import argparse
import collections
import importlib.util
import os
import sys


def _load(name, file):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(os.path.dirname(os.path.abspath(__file__)), file))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


census = _load('dsr_chr_census', 'dsr-chr-census.py')
erhkx = _load('er_hkx_pose', 'er-hkx-pose.py')


class BytesTagfile(erhkx.Tagfile):
    """`er-hkx-pose.py`'s Tagfile over bytes already in memory."""

    def __init__(self, data, label):
        self.b = b = data
        top = erhkx._sections(b, 0, len(b))[0]
        if top[0] != 'TAG0':
            raise ValueError(f'{label}: not a TAG0 tagfile')
        sec = erhkx._section_map(b, top)
        self.data = sec['DATA'][0]
        self.items = [erhkx.struct.unpack_from('<III', b, o) for o in range(*sec['ITEM'], 12)]
        self._memo = {}
        if 'TCRF' in sec:
            raise ValueError(f'{label}: refers to a compendium, which a DSR chr file has none of')
        self.types = erhkx._parse_types(label, b, sec)

    def item(self, idx):
        """The parent's `item`, with a struct recorded before its members are read, so an object
        reached again through its own members (a rigid body and the constraints that name it)
        comes back as itself instead of recursing without end."""
        if idx == 0 or idx in self._memo:
            return self._memo.get(idx)
        ft, off, _ = self.items[idx]
        ti = ft & 0xffffff
        if ft >> 28 == 1 and self._inherit(ti, 'sub') & 0x1f == 7:
            value = {}
            self._memo[idx] = value
            base = self.data + off
            for name, _, member_offset, member_type in self.members(ti):
                value[name] = self.read(member_type, base + member_offset)
            return value
        return super().item(idx)


def character_hkx(chr_dir, chr_id):
    path = os.path.join(chr_dir, f'{chr_id}.chrbnd.dcx')
    with open(path, 'rb') as handle:
        files = census.bnd3(census.dcx(handle.read()))
    name = f'{chr_id}.hkx'
    for file_name, data in files.items():
        if file_name.lower() == name:
            return data
    raise SystemExit(f'{path} holds no {name}: {sorted(files)}')


def animation_skeleton(tf):
    ragdoll = tf.objects('hkaRagdollInstance')[0]['skeleton']
    found = [s for s in tf.objects('hkaSkeleton') if s is not ragdoll]
    if len(found) != 1:
        raise SystemExit(f'expected one animation skeleton beside the ragdoll, found {len(found)}')
    sk = found[0]
    bind = [(tuple(p['translation'][:3]), tuple(p['rotation']), tuple(p['scale'][:3]))
            for p in sk['referencePose']]
    return erhkx.Skeleton([b['name'] for b in sk['bones']], list(sk['parentIndices']), bind)


def anibnd_clips(chr_dir, chr_id):
    path = os.path.join(chr_dir, f'{chr_id}.anibnd.dcx')
    with open(path, 'rb') as handle:
        files = census.bnd3(census.dcx(handle.read()))
    return {name: data for name, data in files.items() if name.lower().endswith('.hkx')}


def load_clip(data, label):
    tf = BytesTagfile(data, label)
    anims = tf.objects('hkaSplineCompressedAnimation')
    bindings = tf.objects('hkaAnimationBinding')
    if len(anims) != 1 or len(bindings) != 1:
        return None
    a = anims[0]
    blocks = [erhkx._decode_block(a, i) for i in range(a['numBlocks'])]
    return erhkx.Animation(label, a, bindings[0], blocks)


def clips(tf, args):
    sk = animation_skeleton(tf)
    matched = [i for i, name in enumerate(sk.names) if args.match.lower() in name.lower()]
    files = anibnd_clips(args.dsr_chr_dir, args.chr_id)
    if args.clips:
        animated = 0
        for name in sorted(files):
            tf_clip = BytesTagfile(files[name], name)
            bindings = tf_clip.objects('hkaAnimationBinding')
            if not bindings:
                print(f'  {name}: no animation binding')
                continue
            bones = set(bindings[0]['transformTrackToBoneIndices'])
            hit = [sk.names[i] for i in matched if i in bones]
            animated += bool(hit)
            print(f"  {name}: {len(bones)} bones animated, {len(hit)} of the {len(matched)} "
                  f"matched")
        print(f'{animated} of {len(files)} clips animate a matched bone')
        return
    data = files.get(args.clip)
    if data is None:
        raise SystemExit(f'no clip {args.clip!r}; the anibnd holds {sorted(files)[:20]}...')
    clip = load_clip(data, args.clip)
    if clip is None:
        raise SystemExit(f'{args.clip}: not one spline-compressed animation')
    # How far each matched bone turns against its parent through the clip: the largest angle
    # between its local rotation at any sample and at the first. Zero is a bone the clip holds
    # still.
    samples = [clip.duration * k / 32 for k in range(33)]
    poses = [erhkx._local_pose(sk, clip, t) for t in samples]
    print(f'{args.clip}: degrees each matched bone turns against its parent over the clip')
    for i in matched:
        first = poses[0][i][1]
        turn = max(erhkx._angle(first, pose[i][1]) for pose in poses)
        print(f'  {sk.names[i]:<14} {turn:6.1f}')
    if len(matched) > 12:
        return
    pelvis = sk.index.get('Pelvis')
    feet = [sk.index[n] for n in ('L Foot', 'R Foot') if n in sk.index]
    print(f'{args.clip}: {clip.duration:.2f} s, {clip.num_frames} frames; heights are metres '
          f'above the lower foot, reach is metres behind the pelvis')
    steps = 4
    for step in range(steps + 1):
        t = clip.duration * step / steps
        model = erhkx._forward_kinematics(sk, erhkx._local_pose(sk, clip, t))
        ground = min(model[f][0][1] for f in feet) if feet else 0.0
        px, py, pz = model[pelvis][0]
        row = []
        for i in matched:
            x, y, z = model[i][0]
            row.append(f'{sk.names[i]} h{y - ground:+.2f} r{((x - px) ** 2 + (z - pz) ** 2) ** 0.5:.2f}')
        print(f'  t={t:5.2f} pelvis h{py - ground:+.2f}: ' + ', '.join(row))


#: The ragdoll bodies a plain Biped ragdoll is made of, by the name after `Ragdoll_`, lower case
#: and with digits and separators dropped.
LIMB_BODIES = {'pelvis', 'spine', 'neck', 'head', 'clavicle', 'upperarm', 'forearm', 'hand',
               'thigh', 'calf', 'foot', 'lclavicle', 'rclavicle', 'lupperarm', 'rupperarm',
               'lforearm', 'rforearm', 'lhand', 'rhand', 'lthigh', 'rthigh', 'lcalf', 'rcalf',
               'lfoot', 'rfoot'}


def limb_key(name):
    stem = (name or '').lower()
    stem = stem[len('ragdoll_'):] if stem.startswith('ragdoll_') else stem
    return ''.join(c for c in stem if c.isalpha())


def survey(chr_dir):
    import glob
    rows = []
    for path in sorted(glob.glob(os.path.join(chr_dir, 'c*.chrbnd.dcx'))):
        chr_id = os.path.basename(path).split('.')[0]
        try:
            with open(path, 'rb') as handle:
                files = census.bnd3(census.dcx(handle.read()))
            data = next((d for n, d in files.items() if n.lower() == f'{chr_id}.hkx'), None)
            if data is None:
                rows.append((chr_id, 'no hkx', ''))
                continue
            tf = BytesTagfile(data, chr_id)
            rows.append(survey_row(chr_id, tf))
        except Exception as error:  # one unreadable file is reported, not fatal
            rows.append((chr_id, f'unreadable: {type(error).__name__} {error}'[:120], ''))
    with_extra = [r for r in rows if r[2]]
    unreadable = [r for r in rows if r[1].startswith('unreadable')]
    for chr_id, bodies, extra in rows:
        if extra or bodies.startswith('unreadable'):
            print(f'{chr_id} {bodies}: past the limbs [{extra}]')
    print(f'{len(rows)} chrbnds; {len(with_extra)} with ragdoll bodies past the limbs, '
          f'{len(unreadable)} unreadable')


def survey_row(chr_id, tf):
    """(id, body count, the ragdoll bodies that are not limbs) for one file."""
    ragdolls = tf.objects('hkaRagdollInstance')
    extra_bodies = [b['name'] for ragdoll in ragdolls for b in ragdoll['rigidBodies']
                    if limb_key(b['name']) not in LIMB_BODIES]
    bodies = sum(len(r['rigidBodies']) for r in ragdolls)
    return (chr_id, f'{bodies} bodies', ' '.join(extra_bodies))


def type_counts(tf):
    counts = collections.Counter()
    for ft, _, _ in tf.items:
        if ft >> 28 == 1:
            counts[tf.type_name(ft & 0xffffff)] += 1
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('chr_id')
    parser.add_argument('--match', default='tail')
    parser.add_argument('--dsr-chr-dir', default=census.DEFAULT_DSR_CHR)
    parser.add_argument('--types', action='store_true')
    parser.add_argument('--clips', action='store_true',
                        help='list the anibnd clips and which of them animate the matched bones')
    parser.add_argument('--clip', help='print the matched bones through this clip (a file name)')
    parser.add_argument('--survey', action='store_true',
                        help='every chr in the install: its ragdoll bodies past the limbs')
    args = parser.parse_args()
    # The decoder recurses once per nested object; the skeletons' files nest past the default.
    sys.setrecursionlimit(20000)
    if args.survey:
        survey(args.dsr_chr_dir)
        return
    tf = BytesTagfile(character_hkx(args.dsr_chr_dir, args.chr_id), f'{args.chr_id}.hkx')
    if args.clips or args.clip:
        clips(tf, args)
        return
    if args.types:
        for name, count in sorted(type_counts(tf).items()):
            print(f'{count:4} {name}')
        for name in ('hkaRagdollInstance', 'hkaSkeletonMapper', 'hkpRigidBody',
                     'hkpConstraintInstance', 'hkpHingeConstraintData',
                     'hkpLimitedHingeConstraintData'):
            objects = tf.objects(name)
            if objects:
                print(f'{name} members: {sorted(objects[0])}')
        return
    report(tf, args.match.lower())


def constraint_kind(tf, data):
    for name in ('hkpLimitedHingeConstraintData', 'hkpHingeConstraintData'):
        if any(data is other for other in tf.objects(name)):
            return name
    return 'other'


def degrees(radians):
    return round(radians * 180 / 3.141592653589793, 1)


def report(tf, match):
    ragdoll = tf.objects('hkaRagdollInstance')[0]
    ragdoll_skeleton = ragdoll['skeleton']
    ragdoll_names = [b['name'] for b in ragdoll_skeleton['bones']]
    animation = [s for s in tf.objects('hkaSkeleton') if s is not ragdoll_skeleton]
    if len(animation) != 1:
        raise SystemExit(f'expected one animation skeleton beside the ragdoll, found {len(animation)}')
    animation = animation[0]
    names = [b['name'] for b in animation['bones']]
    parents = animation['parentIndices']
    print(f"animation skeleton {animation['name']!r}: {len(names)} bones; ragdoll skeleton "
          f"{ragdoll_skeleton['name']!r}: {len(ragdoll_names)} bones, "
          f"{len(ragdoll['rigidBodies'])} bodies, {len(ragdoll['constraints'])} constraints")

    # Which ragdoll bone drives each animation bone: the ragdoll -> animation mapper.
    driven_by = collections.defaultdict(list)
    for mapper in tf.objects('hkaSkeletonMapper'):
        mapping = mapper['mapping']
        a = [b['name'] for b in mapping['skeletonA']['bones']]
        b = [x['name'] for x in mapping['skeletonB']['bones']]
        direction = f"{mapping['skeletonA']['name']} -> {mapping['skeletonB']['name']}"
        print(f"mapper {direction}: {len(mapping['simpleMappings'])} simple, "
              f"{len(mapping['chainMappings'])} chain, "
              f"{len(mapping.get('unmappedBones') or [])} unmapped")
        for simple in mapping['simpleMappings']:
            pair = (a[simple['boneA']], b[simple['boneB']])
            driven_by[pair[0]].append(f'{direction}: simple {pair[0]} -> {pair[1]}')
            driven_by[pair[1]].append(f'{direction}: simple {pair[0]} -> {pair[1]}')
        for chain in mapping['chainMappings']:
            line = (f"{direction}: chain {a[chain['startBoneA']]}..{a[chain['endBoneA']]} -> "
                    f"{b[chain['startBoneB']]}..{b[chain['endBoneB']]}")
            for end in (chain['startBoneA'], chain['endBoneA']):
                driven_by[a[end]].append(line)
            for end in (chain['startBoneB'], chain['endBoneB']):
                driven_by[b[end]].append(line)
        for index in mapping.get('unmappedBones') or []:
            if index < len(a):
                driven_by[a[index]].append(f'{direction}: unmapped')

    print(f'\nanimation bones matching {match!r}:')
    for index, name in enumerate(names):
        if match in name.lower():
            parent = names[parents[index]] if parents[index] >= 0 else '-'
            print(f'  {index:3} {name:<12} parent {parent:<12} {"; ".join(driven_by[name]) or "no mapping"}')

    bodies = ragdoll['rigidBodies']
    body_names = [body['name'] for body in bodies]
    print(f'\nragdoll bodies matching {match!r} (boneToRigidBodyMap {ragdoll["boneToRigidBodyMap"]}):')
    for index, body in enumerate(bodies):
        if match in (body['name'] or '').lower():
            motion = body['motion']
            print(f"  {index:2} {body['name']:<18} motion type {motion.get('type')} "
                  f"{'; '.join(driven_by[ragdoll_names[index]]) if index < len(ragdoll_names) else ''}")

    print('\nconstraints touching them:')
    for index, constraint in enumerate(ragdoll['constraints']):
        ends = [entity['name'] if entity else None for entity in constraint['entities']]
        if not any(match in (end or '').lower() for end in ends):
            continue
        data = constraint['data']
        kind = constraint_kind(tf, data)
        line = f"  {index:2} {ends[0]} <-> {ends[1]}: {kind}"
        atoms = data.get('atoms', {})
        limit = atoms.get('angLimit')
        if limit:
            line += (f", swings {degrees(limit['minAngle'])} to {degrees(limit['maxAngle'])} "
                     f"degrees about its hinge, limit on: {limit.get('isEnabled')}")
        friction = atoms.get('angFriction')
        if friction:
            line += f", friction {friction.get('maxFrictionTorque')} (on: {friction.get('isEnabled')})"
        axis = hinge_axis(constraint['entities'][0], atoms)
        if axis:
            line += f", hinge axis in model space ({axis[0]:+.2f}, {axis[1]:+.2f}, {axis[2]:+.2f})"
        print(line)
    _ = body_names


def hinge_axis(body, atoms):
    """The hinge's axis in model space: the first column of body A's constraint frame, turned by
    body A's orientation. `None` when the atoms carry no frames."""
    transforms = atoms.get('transforms')
    if not transforms or not body:
        return None
    column = tuple(columns(transforms['transformA'])[0])
    state = body['motion'].get('motionState') or {}
    if not state.get('transform'):
        return column
    cols = columns(state['transform'])
    return tuple(sum(cols[k][r] * column[k] for k in range(3)) for r in range(3))


def columns(transform):
    """The three rotation columns of an `hkTransform`, which the decoder gives either as four
    float4 rows (three rotation columns, then the translation) or as a rotation member."""
    if isinstance(transform, dict):
        transform = transform['rotation']
        if isinstance(transform, dict):
            return [transform[f'col{k}'][:3] for k in range(3)]
    flat = [v for row in transform for v in (row if isinstance(row, (list, tuple)) else [row])]
    return [flat[4 * k:4 * k + 3] for k in range(3)]


if __name__ == '__main__':
    main()
