#!/usr/bin/env python3
"""The opening a status proc makes in PvP: what the victim plays, how long it locks him, and
what follow-up damage that lets through. Write-up: `docs/er-mechanics/status.md` section 11.

    python3 scripts/er-mechanics-proc-opening.py --selftest
    python3 scripts/er-mechanics-proc-opening.py --reactions
    python3 scripts/er-mechanics-proc-opening.py --weapon "Lordsworn's Straight Sword" \
        --affinity Heavy --source "Drawstring Freezing Grease"
    python3 scripts/er-mechanics-proc-opening.py --rank --rl 150 --jobs 4 > proc-rank.txt

Labels as in the sibling docs: `VERIFIED` (code in the 1.16.2 image, byte-checked at the mapped
1.17.1 address, or a regulation value), `TAE` (player TimeAct / behavior graph), `COMMUNITY`
(Smithbox's decompile of `c0000.hks` for 1.08.1; the constants it relies on are checked in the
installed 1.17.1 bytecode), `INFERRED`.

# What a proc does to a player (static RE, 1.16.2; 1.17.1 addresses after the slash)

```
FUN_14043d8a0 / 0x14043de00   gauge < 1: CSChrResistModule+0xb8 |= 1 << status index
                              (or [rbx+0xb8], eax at 0x14043d904 / 0x14043de64)
  index = resist-module order: 0 poison, 1 rot, 2 bleed, 3 death blight, 4 frost, 5 sleep, 6 madness
  (ApplySpEffectStatusClearFlags 0x14043e250 sets the same bits by stateInfo 2/5/6/116/260/436/437)
HksEnv 0x140410820, env 409 GetDamageSpecialAttribute(i): switch byte table 0x1404133a4[409 - 223]
  = case 0x6b -> IsStatusClearFlagSet 0x14043e0d0 / 0x14043e630: test [resist+0xb8], 1 << i
ResetStatusClearFlags 0x14043d970 / 0x14043ded0: cleared by the ChrIns update 0x1404011c0 /
  0x1404014b0, so a bit lives for the update that set it
```

`ExecDamage` (`COMMUNITY`, called first by every attack, roll and stagger state through
`ExecPassiveAction`) reads the bits before anything else, the guard branch and
`GetBehaviorID(1)` included:

| bit | status | what the victim plays |
|---|---|---|
| 5 | sleep | `W_DamageSleepResist` -> `a000_005840`, whatever the damage level, poise or hyperarmor |
| 6 | madness | `W_DamageMad` -> `a000_005850`, the same |
| 2, 4 | bleed, frost | a level-0 hit (poise held) becomes `DAMAGE_LEVEL_SMALL`, unless SpEffect 6340 (Stamp stance), 1650 (Endure) or 1851 (Oath of Vengeance) is on, or the one-shot skill is `c_SwordArtsID` 136 (Seppuku's `swordArtsTypeNew`, `INFERRED` reading of the variable). A hit that already staggers keeps its own level |
| 0, 1 | poison, rot | nothing: no branch reads them |

The installed 1.17.1 `c0000.hks` carries the same constant run in `ExecDamage`'s pool (409, 5,
`W_DamageSleepResist`, 6, `W_DamageMad`, 2, 4, ..., 136, 6340, 1650, 1851); the selftest reads it.
Ordinary attack hyperarmor is not an exemption: a ToughnessParam window (SpEffect 6352/6353)
only lowers the hit's level to 0, which is exactly the case the bleed/frost branch promotes.

Proc damage and i-frames (`VERIFIED`): status SpEffect HP is summed by `FUN_1404fb920` with
`param_9` = PlayerIns vtable +0x1e8 (0x140656e90 -> `FUN_1403f3ca0`), true while
`actionModifiersFlags` bit 1 (TAE JumpTable 8, the roll's i-frames) is set, and then every
positive HP change of that update is zeroed. On the victim's machine the hit packet's status
build-up runs in `CalculateDamage2` whatever the victim's own immunity (that only sets
`+0x25f`, damage level 0), so a hit that reaches a rolling victim can still proc: the proc's HP is
lost if the tick runs while the roll's i-frames are on (`INFERRED` frame order), and the forced
reaction above still interrupts the roll.

Damage taken during the opening: frost's proc row multiplies every `*DamageCutRate` by 1.2 for
30 s and is ended early by a hit whose `AttackDamageInfo+0x26` is 11 (fire): `deleteCriteriaDamage`
9 -> `FUN_1404f67c0` case 9 (`param_4 == 0xb`), called at the end of `CalculateDamage2`. The sleep
and madness clips apply SpEffect 54 / 55 (all cut rates 1.2) through TAE event 67 for the clip's
length (`TAE` + regulation; read here, not hard-coded).

# The formula

For an attack sequence with hits j = 1..n, first hit frames T_j, per-hit build-up b_j of
status s, landing chance w_j of link j -> j+1 without a proc (frame-advantage verdicts), hit
damage D_j:

    E[opening_s] = sum_k  P_proc(k)
                   * [ (1 - p_iframe) * H_s
                       + sum_{j>k} D_j * ( m_s(T_j - T_k) * L'_j(k) - L_j(k) )
                       + (m_window - 1) * N_later(k) * D_engagement ]

* P_proc(k): chance the first proc lands on chain position k. Two readings:
  `string_shares` (every hit lands at T_j; the corpus resistances; the gauge refills r_s per
  second between hits: proc when int(R - d) - b < 1, `FUN_14043d8a0`, refill `FUN_14043e440`
  at `resistRecoverPoint_*_Player`), and `fight_shares` (status.md section 9 engagement model:
  an engagement lands hit 1 and each follow-up while the links land, engagements 5 s apart,
  bolus carriers refilled after each, a 5-engagement fight; `er-mechanics-status.simulate`).
* N_later(k) x D_engagement: frost only, the fight's later engagements inside the proc row's 30 s
  window (a carrier's bolus ends it), times one engagement's expected damage.
* L_j(k) = P(hit j lands | hit k landed) = prod_{i=k..j-1} w_i.
* L'_j(k) = 1 while T_j - T_k < E_s + delay (0.5 on equality), afterwards L'_{j-1} * w_{j-1};
  E_s = the earliest roll or guard of the proc reaction R_s (the roll gate for the stagger
  count c). With poise broken (share `stagger`) a bleed/frost proc adds no lock.
* m_s = 1.2 for frost up to 30 s, 1.2 for sleep/madness while T_j - T_k is inside the clip's
  SpEffect 54 / 55 window, else 1.
* H_s = the proc row's HP at the proc moment (status.md section 3; the first tick for poison
  and rot, whose later ticks belong to `status_expected`); p_iframe = chance the victim's own
  i-frames are on when the proc ticks; p_exempt (bleed/frost only) = chance one of the exempt
  effects is on, which scales the lock term. delay = the PvP hit-to-reaction delay (combo.md
  section 12), added to the escape frame as `er-mechanics-combo` does.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
CACHE = Path.home() / '.cache/er-build-planner'


def _sibling(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), HERE / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ST = _sibling('er-mechanics-status')
FA = _sibling('er-mechanics-frame-advantage')
ATK = FA.ATTACKS
FPS = FA.FPS
_PVP = _COMBO = _ASH = None


def pvp():
    global _PVP
    if _PVP is None:
        _PVP = _sibling('er-builds-pvp')
    return _PVP


def combo_mod():
    global _COMBO
    if _COMBO is None:
        _COMBO = _sibling('er-mechanics-combo')
    return _COMBO


def ashes():
    global _ASH
    if _ASH is None:
        _ASH = _sibling('er-mechanics-ashes')
    return _ASH


# ---------------------------------------------------------------------------------- evidence

#: Resist-module index order (`CSChrResistModule` arrays, `FUN_14043daf0` dispatch) = the bit of
#: `+0xb8` and the argument of HKS `GetDamageSpecialAttribute`.
STATUS_INDEX = {name: i for i, name in enumerate(ST.NAMES)}
#: HksEnv id of `GetDamageSpecialAttribute` (Smithbox `c0000.hks` constant list) and the
#: 1.16.2 switch: byte table at 0x1404133a4 indexed by id - `GetMapActionID` (223).
ENV_GET_DAMAGE_SPECIAL_ATTRIBUTE, ENV_GET_MAP_ACTION_ID = 409, 223
HKSENV_SWITCH_BYTES_VA, HKSENV_CASE = 0x1404133a4, 0x6b
#: Sites byte-checked in both images (1.16.2 VA, 1.17.1 VA from `map-rvas-1162-to-1170.py`,
#: every one below the 0xafefe9 boundary so 1.17.0 == 1.17.1).
SITES = {
    'IsStatusClearFlagSet (test [r8+0xb8])': (0x14043e0d0, 0x14043e630,
                                              '4c8bc1b8010000008bcad3e0418580b80000000f97c0c3'),
    'proc sets the bit (or [rbx+0xb8], eax)': (0x14043d904, 0x14043de64, '0983b8000000'),
    'SpEffect HP no-damage gate (call [r9+0x1e8])': (0x140440e68, 0x1404413c8, '41ff91e8010000'),
    'deleteCriteriaDamage read (movzx ecx, [rax+0x327])': (0x1404f6801, 0x1404f75d1, '0fb68827030000'),
}
#: Bleed/frost promotion exemptions read by `ExecDamage` (`COMMUNITY`, constants in the
#: installed bytecode): SpEffect ids and the `c_SwordArtsID` of a one-shot skill.
PROMOTE_EXEMPT_SPEFFECTS = (6340, 1650, 1851)
PROMOTE_EXEMPT_ART_TYPE = 136
#: Statuses whose proc forces a reaction, and how.
PROMOTE = ('bleed', 'frost')
CLIP = {'sleep': 5840, 'madness': 5850}
#: `ExecDamage`'s constant run in the installed bytecode: (tag 3 + big-endian float) or (tag 4 +
#: 8 bytes + length byte + string). Read by the selftest.
HKS_RUN = [409.0, 5.0, 'W_DamageSleepResist', 6.0, 'W_DamageMad', 2.0, 4.0, 'DAMAGE_LEVEL_NONE',
           'IsNodeActive', 'SwordArtsOneShot Selector', 'c_SwordArtsID', 136.0, 6340.0, 1650.0, 1851.0,
           'DAMAGE_LEVEL_SMALL']
#: Frost proc rows (`deleteCriteriaDamage` 9): ended by a fire hit (`FUN_1404f67c0` case 9).
DELETE_ON_FIRE = 9
#: TAE events that apply a SpEffect (`CSChrTaeAnimEvent::AddSpEffect`, both reach 0x14042bfd0).
TAE_ADD_SPEFFECT = (66, 67)
#: Landing weight per verdict (`er-mechanics-status.COMBO_LAND`).
LAND = ST.COMBO_LAND


# ---------------------------------------------------------------------------------- reactions

_REACT = {}
_REG = None


def _sp():
    global _REG
    if _REG is None:
        _REG = ST.Tables()
    return _REG


def clip_taken_windows(anim):
    """[(start, end, multiplier)] in frames: SpEffects the clip applies (TAE 66/67) whose damage
    cut rates are not 1. The multiplier is the slash rate (every cut rate is equal on the rows
    this finds: 54, 55)."""
    anims = FA.common_tae()
    if anims is None or anim not in anims:
        return []
    t = _sp()
    out = []
    for e in anims[anim]:
        if e.type in TAE_ADD_SPEFFECT and len(e.params) >= 4:
            sid = struct.unpack_from('<i', e.params, 0)[0]
            row = t.sp.get(sid)
            if row and abs(row['slashDamageCutRate'] - 1.0) > 1e-6:
                out.append((round(e.start * FPS), round(min(e.end, 1e4) * FPS), row['slashDamageCutRate'], sid))
    return out


def clip_reaction(anim, name):
    """A forced reaction clip in the shape `FA.reaction` returns. `ExecDamage` neither counts nor
    resets `DamageCount` for these, and their clips carry no EzState flag 2..5, so the roll is the
    TAE window (`roll_tae`), read with `UseChainRecover` off (`INFERRED`)."""
    t = FA.clip_timing(anim)
    if t is None:
        return None
    return {'level': None, 'name': name, 'event': None, 'locks': True, 'counts': False,
            'clips': [t['anim']], 'len': t['len'], 'r1': t['r1'], 'r2': t['r2'], 'guard': t['guard'],
            'move': t['move'], 'roll': {c: t['roll_tae'] for c in FA.CHAIN_RECOVER_FLAG},
            'taken': clip_taken_windows(anim)}


def level_reaction(level):
    if level not in _REACT:
        _REACT[level] = FA.reaction(level)
    return _REACT[level]


def proc_reaction(status, hit_level=0, exempt=False):
    """What the victim plays when `status` procs on a hit whose own reaction level is `hit_level`
    (0 = poise held). None for a status that adds nothing to the hit's own reaction."""
    if status in CLIP:
        key = ('clip', status)
        if key not in _REACT:
            _REACT[key] = clip_reaction(CLIP[status], status)
        return _REACT[key]
    if status in PROMOTE and hit_level == 0 and not exempt:
        r = dict(level_reaction(1))
        r['name'] = f'{status} -> small'
        return r
    return None


def escape(react, count=1, delay=0):
    """Earliest frame the victim leaves `react` by rolling or guarding, plus `delay`."""
    if react is None or not react['locks']:
        return 0
    opts = [v for v in (react['roll'][count], react['guard']) if v is not None]
    return min(opts) + delay if opts else 0


def damage_mult(status, react, dt, proc_row=None):
    """Damage-taken multiplier on a hit `dt` frames after the proc."""
    m = 1.0
    if status == 'frost' and proc_row is not None:
        row = _sp().sp[proc_row]
        if dt < row['effectEndurance'] * FPS:
            m *= row['slashDamageCutRate']
    if react is not None:
        for s, e, mult, _ in react.get('taken') or ():
            if s <= dt < e:
                m *= mult
    return m


# ---------------------------------------------------------------------------------- formula

def first_proc(builds, times, resist, recover, d0=0.0):
    """1-based hit on which a defender of resistance `resist` first procs, or None.

    `builds` ints per hit, `times` frames per hit; gauge refills `recover` per second between
    hits; `d0` = depletion already on the gauge at hit 1. Proc when int(gauge) - b < 1
    (`FUN_14043d8a0`, as `er-mechanics-status.simulate`)."""
    gauge = max(0.0, float(resist) - d0)
    for j, b in enumerate(builds):
        if j:
            gauge = min(float(resist), gauge + recover * (times[j] - times[j - 1]) / FPS)
        if b > 0 and int(gauge) - b < 1:
            return j + 1
        gauge -= b
    return None


def proc_shares(builds, times, defenders, recover, d0=0.0):
    """[P_first_proc(k)] for k = 1..n over `defenders` [(resistance, weight)], given every hit
    lands."""
    tot = sum(w for _, w in defenders) or 1.0
    out = [0.0] * len(builds)
    for r, w in defenders:
        k = first_proc(builds, times, r, recover, d0)
        if k:
            out[k - 1] += w / tot
    return out


def string_shares(seq, defenders, recover, d0=0.0):
    """P_first_proc(k) when every hit of `seq` lands at its `t` (the user's 'hits land, gauge
    decays between them' reading)."""
    return proc_shares([h['b'] for h in seq], [h['t'] for h in seq], defenders, recover, d0)


def fight_shares(seq, groups, recover, lock_s, engagements, eng_s=None, window_s=0.0):
    """P(the fight's first proc lands on chain position k), engagement model of status.md
    section 9: each engagement opens with hit 1 and keeps landing while the links do
    (`er-mechanics-status.engagement_lengths` over each link's `w_mix`), engagements `eng_s`
    apart, the gauge refilling in between and a carrier's bolus refilling it after each
    (`er-mechanics-status.simulate`). `groups` = [(resistance, carrier share, count, hp sum)].

    Returns (shares, later): later[k] = expected number of the fight's later engagements that
    start inside `window_s` seconds of a proc on position k (frost's x1.2 reaches them)."""
    eng_s = ST.ENGAGEMENT_SECONDS if eng_s is None else eng_s
    chain = [{'p': h.get('w_mix', h['w'])} for h in seq[:-1]]
    lengths = ST.engagement_lengths(chain)
    out, later = [0.0] * len(seq), [0.0] * len(seq)
    total = sum(g[2] for g in groups) or 1
    reach = int(window_s // eng_s) if eng_s > 0 else 0
    for L, q in lengths:
        builds = tuple(int(h['b']) for h in seq[:L])
        gaps = tuple(seq[i + 1]['t'] - seq[i]['t'] for i in range(L - 1))
        for resist, carrier, count, _hp in groups:
            for c, wc in ((True, carrier), (False, 1.0 - carrier)):
                if wc <= 0:
                    continue
                _, _, first = ST.simulate(builds, gaps, resist, recover, lock_s, c, engagements, eng_s)
                if first:
                    e, landed = first
                    w = q * wc * count / total
                    k = landed - (e - 1) * L - 1
                    out[k] += w
                    # A carrier's bolus ends the frost entry after the engagement (status.md s9).
                    later[k] += w * (0 if c else min(reach, engagements - e))
    return out, [later[k] / out[k] if out[k] else 0.0 for k in range(len(seq))]


def opening(seq, status, shares, proc_hp, proc_row=None, stagger=0.0, count=1, delay=0,
            p_iframe=0.0, p_exempt=0.0, later=None, eng_dmg=0.0):
    """E[opening] of `status` over one attack sequence (module docstring formula).

    `seq` = [{'t': first hit frame, 'dmg': damage, 'w': landing chance of the link to the next
    hit with poise held, 'w_break': the same on a break (default 'w'), 'w_mix': the
    stagger-weighted chance (default 'w')}]. `shares[k]` = P(first proc on hit k + 1), from
    `string_shares` or `fight_shares`. `stagger` = share of defenders whose poise the proc hit
    breaks (a hit's own 'stagger' wins). `later[k]` x `eng_dmg` = damage of later engagements
    inside the proc row's damage-taken window (`fight_shares`), multiplied by its rate - 1.
    Returns the total, its parts and, per k, the opening given a proc there."""
    n = len(seq)
    intact = proc_reaction(status, 0)
    lock_intact = escape(intact, count, delay)
    window_mult = 1.0
    if status == 'frost' and proc_row is not None:
        window_mult = _sp().sp[proc_row]['slashDamageCutRate']
    out_k = []
    total = hp_part = follow_part = window_part = 0.0
    for k in range(n):
        pk = shares[k]
        gain = 0.0
        stg = seq[k].get('stagger', stagger)
        for side, share in (('intact', 1.0 - stg), ('break', stg)):
            if share <= 0:
                continue
            # Sleep and madness replace any stagger; bleed and frost lock only a held poise.
            react = intact if (side == 'intact' or status in CLIP) else None
            lock = lock_intact if react is not None else 0
            eff = 1.0 - p_exempt if status in PROMOTE else 1.0
            base_l = with_l = 1.0
            g = 0.0
            for j in range(k + 1, n):
                h = seq[j - 1]
                if j - 1 == k:
                    w_prev = h.get('w_break', h['w']) if side == 'break' else h['w']
                else:
                    w_prev = h.get('w_mix', h['w'])
                base_l *= w_prev
                dt = seq[j]['t'] - seq[k]['t']
                if react is not None and dt <= lock:
                    with_l = eff * (1.0 if dt < lock else 0.5) + (1.0 - eff) * base_l
                else:
                    with_l *= w_prev
                m = damage_mult(status, react, dt, proc_row)
                g += seq[j]['dmg'] * (m * with_l - base_l)
            gain += share * g
        hp = (1.0 - p_iframe) * proc_hp
        win = (window_mult - 1.0) * (later[k] if later else 0.0) * eng_dmg
        hp_part += pk * hp
        follow_part += pk * gain
        window_part += pk * win
        total += pk * (hp + gain + win)
        out_k.append({'k': k + 1, 'p': pk, 'proc_hp': hp, 'follow_gain': gain, 'window': win,
                      'given_proc': hp + gain + win})
    return {'total': total, 'proc_hp_part': hp_part, 'follow_part': follow_part, 'window_part': window_part,
            'p_proc': sum(o['p'] for o in out_k), 'lock': lock_intact, 'by_k': out_k}


# ---------------------------------------------------------------------------------- sequences

def chain_sequence(rows, link_start, dmg_of, w_of):
    """[{'t','dmg','w','row'}] for consecutive attack rows. `link_start(prev_row)` = the frame the
    next attack's clip starts, from the previous clip's start (cancel frame); gap = start - h0 +
    h0' (`er-mechanics-combo.Model.link`, start clamped to the first hit's end when earlier)."""
    seq, t = [], 0.0
    for i, r in enumerate(rows):
        if i:
            p = rows[i - 1]
            h0 = p['hit_windows'][0][0]
            start = link_start(p)
            if start is None:
                break
            if start < h0:
                start = p['hit_windows'][0][1]
            t = t + start - h0 + r['hit_windows'][0][0]
        seq.append({'t': round(t, 1), 'row': r, 'dmg': dmg_of(r)})
    for i, h in enumerate(seq):
        h['w'], h['w_break'], h['w_mix'] = (w_of(h['row'], seq[i + 1]['t'] - h['t']) if i + 1 < len(seq)
                                             else (0.0, 0.0, 0.0))
    return seq


def base_link_weights(model, row, gap):
    """(w intact, w broken, stagger-weighted) of a link without a proc, from the hit's own
    reactions (`er-mechanics-combo.Model.verdict_side`; true 1, tie 0.5)."""
    vi = model.verdict_side(row, gap, False)
    vb = model.verdict_side(row, gap, True)
    land = {'true': 1.0, 'tie': 0.5}
    wi, wb = land.get(vi.get('verdict'), 0.0), land.get(vb.get('verdict'), 0.0)
    s = model.stagger(row) or 0.0
    return wi, wb, (1.0 - s) * wi + s * wb


# ---------------------------------------------------------------------------------- ranking

#: Status sources tried on every weapon: greases by name (`ST.Tables.greases`) and weapon-buff
#: skills by (skill, on-attack SpEffect read from the buff row's `atkOccurrenceSpEffectId`).
RANK_GREASES = ('Drawstring Freezing Grease', 'Drawstring Blood Grease', 'Drawstring Soporific Grease',
                'Drawstring Poison Grease', 'Drawstring Rot Grease')
RANK_SKILL_BUFFS = {'Chilling Mist': {'right': 826, 'left': 828}, 'Poisonous Mist': {'right': 831, 'left': 831}}
LEFT_AFFINITIES = ('Standard', 'Heavy', 'Cold')


class Ranker:
    def __init__(self, rl=150, window=10, mirror=CACHE / 'builds.jsonl', delay=0, p_iframe=0.0, d0=0.0,
                 engagements=None):
        P = pvp()
        self.P, self.C = P, combo_mod()
        self.t = ST.Tables()
        self.tables = self.t.ar
        self.model = self.C.Model(rl=rl, window=window, mirror=mirror, delay=delay)
        self.pt = P.PvpTables()
        self.dfn = P.Defenders(P.pvp_corpus(Path(mirror), rl - window, rl + window))
        self.st_dfs = ST.Defenders(self.t, ST.corpus_rows(rl=rl, window=window))
        self.hp = self.st_dfs.median_hp
        self.delay, self.p_iframe, self.d0 = delay, p_iframe, d0
        self.engagements = engagements or self.st_dfs.fight_engagements
        files = ST.EPR.load(None)
        rows, _, _ = ST.EPR.rows(ST.EPR.param_bytes(files, 'EquipParamWeapon'), ['isEnhance'])
        #: `EquipParamWeapon.isEnhance` per exact row: whether a grease or buff skill can apply.
        self.enhance = {r['id']: r['isEnhance'] for r in rows}
        self.ash = ashes()
        self.ash_t = self.ash.AshTables()

    def defenders(self, status):
        acc = {}
        for r, _carrier, n, _hp in self.st_dfs.groups[status]:
            acc[r] = acc.get(r, 0) + n
        return sorted(acc.items())

    def can_grease(self, aff_id):
        return bool(self.enhance.get(aff_id, 0))

    def mountable(self, base_id, aff, level, skill):
        arts = self.ash_t.find_arts(skill)
        i = ST.AR.AFFINITIES.index(aff)
        return arts in self.ash.mountable_skills(self.ash_t, base_id, i, level)

    def sources(self, ws, base_id, aff_id, aff, level, hand):
        out = [('innate', None)] if ws['sources'] else []
        if self.can_grease(aff_id):
            gr = self.t.greases()
            out += [(g, gr[g]) for g in RANK_GREASES if g in gr]
        for skill, rows in RANK_SKILL_BUFFS.items():
            if self.mountable(base_id, aff, level, skill):
                out.append((skill, self.t.sp[rows[hand]]['atkOccurrenceSpEffectId']))
        return out

    def evaluate(self, label, rows, link_start, wid_aff, ar_by, ws, src_name, src_id):
        """Every status a source carries on one chain: E[opening] and the parts."""
        P = self.P

        def dmg_of(r):
            return P.slot_hit(self.pt, self.model.reg, wid_aff, r, ar_by, self.dfn)['dmg']

        def w_of(r, gap):
            return base_link_weights(self.model, r, gap)
        seq = chain_sequence(rows, link_start, dmg_of, w_of)
        if len(seq) < 2:
            return []
        for h in seq:
            h['stagger'] = self.model.stagger(h['row']) or 0.0
        per = {}
        for h in seq:
            b = ST.use_buildup(self.t, ws, h['row'], src_id) if src_id else ST.use_buildup(self.t, ws, h['row'])
            h['b_all'] = b
            for s in b:
                per.setdefault(s, None)
        out = []
        for s in per:
            if src_name == 'innate' and not any(x['status'] == s for x in ws['sources']):
                continue
            if src_name != 'innate' and src_id is not None:
                rs = ST.row_status(self.t.sp[src_id])
                if not rs or rs[0] != s:
                    continue
            hs = [{'t': h['t'], 'b': int(h['b_all'].get(s, 0)), 'dmg': h['dmg'], 'w': h['w'],
                   'w_break': h['w_break'], 'w_mix': h['w_mix'], 'stagger': h['stagger']} for h in seq]
            if not any(h['b'] for h in hs):
                continue
            row = ST.proc_row_for(self.t, ws, s, src_id if src_name != 'innate' else None)
            # H_s is the HP at the proc moment: the whole proc for one-tick rows (bleed, frost,
            # madness), the first tick for poison and rot, whose later ticks are
            # `er-mechanics-status.status_expected`'s job (fight end, cure).
            pe = ST.proc_effect(self.t, row, self.hp) if row else None
            hp = (pe['hp_per_tick'] if pe['ticks'] > 1 else pe['hp_total']) if pe else 0.0
            lock_s = self.t.sp[row]['effectEndurance'] if row else 0.0
            fight, later = fight_shares(hs, self.st_dfs.groups[s], self.t.recover[s], lock_s, self.engagements,
                                        window_s=lock_s if s == 'frost' else 0.0)
            string = string_shares(hs, self.defenders(s), self.t.recover[s], self.d0)
            # Expected damage of one engagement: hit 1, then each follow-up while the links land.
            eng_dmg, p = 0.0, 1.0
            for i, h in enumerate(hs):
                eng_dmg += p * h['dmg']
                p *= h['w_mix'] if i + 1 < len(hs) else 0.0
            args = (hp, row, 0.0, 1, self.delay, self.p_iframe, 0.0)
            o = opening(hs, s, fight, *args, later=later, eng_dmg=eng_dmg)
            o_str = opening(hs, s, string, *args)
            out.append({'chain': label, 'status': s, 'source': src_name, 'buildup': [h['b'] for h in hs],
                        'gaps': [round(hs[i + 1]['t'] - hs[i]['t'], 1) for i in range(len(hs) - 1)],
                        'dmg': [round(h['dmg']) for h in hs], 'stagger': [round(h['stagger'], 2) for h in hs],
                        'links': [round(h['w_mix'], 2) for h in hs[:-1]],
                        'p_proc': round(o['p_proc'], 4), 'lock': o['lock'],
                        'proc_hp_part': round(o['proc_hp_part'], 1), 'follow_part': round(o['follow_part'], 1),
                        'window_part': round(o['window_part'], 1), 'total': round(o['total'], 1),
                        'given_proc': [round(x['given_proc'], 1) for x in o['by_k']],
                        'string_p_proc': round(o_str['p_proc'], 4), 'string_total': round(o_str['total'], 1)})
        return out

    def right_row(self, row):
        P = self.P
        b = P.build_for(row, False)
        if b is None:
            return []
        wid = self.tables.find_weapon(row['weapon'], b['aff'])
        base = self.tables.find_weapon(row['weapon'], 'Standard')
        level = self.tables.max_level(self.tables.weapons[wid]['reinforceTypeId'])
        stats = {k: b['stats'][k] for k in ('str', 'dex', 'int', 'fth', 'arc')}
        r = ST.AR.attack_rating(self.tables, row['weapon'], b['aff'], level, stats, row['two'])
        ar_by = {el: r['damage'].get(el, {}).get('total', 0.0) for el in P.ELEMENTS}
        ws = ST.weapon_status(self.t, row['weapon'], b['aff'], level, stats, row['two'], True)
        grip = 'both' if row['two'] else 'one'
        rows = {x['slot'].removeprefix('2h_'): x for x in ATK.weapon_attacks(self.model.reg, wid, grip, level)
                if x.get('hit_windows')}
        chain = [rows[f'r1_{i}'] for i in range(1, 7) if f'r1_{i}' in rows]
        out = []
        for name, sid in self.sources(ws, base, wid, b['aff'], level, 'right'):
            for o in self.evaluate(f"{'2H' if row['two'] else '1H'} R1 chain", chain,
                                   lambda p: (p.get('cancel_frame') or {}).get('r1'), wid, ar_by, ws, name, sid):
                o.update({'weapon': row['weapon'], 'aff': b['aff'], 'hand': 'right 2H' if row['two'] else 'right 1H',
                          'two': row['two']})
                out.append(o)
        return out

    def left_weapon(self, base_id, stats):
        out = []
        reg = self.model.reg
        for aff in LEFT_AFFINITIES:
            try:
                wid = self.tables.find_weapon(base_id, aff)
            except SystemExit:
                continue
            if wid not in reg.weapon:
                continue
            level = self.tables.max_level(self.tables.weapons[wid]['reinforceTypeId'])
            name = self.tables.names.get(base_id)
            try:
                r = ST.AR.attack_rating(self.tables, base_id, aff, level, stats, False)
            except SystemExit:
                continue
            ar_by = {el: r['damage'].get(el, {}).get('total', 0.0) for el in self.P.ELEMENTS}
            ws = ST.weapon_status(self.t, base_id, aff, level, stats, False, True)
            off = self.C.offhand_attacks(reg, wid, level)
            chain = [x for x in off if x.get('hit_windows')]
            if len(chain) < 2:
                return out
            for sname, sid in self.sources(ws, base_id, wid, aff, level, 'left'):
                for o in self.evaluate('off-hand L1 chain', chain, lambda p: p.get('l1_start'), wid, ar_by, ws,
                                       sname, sid):
                    o.update({'weapon': name, 'aff': aff, 'hand': 'left', 'two': False})
                    out.append(o)
        return out


def _rank_worker(args):
    kind, payload, opts = args
    rk = _RANKER
    try:
        return rk.right_row(payload) if kind == 'right' else rk.left_weapon(payload, opts['stats'])
    except Exception as e:                                       # one weapon must not end the run
        return [{'error': f'{kind} {payload if kind == "left" else payload.get("weapon")}: {e!r}'}]


_RANKER = None


def rank(a):
    global _RANKER
    _RANKER = Ranker(a.rl, a.window, a.mirror, a.delay, a.p_iframe, a.depletion, a.engagements)
    rows = [json.loads(line) for line in Path(a.sweep).open()]
    rows = [r for r in rows if r['rl'] == a.rl]
    if a.limit:
        rows = rows[:a.limit]
    stats = dict(combo_mod().REFERENCE_STATS)
    lefts = []
    for wid in combo_mod().base_weapons(_RANKER.model.reg):
        cat = _RANKER.tables.weapons.get(wid, {}).get('weaponCategory')
        if cat in ST.NON_MELEE_CATEGORIES:
            continue
        lefts.append(wid)
    if a.limit:
        lefts = lefts[:a.limit]
    jobs = [('right', r, {}) for r in rows] + ([('left', w, {'stats': stats}) for w in lefts] if not a.no_left else [])
    if a.jobs > 1:
        import multiprocessing as mp
        with mp.get_context('fork').Pool(a.jobs) as pool:
            res = pool.map(_rank_worker, jobs, chunksize=4)
    else:
        res = [_rank_worker(j) for j in jobs]
    flat = [o for r in res for o in r]
    errors = [o['error'] for o in flat if 'error' in o]
    flat = [o for o in flat if 'error' not in o]
    flat.sort(key=lambda o: -o['total'])
    if a.json:
        Path(a.json).write_text(json.dumps({'rows': flat, 'errors': errors}, indent=1))
    print(f"# RL {a.rl}: {len(rows)} right-hand sweep rows, {len(lefts) if not a.no_left else 0} off-hand "
          f"weapons, {len(flat)} (chain, status, source) results, {len(errors)} errors; delay {a.delay}, "
          f"p_iframe {a.p_iframe}, engagements {_RANKER.engagements} x {ST.ENGAGEMENT_SECONDS:.0f} s")
    print('\n## E[opening] per chain over the fight (engagement model)\n')
    print('| # | weapon | aff | hand | chain | source | status | build-up/hit | gaps | links | P(proc) |'
          ' lock | proc HP | follow-up | x1.2 later | E[opening] | given proc on hit 1 |')
    print('|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|')
    for i, o in enumerate(flat[:a.top], 1):
        print(f"| {i} | {o['weapon']} | {o['aff']} | {o['hand']} | {o['chain']} | {o['source']} | {o['status']} | "
              f"{'/'.join(map(str, o['buildup']))} | {'/'.join(map(str, o['gaps']))} | "
              f"{'/'.join(map(str, o['links']))} | {o['p_proc']:.3f} | {o['lock']} | {o['proc_hp_part']:.1f} | "
              f"{o['follow_part']:.1f} | {o['window_part']:.1f} | {o['total']:.1f} | {o['given_proc'][0]:.0f} |")
    print('\n## Opening given a proc on hit 1 (no proc probability)\n')
    print('| # | weapon | aff | hand | source | status | lock | gaps | given proc on hit 1 | P(proc) |')
    print('|---|---|---|---|---|---|---|---|---|---|')
    for i, o in enumerate(sorted(flat, key=lambda o: -o['given_proc'][0])[:a.top], 1):
        print(f"| {i} | {o['weapon']} | {o['aff']} | {o['hand']} | {o['source']} | {o['status']} | {o['lock']} | "
              f"{'/'.join(map(str, o['gaps']))} | {o['given_proc'][0]:.0f} | {o['p_proc']:.3f} |")
    by_status = {}
    for o in flat:
        by_status.setdefault(o['status'], []).append(o)
    print('\n## Best per status')
    for s, lst in sorted(by_status.items()):
        lst = lst[:a.per_status]
        print(f"\n### {s}")
        for o in lst:
            print(f"- {o['weapon']} ({o['aff']}, {o['hand']}, {o['source']}): {o['total']:.1f} "
                  f"(P {o['p_proc']:.3f}, lock {o['lock']}, follow {o['follow_part']:.1f}, gaps {o['gaps']})")
    if errors:
        print('\n# errors (first 10): ' + '; '.join(errors[:10]))


# ---------------------------------------------------------------------------------- CLI views

def print_reactions(count=1, delay=0):
    print('| status | proc reaction | clip | len | R1 | guard | roll | lock (roll/guard) | damage taken x |')
    print('|---|---|---|---|---|---|---|---|---|')
    for s in ST.NAMES:
        r = proc_reaction(s, 0)
        taken = ', '.join(f"x{m:.2f} f{a}-{b} (SpEffect {sid})" for a, b, m, sid in (r or {}).get('taken') or [])
        if s == 'frost':
            taken = 'x1.20 for 30 s (proc row cut rates)' + (f'; {taken}' if taken else '')
        if r is None:
            print(f"| {s} | none | - | - | - | - | - | 0 | {taken or '-'} |")
            continue
        print(f"| {s} | {r['name']} | {', '.join(r['clips'][:2])}{' ...' if len(r['clips']) > 2 else ''} | "
              f"{r['len']} | {r['r1']} | {r['guard']} | {r['roll'][count]} | {escape(r, count, delay)} | "
              f"{taken or '-'} |")


def show_weapon(a):
    rk = Ranker(a.rl, a.window, a.mirror, a.delay, a.p_iframe, a.depletion, a.engagements)
    stats = {k: int(v) for k, v in (kv.split('=') for kv in a.stats.split(',') if kv)}
    stats = {**combo_mod().REFERENCE_STATS, **stats}
    wid = rk.tables.find_weapon(a.weapon, a.affinity)
    level = rk.tables.max_level(rk.tables.weapons[wid]['reinforceTypeId'])
    r = ST.AR.attack_rating(rk.tables, a.weapon, a.affinity, level, stats, a.two_handed)
    ar_by = {el: r['damage'].get(el, {}).get('total', 0.0) for el in rk.P.ELEMENTS}
    ws = ST.weapon_status(rk.t, a.weapon, a.affinity, level, stats, a.two_handed, True)
    if a.left:
        chain = [x for x in rk.C.offhand_attacks(rk.model.reg, wid, level) if x.get('hit_windows')]
        start = lambda p: p.get('l1_start')
    else:
        rows = {x['slot'].removeprefix('2h_'): x
                for x in ATK.weapon_attacks(rk.model.reg, wid, 'both' if a.two_handed else 'one', level)
                if x.get('hit_windows')}
        chain = [rows[f'r1_{i}'] for i in range(1, 7) if f'r1_{i}' in rows]
        start = lambda p: (p.get('cancel_frame') or {}).get('r1')
    gr = rk.t.greases()
    if a.source in (None, 'innate'):
        src = ('innate', None)
    elif a.source in gr:
        src = (a.source, gr[a.source])
    elif a.source in RANK_SKILL_BUFFS:
        src = (a.source, rk.t.sp[RANK_SKILL_BUFFS[a.source]['left' if a.left else 'right']]['atkOccurrenceSpEffectId'])
    else:
        src = (a.source, int(a.source))
    res = rk.evaluate('L1 chain' if a.left else 'R1 chain', chain, start, wid, ar_by, ws, *src)
    print(json.dumps(res, indent=1))


# ---------------------------------------------------------------------------------- selftest

def _hks_run_present(blob):
    """Whether `HKS_RUN` appears in order, each constant adjacent to the previous."""
    def enc(v):
        if isinstance(v, float):
            return b'\x03' + struct.pack('>f', v)
        s = v.encode() + b'\x00'
        return b'\x04' + b'\x00' * 7 + bytes([len(s)]) + s
    parts = [enc(v) for v in HKS_RUN]
    i = blob.find(parts[0])
    while i >= 0:
        j, ok = i + len(parts[0]), True
        for p in parts[1:]:
            k = blob.find(p, j, j + 64)
            if k < 0:
                ok = False
                break
            j = k + len(p)
        if ok:
            return True
        i = blob.find(parts[0], i + 1)
    return False


def selftest():
    fails, skips, n = [], [], 0

    def check(label, got, want):
        nonlocal n
        n += 1
        ok = got == want if not isinstance(want, float) else abs(got - want) < 1e-3
        print(f"  {'ok' if ok else 'FAIL'}  {label}: {got!r}" + ('' if ok else f' (want {want!r})'))
        if not ok:
            fails.append(label)

    # 1. EXE: the HksEnv switch byte and the byte-checked sites in both images.
    img16, img17 = REPO / 'eldenring-deobf.bin', REPO / 'eldenring-deobf-1.17.1.bin'
    if img16.exists():
        with open(img16, 'rb') as f:
            f.seek(HKSENV_SWITCH_BYTES_VA - 0x140000000 + ENV_GET_DAMAGE_SPECIAL_ATTRIBUTE - ENV_GET_MAP_ACTION_ID)
            check('env 409 -> HksEnv case 0x6b (IsStatusClearFlagSet)', f.read(1)[0], HKSENV_CASE)
    else:
        skips.append('1.16.2 image')
    for label, (va16, va17, hexb) in SITES.items():
        for img, va in ((img16, va16), (img17, va17)):
            if not img.exists():
                skips.append(img.name)
                continue
            with open(img, 'rb') as f:
                f.seek(va - 0x140000000)
                check(f'{label} @ {va:#x} in {img.name}', f.read(len(hexb) // 2).hex(), hexb)
    # 2. Installed 1.17.1 HKS: ExecDamage's constant run.
    hks = Path(FA.INSTALLED_HKS)
    if hks.exists():
        check("installed c0000.hks ExecDamage constants (409, 5 sleep, 6 madness, 2/4, 136, 6340, 1650, 1851)",
              _hks_run_present(hks.read_bytes()), True)
    else:
        skips.append('installed HKS')
    # 3. Regulation: exempt rows, frost proc row, clip SpEffects.
    t = _sp()
    check('6340 is the Stamp stance row', t.sp_names.get(6340, '').startswith('Toughness Unk'), True)
    check('1650 Endure / 1851 Oath of Vengeance',
          ('Endure' in t.sp_names.get(1650, ''), 'Oath of Vengeance' in t.sp_names.get(1851, '')), (True, True))
    frost_rows = [r for r in t.sp.values() if r['stateInfo'] == 260 and r['freezeAttackPower'] > 0]
    check('frost rows with deleteCriteriaDamage 9 (fire ends them)',
          all(r.get('deleteCriteriaDamage', DELETE_ON_FIRE) == DELETE_ON_FIRE for r in frost_rows[:1]), True)
    check('Chilling Mist buff 826 / 828 -> on-attack 880, frost 60',
          (t.sp[826]['atkOccurrenceSpEffectId'], t.sp[828]['atkOccurrenceSpEffectId'], ST.row_status(t.sp[880])),
          (880, 880, ('frost', 60)))
    # 4. TAE: forced clips, their locks and damage-taken windows.
    if FA.common_tae() is not None:
        sl, md = proc_reaction('sleep'), proc_reaction('madness')
        check('sleep clip a000_005840: R1 / guard / roll', (sl['r1'], sl['guard'], sl['roll'][1]), (66, 69, 66))
        check('madness clip a000_005850: R1 / guard / roll', (md['r1'], md['guard'], md['roll'][1]), (94, 94, 75))
        check('sleep clip applies SpEffect 54 x1.2 f0-115', [(a, b, round(m, 2), s) for a, b, m, s in sl['taken']],
              [(0, 115, 1.2, 54)])
        check('madness clip applies SpEffect 55 x1.2 f0-130', [(a, b, round(m, 2), s) for a, b, m, s in md['taken']],
              [(0, 130, 1.2, 55)])
        fr = proc_reaction('frost', 0)
        check('frost on a held poise -> small, roll at 10 / 7 / 4 / 0', [fr['roll'][c] for c in (1, 2, 3, 4)],
              [10, 7, 4, 0])
        check('frost with Endure (exempt) -> nothing', proc_reaction('frost', 0, exempt=True), None)
        check('bleed on a broken poise keeps the hit level', proc_reaction('bleed', 2), None)
        check('poison / rot -> nothing', (proc_reaction('poison'), proc_reaction('scarlet_rot')), (None, None))
    else:
        skips.append('a00.tae')
    # 5. Formula arithmetic (synthetic, no game data).
    check('first proc: 300 resist, 100/hit back to back -> hit 3', first_proc([100] * 5, [0, 10, 20, 30, 40], 300, 0),
          3)
    check('first proc: 150/hit, refill 30/s over 3 s gaps -> hit 4 instead of 2',
          (first_proc([150] * 5, [0, 1, 2, 3, 4], 300, 0), first_proc([150] * 5, [0, 90, 180, 270, 360], 300, 30)),
          (2, 4))
    check('shares over two defenders', proc_shares([100] * 4, [0, 10, 20, 30], [(250, 1), (350, 1)], 0),
          [0.0, 0.0, 0.5, 0.5])
    two = [{'t': 0, 'b': 100, 'w': 0.0}, {'t': 18, 'b': 100, 'w': 0.0}]
    # One-hit engagements (link 'no'): 100 per engagement against 250, no refill -> third
    # engagement, carried by hit 1; a carrier boluses after each and never procs.
    sh, later = fight_shares(two, [(250, 0.0, 1, 0), (250, 1.0, 1, 0)], 0.0, 1.0, 5, 5.0, window_s=30.0)
    check('fight shares: one-hit engagements put every proc on hit 1, carriers never proc',
          [round(x, 6) for x in sh], [0.5, 0.0])
    check('fight shares: a proc in engagement 3 of 5 leaves 2 engagements in a 30 s window', later[0], 2.0)
    two_true = [dict(two[0], w=1.0), two[1]]
    check('fight shares: a true link lets hit 2 carry the proc',
          [round(x, 6) for x in fight_shares(two_true, [(150, 0.0, 1, 0)], 0.0, 1.0, 5, 5.0)[0]], [0.0, 1.0])
    if FA.common_tae() is not None:
        seq = [{'t': 0, 'dmg': 100, 'w': 0.0}, {'t': 30, 'dmg': 100, 'w': 0.0},
               {'t': 60, 'dmg': 100, 'w': 0.0}, {'t': 90, 'dmg': 100, 'w': 0.0}]
        o = opening(seq, 'sleep', [1.0, 0, 0, 0], 0.0)
        check('sleep proc on hit 1: hits at 30 and 60 land x1.2 (lock 66), 90 does not', o['total'], 240.0)
        seq2 = [{'t': 0, 'dmg': 100, 'w': 0.0}, {'t': 8, 'dmg': 100, 'w': 0.0}]
        o2 = opening(seq2, 'frost', [1.0, 0], 225.0, proc_row=829)
        check('frost proc: gap 8 < roll 10 lands x1.2 plus the 225 proc', o2['total'], 345.0)
        o3 = opening(seq2, 'frost', [1.0, 0], 225.0, proc_row=829, p_iframe=1.0, p_exempt=1.0)
        check('frost proc in i-frames with Endure on: no HP, no lock', o3['total'], 0.0)
        seq3 = [{'t': 0, 'dmg': 100, 'w': 0.0}, {'t': 18, 'dmg': 100, 'w': 0.0}]
        check('frost proc: gap 18 > roll 10 adds no follow-up',
              opening(seq3, 'frost', [1.0, 0], 0.0, proc_row=829)['total'], 0.0)
        check('PvP delay 9: gap 18 < 10 + 9, the follow-up lands x1.2',
              opening(seq3, 'frost', [1.0, 0], 0.0, proc_row=829, delay=9)['total'], 120.0)
        seq4 = [{'t': 0, 'dmg': 100, 'w': 1.0}, {'t': 18, 'dmg': 100, 'w': 0.0}]
        check('frost x1.2 on a follow-up that lands anyway',
              opening(seq4, 'frost', [1.0, 0], 0.0, proc_row=829)['total'], 20.0)
        check('frost x1.2 on two later engagements of 300',
              opening(seq3, 'frost', [1.0, 0], 0.0, proc_row=829, later=[2.0, 0], eng_dmg=300.0)['total'], 120.0)
        check('half the defenders broken: a bleed proc locks only the held half',
              opening(seq2, 'bleed', [1.0, 0], 0.0, stagger=0.5)['total'], 50.0)
        check('madness through a broken poise still locks (forced clip): gap 8 -> x1.2 on both halves',
              opening(seq2, 'madness', [1.0, 0], 0.0, stagger=0.5)['total'], 120.0)
    print(f"{n - len(fails)}/{n} passed" + (f"; skipped: {', '.join(sorted(set(skips)))}" if skips else ''))
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--reactions', action='store_true', help='per-status proc reaction table')
    ap.add_argument('--rank', action='store_true', help='rank weapons x status sources at --rl')
    ap.add_argument('--weapon')
    ap.add_argument('--affinity', default='Standard')
    ap.add_argument('--two-handed', action='store_true')
    ap.add_argument('--left', action='store_true', help='--weapon as an off-hand L1 chain')
    ap.add_argument('--stats', default='', help='str=40,dex=40,... (default the combo reference)')
    ap.add_argument('--source', default=None, help="innate, a grease name, 'Chilling Mist', or an on-attack SpEffect id")
    ap.add_argument('--rl', type=int, default=150)
    ap.add_argument('--window', type=int, default=10)
    ap.add_argument('--mirror', type=Path, default=CACHE / 'builds.jsonl')
    ap.add_argument('--sweep', type=Path, default=CACHE / 'grease-sweep-dlc-drawstring-150-200.jsonl')
    ap.add_argument('--delay', type=int, default=0, help='PvP hit-to-reaction delay in frames (combo.md s12)')
    ap.add_argument('--p-iframe', type=float, default=0.0, help='chance the victim is in i-frames at the proc')
    ap.add_argument('--depletion', type=float, default=0.0, help='gauge already depleted at hit 1 (string mode)')
    ap.add_argument('--engagements', type=int, default=None,
                    help='engagements per fight (default: the status model corpus value)')
    ap.add_argument('--jobs', type=int, default=1)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--no-left', action='store_true')
    ap.add_argument('--top', type=int, default=40)
    ap.add_argument('--per-status', type=int, default=8)
    ap.add_argument('--json', default=None, help='also write the full ranking here')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.reactions:
        print_reactions(delay=a.delay)
        return 0
    if a.rank:
        rank(a)
        return 0
    if a.weapon:
        show_weapon(a)
        return 0
    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
