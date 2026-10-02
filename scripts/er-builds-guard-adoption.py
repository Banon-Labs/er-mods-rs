#!/usr/bin/env python3
"""Calibrate the guard model's three unmeasured choices against corpus adoption, post hoc.

    python3 scripts/er-builds-guard-adoption.py --pvp new150.json --base base150.json
    python3 scripts/er-builds-guard-adoption.py --corpus-only

The choices (`er-mechanics-powerstance-guard`): the own-guard weight `SCORE_GUARD_OWN_WEIGHT`, the
repel punish weight `SCORE_REPEL_PUNISH`, and what a one-handed row holds in its left hand: its best
corpus shield (`best`, the model before 2026-10-01) or that shield weighted by its weapon class's
corpus shield-carry rate (`carry`, `Blockers.carried_left_shield`, the model since). Each candidate
rescores the stored slots of an `er-builds-pvp.py --json` ranking made with the guard model, in
either mode: a ranking whose `guard` records hold a `carry` value was made in carry mode.

Result at RL 150 (2026-10-01, on a best-mode ranking): 121 of 797 one-handed corpus builds carry a
shield (15.2%; 4% katana to 34% greatsword), so the best-shield assumption overstated the own
guard about sixfold and moved the one-handed rows in the top 50 from 15 to 34, where carry mode
puts 19. Adoption pins neither weight: every w from 0 to 0.4 and P from 0 to 1 is inside the
bootstrap CI in carry mode; only w = 0.4 under best mode is excluded.

Per candidate:

- own guard and its corpus reference are recomputed with `Blockers` at the candidate punish
  weight `P` (the best shield is re-chosen at that `P`); the stored values must reproduce at
  `P = 1`;
- a slot's pressure loss is linear in `P`: loss(P) = loss(1) + (P - 1) x punish_share x can_block;
- `carry` replaces the best-shield assumption with own = p(class) x best shield own (a one-handed
  build without a shield in the left hand cannot guard, which `one_hand_guard_mean` already
  counts as 0 in the reference);
- the moveset score is recomputed with `slot_score`, the skill options' stored scores rescaled by
  their own f_guard ratio, buff gains by the base-score ratio (an approximation).

Checks: within-class percentile coefficient and weapon-level Spearman as
`er-builds-aggregation-adoption.evaluate`, plus a grip test: across weapons with both rows and
enough corpus builds, Spearman(log score 1H / score 2H, corpus one-handed share), bootstrap CI.
Adoption is evidence, not a target; the candidates are checked on the corpus they come from.
"""

from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
CACHE = Path.home() / ".cache/er-build-planner"
_MODS: dict = {}


def _mod(name: str):
    if name not in _MODS:
        spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
        m = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = m
        spec.loader.exec_module(m)
        _MODS[name] = m
    return _MODS[name]


# -- corpus: who carries a shield -------------------------------------------------------------

def profile(st: dict) -> str:
    """The build's leading damage stat (`str`, `dex`, `int`, `fth`, `arc`), `quality` when STR and
    DEX are within 10 of each other and both lead."""
    keys = ("str", "dex", "int", "fth", "arc")
    v = {k: int(st.get(k) or 0) for k in keys}
    top = max(keys, key=lambda k: v[k])
    if top in ("str", "dex") and abs(v["str"] - v["dex"]) <= 10 and min(v["str"], v["dex"]) >= max(
            v["int"], v["fth"], v["arc"]):
        return "quality"
    return top


def corpus_carry(guard, blockers, builds) -> dict:
    """Per one-handed corpus build: primary `wepType`, stat profile and left-hand kind."""
    reg = blockers.reg
    rows = []
    for b, kind in zip(builds, blockers.kind):
        if b.get("is2h"):
            continue
        slot = guard._active_slots(b).get(0)
        wid = blockers._weapon_id(slot) if slot else None
        wt = reg.weapon[wid]["wepType"] if wid is not None else None
        rows.append({"wepType": wt, "profile": profile(b.get("stats") or {}), "kind": kind,
                     "shield": kind == "shield", "primary": slot["name"] if slot else None})
    return rows


def _group(rows, key):
    g = collections.defaultdict(list)
    for r in rows:
        g[r[key]].append(r)
    return sorted(g.items(), key=lambda kv: -len(kv[1]))


def wilson(k, n, z=1.96):
    if not n:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def carry_rates(rows, prior, filled_only=False) -> tuple[dict, float]:
    """`Blockers.carry_rates` recomputed from `corpus_carry` rows (`main` checks the two agree);
    with `filled_only`, over the builds with something in the left hand."""
    use = [r for r in rows if not filled_only or r["kind"] != "bare left hand"]
    overall = sum(r["shield"] for r in use) / max(1, len(use))
    out = {}
    for wt, grp in _group(use, "wepType"):
        k = sum(r["shield"] for r in grp)
        out[wt] = (k + prior * overall) / (len(grp) + prior)
    return out, overall


def print_corpus(rows) -> None:
    n = len(rows)
    k = sum(r["shield"] for r in rows)
    lo, hi = wilson(k, n)
    print(f"one-handed corpus builds: {n}, shield in the left hand {k} ({k / n:.3f} [{lo:.3f}, {hi:.3f}])")
    filled = [r for r in rows if r["kind"] != "bare left hand"]
    kf = sum(r["shield"] for r in filled)
    lo, hi = wilson(kf, len(filled))
    print(f"  with something in the left hand: {len(filled)}, shield {kf} ({kf / len(filled):.3f} [{lo:.3f}, {hi:.3f}])")
    print("  left-hand kinds: " + ", ".join(f"{a} {b}" for a, b in collections.Counter(r["kind"] for r in rows).most_common()))
    for key in ("profile", "wepType"):
        print(f"  by {key} (n >= 8): shield share [Wilson 95%]")
        for v, grp in _group(rows, key):
            if len(grp) < 8:
                continue
            kk = sum(r["shield"] for r in grp)
            lo, hi = wilson(kk, len(grp))
            print(f"    {str(v):<10} n {len(grp):>4}  {kk / len(grp):.3f} [{lo:.3f}, {hi:.3f}]")


# -- rescoring -------------------------------------------------------------------------------

class Guard:
    """Own guard per row at punish weight `P`, recomputed with `Blockers`."""

    def __init__(self, mirror, rl, window):
        self.G = _mod("er-mechanics-powerstance-guard")
        self.pvp = _mod("er-builds-pvp")
        self.gtab = self.G.Tables()
        self.builds = self.G.blocker_corpus(mirror, rl - window, rl + window)
        self.blk = self.G.Blockers(self.gtab, self.builds)
        self.opening = self.blk.opening_hits(self.builds)
        self.tables = self.pvp.AR.Tables(None)
        self._memo = {}

    def at(self, P: float, results) -> tuple[dict, float]:
        """({(weapon, two): (own, shield name)}, own_ref) at punish weight `P`."""
        if P in self._memo:
            return self._memo[P]
        self.G.SCORE_REPEL_PUNISH = P
        ref = self.blk.one_hand_guard_mean(self.opening)
        own = {}
        for r in results:
            start = (r["slots"].get("r1_1") or {}).get("startup")
            if r["two"]:
                wid = self.tables.find_weapon(r["weapon"], r["aff"])
                g = self.G.shield_guard(self.gtab, wid, r["level"], two_handed=True)
                own[(r["weapon"], True)] = (self.blk.own_guard(g, self.opening, startup=start), None)
            else:
                v, g = self.blk.best_left_shield(r["stats"], self.opening, start)
                own[(r["weapon"], False)] = (v, g and g["name"])
        self.G.SCORE_REPEL_PUNISH = 1.0
        self._memo[P] = (own, ref)
        return own, ref


def rescore(results, guard: Guard, w: float, P: float, carry: dict | None, w_old: float = 0.1):
    """`results` with `moveset.score`/`base_score` recomputed for own weight `w`, punish `P`,
    and carry rates `carry` ({wepType: p}, None = always the best shield)."""
    pvp, mv, G = guard.pvp, _mod("er-mechanics-moveset"), guard.G
    ash = _mod("er-mechanics-ashes")
    own_by, ref = guard.at(P, results)
    orig = G.guard_score_factor
    wt_of = {}
    out = []
    G.guard_score_factor = lambda p, o=None, r=None, own_weight=None: orig(p, o, r, own_weight=w)
    try:
        for r in results:
            own = own_by[(r["weapon"], r["two"])][0]
            if carry is not None and not r["two"]:
                if r["weapon"] not in wt_of:
                    wid = guard.tables.find_weapon(r["weapon"], "Standard")
                    wt_of[r["weapon"]] = guard.blk.reg.weapon[wid]["wepType"]
                own *= carry.get(wt_of[r["weapon"]], carry["_overall"])
            slots = {}
            for k, s in r["slots"].items():
                s = dict(s)
                g = s.get("guard")
                if g and g.get("punish_share") is not None:
                    loss = g["loss"] + (P - 1.0) * g["punish_share"] * g["can_block"]
                    s["guard"] = {**g, "loss": loss, "factor": 1.0 - G.SCORE_GUARD_BLOCK_RATE * loss}
                if s.get("guard_own") is not None:
                    s["guard_own"], s["guard_own_ref"] = own, ref
                slots[k] = s
            slots.update(pvp.jump_openers(slots, guard.npool))
            # The ranking charges the right grease's recasts after the moveset (`grease_time_factor`).
            base = mv.moveset_score(slots, pvp.slot_score, pvp.entry_frames)["score"] \
                * r["moveset"].get("grease_time_factor", 1.0)
            final = skill_final(r, base, own, ref, w, w_old, ash)
            out.append({**r, "slots": slots, "moveset": {**r["moveset"], "base_score": base, "score": final}})
    finally:
        G.guard_score_factor = orig
    return out


def skill_final(r, base, own, ref, w, w_old, ash):
    """`er-builds-aggregation-adoption.skill_final` with each option's score rescaled by its own
    f_guard ratio (own and ref are the row's) and buff gains by the base-score ratio."""
    term = r.get("skill_term")
    if not term or not base:
        return base
    m = r["moveset"]
    old_base = m.get("base_score") or base
    ratio_base = base / old_base if old_base else 1.0
    value = 0.0
    for o in term.get("available") or []:
        if o.get("score") is None and not o.get("buff_gain"):
            continue
        sc = o.get("score") or 0.0
        det = o.get("score_detail") or {}
        f_old = det.get("f_guard")
        if sc and f_old:
            pressure = f_old / (1.0 + w_old * (o_own(o, r) - o_ref(o, r))) if o_own(o, r) is not None else f_old
            sc = sc / f_old * pressure * (1.0 + w * (own - ref))
        hit = (o.get("share") or 0.0) * max(0.0, sc - base)
        value = max(value, hit, (o.get("buff_gain") or 0.0) * ratio_base)
    return base + ash.SKILL_WEIGHT * value


def o_own(o, r):
    return (r.get("guard") or {}).get("own")


def o_ref(o, r):
    return (r.get("guard") or {}).get("own_ref")


# -- checks ----------------------------------------------------------------------------------

def grip_test(results, mirror, rl, window, boot, min_n=5, seed=7):
    """Spearman over weapons of log(score 1H / score 2H) against the corpus one-handed share of
    builds with that primary (`is2h` known, at least `min_n`)."""
    gap = _mod("er-builds-adoption-gap")
    sa = _mod("er-builds-score-adoption")
    weapon, prot = gap.weight_tables()
    rs, _ = gap.corpus(mirror, "all", rl - window, rl + window, weapon, prot)
    cnt = collections.defaultdict(lambda: [0, 0])
    for x in rs:
        if x["primary"] and x["is2h"] is not None:
            cnt[x["primary"]][0] += not x["is2h"]
            cnt[x["primary"]][1] += 1
    sc = collections.defaultdict(dict)
    for r in results:
        sc[r["weapon"]][r["two"]] = r["moveset"]["score"]
    names = [w for w, g in sc.items() if len(g) == 2 and cnt.get(w, (0, 0))[1] >= min_n
             and g[False] > 0 and g[True] > 0]
    x = np.array([math.log(sc[w][False] / sc[w][True]) for w in names])
    y = np.array([cnt[w][0] / cnt[w][1] for w in names])
    rho = float(sa.spearman(x, y))
    rng = np.random.default_rng(seed)
    bi = rng.integers(0, len(names), (boot, len(names)))
    b = sa.spearman(x[bi], y[bi])
    one_h = sum(cnt[w][0] for w in cnt)
    tot = sum(cnt[w][1] for w in cnt)
    return {"n": len(names), "rho": rho, "ci": [float(v) for v in np.nanpercentile(b, (2.5, 97.5))],
            "model_1h_pref": float((x > 0).mean()), "corpus_1h_share": one_h / max(1, tot),
            "corpus_1h_pref": float((y > 0.5).mean()), "names": names, "draws": b}


class PctDraws:
    """`er-mechanics-ashes.ash_adoption_check`'s percentile coefficient, with its bootstrap draws
    kept (same weapons, same resample indices for every candidate), so two candidates can be
    compared paired."""

    def __init__(self, mirror, rl_lo, rl_hi, boot, seed=None):
        ash = _mod("er-mechanics-ashes")
        mv = _mod("er-mechanics-moveset")
        gap = _mod("er-builds-adoption-gap")
        self.ash = ash
        self.adoption = {ash.plain(k): v for k, v in mv.grip_shares(Path(mirror), rl_lo, rl_hi)["_adoption"].items()}
        rows, _ = gap.weight_tables()
        self.wep_type = {ash.plain(n): r["wepType"] for n, r in rows.items()}
        self.boot = boot
        self.seed = ash.CHECK_SEED if seed is None else seed
        self.idx = None

    def draws(self, results):
        best = {}
        for r in results:
            name = self.ash.plain(r["weapon"])
            score = (r.get("moveset") or {}).get("score")
            if score is None or name not in self.wep_type:
                continue
            unique = r.get("kind") == "unique"
            prev = best.get(name)
            if prev is None or score > prev[0]:
                best[name] = (score, unique or (prev[1] if prev else False))
            elif unique:
                best[name] = (prev[0], True)
        names = sorted(best)
        by = collections.defaultdict(list)
        for n in names:
            by[self.wep_type[n]].append(n)
        pct = {}
        for members in by.values():
            s = sorted(best[n][0] for n in members)
            for n in members:
                lo = s.index(best[n][0])
                hi = len(s) - 1 - s[::-1].index(best[n][0])
                pct[n] = 0.5 if len(s) == 1 else ((lo + hi) / 2) / (len(s) - 1)
        classes = sorted(by)
        col = {c: i for i, c in enumerate(classes)}
        x = np.zeros((len(names), len(classes) + 2))
        y = np.zeros(len(names))
        for i, n in enumerate(names):
            x[i, col[self.wep_type[n]]] = 1.0
            x[i, -2] = pct[n]
            x[i, -1] = 0.0 if best[n][1] else 1.0
            y[i] = math.log1p(self.adoption.get(n, 0))
        if self.idx is None:
            rng = np.random.default_rng(self.seed)
            self.idx = [rng.integers(0, len(names), len(names)) for _ in range(self.boot)]
            self.names = names
        assert names == self.names, "candidates must cover the same weapons"

        def fit(rows):
            coef, *_ = np.linalg.lstsq(x[rows], y[rows], rcond=None)
            return coef[-2]
        return float(fit(np.arange(len(names)))), np.array([fit(i) for i in self.idx])


def paired(d_a, d_b):
    d = d_a - d_b
    return [float(v) for v in np.percentile(d, (2.5, 97.5))]


def top_one_hand(results, n=50):
    rows = sorted(results, key=lambda r: -r["moveset"]["score"])[:n]
    return sum(not r["two"] for r in rows)


def evaluate(results, a, rl, pd: PctDraws):
    agg = _mod("er-builds-aggregation-adoption")
    e = agg.evaluate(results, a.mirror, rl, a.window, a.boot)
    p, draws = pd.draws(results)
    assert abs(p - e["pct"]) < 1e-9, (p, e["pct"])
    e["pct_draws"] = draws
    e["grip"] = grip_test(results, a.mirror, rl, a.window, a.boot)
    e["top50_1h"] = top_one_hand(results)
    return e


def line(name, e, ref=None, cur=None):
    g = e["grip"]
    extra = ""
    for tag, r in (("vs w0", ref), ("vs stored", cur)):
        if r is not None and r is not e:
            lo, hi = paired(e["pct_draws"], r["pct_draws"])
            glo, ghi = (paired(g["draws"], r["grip"]["draws"]) if g["names"] == r["grip"]["names"] else (np.nan, np.nan))
            extra += (f"  d{tag} pct {e['pct'] - r['pct']:+.3f} [{lo:+.3f}, {hi:+.3f}]"
                      f" grip {g['rho'] - r['grip']['rho']:+.3f} [{glo:+.3f}, {ghi:+.3f}]")
    print(f"  {name:<22}{e['pct']:>+7.3f} [{e['pct_ci'][0]:+.3f}, {e['pct_ci'][1]:+.3f}]"
          f"{e['rho_all']:>+7.3f}{e['rho_adopted']:>+7.3f}  grip {g['rho']:+.3f} [{g['ci'][0]:+.3f}, {g['ci'][1]:+.3f}]"
          f" 1H>2H {g['model_1h_pref']:.2f} top50 1H {e['top50_1h']:>2}" + extra)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pvp", type=Path, help="guard-model er-builds-pvp.py --json ranking")
    ap.add_argument("--base", type=Path, help="the pre-guard-model ranking, checked as given")
    ap.add_argument("--mirror", type=Path, default=CACHE / "builds.jsonl")
    ap.add_argument("--window", type=int, default=10)
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--weights", default="0,0.025,0.05,0.1,0.2,0.4")
    ap.add_argument("--punish", default="0,0.25,0.5,1")
    ap.add_argument("--corpus-only", action="store_true")
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    data = json.load(a.pvp.open()) if a.pvp else {"rl": 150}
    rl = data["rl"]
    guard = Guard(a.mirror, rl, a.window)
    crows = corpus_carry(guard.G, guard.blk, guard.builds)
    print_corpus(crows)
    carry, overall = carry_rates(crows, guard.G.CARRY_PRIOR_BUILDS)
    assert (carry, overall) == guard.blk.carry_rates(), "carry rates differ from Blockers.carry_rates"
    carry = {**carry, "_overall": overall}
    if a.corpus_only:
        return 0
    pvp = guard.pvp
    exch, neut = pvp.EXCH, pvp.NEUT
    pool = exch.Pool(exch.opponent_pool(pvp.ATK.Regulation(None), a.mirror, rl - a.window, rl + a.window))
    guard.npool = neut.NeutralPool(pool, neut.pool_reaches(pool.raw))
    results = [r for r in data["results"] if r["moveset"]["score"] is not None]
    own1, ref1 = guard.at(1.0, results)
    stored_carry = any((r.get("guard") or {}).get("carry") is not None for r in results)

    def best_own(r):
        g = r["guard"]
        return g["own"] / g["carry"] if g.get("carry") else g["own"]
    worst = max(abs(own1[(r["weapon"], r["two"])][0] - best_own(r)) for r in results
                if r["two"] or not stored_carry or r["guard"].get("carry"))
    print(f"\nstored ranking mode: {'carry' if stored_carry else 'best'}; own guard recomputed at P = 1 "
          f"vs stored: worst abs err {worst:.2e}; ref {ref1:.4f} vs {results[0]['guard']['own_ref']:.4f}")
    rep = rescore(results, guard, 0.1, 1.0, carry if stored_carry else None)
    err = max(abs(x["moveset"]["score"] - r["moveset"]["score"]) / max(abs(r["moveset"]["score"]), 1e-9)
              for x, r in zip(rep, results))
    errb = max(abs(x["moveset"]["base_score"] - r["moveset"]["base_score"])
               / max(abs(r["moveset"]["base_score"]), 1e-9) for x, r in zip(rep, results))
    print(f"rescore at the stored setting reproduces base {errb:.1e}, final {err:.1e}")
    table = {"carry": carry}
    pd = PctDraws(a.mirror, rl - a.window, rl + a.window, a.boot)
    stored = evaluate(rep, a, rl, pd)
    g = stored["grip"]
    print(f"grip test: {g['n']} weapons with both rows and >= 5 corpus builds; corpus one-handed share "
          f"{g['corpus_1h_share']:.3f} of primary builds, weapons used one-handed by most: {g['corpus_1h_pref']:.2f}")
    print("  pct = within-class percentile coef; grip = Spearman(log 1H/2H score, corpus 1H share); d = paired "
          "bootstrap difference vs w=0 at the same P (`vs w0`) and vs the stored w=0.1 P=1 setting")
    print(f"\n  {'candidate':<22}{'pct coef [95% CI]':<24}{'rho':>7}{'adopt':>7}")
    if a.base:
        bd = json.load(a.base.open())
        b = [r for r in bd["results"] if r["moveset"]["score"] is not None]
        table["base"] = evaluate(b, a, rl, pd)
        line("base (pre guard)", table["base"], None, stored)
    for P in [float(x) for x in a.punish.split(",")]:
        ref = None
        for w in [float(x) for x in a.weights.split(",")]:
            for cm in ("best", "carry"):
                if cm == "carry" and w == 0:
                    continue
                name = f"w={w:g} P={P:g} {cm}"
                res = rescore(results, guard, w, P, carry if cm == "carry" else None)
                e = evaluate(res, a, rl, pd)
                if w == 0:
                    ref = e
                table[name] = e
                line(name, e, ref, stored)
                sys.stdout.flush()
    if a.json:
        def clean(e):
            return {k: (clean(v) if isinstance(v, dict) else v) for k, v in e.items()
                    if k not in ("pct_draws", "draws", "names")}
        a.json.write_text(json.dumps({k: clean(v) for k, v in table.items()}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
