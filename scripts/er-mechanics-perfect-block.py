#!/usr/bin/env python3
"""The PvP perfect block against my attacks: what it costs an ash or a weapon attack when the
opponent lands one.

    python3 scripts/er-mechanics-perfect-block.py --build <build.json> \
        [--weapons-from <embed.json>] --json <out.json>
    python3 scripts/er-mechanics-perfect-block.py --selftest

The user asked for it on 2026-10-10: no live sessions, so the perfect block is modelled from its
effects, not from its mechanism, which is unproven. Labels as `docs/er-mechanics/attacks.md`:
`VERIFIED`, `TAE`, `MEASURED`, `INFERRED`, `COMMUNITY`. Only the opponent's side is modelled: my
own perfect blocking has no execution model a planner could trust (a 1-frame press whose
mechanism is not known), so it is out of scope.

Effects (bd `perfect-block-static-findings-1171-2026-10-10`, scratchpad `pb-*.md`):

- `VERIFIED`: a perfect-blocked hit is judged guarded (`CalculateDamage` reads the previous frame's
  guard bit), so the guard's cut and stamina cost apply, but the block-stun reaction is skipped.
  The stuns it skips, first actionable frame (`SKIPPED_STUN`): small 24f clip, R1/roll f5, re-guard
  f16; middle 50f, R1/roll f10, re-guard f32; large 64f, R1 f50, roll f47; guard break 70f. The
  stun level is `GUARD_REACTION_TABLE[guard level][damage level]` (`FUN_140686f00`).
- `COMMUNITY` (pb-yt-*.vtt): a ~1-frame input timed to impact; works against single hits and
  spells; a status proc still deals its damage but the effect is dropped; multi-hit attacks
  (Milady, twinblades, Spinning Slash) catch the blocker with their second hit; ~10% of players
  use it; the punish that follows is not always guaranteed.

Chance per attack: P = carry x attempt x success.

- carry: the share of the RL 100-150 corpus (`er-mechanics-shields-ranged.RL_WINDOW`) whose loaded
  stance raises a guard (`er-mechanics-powerstance-guard.Blockers.can`, `MEASURED`): a powerstance,
  a bow two-handed or a catalyst in the left hand cannot perfect-block.
- attempt: `PB_USE` 0.10 (`INFERRED` from the community's "~10% of players"), read as the share
  of guard-capable opponents who answer a given attack with a perfect-block press.
- success: the parry model's press timing (`er-mechanics-parry.p_react`, `p_read`, mixed by
  `REACT_SHARE`): a reacting blocker presses at max(d, c + eps), d his reaction delay, eps normal
  with sd sqrt(0.05^2 + (0.10 c)^2) s, so the longer the telegraph the less exact the press; a
  reading blocker presses uniformly in the windup. The press must land inside `PB_WINDOW_FRAMES`
  (1 frame, `INFERRED` from the community's "~1-frame input") ending on the impact frame. The
  telegraph is the first hit frame from the attack's cue: the cast start (plus a two-handed row's
  grip change, which the blocker sees) or an R2's release. A bullet's telegraph is its spawn
  frame: travel is not modelled, as in the whiff model.

Effects when it lands (per cast, on the cast clock):

- my hits on the impact frame deal the corpus's mean guarded share (`Guards.pass_share`: each
  guard's cut per element through `block_hit`'s formula, my attack's element split, the type cut,
  my weapon's guard-cut cancel; guards weighted by how many corpus builds raise them);
- status HP on those hits: a burst proc (bleed, frost, madness) keeps its HP, a damage-over-time
  proc (`DOT_STATUSES`, poison and scarlet rot) loses it (`INFERRED` reading of "procs deal
  damage but the effect is dropped": the effect of a poison or rot proc is its ticks);
- the blocker acts on the impact frame and punishes like the whiff model's roller
  (`er-mechanics-ash-whiff.whiff` with zero i-frames and zero roll recovery: his pool string,
  `B_HIT_HP` per hit, the pool-weighted mean R1 #1, closing the same gap, hitting until my gate,
  my hyperarmor turning a punish into a trade);
- protection: a later hit of mine that lands before his first punish hit stops the punish and
  is delivered (whiff's `stopped`). A weapon attack's later hit includes the next attack of its
  chain (`CHAIN_NEXT`: its next-attack cancel plus the next slot's first hit), which counts as a
  stopper and adds no damage to the attack being costed.

  The protection window is the blocker's punish time (his approach plus his R1's first hit), not
  the skipped stun's own R1 frame. The stun is what the perfect block removes, so its frames are
  not a window he is held in any more. Taken literally, the stun's R1 frame (f5 small) would
  protect almost nothing and contradict the community's Milady and twinblade second hits; and a
  large stun's f50 would protect a second hit at f30 that the blocker, free at f0, beats with his
  R1 at ~f12. The skipped stun is reported per attack as `stun_skipped` (the mean R1 frame the
  normal stun would have imposed, over the corpus guards): the frames a perfect block gains over
  a normal block.

Value (`er-mechanics-combo-loadout.py --perfect-block`): with `full` the cast's hit damage,
`v_T` the hits on the impact frame, `late` and `taken` the whiff outcome's delivered later hits
and HP taken, `score` and `srate` the row's damage and status rates,

    pb_val = score x (chip v_T + late - taken) / full + srate x (k v_T + late) / full
    bmult  = 1 - P + P x max(0, pb_val / (score + srate))

with k the burst share of the row's status HP. It multiplies `value` like `pmult`: P is a few
percent at most, so multiplying the replies is their mixture to first order.

Weapons (`--weapons-from`, `er-mechanics-class-roles.py --perfect-block`): per weapon, grip and
affinity, the HP per attack thrown the perfect blockers take off, `cost` = P x (full - chip v_T
- k s_T - late + taken), family-weighted (r1, r2, move, jump equal, as `er-mechanics-parry`),
damage and status per hit from the embedding's slots, frames from `weapon_attacks` (right hand)
or `offhand_rows` (L1). Move and jump attacks and the last step of a chain have no follow-up
modelled (`INFERRED`: what comes after a rolling attack is not one fixed slot).
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

HERE = Path(__file__).resolve().parent
_MODS = {}


def _load(key, fname):
    if key not in _MODS:
        mod = sys.modules.get(key)
        if mod is None:
            spec = importlib.util.spec_from_file_location(key, HERE / fname)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[key] = mod
            spec.loader.exec_module(mod)
        _MODS[key] = mod
    return _MODS[key]


def PM():
    return _load("er_mechanics_parry", "er-mechanics-parry.py")


def W():
    return _load("er_mechanics_ash_whiff", "er-mechanics-ash-whiff.py")


def G():
    return PM().G()


def AS():
    return PM().AS()


ELEMENTS = ("physical", "magic", "fire", "lightning", "holy")
#: The press window, frames (`INFERRED`: community "~1-frame input" timed to impact).
PB_WINDOW_FRAMES = 1.0
#: Share of guard-capable opponents who answer an attack with a perfect-block press (`INFERRED`:
#: community anecdote, "~10% of players use it").
PB_USE = 0.10
#: Statuses whose proc HP is the effect itself (ticks), dropped on a perfect-blocked hit
#: (`INFERRED`); bleed, frost and madness are bursts and keep theirs.
DOT_STATUSES = frozenset({"poison", "scarlet_rot"})
#: Block stuns a perfect block skips (`VERIFIED`, bd perfect-block-static-findings-1171-2026-10-10,
#: scratchpad pb-tae-guard-summary.txt): clip length and first actionable frames. Guard break
#: (a00_019500) from its clip: roll f43, R1 f55 (`er-mechanics-powerstance-guard.GUARD_BREAK_R1`).
SKIPPED_STUN = {
    "none": {"len": 0, "r1": 0, "roll": 0, "guard": 0},
    "small": {"len": 24, "r1": 5, "roll": 5, "guard": 16},
    "middle": {"len": 50, "r1": 10, "roll": 10, "guard": 32},
    "large": {"len": 64, "r1": 50, "roll": 47, "guard": None},
    "break": {"len": 70, "r1": 55, "roll": 43, "guard": None},
}
#: Attack families weighed equally per weapon (`er-mechanics-parry.FAMILIES` without the skill,
#: which the ash side costs per cast).
FAMILIES = ("r1", "r2", "move", "jump")
#: Next attack of a chain: the slot a second press of the same button plays.
CHAIN_NEXT = {**{f"r1_{i}": f"r1_{i + 1}" for i in range(1, 6)},
              "r2_1": "r2_2", "r2_1c": "r2_2",
              **{f"left_{i}": f"left_{i + 1}" for i in range(1, 6)}}
#: Engagement distance as a share of an attack's reach (`er-mechanics-ash-interrupt.REACH_SHARE`).
REACH_SHARE = 0.75


# ---------------------------------------------------------------------------------------------
# timing


class Timing:
    """`success(h)`: the chance a perfect-block press lands in the window ending on impact frame
    `h` (frames from the attack's cue), from the parry model's reaction and timing error."""

    def __init__(self, react_share=None, delays=None, sd_frames=None, cv=None, window=PB_WINDOW_FRAMES):
        A, pm = AS(), PM()
        self.rs = A.REACT_SHARE if react_share is None else react_share
        self.delays = delays if delays is not None else A.reaction_delays()
        self.sd = sd_frames if sd_frames is not None else A.DODGE_TIMING_SD_S * A.TAE_FPS
        self.cv = pm.TIMING_CV if cv is None else cv
        self.window = window
        self._c = {}

    def parts(self, h):
        key = round(float(h), 2)
        if key not in self._c:
            pm = PM()
            iv = [(h - self.window, h)]
            r = pm.p_react(iv, self.delays, self.sd, self.cv) if h > 0 else 0.0
            t = pm.p_read(iv, h) if h > 0 else 0.0
            self._c[key] = (r, t, self.rs * r + (1.0 - self.rs) * t)
        return self._c[key]

    def success(self, h):
        return self.parts(h)[2]


# ---------------------------------------------------------------------------------------------
# the corpus's guards


class Guards:
    """The corpus's raised guards as arrays (`Blockers.guards`), weighted by how many builds raise
    each one."""

    def __init__(self, rows, carry, builds):
        self.rows = rows
        self.carry, self.builds = carry, builds
        w = np.array([r["w"] for r in rows], float)
        self.w = w / w.sum() if w.sum() else w
        self.cuts = np.array([[r["cuts"][el] for el in ELEMENTS] for r in rows], float).reshape(-1, len(ELEMENTS))
        self.rate = np.array([r["guard_rate"] for r in rows], float)
        self.type_cuts = np.array([[r["type_cuts"].get(str(k), r["type_cuts"].get(k, 0)) for k in (0, 1, 2)]
                                   for r in rows], float).reshape(-1, 3)
        self.level = np.array([r["guard_level"] for r in rows], int)

    @classmethod
    def from_blockers(cls, bl):
        cnt = collections.Counter(int(i) for i in bl.index if i >= 0)
        rows = [{"w": cnt[i], "name": g["name"], "cuts": {el: float(g["cuts"][el]) for el in ELEMENTS},
                 "guard_rate": float(g["guard_rate"]), "type_cuts": {str(k): float(v) for k, v in g["type_cuts"].items()},
                 "guard_level": int(g["guard_level_class"])}
                for i, g in enumerate(bl.guards) if cnt[i]]
        return cls(rows, float(bl.can.mean()) if bl.n else 0.0, bl.n)

    @classmethod
    def from_summary(cls, s):
        return cls(s["guards"], s["carry"], s["builds"])

    def pass_share(self, split, attribute=None, gcc=1.0):
        """The share of a hit the corpus's guards pass, mean over guards: per element
        (100 - extra x cut x (1 + guardRate / 100) x gcc) / 100 floored at 0
        (`er-mechanics-powerstance-guard.block_hit`'s `chip_fraction`), weighted by `split`
        ({element: share}). `attribute` 0/1/2 adds the guard's slash/strike/pierce cut to the
        physical one. A repel's chip rate is not applied."""
        tot = sum(split.values()) or 1.0
        out = np.zeros(len(self.w))
        for k, el in enumerate(ELEMENTS):
            s = split.get(el, 0.0) / tot
            if not s:
                continue
            extra = 1.0 + (self.type_cuts[:, attribute] / 100.0 if el == "physical" and attribute in (0, 1, 2) else 0.0)
            frac = (100.0 - extra * self.cuts[:, k] * (1.0 + self.rate / 100.0) * gcc) / 100.0
            out += s * np.maximum(0.0, frac)
        return float((self.w * out).sum())

    def stun(self, damage_level):
        """({reaction: share of guards}, mean skipped R1 frame) of a hit of `damage_level`."""
        g = G()
        shares = collections.Counter()
        for lv, w in zip(self.level, self.w):
            shares[g.guard_reaction(int(lv), int(damage_level))] += float(w)
        frames = sum(v * SKIPPED_STUN[k]["r1"] for k, v in shares.items())
        return {k: round(v, 3) for k, v in shares.items()}, round(frames, 1)

    def summary(self):
        return {"carry": round(self.carry, 4), "builds": self.builds, "guards": self.rows}


# ---------------------------------------------------------------------------------------------
# the perfect-blocked cast


def pb_case(opp, hits, windows, gate, D, poise):
    """The whiff model's outcome with every opponent perfect-blocking my first hit: zero
    i-frames (only the hits on the impact frame are taken out) and zero recovery (he acts on the
    impact frame). `hits` [(frame, poise, value, heal)] on the cast clock, a stopper with value 0.
    None for a cast with no value."""
    shim = SimpleNamespace(offs=np.array([-1e9]), reach=opp.reach, k=opp.k, t=opp.t, q=opp.q, m=opp.m,
                           w=np.asarray(opp.w, float) / float(np.sum(opp.w)))
    return W().whiff(shim, hits, windows, gate, D, poise, 0.0, roll=(0.0, 0.0))


def burst_share(status):
    """Share of `status` ({status: HP}) a perfect block keeps on the blocked hit."""
    tot = sum(status.values())
    return sum(v for k, v in status.items() if k not in DOT_STATUSES) / tot if tot else 1.0


def ash_mult(P, w, chip, score, srate, k):
    """(bmult, value-share kept in the perfect-blocked case) of one cast (module docstring)."""
    if not w or not w["full"]:
        return 1.0, 1.0
    full = w["full"]
    v_T = w["v_T"]
    base = score + srate
    if base <= 0:
        return 1.0, 1.0
    pb_val = score * (chip * v_T + w["late"] - w["punish_hp"]) / full + srate * (k * v_T + w["late"]) / full
    kept = max(0.0, pb_val / base)
    return 1.0 - P + P * kept, kept


class Model:
    """P and the perfect-blocked outcome; built from a `--json` summary or from the corpus."""

    def __init__(self, guards, opp=None, timing=None, use=PB_USE):
        self.guards = guards
        self._opp = opp
        self.timing = timing or Timing()
        self.use = use

    @property
    def opp(self):
        if self._opp is None:
            self._opp = W().AI.Opponent()
        return self._opp

    def p(self, h):
        return self.guards.carry * self.use * self.timing.success(h)

    def case(self, hits, windows, gate, D, poise):
        """`pb_case` plus `v_T` (value on the impact frame) and `n_T`."""
        w = pb_case(self.opp, hits, windows, gate, D, poise)
        if not w:
            return None
        T = min(h[0] for h in hits)
        w["v_T"] = sum(h[2] + h[3] for h in hits if h[0] <= T)
        w["n_T"] = sum(1 for h in hits if h[0] <= T and h[2] + h[3] > 0)
        return w

    def case_over(self, hits, windows, gate, parts, poise):
        """`case` over a duel's distances `parts` [(distance, credited time)] (`er-mechanics-ash-
        interrupt.reach_parts`): each number of the outcome is the mean over the distances the
        attack is thrown at, weighted by the duel's time at each. The attack is thrown only where it
        reaches, so a distance it does not reach is no part of it, and the result is per attack
        thrown, as the fixed-distance `case` is. None when it reaches nowhere."""
        got = [(w, self.case(hits, windows, gate, D, poise)) for D, w in parts if w > 0]
        got = [(w, c) for w, c in got if c]
        tot = sum(w for w, _ in got)
        if not tot:
            return None
        out = dict(got[0][1])
        for k, v in out.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool) and k not in ("v_T", "n_T", "full"):
                out[k] = sum(w * c[k] for w, c in got) / tot
        return out


# ---------------------------------------------------------------------------------------------
# ash rows (combo-loadout)


class AshSide:
    """Per sweep row: P, the perfect-blocked outcome and `bmult`, from its interrupt profile."""

    def __init__(self, model, stats):
        self.m = model
        self.stats = stats
        g = G()
        self.g = g
        self.reg = g.Tables()
        self.ARm = g._ar_module()
        self.art = self.ARm.Tables(None)
        self.t = AS().AshTables()
        self.ids = {}
        for sid in self.t.arts:
            self.ids.setdefault(self.t.arts_name(sid), sid)
        self._split, self._first = {}, {}

    def split(self, weapon, affinity, grip):
        """{element: AR} of the carrier at the build's stats, max level."""
        key = (weapon, affinity, grip)
        if key not in self._split:
            try:
                wid = self.art.find_weapon(weapon, affinity)
                lvl = self.art.max_level(self.art.weapons[wid]["reinforceTypeId"])
                r = self.ARm.attack_rating(self.art, weapon, affinity, lvl, self.stats, grip == "2H")
                self._split[key] = {el: r["damage"].get(el, {}).get("total", 0.0) for el in ELEMENTS}
            except (KeyError, SystemExit, ValueError, TypeError):
                self._split[key] = {"physical": 1.0}
        return self._split[key]

    def first_hit(self, ash, weapon, grip, stance=None):
        """{'atk_row', 'kind', 'wid'} of the cast's first hit, or None. `stance` is a stance row's
        button (Unsheathe's R1 slash and R2 slash are different attacks)."""
        key = (ash, weapon, grip, stance)
        if key not in self._first:
            out = None
            try:
                A = AS()
                sid = self.ids[ash]
                wid = self.t.find_weapon(weapon)
                prof = A.skill_profile(self.t, sid, wid, 0, "both" if grip == "2H" else "one", stance)
                acts = [x for x in prof["anims"].get(A.main_anim(prof), []) if x["kind"] in ("melee", "bullet")]
                if acts:
                    f = min(acts, key=lambda x: x["frames"][0])
                    out = {"atk_row": f.get("atk_row"), "kind": f["kind"], "wid": wid}
            except (KeyError, SystemExit, TypeError, ValueError, IndexError, AttributeError):
                out = None
            self._first[key] = out
        return self._first[key]

    def row(self, x, prof, score, srate):
        """Set `bmult` and `pblock` on sweep row `x` from its interrupt profile `prof`."""
        if not prof or "error" in prof or not prof.get("hits"):
            return
        lead = x.get("grip_change", 0.0) or 0.0
        hits = [tuple(h) for h in prof["hits"]]
        T = min(h[0] for h in hits)
        P = self.m.p(T + lead)
        fh = self.first_hit(x["ash"], x["weapon"], x["grip"], x.get("stance"))
        gcc, lvl = 1.0, None
        if fh and fh["atk_row"] is not None:
            hit = {"atk_row": fh["atk_row"], "weapon": fh["wid"]}
            gcc = self.g.guard_cut_cancel(self.reg, hit)
            lvl = self.g.guarded_damage_level(self.reg, hit)
        chip = self.m.guards.pass_share(self.split(x["weapon"], x["affinity"], x["grip"]), None, gcc)
        status = {**(x.get("status_hp") or {}), **(x.get("status_only") or {})}
        k = burst_share(status)
        parts = [q for q in prof.get("spread") or () if q.get("reach") and any(q.get("touch") or ())]
        if parts:
            # Over a duel's distances (`er-mechanics-ash-interrupt.Rows.spread_profile`): at each
            # distance the cast reaches, the hits that touch him there are the ones he can perfect
            # block and the punish closes from there. `bmult_by` per band for `set_by_distance`;
            # `bmult` is their mean weighted by the duel's time, the cast thrown only where it reaches.
            by, tot = [], 0.0
            for q in parts:
                hq = [h for h, t in zip(hits, q["touch"]) if t]
                Pq = self.m.p(min(h[0] for h in hq) + lead)
                wq = self.m.case(hq, prof.get("windows") or [], prof["gate"], q["D"], prof["poise"])
                bq, kq = ash_mult(Pq, wq, chip, score, srate, k)
                by.append((q["node"], q["w"], bq, kq, Pq, wq))
                tot += q["w"]
            bmult = sum(w_ * b for _, w_, b, *_ in by) / tot
            kept = sum(w_ * kq for _, w_, _, kq, *_ in by) / tot
            P = sum(w_ * Pq for _, w_, _, _, Pq, _ in by) / tot
            cases = [(w_, wq) for _, w_, _, _, _, wq in by if wq]
            ct = sum(w_ for w_, _ in cases)
            w = ({key: sum(w_ * c[key] for w_, c in cases) / ct for key in ("punish_hp", "stopped", "trade", "late", "full", "v_T")}
                 if ct else None)
            x["bmult_by"] = [[n, round(b, 4)] for n, _, b, *_ in by]
        else:
            w = self.m.case(hits, prof.get("windows") or [], prof["gate"], prof["D"], prof["poise"])
            bmult, kept = ash_mult(P, w, chip, score, srate, k)
        x["bmult"] = bmult
        info = {"P": round(P, 4), "T": round(T + lead, 1), "chip": round(chip, 3), "kept": round(kept, 3),
                "burst": round(k, 3)}
        if w:
            info.update({"punish_hp": round(w["punish_hp"], 1), "stopped": round(w["stopped"], 3),
                         "trade": round(w["trade"], 3), "late": round(w["late"], 1), "full": round(w["full"], 1),
                         "cost_hp": round(P * (w["full"] - chip * w["v_T"] - w["late"] + w["punish_hp"]), 2)})
        if lvl is not None:
            info["stun"], info["stun_skipped"] = self.m.guards.stun(lvl)
        x["pblock"] = info


# ---------------------------------------------------------------------------------------------
# weapons (class roles)


def ha_windows(hyper):
    """`weapon_attacks` hyperarmor entries as interrupt windows (start, end, menu poise bonus,
    PvP poise taken, floor, PvP HP taken)."""
    out = []
    for h in hyper or []:
        a, b = h["frames"]
        if b is None:
            continue
        out.append((a, b, (h.get("poise_bonus") or 0.0) * AS().POISE_MENU, h.get("pvp_poise_damage_taken", 1.0),
                    (h.get("floor_pct_of_max") or 0) / 100.0, h.get("pvp_hp_damage_taken", 1.0)))
    return sorted(out)


class WeaponSide:
    """Per weapon, grip and affinity: the HP per attack thrown the perfect blockers take off."""

    def __init__(self, model, poise, spread=None):
        self.m = model
        self.poise = poise
        #: A duel's distance spread (`er-mechanics-ash-interrupt.duel_spread`): each attack is
        #: costed over the distances its reach covers (`Model.case_over`), not at `REACH_SHARE`.
        self.spread = spread
        pm = PM()
        self.pm, self.ct = pm, pm._ct()
        self.g = G()
        self.reg = self.g.Tables()
        self._frames, self._case = {}, {}

    def frames(self, name, grip):
        """{slot: {'hits', 'windows', 'phys_type', 'atk_row', 'dmg_level', 'wid'}} of one weapon
        and grip ("1H", "2H", "L1"), slot keys without the `2h_` prefix."""
        key = (name, grip)
        if key in self._frames:
            return self._frames[key]
        out = {}
        try:
            wid = self.reg.find_weapon(name)
        except (KeyError, SystemExit):
            self._frames[key] = out
            return out
        if grip == "L1":
            try:
                rows = self.g.offhand_rows(self.reg, wid)
            except (KeyError, SystemExit, TypeError):
                rows = []
            ATK = self.pm.CR().ATTACKS
            for r in rows:
                atk = self.pm.offhand_attack(self.ct, r)
                if atk and atk["hits"]:
                    h0 = r["hits"][0] if r.get("hits") else {}
                    # Off-hand hyperarmor frames are clip frames: carried to real ones as the hits are.
                    cat, anim = (int(x) for x in r["anim"][1:].split("_"))
                    to_real = ATK.clip_to_real(ATK.resolve_events(cat, anim)[2] or [])
                    hyper = [dict(h, frames=tuple(ATK.real_frame(to_real(f / ATK.TAE_FPS)) for f in h["frames"]))
                             for h in r.get("hyperarmor") or [] if h["frames"][1] is not None]
                    out[r["slot"]] = {"hits": atk["hits"], "windows": ha_windows(hyper),
                                      "phys_type": None, "atk_row": h0.get("atk_row"), "wid": wid}
        else:
            try:
                rows = self.g.ATK.weapon_attacks(self.reg, wid, "both" if grip == "2H" else "one") or []
            except (KeyError, SystemExit, TypeError):
                rows = []
            for r in rows:
                slot = r["slot"].removeprefix("2h_")
                if self.pm.family_of(r["slot"]) not in FAMILIES:
                    continue
                try:
                    atk = self.pm.attack_of_row(self.ct, r)
                except (KeyError, SystemExit, TypeError, ValueError):
                    continue
                if atk["hits"]:
                    out[slot] = {"hits": atk["hits"], "windows": ha_windows(r.get("hyperarmor")),
                                 "phys_type": r.get("phys_type"), "atk_row": r.get("atk_row"), "wid": wid}
        for v in out.values():
            v["dmg_level"] = self.g.guarded_damage_level(self.reg, {"atk_row": v["atk_row"]}) \
                if v["atk_row"] is not None else None
        self._frames[key] = out
        return out

    def slot_case(self, name, grip, slot, fr, rec):
        """The unit-value perfect-blocked case of one slot (cached per weapon, grip and slot):
        every hit worth 1, the chain's next hit a stopper worth 0."""
        key = (name, grip, slot)
        if key not in self._case:
            s = rec["slots"][slot]
            hits = [(float(f), 0.0, 1.0, 0.0) for f in fr["hits"]]
            nxt = CHAIN_NEXT.get(slot)
            ns = rec["slots"].get(nxt) if nxt else None
            if ns and s.get("next_cancel") is not None and ns.get("startup") is not None:
                hits.append((float(s["next_cancel"] + ns["startup"]), 0.0, 0.0, 0.0))
            gate = s.get("roll_cancel") or max(f for f in fr["hits"])
            reach = s.get("reach") or 0.0
            if self.spread and reach:
                parts = [(d, w) for d, w, _ in W().AI.reach_parts(self.spread, reach)]
                self._case[key] = self.m.case_over(sorted(hits), fr["windows"], gate, parts, self.poise)
            else:
                self._case[key] = self.m.case(sorted(hits), fr["windows"], gate, REACH_SHARE * reach, self.poise)
        return self._case[key]

    def record_cost(self, rec):
        """{'cost', 'p', 'stopped', 'slots'} of one embedding record, or None."""
        name, grip = rec["weapon"], rec["grip"]
        fr_all = self.frames(name, grip)
        fams = collections.defaultdict(list)
        for slot, s in rec["slots"].items():
            fr = fr_all.get(slot)
            fam = "l1" if slot.startswith("left_") else self.pm.family_of(slot)
            if not fr or fam not in FAMILIES + ("l1",) or s.get("damage") is None:
                continue
            w = self.slot_case(name, grip, slot, fr, rec)
            if not w:
                continue
            n = len(fr["hits"])
            d = (s.get("damage") or 0.0) / n
            st = s.get("status_hp") or {}
            sv = sum(st.values()) / n
            v = d + sv
            P = self.m.p(fr["hits"][0])
            attr = self.g.PHYS_ATTRIBUTE.get(fr["phys_type"]) if fr["phys_type"] else None
            gcc = self.g.guard_cut_cancel(self.reg, {"atk_row": fr["atk_row"], "weapon": fr["wid"]})
            chip = self.m.guards.pass_share(s.get("attack") or rec.get("ar") or {"physical": 1.0}, attr, gcc)
            nT = w["n_T"]
            # Unit case: `late` counts delivered later hits, `full` the slot's hits.
            lost = n * v - nT * (chip * d + burst_share(st) * sv) - w["late"] * v + w["punish_hp"]
            info = {"p": round(P, 4), "chip": round(chip, 3), "stopped": round(w["stopped"], 3),
                    "punish_hp": round(w["punish_hp"], 1), "cost": round(P * lost, 2)}
            if fr["dmg_level"] is not None:
                info["stun_skipped"] = self.m.guards.stun(fr["dmg_level"])[1]
            fams[fam].append((slot, info))
        if not fams:
            return None
        cost = p = stopped = 0.0
        slots = {}
        for fam, xs in fams.items():
            for slot, info in xs:
                wt = 1.0 / len(fams) / len(xs)
                cost += wt * info["cost"]
                p += wt * info["p"]
                stopped += wt * info["stopped"]
                slots[slot] = info
        return {"cost": round(cost, 2), "p": round(p, 4), "stopped": round(stopped, 3), "slots": slots}

    def weapons(self, emb, log=None):
        """{weapon: {grip: cost at the best-damage affinity, 'by_affinity': {aff: {grip: cost}}}}
        over the embedding's wieldable records."""
        log = log or (lambda m: print(m, file=sys.stderr, flush=True))
        by = collections.defaultdict(list)
        for r in emb["weapons"]:
            if r["wieldable"] and r.get("kind", "weapon") == "weapon":
                by[r["weapon"]].append(r)
        out = {}
        for k, (name, recs) in enumerate(sorted(by.items())):
            ent = {"by_affinity": {}}
            best = {}
            for r in recs:
                try:
                    c = self.record_cost(r)
                except (KeyError, SystemExit, TypeError, ValueError, ZeroDivisionError) as e:
                    log(f"perfect block: {name} {r['affinity']} {r['grip']}: {e!r}")
                    continue
                if not c:
                    continue
                ent["by_affinity"].setdefault(r["affinity"], {})[r["grip"]] = {k_: c[k_] for k_ in ("cost", "p", "stopped")}
                if r["grip"] not in best or (r.get("damage") or 0) > best[r["grip"]][0]:
                    best[r["grip"]] = ((r.get("damage") or 0), dict(c, affinity=r["affinity"]))
            for g, (_, c) in best.items():
                ent[g] = c
            out[name] = ent
            if k % 50 == 0:
                log(f"perfect block weapons: {k} of {len(by)}")
        return out


# ---------------------------------------------------------------------------------------------
# CLI


def corpus_guards(mirror=None, rl=None):
    g = G()
    sr = PM().SR()
    rl = rl or sr.RL_WINDOW
    builds = g.blocker_corpus(mirror, *rl)
    bl = g.Blockers(g.Tables(), builds)
    return Guards.from_blockers(bl), bl


def build_poise(build):
    """My armor poise in menu units (`er-mechanics-ash-interrupt.Rows.armor_poise`)."""
    AI = W().AI
    stats = {k: build["stats"][k] for k in ("str", "dex", "int", "fth", "arc")}
    armor = PM().SR().worn(build)[0] or ["Verdigris Gauntlets", "Verdigris Armor", "Verdigris Greaves"]
    tab = AI._sib("er-mechanics-ash-table").Table("Dagger", "Keen", stats, armor, True)
    return round(tab.armor * 1000)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--build", type=Path, help="planner build (armor poise for the weapon side)")
    ap.add_argument("--weapons-from", type=Path,
                    help="er-mechanics-weapon-embed.py output: cost every wieldable record in it")
    ap.add_argument("--mirror", type=Path)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--arena-m", type=float,
                    help="a duel's starting separation, m: each weapon attack is costed over the duel's "
                         "distance spread where its reach covers (`er-mechanics-ash-interrupt.duel_spread`)")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    guards, bl = corpus_guards(a.mirror)
    m = Model(guards)
    tm = m.timing
    print(f"corpus: {guards.builds} builds, carry (raises a guard) {guards.carry:.3f}, {len(guards.rows)} guards; "
          f"attempt {PB_USE}; window {PB_WINDOW_FRAMES} frame; react share {tm.rs}, sd {tm.sd:.2f} f, cv {tm.cv}")
    print("success by first-hit frame: " + ", ".join(f"f{h} {tm.success(h):.3f}" for h in (8, 12, 16, 20, 30, 45, 60)))
    print("pass share, physical {:.3f}, magic {:.3f}, fire {:.3f}, lightning {:.3f}, holy {:.3f}".format(
        *(guards.pass_share({el: 1.0}) for el in ELEMENTS)))
    summ = {**guards.summary(), "attempt": PB_USE, "window": PB_WINDOW_FRAMES, "react_share": tm.rs,
            "timing_sd_frames": tm.sd, "timing_cv": tm.cv, "b_hit_hp": W().AI.B_HIT_HP,
            "skipped_stun": SKIPPED_STUN, "dot_statuses": sorted(DOT_STATUSES),
            "kinds": dict(collections.Counter(bl.kind).most_common())}
    out = {"summary": summ}
    if a.weapons_from:
        if not a.build:
            ap.error("--weapons-from needs --build (armor poise)")
        poise = build_poise(json.loads(a.build.read_text()))
        print(f"my armor poise {poise}")
        AI = W().AI
        spread = AI.duel_spread_for(a.arena_m) if a.arena_m else None
        if spread:
            print("duel spread: " + ", ".join(f"{n[0]:g} m x{n[1]:.3f}" for n in spread["nodes"]))
        ws = WeaponSide(m, poise, spread)
        out["weapons"] = ws.weapons(json.loads(a.weapons_from.read_text()))
        top = sorted(((v["1H"]["cost"], n) for n, v in out["weapons"].items() if v.get("1H")), reverse=True)
        print("highest 1H perfect-block cost per attack: " + ", ".join(f"{n} {c:.1f}" for c, n in top[:8]))
        print("lowest: " + ", ".join(f"{n} {c:.1f}" for c, n in top[-8:]))
    if a.json:
        a.json.write_text(json.dumps(out, indent=1, default=float))
    return 0


def selftest() -> int:
    fails = []

    def check(label, ok, got=None):
        print(f"{'ok' if ok else 'FAIL'}  {label}" + (f": {got!r}" if got is not None else ""))
        if not ok:
            fails.append(label)

    # Timing: one reaction at 10 frames, a tiny fixed spread.
    tm = Timing(react_share=1.0, delays=[(10.0, 1.0)], sd_frames=0.5, cv=0.10)
    s20, s60 = tm.success(20), tm.success(60)
    check("success falls with the telegraph (scalar timing)", s20 > s60 > 0, (round(s20, 3), round(s60, 3)))
    check("a hit before the reaction delay is never reacted to", tm.success(8) == 0.0, tm.success(8))
    rd = Timing(react_share=0.0, delays=[(10.0, 1.0)], sd_frames=0.5, cv=0.10)
    check("a read press lands in 1 of h frames", abs(rd.success(20) - 1 / 20) < 1e-9, rd.success(20))
    flat = Timing(react_share=1.0, delays=[(5.0, 1.0)], sd_frames=1.0, cv=0.0)
    check("no cv: same success early and late", abs(flat.success(20) - flat.success(60)) < 1e-9)
    check("a 1-frame window under 1-frame sd catches under a half", 0 < flat.success(20) < 0.5, flat.success(20))

    # Guards: one shield, 100 physical cut, 50 magic, no guard rate.
    gd = Guards([{"w": 1, "cuts": {"physical": 100, "magic": 50, "fire": 60, "lightning": 60, "holy": 60},
                  "guard_rate": 0, "type_cuts": {"0": 0, "1": 0, "2": 0}, "guard_level": 1}], 0.8, 10)
    check("a 100% physical cut passes nothing", gd.pass_share({"physical": 1.0}) == 0.0)
    check("half physical, half magic passes a quarter", abs(gd.pass_share({"physical": 1, "magic": 1}) - 0.25) < 1e-9)
    check("guard-cut cancel -50 halves the cut", abs(gd.pass_share({"magic": 1.0}, None, 0.5) - 0.75) < 1e-9)
    check("guard level 1, damage level 2: a middle stun, R1 at f10 skipped", gd.stun(2) == ({"middle": 1.0}, 10.0),
          gd.stun(2))
    check("skipped stuns as measured", (SKIPPED_STUN["small"]["r1"], SKIPPED_STUN["middle"]["guard"],
                                        SKIPPED_STUN["large"]["roll"], SKIPPED_STUN["break"]["len"]) == (5, 32, 47, 70))

    # The punish: B's string hits 15 and 30 frames after he acts, D inside his reach.
    class Fake:
        offs = np.array([0.0])
        w = np.array([1.0])
        reach = np.array([3.0])
        k = 7.5
        t = np.array([[15.0, 30.0]])
        q = np.array([[60.0, 60.0]])
        m = np.array([[True, True]])

    hp = W().AI.B_HIT_HP
    m = Model(gd, opp=Fake(), timing=tm)
    one = m.case([(20.0, 50.0, 100.0, 0.0)], [], 100.0, 2.5, 40.0)
    check("a single hit is punished by the whole string before the gate",
          abs(one["punish_hp"] - 2 * hp) < 1e-9 and one["late"] == 0.0 and one["v_T"] == 100.0)
    two = m.case([(20.0, 50.0, 100.0, 0.0), (30.0, 50.0, 80.0, 0.0)], [], 100.0, 2.5, 40.0)
    check("a second hit before his first punish hit (f35) stops it and lands",
          two["punish_hp"] == 0.0 and abs(two["late"] - 80.0) < 1e-9 and two["stopped"] == 1.0)
    slow = m.case([(20.0, 50.0, 100.0, 0.0), (40.0, 50.0, 80.0, 0.0)], [], 100.0, 2.5, 40.0)
    check("a second hit after it does not", slow["punish_hp"] > 0 and slow["late"] == 0.0)
    stop = m.case([(20.0, 50.0, 100.0, 0.0), (30.0, 0.0, 0.0, 0.0)], [], 100.0, 2.5, 40.0)
    check("a chain stopper worth 0 protects without adding damage",
          stop["punish_hp"] == 0.0 and stop["late"] == 0.0 and stop["full"] == 100.0)
    short = m.case([(20.0, 50.0, 100.0, 0.0)], [], 40.0, 2.5, 40.0)
    check("a gate at f40 leaves his first hit (f35) and not his second (f50)", short["punish_hp"] == hp)
    ha = m.case([(20.0, 50.0, 100.0, 0.0), (40.0, 50.0, 80.0, 0.0)], [(30.0, 45.0, 100.0, 1.0, 0.0, 1.0)],
                100.0, 2.5, 40.0)
    check("hyperarmor at his first hit trades: my later hit still lands", ha["trade"] == 1.0 and ha["late"] == 80.0)
    # Over a duel's distances (`case_over`): from 6 m his run-in delays the punish past my second
    # hit at f40, which stops it; at 2.5 m it does not. The mix weighs each by the duel's time
    # there, and a distance the attack is not thrown at (weight 0) is no part of it.
    far = m.case([(20.0, 50.0, 100.0, 0.0), (40.0, 50.0, 80.0, 0.0)], [], 100.0, 6.0, 40.0)
    mix = m.case_over([(20.0, 50.0, 100.0, 0.0), (40.0, 50.0, 80.0, 0.0)], [], 100.0,
                      [(2.5, 0.3), (6.0, 0.1), (9.0, 0.0)], 40.0)
    check("over the duel's distances: the punish closes from each, weighted by the time there",
          far["stopped"] > slow["stopped"]
          and abs(mix["stopped"] - (0.3 * slow["stopped"] + 0.1 * far["stopped"]) / 0.4) < 1e-9
          and abs(mix["punish_hp"] - (0.3 * slow["punish_hp"] + 0.1 * far["punish_hp"]) / 0.4) < 1e-9
          and mix["v_T"] == slow["v_T"] and m.case_over([(20.0, 50.0, 100.0, 0.0)], [], 100.0, [], 40.0) is None,
          (round(slow["stopped"], 3), round(far["stopped"], 3), round(mix["stopped"], 3)))

    check("burst kept, damage over time dropped", (burst_share({"bleed": 100}), burst_share({"poison": 50}),
                                                    burst_share({"bleed": 30, "poison": 10}), burst_share({}))
          == (1.0, 0.0, 0.75, 1.0))
    b, kept = ash_mult(0.0, one, 0.3, 100.0, 0.0, 1.0)
    check("P 0 leaves the value alone", b == 1.0)
    b, kept = ash_mult(0.05, one, 0.3, 100.0, 0.0, 1.0)
    check("a punished single hit keeps nothing", kept == 0.0 and abs(b - 0.95) < 1e-9, (b, kept))
    b, kept = ash_mult(0.05, two, 0.5, 180.0, 0.0, 1.0)
    check("a protected two-hit cast keeps chip plus its second hit", abs(kept - (0.5 * 100 + 80) / 180) < 1e-9, kept)
    b, kept = ash_mult(0.05, two, 0.5, 180.0, 90.0, 0.0)
    check("status on the blocked hit: a poison cast loses it",
          abs(kept - (180 * (50 + 80) / 180 + 90 * 80 / 180) / 270) < 1e-9, kept)

    # Regulation and corpus (skipped when the game files or the mirror are absent).
    try:
        g = G()
        check("guard reaction table row 1 (`FUN_140686f00`)", g.GUARD_REACTION_TABLE[1][:5] == (0, 1, 3, 3, 4))
        guards, _ = corpus_guards()
        # 0.335 on the 2026-10-10 mirror: a bare left hand, a left melee weapon or catalyst and a
        # powerstance cannot guard one-handed (`GUARD_LEFT_ONE_HAND`).
        check("corpus: a share of builds raises a guard", 0.1 < guards.carry < 1.0, round(guards.carry, 3))
        check("corpus: the mean guard passes less physical than lightning",
              guards.pass_share({"physical": 1}) < guards.pass_share({"lightning": 1}),
              (round(guards.pass_share({"physical": 1}), 3), round(guards.pass_share({"lightning": 1}), 3)))
    except (FileNotFoundError, OSError, SystemExit) as exc:
        print(f"skip corpus checks: {type(exc).__name__}: {exc}")
    print(f"{len(fails)} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
