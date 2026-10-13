#!/usr/bin/env python3
"""Which way to hold each weapon: one-handed, two-handed, powerstanced, or as another weapon's
off-hand, compared on the moveset `er-mechanics-weapon-embed.py` measured.

Every figure is read off the embedding's slots (real frames at 30/s, PvP damage against the
embedding's defender). Per mode:

- `open`: the first attack (`r1_1`, `dual_1`, `left_1`): startup, damage, damage per second to its
  roll cancel;
- `chain`: the whole light string, damage per second from the first startup to the last hit's
  roll cancel, each link at its `next_cancel`;
- `crouch`: `crouch_r1` / `dual_crouch` the same way as `open`.

A powerstance is the weapon with a second copy of itself (`dual_*`, both hands); `--partners`
also ranks every other left weapon the embedding paired it with. An off-hand row is the weapon
held left (`left_*`, the `L1` grip). Usage:

    python3 scripts/er-mechanics-grip-modes.py embed.json [--weapon name ...] [--class name ...]
        [--roles roles.json] [--partners] [--json out.json]
"""
import argparse
import json
import sys
from pathlib import Path

FPS = 30.0


def chain(slots, prefix):
    """(damage per second, frames, damage) of the string `prefix`1, 2, ... to its roll cancel."""
    keys = [k for k in sorted(slots) if k.startswith(prefix) and k[len(prefix):].isdigit()]
    keys.sort(key=lambda k: int(k[len(prefix):]))
    links = [slots[k] for k in keys if slots[k].get("damage") is not None]
    if not links:
        return None
    t = sum(s["next_cancel"] for s in links[:-1]) + links[-1]["roll_cancel"]
    d = sum(s["damage"] for s in links)
    return round(d * FPS / t), t, round(d)


def single(s):
    """(startup, damage, damage per second to roll cancel, hits, reach) of one slot."""
    if not s or s.get("damage") is None or not s.get("roll_cancel"):
        return None
    return (s["startup"], round(s["damage"]), round(s["damage"] * FPS / s["roll_cancel"]), s.get("hits"),
            s.get("reach"))


def modes(e, weapon, affinity):
    out = {}
    for x in e["weapons"]:
        if x["weapon"] != weapon or x["affinity"] != affinity or not x.get("wieldable", True):
            continue
        sl = x["slots"]
        if x["grip"] in ("1H", "2H"):
            out[x["grip"]] = {"open": single(sl.get("r1_1")), "chain": chain(sl, "r1_"),
                              "crouch": single(sl.get("crouch_r1"))}
        elif x["grip"] == "L1":
            out["L1"] = {"open": single(sl.get("left_1")), "chain": chain(sl, "left_"), "crouch": None}
    p = next((p for p in e["powerstance"] if p["right"] == weapon and p["left"] == weapon
              and p["right_affinity"] == affinity == p["left_affinity"]), None)
    if p:
        sl = p["slots"]
        out["PS"] = {"open": single(sl.get("dual_1")), "chain": chain(sl, "dual_"),
                     "crouch": single(dual_crouch(sl))}
    return out


def ps_affinities(e, weapon):
    """Affinities the embedding powerstanced `weapon` with a copy of itself on."""
    return sorted({p["right_affinity"] for p in e["powerstance"] if p["right"] == weapon
                   and p["left"] == weapon and p["right_affinity"] == p["left_affinity"]})


def dual_crouch(slots):
    """The crouch L1: its own clip, or the rolling L1 an embedding made before
    `er-mechanics-powerstance-guard.powerstance_rows` counted the fallback left out (every
    category outside `DUAL_STEALTH_ATTACK_CATEGORIES` has no 034310 and plays 034300)."""
    return slots.get("dual_crouch") or slots.get("dual_roll")


def partners(e, weapon, affinity):
    """[(left weapon, chain dps, open dps, crouch dps)] for every powerstance with `weapon` right."""
    rows = []
    for p in e["powerstance"]:
        if p["right"] == weapon and p["right_affinity"] == affinity:
            sl = p["slots"]
            c, o, cr = chain(sl, "dual_"), single(sl.get("dual_1")), single(dual_crouch(sl))
            rows.append((p["left"], c and c[0], o and o[2], cr and cr[2]))
    return sorted(rows, key=lambda r: -(r[1] or 0))


def best_mode(m, key):
    got = {k: (v[key][0] if key == "chain" else v[key][2]) for k, v in m.items() if v.get(key)}
    return max(got, key=got.get) if got else None


def fmt(v, key):
    if not v:
        return "-"
    if key == "chain":
        return f"{v[0]}/s ({v[2]} in {v[1]}f)"
    return f"f{v[0]} {v[1]} ({v[2]}/s, {v[3]} hit{'s' if v[3] != 1 else ''})"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("embed", type=Path)
    ap.add_argument("--weapon", action="append", default=[])
    ap.add_argument("--class", dest="classes", action="append", default=[])
    ap.add_argument("--roles", type=Path, help="class-roles JSON: each weapon on its chosen affinity")
    ap.add_argument("--affinity", default="Heavy", help="fallback when --roles does not name one")
    ap.add_argument("--partners", action="store_true")
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    e = json.loads(a.embed.read_text())
    roles = json.loads(a.roles.read_text())["weapons"] if a.roles else {}
    affs = {}
    for x in e["weapons"]:
        affs.setdefault(x["weapon"], set()).add(x["affinity"])
    names = list(a.weapon) or sorted({x["weapon"] for x in e["weapons"]
                                      if not a.classes or x["class"] in a.classes})
    report = {}
    for w in names:
        if w not in affs:
            print(f"{w}: not in the embedding", file=sys.stderr)
            continue
        aff = (roles.get(w) or {}).get("affinity")
        if aff not in affs[w]:
            aff = a.affinity if a.affinity in affs[w] else sorted(affs[w])[0]
        # Every mode of a block is on one affinity. A weapon the embedding powerstanced only on
        # other affinities also gets a block on the first of those, so the powerstance is compared
        # with the one- and two-handed grips of the same affinity rather than another's.
        pas = [p for p in ps_affinities(e, w) if p in affs[w]]
        report[w] = []
        for b in [aff] + ([pas[0]] if pas and aff not in pas else []):
            m = modes(e, w, b)
            best = {k: best_mode(m, k) for k in ("open", "chain", "crouch")}
            report[w].append({"affinity": b, "modes": m, "best": best})
            print(f"{w} ({b}): best chain {best['chain']}, opener {best['open']}, crouch {best['crouch']}"
                  + ("" if "PS" in m else f"; powerstance measured only on {', '.join(pas) or 'none'}"))
            for k, v in m.items():
                print(f"  {k:3} chain {fmt(v['chain'], 'chain'):24} open {fmt(v['open'], 'open'):28} "
                      f"crouch {fmt(v['crouch'], 'open')}")
            if a.partners:
                ps = partners(e, w, b)
                report[w][-1]["partners"] = ps
                for left, c, o, cr in ps[:5]:
                    print(f"    PS with {left}: chain {c}/s, opener {o}/s, crouch {cr}/s")
    if a.json:
        a.json.write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
