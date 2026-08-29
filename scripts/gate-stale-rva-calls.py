#!/usr/bin/env python3
"""Route `transmute(base + SOME_RVA)` call sites through the 1.17 address gate, in bulk.

WHY THIS EXISTS
---------------
`scripts/check-stale-rva-calls.py` counts the sites and refuses new ones. Converting them is the
other half, and on 2026-08-29 there were 210 of them across the workspace -- too many to edit by
hand, and each edit is the same shape:

    let f: Fn = unsafe { transmute(base + SOME_RVA) };
    ->
    let f: Fn = unsafe { transmute(match helper(SOME_RVA, "SOME_RVA") {
        Some(address) => address,
        None => <bail>,
    }) };

WHAT IT REFUSES TO TOUCH
------------------------
The bail is the whole risk, so this only rewrites a site whose bail is unambiguous:

  * NEVER inside an `extern "system"` function. Those are detours, and a detour that returns early
    never calls its original -- which silently deletes the game's own behaviour instead of adding
    ours. Measured: `hud_weapon_update_hook` needed `return ret`, not `return`.
  * ONLY where the enclosing function's return type maps to an obvious "did nothing" value:
    `()`, `bool`, an integer, `f32`. Anything else (a struct, a tuple, `Option<...>` where `None`
    might mean something specific) is printed for a human instead of guessed at.

Everything it skips is listed, so the remainder is a work-list rather than a silence.

USAGE
    python3 scripts/gate-stale-rva-calls.py --helper title_fn crates/er-title-flow/src/*.rs
    python3 scripts/gate-stale-rva-calls.py --helper 'crate::gated' --dry-run <paths...>
    python3 scripts/gate-stale-rva-calls.py --selftest
"""

from __future__ import annotations

import argparse
import os
import re
import sys

# Return type -> the expression a refusal should evaluate to. Deliberately small: an entry here is
# a claim that "this value means the function did nothing", and that claim has to be true.
BAIL_FOR_RETURN = {
    "": "return",
    "()": "return",
    "bool": "return false",
    "usize": "return 0",
    "u32": "return 0",
    "u64": "return 0",
    "i32": "return 0",
    "f32": "return 0.0",
}
# ANY `Option<T>`: `None` is that function's own "I could not produce a value", which is exactly
# what a refused address means. It gets the `?` form rather than a match -- see `rewrite`. An
# earlier version listed only two concrete `Option<..>` types and hand-refused the rest, which was
# caution with no reasoning behind it: `Option<Vec<u8>>` means no different from `Option<usize>`.
OPTION_RETURN = re.compile(r"^Option\s*<")

SIGNATURE = re.compile(
    r"^(?P<indent>\s*)(?:pub(?:\([a-z():]+\))?\s+)?(?:unsafe\s+)?"
    r"(?P<abi>extern\s+\"[a-z]+\"\s+)?fn\s+(?P<name>\w+)"
)
# Multi-line on purpose: rustfmt routinely breaks a long `transmute(base + SOME_LONG_RVA)` across
# lines, and a single-line pattern silently missed 13 of 18 sites in er-title-flow alone.
CALL_SITE = re.compile(
    r"transmute\(\s*(?:base|image_base|module_base|game_base)\s*\+\s*"
    r"(?P<prefix>(?:\w+::)*)(?P<const>[A-Z0-9_]*RVA[A-Z0-9_]*)(?P<cast>\s+as\s+usize)?\s*,?\s*\)",
    re.S,
)


def enclosing_function(lines: list[str], index: int) -> tuple[str | None, str, bool]:
    """`(name, return_type, is_extern)` for the function containing `lines[index]`."""
    for i in range(index, -1, -1):
        match = SIGNATURE.match(lines[i])
        if not match:
            continue
        blob, j = lines[i], i
        while "{" not in blob and j + 1 < len(lines) and j - i < 16:
            j += 1
            blob += lines[j]
        return_type = ""
        arrow = blob.find("->")
        if arrow >= 0 and "{" in blob[arrow:]:
            return_type = blob[arrow + 2 : blob.rindex("{")].strip()
        return match.group("name"), return_type, bool(match.group("abi"))
    return None, "", False


def rewrite(path: str, helper: str, dry_run: bool) -> tuple[int, int, list[str]]:
    text = open(path, encoding="utf-8").read()
    lines = text.splitlines(keepends=True)
    # Offset -> line index, so a multi-line match can still name its enclosing function.
    starts, offset = [], 0
    for line in lines:
        starts.append(offset)
        offset += len(line)

    def line_of(position: int) -> int:
        low, high = 0, len(starts) - 1
        while low < high:
            mid = (low + high + 1) // 2
            if starts[mid] <= position:
                low = mid
            else:
                high = mid - 1
        return low

    gated, seen, skipped, pieces, cursor = 0, 0, [], [], 0
    for match in CALL_SITE.finditer(text):
        seen += 1
        index = line_of(match.start())
        name, return_type, is_extern = enclosing_function(lines, index)
        bail = BAIL_FOR_RETURN.get(return_type)
        if bail is None and OPTION_RETURN.match(return_type.strip()):
            bail = "return None"
        if is_extern or bail is None:
            why = (
                "extern fn -- a detour must still call its original"
                if is_extern
                else f"return type {return_type!r} has no obvious did-nothing value"
            )
            skipped.append(f"{os.path.relpath(path)}:{index + 1} in {name}: {why}")
            continue
        # Keep any `as usize`: the constant may be a u32 and dropping the cast is a type error.
        constant = match.group("prefix") + match.group("const") + (match.group("cast") or "")
        call = f'{helper}({constant}, "{match.group("const")}")'
        # `match x { Some(a) => a, None => return None }` IS `x?`, and clippy rejects the long
        # form. Emit what a reader (and the linter) actually wants.
        resolved = (
            f"{call}?"
            if bail == "return None"
            else f"match {call} {{ Some(address) => address, None => {bail} }}"
        )
        pieces.append(text[cursor : match.start()])
        pieces.append(f"transmute({resolved})")
        cursor = match.end()
        gated += 1
    pieces.append(text[cursor:])
    if gated and not dry_run:
        open(path, "w", encoding="utf-8").write("".join(pieces))
    return gated, seen, skipped


def selftest() -> int:
    failures = []
    body = [
        "unsafe extern \"system\" fn hook(a: usize) -> usize {\n",
        "    let f: F = unsafe { transmute(base + SOME_RVA) };\n",
        "}\n",
        "unsafe fn plain(base: usize) {\n",
        "    let g: G = unsafe { transmute(base + OTHER_RVA) };\n",
        "}\n",
        "fn answers(base: usize) -> bool {\n",
        "    let h: H = unsafe { transmute(base + THIRD_RVA) };\n",
        "}\n",
    ]
    name, ret, is_extern = enclosing_function(body, 1)
    if not is_extern:
        failures.append("a detour was not recognised as extern -- it would have been rewritten")
    name, ret, is_extern = enclosing_function(body, 4)
    if is_extern or BAIL_FOR_RETURN.get(ret) != "return":
        failures.append(f"a plain `-> ()` fn resolved to {ret!r}, not the empty return")
    name, ret, is_extern = enclosing_function(body, 7)
    if BAIL_FOR_RETURN.get(ret) != "return false":
        failures.append(f"a `-> bool` fn resolved to {ret!r}, not `return false`")
    if not CALL_SITE.search("unsafe { std::mem::transmute(base + crate::FOO_RVA) }"):
        failures.append("CALL_SITE missed a crate::-prefixed constant")
    cast_match = CALL_SITE.search("transmute(base + FOO_RVA as usize)")
    if not cast_match:
        failures.append("CALL_SITE missed an `as usize` cast")
    elif not (cast_match.group("cast") or "").strip():
        failures.append("CALL_SITE swallowed the `as usize` cast instead of capturing it")
    if not OPTION_RETURN.match("Option<Vec<u8>>"):
        failures.append("OPTION_RETURN did not accept a generic Option")
    if CALL_SITE.search("transmute(orig)"):
        failures.append("CALL_SITE matched a trampoline transmute, which must be left alone")
    for line in failures:
        print(f"selftest FAIL {line}")
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--helper", help="the resolver to call, e.g. `title_fn`")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if not args.helper or not args.paths:
        parser.error("--helper and at least one path are required")
    gated = seen = 0
    skipped: list[str] = []
    for path in args.paths:
        one_gated, one_seen, one_skipped = rewrite(path, args.helper, args.dry_run)
        gated += one_gated
        seen += one_seen
        skipped.extend(one_skipped)
    for line in skipped:
        print(f"  SKIP {line}")
    print(f"gated {gated} / {seen} site(s); {len(skipped)} left for a human")
    return 0


if __name__ == "__main__":
    sys.exit(main())
