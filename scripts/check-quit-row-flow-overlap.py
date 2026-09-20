#!/usr/bin/env python3
"""Two DLLs that arm the Quit tab and offer the same flow must be declared a conflict.

The rule is read off the table rather than invented. Every `[[conflict]]` pair among the row
shells shares at least one `QuitRowActions` flow, and until 2026-09-19 four pairs that shared
one were missing -- which is how an installer came to offer `Quickload` and `Load Character
rows only` as a tickable combination.

Why a shared flow is the condition
----------------------------------
`row_registry::elect` fixed the row table in 2026-09-13: the second host to arm delegates
through the owner's `er_quit_rows_register` export and one merged table results. What it did
not fix, and cannot, is that each cdylib links its own copy of the core's statics.

Run br-20260917-205031-edd3 measured the consequence. The owner answered the product's
registration `open_profile_load_dialog=offered-but-already-held`, and the product's activate
hook then declined because `flow_active` is a latch in the product's copy, set only when the
product opens the picker -- the shell had opened it in its own. The row looked armed, the press
forwarded to the game, and the player got an in-world load instead of the save-safe switch. No
crash, no logged error, the wrong load.

So the hazard this gate looks for needs two hosts offering the same flow. Shells whose rows and
flows are disjoint do not trip it, and it must not demand that they be declared. Two such pairs
were declared in that table anyway, on a second mechanism this gate cannot see: both hosts derived
the same six-cell `02_040` Quit grid, and both installed the row-populate detours with a bare
`MhHook`. Both packages merged into `er-quit-menu` on 2026-09-20 and the pairs went with them, but
the lesson did not: do not read a silent pass here as a pair being co-loadable -- read the table.

What this catches that the conflict gate cannot
-----------------------------------------------
`check-me3-dll-conflicts.py` proves every shipped shell is classified. It cannot know that a
new pair became hazardous because someone added a `save_game_start_flow: Some(..)` to a shell
that did not have one. That is a source fact, so it is read from source here on every run.

Where the flow names come from
------------------------------
From `QuitRowActions` itself, parsed out of `er-quit-menu-core/src/row_cloner.rs` on every run.
They used to be a literal list in this file, and a hand-maintained copy of a type drifts: by
2026-09-19 that list carried three names no such field ever had (`save_game_request_slot`,
`open_build_url_import`, `generate_build_link` -- the last of them a `RowSet` boolean, not an
action) while missing two that shipped (`save_game_as_start_flow`, `save_game_request_save_only`).
A gate that cannot see a flow name cannot see a pair that shares it, so the list is derived
rather than restated, and a renamed or moved struct fails this loudly instead of quietly
matching nothing.

Why the scan follows `cfg`
--------------------------
Because the previous text grep did not, and that is the other half of the same 2026-09-19 error.
`er-save-game-row` spelled its flow two ways: a default build supplied `save_game_as_start_flow`
from a cloned row, and a `--features hijack-quit-row` build supplied `save_game_start_flow` by
taking the native first row over. The grep matched the second, which is the one no shipped
artifact contained -- that crate's `Cargo.toml` had `default = []`. It merged into `er-quit-menu`
on 2026-09-20, which picks between the same two spellings from its config file rather than from a
feature, so a `cfg`-aware read now sees both and the gate treats the shell as offering either.

So each crate's text is read as its shipped build: the default feature closure is taken from its
`Cargo.toml`, and any item behind a `#[cfg(..)]` that closure does not satisfy is removed before
the flow names are matched. `scripts/er-build-dlls.sh` passes neither `--features` nor
`--no-default-features`, so the default closure is exactly what every `.dll` in a profile was
built from. The alternative considered was to keep matching everything and label each hit with
the configuration it came from, which reports the difference without acting on it: that leaves
the gate still unable to say whether a pair collides in the build a player loads, which is the
only question it is here to answer.

A `cfg` predicate this file cannot evaluate is treated as enabled, so the code behind it is
still scanned. The bias is deliberate and matches `MIN_ARMING_SHELLS` below: over-reporting
costs a conflict row someone has to justify, while under-reporting passes the gate green with
the hazard still in the profile.

Usage:
    python3 scripts/check-quit-row-flow-overlap.py
    python3 scripts/check-quit-row-flow-overlap.py --selftest

Exit status is 1 on any failure, so this can gate.
"""

from __future__ import annotations

import argparse
import itertools
import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CRATES_DIR = REPO_ROOT / "crates"
CONFLICTS_TOML = REPO_ROOT / "scripts" / "me3-dll-conflicts.toml"

# The one definition of a row flow. Parsed, not copied -- see the docstring.
ROW_CLONER_RS = CRATES_DIR / "er-quit-menu-core" / "src" / "row_cloner.rs"
ACTIONS_STRUCT = "QuitRowActions"

# What makes a crate a row-arming host. A crate that only links `er-quit-menu-core` without
# arming -- er-input-harness does -- is not one, which is why this looks for the call.
#
# There are two entry points, and the first cut of this gate knew only one. It reported three
# arming shells when there were five: the standalone shells reach the row cloner through
# `arm::arm_standalone`, so they were invisible and so were the pairs they formed. A gate that
# under-detects passes while the thing it guards is broken, which is worse than not having it --
# hence `MIN_ARMING_SHELLS` below.
ARM_CALLS = (
    re.compile(r"\brow_cloner::arm\s*\("),
    re.compile(r"\barm::arm_standalone\s*\("),
)

# The two shells known to arm as of 2026-09-20. A scanner that finds fewer has stopped matching
# something rather than found a simpler workspace, and says so instead of passing. Raise this when
# a new row shell lands; lowering it is only correct alongside a deleted crate.
#
# It was five until 2026-09-20 and came down twice that day, both times with packages rather than
# with detection. Phase 2 merged `er-quit-load-character` and `er-save-game-row` into
# `er-quit-menu`; phase 3 deleted the `er-quit-rows` fork. What is left is the product and the
# merged shell, which is the end state `docs/plans/menus-and-saves-consolidation.md` is for -- so
# this number should not fall again, and a scanner reporting one has found a defect.
MIN_ARMING_SHELLS = 2

# Bare `cfg` idents this file decides for itself. Every shell here is a cdylib built for
# `x86_64-pc-windows-msvc`, so `windows` holds in each of them; `test` and `doc` never do in a
# shipped artifact. Anything absent from this table is assumed enabled, per the docstring.
BARE_CFG = {
    "windows": True,
    "unix": False,
    "test": False,
    "doc": False,
    "doctest": False,
    "miri": False,
}

# `key = "value"` predicates with a settled answer for these artifacts. Same bias applies to a
# key that is not here: assumed enabled rather than assumed away.
KEYED_CFG = {
    "target_os": "windows",
    "target_family": "windows",
    "target_env": "msvc",
    "target_arch": "x86_64",
}


class SourceScanError(RuntimeError):
    """The scanner lost its footing on the source, rather than found a clean workspace."""


# --- the flow names, taken from the type ------------------------------------------------


def flow_fields(source: str | None = None) -> tuple[str, ...]:
    """Every `Option` field of `QuitRowActions`, in declaration order.

    A field that is not an `Option` cannot be filled with `Some(..)`, so it is not a flow slot
    a host can claim and is not tracked.
    """
    if source is None:
        source = ROW_CLONER_RS.read_text(encoding="utf-8")
    match = re.search(
        rf"^pub struct {ACTIONS_STRUCT}\s*\{{(.*?)^\}}", source, re.S | re.M
    )
    if not match:
        raise SourceScanError(
            f"no `pub struct {ACTIONS_STRUCT}` in {ROW_CLONER_RS.relative_to(REPO_ROOT)}. "
            "The flow names are read off that type; a rename or a move has to be followed here "
            "rather than worked around, because a scanner that finds no fields matches no flows "
            "and passes every pair."
        )
    fields = tuple(re.findall(r"^\s*pub\s+(\w+)\s*:\s*Option\s*<", match.group(1), re.M))
    if not fields:
        raise SourceScanError(
            f"`{ACTIONS_STRUCT}` parsed but no `pub <name>: Option<..>` fields were found. "
            "Either the type stopped holding its flows in `Option`s, or this pattern stopped "
            "matching how they are written."
        )
    return fields


# --- reading a crate as the build that ships --------------------------------------------


def default_features(package: str) -> frozenset[str]:
    """The transitive closure of a crate's `default` feature.

    `dep:` entries and `other-crate/feature` entries enable something elsewhere, never a `cfg`
    of this crate, so they are skipped rather than added.
    """
    manifest = CRATES_DIR / package / "Cargo.toml"
    table = tomllib.loads(manifest.read_text(encoding="utf-8")).get("features", {})
    enabled: set[str] = set()
    pending = list(table.get("default", []))
    while pending:
        name = pending.pop()
        if name.startswith("dep:") or "/" in name or name in enabled:
            continue
        enabled.add(name)
        pending.extend(table.get(name, []))
    return frozenset(enabled)


def _skip_string(text: str, i: int) -> int:
    """Index just past the string literal starting at `i`, raw and byte forms included."""
    start = i
    if text[i] in "br":
        while i < len(text) and text[i] in "br":
            i += 1
    if i < len(text) and text[i] == "#":
        hashes = 0
        while i < len(text) and text[i] == "#":
            hashes += 1
            i += 1
        if i >= len(text) or text[i] != '"':
            return start + 1
        closing = '"' + "#" * hashes
        end = text.find(closing, i + 1)
        return len(text) if end < 0 else end + len(closing)
    if i >= len(text) or text[i] != '"':
        return start + 1
    raw = "r" in text[start:i]
    i += 1
    while i < len(text):
        if not raw and text[i] == "\\":
            i += 2
            continue
        if text[i] == '"':
            return i + 1
        i += 1
    return len(text)


def _skip_trivia(text: str, i: int) -> int | None:
    """Index just past a comment or string starting at `i`, or `None` if none starts there."""
    if text.startswith("//", i):
        end = text.find("\n", i)
        return len(text) if end < 0 else end + 1
    if text.startswith("/*", i):
        i += 2
        nest = 1
        while i < len(text) and nest:
            if text.startswith("/*", i):
                nest += 1
                i += 2
            elif text.startswith("*/", i):
                nest -= 1
                i += 2
            else:
                i += 1
        return i
    if text[i] == '"' or (
        text[i] in "br" and re.match(r'(?:b|r|br)#*"', text[i : i + 5])
    ):
        return _skip_string(text, i)
    if text[i] == "'":
        # A char literal, or a lifetime. `'a` is one character and then an identifier; a
        # literal closes on a second quote within a few characters.
        closing = text.find("'", i + 1)
        if closing > 0 and closing - i <= 4 and not re.match(r"'\w+\b(?!')", text[i:]):
            return closing + 1
        return i + 1
    return None


def _match_bracket(text: str, i: int) -> int:
    """Index just past the `]` matching the `[` at `i`."""
    depth = 0
    while i < len(text):
        skipped = _skip_trivia(text, i)
        if skipped is not None:
            i = skipped
            continue
        if text[i] in "([{":
            depth += 1
        elif text[i] in ")]}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise SourceScanError("unterminated attribute while scanning for a `cfg` predicate")


def _item_end(text: str, i: int) -> int:
    """Index just past the item an attribute at `i` applies to.

    Further attributes belong to the same item and are stepped over. The item then ends at the
    first `;` outside any bracket, or at the `}` closing its first block, whichever comes first.
    """
    while i < len(text):
        skipped = _skip_trivia(text, i)
        if skipped is not None:
            i = skipped
            continue
        if text[i].isspace():
            i += 1
            continue
        if text.startswith("#[", i) or text.startswith("#![", i):
            i = _match_bracket(text, text.index("[", i))
            continue
        break
    depth = 0
    while i < len(text):
        skipped = _skip_trivia(text, i)
        if skipped is not None:
            i = skipped
            continue
        char = text[i]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth <= 0 and char == "}":
                return i + 1
        elif char == ";" and depth == 0:
            return i + 1
        i += 1
    return len(text)


def _tokenize_predicate(predicate: str) -> list[str]:
    return re.findall(r'\w+|"[^"]*"|[(),=]', predicate)


def _eval_predicate(tokens: list[str], pos: int, features: frozenset[str]) -> tuple[bool, int]:
    if pos >= len(tokens):
        raise SourceScanError("truncated `cfg` predicate")
    head = tokens[pos]
    if head in ("all", "any", "not") and tokens[pos + 1 : pos + 2] == ["("]:
        pos += 2
        results: list[bool] = []
        while pos < len(tokens) and tokens[pos] != ")":
            value, pos = _eval_predicate(tokens, pos, features)
            results.append(value)
            if pos < len(tokens) and tokens[pos] == ",":
                pos += 1
        pos += 1
        if head == "all":
            return all(results), pos
        if head == "any":
            return any(results), pos
        return (not results[0]) if results else True, pos
    if tokens[pos + 1 : pos + 2] == ["="]:
        value = tokens[pos + 2].strip('"') if pos + 2 < len(tokens) else ""
        pos += 3
        if head == "feature":
            return value in features, pos
        if head in KEYED_CFG:
            return KEYED_CFG[head] == value, pos
        return True, pos
    return BARE_CFG.get(head, True), pos + 1


def cfg_enabled(predicate: str, features: frozenset[str]) -> bool:
    """Whether `#[cfg(<predicate>)]` holds for a shipped build with `features` on."""
    value, _ = _eval_predicate(_tokenize_predicate(predicate), 0, features)
    return value


def visible_source(text: str, features: frozenset[str]) -> str:
    """`text` with every item behind an unsatisfied `#[cfg(..)]` removed."""
    out: list[str] = []
    i = 0
    while True:
        start = text.find("#[cfg(", i)
        if start < 0:
            out.append(text[i:])
            return "".join(out)
        out.append(text[i:start])
        attr_end = _match_bracket(text, start + 1)
        predicate = text[start + len("#[cfg(") : attr_end - 2]
        if cfg_enabled(predicate, features):
            out.append(text[start:attr_end])
            i = attr_end
        else:
            i = _item_end(text, attr_end)


def match_flows(visible: str, fields: tuple[str, ...]) -> set[str]:
    """The flows filled in already-`cfg`-resolved text, matched as `<flow>: Some(`.

    A `None` placeholder does not count as offering one. The one definition of the match, so the
    live run and the selftest cannot drift apart about what filling a slot looks like.
    """
    return {field for field in fields if re.search(rf"\b{field}\s*:\s*Some\s*\(", visible)}


def flows_supplied(text: str, features: frozenset[str], fields: tuple[str, ...]) -> set[str]:
    """The flows a crate's build with `features` on fills. Resolves `cfg`, then matches."""
    return match_flows(visible_source(text, features), fields)


def crate_sources(package: str) -> list[Path]:
    return sorted((CRATES_DIR / package / "src").rglob("*.rs"))


def row_arming_shells(shipped: set[str], fields: tuple[str, ...]) -> dict[str, set[str]]:
    """package -> the flows it supplies, for every shipped shell that arms the Quit rows."""
    hosts: dict[str, set[str]] = {}
    for package in sorted(shipped):
        source_dir = CRATES_DIR / package / "src"
        if not source_dir.is_dir():
            continue
        text = "\n".join(
            path.read_text(encoding="utf-8", errors="replace") for path in crate_sources(package)
        )
        # Read once, as the build that ships. A crate that only arms behind a feature its
        # `default` leaves off is not a host of any artifact in a profile, and a `#[cfg(test)]`
        # fixture is not one either.
        visible = visible_source(text, default_features(package))
        if not any(pattern.search(visible) for pattern in ARM_CALLS):
            continue
        hosts[package] = match_flows(visible, fields)
    return hosts


def declared_pairs(table: dict) -> set[frozenset[str]]:
    return {frozenset((row["a"], row["b"])) for row in table.get("conflict", [])}


def check(hosts: dict[str, set[str]], declared: set[frozenset[str]]) -> list[str]:
    problems: list[str] = []
    for a, b in itertools.combinations(sorted(hosts), 2):
        shared = hosts[a] & hosts[b]
        pair = frozenset((a, b))
        if shared and pair not in declared:
            problems.append(
                f"{a} and {b} both arm the Quit rows and both supply "
                f"{', '.join(sorted(shared))}. Two hosts offering one flow is the shape measured "
                "on br-20260917-205031-edd3: the loser's registration comes back "
                "`offered-but-already-held` and its activate hook reads a latch the winner never "
                "set, so the row looks armed and silently does the wrong thing. Declare the pair "
                "in scripts/me3-dll-conflicts.toml, or make the flows disjoint."
            )
    return problems


# --- selftest ----------------------------------------------------------------------------

# An abridged `er-save-game-row/src/lib.rs`: one crate, one flow slot, two spellings of it
# behind opposite `cfg`s. This is the case the gate was blind to on 2026-09-19 -- it matched the
# `hijack-quit-row` spelling, which `default = []` means no shipped artifact contains, and
# missed both flows the default build actually supplies.
TWO_SPELLINGS_RS = """
#[cfg(all(windows, not(feature = "hijack-quit-row")))]
unsafe fn arm_rows() -> Result<(), ArmError> {
    unsafe {
        row_cloner::arm(
            RowSet { save_game_as: true, ..RowSet::NONE },
            QuitRowActions {
                // A cloned row, so the flow behind it is the `save_game_as` spelling.
                save_game_as_start_flow: Some(system_quit_save_game_start_flow),
                save_game_request_save_only: Some(system_quit_save_game_request_save_only),
                ..QuitRowActions::default()
            },
        )
    }
}

#[cfg(all(windows, feature = "hijack-quit-row"))]
unsafe fn arm_rows() -> Result<(), ArmError> {
    unsafe {
        row_cloner::arm(
            RowSet::NONE,
            QuitRowActions {
                save_game_start_flow: Some(system_quit_save_game_start_flow),
                save_game_request_save_only: Some(system_quit_save_game_request_save_only),
                ..QuitRowActions::default()
            },
        )
    }
}
"""

# A `None` placeholder, a format string carrying braces, and a comment carrying a semicolon --
# the three things a brace-counting scanner walks off the end of if it reads them as code. The
# trailing const is the complementary arm, so one of the two is always live.
PLACEHOLDER_RS = """
#[cfg(feature = "quit-rows")]
fn arm() {
    let armed = row_cloner::arm(RowSet::ALL, QuitRowActions {
        open_profile_load_dialog: Some(open_it),
        // The clone is dropped when the native takeover is supplied; see `arm`.
        save_game_as_start_flow: None,
        ..Default::default()
    });
    log(format_args!("armed: {armed:?} -- {{ not a block }}"));
}

#[cfg(not(feature = "quit-rows"))]
const SHAPE: &str = "the vanilla tab; nothing is cloned and no flow is registered";
"""


def selftest() -> int:
    failures = 0
    checks = 0

    def expect(held: bool, message: str) -> None:
        """Record one check. The count is tallied here so no total is maintained by hand."""
        nonlocal failures, checks
        checks += 1
        if not held:
            print(f"SELFTEST FAIL {message}")
            failures += 1

    cases: list[tuple[str, dict[str, set[str]], set[frozenset[str]], bool]] = [
        (
            "a shared flow with no declaration is caught",
            {"a": {"open_profile_load_dialog"}, "b": {"open_profile_load_dialog"}},
            set(),
            True,
        ),
        (
            "a shared flow that is declared is accepted",
            {"a": {"open_profile_load_dialog"}, "b": {"open_profile_load_dialog"}},
            {frozenset(("a", "b"))},
            False,
        ),
        (
            "disjoint flows need no declaration",
            {"a": {"open_profile_load_dialog"}, "b": {"save_game_start_flow"}},
            set(),
            False,
        ),
        (
            "a host supplying nothing collides with nobody",
            {"a": set(), "b": set()},
            set(),
            False,
        ),
        (
            "one undeclared pair among three hosts is caught",
            {
                "a": {"save_game_start_flow"},
                "b": {"save_game_start_flow"},
                "c": {"open_profile_load_dialog"},
            },
            set(),
            True,
        ),
    ]
    for name, hosts, declared, should_fail in cases:
        problems = check(hosts, declared)
        expect(bool(problems) == should_fail, f"{name}: problems={problems}")

    # The flow names are the type's, so the two names the old literal list was missing have to
    # be among them and the three it invented must not be.
    fields = flow_fields()
    for name in ("save_game_as_start_flow", "save_game_request_save_only"):
        expect(
            name in fields,
            f"`{name}` is a {ACTIONS_STRUCT} field and was not derived: {fields}",
        )
    for name in ("save_game_request_slot", "open_build_url_import", "generate_build_link"):
        expect(
            name not in fields,
            f"`{name}` is not a {ACTIONS_STRUCT} field and was derived anyway",
        )

    # A synthetic struct, so the derivation is proven against known input rather than against
    # whatever the crate happens to hold today.
    derived = flow_fields(
        "pub struct QuitRowActions {\n"
        "    /// doc\n"
        "    pub alpha: Option<unsafe fn(usize) -> bool>,\n"
        "    pub beta: Option<fn()>,\n"
        "    pub not_a_flow: bool,\n"
        "}\n"
    )
    expect(
        derived == ("alpha", "beta"),
        f"derivation took the wrong fields from a known struct: {derived}",
    )
    try:
        flow_fields("pub struct SomethingElse { pub alpha: Option<fn()> }\n")
        refused = False
    except SourceScanError:
        refused = True
    expect(refused, "a missing struct passed silently instead of failing the run")

    # The case the gate was blind to. Each build of one crate supplies what its own `cfg` arm
    # writes, and nothing from the arm that did not compile.
    default_build = flows_supplied(TWO_SPELLINGS_RS, frozenset(), fields)
    expect(
        default_build == {"save_game_as_start_flow", "save_game_request_save_only"},
        f"the default build's flows were read as {sorted(default_build)}",
    )
    hijack_build = flows_supplied(TWO_SPELLINGS_RS, frozenset({"hijack-quit-row"}), fields)
    expect(
        hijack_build == {"save_game_start_flow", "save_game_request_save_only"},
        f"the hijack-quit-row build's flows were read as {sorted(hijack_build)}",
    )
    expect(
        "save_game_start_flow" not in default_build,
        'a flow behind `feature = "hijack-quit-row"` was counted in a build whose '
        "`default = []` leaves it off",
    )

    # The overlap the blind spot hid, expressed as a verdict: a second host supplying the flow
    # this crate's default build really carries must be reported when the pair is undeclared.
    both_request_save = check(
        {"shell": default_build, "product": {"save_game_request_save_only"}},
        set(),
    )
    expect(
        bool(both_request_save),
        "an undeclared `save_game_request_save_only` overlap was not reported",
    )
    # ...and the flow only the unshipped build carries must not manufacture one.
    hijack_only = check(
        {"shell": default_build, "product": {"save_game_start_flow"}},
        set(),
    )
    expect(
        not hijack_only,
        "a pair was reported over `save_game_start_flow`, which no default build supplies",
    )

    # A `None` placeholder is not an offer, and a crate whose arming feature is off offers
    # nothing at all.
    placeholder = flows_supplied(PLACEHOLDER_RS, frozenset({"quit-rows"}), fields)
    expect(
        placeholder == {"open_profile_load_dialog"},
        f"the placeholder crate's flows were read as {sorted(placeholder)}",
    )
    expect(
        not flows_supplied(PLACEHOLDER_RS, frozenset(), fields),
        "a crate with its arming feature off was read as supplying a flow",
    )

    # Predicate evaluation, including the shapes these crates actually write.
    predicates = [
        ('feature = "on"', frozenset({"on"}), True),
        ('feature = "off"', frozenset(), False),
        ('not(feature = "off")', frozenset(), True),
        ('all(windows, not(feature = "off"))', frozenset(), True),
        ('all(windows, feature = "off")', frozenset(), False),
        ('all(feature = "a", not(feature = "b"))', frozenset({"a", "b"}), False),
        ('any(feature = "a", feature = "b")', frozenset({"b"}), True),
        ("windows", frozenset(), True),
        ("test", frozenset(), False),
        ('target_os = "windows"', frozenset(), True),
        ('target_os = "linux"', frozenset(), False),
        ("some_unknown_cfg", frozenset(), True),
    ]
    for predicate, features, expected in predicates:
        expect(
            cfg_enabled(predicate, features) == expected,
            f"`cfg({predicate})` with {sorted(features)} read as {not expected}",
        )

    # The default feature closure is transitive: er-quickload's `quit-rows` turns on
    # `save-game-row`, which is what decides between its two arming blocks.
    quickload = default_features("er-quickload")
    expect(
        {"quit-rows", "save-game-row"} <= quickload,
        f"er-quickload's default closure came back as {sorted(quickload)}",
    )
    # A crate with no `[features]` table at all reads as an empty closure, which is what the
    # `cfg` reader needs in order to strip a feature-gated block. `er-save-game-row` used to be
    # the example here and was deleted with the merge on 2026-09-20; `er-quit-menu` is the shell
    # that took its rows and declares no features of its own.
    expect(
        not default_features("er-quit-menu"),
        "er-quit-menu declares no features and its closure was read as non-empty",
    )

    if failures:
        print(f"selftest: {failures} of {checks} check(s) failed")
        return 1
    print(f"selftest: {checks} checks passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "me3_dll_list", REPO_ROOT / "scripts" / "me3-dll-list.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    shipped = {package for package, _artifact in module.dll_pairs()}

    try:
        fields = flow_fields()
        hosts = row_arming_shells(shipped, fields)
    except SourceScanError as error:
        print(f"quit-row flows: {error}", file=sys.stderr)
        return 1

    if len(hosts) < MIN_ARMING_SHELLS:
        print(
            f"found {len(hosts)} row-arming shell(s) ({', '.join(sorted(hosts)) or 'none'}) but "
            f"expected at least {MIN_ARMING_SHELLS}. Either a crate was deleted, or this scanner "
            "stopped matching how one of them arms -- and an under-detecting scanner passes while "
            "the pairs it cannot see go unclassified. Fix the patterns in ARM_CALLS, or lower "
            "MIN_ARMING_SHELLS alongside the deletion.",
            file=sys.stderr,
        )
        return 1

    table = tomllib.loads(CONFLICTS_TOML.read_text(encoding="utf-8"))
    problems = check(hosts, declared_pairs(table))
    if problems:
        print(f"{len(problems)} undeclared row-flow overlap(s):\n", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}\n", file=sys.stderr)
        return 1

    pairs = sum(1 for a, b in itertools.combinations(sorted(hosts), 2) if hosts[a] & hosts[b])
    print(
        f"quit-row flows: {len(fields)} flow slot(s) on {ACTIONS_STRUCT}, {len(hosts)} arming "
        f"shell(s) read as their default build, {pairs} pair(s) share a flow and all are "
        "declared conflicts."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
