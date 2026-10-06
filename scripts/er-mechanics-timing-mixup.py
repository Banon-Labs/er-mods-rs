#!/usr/bin/env python3
"""Timing mixups: openers the attacker can release from a held state, and what a defender can do.

    python3 scripts/er-mechanics-timing-mixup.py movement                 # crouch speeds, `MEASURED`
    python3 scripts/er-mechanics-timing-mixup.py weapon --weapon Giant-Crusher --grip both
    python3 scripts/er-mechanics-timing-mixup.py --selftest

Write-up: docs/er-mechanics/moveset.md section 9. Labels as in the sibling docs: `HKS` = the
installed compiled `c0000.hks` read with `scripts/er-hks-disasm.py`, `TAE`, `MEASURED` (hkx root
motion or computed here), `INFERRED` (a modelling choice).

1. The crouch is held (`HKS`). `Stealth_Idle_onUpdate` sets `STEALTH_IDLE` and calls only
   `IdleCommonFunction`, `ExecArtsStance` and `ExecGuard`: nothing in it ends the state on a timer.
   `Stealth_to_Stealth_Idle_onUpdate` hands an R1 to `StealthActionCommonFunction` as
   `W_AttackRightLightStealth` / `W_AttackBothLightStealth`, and `ExecAttack` (lines 1406-1408,
   1504-1506) rewrites it to the rolling R1 when `IsUseStealthAttack` is false; powerstanced, the
   same request becomes `W_AttackDualRolling` or `W_AttackDualStealth` (lines 1644-1648).
2. A held crouch moves at the stealth locomotion speed (`MEASURED`, `movement`): a000_320000
   1.37 m/s, 320100 2.98 m/s, 320200 4.41 m/s. `StealthTransitionIndexUpdate` follows
   `MoveSpeedIndex`, so a locked-on crouched player moves at index 1, 2.98 m/s (`INFERRED`, as the
   run is for standing movement in `er-mechanics-neutral`).
3. What the reaction model already does. `er-builds-pvp.Mechanics.slots` times every reaction
   dodge from the attack clip's own start (cue 0): an R2 from its release clip, a crouch, running,
   rolling or backstep attack from its attack clip. Only the jumps are read earlier, from the jump
   input. So a held start is already unreadable there, and so is a roll or backstep in front of an
   attack, although those clips are in plain view. They are not a fixed timing either: the roll's
   R1 input stays open from frame 20 to the clip's end (`entry_window`, `TAE`).
4. The term (`er-builds-pvp.py --timing-mixup`, `INFERRED`):
   - a crouch R1 is thrown from a held crouch: no entry frames at the engagement (the 8-frame
     standing crouch was spent before it), and in the neutral race the attacker closes any reach
     gap at the crouch speed with no dodge (a roll out of the crouch plays the rolling attack,
     which is `roll_r1`'s opener);
   - a rolling or backstep attack (`FIXED_ENTRY_CUE`): the waiting defender takes the better of
     reacting to the attack and anticipating it from the entry's start (`anticipation_evade`: one
     press at his best time, the attack's start uniform over the entry's R1 window).
   A defender who rolls early instead is not scored; `weapon` measures what it earns (item 5).
5. Pre-rolling against an unknown start (`preroll`). A roll pressed `s` frames before the attack
   starts either ends before the attack (he is back in neutral and reacts), escapes (the live
   frames fall in its i-frames or miss him), or is caught (a hit touches him after the i-frames:
   the roll-catch). With the start uniform over a hold of `W` frames, the pre-roll overlaps the
   attack with chance `dodge / W` (`dodge` = the roll's first dodge cancel), and otherwise he
   reacts as before.
"""

from __future__ import annotations

import argparse
import importlib.util
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
TAE_FPS = 30.0
_MODS: dict = {}


def _mod(name: str):
    if name not in _MODS:
        spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
        m = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = m
        spec.loader.exec_module(m)
        _MODS[name] = m
    return _MODS[name]


#: Stealth locomotion clips (a000): walk, move index 1, index 2, forward; `MEASURED` speeds in the
#: module docstring.
CROUCH_ANIMS = {"walk": 320000, "move": 320100, "fast": 320200}
#: The speed a locked-on crouched player closes at (`INFERRED`, docstring item 2).
CROUCH_APPROACH = "move"
#: Openers thrown from a held state (docstring item 4).
HELD = frozenset({"crouch_r1", "dual_crouch"})
#: Openers behind a fixed-length, visible entry: the defender's reaction runs from its start.
FIXED_ENTRY_CUE = frozenset({"roll_r1", "bstep_r1"})
#: Dodge starts are tried every half real frame (`er-mechanics-ashes.DODGE_STEP`).
STEP = 0.5
#: Hold windows `weapon` reports the pre-roll against (real frames).
HOLD_WINDOWS = (15.0, 30.0, 60.0)

_SPEED: dict = {}


def crouch_speeds() -> dict:
    """{name: metres a second} of `CROUCH_ANIMS`, root motion over the clip (`MEASURED`)."""
    if not _SPEED:
        pose = _mod("er-hkx-pose")
        for k, anim in CROUCH_ANIMS.items():
            clip = pose.load_animation(0, anim)
            x, _, z, _ = clip.root_motion(clip.duration)
            _SPEED[k] = round(math.hypot(x, z) / clip.duration, 3)
    return dict(_SPEED)


def crouch_k() -> float:
    """Real frames a held crouch takes to close one metre."""
    return TAE_FPS / crouch_speeds()[CROUCH_APPROACH]


def is_held(key: str) -> bool:
    return key.removeprefix("2h_") in HELD


def preroll(geoms, dist: float, moves=None) -> dict:
    """Pre-roll outcome of one attack at `dist` m: over roll starts in [-dodge, 0) real frames
    before the attack clip starts (each medium-roll direction alike), the shares caught and
    escaped. A roll started earlier is back in neutral when the attack starts."""
    ash = _mod("er-mechanics-ashes")
    moves = moves if moves is not None else ash.roll_motion()
    caught = total = 0.0
    lengths = []
    for m in moves:
        back = float(m["dodge"] if m.get("dodge") is not None else len(m["path"]))
        lengths.append(back)
        starts = np.arange(-back, 0.0, STEP)
        h, _ = ash._catches(geoms, dist, m, starts)
        caught += float(np.mean(h >= 0))
        total += 1.0
    return {"caught": caught / total, "escape": 1.0 - caught / total, "overlap": float(np.mean(lengths))}


#: The a000 clip in front of a fixed-entry opener: its R1 input window is how long the attacker may
#: wait after the entry's R1 frame (`entry_window`, `TAE`).
ENTRY_CLIPS = {"roll_r1": 27110, "bstep_r1": 27000}
_WINDOW: dict = {}


def entry_window(key: str) -> float | None:
    """Real frames the attacker may delay a fixed-entry opener past its first R1 frame: from the
    first frame the entry clip's R1 input and cancel windows overlap to the end of that overlap or
    of the clip, whichever is first (`TAE`: roll 027110 20 -> 50, backstep 027000 14 -> end)."""
    key = key.removeprefix("2h_")
    if key not in ENTRY_CLIPS:
        return None
    if key not in _WINDOW:
        atk, reach, pose = _mod("er-mechanics-attacks"), _mod("er-mechanics-reach"), _mod("er-hkx-pose")
        cat, src, events = reach.resolve_clip(0, ENTRY_CLIPS[key])
        to_real = atk.clip_to_real(events)
        w = atk.recovery_windows(events, 0.0)["r1"]
        spans = [(max(i[0], c[0]), min(i[1], c[1])) for i in w["input"] for c in w["cancel"]
                 if max(i[0], c[0]) < min(i[1], c[1])]
        clip = pose.load_animation(*reach.hkx_source(cat, src))
        lo, hi = min(spans)
        _WINDOW[key] = to_real(min(hi, clip.duration)) * TAE_FPS - to_real(lo) * TAE_FPS
    return _WINDOW[key]


def anticipation_evade(geoms, dist: float, entry: float, window: float, moves=None) -> float:
    """Evade chance of a defender who reacts to a fixed entry (its first frame is his cue) and
    presses one dodge at the time best for him, not knowing when in the `window` frames after the
    entry's R1 frame the attack starts (uniform). Each medium-roll direction alike, his reaction
    `er-mechanics-ashes.reaction_delays` from the entry's start, his press no earlier."""
    ash = _mod("er-mechanics-ashes")
    moves = moves if moves is not None else ash.roll_motion()
    delays = ash.reaction_delays()
    sd = ash.DODGE_TIMING_SD_S * TAE_FPS
    errors = [sd * ash._ND.inv_cdf((k + 0.5) / ash.TIMING_POINTS) for k in range(ash.TIMING_POINTS)]
    live = max(float(g["T"].max()) for g in geoms)
    lo = -(entry + window) - 5.0
    grid = np.arange(lo, live + 1.0, STEP)
    ds = np.arange(0.0, window + STEP / 2, STEP) if window > 0 else np.array([0.0])
    out = 0.0
    for m in moves:
        h, _ = ash._catches(geoms, dist, m, grid)
        ok = (h < 0).astype(float)
        # Press time p (frames from the entry's start); dodge start relative to the attack start
        # s = p - entry - d, averaged over d and his timing error.
        presses = np.arange(0.0, entry + window + live, STEP)
        s = presses[:, None, None] - entry - ds[None, :, None] + np.array(errors)[None, None, :]
        idx = np.clip(np.round((s - lo) / STEP).astype(int), 0, len(grid) - 1)
        p_esc = ok[idx].mean(axis=(1, 2))
        for delay, w in delays:
            best = p_esc[presses >= delay]
            out += w * (float(best.max()) if len(best) else 0.0) / len(moves)
    return out


def slot_reaction_mixup(contacts, distances, key: str, entry: float, my_roll=None, strikes=None,
                        fallback=None):
    """`er-mechanics-ashes.slot_reaction` of an opener under the term: for a fixed-entry opener the
    waiting defender takes the better of reacting to the attack (cue 0) and anticipating from the
    entry (`anticipation_evade`); his punish chance scales with the evade he gets. Other openers
    are `slot_reaction` unchanged."""
    ash = _mod("er-mechanics-ashes")
    window = entry_window(key)
    geoms = [g for g in (ash.contact_geometry(e) for e in (contacts or {}).values()) if g is not None]
    if window is None or not geoms:
        return ash.slot_reaction(contacts, distances, 0.0, my_roll, strikes, fallback=fallback)
    advance = max((float(g["PK"][np.isfinite(g["PK"])].max()) for g in geoms if np.isfinite(g["PK"]).any()),
                  default=0.0)
    outs = []
    for d in distances:
        o = ash.reaction_outcome(geoms, [1.0] * len(geoms), d, 0.0, my_roll=my_roll, advance=advance,
                                 strikes=strikes)
        ant = anticipation_evade(geoms, d, entry, window)
        if o["base"] > 0 and ant > o["p_evade"]:
            scale = ant / o["p_evade"] if o["p_evade"] > 0 else 0.0
            o = {**o, "value": o["base"] * (1.0 - ant), "p_evade": ant,
                 "p_punish": o["p_punish"] * scale, "punish_hp": o["punish_hp"] * scale, "anticipated": True}
        outs.append(o)
    out = ash.react_factors(outs)
    if out is not None:
        out["anticipated"] = sum(1 for o in outs if o.get("anticipated")) / len(outs)
    return out


def mixup_value(react_evade: float, pre: dict, hold: float) -> dict:
    """A defender who pre-rolls at a random moment of a `hold`-frame window: overlap chance
    `overlap / hold`, else he reacts. Returns his evade and caught chances beside the reactor's."""
    p = min(1.0, pre["overlap"] / hold)
    return {"hold": hold, "p_overlap": p, "evade": p * pre["escape"] + (1 - p) * react_evade,
            "caught_by_preroll": p * pre["caught"], "react_evade": react_evade}


def weapon_report(weapon: str, grip: str, slots=("r1_1", "r2_1c", "crouch_r1", "roll_r1", "bstep_r1",
                                                 "run_r1")) -> list[dict]:
    """Per opener of one weapon grip: first live frame, active frames, forward travel at the hit,
    the reactor's evade chance (cue 0, and from the entry for a fixed-entry opener), and the
    pre-roll outcome at 2.5 m."""
    pvp = _mod("er-builds-pvp")
    ash, reach, atk = _mod("er-mechanics-ashes"), _mod("er-mechanics-reach"), pvp.ATK
    tables, reg = pvp.AR.Tables(None), atk.Regulation(None)
    wid = tables.find_weapon(weapon, "Standard")
    attacks = {a["slot"].removeprefix("2h_"): a for a in atk.weapon_attacks(reg, wid, grip)}
    contacts = reach.slot_contacts(wid, grip)
    prefix = "2h_" if grip == "both" else ""
    out = []
    for key in slots:
        a = attacks.get(key)
        cs = contacts.get(prefix + key)
        if not a or not cs:
            continue
        geoms = [g for g in (ash.contact_geometry(e) for e in cs.values()) if g is not None]
        if not geoms:
            continue
        pk = [float(g["PK"][np.isfinite(g["PK"])].max()) for g in geoms if np.isfinite(g["PK"]).any()]
        first = min(float(g["T"].min()) for g in geoms)
        last = max(float(g["T"].max()) for g in geoms)
        entry = pvp.SCORE_ENTRY_FRAMES.get(key, 0.0)
        d = ash.ENGAGE_DISTANCE_M
        react0 = ash.reaction_outcome(geoms, [1.0] * len(geoms), d, 0.0)
        row = {"slot": key, "anim": a.get("anim"), "first_live": round(first, 1), "last_live": round(last, 1),
               "travel": round(max(pk), 2) if pk else None, "entry": entry,
               "react_evade": round(react0["p_evade"], 3)}
        if key in FIXED_ENTRY_CUE:
            row["window"] = round(entry_window(key), 1)
            row["anticipate_evade"] = round(anticipation_evade(geoms, d, entry, row["window"]), 3)
        pre = preroll(geoms, d)
        row["preroll"] = {k: round(v, 3) for k, v in pre.items()}
        row["hold"] = [{k: round(v, 3) for k, v in mixup_value(react0["p_evade"], pre, w).items()}
                       for w in HOLD_WINDOWS]
        out.append(row)
    return out


def selftest() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok   " if cond else "FAIL ") + msg)
        ok = ok and bool(cond)

    win = entry_window("2h_roll_r1")
    check(win is not None and 25.0 <= win <= 35.0 and entry_window("crouch_r1") is None,
          f"the rolling R1 can wait {win:.1f} frames past the roll's frame 20 (TAE)")
    n = 7
    geom = [{"T": np.arange(15.0, 15.0 + n * STEP, STEP)[:n], "X": np.zeros(n), "Z": np.full(n, -99.0),
             "R": np.full(n, 1e3), "PK": np.full(n, -math.inf)}]
    fixed, blurred = anticipation_evade(geom, 2.5, 20.0, 0.0), anticipation_evade(geom, 2.5, 20.0, 60.0)
    check(fixed > 0.9 and blurred < fixed,
          f"anticipating a fixed start escapes ({fixed:.2f}); a 60-frame delay window blurs it ({blurred:.2f})")
    check(is_held("2h_crouch_r1") and is_held("dual_crouch") and not is_held("roll_r1"), "held openers")
    v = mixup_value(0.4, {"caught": 0.5, "escape": 0.5, "overlap": 30.0}, 60.0)
    check(abs(v["evade"] - (0.5 * 0.5 + 0.5 * 0.4)) < 1e-12 and abs(v["caught_by_preroll"] - 0.25) < 1e-12,
          "a pre-roll overlaps overlap/hold of the time, else he reacts")
    sp = crouch_speeds()
    check(abs(sp["walk"] - 1.37) < 0.02 and abs(sp["move"] - 2.98) < 0.02,
          f"crouch speeds MEASURED walk {sp['walk']} move {sp['move']} m/s")
    print("selftest " + ("passed" if ok else "FAILED"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("movement")
    w = sub.add_parser("weapon")
    w.add_argument("--weapon", action="append", required=True)
    w.add_argument("--grip", choices=("one", "both"), default="both")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.cmd == "movement":
        sp = crouch_speeds()
        print("crouch (stealth) locomotion, a000 forward clips, root motion over the clip:")
        for k, anim in CROUCH_ANIMS.items():
            print(f"  {k:<6} a000_{anim:06d}  {sp[k]:.2f} m/s  ({TAE_FPS / sp[k]:.2f} real frames a metre)")
        return 0
    if a.cmd == "weapon":
        for name in a.weapon:
            print(f"\n{name} {'2H' if a.grip == 'both' else '1H'} (defender 2.5 m ahead; frames from the attack "
                  f"clip's start; pre-roll = roll pressed before it)")
            print(f"  {'slot':<10}{'anim':<14}{'live':>11}{'travel':>8}{'entry':>6}{'evade':>7}{'antic':>7}"
                  f"{'pre caught':>11}{'overlap':>8}  evade / caught-by-preroll at hold 15, 30, 60")
            for r in weapon_report(name, a.grip):
                hold = "  ".join(f"{h['evade']:.2f}/{h['caught_by_preroll']:.2f}" for h in r["hold"])
                print(f"  {r['slot']:<10}{str(r['anim']):<14}{r['first_live']:>5.1f}-{r['last_live']:<5.1f}"
                      f"{r['travel'] if r['travel'] is not None else '-':>8}{r['entry']:>6.0f}{r['react_evade']:>7.2f}"
                      f"{r.get('anticipate_evade', float('nan')):>7.2f}{r['preroll']['caught']:>11.2f}"
                      f"{r['preroll']['overlap']:>8.1f}  {hold}")
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
