#!/usr/bin/env python3
"""Void tech: which actions can spawn their projectile twice across a clip handover.

    python3 scripts/er-mechanics-voidtech.py --scan            # every candidate pair
    python3 scripts/er-mechanics-voidtech.py --scan --var JumpAttack_Land

Write-up: `docs/er-mechanics/void-tech.md`. Labels as the sibling docs use them: `BEHAVIOR` = the
`c0000.behbnd` graph read through `scripts/er-behbnd-tree.py`, `TAE` = decoded TimeAct through
`scripts/er-mechanics-attacks.py`, `INFERRED` = a reading whose consumer was not traced.

The mechanism the scan looks for. A jump cast plays an air clip (045070) and, when the character
lands, a manual selector bound to `JumpAttack_Land` switches the same state to the landed clip
(045074, or its `Land_High` variant). Both clips carry the cast event (TAE type 64,
CastHighlightedMagic) on the same frames. A melee hit cannot double across that switch: the
attack is keyed by slot and behavior id and the landed clip's event reuses it, hit list included
(runtime, `scripts/frida/void-trace.js`: 25 straight sword and 63 Sword Lance jumps, one attack and
at most one hit each). A spawn event has no such key: each firing spawns new bullets with their
own hit records. So any selector that swaps a clip for another mid-action, where both clips carry
a spawn event, is a candidate: if the swap lands on the frame the event fires, both clips fire it
(runtime: one Bestial Sling jump cast fired its volley on the landing frame and again on the next).

Spawn events (TAE types, from `scripts/er-tae-event-scan.py`): 2 BulletBehavior, 64
CastHighlightedMagic, 65 ConsumeCurrentGoods, 123 SpawnFFXBySpEffect2 (a gated BulletBehavior),
785 SpawnChrFinderBullet.
"""

from __future__ import annotations

import argparse
import collections
import glob
import importlib.util
import os
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

#: TAE event types that spawn something on each firing.
SPAWN_EVENTS = {2: 'bullet', 64: 'cast', 65: 'item', 123: 'bullet (SpEffect-gated)', 785: 'finder bullet'}
#: Offset of the behavior judge in a type 2 / 123 event's params (as type 1's: s32 at +8).
BULLET_JUDGE_OFFSET = 8

_MODS: dict = {}


def _mod(name: str):
    if name not in _MODS:
        spec = importlib.util.spec_from_file_location(name.replace('-', '_'), HERE / f'{name}.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MODS[name] = mod
    return _MODS[name]


def handover_selectors(var: str | None = None) -> list[dict]:
    """Every hkbManualSelectorGenerator whose index is bound to a behavior variable and whose
    children are CustomManualSelectorGenerators: [{name, var, clips: [(cmsg name, animId)]}]."""
    tree_mod = _mod('er-behbnd-tree')
    t = tree_mod.Tree()
    tf = t.tf
    out = []
    for idx, it in enumerate(tf.items):
        if tf.tname(it['type']) != 'hkbManualSelectorGenerator':
            continue
        binds = [v for p, v, _ in t.bindings(it) if p == 'selectedGeneratorIndex']
        if not binds or (var is not None and var not in binds):
            continue
        clips = []
        # The generators hang off a pointer array item (type `T*`), one level below the selector.
        kids = []
        for c in t.children(idx):
            kids.extend(t.children(c) if tf.tname(tf.items[c]['type']) == 'T*' else [c])
        for c in kids:
            ci = tf.items[c]
            if tf.tname(ci['type']) != 'CustomManualSelectorGenerator':
                continue
            _, anim = struct.unpack_from('<ii', tf.d, tf.data_off + ci['off'] + 0xa8)
            clips.append((t.str_member(ci, tree_mod.BMAP.NAME_OFF), anim))
        if len({a for _, a in clips}) >= 2:
            out.append({'name': t.str_member(it, tree_mod.BMAP.NAME_OFF), 'var': binds[0], 'clips': clips})
    return out


def spawn_windows(category: int, anim: int) -> dict:
    """{(kind, judge or None): [(first real frame, last real frame)]} of a clip's ungated spawn
    events, or {} when the category has no such clip."""
    atk = _mod('er-mechanics-attacks')
    _, _, events = atk.resolve_events(category, anim)
    if not events:
        return {}
    to_real = atk.clip_to_real(events)
    out = collections.defaultdict(list)
    for e in events:
        if e.type not in SPAWN_EVENTS:
            continue
        judge = None
        if e.type in (2, 123) and len(e.params) >= BULLET_JUDGE_OFFSET + 4:
            judge = struct.unpack_from('<i', e.params, BULLET_JUDGE_OFFSET)[0]
        out[(SPAWN_EVENTS[e.type], judge)].append((atk.real_frame(to_real(e.start)), atk.real_frame(to_real(e.end))))
    return dict(out)


def categories() -> list[int]:
    atk = _mod('er-mechanics-attacks')
    return sorted(int(os.path.basename(p)[1:-4]) for p in glob.glob(os.path.join(atk.PLAYER_TAE_DIR, 'a*.tae')))


def scan(var: str | None = None) -> list[dict]:
    """Every (selector, category, first clip, other clip, spawn event) where both clips of one
    handover selector fire the same spawn event (same kind, and for bullets the same judge)."""
    rows = []
    pairs = collections.defaultdict(set)
    for sel in handover_selectors(var):
        first = sel['clips'][0][1]
        for name, anim in sel['clips'][1:]:
            if anim != first:
                pairs[(first, anim)].add((sel['var'], sel['name'], name))
    for cat in categories():
        for (a, b), sels in sorted(pairs.items()):
            wa = spawn_windows(cat, a)
            if not wa:
                continue
            wb = spawn_windows(cat, b)
            for key in sorted(set(wa) & set(wb), key=str):
                rows.append({'category': cat, 'from': a, 'to': b, 'kind': key[0], 'judge': key[1],
                             'from_frames': wa[key], 'to_frames': wb[key],
                             'vars': sorted({v for v, _, _ in sels}),
                             'selectors': sorted({s for _, s, _ in sels})})
    return rows


#: The standing-jump air clips an NPC's inputs reach, by (button, grip): 0310x0 is the one-handed
#: right-hand jump R1, 0312x0 its R2, 033xxx the two-handed pair (the Smithscript Dagger's 031030 and
#: 033030 measured in `RUNTIME`), 045070 the right-hand jump cast.
JUMP_INPUTS = {('R1', 1): 31030, ('R1', 2): 33030, ('R2', 1): 31230, ('R2', 2): 33230}
JUMP_CAST_ANIM = 45070
#: `MagicParam.ezStateBehaviorType`: 0 a sorcery (cast from a weapon with `enableMagic`), 1 an
#: incantation (`enableMiracle`).
SPELL_SCHOOL = {0: 'enableMagic', 1: 'enableMiracle'}


def ai_table() -> dict:
    """What `er_npc_summons.dll` (`er_npc_summons_core::void_tech`) needs to tell from a character's gear whether it can
    void tech and with which input: {weapons: {base id: [{button, grip, anim, category, spawn_s}]},
    spells: {MagicParam id: {anim, category, spawn_s, school}}, catalysts: {base id: [school
    field, ...]}}. `spawn_s` is the earliest spawn event's real time into the air clip, a seed only:
    the driver learns the press time from the landings it sees."""
    atk = _mod('er-mechanics-attacks')
    pr = atk.PR
    files = pr.load()
    rows = scan('JumpAttack_Land')
    spawn = {}
    for r in rows:
        start = min(s for s, _ in r['from_frames'])
        key = (r['category'], r['from'])
        spawn[key] = min(spawn.get(key, start), start)
    weapons, catalysts = {}, {}
    wrows, _, _ = pr.rows(pr.param_bytes(files, 'EquipParamWeapon'))
    for w in wrows:
        if w['id'] % 10000 or w['id'] <= 0:
            continue
        for field in SPELL_SCHOOL.values():
            if w.get(field):
                catalysts.setdefault(str(w['id']), []).append(field)
        out = []
        for (button, grip), anim in JUMP_INPUTS.items():
            category = atk.motion_category(w, anim)
            if (category, anim) in spawn:
                out.append({'button': button, 'grip': grip, 'anim': anim, 'category': category,
                            'spawn_s': round(spawn[(category, anim)] / atk.TAE_FPS, 4)})
        if out:
            weapons[str(w['id'])] = out
    spells = {}
    mrows, _, _ = pr.rows(pr.param_bytes(files, 'Magic'))
    for m in mrows:
        category = 400 + m['refType']
        if (category, JUMP_CAST_ANIM) in spawn and m['ezStateBehaviorType'] in SPELL_SCHOOL:
            spells[str(m['id'])] = {'anim': JUMP_CAST_ANIM, 'category': category,
                                    'spawn_s': round(spawn[(category, JUMP_CAST_ANIM)] / atk.TAE_FPS, 4),
                                    'school': SPELL_SCHOOL[m['ezStateBehaviorType']]}
    return {'weapons': weapons, 'spells': spells, 'catalysts': catalysts}


#: Where the DLL's copy of the table lives (`er_npc_summons_core::void_tech`, `include_str!`).
AI_TABLE_PATH = HERE.parent / 'crates' / 'er-npc-summons-core' / 'data' / 'void-table.tsv'


def ai_table_tsv(table: dict) -> str:
    """The table as the DLL reads it, one fact per line, tab-separated:
    `w <weapon base id> <R1|R2> <grip 1|2> <spawn_s>`, `s <magic id> <school field> <spawn_s>`,
    `c <weapon base id> <school field>`."""
    lines = ['# Void tech gear table, written by scripts/er-mechanics-voidtech.py --ai-table; do not edit.',
             '# w weapon_base button grip spawn_s | s magic school spawn_s | c weapon_base school']
    for wid in sorted(table['weapons'], key=int):
        for e in table['weapons'][wid]:
            lines.append(f"w\t{wid}\t{e['button']}\t{e['grip']}\t{e['spawn_s']}")
    for mid in sorted(table['spells'], key=int):
        e = table['spells'][mid]
        lines.append(f"s\t{mid}\t{e['school']}\t{e['spawn_s']}")
    for wid in sorted(table['catalysts'], key=int):
        for school in table['catalysts'][wid]:
            lines.append(f"c\t{wid}\t{school}")
    return '\n'.join(lines) + '\n'


#: Runtime results (`MEASURED`, scripts/frida/void-trace.js, 2026-10-07, 1.17.1): spawns whose
#: event frame fell on the landing frame, and how many of them doubled; spawns at any other frame.
RUNTIME = {
    'Bestial Sling (NPC, cast)': {'on_landing': 3, 'doubled': 3, 'elsewhere': 31, 'elsewhere_doubled': 0},
    'Smithscript Dagger 2H jump R1 (NPC, bullet)': {'on_landing': 9, 'doubled': 9, 'elsewhere': 58, 'elsewhere_doubled': 0},
    'Smithscript Dagger 1H jump R1 (player, bullet)': {'on_landing': 2, 'doubled': 1, 'elsewhere': 25, 'elsewhere_doubled': 0},
    'melee jump R1 (straight sword, Sword Lance; NPC)': {'on_landing': 0, 'doubled': 0, 'elsewhere': 88, 'elsewhere_doubled': 0},
}


def selftest() -> int:
    results = []

    def check(name, ok):
        results.append(bool(ok))
        print(f"{'ok  ' if ok else 'FAIL'} {name}")

    sels = handover_selectors('JumpAttack_Land')
    names = {s['name'] for s in sels}
    magic = [s for s in sels if s['name'] == 'Jump_N Selector_Magic_Right']
    check('Jump_N Selector_Magic_Right picks air 045070, then landed 045074',
          magic and [a for _, a in magic[0]['clips']][:2] == [45070, 45074])
    check('the same selector has a third, Land_High clip (045074 again)', magic and len(magic[0]['clips']) == 3)
    check('falling casts have their own selector (JumpMagic_Start_Falling_ConditionSelector_Right)',
          'JumpMagic_Start_Falling_ConditionSelector_Right' in names)
    sling_air, sling_land = spawn_windows(438, 45070), spawn_windows(438, 45074)
    check('Bestial Sling (a438) casts on the same frames in the air and landed clips',
          sling_air.get(('cast', None)) and sling_air.get(('cast', None)) == sling_land.get(('cast', None)))
    dag_air, dag_land = spawn_windows(53, 33030), spawn_windows(53, 33070)
    check('Smithscript Dagger (a053) 2H jump R1 throws the judge 350 and 353 bullets in both clips',
          all(k in dag_air and k in dag_land for k in (('bullet', 350), ('bullet', 353))))
    ss_air, ss_land = spawn_windows(23, 31030), spawn_windows(23, 31070)
    check('a straight sword (a023) jump R1 has no spawn event in either clip', not ss_air and not ss_land)
    rows = scan('JumpAttack_Land')
    check('the landing scan finds the five jump-cast families (a432 a438 a479 a480 a506)',
          {r['category'] for r in rows if r['kind'] == 'cast'} == {432, 438, 479, 480, 506})
    check('the DLL gear table (crates/er-npc-summons-core/data/void-table.tsv) is current',
          AI_TABLE_PATH.is_file() and AI_TABLE_PATH.read_text(encoding='utf-8') == ai_table_tsv(ai_table()))
    for name, r in RUNTIME.items():
        check(f'runtime {name}: no double off the landing frame', r['elsewhere_doubled'] == 0)
    print(f'{sum(results)}/{len(results)} checks passed')
    return 0 if all(results) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--scan', action='store_true', help='list every candidate handover pair')
    ap.add_argument('--var', help='only selectors bound to this behavior variable')
    ap.add_argument('--ai-table', metavar='OUT', help="write the DLL gear table (crates/er-npc-summons-core/data/void-table.tsv)")
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.ai_table:
        table = ai_table()
        Path(a.ai_table).write_text(ai_table_tsv(table), encoding='utf-8')
        print(f"{len(table['weapons'])} weapons, {len(table['spells'])} spells, "
              f"{len(table['catalysts'])} catalysts -> {a.ai_table}")
        return 0
    if not a.scan:
        ap.print_help()
        return 0
    rows = scan(a.var)
    print(f"{'cat':>4} {'from':>7} {'to':>7} {'kind':<24} {'judge':>6}  frames (from | to)  vars")
    for r in rows:
        fr = ','.join(f'{s}-{e}' for s, e in r['from_frames'])
        to = ','.join(f'{s}-{e}' for s, e in r['to_frames'])
        print(f"a{r['category']:03d} {r['from']:07d} {r['to']:07d} {r['kind']:<24} {str(r['judge']):>6}  {fr} | {to}  {','.join(r['vars'])}")
    print(f'{len(rows)} pairs')
    return 0


if __name__ == '__main__':
    sys.exit(main())
