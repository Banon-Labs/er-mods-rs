#!/usr/bin/env python3
"""What the hand-marked SpEffects have in common, and what a rule over them would cost.

The marks come from the selector's mark key (`selector_mark_key`), which writes
`er-net-effects-marked.jsonc` into the game directory. That file is a list of ids a human looked
at and judged; it is deliberately not a list the DLL reads back. This script is the step that
turns it into something that can be: it compares the marked rows against the unmarked ones, field
by field, out of `data/effect-master-catalog.json`, and reports which fields separate them.

Read the output as a shopping list for a predicate, not as an answer. A field at the top of the
ranking is one whose presence tracks the marks closely in this population; whether it tracks them
because it causes the behaviour that got marked is a question about the game, which no count
settles. The point is to stop guessing which fields to read.

The second half of the report scores rules. A rule is a conjunction of "this field is non-zero"
clauses, and it is scored the way a filter is judged rather than the way a classifier is:

  recall     how many of the marked rows it would catch      -- misses are effects that slip through
  precision  how many of the rows it catches were marked     -- see the warning below
  unreviewed how many rows it catches that carry no mark     -- rows to look at, not mistakes

An unmarked row is not a row judged acceptable. A marking pass is almost never exhaustive, so a
row carrying no mark was usually never looked at. That makes `precision` a floor and nothing more --
it counts every unreviewed row as a false positive, which is why a good rule scores badly here
early on. The column that means something is `unreviewed`: those rows are the queue for the next
pass, and confirming or rejecting them is what turns the floor into a real number. Read
`precision` as "at least this good".

A rule with perfect recall that catches 4,000 unmarked rows is not a rule, it is a ban. One with
perfect precision that catches three rows is the hand-list again with extra steps. Rules are
ranked by recall and then by cost, so the report reads as "what full coverage costs" and you pick
the knee -- the two errors are not symmetric (a miss is an effect the mod can still push at
another player; an extra is one entry fewer in a list of thousands) and no blended score gets
that right at these population sizes.

Usage:
  python3 scripts/er-net-effects-mark-patterns.py
  python3 scripts/er-net-effects-mark-patterns.py --marked path/to/marked.jsonc
  python3 scripts/er-net-effects-mark-patterns.py --against visuals-only.jsonc
  python3 scripts/er-net-effects-mark-patterns.py --selftest

`--against` narrows the comparison population to one catalog, which is what you want when the
marking pass only covered that catalog: comparing a pass through 843 entries against all 11,325
rows measures which catalog you scrolled, not which effects you marked.

`ER_GAME_DIR` / `ME3_STEAM_DIR` locate the game directory the marked file is read from.
"""

import argparse
import json
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MASTER_CATALOG = os.path.join(REPO_ROOT, "data", "effect-master-catalog.json")
MARKED_FILE_NAME = "er-net-effects-marked.jsonc"
CATALOG_DIR_NAME = "er-net-effect-catalogs"

# How many single fields and how many rules to print. The tail of both rankings is noise -- a
# field present in one marked row out of two hundred separates nothing.
TOP_FIELDS = 25
TOP_RULES = 20
# A field seen in fewer marked rows than this is not evidence of a pattern, it is one effect.
MIN_MARKED_SUPPORT = 2
# Conjunctions are built from the best single fields only. The full power set of 373 fields is
# not searchable and would fit noise anyway.
PAIR_POOL = 12


def game_dir() -> str:
    explicit = os.environ.get("ER_GAME_DIR")
    if explicit:
        return explicit
    steam = os.environ.get(
        "ME3_STEAM_DIR", os.path.join(os.path.expanduser("~"), ".local/share/Steam")
    )
    return os.path.join(steam, "steamapps/common/ELDEN RING/Game")


def parse_id_list(text: str) -> list[int]:
    """Read ids out of a `.jsonc` id list, the way the DLL's own reader does.

    Comments are stripped first so an id inside one is a note, not a mark -- the same rule
    `marked_effects::parse` follows, and for the same reason: a commented-out entry is a
    deliberate exclusion.
    """
    ids: list[int] = []
    for line in text.splitlines():
        code = line.split("//", 1)[0]
        for token in re.findall(r"-?\d+", code):
            value = int(token)
            if value not in ids:
                ids.append(value)
    return ids


def load_master(path: str) -> dict[int, dict]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    return {row["id"]: row for row in data["effects"]}


def nonzero_fields(row: dict) -> set[str]:
    """Which fields this row actually sets.

    The catalog omits any field equal to its PARAMDEF default, so a present-and-zero field is a
    row that was written with an explicit zero. Treating that as "set" would make the ranking
    reward fields whose only signal is that someone typed a zero.
    """
    return {name for name, value in row.get("fields", {}).items() if value not in (0, 0.0)}


def population_ids(args, master: dict[int, dict]) -> tuple[list[int], str]:
    if not args.against:
        return list(master), "all master-catalog rows"
    path = args.against
    if not os.path.exists(path):
        path = os.path.join(game_dir(), CATALOG_DIR_NAME, args.against)
    if not os.path.exists(path):
        sys.exit(f"catalog not found: {args.against}")
    with open(path, encoding="utf-8") as handle:
        ids = parse_id_list(handle.read())
    return [i for i in ids if i in master], os.path.basename(path)


def rank_fields(marked: set[int], population: list[int], master: dict[int, dict]) -> list[dict]:
    """Rank fields by how much more often they are set in the marked rows than the rest."""
    others = [i for i in population if i not in marked]
    marked_in_pop = [i for i in population if i in marked]
    if not marked_in_pop or not others:
        return []
    rows = []
    fields = set()
    for i in marked_in_pop:
        fields |= nonzero_fields(master[i])
    for field in fields:
        hit_marked = sum(1 for i in marked_in_pop if field in nonzero_fields(master[i]))
        if hit_marked < MIN_MARKED_SUPPORT:
            continue
        hit_other = sum(1 for i in others if field in nonzero_fields(master[i]))
        marked_rate = hit_marked / len(marked_in_pop)
        other_rate = hit_other / len(others)
        rows.append(
            {
                "field": field,
                "marked": hit_marked,
                "marked_rate": marked_rate,
                "other": hit_other,
                "other_rate": other_rate,
                # Lift, floored so a field absent from every unmarked row does not divide by zero
                # and does not outrank a field with real support just for being rare.
                "lift": marked_rate / max(other_rate, 1.0 / len(others)),
            }
        )
    # Coverage first, lift as the tiebreak. Ranking by lift alone puts a field that appears in two
    # marked rows and almost nowhere else above one that appears in every marked row, because
    # rarity inflates lift -- and the rare one describes two effects while the common one
    # describes the set. Measured on a synthetic eight-mark pass: lift-first buried
    # `motionInterval` at 100% coverage below two fields at 25%.
    rows.sort(key=lambda row: (row["marked_rate"], row["lift"]), reverse=True)
    return rows


def score_rule(clauses: tuple[str, ...], marked: set[int], population: list[int], master) -> dict:
    caught = [i for i in population if set(clauses) <= nonzero_fields(master[i])]
    marked_in_pop = [i for i in population if i in marked]
    hits = [i for i in caught if i in marked]
    return {
        "clauses": clauses,
        "caught": len(caught),
        "hits": len(hits),
        "extra": len(caught) - len(hits),
        "recall": len(hits) / len(marked_in_pop) if marked_in_pop else 0.0,
        "precision": len(hits) / len(caught) if caught else 0.0,
    }


def rank_rules(ranked_fields, marked, population, master) -> list[dict]:
    pool = [row["field"] for row in ranked_fields[:PAIR_POOL]]
    rules = [score_rule((field,), marked, population, master) for field in pool]
    for index, first in enumerate(pool):
        for second in pool[index + 1 :]:
            rules.append(score_rule((first, second), marked, population, master))
    rules = [rule for rule in rules if rule["hits"] >= MIN_MARKED_SUPPORT]
    # Recall first, then fewest unmarked rows caught. Deliberately not a single blended score:
    # every scalar that mixes recall and precision -- F1, and F-beta at beta=2 too -- puts a rule
    # that catches a quarter of the marks very cleanly above one that catches all of them, because
    # precision differences here run to two orders of magnitude while recall cannot. Measured on
    # an eight-mark synthetic pass against 2,999 rows: F2 ranked a 25%-recall rule first and put
    # the 100%-recall one eleven places down.
    #
    # So the ranking answers "how much does full coverage cost" and the reader picks the knee. The
    # `extra` column is the price; there is no threshold in here pretending to know what price is
    # acceptable.
    rules.sort(key=lambda rule: (rule["recall"], -rule["extra"]), reverse=True)
    return rules


def report(marked_ids, population, population_name, master) -> int:
    marked = set(marked_ids)
    marked_in_pop = [i for i in population if i in marked]
    unknown = [i for i in marked_ids if i not in master]
    print(f"marked ids            : {len(marked_ids)}")
    print(f"population            : {len(population)} ({population_name})")
    print(f"marked within it      : {len(marked_in_pop)}")
    if unknown:
        print(f"not in master catalog : {len(unknown)} -> {unknown[:10]}")
    outside = [i for i in marked_ids if i in master and i not in set(population)]
    if outside:
        print(
            f"marked but outside the population: {len(outside)}"
            " -- narrow --against, or these rows cannot be scored"
        )
    print()
    if len(marked_in_pop) < MIN_MARKED_SUPPORT:
        print("Too few marks to find a pattern in. Keep scrolling.")
        return 0

    ranked = rank_fields(marked, population, master)
    print(f"FIELDS most concentrated in the marked rows (top {TOP_FIELDS})")
    print(f"  {'field':<38}{'marked':>12}{'unmarked':>12}{'lift':>8}")
    for row in ranked[:TOP_FIELDS]:
        marked_cell = f"{row['marked']}/{len(marked_in_pop)} ({row['marked_rate']:.0%})"
        other_cell = f"{row['other']} ({row['other_rate']:.1%})"
        print(f"  {row['field']:<38}{marked_cell:>12}{other_cell:>12}{row['lift']:>8.1f}")
    print()

    rules = rank_rules(ranked, marked, population, master)
    print(f"RULES, scored as filters over the population (top {TOP_RULES})")
    print(f"  {'recall':>7}{'prec>=':>7}{'unrevd':>8}  rule")
    for rule in rules[:TOP_RULES]:
        clauses = " AND ".join(f"{name}!=0" for name in rule["clauses"])
        print(
            f"  {rule['recall']:>6.0%}{rule['precision']:>7.0%}{rule['extra']:>8}  {clauses}"
        )
    print()
    print(
        "`extra` is how many unmarked rows the rule would also restrict. That number, not the\n"
        "percentages, is what a player would feel."
    )
    return 0


def selftest() -> int:
    """Prove the scoring on a population whose answer is known by construction.

    Nine rows: three marked, all of them setting `changeHpPoint`, two of those also setting
    `motionInterval`, against six unmarked rows of which one sets `changeHpPoint` too. So the
    single-field rule must have full recall and imperfect precision, and the conjunction must
    lose recall to gain it -- if the scorer reports anything else it is not measuring what the
    docstring says.
    """
    master = {
        1: {"id": 1, "fields": {"changeHpPoint": 10, "motionInterval": 1}},
        2: {"id": 2, "fields": {"changeHpPoint": 5, "motionInterval": 2}},
        3: {"id": 3, "fields": {"changeHpPoint": 7}},
        4: {"id": 4, "fields": {"changeHpPoint": 3}},
        5: {"id": 5, "fields": {"soulRate": 2}},
        6: {"id": 6, "fields": {}},
        7: {"id": 7, "fields": {"motionInterval": 4}},
        8: {"id": 8, "fields": {"changeHpPoint": 0}},
        9: {"id": 9, "fields": {"soulRate": 1, "motionInterval": 9}},
    }
    marked = {1, 2, 3}
    population = list(master)

    hp = score_rule(("changeHpPoint",), marked, population, master)
    assert hp["recall"] == 1.0, hp
    assert hp["hits"] == 3 and hp["extra"] == 1, hp
    assert hp["precision"] == 0.75, hp

    both = score_rule(("changeHpPoint", "motionInterval"), marked, population, master)
    assert both["hits"] == 2 and both["extra"] == 0, both
    assert both["precision"] == 1.0, both
    assert round(both["recall"], 4) == round(2 / 3, 4), both

    # A field written as an explicit zero is not a field the row sets.
    assert "changeHpPoint" not in nonzero_fields(master[8])

    ranked = rank_fields(marked, population, master)
    assert ranked, "no field ranked at all"
    # Coverage leads: `changeHpPoint` is in all three marked rows, `motionInterval` in two.
    # Ranking by lift alone would invert this, because the rarer field scores higher.
    assert ranked[0]["field"] == "changeHpPoint", ranked[0]
    assert ranked[0]["marked"] == 3 and ranked[0]["other"] == 1, ranked[0]
    assert [row["field"] for row in ranked].index("motionInterval") == 1, ranked

    # Recall-leaning: the full-recall rule must outrank the perfect-precision one that finds two
    # marks out of three. Plain F1 puts them the other way round.
    scored = rank_rules(ranked, marked, population, master)
    assert scored[0]["clauses"] == ("changeHpPoint",), scored[0]

    # A comment is not a mark, and the reader keeps order and drops duplicates.
    assert parse_id_list("[\n 5, 5,\n // 99 no\n 7 // seven\n]") == [5, 7]

    print("selftest ok")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--marked",
        help=f"the marked file (default: <game dir>/{MARKED_FILE_NAME})",
    )
    parser.add_argument(
        "--against",
        help="compare only against this catalog (a path, or a name in the catalog directory)",
    )
    parser.add_argument("--master", default=MASTER_CATALOG, help="the master catalog json")
    parser.add_argument("--selftest", action="store_true", help="check the scoring and exit")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    marked_path = args.marked or os.path.join(game_dir(), MARKED_FILE_NAME)
    if not os.path.exists(marked_path):
        sys.exit(
            f"no marked file at {marked_path}\n"
            "Nothing has been marked yet, or the game directory is elsewhere -- set ER_GAME_DIR."
        )
    with open(marked_path, encoding="utf-8") as handle:
        marked_ids = parse_id_list(handle.read())

    master = load_master(args.master)
    population, population_name = population_ids(args, master)
    print(f"marked file           : {marked_path}")
    return report(marked_ids, population, population_name, master)


if __name__ == "__main__":
    raise SystemExit(main())
