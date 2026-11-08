#!/usr/bin/env python3
"""Equip load tier of every build on your planner account.

    python3 scripts/planner-equip-load.py [--all] [--md]

For each build: burden of the equipped weapons (active weapon set), armor and talismans over the
max load from Endurance and the worn load multipliers, through the same game model
er-builds-optimize.py uses (`er-mechanics-resources.Model`). Prints heavy and overloaded builds,
or every build with `--all`; `--md` prints the heavy and overloaded ones as a Markdown list of
planner links on stdout (the plain lines go to stderr). NPC exports (`<NPC> (<param id>)`) are skipped. The account is the
one in `~/.config/er-mods-rs/planner-login-code` (see er-npc-planner-link.py).
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _sibling(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


link = _sibling("er-npc-planner-link")
OPT = _sibling("er-builds-optimize")
RES, EMBED, upload = OPT.RES, OPT.EMBED, link.upload
NPC_EXPORT = re.compile(r"\(\d{4,}\)$")
LOGIN_CODE = pathlib.Path("~/.config/er-mods-rs/planner-login-code")


def load(doc: dict) -> dict:
    weapon_w, model, plain = OPT._weights()
    active = EMBED.active_set(doc, "weapons")
    weps, unknown = [], []
    for s in (doc.get("inventory") or {}).get("slots") or []:
        es = s.get("equipSet")
        pos = (es[active] if active < len(es) else None) if isinstance(es, list) else s.get("equipIndex")
        if pos is None or not s.get("name"):
            continue
        w = weapon_w.get(plain(s["name"]))
        if w is None:
            unknown.append(s["name"])
        else:
            weps.append(w)
    pa = EMBED.active_set(doc, "protectors")
    armor = [s["name"] for part in ("head", "body", "arms", "legs")
             for s in EMBED.equipped(((doc.get("protectors") or {}).get(part) or {}).get("slots"), pa)]
    tal = [s["name"] for s in EMBED.equipped((doc.get("talismans") or {}).get("slots"),
                                             EMBED.active_set(doc, "talismans"))]
    burden = model.burden(weps, armor, tal)
    stats = RES.from_planner(doc.get("stats") or {})
    res = model.resources(stats, tal, armor, doc.get("greatRune"))
    cap = res["max_equip_load"]
    unknown += [n for _, n in res["unknown_items"]]
    return {"burden": burden, "max": cap, "tier": RES.WEIGHT_TYPES[model.weight_type(burden, cap)],
            "end": res["effective_stats"]["end"], "unknown": unknown}


def main() -> int:
    every, md = "--all" in sys.argv[1:], "--md" in sys.argv[1:]
    session = link.load_session(LOGIN_CODE)
    builds = [b for b in link.account_builds(session) if not NPC_EXPORT.search(b["data"].get("name", ""))]
    tiers: dict = {}
    rows = []
    for b in builds:
        doc = upload.request(f"{upload.API}/{b['id']}")
        r = load(doc)
        tiers[r["tier"]] = tiers.get(r["tier"], 0) + 1
        if every or r["tier"] in ("heavy", "overloaded"):
            rows.append((r, b["id"], doc.get("name") or ""))
            note = f" (unknown: {r['unknown']})" if r["unknown"] else ""
            print(f"{r['tier']:10} {r['burden']:6.1f}/{r['max']:6.1f} {r['burden'] / r['max']:6.1%} "
                  f"END {r['end']:2} {b['id']} {doc.get('name')!r}{note}", file=sys.stderr if md else sys.stdout)
    summary = ", ".join(f"{k} {v}" for k, v in sorted(tiers.items()))
    if md:
        print_markdown(rows, summary)
    else:
        print(summary)
    return 0


def print_markdown(rows: list, summary: str) -> None:
    """The heavy and overloaded builds as planner links, heaviest first, one section per tier."""
    print("# Heavy and overloaded builds\n")
    print(f"Equip load of every build on the account ({summary}). Percent is burden over max load.\n")
    for tier in ("overloaded", "heavy"):
        hits = sorted((x for x in rows if x[0]["tier"] == tier), key=lambda x: -x[0]["burden"] / x[0]["max"])
        print(f"## {tier.capitalize()}\n")
        for r, bid, name in hits:
            label = name.replace("[", "\\[").replace("]", "\\]") or bid
            print(f"- [{label}](https://er-build-planner.nyasu.business/?b={bid}) — "
                  f"{r['burden'] / r['max']:.0%}, END {r['end']}")
        print()


if __name__ == "__main__":
    sys.exit(main())
