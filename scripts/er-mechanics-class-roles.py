#!/usr/bin/env python3
"""Pick weapons per weapon class: best scaling, best reach/moveset, and the fun ones.

    python3 scripts/er-mechanics-class-roles.py <build.json> --embed <embed.json> --list-wieldable
    python3 scripts/er-mechanics-class-roles.py <build.json> --embed <embed.json> --out <roles.json>

`<build.json>` is the planner build document (`GET /inventories/<id>`); it only says which
weapons are the build's own (`in_build`, `order`). `<embed.json>` is
`er-mechanics-weapon-embed.py` output at the build's stat block, and every feature below is read
from its records rather than measured here (docs/er-mechanics/weapon-embedding.md). A weapon is
`wieldable` in a grip when some affinity's vector for that grip is (no damage type with negative
scaling, two-handing's STR x1.5 included); only wieldable weapons take the scaling and reach roles.
`--list-wieldable` prints them comma-separated, for `er-mechanics-ash-table.py --extra-weapons ...
--only-extra`.

Features per weapon, each the better of its wieldable right-hand grips (1H, 2H):

- `ar` (named for its `[class_pick_weights]` key): the vector's `damage`, one motion-value-100 hit
  against the fixed 60 VIG RL150 defender, best affinity and grip; a greasable row (Standard,
  Heavy, Keen, Quality) counts with its best DLC drawstring grease (`damage_greased`), which an
  elemental infusion such as Lightning cannot take;
- `r1_reach`, `r1_recovery`, `r1_chain_dps`, `stamina`: the vector summaries `r1_reach`,
  `r1_recovery`, `r1_chain_dps` and `stamina_per_r1`;
- `r1_startup`: R1 #1's first hit frame (its slot's `startup`), the soonest wieldable grip, scored
  by `r1_cover`, its punish coverage (`PunishCoverage`): the share of the PvP corpus's openings a
  hit that fast lands in. It replaced the straight-line best / value score (user, 2026-10-18:
  "implement the speed weighting"), which gave a 16-frame R1 0.56 of a 9-frame one's credit
  where it punishes 0.20 of the openings against 0.58 (0.34 of it);
- `ash_value`: the best non-utility ash vector's `value` on that weapon at its best-damage
  affinity (a fixed-skill weapon's is its own skill);
- `crit`: `ar` times the critical multiplier (100 + `throwAtkRate`) / 100, so Misericorde
  competes in its class on ripostes the way Shamshir does on R1 startup (user, 2026-10-08);
- `parry_cost` (with `--parry`): the riposte HP per attack thrown that the corpus's parriers are
  expected to land (`er-mechanics-parry.py`), the cheaper wieldable grip; a cost, subtracted as
  weight x cost / the class's best `ar` rather than normalised (`PENALTIES`), so with the `ar`
  weight an HP of riposte taken counts as an HP of hit damage. The off hand's `l1_parry_cost` is
  the same over the L1 chain, against the class's best L1 #1 hit.
- `pb_cost` (with `--perfect-block`): the HP per attack thrown the corpus's perfect blockers are
  expected to take off (`er-mechanics-perfect-block.py`: chip only on the blocked hit, its
  damage-over-time status dropped, their punish unless a later hit or the chain's next attack
  lands first), at the carried affinity, the cheaper wieldable grip; a cost scored as
  `parry_cost` is. `l1_pb_cost` is the same over the L1 chain.
- `feint_hp`: the HP per attack thrown that delaying the held R2 lands on a reacting defender who
  rolls (`FeintModel`: the share of his reaction timings the delay beats, from the release window,
  the roll's i-frames and the reaction plus network delays); a gain on the `ar` scale (`BONUSES`).
  `r2_feint` and `r2_delay` report the share and the window.
- `r1_<family>_startup`, `_recovery`, `_damage` for the running, rolling, backstep and crouch R1
  (`R1_MOVE_SLOTS`), weighted as the off hand's L1 families are (`main_weights`); the rolling and
  backstep R1 trade after their entry clip, the roll's 13 i-frames included and the backstep's
  none (TAE: its only invincibility needs the Fine Crucible Feather).

Each feature is normalised to the class's best wieldable weapon (lower-is-better ones as
best / value) and weighted by `[class_pick_weights]` in `data/ash-preferences.toml`. Roles:

1. scaling: the highest `ar` (damage).
2. reach: the highest weighted score with `ar` left out (weights renormalised).
3. fun: per mechanic whose game-wide share is at most the embedding's `fun_max_share` (a rare
   status, a rare scaling route, a skill with a damaging hit few options can carry), the class's
   wieldable vector carrying it that does the most (weapon mechanics by `damage`, skills by ash
   `value`), rarest first. A class may have none or several. A fun pick only has to be wieldable.

4. main_hand and offhand: every wieldable weapon is scored twice, once per hand, and each hand
   over every affinity that carries a status (Blood bleed, Cold frost, Poison poison, and an
   innate status such as scarlet rot, sleep or madness on whichever affinity keeps it) plus the
   weapon's best statusless affinity by hit damage (user, 2026-10-08: "score each weapon twice,
   once as a main hand weapon with every status as well as 2H...and so on. Not just axes, and not
   just bleed"). Main hand takes the features above from that affinity's 1H and 2H records, the
   better grip each, status HP per hit inside `ar` as `hit_damage` counts it, and keeps each
   grip's own features beside them as `by_grip` (`main_hand_entry`), which the equip sets score a
   one-handed or two-handed set on (`er-mechanics-combo-loadout.hand_scores`). Off hand takes
   the left-hand L1 chain (`OFFHAND_FEATURES`, weighted by `[offhand_pick_weights]` through
   `offhand_weights`): L1 #1's first hit frame, recovery, reach, stamina, and damage plus status
   HP per second over the chain, and the L1 thrown out of a run, roll, backstep and crouch
   (`MOVE_FAMILIES`: first hit frame, recovery and damage each), which with a plain off hand is
   L1 #1 itself; `l1_status` (mean status HP per L1 hit) is reported, and its weight is 0 by default
   because the chain's HP per second already counts it. The
   records are the embedding's own: it carries every affinity a weapon can take, and the R1
   and L1 frames do not change with the affinity, only the attack rating and the build-up do.
   Normalisation is over the class's (weapon, affinity) candidates for that hand. Each class
   gets `main_hand` and `offhand` as {weapon, affinity, score}; each weapon gets the same two
   keys with `by_affinity` scores.

Near-twins are not merged: Shamshir and Scimitar each keep whatever role they win. When a class's
scaling and reach picks are near-twins (`moveset_distance`: right-hand slot sets
`TWIN_SLOT_SHARE` alike, slot features within `TWIN_MAX_DIFF`), `twins.near` says so, and
`er-mechanics-combo-loadout.py --collapse-twins` gives the class one row. A weapon can
hold two roles. Writes {"weapons": {name: {...}}, "classes": {class: {...}}} and prints each
class's top 3 by weighted score with every feature, for auditing the weights.
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import re
import sys
import tomllib
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
PREFS = HERE.parent / "data" / "ash-preferences.toml"
#: The L1 attacks thrown out of movement, scored per family beside the L1 string (user, 2026-10-19:
#: value is moveset-dependent, standing R1 is not the only animation). They are the L1 side of
#: `er-mechanics-parry.MOVE_SLOTS` (running, rolling, backstep and crouch R1). With a powerstance
#: pair they are `dual_dash`, `dual_roll`, `dual_bstep` and `dual_crouch`
#: (`er-mechanics-powerstance-guard.DUAL_SLOTS`). With a plain off hand they are L1 #1: the
#: compiled `c0000.hks` `ExecAttack` loads `W_AttackLeftHeavy1` into the register it plays for
#: `ATTACK_REQUEST_LEFT_HEAVY` once (hks lines 1324 and 1475-1478) and never reassigns it, while
#: only `ATTACK_REQUEST_DUAL_RIGHT` swaps in a dash, rolling, backstep or stealth state (lines
#: 1637-1648); the left-hand state machine has no movement states (`W_AttackLeft*` is the
#: Light/Heavy 1..6 chain and `W_AttackLeftReversal`). The jump L1 is left out: `dual_jump` has
#: no off-hand counterpart this script can name, so neither side is scored on it.
MOVE_FAMILIES = ("dash", "roll", "bstep", "crouch")
#: Per movement family: first hit frame, frames from its last hit to the roll cancel, damage plus
#: status HP of the one attack.
MOVE_FEATURES = tuple(f"l1_{fam}_{x}" for fam in MOVE_FAMILIES for x in ("startup", "recovery", "damage"))
#: The main hand's attacks thrown out of movement, the R1 side of `er-mechanics-parry.MOVE_SLOTS`,
#: scored per family beside the R1 string as the off hand's L1 families are. A weapon with no crouch
#: attack plays its rolling R1 for the crouch press (`c0000.hks` `ExecAttack` lines 1406-1408, with
#: `IsUseStealthAttack` false; `er-mechanics-timing-mixup` item 1). The rolling and backstep R1 are
#: valued after their entry clip, i-frames included (`er-mechanics-ash-interrupt.entry_step`).
R1_MOVE_SLOTS = {"dash": "run_r1", "roll": "roll_r1", "bstep": "bstep_r1", "crouch": "crouch_r1"}
R1_MOVE_FEATURES = tuple(f"r1_{fam}_{x}" for fam in MOVE_FAMILIES for x in ("startup", "recovery", "damage"))
#: Main-hand role features. `feint_hp` is a bonus in HP per attack thrown (`BONUSES`, `FeintModel`).
FEATURES = ("r1_recovery", "r1_reach", "r1_startup", "ar", "r1_chain_dps", "ash_value", "stamina", "crit",
            "parry_cost", "pb_cost", "feint_hp") + R1_MOVE_FEATURES
#: Off-hand role features, read off the L1 record's left-hand chain (or a powerstance record's
#: dual chain, `powerstance_features`).
OFFHAND_FEATURES = ("l1_startup", "l1_recovery", "l1_reach", "l1_chain_dps", "l1_stamina", "l1_status",
                    "l1_parry_cost", "l1_pb_cost") + MOVE_FEATURES
#: Off-hand features no powerstance record measures: the parry and perfect-block sweeps
#: (`er-mechanics-parry.py`, `er-mechanics-perfect-block.py`) cost the 1H, 2H and L1 grips only.
#: A comparison that includes a powerstance pair leaves them out on every side rather than
#: inventing a value (`er-mechanics-combo-loadout.derive_combos`). Reach left this list on
#: 2026-10-19, when the embedding began measuring each dual attack's reach.
POWERSTANCE_UNMEASURED = ("l1_parry_cost", "l1_pb_cost")
LOWER_BETTER = ({"r1_recovery", "r1_startup", "stamina", "l1_startup", "l1_recovery", "l1_stamina"}
                | {f for f in MOVE_FEATURES + R1_MOVE_FEATURES if not f.endswith("_damage")})
#: Costs in HP per attack, subtracted rather than normalised: weight x cost / the pool's best value
#: of the paired HP feature, so with the paired feature's weight an HP of cost counts as an HP of
#: damage lost (`er-mechanics-parry.py` `weapons`: the riposte the corpus's parriers are expected
#: to land per attack I throw; user, 2026-10-10: "Model parrying"; `er-mechanics-perfect-block.py`
#: `weapons`: what the corpus's perfect blockers take off per attack, user 2026-10-10). The weight
#: sits outside the normalising total, so a pool with no costs scores as before.
PENALTIES = {"parry_cost": "ar", "l1_parry_cost": "l1_hit", "pb_cost": "ar", "l1_pb_cost": "l1_hit"}
#: Gains in HP per attack, added the way `PENALTIES` subtract but with the paired feature's own
#: weight, so an HP a feint lands counts as an HP of hit damage (`FeintModel`) and no new weight
#: exists. Scored only when the paired feature is.
BONUSES = {"feint_hp": "ar"}
LEFT_CHAIN = ("left_1", "left_2", "left_3", "left_4", "left_5", "left_6")
#: Scaling pick: a weapon at most `SCALING_TIE` below the class's best hit damage that reaches
#: `SCALING_REACH_GAIN` farther on R1 #1 is picked instead (user, 2026-10-08: 0.8% less damage
#: for 18% more reach, "maybe 5% further is enough"; then 2.5%, after Wakizashi's 2.6% more R1
#: reach than Bloodstained Dagger fell short: "We can change our rule from 5% to 2.5% minimum
#: then"). The main-hand and off-hand picks apply the same rule to their score and their own
#: reach (`hand_pick`).
SCALING_TIE = 0.02
SCALING_REACH_GAIN = 0.025
#: Weapons with a true combo (measured R1/R2 links from `--combos`, or listed in `TRUE_COMBOS`)
#: get this added to every pick score -- more than the spread of most classes, so a combo weapon
#: wins its class's moveset pick (user, 2026-10-08: "any known true combos should add a massive
#: weight to a weapons choseability").
TRUE_COMBO_BONUS = 0.25
TRUE_COMBOS = HERE.parent / "data" / "true-combos.toml"
RIGHT_GRIPS = ("1H", "2H")
#: The public RL 125 planner mirror (`er-builds-scrape.py --rl 125`): the corpus whose openings
#: `PunishCoverage` measures, filtered as `er-mechanics-parry.Corpus` filters its own.
PUNISH_MIRROR = Path.home() / ".cache" / "er-build-planner" / "builds-rl125.jsonl"


def ascii_name(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def best(xs, lower=False):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    return min(xs) if lower else max(xs)


#: Slot features compared for near-twins: timing, cost, motion value and reach (damage and status
#: are left out; they follow the affinity, not the moveset).
TWIN_FEATURES = ("anim", "startup", "active", "last_hit", "roll_cancel", "next_cancel", "stamina",
                 "mv", "hits", "reach")
#: Near-twins: the two weapons' right-hand slot sets agree on at least this share, and the shared
#: slots' features differ by at most this mean relative difference (Shamshir vs Scimitar: same
#: slots, 0.4%, reach alone differing by 3.6 vs 3.45 m on R1 #1).
TWIN_SLOT_SHARE = 0.9
TWIN_MAX_DIFF = 0.05


def hit_damage(r):
    """A vector's one motion-value-100 hit, greased when its row takes a grease (the build carries
    the DLC drawstring greases, and an elemental or status infusion cannot take one)."""
    # Plus the HP its own status is worth per hit (`status_hp`, the linear proc model), averaged
    # over every attack slot -- R1s, R2s, running, rolling, jumping: Spiked Caestus did 4 less
    # than Caestus and lost the class with bleed counted nowhere (user, 2026-10-08: "barely less
    # damage/scaling but apply bleed and are otherwise identical").
    per_hit = [sum(s["status_hp"].values()) / s["hits"]
               for s in (r.get("slots") or {}).values() if s.get("status_hp") and s.get("hits")]
    status = sum(per_hit) / len(per_hit) if per_hit else 0.0
    return max(r.get("damage") or 0.0, r.get("damage_greased") or 0.0) + status


def _parry():
    """The `er-mechanics-parry.py` module (its corpus filter, attack families and helper loaders)."""
    key = "er_mechanics_parry"
    mod = sys.modules.get(key)
    if mod is None:
        spec = importlib.util.spec_from_file_location(key, HERE / "er-mechanics-parry.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        spec.loader.exec_module(mod)
    return mod


class PunishCoverage:
    """`cover(startup)`: the share of the PvP corpus's openings a hit `startup` real frames out
    lands in before the opponent can roll (user, 2026-10-18: "Do we consider how our build leans in
    terms of attack animation duration?").

    Openings: every corpus build's right-hand weapon (`er-mechanics-powerstance-guard.blocker_corpus`
    over the RL window `er-mechanics-parry.Corpus` uses, the active set's slot 0), at the build's
    grip, its attacks in the families `er-mechanics-parry.family_of` names (R1, R2, moving,
    jumping; the L2 skill has no roll cancel in the embedding), weighted as `Corpus.weights`
    weighs them: builds equal, families equal within a build, attacks equal within a family.
    Frames are the embedding's (`--embed`), which do not change with the affinity; names match
    with accents stripped (the planner's "Miséricorde").

    Per opening, two windows on its own clock: `W = roll_cancel - last_hit`, the frames between its
    last hit and its roll cancel, and `V = roll_cancel - startup`, the same from its first hit.
    A punish is either committed before the cue (it lands when `startup <= W`) or a reaction to a
    whiff seen at the first hit (it lands when `d + startup <= V`, `d` over
    `er-mechanics-ashes.reaction_delays`), mixed by `REACT_SHARE` the way `er-mechanics-parry`
    mixes a read press with a reacting one:

        cover(s) = sum_a w_a [(1 - REACT_SHARE) 1{W_a >= s} + REACT_SHARE sum_d p_d 1{V_a >= s + d}]

    Measured on the RL 125 mirror and the heavy-kit embedding (2026-10-18; 481 builds, 7297
    openings): 0.52 at 10 frames, 0.42 at 12, 0.25 at 15, 0.14 at 18, 0.07 at 20. Range and spacing
    are not modelled (`INFERRED`: the punisher is in reach)."""

    def __init__(self, embed_doc, mirror=None, builds=None, react_share=None, delays=None):
        import numpy as np
        P = _parry()
        A = P.AS()
        self.np = np
        self.rs = A.REACT_SHARE if react_share is None else react_share
        d = A.reaction_delays() if delays is None else delays
        self.d = np.array([x for x, _ in d], float)
        self.p = np.array([w for _, w in d], float)
        if builds is None:
            builds = P.G().blocker_corpus(str(mirror or PUNISH_MIRROR), *P.SR().RL_WINDOW)
        frames = {}
        for r in embed_doc["weapons"]:
            if r["grip"] in RIGHT_GRIPS and r.get("slots"):
                frames.setdefault((ascii_name(r["weapon"]), r["grip"]), r["slots"])
        rows, self.n_builds, self.missing = [], 0, collections.Counter()
        for b in builds:
            slot = P.G()._active_slots(b).get(0)
            if not slot:
                continue
            name = ascii_name(slot["name"])
            slots = frames.get((name, "2H" if b.get("is2h") else "1H")) or frames.get((name, "1H"))
            if not slots:
                self.missing[name] += 1
                continue
            fams = collections.defaultdict(list)
            for k, s in slots.items():
                fam = P.family_of(k)
                if fam in (None, "l1") or any(s.get(f) is None for f in ("startup", "last_hit", "roll_cancel")):
                    continue
                fams[fam].append((s["roll_cancel"] - s["last_hit"], s["roll_cancel"] - s["startup"]))
            if not fams:
                continue
            self.n_builds += 1
            for xs in fams.values():
                rows += [(w, v, 1.0 / len(fams) / len(xs)) for w, v in xs]
        a = np.array(rows, float).reshape(-1, 3)
        tot = a[:, 2].sum()
        self.W, self.V, self.w = a[:, 0], a[:, 1], (a[:, 2] / tot if tot else a[:, 2])
        self._c = {}

    def __call__(self, startup):
        if startup is None:
            return 0.0
        if startup not in self._c:
            np = self.np
            read = float(self.w[self.W >= startup].sum())
            react = float((self.w[:, None] * (self.V[:, None] >= startup + self.d[None, :]) * self.p[None, :]).sum())
            self._c[startup] = (1.0 - self.rs) * read + self.rs * react
        return self._c[startup]


def punish_k():
    """The equip-set speed term's weight `k` (`er-mechanics-combo-loadout._set_value`: a set's value
    times 1 + k x its `PunishCoverage`). `INFERRED`: the share of the opponent's attacks I answer by
    waiting and rolling, the split `er-mechanics-ashes.REACT_SHARE` (0.5) already sets for the
    opponent answering mine (`er-mechanics-ash-whiff.DODGE_SHARE`); each answer the set's fastest
    attack covers adds one hit of the set's own, so its hits per exchange grow by that share times
    the coverage. No corpus measures how often openings are whiffed or blocked, so `k` is that
    existing split rather than a new number."""
    return _parry().AS().REACT_SHARE


#: Held R2 presses: the uncharged slot and its charged form (`er-mechanics-weapon-embed` `hold`).
FEINT_SLOTS = (("r2_1", "r2_1c"), ("r2_2", "r2_2c"))


class FeintModel:
    """`model(slots)`: the feint a weapon grip's held R2 is, as {'feint_hp', 'r2_feint',
    'r2_delay'}, or {} when it has no held R2.

    What a feint is here. Every weapon slot's `feint` window (`er-mechanics-weapon-embed.
    feint_window`: a TAE event 300 cancel into R1, R2, roll or guard before the first hit) is 0 in
    the embedding (88045 slot records of the heavy-kit RL 125 embedding, 2026-10-19, as its
    2026-10-08 note found over 2314), so no weapon attack can be cancelled before it lands, and a
    weapon's only feint is its R2 hold: the release, and so the hit, can come at any frame from the
    earliest release to the charged swing. From the press (the cue, the Start clip's first frame)
    the hit lands at `t0 = hold[0] + startup` (the earliest release, `release_lead_in`, plus the
    End clip's first hit) up to `t1` = the charged slot's first hit; `r2_delay` = `t1 - t0`. The
    release is taken to be possible at any frame between (`INFERRED`: the embedding keeps the
    release window's start, not its end).

    The defender's reply is the medium roll (`er-mechanics-interrupt.iframes`: i-frames f0-13,
    TAE), pressed no earlier than his reaction (`er-mechanics-ashes.reaction_delays`: the human
    reaction plus both `NETWORK_ONE_WAY_S` legs, nine quantiles). Per quantile `d`:

    - against the R2 released at once (hit at `t0`, the attacker who never delays) he evades when
      `d <= t0`;
    - against a release uniform over the delay window (the attacker's minimax mix against a
      single covering roll, `INFERRED`) he evades when he can react to the release itself
      (`d <= startup`, the End clip's first hit), else only by guessing: one roll covers at most
      `min(lo + I, t1) - lo` of the window, `lo = max(d, t0)`, `I` the i-frames.

    `r2_feint` = the share of reactions evaded against the plain R2 but not the delayed one, net
    of the slow reactions the delay hands time to, floored at 0 (the attacker then never delays).
    Strafes and backsteps evade by distance, not timing, and pre-rolling readers are not credited
    (`INFERRED`: the term counts the timing mix-up alone). `feint_hp` = `r2_feint` x
    `REACT_SHARE` (the reacting share of defenders) x the share of my attacks that are R2
    presses (one family of `er-mechanics-parry.FAMILIES`, `INFERRED` there) x the R2's damage
    plus status HP: HP per attack thrown, scored as a bonus on the `ar` scale (`BONUSES`)."""

    def __init__(self, iframes: float, delays, react_share: float, r2_share: float):
        self.I, self.delays = float(iframes), list(delays)
        self.react_share, self.r2_share = float(react_share), float(r2_share)

    @classmethod
    def from_game(cls) -> "FeintModel":
        P, ai = _parry(), _ai()
        AS = P.AS()
        return cls(ai.INTR.iframes(ai.INTR.ROLL_ANIM), AS.reaction_delays(), AS.REACT_SHARE, 1.0 / len(P.FAMILIES))

    def beat(self, t0: float, t1: float, release_to_hit: float) -> float:
        """Share of reactions the delayed R2 beats beyond the plain one (class docstring)."""
        H = t1 - t0
        if H <= 0:
            return 0.0
        tot = 0.0
        for d, w in self.delays:
            plain = 1.0 if d <= t0 else 0.0
            if d <= release_to_hit:
                held = 1.0
            else:
                lo = max(d, t0)
                held = max(0.0, min(lo + self.I, t1) - lo) / H
            tot += w * (plain - held)
        return max(0.0, tot)

    def __call__(self, slots: dict) -> dict:
        got = []
        for k, kc in FEINT_SLOTS:
            s, c = slots.get(k) or {}, slots.get(kc) or {}
            hold = s.get("hold")
            if not hold or hold[0] is None or s.get("startup") is None or c.get("startup") is None:
                continue
            t0, t1 = hold[0] + s["startup"], float(c["startup"])
            got.append((self.beat(t0, t1, s["startup"]), max(0.0, t1 - t0), _slot_hp(s)))
        if not got:
            return {}
        p = sum(g[0] for g in got) / len(got)
        hp = sum(g[0] * g[2] for g in got) / len(got)
        return {"feint_hp": round(self.r2_share * self.react_share * hp, 2), "r2_feint": round(p, 4),
                "r2_delay": round(max(g[1] for g in got), 1)}


def _ai():
    """`er-mechanics-ash-interrupt.py`, whose `ChainTrades` values the R1, L1 and dual strings."""
    key = "er_mechanics_ash_interrupt"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, HERE / "er-mechanics-ash-interrupt.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        spec.loader.exec_module(mod)
    return sys.modules[key]


def _progress():
    """`er-mechanics-progress.py`: a loop's `  i/N <unit>` lines, the counter the pipeline's bar reads."""
    key = "er_mechanics_progress"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, HERE / "er-mechanics-progress.py")
        sys.modules[key] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[key])
    return sys.modules[key]


#: RL either side of the build's own that the opponent pool is drawn from: the window every
#: `er-mechanics-exchange.opponent_pool` caller in these scripts uses (`--window 10`).
POOL_RL_WINDOW = 10


def armor_poise(build_doc) -> float | None:
    """My armor poise in menu units from the planner build's worn protectors: the sum of their
    `EquipParamProtector.toughnessCorrectRate` x 1000, the rule `er-mechanics-ash-interrupt.Rows`
    gives the ash rows. None when it wears none."""
    worn = [s["name"] for p in ("arms", "body", "legs", "head")
            for s in ((build_doc.get("protectors") or {}).get(p) or {}).get("slots", []) if s.get("equipSet")]
    if not worn:
        return None
    AT = _ai()._sib("er-mechanics-ash-table")
    return float(round(AT.Table._armor(None, worn) * 1000))


def build_rl(build_doc) -> int:
    """The planner build's rune level: its `stats.rl`, else the stat sum less 79 (every class's
    stats sum to its level + 79)."""
    st = build_doc.get("stats") or {}
    return st.get("rl") or (sum(v for k, v in st.items() if k != "rl") - 79)


def chain_trades(build_doc, rl: int | None = None, arena_m: float | None = None):
    """An `er-mechanics-ash-interrupt.ChainTrades` for the build: my armor poise (`armor_poise`,
    else the pool's median), the exchange pool `POOL_RL_WINDOW` either side of its RL. `arena_m`
    (a duel) values each string over the duel's distance spread, credited only where it reaches,
    as the loadout values the ashes."""
    if rl is None:
        rl = build_rl(build_doc)
    return _ai().ChainTrades.for_pool(armor_poise(build_doc), (rl - POOL_RL_WINDOW, rl + POOL_RL_WINDOW),
                                      arena_m=arena_m)


def _trade(trades, chain, weapon, grip, reach):
    """`trades.trade` of the slots of `chain` that exist, or None without `trades` or slots."""
    chain = [s for s in chain if s]
    if trades is None or not chain:
        return None
    return trades.trade(chain, weapon, grip, reach)


def right_moves(sl, entry=None) -> dict:
    """{family: (slot, chain)} of a right-hand record's movement attacks (`R1_MOVE_SLOTS`; the crouch
    press falls back to the rolling R1, without the roll): `chain` is what `ChainTrades.trade` values, the entry step
    (`er-mechanics-ash-interrupt.entry_step`, its frames and i-frames) before a rolling or backstep
    R1. `entry` stands in for `entry_step`. A family the weapon lacks is left out."""
    out = {}
    for fam, key in R1_MOVE_SLOTS.items():
        s = sl.get(key) or (sl.get("roll_r1") if fam == "crouch" else None)
        if not s or s.get("startup") is None:
            continue
        entry = entry or _ai().entry_step
        # A crouch press that plays the rolling R1 plays it from the held crouch, with no roll in
        # front of it (`er-mechanics-timing-mixup` item 1), so only the slot's own key has an entry.
        step = entry(key) if sl.get(key) else None
        out[fam] = (s, [step, s] if step else [s])
    return out


def trade_items(emb, grip_ok=lambda r: True):
    """Every string the features value (`right_features`, `offhand_features`,
    `powerstance_trades`) as `ChainTrades.prepare` items [(chain, weapon, grip, reach)], so one
    parallel pass simulates them all."""
    out = []
    for r in emb["weapons"]:
        sl = r.get("slots") or {}
        if not r.get("wieldable") or not grip_ok(r):
            continue
        if r["grip"] in RIGHT_GRIPS:
            out.append(([sl[k] for k in R1_CHAIN if k in sl], r["weapon"], r["grip"],
                        (sl.get("r1_1") or {}).get("reach")))
            out += [(chain, r["weapon"], r["grip"], s.get("reach")) for s, chain in right_moves(sl).values()]
        elif r["grip"] == "L1":
            chain = [sl[k] for k in LEFT_CHAIN if k in sl]
            reach = (r.get("summary") or {}).get("first_reach")
            out += [(chain, r["weapon"], "1H", reach), (chain[:1], r["weapon"], "1H", reach)]
    for p in emb.get("powerstance") or ():
        sl = p.get("slots") or {}
        chain = [sl[k] for k in DUAL_CHAIN if k in sl]
        if p.get("wieldable") and chain:
            out.append((chain, p["right"], "1H", chain[0].get("reach")))
            out += [([s], p["right"], "1H", s.get("reach")) for s in _dual_moves(sl).values() if s]
    return [x for x in out if x[0]]


def _traded(x, t):
    """`x` (damage or motion value per second) times the string's interrupt multiplier `t['mult']`:
    what of it is still delivered net of the HP taken when the pool answers it."""
    return round(x * t["mult"], 1) if x is not None and t else x


def pb_grip_cost(pb, affinity, grip):
    """The `er-mechanics-perfect-block.py` cost of one weapon at `affinity` and `grip`, falling back
    to its best-damage affinity's; None without one."""
    c = ((pb or {}).get("by_affinity", {}).get(affinity) or {}).get(grip) or (pb or {}).get(grip)
    return c.get("cost") if c else None


def _move_features(per_rec, trades, entry=None) -> dict:
    """`r1_<family>_startup`, `_recovery` and `_damage` (`R1_MOVE_FEATURES`) over the grips of
    `per_rec` ({grip: record}), each the better grip: first hit frame, frames from its last hit to
    the roll cancel, and its damage plus status HP times its `ChainTrades` multiplier when `trades`
    is given (`right_moves`: the entry and its i-frames before a rolling or backstep R1)."""
    got = {}
    for g, r in per_rec.items():
        for fam, (s, chain) in right_moves(r["slots"], entry).items():
            t = _trade(trades, chain, r.get("weapon"), g, s.get("reach"))
            got.setdefault(fam, []).append((s["startup"], s.get("roll_after_hit"),
                                            _traded(round(_slot_hp(s), 1), t)))
    out = {}
    for fam in MOVE_FAMILIES:
        xs = got.get(fam) or []
        out[f"r1_{fam}_startup"] = best((x[0] for x in xs), lower=True)
        out[f"r1_{fam}_recovery"] = best((x[1] for x in xs), lower=True)
        out[f"r1_{fam}_damage"] = best(x[2] for x in xs)
    return out


def right_features(ok, av, parry=None, pb=None, cover=None, trades=None, feint=None, entry=None):
    """The main-hand features of one weapon's wieldable right-hand records `ok` (any affinities;
    the frame features do not depend on it), `av` its ash value, `parry` its
    `er-mechanics-parry.py` costs per grip ({'1H': {'cost': ...}, ...}) or None, `pb` its
    `er-mechanics-perfect-block.py` entry or None, `cover` a `PunishCoverage` (adds `r1_cover`),
    `trades` an `er-mechanics-ash-interrupt.ChainTrades` (the R1 string's interrupt multiplier,
    `r1_trade`, scales `r1_chain_dps`, and each movement R1's its damage), `feint` a `FeintModel`
    (adds `feint_hp`, `r2_feint`, `r2_delay` from the grip whose held R2 feints for the most HP).
    The movement R1s (`R1_MOVE_FEATURES`) are `_move_features`; `entry` stands in for
    `er-mechanics-ash-interrupt.entry_step`."""
    top = max(ok, key=hit_damage, default=None)
    startup = best(((r["slots"].get("r1_1") or {}).get("startup") for r in ok), lower=True)
    per_grip, per_rec = {}, {}
    for r in ok:
        per_grip.setdefault(r["grip"], r["summary"])
        per_rec.setdefault(r["grip"], r)
    costs = [((parry or {}).get(g) or {}).get("cost") for g in per_grip]
    pbs = [pb_grip_cost(pb, top["affinity"], g) for g in per_grip] if top else []
    # The R1 string per grip against the pool: poise per hit, the string's hyperarmor and its hit
    # size decide which side's hits land (`er-mechanics-ash-interrupt.ChainTrades`).
    tr = {g: _trade(trades, [r["slots"].get(k) for k in R1_CHAIN], r.get("weapon"), g,
                    (r["slots"].get("r1_1") or {}).get("reach")) for g, r in per_rec.items()}
    dps = {g: _traded(s.get("r1_chain_dps"), tr.get(g)) for g, s in per_grip.items()}
    gbest = max((g for g in dps if dps[g] is not None), key=lambda g: dps[g], default=None)
    fe = max((feint(r["slots"]) for r in per_rec.values()), key=lambda f: f.get("feint_hp", -1.0),
             default={}) if feint is not None else {}
    return top, {
        **fe, **_move_features(per_rec, trades, entry),
        # The interrupt multiplier of the R1 string of the grip `r1_chain_dps` takes (None without
        # `trades` or per-hit poise in the embedding), and that string's largest hit's PvP poise.
        **({"r1_trade": tr[gbest]["mult"], "r1_poise_hit": tr[gbest]["poise_hit"]}
           if gbest and tr.get(gbest) else {}),
        # What the corpus's perfect blockers take off per attack at the carried affinity, the
        # cheapest wieldable grip, as `parry_cost`.
        "pb_cost": best((c for c in pbs if c is not None), lower=True),
        # The riposte HP per attack the corpus's parriers land, the cheapest wieldable grip
        # (every other feature also takes its better grip).
        "parry_cost": best((c for c in costs if c is not None), lower=True),
        "ar": hit_damage(top) if top else None,
        "r1_reach": best(s.get("r1_reach") for s in per_grip.values()),
        "r1_recovery": best((s.get("r1_recovery") for s in per_grip.values()), lower=True),
        # Frame R1 #1 first hits (its slot's `startup`), the grip that lands soonest.
        "r1_startup": startup,
        # The share of the corpus's openings that R1 #1 punishes (`PunishCoverage`), which
        # `norm_score` scores `r1_startup` by.
        **({"r1_cover": round(cover(startup), 4)} if cover is not None and startup is not None else {}),
        # Motion value per second over the R1 string, times its interrupt multiplier (`r1_trade`)
        # when `trades` is given: the share still delivered net of the HP the pool's answers take.
        "r1_chain_dps": dps.get(gbest) if gbest else None,
        # Stamina per 100 HP the R1 string deals, not per press: a swing that lands two hits (a
        # paired weapon two-handed) pays for both of them (`stamina_per_hp`).
        "stamina": best((stamina_per_hp(s.get("stamina_per_r1"),
                                        [per_rec[g]["slots"].get(k) for k in R1_CHAIN])
                         for g, s in per_grip.items()), lower=True),
        "ash_value": round(av) if av else None,
        # Riposte and backstab damage: hit damage times the weapon's critical multiplier
        # (`throwAtkRate`, Misericorde +40, Executioner's Greataxe +15).
        # Only for a weapon whose critical multiplier is above the normal one; for the rest it
        # would only repeat `ar`, so `score` gives its weight to `r1_reach` instead (user,
        # 2026-10-08: "Crit should only be used if the crit value is above the normal value,
        # otherwise range should fill the gap").
        "crit": (round(hit_damage(top) * (100 + top["crit"]) / 100, 1)
                 if top and top.get("crit", 0) > 0 else None),
    }


#: `er-mechanics-true-combo-sweep.py` grip names (`er-mechanics-frame-advantage.py --grip`) as the
#: embedding's.
SWEEP_GRIPS = {"one": "1H", "both": "2H"}


def main_hand_entry(mine, av, parry=None, pb=None, cover=None, trades=None, feint=None, entry=None):
    """One role affinity's main-hand entry: `features`, each the better of the wieldable grips in
    `mine` (what the class picks score), and `by_grip` {grip: features}, `right_features` over that
    grip's records alone. A set plays one grip at a time (user, 2026-10-19: two-handed, L1 no longer
    throws the left weapon), so `er-mechanics-combo-loadout.hand_scores` scores each mode on its own
    grip's numbers, not on a 2H AR beside a 1H startup."""
    out = {"features": right_features(mine, av, parry, pb, cover, trades, feint, entry)[1], "by_grip": {}}
    for g in RIGHT_GRIPS:
        recs = [r for r in mine if r["grip"] == g]
        if recs:
            out["by_grip"][g] = right_features(recs, av, parry, pb, cover, trades, feint, entry)[1]
    return out


def mark_true_combo(mh, grips):
    """Set `true_combo` on a main-hand entry (`main_hand_entry`): 1 on the best-of features when any
    grip has a measured true combo, and on each `by_grip` features when that grip has one (`grips`,
    the grips whose same-weapon links the sweep measured true)."""
    mh["features"]["true_combo"] = 1 if grips else 0
    for g, f in (mh.get("by_grip") or {}).items():
        f["true_combo"] = 1 if g in grips else 0


def offhand_features(rec, parry=None, pb=None, trades=None):
    """The off-hand features of one wieldable L1 record, or None when it has no L1 chain. The
    chain is timed as `er-mechanics-weapon-embed.chain_summary` times it (each next press at the
    previous step's next-attack cancel), with each step's status HP added to its damage.
    `l1_hit` (L1 #1's damage plus status HP) is the scale of `l1_parry_cost`, the L1 chain's
    parry cost from `parry` (`er-mechanics-parry.py`, per grip), and of `l1_pb_cost`, its
    perfect-block cost from `pb` (`er-mechanics-perfect-block.py`). The movement families
    (`MOVE_FAMILIES`) are L1 #1 itself, the attack the game plays for an L1 out of movement with a
    plain off hand. With `trades` (`er-mechanics-ash-interrupt.ChainTrades`) the L1 string's
    damage per second and each movement L1's damage are what is delivered when the pool answers
    them (`_left_features`)."""
    chain = [rec["slots"][k] for k in LEFT_CHAIN if k in rec["slots"]]
    if not chain or chain[0].get("startup") is None:
        return None
    reach = rec["summary"].get("first_reach")
    tr = {"chain": _trade(trades, chain, rec.get("weapon"), "1H", reach)}
    tr.update(dict.fromkeys(MOVE_FAMILIES, _trade(trades, chain[:1], rec.get("weapon"), "1H", reach)))
    return {**_left_features(chain, rec["summary"], {fam: chain[0] for fam in MOVE_FAMILIES}, tr),
            "l1_reach": reach,
            "l1_parry_cost": ((parry or {}).get("L1") or {}).get("cost"),
            "l1_pb_cost": pb_grip_cost(pb, rec["affinity"], "L1")}


#: Powerstance chain slots, in press order (`er-mechanics-powerstance-guard.DUAL_SLOTS`).
DUAL_CHAIN = ("dual_1", "dual_2", "dual_3", "dual_4", "dual_5", "dual_6")


def powerstance_key(rec) -> str:
    """The key `powerstance_trades` files a powerstance record's multipliers under."""
    return "|".join((rec["right"], rec.get("right_affinity") or "", rec["left"], rec.get("left_affinity") or ""))


def _dual_moves(sl):
    return {"dash": sl.get("dual_dash"), "roll": sl.get("dual_roll"), "bstep": sl.get("dual_bstep"),
            "crouch": sl.get("dual_crouch") or sl.get("dual_roll")}


def powerstance_trades(rec, trades) -> dict | None:
    """{'chain': trade, family: trade} of one powerstance record's dual string and each movement
    family's dual attack (`er-mechanics-ash-interrupt.ChainTrades.trade`, the right weapon's 1H
    front contact, each attack's own reach), or None when the record has no dual chain."""
    sl = rec.get("slots") or {}
    chain = [sl[k] for k in DUAL_CHAIN if k in sl]
    if not chain or trades is None:
        return None
    out = {"chain": _trade(trades, chain, rec["right"], "1H", chain[0].get("reach"))}
    for fam, s in _dual_moves(sl).items():
        out[fam] = _trade(trades, [s], rec["right"], "1H", (s or {}).get("reach"))
    return out


def powerstance_features(rec, tr=None):
    """The off-hand features of one powerstance record (`er-mechanics-weapon-embed` `powerstance`:
    the right and left weapons at their recorded affinities, both hands' hits counted), on the
    same keys and timing as `offhand_features`, or None when it has no dual chain. The movement
    families are its `dual_dash`, `dual_roll`, `dual_bstep` and `dual_crouch` (a crouch the
    behaviour script refuses plays the rolling L1, `er-mechanics-grip-modes.dual_crouch`). The
    block costs are left out of any comparison with it (`POWERSTANCE_UNMEASURED`). `tr` is its
    `powerstance_trades`, which scales the string's and each movement attack's damage as
    `offhand_features` scales a plain off hand's."""
    sl = rec.get("slots") or {}
    chain = [sl[k] for k in DUAL_CHAIN if k in sl]
    if not chain or chain[0].get("startup") is None:
        return None
    moves = _dual_moves(sl)
    return {**_left_features(chain, rec.get("summary") or {}, moves, tr),
            # The first dual attack's reach, each hand's hits on its own weapon
            # (`er-mechanics-weapon-embed.Embedder.dual_reach`); None on an embedding made
            # before 2026-10-19.
            "l1_reach": chain[0].get("reach"), "l1_parry_cost": None, "l1_pb_cost": None}


def _slot_hp(s):
    return (s.get("damage") or 0.0) + sum((s.get("status_hp") or {}).values())


#: R1 string slots, in press order.
R1_CHAIN = ("r1_1", "r1_2", "r1_3", "r1_4", "r1_5", "r1_6")


def stamina_per_hp(per_press, chain):
    """Stamina per 100 HP dealt: `per_press` (mean stamina a press of the string costs) over the
    mean HP a press deals (`_slot_hp` of each slot of `chain` that exists). Measured 2026-10-19 on
    the heavy kit: per press, a Broadsword pair's dual swing (24 stamina, 465.7 HP) scored below a
    Bloodhound Claw's L1 (8 stamina) on stamina alone by 0.073, though it deals both hands' hits."""
    slots = [s for s in chain if s]
    hp = sum(_slot_hp(s) for s in slots) / len(slots) if slots else 0.0
    if per_press is None or hp <= 0:
        return None
    return round(100.0 * per_press / hp, 3)


def _left_features(chain, sm, moves, tr=None):
    """The L1 string's features (`chain`, timed as `er-mechanics-weapon-embed.chain_summary` times
    it, `sm` its summary) and each movement family's (`moves`, {family: slot or None}). `tr`
    ({'chain': trade, family: trade}, `er-mechanics-ash-interrupt.ChainTrades.trade`) scales the
    string's HP per second and each family's damage by its interrupt multiplier, and puts the
    string's in `l1_trade`."""
    tr = tr or {}
    t, end, hp = 0.0, 0.0, 0.0
    for i, s in enumerate(chain):
        end = t + s["startup"]
        hp += _slot_hp(s)
        if i + 1 < len(chain):
            t += s.get("next_cancel") or 0
    hits = sum(s.get("hits") or 1 for s in chain)
    out = {"l1_startup": chain[0]["startup"], "l1_recovery": sm.get("recovery"),
           "l1_chain_dps": (_traded(round(hp / (end / 30.0), 1), tr.get("chain"))
                            if len(chain) > 1 and end > 0 else None),
           **({"l1_trade": tr["chain"]["mult"], "l1_poise_hit": tr["chain"]["poise_hit"]} if tr.get("chain") else {}),
           # Per 100 HP dealt, as the main hand's `stamina` (`stamina_per_hp`).
           "l1_stamina": stamina_per_hp(sm.get("stamina"), chain),
           "l1_status": round(sum(sum((s.get("status_hp") or {}).values()) for s in chain) / hits, 1),
           "l1_hit": round(_slot_hp(chain[0]), 1)}
    for fam in MOVE_FAMILIES:
        s = moves.get(fam) or {}
        out[f"l1_{fam}_startup"] = s.get("startup")
        out[f"l1_{fam}_recovery"] = s.get("roll_after_hit")
        out[f"l1_{fam}_damage"] = (_traded(round(_slot_hp(s), 1), tr.get(fam))
                                   if s.get("startup") is not None else None)
    return out


#: The string feature each movement feature shares its weight with.
MOVE_ANALOGUE = {"startup": "l1_startup", "recovery": "l1_recovery", "damage": "l1_chain_dps"}
#: The off hand's two attack families: the L1 string and the L1 out of movement.
L1_FAMILIES = ("string", "move")


def offhand_weights(base):
    """`[offhand_pick_weights]` spread over the movement families (`MOVE_FAMILIES`). The string and
    the movement attacks are two attack families with equal shares, attacks equal within a family:
    the split `er-mechanics-parry.FAMILIES` and `Corpus.weights` already give the R1 string and the
    `move` family (`INFERRED` there, and no new number here). So `l1_startup`, `l1_recovery` and
    `l1_chain_dps` keep half their weight, and each movement family's startup, recovery and damage
    take a quarter of the other half of it; the total is unchanged, so scores keep their scale.
    Stamina, status, reach and the block costs stay on the string alone."""
    w = dict(base)
    share = 1.0 / len(L1_FAMILIES)
    for x, f in MOVE_ANALOGUE.items():
        b = base.get(f, 0.0)
        w[f] = b * share
        for fam in MOVE_FAMILIES:
            w[f"l1_{fam}_{x}"] = b * (1.0 - share) / len(MOVE_FAMILIES)
    return w


#: The separate reach features per hand: what `spread_weights` takes off when the strings are
#: valued over the duel's distance spread.
REACH_FEATURES = ("r1_reach", "l1_reach")


def spread_weights(weights, spread) -> dict:
    """`weights` with the reach features (`REACH_FEATURES`) at 0 when the strings are valued over
    the duel's distance spread (`spread`, `ChainTrades.spread`, set by `--arena-m`), else
    `weights` unchanged. Over the spread a string is credited only in the distance bands its reach
    covers (`er-mechanics-ash-interrupt.reach_parts`), so `r1_trade` already carries reach into
    `r1_chain_dps` and the movement R1s, and `l1_trade` into the L1's; a separate `r1_reach` term
    would count it twice. The ash side takes `reach_w` off its spread rows for the same reason
    (`er-mechanics-combo-loadout.spread_proxy`). At a fixed distance (an invasion, no `--arena-m`)
    the trades do not see reach, so the weights stay. `norm_score` then also drops the crit gap's
    reach fallback, which reads its reach off the `r1_reach` weight."""
    if not spread:
        return weights
    return {**weights, **{f: 0.0 for f in REACH_FEATURES if f in weights}}


class PoolScales:
    """The extremes `norm_score` divides by, read off `pool` once per feature: (least, greatest) of
    its truthy values, None where it has none. A caller that scores many features dicts against
    one pool passes one of these (`scales`) instead of having every call walk the pool again: the
    loadout's off-hand pool holds about 26,000 powerstance records, and walking it per candidate
    was half the loadout's main process (py-spy, 2026-10-20). The pool must not change while it is
    in use."""

    def __init__(self, pool):
        self.pool, self._ext = pool, {}

    def ext(self, g):
        if g not in self._ext:
            vals = [y for y in (p.get(g) for p in self.pool) if y]
            self._ext[g] = (min(vals), max(vals)) if vals else None
        return self._ext[g]


def norm_score(feats, pool, use, weights, scales=None):
    """Weighted score of `feats` with each feature normalised to the best of `pool` (lower-is-better
    ones as best / value), less each `PENALTIES` cost, plus each `BONUSES` gain. `scales`, a
    `PoolScales` of `pool`, reads the pool's extremes once for many calls. A weapon with no
    crit bonus has the crit weight score reach instead, while reach is a scored feature: with the
    `r1_reach` weight at 0 (`spread_weights`, the duel spread credits reach inside the strings)
    the crit weight leaves that weapon's total, so reach is not scored there under another name.
    `r1_startup` is scored by `r1_cover` (`PunishCoverage`, higher
    better) over the pool's best when the features carry it, so the frames count as the openings
    they punish rather than in a straight line; features written before it fall back to
    best / value."""
    ext = (scales or PoolScales(pool)).ext
    reach_fill = weights.get("r1_reach", 0.0) > 0
    gap = "crit" in use and feats.get("crit") is None and not reach_fill
    tot = sum(weights.get(f, 0.0) for f in use
              if f not in PENALTIES and f not in BONUSES and not (gap and f == "crit"))
    s = 0.0
    for f in use:
        if gap and f == "crit":
            continue
        if f in PENALTIES:
            x = feats.get(f)
            scale = ext(PENALTIES[f])
            if x and scale:
                s -= weights.get(f, 0.0) * x / scale[1]
            continue
        if f in BONUSES:
            x = feats.get(f)
            scale = ext(BONUSES[f])
            if x and scale and BONUSES[f] in use:
                s += weights.get(BONUSES[f], 0.0) * x / scale[1]
            continue
        g = "r1_reach" if f == "crit" and feats.get("crit") is None else f
        if g == "r1_startup" and "r1_cover" in feats:
            g = "r1_cover"
        x = feats.get(g)
        vals = ext(g)
        if x and vals:
            s += weights.get(f, 0.0) * ((vals[0] / x) if g in LOWER_BETTER else (x / vals[1]))
    return s / tot if tot else 0.0


def role_affinities(rs):
    """The affinities a weapon is scored on per hand: each one with a status, and the best
    statusless one by right-hand hit damage (the build's physical or elemental default)."""
    by = collections.defaultdict(list)
    for r in rs:
        by[r["affinity"]].append(r)
    status = [aff for aff, xs in by.items() if any(r["status"] for r in xs)]
    plain = [(max((hit_damage(r) for r in xs if r["grip"] in RIGHT_GRIPS and r["wieldable"]), default=-1), aff)
             for aff, xs in by.items() if aff not in status]
    plain = [p for p in plain if p[0] >= 0]
    return status + ([max(plain)[1]] if plain else []), by


def moveset_distance(ra, rb):
    """(share of slots both have, mean relative feature difference over them) between two weapons'
    right-hand vectors, {grip: record} each; the slots are the embedding's per-slot features."""
    both = union = 0
    diffs = []
    for g in RIGHT_GRIPS:
        sa, sb = (ra.get(g) or {}).get("slots", {}), (rb.get(g) or {}).get("slots", {})
        both += len(sa.keys() & sb.keys())
        union += len(sa.keys() | sb.keys())
        for k in sa.keys() & sb.keys():
            for f in TWIN_FEATURES:
                x, y = sa[k].get(f), sb[k].get(f)
                if x is None and y is None:
                    continue
                if isinstance(x, str) or isinstance(y, str) or x is None or y is None:
                    diffs.append(0.0 if x == y else 1.0)
                else:
                    diffs.append(abs(x - y) / max(abs(x), abs(y), 1.0))
    return (both / union if union else 0.0), (sum(diffs) / len(diffs) if diffs else 1.0)


#: The skills that parry, generated from the game's clips (`er-duel-rules-gen.py` `PARRY_SKILLS`).
DUEL_TABLES = HERE.parent / "crates" / "er-duel-rules-core" / "src" / "tables.rs"


def parry_skills() -> set[str]:
    src = DUEL_TABLES.read_text()
    i = src.index("pub const PARRY_SKILLS")
    return set(re.findall(r'name: "((?:[^"\\]|\\.)*)"', src[i:src.index("\n];", i)]))


def parry_share(excluded_ashes, skills=None) -> float:
    """The share of the game's parry skills the ruleset leaves legal: 1.0 with no bans, 0.0 on the
    duel ladder, which bans them all. A riposte needs a parry, so the crit weight scales with it
    (user, 2026-10-18: "our rule for crit value needs to be derived by how many parry
    implementations there are in the ruleset")."""
    skills = parry_skills() if skills is None else set(skills)
    return len(skills - set(excluded_ashes)) / len(skills) if skills else 1.0


def main_weights(base) -> dict:
    """`base` main-hand weights spread over the movement R1s as `offhand_weights` spreads the
    off hand's: the R1 string and the movement attacks are two attack families with equal shares
    (`er-mechanics-parry.FAMILIES` and `Corpus.weights`, `INFERRED` there), so `r1_startup`,
    `r1_recovery` and `r1_chain_dps` keep half their weight and each movement family's startup,
    recovery and damage take a quarter of the other half; the total is unchanged."""
    w = dict(base)
    share = 1.0 / len(L1_FAMILIES)
    for x, f in R1_MOVE_ANALOGUE.items():
        b = base.get(f, 0.0)
        w[f] = b * share
        for fam in MOVE_FAMILIES:
            w[f"r1_{fam}_{x}"] = b * (1.0 - share) / len(MOVE_FAMILIES)
    return w


#: The R1 string feature each main-hand movement feature shares its weight with.
R1_MOVE_ANALOGUE = {"startup": "r1_startup", "recovery": "r1_recovery", "damage": "r1_chain_dps"}


def pick_weights(excluded_ashes=(), skills=None) -> dict:
    """`[class_pick_weights]` with `crit` times `parry_share`, spread over the movement R1s
    (`main_weights`)."""
    w = dict(tomllib.loads(PREFS.read_text())["class_pick_weights"])
    w["crit"] = w.get("crit", 0.0) * parry_share(excluded_ashes, skills)
    return main_weights(w)


def selftest() -> int:
    sk = {"Parry", "Golden Parry", "Storm Wall", "Buckler Parry"}
    ok = True
    # `PunishCoverage`: two corpus builds, equal. Misericorde (spelled as the planner spells it)
    # has an R1 (W 10, V 12) and an R2 (W 18, V 20), a quarter each; Club's R1 (W 4, V 5) is half.
    # One reaction delay of 4 frames, half the punishes reactions.
    def bld(name):
        return {"sets": {"weapons": [{"active": True}]}, "inventory": {"slots": [{"name": name, "equipSet": [0]}]}}
    emb = {"weapons": [
        {"weapon": "Misericorde", "grip": "1H", "slots": {
            "r1_1": {"startup": 10, "last_hit": 12, "roll_cancel": 22},
            "r2_1": {"startup": 20, "last_hit": 22, "roll_cancel": 40}}},
        {"weapon": "Club", "grip": "1H", "slots": {"r1_1": {"startup": 15, "last_hit": 16, "roll_cancel": 20},
                                                   "left_1": {"startup": 9, "last_hit": 9, "roll_cancel": 99}}}]}
    pc = PunishCoverage(emb, builds=[bld("Miséricorde"), bld("Club"), bld("Nope")], react_share=0.5,
                        delays=[(4.0, 1.0)])
    rows = [((pc.n_builds, dict(pc.missing)), (2, {"Nope": 1}), "coverage: accents stripped, an unknown weapon counted"),
            ([pc(4), pc(10), pc(19), pc(None)], [0.75, 0.375, 0.0, 0.0],
             "coverage: read share of W >= s plus reaction share of V >= s + d, L1 left out")]
    pool = [{"r1_startup": 10, "r1_cover": 0.4}, {"r1_startup": 15, "r1_cover": 0.1}]
    rows += [(norm_score(pool[1], pool, ("r1_startup",), {"r1_startup": 1.0}), 0.25,
              "norm_score: r1_startup scored by coverage over the pool's best"),
             (round(norm_score({"r1_startup": 15}, [{"r1_startup": 10}, {"r1_startup": 15}], ("r1_startup",),
                               {"r1_startup": 1.0}), 4), 0.6667, "norm_score: no r1_cover, best / value as before"),
             (right_features([{"slots": {"r1_1": {"startup": 10}}, "summary": {}, "grip": "1H", "damage": 1.0,
                               "affinity": "Standard"}], None, cover=pc)[1]["r1_cover"], 0.375,
              "right_features: r1_cover from the soonest R1 #1")]
    # Off hand: a plain L1 (two steps) throws L1 #1 out of movement; a powerstance record its own
    # dual_dash/roll/bstep, a refused crouch its rolling L1, and no reach.
    def sl(su, rah, dmg, nxt=10, status=None):
        return {"startup": su, "roll_after_hit": rah, "damage": dmg, "next_cancel": nxt, "hits": 1, "stamina": 10,
                **({"status_hp": status} if status else {})}
    l1 = {"affinity": "Heavy", "summary": {"recovery": 6.0, "stamina": 10.0, "first_reach": 2.0},
          "slots": {"left_1": sl(10, 5, 100.0, status={"bleed": 20.0}), "left_2": sl(12, 7, 100.0)}}
    of = offhand_features(l1)
    ps = {"summary": {"recovery": 8.0, "stamina": 18.0},
          "slots": {"dual_1": sl(9, 3, 300.0), "dual_2": sl(11, 6, 300.0), "dual_dash": sl(14, 8, 250.0),
                    "dual_roll": sl(12, 8, 200.0), "dual_bstep": sl(11, 11, 180.0), "dual_jump": sl(20, 9, 400.0)}}
    pf = powerstance_features(ps)
    base = {"l1_startup": 0.25, "l1_recovery": 0.2, "l1_reach": 0.2, "l1_chain_dps": 0.25, "l1_stamina": 0.1}
    ow = offhand_weights(base)
    rows += [((of["l1_chain_dps"], of["l1_dash_startup"], of["l1_crouch_recovery"], of["l1_roll_damage"]),
              # 220 HP (bleed counted) by the second press's first hit at 10 + 12 frames.
              (round(220.0 / (22 / 30.0), 1), 10, 5, 120.0), "offhand: chain HP/s, and every movement L1 is L1 #1"),
             ((pf["l1_startup"], pf["l1_chain_dps"], pf["l1_dash_startup"], pf["l1_bstep_recovery"],
               pf["l1_crouch_damage"], pf["l1_reach"], "l1_jump_startup" in pf),
              (9, round(600.0 / (21 / 30.0), 1), 14, 11, 200.0, None, False),
              "powerstance: dual chain, dual_* movement, crouch falls back to the rolling L1, no reach, no jump"),
             ((round(sum(ow.values()), 6), ow["l1_startup"], ow["l1_roll_damage"], ow["l1_reach"]),
              (1.0, 0.125, 0.25 / 2 / 4, 0.2),
              "offhand_weights: string and movement half each, total and the string-only weights unchanged"),
             (powerstance_features({"slots": {"dual_dash": sl(14, 8, 1.0)}}), None, "powerstance: no dual chain, None")]
    # String trades (`ChainTrades`, faked): a multiplier per string, keyed by its first slot's
    # startup and its length, scales the string's rate and each movement attack's damage.
    class FakeTrades:
        def __init__(self, by):
            self.by, self.seen = by, []

        def trade(self, chain, weapon, grip, reach):
            self.seen.append((len(chain), chain[0]["startup"], weapon, grip, reach))
            m = self.by.get((len(chain), chain[0]["startup"], grip))
            return {"mult": m, "poise_hit": 99.0} if m is not None else None
    ft = FakeTrades({(2, 10, "1H"): 0.5, (1, 10, "1H"): 0.8, (2, 9, "1H"): 0.25, (1, 14, "1H"): 2.0})
    oft = offhand_features({**l1, "weapon": "Club"}, trades=ft)
    pst = powerstance_features(ps, powerstance_trades({**ps, "right": "Club"}, ft))
    r1 = [{"weapon": "Club", "grip": g, "affinity": "Standard", "damage": 1.0,
           "slots": {"r1_1": {"startup": su, "reach": 3.0}, "r1_2": {"startup": su}}, "summary": {"r1_chain_dps": dps}}
          for g, su, dps in (("1H", 10, 100.0), ("2H", 12, 80.0))]
    rft = right_features(r1, None, trades=FakeTrades({(2, 10, "1H"): 0.5, (2, 12, "2H"): 0.9}))[1]
    emb_t = {"weapons": [{**r1[0], "wieldable": True}, {**l1, "weapon": "Club", "grip": "L1", "wieldable": True},
                         {**r1[1], "wieldable": False}],
             "powerstance": [{**ps, "right": "Club", "wieldable": True}]}
    rows += [((oft["l1_chain_dps"], oft["l1_trade"], oft["l1_dash_damage"], oft["l1_crouch_damage"]),
              (round(of["l1_chain_dps"] * 0.5, 1), 0.5, round(of["l1_dash_damage"] * 0.8, 1),
               round(of["l1_crouch_damage"] * 0.8, 1)),
              "trades: the L1 string's rate times its multiplier, each movement L1 times L1 #1's"),
             ((pst["l1_chain_dps"], pst["l1_dash_damage"], pst["l1_roll_damage"]),
              (round(pf["l1_chain_dps"] * 0.25, 1), round(pf["l1_dash_damage"] * 2.0, 1), pf["l1_roll_damage"]),
              "trades: a powerstance string and its dash attack scaled, an unvalued roll attack left alone"),
             ((rft["r1_chain_dps"], rft["r1_trade"]), (72.0, 0.9),
              "trades: the R1 rate is the better grip after its own multiplier (2H 80 x 0.9 over 1H 100 x 0.5)"),
             (sorted((len(c), c[0]["startup"], g) for c, _, g, _ in trade_items(emb_t)),
              sorted([(2, 10, "1H"), (2, 10, "1H"), (1, 10, "1H"), (2, 9, "1H"), (1, 14, "1H"), (1, 12, "1H"),
                      (1, 11, "1H"), (1, 12, "1H")]),
              "trade items: R1 per wieldable grip, the L1 string and L1 #1, the dual string and each movement slot")]
    # Per grip (`main_hand_entry`): the 2H record hits harder (STR x1.5) but starts later; the
    # best-of features mix the two, each `by_grip` entry keeps its own grip's numbers.
    mhe = main_hand_entry([r1[0], {**r1[1], "damage": 1.5}], None)
    mark_true_combo(mhe, {"2H"})
    rows += [((mhe["features"]["ar"], mhe["features"]["r1_startup"]), (1.5, 10),
              "main_hand_entry: best-of features take 2H AR beside the 1H startup"),
             ({g: (f["ar"], f["r1_startup"], f["true_combo"]) for g, f in mhe["by_grip"].items()},
              {"1H": (1.0, 10, 0), "2H": (1.5, 12, 1)},
              "main_hand_entry: each grip its own AR and startup; a 2H-only true combo marks 2H only"),
             (sorted(main_hand_entry([r1[0]], None)["by_grip"]), ["1H"],
              "main_hand_entry: a grip with no wieldable record has no entry")]
    # Feint (`FeintModel`): roll i-frames 13, two reactions (8 and 14 frames, half each). R2 hold
    # from f6, End clip first hit f10, charged hit f40: the plain R2 lands at 16, the delayed one
    # anywhere in 16-40. The 8-frame reaction sees the release and evades either way; the 14-frame
    # one evades the plain R2 but covers only 13 of the 24 delay frames.
    fm = FeintModel(13.0, [(8.0, 0.5), (14.0, 0.5)], 0.5, 0.2)
    r2 = {"r2_1": {"startup": 10.0, "hold": [6.0, 90.0], "damage": 300.0, "hits": 1},
          "r2_1c": {"startup": 40.0, "hold": [6.0, 90.0], "damage": 420.0, "hits": 1}}
    p = 0.5 * (1.0 - 13.0 / 24.0)
    rows += [(fm(r2), {"feint_hp": round(0.2 * 0.5 * p * 300.0, 2), "r2_feint": round(p, 4), "r2_delay": 24.0},
              "feint: the delayed R2 beats the reactions that evade the plain one but not a 24-frame guess"),
             (FeintModel(13.0, [(30.0, 1.0)], 0.5, 0.2)(r2)["r2_feint"], 0.0,
              "feint: a delay that only helps slow reactions is never thrown (floored at 0)"),
             (fm({"r1_1": {"startup": 10}}), {}, "feint: no held R2, no feint"),
             (round(norm_score({"ar": 100.0, "feint_hp": 10.0}, [{"ar": 100.0, "feint_hp": 10.0}, {"ar": 200.0}],
                               ("ar", "feint_hp"), {"ar": 1.0}), 4), 0.55,
              "norm_score: feint HP scored on the ar scale with ar's weight, no weight of its own"),
             (norm_score({"ar": 100.0, "feint_hp": 10.0}, [{"ar": 200.0}], ("feint_hp",), {"ar": 1.0}), 0.0,
              "norm_score: a bonus is scored only beside its paired feature")]
    # Movement R1s (`_move_features`): an entry step before the rolling and backstep R1 (faked with
    # a startup to key the fake trades), none before the running R1; no crouch R1, so the crouch
    # press is the rolling one.
    def fake_entry(k):
        return {"startup": {"roll_r1": -1, "bstep_r1": -2}[k]} if k in ("roll_r1", "bstep_r1") else None
    mv = {"run_r1": sl(17, 13, 360.0), "roll_r1": sl(13, 13, 300.0), "bstep_r1": sl(18, 12, 300.0)}
    mf = _move_features({"1H": {"weapon": "Club", "slots": mv}},
                        FakeTrades({(1, 17, "1H"): 0.9, (2, -1, "1H"): 0.6, (2, -2, "1H"): 0.3}), fake_entry)
    rows += [((mf["r1_dash_damage"], mf["r1_roll_damage"], mf["r1_bstep_damage"], mf["r1_crouch_damage"],
               mf["r1_crouch_startup"], mf["r1_bstep_recovery"]),
              (324.0, 180.0, 90.0, 300.0, 13, 12),
              "movement R1s: each traded after its own entry step, the crouch press playing the rolling R1 "
              "with no roll before it (no fake trade for it)"),
             (sorted(k for k, v in right_moves(mv, fake_entry).items() if len(v[1]) == 2), ["bstep", "roll"],
              "movement R1s: only the rolling and backstep R1 carry an entry")]
    mw_ = main_weights({"r1_startup": 0.2, "r1_recovery": 0.2, "r1_chain_dps": 0.1, "ar": 0.3})
    rows += [((round(sum(mw_.values()), 6), mw_["r1_startup"], mw_["r1_roll_damage"], mw_["ar"]),
              (0.8, 0.1, 0.1 / 2 / 4, 0.3),
              "main_weights: R1 string and movement R1s half each, the total and every other weight unchanged")]
    # Reach over the duel spread (`spread_weights`): two weapons whose strings the spread has
    # already valued (`r1_chain_dps` after `r1_trade`, reach inside it), one reaching farther. Under
    # `--arena-m` they must score alike, the reach counted once; at a fixed distance reach still
    # scores. The crit gap's reach fallback is the same reach under another name and goes with it.
    prefs = tomllib.loads(PREFS.read_text())
    mw_s, ow_s = (spread_weights(main_weights(prefs["class_pick_weights"]), {"nodes": [(2.0, 1.0)]}),
                  spread_weights(prefs["offhand_pick_weights"], {"nodes": [(2.0, 1.0)]}))
    near, far = {"r1_chain_dps": 100.0, "r1_reach": 2.5, "ar": 300.0}, {"r1_chain_dps": 100.0, "r1_reach": 4.0, "ar": 300.0}
    use = ("r1_chain_dps", "r1_reach", "ar", "crit")
    wts = {"r1_chain_dps": 0.3, "r1_reach": 0.2, "ar": 0.3, "crit": 0.2}
    sw = spread_weights(wts, {"nodes": [(2.0, 1.0)]})
    rows += [((mw_s["r1_reach"], ow_s["l1_reach"]), (0.0, 0.0),
              "spread_weights: over the duel spread the main and off-hand reach weights are off"),
             (spread_weights(wts, None), wts, "spread_weights: at a fixed distance the weights are unchanged"),
             (norm_score(near, [near, far], use, sw) == norm_score(far, [near, far], use, sw), True,
              "spread: a farther reach adds nothing once the strings carry it (reach not counted twice)"),
             (norm_score(far, [near, far], use, wts) > norm_score(near, [near, far], use, wts), True,
              "fixed distance: the farther reach still scores"),
             (round(norm_score(far, [near, far], use, sw), 4), 1.0,
              "spread: the crit gap leaves the total instead of scoring reach under the crit weight"),
             (round(norm_score(near, [near, far], use, wts), 4), round((0.3 + 0.2 * 2.5 / 4 + 0.3 + 0.2 * 2.5 / 4), 4),
              "fixed distance: the crit gap still scores reach")]
    # `PoolScales`: scoring through a pool's extremes read once is scoring through the pool, for a
    # higher-better, a lower-better, a penalty, a bonus, the crit fallback, `r1_cover` and a feature
    # nobody carries.
    pool = [{"ar": 300.0, "r1_startup": 14, "r1_cover": 0.4, "pb_cost": 2.0, "feint_hp": 5.0, "r1_reach": 3.0},
            {"ar": 200.0, "r1_startup": 11, "pb_cost": 0.0, "feint_hp": 0.0, "crit": 300.0},
            {"ar": 0.0, "r1_startup": 18, "r1_cover": 0.2, "pb_cost": 1.5, "r1_reach": 4.0}]
    use_ = ("ar", "r1_startup", "pb_cost", "feint_hp", "crit", "stamina")
    wts_ = {"ar": 0.4, "r1_startup": 0.2, "pb_cost": 0.3, "feint_hp": 0.1, "crit": 0.2, "stamina": 0.1, "r1_reach": 0.1}
    sc = PoolScales(pool)

    def walked(feats, pool, use, weights):
        """`norm_score` as it was before `PoolScales`, walking the pool per feature."""
        gap = "crit" in use and feats.get("crit") is None and not weights.get("r1_reach", 0.0) > 0
        tot = sum(weights.get(f, 0.0) for f in use if f not in PENALTIES and f not in BONUSES
                  and not (gap and f == "crit"))
        s = 0.0
        for f in use:
            if gap and f == "crit":
                continue
            if f in PENALTIES or f in BONUSES:
                k = PENALTIES.get(f) or BONUSES[f]
                x, scale = feats.get(f), [y for y in (p.get(k) for p in pool) if y]
                if x and scale and (f in PENALTIES or k in use):
                    s += (-weights.get(f, 0.0) if f in PENALTIES else weights.get(k, 0.0)) * x / max(scale)
                continue
            g = "r1_reach" if f == "crit" and feats.get("crit") is None else f
            g = "r1_cover" if g == "r1_startup" and "r1_cover" in feats else g
            x, vals = feats.get(g), [y for y in (p.get(g) for p in pool) if y]
            if x and vals:
                s += weights.get(f, 0.0) * ((min(vals) / x) if g in LOWER_BETTER else (x / max(vals)))
        return s / tot if tot else 0.0
    rows += [([norm_score(f, pool, use_, w, scales=sc) for f in pool + [{}] for w in (wts_, {"ar": 1.0})],
              [walked(f, pool, use_, w) for f in pool + [{}] for w in (wts_, {"ar": 1.0})],
              "PoolScales: the pool's extremes read once score every features dict as walking the pool does")]
    for got, want, what in rows:
        ok &= got == want
        print(f"{'ok  ' if got == want else 'FAIL'} {what}: {got!r}" + ("" if got == want else f" != {want!r}"))
    for got, want, what in [
        (parry_share((), sk), 1.0, "no bans: every parry legal"),
        (parry_share(("Parry", "Golden Parry"), sk), 0.5, "half the parries banned"),
        (parry_share(sk | {"Endure"}, sk), 0.0, "every parry banned"),
        (pick_weights(sk, sk)["crit"], 0.0, "no legal parry: crit weight 0"),
        (pick_weights((), sk)["crit"], tomllib.loads(PREFS.read_text())["class_pick_weights"]["crit"],
         "no bans: crit weight as written"),
        ("Parry" in parry_skills() and "Golden Parry" in parry_skills(), True, "PARRY_SKILLS read from tables.rs"),
    ]:
        ok &= got == want
        print(f"{'ok  ' if got == want else 'FAIL'} {what}: {got!r}" + ("" if got == want else f" != {want!r}"))
    print("selftest passed" if ok else "selftest FAILED")
    return 0 if ok else 1


def main() -> int:
    if sys.argv[1:] == ["--selftest"]:
        return selftest()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("build", type=Path)
    ap.add_argument("--embed", type=Path, required=True)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--list-wieldable", action="store_true")
    ap.add_argument("--combos", type=Path,
                    help="JSONL of measured true combos ({weapon, grip, from, to, gap, escape} per line)")
    ap.add_argument("--parry", type=Path,
                    help="er-mechanics-parry.py --weapons-from output: each weapon's parry cost per grip, "
                         "scored as `parry_cost` / `l1_parry_cost`")
    ap.add_argument("--perfect-block", type=Path,
                    help="er-mechanics-perfect-block.py --weapons-from output: each weapon's perfect-block "
                         "cost per affinity and grip, scored as `pb_cost` / `l1_pb_cost`")
    ap.add_argument("--exclude", type=Path,
                    help="the loadout's --exclude JSON: its banned ashes set the crit weight (`pick_weights`)")
    ap.add_argument("--mirror", type=Path, default=PUNISH_MIRROR,
                    help="planner build mirror whose PvP openings `r1_cover` is measured on (`PunishCoverage`)")
    ap.add_argument("--no-trades", action="store_true",
                    help="leave the R1, L1 and dual strings unvalued against the pool's answers "
                         "(`er-mechanics-ash-interrupt.ChainTrades`)")
    ap.add_argument("--pool-rl", type=int,
                    help="RL the opponent pool is centred on (default: the build's own, +-POOL_RL_WINDOW)")
    ap.add_argument("--jobs", type=int,
                    help="throttled workers for the string simulations (default: every allowed core but one)")
    ap.add_argument("--arena-m", type=float,
                    help="a duel's starting separation, m: the strings are valued over the duel's distance "
                         "spread (`er-mechanics-ash-interrupt.duel_spread`), credited only where they reach")
    a = ap.parse_args()
    parry = json.loads(a.parry.read_text()).get("weapons", {}) if a.parry else {}
    pbw = json.loads(a.perfect_block.read_text()).get("weapons", {}) if a.perfect_block else {}
    excl = json.loads(a.exclude.read_text()) if a.exclude else {}
    weights = pick_weights(excl.get("ashes", ()))
    # A grip the ruleset bans (`--exclude` rows: Cleanrot Knight's Sword 2H on the duel ladder) is
    # never scored, so a weapon is not credited the banned grip's faster R1.
    row_bans = [tuple(r) for r in excl.get("rows", ())]

    def grip_ok(r):
        return not any(w == r["weapon"] and aff in (None, r["affinity"]) and g == r["grip"] for w, aff, g in row_bans)
    emb = json.loads(a.embed.read_text())
    cover = PunishCoverage(emb, a.mirror)
    feint = FeintModel.from_game()
    print(f"feint: roll i-frames {feint.I:g}, reaction {feint.delays[0][0]:.1f}-{feint.delays[-1][0]:.1f} frames "
          f"with network legs, R2 share {feint.r2_share:.2f}, reacting share {feint.react_share:.2f}",
          file=sys.stderr)
    print(f"punish coverage: {cover.n_builds} corpus builds, {len(cover.w)} openings "
          f"({sum(cover.missing.values())} builds' weapons not in the embedding); "
          + ", ".join(f"f{s} {cover(s):.3f}" for s in (8, 10, 12, 14, 16, 18)),
          file=sys.stderr)     # stdout is the `--list-wieldable` list the pipeline reads
    # A reach override only goes to a weapon whose strongest scaling is one of the build's main
    # stats (at least half its highest): a same-moveset sibling built for other stats is not a
    # longer copy of the winner (user, 2026-10-09, Wakizashi over Bloodstained Dagger on STR 80:
    # "Its not really a fun weapon since its just the sibling to bloodstained for other stats").
    spec = importlib.util.spec_from_file_location("er_mechanics_ar", HERE / "er-mechanics-ar.py")
    AR = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(AR)
    ar_tables = AR.Tables(None)
    top_stat = max(emb["stats"].values())
    primary = {k for k, v in emb["stats"].items() if v >= top_stat / 2}
    correct = {"str": "correctStrength", "dex": "correctAgility", "int": "correctMagic",
               "fth": "correctFaith", "arc": "correctLuck"}

    def fits_stats(w, aff="Standard"):
        try:
            row = ar_tables.weapons[ar_tables.find_weapon(w, aff)]
        except (KeyError, SystemExit):
            return True
        best = max(row.get(c, 0) for c in correct.values())
        return best > 0 and any(row.get(correct[k], 0) == best for k in primary)
    max_share = emb["fun_max_share"]
    mech = emb["mechanics"]

    order = []
    for s in json.loads(a.build.read_text())["inventory"]["slots"]:
        w = ascii_name(s["name"])
        if w not in order:
            order.append(w)

    trades = None
    if not a.no_trades and not a.list_wieldable:
        trades = chain_trades(json.loads(a.build.read_text()), a.pool_rl, a.arena_m)
        print(f"string trades: my poise {trades.poise:g}, pool {trades.opp.n_builds} builds "
              f"({trades.opp.missing} without a cached moveset), poise carried {trades.carry}"
              + (", over the duel spread " + ", ".join(f"{n[0]:g} m x{n[1]:.3f}" for n in trades.spread["nodes"])
                 if trades.spread else ", at REACH_SHARE x reach"), file=sys.stderr)
        jobs = a.jobs or max(1, len(_ai().allowed_cpus()) - 1)
        trades.prepare(trade_items(emb, grip_ok), jobs, log=lambda m: print(m, file=sys.stderr, flush=True))
    # Over the duel spread the strings carry reach (`spread_weights`); the reach weights come off.
    reach_spread = bool(trades is not None and trades.spread)
    weights = spread_weights(weights, reach_spread)
    off_base = spread_weights(dict(tomllib.loads(PREFS.read_text())["offhand_pick_weights"]), reach_spread)
    if reach_spread:
        print("reach weights off: r1_reach and l1_reach are credited by the strings over the duel spread",
              file=sys.stderr)

    recs = collections.defaultdict(list)
    for r in emb["weapons"]:
        recs[r["weapon"]].append(r)
    ashes = collections.defaultdict(list)
    for x in emb["ashes"]:
        ashes[x["weapon"]].append(x)

    weapons = {}
    for w, rs in _progress().counted(list(recs.items()), "weapons"):
        right = [r for r in rs if r["grip"] in RIGHT_GRIPS]
        ok = [r for r in right if r["wieldable"] and grip_ok(r)]
        grips = sorted({r["grip"] for r in ok})
        top = max(ok, key=lambda r: hit_damage(r), default=None)
        # The ash value at the affinity the weapon is carried on (its best-damage one), so a weapon
        # some sweep also covered on another affinity is not scored on the better of the two.
        own = [x for x in ashes.get(w, []) if x["cat"] != "utility"]
        av = best(x["value"] for x in ([x for x in own if top and x["affinity"] == top["affinity"]] or own))
        top, feats = right_features(ok, av, parry.get(w), pbw.get(w), cover, trades, feint)
        # Per hand, per role affinity (module docstring, role 4). An affinity the ash sweep did
        # not cover is scored with the carried affinity's ash value.
        affs, by_aff = role_affinities(rs)
        main_hand, offhand = {}, {}
        for aff in affs:
            mine = [r for r in by_aff[aff] if r["grip"] in RIGHT_GRIPS and r["wieldable"] and grip_ok(r)]
            if mine:
                aav = best(x["value"] for x in own if x["affinity"] == aff) or av
                main_hand[aff] = main_hand_entry(mine, aav, parry.get(w), pbw.get(w), cover, trades, feint)
            left = next((r for r in by_aff[aff] if r["grip"] == "L1" and r["wieldable"]), None)
            of = offhand_features(left, parry.get(w), pbw.get(w), trades) if left else None
            if of:
                offhand[aff] = {"features": of}
        weapons[w] = {"order": order.index(w) if w in order else None, "in_build": w in order,
                      "class": rs[0]["class"], "fixed_skill": rs[0]["fixed_skill"],
                      "wieldable": bool(ok), "grips": grips,
                      "affinity": top["affinity"] if top else None, "grip": top["grip"] if top else None,
                      "grease": top.get("grease") if top and top.get("damage_greased") else None,
                      "moveset": rs[0]["moveset"], "features": feats,
                      "parry": {g: {k: v[k] for k in ("cost", "catch", "exposure")}
                                for g, v in (parry.get(w) or {}).items() if v},
                      "perfect_block": {g: {k: v[k] for k in ("cost", "p", "stopped", "affinity")}
                                        for g, v in (pbw.get(w) or {}).items() if g != "by_affinity" and v},
                      "main_hand": {"by_affinity": main_hand}, "offhand": {"by_affinity": offhand},
                      # Fists and claws have no crouch attack (Bloodhound's Step placement).
                      "crouch": any(r["slots"].get("crouch_r1") for r in ok),
                      "fun": max([r["fun"] for r in ok] + [x["fun"] for x in ashes.get(w, [])], default=0.0)}

    combos = collections.defaultdict(list)
    combo_grips = collections.defaultdict(set)
    for c in tomllib.loads(TRUE_COMBOS.read_text()).get("combo", []):
        combos[c["weapon"]].append(c["string"])
        # A combo recorded from the user names no grip: it counts in both.
        combo_grips[c["weapon"]].update(RIGHT_GRIPS)
    if a.combos:
        for line in a.combos.read_text().splitlines():
            if line.strip():
                c = json.loads(line)
                combos[c["weapon"]].append(f"{c['from']} -> {c['to']} ({c['gap']}/{c['escape']})")
                combo_grips[c["weapon"]].add(SWEEP_GRIPS.get(c.get("grip"), c.get("grip")))
    for w, v in weapons.items():
        v["true_combos"] = combos.get(w, [])
        # Carried on the main-hand features too, so every later scorer that re-scores them
        # (`er-mechanics-combo-loadout.hand_scores`, the equip sets) adds `TRUE_COMBO_BONUS` as
        # the class pick does: measured 2026-10-19, Raptor Talons' 2h_r2_1 -> 2h_r2_2 link
        # reached the class pick only, and the sets scored it with no combo term at all.
        for mh in v["main_hand"]["by_affinity"].values():
            mark_true_combo(mh, combo_grips.get(w, set()))

    if a.list_wieldable:
        print(",".join(w for w, v in weapons.items() if v["wieldable"]))
        return 0
    if not a.out:
        ap.error("--out is required unless --list-wieldable")

    def score(c_ws, w, use):
        return (norm_score(weapons[w]["features"], [weapons[o]["features"] for o in c_ws], use, weights)
                + (TRUE_COMBO_BONUS if weapons[w]["true_combos"] else 0.0))

    off_weights = offhand_weights(off_base)

    def hand_pick(ws, hand):
        """Score every (weapon, role affinity) of `ws` for one hand against the others and return
        the best as {weapon, affinity, score}. A true combo is an R1/R2 link, so it counts on the
        main hand only."""
        cands = [(w, aff, v["features"]) for w in ws for aff, v in weapons[w][hand]["by_affinity"].items()]
        pool = [f for _, _, f in cands]
        use, wts = (FEATURES, weights) if hand == "main_hand" else (OFFHAND_FEATURES, off_weights)
        out = None
        for w, aff, f in cands:
            s = norm_score(f, pool, use, wts)
            if hand == "main_hand" and weapons[w]["true_combos"]:
                s += TRUE_COMBO_BONUS
            s = round(s, 3)
            weapons[w][hand]["by_affinity"][aff]["score"] = s
            if s > weapons[w][hand].get("score", -1):
                weapons[w][hand].update(affinity=aff, score=s)
            if out is None or s > out["score"]:
                out = {"weapon": w, "affinity": aff, "score": s}
        # Within `SCALING_TIE` of the best score, `SCALING_REACH_GAIN` more reach wins (the
        # scaling pick's rule, on this hand's own reach): the longest such candidate.
        if out is None:                 # no candidate in this hand (a class the stats cannot wield)
            return None
        reach = "r1_reach" if hand == "main_hand" else "l1_reach"
        top = weapons[out["weapon"]][hand]["by_affinity"][out["affinity"]]["features"].get(reach)
        if top:
            longer = [(w, aff, f) for w, aff, f in cands
                      if weapons[w][hand]["by_affinity"][aff]["score"] >= out["score"] * (1 - SCALING_TIE)
                      and (f.get(reach) or 0) >= top * (1 + SCALING_REACH_GAIN)
                      and fits_stats(w)]  # native scaling: Heavy turns every weapon to STR
            if longer:
                w, aff, f = max(longer, key=lambda c: (c[2][reach], weapons[c[0]][hand]["by_affinity"][c[1]]["score"]))
                out = {"weapon": w, "affinity": aff, "score": weapons[w][hand]["by_affinity"][aff]["score"],
                       "reach_over": out["weapon"]}
        return out

    # Fun candidates: (class, mechanic) -> best carrier.
    fun = {}

    def offer(cls, m, cand, strength):
        if m is None or mech.get(m, {}).get("share", 1.0) > max_share:
            return
        k = (cls, m)
        if k not in fun or strength > fun[k][1]:
            fun[k] = (cand, strength)

    for w, rs in recs.items():
        for r in rs:
            if r["grip"] not in RIGHT_GRIPS or not r["wieldable"]:
                continue
            for m in r["mechanics"]:
                offer(r["class"], m, {"weapon": w, "affinity": r["affinity"], "grip": r["grip"], "ash": None},
                      hit_damage(r))
    for w, xs in ashes.items():
        if w not in weapons:
            continue
        for x in xs:
            # Ash values are ungreased, so a greasable affinity's copy is undervalued against an
            # elemental one; a skill's carrier is taken on the weapon's best-damage affinity (with
            # its grease), and other affinities only stand in when that one was not swept.
            if any(y["affinity"] == weapons[w]["affinity"] for y in xs) and x["affinity"] != weapons[w]["affinity"]:
                continue
            offer(weapons[w]["class"], x.get("mechanic"),
                  {"weapon": w, "affinity": x["affinity"], "grip": x["grip"], "ash": x["ash"]}, x["value"])

    classes, by_class = {}, collections.defaultdict(list)
    for w, v in weapons.items():
        by_class[v["class"]].append(w)
    for c, ws in sorted(by_class.items()):
        ok = [w for w in ws if weapons[w]["wieldable"]]
        for w in ok:
            weapons[w]["score"] = round(score(ok, w, FEATURES), 3)
            weapons[w]["score_no_ar"] = round(score(ok, w, [f for f in FEATURES if f != "ar"]), 3)
        # The scaling pick is the highest hit damage, unless a weapon within `SCALING_TIE` of that
        # damage reaches `SCALING_REACH_GAIN` farther on R1 #1; then the longest such weapon. User,
        # 2026-10-08: Nightrider Glaive does 0.8% less than Gargoyle's Halberd at STR 80 but
        # reaches 18% farther, "that's the weapon that should be chosen".
        top = max(ok, key=lambda w: weapons[w]["features"]["ar"] or 0, default=None)
        scaling = top
        if top:
            f = weapons[top]["features"]
            longer = [w for w in ok if (weapons[w]["features"]["ar"] or 0) >= (f["ar"] or 0) * (1 - SCALING_TIE)
                      and f["r1_reach"]  # whips have no measured reach; no gain to compare
                      and fits_stats(w)
                      and (weapons[w]["features"]["r1_reach"] or 0) >= f["r1_reach"] * (1 + SCALING_REACH_GAIN)]
            scaling = max(longer, key=lambda w: weapons[w]["features"]["r1_reach"], default=top)
        reach = max(ok, key=lambda w: weapons[w]["score_no_ar"], default=None)
        picks = sorted(((m, cand, s) for (cc, m), (cand, s) in fun.items() if cc == c),
                       key=lambda p: (mech[p[0]]["share"], -p[2]))
        twins = None
        if scaling and reach and scaling != reach:
            share, diff = moveset_distance(*({g: next(r for r in recs[w] if r["grip"] == g) for g in RIGHT_GRIPS}
                                             for w in (scaling, reach)))
            twins = {"slot_share": round(share, 3), "feature_diff": round(diff, 4),
                     "near": share >= TWIN_SLOT_SHARE and diff <= TWIN_MAX_DIFF}
        classes[c] = {"scaling": scaling, "scaling_affinity": weapons[scaling]["affinity"] if scaling else None,
                      "reach": reach, "reach_affinity": weapons[reach]["affinity"] if reach else None,
                      "twins": twins,
                      "main_hand": hand_pick(ok, "main_hand"), "offhand": hand_pick(ok, "offhand"),
                      # The top-damage weapon the reach rule passed over: one copy, not a row
                      # (user, 2026-10-08: "We can have 1 of the was, but a row of the now").
                      "damage_top": top if top != scaling else None,
                      "fun": [{**cand, "mechanic": m, "label": mech[m]["label"],
                               "share": mech[m]["share"], "rarity": round(1 - mech[m]["share"], 4)}
                              for m, cand, _ in picks],
                      "top": sorted(ok, key=lambda w: -weapons[w]["score"])[:3]}
    # The same weighted score normalised over every wieldable weapon instead of one class, ash left
    # out: how good the moveset is against the whole arsenal. Bloodhound's Step goes on the best of
    # these that has a crouch attack (user, 2026-10-08: "If a weapon has a crouch attack, and its
    # the best moveset by far, then I think bloodhound step is maximally ideal on it").
    every = [w for w, v in weapons.items() if v["wieldable"]]
    for w in every:
        weapons[w]["moveset_score"] = round(score(every, w, [f for f in FEATURES if f != "ash_value"]), 3)
    # The dual strings' multipliers, read by `er-mechanics-combo-loadout.PowerstanceOffs` so its
    # powerstance off hands are valued as the L1 ones here are, without a second pool.
    ps_trades = {}
    if trades is not None:
        for p in emb.get("powerstance") or ():
            if p.get("wieldable"):
                t = powerstance_trades(p, trades)
                if t:
                    ps_trades[powerstance_key(p)] = t
        trades.save()
    # `off_weights` is the `[offhand_pick_weights]` table before `offhand_weights` spreads it, with
    # `spread_weights` applied, so `er-mechanics-combo-loadout.hand_scores` reads the same table.
    a.out.write_text(json.dumps({"weights": weights, "off_weights": off_base, "reach_spread": reach_spread,
                                 "stats": emb["stats"], "fun_max_share": max_share,
                                 "weapons": weapons, "classes": classes,
                                 "trades": ({"poise": trades.poise, "carried": trades.carry,
                                             "pool_builds": trades.opp.n_builds} if trades else None),
                                 "powerstance_trades": ps_trades}, indent=1))
    for c, v in classes.items():
        tw = v["twins"]
        print(f"== {c}: scaling {v['scaling_affinity']} {v['scaling']}, reach/moveset {v['reach_affinity']} "
              f"{v['reach']}" + (f" (slots shared {tw['slot_share']}, feature diff {tw['feature_diff']}"
                                 f"{', near-twins' if tw['near'] else ''})" if tw else ""))
        for hand in ("main_hand", "offhand"):
            p = v[hand]
            if p:
                f = weapons[p["weapon"]][hand]["by_affinity"][p["affinity"]]["features"]
                print(f"   {hand}: {p['affinity']} {p['weapon']} score {p['score']} "
                      + " ".join(f"{k}={x}" for k, x in f.items()))
        for w in v["top"]:
            f = weapons[w]["features"]
            print(f"   {w}{' *' if weapons[w]['in_build'] else ''}: score {weapons[w]['score']} "
                  f"(no ar {weapons[w]['score_no_ar']}) " + " ".join(f"{k}={f.get(k)}" for k in FEATURES))
        for p in v["fun"]:
            print(f"   fun: {p['affinity']} {p['weapon']}{' with ' + p['ash'] if p['ash'] else ''} "
                  f"({p['label']}, share {p['share']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
