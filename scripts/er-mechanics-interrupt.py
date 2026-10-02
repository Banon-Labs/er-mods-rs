#!/usr/bin/env python3
"""The PvP interrupt matrix: which attacks of a weapon poise-break an opponent mid-swing.

    python3 scripts/er-mechanics-interrupt.py --selftest
    python3 scripts/er-mechanics-interrupt.py --build-cache --jobs 14        # sequences, ~5 min
    python3 scripts/er-mechanics-interrupt.py --run --jobs 14 --out <file>   # the matrix, ~10 min
    python3 scripts/er-mechanics-interrupt.py --report <file> --top 20
    python3 scripts/er-mechanics-interrupt.py --weapon "Cleanrot Knight's Sword" --grip one

Write-up: `docs/er-mechanics/interrupt.md`. Labels as in the other mechanics files: `VERIFIED` =
regulation value or traced EXE code, `TAE` = decoded TimeAct, `MEASURED` = computed here from the
regulation and the corpus mirror, `INFERRED` = a modelling choice or an untraced consumer.

The question, per attack A of a weapon W and per attack B of the RL window's opponent pool: B has
started; A starts `o` frames later. Does A land, and break the defender's poise, before B's
hitbox reaches A? Every offset `o` from B's first frame up to its strike frame is tried (one frame
apart, uniform, `INFERRED`), and every hit A lands before B's strike counts toward the break:

* A's hits are its main-judge windows and extra hitboxes that open a fresh hit record
  (`er-mechanics-attacks` `sweep_hit`), and for the R1, R2 and powerstance L1 openers the whole
  chain, each next clip starting at the previous clip's cancel frame for the same button
  (`INFERRED`: the button is buffered). A hit lands at its front-contact frame 2.5 m ahead
  (`er-mechanics-reach` `front_contact_frame_real`, `er-mechanics-exchange.STRIKE_DISTANCE_M`,
  distance `INFERRED`); a later hit of the same clip keeps its own window's distance from the
  first (`INFERRED`).
* PvP poise damage in menu units: `poise_damage x 10 x FinalDamageRateParam.saRate`, the same
  expression as `er-mechanics-exchange` (units: exchange.md section 1, frame-advantage.md
  section 9 lists the open unit question).
* The defender's poise: its own armor poise (pool builds, `er-mechanics-exchange.opponent_pool`),
  or the pool's poise distribution when W is the defender.

The poise state follows `CSChrToughnessModule` (docs/er-mechanics/interrupt.md section 2):

* A hit subtracts `dealt x unk1 x damageRatio` (the window's rates while a TAE 795 window is open)
  from current poise; at or below 0 the defender is broken (`FUN_140486bf0`, `VERIFIED`).
* Opening or closing a window recomputes `max`, and current becomes
  `clamp(newMax - (oldMax - current), minToughness% x newMax, newMax)` (`FUN_140486e50`,
  `VERIFIED`), so damage taken before and inside a window carries through it, except what the
  window's floor heals.
* Nothing regenerates gradually. Every hit with a nonzero damage level re-arms a timer of
  `GameSystemCommonParam.baseToughnessRecoverTime x PlayerCommonParam.toughnessRecoverCorrection
  x prod(1 + armor toughnessRecoverCorrection)` seconds (30.0 x 1.0 x 1.0 in 1.17.1); when it runs
  out, poise refills to max at once. A break refills it two updates later. A guarded hit subtracts
  nothing (`info+0x258`). All `VERIFIED` in the 1.16.2 image, values from the regulation.

Two chip scenarios: `fresh` (full poise at B's first frame, the exchange module's assumption) and
`carried`: the defender is at a uniformly random point of the steady-state cycle an attacker who
keeps landing the same first hit `d` produces inside the 30 s reset: chip `j x d`,
`j = 0 .. ceil(P / d) - 1`, equally likely (`INFERRED`: every landed hit within 30 s of the last,
nothing else breaks or resets the defender in between).

Aggregates per weapon and grip (`MEASURED` over the model, every weight `INFERRED`):
`stop` = the pool-weighted share of (B, offset) pairs A stops; pool weight = build frequency x
attack family share (four families r1, r2, move, jump, equal; within a family equal; no match log
exists to weight them, moveset.md section 3). `stop_best` takes, per B and offset, the best of W's
attacks (the player picks the answer). `stopped` = the share of (B, offset) pairs, B started
during A's windup, in which B breaks A first, averaged over W's opener families.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CACHE = Path.home() / ".cache/er-build-planner"


def _sibling(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ATK = _sibling("er-mechanics-attacks")
EXCH = _sibling("er-mechanics-exchange")
PR = ATK.PR
POISE_MENU = EXCH.POISE_MENU
STRIKE_DISTANCE_M = EXCH.STRIKE_DISTANCE_M
FPS = 30.0

_MODS = {}


def _mod(name: str):
    if name not in _MODS:
        _MODS[name] = _sibling(name)
    return _MODS[name]


# --------------------------------------------------------------------------------------------
# game rules read from the data

#: SpEffect a player weapon attack puts on its user from its first hitbox frame (TAE 66, bd
#: `er-damage-type-252-253-and-pierce-counter-spEffect45-2026-09-29`).
COUNTER_SPEFFECT = 45
#: Standing medium roll and backstep clips in `a00.tae`; their unconditional JumpTable 8 window is
#: the entry's invincibility (`er-mechanics-ashes._jt8_unconditional` rule: Args+14 u16 == 0).
ROLL_ANIM, BACKSTEP_ANIM = 27110, 27000
JT_INVINCIBLE = 8
#: `er-builds-pvp.SCORE_ENTRY_FRAMES`, restated so this module does not import the ranking (the
#: selftest compares the two). Roll, backstep and crouch are `VERIFIED` TAE, the sprint `INFERRED`.
ENTRY_FRAMES = {"roll_r1": 20.0, "bstep_r1": 14.0, "crouch_r1": 8.0, "run_r1": 20.0, "run_r2": 20.0}


def poise_reset_seconds(armor_rates=(0.0, 0.0, 0.0, 0.0)) -> float:
    """Seconds after the last poise hit until poise refills to max, per the rule in
    `CSChrToughnessModule` vtable slot 8 (0x140487b00) and `FUN_140688e50` (`VERIFIED` 1.16.2):
    `baseToughnessRecoverTime x PlayerCommonParam.toughnessRecoverCorrection x prod(1 + armor
    toughnessRecoverCorrection)`. `armor_rates` are the four pieces' values (every
    EquipParamProtector row holds 0.0 in 1.17.1)."""
    files = PR.load(None)
    gs, _, _ = PR.rows(PR.param_bytes(files, "GameSystemCommonParam"), ["baseToughnessRecoverTime"])
    pc, _, _ = PR.rows(PR.param_bytes(files, "PlayerCommonParam"), ["toughnessRecoverCorrection"])
    prod = 1.0
    for r in armor_rates:
        prod *= 1.0 + r
    return gs[0]["baseToughnessRecoverTime"] * pc[0]["toughnessRecoverCorrection"] * prod


def armor_recover_rates() -> Counter:
    files = PR.load(None)
    rows, _, _ = PR.rows(PR.param_bytes(files, "EquipParamProtector"), ["toughnessRecoverCorrection"])
    return Counter(r["toughnessRecoverCorrection"] for r in rows)


def counter_poise_rate() -> dict:
    """SpEffect 45's fields that could touch poise. `toughnessDamageCutRate` is the SpEffect half
    of the defender's cut rate in `FUN_140486bf0` (toughness vtable slot 7, attacks.md section 2);
    the thrust rate only reaches HP damage."""
    files = PR.load(None)
    rows, _, _ = PR.rows(PR.param_bytes(files, "SpEffectParam"),
                         ["toughnessDamageCutRate", "thrustDamageCutRate", "changeSuperArmorPoint"])
    r = next(x for x in rows if x["id"] == COUNTER_SPEFFECT)
    return {k: r[k] for k in ("toughnessDamageCutRate", "thrustDamageCutRate", "changeSuperArmorPoint")}


def _a00():
    return _mod("er-mechanics-frame-advantage").common_tae()


def iframes(anim: int) -> float | None:
    """Frames from 0 the unconditional invincibility of an `a00` clip lasts (TAE)."""
    events = (_a00() or {}).get(anim)
    if events is None:
        return None
    ends = [e.end for e in events if e.type == 0 and struct.unpack_from("<i", e.params, 0)[0] == JT_INVINCIBLE
            and struct.unpack_from("<H", e.params, 14)[0] == 0 and e.start < 1e-6]
    return round(max(ends) * FPS, 1) if ends else None


def jump_entry() -> float | None:
    """Frames from the jump input to the earliest air-attack press, `er-mechanics-jump.takeoff`
    for the standing jump (SpEffect 140 and the attack cancel overlapping in the jump clip, TAE):
    the frame the jump attack's clip starts, the same entry `er-builds-pvp.SCORE_ENTRY_FRAMES`
    gives `jump_r1`/`jump_r2`. A press later in the air is not modelled (`INFERRED`: the
    earliest press)."""
    return _mod("er-mechanics-jump").takeoff("n")["press"]


# --------------------------------------------------------------------------------------------
# attack sequences

#: Opener sequences: (name, family, members, chain button, entry key). Members after the first
#: play when the previous clip's cancel frame for `button` opens. Guard counters stay out (they
#: need a blocked hit first).
OPENERS = [
    ("r1", "r1", ["r1_1", "r1_2", "r1_3", "r1_4", "r1_5", "r1_6"], "r1", None),
    ("r2", "r2", ["r2_1", "r2_2"], "r2", None),
    ("r2c", "r2", ["r2_1c"], None, None),
    ("run_r1", "move", ["run_r1"], None, "run_r1"),
    ("run_r2", "move", ["run_r2"], None, "run_r2"),
    ("roll_r1", "move", ["roll_r1"], None, "roll_r1"),
    ("bstep_r1", "move", ["bstep_r1"], None, "bstep_r1"),
    ("crouch_r1", "move", ["crouch_r1"], None, "crouch_r1"),
    ("jump_r1", "jump", ["jump_r1"], None, "jump"),
    ("jump_r2", "jump", ["jump_r2"], None, "jump"),
    ("dual", "l1", ["dual_1", "dual_2", "dual_3", "dual_4", "dual_5", "dual_6"], "l1", None),
    ("dual_dash", "l1", ["dual_dash"], None, "run_r1"),
    ("dual_roll", "l1", ["dual_roll"], None, "roll_r1"),
    ("dual_crouch", "l1", ["dual_crouch"], None, "crouch_r1"),
    ("dual_bstep", "l1", ["dual_bstep"], None, "bstep_r1"),
    ("skill", "skill", ["skill"], None, None),
]
OPENER = {o[0]: o for o in OPENERS}
#: Families the pool's attacks are drawn from, equal shares (`INFERRED`: no match log; the pool
#: records neither the build's ash nor a powerstance pair).
POOL_FAMILIES = ("r1", "r2", "move", "jump")
W_FAMILIES = ("r1", "r2", "move", "jump", "l1")

SEQ_VERSION = 3
SEQ_SOURCES = ("er-mechanics-attacks.py", "er-mechanics-reach.py",
               "er-mechanics-moveset.py", "er-mechanics-exchange.py", "er-mechanics-ashes.py")


def _source_stamp() -> str:
    h = hashlib.sha256(str(SEQ_VERSION).encode())
    for name in SEQ_SOURCES:
        h.update((HERE / name).read_bytes())
    return h.hexdigest()[:16]


def _window(reg, weapon, row_id, source, ratio, frames):
    """One TAE 795 window as (start, end, bonus menu, poise-taken multiplier, floor fraction):
    bonus `100 x correctionRate x weapon toughnessCorrectRate` when Args byte 1 is 1 or 2,
    multiplier ToughnessParam `unk1` x the event's damageRatio, floor `minToughness` / 100
    (attacks.md section 2, `VERIFIED` handler 0x14042c2e0 and update 0x140486e50)."""
    t = reg.toughness.get(row_id, {})
    bonus = (ATK.TOUGHNESS_SCALE * t.get("correctionRate", 0.0) * weapon["toughnessCorrectRate"]
             if source in (1, 2) else 0.0)
    floor = (t.get("minToughness", 0) or 0) / 100.0 if not t.get("isNonEffectiveCorrectionForMin") else 0.0
    return [frames[0], frames[1], bonus * POISE_MENU, (t.get("unk1") or 1.0) * (ratio or 1.0), floor]


def _slot_member(reg, sa, wid, atk, reach_row):
    """One `weapon_attacks` row as a raw member: clip-real frames, lead-in kept apart."""
    hits = []
    for d in atk.get("hit_window_detail") or []:
        if d.get("sweep_hit"):
            hits.append([d["frames"][0], atk["poise_damage"] * POISE_MENU, sa(atk["atk_row"])])
    for o in atk.get("other_hitboxes") or []:
        if not o.get("sweep_hit"):
            continue
        n = ATK.attack_numbers(reg, wid, o["judge"])
        if n:
            hits.append([o["frames"][0], n["poise_damage"] * POISE_MENU, sa(n["atk_row"])])
    if not hits:
        return None
    hits.sort()
    w = reg.weapon[wid]
    windows = []
    for h in atk.get("hyperarmor") or []:
        row = h["toughness_row"]
        t = reg.toughness.get(row, {})
        floor = (h.get("floor_pct_of_max") or 0) / 100.0 if not t.get("isNonEffectiveCorrectionForMin") else 0.0
        windows.append([h["frames"][0], h["frames"][1], h["poise_bonus"] * POISE_MENU,
                        (h.get("pvp_poise_damage_taken") or 1.0) * (h.get("poise_damage_taken_ratio") or 1.0),
                        floor])
    fc = (reach_row or {}).get("front_contact_frame_real") or {}
    fc = fc.get(STRIKE_DISTANCE_M, fc.get(str(STRIKE_DISTANCE_M)))
    first = EXCH.first_hit(atk)
    return {"first": first, "fc": fc, "hits": hits, "windows": windows,
            "cancel": dict(atk.get("cancel_frame") or {}), "lead": atk.get("release_lead_in") or 0.0,
            "dmg_level": atk.get("dmg_level"), "wep_type": w["wepType"]}


def _dual_members(reg, sa, wid):
    """Powerstance L1 members (`er-mechanics-moveset.dual_attacks`, same weapon in both hands),
    with their TAE 795 windows read from the resolved clip the same way as `tae_details`."""
    out = {}
    w = reg.weapon[wid]
    for row in _mod("er-mechanics-moveset").dual_attacks(reg, wid):
        hits = [[h["frames"][0], h["poise_damage"] * POISE_MENU, sa(h["atk_row"])] for h in row["hits"]]
        cat, anim = (int(x) for x in row["anim"][1:].split("_"))
        _, _, events = ATK.resolve_events(cat, anim)
        windows = []
        if events:
            to_real = ATK.clip_to_real(events)
            for e in events:
                if e.type == ATK.TAE_TOUGHNESS:
                    ratio = struct.unpack_from("<f", e.params, 4)[0]
                    fr = (ATK.real_frame(to_real(e.start)), ATK.real_frame(to_real(e.end)))
                    windows.append(_window(reg, w, e.params[0], e.params[1], ratio, fr))
        out[row["slot"]] = {"first": min(h[0] for h in hits), "fc": None, "hits": sorted(hits),
                            "windows": windows, "cancel": dict(row["cancel_frame"]), "lead": 0.0,
                            "wep_type": w["wepType"], "proxy": "r1_1"}
    return out


def _skill_member(ash, sa, wid):
    """The weapon's own skill (`EquipParamWeapon.swordArtsParamId`) when its opening animation
    carries melee hits itself. Frames are clip frames (TAE 608 play speed not applied, `INFERRED`
    close to real); stance skills whose hit is a follow-up animation and bullet-only skills are
    left out."""
    w = ash.reg.weapon[wid]
    aid = w.get("swordArtsParamId")
    if aid is None or aid < 0 or aid not in ash.arts:
        return None
    AS = _mod("er-mechanics-ashes")
    try:
        prof = AS.skill_profile(ash, aid, wid)
    except SystemExit:
        return None
    anim = AS.main_anim(prof)
    acts = prof["anims"].get(anim) or []
    hits = [[x["frames"][0], (x.get("poise") or 0.0) * POISE_MENU, sa(x["atk_row"])]
            for x in acts if x["kind"] == "melee"]
    if not hits:
        return None
    windows = []
    for x in acts:
        if x["kind"] == "hyperarmor" and x["frames"][1] is not None:
            t = ash.reg.toughness.get(x["toughness_row"], {})
            floor = (t.get("minToughness", 0) or 0) / 100.0 if not t.get("isNonEffectiveCorrectionForMin") else 0.0
            windows.append([x["frames"][0], x["frames"][1], x["poise_bonus_menu"],
                            x["pvp_poise_damage_taken"], floor])
    return {"first": min(h[0] for h in hits), "fc": None, "hits": sorted(hits), "windows": windows,
            "cancel": {}, "lead": 0.0, "wep_type": w["wepType"], "proxy": "r1_1",
            "skill": prof["name"], "skill_anim": f"{prof['tae']}_{anim:06d}"}


def weapon_members(reg, sa, ash, wid: int, grip: str) -> dict:
    """{member key (no `2h_` prefix): raw member} of one weapon and grip."""
    reach = _mod("er-mechanics-reach").reach_summary(wid, grip)
    out = {}
    for atk in ATK.weapon_attacks(reg, wid, grip):
        key = atk["slot"].removeprefix("2h_")
        if key == "counter":
            continue
        m = _slot_member(reg, sa, wid, atk, reach.get(atk["slot"]))
        if m:
            out[key] = m
    if grip == "one":
        psg = _mod("er-mechanics-powerstance-guard")
        if psg.can_powerstance(reg, wid, wid):
            out.update(_dual_members(reg, sa, wid))
    sk = _skill_member(ash, sa, wid)
    if sk:
        out["skill"] = sk
    return out


# --------------------------------------------------------------------------------------------
# the sequence cache

def target_weapons(pool_raw: dict, sweep: Path | None) -> list[tuple[str, str]]:
    """(regulation name, grip) of every sweep weapon (both grips) and every pool profile."""
    names = set()
    if sweep and sweep.exists():
        for line in sweep.open():
            names.add(EXCH.plain_name(json.loads(line)["weapon"]))
    out = {(n, g) for n in names for g in ("one", "both")}
    for key in pool_raw["profiles"]:
        n, g = key.split("|")
        out.add((n, "both" if g == "2h" else "one"))
    return sorted(out)


_W = {}


def _worker_init():
    _W["reg"] = ATK.Regulation(None)
    _W["sa"] = EXCH.SaRates()
    _W["ash"] = _mod("er-mechanics-ashes").AshTables()
    _W["ids"] = EXCH.weapon_ids()


def _worker_members(job):
    name, grip = job
    wid = _W["ids"].get(name)
    if wid is None:
        return job, None
    try:
        return job, {"wid": wid, "members": weapon_members(_W["reg"], _W["sa"], _W["ash"], wid, grip)}
    except Exception as exc:  # a weapon the extractors cannot read is reported, not fatal
        return job, {"wid": wid, "error": f"{type(exc).__name__}: {exc}"}


def seq_cache_path() -> Path:
    return CACHE / "interrupt-members.json"


def build_cache(jobs: int, sweep: Path, rl: int = 150, window: int = 10) -> dict:
    import multiprocessing as mp
    reg = ATK.Regulation(None)
    pool_raw = EXCH.opponent_pool(reg, CACHE / "builds.jsonl", rl - window, rl + window)
    targets = target_weapons(pool_raw, sweep)
    path = seq_cache_path()
    stamp = _source_stamp()
    got = json.loads(path.read_text()) if path.exists() else {}
    have = got.get("weapons", {}) if got.get("stamp") == stamp else {}
    todo = [t for t in targets if f"{t[0]}|{t[1]}" not in have]
    print(f"{len(targets)} weapon-grips, {len(todo)} to extract", flush=True)
    if todo:
        with mp.get_context("fork").Pool(jobs, initializer=_worker_init) as p:
            for i, (job, res) in enumerate(p.imap_unordered(_worker_members, todo, chunksize=2)):
                have[f"{job[0]}|{job[1]}"] = res
                if i % 50 == 0:
                    print(f"  {i + 1}/{len(todo)} {job}", flush=True)
    out = {"stamp": stamp, "weapons": have}
    path.write_text(json.dumps(out))
    return out


def load_cache() -> dict:
    path = seq_cache_path()
    if not path.exists():
        raise SystemExit(f"no sequence cache at {path}; run --build-cache first")
    got = json.loads(path.read_text())
    if got.get("stamp") != _source_stamp():
        print(f"warning: {path} was built from other sources; run --build-cache to refresh", file=sys.stderr)
    return got["weapons"]


# --------------------------------------------------------------------------------------------
# resolving members into timed sequences

class Delays:
    """Frames from a member's first hit to its 2.5 m front contact, for members the reach module
    could not pose: the median over the first tier with at least 3 measured values, weapon class
    (`wepType`) at the same slot and grip, the class at any slot, every weapon at the same slot,
    every weapon (`INFERRED`, the rule of `er-mechanics-reach.class_fallback` on the cached rows)."""

    def __init__(self, weapons: dict):
        self.by = defaultdict(list)
        for key, w in weapons.items():
            if not w or "members" not in w:
                continue
            grip = key.split("|")[1]
            for slot, m in w["members"].items():
                if m.get("fc") is not None and m.get("first") is not None:
                    d = m["fc"] - m["first"]
                    for k in ((m["wep_type"], slot, grip), (m["wep_type"], None, grip), (None, slot, grip),
                              (None, None, grip)):
                        self.by[k].append(d)

    def __call__(self, wep_type, slot, grip) -> float:
        for k in ((wep_type, slot, grip), (wep_type, None, grip), (None, slot, grip), (None, None, grip)):
            v = self.by.get(k)
            if v and len(v) >= 3:
                return float(np.median(v))
        return 0.0


def resolve(w: dict, grip: str, name: str, delays: Delays, entries: dict, sa_rate: bool = True) -> dict:
    """{opener name: sequence} of one weapon-grip. A sequence is {'hits': [(frame, poise)],
    'strike', 'windows': [(s, e, bonus, mult, floor)], 'invuln': [(s, e)], 'family', 'd1'}, all
    frames from the input (entry included)."""
    members = w["members"]
    out = {}
    for oname, family, keys, button, entry_key in OPENERS:
        if keys[0] not in members:
            continue
        entry = entries.get(entry_key, 0.0) if entry_key else 0.0
        inv = entries.get("inv_" + entry_key) if entry_key else None
        hits, windows, t0 = [], [], entry
        for k in keys:
            m = members.get(k)
            if m is None:
                break
            lead = m.get("lead") or 0.0
            if m.get("fc") is not None:
                delay = m["fc"] - m["first"]
            elif m.get("proxy") and members.get(m["proxy"], {}).get("fc") is not None:
                p = members[m["proxy"]]
                delay = p["fc"] - p["first"]
            else:
                delay = delays(m["wep_type"], k if not m.get("proxy") else m["proxy"], grip)
            base = t0 + lead
            for f, poise, sa in m["hits"]:
                hits.append((base + f + max(delay, 0.0), poise * (sa if sa_rate else 1.0)))
            for s, e, b, mult, fl in m["windows"]:
                windows.append((base + s, base + e, b, mult, fl))
            nxt = (m.get("cancel") or {}).get(button) if button else None
            if nxt is None:
                break
            t0 = base + nxt
        if not hits:
            continue
        hits.sort()
        out[oname] = {"hits": hits, "strike": hits[0][0], "windows": windows,
                      "invuln": [(0.0, inv)] if inv else [], "family": family, "d1": hits[0][1],
                      "weapon": name, "grip": grip}
    return out


def entry_table() -> dict:
    """Entry frames and entry invincibility per entry key (module constants and `a00.tae`)."""
    roll, bstep, jump = iframes(ROLL_ANIM), iframes(BACKSTEP_ANIM), jump_entry()
    return {**ENTRY_FRAMES, "jump": jump or 0.0, "inv_roll_r1": roll, "inv_bstep_r1": bstep}


# --------------------------------------------------------------------------------------------
# the poise simulation

MAX_WINDOWS = 4
MAX_HITS = 8
MAX_CHIP_STATES = 8


def _pad_windows(seqs):
    n = len(seqs)
    arr = np.zeros((5, n, MAX_WINDOWS), float)
    arr[0] = arr[1] = -1.0          # empty: start = end = -1 never covers a frame
    arr[3] = 1.0
    for i, s in enumerate(seqs):
        for k, win in enumerate(s["windows"][:MAX_WINDOWS]):
            arr[:, i, k] = win
    return arr


def _pad_invuln(seqs):
    arr = np.full((2, len(seqs), 1), -1.0)
    for i, s in enumerate(seqs):
        if s["invuln"]:
            arr[:, i, 0] = s["invuln"][0]
    return arr


def chip_states(P: np.ndarray, d: np.ndarray, carried: bool) -> tuple[np.ndarray, np.ndarray]:
    """(chip, weight) arrays of shape P.shape + (C,). Fresh: one state, 0. Carried: `j x d`,
    `j = 0 .. ceil(P / d) - 1` equally likely, at most `MAX_CHIP_STATES` evenly spaced js
    (`INFERRED` sampling of a longer cycle)."""
    P, d = np.broadcast_arrays(np.asarray(P, float), np.asarray(d, float))
    if not carried:
        return np.zeros(P.shape + (1,)), np.ones(P.shape + (1,))
    k = np.where(d > 0, np.ceil(np.maximum(P, 1e-9) / np.maximum(d, 1e-9)), 1.0)
    k = np.clip(k, 1, 1e6)
    C = MAX_CHIP_STATES
    idx = np.arange(C)
    n = np.minimum(k, C)
    # j values: all of 0..k-1 when k <= C, else C evenly spaced over it.
    j = np.where(k[..., None] <= C, idx, np.round(idx * (k[..., None] - 1) / (C - 1)))
    valid = idx < n[..., None]
    chip = np.where(valid, j * d[..., None], 0.0)
    w = np.where(valid, 1.0 / n[..., None], 0.0)
    return chip, w


def simulate(hit_t, hit_d, hit_m, strike, win, inv, P, chip, offsets):
    """Broken flags of the defender, broadcast over every leading dimension.

    `hit_t`, `hit_d`, `hit_m`: attacker hit frames (attacker clock), poise dealt, valid mask,
    shape (..., H). `strike`: the defender's strike frame (defender clock). `win`: (5, ..., K)
    window starts, ends, bonus, multiplier, floor (defender clock). `inv`: (2, ..., J). `P`,
    `chip`: defender poise and carried chip. `offsets`: frames the attacker starts after the
    defender. All must broadcast against each other with `hit_*` indexed on their last axis."""
    M = P + 0.0 * chip
    cur = M - chip
    prev = None
    broken = np.zeros(np.broadcast_shapes(np.shape(cur), np.shape(offsets), np.shape(strike)), bool)
    cur = np.broadcast_to(cur, broken.shape).copy()
    M = np.broadcast_to(M, broken.shape).copy()
    P = np.broadcast_to(P, broken.shape)
    ws, we, wb, wm, wf = win
    for h in range(hit_t.shape[-1]):
        f = offsets + hit_t[..., h]
        cover = (ws <= f[..., None]) & (f[..., None] < we)
        has = cover.any(-1)
        k = np.argmax(cover, -1)
        take = lambda a: np.take_along_axis(np.broadcast_to(a, cover.shape), k[..., None], -1)[..., 0]
        kk = np.where(has, k, -1)
        bonus = np.where(has, take(wb), 0.0)
        mult = np.where(has, take(wm), 1.0)
        floor = np.where(has, take(wf), 0.0)
        changed = kk != (-1 if prev is None else prev)
        new_m = P + bonus
        cur = np.where(changed, np.clip(new_m - (M - cur), floor * new_m, new_m), cur)
        M = np.where(changed, new_m, M)
        prev = kk
        blocked = (inv[0][..., 0] <= f) & (f < inv[1][..., 0])
        land = hit_m[..., h] & (f < strike) & ~blocked & ~broken
        cur = np.where(land, cur - hit_d[..., h] * mult, cur)
        broken |= land & (cur <= 1e-6)
    return broken


def _hits_array(seqs):
    n = len(seqs)
    t = np.zeros((n, MAX_HITS))
    d = np.zeros((n, MAX_HITS))
    m = np.zeros((n, MAX_HITS), bool)
    for i, s in enumerate(seqs):
        for h, (f, p) in enumerate(s["hits"][:MAX_HITS]):
            t[i, h], d[i, h], m[i, h] = f, p, True
    return t, d, m


class PoolRows:
    """The pool's attacks as defender rows (one per distinct attack sequence and poise value) and
    as attacker rows (one per distinct attack sequence)."""

    def __init__(self, pool_raw: dict, weapons: dict, delays: Delays, entries: dict, sa_rate: bool = True,
                 poise_quantiles=(10, 30, 50, 70, 90)):
        seqs, seq_w = [], []       # attacker rows: sequence, pool weight
        drows = Counter()           # (sequence index, poise) -> weight
        cache = {}
        builds = pool_raw["builds"]
        n = len(builds)
        self.missing = Counter()
        for key, poise in builds:
            if key not in cache:
                name, g = key.split("|")
                grip = "both" if g == "2h" else "one"
                w = weapons.get(f"{name}|{grip}")
                if not w or "members" not in w:
                    cache[key] = None
                else:
                    cache[key] = resolve(w, grip, name, delays, entries, sa_rate)
            res = cache[key]
            if not res:
                self.missing[key] += 1
                continue
            fams = defaultdict(list)
            for oname, s in res.items():
                if s["family"] in POOL_FAMILIES:
                    fams[s["family"]].append(oname)
            for fam, names in fams.items():
                for oname in names:
                    wgt = 1.0 / n / len(fams) / len(names)
                    ident = (key, oname)
                    if ident not in cache:
                        cache[ident] = len(seqs)
                        seqs.append(res[oname])
                        seq_w.append(0.0)
                    i = cache[ident]
                    seq_w[i] += wgt
                    drows[(i, float(poise))] += wgt
        self.n_builds = n
        self.seqs = seqs
        self.seq_w = np.array(seq_w)
        self.seq_w /= self.seq_w.sum()
        keys = sorted(drows)
        self.d_idx = np.array([i for i, _ in keys], int)
        self.d_poise = np.array([p for _, p in keys])
        self.d_w = np.array([drows[k] for k in keys])
        self.d_w /= self.d_w.sum()
        self.d_strike = np.array([seqs[i]["strike"] for i in self.d_idx])
        win = _pad_windows(seqs)
        self.d_win = win[:, self.d_idx]
        inv = _pad_invuln(seqs)
        self.d_inv = inv[:, self.d_idx]
        # Attacker side.
        self.a_t, self.a_d, self.a_m = _hits_array(seqs)
        self.a_d1 = np.array([s["d1"] for s in seqs])
        self.my_poise = np.percentile(np.array(pool_raw["poise"], float), poise_quantiles)
        self.max_off = int(math.ceil(self.d_strike.max()))


def forward(rows: PoolRows, seq: dict, carried: bool) -> np.ndarray:
    """(defender rows, offsets) break probability of one attack sequence against every pool row,
    NaN at offsets past the row's strike frame."""
    O = np.arange(rows.max_off, dtype=float)
    R = len(rows.d_idx)
    valid = O[None, :] < rows.d_strike[:, None]
    out = np.where(valid, 0.0, np.nan)
    hit_t = np.array([f for f, _ in seq["hits"][:MAX_HITS]])
    # Rows whose strike comes at or before A's first hit at offset 0 cannot be reached.
    live = np.nonzero(rows.d_strike > hit_t[0])[0]
    if not len(live):
        return out
    hit_d = np.array([p for _, p in seq["hits"][:MAX_HITS]])
    keep = hit_t < rows.d_strike[live].max()
    hit_t, hit_d = hit_t[keep], hit_d[keep]
    hit_m = np.ones(len(hit_t), bool)
    n = len(live)
    chip, cw = chip_states(rows.d_poise[live], np.full(n, seq["d1"]), carried)       # (n, C)
    C = chip.shape[-1]
    win = rows.d_win[:, live][:, :, None, None, :]                                      # (5, n, 1, 1, K)
    inv = rows.d_inv[:, live][:, :, None, None, :]
    Ol = O[: int(math.ceil(rows.d_strike[live].max()))]
    broken = simulate(hit_t, hit_d, hit_m, rows.d_strike[live, None, None], win, inv,
                      rows.d_poise[live, None, None], chip.reshape(n, C, 1), Ol[None, None, :])
    prob = (broken * cw[:, :, None]).sum(1)                                            # (n, O')
    out[live, : len(Ol)] = np.where(valid[live, : len(Ol)], prob, np.nan)
    return out


#: Armor poise (menu) at or below which a pool build counts as low poise: the pool's 25th
#: percentile is 53, and 51 is the community one-R1 breakpoint (`INFERRED` cut).
LOW_POISE = 53.0


def share(rows: PoolRows, prob: np.ndarray, low: bool = False) -> float:
    """Pool-weighted mean over rows of the mean over valid offsets; `low` restricts the pool to
    builds at or below `LOW_POISE`, renormalised."""
    per_row = np.nan_to_num(np.nanmean(prob, axis=1))
    w = rows.d_w * (rows.d_poise <= LOW_POISE) if low else rows.d_w
    return float((per_row * w).sum() / w.sum())


def reverse(rows: PoolRows, seq: dict, carried: bool) -> float:
    """Pool-weighted share of (pool attack, offset, my poise quantile) in which the pool attack,
    started `offset` frames into `seq`'s windup, breaks `seq`'s user before `seq` strikes."""
    S = seq["strike"]
    O = np.arange(int(math.ceil(S)), dtype=float)
    if not len(O):
        return 0.0
    Q = len(rows.my_poise)
    win = _pad_windows([seq])[:, 0]                  # (5, K)
    inv = _pad_invuln([seq])[:, 0]                   # (2, 1)
    # Attackers whose first hit at offset 0 is already at or past the strike cannot break it.
    live = np.nonzero(rows.a_t[:, 0] < S)[0]
    if not len(live):
        return 0.0
    H = int(max(1, (rows.a_m[live] & (rows.a_t[live] < S)).sum(1).max()))
    P = np.broadcast_to(rows.my_poise[None, :], (len(live), Q))
    chip, cw = chip_states(P, np.broadcast_to(rows.a_d1[live, None], P.shape), carried)   # (A, Q, C)
    # Axes: (attacker row, poise, chip, offset, [hit]); the defender's own clock is the base, the
    # attacker starts `offset` later.
    ht = rows.a_t[live, None, None, None, :H]
    hd = rows.a_d[live, None, None, None, :H]
    hm = rows.a_m[live, None, None, None, :H]
    wins = win[:, None, None, None, None, :]
    invs = inv[:, None, None, None, None, :]
    broken = simulate(ht, hd, hm, S, wins, invs, P[:, :, None, None], chip[:, :, :, None], O)
    prob = (broken * cw[..., None]).sum(2).mean(-1).mean(-1)        # (A,)
    return float((prob * rows.seq_w[live]).sum())


# --------------------------------------------------------------------------------------------
# per weapon

def weapon_matrix(rows: PoolRows, seqs: dict) -> dict:
    """Per opener and per weapon: stop and stopped shares, fresh and carried."""
    out = {"slots": {}}
    for scen in ("fresh", "carried"):
        carried = scen == "carried"
        probs = {}
        for oname, s in seqs.items():
            p = forward(rows, s, carried)
            probs[oname] = p
            slot = out["slots"].setdefault(oname, {"family": s["family"], "strike": round(s["strike"], 1),
                                                   "hits": len(s["hits"]), "d1": round(s["d1"], 1)})
            slot[f"stop_{scen}"] = share(rows, p)
            slot[f"stop_low_{scen}"] = share(rows, p, low=True)
            slot[f"stopped_{scen}"] = reverse(rows, s, carried)
        if not probs:
            continue
        # The skill stays out of the aggregates: its frames come from the skill TimeAct's clip
        # frames of `main_anim`, which for several skills is a later part of the move (Marika's
        # Hammer strikes on frame 1.5, Wild Strikes on 5.5), so it would read as the fastest
        # attack in the game. It is still reported per slot, and `stop_best_with_skill` keeps it.
        stack = np.stack([p for o, p in probs.items() if seqs[o]["family"] in W_FAMILIES])
        out[f"stop_best_{scen}"] = share(rows, np.nanmax(stack, 0))
        out[f"stop_best_low_{scen}"] = share(rows, np.nanmax(stack, 0), low=True)
        out[f"stop_best_with_skill_{scen}"] = share(rows, np.nanmax(np.stack(list(probs.values())), 0))
        fam_best = []
        for fam in W_FAMILIES:
            members = [probs[o] for o in probs if seqs[o]["family"] == fam]
            if members:
                fam_best.append(share(rows, np.nanmax(np.stack(members), 0)))
        out[f"stop_family_{scen}"] = float(np.mean(fam_best))
        fam_stopped = []
        for fam in W_FAMILIES:
            members = [out["slots"][o][f"stopped_{scen}"] for o in probs if seqs[o]["family"] == fam]
            if members:
                fam_stopped.append(float(np.mean(members)))
        out[f"stopped_{scen}"] = float(np.mean(fam_stopped))
        if "r1" in probs:
            out[f"stop_r1_{scen}"] = out["slots"]["r1"][f"stop_{scen}"]
            out[f"stopped_r1_{scen}"] = out["slots"]["r1"][f"stopped_{scen}"]
    return out


_R = {}


def _run_init(rows, weapons, delays, entries, sa_rate):
    _R.update(rows=rows, weapons=weapons, delays=delays, entries=entries, sa_rate=sa_rate)


def _run_one(key):
    w = _R["weapons"].get(key)
    if not w or "members" not in w:
        return key, None
    name, grip = key.split("|")
    seqs = resolve(w, grip, name, _R["delays"], _R["entries"], _R["sa_rate"])
    if not seqs:
        return key, None
    return key, weapon_matrix(_R["rows"], seqs)


def run(jobs: int, out: Path, sweep: Path, only: list[str] | None = None, sa_rate: bool = True,
        rl: int = 150, window: int = 10) -> dict:
    import multiprocessing as mp
    reg = ATK.Regulation(None)
    pool_raw = EXCH.opponent_pool(reg, CACHE / "builds.jsonl", rl - window, rl + window)
    weapons = load_cache()
    delays = Delays(weapons)
    entries = entry_table()
    rows = PoolRows(pool_raw, weapons, delays, entries, sa_rate)
    names = set()
    for line in sweep.open():
        names.add(EXCH.plain_name(json.loads(line)["weapon"]))
    keys = sorted(f"{n}|{g}" for n in names for g in ("one", "both"))
    if only:
        keys = [k for k in keys if k.split("|")[0] in only]
    print(f"pool: {rows.n_builds} builds, {len(rows.seqs)} attack sequences, {len(rows.d_idx)} defender rows, "
          f"missing {sum(rows.missing.values())}; {len(keys)} weapon-grips", flush=True)
    results = {}
    _run_init(rows, weapons, delays, entries, sa_rate)
    with mp.get_context("fork").Pool(jobs) as p:
        for i, (key, res) in enumerate(p.imap_unordered(_run_one, keys, chunksize=1)):
            results[key] = res
            if i % 25 == 0:
                print(f"  {i + 1}/{len(keys)} {key}", flush=True)
    doc = {"rl": [rl - window, rl + window], "sa_rate": sa_rate, "entries": entries,
           "poise_reset_s": poise_reset_seconds(), "pool_builds": rows.n_builds,
           "pool_sequences": len(rows.seqs), "my_poise_quantiles": rows.my_poise.tolist(),
           "weapons": results}
    out.write_text(json.dumps(doc))
    return doc


# --------------------------------------------------------------------------------------------
# the interface for the PvP ranking

#: Span of the interrupt factor, `INFERRED`: the same span as `er-mechanics-exchange`'s
#: `EXCHANGE_WEIGHT`, which it replaces for the slots it covers (docs/er-mechanics/interrupt.md
#: section 6).
INTERRUPT_WEIGHT = 0.25
#: `er-builds-pvp` slot key (no `2h_` prefix) -> opener sequence here. Chain follow-ups are not
#: openers and stay neutral.
SLOT_OPENER = {"r1_1": "r1", "r2_1": "r2", "r2_1c": "r2c", "run_r1": "run_r1", "run_r2": "run_r2",
               "roll_r1": "roll_r1", "bstep_r1": "bstep_r1", "crouch_r1": "crouch_r1",
               "jump_r1": "jump_r1", "jump_r2": "jump_r2"}


def load_matrix(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def slot_interrupt(doc: dict, weapon: str, two: bool, slot: str, scenario: str = "fresh") -> dict | None:
    """{'stop', 'stopped', 'f_interrupt'} of one ranking slot from a `--run` result, or None when
    the weapon or slot is not in it. `f_interrupt = 1 + INTERRUPT_WEIGHT x (stop - stopped)`."""
    w = (doc.get("weapons") or {}).get(f"{EXCH.plain_name(weapon)}|{'both' if two else 'one'}")
    o = SLOT_OPENER.get(slot.removeprefix("2h_"))
    s = (w or {}).get("slots", {}).get(o) if o else None
    if not s:
        return None
    stop, stopped = s[f"stop_{scenario}"], s[f"stopped_{scenario}"]
    return {"stop": stop, "stopped": stopped, "f_interrupt": 1.0 + INTERRUPT_WEIGHT * (stop - stopped)}


# --------------------------------------------------------------------------------------------
# report

def report(doc: dict, top: int, names: list[str]) -> None:
    rows = [(k, v) for k, v in doc["weapons"].items() if v and "stop_best_fresh" in v]
    rows.sort(key=lambda kv: -kv[1]["stop_best_fresh"])

    def line(i, k, v):
        n, g = k.split("|")
        r1 = v.get("slots", {}).get("r1", {})
        return (f"{i:>4} {n + (' 2H' if g == 'both' else ' 1H'):<38} best {100 * v['stop_best_fresh']:5.1f} "
                f"/{100 * v['stop_best_carried']:5.1f}  low {100 * v.get('stop_best_low_fresh', 0):5.1f} "
                f"/{100 * v.get('stop_best_low_carried', 0):5.1f}  fam {100 * v['stop_family_fresh']:5.1f} "
                f"/{100 * v['stop_family_carried']:5.1f}  r1 {100 * v.get('stop_r1_fresh', 0):5.1f} "
                f"/{100 * v.get('stop_r1_carried', 0):5.1f}  stopped {100 * v['stopped_fresh']:5.1f} "
                f"/{100 * v['stopped_carried']:5.1f}  r1 strike {r1.get('strike', '-')} d1 {r1.get('d1', '-')}")

    print(f"RL {doc['rl']}, saRate {'on' if doc['sa_rate'] else 'off'}; columns fresh / carried, percent")
    for i, (k, v) in enumerate(rows[:top], 1):
        print(line(i, k, v))
    rank = {k: i for i, (k, _) in enumerate(rows, 1)}
    for n in names:
        for g in ("one", "both"):
            k = f"{n}|{g}"
            if k in rank:
                print(line(rank[k], k, doc["weapons"][k]))


CHIP_WEAPONS = ["Cleanrot Knight's Sword", "Cleanrot Spear", "Dagger", "Misericorde", "Hookclaws",
                "Bloodhound Claws", "Raptor Talons", "Uchigatana", "Longsword", "Rapier", "Estoc", "Hand Axe",
                "Lance", "Greatsword", "Giant-Crusher"]


def chip_report(doc: dict, names: list[str]) -> None:
    """Fresh against carried chip for named weapons, and the weapons carried chip helps most."""
    d = doc["weapons"]
    print(f"{'weapon':<30}{'best f / c':>14}{'r1 f / c':>14}{'low f / c':>14}{'stopped f / c':>15}"
          f"{'r1 strike':>10}{'d1':>7}")
    for n in names:
        for g in ("one", "both"):
            v = d.get(f"{n}|{g}")
            if not v:
                continue
            r1 = v["slots"].get("r1", {})

            def p(k):
                return 100 * v.get(k, 0.0)

            print(f"{n + (' 2H' if g == 'both' else ' 1H'):<30}{p('stop_best_fresh'):7.1f} /{p('stop_best_carried'):5.1f}"
                  f"{p('stop_r1_fresh'):7.1f} /{p('stop_r1_carried'):5.1f}"
                  f"{p('stop_best_low_fresh'):7.1f} /{p('stop_best_low_carried'):5.1f}"
                  f"{p('stopped_fresh'):8.1f} /{p('stopped_carried'):5.1f}{r1.get('strike', '-'):>10}{r1.get('d1', '-'):>7}")
    rows = [(k, v) for k, v in d.items() if v and "stop_best_fresh" in v]
    gain = sorted(rows, key=lambda kv: kv[1]["stop_best_carried"] - kv[1]["stop_best_fresh"], reverse=True)
    print("largest carried-chip gain in stop_best:")
    for k, v in gain[:10]:
        print(f"  {k:<36}{100 * v['stop_best_fresh']:6.1f} -> {100 * v['stop_best_carried']:6.1f}   r1 "
              f"{100 * v.get('stop_r1_fresh', 0):5.1f} -> {100 * v.get('stop_r1_carried', 0):5.1f}   "
              f"r1 d1 {v['slots'].get('r1', {}).get('d1')}")
    gb = [100 * (v["stop_best_carried"] - v["stop_best_fresh"]) for _, v in rows]
    gr = [100 * (v.get("stop_r1_carried", 0) - v.get("stop_r1_fresh", 0)) for _, v in rows]
    print(f"median gain over {len(rows)} weapon-grips: best {np.median(gb):.2f}, r1 {np.median(gr):.2f}; "
          f"mean best {np.mean(gb):.2f}, r1 {np.mean(gr):.2f}")


def print_weapon(doc: dict, name: str, grip: str) -> None:
    v = doc["weapons"].get(f"{name}|{grip}")
    if not v:
        print("no result for", name, grip)
        return
    print(f"{name} {grip}: stop best {100 * v['stop_best_fresh']:.1f}% / {100 * v['stop_best_carried']:.1f}% "
          f"(fresh / carried), stopped {100 * v['stopped_fresh']:.1f}% / {100 * v['stopped_carried']:.1f}%")
    for o, s in v["slots"].items():
        print(f"  {o:<12} {s['family']:<6} strike {s['strike']:>5} hits {s['hits']} d1 {s['d1']:>6}  "
              f"stop {100 * s['stop_fresh']:5.1f} / {100 * s['stop_carried']:5.1f}  "
              f"low {100 * s.get('stop_low_fresh', 0):5.1f} / {100 * s.get('stop_low_carried', 0):5.1f}  "
              f"stopped {100 * s['stopped_fresh']:5.1f} / {100 * s['stopped_carried']:5.1f}")


# --------------------------------------------------------------------------------------------
# self test

#: Instructions of the 1.16.2 image (`eldenring-deobf.bin`, shift 0 against the named dump) the
#: poise rules above rest on, byte-checked by the selftest.
EXE_ANCHORS = [
    (0x140486d52, bytes.fromhex("80bf5802000000"),
     "FUN_140486bf0: cmp byte [info+0x258], 0 (a guarded hit skips the poise subtraction)"),
    (0x140486d93, bytes.fromhex("807f2400"),
     "FUN_140486bf0: cmp byte [info+0x24], 0 (the hit's damage level; nonzero re-arms the timer)"),
    (0x140486d9f, bytes.fromhex("ff5040f30f114320"),
     "FUN_140486bf0: timer [+0x20] = toughness vtable slot 8 (recover time)"),
    (0x140486f80, bytes.fromhex("f30f104b20"),
     "FUN_140486e50: loads the timer [+0x20] to count it down by deltaTime"),
    (0x140487c70, bytes.fromhex("e9db112000"),
     "slot 8 (0x140487b00) ends in jmp FUN_140688e50 = baseToughnessRecoverTime x its product"),
]


def _read(va: int, size: int, path=ATK.DEOBF_1162) -> bytes:
    with open(path, "rb") as f:
        f.seek(va - 0x140000000)
        return f.read(size)


def selftest() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok   " if cond else "FAIL ") + msg)
        ok = ok and bool(cond)

    # Synthetic defender: strike at 20, poise 80, no window; attacker hits at 5 (+offset).
    none_win = np.full((5, 1, MAX_WINDOWS), -1.0)
    none_win[2] = 0.0
    none_win[3] = 1.0
    none_win[4] = 0.0
    none_inv = np.full((2, 1, 1), -1.0)

    def sim(hits, P=80.0, chip=0.0, strike=20.0, win=none_win, inv=none_inv, off=0.0):
        t = np.array([[h[0] for h in hits]])
        d = np.array([[h[1] for h in hits]])
        m = np.ones_like(t, bool)
        return bool(simulate(t, d, m, np.array([strike]), win, inv, np.array([P]), np.array([chip]),
                             np.array([off]))[0])

    check(sim([(5, 80)]) and not sim([(5, 79)]), "one hit breaks exactly when dealt >= poise")
    check(not sim([(5, 100)], off=15.0), "a hit at or after the defender's strike frame does not count")
    check(sim([(5, 50), (10, 40)]) and not sim([(5, 50), (25, 40)]),
          "two hits below poise accumulate; the second must land before the strike")
    w = none_win.copy()
    w[:, 0, 0] = [0.0, 30.0, 100.0, 0.45, 0.8]
    check(not sim([(5, 300)], win=w) and sim([(5, 400)], win=w),
          "inside a +100 window at 0.45: 300 x 0.45 < 180, 400 x 0.45 = 180 breaks")
    w2 = none_win.copy()
    w2[:, 0, 0] = [10.0, 30.0, 100.0, 1.0, 0.8]
    check(sim([(5, 20), (12, 160)], win=w2) and not sim([(5, 20), (12, 159)], win=w2),
          "chip before a window carries through it: 20 taken, 180 - 20 = 160 left inside")
    check(sim([(5, 60), (12, 144)], win=w2) and not sim([(5, 60), (12, 143)], win=w2),
          "entering a window heals chip above its floor (60 taken, current clamped to 144)")
    inv = np.array([[[0.0]], [[13.0]]])
    check(not sim([(5, 200)], inv=inv) and sim([(15, 200)], inv=inv), "a hit inside the roll i-frames whiffs")
    chip, cw = chip_states(np.array([80.0]), np.array([50.0]), True)
    check(np.allclose(chip[0, :2], [0, 50]) and np.isclose(cw[0].sum(), 1.0) and np.isclose(cw[0, 0], 0.5),
          "carried chip cycle for d 50 against 80: states 0 and 50, half each")
    check(sim([(5, 50)], chip=50.0) and not sim([(5, 50)], chip=0.0),
          "a carried chip of one earlier hit lets the same hit break")
    chip, cw = chip_states(np.array([100.0]), np.array([5.0]), True)
    check(np.count_nonzero(cw[0]) == MAX_CHIP_STATES and np.isclose(chip[0, -1], 95.0),
          "a 20-hit cycle is sampled at 8 evenly spaced states, last one 19 x 5")

    doc = {"weapons": {"Dagger|one": {"slots": {"r1": {"stop_fresh": 0.4, "stopped_fresh": 0.1}}}}}
    si = slot_interrupt(doc, "Dagger", False, "r1_1")
    check(si is not None and abs(si["f_interrupt"] - (1 + INTERRUPT_WEIGHT * 0.3)) < 1e-12
          and slot_interrupt(doc, "Dagger", False, "r1_2") is None,
          "ranking interface: R1 #1 reads the R1 chain opener, a chain follow-up stays neutral")

    # Game data.
    reset = poise_reset_seconds()
    check(reset == 30.0, f"poise reset {reset} s (GameSystemCommonParam 30.0 x PlayerCommonParam 1.0)")
    rates = armor_recover_rates()
    check(set(rates) == {0.0}, f"every EquipParamProtector.toughnessRecoverCorrection is 0 ({dict(rates)})")
    cp = counter_poise_rate()
    check(cp["toughnessDamageCutRate"] == 1.0 and cp["changeSuperArmorPoint"] == 0.0
          and abs(cp["thrustDamageCutRate"] - 1.15) < 1e-6,
          f"SpEffect 45 counter: thrust HP x1.15, poise damage x{cp['toughnessDamageCutRate']}")
    check(iframes(ROLL_ANIM) == 13.0 and iframes(BACKSTEP_ANIM) is None,
          f"entry i-frames: medium roll {iframes(ROLL_ANIM)}; backstep {iframes(BACKSTEP_ANIM)}: its only "
          f"JumpTable 8 (f0-7) is gated on stateInfo 473, the Fine Crucible Feather (TAE a00)")
    import ast
    tree = ast.parse((HERE / "er-builds-pvp.py").read_text())
    pvp_entry = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                     and any(getattr(t, "id", None) == "SCORE_ENTRY_FRAMES" for t in n.targets))
    ours = {**ENTRY_FRAMES, "jump_r1": jump_entry(), "jump_r2": jump_entry()}
    check(all(pvp_entry.get(k) == v for k, v in ours.items() if k in pvp_entry),
          f"entry frames agree with er-builds-pvp.SCORE_ENTRY_FRAMES ({ours})")
    if os.path.exists(ATK.DEOBF_1162):
        vt = struct.unpack_from("<Q", _read(0x142a3bf40, 8))[0]
        check(vt == 0x140487b00, f"player toughness vtable slot 8 at 0x142a3bf40 -> {vt:#x} (the recover-time getter)")
        for va, want, what in EXE_ANCHORS:
            got = _read(va, len(want))
            check(got == want, f"{va:#x} {want.hex()}: {what}")
    else:
        print("skip EXE checks: no", ATK.DEOBF_1162)

    # Real weapons.
    reg = ATK.Regulation(None)
    sa = EXCH.SaRates()
    ids = EXCH.weapon_ids()
    ash = _mod("er-mechanics-ashes").AshTables()
    cks = weapon_members(reg, sa, ash, ids["Cleanrot Knight's Sword"], "one")
    check(all(k in cks for k in ("r1_1", "r1_2", "r1_3")) and "dual_1" in cks,
          f"Cleanrot Knight's Sword 1H has an R1 chain and a powerstance L1 ({sorted(cks)[:6]}...)")
    gs = weapon_members(reg, sa, ash, ids["Greatsword"], "both")
    check(gs["r1_1"]["windows"] and gs["r1_1"]["windows"][0][2] > 0 and gs["r1_1"]["windows"][0][4] == 0.8,
          f"Greatsword 2H R1 has a hyperarmor window with a bonus and the 80% floor ({gs['r1_1']['windows']})")
    delays = Delays({"Cleanrot Knight's Sword|one": {"wid": 0, "members": cks},
                     "Greatsword|both": {"wid": 0, "members": gs}})
    seqs = resolve({"members": cks}, "one", "Cleanrot Knight's Sword", delays, entry_table())
    r1 = seqs["r1"]
    check(len(r1["hits"]) >= 3 and all(b[0] > a[0] for a, b in zip(r1["hits"], r1["hits"][1:])),
          f"R1 chain hit frames rise {[round(h[0], 1) for h in r1['hits']]}")
    roll = seqs.get("roll_r1")
    check(roll is not None and roll["invuln"] == [(0.0, 13.0)] and roll["strike"] > 20.0,
          "rolling R1 starts after the 20-frame roll entry and carries its 13 i-frames")
    print("selftest", "passed" if ok else "FAILED")
    return 0 if ok else 1


# --------------------------------------------------------------------------------------------
# command line

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--build-cache", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--report", type=Path)
    ap.add_argument("--weapon")
    ap.add_argument("--chip", action="store_true", help="with --report: fresh against carried chip")
    ap.add_argument("--grip", choices=("one", "both"), default="one")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--no-sa-rate", action="store_true")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--out", type=Path)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--sweep", type=Path, default=CACHE / "grease-sweep-dlc-drawstring-150-200.jsonl")
    ap.add_argument("--rl", type=int, default=150)
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.build_cache:
        build_cache(a.jobs, a.sweep, a.rl)
        return 0
    if a.run:
        out = a.out or Path(f"interrupt-{a.rl}.json")
        run(a.jobs, out, a.sweep, a.only, not a.no_sa_rate, a.rl)
        print("wrote", out)
        return 0
    if a.report:
        doc = json.loads(a.report.read_text())
        if a.chip:
            chip_report(doc, a.only or CHIP_WEAPONS)
        elif a.weapon:
            print_weapon(doc, a.weapon, a.grip)
        else:
            report(doc, a.top, a.only or ["Cleanrot Knight's Sword", "Cleanrot Spear", "Giant-Crusher",
                                          "Greatsword", "Hand Axe", "Lance"])
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
