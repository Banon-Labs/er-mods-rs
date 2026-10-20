#!/usr/bin/env python3
"""Whiff risk of an ash of war: the opponent rolls through it and punishes the recovery.

    python3 scripts/er-mechanics-ash-whiff.py --selftest

The user's question (2026-10-09): "we need to find a formula for determining how risky an ash is
based on how long the time is between activation and damage frames, how long the ash locks you
into an animation, and how early any hyper armor kicks in". `er-mechanics-ash-interrupt` answers
the opponent attacking into the cast; this module answers the other reply, the opponent rolling the
first hit and hitting the whiffed recovery. `er-mechanics-combo-loadout.py --whiff-weight` (the
default) weighs each row by `combined()`: `(1 - DODGE_SHARE) x` the interrupt multiplier `+
DODGE_SHARE x multiplier()`. Each opponent gives one reply, not both. Multiplying the two (tried
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

Left out: projectile travel (a bullet's `T` is its spawn frame, so its telegraph is short-counted),
the roll's own movement, B's timing error (`er-mechanics-ashes.DODGE_TIMING_SD_S`), B not knowing
which ash is coming, and a per-reaction choice between the two replies (the interrupt cache keeps
only the pool-weighted sums, so the split is the fixed `DODGE_SHARE`).
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
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
          roll: tuple[float, float] = (ROLL_IFRAMES, ROLL_READY)) -> dict | None:
    """One cast against the pool's rolling reply. `hits` [(frame, poise, damage, heal)] on the cast
    clock, `windows` as `er-mechanics-ash-interrupt.Rows.cast`. None for a cast with no damage."""
    if not hits:
        return None
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
    """Expected delivered net value over the undisturbed value; 1 with nothing to weigh."""
    if not w:
        return 1.0
    return max(0.0, w["out"] / w["full"])


def combined(imult: float, wmult: float, share: float = DODGE_SHARE) -> float:
    """The row's multiplier against both replies: `(1 - share) x imult + share x wmult`."""
    return (1.0 - share) * imult + share * wmult


def from_profile(opp, prof: dict, lead: float = 0.0, roll=(ROLL_IFRAMES, ROLL_READY)) -> dict | None:
    """`whiff` of one cached `er-mechanics-ash-interrupt` profile."""
    if not prof or "error" in prof or not prof.get("hits"):
        return None
    return whiff(opp, prof["hits"], prof["windows"], prof["gate"], prof["D"], prof["poise"], lead, roll)


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
    try:
        check(measured_roll() == (ROLL_IFRAMES, ROLL_READY), "roll constants match the measured TAE")
    except Exception as exc:  # game files absent: the constants stand as recorded
        print(f"skip roll measurement: {type(exc).__name__}: {exc}")
    print(f"{len(fails)} failed")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    ap.error("--selftest (the term is applied by er-mechanics-combo-loadout.py --whiff-weight)")
    return 2


if __name__ == "__main__":
    sys.exit(main())
