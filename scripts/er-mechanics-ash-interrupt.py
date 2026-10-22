#!/usr/bin/env python3
"""Interrupt risk of an ash of war: how much of a cast is delivered when the opponent answers it.

    python3 scripts/er-mechanics-ash-interrupt.py --skill "Blood Tax" --weapon "Dueling Shield" \
        --affinity Keen --grip 1H --distance 2.5 --poise 88       # one cast, printed per hit
    python3 scripts/er-mechanics-ash-interrupt.py --selftest
    python3 scripts/er-mechanics-ash-interrupt.py --stamp-deps    # regenerate STAMP_DEPS (code stamp)

Used by `er-mechanics-combo-loadout.py --interrupt-weight` (the default): each sweep row's value is
multiplied by `multiplier()`, the expected delivered net value over the undisturbed value. No new
damage or poise model; the pieces are, per cast:

- Engagement distance `D` = `REACH_SHARE` x the ash's max reach (`melee_reach[0]`, a bullet's
  `reach[1]`; steps and buffs `EXCHANGE_DISTANCE_M`). The opponent stands there (`INFERRED`: user,
  2026-10-08, "Impaling Thrust expects you to be at 75% maximum range"). In a duel (`profiles`
  with `arena_m`) the cast is instead valued over the duel's distance spread (`duel_spread`, the
  section by that name below): one simulation per distance with only the hits that touch him
  there, weighted by the share of the duel spent at that distance (`Rows.spread_profile`).
- The opponent (`er-mechanics-interrupt.PoolRows`: the RL 140-160 exchange pool, its weights, B's
  hit frames from B's input, PvP poise with saRate, B's TAE 795 windows and entry i-frames) presses
  attack B at my cast's start plus a reaction: lognormal, median `REACTION_MEDIAN_S`, log sd
  `REACTION_LOG_SD`, plus two `NETWORK_ONE_WAY_S` legs (`er-mechanics-ashes`, ashes-of-war.md
  section 16a), sampled at `REACT_POINTS` quantiles. B's frames are its 2.5 m front contact; when
  `D` is beyond both 2.5 m and B's R1 #1 world reach (`er-mechanics-neutral.pool_reaches`), B first
  closes the gap (`er-mechanics-neutral.arrival`: running or a forward roll, whichever starts the
  attack sooner).
- My hits: the ash's scored hits (`er-mechanics-ash-table`: bullets for a bullet ash, every hit
  otherwise), each at its clip frame plus the weapon's 1H/2H R1 #1 contact delay for a blade hit
  (the proxy `er-mechanics-interrupt._skill_member` uses), PvP poise x saRate, PvP damage as the
  sweep computed it. Heals on hit (AtkParam on-hit SpEffects with negative `changeHpRate` /
  `changeHpPoint`: Blood Tax 1830 / 1831) ride on their hit. My poise is the armor's plus the
  cast's TAE 795 windows (`CSChrToughnessModule` rule of `er-mechanics-interrupt.simulate`).
- B also reads the cast instead of reacting to it (`with_read`): in `1 - REACT_SHARE` of
  exchanges he presses with me, offset 0, the split `er-mechanics-class-roles.PunishCoverage`
  uses for a punish. That is the exchange where both attacks are in their windup together and
  hyperarmor decides who swings through.
- Poise carries between exchanges (`poise_carries`, `carried_states`): it never regenerates
  gradually, and the refill timer (`er-mechanics-interrupt.poise_reset_seconds`, 30 s) outlasts a
  fight window, so both sides start at a uniformly random point of the cycle the other's opening
  hit wears into them.
- One timeline per (pool row, start offset, carried state): hits in time order, simultaneous hits
  both land. A hit of mine that breaks B cancels B's later hits; a hit of B that breaks me cancels
  my later hits. B's hits land on me until the cast's R1 gate (`gate`, the end of my commitment),
  each for `B_HIT_HP` x the open window's PvP HP rate. A broken player opens no riposte
  (`INFERRED`, not traced: a critical needs a parry, a backstab or a stance break, and no player
  stance gauge is modelled anywhere in these scripts).
- Once B breaks me, the gate no longer holds me: the stagger does. His later hits, the rest of his
  string and then the R1 the HKS plays after it, land while the stagger his breaking hit put me in
  holds and his reach still covers me after its knockback, the rule a true combo is judged by
  (section "the stagger" below; user, 2026-10-19: "being broken should cost what the opponent can
  do with it"). Each costs `B_HIT_HP` like any other of his hits (`follow_hp` reports their part).

The R1, L1 and powerstance strings are valued the same way (`ChainTrades`, section below).

    delivered = sum p_i (d_i + heal_i) - E[HP taken] + E[frames saved] x value / 30
    multiplier = max(0, delivered / sum (d_i + heal_i))

`frames saved`: broken at or after my last hit, the gate minus the frame the stagger releases me
(the recovery I no longer sit in), at the row's own value per frame. A step or buff with no damage is worth
P(not broken before its effect frame).

# The escape table (`escape_table`, read by `er-mechanics-ash-whiff.py`)

The other reply, the opponent rolling the cast, depends on where the cast's hits go once both
players move, which no frame count says (user, 2026-10-19: "a seasoned defender would understand
tracking and timing such that when the user activates the ash of war, they dodge at the frame window
needed to make their tracking with the ash of war go in the direction they used to be prior to
rolling"). Each profile of a damaging row carries a key into a table, kept per code stamp in
`ESCAPE_DIR` because it is the same for every affinity and stat block, of one geometry pass with
the opponent standing at `D`:

- My hits are their sampled hit shapes (`er-mechanics-ashes._hit_contacts`: pose samples of each
  window, a bullet's flight, else a shape that touches anyone at the window's start), each sample
  taken relative to my root at that instant (a bullet's to my root at its first sample: it flies on
  once fired). Frames are cast frames: the geometry's real frames plus the lead-in. The contact
  delay the hit frames carry is not added: it stands in for the contact the shapes measure.
- I move by my clip's root motion (`er-hkx-pose` root samples through the TAE 608 play speed), the
  lead-in clip first when the cast has one, and I am locked on: every step I turn toward him by at
  most `er-mechanics-reach.turn_rate_at` (0 under Disable Turning, else the TAE 224 speed, else
  720 deg/s; reach.md section 4, `VERIFIED`), the rule `er-mechanics-backstab.circle` uses. My
  root motion turns with me. His body stops mine: the two push capsules never overlap (`reach`
  `PUSH_CAPSULE_RADIUS`, his body holding its ground is `INFERRED` as there).
- He stands still, then at a start `s` on a one-frame grid (`ESCAPE_STEP` updates) rolls one of
  the four medium-roll directions (`er-mechanics-ashes.roll_motion`, `MEASURED`) relative to where
  I am at `s`, invincible for its first `iframes` frames, or backsteps (`backstep_motion`: the
  medium backstep a000_027000 straight back, never invincible without the Fine Crucible Feather,
  so it escapes only by distance; `BACKSTEP_ANIM`). Two plans per roll: `hold` stays where
  the roll ends (and punishes with his string, `er-mechanics-ash-whiff`), `chase` runs from the
  roll's first R1 frame to `er-mechanics-backstab.TARGET_BEHIND_M` behind me at the locked-on run
  speed and records the first frame the backstab `_start` limits hold (`throw_limits`, `VERIFIED`).
- Or he strafes (`strafe_table`, user 2026-10-19: "a defender who simply walks sideways during a
  55-frame windup is out of the hit, and the long recovery after then gets punished"): from the
  cast's first frame he runs square to the line between us, either way, at the locked-on run speed
  (`er-mechanics-backstab.RUN_SPEED`, `MEASURED` a000_020100, the speed the chase uses and
  `er-mechanics-shields-ranged.STRAFE_SPEED_MPS` holds for a bullet's target), never invincible,
  until a commit frame `c` on the one-frame grid; then hold or chase as after a roll. Already moving
  when the cast starts is `INFERRED`: a defender who waits rather than attacks is circling.
- Or he casts a movement skill (`move_motion`, user 2026-10-19: "every way to move out of the
  hit", two players trading Aspects of the Crucible: Wings miss each other): every skill of the
  sweep rows the ladder allows (`ladder_banned`: no Bloodhound's Step, Quickstep or Raptor of the
  Mists), the attacker's own included. Every clip of a skill is read on its own, a stance's later
  clips after its entry (`skill_moves`), and a clip is an escape only by `escape_reason`: it is
  invincible somewhere (JumpTable 8), or it moves the body two push-capsule radii with no hitbox
  of its own active. A clip with its own hitbox is an attack, its motion up to its last hit the
  lunge into that hit, scored by the interrupt and trade path and not here. Measured 2026-10-19 on
  the heavy-kit sweep's 48 skills: 10 clips pass, among them Blinkbolt (invincible f14-30),
  Unsheathe and Square Off's stance step (40052, 2 m, no hitbox), and the post-hit drift of Wings
  and Lightning Ram; Unsheathe's opening 40050 moves 0.05 m and its slashes 40060/40070 lunge
  2.6 m into their own hits. Each escape is a roll-table entry: its root path per real frame, its
  windows and its first R1 cancel as `ready`, from the same start grid as a roll. Direction is the
  clip's own: he starts facing me and turns toward each `MOVE_HEADINGS` stick heading at the
  clip's TimeAct turn rate not locked on (`er-mechanics-reach.turn_rate_at`, 0 under Disable
  Turning; casting away from me at all means letting go of the lock, `INFERRED`), each frame's
  root motion going the way he then faces; his punish waits for him to turn back
  (`turn_back`). Its FP (`fp`) is charged in `er-mechanics-ash-whiff.afford`. Its own hits on me
  are left out: it is scored as an escape and the punish after it, not as a trade. An entry that at no start up to
  `T` takes him out of a hit is dropped. On any roll or skill path our bodies meet moving and each
  gives half the overlap, so a dash at me stops at my body rather than passing through it.
  Height is not modelled: a leap's airborne frames count only through the TimeAct windows.
- A hit touches when one of its samples is closer to his axis than its contact disc radius and he
  is not invincible then. Stored: the first touch of each hit on him standing still (`t0`) and on
  each plan, the distance between us at `s` + the roll's R1 frame (`gap`), the backstab frame,
  whether the skill's own TimeAct makes me unthrowable (`skill_blocks_backstab`), and my first roll
  frame (`roll_gate`: the clip's roll cancel, on the clock of the row's gate).
"""
from __future__ import annotations

import argparse
import ast
import atexit
import contextlib
import hashlib
import importlib.util
import json
import math
import os
import signal
import statistics
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
CACHE = Path.home() / ".cache/er-build-planner"


def _sib(name):
    key = name.replace("-", "_")
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(key, HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[key] = m
    spec.loader.exec_module(m)
    return m


INTR = _sib("er-mechanics-interrupt")
EXCH, ATK = INTR.EXCH, INTR.ATK
AS = INTR._mod("er-mechanics-ashes")
NEU = _sib("er-mechanics-neutral")


def _backstab():
    """`er-mechanics-backstab`, loaded once. It loads its own copies of the ashes, reach and
    ar-export modules into `sys.modules` under their plain names; the entries there before it are
    put back, so no other module's later lookup lands on its copies."""
    key = "er_mechanics_backstab"
    if key not in sys.modules:
        names = ("er_mechanics_ashes", "er_mechanics_reach", "er_mechanics_ar_export", "er_mechanics_crits")
        before = {n: sys.modules.get(n) for n in names}
        try:
            _sib("er-mechanics-backstab")
        finally:
            for n, m in before.items():
                if m is None:
                    sys.modules.pop(n, None)
                else:
                    sys.modules[n] = m
    return sys.modules[key]

#: Engagement distance as a share of the ash's max reach (user, 2026-10-08). The fixed-`D` model:
#: used when no duel spread is asked for (`profiles(arena_m=None)`, an invasion), and as the
#: in-reach distance the perfect block and the R1/L1 strings (`ChainTrades`) still read.
REACH_SHARE = 0.75
#: Distance for a cast with no reach (steps, buffs): the exchange distance (`INFERRED`).
EXCHANGE_DISTANCE_M = EXCH.STRIKE_DISTANCE_M

# --------------------------------------------------------------------------------------------
# The duel distance spread (`duel_spread`)
#
# User, 2026-10-19: "We need to consider real duel distances as a spread not as fixed gaps between
# players over the duration. Its like a sin wave in terms of distance where they start the
# furthest possible distance." And: "near the end of the duel, the characters rarely get into
# melee distance because they are avoiding death." So the distance between the two duelists is
#
#     d(tau, phi) = lo(tau) + (hi - lo(tau)) x (1 + cos phi) / 2
#
# with `phi` the phase of one approach-and-separate cycle (phi = 0 the start, at `hi`) and `tau`
# in [0, 1] how far the duel has gone. `hi` is the starting separation (`--arena-m`, the duel's
# 16.9 m, `er-weapon-build.DUEL_ARENA_M`: user, 2026-10-18, "Lets assume that in duels, the arena
# sizes are 16.9m", so `INFERRED`, not measured: the duel rules crate, the ladder rules and the
# PvP corpus carry no player positions, and the Royal Colosseum's spawn points are in
# m45_00_00_00.msb.dcx, an Oodle stream no script here decodes). `lo(0)` is contact, two push
# capsules (`contact_m`, `VERIFIED` radius). `lo` rises with the HP the duel has taken, to melee
# range at its end (`late_floor_m`: the opponent pool's weighted median R1 #1 world reach,
# `pool_melee_m`, measured 3.73 m on the RL 140-160 pool on 2026-10-19). The rise is linear in HP
# lost and HP is lost linearly in time (`INFERRED`, the quote above: a duel drinks no crimson flask,
# `er-mechanics-buffs.FIGHT_KINDS`, and its landed hits are an even schedule,
# `ENGAGEMENT_SECONDS` apart), so lo(tau) = lo(0) + (late_floor - lo(0)) x tau.
#
# Uniform phi is the arcsine distribution of distance, heavy at both ends; uniform tau mixes those
# arcsines over the rising floor. A player presses an ash only at distances where it reaches
# (user, 2026-10-19, after uniform casting threw Unsheathe at 10-17 m: "Aren't there occasions when
# its more useful than storm blade?"), so its value is the wave's time share at distances where any
# of its hits touches a standing opponent, times its value cast there with those distances weighted
# by the wave's time (`Rows.spread_profile`). Within that range the press is uniform in time:
# nothing in these scripts gates when a player may press in neutral.
#
# Quadrature (`spread_nodes`): `SPREAD_POINTS` distances at the Gauss-Chebyshev phase midpoints of
# the duel's first cycle (lo(0) to hi), each weighted by the mass the duel's distance spends in the
# cell between the neighbouring phase boundaries. For the stationary wave that mass is exactly
# 1 / `SPREAD_POINTS` per node (the Chebyshev rule); the rising floor moves mass off the near cells.

#: Distances a cast is valued at over a duel (`INFERRED`, a quadrature resolution). Each is one
#: simulation and, where a hit reaches, one escape table, so it sets the cost: 8 puts three nodes
#: inside 4.4 m, where ashes reach.
SPREAD_POINTS = 8
#: Duel-progress points the node weights integrate the rising floor over (no simulation each).
SPREAD_PROGRESS_POINTS = 200


def arcsine_cdf(x, lo: float, hi: float):
    """Share of a cycle the distance spends at or below `x` when it swings as a cosine between
    `lo` and `hi`: 1 - arccos(2 (x - lo) / (hi - lo) - 1) / pi, clipped to [0, 1]."""
    x = np.asarray(x, float)
    if hi <= lo:
        return (x >= lo).astype(float)
    u = np.clip(2.0 * (x - lo) / (hi - lo) - 1.0, -1.0, 1.0)
    return 1.0 - np.arccos(u) / math.pi


def spread_nodes(lo: float, hi: float, n: int = SPREAD_POINTS) -> tuple[np.ndarray, np.ndarray]:
    """(nodes, cell bounds) ascending: the distances at the Chebyshev phase midpoints
    (2i - 1) pi / 2n of the cosine between `lo` and `hi`, and the n + 1 distances at the phase
    boundaries i pi / n around them (`lo` and `hi` the outer two)."""
    mid = lo + (hi - lo) * (1 + np.cos((2 * np.arange(1, n + 1) - 1) * math.pi / (2 * n))) / 2
    edge = lo + (hi - lo) * (1 + np.cos(np.arange(n + 1) * math.pi / n)) / 2
    return np.sort(mid), np.sort(edge)


def spread_weights(lo: float, hi: float, late_floor: float | None = None, n: int = SPREAD_POINTS,
                   progress: tuple[float, float] = (0.0, 1.0), m: int = SPREAD_PROGRESS_POINTS) -> np.ndarray:
    """Weights of `spread_nodes(lo, hi, n)`: the share of the duel's time (`progress` the part of it,
    0 its start, 1 its end) the distance spends in each node's cell, the floor rising from `lo` to
    `late_floor` over the duel (None: it stays at `lo`, the stationary wave). Sums to 1."""
    _, edge = spread_nodes(lo, hi, n)
    w = np.diff(wave_cdf([lo, hi, late_floor, *progress], edge, m))
    return w / w.sum()


def wave_cdf(wave, x, m: int = SPREAD_PROGRESS_POINTS):
    """Share of the duel's time (the part `wave[3]`..`wave[4]` of it) the distance spends at or
    below `x`, `wave` = [lo, hi, late floor or None, progress from, progress to]: the arcsine of
    each cycle mixed over the floor rising from lo to the late floor."""
    lo, hi, floor, a, b = wave
    end = lo if floor is None else floor
    taus = a + (b - a) * (np.arange(m) + 0.5) / m
    if end == lo:
        return arcsine_cdf(x, lo, hi)
    floors = (lo + (end - lo) * taus)[:, None]
    u = np.clip(2.0 * (np.asarray(x, float)[None, :] - floors) / (hi - floors) - 1.0, -1.0, 1.0)
    return np.mean(1.0 - np.arccos(u) / math.pi, 0)


def wave_mass(wave, a: float, b: float) -> float:
    """Share of the duel's time the distance spends in [a, b]."""
    if b <= a:
        return 0.0
    lo_hi = wave_cdf(wave, np.array([a, b], float))
    return float(lo_hi[1] - lo_hi[0])


def wave_mean(wave, a: float, b: float, k: int = 64) -> float:
    """The time-weighted mean distance over [a, b] (midpoint when the wave spends no time there)."""
    x = np.linspace(a, b, k + 1)
    dm = np.diff(wave_cdf(wave, x))
    mid = (x[:-1] + x[1:]) / 2
    return float((dm * mid).sum() / dm.sum()) if dm.sum() > 0 else (a + b) / 2


def covered_cells(wave, cells, reaches, reach: float | None) -> list:
    """Each node's credited time, given which nodes the cast reaches (`reaches`, the geometry's
    standing touch at the node) and the cast's own reach `reach` in m (the sweep's `melee_reach[0]`
    or a bullet's `reach[1]`). `cells` are [node, cell from, cell to]. The cast covers distances up
    to `limit` = the larger of `reach` and the farthest reaching node (no `reach`: that node's cell
    end), so a cell is credited for its part below `limit` rather than all or nothing at its node:
    [(node index, distance, credited time, boundary)]. A reaching node is credited at itself; the
    one cell whose node lies past `limit` but whose start lies inside it is credited at the
    time-weighted mean of its covered part (`boundary` True: the caller measures that distance,
    one extra profile per cast). A node that does not reach with its cell wholly inside `limit`
    (a point-blank miss) gets nothing."""
    far = [i for i, r in enumerate(reaches) if r]
    if reach:
        limit = max(float(reach), max((cells[i][0] for i in far), default=0.0))
    elif far:
        limit = cells[far[-1]][2]
    else:
        return []
    out = []
    for i, (d, a, b) in enumerate(cells):
        top = min(b, limit)
        if reaches[i] and top > a:
            # `limit` is at least every reaching node, so `top` never cuts below the node.
            out.append((i, d, wave_mass(wave, a, top), False))
        elif not reaches[i] and a < limit < d:
            out.append((i, wave_mean(wave, a, limit), wave_mass(wave, a, limit), True))
    return [o for o in out if o[2] > 1e-9]


def contact_m() -> float:
    """Two push capsules (`er-mechanics-reach.PUSH_CAPSULE_RADIUS`, `VERIFIED` InitForPlayer): the
    closest two players' axes get, the escape geometry's own body limit. 0.8 m."""
    return 2 * AS._pose()[1].PUSH_CAPSULE_RADIUS


def duel_spread(hi: float, late_floor: float | None, lo: float | None = None, n: int = SPREAD_POINTS,
                progress: tuple[float, float] = (0.0, 1.0)) -> dict:
    """{'wave': [lo, hi, late floor, progress from, progress to], 'nodes': [[distance, weight, cell
    from, cell to], ...]}: the duel's distances, their time shares and the cells they stand for
    (section above), nodes with no weight dropped; `lo` None is `contact_m`. Rounded, since it is
    part of the cache settings."""
    lo = contact_m() if lo is None else lo
    d, edge = spread_nodes(lo, hi, n)
    w = spread_weights(lo, hi, late_floor, n, progress)
    nodes = [[round(float(x), 2), round(float(y), 6), round(float(a), 4), round(float(b), 4)]
             for x, y, a, b in zip(d, w, edge[:-1], edge[1:]) if y > 1e-9]
    return {"wave": [round(lo, 4), hi, late_floor, *progress], "nodes": nodes}


def duel_spread_for(arena_m: float, opp=None) -> dict:
    """The duel spread every duel valuation shares: `duel_spread` from `arena_m` to contact, the
    floor rising to the melee range (`pool_melee_m`) of the ash rows' pool (`Opponent()`, or `opp`
    when the caller already holds that pool), so the ashes (`profiles`), the strings
    (`ChainTrades`) and the perfect block (`er-mechanics-perfect-block.py`) weigh the same
    distances."""
    return duel_spread(arena_m, pool_melee_m(opp if opp is not None else Opponent()))


def reach_parts(spread, reach: float | None) -> list:
    """[(distance, credited time, node)] of an attack with no escape table over the duel's spread
    (`duel_spread`): the opportunity-share rule `Rows.spread_profile` applies to a cast whose clip
    has no table, every hit touching while the distance is within `reach` (`INFERRED` there too),
    and `covered_cells` crediting each cell for its part inside the reach. A node beyond the reach
    gets nothing: the move is pressed only where it reaches. The times sum to the move's share of
    the duel. Empty with no `reach`; the caller keeps its fixed distance then."""
    if not reach:
        return []
    nodes = spread["nodes"]
    credit = covered_cells(spread["wave"], [n[0:1] + n[2:4] for n in nodes],
                           [n[0] <= reach for n in nodes], reach)
    return [(round(d, 2), w, nodes[i][0]) for i, d, w, _ in credit]


def band_set_value(held) -> float:
    """The duel value of a set holding several ashes, each given as its `by_distance` bands
    [[node, credited time, value there], ...] (`er-mechanics-combo-loadout.set_by_distance`): per
    band the player presses the best ash that reaches there (user, 2026-10-19: "and far away, storm
    blade beats unsheathe"). An ash's credited time in a band is the part of the band from its near
    end up to its reach (`covered_cells`), so the parts are nested: the best ash takes its time,
    the next best only the time beyond it, and so on. A single ash is its own sum of time x value;
    two copies of one ash are worth one."""
    by = {}
    for bands in held:
        for node, c, v in ((b[0], b[1], b[-1]) for b in bands or ()):
            by.setdefault(node, []).append((v, c))
    total = 0.0
    for got in by.values():
        covered = 0.0
        for v, c in sorted(got, reverse=True):
            if v > 0 and c > covered:
                total += v * (c - covered)
                covered = c
    return total


def pool_melee_m(opp) -> float:
    """The opponent pool's weighted median R1 #1 world reach (`Opponent.reach`, `er-mechanics-
    neutral.pool_reaches`), at least the strike distance: melee range, the floor of the duel's
    late cycles."""
    r = np.maximum(EXCH.STRIKE_DISTANCE_M, np.asarray(opp.reach, float))
    o = np.argsort(r)
    c = np.cumsum(np.asarray(opp.w, float)[o])
    return round(float(r[o][np.searchsorted(c, 0.5 * c[-1])]), 2)
#: Reaction quantiles per pool row (the reacted-interrupt setup).
REACT_POINTS = 41
#: HP one of B's hits takes off me: the pool-weighted mean R1 #1 (`er-mechanics-exchange`).
B_HIT_HP = EXCH.OPPONENT_HP_FALLBACK
#: Hits of mine simulated, earliest first.
MAX_MY_HITS = 12
#: The break model, in the profile cache's settings (`profiles`), so a cache made before B's
#: follow-up out of a stagger was charged (`simulate`, the stagger section) is recomputed. 1: none;
#: 2: the follow-up charged, 2026-10-19.
STAGGER_MODEL = 2
#: Hits of B's R1 follow-up kept after his string ends (`stagger_arrays`).
MAX_FOLLOW_HITS = 4
#: Per-hit AtkParam rows and follow-ups of the pool's weapons (`stagger_side`), one file per code
#: stamp and pool stamp.
STAGGER_DIR = CACHE / "interrupt-stagger"
#: Escape tables (module docstring), one file per code stamp: {key: table}.
ESCAPE_DIR = CACHE / "interrupt-escape"
#: The geometry pass moves both players every half real frame, the game's 60 Hz update
#: (`er-mechanics-ashes.DODGE_STEP`).
ESCAPE_STEP = 0.5
#: Roll starts tried after the first touch on a defender standing still: a timing error can put the
#: roll there (`er-mechanics-ash-whiff`), which is then no dodge.
ESCAPE_SPILL = 3
#: Plans `first_touches` places at once: its arrays are (hit samples, plans), and a table with the
#: pool's movement skills holds tens of thousands of plans.
TOUCH_CHUNK = 2048
#: The backstep a defender throws instead of a roll: the medium-load a000_027000, the load the
#: medium roll is taken at (`er-mechanics-neutral.DODGE_ANIMS`). Its only JumpTable 8 window, f0-7,
#: is gated on stateInfo 473, the Fine Crucible Feather (TAE a00; `er-mechanics-talismans`
#: `VERIFIED`), so a plain backstep is never invincible (`er-mechanics-ashes._jt8_unconditional`):
#: it escapes a hit only by its root motion straight back, 2.5 m by the clip's end, first R1 at f14
#: against the roll's 13 i-frames and f20 (measured 2026-10-19).
BACKSTEP_ANIM = 27000
#: Escape-table entries that cost no FP and are no skill: the four rolls and the backstep.
FREE_MOVES = ("roll", "backstep")


def backstep_motion(A=AS) -> list:
    """The backstep as one escape-table entry (`er-mechanics-ashes.evasion_motion`, named
    `backstep`): `iframes` 0, its root path, its first R1 frame as `ready`. Empty when the clip
    cannot be read. `A` is the ashes module whose motion cache to use."""
    m = A.evasion_motion(0, BACKSTEP_ANIM)
    return [{**m, "name": "backstep"}] if m else []
#: Headings a movement skill is cast at, relative to the line to the attacker: the four the medium
#: roll has (`er-mechanics-ashes.ROLL_ANIMS`: forward, back, right, left), so a skill gets no more
#: directions than a roll (`INFERRED`: a defender casts it unlocked toward any stick direction, as he
#: rolls).
MOVE_HEADINGS = (0.0, math.pi, math.pi / 2, -math.pi / 2)
#: Simulations kept across stat blocks, one file per code stamp, opponent pool and armor poise.
#: `simulate` reads my hits' frames and poise, never their damage, so a stat block that keeps a
#: row's timing reuses its simulation: 130 of a cold row's 231 ms (measured 2026-10-14).
SIMS_DIR = CACHE / "interrupt-sims"
#: A file of another stamp is deleted once it has gone this long unwritten.
SIMS_STALE_S = 86400.0


# --------------------------------------------------------------------------------------------
# the code stamp
#
# Every kept file (the profile cache's settings, `ESCAPE_DIR`, `STAGGER_DIR`, `SIMS_DIR`) carries
# a stamp of the code that computed it, so a change to that code recomputes it. The stamp covers
# the code the computation runs, not whole files: each function, method or class named in
# `STAMP_DEPS` as its syntax tree with comments and docstrings dropped (`_stamp_units`), plus the
# module-level statements (the constants) of every script named there. An edit to a comment, a
# docstring, a selftest or a function the profiles never call leaves the stamp as it was; an edit
# to code they run changes it. Until 2026-10-20 it hashed nine whole scripts, so any edit to any of
# them re-simulated every profile (2274 s locally) and the class-roles strings (6-12 min).
#
# Two scopes. `stamp("sim")` keys the kept simulations (`SIMS_DIR`, the ash rows' and
# `ChainTrades`' alike): a simulation depends on `simulate`, what it calls and the opponent's
# arrays, and the arrays enter its file name by `Opponent.fingerprint`. `stamp()` keys the rest
# (profiles, escape tables, stagger sides) over `STAMP_DEPS`, which holds every sim dependency too.
# The profile cache's settings also carry the opponent's fingerprint (`profiles`): the pool and
# sequence caches it is built from are kept by their own scripts, keyed on their own stamps, and a
# warm one runs none of the code that made it, so their data stands in for their code.
#
# The lists are written out and checked by `stamp_selftest`, not trusted: (1) a traced run of real
# rows (`stamp_trace`, `STAMP_TRACE_ROWS`: every function under scripts/ that runs must be
# stamped, with this script's own caches pointed at an empty directory so nothing is skipped by a
# warm read), and (2) a static walk (`stamp_closure`: a bare name of a function or class of the
# same script, a `self.`/`cls.` method of the same class, or a sibling module's function reached
# through a module-level alias, named by a stamped unit must be stamped too), which covers the
# branches the traced rows do not take but a name reaches. A call through a value (`A.skill_tae`
# with `A` a parameter, `self.TAB.A`) only the trace sees. `--stamp-deps` prints both lists
# regenerated from the two, to paste here when the selftest names a missing unit.

#: Units of this script whose code `stamp_trace` and `stamp_closure` do not count: the stamp's
#: own machinery, which runs during a traced profile only because the profile asks for its stamp.
STAMP_TOOLING = ("stamp", "_stamp_units", "_strip_strings", "_is_main_guard", "_binds_only", "_unit_text",
                 "_stamp_guard", "stamp_trace")
#: This script's file name, the one `STAMP_TOOLING` names units of.
SELF_SCRIPT = Path(__file__).name
#: Module-level names of this script left out of its stamped module-level statements: the stamp's
#: own lists and state, which no profile reads (a unit added to a list is stamped by its code).
STAMP_TOOLING_GLOBALS = ("STAMP_TOOLING", "SELF_SCRIPT", "STAMP_TOOLING_GLOBALS", "STAMP_DEPS", "STAMP_SIM_DEPS",
                         "STAMP_TRACE_ROWS", "_UNITS", "_STAMPS", "_GUARD", "_FUNCS")

#: Script -> units whose code the kept profiles, escape tables and stagger sides are computed by
#: (`stamp()`). A unit is a top-level function or class, or `Class.method` (with the class's own
#: statements); the module-level statements of every script listed are stamped as well.
STAMP_DEPS: dict[str, tuple[str, ...]] = {
    'er-builds-embed.py': (
        'active_set', 'equipped', 'from_planner', 'loadout', 'loadout_token', 'slot_pos', 'stats_of', 'tokens'
    ),
    'er-builds-ladder.py': (
        'ash_ban', 'ids', 'table'
    ),
    'er-builds-optimize.py': (
        'Scorer.__init__', '_sibling', '_weights', 'archetype', 'bracket_defender', 'corpus_rows', 'is_pvp',
        'load_profile', 'other_load', 'stat_caps'
    ),
    'er-hks-weapon-category-table.py': (
        'Reader.__init__', 'Reader.i32', 'Reader.take', 'Reader.u32', 'Reader.u64', 'Reader.u8',
        'Table.__init__', 'Table.as_list', 'Unknown.__init__', 'decode', 'parse_main_function',
        'run_main_chunk'
    ),
    'er-hkx-pose.py': (
        'Animation.__init__', 'Animation.local_tracks', 'Animation.root_motion', 'RagdollBody.__init__',
        'Skeleton.__init__', 'Tagfile.__init__', 'Tagfile._inherit', 'Tagfile.item', 'Tagfile.members',
        'Tagfile.objects', 'Tagfile.read', 'Tagfile.type_name', '_Curve.__init__', '_Curve.at',
        '_Packed.__init__', '_Packed.next', '_align', '_attacks_module', '_basis', '_compendium_types',
        '_consumed', '_decode_block', '_find_span', '_forward_kinematics', '_local_pose', '_parse_types',
        '_place', '_qmul', '_qnorm', '_qrot', '_quat', '_quat_threecomp40', '_quat_threecomp48',
        '_read_rotation', '_read_vector', '_section_map', '_sections', '_yaw_quat', 'bone_model_transforms',
        'hkx_path', 'load_animation', 'load_animation_file', 'load_ragdoll', 'load_skeleton', 'pose_model',
        'ragdoll_capsules', 'root_motion'
    ),
    'er-item-name.py': (
        'archive_stamp', 'cache_state', 'game_names', 'is_placeholder', 'load', 'read_fmg'
    ),
    'er-mechanics-ar-export.py': (
        '_load',
    ),
    'er-mechanics-ar.py': (
        'Tables.__init__', 'Tables._ids_named', 'Tables.find_weapon', 'Tables.max_level', 'attack_rating',
        'calc_correct', 'element_multiplier', 'normalise_stats', 'soft_caps', 'stat_multiplier'
    ),
    'er-mechanics-ash-interrupt.py': (
        'ChainTrades.__init__', 'ChainTrades._sim', 'Opponent.__init__', 'Opponent.contact_delay',
        'Rows.__init__', 'Rows._cast', 'Rows._sim', 'Rows._track', 'Rows.armor_poise', 'Rows.cast',
        'Rows.escape', 'Rows.profile', 'Rows.roll_gate', 'Rows.set_movers', 'Rows.spread_profile',
        '_backstab', '_combo', '_enc', '_heal', '_read_sims', '_rot', '_sib', '_stagger_of', '_window_step',
        '_wrap', '_write_sims', 'arcsine_cdf', 'backstep_motion', 'carried_states', 'cast_track',
        'clip_motion', 'clip_track', 'contact_m', 'covered_cells', 'duel_spread', 'escape_reason',
        'escape_table', 'first_touches', 'headed', 'heading', 'ladder_banned', 'play', 'poise_carries',
        'pool_melee_m', 'reaction_frames', 'sim_key', 'simulate', 'skill_moves', 'spread_nodes',
        'spread_weights', 'stagger_arrays', 'stagger_side', 'strafe_table', 'turn_back', 'wave_cdf',
        'wave_mass', 'wave_mean', 'with_read', 'with_stagger'
    ),
    'er-mechanics-ash-levers.py': (
        'Levers.__init__', 'Levers.cast', '_load'
    ),
    'er-mechanics-ash-stats.py': (
        'AshScorer.__init__', 'AshScorer.hits', '_mod'
    ),
    'er-mechanics-ash-table.py': (
        'Table.__init__', 'Table._armor', 'Table.set_weapon', 'Table.travel', '_disengage', '_sibling',
        'window_defender'
    ),
    'er-mechanics-ashes.py': (
        '<class>Opponents', 'AshTables.__init__', 'AshTables._mode', 'AshTables.arts_name',
        'AshTables.effect_kind', 'AshTables.find_arts', 'AshTables.find_weapon', 'AshTables.fp_cost',
        'AshTables.is_buff', 'AshTables.marker_ids', 'AshTables.speffect_summary', 'WeaponContext.__init__',
        'WeaponContext.multiplier', '_bullet_damages_self', '_frames', '_hit_contacts', '_hit_contacts_raw',
        '_jt8_unconditional', '_load', '_opening', '_pose', '_skill_profile', '_unit', 'anim_actions',
        'anim_recovery', 'bullet_buffs', 'bullet_damages', 'bullet_flight', 'bullet_harms', 'bullet_tree',
        'contact_geometry', 'ctx_grip', 'diff_category', 'evasion_motion', 'fly_tree', 'hit_attack',
        'improves', 'is_two_handed', 'lead_in', 'lead_in_hyperarmor', 'main_anim', 'no_fp_twins', 'phys_type',
        'plain', 'pvp_damage', 'resolve_judge', 'roll_motion', 'skill_hits', 'skill_profile', 'skill_tae',
        'stance_buttons', 'stance_windup', 'sword_arts_categories', 'weapon_diff_category'
    ),
    'er-mechanics-attacks.py': (
        'Regulation.__init__', 'Regulation.find_weapon', 'Regulation.resolve_behavior_id', '_bound',
        '_early_interval', '_first_open', '_load', '_reach', '_tidy', 'attack_numbers', 'clip_frame',
        'clip_length', 'clip_to_real', 'hit_records', 'hkx_duration', 'hkx_path', 'motion_category',
        'real_frame', 'recovery_details', 'recovery_windows', 'release_lead_in', 'resolve_events',
        'resolve_phys_type', 'slot_animation', 'tae_animations', 'tae_details', 'uses_stealth_attack',
        'weapon_attacks'
    ),
    'er-mechanics-backstab.py': (
        '_mod', 'skill_blocks_backstab', 'throw_limits'
    ),
    'er-mechanics-buffs.py': (
        '<class>Buffs', '_effect_fields', '_fights', '_implied_spells', '_mod', '_names', '_offensive',
        '_pairs', '_t_context', '_t_expected', '_t_regen', '_t_rows', '_t_sources', '_t_stacking',
        'ar_stat_ratio', 'build_regen', 'drinks_needed', 'engaged', 'f32', 'fight_hits', 'fight_kind',
        'fight_mix', 'fight_points', 'hits_mean', 'recast_plan', 'recast_points', 'sustain_factor',
        'tag_shares'
    ),
    'er-mechanics-combo.py': (
        '<class>_ArCache', 'Model.__init__', 'Model.react', 'Stagger.__call__', 'Stagger.__init__', '_events',
        '_load', 'animated_category', 'follow_key', 'pvp_module', 'substart_lead'
    ),
    'er-mechanics-crits.py': (
        'Tables.__init__', '_load'
    ),
    'er-mechanics-defense.py': (
        '<class>Tables', 'damage', 'defense_curve', 'f32'
    ),
    'er-mechanics-disengage.py': (
        '<class>Paired', '_atk', '_mod', '_reach', '_sibling', 'clip', 'first_open', 'windows'
    ),
    'er-mechanics-exchange.py': (
        '<class>Pool', '<class>RegenProfile', 'SaRates.__call__', 'SaRates.__init__', '_combo',
        '_corpus_builds', '_fallback_delay', '_has_contact', '_reach_module', '_right_hand', '_sibling',
        '_source_stamp', 'attack_profile', 'first_hit', 'follow_up', 'hyper_windows', 'is_pvp',
        'opponent_pool', 'plain_name', 'stamina_total', 'strike_frame', 'weapon_ids'
    ),
    'er-mechanics-frame-advantage.py': (
        'Tables.__init__', '_frame', '_load', 'clip_timing', 'common_tae', 'ezstate_windows', 'hit_level',
        'reaction', 'reaction_level', 'remap'
    ),
    'er-mechanics-infusions.py': (
        '<class>Builder', '_mod'
    ),
    'er-mechanics-interrupt.py': (
        'Delays.__call__', 'Delays.__init__', 'PoolRows.__init__', '_a00', '_hits_array', '_mod',
        '_pad_invuln', '_pad_windows', '_sibling', '_slot_member', '_source_stamp', 'chip_states',
        'entry_table', 'iframes', 'jump_entry', 'load_cache', 'poise_reset_seconds', 'resolve',
        'seq_cache_path'
    ),
    'er-mechanics-jump.py': (
        '_atk', '_jump_events', '_mod', '_windows', 'takeoff'
    ),
    'er-mechanics-neutral.py': (
        '_mod', '_sibling', '_tool', 'arrival', 'dodge_tools', 'frames_per_metre', 'locomotion',
        'pool_reaches', 'tool_from_motion'
    ),
    'er-mechanics-reach.py': (
        'Reach.__init__', '_anibnd_file', '_azimuth', '_body_frame', '_capsule_points', '_carry', '_clip',
        '_clip_time_before', '_flver_string', '_frame', '_hkx_bind', '_load', '_merge', '_meridional_moment',
        '_pose_module', '_qconj', '_qrot', '_root_peak_forward', '_rot_axis', '_shape_left',
        '_shape_world_points', '_turn_integral', '_turn_lockin', 'attached_dummies', 'attack_reach',
        'blade_sweep', 'body_dummies', 'body_shape_points', 'contact_centre_distance', 'contact_disc_radius',
        'coverage_factor', 'defender_capsules', 'defender_hurtbox', 'defender_profile', 'dummy_positions',
        'flver_bone_to_model', 'footprint', 'front_contact_times', 'hit_shapes', 'hkx_source', 'read_flver',
        'real_seconds', 'resolve_clip', 'rigged_shape_points', 'shape_extent', 'skill_reach', 'speed_at',
        'speed_windows', 'sweep_coverage', 'swing_shape', 'tae_imports', 'turn_details', 'turn_rate_at',
        'turn_windows', 'weapon_anibnd_dirs', 'weapon_attached', 'weapon_clip', 'weapon_dummies',
        'weapon_flver_path', 'weapon_rig', 'weapon_rig_pose', 'window_points', 'world_reach'
    ),
    'er-mechanics-resources.py': (
        'Model.__init__', 'Model._by_name', 'Model.burden', 'Model.rate', 'Model.speffects_for', 'f32'
    ),
    'er-mechanics-weapon-card.py': (
        '_mod',
    ),
    'er-param-read.py': (
        '_index_defs', '_item_name', '_smithbox_names', 'layout', 'load', 'param_bytes', 'param_type_of',
        'paramdef', 'row_names', 'row_stride', 'rows'
    ),
    'er-tae-event-scan.py': (
        '<class>BadTae', '_i32', '_i64', 'parse'
    ),
    'er_corpus.py': (
        'iter_rows', 'kept'
    ),
    'regulation-params.py': (
        '<class>ZstdUnavailable', 'bnd4_entries', 'dcx_unpack', 'decrypt', 'require_zstd'
    ),
}
#: Script -> units the kept simulations are computed by (`stamp("sim")`).
STAMP_SIM_DEPS: dict[str, tuple[str, ...]] = {
    'er-mechanics-ash-interrupt.py': (
        'ChainTrades.__init__', 'ChainTrades._sim', 'Rows._sim', '_combo', '_read_sims', '_sib',
        '_stagger_of', '_window_step', 'carried_states', 'poise_carries', 'sim_key', 'simulate'
    ),
    'er-mechanics-attacks.py': (
        '_load',
    ),
    'er-mechanics-exchange.py': (
        '_sibling',
    ),
    'er-mechanics-interrupt.py': (
        '_mod', '_sibling', 'chip_states', 'poise_reset_seconds'
    ),
    'er-mechanics-neutral.py': (
        '_mod', '_sibling', '_tool', 'arrival', 'dodge_tools', 'frames_per_metre', 'locomotion',
        'tool_from_motion'
    ),
    'er-param-read.py': (
        '_index_defs', 'layout', 'load', 'param_bytes', 'param_type_of', 'paramdef', 'row_stride', 'rows'
    ),
}

_UNITS: dict = {}
_STAMPS: dict = {}


def _strip_strings(tree) -> None:
    """Drop every statement of `tree` that is a bare string: docstrings, and string literals used
    as comments. They do not run, so an edit to one must not move the stamp. A body left empty
    gets a `pass`."""
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            body = getattr(node, field, None)
            if not (isinstance(body, list) and body and isinstance(body[0], ast.stmt)):
                continue
            kept = [s for s in body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
                                            and isinstance(s.value.value, str))]
            setattr(node, field, kept or [ast.Pass()])


def _is_main_guard(s) -> bool:
    """Whether module-level statement `s` is `if __name__ == "__main__":`, which does not run when
    the script is loaded as a module, so it is no part of the stamped module-level statements."""
    t = getattr(s, "test", None) if isinstance(s, ast.If) else None
    return (isinstance(t, ast.Compare) and isinstance(t.left, ast.Name) and t.left.id == "__name__"
            and len(t.comparators) == 1 and isinstance(t.comparators[0], ast.Constant)
            and t.comparators[0].value == "__main__")


def _binds_only(s, names) -> bool:
    """Whether module-level statement `s` is an assignment whose every target is one of `names`."""
    if isinstance(s, ast.Assign):
        targets = s.targets
    elif isinstance(s, ast.AnnAssign):
        targets = [s.target]
    else:
        return False
    return all(isinstance(t, ast.Name) and t.id in names for t in targets)


def _stamp_units(name: str, root: Path | None = None) -> dict:
    """{unit: (text, nodes)} of script `name` under `root` (`HERE`): `<module>` its module-level
    statements, each top-level function and class and each method (`Class.method`) its tree, and
    `<class>Class` a class's own statements without its methods. `text` is `ast.dump` with
    docstrings dropped (`_strip_strings`) and no line numbers, so comments, docstrings and moved
    lines leave it unchanged. Kept per file path and content."""
    path = (root or HERE) / name
    data = path.read_bytes()
    key = (str(path), hashlib.sha1(data).hexdigest())
    if key not in _UNITS:
        tree = ast.parse(data, str(path))
        _strip_strings(tree)
        units, top = {}, []

        def put(q, nodes):
            old = units.get(q, ("", []))
            units[q] = (old[0] + "".join(ast.dump(n) for n in nodes), old[1] + list(nodes))

        for s in tree.body:
            if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)):
                put(s.name, [s])
            elif isinstance(s, ast.ClassDef):
                put(s.name, [s])
                own = [*s.bases, *s.keywords, *s.decorator_list]
                for b in s.body:
                    if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        put(f"{s.name}.{b.name}", [b])
                    else:
                        own.append(b)
                put(f"<class>{s.name}", own)
            elif not _is_main_guard(s) and not (name == SELF_SCRIPT and _binds_only(s, STAMP_TOOLING_GLOBALS)):
                top.append(s)
        put("<module>", top)
        _UNITS[key] = units
    return _UNITS[key]


def _unit_text(name: str, unit: str, root: Path | None = None) -> str:
    """The stamped text of `unit` of script `name`: a method's with its class's own statements."""
    u = _stamp_units(name, root)
    if unit not in u:
        raise KeyError(f"stamp: {name} has no {unit!r} (renamed? update STAMP_DEPS: --stamp-deps)")
    cls = f"<class>{unit.split('.')[0]}" if "." in unit else None
    return (u[cls][0] if cls else "") + u[unit][0]


def stamp(scope: str = "profile", deps: dict | None = None, root: Path | None = None) -> str:
    """The code stamp (section above) of `scope` 'profile' (`STAMP_DEPS`) or 'sim'
    (`STAMP_SIM_DEPS`), or of `deps` read under `root`. Kept per process while no listed file's
    content changes."""
    deps = deps if deps is not None else STAMP_SIM_DEPS if scope == "sim" else STAMP_DEPS
    root = root or HERE
    files = [(name, hashlib.sha1((root / name).read_bytes()).hexdigest()) for name in sorted(deps)]
    key = (scope, str(root), tuple(files), repr(sorted((k, tuple(v)) for k, v in deps.items())))
    if key not in _STAMPS:
        h = hashlib.sha256(scope.encode())
        for name in sorted(deps):
            h.update(f"\0{name}\0".encode())
            h.update(_stamp_units(name, root)["<module>"][0].encode())
            for unit in sorted(deps[name]):
                h.update(f"\0{unit}\0".encode())
                h.update(_unit_text(name, unit, root).encode())
        _STAMPS[key] = h.hexdigest()[:16]
    return _STAMPS[key]


def _unit_of(name: str, qualname: str) -> str:
    """The unit (`_stamp_units`) a code object of script `name` with `co_qualname` belongs to: a
    nested function or comprehension its enclosing top-level function or method, a class body or
    class-level lambda its class's own statements, module-level code `<module>`."""
    q = qualname.split(".<locals>", 1)[0]
    parts = q.split(".")
    if parts[0].startswith("<"):
        return "<module>"
    if len(parts) > 1 and parts[1].startswith("<"):
        return f"<class>{parts[0]}"
    if len(parts) == 1 and f"<class>{parts[0]}" in _stamp_units(name):
        return f"<class>{parts[0]}"
    return ".".join(parts[:2])


def stamp_covers(deps: dict, name: str, unit: str) -> bool:
    """Whether `deps` stamps `unit` of script `name`: listed itself, its class listed whole, or (a
    class's own statements) any method of it listed. Module-level statements are stamped with any
    listed script."""
    have = deps.get(name)
    if have is None:
        return False
    if unit == "<module>" or unit in have:
        return True
    if unit.startswith("<class>"):
        cls = unit[len("<class>"):]
        return any(u == cls or u.startswith(cls + ".") for u in have)
    return "." in unit and unit.split(".")[0] in have


def _script_modules() -> dict:
    """{script file name: [loaded module objects]} of the sibling scripts in `sys.modules` (one
    script can be loaded twice: `_backstab` keeps its own copies)."""
    out = {}
    for m in list(sys.modules.values()):
        f = getattr(m, "__file__", None)
        if f and Path(f).resolve().parent == HERE:
            out.setdefault(Path(f).name, []).append(m)
    return out


_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def _scope_names(fn) -> set:
    """The names local to function `fn`: its parameters and every name its own body binds
    (assignment, loop and `with` targets, `except ... as`, imports, nested `def`/`class`),
    less those it declares `global` or `nonlocal`. Nested functions' bodies are their own."""
    a = fn.args
    names = {x.arg for x in (*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg) if x}
    glob = set()
    todo = list(fn.body) if isinstance(fn.body, list) else [fn.body]
    while todo:
        n = todo.pop()
        if isinstance(n, (ast.Global, ast.Nonlocal)):
            glob |= set(n.names)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, ast.Lambda):
            pass
        else:
            if isinstance(n, ast.Name) and not isinstance(n.ctx, ast.Load):
                names.add(n.id)
            elif isinstance(n, ast.ExceptHandler) and n.name:
                names.add(n.name)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                names |= {(x.asname or x.name).split(".")[0] for x in n.names}
            elif isinstance(n, (ast.MatchAs, ast.MatchStar)) and n.name:
                names.add(n.name)
            todo.extend(ast.iter_child_nodes(n))
    return names - glob


def _code_refs(node, scope: frozenset = frozenset()):
    """(expression, local names) for every global name load (`ast.Name` not local where it
    stands) and every attribute access in `node`, scope by scope (`_scope_names`). Annotations are
    skipped: an annotation names a type and runs none of its code, so `opp: Opponent` on
    `simulate` does not make `Opponent.__init__` a dependency of it."""
    todo = [(node, scope)]
    while todo:
        n, sc = todo.pop()
        if isinstance(n, _FUNCS):
            a = n.args
            todo += [(d, sc) for d in (*a.defaults, *(d for d in a.kw_defaults if d))]
            if not isinstance(n, ast.Lambda):
                todo += [(d, sc) for d in n.decorator_list]
            inner = sc | _scope_names(n)
            todo += [(b, inner) for b in (n.body if isinstance(n.body, list) else [n.body])]
            continue
        if isinstance(n, ast.Name):
            if isinstance(n.ctx, ast.Load) and n.id not in sc:
                yield n, sc
            continue
        if isinstance(n, ast.Attribute):
            yield n, sc
        for field, value in ast.iter_fields(n):
            if field in ("annotation", "returns", "type_params"):
                continue
            if isinstance(value, ast.AST):
                todo.append((value, sc))
            elif isinstance(value, list):
                todo += [(v, sc) for v in value if isinstance(v, ast.AST)]


def stamp_closure(deps: dict, why: dict | None = None) -> dict:
    """`deps` plus every unit a stamped unit names statically (section above), to a fixpoint:
    bare names of the same script's functions and classes (a class by its own statements and
    `__init__`), `self.x`/`cls.x` of a method of the same class, and `ALIAS.x` / `ALIAS.SUB.x`
    where `ALIAS` is a module-level name bound to a sibling script's module (resolved on the
    modules loaded now, `_script_modules`); a constant reached that way stamps that script's
    module-level statements. Units in `STAMP_TOOLING` are left out. `why`, when given, is filled
    with {(script, unit): (script, unit) that named it}."""
    mods = _script_modules()
    out = {k: set(v) for k, v in deps.items()}
    todo = [(n, u) for n, us in deps.items() for u in us] + [(n, "<module>") for n in deps]
    why = {} if why is None else why
    src = [None]

    def file_of(m):
        f = getattr(m, "__file__", None)
        return Path(f).name if f and Path(f).resolve().parent == HERE else None

    def add(name, unit):
        if name == SELF_SCRIPT and unit in STAMP_TOOLING:
            return
        if name not in out:
            out[name] = set()
            todo.append((name, "<module>"))
            why.setdefault((name, "<module>"), src[0])
        if unit is None or stamp_covers({name: tuple(out[name])}, name, unit):
            return
        out[name].add(unit)
        todo.append((name, unit))
        why.setdefault((name, unit), src[0])

    while todo:
        name, unit = todo.pop()
        src[0] = (name, unit)
        units = _stamp_units(name)
        if unit not in units:
            continue
        g = {}
        for m in mods.get(name, []):
            g.update({k: v for k, v in vars(m).items() if isinstance(v, type(sys))})
        cls = unit.split(".")[0] if "." in unit else None
        nodes = list(units[unit][1]) + (list(units.get(f"<class>{cls}", ("", []))[1]) if cls else [])

        def resolve(e, sc):
            """The sibling module an expression names, or None."""
            if isinstance(e, ast.Name):
                m = None if e.id in sc else g.get(e.id)
                return m if m is not None and file_of(m) else None
            if isinstance(e, ast.Attribute):
                m = resolve(e.value, sc)
                sub = getattr(m, e.attr, None) if m is not None else None
                return sub if isinstance(sub, type(sys)) and file_of(sub) else None
            return None

        for n in nodes:
            for e, sc in _code_refs(n):
                if isinstance(e, ast.Name):
                    if e.id in units and not e.id.startswith("<"):
                        if f"<class>{e.id}" in units:
                            add(name, f"<class>{e.id}")
                            if f"{e.id}.__init__" in units:
                                add(name, f"{e.id}.__init__")
                        else:
                            add(name, e.id)
                elif isinstance(e, ast.Attribute):
                    if cls and isinstance(e.value, ast.Name) and e.value.id in ("self", "cls"):
                        if f"{cls}.{e.attr}" in units:
                            add(name, f"{cls}.{e.attr}")
                        continue
                    m = resolve(e.value, sc)
                    if m is None:
                        continue
                    other = file_of(m)
                    ou = _stamp_units(other)
                    if e.attr in ou and f"<class>{e.attr}" in ou:
                        add(other, f"<class>{e.attr}")
                        if f"{e.attr}.__init__" in ou:
                            add(other, f"{e.attr}.__init__")
                    elif e.attr in ou:
                        add(other, e.attr)
                    else:
                        add(other, None)
    return {k: tuple(sorted(u for u in v if not u.startswith("<class>")
                            or not any(w.startswith(u[len("<class>"):] + ".") for w in v)))
            for k, v in sorted(out.items())}


#: The rows `stamp_trace` profiles: a melee ash with a stance button (Unsheathe), a two-handed
#: melee ash, a bullet ash and a buff, so the hit, bullet, utility, stance and grip branches of
#: `Rows._cast` and `simulate` all run.
STAMP_TRACE_ROWS = (
    {"ash": "Unsheathe", "weapon": "Uchigatana", "affinity": "Keen", "grip": "1H", "cat": "non-bullet",
     "gate": None, "melee_reach": [4.1], "reach": None, "last": 0, "stance": "r1"},
    {"ash": "Lion's Claw", "weapon": "Claymore", "affinity": "Heavy", "grip": "2H", "cat": "non-bullet",
     "gate": None, "melee_reach": None, "reach": None, "last": 0},
    {"ash": "Glintblade Phalanx", "weapon": "Longsword", "affinity": "Magic", "grip": "1H", "cat": "bullet",
     "gate": None, "melee_reach": None, "reach": [0, 8.0], "last": 0},
    {"ash": "Determination", "weapon": "Longsword", "affinity": "Standard", "grip": "1H", "cat": "utility",
     "gate": None, "melee_reach": None, "reach": None, "last": 40},
)

_GUARD: dict = {}


def _stamp_guard(event, args) -> None:
    """Audit hook of `stamp_trace`: once armed, refuse any write, rename or delete under `CACHE`,
    so a traced run (a selftest) leaves the planner's cache as it found it. Stays armed for the
    rest of the process, which covers writes a module defers to exit."""
    root = _GUARD.get("root")
    if root is None:
        return
    paths, write = [], False
    if event == "open":
        path, mode, flags = (list(args) + [None, None, None])[:3]
        paths = [path]
        write = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            mode is None and isinstance(flags, int) and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT)))
    elif event in ("os.rename", "os.remove", "os.rmdir", "os.utime", "os.truncate", "os.mkdir", "shutil.rmtree"):
        paths, write = list(args[:2]), True
    if not write:
        return
    for p in paths:
        if isinstance(p, (str, bytes, os.PathLike)) and os.path.abspath(os.fsdecode(p)).startswith(root):
            _GUARD["refused"].append(f"{event} {os.fsdecode(p)}")
            raise PermissionError(f"stamp_trace: a traced run does not write the planner cache ({os.fsdecode(p)})")


def stamp_trace(rows=STAMP_TRACE_ROWS, log=print) -> dict:
    """{'profile': {(script, unit)}, 'sim': {(script, unit)}, 'refused': [...], 'errors': [...]}:
    the units under scripts/ whose code runs while an opponent is built and `rows` are profiled
    over a duel spread and at a fixed distance ('profile'), and while one cast's hits are
    simulated by `Rows._sim` and `ChainTrades._sim`, poise carried and not ('sim'). Measured with
    `sys.monitoring` (each code object reported once). This script's caches point at an empty
    directory for the run, so every profile is computed, and no write under `CACHE` happens inside
    it (`_stamp_guard`). The opponent and its melee reach are first built in a separate process:
    the opponent pool and the pool's neutral reach are cached under names carrying a hash of their
    sources, so after any edit to them the files are missing, and the traced build would otherwise
    write them and be refused. That build makes the writes any run makes, and it leaves the traced
    one reading them. It runs in another process so the modules it imports are still first
    imported inside the trace, where their class bodies are counted."""
    import subprocess
    import tempfile
    warm = ("import importlib.util, sys; s = importlib.util.spec_from_file_location('warm', sys.argv[1]); "
            "m = importlib.util.module_from_spec(s); s.loader.exec_module(m); m.pool_melee_m(m.Opponent())")
    subprocess.run([sys.executable, "-c", warm, str(Path(__file__).resolve())], check=True,
                   stdout=subprocess.DEVNULL)
    g = globals()
    if "refused" not in _GUARD:
        sys.addaudithook(_stamp_guard)
        _GUARD["refused"] = []
    _GUARD["root"] = os.path.abspath(CACHE)
    mon = sys.monitoring
    tool = next(i for i in range(6) if mon.get_tool(i) is None)
    mon.use_tool_id(tool, "stamp-trace")
    seen = {"profile": set(), "sim": set()}
    cur = {"set": seen["profile"]}
    prefix = str(HERE) + os.sep

    def start(code, offset):
        f = code.co_filename
        if f.startswith(prefix):
            cur["set"].add((Path(f).name, code.co_qualname))
        return mon.DISABLE

    saved = {k: g[k] for k in ("SIMS_DIR", "ESCAPE_DIR", "STAGGER_DIR")}
    saved_esc = dict(_ESC)
    errors = []
    with tempfile.TemporaryDirectory() as d:
        g.update(SIMS_DIR=Path(d) / "sims", ESCAPE_DIR=Path(d) / "esc", STAGGER_DIR=Path(d) / "stag")
        _ESC.clear()
        mon.register_callback(tool, mon.events.PY_START, start)
        mon.set_events(tool, mon.events.PY_START)
        try:
            opp = with_read(Opponent())
            rows_ = Rows(opp, {"str": 18, "dex": 80, "int": 7, "fth": 8, "arc": 9},
                         ["Verdigris Gauntlets", "Verdigris Armor", "Verdigris Greaves"], 2292.5,
                         carry=poise_carries())
            rows_.set_movers(list(rows))
            poise = rows_.armor_poise()
            spread = duel_spread(20.0, pool_melee_m(opp))
            casts = []
            for x in rows:
                for kw in ({"spread": spread}, {"distance": 2.5}):
                    r = rows_.profile(x, poise, **kw)
                    if r.get("escape_error"):
                        errors.append(f"{x['ash']}: {r['escape_error']}")
                casts.append(rows_.cast(x))
            mon.restart_events()
            cur["set"] = seen["sim"]
            for carry in (False, True):
                for hits, windows, fx, gate in casts:
                    if not hits:
                        continue
                    rows_.carry, rows_._memo = carry, {}
                    rows_._sim(hits, windows, 2.5, gate, fx, poise)
                    ChainTrades(opp, poise, carry)._sim(hits, windows, gate, 2.5)
        finally:
            mon.set_events(tool, 0)
            mon.register_callback(tool, mon.events.PY_START, None)
            mon.free_tool_id(tool)
            g.update(saved)
            _ESC.clear()
            _ESC.update(saved_esc)
    # Logged only now: `log` is the caller's code, and inside the traced run it would count.
    log(f"stamp trace: {len(rows)} rows profiled")
    out = {k: {(n, _unit_of(n, q)) for n, q in v} for k, v in seen.items()}
    out = {k: {(n, u) for n, u in v if not (n == SELF_SCRIPT and u in STAMP_TOOLING)} for k, v in out.items()}
    return {**out, "refused": list(_GUARD["refused"]), "errors": errors}


def deps_literal(label: str, d: dict, width: int = 110) -> str:
    """`d` {script: units} as the Python source of `label`, one script per entry, wrapped."""
    import textwrap
    out = [f"{label}: dict[str, tuple[str, ...]] = {{"]
    for n, us in d.items():
        body = ", ".join(repr(u) for u in us) + ("," if len(us) == 1 else "")
        out.append(f"    {n!r}: (")
        out += textwrap.wrap(body, width, initial_indent=" " * 8, subsequent_indent=" " * 8,
                             break_long_words=False, break_on_hyphens=False)
        out.append("    ),")
    out.append("}")
    return "\n".join(out)


def stamp_deps_report(log=print) -> int:
    """`--stamp-deps`: the two lists regenerated (traced units, then `stamp_closure`), printed as
    the literals to paste over `STAMP_DEPS` and `STAMP_SIM_DEPS`."""
    t = stamp_trace(log=log)

    def lists(units):
        d = {}
        for n, u in units:
            if u != "<module>":
                d.setdefault(n, set()).add(u)
            else:
                d.setdefault(n, set())
        return stamp_closure({k: tuple(v) for k, v in d.items()})

    sim = lists(t["sim"])
    prof = lists(t["profile"] | {(n, u) for n, us in sim.items() for u in us} | {(n, "<module>") for n in sim})
    for label, d in (("STAMP_DEPS", prof), ("STAMP_SIM_DEPS", sim)):
        print(deps_literal(label, d))
    log(f"stamp deps: traced {len(t['profile'])} profile / {len(t['sim'])} sim units; after the static walk "
        f"profile {sum(map(len, prof.values()))} units in {len(prof)} scripts, "
        f"sim {sum(map(len, sim.values()))} in {len(sim)}; refused writes {t['refused']}; "
        f"escape errors {t['errors']}")
    return 0


def reaction_frames(points: int = REACT_POINTS, median: float = AS.REACTION_MEDIAN_S) -> np.ndarray:
    nd = statistics.NormalDist()
    return np.array([(median * math.exp(AS.REACTION_LOG_SD * nd.inv_cdf((k + 0.5) / points))
                      + 2 * AS.NETWORK_ONE_WAY_S) * AS.TAE_FPS for k in range(points)])


# --------------------------------------------------------------------------------------------
# the stagger: what B does with a break of mine
#
# A hit of B's that breaks my poise plays the stagger its damage level picks
# (`er-mechanics-combo.Stagger`: `W_DamageLv<n>`, escape at the earlier of the DamageCount 1 roll
# gate and the guard, `TAE`) and pushes me its `knockbackDist`. B keeps pressing his string at its
# cancels, and once it ends the R1 the HKS plays after its last attack (`er-mechanics-combo
# .follow_key`: R1 #n -> #n+1, jump R1 -> R1 #2, a movement attack or R2 -> R1 #2 through SubStart).
# Each of his later hits lands while the stagger holds me, as a true combo is judged
# (`break_follow_p`: before the escape frame, a tie at even odds) and while the knockback leaves me
# inside his reach (`follow_reaches`, his R1 #1 world reach, `INFERRED` for every hit of his
# string). A follow-up hit that breaks me again (poise refills at a break, `CSChrToughnessModule`)
# starts a new stagger from its own frame; one that does not leaves me in the first. Once a hit
# falls past the escape I am free and his string is over. My cast's gate no longer applies after a
# break: the stagger replaced my recovery, and the frames saved run to the escape, not the break.


def _combo():
    return _sib("er-mechanics-combo")


def stagger_side(pool_raw: dict, reg=None, cache: bool = True) -> dict:
    """{'weapons': {'<name>|one|both': {'rows': {member: [[hit frame, AtkParam row], ...]},
    'follow': {member: [next member, lead-in frames]}}}, 'atk': {row: [escape frames on a first,
    second, third and fourth break, knockback m]}}
    for every weapon of the pool (`er-mechanics-interrupt._slot_member` `atk_rows`,
    `er-mechanics-combo.follow_key`, `substart_lead`, `Stagger`). The sequence cache keeps no
    AtkParam row, so it is read here once and kept beside it (`STAGGER_DIR`)."""
    stamp_ = f"{stamp()}-{(pool_raw.get('stamp') or ['?'])[-1]}-{pool_raw['rl'][0]}-{pool_raw['rl'][1]}"
    path = STAGGER_DIR / f"{stamp_}.json"
    if cache and path.exists():
        got = _read_sims(path)
        if got.get("weapons") is not None:
            return got
    C = _combo()
    reg = reg or ATK.Regulation(None)
    ids, sa, st = EXCH.weapon_ids(), EXCH.SaRates(), C.Stagger()
    out, atk = {}, {}
    keys = sorted({k for k, _ in pool_raw["builds"]})
    print(f"interrupt: reading the stagger of {len(keys)} pool weapons ({path.name})", file=sys.stderr, flush=True)
    for key in keys:
        name, g = key.rsplit("|", 1)
        grip = "both" if g == "2h" else "one"
        wid = ids.get(name)
        if wid is None:
            continue
        rows, follow = {}, {}
        sub = C.substart_lead(reg.weapon[wid])
        for a in ATK.weapon_attacks(reg, wid, grip):
            k = a["slot"].removeprefix("2h_")
            if k == "counter":
                continue
            m = INTR._slot_member(reg, sa, wid, a, None)
            if not m:
                continue
            rows[k] = [[h[0], r] for h, r in zip(m["hits"], m["atk_rows"])]
            for r in m["atk_rows"]:
                if r not in atk:
                    s = st(r)
                    atk[r] = [*s["escapes"], round(s["push"], 3)]
        for k in rows:
            fk = C.follow_key(k)
            if fk and fk[0] in rows and (not fk[1] or sub is not None):
                follow[k] = [fk[0], float(sub) if fk[1] else 0.0]
        out[f"{name}|{grip}"] = {"rows": rows, "follow": follow}
    doc = {"weapons": out, "atk": {str(k): v for k, v in atk.items()}}
    if cache:
        _write_sims(path, doc)
    return doc


def with_stagger(weapons: dict, side: dict) -> dict:
    """`weapons` (the sequence cache) with each member's `atk_rows`, matched to its hits by frame,
    and each weapon's `follow` from `side` (`stagger_side`); weapons `side` does not hold are
    returned as they are."""
    out = dict(weapons)
    for wkey, s in side["weapons"].items():
        w = weapons.get(wkey)
        if not w or "members" not in w:
            continue
        members = {}
        for k, m in w["members"].items():
            got = s["rows"].get(k)
            if got and not m.get("atk_rows"):
                rows = [next((r for f, r in got if abs(f - h[0]) < 0.05), got[0][1]) for h in m["hits"]]
                m = {**m, "atk_rows": rows}
            members[k] = m
        out[wkey] = {**w, "members": members, "follow": s["follow"]}
    return out


def stagger_arrays(seqs, atk: dict, H: int = INTR.MAX_HITS, F: int = MAX_FOLLOW_HITS):
    """(t, q, m, follow, escape, push), each (rows, H + F): B's string hits (the first `H`, as
    `er-mechanics-interrupt._hits_array` keeps them) and then his R1 follow-up's (`follow`, at most
    `F`), packed from the first slot so the hits `simulate` walks are contiguous. `escape` (rows,
    H + F, 4) is the stagger a break by that hit locks me in, frames from the hit, on my first to
    fourth break running (`er-mechanics-combo.Stagger` `escapes`), `push` its knockback, from `atk`
    {row: [escape x 4, push]} (`stagger_side`); a hit with no row locks me in nothing."""
    n = len(seqs)
    shape = (n, H + F)
    nc = len(_combo().STAGGER_COUNTS)
    t, q, push = (np.zeros(shape) for _ in range(3))
    esc = np.zeros(shape + (nc,))
    m, fol = np.zeros(shape, bool), np.zeros(shape, bool)
    none = [0.0] * (nc + 1)
    for i, s in enumerate(seqs):
        rows = s.get("rows") or [None] * len(s["hits"])
        own = [(f, p, r, False) for (f, p), r in zip(s["hits"][:H], rows[:H])]
        more = [(f, p, r, True) for f, p, r in (s.get("follow") or [])[:F]]
        for j, (f, p, r, is_f) in enumerate(own + more):
            got = atk.get(str(r), none) if r is not None else none
            t[i, j], q[i, j], m[i, j], fol[i, j], push[i, j] = f, p, True, is_f, got[-1]
            esc[i, j] = got[:nc]
    return t, q, m, fol, esc, push


def _stagger_of(opp):
    """`opp`'s `stagger_arrays`, or for an opponent made without them (a toy, `er-mechanics-ash-whiff
    .distinct`) its own hits with no follow-up and no stagger lock."""
    if getattr(opp, "bt", None) is not None:
        return opp.bt, opp.bq, opp.bm, opp.bf, opp.be, opp.bk
    z = np.zeros(np.shape(opp.t))
    return (np.asarray(opp.t, float), np.asarray(opp.q, float), np.asarray(opp.m, bool),
            np.zeros(np.shape(opp.t), bool), np.zeros(np.shape(opp.t) + (len(_combo().STAGGER_COUNTS),)), z)


class Opponent:
    """B as arrays over the pool's defender rows (sequence x armor poise)."""

    def __init__(self, points: int = REACT_POINTS, sa_rate: bool = True, rl: tuple[int, int] = (140, 160)):
        self.weapons = INTR.load_cache()
        reg = ATK.Regulation(None)
        pool_raw = EXCH.opponent_pool(reg, INTR.CACHE / "builds.jsonl", *rl)
        self.delays = INTR.Delays(self.weapons)
        # Each hit's AtkParam row and each string's R1 follow-up (`stagger_side`), so a break of
        # mine charges what B does with it (`stagger_arrays`).
        side = stagger_side(pool_raw, reg)
        rows = INTR.PoolRows(pool_raw, with_stagger(self.weapons, side), self.delays, INTR.entry_table(), sa_rate)
        #: Pool builds whose weapon the sequence cache does not hold (`PoolRows.missing`).
        self.missing, self.n_builds = sum(rows.missing.values()), rows.n_builds
        reaches = NEU.pool_reaches(pool_raw)
        fb = float(np.median([v["reach"] for v in reaches.values() if v.get("reach")]))
        seqs = [rows.seqs[i] for i in rows.d_idx]
        self.reach = np.array([(reaches.get(f"{s['weapon']}|{'2h' if s['grip'] == 'both' else '1h'}") or {})
                               .get("reach") or fb for s in seqs], float)
        self.t, self.q, self.m = INTR._hits_array(seqs)
        (self.bt, self.bq, self.bm, self.bf, self.be, self.bk) = stagger_arrays(seqs, side["atk"])
        self.win = rows.d_win            # (5, R, K)
        self.inv = rows.d_inv            # (2, R, 1)
        self.P = rows.d_poise
        self.w = rows.d_w
        self.offs = reaction_frames(points)
        self.k = NEU.frames_per_metre()

    def fingerprint(self) -> str:
        """A hash of every array `simulate` reads, so a kept simulation never outlives its pool."""
        h = hashlib.sha256()
        extra = (np.asarray(self.offs_w, float),) if getattr(self, "offs_w", None) is not None else ()
        for a in (self.P, self.reach, self.t, self.q, self.m, self.win, self.inv, self.w, self.offs,
                  np.float64(self.k), *_stagger_of(self), *extra):
            h.update(np.ascontiguousarray(a).tobytes())
        return h.hexdigest()[:16]

    def contact_delay(self, weapon: str, grip: str) -> float:
        g = "both" if grip == "2H" else "one"
        w = self.weapons.get(f"{EXCH.plain_name(weapon)}|{g}") or {}
        m = (w.get("members") or {}).get("r1_1")
        if m and m.get("fc") is not None and m.get("first") is not None:
            return max(0.0, m["fc"] - m["first"])
        wt = m["wep_type"] if m else None
        return max(0.0, self.delays(wt, "r1_1", g))


def _window_step(t, ws, we, wb, wm, wf, wh, P, cur, M, prev, mask):
    """`er-mechanics-interrupt.simulate`'s window rule at frame `t` for the rows in `mask`:
    returns (cur, M, prev, poise-taken multiplier, HP-taken multiplier)."""
    cover = (ws <= t[..., None]) & (t[..., None] < we)
    has = cover.any(-1)
    k = np.argmax(cover, -1)

    def take(a):
        return np.take_along_axis(np.broadcast_to(a, cover.shape), k[..., None], -1)[..., 0]

    kk = np.where(has, k, -1)
    bonus = np.where(has, take(wb), 0.0)
    mult = np.where(has, take(wm), 1.0)
    floor = np.where(has, take(wf), 0.0)
    hpm = np.where(has, take(wh), 1.0)
    changed = mask & (kk != prev)
    new_m = P + bonus
    cur = np.where(changed, np.clip(new_m - (M - cur), floor * new_m, new_m), cur)
    M = np.where(changed, new_m, M)
    prev = np.where(mask, kk, prev)
    return cur, M, prev, mult, hpm


def simulate(opp: Opponent, hits, windows, D: float, gate: float, poise: float, fx=(), states=None):
    """One cast against the pool. `hits` [(frame, poise dealt)] sorted, `windows` [(start, end,
    bonus, poise mult, floor, HP mult)], `fx` effect frames. Returns per-hit landed shares, the
    expected HP taken, the expected frames saved, effect shares and the break shares.

    `states`: poise already lost when the cast starts, as (B's chip, my chip, weight), each
    (pool rows, C) with the weights of a row summing to 1 (`carried_states`). Each state is one
    more timeline per reaction quantile; current poise starts at max less the chip and carries
    through window changes as any other damage does (`CSChrToughnessModule`, the rule of
    `er-mechanics-interrupt.simulate`). None: full poise on both sides, the ash rows' setting.

    A break of mine hands B his follow-up (the stagger section above `Opponent`): `follow_hp` and
    `follow_hits` are the HP and hits of his that landed on a broken me, already inside `taken`."""
    R, Q = len(opp.P), len(opp.offs)
    if states is None:
        C, b_chip, my_chip, cw = 1, np.zeros((R, 1)), np.zeros((R, 1)), np.ones((R, 1))
    else:
        b_chip, my_chip, cw = (np.asarray(a, float) for a in states)
        C = b_chip.shape[1]
    # Chip states ride the reaction axis: column q x C + c is quantile q in state c.
    offs = np.repeat(opp.offs, C)
    QC = Q * C
    gap = np.maximum(0.0, D - np.maximum(EXCH.STRIKE_DISTANCE_M, opp.reach))
    approach, _ = NEU.arrival(gap, k=opp.k)
    bstart = offs[None, :] + approach[:, None]                              # (R, QC)
    # B's hits: his string, then his R1 follow-up, which he lands only on a broken me (the stagger
    # section above `Opponent`).
    s_t, s_q, s_m, s_f, s_e, s_k = _stagger_of(opp)
    bt = bstart[..., None] + s_t[:, None, :]                                # (R, QC, H)
    H = bt.shape[-1]

    def bget(a, i):
        """Row r's value of B's (R, H) array `a` at his hit `i` (R, QC)."""
        return np.take_along_axis(np.broadcast_to(a[:, None, :], (R, QC, H)), i[..., None], -1)[..., 0]

    def esc_at(i, c):
        """The escape of B's hit `i` breaking me for the `c`-th time running (1-based)."""
        e = np.take_along_axis(np.broadcast_to(s_e[:, None], (R, QC) + s_e.shape[1:]), i[..., None, None], 2)[:, :, 0]
        return np.take_along_axis(e, np.clip(c, 1, e.shape[-1])[..., None] - 1, -1)[..., 0]
    b_reach = np.maximum(EXCH.STRIKE_DISTANCE_M, np.asarray(opp.reach, float))[:, None]
    t = np.array([h[0] for h in hits], float)
    q = np.array([h[1] for h in hits], float)
    n = len(t)
    shape = (R, QC)
    # B's side.
    bws, bwe, bwb, bwm, bwf = (opp.win[i][:, None, :] for i in range(5))
    bwh = np.ones_like(bwm)
    th_P = np.broadcast_to(opp.P[:, None], shape)
    th_cur, th_M, th_prev = th_P - np.tile(b_chip, Q), th_P.copy(), np.full(shape, -1)
    inv0, inv1 = opp.inv[0][:, None, 0], opp.inv[1][:, None, 0]
    # My side.
    if windows:
        mw = np.array(windows, float).T                                    # (6, K)
    else:
        mw = np.array([[-1.0], [-1.0], [0.0], [1.0], [0.0], [1.0]])
    my_P = np.full(shape, float(poise))
    my_cur, my_M, my_prev = my_P - np.tile(my_chip, Q), my_P.copy(), np.full(shape, -1)
    my_bt = np.full(shape, np.inf)
    th_bt = np.full(shape, np.inf)
    landed = np.zeros(shape + (max(n, 1),), bool)
    taken = np.zeros(shape)
    pm = np.zeros(shape, int)
    pb = np.zeros(shape, int)
    # The stagger: the frame it releases me (`lock`, from the hit that last broke me), whether a
    # break locked me at all, my knockback so far, B's string over, and what his hits on a broken
    # me took.
    lock = np.full(shape, -np.inf)
    locked = np.zeros(shape, bool)
    # Breaks running (`DamageCount`): a stagger restarted by a later break escapes at the next gate.
    count = np.zeros(shape, int)
    push = np.zeros(shape)
    b_done = np.zeros(shape, bool)
    follow_hp = np.zeros(shape)
    follow_n = np.zeros(shape)
    tpad = np.append(t, np.inf)
    for _ in range(n + H + 1):
        tm = np.where(np.isinf(my_bt), tpad[np.minimum(pm, n)], np.inf)
        idx = np.minimum(pb, H - 1)
        cand = np.take_along_axis(bt, idx[..., None], -1)[..., 0]
        broke = np.isfinite(my_bt)
        # Standing, B lands his string until my gate and never presses the follow-up (it comes
        # after his string); broken, every hit of his is judged against the stagger instead.
        okb = ((pb < H) & bget(s_m, idx) & np.isinf(th_bt) & ~b_done
               & (broke | ((cand < gate) & ~bget(s_f, idx))))
        tb = np.where(okb, cand, np.inf)
        if not (np.isfinite(tm).any() or np.isfinite(tb).any()):
            break
        do_m = np.isfinite(tm) & (tm <= tb)
        do_b = np.isfinite(tb) & (tb <= tm)
        # My hit on B, on B's clock.
        if do_m.any():
            fB = tm - bstart
            th_cur, th_M, th_prev, mult, _ = _window_step(np.where(do_m, fB, -1e9), bws, bwe, bwb, bwm, bwf, bwh,
                                                          th_P, th_cur, th_M, th_prev, do_m & (fB >= 0))
            blocked = (inv0 <= fB) & (fB < inv1)
            land = do_m & ~blocked
            qi = q[np.minimum(pm, n - 1)] if n else np.zeros(shape)
            th_cur = np.where(land, th_cur - qi * mult, th_cur)
            th_bt = np.where(land & (th_cur <= 1e-6) & np.isinf(th_bt), tm, th_bt)
            if n:
                np.put_along_axis(landed, np.minimum(pm, n - 1)[..., None],
                                  np.take_along_axis(landed, np.minimum(pm, n - 1)[..., None], -1) | land[..., None], -1)
        # B's hit on me.
        if do_b.any():
            qb, kb = bget(s_q, idx), bget(s_k, idx)
            eb = esc_at(idx, count + 1)
            pre, post = do_b & ~broke, do_b & broke
            my_cur, my_M, my_prev, mult, hpm = _window_step(np.where(pre, tb, -1e9), *mw, my_P, my_cur, my_M,
                                                            my_prev, pre)
            my_cur = np.where(pre, my_cur - qb * mult, my_cur)
            taken = np.where(pre, taken + B_HIT_HP * hpm, taken)
            new = pre & (my_cur <= 1e-6)
            my_bt = np.where(new, tb, my_bt)
            # A break ends my cast and its windows: the stagger holds me from here, and poise refills.
            lock, locked = np.where(new, tb + eb, lock), np.where(new, eb > 0, locked)
            push, count = np.where(new, kb, push), np.where(new, 1, count)
            # Broken: the hit lands as a true combo would (`er-mechanics-combo.break_follow_p`), and
            # only while the knockback leaves me inside his reach.
            near = EXCH.STRIKE_DISTANCE_M + push <= b_reach + 1e-9
            p = np.where(post & locked & near, np.where(tb < lock, 1.0, np.where(tb == lock, 0.5, 0.0)), 0.0)
            taken = taken + p * B_HIT_HP
            follow_hp, follow_n = follow_hp + p * B_HIT_HP, follow_n + p
            hit = post & (p >= 1.0)
            after = np.where(hit, my_cur - qb, my_cur)
            again = hit & (after <= 1e-6)
            lock = np.where(again, np.maximum(lock, tb + eb), lock)
            locked = locked | (again & (eb > 0))
            push, count = np.where(again, push + kb, push), np.where(again, count + 1, count)
            my_cur = np.where(new | again, my_P, after)
            # A hit past the escape, out of reach or on a tie is the last: I am free from there.
            b_done = b_done | (post & (p < 1.0))
        pm = pm + do_m
        pb = pb + do_b
    # Each start offset weighs 1 / Q unless the opponent carries its own (`ChainTrades.for_pool`).
    ow = getattr(opp, "offs_w", None)
    ow = np.full(Q, 1.0 / Q) if ow is None else np.asarray(ow, float)
    W = np.broadcast_to(opp.w[:, None] * np.repeat(ow, C)[None, :] * np.tile(cw, Q), shape)
    t_last = t[-1] if n else 0.0
    # Broken after my last hit, the recovery I no longer sit in runs to the frame I can act again:
    # the stagger's escape, not the break.
    free = np.where(locked, np.maximum(lock, my_bt), my_bt)
    saved = np.where(np.isfinite(my_bt) & (my_bt >= t_last), np.maximum(0.0, gate - free), 0.0)
    return {"p": [float((W * landed[..., i]).sum()) for i in range(n)],
            "taken": float((W * taken).sum()), "saved": float((W * saved).sum()),
            "follow_hp": float((W * follow_hp).sum()), "follow_hits": float((W * follow_n).sum()),
            "fx": [float((W * ~(my_bt < f)).sum()) for f in fx],
            "broken": float((W * np.isfinite(my_bt)).sum()),
            "stopped_b": float((W * np.isfinite(th_bt)).sum()),
            "approach": float((opp.w * approach).sum())}


def multiplier(r: dict, value: float) -> float:
    """Delivered net value over undisturbed value for one cached result (`profile`) at the row's
    `value` (HP a second): `max(0, delivered / undisturbed)`; 1 with nothing to weigh."""
    if r is None:
        return 1.0
    if not r["hits"]:
        return r["fx"][0] if r.get("fx") else 1.0
    full = sum(d + h for _, _, d, h in r["hits"])
    if full <= 0:
        return 1.0
    got = sum(p * (d + h) for p, (_, _, d, h) in zip(r["sim"]["p"], r["hits"]))
    got += -r["sim"]["taken"] + r["sim"]["saved"] * value / AS.TAE_FPS
    return max(0.0, got / full)


# --------------------------------------------------------------------------------------------
# attack strings: R1, L1 and powerstance chains (er-effects-rs-q0r9)
#
# The R1, L1 and dual strings are valued as the casts above are: the pool answers the string, a
# hit that breaks the other side cancels its later hits, and `multiplier` is the share of the
# string's HP still delivered net of the HP taken. That is where poise damage per hit, the
# string's own hyperarmor (TAE 795 windows, the `CSChrToughnessModule` rule) and hit size enter: a
# heavy hit that breaks the opponent before his hit lands takes nothing back, a light string that
# trades loses a pool hit (`B_HIT_HP`) against each of its small ones. Casts and strings share
# two rules read from the data:
#
# - Poise carries between exchanges (`poise_carries`). It never regenerates gradually; it refills
#   at once when `er-mechanics-interrupt.poise_reset_seconds` (30 s in 1.17.1) pass without a
#   poise hit, and every hit re-arms that timer. A fight window
#   (`er-mechanics-exchange.FIGHT_WINDOW_S`) is shorter, so each exchange starts with whatever
#   the earlier ones wore off: both sides start at a uniformly random point of the cycle the
#   other's opening hit drives (`carried_states`, the `carried` scenario of
#   `er-mechanics-interrupt`).
# - A poise break of a player opens no riposte (`INFERRED`, module docstring), so a break is worth
#   the opponent hits it cancels and the string's own hits that then land, which `simulate`
#   already counts.

#: Reaction quantiles per pool row for a string. `INFERRED`: a sampling resolution, not a game
#: number; a string is simulated in up to `CHAIN_CHIP_STATES` squared carried-poise states per
#: quantile, so it samples fewer quantiles than a cast (`REACT_POINTS`). Measured 2026-10-19 on
#: the RL 115-135 pool, R1 strings of Pickaxe, Bloodhound Claws, Giant-Crusher, Longsword, Dagger
#: and Greatsword, carried and fresh: 11 against 41 moves no multiplier by more than 0.002 and
#: takes a quarter of the time (Dagger 1H carried 2.0 s against 15.7 s).
CHAIN_REACT_POINTS = 11
#: Carried-poise states per side (`er-mechanics-interrupt.chip_states`), `INFERRED` sampling like
#: its `MAX_CHIP_STATES`.
CHAIN_CHIP_STATES = 4


def with_read(opp):
    """`opp` answering two ways, the split `er-mechanics-class-roles.PunishCoverage` and
    `er-mechanics-ashes.REACT_SHARE` already make for a punish: a reaction (its `reaction_frames`
    quantiles, equal) in `REACT_SHARE` of exchanges, and a read pressed with mine (offset 0,
    `er-mechanics-exchange`'s simultaneous start) in the rest. The read is where hyperarmor and
    poise decide a trade: both attacks are in their windup together, and the side whose window
    holds against the other's hit swings through it. Sets `offs` and `offs_w` in place."""
    share = AS.REACT_SHARE
    q = len(opp.offs)
    opp.offs = np.concatenate([[0.0], opp.offs])
    opp.offs_w = np.concatenate([[1.0 - share], np.full(q, share / q)])
    return opp


def poise_carries() -> bool:
    """True when poise damage carries from one exchange to the next: a fight window
    (`er-mechanics-exchange.FIGHT_WINDOW_S`) ends before the refill timer
    (`er-mechanics-interrupt.poise_reset_seconds`: GameSystemCommonParam
    `baseToughnessRecoverTime` x PlayerCommonParam `toughnessRecoverCorrection`)."""
    return EXCH.FIGHT_WINDOW_S < INTR.poise_reset_seconds()


def carried_states(opp, d1: float, poise: float, C: int = CHAIN_CHIP_STATES):
    """`simulate`'s `states` for both sides at a uniformly random point of the cycle the other's
    opening hit wears into them, independently: B's chip `j x d1` (`d1` my opening hit's poise),
    mine `j x` B's opening hit's poise (`er-mechanics-interrupt.chip_states`, `carried`). States
    no row reaches are dropped."""
    R = len(opp.P)
    bc, bw = INTR.chip_states(opp.P, np.full(R, float(d1)), True, C)
    q1 = np.where(opp.m[:, 0], opp.q[:, 0], 0.0)
    mc, mw = INTR.chip_states(np.full(R, float(poise)), q1, True, C)
    Cb, Cm = bc.shape[1], mc.shape[1]
    b, m, w = np.repeat(bc, Cm, 1), np.tile(mc, Cb), np.repeat(bw, Cm, 1) * np.tile(mw, Cb)
    keep = (w > 0).any(0)
    return b[:, keep], m[:, keep], w[:, keep]


def chain_cast(chain, delay: float = 0.0):
    """(hits [(frame, poise, damage, 0)], windows, gate) of a string pressed at each step's
    next-attack cancel (`er-mechanics-weapon-embed.chain_summary`'s timing), from embedding slots
    that carry `hit_at`, `hit_poise` and `ha` (`er-mechanics-weapon-embed.trade_fields`); None
    when one does not. Every hit moves by `delay`, the weapon's R1 #1 front contact, as
    `Rows.cast` moves a blade hit. A slot's damage plus status HP is split evenly over its hits
    (`INFERRED`: the embedding keeps per-slot damage, and a slot's own hits share one AtkParam
    row). The gate is the last step's roll cancel, else its last hit, and never before the last
    hit."""
    hits, windows, t, gate = [], [], 0.0, 0.0
    for i, s in enumerate(chain):
        if not s or s.get("hit_at") is None:
            return None
        hp = (s.get("damage") or 0.0) + sum((s.get("status_hp") or {}).values())
        n = max(1, len(s["hit_at"]))
        for f, p in zip(s["hit_at"], s["hit_poise"]):
            hits.append((round(t + f + delay, 2), round(p, 2), round(hp / n, 1), 0.0))
        windows += [(round(t + w[0], 2), round(t + w[1], 2), *w[2:]) for w in s.get("ha") or []]
        end = s.get("roll_cancel")
        gate = t + (end if end is not None else (s.get("last_hit") or max(s["hit_at"], default=0.0)))
        if i + 1 < len(chain):
            if s.get("next_cancel") is None:
                break
            t += s["next_cancel"]
    if not hits:
        return None
    hits.sort(key=lambda h: h[0])
    hits = hits[:MAX_MY_HITS]
    return hits, sorted(windows), round(max(gate, hits[-1][0]), 2)


#: Movement attacks whose entry clip plays before the attack's own: the rolling and backstep R1,
#: with `er-mechanics-interrupt.entry_table`'s entry frames (roll 20, backstep 14, `VERIFIED` TAE)
#: and unconditional i-frames (roll f0-13, backstep none: its JumpTable 8 is gated on stateInfo
#: 473, `BACKSTEP_ANIM`). The running and crouch R1 have none here: the run is under way and the
#: crouch held when the press comes (`er-mechanics-timing-mixup` items 1 and 4, `INFERRED`).
ENTRY_SLOTS = ("roll_r1", "bstep_r1")
_ENTRY: dict = {}


def entry_step(key: str, table: dict | None = None) -> dict | None:
    """A `chain_cast` step for the entry clip of movement attack `key` (`ENTRY_SLOTS`): no hits,
    one window over its unconditional i-frames taking poise and HP x0 (`simulate`'s window rule:
    a hit of the pool's inside it whiffs), and its entry frames as the next-attack cancel, so the
    attack's hits and windows follow it. None for any other key. `table` stands in for
    `er-mechanics-interrupt.entry_table` (read once)."""
    key = key.removeprefix("2h_")
    if key not in ENTRY_SLOTS:
        return None
    if table is None:
        if not _ENTRY:
            _ENTRY.update(INTR.entry_table())
        table = _ENTRY
    inv = table.get("inv_" + key) or 0.0
    return {"hit_at": [], "hit_poise": [], "damage": 0.0, "next_cancel": table[key],
            "ha": [[0.0, float(inv), 0.0, 0.0, 0.0, 0.0]] if inv else []}


class ChainTrades:
    """`trade(chain, weapon, grip, reach)`: the interrupt profile of one string against `opp` at my
    armor `poise` (module section above), with `mult` its `multiplier` at the string's own HP per
    second. Simulations are memoised on `sim_key` plus the carried flag and kept in `path`
    (`SIMS_DIR`, keyed by code stamp, pool and poise) across runs.

    With `spread` (`duel_spread`, a duel's `--arena-m`) a string is valued over the duel's
    distances as an ash is (`Rows.spread_profile`): one simulation per distance its reach covers
    (`reach_parts`), each weighted by the duel's time there, and none where it does not reach, so
    `mult` is the string's share of the duel times its value there. A string with no measured
    reach is taken to reach `EXCHANGE_DISTANCE_M`, where the fixed model throws it."""

    def __init__(self, opp, poise: float, carry: bool | None = None, path: Path | None = None, spread=None):
        self.opp, self.poise = opp, float(poise)
        self.carry = poise_carries() if carry is None else carry
        self.path = path
        self.spread = spread
        self.memo = _read_sims(path) if path else {}
        self.new = 0

    @classmethod
    def for_pool(cls, poise: float | None, rl: tuple[int, int], carry: bool | None = None,
                 arena_m: float | None = None) -> "ChainTrades":
        """Against the exchange pool of RL window `rl`. `poise` None: the pool's own weighted
        median armor poise stands in for mine. `arena_m`: a duel's starting separation, the strings
        valued over the spread the ashes are valued over (`duel_spread_for`).

        The pool answers with a read as well as a reaction (`with_read`)."""
        opp = with_read(Opponent(CHAIN_REACT_POINTS, rl=rl))
        if poise is None:
            o = np.argsort(opp.P)
            poise = float(opp.P[o][np.searchsorted(np.cumsum(opp.w[o]), 0.5 * opp.w.sum())])
        carry = poise_carries() if carry is None else carry
        path = (SIMS_DIR / f"{stamp('sim')}-{opp.fingerprint()}-chain-poise{poise:g}-"
                f"{'carried' if carry else 'fresh'}.json")
        spread = duel_spread_for(arena_m) if arena_m else None
        return cls(opp, poise, carry, path, spread)

    def save(self) -> None:
        if self.path and self.new:
            _write_sims(self.path, self.memo)
            self.new = 0

    def _key(self, hits, windows, gate: float, D: float) -> str:
        return sim_key(hits, windows, D, gate, [], self.poise) + ("|carried" if self.carry else "")

    def _sim(self, hits, windows, gate: float, D: float) -> dict:
        states = carried_states(self.opp, hits[0][1], self.poise) if self.carry else None
        return simulate(self.opp, [(h[0], h[1]) for h in hits], windows, D, gate, self.poise, states=states)

    def _cast(self, chain, weapon: str, grip: str, reach: float | None):
        c = chain_cast(chain, self.opp.contact_delay(weapon, grip))
        if c is None:
            return None
        return (*c, REACH_SHARE * reach if reach else EXCHANGE_DISTANCE_M)

    def _parts(self, chain, weapon: str, grip: str, reach: float | None):
        """(hits, windows, gate, [(distance, credited time)]): the fixed distance at weight 1
        without a spread, else `reach_parts` (empty: the string reaches nowhere)."""
        c = self._cast(chain, weapon, grip, reach)
        if c is None:
            return None
        hits, windows, gate, D = c
        if not self.spread:
            return hits, windows, gate, [(D, 1.0)]
        # A string with no measured reach (whips, flails, Nox Flowing: the reach decoder has no pose
        # for them) is taken to reach the exchange distance, the distance the fixed model throws it
        # at, so it pays the same opportunity share as every other string.
        return hits, windows, gate, [(d, w) for d, w, _ in reach_parts(self.spread, reach or EXCHANGE_DISTANCE_M)]

    def profile(self, hits, windows, gate: float, D: float) -> dict:
        key = self._key(hits, windows, gate, D)
        if key not in self.memo:
            self.memo[key] = self._sim(hits, windows, gate, D)
            self.new += 1
        return {"hits": hits, "windows": windows, "gate": gate, "D": round(D, 2), "sim": self.memo[key]}

    def prepare(self, items, jobs: int = 1, log=print) -> int:
        """Simulate every string of `items` [(chain, weapon, grip, reach)] not in the memo, in
        `jobs` forked workers throttled as `profiles` throttles its own (`throttle`), and keep them;
        `trade` then reads the memo. Returns how many were simulated."""
        todo = {}
        for it in items:
            c = self._parts(*it)
            if c is None:
                continue
            hits, windows, gate, parts = c
            for D, _ in parts:
                k = self._key(hits, windows, gate, D)
                if k not in self.memo:
                    todo[k] = (hits, windows, gate, D)
        log(f"strings: {len(todo)} to simulate, {len(self.memo)} kept ({self.path})")
        if not todo:
            return 0
        work = list(todo.items())
        if jobs <= 1:
            for i, (k, c) in enumerate(work, 1):
                self.memo[k] = self._sim(*c)
                self.new += 1
                if i % 50 == 0:
                    log(f"  {i}/{len(work)} strings")
        else:
            import multiprocessing as mp
            ctx = mp.get_context("fork")
            cpus = allowed_cpus()
            workers = cpus[1:] or cpus
            jobs = max(1, min(jobs, len(workers)))
            _W["chain"] = self
            chunks = [c for c in (work[i::jobs * 4] for i in range(jobs * 4)) if c]
            with ctx.Pool(jobs, initializer=_worker_init, initargs=(workers, ctx.Value("i", 0))) as pool:
                for i, res in enumerate(pool.imap_unordered(_chain_work, chunks), 1):
                    self.memo.update(res)
                    self.new += len(res)
                    log(f"  {i}/{len(chunks)} chunks")
        self.save()
        return len(work)

    def trade(self, chain, weapon: str, grip: str, reach: float | None) -> dict | None:
        """{'mult', 'taken', 'stopped_b', 'broken', 'landed', 'poise_hit'} of one string, None when
        its slots carry no per-hit poise (an embedding made before `trade_fields`). `grip` '1H' or
        '2H' is the grip whose R1 #1 front contact moves the hits (a left-hand or dual string:
        '1H'). Over a spread (`spread`), the simulations of its distances are summed weighted by
        the duel's time at each, as `Rows.spread_profile` sums a cast's, and `share` is that
        time; `landed` is the share of the HP landed where it is thrown."""
        c = self._parts(chain, weapon, grip, reach)
        if c is None:
            return None
        hits, windows, gate, parts = c
        full = sum(h[2] for h in hits)
        value = full / (gate / AS.TAE_FPS) if gate > 0 else 0.0
        sims = [(w, self.profile(hits, windows, gate, D)["sim"]) for D, w in parts]
        share = sum(w for w, _ in sims)
        s = {"p": [sum(w * q["p"][i] for w, q in sims) for i in range(len(hits))]}
        for k in ("taken", "saved", "follow_hp", "stopped_b", "broken"):
            s[k] = sum(w * q.get(k, 0.0) for w, q in sims)
        r = {"hits": hits, "windows": windows, "gate": gate, "sim": s}
        out = {"mult": round(multiplier(r, value), 4), "taken": round(s["taken"], 1),
               "follow_hp": round(s.get("follow_hp", 0.0), 1),
               "stopped_b": round(s["stopped_b"], 4), "broken": round(s["broken"], 4),
               "landed": (round(sum(p * h[2] for p, h in zip(s["p"], hits)) / full / share, 4)
                          if full and share else None),
               "poise_hit": round(max(h[1] for h in hits), 1)}
        if self.spread:
            out["share"] = round(share, 4)
        return out


# --------------------------------------------------------------------------------------------
# the escape table (module docstring)


def heading(vx, vz):
    """Facing angle of the direction (vx, vz) in the clip frame: 0 along -Z (a clip's forward),
    growing toward +X."""
    return np.arctan2(vx, -vz)


def _rot(x, z, a):
    """(x, z) turned by `a` radians the way `heading` grows: `_rot(0, -1, h)` faces `h`."""
    c, s = np.cos(a), np.sin(a)
    return x * c - z * s, x * s + z * c


def _wrap(a):
    return (a + np.pi) % (2 * np.pi) - np.pi


def clip_track(cat: int, anim: int, events, frames: float, step: float = ESCAPE_STEP) -> dict:
    """One skill clip over its first `frames` real frames, per `step` from its start (`f`): the
    root's x, z and yaw in the clip's frame and the turn the caster can make in that step, locked on
    (`turn`, radians: `er-mechanics-reach.turn_rate_at`). Real time runs through the clip's TAE 608
    play speed (`clip_to_real`, the clock the hit geometry uses). Past the clip's end the root holds
    still and the turn is the rate `turn_rate_at` gives there."""
    pose, R, _ = AS._pose()
    to_real = AS.ATK.clip_to_real(events) if events else (lambda s: s)
    try:
        clip = pose.load_animation(*R.hkx_source(cat, anim))
    except FileNotFoundError:
        clip = None
    dur = float(clip.duration) if clip else max([e.end for e in events or ()] + [0.0])
    cg = np.linspace(0.0, dur, max(2, int(dur * AS.TAE_FPS * 4) + 1))
    rg = np.array([to_real(c) for c in cg]) * AS.TAE_FPS
    f = np.arange(int(math.ceil(frames / step)) + 1) * step
    c = np.where(f <= rg[-1], np.interp(f, rg, cg), dur + (f - rg[-1]) / AS.TAE_FPS)
    s = np.array(clip.root_samples, float) if clip is not None and clip.root_samples else np.zeros((1, 4))
    if len(s) > 1:
        ts = np.linspace(0.0, clip.root_duration, len(s))
        x, z, yaw = (np.interp(c, ts, col) for col in (s[:, 0], s[:, 2], np.unwrap(s[:, 3])))
    else:
        x, z, yaw = (np.full(f.shape, s[0, i]) for i in (0, 2, 3))
    turns, disabled = R.turn_windows(events or ())
    deg = np.array([R.turn_rate_at(ci, turns, disabled, "locked") for ci in c])
    return {"f": f, "x": x, "z": z, "yaw": yaw, "turn": np.radians(deg) * step / AS.TAE_FPS}


def cast_track(main: dict, pre: dict | None = None, lead: float = 0.0, step: float = ESCAPE_STEP) -> dict:
    """The caster's motion per step on the cast clock: `dx`, `dz` (root motion over the step, in
    the clip frame), `yaw` (the clip's) and `turn`. A lead-in clip `pre` plays its first `lead`
    frames before `main`; the yaw it ends on is not carried into `main` (`INFERRED`: the only
    lead-in, Wild Strikes' a610_040050, is taken to keep its facing)."""
    def part(tr, n=None):
        sl = slice(None) if n is None else slice(0, n)
        return (np.diff(tr["x"], prepend=tr["x"][0])[sl], np.diff(tr["z"], prepend=tr["z"][0])[sl],
                tr["yaw"][sl], tr["turn"][sl])

    parts = [part(main)]
    if pre is not None and lead > 0:
        parts.insert(0, part(pre, int(round(lead / step))))
    dx, dz, yaw, turn = (np.concatenate(a) for a in zip(*parts))
    return {"dx": dx, "dz": dz, "yaw": yaw, "turn": turn}


def play(track: dict, D: float, starts, roll, chase, rolls: list, radius: float, limits: dict,
         run_step: float, behind: float, step: float = ESCAPE_STEP, orbit=None, delay=None):
    """Both players through the cast, one column per plan: he stands `D` m ahead of me, rolls
    `rolls[roll[n]]` at `starts[n]` (inf: never) relative to where I am then, and from the roll's
    first R1 frame either holds where it leaves him or (`chase[n]`) runs `run_step` m a step to
    `behind` m behind me. Per step I move by `track` turned by my tracking angle, his body stops
    mine (two capsules of `radius`), and I turn toward him by at most `track['turn']`.
    `orbit[n]` (+1 or -1, 0 for a roll) replaces the roll's path with a locked-on run sideways:
    each step until the plan's `ready` frame he runs `run_step` m square to the line between us,
    so he circles me rather than leaving along a straight line (the strafe of `escape_table`).
    Returns (my position (K, N, 2), my tracking angle (K, N), his position (K, N, 2), the first
    frame the backstab `limits` hold for a chaser (N), inf when never)."""
    dx, dz, yaw, turn = track["dx"], track["dz"], track["yaw"], track["turn"]
    starts = np.asarray(starts, float)
    roll, chase = np.asarray(roll, int), np.asarray(chase, bool)
    K, N = len(dx), len(starts)
    orbit = np.zeros(N) if orbit is None else np.asarray(orbit, float)
    # `delay[n]`: frames a chaser spends after `ready` before he runs (turning back to face me).
    delay = np.zeros(N) if delay is None else np.asarray(delay, float)
    L = max(len(r["path"]) for r in rolls)
    paths = np.array([list(r["path"]) + [r["path"][-1]] * (L - len(r["path"])) for r in rolls], float)
    plen = np.array([len(r["path"]) for r in rolls], float)
    ready = np.array([r["ready"] for r in rolls], float)[roll]
    P, psi = np.zeros((N, 2)), np.zeros(N)
    Q = np.tile([0.0, -float(D)], (N, 1))
    base, theta, begun = Q.copy(), np.zeros(N), np.zeros(N, bool)
    bs = np.full(N, np.inf)
    Ph, psih, Qh = np.empty((K, N, 2)), np.empty((K, N)), np.empty((K, N, 2))
    for k in range(K):
        f = k * step
        mx, mz = _rot(dx[k], dz[k], psi)
        P[:, 0] += mx
        P[:, 1] += mz
        new = ~begun & (f >= starts)
        if new.any():
            theta[new] = heading(P[new, 0] - Q[new, 0], P[new, 1] - Q[new, 1])
            base[new] = Q[new]
            begun |= new
        u = np.where(begun, f - starts, 0.0)
        run = begun & chase & (u >= ready + delay)
        rolling = begun & ~run & (orbit == 0)
        if rolling.any():
            j = np.clip(np.floor(u).astype(int), 0, L - 1)
            rx, rz = _rot(paths[roll, j, 0], paths[roll, j, 1], theta)
            Q = np.where(rolling[:, None], base + np.stack([rx, rz], 1), Q)
        strafing = begun & ~run & (orbit != 0) & (u < ready)
        if strafing.any():
            ox, oz = P[:, 0] - Q[:, 0], P[:, 1] - Q[:, 1]
            ol = np.maximum(np.hypot(ox, oz), 1e-9)
            go = orbit * run_step / ol
            Q = np.where(strafing[:, None], Q + np.stack([-oz * go, ox * go], 1), Q)
        if run.any():
            fx, fz = _rot(0.0, -1.0, yaw[k] + psi)
            ddx, ddz = P[:, 0] - behind * fx - Q[:, 0], P[:, 1] - behind * fz - Q[:, 1]
            ox, oz = Q[:, 0] - P[:, 0], Q[:, 1] - P[:, 1]
            # Round my side rather than through me (`er-mechanics-backstab.circle`), on the side
            # nearer his way: `circle` always turns the same way, which from my other side steers
            # him into my body for good.
            through = (np.hypot(ox, oz) < 1.0) & (ddx * ox + ddz * oz < 0)
            side = np.where(ddx * oz - ddz * ox >= 0, 1.0, -1.0)
            ddx, ddz = np.where(through, ddx + side * oz, ddx), np.where(through, ddz - side * ox, ddz)
            dist = np.hypot(ddx, ddz)
            go = np.where(run, np.minimum(dist, run_step) / np.maximum(dist, 1e-9), 0.0)
            Q = Q + np.stack([ddx * go, ddz * go], 1)
        gx, gz = P[:, 0] - Q[:, 0], P[:, 1] - Q[:, 1]
        gl = np.hypot(gx, gz)
        close = gl < 2 * radius
        if close.any():
            # Standing, his body holds its ground and stops mine. On a roll or skill path the two
            # bodies meet moving, and each gives half the overlap (`INFERRED`: equal push); his
            # path goes on from where mine stopped him, so a dash through me ends at my body.
            pushed = close & rolling & (u < plen[roll])
            ux, uz = gx / np.maximum(gl, 1e-9), gz / np.maximum(gl, 1e-9)
            half = np.where(pushed, (2 * radius - gl) / 2, 0.0)
            shift = np.stack([-ux * half, -uz * half], 1)
            Q, base = Q + shift, base + shift
            P = P - shift
            kk = np.where(close & ~pushed, 2 * radius / np.maximum(gl, 1e-9), 1.0)
            P = np.where((close & ~pushed)[:, None], Q + np.stack([gx * kk, gz * kk], 1), P)
        want = heading(Q[:, 0] - P[:, 0], Q[:, 1] - P[:, 1])
        psi = psi + np.clip(_wrap(want - (yaw[k] + psi)), -turn[k], turn[k])
        Ph[k], psih[k], Qh[k] = P, psi, Q
        if run.any():
            sep = np.hypot(P[:, 0] - Q[:, 0], P[:, 1] - Q[:, 1])
            rel = np.degrees(_wrap(heading(P[:, 0] - Q[:, 0], P[:, 1] - Q[:, 1]) - (yaw[k] + psi)))
            ok = (run & np.isinf(bs) & (sep < limits["dist"]) & (rel >= limits["diff_min"])
                  & (rel <= limits["diff_max"]))
            bs[ok] = f
    return Ph, psih, Qh, bs


def first_touches(samples: dict, n_hits: int, Ph, psih, Qh, starts, iframes, step: float = ESCAPE_STEP):
    """(n_hits, N): the first frame each hit's samples touch him on each plan of `play`, inf when
    none does. A sample at cast frame `T`, relative to my root at its anchor frame `A`, is placed
    where my root and tracking angle put it then; it touches when it is closer to his axis than its
    contact radius `R` while he is not invincible: `iframes` per plan is either the frames from
    `starts` he is invincible for (a roll), or (N, W, 2) windows [from, to) counted from `starts`
    (a movement skill's TimeAct windows, `move_motion`). Plans are taken `TOUCH_CHUNK` at a time,
    so the (samples, plans) arrays stay bounded however many escapes a table holds."""
    K = Ph.shape[0]
    starts = np.atleast_1d(np.asarray(starts, float))
    win = np.asarray(iframes, float)
    if win.ndim < 3:
        win = np.stack([np.zeros(len(starts)), np.broadcast_to(win, starts.shape)], -1)[:, None, :]
    T = np.asarray(samples["T"], float)
    kt = np.clip(np.rint(T / step).astype(int), 0, K - 1)
    ka = np.clip(np.rint(np.asarray(samples["A"], float) / step).astype(int), 0, K - 1)
    X, Z = np.asarray(samples["X"], float)[:, None], np.asarray(samples["Z"], float)[:, None]
    Rr, H = np.asarray(samples["R"], float)[:, None], np.asarray(samples["H"], int)
    t = T[:, None]
    out = np.full((n_hits, len(starts)), np.inf)
    for a in range(0, len(starts), TOUCH_CHUNK):
        sl = slice(a, a + TOUCH_CHUNK)
        wx, wz = _rot(X, Z, psih[ka, sl])
        d = np.hypot(wx + Ph[ka, sl, 0] - Qh[kt, sl, 0], wz + Ph[ka, sl, 1] - Qh[kt, sl, 1])
        rel = t - starts[sl]
        w = win[sl]
        safe = ((rel[..., None] >= w[None, :, :, 0]) & (rel[..., None] < w[None, :, :, 1])).any(-1)
        when = np.where((d < Rr) & ~safe, t, np.inf)
        part = np.full((n_hits, when.shape[1]), np.inf)
        np.minimum.at(part, H, when)
        out[:, sl] = part
    return out


def turn_back(angle):
    """Frames to turn `angle` radians back to face me after a skill cast away from me, at the
    player's turn rate outside attack TimeAct (`er-mechanics-reach.DEFAULT_TURN_DEG_PER_S`, the
    joint modifier's `turnVelocity`). A roll and a strafe keep facing me while locked on (0)."""
    return np.degrees(np.asarray(angle, float)) / AS._pose()[1].DEFAULT_TURN_DEG_PER_S * AS.TAE_FPS


def _enc(a):
    """Frames as half-frame integers, -1 for never: the table's JSON stays small."""
    return np.where(np.isfinite(a), np.rint(np.where(np.isfinite(a), a, 0.0) * 2), -1).astype(int).tolist()


def escape_table(track: dict, samples: dict, n_hits: int, D: float, rolls: list, radius: float, limits: dict,
                 run_step: float, behind: float, step: float = ESCAPE_STEP) -> dict:
    """The geometry pass of one cast (module docstring). Frames are half-frame integers (`_enc`).
    `t0[h]`: hit h's first touch on him standing still; `T` the earliest. Plans start at
    `s` = 0, 1, ..., floor(T) + `ESCAPE_SPILL`: `touch[h][plan][roll][i]` (plan 0 hold, 1 chase),
    `gap[roll][i]` (metres between our axes at the roll's first R1 frame), `bs[roll][i]` (a
    chaser's first backstab frame), and `strafe` (`strafe_table`).

    A roll has `iframes` from its first frame; an entry carrying `windows` (`move_motion`, a
    movement skill) is invincible in those instead. `moves[roll]` names each entry. A movement
    skill that at no start takes him out of a hit that touches him standing still is no escape
    and is left out of the table, so the whiff pass does not pay for it."""
    never = np.array([np.inf])
    Ph, ps, Qh, _ = play(track, D, never, [0], [False], rolls[:1], radius, limits, run_step, behind, step)
    t0 = first_touches(samples, n_hits, Ph, ps, Qh, never, [0.0], step)[:, 0]
    if not np.isfinite(t0).any():
        return {"t0": _enc(t0), "iframes": [float(r["iframes"]) for r in rolls],
                "ready": [float(r["ready"]) for r in rolls], "T": None}
    T = float(t0.min())
    n = int(math.floor(T)) + ESCAPE_SPILL + 1

    def run(rs):
        nr = len(rs)
        starts = np.tile(np.arange(n, dtype=float), nr)
        roll = np.repeat(np.arange(nr), n)
        wins = [r.get("windows") or [(0.0, float(r["iframes"]))] for r in rs]
        W = max(len(w) for w in wins)
        wa = np.array([list(w) + [(0.0, 0.0)] * (W - len(w)) for w in wins], float)[roll]
        ready = np.array([float(r["ready"]) for r in rs])[roll]
        face = np.array([float(r.get("face", 0.0)) for r in rs])[roll]
        # Hold first: where he is and which way he faces when the clip lets him act decides how
        # long he turns back to me before either plan goes on (`turn_back`).
        Ph, ps, Qh, _ = play(track, D, starts, roll, np.zeros(len(roll), bool), rs, radius, limits, run_step,
                             behind, step)
        K, N = Ph.shape[:2]
        cols = np.arange(N)
        ks = np.clip(np.rint(starts / step).astype(int), 0, K - 1)
        kr = np.clip(np.rint((starts + ready) / step).astype(int), 0, K - 1)
        gap = np.hypot(Ph[kr, cols, 0] - Qh[kr, cols, 0], Ph[kr, cols, 1] - Qh[kr, cols, 1])
        theta0 = heading(Ph[ks, cols, 0] - Qh[ks, cols, 0], Ph[ks, cols, 1] - Qh[ks, cols, 1])
        to_me = heading(Ph[kr, cols, 0] - Qh[kr, cols, 0], Ph[kr, cols, 1] - Qh[kr, cols, 1])
        turn = np.where(face != 0, turn_back(np.abs(_wrap(to_me - theta0 - face))), 0.0)
        th = first_touches(samples, n_hits, Ph, ps, Qh, starts, wa, step)
        Pc, pc, Qc, bs = play(track, D, starts, roll, np.ones(len(roll), bool), rs, radius, limits, run_step,
                              behind, step, delay=turn)
        tc = first_touches(samples, n_hits, Pc, pc, Qc, starts, wa, step)
        tt = np.stack([th.reshape(n_hits, nr, n), tc.reshape(n_hits, nr, n)], 1)
        return tt, gap.reshape(nr, n), bs.reshape(nr, n), turn.reshape(nr, n)

    tt, gap, bs, turn = run(rolls)
    # Keep the rolls, and a movement skill only where some start up to `T` leaves a hit that
    # touches him standing still off him on either plan.
    hit0 = np.isfinite(t0)
    escapes = (~np.isfinite(tt[hit0][..., : int(math.floor(T)) + 1])).any((0, 1, 3))
    keep = [i for i, r in enumerate(rolls) if not r.get("windows") or escapes[i]]
    rolls = [rolls[i] for i in keep]
    tt, gap, bs, turn = tt[:, :, keep], gap[keep], bs[keep], turn[keep]
    base = {"t0": _enc(t0), "iframes": [float(r["iframes"]) for r in rolls],
            "ready": [float(r["ready"]) for r in rolls], "moves": [r.get("name", "roll") for r in rolls],
            "fp": [float(r.get("fp", 0.0)) for r in rolls]}
    return {**base, "T": _enc(np.array([T]))[0], "s": [0, n],
            "touch": [_enc(tt[h]) for h in range(n_hits)],
            "gap": np.round(gap, 2).tolist(),
            "bs": _enc(bs), "turn": np.round(turn, 1).tolist(),
            "strafe": strafe_table(track, samples, n_hits, D, radius, limits, run_step, behind, step)}


def strafe_table(track: dict, samples: dict, n_hits: int, D: float, radius: float, limits: dict,
                 run_step: float, behind: float, step: float = ESCAPE_STEP) -> dict:
    """The strafe plans of one cast (module docstring): he runs sideways round me from the cast's
    first frame, either way, at the locked-on run speed (`run_step`, the speed the chase uses), and
    commits at `c` = 0, 1, ..., the end of the track. A hold plan stops where the strafe leaves him
    and punishes from there; a chase plan runs for the backstab from `c`. Frames as `escape_table`:
    `touch[h][plan][way][i]` (plan 0 hold, 1 chase; way 0 is +1, 1 is -1), `gap[way][i]` (metres
    between our axes at the commit) and `bs[way][i]`. He is never invincible."""
    K = len(track["dx"])
    nc = int(math.floor((K - 1) * step)) + 1
    commits = [{"path": [(0.0, 0.0)], "iframes": 0.0, "ready": float(c)} for c in range(nc)]
    roll = np.tile(np.arange(nc), 4)
    orbit = np.tile(np.repeat([1.0, -1.0], nc), 2)
    chase = np.repeat([False, True], 2 * nc)
    starts = np.zeros(len(roll))
    Ph, ps, Qh, bs = play(track, D, starts, roll, chase, commits, radius, limits, run_step, behind, step,
                          orbit=orbit)
    tt = first_touches(samples, n_hits, Ph, ps, Qh, starts, np.zeros(len(roll)), step)
    N = Ph.shape[1]
    kr = np.clip(np.rint(roll / step).astype(int), 0, K - 1)
    gap = np.hypot(Ph[kr, np.arange(N), 0] - Qh[kr, np.arange(N), 0], Ph[kr, np.arange(N), 1] - Qh[kr, np.arange(N), 1])
    return {"c": [0, nc], "speed_m_step": run_step,
            "touch": [np.array(_enc(tt[h])).reshape(2, 2, nc).tolist() for h in range(n_hits)],
            "gap": np.round(gap.reshape(2, 2, nc)[0], 2).tolist(),
            "bs": np.array(_enc(bs)).reshape(2, 2, nc)[1].tolist()}


def clip_motion(cat: int, anim: int, acts) -> dict | None:
    """One skill clip in real frames from its first frame: `path` (root x, z per frame,
    `er-mechanics-ashes.evasion_motion`, the reader the rolls come from), `windows` (unconditional
    JumpTable 8 invincibility, `_jt8_unconditional`), `active` (its own damaging hitboxes, the
    melee and bullet events of `acts`, the clip's `skill_profile` entries), `ready` (first R1
    cancel, else first dodge cancel, else its last frame) and `turn` (degrees a frame the caster
    can turn, not locked on: `er-mechanics-reach.turn_rate_at`, 0 under Disable Turning)."""
    m = AS.evasion_motion(cat, anim)
    if not m or not m["path"]:
        return None
    _, reach, _ = AS._pose()
    _, _, events = reach.resolve_clip(cat, anim)
    events = events or []
    to_real = AS.ATK.clip_to_real(events)

    def real(s):
        return to_real(s) * AS.TAE_FPS

    windows = sorted((round(real(e.start), 1), round(real(min(e.end, 1e3)), 1))
                     for e in events if AS._jt8_unconditional(e))
    active = sorted((real(x["frames"][0] / AS.TAE_FPS),
                     real((x["frames"][1] if x["frames"][1] is not None else x["frames"][0] + 1) / AS.TAE_FPS))
                    for x in acts or () if x["kind"] in ("melee", "bullet"))
    n = len(m["path"])
    cg = np.linspace(0.0, 4.0 * n / AS.TAE_FPS + 1.0, 16 * n + 2)
    clip_t = np.interp(np.arange(n), np.array([real(c) for c in cg]), cg)
    turns, disabled = reach.turn_windows(events)
    turn = np.array([reach.turn_rate_at(c, turns, disabled, "unlocked") for c in clip_t]) / AS.TAE_FPS
    ready = next((v for v in (m["ready"], m["dodge"]) if v), float(n - 1))
    return {"path": np.array(m["path"], float), "windows": windows, "active": active, "ready": float(ready),
            "turn": turn}


def escape_reason(path, windows, active, radius: float) -> tuple[bool, str]:
    """(an escape?, why) of one clip (or entry plus follow-up), from its data alone (user,
    2026-10-19: a motion is an escape only for the frames it is invincible, or moves the body with
    no hitbox of its own active). A clip with its own hitbox is an attack: the motion up to its last
    active frame is the lunge into that hit (windup and active frames alike), which the interrupt
    and trade path scores, not this one; only what it moves after its last hit counts. A clip
    without one counts all of it. Moving means the body clears where it stood: two push-capsule
    radii (`radius` each)."""
    path = np.asarray(path, float)
    inv = [(a, b) for a, b in windows if b > a]
    if inv:
        return True, "invincible " + ", ".join(f"f{a:g}-{b:g}" for a, b in inv)
    if active:
        last = int(math.ceil(max(b for _, b in active)))
        lunge = float(np.hypot(*path[:last + 1].T).max()) if last < len(path) else float(np.hypot(*path.T).max())
        after = float(np.hypot(*(path[last:] - path[min(last, len(path) - 1)]).T).max()) if last < len(path) else 0.0
        if after >= 2 * radius:
            return True, f"moves {after:.2f} m after its last hit (f{last})"
        return False, f"attack: {lunge:.2f} m lunge by its last hit f{last}, {after:.2f} m after it, no i-frames"
    far = float(np.hypot(*path.T).max())
    if far >= 2 * radius:
        return True, f"moves {far:.2f} m with no hitbox of its own"
    return False, f"moves {far:.2f} m, no hitbox and no i-frames (a stance or standing clip)"


def skill_moves(t, A, sid: int, wid: int, grip: str, ash: str, radius: float) -> tuple[list, list]:
    """(escapes, report) of one skill on one carrier. Every clip of its profile is read on its
    own (the no-FP copies left out): the first clip (a stance skill's entry) as it is, and every
    later one (a stance's moves and follow-ups) played after the first, its frames, windows and
    hitboxes moved by the first clip's length (`INFERRED`: a later clip of a skill is reached from
    its first). An escape is a clip `escape_reason` admits; `fp` is the FP of the clips it plays
    that charge FP, at the skill's own cost (`fp_cost` of its first input that casts it).
    `report` is [(clip name, admitted, reason)] for every clip."""
    prof = A.skill_profile(t, sid, wid, 0, grip)
    cat = A.SKILL_TAE_BASE + t.arts[sid]["swordArtsTypeNew"]
    cost = next((c for c in (t.fp_cost(sid, k) for k in ("L2", "R2", "L1", "R1")) if c and c > 0), 0)
    anims = [a for a in sorted(prof["anims"]) if a not in set(prof["no_fp_anims"])]
    first = anims[0] if anims else None
    pre = clip_motion(cat, first, prof["anims"][first]) if first is not None else None
    out, report = [], []
    for a in anims:
        c = clip_motion(cat, a, prof["anims"][a])
        name = f"{ash}/{a}"
        if c is None:
            report.append((name, False, "no clip"))
            continue
        fp = sum(cost for b in ([a] if a == first or pre is None else [first, a])
                 if any(x["kind"] == "fp" for x in prof["anims"][b]))
        if a != first and pre is not None:
            k = len(pre["path"])
            c = {"path": np.concatenate([pre["path"], pre["path"][-1] + c["path"]]),
                 "windows": pre["windows"] + [(x + k, y + k) for x, y in c["windows"]],
                 "active": pre["active"] + [(x + k, y + k) for x, y in c["active"]],
                 "ready": c["ready"] + k, "turn": np.concatenate([pre["turn"], c["turn"]])}
        ok, why = escape_reason(c["path"], c["windows"], c["active"], radius)
        report.append((name, ok, why))
        if ok:
            out.append({"name": name, "path": c["path"], "windows": [w for w in c["windows"] if w[1] > w[0]]
                        or [(0.0, 0.0)], "iframes": 0.0, "ready": c["ready"], "turn": c["turn"], "fp": float(fp),
                        "far": round(float(np.hypot(*c["path"].T).max()), 2), "why": why})
    return out, report


def headed(moves: list) -> list:
    """Each of `moves` aimed at every `MOVE_HEADINGS` heading from facing the attacker: each frame
    the caster turns toward the heading by at most that frame's `turn` (not locked on, so what the
    clip's TimeAct allows; an entry without `turn` turns at once), and the frame's root
    motion goes the way he faces then. `face` is the heading he ends on, which the punish after it
    turns back from (`escape_table`)."""
    out = []
    for m in moves:
        path = np.asarray(m["path"], float)
        step = np.diff(path, axis=0, prepend=path[:1])
        rate = np.radians(np.asarray(m.get("turn", np.full(len(path), np.inf)), float))
        for h in MOVE_HEADINGS:
            psi, cur, pts = 0.0, np.zeros(2), []
            for f in range(len(path)):
                psi += float(np.clip(_wrap(h - psi), -rate[f], rate[f]))
                dx, dz = _rot(step[f, 0], step[f, 1], psi)
                cur = cur + (dx, dz)
                pts.append((float(cur[0]), float(cur[1])))
            out.append({**{k: v for k, v in m.items() if k != "turn"}, "path": pts, "face": psi,
                        "name": f"{m['name']} {math.degrees(h):+.0f}"})
    return out


#: Skill names `er-builds-ladder.ash_ban` refuses: no defender on the ladder can cast them.
def ladder_banned(ash: str) -> bool:
    return bool(_sib("er-builds-ladder").ash_ban(ash))


# --------------------------------------------------------------------------------------------
# per sweep row

def _heal(t, sids, max_hp):
    hp = 0.0
    for sid in sids or ():
        s = t.speffect_summary(sid)
        e = s.get("effects") or {}
        if s.get("can_target_self") and (e.get("changeHpRate", 0) < 0 or e.get("changeHpPoint", 0) < 0):
            hp += -(e.get("changeHpRate", 0.0) / 100.0 * max_hp + e.get("changeHpPoint", 0))
    return hp


class Rows:
    """Cast profiles of sweep rows on one ash-table `Table` (the sweep's stats and armor)."""

    def __init__(self, opp: Opponent, stats: dict, armor: list, max_hp: float, carry: bool = False):
        self.TAB = _sib("er-mechanics-ash-table")
        self.tab = self.TAB.Table("Dagger", "Keen", stats, armor, True)
        self.opp, self.max_hp = opp, max_hp
        #: Each cast starts from carried poise on both sides (`carried_states`, `poise_carries`).
        self.carry = carry
        self.sa = EXCH.SaRates()
        self._memo = {}
        #: Escape tables by key (`escape`), and the clip tracks they are built from.
        self._esc = {}
        self._tracks = {}
        self.bs = _backstab()
        self.limits = self.bs.throw_limits()
        #: The defender's movement skills (`set_movers`), each at every `MOVE_HEADINGS` heading.
        self.movers = []

    def set_movers(self, xs) -> list:
        """The escape clips of the sweep rows' skills that the ladder allows (`skill_moves`,
        `ladder_banned`), the attacker's own ash among them. Each skill is read on the first of its
        carriers in `xs`, one-handed first (`INFERRED`: one carrier's clips stand for the skill;
        they differ by weapon class and grip). `self.move_report` keeps every clip's verdict."""
        A, t = self.TAB.A, self.tab.t
        radius = A._pose()[1].PUSH_CAPSULE_RADIUS
        first = {}
        for x in sorted(xs, key=lambda x: x["grip"] != "1H"):
            first.setdefault(x["ash"], x)
        out, self.move_report = [], []
        for ash, x in sorted(first.items()):
            if ladder_banned(ash):
                self.move_report.append((ash, False, "banned by the ladder"))
                continue
            try:
                got, rep = skill_moves(t, A, t.find_arts(ash), t.find_weapon(x["weapon"]), x["grip"], ash, radius)
            except (SystemExit, KeyError, FileNotFoundError) as exc:
                self.move_report.append((ash, False, f"unreadable: {type(exc).__name__}"))
                continue
            out += got
            self.move_report += rep
        self.movers = headed(out)
        return out

    def armor_poise(self) -> float:
        return round(self.tab.armor * 1000)

    def cast(self, x):
        """(hits [(frame, poise, damage, heal)], windows, effect frames, gate) of one row."""
        c = self._cast(x)
        return c["hits"], c["windows"], c["fx"], c["gate"]

    def _cast(self, x) -> dict:
        """`cast` plus what the escape table needs: the skill, its opening, the lead-in and the
        scorer's hit rows in the order of `hits`."""
        TAB, tab = self.TAB, self.tab
        A = TAB.A
        if tab.weapon != x["weapon"]:
            # The reach module keeps every skill clip's hit samples it measured; one weapon's are
            # enough here, since rows come grouped by weapon.
            A._pose()[1]._SKILL.clear()
        if (tab.weapon, tab.affinity) != (x["weapon"], x["affinity"]):
            tab.set_weapon(x["weapon"], x["affinity"])
        sid = tab.t.find_arts(x["ash"])
        # The opening depends on weapon class and grip (`skill_profile`, GetSwordArtsDiffCategory).
        # A stance row's button picks its attack (`A.stance_row`).
        prof = A.skill_profile(tab.t, sid, tab.wid, tab.level, x["grip"], x.get("stance"))
        main = A.main_anim(prof)
        acts = prof["anims"].get(main, [])
        # Frames are cast-relative: a clip played before the opening (`A.lead_in`) comes first.
        lead = A.lead_in(tab.t, sid, prof)
        windows = []
        pre = A.lead_in_hyperarmor(prof) if lead else []
        for e in pre + acts:
            at = 0.0 if e in pre else lead
            if e["kind"] == "hyperarmor" and e["frames"][1] is not None:
                tr = tab.t.reg.toughness.get(e["toughness_row"], {})
                floor = (tr.get("minToughness", 0) or 0) / 100.0 if not tr.get("isNonEffectiveCorrectionForMin") else 0.0
                windows.append((e["frames"][0] + at, e["frames"][1] + at, e["poise_bonus_menu"], e["pvp_poise_damage_taken"],
                                floor, e.get("pvp_hp_damage_taken", 1.0)))
        windows = sorted(windows)[:INTR.MAX_WINDOWS]
        gate = x.get("gate")
        info = {"sid": sid, "prof": prof, "main": main, "lead": lead, "windows": windows}
        if x["cat"] == "utility":
            fx = [e["frames"][0] for e in acts
                  if e["kind"] == "iframes" or e["kind"] == "parry"
                  or (e["kind"] == "speffect" and e.get("effect") in ("buff", "stance effect"))]
            return {**info, "hits": [], "raw": [], "fx": sorted(fx)[:1],
                    "gate": gate if gate is not None else x["last"]}
        two = x["grip"] == "2H"
        sc = TAB.S.AshScorer(tab.lev, x["weapon"], x["affinity"], sid, two, tab.defender, None, x.get("stance"))
        hits = sc.hits(tab.stats)
        A.pvp_damage(tab.t, tab.wid, hits, tab.defender, TAB.OPT.DEF)
        if x["cat"] == "bullet":
            hits = [h for h in hits if h["kind"] == "bullet" and tab.travel(h["root_bullet"]) > TAB.MIN_TRAVEL_M]
        delay = self.opp.contact_delay(x["weapon"], x["grip"])
        on_hit = {}
        for a, xs in prof["anims"].items():
            for e in xs:
                if e["kind"] == "melee":
                    on_hit[(a, e["frames"][0], e["atk_row"])] = e.get("on_hit_speffects")
        out = []
        for h in hits:
            melee = h["kind"] == "melee"
            frame = h["frame"] + lead + (delay if melee else 0.0)
            heal = _heal(tab.t, on_hit.get((h.get("anim"), h["frame"], h["atk_row"])), self.max_hp) if melee else 0.0
            pz = (h.get("poise") or 0.0) * h.get("count", 1) * self.sa(h["atk_row"])
            out.append(((round(frame, 2), round(pz, 2), round(h["pvp_damage_total"], 1), round(heal, 1)), h))
        out.sort(key=lambda p: p[0])
        out = out[:MAX_MY_HITS]
        raw, out = [h for _, h in out], [p for p, _ in out]
        last = max(h[0] for h in out) if out else 0.0
        return {**info, "hits": out, "raw": raw, "fx": [], "gate": gate if gate is not None else last}

    def _track(self, cat: int, anim: int, frames: float) -> dict:
        key = (cat, anim)
        have = self._tracks.get(key)
        if have is None or have["f"][-1] < frames:
            events = (self.TAB.A.skill_tae(self.tab.t, self._sid) or {}).get(anim) or []
            have = self._tracks[key] = clip_track(cat, anim, events, max(frames, 150.0))
        return have

    def roll_gate(self, c, cat: int) -> float:
        """My first roll frame on the clock of the row's gate: the gate moved by the opening's roll
        cancel less its R1 cancel (`er-mechanics-disengage.clip` gates); the gate itself when the
        clip gives no roll cancel."""
        clip = self.TAB._disengage().clip(cat, int(c["main"])) if c["main"] is not None else None
        g = (clip or {}).get("gates") or {}
        if c["gate"] is None or g.get("roll") is None or g.get("r1") is None:
            return c["gate"]
        return round(c["gate"] - g["r1"] + g["roll"], 1)

    def escape(self, c, x, D: float) -> tuple[str, dict]:
        """(key, table) of one damaging cast's geometry pass (module docstring). The table depends
        on the weapon, skill, grip, distance and hit frames, not on the affinity or the stats, so
        the rows of one weapon share it."""
        A, tab = self.TAB.A, self.tab
        sid, lead = c["sid"], c["lead"]
        cat = A.SKILL_TAE_BASE + tab.t.arts[sid]["swordArtsTypeNew"]
        roll_gate = self.roll_gate(c, cat)
        steps = backstep_motion(A)
        movers = hashlib.sha256(json.dumps([m["name"] for m in steps + self.movers]).encode()).hexdigest()[:12]
        key = "esc|" + json.dumps([x["weapon"], x["ash"], x["grip"], round(D, 2), c["gate"], roll_gate,
                                   [h[0] for h in c["hits"]], movers])
        if key in self._esc:
            return key, self._esc[key]
        self._sid = sid
        con = A._hit_contacts(tab.t, tab.wid, sid, c["raw"])
        cols = {k: [] for k in "TAXZRH"}
        for i, ((_, _, source, g), h) in enumerate(zip(con, c["raw"])):
            if g is None:
                # No shape at all: it touches anyone when it opens, the landing's own rule.
                g = {"T": np.array([c["hits"][i][0] - lead]), "X": np.zeros(1), "Z": np.full(1, -99.0),
                     "R": np.full(1, 1e3)}
            anchor = np.full_like(g["T"], g["T"].min()) if source == "flight" else g["T"]
            tr = self._track(cat, int(h["anim"]), float(g["T"].max()) + 2) if h.get("anim") is not None else None
            rx = np.interp(anchor, tr["f"], tr["x"]) if tr else 0.0
            rz = np.interp(anchor, tr["f"], tr["z"]) if tr else 0.0
            for k, v in (("T", g["T"] + lead), ("A", anchor + lead), ("X", g["X"] - rx), ("Z", g["Z"] - rz),
                         ("R", g["R"]), ("H", np.full(len(g["T"]), i))):
                cols[k].append(np.asarray(v, float))
        samples = {k: np.concatenate(v) for k, v in cols.items()}
        horizon = max([c["gate"] or 0.0, roll_gate or 0.0, float(samples["T"].max())]) + 2
        main = self._track(cat, int(c["main"]), horizon - lead)
        pre = self._track(cat, int(c["main"]) - 1, lead) if lead else None
        track = cast_track(main, pre, lead)
        K = int(math.ceil(horizon / ESCAPE_STEP)) + 1
        track = {k: v[:K] for k, v in track.items()}
        bsm = self.bs
        # The backstep goes before the rolls: `er-mechanics-ash-whiff` takes the later entry on a
        # tie, so a backstep no better than a roll leaves the roll's result.
        table = escape_table(track, samples, len(c["hits"]), D, steps + A.roll_motion() + self.movers,
                             A._pose()[1].PUSH_CAPSULE_RADIUS,
                             self.limits, bsm.RUN_SPEED * ESCAPE_STEP / AS.TAE_FPS, bsm.TARGET_BEHIND_M)
        table.update(roll_gate=roll_gate, blocks=bool(bsm.skill_blocks_backstab(tab.t, sid)))
        self._esc[key] = table
        return key, table

    def _sim(self, hits, windows, distance: float, gate: float, fx, poise: float) -> dict:
        key = sim_key(hits, windows, distance, gate, fx, poise)
        if key not in self._memo:
            states = carried_states(self.opp, hits[0][1] if hits else 0.0, poise) if self.carry else None
            self._memo[key] = simulate(self.opp, [(h[0], h[1]) for h in hits], windows, distance, gate, poise, fx,
                                       states=states)
        return self._memo[key]

    def profile(self, x, poise: float, distance: float | None = None, spread=None) -> dict:
        """The interrupt profile of row `x` at my `poise`: at `distance`, else at the duel's
        `spread` [[distance, weight], ...] (`duel_spread`, `spread_profile`), else at the fixed
        `REACH_SHARE` x the ash's reach."""
        c = self._cast(x)
        hits, windows, fx, gate = c["hits"], c["windows"], c["fx"], c["gate"]
        if x["cat"] == "bullet":
            reach = (x.get("reach") or [0, 0])[1]
        else:
            reach = (x.get("melee_reach") or [0])[0]
        fixed = REACH_SHARE * reach if reach else EXCHANGE_DISTANCE_M
        if distance is None and spread:
            return self.spread_profile(c, x, poise, spread, fixed, reach)
        if distance is None:
            distance = fixed
        sim = self._sim(hits, windows, distance, gate, fx, poise)
        out = {"hits": hits, "windows": windows, "fx_frames": fx, "gate": gate, "D": round(distance, 2),
               "poise": poise, "sim": sim, "fx": sim["fx"]}
        if hits:
            try:
                # `_escape` rides back to `profiles`, which keeps it in the escape store and leaves the
                # key alone in the profile.
                out["escape"], out["_escape"] = self.escape(c, x, round(distance, 2))
            except Exception as exc:  # no clip or shapes: the whiff falls back to hit frames alone
                out["escape_error"] = f"{type(exc).__name__}: {exc}"
        return out

    def spread_profile(self, c, x, poise: float, spread, fixed: float, reach: float | None) -> dict:
        """The profile over the duel's distances (`duel_spread`): per distance, the hits that touch
        him standing there (the escape table's `t0`; with no table, every hit while the distance is
        within the ash's `reach`, `INFERRED`), a simulation of those hits alone, so a hit that
        cannot reach neither lands nor wears his poise, and the escape table the whiff reply reads.

        A player presses an ash only where it reaches (user, 2026-10-19, of a spread that cast
        Unsheathe at 10-17 m: "Aren't there occasions when its more useful than storm blade?"): a
        distance where none of its hits touches him standing (`reach` False on the part) is never
        cast at, so it is neither simulated nor counted. `share` is the duel's time at distances
        where it reaches. `sim` is the weighted sum over those distances alone, which is `share` x
        the cast's value there with the distances weighted by the wave's time, so `multiplier` is
        that product; an ash that reaches nowhere on the wave is worth 0. A cast with no hit (a step
        or buff) has no range and counts every distance. The opponent attacking into the cast is
        taken to stand where the wave puts him when my hits come out (`INFERRED`: his run-in is not
        walked into them). `D` stays the fixed `REACH_SHARE` distance, the in-reach case the
        perfect block reads.

        Each node stands for a cell of the wave, and a cell is credited for the part of it the
        cast covers, not all or nothing at its node (`covered_cells`): Unsheathe's 4.1 m against
        the 4.38 m node, whose cell runs 3.3-5.9 m, is credited the 3.3-4.1 m part's time, valued
        at that part's time-weighted mean distance, one extra profile (`boundary` on the part)."""
        hits, windows, fx, gate = c["hits"], c["windows"], c["fx"], c["gate"]
        parts, escapes, errors, sims = [], {}, [], {}

        def touches(d):
            """(escape key or None, which hits touch him standing at `d`)."""
            if not hits:
                return None, []
            try:
                key, table = self.escape(c, x, round(d, 2))
                escapes[key] = table
                return key, [t >= 0 for t in table["t0"]]
            except Exception as exc:  # no clip or shapes: reach alone says what touches
                errors.append(f"{type(exc).__name__}: {exc}")
                return None, [bool(reach) and d <= reach] * len(hits)

        nodes = spread["nodes"]
        tested = [touches(n[0]) for n in nodes]
        if hits:
            credit = covered_cells(spread["wave"], [n[0:1] + n[2:4] for n in nodes],
                                   [any(t) for _, t in tested], reach)
        else:
            credit = [(i, n[0], n[1], False) for i, n in enumerate(nodes)]
        by_node = {i: (d, w, edge) for i, d, w, edge in credit}
        for i, n in enumerate(nodes):
            d, w, boundary = by_node.get(i, (n[0], 0.0, False))
            key, touch = touches(d) if boundary else tested[i]
            part = {"D": round(d, 2), "w": round(w, 6), "node": n[0], "touch": touch,
                    "reach": w > 0 and (any(touch) or not hits)}
            if boundary:
                part["boundary"] = True
            if key:
                part["escape"] = key
            if part["reach"]:
                mine = [h for h, t in zip(hits, touch) if t]
                sim = self._sim(mine, windows, d, gate, fx, poise)
                sims[sim_key(mine, windows, d, gate, fx, poise)] = sim
                p, it = [], iter(sim["p"])
                for t in touch:
                    p.append(next(it) if t else 0.0)
                part["sim"] = {**sim, "p": p}
            parts.append(part)
        cast = [q for q in parts if q["reach"]]
        agg = {"p": [sum(q["w"] * q["sim"]["p"][i] for q in cast) for i in range(len(hits))]}
        for k in ("taken", "saved", "broken", "stopped_b", "approach", "follow_hp", "follow_hits"):
            agg[k] = sum(q["w"] * q["sim"].get(k, 0.0) for q in cast)
        agg["fx"] = [sum(q["w"] * q["sim"]["fx"][i] for q in cast) for i in range(len(fx))]
        out = {"hits": hits, "windows": windows, "fx_frames": fx, "gate": gate, "D": round(fixed, 2),
               "poise": poise, "sim": agg, "fx": agg["fx"], "spread": parts, "_sims": sims,
               "share": round(sum(q["w"] for q in cast), 6)}
        if escapes:
            out["_escapes"] = escapes
        if errors:
            out["escape_error"] = errors[0]
        return out


def sim_key(hits, windows, distance: float, gate: float, fx, poise) -> str:
    """The memo key of one `simulate` call, as JSON text: my hits' frames and poise, my windows,
    the distance, the gate, the effect frames and my poise."""
    return json.dumps([[[h[0], h[1]] for h in hits], [list(w) for w in windows], round(distance, 2),
                       round(gate, 1), list(fx), poise])


def _read_sims(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _write_sims(path: Path, sims: dict) -> None:
    """Replace `path` whole, so a run reading it meanwhile never sees half a file, and delete the
    files of other stamps nothing has written for `SIMS_STALE_S`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(sims))
    os.replace(tmp, path)
    now, mine = time.time(), path.name.split("-", 1)[0]
    for f in path.parent.glob("*.json"):
        try:
            if not f.name.startswith(f"{mine}-") and now - f.stat().st_mtime > SIMS_STALE_S:
                f.unlink()
        except OSError:
            pass


def escape_path(st: str | None = None) -> Path:
    return ESCAPE_DIR / f"{st or stamp()}-escape.json"


_ESC = {}


def escape_store() -> dict:
    """{key: escape table} of this code stamp, read once per process; `profiles` keeps it current."""
    if "doc" not in _ESC:
        _ESC["doc"] = _read_sims(escape_path())
    return _ESC["doc"]


def row_key(x) -> str:
    """A row with a lead-in (`A.lead_in`, set as `lead` on the row) is its own entry, so a profile
    cached from the opening alone is not reused for it; so is a stance row's button
    (`A.variant_key`: Unsheathe's R1 and R2 slashes are different casts)."""
    lead = f"|lead{x['lead']:g}" if x.get("lead") else ""
    return f"{x['weapon']}|{x.get('affinity', 'Keen')}|{x['ash']}|{x['grip']}{lead}{AS.variant_key(x)}"


#: Throttle (user, 2026-10-08): never run on core 0, and use at most this share of any other core.
#: Every process gets a CPU-time interval timer (`ITIMER_PROF`); each `THROTTLE_SLICE_S` of CPU it
#: sleeps `THROTTLE_SLICE_S x (1 / THROTTLE_SHARE - 1)`, and each process is pinned to its own core,
#: so no core runs above the share for longer than one slice (or one uninterruptible C call).
THROTTLE_SHARE = 0.9
THROTTLE_SLICE_S = 0.05
THROTTLE_SKIP_CPUS = {0}


#: The CPUs this process could use when the module loaded, before `throttle` pinned it to one.
_START_CPUS = sorted(os.sched_getaffinity(0))


def allowed_cpus() -> list[int]:
    cpus = sorted(set(_START_CPUS) - THROTTLE_SKIP_CPUS)
    return cpus or list(_START_CPUS)


def _throttle_tick(signum, frame):
    time.sleep(THROTTLE_SLICE_S * (1.0 / THROTTLE_SHARE - 1.0))


def throttle(cpus) -> None:
    """Pin this process to `cpus` and cap it at `THROTTLE_SHARE` of a core. A forked child keeps
    the handler but not the timer, so each worker calls this again for its own core."""
    os.sched_setaffinity(0, set(cpus))
    # `SCHED_IDLE`: run only on cycles nothing else wants (user, 2026-10-08: a game dropped to
    # 6 fps under 14 workers at 90%).
    os.sched_setscheduler(0, os.SCHED_IDLE, os.sched_param(0))
    signal.signal(signal.SIGPROF, _throttle_tick)
    signal.setitimer(signal.ITIMER_PROF, THROTTLE_SLICE_S, THROTTLE_SLICE_S)
    # Stop the timer before interpreter shutdown: a SIGPROF that lands after the handler is torn
    # down takes the default action and kills the process, so a finished run exited 155.
    atexit.register(signal.setitimer, signal.ITIMER_PROF, 0)


@contextlib.contextmanager
def caller_throttled(cpu: int):
    """`throttle` this process to `cpu` for the length of a pool, then give back the CPUs, policy
    and timer it had. Left pinned, every pool forked after it inherited the one CPU: on 2026-10-20
    combo-loadout's whiff stage put its ten workers on CPU 1 after this pass and ran at 10% each."""
    aff = os.sched_getaffinity(0)
    policy, param = os.sched_getscheduler(0), os.sched_getparam(0)
    handler = signal.getsignal(signal.SIGPROF)
    throttle([cpu])
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_PROF, 0)
        signal.signal(signal.SIGPROF, handler)
        os.sched_setaffinity(0, aff)
        try:
            os.sched_setscheduler(0, policy, param)
        except PermissionError:
            # Leaving `SCHED_IDLE` needs `RLIMIT_NICE` headroom; staying idle costs only priority.
            pass


_W = {}


def _worker_init(cores, counter):
    with counter.get_lock():
        i = counter.value
        counter.value += 1
    throttle([cores[i % len(cores)]])


def _chain_work(chunk):
    """{sim key: simulation} of one chunk of `ChainTrades.prepare`'s strings."""
    ct = _W["chain"]
    return {k: ct._sim(*c) for k, c in chunk}


def _work(chunk):
    rows, poise, distance, spread = _W["rows"], _W["poise"], _W["distance"], _W.get("spread")
    out = {}
    for x in chunk:
        try:
            out[row_key(x)] = rows.profile(x, poise, distance, spread)
        except Exception as exc:  # a row the extractors cannot read is reported and left neutral
            out[row_key(x)] = {"error": f"{type(exc).__name__}: {exc}"}
    return out


def profiles(xs: list, cache_path: Path, stats: dict, armor: list, max_hp: float, poise: float | None = None,
             distance: float | None = None, jobs: int = 12, log=print, throttled: bool = True,
             arena_m: float | None = None) -> dict:
    """{row key: profile} for every row in `xs`, read from `cache_path` when its stamp and settings
    match, the rest computed (`jobs` forked workers, grouped by weapon) and written back as it goes.
    `throttled`: the caller runs on the first allowed core and each worker on one of the others
    (`throttle`), so `jobs` is at most the allowed cores less one.

    `arena_m` (a duel's starting separation, `--arena-m`): each row is valued over the duel's
    distance spread (`duel_spread` from `arena_m` down to contact, the floor rising to the pool's
    melee range, `pool_melee_m`) instead of at one fixed distance. The spread is part of the
    settings, so a cache made at the fixed distance is recomputed."""
    # The pool answers with a read as well as a reaction, from carried poise (`with_read`,
    # `carried_states`), as it answers the R1, L1 and dual strings (`ChainTrades`).
    opp = with_read(Opponent()) if arena_m else None
    spread = duel_spread_for(arena_m, opp) if arena_m and distance is None else None
    # The opponent's arrays are part of the settings (`Opponent.fingerprint`, about 1 s warm): the
    # pool and sequence caches it is read from are kept by their own scripts, so their code is not
    # in this script's stamp (the code stamp section) and their data stands in for it.
    opp = opp or with_read(Opponent())
    settings = {"stamp": stamp(), "stats": stats, "armor": armor, "max_hp": max_hp, "poise": poise,
                "distance": distance, "points": REACT_POINTS, "read": AS.REACT_SHARE, "carried": poise_carries(),
                "spread": spread, "stagger": STAGGER_MODEL, "pool": opp.fingerprint()}
    if spread:
        log("interrupt: duel distance spread " + ", ".join(f"{n[0]:g} m x{n[1]:.3f}" for n in spread["nodes"]))
    doc = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    have = doc.get("rows", {}) if doc.get("settings") == settings else {}
    todo = [x for x in {row_key(x): x for x in xs}.values() if row_key(x) not in have]
    log(f"interrupt: {len(have)} cached, {len(todo)} to compute -> {cache_path}")
    if todo:
        import multiprocessing as mp
        rows = Rows(opp, stats, armor, max_hp, carry=settings["carried"])
        moves = rows.set_movers(xs)
        log(f"interrupt: {len(moves)} movement skills the defender may escape with: "
            + ", ".join(f"{m['name']} ({m['far']} m, ready f{m['ready']:g})" for m in moves))
        p = rows.armor_poise() if poise is None else poise
        tag = "-carried" if rows.carry else ""
        sims_path = SIMS_DIR / f"{stamp('sim')}-{settings['pool']}-poise{p:g}{tag}.json"
        rows._memo = _read_sims(sims_path)
        log(f"interrupt: {len(rows._memo)} simulations kept from other stat blocks ({sims_path})")
        esc_path = escape_path(settings["stamp"])
        rows._esc = escape_store()
        log(f"interrupt: {len(rows._esc)} escape tables kept ({esc_path})")
        _W.update(rows=rows, poise=p, distance=distance, spread=spread)
        # One chunk per weapon, affinities together: the casts of one weapon share their timing, so
        # the simulation memo serves every affinity after the first.
        groups = {}
        for x in todo:
            groups.setdefault(x["weapon"], []).append(x)
        chunks = sorted((sorted(g, key=lambda x: x["affinity"]) for g in groups.values()), key=len, reverse=True)
        # The chunks run largest first, so a count of weapons over-states what is left early on;
        # the row count beside it is what `er-mechanics-pipeline-watch.py` reads the stage by.
        done, rows_done = 0, 0
        ctx = mp.get_context("fork")
        init, args = None, ()
        caller = contextlib.ExitStack()
        if throttled:
            cpus = allowed_cpus()
            caller.enter_context(caller_throttled(cpus[0]))
            workers = cpus[1:] or cpus
            jobs = max(1, min(jobs, len(workers)))
            init, args = _worker_init, (workers, ctx.Value("i", 0))
            log(f"interrupt: throttled, caller on CPU {cpus[0]}, {jobs} workers on CPUs {workers[:jobs]}, "
                f"{THROTTLE_SHARE:.0%} each")
        with caller, ctx.Pool(jobs, initializer=init, initargs=args) as pool:
            for res in pool.imap_unordered(_work, chunks):
                for r in res.values():
                    if "_escape" in r:
                        rows._esc[r["escape"]] = r.pop("_escape")
                    rows._esc.update(r.pop("_escapes", {}))
                have.update(res)
                for r in res.values():
                    if "_sims" in r:
                        rows._memo.update(r.pop("_sims"))
                    elif "sim" in r:
                        rows._memo[sim_key(r["hits"], r["windows"], r["D"], r["gate"], r["fx_frames"],
                                           r["poise"])] = r["sim"]
                done += 1
                rows_done += len(res)
                log(f"  {done}/{len(chunks)} weapons, {rows_done}/{len(todo)} rows")
                if done % 10 == 0 or done == len(chunks):
                    cache_path.write_text(json.dumps({"settings": settings, "rows": have}))
                    _write_sims(sims_path, rows._memo)
                    _write_sims(esc_path, rows._esc)
    return have


# --------------------------------------------------------------------------------------------

def selftest() -> int:
    fails = []

    def check(cond, msg):
        print(("ok   " if cond else "FAIL ") + msg)
        if not cond:
            fails.append(msg)

    # First, so its traced run starts from a fresh process: the checks after it fill the sibling
    # modules' in-process memos, and a memo hit runs none of the code it saves.
    stamp_selftest(check)
    un = {"weapon": "Uchigatana", "affinity": "Keen", "ash": "Unsheathe", "grip": "1H"}
    keys = [row_key({**un, "lead": 8.0, "stance": "r1"}), row_key({**un, "lead": 8.0, "stance": "r2"}), row_key(un)]
    check(len(set(keys)) == 3 and keys[2] == "Uchigatana|Keen|Unsheathe|1H",
          f"row_key keeps a stance row's button apart, a plain row's key unchanged: {keys}")

    class Fake:
        P = np.array([50.0])
        w = np.array([1.0])
        offs = np.array([10.0])
        reach = np.array([3.0])
        k = 7.5
        t = np.array([[5.0, 20.0]])
        q = np.array([[60.0, 60.0]])
        m = np.array([[True, True]])
        win = np.zeros((5, 1, 1))
        win[0] = win[1] = -1.0
        win[3] = 1.0
        inv = np.full((2, 1, 1), -1.0)
        # The stagger (`stagger_arrays`): both hits lock a broken me for 20 frames on a first
        # break, 10 on a second, 5 on a third, none on a fourth; no knockback, no follow-up.
        bt, bq, bm = t, q, m
        bf = np.zeros((1, 2), bool)
        be = np.array([[[20.0, 10.0, 5.0, 0.0]] * 2])
        bk = np.zeros((1, 2))

    f = Fake()
    # B hits me at 15 (60 > 40 poise) before my hit at 20: my hit is lost, and his next hit at 30,
    # inside the 20-frame stagger, lands although my gate (25) is past.
    r = simulate(f, [(20.0, 100.0)], [], 2.5, 25.0, 40.0)
    check(r["p"] == [0.0] and r["taken"] == 2 * B_HIT_HP and r["follow_hits"] == 1.0,
          "a B hit before mine breaks me, cancels my hit, and his next hit inside the stagger lands past my gate")
    r = simulate(f, [(20.0, 100.0)], [], 2.5, 40.0, 40.0)
    check(r["taken"] == 2 * B_HIT_HP, "B keeps hitting a broken player while the stagger holds him")
    stagger_selftest(check, f)
    # My hit at 12 breaks B (100 > 50) before its first hit at 15: nothing taken.
    r = simulate(f, [(12.0, 100.0)], [], 2.5, 40.0, 40.0)
    check(r["p"] == [1.0] and r["taken"] == 0.0 and r["stopped_b"] == 1.0, "my hit first breaks B and stops it")
    # Simultaneous: both land.
    r = simulate(f, [(15.0, 100.0)], [], 2.5, 40.0, 40.0)
    check(r["p"] == [1.0] and r["taken"] == B_HIT_HP, "a simultaneous hit is a trade")
    # Hyperarmor covering 0-30 with +100 holds through B's first hit; B's second at 30 lands after it.
    r = simulate(f, [(25.0, 10.0)], [(0.0, 30.0, 100.0, 1.0, 0.0, 1.0)], 2.5, 40.0, 40.0)
    check(r["p"] == [1.0], "hyperarmor holds my hit")
    # Late break after the last hit: B breaks me at 15 (stagger to 35) and again at 30 inside it,
    # a second break whose stagger escapes 10 frames on (to 40); frames saved = gate - 40.
    r = simulate(f, [(12.0, 10.0)], [], 2.5, 60.0, 40.0)
    check(r["p"] == [1.0] and abs(r["saved"] - 20.0) < 1e-9,
          f"a break after my last hit saves the recovery past the stagger's escape, the second break's gate "
          f"the next one ({r['saved']:g})")
    # Distance beyond B's reach: B runs 1 m (7.5 frames) first.
    r = simulate(f, [(20.0, 100.0)], [], 4.0, 40.0, 40.0)
    check(abs(r["approach"] - 7.5) < 1e-9 and r["p"] == [1.0], "B out of reach approaches first")
    chain_selftest(check, f)
    spread_selftest(check, f)
    prof = {"hits": [(10.0, 0.0, 100.0, 0.0)], "sim": {"p": [1.0], "taken": 388.0, "saved": 30.0}}
    check(abs(multiplier(prof, 300.0) - max(0.0, (100 - 388 + 300) / 100)) < 1e-9, "multiplier formula")
    # The key a profile is stored under is the one rebuilt from the profile it returned, and damage
    # (the stat-dependent part) is not in it.
    hits, wins = [(20.004, 100.0, 300.0, 0.0)], [(0.0, 30.0, 100.0, 1.0, 0.0, 1.0)]
    k = sim_key(hits, wins, 2.4567, 40.04, [], 89)
    check(k == sim_key([[20.004, 100.0, 999.0, 5.0]], [list(wins[0])], round(2.4567, 2), 40.04, [], 89),
          "a profile's simulation key survives JSON and ignores damage")
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        mine, old, other = (Path(d) / f"{s}-fp-poise89.json" for s in ("aaaa", "bbbb", "cccc"))
        _write_sims(old, {"x": 1})
        _write_sims(other, {"y": 2})
        os.utime(old, (time.time() - 2 * SIMS_STALE_S,) * 2)
        _write_sims(mine, {k: {"p": [1.0]}})
        check(_read_sims(mine) == {k: {"p": [1.0]}} and not old.exists() and other.exists()
              and _read_sims(Path(d) / "absent.json") == {},
              "kept simulations round-trip and only another stamp's stale file is deleted")
    geometry_selftest(check)
    # The backstep reply (`backstep_motion`): read from its TAE and clip, never invincible.
    bs = backstep_motion()
    check(len(bs) == 1 and bs[0]["name"] == "backstep" and bs[0]["iframes"] == 0.0
          and round(bs[0]["ready"]) == 14 and abs(bs[0]["path"][-1][1] - 2.5) < 0.05,
          f"backstep a000_027000: no unconditional i-frames, R1 from f{bs[0]['ready']:.0f}, "
          f"{bs[0]['path'][-1][1]:.2f} m straight back (TAE + root motion)" if bs else "backstep clip readable")
    # Entry steps: a rolling R1's i-frames whiff a pool hit inside them; the backstep's do not.
    tab = {"roll_r1": 20.0, "bstep_r1": 14.0, "inv_roll_r1": 13.0, "inv_bstep_r1": None}
    slot = {"hit_at": [5.0], "hit_poise": [100.0], "damage": 300.0, "roll_cancel": 20.0, "ha": []}
    rh, rw, rg = chain_cast([entry_step("2h_roll_r1", tab), slot])
    bh, bw, _ = chain_cast([entry_step("bstep_r1", tab), slot])
    check(rh[0][0] == 25.0 and rw == [(0.0, 13.0, 0.0, 0.0, 0.0, 0.0)] and rg == 40.0
          and bh[0][0] == 19.0 and bw == [] and entry_step("crouch_r1", tab) is None,
          "entry steps: the roll R1 hits at entry 20 + 5 inside a f0-13 i-frame window, the backstep R1 "
          "at 14 + 5 with none")
    # The toy B hits at 15 and 30: an i-frame window over f15 whiffs the first, so my hit at 25
    # lands and he lands only the second; without it the first breaks me (60 > 40).
    early = simulate(f, [(25.0, 10.0)], [(0.0, 16.0, 0.0, 0.0, 0.0, 0.0)], 2.5, 40.0, 40.0)
    bare = simulate(f, [(25.0, 10.0)], [], 2.5, 40.0, 40.0)
    check(early["taken"] == B_HIT_HP and early["p"] == [1.0] and bare["p"] == [0.0],
          f"an i-frame window whiffs B's hit inside it (taken {early['taken']:.0f} against {bare['taken']:.0f})")
    stagger_pool_selftest(check)
    print(f"{len(fails)} failed")
    return 1 if fails else 0


def stamp_selftest(check, trace: bool = True) -> None:
    """The code stamp (section "the code stamp"), on a copy of the stamped scripts: comments,
    docstrings, moved lines and edits to functions no profile runs leave both stamps; an edit to a
    function a profile runs, to one a simulation runs, in this script or a sibling, or to a
    module-level constant moves the stamps that depend on it. With `trace`, every unit a traced run
    of real rows reaches (`stamp_trace`) is stamped, the static walk (`stamp_closure`) adds nothing
    to either list, and the check fails when a reached unit is taken off a list."""
    import shutil
    import tempfile
    scopes = ("profile", "sim")
    intr = "er-mechanics-interrupt.py"

    def find(tree, qual):
        node = tree
        for part in qual.split("."):
            node = next(n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                        and n.name == part)
        return node

    def first_line(tree, qual):
        """(line index, indent) of the first statement of `qual` after its docstring."""
        fn = find(tree, qual)
        doc = ast.get_docstring(fn) is not None
        s = fn.body[1 if doc else 0]
        line = min([s.lineno] + [d.lineno for d in getattr(s, "decorator_list", [])])
        return line - 1, " " * s.col_offset

    def insert(qual, text):
        def how(lines, tree):
            i, ind = first_line(tree, qual)
            lines.insert(i, f"{ind}{text}\n")
        return how

    def reword_docstring(qual):
        def how(lines, tree):
            i = find(tree, qual).body[0].lineno - 1
            lines[i] = lines[i].replace('"""', '"""Reworded by the stamp selftest. ', 1)
        return how

    def blank_lines(lines, tree):
        lines[1:1] = ["\n"] * 3

    def constant(name, new):
        def how(lines, tree):
            i = next(k for k, l in enumerate(lines) if l.startswith(f"{name} = "))
            lines[i] = f"{name} = {new}\n"
        return how

    # Traced first, in as fresh a process as the selftest has: it also loads the sibling modules
    # the static walk below resolves aliases on.
    t = stamp_trace(log=lambda s: None) if trace else None
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        for n in STAMP_DEPS:
            shutil.copyfile(HERE / n, root / n)
        base = {s: stamp(s, root=root) for s in scopes}
        check(base == {s: stamp(s) for s in scopes} and base["profile"] != base["sim"],
              f"a copy of the stamped scripts stamps as they do: {base}")

        def edited(name, how):
            p = root / name
            text = p.read_text()
            lines = text.splitlines(keepends=True)
            how(lines, ast.parse(text))
            p.write_text("".join(lines))
            try:
                return {s: stamp(s, root=root) for s in scopes}
            finally:
                p.write_text(text)

        unrelated = [(SELF_SCRIPT, "stagger_report"), (SELF_SCRIPT, "selftest"), (SELF_SCRIPT, "resim_report"),
                     (intr, "chip_report")]
        check(not any(stamp_covers(STAMP_DEPS, n, u) for n, u in unrelated),
              f"the unrelated functions edited below are on no stamp list: {unrelated}")
        same = [
            ("a comment in simulate", SELF_SCRIPT, insert("simulate", "# a comment the stamp selftest adds")),
            ("simulate's docstring reworded", SELF_SCRIPT, reword_docstring("simulate")),
            ("Rows._cast's docstring reworded", SELF_SCRIPT, reword_docstring("Rows._cast")),
            ("three blank lines at the top, every line moved", SELF_SCRIPT, blank_lines),
            ("a comment in er-mechanics-interrupt.chip_states", intr, insert("chip_states", "# selftest")),
            *((f"an edit to the unrelated {n.removesuffix('.py')}.{u}", n, insert(u, "_stamp_selftest = None"))
              for n, u in unrelated),
        ]
        for label, name, how in same:
            got = edited(name, how)
            check(got == base, f"stamp unchanged by {label}")
        moved = [
            ("an edit inside simulate", SELF_SCRIPT, insert("simulate", "_stamp_selftest = None"), {"profile", "sim"}),
            ("an edit inside Rows._cast", SELF_SCRIPT, insert("Rows._cast", "_stamp_selftest = None"), {"profile"}),
            ("an edit inside er-mechanics-interrupt.chip_states", intr,
             insert("chip_states", "_stamp_selftest = None"), {"profile", "sim"}),
            ("an edit inside er-mechanics-ashes.skill_profile", "er-mechanics-ashes.py",
             insert("skill_profile", "_stamp_selftest = None"), {"profile"}),
            ("MAX_MY_HITS 12 -> 13", SELF_SCRIPT, constant("MAX_MY_HITS", 13), {"profile", "sim"}),
        ]
        for label, name, how, want in moved:
            got = edited(name, how)
            changed = {s for s in scopes if got[s] != base[s]}
            check(changed == want, f"{label} moves the {' and '.join(sorted(want))} stamp"
                  f"{'s' if len(want) > 1 else ''} (moved: {sorted(changed) or 'none'})")
    for scope, deps in (("profile", STAMP_DEPS), ("sim", STAMP_SIM_DEPS)):
        grown = stamp_closure(deps)
        extra = sorted(f"{n}:{u}" for n, us in grown.items() for u in us if not stamp_covers(deps, n, u))
        extra += sorted(n for n in grown if n not in deps)
        check(not extra, f"the static walk adds nothing to the {scope} list" + (f": {extra}" if extra else ""))
    loose = sorted(f"{n}:{u}" for n, us in STAMP_SIM_DEPS.items() for u in us if not stamp_covers(STAMP_DEPS, n, u))
    check(not loose, "every sim unit is on the profile list" + (f": {loose}" if loose else ""))
    if t is None:
        return
    for scope, deps in (("profile", STAMP_DEPS), ("sim", STAMP_SIM_DEPS)):
        missing = sorted(f"{n}:{u}" for n, u in t[scope] if not stamp_covers(deps, n, u))
        check(len(t[scope]) > 10 and not missing,
              f"every unit a traced {scope} run reaches is stamped ({len(t[scope])} traced)"
              + (f"; missing {missing} (regenerate with --stamp-deps)" if missing else ""))
    pruned = {**STAMP_SIM_DEPS, SELF_SCRIPT: tuple(u for u in STAMP_SIM_DEPS[SELF_SCRIPT] if u != "simulate")}
    check(any(n == SELF_SCRIPT and u == "simulate" and not stamp_covers(pruned, n, u) for n, u in t["sim"]),
          "the trace check fails a list with simulate taken off it")
    check(not t["refused"], f"the traced run wrote nothing under the planner cache (refused: {t['refused']})")


def stagger_selftest(check, f) -> None:
    """B's follow-up on a broken me (the stagger section): the toy pool row of `selftest` (B at 50
    poise presses at 10, hits at 15 and 30 for 60 poise each) with its stagger arrays changed."""
    from types import SimpleNamespace

    def toy(**kw):
        d = {k: getattr(f, k) for k in ("P", "w", "offs", "reach", "k", "t", "q", "m", "win", "inv",
                                         "bt", "bq", "bm", "bf", "be", "bk")}
        d.update(kw)
        return SimpleNamespace(**d)

    def esc(*e):
        return np.array([[list(e) + [0.0] * (4 - len(e))] * f.bt.shape[1]])

    # Gate 40 holds both of B's hits in the old model; the stagger decides now: escape 20 (free at
    # 35) charges his 30, escape 10 (free at 25) does not.
    inside = simulate(toy(be=esc(20.0, 10.0)), [(20.0, 100.0)], [], 2.5, 40.0, 40.0)
    outside = simulate(toy(be=esc(10.0, 5.0)), [(20.0, 100.0)], [], 2.5, 40.0, 40.0)
    check(inside["taken"] == 2 * B_HIT_HP and inside["follow_hp"] == B_HIT_HP
          and outside["taken"] == B_HIT_HP and outside["follow_hp"] == 0.0,
          "stagger: a break followed by a hit inside the escape window charges it, one outside does not")
    tie = simulate(toy(be=esc(15.0)), [(20.0, 100.0)], [], 2.5, 40.0, 40.0)
    check(tie["follow_hits"] == 0.5, "stagger: a hit on the escape frame itself is a tie, half a hit")
    # Knocked 1 m back from his 2.5 m contact, a 3 m reach no longer covers me.
    pushed = simulate(toy(be=esc(20.0), bk=np.array([[1.0, 1.0]])), [(20.0, 100.0)], [], 2.5, 40.0, 40.0)
    check(pushed["follow_hits"] == 0.0 and pushed["taken"] == B_HIT_HP,
          "stagger: a follow-up out of reach after the knockback is not charged")
    # His R1 follow-up after the string (hit 3, at 40): pressed only on a broken me. Escape 30
    # holds me to 45, so his 30 (a second break, escape 10, still to 45) and his 40 both land.
    three = dict(bt=np.array([[5.0, 20.0, 30.0]]), bq=np.array([[60.0, 60.0, 60.0]]), bm=np.ones((1, 3), bool),
                 bf=np.array([[False, False, True]]), be=np.array([[[30.0, 10.0, 0.0, 0.0]] * 3]),
                 bk=np.zeros((1, 3)))
    broken = simulate(toy(**three), [(20.0, 100.0)], [], 2.5, 25.0, 40.0)
    held = simulate(toy(**three), [(20.0, 10.0)], [(0.0, 60.0, 100.0, 1.0, 0.0, 1.0)], 2.5, 60.0, 40.0)
    check(broken["taken"] == 3 * B_HIT_HP and broken["follow_hits"] == 2.0 and held["taken"] == 2 * B_HIT_HP
          and held["broken"] == 0.0,
          f"stagger: his R1 follow-up after the string lands on a broken me ({broken['taken']:g}), never on a "
          f"standing one ({held['taken']:g})")


def stagger_pool_selftest(check) -> None:
    """The stagger charge on the real RL 140-160 pool, once per setup the break logic serves: an
    ash cast one-handed and two-handed (`Rows._cast`, Heavy Uchigatana Unsheathe at 89 poise), an
    R1 string and a powerstance string (`ChainTrades.trade`, Uchigatana's r1_* and dual_* members
    from the sequence cache). Each must charge B's hits on a broken me (`follow_hp` > 0), and none
    of it with the stagger lock taken away (every escape 0). Skipped without the caches."""
    if not (INTR.seq_cache_path().exists() and (INTR.CACHE / "builds.jsonl").exists()):
        print("skip stagger on the real pool: no sequence cache or corpus mirror")
        return
    from types import SimpleNamespace
    opp = with_read(Opponent(CHAIN_REACT_POINTS))
    off = SimpleNamespace(**{**vars(opp), "be": np.zeros_like(opp.be)})
    off.contact_delay = opp.contact_delay
    rows = Rows(opp, {"str": 18, "dex": 80, "int": 7, "fth": 8, "arc": 9},
                ["Verdigris Gauntlets", "Verdigris Armor", "Verdigris Greaves"], 2292.5, carry=True)
    for grip in ("1H", "2H"):
        x = {"ash": "Unsheathe", "weapon": "Uchigatana", "affinity": "Heavy", "grip": grip, "cat": "non-bullet",
             "gate": None, "melee_reach": None, "reach": None, "last": 0}
        c = rows._cast(x)
        hits = [(h[0], h[1]) for h in c["hits"]]
        st = carried_states(opp, hits[0][1], 89.0)
        on = simulate(opp, hits, c["windows"], REACH_SHARE * 3.0, c["gate"], 89.0, states=st)
        bare = simulate(off, hits, c["windows"], REACH_SHARE * 3.0, c["gate"], 89.0, states=st)
        check(on["follow_hp"] > 0.0 and bare["follow_hp"] == 0.0 and on["broken"] == bare["broken"],
              f"stagger, ash {grip}: Heavy Uchigatana Unsheathe at 89 poise is charged B's hits on a broken me "
              f"({on['follow_hp']:.2f} HP, {on['follow_hits']:.4f} hits; none with no stagger lock)")
    members = opp.weapons["Uchigatana|one"]["members"]

    def slots(keys, button):
        out = []
        for k in keys:
            m = members.get(k)
            if m is None:
                break
            out.append({"hit_at": [h[0] for h in m["hits"]], "hit_poise": [h[1] * h[2] for h in m["hits"]],
                        "ha": [list(w) + [1.0] for w in m["windows"]], "damage": 300.0,
                        "next_cancel": m["cancel"].get(button), "roll_cancel": m["cancel"].get("dodge")})
        return out
    for name, keys, button in (("R1 string", [f"r1_{i}" for i in range(1, 6)], "r1"),
                               ("powerstance string", [f"dual_{i}" for i in range(1, 7)], "l1")):
        chain = slots(keys, button)
        on = ChainTrades(opp, 89.0, carry=True).trade(chain, "Uchigatana", "1H", 3.0)
        bare = ChainTrades(off, 89.0, carry=True).trade(chain, "Uchigatana", "1H", 3.0)
        check(on and bare and on["follow_hp"] > 0.0 and bare["follow_hp"] == 0.0 and on["broken"] == bare["broken"],
              f"stagger, {name}: Uchigatana's {len(chain)}-step string at 89 poise is charged B's hits on a broken "
              f"me ({on and on['follow_hp']} HP, multiplier {on and on['mult']} against {bare and bare['mult']} "
              f"with no stagger lock)")


def chain_selftest(check, f) -> None:
    """The string terms (`carried_states`, `simulate`'s `states`, `chain_cast`, `ChainTrades`) on
    the toy pool row of `selftest`: B at 50 poise presses at 10, hits at 15 (60 poise) and 30."""
    plain = simulate(f, [(20.0, 100.0)], [], 2.5, 40.0, 40.0)
    zero = simulate(f, [(20.0, 100.0)], [], 2.5, 40.0, 40.0,
                    states=(np.zeros((1, 1)), np.zeros((1, 1)), np.ones((1, 1))))
    check(plain == zero, "states: one zero-chip state is the fresh simulation")
    # My 30-poise hit at 12 does not break 50 poise fresh; half the time B carries 30 already.
    fresh = simulate(f, [(12.0, 30.0)], [], 2.5, 14.0, 200.0)
    half = simulate(f, [(12.0, 30.0)], [], 2.5, 14.0, 200.0,
                    states=(np.array([[0.0, 30.0]]), np.zeros((1, 2)), np.array([[0.5, 0.5]])))
    check(fresh["stopped_b"] == 0.0 and half["stopped_b"] == 0.5,
          "states: B's carried chip adds to my hit's poise damage")
    # B's 60-poise hit at 15 does not break my 100 fresh; carrying 60 it does (its hit at 30 is
    # past the gate).
    mine = simulate(f, [(20.0, 1.0)], [], 2.5, 25.0, 100.0,
                    states=(np.zeros((1, 2)), np.array([[0.0, 60.0]]), np.array([[0.5, 0.5]])))
    check(mine["broken"] == 0.5 and mine["p"] == [0.5], "states: my carried chip lets B's hit break me")
    b, m, w = carried_states(f, 20.0, 89.0)
    check(b.tolist() == [[0, 0, 20, 20, 40, 40]] and m.tolist() == [[0, 60] * 3]
          and np.allclose(w, 1 / 6), "carried states: ceil(50/20)=3 of B times ceil(89/60)=2 of mine, equal")
    s1 = {"hit_at": [10.0], "hit_poise": [30.0], "ha": [], "damage": 90.0, "status_hp": {"bleed": 10.0},
          "next_cancel": 20.0, "roll_cancel": 25.0}
    s2 = {"hit_at": [5.0, 8.0], "hit_poise": [40.0, 40.0], "ha": [[0.0, 10.0, 50.0, 1.0, 0.0, 1.0]],
          "damage": 60.0, "next_cancel": None, "roll_cancel": 30.0}
    hits, wins, gate = chain_cast([s1, s2], delay=1.0)
    check((hits, wins, gate) == ([(11.0, 30.0, 100.0, 0.0), (26.0, 40.0, 30.0, 0.0), (29.0, 40.0, 30.0, 0.0)],
                                 [(20.0, 30.0, 50.0, 1.0, 0.0, 1.0)], 50.0),
          "chain cast: steps at the next-attack cancel, hits moved by the contact delay, damage split, "
          "windows on the string's clock, gate at the last roll cancel")
    check(chain_cast([s1, {"startup": 9}]) is None, "chain cast: a slot without per-hit poise gives none")

    class Toy:
        pass
    toy = Toy()
    toy.__dict__.update({k: getattr(f, k) for k in ("P", "w", "offs", "reach", "k", "t", "q", "m", "win", "inv",
                                                    "bt", "bq", "bm", "bf", "be", "bk")})
    toy.contact_delay = lambda weapon, grip: 0.0
    ct = ChainTrades(toy, 40.0, carry=False)
    heavy = ct.trade([{"hit_at": [12.0], "hit_poise": [100.0], "damage": 300.0, "roll_cancel": 32.0}], "W", "1H", None)
    light = ct.trade([{"hit_at": [12.0], "hit_poise": [10.0], "damage": 300.0, "roll_cancel": 32.0}], "W", "1H", None)
    armored = ct.trade([{"hit_at": [12.0], "hit_poise": [10.0], "damage": 300.0, "roll_cancel": 32.0,
                         "ha": [[0.0, 32.0, 100.0, 1.0, 0.0, 1.0]]}], "W", "1H", None)
    # Light: B's 60 breaks my 40 at 15 and its 30 lands inside the stagger, 776 HP back for 300
    # dealt; the stagger holds me to 35, past the gate at 32, so no recovery is saved.
    want_light = max(0.0, (300 - 2 * B_HIT_HP) / 300)
    check(heavy["mult"] == 1.0 and heavy["stopped_b"] == 1.0 and heavy["taken"] == 0.0
          and abs(light["mult"] - round(want_light, 4)) < 1e-9 and light["broken"] == 1.0
          and light["follow_hp"] == B_HIT_HP,
          "string trade: a hit that breaks B first takes nothing back; a light one takes B's string")
    # The same light string against a short stagger (10 frames: free at 25, before B's hit at 30)
    # takes only the breaking hit, and gets the frames from 25 to the gate back.
    short = Toy()
    short.__dict__.update(toy.__dict__)
    short.be = np.array([[[10.0, 5.0, 0.0, 0.0]] * 2])
    sl = ChainTrades(short, 40.0, carry=False).trade(
        [{"hit_at": [12.0], "hit_poise": [10.0], "damage": 300.0, "roll_cancel": 32.0}], "W", "1H", None)
    want_short = max(0.0, (300 - B_HIT_HP + 7 * (300 / (32 / 30)) / 30) / 300)
    check(sl["taken"] == B_HIT_HP and sl["follow_hp"] == 0.0 and abs(sl["mult"] - round(want_short, 4)) < 1e-9,
          f"string trade: B's next hit after the stagger's escape is not charged ({sl['taken']:g} taken)")
    check(armored["broken"] == 0.0 and armored["taken"] == 2 * B_HIT_HP and armored["mult"] == 0.0,
          "string trade: hyperarmor keeps me standing, so both of B's hits trade into it")
    # Weighted start offsets (`for_pool`'s read at 0 beside the reactions): B pressing with me hits
    # at 5, before my 12, and breaks my 40; pressing 10 later it is broken first. A quarter read.
    toy.offs, toy.offs_w = np.array([0.0, 10.0]), np.array([0.25, 0.75])
    mixed = ChainTrades(toy, 40.0, carry=False).trade(
        [{"hit_at": [12.0], "hit_poise": [100.0], "damage": 300.0, "roll_cancel": 32.0}], "W", "1H", None)
    check(mixed["broken"] == 0.25 and mixed["stopped_b"] == 0.75,
          "string trade: start offsets weighted by the opponent's own offset weights")
    # Over a duel's spread (`reach_parts`, the opportunity-share rule): a string is credited only
    # in the bands its reach covers, the band it ends in for its covered part alone.
    sp = duel_spread(16.9, 3.73, lo=0.8)
    nodes = [n[0] for n in sp["nodes"]]
    p41 = reach_parts(sp, 4.1)
    check([n for _, _, n in p41] == nodes[:3] and p41[2][0] < 4.1
          and abs(sum(w for _, w, _ in p41) - wave_mass(sp["wave"], sp["wave"][0], 4.1)) < 1e-6
          and reach_parts(sp, None) == [],
          f"spread parts: a 4.1 m reach is credited the 0.95 and 2.16 m cells and the 4.38 m cell up to "
          f"4.1 m, its time the duel's below 4.1 m; no reach, no parts: {p41}")
    sl_ = [{"hit_at": [12.0], "hit_poise": [100.0], "damage": 300.0, "roll_cancel": 32.0}]
    toy.offs, toy.offs_w = np.array([10.0]), np.array([1.0])
    flat = ChainTrades(toy, 40.0, carry=False)
    duel = ChainTrades(toy, 40.0, carry=False, spread=sp)
    fixed_m = flat.trade(sl_, "W", "1H", 3.0)["mult"]
    near, far = duel.trade(sl_, "W", "1H", 2.0), duel.trade(sl_, "W", "1H", 4.1)
    # The heavy hit breaks B first at every distance (mult 1 there), so the spread's mult is the
    # share alone.
    want_near = sum(w for _, w, _ in reach_parts(sp, 2.0)) * fixed_m
    unmeasured = duel.trade(sl_, "W", "1H", None)
    check(abs(unmeasured["share"] - sum(w for _, w, _ in reach_parts(sp, EXCHANGE_DISTANCE_M))) < 1e-4,
          f"string trade over the spread: a string with no measured reach pays the share of the exchange "
          f"distance ({unmeasured['share']}), not a free whole duel")
    check(abs(near["share"] - sum(w for _, w, _ in reach_parts(sp, 2.0))) < 1e-4 and near["mult"] < fixed_m
          and far["share"] > near["share"] and far["mult"] > near["mult"]
          and abs(near["mult"] - round(want_near, 4)) < 1e-3 and "share" not in flat.trade(sl_, "W", "1H", 3.0),
          f"string trade over the spread: mult is its share of the duel times its value where it reaches, "
          f"so the longer string earns more (2.0 m {near['mult']} share {near['share']}, 4.1 m {far['mult']} "
          f"share {far['share']}, fixed {fixed_m})")


class _SpreadToy:
    """The parts of `Rows` that `Rows.spread_profile` calls, on a toy cast: `escape` builds the
    toy geometry's table at each distance, `_sim` is `Rows._sim` against the toy opponent."""

    _sim = Rows._sim

    def __init__(self, opp, track, hit):
        self.opp, self.track, self.hit, self.carry, self._memo, self._esc = opp, track, hit, False, {}, {}

    def escape(self, c, x, D):
        key = f"toy|{x['ash']}|{D:g}"
        if key not in self._esc:
            self._esc[key] = escape_table(self.track, self.hit, 1, D, toy_rolls(), 0.4, TOY_LIMITS, TOY_RUN, 1.0)
        return key, self._esc[key]

    def profile(self, poise, spread=None, fixed=None, reach=None, dmg=2000.0):
        """The toy cast's profile (one hit at f20, 100 poise, `dmg`) over `spread`, else at `fixed`."""
        c = {"hits": [(20.0, 100.0, dmg, 0.0)], "windows": [], "fx": [], "gate": 40.0}
        if spread is None:
            return {"hits": c["hits"], "sim": self._sim(c["hits"], [], fixed, c["gate"], [], poise)}
        return Rows.spread_profile(self, c, {"ash": "toy"}, poise, spread, fixed, reach)

    def value(self, poise, spread=None, fixed=None, reach=None, dmg=2000.0):
        """`multiplier` of `profile`."""
        return multiplier(self.profile(poise, spread, fixed, reach, dmg), 0.0)


def spread_selftest(check, f) -> None:
    """The duel distance spread (`duel_spread`) and a cast valued over it (`Rows.spread_profile`)."""
    melee = 3.73
    flat = spread_weights(0.8, 16.9, None)
    check(abs(flat.sum() - 1.0) < 1e-12 and np.allclose(flat, flat[::-1]) and np.allclose(flat, 1 / SPREAD_POINTS),
          "stationary wave: the arcsine weights sum to 1, are symmetric, and are the Chebyshev rule's 1/n each")
    nodes, edge = spread_nodes(0.8, 16.9)
    check(np.allclose(nodes + nodes[::-1], 0.8 + 16.9) and edge[0] == 0.8 and abs(edge[-1] - 16.9) < 1e-9,
          "the nodes are symmetric about the middle of the swing and the cells span contact to the start")
    duel = spread_weights(0.8, 16.9, melee)
    early = spread_weights(0.8, 16.9, melee, progress=(0.0, 1 / 3))
    late = spread_weights(0.8, 16.9, melee, progress=(2 / 3, 1.0))
    near = nodes < melee
    check(abs(duel.sum() - 1.0) < 1e-12 and late[near].sum() < early[near].sum() < flat[near].sum(),
          f"the rising floor: mass inside melee range {late[near].sum():.3f} late < {early[near].sum():.3f} early "
          f"< {flat[near].sum():.3f} stationary")
    check(duel_spread(16.9, melee, lo=0.8)["nodes"][-1][0] == round(float(nodes[-1]), 2),
          "the farthest node is near the start")
    # Partial cells (`covered_cells`): with the nodes up to a reach R reaching, the credited time is
    # the wave's time below R, cut inside the cell R falls in, so it rises smoothly with R.
    for wave_name, sp in (("stationary", duel_spread(16.9, None, lo=0.8)), ("duel", duel_spread(16.9, melee, lo=0.8))):
        cells = [n[0:1] + n[2:4] for n in sp["nodes"]]
        reaches = np.round(np.arange(0.9, 16.8, 0.02), 2)
        share = np.array([sum(c[2] for c in covered_cells(sp["wave"], cells, [n[0] <= r for n in cells], r))
                          for r in reaches])
        exact = np.array([wave_mass(sp["wave"], 0.8, r) for r in reaches])
        edges = [n[3] for n in sp["nodes"][:-1]]
        jumps = [abs(sum(c[2] for c in covered_cells(sp["wave"], cells, [n[0] <= e + s for n in cells], e + s))
                     - sum(c[2] for c in covered_cells(sp["wave"], cells, [n[0] <= e - s for n in cells], e - s)))
                 for e in edges for s in (1e-4,)]
        check(np.all(np.diff(share) >= -1e-12) and np.max(np.abs(share - exact)) < 1e-9 and max(jumps) < 1e-3,
              f"{wave_name} wave: an ash's share is the wave's time below its reach, rising with no step at a "
              f"cell boundary (largest jump across one {max(jumps):.1e})")
    # A short ash (standing still, a hit 1.5 m ahead, 0.6 m wide: it touches from 0.9 to 2.1 m)
    # against a gap-closer (a 6 m dash, the hit 1 m ahead of the dash: toy_mirror). Both scored at
    # 0.75 of their reach touch there; over the spread the short one reaches only the nearest node.
    short = _SpreadToy(f, toy_track(80.0), toy_hit(18.0, 22.0, -1.5, 0.6))
    track, hit, _ = toy_mirror()
    lunge = _SpreadToy(f, track, hit)
    stat = duel_spread(16.9, None, lo=0.8)
    lived = duel_spread(16.9, melee, lo=0.8)
    v = {}
    for name, toy, reach in (("short", short, 2.1), ("lunge", lunge, 7.0)):
        fixed = REACH_SHARE * reach
        v[name] = (toy.value(100.0, fixed=fixed), toy.value(100.0, stat, fixed, reach),
                   toy.value(100.0, lived, fixed, reach))
    rs, rl = v["short"][1] / v["short"][0], v["lunge"][1] / v["lunge"][0]
    check(v["short"][0] > 0 and rs < 1.0 and rl > rs,
          f"over the spread a short ash keeps {rs:.3f} of its fixed-D value, a gap-closer {rl:.3f}")
    check(v["short"][2] < v["short"][1] and v["short"][2] / v["short"][1] < v["lunge"][2] / v["lunge"][1],
          f"the late-duel floor costs the short ash more ({v['short'][2]:.3f} against {v['short'][1]:.3f} "
          f"stationary) than the gap-closer ({v['lunge'][2]:.3f} against {v['lunge'][1]:.3f})")
    # A short ash is pressed only where it reaches: its value is that time share times its value
    # over the reaching distances (weighted by the wave's time there), with nothing charged for the
    # distances it is never thrown at.
    prof = short.profile(100.0, stat, 1.6, 2.1)
    q = prof["spread"]
    alone = sum(p["w"] * multiplier({"hits": prof["hits"], "sim": p["sim"]}, 0.0) for p in q if p["reach"]) / prof["share"]
    check([p["reach"] for p in q] == [True, True] + [False] * (len(q) - 2) and all("sim" not in p for p in q[2:])
          and abs(multiplier(prof, 0.0) - prof["share"] * alone) < 1e-9,
          f"a short ash is worth its in-range value {alone:.3f} x its time share {prof['share']:.3f}, "
          f"no whiff charged where it is never cast")
    # Its reach (2.1 m) ends inside the 2.16 m node's cell: that cell is credited its covered part
    # below 2.1 m, measured at the part's time-weighted mean distance, not at the node it misses.
    b = q[1]
    a0, b0 = stat["nodes"][1][2], stat["nodes"][1][3]
    check(b.get("boundary") and abs(b["w"] - wave_mass(stat["wave"], a0, 2.1)) < 1e-6 and a0 < b["D"] < 2.1
          and abs(prof["share"] - arcsine_cdf(2.1, 0.8, 16.9)) < 1e-5,
          f"a reach ending inside a cell gets that cell's covered time: {b['w']:.4f} of the {a0:g}-{b0:g} m cell, "
          f"valued at {b['D']:g} m; the share {prof['share']:.4f} is the wave's time below 2.1 m")
    far = _SpreadToy(f, toy_track(80.0), toy_hit(18.0, 22.0, -30.0, 0.6))
    pf = far.profile(100.0, lived, 22.0, 30.6)
    check(pf["share"] == 0.0 and multiplier(pf, 0.0) == 0.0, "an ash that reaches nowhere on the wave is worth 0")
    # A shape that touches anyone (a bullet's `R` of 1e3, the `escape` no-shape rule) reaches at
    # every distance, as Storm Blade does: its share is 1.
    wide = _SpreadToy(f, toy_track(80.0), toy_hit(18.0, 22.0, -99.0, 1e3))
    pw = wide.profile(100.0, lived, 14.0, 18.7)
    check(abs(pw["share"] - 1.0) < 1e-5 and all(p["reach"] for p in pw["spread"]),
          f"a cast that reaches across the arena is pressed over the whole duel: share {pw['share']:.6f}")


def toy_rolls(n: int = 40, speed: float = 0.2, iframes: float = 13.0, ready: float = 20.0) -> list:
    """Four rolls shaped like `er-mechanics-ashes.roll_motion` (forward -Z, back, +X, -X), moving
    `speed` m a frame until their i-frames end."""
    out = []
    for ux, uz in ((0.0, -1.0), (0.0, 1.0), (1.0, 0.0), (-1.0, 0.0)):
        d = [speed * min(k, iframes) for k in range(n)]
        out.append({"path": [(ux * x, uz * x) for x in d], "iframes": iframes, "ready": ready})
    return out


def toy_track(frames: float, dz: float = 0.0, turn_deg: float = 0.0, dash_frames: float = 0.0) -> dict:
    """A caster standing still (or dashing `dz` m a step for `dash_frames` frames) who can turn
    `turn_deg` a step."""
    K = int(frames / ESCAPE_STEP) + 1
    f = np.arange(K) * ESCAPE_STEP
    return {"dx": np.zeros(K), "dz": np.where((f > 0) & (f <= dash_frames), dz, 0.0), "yaw": np.zeros(K),
            "turn": np.full(K, math.radians(turn_deg))}


def toy_hit(t0: float, t1: float, z: float, radius: float, h: int = 0) -> dict:
    """One hit whose shape sits `z` m ahead of my root (negative is ahead) from frame `t0` to `t1`."""
    T = np.arange(t0, t1 + 1e-9, ESCAPE_STEP)
    return {"T": T, "A": T, "X": np.zeros(len(T)), "Z": np.full(len(T), z), "R": np.full(len(T), radius),
            "H": np.full(len(T), h)}


def toy_mirror():
    """(track, hit, skill): a cast that dashes 6 m forward over its first 20 frames (0.3 m a
    frame) with a hit 1 m ahead of the caster at f18-22, and the same dash as a defender's movement
    skill (`move_motion` shape: no invincibility, R1 ready at f30)."""
    track = toy_track(80.0, dz=-0.15, dash_frames=20.0)
    skill = {"name": "Mirror Dash", "path": [(0.0, -0.3 * min(k, 20)) for k in range(41)],
             "windows": [(0.0, 0.0)], "iframes": 0.0, "ready": 30.0, "far": 6.0}
    return track, toy_hit(18.0, 22.0, -1.0, 0.8), skill


TOY_LIMITS = {"dist": 1.7, "diff_min": -35.0, "diff_max": 35.0, "my_to_def": 70.0}
TOY_RUN = 4.01 * ESCAPE_STEP / 30.0


def geometry_selftest(check) -> None:
    rolls = toy_rolls()
    hit = toy_hit(20.0, 24.0, -3.0, 1.5)
    locked = escape_table(toy_track(100.0), hit, 1, 3.0, rolls, 0.4, TOY_LIMITS, TOY_RUN, 1.0)
    free = escape_table(toy_track(100.0, turn_deg=30.0), hit, 1, 3.0, rolls, 0.4, TOY_LIMITS, TOY_RUN, 1.0)
    # Standing still he is touched when the hit opens.
    check(locked["T"] == 40 and locked["t0"] == [40], "the first touch on him standing still is the hit's first frame")
    # A side roll at f5 is past its i-frames at f18, 2.6 m to the side: a caster who cannot turn
    # misses, one who turns 30 degrees a step swings the hit onto him.
    side = locked["touch"][0][0][2][5], free["touch"][0][0][2][5]
    check(side[0] == -1 and side[1] == 40, "a sideways roll misses a cast whose tracking is locked, not one that turns")
    # Rolling in place (start after the hit) is no escape at all.
    check(locked["touch"][0][0][2][21] == 40, "a roll started after the first touch does not escape it")
    # The chaser of a caster who cannot turn reaches his back; a caster who turns keeps facing him.
    bs_locked, bs_free = locked["bs"][2][5], free["bs"][2][5]
    check(bs_locked > 2 * 25 and bs_free == -1, "the chaser gets behind a caster who cannot turn, never one who can")
    # A dash: the hit sits 1 m ahead of a caster who covers 0.3 m a step for 10 frames toward a
    # defender 6 m away. Standing still the hit never reaches him; dashing it does, and his body
    # stops mine two capsule radii from his axis.
    dash = toy_hit(10.0, 12.0, -1.0, 0.5)
    still = escape_table(toy_track(60.0), dash, 1, 6.0, rolls, 0.4, TOY_LIMITS, TOY_RUN, 1.0)
    moving = toy_track(60.0, dz=-0.3, dash_frames=10.0)
    run = escape_table(moving, dash, 1, 6.0, rolls, 0.4, TOY_LIMITS, TOY_RUN, 1.0)
    P, _, Q, _ = play(moving, 6.0, [np.inf], [0], [False], rolls, 0.4, TOY_LIMITS, TOY_RUN, 1.0)
    gaps = np.hypot(P[:, 0, 0] - Q[:, 0, 0], P[:, 0, 1] - Q[:, 0, 1])
    check(still["T"] is None and run["T"] is not None and abs(gaps.min() - 0.8) < 1e-9,
          "a dashing caster's root motion carries the hit to him, and his body stops it")
    # A back roll from the dash leaves him farther from where the dash stopped me than a forward one.
    check(run["gap"][1][0] > run["gap"][0][0], "the hold gap is measured to where the caster actually is")
    # Strafing from the first frame at 4.01 m/s: by f40 he is 5.3 m round a 3 m circle, clear of a
    # hit 3 m ahead of a caster who cannot turn, and inside it when the caster turns with him.
    late_hit = toy_hit(40.0, 44.0, -3.0, 1.0)
    lk = escape_table(toy_track(100.0), late_hit, 1, 3.0, rolls, 0.4, TOY_LIMITS, TOY_RUN, 1.0)["strafe"]
    fr = escape_table(toy_track(100.0, turn_deg=30.0), late_hit, 1, 3.0, rolls, 0.4, TOY_LIMITS, TOY_RUN,
                      1.0)["strafe"]
    check(lk["touch"][0][0][0][-1] == -1 and lk["touch"][0][0][1][-1] == -1 and fr["touch"][0][0][0][-1] == 80,
          "a strafe either way walks out of a late hit whose tracking is locked, not out of one that turns")
    # A commit before the hit stops him in front of it, and a hit in the first frames catches him
    # before the strafe has taken him anywhere.
    check(lk["touch"][0][0][0][0] == 80, "a strafe committed at frame 0 is standing still")
    fast = escape_table(toy_track(100.0), toy_hit(2.0, 4.0, -3.0, 1.0), 1, 3.0, rolls, 0.4, TOY_LIMITS, TOY_RUN,
                        1.0)["strafe"]
    check(fast["touch"][0][0][0][-1] == 4, "a hit two frames in catches the strafer")
    # He circles me: the distance between us holds while he runs.
    check(abs(lk["gap"][0][30] - 3.0) < 0.1, "the strafe circles the caster at the distance it started from")
    # The mirror: the defender answers a 6 m dash with the same dash. Cast back or sideways it
    # keeps him out of the hit; cast at me, our bodies meet and the hit lands, so that heading is
    # no escape and is left out of the table.
    track, mhit, mirror = toy_mirror()
    still = escape_table(track, mhit, 1, 6.0, rolls[:1], 0.4, TOY_LIMITS, TOY_RUN, 1.0)
    m = escape_table(track, mhit, 1, 6.0, rolls[:1] + headed([mirror]), 0.4, TOY_LIMITS, TOY_RUN, 1.0)
    check(still["T"] == 36 and m["moves"] == ["roll", "Mirror Dash +180", "Mirror Dash +90", "Mirror Dash -90"],
          f"the same dash cast away or aside dodges itself, cast head-on it does not: {m['moves']}")
    back = m["moves"].index("Mirror Dash +180")
    check(m["touch"][0][0][back][0] == -1 and m["gap"][back][0] > 5.0,
          "the mirrored dash from frame 0 ends as far away as it started, the hit missing")
    P, _, Q, _ = play(track, 6.0, [0.0], [0], [False], headed([mirror])[:1], 0.4, TOY_LIMITS, TOY_RUN, 1.0)
    gaps = np.hypot(P[:, 0, 0] - Q[:, 0, 0], P[:, 0, 1] - Q[:, 0, 1])
    check(abs(gaps.min() - 0.8) < 1e-6, "two dashes into each other stop at their bodies, neither passing through")
    check([ladder_banned(a) for a in ("Bloodhound's Step", "Quickstep", "Raptor of the Mists", "Charge Forth")]
          == [True, True, True, False], "the ladder's banned movement skills are no escape")
    # Cast backwards it turns him away from me: the punish waits 180 degrees at 720 deg/s.
    check(abs(m["turn"][back][0] - 7.5) < 1e-6 and m["turn"][0][0] == 0.0,
          f"a skill cast away is charged its turn back before the punish, a roll nothing: {m['turn'][back][0]}")
    # Which clips are escapes, from their own frames (user, 2026-10-19).
    line = np.array([(0.0, -0.1 * k) for k in range(40)])
    check(not escape_reason(np.zeros((30, 2)), [], [], 0.4)[0], "a stance entry that does not move is no escape")
    check(not escape_reason(line[:19], [], [(15.0, 18.0)], 0.4)[0]
          and not escape_reason(line[:20], [], [(18.0, 19.0)], 0.4)[0],
          "a lunge that carries the body into its own hit is an attack, not a dodge")
    check(escape_reason(line, [], [(5.0, 6.0)], 0.4)[0], "what an attack moves after its last hit counts")
    check(escape_reason(line, [], [], 0.4)[0] and escape_reason(line[:2], [(14.0, 30.0)], [(19.0, 31.0)], 0.4)[0],
          "a move with no hitbox of its own, or an invincible one, is an escape")
    # Direction: a clip under Disable Turning goes the way he faced, toward me, whatever the stick says.
    held = headed([{"name": "Locked", "path": line, "turn": np.zeros(len(line)), "windows": [(0.0, 0.0)],
                    "iframes": 0.0, "ready": 30.0}])
    fwd = held[0]["path"][-1]
    check(all(np.allclose(h["path"][-1], fwd) and h["face"] == 0.0 for h in held),
          "a clip that cannot turn is cast toward the target at every heading")
    slow = headed([{"name": "Slow", "path": line, "turn": np.full(len(line), 9.0), "windows": [(0.0, 0.0)],
                    "iframes": 0.0, "ready": 30.0}])[1]
    check(abs(abs(math.degrees(slow["face"])) - 180.0) < 1e-6 and slow["path"][-1][1] > 0.0,
          "turning 9 degrees a frame, a backward cast reaches 180 degrees by frame 20 and ends behind its start")

def stagger_report(opp) -> int:
    """The pool's links on a first break (`stagger_arrays`): per pair of consecutive hits of a row,
    the gap against the first one's escape and the knockback against his reach, pool-weighted."""
    R = len(opp.P)
    links = []
    for r in range(R):
        idx = np.nonzero(opp.bm[r])[0]
        for a, b in zip(idx[:-1], idx[1:]):
            gap, e = opp.bt[r, b] - opp.bt[r, a], opp.be[r, a, 0]
            near = EXCH.STRIKE_DISTANCE_M + opp.bk[r, a] <= max(EXCH.STRIKE_DISTANCE_M, opp.reach[r]) + 1e-9
            links.append((gap, e, near, bool(opp.bf[r, b]), opp.w[r]))
    g = np.array(links, float)
    w = g[:, 4] / g[:, 4].sum()
    true = (g[:, 0] < g[:, 1]) & (g[:, 2] > 0)
    print(f"{R} pool rows, {int(opp.bf.any(1).sum())} with an R1 follow-up after the string "
          f"({opp.w[opp.bf.any(1)].sum():.3f} of the pool weight); {len(g)} links")
    print(f"  true on a first break: {w[true].sum():.3f} of links (string {w[true & (g[:, 3] == 0)].sum():.3f}, "
          f"follow-up {w[true & (g[:, 3] > 0)].sum():.3f}); inside the escape but knocked out of reach "
          f"{w[(g[:, 0] < g[:, 1]) & (g[:, 2] == 0)].sum():.3f}")
    for lo, hi in ((0, 1), (1, 11), (11, 26), (26, 36), (36, 1e9)):
        sel = (g[:, 1] >= lo) & (g[:, 1] < hi)
        if sel.any():
            print(f"  escape {lo:g}-{hi:g} f: {w[sel].sum():.3f} of links, gap median {np.median(g[sel, 0]):.1f} f, "
                  f"true {w[sel & true].sum() / w[sel].sum():.3f}")
    return 0


def _resim_work(chunk):
    """{sim key: (simulation, baseline simulation or None)} of one chunk of `resim_report`'s."""
    opp, carry, base = _W["resim"]
    out = {}
    for k, (hits, windows, D, gate, fx, poise) in chunk:
        st = carried_states(opp, hits[0][1] if hits else 0.0, poise) if carry else None
        out[k] = (simulate(opp, hits, windows, D, gate, poise, fx, states=st),
                  base.simulate(opp, hits, windows, D, gate, poise, fx, states=st) if base else None)
    return out


def resim_report(cache_path: Path, rows_path: Path, top: int = 15, jobs: int = 8, baseline: Path | None = None) -> int:
    """Before and after of a change to `simulate` on a sweep's cached profiles (`profiles`' file at
    `cache_path`) without the escape geometry: every profile's simulations are run again against
    today's pool and its multiplier compared, each at its sweep row's `score` from `rows_path`.
    Fixed-distance and spread profiles alike (a spread part is re-run on the hits that touch
    there). "Before" is the cached simulation, or with `baseline` (a copy of this script from
    before the change) that copy's `simulate` against the same pool, so a pool that moved since
    the cache was made does not count as the change."""
    base = None
    if baseline:
        spec = importlib.util.spec_from_file_location("er_mechanics_ash_interrupt_baseline", baseline)
        base = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(base)
    doc = json.loads(cache_path.read_text())
    carry = bool(doc["settings"].get("carried"))
    score = {row_key(x): x.get("score") or 0.0 for x in json.loads(rows_path.read_text())}
    todo, plan = {}, {}
    for key, p in doc["rows"].items():
        if "sim" not in p or not p.get("hits"):
            continue
        parts = [q for q in p.get("spread") or () if q.get("reach")] or [None]
        jobs_of = []
        for q in parts:
            hits = [(h[0], h[1]) for h, t in zip(p["hits"], q["touch"] if q else [True] * len(p["hits"])) if t]
            D = q["D"] if q else p["D"]
            k = sim_key(hits, p["windows"], D, p["gate"], p["fx_frames"], p["poise"])
            todo[k] = (hits, [tuple(w) for w in p["windows"]], D, p["gate"], p["fx_frames"], p["poise"])
            jobs_of.append((k, q))
        plan[key] = jobs_of
    print(f"{len(plan)} profiles, {len(todo)} simulations to run again (carried {carry})", flush=True)
    import multiprocessing as mp
    opp = with_read(Opponent())
    _W["resim"] = (opp, carry, base)
    work = list(todo.items())
    chunks = [work[i::jobs * 4] for i in range(jobs * 4)]
    sims, olds = {}, {}
    ctx = mp.get_context("fork")
    cpus = allowed_cpus()
    workers = cpus[1:] or cpus
    jobs = max(1, min(jobs, len(workers)))
    with ctx.Pool(jobs, initializer=_worker_init, initargs=(workers, ctx.Value("i", 0))) as pool:
        for i, res in enumerate(pool.imap_unordered(_resim_work, [c for c in chunks if c]), 1):
            for k, (s, b) in res.items():
                sims[k] = s
                olds[k] = b
            print(f"  {i}/{len(chunks)} chunks", flush=True)

    def combine(p, jobs_of, got):
        if jobs_of[0][1] is None:
            return got[jobs_of[0][0]]
        new = {"p": [0.0] * len(p["hits"])}
        for k in ("taken", "saved", "broken", "follow_hp", "follow_hits"):
            new[k] = 0.0
        for k, q in jobs_of:
            s, it = got[k], iter(got[k]["p"])
            for i, t in enumerate(q["touch"]):
                new["p"][i] += q["w"] * (next(it) if t else 0.0)
            for f in ("taken", "saved", "broken", "follow_hp", "follow_hits"):
                new[f] += q["w"] * s.get(f, 0.0)
        return new

    out = []
    for key, jobs_of in plan.items():
        p = doc["rows"][key]
        new = combine(p, jobs_of, sims)
        old = combine(p, jobs_of, olds) if base else p["sim"]
        v = score.get(key, 0.0)
        before, after = multiplier({**p, "sim": old}, v), multiplier({**p, "sim": new}, v)
        out.append((after - before, key, before, after, old["broken"], new["broken"], old["taken"],
                    new["taken"], new.get("follow_hp", 0.0), old["saved"], new["saved"]))
    moved = [o for o in out if abs(o[0]) > 1e-4]
    same_break = sum(1 for o in out if abs(o[4] - o[5]) < 1e-9)
    print(f"{len(out)} profiles; multiplier moved by more than 1e-4 on {len(moved)}; break share unchanged on "
          f"{same_break} (a check that the pool is the one the cache was made against)")
    if out:
        d = np.array([o[0] for o in out])
        print(f"  change: mean {d.mean():+.4f}, min {d.min():+.4f}, max {d.max():+.4f}; follow-up HP mean "
              f"{np.mean([o[8] for o in out]):.2f}, max {max(o[8] for o in out):.1f}")
    for o in sorted(moved, key=lambda o: o[0])[:top]:
        print(f"  {o[0]:+.4f} {o[1]}: mult {o[2]:.4f} -> {o[3]:.4f}, broken {o[4]:.3f}, taken {o[6]:.0f} -> "
              f"{o[7]:.0f} (follow-up {o[8]:.1f}), saved {o[9]:.1f} -> {o[10]:.1f}")
    for o in sorted(moved, key=lambda o: -o[0])[:min(top, 5)]:
        if o[0] > 0:
            print(f"  {o[0]:+.4f} {o[1]}: mult {o[2]:.4f} -> {o[3]:.4f}, taken {o[6]:.0f} -> {o[7]:.0f}, "
                  f"saved {o[9]:.1f} -> {o[10]:.1f}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--stamp-deps", action="store_true",
                    help="trace a few real profiles and print STAMP_DEPS / STAMP_SIM_DEPS regenerated "
                         "(`stamp_deps_report`)")
    ap.add_argument("--skill")
    ap.add_argument("--weapon")
    ap.add_argument("--affinity", default="Keen")
    ap.add_argument("--grip", default="1H", choices=("1H", "2H"))
    ap.add_argument("--rows", type=Path, help="sweep JSON to take the row from (else a minimal row)")
    ap.add_argument("--poise", type=float)
    ap.add_argument("--distance", type=float)
    ap.add_argument("--arena-m", type=float,
                    help="value the cast over a duel's distance spread from this starting separation (`duel_spread`)")
    ap.add_argument("--stats", default="str=18,dex=80,int=7,fth=8,arc=9")
    ap.add_argument("--armor", default="Verdigris Gauntlets,Verdigris Armor,Verdigris Greaves")
    ap.add_argument("--max-hp", type=float, default=2292.5)
    ap.add_argument("--stagger-report", action="store_true",
                    help="how often the pool's next hit lands inside the stagger its previous hit breaks me into")
    ap.add_argument("--resim", type=Path, metavar="PROFILES",
                    help="re-run a sweep's cached interrupt profiles against today's model and print the "
                         "multiplier changes (`resim_report`; needs --rows, the sweep's row file)")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--baseline", type=Path, metavar="PY",
                    help="with --resim: a copy of this script from before a change, whose `simulate` is the before")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.stamp_deps:
        return stamp_deps_report(log=lambda s: print(s, file=sys.stderr))
    if a.stagger_report:
        return stagger_report(Opponent())
    if a.resim:
        if not a.rows:
            ap.error("--resim needs --rows")
        return resim_report(a.resim, a.rows, jobs=a.jobs, baseline=a.baseline)
    if not (a.skill and a.weapon):
        ap.error("--skill and --weapon, or --selftest")
    x = None
    if a.rows:
        x = next((r for r in json.loads(a.rows.read_text()) if r["ash"] == a.skill and r["weapon"] == a.weapon
                  and r.get("affinity", "Keen") == a.affinity and r["grip"] == a.grip), None)
    x = x or {"ash": a.skill, "weapon": a.weapon, "affinity": a.affinity, "grip": a.grip, "cat": "non-bullet",
              "gate": None, "melee_reach": None, "reach": None, "last": 0}
    stats = {k: int(v) for k, v in (p.split("=") for p in a.stats.split(","))}
    opp = with_read(Opponent())
    rows = Rows(opp, stats, [n.strip() for n in a.armor.split(",")], a.max_hp, carry=poise_carries())
    poise = rows.armor_poise() if a.poise is None else a.poise
    spread = duel_spread(a.arena_m, pool_melee_m(opp)) if a.arena_m and a.distance is None else None
    r = rows.profile(x, poise, a.distance, spread)
    s = r["sim"]
    for q in r.get("spread") or ():
        print(f"  at {q['D']:5.2f} m x{q['w']:.3f}: touches {sum(q['touch'])}/{len(q['touch'])} hits"
              + (f", delivered {[round(p, 3) for p in q['sim']['p']]}, HP taken {q['sim']['taken']:.0f}"
                 if q["reach"] else ", out of reach: never cast here"))
    if "share" in r:
        print(f"  share of the duel in reach {r['share']:.3f}")
    print(f"{a.affinity} {a.weapon} {a.grip} {a.skill}: D {r['D']} m, poise {poise:g}, gate f{r['gate']}, "
          f"B approach {s['approach']:.1f} f, B starts f0 (read, {1 - AS.REACT_SHARE:.0%}) or "
          f"f{np.median(reaction_frames()):.1f} (reaction) + approach, poise carried {rows.carry}")
    for (fr, pz, d, h), p in zip(r["hits"], s["p"]):
        print(f"  hit f{fr:5.1f} poise {pz:6.1f} dmg {d:6.1f} heal {h:5.1f}: delivered {100 * p:5.1f}%")
    print(f"  broken {100 * s['broken']:.1f}%, B stopped {100 * s['stopped_b']:.1f}%, HP taken {s['taken']:.0f} "
          f"({s.get('follow_hp', 0.0):.0f} of it his follow-up on a broken me, {s.get('follow_hits', 0.0):.3f} hits), "
          f"frames saved {s['saved']:.1f}")
    if "value" in x or "score" in x:
        v = x.get("score", 0)
        print(f"  multiplier at value {v}: {multiplier(r, v):.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
