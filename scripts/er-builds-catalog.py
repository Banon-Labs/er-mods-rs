#!/usr/bin/env python3
"""Join er-build-planner item names to the game's param row ids, offline.

    python3 scripts/er-builds-catalog.py            # write the catalog, report the corpus join
    python3 scripts/er-builds-catalog.py --misses   # also list every name that did not join

The planner names items and carries no ids; the optimizer needs ids to read requirements,
weights and scaling out of the regulation. The names come from the game's own message files
through `scripts/er-item-name.py` (base, then dlc01, then dlc02, the order the game tries), and are
folded the way `er-build-import-core/src/name.rs` folds them: accents stripped, case and
whitespace ignored.

Names are scoped by kind, as the importer's catalog is, because Elden Ring reuses them across
categories (`Golden Vow` is a spell, an ash of war and a consumable).

| kind | message file | row id is |
| --- | --- | --- |
| armament | `WeaponName` | the `EquipParamWeapon` row with no affinity and no upgrade (id % 10000 == 0) |
| armor | `ProtectorName` | `EquipParamProtector` |
| talisman | `AccessoryName` | `EquipParamAccessory` |
| ash of war | `GemName` | `EquipParamGem` |
| spell, crystal tear, great rune | `GoodsName` | `EquipParamGoods`; a spell's row is also its `Magic` row |

When one folded name maps to several rows the lowest id wins, and the count of such names is
reported rather than hidden. For great runes that is the importer's own rule too: only goods rows
191..196 are the runes the game equips.

Output: `~/.cache/er-build-planner/catalog.json`, `{kind: {folded name: row id}}`.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import unicodedata
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = Path.home() / ".cache/er-build-planner"


def _sibling(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ITEM_NAME = _sibling("er-item-name")
EMBED = _sibling("er-builds-embed")

# token prefix (see er-builds-embed.py) -> (catalog kind, message file stem)
KINDS = {
    "w": ("armament", "WeaponName"),
    "a": ("armor", "ProtectorName"),
    "t": ("talisman", "AccessoryName"),
    "aow": ("ash of war", "GemName"),
    "s": ("spell", "GoodsName"),
    "ct": ("crystal tear", "GoodsName"),
    "gr": ("great rune", "GoodsName"),
}
GREAT_RUNE_IDS = range(191, 197)
ASH_PREFIX = "Ash of War: "


def fold(name: str) -> str:
    """ASCII, case-insensitive, whitespace-normalised; typographic quotes and dashes flattened.
    NFKD then dropping combining marks covers the Latin-1 table `name.rs` spells out by hand."""
    s = name.replace("‘", "'").replace("’", "'").replace("–", "-").replace("—", "-")
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = s.replace("æ", "ae").replace("Æ", "ae").replace("œ", "oe").replace("ß", "ss")
    return " ".join(s.lower().split())


def usable(text) -> bool:
    return bool(text) and not text.startswith("?") and not text.startswith("[ERROR]")


def build_catalog(corpus_root: str) -> tuple[dict, Counter]:
    fmgs: dict[str, dict] = {}
    cat: dict[str, dict[str, int]] = {}
    collisions: Counter = Counter()
    for kind, stem in KINDS.values():
        if stem not in fmgs:
            # One table per bundle, base first; the first bundle that names a row is the one the
            # game's getter would have answered from.
            merged: dict[int, str] = {}
            for _, table in ITEM_NAME.load(corpus_root, stem):
                for rid, text in table.items():
                    if usable(text):
                        merged.setdefault(rid, text)
            if not merged:
                raise SystemExit(f"no {stem} message file under {corpus_root}")
            fmgs[stem] = merged
        table: dict[str, int] = {}
        for rid, text in sorted(fmgs[stem].items()):
            if not usable(text):
                continue
            if kind == "armament" and rid % 10000:
                continue
            if kind == "great rune" and rid not in GREAT_RUNE_IDS:
                continue
            # The game names every gem "Ash of War: <skill>"; the planner names the skill.
            if kind == "ash of war" and text.startswith(ASH_PREFIX):
                text = text[len(ASH_PREFIX):]
            key = fold(text)
            if key in table:
                collisions[kind] += 1
                continue
            table[key] = rid
        cat[kind] = table
    return cat, collisions


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", default=ITEM_NAME.DEFAULT_CORPUS, help="message-file extraction root")
    ap.add_argument("--mirror", type=Path, default=CACHE / "builds.jsonl")
    ap.add_argument("--out", type=Path, default=CACHE / "catalog.json")
    ap.add_argument("--misses", action="store_true")
    a = ap.parse_args()

    cat, collisions = build_catalog(a.corpus)
    a.out.write_text(json.dumps(cat, indent=1, sort_keys=True))
    print(f"wrote {a.out}: " + ", ".join(f"{k} {len(v)}" for k, v in cat.items()))
    if collisions:
        print("names shared by several rows (lowest id kept): " +
              ", ".join(f"{k} {v}" for k, v in collisions.items()))

    corpus, _ = EMBED.load_corpus(a.mirror, 125, 169)
    seen: Counter = Counter()
    hit: Counter = Counter()
    missed: dict[str, Counter] = {}
    for b in corpus:
        for tok in b["tokens"]:
            prefix, name = tok.split(":", 1)
            if prefix not in KINDS:
                continue
            kind = KINDS[prefix][0]
            seen[kind] += 1
            if fold(name) in cat[kind]:
                hit[kind] += 1
            else:
                missed.setdefault(kind, Counter())[name] += 1
    print("corpus join, RL 125-169 (item uses joined / item uses):")
    for kind in seen:
        distinct = len(missed.get(kind, {}))
        print(f"  {kind:<13} {hit[kind]:>6}/{seen[kind]:<6} {hit[kind] / seen[kind]:6.1%}   distinct misses {distinct}")
        if a.misses and distinct:
            for name, n in missed[kind].most_common():
                print(f"      {n:>4}  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
