#!/usr/bin/env python3
"""Best stat spread, class and affinity for one Elden Ring weapon at one rune level.

    python3 scripts/er-builds-optimize.py "Devourer's Scepter" --rl 150
    python3 scripts/er-builds-optimize.py Uchigatana --rl 125 --two-handed --floor vig=50
    python3 scripts/er-builds-optimize.py "Rivers of Blood" --rl 150 --objective ar
    python3 scripts/er-builds-optimize.py --weapons-for str=42,dex=42,int=8,fth=25,arc=6 --rl 150

`--weapons-for` turns the question around: every armament the game names, every affinity it has,
one- and two-handed, at max level, ranked by the same objective for a fixed stat block. Anything
whose requirements the stats miss is left out, since the 40% penalty makes it a strictly worse
pick than something the stats can wield.

What "best" means here, stated so it can be argued with:

* Survivability is a floor, not a trade. Vigor, Mind and Endurance start at the median of corpus builds
  at this RL that use this weapon (the whole pool when fewer than `--min-peers` do), and `--floor`
  overrides any of them. The corpus is `scripts/er-builds-scrape.py`'s mirror. With `--floors pvp`
  (default) the pool is the window's PvP builds; the sweeps narrow it further to PvP builds of the
  weapon's stat archetype (the damage stat that raises its AR most, `candidate_archetype`), since
  a STR PvP build carries Endurance 45 where the all-builds median is 33.
* Roll tier is a floor too (`--roll medium`, default): Endurance rises until the weapon plus the pool's
  median rest-of-kit load (armor, talismans, the other weapons) is at most 70% of max load
  (`GetWeightType` 0x14068c630). Load talismans are not assumed.
* Every requirement of the weapon is met, two-handing's STR x1.5 included. An unmet requirement
  costs the whole element 40% (`docs/er-mechanics/attack-rating.md`), which no spread recovers.
* Every other point goes where it buys the most `--objective`:
  - `damage` (default): one motion-value-100 hit on the median defender of the RL window, using
    the defense curve and absorption of `docs/er-mechanics/defense.md`. Defense and absorption are
    the planner's computed values for those builds, which agree with the game model on 99% and
    92% of builds.
  - `ar`: the attack-rating total, which overvalues splitting damage across elements.
* Every starting class and every affinity the weapon has is tried; the level is the weapon's max.
* A physical build on a greasable affinity is scored with its best DLC grease element. Physical
  means most infusable weapons the stats can wield do best (by AR, ungreased) on Standard, Heavy,
  Keen or Quality -- `physical_build` (`--grease`, default the drawstring tier, +135 flat before defense; `none`
  turns it off). The grease sweep measured that choice winning on 98% of infusable weapons at RL
  150-200, so leaving grease out would rank the wrong affinity first.

`--grease-sweep` covers every melee armament: the greasable ones as above, and since 2026-09-29
fixed-affinity weapons too (`sweep_kind`: uniques with their one affinity and own skill, and
ash-of-war weapons with no greasable affinity such as shields). Each row carries `weight`, the
`weight_charge` of its highest-damage configuration: the mean over the floor pool's kits of the
damage left after the Endurance each kit needs to keep medium roll with this weapon, relative to
the row's own damage, plus `fit`, the share of kits that already carry it at medium roll.

Gear comes from the embeddings (`scripts/er-builds-embed.py fit`): the talismans, armor, ashes of
war and tears that EASE scores highest for a build holding this weapon.

The spread is found by a greedy walk that buys whichever stat gains most per point over the next
1..10 points, then a swap pass that moves single points between stats until nothing improves.
AR is not concave across soft caps, so the look-ahead is what lets it cross one; the swap pass is
what makes the result a local optimum rather than an order artefact.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import statistics
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


AR = _sibling("er-mechanics-ar")
DEF = _sibling("er-mechanics-defense")
RES = _sibling("er-mechanics-resources")
EMBED = _sibling("er-builds-embed")

# Planner stat keys; `vit` is Endurance there and in er-mechanics-resources.
STATS = ("vig", "mnd", "vit", "str", "dex", "int", "fth", "arc")
SURVIVAL = ("vig", "mnd", "vit")
DAMAGE_STATS = ("str", "dex", "int", "fth", "arc")
LEVEL_OFFSET = 79
STAT_CAP = 99
LOOKAHEAD = 10
ELEMENTS = ("physical", "magic", "fire", "lightning", "holy")
ABSORB_KEY = {"physical": "physical", "magic": "magic", "fire": "fire", "lightning": "lightning", "holy": "holy"}


# --------------------------------------------------------------------------------------------
# corpus: floors and the bracket defender

#: Planner tags that mark a build as made for player fights (same rule as `er-builds-pvp.is_pvp`).
PVP_TAGS = {"Invasions", "Duels", "Co-op/Gank", "2v2", "Ladder", "Fishing"}
#: Weapon positions 0-2 of a planner set are the right hand, 3-5 the left.
RIGHT_HAND = (0, 1, 2)
#: Burden / max load above this is heavy roll (`GetWeightType` 0x14068c630, resources.md section 5).
MEDIUM_ROLL_MAX = RES.WEIGHT_HEAVY
FLOOR_SOURCES = ("pvp", "all")


def is_pvp(build: dict) -> bool:
    if build.get("isPvE") is True:
        return False
    return build.get("isPvE") is False or bool(set(build.get("tags") or []) & PVP_TAGS)


def archetype(stats: dict) -> str:
    """The damage stat a build or spread invests most in (ties go to the earlier of DAMAGE_STATS)."""
    return max(DAMAGE_STATS, key=lambda k: stats[k])


_WEIGHTS: dict = {}


def _weights():
    """(weapon weight by planner name, resource model for armor and talisman weights)."""
    if not _WEIGHTS:
        import unicodedata
        rows, _, _ = AR.EPR.rows(AR.EPR.param_bytes(AR.EPR.load(None), "EquipParamWeapon"), ["weight"])
        names = AR.EPR.row_names("EquipParamWeapon")
        by_name = {}
        for r in sorted(rows, key=lambda r: r["id"]):
            n = names.get(r["id"])
            if n and r["id"] % 10000 == 0 and n not in by_name:
                by_name[n] = r["weight"]
        _WEIGHTS["plain"] = lambda s: "".join(c for c in unicodedata.normalize("NFKD", s)
                                              if not unicodedata.combining(c))
        _WEIGHTS["weapon"], _WEIGHTS["model"] = by_name, RES.Model()
    return _WEIGHTS["weapon"], _WEIGHTS["model"], _WEIGHTS["plain"]


def load_profile(build: dict) -> dict | None:
    """What a swapped-in primary weapon has to fit beside, or None when a weapon name is unknown:
    `rest` is the burden of everything but the heaviest right-hand weapon, `rate` the max-load
    multiplier of the worn talismans, armor and great rune (Arsenal Charm x1.15 and up), and
    `end_bonus` the Endurance they add (Radagon's Soreseal)."""
    rest = other_load(build)
    if rest is None:
        return None
    _, model, _ = _weights()
    pa = EMBED.active_set(build, "protectors")
    armor = [s["name"] for part in ("head", "body", "arms", "legs")
             for s in EMBED.equipped(((build.get("protectors") or {}).get(part) or {}).get("slots"), pa)]
    tal = [s["name"] for s in EMBED.equipped((build.get("talismans") or {}).get("slots"),
                                             EMBED.active_set(build, "talismans"))]
    effects, _ = model.speffects_for(tal, armor, build.get("greatRune"))
    bonus = sum(int(e[RES.STAT_FIELDS["vit"]]) for e in effects)
    return {"rest": rest, "rate": model.rate(effects, "equipWeightChangeRate"), "end_bonus": bonus}


def other_load(build: dict) -> float | None:
    """Burden of everything but the heaviest right-hand weapon. None when a weapon name is unknown."""
    weapon_w, model, plain = _weights()
    weps, right = [], []
    active = EMBED.active_set(build, "weapons")
    for s in (build.get("inventory") or {}).get("slots") or []:
        es = s.get("equipSet")
        pos = (es[active] if active < len(es) else None) if isinstance(es, list) else s.get("equipIndex")
        if pos is None or not s.get("name"):
            continue
        w = weapon_w.get(plain(s["name"]))
        if w is None:
            return None
        weps.append(w)
        if pos in RIGHT_HAND:
            right.append(w)
    pa = EMBED.active_set(build, "protectors")
    armor = [s["name"] for part in ("head", "body", "arms", "legs")
             for s in EMBED.equipped(((build.get("protectors") or {}).get(part) or {}).get("slots"), pa)]
    tal = [s["name"] for s in EMBED.equipped((build.get("talismans") or {}).get("slots"),
                                             EMBED.active_set(build, "talismans"))]
    total = model.burden(weps, armor, tal)
    return total - (max(right) if right else 0.0)


def corpus_rows(mirror: Path, rl_lo: int, rl_hi: int) -> list[dict]:
    out = []
    for line in mirror.read_text().splitlines():
        b = json.loads(line)["build"]
        st = EMBED.stats_of(b)
        if st is None or not rl_lo <= st["rl"] <= rl_hi:
            continue
        if sum(st[k] for k in EMBED.ATTRS) - LEVEL_OFFSET != st["rl"]:
            continue
        load = load_profile(b)
        if load is not None:
            load["end"] = st["vit"]  # the build's own Endurance, for `weight_charge`'s fit share
        out.append({"stats": st, "tokens": EMBED.tokens(b), "computed": b.get("computed") or {},
                    "pvp": is_pvp(b), "archetype": archetype(st), "load": load})
    return out


def floor_pool(rows: list[dict], source: str = "all", arch: str | None = None,
               min_peers: int = 0) -> tuple[list[dict], str]:
    """The corpus rows survivability floors are taken from: PvP builds of the same archetype when
    `source` is `pvp` and there are at least `min_peers` of them, else all PvP builds, else all."""
    if source == "pvp":
        pvp = [r for r in rows if r["pvp"]]
        same = [r for r in pvp if arch and r["archetype"] == arch]
        if arch and len(same) >= max(min_peers, 1):
            return same, f"{len(same)} PvP {arch.upper()} builds"
        if len(pvp) >= max(min_peers, 1):
            return pvp, f"{len(pvp)} PvP builds"
    return rows, f"all {len(rows)} builds"


def floors(rows: list[dict], weapon: str, min_peers: int, source: str = "all",
           arch: str | None = None) -> tuple[dict, int, str]:
    pool, pool_scope = floor_pool(rows, source, arch, min_peers)
    peers = [r for r in pool if f"w:{weapon}" in r["tokens"]]
    src, scope = (peers, f"{len(peers)} builds with {weapon} (of {pool_scope})") if len(peers) >= min_peers else \
        (pool, f"{pool_scope} (only {len(peers)} use {weapon})")
    return {k: int(statistics.median(r["stats"][k] for r in src)) for k in SURVIVAL}, len(peers), scope


def load_pool(rows: list[dict], source: str = "all", arch: str | None = None,
              min_peers: int = 0) -> list[dict]:
    """The floor pool's `load_profile`s."""
    pool, _ = floor_pool(rows, source, arch, min_peers)
    return [r["load"] for r in pool if r["load"] is not None]


_LOAD_CURVE: list = []


def end_for_load(load: float, rate: float = 1.0, end_bonus: int = 0) -> int:
    """The least base Endurance whose max load (`rate` x curve 220 at Endurance + `end_bonus`) keeps
    `load` at medium roll; STAT_CAP + 1 when none does."""
    if not _LOAD_CURVE:
        model = _weights()[1]
        _LOAD_CURVE.extend(model.calc_correct(RES.GRAPH_EQUIP_LOAD, e) for e in range(STAT_CAP + 1))
    for end in range(1, STAT_CAP + 1):
        if load <= MEDIUM_ROLL_MAX * rate * _LOAD_CURVE[min(STAT_CAP, end + end_bonus)]:
            return end
    return STAT_CAP + 1


def medium_roll_end(weight: float, pool: list[dict]) -> int:
    """The Endurance the pool's median build needs to keep medium roll with `weight` swapped in for
    its heaviest right-hand weapon: each build's own requirement (its rest-of-kit burden, load
    multiplier and Endurance bonus), then the median. Per build rather than from a median burden,
    because half the STR PvP builds wear an Arsenal talisman and that is not a median effect."""
    if not pool:
        return 1
    return min(STAT_CAP, int(statistics.median(end_for_load(weight + p["rest"], p["rate"], p["end_bonus"])
                                               for p in pool)))


#: Grid step, in Endurance points, at which `weight_charge` evaluates damage and interpolates.
WEIGHT_CHARGE_END_STEP = 3


def weight_charge(weight: float, pool: list[dict], end_floor: int, dmg_at, end_row: int | None = None) -> dict:
    """What carrying a weapon of `weight` at medium roll costs, against the corpus kits in `pool`.

    Each pool entry is a `load_profile` (its rest-of-kit burden, load multiplier, Endurance bonus
    and, when known, the build's own Endurance `end`). For each one the Endurance it needs is
    `need_i = max(end_floor, end_for_load(weight + rest_i, rate_i, end_bonus_i))`: that build's
    kit with this weapon swapped in for its heaviest right-hand one, kept at medium roll, never
    below the survivability floor. Endurance above the floor comes out of the damage stats, so
    `dmg_at(e)` is the damage of the weapon's best spread with Endurance `e` (the sweep's walk),
    or None when the weapon's requirements and the floors leave no spread at `e`.

    Returns:
      `fit`      share of pool builds that keep medium roll at their own Endurance (needs `end`),
                 the corpus accessibility figure of giant-crusher-adoption-gap.md section 1;
      `end_mean` mean of `need_i`; `over` share of the pool with `need_i` above the floor;
      `dmg_free` damage at `end_floor`, the weapon weightless;
      `dmg_expected` mean over the pool of `dmg_at(need_i)`, linear between grid points
                 `WEIGHT_CHARGE_END_STEP` apart; a `need_i` with no spread (or above STAT_CAP)
                 takes the damage of the highest feasible grid point, that build going heavy
                 roll rather than dropping the weapon (`INFERRED` choice);
      `factor`   `dmg_expected / dmg_at(end_row)` when `end_row` is given (the Endurance the
                 build's damage was computed at, so the charge is not counted twice), else
                 `dmg_expected / dmg_free`.
    None when the pool is empty or the weapon has no spread even at the floor."""
    if not pool:
        return None
    raw = [end_for_load(weight + p["rest"], p["rate"], p["end_bonus"]) for p in pool]
    needs = [min(STAT_CAP + 1, max(end_floor, n)) for n in raw]
    known = [(n, p["end"]) for n, p in zip(raw, pool) if p.get("end") is not None]
    cache: dict = {}

    def at(e: int):
        if e not in cache:
            cache[e] = dmg_at(e) if e <= STAT_CAP else None
        return cache[e]

    free = at(end_floor)
    if free is None:
        return None
    top = max(needs)
    grid = list(range(end_floor, top + 1, WEIGHT_CHARGE_END_STEP))
    if grid[-1] != top:
        grid.append(top)
    pts = []
    for e in grid:
        d = at(e)
        if d is None:
            break
        pts.append((e, d))

    def interp(e: int) -> float:
        if e >= pts[-1][0]:
            return pts[-1][1]
        for (e0, d0), (e1, d1) in zip(pts, pts[1:]):
            if e0 <= e <= e1:
                return d0 + (d1 - d0) * (e - e0) / (e1 - e0)
        return pts[0][1]

    expected = statistics.fmean(interp(n) for n in needs)
    ref = free
    if end_row is not None:
        ref = at(end_row) if end_row <= pts[-1][0] and at(end_row) is not None else interp(end_row)
    return {"weight": weight, "pool": len(pool), "end_floor": end_floor, "end_row": end_row,
            "fit": (sum(n <= own for n, own in known) / len(known)) if known else None,
            "end_mean": statistics.fmean(needs), "over": sum(n > end_floor for n in needs) / len(needs),
            "dmg_free": free, "dmg_expected": expected, "factor": expected / ref if ref else 1.0}


def candidate_archetype(tables, weapon, affinity: str, level: int, two_handed: bool, need: dict) -> str:
    """The damage stat ten more points of which raise this weapon's AR most, from its requirements:
    the archetype a build around it belongs to, so its floors come from builds of that kind."""
    base = {k: max(10, need.get(k, 0)) for k in DAMAGE_STATS}
    ar0 = AR.attack_rating(tables, weapon, affinity, level, base, two_handed)["total"]
    gain = {k: AR.attack_rating(tables, weapon, affinity, level, dict(base, **{k: min(STAT_CAP, base[k] + 10)}),
                                two_handed)["total"] - ar0 for k in DAMAGE_STATS}
    return max(DAMAGE_STATS, key=lambda k: gain[k])


def sweep_floor(weapon_id: int, affinity: str, level: int, two: bool, need: dict, rl: int) -> dict:
    """A sweep candidate's survivability floor at `rl` (`_SWEEP` holds the per-RL corpus stats)."""
    t = _SWEEP["tables"]
    arch = candidate_archetype(t, weapon_id, affinity, level, two, need)
    fl = dict(_SWEEP["floors"][rl][arch])
    if _SWEEP.get("roll") == "medium":
        weight = t.weapons[weapon_id + AR.AFFINITIES.index(affinity) * 100]["weight"]
        fl["vit"] = max(fl["vit"], medium_roll_end(weight, _SWEEP["other_load"][rl][arch]))
    return fl


def sweep_corpus_stats(rows_by_rl: dict, rls, min_peers: int, source: str) -> tuple[dict, dict]:
    """Per RL and archetype: the survivability floor and the pool's load profiles."""
    fl, load = {}, {}
    for rl in rls:
        rows = rows_by_rl[rl]
        fl[rl] = {a: floors(rows, "", min_peers, source, a)[0] for a in DAMAGE_STATS}
        load[rl] = {a: load_pool(rows, source, a, min_peers) for a in DAMAGE_STATS}
    return fl, load


def bracket_defender(rows: list[dict]) -> dict:
    """The median defender of the window, in the shape `er-mechanics-defense.damage` reads."""
    have = [r["computed"] for r in rows if r["computed"].get("defenses") and r["computed"].get("absorption")]
    if not have:
        raise SystemExit("no corpus build in the RL window carries computed defenses")
    dfn = {el: statistics.median(c["defenses"][el] for c in have) for el in ELEMENTS}
    types = ("physical", "strike", "slash", "pierce", "magic", "fire", "lightning", "holy")
    armor = {k: 1.0 - statistics.median(c["absorption"][k if k != "pierce" else "pierce"] for c in have) / 100.0
             for k in types}
    return {"defense": dfn, "armor_mult": armor, "effect_mult": {k: 1.0 for k in types}, "n": len(have)}


# --------------------------------------------------------------------------------------------
# search

class Scorer:
    def __init__(self, tables, weapon, affinity, level, two_handed, objective, defender, grease=None):
        self.args = (tables, weapon, affinity, level)
        self.two_handed, self.objective, self.defender = two_handed, objective, defender
        self.grease = grease  # (element, flat attack) or None
        self.cache: dict = {}

    def ar(self, st: dict) -> dict:
        key = tuple(st[k] for k in DAMAGE_STATS)
        if key not in self.cache:
            t, w, a, lv = self.args
            self.cache[key] = AR.attack_rating(t, w, a, lv, {k: st[k] for k in DAMAGE_STATS}, self.two_handed)
        return self.cache[key]

    def by_element(self, st: dict) -> dict:
        r = self.ar(st)
        by = {el: r["damage"].get(el, {}).get("total", 0.0) for el in ELEMENTS}
        if self.grease:
            by[self.grease[0]] += self.grease[1]
        return by

    def score(self, st: dict) -> float:
        by = self.by_element(st)
        if self.objective == "ar":
            return sum(by.values())
        return DEF.damage(by, 100.0, self.defender)["total"]


def requirements(tables, weapon: str, affinity: str, level: int, two_handed: bool) -> dict:
    """The smallest value of each damage stat at which that stat stops costing the 40% penalty.
    Found by asking the calculator rather than re-reading requirement fields, so two-handing and
    anything else it models are honoured by construction."""
    need = {}
    base = {k: STAT_CAP for k in DAMAGE_STATS}
    for k in DAMAGE_STATS:
        lo, hi = 0, STAT_CAP
        # The penalty drops the element's multiplier below 1, so its scaling term turns negative;
        # it is a step in the stat, so the first value that clears it bisects cleanly.
        while lo < hi:
            mid = (lo + hi) // 2
            r = AR.attack_rating(tables, weapon, affinity, level, dict(base, **{k: mid}), two_handed)
            if any(v.get("scaling", 0.0) < 0 for v in r["damage"].values()):
                lo = mid + 1
            else:
                hi = mid
        need[k] = lo
    return need


def spend(st: dict, points: int, scorer: Scorer, stats=DAMAGE_STATS) -> tuple[dict, int]:
    st = dict(st)
    cur = scorer.score(st)
    while points > 0:
        best = None
        for k in stats:
            for n in range(1, min(LOOKAHEAD, points, STAT_CAP - st[k]) + 1):
                gain = (scorer.score(dict(st, **{k: st[k] + n})) - cur) / n
                if best is None or gain > best[0]:
                    best = (gain, k, n)
        if best is None or best[0] <= 0:
            break
        _, k, n = best
        st[k] += n
        points -= n
        cur = scorer.score(st)
    # Swap pass: move one point from any stat above its floor to any other, while it helps.
    improved = True
    while improved:
        improved = False
        for a in stats:
            for b in stats:
                if a == b or st[a] <= scorer.floor[a] or st[b] >= STAT_CAP:
                    continue
                trial = dict(st, **{a: st[a] - 1, b: st[b] + 1})
                s = scorer.score(trial)
                if s > cur + 1e-9:
                    st, cur, improved = trial, s, True
    return st, points


PHYSICAL_AFFINITIES = {"Standard", "Heavy", "Keen", "Quality"}
_PHYSICAL: dict = {}


def physical_build(tables, st: dict, defender: dict) -> tuple[bool, int, int]:
    """Whether a stat block is a physical build, and the vote behind it.

    Every infusable armament the stats can wield (some grip meets every requirement) votes with
    the affinity that deals it the most damage per hit on `defender`, grease left out so the
    answer does not assume itself. The build is physical when most votes land on Standard,
    Heavy, Keen or Quality, the affinities a grease can be applied to. Returns (physical,
    physical votes, votes).

    The vote is by damage and not by AR because AR sums a split weapon's elements at face value:
    measured on STR 88 / DEX 9, Fire wins 90 of 98 votes by AR and Heavy wins 95 of 98 by damage,
    since each element meets its own defense and a split hit loses more to it."""
    key = tuple(st[k] for k in DAMAGE_STATS)
    if key in _PHYSICAL:
        return _PHYSICAL[key]
    dmg = {k: st[k] for k in DAMAGE_STATS}
    real = _real_weapon_ids()
    phys = votes = 0
    for base_id, wep in tables.weapons.items():
        if base_id % 10000 or wep.get("gemMountType") != INFUSABLE_GEM_MOUNT or not tables.names.get(base_id):
            continue
        if (real and base_id not in real) or wep.get("wepType") in AMMO_WEP_TYPES:
            continue
        best = (-1.0, None)
        for aff in affinities(tables, base_id):
            row = tables.weapons[base_id + AR.AFFINITIES.index(aff) * 100]
            level = tables.max_level(row["reinforceTypeId"])
            for two in (False, True):
                r = AR.attack_rating(tables, base_id, aff, level, dmg, two)
                if any(v.get("scaling", 0.0) < 0 for v in r["damage"].values()):
                    continue
                by = {el: r["damage"].get(el, {}).get("total", 0.0) for el in ELEMENTS}
                d = DEF.damage(by, 100.0, defender)["total"]
                if d > best[0]:
                    best = (d, aff)
        if best[1]:
            votes += 1
            phys += best[1] in PHYSICAL_AFFINITIES
    _PHYSICAL[key] = (votes > 0 and 2 * phys > votes, phys, votes)
    return _PHYSICAL[key]


def _real_weapon_ids() -> set:
    """Armament rows the game's own message files name (`scripts/er-builds-catalog.py`), so cut
    content with a regulation row does not vote."""
    path = CACHE / "catalog.json"
    return set(json.loads(path.read_text())["armament"].values()) if path.exists() else set()


def greasable(tables, weapon, affinity) -> bool:
    """`isEnhance` on the affinity row: `CanUseGoods` refuses a grease without it (grease.md)."""
    return bool(tables.weapons[tables.find_weapon(weapon, affinity)].get("isEnhance"))


def grease_options(tables, weapon, affinity, tier):
    if tier is None or not greasable(tables, weapon, affinity):
        return [None]
    return [None] + [(el, GREASES[tier]) for el in GREASE_ELEMENTS]


def optimize(tables, model, weapon, rl, two_handed, objective, floor, defender, affinities,
             grease_tier=None, keep=10):
    """Every class and affinity; a greasable affinity is also tried with each grease element of
    `grease_tier`. Greased results are kept only when the spread they land on is a physical build
    (`physical_build`); that check costs about a second, so it runs on results in score order and
    stops once enough have passed."""
    import multiprocessing as mp
    jobs = []
    for aff in affinities:
        try:
            probe = AR.attack_rating(tables, weapon, aff, 0, {k: 10 for k in DAMAGE_STATS}, two_handed)
        except (KeyError, ValueError, SystemExit):
            continue
        level = probe["max_level"]
        need = requirements(tables, weapon, aff, level, two_handed)
        for grease in grease_options(tables, weapon, aff, grease_tier):
            for cls in RES.CLASS_ROWS:
                cls_level, base = model.class_base(cls)
                st = {k: max(base[k], floor.get(k, 0), need.get(k, 0)) for k in STATS}
                if rl - (sum(st.values()) - LEVEL_OFFSET) < 0 or rl < cls_level:
                    continue
                jobs.append((cls, aff, level, grease, st))
    _OPT.update(tables=tables, weapon=weapon, rl=rl, two=two_handed, objective=objective, defender=defender)
    with mp.get_context("fork").Pool() as pool:
        results = [r for r in pool.map(_optimize_one, jobs) if r]
    results.sort(key=lambda r: -r["score"])
    kept = []
    for r in results:
        if r["grease"] and not physical_build(tables, r["stats"], defender)[0]:
            continue
        kept.append(r)
        if len(kept) > keep:
            break
    return kept


_OPT: dict = {}


def _optimize_one(job):
    cls, aff, level, grease, st = job
    o = _OPT
    scorer = Scorer(o["tables"], o["weapon"], aff, level, o["two"], o["objective"], o["defender"], grease)
    scorer.floor = dict(st)
    st, left = spend(st, o["rl"] - (sum(st.values()) - LEVEL_OFFSET), scorer)
    # Points no damage stat wants (every one capped or gaining nothing) go to survival.
    for k in ("vig", "vit", "mnd"):
        add = min(left, STAT_CAP - st[k])
        st[k] += add
        left -= add
    return {"class": cls, "affinity": aff, "level": level, "stats": st, "grease": grease,
            "score": scorer.score(st), "ar": scorer.ar(st), "by_element": scorer.by_element(st)}


# --------------------------------------------------------------------------------------------
# weapons for a stat block

AMMO_WEP_TYPES = {81, 83, 85, 86}  # arrows, greatarrows, bolts, greatbolts: not wielded
INFUSABLE_GEM_MOUNT = 2


def affinities(tables, base_id: int) -> list[str]:
    """The affinities a player can actually give this weapon.

    A weapon that takes no ash of war (`gemMountType` other than 2) still has a full set of
    affinity rows in the regulation, e.g. Serpentbone Blade, but they are placeholders that no
    whetblade can reach, so it gets its standard row only."""
    if tables.weapons[base_id].get("gemMountType") != INFUSABLE_GEM_MOUNT:
        return ["Standard"]
    return [a for i, a in enumerate(AR.AFFINITIES) if base_id + i * 100 in tables.weapons]


def weapons_for(tables, stats: dict, objective: str, defender: dict, catalog: Path,
                grease_tier=None) -> list[dict]:
    greased = grease_tier is not None and physical_build(tables, stats, defender)[0]
    names = json.loads(catalog.read_text())["armament"] if catalog.exists() else {}
    real = set(names.values())
    out = []
    for base_id, wep in tables.weapons.items():
        if base_id % 10000 or (real and base_id not in real) or wep.get("wepType") in AMMO_WEP_TYPES:
            continue
        name = tables.names.get(base_id)
        if not name:
            continue
        for aff in affinities(tables, base_id):
            aff_i = AR.AFFINITIES.index(aff)
            level = tables.max_level(tables.weapons[base_id + aff_i * 100]["reinforceTypeId"])
            for two in (False, True):
                r = AR.attack_rating(tables, base_id, aff, level, stats, two)
                if any(v.get("scaling", 0.0) < 0 for v in r["damage"].values()):
                    continue  # a requirement is unmet
                options = [None]
                if greased and tables.weapons[base_id + aff_i * 100].get("isEnhance"):
                    options = [(el, GREASES[grease_tier]) for el in GREASE_ELEMENTS]
                for g in options:
                    by = {el: r["damage"].get(el, {}).get("total", 0.0) for el in ELEMENTS}
                    if g:
                        by[g[0]] += g[1]
                    score = sum(by.values()) if objective == "ar" else DEF.damage(by, 100.0, defender)["total"]
                    out.append({"name": name, "affinity": aff, "level": level, "two": two,
                                "score": score, "ar": r, "grease": g, "total_ar": sum(by.values())})
    # One row per weapon: its best affinity and grip.
    best: dict[str, dict] = {}
    for r in out:
        if r["name"] not in best or r["score"] > best[r["name"]]["score"]:
            best[r["name"]] = r
    return sorted(best.values(), key=lambda r: -r["score"])


# --------------------------------------------------------------------------------------------
# quality sweep: does Quality ever win on a build made for another affinity?

QUALITY = "Quality"
SWEEP_CLASS = "Wretch"  # every base stat 10 at level 1, so no class shapes the spread
_SWEEP: dict = {}


def _snapshots(tables, weapon_id, aff, level, two, floor, need, rls, defender,
               objective="ar", grease=None, base=None) -> dict:
    """Optimise one affinity for every RL in `rls` from a single greedy walk.

    The walk from the floors to the highest RL passes through every lower RL's point count, so
    each RL takes the state the walk held there and then runs its own swap pass. `base` is a
    starting class's level-1 stats; without it every stat starts at 10, the Wretch."""
    scorer = Scorer(tables, weapon_id, aff, level, two, objective, defender, grease)
    start = {k: max((base or {}).get(k, 10), floor.get(k, 0), need.get(k, 0)) for k in STATS}
    scorer.floor = dict(start)
    used = sum(start.values()) - LEVEL_OFFSET
    out = {}
    st = dict(start)
    for rl in sorted(rls):
        if rl < used:
            continue
        spent = sum(st.values()) - LEVEL_OFFSET
        st, _ = spend(st, rl - spent, scorer)
        # A look-ahead step can land a few points short of this RL when no stat gains; top up
        # survival so every snapshot sits exactly at its RL.
        gap = rl - (sum(st.values()) - LEVEL_OFFSET)
        for k in ("vig", "vit", "mnd"):
            add = min(gap, STAT_CAP - st[k])
            st[k] += add
            gap -= add
        out[rl] = (dict(st), scorer.score(st))
    return out


def _sweep_one(job):
    weapon_id, two = job
    t = _SWEEP["tables"]
    rls, floors_by_rl = _SWEEP["rls"], _SWEEP["floors"]
    affs = affinities(t, weapon_id)
    if QUALITY not in affs or len(affs) < 2:
        return []
    level = t.max_level(t.weapons[weapon_id]["reinforceTypeId"])
    rows = []
    best: dict = {}  # rl -> (ar, affinity, stats)
    for aff in affs:
        if aff == QUALITY:
            continue
        need = requirements(t, weapon_id, aff, level, two)
        # Floors differ per RL window, so each RL gets its own walk only when its floors differ.
        by_floor: dict = {}
        for rl in rls:
            fl = sweep_floor(weapon_id, aff, level, two, need, rl)
            by_floor.setdefault(tuple(sorted(fl.items())), []).append(rl)
        for floor_key, group in by_floor.items():
            for rl, (st, ar) in _snapshots(t, weapon_id, aff, level, two, dict(floor_key), need, group,
                                           None).items():
                if rl not in best or ar > best[rl][0]:
                    best[rl] = (ar, aff, st)
    if best:
        for rl, (ar, aff, st) in sorted(best.items()):
            dmg = {k: st[k] for k in DAMAGE_STATS}
            per = {a: AR.attack_rating(t, weapon_id, a, level, dmg, two)["total"] for a in affs}
            top = max(per, key=per.get)
            rows.append({"weapon": t.names.get(weapon_id), "two": two, "rl": rl, "built_for": aff,
                         "built_ar": ar, "quality_ar": per[QUALITY], "best_on_build": top,
                         "best_ar": per[top], "stats": st})
    return rows


def quality_sweep(tables, rows_by_rl, rls, grips, jobs, out_path: Path, min_peers: int,
                  source: str = "pvp", roll: str = "medium") -> list[dict]:
    import multiprocessing as mp
    fl, load = sweep_corpus_stats(rows_by_rl, rls, min_peers, source)
    _SWEEP.update(tables=tables, rls=rls, floors=fl, other_load=load, roll=roll)
    weapon_ids = sorted(i for i in tables.weapons if i % 10000 == 0 and i + 300 in tables.weapons
                        and tables.names.get(i))
    work = [(w, two) for w in weapon_ids for two in grips]
    with mp.get_context("fork").Pool(jobs) as pool:
        results = [r for part in pool.imap_unordered(_sweep_one, work) for r in part]
    results.sort(key=lambda r: (r["weapon"], r["two"], r["rl"]))
    out_path.write_text("\n".join(json.dumps(r) for r in results) + "\n")
    return results


# --------------------------------------------------------------------------------------------
# grease sweep: buffable affinity plus grease against the best affinity that cannot be greased

# `SpEffectParam` <element>AttackPower of each grease's effect, read from the 1.17.1 regulation.
# That it is added flat to the element's attack, unscaled, is `COMMUNITY`, not traced.
GREASES = {
    "dlc-drawstring": 135,  # Drawstring Messmerfire/Dragonbolt/Royal Magic/Golden, 25 s
    "dlc": 120,             # Messmerfire/Dragonbolt/Royal Magic/Golden Grease, 60 s
    "drawstring": 110,      # Drawstring Fire/Lightning/Magic/Holy Grease, 11 s
    "base": 85,             # Fire/Lightning/Magic/Holy Grease, 60 s
}
GREASE_ELEMENTS = ("fire", "lightning", "magic", "holy")
GREASE_NAMES = {
    "dlc-drawstring": {"fire": "Drawstring Messmerfire Grease", "lightning": "Drawstring Dragonbolt Grease",
                       "magic": "Drawstring Royal Magic Grease", "holy": "Drawstring Golden Grease"},
    "dlc": {"fire": "Messmerfire Grease", "lightning": "Dragonbolt Grease",
            "magic": "Royal Magic Grease", "holy": "Golden Grease"},
    "drawstring": {el: f"Drawstring {el.title()} Grease" for el in GREASE_ELEMENTS},
    "base": {el: f"{el.title()} Grease" for el in GREASE_ELEMENTS},
}


#: Weapon types the fixed-affinity half of the grease sweep leaves out: ammunition, bows,
#: crossbows and ballistae (their damage is the projectile's), staves and seals (their damage is
#: the spell's), throwables and consumable-like rows (type 0), Unarmed and perfume bottles.
SWEEP_SKIP_WEP_TYPES = AMMO_WEP_TYPES | {0, 33, 50, 51, 53, 55, 56, 57, 61, 89}


def sweep_kind(tables, weapon_id: int, real: set) -> str | None:
    """How the grease sweep treats a weapon, or None when it skips it.

    `greasable`: an ash-of-war weapon with at least one greasable and one ungreasable affinity,
    the original sweep (greased buffable affinity against the best ungreasable one). The other two
    were left out before 2026-09-29 and are swept since, every affinity they have tried, greased
    only where `isEnhance` allows it: `unique`, a weapon that takes no ash of war (its one
    affinity and its own skill), and `ungreasable`, an ash-of-war weapon none of whose affinities
    takes grease (shields, hand-to-hand arts, Smithscript weapons). Those two skip weapons the
    game's message files do not name and `SWEEP_SKIP_WEP_TYPES`."""
    affs = affinities(tables, weapon_id)
    ids = [weapon_id + AR.AFFINITIES.index(a) * 100 for a in affs]
    buff = sum(bool(tables.weapons[i].get("isEnhance")) for i in ids)
    if buff and buff < len(ids):
        return "greasable"
    wep = tables.weapons[weapon_id]
    if (real and weapon_id not in real) or wep.get("wepType") in SWEEP_SKIP_WEP_TYPES:
        return None
    return "unique" if wep.get("gemMountType") != INFUSABLE_GEM_MOUNT else "ungreasable"


def _grease_one(job):
    weapon_id, two = job
    t = _SWEEP["tables"]
    rls, dfn, amount = _SWEEP["rls"], _SWEEP["defender"], _SWEEP["amount"]
    kind = sweep_kind(t, weapon_id, _SWEEP.get("real") or set())
    if kind is None:
        return []
    affs = affinities(t, weapon_id)
    ids = {a: weapon_id + AR.AFFINITIES.index(a) * 100 for a in affs}
    buff = [a for a in affs if t.weapons[ids[a]].get("isEnhance")]
    level = t.max_level(t.weapons[weapon_id]["reinforceTypeId"])
    rows = []
    cand: dict = {}  # rl -> list of (damage, affinity, grease element or None, stats, class)
    needs: dict = {}
    classes = _SWEEP.get("classes") or [(SWEEP_CLASS, None)]
    for aff in affs:
        need = needs[aff] = requirements(t, weapon_id, aff, level, two)
        by_floor: dict = {}
        for rl in rls:
            fl = sweep_floor(weapon_id, aff, level, two, need, rl)
            by_floor.setdefault(tuple(sorted(fl.items())), []).append(rl)
        greases = [(el, amount) for el in GREASE_ELEMENTS] if aff in buff else []
        # The original sweep tries a buffable affinity greased only; a fixed-affinity weapon is
        # also tried without, since it may have no ungreasable affinity to fall back on.
        if aff not in buff or kind != "greasable":
            greases = [None] + greases
        for floor_key, group in by_floor.items():
            for g in greases:
                for cls, base in classes:
                    snaps = _snapshots(t, weapon_id, aff, level, two, dict(floor_key), need, group, dfn, "damage", g,
                                       base)
                    for rl, (st, dmg) in snaps.items():
                        cand.setdefault(rl, []).append((dmg, aff, g[0] if g else None, st, cls))
    for rl, cs in sorted(cand.items()):
        e = max((c for c in cs if c[2] is None), key=lambda c: c[0], default=None)
        b = max((c for c in cs if c[2] is not None), key=lambda c: c[0], default=None)
        q = max((c for c in cs if c[1] == QUALITY and c[2] is not None), key=lambda c: c[0], default=None)
        row = {"weapon": t.names.get(weapon_id), "two": two, "rl": rl, "kind": kind,
               "elemental": {"dmg": e[0], "aff": e[1], "stats": e[3], "class": e[4]} if e else None,
               "greased": {"dmg": b[0], "aff": b[1], "grease": b[2], "stats": b[3], "class": b[4]} if b else None,
               "quality": ({"dmg": q[0], "grease": q[2], "stats": q[3], "class": q[4]} if q else None)}
        # The weight charge of the configuration `er-builds-pvp.build_for` picks (highest damage).
        win = max((c for c in (e, b, q) if c), key=lambda c: c[0])
        row["weight"] = _row_weight_charge(t, weapon_id, win[1], level, two, needs[win[1]], rl, dfn,
                                           (win[2], amount) if win[2] else None, win[3]["vit"])
        rows.append(row)
    return rows


def _row_weight_charge(t, weapon_id, aff, level, two, need, rl, dfn, grease, end_row):
    """`weight_charge` for one sweep configuration at one RL, `dmg_at` being the sweep's walk
    with the Endurance floor set to each grid value."""
    if not _SWEEP.get("weight_charge", True):
        return None
    arch = candidate_archetype(t, weapon_id, aff, level, two, need)
    base = dict(_SWEEP["floors"][rl][arch])
    pool = _SWEEP["other_load"][rl][arch]
    weight = t.weapons[weapon_id + AR.AFFINITIES.index(aff) * 100]["weight"]

    def dmg_at(e):
        snap = _snapshots(t, weapon_id, aff, level, two, dict(base, vit=e), need, [rl], dfn, "damage", grease)
        return snap[rl][1] if rl in snap else None

    wc = weight_charge(weight, pool, base["vit"], dmg_at, end_row)
    if wc:
        wc["archetype"] = arch
    return wc


def grease_sweep(tables, rows_by_rl, rls, grips, jobs, amount, defender, out_path, min_peers,
                 source: str = "pvp", roll: str = "medium", charge: bool = True, every_class: bool = False):
    import multiprocessing as mp
    fl, load = sweep_corpus_stats(rows_by_rl, rls, min_peers, source)
    classes = None
    if every_class:
        model = RES.Model()
        classes = [(cls, model.class_base(cls)[1]) for cls in RES.CLASS_ROWS]
    _SWEEP.update(tables=tables, rls=rls, defender=defender, amount=amount, floors=fl, other_load=load,
                  roll=roll, real=_real_weapon_ids(), weight_charge=charge, classes=classes)
    weapon_ids = sorted(i for i in tables.weapons if i % 10000 == 0 and tables.names.get(i))
    work = [(w, two) for w in weapon_ids for two in grips]
    with mp.get_context("fork").Pool(jobs) as pool:
        results = [r for part in pool.imap_unordered(_grease_one, work) for r in part]
    results.sort(key=lambda r: (r["weapon"], r["two"], r["rl"]))
    # Written beside the target and renamed over it, so a reader never sees half a file.
    tmp = out_path.with_name(out_path.name + ".partial")
    tmp.write_text("\n".join(json.dumps(r) for r in results) + "\n")
    os.replace(tmp, out_path)
    return results


# --------------------------------------------------------------------------------------------
# gear

def gear(weapon: str, affinity: str, model_path: Path, n: int) -> dict:
    if not model_path.exists():
        return {}
    m = EMBED.load_model(model_path)
    vocab = {t: i for i, t in enumerate(m["vocab"])}
    idx = [vocab[t] for t in (f"w:{weapon}", f"wa:{weapon}|{affinity}") if t in vocab]
    if not idx:
        return {}
    x = np.zeros(len(vocab), dtype=np.float32)
    x[idx] = 1
    s = x @ m["B"]
    s[idx] = -np.inf
    out = {}
    for kind in ("t", "a", "aow", "ct", "gr"):
        out[EMBED.KINDS[kind]] = [m["vocab"][j].split(":", 1)[1] for j in np.argsort(-s)
                                  if m["vocab"][j].startswith(kind + ":") and s[j] > 0][:n]
    return out


# --------------------------------------------------------------------------------------------
# self test

def selftest() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok   " if cond else "FAIL ") + msg)
        ok = ok and bool(cond)

    # end_for_load against the curve.
    check(all(end_for_load(w) <= end_for_load(w + 5) for w in range(0, 120, 5)),
          "end_for_load never falls as the load rises")
    check(end_for_load(10_000.0) == STAT_CAP + 1, "a load no Endurance carries at medium roll returns STAT_CAP + 1")
    check(end_for_load(60.0, rate=1.15) <= end_for_load(60.0), "a max-load multiplier never raises the Endurance needed")

    # weight_charge on synthetic kits: linear damage in Endurance above a floor of 40.
    def lin(e):
        return 1000.0 - 5.0 * (e - 40)

    light = [{"rest": 20.0, "rate": 1.0, "end_bonus": 0, "end": 45}] * 4
    wc = weight_charge(1.0, light, 40, lin)
    check(wc and wc["over"] == 0 and abs(wc["factor"] - 1.0) < 1e-9 and wc["fit"] == 1.0,
          "a weapon every kit carries at the floor costs nothing and fits every build")
    mixed = [{"rest": r, "rate": 1.0, "end_bonus": 0, "end": 45} for r in (20.0, 40.0, 55.0, 70.0)]
    lo, hi = weight_charge(10.0, mixed, 40, lin), weight_charge(25.0, mixed, 40, lin)
    check(hi["factor"] < lo["factor"] <= 1.0 and hi["end_mean"] > lo["end_mean"] and hi["fit"] <= lo["fit"],
          f"a heavier weapon costs more (factor {lo['factor']:.3f} -> {hi['factor']:.3f}, fit {lo['fit']} -> {hi['fit']})")
    want = statistics.fmean(lin(max(40, end_for_load(25.0 + p["rest"]))) for p in mixed)
    check(abs(hi["dmg_expected"] - want) < 1e-6, "linear damage interpolates exactly between grid points")
    er = round(hi["end_mean"])
    check(abs(weight_charge(25.0, mixed, 40, lin, er)["factor"] - hi["dmg_expected"] / lin(er)) < 1e-9,
          "end_row makes the factor relative to the damage the row was built at")
    capped = weight_charge(25.0, mixed, 40, lambda e: lin(e) if e <= 45 else None)
    check(capped and capped["dmg_expected"] >= hi["dmg_expected"],
          "an Endurance with no spread takes the highest feasible grid point, not zero")
    check(weight_charge(5.0, [], 40, lin) is None and weight_charge(5.0, light, 40, lambda e: None) is None,
          "no pool or no spread at the floor gives no charge")
    check(weight_charge(5.0, [{"rest": 20.0, "rate": 1.0, "end_bonus": 0}], 40, lin)["fit"] is None,
          "fit is None when no pool build carries its own Endurance")

    # sweep_kind on the regulation.
    tables = AR.Tables(None)
    real = _real_weapon_ids()
    kinds = {n: sweep_kind(tables, tables.find_weapon(n), real) for n in
             ("Giant-Crusher", "Greatsword", "Misericorde", "Bloodhound's Fang", "Icon Shield", "Kite Shield",
              "Longbow", "Finger Seal", "Arrow")}
    check(kinds == {"Giant-Crusher": "greasable", "Greatsword": "greasable", "Misericorde": "greasable",
                    "Bloodhound's Fang": "unique", "Icon Shield": "unique", "Kite Shield": "ungreasable",
                    "Longbow": None, "Finger Seal": None, "Arrow": None}, f"sweep_kind {kinds}")

    # The corpus: Giant-Crusher weighs more than the Greatsword, so it fits fewer STR PvP kits.
    mirror = CACHE / "builds.jsonl"
    if not mirror.exists():
        print("skip corpus checks: no mirror at", mirror)
    else:
        rows = corpus_rows(mirror, 140, 160)
        pool = load_pool(rows, "pvp", "str", 15)
        fl = floors(rows, "", 15, "pvp", "str")[0]
        w = {n: tables.weapons[tables.find_weapon(n)]["weight"] for n in ("Giant-Crusher", "Greatsword")}
        fit = {n: weight_charge(w[n], pool, fl["vit"], lin)["fit"] for n in w}
        check(w["Giant-Crusher"] > w["Greatsword"] and fit["Giant-Crusher"] < fit["Greatsword"],
              f"STR PvP RL 140-160 ({len(pool)} kits, END floor {fl['vit']}): fit at own END "
              f"Giant-Crusher {fit['Giant-Crusher']:.2f} < Greatsword {fit['Greatsword']:.2f}")
        # One fixed-affinity weapon through the sweep job, one RL.
        dfn = bracket_defender(rows)
        f2, l2 = sweep_corpus_stats({150: rows}, [150], 15, "pvp")
        _SWEEP.update(tables=tables, rls=[150], defender=dfn, amount=GREASES["dlc-drawstring"], floors=f2,
                      other_load=l2, roll="medium", real=real, weight_charge=True)
        out = _grease_one((tables.find_weapon("Bloodhound's Fang"), True))
        r = out[0] if out else {}
        check(r.get("kind") == "unique" and r.get("elemental") and r["elemental"]["aff"] == "Standard"
              and r.get("quality") is None,
              f"Bloodhound's Fang 2H gets a Standard build ({(r.get('elemental') or {}).get('dmg', 0):.0f} dmg)")
        check(bool(r.get("greased")) == greasable(tables, "Bloodhound's Fang", "Standard"),
              "a unique weapon is greased exactly when its row has isEnhance")
        wc = r.get("weight") or {}
        check(wc.get("factor") is not None and 0.5 < wc["factor"] <= 1.0 + 1e-6,
              f"its weight charge is a factor in (0.5, 1] ({wc.get('factor')})")
    print("selftest", "passed" if ok else "FAILED")
    return 0 if ok else 1


# --------------------------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("weapon", nargs="?")
    ap.add_argument("--weapons-for", metavar="STATS", help="str=42,dex=42,int=8,fth=25,arc=6: rank weapons instead")
    ap.add_argument("--quality-sweep", metavar="LO-HI", help="150-200: does Quality win on builds made for other affinities")
    ap.add_argument("--grease-sweep", metavar="LO-HI", help="150-200: buffable affinity + grease vs best ungreasable affinity")
    ap.add_argument("--grease", choices=list(GREASES) + ["none"], default="dlc-drawstring",
                    help="grease a STR/DEX build is scored with on a greasable affinity (default: DLC drawstring, +135)")
    ap.add_argument("--every-class", action="store_true",
                    help=f"grease sweep: start each build from every class and keep the best, not only {SWEEP_CLASS}")
    ap.add_argument("--rl-step", type=int, default=5)
    ap.add_argument("--grip", choices=["1h", "2h", "both"], default="both")
    ap.add_argument("--jobs", type=int, default=16)
    ap.add_argument("--rl", type=int, default=150)
    ap.add_argument("--two-handed", action="store_true")
    ap.add_argument("--affinity", action="append", help="limit to these affinities (repeatable)")
    ap.add_argument("--objective", choices=["damage", "ar"], default="damage")
    ap.add_argument("--floor", action="append", default=[], help="vig=60 / mnd=15 / end=30 (repeatable)")
    ap.add_argument("--window", type=int, default=10, help="corpus RL window is rl +- this")
    ap.add_argument("--min-peers", type=int, default=15)
    ap.add_argument("--floors", choices=FLOOR_SOURCES, default="pvp",
                    help="survivability floors from PvP builds (of the weapon's stat archetype in sweeps) "
                         "or from every build of the window")
    ap.add_argument("--roll", choices=["medium", "any"], default="medium",
                    help="medium: raise END until the weapon plus the pool's typical rest-of-kit load "
                         "is at medium roll")
    ap.add_argument("--out", type=Path, help="sweep output path (default under the cache)")
    ap.add_argument("--mirror", type=Path, default=CACHE / "builds.jsonl")
    ap.add_argument("--model", type=Path, default=CACHE / "embeddings.npz")
    ap.add_argument("--top", type=int, default=3, help="alternatives shown")
    ap.add_argument("--top-weapons", type=int, default=20, help="rows shown by --weapons-for")
    ap.add_argument("-n", type=int, default=4, help="gear suggestions per kind")
    ap.add_argument("--no-weight-charge", action="store_true",
                    help="grease sweep: skip each row's weight charge (`weight_charge`), about 40%% of the run time")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    if not a.weapon and not a.weapons_for and not a.quality_sweep and not a.grease_sweep:
        ap.error("name a weapon, or pass --weapons-for or --quality-sweep")
    tables = AR.Tables(None)

    if a.grease_sweep:
        lo, hi = (int(x) for x in a.grease_sweep.split("-"))
        rls = sorted(set(list(range(lo, hi + 1, a.rl_step)) + [hi]))
        rows_by_rl = {rl: corpus_rows(a.mirror, rl - a.window, rl + a.window) for rl in rls}
        dfn = bracket_defender(corpus_rows(a.mirror, lo - a.window, hi + a.window))
        grips = {"1h": [False], "2h": [True], "both": [False, True]}[a.grip]
        amount = GREASES[a.grease]
        out = a.out or CACHE / f"grease-sweep-{a.grease}-{lo}-{hi}{'-every-class' if a.every_class else ''}.jsonl"
        res = grease_sweep(tables, rows_by_rl, rls, grips, a.jobs, amount, dfn, out, a.min_peers,
                           a.floors, a.roll, not a.no_weight_charge, a.every_class)
        from collections import Counter
        kinds = Counter(r.get("kind", "greasable") for r in res)
        print("rows by kind: " + ", ".join(f"{k} {v} ({len({r['weapon'] for r in res if r.get('kind') == k})} weapons)"
                                           for k, v in kinds.most_common()))
        wcs = [r["weight"] for r in res if r.get("weight")]
        if wcs:
            fs = sorted(w["factor"] for w in wcs)
            print(f"  weight charge factor over {len(fs)} rows: min {fs[0]:.3f}, median {fs[len(fs) // 2]:.3f}, "
                  f"max {fs[-1]:.3f}")
        res = [r for r in res if r.get("kind", "greasable") == "greasable"]
        n = len(res)
        win = [r for r in res if r["greased"]["dmg"] > r["elemental"]["dmg"]]
        qwin = [r for r in res if r["quality"] and r["quality"]["dmg"] > r["elemental"]["dmg"]]
        print(f"grease sweep RL {lo}-{hi} step {a.rl_step}, grips {a.grip}, grease {a.grease} (+{amount}), "
              f"one MV-100 hit on the median RL {lo - a.window}-{hi + a.window} defender "
              f"({', '.join(f'{el} {dfn['defense'][el]:.0f}' for el in ELEMENTS)}); rows in {out}")
        print(f"  greased buffable affinity beats the best ungreasable affinity: {len(win)}/{n} "
              f"({len(win) / n:.0%}), {len({r['weapon'] for r in win})}/{len({r['weapon'] for r in res})} weapons")
        print(f"  greased Quality alone beats it: {len(qwin)}/{n} ({len(qwin) / n:.0%})")
        print("  winning greased affinity: " + ", ".join(f"{k} {v}" for k, v in
              Counter(r["greased"]["aff"] for r in win).most_common()))
        print("  winning grease element: " + ", ".join(f"{k} {v}" for k, v in
              Counter(r["greased"]["grease"] for r in win).most_common()))
        print("  ungreasable affinity it beat: " + ", ".join(f"{k} {v}" for k, v in
              Counter(r["elemental"]["aff"] for r in win).most_common()))
        lose = sorted((r for r in res if r not in win), key=lambda r: r["elemental"]["dmg"] - r["greased"]["dmg"])
        if lose:
            print("  largest losses of greased to ungreasable:")
            for r in lose[-5:][::-1]:
                print(f"    {r['weapon'][:30]:<30} {'2H' if r['two'] else '1H'} RL {r['rl']}: {r['elemental']['aff']} "
                      f"{r['elemental']['dmg']:.0f} vs {r['greased']['aff']}+{r['greased']['grease']} {r['greased']['dmg']:.0f}")
        return 0

    if a.quality_sweep:
        lo, hi = (int(x) for x in a.quality_sweep.split("-"))
        rls = list(range(lo, hi + 1, a.rl_step))
        if rls[-1] != hi:
            rls.append(hi)
        rows_by_rl = {rl: corpus_rows(a.mirror, rl - a.window, rl + a.window) for rl in rls}
        grips = {"1h": [False], "2h": [True], "both": [False, True]}[a.grip]
        out = a.out or CACHE / f"quality-sweep-{lo}-{hi}.jsonl"
        res = quality_sweep(tables, rows_by_rl, rls, grips, a.jobs, out, a.min_peers, a.floors, a.roll)
        wins = [r for r in res if r["best_on_build"] == QUALITY and r["quality_ar"] > r["built_ar"] + 1e-6]
        print(f"quality sweep RL {lo}-{hi} step {a.rl_step}, grips {a.grip}, class {SWEEP_CLASS}: "
              f"{len(res)} (weapon, grip, RL) builds, {len({r['weapon'] for r in res})} weapons; all rows in {out}")
        print(f"Quality beats every affinity on a build made for the best non-Quality affinity: {len(wins)} cases, "
              f"{len({r['weapon'] for r in wins})} weapons")
        for r in sorted(wins, key=lambda r: -(r["quality_ar"] - r["built_ar"]))[:a.top_weapons]:
            s = r["stats"]
            print(f"  {r['weapon'][:32]:<32} {'2H' if r['two'] else '1H'} RL {r['rl']}: built for {r['built_for']} "
                  f"{r['built_ar']:.0f}, Quality {r['quality_ar']:.0f} (+{r['quality_ar'] - r['built_ar']:.0f})  "
                  f"STR {s['str']} DEX {s['dex']}")
        return 0
    rows = corpus_rows(a.mirror, a.rl - a.window, a.rl + a.window)

    if a.weapons_for:
        stats = {k: 10 for k in DAMAGE_STATS}
        stats.update({k: int(v) for k, v in (kv.split("=") for kv in a.weapons_for.split(",") if kv)})
        dfn = bracket_defender(rows)
        tier = None if a.grease == "none" else a.grease
        ranked = weapons_for(tables, stats, a.objective, dfn, CACHE / "catalog.json", tier)
        print(f"weapons for {', '.join(f'{k.upper()} {stats[k]}' for k in DAMAGE_STATS)}, objective {a.objective}"
              + (f" (one hit on the median RL {a.rl - a.window}-{a.rl + a.window} defender)" if a.objective == "damage" else "")
              + (f", greasable affinities scored with {a.grease} grease" if tier and physical_build(tables, stats, dfn)[0] else ""))
        ph, pv, nv = physical_build(tables, stats, dfn)
        print(f"physical build: {'yes' if ph else 'no'} ({pv} of {nv} wieldable infusable weapons do best on "
              f"Standard/Heavy/Keen/Quality)")
        print(f"{'score':>7}  {'AR':>5}  {'weapon':<34} {'affinity':<10} grip  grease / status")
        for r in ranked[:a.top_weapons]:
            st = ", ".join(f"{k} {v['total']:.0f}" for k, v in r["ar"].get("status", {}).items())
            gname = GREASE_NAMES[tier][r["grease"][0]] if r["grease"] else ""
            print(f"{r['score']:7.1f}  {r['total_ar']:5.0f}  {r['name'][:34]:<34} {r['affinity']:<10} "
                  f"{'2H' if r['two'] else '1H'}    {', '.join(x for x in (gname, st) if x)}")
        print(f"({len(ranked)} weapons usable at these stats)")
        return 0

    model = RES.Model()
    base_id = tables.find_weapon(a.weapon)
    # The archetype of the first affinity asked for (Standard by default) picks the floor pool.
    aff0 = (a.affinity or ["Standard"])[0]
    lvl0 = tables.max_level(tables.weapons[tables.find_weapon(a.weapon, aff0)]["reinforceTypeId"])
    arch = candidate_archetype(tables, a.weapon, aff0, lvl0, a.two_handed,
                               requirements(tables, a.weapon, aff0, lvl0, a.two_handed))
    fl, n_peers, scope = floors(rows, a.weapon, a.min_peers, a.floors, arch)
    if a.roll == "medium":
        need_end = medium_roll_end(tables.weapons[base_id]["weight"], load_pool(rows, a.floors, arch, a.min_peers))
        if need_end > fl["vit"]:
            scope += f"; END {fl['vit']} -> {need_end} for medium roll"
            fl["vit"] = need_end
    for kv in a.floor:
        k, v = kv.split("=")
        fl[{"end": "vit", "endurance": "vit", "vigor": "vig", "mind": "mnd"}.get(k, k)] = int(v)
    dfn = bracket_defender(rows)
    tier = None if a.grease == "none" else a.grease
    res = optimize(tables, model, a.weapon, a.rl, a.two_handed, a.objective, fl, dfn,
                   a.affinity or affinities(tables, base_id), tier)
    if not res:
        print(f"no class reaches RL {a.rl} with {a.weapon}'s requirements and floors {fl}")
        return 1

    best = res[0]
    grip = "two-handed" if a.two_handed else "one-handed"
    print(f"{a.weapon}, RL {a.rl}, {grip}, objective {a.objective}")
    print(f"floors from {scope}: " + ", ".join(f"{k.upper() if k != 'vit' else 'END'} {v}" for k, v in fl.items()))
    print(f"defender: median of {dfn['n']} builds at RL {a.rl - a.window}-{a.rl + a.window}: defense " +
          ", ".join(f"{el} {dfn['defense'][el]:.0f}" for el in ELEMENTS))
    print()
    st = best["stats"]
    gtxt = f" with {GREASE_NAMES[tier][best['grease'][0]]} (+{best['grease'][1]} {best['grease'][0]})" \
        if best.get("grease") else ""
    print(f"best: {best['affinity']} +{best['level']}{gtxt}, start as {best['class']}")
    print("  " + "  ".join(f"{('END' if k == 'vit' else k.upper())} {st[k]}" for k in STATS) +
          f"   (RL {sum(st.values()) - LEVEL_OFFSET})")
    ar = best["ar"]
    by = best["by_element"]
    print("  AR " + ", ".join(f"{el} {v:.0f}" for el, v in by.items() if v) + f"  = {sum(by.values()):.0f}"
          + (" (grease included)" if best.get("grease") else ""))
    if ar.get("status"):
        print("  status " + ", ".join(f"{k} {v['total']:.0f}" for k, v in ar["status"].items()))
    if ar.get("spell_buff"):
        print("  spell buff " + ", ".join(f"{k} {v:.0f}" for k, v in ar["spell_buff"].items()))
    if a.objective == "damage":
        print(f"  one motion-value-100 hit on the median defender: {best['score']:.0f}")
    r = model.resources(dict(st))
    print(f"  HP {r['max_hp']}, FP {r['max_fp']}, stamina {r['max_stamina']}, "
          f"equip load {r['max_equip_load']:.1f}")
    if len(res) > 1:
        print("\nnext best:")
        seen = {(best["class"], best["affinity"])}
        shown = 0
        for r2 in res[1:]:
            if shown >= a.top or (r2["affinity"], tuple(r2["stats"].values())) == (best["affinity"], tuple(st.values())):
                continue
            s2 = r2["stats"]
            g2 = f"+{r2['grease'][0]}" if r2.get("grease") else ""
            print(f"  {r2['score']:7.1f}  {(r2['affinity'] + g2):<20} {r2['class']:<12} " +
                  " ".join(f"{('END' if k == 'vit' else k.upper())} {s2[k]}" for k in STATS))
            shown += 1
    g = gear(a.weapon, best["affinity"], a.model, a.n)
    if g:
        print(f"\ngear builds with {a.weapon} use most (EASE, {n_peers} such builds in the window):")
        for kind, items in g.items():
            if items:
                print(f"  {kind}: " + ", ".join(items))
    return 0


if __name__ == "__main__":
    sys.exit(main())
