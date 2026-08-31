#!/usr/bin/env python3
"""Route hand-built `base + SOME_RVA` DATA addresses through the 1.17 data map.

WHY THIS IS A SEPARATE TOOL FROM THE CALL ONE
---------------------------------------------
`gate-stale-rva-calls.py` handles addresses that get EXECUTED, where a refusal has to become a
control-flow decision (return what, exactly?) and the risk is deleting behaviour. Data addresses
have no such problem: `er_game_base::mem::game_data_addr` returns `0` when the running build has
no verified mapping, and every one of these sites is already a read or an identity compare with an
existing "not the object I wanted" branch. Handing it `0` puts a refusal down the path the caller
already had.

The hazard being fixed is the QUIET one. A stale data address does not crash -- the reads are
fault-safe -- so the comparison simply never matches and the feature behind it stops working with
nothing said. Measured 2026-08-29: `TITLE_OWNER_VTABLE_RVA` is `CS::TitleStep` in 1.16.2 and not a
vtable at all in 1.17, and its three scans had been finding no title owner, forever, silently.

ONLY CONSTANTS ONE OF THE THREE MAPS ALREADY CARRIES are rewritten. A constant with no row would gain
nothing but noise: `game_data_addr` would return 0 where the raw value at least had a chance of
being right on some build. Getting the row is `map-data-rvas-1162-to-1170.py`'s job first.

REPORTING IS THE DEFAULT; REWRITING TAKES `--write`
---------------------------------------------------
Inverted 2026-08-30. The old default was to REWRITE, and `--dry-run` was the flag you had to
remember -- which put the destructive action one forgotten word away, on a tool whose whole job is
editing source in bulk across the workspace. It cost exactly what it was always going to cost: an
agent ran the bare command while investigating, silently rewrote
`crates/er-quickload/src/experiments/trace/native_result_map_hooks.rs`, and had to `git checkout`
it back out. Nobody asks a scanner for a report and gets edits; they do the reverse by accident.
`--dry-run` is still accepted and still means "do not write", so old invocations keep working.

USAGE
    python3 scripts/gate-stale-rva-data.py                # report only
    python3 scripts/gate-stale-rva-data.py --write        # actually rewrite
    python3 scripts/gate-stale-rva-data.py --selftest
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The resolver's table is fed by ALL THREE maps (see crates/er-game-base/build.rs), so "already
# mapped" has to mean the union. Scoring against the data map alone said 167 sites needed a new
# row; against the union it is 110, and 57 of the difference were free wins sitting in plain sight.
# NAME column per map, or None where the map has no constant column at all. `verified.tsv` has
# none -- its column 5 is a signature description -- and reading it as a name pulled junk into the
# "already mapped" set. Match by RVA there, which is the only key every map actually shares.
MAPS = (
    (os.path.join(REPO, "docs", "recon", "rva-map-1162-to-1170.data.tsv"), 2),
    (os.path.join(REPO, "docs", "recon", "rva-map-1162-to-1170.needed-verified.tsv"), 5),
    (os.path.join(REPO, "docs", "recon", "rva-map-1162-to-1170.verified.tsv"), None),
)
IMAGE_BASE = 0x140000000
# `const FOO_RVA: usize = 0x1234;` -- only the literal form; a constant defined from an enum
# discriminant has no value here and falls back to matching by NAME.
DECLARATION = re.compile(r"const\s+([A-Z0-9_]*RVA[A-Z0-9_]*)\s*:\s*usize\s*=\s*(0x[0-9a-fA-F_]+)")
RESOLVER = "er_game_base::mem::game_data_addr"

# `$base` included on purpose: several sites live inside macro bodies, and the dollar has to stay
# attached to the identifier rather than being swallowed into the replacement.
#
# THE BASE IS ANY LOWERCASE BINDING, not a fixed list of four spellings. The list used to be
# `(base|module_base|image_base|game_base)`, and `crates/er-loading-portrait-core/src/lookat_stage_camera.rs`
# writes `if let Ok(b) = game_module_base()` -- so `b + TITLE_CUSTOM_COVER_PROFILE_RENDERER_VTABLE_RVA`
# was invisible to this scanner and stayed on a 1.16.2 address through the whole 1.17 migration, with
# no refusal logged because a raw add never reaches the resolver. A scanner keyed on what the author
# happened to CALL the variable finds only the sites written by authors who agreed with it.
#
# What keys the match instead is the CONSTANT: `*_RVA*`, and only when `mapped_constants()` says the
# resolver can answer for it. Two deliberate restrictions remain on the base itself:
#   * it must start lowercase, so a SCREAMING_CASE constant added to an RVA (`SOME_OFFSET + FOO_RVA`)
#     is not mistaken for a module base;
#   * it must not be preceded by `.`, because rewriting `self.base + FOO_RVA` would drop the
#     receiver and produce `game_data_addr(base, ...)` -- which can still COMPILE where a local
#     `base` exists, and that is a silent wrong answer rather than a loud one.
SITE = re.compile(
    r"(?<![.\w])(?P<base>\$?[a-z_][A-Za-z0-9_]*)\s*\+\s*"
    r"(?P<prefix>(?:\w+::)*)(?P<const>[A-Z0-9_]*RVA[A-Z0-9_]*)\b"
)
# `base.checked_add(FOO_RVA)?` is the SAME hand-built address wearing a different costume, and the
# `base + FOO_RVA` pattern walked straight past six of them. That gap black-screened the game on
# 2026-08-29: `find_title_owner_by_vtable` ended up comparing a RESOLVED state table against a RAW
# 1.16.2 vtable, so the scan could never match, the autoload waited forever for a title owner that
# would never be found, and the boot cover -- which holds until the game's own loading screen
# lights -- never released. A converter that covers one spelling of an idiom covers none of it.
CHECKED_ADD_SITE = re.compile(
    r"(?<![.\w])(?P<base>\w+)\.checked_add\(\s*(?P<prefix>(?:\w+::)*)(?P<const>[A-Z0-9_]*RVA[A-Z0-9_]*)\s*\)\?"
)
# Tested against THE ENCLOSING CALL, never a window of surrounding lines.
#
# It used to be tested against a +/-2-line window, and that is precisely backwards: the shape this
# whole tool exists to catch is a HALF-converted site, one gated address sitting beside a raw one,
# and a window makes the gated neighbour hide the raw one. Both of the addresses found broken on
# 2026-08-30 were hidden this way -- `dialog_active.rs` computes `want_a` raw and `want_b` through
# the resolver four lines apart, and `profile_select_flow.rs` had both halves of a single `||`
# written the two different ways, on ONE line.
#
# What "already gated" actually means is structural, not positional: the raw add is an ARGUMENT to a
# resolver, as in `resolve_game_address(base + SPLASH_SKIP_FN_RVA, "SPLASH_SKIP_FN_RVA")`, which
# takes an absolute address and is correct as written. A resolver call merely NEAR the site says
# nothing. `enclosing_calls_gated` answers the structural question by walking outward through the
# unclosed parentheses that contain the match, so a neighbour cannot hide anything and an argument
# is still recognised across the line breaks of a multi-line call.
ALREADY_GATED = re.compile(r"game_data_addr|game_rva|resolve_game_address|game_ptr")
# How far back `enclosing_calls_gated` looks for the parentheses that contain a match. Wide enough
# for a multi-line call's argument list, deliberately too narrow to reach the enclosing `fn` name of
# a typical body -- a function that merely HAS a resolver-ish name should not silence its contents.
ENCLOSING_SCAN_CHARS = 400
# A HOOK TARGET must stay a raw `base + rva`: `MhHook::new` resolves it itself, through the DETOUR
# resolver, which is a stricter test than the call one. Pre-resolving it does one of two bad
# things -- translates the address TWICE (the bug in bd resolve-twice-refuses-double-translation),
# or hands MinHook the `0` that `game_data_addr` returns on a refusal, which is an install at
# address zero. Measured 2026-08-29: an earlier version of this tool rewrote 18 such sites,
# including a whole `let targets = [...]` list whose `MhHook::new` sat fourteen lines below the
# addresses it collected -- hence the deliberately wide window.
HOOK_TARGET = re.compile(r"MhHook::new|MH_CreateHook|register_union_hook|detour|trampoline|hook as \*mut", re.I)
HOOK_WINDOW_LINES = 14


def mapped_constants() -> set[str]:
    """Every constant the resolver can answer for, by NAME -- resolved through both keys.

    A constant counts as mapped when its NAME appears in a map that has a name column, OR when its
    declared RVA appears in any map. The second half matters: `verified.tsv` carries no names, so
    ~three constants per sweep looked unmapped while the resolver knew them perfectly well.
    """
    names: set[str] = set()
    rvas: set[int] = set()
    for path, column in MAPS:
        try:
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    if not line.startswith("0x"):
                        continue
                    parts = line.rstrip("\n").split("\t")
                    try:
                        value = int(parts[0], 16)
                    except ValueError:
                        continue
                    rvas.add(value - IMAGE_BASE if value >= IMAGE_BASE else value)
                    if column is not None and len(parts) > column:
                        names.add(parts[column].strip())
        except OSError:
            continue
    for path in glob.glob(os.path.join(REPO, "crates", "**", "*.rs"), recursive=True):
        try:
            source = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for match in DECLARATION.finditer(source):
            if int(match.group(2).replace("_", ""), 16) in rvas:
                names.add(match.group(1))
    return names


def enclosing_calls_gated(text: str, pos: int, limit: int = ENCLOSING_SCAN_CHARS) -> bool:
    """Is the expression at `pos` an argument to a call whose name is already a resolver?

    Walks left from `pos` matching parentheses. A `)` deepens; a `(` at depth zero is a call that
    still CONTAINS `pos`, so the identifier immediately before it is read and tested, then the walk
    continues outward through the next enclosing call. Nothing else is examined -- a resolver call
    that is a sibling, a neighbour or a previous statement never reaches depth zero from here, which
    is exactly the difference between "this address is already resolved" and "an address near it
    is".
    """
    depth = 0
    index = pos - 1
    stop = max(0, pos - limit)
    while index >= stop:
        char = text[index]
        if char == ")":
            depth += 1
        elif char == "(":
            if depth == 0:
                start = index
                while start > 0 and (text[start - 1].isalnum() or text[start - 1] in "_:!."):
                    start -= 1
                if ALREADY_GATED.search(text[start:index]):
                    return True
                index = start - 1
                continue
            depth -= 1
        index -= 1
    return False


def rewrite(path: str, mapped: set[str], dry_run: bool, found: list | None = None) -> int:
    """Route every ungated `base + FOO_RVA` in `path` through the resolver. Returns the count.

    Both idioms are collected against the ORIGINAL line and the replacements are spliced in by
    offset, rather than run as two chained `re.sub` passes. The chained form was fine while the
    decisions were purely local, but `enclosing_calls_gated` needs each match's offset in the
    UNMODIFIED file, and the first substitution moves every offset after it.
    """
    text = open(path, encoding="utf-8").read()
    lines = text.splitlines(keepends=True)
    out, changed = [], 0
    offset = 0
    for index, line in enumerate(lines):
        line_start = offset
        offset += len(line)
        if line.lstrip().startswith("//"):
            out.append(line)
            continue
        # The HOOK window stays wide (and stays a window): `MhHook::new` can sit fourteen lines
        # below the `let targets = [...]` list whose addresses it installs, and pre-resolving one of
        # those is a real bug. Being gated is the opposite case and is decided per match, below.
        hook_window = "".join(
            lines[max(0, index - HOOK_WINDOW_LINES) : index + HOOK_WINDOW_LINES + 1]
        )
        if HOOK_TARGET.search(hook_window):
            out.append(line)
            continue

        matches = sorted(
            list(SITE.finditer(line)) + list(CHECKED_ADD_SITE.finditer(line)),
            key=lambda found: found.start(),
        )
        rebuilt, cursor = [], 0
        for match in matches:
            if match.start() < cursor:
                # The two idioms overlapped; the earlier match already consumed these bytes.
                continue
            if match.group("const") not in mapped:
                continue
            if ALREADY_GATED.search(match.group(0)):
                continue
            if enclosing_calls_gated(text, line_start + match.start()):
                continue
            rebuilt.append(line[cursor : match.start()])
            constant = match.group("prefix") + match.group("const")
            rebuilt.append(
                f'{RESOLVER}({match.group("base")}, {constant}, "{match.group("const")}")'
            )
            cursor = match.end()
            changed += 1
            if found is not None:
                found.append((path, index + 1, match.group("const"), line.strip()))
        rebuilt.append(line[cursor:])
        out.append("".join(rebuilt))
    if changed and not dry_run:
        open(path, "w", encoding="utf-8").write("".join(out))
    return changed


def selftest() -> int:
    failures = []
    mapped = mapped_constants()
    if len(mapped) < 200:
        failures.append(f"only {len(mapped)} constants read from the data map")
    match = SITE.search("if vt != base + FOO_VTABLE_RVA {")
    if not match or match.group("base") != "base":
        failures.append("SITE did not capture a plain `base +`")
    match = SITE.search("if vt == $base + FOO_VTABLE_RVA {")
    if not match or match.group("base") != "$base":
        failures.append("SITE lost the `$` on a macro base -- the rewrite would not compile")
    checked = CHECKED_ADD_SITE.search("let want = base.checked_add(FOO_VTABLE_RVA)?;")
    if not checked or checked.group("const") != "FOO_VTABLE_RVA":
        failures.append("CHECKED_ADD_SITE missed `base.checked_add(FOO_RVA)?` -- six such sites once black-screened the game")
    if not ALREADY_GATED.search("game_data_addr(base, FOO_RVA, \"FOO_RVA\")"):
        failures.append("ALREADY_GATED would rewrite an already-gated site twice")
    if not HOOK_TARGET.search("let hook = unsafe { MhHook::new(target as *mut c_void, detour) };"):
        failures.append("HOOK_TARGET missed an MhHook::new install -- it would be pre-resolved")
    if HOOK_TARGET.search("if vt != base + FOO_VTABLE_RVA {"):
        failures.append("HOOK_TARGET wrongly claimed a plain vtable compare is a hook install")

    # THE HALF-CONVERTED LINE. One gated address and one raw one in a single `||`, which is
    # verbatim the shape of `crates/er-title-flow/src/profile_select_flow.rs`. Under the old
    # window-based ALREADY_GATED test the gated half hid the raw half and this line was skipped.
    half = (
        'vt == base + MSGBOX_DIALOG_VTABLE_RVA || vt == '
        'er_game_base::mem::game_data_addr(base, SAVE_RETRY_DIALOG_VTABLE_RVA, '
        '"SAVE_RETRY_DIALOG_VTABLE_RVA")'
    )
    gated = SITE.sub(
        lambda m: f'{RESOLVER}({m.group("base")}, {m.group("const")}, "{m.group("const")}")',
        half,
    )
    if "base + MSGBOX_DIALOG_VTABLE_RVA" in gated:
        failures.append(
            "a raw `base + RVA` beside an already-gated one was skipped -- the half-converted "
            "shape is the whole point of this tool"
        )
    if gated.count(RESOLVER) != 2:
        failures.append(f"half-converted line produced {gated.count(RESOLVER)} resolver calls, want 2")

    # THE BASE NAMED `b`. `lookat_stage_camera.rs` binds the module base as `b`, which the old
    # hard-coded `(base|module_base|image_base|game_base)` alternation could not see.
    match = SITE.search("== b + TITLE_CUSTOM_COVER_PROFILE_RENDERER_VTABLE_RVA")
    if not match or match.group("base") != "b" or not match.group("const").endswith("_RVA"):
        failures.append("SITE missed a one-letter module base -- a real site hid behind that for the whole 1.17 migration")

    # ...but the base still has to look like a binding. These two must NOT match, because rewriting
    # them produces something wrong rather than something gated.
    if SITE.search("let addr = TITLE_OWNER_SCAN_START_ADDRESS + FOO_VTABLE_RVA;"):
        failures.append("SITE treated a SCREAMING_CASE constant as a module base")
    if SITE.search("if vt == self.base + FOO_VTABLE_RVA {"):
        failures.append("SITE would drop the `self.` receiver, which can still compile and be silently wrong")

    # ALREADY_GATED must remain a real guard on the expression it is handed.
    if ALREADY_GATED.search("base + FOO_VTABLE_RVA"):
        failures.append("ALREADY_GATED matched a raw site -- it would skip every rewrite")

    # THE ENCLOSING-CALL TEST, both directions. `resolve_game_address` takes an ABSOLUTE address, so
    # a raw add inside its argument list is correct as written (`constants_autoload_state.rs:224`)
    # and must be left alone -- while the same raw add merely NEXT TO a resolver call must not be.
    argument = 'er_game_base::game_build::resolve_game_address(base + SPLASH_SKIP_FN_RVA, "SPLASH_SKIP_FN_RVA")'
    found = SITE.search(argument)
    if not found:
        failures.append("SITE stopped matching the resolve_game_address argument fixture")
    elif not enclosing_calls_gated(argument, found.start()):
        failures.append("a raw add INSIDE resolve_game_address(...) was not recognised as gated")
    sibling = 'vt == base + MSGBOX_DIALOG_VTABLE_RVA || game_data_addr(base, BAR_RVA, "BAR_RVA") != 0'
    found = SITE.search(sibling)
    if not found or enclosing_calls_gated(sibling, found.start()):
        failures.append("a resolver call SIBLING to a raw add was treated as if it gated it")
    # Across line breaks, which is what makes this different from testing the line alone.
    multiline = 'resolve_game_address(\n    base + SPLASH_SKIP_FN_RVA,\n    "SPLASH_SKIP_FN_RVA",\n)'
    found = SITE.search(multiline)
    if not found or not enclosing_calls_gated(multiline, found.start()):
        failures.append("a multi-line resolver call did not gate the argument on its own line")

    # REPORTING MUST NOT WRITE. This file is its own fixture: the example strings above make it
    # match, so a dry run over it reports a nonzero count -- and must leave the bytes alone. That is
    # the property a forgotten flag violated when this tool rewrote a source file nobody asked it
    # to touch.
    me = os.path.abspath(__file__)
    before = open(me, "rb").read()
    counted = rewrite(me, mapped, dry_run=True)
    if counted == 0:
        failures.append("dry-run fixture found no sites -- the write check below proves nothing")
    if open(me, "rb").read() != before:
        failures.append("a dry run WROTE to a file; the safe default is not safe")

    for line in failures:
        print(f"selftest FAIL {line}")
    print(f"selftest: {len(failures)} failure(s); {len(mapped)} mapped constants")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="actually rewrite the files. Without it this only reports, which is the default "
        "because the bare command used to edit source across the whole workspace",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="accepted for compatibility; reporting is already the default and this cannot "
        "re-enable writing",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="name every site: file, line, constant. A count alone says work exists without "
        "saying where, which is the one thing a report has to answer",
    )
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    # `--dry-run` can only ever turn writing OFF, never on, so the two flags cannot contradict.
    write = args.write and not args.dry_run
    mapped = mapped_constants()
    paths = args.paths or glob.glob(os.path.join(REPO, "crates", "**", "*.rs"), recursive=True)
    total = 0
    sites: list = []
    for path in paths:
        count = rewrite(path, mapped, dry_run=not write, found=sites if args.show else None)
        if count:
            print(f"  {os.path.relpath(path, REPO)}: {count}")
            total += count
    for path, line_no, constant, text in sites:
        print(f"    {os.path.relpath(path, REPO)}:{line_no}  {constant}\n      {text}")
    print(f"{'routed' if write else 'would route'} {total} data site(s) through {RESOLVER}")
    if total and not write:
        print("nothing was written; re-run with --write to apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
