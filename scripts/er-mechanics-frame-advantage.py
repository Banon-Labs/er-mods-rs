#!/usr/bin/env python3
"""Frame advantage, hitstun and true combos for player-vs-player hits.

Write-up: `docs/er-mechanics/frame-advantage.md`. Labels follow `docs/er-mechanics/*.md`:
`VERIFIED` = regulation value or code read out of the 1.16.2 executable (addresses below),
`TAE` = decoded TimeAct or behavior-graph data, `COMMUNITY` = the curated decompile of
`c0000.hks` (ER 1.08.1, ividyon/EldenRingHKS via Smithbox) or another outside source,
`INFERRED` = consistent with the data but the consumer was not traced.

Which reaction a player defender plays (`VERIFIED`, 1.16.2):

    HitChr 0x1404445e0: level = ChrIns::GetDamageLevel 0x1406901d0
        = AtkParam.dmgLevel, or remap(AtkParam.dmgLevel_vsPlayer) when that is nonzero
    ApplyDamage 0x1404497d0: poise damage is applied first (FUN_140486bf0), then the reaction
        is written by FUN_140445b20 -> actionFlag->damageLevel = FUN_140690250(level):
      poise holds (toughness slot 6 0x140486ba0: max > 0 and current > 0)
          -> v = SpEffectParam[ToughnessParam[row].spEffectId].dmgLv_*[level]   (row +0x148)
      poise broken
          -> v = category-1001 SpEffect override, 0 when there is none
      level = REMAP[v] if 0 < v < 13 else level            (FUN_140d22e90, table 0x142bafc88)

ToughnessParam row 0 (no hyperarmor) and every weapon hyperarmor row name SpEffect 6352, which
maps small, middle, large, push and minimum to 0: with poise intact those hits leave only the
additive flinch. The HKS (`COMMUNITY`) plays `ExecAddDamage` for level 0 on an additive layer
and returns without changing state, so the defender is not interrupted. A broken poise refills
to max two toughness updates later (0x140486e50, `field_0x2a` -> `field_0x2b`).

When the defender can act again (`TAE` windows, `COMMUNITY` gates): R1/R2/guard/move use the
same JumpTable input and cancel pairs as `er-mechanics-attacks.py`. Rolling out of small,
middle, large, push and minimum also needs `GetEventEzStateFlag(n)` (TAE event 227), with n
chosen by the HKS `DamageCount` of consecutive staggers: 1 -> flag 2, 2 -> 3, 3 -> 4, 4+ -> 5.

Usage:

    python3 scripts/er-mechanics-frame-advantage.py                 # the default weapon set
    python3 scripts/er-mechanics-frame-advantage.py Uchigatana --grip both
    python3 scripts/er-mechanics-frame-advantage.py --reactions
    python3 scripts/er-mechanics-frame-advantage.py Greatsword --grip both --json
    python3 scripts/er-mechanics-frame-advantage.py --selftest
"""
import argparse
import importlib.util
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ATTACKS = _load('er_mechanics_attacks', 'er-mechanics-attacks.py')
TAE_SCAN = _load('er_tae_event_scan', 'er-tae-event-scan.py')
PR = ATTACKS.PR
FPS = ATTACKS.TAE_FPS

#: Installed compiled `c0000.hks`, used only to check that the names the 1.08.1 decompile
#: relies on still exist in the build that is installed.
INSTALLED_HKS = os.environ.get(
    'ER_PLAYER_HKS',
    os.path.expanduser('~/er-extract/LOOK_HERE_ALL_ASSETS_20260713/action/script/c0000.hks'))

# 1.16.2 addresses (named Ghidra dump on :8765, shift 0 against eldenring-deobf.bin).
GET_DAMAGE_LEVEL = 0x1406901d0       # CS::ChrIns::GetDamageLevel
REACTION_LEVEL = 0x140690250         # FUN_140690250, poise-dependent remap
REACTION_WRITER = 0x140445b20        # writes actionFlag->damageLevel (+0x1c)
TOUGHNESS_HOLDS = 0x140486ba0        # toughness vtable slot 6
TOUGHNESS_SPEFFECT = 0x1404878c0     # player toughness vtable slot 9
TOUGHNESS_DAMAGE = 0x140486bf0       # subtracts poise, sets the broken flags
TOUGHNESS_UPDATE = 0x140486e50       # refill after a break
PLAYER_TOUGHNESS_VTABLE = 0x142a3bf00
#: `FUN_140d22e90`: SpEffect dmgLv_* value -> damage level. 0 means keep the level.
REMAP_TABLE_VA = 0x142bafc88
REMAP = (0, 0, 8, 1, 2, 3, 7, 4, 6, 9, 5, 10, 11)
#: Slot 9 fallback when the ToughnessParam row is missing: SpEffect base id and the
#: `toughnessDurablityUnk` thresholds (floats at 0x143b179f0, int base at 0x143b17a00).
FALLBACK_THRESHOLDS_VA, FALLBACK_BASE_VA = 0x143b179f0, 0x143b17a00
FALLBACK_THRESHOLDS, FALLBACK_BASE = (30.0, 50.0, 70.0, 100.0), 6350
#: `SP_EFFECT_PARAM_ST` offset read by `FUN_140d4ffa0` (`row + 0x148 + level`).
DMG_LV_OFFSET = 0x148
DMG_LV_FIELDS = ('dmgLv_None', 'dmgLv_S', 'dmgLv_M', 'dmgLv_L', 'dmgLv_BlowM', 'dmgLv_Push',
                 'dmgLv_Strike', 'dmgLv_BlowS', 'dmgLv_Min', 'dmgLv_Uppercut', 'dmgLv_BlowLL',
                 'dmgLv_Breath')

#: TAE event types read here.
TAE_JUMP_TABLE, TAE_EZSTATE_FLAG = 0, 227
JT_IFRAMES = 8
#: HKS `ExecEvasion` (decompile, `COMMUNITY`): consecutive-stagger count -> EzState flag the roll needs.
CHAIN_RECOVER_FLAG = {1: 2, 2: 3, 3: 4, 4: 5}

#: Damage level -> (name, behavior-graph event, clips it plays, counts toward DamageCount).
#: Clip lists are `scripts/er-behbnd-attack-map.py` output for `c0000.behbnd` (`TAE`). The
#: level number <-> event pairing is the HKS `ExecDamage` branch (`COMMUNITY`), and it agrees
#: with the `Lv<n>` in every state name. Large plays `DamageLarge2` (5350..5353) instead when
#: `env(GetBehaviorID, 3)` is true, which was not traced.
LEVELS = {
    0: ('none', 'W_AddDamageLv0 (additive)', (10000, 10001, 10002), False),
    1: ('small', 'W_DamageLv1_Small', tuple(5100 + 10 * d + i for d in range(4)
                                             for i in range(5)), True),
    2: ('middle', 'W_DamageLv2_Middle', tuple(5200 + 10 * d + i for d in range(4)
                                               for i in range(5)), True),
    3: ('large', 'W_DamageLv3_Large', tuple(5300 + 10 * d + i for d in range(4)
                                             for i in range(5)), True),
    4: ('exlarge', 'W_DamageLv4_ExLarge', (5450, 5460, 5470, 5480), False),
    5: ('push', 'W_DamageLv5_Push', (5500, 5510, 5520, 5530), True),
    6: ('fling', 'W_DamageLv6_Fling', (5700,), False),
    7: ('small blow', 'W_DamageLv7_SmallBlow', (5400, 5410, 5420, 5430), False),
    8: ('minimum', 'W_DamageLv8_Minimum', (5000, 5001, 5002), True),
    9: ('upper', 'W_DamageLv9_Upper', (5710, 5711, 5712), False),
    10: ('ex blast', 'W_DamageLV10_ExBlast', (5450, 5460, 5470, 5480), False),
    11: ('breath', 'W_DamageLv11_Breath', (5600, 5610, 5620, 5630), False),
}
LARGE2_CLIPS = (5350, 5351, 5352, 5353)
#: A standing roll (`a000_0271xx`) has i-frames (JumpTable 8) from its first frame.
ROLL_CLIP = 27100

#: Default table: (weapon name, grip, slots). `None` = every slot the weapon has.
DEFAULT_SET = [
    ('Greatsword', 'both', ('2h_r1_1', '2h_r1_2', '2h_r1_3', '2h_crouch_r1', '2h_r2_1',
                            '2h_r2_2', '2h_r2_1c', '2h_r2_2c')),
    ('Giant-Crusher', 'both', ('2h_r1_1', '2h_r1_2', '2h_r1_3', '2h_crouch_r1', '2h_r2_1',
                               '2h_r2_2', '2h_r2_1c', '2h_r2_2c')),
    ('Uchigatana', 'both', ('2h_r1_1', '2h_r1_2', '2h_r1_3', '2h_r1_4', '2h_r1_5',
                            '2h_crouch_r1', '2h_r2_1', '2h_r2_2')),
    ('Erdsteel Dagger', 'one', ('r1_1', 'r1_2', 'r1_3', 'r1_4', 'r1_5', 'r1_6', 'crouch_r1',
                                'r2_1', 'r2_2')),
]

_COMMON = None


def common_tae():
    """`a00.tae`: the common (non-weapon) player animations, damage reactions included."""
    global _COMMON
    if _COMMON is None:
        path = os.path.join(ATTACKS.PLAYER_TAE_DIR, 'a00.tae')
        _COMMON = TAE_SCAN.parse(path)[1] if os.path.exists(path) else None
    return _COMMON


def _frame(seconds):
    return round(seconds * FPS)


def ezstate_windows(events):
    """{flag: [(start, end)]} in frames from TAE event 227 (arg 0 = flag id)."""
    out = {}
    for e in events:
        if e.type == TAE_EZSTATE_FLAG:
            flag = struct.unpack_from('<i', e.params, 0)[0]
            out.setdefault(flag, []).append((_frame(e.start), _frame(min(e.end, 1e4))))
    return out


def clip_timing(anim):
    """Timing of one damage clip, in frames from its first frame, or None."""
    anims = common_tae()
    if anims is None or anim not in anims:
        return None
    events = anims[anim]
    windows = ATTACKS.recovery_windows(events, 0.0)
    first = {}
    for key, _, inputs, _ in ATTACKS.RECOVERY_ACTIONS:
        t = ATTACKS._first_open(windows[key], inputs is not None)
        first[key] = None if t is None else _frame(t)
    flags = ezstate_windows(events)
    roll = {}
    for count, flag in CHAIN_RECOVER_FLAG.items():
        opens = [s for s, e in flags.get(flag, []) if e > s]
        tae = first['dodge']
        roll[count] = None if not opens or tae is None else max(min(opens), tae)
    path = ATTACKS.hkx_path(0, anim)
    clip = ATTACKS.hkx_duration(path) if path else None
    return {
        'anim': f'a000_{anim:06d}',
        'r1': first['r1'], 'r2': first['r2'], 'guard': first['guard'], 'move': first['move'],
        'roll_tae': first['dodge'], 'roll_by_count': roll,
        'unresolved_early': {k: v['unresolved_early'] for k, v in windows.items()
                             if v['unresolved_early']},
        'len': _frame(clip[0]) if clip else None,
    }


def reaction(level, large2=False):
    """Defender timing for one damage level (all clips of the state, earliest per field)."""
    name, event, clips, counts = LEVELS[level]
    if level == 0:
        return {'level': 0, 'name': name, 'event': event, 'locks': False, 'counts': False,
                'r1': 0, 'r2': 0, 'guard': 0, 'move': 0, 'roll': {c: 0 for c in CHAIN_RECOVER_FLAG},
                'clips': [f'a000_{c:06d}' for c in clips], 'len': None, 'spread': {}}
    if level == 3 and large2:
        clips, event = LARGE2_CLIPS, 'W_DamageLarge2'
    timings = [t for t in (clip_timing(c) for c in clips) if t]
    if not timings:
        return None

    def earliest(values):
        values = [v for v in values if v is not None]
        return min(values) if values else None

    out = {'level': level, 'name': name, 'event': event, 'locks': True, 'counts': counts,
           'clips': [t['anim'] for t in timings],
           'len': earliest(t['len'] for t in timings)}
    spread = {}
    for key in ('r1', 'r2', 'guard', 'move'):
        values = [t[key] for t in timings if t[key] is not None]
        out[key] = min(values) if values else None
        if values and min(values) != max(values):
            spread[key] = (min(values), max(values))
    if counts:
        out['roll'] = {c: earliest(t['roll_by_count'][c] for t in timings)
                       for c in CHAIN_RECOVER_FLAG}
    else:
        # DamageCount is reset for these levels, so ExecEvasion skips the EzState gate.
        tae = earliest(t['roll_tae'] for t in timings)
        out['roll'] = {c: tae for c in CHAIN_RECOVER_FLAG}
    out['spread'] = spread
    out['unresolved_early'] = sorted({k for t in timings for k in t['unresolved_early']})
    return out


class Tables:
    """Regulation rows this module reads beyond `er-mechanics-attacks.Regulation`."""

    def __init__(self, reg, regulation=None):
        files = PR.load(regulation)

        def table(stem, fields=None):
            rows, _, _ = PR.rows(PR.param_bytes(files, stem), fields)
            return {r['id']: r for r in rows}

        self.reg = reg
        self.atk = table('AtkParam_Pc', ['dmgLevel', 'dmgLevel_vsPlayer', 'finalDamageRateId'])
        self.final_rate = table('FinalDamageRateParam')
        self.toughness = table('ToughnessParam')
        wanted = {r['spEffectId'] for r in self.toughness.values()}
        wanted |= {FALLBACK_BASE + i for i in range(5)}
        sp = table('SpEffectParam', list(DMG_LV_FIELDS))
        self.dmg_lv = {i: tuple(sp[i][f] for f in DMG_LV_FIELDS) for i in wanted if i in sp}

    def toughness_speffect(self, row=0):
        r = self.toughness.get(row)
        return r['spEffectId'] if r and r['spEffectId'] >= 0 else None


def remap(value, level):
    """`FUN_140d22e90`: a dmgLv_* value to a damage level; 0 or out of range keeps `level`."""
    return REMAP[value] if 0 < value < len(REMAP) else level


def hit_level(atk):
    """`ChrIns::GetDamageLevel` for a player defender."""
    vs_player = atk.get('dmgLevel_vsPlayer') or 0
    return remap(vs_player, atk['dmgLevel']) if vs_player else atk['dmgLevel']


def reaction_level(tables, atk_row, poise_broken, toughness_row=0):
    """Damage level the defender's HKS reads, for a player defender with no SpEffect overrides.

    `toughness_row` is the ToughnessParam row of the defender's current hyperarmor window
    (0 outside one). With poise broken, the category-1001 SpEffect override is taken as absent.
    """
    level = hit_level(tables.atk[atk_row])
    if level == 0 or poise_broken:
        return level
    sp = tables.toughness_speffect(toughness_row)
    table = tables.dmg_lv.get(sp)
    if table is None or level >= len(table):
        return level
    return remap(table[level], level)


def _first(cancel):
    """Earliest attacker action and its frame from a `cancel_frame` dict."""
    ready = {k: v for k, v in cancel.items() if v is not None}
    if not ready:
        return None, None
    key = min(ready, key=ready.get)
    return key, ready[key]


def advantage(slot, react, count=1, delay=0):
    """Frame advantage of one hit, measured from its first hit frame (positive = attacker first).

    Defender side: earliest of R1, guard and roll out of `react` (+ `delay` frames before the
    reaction starts). Attacker side: earliest of its own r1/r2/dodge/guard/move cancels.
    """
    if not slot.get('hit_windows'):
        return None
    h0 = slot['hit_windows'][0][0]
    key, att = _first(slot['cancel_frame'])
    if att is None:
        return None
    # Attack frames are real time to 0.1 (TAE 608 play speed, attacks.md s.4); keep the
    # differences at that precision.
    attacker = round(att - h0, 1)
    defender = {'r1': react['r1'], 'guard': react['guard'], 'roll': react['roll'][count]}
    defender = {k: (v + delay if react['locks'] else 0) for k, v in defender.items()
                if v is not None}
    d_key = min(defender, key=defender.get)
    return {'attacker_action': key, 'attacker_ready': attacker,
            'defender_action': d_key, 'defender_ready': defender[d_key],
            'defender': defender, 'advantage': round(defender[d_key] - attacker, 1),
            'roll_advantage': round(defender.get('roll', 0) - attacker, 1)}


#: Follow-ups the HKS plays by default (`COMMUNITY`, `AttackBoth/RightLight<n>_onUpdate`,
#: `AttackBoth/RightHeavy1End_onUpdate`): R1 #n -> R1 #n+1 and uncharged R2 #1 -> R2 #2.
#: R1 -> R2, crouch R1 -> R1 and rolling R1 -> R1 go through `*SubStart` clips (030080..,
#: 032080.., 032501) that carry no hitbox; their lead-in to the next hitting clip was not
#: traced, so those pairs are not reported.
#:
#: R2 #1 -> R2 #2 is not a direct link either. `AttackRightHeavy1End_onUpdate` sends R2 to
#: `W_AttackRightHeavy2Start` (c0000.hks line 7778), and `AttackRightHeavy2Start_onUpdate`
#: (line 7788) moves on to `W_AttackRightHeavy2End`, the clip that hits, only once R2 is up and
#: `GetGeneralTAEFlag(TAE_FLAG_CHARGING) == 1 or GetSpEffectID(100280)`. `combo` adds that
#: charge-start clip's lead-in (`er-mechanics-attacks.release_lead_in`: first frame of SpEffect
#: 100280 in the Start clip) to the gap; `R2_LEAD_IN_VIA` names the follow-ups that pay it.
#:
#: The flag half of the gate cannot open earlier (`VERIFIED`, 1.16.2): `GetGeneralTAEFlag`
#: (HksEnv case, `FUN_1404167b0`) tests bit n of `CSChrBehaviorDataModule+0x308`, which
#: `PreBehaviorSafe` clears every frame (`FUN_1404146b0`) and whose only setter
#: (`0x140416930`, `bts`) is reached from `CSChrTaeAnimEvent::ExecuteThreadOne` for TAE event
#: 600 (`cmp eax, 0x258; je 0x14042e764`), bit = the event's first argument. None of the 1164
#: player R2 Start/End clips (0305xx, 0325xx, 0405xx, 0425xx in every `a*.tae`) carries an event
#: 600 (`TAE`), so the flag is never set there and SpEffect 100280 is the only way through.
#: Both TAE 66 and 67 reach `CSChrTaeAnimEvent::AddSpEffect` (0x14042bfd0, 67 with
#: `doNotSync` set), so the event-67 windows the Start clips use do apply it.
R2_LEAD_IN_VIA = 'r2'
TAE_SET_GENERAL_FLAG = 600
SET_GENERAL_TAE_FLAG = 0x140416930   # bts [CSChrBehaviorDataModule+0x308], edx
GET_GENERAL_TAE_FLAG = 0x1404167b0   # HksEnv GetGeneralTAEFlag -> bit test of +0x308
def follow_ups(keys):
    pairs = []
    for key in keys:
        base = key.replace('2h_', '')
        prefix = '2h_' if key.startswith('2h_') else ''
        if base.startswith('r1_'):
            nxt = f'{prefix}r1_{int(base[3:]) + 1}'
            pairs.append((key, nxt, 'r1'))
        elif base == 'r2_1':
            pairs.append((key, f'{prefix}r2_2', 'r2'))
    return [(a, b, via) for a, b, via in pairs if b in keys]


def combo(first, second, via, react, count=1, delay=0):
    """Whether `second` connects before the defender can leave the stagger `first` caused.

    The gap is counted from `first`'s first hit frame to `second`'s first hit frame. A roll
    has i-frames from its first frame, so the hit must land before the roll can start. `tie`
    = the same frame, where the order of the hit and the defender's HKS update decides.

    A follow-up entered through `R2_LEAD_IN_VIA` plays its charge-start clip before the clip
    its hit frames are measured in, so its `release_lead_in` is part of the gap and is
    reported as `lead_in`. When that lead-in is unknown (Start clip or SpEffect 100280 event
    absent) the link has no gap and None is returned rather than a verdict without it.
    """
    if not first.get('hit_windows') or not second.get('hit_windows'):
        return None
    ready = first['cancel_frame'].get(via)
    if ready is None:
        return None
    lead = 0
    if via == R2_LEAD_IN_VIA:
        lead = second.get('release_lead_in')
        if lead is None:
            return None
    gap = round(ready - first['hit_windows'][0][0] + lead + second['hit_windows'][0][0], 1)
    if not react['locks']:
        return {'gap': gap, 'lead_in': lead, 'escape': 0, 'escape_by': 'not locked',
                'verdict': 'no'}
    escapes = {'roll': react['roll'][count], 'guard': react['guard']}
    escapes = {k: v + delay for k, v in escapes.items() if v is not None}
    by = min(escapes, key=escapes.get)
    esc = escapes[by]
    verdict = 'true' if gap < esc else ('tie' if gap == esc else 'no')
    return {'gap': gap, 'lead_in': lead, 'escape': esc, 'escape_by': by, 'verdict': verdict}


def slot_profile(reg, tables, weapon_id, grip='one', slots=None, delay=0, count=1):
    """Per-slot frame data a ranking can consume. One dict per attack slot."""
    rows = ATTACKS.weapon_attacks(reg, weapon_id, grip)
    by_key = {r['slot']: r for r in rows}
    keys = [k for k in (slots or by_key) if k in by_key]
    reactions = {}

    def react(level):
        if level not in reactions:
            reactions[level] = reaction(level)
        return reactions[level]

    out = []
    for key in keys:
        r = by_key[key]
        atk = tables.atk[r['atk_row']]
        broken = reaction_level(tables, r['atk_row'], True)
        intact = reaction_level(tables, r['atk_row'], False)
        fdr = tables.final_rate.get(atk['finalDamageRateId'], {})
        entry = {
            'slot': key, 'label': r['label'], 'anim': r['anim'], 'atk_row': r['atk_row'],
            'hit_windows': r.get('hit_windows'), 'cancel_frame': r.get('cancel_frame'),
            'hyperarmor': [{'frames': h['frames'], 'row': h['toughness_row'],
                            'bonus': h['poise_bonus']} for h in r.get('hyperarmor', [])],
            'poise_damage': r['poise_damage'],
            'pvp_sa_rate': fdr.get('saRate'),
            'dmg_level': atk['dmgLevel'], 'dmg_level_vs_player': atk['dmgLevel_vsPlayer'],
            'reaction_on_break': broken, 'reaction_poise_intact': intact,
            'on_break': advantage(r, react(broken), count, delay) if react(broken) else None,
            'on_intact': advantage(r, react(intact), count, delay) if react(intact) else None,
        }
        out.append(entry)
    for a, b, via in follow_ups(keys):
        first = by_key[a]
        entry = next(e for e in out if e['slot'] == a)
        entry.setdefault('combos', []).append({
            'next': b, 'via': via,
            'on_break': combo(first, by_key[b], via, react(entry['reaction_on_break']),
                              count, delay),
            'on_intact': combo(first, by_key[b], via, react(entry['reaction_poise_intact']),
                               count, delay),
        })
    return out


def pvp_poise_damage(tables, entry, defender_ha_row=None):
    """Poise damage one hit deals to a player defender, in the attacks module's units.

    `FUN_140486bf0`, both sides players, multiplies `hit[+0x100] * hit[+0x244]` by
    `FinalDamageRateParam[atk.finalDamageRateId].saRate` and, while the defender's window is
    active, by `ToughnessParam[row].unk1`. That `+0x100` is the attacks module's
    `poise_damage` is inferred; `+0x244` is taken as 1.0 (unknown), and armor/SpEffect
    `toughnessDamageCutRate` as 1.0.
    """
    rate = entry.get('pvp_sa_rate') or 1.0
    ha = 1.0
    if defender_ha_row is not None:
        ha = tables.toughness.get(defender_ha_row, {}).get('unk1', 1.0)
    return entry['poise_damage'] * rate * ha


def trade(tables, first, second, offset, second_poise, first_poise=None):
    """Two players swing: `first` starts on frame 0, `second` on frame `offset`.

    Entries are `slot_profile` dicts; poise values are armor poise in the attacks module's
    internal units (menu / 10). Whoever's first hit frame comes earlier strikes the other; the
    struck side is interrupted only if that hit breaks its poise (armor + hyperarmor bonus if
    its window covers that frame). Otherwise it only flinches (level 0 through SpEffect 6352)
    and its own attack still lands: a trade.
    """
    first_poise = second_poise if first_poise is None else first_poise
    t_first = first['hit_windows'][0][0]
    t_second = offset + second['hit_windows'][0][0]
    if t_first == t_second:
        return {'verdict': 'trade (same frame)', 'hit_frame': t_first}
    if t_first < t_second:
        hitter, struck, struck_start, poise, who = first, second, offset, second_poise, 'first'
    else:
        hitter, struck, struck_start, poise, who = second, first, 0, first_poise, 'second'
    t = min(t_first, t_second)
    local = t - struck_start
    window = next((h for h in struck.get('hyperarmor', [])
                   if h['frames'][0] <= local < h['frames'][1]), None)
    held = poise + (window['bonus'] if window else 0.0)
    dealt = pvp_poise_damage(tables, hitter, window['row'] if window else None)
    return {'hits_first': who, 'hit_frame': t, 'struck_anim_frame': local,
            'struck_in_hyperarmor': bool(window), 'poise_dealt': round(dealt, 3),
            'poise_held': round(held, 3),
            'verdict': 'struck side interrupted' if dealt >= held else 'trade (both hit)'}


def _dash(v):
    return '-' if v is None else v


def print_reactions():
    print('level name        clips                len  R1  R2 guard move  roll@count1/2/3/4')
    for level in sorted(LEVELS):
        for large2 in ((False, True) if level == 3 else (False,)):
            r = reaction(level, large2)
            if r is None:
                print(level, LEVELS[level][0], 'TAE absent')
                continue
            roll = '/'.join(str(_dash(r['roll'][c])) for c in CHAIN_RECOVER_FLAG)
            clips = r['clips'][0] + ('..' if len(r['clips']) > 1 else '')
            name = r['name'] + (' (Large2)' if large2 else '')
            print(f"{level:>5} {name:<11} {clips:<20} {_dash(r['len']):>4} {_dash(r['r1']):>3} "
                  f"{_dash(r['r2']):>3} {_dash(r['guard']):>5} {_dash(r['move']):>4}  {roll}"
                  + (f"  spread {r['spread']}" if r['spread'] else '')
                  + (f"  (counts)" if r['counts'] else ''))
    print('Frames from the reaction clip start at 30 fps. roll@count = first roll frame after '
          'the Nth consecutive stagger.')


def print_profile(name, rows):
    print(f'\n{name}')
    print(f"{'slot':14} {'hit':>7} {'lvl':>3} {'brk':>3} {'int':>3} {'att':>4} "
          f"{'defB':>5} {'advB':>5} {'advI':>5}  combos (gap/escape on break)")
    for e in rows:
        hit = e['hit_windows'][0] if e['hit_windows'] else None
        b, i = e['on_break'] or {}, e['on_intact'] or {}
        combos = '; '.join(f"->{c['next']} {c['on_break']['gap']}/{c['on_break']['escape']} "
                           f"{c['on_break']['verdict']}" for c in e.get('combos', [])
                           if c['on_break'])
        print(f"{e['slot']:14} {str(hit):>7} {e['dmg_level']:>3} "
              f"{e['reaction_on_break']:>3} {e['reaction_poise_intact']:>3} "
              f"{_dash(b.get('attacker_ready')):>4} {_dash(b.get('defender_ready')):>5} "
              f"{_dash(b.get('advantage')):>+5} {_dash(i.get('advantage')):>+5}  {combos}")


# ---------------------------------------------------------------- selftest


def _image(va, size, path=ATTACKS.DEOBF_1162):
    with open(path, 'rb') as handle:
        handle.seek(va - 0x140000000)
        return handle.read(size)


def selftest():
    failures, passes, skips = [], [], []

    def check(name, got, want, source):
        (passes if got == want else failures).append(
            f'{name}: got {got!r} want {want!r} [{source}]')

    # 1. Constants read out of the 1.16.2 image rather than restated from this file.
    if os.path.exists(ATTACKS.DEOBF_1162):
        src = 'EXE eldenring-deobf.bin (1.16.2)'
        check('remap table 0x142bafc88', tuple(_image(REMAP_TABLE_VA, 13)), REMAP, src)
        check('fallback thresholds 0x143b179f0',
              struct.unpack('<4f', _image(FALLBACK_THRESHOLDS_VA, 16)), FALLBACK_THRESHOLDS, src)
        check('fallback SpEffect base 0x143b17a00',
              struct.unpack('<i', _image(FALLBACK_BASE_VA, 4))[0], FALLBACK_BASE, src)
        vt = struct.unpack('<10Q', _image(PLAYER_TOUGHNESS_VTABLE, 80))
        check('player toughness vtable slot 6 is the poise-holds test', vt[6], TOUGHNESS_HOLDS, src)
        check('player toughness vtable slot 9 is the SpEffect-id getter', vt[9],
              TOUGHNESS_SPEFFECT, src)
        # The general TAE flag: one setter, reached only from TAE event 600, cleared per frame.
        check('0x140416930 sets bit edx of +0x308 (cmp edx,0x3f; ja; movsxd; mov; bts; mov)',
              _image(SET_GENERAL_TAE_FLAG, 26).hex(),
              '83fa3f771548' '63c2' '488b9108030000' '480fabc2' '48899108030000', src)
        check('GetGeneralTAEFlag tests the same field (test [rsi+0x308], rax)',
              _image(GET_GENERAL_TAE_FLAG + 0x86, 7).hex(), '48858608030000', src)
        check('ExecuteThreadOne: event 600 (cmp eax,0x258) jumps to the setter branch',
              (_image(0x14042e4dc, 5).hex(), _image(0x14042e4e7, 2).hex(),
               0x14042e4ed + struct.unpack('<i', _image(0x14042e4e9, 4))[0],
               0x14042e790 + struct.unpack('<i', _image(0x14042e78c, 4))[0]),
              ('3d58020000', '0f84', 0x14042e764, SET_GENERAL_TAE_FLAG), src)
        check('PreBehaviorSafe helper 0x1404146b0 clears +0x308',
              _image(0x1404146df, 11).hex(), '48c78108030000' '00000000', src)
    else:
        skips.append('EXE constants: ' + ATTACKS.DEOBF_1162 + ' absent')

    # 2. The paramdef puts dmgLv_None where FUN_140d4ffa0 reads (row + 0x148 + level).
    for modern in (True, False):
        fields, _ = PR.layout(PR.paramdef('SP_EFFECT_PARAM_ST', modern))
        offs = {f['name']: f['off'] for f in fields}
        check(f'SpEffectParam dmgLv_* at 0x148.. (modern={modern})',
              [offs.get(n) for n in DMG_LV_FIELDS],
              list(range(DMG_LV_OFFSET, DMG_LV_OFFSET + 12)), 'Smithbox paramdef vs EXE offset')

    # 3. Regulation: the rows the remap depends on.
    reg = ATTACKS.Regulation()
    tables = Tables(reg)
    src = 'REGULATION 1.17.1'
    check('ToughnessParam row 0 spEffectId', tables.toughness_speffect(0), 6352, src)
    check('SpEffect 6352 dmgLv_*', tables.dmg_lv.get(6352),
          (0, 1, 1, 1, 6, 1, 0, 4, 1, 0, 7, 0), src)
    intact = {lv: remap(tables.dmg_lv[6352][lv], lv) for lv in range(12)}
    check('poise intact: small/middle/large/push/minimum -> none',
          [intact[lv] for lv in (1, 2, 3, 5, 8)], [0] * 5, 'derived from the two rows above')
    check('poise intact: exlarge -> small blow, small blow -> middle, ex blast -> exlarge',
          (intact[4], intact[7], intact[10]), (7, 2, 4), 'derived')

    # 4. The installed compiled HKS still carries the names the 1.08.1 decompile uses.
    if os.path.exists(INSTALLED_HKS):
        with open(INSTALLED_HKS, 'rb') as handle:
            blob = handle.read()
        for name in (b'UseChainRecover', b'DamageCount', b'ExecAddDamage', b'W_AddDamageLv0',
                     b'W_DamageLv1_Small', b'W_DamageLv8_Minimum', b'DamageCommonFunction'):
            check(f'installed c0000.hks has {name.decode()}', name in blob, True,
                  'installed HKS bytecode')
    else:
        skips.append('installed HKS: ' + INSTALLED_HKS + ' absent')

    # 5. TimeAct: EzState flag windows and roll i-frames, and the event-227 name.
    anims = common_tae()
    if anims is None:
        skips.append('TAE: a00.tae absent under ' + ATTACKS.PLAYER_TAE_DIR)
    else:
        flags = ezstate_windows(anims[5100])
        check('a000_005100 roll flags 2/3/4/5 open at', tuple(min(s for s, _ in flags[f])
                                                            for f in (2, 3, 4, 5)),
              (10, 7, 4, 0), 'TAE a00.tae')
        iframes = [(_frame(e.start)) for e in anims[ROLL_CLIP] if e.type == TAE_JUMP_TABLE
                   and struct.unpack_from('<i', e.params, 0)[0] == JT_IFRAMES]
        check('a000_027100 i-frames start on frame 0', min(iframes), 0, 'TAE a00.tae')
        small = reaction(1)
        check('small stagger: R1 at 7, roll at 10/7/4/0 by count',
              (small['r1'], tuple(small['roll'][c] for c in (1, 2, 3, 4))), (7, (10, 7, 4, 0)),
              'TAE a00.tae + HKS gate')
        additive = [e for a in LEVELS[0][2] for e in anims[a] if e.type == TAE_JUMP_TABLE]
        check('additive flinch clips carry no JumpTable events', len(additive), 0, 'TAE a00.tae')
    template = ATTACKS.TAE_TEMPLATE_ER
    if os.path.exists(template):
        with open(template, encoding='utf-8') as handle:
            text = handle.read()
        check('template names event 227 EventEzStateFlag<HKS_env301>',
              'id="227" name="EventEzStateFlag&lt;HKS_env301&gt;"' in text, True,
              'COMMUNITY: WitchyBND TAE.Template.ER.xml')
    else:
        skips.append('TAE template absent')

    # 6. A worked example against the attacks module's own numbers.
    uchi = {e['slot']: e for e in slot_profile(reg, tables, 9000000, 'both', ('2h_r1_1', '2h_r1_2'))}
    c = uchi['2h_r1_1']['combos'][0]['on_break']
    check('Uchigatana 2H R1 #1 -> #2 on break: gap 18 vs roll at 10', (c['gap'], c['escape']),
          (18, 10), 'attacks module + reaction table')

    # 7. The chained R2 #2 pays its charge-start lead-in, and nothing sets the charging flag
    #    earlier than SpEffect 100280 does.
    gs_id = reg.find_weapon('Greatsword')
    gs_rows = {r['slot']: r for r in ATTACKS.weapon_attacks(reg, gs_id, 'both')}
    gs = {e['slot']: e for e in slot_profile(reg, tables, gs_id, 'both', ('2h_r2_1', '2h_r2_2'))}
    c = gs['2h_r2_1']['combos'][0]['on_break']
    r1, r2 = gs_rows['2h_r2_1'], gs_rows['2h_r2_2']
    lead = r2.get('release_lead_in')
    want = None if lead is None else round(r1['cancel_frame']['r2'] - r1['hit_windows'][0][0]
                                           + lead + r2['hit_windows'][0][0], 1)
    check('Greatsword 2H R2 #1 -> #2: gap includes the R2 #2 release lead-in',
          (c and c['lead_in'], c and c['gap']), (lead, want), 'attacks module')
    check('Greatsword 2H R2 #2 has a release lead-in', lead is not None and lead > 0, True,
          'TAE a136_030510')
    w = reg.weapon[gs_id]
    for start in (30500, 30510):
        cat = ATTACKS.motion_category(w, start, right_hand_fallback=False)
        _, _, events = ATTACKS.resolve_events(cat, start)
        check(f'Greatsword a{cat:03d}_{start:06d} carries no TAE {TAE_SET_GENERAL_FLAG}',
              sum(e.type == TAE_SET_GENERAL_FLAG for e in events or ()), 0, 'TAE')
    fake = {'hit_windows': [(10, 12)], 'cancel_frame': {'r2': 20}}
    check('an R2 follow-up with no known lead-in has no verdict',
          combo(fake, {'hit_windows': [(5, 6)]}, 'r2', reaction(1)), None, 'rule')

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
    ap.add_argument('weapon', nargs='?', help='EquipParamWeapon id or name (default: the set '
                    'in the write-up)')
    ap.add_argument('--grip', choices=('one', 'both'), default='one')
    ap.add_argument('--count', type=int, default=1, choices=(1, 2, 3, 4),
                    help='consecutive-stagger count for the roll gate (default 1)')
    ap.add_argument('--delay', type=int, default=0,
                    help='frames between the hit and the reaction clip starting (default 0)')
    ap.add_argument('--reactions', action='store_true', help='print the reaction table only')
    ap.add_argument('--trade', nargs=2, metavar=('FIRST', 'SECOND'),
                    help='two attacks as <weapon>:<one|both>:<slot>, e.g. '
                    'Greatsword:both:2h_r1_1 Uchigatana:both:2h_r1_1')
    ap.add_argument('--offset', type=int, default=0,
                    help='with --trade: frames after FIRST that SECOND starts')
    ap.add_argument('--poise', type=float, nargs=2, default=(5.1, 5.1),
                    metavar=('FIRST', 'SECOND'),
                    help='with --trade: armor poise of each side, internal units (menu / 10)')
    ap.add_argument('--regulation')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.reactions:
        print_reactions()
        return 0
    reg = ATTACKS.Regulation(a.regulation)
    tables = Tables(reg, a.regulation)
    if a.trade:
        sides = []
        for spec in a.trade:
            name, grip, slot = spec.rsplit(':', 2)
            rows = slot_profile(reg, tables, reg.find_weapon(name), grip, (slot,))
            if not rows:
                raise SystemExit(f'{spec}: no such slot')
            sides.append(rows[0])
        out = trade(tables, sides[0], sides[1], a.offset, a.poise[1], a.poise[0])
        print(json.dumps(out, indent=1))
        return 0
    targets = ([(a.weapon, a.grip, None)] if a.weapon else DEFAULT_SET)
    result = []
    for name, grip, slots in targets:
        wid = reg.find_weapon(name)
        rows = slot_profile(reg, tables, wid, grip, slots, a.delay, a.count)
        label = f'{reg.weapon_names.get(wid)} ({wid}) {"2H" if grip == "both" else "1H"}'
        result.append({'weapon': wid, 'name': label, 'slots': rows})
        if not a.json:
            print_profile(label, rows)
    if a.json:
        print(json.dumps(result, indent=1, default=str))
    else:
        print('\nlvl = AtkParam dmgLevel; brk/int = reaction level with poise broken / intact; '
              'att = attacker frames from first hit to its first action; defB = defender frames '
              'to its first action on break; advB/advI = defB - att on break / poise intact.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
