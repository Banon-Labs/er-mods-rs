#!/usr/bin/env python3
"""Which collision shapes each cloth of an ER part collides with, and which bones it drives.

An ER part's `<PART>_c.hkx` holds an `hclClothData` per simulated piece. Its
`hclSimClothData` particles only collide with the `perInstanceCollidables` their
`staticCollisionMasks` bits select, so a capsule present in the file is not
proof that a given cape avoids it. Only a cloth with an
`hclSimpleMeshBoneDeformOperator` moves `[cloth]` bones; a cloth without one moves
its own mesh and nothing skinned to the part's bones.

    python3 scripts/er-cloth-collidables.py <part>_c.hkx...
    python3 scripts/er-cloth-collidables.py --driving-near Arm <part>_c.hkx...

`--driving-near <text>` prints only the files with a bone-driving cloth whose
particles hit a collidable named with `<text>`.
"""
import importlib.util
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    'hkx_tagfile', Path(__file__).with_name('hkx-tagfile.py'))
hkx = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(hkx)

COLLIDABLE_MARK = '_Collidable_'
MATRIX_SIZE = 64


class Reader:
    def __init__(self, data):
        self.tf = hkx.Tagfile(data)

    def member(self, item, name):
        return item['off'] + self.tf.member_map(item['type'])[name][0]

    def u32(self, item, name):
        return int.from_bytes(self.raw(self.member(item, name), 4), 'little')

    def raw(self, at, size):
        start = self.tf.data_off + at
        return self.tf.d[start:start + size]

    def string(self, item, name):
        target = self.tf.ptch.get(self.member(item, name))
        return self.tf.cstr(target) if target is not None else ''

    def array(self, item, name):
        """(body item, count): a tagfile hkArray holds only its body's item index."""
        body = self.tf.ptch.get(self.member(item, name))
        return body, self.tf.items[body]['count'] if body is not None else 0

    def pointers(self, item, name):
        body, count = self.array(item, name)
        if body is None:
            return []
        first = self.tf.items[body]['off']
        return [self.tf.items[self.tf.ptch[first + 8 * k]] for k in range(count)
                if first + 8 * k in self.tf.ptch]

    def words(self, item, name, size):
        body, count = self.array(item, name)
        if body is None:
            return []
        data = self.raw(self.tf.items[body]['off'], size * count)
        return [int.from_bytes(data[size * k:size * k + size], 'little') for k in range(count)]


def collisions(reader, sim):
    names = [reader.string(c, 'name') for c in reader.pointers(sim, 'perInstanceCollidables')]
    used = 0
    for mask in reader.words(sim, 'staticCollisionMasks', 4):
        used |= mask
    return [name for bit, name in enumerate(names) if used >> bit & 1]


def skeleton_bones(reader):
    """The bone names of the container's first skeleton: transform sets index into it."""
    for skeleton in reader.tf.find_items('hkaSkeleton'):
        body, count = reader.array(skeleton, 'bones')
        if body is None:
            continue
        first = reader.tf.items[body]['off']
        stride = reader.tf.types[reader.tf.items[body]['type']].size or 16
        return [reader.tf.cstr(reader.tf.ptch[first + stride * k])
                if first + stride * k in reader.tf.ptch else '?' for k in range(count)]
    return []


def cloths(data):
    """[(cloth name, [(sim name, particles, collidables hit)], [bones it drives])]"""
    reader = Reader(data)
    bones = skeleton_bones(reader)
    out = []
    for cloth in reader.tf.find_items('hclClothData'):
        sims = []
        for sim in reader.pointers(cloth, 'simClothDatas'):
            _, particles = reader.array(sim, 'particleDatas')
            sims.append((reader.string(sim, 'name'), particles, collisions(reader, sim)))
        driven = []
        for operator in reader.pointers(cloth, 'operators'):
            if reader.tf.tname(operator['type']) != 'hclSimpleMeshBoneDeformOperator':
                continue
            body, count = reader.array(operator, 'triangleBonePairs')
            if body is None:
                continue
            pairs = reader.raw(reader.tf.items[body]['off'], 4 * count)
            for k in range(count):
                # boneOffset is a byte offset into the transform set's 64-byte matrices.
                index = int.from_bytes(pairs[4 * k:4 * k + 2], 'little') // MATRIX_SIZE
                name = bones[index] if index < len(bones) else f'#{index}'
                if name not in driven:
                    driven.append(name)
        out.append((reader.string(cloth, 'name'), sims, driven))
    return out


def short(name):
    return name.split(COLLIDABLE_MARK)[-1]


def main(argv):
    near = None
    if argv[:1] == ['--driving-near']:
        near, argv = argv[1], argv[2:]
    for path in argv:
        found = cloths(Path(path).read_bytes())
        if near is not None:
            found = [c for c in found if c[2] and any(
                near in short(hit) for _, _, hits in c[1] for hit in hits)]
            if not found:
                continue
        print(Path(path).name)
        for name, sims, driven in found:
            print(f'  cloth {name}: drives {len(driven)} bones {", ".join(driven) or "-"}')
            for sim, particles, hits in sims:
                print(f'    sim {sim}: {particles} particles, collides with '
                      f'{", ".join(short(h) for h in hits) or "nothing"}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
