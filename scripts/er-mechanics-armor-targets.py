#!/usr/bin/env python3
"""What poise and which absorption to aim for, from what the RL window's PvP builds hit with.

    python3 scripts/er-mechanics-armor-targets.py --stats vig=56,mnd=8,vit=41,str=80,dex=20,int=7,fth=8,arc=9 \\
        --talismans "Two Fingers Heirloom,Great-Jar's Arsenal,Bull-Goat's Talisman,Two-Handed Sword Talisman" \\
        --budget 46.4 --budget 52.0 --weapons "Executioner's Greataxe|both,Halberd|one"

Three measurements over the PvP builds of the RL window (`er-mechanics-exchange` corpus filter):

1. Incoming damage mix. Each build's right primary weapon at its own stats, affinity and level
   (`er-mechanics-ar.attack_rating`), through the pool's four attack families (R1 #1, R2 #1,
   movement attacks, jump attacks; equal shares, `er-mechanics-interrupt.POOL_FAMILIES`), each
   element's attack times its motion value, then the defense curve against this build's own flat
   defense (`er-mechanics-defense`). Physical damage is filed under the hit's physical type
   (`er-mechanics-attacks.resolve_phys_type`). The result is the damage that reaches armor
   absorption, per type, summed over the window: how much each absorption column is worth.
   Spells are not in it: the planner's spell slots are nearly empty in the scrape (buffs.md
   section 7), so catalyst primaries are counted and reported, not modelled.
2. Poise. The pool's attack sequences with PvP poise damage (`saRate` applied, interrupt.md
   section 1), weighted by build adoption and family. For each poise value: the share of pool
   attacks whose first hit breaks it, and whose whole string does. With `--weapons`, the share of
   pool attacks that break the named weapon's openers during their windup at that poise
   (`er-mechanics-interrupt.reverse`, hyperarmor windows included).
3. Armor frontier. For each weight budget and poise floor, the four pieces (an empty slot
   allowed) that minimise the damage kept, `sum_t share_t * prod_slots cut_t`, found by a dynamic
   program over (weight, poise) on the log of the cut rates and re-scored exactly.

Poise here is the menu number with the talismans' `toughnessDamageCutRate` applied (Bull-Goat's
divides by 0.75), which is the number the pool's poise damage is compared against.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
CACHE = Path.home() / ".cache/er-build-planner"


def _sibling(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


DEF = _sibling("er-mechanics-defense")
AR = _sibling("er-mechanics-ar")
INT = _sibling("er-mechanics-interrupt")
ATK = INT.ATK
EXCH = INT.EXCH
EMBED = _sibling("er-builds-embed")

#: Absorption columns, in the order reported. `standard` is the armor's neutral column.
TYPES = ("slash", "strike", "pierce", "standard", "magic", "fire", "lightning", "holy")
CUT_FIELD = {"slash": "slashDamageCutRate", "strike": "blowDamageCutRate", "pierce": "thrustDamageCutRate",
             "standard": "neutralDamageCutRate", "magic": "magicDamageCutRate", "fire": "fireDamageCutRate",
             "lightning": "thunderDamageCutRate", "holy": "darkDamageCutRate"}
ELEMENTS = ("physical", "magic", "fire", "lightning", "holy")
MV_KEY = {"physical": "mv_phys", "magic": "mv_mag", "fire": "mv_fire", "lightning": "mv_light", "holy": "mv_holy"}
#: The pool's families and the slots that stand for each (judge ids of `ATK.SLOTS_ONE_HAND`); a
#: two-handed build adds `ATK.TWO_HAND_JUDGE_OFFSET`.
FAMILY_JUDGES = {"r1": [0], "r2": [100], "move": [120, 125, 130, 140], "jump": [150, 160]}
#: Bow, crossbow, ballista and catalyst classes: no melee moveset to file.
RANGED_TYPES = {50, 51, 53, 55, 56, 57, 61}
REGULAR_MAX = 25

STAT_ALIAS = {"vit": "end"}


def parse_stats(s: str) -> dict:
    out = {}
    for part in s.split(","):
        k, v = part.split("=")
        out[k.strip().lower()] = int(v)
    return out


# --------------------------------------------------------------------------------------------
# 1. incoming damage mix

def corpus_builds(rl_lo: int, rl_hi: int, mirror: Path = CACHE / "builds.jsonl") -> list[dict]:
    """The window's PvP builds, deduplicated the way `er-mechanics-exchange._corpus_builds` does."""
    out, seen = [], set()
    for line in mirror.open():
        row = json.loads(line)
        b = row["build"]
        st = EMBED.stats_of(b)
        if st is None or not rl_lo <= st["rl"] <= rl_hi or not EXCH.is_pvp(b):
            continue
        if sum(st[k] for k in EMBED.ATTRS) - EMBED.LEVEL_OFFSET != st["rl"]:
            continue
        key = (row.get("user"), tuple(EMBED.tokens(b)))
        if key in seen:
            continue
        seen.add(key)
        out.append(b)
    return out


def right_primary(b: dict) -> dict | None:
    a = EMBED.active_set(b, "weapons")
    for s in (b.get("inventory") or {}).get("slots") or []:
        if EMBED.slot_pos(s, a) == EMBED.RIGHT_PRIMARY and s.get("name"):
            return s
    return None


def damage_mix(builds: list[dict], my_defense: dict, ar_tables, reg) -> dict:
    """Damage reaching absorption per type, summed over the builds (module docstring, item 1)."""
    total = defaultdict(float)
    per_build = []
    why = Counter()
    memo = {}
    for b in builds:
        s = right_primary(b)
        if s is None:
            why["no right primary"] += 1
            continue
        name, aff = s["name"], s.get("infusion") or "Standard"
        try:
            wid = ar_tables.find_weapon(name, aff)
        except (SystemExit, KeyError, ValueError):
            why["weapon not in regulation"] += 1
            continue
        wep = ar_tables.weapons[wid]
        if wep["wepType"] in RANGED_TYPES:
            why["ranged or catalyst primary"] += 1
            continue
        maxlvl = ar_tables.max_level(wep["reinforceTypeId"])
        up = s.get("upgrade")
        if up is None:
            up = b.get("weaponUpgrade") or REGULAR_MAX
        lvl = min(maxlvl, int(round(up * maxlvl / REGULAR_MAX)) if maxlvl != REGULAR_MAX else int(up))
        st = EMBED.stats_of(b)
        two = bool(b.get("is2h"))
        try:
            ar = AR.attack_rating(ar_tables, wid, "Standard", lvl, {k: st[k] for k in ("str", "dex", "int", "fth", "arc")},
                                  two)
        except (SystemExit, KeyError, ValueError):
            why["attack rating failed"] += 1
            continue
        by_el = {el: ar["damage"].get(el, {}).get("total", 0.0) for el in ELEMENTS}
        off = ATK.TWO_HAND_JUDGE_OFFSET if two else 0
        fams = {}
        for fam, judges in FAMILY_JUDGES.items():
            slots = []
            for j in judges:
                k = (wid, j + off)
                if k not in memo:
                    memo[k] = ATK.attack_numbers(reg, wid, j + off)
                if memo[k] is not None and memo[k]["can_hit_enemy"]:
                    slots.append(memo[k])
            if slots:
                fams[fam] = slots
        if not fams:
            why["no melee slot"] += 1
            continue
        got = defaultdict(float)
        for fam, slots in fams.items():
            for a in slots:
                wgt = 1.0 / len(fams) / len(slots)
                for el in ELEMENTS:
                    atk = by_el[el] * a[MV_KEY[el]] / 100.0
                    d = DEF.defense_curve(atk, my_defense[el]) if atk > 0 else 0.0
                    key = a["phys_type"] if el == "physical" else el
                    if key == "none":
                        continue
                    got[key] += wgt * d
        for k, v in got.items():
            total[k] += v
        per_build.append({"weapon": name, "aff": aff, "two": two, "hit": sum(got.values()), "by": dict(got)})
    tot = sum(total.values()) or 1.0
    return {"share": {t: total.get(t, 0.0) / tot for t in TYPES}, "builds": len(per_build), "dropped": dict(why),
            "mean_hit": tot / max(1, len(per_build)), "per_build": per_build}


# --------------------------------------------------------------------------------------------
# 2. poise

def pool_rows(rl_lo: int, rl_hi: int):
    reg = ATK.Regulation(None)
    pool_raw = EXCH.opponent_pool(reg, CACHE / "builds.jsonl", rl_lo, rl_hi)
    weapons = INT.load_cache()
    delays = INT.Delays(weapons)
    entries = INT.entry_table()
    rows = INT.PoolRows(pool_raw, weapons, delays, entries, True)
    return rows, weapons, delays, entries, pool_raw


def poise_curve(rows, grid) -> dict:
    """Pool-weighted share of attacks whose first hit, or whole string, reaches each poise."""
    d1 = np.array([s["hits"][0][1] for s in rows.seqs])
    tot = np.array([sum(p for _, p in s["hits"]) for s in rows.seqs])
    w = rows.seq_w
    first = [float(w[d1 >= P].sum()) for P in grid]
    string = [float(w[tot >= P].sum()) for P in grid]
    fam = defaultdict(lambda: np.zeros(len(grid)))
    for i, s in enumerate(rows.seqs):
        fam[s["family"]] += w[i] * (d1[i] >= np.asarray(grid))
    return {"grid": list(grid), "first": first, "string": string,
            "family_first": {k: v.tolist() for k, v in fam.items()}}


def weighted_quantile(x, w, q):
    o = np.argsort(x)
    c = np.cumsum(w[o]) / w.sum()
    return float(x[o][np.searchsorted(c, q)])


def stopped_curve(rows, weapons, delays, entries, specs, grid, openers=("r1", "r2", "r2c", "run_r2", "jump_r2")):
    """Per weapon-grip and opener: pool share that breaks it during its windup, per poise."""
    out = {}
    keep = rows.my_poise
    try:
        for spec in specs:
            name, grip = spec.split("|")
            w = weapons.get(f"{name}|{grip}")
            if not w or "members" not in w:
                out[spec] = None
                continue
            seqs = INT.resolve(w, grip, name, delays, entries, True)
            res = {}
            for o in openers:
                if o not in seqs:
                    continue
                vals = []
                for P in grid:
                    rows.my_poise = np.array([float(P)])
                    vals.append(INT.reverse(rows, seqs[o], False))
                res[o] = {"strike": seqs[o]["strike"], "stopped": vals}
            out[spec] = res
    finally:
        rows.my_poise = keep
    return out


# --------------------------------------------------------------------------------------------
# 3. armor frontier

def armor_candidates(t) -> dict:
    """Per slot (0 head, 1 body, 2 arms, 3 legs): named pieces, one row per name, plus none."""
    out = {c: [{"name": "-", "weight": 0.0, "poise": 0.0, "cut": {k: 1.0 for k in TYPES}}] for c in range(4)}
    names = DEF.PR.row_names("EquipParamProtector")
    for rid in t.prot_by_name.values():
        r = t.protector[rid]
        c = r["protectorCategory"]
        out[c].append({"id": rid, "name": names[rid], "weight": r["weight"], "poise": r["toughnessCorrectRate"] * 1000.0,
                       "cut": {k: r[CUT_FIELD[k]] for k in TYPES}})
    return out


class Frontier:
    """Min of `sum_t share_t * log(cut_t)` over pieces, per (weight bin, poise unit)."""

    def __init__(self, cands: dict, share: dict, wmax: float, step: float = 0.1):
        self.cands, self.share, self.step = cands, share, step
        self.W = int(round(wmax / step)) + 1
        self.pmax = int(sum(max(p["poise"] for p in cands[c]) for c in range(4))) + 1
        sh = np.array([share[k] for k in TYPES])
        inf = np.inf
        best = np.full((self.W, self.pmax + 1), inf)
        best[0, 0] = 0.0
        self.choice = []
        for c in range(4):
            nb = np.full_like(best, inf)
            ch = np.full(best.shape, -1, np.int32)
            for i, p in enumerate(cands[c]):
                iw = int(math.ceil(p["weight"] / step - 1e-9))
                ip = int(round(p["poise"]))
                if iw >= self.W:
                    continue
                sc = float(sh @ np.log(np.array([p["cut"][k] for k in TYPES])))
                src = best[: self.W - iw, : self.pmax + 1 - ip] + sc
                dst = nb[iw:, ip:]
                better = src < dst
                dst[better] = src[better]
                ch[iw:, ip:][better] = i
            self.choice.append(ch)
            best = nb
        self.best = best

    def pick(self, budget: float, poise_min: float):
        wb = min(self.W - 1, int(math.floor(budget / self.step + 1e-9)))
        pb = max(0, int(math.ceil(poise_min - 1e-9)))
        if pb > self.pmax:
            return None
        sub = self.best[: wb + 1, pb:]
        if not np.isfinite(sub).any():
            return None
        w, p = np.unravel_index(np.argmin(sub), sub.shape)
        p += pb
        pieces = []
        for c in (3, 2, 1, 0):
            i = int(self.choice[c][w, p])
            piece = self.cands[c][i]
            pieces.append(piece)
            w -= int(math.ceil(piece["weight"] / self.step - 1e-9))
            p -= int(round(piece["poise"]))
        pieces.reverse()
        return pieces


def kept(pieces, share) -> float:
    return sum(share[k] * math.prod(p["cut"][k] for p in pieces) for k in TYPES)


# --------------------------------------------------------------------------------------------
# 4. defensive sources that need no stats

PHYS_OF = {"slash": "slash", "strike": "strike", "pierce": "pierce", "standard": "standard"}


def adoption(builds: list[dict]) -> Counter:
    """Builds carrying each tool, tear, great rune or equipped talisman."""
    c = Counter()
    for b in builds:
        items = b.get("items") or {}
        have = {("tool", s["name"]) for s in (items.get("tools") or {}).get("slots") or [] if s.get("name")}
        have |= {("tear", x) for x in items.get("crystalTears") or []}
        if b.get("greatRune"):
            have.add(("great rune", b["greatRune"]))
        a = EMBED.active_set(b, "talismans")
        have |= {("talisman", s["name"]) for s in (b.get("talismans") or {}).get("slots") or []
                 if s.get("name") and EMBED.slot_pos(s, a) is not None}
        for h in have:
            c[h] += 1
    return c


def buff_table(t, share: dict, builds: list[dict]) -> list[dict]:
    """Consumables, physick tears, great runes and talismans that lower PvP damage taken or raise
    poise: the damage kept on the window's mix while active, and how many window builds carry it."""
    BUFFS = _sibling("er-mechanics-buffs")
    m = BUFFS.Buffs()
    used = adoption(builds)
    n = len(builds)
    out = []
    seen = set()
    for s in m.sources:
        if s["kind"] not in ("consumable", "tear", "great rune") or s["name"] in seen:
            continue
        seen.add(s["name"])
        k, pdiv, per = 0.0, 1.0, {}
        for ty in TYPES:
            pt = PHYS_OF.get(ty, "standard")
            d = m.defense_context([s["name"]], pvp=True, phys_type=pt)
            el = "physical" if ty in PHYS_OF else ty
            per[ty] = d["cut"][el] * d["correct"][el]
            k += share[ty] * per[ty]
            pdiv = d["poise_div"]
        worst = max(per, key=per.get)
        dur = max((m.sp[i]["effectEndurance"] for i, _ in s["entries"] if i in m.sp), default=0.0)
        tag = "tool" if s["kind"] == "consumable" else s["kind"]
        if k < 0.999 or pdiv < 0.999:
            one = any(m.one_hit(i) for i, _ in s["entries"] if i in m.sp)
            out.append({"name": s["name"], "kind": s["kind"] + (" (one hit)" if one else ""), "kept": k,
                        "poise_x": 1 / pdiv, "seconds": dur, "worst": (worst, per[worst]),
                        "builds": used[(tag, s["name"])], "share": used[(tag, s["name"])] / max(1, n)})
    names = DEF.PR.row_names("EquipParamAccessory")
    for rid, r in t.accessory.items():
        name = names.get(rid)
        if not name:
            continue
        effs = DEF.gather_effects(t, [], [r], all_talisman_effects=True)
        if not effs:
            continue
        k, pdiv, cond = 0.0, 1.0, False
        for sp in effs:
            pdiv *= sp["toughnessDamageCutRate"]
            cond |= not DEF.is_passive(sp)
        per = {}
        for ty in TYPES:
            col = DEF.SP_CUT["physical" if ty == "standard" else ty]
            suf = DEF.CORRECT_SUFFIX["physical" if ty == "standard" else ty]
            per[ty] = math.prod(sp[col] * sp[f"defPlayerDmgCorrectRate_{suf}"] for sp in effs)
            k += share[ty] * per[ty]
        worst = max(per, key=per.get)
        if k < 0.999 or pdiv < 0.999:
            out.append({"name": name, "kind": "talisman" + (" (conditional)" if cond else ""), "kept": k,
                        "worst": (worst, per[worst]),
                        "poise_x": 1 / pdiv, "seconds": None, "builds": used[("talisman", name)],
                        "share": used[("talisman", name)] / max(1, n)})
    return sorted(out, key=lambda x: (x["kept"], -x["poise_x"]))


# --------------------------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stats", required=True, help="planner keys: vig,mnd,vit,str,dex,int,fth,arc")
    ap.add_argument("--talismans", default="")
    ap.add_argument("--great-rune")
    ap.add_argument("--rl", type=int, default=150)
    ap.add_argument("--window", type=int, default=10)
    ap.add_argument("--budget", type=float, action="append", default=[],
                    help="armor weight budget (medium-roll load left for armor); repeatable")
    ap.add_argument("--weapons", default="", help="comma list of Name|one or Name|both for the windup curve")
    ap.add_argument("--poise-floors", default="0,41,54,104,111,122,136,166")
    ap.add_argument("--current", default="", help="comma list of worn armor pieces to score")
    ap.add_argument("--focus", action="append", default=[], choices=TYPES,
                    help="also find the best set for this one absorption type; repeatable")
    ap.add_argument("--focus-poise", type=float, default=111.0, help="poise floor for the --focus sets")
    ap.add_argument("--wet", type=int, action="append", default=[],
                    help="defender SpEffect active for the whole fight, e.g. 4600 (Wet (Rain)); repeatable")
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()

    stats = {STAT_ALIAS.get(k, k): v for k, v in parse_stats(a.stats).items()}
    t = DEF.Tables()
    tals = [x.strip() for x in a.talismans.split(",") if x.strip()]
    me = DEF.defender(t, stats, (), tals, a.great_rune, pvp=True)
    tcut = me["toughness_cut"]
    tal_weight = sum(t.talisman_row(n)["weight"] for n in tals)
    lo, hi = a.rl - a.window, a.rl + a.window
    print(f"my flat defense {me['defense']}; talisman weight {tal_weight:.1f}; poise divisor {tcut:.3f}")

    builds = corpus_builds(lo, hi)
    ar_tables = AR.Tables()
    reg = ATK.Regulation(None)
    mix = damage_mix(builds, me["defense"], ar_tables, reg)
    share = mix["share"]
    print(f"\n1. damage reaching absorption, RL {lo}-{hi} PvP: {mix['builds']} melee primaries of {len(builds)} builds; "
          f"dropped {mix['dropped']}")
    print("   " + "  ".join(f"{k} {100 * share[k]:.1f}%" for k in TYPES))
    if a.wet:
        # A defender state such as SpEffect 4600 "Wet (Rain)" multiplies what arrives per type, so
        # it scales the mix instead of being a choice: everything after this reads relative to dry.
        env = {ty: math.prod(t.speffect[i][DEF.SP_CUT["physical" if ty == "standard" else ty]] for i in a.wet)
               for ty in TYPES}
        share = {ty: share[ty] * env[ty] for ty in TYPES}
        print(f"   under SpEffect {a.wet}: " + "  ".join(f"{k} x{env[k]:.2f}" for k in TYPES if abs(env[k] - 1) > 1e-6)
              + f"; total vs dry x{sum(share.values()):.3f}")
    phys = sum(share[k] for k in ("slash", "strike", "pierce", "standard"))
    print(f"   physical {100 * phys:.1f}%, elemental {100 * (1 - phys):.1f}%; mean pool hit {mix['mean_hit']:.0f} before absorption")
    affs = Counter(p["aff"] for p in mix["per_build"])
    print("   primary affinities: " + ", ".join(f"{k} {v}" for k, v in affs.most_common(10)))

    rows, weapons, delays, entries, pool_raw = pool_rows(lo, hi)
    grid = list(range(0, 251))
    pc = poise_curve(rows, grid)
    print(f"\n2. pool attack poise damage (menu units, PvP): {rows.n_builds} builds, {len(rows.seqs)} sequences")
    d1 = np.array([s["hits"][0][1] for s in rows.seqs])
    print("   first-hit poise damage quantiles: " + ", ".join(
        f"p{q} {weighted_quantile(d1, rows.seq_w, q / 100):.0f}" for q in (10, 25, 50, 75, 90)))
    floors = [float(x) for x in a.poise_floors.split(",")]
    print(f"   {'poise':>6}{'1st hit breaks':>16}{'string breaks':>15}" + "".join(f"{f:>8}" for f in ("r1", "r2", "move", "jump")))
    for P in floors:
        i = int(P)
        fam = pc["family_first"]
        famw = {k: sum(rows.seq_w[j] for j, s in enumerate(rows.seqs) if s["family"] == k) for k in fam}
        print(f"   {P:>6.0f}{100 * pc['first'][i]:>15.1f}%{100 * pc['string'][i]:>14.1f}%"
              + "".join(f"{100 * fam[k][i] / famw[k]:>7.0f}%" for k in ("r1", "r2", "move", "jump")))
    # Breakpoints: poise values where the first-hit share drops by at least 2 points.
    steps = [(grid[i], pc["first"][i - 1] - pc["first"][i]) for i in range(1, len(grid))]
    big = sorted([s for s in steps if s[1] >= 0.01], key=lambda s: s[0])
    print("   breakpoints (first-hit share drop >= 1 pt just past this poise): "
          + ", ".join(f"{p - 1}->{p}: -{100 * d:.1f}" for p, d in big))

    specs = [x.strip() for x in a.weapons.split(",") if x.strip()]
    stopped = {}
    if specs:
        sgrid = list(range(0, 201, 10))
        stopped = stopped_curve(rows, weapons, delays, entries, specs, sgrid)
        print("\n   pool share that breaks my opener during its windup, by poise:")
        print(f"   {'weapon opener':<36}" + "".join(f"{p:>5}" for p in sgrid))
        for spec, res in stopped.items():
            if not res:
                print(f"   {spec}: not in the sequence cache")
                continue
            for o, r in res.items():
                print(f"   {(spec + ' ' + o)[:36]:<36}" + "".join(f"{100 * v:>5.0f}" for v in r["stopped"]))

    frontier = {}
    if a.budget:
        cands = armor_candidates(t)
        fr = Frontier(cands, share, max(a.budget))
        print("\n3. lightest-loss armor per budget and poise floor (poise with talismans; damage kept vs no armor)")
        for B in a.budget:
            print(f"   budget {B:.1f}:")
            rowsout = []
            for P in floors:
                pieces = fr.pick(B, P * tcut)
                if not pieces:
                    print(f"     poise {P:>4.0f}: none fits")
                    continue
                k = kept(pieces, share)
                wsum = sum(p["weight"] for p in pieces)
                psum = sum(p["poise"] for p in pieces) / tcut
                cuts = {ty: 100 * (1 - math.prod(p["cut"][ty] for p in pieces)) for ty in TYPES}
                print(f"     poise {P:>4.0f}: kept {100 * k:5.1f}%  wt {wsum:5.1f}  poise {psum:5.1f}  "
                      + " / ".join(p["name"] for p in pieces)
                      + "   abs " + " ".join(f"{ty[:3]} {cuts[ty]:.0f}" for ty in TYPES))
                rowsout.append({"floor": P, "kept": k, "weight": wsum, "poise": psum,
                                "pieces": [p["name"] for p in pieces], "abs": cuts})
            frontier[B] = rowsout
        print("\n   damage kept by the best set per budget (no poise floor):")
        for B in range(0, int(max(a.budget)) + 1, 5):
            pieces = fr.pick(B, 0)
            psum = sum(p["poise"] for p in pieces) / tcut
            print(f"     {B:>3}: kept {100 * kept(pieces, share):5.1f}%  poise {psum:5.1f}  "
                  + " / ".join(p["name"] for p in pieces))
        floor = a.focus_poise * tcut
        if a.focus:
            print(f"\n   one-type sets at poise >= {a.focus_poise:.0f} (absorption of that type, best set per budget):")
        for ty in a.focus:
            one = {k: float(k == ty) for k in TYPES}
            ff = Frontier(cands, one, max(a.budget))
            for B in a.budget:
                pieces = ff.pick(B, floor)
                if not pieces:
                    print(f"     {ty:<9} {B:5.1f}: none fits")
                    continue
                print(f"     {ty:<9} {B:5.1f}: abs {100 * (1 - kept(pieces, one)):4.1f}% (mix kept {100 * kept(pieces, share):.1f}%)  "
                      f"wt {sum(p['weight'] for p in pieces):5.1f}  poise {sum(p['poise'] for p in pieces) / tcut:5.1f}  "
                      + " / ".join(p["name"] for p in pieces))
        if a.current:
            names = [x.strip() for x in a.current.split(",") if x.strip()]
            cur = []
            for c in range(4):
                cur += [p for p in cands[c] if p["name"] in names]
            print(f"   current {' / '.join(p['name'] for p in cur)}: kept {100 * kept(cur, share):.1f}%, "
                  f"weight {sum(p['weight'] for p in cur):.1f}, poise {sum(p['poise'] for p in cur) / tcut:.1f}")

    buffs = buff_table(t, share, builds)
    print("\n4. defensive sources needing no stats (damage kept on this mix while active; window adoption)")
    for b in buffs:
        dur = f"{b['seconds']:.0f}s" if b["seconds"] else "-"
        print(f"   {b['name'][:34]:<34} {b['kind']:<24} kept {100 * b['kept']:5.1f}%  poise x{b['poise_x']:.2f}  "
              f"{dur:>5}  worst {b['worst'][0][:3]} x{b['worst'][1]:.2f}  {b['builds']:>4} builds ({100 * b['share']:.1f}%)")

    if a.json:
        a.json.write_text(json.dumps({"share": share, "mix": {k: v for k, v in mix.items() if k != "per_build"},
                                      "poise": pc, "stopped": stopped, "frontier": frontier,
                                      "defense": me["defense"], "toughness_cut": tcut, "buffs": buffs}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
