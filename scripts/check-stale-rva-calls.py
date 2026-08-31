#!/usr/bin/env python3
"""Ratchet on game addresses that are CALLED without asking where they live on this build.

Every game RVA in this workspace was reverse-engineered against ELDEN RING 1.16.2. On 2026-08-27
the game shipped 1.17 and moved the code. Two things then had to be gated, and only one was:

  DETOURS      `er-hook` resolves the address before installing, translating it when a mapping is
               verified and refusing when it is not. Done, and it turned a boot-killing crash into
               log lines.
  DIRECT CALLS `transmute(base + SOME_RVA)` builds a function pointer and calls it. Nothing looks
               at it. On a moved build this transfers control into whatever now occupies those
               bytes -- routinely the middle of an unrelated function, which faults with no unwind
               information and no exception record naming anything of ours. It is the WORSE of the
               two: a refused detour makes one feature inert and says so, while a stale call takes
               the process down and leaves a backtrace pointing into game code.

The fix per site is `er_game_base::mem::game_rva`, which translates or returns `Err`. Converting
them is ordinary work; what this gate exists to stop is the set GROWING while that happens. It is
a ratchet, not a freeze: removing sites is free, adding one is refused, and `--refresh` accepts a
new baseline as a reviewable diff rather than as an invisible default.

The key is (file, constant), not a line number, so ordinary edits do not churn the baseline.

WHAT THIS GATE COULD NOT SEE UNTIL 2026-08-30, and what it recorded instead:

  IT READ PROSE.       The matcher ran over raw file text, so a `//` paragraph explaining the
                       hazard and a `///` doc comment saying a call "used to be a bare
                       `transmute(base + SOME_RVA)`" were both recorded as call sites. TWO OF THE
                       THREE BASELINE ROWS WERE THAT. A ratchet whose baseline holds non-findings
                       stays green while real sites appear beside them, and the next agent to
                       shrink it edits a sentence. Comments and string bodies are now blanked
                       before matching, and the baseline was re-derived from scratch rather than
                       having the two rows deleted -- a baseline is only meaningful if every row
                       in it is a real finding someone consciously accepted.
  IT TRUSTED NAMES.    It required the constant to be spelled `*RVA*`. Forty ungated transmutes in
                       `er-build-import-runtime` were named `GET_MAIN_PLAYER_STATS` and the like,
                       or imported under aliases that strip the suffix, so the gate whose entire
                       job was to find them could never match one. The name filter is gone; the
                       arithmetic is the evidence.
  ...WHICH THEN        Dropping the name filter admitted seven `base + PE_DOS_LFANEW_OFFSET`
  ADMITTED A NEW CLASS reads of the DOS header's `0x3c` field. Those are excluded by VALUE -- below
                       `.text`, so fixed by the PE format and unable to move -- and never by name,
                       and a constant that cannot be resolved is KEPT rather than assumed safe.
  IT COULD NOT READ    `own_stepper_idx10_fallbacks!` receives the module base as a metavariable, so
  A MACRO BODY.        every call inside it is spelled `transmute($base + SOME_RVA)`. One `$` was
                       enough to hide `TITLE_TOP_DIALOG_IS_IN_STATE_RVA`, a function that moved
                       0x749b20 -> 0x74a970 and left the 1.16.2 address mid-instruction -- a LIVE
                       1.17 crash the gate reported as absent while it stood.
  IT ASSUMED A         `er-title-flow` keeps most of its addresses in C-like enums, so the call
  CONSTANT IS          reads `base + ProfileLoadMenuRva::ProfileLoadSelectSaveSlot as usize`, and
  SCREAMING_SNAKE.     other crates reach one through its module (`jp::GAME_MAN_GLOBAL_RVA`).
                       Neither is SCREAMING_SNAKE at the use site. That hid a second live call
                       (0x9a5f20 -> 0x9a70c0) and six raw global reads. Both spellings now match,
                       keyed on the LAST path segment so a rewritten `use` does not churn the
                       baseline.

Both were found the same way the earlier two were: by reading the source directly instead of
trusting this gate's silence. It reported `1 known ungated site(s), none new` throughout.

    python3 scripts/check-stale-rva-calls.py             # enforce
    python3 scripts/check-stale-rva-calls.py --list      # what is still ungated, by crate
    python3 scripts/check-stale-rva-calls.py --refresh   # accept the current set
    python3 scripts/check-stale-rva-calls.py --selftest
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# ONE DIALECT, NOT THREE. `code_only` moved to `scripts/rva_symbols.py` on 2026-08-30 so this gate
# and `check-1170-translation-collisions.py` share it. Both had been caught the same day looking
# for an address in one spelling; the second of them recommended DELETING a row on that silence.
# A shared reader is the point: a fix to comment/string stripping now lands in both at once.
try:  # noqa: E402 - repo-local; the sys.path line above is what makes it work
    from rva_symbols import code_only
except ImportError as missing:  # a shared reader that cannot load must stop the gate, not degrade
    raise ImportError(
        "scripts/rva_symbols.py could not be imported, so comments and string bodies cannot be "
        "blanked before matching. Without it this gate reads PROSE as findings -- two of its three "
        "baseline rows once were exactly that. Fix the import rather than restoring a local copy."
    ) from missing

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CRATES = os.path.join(ROOT, "crates")
BASELINE = os.path.join(ROOT, "docs", "recon", "stale-rva-call-sites.txt")
# `\$?` FOR MACRO BODIES (widened 2026-08-30). `own_stepper_idx10_fallbacks!` takes the module base
# as a metavariable and spells it `$base`, so its call sites read `transmute($base + SOME_RVA)`.
# The `\(\s*` before this expression will not step over the `$`, so every site inside that macro
# was invisible -- and one of them was the live 1.17 defect this gate exists to catch:
# `transmute($base + TITLE_TOP_DIALOG_IS_IN_STATE_RVA)`, whose function moved 0x749b20 -> 0x74a970,
# leaving the 1.16.2 address mid-instruction. A macro body is ordinary code; the `$` is punctuation.
BASE_EXPR = r"\$?(?:base|module_base|game_base|image_base)"
# THE CONSTANT'S NAME IS NOT EVIDENCE OF ANYTHING (widened 2026-08-30).
#
# Both patterns below used to require `[A-Z0-9_]*RVA[A-Z0-9_]*`, on the reasoning that `_RVA` is
# this workspace's naming convention for a 1.16.2 address and is "what makes the set findable at
# all". It is not. What makes a site a stale call is the ARITHMETIC -- a module base plus a
# compile-time constant, transmuted and called -- and the arithmetic does not care what the
# constant is spelled. Two spellings defeated the old filter outright:
#
#   * constants that never carried the suffix. `er-build-import-runtime` calls
#     `GET_MAIN_PLAYER_STATS`, `EQUIP_ITEM_TO_CHR_ASM_SLOT` and thirty-eight more by those names.
#   * constants that HAD the suffix and lost it at the import. `use ...::FOO_RVA as FOO;` renames
#     the address at the use site, which is exactly where this gate looks.
#
# Forty ungated sites in one crate were invisible to a gate whose entire purpose was to find them,
# and it reported `3 known ungated site(s), none new` while they were being written. So the name
# filter is gone: any SCREAMING_SNAKE identifier added to a module base is a candidate. That admits
# a genuinely version-independent constant, which is what the baseline and the "say so at the call
# site" escape hatch are for -- a false positive costs a line of review, and this false NEGATIVE
# cost forty.
#
# NOR IS ITS SHAPE (widened again 2026-08-30). SCREAMING_SNAKE is not the only spelling an address
# constant has here. `er-title-flow` collects them into C-like enums and writes the address as
# `ProfileLoadMenuRva::ProfileLoadSelectSaveSlot as usize`, and other crates reach a constant
# through its module (`jp::GAME_MAN_GLOBAL_RVA`, `ersc::SHOW_RVA`). Both are `base + <constant>`
# arithmetic and neither matched a SCREAMING_SNAKE-only pattern -- which is how
# `transmute(base + ProfileLoadMenuRva::ProfileLoadSelectSaveSlot as usize)` sat in
# `profile_dialog_select_save_slot` calling 1.16.2's 0x9a5f20 on a build where that function had
# moved to 0x9a70c0 and the old address was mid-instruction. So a path qualifier and a trailing
# `as usize` are both accepted, and the LAST path segment is what the baseline is keyed on so the
# key stays stable if a `use` is rewritten.
CONSTANT = r"(?:[A-Za-z_][A-Za-z0-9_]*::)*([A-Z][A-Za-z0-9_]*)(?:\s+as\s+usize)?"
CALL_SITE = re.compile(
    r"transmute(?:::<[^>]*>)?\(\s*" + BASE_EXPR + r"\s*\+\s*" + CONSTANT + r"\s*\)"
)
# A game GLOBAL read through the same stale arithmetic. Added 2026-08-29, because counting only
# the calls understated the exposure and the miss was expensive.
#
# Every `.data` global moved between 2.6.2.0 and 2.7.0.0 -- most by +0x4070, but not all, and
# `cs_system_step` by a different amount than the neighbour eight bytes away, so no constant
# delta rescues these. A stale read does not fault the way a stale call does; `safe_read_usize`
# succeeds and returns whatever now lives there. `GLOBAL_TEX_REPOSITORY_RVA` read that way, and
# the garbage pointer went into `CreateTpfResCap`, which divided by zero and took the game down
# 894ms after load with a perfectly correct function address one frame up. Quieter than a stale
# call, and harder to attribute for exactly that reason.
READ_SITE = re.compile(
    r"(?:safe_read_(?:usize|u64|u32|u16|u8|i32)|read_bytes)\(\s*"
    + BASE_EXPR + r"\s*\+\s*" + CONSTANT + r"\s*[,)]"
)
# A game address used as one side of a COMPARISON. Added 2026-08-30, and it is the quietest member
# of the family by some distance.
#
# The three failures are not the same failure. A stale CALL kills the process and leaves a
# backtrace. A stale READ returns plausible nonsense that surfaces somewhere else, later. A stale
# COMPARISON does nothing at all: `observed_vtable == base + MOVED_RVA` is simply false forever,
# so the classifier behind it answers "no" to every object it is shown and the feature it gates is
# absent -- with no fault, no refusal line, and no counter moving. Perfect silence is the one
# outcome `resolve_game_address` was written to make impossible, and the comparison form walked
# straight past it because nothing about `==` looks like a call.
#
# MEASURED, all on 1.17, all found by reading the source rather than by this gate: the Scaleform
# `MemoryFile` vtable (0x2ba4c80 -> 0x2ba7d70) matched no File, so both DLLs' `.gfx` swaps stopped;
# `MenuTitleContinue::_Do_call` (0x764b80 -> 0x7659d0) matched no functor at four separate hooks,
# so the autoload never identified the Continue row; `CS::SceneObjProxy`'s vtable
# (0x2a94a70 -> 0x2a97af0) made product-core readiness report BLOCKER_PRESS_START forever.
#
# THE OTHER HALF OF THE RULE, which this pattern cannot check and a reviewer must: the resolved
# side has to be screened against ZERO before it is compared. `game_data_addr` answers 0 for a
# refusal -- harmless as a read target, and as a COMPARISON target it makes `x == 0` true for every
# unset field, which is worse than never matching. `dispatch_target_is_purecall` and
# `save_picker_rebuild_target_is_live` are the two worked examples in the tree.
COMPARE_SITE = re.compile(
    r"(?:[!=]=\s*" + BASE_EXPR + r"\s*\+\s*" + CONSTANT + r")"
    r"|(?:" + BASE_EXPR + r"\s*\+\s*" + CONSTANT + r"\s*[!=]=)"
)
# The patterns this gate USED to be, kept ONLY so `--selftest` can prove each widening is
# load-bearing: the positive controls below must be invisible to the old shape and visible to the
# current one. A control that both patterns catch would pass on the broken gate and prove nothing.
#
# SPELLED OUT AS LITERALS, NOT COMPOSED FROM `BASE_EXPR`/`CONSTANT`. A frozen control assembled from
# the live pieces is not frozen: it widens whenever they widen, and the assertion that "the old
# pattern misses this" silently becomes an assertion that the new pattern misses it, which is the
# opposite claim. That already nearly happened -- `LEGACY_NAME_FILTERED` was built from `BASE_EXPR`,
# so widening `BASE_EXPR` to accept `$base` would have quietly taught the "legacy" pattern the very
# spelling the new control exists to prove it could not see.
LEGACY_BASE = r"(?:base|module_base|game_base|image_base)"
LEGACY_SCREAMING_SNAKE = r"([A-Z][A-Z0-9_]*)"
# Before 2026-08-30 (morning): the constant had to be spelled `*RVA*`.
LEGACY_NAME_FILTERED = re.compile(
    r"transmute(?:::<[^>]*>)?\(\s*" + LEGACY_BASE + r"\s*\+\s*([A-Z0-9_]*RVA[A-Z0-9_]*)\s*\)"
)
# Before 2026-08-30 (afternoon): the base could not be a macro metavariable and the constant could
# not be path-qualified or an enum variant.
LEGACY_PRE_WIDENING_CALL = re.compile(
    r"transmute(?:::<[^>]*>)?\(\s*" + LEGACY_BASE + r"\s*\+\s*" + LEGACY_SCREAMING_SNAKE + r"\s*\)"
)
# Before the comparison pattern existed the gate looked ONLY for a transmuted call and a
# `safe_read_*`. Frozen as literals for the same reason as the two above: composing this from
# `CALL_SITE`/`READ_SITE` would teach it every future widening and turn "the old gate could not see
# a comparison" into a claim about whatever the gate looks like next.
LEGACY_CALL_AND_READ_ONLY = re.compile(
    r"transmute(?:::<[^>]*>)?\(\s*"
    + LEGACY_BASE
    + r"\s*\+\s*"
    + LEGACY_SCREAMING_SNAKE
    + r"\s*\)"
    r"|(?:safe_read_(?:usize|u64|u32|u16|u8|i32)|read_bytes)\(\s*"
    + LEGACY_BASE
    + r"\s*\+\s*"
    + LEGACY_SCREAMING_SNAKE
    + r"\s*[,)]"
)
LEGACY_PRE_WIDENING_READ = re.compile(
    r"(?:safe_read_(?:usize|u64|u32|u16|u8|i32)|read_bytes)\(\s*"
    + LEGACY_BASE + r"\s*\+\s*" + LEGACY_SCREAMING_SNAKE + r"\s*[,)]"
)


# `.text` begins at RVA 0x1000 in both 1.16.2 and 1.17. Everything below that is the DOS stub and
# the PE headers, whose layout is fixed by the PE specification and is therefore the one thing in
# the image that CANNOT move between game builds.
#
# This matters because dropping the name filter admitted a class the name filter had been hiding
# by accident: `safe_read_u32(base + PE_DOS_LFANEW_OFFSET)`, seven sites across five crates, all
# reading the same `0x3c` field to find the NT header. That is `base + constant` arithmetic, it
# does match the pattern, and it is not a stale address in any build -- baselining seven permanent
# non-findings would rebuild exactly the contaminated baseline this pass exists to clear.
#
# So the exclusion is by VALUE, not by name -- the same lesson as the widening itself. A constant
# whose value cannot be resolved is KEPT, because "I could not read it" must not be spelled the
# same way as "I read it and it is safe".
PE_HEADER_LIMIT = 0x1000
DECLARATION = re.compile(
    r"const\s+([A-Z][A-Z0-9_]*)\s*:\s*\w+\s*=\s*(0x[0-9a-fA-F_]+|\d+)\s*;"
)


def rust_sources():
    """Every `.rs` file the gate reads, as absolute paths.

    Factored out of the two walks so `--selftest` can assert the WALK found a tree. That assertion
    used to be spelled "at least one ungated site must exist", which conflated two very different
    facts -- the matcher works, and the tree still has findings -- and made a genuinely clean tree
    indistinguishable from a broken scan. The set reaching zero is the POINT of a ratchet; a walk
    reaching zero files is a bug. Those are now separate assertions.
    """
    out = []
    for dirpath, dirnames, filenames in os.walk(CRATES):
        dirnames[:] = [d for d in dirnames if d != "target"]
        out.extend(
            os.path.join(dirpath, name) for name in filenames if name.endswith(".rs")
        )
    return out


def constant_values():
    """`{name: value}` for every unambiguous `const NAME: T = <literal>;` under `crates/`.

    A name declared twice with DIFFERENT values is recorded as `None`: ambiguous is not resolved,
    and an unresolved constant is kept as a finding rather than dropped.
    """
    seen = {}
    for path in rust_sources():
            text = code_only(open(path, encoding="utf-8", errors="replace").read())
            for constant, literal in DECLARATION.findall(text):
                value = int(literal.replace("_", ""), 0)
                if constant in seen and seen[constant] != value:
                    seen[constant] = None
                else:
                    seen.setdefault(constant, value)
    return seen


def is_finding(constant, values):
    """Is `base + constant` a stale-address hazard? Unresolvable means YES."""
    value = values.get(constant)
    return value is None or value >= PE_HEADER_LIMIT


def sites(values=None):
    """{(crate-relative path, constant)} for every ungated direct call or read in the tree."""
    if values is None:
        values = constant_values()
    found = set()
    for path in rust_sources():
        relative = os.path.relpath(path, ROOT)
        text = code_only(open(path, encoding="utf-8", errors="replace").read())
        for pattern in (CALL_SITE, READ_SITE, COMPARE_SITE):
            for match in pattern.findall(text):
                # COMPARE_SITE has two alternatives and so two groups; exactly one participates.
                constant = (
                    next((group for group in match if group), "")
                    if isinstance(match, tuple)
                    else match
                )
                if constant and is_finding(constant, values):
                    found.add((relative, constant))
    return found


def baseline():
    if not os.path.exists(BASELINE):
        return set()
    out = set()
    for line in open(BASELINE, encoding="utf-8"):
        if line.startswith("#") or not line.strip():
            continue
        path, _, constant = line.strip().rpartition("\t")
        out.add((path, constant))
    return out


def write_baseline(current):
    with open(BASELINE, "w", encoding="utf-8") as handle:
        handle.write(
            "# Game addresses CALLED without resolving them for the running build.\n"
            "#\n"
            "# Generated by scripts/check-stale-rva-calls.py --refresh. One line per\n"
            "# (file, RVA constant). This set may SHRINK freely; growth is refused, because each\n"
            "# entry is a direct transfer of control to a 1.16.2 address on a build that moved the\n"
            "# code. Convert a site by resolving through er_game_base::mem::game_rva and handling\n"
            "# the Err, then re-run --refresh to record the smaller set.\n"
            f"# Currently {len(current)} sites in "
            f"{len({path for path, _ in current})} files.\n"
        )
        for path, constant in sorted(current):
            handle.write(f"{path}\t{constant}\n")


def report(current):
    by_crate = {}
    for path, constant in current:
        crate = path.split(os.sep)[1] if os.sep in path else path
        by_crate.setdefault(crate, set()).add(constant)
    for crate in sorted(by_crate, key=lambda c: (-len(by_crate[c]), c)):
        print(f"{len(by_crate[crate]):4}  {crate}")
    print(f"{len(current):4}  TOTAL across {len(by_crate)} crates")


def enforce():
    current, known = sites(), baseline()
    added = current - known
    removed = known - current
    if added:
        print(f"{len(added)} game address(es) newly CALLED without resolving for the build:\n")
        for path, constant in sorted(added):
            print(f"  {path}\t{constant}")
        print(
            "\nResolve through er_game_base::mem::game_rva, which translates the address when a\n"
            "mapping is verified and returns Err when it is not, and handle the Err. If the site\n"
            "is genuinely version-independent, say so at the call site and re-run --refresh."
        )
        return 1
    if removed:
        print(f"{len(removed)} site(s) resolved since the baseline -- run --refresh to record it:")
        for path, constant in sorted(removed):
            print(f"  {path}\t{constant}")
        return 1
    print(f"stale-rva-calls: {len(current)} known ungated site(s), none new")
    return 0


def selftest():
    """The matcher must find a real site, must not fire on a resolved one, and must not read prose.

    THE CONTROLS THAT MATTER ARE THE ONES THE OLD GATE WOULD HAVE FAILED. A control built from a
    `*_RVA`-named constant in live code passes on the broken version too and therefore proves
    nothing; every control below is checked against `LEGACY_NAME_FILTERED` as well, and the two
    spellings that defeated the old gate are asserted to be invisible to it.
    """
    ungated = "let f: F = unsafe { std::mem::transmute(base + SET_SAVE_SLOT_RVA) };"
    gated = "let f: F = unsafe { transmute(game_rva(SET_SAVE_SLOT_RVA)?) };"
    assert CALL_SITE.findall(ungated) == ["SET_SAVE_SLOT_RVA"], "must flag a raw base + RVA call"
    assert not CALL_SITE.findall(gated), "must not flag a call already routed through game_rva"
    turbofish = "unsafe { transmute::<usize, F>(module_base + MENU_RVA) }"
    assert CALL_SITE.findall(turbofish) == ["MENU_RVA"], "must see through a turbofish"

    # POSITIVE CONTROL 1 -- a constant that never carried the suffix. Forty of these live in
    # er-build-import-runtime and not one was visible to the gate written to find them.
    unsuffixed = "let f: F = unsafe { transmute(base + GET_MAIN_PLAYER_STATS) };"
    assert CALL_SITE.findall(unsuffixed) == ["GET_MAIN_PLAYER_STATS"], (
        "must flag a call whose constant is not named *RVA*"
    )
    assert not LEGACY_NAME_FILTERED.findall(unsuffixed), (
        "control is worthless unless the OLD name-filtered pattern misses it"
    )

    # POSITIVE CONTROL 2 -- the suffix survives the declaration and dies at the import. The gate
    # reads the USE site, which is where the alias has already stripped it.
    aliased = (
        "use er_game_base::rva::EQUIP_ITEM_TO_CHR_ASM_SLOT_RVA as EQUIP_ITEM_TO_CHR_ASM_SLOT;\n"
        "let f: F = unsafe { transmute(base + EQUIP_ITEM_TO_CHR_ASM_SLOT) };"
    )
    assert CALL_SITE.findall(aliased) == ["EQUIP_ITEM_TO_CHR_ASM_SLOT"], (
        "must flag a call reached through an alias that strips the _RVA suffix"
    )
    assert not LEGACY_NAME_FILTERED.findall(aliased), (
        "control is worthless unless the OLD name-filtered pattern misses it"
    )

    # POSITIVE CONTROL 3 -- the same widening on the READ pattern.
    read = "match unsafe { safe_read_usize(base + WORLD_CHR_MAN) } { _ => () }"
    assert READ_SITE.findall(read) == ["WORLD_CHR_MAN"], "must flag an unsuffixed stale read"

    # POSITIVE CONTROL 4 -- a MACRO BODY. `own_stepper_idx10_fallbacks!` receives the module base as
    # a metavariable, so every call inside it reads `$base + ...`. The live 1.17 defect found on
    # 2026-08-30 -- `transmute($base + TITLE_TOP_DIALOG_IS_IN_STATE_RVA)`, a function that had moved
    # 0x749b20 -> 0x74a970 -- sat in exactly that spelling and this gate reported it as absent.
    macro_body = "let f: F = unsafe { std::mem::transmute($base + TITLE_TOP_DIALOG_IS_IN_STATE_RVA) };"
    assert CALL_SITE.findall(macro_body) == ["TITLE_TOP_DIALOG_IS_IN_STATE_RVA"], (
        "must flag a call written against a macro metavariable base"
    )
    assert not LEGACY_PRE_WIDENING_CALL.findall(macro_body), (
        "control is worthless unless the OLD `$`-blind pattern misses it"
    )

    # POSITIVE CONTROL 5 -- an ENUM VARIANT, which is how er-title-flow spells most of its
    # addresses. `transmute(base + ProfileLoadMenuRva::ProfileLoadSelectSaveSlot as usize)` called
    # 1.16.2's 0x9a5f20 on a build where that function lives at 0x9a70c0 and the old address is
    # mid-instruction; a SCREAMING_SNAKE-only pattern could not see it.
    enum_variant = (
        "let f: F = unsafe { core::mem::transmute("
        "base + ProfileLoadMenuRva::ProfileLoadSelectSaveSlot as usize) };"
    )
    assert CALL_SITE.findall(enum_variant) == ["ProfileLoadSelectSaveSlot"], (
        "must flag a call whose address is an enum variant, keyed on the last path segment"
    )
    assert not LEGACY_PRE_WIDENING_CALL.findall(enum_variant), (
        "control is worthless unless the OLD SCREAMING_SNAKE-only pattern misses it"
    )

    # POSITIVE CONTROL 6 -- a module-qualified constant, on the READ pattern. `jp::GAME_MAN_GLOBAL_RVA`
    # was read raw two lines below a sibling that resolved correctly.
    qualified = "let g = unsafe { safe_read_usize(base + jp::GAME_MAN_GLOBAL_RVA) };"
    assert READ_SITE.findall(qualified) == ["GAME_MAN_GLOBAL_RVA"], (
        "must flag a read through a module-qualified constant"
    )
    assert not LEGACY_PRE_WIDENING_READ.findall(qualified), (
        "control is worthless unless the OLD unqualified pattern misses it"
    )

    # POSITIVE CONTROL 7 -- a COMPARISON target, in both operand orders. This is the class that
    # produced no evidence of its own existence: on 1.17 the Scaleform `MemoryFile` vtable moved
    # 0x2ba4c80 -> 0x2ba7d70, so `vtable != base + MEMORY_FILE_VTABLE_RVA` was true for every File
    # and both DLLs' `.gfx` swaps simply stopped happening, silently.
    def compared(text):
        return [c for pair in COMPARE_SITE.findall(text) for c in pair if c]

    compare_rhs = "if vtable != base + MEMORY_FILE_VTABLE_RVA { return; }"
    assert compared(compare_rhs) == ["MEMORY_FILE_VTABLE_RVA"], (
        "must flag a vtable compared against a raw base + RVA"
    )
    compare_lhs = "if base + MENU_TITLE_CONTINUE_DOCALL_RVA == do_call { hit(); }"
    assert compared(compare_lhs) == ["MENU_TITLE_CONTINUE_DOCALL_RVA"], (
        "must flag the comparison written the other way round"
    )
    for control in (compare_rhs, compare_lhs):
        assert not LEGACY_CALL_AND_READ_ONLY.findall(control), (
            "control is worthless unless the OLD call-and-read-only gate misses it"
        )
    resolved_compare = (
        'if vtable != er_game_base::mem::game_data_addr(base, MEMORY_FILE_VTABLE_RVA, "X") {}'
    )
    assert not compared(resolved_compare), (
        "must not flag a comparison already routed through game_data_addr"
    )

    # NEGATIVE CONTROLS -- prose that DESCRIBES the hazard is not the hazard. Both of these were
    # real baseline rows until 2026-08-30.
    line_comment = "// a stale address is reachable as a CALL (`transmute(base + RVA)`) too"
    doc_comment = "/// used to be a bare `transmute(base + SOME_RVA)`. On 1.17 that is a dead process."
    block_comment = "/* transmute(base + BLOCK_COMMENT_RVA) */"
    nested_block = "/* outer /* inner transmute(base + NESTED_RVA) */ still comment */"
    for prose in (line_comment, doc_comment, block_comment, nested_block):
        assert not CALL_SITE.findall(code_only(prose)), f"must not read prose: {prose[:40]}"

    # ...and stripping must not blind the matcher to code that merely SITS NEAR prose, or to code
    # after a string containing comment or quote characters.
    mixed = (
        "// transmute(base + IN_A_COMMENT_RVA)\n"
        'let url = "https://example/not-a-comment";\n'
        "let f: F = unsafe { transmute(base + REAL_SITE_RVA) };"
    )
    assert CALL_SITE.findall(code_only(mixed)) == ["REAL_SITE_RVA"], (
        "comment stripping must blank only the comment"
    )
    in_string = 'let s = "// transmute(base + QUOTED_RVA)"; transmute(base + AFTER_STRING_RVA)'
    assert CALL_SITE.findall(code_only(in_string)) == ["AFTER_STRING_RVA"], (
        "a // inside a string literal does not open a comment"
    )
    raw_string = 'let s = r#"/* transmute(base + RAW_RVA) */"#; transmute(base + AFTER_RAW_RVA)'
    assert CALL_SITE.findall(code_only(raw_string)) == ["AFTER_RAW_RVA"], (
        "a block comment inside a raw string is not a comment"
    )
    lifetime = "fn f<'a>(x: &'a u8) { transmute(base + AFTER_LIFETIME_RVA) }"
    assert CALL_SITE.findall(code_only(lifetime)) == ["AFTER_LIFETIME_RVA"], (
        "a lifetime tick is not an unterminated char literal"
    )

    # THE VALUE GATE. A PE-header field is excluded because of what it IS, not what it is called,
    # and a constant nobody could resolve is kept -- "unreadable" must not be spelled the same way
    # as "safe".
    values = {"PE_DOS_LFANEW_OFFSET": 0x3C, "CSDLC_SINGLETON_RVA": 0x3D86BD8, "AMBIGUOUS": None}
    assert not is_finding("PE_DOS_LFANEW_OFFSET", values), "a PE header offset is not a game address"
    assert is_finding("CSDLC_SINGLETON_RVA", values), "a real .data RVA is a finding"
    assert is_finding("AMBIGUOUS", values), "a constant declared twice with different values is kept"
    assert is_finding("NEVER_DECLARED", values), "a constant that could not be resolved is kept"

    # EMPTINESS -- OF THE WALK, NOT OF THE FINDINGS. Every INPUT this gate reasons from is asserted
    # non-empty, with an order of magnitude, before anything is concluded from it: a scan that
    # silently reads nothing makes `no new sites` and `I did not look` indistinguishable, and only
    # one of those is good news. Ranges are deliberately loose -- they catch a walk that broke, not
    # a tree that grew.
    #
    # WHAT IS NO LONGER ASSERTED, and why. This used to require at least one ungated site to exist,
    # on the same reasoning. It is the wrong place to put it: the set reaching zero is the POINT of
    # a ratchet, and on 2026-08-30 it did -- the two live `transmute(base + RVA)` calls and the six
    # raw global reads were converted, and the gate that had just been widened to see them promptly
    # failed its own selftest for finding nothing. Asserting on the findings conflates "the matcher
    # works" with "the tree still has work", and only the first is this function's business. The
    # first is established by the eight controls above, which do not depend on the tree at all.
    sources = rust_sources()
    assert len(sources) > 200, (
        f"only {len(sources)} .rs files found under {CRATES}; the walk is broken, so both the "
        "declaration scan and the site scan below it are reading nothing"
    )
    declared = constant_values()
    assert len(declared) > 500, (
        f"only {len(declared)} constants resolved from crates/; this walk normally sees thousands, "
        "so the declaration scan is broken and every value-gate decision below it is unfounded"
    )
    current = sites(declared)
    print(
        f"selftest OK ({len(current)} ungated site(s) visible across {len(sources)} sources, "
        f"{len(declared)} constants resolved, "
        f"{sum(1 for v in declared.values() if v is not None and v < PE_HEADER_LIMIT)} "
        "sub-0x1000 constants excluded by value)"
    )
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--refresh", action="store_true", help="accept the current set")
    parser.add_argument("--list", action="store_true", help="count ungated sites per crate")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if args.list:
        report(sites())
        return 0
    if args.refresh:
        current = sites()
        write_baseline(current)
        print(f"baseline refreshed: {len(current)} site(s)")
        return 0
    return enforce()


if __name__ == "__main__":
    sys.exit(main())
