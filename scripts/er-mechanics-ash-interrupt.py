#!/usr/bin/env python3
"""Interrupt risk of an ash of war: how much of a cast is delivered when the opponent answers it.

    python3 scripts/er-mechanics-ash-interrupt.py --skill "Blood Tax" --weapon "Dueling Shield" \
        --affinity Keen --grip 1H --distance 2.5 --poise 88       # one cast, printed per hit
    python3 scripts/er-mechanics-ash-interrupt.py --selftest

Used by `er-mechanics-combo-loadout.py --interrupt-weight` (the default): each sweep row's value is
multiplied by `multiplier()`, the expected delivered net value over the undisturbed value. No new
damage or poise model; the pieces are, per cast:

- Engagement distance `D` = `REACH_SHARE` x the ash's max reach (`melee_reach[0]`, a bullet's
  `reach[1]`; steps and buffs `EXCHANGE_DISTANCE_M`). The opponent stands there (`INFERRED`: user,
  2026-10-08, "Impaling Thrust expects you to be at 75% maximum range").
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
- One timeline per (pool row, reaction quantile): hits in time order, simultaneous hits both land.
  A hit of mine that breaks B cancels B's later hits; a hit of B that breaks me cancels my later
  hits. B's hits land on me until the cast's R1 gate (`gate`, the end of my commitment), each for
  `B_HIT_HP` x the open window's PvP HP rate.

    delivered = sum p_i (d_i + heal_i) - E[HP taken] + E[frames saved] x value / 30
    multiplier = max(0, delivered / sum (d_i + heal_i))

`frames saved`: broken at or after my last hit, the gate minus the break frame (the recovery I no
longer sit in), at the row's own value per frame. A step or buff with no damage is worth
P(not broken before its effect frame).
"""
from __future__ import annotations

import argparse
import atexit
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

#: Engagement distance as a share of the ash's max reach (user, 2026-10-08).
REACH_SHARE = 0.75
#: Distance for a cast with no reach (steps, buffs): the exchange distance (`INFERRED`).
EXCHANGE_DISTANCE_M = EXCH.STRIKE_DISTANCE_M
#: Reaction quantiles per pool row (the reacted-interrupt setup).
REACT_POINTS = 41
#: HP one of B's hits takes off me: the pool-weighted mean R1 #1 (`er-mechanics-exchange`).
B_HIT_HP = EXCH.OPPONENT_HP_FALLBACK
#: Hits of mine simulated, earliest first.
MAX_MY_HITS = 12
STAMP_SOURCES = ("er-mechanics-ash-interrupt.py", "er-mechanics-interrupt.py", "er-mechanics-neutral.py",
                 "er-mechanics-ash-table.py", "er-mechanics-ashes.py")
#: Simulations kept across stat blocks, one file per code stamp, opponent pool and armor poise.
#: `simulate` reads my hits' frames and poise, never their damage, so a stat block that keeps a
#: row's timing reuses its simulation: 130 of a cold row's 231 ms (measured 2026-10-14).
SIMS_DIR = CACHE / "interrupt-sims"
#: A file of another stamp is deleted once it has gone this long unwritten.
SIMS_STALE_S = 86400.0


def stamp() -> str:
    h = hashlib.sha256()
    for n in STAMP_SOURCES:
        h.update((HERE / n).read_bytes())
    return h.hexdigest()[:16]


def reaction_frames(points: int = REACT_POINTS, median: float = AS.REACTION_MEDIAN_S) -> np.ndarray:
    nd = statistics.NormalDist()
    return np.array([(median * math.exp(AS.REACTION_LOG_SD * nd.inv_cdf((k + 0.5) / points))
                      + 2 * AS.NETWORK_ONE_WAY_S) * AS.TAE_FPS for k in range(points)])


class Opponent:
    """B as arrays over the pool's defender rows (sequence x armor poise)."""

    def __init__(self, points: int = REACT_POINTS, sa_rate: bool = True):
        self.weapons = INTR.load_cache()
        reg = ATK.Regulation(None)
        pool_raw = EXCH.opponent_pool(reg, INTR.CACHE / "builds.jsonl", 140, 160)
        self.delays = INTR.Delays(self.weapons)
        rows = INTR.PoolRows(pool_raw, self.weapons, self.delays, INTR.entry_table(), sa_rate)
        reaches = NEU.pool_reaches(pool_raw)
        fb = float(np.median([v["reach"] for v in reaches.values() if v.get("reach")]))
        seqs = [rows.seqs[i] for i in rows.d_idx]
        self.reach = np.array([(reaches.get(f"{s['weapon']}|{'2h' if s['grip'] == 'both' else '1h'}") or {})
                               .get("reach") or fb for s in seqs], float)
        self.t, self.q, self.m = INTR._hits_array(seqs)
        self.win = rows.d_win            # (5, R, K)
        self.inv = rows.d_inv            # (2, R, 1)
        self.P = rows.d_poise
        self.w = rows.d_w
        self.offs = reaction_frames(points)
        self.k = NEU.frames_per_metre()

    def fingerprint(self) -> str:
        """A hash of every array `simulate` reads, so a kept simulation never outlives its pool."""
        h = hashlib.sha256()
        for a in (self.P, self.reach, self.t, self.q, self.m, self.win, self.inv, self.w, self.offs,
                  np.float64(self.k)):
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


def simulate(opp: Opponent, hits, windows, D: float, gate: float, poise: float, fx=()):
    """One cast against the pool. `hits` [(frame, poise dealt)] sorted, `windows` [(start, end,
    bonus, poise mult, floor, HP mult)], `fx` effect frames. Returns per-hit landed shares, the
    expected HP taken, the expected frames saved, effect shares and the break shares."""
    R, Q = len(opp.P), len(opp.offs)
    gap = np.maximum(0.0, D - np.maximum(EXCH.STRIKE_DISTANCE_M, opp.reach))
    approach, _ = NEU.arrival(gap, k=opp.k)
    bstart = opp.offs[None, :] + approach[:, None]                         # (R, Q)
    bt = bstart[..., None] + opp.t[:, None, :]                              # (R, Q, H)
    bm = np.broadcast_to(opp.m[:, None, :], bt.shape)
    H = bt.shape[-1]
    t = np.array([h[0] for h in hits], float)
    q = np.array([h[1] for h in hits], float)
    n = len(t)
    shape = (R, Q)
    # B's side.
    bws, bwe, bwb, bwm, bwf = (opp.win[i][:, None, :] for i in range(5))
    bwh = np.ones_like(bwm)
    th_P = np.broadcast_to(opp.P[:, None], shape)
    th_cur, th_M, th_prev = th_P.copy(), th_P.copy(), np.full(shape, -1)
    inv0, inv1 = opp.inv[0][:, None, 0], opp.inv[1][:, None, 0]
    # My side.
    if windows:
        mw = np.array(windows, float).T                                    # (6, K)
    else:
        mw = np.array([[-1.0], [-1.0], [0.0], [1.0], [0.0], [1.0]])
    my_P = np.full(shape, float(poise))
    my_cur, my_M, my_prev = my_P.copy(), my_P.copy(), np.full(shape, -1)
    my_bt = np.full(shape, np.inf)
    th_bt = np.full(shape, np.inf)
    landed = np.zeros(shape + (max(n, 1),), bool)
    taken = np.zeros(shape)
    pm = np.zeros(shape, int)
    pb = np.zeros(shape, int)
    tpad = np.append(t, np.inf)
    for _ in range(n + H + 1):
        tm = np.where(np.isinf(my_bt), tpad[np.minimum(pm, n)], np.inf)
        idx = np.minimum(pb, H - 1)
        cand = np.take_along_axis(bt, idx[..., None], -1)[..., 0]
        okb = (pb < H) & np.take_along_axis(bm, idx[..., None], -1)[..., 0] & np.isinf(th_bt) & (cand < gate)
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
            my_cur, my_M, my_prev, mult, hpm = _window_step(np.where(do_b, tb, -1e9), *mw, my_P, my_cur, my_M,
                                                            my_prev, do_b)
            qb = np.take_along_axis(opp.q[:, None, :].repeat(Q, 1), idx[..., None], -1)[..., 0]
            my_cur = np.where(do_b, my_cur - qb * mult, my_cur)
            taken = np.where(do_b, taken + B_HIT_HP * hpm, taken)
            my_bt = np.where(do_b & (my_cur <= 1e-6) & np.isinf(my_bt), tb, my_bt)
        pm = pm + do_m
        pb = pb + do_b
    W = np.broadcast_to(opp.w[:, None] / Q, shape)
    t_last = t[-1] if n else 0.0
    saved = np.where(np.isfinite(my_bt) & (my_bt >= t_last), np.maximum(0.0, gate - my_bt), 0.0)
    return {"p": [float((W * landed[..., i]).sum()) for i in range(n)],
            "taken": float((W * taken).sum()), "saved": float((W * saved).sum()),
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

    def __init__(self, opp: Opponent, stats: dict, armor: list, max_hp: float):
        self.TAB = _sib("er-mechanics-ash-table")
        self.tab = self.TAB.Table("Dagger", "Keen", stats, armor, True)
        self.opp, self.max_hp = opp, max_hp
        self.sa = EXCH.SaRates()
        self._memo = {}

    def armor_poise(self) -> float:
        return round(self.tab.armor * 1000)

    def cast(self, x):
        """(hits [(frame, poise, damage, heal)], windows, effect frames, gate) of one row."""
        TAB, tab = self.TAB, self.tab
        A = TAB.A
        if (tab.weapon, tab.affinity) != (x["weapon"], x["affinity"]):
            tab.set_weapon(x["weapon"], x["affinity"])
        sid = tab.t.find_arts(x["ash"])
        # The opening depends on weapon class and grip (`skill_profile`, GetSwordArtsDiffCategory).
        prof = A.skill_profile(tab.t, sid, tab.wid, tab.level, x["grip"])
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
        if x["cat"] == "utility":
            fx = [e["frames"][0] for e in acts
                  if e["kind"] == "iframes" or e["kind"] == "parry"
                  or (e["kind"] == "speffect" and e.get("effect") in ("buff", "stance effect"))]
            return [], windows, sorted(fx)[:1], gate if gate is not None else x["last"]
        two = x["grip"] == "2H"
        sc = TAB.S.AshScorer(tab.lev, x["weapon"], x["affinity"], sid, two, tab.defender, None)
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
            out.append((round(frame, 2), round(pz, 2), round(h["pvp_damage_total"], 1), round(heal, 1)))
        out.sort()
        out = out[:MAX_MY_HITS]
        last = max(h[0] for h in out) if out else 0.0
        return out, windows, [], gate if gate is not None else last

    def profile(self, x, poise: float, distance: float | None = None) -> dict:
        hits, windows, fx, gate = self.cast(x)
        if distance is None:
            if x["cat"] == "bullet":
                reach = (x.get("reach") or [0, 0])[1]
            else:
                reach = (x.get("melee_reach") or [0])[0]
            distance = REACH_SHARE * reach if reach else EXCHANGE_DISTANCE_M
        key = sim_key(hits, windows, distance, gate, fx, poise)
        if key not in self._memo:
            self._memo[key] = simulate(self.opp, [(h[0], h[1]) for h in hits], windows, distance, gate, poise, fx)
        return {"hits": hits, "windows": windows, "fx_frames": fx, "gate": gate, "D": round(distance, 2),
                "poise": poise, "sim": self._memo[key], "fx": self._memo[key]["fx"]}


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


def row_key(x) -> str:
    """A row with a lead-in (`A.lead_in`, set as `lead` on the row) is its own entry, so a profile
    cached from the opening alone is not reused for it."""
    lead = f"|lead{x['lead']:g}" if x.get("lead") else ""
    return f"{x['weapon']}|{x.get('affinity', 'Keen')}|{x['ash']}|{x['grip']}{lead}"


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


_W = {}


def _worker_init(cores, counter):
    with counter.get_lock():
        i = counter.value
        counter.value += 1
    throttle([cores[i % len(cores)]])


def _work(chunk):
    rows, poise, distance = _W["rows"], _W["poise"], _W["distance"]
    out = {}
    for x in chunk:
        try:
            out[row_key(x)] = rows.profile(x, poise, distance)
        except Exception as exc:  # a row the extractors cannot read is reported and left neutral
            out[row_key(x)] = {"error": f"{type(exc).__name__}: {exc}"}
    return out


def profiles(xs: list, cache_path: Path, stats: dict, armor: list, max_hp: float, poise: float | None = None,
             distance: float | None = None, jobs: int = 12, log=print, throttled: bool = True) -> dict:
    """{row key: profile} for every row in `xs`, read from `cache_path` when its stamp and settings
    match, the rest computed (`jobs` forked workers, grouped by weapon) and written back as it goes.
    `throttled`: the caller runs on the first allowed core and each worker on one of the others
    (`throttle`), so `jobs` is at most the allowed cores less one."""
    settings = {"stamp": stamp(), "stats": stats, "armor": armor, "max_hp": max_hp, "poise": poise,
                "distance": distance, "points": REACT_POINTS}
    doc = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    have = doc.get("rows", {}) if doc.get("settings") == settings else {}
    todo = [x for x in {row_key(x): x for x in xs}.values() if row_key(x) not in have]
    log(f"interrupt: {len(have)} cached, {len(todo)} to compute -> {cache_path}")
    if todo:
        import multiprocessing as mp
        opp = Opponent()
        rows = Rows(opp, stats, armor, max_hp)
        p = rows.armor_poise() if poise is None else poise
        sims_path = SIMS_DIR / f"{settings['stamp']}-{opp.fingerprint()}-poise{p:g}.json"
        rows._memo = _read_sims(sims_path)
        log(f"interrupt: {len(rows._memo)} simulations kept from other stat blocks ({sims_path})")
        _W.update(rows=rows, poise=p, distance=distance)
        # One chunk per weapon, affinities together: the casts of one weapon share their timing, so
        # the simulation memo serves every affinity after the first.
        groups = {}
        for x in todo:
            groups.setdefault(x["weapon"], []).append(x)
        chunks = sorted((sorted(g, key=lambda x: x["affinity"]) for g in groups.values()), key=len, reverse=True)
        done = 0
        ctx = mp.get_context("fork")
        init, args = None, ()
        if throttled:
            cpus = allowed_cpus()
            throttle(cpus[:1])
            workers = cpus[1:] or cpus
            jobs = max(1, min(jobs, len(workers)))
            init, args = _worker_init, (workers, ctx.Value("i", 0))
            log(f"interrupt: throttled, caller on CPU {cpus[0]}, {jobs} workers on CPUs {workers[:jobs]}, "
                f"{THROTTLE_SHARE:.0%} each")
        with ctx.Pool(jobs, initializer=init, initargs=args) as pool:
            for res in pool.imap_unordered(_work, chunks):
                have.update(res)
                for r in res.values():
                    if "sim" in r:
                        rows._memo[sim_key(r["hits"], r["windows"], r["D"], r["gate"], r["fx_frames"],
                                           r["poise"])] = r["sim"]
                done += 1
                log(f"  {done}/{len(chunks)} weapons")
                if done % 10 == 0 or done == len(chunks):
                    cache_path.write_text(json.dumps({"settings": settings, "rows": have}))
                    _write_sims(sims_path, rows._memo)
    return have


# --------------------------------------------------------------------------------------------

def selftest() -> int:
    fails = []

    def check(cond, msg):
        print(("ok   " if cond else "FAIL ") + msg)
        if not cond:
            fails.append(msg)

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

    f = Fake()
    # B hits me at 15 (60 > 40 poise) before my hit at 20: my hit is lost, I take one B hit.
    r = simulate(f, [(20.0, 100.0)], [], 2.5, 25.0, 40.0)
    check(r["p"] == [0.0] and r["taken"] == B_HIT_HP, "a B hit before mine breaks me and cancels my hit")
    r = simulate(f, [(20.0, 100.0)], [], 2.5, 40.0, 40.0)
    check(r["taken"] == 2 * B_HIT_HP, "B keeps hitting a broken player until the gate")
    # My hit at 12 breaks B (100 > 50) before its first hit at 15: nothing taken.
    r = simulate(f, [(12.0, 100.0)], [], 2.5, 40.0, 40.0)
    check(r["p"] == [1.0] and r["taken"] == 0.0 and r["stopped_b"] == 1.0, "my hit first breaks B and stops it")
    # Simultaneous: both land.
    r = simulate(f, [(15.0, 100.0)], [], 2.5, 40.0, 40.0)
    check(r["p"] == [1.0] and r["taken"] == B_HIT_HP, "a simultaneous hit is a trade")
    # Hyperarmor covering 0-30 with +100 holds through B's first hit; B's second at 30 lands after it.
    r = simulate(f, [(25.0, 10.0)], [(0.0, 30.0, 100.0, 1.0, 0.0, 1.0)], 2.5, 40.0, 40.0)
    check(r["p"] == [1.0], "hyperarmor holds my hit")
    # Late break after the last hit: frames saved = gate - break frame.
    r = simulate(f, [(12.0, 10.0)], [], 2.5, 60.0, 40.0)
    check(r["p"] == [1.0] and abs(r["saved"] - 45.0) < 1e-9, "a break after my last hit saves the recovery")
    # Distance beyond B's reach: B runs 1 m (7.5 frames) first.
    r = simulate(f, [(20.0, 100.0)], [], 4.0, 40.0, 40.0)
    check(abs(r["approach"] - 7.5) < 1e-9 and r["p"] == [1.0], "B out of reach approaches first")
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
    print(f"{len(fails)} failed")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--skill")
    ap.add_argument("--weapon")
    ap.add_argument("--affinity", default="Keen")
    ap.add_argument("--grip", default="1H", choices=("1H", "2H"))
    ap.add_argument("--rows", type=Path, help="sweep JSON to take the row from (else a minimal row)")
    ap.add_argument("--poise", type=float)
    ap.add_argument("--distance", type=float)
    ap.add_argument("--stats", default="str=18,dex=80,int=7,fth=8,arc=9")
    ap.add_argument("--armor", default="Verdigris Gauntlets,Verdigris Armor,Verdigris Greaves")
    ap.add_argument("--max-hp", type=float, default=2292.5)
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not (a.skill and a.weapon):
        ap.error("--skill and --weapon, or --selftest")
    x = None
    if a.rows:
        x = next((r for r in json.loads(a.rows.read_text()) if r["ash"] == a.skill and r["weapon"] == a.weapon
                  and r.get("affinity", "Keen") == a.affinity and r["grip"] == a.grip), None)
    x = x or {"ash": a.skill, "weapon": a.weapon, "affinity": a.affinity, "grip": a.grip, "cat": "non-bullet",
              "gate": None, "melee_reach": None, "reach": None, "last": 0}
    stats = {k: int(v) for k, v in (p.split("=") for p in a.stats.split(","))}
    rows = Rows(Opponent(), stats, [n.strip() for n in a.armor.split(",")], a.max_hp)
    poise = rows.armor_poise() if a.poise is None else a.poise
    r = rows.profile(x, poise, a.distance)
    s = r["sim"]
    print(f"{a.affinity} {a.weapon} {a.grip} {a.skill}: D {r['D']} m, poise {poise:g}, gate f{r['gate']}, "
          f"B approach {s['approach']:.1f} f, B starts f{np.median(reaction_frames()):.1f} + approach")
    for (fr, pz, d, h), p in zip(r["hits"], s["p"]):
        print(f"  hit f{fr:5.1f} poise {pz:6.1f} dmg {d:6.1f} heal {h:5.1f}: delivered {100 * p:5.1f}%")
    print(f"  broken {100 * s['broken']:.1f}%, B stopped {100 * s['stopped_b']:.1f}%, HP taken {s['taken']:.0f}, "
          f"frames saved {s['saved']:.1f}")
    if "value" in x or "score" in x:
        v = x.get("score", 0)
        print(f"  multiplier at value {v}: {multiplier(r, v):.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
