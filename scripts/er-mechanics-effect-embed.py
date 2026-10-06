#!/usr/bin/env python3
"""Talismans and armor as vectors of what their effects do, so items that do the same thing sit together.

    python3 scripts/er-mechanics-effect-embed.py similar "Kindred of Rot's Exultation"
    python3 scripts/er-mechanics-effect-embed.py show "Mushroom Crown"
    python3 scripts/er-mechanics-effect-embed.py --json > /tmp/effects.json
    python3 scripts/er-mechanics-effect-embed.py --selftest

Each item is the SpEffect closure `er-mechanics-talismans.py` already follows (talismans through
`refId` and `residentSpEffectId1..4`, armor through `residentSpEffectId..3`). Every effect in it
becomes one feature, `trigger | system | effect`:

* trigger: what has to be true for the row to apply, inherited down the closure, so the 20 s buff
  Kindred of Rot's Exultation cycles into keeps its parent's "stateInfo 380" condition. Durations
  are dropped and every successive-hit accumulator is one trigger, since what the item does is
  the same whichever counter feeds it.
* system and effect: `_systems()`'s summary with its numbers replaced by a direction, so x1.1 and
  x1.2 damage are the same feature and x0.9 is a different one.

Features are binary. A magnitude would need an exchange rate between, say, +5 DEX and x1.04
damage, and nothing in the game supplies one. Similarity is the cosine of two items' feature
sets, which is the share of what they do that they do alike.

Two pairs anchor `--selftest`: Kindred of Rot's Exultation with Mushroom Crown (the same stateInfo
380 trigger, the same damage rows), and Millicent's Prosthesis with Rotten Winged Sword Insignia
(the same successive-hit damage, plus DEX on Millicent's only, so half of what it does).
"""
import argparse
import importlib.util
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_s = importlib.util.spec_from_file_location('er_mechanics_talismans', os.path.join(HERE, 'er-mechanics-talismans.py'))
T = importlib.util.module_from_spec(_s)
_s.loader.exec_module(T)
PR = T.PR

NUMBER = re.compile(r'[-+]?\d+(?:\.\d+)?')


def trigger_terms(sp, stateinfo_names):
    """The conditions of one row that say when it applies, durations left out."""
    out = []
    for c in sp.condition():
        if c.startswith(('lasts ', 'timed buff')):
            continue
        if 'successive' in c:
            out.append('successive hits')
            continue
        m = re.match(r'owner has an effect with stateInfo (\d+)', c)
        if m:
            si = int(m.group(1))
            out.append(f'while stateInfo {si} {stateinfo_names.get(si, "")}'.rstrip())
            continue
        out.append(NUMBER.sub('#', c))
    return out


# The game keeps a players and an enemies column for each damage rate. They are one effect seen
# from two targets, and as two features they outweighed everything else an item does two to one.
SAME_SYSTEM = {'damage vs players': 'damage dealt', 'damage vs enemies': 'damage dealt',
               'damage taken from players': 'damage taken', 'damage taken from enemies': 'damage taken'}


def effect_term(system, what):
    system = SAME_SYSTEM.get(system, system)
    nums = [float(x) for x in NUMBER.findall(what)]
    direction = ''
    if nums:
        v = nums[0]
        direction = ' up' if (v > 1 if ' x' in what or what.startswith('x') else v > 0) else ' down'
    return f'{system}: {NUMBER.sub("#", what)}{direction}'


def features(speffects, stateinfo_names):
    """{feature} of one closure; each row inherits the triggers of the rows that link to it."""
    by_id = {s.id: s for s in speffects}
    inherited = {s.id: set() for s in speffects}
    for s in speffects:
        own = set(trigger_terms(s, stateinfo_names)) | inherited[s.id]
        inherited[s.id] = own
        for child in s.links.values():
            if child in by_id:
                inherited[child] |= own
    out = set()
    for s in speffects:
        trig = ' & '.join(sorted(inherited[s.id])) or 'always'
        for system, what in s.systems():
            out.add(f'{trig} | {effect_term(system, what)}')
    return out


class Items:
    def __init__(self):
        self.t = T.Talismans()
        names = self.t.stateinfo_names or {}
        si = {int(k): v for k, v in names.items()} if isinstance(names, dict) else {}
        self.items = {}
        for tal in self.t:
            self.items[tal.name] = ('talisman', features(tal.speffects, si))
        rows = PR.rows(PR.param_bytes(self.t._files, 'EquipParamProtector'),
                       ['residentSpEffectId', 'residentSpEffectId2', 'residentSpEffectId3'], strict=False)[0]
        pn = PR.row_names('EquipParamProtector')
        for r in rows:
            nm = pn.get(r['id'])
            ids = [r['residentSpEffectId'], r['residentSpEffectId2'], r['residentSpEffectId3']]
            if not nm or not any(i > 0 for i in ids) or nm in self.items:
                continue
            fake = {'refId': -1, 'residentSpEffectId1': ids[0], 'residentSpEffectId2': ids[1],
                    'residentSpEffectId3': ids[2], 'residentSpEffectId4': -1}
            f = features(self.t._closure(fake, []), si)
            if f:
                self.items[nm] = ('armor', f)

    def find(self, name):
        if name in self.items:
            return name
        low = name.casefold()
        hits = [n for n in self.items if low in n.casefold()]
        return min(hits, key=len) if hits else None

    def similar(self, name, n=10):
        a = self.items[name][1]
        out = []
        for other, (kind, b) in self.items.items():
            if other == name or not b:
                continue
            cos = len(a & b) / math.sqrt(len(a) * len(b))
            if cos > 0:
                out.append((cos, other, kind, sorted(a & b)))
        return sorted(out, key=lambda x: (-x[0], x[1]))[:n]


def selftest():
    it = Items()
    kin, mush = it.find("Kindred of Rot's Exultation"), it.find('Mushroom Crown')
    mil, rot = it.find("Millicent's Prosthesis"), it.find('Rotten Winged Sword Insignia')
    assert kin and mush and mil and rot, (kin, mush, mil, rot)
    top = it.similar(kin, 3)
    assert any(o == mush and c == 1.0 for c, o, _, _ in top), top
    mr = dict((o, c) for c, o, _, _ in it.similar(mil, 50))
    assert 0.5 <= mr.get(rot, 0) < 1.0, mr.get(rot)
    print(f"selftest ok: {kin} ~ {mush} 1.000, {mil} ~ {rot} {mr[rot]:.3f}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd', nargs='?', choices=['similar', 'show'])
    ap.add_argument('name', nargs='?')
    ap.add_argument('-n', type=int, default=10)
    ap.add_argument('--json', action='store_true', help='every item and its features')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    it = Items()
    if a.json:
        json.dump({n: {'kind': k, 'features': sorted(f)} for n, (k, f) in it.items.items()}, sys.stdout, indent=1)
        return 0
    if not a.cmd or not a.name:
        ap.error('similar/show need an item name')
    name = it.find(a.name)
    if not name:
        print(f'no talisman or armor piece with an effect matches {a.name!r}')
        return 1
    kind, feats = it.items[name]
    print(f'{name} ({kind})')
    if a.cmd == 'show':
        for f in sorted(feats):
            print(f'  {f}')
        return 0
    for cos, other, k, shared in it.similar(name, a.n):
        print(f'  {cos:.3f}  {other:<40} {k:<8} {"; ".join(shared)[:110]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
