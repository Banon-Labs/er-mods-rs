#!/usr/bin/env python3
"""Attack-spell damage: what one cast of a sorcery or incantation does to a player, and the FP and
frames it costs, for one build's stats and catalyst.

    python3 scripts/er-mechanics-spells.py --stats int=80,dex=20 --catalyst "Carian Regal Scepter"
    python3 scripts/er-mechanics-spells.py --stats fth=60,str=20 --catalyst "Erdtree Seal" \\
        --talismans "Flock's Canvas Talisman" --sort full_per_fp
    python3 scripts/er-mechanics-spells.py --stats int=60 --catalyst "Glintstone Staff+25" --show "Rock Sling"
    python3 scripts/er-mechanics-spells.py --selftest

Labels as the sibling docs use them: `VERIFIED` = read in the 1.16.2 named dump (addresses are
1.16.2), `REGULATION` = read from the installed params, `TAE` = decoded TimeAct, `INFERRED` = a
reading whose consumer was not traced.

# One hit (`VERIFIED`)

`FUN_1406832a0`, the per-hit attack power (the vtable slot `FUN_140651d40` calls for a player),
has two branches on `AttackInfo+0xf4`, which the dump names `attackNotFromWeapon`:

    set:    AR[e] = AtkParam.atk<e> x FUN_140690390(weaponParamId1, e) x durability x ctx-rate[e] + ctx-add[e]
    clear:  AR[e] = ((base[e] of both hands) x AtkParam.atk<e>Correction / 100 + isAddBaseAtk term)
                    x FUN_140690390(...) x ...   (the weapon hit attack-rating.md section 1 models)

`FUN_140690390` is the catalyst's own multiplier for element `e`: its `attackElementCorrectId` row,
replaced by the hit's `AtkParam.overwriteAttackElementCorrectId` when that is >= 0, that element's
`correctType_*` graph, STR x1.5 when `AttackInfo.isTwoHanding` is set and the AtkParam's
`isDisableBothHandsAtkBonus` is not, and the 0.6 penalty on an unmet requirement. That is
`er-mechanics-ar.element_multiplier`, which `spell_buff` already calls for the magic and holy rows
(Glintstone Staff +25 at INT 80 is 329.5, attack-rating.md section 4). So a fire incantation scales
with the seal's fire row, not its holy row, and the AtkParam correction fields are not read.
`durability` is `ItemEntryInfo::GetDurabilityConditionMultiplier`, 1.0 at full durability (the
caller passes it as `param_5`). For a bullet, `weaponParamId1` is the equipment in the casting
hand (`FUN_14038e380`: `GetEquipmentEntry(spawn+0x2c)`), so it is the catalyst.

That a spell's bullet takes the first branch is `INFERRED`: no store to `+0xf4` (or to the
bullet's copy at `BulletAttackInfo+0x28+0xf4`) was found in the bullet builders, and
`InitAttackStruct` clears it. The other branch cannot be the one a spell uses, though: no
catalyst has a fire or lightning `attackBase*` (the elemental ones are Erdtree Seal's 15 holy and
Carian Sorcery Sword's 69 magic) and the only player spells whose AtkParams set `isAddBaseAtk` are
Furious Blade of Ansbach and Scarlet Aeonia, so that branch would put no fire or lightning on
Lightning Spear or Black Flame (`--selftest` checks all three over the regulation). Those two are
scored on the flat branch like the rest, which for them is `INFERRED` twice over.

On top come the attacker's SpEffect `*AttackRate` and `atkPlayerDmgCorrectRate_*` rows the spell's
context admits (`Magic.spEffectCategory` -> `magParamChange` / `miracleParamChange`, then
`magicSubCategoryChange1..3` against the spell's subcategories), exactly as
`er-mechanics-cast-speed.CastModel` applies them for talismans and the held weapons' residents.
Then the defense step is `er-mechanics-defense.damage` with the hit's own
`FinalDamageRateParam` row (`AtkParam.finalDamageRateId`, applied when both sides are players,
`VERIFIED` 0x140686070). The physical type is the bullet's `atkAttribute` unless 254, else the
AtkParam's, 253/252 taking the catalyst's (defense.md section 2b).

# One cast (`TAE` + `REGULATION`)

The spell's TimeAct is `a<400 + Magic.refType>`. Clip `045000` is the start clip, which hands off
at the opening of JumpTable 93 (the cast-speed window, `er-mechanics-cast-speed`); `045010` is the
release; a spell with `Magic.mp_charge` > 0 is chargeable, and then `045010` is the charged release
and `045011` the tap. The data settles which is which: on every chargeable spell checked the
`045010` ref carries the larger hit (Lightning Spear 293 against 234, Comet 365 against 292). TAE
type 64 `CastHighlightedMagic` fires `Magic.refId<n>`, n being its byte 8 plus one (COMMUNITY:
WitchyBND `TAE.Template.ER.xml` names it `RefType`). A ref is an `AtkParam_Pc` row hit directly
(`refCategory` 0, Carian Slicer), a `Bullet` row (1), or a SpEffect (2, no damage here).

Hits on one target are counted two ways, since no param says how many of a volley land on a moving
player:

- `one_hit`: the largest single hit the cast can land, the value of one connect.
- `full`: every hit the cast can put on a player standing in its path, counted per hit list. A
  target is hit once per list, and again every `dmgHitRecordLifeTime` s while a penetrating or
  endless member still touches it. A moving bullet touches it for the time it takes to cross the
  player's idle depth (0.564 m, reach.md section 3) plus its own diameter, at its slower of
  `initVellocity` / `maxVellocity`. A bullet that stands still or follows its owner (`FollowType`)
  touches it for its whole `life`. A trail child adds its life to its parent's pass. A bullet with
  `isUseSharedHitList` joins its parent's list, or, when the cast fires it, gives its `numShoot`
  volley one list; any other bullet has a list of its own. Children: `HitBulletID` one per parent
  bullet when it hits or expires, `intervalCreateBulletId` one every mean interval over
  `life - intervalCreateWaitTime`. These are the paramdef descriptions read as rules
  (`INFERRED`). The sharing reading is the one the data asks for: Glintstone Arc's eight bullets
  all carry the same AtkParam, `isUseSharedHitList` and a 99 s record, which is one hit, not
  eight. A spread volley without a shared list counts all its bullets, which makes `full` an upper
  bound for a spread. A channelled spell (one with a `045012` loop) counts its release clip only.

FP is `Magic.mp` for a tap and `mp_charge` for a charged cast (`INFERRED` from the paramdef name;
the two are equal on every chargeable spell in the list above). Frames run from the press:
`startup` is the hand-off at this build's casting value plus the release clip's first damaging
cast event (bullet travel is not counted, it depends on distance), and `act` is the hand-off plus
the release clip's first R1 or roll gate (`er-mechanics-disengage.clip`).

# The kit: how many spells (`--kit`)

One damage number per cast always picks one spell. A second spell earns its slots only where the
first does not reach, and the situations that decide it here are punishes:

- Each build of the RL window's opponent pool (`er-mechanics-exchange.opponent_pool`) throws its R1
  #1 and misses at its own world reach (`er-mechanics-neutral.pool_reaches`). The caster presses at
  the end of its last hit window (`INFERRED`: a whiff he spaced and planned to punish). The defender
  stands at that distance and cannot roll before his swing's dodge cancel (`TAE`); one situation per
  distinct (reach, recovery), weighted by the builds that throw it.
- From then on he rolls on reaction to the cast: the ash model's `reaction_outcome`
  (ashes-of-war.md section 16a: reaction lognormal around 0.25 s plus two 0.05 s network legs, the
  medium roll's four directions, `INFERRED` constants), held back to that dodge cancel.
- The cast's bullets fly straight ahead under the ash model's flight rules (`fly_tree`, section 15:
  homing off, the emit point 1 m ahead and 1 m up, `INFERRED`). A cast event that catches him first
  is worth its own hits and every later event's, at the `full` count.
- Kit value: the weighted mean over situations of the best member's landed damage. The search is
  the pouch's greedy: add the spell that raises it most while its `slotLength` fits the memory
  slots, stop when nothing raises it by more than `KIT_EPS`.

The rounds show where the gain flattens. A slot's real price is the buff it displaces, which only
`er-mechanics-pouch` weighs, so the count is not final until the kit is scored there. A spell that
hits an AtkParam directly (Carian Slicer) has no flight and is left out.

# Not established

- The writer of `AttackInfo+0xf4` for a local spell's bullet (above).
- How many hits of a spread or a lingering bullet land on a moving player; `one_hit` and `full`
  bracket it.
- Charged-spell boosts that are not a `*AttackRate` row (Godfrey Icon), and SpEffect rows that ask
  for the AtkParam's `spEffectAtkPowerCorrectRate_by*` (grease.md section 2): no row is applied
  beyond what `CastModel` applies.
- Whether a tap of a chargeable spell hands off at the same JumpTable 93 opening as the charge.
- For the kit: homing (the flight has no target), the caster's own reaction to the whiff, and every
  punish other than a whiffed R1 #1 (a blocked string, a roll read at range, a defender who
  commits instead of rolling).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _mod(name, fname):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, fname))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


AR = _mod('er_mechanics_ar', 'er-mechanics-ar.py')
CS = _mod('er_mechanics_cast_speed', 'er-mechanics-cast-speed.py')
DEF = _mod('er_mechanics_defense', 'er-mechanics-defense.py')
D = _mod('er_mechanics_disengage', 'er-mechanics-disengage.py')
ATK = CS.A
PR = CS.PR
T = CS.T

START, RELEASE, TAP, LOOP = 45000, 45010, 45011, 45012
SPELL_TAE_BASE = 400
TAE_CAST = CS.TAE_CAST
FPS = ATK.TAE_FPS
#: AtkParam element field per element name (`AR.ELEMENTS` order).
ATK_FIELD = {'physical': 'atkPhys', 'magic': 'atkMag', 'fire': 'atkFire', 'lightning': 'atkThun',
             'holy': 'atkDark'}
#: `atkAttribute` -> the physical absorption `er-mechanics-defense.damage` applies (defense.md 2b);
#: any other value meets no physical cut rate.
PHYS_TYPE = {0: 'slash', 1: 'strike', 2: 'pierce', 3: 'standard'}
ATK_FIELDS = list(ATK_FIELD.values()) + [
    'finalDamageRateId', 'overwriteAttackElementCorrectId', 'atkAttribute', 'isAddBaseAtk',
    'isDisableBothHandsAtkBonus']
BULLET_FIELDS = ['atkId_Bullet', 'HitBulletID', 'intervalCreateBulletId', 'intervalCreateTimeMin',
                 'intervalCreateTimeMax', 'intervalCreateWaitTime', 'numShoot', 'life',
                 'dmgHitRecordLifeTime', 'isPenetrateChr', 'isEndlessHit', 'isUseSharedHitList',
                 'atkAttribute', 'initVellocity', 'maxVellocity', 'hitRadius', 'hitRadiusMax', 'FollowType']
#: Front-to-back depth of a player standing idle: the farthest hurtbox surface ahead of the
#: origin (0.316, the forward foot) plus behind it (0.248, the rear calf), reach.md section 3
#: (`MEASURED`, `er-mechanics-reach.py --hurtbox`).
BODY_DEPTH = 0.316 + 0.248
MAGIC_FIELDS = ['refType', 'mp', 'mp_charge', 'spEffectCategory', 'slotLength', 'requirementIntellect',
                'requirementFaith', 'requirementLuck', 'enable_multi', 'enable_live', 'enable_hostile',
                'enable_friendly'] + [f'refCategory{i}' for i in range(1, 11)] + [f'refId{i}' for i in range(1, 11)]
REF_ATTACK, REF_BULLET = 0, 1
#: The `EquipParamWeapon` flag a catalyst needs, by the Magic row name's school. Not
#: `Magic.spEffectCategory`, which is the SpEffect context (`er-mechanics-cast-speed`) and is 3 on
#: some incantations: Briars of Punishment.
SCHOOL_FLAG = {'[Sorcery]': 'enableMagic', '[Incantation]': 'enableMiracle'}
#: A bullet tree deeper than this is a loop in the data, not a spell.
MAX_DEPTH = 8
#: RL window of the median defender, either side of `--rl`.
WINDOW = 10


class SpellModel:
    """One regulation read: player spells, the bullets and AtkParams they reach, their TimeAct."""

    def __init__(self, regulation=None):
        self.ar = AR.Tables(regulation)
        self.cast = CS.CastModel(regulation)
        files = self.cast.t._files

        def by_id(stem, fields):
            return {r['id']: r for r in PR.rows(PR.param_bytes(files, stem), fields)[0]}

        self.magic = by_id('Magic', MAGIC_FIELDS)
        self.bullet = by_id('Bullet', BULLET_FIELDS)
        self.atk = by_id('AtkParam_Pc', ATK_FIELDS)
        self.final_rate = by_id('FinalDamageRateParam', None)
        self.names, self.school = {}, {}
        for mid, nm in PR.row_names('Magic').items():
            if mid in self.magic and nm and nm.startswith(('[Sorcery] ', '[Incantation] ')):
                self.names[mid] = nm.split('] ', 1)[1]
                self.school[mid] = SCHOOL_FLAG[nm.split('] ', 1)[0] + ']']
        self.by_name = {}
        for mid, nm in sorted(self.names.items()):
            self.by_name.setdefault(T._norm(nm), mid)
        self._defender = {}
        self._ash = None

    # -- the catalyst ------------------------------------------------------------------------

    def catalyst(self, text, level=None):
        """(weapon row id at its level, base row, reinforce row, name) for 'Name' or 'Name+10'."""
        m = re.match(r'^(.*?)(?:\s*\+\s*(\d+))?$', text.strip())
        name, lvl = m.group(1), m.group(2)
        base = self.ar.find_weapon(name, 'Standard')
        wep = self.ar.weapons[base]
        top = self.ar.max_level(wep['reinforceTypeId'])
        lvl = top if lvl is None and level is None else int(lvl if lvl is not None else level)
        if not 0 <= lvl <= top:
            raise SystemExit(f'{name} has levels 0..{top}')
        return {'id': base + lvl, 'base': base, 'wep': wep, 'level': lvl, 'name': self.ar.names.get(base, name),
                'reinf': self.ar.reinforce[wep['reinforceTypeId'] + lvl]}

    def multiplier(self, cat, element, stats, atk_row=None, two_handed=False):
        """`FUN_140690390` for one element of a spell hit: the catalyst's element multiplier, with
        the AtkParam's AECP overwrite and two-handing bonus."""
        wep, reinf = cat['wep'], cat['reinf']
        aecp_id = wep['attackElementCorrectId']
        if atk_row and atk_row.get('overwriteAttackElementCorrectId', -1) >= 0:
            aecp_id = atk_row['overwriteAttackElementCorrectId']
        two = two_handed and not (atk_row or {}).get('isDisableBothHandsAtkBonus')
        _, suf, _, _, gfield = next(e for e in AR.ELEMENTS if e[0] == element)
        return AR.element_multiplier(self.ar, wep, reinf, self.ar.aecp.get(aecp_id, {}), suf, wep[gfield],
                                     AR.normalise_stats(stats), AR.TWO_HAND_STR_MULT if two else 1.0)

    def can_cast(self, mid, cat):
        return bool(cat['wep'].get(self.school[mid]))

    def short(self, mid, stats):
        """{stat: shortfall} of the spell's own requirements; empty when it can be cast."""
        m = self.magic[mid]
        out = {}
        for k, f in (('int', 'requirementIntellect'), ('fth', 'requirementFaith'), ('arc', 'requirementLuck')):
            if stats.get(k, 0) < m[f]:
                out[k] = m[f] - stats.get(k, 0)
        return out

    # -- the cast ----------------------------------------------------------------------------

    def chargeable(self, mid):
        return self.magic[mid]['mp_charge'] > 0

    def _events(self, cat, anim):
        for c in (cat, SPELL_TAE_BASE):
            got = ATK.resolve_events(c, anim)
            if got and got[2]:
                return c, got[2]
        return None, None

    def variants(self, mid):
        cat = SPELL_TAE_BASE + self.magic[mid]['refType']
        out = []
        if self.chargeable(mid) and self._events(cat, TAP)[1]:
            out.append(('tap', TAP))
            out.append(('charged', RELEASE))
        else:
            out.append(('cast', RELEASE))
        return out

    def handoff(self, mid, value):
        """Real frames into the start clip at which it hands off to the release (JumpTable 93 at
        casting value `value`), or its whole length when it has no such window, or 0 without one."""
        cat = SPELL_TAE_BASE + self.magic[mid]['refType']
        c, ev = self._events(cat, START)
        if ev is None:
            return 0.0
        real = ATK.clip_to_real(ev)
        for e in ev:
            if e.type == ATK.TAE_JUMP_TABLE_EARLY and struct.unpack_from('<hh', e.params, 0) == (
                    CS.CAST_START_JUMP, CS.EARLY_CASTING_SPEED):
                iv = ATK._early_interval(e, value)
                return real(e.end if iv is None else iv[0]) * FPS
        info = D.clip(c, START)
        return float(info['length']) if info and info.get('length') else 0.0

    def release_events(self, mid, anim):
        """[(real frame in the release clip, ref index 1..10)] for the TAE 64 events it carries."""
        cat = SPELL_TAE_BASE + self.magic[mid]['refType']
        c, ev = self._events(cat, anim)
        if ev is None:
            return c, []
        real = ATK.clip_to_real(ev)
        return c, sorted((real(e.start) * FPS, struct.unpack_from('<b', e.params, 8)[0] + 1)
                         for e in ev if e.type == TAE_CAST)

    def ref_hits(self, mid, n):
        """[(atk id, bullet id or None, count)] one fired `refId<n>` can put on a target that stays
        in it (the `full` rule in the module doc), one entry per hit list."""
        m = self.magic[mid]
        kind, rid = m[f'refCategory{n}'], m[f'refId{n}']
        if rid <= 0:
            return []
        if kind == REF_ATTACK:
            return [(rid, None, 1.0)] if self._damaging(rid) else []
        if kind != REF_BULLET:
            return []
        lists = []

        def walk(b, instances, joined, before, depth):
            r = self.bullet.get(b)
            if r is None or depth > MAX_DEPTH or instances <= 0:
                return
            shots = max(r['numShoot'], 1)
            life = max(r['life'], 0.0)
            if r['isUseSharedHitList'] and joined is not None:
                own = joined
            else:
                # Its own list: one for every bullet, or one for the volley when it shares.
                own = {'mult': instances * (1 if r['isUseSharedHitList'] else shots), 'rec': r['dmgHitRecordLifeTime'],
                       'contact': 0.0, 'best': None}
                lists.append(own)
                before = 0.0
            touch = before + self.contact(r)
            if self._damaging(r['atkId_Bullet']):
                own['contact'] = max(own['contact'], touch)
                if own['best'] is None or self._base(r['atkId_Bullet']) > self._base(own['best'][0]):
                    own['best'] = (r['atkId_Bullet'], b)
            if r['HitBulletID'] > 0:
                walk(r['HitBulletID'], instances * shots, own, 0.0, depth + 1)
            iv = (r['intervalCreateTimeMin'] + r['intervalCreateTimeMax']) / 2
            if r['intervalCreateBulletId'] > 0 and iv > 0 and life > 0:
                k = 1 + int(max(life - r['intervalCreateWaitTime'], 0.0) // iv)
                # A trail: the children dropped while the parent touched the target outlast it.
                walk(r['intervalCreateBulletId'], instances * shots * k, own, touch, depth + 1)

        walk(rid, 1, None, 0.0, 0)
        out = []
        for li in lists:
            if li['best'] is None:
                continue
            rec = li['rec']
            hits = 1 + int(li['contact'] // rec) if rec > 0 and li['contact'] > rec else 1
            out.append((li['best'][0], li['best'][1], float(li['mult'] * hits)))
        return out

    def contact(self, r):
        """Seconds one bullet can touch a target standing in its path: 0 unless it penetrates or
        is endless (it ends on the hit); its whole life when it stands still or follows its owner;
        else the time to pass through the target's depth plus its own diameter."""
        if not (r['isPenetrateChr'] or r['isEndlessHit']):
            return 0.0
        life = max(r['life'], 0.0)
        speeds = [v for v in (r['initVellocity'], r['maxVellocity']) if v > 0]
        if r['FollowType'] or not speeds:
            return life
        return min(life, (2 * max(r['hitRadius'], r['hitRadiusMax']) + BODY_DEPTH) / min(speeds))

    def _base(self, atk_id):
        a = self.atk.get(atk_id)
        return sum(a[f] for f in ATK_FIELD.values()) if a else 0

    def _damaging(self, atk_id):
        a = self.atk.get(atk_id)
        return bool(a) and any(a[f] > 0 for f in ATK_FIELD.values())

    def phys_type(self, atk_id, bullet_id, cat):
        attr = self.atk.get(atk_id, {}).get('atkAttribute', 3)
        b = self.bullet.get(bullet_id) if bullet_id is not None else None
        if b is not None and b['atkAttribute'] != 254:
            attr = b['atkAttribute']
        if attr == 253:
            attr = cat['wep']['atkAttribute']
        elif attr == 252:
            attr = cat['wep']['atkAttribute2']
        return PHYS_TYPE.get(attr, 'none')

    # -- what the build wears ---------------------------------------------------------------

    def rates(self, mid, cat, talismans=(), left=None):
        """({element: attack rate}, {element: PvP correct rate}, casting-speed bonus) from the
        talismans and held weapons' residents this spell's context admits (`CastModel`)."""
        sp = self.cast.spell(mid)
        rows = self.cast._rows_of(talismans, (cat['name'], left))
        bonus = sum(r.get(CS.CAST_SPEED_FIELD, 0) for _, r in rows)
        rate = {e: 1.0 for e in ATK_FIELD}
        correct = dict(rate)
        for _, r in rows:
            if not self.cast._applies(sp, r):
                continue
            for e in rate:
                rate[e] = T.f32(rate[e] * T.f32(r.get(T.ATTACK_RATE[e], 1.0)))
                correct[e] *= r.get('atkPlayerDmgCorrectRate_' + T.EL_SUFFIX[e], 1.0)
        # `*AttackRate` reaches the damage as a whole percent (FUN_140d278a0, talismans.py).
        rate = {e: int(T.f32(v * T.f32(100.0))) / 100.0 for e, v in rate.items()}
        return rate, correct, bonus

    def defender(self, rl):
        if rl not in self._defender:
            opt = _mod('er_builds_optimize', 'er-builds-optimize.py')
            self._defender[rl] = opt.bracket_defender(opt.corpus_rows(opt.CACHE / 'builds.jsonl', rl - WINDOW,
                                                                      rl + WINDOW))
        return self._defender[rl]

    # -- the number --------------------------------------------------------------------------

    def hit(self, atk_id, bullet_id, cat, stats, rate, correct, defender, two_handed=False, pvp=True):
        """AR by element and damage of one hit on `defender`."""
        a = self.atk[atk_id]
        ar = {}
        for e, f in ATK_FIELD.items():
            if a[f] > 0:
                ar[e] = a[f] * self.multiplier(cat, e, stats, a, two_handed) * rate[e] * (correct[e] if pvp else 1.0)
        fr = self.final_rate.get(a['finalDamageRateId']) if pvp and a['finalDamageRateId'] >= 0 else None
        pt = self.phys_type(atk_id, bullet_id, cat)
        dmg = DEF.damage(ar, 100.0, defender, pt, fr)['total']
        return {'ar': {e: round(v, 2) for e, v in ar.items()}, 'damage': dmg, 'phys_type': pt,
                'final_rate': fr['magRate'] if fr else 1.0}

    def evaluate(self, mid, cat, stats, talismans=(), left=None, defender=None, two_handed=False, pvp=True):
        """Every variant of one spell: hits, one_hit, full, FP and frames."""
        rate, correct, bonus = self.rates(mid, cat, talismans, left)
        m = self.magic[mid]
        sp = self.cast.spell(mid)
        value = CS.casting_value(stats.get('dex', 10), bonus, sp['lo'], sp['hi'])
        hand = self.handoff(mid, value)
        out = []
        for label, anim in self.variants(mid):
            c, events = self.release_events(mid, anim)
            hits, first = [], None
            for frame, n in events:
                for atk_id, bid, count in self.ref_hits(mid, n):
                    h = self.hit(atk_id, bid, cat, stats, rate, correct, defender, two_handed, pvp)
                    hits.append(dict(h, ref=n, atk=atk_id, bullet=bid, count=count, frame=round(frame, 1)))
                    first = frame if first is None else min(first, frame)
            if not hits:
                continue
            info = D.clip(c, anim) if c is not None else None
            g = (info or {}).get('gates') or {}
            gate = min((v for v in (g.get('r1'), g.get('roll')) if v is not None),
                       default=(info or {}).get('length') or 0.0)
            fp = m['mp_charge'] if label == 'charged' and m['mp_charge'] > 0 else m['mp']
            full = sum(h['damage'] * h['count'] for h in hits)
            act = hand + gate
            out.append({'variant': label, 'fp': fp, 'startup': round(hand + first, 1), 'act': round(act, 1),
                        'one_hit': round(max(h['damage'] for h in hits), 1), 'full': round(full, 1),
                        'full_per_fp': round(full / fp, 2) if fp else None,
                        'full_per_s': round(full / (act / FPS), 1) if act > 0 else None,
                        'channel': bool(self._events(SPELL_TAE_BASE + m['refType'], LOOP)[1]),
                        'hits': hits})
        return out

    def table(self, cats, stats, talismans=(), left=None, rl=150, two_handed=False, pvp=True, only=None):
        """[(spell row, variant result)] for every player attack spell a catalyst casts and the stats
        meet; plus {name: why} for the ones left out."""
        dfn = self.defender(rl)
        rows, skipped = [], {}
        for mid, nm in sorted(self.names.items(), key=lambda x: x[1]):
            if only and T._norm(nm) not in only:
                continue
            m = self.magic[mid]
            if not m['enable_multi']:
                skipped[nm] = 'not usable in multiplayer'
                continue
            cat = next((c for c in cats if self.can_cast(mid, c)), None)
            if cat is None:
                skipped[nm] = 'no catalyst of its school'
                continue
            short = self.short(mid, stats)
            if short:
                skipped[nm] = 'short ' + ', '.join(f'{k} {v}' for k, v in short.items())
                continue
            try:
                got = self.evaluate(mid, cat, stats, talismans, left, dfn, two_handed, pvp)
            except KeyError as e:
                skipped[nm] = f'unreadable: {e}'
                continue
            if not got:
                if only:
                    skipped[nm] = 'no damaging hit'
                continue
            for v in got:
                rows.append(({'id': mid, 'name': nm, 'catalyst': cat['name'], 'slots': m['slotLength']}, v))
        return rows, skipped

    # -- the kit -----------------------------------------------------------------------------

    def ash(self):
        if self._ash is None:
            self._ash = (_mod('er_mechanics_ashes', 'er-mechanics-ashes.py'),)
            self._ash += (self._ash[0].AshTables(),)
        return self._ash

    def geometry(self, mid, variant):
        """(per damaging cast event in frame order: `contact_geometry` of its bullet tree flown
        straight ahead from the press, its real frame), or None when an event has no flight (a
        direct AtkParam hit, whose reach is not modelled here)."""
        A, t = self.ash()
        reach_mod = A._pose()[1]
        profile = reach_mod.defender_profile('idle')
        m = self.magic[mid]
        out = []
        for frame, n in variant['events']:
            if m[f'refCategory{n}'] != REF_BULLET:
                return None
            bt = A.bullet_tree(t, m[f'refId{n}'])
            if bt is None:
                return None
            pts = []
            start = (variant['hand'] + frame) / FPS
            for node, path, t0, _ in A.fly_tree(t, bt):
                if not A._bullet_damages_self(node):
                    continue
                # The segment to the next frame sampled every `CONTACT_STEP_M`, as a skill's is.
                for k, q in enumerate(path):
                    nxt = path[k + 1] if k + 1 < len(path) else q
                    steps = max(1, math.ceil(math.dist(q, nxt) / A.CONTACT_STEP_M))
                    for j in range(steps if k + 1 < len(path) else 1):
                        u = j / steps
                        p3 = [q[i] + (nxt[i] - q[i]) * u for i in range(3)]
                        d = reach_mod.contact_disc_radius(p3[1], node['radius_m'], profile)
                        if d is not None:
                            pts.append((start + t0 + (k + u) / A.BULLET_FPS, p3[0], p3[2], d))
            geom = A.contact_geometry(bullet_points=sorted(pts))
            if geom is not None:
                out.append((geom, frame))
        return out or None

    def punish_values(self, mid, cat, stats, situations, talismans=(), left=None, defender=None,
                      two_handed=False, pvp=True):
        """Per situation: the damage one cast lands on a defender caught in his recovery at the
        situation's distance (`kit` in the module doc), the best of the spell's variants; with the
        variant each one came from. None when no variant has a flight."""
        A, _ = self.ash()
        got = self.evaluate(mid, cat, stats, talismans, left, defender, two_handed, pvp)
        best, label = None, None
        for v in got:
            events = sorted({(h['frame'], h['ref']) for h in v['hits']})
            value = {f: sum(h['damage'] * h['count'] for h in v['hits'] if h['frame'] == f) for f, _ in events}
            geo = self.geometry(mid, {'events': events, 'hand': v['startup'] - min(f for f, _ in events)})
            if geo is None:
                continue
            geoms = [g for g, _ in geo]
            # Hit h first to catch him is worth itself and every later cast event.
            worth = [sum(value[f] for f in value if f >= fr) for _, fr in geo]
            vals = []
            for dist, floor in situations:
                o = A.reaction_outcome(geoms, worth, dist, cue=0.0, floor=floor)
                vals.append(o['value'])
            if best is None or sum(vals) > sum(best):
                best, label = vals, v['variant']
        return (best, label) if best is not None else None

    def kit(self, cats, stats, talismans=(), left=None, rl=150, two_handed=False, pvp=True, slots=None):
        """The greedy attack-spell kit by punish coverage (module doc): rounds of (spell, its
        slots, kit value after it, gain), and the spells left out with why."""
        slots = MEMORY_SLOTS if slots is None else slots
        sit = punish_situations(rl)
        pairs = [(s['reach'], s['recovery']) for s in sit]
        weight = [s['weight'] for s in sit]
        total = sum(weight)
        dfn = self.defender(rl)
        values, skipped = {}, {}
        rows, why = self.table(cats, stats, talismans, left, rl, two_handed, pvp)
        skipped.update(why)
        for mid in sorted({r[0]['id'] for r in rows}):
            cat = next(c for c in cats if self.can_cast(mid, c))
            got = self.punish_values(mid, cat, stats, pairs, talismans, left, dfn, two_handed, pvp)
            if got is None:
                skipped[self.names[mid]] = 'no bullet flight (reach not modelled)'
                continue
            values[mid] = got
        return greedy_kit({mid: v for mid, (v, _) in values.items()}, weight, total,
                          {mid: self.magic[mid]['slotLength'] for mid in values}, slots,
                          {mid: lab for mid, (_, lab) in values.items()}, self.names), skipped


#: Memory slots: `er-mechanics-pouch.MEMORY_SLOTS` (eight Memory Stones and Moon of Nokstella).
MEMORY_SLOTS = 10
#: Relative gain below which the kit search stops: `er-mechanics-pouch.EPS`.
KIT_EPS = 1e-6
#: Bump when `punish_situations` changes what it caches.
SITUATIONS_VERSION = 1


def greedy_kit(values, weight, total, slot_cost, slots, labels=None, names=None):
    """Add spells one at a time, each round the one that raises the kit value most, while it fits
    the memory slots and raises it by more than `KIT_EPS`. `values[spell]` lists its landed damage
    per situation, `weight` the situations' weights. Kit value: the weighted mean over situations
    of the best member's damage there."""
    best = [0.0] * len(weight)
    used, rounds, cur = 0, [], 0.0
    left = dict(values)
    while left:
        pick = None
        for s, v in left.items():
            if used + slot_cost[s] > slots:
                continue
            val = sum(w * max(b, x) for w, b, x in zip(weight, best, v)) / total
            if pick is None or val > pick[1] + 1e-12:
                pick = (s, val)
        if pick is None or pick[1] <= cur * (1.0 + KIT_EPS) + 1e-12:
            break
        s, val = pick
        v = left.pop(s)
        covers = sum(w for w, b, x in zip(weight, best, v) if x > b) / total
        best = [max(b, x) for b, x in zip(best, v)]
        used += slot_cost[s]
        rounds.append({'id': s, 'name': (names or {}).get(s, s), 'variant': (labels or {}).get(s),
                       'slots': slot_cost[s], 'value': round(val, 1), 'gain': round(val - cur, 1),
                       'best_share': round(covers, 3)})
        cur = val
    return rounds


def punish_situations(rl):
    """[{'reach', 'recovery', 'weight'}]: one per distinct (reach, recovery) of the RL window's
    opponent pool (`er-mechanics-exchange.opponent_pool`): his R1 #1 world reach
    (`er-mechanics-neutral.pool_reaches`) and the real frames from the end of its last hit window to
    its dodge cancel, weighted by the builds that throw it. Cached beside the pool."""
    EX = _mod('er_mechanics_exchange', 'er-mechanics-exchange.py')
    NEU = _mod('er_mechanics_neutral', 'er-mechanics-neutral.py')
    reg = ATK.Regulation(None)
    raw = EX.opponent_pool(reg, EX.CACHE / 'builds.jsonl', rl - WINDOW, rl + WINDOW)
    stamp = [SITUATIONS_VERSION] + list(raw['stamp'])
    path = EX.CACHE / f"spell-punish-{rl - WINDOW}-{rl + WINDOW}-{raw['stamp'][-1]}.json"
    if path.exists():
        got = json.loads(path.read_text())
        if got.get('stamp') == stamp:
            return got['situations']
    reaches = NEU.pool_reaches(raw)
    ids = EX.weapon_ids()
    count = {}
    for key, _ in raw['builds']:
        count[key] = count.get(key, 0) + 1
    merged = {}
    for key, n in sorted(count.items()):
        name, grip = key.rsplit('|', 1)
        two = grip == '2h'
        rows = ATK.weapon_attacks(reg, ids[name], 'both' if two else 'one')
        atk = next((r for r in rows if r['slot'] == ('2h_' if two else '') + EX.OPPONENT_SLOT), None)
        reach = (reaches.get(key) or {}).get('reach')
        if atk is None or not atk.get('hit_windows') or reach is None:
            continue
        end = max(w[1] for w in atk['hit_windows'])
        gate = atk['cancel_frame'].get('dodge')
        if gate is None:
            continue
        k = (round(reach, 1), round(max(gate - end, 0.0) * 2) / 2)
        merged[k] = merged.get(k, 0) + n
    out = [{'reach': r, 'recovery': f, 'weight': n} for (r, f), n in sorted(merged.items())]
    path.write_text(json.dumps({'stamp': stamp, 'situations': out}))
    return out


def parse_stats(text):
    out = {}
    for part in filter(None, (text or '').split(',')):
        k, v = part.split('=')
        out[k.strip().lower()] = int(v)
    return out


def selftest():
    m = SpellModel()
    n = [0]

    def check(name, ok):
        n[0] += 1
        if not ok:
            sys.exit(f'FAIL {name}')

    # The branch argument: the weapon branch would put no element on a spell.
    cats = [w for w in m.ar.weapons.values() if w.get('enableMagic') or w.get('enableMiracle')]
    check('catalysts exist', len(cats) > 20)
    check('no catalyst has a fire or lightning attackBase', all(
        w[f] == 0 for w in cats for f in ('attackBaseFire', 'attackBaseThunder')))
    reached, add_base = set(), set()
    for mid in m.names:
        for i in range(1, 11):
            for atk_id, _, _ in m.ref_hits(mid, i):
                reached.add(atk_id)
                if m.atk[atk_id]['isAddBaseAtk']:
                    add_base.add(m.names[mid])
    check('spells reach hundreds of damaging AtkParams', len(reached) > 200)
    check('isAddBaseAtk only on two spells', add_base == {'Furious Blade of Ansbach', 'Scarlet Aeonia'})
    check('fire and lightning spell hits exist', any(m.atk[a]['atkFire'] > 0 for a in reached)
          and any(m.atk[a]['atkThun'] > 0 for a in reached))
    # The multiplier is the spell buff attack-rating.md section 4 checked against `COMMUNITY`.
    staff = m.catalyst('Glintstone Staff+25')
    check('Glintstone Staff +25 INT 80 is 329.5', round(100 * m.multiplier(staff, 'magic', {'int': 80}), 2) == 329.5)
    seal = m.catalyst('Finger Seal+25')
    check('Finger Seal +25 FTH 60 is 286.75', round(100 * m.multiplier(seal, 'holy', {'fth': 60}), 2) == 286.75)
    check('unmet catalyst requirement is 0.6', round(m.multiplier(staff, 'magic', {'int': 5}), 4) == 0.6)
    pebble = m.by_name['glintstone pebble']
    h = m.hit(40000, 10400000, staff, {'int': 80}, {e: 1.0 for e in ATK_FIELD}, {e: 1.0 for e in ATK_FIELD},
              m.defender(150))
    check('Pebble AR is 152 x 3.295', h['ar'] == {'magic': round(152 * 3.295, 2)})
    # Clip roles, from the data.
    spear = m.by_name['lightning spear']
    check('Lightning Spear is chargeable', m.chargeable(spear) and [v for v, _ in m.variants(spear)] == ['tap', 'charged'])
    top = {lab: max(m.atk[a]['atkThun'] for _, ref in m.release_events(spear, anim)[1]
                    for a, _, _ in m.ref_hits(spear, ref)) for lab, anim in m.variants(spear)}
    check('charged spear hits harder than the tap', top == {'tap': 234, 'charged': 293})
    sling = m.by_name['rock sling']
    check('Rock Sling fires refs 1, 2, 3', [r for _, r in m.release_events(sling, RELEASE)[1]] == [1, 2, 3])
    check('Pebble is one cast', [v for v, _ in m.variants(pebble)] == ['cast'])
    slicer = m.by_name['carian slicer']
    check('Carian Slicer hits an AtkParam directly', m.ref_hits(slicer, 1) == [(44400, None, 1.0)])
    # A whole cast, end to end.
    got = m.evaluate(pebble, staff, {'int': 80, 'dex': 10}, defender=m.defender(150))
    check('Pebble: one variant, one hit, 7 FP', len(got) == 1 and len(got[0]['hits']) == 1 and got[0]['fp'] == 7)
    check('Pebble: one_hit == full', got[0]['one_hit'] == got[0]['full'] > 0)
    arc = m.by_name['glintstone arc']
    check('Glintstone Arc: eight bullets, one shared list, one hit', m.ref_hits(arc, 1) == [(40070, 10407000, 1.0)])
    comet = m.by_name['comet']
    check('charged Comet passes through once', [c for _, _, c in m.ref_hits(comet, 2)] == [1.0])
    still = dict(m.bullet[10403001])
    check('a standing bullet touches for its life', m.contact(still) == still['life'] and still['initVellocity'] == 0)
    # The kit: the greedy, the flight, and the recovery that holds the roll back.
    rounds = greedy_kit({'a': [10, 0], 'b': [0, 8], 'c': [5, 5]}, [1, 1], 2, {'a': 1, 'b': 1, 'c': 1}, 3)
    check('greedy: the second pick covers what the first misses, then it stops',
          [r['id'] for r in rounds] == ['a', 'b'] and rounds[-1]['value'] == 9.0)
    check('greedy: the slots cap the kit',
          [r['id'] for r in greedy_kit({'a': [10, 0], 'b': [0, 8]}, [1, 1], 2, {'a': 1, 'b': 2}, 2)] == ['a'])
    check('Carian Slicer has no flight', m.geometry(slicer, {'events': [(0.0, 1)], 'hand': 0.0}) is None)
    geo = m.geometry(pebble, {'events': m.release_events(pebble, RELEASE)[1], 'hand': 20.0})
    check('Pebble flies', geo is not None and len(geo) == 1)
    A, _ = m.ash()
    geoms = [g for g, _ in geo]
    free = A.reaction_outcome(geoms, [100.0], 6.0)
    held = A.reaction_outcome(geoms, [100.0], 6.0, floor=float(geoms[0]['T'].max()) + 1.0)
    check('a defender locked past the flight takes the cast', round(held['value'], 6) == held['base'] == 100.0)
    check('a free defender rolls some of it away', free['value'] < held['value'])
    print(f'selftest ok ({n[0]} checks)')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument('--stats', default='', help='e.g. int=80,dex=20,fth=8 (unset stats are 0, DEX 10)')
    ap.add_argument('--catalyst', action='append', default=[],
                    help="a staff or seal, 'Name' or 'Name+10' (default max level); repeatable")
    ap.add_argument('--left', help='the off-hand, e.g. a second seal')
    ap.add_argument('--talismans', default='', help='talismans worn, comma-separated')
    ap.add_argument('--two-handed', action='store_true', help='two-hand the catalyst (STR x1.5)')
    ap.add_argument('--rl', type=int, default=150, help=f'median defender of corpus builds within RL +-{WINDOW}')
    ap.add_argument('--pve', action='store_true', help='no PvP rates (FinalDamageRateParam, atkPlayerDmgCorrectRate)')
    ap.add_argument('--show', action='append', default=[], help='spell to break down hit by hit; repeatable')
    ap.add_argument('--sort', default='one_hit', choices=('one_hit', 'full', 'full_per_fp', 'full_per_s', 'startup'),
                    help='default one_hit: the only column no hit-count rule moves')
    ap.add_argument('--top', type=int, default=40)
    ap.add_argument('--kit', action='store_true', help='the greedy kit by punish coverage, spell by spell')
    ap.add_argument('--slots', type=int, default=MEMORY_SLOTS, help='memory slots the kit may fill')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.catalyst:
        ap.error('at least one --catalyst is required')
    m = SpellModel()
    stats = parse_stats(a.stats)
    cats = [m.catalyst(c) for c in a.catalyst]
    tals = [x.strip() for x in a.talismans.split(',') if x.strip()]
    if a.kit:
        rounds, skipped = m.kit(cats, stats, tals, a.left, a.rl, a.two_handed, not a.pve, a.slots)
        if a.json:
            print(json.dumps({'rounds': rounds, 'skipped': skipped}, indent=1))
            return 0
        print(f"punish kit vs the RL {a.rl - WINDOW}-{a.rl + WINDOW} pool's R1 whiffs"
              f"{'' if a.pve else ', PvP rates'}; value = mean damage landed per punish")
        print(f"{'#':>2s} {'spell':34s} {'variant':8s} {'slots':>5s} {'value':>7s} {'gain':>7s} {'best':>6s}")
        for i, r in enumerate(rounds, 1):
            print(f"{i:>2d} {r['name'][:34]:34s} {r['variant'] or '':8s} {r['slots']:>5d} {r['value']:>7.1f} "
                  f"{r['gain']:>7.1f} {100 * r['best_share']:>5.1f}%")
        return 0
    only = {T._norm(s) for s in a.show} or None
    rows, skipped = m.table(cats, stats, tals, a.left, a.rl, a.two_handed, not a.pve, only)
    rev = a.sort != 'startup'
    rows.sort(key=lambda r: (r[1][a.sort] or 0) * (1 if rev else -1), reverse=True)
    if a.json:
        print(json.dumps({'rows': [dict(s, **v) for s, v in rows], 'skipped': skipped}, indent=1))
        return 0
    print(f"vs the median defender of RL {a.rl - WINDOW}-{a.rl + WINDOW}{'' if a.pve else ', PvP rates'}; "
          f"catalysts {', '.join(f'{c['name']} +{c['level']}' for c in cats)}")
    print(f"{'spell':34s} {'variant':8s} {'FP':>3s} {'start':>6s} {'act':>6s} {'one hit':>8s} {'full':>8s} "
          f"{'full/FP':>8s} {'full/s':>7s}")
    for s, v in rows[:a.top]:
        tag = ' (loop: first cycle)' if v['channel'] else ''
        print(f"{s['name'][:34]:34s} {v['variant']:8s} {v['fp']:>3d} {v['startup']:>6.1f} {v['act']:>6.1f} "
              f"{v['one_hit']:>8.1f} {v['full']:>8.1f} {v['full_per_fp'] or 0:>8.2f} {v['full_per_s'] or 0:>7.1f}{tag}")
        if only:
            for h in v['hits']:
                print(f"    ref{h['ref']} f{h['frame']:<5g} atk {h['atk']} x{h['count']:g}: AR "
                      f"{', '.join(f'{e} {x:g}' for e, x in h['ar'].items())} -> {h['damage']:.1f} "
                      f"({h['phys_type']}, PvP x{h['final_rate']:g})")
    if only and skipped:
        for nm, why in skipped.items():
            print(f'  {nm}: {why}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
