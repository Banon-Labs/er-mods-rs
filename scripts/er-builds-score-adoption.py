#!/usr/bin/env python3
"""How much each factor of the PvP weapon score moves its agreement with what players carry.

    python3 scripts/er-builds-pvp.py --rl 150 --json > rank.json            # the full ranking
    python3 scripts/er-builds-pvp.py --rl 150 --json --no-buff > nobuff.json # optional, for buffs
    python3 scripts/er-builds-score-adoption.py --pvp rank.json [--nobuff nobuff.json]
    python3 scripts/er-builds-score-adoption.py --selftest

Evidence, not an objective. Primary-weapon adoption in the planner corpus reflects taste, meta,
fashion, availability and build archetype as well as strength, so a factor that lowers the Spearman
rho against it is not thereby wrong, and one that raises it is not thereby right. Nothing here
feeds back into `er-builds-pvp.py`.

What is measured:

1. The weapon score as `er-builds-pvp.py --sort score` ranks it (`moveset.score`, skill term
   included), per weapon blended over its 1H/2H rows with `er-mechanics-moveset.grip_blend` and
   `grip_shares`, against primary adoption in the same window's PvP builds (`grip_shares`
   `_adoption`, RL `rl` +- `window`). Spearman over every swept weapon (unadopted ones as 0) and
   over the adopted ones only, each with a percentile bootstrap CI over weapons.
2. Ablation. The score is recomputed from the stored slots with one factor neutralised: its
   multiplier divided out of every `slot_score` (engagements included, since they call
   `slot_score` too), or for crit and parry its HP term dropped from the rate. The moveset
   aggregation, combo links and the skill term are ablated structurally (see `VARIANTS`). The
   skill term is rebuilt as `base + SKILL_WEIGHT x sum p x share x max(0, option - base)` from the
   stored options: an option score is adjusted for stagger, crit and parry (recomputable from what
   the options store) and held fixed for the rest, whose skill-side values are not in the file.
   Buffs cannot be divided out of stored damage, so that row compares against a second full run
   made without them, when `--nobuff` is given.
3. Delta = rho with the factor - rho without it, CI from the same bootstrap resamples (paired). A
   delta CI that spans 0 is reported as within noise.

The baseline recomputation is checked against the stored `moveset.base_score` and `moveset.score`
and the run refuses to report when they disagree.
"""

from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
CACHE = Path.home() / ".cache/er-build-planner"
BOOT = 2000
SEED = 20260929
#: Relative tolerance for the baseline to reproduce the stored scores.
REPRO_TOL = 1e-6
#: Rankings before 2026-10-01 stored `grease_time_factor` rounded to 5 decimals, so a row that
#: carries one may miss by up to half that digit over the factor on top of `REPRO_TOL`.
GREASE_FACTOR_ROUNDING = 0.5e-5

_MODS: dict = {}


def _mod(name: str):
    if name not in _MODS:
        spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
        m = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = m
        spec.loader.exec_module(m)
        _MODS[name] = m
    return _MODS[name]


#: name -> (kind, argument). kind `div`: divide that `slot_score` multiplier out; `hp`: drop that
#: HP term from the rate; the rest are structural.
VARIANTS = {
    "reach": ("div", "f_reach"),
    "coverage": ("div", "f_cover"),
    "frame adv": ("div", "f_adv"),
    "stagger": ("div", "f_stagger"),
    "guard pressure": ("div", "f_guard"),
    "exchange": ("div", "f_exchange"),
    "stamina": ("div", "f_stamina"),
    "equip weight": ("div", "f_weight"),
    "crit HP": ("hp", "crit_hp"),
    "parry HP": ("hp", "parry_hp"),
    "skill": ("skill", None),
    "combo links": ("links", None),
    "aggregation": ("bestslot", None),
}


# --------------------------------------------------------------------------------------------
# statistics


def rankdata(a: np.ndarray) -> np.ndarray:
    """Average ranks along the last axis (ties share their mean rank), 1-based."""
    a = np.asarray(a, dtype=float)
    flat = a.reshape(-1, a.shape[-1])
    out = np.empty_like(flat)
    for i, row in enumerate(flat):
        order = np.argsort(row, kind="mergesort")
        s = row[order]
        edges = np.flatnonzero(np.diff(s)) + 1
        starts = np.concatenate(([0], edges))
        ends = np.concatenate((edges, [len(s)]))
        r = np.empty(len(s))
        for b, e in zip(starts, ends):
            r[b:e] = (b + e + 1) / 2.0
        out[i, order] = r
    return out.reshape(a.shape)


def spearman(x, y) -> np.ndarray:
    """Spearman rho along the last axis (Pearson of average ranks); nan where a side is constant."""
    rx, ry = rankdata(x), rankdata(y)
    rx = rx - rx.mean(-1, keepdims=True)
    ry = ry - ry.mean(-1, keepdims=True)
    den = np.sqrt((rx * rx).sum(-1) * (ry * ry).sum(-1))
    with np.errstate(invalid="ignore", divide="ignore"):
        return (rx * ry).sum(-1) / den


def boot_index(n: int, boot: int = BOOT, seed: int = SEED) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, n, size=(boot, n))


def ci(v: np.ndarray, level: float = 0.95) -> tuple[float, float]:
    v = v[np.isfinite(v)]
    lo = (1 - level) / 2 * 100
    return float(np.percentile(v, lo)), float(np.percentile(v, 100 - lo))


# --------------------------------------------------------------------------------------------
# scores


def score_fn_for(pvp, variant: str | None):
    """`slot_score` with one factor neutralised."""
    kind, arg = VARIANTS.get(variant, (None, None))

    def fn(s, entry=0.0):
        sc = pvp.slot_score(s, entry)
        if not sc or kind is None:
            return sc
        out = dict(sc)
        if kind == "div":
            f = sc.get(arg) or 1.0
            out["score"] = sc["score"] / f
        elif kind == "hp":
            term = sc.get(arg) or 0.0
            if term and sc["rate"]:
                # crit adds `SCORE_CRIT_WEIGHT` x crit HP to the rate, parry subtracts parry HP.
                delta = pvp.SCORE_CRIT_WEIGHT * term if arg == "crit_hp" else -term
                rate = sc["rate"] - delta / sc["commit"] * pvp.SCORE_FPS
                out["score"] = sc["score"] * rate / sc["rate"]
                out["rate"] = rate
        return out
    return fn


def option_score(pvp, o: dict, crit: dict | None, variant: str | None) -> float | None:
    """A stored skill option's score with the factors recomputable from the file adjusted."""
    s = o.get("score")
    if s is None:
        return None
    kind, arg = VARIANTS.get(variant, (None, None))
    if kind == "div" and arg == "f_stagger":
        return s / (1.0 + pvp.SCORE_STAGGER_WEIGHT * (o.get("stagger") or 0.0))
    if kind == "hp":
        ends = [f for f in (o.get("roll"), o.get("next")) if f]
        crit_hp = (crit or {}).get("crit_hp") or 0.0
        parry_hp = ((crit or {}).get("parry_hp") or 0.0) if o.get("parryable") else 0.0
        num = o["dmg"] + pvp.SCORE_CRIT_WEIGHT * crit_hp - parry_hp
        if not ends or not num:
            return s
        new = num - (pvp.SCORE_CRIT_WEIGHT * crit_hp if arg == "crit_hp" else -parry_hp)
        return s * new / num
    return s


def skill_final(pvp, ash, r: dict, base: float, variant: str | None) -> float:
    term = r.get("skill_term")
    if not term or not base or variant == "skill":
        return base
    value = 0.0
    for o in term["options"]:
        sc = option_score(pvp, o, r.get("crit"), variant)
        if sc is None:
            continue
        value += o["p"] * o["share"] * max(0.0, sc - base)
    return base + ash.SKILL_WEIGHT * value


def row_score(pvp, mv, ash, r: dict, variant: str | None) -> tuple[float, float]:
    """(base moveset score, final score) of one sweep row under `variant`."""
    fn = score_fn_for(pvp, variant)
    if variant == "bestslot" or VARIANTS.get(variant, (None,))[0] == "bestslot":
        scored = [fn(s, pvp.entry_frames(k)) for k, s in r["slots"].items()
                  if k.removeprefix("2h_") not in pvp.SCORE_BEST_SLOT_EXCLUDED]
        base = max([x["score"] for x in scored if x] or [0.0])
    elif VARIANTS.get(variant, (None,))[0] == "links":
        keep = mv.chain_links
        mv.chain_links = lambda slots, opener: []
        try:
            base = mv.moveset_score(r["slots"], fn, pvp.entry_frames)["score"]
        finally:
            mv.chain_links = keep
    else:
        base = mv.moveset_score(r["slots"], fn, pvp.entry_frames)["score"]
    # The ranking charges the right hand's grease recasts after the moveset (`grease_time_factor`).
    base *= r["moveset"].get("grease_time_factor", 1.0)
    return base, skill_final(pvp, ash, r, base, variant)


def weapon_scores(mv, rows: dict, shares: dict) -> dict:
    """{weapon: grip-blended score} from {(weapon, two): score}."""
    by = collections.defaultdict(dict)
    for (w, two), s in rows.items():
        by[w][two] = s
    return {w: mv.grip_blend(g, shares.get(w)) or 0.0 for w, g in by.items()}


def repro_tol(r: dict) -> float:
    """`REPRO_TOL` plus the rounding a stored `grease_time_factor` may carry (`GREASE_FACTOR_ROUNDING`)."""
    f = r["moveset"].get("grease_time_factor")
    return REPRO_TOL + (GREASE_FACTOR_ROUNDING / f if f else 0.0)


def check_baseline(pvp, mv, ash, results: list[dict]) -> dict:
    """Worst relative error of the recomputed base and final scores, and `ok_base` / `ok_final`
    when every row is inside its `repro_tol`."""
    worst = {"base": 0.0, "final": 0.0}
    ok = {"base": True, "final": True}
    for r in results:
        m = r["moveset"]
        base, final = row_score(pvp, mv, ash, r, None)
        for want, got, which in ((m.get("base_score", m["score"]), base, "base"), (m["score"], final, "final")):
            err = abs(got - want) / max(abs(want), 1e-9)
            ok[which] = ok[which] and err <= repro_tol(r)
            worst[which] = max(worst[which], err)
    return {**worst, "ok_base": ok["base"], "ok_final": ok["final"]}


# --------------------------------------------------------------------------------------------
# analysis


def analyse(scores: dict[str, dict], adoption: dict, boot: int = BOOT, seed: int = SEED) -> dict:
    """`scores` = {variant name: {weapon: score}} with a `full` entry. Returns per population
    (`all`, `adopted`) the full rho with CI and per variant rho, delta and delta CI."""
    names = sorted(scores["full"])
    adopt = np.array([adoption.get(w, 0) for w in names], dtype=float)
    mats = {k: np.array([v.get(w, 0.0) or 0.0 for w in names]) for k, v in scores.items()}
    out = {}
    for pop, mask in (("all", np.ones(len(names), bool)), ("adopted", adopt > 0)):
        idx = np.flatnonzero(mask)
        a = adopt[idx]
        bi = idx[boot_index(len(idx), boot, seed)]
        ab = adopt[bi]
        full = mats["full"]
        r_full = float(spearman(full[idx], a))
        b_full = spearman(full[bi], ab)
        res = {"n": int(len(idx)), "rho": r_full, "ci": ci(b_full), "variants": {}}
        for k, m in mats.items():
            if k == "full":
                continue
            r = float(spearman(m[idx], a))
            bv = spearman(m[bi], ab)
            d = b_full - bv
            lo, hi = ci(d)
            res["variants"][k] = {"rho_without": r, "delta": r_full - r, "ci": (lo, hi),
                                  "ci_without": ci(bv),
                                  "verdict": ("within noise" if lo <= 0 <= hi else
                                              "raises agreement" if lo > 0 else "lowers agreement")}
        out[pop] = res
    return out


def print_report(res: dict, meta: dict) -> None:
    print(f"RL {meta['rl']} +- {meta['window']}: {meta['builds']} PvP builds, {meta['adopted_builds']} with a "
          f"primary weapon; {meta['weapons']} swept weapons, {meta['adopted_weapons']} of them adopted. "
          f"Bootstrap {meta['boot']} resamples over weapons, 95% percentile CIs.")
    if meta["repro"] is None:
        print("--full-only: stored scores as given, no ablation.")
    else:
        print(f"Baseline reproduces stored scores to rel err base {meta['repro']['base']:.1e}, "
              f"final {meta['repro']['final']:.1e}.")
    print("delta = rho with the factor - rho without it (positive: the factor moves the score toward "
          "what players carry). Adoption is taste and meta as well as strength: evidence, not a target.")
    for pop in ("all", "adopted"):
        r = res[pop]
        label = "all swept weapons (unadopted = 0)" if pop == "all" else "adopted weapons only"
        print(f"\n{label}: n={r['n']}  full score rho {r['rho']:+.3f}  CI [{r['ci'][0]:+.3f}, {r['ci'][1]:+.3f}]")
        print(f"  {'factor':<16}{'with':>8}{'without':>9}{'delta':>8}  {'delta 95% CI':<18}verdict")
        for k, v in sorted(r["variants"].items(), key=lambda kv: -kv[1]["delta"]):
            print(f"  {k:<16}{r['rho']:>+8.3f}{v['rho_without']:>+9.3f}{v['delta']:>+8.3f}  "
                  f"[{v['ci'][0]:+.3f}, {v['ci'][1]:+.3f}]  {v['verdict']}")


# --------------------------------------------------------------------------------------------
# self test


def selftest() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok   " if cond else "FAIL ") + msg)
        ok = ok and bool(cond)

    gap = _mod("er-builds-adoption-gap")
    rng = np.random.default_rng(1)
    x = rng.integers(0, 5, 40).astype(float)
    y = x + rng.normal(0, 2, 40)
    check(abs(float(spearman(x, y)) - gap.spearman(list(x), list(y))) < 1e-12,
          "numpy spearman equals er-builds-adoption-gap.spearman, ties included")
    check(np.allclose(rankdata(np.array([3.0, 1.0, 3.0, 2.0])), [3.5, 1.0, 3.5, 2.0]), "average ranks")
    m = np.stack([x, -x])
    r = spearman(m, np.stack([y, y]))
    check(r[0] > 0.5 and abs(r[0] + r[1]) < 1e-12, "batched spearman is per row")

    pvp, mv, ash = _mod("er-builds-pvp"), _mod("er-mechanics-moveset"), _mod("er-mechanics-ashes")
    slot = {"dmg": 300.0, "next": 30, "roll": 32, "reach": 3.0, "coverage": 1.1, "adv": -5, "adv_stagger": 10,
            "stagger": 0.4, "guard": None, "crit_hp": 40.0, "parry_hp": 25.0, "parryable": True,
            "f_weight": 0.9, "exchange": {"factor": 0.95 * 1.05, "f_exchange": 0.95, "f_stamina": 1.05},
            "status": {}, "combos": []}
    sc = pvp.slot_score(slot)
    check(score_fn_for(pvp, None)(slot)["score"] == sc["score"], "no variant leaves slot_score alone")
    for k, (kind, arg) in VARIANTS.items():
        if kind == "div":
            got = score_fn_for(pvp, k)(slot)["score"]
            check(abs(got * sc[arg] - sc["score"]) < 1e-9, f"{k}: divides {arg} out")
    nc = score_fn_for(pvp, "crit HP")(slot)["score"]
    want = pvp.slot_score({**slot, "crit_hp": 0.0})["score"]
    check(abs(nc - want) < 1e-9, "crit HP ablation equals slot_score with crit_hp 0")
    npar = score_fn_for(pvp, "parry HP")(slot)["score"]
    want = pvp.slot_score({**slot, "parryable": False})["score"]
    check(abs(npar - want) < 1e-9, "parry HP ablation equals slot_score of an unparryable hit")

    # A factor built to anti-correlate with adoption: removing it must raise rho (negative delta).
    n = 200
    strength = rng.gamma(2.0, 1.0, n)
    adoption = {f"w{i}": int(v) for i, v in enumerate(rng.poisson(strength * 2))}
    noise = rng.uniform(0.3, 3.0, n)
    full = {f"w{i}": strength[i] * noise[i] for i in range(n)}
    clean = {f"w{i}": strength[i] for i in range(n)}
    res = analyse({"full": full, "noise": clean}, adoption, boot=400)
    v = res["all"]["variants"]["noise"]
    check(v["delta"] < 0 and v["verdict"] == "lowers agreement",
          f"an unrelated multiplier reads as lowering agreement (delta {v['delta']:+.3f})")
    res = analyse({"full": full, "same": dict(full)}, adoption, boot=400)
    check(res["all"]["variants"]["same"]["verdict"] == "within noise" and
          res["all"]["variants"]["same"]["delta"] == 0.0, "an identical score is within noise")
    lo, hi = res["all"]["ci"]
    check(lo <= res["all"]["rho"] <= hi, "the full rho lies inside its bootstrap CI")
    check(abs(skill_final(pvp, ash, {"skill_term": {"options": [
        {"p": 0.5, "share": 1.0, "score": 900.0, "dmg": 500.0, "roll": 40, "next": 40, "stagger": 0.0,
         "parryable": False}]}, "crit": None}, 700.0, None) - (700.0 + ash.SKILL_WEIGHT * 0.5 * 200.0)) < 1e-9,
          "skill term rebuilt as base + SKILL_WEIGHT x p x share x gain")
    greased = {"slots": {"r1_1": slot}, "moveset": {"grease_time_factor": 0.9}}
    plain = {"slots": {"r1_1": slot}, "moveset": {}}
    check(abs(row_score(pvp, mv, ash, greased, None)[0] - 0.9 * row_score(pvp, mv, ash, plain, None)[0]) < 1e-9,
          "the baseline charges the stored grease_time_factor, as the ranking does")
    check(repro_tol(plain) == REPRO_TOL and abs(repro_tol(greased) - (REPRO_TOL + 0.5e-5 / 0.9)) < 1e-15,
          "a stored grease factor widens the tolerance by its rounding over the factor")
    print("selftest " + ("passed" if ok else "FAILED"))
    return 0 if ok else 1


# --------------------------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pvp", type=Path, help="er-builds-pvp.py --json output (the full ranking)")
    ap.add_argument("--nobuff", type=Path, help="the same ranking run without buffs, for the buff row")
    ap.add_argument("--mirror", type=Path, default=CACHE / "builds.jsonl")
    ap.add_argument("--window", type=int, default=10)
    ap.add_argument("--boot", type=int, default=BOOT)
    ap.add_argument("--json", type=Path, help="also write the result here")
    ap.add_argument("--full-only", action="store_true",
                    help="report only the stored score against adoption: no ablation, so no baseline "
                         "reproduction check (for comparing whole rankings, e.g. a constant sweep)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.pvp:
        ap.error("--pvp is required")
    pvp, mv, ash = _mod("er-builds-pvp"), _mod("er-mechanics-moveset"), _mod("er-mechanics-ashes")
    data = json.load(a.pvp.open())
    results, rl = data["results"], data["rl"]
    repro = None if a.full_only else check_baseline(pvp, mv, ash, results)
    if repro and not (repro["ok_base"] and repro["ok_final"]):
        raise SystemExit(f"baseline does not reproduce the stored scores: {repro}")
    shares = mv.grip_shares(a.mirror, rl - a.window, rl + a.window)
    adoption = shares["_adoption"]
    scores = {"full": weapon_scores(mv, {(r["weapon"], r["two"]): r["moveset"]["score"] for r in results}, shares)}
    for k in () if a.full_only else VARIANTS:
        scores[k] = weapon_scores(mv, {(r["weapon"], r["two"]): row_score(pvp, mv, ash, r, k)[1]
                                       for r in results}, shares)
    if a.nobuff:
        nb = json.load(a.nobuff.open())["results"]
        scores["buffs"] = weapon_scores(mv, {(r["weapon"], r["two"]): r["moveset"]["score"] for r in nb}, shares)
        missing = set(scores["full"]) - set(scores["buffs"])
        if missing:
            raise SystemExit(f"--nobuff lacks {len(missing)} weapons, e.g. {sorted(missing)[0]}")
    res = analyse(scores, adoption, boot=a.boot)
    meta = {"rl": rl, "window": a.window, "builds": shares["_builds"], "adopted_builds": sum(adoption.values()),
            "weapons": len(scores["full"]), "adopted_weapons": sum(1 for w in scores["full"] if adoption.get(w)),
            "boot": a.boot, "repro": repro}
    print_report(res, meta)
    if a.json:
        a.json.write_text(json.dumps({"meta": meta, "result": res}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
