#!/usr/bin/env python3
"""How the way a weapon row combines its openers moves the ranking's agreement with adoption.

    python3 scripts/er-builds-aggregation-adoption.py --pvp rank.json            # every candidate
    python3 scripts/er-builds-aggregation-adoption.py --pvp rank.json --agg standout:0.5 --movers 15
    python3 scripts/er-builds-aggregation-adoption.py --selftest

Write-up: docs/er-mechanics/moveset.md section 8. The candidates are the `AGGREGATE` forms of
`er-mechanics-moveset.aggregate`, one parameter each:

- `family:a` the matching mean over families with exponent `a` (`family:1` is the default; `inf`
  is the best family, which is the best opener);
- `standout:w` `(1 - w)` x the family matching mean + `w` x the best opener's engagement;
- `openers:a` the matching mean over every opener's own engagement.

Each candidate is recomputed from the stored slots of an `er-builds-pvp.py --json` ranking: the
jump openers rebuilt with their neutral contest (`er-builds-pvp.jump_openers`), the moveset score
with `er-mechanics-moveset.moveset_score(..., agg=...)`, and the skill term as
`er-mechanics-ashes.skill_term` forms it from the stored mountable options (`skill_final`; their
scores and buff gains held fixed, so a buff option whose own score depends on the aggregation is
not rescored: an approximation a full `er-builds-pvp.py --aggregate` run removes). The baseline
must reproduce the stored base and final scores first.

Checks per candidate: the within-class score percentile coefficient of
`er-mechanics-ashes.ash_adoption_check` (log1p adoption ~ class + percentile + ash, bootstrap CI)
and the weapon-level Spearman rho of `er-builds-score-adoption.analyse` (all swept weapons, and
the adopted ones). Adoption is evidence, not a target, and the candidates are chosen on the same
corpus they are checked against.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = Path.home() / ".cache/er-build-planner"
_MODS: dict = {}

#: The candidates `main` sweeps without `--agg`.
CANDIDATES = ("family:0", "family:1", "family:2", "family:4", "family:inf",
              "standout:0.25", "standout:0.5", "standout:0.75",
              "openers:1", "openers:2", "openers:4")


def _mod(name: str):
    if name not in _MODS:
        spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
        m = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = m
        spec.loader.exec_module(m)
        _MODS[name] = m
    return _MODS[name]


def parse_agg(text: str) -> tuple[str, float]:
    """`kind:x` -> (kind, x); `family:a` becomes (`family`, a), handled as the matching exponent."""
    kind, _, x = text.partition(":")
    if kind not in ("family", "standout", "openers") or not x:
        raise ValueError(f"aggregation {text!r}: want family:a, standout:w or openers:a")
    return kind, float(x)


def skill_final(r: dict, base: float) -> float:
    """The final score for a new moveset `base`, the way `er-mechanics-ashes.skill_term` forms it:
    base + `SKILL_WEIGHT` x the best `worth` over the mountable options, worth = max(share x
    max(0, option score - base), buff gain). The option scores and buff gains are the stored ones."""
    term = r.get("skill_term")
    if not term or not base:
        return base
    ash = _mod("er-mechanics-ashes")
    value = 0.0
    for o in term.get("available") or []:
        if o.get("score") is None and not o.get("buff_gain"):
            continue
        hit = (o.get("share") or 0.0) * max(0.0, (o.get("score") or 0.0) - base)
        value = max(value, hit, o.get("buff_gain") or 0.0)
    return base + ash.SKILL_WEIGHT * value


def check_final(results: list[dict]) -> float:
    """Worst relative error of `skill_final` against the stored final scores."""
    worst = 0.0
    for r in results:
        m = r["moveset"]
        got = skill_final(r, m["base_score"])
        worst = max(worst, abs(got - m["score"]) / max(abs(m["score"]), 1e-9))
    return worst


def rescore(results: list[dict], agg: tuple[str, float]) -> list[dict]:
    """A shallow copy of `results` with `moveset.score` / `base_score` recomputed under `agg`."""
    pvp, mv, ash, sa = (_mod("er-builds-pvp"), _mod("er-mechanics-moveset"), _mod("er-mechanics-ashes"),
                        _mod("er-builds-score-adoption"))
    kind, x = agg
    out = []
    for r in results:
        if kind == "family":
            ms = mv.moveset_score(r["slots"], pvp.slot_score, pvp.entry_frames, exp=x)
        else:
            ms = mv.moveset_score(r["slots"], pvp.slot_score, pvp.entry_frames, agg=agg)
        # The ranking charges the right hand's grease recasts after the moveset (`grease_time_factor`).
        base = ms["score"] * r["moveset"].get("grease_time_factor", 1.0)
        final = skill_final(r, base)
        rr = dict(r)
        rr["moveset"] = {**r["moveset"], "base_score": base, "score": final}
        out.append(rr)
    return out


#: The right-hand slot whose factors a powerstance slot borrows (`powerstance_rows`).
DUAL_TWIN = {"dual_1": "r1_1", "dual_2": "r1_2", "dual_3": "r1_3", "dual_4": "r1_4", "dual_5": "r1_5",
             "dual_6": "r1_6", "dual_dash": "run_r1", "dual_roll": "roll_r1", "dual_crouch": "crouch_r1",
             "dual_bstep": "bstep_r1", "dual_jump": "jump_r1"}
#: Fields a powerstance slot takes from its twin: the contest, reaction dodge, guard pressure,
#: coverage, crit and parry HP, equip weight and reach. `er-mechanics-moveset.DualScorer` computes
#: none of them (the reach module poses right-hand hitboxes only), so without them a dual slot
#: would skip every penalty a right-hand slot pays. `INFERRED` proxy.
TWIN_FIELDS = ("exchange", "neutral", "react", "guard", "guard_own", "guard_own_ref", "coverage", "crit_hp",
               "parry_hp", "f_weight", "reach", "reach_source")


def powerstance_rows(results: list[dict], rl: int, window: int, mirror: Path, grease: str,
                     pool=None, npool=None) -> list[dict]:
    """One row per 1H row whose weapon powerstances with itself: its right-hand slots plus the dual
    L1 slots (`er-mechanics-moveset.add_powerstance`, with the crouch L1 played as the rolling L1
    where the behavior script does so), each dual slot carrying its twin's factors (`TWIN_FIELDS`)
    except those measured on its own clip (the slot's `measured`), and scored with `slot_score`.
    The skill options are the 1H row's."""
    pvp, mv = _mod("er-builds-pvp"), _mod("er-mechanics-moveset")
    one = [dict(r) for r in results if not r["two"]]
    mv.add_powerstance(pvp, one, rl, window, mirror, grease, pool, npool)
    entry = mv._entry_fn(pvp)
    tables, reg = pvp.AR.Tables(None), pvp.ATK.Regulation(None)
    out = []
    for r in one:
        duals = r.get("dual_slots")
        if not duals:
            continue
        for k, s in duals.items():
            twin = r["slots"].get(DUAL_TWIN.get(k, ""))
            if twin:
                s.update({f: twin[f] for f in TWIN_FIELDS if f in twin and f not in s.get("measured", ())})
        if "dual_jump" in duals:
            # `with_jumps` skips a slot set whose right-hand jumps are already openers.
            duals.update(mv._mod("er-mechanics-jump").jump_slots({"dual_jump": duals["dual_jump"]}, entry))
        cat = reg.weapon[tables.find_weapon(r["weapon"], "Standard")]["wepmotionCategory"]
        out.append({**r, "dual_slots": duals, "powerstance": True, "motion_category": cat})
    return out


def score_powerstance(row: dict, agg: tuple | None) -> dict:
    """The powerstance row's moveset (families of `er-mechanics-moveset.score_results`) and final
    score under `agg` (None: the stored aggregation)."""
    pvp, mv = _mod("er-builds-pvp"), _mod("er-mechanics-moveset")
    # Only the attacks powerstance plays: with the right-hand slots in, a pair whose best opener is
    # its one-handed R2 or jump scored as a powerstance row while powerstance added nothing to it.
    fam = dict(mv.DUAL_FAMILIES)
    slots = dict(row["dual_slots"])
    kw = {"exp": agg[1]} if agg and agg[0] == "family" else {"agg": agg} if agg else {}
    ms = mv.moveset_score(slots, pvp.slot_score, mv._entry_fn(pvp), families=fam, **kw)
    base = ms["score"] * row["moveset"].get("grease_time_factor", 1.0)
    return {"base": base, "score": skill_final(row, base), "best": ms["best_opener"],
            "families": {n: (f["opener"], round(f["score"], 1)) for n, f in ms["families"].items()}}


def report_powerstance(results: list[dict], ps: list[dict], mirror: Path, rl: int, window: int, boot: int,
                       table: dict, agg: tuple | None = None) -> None:
    """Where the powerstance rows land among the stored rows, and the percentile check with a
    weapon's best of 1H, 2H and powerstance."""
    ash = _mod("er-mechanics-ashes")
    scored = [(r, score_powerstance(r, agg)) for r in ps]
    allrows = sorted([(r["moveset"]["score"], r["weapon"], "2H" if r["two"] else "1H") for r in results] +
                     [(p["score"], r["weapon"], "PS") for r, p in scored], reverse=True)
    rank = {(w, g): i + 1 for i, (_, w, g) in enumerate(allrows)}
    base_rank = ranks(results)
    print(f"\npowerstance rows ({len(scored)}), ranked among {len(allrows)} rows (stored rows + powerstance)"
          f"{'' if agg is None else ' under ' + str(agg)}; 1H/2H rank before powerstance in brackets")
    top = sorted(scored, key=lambda x: -x[1]["score"])
    colossal = [x for x in scored if x[0].get("motion_category") in COLOSSAL_CATEGORIES]
    for r, p in top[:15] + [x for x in colossal if x not in top[:15]]:
        w = r["weapon"]
        print(f"  {rank[(w, 'PS')]:>4} {w[:30]:<31}PS {p['score']:>7.1f}  1H {rank.get((w, '1H'), '-')}"
              f" ({base_rank.get((w, False), '-')})  2H {rank.get((w, '2H'), '-')} ({base_rank.get((w, True), '-')})"
              f"  best {p['best']}  " + ", ".join(f"{n} {o} {s}" for n, (o, s) in p["families"].items()))
    extra = [{"weapon": r["weapon"], "two": False, "kind": r.get("kind"), "moveset": {"score": p["score"]}}
             for r, p in scored]
    c = ash.ash_adoption_check(results + extra, mirror, rl - window, rl + window, boot=boot)
    print(f"  percentile coef with powerstance as a third grip (best row per weapon): {c['pct_coef']:+.3f} "
          f"[{c['pct_ci'][0]:+.3f}, {c['pct_ci'][1]:+.3f}]")
    table[f"powerstance{'' if agg is None else ':' + str(agg)}"] = {
        "pct": c["pct_coef"], "pct_ci": c["pct_ci"],
        "rows": {r["weapon"]: {"rank": rank[(r["weapon"], "PS")], **p} for r, p in scored}}


#: `wepmotionCategory` of the colossal swords (26) and colossal weapons (31)
#: (`er-mechanics-attacks.STEALTH_ATTACK_CATEGORIES` notes); `report_powerstance` always lists them.
COLOSSAL_CATEGORIES = (26, 31)


def evaluate(results: list[dict], mirror: Path, rl: int, window: int, boot: int) -> dict:
    mv, ash, sa = _mod("er-mechanics-moveset"), _mod("er-mechanics-ashes"), _mod("er-builds-score-adoption")
    c = ash.ash_adoption_check(results, mirror, rl - window, rl + window, boot=boot)
    shares = mv.grip_shares(mirror, rl - window, rl + window)
    ws = sa.weapon_scores(mv, {(r["weapon"], r["two"]): r["moveset"]["score"] for r in results}, shares)
    sp = sa.analyse({"full": ws}, shares["_adoption"], boot=boot)
    return {"pct": c["pct_coef"], "pct_ci": c["pct_ci"], "ash": c["ash_coef"],
            "rho_all": sp["all"]["rho"], "rho_all_ci": sp["all"]["ci"],
            "rho_adopted": sp["adopted"]["rho"], "rho_adopted_ci": sp["adopted"]["ci"]}


def ranks(results: list[dict]) -> dict:
    rows = sorted(results, key=lambda r: -(r["moveset"]["score"] or 0.0))
    return {(r["weapon"], r["two"]): i + 1 for i, r in enumerate(rows)}


def selftest() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok   " if cond else "FAIL ") + msg)
        ok = ok and bool(cond)

    mv = _mod("er-mechanics-moveset")
    fam, ops = [4.0, 2.0, 1.0], [4.0, 3.0, 2.0, 1.0]
    check(abs(mv.aggregate(fam, ops) - mv.matching_mean(fam)[0]) < 1e-12, "no aggregation is the family mean")
    check(abs(mv.aggregate(fam, ops, agg=("standout", 1.0)) - 4.0) < 1e-12, "standout 1 is the best opener")
    check(abs(mv.aggregate(fam, ops, agg=("standout", 0.0)) - mv.matching_mean(fam)[0]) < 1e-12,
          "standout 0 is the family mean")
    check(abs(mv.aggregate(fam, ops, agg=("openers", 0.0)) - 2.5) < 1e-12, "openers 0 is the opener mean")
    check(parse_agg("family:inf") == ("family", float("inf")), "family:inf parses")
    print("selftest " + ("passed" if ok else "FAILED"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pvp", type=Path, help="er-builds-pvp.py --json output")
    ap.add_argument("--agg", action="append", default=[], help="candidate(s); default the CANDIDATES list")
    ap.add_argument("--mirror", type=Path, default=CACHE / "builds.jsonl")
    ap.add_argument("--window", type=int, default=10)
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--movers", type=int, default=0, help="also list the rows that move most, per candidate")
    ap.add_argument("--json", type=Path, help="write the table here")
    ap.add_argument("--no-neutral", action="store_true", help="the ranking was run with --no-neutral")
    ap.add_argument("--timing-mixup", nargs="?", const="crouch,entry", default="", metavar="PARTS",
                    help="the ranking was run with --timing-mixup PARTS")
    ap.add_argument("--stored-only", action="store_true", help="only the stored ranking's checks, no candidates")
    ap.add_argument("--powerstance", action="store_true",
                    help="also rank same-weapon powerstance rows (`powerstance_rows`) among the stored rows")
    ap.add_argument("--grease", default="dlc-drawstring")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.pvp:
        ap.error("--pvp is required")
    pvp = _mod("er-builds-pvp")
    _MODS.setdefault("er-mechanics-moveset", pvp.MOVESET)
    mv, ash, sa = _mod("er-mechanics-moveset"), _mod("er-mechanics-ashes"), _mod("er-builds-score-adoption")
    # `er-builds-score-adoption` and this module must score with the same `er-builds-pvp` and
    # moveset modules whose flags are set below.
    sa._MODS.update({"er-builds-pvp": pvp, "er-mechanics-moveset": mv})
    pvp.TIMING_MIXUP = frozenset(p.strip() for p in a.timing_mixup.split(",") if p.strip())
    data = json.load(a.pvp.open())
    results, rl = [r for r in data["results"] if r["moveset"]["score"] is not None], data["rl"]
    stored_agg = next((r["moveset"].get("agg") for r in results if r["moveset"].get("agg")), None)
    mv.AGGREGATE = tuple(stored_agg) if stored_agg else None
    if not a.no_neutral:
        # The ranking scores its jump openers with their own neutral contest
        # (`er-builds-pvp.jump_openers`), which the stored slots do not carry: rebuild them with
        # the same pool so the baseline reproduces.
        exch, neut = pvp.EXCH, pvp.NEUT
        pool = exch.Pool(exch.opponent_pool(pvp.ATK.Regulation(None), a.mirror, rl - a.window, rl + a.window))
        npool = neut.NeutralPool(pool, neut.pool_reaches(pool.raw))
        results = [{**r, "slots": {**r["slots"], **pvp.jump_openers(r["slots"], npool)}} for r in results]
    base_check = sa.check_baseline(pvp, mv, ash, results)
    repro = {"base": base_check["base"], "final": check_final(results)}
    if not base_check["ok_base"] or repro["final"] > sa.REPRO_TOL:
        raise SystemExit(f"baseline does not reproduce the stored scores: {repro}")
    stored = evaluate(results, a.mirror, rl, a.window, a.boot)
    base_rank = ranks(results)
    table = {"stored": stored}
    print(f"RL {rl}, {len(results)} rows; baseline reproduces to {repro['base']:.1e} / {repro['final']:.1e}")
    print(f"  {'aggregation':<16}{'pct coef':>9}  {'CI':<17}{'ash':>7}{'rho all':>9}{'rho adopted':>12}")

    def line(name, e):
        print(f"  {name:<16}{e['pct']:>+9.3f}  [{e['pct_ci'][0]:+.3f}, {e['pct_ci'][1]:+.3f}]"
              f"{e['ash']:>+7.3f}{e['rho_all']:>+9.3f}{e['rho_adopted']:>+12.3f}")
    line("stored", stored)
    if a.powerstance:
        ps = powerstance_rows(results, rl, a.window, a.mirror, a.grease,
                              *((pool, npool) if not a.no_neutral else ()))
        report_powerstance(results, ps, a.mirror, rl, a.window, a.boot, table)
    for text in ([] if a.stored_only else a.agg or CANDIDATES):
        agg = parse_agg(text)
        res = rescore(results, agg)
        e = evaluate(res, a.mirror, rl, a.window, a.boot)
        table[text] = e
        line(text, e)
        if a.movers:
            rk = ranks(res)
            mv_rows = sorted(rk, key=lambda k: -abs(rk[k] - base_rank[k]))[:a.movers]
            for k in mv_rows:
                print(f"      {k[0][:30]:<31}{'2H' if k[1] else '1H'} {base_rank[k]:>4} -> {rk[k]:>4}")
    if a.json:
        a.json.write_text(json.dumps(table, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
