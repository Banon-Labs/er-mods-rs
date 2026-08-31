#!/usr/bin/env python3
"""Which constants this workspace USES as game addresses, derived from the call sites.

WHY THIS EXISTS, AND WHY IT IS NOT A FOURTH NAME REGEX
------------------------------------------------------
`select-needed-1170-rows.py` decides what to translate by scanning for constants whose NAME
carries `RVA`. That question -- "is this spelled like an address?" -- is not the question that
matters, and it has now been answered wrong four times:

  * `_BOUND`-suffixed names, filtered out of selection and thereby out of the "is it declared?"
    question too;
  * `Enum::Variant as usize` aliases, whose value lives in another file -- 37 of them, and the
    miss black-screened the game;
  * bare `rva: 0x...` fields in `HookSpec`/`MapSeam` literals with no constant name at all -- 53
    of them, refused at runtime under no name anyone could search for;
  * and, measured 2026-08-30, every one of the 27 game functions `er-build-import-runtime` calls,
    named `GET_WEAPON_NAME`, `SET_REINFORCEMENT`, `EQUIP_ITEM_TO_CHR_ASM_SLOT` and so on. Zero of
    the 27 were visible. The build importer silently applied nothing: the six `MsgRepository`
    name getters were refused, so every item name failed to resolve, so `read_character.rs`
    dropped all 18 equipped items, so "Generate Build Link" exported an itemless build and "Load
    Build from URL" applied none. The log said `catalog: 0 named, 6966 unnamed` and the telemetry
    said success.

The question that MATTERS is "does this workspace hand the constant to the address resolver?"
That is a property of the CALL SITE, not of the spelling, and it cannot drift as names drift.
A constant passed to `native::resolve` IS a game address by construction -- there is no other
reason to pass it -- so this module answers by reading the argument lists.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not guess from the VALUE. A value threshold was measured first and rejected: admitting
any uppercase hex constant at or above `0x1000` pulls in eleven constants that are not addresses,
ten of them because they are exactly `0x1000` -- which is where `.text` begins and therefore
where a function begins, so they pair cleanly against the function map and mean nothing. That is
the same trap `select-needed-1170-rows.py::BOUND` already documents for `AV_GAME_TEXT_RVA_MIN`.
`MEM_COMMIT`, `DDSD_PIXELFORMAT` and `MINIDUMP_WITH_THREAD_INFO` are Win32 flags; translating one
would write a wrong row into a tracked ledger, and the ledger's own docstring is explicit that a
wrong row that survives forever reads as a live value and is worse than a missing one.

Usage has no such failure mode: nothing passes `MEM_COMMIT` to an address resolver.
"""

from __future__ import annotations

import re
from pathlib import Path

# The resolver entry points. `resolve` last and alternation-anchored so `resolve_all` and
# `resolve_game_address` are not clipped to their first six characters.
RESOLVERS = re.compile(r"\b(?:native::)?(?:resolve_all|resolve_game_address|resolve)\s*\(")
# UPPER_SNAKE of four characters or more. Shorter runs are type parameters and enum discriminants.
IDENT = re.compile(r"\b([A-Z][A-Z0-9_]{3,})\b")
# The pre-gate shape this repo is removing: `transmute(module_base + SOME_RVA)`. Still matched,
# because a crate that has not yet been moved onto the resolver is exactly the crate whose
# addresses most need translating.
TRANSMUTE = re.compile(
    r"transmute\s*(?:::<[^>]*>)?\s*\(\s*\w+\s*\+\s*([A-Z][A-Z0-9_]{3,})\b"
)
# A table field that HOLDS an address, initialised from a named constant:
# `getter_rva: rva::GET_WEAPON_NAME_RVA,`. This is the named twin of the bare `rva: 0x...` literal
# that `select-needed-1170-rows.py::BARE_RVA_FIELD` already special-cases, and it is how the six
# `MsgRepository` name getters reach the resolver: `SOURCES` stores the address, `getter_rva_for`
# returns it, and `name_for` resolves the returned VARIABLE. Four of the six therefore never
# appear inside a resolver's argument list at all, and a call-site-only scan finds 23 of the 27
# rather than 27 -- measured on the pre-rename tree before this pattern was added.
# NOT `re.I`: a case-insensitive flag also widens the `[A-Z]` capture, which matched the
# lowercase `usize` in `const FOO_RVA: usize = ...` and put a type name in the vocabulary.
# Harmless only because admission also requires the name to be a declared constant -- so it
# is spelled out instead.
FIELD_CONST = re.compile(r"\b\w*(?:rva|RVA)\s*:\s*(?:\w+::)*([A-Z][A-Z0-9_]{3,})\b")


def _args_of_calls(text: str) -> list[str]:
    """The parenthesised argument text of every resolver call in `text`.

    Paren-BALANCED rather than line- or regex-delimited. The equip pass asks for ten functions in
    one `resolve_all` array spanning forty lines; a fixed lookahead reads part of it and reports a
    subset, which is the same silent undercount this module exists to end.
    """
    out: list[str] = []
    for match in RESOLVERS.finditer(text):
        start = match.end() - 1
        depth = 0
        for index in range(start, len(text)):
            char = text[index]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    out.append(text[start : index + 1])
                    break
    return out


def used_as_game_address(text: str) -> set[str]:
    """Constant names `text` hands to an address resolver."""
    names: set[str] = set()
    for args in _args_of_calls(text):
        names |= set(IDENT.findall(args))
    names |= set(TRANSMUTE.findall(text))
    names |= set(FIELD_CONST.findall(text))
    return names


def workspace_usage(repo: Path) -> set[str]:
    """Every such name across `crates/`.

    Collected workspace-wide and not per file, because the declaration and the call site are
    routinely in different crates: `er-build-import-runtime` calls ten addresses that
    `er-game-base::rva` declares.
    """
    names: set[str] = set()
    for path in sorted(repo.glob("crates/**/*.rs")):
        names |= used_as_game_address(path.read_text(encoding="utf-8", errors="replace"))
    return names


# `#[cfg(test)]` followed by its `mod`. The attribute and the `mod` keyword are routinely
# separated by a doc comment, so the gap is matched rather than assumed away.
CFG_TEST_MOD = re.compile(r"#\[cfg\(test\)\]\s*(?://[^\n]*\n\s*)*(?:pub\s+)?mod\s+\w+\s*\{")


def test_module_spans(text: str) -> list[tuple[int, int]]:
    """Byte ranges of every `#[cfg(test)]` module in `text`.

    WHY THE SELECTOR HAS TO SKIP THESE. A test may declare an address deliberately in order to
    assert that the workspace does NOT use it, and `er-seamless-bugfixes` does exactly that:

        const CHAINED_CONTINUATION_RVA: usize = 0xc5_7666;

    names a `.pdata` CHAINED-CONTINUATION record 0x86 bytes inside a live function, and the test
    around it exists to prove `FREELIST_SHUTDOWN_ASSERT_FN_RVA` is not that address. Its own
    doc comment says naming it "would put a BOTH-ENTRIES row into the maps for an address 0x86
    inside a live function" -- which is precisely what happened: the name ends in `_RVA`, so the
    selector took it, and `check-no-chained-continuation-rows.py` failed on the row.

    Test code is never resolved at runtime, so nothing declared here belongs in an address map.
    """
    spans: list[tuple[int, int]] = []
    for match in CFG_TEST_MOD.finditer(text):
        depth = 0
        for index in range(match.end() - 1, len(text)):
            char = text[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    spans.append((match.start(), index + 1))
                    break
    return spans


def in_any_span(offset: int, spans: list[tuple[int, int]]) -> bool:
    """Whether `offset` falls inside one of `spans`."""
    return any(start <= offset < end for start, end in spans)


def selftest() -> int:
    """Prove the matcher catches the shapes that defeated the name scan, and no others."""
    multiline = (
        "let [a, b] = crate::native::resolve_all(\n"
        "    module_base,\n"
        "    [\n"
        "        (GET_WEAPON_NAME, \"MsgRepositoryImp::GetWeaponName\"),\n"
        "        (\n"
        "            SET_REINFORCEMENT,\n"
        "            \"SetReinforcement\",\n"
        "        ),\n"
        "    ],\n"
        ")?;"
    )
    found = used_as_game_address(multiline)
    assert "GET_WEAPON_NAME" in found and "SET_REINFORCEMENT" in found, (
        f"a resolver argument spanning several lines was missed: {sorted(found)}"
    )

    single = "let g = crate::native::resolve(module_base, EQUIP_PERMISSION_GATE, \"gate\");"
    assert "EQUIP_PERMISSION_GATE" in used_as_game_address(single)

    legacy = "let f: F = unsafe { transmute(module_base + GET_MAIN_PLAYER_STATS) };"
    assert "GET_MAIN_PLAYER_STATS" in used_as_game_address(legacy)

    # The indirect shape: stored in a table, returned as a variable, resolved through the variable.
    # The constant's name never occurs inside the resolver call.
    stored = "NameSource { kind: Kind::Weapon, getter_rva: rva::GET_WEAPON_NAME },"
    assert "GET_WEAPON_NAME" in used_as_game_address(stored), (
        "an address held in a table field was missed; four of the six name getters reach the "
        "resolver only this way"
    )

    # A Win32 flag is never handed to a resolver, which is the whole point of asking about usage
    # rather than about the value.
    flags = "const MEM_COMMIT: u32 = 0x1000;\nVirtualAlloc(p, n, MEM_COMMIT, PAGE_READWRITE);"
    assert "MEM_COMMIT" not in used_as_game_address(flags), (
        "a non-address constant was admitted; the value heuristic this module rejects is back"
    )

    # NON-VACUITY. Blind the paren balancer to multi-line calls and the first assertion must fail.
    global RESOLVERS
    keep = RESOLVERS
    try:
        RESOLVERS = re.compile(r"\bthis_matches_nothing_at_all\s*\(")
        blinded = used_as_game_address(multiline)
        assert "GET_WEAPON_NAME" not in blinded, (
            "the matcher still reported a name after being blinded, so the assertions above "
            "prove nothing about the matcher"
        )
    finally:
        RESOLVERS = keep

    # Test-module spans, and the negative fixture that made them necessary.
    src = (
        "const LIVE_RVA: usize = 0x111;\n"
        "#[cfg(test)]\n"
        "mod tests {\n"
        "    const CHAINED_CONTINUATION_RVA: usize = 0xc5_7666;\n"
        "    fn inner() { if x { y } }\n"
        "}\n"
    )
    spans = test_module_spans(src)
    assert len(spans) == 1, f"expected one test module, got {spans}"
    assert in_any_span(src.index("CHAINED_CONTINUATION_RVA"), spans), (
        "the negative fixture was not recognised as test-only"
    )
    assert not in_any_span(src.index("LIVE_RVA"), spans), (
        "a production constant was swallowed by the test span; the brace match ran long"
    )

    print("scripts/rva_usage.py: selftest OK (5 shapes matched, 1 rejected, "
          "test-module spans bounded, blinding observed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(selftest())
