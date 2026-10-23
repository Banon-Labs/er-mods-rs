#!/usr/bin/env python3
"""Cast speed against damage for one attack spell: the rule the build generators use to fill the
talisman slots and the off-hand of a spell build.

    python3 scripts/er-mechanics-cast-speed.py "Giantsflame Take Thee" --stats dex=10,fth=60,str=30 \
        --catalyst "Giant's Seal"
    python3 scripts/er-mechanics-cast-speed.py "Catch Flame" --stats dex=40 --talismans "Radagon Icon"
    python3 scripts/er-mechanics-cast-speed.py --selftest

# What cast speed does

`CSPlayerMagicModule::GetCastingSpeed` (`0x140454300`, decompiled as 1.16.2 `0x140453cc0`):

    v = clamp(1 - (clamp(DEX + bonus, lo, hi) - lo) / (hi - lo), 0, 1)     (0 when hi == lo)

`DEX` is the effective DEX (`PlayerGameData::GetEffectiveDexterity`), `bonus` is
`SpecialEffect::GetCastingSpeedModifier`, the `dexterityCancelSystemOnlyAddDexterity` of the live
SpEffects (Radagon Icon 30, Azur's Glintstone Staff 40, Beloved Stardust 99; that several sources
add up is `INFERRED`), and `lo`/`hi` are `Magic.analogDexterityMin/Max` (10/70 on every player
spell). `v` is early type 5 of TAE event 300 (`ActivateChrActionFlagEarly`, attacks.md), and the
only type-5 events in a spell's TAE (`a<400 + Magic.refType>`) open JumpTable 93 in the start clips
(`..045000`, `..045100`, `..145000`), with weights 0 -> 1. So `v` places the opening inside that
event: frame `start + v * (end - start)`. The release clip (start + 10) carries the cast (TAE 64) and
no type-5 event, so cast speed moves the start of the cast and nothing after it: not the release,
not the recovery, not the release clips without a start clip in front of them (`..045011` for
Giantsflame). That the release clip begins when 93 opens is `INFERRED` (the hkb transition was not
traced). Which start clip is standing, running or left-handed is not known; they carry the same
window in every spell checked, and `--clip` picks one.

Measured: Giantsflame's window is frames 18..23 and its cast lands 46 frames into the release clip,
so 64..69 frames; Catch Flame's is 10..15 then 5, so 15..20. Every 12 effective DEX is one frame on
both, and nothing above `hi` counts.

# What damage the slot could buy instead

A spell's hit takes the attacker's SpEffect `*AttackRate` and `atkPlayerDmgCorrectRate_*` like a
weapon's (talismans.md). For a spell the context gate is `Magic.spEffectCategory`: 3 needs the row's
`magParamChange`, 4 `miracleParamChange`, 10 `shamanParamChange` (`IsApplicableForCategory`,
`VERIFIED`). Then `magicSubCategoryChange1..3` must meet the attack's subcategory mask. Giantsflame's
AtkParam rows carry no subcategory, while the mask in `AttackInfo+0x14` is accumulated with bitwise or
(`FUN_1406912a0`), and Giant's Seal's 1923 gates on 21 = Giantsflame's `Magic.subCategory1`, so the
spell's own subcategories are read as its mask (`INFERRED`). Held weapons' resident SpEffects count
from either hand, and two copies of one seal both stay live: 1923 is `spCategory` 10, which the
add path's same-id clash skips (buffs.md section 3, `VERIFIED`); that the two rates multiply is
`INFERRED`.

# The rule

    score = damage x (startup at v = 1) / startup / incoming

`damage` is the spell's element mix through every applicable rate; `incoming` is what the build
takes from a physical hit (`defPlayerDmgCorrectRate_Physics` x `neutralDamageCutRate`; PvP
damage is mostly physical, `INFERRED`), so Beloved Stardust's x1.3 and the scorpion charms' x1.15
are paid for. The speed term counts every frame saved as that much more casting, which is the most
a frame can be worth; a spell the opponent reacts to whatever its windup is worth less. With that
generous bound a 5-frame saving on Giantsflame is still x1.08, under a second Giant's Seal's x1.2,
while the same 5 frames on Catch Flame are x1.33. `best_loadout` fills the off-hand and the talisman
slots greedily on this score.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _mod(name, fname):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, fname))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


T = _mod('er_mechanics_talismans', 'er-mechanics-talismans.py')
A = _mod('er_mechanics_attacks', 'er-mechanics-attacks.py')
PR = T.PR

CAST_SPEED_FIELD = 'dexterityCancelSystemOnlyAddDexterity'
#: The JumpTable id the type-5 early events open: the start clip's hand-off to the release clip.
CAST_START_JUMP = 93
EARLY_CASTING_SPEED = 5
#: TAE event type that casts the spell (void-tech.md: "casts (type 64)").
TAE_CAST = 64
#: `Magic.spEffectCategory` -> the SpEffect flag that context needs (`IsApplicableForCategory`).
SPELL_CONTEXT_FLAG = {3: 'magParamChange', 4: 'miracleParamChange', 10: 'shamanParamChange'}
#: `Magic.spEffectCategory` -> the `EquipParamWeapon` flag a catalyst needs to cast it.
CATALYST_FLAG = {3: 'enableMagic', 4: 'enableMiracle'}
AFFIX = {'physical': 'atkPhys', 'magic': 'atkMag', 'fire': 'atkFire', 'lightning': 'atkThun', 'holy': 'atkDark'}
REQUIRE = {'str': 'properStrength', 'dex': 'properAgility', 'int': 'properMagic', 'fth': 'properFaith',
           'arc': 'properLuck'}
SPELL_REQUIRE = {'int': 'requirementIntellect', 'fth': 'requirementFaith', 'arc': 'requirementLuck'}
WEAPON_FIELDS = ['residentSpEffectId', 'residentSpEffectId1', 'residentSpEffectId2', 'enableMagic',
                 'enableMiracle', 'wepType'] + list(REQUIRE.values())
MAGIC_FIELDS = ['refType', 'analogDexterityMin', 'analogDexterityMax', 'refId1', 'refCategory1',
                'subCategory1', 'subCategory2', 'spEffectCategory', 'mp'] + list(SPELL_REQUIRE.values())
#: Change in score below which another pick is not worth a slot.
MIN_GAIN = 1e-6


def casting_value(dex, bonus, lo, hi):
    """`GetCastingSpeed`: 1 is the slowest start, 0 the fastest."""
    hi = max(hi, lo)
    if hi == lo:
        return 0.0
    x = min(max(dex + bonus, lo), hi)
    return min(max(1.0 - (x - lo) / (hi - lo), 0.0), 1.0)


class _Row:
    """A raw SpEffect row in the shape `T._hp_gate` / `T._timed_gate` read."""
    __slots__ = ('id', 'fields')

    def __init__(self, sid, fields):
        self.id, self.fields = sid, fields


class CastModel:
    """One regulation read: spells, catalysts, talismans, and the TAE of each spell's cast."""

    def __init__(self, regulation=None):
        self.t = T.Talismans(regulation)
        files = self.t._files
        mrows = PR.rows(PR.param_bytes(files, 'Magic'), MAGIC_FIELDS)[0]
        self.magic = {r['id']: r for r in mrows}
        self.spell_by_name = {}
        for mid, nm in PR.row_names('Magic').items():
            if mid in self.magic and nm and nm.startswith(('[Sorcery] ', '[Incantation] ')):
                self.spell_by_name.setdefault(T._norm(nm.split('] ', 1)[1]), mid)
        self.bullet = {r['id']: r for r in PR.rows(PR.param_bytes(files, 'Bullet'),
                                                   ['atkId_Bullet', 'HitBulletID', 'intervalCreateBulletId'])[0]}
        self.atk = {r['id']: r for r in PR.rows(PR.param_bytes(files, 'AtkParam_Pc'), list(AFFIX.values()))[0]}
        wrows = PR.rows(PR.param_bytes(files, 'EquipParamWeapon'), WEAPON_FIELDS)[0]
        names = T._fmg('WeaponName')
        self.weapons, self.weapon_by_name = {}, {}
        for r in wrows:
            nm = names.get(r['id'])
            if r['id'] % 10000 or not nm or nm.startswith('[ERROR]'):
                continue
            self.weapons[r['id']] = dict(r, name=nm)
            self.weapon_by_name.setdefault(T._norm(nm), r['id'])

    # -- the spell --------------------------------------------------------------------------

    def spell(self, name):
        mid = name if isinstance(name, int) else self.spell_by_name.get(T._norm(name))
        if mid is None or mid not in self.magic:
            raise KeyError(f'no player spell named {name!r}')
        m = self.magic[mid]
        nm = PR.row_names('Magic').get(mid, str(mid)).split('] ', 1)[-1]
        return {'id': mid, 'name': nm, 'context': m['spEffectCategory'], 'lo': m['analogDexterityMin'],
                'hi': m['analogDexterityMax'], 'tae': 400 + m['refType'],
                'subcats': {m[f'subCategory{i}'] for i in (1, 2)} - {0},
                'elements': self._elements(m), 'clips': self._clips(400 + m['refType']),
                'requires': {k: m[f] for k, f in SPELL_REQUIRE.items() if m[f]}}

    def _elements(self, m):
        """Share of each element in the damage of every bullet the spell's first ref reaches."""
        if m['refCategory1'] != 1:
            return {}
        tot = dict.fromkeys(AFFIX, 0.0)
        seen, todo = set(), [m['refId1']]
        while todo:
            b = todo.pop()
            if b in seen or b not in self.bullet:
                continue
            seen.add(b)
            r = self.bullet[b]
            a = self.atk.get(r['atkId_Bullet'])
            if a:
                for e, f in AFFIX.items():
                    tot[e] += max(a[f], 0)
            todo += [r[k] for k in ('HitBulletID', 'intervalCreateBulletId') if r[k] > 0]
        s = sum(tot.values())
        return {e: v / s for e, v in tot.items() if v} if s else {}

    def _clips(self, cat):
        """[{start, release, open, spawn}] in real frames: each start clip with a cast-speed window
        and the release clip after it (`open` = (earliest, latest) opening of JumpTable 93)."""
        tab = A.tae_animations(cat) or {}
        out = []
        for anim in sorted(tab):
            _, _, ev = A.resolve_events(cat, anim)
            win = [e for e in ev or () if e.type == A.TAE_JUMP_TABLE_EARLY
                   and struct.unpack_from('<hh', e.params, 0) == (CAST_START_JUMP, EARLY_CASTING_SPEED)]
            if not win:
                continue
            _, _, rel = A.resolve_events(cat, anim + 10)
            casts = [e for e in rel or () if e.type == TAE_CAST]
            if not casts:
                continue
            real, rreal = A.clip_to_real(ev), A.clip_to_real(rel)
            e = win[0]
            out.append({'start': anim, 'release': anim + 10, 'event': e, 'real': real,
                        'spawn': min(rreal(c.start) for c in casts) * A.TAE_FPS})
        return out

    def startup(self, spell, value, clip=None):
        """Real frames from the start clip's first frame to the cast, at casting value `value`."""
        c = self._clip(spell, clip)
        # The id opens from progress `value` (weights 0 -> 1): `_early_interval` with that threshold.
        iv = A._early_interval(c['event'], value)
        opened = c['event'].end if iv is None else iv[0]
        return c['real'](opened) * A.TAE_FPS + c['spawn']

    def _clip(self, spell, clip):
        clips = spell['clips']
        if not clips:
            raise KeyError(f"{spell['name']} has no start clip with a cast-speed window")
        return next((c for c in clips if c['start'] == clip), clips[0]) if clip else clips[0]

    # -- what the build wears -------------------------------------------------------------

    def _rows_of(self, talismans, held):
        """(source, raw SpEffect row) for every talisman SpEffect and every held weapon's residents.
        A weapon held twice contributes twice (category-10 residents stack, see the module doc)."""
        rows = []
        groups = set()
        for key in talismans:
            tal = self.t.get(key)
            if tal is None:
                raise KeyError(f'no talisman named {key!r}')
            if tal.group in groups:
                continue
            groups.add(tal.group)
            rows += [(tal.name, self.t.sp[s.id]) for s in tal.speffects]
        for w in held:
            if not w:
                continue
            wid = self.weapon_id(w)
            for k in ('residentSpEffectId', 'residentSpEffectId1', 'residentSpEffectId2'):
                sid = self.weapons[wid][k]
                if sid > 0 and sid in self.t.sp:
                    rows.append((self.weapons[wid]['name'], self.t.sp[sid]))
        return rows

    def weapon_id(self, w):
        wid = w if isinstance(w, int) else self.weapon_by_name.get(T._norm(w))
        if wid not in self.weapons:
            raise KeyError(f'no weapon named {w!r}')
        return wid

    def _applies(self, spell, r):
        flag = SPELL_CONTEXT_FLAG.get(spell['context'])
        if flag is None or not r.get(flag):
            return False
        sc = {r.get(f'magicSubCategoryChange{i}', 0) for i in (1, 2, 3)} - {0}
        if sc and not sc & spell['subcats']:
            return False
        row = _Row(r['id'], r)
        if T._hp_gate(r, 1.0) or T._timed_gate(row, set()) or r['id'] in T.STAGE_OF:
            return False
        return True

    def evaluate(self, spell, stats, talismans=(), right=None, left=None, clip=None):
        """Damage, cast speed and incoming damage of one loadout casting `spell`."""
        rows = self._rows_of(talismans, (right, left))
        bonus = sum(r.get(CAST_SPEED_FIELD, 0) for _, r in rows)
        dex = stats.get('dex', 10)
        v = casting_value(dex, bonus, spell['lo'], spell['hi'])
        rate = {e: 1.0 for e in spell['elements']}
        correct = dict(rate)
        applied = []
        for src, r in rows:
            if not self._applies(spell, r):
                continue
            for e in rate:
                x = r.get(T.ATTACK_RATE[e], 1.0)
                c = r.get('atkPlayerDmgCorrectRate_' + T.EL_SUFFIX[e], 1.0)
                if x != 1.0 or c != 1.0:
                    rate[e] = T.f32(rate[e] * T.f32(x))
                    correct[e] *= c
                    applied.append((src, r['id'], e, round(x * c, 4)))
        # `*AttackRate` reaches the damage as a whole percent (FUN_140d278a0, talismans.py).
        damage = sum(w * int(T.f32(rate[e] * T.f32(100.0))) / 100.0 * correct[e]
                     for e, w in spell['elements'].items()) or 1.0
        inc = T.defender_modifiers(self.t, [x for x in talismans])
        incoming = inc['damage_taken']['physical'] * inc['absorption_mult']['standard']
        startup = self.startup(spell, v, clip)
        slowest = self.startup(spell, 1.0, clip)
        return {'bonus': bonus, 'effective_dex': min(max(dex + bonus, spell['lo']), spell['hi']), 'value': v,
                'startup': round(startup, 2), 'frames_saved': round(slowest - startup, 2),
                'damage': round(damage, 4), 'incoming': round(incoming, 4),
                'score': round(damage * slowest / startup / incoming, 4), 'applied': applied}

    # -- choosing ----------------------------------------------------------------------------

    def meets(self, wid, stats):
        w = self.weapons[wid]
        return all(stats.get(k, 0) >= w[f] for k, f in REQUIRE.items())

    def can_cast(self, spell, w):
        flag = CATALYST_FLAG.get(spell['context'])
        return bool(flag and self.weapons[self.weapon_id(w)][flag])

    def _relevant_rows(self, spell, rows):
        return any(r.get(CAST_SPEED_FIELD, 0) or self._applies(spell, r) and any(
            r.get(T.ATTACK_RATE[e], 1.0) != 1.0 or r.get('atkPlayerDmgCorrectRate_' + T.EL_SUFFIX[e], 1.0) != 1.0
            for e in spell['elements']) for r in rows)

    def offhand_candidates(self, spell, stats):
        """Weapons whose residents change this spell's speed or damage and that the stats wield."""
        out = []
        for wid, w in self.weapons.items():
            rows = [self.t.sp[w[k]] for k in ('residentSpEffectId', 'residentSpEffectId1', 'residentSpEffectId2')
                    if w[k] > 0 and w[k] in self.t.sp]
            if rows and self._relevant_rows(spell, rows) and self.meets(wid, stats):
                out.append(w['name'])
        return out

    def talisman_candidates(self, spell):
        out = []
        for tal in self.t:
            rows = [self.t.sp[s.id] for s in tal.speffects]
            if self._relevant_rows(spell, rows):
                out.append(tal.name)
        return out

    def best_loadout(self, spell, stats, catalyst=None, left=None, talismans=(), slots=4, clip=None):
        """The off-hand (unless `left` is given) and then talismans, one at a time, each the pick that
        raises `score` most; stops when nothing raises it. `talismans` are kept and count to `slots`."""
        if isinstance(spell, str):
            spell = self.spell(spell)
        chosen = list(talismans)
        if left is None:
            best = (self.evaluate(spell, stats, chosen, catalyst, None, clip)['score'], None)
            for w in self.offhand_candidates(spell, stats):
                s = self.evaluate(spell, stats, chosen, catalyst, w, clip)['score']
                if s > best[0] + MIN_GAIN:
                    best = (s, w)
            left = best[1]
        pool = self.talisman_candidates(spell)
        while len(chosen) < slots:
            cur = self.evaluate(spell, stats, chosen, catalyst, left, clip)['score']
            groups = {self.t.get(x).group for x in chosen}
            top = None
            for name in pool:
                if name in chosen or self.t.get(name).group in groups:
                    continue
                s = self.evaluate(spell, stats, chosen + [name], catalyst, left, clip)['score']
                if s > cur + MIN_GAIN and (top is None or s > top[0]):
                    top = (s, name)
            if top is None:
                break
            chosen.append(top[1])
        res = self.evaluate(spell, stats, chosen, catalyst, left, clip)
        return dict(res, spell=spell['name'], catalyst=catalyst, left=left, talismans=chosen,
                    added=[x for x in chosen if x not in talismans])

    def options(self, spell, stats, catalyst=None, left=None, talismans=(), clip=None):
        """Each single change against the given loadout: (what, frames saved, damage, incoming, score)."""
        base = self.evaluate(spell, stats, list(talismans), catalyst, left, clip)
        rows = []
        for w in self.offhand_candidates(spell, stats):
            if w != left:
                rows.append((f'off-hand {w}', self.evaluate(spell, stats, list(talismans), catalyst, w, clip)))
        groups = {self.t.get(x).group for x in talismans}
        for name in self.talisman_candidates(spell):
            if name not in talismans and self.t.get(name).group not in groups:
                rows.append((f'talisman {name}', self.evaluate(spell, stats, list(talismans) + [name], catalyst,
                                                               left, clip)))
        dex = stats.get('dex', 10)
        for d in (12, 24, 36):
            if dex + d <= 99:
                rows.append((f'DEX +{d}', self.evaluate(spell, dict(stats, dex=dex + d), list(talismans),
                                                         catalyst, left, clip)))
        rows.sort(key=lambda r: -r[1]['score'])
        return base, rows


def parse_stats(text):
    out = {}
    for part in filter(None, (text or '').split(',')):
        k, v = part.split('=')
        out[k.strip()] = int(v)
    return out


def selftest():
    m = CastModel()
    n = [0]

    def check(name, ok):
        n[0] += 1
        if not ok:
            sys.exit(f'FAIL {name}')

    check('slowest at DEX 10', casting_value(10, 0, 10, 70) == 1.0)
    check('fastest at 70', casting_value(70, 0, 10, 70) == 0.0)
    check('bonus counts', casting_value(40, 30, 10, 70) == 0.0)
    check('half way', casting_value(40, 0, 10, 70) == 0.5)
    check('flat range', casting_value(40, 0, 20, 20) == 0.0)
    gf = m.spell('Giantsflame Take Thee')
    cf = m.spell('Catch Flame')
    check('Giantsflame is a417, subcategory 21, fire', gf['tae'] == 417 and gf['subcats'] == {21}
          and set(gf['elements']) == {'fire'})
    check('Giantsflame start 64..69', (round(m.startup(gf, 0.0)), round(m.startup(gf, 1.0))) == (64, 69))
    check('Catch Flame start 15..20', (round(m.startup(cf, 0.0)), round(m.startup(cf, 1.0))) == (15, 20))
    st = {'dex': 10, 'str': 40, 'fth': 60}
    seal = "Giant's Seal"
    r = m.evaluate(gf, st, ['Radagon Icon'], seal)
    check('Radagon Icon at DEX 10 saves 2.5 frames', r['frames_saved'] == 2.5 and r['bonus'] == 30)
    check('one seal x1.2', m.evaluate(gf, st, (), seal)['damage'] == 1.2)
    check('two seals x1.44', m.evaluate(gf, st, (), seal, seal)['damage'] == 1.44)
    check("Catch Flame is not a giant's flame", m.evaluate(cf, st, (), seal, seal)['damage'] == 1.0)
    az = m.evaluate(gf, st, (), seal, "Azur's Glintstone Staff")
    check("Azur's staff: +40 and no damage", az['bonus'] == 40 and az['damage'] == 1.2)
    check("Azur's staff casts no incantation", not m.can_cast(gf, "Azur's Glintstone Staff") and m.can_cast(gf, seal))
    check('Beloved Stardust costs x1.3 taken', m.evaluate(gf, st, ['Beloved Stardust'], seal)['incoming'] == 1.3)
    check("Flock's Canvas reaches incantations", m.evaluate(gf, st, ["Flock's Canvas Talisman"], seal)['damage']
          == 1.29)
    pebble = m.spell('Glintstone Pebble')
    check("Flock's Canvas does not reach sorceries",
          m.evaluate(pebble, st, ["Flock's Canvas Talisman"])['damage'] == 1.0)
    best = m.best_loadout(gf, st, catalyst=seal)
    check('the rule holds a second seal for Giantsflame', best['left'] == seal)
    # Cast speed only takes a slot no damage talisman wants, and never at Stardust's x1.3 taken.
    tl = best['talismans']
    check('damage before cast speed', "Flock's Canvas Talisman" in tl and 'Radagon Icon' in tl
          and tl.index("Flock's Canvas Talisman") < tl.index('Radagon Icon') and 'Beloved Stardust' not in tl)
    fire = m.evaluate(gf, st, ['Fire Scorpion Charm'], seal)
    check('Fire Scorpion: x1.08 dealt, x1.15 taken', fire['damage'] == 1.296 and fire['incoming'] == 1.15)
    print(f'selftest ok ({n[0]} checks)')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument('spell', nargs='?')
    ap.add_argument('--stats', default='', help='e.g. str=30,dex=12,fth=60 (unset stats are 0, DEX 10)')
    ap.add_argument('--catalyst', help='the right-hand catalyst that casts it')
    ap.add_argument('--left', help='the off-hand, fixed (default: chosen by the rule)')
    ap.add_argument('--talismans', default='', help='talismans already worn, comma-separated')
    ap.add_argument('--slots', type=int, default=4)
    ap.add_argument('--clip', type=int, help='start clip id (default: the lowest)')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.spell:
        ap.error('a spell name is required')
    m = CastModel()
    spell = m.spell(a.spell)
    stats = parse_stats(a.stats)
    tals = [x.strip() for x in a.talismans.split(',') if x.strip()]
    if a.catalyst and not m.can_cast(spell, a.catalyst):
        sys.exit(f"{a.catalyst} cannot cast {spell['name']}")
    best = m.best_loadout(spell, stats, a.catalyst, a.left, tals, a.slots, a.clip)
    base, rows = m.options(spell, stats, a.catalyst, a.left, tals, a.clip)
    if a.json:
        print(json.dumps({'best': best, 'base': base, 'options': [{'change': w, **r} for w, r in rows]}, indent=1))
        return 0
    print(f"{spell['name']}: start {m.startup(spell, 0.0):g}..{m.startup(spell, 1.0):g} frames, "
          f"one frame per {(spell['hi'] - spell['lo']) / (m.startup(spell, 1.0) - m.startup(spell, 0.0)):g} "
          f"effective DEX up to {spell['hi']}")
    print(f"now: {base['startup']:g} frames, damage x{base['damage']:g}, taken x{base['incoming']:g}, "
          f"score {base['score']:g}")
    print(f"{'change':44s} {'frames':>6s} {'damage':>7s} {'taken':>6s} {'score':>6s}")
    for what, r in rows:
        print(f"{what:44s} {r['frames_saved'] - base['frames_saved']:>+6.2f} {r['damage']:>7.3f} "
              f"{r['incoming']:>6.3f} {r['score']:>6.3f}")
    print(f"\nrule: off-hand {best['left'] or 'nothing that helps'}; talismans {', '.join(best['talismans']) or 'none'}"
          f" -> score {best['score']:g} (damage x{best['damage']:g}, {best['frames_saved']:g} frames saved, "
          f"taken x{best['incoming']:g})")
    return 0


if __name__ == '__main__':
    sys.exit(main())
