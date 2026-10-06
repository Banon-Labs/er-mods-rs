#!/usr/bin/env python3
"""Per-creature, per-animation attack windup and parryability, as a Lua data file.

For every enemy chr and every TimeAct animation that carries an attack, emit:

  w  windup: the TimeAct start time (seconds, animation-local) of the animation's first
     attack-behavior event that resolves to an AtkParam_Npc row -- the earliest moment its
     hitbox can exist.
  p  1 when the animation can be parried, else 0. Two native paths make an enemy attack
     parryable, and either is enough:
       old  the attacking animation carries a TAE type-0 ChrActionFlag event with
            FlagType 5. `CS::CSChrTaeAnimEvent::_ChrActionFlag` (1.16.2 0x1404275e0) case 5
            sets `actionModifiersFlags |= 0x400`, and
            `CS::CSChrDamageModule::ValidateParryAngles` (1.16.2 0x140444840) only accepts an
            incoming parry hit (`AttackDamageInfo+0x34 == 0x40`) on a chr whose bit 10 of
            `actionModifiersFlags` is set. So FlagType 5 is the "parry possible state" of
            the attacker, and it is how Morgott, Malenia and the Godrick Knights are
            parried -- every one of their AtkParam_Npc rows has isDisableParry == 1.
       new  any AtkParam_Npc row the first hit resolves to has isDisableParry == 0. The
            paramdef describes the flag as disabling the new parry control (the attacker's
            damage contacting a chr in parry state). Mohg (c4800) has no FlagType 5 window
            at all and is parryable through this path.
  q  (only when present and different from w) start of the first FlagType 5 window, the
     moment the parry window opens.
  b  (only when 1) the windup event is a bullet spawn, not a melee hitbox: the hit lands
     after the projectile travels, so w is a lower bound on time-to-impact.

Everything is NpcParam-independent apart from `behaviorVariationId`, which the creature
`ResolveBehaviorId` needs ((variation + 200000) * 1000 + judgeId), taken as the majority over
the chr's NpcParam rows exactly like `scripts/er-moveset-table-gen.py` does.

Key transform (the one a runtime reader must apply):

  key = chrNum * 100000000 + taeId

  chrNum is the numeric creature id of the ChrIns playing the animation (c4351 -> 4351), even
  when its TimeAct is inherited from the family base (c4351 plays out of c4350.tae).
  taeId is the raw id `CSChrTimeActModule::animQueue[i].animId` reports (TAE_Callback's
  taeId) -- Not collapsed with `% 1000000` the way moveset.tbl is. A creature TimeAct holds
  several groups (3000 and 1003000 are different clips), so the raw id is the only exact key.

The player chr c0000 (human NPCs) is out of scope: its attacks resolve through the equipped
weapon's behavior variation and AtkParam_Pc, not through NpcParam.

Reuses the generator's corpus discovery, TimeAct parse, family-base TimeAct inheritance and
BehaviorParam resolution by importing it.

  python3 scripts/er-attack-windup-table.py                  # write the Lua table
  python3 scripts/er-attack-windup-table.py --selftest
  python3 scripts/er-attack-windup-table.py --only c4351,c4800 --out -
"""
import argparse
import importlib.util
import multiprocessing
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# Not under mods/: the lab re-runs every mod file about every 2 s, and this table is read by the
# spawn agent (Frida side) instead, which attaches each hostile's windup to the facts it pushes.
DEFAULT_OUT = os.path.join(HERE, 'frida', 'ai-lua', 'data', 'attack_table.lua')


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GEN = _load('movesetgen', 'er-moveset-table-gen.py')
TAE = GEN.TAE
PR = GEN.PR

#: Raw TAE ids are below this; checked per row, so a wider id cannot alias a neighbour chr.
KEY_CHR_STRIDE = 100000000
#: ChrActionFlag FlagType whose handler sets actionModifiersFlags bit 0x400, the bit
#: ValidateParryAngles requires on the chr receiving a parry hit.
PARRY_POSSIBLE_FLAG_TYPE = 5
#: AtkParam damage columns. The generator's ATK_DAMAGE_FIELDS omits atkDark (holy).
DAMAGE_FIELDS = ('atkPhys', 'atkMag', 'atkFire', 'atkThun', 'atkDark')
SPEFFECT_FIELDS = tuple(f'spEffectId{i}' for i in range(5))
#: BehaviorParam refType values.
REF_ATTACK, REF_BULLET = 0, 1


class ParryRegulation(GEN.Regulation):
    """The generator's Regulation plus the three columns this table needs."""

    def __init__(self, path=None):
        super().__init__(path)
        files = PR.load(path)
        atk, _, _ = PR.rows(PR.param_bytes(files, 'AtkParam_Npc'),
                            list(DAMAGE_FIELDS) + list(SPEFFECT_FIELDS)
                            + ['isDisableParry', 'throwTypeId'])
        #: rowId -> (isDisableParry, hits: deals damage, starts a throw or applies a
        #: SpEffect such as a status buildup)
        self.parry = {
            row['id']: (int(row['isDisableParry']),
                        any(row[f] > 0 for f in DAMAGE_FIELDS)
                        or any(row[f] > 0 for f in SPEFFECT_FIELDS)
                        or row['throwTypeId'] != 0)
            for row in atk
        }
        bullet, _, _ = PR.rows(PR.param_bytes(files, 'Bullet'), ['atkId_Bullet'])
        self.bullet_atk = {row['id']: row['atkId_Bullet'] for row in bullet}

    def atk_rows(self, event_type, value, variation):
        """[(atkRowId, isBullet)] one TAE ability event lands on, or []."""
        resolved = self.resolve(event_type, value, variation)
        if resolved is None:
            return []
        ref_type, ref_id = resolved
        if ref_type == REF_ATTACK and ref_id in self.parry:
            return [(ref_id, False)]
        if ref_type == REF_BULLET:
            atk = self.bullet_atk.get(ref_id)
            if atk is not None and atk in self.parry:
                return [(atk, True)]
        return []


def tae_attack_events(tae_path):
    """{rawTaeId: ([(start, type, value)], parryWindowStart|None)} for one .tae file.

    Raw ids, uncollapsed. Only ability events (the generator's ABILITY_ARG types) and
    FlagType 5 windows are kept.
    """
    _, anims = TAE.parse(tae_path)
    out = {}
    for raw_id, events in anims.items():
        abilities = []
        parry_from = None
        for event in events:
            start = float(event.start)
            if not 0.0 <= start <= GEN.MAX_PLAUSIBLE_EVENT_TIME:
                continue
            if (event.type == TAE.JUMPTABLE_EVENT_TYPE and len(event.params) >= 4
                    and struct.unpack_from('<i', event.params, 0)[0]
                    == PARRY_POSSIBLE_FLAG_TYPE):
                parry_from = start if parry_from is None else min(parry_from, start)
                continue
            arg = GEN.ABILITY_ARG.get(event.type)
            if arg is None or len(event.params) < (arg + 1) * 4:
                continue
            value = struct.unpack_from('<i', event.params, arg * 4)[0]
            abilities.append((start, event.type, value))
        if abilities or parry_from is not None:
            out[raw_id] = (abilities, parry_from)
    return out


def _parse_chr(args):
    chr_id, taes = args
    merged, failures = {}, []
    for path in taes:
        try:
            facts = tae_attack_events(path)
        except Exception as error:                   # one bad file must not drop the chr
            failures.append(f'{os.path.basename(path)}: {error!r}'[:120])
            continue
        for raw_id, (abilities, parry_from) in facts.items():
            prior = merged.get(raw_id)
            if prior:
                abilities = prior[0] + abilities
                parry_from = GEN._earliest(prior[1], parry_from)
            merged[raw_id] = (abilities, parry_from)
    return chr_id, merged, failures


def chr_rows(regulation, merged, variation):
    """{rawTaeId: (w, p, q, b, c)} for one creature; animations with no AtkParam hit omitted.

    Only rows that deal damage, start a throw or apply a SpEffect count as a hit.
    Zero-effect marker rows (c4500's judge 900, a 9 m capsule attached to nearly every
    dragon attack; the shared AtkParam_Npc 3000 and 3305) are not the hit, and an animation
    whose only resolved rows are markers is omitted rather than given the marker's timing.

    The windup prefers the creature's own judge-resolved behaviors (types 1, 2, 123, 304, 307)
    over type 5 CommonBehavior. Type 5 carries a raw shared BehaviorParam row (3000-3305,
    AtkParam_Npc 3020 is 80 phys at 0.6 m) and is typically authored as one event spanning
    the whole clip from 0.0: c4360 a3005 has it at 0.0-2.833 beside its real type-1 swing at
    1.3 and its ChrActionFlag 5 parry window. The handler (1.16.2 0x1404269e0) does start a
    hitbox, so it is a real body-contact hit, but it is not the attack the windup is asked
    about. A clip whose only hit is type 5 falls back to it and is flagged c.
    """
    rows = {}
    for raw_id, (abilities, parry_from) in merged.items():
        tiers = ([], [])   # own judge-resolved hits, common type 5 hits
        for start, event_type, value in abilities:
            common = event_type in GEN.RAW_BEHAVIOR_TYPES
            for atk_id, is_bullet in regulation.atk_rows(event_type, value, variation):
                disable_parry, deals = regulation.parry[atk_id]
                if deals:
                    tiers[int(common)].append((start, is_bullet, disable_parry))
        common_only = int(not tiers[0])
        chosen = tiers[0] or tiers[1]
        if not chosen:
            continue
        start = min(s for s, _, _ in chosen)
        first = [(b, d) for s, b, d in chosen if s == start]
        bullet_only = all(b for b, _ in first)
        # The new-control path is judged on the rows the first hit resolves to (several
        # events usually share that start: c4350 a3000 fires judges 100-106 at 0.633). The
        # old path is a property of the clip, so any FlagType 5 window counts.
        new_parry = any(d == 0 for _, d in first)
        parryable = int(new_parry or parry_from is not None)
        rows[raw_id] = (start, parryable, parry_from, int(bullet_only), common_only)
    return rows


def build(root, only=None, jobs=8, regulation_path=None):
    """-> (regulation, {chrId: rows}, {chrId: reason}) over every non-player chr."""
    regulation = ParryRegulation(regulation_path)
    behbnds = GEN.chr_dirs(root, 'behbnd')
    anibnds = GEN.chr_dirs(root, 'anibnd')
    work, owners, skipped = [], {}, {}
    for chr_id in sorted(set(behbnds) | set(anibnds)):
        if chr_id == 'c0000' or (only and chr_id not in only):
            continue
        variation = regulation.variation_for(chr_id)
        if variation is None:
            # Without a behaviorVariationId no judge id resolves, and what is left -- type 5
            # raw rows -- is the shared body-contact behavior, not this creature's attacks.
            # c4350, c4310 and c4370 are such family bases: their variants (c4351 ...) carry
            # the NpcParam rows and are emitted under their own ids.
            skipped[chr_id] = 'no NpcParam row, so no behaviorVariationId to resolve with'
            continue
        taes, owner = GEN.tae_paths_for_chr(anibnds, chr_id, variation)
        if not taes:
            skipped[chr_id] = 'no TimeAct under its own id or its family base'
            continue
        owners[chr_id] = (owner, variation)
        work.append((chr_id, taes))
    per_chr = {}
    if jobs <= 1:
        results = map(_parse_chr, work)
        pool = None
    else:
        pool = multiprocessing.get_context('fork').Pool(jobs)
        results = pool.imap_unordered(_parse_chr, work, chunksize=1)
    try:
        for chr_id, merged, failures in results:
            for failure in failures:
                print(f'# TAE parse failed {chr_id} {failure}', file=sys.stderr)
            owner, variation = owners[chr_id]
            rows = chr_rows(regulation, merged, variation)
            if rows:
                per_chr[chr_id] = rows
            else:
                skipped[chr_id] = f'no attack event resolves to AtkParam_Npc (tae {owner})'
    finally:
        if pool is not None:
            pool.close()
            pool.join()
    return regulation, per_chr, skipped


def lua_key(chr_id, raw_id):
    if not 0 <= raw_id < KEY_CHR_STRIDE:
        raise ValueError(f'{chr_id} raw TAE id {raw_id} does not fit the key stride')
    return int(chr_id[1:]) * KEY_CHR_STRIDE + raw_id


def _num(value):
    """Shortest Lua literal for a non-negative time rounded to the millisecond."""
    text = f'{round(value, 3):.3f}'.rstrip('0').rstrip('.')
    if text.startswith('0.'):
        text = text[1:]
    return text or '0'


def format_lua(per_chr):
    lines = [
        '-- Attack windup / parryability table -- GENERATED by scripts/er-attack-windup-table.py,',
        '-- do not hand-edit. Regenerate: python3 scripts/er-attack-windup-table.py',
        '--',
        '-- key = chrNum * 100000000 + taeId',
        '--   chrNum: the creature id of the ChrIns playing the animation (c4351 -> 4351), even',
        '--           when its TimeAct is inherited from the family base (c4350).',
        '--   taeId:  the RAW id CSChrTimeActModule animQueue reports (TAE_Callback taeId), NOT',
        '--           collapsed % 1000000 -- 3000 and 1003000 are different clips.',
        '-- w = seconds from animation start to the first damaging attack hitbox (TAE event',
        '--     start); the creature\'s own judge-resolved behaviors win over type 5',
        '--     CommonBehavior body-contact hits, which usually span the clip from 0',
        '-- p = 1 parryable (a TAE ChrActionFlag 5 window in the clip, or an AtkParam_Npc row',
        '--     of the first hit with isDisableParry == 0), 0 not',
        '-- q = parry window (ChrActionFlag 5) start, only when present and != w',
        '-- b = 1 when the first hit is a bullet spawn (w is a lower bound on impact)',
        '-- c = 1 when the only hit is a shared type 5 CommonBehavior row: body contact,',
        '--     often pulsed every 0.5 s from 0 (7000-band clips) -- not an attack windup',
        '-- c0000 (human NPCs) is not covered.',
        'ATTACK_TABLE = {',
    ]
    for chr_id in sorted(per_chr):
        for raw_id in sorted(per_chr[chr_id]):
            w, p, q, b, c = per_chr[chr_id][raw_id]
            fields = [f'w={_num(w)}', f'p={p}']
            if q is not None and round(q, 3) != round(w, 3):
                fields.append(f'q={_num(q)}')
            if b:
                fields.append('b=1')
            if c:
                fields.append('c=1')
            lines.append(f'[{lua_key(chr_id, raw_id)}]={{{",".join(fields)}}},')
    lines.append('}')
    return '\n'.join(lines) + '\n'


# Hand-verified 2026-10-05 against the raw corpus, independently of this script's join:
#  - c4351 Godrick Knight inherits c4350.tae (variation 43500). a3000: ChrActionFlag 5
#    window 0.633-0.667 and the first type-1 event at 0.633, which resolves to
#    AtkParam_Npc 4350100 (atkPhys 210, isDisableParry 1) -> parryable by the old path only.
#  - c4800 Mohg: no ChrActionFlag 5 in any attack, 62 of 64 AtkParam_Npc 4800xxx rows have
#    isDisableParry 0 -> parryable by the new path.
#  - c4500 dragon: no ChrActionFlag 5, every 4500xxx row isDisableParry 1 -> never parryable.
#  - c2010 Blaidd a7000: only type 5 CommonBehavior 3023 pulses at 0.0, 0.5, 1.0 ... -> c=1, w=0.
#  - c4360: no NpcParam row, so no variation; its only resolvable rows are type 5 markers.
SELFTEST_ONLY = {'c4351', 'c4800', 'c4500', 'c2010', 'c4360'}


def selftest(root, regulation_path):
    _, per_chr, _ = build(root, SELFTEST_ONLY, jobs=1, regulation_path=regulation_path)
    failures = []

    def check(label, ok):
        print(('ok   ' if ok else 'FAIL ') + label)
        if not ok:
            failures.append(label)

    knight = per_chr.get('c4351', {})
    row = knight.get(3000)
    check('c4351 a3000 present (inherited c4350 TimeAct)', row is not None)
    if row:
        check(f'c4351 a3000 windup 0.633 (got {row[0]:.3f})', abs(row[0] - 0.633) < 0.002)
        check('c4351 a3000 parryable via ChrActionFlag 5', row[1] == 1 and row[2] is not None)
        check('c4351 a3000 melee, not bullet', row[3] == 0)
    check('c4351 key transform', lua_key('c4351', 3000) == 435100003000)
    check('c4351 keeps raw group ids (>= 1000000) distinct',
          any(r >= 1000000 for r in knight))
    mohg = per_chr.get('c4800', {})
    check('c4800 has attacks', bool(mohg))
    check('c4800 has no ChrActionFlag 5 window', all(r[2] is None for r in mohg.values()))
    check('c4800 parryable rows exist via isDisableParry 0', any(r[1] for r in mohg.values()))
    dragon = per_chr.get('c4500', {})
    check('c4500 has attacks', bool(dragon))
    check('c4500 never parryable', dragon and not any(r[1] for r in dragon.values()))
    blaidd = per_chr.get('c2010', {}).get(7000)
    check('c2010 a7000 is a common-behavior-only clip at 0.0',
          blaidd is not None and blaidd[4] == 1 and blaidd[0] == 0.0)
    check('c2010 a3000 is its own attack, not common-only',
          per_chr.get('c2010', {}).get(3000, (0, 0, 0, 0, 1))[4] == 0)
    check('c4360 skipped without a behaviorVariationId', 'c4360' not in per_chr)
    check('lua number format', _num(0.6333) == '.633' and _num(1.1) == '1.1' and _num(0) == '0')
    print('selftest ' + ('FAILED' if failures else 'passed'))
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--root', default=GEN.CORPUS_ROOT)
    parser.add_argument('--out', default=DEFAULT_OUT)
    parser.add_argument('--only')
    parser.add_argument('--jobs', type=int, default=8)
    parser.add_argument('--regulation')
    parser.add_argument('--selftest', action='store_true')
    options = parser.parse_args()
    if options.selftest:
        return selftest(options.root, options.regulation)
    only = set(options.only.split(',')) if options.only else None
    _, per_chr, skipped = build(options.root, only, options.jobs, options.regulation)
    text = format_lua(per_chr)
    if options.out == '-':
        sys.stdout.write(text)
    else:
        with open(options.out, 'w', encoding='utf-8') as handle:
            handle.write(text)
    rows = sum(len(v) for v in per_chr.values())
    parryable = sum(r[1] for v in per_chr.values() for r in v.values())
    print(f'# {len(per_chr)} chrs, {rows} rows ({parryable} parryable), {len(text)} bytes',
          file=sys.stderr)
    for chr_id, reason in sorted(skipped.items()):
        print(f'# skipped {chr_id}: {reason}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
