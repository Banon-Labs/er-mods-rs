#!/usr/bin/env python3
"""The neutral game: who can start an exchange from where, and what movement buys there.

    python3 scripts/er-mechanics-neutral.py movement               # speeds and dodge tools, `MEASURED`
    python3 scripts/er-mechanics-neutral.py pool --rl 150          # the opponent pool's R1 reach
    python3 scripts/er-mechanics-neutral.py race --reach 4.45 --strike 18 --poise 300
    python3 scripts/er-mechanics-neutral.py --selftest

Write-up: `docs/er-mechanics/neutral.md`. Labels as in attacks.md: `VERIFIED` (regulation or
traced code), `MEASURED` (computed here from game files), `COMMUNITY` (the Smithbox decompile of
`c0000.hks`), `INFERRED` (a modelling choice).

The exchange module (`er-mechanics-exchange.exchange`) starts both players' attacks on the same
frame with the defender 2.5 m ahead, so a hit that reaches farther than 2.5 m buys nothing there
and a short one is never asked to walk in. This module starts the same exchange from the neutral
game instead:

* Movement (`locomotion`, `dodge_tools`, `MEASURED` from the hkx root motion): walk a000_020000
  1.50 m/s, run a000_020100 4.01 m/s, sprint a000_020200 6.04 m/s; the medium roll, backstep,
  Bloodhound's Step and Quickstep as i-frames, first R1 frame and distance at that frame. The
  behavior script moves at run index 1 in every direction but forward sprint
  (`ChangeMoveSpeedIndex`: `MoveSpeedIndexBLR` is capped at 1, and sprinting cancels the lock-on
  facing through `LockonFixedAngleCancel`, `COMMUNITY`), so a locked-on player closes and
  retreats at run speed (`INFERRED` for the back and side directions, which have no run clip of
  their own).
* Threat (`NeutralPool`): each pool build's R1 #1 world reach (`er-mechanics-reach`
  `world_reach_m`, `MEASURED` pose, class median when unposed) beside the exchange pool's strike
  frame, poise damage and hyperarmor windows.
* The race (`neutral_exchange`): the players start outside both reaches and both commit. Whoever
  reaches farther stands at his reach and strikes at his strike frame; the other must first close
  the difference, running (`k` = 30 / run speed frames a metre) or with a dodge toward him (its
  path up to its first R1 frame, then running), whichever lets his attack start first. Starting
  farther out adds the same time to both while both run, so it cancels (`INFERRED`: both close at
  run speed until the longer reach is in range, then only the shorter one moves). A hit that lands
  entirely inside the other's dodge i-frames misses him. Then first hit, poise and hyperarmor
  decide exactly as in `er-mechanics-exchange.exchange` (the same break rule, the same weight):
      f_neutral = 1 + EXCHANGE_WEIGHT x (P(win) - P(loss))
  The strike frame is the exchange's (first contact 2.5 m ahead); that it also stands for the
  contact at the attack's full reach is `INFERRED` (a lunge reaches its far end a little later).

What this module does not model: feints, the attacker picking his distance inside the other's
reach, chasing a retreating player (equal run speeds cancel), and anything beyond one engagement.
"""

import argparse
import importlib.util
import json
import math
import os
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
CACHE = Path.home() / ".cache/er-build-planner"


def _sibling(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_MODS = {}


def _mod(name: str):
    if name not in _MODS:
        _MODS[name] = _sibling(name)
    return _MODS[name]


TAE_FPS = 30.0
#: Locomotion clips (a000, category 0): the forward loop of each move-speed index. 020100 /
#: 020110 / 020120 share 4.01 m/s and 020130 is 2.79 (taken as the overloaded one, `INFERRED`
#: from the ordering); 020200-020220 are 6.04 and 020230 4.13.
LOCOMOTION_ANIMS = {"walk": 20000, "run": 20100, "sprint": 20200}
#: Dodge clips: the medium roll forward / back (a000 027110 / 027111, `er-builds-pvp`
#: SCORE_ENTRY_FRAMES), the medium backstep 027000, Bloodhound's Step a756 040080 / 040081 and
#: Quickstep a755 040080 / 040081 (the skills' paid forward / back animations, ashes-of-war.md 14a).
DODGE_ANIMS = {"roll": ((0, 27110), (0, 27111)), "backstep": (None, (0, 27000)),
               "step": ((756, 40080), (756, 40081)), "quickstep": ((755, 40080), (755, 40081))}
#: The tool every player carries into the race, `INFERRED`: the sweep builds sit at medium load.
DEFAULT_TOOLS = ("roll",)
#: Contact distance the exchange pool's strike frames are measured at
#: (`er-mechanics-exchange.STRIKE_DISTANCE_M`).
STRIKE_DISTANCE_M = 2.5

_LOCO = {}


def locomotion() -> dict:
    """{'walk'|'run'|'sprint': metres a second}: the clip's root-motion distance over its length
    (`MEASURED`, er-hkx-pose)."""
    if not _LOCO:
        pose = _mod("er-hkx-pose")
        for key, anim in LOCOMOTION_ANIMS.items():
            clip = pose.load_animation(0, anim)
            x, _, z, _ = clip.root_motion(clip.duration)
            _LOCO[key] = round(math.hypot(x, z) / clip.duration, 3)
    return dict(_LOCO)


def frames_per_metre() -> float:
    """Real frames a locked-on player takes to run one metre (`locomotion` run)."""
    return TAE_FPS / locomotion()["run"]


_TOOLS = {}


def _tool(cat_anim, sign):
    if cat_anim is None:
        return None
    m = _mod("er-mechanics-ashes").evasion_motion(*cat_anim)
    return tool_from_motion(m, sign) if m else None


def tool_from_motion(m: dict, sign: float = 1.0) -> dict:
    """A dodge tool from one `er-mechanics-ashes.evasion_motion` result; `sign` 1 for a forward
    animation (its -Z path is toward the other player), -1 for a backward one."""
    ready = m["ready"] if m["ready"] is not None else len(m["path"]) - 1
    k = min(int(math.floor(ready)), len(m["path"]) - 1)
    # Forward is -Z in the clip; `along` is the distance toward the other player.
    along = [sign * -z for _, z in m["path"]]
    return {"anim": m["anim"], "iframes": m["iframes"], "ready": round(ready, 1), "dodge": m["dodge"],
            "along": along, "at_ready": round(along[k], 3), "distance": m["distance"]}


def dodge_tools(kind: str) -> dict:
    """{'fwd', 'back'}: one dodge's forward and backward animation (`DODGE_ANIMS`), each with
    `iframes` (unconditional JumpTable 8 from frame 0), `ready` (first R1), `along` (metres toward
    the other player per real frame) and `at_ready`. Cached."""
    if kind not in _TOOLS:
        fwd, back = DODGE_ANIMS[kind]
        _TOOLS[kind] = {"fwd": _tool(fwd, 1.0), "back": _tool(back, -1.0)}
    return _TOOLS[kind]


def arrival(gap: np.ndarray, tools=DEFAULT_TOOLS, k: float | None = None) -> tuple:
    """Per gap (metres to close before the attack can start): (frames until the attack starts,
    i-frames of the dodge used, from the start of the approach; 0 when running). Running costs
    `gap x k`; a forward dodge costs its first R1 frame plus running whatever its path to that
    frame leaves (`INFERRED`: one dodge, then running; it moves him up to that frame and no
    farther)."""
    k = frames_per_metre() if k is None else k
    gap = np.maximum(np.asarray(gap, float), 0.0)
    best = gap * k
    ifr = np.zeros_like(best)
    for kind in tools:
        m = kind if isinstance(kind, dict) else dodge_tools(kind)["fwd"]
        if not m:
            continue
        t = m["ready"] + np.maximum(0.0, gap - m["at_ready"]) * k
        better = (t < best) & (gap > 0)
        best = np.where(better, t, best)
        ifr = np.where(better, m["iframes"], ifr)
    return best, ifr


# --------------------------------------------------------------------------------------------
# the pool's threat


def pool_reaches(pool_raw: dict, cache: bool = True) -> dict:
    """{profile key '<weapon>|1h|2h': {'reach', 'active', 'source'}}: the R1 #1 world reach and
    active frames of every exchange-pool profile (`er-mechanics-reach.reach_summary`, the class
    median when unposed). Cached beside the exchange pool, keyed on its stamp."""
    ex = _mod("er-mechanics-exchange")
    stamp = pool_raw.get("stamp")
    # Named by the pool's source stamp as well (`er-mechanics-exchange.opponent_pool`).
    tag = f"-{stamp[-1]}" if stamp else ""
    path = CACHE / f"neutral-pool-reach-{pool_raw['rl'][0]}-{pool_raw['rl'][1]}{tag}.json"
    if cache and path.exists():
        got = json.loads(path.read_text())
        if got.get("stamp") == stamp:
            return got["reaches"]
    reach_mod, atk_mod = _mod("er-mechanics-reach"), _mod("er-mechanics-attacks")
    ids = ex.weapon_ids()
    reg = atk_mod.Regulation(None)
    out = {}
    for key in sorted(pool_raw["profiles"]):
        name, grip = key.rsplit("|", 1)
        wid = ids.get(name)
        if wid is None:
            continue
        two = grip == "2h"
        slot = ("2h_" if two else "") + ex.OPPONENT_SLOT
        g = "both" if two else "one"
        row = reach_mod.reach_summary(wid, g).get(slot) or {}
        reach, source = row.get("world_reach_m"), "world"
        if reach is None:
            reach, source = reach_mod.class_fallback(wid, g, slot).get("world_reach_m"), "inferred"
        atk = next((a for a in atk_mod.weapon_attacks(reg, wid, g) if a["slot"] == slot), None)
        wins = (atk or {}).get("hit_windows") or []
        active = round(wins[0][1] - wins[0][0], 1) if wins else None
        out[key] = {"reach": reach, "active": active, "source": source}
    if cache:
        path.write_text(json.dumps({"stamp": stamp, "reaches": out}))
    return out


class NeutralPool:
    """The exchange pool (`er-mechanics-exchange.Pool`) with each profile's reach and active
    frames: arrays in the pool's profile order."""

    def __init__(self, pool, reaches: dict, tools=DEFAULT_TOOLS):
        self.pool = pool
        fb_reach = float(np.median([v["reach"] for v in reaches.values() if v.get("reach")] or [3.0]))
        fb_active = float(np.median([v["active"] for v in reaches.values() if v.get("active")] or [3.0]))
        self.reach = np.array([(reaches.get(k) or {}).get("reach") or fb_reach for k in pool.keys], float)
        self.active = np.array([(reaches.get(k) or {}).get("active") or fb_active for k in pool.keys], float)
        self.tools = tuple(tools)
        self.k = frames_per_metre()
        self.weight = np.bincount(pool.build_prof, weights=pool.weight,
                                  minlength=len(pool.keys)).astype(float)
        self.cover = None

    def mean_reach(self) -> float:
        return float((self.reach * self.weight).sum() / self.weight.sum())

    @classmethod
    def from_results(cls, pool, reaches: dict, results: list, slots_fn=None, tools=DEFAULT_TOOLS):
        """The pool throwing what each build's weapon and grip throws in a stored `er-builds-pvp`
        ranking (`results`) instead of R1 #1 alone: every moveset family's best opener
        (`moveset.families[*].opener`) at that family's use share, renormalised over the openers
        that have a `neutral_in` (strike frame at 2.5 m from the opener's own input, entry and R2
        lead-in included; world reach; PvP poise; TAE 795 windows on the same clock; live
        frames) and a `dmg`. `slots_fn(row)` gives the row's slots with the synthesized jump
        openers (`er-builds-pvp.jump_openers`), else the stored slots alone. A build whose weapon
        has no row, or no opener resolves, keeps its R1 #1 (`pool`'s row, `reaches` for its reach).
        Each build keeps its own armor poise across its openers. `cover` records the entry share
        that fell back to R1 #1, the share of dropped families, and the opener shares."""
        ex = _mod("er-mechanics-exchange")
        by = {ex.profile_key(r["weapon"], r["two"]): r for r in results}
        fb_reach = float(np.median([v["reach"] for v in reaches.values() if v.get("reach")] or [3.0]))
        fb_active = float(np.median([v["active"] for v in reaches.values() if v.get("active")] or [3.0]))
        keys, rows, idx, entries, rreach = [], [], {}, [], {}
        cover = {"r1_fallback": 0.0, "dropped": {}, "openers": {}}
        nb = len(pool.build_prof)
        memo = {}
        for b, (pk, poise) in enumerate(zip(pool.build_prof, pool.build_poise)):
            key = pool.keys[pk]
            if key not in memo:
                r = by.get(key)
                opts, dropped = [], {}
                if r is not None:
                    slots = slots_fn(r) if slots_fn else (r.get("slots") or {})
                    for fam, f in ((r.get("moveset") or {}).get("families") or {}).items():
                        if not f.get("share"):
                            continue
                        s = slots.get(f.get("opener")) or {}
                        ni = s.get("neutral_in")
                        if not ni or not ni.get("reach") or ni.get("strike") is None or not s.get("dmg"):
                            dropped[fam] = dropped.get(fam, 0.0) + f["share"]
                            continue
                        opts.append((f["share"], f["opener"], {
                            "startup": float(ni["strike"]), "poise": float(ni.get("poise") or 0.0),
                            "hyper": [tuple(h) for h in ni.get("hyper") or []], "dmg": float(s["dmg"]),
                            "reach": float(ni["reach"]), "active": float(ni.get("active") or 3.0)}))
                tot = sum(o[0] for o in opts)
                if tot:
                    opts = [(w / tot, op, row) for w, op, row in opts]
                else:
                    rr = reaches.get(key) or {}
                    opts = [(1.0, ex.OPPONENT_SLOT, {
                        "startup": float(pool.startup[pk]), "poise": float(pool.poise_dealt[pk]),
                        "hyper": list(pool.hyper[pk]), "dmg": float(pool.dmg[pk]),
                        "reach": rr.get("reach") or fb_reach, "active": rr.get("active") or fb_active})]
                    dropped = None
                memo[key] = (opts, dropped)
            opts, dropped = memo[key]
            if dropped is None:
                cover["r1_fallback"] += 1.0 / nb
            else:
                for fam, sh in dropped.items():
                    cover["dropped"][fam] = cover["dropped"].get(fam, 0.0) + sh / nb
            for w, opener, row in opts:
                ident = f"{key}#{opener}"
                if ident not in idx:
                    idx[ident] = len(rows)
                    keys.append(ident)
                    rows.append(row)
                    rreach[ident] = {"reach": row["reach"], "active": row["active"]}
                entries.append((idx[ident], float(poise), w))
                cover["openers"][opener] = cover["openers"].get(opener, 0.0) + w / nb
        out = cls(ex.Pool.weighted(pool, keys, rows, entries), rreach, tools)
        out.cover = cover
        return out


def _window_at(windows, frame):
    for s, e, bonus, mult in windows:
        if s <= frame < e:
            return bonus, mult
    return 0.0, 1.0


def neutral_exchange(npool: NeutralPool, strike: float, reach: float, poise: float, hyper: list,
                     active: float = 3.0, tools=DEFAULT_TOOLS, weight: float | None = None,
                     k: float | None = None, dmg: float | None = None) -> dict:
    """The exchange of one attack against the pool, started from the neutral game (module
    docstring). `strike` is the attack's strike frame 2.5 m ahead (`er-mechanics-exchange`
    `strike_frame`), `reach` its world reach, `poise` its PvP poise damage (menu x saRate),
    `hyper` its TAE 795 windows [(start, end, bonus, multiplier)] from its own start, `active`
    its live frames, `tools` the dodges the attacker may close with, `k` the attacker's frames a
    metre when he closes slower than a run (a held crouch, `er-mechanics-timing-mixup`), `dmg` its
    own hit, read only when the pool prices trades (`trade_clamp`). Returns the outcome shares,
    `f_neutral`, and the shares of the pool it outreaches and that must approach it."""
    ex = _mod("er-mechanics-exchange")
    p = npool.pool
    w = ex.EXCHANGE_WEIGHT if weight is None else weight
    reach = float(reach)
    dist = np.maximum(npool.reach, reach)
    my_start, my_ifr = arrival(dist - reach, tools, npool.k if k is None else k)
    their_start, their_ifr = arrival(dist - npool.reach, npool.tools, npool.k)
    mine = my_start + strike
    theirs = their_start + p.startup
    # A hit whose whole live window falls inside the other's i-frames misses him.
    they_miss = (my_ifr > 0) & (theirs >= 0) & (theirs + npool.active <= my_ifr)
    i_miss = (their_ifr > 0) & (mine >= 0) & (mine + active <= their_ifr)
    first = ((mine < theirs) & ~i_miss) | (they_miss & ~i_miss)
    second = ((mine > theirs) & ~they_miss) | (i_miss & ~they_miss)
    # Their hyperarmor at my hit, from their own start; mine at theirs, from mine.
    their = [_window_at(h, t - s) for h, t, s in zip(p.hyper, mine, their_start)]
    their_bonus = np.array([b for b, _ in their])[p.build_prof]
    their_mult = np.array([m for _, m in their])[p.build_prof]
    breaks_them = poise * their_mult >= p.build_poise + their_bonus
    my_w = [_window_at(hyper, t - s) for t, s in zip(theirs, my_start)]
    dealt = p.poise_dealt * np.array([m for _, m in my_w])
    room = dealt - np.array([b for b, _ in my_w])
    p_break_me = np.searchsorted(p.my_poise, room, side="right") / len(p.my_poise)
    f_b, s_b = first[p.build_prof], second[p.build_prof]
    # A hit that lands on a player whose own hit whiffed has nothing to trade against.
    whiff_b, iwhiff_b = (they_miss & ~i_miss)[p.build_prof], (i_miss & ~they_miss)[p.build_prof]
    win_b = f_b & (breaks_them | whiff_b)
    loss_b = np.where(s_b, np.where(iwhiff_b, 1.0, p_break_me[p.build_prof]), 0.0)
    win, loss = p.mean(win_b), p.mean(loss_b)
    outreach = p.mean((npool.reach < reach)[p.build_prof])
    f = 1.0 + w * (win - loss)
    priced = {}
    clamp = getattr(p, "trade_clamp", None)
    if clamp is not None and dmg:
        # Trades priced by damage (`er-mechanics-exchange.priced_net`, exchange.md section 2a).
        net_hp = ex.priced_net(p, win_b, loss_b, float(dmg))
        f = 1.0 + w * max(-clamp, min(clamp, net_hp))
        priced = {"net_hp": net_hp}
    return {"win": win, "loss": loss, "trade": 1.0 - win - loss, "net": win - loss, **priced,
            "p_first": p.mean(f_b), "p_second": p.mean(s_b),
            "outreach": outreach, "dodge_in": p.mean((my_ifr > 0)[p.build_prof]),
            "f_neutral": f, "strike": strike, "reach": reach,
            "tools": [x if isinstance(x, str) else x.get("anim") for x in tools]}


def slot_neutral(npool: NeutralPool, slot: dict, atk: dict | None, entry: float = 0.0,
                 tools=DEFAULT_TOOLS, k: float | None = None) -> dict | None:
    """`neutral_exchange` of one `er-builds-pvp` slot, read from its exchange result (`strike_frame`,
    the entry already in) and its reach; the hyperarmor windows move by the R2 lead-in and the
    entry as `er-mechanics-exchange.slot_exchange` moves them. None without a strike frame or
    reach."""
    ex = slot.get("exchange") or {}
    strike, reach = ex.get("strike_frame"), slot.get("reach")
    if strike is None or not reach:
        return None
    lead = (atk or {}).get("release_lead_in") or 0.0
    hyper = _mod("er-mechanics-exchange").hyper_windows(atk or {}, lead + entry)
    return neutral_exchange(npool, strike, reach, slot.get("poise") or 0.0, hyper,
                            slot.get("active") or 3.0, tools, k=k, dmg=slot.get("dmg"))


# --------------------------------------------------------------------------------------------
# self test


def selftest() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok   " if cond else "FAIL ") + msg)
        ok = ok and bool(cond)

    loco = locomotion()
    check(abs(loco["walk"] - 1.5) < 0.02 and abs(loco["run"] - 4.01) < 0.02 and abs(loco["sprint"] - 6.04) < 0.02,
          f"locomotion from the hkx root motion: walk 1.50, run 4.01, sprint 6.04 m/s ({loco})")
    roll, step = dodge_tools("roll"), dodge_tools("step")
    check(roll["fwd"]["iframes"] == 13 and roll["fwd"]["ready"] == 20 and step["fwd"]["iframes"] == 10
          and step["fwd"]["ready"] == 17,
          f"medium roll i-frames f0-13, R1 from 20; Bloodhound's Step f0-10, R1 from 17 (ashes-of-war.md 14a)")
    check(step["fwd"]["at_ready"] > roll["fwd"]["at_ready"] > 3.0,
          f"the step carries farther by its R1 frame than the roll ({step['fwd']['at_ready']} vs "
          f"{roll['fwd']['at_ready']} m)")
    k = frames_per_metre()
    t, ifr = arrival(np.array([0.0, 1.0, 5.0]), ("roll",), k)
    check(t[0] == 0 and abs(t[1] - k) < 1e-9 and ifr[1] == 0 and t[2] < 5.0 * k and ifr[2] == 13,
          f"arrival: nothing to close costs nothing, 1 m is run ({t[1]:.1f} f), 5 m is rolled ({t[2]:.1f} f)")
    t2, _ = arrival(np.array([5.0]), ("roll", "step"), k)
    check(t2[0] < t[2], "the step closes 5 m sooner than the roll")

    # A synthetic pool: two profiles at the same strike frame, one short (2.5 m) and one long (5 m).
    class P:
        pass

    p = P()
    p.keys = ["short", "long"]
    p.startup = np.array([16.0, 16.0])
    p.poise_dealt = np.array([50.0, 50.0])
    p.hyper = [[], []]
    p.build_prof = np.array([0, 1])
    p.build_poise = np.array([40.0, 40.0])
    p.my_poise = np.sort(np.array([40.0, 40.0]))
    p.weight = None
    p.dmg = np.array([400.0, 400.0])
    p.mean = lambda x: float(np.mean(np.asarray(x, float)))
    npool = NeutralPool(p, {"short": {"reach": 2.5, "active": 3.0}, "long": {"reach": 5.0, "active": 3.0}})
    e = neutral_exchange(npool, 16.0, 4.0, 100.0, [])
    check(abs(e["outreach"] - 0.5) < 1e-9 and e["win"] == 0.5 and e["loss"] == 0.5,
          f"a 4 m attack at the pool's frame beats the 2.5 m build (he walks 1.5 m) and loses to the 5 m "
          f"one (it walks 1 m) ({e['win']}, {e['loss']})")
    same = neutral_exchange(npool, 16.0, 2.5, 100.0, [])
    check(same["win"] == 0.0 and same["loss"] == 0.5,
          "at the short build's own reach and frame: a trade with it, a loss to the long one")
    armor = neutral_exchange(npool, 16.0, 2.5, 100.0, [(0.0, 60.0, 100.0, 1.0)])
    check(armor["loss"] == 0.5, "hyperarmor does not cover the approach: the long build's hit lands while he "
                                "is still running in")
    armor = neutral_exchange(npool, 20.0, 5.0, 100.0, [(0.0, 60.0, 100.0, 1.0)])
    check(armor["loss"] == 0.0 and armor["win"] == 0.5,
          "from its own reach, hyperarmor trades through the long build's earlier hit, and the short "
          "build is hit while it walks in")
    check(neutral_exchange(npool, 16.0, 6.0, 100.0, [])["f_neutral"] > e["f_neutral"] > same["f_neutral"],
          "more reach scores higher")
    fast = neutral_exchange(npool, 10.0, 6.0, 100.0, [])
    check(fast["loss"] == 0.5 and fast["win"] == 0.5,
          "a poke live over f10-13 lands inside the roll the short build closes 3.5 m with (i-frames "
          "f0-13), which then hits back")

    # The weighted pool: the same two profiles as a real `er-mechanics-exchange.Pool`, then thrown
    # from a ranking where build A opens half with its 2.5 m R1 and half with a 5 m jump, and B
    # has no row (it keeps its R1 #1).
    ex = _mod("er-mechanics-exchange")
    raw = {"profiles": {"A|1h": {"startup": 16.0, "poise": 50.0, "hyper": [], "stamina": 10},
                        "B|1h": {"startup": 16.0, "poise": 50.0, "hyper": [], "stamina": 10}},
           "builds": [["A|1h", 40.0], ["B|1h", 40.0]], "poise": [40.0, 40.0], "bar": 150.0, "ref_per_bar": 7.0}
    base = ex.Pool(raw)
    reaches = {"A|1h": {"reach": 2.5, "active": 3.0}, "B|1h": {"reach": 5.0, "active": 3.0}}
    plain = NeutralPool(base, reaches)
    rows = [{"startup": 16.0, "poise": 50.0, "hyper": [], "dmg": 388.0}] * 2
    same_w = NeutralPool(ex.Pool.weighted(base, base.keys, rows, [(0, 40.0, 3.0), (1, 40.0, 3.0)]), reaches)
    a, b = neutral_exchange(plain, 16.0, 4.0, 100.0, []), neutral_exchange(same_w, 16.0, 4.0, 100.0, [])
    check(all(abs(a[k] - b[k]) < 1e-12 for k in ("win", "loss", "f_neutral", "outreach")),
          "equal entry weights reproduce the unweighted pool")
    ni = {"poise": 50.0, "hyper": [], "active": 3.0}
    res = [{"weapon": "A", "two": False,
            "moveset": {"families": {"r1": {"opener": "r1_1", "share": 0.3},
                                     "jump": {"opener": "jump_r1_f", "share": 0.3},
                                     "move": {"opener": "run_r1", "share": 0.4}}},
            "slots": {"r1_1": {"dmg": 300.0, "neutral_in": {**ni, "strike": 16.0, "reach": 2.5}},
                      "run_r1": {"dmg": 300.0}}}]
    jumps = {"jump_r1_f": {"dmg": 500.0, "neutral_in": {**ni, "strike": 16.0, "reach": 5.0}}}
    fam = NeutralPool.from_results(base, reaches, res, lambda r: {**r["slots"], **jumps})
    e = neutral_exchange(fam, 16.0, 4.0, 100.0, [])
    check(abs(e["win"] - 0.25) < 1e-12 and abs(e["loss"] - 0.75) < 1e-12,
          f"family pool: A's R1 and jump at half each once the unresolved run R1 is dropped, B on its "
          f"R1 #1 (win 0.25, loss 0.75: {e['win']:.3f}, {e['loss']:.3f})")
    cov = fam.cover
    check(abs(cov["r1_fallback"] - 0.5) < 1e-12 and abs(cov["dropped"]["move"] - 0.2) < 1e-12
          and abs(cov["openers"]["jump_r1_f"] - 0.25) < 1e-12 and sorted(fam.pool.dmg) == [300.0, 388.0, 500.0],
          f"coverage: B falls back, the run R1 is dropped, rows carry their own dmg ({cov})")
    # Priced trades on the first synthetic pool (both builds hit for 400): an 800 hit that beats
    # the short build and loses to the long one is net (800 / 600 - 400 / 600) / 2 = 1/3, not 0.
    p.trade_clamp = 1.0
    pr = neutral_exchange(npool, 16.0, 4.0, 100.0, [], dmg=800.0)
    check(abs(pr["net_hp"] - 1.0 / 3.0) < 1e-12 and abs(pr["f_neutral"] - (1.0 + 0.25 / 3.0)) < 1e-12
          and neutral_exchange(npool, 16.0, 4.0, 100.0, [])["f_neutral"] == 1.0,
          f"priced trades: net 1/3 with an 800 hit ({pr['net_hp']:.4f}); no damage given, unpriced")
    p.trade_clamp = None
    print("selftest", "passed" if ok else "FAILED")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", choices=("movement", "pool", "race"))
    ap.add_argument("--rl", type=int, default=150)
    ap.add_argument("--window", type=int, default=10)
    ap.add_argument("--reach", type=float, default=2.5)
    ap.add_argument("--strike", type=float, default=16.0)
    ap.add_argument("--poise", type=float, default=0.0)
    ap.add_argument("--tools", default="roll", help="comma list of dodge tools the attacker closes with")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.cmd == "movement":
        print("locomotion m/s:", locomotion(), f"({frames_per_metre():.2f} real frames a metre at run)")
        for kind in DODGE_ANIMS:
            for d, m in dodge_tools(kind).items():
                if m:
                    print(f"  {kind:<10}{d:<5}{m['anim']:<14} i-frames {m['iframes']:>5}  R1 {m['ready']:>5}  "
                          f"at R1 {m['at_ready']:>5.2f} m  end {m['distance']:.2f} m")
        return 0
    ex = _mod("er-mechanics-exchange")
    atk = _mod("er-mechanics-attacks")
    mirror = CACHE / "builds.jsonl"
    raw = ex.opponent_pool(atk.Regulation(None), mirror, a.rl - a.window, a.rl + a.window)
    pool = ex.Pool(raw)
    npool = NeutralPool(pool, pool_reaches(raw))
    if a.cmd == "pool":
        r = npool.reach[pool.build_prof]
        print(f"{len(pool.keys)} profiles, {pool.n} builds; R1 #1 world reach mean {npool.mean_reach():.2f} m, "
              + ", ".join(f"p{q} {np.percentile(r, q):.2f}" for q in (10, 25, 50, 75, 90)))
        return 0
    if a.cmd == "race":
        tools = tuple(x.strip() for x in a.tools.split(",") if x.strip())
        print(json.dumps(neutral_exchange(npool, a.strike, a.reach, a.poise, [], tools=tools), indent=1))
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
