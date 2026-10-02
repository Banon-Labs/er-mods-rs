#!/usr/bin/env python3
"""Cross-hand true combos: a right-hand attack cancelled into the left weapon's off-hand L1.

Write-up: `docs/er-mechanics/combo.md`. Labels as in the other `docs/er-mechanics/*.md`:
`VERIFIED` = regulation value or code read out of the 1.16.2 executable (shift 0 against
`eldenring-deobf.bin`), `TAE` = decoded TimeAct / behavior graph, `COMMUNITY` = Smithbox's decompiled
`c0000.hks` (ER 1.08.1) or WitchyBND's TAE template, `MEASURED` = counted over the corpus or read out
of a clip, `INFERRED` = fits the data, consumer not traced.

How an L1 press leaves a right-hand attack:

    TAE JumpTable (`_ChrActionFlag` 0x1404275e0, jump table 0x140428650), `VERIFIED`:
      9  -> AllowInputLHAttack 0x140430070: SetPossibleInputState(L1 = 2), and L2 = 3 unless
            actionAnimationFlags & 0x7f8
      87 -> AllowInputRHAttack, AllowInputLHAttack, AllowInputDodge, ...   ("Input - Common")
      16 -> SetAllowedCancelToActionState(2) and (3)                     ("Cancel - LH Attack")
      117 -> SetAllowedCancelToActionState(2) through case 115's tail     ("Cancel - L1 Attack")
      4 / 115 (R1) never set action 2.
    HKS (`COMMUNITY`): every right-hand attack state's `onUpdate` calls
      AttackCommonFunction(r1, r2, "W_AttackLeftLight1", "W_AttackLeftHeavy1", ...), and
      GetAttackRequest maps an L1 press to ATTACK_REQUEST_LEFT_HEAVY when the left weapon neither
      pairs (IsEnableDualWielding) nor guards (IsWeaponCanGuard, `WeaponCategoryID` column 2,
      `VERIFIED` bytecode of common_define.hks). ExecAttack then plays W_AttackLeftHeavy1 directly.
      The movement attacks (running, rolling, backstep, crouch) and the R2 End clips send R1 to
      W_AttackRightLightSubStart instead (clips 030080..030091, no hitbox), which moves on to
      W_AttackRightLight2 only at EzState flag 0 or the clip's end.

So the L1 follow-up needs the L1 input and cancel windows of the first clip (9/87 with 16/117),
not the R1 ones, and skips the SubStart clip an R1 re-chain out of a movement attack plays.

Link verdict (per defender case), frames at 30 fps real time from the first attack's first hit:

    start = first frame the follow-up's clip can start (its button's input and cancel overlap)
    gap   = start - first hit + lead-in (R2 #2 release, SubStart) + follow-up first hit frame
    escape = min(roll gate for DamageCount 1, guard) of the reaction the first hit causes
             (er-mechanics-frame-advantage.reaction), + `delay`
    true combo if gap < escape, tie if equal, roll-out-able otherwise; blocked by poise when the
    hit leaves poise intact and so plays the level-0 flinch (no hitstun at all).

Poise uses the ranking's corpus (`er-builds-pvp.pvp_corpus`): PvP poise damage =
`poise_damage * 10 * FinalDamageRateParam.saRate` (menu units), broken when it reaches the
victim's menu poise; `stagger` = share of the RL window's PvP builds it breaks.

Hitstop and knockback, both `INFERRED` in effect: `AtkParam.hitStopTime` (s) is reported as a
frame count and `gap_hitstop` adds it to the gap (the case where only the attacker's clock stops);
`AtkParam.knockbackDist` (m) is taken as the victim's push on a stagger (the small, middle and large
clips carry no root motion, measured here). With `--reach` the pushed victim must still be inside
the follow-up's contact distance (`er-mechanics-reach` pose decoder).

Usage:

    python3 scripts/er-mechanics-combo.py --pair Halberd "Battle Axe"
    python3 scripts/er-mechanics-combo.py --pair Halberd "Battle Axe" --reach --json
    python3 scripts/er-mechanics-combo.py --sweep --top 25
    python3 scripts/er-mechanics-combo.py --selftest
"""
import argparse
import importlib.util
import json
import os
import struct
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FA = _load('er_mechanics_frame_advantage', 'er-mechanics-frame-advantage.py')
ATK = FA.ATTACKS
PR = ATK.PR
FPS = ATK.TAE_FPS

_PSG = None
_REACH = None
_PVP = None


def psg():
    """`er-mechanics-powerstance-guard` (pairing rule, off-hand slots, guard table)."""
    global _PSG
    if _PSG is None:
        _PSG = _load('er_mechanics_powerstance_guard', 'er-mechanics-powerstance-guard.py')
    return _PSG


def reach_module():
    global _REACH
    if _REACH is None:
        _REACH = _load('er_mechanics_reach', 'er-mechanics-reach.py')
    return _REACH


def pvp_module():
    global _PVP
    if _PVP is None:
        _PVP = _load('er_builds_pvp', 'er-builds-pvp.py')
    return _PVP


# ------------------------------------------------------------------------------------ constants

#: L1 (ChrActionType 2) input and cancel JumpTable ids, `VERIFIED` (module docstring).
L1_INPUT_IDS, L1_CANCEL_IDS = (9, 87), (16, 117)
ACT_L1 = 2
ALLOW_INPUT_LH_ATTACK = 0x140430070     # CS::CSChrTaeAnimEvent::AllowInputLHAttack
SET_POSSIBLE_INPUT = 0x140407b80        # CSChrActionRequestModule::SetPossibleInputState
#: `_ChrActionFlag` case 115's shared tail: `mov r8b, 1; mov rcx, rbx; call SetAllowedCancel...`.
CANCEL_TAIL_115 = 0x1404277dc

#: Off-hand chain (`TAE` + behavior graph): W_AttackLeftHeavy1 plays 035000 for a one-handed left
#: weapon (its other two clips 032010/032020 are two-handed ones, `INFERRED` selector), and each
#: `AttackLeftHeavy<n>_onUpdate` sends L1 to <n+1> and R1 to W_AttackRightLight1 (`COMMUNITY`).
#: (key, label, judge, anim), the judges `er-mechanics-powerstance-guard.OFFHAND_SLOTS` uses.
OFFHAND = [(k, lab, j, anim) for k, lab, j, anim, _ in [
    ('left_1', 'off-hand L1 #1', 400, 35000, None), ('left_2', 'off-hand L1 #2', 410, 35010, None),
    ('left_3', 'off-hand L1 #3', 420, 35020, None), ('left_4', 'off-hand L1 #4', 430, 35030, None),
    ('left_5', 'off-hand L1 #5', 440, 35040, None), ('left_6', 'off-hand L1 #6', 450, 35050, None)]]
#: The left hand falls back to a048 when neither its spAtkcategory nor wepmotionCategory TAE binds
#: the animation (`FUN_1403f1d40`, `VERIFIED` in er-mechanics-attacks).
LEFT_HAND_FALLBACK_CATEGORY = 48

#: Right-hand states whose R1 goes through W_AttackRightLightSubStart (`COMMUNITY` onUpdate
#: bodies, lines 7692-7793 of the Smithbox decompile): the movement attacks and the R2s. R1 #n
#: goes to R1 #n+1 directly, and so does the jump R1 (Jump_LandAttack_Normal_onUpdate).
SUBSTART_OPENERS = ('run_r1', 'run_r2', 'roll_r1', 'bstep_r1', 'crouch_r1', 'r2_1', 'r2_1c',
                    'r2_2', 'r2_2c', 'counter')
SUBSTART_ANIMS = (30080, 30081, 30090, 30091)
EZSTATE_FLAG_NEXT = 0

#: Left-hand categories (`wepmotionCategory`) whose L1 is not an attack: staff 41 (`ATTACK_REQUEST_INVALID`), the
#: bows/crossbows/ballista 44/45/46/51/52 (their own shots), and the one-handed guard categories
#: (torch 21 and shields 47/48/49/57, `er-mechanics-powerstance-guard.GUARD_LEFT_ONE_HAND`).
LEFT_NO_ATTACK = {41: 'staff/seal', 44: 'bow', 45: 'greatbow', 46: 'crossbow', 51: 'light bow',
                  52: 'ballista'}
MELEE_CATEGORIES = frozenset(range(20, 44)) - {21, 41} | {50, 53, 55, 56, 58, 60, 61, 62}

#: Opener slots (right hand, one-handed) and the same-weapon follow-ups the HKS plays directly.
OPENERS = ('r1_1', 'r1_2', 'r1_3', 'r1_4', 'r1_5', 'r2_1', 'r2_2', 'r2_1c', 'run_r1', 'run_r2',
           'roll_r1', 'bstep_r1', 'crouch_r1', 'jump_r1', 'jump_r2')

#: Menu poise = internal x 10 (docs/er-mechanics/attacks.md section 2).
POISE_MENU = 10.0
#: Stats the damage proxy of `--sweep` assumes (`INFERRED`, a reference line, not a build).
REFERENCE_STATS = {'str': 40, 'dex': 40, 'int': 10, 'fth': 10, 'arc': 10}
CACHE = Path.home() / '.cache/er-build-planner'


# ------------------------------------------------------------------------------------ frames


def _events(category, anim):
    return ATK.resolve_events(category, anim)


def l1_start(w, category, anim):
    """(real frame, clip seconds) the L1 (off-hand) can start out of this clip, or (None, None).

    Input 9/87 overlapping cancel 16/117 (`_first_open`), type-300 early windows for 16/117
    included at the weapon's `weaponWeightRate` (0.0 on every row)."""
    _, _, events = _events(category, anim)
    if events is None:
        return None, None
    win = {'input': [], 'cancel': [], 'unresolved_early': 0}
    for e in events:
        if e.type == ATK.TAE_JUMP_TABLE:
            jid = struct.unpack_from('<i', e.params, 0)[0]
            if struct.unpack_from('<H', e.params, ATK.JUMP_TABLE_STATE_GATE_OFFSET)[0]:
                continue
            if jid in L1_INPUT_IDS:
                win['input'].append((e.start, e.end))
            if jid in L1_CANCEL_IDS:
                win['cancel'].append((e.start, e.end))
        elif e.type == ATK.TAE_JUMP_TABLE_EARLY:
            jid, early_type = struct.unpack_from('<hh', e.params, 0)
            if jid in L1_CANCEL_IDS and early_type in (ATK.EARLY_DEFAULT,
                                                       ATK.EARLY_WEAPON_WEIGHT_RATE):
                x = ATK._early_interval(e, w['weaponWeightRate'] if early_type else 0.0)
                if x:
                    win['cancel'].append(x)
    t = ATK._first_open(win, True)
    if t is None:
        return None, None
    return ATK.real_frame(ATK.clip_to_real(events)(t)), t


def _clip_ref(row):
    """(category, anim) of an attacks-module row's TAE entry."""
    cat, anim = row['tae_entry'][1:].split('_')
    return int(cat), int(anim)


def substart_lead(w):
    """Frames the right hand's SubStart clip plays before W_AttackRightLight2 (EzState flag 0, else
    the clip's end), the shortest of its four clips (which one plays is picked by SpEffects
    135-138 from the first clip's TAE, not traced here; the shortest is the optimistic bound)."""
    best = None
    for anim in SUBSTART_ANIMS:
        cat = ATK.motion_category(w, anim, right_hand_fallback=False)
        _, _, events = _events(cat, anim)
        if events is None:
            continue
        flags = FA.ezstate_windows(events)
        opens = [s for s, e in flags.get(EZSTATE_FLAG_NEXT, []) if e > s]
        if opens:
            f = min(opens)
        else:
            clip = ATK.clip_length(cat, anim)
            if not clip:
                continue
            f = ATK.real_frame(clip[0])
        best = f if best is None or f < best else best
    return best


def left_category(w, anim):
    sp = w.get('spAtkcategory') or 0
    if sp and ATK._bound(sp, anim):
        return sp
    cat = w['wepmotionCategory']
    if ATK._bound(cat, anim):
        return cat
    if ATK._bound(LEFT_HAND_FALLBACK_CATEGORY, anim):
        return LEFT_HAND_FALLBACK_CATEGORY
    return cat


def offhand_attacks(reg, weapon_id, level=0):
    """Off-hand L1 rows of `weapon_id` in the left hand, in the attacks module's row shape
    (real-time `hit_windows`, `cancel_frame` for r1/r2/dodge/guard/move) plus `l1_start`."""
    w = reg.weapon[weapon_id]
    out = []
    for key, label, judge, anim in OFFHAND:
        nums = ATK.attack_numbers(reg, weapon_id, judge, level)
        if nums is None:
            continue
        cat = left_category(w, anim)
        tae = ATK.tae_details(reg, weapon_id, anim, judge, cat)
        if tae is None or not tae['hit_windows']:
            continue
        src_cat, src_anim = tae['source']
        row = {'slot': key, 'label': label, 'anim': f'a{src_cat:03d}_{src_anim:06d}',
               'tae_entry': f'a{cat:03d}_{anim:06d}', **nums, **tae}
        row['l1_start'], _ = l1_start(w, cat, anim)
        out.append(row)
    return out


def left_mode(reg, right, left):
    """What L1 does with `left` in the left hand and `right` one-handed in the right
    (`GetAttackRequest`, `COMMUNITY` + `VERIFIED` guard table): 'offhand', 'dual', 'guard' or a
    label for a left weapon whose L1 is not a melee attack."""
    lk = reg.weapon[left]['wepmotionCategory']
    if lk in LEFT_NO_ATTACK:
        return LEFT_NO_ATTACK[lk]
    if lk in psg().GUARD_LEFT_ONE_HAND:
        return 'guard'
    if psg().can_powerstance(reg, right, left):
        return 'dual'
    return 'offhand'


# ------------------------------------------------------------------------------------ links


class Model:
    """Regulation, frame-advantage tables, the RL window's poise corpus and a damage proxy."""

    def __init__(self, regulation=None, rl=150, window=10, mirror=CACHE / 'builds.jsonl',
                 victim_poise=None, delay=0):
        self.reg = ATK.Regulation(regulation)
        self.fa = FA.Tables(self.reg, regulation)
        files = PR.load(regulation)
        rows, _, _ = PR.rows(PR.param_bytes(files, 'AtkParam_Pc'), ['knockbackDist', 'hitStopTime'])
        self.push = {r['id']: (r['knockbackDist'], r['hitStopTime']) for r in rows}
        self.delay = delay
        self.poises = []
        if mirror is not None and Path(mirror).exists():
            P = pvp_module()
            self.poises = P.Defenders(P.pvp_corpus(Path(mirror), rl - window, rl + window)).poise
        srt = sorted(self.poises)
        self.victim_poise = victim_poise if victim_poise is not None else (
            srt[len(srt) // 2] if srt else 51.0)
        self._react = {}
        self._rows = {}
        self._off = {}
        self._sub = {}
        self._cover, self._rc = {}, None

    def react(self, level):
        if level not in self._react:
            self._react[level] = FA.reaction(level)
        return self._react[level]

    def rows(self, wid):
        if wid not in self._rows:
            rows = {r['slot']: r for r in ATK.weapon_attacks(self.reg, wid, 'one')
                    if r.get('hit_windows')}
            w = self.reg.weapon[wid]
            for r in rows.values():
                cat, anim = _clip_ref(r)
                r['l1_start'], r['l1_start_clip'] = l1_start(w, cat, anim)
            self._rows[wid] = rows
        return self._rows[wid]

    def offhand(self, wid):
        if wid not in self._off:
            self._off[wid] = {r['slot']: r for r in offhand_attacks(self.reg, wid)}
        return self._off[wid]

    def offhand_coverage(self, wid, slot='left_1'):
        """`er-mechanics-reach.coverage_factor` of the off-hand slot, read from its own left-hand
        TimeAct entry (`attack_reach` with `clip`). Unmeasured, the class median of the weapon's
        one-handed R1 #1 stands in (`class_fallback`), as for a right-hand slot. None without
        the row."""
        key = (wid, slot)
        if key not in self._cover:
            row = self.offhand(wid).get(slot)
            cover = None
            if row is not None:
                R = reach_module()
                if self._rc is None:
                    self._rc = R.Reach()
                cat, anim = _clip_ref(row)
                r = R.attack_reach(self._rc, wid, row['slot'], row['label'], row['judge'], anim, 'one',
                                   clip=(cat, anim))
                cover = r and r.get('coverage_factor')
                if cover is None:
                    cover = R.class_fallback(wid, 'one', 'r1_1').get('coverage_factor')
            self._cover[key] = cover
        return self._cover[key]

    def substart(self, wid):
        if wid not in self._sub:
            self._sub[wid] = substart_lead(self.reg.weapon[wid])
        return self._sub[wid]

    def pvp_poise(self, row):
        """Menu poise damage between players (poise x 10 x saRate)."""
        atk = self.fa.atk.get(row['atk_row']) or {}
        fr = self.fa.final_rate.get(atk.get('finalDamageRateId', -1), {})
        return row['poise_damage'] * POISE_MENU * (fr.get('saRate') or 1.0)

    def stagger(self, row):
        p = self.pvp_poise(row)
        return sum(x < p for x in self.poises) / len(self.poises) if self.poises else None

    def verdict_side(self, first, gap, broken):
        level = FA.reaction_level(self.fa, first['atk_row'], broken)
        react = self.react(level)
        if react is None:
            return {'level': level, 'verdict': None}
        if not react['locks']:
            return {'level': level, 'escape': 0, 'escape_by': 'not locked',
                    'verdict': 'blocked by poise' if not broken else 'no hitstun'}
        esc = {'roll': react['roll'][1], 'guard': react['guard']}
        esc = {k: v + self.delay for k, v in esc.items() if v is not None}
        by = min(esc, key=esc.get)
        e = esc[by]
        v = 'true' if gap < e else ('tie' if gap == e else 'roll-out-able')
        # `window`: frames the defender has to start the escape before the follow-up lands;
        # `delay_needed`: the hit-to-reaction delay (never measured) that would make it true.
        return {'level': level, 'escape': e, 'escape_by': by, 'verdict': v,
                'window': round(max(0.0, gap - e), 1),
                'delay_needed': int((gap - e) // 1) + 1 if gap >= e else 0}

    def link(self, first, second, start, lead=0.0, via='l1'):
        """One link A -> B. `start` = frame B's clip starts, from A's clip start."""
        if start is None or not first.get('hit_windows') or not second.get('hit_windows'):
            return None
        h0 = first['hit_windows'][0][0]
        clamped = False
        if start < h0:
            # The follow-up would cut the first attack off before it hits (`INFERRED`: A must land).
            start, clamped = first['hit_windows'][0][1], True
        b0 = second['hit_windows'][0][0]
        gap = round(start - h0 + lead + b0, 1)
        knock, stop = self.push.get(first['atk_row'], (0.0, 0.0))
        stop_f = round(stop * FPS, 1)
        out = {'first': first['slot'], 'next': second['slot'], 'via': via,
               'start': round(start, 1), 'first_hit': h0, 'lead_in': lead,
               'next_first_hit': b0, 'gap': gap, 'gap_hitstop': round(gap + stop_f, 1),
               'hitstop_frames': stop_f, 'knockback_m': round(knock, 3),
               'clamped_to_hit': clamped,
               'on_break': self.verdict_side(first, gap, True),
               'on_intact': self.verdict_side(first, gap, False),
               'pvp_poise': round(self.pvp_poise(first), 1), 'stagger': self.stagger(first)}
        b = out['on_break']
        out['on_break']['robust'] = (b.get('escape') is not None and b['verdict'] == 'true'
                                     and out['gap_hitstop'] < b['escape'])
        out['breaks_victim'] = out['pvp_poise'] >= self.victim_poise
        out['verdict'] = (out['on_break']['verdict'] if out['breaks_victim']
                          else out['on_intact']['verdict'])
        return out

    def cross_links(self, right, left):
        """Right-hand openers -> off-hand L1 #1, and off-hand L1 #n -> right R1 #1 / L1 #n+1."""
        rr, lr = self.rows(right), self.offhand(left)
        out = []
        l1 = lr.get('left_1')
        if l1 is None:
            return out
        for key in OPENERS:
            a = rr.get(key)
            if a is not None:
                lk = self.link(a, l1, a['l1_start'], 0.0, 'l1 (16/117)')
                if lk:
                    out.append(lk)
        r1 = rr.get('r1_1')
        for i, (key, _, _, _) in enumerate(OFFHAND):
            a = lr.get(key)
            if a is None:
                continue
            if r1 is not None:
                lk = self.link(a, r1, a['cancel_frame'].get('r1'), 0.0, 'r1 (4/115)')
                if lk:
                    out.append(lk)
            nxt = lr.get(OFFHAND[i + 1][0]) if i + 1 < len(OFFHAND) else None
            if nxt is not None:
                lk = self.link(a, nxt, a.get('l1_start'), 0.0, 'l1 (16/117)')
                if lk:
                    out.append(lk)
        return out

    def same_links(self, wid):
        """Same-weapon R1 follow-ups the HKS plays: R1 #n -> #n+1 and jump R1 -> R1 #2 direct,
        the movement attacks and R2s -> R1 #2 through the SubStart clip, R2 #1 -> #2 with its
        release lead-in."""
        rr = self.rows(wid)
        out = []
        sub = self.substart(wid)
        for key in OPENERS:
            a = rr.get(key)
            if a is None:
                continue
            ready = a['cancel_frame'].get('r1')
            if key.startswith('r1_'):
                b = rr.get(f'r1_{int(key[3:]) + 1}')
                lk = b and self.link(a, b, ready, 0.0, 'r1 (4/115)')
            elif key == 'jump_r1':
                b = rr.get('r1_2')
                lk = b and self.link(a, b, ready, 0.0, 'r1 (4/115)')
            elif key in SUBSTART_OPENERS:
                b = rr.get('r1_2')
                lk = b and sub is not None and self.link(a, b, ready, float(sub), 'r1 via SubStart')
            else:
                lk = None
            if lk:
                out.append(lk)
            if key == 'r2_1' and rr.get('r2_2') and rr['r2_2'].get('release_lead_in') is not None:
                lk = self.link(a, rr['r2_2'], a['cancel_frame'].get('r2'),
                               rr['r2_2']['release_lead_in'], 'r2 (release lead-in)')
                if lk:
                    out.append(lk)
        return out


# ------------------------------------------------------------------------------------ ranking


#: Verdicts in the frame-advantage vocabulary `er-mechanics-status.combo_land` reads. A
#: roll-out-able link keeps its own name and carries its landing chance as `p` (`link_p`).
TO_FA_VERDICT = {'true': 'true', 'tie': 'tie', 'roll-out-able': 'roll-out-able'}
#: Landing chance of a `true` and a `tie` link (`er-mechanics-status.COMBO_LAND`: a tie is even
#: odds, `INFERRED`).
VERDICT_P = {'true': 1.0, 'tie': 0.5}
_STATUS = None
_ASHES = None


def roll_out_p(lead_in, next_first_hit):
    """Chance a defender does not escape a roll-out-able link: he starts the roll or guard only
    once he sees the follow-up (its clip start), and it is live on the attacker's machine his
    reaction plus two network legs later (`er-mechanics-ashes.reaction_delays`, `INFERRED` model).

    The gate opens before the follow-up lands (that is what roll-out-able means), so he escapes
    exactly when that delay is shorter than the follow-up's own startup, `lead_in` +
    `next_first_hit`, wherever the gate sits. A delay equal to it counts as a tie (0.5)."""
    global _ASHES
    if _ASHES is None:
        _ASHES = _load('er_mechanics_ashes', 'er-mechanics-ashes.py')
    need = (lead_in or 0.0) + next_first_hit
    return sum(w * (1.0 if d > need else 0.5 if d == need else 0.0) for d, w in _ASHES.reaction_delays())


def link_p(side):
    """Landing chance of one side of a link: `p` when it carries one, else by verdict."""
    if not side:
        return 0.0
    if side.get('p') is not None:
        return side['p']
    return VERDICT_P.get(side.get('verdict'), 0.0)


def link_land(combo, stagger=0.0):
    """`er-mechanics-status.combo_land` with a side's own `p` read first: `on_intact` when poise
    holds, `on_break` when it breaks, weighted by the stagger share of the hit before it."""
    if not combo:
        return 0.0
    return (1.0 - stagger) * link_p(combo.get('on_intact')) + stagger * link_p(combo.get('on_break'))


def _land(combo, stagger):
    return link_land(combo, stagger)


def paired_slots(model, right, left, slots, hit_fn, catch=None):
    """`slots` (er-builds-pvp's {slot key: slot dict}, one-handed) with the off-hand L1 #1 of
    `left` added as slot `left_1` and, on every opener whose cross-hand link lands more often
    than its current first follow-up, that link put first in `combos` (with `start`, the frame
    the L1 clip starts, for `er-mechanics-moveset.engagement`). Each side carries its landing
    chance `p`: 1 true, 0.5 tie, `roll_out_p` roll-out-able, 0 otherwise. `hit_fn(row)` scores the
    off-hand row like a slot (`er-builds-pvp.slot_hit`). Returns `slots` itself when the pair
    has no off-hand L1 (paired, guarding or ranged left hand). The input dicts are not changed."""
    if left_mode(model.reg, right, left) != 'offhand':
        return slots
    off = model.offhand(left).get('left_1')
    if off is None:
        return slots
    hit = hit_fn(off)
    adv = {}
    for key, broken in (('adv', False), ('adv_stagger', True)):
        react = model.react(FA.reaction_level(model.fa, off['atk_row'], broken))
        a = FA.advantage(off, react) if react else None
        adv[key] = a['advantage'] if a else None
    out = dict(slots)
    out['left_1'] = {'label': f"off-hand L1 ({model.reg.weapon_names.get(left)})", 'anim': off['anim'],
                     'mv': off['mv_phys'], **hit, **adv, 'coverage': model.offhand_coverage(left),
                     'combos': []}
    for lk in model.cross_links(right, left):
        s = slots.get(lk['first'])
        if lk['next'] != 'left_1' or s is None:
            continue
        lead = s.get('release_lead_in') or 0.0
        # `p_roll`: the landing chance if the link turns roll-out-able (the jump openers re-time
        # the gap to the landing, `er-mechanics-jump.jump_slots`); it does not depend on the gap.
        p_roll = roll_out_p(lk['lead_in'], lk['next_first_hit'])
        sides = {}
        start = lk['start'] + lead
        for side in ('on_break', 'on_intact'):
            v = TO_FA_VERDICT.get(lk[side]['verdict'], 'no')
            p = p_roll if v == 'roll-out-able' else VERDICT_P.get(v, 0.0)
            sides[side] = {'gap': lk['gap'], 'escape': lk[side].get('escape'), 'verdict': v,
                           'p': p, 'p_roll': p_roll}
            if catch and v in ('roll-out-able', 'tie') and lk[side].get('escape'):
                # Roll-catch (combo.md section 10b): a defender who rolls out of the stagger is
                # caught by L1 #2 with `catch['p']`; that hit starts one L1 cycle later.
                extra = (1.0 - p) * catch['p']
                sides[side].update(p=p + extra, p_catch=catch['p'],
                                   start=(p * start + extra * (start + catch['next'])) / (p + extra)
                                   if p + extra else start)
        if catch and sides['on_break'].get('start') is not None:
            start = sides['on_break']['start']
        entry = {'next': 'left_1', 'via': 'l1', 'start': round(start, 1),
                 'first_hit': lk['first_hit'], 'lead_in': lk['lead_in'],
                 'next_first_hit': lk['next_first_hit'], **sides}
        stag = s.get('stagger') or 0.0
        old = (s.get('combos') or [None])[0]
        if _land(entry, stag) > (_land(old, stag) if old else 0.0):
            out[lk['first']] = {**s, 'combos': [entry] + list(s.get('combos') or [])}
    return out


# ------------------------------------------------------------------------------------ reach


def reach_check(model, right, left, lk, rc=None):
    """Whether the pushed victim is still inside the follow-up's contact distance.

    Distances in metres along the attacker's facing, from the first clip's start position:
    victim centre at the first hit = the first attack's `contact_centre_m` (its farthest hit, the
    worst case) or its lunge + two idle front radii (body to body); pushed by `knockbackDist`;
    the attacker has moved by the first clip's root motion up to the follow-up's start, and the
    follow-up then reaches its own `contact_centre_m` from there. `INFERRED` geometry: straight
    line, no tracking, no pushback from body collision."""
    R = reach_module()
    rc = rc or R.Reach()
    pose = R._pose_module()
    if pose is None:
        return {'error': 'pose decoder absent'}
    a_hand = left if lk['first'].startswith('left') else right
    b_hand = left if lk['next'].startswith('left') else right
    a =(model.rows(right).get(lk['first']) if not lk['first'].startswith('left')
         else model.offhand(left).get(lk['first']))
    b = (model.rows(right).get(lk['next']) if not lk['next'].startswith('left')
         else model.offhand(left).get(lk['next']))

    def contact(wid, row):
        cat, anim = _clip_ref(row)
        return R.attack_reach(rc, wid, row['slot'], row['label'], row['judge'], anim, 'one',
                              clip=(cat, anim))
    ra = contact(a_hand, a)
    rb = contact(b_hand, b)
    if not ra or not rb or ra.get('contact_centre_m') is None or rb.get('contact_centre_m') is None:
        return {'error': 'no contact measurement',
                'first': ra and ra.get('world_reach_error'), 'next': rb and rb.get('world_reach_error')}
    front = R.defender_hurtbox('idle') or {'front_m': 0.3}
    cat, anim = _clip_ref(a)
    hc, ha = R.hkx_source(cat, anim)
    # Start frame (real) back to clip seconds: the clip-time value `l1_start` returned, else
    # the frame / 30 (no play-speed window between, `INFERRED`).
    t = (a.get('l1_start_clip') if lk['via'].startswith('l1') and a.get('l1_start_clip')
         else lk['start'] / FPS)
    moved = -pose.root_motion(hc, ha, t)[2]
    tip = ra['contact_centre_m']
    body = (ra.get('root_motion_to_hit_m') or 0.0) + 2 * front['front_m']
    push = lk['knockback_m']
    out = {'first_contact_m': tip, 'next_contact_m': rb['contact_centre_m'],
           'attacker_moved_m': round(moved, 3), 'push_m': push}
    for name, d0 in (('tip', tip), ('body', body)):
        need = d0 + push - moved
        out[name] = {'victim_at_m': round(d0, 3), 'need_m': round(need, 3),
                     'reaches': need <= rb['contact_centre_m']}
    return out


# ------------------------------------------------------------------------------------ sweep


def base_weapons(reg):
    """Named base (affinity 0) melee weapons, one per name."""
    seen, out = set(), []
    for wid in sorted(reg.weapon):
        name = reg.weapon_names.get(wid)
        if wid % 10000 or not name or name.startswith('[') or name in seen:
            continue
        if reg.weapon[wid]['wepmotionCategory'] not in MELEE_CATEGORIES:
            continue
        seen.add(name)
        out.append(wid)
    return out


_AR_BY = {}


def damage_proxy(reg, wid, row, stats=None):
    """AR at max upgrade, Standard, `REFERENCE_STATS`, times the row's motion values / 100: a
    comparable size for a follow-up, not a corpus damage (`INFERRED` proxy)."""
    ar = _ar()
    if wid not in _AR_BY:
        level = ar.tables.max_level(reg.weapon[wid]['reinforceTypeId'])
        r = ar.mod.attack_rating(ar.tables, wid, 'Standard', level, stats or REFERENCE_STATS, False)
        _AR_BY[wid] = {el: r['damage'].get(el, {}).get('total', 0.0)
                       for el in ('physical', 'magic', 'fire', 'lightning', 'holy')}
    by = _AR_BY[wid]
    return sum(by[el] * row.get(key, 0) / 100.0 for el, key in (
        ('physical', 'mv_phys'), ('magic', 'mv_mag'), ('fire', 'mv_fire'),
        ('lightning', 'mv_light'), ('holy', 'mv_holy')))


class _ArCache:
    def __init__(self):
        self.mod = _load('er_mechanics_ar', 'er-mechanics-ar.py')
        self.tables = self.mod.Tables(None)


_AR = None


def _ar():
    global _AR
    if _AR is None:
        _AR = _ArCache()
    return _AR


def pair_value(model, right, left, links, robust=False):
    """Best guaranteed follow-up of a pair: max over its true links of stagger x proxy damage.
    `robust` keeps only links that stay true with the first hit's hitstop added to the gap."""
    best = None
    for lk in links:
        if lk['on_break']['verdict'] != 'true' or not lk['stagger']:
            continue
        if robust and not lk['on_break']['robust']:
            continue
        hand = left if lk['next'].startswith('left') else right
        row = (model.offhand(left) if hand == left and lk['next'].startswith('left')
               else model.rows(right)).get(lk['next'])
        if row is None:
            continue
        dmg = damage_proxy(model.reg, hand, row)
        value = lk['stagger'] * dmg
        if best is None or value > best['value']:
            best = {'value': round(value, 1), 'damage': round(dmg, 1), 'link': lk}
    return best


def sweep(model, top=25, reach=False):
    reg = model.reg
    weapons = base_weapons(reg)
    stats = {'right': 0, 'pairs': 0, 'links': 0, 'true': 0, 'tie': 0, 'true_robust': 0,
             'true_by_first': {}, 'true_same': 0, 'same_links': 0}
    offhand_ok = [w for w in weapons if reg.weapon[w]['wepmotionCategory'] not in LEFT_NO_ATTACK
                  and reg.weapon[w]['wepmotionCategory'] not in psg().GUARD_LEFT_ONE_HAND
                  and model.offhand(w).get('left_1')]
    results = []
    for right in weapons:
        rows = model.rows(right)
        if not rows:
            continue
        stats['right'] += 1
        for lk in model.same_links(right):
            stats['same_links'] += 1
            stats['true_same'] += lk['on_break']['verdict'] == 'true'
        for left in offhand_ok:
            if left_mode(reg, right, left) != 'offhand':
                continue
            links = model.cross_links(right, left)
            stats['pairs'] += 1
            if any(lk['on_break']['verdict'] == 'true' and lk['next'] == 'left_1' for lk in links):
                name = reg.weapon_names[left]
                stats.setdefault('rights_with_true_l1', {})[name] = \
                    stats.get('rights_with_true_l1', {}).get(name, 0) + 1
            for lk in links:
                stats['links'] += 1
                v = lk['on_break']['verdict']
                if v == 'true':
                    stats['true'] += 1
                    stats['true_robust'] += lk['on_break']['robust']
                    k = lk['first'] + '->' + lk['next'].split('_')[0]
                    stats['true_by_first'][k] = stats['true_by_first'].get(k, 0) + 1
                elif v == 'tie':
                    stats['tie'] += 1
            for robust in (False, True):
                best = pair_value(model, right, left, links, robust)
                if best:
                    results.append({'right': reg.weapon_names[right], 'left': reg.weapon_names[left],
                                    'right_id': right, 'left_id': left, 'robust': robust,
                                    'family': moveset_family(reg, right), **best})
    results.sort(key=lambda r: -r['value'])
    out = {}
    for robust in (False, True):
        # Weapons that share a clip give the same frames: keep the first of each (opener clip,
        # left weapon) so the list shows distinct setups.
        seen, keep = set(), []
        for r in results:
            first = r['link']['first']
            src = (model.offhand(r['left_id']) if first.startswith('left')
                   else model.rows(r['right_id'])).get(first, {}).get('anim')
            key = (src, r['left_id'] if r['link']['next'].startswith('left') else r['right_id'])
            if r['robust'] != robust or key in seen:
                continue
            seen.add(key)
            keep.append(r)
            if len(keep) >= top:
                break
        out['robust' if robust else 'true'] = keep
    if reach:
        rc = reach_module().Reach()
        for keep in out.values():
            for r in keep:
                r['reach'] = reach_check(model, r['right_id'], r['left_id'], r['link'], rc)
    return stats, out


def moveset_family(reg, wid):
    """(wepmotionCategory, spAtkcategory): weapons with the same pair play the same clips."""
    w = reg.weapon[wid]
    return (w['wepmotionCategory'], w.get('spAtkcategory') or 0)


# ------------------------------------------------------------------------------------ output


def _fmt_link(lk):
    b, i = lk['on_break'], lk['on_intact']
    return (f"{lk['first']:>9} -> {lk['next']:<7} via {lk['via']:<20} start {lk['start']:>5} "
            f"gap {lk['gap']:>5} (+hitstop {lk['gap_hitstop']:>5}) | break lv{b['level']} "
            f"esc {b.get('escape', '-')!s:>3} {b['verdict']:<14} | intact lv{i['level']} "
            f"{i['verdict']:<16} | poise {lk['pvp_poise']:>5} stag "
            f"{'-' if lk['stagger'] is None else round(100 * lk['stagger'])}%")


def print_pair(model, right, left, reach=False):
    reg = model.reg
    mode = left_mode(reg, right, left)
    print(f"{reg.weapon_names[right]} ({right}) right, {reg.weapon_names[left]} ({left}) left: L1 = {mode}")
    print(f"victim menu poise {model.victim_poise} (corpus median unless --victim-poise), delay {model.delay}")
    rr = model.rows(right)
    print('\nright-hand clips: first hit, R1 cancel, L1 cancel (16/117), roll')
    for k in OPENERS:
        r = rr.get(k)
        if r:
            print(f"  {k:<9} {r['anim']}  hit {r['hit_windows'][0]}  r1 {r['cancel_frame'].get('r1')}"
                  f"  l1 {r['l1_start']}  roll {r['cancel_frame'].get('dodge')}")
    print(f"  SubStart lead-in before R1 #2 (shortest clip): {model.substart(right)}")
    if mode == 'offhand':
        for r in model.offhand(left).values():
            print(f"  {r['slot']:<9} {r['anim']}  hit {r['hit_windows'][0]}  r1 {r['cancel_frame'].get('r1')}"
                  f"  l1 {r['l1_start']}  roll {r['cancel_frame'].get('dodge')}")
    print('\nsame weapon:')
    for lk in model.same_links(right):
        print(' ', _fmt_link(lk))
    if mode == 'offhand':
        print('\ncross hand:')
        rc = reach_module().Reach() if reach else None
        for lk in model.cross_links(right, left):
            print(' ', _fmt_link(lk))
            if reach and lk['first'] in ('run_r1', 'r1_1', 'roll_r1', 'r2_1'):
                print('     reach', json.dumps(reach_check(model, right, left, lk, rc)))


# ------------------------------------------------------------------------------------ selftest


def selftest():
    failures, passes, skips = [], [], []

    def check(name, got, want, source):
        (passes if got == want else failures).append(f'{name}: got {got!r} want {want!r} [{source}]')

    # 1. The EXE: which JumpTable cases open L1 (ChrActionType 2).
    path = ATK.DEOBF_1162
    if os.path.exists(path):
        src = 'EXE eldenring-deobf.bin 1.16.2'
        calls = ATK._jump_table_case_calls(path)
        check('JumpTable 16 sets allowed cancel 2 and 3',
              {(ATK.SET_ALLOWED_CANCEL, 2), (ATK.SET_ALLOWED_CANCEL, 3)} <= calls[16], True, src)
        check('JumpTable 4 and 115 never set allowed cancel 2',
              any(e == ACT_L1 for c in (4, 115) for _, e in calls[c]), False, src)
        table = struct.unpack(f'<{ATK.JT_COUNT}I', ATK._image_read(path, ATK.JT_TABLE, 4 * ATK.JT_COUNT))
        case = {j: ATK.IMAGE_BASE + table[j - 1] for j in (9, 87, 117)}
        body9 = ATK._image_read(path, case[9], 8)
        check('JumpTable 9 calls AllowInputLHAttack',
              case[9] + 8 + struct.unpack_from('<i', body9, 4)[0], ALLOW_INPUT_LH_ATTACK, src)
        body87 = ATK._image_read(path, case[87], 16)
        check('JumpTable 87 calls AllowInputLHAttack second',
              case[87] + 16 + struct.unpack_from('<i', body87, 12)[0], ALLOW_INPUT_LH_ATTACK, src)
        b117 = ATK._image_read(path, case[117], 0x25)
        # ... test rbx; je; mov edx, 2 (ba 02 00 00 00); jmp rel32 -> case 115's tail
        check('JumpTable 117 passes edx 2 into case 115 tail', (
            b117[0x1b:0x20].hex(), case[117] + 0x25 + struct.unpack_from('<i', b117, 0x21)[0]),
            ('ba02000000', CANCEL_TAIL_115), src)
        tail = ATK._image_read(path, CANCEL_TAIL_115, 11)
        check('case 115 tail calls SetAllowedCancelToActionState',
              CANCEL_TAIL_115 + 11 + struct.unpack_from('<i', tail, 7)[0], ATK.SET_ALLOWED_CANCEL, src)
        lh = ATK._image_read(path, ALLOW_INPUT_LH_ATTACK, 0x60)
        check('AllowInputLHAttack: mov edx, 2 then call SetPossibleInputState',
              (lh[0x27:0x2c].hex(), ALLOW_INPUT_LH_ATTACK + 0x34
               + struct.unpack_from('<i', lh, 0x30)[0]), ('ba02000000', SET_POSSIBLE_INPUT), src)
    else:
        skips.append('EXE: ' + path + ' absent')

    # 2. The community HKS decompile: the L1 route out of right-hand attacks.
    hks = psg().HKS_DECOMPILE
    if os.path.exists(hks):
        text = open(hks, encoding='utf-8', errors='replace').read()
        src = 'COMMUNITY Smithbox c0000.hks'

        def body(fn):
            i = text.find(f'function {fn}()')
            return text[i:text.find('\nend', i)] if i >= 0 else ''
        dash = body('AttackRightLightDash_onUpdate')
        check('running R1: R1 -> SubStart, L1 -> W_AttackLeftHeavy1',
              ('"W_AttackRightLightSubStart"' in dash, '"W_AttackLeftHeavy1"' in dash), (True, True), src)
        check('off-hand L1 #1: R1 -> W_AttackRightLight1', '"W_AttackRightLight1"'
              in body('AttackLeftHeavy1_onUpdate'), True, src)
        req = text[text.find('local isEnableDualWielding = IsEnableDualWielding()'):][:600]
        check('GetAttackRequest: L1 -> LEFT_HEAVY unless paired or IsWeaponCanGuard',
              ('ATTACK_REQUEST_DUAL_RIGHT' in req, 'IsWeaponCanGuard' in req,
               'ATTACK_REQUEST_LEFT_HEAVY' in req), (True, True, True), src)
        sub = body('AttackRightLightSubStart_onUpdate')
        check('SubStart moves to R1 #2 on EzState flag 0 or anim end',
              ('GetEventEzStateFlag, 0' in sub, '"W_AttackRightLight2"' in sub), (True, True), src)
    else:
        skips.append('HKS decompile absent: ' + hks)

    # 3. Regulation + TAE: the halberd -> axe case, and the arithmetic of a link.
    model = Model(mirror=None, victim_poise=51.0)
    reg = model.reg
    hal, axe = reg.find_weapon('Halberd'), reg.find_weapon('Battle Axe')
    check('Halberd + Battle Axe: L1 is an off-hand attack', left_mode(reg, hal, axe), 'offhand',
          'regulation categories + HKS rule')
    check('Halberd + Halberd: L1 is powerstance', left_mode(reg, hal, hal), 'dual', 'rule')
    check('Halberd + Buckler: L1 guards', left_mode(reg, hal, reg.find_weapon('Buckler')), 'guard',
          'rule')
    run = model.rows(hal).get('run_r1')
    # a038_030200: type-300 early events open cancel 4 on frame 28 and cancel 16 on frame 32;
    # the L1 opens four frames after the R1, it wins by skipping the SubStart clip.
    check('Halberd running R1: first hit, R1 start, L1 start',
          run and (run['hit_windows'][0][0], run['cancel_frame']['r1'], run['l1_start']),
          (15, 28, 32), 'TAE a038_030200')
    l1 = model.offhand(axe).get('left_1')
    check('Battle Axe off-hand L1 #1 resolves (judge 400, a030_035000)',
          (l1 is not None, l1 and l1['anim']), (True, 'a030_035000'), 'TAE + regulation')
    if run and l1:
        lk = model.link(run, l1, run['l1_start'])
        want = round(run['l1_start'] - run['hit_windows'][0][0] + l1['hit_windows'][0][0], 1)
        check('link gap = L1 start - first hit + L1 first hit', lk['gap'], want, 'rule')
        check('Halberd running R1 is dmgLevel 2 (middle): escape 25 on a break',
              (lk['on_break']['level'], lk['on_break']['escape']), (2, 25), 'regulation + TAE')
        check('poise intact: blocked by poise', lk['on_intact']['verdict'], 'blocked by poise', 'rule')
    fake_a = {'slot': 'x', 'atk_row': run['atk_row'], 'hit_windows': [(10, 12)],
              'poise_damage': 1.0}
    fake_b = {'slot': 'y', 'hit_windows': [(5, 6)]}
    check('a follow-up that would start before the first hit is clamped to the hit end',
          model.link(fake_a, fake_b, 4)['start'], 12, 'rule')
    check('gap 24 against middle escape 25 is true, 25 tie, 26 roll-out-able',
          [model.verdict_side(fake_a, g, True)['verdict'] for g in (24, 25, 26)],
          ['true', 'tie', 'roll-out-able'], 'rule')
    # 4. The ranking hook: a colossal sword's one-handed rolling R1 gets the axe's L1 put first.
    gs = reg.find_weapon('Greatsword')
    slots = {'roll_r1': {'dmg': 100.0, 'next': 40, 'roll': 40, 'stagger': 1.0, 'combos': []}}
    got = paired_slots(model, gs, axe, slots, lambda row: {'dmg': 50.0, 'next': 22, 'roll': 24})
    c = (got.get('roll_r1', {}).get('combos') or [{}])[0]
    roll = model.rows(gs)['roll_r1']
    check('paired_slots: Greatsword rolling R1 -> Battle Axe L1 #1 put first, true on break, '
          'starting on the L1 frame', (c.get('next'), c.get('on_break', {}).get('verdict'),
                                       c.get('start'), 'left_1' in got, slots['roll_r1']['combos']),
          ('left_1', 'true', roll['l1_start'], True, []), 'rule + TAE')
    # 5. A roll-out-able link lands when the defender reacts to the follow-up too late: the reaction
    # model's delays run 8.45-13.31 frames, so an L1 hitting 13 frames after its start is escaped
    # by 8 of the 9 reaction quantiles.
    check('roll_out_p: delay > follow-up startup lands, one quantile of 9 for a 13-frame L1',
          (round(roll_out_p(0.0, 13), 4), roll_out_p(0.0, 5), roll_out_p(0.0, 20)),
          (round(1 / 9, 4), 1.0, 0.0), 'rule (INFERRED reaction model)')
    if run and l1:
        hal_slots = {'run_r1': {'dmg': 100.0, 'next': 40, 'roll': 40, 'stagger': 1.0, 'combos': []}}
        hg = paired_slots(model, hal, axe, hal_slots, lambda row: {'dmg': 50.0, 'next': 22, 'roll': 24})
        hc = (hg.get('run_r1', {}).get('combos') or [{}])[0]
        want_p = roll_out_p(0.0, l1['hit_windows'][0][0])
        check('Halberd running R1 -> axe L1 is credited roll-out-able with the reaction chance',
              (hc.get('on_break', {}).get('verdict'), round(link_land(hc, 1.0), 4)),
              ('roll-out-able', round(want_p, 4)), 'rule')
    rows, _, _ = PR.rows(PR.param_bytes(PR.load(None), 'AtkParam_Pc'), ['knockbackDist', 'hitStopTime'])
    check('AtkParam knockbackDist/hitStopTime read at +0x10/+0x14 (paramdef)',
          [f['off'] for f in PR.layout(PR.paramdef('ATK_PARAM_ST', True))[0]
           if f['name'] in ('knockbackDist', 'hitStopTime')], [0x10, 0x14], 'Smithbox paramdef')
    del rows
    pose = reach_module()._pose_module()
    if pose is not None:
        moves = [abs(pose.root_motion(0, a, 1.0)[2]) for a in (5100, 5200, 5300)]
        check('small/middle/large stagger clips carry no root motion (push is knockbackDist)',
              max(moves) < 1e-6, True, 'MEASURED a000 hkx')
    else:
        skips.append('pose decoder absent')

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
    ap.add_argument('--pair', nargs=2, metavar=('RIGHT', 'LEFT'))
    ap.add_argument('--sweep', action='store_true', help='every right x off-hand-capable left pair')
    ap.add_argument('--top', type=int, default=25)
    ap.add_argument('--reach', action='store_true', help='check the pushed victim is in reach (slow)')
    ap.add_argument('--rl', type=int, default=150)
    ap.add_argument('--window', type=int, default=10)
    ap.add_argument('--mirror', type=Path, default=CACHE / 'builds.jsonl')
    ap.add_argument('--victim-poise', type=float, help='menu poise (default: corpus median)')
    ap.add_argument('--delay', type=int, default=0, help='frames from hit to reaction start')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    model = Model(rl=a.rl, window=a.window, mirror=a.mirror, victim_poise=a.victim_poise,
                  delay=a.delay)
    if a.pair:
        right, left = (model.reg.find_weapon(n) for n in a.pair)
        if a.json:
            out = {'right': right, 'left': left, 'mode': left_mode(model.reg, right, left),
                   'victim_poise': model.victim_poise, 'same': model.same_links(right),
                   'cross': model.cross_links(right, left)}
            if a.reach:
                rc = reach_module().Reach()
                for lk in out['cross']:
                    lk['reach'] = reach_check(model, right, left, lk, rc)
            print(json.dumps(out, indent=1, default=str))
        else:
            print_pair(model, right, left, a.reach)
        return 0
    if a.sweep:
        stats, results = sweep(model, a.top, a.reach)
        if a.json:
            print(json.dumps({'stats': stats, **results}, indent=1, default=str))
            return 0
        print(json.dumps(stats, indent=1))
        for name, rows in results.items():
            print(f'\n{name} (stagger share x proxy damage of the follow-up)')
            for r in rows:
                lk = r['link']
                reach = r.get('reach') or {}
                print(f"{r['value']:>7} {r['right']:<28} + {r['left']:<28} {lk['first']:>9}->{lk['next']:<7} "
                      f"gap {lk['gap']:>5} (+hs {lk['gap_hitstop']}) esc {lk['on_break']['escape']} "
                      f"stag {round(100 * lk['stagger'])}% dmg {r['damage']}"
                      + (f" reach tip {reach.get('tip', {}).get('reaches')} "
                         f"body {reach.get('body', {}).get('reaches')}" if reach else ''))
        return 0
    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
