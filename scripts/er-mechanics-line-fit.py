#!/usr/bin/env python3
"""Stat fit at fixed RL for an attacker line: Vigor, Endurance (and with it the armor, the poise and
the HP piece it carries at medium roll), Mind and the damage stats, traded on one fight-level number.
A line's offense is its kit (`Line.kit`): spells (`Spell`, each at its school's stat) and a weapon's
R1 (`Weapon`, at every damage stat through `er-mechanics-ar`), so a caster, a melee and a split line
are fitted on the same D.

    python3 scripts/er-mechanics-line-fit.py --selftest
    python3 scripts/er-mechanics-line-fit.py --line Intelligence --line Faith --json <out.json>

Write-up: `docs/er-mechanics/line-fit.md`. Labels as in the sibling docs: `VERIFIED` = regulation
value or traced code, `MEASURED` = computed here from the regulation and the corpus mirror,
`INFERRED` = a modelling choice or an untraced consumer.

The question (user, 2026-11-12): "with a mage build, we could go less than 89 poise, get less than
58 vigor, and dump points into mind, but I just never know if that's actually better".

# The number

D is the damage the build deals before it dies (`er-mechanics-pouch`'s module doc, "How offense,
defense and time make one number"), here with every term the stat block moves:

    D = win x casts x hit_out x u x paid x N

- `hit_out`: one connect of the line's representative spell on the window's median defender
  (`er-mechanics-spells.SpellModel.evaluate` `one_hit`, its talismans' rates applied), at the main
  stat the allocation leaves. The catalyst buff it carries is reported beside it. A weapon's is the
  first hit of its R1 on the same defender (`GameWorld.weapon_hit`: `er-mechanics-ar.attack_rating`
  at the five damage stats, the R1's motion values, the weapon's vsPlayer rates and the hit's
  FinalDamageRateParam row). A kit of several attacks deals their share-weighted mean.
- `u`: the share of casts not broken in their windup, 1 - `contested` x the pool share that breaks
  the cast before it releases at the build's poise (`er-mechanics-interrupt.reverse` on the cast as
  a sequence: its strike frame is `startup`, its TAE 795 windows read off the cast clips; a weapon's
  R1 is the interrupt module's own `resolve` sequence).
- `paid`: the share of the uninterrupted casts the FP plan pays: one bar at the build's Mind, the
  FP items between engagements, then Ceruleans at min(220, bar) each; at most one bar is spent in
  one engagement (`fp_paid`). A weapon's R1 costs no FP and is always paid.
- `N`: the engagements the build lives: (max HP + Crimson drunk x min(810, max HP - `DRINK_AT` x L))
  / L, at most the fight's engagements (`crimson='pool'`, the default; a flask never heals past max
  HP), with
  L = (1 - win) x attackers x mean pool hit x hits per pool attack at the build's poise.
  `crimson='pouch'` prices the Crimson as `er-mechanics-pouch` does instead: HP / L x
  (L + heal) / L, the heal spread over the fight's engagements.

The Cerulean count is chosen inside every allocation, the rest of the flasks staying Crimson.
The medium-roll budget carries the kit alone (`carried_for`: the catalysts and the weapon the line's
offense is measured on), never what its archetype's players carry.
Everything else is chosen by `fit`: the starting class, the three searched stats, the HP piece
(`ArmorModel.hp_pieces`, looped as `er-weapon-build.joint_for` loops them), the armor set on the
`ArmorModel` frontier at every poise (no floor), the talismans (four slots, greedy on D), then the
physick tears (two slots, the ones that cut damage or raise poise, `AT.buff_table`, against the ones
that raise the spell, its main stat or cut its FP cost) and the consumables (`Buffs.stack` keeping
one of an exclusive family).

# Interface (for `er-mechanics-attack-similarity` and anything else)

    F = importlib (scripts/er-mechanics-line-fit.py)
    world = F.GameWorld(rl=150)
    got = F.fit(F.LINES['Intelligence'], world)
    got['stats'], got['class'], got['D'], got['offense'], got['defense'], got['fp'], got['gear']

`fit(line, world)` takes a `Line` (frozen dataclass, `dataclasses.replace` to vary it) and returns
the allocation and its breakdown; `evaluate(line, world, gear, stats, wear, armor_set)` scores one
given allocation.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent


def _mod(name: str, fname: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HERE / fname)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


AT = _mod("er_mechanics_armor_targets", "er-mechanics-armor-targets.py")
INT, DEF, RES = AT.INT, AT.DEF, AT.RES
SIM = _mod("er_mechanics_attack_similarity", "er-mechanics-attack-similarity.py")
OPT = _mod("er_builds_optimize", "er-builds-optimize.py")

ATTRS = SIM.ATTRS
LEVEL_OFFSET = SIM.LEVEL_OFFSET
STAT_MAX = SIM.STAT_MAX
#: The search ranges (`fit`). Vigor 20..80 brackets the HP curve's knee (60, `SIM.vigor_knee`) on
#: both sides; Endurance and Mind run from the lowest class base up to 60. A fit that lands on an
#: edge says so (`edges`).
VIG_RANGE = (20, 80)
END_MAX = 60
MND_MAX = 60
#: Poise grid (menu units) for the stagger and interrupt curves; `U_STEP` is the interrupt curve's
#: step (one `INT.reverse` per point, 10-20 ms each, `MEASURED` 2026-11-12), linear between points.
POISE_MAX = 300
U_STEP = 5
TALISMAN_SLOTS = 4
TEAR_SLOTS = 2
#: Rounds of the coordinate ascent: stats, then talismans at those stats, until the set repeats.
ROUNDS = 3
MEDIUM_ROLL_MAX = RES.WEIGHT_HEAVY
#: `AT.TYPES` column -> the `er-mechanics-defense` type of its absorption.
DEF_TYPE = {"slash": "slash", "strike": "strike", "pierce": "pierce", "standard": "physical", "magic": "magic",
            "fire": "fire", "lightning": "lightning", "holy": "holy"}
#: The stat each `AT.ELEMENTS` entry's flat defense reads (`DEF.STAT_DEF_GRAPH`; lightning reads
#: none, so its column is indexed by Endurance and gets no stat term).
ELEMENT_STAT = ["str", "int", "vig", "end", "arc"]
#: FP cost field of the school's casts on a SpEffect row (`VERIFIED` names in the paramdef;
#: Primal Glintstone Blade's row 330800 carries 0.75 in both).
CONSUMPTION = {"int": "magicConsumptionRate", "fth": "miracleConsumptionRate"}
#: The spell buff the catalyst shows: 100 x its magic (sorcery) or holy (incantation) multiplier
#: (`er-mechanics-ar`, `FUN_1407c16c0` / `FUN_1407c1760`).
BUFF_ELEMENT = {"int": "magic", "fth": "holy"}
IMP_HEAD = "Imp Head (Lion)"
#: The HP left, as a share of one engagement's drain, when a Crimson is drunk (`INFERRED`: the
#: drink comes before the engagement that would kill, on average half its drain above 0).
DRINK_AT = 0.5
#: `SpEffectParam.stateInfo` of the counter-hit rows (Spear Talisman's 320600). `Buffs.STATE_SKIPPED`
#: lists it among the states its accumulator skips; no share of counter hits is modelled here.
COUNTER_STATE = 197
#: `EquipParamGoods.goodsType` of a crystal tear (`VERIFIED` on the installed regulation: Opaline
#: Hardtear 11011, Intelligence-knot Crystal Tear 11023, Magic-Shrouding Cracked Tear 11029 are 10;
#: `er-mechanics-pouch.PHYSICK_GOODS_TYPE` 9 is the flask's own type).
TEAR_GOODS_TYPE = 10
#: The five damage stats.
DMG = ("str", "dex", "int", "fth", "arc")
#: Weapon classes with no melee R1 to measure a melee line's offense on: bows, crossbows, ballistas
#: and catalysts (`er-mechanics-armor-targets.RANGED_TYPES`).
NOT_MELEE = AT.RANGED_TYPES
#: The R1's first judge (`AT.FAMILY_JUDGES`), plus `ATK.TWO_HAND_JUDGE_OFFSET` held two-handed.
R1_JUDGE = AT.FAMILY_JUDGES["r1"][0]
#: How many of the weapons best by hit `pick_weapon` also prices with their R1's windup interrupt
#: (one `INT.reverse` each, and the interrupt module's members of a weapon its cache lacks, about
#: 1 s, `MEASURED` 2026-11-12). `INFERRED`: a weapon past the fifth by hit is not reached.
SCREEN_TOP = 5


@dataclass(frozen=True)
class Spell:
    """One spell a line casts: its damage stat ('int' or 'fth'), its catalyst and the spell, and
    `share`, its share of the line's attacks."""
    main: str
    catalyst: str
    spell: str
    share: float = 1.0


@dataclass(frozen=True)
class Weapon:
    """The weapon a line's melee offense is one R1 hit of (`GameWorld.weapon_hit`): its regulation
    name, affinity, upgrade level and grip, and `share`, its share of the line's attacks."""
    name: str
    affinity: str = "Standard"
    level: int = 25
    two: bool = False
    share: float = 1.0


@dataclass(frozen=True)
class Line:
    """One line to fit. `main` is the damage stat ('int' or 'fth'), raised to `cap` with the points
    past it to STR and DEX (`SIM.allocate_line`'s spread); `catalyst` and `spell` are what the line
    casts. `casts` is the attacks thrown per engagement (`INFERRED`, the pouch's one attack per
    engagement by default). `carried` is the weight in the hands; None takes the kit's own
    (`carried_for`: its catalysts and its weapon).

    `kit` is the line's attacks (`Spell`, `Weapon`), each at its share of them; empty, the one spell
    `main`, `catalyst`, `spell` name. `mains` (a melee line's, or two schools') are raised evenly to
    `cap` by `SIM.allocate_line`, the points past them to STR and DEX, on the starting class whose
    kit hits hardest (`search`); None keeps the caster allocation (`allocation`).

    Constraints for comparison points: `vig`, `end`, `mnd` pin a stat; `poise_min` / `poise_max`
    bound the armor's own poise (`poise_max` exclusive); `wears` limits the HP pieces (None: every
    one); `lightest` replaces the frontier by `SIM.lightest_armor` at `poise_min` and Endurance by
    the least that keeps it at medium roll. `talismans` and `tears` fix the gear in `fit` (None:
    chosen by D)."""
    name: str
    main: str
    catalyst: str
    spell: str
    cap: int = SIM.DAMAGE_CAP
    casts: float = 1.0
    carried: float | None = None
    archetype: str | None = None
    crimson: str = "pool"
    contested: float = 1.0
    scenario: str = "carried"
    vig: int | None = None
    end: int | None = None
    mnd: int | None = None
    poise_min: float = 0.0
    poise_max: float = math.inf
    wears: tuple | None = None
    talismans: tuple | None = None
    tears: tuple | None = None
    lightest: bool = False
    mains: tuple | None = None
    kit: tuple = ()


def kit_of(line):
    """The line's attacks: `line.kit`, else the one spell its `main`, `catalyst`, `spell` name."""
    return tuple(line.kit) if line.kit else (Spell(line.main, line.catalyst, line.spell),)


def spells_of(line):
    return [k for k in kit_of(line) if isinstance(k, Spell)]


def weapons_of(line):
    return [k for k in kit_of(line) if isinstance(k, Weapon)]


#: The two pure lines. Each casts one 18-FP attack spell, a PvP staple of its school (`INFERRED`
#: representative; the report's spend rows cover more casts per engagement).
LINES = {
    "Intelligence": Line("Intelligence", "int", "Glintstone Staff+25", "Rock Sling", archetype="Intelligence"),
    "Faith": Line("Faith", "fth", "Finger Seal+25", "Lightning Spear", archetype="Faith"),
}


# ---------------------------------------------------------------------------------------------
# pure parts (the selftest checks them on synthetic numbers)

def fp_paid(N, s, bar, items, n_c, cerulean):
    """FP paid over `N` engagements that each want `s` FP: one bar, then the FP items between
    engagements, then `n_c` Ceruleans of min(`cerulean`, bar). At most one bar is spent in one
    engagement (the bar refills only between them and never past itself), an item refill cannot
    exceed what the engagement spent, and a drink lands on an empty bar (`INFERRED`: the closed
    form is an upper bound by at most one engagement's want per drink, `fp_paid_steps`). `items` is
    [(FP per gap, gaps its stock lasts)] (`INFERRED`: both items tick at once, each use back to
    back). Arrays broadcast."""
    s_cap = np.minimum(s, bar)
    gaps = np.maximum(np.asarray(N, float) - 1.0, 0.0)
    item = np.zeros_like(gaps)
    for rate, stock in items:
        item = item + rate * np.minimum(gaps, stock)
    item = np.minimum(item, gaps * s_cap)
    supply = bar + item + n_c * np.minimum(cerulean, bar)
    return np.minimum(N * s_cap, supply)


def fp_paid_steps(N, s, bar, items, n_c, cerulean):
    """`fp_paid` engagement by engagement (integer `N`), for the selftest: each engagement spends
    min(want, FP), the items refill the bar between engagements, and a Cerulean is drunk when what
    is left will not pay the next engagement."""
    f, paid, left = float(bar), 0.0, int(n_c)
    stock = [st for _, st in items]
    for i in range(int(N)):
        spend = min(s, f)
        paid += spend
        f -= spend
        if i == int(N) - 1:
            break
        add = 0.0
        for k, (rate, _) in enumerate(items):
            if stock[k] >= 1:
                add += rate
                stock[k] -= 1
        f = min(bar, f + min(add, spend))
        if f < s and left > 0:
            f = min(bar, f + cerulean)
            left -= 1
    return paid


def score(off, fp, casts, u, hp, hit, hits, bar, fight, crimson="pool", free=0.0):
    """D (module doc) over a last axis of Cerulean counts 0..flasks (capped by
    `fight['cerulean_cap']`), every other argument broadcast (`fp` too). `free` is the offense of
    the attacks that cost no FP (a weapon's R1), per attack thrown, its windup interrupt already in
    it: it is paid whatever the FP plan does. Returns (D, paid share, N, L, s), each with that last
    axis (L and s with length 1 there)."""
    F = int(fight["flasks"])
    cap = F if fight.get("cerulean_cap") is None else min(F, int(fight["cerulean_cap"]))
    nc = np.arange(cap + 1, dtype=float)
    win, att, E = fight["win"], fight["attackers"], fight["engagements"]
    heal = fight["heal"] * fight["eta"]
    L = ((1.0 - win) * att * np.asarray(hit, float) * np.asarray(hits, float))[..., None]
    u_ = np.asarray(u, float)[..., None]
    fp_ = np.asarray(fp, float)[..., None]
    s = casts * u_ * fp_
    bar_ = np.asarray(bar, float)[..., None]
    hp_ = np.asarray(hp, float)[..., None]
    off_ = np.asarray(off, float)[..., None]
    free_ = np.asarray(free, float)[..., None]
    crim = F - nc
    if crimson == "pool":
        # A Crimson heals 810 but never past max HP; it is drunk with `DRINK_AT` of an engagement's
        # drain left, so a bar under that drain plus 810 wastes the rest of the flask.
        drunk = np.clip(hp_ - DRINK_AT * L, 0.0, heal)
        N = np.minimum(E, (hp_ + crim * drunk) / np.maximum(L, 1e-9))
        paid = fp_paid(N, s, bar_, fight["items"], nc, fight["cerulean"])
        casts_paid = np.where(fp_ > 0, paid / np.maximum(fp_, 1e-12), N * casts * u_)
        D = win * off_ * casts_paid + win * free_ * N * casts
    elif crimson == "pouch":
        N = np.full(np.broadcast_shapes(L.shape, nc.shape, s.shape, bar_.shape), float(E))
        paid = fp_paid(N, s, bar_, fight["items"], nc, fight["cerulean"])
        per_eng = np.where(fp_ > 0, paid / np.maximum(fp_, 1e-12), N * casts * u_) / E
        h = crim * heal * fight["crimson_share"] / E
        life = hp_ / np.maximum(L, 1e-9) * (1.0 + h / np.maximum(L, 1e-9))
        D = win * (off_ * per_eng + free_ * casts) * life
        share = np.where(N * s > 0, paid / np.maximum(N * s, 1e-12), 1.0)
        return D, share, life, L, s
    else:
        raise ValueError(f"crimson must be 'pool' or 'pouch', not {crimson!r}")
    share = np.where(N * s > 0, paid / np.maximum(N * s, 1e-12), 1.0)
    return D, share, N, L, s


def chain_hits(seqs, w, P, carried=True):
    """(hits landed per pool attack, share whose first hit breaks) at menu poise `P` (array), over
    the pool's attack sequences `seqs` weighted `w` (`INT.PoolRows`). The first hit breaks as
    `INT.chip_states` has it (carried: the chip of the steady-state cycle of that attacker's own
    first hit; fresh: full poise). A first hit that does not break is all that lands (`INFERRED`:
    the defender acts out of it). A break holds the defender for the string's next hit, and each
    hit that lands on refilled poise and breaks it again holds him for the one after (`INFERRED`:
    the stagger outlasts the gap to the next hit; poise refills at a break, `CSChrToughnessModule`,
    interrupt.md section 2). Every landed hit counts as the pool's mean hit (`INFERRED`)."""
    P = np.asarray(P, float)
    landed = np.zeros_like(P)
    broke = np.zeros_like(P)
    for s, wt in zip(seqs, w):
        d = [p for _, p in s["hits"]]
        chip, cw = INT.chip_states(P, np.full(P.shape, d[0]), carried)
        b1 = ((chip + d[0] >= P[..., None] - 1e-9) * cw).sum(-1)
        after = np.ones_like(P) if len(d) > 1 else np.zeros_like(P)
        run = np.ones(P.shape, bool)
        for x in d[1:-1]:
            run &= x >= P - 1e-9
            after = after + run
        landed += wt * (1.0 + b1 * after)
        broke += wt * b1
    return landed, broke


def even_fill(a, b, k):
    """(a', b'): `k` points spread over two stats from `a`, `b` the way `SIM.allocate_line` spreads
    them, each point to the lower one, ties to the first. Arrays broadcast."""
    a, b, k = (np.asarray(x, float) for x in (a, b, k))
    lo_a = a <= b
    first = np.minimum(k, np.abs(a - b))
    rest = k - first
    a2 = np.where(lo_a, a + first, a) + np.ceil(rest / 2.0)
    b2 = np.where(lo_a, b, b + first) + np.floor(rest / 2.0)
    return a2, b2


def _reconstruct(fr, w, p):
    """The four pieces at frontier cell (weight bin `w`, poise `p`), backtracked through its choice
    tables the way `AT.Frontier.pick` does (here for a cell whose poise is bounded above too)."""
    pieces = []
    for c in (3, 2, 1, 0):
        piece = fr.cands[c][int(fr.choice[c][w, p])]
        pieces.append(piece)
        w -= int(round(piece["weight"] / fr.step))
        p -= int(round(piece["poise"]))
    pieces.reverse()
    return pieces


# ---------------------------------------------------------------------------------------------
# the game's side of every term

class Gear:
    """What one talisman set (and the physick and consumable modifiers) does, read once: weight,
    the stat adds, the damage cut per type, the poise divisor, the school's FP cost rate, and lazily
    the HP, FP and equip-load curves and the spell's hit per main stat."""

    def __init__(self, world, line, talismans=(), mods=()):
        self.world, self.line, self.talismans = world, line, tuple(talismans)
        self.mods = tuple(mods)
        t = world.t
        self.weight = sum(t.talisman_row(n)["weight"] for n in self.talismans)
        rows = [i for n in self.talismans for i in world.talisman_rows(n)]
        # A talisman row that keeps re-applying another (`cycleOccurrenceSpEffectId` every
        # `motionInterval` s, the child living at least that long) keeps the child on as long as it
        # is worn: Beloved Stardust's 20381900 cycles 20381901, x1.3 damage taken, every 0.06 s.
        # `DEF.defender` reads the talisman's own rows only, so the children go in as extras.
        cycled = world.cycled(rows)
        me = DEF.defender(t, world.ref, (), list(self.talismans), None, pvp=True, extra_speffects=cycled)
        self.eff = np.array([me["effect_mult"][DEF_TYPE[ty]] for ty in AT.TYPES])
        self.tcut = me["toughness_cut"]
        self.adds = {k: me["effective_stats"][k] - world.ref[k] for k in ATTRS}
        # The FP cost rate of each school the kit casts.
        self.fp_rates = {}
        for school in {k.main for k in spells_of(line)}:
            rate = 1.0
            for i in rows + cycled:
                row = world.sp_all.get(i)
                if row is not None:
                    rate *= row[CONSUMPTION[school]]
            self.fp_rates[school] = rate
        self.fp_rate = self.fp_rates.get(line.main, 1.0)
        self._parts = None
        # Uptime-weighted modifiers from physick tears and consumables (`GameWorld.mod_sources`):
        # incoming damage x `kept`, poise x `poise_x`, the spell x `rate_x`, the main stat +
        # `add_main`, its FP cost x `fp_x`, each while up; offense x `time` (the use frames).
        self.kept, self.poise_mix, self.off_mix, self.fp_mix, self.time = 1.0, [], [], 1.0, 1.0
        for m in self.mods:
            up = m["uptime"]
            self.kept *= 1.0 - up * (1.0 - m.get("kept", 1.0))
            if abs(m.get("poise_x", 1.0) - 1.0) > 1e-9:
                self.poise_mix.append((up, m["poise_x"]))
            if abs(m.get("rate_x", 1.0) - 1.0) > 1e-9 or m.get("add_main", 0):
                self.off_mix.append((up, m.get("rate_x", 1.0), int(m.get("add_main", 0))))
            self.fp_mix *= 1.0 - up * (1.0 - m.get("fp_x", 1.0))
            self.time *= m["time"]
        self._hp, self._fp, self._load = {}, None, None

    def spell_hit(self, main_eff, off=None):
        """The line's spell's one connect at effective main stat `main_eff` (array), the offense
        tears mixed in by their uptime (`off_mix`) and the use frames charged (`time`)."""
        hit = (off or self.offense())["hit"]
        combos = [(1.0, 1.0, 0)]
        for up, x, add in self.off_mix:
            combos = [(p * up, r * x, a + add) for p, r, a in combos] + [(p * (1 - up), r, a) for p, r, a in combos]
        out = 0.0
        for p, r, a in combos:
            out = out + p * r * hit[np.clip(np.asarray(main_eff) + a, 0, len(hit) - 1).astype(int)]
        return out * self.time

    def fp_cast(self):
        return self.offense()["fp"] * self.fp_mix

    def own(self, k):
        """Whether attack `k` is the line's own spell, the one its tears are read for."""
        ln = self.line
        return isinstance(k, Spell) and (k.main, k.catalyst, k.spell) == (ln.main, ln.catalyst, ln.spell)

    def parts(self):
        """[(attack, its offense)] over the line's kit (`kit_of`): a spell's `GameWorld.spell_offense`
        at its school's FP rate, a weapon's `GameWorld.weapon_offense`."""
        if self._parts is None:
            self._parts = [(k, self.world.spell_offense(k, self.talismans, self.fp_rates.get(k.main, 1.0))
                            if isinstance(k, Spell) else self.world.weapon_offense(k)) for k in kit_of(self.line)]
        return self._parts

    def hit_of(self, k, off, eff, ok=None):
        """Attack `k`'s one hit at effective damage stats `eff` ({stat: array}): a weapon's
        (`GameWorld.weapon_hits`), a spell's at its school's stat, nothing where `ok` (its and its
        catalyst's requirements met) is False. The line's own spell takes the offense tears; every
        attack pays the use frames (`time`)."""
        if isinstance(k, Weapon):
            return self.world.weapon_hits(k, eff) * self.time
        x = np.asarray(eff[k.main])
        if self.own(k):
            h = self.spell_hit(x, off)
        else:
            h = off["hit"][np.clip(x, 0, len(off["hit"]) - 1).astype(int)] * self.time
        return h if ok is None else np.where(ok, h, 0.0)

    def fp_of(self, k, off):
        """FP one throw of attack `k` costs: a spell's at its school's rate (the line's own with its
        tears' too), a weapon's R1 none."""
        if isinstance(k, Weapon):
            return 0.0
        return off["fp"] * (self.fp_mix if self.own(k) else 1.0)

    def _curve(self, stat, field, armor=()):
        ten = {k: 10 for k in ATTRS}
        res = self.world.res
        return np.array([0.0] + [float(res.resources(dict(ten, **{stat: v}), self.talismans, list(armor))[field])
                                 for v in range(1, STAT_MAX + 1)])

    def hp(self, wear):
        """Max HP per base Vigor 0..99 wearing `wear` (and these talismans)."""
        if wear not in self._hp:
            self._hp[wear] = self._curve("vig", "max_hp", [wear] if wear else [])
        return self._hp[wear]

    def fp(self):
        if self._fp is None:
            self._fp = self._curve("mnd", "max_fp")
        return self._fp

    def load(self):
        if self._load is None:
            self._load = self._curve("end", "max_equip_load")
        return self._load

    def offense(self):
        return self.world.offense(self.line, self.talismans, self.fp_rate)

    def poise_curves(self, P, carried, u_curve):
        """(hits per pool attack, first-hit break share, uninterrupted share) at the armor's menu
        poise `P` under this gear, each poise source mixed in by its uptime."""
        combos = [(1.0, 1.0)]
        for up, x in self.poise_mix:
            combos = [(p * up, m * x) for p, m in combos] + [(p * (1 - up), m) for p, m in combos]
        H = B = U = 0.0
        for p, m in combos:
            Pm = np.asarray(P, float) * m
            h, b = self.world.hits_at(Pm, carried)
            H, B, U = H + p * h, B + p * b, U + p * u_curve(Pm)
        return H, B, U


class GameWorld:
    """The regulation, the RL window's opponent pool and the fight, loaded once (37.6 s measured
    2026-11-12)."""

    def __init__(self, rl=150, window=10, fight="invasion"):
        self.rl, self.window = rl, window
        lo, hi = rl - window, rl + window
        self.total = rl + LEVEL_OFFSET
        self.res = RES.Model()
        #: The stat block the armor frontier's damage mix and the HP pieces are read at: the
        #: Intelligence line's current shape. Flat defense, HP and FP are read per allocation.
        self.ref = {"vig": 60, "mnd": 15, "end": 25, "str": 10, "dex": 12, "int": 80, "fth": 7, "arc": 9}
        self.armor = AT.ArmorModel(self.ref, (), None, rl, window)
        self.t = self.armor.t
        self.floor = self.armor.melee_floor
        # The pool, and the interrupt module's members, delays and entry frames a weapon's R1 is
        # resolved with (`r1_seq`).
        self.rows, self.int_weapons, self.int_delays, self.int_entries, _ = AT.pool_rows(lo, hi)
        P = _mod("er_mechanics_pouch", "er-mechanics-pouch.py")
        self.P, self.B = P, P.B
        self.pouch = P.Pouch(rl, window, fight=fight)
        self.SP, self.sm = self.pouch.spell_model()
        self.dfn = self.sm.defender(rl)
        self.reg = AT.ATK.Regulation(None)
        win = P.FIGHT_WIN_SHARE
        self.fight = {"kind": fight, "win": win, "attackers": self.pouch.attackers,
                      "engagements": self.pouch.crimson_hits / win, "crimson_share": self.pouch.crimson_share,
                      "flasks": P.FLASK_TOTAL, "heal": P.B.FLASK_HEAL_HP, "eta": P.B.FLASK_ETA,
                      "cerulean": P.CERULEAN_FP, "cerulean_cap": None, "items": [], "item_names": [],
                      "engagement_s": P.B.ENGAGEMENT_SECONDS}
        if fight == "duel":
            self.fight["cerulean_cap"] = _mod("er_builds_ladder", "er-builds-ladder.py").CERULEAN_FLASKS
        gap = self.fight["engagement_s"]
        self.role = P.B.FIGHT_KINDS[fight]["role"]
        for key, c in sorted(self.pouch.cands.items()):
            if c.get("fp_rows") and c["kind"] == "consumable" and self.role in c.get("roles", {self.role}):
                uses = self.pouch._limit(key, [key])[0]
                for sid in c["fp_rows"]:
                    pt, pct, iv, d = self.pouch.fp_row(sid)
                    if iv <= 0 or pct:
                        continue
                    self.fight["items"].append((pt / iv * gap, uses * d / gap))
                    self.fight["item_names"].append(f"{c['name']}: {pt / iv:g} FP/s for {d:g} s, {uses} uses")
        self.classes = {c: self.res.class_base(c)[1] for c in RES.CLASS_ROWS}
        files = DEF.PR.load(None)
        self.sp_all = {r["id"]: r for r in DEF.PR.rows(DEF.PR.param_bytes(files, "SpEffectParam"), None)[0]}
        self.goods = {r["id"]: r for r in DEF.PR.rows(DEF.PR.param_bytes(files, "EquipParamGoods"),
                                                       ["goodsType", "refId_default", "sortId"])[0]}
        self._tal_why, self._tear_why = {}, {}
        att = self.armor.attacks
        self._per_el = {}
        for i in range(len(AT.ELEMENTS)):
            m = att["el"] == i
            tab = np.zeros((401, len(AT.TYPES)))
            for d in range(401):
                left = AT.defense_curve_np(att["atk"][m], float(d))
                tab[d] = np.bincount(att["key"][m], weights=att["w"][m] * left, minlength=len(AT.TYPES))
            self._per_el[i] = tab
        g = self.t.graph
        self._lvl_of = {}
        self._lvl_graph = g[DEF.LEVEL_DEF_GRAPH]
        self._st = [np.array([DEF.calc_correct(g[DEF.STAT_DEF_GRAPH[el][1]], x) if DEF.STAT_DEF_GRAPH[el] else 0.0
                              for x in range(160)]) for el in AT.ELEMENTS]
        self._hits, self._u, self._gear, self._sets, self._wear_adds, self._off = {}, {}, {}, {}, {}, {}
        self._whit, self._seq = {}, {}
        self.grid = np.arange(POISE_MAX + 1, dtype=float)
        self._mods = None
        ten = {k: 10 for k in ATTRS}
        #: The knee of max HP (`SIM.vigor_knee`), where a weapon is picked before a fit has run.
        self.knee = SIM.vigor_knee(lambda v: self.res.resources(dict(ten, vig=v))["max_hp"])[0]

    # -- the pool's side -------------------------------------------------------------------------
    def hits_at(self, P, carried):
        """`chain_hits` at poise `P`, read off the integer grid (linear between points)."""
        if carried not in self._hits:
            self._hits[carried] = chain_hits(self.rows.seqs, self.rows.seq_w, self.grid, carried)
        h, b = self._hits[carried]
        P = np.clip(np.asarray(P, float), 0, POISE_MAX)
        return np.interp(P, self.grid, h), np.interp(P, self.grid, b)

    def u_curve(self, seq, carried, contested):
        """P -> 1 - `contested` x `INT.reverse` share for cast `seq` (`U_STEP` grid, linear)."""
        key = (round(seq["strike"], 2), tuple(tuple(round(float(x), 3) for x in w) for w in seq["windows"]), carried)
        if key not in self._u:
            pts = np.arange(0, POISE_MAX + 1, U_STEP, dtype=float)
            keep = self.rows.my_poise
            vals = []
            try:
                for P in pts:
                    self.rows.my_poise = np.array([P])
                    vals.append(INT.reverse(self.rows, seq, carried))
            finally:
                self.rows.my_poise = keep
            self._u[key] = (pts, np.array(vals))
        pts, vals = self._u[key]
        return lambda P: 1.0 - contested * np.interp(np.clip(np.asarray(P, float), 0, POISE_MAX), pts, vals)

    # -- defense ---------------------------------------------------------------------------------
    def _lvl(self, level):
        if level not in self._lvl_of:
            self._lvl_of[level] = DEF.calc_correct(self._lvl_graph, level)
        return self._lvl_of[level]

    def incoming(self, level, stats, cut):
        """The mean damage one pool attack deals (`AT.pool_hit`'s sum): per set s, the pool's attacks
        after this build's flat defense, times absorption `cut[s]` (`AT.TYPES` columns). `stats`
        {'str', 'int', 'vig', 'end', 'arc'} are effective values broadcast to (..., S), `level` (S,)
        their level sum. Flat defense is `DEF.defender`'s, the truncated level graph plus stat graph
        (the selftest checks it against `DEF.defender`)."""
        level = np.asarray(level)
        lvl = np.array([self._lvl(int(x)) for x in level.ravel()]).reshape(level.shape)
        cut = np.atleast_2d(cut)
        sidx = np.arange(cut.shape[0])
        out = 0.0
        for i, st in enumerate(ELEMENT_STAT):
            x = np.clip(np.asarray(stats[st]).astype(int), 0, len(self._st[i]) - 1)
            d = np.clip(np.trunc(lvl + self._st[i][x]).astype(int), 0, 400)
            out = out + (self._per_el[i] @ cut.T)[d, sidx]
        return out

    def wear_adds(self, wear):
        """Stat adds of HP piece `wear` (its resident SpEffects, `DEF.defender`)."""
        if wear not in self._wear_adds:
            me = DEF.defender(self.t, self.ref, [wear] if wear else [], (), None, pvp=True)
            self._wear_adds[wear] = {k: me["effective_stats"][k] - self.ref[k] for k in ATTRS}
        return self._wear_adds[wear]

    def sets(self, budget, wear, pmin=0.0, pmax=math.inf):
        """The frontier's sets within `budget` with `wear`, one per poise it steps to: at each poise
        floor from `pmin` the least-kept set (`AT.Frontier`'s objective) whose poise is under
        `pmax`. Each {'pieces', 'weight', 'armor_poise', 'cut' (`AT.TYPES` array), 'eff' (the
        pieces' resident SpEffect cut), 'tcut', 'adds', 'wear'}."""
        key = (round(float(budget), 2), wear, pmin, pmax)
        if key in self._sets:
            return self._sets[key]
        out = []
        fr = self.armor._frontier(wear)
        wb = min(fr.W - 1, int(math.floor(budget / fr.step + 1e-9))) if budget >= 0 else -1
        if wb >= 0:
            sub = fr.best[: wb + 1]
            col, arg = sub.min(0), sub.argmin(0)
            hi = fr.pmax + 1 if math.isinf(pmax) else min(fr.pmax + 1, int(math.ceil(pmax)) + 1)
            f = max(0, int(math.ceil(pmin - 1e-9)))
            while f < hi:
                seg = col[f:hi]
                if not np.isfinite(seg).any():
                    break
                p = f + int(np.argmin(seg))
                pieces = _reconstruct(fr, int(arg[p]), p)
                o = {"pieces": [x["name"] for x in pieces], "weight": round(sum(x["weight"] for x in pieces), 1),
                     "armor_poise": round(sum(x["poise"] for x in pieces), 1),
                     "cut": {ty: math.prod(x["cut"][ty] for x in pieces) for ty in AT.TYPES}, "wear": wear}
                if pmin <= o["armor_poise"] < pmax:
                    out.append(self._set(o))
                f = p + 1
        self._sets[key] = out
        return out

    def _set(self, o):
        names = [n for n in o["pieces"] if n != "-"]
        me = DEF.defender(self.t, self.ref, names, (), None, pvp=True)
        return {"pieces": o["pieces"], "weight": o["weight"], "armor_poise": o["armor_poise"],
                "cut": np.array([o["cut"][ty] for ty in AT.TYPES]), "wear": o.get("wear"),
                "eff": np.array([me["effect_mult"][DEF_TYPE[ty]] for ty in AT.TYPES]), "tcut": me["toughness_cut"],
                "adds": {k: me["effective_stats"][k] - self.ref[k] for k in ATTRS}}

    def named_set(self, names):
        """A set given by piece names (`SIM.lightest_armor`), in `sets`' shape."""
        cands = AT.armor_candidates(self.t)
        pieces = [p for c in range(4) for p in cands[c] if p["name"] in names]
        o = {"pieces": [p["name"] for p in pieces], "weight": round(sum(p["weight"] for p in pieces), 1),
             "armor_poise": round(sum(p["poise"] for p in pieces), 1),
             "cut": {ty: math.prod(p["cut"][ty] for p in pieces) for ty in AT.TYPES}}
        return self._set(o)

    # -- offense ---------------------------------------------------------------------------------
    def offense(self, line, talismans, fp_rate):
        """`spell_offense` for the line's own spell (`main`, `catalyst`, `spell`)."""
        return self.spell_offense(Spell(line.main, line.catalyst, line.spell), talismans, fp_rate)

    def spell_offense(self, sp, talismans, fp_rate):
        """{'hit', 'buff' (per effective main stat 0..130), 'fp' (per cast, the talismans' cost rate
        applied), 'fp_row', 'startup', 'variant', 'seq'}: spell `sp`'s one connect on the window's
        median defender and the catalyst buff at that main stat, the other damage stats at 10 and
        Dexterity at 10 for the casting speed (`INFERRED`: the line's own Dexterity is 10 to 14), and
        the cast as an interrupt sequence (`cast_seq`). Kept per what the talismans do to the spell
        (`SpellModel.rates`), so a talisman that leaves it alone reads it once."""
        sm = self.sm
        cat = sm.catalyst(sp.catalyst)
        mid = sm.by_name[self.SP.T._norm(sp.spell)]
        rate, correct, bonus = sm.rates(mid, cat, tuple(talismans))
        key = (sp.spell, sp.catalyst, tuple(sorted(rate.items())), tuple(sorted(correct.items())), bonus,
               round(fp_rate, 6))
        if key not in self._off:
            base = {"str": 10, "dex": 10, "int": 10, "fth": 10, "arc": 10}
            hits, buffs, first = [], [], None
            for x in range(0, 131):
                st = dict(base, **{sp.main: x})
                v = sm.evaluate(mid, cat, st, list(talismans), defender=self.dfn)
                if not v:
                    raise SystemExit(f"{sp.spell} has no damaging variant from {sp.catalyst}")
                first = first or v[0]
                hits.append(v[0]["one_hit"])
                buffs.append(100.0 * sm.multiplier(cat, BUFF_ELEMENT[sp.main], st))
            self._off[key] = {"hit": np.array(hits), "buff": np.array(buffs), "fp": first["fp"] * fp_rate,
                              "fp_row": first["fp"], "startup": first["startup"], "variant": first["variant"],
                              "seq": self.cast_seq(mid, cat, first), "mid": mid}
        return self._off[key]

    def spell_need(self, sp):
        """{stat: least base value} spell `sp` is cast at: its own requirement and its catalyst's (a
        catalyst whose requirement is not met casts nothing, `COMMUNITY` as attack-similarity reads
        it)."""
        key = ("need", sp.spell, sp.catalyst)
        if key not in self._off:
            cat = self.sm.catalyst(sp.catalyst)["wep"]
            mg = self.sm.magic[self.sm.by_name[self.SP.T._norm(sp.spell)]]
            need = {k: cat[f] for k, f in SIM.WEAPON_NEED.items()}
            for k, f in (("int", "requirementIntellect"), ("fth", "requirementFaith"), ("arc", "requirementLuck")):
                need[k] = max(need[k], mg.get(f, 0) or 0)
            self._off[key] = need
        return self._off[key]

    def spell_ok(self, sp, dm):
        """Whether damage stats `dm` ({stat: array}, base values) cast spell `sp` (`spell_need`)."""
        need = self.spell_need(sp)
        out = True
        for k, v in need.items():
            out = out & (np.asarray(dm[k]) >= v)
        return out

    def weapon_row(self, w):
        """(base row id, affinity row id) of weapon `w` in `EquipParamWeapon`."""
        base = self.sm.ar.find_weapon(w.name, "Standard")
        return base, base + OPT.AR.AFFINITIES.index(w.affinity) * 100

    def weapon_offense(self, w):
        """{'fp' 0, 'variant' 'r1', 'weight', 'base', 'wid', 'mv', 'vs', 'final', 'phys', 'atk_row'}:
        what one R1 hit of weapon `w` reads (`weapon_hit`): its R1's first judge
        (`R1_JUDGE`, two-handed with `ATK.TWO_HAND_JUDGE_OFFSET`) as `ATK.attack_numbers` resolves it
        on the affinity row, the motion value per element, the weapon's vsPlayer rate per element
        (`er-mechanics-ashes.pvp_damage`'s rule), the hit's FinalDamageRateParam row and physical
        type. Its R1 as an interrupt sequence is `r1_seq`, read only when a term needs it."""
        key = ("weapon", w.name, w.affinity, w.level, w.two)
        if key not in self._off:
            A, t = self.sm.ash()
            base, wid = self.weapon_row(w)
            judge = R1_JUDGE + (AT.ATK.TWO_HAND_JUDGE_OFFSET if w.two else 0)
            n = AT.ATK.attack_numbers(t.reg, wid, judge, w.level)
            if n is None or not n["can_hit_enemy"]:
                raise ValueError(f"{w.name} ({w.affinity}) has no R1 that hits an enemy")
            row = t.reg.weapon[wid]
            fid = (t.atk_extra.get(n["atk_row"]) or {}).get("finalDamageRateId", -1)
            self._off[key] = {"fp": 0.0, "fp_row": 0.0, "variant": "r1", "weight": float(self.sm.ar.weapons[wid]["weight"]),
                              "base": base, "wid": wid, "atk_row": n["atk_row"], "phys": n["phys_type"],
                              "mv": {el: n[AT.MV_KEY[el]] / 100.0 for el in AT.ELEMENTS},
                              "vs": {el: row.get(f"vsPlayerDmgCorrectRate_{A.VS_PLAYER[el]}", 1.0) for el in AT.ELEMENTS},
                              "final": t.final_rate.get(fid) if fid is not None and fid >= 0 else None}
        return self._off[key]

    def weapon_hit(self, w, stats):
        """One R1 hit of weapon `w` at damage stats `stats` ({stat: int}) on the window's median
        defender (`SpellModel.defender`, the one the spells are read on): the attack rating per
        element (`er-mechanics-ar.attack_rating`, its 40% requirement penalty included), times the
        R1's motion value and the weapon's vsPlayer rate per element, through `DEF.damage` with the
        hit's physical type and FinalDamageRateParam row."""
        st = tuple(int(np.clip(stats[k], 1, 148)) for k in DMG)
        key = (w.name, w.affinity, w.level, w.two) + st
        if key not in self._whit:
            o = self.weapon_offense(w)
            r = OPT.AR.attack_rating(self.sm.ar, o["base"], w.affinity, w.level, dict(zip(DMG, st)), w.two)
            atk = {el: r["damage"].get(el, {}).get("total", 0.0) * o["mv"][el] * o["vs"][el] for el in AT.ELEMENTS}
            self._whit[key] = float(DEF.damage(atk, 100.0, self.dfn, o["phys"], o["final"])["total"])
        return self._whit[key]

    def weapon_hits(self, w, eff):
        """`weapon_hit` over arrays of damage stats `eff` ({stat: array}, broadcast), read once per
        distinct stat block."""
        arrs = np.broadcast_arrays(*(np.asarray(eff[k]) for k in DMG))
        flat = np.stack([a.ravel() for a in arrs], 1).astype(int)
        if not len(flat):
            return np.zeros(arrs[0].shape)
        uniq, inv = np.unique(flat, axis=0, return_inverse=True)
        vals = np.array([self.weapon_hit(w, dict(zip(DMG, u))) for u in uniq])
        return vals[np.asarray(inv).ravel()].reshape(arrs[0].shape)

    def r1_seq(self, w):
        """Weapon `w`'s R1 as the interrupt module times it (`INT.resolve` 'r1': the string from the
        press, its TAE 795 windows, the first hit at front contact): from the members cache the
        pool is read with, else extracted as that cache's builder extracts it (`INT._worker_members`,
        about 1 s a weapon, `MEASURED` 2026-11-12)."""
        grip = "both" if w.two else "one"
        key = (w.name, grip)
        if key not in self._seq:
            mem = self.int_weapons.get(f"{w.name}|{grip}")
            if not mem or "members" not in mem:
                if not INT._W:
                    INT._worker_init()
                _, mem = INT._worker_members((w.name, grip))
            if not mem or "members" not in mem:
                raise ValueError(f"no attack members for {w.name} ({grip}): {(mem or {}).get('error')}")
            seqs = INT.resolve(mem, grip, w.name, self.int_delays, self.int_entries, True)
            if "r1" not in seqs:
                raise ValueError(f"{w.name} ({grip}) has no R1 sequence")
            self._seq[key] = seqs["r1"]
        return self._seq[key]

    def seq_of(self, k, off):
        """The interrupt sequence of attack `k`: a spell's cast (`cast_seq`), a weapon's R1."""
        return self.r1_seq(k) if isinstance(k, Weapon) else off["seq"]

    def scaling_tag(self, row):
        """The planner's weapon archetype of an `EquipParamWeapon` affinity row
        (`er-mechanics-build-tags.SCALING_RULES` on its `correct*` fields, squared shares), or None."""
        BT = SIM._build_tags()
        sq = {k: (row[BT.CORRECT[k]] / 100.0) ** 2 for k in DMG}
        whole = sum(sq.values())
        if not whole:
            return None
        share = {k: v / whole for k, v in sq.items()}
        return next((tag for tag, rule in BT.SCALING_RULES if rule(share)), None)

    def weapon_candidates(self, dmg, tag=None):
        """[(hit, Weapon)], hardest first: every weapon a player holds (`er-builds-optimize`'s rule:
        named, in the game's own message files, not ammunition) whose class has a melee R1 (not
        `NOT_MELEE`), at each affinity it takes and each grip, whose requirements damage stats `dmg`
        meet (no negative scaling, `er-builds-optimize.weapons_for`'s test) and, with `tag`, whose
        affinity row's scaling archetype (`scaling_tag`) is `tag`: per weapon, its affinity and grip
        of hardest R1 hit (`weapon_hit`)."""
        ar = self.sm.ar
        real = OPT._real_weapon_ids()
        skip = set(OPT.AMMO_WEP_TYPES) | set(NOT_MELEE)
        st = {k: int(dmg[k]) for k in DMG}
        best = {}
        for base, wep in sorted(ar.weapons.items()):
            if base % 10000 or (real and base not in real) or wep.get("wepType") in skip:
                continue
            name = ar.names.get(base)
            if not name:
                continue
            for aff in OPT.affinities(ar, base):
                row = ar.weapons[base + OPT.AR.AFFINITIES.index(aff) * 100]
                if tag is not None and self.scaling_tag(row) != tag:
                    continue
                level = ar.max_level(row["reinforceTypeId"])
                for two in (False, True):
                    try:
                        r = OPT.AR.attack_rating(ar, base, aff, level, st, two)
                        if any(v.get("scaling", 0.0) < 0 for v in r["damage"].values()):
                            continue
                        w = Weapon(name, aff, level, two)
                        h = self.weapon_hit(w, st)
                    except (KeyError, ValueError, TypeError, SystemExit):
                        continue
                    if name not in best or h > best[name][0]:
                        best[name] = (h, w)
        return sorted(best.values(), key=lambda x: (-x[0], x[1].name))

    def cast_seq(self, mid, cat, v):
        """The cast as an `INT.reverse` sequence: strike at `startup` (the press to the first damaging
        cast event, at this casting value), and the TAE 795 windows of the start clip up to the
        hand-off and of the release clip after it, read as `INT._window` reads a weapon's (the
        catalyst's `toughnessCorrectRate`)."""
        sm, SP, ATK = self.sm, self.SP, AT.ATK
        refcat = SP.SPELL_TAE_BASE + sm.magic[mid]["refType"]
        hand = v["startup"] - min(h["frame"] for h in v["hits"])
        lead = v.get("lead") or 0.0
        clips = [(SP.START, 0.0, hand)]
        if v["anim"] == SP.TAP and lead:
            clips += [(SP.RELEASE, hand, hand + lead), (SP.TAP, hand + lead, math.inf)]
        else:
            clips.append((v["anim"], hand, math.inf))
        wep = self.reg.weapon.get(cat["id"]) or self.reg.weapon.get(cat["base"]) or cat["wep"]
        wins = []
        for anim, off, end in clips:
            _, ev = sm._events(refcat, anim)
            if not ev:
                continue
            real = ATK.clip_to_real(ev)
            for e in ev:
                if e.type != ATK.TAE_TOUGHNESS:
                    continue
                ratio = float(np.frombuffer(bytes(e.params[4:8]), "<f4")[0])
                s0 = off + ATK.real_frame(real(e.start))
                s1 = off + ATK.real_frame(real(e.end))
                if s0 >= end:
                    continue
                wins.append(INT._window(self.reg, wep, e.params[0], e.params[1], ratio, (s0, min(s1, end))))
        return {"hits": [(v["startup"], 0.0)], "strike": v["startup"], "windows": wins, "invuln": [],
                "family": "cast", "d1": 0.0}

    # -- gear ------------------------------------------------------------------------------------
    def gear(self, line, talismans=(), mods=()):
        key = (line.main, line.catalyst, line.spell, kit_of(line), tuple(talismans),
               tuple((m["name"], round(m["uptime"], 4), round(m["time"], 4)) for m in mods))
        if key not in self._gear:
            self._gear[key] = Gear(self, line, talismans, mods)
        return self._gear[key]

    def talisman_candidates(self, line):
        """Every named talisman whose rows move a term D reads: max HP, FP or equip load, a stat, a
        damage cut, the poise divisor, the line's spell (its rates or casting speed,
        `SpellModel.rates`) or its FP cost. One that does nothing but weigh is left out."""
        key = ("tal", line.main, line.catalyst, line.spell)
        if key in self._off:
            return self._off[key]
        names = DEF.PR.row_names("EquipParamAccessory")
        seen, out = set(), []
        sm = self.sm
        casts = bool(spells_of(line))
        cat = sm.catalyst(line.catalyst) if casts else None
        mid = sm.by_name[self.SP.T._norm(line.spell)] if casts else None
        ten = {k: 10 for k in ATTRS}
        bare = self.res.resources(ten)
        r0 = sm.rates(mid, cat, ()) if casts else None
        for rid, nm in sorted(names.items()):
            if not nm or nm.startswith("[") or nm in seen or rid not in self.t.accessory:
                continue
            seen.add(nm)
            gated = self.conditional_spell_rows(nm)
            if gated:
                self._tal_why[nm] = "its spell rows need " + ", ".join(sorted({g for _, g in gated})) + \
                                    f" ({', '.join(str(i) for i, _ in gated)}): no share of casts meets it here"
                continue
            try:
                g = Gear(self, line, (nm,))
                r = self.res.resources(ten, (nm,))
                rr = sm.rates(mid, cat, (nm,)) if casts else None
            except (KeyError, TypeError, ValueError):
                continue
            moved = (any(r[k] != bare[k] for k in ("max_hp", "max_fp", "max_equip_load"))
                     or any(g.adds.values()) or bool(np.any(np.abs(g.eff - 1) > 1e-9)) or abs(g.tcut - 1) > 1e-9
                     or abs(g.fp_rate - 1) > 1e-9 or rr != r0)
            if moved:
                out.append(nm)
        self._off[key] = out
        return out

    def group_of(self, name):
        g = self.t.talisman_row(name)["accessoryGroup"]
        return g if g is not None and g >= 0 else None

    def talisman_rows(self, name):
        r = self.t.talisman_row(name)
        return [i for i in (r["refId"], r["residentSpEffectId1"], r["residentSpEffectId2"]) if i and i > 0]

    def cycled(self, rows):
        """The rows `rows` keep on by re-applying them (`cycleOccurrenceSpEffectId` every
        `motionInterval` s, the child's `effectEndurance` at least that long), one level down."""
        out = []
        for i in rows:
            r = self.sp_all.get(i)
            if not r:
                continue
            c, iv = r.get("cycleOccurrenceSpEffectId", -1), r.get("motionInterval", 0.0)
            child = self.sp_all.get(c) if c and c > 0 else None
            if child and iv > 0 and child.get("effectEndurance", 0.0) >= iv - 1e-6:
                out.append(c)
        return out

    def conditional_spell_rows(self, name):
        """The talisman's rows that change a spell's attack and are gated on a state no term here
        has a share for: a counter hit (`COUNTER_STATE`) or an HP condition (`conditionHp` /
        `conditionHpRate`, Ritual Sword's full HP). `SpellModel.rates` applies them as if always
        met, so the fit leaves such a talisman out (`talisman_candidates`)."""
        out = []
        for i in self.talisman_rows(name) + self.cycled(self.talisman_rows(name)):
            r = self.sp_all.get(i) or {}
            moves = any(abs(r.get(f, 1.0) - 1.0) > 1e-9 for f in r if f.endswith("AttackRate")
                        or f.startswith("atkPlayerDmgCorrectRate_"))
            gated = r.get("stateInfo") == COUNTER_STATE or r.get("conditionHp", -1) >= 0 or r.get("conditionHpRate", -1) >= 0
            if moves and gated:
                out.append((i, "counter hit" if r.get("stateInfo") == COUNTER_STATE else "HP condition"))
        return out

    def _source_rows(self, name, key=None):
        try:
            return list(self.pouch._rows(key or name, self.role))
        except (KeyError, TypeError, ValueError):
            return []

    def stack_ok(self, mods):
        """Whether every SpEffect row of `mods`, used in that order, stays live through
        `Buffs.stack` (the exclusive families keep one live entry: two stews, a Pickled and a Dried
        Liver of one element, do not both count)."""
        m = self.pouch.m
        entries = [(sid, x["name"]) for x in mods for sid in x["rows"] if sid in m.sp]
        return len(m.stack(entries)) == len(entries)

    def mod_sources(self, line):
        """Physick tears and consumables, each a dict with its uptime inputs ('seconds', 'uses',
        'frames'), its SpEffect 'rows' and what it does while up:
        - 'kept', 'poise_x': the damage kept on the window's mix and the poise multiplier
          (`AT.buff_table`, the sources that cut PvP damage or raise poise);
        - 'rate_x', 'add_main', 'fp_x': for a tear, what its rows do to `line`'s spell (rows
          `CastModel._applies` admits for the spell, as it admits a talisman's), its main stat and
          the school's FP cost, so a damage tear competes with a defensive one for the two slots.
        Left out, with the reason in `mods_left_out`: talismans (chosen as talismans), great runes
        (inert for an invader, er-weapon-build), one-hit rows, body transforms, rows shorter than
        `REFRESHED_ROW_S`, goods this fight's role cannot use."""
        key = (line.main, line.catalyst, line.spell)
        if self._mods is None:
            self._mods = {}
            self._def_mods = self._defensive_mods()
        if key not in self._mods:
            out = {m["name"]: dict(m) for m in self._def_mods}
            for m in self._offense_tears(line):
                out[m["name"]] = {**out.get(m["name"], {}), **m}
            self._mods[key] = list(out.values())
        return self._mods[key]

    def _offense_tears(self, line):
        if not spells_of(line):
            return []
        sm, T = self.sm, self.SP.T
        sp = sm.cast.spell(sm.by_name[T._norm(line.spell)])
        names = DEF.PR.row_names("EquipParamGoods")
        out = []
        for gid, g in sorted(self.goods.items()):
            nm = names.get(gid)
            if g["goodsType"] != TEAR_GOODS_TYPE or not nm or g["sortId"] == SIM.S.UNOBTAINABLE_SORT_ID:
                continue
            rows = self._source_rows(nm) or ([g["refId_default"]] if g["refId_default"] > 0 else [])
            rows = rows + self.cycled(rows)
            # Every row a cycle reaches, at any interval: Bloodsucking Cracked Tear's 20511050 cycles
            # 20511051 (0.2% max HP + 20 a tick) with no `motionInterval` set.
            linked = rows + [(self.sp_all.get(i) or {}).get("cycleOccurrenceSpEffectId", -1) for i in rows]
            drain = sorted({i for i in linked if (self.sp_all.get(i) or {}).get("changeHpRate", 0) > 0
                            or (self.sp_all.get(i) or {}).get("changeHpPoint", 0) > 0})
            if drain:
                self._tear_why[nm] = f"drains its user's HP (rows {drain}): the drain is not priced here"
                continue
            rate_x, add, fp_x, secs = 1.0, 0, 1.0, 0.0
            for i in rows:
                r = self.sp_all.get(i)
                if not r:
                    continue
                if sm.cast._applies(sp, r):
                    rate_x *= sum(w * r.get(T.ATTACK_RATE[e], 1.0) * r.get("atkPlayerDmgCorrectRate_" + T.EL_SUFFIX[e], 1.0)
                                  for e, w in sp["elements"].items())
                if r.get("stateInfo") == 0 and r.get("conditionHp", -1) < 0 and r.get("conditionHpRate", -1) < 0:
                    add += int(r.get(DEF.STAT_ADD[line.main], 0))
                    fp_x *= r.get(CONSUMPTION[line.main], 1.0)
                secs = max(secs, r.get("effectEndurance", 0.0))
            if (abs(rate_x - 1) > 1e-9 or add or abs(fp_x - 1) > 1e-9) and secs >= self.B.REFRESHED_ROW_S:
                out.append({"name": nm, "kind": "tear", "rate_x": rate_x, "add_main": add, "fp_x": fp_x, "seconds": secs,
                            "uses": self.B.TEAR_USES, "frames": self.B.FLASK_DRINK_FRAMES, "rows": rows,
                            "kept": 1.0, "poise_x": 1.0})
        return out

    def _defensive_mods(self):
        builds = AT.corpus_builds(self.rl - self.window, self.rl + self.window)
        out, why = [], {}
        for b in AT.buff_table(self.t, self.armor.share, builds):
            kind = b["kind"]
            if kind.startswith("talisman"):
                continue
            if kind.startswith("great rune"):
                why[b["name"]] = "great rune: inert for the invader"
                continue
            if "one hit" in kind:
                why[b["name"]] = "one-hit row: not priced"
                continue
            if b["seconds"] is not None and b["seconds"] < 0:
                why[b["name"]] = "body transform: replaces the build (pouch's rule)"
                continue
            if b["seconds"] is None or b["seconds"] < self.B.REFRESHED_ROW_S:
                why[b["name"]] = "row shorter than the refresh limit: no uptime read"
                continue
            ck = f"consumable:{b['name']}"
            c = self.pouch.cands.get(ck)
            if kind.startswith("tear"):
                uses, frames = self.B.TEAR_USES, self.B.FLASK_DRINK_FRAMES
            elif c is not None:
                uses = self.pouch._limit(ck, [ck])[0]
                frames = 0.0 if c.get("walks") else self.pouch.m.item_cast_frames
            else:
                why[b["name"]] = "not a pouch candidate for this fight's role"
                continue
            if c is not None and self.role not in c.get("roles", {self.role}):
                why[b["name"]] = f"not usable by the {self.role}"
                continue
            out.append({"name": b["name"], "kind": kind.split(" ")[0], "kept": b["kept"], "poise_x": b["poise_x"],
                        "seconds": b["seconds"], "uses": uses, "frames": frames,
                        "rows": self._source_rows(b["name"], ck if c is not None else None)})
        self._mods_why = why
        return out


# ---------------------------------------------------------------------------------------------
# the search

def carried_for(line, world):
    """The line's carried weight: given (`Line.carried`), else its kit's own: each catalyst its
    spells are cast with, once, and the weapon its melee offense is measured on (`EquipParamWeapon.
    weight`). A line carries what its own stats wield, never what its archetype's players carry
    (user, 2026-11-12, of a 12.5 corpus median that cost the Intelligence line 10 END: "46
    endurance seems a little crazy given it can't use weapons though"). No swap is carried."""
    if line.carried is not None:
        return float(line.carried)
    cats = dict.fromkeys(k.catalyst for k in spells_of(line) if k.catalyst)
    out = sum(float(world.sm.catalyst(c)["wep"]["weight"]) for c in cats)
    out += sum(world.weapon_offense(w)["weight"] for w in weapons_of(line))
    return round(out, 2)


def class_table(line, world):
    """Per starting class: its bases, the stats neither searched nor the main one (STR and DEX at
    their base or the catalyst's requirement, the rest at base) and their sum, and the least main
    stat (its base, the catalyst's and the spell's requirement)."""
    cat = world.sm.catalyst(line.catalyst)["wep"]
    need = {"str": cat["properStrength"], "dex": cat["properAgility"], "int": cat["properMagic"],
            "fth": cat["properFaith"], "arc": cat["properLuck"]}
    mg = world.sm.magic[world.sm.by_name[world.SP.T._norm(line.spell)]]
    need[line.main] = max(need[line.main], {"int": mg["requirementIntellect"], "fth": mg["requirementFaith"]}[line.main])
    out = {}
    for c, b in world.classes.items():
        fixed = {k: max(b[k], need.get(k, 0)) if k in ("str", "dex") else b[k]
                 for k in ATTRS if k not in ("vig", "end", "mnd", line.main)}
        out[c] = {"base": b, "fixed": fixed, "others": sum(fixed.values()), "main_min": max(b[line.main], need[line.main])}
    return out


def fill_steps(base, mains, cap, n):
    """[(str, dex, int, fth, arc)] after 0..`n` points over base damage stats `base`, given as
    `SIM.allocate_line` gives them: `mains` evenly to `cap`, then STR and DEX less `mains` evenly,
    then every damage stat evenly (each point to the stat with fewest, ties in order). Shorter when
    every stat is full. The selftest checks it against `SIM.allocate_line`."""
    st = {k: int(base[k]) for k in DMG}
    rest = tuple(k for k in SIM.WEAPON_STATS if k not in mains)
    groups = ((tuple(mains), cap), (rest, STAT_MAX), (tuple(SIM.WEAPON_NEED), STAT_MAX))
    out = [tuple(st[k] for k in DMG)]
    for _ in range(n):
        for group, top in groups:
            free = [k for k in group if st[k] < top]
            if free:
                k = min(free, key=lambda s: (st[s], group.index(s)))
                st[k] += 1
                break
        else:
            break
        out.append(tuple(st[k] for k in DMG))
    return out


def fill_table(line, world):
    """{class: array (points left + 1, 5)}: `fill_steps` on each starting class's bases for every
    count of points VIG, `END` and MND leave, -1 past what the damage stats take."""
    key = ("fill", tuple(line.mains), line.cap)
    if key not in world._off:
        out = {}
        for c, b in world.classes.items():
            steps = fill_steps(b, line.mains, line.cap, world.total)
            arr = np.full((world.total + 1, len(DMG)), -1, int)
            arr[: len(steps)] = steps
            out[c] = arr
        world._off[key] = out
    return world._off[key]


def kit_hit(line, world, gear, stats, wear=None):
    """The kit's share-weighted one hit at base attributes `stats`, no windup interrupt: what a
    melee or two-school line's starting class is chosen on (`search`, `shape_point`)."""
    wadd = world.wear_adds(wear)
    eff = {k: np.array(stats[k] + gear.adds[k] + wadd[k]) for k in DMG}
    out = 0.0
    for k, off in gear.parts():
        ok = world.spell_ok(k, stats) if isinstance(k, Spell) else None
        out += k.share * float(gear.hit_of(k, off, eff, ok))
    return out


def allocation(line, world, cls, vig, end, mnd):
    """The eight attributes of class `cls` with those three stats, or None: a caster line's main
    stat to its cap and the points past it to STR and DEX (`even_fill`); a line with `mains`, as
    `SIM.allocate_line` makes it."""
    if line.mains:
        b = world.classes[cls]
        if vig < b["vig"] or end < b["end"] or mnd < b["mnd"]:
            return None
        return SIM.allocate_line(dict(b, mnd=mnd), world.total, vig, end, line.mains, line.cap)
    ct = class_table(line, world)[cls]
    b = ct["base"]
    if vig < b["vig"] or end < b["end"] or mnd < b["mnd"]:
        return None
    rest = world.total - vig - end - mnd - ct["others"]
    main = min(line.cap, rest)
    if main < ct["main_min"]:
        return None
    st = dict(ct["fixed"], vig=vig, end=end, mnd=mnd, **{line.main: main})
    s2, d2 = even_fill(st["str"], st["dex"], rest - main)
    st["str"], st["dex"] = int(s2), int(d2)
    if max(st.values()) > STAT_MAX:
        return None
    return st


def search(line, world, gear, vigs, ends, mnds, wears, fixed_set=None):
    """The best allocation for `line` under `gear` over the given stat ranges and HP pieces:
    {'D', 'class', 'vig', 'end', 'mnd', 'wear', 'set', 'n_c'} or None. Vectorised over Vigor x Mind
    x sets x Cerulean count per (HP piece, Endurance).

    The starting class: for a caster line (no `mains`) the one with the fewest points in the stats
    it does not search (so the most in the main stat); for a line with `mains` the one whose kit
    hits hardest at the allocation (`kit_hit`, no windup interrupt), ties to the most points in the
    mains, then in STR and DEX, then the first name: a weapon whose requirement one class's bases
    miss is not credited to it."""
    names = sorted(world.classes)
    carried = carried_for(line, world)
    parts = gear.parts()
    load = gear.load()
    fpc = gear.fp()
    V = np.array(vigs, float)[:, None]
    M = np.array(mnds, float)[None, :]
    chip = line.scenario == "carried"
    u_ofs = [world.u_curve(world.seq_of(k, off), chip, line.contested) for k, off in parts]
    if line.mains:
        fill = fill_table(line, world)
        dsum = {c: sum(int(world.classes[c][k]) for k in DMG) for c in names}
        rest_k = [k for k in SIM.WEAPON_STATS if k not in line.mains]
        mi = [DMG.index(k) for k in line.mains]
        ri = [DMG.index(k) for k in rest_k]
    else:
        ct = class_table(line, world)
    best = None
    for wear in wears:
        hpc = gear.hp(wear)
        wadd = world.wear_adds(wear)
        hp = hpc[np.array(vigs)][:, None, None]
        bar = fpc[np.array(mnds)][None, :, None]
        if line.mains:
            # Per class, per count of points left: the damage stats, each attack's hit and the
            # kit's, read once for this HP piece.
            tabs = {}
            for c in names:
                tab = fill[c]
                dm_t = {k: tab[:, i] for i, k in enumerate(DMG)}
                eff_t = {k: dm_t[k] + gear.adds[k] + wadd[k] for k in DMG}
                hs = []
                for k, off in parts:
                    ok = (tab[:, 0] >= 0) & (world.spell_ok(k, dm_t) if isinstance(k, Spell) else True)
                    hs.append(np.where(ok, gear.hit_of(k, off, eff_t), 0.0))
                kit = sum(k.share * h for (k, _), h in zip(parts, hs))
                tie = 1e-6 * tab[:, mi].sum(1) + 1e-9 * (tab[:, ri].sum(1) if ri else 0.0)
                tabs[c] = (tab, hs, kit + tie)
        for e in ends:
            if fixed_set is not None:
                sets = [fixed_set]
            else:
                budget = MEDIUM_ROLL_MAX * load[e] - carried - gear.weight
                sets = world.sets(budget, wear, line.poise_min, line.poise_max)
            if not sets:
                continue
            if line.mains:
                keys, lefts = [], []
                for c in names:
                    b = world.classes[c]
                    tab, _, kv = tabs[c]
                    left = (world.total - V - e - M - dsum[c]).astype(int)
                    li = np.clip(left, 0, len(tab) - 1)
                    ok = ((V >= b["vig"]) & (M >= b["mnd"]) & (e >= b["end"]) & (left >= 0) & (left < len(tab))
                          & (tab[li, 0] >= 0))
                    keys.append(np.where(ok, kv[li], -np.inf))
                    lefts.append(li)
                keys = np.stack(keys)
                valid = np.isfinite(keys.max(0))
                if not valid.any():
                    continue
                ci = np.argmax(keys, 0)
                dm = {k: np.choose(ci, [tabs[c][0][lefts[n], i] for n, c in enumerate(names)])
                      for i, k in enumerate(DMG)}
                hits = [np.choose(ci, [tabs[c][1][j][lefts[n]] for n, c in enumerate(names)])
                        for j in range(len(parts))]
            else:
                # The class with the fewest points in the fixed stats among those whose bases fit.
                others = np.full((len(names),) + np.broadcast_shapes(V.shape, M.shape), np.inf)
                for i, c in enumerate(names):
                    b = ct[c]["base"]
                    rest = world.total - V - e - M - ct[c]["others"]
                    ok = (V >= b["vig"]) & (M >= b["mnd"]) & (e >= b["end"]) & (np.minimum(line.cap, rest) >= ct[c]["main_min"])
                    others[i] = np.where(ok, ct[c]["others"], np.inf)
                valid = np.isfinite(others.min(0))
                if not valid.any():
                    continue
                ci = np.argmin(others, 0)
                rest = world.total - V - e - M - np.where(valid, others.min(0), 0.0)
                main = np.minimum(line.cap, rest)
                spill = np.maximum(rest - main, 0)
                dm = {k: np.choose(ci, [ct[c]["fixed"].get(k, 0) for c in names]) + 0 * main for k in DMG}
                dm["str"], dm["dex"] = even_fill(dm["str"], dm["dex"], spill)
                dm[line.main] = main
                eff = {k: np.clip(dm[k] + gear.adds[k] + wadd[k], 0, 130).astype(int) for k in DMG}
                hits = [gear.hit_of(k, off, eff, world.spell_ok(k, dm) if isinstance(k, Spell) else None)
                        for k, off in parts]
            st = {"str": dm["str"], "int": dm["int"], "vig": V + 0 * M, "end": np.full(valid.shape, float(e)),
                  "arc": dm["arc"]}
            cut = np.stack([s["cut"] * s["eff"] for s in sets]) * gear.eff * gear.kept           # (S, 8)
            P = np.array([s["armor_poise"] / (s["tcut"] * gear.tcut) for s in sets])
            sadd = {k: np.array([s["adds"][k] + gear.adds[k] + wadd[k] for s in sets], float) for k in ATTRS}
            level = (world.total + sum(sadd.values())).astype(int)                               # (S,)
            eff_st = {k: st[k][..., None] + sadd[k] for k in ELEMENT_STAT}
            hit = world.incoming(level, eff_st, cut)                                               # (V, M, S)
            offm, fpm, free = 0.0, np.zeros(len(sets)), 0.0
            for (k, off), u_of, h in zip(parts, u_ofs, hits):
                H, _, U = gear.poise_curves(P, chip, u_of)
                if isinstance(k, Spell):
                    offm = offm + k.share * h[..., None] * U[None, None, :]
                    fpm = fpm + k.share * U * gear.fp_of(k, off)
                else:
                    free = free + k.share * h[..., None] * U[None, None, :]
            D, _, _, _, _ = score(offm, fpm, line.casts, 1.0, hp, hit, H[None, None, :], bar, world.fight,
                                  line.crimson, free)
            D = np.where(valid[..., None, None], D, -np.inf)
            k = np.unravel_index(int(np.argmax(D)), D.shape)
            if best is None or D[k] > best["D"]:
                best = {"D": float(D[k]), "class": names[int(ci[k[0], k[1]])], "vig": int(vigs[k[0]]),
                        "end": int(e), "mnd": int(mnds[k[1]]), "wear": wear, "set": sets[k[2]], "n_c": int(k[3])}
    return best


def evaluate(line, world, gear, stats, wear, armor_set, cls=None):
    """The breakdown of one allocation: `stats` (the eight attributes), `wear`, `armor_set`
    (`GameWorld.sets`' shape), the Cerulean count chosen on D. Scalar, per term. 'offense' is the
    kit's first attack's, with every attack's own under 'parts'."""
    wadd = world.wear_adds(wear)
    sadd = {k: armor_set["adds"][k] + gear.adds[k] + wadd[k] for k in ATTRS}
    eff = {k: stats[k] + sadd[k] for k in ATTRS}
    level = world.total + sum(sadd.values())
    cut = armor_set["cut"] * armor_set["eff"] * gear.eff * gear.kept
    hit = float(world.incoming(np.array([level]), {k: np.array([eff[k]]) for k in ELEMENT_STAT}, cut[None, :])[0])
    P = armor_set["armor_poise"] / (armor_set["tcut"] * gear.tcut)
    chip = line.scenario == "carried"
    hp = float(gear.hp(wear)[stats["vig"]])
    bar = float(gear.fp()[stats["mnd"]])
    oeff = {k: np.array(int(min(130, stats[k] + gear.adds[k] + wadd[k]))) for k in DMG}
    offm, fpm, free, parts = 0.0, 0.0, 0.0, []
    for k, off in gear.parts():
        u_of = world.u_curve(world.seq_of(k, off), chip, line.contested)
        H, B1, U = gear.poise_curves(np.array([P]), chip, u_of)
        spell = isinstance(k, Spell)
        h = float(gear.hit_of(k, off, oeff, world.spell_ok(k, stats) if spell else None))
        fp = float(gear.fp_of(k, off))
        if spell:
            offm, fpm = offm + k.share * U[0] * h, fpm + k.share * U[0] * fp
        else:
            free = free + k.share * U[0] * h
        stop = float(1 - U[0]) / line.contested if line.contested else 0.0
        parts.append({"kind": "spell" if spell else "weapon", "share": k.share,
                      "name": k.spell if spell else k.name, "catalyst": k.catalyst if spell else None,
                      "affinity": None if spell else k.affinity, "two": None if spell else k.two,
                      "level": None if spell else k.level,
                      "buff": round(float(off["buff"][int(oeff[k.main])]), 1) if spell else None,
                      "hit": round(h, 1), "fp_cast": round(fp, 2), "variant": off["variant"],
                      "startup": off["startup"] if spell else world.seq_of(k, off)["strike"],
                      "uninterrupted": round(float(U[0]), 4), "stopped": round(stop, 4)})
    D, share, N, L, s = score(np.array([offm]), np.array([fpm]), line.casts, np.array([1.0]), np.array([hp]),
                              np.array([hit]), H, np.array([bar]), world.fight, line.crimson, np.array([free]))
    k = int(np.argmax(D[0]))
    load = float(gear.load()[stats["end"]])
    burden = armor_set["weight"] + carried_for(line, world) + gear.weight
    first = parts[0]
    return {"D": float(D[0, k]), "stats": dict(stats), "class": cls, "wear": wear, "set": armor_set,
            "offense": {"buff": first["buff"], "hit": first["hit"], "spell": first["name"],
                        "variant": first["variant"], "fp_cast": first["fp_cast"], "startup": first["startup"],
                        "paid": round(float(share[0, k]), 4), "uninterrupted": first["uninterrupted"],
                        "stopped": first["stopped"], "casts": line.casts, "parts": parts},
            "defense": {"hp": int(hp), "mean_hit": round(hit, 1), "menu_poise": round(float(P), 1),
                        "armor_poise": armor_set["armor_poise"], "hits_per_attack": round(float(H[0]), 4),
                        "stagger": round(float(B1[0]), 4), "life": round(float(N[0, k]), 2),
                        "drain": round(float(L[0, 0]), 1)},
            "fp": {"bar": int(bar), "spend": round(float(s[0, 0]), 2), "cerulean": k,
                   "crimson": int(world.fight["flasks"]) - k},
            "gear": {"pieces": list(armor_set["pieces"]), "weight": armor_set["weight"], "talismans": list(gear.talismans),
                     "mods": [m["name"] for m in gear.mods], "carried": carried_for(line, world),
                     "burden": round(burden / load, 4) if load else None}}


def _ranges(line, world):
    lo = {k: min(b[k] for b in world.classes.values()) for k in ("vig", "end", "mnd")}
    vigs = [line.vig] if line.vig is not None else list(range(max(lo["vig"], VIG_RANGE[0]), VIG_RANGE[1] + 1))
    ends = [line.end] if line.end is not None else list(range(lo["end"], END_MAX + 1))
    mnds = [line.mnd] if line.mnd is not None else list(range(lo["mnd"], MND_MAX + 1))
    return vigs, ends, mnds


def _wears(line, world):
    return [w for w in [None, *world.armor.hp_pieces] if line.wears is None or w in line.wears]


def fit_stats(line, world, gear):
    """`search` over the line's ranges and HP pieces under `gear`, then its `evaluate` breakdown."""
    if line.lightest:
        return shape_point(line, world, gear)
    vigs, ends, mnds = _ranges(line, world)
    b = search(line, world, gear, vigs, ends, mnds, _wears(line, world))
    if b is None:
        return None
    st = allocation(line, world, b["class"], b["vig"], b["end"], b["mnd"])
    out = evaluate(line, world, gear, st, b["wear"], b["set"], b["class"])
    # An edge of a range that is not a class base: the optimum may lie past it.
    floor = {"vig": VIG_RANGE[0], "end": None, "mnd": None}
    out["edges"] = [k for k, v, r in (("vig", b["vig"], vigs), ("end", b["end"], ends), ("mnd", b["mnd"], mnds))
                    if len(r) > 1 and (v == r[-1] or (v == r[0] and floor[k] == v))]
    return out


def consistency(world):
    """Checks of this module's readings against the modules it reads, on the installed regulation:
    `incoming` against `ArmorModel.hit` on frontier sets whose pieces carry no resident cut, and
    `chain_hits`' fresh break share against `AT.poise_curve`'s first-hit share. [(label, mine,
    theirs)]."""
    out = []
    st = dict(world.ref)
    for budget, floor in ((30.0, 0.0), (45.0, 51.0), (55.0, 89.0)):
        o = world.armor.pick(budget, floor)
        s = world._set(o)
        if np.any(np.abs(s["eff"] - 1) > 1e-9):
            continue
        eff = {k: st[k] + s["adds"][k] for k in ATTRS}
        mine = float(world.incoming(np.array([sum(eff.values())]), {k: np.array([eff[k]]) for k in ELEMENT_STAT},
                                    s["cut"][None, :])[0])
        out.append((f"mean pool hit, {' / '.join(o['pieces'])}", round(mine, 3), round(world.armor.hit(st, o), 3)))
    grid = [0, 51, 89, 130]
    pc = AT.poise_curve(world.rows, grid)
    _, b = world.hits_at(np.array(grid, float), False)
    for P, x, y in zip(grid, b, pc["first"]):
        out.append((f"first-hit break share at poise {P}, fresh", round(float(x), 4), round(float(y), 4)))
    return out


def least_end(weight, load):
    """The least `END` at which burden `weight` keeps medium roll at max equip load `load[e]`
    (`RES.Model.weight_type`), `STAT_MAX` + 1 when none does."""
    return next((e for e in range(1, STAT_MAX + 1) if RES.Model.weight_type(weight, load[e]) <= 2), STAT_MAX + 1)


def class_point(line, world, gear, vig, end, mnd=None, wear=None):
    """(class, eight attributes) at those VIG and `END` (each at least the class's base) and MND (the
    class's base unless given, or pinned by `line.mnd`), or None: a caster line on the class that
    leaves the most points in the main stat, then in STR and DEX (`SIM.attacker_builds`' order);
    a line with `mains` on the one whose kit hits hardest (`kit_hit`), ties to the most points in
    the mains, then in STR and DEX."""
    best = None
    rest = [k for k in SIM.WEAPON_STATS if k not in (line.mains or (line.main,))]
    for cls, b in sorted(world.classes.items()):
        m = line.mnd if line.mnd is not None else (mnd if mnd is not None else b["mnd"])
        st = allocation(line, world, cls, max(vig, b["vig"]), max(end, b["end"]), max(m, b["mnd"]))
        if st is None:
            continue
        if line.mains:
            key = (round(kit_hit(line, world, gear, st, wear), 6), sum(st[k] for k in line.mains), sum(st[k] for k in rest))
        else:
            key = (st[line.main], st["str"] + st["dex"])
        if best is None or key > best[0]:
            best = (key, cls, st)
    return None if best is None else best[1:]


def shape_point(line, world, gear):
    """The attack-similarity line shape (`SIM.attacker_builds`): Vigor pinned (`line.vig`), Mind at
    the class base unless pinned, the lightest set at `line.poise_min` (`SIM.lightest_armor`),
    Endurance the least that keeps that set, the carried weight and the talismans at medium roll,
    and the class `class_point` picks."""
    worn = SIM.lightest_armor(AT.armor_candidates(world.t), line.poise_min)
    aset = world.named_set(worn["pieces"])
    need = round(aset["weight"] + carried_for(line, world) + gear.weight, 1)
    end = least_end(need, gear.load())
    got = class_point(line, world, gear, line.vig, end)
    if got is None:
        return None
    cls, st = got
    return evaluate(line, world, gear, st, None, aset, cls)


def kit_with(line, w):
    """`line` with weapon `w` in its kit in place of any other, at the share the kit's weapon
    slot has (1 when it has none)."""
    old = weapons_of(line)
    share = old[0].share if old else 1.0
    return replace(line, kit=tuple(k for k in kit_of(line) if not isinstance(k, Weapon)) + (replace(w, share=share),))


def pick_weapon(line, world, tag=None, point=None):
    """(Weapon, screen): the weapon `line`'s melee offense is measured on, read off the game at the
    line's own stats. The point: VIG, MND, HP piece and armor set from `point` (an `evaluate`
    result), else VIG at `line.vig` or the knee of max HP, MND at the class base, no HP piece and
    the lightest set at `line.poise_min` (`SIM.lightest_armor`). Per candidate
    (`GameWorld.weapon_candidates` at the damage stats `END` least for the kit without a weapon
    leaves, of archetype `tag` when given): `END` the least that keeps the set, the kit with it and
    the talismans at medium roll, the stats `class_point` gives there, and its R1 hit at them
    (`weapon_hit`). The `SCREEN_TOP` hardest are priced with their R1's windup interrupt at the
    set's poise (`u_curve`, `contested` as the line's), and the weapon of the highest hit x
    uninterrupted share is the line's. `INFERRED`: one hit an engagement, as D reads the kit, so
    the screen prices neither attack speed nor recovery; the weapon's weight it does price, through
    the `END` it takes. `screen` lists every priced candidate."""
    probe = kit_with(line, Weapon("-"))
    gear = world.gear(probe, line.talismans or ())
    if point is not None:
        vig, mnd, wear, aset = point["stats"]["vig"], point["stats"]["mnd"], point["wear"], point["set"]
    else:
        vig, mnd, wear = line.vig if line.vig is not None else world.knee, None, None
        aset = world.named_set(SIM.lightest_armor(AT.armor_candidates(world.t), line.poise_min)["pieces"])
    load = gear.load()
    wadd = world.wear_adds(wear)
    mains = line.mains or (line.main,)
    rest = [k for k in SIM.WEAPON_STATS if k not in mains]
    cats = dict.fromkeys(k.catalyst for k in spells_of(line) if k.catalyst)
    base_w = sum(float(world.sm.catalyst(c)["wep"]["weight"]) for c in cats)

    def at(end, w=None):
        """(class, stats, hit) at this END: the class `class_point`'s rule picks, by the hit of `w`
        for a line with `mains`, by points otherwise."""
        best = None
        for cls, b in sorted(world.classes.items()):
            m = line.mnd if line.mnd is not None else (mnd if mnd is not None else b["mnd"])
            st = allocation(line, world, cls, max(vig, b["vig"]), max(end, b["end"]), max(m, b["mnd"]))
            if st is None:
                continue
            h = world.weapon_hit(w, {k: st[k] + gear.adds[k] + wadd[k] for k in DMG}) if w is not None else 0.0
            pts = (sum(st[k] for k in mains), sum(st[k] for k in rest))
            key = (round(h, 6),) + pts if line.mains else pts
            if best is None or key > best[0]:
                best = (key, cls, st, h)
        return None if best is None else best[1:]

    first = at(least_end(aset["weight"] + base_w + gear.weight, load))
    if first is None:
        return None, []
    cands = world.weapon_candidates(first[1], tag)
    rows = []
    for _, w in cands:
        weight = base_w + world.weapon_offense(w)["weight"]
        end = least_end(aset["weight"] + weight + gear.weight, load)
        got = at(end, w)
        if got is None:
            continue
        cls, st, h = got
        if not line.mains:
            h = world.weapon_hit(w, {k: st[k] + gear.adds[k] + wadd[k] for k in DMG})
        rows.append({"weapon": w, "weight": round(weight, 2), "end": end, "class": cls, "stats": st, "hit": round(h, 1)})
    rows.sort(key=lambda r: (-r["hit"], r["weapon"].name))
    P = aset["armor_poise"] / (aset["tcut"] * gear.tcut)
    chip = line.scenario == "carried"
    priced = []
    for r in rows:
        if len(priced) >= SCREEN_TOP:
            break
        try:
            u = float(world.u_curve(world.r1_seq(r["weapon"]), chip, line.contested)(np.array([P]))[0])
        except ValueError as exc:
            r["skipped"] = str(exc)
            continue
        r.update(uninterrupted=round(u, 4), value=round(r["hit"] * u, 2))
        priced.append(r)
    if not priced:
        return None, rows[: SCREEN_TOP]
    best = max(priced, key=lambda r: (r["value"], -rows.index(r)))
    share = weapons_of(line)[0].share if weapons_of(line) else 1.0
    screen = [{"name": r["weapon"].name, "affinity": r["weapon"].affinity, "two": r["weapon"].two,
               "weight": r["weight"], "end": r["end"], "class": r["class"], "hit": r["hit"],
               "uninterrupted": r.get("uninterrupted"), "value": r.get("value"), "skipped": r.get("skipped")}
              for r in rows[: max(SCREEN_TOP, rows.index(priced[-1]) + 1)]]
    return replace(best["weapon"], share=share), screen


def _pinned(line, point):
    st = point["stats"]
    return replace(line, vig=st["vig"], end=st["end"], mnd=st["mnd"], lightest=False), [st["vig"]], [st["end"]], [st["mnd"]]


def greedy_talismans(line, world, point, mods=()):
    """Four talisman slots filled greedily on D at `point`'s three searched stats (class, HP piece,
    armor set and Ceruleans re-chosen with each candidate), one per `accessoryGroup`. Returns
    (talismans, [{'talisman', 'gain'}])."""
    cands = world.talisman_candidates(line)
    at, v, e, m = _pinned(line, point)
    wears = _wears(line, world)
    cur = search(at, world, world.gear(line, (), mods), v, e, m, wears)
    cur_d = cur["D"] if cur else -math.inf
    pick, gains = [], []
    for _ in range(TALISMAN_SLOTS):
        groups = {world.group_of(n) for n in pick} - {None}
        top = None
        for n in cands:
            if n in pick or world.group_of(n) in groups:
                continue
            got = search(at, world, world.gear(line, tuple(pick + [n]), mods), v, e, m, wears)
            if got and (top is None or got["D"] > top[0]):
                top = (got["D"], n)
        if top is None or top[0] <= cur_d * (1 + 1e-6):
            break
        gains.append({"talisman": top[1], "gain": round(top[0] / cur_d - 1, 4) if cur_d > 0 else None})
        pick.append(top[1])
        cur_d = top[0]
    return pick, gains


def greedy_mods(line, world, point, talismans):
    """Physick tears (two) and consumables (any number) that raise D at `point`, each priced by its
    uptime over the engagements the build lives (uses x seconds / (N x `ENGAGEMENT_SECONDS`),
    `INFERRED`: the uses back to back from the start), its use frames charged to offense (a use its
    user walks through costs none, the pouch's reading). Returns (sources, gains)."""
    secs = max(point["defense"]["life"], 1e-6) * world.fight["engagement_s"]
    srcs = []
    for m in world.mod_sources(line):
        up = min(1.0, m["uses"] * m["seconds"] / secs)
        spent = m["uses"] * m["frames"] / world.B.CAST_FPS
        srcs.append(dict(m, uptime=up, time=max(0.0, 1.0 - spent / secs)))
    at, v, e, mn = _pinned(line, point)
    wears = _wears(line, world)
    cur = search(at, world, world.gear(line, talismans, ()), v, e, mn, wears)["D"]
    pick, gains = [], []
    while True:
        tears = sum(1 for x in pick if x["kind"] == "tear")
        top = None
        for m in srcs:
            if any(m["name"] == x["name"] for x in pick) or (m["kind"] == "tear" and tears >= TEAR_SLOTS):
                continue
            if not world.stack_ok(pick + [m]):
                continue
            got = search(at, world, world.gear(line, talismans, tuple(pick + [m])), v, e, mn, wears)
            if got and (top is None or got["D"] > top[0]):
                top = (got["D"], m)
        if top is None or top[0] <= cur * (1 + 1e-6):
            break
        gains.append({"source": top[1]["name"], "gain": round(top[0] / cur - 1, 4), "uptime": round(top[1]["uptime"], 3)})
        pick.append(top[1])
        cur = top[0]
    return pick, gains


def fit(line, world, log=print):
    """The allocation for `line` with its gear: the stats on D (`search`), then the talismans at
    those stats (`greedy_talismans`), the stats again with them, until the set repeats (`ROUNDS`);
    then the physick tears and consumables (`greedy_mods`) and the stats once more with them."""
    tal = tuple(line.talismans) if line.talismans is not None else ()
    point, history, tal_gains = None, [], []
    for r in range(ROUNDS):
        point = fit_stats(line, world, world.gear(line, tal))
        if point is None:
            return None
        history.append({"round": r + 1, "talismans": list(tal), "D": round(point["D"], 1),
                        "stats": {k: point["stats"][k] for k in ("vig", "end", "mnd", line.main)}})
        log(f"  round {r + 1}: {line_text(point, line)}, {point['wear'] or 'no HP piece'}, poise "
            f"{point['defense']['armor_poise']:g}: D {point['D']:.0f} with {', '.join(tal) or 'no talismans'}")
        if line.talismans is not None:
            break
        new, tal_gains = greedy_talismans(line, world, point)
        if tuple(new) == tal:
            break
        tal = tuple(new)
    mods, mod_gains = (), []
    if line.tears is None:
        picked, mod_gains = greedy_mods(line, world, point, tal)
        if picked:
            mods = tuple(picked)
            point = fit_stats(line, world, world.gear(line, tal, mods))
            log(f"  with {', '.join(m['name'] for m in mods)}: {line_text(point, line)}: D {point['D']:.0f}")
    point["history"] = history
    point["talisman_gains"] = tal_gains
    point["mod_gains"] = mod_gains
    point["mods_detail"] = [{k: m.get(k) for k in ("name", "kind", "kept", "poise_x", "rate_x", "add_main", "fp_x",
                                                  "seconds", "uses", "uptime", "time")} for m in mods]
    point["mods"] = mods
    return point


#: Fits `fit_line` runs at most: the weapon picked at the line's shape, then re-picked at each fit.
WEAPON_ROUNDS = 2


def fit_line(line, world, tag=None, log=print):
    """(fit, line fitted): `fit` for `line`, whose kit's weapon (any `Weapon` in it, its name a
    placeholder) is picked off the game first (`pick_weapon` at the line's shape, archetype `tag`),
    then picked again at the fit's own VIG, MND, HP piece and set, and the line refitted with the
    new one while it changes (`WEAPON_ROUNDS` fits at most). The fit carries 'weapon_screens' (every
    screen, in order) and 'weapon_settled' (the last re-pick named the weapon fitted). A kit with no
    weapon is fitted as it is."""
    if not weapons_of(line):
        return fit(line, world, log), line
    w, screen = pick_weapon(line, world, tag)
    if w is None:
        return None, line
    screens, got, ln, settled = [screen], None, line, False
    for _ in range(WEAPON_ROUNDS):
        ln = kit_with(line, w)
        got = fit(ln, world, log)
        if got is None:
            return None, ln
        w2, screen = pick_weapon(ln, world, tag, got)
        screens.append(screen)
        settled = w2 is None or w2 == w
        log(f"  weapon {w.name} ({w.affinity}{', two-handed' if w.two else ''}): re-picked at the fit: "
            f"{w2.name if w2 else 'none'}")
        if settled:
            break
        w = w2
    got["weapon_screens"] = screens
    got["weapon_settled"] = settled
    return got, ln


def line_text(p, line):
    s = p["stats"]
    return (f"VIG {s['vig']} END {s['end']} MND {s['mnd']} {line.main.upper()} {s[line.main]} "
            f"STR {s['str']} DEX {s['dex']} ({p['class']})")


# ---------------------------------------------------------------------------------------------
# the report

def ehp_text(p):
    g = p["gear"]
    return ", ".join([p["wear"] or "no HP piece"] + g["talismans"] + g["mods"])


def row(label, p, ref, line):
    s, o, d, f = p["stats"], p["offense"], p["defense"], p["fp"]
    return (f"| {label} | {s['vig']} | {s['end']} | {s['mnd']} | {s[line.main]} | {s['str']}/{s['dex']} | {p['class']} "
            f"| {d['armor_poise']:g} ({d['menu_poise']:g}) | {ehp_text(p)} | {d['hp']} | {d['mean_hit']:.0f} "
            f"| {d['hits_per_attack']:.3f} | {100 * d['stagger']:.1f}% | {o['buff']:.1f} | {o['hit']:.0f} "
            f"| {100 * o['paid']:.1f}% | {100 * o['uninterrupted']:.1f}% | {f['bar']} | {f['cerulean']}/{f['crimson']} "
            f"| {d['life']:.1f} | {p['D']:.0f} | {p['D'] / ref:.3f} |")


HEADER = ("| point | VIG | END | MND | main | STR/DEX | class | poise armor (menu) "
          "| HP piece, talismans, tears, consumables | HP | mean hit | hits / attack | stagger | buff | spell hit "
          "| paid | uninterrupted | FP bar | blue / red | life (eng.) | D | D / ref |\n"
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")


def report(names, world, json_path=None, log=print):
    out = {"fight": {k: v for k, v in world.fight.items() if k != "items"}, "lines": {}}
    out["fight"]["melee_poise_floor"] = world.floor
    out["consistency"] = consistency(world)
    for label, mine, theirs in out["consistency"]:
        log(f"check: {label}: {mine} here, {theirs} in the module it reads")
    grid = np.array([0, 25, 51, 70, 89, 110, 130, 160], float)
    for carried in (True, False):
        h, b = world.hits_at(grid, carried)
        log(f"pool attacks at poise {grid.astype(int).tolist()} ({'carried chip' if carried else 'fresh'}): "
            f"hits landed {np.round(h, 3).tolist()}, first-hit break share {np.round(b, 3).tolist()}")
    out["poise_table"] = {str(c): [np.round(x, 4).tolist() for x in world.hits_at(grid, c)] for c in (True, False)}
    for name in names:
        line = LINES[name]
        log(f"{name}: fit ({line.spell} from {line.catalyst}, {carried_for(line, world):g} carried)")
        best = fit(line, world, log)
        log(f"  talismans by gain: {best['talisman_gains']}; tears and consumables: {best['mod_gains']}")
        off = world.gear(line).offense()
        u_of = world.u_curve(off["seq"], line.scenario == "carried", 1.0)
        log(f"  {line.spell} ({off['variant']}, strike f{off['startup']:g}, {len(off['seq']['windows'])} TAE 795 windows): "
            f"pool share that breaks the cast at poise {grid.astype(int).tolist()}: "
            f"{np.round(1 - u_of(grid), 4).tolist()}")
        tal = tuple(best["gear"]["talismans"])
        mods = tuple(best["mods"])
        g = world.gear(line, tal, mods)
        same = replace(line, talismans=tal, tears=tuple(m["name"] for m in mods))
        log(f"{name}: comparison points (the fit's talismans, tears and consumables held)")
        pts = [("fit", best),
               ("current shape: VIG 60, lightest 89-poise set, MND base",
                shape_point(replace(same, vig=60, poise_min=world.floor, lightest=True), world, g)),
               ("VIG 60, poise >= 89, MND fitted", fit_stats(replace(same, vig=60, poise_min=world.floor), world, g)),
               ("sketch: VIG 45, poise < 89, MND fitted", fit_stats(replace(same, vig=45, poise_max=world.floor), world, g)),
               ("sketch: VIG 45, poise < 89, MND 38", fit_stats(replace(same, vig=45, mnd=38, poise_max=world.floor), world, g))]
        split = replace(same, name=f"{name} + melee", cap=SIM.SPLIT_CAP)
        pts += [("split: main 60, VIG 60, lightest 89-poise set, MND base",
                 shape_point(replace(split, vig=60, poise_min=world.floor, lightest=True), world, g)),
                ("split: main 60, rest fitted", fit_stats(split, world, g))]
        log(f"{name}: best with and without {IMP_HEAD} (each with its own talismans)")
        without = fit(replace(line, wears=tuple(w for w in _wears(line, world) if w != IMP_HEAD)), world, log)
        withimp = fit(replace(line, wears=(IMP_HEAD,)), world, log)
        pts += [(f"best without {IMP_HEAD}", without), (f"best with {IMP_HEAD}", withimp)]
        ref = best["D"]
        table = "\n".join([HEADER] + [row(label, p, ref, line) for label, p in pts if p])
        log(table)
        log(f"{name}: sensitivity (D / ref: the refit over the fit's allocation scored the same way)")
        sens = []
        for label, ln in (("Crimson priced as the pouch prices it", replace(same, crimson="pouch")),
                          ("fresh poise (no carried chip)", replace(same, scenario="fresh")),
                          ("half the casts contested", replace(same, contested=0.5)),
                          ("2 casts per engagement", replace(same, casts=2.0)),
                          ("3 casts per engagement", replace(same, casts=3.0)),
                          ("4 casts per engagement", replace(same, casts=4.0))):
            p = fit_stats(ln, world, g)
            at_fit = evaluate(ln, world, g, best["stats"], best["wear"], best["set"], best["class"])
            sens.append((label, p, at_fit))
        stable = "\n".join([HEADER] + [row(label, p, at["D"], line) for label, p, at in sens if p])
        log(stable)
        out["lines"][name] = {"spell": line.spell, "catalyst": line.catalyst, "carried": carried_for(line, world),
                              "points": [dict(label=label, **_jsonable(p)) for label, p in pts if p],
                              "sensitivity": [dict(label=label, at_fit_D=at["D"], **_jsonable(p)) for label, p, at in sens if p],
                              "table": table, "sensitivity_table": stable}
    out["fp_items"] = world.fight["item_names"]
    out["mods_left_out"] = {**getattr(world, "_mods_why", {}), **world._tear_why}
    out["talismans_left_out"] = world._tal_why
    for k, why in sorted(world._tal_why.items()):
        log(f"talisman left out: {k}: {why}")
    for k, why in sorted(out["mods_left_out"].items()):
        log(f"tear or consumable left out: {k}: {why}")
    if json_path:
        Path(json_path).write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def _jsonable(p):
    q = {k: v for k, v in p.items() if k not in ("set", "mods")}
    return json.loads(json.dumps(q, default=lambda x: x.tolist() if hasattr(x, "tolist") else float(x)))


# ---------------------------------------------------------------------------------------------
# selftest

class _Synthetic:
    """A world small enough to know the answer: one class, sets that buy 10 poise per 5 weight of
    pieces with no absorption, a flat incoming hit, HP linear in Vigor, FP linear in Mind, offense
    linear in the main stat."""

    def __init__(self, stagger=True, items=(), casts_fp=18.0):
        self.total = 100
        self.fight = {"kind": "test", "win": 0.5, "attackers": 1.0, "engagements": 40.0, "crimson_share": 1.0,
                      "flasks": 6, "heal": 500.0, "eta": 1.0, "cerulean": 100.0, "cerulean_cap": None,
                      "items": list(items), "item_names": [], "engagement_s": 5.0}
        self.stagger, self.casts_fp = stagger, casts_fp
        self.classes = {"Test": {k: 10 for k in ATTRS}}
        self._off = {}

    def seq_of(self, k, off):
        return off["seq"]

    def spell_ok(self, k, dm):
        return True

    def weapon_hits(self, w, eff):
        return 2.0 * np.asarray(eff["str"], float)

    def hits_at(self, P, carried):
        P = np.asarray(P, float)
        if not self.stagger:
            return np.ones_like(P), np.zeros_like(P)
        return 1.0 + np.exp(-P / 40.0), np.exp(-P / 40.0)

    def u_curve(self, seq, carried, contested):
        if not self.stagger:
            return lambda P: np.ones_like(np.asarray(P, float))
        return lambda P: 1.0 - contested * 0.2 * np.exp(-np.asarray(P, float) / 40.0)

    def incoming(self, level, stats, cut):
        shape = np.broadcast_shapes(*(np.shape(x) for x in stats.values()))
        return np.broadcast_to(100.0 * np.atleast_2d(cut)[:, 0], shape).copy()

    def wear_adds(self, wear):
        return {k: 0 for k in ATTRS}

    def sets(self, budget, wear, pmin=0.0, pmax=math.inf):
        out = []
        for heavy in range(5):
            w = 5.0 * heavy
            if w > budget:
                break
            p = 10.0 * heavy
            if not pmin <= p < pmax:
                continue
            light = budget - w
            out.append({"pieces": [f"heavy x{heavy}"], "weight": budget, "armor_poise": p,
                        "cut": np.full(len(AT.TYPES), max(0.5, 1.0 - 0.01 * light)), "eff": np.ones(len(AT.TYPES)),
                        "tcut": 1.0, "adds": {k: 0 for k in ATTRS}, "wear": wear})
        return out


class _SynGear:
    """A gear for `_Synthetic`: the spell's hit is its main stat, a weapon's twice STR
    (`_Synthetic.weapon_hits`), FP `casts_fp` a cast."""

    def __init__(self, world, line=None):
        self.world, self.talismans, self.mods = world, (), ()
        self.line = line
        self.weight, self.eff, self.tcut, self.fp_rate, self.kept, self.time = 0.0, np.ones(8), 1.0, 1.0, 1.0, 1.0
        self.adds = {k: 0 for k in ATTRS}

    def parts(self):
        kit = kit_of(self.line) if self.line is not None else (Spell("int", "-", "-"),)
        return [(k, self.offense()) for k in kit]

    def hit_of(self, k, off, eff, ok=None):
        if isinstance(k, Weapon):
            return self.world.weapon_hits(k, eff)
        return np.asarray(eff[k.main], float)

    def fp_of(self, k, off):
        return 0.0 if isinstance(k, Weapon) else self.world.casts_fp

    def hp(self, wear):
        return np.array([300.0 + 30.0 * v for v in range(STAT_MAX + 1)])

    def fp(self):
        return np.array([20.0 + 5.0 * m for m in range(STAT_MAX + 1)])

    def load(self):
        return np.array([20.0 + 1.0 * e for e in range(STAT_MAX + 1)])

    def offense(self):
        return {"hit": np.arange(131, dtype=float), "buff": np.arange(131, dtype=float), "fp": self.world.casts_fp,
                "seq": {"strike": 20.0, "windows": []}, "startup": 20.0, "variant": "cast"}

    def spell_hit(self, main_eff):
        return np.asarray(main_eff, float)

    def fp_cast(self):
        return self.world.casts_fp

    def poise_curves(self, P, carried, u_curve):
        h, b = self.world.hits_at(P, carried)
        return h, b, u_curve(P)


def _syn_search(world, line, gear):
    """`search` on a `_Synthetic` world: one class of all-10 bases, nothing carried."""
    fixed = {k: 10 for k in ATTRS if k not in ("vig", "end", "mnd", line.main)}
    table = {"Test": {"base": {k: 10 for k in ATTRS}, "fixed": fixed, "others": sum(fixed.values()), "main_min": 10}}
    mod = sys.modules[__name__]
    keep = mod.class_table, mod.carried_for
    mod.class_table, mod.carried_for = (lambda ln, w: table), (lambda ln, w: 0.0)
    try:
        return search(line, world, gear, list(range(10, 41)), list(range(10, 31)), list(range(10, 41)), [None])
    finally:
        mod.class_table, mod.carried_for = keep


def selftest() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok   " if cond else "FAIL ") + msg)
        ok = ok and bool(cond)

    # The FP plan: the closed form against the engagement-by-engagement plan. They agree with no
    # drink and differ by at most one engagement's want per drink (the drink's bar room).
    items = [(10.0, 120.0), (12.5, 120.0)]
    for N, s, bar, nc in ((20, 18.0, 78.0, 0), (5, 40.0, 150.0, 0), (20, 40.0, 78.0, 2), (20, 90.0, 78.0, 3),
                          (30, 30.0, 221.0, 1)):
        a = float(fp_paid(float(N), s, bar, items, float(nc), 220.0))
        b = fp_paid_steps(N, s, bar, items, nc, 220.0)
        check(b - 1e-6 <= a <= b + nc * s + 1e-6,
              f"fp_paid {a:.1f} is the step plan's {b:.1f} up to {nc} drink(s) of {s:g} (N {N}, bar {bar:g})")
    check(float(fp_paid(10.0, 90.0, 78.0, [], 0.0, 220.0)) == 78.0, "with no item and no drink one bar is all that is paid")
    check(float(fp_paid(10.0, 10.0, 78.0, [(100.0, 99.0)], 0.0, 220.0)) == 100.0,
          "items that out-refill the spend pay every cast")

    # The chain rule on two hand-made strings.
    seqs = [{"hits": [(10, 50.0), (30, 50.0), (50, 50.0)]}, {"hits": [(10, 100.0)]}]
    h, b = chain_hits(seqs, [0.5, 0.5], np.array([0.0, 40.0, 60.0, 500.0]), carried=False)
    check(np.allclose(h, [2.0, 2.0, 1.0, 1.0]),
          f"chain_hits: under every hit the whole string lands, past its hits one lands ({h.tolist()})")
    check(np.allclose(b, [1.0, 1.0, 0.5, 0.0]), f"chain_hits: the share whose first hit breaks ({b.tolist()})")
    _, b2 = chain_hits(seqs, [0.5, 0.5], np.array([120.0]), carried=True)
    check(0 < b2[0] < 0.5, f"chain_hits carried: chip breaks a poise no single first hit reaches, in part ({b2[0]:.3f})")
    check(tuple(map(float, even_fill(10, 14, 6))) == (15.0, 15.0) and tuple(map(float, even_fill(10, 14, 7))) == (16.0, 15.0),
          "even_fill raises the lower stat first, then both evenly, ties to the first")

    # score: the pool form's life is HP plus the Crimson drunk, over the drain, capped by the fight.
    fight = {"win": 0.5, "attackers": 1.0, "engagements": 1000.0, "flasks": 2, "heal": 100.0, "eta": 1.0,
             "cerulean": 100.0, "cerulean_cap": None, "items": [], "crimson_share": 1.0}
    D, _, N, _, _ = score(np.array([10.0]), 0.0, 1.0, np.array([1.0]), np.array([400.0]), np.array([100.0]),
                          np.array([1.0]), np.array([50.0]), fight, "pool")
    check(np.allclose(N[0], [12.0, 10.0, 8.0]), "score: life is (HP + Crimson x heal) / drain, a Cerulean takes a Crimson's place")
    check(np.allclose(D[0], 5.0 * N[0]), "score: a spell that costs nothing deals win x hit per engagement lived")
    _, _, N2, _, _ = score(np.array([10.0]), 0.0, 1.0, np.array([1.0]), np.array([100.0]), np.array([200.0]),
                           np.array([1.0]), np.array([50.0]), fight, "pool")
    check(np.allclose(N2[0], [(100 + 2 * 50) / 100, (100 + 50) / 100, 1.0]),
          "score: a Crimson heals no more than max HP less the drain it is drunk at")

    syn = Line("Test", "int", "-", "-", cap=99)
    w0 = _Synthetic(stagger=False)
    g0 = _syn_search(w0, syn, _SynGear(w0))
    check(g0["set"]["armor_poise"] == 0.0, f"free poise (no stagger, no interrupt) drives poise to 0 ({g0['set']['armor_poise']:g})")
    w1 = _Synthetic(stagger=True)
    g1 = _syn_search(w1, syn, _SynGear(w1))
    check(g1["set"]["armor_poise"] > 0.0, f"a stagger share that falls with poise buys poise ({g1['set']['armor_poise']:g})")
    w2 = _Synthetic(stagger=True, items=[(1000.0, 1000.0)])
    g2 = _syn_search(w2, syn, _SynGear(w2))
    check(g2["mnd"] == 10 and g2["n_c"] == 0,
          f"FP items that pay every cast leave Mind at the class base, no Cerulean (Mind {g2['mnd']}, blue {g2['n_c']})")
    w3 = _Synthetic(stagger=True, casts_fp=60.0)
    g3 = _syn_search(w3, replace(syn, casts=2.0), _SynGear(w3))
    check(g3["mnd"] > 10, f"a want past the bar each engagement buys Mind (Mind {g3['mnd']}, blue {g3['n_c']})")
    w4 = _Synthetic(stagger=True, casts_fp=18.0)
    g4 = _syn_search(w4, replace(syn, casts=3.0), _SynGear(w4))
    check(g4["n_c"] > 0 or g4["mnd"] > 10, f"a want under the bar but past it over the fight buys Mind or blue "
                                           f"(Mind {g4['mnd']}, blue {g4['n_c']})")

    # A kit: an FP-free attack (`free`) is paid whatever the plan, and a spell beside it is paid as
    # alone; the share-weighted kit is their sum.
    Df, sh_f, Nf, _, _ = score(np.array([0.0]), np.array([0.0]), 1.0, np.array([1.0]), np.array([400.0]),
                               np.array([100.0]), np.array([1.0]), np.array([50.0]), fight, "pool", np.array([10.0]))
    Ds, _, _, _, _ = score(np.array([6.0]), np.array([18.0]), 1.0, np.array([1.0]), np.array([400.0]),
                           np.array([100.0]), np.array([1.0]), np.array([50.0]), fight, "pool")
    Dk, _, _, _, _ = score(np.array([6.0]), np.array([18.0]), 1.0, np.array([1.0]), np.array([400.0]),
                           np.array([100.0]), np.array([1.0]), np.array([50.0]), fight, "pool", np.array([10.0]))
    check(np.allclose(Df[0], 5.0 * Nf[0]) and np.allclose(sh_f[0], 1.0) and np.allclose(Dk, Ds + Df),
          "score: an FP-free attack deals win x hit per engagement lived, always paid, and adds to a spell beside it")
    for base in ({k: 10 for k in ATTRS}, {"vig": 12, "mnd": 8, "end": 12, "str": 15, "dex": 11, "int": 7, "fth": 8,
                                          "arc": 9}):
        for mains, cap in ((("str",), 80), (("str", "dex"), 80), (("arc",), 80), (("int", "fth"), 80)):
            steps = fill_steps(base, mains, cap, 400)
            want = [SIM.allocate_line(base, sum(base.values()) + n, base["vig"], base["end"], mains, cap)
                    for n in range(0, 401, 37)]
            got_ = [steps[n] if n < len(steps) else None for n in range(0, 401, 37)]
            check(all((w is None and g is None) or (w is not None and g == tuple(w[k] for k in DMG))
                      for w, g in zip(want, got_)),
                  f"fill_steps gives SIM.allocate_line's damage stats point by point ({'/'.join(mains)} to {cap})")
    melee = Line("Melee", "str", "", "", cap=99, mains=("str",), kit=(Weapon("w"),), carried=0.0)
    w5 = _Synthetic(stagger=True)
    g5 = search(melee, w5, _SynGear(w5, melee), list(range(10, 41)), list(range(10, 31)), list(range(10, 41)), [None])
    check(g5["mnd"] == 10 and g5["n_c"] == 0 and g5["set"]["armor_poise"] > 0.0,
          f"a melee kit (an FP-free R1 on STR): Mind stays at the base with no Cerulean, the poise a stagger share "
          f"buys is bought (Mind {g5['mnd']}, blue {g5['n_c']}, poise {g5['set']['armor_poise']:g})")

    # Real tables, when the regulation is installed: the flat defense helper against DEF.defender.
    try:
        t = DEF.Tables()
    except (OSError, SystemExit, KeyError, ValueError) as exc:
        print(f"skip real-table checks: {exc}")
        t = None
    if t is not None:
        g = t.graph
        for st in ({"vig": 45, "mnd": 30, "end": 22, "str": 12, "dex": 14, "int": 80, "fth": 7, "arc": 9},
                   {"vig": 60, "mnd": 10, "end": 30, "str": 16, "dex": 9, "int": 9, "fth": 80, "arc": 6}):
            me = DEF.defender(t, st, (), (), None, pvp=True)
            lvl = DEF.calc_correct(g[DEF.LEVEL_DEF_GRAPH], sum(st.values()))
            mine = {el: math.trunc(lvl + (DEF.calc_correct(g[DEF.STAT_DEF_GRAPH[el][1]], st[ELEMENT_STAT[i]])
                                          if DEF.STAT_DEF_GRAPH[el] else 0.0))
                    for i, el in enumerate(AT.ELEMENTS)}
            check(mine == {el: me["defense"][el] for el in AT.ELEMENTS},
                  f"flat defense per element is DEF.defender's ({mine})")
    print("selftest", "passed" if ok else "FAILED")
    return 0 if ok else 1


def main() -> int:
    if "--selftest" in sys.argv[1:]:
        return selftest()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--line", action="append", choices=sorted(LINES), help="line to fit (repeatable; default both)")
    ap.add_argument("--rl", type=int, default=150)
    ap.add_argument("--fight", default="invasion", choices=("invasion", "duel"))
    ap.add_argument("--json", type=Path)
    ap.add_argument("--selftest", action="store_true", help="check the pure parts on synthetic numbers")
    a = ap.parse_args()
    world = GameWorld(a.rl, fight=a.fight)
    f = world.fight
    print(f"fight: {f['kind']}, {f['engagements']:.1f} engagements, {f['attackers']:.2f} attackers at once, "
          f"{f['flasks']} flasks; FP items: {'; '.join(f['item_names'])}; melee poise floor {world.floor}")
    report(a.line or sorted(LINES), world, a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
