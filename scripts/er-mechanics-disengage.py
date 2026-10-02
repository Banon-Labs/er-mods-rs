#!/usr/bin/env python3
"""Disengaging: what a dodge buys a player who wants out of the fight for a moment.

    python3 scripts/er-mechanics-disengage.py tools              # every dodge's frames and distance
    python3 scripts/er-mechanics-disengage.py gates              # roll / skill / R1 out of staggers and blocks
    python3 scripts/er-mechanics-disengage.py flask [--level 12] # Flask of Crimson Tears: timing and heal
    python3 scripts/er-mechanics-disengage.py race [--chaser sprint|run] [--delay 10.5] [--hidden]
    python3 scripts/er-mechanics-disengage.py pool --opponents-from rank.json   # the scored term, per tool
    python3 scripts/er-mechanics-disengage.py pool --opponents-from rank.json --paired-strings 0.25 0.5
    python3 scripts/er-mechanics-disengage.py cross-hand         # combo.md links a step escapes (slow, ~minutes)
    python3 scripts/er-mechanics-disengage.py --selftest

Write-up: `docs/er-mechanics/disengage.md`. Labels as in the sibling docs: `VERIFIED` (regulation
value or code read out of the 1.16.2 executable, shift 0), `TAE` (decoded TimeAct), `MEASURED`
(computed here from game files), `COMMUNITY` (the Smithbox decompile of `c0000.hks`, or the
Smithbox TAE template's event names), `INFERRED` (a modelling choice).

What an action needs to leave an animation (`VERIFIED`, `_ChrActionFlag` 0x1404275e0 read on the
named 1.16.2 dump): an input window and a cancel window that overlap.

    roll   input 25 / 87, cancel 26 (`SP_MOVE`, `BACKSTEP`, `ROLLING`)
    skill  L2 (ChrActionType 3). Cancel 16 and 118 allow L2 only when actionAnimationFlags & 0x7f8
           is 0; 103 when it is 8; 104 when it is 8 or 16. Input: 9 and 87 give L2 only when it is
           0 (AllowInputLHAttack), 106 when it is 8 or 16. What sets those flag bits was not traced,
           so both readings are measured and the later one is used (`INFERRED`).
    item   input 30 / 87, cancel 31 (USE_ITEM)
    r1     input 1 / 87, cancel 4 / 115;   move: cancel 11 / 78, no input

Out of a stagger (`COMMUNITY` `DamageCommonFunction`): `ExecEvasion` is called with
`UseChainRecover`, so the roll also waits for EzState flag 2 / 3 / 4 / 5 (DamageCount 1 / 2 / 3 /
4+, TAE event 227); `ExecAttack`, which carries L2 and so every skill, is not gated by it.

Bloodhound's Step and Quickstep (`COMMUNITY` `SWORDARTS_REQUEST_RIGHT_STEP`): not locked on, the
behavior script plays the forward animation turned to the stick (direction 0), so a player running
away steps away with a040080; locked on, back is a040081. A second step inside the first plays
`W_SwordArtsRolling_SelfTrans`: the step chains into itself. The roll does the same
(`RollingDirectionIndex` 0 when not locked on, `Rolling_Selftrans`).

The race (`INFERRED` throughout, see `disengage.md`): the escaper leaves along a straight line,
the chaser follows at `CHASER_SPEED` (sprint 6.04 m/s: a chaser who sprints drops his lock-on,
neutral.md section 1) from the later of his own recovery and his reaction to the escape. Once
both are at top speed the gap stays, so the escaper drinks at the first frame his last dodge lets
him use an item. The chaser then hits him with whichever of his running attack, sprinting jump
attacks or R1 lands first from that distance. A hit before the flask's `Consume Selected Goods`
event (f31) denies the heal; one between f31 and f54 (the first frame anything cancels the drink)
trades it; later, the drinker rolls away and keeps it. He drinks only when that is worth it.
"""

import argparse
import importlib.util
import json
import math
import struct
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
CACHE = Path.home() / ".cache/er-build-planner"
TAE_FPS = 30.0


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


# --------------------------------------------------------------------------------------------
# action gates (module docstring)

TAE_JUMP_TABLE, TAE_JUMP_TABLE_EARLY, TAE_EZSTATE_FLAG = 0, 300, 227
#: TAE 193 `Set Opacity Keyframe` (`COMMUNITY` template name): Args f32 opacity at the event's
#: start and at its end.
TAE_OPACITY = 193
#: TAE 65 `Consume Selected Goods` (`COMMUNITY` template name): the frame the quick item is used.
TAE_CONSUME_GOODS = 65
JUMP_TABLE_STATE_GATE_OFFSET = 0xE
#: {action: (input JumpTable ids or None, cancel ids)}; `VERIFIED` (module docstring).
ACTIONS = {
    "r1": ((1, 87), (4, 115)),
    "roll": ((25, 87), (26,)),
    "skill": ((9, 87), (16, 118)),
    "skill_q": ((106,), (103, 104)),
    "item": ((30, 87), (31,)),
    "guard": ((21, 87), (22,)),
    "move": (None, (11, 78)),
}


def _atk():
    return _mod("er-mechanics-attacks")


def _reach():
    return _mod("er-mechanics-reach")


def _ash():
    return _mod("er-mechanics-ashes")


def windows(events, to_real) -> dict:
    """{action: {'input': [(s, e)], 'cancel': [(s, e)]}} in real frames. A JumpTable event with a
    stateInfo gate (Args+0xe) is left out, as `er-mechanics-attacks.recovery_windows` does. An early
    event (300) of weight type 0 is resolved; other types are left out (`INFERRED`: the roll's and
    the step's type-1 early events fall a frame or two before their plain windows, so leaving them
    out can only make an action later)."""
    atk = _atk()
    out = {k: {"input": [], "cancel": []} for k in ACTIONS}
    for e in events:
        if e.type == TAE_JUMP_TABLE:
            jid = struct.unpack_from("<i", e.params, 0)[0]
            if struct.unpack_from("<H", e.params, JUMP_TABLE_STATE_GATE_OFFSET)[0]:
                continue
            span = (to_real(e.start) * TAE_FPS, to_real(min(e.end, 1e3)) * TAE_FPS)
        elif e.type == TAE_JUMP_TABLE_EARLY:
            jid, kind = struct.unpack_from("<hh", e.params, 0)
            if kind != 0:
                continue
            w = atk._early_interval(e, 0.0)
            if not w:
                continue
            span = (to_real(w[0]) * TAE_FPS, to_real(w[1]) * TAE_FPS)
        else:
            continue
        for key, (inputs, cancels) in ACTIONS.items():
            if inputs and jid in inputs:
                out[key]["input"].append(span)
            if jid in cancels:
                out[key]["cancel"].append(span)
    return out


def first_open(win: dict, gated: bool):
    if not gated:
        starts = [c[0] for c in win["cancel"]]
    else:
        starts = [max(i[0], c[0]) for i in win["input"] for c in win["cancel"] if max(i[0], c[0]) < min(i[1], c[1])]
    return round(min(starts), 1) if starts else None


_CLIPS = {}


def clip(category: int, anim: int) -> dict | None:
    """One animation's gates (real frames from its first frame), EzState flag openings,
    opacity keyframes, the goods-consume frame and its length. Cached."""
    key = (category, anim)
    if key in _CLIPS:
        return _CLIPS[key]
    cat, src, events = _reach().resolve_clip(category, anim)
    if not events:
        _CLIPS[key] = None
        return None
    atk = _atk()
    to_real = atk.clip_to_real(events)
    win = windows(events, to_real)
    gates = {k: first_open(win[k], ACTIONS[k][0] is not None) for k in ACTIONS}
    # The L2 reading that comes later (module docstring).
    both = [g for g in (gates["skill"], gates["skill_q"]) if g is not None]
    gates["skill_late"] = max(both) if both else None
    flags, opacity, consume = {}, [], None
    for e in events:
        s = round(to_real(e.start) * TAE_FPS, 1)
        if e.type == TAE_EZSTATE_FLAG:
            f = struct.unpack_from("<i", e.params, 0)[0]
            flags[f] = min(flags.get(f, 1e9), s)
        elif e.type == TAE_OPACITY:
            a, b = struct.unpack_from("<ff", e.params, 0)
            opacity.append((s, round(to_real(min(e.end, 1e3)) * TAE_FPS, 1), round(a, 3), round(b, 3)))
        elif e.type == TAE_CONSUME_GOODS:
            consume = s if consume is None else min(consume, s)
    path = atk.hkx_path(*_reach().hkx_source(cat, src))
    dur = atk.hkx_duration(path) if path else None
    out = {"anim": f"a{category:03d}_{anim:06d}", "gates": gates, "flags": flags,
           "opacity": sorted(opacity), "consume": consume,
           "length": round(to_real(dur[0]) * TAE_FPS, 1) if dur else None}
    _CLIPS[key] = out
    return out


def hidden_span(c: dict):
    """(first, last) real frame the character is fully transparent (opacity keyframes at 0), or
    None. `COMMUNITY` for what event 193 does."""
    zero = [(s, e) for s, e, a, b in c["opacity"] if a == 0 and b == 0]
    return (min(s for s, _ in zero), max(e for _, e in zero)) if zero else None


# --------------------------------------------------------------------------------------------
# the dodges

#: Each escape tool: (category, anim, direction). `away` = the clip's forward travel turned away
#: from the chaser (not locked on, module docstring), `back` = the backward clip (locked on).
#: Which roll clip belongs to which equip load is read from the order of the ten-block (light,
#: medium, heavy, overloaded, and 027140 a fifth variant) and is `INFERRED`; medium 027110 is the
#: one the ranking uses (`er-builds-pvp` `SCORE_ENTRY_FRAMES`).
TOOLS = {
    "roll light": (0, 27100, "away"), "roll medium": (0, 27110, "away"), "roll heavy": (0, 27120, "away"),
    "roll overloaded": (0, 27130, "away"), "roll 027140": (0, 27140, "away"),
    "roll medium back": (0, 27111, "back"),
    "backstep 027000": (0, 27000, "back"), "backstep 027010": (0, 27010, "back"),
    "backstep 027020": (0, 27020, "back"), "backstep 027030": (0, 27030, "back"),
    "step": (756, 40080, "away"), "step back": (756, 40081, "back"),
    "step 040085": (756, 40085, "away"),
    "quickstep": (755, 40080, "away"), "quickstep back": (755, 40081, "back"),
}
#: The chain action per tool family: a roll chains with the roll, a step with L2.
CHAIN_ACTION = {"roll": "roll", "backstep": "roll", "step": "skill_late", "quickstep": "skill_late"}
_TOOLS = {}


def tool(name: str) -> dict | None:
    """One escape tool: `away` (metres away from where it started, per real frame; the clip's
    root motion projected on the escape direction, standing still after the clip), `iframes`,
    and its gates: `r1`, `roll`, `skill` (both L2 readings, later one), `item`, `move`, `chain`
    (the frame the same tool can start again). Cached."""
    if name in _TOOLS:
        return _TOOLS[name]
    cat, anim, direction = TOOLS[name]
    m = _ash().evasion_motion(cat, anim)
    c = clip(cat, anim)
    if not m or not c:
        _TOOLS[name] = None
        return None
    sign = -1.0 if direction == "away" else 1.0
    away = [sign * z for _, z in m["path"]]
    family = name.split()[0]
    g = c["gates"]
    out = {"name": name, "anim": m["anim"], "direction": direction, "iframes": m["iframes"],
           "away": away, "end": round(away[-1], 2), "frames": len(away) - 1,
           "r1": g["r1"], "roll": g["roll"], "skill": g["skill_late"], "skill_0": g["skill"],
           "skill_8": g["skill_q"], "item": g["item"], "move": g["move"],
           "chain": g[CHAIN_ACTION[family]], "hidden": hidden_span(c)}
    _TOOLS[name] = out
    return out


def tool_from_motion(m: dict, family: str = "step") -> dict | None:
    """A `tool` from an `er-mechanics-ashes.evasion_motion` result (a skill's own step)."""
    cat, anim = int(m["anim"][1:4]), int(m["anim"][5:])
    for name, (c0, a0, _) in TOOLS.items():
        if (c0, a0) == (cat, anim):
            return tool(name)
    c = clip(cat, anim)
    if not c:
        return None
    g = c["gates"]
    away = [-z for _, z in m["path"]]
    return {"name": m["anim"], "anim": m["anim"], "direction": "away", "iframes": m["iframes"], "away": away,
            "end": round(away[-1], 2), "frames": len(away) - 1, "r1": g["r1"], "roll": g["roll"],
            "skill": g["skill_late"], "skill_0": g["skill"], "skill_8": g["skill_q"], "item": g["item"],
            "move": g["move"], "chain": g[CHAIN_ACTION.get(family, "skill_late")], "hidden": hidden_span(c)}


def at(tl: dict, frame: float) -> float:
    a = tl["away"]
    return a[min(max(int(math.floor(frame)), 0), len(a) - 1)]


def sequences(tl: dict, chains: int = 1) -> list:
    """[(label, away(frame) path as a list, frame the drink can start)]: the tool alone and the
    tool chained into itself up to `chains` more times at its `chain` frame (`INFERRED`: pressed
    on the first frame it opens)."""
    out = []
    if tl["item"] is None:
        return out
    path = list(tl["away"])
    out.append(("x1", path[:], tl["item"]))
    if tl["chain"] is None:
        return out
    done, k = path[:], 1
    for n in range(chains):
        cut = int(math.floor(tl["chain"])) + (len(done) - len(tl["away"]))
        base = done[cut]
        done = done[:cut] + [base + x for x in tl["away"]]
        k += 1
        out.append((f"x{k}", done[:], cut + tl["item"]))
    return out


# --------------------------------------------------------------------------------------------
# staggers and blocks

#: Guard reactions (`c0000.behbnd`, W_GuardDamageSmall / Middle / Large, W_GuardBreak), the first
#: clip of each.
GUARD_CLIPS = {"guard small": 19200, "guard middle": 19210, "guard large": 19220, "guard break": 19500}


def damage_gates(levels=(8, 1, 2, 3, 5)) -> list:
    """Per stagger level (`er-mechanics-frame-advantage.LEVELS`, first clip of each): the roll's
    frame per DamageCount (TAE window and EzState flag), and the skill's and R1's. Then the guard
    reactions, where no DamageCount gate applies (`INFERRED`: `UseChainRecover` is a damage
    variable)."""
    fa = _mod("er-mechanics-frame-advantage")
    out = []
    for lv in levels:
        name, _, clips, counts = fa.LEVELS[lv]
        c = clip(0, clips[0])
        if not c:
            continue
        g = c["gates"]
        roll = {n: (max(g["roll"], c["flags"][f]) if g["roll"] is not None and f in c["flags"] else None)
                for n, f in fa.CHAIN_RECOVER_FLAG.items()} if counts else {n: g["roll"] for n in fa.CHAIN_RECOVER_FLAG}
        out.append({"level": lv, "name": name, "anim": c["anim"], "roll": roll, "skill": g["skill_late"],
                    "skill_0": g["skill"], "skill_8": g["skill_q"], "r1": g["r1"], "item": g["item"]})
    for name, anim in GUARD_CLIPS.items():
        c = clip(0, anim)
        if c:
            g = c["gates"]
            out.append({"level": None, "name": name, "anim": c["anim"], "roll": {1: g["roll"]},
                        "skill": g["skill_late"], "skill_0": g["skill"], "skill_8": g["skill_q"],
                        "r1": g["r1"], "item": g["item"], "guard": g["guard"]})
    return out


# --------------------------------------------------------------------------------------------
# the flask

FLASK_CLIP = 50000
#: EquipParamGoods: Flask of Crimson Tears +n full = 1001 + 2n (the odd rows carry `refId_default`;
#: the even ones are the empty flasks, `VERIFIED` regulation).
FLASK_GOODS_BASE = 1001
FLASK_LEVEL_DEFAULT = 12


def flask(level: int = FLASK_LEVEL_DEFAULT) -> dict:
    """Crimson Tears: `heal` (-SpEffect `changeHpEstusFlaskPoint` of the goods' `refId_default`,
    `VERIFIED` regulation), `consume` (the TAE 65 frame, `COMMUNITY` name), `free` (the first frame
    the drink can be left by any action: roll, R1, item, move) and the clip length."""
    pr = _atk().PR
    files = pr.load(None)
    goods, _, _ = pr.rows(pr.param_bytes(files, "EquipParamGoods"), ["refId_default", "goodsUseAnim"])
    g = {r["id"]: r for r in goods}[FLASK_GOODS_BASE + 2 * level]
    sp, _, _ = pr.rows(pr.param_bytes(files, "SpEffectParam"), ["changeHpEstusFlaskPoint"])
    heal = -{r["id"]: r for r in sp}[g["refId_default"]]["changeHpEstusFlaskPoint"]
    c = clip(0, FLASK_CLIP)
    gg = c["gates"]
    free = min(v for v in (gg["roll"], gg["r1"], gg["item"], gg["move"]) if v is not None)
    return {"level": level, "goods": FLASK_GOODS_BASE + 2 * level, "speffect": g["refId_default"],
            "use_anim": g["goodsUseAnim"], "heal": heal, "consume": c["consume"], "free": free,
            "roll": gg["roll"], "length": c["length"]}


# --------------------------------------------------------------------------------------------
# the chaser

#: How fast the chaser follows (`INFERRED`: he drops his lock-on and sprints; neutral.md section 1
#: measures the sprint 6.04 m/s and the locked-on run 4.01 m/s).
CHASER_SPEED = "sprint"
#: A chaser the ranking has no row for: the running R1, pool-weighted over the RL 150 ranking's
#: matched opponents (`MEASURED` 2026-09-30 from rank-neutral-int.json with `chaser_options`:
#: reach 5.98 m, first hit f16.9, 410 HP).
CHASER_FALLBACK = [{"kind": "run_r1", "reach": 5.98, "hit": 16.9, "hp": 410.0}]


def chaser_options(slots: dict) -> list:
    """[{'kind', 'reach' (m, from where the attack starts), 'hit' (first hit frame from its input),
    'hp'}]: what a chaser who is already running can land. The running R1 (a sprint plays the
    running attacks, `COMMUNITY` `ExecAttack`), the running and sprinting jump R1 / R2
    (`er-mechanics-jump.sequence`, travel included), and a plain R1 #1 for when he is already in
    range."""
    jump = _mod("er-mechanics-jump")
    out = []
    for key in ("run_r1", "r1_1"):
        s = slots.get(key)
        if s and s.get("reach") and s.get("startup") is not None:
            out.append({"kind": key, "reach": float(s["reach"]), "hit": float(s["startup"]),
                        "hp": float(s.get("dmg") or 0.0)})
    for key in ("jump_r1", "jump_r2"):
        s = slots.get(key)
        if not s:
            continue
        for kind in ("f", "d"):
            try:
                q = jump.sequence(s, kind)
            except Exception:  # a clip the jump reader cannot resolve: leave that option out
                q = None
            if q and q.get("reach"):
                out.append({"kind": f"{key}_{kind}", "reach": float(q["reach"]), "hit": float(q["first_hit"]),
                            "hp": float(s.get("dmg") or 0.0)})
    return out


def speed(kind: str = CHASER_SPEED) -> float:
    return _mod("er-mechanics-neutral").locomotion()[kind]


def time_to_hit(sep, options, v) -> tuple:
    """(frames from now to the chaser's first landed hit, that hit's hp) for separations `sep`
    (array): the option that lands first. He runs `sep - reach` at `v`, then attacks."""
    sep = np.asarray(sep, float)
    best_t = np.full(sep.shape, np.inf)
    best_hp = np.zeros(sep.shape)
    for o in options:
        t = np.maximum(0.0, sep - o["reach"]) / v * TAE_FPS + o["hit"]
        better = t < best_t
        best_t = np.where(better, t, best_t)
        best_hp = np.where(better, o["hp"], best_hp)
    return best_t, best_hp


def heal_outcome(sep, options, v, fl: dict) -> dict:
    """The heal drunk at separation `sep` against a chaser already at speed: net HP per attempt,
    and whether it was safe / traded / denied. He drinks only when the net is above 0."""
    t, hp = time_to_hit(sep, options, v)
    safe = t >= fl["free"]
    trade = (t >= fl["consume"]) & ~safe
    net = np.where(safe, fl["heal"], np.where(trade, fl["heal"] - hp, -hp))
    return {"t": t, "hp": hp, "safe": safe, "trade": trade, "net": np.maximum(net, 0.0), "raw": net}


def safe_distance(options, v, fl: dict, until: float) -> float:
    """The smallest separation at which no option lands before frame `until` of the drink."""
    return max(o["reach"] + v * max(0.0, until - o["hit"]) / TAE_FPS for o in options)


def race(tl: dict, delay: float, v: float, d0: float = 2.5, chains: int = 1, hidden: bool = False) -> list:
    """Per sequence of `tl`: separation when the drink starts, the chaser leaving `delay` real
    frames after the first dodge started (plus the frames the tool hides the escaper when
    `hidden`, `INFERRED`)."""
    extra = tl["hidden"][1] if hidden and tl.get("hidden") else 0.0
    out = []
    for label, path, drink in sequences(tl, chains):
        start = delay + extra
        sep = d0 + path[min(int(math.floor(drink)), len(path) - 1)] - v * max(0.0, drink - start) / TAE_FPS
        out.append({"seq": label, "drink": drink, "away": round(path[min(int(drink), len(path) - 1)], 2),
                    "sep": round(sep, 2)})
    return out


# --------------------------------------------------------------------------------------------
# the scored term

#: Share of the HP a player takes per engagement that he will want back from a flask (`INFERRED`:
#: the fight's engagements land about one hit each, er-mechanics-status, split between the two
#: players; every point of it is worth healing while flasks last).
HEAL_NEED_SHARE = 0.5
#: Most dodges the escaper chains onto the first before he drinks: three in a row (`INFERRED`).
#: FP is not charged here; Bloodhound's Step costs 5 per use (SwordArtsParam 801
#: `useMagicPoint_L2`, `VERIFIED`), so three are 15 of the corpus bar's 88.
MAX_CHAINS = 2


def escape_value(opp, tl: dict, v: float, fl: dict, chains: int = MAX_CHAINS, hidden: bool = False,
                 d0: float = 2.5) -> dict:
    """Expected heal HP per string after answering an opponent's string with `tl`, away from him.
    Per `er-mechanics-ashes.Opponents` row and reaction-timed press (its `dodge_presses`, the rule
    `Opponents._table` uses): the string must be evaded (every live frame inside the i-frames or
    out of reach, `Opponents._escapes`, both hits); the chaser leaves at the later of his `end`
    and the press plus his median reaction (`reaction_delays`, plus the hidden frames when
    `hidden`); the escaper drinks after the tool, or after chaining it, whichever nets more. The
    chaser's options are the row's `chase` (`chaser_options`), else `CHASER_FALLBACK`."""
    ash = _ash()
    delays = ash.reaction_delays()
    react = float(np.median([d for d, _ in delays]))
    extra = tl["hidden"][1] if hidden and tl.get("hidden") else 0.0
    dist = np.array([d0 + a for a in tl["away"]])
    seqs = sequences(tl, chains)
    total = evaded = safe = trade = 0.0
    seps = []
    for i in range(len(opp.w)):
        grid = np.arange(0.0, opp.startup[i] + opp.active[i] + 1.0, ash.DODGE_STEP)
        ok = opp._escapes(dist, tl["iframes"], opp.startup[i], opp.active[i], opp.reach[i], grid)
        presses = ash.dodge_presses(ok, opp.cue[i])
        s = np.array([p for p, _ in presses])
        w = np.array([q for _, q in presses]) * opp.w[i]
        first = opp._escapes(dist, tl["iframes"], opp.startup[i], opp.active[i], opp.reach[i], s)
        second = opp._escapes(dist, tl["iframes"], opp.startup[i] + opp.gap2[i], opp.active2[i], opp.reach2[i], s) \
            if opp.follow[i] else np.ones(len(s), bool)
        ev = first & second
        opts = (opp.rows[i].get("chase") if hasattr(opp, "rows") else None) or CHASER_FALLBACK
        leave = np.maximum(opp.end[i], s + react + extra)
        best = np.zeros(len(s))
        bsafe = np.zeros(len(s), bool)
        btrade = np.zeros(len(s), bool)
        bsep = np.full(len(s), -np.inf)
        for _, path, drink in seqs:
            a = path[min(int(math.floor(drink)), len(path) - 1)]
            sep = d0 + a - opp.advance[i] - v * np.maximum(0.0, s + drink - leave) / TAE_FPS
            h = heal_outcome(np.maximum(sep, 0.0), opts, v, fl)
            better = h["net"] > best
            best = np.where(better, h["net"], best)
            bsafe = np.where(better, h["safe"], bsafe)
            btrade = np.where(better, h["trade"], btrade)
            bsep = np.maximum(bsep, sep)
        total += float((w * ev * best).sum())
        evaded += float((w * ev).sum())
        safe += float((w * ev * bsafe).sum())
        trade += float((w * ev * btrade).sum())
        seps.append(float((w * ev * bsep).sum()))
    return {"heal_hp": total, "p_evade": evaded, "p_safe": safe, "p_trade": trade,
            "mean_sep": sum(seps) / evaded if evaded else None}


def skill_vs_roll(opp, moves: list, v: float | None = None, fl: dict | None = None, hidden: bool = False,
                  base: str = "roll medium") -> dict:
    """The disengage term of a dodge skill (`er-mechanics-ashes.utility_value` under `DISENGAGE`):
    heal HP per string with the skill's forward animation turned away (`moves`, the skill's own
    `evasion_motion`s) against the same with the medium roll, times the heal attempts per string
    (`HEAL_NEED_SHARE` x the pool's mean landed hit / the flask's heal). Cached per skill on `opp`."""
    v = speed() if v is None else v
    fl = fl or flask()
    fwd = min(moves, key=lambda m: m["path"][-1][1])
    key = ("disengage", fwd["anim"], v, hidden, base)
    cache = opp.__dict__.setdefault("_disengage", {})
    if key in cache:
        return cache[key]
    mine = tool_from_motion(fwd, "step")
    roll = tool(base)
    a = escape_value(opp, mine, v, fl, hidden=hidden)
    b = escape_value(opp, roll, v, fl)
    attempts = HEAL_NEED_SHARE * opp.mean_hp_landed / fl["heal"]
    out = {"hp": attempts * (a["heal_hp"] - b["heal_hp"]), "attempts": attempts, "skill": a, "roll": b,
           "tool": mine["anim"], "base": roll["anim"], "speed": v, "heal": fl["heal"]}
    cache[key] = out
    return out


# --------------------------------------------------------------------------------------------
# combo.md's cross-hand links, re-read with the skill's gate


def cross_hand_escapes() -> dict:
    """Every right-hand x off-hand link `er-mechanics-combo --sweep` counts, re-read with the skill's
    gate out of the stagger (DamageCount 1): a link that is true or a tie against the roll and
    whose gap is at or after the skill's gate is one a step escapes and a roll cannot. A step
    pressed at the gate has i-frames f0-10, and no roll gate is more than 5 frames after the
    skill's, so a link in that window lands inside them."""
    combo = _mod("er-mechanics-combo")
    gates = {g["level"]: g["skill"] for g in damage_gates() if g["level"] is not None}
    model = combo.Model(mirror=None)
    reg = model.reg
    weapons = combo.base_weapons(reg)
    offhand = [w for w in weapons if reg.weapon[w]["wepmotionCategory"] not in combo.LEFT_NO_ATTACK
               and reg.weapon[w]["wepmotionCategory"] not in combo.psg().GUARD_LEFT_ONE_HAND
               and model.offhand(w).get("left_1")]
    n = {"links": 0, "true": 0, "tie": 0, "true_skill_escapes": 0, "tie_skill_escapes": 0}
    by_level = {}
    for right in weapons:
        if not model.rows(right):
            continue
        for left in offhand:
            if combo.left_mode(reg, right, left) != "offhand":
                continue
            for lk in model.cross_links(right, left):
                n["links"] += 1
                b = lk["on_break"]
                if b["verdict"] not in ("true", "tie"):
                    continue
                n[b["verdict"]] += 1
                g = gates.get(b.get("level"))
                if g is not None and lk["gap"] >= g:
                    n[f"{b['verdict']}_skill_escapes"] += 1
                    by_level[b["level"]] = by_level.get(b["level"], 0) + 1
    n["by_level"] = by_level
    return n


#: Landing chance of a link the roll cannot leave (`er-mechanics-combo.VERDICT_P`): a tie lands
#: half the time.
ROLL_LAND = {"true": 1.0, "tie": 0.5}


#: `GetDualAttackMaxNumber` (`COMMUNITY` `c0000.hks`): how many powerstance L1s chain before the
#: next L1 starts the string again, by the right hand's weapon category (`LUA/Enums.txt` values;
#: that the category is `wepmotionCategory` is `INFERRED`, as in er-mechanics-powerstance-guard).
#: Any other category: 1.
DUAL_MAX = {**dict.fromkeys((26, 31, 37, 25, 32, 35, 36, 38, 40, 50, 43), 3),
            **dict.fromkeys((23, 24, 29, 30, 33, 34, 39, 20, 27, 28, 56, 57), 4),
            **dict.fromkeys((22, 42, 53, 55, 58, 59), 6)}
#: Powerstance openers out of movement (`AttackDual{Dash,Rolling,Stealth,BackStep}_onUpdate`):
#: their next L1 is `W_AttackLeftLight2` -> `W_AttackDualLight2` (`COMMUNITY`).
DUAL_MOVEMENT = ("dual_dash", "dual_roll", "dual_crouch", "dual_bstep")
#: L1-opened strings the pool's opponent rows can throw (`paired_rows`).
L1_OPENERS = ("left_1", "dual_1")


class Paired:
    """The pool's left hands as attackers: the off-hand L1 (`er-mechanics-combo`) and the
    powerstance L1 (`er-mechanics-powerstance-guard.powerstance_rows`), each clip as a list of
    hits in real frames with the menu poise and the HP of each.

    What `ExecAttack` plays on L1 with a pair in hand (`COMMUNITY` `c0000.hks`): every right-hand
    attack's onUpdate passes `W_AttackLeftLight1`, which `ATTACK_REQUEST_DUAL_RIGHT` turns into
    `W_AttackDualLight1`, so a right opener chains into powerstance L1 #1 at the same L1 window as
    the off-hand L1 (both are ChrActionType 2). `AttackDualLight<n>_onUpdate` sends L1 to
    `W_AttackDualLight<n+1>` up to `DUAL_MAX`, and the movement openers to L1 #2.

    The stagger gate only favours the skill at DamageCount 1: at 2 and above the roll comes at or
    before the skill on every level (`gates`). So a hit only counts as an escape point when it is
    the one hit of the string so far that put the defender into a stagger. Per corpus poise value
    (the pool's own `poise`), hits accumulate poise damage and a break resets it (`INFERRED`), and
    a link is scored from the breaking hit when exactly one hit broke."""

    def __init__(self, raw: dict, results: list, plain, model=None):
        combo, ex = _mod("er-mechanics-combo"), _mod("er-mechanics-exchange")
        self.combo, self.atk = combo, combo.ATK
        self.model = model or combo.Model(mirror=None)
        self.reg = self.model.reg
        self.ids = ex.weapon_ids()
        self.names = {v: k for k, v in self.ids.items()}
        self.rows = {f"{plain(r['weapon'])}|{'2h' if r['two'] else '1h'}": r for r in results}
        self.poise = np.asarray(raw.get("poise") or [], float)
        self.levels = {g["level"]: g for g in damage_gates() if g["level"] is not None}
        self._clips = {}

    def r1(self, name: str) -> dict:
        return ((self.rows.get(f"{name}|1h") or {}).get("slots") or {}).get("r1_1") or {}

    def hp(self, name: str, mv: float):
        """A hit's HP: that weapon's one-handed R1 #1 in the ranking scaled by the motion values
        (`INFERRED`: same AR in either hand and in powerstance)."""
        s = self.r1(name)
        return s["dmg"] * mv / s["mv"] if s.get("dmg") and s.get("mv") else None

    def clips(self, rid: int, lid: int) -> dict:
        """{slot: {'hits': [{start, end, atk_row, poise_damage, poise, hp}], 'l1': real frame the
        next L1 starts, 'cancel': real cancel frames}} of the L1 clips of this pair: off-hand
        `left_<n>` or powerstance `dual_*`, by `er-mechanics-combo.left_mode`."""
        key = (rid, lid)
        if key in self._clips:
            return self._clips[key]
        m, out = self.model, {}
        mode = self.combo.left_mode(self.reg, rid, lid)
        if mode == "offhand":
            name = self.names.get(lid)
            for slot, row in m.offhand(lid).items():
                hp = self.hp(name, row["mv_phys"])
                if hp is None:
                    continue
                hits = [{"start": s, "end": e, "atk_row": row["atk_row"], "poise_damage": row["poise_damage"],
                         "poise": m.pvp_poise(row), "hp": hp} for s, e in row["hit_windows"]]
                out[slot] = {"hits": hits, "l1": row.get("l1_start"), "cancel": row.get("cancel_frame") or {}}
        elif mode == "dual":
            w = self.reg.weapon[rid]
            cat = w["wepmotionCategory"]
            for row in self.combo.psg().powerstance_rows(self.reg, rid, lid):
                anim = int(row["anim"].split("_")[1])
                _, _, events = self.atk.resolve_events(cat, anim)
                if not events:
                    continue
                to_real = self.atk.clip_to_real(events)

                def real(f, to_real=to_real):
                    return self.atk.real_frame(to_real(f / self.atk.TAE_FPS))

                hits = []
                for h in row["hits"]:
                    hp = self.hp(self.names.get(h["weapon"]), h["mv_phys"])
                    if hp is None:
                        hits = []
                        break
                    r = {"atk_row": h["atk_row"], "poise_damage": h["poise_damage"]}
                    hits.append({"start": real(h["frames"][0]), "end": real(h["frames"][1]), **r,
                                 "poise": m.pvp_poise(r), "hp": hp})
                if not hits:
                    continue
                hits.sort(key=lambda h: h["start"])
                rec = self.atk.recovery_details(w, anim, events, [(h["start"], h["end"]) for h in hits], cat, to_real)
                out[row["slot"]] = {"hits": hits, "l1": self.combo.l1_start(w, cat, anim)[0],
                                    "cancel": rec["cancel_frame"]}
        self._clips[key] = (mode, out)
        return self._clips[key]

    def next_l1(self, rid: int, mode: str, slot: str):
        """The L1 clip the HKS plays after `slot` (class docstring), or None."""
        if mode == "offhand":
            n = int(slot.split("_")[1])
            return f"left_{n + 1}"
        if slot in DUAL_MOVEMENT:
            return "dual_2"
        if slot.startswith("dual_") and slot[5:].isdigit():
            n = int(slot[5:])
            return f"dual_{n + 1}" if n < DUAL_MAX.get(self.reg.weapon[rid]["wepmotionCategory"], 1) else None
        return None

    def avoided(self, nxt: dict) -> float:
        """HP of a follow-up clip the defender takes when the roll cannot leave before it: its
        first hit, and every later hit inside the roll's DamageCount 2 gate of the first hit's
        reaction (`INFERRED`: the first hit staggers him again)."""
        h0 = nxt["hits"][0]
        level = self.combo.FA.reaction_level(self.model.fa, h0["atk_row"], True)
        g = (self.levels.get(level) or {}).get("roll", {}).get(2)
        return sum(h["hp"] for h in nxt["hits"] if g is None or h["start"] - h0["start"] < g)

    def link_escape(self, first: dict, start, nxt: dict):
        """(escape weight, link) of one breaking hit `first` into the clip `nxt` started at real
        frame `start`: the roll's landing chance when the gap is at or after the skill's gate."""
        a = {"slot": "x", "hit_windows": [(first["start"], first["end"])], "atk_row": first["atk_row"],
             "poise_damage": first["poise_damage"]}
        b = {"slot": "y", "hit_windows": [(nxt["hits"][0]["start"], nxt["hits"][0]["end"])]}
        lk = self.model.link(a, b, start)
        if not lk:
            return 0.0, None
        br = lk["on_break"]
        g = (self.levels.get(br.get("level")) or {}).get("skill")
        if br.get("verdict") not in ROLL_LAND or g is None or lk["gap"] < g:
            return 0.0, lk
        return ROLL_LAND[br["verdict"]], lk

    def string_escape(self, clip: dict, nxt: dict) -> float:
        """Share of strings opened by `clip` (no hit before it) in which a skill leaves the stagger
        before `nxt` and the roll does not (class docstring), over the corpus poise values."""
        if not len(self.poise) or clip.get("l1") is None:
            return 0.0
        hits = clip["hits"]
        acc = np.zeros(len(self.poise))
        breaks = np.zeros(len(self.poise), int)
        broke_at = np.full(len(self.poise), -1)
        for i, h in enumerate(hits):
            acc += h["poise"]
            hit = acc >= self.poise
            broke_at = np.where(hit & (breaks == 0), i, broke_at)
            breaks += hit
            acc = np.where(hit, 0.0, acc)
        share = 0.0
        for i, h in enumerate(hits):
            sel = (breaks == 1) & (broke_at == i)
            if sel.any():
                p, _ = self.link_escape(h, clip["l1"], nxt)
                share += p * float(sel.mean())
        return share


def offhand_escapes(raw: dict, results: list, plain, model=None, detail: dict | None = None,
                    paired: Paired | None = None) -> dict:
    """{'<weapon>|1h': {opener slot: HP}}: per string an opponent opens with that opener, the
    left hand's next L1 damage a dodge skill out of the stagger avoids and a medium roll does not,
    over the left hands the corpus carries with that right hand (`opponent_pool` 'offhand').

    Right-hand openers, per left weapon: the opener's stagger share (the ranking slot's `stagger`)
    x the roll's landing chance of the link (`ROLL_LAND`) x the L1's damage, when the link's gap
    is at or after the skill's gate for its reaction level (`cross_hand_escapes`). The L1 is the
    off-hand L1 #1 (its damage: that weapon's own one-handed R1 #1 in the ranking scaled by the two
    motion values, `INFERRED`: same AR in either hand) or, with a pair in hand, powerstance L1 #1
    (`Paired`, every hit inside the roll's second gate). The L1-opened strings (`L1_OPENERS`,
    `left_1` -> `left_2`, `dual_1` -> `dual_2`, scored from the one breaking hit per `Paired`)
    are under their own opener keys, read only when the pool throws them (`paired_rows`).
    `detail`, when given, collects {(key, opener): {kind: HP}}."""
    paired = paired or Paired(raw, results, plain, model)
    model, reg, ids = paired.model, paired.reg, paired.ids
    gates = {lv: g["skill"] for lv, g in paired.levels.items()}
    rows = paired.rows

    out = {}
    for key, lefts in (raw.get("offhand") or {}).items():
        name, grip = key.rsplit("|", 1)
        r, rid = rows.get(key), ids.get(name)
        if grip != "1h" or r is None or rid is None or not model.rows(rid):
            continue
        slots = r.get("slots") or {}
        total = sum(lefts.values())
        per = {}

        def add(opener, value, kind):
            per[opener] = per.get(opener, 0.0) + value
            if detail is not None:
                d = detail.setdefault((key, opener), {})
                d[kind] = d.get(kind, 0.0) + value

        for left, n in lefts.items():
            lid = ids.get(left)
            if lid is None:
                continue
            mode, clips = paired.clips(rid, lid)
            first = clips.get("left_1" if mode == "offhand" else "dual_1")
            if first is None:
                continue
            hp = paired.avoided(first) if mode == "dual" else first["hits"][0]["hp"]
            for opener in model.rows(rid):
                a = model.rows(rid)[opener]
                if opener not in paired.combo.OPENERS:
                    continue
                stag = (slots.get(opener) or {}).get("stagger")
                lk = model.link(a, {"slot": "y", "hit_windows": [(first["hits"][0]["start"], first["hits"][0]["end"])]},
                                a["l1_start"], 0.0, "l1 (16/117)")
                if not lk or not stag:
                    continue
                b = lk["on_break"]
                g = gates.get(b.get("level"))
                if b.get("verdict") not in ROLL_LAND or g is None or lk["gap"] < g:
                    continue
                add(opener, n / total * stag * ROLL_LAND[b["verdict"]] * hp, f"{mode} after a right opener")
            for opener in ([s for s in clips if s == "dual_1" or s in DUAL_MOVEMENT] if mode == "dual" else ["left_1"]):
                nxt = clips.get(paired.next_l1(rid, mode, opener) or "")
                if nxt is not None:
                    e = paired.string_escape(clips[opener], nxt)
                    if e:
                        add(opener, n / total * e * paired.avoided(nxt), f"{mode} L1-opened")
        if per:
            out[key] = per
    return out


def paired_rows(raw: dict, results: list, plain, model=None, paired: Paired | None = None) -> dict:
    """{'<weapon>|1h': [(share of that key's builds, `er-mechanics-ashes.Opponents` row)]}: the
    L1-opened strings (`L1_OPENERS`) of the pool's left hands, one row per left weapon that has
    one, for `er-mechanics-ashes.PAIRED_STRINGS`. The row: the clip's first hit as the opener, its
    second hit as the chained one (same clip, so no link to judge), reach and landing chance
    borrowed from the right hand's R1 #1 (powerstance) or the left weapon's own R1 #1 (off-hand)
    in the ranking (`INFERRED`), `end` the clip's first R1 or roll cancel, and the string's
    `skill_escape_hp` (`Paired.string_escape` into its next L1 x that clip's avoided HP)."""
    paired = paired or Paired(raw, results, plain, model)
    ids = paired.ids
    out = {}
    for key, lefts in (raw.get("offhand") or {}).items():
        name, grip = key.rsplit("|", 1)
        rid = ids.get(name)
        if grip != "1h" or rid is None or key not in paired.rows:
            continue
        total = sum(lefts.values())
        got = []
        for left, n in lefts.items():
            lid = ids.get(left)
            if lid is None:
                continue
            mode, clips = paired.clips(rid, lid)
            opener = "left_1" if mode == "offhand" else "dual_1"
            c = clips.get(opener)
            if c is None:
                continue
            s = paired.r1(name if mode == "dual" else left)
            reach = s.get("reach") or CHASER_FALLBACK[0]["reach"]
            h = c["hits"]
            ends = [v for v in (c["cancel"].get("r1"), c["cancel"].get("dodge")) if v]
            row = {"opener": opener, "cue": 0.0, "startup": h[0]["start"],
                   "active": max(1.0, h[0]["end"] - h[0]["start"]), "reach": reach, "hp": h[0]["hp"],
                   "advance": max(0.0, reach - (s.get("weapon_reach") or reach)),
                   "land": (s.get("react") or {}).get("land", 1.0),
                   "end": min(ends) if ends else h[-1]["end"] + 10.0,
                   "skill_escape_hp": 0.0}
            nxt = clips.get(paired.next_l1(rid, mode, opener) or "")
            if nxt is not None:
                row["skill_escape_hp"] = paired.string_escape(c, nxt) * paired.avoided(nxt)
            if len(h) > 1:
                row.update(gap2=h[1]["start"] - h[0]["start"], reach2=reach,
                           active2=max(1.0, h[1]["end"] - h[1]["start"]), hp2=sum(x["hp"] for x in h[1:]))
            got.append((n / total, row))
        if got:
            out[key] = got
    return out


# --------------------------------------------------------------------------------------------
# commands


def cmd_tools(_a) -> int:
    print("tool                  anim         i-fr  R1    roll  skill(0/8)   item  move  chain  "
          "at item  end   frames  hidden")
    for name in TOOLS:
        t = tool(name)
        if not t:
            print(f"{name:<22}(no clip)")
            continue
        item = at(t, t["item"]) if t["item"] is not None else float("nan")
        print(f"{name:<22}{t['anim']:<13}{t['iframes']:>5} {t['r1']!s:>5} {t['roll']!s:>5} "
              f"{t['skill_0']!s:>5}/{t['skill_8']!s:<6} {t['item']!s:>5} {t['move']!s:>5} {t['chain']!s:>5}  "
              f"{item:>6.2f} {t['end']:>5.2f} {t['frames']:>5}  {t['hidden']}")
    return 0


def cmd_gates(_a) -> int:
    print("reaction      anim          roll by DamageCount 1/2/3/4+   skill (0/8)   R1   item")
    for g in damage_gates():
        r = "/".join(str(g["roll"].get(k)) for k in sorted(g["roll"]))
        print(f"{g['name']:<14}{g['anim']:<14}{r:<30} {g['skill']!s:>5} ({g['skill_0']}/{g['skill_8']})"
              f"  {g['r1']!s:>5} {g['item']!s:>5}" + (f"  guard {g['guard']}" if "guard" in g else ""))
    return 0


def cmd_flask(a) -> int:
    print(json.dumps(flask(a.level), indent=1))
    return 0


def cmd_race(a) -> int:
    v = speed(a.chaser)
    fl = flask(a.level)
    print(f"chaser {a.chaser} {v} m/s, leaves {a.delay} frames after the dodge starts; start 2.5 m apart; "
          f"flask +{a.level}: heal {fl['heal']} at f{fl['consume']}, free at f{fl['free']}")
    for o in (CHASER_FALLBACK[0],):
        print(f"reference chaser {o}: drink denied under {safe_distance([o], v, fl, fl['consume']):.2f} m, "
              f"traded under {safe_distance([o], v, fl, fl['free']):.2f} m")
    for name in ("roll medium", "roll medium back", "backstep 027000", "step", "step back", "quickstep"):
        t = tool(name)
        for r in race(t, a.delay, v, chains=2, hidden=a.hidden):
            print(f"  {name:<18}{r['seq']}  drink at f{r['drink']:>5}  away {r['away']:>5.2f} m  separation {r['sep']:>6.2f} m")
    return 0


def _opponents(path: Path, rl: int, window: int, paired_strings=None):
    ex, atk, ash = _mod("er-mechanics-exchange"), _atk(), _ash()
    raw = ex.opponent_pool(atk.Regulation(None), CACHE / "builds.jsonl", rl - window, rl + window)
    results = json.loads(path.read_text())["results"]
    ash.DISENGAGE = CHASER_SPEED
    ash.PAIRED_STRINGS = paired_strings
    opp, matched, _ = ash.opponents_from_results(raw, results)
    return opp, matched, raw, results


#: The dodge skill the pool command compares with the roll: Bloodhound's Step's four directions.
STEP_MOVES = (756, (40080, 40081, 40082, 40083))
#: A reference dodger for `Opponents.dodge_value`'s punish half (R1 strike frame, reach, HP).
REF_DODGER = (13.0, 2.6, 300.0)


def _step_vs_roll(opp) -> dict:
    """Bloodhound's Step against the medium roll on `opp`, as `er-mechanics-ashes.utility_value`
    scores a dodge skill (without the `p_second` factor): evade + punish, the disengage heal and
    the stagger escape."""
    ash = _ash()
    cat, anims = STEP_MOVES
    moves = [m for m in (ash.evasion_motion(cat, x) for x in anims) if m]
    step = opp.dodge_value(moves, ("skill", "pool"), *REF_DODGER)
    roll = opp.dodge_value(ash.roll_motion(), "roll", *REF_DODGER)
    dis = skill_vs_roll(opp, moves, v=speed(CHASER_SPEED))
    esc = float((opp.w * opp.land * opp.skill_escape_hp).sum())
    return {"evade": step["evade_hp"] - roll["evade_hp"], "punish": step["punish_hp"] - roll["punish_hp"],
            "disengage": dis["hp"], "stagger_escape": esc,
            "total": step["hp"] - roll["hp"] + dis["hp"] + esc, "mean_hp": opp.mean_hp}


def cmd_pool(a) -> int:
    opp, matched, raw, results = _opponents(a.opponents_from, a.rl, a.window)
    plain = _ash().plain
    paired = Paired(raw, results, plain)
    detail = {}
    offhand_escapes(raw, results, plain, detail=detail, paired=paired)
    held = {"offhand": 0.0, "dual": 0.0}
    builds = 0.0
    for key, lefts in (raw.get("offhand") or {}).items():
        rid = paired.ids.get(key.rsplit("|", 1)[0])
        builds += sum(lefts.values())
        for left, n in lefts.items():
            lid = paired.ids.get(left)
            if rid is not None and lid is not None and key.endswith("|1h"):
                mode = paired.combo.left_mode(paired.reg, rid, lid)
                if mode in held:
                    held[mode] += n
    print(f"left hands over {builds:.0f} pool builds: off-hand melee {held['offhand']:.1f}, "
          f"powerstance pair {held['dual']:.1f}")
    by_kind, rows_by_kind = {}, {}
    for wi, li, r in zip(opp.w, opp.land, opp.rows):
        for kind, hp in (detail.get((r.get("key"), r.get("opener"))) or {}).items():
            by_kind[kind] = by_kind.get(kind, 0.0) + float(wi * li) * hp
            rows_by_kind[kind] = rows_by_kind.get(kind, 0) + 1
    for kind, hp in sorted(by_kind.items()):
        print(f"  stagger escape per string, {kind:<28}{hp:.3f} HP over {rows_by_kind[kind]} rows")
    v = speed(a.chaser)
    fl = flask(a.level)
    attempts = HEAL_NEED_SHARE * opp.mean_hp_landed / fl["heal"]
    print(f"{len(opp.w)} opponent rows, {matched:.1%} of pool builds matched; chaser {a.chaser} {v} m/s; "
          f"heal attempts per string {attempts:.3f}")
    esc = opp.w * opp.land * opp.skill_escape_hp
    top = sorted(((float(e), r) for e, r in zip(esc, opp.rows) if e > 0), key=lambda x: -x[0])[:8]
    print(f"stagger escape: {float(esc.sum()):.1f} HP per string a dodge skill leaves before a cross-hand L1 "
          f"the roll does not; {int((esc > 0).sum())} of {len(esc)} rows carry one")
    for e, r in top:
        print(f"  {e:6.2f}  opener {r.get('opener')}  row {r.get('skill_escape_hp', 0):.1f} HP  w {r['w']:.1f}")
    base = escape_value(opp, tool("roll medium"), v, fl)
    for name in ("roll medium", "roll light", "roll heavy", "step", "step back", "quickstep"):
        t = tool(name)
        for hidden in ((False, True) if name.startswith("step") else (False,)):
            e = escape_value(opp, t, v, fl, hidden=hidden)
            print(f"  {name:<14}{' hidden' if hidden else '       '} evade {e['p_evade']:.3f}  safe {e['p_safe']:.3f}  "
                  f"trade {e['p_trade']:.3f}  mean sep {e['mean_sep'] or 0:.2f} m  heal/string "
                  f"{e['heal_hp']:.1f}  vs medium roll x attempts {attempts * (e['heal_hp'] - base['heal_hp']):+.1f} HP")
    base_sv = _step_vs_roll(opp)
    print("Bloodhound's Step - medium roll per string (reference dodger): "
          + "  ".join(f"{k} {v:+.2f}" for k, v in base_sv.items()))
    for p in a.paired_strings or ():
        o, *_ = _opponents(a.opponents_from, a.rl, a.window, p)
        sv = _step_vs_roll(o)
        l1 = [r for r in o.rows if r.get("opener") in L1_OPENERS]
        w = float(sum(wi for wi, r in zip(o.w, o.rows) if r.get("opener") in L1_OPENERS))
        e1 = float(sum(wi * li * r.get("skill_escape_hp", 0.0) for wi, li, r in zip(o.w, o.land, o.rows)
                       if r.get("opener") in L1_OPENERS))
        print(f"paired strings {p}: {len(l1)} L1-opened rows at weight {w:.3f}, their stagger escape {e1:.3f} HP; "
              + "  ".join(f"{k} {v:+.2f} ({v - base_sv[k]:+.2f})" for k, v in sv.items()))
    _ash().PAIRED_STRINGS = None
    return 0


def cmd_cross(_a) -> int:
    print(json.dumps(cross_hand_escapes(), indent=1))
    return 0


# --------------------------------------------------------------------------------------------
# self test


def selftest() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok   " if cond else "FAIL ") + msg)
        ok = ok and bool(cond)

    step, roll = tool("step"), tool("roll medium")
    check(step["iframes"] == 10 and roll["iframes"] == 13, f"i-frames: step f0-10, medium roll f0-13 ({step['iframes']}, {roll['iframes']})")
    check(step["r1"] == 17 and roll["r1"] == 20, f"R1: step f17, roll f20 ({step['r1']}, {roll['r1']})")
    check(step["skill_0"] == 21 and step["skill_8"] == 23 and step["chain"] == 23,
          f"the step chains into itself at f21 (cancel 16) or f23 (103/104); f23 used ({step['skill_0']}, {step['skill_8']})")
    check(roll["chain"] == 21 and roll["item"] == 21 and step["item"] == 27,
          f"roll into roll f21, flask after a roll f21, after a step f27 ({roll['chain']}, {roll['item']}, {step['item']})")
    check(abs(step["end"] - 5.24) < 0.02 and abs(roll["end"] - 3.65) < 0.02,
          f"forward travel 5.24 / 3.65 m ({step['end']}, {roll['end']})")
    check(step["hidden"] == (5.0, 10.0) and roll["hidden"] is None, f"the step is transparent f5-10, the roll never ({step['hidden']})")
    gates = {g["name"]: g for g in damage_gates()}
    mid, large, small = gates["middle"], gates["large"], gates["small"]
    check(mid["roll"][1] == 25 and mid["skill"] == 24 and large["roll"][1] == 35 and large["skill"] == 30
          and small["roll"][1] == 10 and small["skill"] == 7,
          f"DamageCount 1: skill out of small / middle / large at 7 / 24 / 30, roll at 10 / 25 / 35")
    check(mid["roll"][4] == 0 and mid["roll"][2] == 10, "the roll gate falls with consecutive staggers (middle: count 2 f10, 4+ f0)")
    check(gates["guard break"]["roll"][1] == 43 and gates["guard break"]["skill"] == 55,
          "out of a guard break the roll (f43) comes before the skill (f55)")
    fl = flask()
    check(fl["heal"] == 810 and fl["consume"] == 31 and fl["free"] == 54,
          f"Crimson Tears +12: 810 HP at f31, nothing cancels it before f54 ({fl})")
    seq = sequences(step, 1)
    check(len(seq) == 2 and seq[1][2] == 23 + 27 and abs(seq[1][1][-1] - (step["away"][23] + 5.24)) < 0.02,
          "a chained step drinks at 23 + 27 and ends 5.24 m past where the first was at f23")
    o = [{"kind": "x", "reach": 5.0, "hit": 17.0, "hp": 400.0}]
    h = heal_outcome(np.array([5.0, 8.0, 20.0]), o, 6.0, fl)
    check(list(h["safe"]) == [False, False, True] and list(h["trade"]) == [False, True, False]
          and h["net"][0] == 0 and h["net"][1] == 410 and h["net"][2] == 810,
          f"heal outcome at 5 / 8 / 20 m against a 5 m, f17 attack at 6 m/s: denied (no drink), traded, safe ({h['t']})")
    check(abs(safe_distance(o, 6.0, fl, fl["free"]) - (5.0 + 6.0 * 37 / 30)) < 1e-9, "safe distance = reach + speed x (54 - hit)")
    v = speed("sprint")
    r = {x["seq"]: x["sep"] for x in race(step, 10.5, v, chains=1)}
    rr = {x["seq"]: x["sep"] for x in race(roll, 10.5, v, chains=1)}
    check(r["x1"] > rr["x1"], f"against a sprinting chaser the step leaves more room than the roll ({r} vs {rr})")
    print("selftest", "passed" if ok else "FAILED")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", choices=("tools", "gates", "flask", "race", "pool", "cross-hand"))
    ap.add_argument("--level", type=int, default=FLASK_LEVEL_DEFAULT)
    ap.add_argument("--chaser", choices=("sprint", "run"), default=CHASER_SPEED)
    ap.add_argument("--delay", type=float, default=10.5, help="real frames before the chaser leaves")
    ap.add_argument("--hidden", action="store_true", help="the chaser reacts only once the step shows the escaper again")
    ap.add_argument("--opponents-from", type=Path)
    ap.add_argument("--rl", type=int, default=150)
    ap.add_argument("--window", type=int, default=10)
    ap.add_argument("--paired-strings", type=float, nargs="*",
                    help="pool: also score with this share of each attacking left hand's strings opened with L1")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    cmds = {"tools": cmd_tools, "gates": cmd_gates, "flask": cmd_flask, "race": cmd_race, "pool": cmd_pool,
            "cross-hand": cmd_cross}
    if a.cmd in cmds:
        if a.cmd == "pool" and not a.opponents_from:
            ap.error("pool needs --opponents-from")
        return cmds[a.cmd](a)
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
