#!/usr/bin/env python3
"""Score a stat spread on one weapon the way the weapon embedding scores it: the R1 string's PvP
damage plus what its status is worth, per second of the string. Shared by
`er-mechanics-class-by-weapon.py` and `er-mechanics-status-infusions.py`.

Per (weapon, affinity, grip) the R1 string is the embedding's moveset (`Embed.moveset`): links
r1_1, r1_2, ... in order. Its length follows `er-mechanics-grip-modes.chain`: each press at the
previous link's earliest next-R1 cancel, the last link to its roll cancel. Per stat spread:

    damage = sum over links of `Embed.hit_damage` (each element's attack x the link's motion
             value, through the PvP damage function against the corpus median defender of the
             RL window), the same number the embedding's slot `damage` is
    status = `ash-table.Table.status_hp` over the links (each hit's build-up spread linearly
             over the target's gauge, a DoT cut to one engagement), the embedding's `status_hp`
    score  = (damage + every status's HP) x 30 / string frames

This replaced one motion-value-100 hit as the score (user, 2026-10-12): per hit, Giant-Crusher
Heavy 2H ranked 1st of 3946 options on the STR 80 build's embedding and 32nd by R1 string damage
per second, which no speed-blind number can show.

    python3 scripts/er-mechanics-string-score.py --selftest
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FPS = 30.0


def _load(name: str, fname: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HERE / fname)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


WE = _load("er_mechanics_weapon_embed", "er-mechanics-weapon-embed.py")
OPT, AR, AT = WE.OPT, WE.AR, WE.AT
RES = OPT.RES
#: Statuses the arcane multiplier scales (`docs/er-mechanics/status.md` section 1).
ARCANE_STATUSES = {"bleed", "poison", "sleep", "madness"}
#: Coarse arcane step of `best_spend`, then every value within one step of the best.
ARC_STEP = 5
_E: dict = {}


def embed() -> "WE.Embed":
    """One `Embed` per process (8-9 s to build); its stats are set per evaluation."""
    if "e" not in _E:
        _E["e"] = WE.Embed(WE.DEFAULT_STATS, reach=False)
    return _E["e"]


def r1_string(e, base_id: int, grip: str) -> tuple[list, float] | None:
    """(links, frames) of the weapon's R1 string in `grip` ("1H"/"2H"), or None."""
    slots = e.moveset(base_id).get(grip) or {}
    keys = sorted((k for k in slots if k.startswith("r1_") and k[3:].isdigit() and "_row" in slots[k]),
                  key=lambda k: int(k[3:]))
    links = [slots[k] for k in keys]
    if not links or not links[-1].get("roll_cancel"):
        return None
    frames = sum(s.get("next_cancel") or s.get("roll_cancel") or 0 for s in links[:-1]) + links[-1]["roll_cancel"]
    return (links, float(frames)) if frames > 0 else None


class StringScorer:
    """`score(st)` for `OPT.spend`: R1 string (damage + status) per second. `cap` and `floor`
    are what `spend` reads; scores are cached by the damage stats."""

    def __init__(self, e, base_id: int, affinity: str, grip: str):
        self.e, self.base_id, self.affinity, self.grip = e, base_id, affinity, grip
        self.name = e.art.names[base_id]
        self.two = grip == "2H"
        got = r1_string(e, base_id, grip)
        self.links, self.frames = got if got else ([], 0.0)
        wid = e.art.find_weapon(self.name, affinity)
        self.level = e.art.max_level(e.art.weapons[wid]["reinforceTypeId"])
        self.cap = OPT.stat_caps(e.art, base_id, affinity, self.level)
        self.floor: dict = {}
        self.cache: dict = {}
        self.status_cache: dict = {}

    def parts(self, st: dict) -> dict:
        key = tuple(st[k] for k in OPT.DAMAGE_STATS)
        if key not in self.cache:
            e = self.e
            e.stats = {k: st[k] for k in OPT.DAMAGE_STATS}
            r, _, level = e.ar(self.name, self.affinity, self.grip)
            ar_by = {el: r["damage"].get(el, {}).get("total", 0.0) for el in WE.ELEMENTS}
            dmg = sum(e.hit_damage(self.base_id, ar_by, s["mv_by"], s["_row"]["atk_row"], s["hits"])["pvp_damage_total"]
                      for s in self.links)
            # Build-up depends on arcane alone once every requirement is met, which `build`'s
            # floors guarantee, so the status half is cached by arcane.
            if st["arc"] not in self.status_cache:
                sh, ws = e.status_shim(self.name, self.affinity, level, self.two)
                status = (AT.Table.status_hp(sh, self.two, [], [{"attack": s["_row"]} for s in self.links])
                          if ws["sources"] else {})
                self.status_cache[st["arc"]] = (status, any(s["status"] in ARCANE_STATUSES for s in ws["sources"]))
            status, arcane = self.status_cache[st["arc"]]
            total = dmg + sum(status.values())
            self.cache[key] = {"damage": dmg, "status": status,
                               "score": total * FPS / self.frames if self.frames else 0.0,
                               "arcane_status": arcane}
        return self.cache[key]

    def score(self, st: dict) -> float:
        return self.parts(st)["score"]


def best_spend(sc: StringScorer, st: dict, points: int) -> tuple[dict, int]:
    """`OPT.spend`, with arcane searched outside it when the weapon's own status scales with it.
    The greedy walk looks `LOOKAHEAD` points ahead and the arcane status curve is flat for
    longer than that before it climbs, so the walk stops short: Blood Uchigatana at RL 150
    settled on ARC 22 (382.8 per hit) where ARC 45 scored 404.8. Each arcane value is fixed in
    turn and the rest spent on the other damage stats.

    The plain greedy spend over every damage stat stays a candidate: fixing arcane and spending
    the rest can lose to the joint walk when the status curve is flat. Measured with the
    proc-threshold status model (`er-mechanics-status.sequence_hp`): Blood Uchigatana 1H, Bandit
    RL 150, the fixed-arcane search settled on ARC 28 at 541.5 while the joint walk reached ARC
    22 at 542.2."""
    sc.floor = dict(st)
    plain = OPT.spend(dict(st), points, sc, OPT.DAMAGE_STATS)
    if not sc.parts(st)["arcane_status"]:
        return plain
    others = tuple(k for k in OPT.DAMAGE_STATS if k != "arc")
    top = min(sc.cap.get("arc", OPT.STAT_CAP), st["arc"] + points)
    tried: dict = {}

    def at(arc):
        if arc not in tried:
            fixed = dict(st, arc=arc)
            sc.floor = dict(fixed)
            got, left = OPT.spend(fixed, points - (arc - st["arc"]), sc, others)
            tried[arc] = (sc.score(got), got, left)
        return tried[arc]

    for arc in sorted(set(range(st["arc"], top + 1, ARC_STEP)) | {top}):
        at(arc)
    best = max(tried, key=lambda k: tried[k][0])
    for arc in range(max(st["arc"], best - ARC_STEP + 1), min(top, best + ARC_STEP - 1) + 1):
        at(arc)
    _, got, left = max(list(tried.values()) + [(sc.score(plain[0]), *plain)], key=lambda v: v[0])
    return got, left


def start_spread(sc: StringScorer, cls: str, rl: int, vig: int, end: int,
                 floors: dict | None = None) -> tuple[dict, int] | None:
    """(spread, points left to spend) before any damage stat is bought: the class's base stats
    raised to the Vigor and Endurance floors (`vit` is Endurance in the planner's keys,
    `er-builds-optimize.STATS`), every requirement and any extra `floors`. None when the class
    cannot reach `rl` with those floors."""
    if "model" not in _E:                 # 0.7 s to build; `setdefault` would build it every call
        _E["model"] = RES.Model()
    cls_level, base = _E["model"].class_base(cls)
    need = OPT.requirements(sc.e.art, sc.base_id, sc.affinity, sc.level, sc.two)
    st = {k: max(base[k], need.get(k, 0), (floors or {}).get(k, 0)) for k in OPT.STATS}
    st["vig"] = max(st["vig"], vig)
    st["end"] = max(st["end"], end)
    points = rl - (sum(st.values()) - OPT.LEVEL_OFFSET)
    if points < 0 or rl < cls_level:
        return None
    return st, points


def build(sc: StringScorer, cls: str, rl: int, vig: int, end: int, floors: dict | None = None) -> dict | None:
    """The class's best spread on `sc`'s weapon: `start_spread`, the rest spent by `best_spend`.
    None when the class cannot reach `rl` with those floors."""
    got = start_spread(sc, cls, rl, vig, end, floors)
    if got is None:
        return None
    st, left = best_spend(sc, *got)
    p = sc.parts(st)
    return {"stats": st, "unspent": left, "score": round(p["score"], 1), "damage": round(p["damage"], 1),
            "status": p["status"], "frames": sc.frames, "links": len(sc.links)}


def selftest() -> int:
    e = embed()
    base = e.art.find_weapon("Uchigatana", "Standard")
    base -= base % 10000
    sc = StringScorer(e, base, "Blood", "1H")
    ok = True

    def check(label, cond):
        nonlocal ok
        print(("ok   " if cond else "FAIL ") + label)
        ok &= bool(cond)

    check("Uchigatana 1H R1 string has 5 links", len(sc.links) == 5)
    lo = sc.parts({"str": 14, "dex": 60, "int": 9, "fth": 8, "arc": 10})
    hi = sc.parts({"str": 14, "dex": 25, "int": 9, "fth": 8, "arc": 45})
    check("Blood Uchigatana: ARC 45 builds more bleed than ARC 10", hi["status"]["bleed"] > lo["status"]["bleed"])
    check("Blood Uchigatana: DEX 60 deals more damage than DEX 25", lo["damage"] > hi["damage"])
    # Under the proc-threshold status model the R1 string's bleed stops growing once every
    # defender group procs: 464 HP at ARC 14, 503 at ARC 38 and still 503 at ARC 77. Past that
    # point each arcane level is a DEX level lost, so the best spread no longer runs arcane past
    # 30 (the expectation of the linear model this test was written against).
    sat = [sc.parts({"str": 14, "dex": 40, "int": 9, "fth": 8, "arc": a})["status"]["bleed"] for a in (38, 60)]
    check(f"Blood Uchigatana: bleed saturates by ARC 38 (ARC 38 {sat[0]}, ARC 60 {sat[1]})", sat[0] == sat[1])
    st, points = start_spread(sc, "Bandit", 150, 56, 41)
    r = build(sc, "Bandit", 150, 56, 41)
    arc = r["stats"]["arc"]
    check(f"Blood Uchigatana Bandit spends ARC past the class's {st['arc']} and short of the plateau (got {arc})",
          st["arc"] < arc < 38)
    sc.floor = dict(st)
    greedy = sc.score(OPT.spend(dict(st), points, sc, OPT.DAMAGE_STATS)[0])
    check(f"Bandit build scores at least the plain greedy spend ({r['score']} vs {greedy:.1f})",
          r["score"] >= round(greedy, 1))
    return 0 if ok else 1


def wield_floors(e, name: str, affinity: str = "Standard") -> dict:
    """One-handed requirements of `name` at `affinity`: the stats a build needs to also carry it."""
    wid = e.art.find_weapon(name, affinity)
    base = wid - wid % 10000
    return OPT.requirements(e.art, base, affinity, e.art.max_level(e.art.weapons[wid]["reinforceTypeId"]), False)


def compare(spec: str, also: list[str], rl: int, vig: int, end: int) -> int:
    """Per class, `spec` (WEAPON:AFFINITY:GRIP) scored free and with every `also` weapon's
    requirements as floors."""
    name, affinity, grip = spec.split(":")
    e = embed()
    wid = e.art.find_weapon(name, affinity)
    sc = StringScorer(e, wid - wid % 10000, affinity, grip)
    floors: dict = {}
    for a in also:
        n, _, aff = a.partition(":")
        for k, v in wield_floors(e, n, aff or "Standard").items():
            floors[k] = max(floors.get(k, 0), v)
    print(f"{spec} at RL {rl} (VIG {vig}, END {end}); floors from {', '.join(also)}: {floors}")
    rows = []
    for cls in RES.CLASS_ROWS:
        free, held = build(sc, cls, rl, vig, end), build(sc, cls, rl, vig, end, floors)
        if free and held:
            rows.append((cls, free, held))
    best_free = max(r[1]["score"] for r in rows)
    for cls, free, held in sorted(rows, key=lambda r: -r[2]["score"]):
        st = held["stats"]
        print(f"  {cls:13s} free {free['score']:6.1f}  with floors {held['score']:6.1f} "
              f"({held['score'] / best_free - 1:+.2%} vs best free)  "
              + " ".join(f"{k.upper()} {st[k]}" for k in OPT.STATS) + f"  unspent {held['unspent']}")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(selftest())
    import argparse
    ap = argparse.ArgumentParser(description="Per-class score of one weapon, free and with other weapons' requirements as floors.")
    ap.add_argument("weapon", help="WEAPON:AFFINITY:GRIP, e.g. 'Warped Axe:Blood:2H'")
    ap.add_argument("--also", action="append", default=[], metavar="WEAPON[:AFFINITY]")
    ap.add_argument("--rl", type=int, default=150)
    ap.add_argument("--vig", type=int, default=56)
    ap.add_argument("--end", type=int, default=41)
    a = ap.parse_args()
    sys.exit(compare(a.weapon, a.also, a.rl, a.vig, a.end))
