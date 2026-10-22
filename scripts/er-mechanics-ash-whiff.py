#!/usr/bin/env python3
"""Whiff risk of an ash of war: the opponent rolls through it and punishes the recovery.

    python3 scripts/er-mechanics-ash-whiff.py --selftest

The user's question (2026-10-09): "we need to find a formula for determining how risky an ash is
based on how long the time is between activation and damage frames, how long the ash locks you
into an animation, and how early any hyper armor kicks in". `er-mechanics-ash-interrupt` answers
the opponent attacking into the cast; this module answers the other reply, the opponent rolling the
first hit and hitting the whiffed recovery. `er-mechanics-combo-loadout.py --whiff-weight` (the
default) weighs each row by `combined()`: `(1 - DODGE_SHARE) x` the interrupt multiplier `+
DODGE_SHARE x net()`, floored at 0 as a whole, so a punish larger than the cast is charged against
the interrupt half rather than vanishing at 0. Each opponent gives one reply, not both. Multiplying the two (tried
first, 2026-10-09) charged a slow ash twice, and since p_dodge is 1.00 for every ash whose first
hit is later than about f16 on the STR sweep, it set every punishable ash to 0 and the rank cut
became a tie-break.

It reads the interrupt profile of the row (`er-mechanics-ash-interrupt.Rows.profile`: my hits with
contact delay, damage and heal, my TAE 795 windows, the gate, the engagement distance `D`) and the
same opponent pool (`Opponent`: reaction quantiles, B's hit frames, poise, weights, reach), so no
damage, poise or reaction model is new. It lives in its own file because the interrupt cache is
keyed on a hash of `er-mechanics-ash-interrupt.py`, and this term does not change those profiles.

Per (pool row j, reaction quantile k), on the cast clock (frame 0 = skill start):

- Telegraph `T` = my first hit frame. A two-handed row is seen from the start of its grip change
  (`er-mechanics-combo-loadout.add_grip_change`), so B's reaction counts from `-lead`.
- Roll: the medium roll a000_027110 (`er-mechanics-neutral.dodge_tools`, `MEASURED`): i-frames
  from its first frame to `ROLL_IFRAMES` (13), first R1 at `ROLL_READY` (20).
- B can start the roll at `r_k` = reaction + two network legs - `lead` (the interrupt model's
  `reaction_frames`). He dodges when `max(r_k, T - ROLL_IFRAMES) <= T`, i.e. `r_k <= T` since the
  i-frames open on the roll's first frame. p_dodge = the weighted share of (j, k) that dodge.
- Roll start `s_k`: among starts in [max(r_k, T - ROLL_IFRAMES), T], the one whose i-frames cover
  the most of my damage, earliest on a tie (`INFERRED`: B knows the ash and times the roll to
  swallow as many of its hits as one roll can). Hits after `s_k + ROLL_IFRAMES` are late hits.
- Punish: B presses R1 when the roll allows it and closes the same gap the interrupt model gives
  him (`er-mechanics-neutral.arrival`, the roll taken to leave the distance unchanged, `INFERRED`):
  his hits land at `s_k + ROLL_READY + approach_j + t_jh` while they are before my gate.
- A late hit before B's first punish hit lands on him and stops the punish (`INFERRED`: no poise
  check on his side): I deliver the late hits and take nothing.
- Otherwise every punish hit before the gate takes `B_HIT_HP` x the HP rate of my window open at
  that frame. My late hits at or before B's first hit still land; those after it land only when a
  hyperarmor window open at B's first hit holds it (my poise plus the window's bonus above B's
  poise damage times the window's rate, `er-mechanics-ash-interrupt.simulate`'s break rule): a
  trade. Hyperarmor plays no part in whether B can dodge; it only turns a punish into a trade.

    outcome_jk = full                          not dodged
               = late_jk - taken_jk            dodged
    multiplier = max(0, E_jk[outcome] / full),  full = sum (d_i + heal_i)

whiff risk = E[dodged x taken], the expected punish HP per cast.

# With geometry (a profile carrying an escape table)

The model above keeps B rolling in place and me standing still, so every hit after his 13 i-frames
lands and stops his punish: a multi-hit ash could not be whiffed. The user (2026-10-19): "How many
recovery frames does the opponent have to backstab you if you miss them entirely? This should be a
massive factor for all ashes of war." A profile built by the current `er-mechanics-ash-interrupt`
carries the key of an escape table (that module's docstring: both players moved by root motion, my
tracking turn, his roll direction and start, a hold and a chase plan), and `whiff` then reads:

- `full` = the hits that touch him standing at `D`; `T` = the first frame one does. He dodges when
  `r_k <= T`, as before.
- A plan is a roll direction, a start `s` on the one-frame grid and hold or chase. Hold: the hits
  that touch him on that path are the late hits; his string starts at `s + ready + approach`, the
  approach closing the gap between our axes at `s + ready` less `max(2.5 m, his reach)`; the
  stopped and trade rules above apply unchanged, with each late hit at its touch frame. Chase:
  when no hit touches him before he reaches backstab position, and that is before my first roll
  frame (`roll_gate`) and my skill does not make me unthrowable, he takes `B_BACKSTAB_HP` x the
  HP rate of my window then; a hit that touches him first lands with every other that touches.
  A plan starting after `T` is no dodge: he takes `full`.
- He aims at a plan no earlier than `r_k` and presses with a normal timing error
  (`er-mechanics-ashes.DODGE_TIMING_SD_S`, `TIMING_POINTS` quantiles, rounded to the grid; an
  early error is not clipped to his reaction, `INFERRED`). Per pool row and reaction quantile he
  takes the aim whose expected outcome over the error is worst for me, earliest on a tie
  (`INFERRED`: a seasoned defender knows the ash and his own weapon).
- `B_BACKSTAB_HP`: `er-mechanics-crits.crit_evidence(...)['backstab_hp']`, the corpus' mean best
  backstab on the corpus (`MEASURED` there), cached by `backstab_hp`.
- Strafe (a table carrying `strafe`, `er-mechanics-ash-interrupt.strafe_table`; user 2026-10-19:
  a defender who walks sideways through a 55-frame windup is out of the hit and punishes the
  recovery). He is already running round me when the cast starts, so being out of a hit needs no
  reaction; what he presses is the commit, to stop and punish (hold) or to run for the backstab
  (chase), aimed at a frame no earlier than `r_k` with the same timing error, and the hold and
  chase rules above apply from the commit as they do from a roll's R1 frame. The waiting
  defender's share of the strafe is not a new constant: per pool row and reaction he takes
  whichever of the roll and strafe plans is worst for me (the roll on a tie), and the strafe
  is open to reactions too slow to roll. So the reply stays one `DODGE_SHARE` (`INFERRED`, single
  source `er-mechanics-ashes.REACT_SHARE`), and `strafe` reports the part of it that strafed.

- Movement skills (`er-mechanics-ash-interrupt.move_motion`) are more entries of the roll table,
  so they take the roll's rules (start aimed no earlier than `r_k`, no later than `T`, the same
  timing error, hold or chase from their `ready` plus the turn back to face me). Each costs its
  FP: per pool row and reaction he answers with his skills in the share of the fight his FP pays
  for the one he would pick (`afford`), and with rolls and strafes alone in the rest. `skill` is
  the share that escaped with one, `skill_top` the clip used most; `p_dodge` the share that rolled.

- The backstep (`er-mechanics-ash-interrupt.backstep_motion`, a000_027000) is one more roll-table
  entry, free like the rolls (`FREE_MOVES`): no i-frames (its f0-7 JumpTable 8 needs the Fine
  Crucible Feather's stateInfo 473, TAE), 2.5 m straight back, first R1 at f14 against the roll's
  f20. Its timing is the roll's: aimed no earlier than `r_k`, which carries the reaction and both
  `er-mechanics-ashes.NETWORK_ONE_WAY_S` legs, with the same timing error. `backstep` is the share
  of replies that took it (the escape table lists it before the rolls, so a tie at one start keeps
  the roll), and `bstep_beats_roll` /
  `bstep_ties_roll` set its best plan against the rolls' best alone (no strafe, no skill) per pool
  row and reaction, over `bstep_open`, the replies where either is open.

Over a duel's distance spread (a profile carrying `spread`, `er-mechanics-ash-interrupt.
duel_spread`), `from_spread` runs the reply at each distance with that distance's escape table and
weighs the results by the duel's time there. `full` is then every hit of the cast, so a distance
the cast cannot reach delivers nothing, and there he punishes the whiffed recovery by running in
(the interrupt simulation's `taken` at that distance).

Extra results: `backstab` (share of casts he backstabs), `miss` (share of casts he dodges with
nothing landing on him), `strafe` (share that strafed rather than rolled; `p_dodge` is now the
share that rolled) and `geometry` (1: the result comes from the escape table, so it measured
which hits land on this carrier).

Left out: B not knowing which ash is coming, a per-reaction choice between the two replies (the
interrupt cache keeps only the pool-weighted sums, so the split is the fixed `DODGE_SHARE`), the
time a hold plan's string spends walking in (late hits during his approach are not tested), and a
bullet homing on him after it is fired. Without an escape table (an old profile, a clip with no
pose) the model above applies as written: projectile travel and both players' movement left out.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import statistics
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

HERE = Path(__file__).resolve().parent


def _ai():
    key = "er_mechanics_ash_interrupt"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, HERE / "er-mechanics-ash-interrupt.py")
        m = importlib.util.module_from_spec(spec)
        sys.modules[key] = m
        spec.loader.exec_module(m)
    return sys.modules[key]


AI = _ai()
NEU, EXCH = AI.NEU, AI.EXCH
#: Medium roll a000_027110 (`er-mechanics-neutral.py movement`, `MEASURED` 2026-10-09): i-frames
#: 0-13, first R1 frame 20. The back roll 027111 has the same two numbers.
ROLL_IFRAMES = 13.0
ROLL_READY = 20.0
#: Share of opponents who wait and roll rather than attack into the cast
#: (`er-mechanics-ashes.REACT_SHARE`, `INFERRED` 0.5), the split `er-builds-pvp` already weighs a
#: hit by: `(1 - share) x` the interrupt multiplier `+ share x` this one.
DODGE_SHARE = AI.AS.REACT_SHARE
#: Fight model the defender's FP is spread over (`er-mechanics-buffs.FIGHT_KINDS`): the duel, the
#: fight the ladder's blue-flask limit (`er-builds-ladder.CERULEAN_FLASKS`) is a rule of.
FP_FIGHT = "duel"


def afford(cost: float, fight: str = FP_FIGHT) -> float:
    """Share of his waiting replies in a fight that a movement skill of `cost` FP can be, 1 for a
    free one. His FP is the corpus median bar (`er-mechanics-ashes.FP_BAR_DEFAULT`, RL 140-160, the
    opponent pool's window) plus the ladder's blue flasks (`CERULEAN_FLASKS` x
    `er-mechanics-buffs.CERULEAN_FP`), cast as `fp_uses` counts them. Over each fight length of
    `fight` he waits for `f / ENGAGEMENT_SECONDS x DODGE_SHARE` casts (an engagement every
    `ENGAGEMENT_SECONDS`, `INFERRED` there, and this reply's share of them); the share is the mean
    over the lengths of min(1, casts paid / casts waited for). The rest fall back to rolls and
    strafes (`_whiff_moving`). His FP spent on anything else is not counted."""
    if cost <= 0:
        return 1.0
    B, lad = AI._sib("er-mechanics-buffs"), AI._sib("er-builds-ladder")
    uses = AI.AS.fp_uses(AI.AS.FP_BAR_DEFAULT + lad.CERULEAN_FLASKS * B.CERULEAN_FP, cost) or 0
    fs = B.fight_kind_points(fight)
    return statistics.fmean(min(1.0, uses / max(f / B.ENGAGEMENT_SECONDS * DODGE_SHARE, 1e-9)) for f in fs)


#: Where `backstab_hp` keeps the corpus backstab, keyed on the crits module and the corpus file.
BACKSTAB_CACHE = AI.CACHE / "whiff-backstab-hp.json"
#: Plans whose v-independent arrays are kept (`_hold_arrays`), so the affinities of one weapon,
#: which share an escape table, pay for the pool sweep once.
PLAN_MEMO_SIZE = 4
_PLANS = {}
_BS_HP = {}


def backstab_hp() -> float | None:
    """`er-mechanics-crits.crit_evidence(t)['backstab_hp']` (RL 150 +- 10): the mean over the
    corpus builds of the best backstab of their own right-hand weapons on the corpus. Kept in
    `BACKSTAB_CACHE` while the crits module and the corpus file are unchanged (it takes about 7 s).
    None when it cannot be measured: the chase plan then has no backstab."""
    if "hp" in _BS_HP:
        return _BS_HP["hp"]
    src = HERE / "er-mechanics-crits.py"
    key = hashlib.sha256(src.read_bytes()).hexdigest()[:16]
    hp = None
    try:
        crits = AI._sib("er-mechanics-crits")
        st = os.stat(crits.CORPUS)
        key = f"{key}-{st.st_size}-{int(st.st_mtime)}-since{crits.er_corpus.CORPUS_SINCE}"
        doc = json.loads(BACKSTAB_CACHE.read_text()) if BACKSTAB_CACHE.exists() else {}
        if doc.get("key") == key:
            hp = doc["hp"]
        else:
            hp = float(crits.crit_evidence(crits.Tables())["backstab_hp"]) or None
            BACKSTAB_CACHE.parent.mkdir(parents=True, exist_ok=True)
            BACKSTAB_CACHE.write_text(json.dumps({"key": key, "hp": hp, "at": time.time()}))
    except Exception as exc:  # no corpus or tables: report it and leave the backstab out
        print(f"whiff: no backstab damage ({type(exc).__name__}: {exc}); the chase plan is left out",
              file=sys.stderr)
    _BS_HP["hp"] = hp
    return hp


def measured_roll() -> tuple[float, float]:
    """(i-frames end, first R1 frame) of the forward medium roll, read from its TAE."""
    m = NEU.dodge_tools("roll")["fwd"]
    return float(m["iframes"]), float(m["ready"])


def _window_at(windows, t):
    """Per element of `t`: (poise bonus, poise rate, HP rate) of the first window of mine open
    there, else (0, 1, 1)."""
    bonus, mult, hpm = np.zeros_like(t), np.ones_like(t), np.ones_like(t)
    done = np.zeros(t.shape, bool)
    for ws, we, wb, wm, _wf, wh in windows:
        hit = ~done & (ws <= t) & (t < we)
        bonus = np.where(hit, wb, bonus)
        mult = np.where(hit, wm, mult)
        hpm = np.where(hit, wh, hpm)
        done |= hit
    return bonus, mult, hpm


def whiff(opp, hits, windows, gate: float, D: float, poise: float, lead: float = 0.0,
          roll: tuple[float, float] = (ROLL_IFRAMES, ROLL_READY), escape: dict | None = None,
          bs_hp: float | None = None, memo_key: str | None = None) -> dict | None:
    """One cast against the pool's rolling reply. `hits` [(frame, poise, damage, heal)] on the cast
    clock, `windows` as `er-mechanics-ash-interrupt.Rows.cast`. None for a cast with no damage.
    With an escape table (`er-mechanics-ash-interrupt.escape_table`, in the order of `hits`) both
    players move (module docstring); `roll` is then the table's own, and `bs_hp` the backstab."""
    if not hits:
        return None
    if escape and escape.get("T") is not None:
        return _whiff_moving(opp, hits, windows, gate, poise, lead, escape, bs_hp, memo_key)
    iframes, ready = roll
    t = np.array([h[0] for h in hits], float)
    v = np.array([h[2] + h[3] for h in hits], float)
    order = np.argsort(t, kind="stable")
    t, v = t[order], v[order]
    full = float(v.sum())
    if full <= 0:
        return None
    T = float(t[0])
    r = np.asarray(opp.offs, float) - lead                                  # (Q,)
    lo = np.maximum(r, T - iframes)
    dodged = lo <= T
    # Candidate roll starts: the earliest, and each one that just reaches a hit with its i-frames.
    cand = np.concatenate([lo[:, None], np.clip(t[None, :] - iframes, lo[:, None], T)], 1)   # (Q, n+1)
    cov = ((cand[..., None] <= t) & (t <= cand[..., None] + iframes)) @ v                    # (Q, n+1)
    best = np.array([np.lexsort((cand[q], -cov[q]))[0] for q in range(len(r))])
    s = cand[np.arange(len(r)), best]                                        # (Q,)
    late = t[None, :] > s[:, None] + iframes                                 # (Q, n)
    t_late = np.where(late, t[None, :], np.inf).min(1)                       # (Q,)
    # B's punish.
    gap = np.maximum(0.0, D - np.maximum(EXCH.STRIKE_DISTANCE_M, opp.reach))
    approach, _ = NEU.arrival(gap, k=opp.k)                                  # (R,)
    start = s[None, :] + ready + approach[:, None]                           # (R, Q)
    bt = start[..., None] + opp.t[:, None, :]                                # (R, Q, H)
    valid = opp.m[:, None, :] & (bt < gate)
    fb = np.where(valid, bt, np.inf).min(-1)                                 # (R, Q)
    stopped = t_late[None, :] < fb
    _, _, hpm = _window_at(windows, bt)
    taken = np.where(valid, AI.B_HIT_HP * hpm, 0.0).sum(-1)                  # (R, Q)
    # Hyperarmor at B's first hit.
    h0 = np.argmax(valid, -1)
    q0 = np.take_along_axis(np.broadcast_to(opp.q[:, None, :], bt.shape), h0[..., None], -1)[..., 0]
    bonus, mult, _ = _window_at(windows, np.where(np.isfinite(fb), fb, -1e9))
    holds = np.isfinite(fb) & (poise + bonus > q0 * mult)
    lv = late[None, :, :] * v                                                # (1, Q, n)
    before = (t[None, None, :] <= fb[..., None]) | holds[..., None] | stopped[..., None]
    delivered = (lv * before).sum(-1)                                        # (R, Q)
    taken = np.where(stopped, 0.0, taken)
    out = np.where(dodged[None, :], delivered - taken, full)
    Q = len(r)
    W = np.broadcast_to(opp.w[:, None] / Q, out.shape)
    dmask = np.broadcast_to(dodged[None, :], out.shape)
    return {"T": T, "lead": lead, "gate": gate, "full": full,
            "p_dodge": float((W * dmask).sum()),
            "punish_hp": float((W * dmask * taken).sum()),
            "late": float((W * dmask * delivered).sum()),
            "stopped": float((W * dmask * stopped).sum()),
            "trade": float((W * dmask * ~stopped * holds).sum()),
            "out": float((W * out).sum())}


def _dec(a):
    """An escape table's half-frame integers as frames, -1 as inf."""
    a = np.asarray(a, float)
    return np.where(a < 0, np.inf, a / 2.0)


def timing_offsets() -> np.ndarray:
    """Grid steps (one frame each) a press lands off its aim: `TIMING_POINTS` quantiles of a
    normal error with sd `DODGE_TIMING_SD_S`, rounded to the grid."""
    sd = AI.AS.DODGE_TIMING_SD_S * AI.AS.TAE_FPS
    nd = statistics.NormalDist()
    n = AI.AS.TIMING_POINTS
    return np.rint([sd * nd.inv_cdf((q + 0.5) / n) for q in range(n)]).astype(int)


def _smooth(a, offs):
    """`a` (..., n) averaged over the presses an aim at each grid index gives, the index moved by
    each of `offs` and held inside the grid."""
    n = a.shape[-1]
    lo, hi = max(0, -int(offs.min())), max(0, int(offs.max()))
    pad = np.concatenate([np.repeat(a[..., :1], lo, -1), a, np.repeat(a[..., -1:], hi, -1)], -1)
    out = np.zeros(a.shape, float)
    for o in offs:
        out += pad[..., lo + o:lo + o + n]
    return out / len(offs)


_GROUPS = {}


def _pool_groups(opp) -> list:
    """[(rows, t, q, m)]: the pool split by its moves' number of hits, each part's hit arrays cut
    to that many. Most moves have one hit, so the plan sweep stops paying for the longest string
    on every move."""
    if _GROUPS.get("opp") is not opp:
        cnt = opp.m.sum(1)
        out = []
        for c in np.unique(cnt):
            rows = np.flatnonzero(cnt == c)
            cols = np.argsort(~opp.m[rows], axis=1, kind="stable")[:, :max(int(c), 1)]
            out.append((rows, *(np.take_along_axis(a[rows], cols, 1) for a in (opp.t, opp.q, opp.m))))
        _GROUPS.update(opp=opp, groups=out)
    return _GROUPS["groups"]


def _replies(opp, TL, TC, go, gap, bs, windows, gate: float, poise: float, bs_hp: float | None,
             roll_gate: float, blocks: bool) -> dict:
    """Hold and chase arrays of one set of plans, each (..., ways, starts): `go` is the frame his
    string can start once he has closed the gap (`gap`, metres at that frame) and `bs` a chaser's
    backstab frame. `TL`, `TC` (hits, ways, starts) are the first touches on the hold and the
    chase plan. Hold: `taken`, `stopped`, `trade`, `lands` (hits, R, ways, starts). Chase:
    `taken`, `stopped`, `bs`, `lands` (hits, ways, starts)."""
    R = len(opp.w)
    shape = gap.shape
    fb, taken, holds = np.empty((R,) + shape), np.empty((R,) + shape), np.empty((R,) + shape, bool)
    reach = np.maximum(EXCH.STRIKE_DISTANCE_M, opp.reach)[:, None, None]
    approach, _ = NEU.arrival(np.maximum(0.0, gap[None] - reach), k=opp.k)   # (R, ways, starts)
    start = go + approach
    for rows, t, q, m in _pool_groups(opp):
        bt = start[rows][..., None] + t[:, None, None, :]
        valid = m[:, None, None, :] & (bt < gate)
        f = np.where(valid, bt, np.inf).min(-1)
        _, _, hpm = _window_at(windows, bt)
        h0 = np.argmax(valid, -1)
        q0 = np.take_along_axis(np.broadcast_to(q[:, None, None, :], bt.shape), h0[..., None], -1)[..., 0]
        bonus, mult, _ = _window_at(windows, np.where(np.isfinite(f), f, -1e9))
        fb[rows], taken[rows] = f, np.where(valid, AI.B_HIT_HP * hpm, 0.0).sum(-1)
        holds[rows] = np.isfinite(f) & (poise + bonus > q0 * mult)
    stopped = TL.min(0)[None] < fb
    taken = np.where(stopped, 0.0, taken)
    trade = ~stopped & holds
    lands_h = np.isfinite(TL)[:, None] & ((TL[:, None] <= fb[None]) | holds[None] | stopped[None])
    # Chase: the backstab, unless a hit reaches him first.
    can = np.isfinite(bs) & (bs < roll_gate) & (not blocks) & (bs_hp is not None)
    c_stop = TC.min(0) < np.where(can, bs, np.inf)
    _, _, hpm_bs = _window_at(windows, np.where(can, bs, -1e9))
    c_taken = np.where(can & ~c_stop, (bs_hp or 0.0) * hpm_bs, 0.0)
    lands_c = np.isfinite(TC) & c_stop[None]
    c_bs = can & ~c_stop
    return {"h": {"taken": taken, "stopped": stopped, "trade": trade, "lands": lands_h},
            "c": {"taken": c_taken, "stopped": c_stop, "bs": c_bs, "lands": lands_c}}


def _aims(starts, keep, offs, nr: int):
    """(aim starts in descending order, `cols`): the grid indices `keep` of every way, and
    `cols`, which turns a (..., ways, starts) array into those aims, timing-smoothed."""
    s_a = np.tile(starts[keep], nr)
    order = np.argsort(-s_a, kind="stable")
    na = len(keep)

    def cols(a):
        """`a` (..., ways, starts) smoothed and cut to the aims, (..., aims) in `order`."""
        a = _smooth(np.asarray(a, np.float32), offs)[..., keep]
        return a.reshape(a.shape[:-2] + (nr * na,))[..., order].astype(np.float32)

    return s_a[order], cols, np.repeat(np.arange(nr), na)[order]


def _plans(opp, escape: dict, windows, gate: float, poise: float, bs_hp: float | None, lead: float = 0.0,
           memo_key=None) -> dict:
    """Everything about the plans of one escape table that my hits' damage does not change.
    A plan is a roll and a start, held (`h`, per pool row) or chased (`c`), or a strafe and a
    commit frame, held (`sh`) or chased (`sc`). `lands_<plan>` (hits, R, ways, starts) for a hold
    and (hits, ways, starts) for a chase: a hit lands on him on that plan. Per aim (a roll start
    from the fastest reaction to `T`, or a strafe commit from the fastest reaction on, every way,
    in descending order `s_a` / `s_s`): timing-smoothed `taken`, `stopped`, `trade` (hold only),
    `bs` (chase only) and `miss` (nothing lands). `cols` / `cols_s` turn a (..., ways, starts)
    array into that aim order. Kept for `PLAN_MEMO_SIZE` tables."""
    key = None
    if memo_key is not None:
        key = (memo_key, id(opp), json.dumps(windows), gate, poise, bs_hp, lead)
        if key in _PLANS:
            return _PLANS[key]
    nr, n = len(escape["iframes"]), int(escape["s"][1])
    s = escape["s"][0] + np.arange(n, dtype=float)
    T = escape["T"] / 2.0
    TT = _dec(escape["touch"])                                               # (nh, 2, nr, n)
    ready = np.asarray(escape["ready"], float)[:, None]
    roll_gate, blocks = escape.get("roll_gate", gate), bool(escape.get("blocks"))
    # Hold: his string from the roll's R1 frame, after turning back to face me (a skill cast away
    # from me, `er-mechanics-ash-interrupt.turn_back`) and closing the gap.
    turn = np.asarray(escape.get("turn", 0.0), float)
    rp = _replies(opp, TT[:, 0], TT[:, 1], s + ready + turn, np.asarray(escape["gap"], float), _dec(escape["bs"]),
                  windows, gate, poise, bs_hp, roll_gate, blocks)
    taken, stopped, trade, lands_h = (rp["h"][k] for k in ("taken", "stopped", "trade", "lands"))
    c_taken, c_stop, c_bs, lands_c = (rp["c"][k] for k in ("taken", "stopped", "bs", "lands"))
    # A roll after the first touch on him standing still is no dodge: every hit that touches him
    # there lands and he takes nothing back.
    late = s > T
    touch0 = np.isfinite(_dec(escape["t0"]))
    lands_h[..., late] = touch0[:, None, None, None]
    lands_c[..., late] = touch0[:, None, None]
    taken[..., late], stopped[..., late], trade[..., late] = 0.0, True, False
    c_taken[..., late], c_stop[..., late], c_bs[..., late] = 0.0, True, False
    offs = timing_offsets()
    floor = np.floor(np.min(opp.offs) - lead)
    # Aims: no later than `T`, and no earlier than the fastest reaction can press.
    ai = np.flatnonzero((s <= T) & (s >= floor))
    s_a, cols, way = _aims(s, ai, offs, nr)
    moves = escape.get("moves") or ["roll"] * nr
    res = {"h": {"taken": cols(taken), "stopped": cols(stopped), "trade": cols(trade), "miss": cols(~lands_h.any(0))},
           "c": {"taken": cols(c_taken), "stopped": cols(c_stop), "bs": cols(c_bs), "miss": cols(~lands_c.any(0))},
           "lands_h": lands_h, "lands_c": lands_c, "s_a": s_a, "cols": cols, "T": T, "na": len(ai), "ns": 0,
           "skill_aim": np.array([moves[i] not in AI.FREE_MOVES for i in way], bool), "way": way, "moves": moves}
    st = escape.get("strafe")
    if st:
        # Strafe: running from frame 0, so he needs no reaction to be out of a hit; the commit
        # (stop and punish, or run for the backstab) is a press, aimed no earlier than he reacts.
        c = st["c"][0] + np.arange(int(st["c"][1]), dtype=float)
        ST = _dec(st["touch"])                                               # (nh, 2, ways, nc)
        sp = _replies(opp, ST[:, 0], ST[:, 1], np.broadcast_to(c, (2, len(c))), np.asarray(st["gap"], float),
                      _dec(st["bs"]), windows, gate, poise, bs_hp, roll_gate, blocks)
        ci = np.flatnonzero(c >= floor)
        s_s, cols_s, _ = _aims(c, ci, offs, 2)
        res.update({"sh": {k: cols_s(sp["h"][k]) for k in ("taken", "stopped", "trade")},
                    "sc": {k: cols_s(sp["c"][k]) for k in ("taken", "stopped", "bs")},
                    "lands_sh": sp["h"]["lands"], "lands_sc": sp["c"]["lands"], "s_s": s_s, "cols_s": cols_s,
                    "ns": len(ci)})
        res["sh"]["miss"] = cols_s(~sp["h"]["lands"].any(0))
        res["sc"]["miss"] = cols_s(~sp["c"]["lands"].any(0))
    if key is not None:
        while len(_PLANS) >= PLAN_MEMO_SIZE:
            _PLANS.pop(next(iter(_PLANS)))
        _PLANS[key] = res
    return res


def _whiff_moving(opp, hits, windows, gate, poise, lead, escape, bs_hp, memo_key):
    """`whiff` with both players moving (module docstring)."""
    v = np.array([h[2] + h[3] for h in hits], float)
    touch0 = np.isfinite(_dec(escape["t0"]))
    full = float(v[touch0].sum())
    if full <= 0:
        return None
    p = _plans(opp, escape, windows, gate, poise, bs_hp, lead, memo_key)
    T = p["T"]
    if not p["na"] and not p["ns"]:
        return None
    r = np.asarray(opp.offs, float) - lead
    Q, R = len(r), len(opp.w)

    def deliver(hold, chase, cols):
        dh = np.zeros(p[hold].shape[1:], np.float32)
        dc = np.zeros(p[chase].shape[1:], np.float32)
        for h in np.flatnonzero(v):
            dh += np.float32(v[h]) * p[hold][h]
            dc += np.float32(v[h]) * p[chase][h]
        return cols(dh), cols(dc)

    def best(score):
        """(running best value, its aim) along the aims in descending start order; the latest
        (earliest start) aim on a tie."""
        cm = np.maximum.accumulate(score, -1)
        return cm, np.maximum.accumulate(np.where(score >= cm, np.arange(score.shape[-1]), 0), -1)

    # The reactive defender's options: roll and hold, roll and chase, strafe and hold, strafe and
    # chase, the roll set holding his movement skills beside the rolls. A hold plan depends on his
    # own move; a chase plan does not. Per pool row and reaction he takes the one worst for me,
    # earliest in that order on a tie, so a strafe that does no better than the roll leaves the
    # roll's result as it was. A roll needs `r <= T`; a strafe needs only a commit he can press.
    deliv = {}
    sets = (("h", "c", "lands_h", "lands_c", p["s_a"], p["cols"], p["na"], r <= T),
            ("sh", "sc", "lands_sh", "lands_sc", p.get("s_s"), p.get("cols_s"), p["ns"], np.ones(Q, bool)))
    for hk, ck, hold, chase, _, cols, n_aims, _ in sets:
        if n_aims:
            deliv[hk], deliv[ck] = deliver(hold, chase, cols)

    moves_ = p["moves"]
    is_skill = np.array([m not in AI.FREE_MOVES for m in moves_], bool)

    def decide(allowed, strafe: bool = True):
        """(options, pick, any option open) with the roll-set entries `allowed` (a bool per entry
        of `moves`: the rolls, the backstep, the movement skills), and the strafe unless not
        `strafe`."""
        opts = []
        for hk, ck, _, _, s_a, _, n_aims, may in sets:
            if not n_aims or (hk == "sh" and not strafe):
                continue
            cnt = (s_a[None, :] >= r[:, None]).sum(1)
            ok = may & (cnt > 0)
            k_at = np.maximum(cnt - 1, 0)
            sh, sc = -(deliv[hk] - p[hk]["taken"]), -(deliv[ck] - p[ck]["taken"])
            if hk == "h":
                bar = ~np.asarray(allowed, bool)[p["way"]]
                sh, sc = np.where(bar, -np.inf, sh), np.where(bar, -np.inf, sc)
            vh, ih = best(sh)
            vc, ic = best(sc)
            opts.append((hk, np.where(ok[None, :], vh[:, k_at], -np.inf), ih[:, k_at]))
            opts.append((ck, np.broadcast_to(np.where(ok, vc[k_at], -np.inf)[None, :], (R, Q)), ic[k_at]))
        if not opts:
            return [], np.zeros((R, Q), int), np.zeros((R, Q), bool), np.full((R, Q), -np.inf)
        vals = np.stack([o[1] for o in opts])                                # (opts, R, Q)
        return opts, np.argmax(vals, 0), np.isfinite(vals.max(0)), vals.max(0)

    def at(d, name, src):
        """Decision `d`'s chosen option's `name` (from `src[option key]`, 0 where it has none)."""
        opts, pick = d[:2]
        out = np.zeros((R, Q))
        for i, (k, _, aim) in enumerate(opts):
            if name not in src.get(k, {}):
                continue
            a = src[k][name]
            got = np.take_along_axis(a, aim, 1) if a.ndim == 2 else a[aim][None, :]
            out = np.where(pick == i, got, out)
        return out

    def way_of(d):
        """The roll-set entry (a roll, the backstep or a movement skill, `moves`) each pick used, -1
        for a strafe."""
        opts, pick = d[:2]
        way = np.full((R, Q), -1)
        for i, (k, _, aim) in enumerate(opts):
            if k in ("h", "c") and p["na"]:
                way = np.where(pick == i, p["way"][np.broadcast_to(aim, (R, Q))], way)
        return way

    # FP: with his skills he answers as `full` does in the share of the fight his FP pays for the
    # skill picked (`afford`); in the rest he answers as `base`, with rolls and strafes alone.
    full_d, base_d = decide(np.ones(len(moves_), bool)), decide(~is_skill)
    way = way_of(full_d)
    pay = np.array([afford(c) for c in (escape.get("fp") or [0.0] * len(p["moves"]))])
    a = np.where(way >= 0, np.where(is_skill[np.maximum(way, 0)], pay[np.maximum(way, 0)], 1.0), 1.0)
    W = np.broadcast_to(opp.w[:, None] / Q, (R, Q))
    vsrc = {k: {"v": x} for k, x in deliv.items()}

    def mix(f):
        """`f(decision)` (R, Q) blended: `a` of the skill decision, the rest of the base one."""
        return a * f(full_d) + (1.0 - a) * f(base_d)

    def mean(name):
        return float((W * mix(lambda d: d[2] * at(d, name, p))).sum())

    def share(keys):
        return float((W * mix(lambda d: d[2] * np.isin(d[1], [i for i, (k, _, _) in enumerate(d[0])
                                                                 if k in keys]))).sum())

    late = mix(lambda d: d[2] * at(d, "v", vsrc))
    o = mix(lambda d: np.where(d[2], at(d, "v", vsrc) - at(d, "taken", p), full))
    used = {}
    for j in np.unique(way[full_d[2] & (way >= 0)]):
        if is_skill[j]:
            name = p["moves"][j].rsplit(" ", 1)[0]
            used[name] = used.get(name, 0.0) + float((W * full_d[2] * a * (way == j)).sum())
    skill = sum(used.values())
    # The backstep: the share of replies that took it (either decision), and, set against the
    # rolls alone (no strafe, no skill), how often its best plan does better for him than the
    # rolls' best, as well, or worse, over the replies where either is open.
    is_bstep = np.array([m == "backstep" for m in moves_], bool)
    backstep = float((W * mix(lambda d: d[2] * np.isin(way_of(d), np.flatnonzero(is_bstep)))).sum())
    out = {}
    if is_bstep.any():
        vr = decide(np.array([m == "roll" for m in moves_], bool), strafe=False)[3]
        vb = decide(is_bstep, strafe=False)[3]
        both = np.isfinite(vr) | np.isfinite(vb)
        out = {"bstep_beats_roll": float((W * (both & (vb > vr))).sum()),
               "bstep_ties_roll": float((W * (both & (vb == vr))).sum()),
               "bstep_open": float((W * both).sum())}

    return {"T": T, "lead": lead, "gate": gate, "full": full,
            "p_dodge": share(("h", "c")) - skill - backstep, "backstep": backstep, **out,
            "skill": skill, "skill_top": max(((v, k) for k, v in used.items()), default=(0.0, ""))[1],
            "strafe": share(("sh", "sc")), "punish_hp": mean("taken"), "late": float((W * late).sum()),
            "stopped": mean("stopped"), "trade": mean("trade"), "backstab": mean("bs"), "miss": mean("miss"),
            "out": float((W * o).sum()), "roll_gate": float(escape.get("roll_gate", gate)), "geometry": 1.0}


def distinct(opp) -> SimpleNamespace:
    """`opp` with the moves `whiff` cannot tell apart merged into one and their weights summed.
    `whiff` reads a move only through its reach and its hits' times, poise and mask, and the
    measured pool of 7729 moves holds 2617 such shapes, so a row costs a third as much."""
    key = np.concatenate([opp.reach[:, None], np.where(opp.m, opp.t, -1.0),
                          np.where(opp.m, opp.q, -1.0), opp.m], 1)
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    return SimpleNamespace(offs=opp.offs, k=opp.k, reach=opp.reach[first], t=opp.t[first],
                           q=opp.q[first], m=opp.m[first],
                           w=np.bincount(inv.ravel(), opp.w, len(first)))


def multiplier(w: dict | None) -> float:
    """Expected delivered net value over the undisturbed value, at least 0; 1 with nothing to
    weigh. A report figure: `combined` takes `net`."""
    return max(0.0, net(w))


def net(w: dict | None) -> float:
    """Expected delivered net value over the undisturbed value, below 0 when his punish takes
    more than the cast delivers; 1 with nothing to weigh."""
    if not w:
        return 1.0
    return w["out"] / w["full"]


def combined(imult: float, wnet: float, share: float = DODGE_SHARE) -> float:
    """The row's multiplier against both replies: `(1 - share) x imult + share x wnet`, at least 0.

    `wnet` is `net`, not `multiplier`: the reply's punish is charged against what the other reply
    delivers. Flooring each reply at 0 first (until 2026-10-19) made a cast that is backstabbed
    for 940 HP after it whiffs (Wings on a Gargoyle's Greatsword) cost exactly what a cast that
    merely misses costs, so the recovery was never charged: 6151 of the 6174 heavy-kit rows had
    the reply at 0, and the rank between them came from the interrupt half alone."""
    return max(0.0, (1.0 - share) * imult + share * wnet)


def from_profile(opp, prof: dict, lead: float = 0.0, roll=(ROLL_IFRAMES, ROLL_READY)) -> dict | None:
    """`whiff` of one cached `er-mechanics-ash-interrupt` profile."""
    if not prof or "error" in prof or not prof.get("hits"):
        return None
    if prof.get("spread"):
        return from_spread(opp, prof, lead, roll)
    esc = prof.get("_escape")
    if esc is None and prof.get("escape"):
        esc = AI.escape_store().get(prof["escape"])
    bs = backstab_hp() if esc else None
    return whiff(opp, prof["hits"], prof["windows"], prof["gate"], prof["D"], prof["poise"], lead, roll,
                 escape=esc, bs_hp=bs, memo_key=prof.get("escape"))


#: Results `from_spread` sums over the distances with their weights; the rest are per-distance
#: shares or HP that sum the same way.
SPREAD_SUMMED = ("p_dodge", "backstep", "skill", "strafe", "punish_hp", "late", "stopped", "trade", "backstab",
                 "miss", "out")


def from_spread(opp, prof: dict, lead: float = 0.0, roll=(ROLL_IFRAMES, ROLL_READY)) -> dict | None:
    """`whiff` of a profile valued over the duel's distances (`er-mechanics-ash-interrupt.
    Rows.spread_profile`): each distance's reply with that distance's escape table, its result
    weighted by the distance's share of the duel. `full` is every hit of the cast, the value the
    sweep credits, so `net` = `out` / `full` charges the hits a distance cannot reach. A distance
    where no hit touches him standing (`reach` False) is one the player never casts at
    (`Rows.spread_profile`): it adds nothing, so `net` is the in-range time share x the reply's
    value over the in-range distances."""
    v = np.array([h[2] + h[3] for h in prof["hits"]], float)
    if float(v.sum()) <= 0:
        return None
    return _spread_sum(prof, [_spread_at(opp, prof, q, lead, roll) for q in prof["spread"]], lead)


def from_spreads(opp, profs, lead: float = 0.0, roll=(ROLL_IFRAMES, ROLL_READY)) -> list:
    """[`from_profile` of each of `profs`], the spread ones taken distance by distance across all
    of them: rows whose casts share their escape tables (one weapon's affinities) then share each
    distance's plans (`_plans`), which a row at a time would compute again once its eight or so
    distances had pushed the first ones out of `PLAN_MEMO_SIZE`. Each `whiff` is the call
    `from_spread` makes and each row's distances are summed in their own order, so every result
    is the one `from_profile` returns."""
    out = [None] * len(profs)
    spread = []
    for i, prof in enumerate(profs):
        if not prof or "error" in prof or not prof.get("hits"):
            continue
        if not prof.get("spread"):
            out[i] = from_profile(opp, prof, lead, roll)
        elif float(np.array([h[2] + h[3] for h in prof["hits"]], float).sum()) > 0:
            spread.append(i)
    at = {i: [None] * len(profs[i]["spread"]) for i in spread}
    for j in range(max((len(profs[i]["spread"]) for i in spread), default=0)):
        for i in spread:
            if j < len(at[i]):
                at[i][j] = _spread_at(opp, profs[i], profs[i]["spread"][j], lead, roll)
    for i in spread:
        out[i] = _spread_sum(profs[i], at[i], lead)
    return out


#: Not a reply: `_spread_at` of a distance the player never casts at (`reach` False).
_UNREACHED = object()


def _spread_at(opp, prof, q, lead, roll):
    """(`whiff` at the spread distance `q`, whether it had an escape table), or `_UNREACHED`."""
    if not q.get("reach", any(q["touch"])):
        return _UNREACHED
    esc = None
    if q.get("escape"):
        esc = (prof.get("_escapes") or {}).get(q["escape"]) or AI.escape_store().get(q["escape"])
    if esc is not None and esc.get("T") is not None:
        return whiff(opp, prof["hits"], prof["windows"], prof["gate"], q["D"], prof["poise"], lead, roll,
                     escape=esc, bs_hp=backstab_hp(), memo_key=q["escape"]), True
    mine = [h for h, t in zip(prof["hits"], q["touch"]) if t]
    return whiff(opp, mine, prof["windows"], prof["gate"], q["D"], prof["poise"], lead, roll), False


def _spread_sum(prof, results, lead) -> dict:
    """`from_spread`'s sum of its distances' `_spread_at` results, in the spread's order."""
    v = np.array([h[2] + h[3] for h in prof["hits"]], float)
    full = float(v.sum())
    acc = {k: 0.0 for k in SPREAD_SUMMED}
    parts, T, used, geo = [], None, {}, True
    for q, got in zip(prof["spread"], results):
        if got is _UNREACHED:
            continue
        w = q["w"]
        r, moving = got
        geo = geo and moving
        if r is None:
            continue
        T = r["T"] if T is None else min(T, r["T"])
        if r.get("skill_top"):
            used[r["skill_top"]] = used.get(r["skill_top"], 0.0) + w * r.get("skill", 0.0)
        for k in SPREAD_SUMMED:
            acc[k] += w * float(r.get(k, 0.0))
        # [distance, credited time, the reply's net there (not weighted), the wave node it stands for]
        parts.append([q["D"], w, round(float(r["out"]) / full, 4), q.get("node", q["D"])])
    return {**acc, "T": T if T is not None else float(min(h[0] for h in prof["hits"])), "lead": lead,
            "gate": prof["gate"], "full": full, "geometry": 1.0 if geo else 0.0, "share": prof.get("share"),
            "skill_top": max(((s, k) for k, s in used.items()), default=(0.0, ""))[1], "spread": parts}


# --------------------------------------------------------------------------------------------

def selftest() -> int:
    fails = []

    def check(cond, msg):
        print(("ok   " if cond else "FAIL ") + msg)
        if not cond:
            fails.append(msg)

    class Fake:
        offs = np.array([10.0, 30.0])        # two reaction quantiles
        w = np.array([1.0])
        reach = np.array([3.0])
        k = 7.5
        t = np.array([[15.0, 30.0]])         # B's punish string: hits 15 and 30 frames after input
        q = np.array([[60.0, 60.0]])
        m = np.array([[True, True]])

    f = Fake()
    hp = AI.B_HIT_HP
    # T = 20: only the fast quantile (10) dodges. Roll at max(10, 7) = 10 covers 10-23.
    # Punish starts 10 + 20 = 30, hits at 45 and 60; gate 100 -> both land.
    w = whiff(f, [(20.0, 50.0, 100.0, 0.0)], [], 100.0, 2.5, 40.0, roll=(13.0, 20.0))
    check(abs(w["p_dodge"] - 0.5) < 1e-9, "p_dodge is the share of reactions at or before the first hit")
    check(abs(w["punish_hp"] - 0.5 * 2 * hp) < 1e-9, "a whiffed cast eats every punish hit before the gate")
    check(abs(w["out"] - (0.5 * 100.0 + 0.5 * (0.0 - 2 * hp))) < 1e-9, "outcome mixes the full and whiffed casts")
    # Gate 50: only B's first hit (45) is before the gate.
    w = whiff(f, [(20.0, 50.0, 100.0, 0.0)], [], 50.0, 2.5, 40.0, roll=(13.0, 20.0))
    check(abs(w["punish_hp"] - 0.5 * hp) < 1e-9, "a short gate shrinks the punish")
    # A two-handed lead of 7 frames: the 30-frame quantile reacts at 23 on the cast clock, before 25.
    w = whiff(f, [(25.0, 50.0, 100.0, 0.0)], [], 50.0, 2.5, 40.0, lead=7.0, roll=(13.0, 20.0))
    check(abs(w["p_dodge"] - 1.0) < 1e-9, "the grip change counts as telegraph")
    # A late hit at 40 lands before B's first punish hit (s=10 -> 45): punish stopped.
    w = whiff(f, [(20.0, 50.0, 100.0, 0.0), (40.0, 50.0, 80.0, 0.0)], [], 100.0, 2.5, 40.0, roll=(13.0, 20.0))
    check(abs(w["stopped"] - 0.5) < 1e-9 and abs(w["late"] - 0.5 * 80.0) < 1e-9,
          "a late hit before the punish lands and stops it")
    # The best roll start covers both hits 20 and 30 (start 17 covers 17-30) over the earliest (10).
    w = whiff(f, [(20.0, 50.0, 100.0, 0.0), (30.0, 50.0, 100.0, 0.0)], [], 100.0, 2.5, 40.0, roll=(13.0, 20.0))
    check(w["late"] == 0.0 and abs(w["punish_hp"] - 0.5 * 2 * hp) < 1e-9,
          "the roll is timed to swallow as many hits as it can")
    # Hyperarmor +100 over 40-50 holds B's 60-poise first hit at 45: my hit at 48 still lands.
    late = [(20.0, 50.0, 100.0, 0.0), (48.0, 50.0, 80.0, 0.0)]
    w = whiff(f, late, [], 100.0, 2.5, 40.0, roll=(13.0, 20.0))
    check(w["late"] == 0.0 and w["trade"] == 0.0, "without hyperarmor B's hit cancels my later hit")
    w = whiff(f, late, [(40.0, 50.0, 100.0, 1.0, 0.0, 1.0)], 100.0, 2.5, 40.0, roll=(13.0, 20.0))
    check(abs(w["late"] - 0.5 * 80.0) < 1e-9 and abs(w["trade"] - 0.5) < 1e-9, "hyperarmor turns the punish into a trade")
    check(multiplier(None) == 1.0 and multiplier({"out": -5.0, "full": 10.0}) == 0.0, "multiplier floor and neutral")
    check(abs(combined(0.8, 0.2, 0.5) - 0.5) < 1e-9, "the two replies are mixed by the dodge share")
    check(net({"out": -5.0, "full": 10.0}) == -0.5 and net(None) == 1.0, "net keeps a punish larger than the cast")
    check(abs(combined(0.8, -0.4, 0.5) - 0.2) < 1e-9 and combined(0.2, -1.0, 0.5) == 0.0,
          "a punished whiff is charged against the interrupt half, and only the mix is floored")
    # Out of B's reach: D 4 m against a 3 m reach adds 7.5 frames of running.
    w = whiff(f, [(20.0, 50.0, 100.0, 0.0)], [], 50.0, 4.0, 40.0, roll=(13.0, 20.0))
    check(w["punish_hp"] == 0.0, "closing distance pushes the punish past a short gate")
    # Three moves, two the same shape but for a masked hit time: merging them changes nothing.
    pool = SimpleNamespace(offs=f.offs, k=f.k, w=np.array([0.2, 0.3, 0.5]), reach=np.array([3.0, 3.0, 5.0]),
                           t=np.array([[15.0, 30.0], [15.0, 99.0], [12.0, 40.0]]),
                           q=np.array([[60.0, 60.0], [60.0, 7.0], [30.0, 30.0]]),
                           m=np.array([[True, True], [True, False], [True, True]]))
    pool.m[0, 1] = False
    d = distinct(pool)
    a = whiff(pool, late, [(40.0, 50.0, 100.0, 1.0, 0.0, 1.0)], 100.0, 2.5, 40.0, roll=(13.0, 20.0))
    b = whiff(d, late, [(40.0, 50.0, 100.0, 1.0, 0.0, 1.0)], 100.0, 2.5, 40.0, roll=(13.0, 20.0))
    check(len(d.w) == 2 and all(abs(a[k] - b[k]) < 1e-12 for k in a),
          "merging moves whiff cannot tell apart keeps every result")
    # Over the duel's distances (`from_spread`): at 2 m the hit touches and the flat model applies;
    # at 12 m nothing touches, so the cast is never thrown there and that distance adds nothing.
    hit = [(20.0, 50.0, 100.0, 0.0)]
    near = whiff(f, hit, [], 50.0, 2.0, 40.0, roll=(13.0, 20.0))
    sp = {"hits": hit, "windows": [], "gate": 50.0, "poise": 40.0, "D": 2.0, "share": 0.25,
          "spread": [{"D": 2.0, "w": 0.25, "touch": [True], "reach": True, "sim": {"taken": 0.0}},
                     {"D": 12.0, "w": 0.75, "touch": [False], "reach": False}]}
    got = from_profile(f, sp, roll=(13.0, 20.0))
    check(abs(net(got) - 0.25 * near["out"] / 100.0) < 1e-12 and got["full"] == 100.0 and got["share"] == 0.25
          and abs(got["miss"] - 0.25 * near.get("miss", 0.0)) < 1e-12,
          "over the spread the reply is the in-range time share x its value there; out of range it is never thrown")
    none = {**sp, "share": 0.0, "spread": [{**q, "touch": [False], "reach": False} for q in sp["spread"]]}
    check(net(from_profile(f, none, roll=(13.0, 20.0))) == 0.0, "an ash that reaches nowhere on the wave replies 0")
    # `from_spreads`: rows taken distance by distance give each row what `from_profile` gives it.
    two = {**sp, "hits": [(20.0, 50.0, 100.0, 0.0), (30.0, 50.0, 60.0, 0.0)],
           "spread": [{**sp["spread"][0], "touch": [True, False]}, {**sp["spread"][1], "D": 4.0, "w": 0.5,
                                                                    "touch": [True, True], "reach": True},
                      {**sp["spread"][1]}]}
    flat = {"hits": hit, "windows": [], "gate": 50.0, "poise": 40.0, "D": 2.0}
    rows = [sp, none, two, flat, None, {"error": "x"}, {**sp, "hits": []}]
    check(from_spreads(f, rows, 7.0, roll=(13.0, 20.0)) == [from_profile(f, p, 7.0, roll=(13.0, 20.0)) for p in rows],
          "from_spreads gives every row, spread or not, what from_profile gives it")
    moving_selftest(check, f)
    try:
        check(measured_roll() == (ROLL_IFRAMES, ROLL_READY), "roll constants match the measured TAE")
    except Exception as exc:  # game files absent: the constants stand as recorded
        print(f"skip roll measurement: {type(exc).__name__}: {exc}")
    print(f"{len(fails)} failed")
    return 1 if fails else 0


def report(keys: list[str], cache: Path, grip_change: float, skills: bool = True) -> int:
    """Profile each row key ('weapon|affinity|ash|grip') afresh with the stats, armor, distance and
    gate of `cache` (an `er-mechanics-ash-interrupt` cache), and print its whiff with and without
    the geometry, with the time each part took."""
    doc = json.loads(cache.read_text())
    st, cached = doc["settings"], doc["rows"]
    t0 = time.time()
    opp = AI.Opponent()
    rows = AI.Rows(opp, st["stats"], st["armor"], st["max_hp"])
    xs = [dict(zip(("weapon", "affinity", "ash", "grip"), k.split("|")[:4])) for k in cached]
    t_m = time.time()
    moves = rows.set_movers(xs) if skills else []
    if not skills:
        rows.movers = []
    print(f"movers {time.time() - t_m:.1f} s: {len(moves)} escape clips x {len(AI.MOVE_HEADINGS)} headings")
    for name, ok, why in getattr(rows, "move_report", []):
        m = next((m for m in moves if m["name"] == name), None)
        print(f"  {'ESCAPE' if ok else 'no    '} {name}: {why}"
              + (f"; ready f{m['ready']:g}, {m['fp']:g} FP, paid in {afford(m['fp']):.2f} of the replies" if m else ""))
    pool = distinct(opp)
    poise = rows.armor_poise() if st.get("poise") is None else st["poise"]
    print(f"setup {time.time() - t0:.1f} s, backstab {backstab_hp() or 0:.0f} HP, poise {poise:g}")
    for key in keys:
        weapon, affinity, ash, grip, *rest = key.split("|")
        old = cached.get(key) or {}
        x = {"weapon": weapon, "affinity": affinity, "ash": ash, "grip": grip, "cat": "non-bullet",
             "gate": old.get("gate"), "melee_reach": None, "reach": None, "last": 0}
        if rest and rest[0].startswith("lead"):
            x["lead"] = float(rest[0][4:])
        lead = grip_change if grip == "2H" else 0.0
        t1 = time.time()
        spread = st.get("spread")
        prof = rows.profile(x, poise, None if spread else old.get("D"), spread)
        t2 = time.time()
        w = from_profile(pool, prof, lead)
        t3 = time.time()
        from_profile(pool, prof, lead)
        t4 = time.time()
        flat = whiff(pool, prof["hits"], prof["windows"], prof["gate"], prof["D"], prof["poise"], lead)
        esc = prof.get("_escape") or next(iter((prof.get("_escapes") or {}).values()), {})
        if w and w.get("spread"):
            print("  spread: " + ", ".join(f"{d:g} m x{q:.3f} net {n:+.3f}" for d, q, n, *_ in w["spread"]))
        print(f"{key}: D {prof['D']} m, gate f{prof['gate']}, roll gate f{esc.get('roll_gate')}, "
              f"profile {t2 - t1:.2f} s, whiff {t3 - t2:.2f} s (again {t4 - t3:.3f} s)"
              + (f", escape error {prof['escape_error']}" if prof.get("escape_error") else ""))
        for name, r in (("moving", w), ("standing", flat)):
            if r:
                print(f"  {name:8} T f{r['T']:.1f} full {r['full']:.1f} p_dodge {r['p_dodge']:.2f} "
                      f"punish {r['punish_hp']:.1f} late {r['late']:.1f} stopped {r['stopped']:.2f} "
                      f"trade {r['trade']:.2f} backstab {r.get('backstab', 0):.2f} miss {r.get('miss', 0):.2f} "
                      f"strafe {r.get('strafe', 0):.2f} skill {r.get('skill', 0):.2f} ({r.get('skill_top') or '-'}) "
                      f"out {r['out']:.1f} net {net(r):.3f}"
                      + (f" backstep {r['backstep']:.2f}, beats the roll {r['bstep_beats_roll']:.3f} ties "
                         f"{r['bstep_ties_roll']:.3f} of {r['bstep_open']:.3f} open" if "bstep_open" in r else ""))
        if esc.get("moves"):
            print(f"  escape table: {len(esc['moves'])} entries kept "
                  f"({sum(m not in AI.FREE_MOVES for m in esc['moves'])} skill headings)")
    return 0


def compare_spread(keys: list[str], cache: Path, sweep: Path, arena_m: float, grip_change: float) -> int:
    """Each row key ('weapon|affinity|ash|grip') of the sweep `sweep`, profiled as
    `er-mechanics-ash-interrupt.profiles` does (the pool with its read, carried poise, the sweep's
    movement skills) at the stats, armor and max HP of `cache`, three ways: at the fixed
    `REACH_SHARE` distance, over the stationary wave and over the duel (`duel_spread` from
    `arena_m`). Prints the interrupt multiplier, the whiff reply's `net`, `combined` and the time
    each profile took."""
    st = json.loads(cache.read_text())["settings"]
    rows_in = json.loads(sweep.read_text())
    t0 = time.time()
    opp = AI.with_read(AI.Opponent())
    melee = AI.pool_melee_m(opp)
    rows = AI.Rows(opp, st["stats"], st["armor"], st["max_hp"], carry=AI.poise_carries())
    moves = rows.set_movers(rows_in)
    poise = rows.armor_poise() if st.get("poise") is None else st["poise"]
    pool = distinct(AI.Opponent())
    ways = (("fixed", None), ("stationary", AI.duel_spread(arena_m, None)), ("duel", AI.duel_spread(arena_m, melee)))
    print(f"setup {time.time() - t0:.1f} s: contact {AI.contact_m():g} m, start {arena_m:g} m, melee floor "
          f"{melee:g} m, {len(moves)} movement clips, poise {poise:g}")
    for name, sp in ways[1:]:
        print(f"  {name}: " + ", ".join(f"{n[0]:g} m x{n[1]:.3f} ({n[2]:g}-{n[3]:g})" for n in sp["nodes"]))
    bands = {}
    for key in keys:
        weapon, affinity, ash, grip = key.split("|")[:4]
        x = next((r for r in rows_in if (r["weapon"], r.get("affinity", "Keen"), r["ash"], r["grip"])
                  == (weapon, affinity, ash, grip)), None)
        if x is None:
            print(f"{key}: not in {sweep}")
            continue
        x = {**x, **x["cast"]} if x.get("cast") else x
        lead = grip_change if grip == "2H" else 0.0
        print(f"{key}: score {x['score']}")
        for name, sp in ways:
            t1 = time.time()
            prof = rows.profile(x, poise, None, sp)
            t2 = time.time()
            im = AI.multiplier(prof, x["score"])
            w = from_profile(pool, prof, lead)
            t3 = time.time()
            reach = ",".join(f"{q['D']:g}" for q in prof.get("spread") or () if any(q["touch"]))
            print(f"  {name:10} D {prof['D']:5.2f} imult {im:.3f} wnet {net(w):+.3f} combined "
                  f"{combined(im, net(w)):.3f}  profile {t2 - t1:5.2f} s whiff {t3 - t2:4.2f} s"
                  + (f"  share {prof['share']:.3f}, touches at {reach} m" if prof.get("spread") else "")
                  + (f"  escape error {prof['escape_error']}" if prof.get("escape_error") else ""))
            if name == "duel":
                # Per band (`er-mechanics-combo-loadout.set_by_distance`, here with the score as the
                # whole value): [node, credited time, m, score x m].
                net_by = {p[3]: p[2] for p in (w or {}).get("spread") or ()}
                bands[key] = []
                for q in prof["spread"]:
                    if q["reach"]:
                        m = combined(AI.multiplier({"hits": prof["hits"], "sim": q["sim"]}, x["score"]),
                                     net_by.get(q["node"], 1.0))
                        bands[key].append([q["node"], q["w"], round(m, 4), round(x["score"] * m, 2)])
                print("  bands: " + ", ".join(f"{b[0]:g} m t{b[1]:.3f} m{b[2]:.3f}" for b in bands[key]))
    keys_b = list(bands)
    print("duel ash value (score x m per band, `band_set_value`):")
    for i, k in enumerate(keys_b):
        print(f"  {k}: {AI.band_set_value([bands[k]]):.1f}")
        for k2 in keys_b[i + 1:]:
            print(f"    with {k2}: {AI.band_set_value([bands[k], bands[k2]]):.1f}")
    return 0


def moving_selftest(check, f) -> None:
    """The geometry pass of `er-mechanics-ash-interrupt` feeding `whiff`, on toy shapes."""
    hp = AI.B_HIT_HP
    rolls = AI.toy_rolls()
    # Three hits 3 m ahead of a caster who stands still, at f20, f30 and f40: more than one roll's
    # i-frames apart.
    parts = [AI.toy_hit(t, t + 2.0, -3.0, 1.5, h) for h, t in enumerate((20.0, 30.0, 40.0))]
    samples = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    hits = [(20.0, 50.0, 100.0, 0.0), (30.0, 50.0, 100.0, 0.0), (40.0, 50.0, 100.0, 0.0)]

    def table(turn_deg, roll_gate, blocks=False, strafe=False):
        """The toy escape table; the roll checks below run without its strafe plans."""
        esc = AI.escape_table(AI.toy_track(120.0, turn_deg=turn_deg), samples, 3, 3.0, rolls, 0.4,
                              AI.TOY_LIMITS, AI.TOY_RUN, 1.0)
        if not strafe:
            esc.pop("strafe", None)
        return {**esc, "roll_gate": roll_gate, "blocks": blocks}

    def run(esc, bs_hp=1000.0):
        return whiff(f, hits, [], 100.0, 3.0, 40.0, escape=esc, bs_hp=bs_hp)

    # Standing in place the two later hits land after his i-frames and stop his punish: the
    # premise of the model without geometry, which still holds when there is no table.
    flat = whiff(f, hits, [], 100.0, 3.0, 40.0, roll=(13.0, 20.0))
    check(flat["stopped"] == 0.5 and flat["punish_hp"] == 0.0, "rolling in place, the later hits stop the punish")
    # Tracking locked: a side roll takes him out of all three, and he backstabs before my roll gate.
    w = run(table(0.0, 100.0))
    check(abs(w["p_dodge"] - 0.5) < 1e-9 and abs(w["miss"] - 0.5) < 1e-9 and w["late"] == 0.0,
          "a side roll misses every hit of a cast whose tracking is locked")
    check(abs(w["backstab"] - 0.5) < 1e-9 and abs(w["punish_hp"] - 0.5 * 1000.0) < 1e-6
          and abs(w["out"] - (0.5 * 300.0 - 0.5 * 1000.0)) < 1e-6,
          "missed entirely, he backstabs before the roll gate")
    # A roll gate before he gets behind me leaves him his string: two hits before the gate.
    w = run(table(0.0, 40.0))
    check(w["backstab"] == 0.0 and abs(w["punish_hp"] - 0.5 * 2 * hp) < 1e-6,
          "a backstab after my roll gate is no backstab; his string punishes instead")
    w = run(table(0.0, 100.0, blocks=True))
    check(w["backstab"] == 0.0, "a skill that makes me unthrowable cannot be backstabbed")
    # A caster who turns 30 degrees a step brings the later hits onto a side roll, and keeps
    # facing a chaser: no backstab, only a back roll out of reach and his string.
    free = table(30.0, 100.0)
    w = run(free)
    check(free["touch"][1][0][2][10] > 0 and w["backstab"] == 0.0 and w["punish_hp"] > 0.0,
          "tracking that keeps turning catches a side roll and faces the chaser")
    check(abs(run(table(0.0, 100.0))["full"] - 300.0) < 1e-9, "full counts the hits that touch him standing still")
    # Strafe. The slow quantile (30 frames) reacts after the first hit (f20) and cannot roll it,
    # but running round me from the first frame he is 51 degrees round a 3 m circle by f20, clear
    # of hits a caster who cannot turn keeps 3 m ahead, and runs in for the backstab afterwards.
    roll_only, both = run(table(0.0, 100.0)), run(table(0.0, 100.0, strafe=True))
    show = {k: round(both[k], 3) for k in ("p_dodge", "strafe", "backstab", "miss", "out")}
    check(abs(roll_only["p_dodge"] - 0.5) < 1e-9 and abs(both["p_dodge"] - 0.5) < 1e-9
          and abs(both["strafe"] - 0.5) < 1e-9 and abs(both["miss"] - 1.0) < 1e-9
          and abs(both["backstab"] - 1.0) < 1e-6 and abs(both["out"] + 1000.0) < 1e-6,
          f"a reaction too slow to roll strafes out of a locked cast and backstabs the recovery: {show}")
    # The mirror (`er-mechanics-ash-interrupt.toy_mirror`): a 6 m dash with its hit at f18 against
    # a defender with only the same dash to answer it. The fast quantile (10) casts it back or aside
    # and is out of the hit; the slow one (30) is too late and takes it.
    track, mhit, mirror = AI.toy_mirror()
    mesc = AI.escape_table(track, mhit, 1, 6.0, AI.headed([mirror]), 0.4, AI.TOY_LIMITS, AI.TOY_RUN, 1.0)
    mesc = {**{k: v for k, v in mesc.items() if k != "strafe"}, "roll_gate": 100.0, "blocks": False}
    mw = whiff(f, [(18.0, 50.0, 100.0, 0.0)], [], 100.0, 6.0, 40.0, escape=mesc, bs_hp=1000.0)
    check(abs(mw["skill"] - 0.5) < 1e-9 and abs(mw["miss"] - 0.5) < 1e-9 and mw["skill_top"] == "Mirror Dash"
          and mw["p_dodge"] < 1e-9, f"a dash dodges the same dash: skill {mw['skill']:.2f} miss {mw['miss']:.2f}")
    # FP: a skill he cannot pay for is no answer, and he falls back to what is left (nothing here,
    # so he takes the hit); a cheap one is paid for in some share of the fight, never more than all.
    broke = whiff(f, [(18.0, 50.0, 100.0, 0.0)], [], 100.0, 6.0, 40.0, bs_hp=1000.0,
                  escape={**mesc, "fp": [1e6] * len(mesc["moves"])})
    check(broke["skill"] == 0.0 and abs(broke["out"] - 100.0) < 1e-9, "a skill the FP pool cannot pay is never cast")
    shares = [afford(c) for c in (0, 10, 30, 100, 1e6)]
    check(shares[0] == 1.0 and shares[-1] == 0.0 and all(a >= b for a, b in zip(shares, shares[1:])),
          f"the share of the fight a skill is paid for falls with its cost: {[round(s, 3) for s in shares]}")
    half = whiff(f, [(18.0, 50.0, 100.0, 0.0)], [], 100.0, 6.0, 40.0, bs_hp=1000.0,
                 escape={**mesc, "fp": [30.0] * len(mesc["moves"])})
    check(abs(half["skill"] - 0.5 * afford(30.0)) < 1e-9, "the paid share of the fast quantile casts the skill")
    # The fast quantile backstabs from the roll as well (`p_dodge` 0.5 above): on that tie the roll
    # is kept, so a strafe that is no worse for me changes no result the roll model gave.
    # A caster who turns with him keeps the hits on him: strafing buys the slow quantile nothing.
    turn_roll, turn_both = run(table(30.0, 100.0)), run(table(30.0, 100.0, strafe=True))
    check(turn_both["out"] <= turn_roll["out"] + 1e-9 and turn_both["out"] > -1000.0,
          f"tracking that turns keeps a strafer in the cast: {turn_roll['out']:.1f} -> {turn_both['out']:.1f}")
    # `from_spreads` on moving rows: two casts with the same escape tables at each distance (one
    # weapon's affinities) and different damage share each distance's plans and get what
    # `from_profile` gives each alone.
    escs = {"locked": table(0.0, 100.0), "turning": table(30.0, 100.0, strafe=True)}
    spread = [{"D": 3.0, "w": 0.6, "touch": [True] * 3, "reach": True, "escape": "locked"},
              {"D": 3.0, "w": 0.4, "touch": [True] * 3, "reach": True, "escape": "turning"}]

    def cast(dmg):
        return {"hits": [(t, 50.0, dmg, 0.0) for t, *_ in hits], "windows": [], "gate": 100.0, "poise": 40.0,
                "D": 3.0, "share": 1.0, "spread": spread, "_escapes": escs}
    rows = [cast(100.0), cast(140.0), cast(60.0)]
    _PLANS.clear()
    alone = [from_profile(f, p, 2.0) for p in rows]
    _PLANS.clear()
    check(from_spreads(f, rows, 2.0) == alone and alone[0]["geometry"] == 1.0,
          "from_spreads on moving rows sharing their escape tables gives each what from_profile does")
    # The backstep (`er-mechanics-ash-interrupt.backstep_motion` shape: no i-frames, 2.5 m straight
    # back by f10, R1 from f14). Alone it escapes the locked cast by distance for the fast quantile
    # and is no skill; beside the rolls it is one more free entry, compared with them per reply.
    bstep = {"name": "backstep", "path": [(0.0, 0.25 * min(k, 10)) for k in range(40)], "iframes": 0.0,
             "ready": 14.0}

    def esc_with(entries, turn_deg=0.0):
        e = AI.escape_table(AI.toy_track(120.0, turn_deg=turn_deg), samples, 3, 3.0, entries, 0.4,
                            AI.TOY_LIMITS, AI.TOY_RUN, 1.0)
        e.pop("strafe", None)
        return {**e, "roll_gate": 100.0, "blocks": False}
    alone = run(esc_with([bstep]))
    check(abs(alone["backstep"] - 0.5) < 1e-9 and alone["skill"] == 0.0 and abs(alone["p_dodge"]) < 1e-9
          and abs(alone["miss"] - 0.5) < 1e-9 and abs(alone["bstep_beats_roll"] - 0.5) < 1e-9,
          f"a backstep with no i-frames steps out of a locked cast's reach, a free reply, not a skill: "
          f"{ {k: round(alone[k], 3) for k in ('backstep', 'skill', 'p_dodge', 'miss', 'bstep_beats_roll')} }")
    mixed = run(esc_with(rolls + [bstep], 30.0))
    tot = mixed["p_dodge"] + mixed["backstep"] + mixed["skill"]
    check(mixed["skill"] == 0.0 and tot <= 1.0 + 1e-9 and abs(mixed["bstep_open"] - 0.5) < 1e-9
          and mixed["bstep_beats_roll"] + mixed["bstep_ties_roll"] <= mixed["bstep_open"] + 1e-9,
          f"beside the rolls the backstep is compared on the replies either can make: "
          f"{ {k: round(mixed[k], 3) for k in ('p_dodge', 'backstep', 'bstep_open', 'bstep_beats_roll', 'bstep_ties_roll')} }")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--row", action="append", default=[], help="'weapon|affinity|ash|grip', repeatable")
    ap.add_argument("--cache", type=Path, help="er-mechanics-ash-interrupt cache giving stats, armor, D and gate")
    ap.add_argument("--grip-change", type=float, default=7.0, help="telegraph frames of a 2H row's grip change")
    ap.add_argument("--skills", action=argparse.BooleanOptionalAction, default=True,
                    help="with --row: give the defender the sweep's movement skills (--no-skills: rolls and "
                         "strafe only, to time what the skills cost)")
    ap.add_argument("--sweep", type=Path, help="with --row, --cache and --arena-m: the sweep JSON the rows come "
                                               "from (`compare_spread`)")
    ap.add_argument("--arena-m", type=float, help="with --sweep: a duel's starting separation; each row is "
                                                  "compared at its fixed distance and over the duel's spread")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.row and a.cache and a.sweep and a.arena_m:
        return compare_spread(a.row, a.cache, a.sweep, a.arena_m, a.grip_change)
    if a.row and a.cache:
        return report(a.row, a.cache, a.grip_change, a.skills)
    ap.error("--selftest, or --row with --cache (the term is applied by er-mechanics-combo-loadout.py "
             "--whiff-weight)")
    return 2


if __name__ == "__main__":
    sys.exit(main())
