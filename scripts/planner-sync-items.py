#!/usr/bin/env python3
"""Copy part of one build onto every other build on your planner account.

    python3 scripts/planner-sync-items.py <reference build id> [--talisman-order | --armor] [--apply]

By default it copies the Items page: `items.tools` (quick items and pouch, with their positions),
`items.flasks` and `items.crystalTears`; ammunition, weapons, armor, talismans and spells are left
alone. `--talisman-order` instead puts each build's own talisman inventory in the reference's order:
no talisman is added or removed and every slot keeps its equip positions. The k-th copy of a name
takes the place of the reference's k-th copy, extra copies follow the reference's last one, and
talismans the reference does not hold go last, in the order the build already had them.
`--talisman-fill` first adds the reference's talismans a build holds fewer copies of, unequipped,
then orders them the same way. `--talisman-exact` makes the talisman inventory the reference's,
dropping every other talisman except the build's equipped ones: an equipped talisman takes the
equip slot onto the reference's first copy of its name, or stays at the end when the reference
holds none.
`--armor` replaces the armor inventory and what is equipped (`protectors` and `sets.protectors`)
with the reference's, whatever that does to equip load.

    python3 scripts/planner-sync-items.py --great-rune "Morgott's Great Rune" [--apply]

sets that great rune on every build that has none, with no reference build. `--strip-level-tag`
(no reference either) drops a level in parentheses from each name: `Treeman (RL125)` -> `Treeman`.

The account is the one in `~/.config/er-mods-rs/planner-login-code` (see er-npc-planner-link.py).
Builds named `<NPC> (<param id>)` -- the NPC exports -- are skipped. Without `--apply` it only lists
what would change.
"""
from __future__ import annotations

import collections
import importlib.util
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("er_npc_planner_link", HERE / "er-npc-planner-link.py")
link = importlib.util.module_from_spec(spec)
spec.loader.exec_module(link)
upload = link.upload

SERVER_FIELDS = ("id", "author", "views", "revision", "computed")
NPC_EXPORT = re.compile(r"\(\d{4,}\)$")
#: ` (RL90)`, ` (Lvl 125)`, ` (150)`: a level in parentheses, with the space before it.
LEVEL_TAG = re.compile(r"\s*\((?:[A-Za-z]{0,5}[\s.:-]*)?\d+\)")
ITEM_KEYS = ("tools", "flasks", "crystalTears")
LOGIN_CODE = pathlib.Path("~/.config/er-mods-rs/planner-login-code")


def items_view(doc: dict) -> dict:
    items = doc.get("items") or {}
    return {k: items.get(k) for k in ITEM_KEYS}


def copy_items(doc: dict, ref: dict) -> str:
    old = (doc.get("items") or {}).get("crystalTears")
    doc.setdefault("items", {}).update(json.loads(json.dumps(items_view(ref))))
    return f"tears {old}"


def talisman_view(doc: dict) -> list:
    t = doc.get("talismans") or {}
    return [t.get("sorting"), [(s["name"], s.get("equipSet"), s.get("equipIndex")) for s in t.get("slots") or []]]


def copy_talisman_order(doc: dict, ref: dict) -> str:
    rank, seen = {}, {}
    for i, s in enumerate(ref["talismans"]["slots"]):
        k = seen[s["name"]] = seen.get(s["name"], 0) + 1
        rank[(s["name"], k)] = i
    talismans = doc.setdefault("talismans", {})
    slots, seen = talismans.get("slots") or [], {}
    keyed = []
    for i, s in enumerate(slots):
        k = seen[s["name"]] = seen.get(s["name"], 0) + 1
        held = sum(1 for n, _ in rank if n == s["name"])
        # A copy past the reference's count sits right after the reference's last copy of it.
        keyed.append(((rank[(s["name"], min(k, held))], k) if held else (len(rank) + i, 0), s))
    keyed.sort(key=lambda p: p[0])
    talismans["slots"] = [{**s, "order": i} for i, (_, s) in enumerate(keyed)]
    talismans["sorting"] = dict(ref["talismans"]["sorting"])
    extra = [s["name"] for r, s in keyed if r[0] >= len(rank)]
    return f"{len(slots)} talismans" + (f", not in reference (kept last): {extra}" if extra else "")


def fill_talismans(doc: dict, ref: dict) -> str:
    slots = doc.setdefault("talismans", {}).setdefault("slots", [])
    held = collections.Counter(s["name"] for s in slots)
    need = collections.Counter(s["name"] for s in ref["talismans"]["slots"]) - held
    slots += [{"name": n} for n, k in need.items() for _ in range(k)]
    return f"+{sum(need.values())}, " + copy_talisman_order(doc, ref)


EQUIP_KEYS = ("equipSet", "equipIndex")


def exact_talismans(doc: dict, ref: dict) -> str:
    talismans = doc.setdefault("talismans", {})
    old = talismans.get("slots") or []
    slots = [{"name": s["name"]} for s in ref["talismans"]["slots"]]
    kept = []
    for s in old:
        equip = {k: s[k] for k in EQUIP_KEYS if k in s}
        if not equip:
            continue
        free = next((t for t in slots if t["name"] == s["name"] and not any(k in t for k in EQUIP_KEYS)), None)
        if free is None:
            slots.append({"name": s["name"], **equip})
            kept.append(s["name"])
        else:
            free.update(equip)
    talismans["slots"] = [{**s, "order": i} for i, s in enumerate(slots)]
    talismans["sorting"] = dict(ref["talismans"]["sorting"])
    return f"{len(old)} -> {len(slots)} talismans" + (f", equipped and kept: {kept}" if kept else "")


def armor_view(doc: dict) -> list:
    return [doc.get("protectors"), (doc.get("sets") or {}).get("protectors")]


def copy_armor(doc: dict, ref: dict) -> str:
    sets = (doc.get("sets") or {}).get("protectors") or []
    doc["protectors"] = json.loads(json.dumps(ref["protectors"]))
    doc.setdefault("sets", {})["protectors"] = json.loads(json.dumps(ref["sets"]["protectors"]))
    return f"{len(sets)} armor set(s) before"


def great_rune_view(doc: dict) -> object:
    return doc.get("greatRune")


MODES = {"--talisman-order": (talisman_view, copy_talisman_order), "--armor": (armor_view, copy_armor),
         "--talisman-fill": (talisman_view, fill_talismans), "--talisman-exact": (talisman_view, exact_talismans)}


def main() -> int:
    args = sys.argv[1:]
    apply = "--apply" in args
    if "--great-rune" in args:
        rune = args[args.index("--great-rune") + 1]
        ref_id, ref = None, None

        def fill(doc: dict, _ref) -> str:
            old = doc.get("greatRune")
            if not old:
                doc["greatRune"] = rune
            return f"was {old!r}"
        view, copy = great_rune_view, fill
    elif "--strip-level-tag" in args:
        ref_id, ref = None, None

        def strip(doc: dict, _ref) -> str:
            old = doc.get("name") or ""
            doc["name"] = LEVEL_TAG.sub("", old)
            return f"was {old!r}"
        view, copy = (lambda doc: doc.get("name")), strip
    elif not args or args[0].startswith("-"):
        sys.exit(__doc__)
    else:
        ref_id = args[0]
        view, copy = next((MODES[a] for a in args if a in MODES), (items_view, copy_items))
    session = link.load_session(LOGIN_CODE)
    if ref_id:
        ref = upload.request(f"{upload.API}/{ref_id}")
    builds = [b for b in link.account_builds(session)
              if b["id"] != ref_id and not NPC_EXPORT.search(b["data"].get("name", ""))]
    print(f"{len(builds)} builds" + (f", reference {ref.get('name')!r}" if ref else ""))
    changed = same = failed = 0
    for b in builds:
        try:
            doc = upload.request(f"{upload.API}/{b['id']}")
        except SystemExit as e:  # deleted since the listing; `request` exits on an HTTP error
            print(f"  gone     {b['id']} {b['data'].get('name')!r} {e}")
            continue
        before = view(doc)
        note = copy(doc, ref)
        want = view(doc)
        if want == before:
            same += 1
            print(f"  same     {b['id']} {doc.get('name')!r}")
            continue
        for k in SERVER_FIELDS:
            doc.pop(k, None)
        if not apply:
            changed += 1
            print(f"  would    {b['id']} {doc.get('name')!r} {note}")
            continue
        stored = upload.request(upload.API, {"id": b["id"], "data": doc, "gameId": 1, "version": doc.get("version")},
                                link.headers(session))
        back = upload.request(f"{upload.API}/{b['id']}")
        ok = stored.get("id") == b["id"] and view(back) == want
        changed += ok
        failed += not ok
        print(f"  {'updated' if ok else 'FAILED '}  {b['id']} {back.get('name')!r} {note}")
    print(f"changed {changed}, already the same {same}, failed {failed}{'' if apply else ' (dry run)'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
